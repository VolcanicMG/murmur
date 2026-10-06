import sys, tempfile, tomllib, unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import store


class T(unittest.TestCase):
    def setUp(self):
        store.HOME = Path(tempfile.mkdtemp())
        store.ensure()

    def test_stats(self):
        now = datetime(2026, 10, 10)
        rows = [{"ts": "2026-10-09T10:00:00", "final": "one two three four"},
                {"ts": "2026-09-01T10:00:00", "final": "a b"}, {"final": "x"}]
        s = store.stats(rows, now)
        self.assertEqual((s["dictations"], s["words"], s["week_words"]), (3, 7, 4))
        self.assertAlmostEqual(s["minutes_saved"], 7 / 40 - 7 / 150)
        self.assertEqual(store.stats([])["words"], 0)

    def test_snippets_round_trip(self):
        sn = {"sig": 'Best,\n"Ethan"\\o/', "emoji": "hi \U0001F600 \u2014 tab\t", "multi word": "x"}
        store.save_snippets(sn)
        self.assertEqual(store.load_snippets(), sn)

    def test_dump_toml_default_config(self):
        d = tomllib.loads(store.DEFAULT_CONFIG)
        self.assertEqual(tomllib.loads(store.dump_toml(d)), d)

    def test_save_tones_keeps_rest(self):
        store.save_tones({"casual": "Tone: casual.", "tweet": "Tone: tweet."})
        c = store.load_config()
        self.assertEqual(c["tones"], {"casual": "Tone: casual.", "tweet": "Tone: tweet."})
        self.assertEqual(c["hotkeys"]["dictate"], "right ctrl")

    def test_save_tones_refuses_nested(self):
        path = store.HOME / "config.toml"
        path.write_text(store.DEFAULT_CONFIG + '\n[apps.extra]\nx = "y"\n', encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            store.save_tones({"casual": "c"})
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_save_tones_refuses_dates(self):
        (store.HOME / "config.toml").write_text(store.DEFAULT_CONFIG + "\nwhen = 2026-10-05\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            store.save_tones({"casual": "c"})

    def test_add_terms_newline(self):
        (store.HOME / "dictionary.txt").write_text("# c\nFoo", encoding="utf-8")
        store.add_terms(["Bar"])
        self.assertEqual(store.load_dictionary(), ["Foo", "Bar"])

    def test_dictionary_and_history_edits(self):
        store.add_terms(["Foo", "Bar"]); store.remove_term("Foo")
        self.assertEqual(store.load_dictionary(), ["Bar"])
        self.assertIn("# one preferred", (store.HOME / "dictionary.txt").read_text())
        store.append_history({"ts": "1", "final": "a"}); store.append_history({"ts": "2", "final": "b"})
        store.delete_row("1", "a"); store.delete_row("2", "WRONG")
        self.assertEqual([h["ts"] for h in store.read_history()], ["2"])


if __name__ == "__main__":
    unittest.main()
