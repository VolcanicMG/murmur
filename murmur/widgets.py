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


class TextField(tk.Entry):
    """Flat entry with get()/set(); `digits` restricts input to whole numbers."""

    def __init__(self, parent, ui, width=20, digits=False):
        super().__init__(parent, relief="flat", bd=0, bg=INPUT, fg=TEXT, insertbackground=ACCENT, selectbackground=SEL, selectforeground=TEXT,
                         highlightthickness=1, highlightbackground=HAIR, highlightcolor=ACCENT, font=ui.fonts["body"], width=width)
        if digits:
            self.config(validate="key", validatecommand=(self.register(lambda v: v == "" or v.isdigit()), "%P"))

    def set(self, v):
        self.delete(0, "end"); self.insert(0, str(v))


class Select(tk.OptionMenu):
    """Flat dropdown with an anti-aliased chevron; reset(options) swaps the choices. Width is fixed in px (capped at 260 px x scale, so a Test pill beside it still fits the card)."""

    def __init__(self, parent, ui, options, var=None):
        self.var, self.ui = var or tk.StringVar(value=options[0] if options else ""), ui
        super().__init__(parent, self.var, *options)
        k, f = ui.k, ui.fonts["body"]
        self.px = min(k(260), f.measure("0") * min(max([len(o) for o in options] + [8]), 34) + k(20) + k(10 + 14))
        self.config(bg=INPUT, fg=TEXT, activebackground=on_fill(0.14), activeforeground=TEXT, highlightthickness=1, highlightbackground=HAIR,
                    relief="flat", bd=0, font=f, anchor="w", padx=k(10), pady=k(3), cursor="hand2", indicatoron=0, compound="right", width=self.px)
        self["menu"].config(bg=on_fill(0.06), fg=TEXT, activebackground=SEL, activeforeground=TEXT, bd=0, borderwidth=0, activeborderwidth=0, relief="flat", font=f)
        self.var.trace_add("write", lambda *_: self._chev())
        self._chev()

    def _chev(self):
        """Chevron image as wide as the free space right of the text, glyph at its far end (so it sits at the button's right edge)."""
        k = self.ui.k
        cw, ch = k(10), k(6)
        free = self.px - 2 * k(10) - self.ui.fonts["body"].measure(self.var.get()) - 2 * k(1)
        w = max(cw + k(6), free)
        from PIL import Image
        img = Image.new("RGBA", (w, ch), (0, 0, 0, 0)); img.alpha_composite(render.chevron((cw, ch), SECONDARY), (w - cw, 0))
        self._chev_img = photo(img, INPUT)
        self.config(image=self._chev_img)

    def reset(self, options):
        menu = self["menu"]
        menu.delete(0, "end")
        for o in options:
            menu.add_command(label=o, command=tk._setit(self.var, o))

    def get(self):
        return self.var.get()

    def set(self, v):
        self.var.set(v)


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
