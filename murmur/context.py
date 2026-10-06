"""Per-recording context for prompt building."""
import logging
from . import brain, win

log = logging.getLogger("murmur")


def gather(cfg):
    """Context dict: {app, title, tone}. Each provider adds keys; providers must never raise."""
    app, title = win.active_app()
    return {"app": app, "title": title, "tone": brain.tone_for(app, cfg)}


def read_field(cfg, on_rect=None, text=True):
    """Text around the caret in the focused control: {before, after, selected} or {} on any failure.

    Uses UI Automation (TextPattern, then ValuePattern with the caret treated as end-of-field).
    Run it in a daemon thread with a short join: UIA can hang on some apps. on_rect((l, t, r, b)) gets the
    control's screen rect as soon as it is known (before the slower text read) so the card can anchor early.
    text=False reads the rect only (terminal apps: scrollback must not reach prompts).
    """
    c = cfg.get("context", {})
    if not on_rect and not (text and c.get("enabled", True)):
        return {}
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            ctl = auto.GetFocusedControl()
            _emit_rect(auto, ctl, on_rect)
            if not (text and c.get("enabled", True)):
                del ctl
                return {}
            out = _from_text_pattern(auto, ctl) or _from_value_pattern(auto, ctl)
            del ctl  # no COM object may outlive the initializer
            n, m = c.get("before_chars", 600), c.get("after_chars", 200)
            return {"before": out[0][-n:], "after": out[1][:m], "selected": out[2]} if out else {}
    except Exception as e:
        log.debug("read_field failed: %s", e)
        return {}


def _emit_rect(auto, ctl, on_rect):
    """Report where the card should sit: the control's left/right, and (when the control has a TextPattern) the
    caret line's top/bottom, since a terminal or editor pane is far taller than the line being typed. Geometry only."""
    if not on_rect:
        return
    try:
        r = ctl.BoundingRectangle
        if not (r.right > r.left and r.bottom > r.top):
            return
        line = _caret_line(auto, ctl)
        on_rect((r.left, line[0], r.right, line[1]) if line else (r.left, r.top, r.right, r.bottom))
    except Exception as e:
        log.debug("bounding rect failed: %s", e)


def _caret_line(auto, ctl):
    """(top, bottom) of the line containing the caret, or None."""
    tp = ctl.GetPattern(auto.PatternId.TextPattern)
    sels = tp.GetSelection() if tp else None
    if not sels:
        return None
    rng = sels[0].Clone()
    rects = rng.GetBoundingRectangles()  # a collapsed caret may still report its own rect
    rng.ExpandToEnclosingUnit(auto.TextUnit.Line, waitTime=0)
    rects = rng.GetBoundingRectangles() or rects
    rects = [x for x in rects if x.bottom > x.top]
    return (rects[0].top, rects[0].bottom) if rects else None


def _from_text_pattern(auto, ctl):
    tp = ctl.GetPattern(auto.PatternId.TextPattern)
    sels = tp.GetSelection() if tp else None
    if not sels:
        return None
    # ponytail: reads whole document text then slices; fine for chat/editors, slow on huge docs
    doc, sel = tp.DocumentRange, sels[0]
    ends = auto.TextPatternRangeEndpoint
    before, after = doc.Clone(), doc.Clone()
    before.MoveEndpointByRange(ends.End, sel, ends.Start, waitTime=0)  # default waitTime sleeps 0.5 s
    after.MoveEndpointByRange(ends.Start, sel, ends.End, waitTime=0)
    out = before.GetText(-1), after.GetText(-1), sel.GetText(-1)
    del tp, sels, doc, sel, before, after
    return out


def _from_value_pattern(auto, ctl):
    vp = ctl.GetPattern(auto.PatternId.ValuePattern)
    out = (vp.Value or "", "", "") if vp else None
    del vp
    return out
