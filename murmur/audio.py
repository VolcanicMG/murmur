"""Speech detection for the decode path, plus an audio-conditioning experiment the app deliberately does NOT use.

What the app uses: speech_mask()/has_speech() — per-20 ms-frame energy against the recording's own noise floor (quietest 10 %
+ 12 dB). session.transcribe skips any segment without speech: Parakeet answers silence with "Yeah."/"Okay.", and the
long-dictation split hands it exactly such a segment after the last word (every silent tail of Ethan's takes decoded alone
gave 'Okay.').

condition(): the community recipe (trim silence, cap pauses > MAX_PAUSE_S, boost a quiet mic toward TARGET_DBFS, pad both
ends; NeMo-Speech #15757, hyprwhspr #268, Plainsong #173). Measured on 17 of Ethan's takes with evals/stt_compare (raw vs
conditioned, Parakeet v2 int8): every variant, including trim+pad alone, made the output WORSE — "2:30" became "two
thirty", "example.com" became "example dot com", "actually" was dropped, "launch" became "lunch". Parakeet's built-in
number/URL formatting leans on the untouched context, so the live path decodes the raw take. Kept for stt_compare so a
different mic or model can re-run the comparison. Pure numpy, no I/O; unit-tested."""
import numpy as np

RATE, FRAME_S = 16000, 0.02
PAD_S, TAIL_S, MAX_PAUSE_S = 0.25, 0.3, 1.5
TARGET_DBFS, QUIET_DBFS, MAX_GAIN_DB, FLOOR_DB = -22.0, -32.0, 14.0, 12.0


def _frames(x):
    n = int(RATE * FRAME_S)
    k = len(x) // n
    return x[: k * n].reshape(k, n), n


def speech_mask(x):
    """Per-20 ms-frame bool: louder than the noise floor (quietest 10 % of frames) by FLOOR_DB. All-False for silence."""
    fr, _ = _frames(x)
    if len(fr) < 5:
        return np.zeros(len(fr), bool)
    rms = np.sqrt((fr * fr).mean(axis=1)) + 1e-9
    floor = np.percentile(rms, 10)
    thr = max(floor * 10 ** (FLOOR_DB / 20), 0.003)  # 0.003: a dead-quiet room must not promote its own hiss to speech
    return rms > thr


def has_speech(x):
    """True when at least two frames (40 ms) rise above the floor: decode this; otherwise don't (the model would invent)."""
    return int(speech_mask(np.asarray(x, np.float32)).sum()) >= 2


def condition(x):
    """float32 mono 16 kHz -> conditioned copy (see module doc). Audio with no detectable speech comes back unchanged."""
    x = np.asarray(x, np.float32)
    mask = speech_mask(x)
    if not mask.any():
        return x
    fr, n = _frames(x)
    idx = np.flatnonzero(mask)
    lo, hi = max(0, idx[0] - int(PAD_S / FRAME_S)), min(len(fr), idx[-1] + 1 + int(TAIL_S / FRAME_S))
    fr, mask = fr[lo:hi], mask[lo:hi]
    # shorten long pauses
    keep, run = np.ones(len(fr), bool), 0
    max_f = int(MAX_PAUSE_S / FRAME_S)
    for i, sp in enumerate(mask):
        run = 0 if sp else run + 1
        if run > max_f:
            keep[i] = False
    fr, mask = fr[keep], mask[keep]
    y = fr.reshape(-1)
    # a quiet mic only: linear gain, never past the peak cap
    speech = fr[mask].reshape(-1)
    rms = float(np.sqrt((speech * speech).mean())) if len(speech) else 0.0
    if 0 < rms < 10 ** (QUIET_DBFS / 20):
        gain = min(10 ** (TARGET_DBFS / 20) / rms, 10 ** (MAX_GAIN_DB / 20), 0.95 / max(float(np.abs(y).max()), 1e-6))
        y = y * gain
    pad = np.zeros(int(PAD_S * RATE), np.float32)
    return np.concatenate([pad, y.astype(np.float32), pad])
