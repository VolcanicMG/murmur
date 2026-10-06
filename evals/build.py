"""python -m evals.build [--history PATH]: LOCAL cases from your real history -> evals/cases/local/history.jsonl (gitignored, personal text)."""
import argparse, json
from pathlib import Path

HERE = Path(__file__).parent
CANDIDATES = [Path.home() / ".murmur/history.jsonl", Path("/mnt/c/Users/ethan/.murmur/history.jsonl")]


def history_case(i, r):
    """One eval case from a history row, or None for rows that aren't reviewable dictations."""
    if r.get("mode", "dictate") != "dictate" or not r.get("raw") or not (r.get("corrected") or r.get("final")):
        return None
    return {"id": f"hist-{i:04d}", "kind": "dictate", "raw": r["raw"], "tone": r.get("tone", "neutral"), "app": r.get("app", ""),
            "expected": r.get("corrected") or r["final"],
            "checks": {"max_extra_words": 3, "contains_content_words_of_expected": True}, "reviewed": False,
            "note": "from history; expected = corrected or final (unreviewed)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", help="history.jsonl (default ~/.murmur, or the Windows one from WSL)")
    a = ap.parse_args()
    src = Path(a.history) if a.history else next((p for p in CANDIDATES if p.exists()), None)
    if not src:
        raise SystemExit("no history.jsonl found")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = [c for c in (history_case(i, r) for i, r in enumerate(rows)) if c]
    out = HERE / "cases/local/history.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"{len(cases)} cases from {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
