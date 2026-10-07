"""Silero VAD, streamed: a ~2 MB neural voice-activity model (via onnx-asr's export) scored per 32 ms frame of the live mic.
Hands-free endpointing used an energy threshold sampled from the first 300 ms; when you start talking right at the tap that
window holds your voice, the bar jumps and a quiet mic is "silent" mid-sentence (measured on Ethan's takes: 0.2 s of speech
heard out of 3 s, auto-stop 2.7 s into a 9 s dictation). Silero does not depend on level. Bundled like the speech model
(packaging/models/silero, `python -m murmur fetch-vad`); missing -> load() returns None and capture falls back to energy.
Not thread-safe: one stream (the session's audio callback) at a time, reset() per session."""
import logging
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger("murmur")
HOP, CTX, RATE = 512, 64, 16000  # Silero v5 at 16 kHz: 512-sample hops with 64 samples of left context


def model_dir():
    """Bundled folder: _internal/packaging/models/silero in the exe, packaging/models/silero in a checkout."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "models" / "silero"


class Silero:
    """feed(block) -> speech probability for this audio block (max over the hops it completed; the previous value while a
    hop is still filling). `sess` is anything with onnxruntime's run(outputs, feeds) -> [output, state]."""

    def __init__(self, sess):
        self.sess = sess
        self.reset()

    def reset(self):
        self.state, self.ctx = np.zeros((2, 1, 128), np.float32), np.zeros(CTX, np.float32)
        self.pend, self.prob, self.dead = np.zeros(0, np.float32), 0.0, False

    def _hop(self, hop):
        feeds = {"input": np.concatenate([self.ctx, hop])[None], "state": self.state, "sr": np.array(RATE, np.int64)}
        out, self.state = self.sess.run(["output", "stateN"], feeds)
        self.ctx = hop[-CTX:]
        return float(np.asarray(out).reshape(-1)[0])

    def feed(self, block):
        self.pend = np.concatenate([self.pend, np.asarray(block, np.float32).reshape(-1)])
        probs = []
        while len(self.pend) >= HOP:
            probs.append(self._hop(self.pend[:HOP]))
            self.pend = self.pend[HOP:]
        self.prob = max(probs) if probs else self.prob
        return self.prob

    def safe(self, block):
        """feed() for the audio callback: None (energy VAD) once the model has failed this session; never raises (an
        exception inside the PortAudio callback would kill the stream)."""
        if self.dead:
            return None
        try:
            return self.feed(block)
        except Exception:
            log.exception("Silero VAD failed; energy VAD for the rest of this session")
            self.dead = True
            return None


def load(path=None):
    """Silero from the bundled folder (or `path`), or None when it is missing or fails to load (energy VAD takes over)."""
    f = Path(path or model_dir()) / "silero_vad.onnx"
    if not f.is_file():
        log.info("no Silero VAD at %s: hands-free uses the energy VAD", f)
        return None
    try:
        import onnxruntime as rt
        so = rt.SessionOptions()
        so.intra_op_num_threads = so.inter_op_num_threads = 1  # 0.06 ms a frame; never compete with the STT
        return Silero(rt.InferenceSession(str(f), so, providers=["CPUExecutionProvider"]))
    except Exception:
        log.exception("Silero VAD failed to load: hands-free uses the energy VAD")
        return None


def fetch(dest=None):
    """Download Silero into dest (default model_dir()) if missing; returns the folder. Build-time only."""
    import onnx_asr
    dest = Path(dest) if dest else model_dir()
    onnx_asr.load_vad("silero", dest)
    return dest
