import sys, tempfile, tomllib, unittest
from unittest import mock
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
        self.assertEqual(m["apps"], {"a.exe": "code", "exclude": [], "pause_fullscreen": True})
        m = store.merged_config({"hotkeys": {"dictate": "right ctrl"}})  # a config predating learn_word still shows it
        self.assertEqual(m["hotkeys"]["learn_word"], "ctrl+alt+d")

    def test_form_sound_volume_and_fallback(self):
        base = store.merged_config()
        cfg = store.form_to_cfg({"ui.sound_volume": "0.5", "ui.sounds": True, "llm.fallback": True, "llm_fallback.timeout": "9"}, base)
        self.assertEqual((cfg["ui"]["sound_volume"], cfg["llm"]["fallback"], cfg["llm_fallback"]["timeout"]), (0.5, True, 9))
        with self.assertRaises(ValueError):
            store.form_to_cfg({"ui.sound_volume": "7"}, base)
        self.assertEqual(base["llm_fallback"]["model"], "google/gemma-4-e4b")

    def test_learn_subcommand_skips_the_single_instance_check(self):  # D4
        src = (Path(__file__).resolve().parent.parent / "murmur" / "__main__.py").read_text(encoding="utf-8")
        self.assertLess(src.index('a.cmd == "learn"'), src.index("single_instance()"))

    def test_privacy_and_exclude_defaults_and_form(self):  # A3/A4
        m = store.merged_config({"hotkeys": {"dictate": "right ctrl"}, "apps": {"a.exe": "code", "no_copy": []}})
        self.assertEqual((m["privacy"]["history_days"], m["privacy"]["private"]), (0, False))
        self.assertEqual((m["apps"]["exclude"], m["apps"]["pause_fullscreen"]), ([], True))
        base = store.merged_config()
        c = store.form_to_cfg({"privacy.history_days": "30", "privacy.private": True, "exclude": ["Game.exe", " "],
                               "apps.pause_fullscreen": False, "apps": {}, "no_copy": []}, base)
        self.assertEqual((c["privacy"]["history_days"], c["privacy"]["private"]), (30, True))
        self.assertEqual((c["apps"]["exclude"], c["apps"]["pause_fullscreen"]), (["game.exe"], False))
        with self.assertRaises(ValueError):
            store.form_to_cfg({"privacy.history_days": "-1"}, base)
        kept = store.form_to_cfg({"apps": {}, "no_copy": []}, {**base, "apps": {"exclude": ["x.exe"], "pause_fullscreen": False}})
        self.assertEqual((kept["apps"]["exclude"], kept["apps"]["pause_fullscreen"]), (["x.exe"], False))  # a form without those rows keeps them
        store.save_config(c)  # round-trips
        self.assertEqual(store.apps_exclude(c), ["game.exe"])
        self.assertTrue(store.is_private(c))

    def test_clear_history_and_strip_keys(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        store.append_history({"ts": "2026-10-06T10:00:00", "final": "a", "fillers": 2, "duration_s": 3.0})
        store.append_history({"ts": "2026-10-06T10:01:00", "final": "b"})
        self.assertEqual(store.strip_history_keys(("fillers", "duration_s")), 1)
        rows = store.read_history()
        self.assertEqual([r["final"] for r in rows], ["a", "b"]); self.assertNotIn("fillers", rows[0])
        self.assertEqual(store.clear_history(), 2)
        self.assertEqual(store.read_history(), [])

    def test_purge_history(self):  # A4
        now = datetime(2026, 10, 10, 12, 0, 0)
        rows = [{"ts": "2026-08-01T10:00:00", "final": "old"}, {"ts": "2026-10-09T10:00:00", "final": "new"}, {"final": "no ts"},
                {"ts": "garbage", "final": "bad ts"}]
        for r in rows:
            store.append_history(r)
        self.assertEqual(store.purge_history(0, now), 0)  # 0 = forever
        self.assertEqual(store.purge_history(30, now), 1)
        self.assertEqual([r["final"] for r in store.read_history()], ["new", "no ts", "bad ts"])
        self.assertEqual(store.purge_history(30, now), 0)
        self.assertFalse((store.HOME / "history.jsonl.tmp").exists())

    def test_find_lms(self):  # A5
        import tempfile
        home = Path(tempfile.mkdtemp())
        self.assertIsNone(store.find_lms({"USERPROFILE": str(home)}, which=lambda n: None))
        self.assertEqual(store.find_lms({"USERPROFILE": str(home)}, which=lambda n: "/usr/bin/lms"), "/usr/bin/lms")
        (home / ".lmstudio" / "bin").mkdir(parents=True)
        (home / ".lmstudio" / "bin" / "lms.exe").write_text("")
        self.assertEqual(store.find_lms({"USERPROFILE": str(home)}, which=lambda n: "/usr/bin/lms"), str(home / ".lmstudio" / "bin" / "lms.exe"))
        cfg = tomllib.loads(store.DEFAULT_CONFIG)
        self.assertEqual((cfg["llm"]["lms"], cfg["llm"]["lms_load"]), ("", ""))

    def test_lms_command_auto_and_explicit(self):  # A5
        import tempfile
        from unittest import mock
        with mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in ("numpy", "onnx_asr")}):
            from murmur import __main__ as m
        home = Path(tempfile.mkdtemp()); (home / ".lmstudio" / "bin").mkdir(parents=True)
        exe = home / ".lmstudio" / "bin" / "lms.exe"; exe.write_text("")
        with mock.patch.object(store, "find_lms", return_value=str(exe)):
            lms, load = m.lms_command({"lms": "", "lms_load": "", "model": "google/gemma-4-e4b"})
        self.assertEqual((lms, load[:3], load[-1]), (str(exe), [str(exe), "load", "google/gemma-4-e4b"], "8192"))
        lms, load = m.lms_command({"lms": "C:/x/lms.exe", "lms_load": "C:/x/lms.exe load m -y", "model": "m"})
        self.assertEqual((lms, load), ("C:/x/lms.exe", ["C:/x/lms.exe", "load", "m", "-y"]))  # explicit configs keep working
        with mock.patch.object(store, "find_lms", return_value=None):
            self.assertEqual(m.lms_command({"lms": "", "lms_load": "", "model": "m"}), (None, []))

    def test_new_config_defaults_to_the_bundled_model_with_the_fallback_off(self):
        cfg = tomllib.loads(store.DEFAULT_CONFIG)
        self.assertEqual((cfg["llm"]["provider"], cfg["llm"]["fallback"]), ("builtin", False))
        self.assertEqual((cfg["llm_fallback"]["provider"], cfg["llm_fallback"]["url"], cfg["llm_fallback"]["model"]),
                         ("openai", "http://localhost:1234/v1/chat/completions", "google/gemma-4-e4b"))
        self.assertEqual(cfg["builtin"], {"enabled": True, "backend": "vulkan", "model": "", "gpu_layers": -1, "ctx": 8192, "port": 0})
        # an existing config keeps its provider; merged defaults only add the missing tables
        m = store.merged_config({"llm": {"provider": "openai", "url": "http://localhost:1234/v1/chat/completions", "model": "m"}})
        self.assertEqual(m["llm"]["provider"], "openai")
        self.assertEqual(m["builtin"]["backend"], "vulkan")
        # R1: a config that predates provider/fallback/builtin.enabled keeps the OLD meaning, so one Settings save can't switch it
        old = store.merged_config({"hotkeys": {"dictate": "right ctrl"}, "llm": {"model": "m", "url": "http://localhost:1234/v1/chat/completions"}})
        self.assertEqual((old["llm"]["provider"], old["llm"]["fallback"], old["builtin"]["enabled"]), ("openai", False, False))
        kept = store.form_to_cfg({}, old)
        self.assertEqual((kept["llm"]["provider"], kept["llm"]["fallback"]), ("openai", False))
        fresh = store.merged_config(tomllib.loads(store.DEFAULT_CONFIG))  # a new file spells the keys out: new defaults
        self.assertEqual((fresh["llm"]["provider"], fresh["llm"]["fallback"], fresh["builtin"]["enabled"]), ("builtin", False, True))
        self.assertEqual(store.merged_config({"llm": {"provider": "builtin"}})["llm"]["provider"], "builtin")  # explicit wins

    def test_builtin_int_keys_and_url_check(self):
        base = store.merged_config()
        c = store.form_to_cfg({"builtin.gpu_layers": "-1", "builtin.ctx": "4096", "builtin.port": "0", "llm.provider": "builtin", "llm.url": ""}, base)
        self.assertEqual((c["builtin"]["gpu_layers"], c["builtin"]["ctx"], c["builtin"]["port"]), (-1, 4096, 0))
        self.assertEqual(c["llm"]["url"], "")  # builtin has no URL: not validated
        for bad in ({"builtin.ctx": "-1"}, {"builtin.port": "x"}, {"builtin.gpu_layers": "-2"}, {"llm.provider": "openai", "llm.url": ""},
                    {"llm_fallback.provider": "openai", "llm_fallback.url": "nope"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                store.form_to_cfg(bad, base)
        ok = store.form_to_cfg({"llm_fallback.provider": "builtin", "llm_fallback.url": ""}, base)
        self.assertEqual(ok["llm_fallback"]["url"], "")
        store.save_config(c)  # round-trips

    def test_learning_and_thread_defaults_and_local_only(self):  # Track B
        m = store.merged_config({"hotkeys": {"dictate": "right ctrl"}})
        self.assertEqual(m["learning"], {"auto_edits": True, "phonetic": True, "style_auto": True, "style_every": 50})
        self.assertEqual((m["context"]["thread"], m["context"]["thread_chars"]), (False, 600))
        c = store.form_to_cfg({"learning.style_every": "20", "learning.auto_edits": False, "context.thread": True, "context.thread_chars": "300"}, store.merged_config())
        self.assertEqual((c["learning"]["style_every"], c["learning"]["auto_edits"], c["context"]["thread"], c["context"]["thread_chars"]), (20, False, True, 300))
        self.assertTrue(store.learning({}, "phonetic"))  # older configs default on
        self.assertFalse(store.learning({"learning": {"phonetic": False}}, "phonetic"))
        self.assertTrue(store.local_only({"llm": {"provider": "builtin"}}))
        self.assertFalse(store.local_only({"llm": {"provider": "builtin", "fallback": True}, "llm_fallback": {"provider": "anthropic"}}))
        self.assertFalse(store.local_only({"llm": {"provider": "openai", "url": "https://api.example.com/v1"}}))

    def test_ensure_reports_a_first_run(self):
        store.HOME = Path(tempfile.mkdtemp())
        self.assertTrue(store.ensure())
        self.assertFalse(store.ensure())

    def _main(self):
        with mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in ("numpy", "onnx_asr")}):
            from murmur import __main__ as m
        return m

    def test_main_version_flag(self):
        import io
        from contextlib import redirect_stdout
        m, out = self._main(), io.StringIO()
        with mock.patch.object(sys, "argv", ["murmur", "--version"]), redirect_stdout(out):
            m.main()
        from murmur import __version__
        self.assertEqual(out.getvalue().strip(), __version__)

    def test_start_llm_launches_the_bundled_model_on_a_thread_and_skips_lm_studio(self):
        m = self._main()
        cfg = {"llm": {"provider": "builtin", "fallback": True}, "llm_fallback": {"provider": "openai"}}
        with mock.patch.object(m.threading, "Thread") as th, mock.patch.object(m.subprocess, "run") as run:
            m.start_llm(cfg)
        th.assert_called_once()
        self.assertIs(th.call_args.kwargs["target"], m.start_builtin)
        run.assert_not_called()  # no lms steps for provider builtin
        # fallback = builtin with an openai primary: both start (the bundled model in the background, LM Studio as before)
        cfg2 = {"llm": {"provider": "openai", "fallback": True, "lms": "", "lms_load": "", "model": "m"}, "llm_fallback": {"provider": "builtin"}}
        with mock.patch.object(m.threading, "Thread") as th2, mock.patch.object(store, "find_lms", return_value=None), mock.patch.object(m.brain, "_chat"):
            m.start_llm(cfg2)
        th2.assert_called_once()

    def test_start_builtin_starts_then_warms_up_once_and_always_clears_the_flag(self):
        m = self._main()
        fake = mock.MagicMock()
        fake.start.return_value = "http://127.0.0.1:5/v1"
        import murmur
        with mock.patch.dict(sys.modules, {"murmur.localllm": fake}), mock.patch.object(murmur, "localllm", fake, create=True), \
                mock.patch.object(m.brain, "_chat") as chat:
            m.start_builtin({"llm": {"provider": "builtin"}})
        fake.start.assert_called_once()
        self.assertEqual(chat.call_count, 1)  # ONE throwaway warm-up chat
        self.assertFalse(store.runtime["llm_starting"])
        fake.start.side_effect = RuntimeError("no model")
        with mock.patch.dict(sys.modules, {"murmur.localllm": fake}), mock.patch.object(murmur, "localllm", fake, create=True), \
                mock.patch.object(m.brain, "_chat") as chat2:
            m.start_builtin({"llm": {"provider": "builtin"}})  # logged, never raised
        chat2.assert_not_called()
        self.assertFalse(store.runtime["llm_starting"])

    def test_context_warning_treats_builtin_as_local(self):
        self.assertTrue(store.local_only({"llm": {"provider": "builtin", "fallback": True}, "llm_fallback": {"provider": "openai", "url": "http://localhost:1234/v1"}}))

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
        self.assertEqual(c["apps"], {"a.exe": "code", "no_copy": ["wt.exe"], "exclude": [], "pause_fullscreen": True})
        self.assertEqual(base["hotkeys"]["dictate"], "right ctrl")  # base untouched
        for bad in ({"llm.timeout": "x"}, {"context.before_chars": "-1"}, {"hotkeys.fix_last": " "}, {"llm.url": "ftp://x", "llm.provider": "openai"}):
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


@unittest.skipUnless(__import__("importlib").util.find_spec("keyboard"), "keyboard not installed")
class HotkeyValidation(unittest.TestCase):
    def test_form_rejects_a_half_typed_hotkey(self):
        self.assertTrue(store.hotkey_ok("right ctrl") and store.hotkey_ok("ctrl+alt+m"))
        self.assertFalse(store.hotkey_ok("ctrl+") or store.hotkey_ok("right c"))
        self.assertTrue(store.hotkey_ok("right ctrl", "dictate") and store.hotkey_ok("f8", "command"))
        self.assertFalse(store.hotkey_ok("ctrl+d", "dictate"))  # hooked as one key: a combo would kill every hotkey
        with self.assertRaises(ValueError):
            store._text_field("hotkeys.dictate", "ctrl+", {}, {})


@unittest.skipUnless(__import__("importlib").util.find_spec("tkinter"), "tkinter not installed")
class ConfigAutosave(unittest.TestCase):
    def test_saves_changes_once_and_waits_for_text_fields(self):
        from types import SimpleNamespace
        stubs = {m: mock.MagicMock() for m in ("keyboard", "pyperclip", "win32api", "win32gui", "win32process")}
        with mock.patch.dict(sys.modules, stubs):  # settings imports the Windows glue; CI runs this on Linux
            from murmur import settings
        st, saves = SimpleNamespace(saved="a"), []
        tick = lambda now, editing=False: settings.autosave_tick(st, now, editing, lambda: saves.append(now))
        tick("a")  # unchanged
        tick("b", editing=True)  # mid-typing in a field: wait
        self.assertEqual(saves, [])
        tick("b"); tick("b")  # left the field: one save, not one per poll
        self.assertEqual(saves, ["b"])
        tick("bad"); tick("bad")  # a value the form rejects is tried once, not every 500 ms
        self.assertEqual(saves, ["b", "bad"])


if __name__ == "__main__":
    unittest.main()
