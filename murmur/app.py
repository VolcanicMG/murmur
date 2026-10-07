"""Tray app wiring: card overlay, hotkeys, tray icon and menu."""
import logging, os, sys, threading, time
from types import SimpleNamespace
import keyboard, pystray
from . import brain, capture, card, context, cues, settings, store, win
from .session import Session
from . import tray
from .tray import TOOLTIPS, tray_image

log = logging.getLogger("murmur")
READY_HOLD_MS = 3000  # the loading card stays at 100 % this long so the start is seen, not a flash
S = {"mode": None, "held": False, "paused": False, "sid": 0, "last_paste": (0.0, "", ""), "capturing": False, "ready": False, "session": None, "g": ("idle", 0.0), "dropped": False, "last_fg": "", "keys": {}, "pending_rebind": False}  # shared hotkey/session state
_bound = {"ov": None, "mark": None, "removers": []}  # hotkey registration bookkeeping (see rebind_hotkeys)
_NOT_FG = {"", "python.exe", "pythonw.exe", "murmur.exe", "explorer.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe"}
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
        if S["pending_rebind"]:
            threading.Thread(target=_apply_pending_rebind, daemon=True).start()

    def pasted(app, text=""):
        """Record a paste of `text` into `app`; None clears it."""
        S["last_paste"] = (time.time(), app, text) if app else (0.0, "", "")

    def last_paste():
        return S["last_paste"]
    return SimpleNamespace(held=held, stop=stop, current=current, done=done, mark=mark, pasted=pasted, last_paste=last_paste, retry=retry, live=lambda: bool(S["session"]))


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


def _note_foreground():
    """Remember the last foreground app that is neither murmur nor the shell (what 'Don't use murmur in <app>' targets)."""
    exe = win.active_app()[0]
    if exe not in _NOT_FG:
        S["last_fg"] = exe
    return exe


def last_foreground():
    """Process name of the app that was in front before the tray was clicked ('' if unknown)."""
    return S["last_fg"]


def _ignored(cfg):
    """Hotkeys do nothing in an excluded app or while an exclusive-fullscreen game is in front (no card, no cue)."""
    app = _note_foreground()
    return capture.should_ignore(app, cfg, win.quns_state() if store.pause_fullscreen(cfg) else 0)


def _cfg(cfg=None):
    return cfg if cfg is not None else _bound.get("cfg") or {}


def is_private(cfg=None):
    """Private mode on? (tray.extra_items calls this without arguments.)"""
    return store.is_private(_cfg(cfg))


def _watch_config():
    """Another murmur process (the Explorer verb `murmur --exclude <file>`) may edit [apps] exclude in config.toml: pick the
    change up on the 3 s poll, apply it to the live cfg and tell the user on the card."""
    cfg, p = _bound.get("cfg"), store.HOME / "config.toml"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return
    if mtime == _bound.get("cfg_mtime"):
        return
    _bound["cfg_mtime"] = mtime
    if cfg is None or not _bound.get("ov"):
        return
    try:
        new = store.apps_exclude(store.load_config())
    except (OSError, ValueError):
        return
    old = store.apps_exclude(cfg)
    if new == old:
        return  # our own save (Settings) or an unrelated edit
    cfg.setdefault("apps", {})["exclude"] = new
    added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    text = (f"murmur is off in {', '.join(added)}." if added else "") + (f" murmur is back on in {', '.join(removed)}." if removed else "")
    _bound["ov"].post(lambda: _bound["ov"].flash("done" if added else "undone", text.strip(), label="Excluded" if added else "Included"))


def toggle_private(cfg=None):
    """Flip private mode (no history, no learning) and persist it. Returns the new state."""
    cfg = _cfg(cfg)
    cfg.setdefault("privacy", {})["private"] = not store.is_private(cfg)
    _save(cfg)
    if _bound.get("mark"):
        _bound["mark"]("idle")  # refresh the tooltip's private suffix
    return store.is_private(cfg)


def _save(cfg):
    try:
        store.save_config(cfg)
    except (ValueError, TypeError, OSError):
        log.exception("saving config failed")


def press(mode, cfg, ov, mark):
    log.info("hotkey %s press (mode=%s ready=%s capturing=%s)", mode, S["mode"], S["ready"], S["capturing"])
    if not S["session"] and _ignored(cfg):  # a live session may still be stopped by its own key
        return
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
    if got.get("password"):
        return ""  # never read from a password field
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
    if S["capturing"] or _ignored(cfg):
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
    icon.title = tray.tooltip(state, key, private=store.is_private(_bound.get("cfg") or {}))


def _stop_builtin():
    """Deterministic shutdown of the bundled llama-server child (module may not exist in older builds)."""
    try:
        from . import localllm
        localllm.stop()
    except ImportError:
        pass
    except Exception:
        log.exception("stopping the bundled model failed")


def quit_app(icon, ov):
    """Stop any running session without pasting, hide the card, then exit."""
    if S["session"]:
        S["session"].cancel()
    S["held"] = False
    ov.post(ov.hide)
    _stop_builtin()
    icon.stop()
    ov.post(ov.root.quit)


def _register(cfg, ov, mark):
    """Register every hotkey hook for cfg; returns (keys, learn_key, removers). `removers` are keyboard's hook handles."""
    hk = cfg["hotkeys"]
    keys = {m: keyboard.normalize_name(hk[m]) for m in ("dictate", "command")}
    learn_key = hk.get("learn_word", "ctrl+alt+d")
    handles = []
    for mode, key in keys.items():
        handles.append(keyboard.on_press_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and press(m, cfg, ov, mark)))
        handles.append(keyboard.on_release_key(key, lambda e, m=mode, k=key: store.key_matches(e.name, k) and release(m, cfg)))
    handles.append(keyboard.hook(lambda e: other_key(e, keys["dictate"], cfg)))
    handles.append(keyboard.add_hotkey(hk["fix_last"], lambda: None if _ignored(cfg) else (
        log.info("hotkey fix_last fired"), ov.post(lambda: fix_newest(ov.root)))))
    handles.append(keyboard.add_hotkey(learn_key, lambda: learn_word(cfg, ov)))  # fires on press; trigger_on_release never fired in the keyboard lib
    return keys, learn_key, handles


def _unregister(handles):
    for h in handles:
        try:
            keyboard.unhook(h)
        except Exception:
            log.exception("unhook failed")


def rebind_hotkeys(cfg):
    """Apply a changed [hotkeys] table without a restart: unhook every murmur hook and register the new ones. Idempotent.
    Safe on the Tk thread; while a recording is live (or a key gesture is in flight) it is deferred until the session ends.
    Settings should call `app.rebind_hotkeys(cfg)` after saving the form (cfg = the live config dict)."""
    if not _bound["ov"]:
        return False  # tray not running (standalone settings window): a restart is needed
    if S["session"] or S["mode"] or S["g"][0] != "idle":
        S["pending_rebind"] = True
        _bound["cfg"] = cfg
        return False
    S["pending_rebind"] = False
    _unregister(_bound["removers"])
    S["keys"], _bound["learn_key"], _bound["removers"] = _register(cfg, _bound["ov"], _bound["mark"])
    log.info("hotkeys re-bound: dictate=%s command=%s", S["keys"]["dictate"], S["keys"]["command"])
    return True


def _apply_pending_rebind():
    if S["pending_rebind"] and not S["session"]:
        time.sleep(0.2)  # let the session finish tearing down
        rebind_hotkeys(_bound.get("cfg"))


def _purge_loop(cfg):
    """Drop history older than [privacy] history_days now, then again every 24 h (a chained daemon Timer)."""
    try:
        n = store.purge_history(cfg.get("privacy", {}).get("history_days", 0))
        if n:
            log.info("history: purged %d old rows", n)
    except Exception:
        log.exception("history purge failed")
    t = threading.Timer(86400, _purge_loop, args=(cfg,)); t.daemon = True; t.start()


def _track_foreground():
    """Cheap 3 s poll so last_foreground() is right even when no hotkey was pressed (a chained daemon Timer)."""
    try:
        _note_foreground()
        _watch_config()
    except Exception:
        pass
    t = threading.Timer(3.0, _track_foreground); t.daemon = True; t.start()


def tray_app(cfg, learn, boot, first_run=False):
    """boot(cfg, progress) loads the speech model and warms the LLM; it runs on a thread while the loading card shows its steps."""
    ov = card.Card()

    def mark(state):
        _set_icon(icon, state, S["keys"]["dictate"])
    S["keys"], learn_key, removers = _register(cfg, ov, mark)
    _bound.update(ov=ov, mark=mark, removers=removers, learn_key=learn_key, cfg=cfg)
    if os.name == "nt":
        try:
            from . import shellmenu
            shellmenu.sync(cfg)  # keep the Explorer verb's command pointing at this exe (or remove it when turned off)
        except Exception:
            log.exception("shell menu sync failed")
    _track_foreground()
    keys = S["keys"]

    def toggle(i, it):
        S["paused"] = not S["paused"]
        mark("idle")

    def open_settings(i=None, it=None, page=None):
        ov.post(lambda: settings.open_settings(ov.root, cfg, fix_last, **({"page": page} if page else {})))
    menu = pystray.Menu(
        pystray.MenuItem("Settings\u2026", lambda i, it: open_settings(), default=True),
        pystray.MenuItem(lambda it: "Resume dictation" if S["paused"] else "Pause dictation", toggle, checked=lambda it: S["paused"]),
        *tray.extra_items(sys.modules[__name__], lambda i, it: open_settings(page="About")),  # Private mode, About
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open config folder", lambda i, it: os.startfile(store.HOME)),
        pystray.MenuItem("Learn style", lambda i, it: threading.Thread(target=learn, args=(cfg,), daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit murmur", lambda i, it: quit_app(i, ov)))
    icon = pystray.Icon("murmur", tray_image("idle"), tray.tooltip("idle", keys["dictate"], private=store.is_private(cfg)), menu)
    icon.run_detached()
    ov.post(lambda: ov.flash("loading"))
    if first_run:  # a brand-new ~/.murmur: walk through setup once
        try:
            from . import setup
            ov.post(lambda: setup.open_setup(ov.root))
        except ImportError:
            pass

    def start():
        try:
            boot(cfg, lambda frac, text: ov.post(lambda: ov.progress(frac, text)))
        except Exception:
            log.exception("startup failed")
            ov.post(lambda: ov.flash("failed"))
            return
        _purge_loop(cfg)
        S["ready"] = True
        ov.post(lambda: ov.progress(1.0, f"Ready. Hold {keys['dictate']} to dictate."))
        ov.post(lambda: ov.root.after(READY_HOLD_MS, lambda: ov.st == "loading" and ov.hide()))  # a session that started meanwhile owns the card
        log.info("ready: hold %s to dictate, %s for commands, %s adds the selected word", keys["dictate"], keys["command"], learn_key)
        if cfg.get("ui", {}).get("startup_toast", True):
            try:
                icon.notify(f"murmur is running \u2014 hold {keys['dictate']} to dictate", "murmur")
            except Exception:
                log.exception("startup toast failed")
    threading.Thread(target=start, daemon=True).start()
    ov.run()
    os._exit(0)  # PortAudio/onnxruntime teardown can hang
