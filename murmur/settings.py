"""Settings + history window (tkinter, Tk main thread only). Each tab builder returns (frame, load)."""
import tkinter as tk, tomllib
from tkinter import ttk
import pyperclip
from . import store

BG, FG, BOX, MONO = "#1e1e1e", "#d4d4d4", "#252526", ("Consolas", 10)


def _style(top):
    st = ttk.Style(top)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, fieldbackground=BOX)
    st.configure("Treeview", background=BOX, foreground=FG, fieldbackground=BOX)
    st.configure("TNotebook.Tab", background=BOX, foreground=FG, padding=(10, 4))
    st.map("TNotebook.Tab", background=[("selected", BG)])


def _text(parent, height=12):
    return tk.Text(parent, bg=BOX, fg=FG, insertbackground=FG, font=MONO, height=height, wrap="word", undo=True)


def _buttons(parent, *pairs):
    bar = ttk.Frame(parent)
    first = parent.pack_slaves()
    bar.pack(side="bottom", fill="x", padx=8, pady=4, **({"before": first[0]} if first else {}))  # reserve space before the tab body expands
    for label, cmd in pairs:
        ttk.Button(bar, text=label, command=cmd).pack(side="left", padx=2)


def _set(box, text):
    box.delete("1.0", "end"); box.insert("1.0", text)


def _tab(nb, title):
    f = ttk.Frame(nb); nb.add(f, text=title)
    return f


def _row_values(r):
    return r.get("ts", "").replace("T", " "), r.get("app", ""), r.get("tone", ""), r["final"][:100].replace("\n", " ")


def _stats_line():
    s = store.stats(store.read_history())
    saved = f"~{s['minutes_saved']:.0f} min saved"
    return f"{s['dictations']} dictations \u00b7 {s['words']} words \u00b7 {saved} \u00b7 {s['week_words']} words this week"


def _history_tab(nb, root, fix):
    f, rows = _tab(nb, "History"), []
    head = ttk.Label(f); head.pack(anchor="w", padx=8, pady=4)
    tree = ttk.Treeview(f, columns=("time", "app", "tone", "text"), show="headings", height=14)
    for col, w in (("time", 140), ("app", 110), ("tone", 60), ("text", 520)):
        tree.heading(col, text=col); tree.column(col, width=w, anchor="w")
    tree.pack(fill="both", expand=True, padx=8)

    def current():
        sel = tree.selection()
        return rows[int(sel[0])] if sel else None

    def load():
        rows[:] = store.read_history(300)[::-1]
        tree.delete(*tree.get_children())
        for i, r in enumerate(rows):
            tree.insert("", "end", iid=str(i), values=_row_values(r))
        head.config(text=_stats_line())

    def copy():
        if r := current():
            pyperclip.copy(r["final"])

    def fix_row():
        if (r := current()) and fix:
            fix(root, r)

    def delete():
        if r := current():
            store.delete_row(r["ts"], r["final"]); load()
    _buttons(f, ("Copy", copy), ("Fix\u2026", fix_row), ("Delete row", delete), ("Refresh", load))
    return f, load


def _dictionary_tab(nb):
    f = _tab(nb, "Dictionary")
    lb = tk.Listbox(f, bg=BOX, fg=FG, font=MONO, height=14); lb.pack(fill="both", expand=True, padx=8, pady=8)
    entry = ttk.Entry(f); entry.pack(fill="x", padx=8)

    def load():
        lb.delete(0, "end")
        for t in store.load_dictionary():
            lb.insert("end", t)

    def add():
        if t := entry.get().strip():
            store.add_terms([t]); entry.delete(0, "end"); load()

    def remove():
        for i in lb.curselection():
            store.remove_term(lb.get(i))
        load()
    _buttons(f, ("Add", add), ("Remove", remove))
    return f, load


def _snippets_tab(nb):
    f, snips = _tab(nb, "Snippets"), {}
    lb = tk.Listbox(f, bg=BOX, fg=FG, font=MONO, height=6); lb.pack(fill="x", padx=8, pady=8)
    name = ttk.Entry(f); name.pack(fill="x", padx=8)
    body = _text(f, 8); body.pack(fill="both", expand=True, padx=8, pady=4)

    def load():
        snips.clear(); snips.update(store.load_snippets())
        lb.delete(0, "end")
        for n in snips:
            lb.insert("end", n)

    def show(_e=None):
        if lb.curselection():
            n = lb.get(lb.curselection()[0])
            name.delete(0, "end"); name.insert(0, n); _set(body, snips[n])

    def new():
        name.delete(0, "end"); _set(body, "")

    def save():
        if n := name.get().strip():
            snips[n] = body.get("1.0", "end").rstrip("\n"); store.save_snippets(snips); load()

    def remove():
        if (n := name.get().strip()) in snips:
            del snips[n]; store.save_snippets(snips); new(); load()
    lb.bind("<<ListboxSelect>>", show)
    _buttons(f, ("New", new), ("Save", save), ("Remove", remove), ("Reload", load))
    return f, load


def _prompt_tab(nb):
    f = _tab(nb, "Prompt")
    box = _text(f, 20); box.pack(fill="both", expand=True, padx=8, pady=8)
    path = lambda: store.HOME / "prompt.md"

    def load():
        _set(box, store.load_prompt())

    def save():
        path().write_text(box.get("1.0", "end").strip() + "\n", encoding="utf-8")

    def reset():
        _set(box, store.DEFAULT_PROMPT.strip()); save()
    _buttons(f, ("Save", save), ("Reset to default", reset), ("Reload", load))
    return f, load


def _reload(cfg, status, standalone):
    """Apply saved config to the live dict in place (never empty); flag hotkey changes."""
    old = dict(cfg.get("hotkeys", {}))
    try:
        new = store.load_config()
    except Exception as e:
        status.set(f"Saved, but config failed to load: {e}")
        return
    cfg.update(new)
    for k in [k for k in cfg if k not in new]:
        cfg.pop(k)
    if standalone:
        status.set("Saved. Restart murmur to apply.")
    else:
        status.set("Saved." + (" Hotkey changes apply after restart." if cfg.get("hotkeys") != old else ""))


def _tones_tab(nb, cfg, status, after_save):
    f, boxes = _tab(nb, "Tones"), {}
    ttk.Label(f, text="Saving rewrites config.toml and drops its comments (the Config tab keeps them).").pack(anchor="w", padx=8, pady=4)
    body = ttk.Frame(f); body.pack(fill="both", expand=True)

    def load():
        for w in body.winfo_children():
            w.destroy()
        boxes.clear()
        for n, text in {**store.DEFAULT_TONES, **cfg.get("tones", {})}.items():
            ttk.Label(body, text=n).pack(anchor="w", padx=8)
            boxes[n] = _text(body, 3); boxes[n].pack(fill="x", padx=8, pady=(0, 4)); _set(boxes[n], text)

    def save():
        try:
            store.save_tones({n: b.get("1.0", "end").strip() for n, b in boxes.items()})
        except ValueError as e:
            status.set(str(e)); return
        after_save()
    _buttons(f, ("Save", save), ("Reload", load))
    return f, load


def _config_tab(nb, status, after_save):
    f = _tab(nb, "Config")
    box = _text(f, 22); box.pack(fill="both", expand=True, padx=8, pady=8)
    err = ttk.Label(f, foreground="#f48771"); err.pack(anchor="w", padx=8)

    def load():
        _set(box, (store.HOME / "config.toml").read_text(encoding="utf-8")); err.config(text="")

    def save():
        text = box.get("1.0", "end")
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            err.config(text=f"Not saved: {e}"); return
        err.config(text="")
        (store.HOME / "config.toml").write_text(text, encoding="utf-8")
        after_save()
    _buttons(f, ("Save", save), ("Reload", load))
    return f, load


def open_settings(root, cfg, fix=None, standalone=False):
    """Open the window; `cfg` is the live config dict (updated in place on save); fix(root, row) opens the row editor."""
    top = tk.Toplevel(root); top.title("murmur settings"); top.configure(bg=BG); top.geometry("820x560")
    _style(top)
    status, editors = tk.StringVar(), []

    def after_save():
        _reload(cfg, status, standalone)
        for load in editors:
            load()  # editors that mirror config.toml re-read it so a later save can't clobber this one
    nb = ttk.Notebook(top); nb.pack(fill="both", expand=True)
    live = {}
    tabs = [(_history_tab(nb, root, fix), True), (_dictionary_tab(nb), True), (_snippets_tab(nb), False),
            (_prompt_tab(nb), False), (_tones_tab(nb, cfg, status, after_save), False),
            (_config_tab(nb, status, after_save), False)]
    for (f, load), refresh in tabs:
        load()
        if refresh:
            live[str(f)] = load  # only list tabs re-read on switch; editor tabs keep unsaved text
    editors += [tabs[4][0][1], tabs[5][0][1]]
    nb.bind("<<NotebookTabChanged>>", lambda e: live.get(nb.select(), lambda: None)())
    ttk.Label(top, textvariable=status).pack(side="bottom", anchor="w", padx=8, pady=4, before=nb)
    return top


def run_standalone(cfg, fix=None):
    root = tk.Tk(); root.withdraw()
    top = open_settings(root, cfg, fix, standalone=True)
    top.bind("<Destroy>", lambda e: root.quit() if e.widget is top else None)
    root.mainloop()
