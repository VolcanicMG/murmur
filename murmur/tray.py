"""Tray icon image and tooltip per state (PIL). Same design language as the caption card."""
import logging, sys
from pathlib import Path
from functools import lru_cache
from PIL import Image, ImageDraw
from .theme import ERROR, SECONDARY, TEXT

log = logging.getLogger("murmur")
BUBBLE = (0xA1, 0xA6, 0xF2)  # periwinkle of packaging/icon.png
COLORS = {"idle": "#A1A6F2", "recording": ERROR, "polishing": "#FFB454", "paused": SECONDARY, "error": ERROR}
TOOLTIPS = {"idle": "murmur \u00b7 ready \u2014 hold {key} to dictate", "recording": "murmur \u00b7 listening",
            "polishing": "murmur \u00b7 polishing", "paused": "murmur \u00b7 paused", "error": "murmur \u00b7 no microphone"}
BARS = ((88, 82, 128), (128, 60, 150), (168, 82, 128))  # fallback bar (cx, top, bottom) on the 256 px canvas


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _base():
    """256 px RGBA bubble in the original periwinkle: packaging/icon.png (source or frozen), else drawn procedurally."""
    p = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "icon.png"
    try:
        im = Image.open(p).convert("RGBA").resize((256, 256), Image.LANCZOS)
        log.debug("tray icon base: %s", p)
        return im
    except OSError:
        log.warning("tray icon.png not found at %s; drawing the fallback bubble", p)
        im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((3, 22, 253, 200), radius=60, fill=BUBBLE + (255,))
        d.polygon([(50, 190), (66, 234), (112, 190)], fill=BUBBLE + (255,))
        for cx, y0, y1 in BARS:
            d.rounded_rectangle((cx - 10, y0, cx + 10, y1), radius=10, fill=(0, 0, 0, 255))
        return im


@lru_cache(maxsize=None)
def tray_image(state, size=64):
    """The product bubble recoloured for `state` (bars stay dark): paused gets a slash, error a dot top-right."""
    im, new = _base(), _rgb(COLORS[state])
    px, top = im.load(), max(BUBBLE)
    for y in range(256):
        for x in range(256):
            r, g, b, a = px[x, y]
            if a:
                t = min(1.0, max(r, g, b) / top)  # bars are black, so brightness is the bubble fraction (edge pixels blend)
                px[x, y] = (round(new[0] * t), round(new[1] * t), round(new[2] * t), a)
    d = ImageDraw.Draw(im)
    if state == "paused":
        d.line((40, 215, 216, 40), fill=(0, 0, 0, 0), width=40)
        d.line((40, 215, 216, 40), fill=_rgb(TEXT) + (255,), width=18)
    if state == "error":
        d.ellipse((176, 0, 255, 79), fill=(0, 0, 0, 0))
        d.ellipse((190, 14, 242, 66), fill=_rgb(TEXT) + (255,))
    return im.resize((size, size), Image.LANCZOS)


def tooltip(state, key, private=False):
    """Tray tooltip for `state`; private mode adds a suffix."""
    return TOOLTIPS[state].format(key=key) + (" \u00b7 private" if private else "")


def extra_items(app, open_about=None):
    """pystray MenuItems for the logic worker's tray hooks, to splice into the menu (`*tray.extra_items(app_module, cb)`).
    Each item exists only when `app` has the callable: toggle_private() (+ optional is_private()). open_about(icon, item) adds
    "About murmur". (Excluding an app lives on the card's right-click menu, where the target app is known, and in Settings > Config.)"""
    import pystray
    items = []
    toggle, is_private = (getattr(app, n, None) for n in ("toggle_private", "is_private"))
    if toggle:
        items.append(pystray.MenuItem("Private mode", lambda i, it: toggle(), checked=(lambda it: bool(is_private())) if is_private else None))
    if open_about:
        items.append(pystray.MenuItem("About murmur", open_about))
    return items
