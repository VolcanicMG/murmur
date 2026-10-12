"""First-run setup: a 4-step window (Welcome, Microphone, Cleanup model, Hotkeys) in the settings design language.
`run_standalone()` for `--setup` / first launch; `open_setup(root, on_close)` inside a running Tk. Finish writes the config; Skip changes nothing.
Pure helpers (lm_status) have no tk/win32 imports and are unit-tested."""
import subprocess, threading, webbrowser
from types import SimpleNamespace
from . import store

try:
    import tkinter as tk
except ImportError:  # Linux test runs
    tk = None

LMSTUDIO_URL = "https://lmstudio.ai"
BUILTIN_MODEL = True  # bundled llama.cpp provider "builtin" exists: step 3 offers it first (False hides it)
STEPS = (("Welcome", "murmur types what you say, into any app."),
         ("Microphone", "Pick the input murmur listens to. The bar moves when it hears you."),
         ("Cleanup model", "A small local model removes filler words and fixes punctuation."),
         ("Hotkeys", "Hold a key to talk, let go to paste."))


def lm_status(cfg, find=None, run=subprocess.run):
    """("ready"|"nomodel"|"missing"|"error", detail): is LM Studio's CLI there and does `lms ls` list the configured model?"""
    llm = cfg.get("llm", {})
    model = llm.get("model", "")
    try:
        lms = (find or getattr(store, "find_lms", lambda: None))() or (llm.get("lms") if llm.get("lms") and __import__("os").path.isfile(llm["lms"]) else None)
        if not lms:
            return "missing", model
        out = run([lms, "ls"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return "error", str(e)
    return ("ready" if model and model in out else "nomodel"), model


def _lm_text(state, detail):
    return {"ready": ("LM Studio found, model ready", f"{detail} is downloaded. murmur loads it when you start dictating.", "success"),
            "nomodel": ("LM Studio found, model missing", f"Run:  lms get {detail}", "error"),
            "missing": ("LM Studio not found", "Install it, then run this setup again from the tray (About > Run setup again) or `murmur --setup`.", "error"),
            "error": ("Couldn't check LM Studio", detail, "error")}[state]


def _page_welcome(body, ui, ctx):
    from .settings import _label
    from .theme import SECONDARY, TEXT
    for text in ("Hold your dictate key, speak, release: the cleaned-up text is pasted where your cursor is.",
                 "Everything runs on this computer: speech recognition is local and the cleanup model is one you run yourself. "
                 "Nothing is sent anywhere unless you configure a remote provider.",
                 "This takes about a minute and you can skip it: every choice lives in Settings."):
        lab = _label(body, ui, text, "body", TEXT); lab.config(wraplength=ui.k(480), justify="left")
        lab.pack(anchor="w", pady=(0, ui.k(12)))
    return SimpleNamespace(enter=lambda: None, leave=lambda: None, values=lambda: {})


def _meter(parent, ui):
    """Canvas level bar: rounded track + accent fill; returns (canvas, set_level(0..1))."""
    from . import render
    from .theme import ACCENT, FIELD, HAIR, INPUT
    from .widgets import put
    w, h = ui.k(360), ui.k(14)
    c = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=0, bd=0)

    def set_level(v):
        c.delete("all"); c._imgs = []
        img = render.pill((w, h), INPUT, HAIR).copy()
        fw = max(h, round(w * min(1.0, v)))
        img.alpha_composite(render.pill((fw, h), ACCENT), (0, 0))
        put(c, img, 0, 0)
    set_level(0)
    return c, set_level


class _Listener:
    """Live input level from a sounddevice stream (audio thread writes `level`; the Tk side polls it)."""

    def __init__(self, status):
        self.status, self.stream, self.level, self.shown, self.on = status, None, 0.0, 0.0, False

    def stop(self):
        self.on = False
        stream, self.stream = self.stream, None
        if stream:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def _cb(self, data, *_):
        import numpy as np
        self.level = float(np.sqrt(np.mean(np.square(data))))

    def start(self, device_name):
        """Open the named input (default when ""); False + a status message when it can't be opened."""
        self.stop()
        try:
            import sounddevice as sd
            idx = store.resolve_device(device_name, sd.query_devices())
            self.stream = sd.InputStream(device=idx, channels=1, samplerate=16000, callback=self._cb)
            self.stream.start()
        except Exception as e:
            self.status.set(f"Can't open the input: {e}", "error")
            return False
        self.on = True
        return True


def _page_mic(body, ui, ctx):
    from .card import norm_level
    from .devices import MicField
    from .settings import _label
    mic = MicField(body, ui, ctx.status)
    mic.pack(anchor="w")
    _label(body, ui, "Level", "small", ctx.secondary).pack(anchor="w", pady=(ui.k(18), ui.k(6)))
    meter, set_level = _meter(body, ui)
    meter.pack(anchor="w")
    lis = _Listener(ctx.status)

    def tick():
        if not lis.on or not meter.winfo_exists():
            return
        lis.shown += (norm_level(lis.level) - lis.shown) * 0.4
        set_level(lis.shown)
        meter.after(50, tick)

    def start():
        if lis.start(mic.get()):
            tick()
    mic.sel.var.trace_add("write", lambda *_: start() if lis.on else None)
    stop = lis.stop
    return SimpleNamespace(enter=start, leave=stop, values=lambda: {"audio.device": mic.get()}, load=lambda cfg: mic.set(cfg.get("audio", {}).get("device", "")))


def _page_model(body, ui, ctx):
    from . import builtin
    from .inputs import Select
    from .settings import _label
    from .theme import ERROR, SECONDARY, SUCCESS, TEXT
    from .widgets import Pill
    opts = ["builtin", "lmstudio"] if BUILTIN_MODEL else ["lmstudio"]
    choice = Select(body, ui, opts).labelled({"builtin": "Built-in model (recommended)", "lmstudio": "Use LM Studio instead"})
    choice.pack(anchor="w", pady=(0, ui.k(16)))
    bi = tk.Frame(body, bg=body.cget("bg"))
    lead = _label(bi, ui, "", "section", ERROR); lead.config(wraplength=ui.k(480), justify="left")  # shown only while no model exists
    panel = None

    def follow(have):
        lead.config(text="" if have else "You need a cleanup model. Without one murmur pastes your words exactly as heard.")
        lead.pack_forget() if have else lead.pack(anchor="w", **({"before": panel} if panel is not None and panel.winfo_manager() else {}))
    panel = builtin.build(bi, ui, store.merged_config, follow)
    panel.pack(anchor="w", pady=(ui.k(8), 0))
    lm = tk.Frame(body, bg=body.cget("bg"))
    head = _label(lm, ui, "Checking LM Studio\u2026", "section", TEXT)
    head.pack(anchor="w")
    detail = _label(lm, ui, "", "body", SECONDARY); detail.config(wraplength=ui.k(480), justify="left")
    detail.pack(anchor="w", pady=(ui.k(6), ui.k(14)))
    Pill(lm, ui, "Get LM Studio (lmstudio.ai)", lambda: webbrowser.open(LMSTUDIO_URL)).pack(anchor="w")
    box = []

    def show(*_):
        (bi if choice.get() == "builtin" else lm).pack(anchor="w")
        (lm if choice.get() == "builtin" else bi).pack_forget()
    choice.var.trace_add("write", show)
    choice.set(opts[0])
    show()

    def enter():
        panel.refresh()
        threading.Thread(target=lambda: box.append(lm_status(store.merged_config())), daemon=True).start()
        poll()

    def poll():
        if not head.winfo_exists():
            return
        if not box:
            head.after(150, poll); return
        title, text, kind = _lm_text(*box.pop())
        head.config(text=title, fg={"success": SUCCESS, "error": ERROR}[kind]); detail.config(text=text)

    def values():
        if choice.get() == "builtin":
            return {"llm.provider": "builtin", "builtin.enabled": True}
        return {"llm.provider": "openai"}
    return SimpleNamespace(enter=enter, leave=lambda: None, values=values)


def _page_keys(body, ui, ctx):
    from .devices import HotkeyField
    from .settings import _label
    from .theme import SECONDARY
    g = tk.Frame(body, bg=body.cget("bg")); g.pack(anchor="w")
    fields = {}
    for i, (key, label) in enumerate((("dictate", "Dictate (hold)"), ("command", "Command (hold)"))):
        _label(g, ui, label, "body", ctx.text, body.cget("bg")).grid(row=i, column=0, sticky="w", padx=(0, ui.k(16)), pady=ui.k(6))
        fields[f"hotkeys.{key}"] = HotkeyField(g, ui, ctx.status)
        fields[f"hotkeys.{key}"].grid(row=i, column=1, sticky="w", pady=ui.k(6))
    note = _label(body, ui, "Hands-free: tap the dictate key quickly instead of holding it and murmur keeps recording until you tap again "
                  "or go quiet. Command mode edits your selection from a spoken instruction.", "small", SECONDARY)
    note.config(wraplength=ui.k(480), justify="left"); note.pack(anchor="w", pady=(ui.k(18), 0))
    return SimpleNamespace(enter=lambda: None, leave=lambda: None, values=lambda: {p: f.get() for p, f in fields.items()},
                           load=lambda cfg: [f.set(cfg.get("hotkeys", {}).get(p.split(".")[1], "")) for p, f in fields.items()])


def build(win, ui, on_close):
    """Fill `win` (a Tk or Toplevel) with the stepper; returns a namespace with goto(i)."""
    from . import render, settings
    from .theme import ACCENT, FILL, NAME, SECONDARY, SIDEBAR, TEXT
    from .widgets import Pill, pill, put
    k = ui.k
    side = tk.Frame(win, bg=SIDEBAR, width=k(200)); side.pack(side="left", fill="y"); side.pack_propagate(False)
    main = tk.Frame(win, bg=FILL); main.pack(side="left", fill="both", expand=True)
    tk.Label(side, text="murmur", bg=SIDEBAR, fg=ACCENT, font=ui.fonts["title"], anchor="w").pack(anchor="w", padx=k(20), pady=(k(20), k(18)))
    status = settings.Status(main, ui); status.pack(side="bottom", fill="x", padx=k(24), pady=(0, k(10)))
    bar = tk.Frame(main, bg=FILL); bar.pack(side="bottom", fill="x", padx=k(24), pady=(k(10), k(6)))
    title = tk.Label(main, bg=FILL, fg=TEXT, font=ui.fonts["title"], anchor="w"); title.pack(anchor="w", padx=k(24), pady=(k(24), 0))
    desc = tk.Label(main, bg=FILL, fg=SECONDARY, font=ui.fonts["body"], anchor="w"); desc.pack(anchor="w", padx=k(24), pady=(k(4), k(18)))
    body = tk.Frame(main, bg=FILL); body.pack(fill="both", expand=True, padx=k(24))
    ctx = SimpleNamespace(status=status, secondary=SECONDARY, text=NAME)
    frames, pages = [], []
    for builder in (_page_welcome, _page_mic, _page_model, _page_keys):
        fr = tk.Frame(body, bg=FILL); frames.append(fr); pages.append(builder(fr, ui, ctx))
    cfg = store.merged_config()
    for p in pages:
        getattr(p, "load", lambda c: None)(cfg)
    dots = []
    for i, (name, _) in enumerate(STEPS):
        row = tk.Frame(side, bg=SIDEBAR); row.pack(fill="x", padx=k(20), pady=k(6))
        dot = tk.Canvas(row, width=k(10), height=k(10), bg=SIDEBAR, highlightthickness=0, bd=0); dot.pack(side="left", padx=(0, k(10)))
        lab = tk.Label(row, text=name, bg=SIDEBAR, font=ui.fonts["chip"], anchor="w"); lab.pack(side="left")
        dots.append((dot, lab))
    cur = [0]
    nav = SimpleNamespace(back=None, nxt=None)

    def paint():
        for i, (dot, lab) in enumerate(dots):
            dot.delete("all"); dot._imgs = []
            put(dot, render.circle(k(10), ACCENT if i <= cur[0] else "#3a3d52"), 0, 0)
            lab.config(fg=ACCENT if i == cur[0] else SECONDARY)

    def goto(i):
        pages[cur[0]].leave(); frames[cur[0]].pack_forget()
        cur[0] = i
        title.config(text=STEPS[i][0]); desc.config(text=STEPS[i][1])
        frames[i].pack(fill="both", expand=True)
        paint(); status.set("")
        nav.back.pack_forget() if i == 0 else nav.back.pack(side="right", padx=(0, k(8)))
        nav.nxt.text = "Finish" if i == len(STEPS) - 1 else "Next"; nav.nxt.draw()
        pages[i].enter()

    def finish():
        values = {}
        for p in pages:
            values.update(p.values())
        try:
            c = store.load_config()
            for path, v in values.items():
                sec, key = path.split(".")
                c.setdefault(sec, {})[key] = v
            store.save_config(c)
        except (ValueError, TypeError, OSError) as e:
            status.set(f"Not saved: {e}", "error"); return
        close()

    def close():
        pages[cur[0]].leave()
        on_close()

    def nxt():
        finish() if cur[0] == len(STEPS) - 1 else goto(cur[0] + 1)
    Pill(bar, ui, "Skip setup", close).pack(side="left")
    nav.nxt = pill(bar, ui, "Next", nxt, "primary"); nav.nxt.pack(side="right")
    nav.back = Pill(bar, ui, "Back", lambda: goto(cur[0] - 1))
    win.bind("<Destroy>", lambda e: pages[cur[0]].leave() if e.widget is win else None, add="+")
    goto(0)
    return SimpleNamespace(goto=goto)


def _make_window(root, ui):
    top = tk.Toplevel(root); top.title("murmur setup"); top.configure(bg="#161826")
    top.geometry(f"{round(780 * ui.s)}x{round(520 * ui.s)}")
    from . import win
    win.style_window(top)
    return top


def open_setup(root, on_close=lambda: None):
    """Open the setup window inside a running Tk (root). on_close runs after Finish or Skip, once the window is gone."""
    from . import settings
    ui = settings.make_ui()
    top = _make_window(root, ui)

    def close():
        top.destroy()
        on_close()
    top.protocol("WM_DELETE_WINDOW", close)  # the X counts as Skip
    top.goto = build(top, ui, close).goto
    return top


def run_standalone():
    """`murmur --setup`: own Tk root; returns when the window closes."""
    from . import win
    win.dpi_aware()
    root = tk.Tk(); root.withdraw()
    open_setup(root, root.quit)
    root.mainloop()
