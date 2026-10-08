import sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STUBS = ("keyboard", "pystray", "pyperclip", "win32api", "win32gui", "win32process", "numpy", "sounddevice", "onnx_asr",
         "murmur.settings", "murmur.card", "murmur.tray", "murmur.session")


class FakeKeyboard:
    """Records hook registrations; every register call returns a distinct handle, unhook(handle) records the removal."""
    def __init__(self):
        self.live, self.n = {}, 0

    def _reg(self, kind, what):
        self.n += 1; h = f"{kind}#{self.n}"; self.live[h] = what
        return h

    def normalize_name(self, k):
        return k

    def on_press_key(self, key, cb):
        return self._reg("press", key)

    def on_release_key(self, key, cb):
        return self._reg("release", key)

    def hook(self, cb):
        return self._reg("hook", "other")

    def add_hotkey(self, key, cb):
        return self._reg("hotkey", key)

    def unhook(self, h):
        del self.live[h]  # KeyError on a double unhook = a bookkeeping bug


def load_app():
    kb = FakeKeyboard()
    mods = {m: mock.MagicMock() for m in STUBS}
    mods["keyboard"] = kb
    import murmur
    before = set(sys.modules)
    with mock.patch.dict(sys.modules, mods):
        from murmur import app
        new = set(sys.modules) - before
    for name in new:  # patch.dict drops them from sys.modules; drop the package attributes too so other tests don't
        if name.startswith("murmur."):  # pick up modules that were bound to stubs
            murmur.__dict__.pop(name.split(".", 1)[1], None)
    return app, kb


def cfg(dictate="right ctrl", command="right alt", **extra):
    return {"hotkeys": {"dictate": dictate, "command": command, "fix_last": "ctrl+alt+m", "learn_word": "ctrl+alt+d"}, "apps": {}, **extra}


class Rebind(unittest.TestCase):
    def setUp(self):
        self.app, self.kb = load_app()
        self.app.keyboard = self.kb
        self.app._bound.update(ov=object(), mark=lambda s: None, removers=[])
        self.app.S.update(session=None, mode=None, g=("idle", 0.0), pending_rebind=False)
        self.app.S["keys"], _, self.app._bound["removers"] = self.app._register(cfg(), self.app._bound["ov"], self.app._bound["mark"])

    def test_rebind_swaps_every_hook_and_is_idempotent(self):  # A6
        self.assertEqual(len(self.kb.live), 7)  # press+release x2, other-key hook, fix_last, learn_word
        self.assertTrue(self.app.rebind_hotkeys(cfg(dictate="right shift")))
        self.assertEqual(len(self.kb.live), 7)  # nothing leaked
        self.assertEqual(sorted(v for k, v in self.kb.live.items() if k.startswith("press")), ["right alt", "right shift"])
        self.assertEqual(self.app.S["keys"]["dictate"], "right shift")
        self.assertTrue(self.app.rebind_hotkeys(cfg(dictate="right shift")))
        self.assertEqual(len(self.kb.live), 7)

    def test_deferred_while_a_session_is_live(self):
        self.app.S["session"] = object()
        self.assertFalse(self.app.rebind_hotkeys(cfg(dictate="right shift")))
        self.assertTrue(self.app.S["pending_rebind"])
        self.assertEqual(self.app.S["keys"]["dictate"], "right ctrl")  # unchanged until the session ends
        self.app.S["session"] = None
        with mock.patch.object(self.app.time, "sleep"):
            self.app._apply_pending_rebind()
        self.assertEqual((self.app.S["keys"]["dictate"], self.app.S["pending_rebind"]), ("right shift", False))

    def test_invalid_hotkey_keeps_the_old_hooks(self):
        with mock.patch.object(self.app.store, "hotkey_ok", lambda v, key=None: v != "ctrl+"):
            self.assertFalse(self.app.rebind_hotkeys(cfg(dictate="ctrl+")))
        self.assertEqual(len(self.kb.live), 7)  # never unhooked
        self.assertEqual(self.app.S["keys"]["dictate"], "right ctrl")

    def test_not_running_is_a_noop(self):
        self.app._bound["ov"] = None
        self.assertFalse(self.app.rebind_hotkeys(cfg()))
        self.assertEqual(len(self.kb.live), 7)


class FixPanel(unittest.TestCase):
    def test_saving_a_fix_learns_terms_and_records_the_correction(self):
        app, _ = load_app()
        panel = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"murmur.settings": panel}), \
                mock.patch.object(app.store, "add_terms", return_value=["Podsteads"]) as add, \
                mock.patch.object(app.store, "correct_last") as corr:
            app.fix_last("root", {"ts": "t1", "final": "two pod sets", "raw": "two pod sets"})
            root, row, on_save = panel.fix_panel.call_args[0]
            on_save("two Podsteads")
        add.assert_called_once_with(["Podsteads"])
        corr.assert_called_once_with("t1", "two pod sets", "two Podsteads")


class Hooks(unittest.TestCase):
    def setUp(self):
        self.app, self.kb = load_app()

    def test_quit_stops_the_bundled_model_before_the_tray_icon(self):
        order = []
        fake = mock.MagicMock(); fake.stop.side_effect = lambda: order.append("localllm.stop")
        icon, ov = mock.Mock(), mock.Mock()
        icon.stop.side_effect = lambda: order.append("icon.stop")
        self.app.S.update(session=None, held=False)
        import murmur
        with mock.patch.dict(sys.modules, {"murmur.localllm": fake}), mock.patch.object(murmur, "localllm", fake, create=True):
            self.app.quit_app(icon, ov)
        self.assertEqual(order, ["localllm.stop", "icon.stop"])

    def test_quit_survives_a_missing_or_broken_localllm(self):
        fake = mock.MagicMock(); fake.stop.side_effect = RuntimeError("x")
        import murmur
        with mock.patch.dict(sys.modules, {"murmur.localllm": fake}), mock.patch.object(murmur, "localllm", fake, create=True):
            self.app._stop_builtin()  # logged, not raised
        with mock.patch.dict(sys.modules, {"murmur.localllm": None}):  # import fails -> ImportError
            self.app._stop_builtin()


class TrayHooks(unittest.TestCase):
    def test_no_arg_hooks_for_tray_extra_items(self):  # tray.extra_items calls these without a cfg
        app, _ = load_app()
        from murmur import store
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        cfg = store.load_config()
        app._bound.update(cfg=cfg, mark=None)
        self.assertFalse(app.is_private())
        self.assertTrue(app.toggle_private())
        self.assertTrue(app.is_private())
        self.assertTrue(store.is_private(store.load_config()))  # persisted


class Exclusions(unittest.TestCase):
    def setUp(self):
        self.app, self.kb = load_app()
        from murmur import store
        self.store = store
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        self.cfg = store.load_config()
        self.app.S["last_fg"] = ""

    def test_last_foreground_ignores_murmur_and_the_shell(self):
        for exe, want in (("code.exe", "code.exe"), ("explorer.exe", "code.exe"), ("pythonw.exe", "code.exe"), ("", "code.exe"), ("game.exe", "game.exe")):
            with mock.patch.object(self.app.win, "active_app", return_value=(exe, "t")):
                self.app._note_foreground()
            self.assertEqual(self.app.last_foreground(), want)

    def test_shell_verb_toggles_by_file_and_the_tray_picks_it_up(self):  # A3: Explorer "Don't use murmur in this app"
        from murmur import shellmenu
        ov = mock.Mock(); ov.post = lambda fn: fn()
        self.app._bound.update(cfg=self.cfg, ov=ov, cfg_mtime=None)
        self.app._watch_config()  # the tray is up: records the mtime, nothing to sync
        ov.flash.assert_not_called()
        self.assertEqual(shellmenu.exclude_file(r"C:\Games\Game.EXE"), ("game.exe", True))  # the Explorer verb, another process
        self.assertEqual(self.store.apps_exclude(self.store.load_config()), ["game.exe"])
        self.assertIn('--exclude "%1"', shellmenu._cmd())
        import os, time
        os.utime(self.store.HOME / "config.toml", (time.time() + 5, time.time() + 5))
        self.app._watch_config()
        self.assertEqual(self.store.apps_exclude(self.cfg), ["game.exe"])
        self.assertEqual(ov.flash.call_args.kwargs["label"], "Excluded")
        self.assertEqual(shellmenu.exclude_file("game.exe"), ("game.exe", False))  # second right-click = back on
        self.assertEqual(shellmenu.target_name("notes.txt"), "notes.txt")

    def test_ignored_uses_exclusion_and_fullscreen(self):
        self.cfg["apps"]["exclude"] = ["game.exe"]
        with mock.patch.object(self.app.win, "active_app", return_value=("game.exe", "t")), mock.patch.object(self.app.win, "quns_state", return_value=0):
            self.assertTrue(self.app._ignored(self.cfg))
        with mock.patch.object(self.app.win, "active_app", return_value=("code.exe", "t")), mock.patch.object(self.app.win, "quns_state", return_value=3):
            self.assertTrue(self.app._ignored(self.cfg))
            self.cfg["apps"]["pause_fullscreen"] = False
            self.assertFalse(self.app._ignored(self.cfg))

    def test_private_mode_toggle_persists(self):  # A4
        self.assertTrue(self.app.toggle_private(self.cfg))
        self.assertTrue(self.store.is_private(self.store.load_config()))
        self.assertFalse(self.app.toggle_private(self.cfg))

    def test_press_in_an_excluded_app_starts_nothing(self):
        self.cfg["apps"]["exclude"] = ["game.exe"]
        self.app.S.update(session=None, mode=None, ready=True, paused=False, capturing=False, g=("idle", 0.0))
        with mock.patch.object(self.app.win, "active_app", return_value=("game.exe", "t")), mock.patch.object(self.app.win, "quns_state", return_value=0):
            self.app.press("dictate", self.cfg, mock.Mock(), lambda s: None)
        self.assertEqual((self.app.S["mode"], self.app.S["g"]), (None, ("idle", 0.0)))  # no session, no gesture state


if __name__ == "__main__":
    unittest.main()
