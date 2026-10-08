"""Pure capture decisions: key gesture (hold / tap-latch), voice-activity auto-stop, long-dictation segment commit."""
import statistics

IGNORE_S, FLOOR_S, MIN_RMS, MAX_FLOOR = 0.25, 0.3, 0.008, 0.01  # skip the start-cue window, then sample the floor
COMMIT_AUDIO_S, COMMIT_PAUSE_MS, MAX_OPEN_S = 8.0, 700, 30.0  # commit: >= 8 s then a >= 700 ms pause; or force at 30 s
SPEECH_P, KEEP_P = 0.5, 0.35  # Silero: speech starts at p >= 0.5 and lasts while p >= 0.35 (its own hysteresis defaults)


def gesture(state, event, t, tap_ms=300):
    """Dictate-key state machine. state = (name, t_press), start with ("idle", 0.0); event = "press" | "release" |
    "other" (another key went down while the dictate key is held: a chord, not dictation) | "stop" (the session ended some
    other way: Esc, Enter, silence). Returns (state, action) with action "start" | "latch" | "stop" | "cancel" | "none".
    A release under tap_ms latches the session (hands-free); the next press stops it and its release is swallowed; a chord
    release cancels it silently; key auto-repeat is ignored, also after the session was ended while the key is still down
    ("spent" until its release). tap_ms=0 disables latching (hold-only)."""
    name, t0 = state
    idle = ("idle", 0.0)
    if event == "stop":
        return (("spent", t), "none") if name in ("down", "chord", "stopping") else (idle, "none")
    if event == "other":  # only near the press: a stray key during a long held dictation must not discard it
        return (("chord", t0), "none") if name == "down" and (t - t0) * 1000 < max(tap_ms, 1000) else (state, "none")
    if event == "press":
        if name == "idle":
            return ("down", t), "start"
        if name == "latched":
            return ("stopping", t), "stop"
        return (("spent", t), "none") if name == "spent" else (state, "none")  # auto-repeat (refreshes spent so staleness measures silence)
    if name == "down":
        return (("latched", t0), "latch") if (t - t0) * 1000 < tap_ms else (idle, "stop")
    if name == "chord":
        return idle, "cancel"
    return (idle, "none") if name in ("stopping", "spent") else (state, "none")


def stale(state, now, session_alive, age=2.0):
    """True if the gesture state is stuck: not idle, no session, and untouched for `age` seconds (a missed key release:
    elevated window, Win+L, hook timeout). Every non-idle state carries a monotonic timestamp."""
    return state[0] != "idle" and not session_alive and now - state[1] > age


MIN_SPEECH_S = 0.25  # a take with less detected speech than this and only a few words is the model inventing ("Yeah.")


def hallucinated(raw, speech_s):
    """Parakeet answers near-silence with a short filler ("Yeah.", "Yes.", "Thank you."). True when the VAD heard under
    MIN_SPEECH_S of speech and the transcript is at most three words; longer output on a quiet mic is still kept."""
    return bool(raw) and speech_s < MIN_SPEECH_S and len(raw.split()) <= 3


STAGES = (("tail", "release", "stopped"), ("stt", "stopped", "heard"), ("clean", "heard", "cleaned"), ("paste", "cleaned", "pasted"))


def stages(marks):
    """Seconds per stage from monotonic marks (release = key up, stopped = mic off after the tail, heard = transcript,
    cleaned = final text, pasted = in the app) plus total release -> pasted; a stage whose marks are missing is left out."""
    out = {name: round(marks[b] - marks[a], 3) for name, a, b in STAGES if a in marks and b in marks}
    if "release" in marks and "pasted" in marks:
        out["total"] = round(marks["pasted"] - marks["release"], 3)
    return out


def vad_new():
    return {"t": 0.0, "floor": [], "speech": False, "on": False, "quiet_ms": 0.0, "speech_s": 0.0, "pauses": 0}


def vad_step(state, rms, dt, cfg, prob=None):
    """One audio block (rms, dt seconds). The first 250 ms are ignored (the start cue may leak into the mic); the floor is
    the median RMS of the next 300 ms, capped at 0.01 so speaking at once can't raise the bar; speech = rms > max(3 x floor,
    0.008). Returns (new state, decision): "finish" after cfg silence_ms (2000) of non-speech following speech, "cancel" if
    no speech by cfg no_speech_ms (6000), else None. state["quiet_ms"] is the current run of non-speech. For the speaking
    metrics it also totals state["speech_s"] (seconds of speech; the 250 ms ignore window never counts and the floor window
    counts blocks above 0.008) and state["pauses"] (speech resuming after a >= 700 ms gap).
    prob (a Silero speech probability for this block, see vad.py) replaces the energy test when given: no floor window, a
    block is speech while p >= SPEECH_P, or p >= KEEP_P right after speech (state["on"])."""
    s = dict(state)
    s["t"] = state["t"] + dt
    if s["t"] <= IGNORE_S + 1e-9:
        return s, None
    if prob is not None:
        s["on"] = prob >= SPEECH_P or (state.get("on", False) and prob >= KEEP_P)
        return _advance(state, s, s["on"], dt, cfg)
    if s["t"] <= IGNORE_S + FLOOR_S + 1e-9:
        s["floor"] = state["floor"] + [rms]
        s["speech_s"] = state["speech_s"] + (dt if rms > MIN_RMS else 0.0)
        return s, None
    floor = min(statistics.median(s["floor"]), MAX_FLOOR) if s["floor"] else 0.0
    return _advance(state, s, rms > max(3 * floor, MIN_RMS), dt, cfg)


def _advance(state, s, speaking, dt, cfg):
    """Shared tail of vad_step once a block is classified: speech/pause bookkeeping and the finish/cancel decision."""
    if speaking:
        if state["speech"] and state["quiet_ms"] >= COMMIT_PAUSE_MS:
            s["pauses"] = state["pauses"] + 1
        s["speech"], s["quiet_ms"], s["speech_s"] = True, 0.0, state["speech_s"] + dt
    else:
        s["quiet_ms"] = state["quiet_ms"] + dt * 1000
    if s["speech"] and s["quiet_ms"] >= cfg.get("silence_ms", 2000):
        return s, "finish"
    if not s["speech"] and s["t"] * 1000 >= cfg.get("no_speech_ms", 6000):
        return s, "cancel"
    return s, None


def commit_kind(segment_s, quiet_ms):
    """Long dictation: "pause" once the open segment holds >= 8 s of audio and a >= 700 ms pause follows; "cap" when it
    passes 30 s with no pause (a noisy room) so re-transcription cost stays bounded; else None."""
    if segment_s >= COMMIT_AUDIO_S and quiet_ms >= COMMIT_PAUSE_MS:
        return "pause"
    return "cap" if segment_s >= MAX_OPEN_S else None


def should_commit(segment_s, quiet_ms):
    return commit_kind(segment_s, quiet_ms) is not None


def cut_point(rms, frame_s, tail_s=2.0, win_s=0.1):
    """How many leading frames to commit when forcing a cut: the end of the quietest ~100 ms window within the last 2 s
    of audio. rms = per-frame RMS list, frame_s = seconds per frame."""
    n, per_win = len(rms), max(1, round(win_s / frame_s))
    first = max(0, n - max(per_win, round(tail_s / frame_s)))
    best, cut = None, n
    for i in range(first, n - per_win + 1):
        level = sum(rms[i:i + per_win]) / per_win
        if best is None or level <= best:
            best, cut = level, i + per_win
    return cut


QUNS_D3D_FULL_SCREEN = 3  # SHQueryUserNotificationState: a Direct3D exclusive-fullscreen app (a game) is in front


def should_ignore(app, cfg, quns=0):
    """Should the hotkeys do nothing right now? True when the foreground process is in [apps] exclude, or [apps]
    pause_fullscreen (default on) and Windows reports an exclusive-fullscreen D3D app (quns == 3)."""
    apps = cfg.get("apps", {})
    if app and app.lower() in [a.lower() for a in apps.get("exclude", []) if isinstance(a, str)]:
        return True
    return bool(apps.get("pause_fullscreen", True)) and quns == QUNS_D3D_FULL_SCREEN
