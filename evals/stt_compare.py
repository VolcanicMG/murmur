"""Re-run kept recordings (~/.murmur/recordings, see [audio] keep_recordings) through several speech models and print them side
by side, so the bundled model choice is made on the user's own voice, not a benchmark.

    python -m evals.stt_compare                      # default: shipped int8 v2 (English-only) vs int8 v3 vs fp32 v3, each raw and conditioned
    python -m evals.stt_compare nemo-parakeet-tdt-0.6b-v2:int8 D:/models/mine

Each model is NAME[:QUANT] (downloads to the HF cache) or a folder path. Every model is run on the take as recorded and on
audio.condition(take) (what the app feeds the model); --no-condition skips the second. The first line per take is the raw
transcript murmur produced at the time (the .txt sidecar). With --ref FILE (one line per take, in order) a word error rate is
printed per model and variant."""
import argparse, re, sys, time
from pathlib import Path

import onnx_asr

DEFAULT = ["nemo-parakeet-tdt-0.6b-v2:int8", "nemo-parakeet-tdt-0.6b-v3:int8", "nemo-parakeet-tdt-0.6b-v3"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import audio, store  # noqa: E402  (audio: the conditioning the app applies before decoding)


def load(spec):
    if Path(spec).is_dir():
        return onnx_asr.load_model(None, spec)
    name, _, quant = spec.partition(":")
    return onnx_asr.load_model(name, quantization=quant or None)


def words(t):
    return re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[-1] / max(len(r), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("models", nargs="*", default=DEFAULT)
    ap.add_argument("--dir", default=str(Path.home() / ".murmur" / "recordings"))
    ap.add_argument("--ref", help="text file with the intended line per take, oldest first")
    ap.add_argument("--no-condition", action="store_true", help="raw audio only")
    a = ap.parse_args()
    wavs = sorted(Path(a.dir).glob("*.wav"))
    if not wavs:
        sys.exit(f"no recordings in {a.dir}")
    refs = Path(a.ref).read_text(encoding="utf-8").splitlines() if a.ref else []
    models = {}
    for spec in a.models:
        t = time.time(); models[spec] = load(spec); print(f"loaded {spec} in {time.time() - t:.1f}s")
    variants = [("raw", lambda x: x)] + ([] if a.no_condition else [("cond", audio.condition)])
    outs = {f"{spec} {v}": [] for spec in models for v, _ in variants}
    for i, w in enumerate(wavs):
        take, raw = store.load_recording(w)
        print(f"\n== {w.name} ({len(take) / 16000:.1f}s)")
        if i < len(refs):
            print(f"   ref : {refs[i]}")
        print(f"   was : {raw}")
        for spec, m in models.items():
            for v, fn in variants:
                t = time.time(); hyp = str(m.recognize(fn(take))).strip(); dt = time.time() - t
                outs[f"{spec} {v}"].append(hyp)
                print(f"   {spec[:30]:30s} {v:4s}: {hyp}   [{dt:.2f}s]")
    if refs:
        print("\nword error rate vs --ref:")
        for spec, hyps in outs.items():
            n = min(len(refs), len(hyps))
            print(f"   {spec:40s} {100 * sum(wer(refs[k], hyps[k]) for k in range(n)) / max(n, 1):5.1f}%")


if __name__ == "__main__":
    main()
