"""Adapt to the user without being asked: watch what happens to each dictate paste (unchanged / edited / updated), learn from
misheard words, and refresh style.md every N dictations. All of it is off in private mode and for password fields (callers
never register those). No win32 at import time."""
import logging, threading, time
from collections import Counter, namedtuple

from . import brain, store

log = logging.getLogger("murmur")
WINDOW_S, MAX_PENDING = 30 * 60, 5
BASELINE_S, POLL_S, SETTLE_S, WATCH_S, HANDOFF_S = 0.3, 0.25, 0.7, 120, 2.0
_lock = threading.Lock()
_pending = []  # [{app, title, text, ts, final, t, watching?}] newest last
_watch = None  # the one live watch: {"e", "stop": Event, "got": Event, "handoff": field text from the next session's read}


Paste = namedtuple("Paste", "app title text ts final t", defaults=(None,))  # ts = the history row's timestamp; t = paste time (default now)


def remember(paste):
    """Remember a dictate paste (a Paste) so a later hand edit of it can be noticed (at most 5 pending, 30 min each).
    Returns the pending entry."""
    e = {**paste._asdict(), "text": paste.text.strip(), "t": paste.t or time.time()}
    with _lock:
        _pending.append(e)
        del _pending[:-MAX_PENDING]
    return e


def check(app, field_text, now=None, pending=None):
    """Pure core: for every pending paste in `app` still inside the 30 min window (and not under a live watch), look for a
    hand edit in `field_text`. Returns the hits [(entry, edited span)]; those entries are removed, expired ones dropped, the
    rest stay pending. `pending` (a list) is mutated in place; default = the module's own."""
    now = now or time.time()
    pend = _pending if pending is None else pending
    hits = []
    for e in list(pend):
        if now - e["t"] > WINDOW_S:
            pend.remove(e)
        elif e["app"] == app and not e.get("watching") and (edited := brain.detect_edit(e["text"], field_text)):
            pend.remove(e); hits.append((e, edited))
    return hits


def _log_outcome(ts, res):
    """One INFO line per observed paste: outcome and edit kinds, never text (murmur.log is not encrypted)."""
    kinds = Counter(x["kind"] for x in (res or {}).get("edits", []))
    log.info("outcome ts=%s %s edits=%d kinds=%s", ts, (res or {}).get("outcome", "unknown"), sum(kinds.values()),
             ",".join(f"{k}:{n}" for k, n in sorted(kinds.items())) or "-")


def record(e, res, source, learn=True):
    """Write a classify_edit result to the paste's history row (outcome, edits, added, rewrite, outcome_source; sent = the
    user's version when edited) and, with `learn`, learn by Ethan's rule (learn.learn_from_result): only misheard edits add
    dictionary terms and set `corrected`. `e` needs ts, final and text (what was pasted). Returns the new dictionary terms."""
    fields = {"outcome": res["outcome"], "edits": res["edits"], "added": res["added"], "rewrite": res["rewrite"],
              "outcome_source": source}
    if res["outcome"] == "edited":
        fields["sent"] = res["span"]
    terms, corrected = brain.learn_from_result(e["text"], res) if learn else ([], None)
    if corrected:
        fields["corrected"] = corrected
    store.set_outcome(e["ts"], e["final"], fields)
    learned = store.add_terms(terms) if terms else []
    _log_outcome(e["ts"], res)
    if learned:
        log.info("auto-learn from your edits: %d term(s)", len(learned))
    return learned


def apply(hits, cfg):
    """Hook (a) hits: classify each hand edit and record it. Returns the new dictionary terms."""
    learned = []
    for e, edited in hits:
        learned += record(e, brain.classify_edit(e["text"], edited), "watch")
    return learned


def check_and_apply(app, field_text, cfg, now=None):
    """Session-start hook (a), with the field text the new session already read: first it is the settled read a live watch
    in the same app ends on (the user stopped typing to dictate again); then pastes a watch left unknown are checked for a
    hand edit. Gated by [learning] auto_edits."""
    if not store.learning(cfg, "auto_edits") or store.is_private(cfg):
        return []
    if (w := _watch) and w["e"]["app"] == app:
        w["handoff"] = field_text; w["got"].set()
    with _lock:
        hits = check(app, field_text, now) if _pending else []
    return apply(hits, cfg) if hits else []


# ---- the live watch after a paste (pure decisions first, then the thread)


def should_read(now, last_key, last_read):
    """Read the field now? Keys were pressed since the last read and the newest is SETTLE_S old (the user paused)."""
    return last_key is not None and last_key > last_read and now - last_key >= SETTLE_S


def finish_reason(elapsed, enter, stopped, new_session, app_changed):
    """Why the watch ends now, or None: Enter (the text was sent or a line ended), replaced by a newer paste, a new session
    started, the foreground app changed, or WATCH_S passed."""
    return ("enter" if enter else "replaced" if stopped else "session" if new_session else "app" if app_changed
            else "timeout" if elapsed >= WATCH_S else None)


UNCHANGED = {"outcome": "unchanged", "rewrite": False, "added": 0, "edits": []}


def _nl(t):
    return t.replace("\r\n", "\n").replace("\r", "\n")


ANCHOR_CHARS, ANCHOR_WORDS = 20, 3


def anchored(pasted, base, newest, plo):
    """Is `newest` still the same field? Up to 20 chars of baseline just before the paste (its first 3 words when it starts
    the field) or just after it (its last 3 words when it ends the field) must still be there, on at least one side."""
    words, phi = pasted.split(), plo + len(pasted)
    left, right = base[max(0, plo - ANCHOR_CHARS):plo], base[phi:phi + ANCHOR_CHARS]
    left = left if left.strip() else " ".join(words[:ANCHOR_WORDS])
    right = right if right.strip() else " ".join(words[-ANCHOR_WORDS:])
    return left in newest or right in newest


def judge(pasted, base, newest):
    """Pure: what happened to `pasted` between the baseline field text (read just after the paste) and the newest settled
    read, or None when the paste can't be located in the baseline or `newest` lacks its anchors (another field). The changed region is what's left after the common
    prefix/suffix; entirely outside the paste = "updated", else classify_edit over the paste plus that region."""
    pasted, base, newest = _nl(pasted).strip(), _nl(base), _nl(newest)
    plo = base.rfind(pasted)
    if plo < 0 or not pasted or not anchored(pasted, base, newest, plo):
        return None
    if base == newest:
        return dict(UNCHANGED)
    phi, n = plo + len(pasted), min(len(base), len(newest))
    p = next((i for i in range(n) if base[i] != newest[i]), n)
    s = next((i for i in range(n - p) if base[-1 - i] != newest[-1 - i]), n - p)
    be, ne = len(base) - s, len(newest) - s
    if be <= plo or p >= phi:
        # ponytail: one region from the first to the last change; edits on both sides of the paste make it one overlapping region
        return {**UNCHANGED, "outcome": "updated", "added": len(newest[p:ne].split())}
    lo = min(plo, p)
    hi = max(ne, phi + len(newest) - len(base) if phi >= be else ne)
    while lo > 0 and not newest[lo - 1].isspace():
        lo -= 1
    while hi < len(newest) and not newest[hi].isspace():
        hi += 1
    res = brain.classify_edit(pasted, newest[lo:hi])
    return None if res["outcome"] == "missing" else res


def watch_result(pasted, base, newest, keyed, settled):
    """Pure: the watch's verdict. No keys at all = unchanged; keys without a settled read after them = None (unknown, never
    guessed); else judge(baseline, newest read)."""
    if not keyed:
        return dict(UNCHANGED)
    return judge(pasted, base, newest) if settled and newest is not None else None


def stop_watch():
    """End the live watch (a newer paste replaces it)."""
    if w := _watch:
        w["stop"].set()


def _allowed(cfg):
    """May the watch read or record? Checked again on every read: cfg is live (the tray's private toggle)."""
    return store.learning(cfg, "auto_edits") and not store.is_private(cfg)


def _poll(cfg, e, w, keys, busy, read_text, active_app):
    """The watch loop: poll every POLL_S, re-read the field once typing settles, until a finish reason (or "private" when
    private mode or auto_edits switched on/off mid-watch). Returns (reason, newest field text or None, its read time)."""
    start = last_read = time.monotonic()
    newest, read_t, idle_seen = None, None, False
    while True:
        time.sleep(POLL_S)
        if not _allowed(cfg):
            return "private", None, None
        now, b = time.monotonic(), busy()
        reason = finish_reason(now - start, keys["enter"], w["stop"].is_set(), idle_seen and b, active_app() != e["app"])
        idle_seen = idle_seen or not b
        if reason:
            return reason, newest, read_t
        if should_read(now, keys["last"], last_read):
            last_read = now
            cur, text = read_text(cfg, e["app"])
            if cur == e["app"] and text:
                newest, read_t = text, now


def _finish(e, w, res, cfg):
    """Release the watch and record `res` unless hook (a) took the paste meanwhile; nothing at all once private."""
    global _watch
    allowed = _allowed(cfg)
    with _lock:
        mine = e in _pending
        if mine and (res or not allowed):
            _pending.remove(e)  # recorded, or dropped for privacy (hook (a) must not learn from it either)
        e.pop("watching", None)
        if _watch is w:
            _watch = None
    if not allowed:
        return
    if res and mine:
        record(e, res, "watch")
    elif not res:
        _log_outcome(e["ts"], None)


def watch(cfg, e, busy, read_text, active_app, kb=None):
    """After a dictate paste (`e` from remember), watch the field until Enter, an app switch, a new session or WATCH_S, then
    record the outcome. Baseline: one read BASELINE_S after the paste. A passive keyboard hook (never suppresses, unhooked by
    handle) notes key times; the field is re-read once typing pauses for SETTLE_S. `busy()`: a session is running (the
    paste's own session is still finishing at first); `read_text(cfg, expect_app) -> (app, text)` reads on its own thread
    with a cap and skips other apps; `active_app() -> exe`. Never for terminals (no_copy), private mode or with [learning]
    auto_edits off, and the watch drops everything if either switches on mid-watch. Returns the thread."""
    global _watch
    if not _allowed(cfg) or e["app"] in store.no_copy(cfg):
        return None
    if kb is None:
        import keyboard as kb
    stop_watch()
    w = {"e": e, "stop": threading.Event(), "got": threading.Event(), "handoff": None}
    e["watching"], _watch = True, w

    def on_key(ev, keys):
        if ev.event_type != "down" or kb.is_modifier(ev.name):
            return
        if ev.name == "enter":  # numpad enter has the same name
            keys["enter"] = True
        else:
            keys["last"] = time.monotonic()

    def run():
        keys, handle, res = {"last": None, "enter": False}, None, None
        try:
            time.sleep(BASELINE_S)
            app, base = read_text(cfg, e["app"]) if _allowed(cfg) else ("", "")
            if app == e["app"] and base and not w["stop"].is_set():
                handle = kb.hook(lambda ev: on_key(ev, keys))
                reason, newest, read_t = _poll(cfg, e, w, keys, busy, read_text, active_app)
                if reason == "session" and w["got"].wait(HANDOFF_S):
                    newest, read_t = w["handoff"], time.monotonic()
                settled = read_t is not None and read_t >= (keys["last"] or 0)
                res = watch_result(e["text"], base, newest, keys["last"] is not None, settled)
        except Exception:
            log.exception("paste watch failed")
        finally:
            if handle is not None:
                kb.unhook(handle)
        _finish(e, w, res, cfg)
    t = threading.Thread(target=run, daemon=True); t.start()
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
