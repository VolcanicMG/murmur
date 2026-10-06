"""Bundled-LLM status line + explicit model download, shared by Settings > Config > Cleanup model and the setup wizard.
status_line is pure (unit-tested); the widget imports tk on use."""
import threading
from types import SimpleNamespace
from . import localllm

CONFIRM = "Downloads about 6 GB into ~/.murmur/models. Click again to start."


def status_line(cfg, ll=localllm):
    """(text, kind "success"|"info"|"error", have_model) for the bundled runtime + model."""
    runtime = (ll.runtime_dir(cfg) / ll.EXE).is_file()
    model = ll.find_model(cfg)
    if model and runtime:
        return f"Runtime ready · model: {model.name}", "success", True
    if model:
        return f"Runtime not downloaded (≈90 MB, fetched on first use) · model: {model.name}", "info", True
    return ("No model found" if runtime else "Runtime not downloaded (≈90 MB, fetched on first use) · No model found"), "error", False


def fmt_progress(done, total):
    return f"Downloading model… {100 * done // max(total, 1)}% ({done / 1e9:.1f} / {total / 1e9:.1f} GB)"


def build(parent, ui, cfg_fn):
    """Frame with a status label and a "Download model (≈6 GB)" pill (hidden once a model exists). The first click only asks for
    confirmation in the status text; the second runs download_model on a worker thread with progress. refresh() re-reads the status."""
    import tkinter as tk
    from .theme import ERROR, SECONDARY, SUCCESS
    from .widgets import Pill
    bg = parent.cget("bg")
    fr = tk.Frame(parent, bg=bg)
    lab = tk.Label(fr, bg=bg, fg=SECONDARY, font=ui.fonts["small"], anchor="w", justify="left", wraplength=ui.k(340))
    lab.pack(anchor="w")
    st = SimpleNamespace(armed=False, busy=False, progress=None, error="")
    pill = Pill(fr, ui, "Download model (≈6 GB)", lambda: click())

    def refresh():
        if st.busy:
            return
        text, kind, have = status_line(cfg_fn())
        lab.config(text=st.error or text, fg=ERROR if st.error else {"success": SUCCESS, "error": ERROR}.get(kind, SECONDARY))
        pill.pack_forget() if have else pill.pack(anchor="w", pady=(ui.k(8), 0))

    def click():
        if st.busy:
            return
        if not st.armed:
            st.armed, st.error = True, ""
            lab.config(text=CONFIRM, fg=SECONDARY)
            return
        st.armed, st.busy, st.progress = False, True, (0, 1)

        def work():
            try:
                localllm.download_model(cfg_fn(), lambda d, t: setattr(st, "progress", (d, t)))
            except Exception as e:
                st.error = f"Download failed: {e}"
            st.busy = False
        threading.Thread(target=work, daemon=True).start()
        poll()

    def poll():
        if not lab.winfo_exists():
            return
        if st.busy:
            lab.config(text=fmt_progress(*st.progress), fg=SECONDARY)
            lab.after(300, poll)
        else:
            refresh()
    fr.refresh = refresh
    refresh()
    return fr
