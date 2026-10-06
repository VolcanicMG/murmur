"""Adapt to the user without being asked: learn from hand edits to pasted text, and refresh style.md every N dictations.
All of it is off in private mode and for password fields (callers never register those). No win32 at import time."""
import logging, threading, time
from collections import namedtuple

from . import brain, store

log = logging.getLogger("murmur")
WINDOW_S, DELAY_S, MAX_PENDING = 30 * 60, 45, 5
_lock = threading.Lock()
_pending = []  # [{app, title, text, ts, final, t}] newest last


Paste = namedtuple("Paste", "app title text ts final t", defaults=(None,))  # ts = the history row's timestamp; t = paste time (default now)


def remember(paste):
    """Remember a dictate paste (a Paste) so a later hand edit of it can be noticed (at most 5 pending, 30 min each)."""
    with _lock:
        _pending.append({**paste._asdict(), "text": paste.text.strip(), "t": paste.t or time.time()})
        del _pending[:-MAX_PENDING]


def check(app, field_text, now=None, pending=None):
    """Pure core: for every pending paste in `app` still inside the 30 min window, look for a hand edit in `field_text`.
    Returns the hits [(entry, edited span)]; those entries are removed, expired ones dropped, the rest stay pending.
    `pending` (a list) is mutated in place; default = the module's own."""
    now = now or time.time()
    pend = _pending if pending is None else pending
    hits = []
    for e in list(pend):
        if now - e["t"] > WINDOW_S:
            pend.remove(e)
        elif e["app"] == app and (edited := brain.detect_edit(e["text"], field_text)):
            pend.remove(e); hits.append((e, edited))
    return hits


def apply(hits, cfg):
    """Learn from the hits: new dictionary terms, and mark the history row corrected (feeds the corrections few-shot)."""
    learned = []
    for e, edited in hits:
        terms, corrected = brain.learn_from_edit(e["text"], edited)
        if terms:
            learned += store.add_terms(terms)
        if corrected:
            store.correct_last(e["ts"], e["final"], edited)
    if learned:
        log.info("auto-learn from your edits: %s", learned)
    return learned


def check_and_apply(app, field_text, cfg, now=None):
    """Session-start hook (a): use the field text already read. Gated by [learning] auto_edits."""
    if not store.learning(cfg, "auto_edits") or store.is_private(cfg) or not _pending:
        return []
    with _lock:
        hits = check(app, field_text, now)
    return apply(hits, cfg) if hits else []


def schedule(cfg, app, busy, read_text, delay=DELAY_S):
    """Hook (b): one check `delay` s after a paste if the same app is still in front and no session is live. `busy()` -> bool
    (a session is running), `read_text(cfg) -> (app, field text)` is a read-only UIA read on its own thread. Returns the Timer."""
    def run():
        try:
            if busy() or not store.learning(cfg, "auto_edits") or store.is_private(cfg) or app in store.no_copy(cfg):
                return  # no_copy apps are terminals: their scrollback is never read
            cur, text = read_text(cfg)
            if cur == app and text:
                check_and_apply(app, text, cfg)
        except Exception:
            log.exception("delayed edit check failed")
    t = threading.Timer(delay, run); t.daemon = True; t.start()
    return t


# ---- style refresh


def count_dictation():
    """One more dictation since the last style refresh (state.json)."""
    store.update_state(lambda st: st.__setitem__("since_style", st.get("since_style", 0) + 1))


def style_due(cfg, dictate_rows):
    """Is a refresh due? [learning] style_auto, >= style_every dictations since the last one, >= 30 dictate rows in total."""
    every = max(1, cfg.get("learning", {}).get("style_every", 50))
    return (store.learning(cfg, "style_auto") and not store.is_private(cfg)
            and store.load_state().get("since_style", 0) >= every and dictate_rows >= 30)


def refresh_style(cfg):
    """Rebuild style.md from recent history on this thread. On failure the old style.md stays; either way the counter
    resets, so the next try is after another style_every dictations."""
    try:
        text = brain.learn_style(store.read_history(150), cfg)
        if text.strip():
            (store.HOME / "style.md").write_text(text.strip() + "\n", encoding="utf-8")
            log.info("style.md refreshed from recent dictations")
    except Exception as e:
        log.info("style refresh failed, keeping the old style.md: %s", e)
    store.update_state(lambda st: st.__setitem__("since_style", 0))


_style_lock = threading.Lock()


def maybe_refresh_style(cfg, busy):
    """Start a background refresh when due and no session is live (call at session end; it just no-ops until idle)."""
    if busy() or not _style_lock.acquire(blocking=False):
        return None
    rows = sum(1 for r in store.read_history() if r.get("mode", "dictate") == "dictate")
    if not style_due(cfg, rows):
        _style_lock.release()
        return None

    def run():
        try:
            refresh_style(cfg)
        finally:
            _style_lock.release()
    t = threading.Thread(target=run, daemon=True); t.start()
    return t
