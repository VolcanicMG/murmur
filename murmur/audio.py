"""Condition a recording before Parakeet sees it: what the NeMo/Parakeet community found moves accuracy on dictation audio.
  1. trim: cut leading/trailing silence down to PAD_S (long tails can empty a short utterance; NeMo-Speech #15757)
  2. pauses: shorten any silence longer than MAX_PAUSE_S to MAX_PAUSE_S (speech after a long pause gets scored as silence)
  3. level: boost-only loudness normalisation of the speech frames toward TARGET_DBFS, capped at MAX_GAIN_DB, soft limiter
  4. pad: PAD_S of silence at both ends (a lead-in helps the first word; the caller retries unpadded if the decode is empty)
Silence is judged per 20 ms frame against the recording's own noise floor (quietest 10 % + 12 dB), so it adapts to the mic.
Pure numpy, no I/O; unit-tested."""
import numpy as np

RATE, FRAME_S = 16000, 0.02
PAD_S, MAX_PAUSE_S, TARGET_DBFS, MAX_GAIN_DB, FLOOR_DB = 0.25, 1.0, -20.0, 20.0, 12.0


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


def condition(x):
    """float32 mono 16 kHz -> conditioned copy (see module doc). Audio with no detectable speech comes back unchanged."""
    x = np.asarray(x, np.float32)
    mask = speech_mask(x)
    if not mask.any():
        return x
    fr, n = _frames(x)
    idx = np.flatnonzero(mask)
    pad_f = int(PAD_S / FRAME_S)
    lo, hi = max(0, idx[0] - pad_f), min(len(fr), idx[-1] + 1 + pad_f)
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
    # boost-only level toward TARGET_DBFS over speech frames, soft limiter
    speech = fr[mask].reshape(-1)
    rms = float(np.sqrt((speech * speech).mean())) if len(speech) else 0.0
    if rms > 0:
        gain = min(10 ** (TARGET_DBFS / 20) / rms, 10 ** (MAX_GAIN_DB / 20))
        if gain > 1.0:
            y = np.tanh(y * gain * 0.9) / 0.9  # ~linear below 0.5, never clips
    pad = np.zeros(int(PAD_S * RATE), np.float32)
    return np.concatenate([pad, y.astype(np.float32), pad])
