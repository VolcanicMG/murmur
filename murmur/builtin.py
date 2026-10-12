"""Bundled-LLM status, the model download dialog and the inline panel shared by Settings > Config > Cleanup model and the setup wizard.
The helpers (status_line, fmt_progress, facts, needs_model, warm_up) are pure and unit-tested; the widgets import tk on use and run on
the Tk main thread only (the download itself runs on a worker thread that the dialog polls with after())."""
import collections, logging, shutil, threading, time
from pathlib import Path
from types import SimpleNamespace
from . import localllm, store

log = logging.getLogger("murmur")
NO_MODEL = "No cleanup model yet: dictations are pasted exactly as heard"
_dialog = None  # the one open download dialog (single instance)


def status_line(cfg, ll=localllm):
    """(text, kind "success"|"info"|"error", have_model) for the bundled runtime + model."""
    runtime = (ll.runtime_dir(cfg) / ll.EXE).is_file()
    model = ll.find_model(cfg)
    if model and runtime:
        return f"Runtime ready · model: {model.name}", "success", True
    if model:
        return f"Runtime not downloaded (≈90 MB, fetched on first use) · model: {model.name}", "info", True
    return NO_MODEL, "error", False


def needs_model(cfg, find_lms=store.find_lms, ll=localllm):
    """True when cleanup has nothing to run on: the primary provider is the enabled bundled model, no GGUF is found, and there is no
    usable fallback (fallback on AND either a remote provider or an LM Studio install)."""
    llm = cfg.get("llm", {})
    if llm.get("provider") != "builtin" or not ll.enabled(cfg) or ll.find_model(cfg):
        return False
    if llm.get("fallback") and (not store.provider_is_local(cfg.get("llm_fallback", {})) or find_lms()):
        return False
    return True


def should_prompt(cfg, state, wizard_ran, find_lms=store.find_lms, ll=localllm):
    """Open the download dialog by itself at launch? Not when the user chose "Don't ask again" (state.json model_prompt_off), when the
    setup wizard ran this launch (it has its own model step), or when needs_model is False."""
    return not state.get("model_prompt_off") and not wizard_ran and needs_model(cfg, find_lms, ll)


def fmt_size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _fmt_eta(sec):
    if sec < 45:
        return "less than a minute left"
    m = max(1, round(sec / 60))
    return f"about {m} min left" if m < 60 else f"about {m // 60} h {m % 60} min left"


def fmt_progress(done, total, rate=None):
    """One line for the dialog: "42% · 2.6 of 6.2 GB · 18 MB/s · about 3 min left". rate in bytes/s (None while unknown)."""
    total = max(total, 1)
    if done >= total:
        return "Checking the download…"
    unit, div = ("GB", 1e9) if total >= 1e9 else ("MB", 1e6)
    parts = [f"{100 * done // total}%", f"{done / div:.1f} of {total / div:.1f} {unit}"]
    if rate and rate > 0:
        parts.append(f"{rate / 1e6:.0f} MB/s" if rate >= 1e7 else f"{rate / 1e6:.1f} MB/s" if rate >= 1e6 else f"{rate / 1e3:.0f} KB/s")
        parts.append(_fmt_eta((total - done) / rate))
    return " · ".join(parts)


def facts(ll=localllm, disk_usage=shutil.disk_usage):
    """(label, value) rows for the dialog's intro panel; the models folder is the real path, free space is that drive's."""
    d = Path(ll.models_dir())
    probe = d
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = disk_usage(probe).free
        space = f"{free / 1e9:.0f} GB on {d.drive or d.anchor}" if free >= 1e11 else f"{free / 1e9:.1f} GB on {d.drive or d.anchor}"
    except OSError:
        space = "unknown"
    return [("Model", "Gemma 4 E4B (Q6_K)"), ("Size", fmt_size(ll.MODEL_SIZE)), ("Saved to", str(d)),
            ("From", "huggingface.co, lmstudio-community"), ("License", "Apache-2.0"), ("Free space", space)]


def resume_note(ll=localllm):
    """"Resumes from 42%" when a partial download exists, else ""."""
    have = ll.part_bytes()
    return f"Resumes from {100 * have // ll.MODEL_SIZE}%" if 0 < have < ll.MODEL_SIZE else ""


def warm_up(cfg, ll=localllm):
    """Start the bundled server and send ONE throwaway chat so a first-install shader compile (~12 s on Vulkan) isn't paid by the first
    dictation. Runs on a thread named localllm.WARMUP_THREAD (llm.py lets only that thread talk to a starting model); failures are
    logged. Clears any start back-off first, so it also works right after a download that followed a failed no-model start."""
    store.runtime["llm_starting"] = True
    try:
        from . import brain
        ll._failures, ll._retry_at = 0, 0.0
        t = time.time(); ll.start(cfg); log.info("bundled model up in %.1fs", time.time() - t)
        t = time.time(); brain._chat(cfg, "Reply with one word.", "ping"); log.info("bundled model warm-up %.1fs", time.time() - t)
    except Exception as e:
        log.info("bundled model start failed: %s", e)
    finally:
        store.runtime["llm_starting"] = False


class _Rate:
    """Bytes/s over the last few seconds of (time, done) samples."""

    def __init__(self, window=6.0):
        self.window, self.s = window, collections.deque()

    def add(self, done, now=None):
        now = time.monotonic() if now is None else now
        self.s.append((now, done))
        while len(self.s) > 2 and now - self.s[0][0] > self.window:
            self.s.popleft()
        (t0, d0), (t1, d1) = self.s[0], self.s[-1]
        return (d1 - d0) / (t1 - t0) if t1 - t0 >= 1 and d1 > d0 else None


def open_download(parent, ui, cfg_fn, on_done=None, auto=False):
    """The model download dialog (intro -> downloading -> done | error), single instance. parent: a mapped window to centre over and
    stay on top of (transient + grab), or a hidden root / None to centre on the screen. on_done() runs after a finished download.
    auto=True (opened by the launch prompt) adds a quiet "Don't ask again" that persists model_prompt_off in state.json.
    Returns the Toplevel; its show_intro / show_progress / show_done / show_error(msg) state functions are what the UI itself calls."""
    global _dialog
    import tkinter as tk
    from . import render, win
    from .settings import Panel, _label, icon_path
    from .theme import ACCENT, ERROR, FILL, HAIR, INPUT, NAME, SECONDARY, SUCCESS, TEXT
    from .widgets import pill, put
    if _dialog is not None:
        try:
            if _dialog.winfo_exists():
                _dialog.lift(); _dialog.focus_force()
                return _dialog
        except tk.TclError:
            pass
    k = ui.k
    mapped = parent is not None and parent.winfo_viewable()
    top = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    _dialog = top
    top.withdraw()  # shown once sized and placed
    top.title("murmur"); top.configure(bg=FILL); top.resizable(False, False)
    try:
        top.iconbitmap(str(icon_path()))  # the launch prompt opens before any settings window set the default icon
    except tk.TclError:
        pass
    st = SimpleNamespace(cancel=threading.Event(), thread=None, result=None, progress=(0, localllm.MODEL_SIZE), rate=_Rate(), speed=None)
    bar = tk.Frame(top, bg=FILL); bar.pack(side="bottom", fill="x", padx=k(24), pady=(k(8), k(20)))
    body = tk.Frame(top, bg=FILL); body.pack(side="top", fill="both", expand=True, padx=k(24), pady=(k(22), 0))
    wrap = k(472)

    def reset(title):
        for f in (bar, body):
            for w in f.winfo_children():
                w.destroy()
        _label(body, ui, title, "title", TEXT).pack(anchor="w")

    def para(text, fg=SECONDARY, role="body", pady=(k(10), 0)):
        lab = _label(body, ui, text, role, fg); lab.config(wraplength=wrap, justify="left")
        lab.pack(anchor="w", pady=pady)
        return lab

    def buttons(primary=None, secondary=None):
        if primary:
            pill(bar, ui, primary[0], primary[1], "primary").pack(side="right")
        if secondary:
            pill(bar, ui, secondary[0], secondary[1]).pack(side="right", padx=(0, k(8)) if primary else 0)

    def fit():
        """Hug the current state's content: the window was sized for the intro, the tallest state."""
        if top.winfo_viewable():
            top.update_idletasks()
            top.geometry(f"{top.winfo_width()}x{top.winfo_reqheight()}")

    def close():
        global _dialog
        _dialog = None
        try:
            top.grab_release()
        except tk.TclError:
            pass
        top.destroy()

    def show_intro():
        reset("Download the cleanup model")
        para("murmur tidies what you say with a language model that runs on this PC. None is installed yet, so dictations are pasted "
             "exactly as heard.", TEXT)
        panel = Panel(body, ui, "About the download"); panel.pack(fill="x", pady=(k(16), 0))
        for i, (name, value) in enumerate(facts()):
            rb = panel.rows.cget("bg")
            _label(panel.rows, ui, name, "small", SECONDARY, rb).grid(row=i, column=0, sticky="nw", padx=(0, k(16)), pady=k(2))
            v = _label(panel.rows, ui, value, "small", NAME, rb); v.config(wraplength=k(330), justify="left")
            v.grid(row=i, column=1, sticky="w", pady=k(2))
        panel.update_idletasks()
        panel.config(height=panel.rows.master.winfo_reqheight() + 2 * panel.pad)  # Panel sizes itself on <Configure>, which a withdrawn window never sends
        note = resume_note()
        if note:
            para(note, ACCENT, "small", (k(10), 0))
        buttons(("Download", start), ("Not now", close))
        if auto:
            q = tk.Label(bar, text="Don't ask again", bg=FILL, fg=SECONDARY, font=ui.fonts["small"], cursor="hand2")
            q.pack(side="left"); q.bind("<Button-1>", lambda e: (store.update_state(lambda s: s.__setitem__("model_prompt_off", True)), close()))
            q.bind("<Enter>", lambda e: q.config(fg=TEXT)); q.bind("<Leave>", lambda e: q.config(fg=SECONDARY))

    def show_progress(done=0, total=localllm.MODEL_SIZE, rate=None):
        if not getattr(top, "_bar", None) or not top._bar.winfo_exists():
            reset("Downloading the cleanup model")
            para("You can keep using your PC. Cancel keeps what is already downloaded, and the download resumes from there next time.")
            top._bar = tk.Canvas(body, width=wrap, height=k(10), bg=FILL, highlightthickness=0, bd=0); top._bar.pack(anchor="w", pady=(k(22), k(10)))
            top._line = _label(body, ui, "", "small", NAME); top._line.pack(anchor="w")
            buttons(None, ("Cancel", cancel))
            fit()
        c, w, h = top._bar, wrap, k(10)
        c.delete("all"); c._imgs = []
        img = render.pill((w, h), INPUT, HAIR).copy()
        frac = min(1.0, done / max(total, 1))
        if frac > 0:
            img.alpha_composite(render.pill((max(h, round(w * frac)), h), ACCENT), (0, 0))
        put(c, img, 0, 0)
        top._line.config(text=fmt_progress(done, total, rate))

    def show_done():
        reset("Cleanup model ready")
        para("Model ready. murmur now cleans up your dictations.", SUCCESS, "section")
        buttons(("Done", close))
        fit()

    def show_error(msg):
        reset("Couldn't download the model")
        para(msg, ERROR, "section")
        if localllm.part_bytes():
            para("What was already downloaded is kept; Try again picks up from there.")
        buttons(("Try again", start), ("Close", close))
        fit()

    def start():
        st.cancel.clear(); st.result, st.rate, st.progress = None, _Rate(), (localllm.part_bytes(), localllm.MODEL_SIZE)
        show_progress(*st.progress)

        def work():
            try:
                localllm.download_model(cfg_fn(), lambda d, t: setattr(st, "progress", (d, t)), st.cancel)
                st.result = ("done", "")
            except localllm.Cancelled:
                st.result = ("cancelled", "")
            except Exception as e:
                log.info("model download failed: %s", e)
                st.result = ("error", str(e) if isinstance(e, localllm.DownloadError) else f"Download failed: {e}")
        st.thread = threading.Thread(target=work, name="model-download", daemon=True)
        st.thread.start()
        poll()

    def cancel():
        st.cancel.set()
        for w in bar.winfo_children():
            w.destroy()
        _label(bar, ui, "Cancelling…", "small", SECONDARY).pack(side="right")

    def poll():
        if not top.winfo_exists():
            return
        if st.result is None:
            done, total = st.progress
            if not st.cancel.is_set():
                show_progress(done, total, st.rate.add(done))
            top.after(200, poll)
            return
        kind, msg = st.result
        if kind == "cancelled":
            close()
        elif kind == "done":
            show_done()
            threading.Thread(target=warm_up, args=(cfg_fn(),), name=localllm.WARMUP_THREAD, daemon=True).start()
            if on_done:
                on_done()
        else:
            show_error(msg)

    def on_close():
        if st.thread is not None and st.result is None:
            cancel()  # closing mid-download = Cancel; poll() destroys the window once the worker has stopped
        else:
            close()
    top.protocol("WM_DELETE_WINDOW", on_close)
    top.show_intro, top.show_progress, top.show_done, top.show_error = show_intro, show_progress, show_done, show_error
    show_intro()
    win.style_window(top)
    top.update_idletasks()
    w, h = max(top.winfo_reqwidth(), k(540)), top.winfo_reqheight()
    if mapped:
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
    else:
        x, y = (top.winfo_screenwidth() - w) // 2, (top.winfo_screenheight() - h) // 3
    top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
    if mapped:
        top.transient(parent)
        top.after(50, lambda: _grab(top))
    else:
        top.attributes("-topmost", True); top.after(400, lambda: top.attributes("-topmost", False))
    top.deiconify()
    return top


def _grab(top):
    try:
        top.grab_set(); top.focus_set()
    except Exception:
        pass


def build(parent, ui, cfg_fn, on_refresh=None):
    """Frame with a status label and, while no model exists, a PRIMARY "Download model…" pill that opens the download dialog on one
    click. Once a model exists the pill is hidden; refresh() re-reads the status and runs itself after a finished download, then
    calls on_refresh(have_model) so a host page can follow."""
    import tkinter as tk
    from .theme import ERROR, SECONDARY, SUCCESS
    from .widgets import pill
    bg = parent.cget("bg")
    fr = tk.Frame(parent, bg=bg)
    lab = tk.Label(fr, bg=bg, fg=SECONDARY, font=ui.fonts["small"], anchor="w", justify="left", wraplength=ui.k(340))
    lab.pack(anchor="w")
    btn = pill(fr, ui, "Download model…", lambda: open_download(fr.winfo_toplevel(), ui, cfg_fn, lambda: fr.winfo_exists() and refresh()), "primary")

    def refresh():
        if not lab.winfo_exists():
            return
        text, kind, have = status_line(cfg_fn())
        lab.config(text=text, fg={"success": SUCCESS, "error": ERROR}.get(kind, SECONDARY))
        btn.relabel("Resume download…" if localllm.part_bytes() else "Download model…")
        btn.pack_forget() if have else btn.pack(anchor="w", pady=(ui.k(8), 0))
        if on_refresh:
            on_refresh(have)
    fr.refresh = refresh
    refresh()
    return fr
