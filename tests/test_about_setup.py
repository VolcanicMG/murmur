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
        self.assertEqual(builtin.status_line({}, ll(True, None)), (builtin.NO_MODEL, "error", False))
        self.assertTrue(builtin.NO_MODEL.startswith("No cleanup model yet"))
        self.assertFalse(builtin.status_line({}, ll(False, None))[2])

    def test_fmt_progress_line_and_eta(self):
        f = builtin.fmt_progress
        self.assertEqual(f(2_600_000_000, 6_200_000_000, 18e6), "41% \u00b7 2.6 of 6.2 GB \u00b7 18 MB/s \u00b7 about 3 min left")
        self.assertEqual(f(3_000_000_000, 6_000_000_000), "50% \u00b7 3.0 of 6.0 GB")  # no rate yet: no speed, no ETA
        self.assertEqual(f(5_990_000_000, 6_000_000_000, 5e6), "99% \u00b7 6.0 of 6.0 GB \u00b7 5.0 MB/s \u00b7 less than a minute left")
        self.assertIn("about 2 h 3 min left", f(0, 6_000_000_000, 6_000_000_000 / (123 * 60)))
        self.assertIn("800 KB/s", f(1, 6_000_000_000, 8e5))
        self.assertEqual(f(6_000_000_000, 6_000_000_000, 1e7), "Checking the download\u2026")

    def test_rate_is_a_moving_average(self):
        r = builtin._Rate(window=6)
        self.assertIsNone(r.add(0, now=0)); self.assertIsNone(r.add(10, now=0.5))  # under a second of samples: unknown
        self.assertEqual(r.add(10_000_000, now=2), 5_000_000)
        self.assertAlmostEqual(r.add(40_000_000, now=12), (40_000_000 - 10_000_000) / 10, delta=1)  # the sample at t=0 aged out

    def test_needs_model_only_when_nothing_can_clean(self):
        none = SimpleNamespace(enabled=lambda c: c.get("builtin", {}).get("enabled", True), find_model=lambda c: None)
        have = SimpleNamespace(enabled=none.enabled, find_model=lambda c: "m.gguf")
        lms, nolms = (lambda: "lms.exe"), (lambda: None)
        base = {"llm": {"provider": "builtin"}}
        n = lambda cfg, find=nolms, ll=none: builtin.needs_model(cfg, find, ll)
        self.assertTrue(n(base))
        self.assertFalse(n(base, ll=have))
        self.assertFalse(n({"llm": {"provider": "openai"}}))
        self.assertFalse(n({**base, "builtin": {"enabled": False}}))
        fb = lambda tbl, on=True: {"llm": {"provider": "builtin", "fallback": on}, "llm_fallback": tbl}
        lm_local = {"provider": "openai", "url": "http://localhost:1234/v1/chat/completions"}
        self.assertTrue(n(fb(lm_local), nolms))  # fallback on, but LM Studio is not installed
        self.assertFalse(n(fb(lm_local), lms))
        self.assertTrue(n(fb(lm_local, on=False), lms))  # fallback off: LM Studio is not used
        self.assertFalse(n(fb({"provider": "anthropic", "url": "https://api.anthropic.com", "model": "h"}), nolms))  # remote = usable

    def test_launch_prompt_decision(self):
        none = SimpleNamespace(enabled=lambda c: True, find_model=lambda c: None)
        have = SimpleNamespace(enabled=lambda c: True, find_model=lambda c: "m")
        cfg, nolms = {"llm": {"provider": "builtin"}}, (lambda: None)
        sp = lambda state, wiz=False, ll=none: builtin.should_prompt(cfg, state, wiz, nolms, ll)
        self.assertTrue(sp({}))
        self.assertFalse(sp({"model_prompt_off": True}))  # "Don't ask again"
        self.assertFalse(sp({}, wiz=True))  # the wizard covers the first run
        self.assertFalse(sp({}, ll=have))  # nothing to prompt for

    def test_facts_rows_use_the_real_models_folder(self):
        with tempfile.TemporaryDirectory() as t:
            ll = SimpleNamespace(models_dir=lambda: Path(t) / "models", MODEL_SIZE=6_217_260_864)
            rows = dict(builtin.facts(ll, lambda p: SimpleNamespace(free=2_100_000_000)))
        self.assertEqual((rows["Size"], rows["License"], rows["Model"]), ("6.2 GB", "Apache-2.0", "Gemma 4 E4B (Q6_K)"))
        self.assertEqual(rows["Saved to"], str(Path(t) / "models"))
        self.assertTrue(rows["Free space"].startswith("2.1 GB on"))
        self.assertIn("lmstudio-community", rows["From"])

    def test_resume_note(self):
        ll = lambda have: SimpleNamespace(part_bytes=lambda: have, MODEL_SIZE=1000)
        self.assertEqual(builtin.resume_note(ll(420)), "Resumes from 42%")
        self.assertEqual(builtin.resume_note(ll(0)), "")

    def test_warm_up_clears_the_backoff_and_always_resets_the_flag(self):
        from unittest import mock
        from murmur import store
        ll = SimpleNamespace(_failures=3, _retry_at=999.0, start=mock.Mock(side_effect=RuntimeError("boom")))
        builtin.warm_up({}, ll)
        self.assertEqual((ll._failures, ll._retry_at), (0, 0.0))
        self.assertFalse(store.runtime["llm_starting"])


class _Dir:
    def __init__(self, present):
        self.present = present

    def __truediv__(self, name):
        return SimpleNamespace(is_file=lambda: self.present)


if __name__ == "__main__":
    unittest.main()
