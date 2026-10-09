import json, sys, tempfile, unittest
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import seal, store


def _fake_protect(d):
    return b"X" + bytes(b ^ 0x5A for b in d)


def _fake_unprotect(b):
    if not b.startswith(b"X"):
        raise RuntimeError("bad blob")
    return bytes(c ^ 0x5A for c in b[1:])


class Fake(unittest.TestCase):
    """DPAPI swapped for a reversible xor so the sealed paths run on Linux."""

    def setUp(self):
        self.addCleanup(setattr, store, "HOME", store.HOME)
        store.HOME = Path(tempfile.mkdtemp())
        store.ensure()
        for name, fn in (("available", lambda: True), ("_protect", _fake_protect), ("_unprotect", _fake_unprotect)):
            p = mock.patch.object(seal, name, fn); p.start(); self.addCleanup(p.stop)
        seal._open_sealed.cache_clear()
        self.hist = store.HOME / "history.jsonl"

    def test_seal_round_trip_and_plain_passthrough(self):
        s = seal.seal(b"secret words")
        self.assertTrue(s.startswith(seal.MAGIC)); self.assertNotIn(b"secret", s)
        self.assertEqual(seal.unseal(s), b"secret words")
        self.assertEqual(seal.seal(s), s)  # already sealed
        self.assertEqual(seal.unseal(b"RIFF plain"), b"RIFF plain")
        with self.assertRaises(ValueError):
            seal.unseal(seal.MAGIC + b"garbage")

    def test_history_sealed_on_disk_and_mixed_reads(self):
        self.hist.write_text(json.dumps({"ts": "a", "final": "old plain"}) + "\n", encoding="utf-8")
        store.append_history({"ts": "b", "final": "hunter two"})
        text = self.hist.read_text(encoding="utf-8")
        self.assertNotIn("hunter", text)
        self.assertTrue(text.splitlines()[1].startswith("!"))
        with open(self.hist, "a", encoding="utf-8") as f:
            f.write("!torn-line\n")
        self.assertEqual([r["final"] for r in store.read_history()], ["old plain", "hunter two"])

    def test_rewrites_stay_sealed(self):
        for i in range(3):
            store.append_history({"ts": str(i), "final": f"secret {i}"})
        store.correct_last("1", "secret 1", "edited secret")
        store.delete_row("0", "secret 0")
        store.strip_history_keys(["nothing"])
        lines = self.hist.read_text(encoding="utf-8").splitlines()
        self.assertTrue(all(l.startswith("!") for l in lines))
        self.assertEqual([r.get("corrected", r["final"]) for r in store.read_history()], ["edited secret", "secret 2"])

    def test_unreadable_sealed_lines_survive_rewrites(self):
        store.append_history({"ts": "1", "final": "mine"})
        with open(self.hist, "a", encoding="utf-8") as f:
            f.write("!not-openable-here\n")  # DPAPI refuses it (another login, a torn write)
        store.append_history({"ts": "2", "final": "also mine"})
        store.delete_row("1", "mine"); store.strip_history_keys(["x"]); store.purge_history(1)
        self.assertIn("!not-openable-here", self.hist.read_text(encoding="utf-8"))  # never silently dropped
        self.assertEqual(store.clear_history(), 1)  # Delete all is the one rewrite that removes it
        self.assertEqual(self.hist.read_text(encoding="utf-8"), "")

    def test_outcomes_append_merge_and_fold(self):  # set_outcome never rewrites history.jsonl
        out = store.HOME / store.OUTCOMES
        for ts in ("1", "2"):
            store.append_history({"ts": ts, "final": "f"})
        before = self.hist.read_text(encoding="utf-8")
        store.set_outcome("1", "f", {"outcome": "edited", "sent": "secret fix"})
        store.set_outcome("1", "f", {"outcome": "updated"})  # later records win
        store.set_outcome("9", "gone", {"outcome": "unchanged"})  # no such row: ignored
        with open(out, "a", encoding="utf-8") as f:
            f.write("{torn\n!not-openable-here\n")
        self.assertEqual(self.hist.read_text(encoding="utf-8"), before)
        self.assertNotIn("secret", out.read_text(encoding="utf-8"))  # sealed like history
        a, b = store.read_history()
        self.assertEqual((a["outcome"], a["sent"]), ("updated", "secret fix"))
        self.assertNotIn("outcome", b)
        store.correct_last("2", "f", "F")  # any full rewrite folds the records in
        self.assertEqual(out.read_text(encoding="utf-8"), "!not-openable-here\n")  # only the unopenable line is kept
        self.assertEqual([r.get("outcome") for r in store.read_history()], ["updated", None])
        store.set_outcome("2", "f", {"outcome": "unchanged"})
        store.clear_history()
        self.assertFalse(out.exists())  # Delete all leaves no outcome data

    def test_seal_existing_seals_plain_outcomes(self):
        store.append_history({"ts": "1", "final": "f"})
        (store.HOME / store.OUTCOMES).write_text(json.dumps({"ts": "1", "final": "f", "fields": {"outcome": "edited"}}) + "\n", encoding="utf-8")
        store.seal_existing()
        self.assertFalse((store.HOME / store.OUTCOMES).exists())  # folded into the sealed history
        self.assertEqual(store.read_history()[0]["outcome"], "edited")

    def test_seal_existing_migrates_once(self):
        self.hist.write_text("".join(json.dumps({"ts": str(i), "final": "plain"}) + "\n" for i in range(2)), encoding="utf-8")
        rec = store.HOME / "recordings"; rec.mkdir()
        (rec / "1.wav").write_bytes(b"RIFFdata"); (rec / "1.txt").write_bytes(b"heard words\n")
        extra = Path(tempfile.mkdtemp()); (extra / "2.wav").write_bytes(b"RIFFmore"); (extra / "notes.md").write_bytes(b"x")
        self.assertEqual(store.seal_existing([extra]), (2, 3))
        self.assertNotIn("plain", self.hist.read_text(encoding="utf-8"))
        self.assertEqual(len(store.read_history()), 2)
        self.assertTrue(seal.is_sealed((rec / "1.wav").read_bytes()))
        self.assertEqual((extra / "notes.md").read_bytes(), b"x")
        self.assertEqual(seal.unseal((extra / "2.wav").read_bytes()), b"RIFFmore")
        self.assertEqual(store.seal_existing([extra]), (0, 0))  # idempotent

    def test_seal_existing_noop_without_dpapi(self):
        self.hist.write_text(json.dumps({"final": "plain"}) + "\n", encoding="utf-8")
        with mock.patch.object(seal, "available", lambda: False):
            self.assertEqual(store.seal_existing(), (0, 0))
        self.assertIn("plain", self.hist.read_text(encoding="utf-8"))

    def test_recording_round_trip(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        store.save_recording(np.full(1600, 0.25, np.float32), "what I said", keep=3)
        wav = next((store.HOME / "recordings").glob("*.wav"))
        self.assertTrue(seal.is_sealed(wav.read_bytes()))
        self.assertTrue(seal.is_sealed(wav.with_suffix(".txt").read_bytes()))
        x, raw = store.load_recording(wav)
        self.assertEqual((len(x), raw), (1600, "what I said"))
        self.assertAlmostEqual(float(x[0]), 0.25, places=3)

    def test_clear_recordings(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        store.save_recording(np.zeros(1600, dtype="float32"), "one", 5)
        (store.HOME / "recordings" / "x.wav.tmp").write_bytes(b"left over")
        (store.HOME / "recordings" / "keep.me").write_bytes(b"not ours")
        self.assertEqual(store.clear_recordings(), 1)
        self.assertEqual([f.name for f in (store.HOME / "recordings").iterdir()], ["keep.me"])
        self.assertEqual(store.clear_recordings(), 0)

    def test_open_line_cache(self):
        line = seal.seal_line('{"a": 1}')
        calls = []
        with mock.patch.object(seal, "_unprotect", lambda b: calls.append(1) or _fake_unprotect(b)):
            for _ in range(3):
                self.assertEqual(seal.open_line(line), '{"a": 1}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(seal.open_line('{"b": 2}'), '{"b": 2}')


class NoDpapi(unittest.TestCase):
    def test_plaintext_and_clear_error(self):
        with mock.patch.object(seal, "available", lambda: False):
            self.assertEqual(seal.seal(b"x"), b"x")
            self.assertEqual(seal.seal_line("{}"), "{}")
            with self.assertRaisesRegex(seal.SealedError, "Windows venv"):
                seal.unseal(seal.MAGIC + b"blob")


@unittest.skipUnless(seal.available(), "win32crypt not installed")
class RealDpapi(unittest.TestCase):
    def test_round_trip(self):
        s = seal.seal(b"real secret")
        self.assertNotIn(b"real secret", s)
        self.assertEqual(seal.unseal(s), b"real secret")
        self.assertEqual(seal.open_line(seal.seal_line('{"x": 1}')), '{"x": 1}')


if __name__ == "__main__":
    unittest.main()
