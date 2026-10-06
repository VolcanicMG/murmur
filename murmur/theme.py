"""Shared design tokens (card, tray icon, settings). Source of truth:
C:\\Users\\ethan\\Downloads\\Dictation software interface design\\design_handoff_dictation_caption_card\\README.md
Pure module: no tk/win32 imports, so it is unit-tested on Linux.
"""
ACCENT, SUCCESS, ERROR, AMBER = "#8E93F7", "#52CC95", "#F0756A", "#FFB454"
TEXT, SECONDARY, NAME, FILL, TAIL, WHITE = "#F1F1F5", "#A8AAB8", "#E3E4EA", "#161826", "#1A1C2A", "#FFFFFF"
CMD_TEXT, ERR_TEXT, ON_ACCENT = "#B9BCFF", "#F2B8B0", "#16182A"
UI_FONTS = ("Segoe UI Variable Text", "Segoe UI")
SEMI_FONTS = ("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
MONO_FONTS = ("Cascadia Mono", "Consolas")


def _rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def blend(fg, bg, a):
    """Hex colour of fg at opacity a over bg (Tk has no alpha, so every rgba token becomes a blend)."""
    return "#%02x%02x%02x" % tuple(round(f * a + b * (1 - a)) for f, b in zip(_rgb(fg), _rgb(bg)))


def on_fill(a, fg=WHITE):
    """fg at opacity a over the card fill (hairlines, chip backgrounds, input fills)."""
    return blend(fg, FILL, a)


def rounded_points(rect, r, steps=8):
    """Polygon points of a rounded rect with exact corner radius (Tk's smooth=True shrinks it)."""
    import math
    x0, y0, x1, y1 = rect
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(steps + 1):
            a = math.radians(a0 + 90 * k / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def pick_fonts(families):
    """(ui, semibold, mono) family names available in `families`, with safe fallbacks."""
    first = lambda names, fb: next((f for f in names if f in families), fb)
    ui = first(UI_FONTS, "TkDefaultFont")
    return ui, first(SEMI_FONTS, ui), first(MONO_FONTS, "TkFixedFont")
