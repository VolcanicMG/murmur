import hashlib, io, sys, tempfile, threading, time, unittest, zipfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import llm, localllm, store


def zbytes(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        p = mock.patch.object(store, "HOME", self.home); p.start(); self.addCleanup(p.stop)
        localllm._proc = None; localllm._crashes = 0; localllm._failures, localllm._retry_at = 0, 0.0
        self.addCleanup(self.tmp.cleanup)

    def fake_release(self, files, sha=None):
        data = zbytes(files)
        sha = sha or hashlib.sha256(data).hexdigest()
        asset = localllm.ASSETS["vulkan"][0][0]

        def fetch(url, dest, progress=None):
            Path(dest).write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        return mock.patch.dict(localllm.ASSETS, {"vulkan": [(asset, sha)]}), mock.patch.object(localllm, "_fetch", side_effect=fetch)


class Binary(Base):
    def test_downloads_extracts_and_never_redownloads(self):
        a, f = self.fake_release({"llama-server.exe": "x", "ggml.dll": "y"})
        with a, f as fetch:
            exe = localllm.ensure_binary({})
            self.assertTrue(exe.is_file())
            self.assertEqual(localllm.ensure_binary({}), exe)
            self.assertEqual(fetch.call_count, 1)
        self.assertFalse(list((self.home / "runtime").glob("*.partial")))

    def test_checksum_mismatch_refuses_and_installs_nothing(self):
        a, f = self.fake_release({"llama-server.exe": "x"}, sha="0" * 64)
        with a, f, self.assertRaisesRegex(RuntimeError, "checksum"):
            localllm.ensure_binary({})
        self.assertEqual(list((self.home / "runtime").iterdir()), [])

    def test_path_traversal_rejected(self):
        a, f = self.fake_release({"llama-server.exe": "x", "../evil.txt": "z"})
        with a, f, self.assertRaisesRegex(RuntimeError, "unsafe"):
            localllm.ensure_binary({})
        self.assertFalse((self.home / "runtime" / "evil.txt").exists())

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            localllm.ensure_binary({"builtin": {"backend": "metal"}})


class Model(Base):
    def touch(self, p):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"g")
        return p

    def test_order_config_then_murmur_then_lmstudio(self):
        lm = self.touch(self.home / ".lmstudio" / "models" / "c" / "gemma-4-E4B-it-Q6_K.gguf")
        self.touch(self.home / ".lmstudio" / "models" / "c" / "mmproj-gemma-4-E4B-it-BF16.gguf")
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(localllm.find_model({}), lm)
            mine = self.touch(self.home / "models" / "m.gguf")
            self.assertEqual(localllm.find_model({}), mine)
            cfgd = self.touch(self.home / "other.gguf")
            self.assertEqual(localllm.find_model({"builtin": {"model": str(cfgd)}}), cfgd)
            self.assertEqual(localllm.find_model({"builtin": {"model": str(self.home / "nope.gguf")}}), mine)

    def test_none_when_absent(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertIsNone(localllm.find_model({}))


class Process(Base):
    def popen(self, alive=True):
        p = mock.Mock(); p.poll.return_value = None if alive else 1; p.returncode = 1; p.pid = 7
        return p

    def patches(self, healthy=True, proc=None):
        proc = proc or self.popen()
        return (mock.patch.object(localllm, "ensure_binary", return_value=self.home / "llama-server.exe"),
                mock.patch.object(localllm, "find_model", return_value=self.home / "m.gguf"),
                mock.patch.object(localllm.subprocess, "Popen", return_value=proc),
                mock.patch.object(localllm, "_kill_with_parent"),
                mock.patch.object(localllm, "_healthy", return_value=healthy)), proc

    def test_start_reuses_running_server_and_stop_is_idempotent(self):
        ps, proc = self.patches()
        with ps[0], ps[1], ps[2] as popen, ps[3], ps[4], mock.patch.object(localllm.atexit, "register"):
            url = localllm.start({"builtin": {"port": 5555}})
            self.assertEqual(url, "http://127.0.0.1:5555/v1")
            self.assertEqual(localllm.start({}), url)
            self.assertEqual(popen.call_count, 1)
            cmd = popen.call_args.args[0]
            self.assertIn("127.0.0.1", cmd); self.assertEqual(cmd[cmd.index("--reasoning") + 1], "off")
            localllm.stop(); localllm.stop()
            proc.terminate.assert_called_once()
            self.assertFalse(localllm.status()["running"])

    def test_start_timeout_kills_and_raises(self):
        ps, proc = self.patches(healthy=False)
        with ps[0], ps[1], ps[2], ps[3], ps[4], mock.patch.object(localllm.atexit, "register"), \
                self.assertRaisesRegex(RuntimeError, "not healthy"):
            localllm.start({"builtin": {"start_timeout": 0}})
        proc.terminate.assert_called()

    def test_no_model_raises(self):
        with mock.patch.object(localllm, "ensure_binary"), mock.patch.object(localllm, "find_model", return_value=None), \
                mock.patch.object(localllm.atexit, "register"), self.assertRaisesRegex(RuntimeError, "no model"):
            localllm.start({})

    def test_restarts_once_then_gives_up(self):
        ps, proc = self.patches()
        with ps[0], ps[1], ps[2] as popen, ps[3], ps[4], mock.patch.object(localllm.atexit, "register"):
            localllm.start({})
            proc.poll.return_value = 1  # crash 1: restarted
            localllm.start({})
            self.assertEqual(popen.call_count, 2)
            proc.poll.return_value = 1  # crash 2: refused
            with self.assertRaisesRegex(RuntimeError, "twice"):
                localllm.start({})


class Routing(unittest.TestCase):
    def setUp(self):
        llm._down_until = 0.0

    def test_builtin_provider_uses_started_server(self):
        cfg = {"llm": {"provider": "builtin"}}
        reply = {"choices": [{"message": {"content": "ok"}}]}
        with mock.patch.object(localllm, "ready_url", return_value="http://127.0.0.1:9/v1") as st, \
                mock.patch.object(llm, "_post", return_value=reply) as post:
            self.assertEqual(llm.chat(cfg, "s", "u"), "ok")
        st.assert_called_once_with(cfg)
        url, body = post.call_args.args[:2]
        self.assertEqual(url, "http://127.0.0.1:9/v1/chat/completions")
        self.assertEqual(body["reasoning_effort"], "none")

    def test_builtin_failure_falls_back(self):
        cfg = {"llm": {"provider": "builtin", "fallback": True},
               "llm_fallback": {"provider": "anthropic", "url": "http://a", "model": "h"}}
        with mock.patch.object(localllm, "ready_url", side_effect=RuntimeError("no model")), \
                mock.patch.object(llm, "_post", return_value={"content": [{"text": "F"}]}):
            self.assertEqual(llm.chat(cfg, "s", "u"), "F")
        self.assertEqual(llm.route(), "fallback")

    def test_builtin_as_fallback_provider(self):
        cfg = {"llm": {"provider": "openai", "url": "http://p", "model": "m", "fallback": True},
               "llm_fallback": {"provider": "builtin"}}
        reply = {"choices": [{"message": {"content": "B"}}]}

        def post(url, *a):
            if url == "http://p":
                raise llm.urllib.error.URLError("refused")
            return reply
        with mock.patch.object(localllm, "ready_url", return_value="http://127.0.0.1:9/v1"), mock.patch.object(llm, "_post", side_effect=post):
            self.assertEqual(llm.chat(cfg, "s", "u"), "B")

    def test_a_start_in_progress_goes_to_the_fallback_without_the_breaker(self):  # R5
        cfg = {"llm": {"provider": "builtin", "fallback": True}, "llm_fallback": {"provider": "anthropic", "url": "http://a", "model": "h"}}
        with mock.patch.object(localllm, "ready_url", side_effect=RuntimeError("bundled model starting")), \
                mock.patch.object(llm, "_post", return_value={"content": [{"text": "F"}]}):
            self.assertEqual(llm.chat(cfg, "s", "u"), "F")
        self.assertEqual(llm._down_until, 0.0)  # a start is not an outage: the next call tries the primary again

    def test_dictations_do_not_queue_behind_the_warm_up(self):
        cfg = {"llm": {"provider": "builtin"}}
        store.runtime["llm_starting"] = True
        try:
            with mock.patch.object(localllm, "ready_url", return_value="http://127.0.0.1:9/v1"), \
                    mock.patch.object(llm, "_post", return_value={"choices": [{"message": {"content": "ok"}}]}):
                with self.assertRaises(llm.Starting):
                    llm.chat(cfg, "s", "u")  # a dictation while the warm-up runs
                out = []
                import threading
                t = threading.Thread(target=lambda: out.append(llm.chat(cfg, "s", "u")), name=localllm.WARMUP_THREAD)
                t.start(); t.join()
                self.assertEqual(out, ["ok"])  # the warm-up itself is allowed through
        finally:
            store.runtime["llm_starting"] = False


class Switch(Base):
    def test_enabled_defaults_on_and_off_means_off(self):  # the module default matches DEFAULT_CONFIG
        self.assertTrue(localllm.enabled({}))
        self.assertTrue(localllm.enabled({"builtin": {"enabled": True}}))
        self.assertFalse(localllm.enabled({"builtin": {"enabled": False}}))

    def test_a_disabled_bundled_model_goes_to_the_fallback_and_never_starts(self):
        cfg = {"llm": {"provider": "builtin", "fallback": True}, "builtin": {"enabled": False},
               "llm_fallback": {"provider": "anthropic", "url": "http://a", "model": "h"}}
        with mock.patch.object(localllm, "ready_url") as ru, mock.patch.object(llm, "_post", return_value={"content": [{"text": "F"}]}):
            self.assertEqual(llm.chat(cfg, "s", "u"), "F")
        ru.assert_not_called()
        self.assertEqual(llm.route(), "fallback")

    def test_start_llm_ignores_a_disabled_bundled_model(self):
        import sys
        with mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in ("numpy", "onnx_asr")}):
            from murmur import __main__ as m
        on = {"llm": {"provider": "builtin"}}
        off = {**on, "builtin": {"enabled": False}}
        self.assertTrue(m._builtin_wanted(on))
        self.assertFalse(m._builtin_wanted(off))
        self.assertFalse(m._builtin_wanted({"llm": {"provider": "openai"}}))
        with mock.patch.object(m.threading, "Thread") as th, mock.patch.object(m.brain, "_chat"), mock.patch.object(m.subprocess, "run"), \
                mock.patch.object(store, "find_lms", return_value=None):
            m.start_llm({"llm": {"provider": "builtin", "lms": "", "lms_load": "", "model": "m"}, "builtin": {"enabled": False}})
        th.assert_not_called()


class NonBlocking(Base):
    def test_ready_url_never_waits_for_a_start_in_progress(self):  # R5
        started = threading.Event(), threading.Event()

        def slow_spawn(cfg):
            started[0].set(); started[1].wait(5)  # holds the lock like a real health wait
        with mock.patch.object(localllm, "_spawn", side_effect=slow_spawn), mock.patch.object(localllm.atexit, "register"):
            t = threading.Thread(target=lambda: localllm.start({}), daemon=True)
            t.start(); started[0].wait(5)
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as cm:
                localllm.ready_url({})
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertIn("starting", str(cm.exception))
            started[1].set(); t.join(5)

    def test_not_started_kicks_a_background_start_and_raises(self):
        with mock.patch.object(localllm, "_safe_start") as safe:
            with self.assertRaises(RuntimeError):
                localllm.ready_url({})
            time.sleep(0.1)
        safe.assert_called_once()

    def test_spawn_failures_back_off_instead_of_respawning(self):  # R11
        with mock.patch.object(localllm, "_spawn", side_effect=RuntimeError("no model")) as sp, mock.patch.object(localllm.atexit, "register"):
            with self.assertRaises(RuntimeError):
                localllm.start({})
            with self.assertRaises(RuntimeError) as cm:
                localllm.start({})  # inside the back-off window: no second spawn
        self.assertEqual(sp.call_count, 1)
        self.assertIn("trying again", str(cm.exception))
        self.assertEqual(localllm._failures, 1)
        localllm._retry_at = 0.0
        with mock.patch.object(localllm, "_spawn") as sp2, mock.patch.object(localllm.atexit, "register"):
            localllm.start({})
        self.assertEqual((localllm._failures, sp2.call_count), (0, 1))  # a success clears it

    def test_log_is_truncated_at_each_spawn(self):
        src = Path(localllm.__file__).read_text(encoding="utf-8")
        self.assertIn('open(store.HOME / "llama-server.log", "wb")', src)


if __name__ == "__main__":
    unittest.main()
