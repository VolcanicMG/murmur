"""Caption-card frames as PIL RGBA images for the per-pixel-alpha layered window (see card.Layered).
Everything is laid out in design px (the handoff's CSS px) and rounded to device px by the DPI scale.
Static pieces (shadow, body, tail, chip, hints) and the transcript text are cached; per frame only the level halo,
waveform bars and chip colour are recomposited. Pure PIL, no tk/win32.
"""
import math
from collections import namedtuple
from types import SimpleNamespace
from PIL import Image, ImageChops, ImageDraw
from . import render
from .card import MAX_LINES, RETRY_TEXT, STATES, W, bar_heights
from .theme import ERROR, FILL, NAME, SECONDARY, TAIL, WHITE

FILL_A = 0.97  # handoff: 0.76 over an acrylic blur. No blur here, so the body is near-opaque or desktop text competes with the transcript
SHADOW = (30, 10, 0.55)  # CSS blur, y offset, alpha: the handoff's 60/24/0.55 scaled down so the window padding stays <= 40 px


View = namedtuple("View", "label col dest up tcx lvl now")  # one frame's inputs: chip label, state colour, destination, tail up?, tail centre (design px), mic level, time


def _cap(cache, key, make, n=48):
    """Dict cache with a crude size cap (frames are cheap to rebuild, unbounded text keys are not)."""
    if key not in cache:
        if len(cache) >= n:
            cache.clear()
        cache[key] = make()
    return cache[key]


class Painter:
    """Draws card frames at one DPI scale. draw() returns a shared image (never mutate); same image object = nothing changed."""

    def __init__(self, s):
        self.s, self.k = s, lambda v: round(v * s)
        k = self.k
        self.blur, self.off, self.sh_a = k(SHADOW[0]), k(SHADOW[1]), SHADOW[2]
        self.pad = render.shadow_pad(self.blur, self.off)
        px = lambda n: round(n * s)
        self.f = {"body": render.font("ui", px(18)), "chip": render.font("semi", px(12.5)), "dest": render.font("ui", px(12.5)),
                  "destb": render.font("semi", px(12.5)), "hint": render.font("ui", px(12)), "key": render.font("mono", px(11)),
                  "retry": render.font("semi", px(13))}
        self._lay, self._stat, self._chip, self._txt = {}, {}, {}, {}
        self.sig = self.img = None
        self.retry_box = None

    def m(self, font, text):
        """Text width in device px."""
        return self.f[font].getlength(text)

    # ---- layout
    def layout(self, name, text, interim):
        """Wrapped transcript lines and the card height (design px) for a state + text."""
        return _cap(self._lay, (name, text, interim), lambda: self._build(name, text, interim))

    def _build(self, name, text, interim):
        _, _, tcol, kind, hints, retry, _, default, _ = STATES[name]
        runs = self._runs(text, interim, default)
        lines = self._wrap(runs)[-MAX_LINES:]
        th = max(58, len(lines) * 27)
        row_h = 32 if retry else 30
        ch = 52 + th + 4 + 10 + row_h + 14
        return SimpleNamespace(name=name, key=(name, text, interim), lines=lines, th=th, row_h=row_h, ch=ch, tcol=tcol, kind=kind, hints=hints, retry=retry)

    @staticmethod
    def _runs(text, interim, default):
        """[(text, class)] runs: final text, the interim word, or the placeholder."""
        if not (text or interim):
            return [(default, "default")] if default else []
        gap = " " if text and interim and not text.endswith(" ") else ""
        return [(text + gap, "main")] + ([(interim, "interim")] if interim else [])

    def _wrap(self, runs):
        """Greedy word wrap of runs to the text width -> lines of [(word + " ", class)]."""
        lines, cur, x, width = [], [], 0.0, self.k(W - 40)
        for t, cls in runs:
            for word in filter(None, t.split(" ")):
                if cur and x + self.m("body", word) > width:
                    lines.append(cur); cur, x = [], 0.0
                cur.append((word + " ", cls)); x += self.m("body", word + " ")
        return lines + [cur] if cur else lines

    # ---- frame
    def draw(self, lay, v):
        """Frame for layout `lay` and View `v`; only the halo, chip colour, text pulse and bars are recomposited per call."""
        k, P = self.k, self.pad
        stat = _cap(self._stat, (lay.ch, lay.hints, lay.retry, lay.kind, v.label, v.dest, v.up, round(v.tcx)), lambda: self._static(lay, v), 16)
        self.retry_box = stat.retry_box
        bar_col = (WHITE, 0.22) if lay.kind == "grey" else v.col
        hs = tuple(max(1, round(h * self.s)) for h in bar_heights(v.lvl, v.now * 1000, lay.kind, 64))
        pq = round((0.75 + 0.15 * math.sin(v.now * 2 * math.pi / 0.9)) * 20) / 20 if lay.name == "polishing" else 1.0
        halo = round(k(8) * (1 + v.lvl * 1.6)) // 2 * 2 if lay.kind == "live" else 0
        sig = (stat.id, v.label, v.col, lay.key, pq, halo, hs, bar_col)
        if sig == self.sig:
            return self.img
        f = stat.img.copy()
        if halo:
            f.alpha_composite(render.circle(halo, (v.col, 0.35)), (stat.dot[0] - halo // 2, stat.dot[1] - halo // 2))
        f.alpha_composite(_cap(self._chip, (v.label, v.col), lambda: self._chip_fg(v.label, v.col, stat), 24), stat.chip_xy)
        if lay.lines:
            f.alpha_composite(_cap(self._txt, (lay.key, pq), lambda: self._text(lay, pq), 24), (P + k(20), P + k(52)))
        self._bars(f, stat.bars, hs, bar_col)
        self.sig, self.img = sig, f
        return f

    def _bars(self, f, span, hs, col):
        """64 round-ended bars across span (x0, x1, centre y); heights in device px."""
        x0, x1, fy = span
        gap = self.k(2)
        pitch = (x1 - x0 + gap) / 64
        for i, h in enumerate(hs):
            xa = round(x0 + i * pitch)
            w = max(1, round(x0 + i * pitch + pitch - gap) - xa)
            f.alpha_composite(render.bar(w, h, col), (xa, fy - h // 2))

    def _static(self, lay, v):
        """Everything that doesn't move with the mic level or the transcript."""
        k, P = self.k, self.pad
        chh = k(lay.ch)
        img = Image.new("RGBA", (k(W) + 2 * P, chh + 2 * P), (0, 0, 0, 0))
        self._body(img, chh)
        self._tail(img, chh, v)
        d = ImageDraw.Draw(img)
        chip_xy, dot, cwid = self._header(img, d, v)
        fy = P + k(52 + lay.th + 4 + 10) + k(lay.row_h) // 2
        box, right = self._footer(img, d, lay, fy)
        bars = (P + k(20), right, fy)
        return SimpleNamespace(img=img, id=id(img), retry_box=box, bars=bars, chip_xy=chip_xy, dot=dot, chip_h=k(26), chip_w=cwid)

    def _footer(self, img, d, lay, fy):
        """Retry pill or hints -> (retry hit box or None, right edge for the bars)."""
        right = self.pad + self.k(W - 14)
        if lay.retry:
            return self._retry(img, d, right, fy)
        return None, (self._hints(img, d, right, fy) if lay.hints else right)

    def _body(self, img, chh):
        """Shadow (cleared under the card), translucent body with border, inner top highlight."""
        k, P, bw = self.k, self.pad, max(1, self.k(1))
        cw = k(W)
        img.alpha_composite(render.shadow((cw, chh), k(24), self.blur, self.off, self.sh_a))
        mask = render.rounded((cw, chh), k(24), "#ffffff").getchannel("A")
        img.paste((0, 0, 0, 0), (P, P), mask)  # CSS box-shadow isn't drawn under the box: the translucent fill must not show it
        img.alpha_composite(render.rounded((cw, chh), k(24), (FILL, FILL_A), (WHITE, 0.10), bw), (P, P))
        inner = render.rounded((cw - 2 * bw, chh - 2 * bw), k(24) - bw, "#ffffff").getchannel("A")  # inset 0 1px 0 white 7%
        lower = Image.new("L", inner.size, 0); lower.paste(inner, (0, bw))
        hl = Image.new("RGBA", inner.size, (255, 255, 255, 0)); hl.putalpha(ImageChops.subtract(inner, lower).point(lambda v: round(v * 0.07)))
        img.alpha_composite(hl, (P + bw, P + bw))

    def _tail(self, img, chh, v):
        """Tail with +bw rows overlapping the card's border so the join is seamless."""
        k, P, bw = self.k, self.pad, max(1, self.k(1))
        t = render.tail(k(14), (TAIL, 0.95), (WHITE, 0.10), v.up, bw)
        img.alpha_composite(t, (P + k(v.tcx) - t.width // 2, P - t.height + bw if v.up else P + chh - bw))

    def _header(self, img, d, v):
        """Chip pill and destination text -> (chip top-left, dot centre, chip width)."""
        k, P, f = self.k, self.pad, self.f
        cy = P + k(14 + 13)
        cwid = k(27 + 11) + math.ceil(self.m("chip", v.label))
        chip_xy = (P + k(18), cy - k(13))
        img.alpha_composite(render.pill((cwid, k(26)), (WHITE, 0.06)), chip_xy)
        if v.dest:
            right = P + k(W - 18)
            d.text((right, cy), v.dest, font=f["destb"], fill=NAME, anchor="rm")
            d.text((right - self.m("destb", v.dest) - k(4), cy), "Typing into", font=f["dest"], fill=SECONDARY, anchor="rm")
        return chip_xy, (P + k(18 + 15), cy), cwid

    def _retry(self, img, d, right, fy):
        """Retry pill -> (hit box in card-local design px, new right edge for the bars)."""
        k, P = self.k, self.pad
        pw = math.ceil(self.m("retry", "Retry")) + k(28)
        img.alpha_composite(render.pill((pw, k(32)), ERROR), (right - pw, fy - k(16)))
        d.text((right - pw / 2, fy), "Retry", font=self.f["retry"], fill=RETRY_TEXT, anchor="mm")
        box = ((right - P - pw) / self.s, (fy - P - k(16)) / self.s, (right - P) / self.s, (fy - P + k(16)) / self.s)
        return box, right - pw - k(16)

    def _hints(self, img, d, right, fy):
        """Enter/Esc keycaps with labels -> new right edge for the bars."""
        k, f, bw = self.k, self.f, max(1, self.k(1))
        items = [("cap", "Enter"), ("lab", "insert"), ("gap", None), ("cap", "Esc"), ("lab", "cancel")]
        wd = {"cap": lambda t: math.ceil(self.m("key", t)) + k(16), "lab": lambda t: math.ceil(self.m("hint", t)), "gap": lambda t: k(12)}
        total = sum(wd[kind](t) for kind, t in items) + 2 * k(6)
        x = right - total
        for kind, t in items:
            w = wd[kind](t)
            if kind == "cap":
                img.alpha_composite(render.rounded((w, k(24)), k(8), None, (WHITE, 0.14), bw), (x, fy - k(12)))
                d.text((x + w / 2, fy), t, font=f["key"], fill=SECONDARY, anchor="mm")
                x += w + k(6)
                continue
            if kind == "lab":
                d.text((x, fy), t, font=f["hint"], fill=SECONDARY, anchor="lm")
            x += w
        return right - total - k(16)

    def _chip_fg(self, label, col, stat):
        """Status label + dot in the state colour, sized like the chip pill."""
        k = self.k
        layer = Image.new("RGBA", (stat.chip_w, stat.chip_h), (0, 0, 0, 0))
        layer.alpha_composite(render.circle(k(8), col), (k(15) - k(4), stat.chip_h // 2 - k(4)))
        layer.alpha_composite(self._text_layer(label, ("chip", col, 1.0), (stat.chip_w - k(27), stat.chip_h)), (k(27), 0))
        return layer

    def _text_layer(self, text, style, size):
        """Anti-aliased text on a transparent layer whose colour is constant (so edges don't darken). style: (font, colour, alpha)."""
        font, col, alpha = style
        r, g, b, _ = render.rgba(col)
        w, h = size
        layer = Image.new("RGBA", (max(1, math.ceil(w)), h), (r, g, b, 0))
        ImageDraw.Draw(layer).text((0, h / 2), text, font=self.f[font], fill=(r, g, b, round(255 * alpha)), anchor="lm")
        return layer

    def _text(self, lay, pq):
        """Transcript lines; the final text pulses (polishing), the interim word is 45% and the placeholder 50%."""
        k = self.k
        alpha = {"main": pq, "interim": 0.45, "default": 0.5 if lay.name == "listening" else 1.0}
        r, g, b, _ = render.rgba(lay.tcol)
        layer = Image.new("RGBA", (k(W - 40), k(lay.th)), (r, g, b, 0))
        d = ImageDraw.Draw(layer)
        for i, line in enumerate(lay.lines):
            x, y = 0.0, k(i * 27 + 13.5)
            for word, cls in line:
                d.text((x, y), word, font=self.f["body"], fill=(r, g, b, round(255 * alpha[cls])), anchor="lm")
                x += self.m("body", word)
        return layer
