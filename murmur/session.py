"""One recording: capture audio, live transcribe/clean, finish by pasting. Runs on its own thread."""
import logging, threading, time
from datetime import datetime
import numpy as np
import sounddevice as sd
from . import brain, context, store, stt, win

log = logging.getLogger("murmur")
UNDO_WINDOW = 60  # seconds after our last paste during which "scratch that" sends ctrl+z
REDICT_WINDOW = 90  # seconds within which a near-identical dictation counts as a correction


def transcribe(buf):
    return stt.recognize(np.concatenate(buf)[:, 0]) if buf else ""


class Session:
    """hooks: held() key down, stop() end the recording, current() newest session, done() release shared state,
    mark(state) tray icon state, pasted(app)/last_paste() for spoken undo, retry() re-probe the mic."""

    _missing = set()  # device names already warned about

    def __init__(self, mode, cfg, ov, hooks):
        self.mode, self.cfg, self.ov = mode, cfg, ov
        self.held, self.stop, self.current, self.done, self.mark = hooks.held, hooks.stop, hooks.current, hooks.done, hooks.mark
        self.retry = hooks.retry
        self.pasted, self.last_paste = hooks.pasted, hooks.last_paste  # (time, app) of the last paste lives in app state across sessions
        self.stream, self.buf = None, []
        self.raw, self.cleaned, self.inflight = "", ("", "", False), (None, None)  # cleaned = (raw, text, had_ctx); inflight = (raw, Event)
        self.app = self.title = ""
        self.ctx = {}
        self.cancelled = self.failed = self.degraded = self.paste_now = False

    def cancel(self):
        """Esc on the card: discard everything (no paste, no history), at any point."""
        self.cancelled = True
        self.stop()
        self.ov.post(self.ov.hide)  # hide also unbinds Esc/Enter, even while the LLM call is still running

    def enter(self):
        """Enter on the card: while recording = finish now; while polishing = paste the raw transcript now."""
        if self.held():
            self.stop()
        elif self.mode == "dictate":  # a command has no raw text to fall back to; Esc cancels it
            self.paste_now = True

    def _post(self, fn):
        """Post to the Tk thread, dropped if a newer session has taken over the card by then."""
        self.ov.post(lambda: self.current() and fn())

    def run(self):
        try:
            if not self._start():
                return
            self._poll_loop()
            self.stream.stop()
            if self.cancelled or time.time() - self.t0 < 0.3:
                return
            self.raw = transcribe(self.buf)
            if not self.raw:
                return
            if self.mode == "dictate" and (name := brain.spoken_command(self.raw)):
                getattr(self, "_cmd_" + name)()
                return
            self.reader.join(0.4)  # never block dictation on UIA
            self.mark("polishing")
            final = self._finish_command() if self.mode == "command" else self._finish_dictate()
            if final and self.mode == "dictate":
                final = brain.fit(final, self.ctx.get("before", ""))
            if final and not self.cancelled:
                self._paste_and_log(final)
        except Exception:
            log.exception("session failed")
        finally:
            self._cleanup()

    def _cmd_undo(self):
        """Spoken undo: one ctrl+z if we pasted recently into this same app; nothing is pasted or logged."""
        t, app = self.last_paste()
        if time.time() - t >= UNDO_WINDOW:
            name, text = "nothing", "Nothing to undo."
        elif win.active_app()[0] != app:
            name, text = "nothing", "Nothing to undo here."
        else:
            win.undo(); self.pasted(None)
            name, text = "undone", None
        self._post(lambda: self.ov.state(name, text))

    def _cleanup(self):
        try:
            if self.stream:
                self.stream.close()
        except Exception:
            log.exception("stream close failed")
        self.done(); self.mark("error" if self.failed else "idle"); self._post(self.ov.release)

    def _audio(self, d, *_):
        self.buf.append(d.copy())
        self.ov.level = float(np.sqrt(np.mean(d * d)))

    def _handlers(self):
        return {"cancel": self.cancel, "enter": self.enter, "retry": self.retry}

    def _device(self):
        """Configured input device index (resolved by name each time; indices shift between boots), None = default."""
        name = self.cfg.get("audio", {}).get("device", "")
        idx = store.resolve_device(name, sd.query_devices())
        if name and idx is None and name not in Session._missing:
            Session._missing.add(name); log.warning("input device %r not found, using the default", name)
        return idx

    def _start(self):
        try:
            self.stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=self._audio, device=self._device())
            self.stream.start()  # first, so the first syllable isn't clipped
        except Exception:
            log.exception("microphone unavailable")
            self.failed, self.stream = True, None
            self._post(lambda: (self.ov.show(self.mode, "", self._handlers()), self.ov.state("error")))
            return False
        self.t0 = time.time()
        self.mark("recording")
        self.ctx = {"cfg": self.cfg, **context.gather(self.cfg)}
        self.reader = threading.Thread(target=self._read_field, daemon=True)
        self.reader.start()  # UIA off the hotkey/Tk threads; live clean uses whatever has arrived
        self.app, self.title = self.ctx["app"], self.ctx["title"]
        where = self.title or self.app
        self._post(lambda: self.ov.show(self.mode, where, self._handlers()))
        return True

    def _read_field(self):
        text_ok = self.app not in store.no_copy(self.cfg)  # terminal scrollback must not go into prompts
        self.ctx.update(context.read_field(self.cfg, on_rect=self._anchor, text=text_ok))

    def _anchor(self, rect):
        if self.current():
            self._post(lambda: self.ov.anchor(rect))

    def _poll_loop(self):
        while self.held():
            time.sleep(0.6)
            # ponytail: whole-buffer retranscribe, chunk if dictations exceed ~60s
            r = transcribe(list(self.buf))
            if r and r != self.raw:
                self.raw = r
                self._post(lambda r=r: self._paint_raw(r))
                self._maybe_live(r)

    def _paint_raw(self, r):
        """Tk thread: raw words stream into the card, the last one dimmed as still changing."""
        if not self.current():
            return
        if self.mode == "command":
            self.ov.transcript(f"\u201c{r}\u201d", "")
            return
        words = r.split()
        self.ov.transcript(" ".join(words[:-1]), words[-1] if words else "")
        if self.ov.st == "listening":
            self.ov.state("transcribing")

    def _maybe_live(self, r):
        idle = self.inflight[1] is None or self.inflight[1].is_set()
        if self.mode == "dictate" and self.cfg["llm"]["live_clean"] and idle:
            self.inflight = (r, threading.Event())
            threading.Thread(target=self._live, args=self.inflight, daemon=True).start()

    def _live(self, r, ev):
        try:
            had_ctx = "before" in self.ctx  # a context-less result must not be reused once context arrives
            self.cleaned = (r, brain.clean(r, self.ctx), had_ctx)
        except Exception as e:
            log.info("live clean failed: %s", e)
        finally:
            ev.set()

    def _finish_dictate(self):
        raw = self.raw
        shown = self.cleaned[1] if self.cleaned[0] == raw else raw
        self._post(lambda: self.ov.state("polishing", shown))
        timeout = self.cfg["llm"].get("timeout", 15)
        if self.cleaned[0] != raw and self.inflight[0] == raw:
            self._wait(self.inflight[1], timeout)  # reuse the in-flight request
        if self.paste_now or self.cancelled:
            return raw
        if self.cleaned[0] == raw and self.cleaned[2] == ("before" in self.ctx):
            return self.cleaned[1]
        box, done = [], threading.Event()
        threading.Thread(target=self._clean_into, args=(raw, box, done), daemon=True).start()
        self._wait(done, timeout)
        if not box:  # interrupted by Enter/Esc (raw is what the user asked for) or still running
            return raw
        if box[0] is None:
            self.degraded = True
            return raw
        return box[0]

    def _clean_into(self, raw, box, done):
        try:
            box.append(brain.clean(raw, self.ctx))
        except Exception as e:
            log.info("clean failed, pasting raw: %s", e)
            box.append(None)
        finally:
            done.set()

    def _wait(self, ev, timeout):
        """Wait for ev, but give up early when the user presses Enter (paste raw) or Esc (cancel)."""
        end = time.time() + timeout
        while not ev.is_set() and time.time() < end and not (self.paste_now or self.cancelled):
            ev.wait(0.1)

    def _finish_command(self):
        sel = self.ctx.get("selected") or None
        if not sel and self.app not in store.no_copy(self.cfg):
            # ponytail: VS Code's empty-selection ctrl+c copies the whole line
            sel = win.copy_selection()
        try:
            return brain.rewrite(sel, self.raw, self.ctx)
        except Exception as e:
            log.info("rewrite failed: %s", e)  # pasting would clobber the selection
            self._post(lambda: self.ov.state("offline", "Rewrite failed; LM Studio is unavailable."))
            self.cancelled = True  # nothing to paste
            return None

    def _deliver(self, final):
        """Insert into the app we started in; if it changed or insertion fails, leave the text on the clipboard."""
        try:
            if win.active_app()[0] == self.app:
                win.insert(final, self.cfg)
                return True
        except Exception:
            log.exception("insert failed")
        win.to_clipboard(final)
        return False

    def _paste_and_log(self, final):
        prev = (store.read_history(1) or [None])[-1]
        if not self._deliver(final):
            self._post(lambda: self.ov.state("paste_failed"))
            return
        self.pasted(self.app)
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "app": self.app, "title": self.title,
                 "tone": self.ctx["tone"], "raw": self.raw, "final": final}
        store.append_history(entry)
        self._learn_from_redictation(prev, final)
        self._post(lambda: self._show_outcome(final))

    def _show_outcome(self, final):
        if self.degraded:
            self.ov.state("offline")
        elif self.mode == "command":
            n = final.count("\n") + 1
            self.ov.state("done", f"Rewrote {n} line{'s' if n != 1 else ''}")
        else:
            self.ov.state("pasted", final)

    def _learn_from_redictation(self, prev, final):
        """A near-identical dictation soon after the last one is a correction: learn the changed terms."""
        try:
            fresh = prev and prev["app"] == self.app and not prev.get("corrected")
            age = (datetime.now() - datetime.fromisoformat(prev["ts"])).total_seconds() if fresh else None
            if age is None or age > REDICT_WINDOW or not brain.is_redictation(prev["final"], final):
                return
            terms = brain.redictation_terms(prev["final"], final)
            if not terms:
                return  # nothing worth learning: don't spend a few-shot slot on noise
            store.add_terms(terms)
            store.correct_last(prev["ts"], prev["final"], final)
            log.info("re-dictation correction; learned: %s", terms)
        except Exception:
            log.exception("re-dictation learning failed")
