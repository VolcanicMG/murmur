"""Settings + history window in the caption-card design language (tkinter, Tk main thread only).
Each page builder returns (page, load); load() (re)fills the page from disk."""
import queue, sys, threading, tkinter as tk, tkinter.font as tkfont, tomllib
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
import pyperclip
from . import cues, render, store, win
from .theme import ACCENT, CMD_TEXT, ERROR, FIELD, FILL, HAIR, INPUT, NAME, ON_ACCENT, SECONDARY, SEL, SIDEBAR, SUCCESS, TEXT, WHITE, blend, on_fill, pick_fonts
from .inputs import Select, TextField, frame_text
from .widgets import Scrollbar, wheel_scroll, Chip, Pill, Tag, Tile, Toggle, photo, pill, put

SECTIONS = {
    "History": "Everything murmur has typed for you, dictation and commands. Fix a row to teach it new spellings.",
    "Dictionary": "Preferred spellings. Cleanup is nudged toward these words.",
    "Snippets": "Say \u201cinsert <name>\u201d to expand a snippet.",
    "Prompt": "The cleanup rules sent to the LLM with every dictation.",
    "Tones": "How murmur writes in each kind of app. Map apps to tones in Config.",
    "Config": "Hotkeys, models and apps. Advanced holds the raw config.toml.",
}


def icon_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # frozen exe unpacks to _MEIPASS
    return base / "packaging" / "murmur.ico"


def status_kind(text):
    low = text.lower()
    if low.startswith("saved") and "failed" not in low:
        return "success"
    return "error" if any(w in low for w in ("failed", "not saved", "can't", "cannot")) else "info"


class Status(tk.Frame):
    """Bottom status line: kind dot + message."""

    def __init__(self, parent, ui):
        super().__init__(parent, bg=FILL)
        self.dot = tk.Canvas(self, width=ui.k(8), height=ui.k(8), bg=FILL, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, ui.k(8)))
        self.label = tk.Label(self, bg=FILL, fg=SECONDARY, font=ui.fonts["small"], anchor="w")
        self.label.pack(side="left")
        self.ui = ui
        self.set("")

    def set(self, text, kind=None):
        col = {"success": SUCCESS, "error": ERROR}.get(kind or status_kind(text), SECONDARY)
        self.dot.delete("all")
        self.dot._imgs = []
        if text:
            put(self.dot, render.circle(self.ui.k(8), col), 0, 0)
        self.label.config(text=text, fg=col if kind or text else SECONDARY)


DEFAULT_MIC = "System default"


def input_devices():
    """Input device labels "<name> (<hostapi>)", de-duplicated, as [(label, name)]; sounddevice imported lazily (Linux tests)."""
    import sounddevice as sd
    apis, seen, out = sd.query_hostapis(), set(), []
    for d in sd.query_devices():
        label = f"{d['name']} ({apis[d['hostapi']]['name']})"
        if d["max_input_channels"] > 0 and label not in seen:
            seen.add(label); out.append((label, d["name"]))
    return out


class MicField(tk.Frame):
    """Input-device Select plus Test mic (1.5 s on a worker thread, peak level to the status line). Stores the device NAME."""

    def __init__(self, parent, ui, status):
        super().__init__(parent, bg=parent.cget("bg"))
        self.status, self.q, self.names = status, queue.Queue(), {DEFAULT_MIC: ""}
        try:
            devs = input_devices()
        except Exception:
            devs = []
        self.names.update(devs)
        self.sel = Select(self, ui, list(self.names)); self.sel.pack(side="left")
        Pill(self, ui, "Test mic", self.test).pack(side="left", padx=(ui.k(8), 0))

    def get(self):
        return self.names.get(self.sel.get(), "")

    def set(self, name):
        label = next((l for l, n in self.names.items() if n == name), None)
        if label is None and name:  # configured device not present now: keep it selectable so a save doesn't drop it
            label = f"{name} (not found)"; self.names[label] = name; self.sel.reset(list(self.names))
        self.sel.set(label or DEFAULT_MIC)

    def test(self):
        self.status.set("Listening for 1.5 s\u2026", "info")
        threading.Thread(target=self._rec, args=(self.get(),), daemon=True).start()
        self.after(100, self._poll)

    def _rec(self, name):
        try:
            import sounddevice as sd
            idx = store.resolve_device(name, sd.query_devices())
            data = sd.rec(int(1.5 * 16000), samplerate=16000, channels=1, dtype="float32", device=idx, blocking=True)
            self.q.put(float(abs(data).max()))
        except Exception as e:
            self.q.put(e)

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            res = self.q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll); return
        if isinstance(res, Exception):
            self.status.set(f"Can't open the input: {res}", "error")
        elif res < 0.01:
            self.status.set("No signal \u2014 check the input", "error")
        else:
            self.status.set(f"Mic ok \u00b7 peak {res:.2f}", "success")


class HotkeyField(tk.Frame):
    """Entry plus a Record pill: a worker thread runs keyboard.read_hotkey, the Tk side polls a queue (Esc cancels)."""

    def __init__(self, parent, ui, status):
        super().__init__(parent, bg=parent.cget("bg"))
        self.status, self.q, self.busy = status, queue.Queue(), False
        self.entry = TextField(self, ui, 22)
        self.entry.pack(side="left"); Pill(self, ui, "Record", self.record).pack(side="left", padx=(ui.k(8), 0))

    def get(self):
        return self.entry.get()

    def set(self, v):
        self.entry.set(v)

    def record(self):
        if self.busy:
            return
        self.busy = True
        self.status.set("Press the keys… (Esc cancels)", "info")
        threading.Thread(target=self._read, daemon=True).start()
        self.after(100, self._poll)

    def _read(self):
        from .app import S
        S["capturing"] = True  # the tray's own hotkeys must ignore what is being recorded
        try:
            import keyboard
            name = keyboard.read_hotkey(suppress=False)
            self.q.put(name if "+" in name else keyboard.normalize_name(name))
        except Exception as e:
            self.q.put(e)
        finally:
            S["capturing"] = False

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            res = self.q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll); return
        self.busy = False
        if isinstance(res, Exception):
            self.status.set(f"Can't record keys: {res}", "error")
        elif res == "esc":
            self.status.set("Recording cancelled.", "info")
        else:
            self.set(res); self.status.set(f"Captured {res}. Save to apply.", "info")


class Panel(tk.Canvas):
    """Rounded card (radius 12, 16 px padding, 14 px section title); put rows in `.rows`."""

    def __init__(self, parent, ui, title):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, height=ui.k(40))
        self.ui, self.pad = ui, ui.k(16)
        inner = tk.Frame(self, bg=FIELD)
        self.title = _label(inner, ui, title, "section", TEXT, FIELD); self.title.pack(anchor="w", pady=(0, ui.k(6)))
        self.rows = tk.Frame(inner, bg=FIELD); self.rows.pack(fill="x")
        self.win = self.create_window(self.pad, self.pad, window=inner, anchor="nw")
        self.bind("<Configure>", self._resize)
        inner.bind("<Configure>", lambda e: self.config(height=inner.winfo_reqheight() + 2 * self.pad))

    def _resize(self, e):
        self.itemconfig(self.win, width=e.width - 2 * self.pad)
        self.delete("bg")
        self._imgs = []
        if e.width > 1 and e.height > 1:
            put(self, render.rounded((e.width, e.height), self.ui.k(12), FIELD, HAIR), 0, 0, "bg")
            self.tag_lower("bg")


class AppsTable(tk.Frame):
    """process -> tone rows, each with a tone Select and Remove, plus an add row. get() -> {exe: tone}."""

    def __init__(self, parent, ui):
        super().__init__(parent, bg=FIELD)
        self.ui, self.tones, self.vars = ui, [], {}
        self.list = tk.Frame(self, bg=FIELD); self.list.pack(fill="x")
        add = tk.Frame(self, bg=FIELD); add.pack(fill="x", pady=(ui.k(10), 0))
        self.name = TextField(add, ui, 18); self.name.pack(side="left")
        self.pick = Select(add, ui, ["casual"]); self.pick.resize(ui.k(170)); self.pick.pack(side="left", padx=ui.k(8))
        Pill(add, ui, "Add row", self.add).pack(side="left")

    def get(self):
        return {e: v.get() for e, v in self.vars.items()}

    def set(self, apps, tones=None):
        if tones is not None:
            self.tones = tones; self.pick.reset(tones); self.pick.set(tones[0] if tones else "")
        self.vars = {e: tk.StringVar(value=t) for e, t in apps.items() if isinstance(t, str)}
        self.render()

    def add(self):
        if (exe := self.name.get().strip().lower()):
            self.vars[exe] = tk.StringVar(value=self.pick.get()); self.name.set(""); self.render()

    def remove(self, exe):
        self.vars.pop(exe, None); self.render()

    def render(self):
        k = self.ui.k
        for w in self.list.winfo_children():
            w.destroy()
        for exe, var in self.vars.items():
            row = tk.Frame(self.list, bg=FIELD); row.pack(fill="x", pady=k(2))
            _label(row, self.ui, exe, "body", NAME, FIELD).pack(side="left"); row.winfo_children()[0].config(width=22)
            sel = Select(row, self.ui, self.tones + ([var.get()] if var.get() not in self.tones else []), var=var)
            sel.resize(k(170)); sel.pack(side="left", padx=k(8))
            Tag(row, self.ui, "Remove", lambda e=exe: self.remove(e)).pack(side="left")


class ChipList(tk.Frame):
    """Removable exe-name chips (wrapping) plus an add entry. get() -> list."""

    def __init__(self, parent, ui, lower=True):
        super().__init__(parent, bg=FIELD)
        self.ui, self.items, self.lower = ui, [], lower
        self.flow = tk.Frame(self, bg=FIELD); self.flow.pack(fill="x")
        add = tk.Frame(self, bg=FIELD); add.pack(fill="x", pady=(ui.k(10), 0))
        self.name = TextField(add, ui, 18); self.name.pack(side="left")
        Pill(add, ui, "Add", self.add).pack(side="left", padx=(ui.k(8), 0))
        self.flow.bind("<Configure>", lambda e: self.render())

    def get(self):
        return list(self.items)

    def set(self, items):
        self.items = list(items); self.render()

    def add(self):
        n = self.name.get().strip()
        if n and self.lower:
            n = n.lower()
        if n and n not in self.items:
            self.items.append(n); self.name.set(""); self.render()

    def render(self):
        k, x, y, rowh = self.ui.k, 0, 0, self.ui.k(32)
        limit = max(self.flow.winfo_width(), k(300))
        for w in self.flow.winfo_children():
            w.destroy()
        for n in self.items:
            t = Tag(self.flow, self.ui, f"{n}  ×", lambda n=n: (self.items.remove(n), self.render()))
            w = int(t.cget("width"))
            if x and x + w > limit:
                x, y = 0, y + rowh
            t.place(x=x, y=y); x += w + k(6)
        self.flow.config(height=y + rowh)



def _style(top, ui):
    st = ttk.Style(top)
    st.theme_use("clam")
    k = ui.k
    st.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT, rowheight=k(28), borderwidth=0, font=ui.fonts["body"], bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD)
    st.configure("Treeview.Heading", background=on_fill(0.06), foreground=SECONDARY, relief="flat", borderwidth=0, font=ui.fonts["small"], padding=(k(8), k(6)))
    st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])
    st.map("Treeview.Heading", background=[("active", on_fill(0.08))])


def _entry(parent, ui, bg=FIELD):
    return TextField(parent, ui, 20)


def _text(parent, ui, height=12, mono=True):
    """Multi-line editor in a rounded panel (returns the Text; pack/grid act on the panel)."""
    def make(f):
        return tk.Text(f, relief="flat", bd=0, bg=INPUT, fg=TEXT, insertbackground=ACCENT, selectbackground=SEL, selectforeground=TEXT,
                       highlightthickness=0, height=height, wrap="word", undo=True, padx=0, pady=0, font=ui.fonts["mono" if mono else "body"])
    return frame_text(parent, ui, make)


def _listbox(parent, ui, height=12):
    def make(f):
        return tk.Listbox(f, relief="flat", bd=0, bg=INPUT, fg=TEXT, selectbackground=SEL, selectforeground=TEXT, activestyle="none",
                          highlightthickness=0, height=height, font=ui.fonts["body"], exportselection=False)
    return frame_text(parent, ui, make)


def _scrolled(parent, make, bg=FILL):
    """(frame, widget): widget with a slim vertical scrollbar; bg = the surface it sits on."""
    box = tk.Frame(parent, bg=bg)
    w = make(box)
    sb = Scrollbar(box, lambda *a: w.yview(*a))
    w.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y", padx=(4, 0)); w.pack(side="left", fill="both", expand=True)
    return box, w


def _label(parent, ui, text, role="small", fg=SECONDARY, bg=FILL):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=ui.fonts[role], anchor="w")


def _page(content, ui, title, desc):
    f = tk.Frame(content, bg=FILL)
    _label(f, ui, title, "title", TEXT).pack(anchor="w", padx=ui.k(20), pady=(ui.k(20), 0))
    _label(f, ui, desc, "body").pack(anchor="w", padx=ui.k(20), pady=(ui.k(4), ui.k(14)))
    bar = tk.Frame(f, bg=FILL); bar.pack(side="bottom", fill="x", padx=ui.k(20), pady=(ui.k(12), ui.k(8)))  # packed before the body so it stays visible
    body = tk.Frame(f, bg=FILL); body.pack(fill="both", expand=True, padx=ui.k(20))
    return SimpleNamespace(frame=f, body=body, bar=bar)


def _pills(page, ui, *specs):
    for label, cmd, kind in specs:
        pill(page.bar, ui, label, cmd, kind).pack(side="left", padx=(0, ui.k(8)))


def _set(box, text):
    box.delete("1.0", "end"); box.insert("1.0", text)


def _row_values(r):
    mode = "Command" if r.get("mode") == "command" else "Dictation"
    return r.get("ts", "").replace("T", " "), mode, r.get("app", ""), r.get("tone", ""), r["final"][:100].replace("\n", " ")


def row_detail(r):
    """(meta line, raw pane text, final pane text) for a history row; command rows show the instruction then the selection."""
    cmd = r.get("mode") == "command"
    meta = " \u00b7 ".join(x for x in (r.get("app"), r.get("title"), r.get("tone"), "Command" if cmd else "Dictation") if x)
    raw = r.get("raw", "")
    if cmd:
        sel = r.get("selection", "")
        raw += "\n\nSelection:\n" + (sel if len(sel) <= 300 else sel[:299] + "\u2026") if sel else ""
    return meta, raw, r.get("final", "")


def _history_page(content, ui, root, fix):
    pg, rows, k = _page(content, ui, "History", SECTIONS["History"]), [], ui.k
    strip = tk.Frame(pg.body, bg=FILL); strip.pack(fill="x", pady=(0, k(12)))
    tiles = {n: Tile(strip, ui, n) for n in ("Dictations", "Commands", "Words", "Minutes saved", "This week")}
    for i, t in enumerate(tiles.values()):
        t.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else k(8), 0))
    box, tree = _scrolled(pg.body, lambda p: ttk.Treeview(p, columns=("time", "mode", "app", "tone", "text"), show="headings", height=3))
    tree.tag_configure("command", foreground=CMD_TEXT)  # Treeview can't colour one cell, so command rows get the accent text
    for col, w in (("time", 140), ("mode", 80), ("app", 110), ("tone", 60), ("text", 380)):
        tree.heading(col, text=col.capitalize(), anchor="w"); tree.column(col, width=k(w), anchor="w")
    pane = Panel(pg.body, ui, ""); pane.pack(side="bottom", fill="x", pady=(k(12), 0))  # packed before the table: it keeps its height, the table shrinks first
    box.pack(fill="both", expand=True)
    cols = tk.Frame(pane.rows, bg=FIELD); cols.pack(fill="x"); cols.columnconfigure((0, 1), weight=1, uniform="c")
    views = {}
    for i, name in enumerate(("Raw", "Final")):
        _label(cols, ui, name, "label", SECONDARY, FIELD).grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else k(12), 0))
        sbox, t = _scrolled(cols, lambda p: _text(p, ui, 4, mono=False), FIELD); t.config(state="disabled")
        sbox.grid(row=1, column=i, sticky="ew", padx=(0 if i == 0 else k(12), 0)); views[name] = t

    def show_detail(r):
        meta, raw, final = row_detail(r) if r else ("Select a row to compare what was heard with what was typed.", "", "")
        pane.title.config(text=meta)
        for name, txt in (("Raw", raw), ("Final", final)):
            views[name].config(state="normal"); _set(views[name], txt); views[name].config(state="disabled")
    tree.bind("<<TreeviewSelect>>", lambda e: show_detail(current()))

    def current():
        sel = tree.selection()
        return rows[int(sel[0])] if sel else None

    def load():
        rows[:] = store.read_history(300)[::-1]
        tree.delete(*tree.get_children())
        for i, r in enumerate(rows):
            tree.insert("", "end", iid=str(i), values=_row_values(r), tags=("command",) if r.get("mode") == "command" else ())
        show_detail(None)
        s = store.stats(store.read_history())
        for name, v in zip(tiles, (s["dictations"], s["commands"], s["words"], f"~{s['minutes_saved']:.0f} min", s["week_words"])):
            tiles[name].set(v)

    def copy():
        if r := current():
            pyperclip.copy(r["final"])

    def copy_raw():
        if r := current():
            pyperclip.copy(r.get("raw", ""))

    def fix_row():
        if (r := current()) and fix:
            fix(root, r)

    def delete():
        if r := current():
            store.delete_row(r["ts"], r["final"]); load()
    _pills(pg, ui, ("Copy", copy, "primary"), ("Copy raw", copy_raw, "secondary"), ("Fix\u2026", fix_row, "secondary"), ("Delete row", delete, "danger"), ("Refresh", load, "secondary"))
    return pg, load


def _dictionary_page(content, ui):
    pg = _page(content, ui, "Dictionary", SECTIONS["Dictionary"])
    box, lb = _scrolled(pg.body, lambda p: _listbox(p, ui))
    box.pack(fill="both", expand=True)
    entry = _entry(pg.body, ui); entry.pack(fill="x", pady=(ui.k(12), 0))

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
    entry.bind("<Return>", lambda e: add())
    _pills(pg, ui, ("Add", add, "primary"), ("Remove", remove, "danger"))
    return pg, load


def _snippets_page(content, ui):
    pg, snips = _page(content, ui, "Snippets", SECTIONS["Snippets"]), {}
    box, lb = _scrolled(pg.body, lambda p: _listbox(p, ui, 5))
    box.pack(fill="x")
    _label(pg.body, ui, "Name").pack(anchor="w", pady=(ui.k(12), ui.k(4)))
    name = _entry(pg.body, ui); name.pack(fill="x")
    _label(pg.body, ui, "Text").pack(anchor="w", pady=(ui.k(12), ui.k(4)))
    body = _text(pg.body, ui, 6); body.pack(fill="both", expand=True)

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
    _pills(pg, ui, ("Save", save, "primary"), ("New", new, "secondary"), ("Remove", remove, "danger"), ("Reload", load, "secondary"))
    return pg, load


def _prompt_page(content, ui):
    pg = _page(content, ui, "Prompt", SECTIONS["Prompt"])
    box, text = _scrolled(pg.body, lambda p: _text(p, ui, 18))
    box.pack(fill="both", expand=True)

    def load():
        _set(text, store.load_prompt())

    def save():
        (store.HOME / "prompt.md").write_text(text.get("1.0", "end").strip() + "\n", encoding="utf-8")

    def reset():
        _set(text, store.DEFAULT_PROMPT.strip()); save()
    _pills(pg, ui, ("Save", save, "primary"), ("Reset to default", reset, "secondary"), ("Reload", load, "secondary"))
    return pg, load


def _reload(cfg, status, standalone):
    """Apply saved config to the live dict in place (never empty); flag hotkey changes."""
    old = dict(cfg.get("hotkeys", {}))
    try:
        new = store.load_config()
    except Exception as e:
        status.set(f"Saved, but config failed to load: {e}", "error")
        return
    cfg.update(new)
    for k in [k for k in cfg if k not in new]:
        cfg.pop(k)
    if standalone:
        status.set("Saved. Restart murmur to apply.", "success")
    else:
        status.set("Saved." + (" Hotkey changes apply after restart." if cfg.get("hotkeys") != old else ""), "success")


def _tones_page(content, ui, cfg, status, after_save):
    pg, boxes = _page(content, ui, "Tones", SECTIONS["Tones"]), {}
    _label(pg.body, ui, "Saving rewrites config.toml and drops its comments (the Config page keeps them).").pack(anchor="w", pady=(0, ui.k(8)))
    inner = _scroll_frame(pg.body)

    def load():
        for w in inner.winfo_children():
            w.destroy()
        boxes.clear()
        for n, text in {**store.DEFAULT_TONES, **cfg.get("tones", {})}.items():
            _label(inner, ui, n, "chip", NAME).pack(anchor="w", pady=(ui.k(8), ui.k(4)))
            boxes[n] = _text(inner, ui, 3, mono=False); boxes[n].pack(fill="x"); _set(boxes[n], text)

    def save():
        try:
            store.save_tones({n: b.get("1.0", "end").strip() for n, b in boxes.items()})
        except ValueError as e:
            status.set(str(e), "error"); return
        after_save()
    _pills(pg, ui, ("Save", save, "primary"), ("Reload", load, "secondary"))
    return pg, load


def _scroll_frame(parent):
    """Frame inside a vertically scrolling canvas (dark scrollbar, mouse wheel)."""
    sc = tk.Canvas(parent, bg=FILL, highlightthickness=0, bd=0)
    sb = Scrollbar(parent, lambda *a: sc.yview(*a))
    sc.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y", padx=(4, 0)); sc.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(sc, bg=FILL)
    win_id = sc.create_window(0, 0, window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
    sc.bind("<Configure>", lambda e: sc.itemconfig(win_id, width=e.width))
    sc.wheel = lambda e: sc.yview_scroll(-1 if e.delta > 0 else 1, "units")  # found by widgets.wheel_scroll from any child under the pointer
    wheel_scroll(sc)
    return inner


def _show_rows(widgets, on):
    """Show/hide grid-managed widgets (a form row's label and control)."""
    for w in widgets:
        w.grid() if on else w.grid_remove()


def _config_page(content, ui, status, after_save, standalone=False):
    """Form of cards over config.toml (Advanced = the raw editor); load() refills every control from disk over defaults."""
    pg = _page(content, ui, "Config", SECTIONS["Config"])
    B = SimpleNamespace(ui=ui, k=ui.k, form=_scroll_frame(pg.body), fields={}, status=status, standalone=standalone, after_save=after_save)
    hooks = [c(B) for c in (_hotkeys_card, _dictation_card, _speech_card, _cleanup_card, _code_card, _apps_card)]  # each returns a post-load hook or None
    raw = _advanced_card(B)

    def load():
        cfg = store.merged_config()
        for path, w in B.fields.items():
            _load_field(cfg, path, w)
        raw.load()
        for h in hooks:
            h and h()

    def save_form():
        try:
            store.save_config(store.form_to_cfg({p: w.get() for p, w in B.fields.items()}, store.load_config()))
        except (ValueError, TypeError) as e:
            status.set(f"Not saved: {e}", "error"); return
        after_save()
    raw.bind_buttons(load)
    _pills(pg, ui, ("Save", save_form, "primary"), ("Reload", load, "secondary"))
    _label(pg.bar, ui, "Saving the form rewrites config.toml and drops its comments.").pack(side="left", padx=(B.k(4), 0))
    return pg, load


def _load_field(cfg, path, w):
    """Fill one form control from the merged config."""
    if path == "apps":
        tones = list(cfg.get("tones", {})) or list(store.DEFAULT_TONES)
        w.set({a: t for a, t in cfg.get("apps", {}).items() if isinstance(t, str)}, tones)
    elif path == "no_copy":
        w.set(cfg.get("apps", {}).get("no_copy", store.NO_COPY))
    else:
        sec, key = path.split(".")
        w.set(cfg.get(sec, {}).get(key, ""))


def _card(B, title, note=None):
    p = Panel(B.form, B.ui, title); p.pack(fill="x", pady=(0, B.k(12)))
    if note:
        n = _label(p.rows, B.ui, note, "small", SECONDARY, FIELD)
        n.config(justify="left"); n.pack(side="bottom", anchor="w", pady=(B.k(6), 0))
        p.rows.bind("<Configure>", lambda e: n.config(wraplength=e.width))  # wrap to the card's inner width
    return p.rows


def _grid_card(B, title, note=None):
    """A card whose rows share a 2-column grid; a note is packed below the grid frame."""
    g = tk.Frame(_card(B, title, note), bg=FIELD); g.pack(side="top", fill="x")
    g.columnconfigure(0, minsize=B.k(210)); g.columnconfigure(1, weight=1)
    return g


def _row(B, g, path, label, widget):
    if path:
        B.fields[path] = widget
    r = g.grid_size()[1]
    lab = _label(g, B.ui, label, "body", NAME, FIELD); lab.grid(row=r, column=0, sticky="w", pady=B.k(5), padx=(0, B.k(16)))
    widget.grid(row=r, column=1, sticky="w", pady=B.k(5))
    return lab, widget


def _text_row(B, g, path, label):
    lab, w = _row(B, g, path, label, TextField(g, B.ui, 30))
    w.grid_configure(sticky="ew")
    return lab, w


def _number_row(B, g, path, label):
    lab, w = _row(B, g, path, label, TextField(g, B.ui, 8, digits=True))
    return lab, w


def _toggle_row(B, g, path, label):
    return _row(B, g, path, label, Toggle(g, B.ui))


def _hotkeys_card(B):
    g = _grid_card(B, "Hotkeys", "Hotkey changes apply after restart." + (
        " Recording a hotkey while murmur is running will trigger it \u2014 pause murmur from the tray first." if B.standalone else ""))
    for key, label in (("dictate", "Dictate (hold)"), ("command", "Command (hold)"), ("fix_last", "Fix last"), ("learn_word", "Learn word")):
        _row(B, g, f"hotkeys.{key}", label, HotkeyField(g, B.ui, B.status))


def _dictation_card(B):
    g, ui, k = _grid_card(B, "Dictation"), B.ui, B.k
    _toggle_row(B, g, "llm.live_clean", "Live cleanup")
    _row(B, g, "audio.device", "Microphone", MicField(g, ui, B.status))
    _row(B, g, "output.method", "Output method", Select(g, ui, ["paste", "type"]))
    _toggle_row(B, g, "context.enabled", "Read text around the cursor")
    _number_row(B, g, "context.before_chars", "Context before (chars)"); _number_row(B, g, "context.after_chars", "Context after (chars)")
    _toggle_row(B, g, "ui.startup_toast", "Startup notification")
    _toggle_row(B, g, "dictation.toggle", "Tap to toggle (hands-free)")
    _number_row(B, g, "dictation.silence_ms", "Stop after silence (ms)")
    _toggle_row(B, g, "ui.sounds", "Sounds")
    lab, _ = _toggle_row(B, g, "ui.start_sound", "Start sound")
    lab.config(padx=k(14)); _label(g, ui, "Plays when recording begins", "small", SECONDARY, FIELD).grid(row=g.grid_size()[1] - 1, column=1, sticky="w", padx=(k(60), 0))
    vol = Select(g, ui, ["0.1", "0.25", "0.5"])
    _row(B, g, "ui.sound_volume", "Sound volume", vol)

    def play_test():
        cues.play("start", {"ui": {"sounds": True, "start_sound": True, "sound_volume": float(vol.get() or 0.25)}})
    Pill(g, ui, "Test", play_test).grid(row=g.grid_size()[1] - 1, column=1, sticky="w", padx=(vol.px + k(8), 0))  # same cell as the Select: no third column to widen the card


def _speech_card(B):
    g = _grid_card(B, "Speech model")
    _text_row(B, g, "stt.model", "Model"); _row(B, g, "stt.quantization", "Quantization", Select(g, B.ui, ["int8", "none"]))


def _cleanup_card(B):
    """Cleanup model + optional fallback; returns the hook that applies provider/fallback row visibility after a load."""
    g, ui = _grid_card(B, "Cleanup model", "The fallback sends your dictated text to that provider."), B.ui
    provider = Select(g, ui, ["openai", "anthropic"]); _row(B, g, "llm.provider", "Provider", provider)
    _text_row(B, g, "llm.url", "URL"); _text_row(B, g, "llm.model", "Model"); _number_row(B, g, "llm.timeout", "Timeout (s)")
    key_row = _text_row(B, g, "llm.api_key_env", "API key env var")
    lms_rows = _text_row(B, g, "llm.lms", "LM Studio CLI") + _text_row(B, g, "llm.lms_load", "LM Studio load command")

    def show_provider(*_):
        _show_rows(key_row, provider.get() == "anthropic"); _show_rows(lms_rows, provider.get() == "openai")
    provider.var.trace_add("write", show_provider)
    fb_on = Toggle(g, ui); _row(B, g, "llm.fallback", "Fall back to a second provider when the first is down", fb_on)
    fb_prov = Select(g, ui, ["openai", "anthropic"]); fb_rows = list(_row(B, g, "llm_fallback.provider", "Provider", fb_prov))
    fb_rows += _text_row(B, g, "llm_fallback.url", "URL") + _text_row(B, g, "llm_fallback.model", "Model")
    fb_rows += _number_row(B, g, "llm_fallback.timeout", "Timeout (s)") + _text_row(B, g, "llm_fallback.api_key_env", "API key env var")
    show_fallback = lambda *_: _show_rows(fb_rows, fb_on.get())
    fb_on.bind("<Button-1>", lambda e: g.after(1, show_fallback), add="+")

    def after_load():
        provider.var.set(provider.get())  # fire the show/hide trace
        show_fallback()
    return after_load


def _code_card(B):
    rows, k = _card(B, "Code"), B.k
    cg = tk.Frame(rows, bg=FIELD); cg.pack(side="top", fill="x"); cg.columnconfigure(0, minsize=k(210)); cg.columnconfigure(1, weight=1)
    _row(B, cg, "code.file_tags", "File tags", Select(cg, B.ui, ["off", "chat", "always"]))
    _label(rows, B.ui, "Workspace roots to index (besides the folder VS Code, Cursor or Windsurf has open)", "small", SECONDARY, FIELD).pack(anchor="w", pady=(k(10), k(4)))
    B.fields["code.roots"] = ChipList(rows, B.ui, lower=False); B.fields["code.roots"].pack(fill="x")


def _apps_card(B):
    rows, k = _card(B, "Apps & tones"), B.k
    _label(rows, B.ui, "Process name to tone", "small", SECONDARY, FIELD).pack(anchor="w", pady=(0, k(4)))
    B.fields["apps"] = AppsTable(rows, B.ui); B.fields["apps"].pack(fill="x")
    _label(rows, B.ui, "Never copy selection in (command mode)", "small", SECONDARY, FIELD).pack(anchor="w", pady=(k(14), k(4)))
    B.fields["no_copy"] = ChipList(rows, B.ui); B.fields["no_copy"].pack(fill="x")


def _advanced_card(B):
    """The raw config.toml editor; returns an object with load() and bind_buttons(reload) (it needs the page's reload callback)."""
    ui, k = B.ui, B.k
    adv = Panel(B.form, ui, "Advanced"); adv.pack(fill="x", pady=(0, k(12)))
    _label(adv.rows, ui, "The raw config.toml. Saving here keeps comments; the form above drops them.", "small", SECONDARY, FIELD).pack(anchor="w")
    raw = tk.Frame(adv.rows, bg=FIELD)
    box, edit = _scrolled(raw, lambda p: _text(p, ui, 14)); box.pack(fill="x", pady=(k(8), 0))
    err = _label(raw, ui, "", "small", ERROR, FIELD); err.pack(anchor="w", pady=(k(6), 0))
    btns = tk.Frame(raw, bg=FIELD); btns.pack(anchor="w", pady=(k(8), 0))
    shown = [False]

    def toggle_raw():
        shown[0] = not shown[0]
        (raw.pack if shown[0] else raw.pack_forget)(fill="x")
        arrow.config(text="Hide raw editor" if shown[0] else "Show raw editor")
    arrow = tk.Label(adv.rows, text="Show raw editor", bg=FIELD, fg=ACCENT, font=ui.fonts["chip"], cursor="hand2", anchor="w")
    arrow.pack(anchor="w", pady=(k(6), 0)); arrow.bind("<Button-1>", lambda e: toggle_raw())

    def load():
        _set(edit, (store.HOME / "config.toml").read_text(encoding="utf-8")); err.config(text="")

    def save_raw():
        raw_text = edit.get("1.0", "end")
        try:
            tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError as e:
            err.config(text=f"Not saved: {e}"); B.status.set("Not saved: config.toml has a syntax error", "error"); return
        (store.HOME / "config.toml").write_text(raw_text, encoding="utf-8")
        B.after_save()

    def bind_buttons(reload):
        pill(btns, ui, "Save raw", save_raw, "primary").pack(side="left", padx=(0, k(8))); Pill(btns, ui, "Reload", reload).pack(side="left")
    return SimpleNamespace(load=load, bind_buttons=bind_buttons)


def open_settings(root, cfg, fix=None, standalone=False, start="History"):
    """Open the window; `cfg` is the live config dict (updated in place on save); fix(root, row) opens the row editor."""
    s = win.dpi_scale()
    top = tk.Toplevel(root); top.title("murmur"); top.configure(bg=FILL); top.geometry(f"{round(880 * s)}x{round(600 * s)}")
    ui = SimpleNamespace(s=s, k=lambda v: round(v * s))
    ui_f, semi_f, mono_f = pick_fonts(set(tkfont.families()))
    px = lambda n: -round(n * s)
    ui.fonts = {"body": tkfont.Font(family=ui_f, size=px(13)), "small": tkfont.Font(family=ui_f, size=px(12.5)),
                "label": tkfont.Font(family=ui_f, size=px(11)), "title": tkfont.Font(family=semi_f, size=px(20)),
                "chip": tkfont.Font(family=semi_f, size=px(13)), "pill": tkfont.Font(family=semi_f, size=px(13)),
                "tile": tkfont.Font(family=semi_f, size=px(18)), "section": tkfont.Font(family=semi_f, size=px(14)), "mono": tkfont.Font(family=mono_f, size=px(12))}
    _style(top, ui)
    try:
        root.iconbitmap(default=str(icon_path()))  # also what the taskbar shows
    except tk.TclError:
        pass
    win.style_window(top)
    side = tk.Frame(top, bg=SIDEBAR, width=ui.k(200)); side.pack(side="left", fill="y"); side.pack_propagate(False)
    content = tk.Frame(top, bg=FILL); content.pack(side="left", fill="both", expand=True)
    status = Status(content, ui); status.pack(side="bottom", fill="x", padx=ui.k(20), pady=(0, ui.k(14)))
    editors = []

    def after_save():
        _reload(cfg, status, standalone)
        for load in editors:
            load()  # editors that mirror config.toml re-read it so a later save can't clobber this one

    pages = {"History": (_history_page(content, ui, root, fix), True), "Dictionary": (_dictionary_page(content, ui), True),
             "Snippets": (_snippets_page(content, ui), False), "Prompt": (_prompt_page(content, ui), False)}
    pages["Tones"] = (_tones_page(content, ui, cfg, status, after_save), False)
    pages["Config"] = (_config_page(content, ui, status, after_save, standalone), False)
    for (pg, load), _ in pages.values():
        load()
    editors += [pages["Tones"][0][1], pages["Config"][0][1]]
    tk.Label(side, text="murmur", bg=SIDEBAR, fg=ACCENT, font=ui.fonts["title"], anchor="w").pack(anchor="w", padx=ui.k(20), pady=(ui.k(20), ui.k(14)))
    chips, cur = {}, [None]

    def show(name):
        if cur[0]:
            pages[cur[0]][0][0].frame.pack_forget(); chips[cur[0]].set_active(False)
        cur[0] = name
        (pg, load), refresh = pages[name]
        pg.frame.pack(fill="both", expand=True)
        chips[name].set_active(True)
        if refresh:
            load()  # list pages re-read on show; editor pages keep unsaved text
    for name in SECTIONS:
        chips[name] = Chip(side, ui, name, lambda n=name: show(n))
        chips[name].pack(fill="x", padx=ui.k(12), pady=ui.k(2))
    show(start if start in SECTIONS else "History")
    return top


def run_standalone(cfg, fix=None, start="History"):
    root = tk.Tk(); root.withdraw()
    top = open_settings(root, cfg, fix, standalone=True, start=start)
    top.bind("<Destroy>", lambda e: root.quit() if e.widget is top else None)
    root.mainloop()
