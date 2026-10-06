"""Replayable evaluation of the murmur cleanup pipeline: python -m evals.run [--cases core|local|all|PATH] [--n 3] ...
Runs the REAL brain.py functions (apply_scratch -> clean_auto -> fit, plausible, command_intent) against case files.
No win32; works on Linux. --no-llm runs only the deterministic kinds."""
import argparse, json, re, statistics, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from murmur import brain, llm, store

HERE = Path(__file__).parent
LLM_KINDS = {"dictate"}


def load_cases(spec):
    """Case dicts from 'core' | 'local' | 'all' | a .jsonl path."""
    paths = {"core": [HERE / "cases/core.jsonl"], "local": sorted((HERE / "cases/local").glob("*.jsonl"))}
    paths["all"] = paths["core"] + paths["local"]
    out = []
    for p in paths.get(spec) or [Path(spec)]:
        out += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return out


def norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower().replace("'", "")).split())


def extra_words(raw, out):
    """Words in `out` that cannot be derived from `raw` (numbers normalised, fillers/spoken punctuation ignored)."""
    have = set(brain._words(raw, raw=True))
    return [w for w in brain._words(out) if w not in have]


def first_alpha(s):
    return next((c for c in s if c.isalpha()), "")


def check(name, arg, case, out):
    """True/False for one declarative check against the output string."""
    low = out.lower()
    if name == "equals": return out == arg
    if name == "equals_norm": return norm(out) == norm(arg)
    if name == "contains": return all(any(a.lower() in low for a in x.split("|")) for x in arg)  # "one|1" = either
    if name == "contains_exact": return all(x in out for x in arg)
    if name == "not_contains": return not any(x.lower() in low for x in arg)
    if name == "starts_upper": return first_alpha(out).isupper() == arg
    if name == "starts_lower": return first_alpha(out).islower() == arg
    if name == "ends_with": return out.rstrip().endswith(arg)
    if name == "max_extra_words": return len(extra_words(case["raw"], out)) <= arg
    if name == "is_question": return out.rstrip().endswith("?") == arg
    if name == "empty": return (not out.strip()) == arg
    if name == "lines": return len([l for l in out.splitlines() if l.strip()]) == arg
    if name == "min_lines": return len([l for l in out.splitlines() if l.strip()]) >= arg
    if name == "regex": return re.search(arg, out) is not None
    if name == "plausible": return arg == case["_plausible"]
    if name == "guard_ok": return arg == case.get("_guard_ok", True)  # the plausibility guard accepted the LLM output (no fallback to raw)
    if name == "contains_content_words_of_expected":  # >= 80% of the expected output's content words appear
        want = [w for w in brain._words(case.get("expected", "")) if len(w) > 3 and w not in brain.COMMON]
        got = set(brain._words(out))
        return not arg or not want or sum(w in got for w in want) / len(want) >= 0.8
    raise ValueError(f"unknown check {name!r} in case {case['id']}")


def make_ctx(case, cfg):
    ctx = {"cfg": cfg, "tone": case.get("tone", "neutral")}
    for k in ("before", "after", "terms", "selected"):
        if case.get(k):
            ctx[k] = case[k]
    return ctx


def run_case(case, cfg):
    """(output, info) for one execution. info: guard decisions + LLM calls [{s}]."""
    kind, raw, info = case["kind"], case["raw"], {"calls": []}
    if kind == "scratch":
        return brain.apply_scratch(raw), info
    if kind == "fit":
        return brain.fit(raw, case.get("before", ""), case.get("tone")), info
    if kind == "intent":
        k, arg = brain.command_intent(raw, case.get("tone"), bool(case.get("selected")))
        return k if arg is None or k == "rewrite" else f"{k}:{arg}", info
    if kind == "guard":
        case["_plausible"] = info["plausible"] = brain.plausible(raw, case["output"])
        return case["output"], info
    # dictate: mirror Session.finish
    if brain.spoken_command(raw):
        info["command"] = brain.spoken_command(raw)
        return "", info
    ctx = make_ctx(case, cfg)
    w, info["hard"] = brain.prepare(raw, ctx, list(ctx.get("terms", [])) + store.load_dictionary())
    if not w:
        return "", info
    orig, t_calls = llm.chat, info["calls"]

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            t_calls.append(round(time.perf_counter() - t, 3))
    llm.chat = timed
    try:
        text, ok = brain.clean_auto(w, ctx)
        info["guard_ok"] = case["_guard_ok"] = ok
        info["fallback_raw"] = not ok
        info["retry"] = ok and len(t_calls) > len(brain.split_chunks(w))
        return brain.fit(text, ctx.get("before", ""), ctx["tone"]), info
    except Exception as e:  # session falls back to the scratched raw on LLM failure
        info["error"] = repr(e)[:200]
        return w, info
    finally:
        llm.chat = orig


def warm_builtin(cfg):
    """Start the bundled model and wait (<= ~60 s) for one real reply so startup isn't recorded as failures."""
    from murmur import localllm
    try:
        localllm.start(cfg)
    except Exception as e:
        print("builtin start:", e)
    end = time.time() + 60
    while time.time() < end:
        try:
            llm.chat(cfg, "Reply with one word.", "ping")
            return
        except llm.Starting:
            time.sleep(1)
        except Exception as e:
            print("builtin warm-up:", e); return


def run_once(c, cfg, i):
    """One execution of one case as a result row."""
    out, info = run_case(c, cfg)
    res = {k: check(k, v, c, out) for k, v in c.get("checks", {}).items()}
    return {"id": c["id"], "kind": c["kind"], "run": i, "output": out, "checks": res, "pass": all(res.values()) and "error" not in info, **info}


def evaluate(cases, cfg, n):
    return [run_once(c, cfg, i) for c in cases for i in range(n if c["kind"] in LLM_KINDS else 1)]


def pct(x):
    return f"{100 * x:.0f}%"


def kind_lines(runs, by_case):
    """The per-kind pass table plus the ALL row."""
    by_kind = {}
    for r in runs:
        by_kind.setdefault(r["kind"], []).append(r["pass"])
    lines = ["kind        cases  runs  pass"]
    for k, v in sorted(by_kind.items()):
        lines.append(f"{k:<10} {len({r['id'] for r in runs if r['kind'] == k}):>6} {len(v):>5}  {pct(sum(v) / len(v))}")
    allp = [r["pass"] for r in runs]
    lines.append(f"{'ALL':<10} {len(by_case):>6} {len(allp):>5}  {pct(sum(allp) / max(1, len(allp)))}")
    return lines


def latency_line(calls):
    p95 = calls[min(len(calls) - 1, int(.95 * len(calls)))]
    return f"\nlatency per LLM call: median {statistics.median(calls):.2f}s  p95 {p95:.2f}s  ({len(calls)} calls)"


def guard_line(llm_runs):
    rejected, retried = sum(r.get("fallback_raw", False) for r in llm_runs), sum(r.get("retry", False) for r in llm_runs)
    return f"guard: rejected->raw {rejected}  retry used {retried}  llm errors {sum('error' in r for r in llm_runs)}  (of {len(llm_runs)} runs)"


def llm_lines(runs):
    """Latency per LLM call and the guard statistics."""
    calls = sorted(t for r in runs for t in r["calls"])
    llm_runs = [r for r in runs if r["kind"] in LLM_KINDS]
    return ([latency_line(calls)] if calls else []) + ([guard_line(llm_runs)] if llm_runs else [])


def failing_line(rs):
    bad = next(r for r in rs if not r["pass"])
    return (f"- {rs[0]['id']} [{rs[0]['kind']}] {sum(r['pass'] for r in rs)}/{len(rs)} "
            f"fail={[k for k, v in bad['checks'].items() if not v] or bad.get('error')} out={bad['output']!r}")


def summarize(runs, cases):
    by_case = {}
    for r in runs:
        by_case.setdefault(r["id"], []).append(r)
    lines = kind_lines(runs, by_case) + llm_lines(runs)
    worst = sorted((rs for rs in by_case.values() if not all(r["pass"] for r in rs)), key=lambda rs: sum(r["pass"] for r in rs) / len(rs))
    if worst:
        lines += ["\nfailing cases (pass/runs, failed checks, output of a failing run):"] + [failing_line(rs) for rs in worst]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(prog="evals.run", description="Replay eval cases through the murmur cleanup pipeline")
    ap.add_argument("--cases", default="core", help="core | local | all | path to a .jsonl")
    ap.add_argument("--n", type=int, default=3, help="runs per LLM case")
    ap.add_argument("--model"); ap.add_argument("--provider"); ap.add_argument("--url")
    ap.add_argument("--prompt-file", help="use this file as prompt.md for the run")
    ap.add_argument("--label", default="run")
    ap.add_argument("--filter", help="kind=dictate or id~substring")
    ap.add_argument("--real-home", action="store_true", help="use ~/.murmur (dictionary, style, prompt, history) instead of a fresh temp HOME")
    ap.add_argument("--no-llm", action="store_true", help="deterministic kinds only (no LM Studio needed)")
    ap.add_argument("--min-pass", type=float, help="exit 1 if the overall pass rate (0-1) is below this")
    a = ap.parse_args()

    if not a.real_home:
        store.HOME = Path(tempfile.mkdtemp(prefix="murmur-eval-"))
    store.ensure()
    if a.prompt_file:
        (store.HOME / "prompt.md").write_text(Path(a.prompt_file).read_text(encoding="utf-8"), encoding="utf-8")
    cfg = store.load_config()
    if not a.real_home:  # stable baseline whatever the shipped default provider is: LM Studio + gemma unless overridden
        cfg["llm"].update(provider="openai", url="http://localhost:1234/v1/chat/completions", model="google/gemma-4-e4b")
    for k in ("model", "provider", "url"):
        if getattr(a, k):
            cfg["llm"][k] = getattr(a, k)
    cfg["llm"]["fallback"] = False  # never leak eval text to a remote fallback
    cfg["llm"]["timeout"] = max(cfg["llm"].get("timeout", 15), 60)

    if cfg["llm"].get("provider") == "builtin" and not a.no_llm:
        warm_builtin(cfg)
    cases = load_cases(a.cases)
    if a.no_llm:
        cases = [c for c in cases if c["kind"] not in LLM_KINDS]
    if a.filter:
        key, sep, val = re.split(r"([=~])", a.filter, maxsplit=1)
        cases = [c for c in cases if (str(c.get(key)) == val if sep == "=" else val in str(c.get(key)))]
    if not cases:
        sys.exit("no cases selected")
    t0 = time.time()
    runs = evaluate(cases, cfg, a.n)
    text = summarize(runs, cases)
    print(text)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / f"{stamp}-{a.label}"
    meta = {"label": a.label, "ts": stamp, "cases": a.cases, "n": a.n, "no_llm": a.no_llm, "real_home": a.real_home, "seconds": round(time.time() - t0, 1),
            "llm": {k: cfg["llm"].get(k) for k in ("provider", "model", "url")}, "prompt_file": a.prompt_file}
    out.with_suffix(".json").write_text(json.dumps({"meta": meta, "runs": runs}, indent=1, ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".md").write_text(f"# eval {a.label} ({stamp})\n\n```\n{text}\n```\n\n`{json.dumps(meta)}`\n", encoding="utf-8")
    print(f"\nwrote {out}.json / .md")
    rate = sum(r["pass"] for r in runs) / len(runs)
    sys.exit(1 if a.min_pass is not None and rate < a.min_pass else 0)


if __name__ == "__main__":
    main()
