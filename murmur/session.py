"""One recording: capture audio, live transcribe/clean, finish by pasting. Runs on its own thread."""
import logging, threading, time
import keyboard
from datetime import datetime
import numpy as np
import sounddevice as sd
from . import brain, capture, context, cues, llm, store, stt, win, workspace

log = logging.getLogger("murmur")
UNDO_WINDOW = 60  # seconds after our last paste during which "scratch that" sends ctrl+z
MAX_SESSION = 600  # hard cap on one recording, seconds
REDICT_WINDOW = 90  # seconds within which a near-identical dictation counts as a correction


def wait_cap(cfg, chunks):
    """Seconds to wait for the final clean: [llm] timeout per chunk, doubled when a fallback may run after a hung primary."""
    return cfg["llm"].get("timeout", 15) * max(chunks, 1) * (2 if cfg["llm"].get("fallback") else 1)


def transcribe(buf):
    return stt.recognize(np.concatenate(buf)[:, 0]) if buf else ""


class Session:
    """hooks: held() key down, stop() end the recording, current() newest session, done() release shared state,
    mark(state) tray icon state, pasted(app, text)/last_paste() = (time, app, text) for spoken undo and editing the last dictation, retry() re-probe the mic."""

    _missing = set()  # device names already warned about

    def __init__(self, mode, cfg, ov, hooks):
        self.mode, self.cfg, self.ov = mode, cfg, ov
        self.held, self.stop, self.current, self.done, self.mark = hooks.held, hooks.stop, hooks.current, hooks.done, hooks.mark
        self.retry = hooks.retry
        self.pasted, self.last_paste = hooks.pasted, hooks.last_paste  # (time, app, text) of the last paste lives in app state across sessions
        self.stream, self.buf = None, []
        self.raw, self.cleaned, self.inflight = "", ("", "", False, True), (None, None)  # cleaned = (raw, text, had_ctx, plausible); inflight = (raw, Event)
        self.app = self.title = ""
        self.ctx = {}
        self.cancelled = self.failed = self.degraded = self.paste_now = self.kept_raw = False
        self.selection, self.target, self.trailing, self.pasted_ok = "", "new", "", False
        self.latched, self.vad, self.vad_decision, self.label, self.route = False, capture.vad_new(), None, None, "primary"
        self.committed, self.tag_index, self.silent = "", None, False
        self.rms, self.live_route = [], "primary"  # per-block RMS aligned with buf (forced segment cuts); route of the live clean  # committed = text of already-transcribed long-dictation segments

    def _cue(self, name):
        """Audio cue (async, honours [ui] sounds); never raises."""
        cues.play(name, self.cfg)

    def latch(self):
        """The dictate key was tapped: keep recording hands-free until the key is pressed again, Enter, Esc or silence."""
        self.latched, self.label = True, "Listening \u00b7 hands-free"
        self._post(lambda: self.ov.state(self.ov.st, label=self.label))

    def discard(self):
        """Drop the session silently (no card cue): a chord, or the learn hotkey right after the start."""
        self.silent = True
        self.cancel()

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
            self.raw = self._compose(transcribe(self.buf))
            if not self.raw:
                if self.vad_decision == "cancel":  # nothing was said: only now discard
                    self._cue("cancel")
                    self._post(lambda: self.ov.state("nothing", "No speech heard.", label="No speech"))
                return
            self._cue("stop")
            if self.mode == "dictate" and (name := brain.spoken_command(self.raw)):
                getattr(self, "_cmd_" + name)()
                return
            if self.mode == "dictate" and brain.has_scratch(self.raw) and not brain.apply_scratch(self.raw):
                self._cue("cancel")
                self._post(lambda: self.ov.state("nothing", "Scratched.", label="Scratched"))  # whole utterance scratched
                return
            self.reader.join(1.0 if self.mode == "command" else 0.4)  # never block dictation on UIA
            self.mark("polishing")
            final = self._finish_command() if self.mode == "command" else self._finish_dictate()
            if final and self.mode == "dictate":
                final = brain.fit(self._tag_files(final), self.ctx.get("before", ""), self.ctx.get("tone"))
            if final and not self.cancelled:
                self._paste_and_log(final)
        except Exception:
            log.exception("session failed")
        finally:
            self._release_last()
            self._cleanup()

    def _compose(self, open_text):
        """Full transcript: committed long-dictation segments plus the still-open one."""
        return " ".join(t for t in (self.committed, open_text) if t)

    def _tag_files(self, text):
        """Swap spoken file names for workspace paths (code editors); any failure or a not-yet-built index leaves the text as is."""
        try:
            style = workspace.tag_style(self.cfg.get("code", {}).get("file_tags", "chat"), self.ctx.get("control_name", ""))
            return workspace.tag_files(text, self.tag_index, style) if style and self.tag_index else text
        except Exception:
            log.exception("file tagging failed")
            return text

    def _release_last(self):
        """Target 'last' selected the last dictation; if nothing replaced it (Esc, error, app switch) collapse the selection so
        the next keystroke doesn't delete it. Only while the original app is still focused."""
        if self.target != "last" or self.pasted_ok:
            return
        try:
            if win.active_app()[0] == self.app and not context.collapse_selection():
                keyboard.send("right")
        except Exception:
            log.exception("collapse selection failed")

    def _cmd_undo(self):
        """Spoken undo: one ctrl+z if we pasted recently into this same app; nothing is pasted or logged."""
        t, app, _ = self.last_paste()
        if time.time() - t >= UNDO_WINDOW:
            name, text = "nothing", "Nothing to undo."
        elif win.active_app()[0] != app:
            name, text = "nothing", "Nothing to undo here."
        else:
            win.undo(); self.pasted(None)
            name, text = "undone", None
        self._cue("done" if name == "undone" else "cancel")
        self._post(lambda: self.ov.state(name, text))

    def _cleanup(self):
        try:
            if self.stream:
                self.stream.close()
        except Exception:
            log.exception("stream close failed")
        if self.cancelled and not self.silent:
            self._cue("cancel")  # Esc
        self.done(); self.mark("error" if self.failed else "idle"); self._post(self.ov.release)

    def _audio(self, d, *_):
        rms = self.ov.level = float(np.sqrt(np.mean(d * d)))
        self.rms.append(rms)  # before buf, so rms is never shorter than buf
        self.buf.append(d.copy())
        self.vad, decision = capture.vad_step(self.vad, rms, len(d) / 16000, self.cfg.get("dictation", {}))
        if decision and self.latched and not self.vad_decision:  # silence auto-stop is for hands-free sessions only
            self.vad_decision = decision
            self.stop()

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
            self._cue("error")
            self._post(lambda: (self.ov.show(self.mode, "", self._handlers()), self.ov.state("error")))
            return False
        self.t0 = time.time()
        self._cue("start")
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
        if self.mode == "dictate" and self.cfg.get("code", {}).get("file_tags", "chat") != "off":
            try:
                if roots := workspace.roots_for(self.app, self.title, self.cfg):
                    self.tag_index = workspace.index_roots(roots)
            except Exception:
                log.exception("workspace index failed")

    def _anchor(self, rect, kind="field"):
        if self.current():
            self._post(lambda: self.ov.anchor(rect, kind))

    def _poll_loop(self):
        while self.held():
            time.sleep(0.6)
            if time.time() - self.t0 >= MAX_SESSION:
                log.info("recording hit the %d s cap; finishing", MAX_SESSION)
                self.stop()
                break
            frames = list(self.buf)
            open_text = self._commit(frames, transcribe(frames))
            r = self._compose(open_text)
            if r and r != self.raw:
                self.raw = r
                self._post(lambda r=r: self._paint_raw(r))
                if not self.committed:  # long sessions skip live clean: the final pass cleans in chunks
                    self._maybe_live(r)

    def _commit(self, frames, open_text):
        """Long dictation: transcribe-once-and-drop the open segment at a pause (or force a cut at 30 s in a room that never
        goes quiet). Returns the text of what is still open."""
        n = len(frames)
        kind = capture.commit_kind(sum(len(f) for f in frames) / 16000, self.vad["quiet_ms"]) if open_text else None
        if not kind:
            return open_text
        rms = self.rms[:n]
        cut = n if kind == "pause" or len(rms) < n else capture.cut_point(rms, len(frames[0]) / 16000)
        head = open_text if cut >= n else transcribe(frames[:cut])
        self.committed = self._compose(head)
        del self.buf[:cut], self.rms[:cut]
        return "" if cut >= n else transcribe(frames[cut:])

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
            self.ov.state("transcribing", label=self.label)

    def _maybe_live(self, r):
        idle = self.inflight[1] is None or self.inflight[1].is_set()
        if self.mode == "dictate" and self.cfg["llm"]["live_clean"] and idle:
            self.inflight = (r, threading.Event())
            threading.Thread(target=self._live, args=self.inflight, daemon=True).start()

    def _live(self, r, ev):
        try:
            had_ctx = "before" in self.ctx  # a context-less result must not be reused once context arrives
            w = brain.apply_scratch(r)
            if not w:  # everything scratched: nothing to clean
                self.cleaned = (r, "", had_ctx, True)
                return
            text, ok = brain.clean_checked(w, self.ctx, retry=False)  # a rejected live result is never shown, only kept as the raw fallback
            self.live_route = llm.route()
            self.cleaned = (r, text, had_ctx, ok)
        except Exception as e:
            log.info("live clean failed: %s", e)
        finally:
            ev.set()

    def _finish_dictate(self):
        raw = self.raw
        shown = self.cleaned[1] if self.cleaned[0] == raw else raw
        self._post(lambda: self.ov.state("polishing", shown))
        n = len(brain.split_chunks(brain.apply_scratch(raw)))
        timeout = wait_cap(self.cfg, n)
        if n <= 1 and self.cleaned[0] != raw and self.inflight[0] == raw:
            self._wait(self.inflight[1], timeout)  # reuse the in-flight request
        if self.paste_now or self.cancelled:
            return brain.apply_scratch(raw)
        if n <= 1 and self.cleaned[0] == raw and self.cleaned[2] == ("before" in self.ctx):
            self.route = self.live_route
            self.kept_raw = not self.cleaned[3]
            return self.cleaned[1]
        box, done = [], threading.Event()
        threading.Thread(target=self._clean_into, args=(raw, box, done), daemon=True).start()
        self._wait(done, timeout)
        if not box:  # interrupted by Enter/Esc (raw is what the user asked for) or still running
            return brain.apply_scratch(raw)
        if box[0] is None:
            self.degraded = True
            return brain.apply_scratch(raw)
        self.kept_raw = not box[0][1]
        return box[0][0]

    def _clean_into(self, raw, box, done):
        try:
            box.append(brain.clean_auto(brain.apply_scratch(raw), self.ctx))
            self.route = llm.route()  # this call's route (thread-local), not a global
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

    def _cmd_learn(self, word, selected):
        """Voice 'add this word to the dictionary': the selection (or the spoken `word`) goes in; nothing is pasted or logged."""
        text = brain.clean_word(word or selected)
        if not text:
            name, msg = "nothing", "Select the word first."
        elif len(text.split()) > 4 or (word and len(word.split()) > 1):
            name, msg = "nothing", "Select 1\u20134 words to add to your dictionary."
        else:
            store.add_terms([text]); log.info("learned word (voice): %s", text)
            name, msg = "learned", f"\u201c{text}\u201d added to your dictionary"
        self._cue("done" if name == "learned" else "cancel")
        self._post(lambda: self.ov.state(name, msg))

    def _selected(self):
        """(sel_known, selected). UIA's TextPattern answer is authoritative even when empty; only when UIA could not say
        does the clipboard run (never in no_copy apps, never while the UIA read is merely unfinished; VS Code copies the
        whole line on an empty selection)."""
        if self.reader.is_alive():
            return False, ""
        known, sel = self.ctx.get("sel_known", False), self.ctx.get("selected", "")
        if not known and not sel and self.app not in store.no_copy(self.cfg):
            sel = win.copy_selection() or ""
        return known, sel

    def _select_last(self, core, before):
        """Select the last paste (immediately before the caret) so the paste replaces it. UIA verified select; the key
        fallback only when the control has no TextPattern at all. True only when verified. Sets self.trailing
        (whitespace between the text and the caret)."""
        b = before.replace("\r\n", "\n")
        self.trailing = b[len(b.rstrip()):]
        expect = core + self.trailing
        got = context.select_before_caret(expect)
        if got != "unknown":
            return got == "ok"
        if self.app in store.no_copy(self.cfg) or len(expect) > 600:
            return False
        for _ in range(len(expect)):
            keyboard.send("shift+left")
        if (win.copy_selection() or "").replace("\r\n", "\n") == expect:
            return True
        keyboard.send("right")  # wrong text selected: collapse back to the caret
        return False

    def _delete_selection(self, text):
        """'delete that' with a real selection deletes it (never ctrl+z the user's own typing)."""
        keyboard.send("delete")
        self._cue("done")
        self._post(lambda: self.ov.state("done", f"Deleted {len(text)} chars"))

    def _label(self, desc):
        where = f"{self.app} ({desc})"
        self._post(lambda: setattr(self.ov, "dest", where if len(where) <= 40 else where[:39] + "\u2026"))

    def _needs_selection(self):
        """Does this spoken command depend on what is selected? Rewrites, 'delete that', and learn-the-selection do;
        a plain undo or 'add <word> to the dictionary' does not."""
        kind, arg = brain.command_intent(self.raw, self.ctx.get("tone"), False)
        return kind in ("rewrite", "scratch") or (kind == "learn" and arg is None) or (kind == "undo" and "delete" in self.raw.lower())

    def _finish_command(self):
        if self.reader.is_alive() and self._needs_selection():  # UIA hasn't answered: pasting could replace a selection we never saw
            self._cue("error")
            self._post(lambda: self.ov.state("offline", "Couldn't read the selection \u2014 try again.", label="Not read"))
            return None
        known, selected = self._selected()
        before = self.ctx.get("before", "")
        kind, arg = brain.command_intent(self.raw, self.ctx.get("tone"), bool(selected.strip()))
        if kind == "undo" or (kind == "scratch" and not arg.strip()):
            if selected.strip() and known:
                self._delete_selection(selected)
            elif selected.strip():  # clipboard-derived: not trustworthy enough to press Delete on
                self._cue("cancel")
                self._post(lambda: self.ov.state("nothing", "Select the text first, then say it again.", label="Not selected"))
            else:
                self._cmd_undo()
            return None
        if kind == "learn":
            self._cmd_learn(arg, selected)
            return None
        instruction = arg if kind == "scratch" else self.raw
        self.target, text = brain.resolve_target((known, selected), before, self.last_paste(), (self.app, time.time()))
        if self.target == "last" and not self._select_last(text, before):
            self.target, text = "new", ""
        self.selection = text
        self._label({"selection": f"selection: {len(text)} chars", "last": "editing last dictation", "new": "new text"}[self.target])
        try:
            out = brain.rewrite(text or None, instruction, self.ctx, self.target)
        except Exception as e:
            log.info("rewrite failed: %s", e)  # pasting would clobber the selection
            self._cue("error")
            self._post(lambda: self.ov.state("offline", "Rewrite failed; LM Studio is unavailable."))
            self.cancelled = True  # nothing to paste
            return None
        if self.target == "new":
            return brain.fit(out, before, self.ctx.get("tone"))
        return out + (self.trailing if self.target == "last" else "")

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
            self._cue("error")
            self._post(lambda: self.ov.state("paste_failed"))
            return
        self._cue("error" if self.degraded or self.kept_raw else "done")
        self.pasted_ok = True
        self.pasted(self.app, final.strip())
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "app": self.app, "title": self.title,
                 "tone": self.ctx["tone"], "mode": self.mode, "raw": self.raw, "final": final}
        if self.mode == "command":
            entry["selection"], entry["target"] = self.selection, self.target
        store.append_history(entry)
        self._learn_from_redictation(prev, final)
        self._post(lambda: self._show_outcome(final))

    def _show_outcome(self, final):
        if self.degraded:
            self.ov.state("offline")
        elif self.kept_raw:
            self.ov.state("offline", "Kept your words; cleanup looked wrong.", label="Kept raw")
        elif self.mode == "command":
            n = final.count("\n") + 1
            self.ov.state("done", f"Rewrote {n} line{'s' if n != 1 else ''}")
        else:
            self.ov.state("pasted", final, label="Pasted \u00b7 fallback" if self.route == "fallback" else None)

    def _learn_from_redictation(self, prev, final):
        """A near-identical dictation soon after the last one is a correction: learn the changed terms."""
        if self.mode != "dictate" or (prev or {}).get("mode", "dictate") != "dictate":
            return  # command rows are not dictations to compare or learn from
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
