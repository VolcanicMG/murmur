import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STUBS = ("numpy", "sounddevice", "keyboard", "pyperclip", "win32api", "win32gui", "win32process", "onnx_asr")


def load_session():
    with mock.patch.dict(sys.modules, {m: mock.MagicMock() for m in STUBS}):
        from murmur import session
    return session


def make(session, raw="delete that", reader_alive=False, ctx=None, app="code.exe"):
    ov = SimpleNamespace(post=lambda fn: fn(), state=mock.Mock(), level=0.0)
    hooks = SimpleNamespace(held=lambda: False, stop=lambda: None, current=lambda: True, done=lambda: None, mark=lambda s: None,
                            pasted=lambda *a: None, last_paste=lambda: (0.0, "", ""), retry=lambda: None)
    cfg = {"llm": {"timeout": 15}, "ui": {"sounds": False}, "apps": {}}
    s = session.Session("command", cfg, ov, hooks)
    s.raw, s.app, s.ctx = raw, app, ctx or {"tone": "neutral", "cfg": cfg}
    s.reader = SimpleNamespace(is_alive=lambda: reader_alive)
    return s, ov


class CommandGuards(unittest.TestCase):
    def setUp(self):
        self.session = load_session()
        self.cues = mock.patch.object(self.session.cues, "play").start()
        self.addCleanup(mock.patch.stopall)

    def test_unfinished_uia_read_aborts_instead_of_pasting(self):  # D8
        s, ov = make(self.session, "make it formal", reader_alive=True)
        with mock.patch.object(self.session.brain, "rewrite") as rw:
            self.assertIsNone(s._finish_command())
        rw.assert_not_called()
        self.assertEqual(ov.state.call_args.args[0], "offline")
        self.cues.assert_called_with("error", s.cfg)

    def test_unread_selection_only_blocks_commands_that_need_it(self):  # F5
        for raw in ("add Toung to the dictionary", "scratch that"):
            s, ov = make(self.session, raw, reader_alive=True)
            with mock.patch.object(self.session.brain, "rewrite") as rw, mock.patch.object(s, "_cmd_undo"), mock.patch.object(s, "_cmd_learn"):
                self.assertIsNone(s._finish_command())
            rw.assert_not_called()
            self.assertNotIn("Not read", [c.kwargs.get("label") for c in ov.state.call_args_list])  # no abort card
        for raw in ("delete that", "make it formal", "add this word to the dictionary"):
            s, ov = make(self.session, raw, reader_alive=True)
            self.assertIsNone(s._finish_command())
            self.assertEqual(ov.state.call_args.kwargs.get("label"), "Not read")

    def test_delete_that_with_clipboard_selection_never_presses_delete(self):  # D9
        s, ov = make(self.session, "delete that", ctx={"tone": "neutral", "sel_known": False, "selected": ""})
        with mock.patch.object(self.session.win, "copy_selection", return_value="some line\n"):
            self.assertIsNone(s._finish_command())
        self.session.keyboard.send.assert_not_called()
        self.assertEqual(ov.state.call_args.args[0], "nothing")

    def test_delete_that_with_uia_selection_deletes_it(self):
        s, ov = make(self.session, "delete that", ctx={"tone": "neutral", "sel_known": True, "selected": "abc"})
        self.assertIsNone(s._finish_command())
        self.session.keyboard.send.assert_called_with("delete")
        self.assertIn("Deleted 3 chars", ov.state.call_args.args)

    def test_wait_cap(self):  # D11
        w = self.session.wait_cap
        self.assertEqual(w({"llm": {"timeout": 15}}, 1), 15)
        self.assertEqual(w({"llm": {"timeout": 15, "fallback": True}}, 3), 90)


class Commit(unittest.TestCase):
    def test_forced_cut_in_a_room_that_never_goes_quiet(self):  # D7
        session = load_session()
        s, _ = make(session)
        frames = [[0] * 160 for _ in range(3100)]  # 31 s in 10 ms blocks
        s.rms = [0.05] * 3100
        s.rms[2950] = s.rms[2951] = 0.001  # the quietest spot in the last 2 s
        s.vad = {**s.vad, "quiet_ms": 0.0}
        s.buf = list(frames)
        calls = []

        def fake_transcribe(f):
            calls.append(len(f)); return f"seg{len(f)}"
        with mock.patch.object(session, "transcribe", fake_transcribe):
            rest = s._commit(frames, "whole")
        cut = calls[0]
        self.assertTrue(2900 < cut < 3100)
        self.assertEqual(s.committed, f"seg{cut}")
        self.assertEqual(rest, f"seg{3100 - cut}")
        self.assertEqual((len(s.buf), len(s.rms)), (3100 - cut, 3100 - cut))

    def test_pause_commits_everything_once(self):
        session = load_session()
        s, _ = make(session)
        frames = [[0] * 160 for _ in range(900)]  # 9 s
        s.buf, s.rms, s.vad = list(frames), [0.01] * 900, {**s.vad, "quiet_ms": 800.0}
        with mock.patch.object(session, "transcribe", side_effect=AssertionError("must not re-transcribe")):
            self.assertEqual(s._commit(frames, "hello there"), "")
        self.assertEqual((s.committed, s.buf), ("hello there", []))


if __name__ == "__main__":
    unittest.main()
