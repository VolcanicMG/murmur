"""Settings inputs in the rounded design language: Framed (rounded panel around an Entry/Text/Listbox), TextField, Select with its
popup list. Pure Tk + render.py images; Tk main thread only."""
import time, tkinter as tk
from . import render
from .theme import ACCENT, HAIR, INPUT, SECONDARY, SEL, TEXT, WHITE, blend
from .widgets import Scrollbar, put

_GEOMETRY = ("pack", "grid", "place", "pack_forget", "grid_remove", "grid_forget", "place_forget", "pack_configure", "grid_configure", "pack_info", "grid_info")


def fit(font, text, maxw):
    """text shortened with an ellipsis to measure <= maxw px."""
    if font.measure(text) <= maxw:
        return text
    while text and font.measure(text + "\u2026") > maxw:
        text = text[:-1]
    return text.rstrip() + "\u2026"


class Framed(tk.Canvas):
    """Rounded field/panel (radius 8, INPUT fill, hairline; brighter on hover, accent while the inner widget has focus) around
    `inner`, which is made by make(self). The inner widget's geometry methods are redirected to the frame, so callers pack/grid
    the inner widget as before. shape: (pad x, pad y, fixed height px or None): fixed = single-line field with the inner widget
    centred; None = the frame hugs the inner widget."""

    def __init__(self, parent, ui, make, shape=(10, 10, None)):
        self.ui, self.hover = ui, False
        k = ui.k
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.inner = inner = make(self)
        self.px, self.py, self.fixed_h = k(shape[0]), k(shape[1]), shape[2]
        fixed_h = self.fixed_h
        self.config(width=inner.winfo_reqwidth() + 2 * self.px, height=fixed_h or inner.winfo_reqheight() + 2 * self.py)
        self.win = self.create_window(self.px, self.py, window=inner, anchor="w" if fixed_h else "nw")
        for m in _GEOMETRY:
            setattr(inner, m, getattr(self, m))
        for w in (self, inner):
            for ev in ("<Enter>", "<Leave>"):
                w.bind(ev, lambda e: self._sync(), add="+")
        for ev in ("<FocusIn>", "<FocusOut>"):
            inner.bind(ev, lambda e: self.draw(), add="+")
        self.bind("<Configure>", self._resize)

    def _sync(self):
        x, y = self.winfo_pointerxy()
        on = 0 <= x - self.winfo_rootx() < self.winfo_width() and 0 <= y - self.winfo_rooty() < self.winfo_height()
        if on != self.hover:
            self.hover = on
            self.draw()

    def _resize(self, e):
        w, h = e.width - 2 * self.px, e.height - 2 * self.py
        if self.fixed_h:
            self.coords(self.win, self.px, e.height / 2)
            self.itemconfig(self.win, width=w)
        else:
            self.itemconfig(self.win, width=w, height=h)
        self.draw()

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("bg")
        self._imgs = []
        if w < 2 or h < 2:
            return
        try:
            focused = self.focus_get() is self.inner
        except KeyError:
            focused = False
        line = ACCENT if focused else blend(WHITE, INPUT, 0.22) if self.hover else HAIR
        put(self, render.rounded((w, h), self.ui.k(8), INPUT, line), 0, 0, "bg")
        self.tag_lower("bg")


class _Entry(tk.Entry):
    def set(self, v):
        self.delete(0, "end"); self.insert(0, str(v))


def TextField(parent, ui, width=20, digits=False):
    """Rounded single-line entry (32 px x scale, text inset 12 px) with get()/set(); `digits` restricts input to whole numbers.
    Returns the Entry; its pack/grid act on the frame around it."""
    def make(frame):
        e = _Entry(frame, relief="flat", bd=0, bg=INPUT, fg=TEXT, insertbackground=ACCENT, selectbackground=SEL, selectforeground=TEXT,
                   highlightthickness=0, font=ui.fonts["body"], width=width)
        if digits:
            e.config(validate="key", validatecommand=(e.register(lambda v: v == "" or v.isdigit()), "%P"))
        return e
    return Framed(parent, ui, make, (12, 0, ui.k(32))).inner


def frame_text(parent, ui, make, pad=10):
    """A Text/Listbox made by make(frame) inside a rounded panel; returns the inner widget (geometry redirected to the panel)."""
    return Framed(parent, ui, make, (pad, pad, None)).inner


_current = None  # the one open popup


class _Popup(tk.Toplevel):
    """Borderless option list under a Select: rows of 32 px x scale, a fixed gutter for the current-value dot, rounded hover row,
    8 rows max (then wheel/Scrollbar). Closes on Esc, a pick, a click outside, the window moving, focus loss or a second Select click."""

    ROWS = 8

    def __init__(self, sel):
        global _current
        if _current:
            _current.close()
        super().__init__(sel)
        self.withdraw(); self.overrideredirect(True); self.attributes("-topmost", True)
        k, self.sel, self.opts = sel.ui.k, sel, sel.options
        n = len(self.opts)
        page = sel.master.cget("bg")
        self.bg, self.rh, self.pad, self.rows, self.w = blend(WHITE, page, 0.06), k(32), k(4), min(n, self.ROWS), sel.px
        self.h = self.rows * self.rh + 2 * self.pad
        self.cur = self.opts.index(sel.get()) if sel.get() in self.opts else -1
        self.hi = max(self.cur, 0)
        self.top = max(0, min(self.hi - self.rows // 2, n - self.rows))
        self.configure(bg=self.bg)
        self.cv = tk.Canvas(self, width=self.w, height=self.h, bg=page, highlightthickness=0, bd=0)
        self.cv.pack()
        self.sb = None
        if n > self.rows:
            self.sb = Scrollbar(self, self._sb_cmd)
            self.sb.place(x=self.w - k(10) - k(3), y=self.pad, width=k(10), height=self.rows * self.rh)
        self._bind()
        _current = self
        self.geometry(f"{self.w}x{self.h}+{self._pos()}")
        self.draw()
        self.deiconify(); self.lift(); self.cv.focus_force()

    def _pos(self):
        sel = self.sel
        x, y = sel.winfo_rootx(), sel.winfo_rooty() + sel.winfo_height() + sel.ui.k(4)
        try:
            from . import win
            bottom = win.work_area((x, y))[3]
        except Exception:
            bottom = sel.winfo_screenheight()
        if y + self.h > bottom:
            y = sel.winfo_rooty() - sel.ui.k(4) - self.h
        return f"{x}+{y}"

    def _bind(self):
        cv = self.cv
        for ev, fn in (("<Motion>", self._motion), ("<Button-1>", lambda e: self._choose(self._row(e.y))), ("<Up>", lambda e: self._move(-1)),
                       ("<Down>", lambda e: self._move(1)), ("<Return>", lambda e: self._choose(self.hi)), ("<Escape>", lambda e: self.close()),
                       ("<MouseWheel>", self._wheel)):
            cv.bind(ev, fn)
        if self.sb:
            self.sb.bind("<MouseWheel>", self._wheel)
        self.after(150, lambda: cv.bind("<FocusOut>", lambda e: self.close()))  # not before focus has landed
        self._bind_outside()

    def _bind_outside(self):
        """Close on a click elsewhere in the settings window (not on a Select), wheel, or the window moving."""
        tl = self._tl = self.sel.winfo_toplevel()

        def outside(e):
            if not isinstance(e.widget, Select):
                self.close()

        def moved(e):
            if e.widget is tl:
                self.close()
        handlers = (("<Button-1>", outside), ("<MouseWheel>", lambda e: self.close()), ("<Configure>", moved))
        self._ids = [(ev, tl.bind(ev, fn, add="+")) for ev, fn in handlers]

    def _row(self, y):
        return max(0, min(self.top + (y - self.pad) // self.rh, len(self.opts) - 1))

    def _motion(self, e):
        r = self._row(e.y)
        if r != self.hi:
            self.hi = r
            self.draw()

    def _move(self, d):
        self.hi = max(0, min(self.hi + d, len(self.opts) - 1))
        self.top = max(self.hi - self.rows + 1, min(self.top, self.hi))
        self.draw()

    def _wheel(self, e):
        self._scroll(-1 if e.delta > 0 else 1)

    def _scroll(self, d):
        self.top = max(0, min(self.top + d, len(self.opts) - self.rows))
        self.draw()

    def _sb_cmd(self, what, *a):
        if what == "moveto":
            self.top = max(0, min(round(float(a[0]) * len(self.opts)), len(self.opts) - self.rows)); self.draw()
        else:
            self._scroll(int(a[0]) * self.rows)

    def _choose(self, i):
        self.sel.var.set(self.opts[i])
        self.close()

    def draw(self):
        k, cv, n = self.sel.ui.k, self.cv, len(self.opts)
        f, w = self.sel.ui.fonts["body"], self.w
        cv.delete("all")
        cv._imgs = []
        img = render.rounded((w, self.h), k(8), self.bg, HAIR).copy()
        for r in range(self.rows):
            i, y = self.top + r, self.pad + r * self.rh
            if i == self.hi:
                img.alpha_composite(render.rounded((w - 2 * self.pad, self.rh), k(6), blend(ACCENT, self.bg, 0.30)), (self.pad, y))
            if i == self.cur:
                img.alpha_composite(render.circle(k(6), ACCENT), (k(12), y + (self.rh - k(6)) // 2))
        put(cv, img, 0, 0)
        room = w - k(28 + 12) - (k(14) if self.sb else 0)
        for r in range(self.rows):
            cv.create_text(k(28), self.pad + r * self.rh + self.rh / 2, text=fit(f, self.opts[self.top + r], room), anchor="w", fill=TEXT, font=f)
        if self.sb:
            self.sb.set(self.top / n, (self.top + self.rows) / n)

    def close(self):
        global _current
        if _current is not self:
            return
        _current = None
        for ev, i in self._ids:
            self._tl.unbind(ev, i)
        self.sel.popup_closed()
        self.destroy()


class Select(tk.Canvas):
    """Dropdown drawn like the other controls: rounded field, left-padded value (ellipsized), right-aligned chevron.
    Click/Down/Enter/Space opens the option popup. get()/set()/reset(options); `var` is the StringVar (trace it to react to
    changes). Width fitted to the options, capped at 260 px x scale (a Test pill beside it must still fit)."""

    def __init__(self, parent, ui, options, var=None):
        k, f = ui.k, ui.fonts["body"]
        self.var, self.ui, self.options = var or tk.StringVar(value=options[0] if options else ""), ui, list(options)
        self.px = min(k(260), f.measure("0") * min(max([len(o) for o in options] + [8]), 34) + k(12 + 12 + 10 + 8))
        super().__init__(parent, width=self.px, height=k(32), bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.hover = self.opened = False
        self._closed = 0.0
        self.var.trace_add("write", lambda *_: self.draw())
        for ev, fn in (("<Enter>", lambda e: self._set_hover(True)), ("<Leave>", lambda e: self._set_hover(False)), ("<FocusIn>", lambda e: self.draw()),
                       ("<FocusOut>", lambda e: self.draw()), ("<Button-1>", self._click), ("<Down>", lambda e: self.post()),
                       ("<Return>", lambda e: self.post()), ("<space>", lambda e: self.post()), ("<Destroy>", lambda e: self.close_popup())):
            self.bind(ev, fn)
        self.draw()

    def resize(self, px):
        """Fixed width in px (e.g. so table rows align)."""
        self.px = px
        self.config(width=px)
        self.draw()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def _click(self, e):
        self.focus_set()
        if self.opened:  # a click on the field while open: the popup's focus-out may already have closed it, so don't reopen at once
            self.close_popup()
        elif time.monotonic() - self._closed > 0.25:
            self.post()

    def draw(self):
        """Field + chevron as one image (so the chevron has no fringe), value text on top."""
        k, w, h = self.ui.k, self.px, self.ui.k(32)
        self.delete("all")
        self._imgs = []
        try:
            focused = self.focus_get() is self
        except KeyError:
            focused = False
        line = ACCENT if self.opened or focused else blend(WHITE, INPUT, 0.22) if self.hover else HAIR
        img = render.rounded((w, h), k(8), INPUT, line).copy()
        cw, ch = k(10), k(6)
        img.alpha_composite(render.chevron((cw, ch), SECONDARY), (w - k(12) - cw, (h - ch) // 2))
        put(self, img, 0, 0)
        self.create_text(k(12), h / 2, text=fit(self.ui.fonts["body"], self.var.get(), w - k(12 + 12 + 10 + 8)), anchor="w", fill=TEXT, font=self.ui.fonts["body"])

    def post(self):
        """Open the option popup under the field."""
        self.opened = True
        self.draw()
        _Popup(self)

    def close_popup(self):
        if _current and _current.sel is self:
            _current.close()

    def popup_closed(self):
        self.opened, self._closed = False, time.monotonic()
        if self.winfo_exists():
            self.draw()

    def reset(self, options):
        self.options = list(options)

    def get(self):
        return self.var.get()

    def set(self, v):
        self.var.set(v)
