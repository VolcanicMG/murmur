"""About page: version, where things live, diagnostics and a read-only log viewer. Pure helpers (redact, diagnostics, tail_lines) have no
tk/win32 imports and are unit-tested; the page builder imports tk lazily."""
import os, platform, re, subprocess, sys
from pathlib import Path
from . import __version__, store

CREDIT = "Caption card and settings design from the dictation handoff."
_PRIVATE = re.compile(r"((?:raw|final)=|learn_word: selection |phonetic: |learned(?: word(?: \(voice\))?)?: |auto-learn from your edits: ).*")


def redact(line):
    """Drop everything after `raw=` / `final=` and after the log prefixes that carry words you said or selected (learned terms,
    phonetic matches, learn_word selections): dictated text must never leave the machine in a pasted report."""
    return _PRIVATE.sub(r"\1[redacted]", line)


def tail_lines(path, n=200):
    """Last n lines of a text file (reads at most the final 256 KB); [] if it can't be read."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256 * 1024))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return data.splitlines()[-n:]


def install_path():
    """The folder murmur runs from: the exe's folder when frozen, else the source checkout."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def diagnostics(cfg, log_lines):
    """Plain-text report: version, Python, OS, provider/model names (no keys, URLs or text) and the last 50 log lines, redacted."""
    llm, fb = cfg.get("llm", {}), cfg.get("llm_fallback", {})
    head = [f"murmur {__version__}", f"Python {platform.python_version()}", f"OS {platform.platform()}",
            f"LLM {llm.get('provider', '?')} / {llm.get('model', '?')}",
            f"Fallback {fb.get('provider', '?')} / {fb.get('model', '?')}" if llm.get("fallback") else "Fallback off",
            f"STT {cfg.get('stt', {}).get('model', '?')}", f"Install {install_path()}", "", "--- last log lines ---"]
    return "\n".join(head + [redact(l) for l in log_lines[-50:]])


def _open(path):
    try:
        os.startfile(path)
    except (OSError, AttributeError):
        subprocess.Popen(["explorer", str(path)])


def about_page(content, ui, cfg):
    """(page, load) for the sidebar; load() refreshes the log viewer."""
    import tkinter as tk
    import pyperclip
    from PIL import Image, ImageTk
    from .settings import _label, _page, _scrolled, _set, _text
    from .theme import NAME, SECONDARY, TEXT
    from .widgets import Pill
    k = ui.k
    pg = _page(content, ui, "About", "Version, where things live, and the log.")
    head = tk.Frame(pg.body, bg=pg.body.cget("bg")); head.pack(fill="x")
    try:
        icon = Image.open(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "icon.png").convert("RGBA").resize((k(44), k(44)), Image.LANCZOS)
        pg.icon = ImageTk.PhotoImage(icon)
        tk.Label(head, image=pg.icon, bg=head.cget("bg")).pack(side="left", padx=(0, k(12)))
    except OSError:
        pass
    col = tk.Frame(head, bg=head.cget("bg")); col.pack(side="left")
    _label(col, ui, "murmur", "title", TEXT).pack(anchor="w")
    _label(col, ui, f"Version {__version__} · local dictation, nothing leaves this machine unless you pick a remote model", "small").pack(anchor="w")
    info = tk.Frame(pg.body, bg=pg.body.cget("bg")); info.pack(fill="x", pady=(k(14), 0))
    for i, (name, value) in enumerate((("Config folder", store.HOME), ("Log file", store.HOME / "murmur.log"), ("Installed at", install_path()))):
        _label(info, ui, name, "small", SECONDARY).grid(row=i, column=0, sticky="w", padx=(0, k(16)), pady=k(2))
        _label(info, ui, str(value), "small", NAME).grid(row=i, column=1, sticky="w", pady=k(2))
    log = store.HOME / "murmur.log"
    bar = tk.Frame(pg.body, bg=pg.body.cget("bg")); bar.pack(fill="x", pady=(k(12), k(10)))
    box, view = _scrolled(pg.body, lambda p: _text(p, ui, 12))

    def load():
        view.config(state="normal")
        _set(view, "\n".join(tail_lines(log, 200)) or "(no log yet)")
        view.config(state="disabled"); view.see("end")

    def copy_diag():
        pyperclip.copy(diagnostics(store.merged_config(cfg), tail_lines(log, 50)))
    def run_setup():
        from . import setup
        setup.open_setup(pg.body.winfo_toplevel())  # the wizard opens on top of this settings window
    for text, fn in (("Run setup again", run_setup), ("Open config folder", lambda: _open(store.HOME)), ("Open log", lambda: _open(log)), ("Copy diagnostics", copy_diag), ("Refresh log", load)):
        Pill(bar, ui, text, fn).pack(side="left", padx=(0, k(8)))
    box.pack(fill="both", expand=True)
    _label(pg.body, ui, CREDIT, "label").pack(anchor="w", pady=(k(8), 0))
    return pg, load
