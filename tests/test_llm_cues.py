import io, sys, unittest, wave
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urllib.error
from murmur import llm
try:
    import numpy as np
    from murmur import cues
except ImportError:  # WSL python has no numpy: cue tests run in the Windows venv
    np = cues = None


def cfg(fallback=True):
    return {"llm": {"provider": "openai", "url": "http://p/v1", "model": "m", "fallback": fallback},
            "llm_fallback": {"provider": "anthropic", "url": "https://a/v1/messages", "model": "h"}}


def ok(url, *a, **k):
    return {"choices": [{"message": {"content": "P"}}]} if url == "http://p/v1" else {"content": [{"text": "F"}]}


class LlmFallback(unittest.TestCase):
    def setUp(self):
        llm._down_until = 0.0; llm._local.route = "primary"

    def test_primary_ok_no_fallback_call(self):
        with mock.patch.object(llm, "_post", side_effect=ok) as p:
            self.assertEqual(llm.chat(cfg(), "s", "u"), "P")
        self.assertEqual([c.args[0] for c in p.call_args_list], ["http://p/v1"])
        self.assertEqual(llm.route(), "primary")

    def test_primary_down_uses_fallback_and_breaker(self):
        calls, t = [], [100.0]

        def post(url, *a):
            calls.append(url)
            if url == "http://p/v1":
                raise urllib.error.URLError("refused")
            return ok(url)
        with mock.patch.object(llm, "_post", side_effect=post), mock.patch.object(llm.time, "monotonic", lambda: t[0]):
            self.assertEqual(llm.chat(cfg(), "s", "u"), "F")
            self.assertEqual(llm.route(), "fallback")
            t[0] = 130.0  # inside the 60 s window: primary skipped
            llm.chat(cfg(), "s", "u")
            self.assertEqual(calls, ["http://p/v1", "https://a/v1/messages", "https://a/v1/messages"])
            t[0] = 161.0  # window over: primary retried
            llm.chat(cfg(), "s", "u")
            self.assertEqual(calls[3], "http://p/v1")

    def test_route_is_per_thread(self):  # D12: another thread's call must not change this thread's route
        import threading
        with mock.patch.object(llm, "_post", side_effect=ok):
            llm.chat(cfg(), "s", "u")
        seen = []

        def other():
            llm._local.route = "fallback"; seen.append(llm.route())
        t = threading.Thread(target=other); t.start(); t.join()
        self.assertEqual((llm.route(), seen), ("primary", ["fallback"]))

    def test_5xx_falls_back_4xx_raises(self):
        err = lambda c: urllib.error.HTTPError("u", c, "x", {}, None)
        with mock.patch.object(llm, "_post", side_effect=lambda u, *a: (_ for _ in ()).throw(err(503)) if u == "http://p/v1" else ok(u)):
            self.assertEqual(llm.chat(cfg(), "s", "u"), "F")
        llm._down_until = 0.0
        with mock.patch.object(llm, "_post", side_effect=err(401)), self.assertRaises(urllib.error.HTTPError):
            llm.chat(cfg(), "s", "u")

    def test_both_fail_raises(self):
        with mock.patch.object(llm, "_post", side_effect=urllib.error.URLError("x")), self.assertRaises(urllib.error.URLError):
            llm.chat(cfg(), "s", "u")

    def test_fallback_disabled_raises(self):
        with mock.patch.object(llm, "_post", side_effect=TimeoutError()) as p, self.assertRaises(TimeoutError):
            llm.chat(cfg(False), "s", "u")
        self.assertEqual(p.call_count, 1)


@unittest.skipUnless(np, "numpy not installed")
class Cues(unittest.TestCase):
    def test_tone_wav(self):
        data = cues.tone_wav([660, 880], 70, 0.25)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 22050))
            self.assertEqual(w.getnframes(), 2 * int(22050 * 0.07))
            pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
        self.assertGreater(np.abs(pcm).max(), 0)
        loud = np.frombuffer(cues.tone_wav([660], 70, 0.5)[44:], "<i2")
        quiet = np.frombuffer(cues.tone_wav([660], 70, 0.25)[44:], "<i2")
        self.assertGreater(np.abs(loud).max(), 1.9 * np.abs(quiet).max())

    def test_all_cues_short(self):
        for name, (freqs, ms) in cues.CUES.items():
            self.assertLessEqual(len(freqs) * ms, 160, name)

    def test_play_uses_a_wav_file_not_memory(self):  # D2: SND_MEMORY|SND_ASYNC raises on Windows
        import tempfile
        from murmur import store
        store.HOME = Path(tempfile.mkdtemp())
        ws = mock.MagicMock(SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4, SND_MEMORY=8)
        with mock.patch.dict(sys.modules, {"winsound": ws}):
            cues.play("done", {"ui": {"sounds": True, "sound_volume": 0.5}})
        path, flags = ws.PlaySound.call_args.args
        self.assertEqual(flags, 1 | 2 | 4)
        self.assertTrue(Path(path).exists() and Path(path).parent == store.HOME / "cues")
        with wave.open(path) as w:
            self.assertEqual(w.getframerate(), 22050)

    def test_cue_path_writes_atomically(self):  # F4
        import tempfile
        from murmur import store
        store.HOME = Path(tempfile.mkdtemp())
        p = cues.cue_path("start", 0.25)
        self.assertTrue(p.exists())
        self.assertEqual([x.name for x in p.parent.iterdir()], ["start-0.087.wav"])  # no .tmp left behind

    def test_play_failure_is_logged_not_swallowed(self):
        ws = mock.MagicMock(); ws.PlaySound.side_effect = RuntimeError("boom")
        with mock.patch.dict(sys.modules, {"winsound": ws}), self.assertLogs("murmur", "WARNING"):
            cues.play("done", {"ui": {"sounds": True, "sound_volume": 0.3}})

    def test_play_noop_when_disabled(self):
        with mock.patch.dict(sys.modules, {"winsound": mock.MagicMock()}) as m:
            cues.play("start", {"ui": {"sounds": False}})
            cues.play("start", {"ui": {"sounds": True, "start_sound": False}})  # start muted on its own
            sys.modules["winsound"].PlaySound.assert_not_called()
            cues.play("start", {"ui": {"sounds": True}})
            sys.modules["winsound"].PlaySound.assert_called_once()
        cues.play("start", {})  # no winsound on Linux: still silent, no raise

    def test_single_instance(self):
        fake = {m: mock.MagicMock() for m in ("keyboard", "pyperclip", "win32api", "win32gui", "win32process")}
        with mock.patch.dict(sys.modules, fake):
            from murmur import win
            import os
            self.assertTrue(callable(win.single_instance))
            if os.name != "nt":
                self.assertTrue(win.single_instance())


if __name__ == "__main__":
    unittest.main()
