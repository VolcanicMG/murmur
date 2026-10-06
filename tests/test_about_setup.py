import sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import __version__, about, builtin, setup


class T(unittest.TestCase):
    def test_redact_drops_dictated_text(self):
        self.assertEqual(about.redact("12:00 done raw=hello secret final=Hi there"), "12:00 done raw=[redacted]")
        self.assertEqual(about.redact("12:00 final=Secret text"), "12:00 final=[redacted]")
        self.assertEqual(about.redact("plain line"), "plain line")
        for line, kept in (("learn_word: selection 'Hunter Two'", "learn_word: selection "), ("phonetic: pod stead -> Podstead", "phonetic: "),
                           ("learned word: Toung", "learned word: "), ("learned word (voice): Toung", "learned word (voice): "),
                           ("learned: ['Jon']", "learned: "), ("auto-learn from your edits: ['Jon']", "auto-learn from your edits: ")):
            self.assertEqual(about.redact("12:00:01,2 " + line), "12:00:01,2 " + kept + "[redacted]", line)

    def test_diagnostics(self):
        cfg = {"llm": {"provider": "openai", "model": "m", "url": "http://secret", "api_key_env": "KEY"}, "stt": {"model": "s"}}
        out = about.diagnostics(cfg, [f"l{i} raw=x" for i in range(80)])
        self.assertIn(f"murmur {__version__}", out)
        self.assertIn("openai / m", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("raw=x", out)
        self.assertEqual(out.count("raw=[redacted]"), 50)  # last 50 lines only

    def test_tail_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.log"
            p.write_text("\n".join(str(i) for i in range(500)), encoding="utf-8")
            self.assertEqual(about.tail_lines(p, 3), ["497", "498", "499"])
            self.assertEqual(about.tail_lines(Path(d) / "missing.log"), [])

    def test_lm_status(self):
        cfg = {"llm": {"model": "google/gemma-4-e4b"}}
        run = lambda out: (lambda *a, **k: SimpleNamespace(stdout=out))
        self.assertEqual(setup.lm_status(cfg, find=lambda: "lms.exe", run=run("google/gemma-4-e4b  4 GB")), ("ready", "google/gemma-4-e4b"))
        self.assertEqual(setup.lm_status(cfg, find=lambda: "lms.exe", run=run("other-model")), ("nomodel", "google/gemma-4-e4b"))
        self.assertEqual(setup.lm_status(cfg, find=lambda: None, run=run(""))[0], "missing")

        def boom(*a, **k):
            raise OSError("no exec")
        self.assertEqual(setup.lm_status(cfg, find=lambda: "lms.exe", run=boom)[0], "error")

    def test_lm_text_covers_every_state(self):
        for state in ("ready", "nomodel", "missing", "error"):
            self.assertTrue(all(setup._lm_text(state, "m")))

    def test_builtin_status_line(self):
        from pathlib import PurePosixPath as P
        ll = lambda runtime, model: SimpleNamespace(EXE="x", runtime_dir=lambda cfg: _Dir(runtime), find_model=lambda cfg: model)
        m = P("/m/gemma.gguf")
        self.assertEqual(builtin.status_line({}, ll(True, m))[:2], ("Runtime ready \u00b7 model: gemma.gguf", "success"))
        self.assertIn("not downloaded", builtin.status_line({}, ll(False, m))[0])
        self.assertEqual(builtin.status_line({}, ll(True, None)), ("No model found", "error", False))
        self.assertFalse(builtin.status_line({}, ll(False, None))[2])
        self.assertEqual(builtin.fmt_progress(3_000_000_000, 6_000_000_000), "Downloading model\u2026 50% (3.0 / 6.0 GB)")


class _Dir:
    def __init__(self, present):
        self.present = present

    def __truediv__(self, name):
        return SimpleNamespace(is_file=lambda: self.present)


if __name__ == "__main__":
    unittest.main()
