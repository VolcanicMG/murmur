"""python -m evals.compare A.json B.json: per-kind and per-case pass-rate deltas (B vs A), regressions first."""
import json, sys


def rates(path):
    d = json.load(open(path, encoding="utf-8"))
    by = {}
    for r in d["runs"]:
        by.setdefault((r["kind"], r["id"]), []).append(r["pass"])
    return d["meta"], {k: sum(v) / len(v) for k, v in by.items()}, {k: next(r["output"] for r in d["runs"] if (r["kind"], r["id"]) == k) for k in by}


def main():
    (ma, a, _), (mb, b, ob) = rates(sys.argv[1]), rates(sys.argv[2])
    print(f"A: {ma['label']} {ma['llm']}\nB: {mb['label']} {mb['llm']}\n")
    common = a.keys() & b.keys()
    for kind in sorted({k for k, _ in common}):
        ids = [k for k in common if k[0] == kind]
        x, y = sum(a[k] for k in ids) / len(ids), sum(b[k] for k in ids) / len(ids)
        print(f"{kind:<10} {x:6.0%} -> {y:6.0%}  ({y - x:+.0%})  [{len(ids)} cases]")
    diffs = sorted((b[k] - a[k], k) for k in common if b[k] != a[k])
    print("\nregressions:")
    for d, k in [x for x in diffs if x[0] < 0] or [(0, None)]:
        print("  none" if k is None else f"  {k[1]} [{k[0]}] {a[k]:.0%} -> {b[k]:.0%}  B out={ob[k]!r}")
    print("improvements:")
    for d, k in reversed([x for x in diffs if x[0] > 0]) or [(0, None)]:
        print("  none" if k is None else f"  {k[1]} [{k[0]}] {a[k]:.0%} -> {b[k]:.0%}")
    print(f"\nonly in A: {len(a.keys() - b.keys())}  only in B: {len(b.keys() - a.keys())}")


if __name__ == "__main__":
    main()
