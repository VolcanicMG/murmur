"""python -m evals.build [--history PATH]: LOCAL cases from your real history -> evals/cases/local/history.jsonl (gitignored, personal text).
python -m evals.build --recordings [DIR]: append every kept recording (~/.murmur/recordings, [audio] keep_recordings) that has no case
yet to evals/cases/local/recordings.jsonl as an `audio` case whose `expected` is the draft transcript murmur heard, and copies
the wav into evals/cases/local/audio (keep_recordings prunes its folder; the suite must keep its takes). Correct `expected` to
what you actually said (and add checks); until then the case only guards against regressions."""
import argparse, json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import seal  # noqa: E402  (history and recordings are sealed with DPAPI on Windows)

HERE = Path(__file__).parent
CANDIDATES = [Path.home() / ".murmur/history.jsonl", Path("/mnt/c/Users/ethan/.murmur/history.jsonl")]
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
    ap.add_argument("--recordings", nargs="?", const="", help="add audio cases from kept recordings (default folder ~/.murmur/recordings)")
    a = ap.parse_args()
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
        rows = [json.loads(seal.open_line(l)) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    except seal.SealedError as e:
        raise SystemExit(f"{src}: {e}")
    cases = [c for c in (history_case(i, r) for i, r in enumerate(rows)) if c]
    out = HERE / "cases/local/history.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"{len(cases)} cases from {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
