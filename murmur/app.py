"""Tray app wiring: card overlay, hotkeys, tray icon and menu."""
import logging, os, threading, time
from types import SimpleNamespace
import keyboard, pystray
from . import brain, capture, card, context, cues, settings, store, win
from .session import Session
from .tray import TOOLTIPS, tray_image

log = logging.getLogger("murmur")
S = {"mode": None, "held": False, "paused": False, "sid": 0, "last_paste": (0.0, "", ""), "capturing": False, "ready": False, "session": None, "g": ("idle", 0.0), "dropped": False}  # shared hotkey/session state
_learn_lock = threading.Lock()  # a double press must not race copy_selection (clipboard)


def _hooks(sid, mark, retry):
    def held():
        return S["held"]

    def stop():
        S["held"] = False
        S["g"] = capture.gesture(S["g"], "stop", time.monotonic())[0]  # Esc/Enter/silence ended it: forget a latch

    def current():
        return S["sid"] == sid

    def done():
        S["mode"] = None; S["session"] = None
        if S["g"][0] == "latched":
            S["g"] = ("idle", 0.0)

    def pasted(app, text=""):
        """Record a paste of `text` into `app`; None clears it."""
        S["last_paste"] = (time.time(), app, text) if app else (0.0, "", "")

    def last_paste():
        return S["last_paste"]
    return SimpleNamespace(held=held, stop=stop, current=current, done=done, mark=mark, pasted=pasted, last_paste=last_paste, retry=retry)


def probe_mic(cfg, ov):
    """Retry on the error card: open the input for 0.5 s; show 'mic ready' if it works, else the error again."""
    def run():
        try:
            import sounddevice as sd
            dev = store.resolve_device(cfg.get("audio", {}).get("device", ""), sd.query_devices())
            with sd.InputStream(samplerate=16000, channels=1, device=dev):
                time.sleep(0.5)
        except Exception:
            log.exception("mic probe failed")
            ov.post(lambda: ov.flash("error", handlers={"retry": lambda: probe_mic(cfg, ov)}))
            return
        ov.post(lambda: ov.flash("done", "Microphone is working. Hold the key to dictate.", label="Mic ready"))
    threading.Thread(target=run, daemon=True).start()


def _tap_ms(cfg):
    """Tap-to-latch window in ms; 0 when [dictation] toggle is off (hold-only)."""
    d = cfg.get("dictation", {})
    return d.get("tap_ms", 300) if d.get("toggle", True) else 0


def press(mode, cfg, ov, mark):
    log.info("hotkey %s press (mode=%s ready=%s capturing=%s)", mode, S["mode"], S["ready"], S["capturing"])
    if mode == "dictate":
        if capture.stale(S["g"], time.monotonic(), bool(S["session"])):
            S["g"] = ("idle", 0.0)  # a release was missed: don't leave the dictate key dead
        S["g"], action = capture.gesture(S["g"], "press", time.monotonic(), _tap_ms(cfg))
        if action == "stop":  # second tap ends a hands-free session; this press must not start a new one
            S["held"] = False
        if action != "start":
            return
    if S["mode"] or S["paused"] or S["capturing"] or not S["ready"]:
        if mode == "dictate":
            S["g"] = ("idle", 0.0)
        return
    S.update(mode=mode, held=True, sid=S["sid"] + 1)
    if mode == "command":
        keyboard.send(-0xE8)  # mask alt so a lone alt tap doesn't open the app menu
    sess = Session(mode, cfg, ov, _hooks(S["sid"], mark, lambda: probe_mic(cfg, ov)))
    S["session"] = sess
    threading.Thread(target=sess.run, daemon=True).start()


def other_key(event, dictate_key, cfg=None):
    """Global non-suppressing hook: any other key going down while the dictate key is held marks a chord."""
    if event.event_type == "down" and S["g"][0] == "down" and not store.key_matches(event.name, dictate_key):
        S["g"] = capture.gesture(S["g"], "other", time.monotonic(), _tap_ms(cfg or {}))[0]


def release(mode, cfg=None):
    if mode == "dictate":
        S["g"], action = capture.gesture(S["g"], "release", time.monotonic(), _tap_ms(cfg or {}))
        if action == "latch" and S["session"] and S["mode"] == "dictate":
            S["session"].latch()  # tap: keep recording hands-free
        elif action == "latch":
            S["g"] = ("idle", 0.0)
        elif action == "stop" and S["mode"] == "dictate":
            S["held"] = False
        elif action == "cancel" and S["session"] and S["mode"] == "dictate":
            S["session"].discard()  # a chord (e.g. right ctrl + c), not dictation: drop it silently
        return
    if S["mode"] == mode:
        S["held"] = False


def fix_last(root, row):
    """Editor for one history row; saving teaches the dictionary and records the correction."""
    import tkinter as tk
    final, ts = row["final"], row["ts"]  # ts pins the row even if a newer dictation lands
    top = tk.Toplevel(root); top.title("murmur: fix"); top.attributes("-topmost", True)
    box = tk.Text(top, width=70, height=10, wrap="word"); box.insert("1.0", final); box.pack(padx=8, pady=8)

    def save():
        edited = box.get("1.0", "end").strip()
        new = store.add_terms(brain.learn_dictionary(final, edited))
        store.correct_last(ts, final, edited); log.info("learned: %s", new); top.destroy()
    tk.Button(top, text="Save", command=save).pack(pady=(0, 8))


def fix_newest(root):
    if S["capturing"]:
        return
    hist = store.read_history(1)
    if hist:
        fix_last(root, hist[-1])


def _selection(cfg, app):
    """Selected text via UIA (1 s cap, own thread), else the clipboard unless the app is no_copy."""
    got = {}
    t = threading.Thread(target=lambda: got.update(context.read_field(cfg)), daemon=True)
    t.start(); t.join(1.0)
    if got.get("selected"):
        return got["selected"].strip()
    if got.get("sel_known") or app in store.no_copy(cfg):  # UIA's empty selection is authoritative; VS Code's ctrl+c copies the line
        return ""
    for _ in range(20):  # the hotkey's modifiers must be up or ctrl+c becomes ctrl+alt+c
        if not (keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt")):
            return (win.copy_selection() or "").strip()
        time.sleep(0.05)
    return None  # still held: never send ctrl+c


def _learn_word(cfg, ov):
    if not _learn_lock.acquire(blocking=False):
        return
    try:
        for _ in range(20):  # wait for a cancelled session to release the card, or its Learned flash would be skipped
            if not S["session"] or not S["dropped"]:
                break
            time.sleep(0.05)
        got = _selection(cfg, win.active_app()[0])
        text = None if got is None else brain.clean_word(got)
    finally:
        _learn_lock.release()
    log.info("learn_word: selection %r", text)
    if text is None:
        flash = ("nothing", "Release the keys and try again.", "Keys held")
    elif not text:
        flash = ("nothing", "Select a word first, then press the hotkey.", "Nothing selected")
    elif len(text.split()) > 4:
        flash = ("nothing", "Select 1\u20134 words to add to your dictionary.", "Too long")
    else:
        store.add_terms([text]); log.info("learned word: %s", text)
        flash = ("learned", f"\u201c{text}\u201d added to your dictionary", None)
    if S["session"] and not S["dropped"]:  # a live dictation keeps the card (flash is skipped): give audible feedback instead
        log.info("learn_word during a live session: %s", flash[0])
        cues.play("done" if flash[0] == "learned" else "cancel", cfg)
    ov.post(lambda: ov.flash(*flash))


def learn_word(cfg, ov):
    log.info("hotkey learn_word fired (capturing=%s)", S["capturing"])
    if S["capturing"]:
        return
    s = S["session"]
    young = s and (time.time() - getattr(s, "t0", time.time()) < 0.3 or not s.vad["speech"])
    S["dropped"] = bool(young)
    if young:  # a chord that just started a session (or one with no speech yet) is dropped silently so the card can show
        s.discard()
    threading.Thread(target=_learn_word, args=(cfg, ov), daemon=True).start()  # off the hook thread


def _set_icon(icon, state, key):
    """Tray icon + tooltip for a state (paused wins over idle). A fresh copy is assigned so the shell always updates."""
    state = "paused" if S["paused"] and state == "idle" else state
    icon.icon = tray_image(state).copy()
    icon.title = TOOLTIPS[state].format(key=key)


def quit_app(icon, ov):
    """Stop any running session without pasting, hide the card, then exit."""
    if S["session"]:
        S["session"].cancel()
    S["held"] = False
    ov.post(ov.hide)
    icon.stop()
    ov.post(ov.root.quit)


def tray_app(cfg, learn, boot):
    """boot(cfg) loads the speech model and warms the LLM; it runs on a thread while the loading card shows."""
    ov = card.Card()
    hk = cfg["hotkeys"]
    keys = {m: keyboard.normalize_name(hk[m]) for m in ("dictate", "command")}
    learn_key = hk.get("learn_word", "ctrl+alt+d")

    def mark(state):
        _set_icon(icon, state, keys["dictate"])

    for mode, key in keys.items():
        keyboard.on_press_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and press(m, cfg, ov, mark))
        keyboard.on_release_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and release(m, cfg))
    keyboard.hook(lambda e: other_key(e, keys["dictate"], cfg))
    keyboard.add_hotkey(hk["fix_last"], lambda: (log.info("hotkey fix_last fired"), ov.post(lambda: fix_newest(ov.root))))
    keyboard.add_hotkey(learn_key, lambda: learn_word(cfg, ov))  # fires on press; trigger_on_release never fired in the keyboard lib

    def toggle(i, it):
        S["paused"] = not S["paused"]
        mark("idle")

    def open_settings(i, it):
        ov.post(lambda: settings.open_settings(ov.root, cfg, fix_last))
    menu = pystray.Menu(
        pystray.MenuItem("Settings\u2026", open_settings, default=True),
        pystray.MenuItem(lambda it: "Resume dictation" if S["paused"] else "Pause dictation", toggle, checked=lambda it: S["paused"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open config folder", lambda i, it: os.startfile(store.HOME)),
        pystray.MenuItem("Learn style", lambda i, it: threading.Thread(target=learn, args=(cfg,), daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit murmur", lambda i, it: quit_app(i, ov)))
    icon = pystray.Icon("murmur", tray_image("idle"), TOOLTIPS["idle"].format(key=keys["dictate"]), menu)
    icon.run_detached()
    ov.post(lambda: ov.flash("loading"))

    def start():
        try:
            boot(cfg)
        except Exception:
            log.exception("startup failed")
            ov.post(lambda: ov.flash("failed"))
            return
        S["ready"] = True
        ov.post(ov.hide)
        log.info("ready: hold %s to dictate, %s for commands, %s adds the selected word", keys["dictate"], keys["command"], learn_key)
        if cfg.get("ui", {}).get("startup_toast", True):
            try:
                icon.notify(f"murmur is running \u2014 hold {keys['dictate']} to dictate", "murmur")
            except Exception:
                log.exception("startup toast failed")
    threading.Thread(target=start, daemon=True).start()
    ov.run()
    os._exit(0)  # PortAudio/onnxruntime teardown can hang
