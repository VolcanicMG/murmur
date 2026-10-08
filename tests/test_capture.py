import json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import capture, workspace


class Gesture(unittest.TestCase):
    def run_events(self, evs, tap_ms=300):
        st, out = ("idle", 0.0), []
        for e, t in evs:
            st, a = capture.gesture(st, e, t, tap_ms); out.append(a)
        return st, out

    def test_hold(self):
        st, out = self.run_events([("press", 0), ("release", 1.5)])
        self.assertEqual((st, out), (("idle", 0.0), ["start", "stop"]))

    def test_tap_latches_then_tap_stops_and_swallows_release(self):
        st, out = self.run_events([("press", 0), ("release", 0.1), ("press", 5), ("release", 5.05)])
        self.assertEqual(out, ["start", "latch", "stop", "none"])
        self.assertEqual(st, ("idle", 0.0))

    def test_tap_then_esc(self):
        st, out = self.run_events([("press", 0), ("release", 0.1), ("stop", 2)])
        self.assertEqual((st, out[-1]), (("idle", 0.0), "none"))
        self.assertEqual(self.run_events([("press", 0), ("release", 0.1), ("stop", 2), ("press", 3)])[1][-1], "start")

    def test_autorepeat_ignored(self):
        _, out = self.run_events([("press", 0), ("press", 0.05), ("press", 0.1), ("release", 1.0)])
        self.assertEqual(out, ["start", "none", "none", "stop"])

    def test_toggle_off_never_latches(self):
        self.assertEqual(self.run_events([("press", 0), ("release", 0.05)], tap_ms=0)[1], ["start", "stop"])

    def test_chord_release_cancels_silently(self):
        st, out = self.run_events([("press", 0), ("other", 0.1), ("release", 0.2)])
        self.assertEqual((st, out), (("idle", 0.0), ["start", "none", "cancel"]))
        _, out = self.run_events([("press", 0), ("other", 0.1), ("release", 2.0)])  # a long chord cancels too
        self.assertEqual(out[-1], "cancel")

    def test_stray_key_late_in_a_long_hold_does_not_discard(self):  # F1
        _, out = self.run_events([("press", 0), ("other", 0.2), ("release", 20)])
        self.assertEqual(out[-1], "cancel")  # a chord right at the press
        _, out = self.run_events([("press", 0), ("other", 15), ("release", 20)])
        self.assertEqual(out[-1], "stop")  # a stray key at 15 s: normal stop, dictation kept

    def test_stale_state_after_a_missed_release(self):  # F2
        st = ("idle", 0.0)
        for ev, t in (("press", 10.0), ("stop", 10.5)):
            st, _ = capture.gesture(st, ev, t)
        self.assertEqual(st, ("spent", 10.5))
        self.assertFalse(capture.stale(st, 11.5, False))
        self.assertTrue(capture.stale(st, 13.0, False))  # no release ever came
        self.assertFalse(capture.stale(st, 99.0, True))  # a live session is never stale
        self.assertFalse(capture.stale(("idle", 0.0), 99.0, False))
        for t in (11.0, 11.5, 12.0, 12.5):  # auto-repeat of a still-held key keeps refreshing spent
            st, _ = capture.gesture(st, "press", t)
        self.assertFalse(capture.stale(st, 14.0, False))
        self.assertTrue(capture.stale(st, 15.0, False))

    def test_other_key_ignored_when_not_holding(self):
        _, out = self.run_events([("other", 0), ("press", 1), ("release", 1.1), ("other", 2)])
        self.assertEqual(out, ["none", "start", "latch", "none"])

    def test_esc_while_key_down_ignores_autorepeat_until_release(self):
        st, out = self.run_events([("press", 0), ("stop", 0.5), ("press", 0.55), ("press", 0.6), ("release", 0.9), ("press", 1.5)])
        self.assertEqual(out, ["start", "none", "none", "none", "none", "start"])

    def test_autorepeat_after_latch_stop_press(self):
        _, out = self.run_events([("press", 0), ("release", 0.1), ("press", 5), ("press", 5.03), ("release", 5.1)])
        self.assertEqual(out, ["start", "latch", "stop", "none", "none"])


class Vad(unittest.TestCase):
    CFG = {"silence_ms": 2000, "no_speech_ms": 6000}

    def feed(self, seq, dt=0.1):
        st, decisions = capture.vad_new(), []
        for rms in seq:
            st, d = capture.vad_step(st, rms, dt, self.CFG)
            decisions.append(d)
        return st, decisions

    def test_finish_after_speech_then_silence(self):
        seq = [0.002] * 5 + [0.05] * 10 + [0.002] * 25
        _, d = self.feed(seq)
        self.assertEqual(d.index("finish"), 5 + 10 + 19)  # exactly 2000 ms of quiet after the last speech block
        self.assertNotIn("cancel", d)

    def test_no_speech_cancels(self):
        _, d = self.feed([0.002] * 80)
        self.assertIn(d.index("cancel"), (59, 60))  # ~6 s (float accumulation)
        self.assertNotIn("finish", d)

    def test_noisy_floor_raises_threshold(self):
        _, d = self.feed([0.002] * 2 + [0.0095] * 3 + [0.02] * 80)  # floor 0.0095: 3 x = 0.0285 > 0.02, so never speech
        self.assertIn("cancel", d)

    def test_speech_resets_quiet_run(self):
        seq = [0.002] * 5 + [0.05] * 5 + [0.002] * 15 + [0.05] * 2 + [0.002] * 15
        _, d = self.feed(seq)
        self.assertNotIn("finish", d)

    def test_low_floor_still_needs_min_rms(self):
        _, d = self.feed([0.0] * 5 + [0.005] * 70)  # above 3 x 0 but below the 0.008 floor
        self.assertIn("cancel", d)

    def test_speaking_immediately_still_detected(self):
        # speech from block 0: the floor is capped at 0.01 so a 0.05 voice stays above 3 x floor
        _, d = self.feed([0.05] * 30 + [0.002] * 25)
        self.assertIn("finish", d)
        self.assertNotIn("cancel", d)

    def test_start_cue_window_ignored(self):
        _, d = self.feed([0.2, 0.2] + [0.002] * 3 + [0.05] * 5 + [0.002] * 25)  # loud cue in the first 250 ms
        self.assertIn("finish", d)

    def test_commit_kind_and_cut_point(self):
        self.assertEqual(capture.commit_kind(9, 800), "pause")
        self.assertEqual(capture.commit_kind(31, 0), "cap")
        self.assertIsNone(capture.commit_kind(20, 100))
        rms = [0.05] * 100 + [0.04, 0.001, 0.001, 0.03] + [0.05] * 16  # 120 frames of 20 ms, a dip at frames 101-102
        self.assertIn(capture.cut_point(rms, 0.02), (103, 104, 105))  # ends the quietest 100 ms window around the dip
        self.assertEqual(capture.cut_point([0.1] * 10, 0.02), 10)

    def test_should_commit(self):
        self.assertTrue(capture.should_commit(8.0, 700))
        self.assertFalse(capture.should_commit(7.9, 2000))
        self.assertFalse(capture.should_commit(29.9, 600))


class Ignore(unittest.TestCase):
    def test_should_ignore(self):  # A3
        cfg = {"apps": {"exclude": ["Game.exe"], "pause_fullscreen": True}}
        self.assertTrue(capture.should_ignore("game.exe", cfg))  # case-insensitive
        self.assertFalse(capture.should_ignore("code.exe", cfg, 0))
        self.assertTrue(capture.should_ignore("code.exe", cfg, 3))  # exclusive-fullscreen D3D
        self.assertFalse(capture.should_ignore("code.exe", cfg, 2))  # QUNS_BUSY (a non-D3D fullscreen) is not paused
        self.assertFalse(capture.should_ignore("code.exe", {"apps": {"pause_fullscreen": False}}, 3))
        self.assertTrue(capture.should_ignore("code.exe", {}, 3))  # default on for older configs
        self.assertFalse(capture.should_ignore("", {}, 0))


class Workspace(unittest.TestCase):
    def test_title_folder(self):
        f = workspace.title_folder
        self.assertEqual(f("server.js - repos [WSL: Ubuntu-24.04] - Visual Studio Code"), "repos")
        self.assertEqual(f("\u25cf server.js \u2014 repos \u2014 Visual Studio Code"), "repos")
        self.assertEqual(f("app.py - murmur - Cursor"), "murmur")
        self.assertEqual(f("murmur - Visual Studio Code"), "murmur")
        self.assertEqual(f("notes.md - Visual Studio Code"), None)
        self.assertEqual(f("a.py - proj (Workspace) - Windsurf"), "proj")
        self.assertEqual(f("Visual Studio Code"), None)

    def test_storage_folders_and_roots(self):
        data = {"windowsState": {"lastActiveWindow": {"folder": "file:///c%3A/Users/ethan/repos/murmur"},
                                 "openedWindows": [{"folder": "vscode-remote://wsl%2Bubuntu-24.04/home/ethan/repos"}, {"workspace": {}}]},
                "backupWorkspaces": {"folders": [{"folderUri": "file:///c%3A/dev/other"}]}}
        self.assertEqual(workspace.storage_folders(data), ["c:/Users/ethan/repos/murmur", "//wsl.localhost/ubuntu-24.04/home/ethan/repos", "c:/dev/other"])

    def test_roots_for_reads_storage_json(self):
        import tempfile
        d = Path(tempfile.mkdtemp()); p = d / "Code" / "User" / "globalStorage"; p.mkdir(parents=True)
        (p / "storage.json").write_text(json.dumps({"windowsState": {"openedWindows": [
            {"folder": "vscode-remote://wsl%2Bubuntu-24.04/home/ethan/repos"}, {"folder": "file:///c%3A/x/murmur"}]}}))
        r = workspace.roots_for("code.exe", "a.js - repos [WSL: Ubuntu-24.04] - Visual Studio Code", {"code": {"roots": ["D:/extra"]}}, str(d))
        self.assertEqual([x.as_posix() for x in r], ["//wsl.localhost/ubuntu-24.04/home/ethan/repos", "D:/extra"])
        self.assertEqual(workspace.roots_for("notepad.exe", "x", {}), [])

    def test_tag_files(self):
        idx = workspace.build_index(["src/app.py", "src/util.py", "tests/util.py", "README.md", "web\\main.ts"])
        t = workspace.tag_files
        self.assertEqual(t("open app.py now", idx, "path"), "open `src/app.py` now")
        self.assertEqual(t("open app.py.", idx, "at"), "open @src/app.py.")
        self.assertEqual(t("see util.py", idx, "at"), "see util.py")  # ambiguous
        self.assertEqual(t("see nothing.py or e.g. that", idx, "at"), "see nothing.py or e.g. that")
        self.assertEqual(t("edit `app.py` please", idx, "path"), "edit `src/app.py` please")
        self.assertEqual(t("already src/app.py here", idx, "at"), "already src/app.py here")
        self.assertEqual(t("see Readme.md and main.ts", idx, "at"), "see @README.md and @web/main.ts")  # case-insensitive basename
        self.assertEqual(workspace.tag_style("chat", "Chat input"), "at")
        self.assertEqual(workspace.tag_style("chat", "Editor"), None)
        self.assertEqual(workspace.tag_style("always", "Editor"), "path")
        self.assertEqual(workspace.tag_style("always", "Ask Copilot"), "at")
        self.assertEqual(workspace.tag_style("off", "chat"), None)

    def test_chat_hint_is_whole_word(self):  # D6
        self.assertEqual(workspace.tag_style("chat", "flask_app.py, Editor Group 1"), None)
        self.assertEqual(workspace.tag_style("chat", "Tasks"), None)
        self.assertEqual(workspace.tag_style("chat", "Ask Copilot"), "at")
        self.assertEqual(workspace.tag_style("chat", "chat-input Composer"), "at")

    def test_git_ls_files_marks_the_repo_safe(self):  # D5
        from unittest import mock
        with mock.patch.object(workspace.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="a.py\nb/c.py\n")) as run:
            self.assertEqual(workspace._git_files("//wsl.localhost/x/repo"), ["a.py", "b/c.py"])
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ["git", "-c", "safe.directory=*"])

    def test_walk_skips_more_dirs_and_has_a_time_budget(self):
        import tempfile
        from unittest import mock
        d = Path(tempfile.mkdtemp())
        for sub in ("__pycache__", ".next", "target", "venv", ".mypy_cache", ".pytest_cache", "src"):
            (d / sub).mkdir(); (d / sub / "f.py").write_text("")
        self.assertEqual(workspace._walk_files(d), [str(Path("src") / "f.py")])
        ticks = iter([0.0, 5.0, 9.0, 9.0, 9.0])  # budget exhausted after the first directory
        with mock.patch.object(workspace.time, "monotonic", lambda: next(ticks, 99.0)):
            self.assertLessEqual(len(workspace._walk_files(d)), 1)

    def test_walk_index_caps_and_skips(self):
        import os, tempfile
        d = Path(tempfile.mkdtemp()); (d / "node_modules").mkdir(); (d / "node_modules" / "x.js").write_text("")
        (d / "a.py").write_text("")
        self.assertEqual(sorted(workspace._walk_files(d)), ["a.py"])


if __name__ == "__main__":
    unittest.main()


def _numpy_or_skip(tc):
    try:
        import numpy as np
        return np
    except ImportError:
        tc.skipTest("numpy not installed")


class Mic(unittest.TestCase):
    def test_ring_keeps_the_last_window(self):
        from murmur import mic
        r = mic.Ring(0.4)  # 6400 samples
        for i in range(10):
            r.push([i] * 1600)  # 100 ms frames
        got = r.take()
        self.assertEqual([f[0] for f in got], [6, 7, 8, 9])  # 400 ms = the last four frames
        self.assertEqual(r.take(), [])  # emptied

    def test_save_recording_prunes(self):
        import tempfile
        np = _numpy_or_skip(self)
        from murmur import store
        self.addCleanup(setattr, store, "HOME", store.HOME)  # never leak a temp HOME into later tests
        store.HOME = Path(tempfile.mkdtemp())
        for i in range(4):
            store.save_recording(np.zeros(1600, np.float32), f"raw {i}", keep=2)
            import time; time.sleep(1.05)  # distinct timestamps (seconds)
        wavs = sorted((store.HOME / "recordings").glob("*.wav"))
        self.assertEqual(len(wavs), 2)
        self.assertEqual(store.load_recording(wavs[-1])[1], "raw 3")  # sealed on Windows


class Hallucination(unittest.TestCase):
    def test_short_output_on_silence_is_dropped(self):
        self.assertTrue(capture.hallucinated("Yeah.", 0.0))
        self.assertTrue(capture.hallucinated("Thank you.", 0.1))
        self.assertFalse(capture.hallucinated("Yeah.", 0.6))  # real speech heard
        self.assertFalse(capture.hallucinated("we can always adjust later", 0.0))  # quiet mic, real words: kept
        self.assertFalse(capture.hallucinated("", 0.0))


class Conditioning(unittest.TestCase):
    def setUp(self):
        self.np = _numpy_or_skip(self)
        from murmur import audio
        self.audio = audio

    def tone(self, seconds, amp):
        np = self.np
        t = np.arange(int(16000 * seconds)) / 16000
        return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def test_trim_pause_level_pad(self):
        np, A = self.np, self.audio
        quiet = (np.random.default_rng(0).standard_normal(16000 * 3) * 0.0005).astype(np.float32)  # 3 s of hiss
        x = np.concatenate([quiet[:16000 * 2], self.tone(0.5, 0.02), quiet[:16000 * 3], self.tone(0.5, 0.02), quiet[:16000 * 2]])
        y = A.condition(x)
        # 2 s head + 3 s pause + 2 s tail shrink to 0.25 pad + ~0.25 + 1.0 pause + ~0.25 + 0.25 pad around 1 s of speech
        self.assertLess(len(y) / 16000, 4.1); self.assertGreater(len(y) / 16000, 2.2)
        self.assertTrue(np.all(y[: int(0.25 * 16000)] == 0))  # lead-in pad
        self.assertGreater(np.abs(y).max(), 0.05)  # the -34 dBFS tone counts as a quiet mic: boosted
        self.assertLessEqual(np.abs(y).max(), 0.95)  # peak cap
        self.assertFalse(A.has_speech(quiet)); self.assertTrue(A.has_speech(x))  # what the decode path actually uses
        self.assertEqual(len(A.condition(quiet)), len(quiet))  # silence: untouched
        loud = self.tone(1.0, 0.5)
        self.assertAlmostEqual(float(np.abs(A.condition(loud)).max()), 0.5, places=2)  # never lowered


class SileroMode(unittest.TestCase):
    CFG = {"silence_ms": 2000, "no_speech_ms": 6000}

    def run_probs(self, probs, dt=0.032):
        s, out = capture.vad_new(), []
        for p in probs:
            s, d = capture.vad_step(s, 0.0, dt, self.CFG, p)  # rms 0: only the probability decides
            out.append(d)
        return s, out

    def test_speech_from_the_first_block_is_heard(self):  # the energy floor window would have swallowed this
        s, out = self.run_probs([0.9] * 60)  # ~1.9 s of speech straight away
        self.assertTrue(s["speech"]); self.assertGreater(s["speech_s"], 1.5); self.assertTrue(all(d is None for d in out))

    def test_hysteresis_and_finish(self):
        s, _ = self.run_probs([0.9] * 20 + [0.4] * 10)  # 0.4 right after speech still counts (>= KEEP_P)
        self.assertEqual(s["quiet_ms"], 0.0)
        s, out = self.run_probs([0.9] * 20 + [0.1] * 70)  # 2.24 s of non-speech after speech
        first = next(i for i, d in enumerate(out) if d)  # the session acts on the first decision
        self.assertEqual(out[first], "finish"); self.assertAlmostEqual((first - 19) * 0.032, 2.0, delta=0.05)

    def test_cancel_without_speech(self):
        _, out = self.run_probs([0.2] * 200)
        self.assertIn("cancel", out)


class SileroStream(unittest.TestCase):
    def setUp(self):
        self.np = _numpy_or_skip(self)
        from murmur import vad
        self.vad = vad

    def test_hops_context_and_max(self):
        np, V = self.np, self.vad
        calls = []

        class Sess:
            def run(self, names, feeds):
                calls.append(feeds["input"].shape)
                return np.array([[0.2 + 0.1 * len(calls)]], np.float32), feeds["state"] + 1

        s = V.Silero(Sess())
        self.assertEqual(s.feed(np.zeros(300, np.float32)), 0.0)  # no full hop yet
        self.assertAlmostEqual(s.feed(np.zeros(300, np.float32)), 0.3, places=5)  # one hop
        self.assertEqual(calls, [(1, V.CTX + V.HOP)])  # 64 samples of context + 512
        self.assertAlmostEqual(s.feed(np.zeros(1100, np.float32)), 0.5, places=5)  # two hops: the max
        self.assertEqual(float(s.state[0, 0, 0]), 3.0)  # state carried across hops
        s.reset(); self.assertEqual(s.prob, 0.0); self.assertEqual(float(s.state.sum()), 0.0)

    def test_safe_disables_on_error(self):
        np = self.np

        class Boom:
            def run(self, names, feeds):
                raise RuntimeError("onnx")

        s = self.vad.Silero(Boom())
        self.assertIsNone(s.safe(np.zeros(600, np.float32)))  # the error is swallowed in the callback
        self.assertTrue(s.dead); self.assertIsNone(s.safe(np.zeros(600, np.float32)))
        s.reset(); self.assertFalse(s.dead)  # the next session tries again

    def test_missing_model_falls_back(self):
        import tempfile
        self.assertIsNone(self.vad.load(tempfile.mkdtemp()))


class Stages(unittest.TestCase):
    def test_stage_seconds_and_total(self):
        m = {"release": 10.0, "stopped": 10.35, "heard": 10.62, "cleaned": 10.85, "pasted": 10.9}
        self.assertEqual(capture.stages(m), {"tail": 0.35, "stt": 0.27, "clean": 0.23, "paste": 0.05, "total": 0.9})
        self.assertEqual(capture.stages({"release": 1.0, "stopped": 1.3}), {"tail": 0.3})  # cancelled early: partial, no total
