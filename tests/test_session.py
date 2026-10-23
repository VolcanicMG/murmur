import sys, time, unittest
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
                    mock.patch.object(self.session.autolearn, "watch") as sched:
                s._paste_and_log("Hello there.")
            self.assertEqual((rem.called, cnt.called, sched.called), (expect,) * 3, (private, mode))

    def test_dictation_log_line_has_sizes_never_text(self):
        s, _ = make(self.session, "secret words", ctx={"tone": "neutral", "before": "abc secret", "after": "", "sel_known": True})
        s.mode, s.segments, s.redecoded, s.live_use, s.ctx_src = "dictate", 2, True, "reused", "uia"
        with self.assertLogs("murmur", "INFO") as cm:
            s._log_dictation()
        self.assertEqual(cm.output[-1].split(":", 2)[-1], "dictation app=code.exe text=read before=10 after=0 sel=known ctx=uia "
                         "anchor=none segments=2 redecoded=yes live=reused guard=ok resumed=no")
        s.cfg["privacy"] = {"private": True}
        with self.assertLogs("murmur", "INFO") as cm:
            s._log_dictation()
        self.assertTrue(cm.output[-1].endswith(":dictation private"))

    def test_model_starting_label(self):
        s, ov = make(self.session)
        s.degraded = True
        self.session.store.runtime["llm_starting"] = True
        s._show_outcome("x")
        self.assertEqual(ov.state.call_args.kwargs.get("label"), "Model starting\u2026")
        self.session.store.runtime["llm_starting"] = False
        s._show_outcome("x")
        self.assertIsNone(ov.state.call_args.kwargs.get("label"))

    def test_no_cleanup_model_says_where_to_get_it(self):
        s, ov = make(self.session)
        s.degraded = True
        with mock.patch.object(self.session.builtin, "needs_model", return_value=True):
            s._show_outcome("x")
        self.assertEqual(ov.state.call_args.kwargs.get("label"), "No cleanup model")
        self.assertIn("Settings", ov.state.call_args.args[1])


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


class Resume(unittest.TestCase):  # Ethan 10-22: release and press again quickly = one dictation
    def setUp(self):
        self.session = load_session()
        for name in ("play",):
            mock.patch.object(self.session.cues, name).start()
        mock.patch.object(self.session.autolearn, "maybe_refresh_style").start()
        mock.patch.object(self.session.store, "keep_take").start()
        self.addCleanup(mock.patch.stopall)

    def dictation(self, prev=None, speech=2.0, frames=1):
        s, ov = make(self.session, "")
        s.mode, s.prev, s.t0 = "dictate", prev, time.time() - 2
        s.stream = SimpleNamespace(stop=lambda: None, close=lambda: None)
        s.vad = {**s.vad, "speech_s": speech}
        s.full = [[0] * 16000 for _ in range(frames)]
        s.reader = SimpleNamespace(is_alive=lambda: False, join=lambda t: None)
        ov.hide = ov.release = mock.Mock()
        return s

    def run_session(self, s, heard, during_clean=None):
        def finish():
            if during_clean:
                during_clean()
            return s.raw
        with mock.patch.object(s, "_start", return_value=True), mock.patch.object(s, "_poll_loop"), \
                mock.patch.object(self.session, "transcribe", side_effect=heard), \
                mock.patch.object(s, "_finish_dictate", side_effect=finish) as fd, mock.patch.object(s, "_paste_and_log") as pl:
            s.run()
        return pl, fd

    def handed_over(self, raw="so I was saying"):
        old = self.dictation()
        self.assertTrue(old.claim(False))
        pl, fd = self.run_session(old, lambda f: raw)
        pl.assert_not_called(); fd.assert_not_called()  # never cleaned, never pasted (so no history, no learning)
        self.assertTrue(old.handoff.is_set())
        return old

    def test_claimed_session_hands_over_raw_and_frames_and_never_pastes(self):
        old = self.handed_over()
        self.assertEqual(old.raw, "so I was saying")
        new = self.dictation(prev=old, frames=2)
        new.resumed = True
        pl, _ = self.run_session(new, lambda f: f"whole{len(f)}")
        pl.assert_called_once_with("Whole3")  # the whole take (1 handed + 2 own frames) decoded in one piece
        self.assertTrue(new.merged and new.redecoded)

    def test_new_take_without_speech_pastes_the_handed_text_alone(self):
        new = self.dictation(prev=self.handed_over(), speech=0.0)
        new.resumed = True
        pl, _ = self.run_session(new, lambda f: "")
        pl.assert_called_once_with("So I was saying")

    def test_a_chord_drops_only_the_new_take(self):
        new = self.dictation(prev=self.handed_over())
        new.resumed = True
        new.discard()
        pl, _ = self.run_session(new, lambda f: "control c")
        pl.assert_called_once_with("So I was saying")

    def test_esc_on_the_new_card_cancels_the_merged_dictation(self):
        new = self.dictation(prev=self.handed_over())
        new.resumed = True
        new.cancel()
        pl, _ = self.run_session(new, lambda f: "more words")
        pl.assert_not_called()

    def test_claim_during_cleanup_aborts_before_delivery(self):
        old = self.dictation()
        pl, fd = self.run_session(old, lambda f: "first part", during_clean=lambda: self.assertTrue(old.claim(False)))
        fd.assert_called_once(); pl.assert_not_called()
        self.assertTrue(old.handoff.is_set())

    def test_too_late_once_delivery_started(self):
        old = self.dictation()
        pl, _ = self.run_session(old, lambda f: "first part")
        pl.assert_called_once()
        self.assertFalse(old.claim(False))  # sealed: a follower is independent

    def test_commands_never_resume(self):
        s, _ = make(self.session, "make it formal")
        self.assertFalse(s.resumable("code.exe"))
        s.mode = "dictate"
        self.assertTrue(s.resumable("code.exe"))
        self.assertFalse(s.resumable("other.exe"))

    def test_a_chord_before_the_mic_opens_still_keeps_take_one(self):  # review 1
        old = self.dictation()
        new = self.dictation(prev=old)
        new.discard()  # the chord / learn hotkey lands before _start
        self.assertFalse(new.cancelled)
        self.assertTrue(self.start(new))
        self.assertTrue(new.resumed and old.handed)
        new.reader = SimpleNamespace(is_alive=lambda: False, join=lambda t: None)  # _start's stubbed thread
        self.run_session(old, lambda f: "so I was saying")
        pl, _ = self.run_session(new, lambda f: "control c")
        pl.assert_called_once_with("So I was saying")

    def test_a_chord_with_nothing_handed_over_is_a_silent_discard(self):
        late = self.dictation()
        late._seal()
        new = self.dictation(prev=late)
        new.discard()
        self.start(new)
        self.assertFalse(new.resumed)
        pl, _ = self.run_session(new, lambda f: "control c")
        pl.assert_not_called()
        self.assertTrue(new.cancelled and new.silent)

    def test_never_claim_an_unverified_password_or_a_private_flip(self):  # review 2
        pending, _ = make(self.session, "", verdict="pending")
        pending.mode = "dictate"
        self.assertFalse(pending.claim(False))
        pw, _ = make(self.session, "", ctx={"tone": "neutral", "password": True})
        pw.mode = "dictate"
        self.assertFalse(pw.claim(False))
        ok = self.dictation()
        self.assertFalse(ok.claim(True))  # private mode was switched on between the takes
        self.assertTrue(ok.claim(False))

    def test_esc_on_the_taken_over_session_cancels_the_follower(self):  # review 3
        old = self.dictation()
        self.assertTrue(old.claim(False))
        old.cancel()
        self.run_session(old, lambda f: "first part")
        new = self.dictation(prev=old)
        new.resumed = True
        pl, _ = self.run_session(new, lambda f: "more")
        pl.assert_not_called()
        self.assertTrue(new.cancelled)

    def test_a_crashed_previous_session_is_not_absorbed(self):
        old = self.dictation()
        self.assertTrue(old.claim(False))
        old.raw = "partial live words"

        def boom(f):
            raise RuntimeError("stt")
        self.run_session(old, boom)
        self.assertTrue(old.crashed and old.handoff.is_set())
        new = self.dictation(prev=old)
        new.resumed = True
        pl, _ = self.run_session(new, lambda f: "second take")
        pl.assert_called_once_with("Second take")
        self.assertFalse(new.merged)

    def test_spoken_command_in_take_one_runs_and_hands_nothing_over(self):  # review 5
        old = self.dictation()
        self.assertTrue(old.claim(False))
        with mock.patch.object(old, "_cmd_undo") as undo:
            self.run_session(old, lambda f: "undo that")
        undo.assert_called_once()
        new = self.dictation(prev=old)
        new.resumed = True
        pl, _ = self.run_session(new, lambda f: "hello there")
        pl.assert_called_once_with("Hello there")
        self.assertFalse(new.merged)

    def test_claim_during_the_password_wait_skips_keep_take_and_the_clean(self):  # review 6
        old = self.dictation()
        real = old._decide_password
        with mock.patch.object(old, "_decide_password", side_effect=lambda: (real(), old.claim(False))):
            pl, fd = self.run_session(old, lambda f: "first part")
        self.session.store.keep_take.assert_not_called()
        fd.assert_not_called(); pl.assert_not_called()

    def run_ordered(self, s, raw, order, gate=None):
        """run() with instance-only stubs (safe on two threads at once); the paste appends raw to order."""
        def finish():
            if gate:
                gate.wait(5)
            return s.raw
        s._start, s._poll_loop = (lambda: True), (lambda: None)
        s._final_transcript, s._finish_dictate = (lambda: raw), finish
        s._paste_and_log = lambda final: order.append(raw)
        s.run()

    def refused_pair(self):
        old = self.dictation()
        new = self.dictation(prev=old)
        new.was_private = True  # private mode flipped between the takes: the claim is refused
        self.assertTrue(self.start(new))
        self.assertFalse(new.resumed)
        self.assertIs(new.after, old)
        new.reader = SimpleNamespace(is_alive=lambda: False, join=lambda t: None)
        new.t0 = time.time() - 2
        return old, new

    def test_refused_claim_keeps_paste_order(self):  # re-review R1
        import threading
        old, new = self.refused_pair()
        order, gate = [], threading.Event()
        ta = threading.Thread(target=self.run_ordered, args=(old, "first", order, gate)); ta.start()
        tb = threading.Thread(target=self.run_ordered, args=(new, "second", order)); tb.start()
        tb.join(0.3)
        self.assertEqual(order, [])  # B is ready but waits for the slow A
        gate.set(); ta.join(5); tb.join(5)
        self.assertEqual(order, ["first", "second"])

    def test_a_stuck_previous_session_only_delays_the_paste(self):
        old, new = self.refused_pair()
        new.PREV_WAIT, order = 0.05, []
        with self.assertLogs("murmur", "WARNING"):
            self.run_ordered(new, "second", order)  # A never finishes
        self.assertEqual(order, ["second"])

    def start(self, new, fail=False):
        new.mic = None
        sd = mock.MagicMock()
        if fail:
            sd.InputStream.side_effect = OSError("no mic")
        new.ov.show = lambda *a: None
        with mock.patch.object(self.session.context, "gather", lambda cfg: {"app": "code.exe", "title": "t", "tone": "neutral"}), \
                mock.patch.object(self.session.threading, "Thread", lambda target, daemon: SimpleNamespace(start=lambda: None)), \
                mock.patch.object(self.session, "sd", sd), mock.patch.object(self.session, "mic"):
            return new._start()

    def test_start_claims_only_once_the_mic_works(self):
        old = self.dictation()
        self.assertFalse(self.start(self.dictation(prev=old), fail=True))
        self.assertFalse(old.handed)  # the old take still pastes itself
        new = self.dictation(prev=old)
        self.assertTrue(self.start(new))
        self.assertTrue(new.resumed and old.handed and old.cut_tail.is_set())
        late = self.dictation()
        late._seal()
        new2 = self.dictation(prev=late)
        self.start(new2)
        self.assertFalse(new2.resumed); self.assertIsNone(new2.prev)


class OwnContext(unittest.TestCase):  # Ethan 10-22: no space after the period in a terminal
    def setUp(self):
        self.session = load_session()

    def session_in(self, ctx=None, typed=0.0, age=5.0, private=False):
        s, _ = make(self.session, "x", app="windowsterminal.exe", ctx=ctx)
        s.mode = "dictate"
        s.last_paste = lambda: (time.time() - age, "windowsterminal.exe", "Hello there.")
        s.typed_at = lambda: time.time() - age + typed if typed else 0.0
        if private:
            s.cfg["privacy"] = {"private": True}
        return s

    def test_last_paste_stands_in_for_an_unreadable_field(self):
        s = self.session_in()
        s._own_context()
        self.assertEqual((s.ctx["before"], s.ctx_src), ("Hello there.", "lastpaste"))
        self.assertEqual(self.session.brain.fit("Next.", s.ctx["before"]), " Next.")

    def test_gates(self):
        for s in (self.session_in(typed=1.0), self.session_in(age=200), self.session_in(private=True),
                  self.session_in(ctx={"tone": "neutral", "password": True})):
            s._own_context()
            self.assertEqual((s.ctx.get("before", ""), s.ctx_src), ("", "none"))
        s = self.session_in(ctx={"tone": "neutral", "before": ""})  # UIA read a known-empty field: stays empty
        s._own_context()
        self.assertEqual((s.ctx["before"], s.ctx_src), ("", "none"))
        s = self.session_in(ctx={"tone": "neutral", "before": "typed by hand"})
        s._own_context()
        self.assertEqual((s.ctx["before"], s.ctx_src), ("typed by hand", "uia"))

    def test_deliver_brackets_our_paste_keys(self):
        s, _ = make(self.session, "x")
        seen = []
        s.pasting = seen.append
        with mock.patch.object(self.session.win, "active_app", return_value=("code.exe", "t")), \
                mock.patch.object(self.session.win, "insert", side_effect=lambda *a: seen.append("insert")):
            self.assertTrue(s._deliver("hi"))
        with mock.patch.object(self.session.win, "active_app", side_effect=RuntimeError), mock.patch.object(self.session.win, "to_clipboard"):
            self.assertFalse(s._deliver("hi"))
        self.assertEqual(seen, [True, "insert", False, True, False])


if __name__ == "__main__":
    unittest.main()
