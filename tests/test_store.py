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

    def test_stats_modes_and_default_mode(self):
        rows = [{"ts": "2026-10-09T10:00:00", "final": "one two"}, {"ts": "2026-10-09T10:01:00", "mode": "command", "raw": "make it formal",
                "selection": "yo", "final": "Hello there my friend"}]
        s = store.stats(rows, datetime(2026, 10, 10))
        self.assertEqual((s["dictations"], s["words"], s["week_words"], s["commands"]), (1, 2, 2, 1))
        for r in rows:
            store.append_history(r)
        self.assertEqual([h["mode"] for h in store.read_history()], ["dictate", "command"])
        self.assertEqual(store.read_history()[1]["selection"], "yo")

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

    def test_save_config_round_trip_and_refusal(self):
        cfg = store.merged_config(); cfg["llm"]["model"] = "m"
        store.save_config(cfg)
        self.assertEqual(store.load_config(), cfg)
        before = (store.HOME / "config.toml").read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            store.save_config({**cfg, "apps": {"x": {"nested": "y"}}})
        self.assertEqual((store.HOME / "config.toml").read_text(encoding="utf-8"), before)

    def test_merged_config_fills_defaults(self):
        m = store.merged_config({"llm": {"model": "x"}, "apps": {"a.exe": "code"}})
        self.assertEqual((m["llm"]["model"], m["llm"]["timeout"], m["hotkeys"]["dictate"]), ("x", 15, "right ctrl"))
        self.assertEqual(m["apps"], {"a.exe": "code"})
        m = store.merged_config({"hotkeys": {"dictate": "right ctrl"}})  # a config predating learn_word still shows it
        self.assertEqual(m["hotkeys"]["learn_word"], "ctrl+alt+d")

    def test_form_sound_volume_and_fallback(self):
        base = store.merged_config()
        cfg = store.form_to_cfg({"ui.sound_volume": "0.5", "ui.sounds": True, "llm.fallback": True, "llm_fallback.timeout": "9"}, base)
        self.assertEqual((cfg["ui"]["sound_volume"], cfg["llm"]["fallback"], cfg["llm_fallback"]["timeout"]), (0.5, True, 9))
        with self.assertRaises(ValueError):
            store.form_to_cfg({"ui.sound_volume": "7"}, base)
        self.assertEqual(base["llm_fallback"]["model"], "claude-haiku-4-5-20251001")

    def test_learn_subcommand_skips_the_single_instance_check(self):  # D4
        src = (Path(__file__).resolve().parent.parent / "murmur" / "__main__.py").read_text(encoding="utf-8")
        self.assertLess(src.index('a.cmd == "learn"'), src.index("single_instance()"))

    def test_resolve_device(self):
        devs = [{"name": "Speakers", "max_input_channels": 0}, {"name": "Microphone (USB Audio)", "max_input_channels": 1},
                {"name": "Mic Array", "max_input_channels": 2}]
        self.assertEqual(store.resolve_device("Mic Array", devs), 2)
        self.assertEqual(store.resolve_device("usb", devs), 1)
        self.assertIsNone(store.resolve_device("Speakers", devs))  # output only
        self.assertIsNone(store.resolve_device("gone", devs)); self.assertIsNone(store.resolve_device("", devs))

    def test_form_to_cfg(self):
        base = store.merged_config()
        vals = {"hotkeys.dictate": " f9 ", "llm.timeout": "20", "llm.live_clean": False, "apps": {"a.exe": "code"}, "no_copy": ["wt.exe"]}
        c = store.form_to_cfg(vals, base)
        self.assertEqual((c["hotkeys"]["dictate"], c["llm"]["timeout"], c["llm"]["live_clean"]), ("f9", 20, False))
        self.assertEqual(c["apps"], {"a.exe": "code", "no_copy": ["wt.exe"]})
        self.assertEqual(base["hotkeys"]["dictate"], "right ctrl")  # base untouched
        for bad in ({"llm.timeout": "x"}, {"context.before_chars": "-1"}, {"hotkeys.fix_last": " "}, {"llm.url": "ftp://x"}):
            with self.assertRaises(ValueError):
                store.form_to_cfg(bad, base)

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
