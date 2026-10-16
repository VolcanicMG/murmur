"""Caption Card overlay on the Tk main thread. Default renderer: a per-pixel-alpha layered window fed PIL frames from
cardpaint.Painter (real translucency, shadow, anti-aliased shapes); fallback: a chroma-key Canvas (flag LAYERED / failure
of UpdateLayeredWindow at startup). Design source of truth:
the design handoff README (design_handoff_dictation_caption_card/README.md, not in this repo)

Pure helpers (blend, bar_heights, norm_level, place, ellipsize) have no tk/win32 imports and are unit-tested on Linux.
"""
import logging, math, queue, time

log = logging.getLogger("murmur")
from .theme import ACCENT, CMD_TEXT, ERR_TEXT, ERROR, FILL, NAME, SECONDARY, SUCCESS, TAIL, TEXT, WHITE, blend, pick_fonts, rounded_points

RETRY_TEXT = "#2A1412"
LAYERED = True  # False forces the old chroma-key Canvas renderer
KEY = "#010203"  # chroma key: Tk can't do per-pixel alpha, so the card shape is cut out of the window
W, TAIL_PX, ALPHA, MAX_LINES = 560, 10, 0.96, 7  # MAX_LINES: transcript lines kept (the newest)
# label, colour, text colour, bars kind, hints, retry, auto-dismiss ms, default text, keys bound (live session)
STATES = {
    "listening": ("Listening", ACCENT, TEXT, "live", True, False, None, "Start speaking\u2026", True),
    "transcribing": ("Listening", ACCENT, TEXT, "live", True, False, None, "", True),
    "polishing": ("Polishing", ACCENT, TEXT, "wave", True, False, None, "", True),
    "pasted": ("Pasted", SUCCESS, TEXT, "flat", False, False, 800, "", False),
    "command": ("Command", ACCENT, CMD_TEXT, "live", True, False, None, "", True),
    "done": ("Done", SUCCESS, TEXT, "flat", False, False, 1100, "", False),
    "error": ("No microphone", ERROR, ERR_TEXT, "grey", False, True, 8000,
              "No microphone detected. Plug one in or pick another input in Settings.", False),
    "loading": ("Starting", SECONDARY, TEXT, "progress", False, False, None, "Loading speech model\u2026", False),  # grey: not listening
    "ready": ("Ready", SUCCESS, TEXT, "progress", False, False, None, "", False),  # full bar in green for the start-up hold
    "offline": ("Model offline", ERROR, TEXT, "grey", False, False, 2400,
                "Pasted the raw transcript; cleanup unavailable.", False),
    "paste_failed": ("Couldn't paste", ERROR, TEXT, "grey", False, False, 2800,
                     "The target field was lost. Text is on your clipboard.", False),
    "learned": ("Learned", SUCCESS, TEXT, "flat", False, False, 1400, "", False),
    "undone": ("Undone", SUCCESS, TEXT, "flat", False, False, 1200, "Undid the last paste.", False),
    "nothing": ("Nothing to undo", SECONDARY, TEXT, "grey", False, False, 1200, "Nothing to undo.", False),
    "failed": ("Startup failed", ERROR, ERR_TEXT, "grey", False, False, 9000,
               "The speech model failed to load. See murmur.log.", False),
}


STARTUP = ("loading", "ready")  # card states owned by the boot sequence
PROG_RATE, READY_HOLD_MS = 1 / 4.0, 1000  # the loading bar fills at a fixed pace (full in 4 s, never jumps), then "Ready" holds 1 s


def envelope(i, n=64):
    return math.sin(math.pi * (i + 0.5) / n) ** 0.7


def bar_heights(level, t_ms, kind, n=64, span=(2.0, 28.0)):
    """Waveform bar heights. kinds: live (speech-driven), wave (travelling, polishing), flat/grey (idle),
    progress (level = fraction done: bars left of it stand up, the rest stay flat)."""
    lo, hi = span
    out = []
    for i in range(n):
        env = envelope(i, n)
        if kind == "progress":
            h = lo + 0.45 * hi * max(env, 0.25) if (i + 0.5) / n <= level else lo  # floor: the first bars must read as "done"
        elif kind == "wave":
            h = lo + hi * 0.45 * max(0.0, math.sin(i * 0.45 - t_ms / 90)) * env
        elif kind in ("flat", "grey"):
            h = lo + 0.5 * env
        else:
            j = abs(0.55 + 0.45 * math.sin(i * 2.3 + t_ms / 70) * math.cos(i * 0.7 - t_ms / 130))
            h = lo + (hi - lo) * level * env * j
        out.append(h)
    return out


def norm_level(rms):
    """Typical speech RMS (~0.02-0.2) -> 0..1 with a soft curve so the bars move."""
    return 1 - math.exp(-max(rms, 0.0) * 14)


GAP, MARGIN = 16, 12  # card distance from the field, minimum distance from the screen edge


def place(size, field, work, kind="caret"):
    """Card top-left and tail direction. Above the field if it fits, else flipped below; clamped to the work area.
    kind "pane" (field is a whole editor pane, no caret found): sit inside it near the bottom-left, never flip."""
    (w, h), (fl, ft, fr, fb) = size, field
    wl, wt, wr, wb = work
    gap, margin = GAP, MARGIN
    if kind == "pane":
        return max(wl + margin, min(fl + 16, wr - margin - w)), max(wt + margin, min(fb - 16 - h, wb - margin - h)), False
    y, tail_up = ft - gap - h, False
    if y < wt + margin:
        y, tail_up = fb + gap, True
    x = max(wl + margin, min(fl, wr - margin - w))
    y = max(wt + margin, min(y, wb - margin - h))
    return x, y, tail_up


def _sane(r, ctl_rect):
    """A believable caret rect: positive size, at most 80 px tall, touching the control."""
    if not r:
        return False
    l, t, rr, b = r
    if rr <= l or b <= t or b - t > 80:
        return False
    cl, ct, cr, cb = ctl_rect
    return l < cr and rr > cl and t < cb and b > ct


def pick_rect(providers, ctl_rect):
    """((l, t, r, b), kind). providers: callables returning a caret rect or None, tried in order (exceptions skipped);
    the first sane one wins -> kind "caret". Otherwise the control rect: "field" if <= 200 px tall, else "pane" (an editor
    pane, not the line being typed)."""
    for p in providers:
        try:
            r = p()
        except Exception:
            continue
        if _sane(r, ctl_rect):
            return tuple(r), "caret"
    return tuple(ctl_rect), "pane" if ctl_rect[3] - ctl_rect[1] > 200 else "field"


def win32_hwnd(root):
    """Real top-level hwnd of a Tk window (the wrapper above winfo_id)."""
    import win32gui
    return win32gui.GetParent(root.winfo_id()) or root.winfo_id()


def ellipsize(text, n=28):
    return text if len(text) <= n else text[:n - 1].rstrip() + "\u2026"


class Card:
    """The overlay. Owns the Tk root: run() on the main thread, post() from any thread.

    Drawing is always on the main thread. `level` (raw mic RMS) may be written from the audio thread.
    """

    def __init__(self, demo=False):
        import tkinter as tk, tkinter.font as tkfont
        from . import win
        prev = win.foreground()  # Tk init steals focus; handed back below
        self.demo, self.q, self.level = demo, queue.Queue(), 0.0
        self.recording, self.visible, self.handlers, self._keys = False, False, {}, []
        self.tail_up, self.card_x, self.kind = False, 0, "caret"
        self.st, self.text, self.interim, self.field, self.dest, self.label_o = "listening", "", "", None, "", None
        self.lvl, self._t_last, self._anim = 0.0, time.monotonic(), None  # smoothed level; ("in"|"out", t0)
        self.prog, self.prog_cap = 0.0, 0.0  # loading progress: moves at PROG_RATE toward the cap (the running step's end share)
        self._col_from, self._col_t0, self._dismiss_id, self._geom, self.level_fn = ACCENT, 0.0, None, "", None
        self.root = r = tk.Tk()
        r.title("murmur card"); r.overrideredirect(True)
        r.attributes("-topmost", True)
        self.s = win.dpi_scale(r)
        self._retry_box, self._ms, self.layered, self.pad = None, [], False, 0
        self._shown = self._pos = self._a = None
        self.root.geometry("2x2+-32000+-32000")
        r.update()
        win.no_activate(r)
        if LAYERED:
            try:
                from .cardpaint import Painter, View
                from .layered import Layered
                self.lw, self.p, self._view = Layered(win32_hwnd(r)), Painter(self.s), View
                self.pad, self.layered = self.p.pad, True
                r.bind("<Button-1>", self._click)
            except Exception:
                log.exception("layered card window unavailable, using the chroma-key renderer")
        if not self.layered:
            self._init_keyed(tk, tkfont)
        win.refocus(prev)

    def _init_keyed(self, tk, tkfont):
        """Old renderer: chroma-key window + Canvas (no shadow, no real translucency)."""
        r = self.root
        r.attributes("-alpha", 0.0, "-transparentcolor", KEY)
        r.configure(bg=KEY)
        self.c = tk.Canvas(r, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True); self.c.bind("<Button-1>", self._click)
        ui, semi, mono = pick_fonts(set(tkfont.families()))
        px = lambda n: -round(n * self.s)
        self.f = {"body": tkfont.Font(family=ui, size=px(18)), "chip": tkfont.Font(family=semi, size=px(12.5)),
                  "dest": tkfont.Font(family=ui, size=px(12.5)), "destb": tkfont.Font(family=semi, size=px(12.5)),
                  "hint": tkfont.Font(family=ui, size=px(12)), "key": tkfont.Font(family=mono, size=px(11)),
                  "retry": tkfont.Font(family=semi, size=px(13))}
        r.update()

    # ---- threading contract
    def post(self, fn):
        self.q.put(fn)

    def run(self):
        self._loop()
        self.root.mainloop()

    def _loop(self):
        try:
            while not self.q.empty():
                try:
                    self.q.get()()
                except Exception:
                    log.exception("overlay task failed")
            if self.visible:
                self._frame()
        finally:
            self.root.after(33, self._loop)

    # ---- public API (main thread; use post from other threads)
    def show(self, mode, destination, handlers=None, field=None):
        """Start a live session card. mode: dictate|command. handlers: {cancel, enter, retry} callables."""
        self._cancel_dismiss()
        self.recording, self.handlers, self.dest = True, handlers or {}, ellipsize(destination)
        self.field, self.kind = field or self._fallback_field(), "caret"
        self.text = self.interim = ""
        self._set_state("command" if mode == "command" else "listening", fade=False)
        self._appear()

    def state(self, name, text=None, label=None):
        """Switch state; text (if given) replaces the transcript, label the chip. States with a dismiss time hide themselves."""
        if text is not None:
            self.text, self.interim = text, ""
        if not self.visible:
            self._appear()
        self._set_state(name, label=label)

    def transcript(self, final, interim=""):
        self.text, self.interim = final, interim

    def anchor(self, rect, kind="field"):
        """Field rect (l, t, r, b) in screen pixels; the card jumps to it. kind: "caret" | "field" | "pane"."""
        self.field, self.kind = tuple(rect), kind

    def flash(self, name, text=None, label=None, handlers=None):
        """Notice outside a session (learned word, nothing to undo); skipped while a session is live."""
        if self.recording:
            return
        self._cancel_dismiss()
        self.handlers, self.dest, self.field, self.kind = handlers or {}, "", self._fallback_field(), "caret"
        self.text = self.interim = ""
        if text is not None:
            self.text = text
        self._set_state(name, fade=False, label=label)
        self._appear()

    def progress(self, frac, text):
        """Loading card step: `frac` is where this step ends (the bar paces itself there), `text` names the step. The switch
        to "ready" happens in _frame when the bar arrives at 100 %, so the end is never a jump."""
        self.prog_cap, self.text = frac, text
        if self.st not in STARTUP:
            self.flash("loading", text)

    def release(self):
        """The session is over: keep the card if its state dismisses itself, otherwise hide."""
        self.recording = False
        if self._dismiss_id is None and self.st not in STARTUP:
            self.hide()

    def hide(self):
        self._cancel_dismiss()
        self._unbind_keys()
        self.recording = False
        if self.visible and not (self._anim and self._anim[0] == "out"):
            self._anim = ("out", time.monotonic())

    # ---- state / animation
    def _set_state(self, name, fade=True, label=None):
        self._col_from = self._color() if fade and self.visible else STATES[name][1]
        self._col_t0 = time.monotonic()
        self.st, self.label_o = name, label
        self._cancel_dismiss()
        d = STATES[name][6]
        if d:
            self._dismiss_id = self.root.after(d, self._dismiss)
        if STATES[name][8] and self.handlers and not self.demo:
            self._bind_keys()
        else:
            self._unbind_keys()

    def _dismiss(self):
        self._dismiss_id = None
        if not self.recording:
            self.hide()

    def _cancel_dismiss(self):
        if self._dismiss_id:
            self.root.after_cancel(self._dismiss_id); self._dismiss_id = None

    def _appear(self):
        self.visible, self._anim = True, ("in", time.monotonic())
        self._geom = ""
        try:
            from . import win
            win.keep_on_top(win32_hwnd(self.root))
        except Exception:
            log.debug("could not raise the card to the top", exc_info=True)

    def _color(self):
        target = STATES[self.st][1]
        p = min(1.0, (time.monotonic() - self._col_t0) / 0.15)
        return blend(target, self._col_from, p)

    def _bind_keys(self):
        """Esc/Enter are swallowed system-wide only while a live session card is up.

        hook_key (not add_hotkey): a suppressed hotkey leaves the keyboard lib's blocking_hotkeys non-empty
        forever, which holds back every ctrl/alt key-down app-wide. hook_key also matches while ctrl/alt are held.
        """
        if self._keys:
            return
        import keyboard
        try:
            for key, name in (("esc", "cancel"), ("enter", "enter")):
                self._keys.append(keyboard.hook_key(key, lambda e, n=name: self._on_key(e, n), suppress=True))
        except Exception:
            log.exception("binding card keys failed")
            self._unbind_keys()

    def _unbind_keys(self):
        if not self._keys:
            return
        import keyboard
        for h in self._keys:
            try:
                keyboard.unhook(h)
            except Exception:
                log.exception("unbinding card key failed")
        self._keys = []

    def _on_key(self, e, name):
        """Runs inside the low-level hook (keep it cheap): act on key-down, block down and up."""
        if e.event_type == "down":
            self._key(name)
        return False

    def _key(self, name):
        fn = self.handlers.get(name)
        if fn:
            fn()

    def _click(self, e):
        b, x, y = self._retry_box, (e.x - self.pad) / self.s, (e.y - self.pad) / self.s  # card-local design px
        if self.st == "error" and b and b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            retry = self.handlers.get("retry")
            self.hide()
            if retry:
                retry()

    # ---- placement
    def _fallback_field(self):
        from . import win
        r = win.caret_rect()
        if r:
            return r
        l, t, rr, b = win.work_area(None)
        cx = (l + rr) // 2
        return (cx - W // 2, b - 110, cx + W // 2, b - 90)

    def _geometry(self, card_h, slide):
        from . import win
        f = self.field
        work = win.work_area((f[0], f[1]))
        x, y, up = place((W * self.s, card_h * self.s), f, work, kind=self.kind)
        y0 = y - (TAIL_PX * self.s if up else 0)
        self.tail_up, self.card_x = up, x
        return f"{round(W * self.s)}x{round((card_h + TAIL_PX) * self.s)}+{round(x)}+{round(y0 + slide)}"

    # ---- per-frame
    def _frame(self):
        now = time.monotonic()
        dt, self._t_last = now - self._t_last, now
        raw = self.level_fn(now) if self.level_fn else self.level
        kind = STATES[self.st][3]
        if kind == "progress":
            self.prog = min(self.prog_cap, self.prog + dt * PROG_RATE)
            if self.st == "loading" and self.prog >= 1.0:  # reached the end for real: green Ready, then hide unless a session took over
                self._set_state("ready", fade=False)
                self.root.after(READY_HOLD_MS, lambda: self.st in STARTUP and self.hide())
        tgt = norm_level(raw) if kind == "live" else self.prog if kind == "progress" else 0.0
        if self.st == "listening":
            tgt = max(tgt, 0.1 + 0.05 * abs(math.sin(now * 5.5)))  # idle wobble until speech arrives
        tau = 0.08 if tgt > self.lvl else 0.2
        self.lvl += (tgt - self.lvl) * (1 - math.exp(-dt / tau))
        alpha, slide = 1.0 if self.layered else ALPHA, 0.0
        base = alpha
        if self._anim:
            kind, t0 = self._anim
            dur = 0.18 if kind == "in" else 0.14
            p = min(1.0, (now - t0) / dur)
            e = 1 - (1 - p) ** 3 if kind == "in" else p ** 2
            alpha = base * (e if kind == "in" else 1 - e)
            slide = 6 * self.s * (1 - e) if kind == "in" else 6 * self.s * e
            if p >= 1.0:
                self._anim = None
                if kind == "out":
                    self.visible = False
                    if self.layered:
                        self.lw.update(self.lw.im, -32000, -32000, 0)
                        self._shown = self._pos = self._a = None
                        return
                    self.root.attributes("-alpha", 0.0)
                    self.root.geometry("2x2+-32000+-32000"); self._geom = ""
                    return
        if self.layered:
            return self._paint(now, alpha, slide)
        card_h = self._draw(now)
        g = self._geometry(card_h, slide)
        if g != self._geom:
            self._geom = g
            self.root.geometry(g)
        self.root.attributes("-alpha", alpha)

    def _paint(self, now, alpha, slide):
        """Layered renderer: compose a PIL frame and push it with UpdateLayeredWindow (skipped when nothing changed)."""
        from . import win
        t0 = time.perf_counter()
        p, s, f = self.p, self.s, self.field
        lay = p.layout(self.st, self.text, self.interim)
        x, y, up = place((W * s, lay.ch * s), f, win.work_area((f[0], f[1])), kind=self.kind)
        self.tail_up, self.card_x = up, x
        tcx = -1 if self.st in STARTUP else min(max(43 + (f[0] - x) / s, 24), W - 24)  # tail 43 px from the visible left edge unless clamped; none at start-up (nothing to point at)
        label = self.label_o or STATES[self.st][0]
        img = p.draw(lay, self._view(label, self._color(), self.dest, up, tcx, self.lvl, now))
        self._retry_box = p.retry_box
        pos, a = (round(x) - self.pad, round(y + slide) - self.pad), round(255 * alpha)
        if img is not self._shown or pos != self._pos or a != self._a:
            if self._shown is None or img.size != self._shown.size:
                self.root.geometry(f"{img.width}x{img.height}+{pos[0]}+{pos[1]}")  # keeps Tk's idea of the hwnd rect (hit-testing) right
            self.lw.update(img, pos[0], pos[1], a)
            self._shown, self._pos, self._a = img, pos, a
        self._ms.append((time.perf_counter() - t0) * 1000)

    # ---- drawing (design px; the whole canvas is scaled by self.s at the end)
    def _rrect(self, rect, r, fill, outline=""):
        return self.c.create_polygon(rounded_points(rect, r), fill=fill, outline=outline, width=1)

    def _m(self, font, text):
        return self.f[font].measure(text) / self.s

    def _wrap(self, runs, width):
        """Greedy word wrap of [(text, colour)] runs -> lines of [(text, colour)]."""
        lines, cur, x = [], [], 0.0
        for text, col in runs:
            for word in filter(None, text.replace("\n", " \n ").split(" ")):  # a "\n" token (list item) starts a new line
                if word == "\n" or cur and x + self._m("body", word) > width:
                    lines.append(cur); cur, x = [], 0.0
                if word != "\n":
                    cur.append((word + " ", col)); x += self._m("body", word + " ")
        if cur:
            lines.append(cur)
        return lines

    def _draw(self, now):
        c, st = self.c, STATES[self.st]
        label, _, tcol, kind, hints, retry, _, default, _ = st
        label = self.label_o or label
        col = self._color()
        c.delete("all")
        pulse = 0.75 + 0.15 * math.sin(now * 2 * math.pi / 0.9) if self.st == "polishing" else 1.0
        if self.text or self.interim:
            runs = [(self.text, blend(tcol, FILL, pulse))] + ([(self.interim, blend(tcol, FILL, 0.45))] if self.interim else [])
            if self.text and self.interim and not self.text.endswith(" "):
                runs[0] = (self.text + " ", runs[0][1])
        else:
            runs = [(default, blend(tcol, FILL, 0.5 if self.st == "listening" else 1.0))] if default else []
        lines = self._wrap(runs, W - 40)
        th = max(58, len(lines) * 27)
        row_h = 32 if retry else 30
        ch = 52 + th + 4 + 10 + row_h + 14
        up = getattr(self, "tail_up", False)
        oy = TAIL_PX if up else 0
        hair = blend(WHITE, FILL, 0.10)
        # tail first (half hidden under the card), then card
        tcx = min(max(43 + (self.field[0] - getattr(self, "card_x", self.field[0])) / self.s, 24), W - 24)
        d = 9.9
        if up:
            tri = [tcx - d, oy, tcx, oy - d, tcx + d, oy]
        else:
            tri = [tcx - d, ch, tcx, ch + d, tcx + d, ch]
        self._rrect((0.5, oy + 0.5, W - 0.5, oy + ch - 0.5), 24, FILL, hair)
        if self.st not in STARTUP:
            c.create_polygon(tri, fill=TAIL, outline=hair, width=1)
        by = oy if up else ch
        c.create_line(tcx - d + 1, by + (0.5 if up else -0.5), tcx + d - 1, by + (0.5 if up else -0.5), fill=TAIL)
        # header: chip
        cy = oy + 14 + 13
        lw = self._m("chip", label)
        cw = 11 + 8 + 8 + lw + 11
        self._rrect((18, cy - 13, 18 + cw, cy + 13), 13, blend(WHITE, FILL, 0.06))
        chip_bg = blend(WHITE, FILL, 0.06)
        dx = 18 + 11 + 4
        if kind == "live":
            r = 4 * (1 + self.lvl * 1.6)
            c.create_oval(dx - r, cy - r, dx + r, cy + r, fill=blend(col, chip_bg, 0.35), outline="")
        c.create_oval(dx - 4, cy - 4, dx + 4, cy + 4, fill=col, outline="")
        c.create_text(18 + 11 + 8 + 8, cy, text=label, anchor="w", fill=col, font=self.f["chip"])
        if self.dest:
            nx = W - 18
            c.create_text(nx, cy, text=self.dest, anchor="e", fill=NAME, font=self.f["destb"])
            c.create_text(nx - self._m("destb", self.dest) - 4, cy, text="Typing into", anchor="e", fill=SECONDARY, font=self.f["dest"])
        # transcript
        for i, line in enumerate(lines):
            x = 20.0
            for text, tc in line:
                c.create_text(x, oy + 52 + i * 27 + 13.5, text=text, anchor="w", fill=tc, font=self.f["body"])
                x += self._m("body", text)
        # footer
        fy = oy + 52 + th + 4 + 10 + row_h / 2
        right, self._retry_box = W - 14, None
        if retry:
            tw = self._m("retry", "Retry")
            self._rrect((right - tw - 28, fy - 16, right, fy + 16), 16, ERROR)
            c.create_text(right - 14 - tw, fy, text="Retry", anchor="w", fill=RETRY_TEXT, font=self.f["retry"])
            self._retry_box = (right - tw - 28, fy - 16, right, fy + 16)
            right -= tw + 28 + 16
        elif hints:
            right = self._hints(fy, right) - 16
        self._bars((fy, 20, right), kind, col, now)
        c.scale("all", 0, 0, self.s, self.s)
        return ch  # card height; the tail margin is added by _geometry

    def _hints(self, fy, right):
        c = self.c
        items = [("cap", "Enter"), ("lab", "insert"), ("gap", 12), ("cap", "Esc"), ("lab", "cancel")]
        wd = {"cap": lambda t: self._m("key", t) + 16, "lab": lambda t: self._m("hint", t), "gap": lambda t: t}
        total = sum(wd[k](t) for k, t in items) + 12  # 6px gap after each keycap
        x = right - total
        for k, t in items:
            if k == "cap":
                w = wd[k](t)
                self._rrect((x, fy - 12, x + w, fy + 12), 8, FILL, blend(WHITE, FILL, 0.14))
                c.create_text(x + w / 2, fy, text=t, fill=SECONDARY, font=self.f["key"])
                x += w + 6
            elif k == "lab":
                c.create_text(x, fy, text=t, anchor="w", fill=SECONDARY, font=self.f["hint"]); x += wd[k](t)
            else:
                x += t
        return right - total

    def _bars(self, row, kind, col, now):
        fy, x0, x1 = row
        n = 64
        bw = max(1.0, (x1 - x0 - 2 * (n - 1)) / n)
        dim = blend(WHITE, FILL, 0.22)
        color = dim if kind == "grey" else col
        for i, h in enumerate(bar_heights(self.lvl, now * 1000, kind, n)):
            x = x0 + i * (bw + 2) + bw / 2
            half = max(0.0, h / 2 - bw / 2)
            c = dim if kind == "progress" and (i + 0.5) / n > self.lvl else color
            self.c.create_line(x, fy - half, x, fy + half + 0.01, width=bw * self.s, fill=c, capstyle="round")


def demo(state=None):
    """--demo-card: card at a fixed spot with synthetic level; cycles all states every 2.2 s unless one is given."""
    c = Card(demo=True)
    sw, sh = c.root.winfo_screenwidth(), c.root.winfo_screenheight()
    field = (sw // 2 - 280, sh // 2, sw // 2 + 280, sh // 2 + 40)
    names = list(STATES)
    raw = "ok just pushed the new build to staging can someone sanity check the onboarding flow before lunch"
    clean = "Ok, just pushed the new build to staging. Can someone sanity-check the onboarding flow before lunch?"
    sample = {"transcribing": (raw[:-6], "lunch"), "polishing": (clean, ""), "pasted": (clean, ""),
              "command": ("\u201cdelete before lunch\u201d", ""), "done": ("Removed \u201cbefore lunch\u201d", ""),
              "learned": ("\u201cPodstead\u201d added to your dictionary", "")}
    c.level_fn = lambda t: 0.02 + 0.2 * abs(math.sin(t * 9) * math.sin(t * 2.7 + 0.7)) + 0.02 * abs(math.sin(t * 31))
    c.show("dictate", "Slack \u2014 # launch-crew", field=field)

    def apply(name):
        text, interim = sample.get(name, ("", ""))
        c.state(name, text); c.transcript(text, interim)
        c._cancel_dismiss()  # demo holds each state
        c.prog, c.prog_cap = (1.0, 1.0) if name == "ready" else (0.0, 0.999)  # loading: the bar paces up (stops short of flipping to ready); ready: full

    def bench():  # composition + UpdateLayeredWindow time over the first 200 frames, written next to the screenshots
        if len(c._ms) < 200:
            return c.root.after(500, bench)
        from . import store
        ms = sorted(c._ms[:200])
        out = f"layered={c.layered} scale={c.s} frames=200 avg={sum(ms) / 200:.2f}ms p95={ms[189]:.2f}ms max={ms[-1]:.2f}ms\n"
        (store.HOME / "shots").mkdir(exist_ok=True); (store.HOME / "shots" / "card-frame-ms.txt").write_text(out)
    c.root.after(500, bench)
    if state:
        c.root.after(300, lambda: apply(state))
    else:
        i = [0]

        def step():
            apply(names[i[0] % len(names)]); i[0] += 1
            c.root.after(2200, step)
        c.root.after(300, step)
    c.run()
