"""Per-recording context for prompt building."""
import logging, threading, time
from . import brain, win
from .card import pick_rect

log = logging.getLogger("murmur")


def gather(cfg):
    """Context dict: {app, title, tone}. Each provider adds keys; providers must never raise."""
    app, title = win.active_app()
    return {"app": app, "title": title, "tone": brain.tone_for(app, cfg)}


def read_field(cfg, on_rect=None, text=True):
    """Text around the caret in the focused control: {before, after, selected, sel_known} or {} on any failure.

    Uses UI Automation (TextPattern, then ValuePattern with the caret treated as end-of-field). sel_known is True only
    when a TextPattern answered, so an empty `selected` is then authoritative ("nothing selected"); False means UIA
    could not tell and a clipboard fallback is the only way to look (callers must not use it merely because this
    read has not finished). Run it in a daemon thread with a short join: UIA can hang on some apps.
    on_rect((l, t, r, b), kind) gets the card anchor (kind "caret" | "field" | "pane", see card.pick_rect): from the
    cheap line rect before the text read, else from the slower providers after it. text=False reads the rect only
    (terminal apps: scrollback must not reach prompts).
    """
    c = cfg.get("context", {})
    want_text = text and c.get("enabled", True)
    if not on_rect and not want_text:
        return {}
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            return _read(auto, c, on_rect, want_text)
    except Exception as e:
        log.debug("read_field failed: %s", e)
        return {}


def _read(auto, c, on_rect, want_text):
    ctl = auto.GetFocusedControl()
    rect = _control_rect(ctl) if on_rect else None
    sel = _selection_range(auto, ctl) if rect else None
    anchored = bool(rect) and _anchor(on_rect, [lambda: _line_rect(auto, sel)], rect)
    out = (_from_text_pattern(auto, ctl) or _from_value_pattern(auto, ctl)) if want_text else None
    if rect and not anchored:
        _anchor(on_rect, [lambda: _point_rect(auto, sel), lambda: _caret2_rect(auto, ctl), _msaa_caret, win.caret_rect], rect, force=True)
    meta = {"control_name": _control_name(ctl)}
    del ctl, sel  # no COM object may outlive the initializer
    n, m = c.get("before_chars", 600), c.get("after_chars", 200)
    return {**meta, "before": out[0][-n:], "after": out[1][:m], "selected": out[2], "sel_known": out[3]} if out else meta


def _control_name(ctl):
    """Name, class and automation id of the focused control (metadata only; used to spot AI chat inputs)."""
    try:
        return " ".join(str(x) for x in (ctl.Name, ctl.ClassName, ctl.AutomationId) if x)
    except Exception:
        return ""


def _control_rect(ctl):
    """(l, t, r, b) of the focused control, or None if it has no usable rect."""
    try:
        r = ctl.BoundingRectangle
        return (r.left, r.top, r.right, r.bottom) if r.right > r.left and r.bottom > r.top else None
    except Exception as e:
        log.debug("bounding rect failed: %s", e)
        return None


def _anchor(on_rect, providers, ctl_rect, force=False):
    """Emit the card anchor from the first sane caret provider (control left/right, caret top/bottom). With force, also
    emit the control-rect fallback. True if a caret rect was found."""
    rect, kind = pick_rect(providers, ctl_rect)
    if kind == "caret":
        on_rect((ctl_rect[0], rect[1], ctl_rect[2], rect[3]), kind)
    elif force:
        on_rect(rect, kind)
    return kind == "caret"


def _selection_range(auto, ctl):
    """Clone of the TextPattern selection (caret) range, or None."""
    tp = ctl.GetPattern(auto.PatternId.TextPattern)
    sels = tp.GetSelection() if tp else None
    return sels[0].Clone() if sels else None


def _first_rect(rng):
    rects = [x for x in rng.GetBoundingRectangles() if x.bottom > x.top]
    return (rects[0].left, rects[0].top, rects[0].right, rects[0].bottom) if rects else None


def _line_rect(auto, sel):
    """Rect of the line containing the caret."""
    if sel is None:
        return None
    rng = sel.Clone()
    rng.ExpandToEnclosingUnit(auto.TextUnit.Line, waitTime=0)
    return _first_rect(rng)


def _point_rect(auto, sel):
    """The collapsed caret's own rect (some Chromium builds), else the rect of the next character."""
    if sel is None:
        return None
    r = _first_rect(sel.Clone())
    if r:
        return r
    rng = sel.Clone()
    rng.MoveEndpointByUnit(auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character, 1, waitTime=0)
    return _first_rect(rng)


def _caret2_rect(auto, ctl):
    """Rect from TextPattern2.GetCaretRange when the control supports it."""
    tp2 = ctl.GetPattern(auto.PatternId.TextPattern2)
    if not tp2:
        return None
    _, rng = tp2.pattern.GetCaretRange()
    return _first_rect(auto.TextRange(textRange=rng)) if rng else None


def _msaa_caret():
    """MSAA caret (OBJID_CARET) of the focused window: Chromium/Electron expose it once an accessibility client is attached."""
    import ctypes, comtypes.client
    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen import Accessibility
    hwnd = win.focus_hwnd()
    if not hwnd:
        return None
    acc = ctypes.POINTER(Accessibility.IAccessible)()
    ctypes.oledll.oleacc.AccessibleObjectFromWindow(hwnd, 0xFFFFFFF8, ctypes.byref(Accessibility.IAccessible._iid_), ctypes.byref(acc))
    l, t, w, h = acc.accLocation(0)
    del acc
    return (l, t, l + w, t + h)


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
    out = before.GetText(-1), after.GetText(-1), sel.GetText(-1), True
    del tp, sels, doc, sel, before, after
    return out


def _from_value_pattern(auto, ctl):
    vp = ctl.GetPattern(auto.PatternId.ValuePattern)
    out = (vp.Value or "", "", "", False) if vp else None
    del vp
    return out


def _norm(t):
    return t.replace("\r\n", "\n").replace("\r", "\n")


def _utf16_len(t):
    return len(t.encode("utf-16-le")) // 2


def _selection_reads(tp, expect, wait=0.25):
    """Poll up to `wait` s for the selection to read back as `expect` (Chromium applies Select asynchronously)."""
    end = time.monotonic() + wait
    while True:
        back = tp.GetSelection()
        if back and _norm(back[0].GetText(-1)) == _norm(expect):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def _select_back(env, n, expect):
    """Extend a clone of the collapsed caret `orig` back n characters, select it, True if it reads back as `expect`.
    None without touching the selection if the caller already gave up (`abort` set). env = (auto, tp, orig, abort)."""
    auto, tp, orig, abort = env
    rng = orig.Clone()
    rng.MoveEndpointByUnit(auto.TextPatternRangeEndpoint.Start, auto.TextUnit.Character, -n, waitTime=0)
    if abort.is_set():
        return None
    rng.Select(waitTime=0)
    return _selection_reads(tp, expect)


def _resolve_timeout(state, abort, lock):
    """Caller side after the join timed out: tell the worker to give up, then take the final word under the same lock
    (a worker that already reached "ok" keeps its selection; otherwise the worker restores the caret itself)."""
    abort.set()
    with lock:
        return "ok" if state[0] == "ok" else "restored"


def _try_select(auto, expect, state, abort=None, lock=None):
    """Worker body: state[0] becomes "ok" | "restored" (a Select was tried and undone) | "unknown" (no TextPattern).
    If `abort` is set (the caller timed out) it never selects, or puts the caret back itself if it already had."""
    abort, lock = abort or threading.Event(), lock or threading.Lock()
    ctl = auto.GetFocusedControl()
    tp = ctl.GetPattern(auto.PatternId.TextPattern)
    sels = tp.GetSelection() if tp else None
    if not sels:
        return
    orig = sels[0].Clone()  # the collapsed caret, to put back if verification fails
    state[0] = "restored"
    touched, result = False, "restored"
    for n in dict.fromkeys([_utf16_len(expect), len(expect)]):  # UIA Character units are UTF-16 in most providers
        got = _select_back((auto, tp, orig, abort), n, expect)
        touched = touched or got is not None
        if got:
            result = "ok"
            break
    with lock:  # decided together with the caller's timeout path
        if result == "ok" and abort.is_set():
            result = "restored"
        if result != "ok" and touched:
            orig.Select(waitTime=0)
        state[0] = result
    del tp, sels, orig, ctl


def select_before_caret(expect, timeout=1.5):
    """Select the text `expect` that sits right before the collapsed caret, via UIA, and verify it reads back (CRLF/CR
    normalised). Returns "ok"; "restored" (a UIA select was attempted and failed or timed out: the caret was put back, do
    NOT fall back to keys); or "unknown" (no TextPattern at all: nothing was touched). Own thread with a join cap."""
    state, abort, lock = ["unknown"], threading.Event(), threading.Lock()

    def run():
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                _try_select(auto, expect, state, abort, lock)
        except Exception as e:
            log.debug("select_before_caret failed: %s", e)
    t = threading.Thread(target=run, daemon=True)
    t.start(); t.join(timeout)
    if t.is_alive():
        return _resolve_timeout(state, abort, lock)  # a hang is never "unknown": no key fallback
    return state[0]


def _collapse(auto):
    """Worker body: collapse the focused control's selection to its end. True if it had a TextPattern selection."""
    tp = auto.GetFocusedControl().GetPattern(auto.PatternId.TextPattern)
    sels = tp.GetSelection() if tp else None
    if not sels:
        return False
    ends, rng = auto.TextPatternRangeEndpoint, sels[0].Clone()
    rng.MoveEndpointByRange(ends.Start, rng, ends.End, waitTime=0)
    rng.Select(waitTime=0)
    return True


def collapse_selection(timeout=1.0):
    """Collapse the current selection to its end (undo of select_before_caret when nothing was pasted). True on success."""
    done = []

    def run():
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                done.append(_collapse(auto))
        except Exception as e:
            log.debug("collapse_selection failed: %s", e)
    t = threading.Thread(target=run, daemon=True)
    t.start(); t.join(timeout)
    return bool(done and done[0])
