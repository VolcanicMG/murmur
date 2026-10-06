"""Tray app wiring: overlay, hotkeys, tray menu."""
import logging, os, threading
from types import SimpleNamespace
import keyboard, pystray
from PIL import Image, ImageDraw
from . import brain, settings, store, win
from .session import Session

log = logging.getLogger("murmur")
S = {"mode": None, "held": False, "paused": False, "sid": 0}  # shared hotkey/session state


def icon_img(color):
    im = Image.new("RGBA", (64, 64)); ImageDraw.Draw(im).ellipse((8, 8, 56, 56), fill=color); return im


def _hooks(sid, mark):
    def held():
        return S["held"]

    def current():
        return S["sid"] == sid

    def done():
        S["mode"] = None
    return SimpleNamespace(held=held, current=current, done=done, mark=mark)


def press(mode, cfg, ov, mark):
    if S["mode"] or S["paused"]:
        return
    S.update(mode=mode, held=True, sid=S["sid"] + 1)
    if mode == "command":
        keyboard.send(-0xE8)  # mask alt so a lone alt tap doesn't open the app menu
    sid = S["sid"]
    sess = Session(mode, cfg, ov, _hooks(sid, mark))
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
    hist = store.read_history(1)
    if hist:
        fix_last(root, hist[-1])


def tray_app(cfg, learn):
    ov = win.Overlay()
    hk = cfg["hotkeys"]

    def mark(color):
        icon.icon = icon_img(color)

    keys = {m: keyboard.normalize_name(hk[m]) for m in ("dictate", "command")}
    for mode, key in keys.items():
        keyboard.on_press_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and press(m, cfg, ov, mark))
        keyboard.on_release_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and release(m))
    keyboard.add_hotkey(hk["fix_last"], lambda: ov.post(lambda: fix_newest(ov.root)))

    def toggle(i, it):
        S["paused"] = not S["paused"]
    menu = pystray.Menu(
        pystray.MenuItem("Settings\u2026", lambda i, it: ov.post(lambda: settings.open_settings(ov.root, cfg, fix_last))),
        pystray.MenuItem("Open config folder", lambda i, it: os.startfile(store.HOME)),
        pystray.MenuItem("Learn style", lambda i, it: threading.Thread(target=learn, args=(cfg,), daemon=True).start()),
        pystray.MenuItem(lambda it: "Resume" if S["paused"] else "Pause", toggle),
        pystray.MenuItem("Quit", lambda i, it: (i.stop(), ov.post(ov.root.quit))))
    icon = pystray.Icon("murmur", icon_img("grey"), "murmur", menu)
    icon.run_detached()
    log.info("ready: hold %s to dictate, %s for commands", keys["dictate"], keys["command"])
    ov.run()
    os._exit(0)  # PortAudio/onnxruntime teardown can hang
