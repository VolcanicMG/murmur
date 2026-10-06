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
