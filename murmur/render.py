"""Anti-aliased shapes for the card and settings window. Tk canvas shapes and chroma-key windows have no AA,
so every curved shape is drawn here at 4x and downscaled with LANCZOS. Pure PIL, no tk/win32 (unit-tested).

Colours: "#rrggbb" or ("#rrggbb", alpha 0-1). Every function is LRU-cached by its arguments and returns a shared
RGBA image: callers must not mutate it (copy first).
"""
import math
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

SS = 4
FONT_DIR = "C:\\Windows\\Fonts\\"


def rgba(c, a=1.0):
    """(r, g, b, A) from "#rrggbb" or ("#rrggbb", alpha)."""
    if isinstance(c, tuple):
        c, a = c
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), round(255 * a)


def _mask(size, radius, inset=0):
    """L mask of a rounded rect at supersampled `size`, shrunk by `inset` px on every side."""
    m = Image.new("L", size, 0)
    w, h = size
    if w - 2 * inset > 0 and h - 2 * inset > 0:
        ImageDraw.Draw(m).rounded_rectangle((inset, inset, w - 1 - inset, h - 1 - inset), max(0, radius - inset), fill=255)
    return m


def _solid(size, colour, mask):
    """Layer of one colour whose alpha is mask scaled by the colour's alpha (colour stays constant: no dark fringe)."""
    r, g, b, a = rgba(colour)
    layer = Image.new("RGBA", size, (r, g, b, 0))
    layer.putalpha(mask if a == 255 else mask.point(lambda v: round(v * a / 255)))
    return layer


def _down(img, size):
    return img.resize(size, Image.LANCZOS)  # RGBA resize is premultiplied inside Pillow


@lru_cache(maxsize=512)
def rounded(size, radius, fill=None, outline=None, width=1):
    """size (w, h) px, radius px; outline is an inside border of `width` px composited over the fill."""
    w, h = size
    big, r = (w * SS, h * SS), radius * SS
    outer = _mask(big, r)
    img = Image.new("RGBA", big, (0, 0, 0, 0))
    if fill:
        img = Image.alpha_composite(img, _solid(big, fill, outer))
    if outline:
        ring = ImageChops.subtract(outer, _mask(big, r, width * SS))
        img = Image.alpha_composite(img, _solid(big, outline, ring))
    return _down(img, size)


def pill(size, fill=None, outline=None):
    return rounded(size, min(size) / 2, fill, outline)


def circle(d, fill):
    return rounded((d, d), d / 2, fill)


def bar(w, h, fill):
    """Waveform bar with round ends."""
    return rounded((w, h), min(w, h) / 2, fill)


@lru_cache(maxsize=64)
def tail(side, fill, outline=None, up=False, inner=0):
    """Visible (outer) half of a `side` px square rotated 45 deg, corner radius 3/14 of the side, outline on both visible
    edges. Image is (tw, tw//2 + inner): `inner` extra rows of the inner half sit on the card side (to cover its border); the
    diamond centre line is at row `inner` (down) / height - inner (up)."""
    tw = math.ceil(side * math.sqrt(2)) + 2
    tw += tw % 2
    big = tw * SS
    sq = rounded((side * SS, side * SS), side * SS * 3 / 14, fill, outline, 1 * SS)  # already at 4x: not downscaled again
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    canvas.paste(sq, ((big - side * SS) // 2, (big - side * SS) // 2))
    canvas = canvas.rotate(45, Image.BICUBIC)
    e = inner * SS
    half = canvas.crop((0, 0, big, big // 2 + e)) if up else canvas.crop((0, big // 2 - e, big, big))
    return _down(half, (tw, tw // 2 + inner))


@lru_cache(maxsize=32)
def shadow(size, radius, blur, offset, alpha):
    """Soft drop shadow of a rounded rect. blur is the CSS blur radius (gaussian sigma = blur / 2). The image is padded by
    p = ceil(blur) + offset on every side: paste it at (card_x - p, card_y - p). Inside-the-card pixels are NOT cleared."""
    w, h = size
    p = math.ceil(blur) + offset
    layer = Image.new("RGBA", (w + 2 * p, h + 2 * p), (0, 0, 0, 0))
    layer.paste(rounded(size, radius, ("#000000", alpha)), (p, p + offset))
    return layer.filter(ImageFilter.GaussianBlur(blur / 2))


def shadow_pad(blur, offset):
    return math.ceil(blur) + offset


@lru_cache(maxsize=32)
def chevron(size, fill, width=1.6):
    """Down chevron (two round-capped strokes) filling `size` (w, h) px; stroke `width` px."""
    w, h = size
    big, sw = (w * SS, h * SS), width * SS
    m = Image.new("L", big, 0)
    d, pad = ImageDraw.Draw(m), sw / 2 + SS * 0.5
    pts = [(pad, pad), (big[0] / 2, big[1] - pad), (big[0] - pad, pad)]
    d.line(pts, fill=255, width=round(sw), joint="curve")
    for x, y in pts:
        d.ellipse((x - sw / 2, y - sw / 2, x + sw / 2, y + sw / 2), fill=255)
    return _down(_solid(big, fill, m), size)


@lru_cache(maxsize=256)
def toggle(on, size, colours):
    """Pill switch. on: 0..1 (0.5 = mid-slide). colours: (track_off, track_on, knob_off, knob_on) as "#rrggbb"."""
    from .theme import blend
    w, h = size
    t_off, t_on, k_off, k_on = colours
    img = pill(size, blend(t_on, t_off, on)).copy()
    pad = max(1, round(h / 8))
    d = h - 2 * pad
    knob = circle(d, blend(k_on, k_off, on))
    img.alpha_composite(knob, (round(pad + on * (w - h)), pad))
    return img


def _truetype(name, px, axes=None):
    """Windows font file at px, optionally with variable-font axes; OSError if missing."""
    f = ImageFont.truetype(FONT_DIR + name, px)
    if axes:
        f.set_variation_by_axes(axes)
    return f


@lru_cache(maxsize=64)
def font(kind, px):
    """PIL font for kind ui|semi|mono at px pixels: Segoe UI Variable Text (variable weight) -> Segoe UI; Cascadia Mono -> Consolas.
    Falls back to Pillow's default face when no Windows fonts exist (Linux)."""
    if kind == "mono":
        tries = (("CascadiaMono.ttf", [400]), ("consola.ttf", None))
    else:
        wght = 600 if kind == "semi" else 400
        tries = (("SegUIVar.ttf", [wght, min(36, max(5, px * 0.75))]), ("seguisb.ttf" if kind == "semi" else "segoeui.ttf", None))
    for name, axes in tries:
        try:
            return _truetype(name, px, axes)
        except OSError:
            continue
    return ImageFont.load_default(px)
