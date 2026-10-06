"""One recording: capture audio, live transcribe/clean, finish by pasting. Runs on its own thread."""
import logging, threading, time
from datetime import datetime
import numpy as np
import sounddevice as sd
from . import brain, context, store, stt, win

log = logging.getLogger("murmur")
COMMANDS = {}  # phase 2c: spoken command -> handler
NO_COPY = ["windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe"]


def transcribe(buf):
    return stt.recognize(np.concatenate(buf)[:, 0]) if buf else ""


class Session:
    """hooks: held() key still down, current() newest session, done() release shared state, mark(color) tray."""

    def __init__(self, mode, cfg, ov, hooks):
        self.mode, self.cfg, self.ov = mode, cfg, ov
        self.held, self.current, self.done, self.mark = hooks.held, hooks.current, hooks.done, hooks.mark
        self.label = "Recording" if mode == "dictate" else "Command"
        self.stream, self.buf = None, []
        self.raw, self.cleaned, self.inflight = "", ("", "", False), (None, None)  # cleaned = (raw, text, had_ctx); inflight = (raw, Event)
        self.app = self.title = ""
        self.ctx = {}

    def run(self):
        try:
            self._start()
            self._poll_loop()
            self.stream.stop()
            if time.time() - self.t0 < 0.3:
                return
            self.raw = transcribe(self.buf)
            if not self.raw:
                return
            if COMMANDS.get(self.raw.lower().strip(" .,!")):  # phase 2c
                return
            self.reader.join(0.4)  # never block dictation on UIA
            self.mark("orange")
            final = self._finish_command() if self.mode == "command" else self._finish_dictate()
            if final and self.mode == "dictate":
                final = brain.fit(final, self.ctx.get("before", ""))
            if final:
                self._paste_and_log(final)
        except Exception:
            log.exception("session failed")
        finally:
            self._cleanup()

    def _cleanup(self):
        try:
            if self.stream:
                self.stream.close()
        except Exception:
            log.exception("stream close failed")
        self.done(); self.mark("grey"); self.ov.post(self.ov.hide)

    def _audio(self, d, *_):
        self.buf.append(d.copy())
        self.ov.level = float(np.sqrt(np.mean(d * d)))

    def _start(self):
        self.stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=self._audio)
        self.stream.start()  # first, so the first syllable isn't clipped
        self.t0 = time.time()
        self.mark("red")
        self.ctx = {"cfg": self.cfg, **context.gather(self.cfg)}
        self.reader = threading.Thread(target=self._read_field, daemon=True)
        self.reader.start()  # UIA off the hotkey/Tk threads; live clean uses whatever has arrived
        self.app, self.title = self.ctx["app"], self.ctx["title"]
        where = f"{self.app} \u2014 {self.title[:60]}" if self.title else self.app
        self.ov.post(lambda: self.ov.show("#ff4444", f"{self.label} \u2192 {where}"))

    def _read_field(self):
        if self.app in self.cfg["apps"].get("no_copy", NO_COPY):
            return  # terminal scrollback must not go into prompts
        self.ctx.update(context.read_field(self.cfg))

    def _poll_loop(self):
        while self.held():
            time.sleep(0.6)
            # ponytail: whole-buffer retranscribe, chunk if dictations exceed ~60s
            r = transcribe(list(self.buf))
            if r and r != self.raw:
                self.raw = r
                self.ov.post(lambda r=r, c=self.cleaned[1]: self.ov.update(r, c))
                self._maybe_live(r)

    def _maybe_live(self, r):
        idle = self.inflight[1] is None or self.inflight[1].is_set()
        if self.mode == "dictate" and self.cfg["llm"]["live_clean"] and idle:
            self.inflight = (r, threading.Event())
            threading.Thread(target=self._live, args=self.inflight, daemon=True).start()

    def _live(self, r, ev):
        try:
            had_ctx = "before" in self.ctx  # a context-less result must not be reused once context arrives
            c = brain.clean(r, self.ctx)
            self.cleaned = (r, c, had_ctx)
            if self.current() and r == self.raw:  # a late result must not paint a newer session
                self.ov.post(lambda: self.ov.update(r, c))
        except Exception as e:
            log.info("live clean failed: %s", e)
        finally:
            ev.set()

    def _finish_dictate(self):
        raw = self.raw
        self.ov.post(lambda: self.ov.header("#ffaa00", f"Cleaning \u2192 {self.app}"))
        if self.cleaned[0] != raw and self.inflight[0] == raw:
            self.inflight[1].wait(self.cfg["llm"].get("timeout", 15))  # reuse the in-flight request
        if self.cleaned[0] == raw and self.cleaned[2] == ("before" in self.ctx):
            return self.cleaned[1]
        try:
            return brain.clean(raw, self.ctx)
        except Exception as e:
            log.info("clean failed, pasting raw: %s", e)
            return raw

    def _finish_command(self):
        sel, via = self.ctx.get("selected") or None, "UIA"
        if not sel and self.app not in self.cfg["apps"].get("no_copy", NO_COPY):
            # ponytail: VS Code's empty-selection ctrl+c copies the whole line
            sel, via = win.copy_selection(), "clipboard"
        note = f" (selection: {len(sel)} chars via {via})" if sel else " (no selection)"
        self.ov.post(lambda: self.ov.header("#ffaa00", f"{self.label} \u2192 {self.app}{note}"))
        try:
            return brain.rewrite(sel, self.raw, self.ctx)
        except Exception as e:
            log.info("rewrite failed: %s", e)  # pasting would clobber the selection
            self.ov.post(lambda: self.ov.update(self.raw, "rewrite failed"))
            time.sleep(1.5)
            return None

    def _paste_and_log(self, final):
        win.insert(final, self.cfg)
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "app": self.app, "title": self.title,
                 "tone": self.ctx["tone"], "raw": self.raw, "final": final}
        store.append_history(entry)
