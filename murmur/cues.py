"""Soft audio cues. tone_wav is pure; play is a no-op off Windows or when [ui] sounds is false."""
import io, logging, os, threading, wave
import numpy as np
from . import store

log = logging.getLogger("murmur")

RATE = 22050
CUES = {"start": ([660, 880], 70), "stop": ([880, 660], 70), "done": ([990], 60), "error": ([220], 140), "cancel": ([440], 60)}  # name -> (freqs Hz, ms per note)
_cache = {}


def tone_wav(freqs, ms, volume):
    """16-bit mono 22050 Hz WAV bytes: one `ms` sine note per freq, 5 ms fade in/out each, peak = volume (0-1)."""
    n, fade, parts = int(RATE * ms / 1000), int(RATE * 0.005), []
    env = np.ones(n); env[:fade] = np.linspace(0, 1, fade); env[-fade:] = np.linspace(1, 0, fade)
    for f in freqs:
        parts.append(np.sin(2 * np.pi * f * np.arange(n) / RATE) * env)
    pcm = (np.concatenate(parts) * min(max(volume, 0.0), 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())
    return buf.getvalue()


def cue_path(name, vol):
    """WAV file for a cue, written once (winsound can't play from memory asynchronously)."""
    p = store.HOME / "cues" / f"{name}-{vol}.wav"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # two threads may race on the first play
        tmp.write_bytes(tone_wav(*CUES[name], vol))
        os.replace(tmp, p)
    return p


def play(name, cfg):
    """Play cue `name` asynchronously at [ui] sound_volume; never raises (failures are logged)."""
    ui = cfg.get("ui", {})
    if not ui.get("sounds", True):
        return
    try:
        import winsound
    except ImportError:
        return  # not Windows
    try:
        key = (name, float(ui.get("sound_volume", 0.25)))
        if key not in _cache:
            _cache[key] = str(cue_path(*key))
        winsound.PlaySound(_cache[key], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception as e:
        log.warning("audio cue %s failed: %s", name, e)
