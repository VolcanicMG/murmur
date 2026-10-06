"""Settings widgets: anti-aliased pills/chips/tiles/tags/toggle (render.py images on Tk canvases), flat entry and dropdown."""
import tkinter as tk
from . import render
from .theme import ACCENT, ERROR, FIELD, HAIR, INPUT, NAME, ON_ACCENT, SECONDARY, SEL, TEXT, WHITE, blend, on_fill

DANGER_TEXT = "#2A1412"


def photo(img, bg):
    """PhotoImage of an anti-aliased render flattened onto the opaque background colour `bg` (Tk shapes have no AA)."""
    from PIL import Image, ImageTk
    return ImageTk.PhotoImage(Image.alpha_composite(Image.new("RGBA", img.size, render.rgba(bg)), img))


def put(canvas, img, x, y, tag="img"):
    """Draw a render on a canvas at x, y; the PhotoImage is kept alive on the canvas until the next draw."""
    ph = photo(img, canvas.cget("bg"))
    canvas._imgs = getattr(canvas, "_imgs", []) + [ph]
    return canvas.create_image(x, y, image=ph, anchor="nw", tags=tag)


class _Round(tk.Canvas):
    """Canvas that redraws a rounded shape plus content whenever it is resized or hovered."""

    def __init__(self, parent, ui, height, **kw):
        super().__init__(parent, height=ui.k(height), bg=parent.cget("bg"), highlightthickness=0, bd=0, **kw)
        self.ui, self.hover = ui, False
        self.bind("<Configure>", lambda e: self.draw())

    def shape(self, r, fill, outline=""):
        w, h = self.winfo_width(), self.winfo_height()
        if w > 1 and h > 1:
            put(self, render.rounded((w, h), self.ui.k(r), fill or None, outline or None), 0, 0)

    def draw(self):
        self.delete("all")
        self._imgs = []
        self.paint()


class Pill(_Round):
    """32 px pill button: primary (accent), secondary (hairline, default), danger (error); build with pill() to pick the kind."""
    kind = "secondary"

    def __init__(self, parent, ui, text, command):
        super().__init__(parent, ui, 32, cursor="hand2")
        self.text, self.command = text, command  # 14 px horizontal padding, 32 px tall for every kind
        self.config(width=ui.fonts["pill"].measure(text) + ui.k(28))
        self.bind("<Enter>", lambda e: self._hover(True)); self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command() if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height() else None)

    def _hover(self, on):
        self.hover = on
        self.draw()

    def paint(self):
        """Anti-aliased pill image (filled, or hairline when secondary), text on top."""
        bg, w, h = self.cget("bg"), self.winfo_width(), self.winfo_height()
        fill, fg, line = {"primary": (ACCENT, ON_ACCENT, ""), "danger": (ERROR, DANGER_TEXT, ""),
                          "secondary": ("", NAME, on_fill(0.14))}[self.kind]
        if self.hover:
            fill = blend(WHITE, fill or bg, 0.08)
        if w > 1 and h > 1:
            put(self, render.pill((w, h), fill or None, line or None), 0, 0)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.ui.fonts["pill"])


def pill(parent, ui, text, command, kind="secondary"):
    """A Pill of the given kind."""
    p = Pill(parent, ui, text, command)
    p.kind = kind
    return p


class Chip(_Round):
    """Sidebar section chip in the card's status-chip style."""

    def __init__(self, parent, ui, text, command):
        super().__init__(parent, ui, 30, cursor="hand2")
        self.text, self.active = text, False
        self.bind("<Enter>", lambda e: self._hover(True)); self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", lambda e: command())

    def _hover(self, on):
        self.hover = on
        self.draw()

    def set_active(self, on):
        self.active = on
        self.draw()

    def paint(self):
        k, w, h = self.ui.k, self.winfo_width(), self.winfo_height()
        if (self.active or self.hover) and w > 1 and h > 1:
            img = render.rounded((w, h), k(15), on_fill(0.08 if self.active else 0.04)).copy()
            if self.active:  # dot composited onto the chip fill (not the canvas bg) so it has no dark fringe
                r = k(3)
                img.alpha_composite(render.circle(2 * r, ACCENT), (k(14) - r, round(h / 2) - r))
            put(self, img, 0, 0)
        self.create_text(k(26), h / 2, text=self.text, anchor="w", fill=ACCENT if self.active else SECONDARY, font=self.ui.fonts["chip"])


class Tile(_Round):
    """Stat tile: small label over a large value."""

    def __init__(self, parent, ui, label):
        super().__init__(parent, ui, 64, width=ui.k(10))  # natural width small so the row shares space evenly
        self.label, self.value = label, "-"

    def set(self, value):
        self.value = str(value)
        self.draw()

    def paint(self):
        k = self.ui.k
        self.shape(12, FIELD)
        self.create_text(k(14), k(18), text=self.label, anchor="w", fill=SECONDARY, font=self.ui.fonts["label"])
        self.create_text(k(14), k(42), text=self.value, anchor="w", fill=TEXT, font=self.ui.fonts["tile"])




class Toggle(tk.Canvas):
    """44x24 pill switch: accent track when on, knob slides. get()/set() like the other form controls."""

    def __init__(self, parent, ui):
        k = ui.k
        super().__init__(parent, width=k(44), height=k(24), bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self.k, self.on, self.pos = k, False, 0.0
        self.bind("<Button-1>", lambda e: self.set(not self.on, animate=True))
        self.draw()

    def get(self):
        return self.on

    def set(self, on, animate=False):
        self.on = bool(on)
        if animate:
            self._slide()
        else:
            self.pos = float(self.on); self.draw()

    def _slide(self):
        goal = float(self.on)
        self.pos += max(-0.34, min(0.34, goal - self.pos))
        self.draw()
        if self.pos != goal:
            self.after(16, self._slide)

    def draw(self):
        self.delete("all")
        self._imgs = []
        put(self, render.toggle(round(self.pos, 2), (self.k(44), self.k(24)), (on_fill(0.14), ACCENT, NAME, ON_ACCENT)), 0, 0)


class Tag(_Round):
    """Small rounded label button (removable chip / Remove)."""

    def __init__(self, parent, ui, text, command):
        super().__init__(parent, ui, 26, cursor="hand2")
        self.text = text
        self.config(width=ui.fonts["small"].measure(text) + ui.k(22))
        self.bind("<Enter>", lambda e: self._hover(True)); self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", lambda e: command())

    def _hover(self, on):
        self.hover = on
        self.draw()

    def paint(self):
        self.shape(13, on_fill(0.14 if self.hover else 0.09))
        self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text=self.text, fill=TEXT if self.hover else NAME, font=self.ui.fonts["small"])


class Scrollbar(tk.Canvas):
    """Slim vertical scrollbar, drop-in for ttk.Scrollbar: set(first, last) from the widget's yscrollcommand; `command` gets
    ("moveto", frac) while dragging and ("scroll", +-1, "pages") on track clicks. Draws nothing when everything is visible."""

    def __init__(self, parent, command=None):
        self.s = parent.winfo_fpixels("1i") / 96  # same DPI scale as the rest of the window
        super().__init__(parent, width=round(10 * self.s), bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.cmd, self.first, self.last, self.hover, self.grab = command, 0.0, 1.0, False, None
        for ev, fn in (("<Configure>", lambda e: self.draw()), ("<Enter>", lambda e: self._hover(True)), ("<Leave>", lambda e: self._hover(False)),
                       ("<Button-1>", self._press), ("<B1-Motion>", self._drag), ("<ButtonRelease-1>", self._release)):
            self.bind(ev, fn)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._toggle_pack()
        self.draw()

    def _toggle_pack(self):
        """When nothing scrolls, give the column back (pack-managed only) so panels beside it line up with full-width fields."""
        hide = self.first <= 0 and self.last >= 1
        if hide and self.winfo_manager() == "pack":
            self._pack_info = self.pack_info()
            self.pack_forget()
        elif not hide and not self.winfo_manager() and getattr(self, "_pack_info", None):
            info = dict(self._pack_info); info.pop("in", None)
            self.pack(**info)

    def _hover(self, on):
        self.hover = on
        self.draw()

    def _thumb(self):
        """(top, bottom) px of the thumb, at least 28 px x scale tall, or None when nothing scrolls."""
        h = self.winfo_height()
        if self.first <= 0 and self.last >= 1 or h < 2:
            return None
        top, bot = self.first * h, self.last * h
        grow = max(0, round(28 * self.s) - (bot - top))
        top = max(0, min(top - grow * self.first, h - (bot - top) - grow))
        return top, top + (bot - top) + grow

    def draw(self):
        self.delete("all")
        self._imgs = []
        t = self._thumb()
        if not t:
            return
        bg = self.cget("bg")
        col = blend(ACCENT, bg, 0.6) if self.grab is not None else blend(WHITE, bg, 0.24 if self.hover else 0.14)
        tw, th = round(6 * self.s), max(2, round(t[1] - t[0]))
        put(self, render.pill((tw, th), col), (self.winfo_width() - tw) // 2, round(t[0]))

    def _press(self, e):
        t = self._thumb()
        if not t:
            return
        if t[0] <= e.y <= t[1]:
            self.grab = e.y - t[0]
            self.draw()
        elif self.cmd:
            self.cmd("scroll", -1 if e.y < t[0] else 1, "pages")

    def _drag(self, e):
        t = self._thumb()
        if self.grab is None or not t or not self.cmd:
            return
        h = self.winfo_height()
        self.cmd("moveto", max(0.0, min((e.y - self.grab) / h, 1 - (self.last - self.first))))

    def _release(self, e):
        self.grab = None
        self.draw()


def wheel_scroll(root):
    """One global <MouseWheel> dispatcher: scrolls the nearest ancestor of the widget under the pointer that has a `wheel`
    callable (set on scroll canvases); Text/Treeview/Listbox scroll themselves. Idempotent per root."""
    if getattr(root, "_wheel_bound", False):
        return
    root._wheel_bound = True

    def on_wheel(e):
        w = e.widget
        while isinstance(w, tk.Misc):
            if isinstance(w, (tk.Text, tk.Listbox)) or w.winfo_class() == "Treeview":
                return
            if getattr(w, "wheel", None):
                return w.wheel(e)
            w = w.master
    root.bind_all("<MouseWheel>", on_wheel)
