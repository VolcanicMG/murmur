"""python -m evals.build [--history PATH]: LOCAL cases from your real history -> evals/cases/local/history.jsonl (gitignored, personal text).
python -m evals.build --recordings [DIR]: append every kept recording (~/.murmur/recordings, [audio] keep_recordings) that has no case
yet to evals/cases/local/recordings.jsonl as an `audio` case whose `expected` is the draft transcript murmur heard, and copies
the wav into evals/cases/local/audio (keep_recordings prunes its folder; the suite must keep its takes). Correct `expected` to
what you actually said (and add checks); until then the case only guards against regressions.
python -m evals.build --claude-code [DIR ...]: murmur never reads terminals, so for dictations pasted into one ([apps] no_copy)
the message actually sent is looked up in Claude Code transcripts (default ~/.claude/projects, WSL's too) and the outcome is
written back into history (outcome_source "claude-code") with the same learning rule as the live watch."""
import argparse, bisect, json, shutil, sys, tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import autolearn, brain, seal, store  # noqa: E402  (history and recordings are sealed with DPAPI on Windows)

HERE = Path(__file__).parent
CANDIDATES = [Path.home() / ".murmur/history.jsonl", Path("/mnt/c/Users/ethan/.murmur/history.jsonl")]
CC_CANDIDATES = [Path.home() / ".claude/projects", Path(r"\\wsl.localhost\Ubuntu-24.04\home\ethan\.claude\projects"),
                 Path("/home/ethan/.claude/projects")]
CC_BEFORE, CC_AFTER = timedelta(seconds=5), timedelta(minutes=10)  # a message sent this close to a paste can carry it
REC_CANDIDATES = [Path.home() / ".murmur/recordings", Path("/mnt/c/Users/ethan/.murmur/recordings")]


def draft_case(w):
    """A draft audio case for one kept recording: expected = the transcript murmur heard (its .txt sidecar)."""
    txt = w.with_suffix(".txt")
    heard = seal.unseal(txt.read_bytes()).decode("utf-8").strip() if txt.exists() else ""
    return {"id": f"rec-{w.stem}", "kind": "audio", "wav": w.name, "tone": "casual", "expected": heard,
            "checks": {"max_wer": 0.15}, "reviewed": False, "note": "draft: expected = what murmur heard; correct it"}


def recording_cases(rec_dir, have):
    """Draft audio cases for recordings not in `have` (wav names already cased)."""
    return [draft_case(w) for w in sorted(Path(rec_dir).glob("*.wav")) if w.name not in have]


def add_recordings(rec_dir):
    out = HERE / "cases/local/recordings.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    have = {json.loads(l)["wav"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()} if out.exists() else set()
    new = recording_cases(rec_dir, have)
    (keep := HERE / "cases/local/audio").mkdir(exist_ok=True)
    for c in new:
        shutil.copy2(Path(rec_dir) / c["wav"], keep / c["wav"])  # still sealed: run.py unseals it on use
    with open(out, "a", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in new)
    print(f"{len(new)} new audio cases ({len(have)} already there) -> {out}")


def history_case(i, r):
    """One eval case from a history row, or None for rows that aren't reviewable dictations. expected = what the user sent
    (`sent` of an edited paste, never a rewrite), else corrected, else final."""
    if r.get("mode", "dictate") != "dictate" or not r.get("raw") or not (r.get("corrected") or r.get("final")):
        return None
    sent = None if r.get("rewrite") else r.get("sent")
    kinds = ",".join(sorted({e["kind"] for e in r.get("edits", [])}))
    note = (f"from history; EDITED by the user ({kinds}), expected = what was sent" if sent
            else "from history; expected = corrected or final (unreviewed)")
    return {"id": f"hist-{i:04d}", "kind": "dictate", "raw": r["raw"], "tone": r.get("tone", "neutral"), "app": r.get("app", ""),
            "expected": sent or r.get("corrected") or r["final"],
            "checks": {"max_extra_words": 3, "contains_content_words_of_expected": True}, "reviewed": False, "note": note}


def human_text(obj):
    """What a person typed in one Claude Code transcript line, or None (tool results, sidechains, meta/system lines)."""
    if obj.get("type") != "user" or obj.get("isSidechain") or obj.get("isMeta"):
        return None
    c = (obj.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list) or any(not isinstance(p, dict) or p.get("type") == "tool_result" for p in c):
        return None
    return "\n".join(p.get("text", "") for p in c if p.get("type") == "text") or None


def human_messages(dirs):
    """[(UTC datetime, text)] of every human message in the *.jsonl transcripts under `dirs`, oldest first."""
    out = []
    for d in dirs:
        for f in Path(d).rglob("*.jsonl"):
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    o = json.loads(line)
                    if (t := human_text(o)) and o.get("timestamp"):
                        out.append((datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")), t))
                except (ValueError, AttributeError, TypeError):
                    continue
    return sorted(out, key=lambda m: m[0])


def match_row(row, msgs, times=None):
    """classify_edit result for THE first human message in [ts - 5 s, ts + 10 min] when it carries this row's paste (found,
    at least half its words matched), else None; a later message is never tried (a loose match there poisons `corrected`).
    History ts is local naive time, transcript times UTC. `times` = the msgs' times."""
    t = datetime.fromisoformat(row["ts"]).astimezone(timezone.utc)
    times = times or [m[0] for m in msgs]
    i = bisect.bisect_left(times, t - CC_BEFORE)
    if i == len(msgs) or msgs[i][0] > t + CC_AFTER:
        return None
    res = brain.classify_edit(row["final"], msgs[i][1])
    return res if res["outcome"] != "missing" and not res["rewrite"] else None


def strong(res):
    """May a Claude Code match teach? >= 80% of the pasted words found and a span of 0.5x-1.5x the paste's word count."""
    return res["matched"] >= 0.8 * res["words"] and 0.5 * res["words"] <= res["span_words"] <= 1.5 * res["words"]


def claude_code(src, dirs):
    """Write outcomes for terminal dictations (no_copy apps, no outcome yet) matched in Claude Code transcripts.
    Returns {outcome or "unmatched": count}."""
    store.HOME = src.parent  # history, outcomes and dictionary next to this history
    cfg = src.parent / "config.toml"
    no_copy = store.no_copy(tomllib.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {})
    for l in src.read_text(encoding="utf-8").splitlines():
        try:
            seal.open_line(l)  # sealed and no DPAPI here: SealedError before anything is written
        except ValueError:
            pass  # torn line: the store skips it too
    rows = store.read_history()  # with the outcomes recorded since the last rewrite
    msgs, counts = human_messages(dirs), {}
    times = [m[0] for m in msgs]
    for r in rows:
        if r["mode"] != "dictate" or r.get("app") not in no_copy or "outcome" in r or not r.get("final") or not r.get("ts"):
            continue
        res = match_row(r, msgs, times)
        if res:
            autolearn.record({"ts": r["ts"], "final": r["final"], "text": r["final"].strip()}, res, "claude-code", learn=strong(res))
        key = res["outcome"] if res else "unmatched"
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", help="history.jsonl (default ~/.murmur, or the Windows one from WSL)")
    ap.add_argument("--recordings", nargs="?", const="", help="add audio cases from kept recordings (default folder ~/.murmur/recordings)")
    ap.add_argument("--claude-code", nargs="*", metavar="DIR", help="outcomes of terminal dictations from Claude Code transcripts (default ~/.claude/projects)")
    a = ap.parse_args()
    if a.claude_code is not None:
        src = Path(a.history) if a.history else next((p for p in CANDIDATES if p.exists()), None)
        dirs = [Path(d) for d in a.claude_code] or [p for p in CC_CANDIDATES if p.exists()]
        if not src or not dirs:
            raise SystemExit("no Claude Code projects folder found" if src else "no history.jsonl found")
        try:
            counts = claude_code(src, dirs)
        except seal.SealedError as e:
            raise SystemExit(f"{src}: {e}")
        print("claude-code outcomes: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "nothing to match"))
        return
    if a.recordings is not None:
        rec = Path(a.recordings) if a.recordings else next((p for p in REC_CANDIDATES if p.exists()), None)
        if not rec:
            raise SystemExit("no recordings folder found")
        try:
            return add_recordings(rec)
        except seal.SealedError as e:
            raise SystemExit(str(e))
    src = Path(a.history) if a.history else next((p for p in CANDIDATES if p.exists()), None)
    if not src:
        raise SystemExit("no history.jsonl found")
    try:
        [seal.open_line(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]  # sealed on Linux: stop here
    except seal.SealedError as e:
        raise SystemExit(f"{src}: {e}")
    store.HOME = src.parent
    rows = store.read_history()  # with the outcomes still waiting in outcomes.jsonl merged in
    cases = [c for c in (history_case(i, r) for i, r in enumerate(rows)) if c]
    out = HERE / "cases/local/history.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"{len(cases)} cases from {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
