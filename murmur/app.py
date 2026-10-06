"""Tray app wiring: card overlay, hotkeys, tray icon and menu."""
import logging, os, threading, time
from types import SimpleNamespace
import keyboard, pystray
from . import brain, card, context, settings, store, win
from .session import Session
from .tray import TOOLTIPS, tray_image

log = logging.getLogger("murmur")
S = {"mode": None, "held": False, "paused": False, "sid": 0, "last_paste": (0.0, ""), "capturing": False, "ready": False, "session": None}  # shared hotkey/session state
_learn_lock = threading.Lock()  # a double press must not race copy_selection (clipboard)


def _hooks(sid, mark, retry):
    def held():
        return S["held"]

    def stop():
        S["held"] = False

    def current():
        return S["sid"] == sid

    def done():
        S["mode"] = None; S["session"] = None

    def pasted(app):
        """Record a paste into `app`; None clears it."""
        S["last_paste"] = (time.time(), app) if app else (0.0, "")

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


def press(mode, cfg, ov, mark):
    if S["mode"] or S["paused"] or S["capturing"] or not S["ready"]:
        return
    S.update(mode=mode, held=True, sid=S["sid"] + 1)
    if mode == "command":
        keyboard.send(-0xE8)  # mask alt so a lone alt tap doesn't open the app menu
    sess = Session(mode, cfg, ov, _hooks(S["sid"], mark, lambda: probe_mic(cfg, ov)))
    S["session"] = sess
    threading.Thread(target=sess.run, daemon=True).start()


def release(mode):
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
    if app in store.no_copy(cfg):
        return ""
    for _ in range(20):  # the hotkey's modifiers must be up or ctrl+c becomes ctrl+alt+c
        if not (keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt")):
            break
        time.sleep(0.05)
    return (win.copy_selection() or "").strip()


def _learn_word(cfg, ov):
    if not _learn_lock.acquire(blocking=False):
        return
    try:
        text = brain.clean_word(_selection(cfg, win.active_app()[0]))
    finally:
        _learn_lock.release()
    if not text:
        flash = ("nothing", "Select a word first, then press the hotkey.", "Nothing selected")
    elif len(text.split()) > 4:
        flash = ("nothing", "Select 1\u20134 words to add to your dictionary.", "Too long")
    else:
        store.add_terms([text]); log.info("learned word: %s", text)
        flash = ("learned", f"\u201c{text}\u201d added to your dictionary", None)
    ov.post(lambda: ov.flash(*flash))


def learn_word(cfg, ov):
    if S["capturing"]:
        return
    threading.Thread(target=_learn_word, args=(cfg, ov), daemon=True).start()  # off the hook thread


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
        state = "paused" if S["paused"] and state == "idle" else state
        icon.icon = tray_image(state)
        icon.title = TOOLTIPS[state].format(key=keys["dictate"])

    for mode, key in keys.items():
        keyboard.on_press_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and press(m, cfg, ov, mark))
        keyboard.on_release_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and release(m))
    keyboard.add_hotkey(hk["fix_last"], lambda: ov.post(lambda: fix_newest(ov.root)))
    keyboard.add_hotkey(learn_key, lambda: learn_word(cfg, ov), trigger_on_release=True)

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
