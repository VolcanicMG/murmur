"""Eval runner for kind=command cases: mirrors Session._finish_command (intent -> rewrite_checked) with the real LLM."""
import time
from murmur import brain, llm


def run_command_case(case, cfg):
    """(output, info). Output = what would be pasted over the selection; a rejected edit leaves the selection untouched
    (info rejected=True, `guard_ok` check false); a non-rewrite intent returns '' with info intent."""
    raw, selected, info = case["raw"], case.get("selected", ""), {"calls": []}
    kind, arg = brain.command_intent(raw, case.get("tone"), bool(selected.strip()))
    if kind != "rewrite":
        info["intent"] = kind
        return "", info
    ctx = {"cfg": cfg, "tone": case.get("tone", "neutral"), "mode": "command"}
    orig = llm.chat

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            info["calls"].append(round(time.perf_counter() - t, 3))
    llm.chat = timed
    try:
        out, ok = brain.rewrite_checked(selected or None, raw, ctx, "selection" if selected.strip() else "new")
        info["guard_ok"] = case["_guard_ok"] = ok
        info["fallback_raw"] = not ok  # counted as "rejected" by the guard stats line
        info["retry"] = ok and len(info["calls"]) > 1
        return (out if ok else selected), info
    except Exception as e:  # Session leaves the selection alone on LLM failure
        info["error"] = repr(e)[:200]
        return selected, info
    finally:
        llm.chat = orig
