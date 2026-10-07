"""The shared microphone stream. Default ("warm"): the stream is opened once and kept STOPPED between dictations, so a session
starts it in a few ms instead of paying the ~100-200 ms device open on key-down that clipped first syllables ("What" -> "But");
nothing is captured while stopped and Windows shows no microphone-in-use indicator. Opt-in `[audio] preroll_ms > 0`: the
stream runs all the time and a Ring keeps the last pre-roll seconds, so a dictation includes what was said just before the key
went down - at the price of the mic showing as in use while murmur runs. Audio only reaches a Session while one is attached.
Pure logic (Ring) has no sounddevice import and is unit-tested."""
import logging
from collections import deque

from . import store

log = logging.getLogger("murmur")
RATE = 16000
_missing = set()  # device names already warned about


def device_index(cfg):
    """Configured input device index (resolved by name each time; indices shift between boots), None = default."""
    import sounddevice as sd
    name = cfg.get("audio", {}).get("device", "")
    idx = store.resolve_device(name, sd.query_devices())
    if name and idx is None and name not in _missing:
        _missing.add(name); log.warning("input device %r not found, using the default", name)
    return idx


class Attached:
    """Stream stand-in while a session is fed by the shared Monitor: stop() detaches, close() is a no-op."""

    def __init__(self, mon):
        self.mon = mon

    def stop(self):
        self.mon.detach()

    def close(self):
        pass


class Ring:
    """Last `seconds` of frames. push(frame) keeps the window; take() returns the frames oldest-first and empties it."""

    def __init__(self, seconds):
        self.n, self.frames, self.have = int(seconds * RATE), deque(), 0

    def push(self, frame):
        self.frames.append(frame); self.have += len(frame)
        while self.frames and self.have - len(self.frames[0]) >= self.n:
            self.have -= len(self.frames.popleft())

    def take(self):
        out = list(self.frames)
        self.frames.clear(); self.have = 0
        return out


class Monitor:
    """One sounddevice InputStream kept open; attach(sink) routes frames to a session and returns the pre-roll (empty in warm mode)."""

    def __init__(self, device_fn, preroll_s=0.0):
        self.device_fn, self.ring, self.sink, self.stream, self.device = device_fn, Ring(max(preroll_s, 0.1)), None, None, None
        self.preroll = preroll_s > 0  # False = warm mode: opened, started only while a session is attached

    def _cb(self, d, *_):
        sink = self.sink
        if sink:
            sink(d, *_)
        else:
            self.ring.push(d.copy())

    def start(self):
        """(Re)open the stream on the configured device; it runs now only in pre-roll mode. Returns True when the mic is usable."""
        import sounddevice as sd
        self.close()
        try:
            self.device = self.device_fn()
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=self._cb, device=self.device)
            if self.preroll:
                self.stream.start()
            return True
        except Exception as e:
            log.info("mic monitor unavailable (%s); sessions will open the mic on key-down", e)
            self.stream = None
            return False

    def ok(self):
        return self.stream is not None and (self.stream.active or not self.preroll)

    def attach(self, sink):
        """Route audio to `sink(frame)` and return the pre-roll frames. Reopens the stream when the configured device changed.
        None when the monitor is not live (the caller opens its own stream)."""
        if self.device_fn() != self.device or not self.ok():
            self.start()
        if not self.ok():
            return None
        pre = self.ring.take()
        self.sink = sink
        if not self.stream.active:
            self.stream.start()  # warm mode: a few ms, no device open on the hotkey path
        return pre

    def detach(self):
        self.sink = None
        if not self.preroll and self.stream:
            try:
                self.stream.stop()  # back to warm: nothing captured, no mic-in-use indicator
            except Exception:
                log.exception("mic stop failed")

    def close(self):
        if self.stream:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
        self.stream = None
