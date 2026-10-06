import sys, unittest
from datetime import date, datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import capture, speech, store


def row(day, fillers=3, wpm_words=100, secs=40.0, corr=1, pauses=2, app="code.exe", raw=None, final=None, d0=date(2026, 9, 1)):
    ts = datetime.combine(d0 + timedelta(days=day), datetime.min.time()).replace(hour=10).isoformat(timespec="seconds")
    raw = raw or ("um " * fillers + "we shipped the build to staging " * 3).strip()
    return {"ts": ts, "app": app, "mode": "dictate", "raw": raw, "final": final or "We shipped the build to staging. " * 3,
            "words_raw": wpm_words, "duration_s": secs + 5, "speech_s": secs, "pauses": pauses, "fillers": fillers, "self_corrections": corr}


def rows(n, per_day=2, **kw):
    return [row(i // per_day, **kw) for i in range(n)]


class Counting(unittest.TestCase):
    def test_fillers_corrections_words(self):
        self.assertEqual(speech.count_fillers("Um so like, I uh think like it works. Like we did"), 4)
        self.assertEqual(speech.count_fillers("I like cake and a lot of likes"), 0)  # 'like' as a verb is not a filler
        self.assertEqual(speech.count_fillers("hmm. Erm, well"), 2)
        self.assertEqual(speech.count_corrections("Tuesday no wait Thursday, actually I mean Friday"), 3)
        self.assertEqual(speech.count_corrections("scratch that. strike that"), 2)
        self.assertEqual(speech.count_words("one two  three"), 3)

    def test_capture_fields_keys(self):
        vad = {"speech_s": 12.345, "pauses": 3}
        f = speech.capture_fields("um hello there actually yes", 15.0, vad)
        self.assertEqual(f, {"duration_s": 15.0, "fillers": 1, "self_corrections": 1, "words_raw": 5, "pauses": 3, "speech_s": 12.35})
        self.assertNotIn("speech_s", speech.capture_fields("hello", 2.0, {"speech_s": 0.0, "pauses": 0}))  # VAD saw nothing: omitted
        self.assertNotIn("speech_s", speech.capture_fields("hello", 2.0))


class VadSpeechTotals(unittest.TestCase):
    def test_speech_seconds_and_pauses(self):
        st, cfg = capture.vad_new(), {}
        seq = [0.002] * 5 + [0.05] * 10 + [0.002] * 8 + [0.05] * 10 + [0.002] * 3 + [0.05] * 5  # 0.8 s gap, then a 0.3 s gap
        for r in seq:
            st, _ = capture.vad_step(st, r, 0.1, cfg)
        self.assertEqual(st["pauses"], 1)  # only the >= 700 ms gap counts
        self.assertAlmostEqual(st["speech_s"], 2.5, places=1)


class Metrics(unittest.TestCase):
    def test_row_metrics(self):
        m = speech.row_metrics(row(0, fillers=4, wpm_words=120, secs=48.0, corr=2, pauses=3))
        self.assertAlmostEqual(m["wpm"], 150.0)
        self.assertAlmostEqual(m["fillers_per_100"], 3.33, places=1)
        self.assertAlmostEqual(m["corrections_per_100"], 1.67, places=1)
        self.assertAlmostEqual(m["pause_ratio"], 0.5)  # 3 pauses per 6 chunks of 20 words
        self.assertGreaterEqual(m["cleanup_distance"], 0.0)
        self.assertEqual(m["date"], date(2026, 9, 1))

    def test_wpm_falls_back_to_duration_and_old_rows_are_skipped(self):
        r = row(0, wpm_words=100, secs=50.0); del r["speech_s"]; r["duration_s"] = 40.0
        self.assertAlmostEqual(speech.row_metrics(r)["wpm"], 150.0)
        self.assertIsNone(speech.row_metrics({"ts": "2026-09-01T10:00:00", "raw": "x", "final": "x", "mode": "dictate"}))  # predates the keys
        self.assertIsNone(speech.row_metrics({**row(0), "mode": "command"}))
        self.assertIsNone(speech.row_metrics({**row(0), "words_raw": 3}))  # too short to measure
        self.assertEqual(speech.ready([{"final": "old"}] * 10 + rows(7)), (7, 50))

    def test_cleanup_distance_ignores_fillers_but_sees_rewrites(self):
        same = speech.row_metrics(row(0, raw="um we shipped the build to staging today", final="We shipped the build to staging today.", wpm_words=8))
        rewrite = speech.row_metrics(row(0, raw="we shipped the build to staging today", final="Release went out yesterday evening.", wpm_words=8))
        self.assertLess(same["cleanup_distance"], 0.05)
        self.assertGreater(rewrite["cleanup_distance"], 0.5)


class Trends(unittest.TestCase):
    def improving(self, n=60):
        return [row(i // 3, fillers=max(0, 8 - i // 8), wpm_words=100) for i in range(n)]

    def test_series_needs_three_rows_per_window(self):
        pts = speech.series(rows(10, per_day=1), "fillers")
        self.assertEqual([d for d, _ in pts][:2], [date(2026, 9, 3), date(2026, 9, 4)])  # the first two days have < 3 rows in the window
        self.assertEqual(speech.series(rows(2), "fillers"), [])
        self.assertTrue(all(isinstance(v, float) or isinstance(v, int) for _, v in pts))

    def test_baseline_needs_25_rows_and_uses_the_first_14_days(self):
        self.assertIsNone(speech.baseline(rows(24)))
        b = speech.baseline(self.improving(60))
        self.assertIsNotNone(b)
        self.assertGreater(b["fillers_per_100"], 5)  # early rows had many fillers
        late = self.improving(60) + [row(40 + i // 3, fillers=0) for i in range(30)]
        self.assertEqual(speech.baseline(late), speech.baseline(self.improving(60)))  # later rows never move the baseline

    def test_improving_trend(self):
        t = speech.trend(self.improving(), "fillers")
        self.assertTrue(t["enough"])
        self.assertEqual((t["direction"], t["better"]), ("down", True))
        self.assertLess(t["delta_pct"], -30)

    def test_worsening_trend(self):
        worse = [row(i // 3, fillers=min(12, 1 + i // 6)) for i in range(60)]
        t = speech.trend(worse, "fillers")
        self.assertEqual((t["direction"], t["better"]), ("up", False))
        self.assertGreater(t["delta_pct"], 30)

    def test_flat_trend(self):
        t = speech.trend(rows(60, per_day=3), "pace")
        self.assertEqual((t["direction"], t["delta_pct"]), ("flat", 0.0))
        self.assertIsNone(t["better"])  # pace is neither better nor worse by itself

    def test_too_few_rows(self):
        t = speech.trend(rows(10), "fillers")
        self.assertFalse(t["enough"])
        self.assertEqual(speech.summary(rows(10), ["fillers"]), ["Keep dictating: 10 of 50 dictations measured so far."])

    def test_summary_sentences(self):
        lines = speech.summary(self.improving(), ["fillers", "pace", "corrections"])
        self.assertTrue(lines[0].startswith("Fillers: ") and "per 100 words over" in lines[0] and "−" in lines[0], lines[0])
        self.assertTrue(any(l.startswith("Pace: ") for l in lines))
        self.assertTrue(lines[0].endswith("%)"))

    def test_by_app_top_five(self):
        rs = [row(i // 2, fillers=i % 4, app=f"app{i % 7}.exe") for i in range(70)]
        top = speech.by_app(rs, "fillers")
        self.assertEqual(len(top), 5)
        self.assertTrue(all(n >= 3 for _, _, n in top))
        self.assertEqual(speech.by_app(rows(2), "fillers"), [])

    def test_hint_uses_only_goal_metrics(self):
        r = row(0, fillers=3, wpm_words=142, secs=60.0, corr=0, pauses=0)
        self.assertEqual(speech.hint(r, ["fillers", "pace"]), "um ×3 · 142 wpm")
        self.assertEqual(speech.hint(r, ["pace"]), "142 wpm")
        self.assertEqual(speech.hint(r, ["corrections"]), "")  # no corrections: nothing to say
        self.assertEqual(speech.hint({"final": "old"}, ["fillers"]), "")


class Config(unittest.TestCase):
    def setUp(self):
        import tempfile
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()

    def test_coach_defaults_and_validation(self):
        m = store.merged_config({"hotkeys": {"dictate": "right ctrl"}})
        self.assertEqual((m["coach"]["goals"], m["coach"]["card_hint"]), (["fillers", "pace", "corrections"], False))
        c = store.form_to_cfg({"coach.goals": ["pauses", "clarity", "pauses"], "coach.card_hint": True}, store.merged_config())
        self.assertEqual((c["coach"]["goals"], c["coach"]["card_hint"]), (["pauses", "clarity"], True))
        with self.assertRaises(ValueError):
            store.form_to_cfg({"coach.goals": ["fillers", "volume"]}, store.merged_config())
        self.assertEqual(set(speech.VALID_GOALS), {"fillers", "pace", "corrections", "pauses", "clarity", "sentence_length"})


if __name__ == "__main__":
    unittest.main()
