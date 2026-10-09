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


def make(session, raw="delete that", reader_alive=False, ctx=None, app="code.exe", verdict=None):
    ov = SimpleNamespace(post=lambda fn: fn(), state=mock.Mock(), transcript=mock.Mock(), st="listening", level=0.0)
    hooks = SimpleNamespace(held=lambda: False, stop=lambda: None, current=lambda: True, done=lambda: None, mark=lambda s: None,
                            pasted=lambda *a: None, last_paste=lambda: (0.0, "", ""), retry=lambda: None)
    cfg = {"llm": {"timeout": 15}, "ui": {"sounds": False}, "apps": {}}  # no [privacy]: private mode off
    s = session.Session("command", cfg, ov, hooks)
    s.raw, s.app, s.ctx = raw, app, ctx or {"tone": "neutral", "cfg": cfg}
    s.reader = SimpleNamespace(is_alive=lambda: reader_alive)
    if verdict != "pending":  # the IsPassword answer UIA published (tests of the slow-reader case pass verdict="pending")
        s.verdict.publish(bool(s.ctx.get("password")))
        s._decide_password()
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


class Passwords(unittest.TestCase):  # A1
    def setUp(self):
        self.session = load_session()
        mock.patch.object(self.session.cues, "play").start()
        self.addCleanup(mock.patch.stopall)

    def test_dictation_skips_the_llm_context_history_and_clipboard(self):
        s, ov = make(self.session, "hunter two", ctx={"tone": "neutral", "password": True})
        s.mode = "dictate"
        with mock.patch.object(self.session.brain, "clean_auto", side_effect=AssertionError("password sent to the LLM")):
            self.assertEqual(s._finish_dictate(), "hunter two")
        pasted = []
        s.pasted = lambda *a: pasted.append(a)
        with mock.patch.object(self.session.win, "active_app", return_value=("code.exe", "t")), \
                mock.patch.object(self.session.win, "insert") as ins, mock.patch.object(self.session.win, "to_clipboard") as clipb, \
                mock.patch.object(self.session.store, "append_history") as hist, mock.patch.object(self.session.store, "read_history") as rh:
            s._paste_and_log("hunter two")
        self.assertEqual(ins.call_args.args[1]["output"]["method"], "type")  # typed: never on the clipboard
        hist.assert_not_called(); rh.assert_not_called(); clipb.assert_not_called()
        self.assertEqual(pasted, [("code.exe", "")])  # nothing to edit-last
        self.assertEqual(ov.state.call_args.args[1], "Typed into a password field \u00b7 not saved")  # never echoed back

    def test_failed_delivery_never_puts_a_password_on_the_clipboard(self):
        s, _ = make(self.session, "x", ctx={"tone": "neutral", "password": True})
        with mock.patch.object(self.session.win, "active_app", return_value=("other.exe", "t")), \
                mock.patch.object(self.session.win, "to_clipboard") as clipb:
            self.assertFalse(s._deliver("secret"))
        clipb.assert_not_called()

    def test_command_mode_is_refused(self):
        s, ov = make(self.session, "make it formal", ctx={"tone": "neutral", "password": True})
        with mock.patch.object(self.session.brain, "rewrite") as rw:
            self.assertIsNone(s._finish_command())
        rw.assert_not_called()
        self.assertEqual(ov.state.call_args.args[:2], ("offline", "Not available in password fields."))

    def test_no_live_clean_for_password_or_unfinished_reader(self):
        s, _ = make(self.session, ctx={"tone": "neutral", "password": True})
        s.mode, s.cfg["llm"]["live_clean"] = "dictate", True
        with mock.patch.object(self.session.threading, "Thread") as th:
            s._maybe_live("hello")
        th.assert_not_called()


class SlowPasswordVerdict(unittest.TestCase):  # R3
    def setUp(self):
        self.session = load_session()
        mock.patch.object(self.session.cues, "play").start()
        self.addCleanup(mock.patch.stopall)

    def test_words_are_masked_until_uia_says_not_a_password(self):
        s, ov = make(self.session, verdict="pending")
        self.assertTrue(s.guarded)
        s._paint_raw("hunter two is my password")
        self.assertNotIn("hunter", str(ov.transcript.call_args))  # a mask, never the words
        s.verdict.publish(False)
        self.assertFalse(s.guarded)
        s._paint_raw("hello there")
        self.assertIn("hello", str(ov.transcript.call_args))

    def test_live_clean_shows_on_the_card(self):  # "flow editing while you speak": the settled text replaces the raw words
        s, ov = make(self.session)
        s.mode = "dictate"
        s.cleaned = ("the budget is zero point four three actually zero point four four", "The budget is 0.44", True, True)
        s._paint_raw("the budget is zero point four three, actually zero point four four per unit")
        ov.transcript.assert_called_with("The budget is 0.44 per", "unit")
        s.cleaned = ("a totally different start", "Different.", True, True)  # no longer a prefix: raw words
        s._paint_raw("hello there friend")
        ov.transcript.assert_called_with("hello there", "friend")
        s.cleaned = ("hello there", "Rejected output", True, False)  # the guard rejected it: never shown
        s._paint_raw("hello there friend")
        ov.transcript.assert_called_with("hello there", "friend")
        n = ov.transcript.call_count
        s.marks["release"] = 1.0  # key up: a late live clean must not overwrite the result card
        s._paint_raw("hello there friend again")
        self.assertEqual(ov.transcript.call_count, n)

    def test_password_true_masks_forever(self):
        s, ov = make(self.session, verdict="pending")
        s.verdict.publish(True)
        s._paint_raw("secret words")
        self.assertNotIn("secret", str(ov.transcript.call_args))
        self.assertTrue(s.guarded)

    def test_unknown_verdict_makes_the_session_private_and_local(self):
        s, _ = make(self.session, verdict="pending")
        s.PW_WAIT = 0.01  # the slow reader never answers
        s.cfg["llm"] = {"provider": "anthropic", "fallback": True}
        s._decide_password()
        self.assertTrue(s._unknown and s.sensitive and s.private)
        self.assertFalse(s.ctx["cfg"]["llm"]["fallback"])  # no remote fallback reachable
        self.assertEqual(s._finish_dictate(), s.raw.strip())  # remote primary: no LLM at all
        with mock.patch.object(self.session.win, "active_app", return_value=("code.exe", "t")), \
                mock.patch.object(self.session.win, "insert") as ins, mock.patch.object(self.session.store, "append_history") as hist, \
                mock.patch.object(self.session.win, "to_clipboard") as clipb, mock.patch.object(self.session.autolearn, "remember") as rem:
            s._paste_and_log("typed words")
        self.assertEqual(ins.call_args.args[1]["output"]["method"], "type")
        hist.assert_not_called(); clipb.assert_not_called(); rem.assert_not_called()
        self.assertEqual(s._prep("pod stead"), "pod stead")  # no phonetic matching either

    def test_verdict_is_frozen_at_release(self):
        s, _ = make(self.session, verdict="pending")
        s.verdict.publish(True)
        s._decide_password()
        self.assertTrue(s.password)
        s.verdict.value = False  # a late/odd flip must not change the decision after delivery started
        self.assertTrue(s.password)

    def test_password_text_loses_the_period_and_the_auto_capital(self):
        s, _ = make(self.session, "Hunter two.", ctx={"tone": "neutral", "password": True})
        s.raw = "Hunter two."
        self.assertEqual(s._finish_dictate(), "hunter two")
        self.assertEqual(self.session.brain.password_text("OpenSesame!"), "OpenSesame")
        self.assertEqual(self.session.brain.password_text("I am."), "I am")


class PrivateMode(unittest.TestCase):  # A4
    def test_private_mode_writes_no_history_and_learns_nothing(self):
        session = load_session()
        mock.patch.object(session.cues, "play").start(); self.addCleanup(mock.patch.stopall)
        s, ov = make(session, "hello there")
        s.mode, s.cfg["privacy"] = "dictate", {"private": True}
        with mock.patch.object(session.win, "active_app", return_value=("code.exe", "t")), mock.patch.object(session.win, "insert"), \
                mock.patch.object(session.store, "append_history") as hist, mock.patch.object(s, "_learn_from_redictation") as learn:
            s._paste_and_log("Hello there.")
        hist.assert_not_called(); learn.assert_not_called()
        self.assertTrue(s.pasted_ok)


class Adapt(unittest.TestCase):  # Track B wiring
    def setUp(self):
        self.session = load_session()
        mock.patch.object(self.session.cues, "play").start()
        self.addCleanup(mock.patch.stopall)

    def test_prep_applies_scratch_then_phonetic_and_sets_hints(self):
        s, _ = make(self.session, ctx={"tone": "neutral", "terms": ["Podstead", "Kubernetes"]})
        s.cfg["learning"] = {"phonetic": True}
        with mock.patch.object(self.session.store, "load_dictionary", return_value=[]):
            out = s._prep("We moved it to pod stead and cooper netties.")
        self.assertEqual(out, "We moved it to Podstead and cooper netties.")
        self.assertEqual(s.ctx["hints"], [("cooper netties", "Kubernetes")])
        with mock.patch.object(self.session.store, "load_dictionary", return_value=[]):
            self.assertEqual(s._prep("Hello. Pod stead is up. Scratch that."), "Hello.")  # scratch first, then phonetic

    def test_prep_is_off_for_passwords_and_when_switched_off(self):
        s, _ = make(self.session, ctx={"tone": "neutral", "terms": ["Podstead"], "password": True})
        self.assertEqual(s._prep("pod stead"), "pod stead")
        s2, _ = make(self.session, ctx={"tone": "neutral", "terms": ["Podstead"]})
        s2.cfg["learning"] = {"phonetic": False}
        self.assertEqual(s2._prep("pod stead"), "pod stead")

    def test_a_dictate_paste_feeds_the_learners_but_private_and_command_do_not(self):
        for private, mode, expect in ((False, "dictate", True), (True, "dictate", False), (False, "command", False)):
            s, _ = make(self.session, "hello there")
            s.mode = mode
            if private:
                s.cfg["privacy"] = {"private": True}
            with mock.patch.object(self.session.win, "active_app", return_value=("code.exe", "t")), mock.patch.object(self.session.win, "insert"), \
                    mock.patch.object(self.session.store, "append_history"), mock.patch.object(self.session.store, "read_history", return_value=[]), \
                    mock.patch.object(self.session.autolearn, "remember") as rem, mock.patch.object(self.session.autolearn, "count_dictation") as cnt, \
                    mock.patch.object(self.session.autolearn, "schedule") as sched:
                s._paste_and_log("Hello there.")
            self.assertEqual((rem.called, cnt.called, sched.called), (expect,) * 3, (private, mode))

    def test_model_starting_label(self):
        s, ov = make(self.session)
        s.degraded = True
        self.session.store.runtime["llm_starting"] = True
        s._show_outcome("x")
        self.assertEqual(ov.state.call_args.kwargs.get("label"), "Model starting\u2026")
        self.session.store.runtime["llm_starting"] = False
        s._show_outcome("x")
        self.assertIsNone(ov.state.call_args.kwargs.get("label"))


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


class LongTake(unittest.TestCase):
    def test_committed_take_is_decoded_again_whole(self):  # Ethan 10-08: cuts misheard "vaults" as "bolts"
        session = load_session()
        s, _ = make(session)
        s.full, s.buf, s.committed = [[0] * 16000] * 20, [[0] * 16000] * 5, "same format as the bolts"
        with mock.patch.object(session, "transcribe", lambda f: f"whole{len(f)}"):
            self.assertEqual(s._final_transcript(), "whole20")
            s.full = [[0] * 16000] * (session.REDECODE_S + 1)  # too long: committed + open as before
            self.assertEqual(s._final_transcript(), "same format as the bolts whole5")
            s.committed = ""
            self.assertEqual(s._final_transcript(), "whole5")

    def test_reader_sees_the_app_before_it_reads(self):  # Ethan 10-08: terminal scrollback reached the prompt (app was "")
        session = load_session()
        s, _ = make(session, app="")
        seen = []
        s.mic = SimpleNamespace(attach=lambda cb: None)
        with mock.patch.object(session.context, "gather", lambda cfg: {"app": "windowsterminal.exe", "title": "t", "tone": "code"}), \
             mock.patch.object(session.threading, "Thread", lambda target, daemon: SimpleNamespace(start=lambda: seen.append(s.app))), \
             mock.patch.object(session, "sd"), mock.patch.object(session, "mic"):
            s.ov.show = lambda *a: None
            self.assertTrue(s._start())
        self.assertEqual(seen, ["windowsterminal.exe"])

if __name__ == "__main__":
    unittest.main()
