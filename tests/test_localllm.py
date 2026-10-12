import errno, hashlib, io, sys, tempfile, threading, time, unittest, zipfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urllib.error
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


DATA = bytes(range(256)) * 400  # 102,400 bytes standing in for the GGUF


class FakeResp:
    def __init__(self, body, status=200, start=None):
        self.body, self.status, self.fp = body, status, io.BytesIO(body)
        self.headers = {"Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(DATA)}"} if start is not None else {}

    def read(self, n=-1):
        return self.fp.read(10000 if n < 0 else min(n, 10000))  # short reads like a socket

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Download(Base):
    def setUp(self):
        super().setUp()
        for name, val in (("MODEL_SIZE", len(DATA)), ("MODEL_SHA256", hashlib.sha256(DATA).hexdigest())):
            p = mock.patch.object(localllm, name, val); p.start(); self.addCleanup(p.stop)
        self.reqs = []
        self.free = lambda p: mock.Mock(free=10**12)

    def opener(self, honour_range=True, fail=None, data=DATA):
        def op(req, timeout=None):
            self.reqs.append(req.get_header("Range"))
            if fail:
                raise fail
            rng = req.get_header("Range")
            if rng and honour_range:
                start = int(rng.split("=")[1].rstrip("-"))
                return FakeResp(data[start:], 206, start)
            return FakeResp(data)
        return op

    def run_dl(self, **kw):
        kw.setdefault("opener", self.opener()); kw.setdefault("disk_usage", self.free)
        return localllm.download_model({}, **kw)

    def test_fresh_download_verifies_and_renames_atomically(self):
        seen = []
        out = self.run_dl(progress_cb=lambda d, t: seen.append((d, t)))
        self.assertEqual(out, self.home / "models" / localllm.MODEL_FILE)
        self.assertEqual(out.read_bytes(), DATA)
        self.assertFalse(localllm.part_path().exists())
        self.assertEqual((seen[0], seen[-1]), ((0, len(DATA)), (len(DATA), len(DATA))))
        self.assertEqual(self.reqs, [None])

    def test_progress_is_throttled(self):
        seen = []
        self.run_dl(progress_cb=lambda d, t: seen.append(d))
        self.assertLessEqual(len(seen), 4)  # first, (maybe one tick), last: nowhere near one call per chunk

    def test_resumes_from_the_part_with_a_range_request(self):
        localllm.part_path().parent.mkdir(parents=True); localllm.part_path().write_bytes(DATA[:30000])
        seen = []
        out = self.run_dl(progress_cb=lambda d, t: seen.append(d))
        self.assertEqual(self.reqs, ["bytes=30000-"])
        self.assertEqual(seen[0], 30000)
        self.assertEqual(out.read_bytes(), DATA)

    def test_server_answering_200_restarts_clean(self):
        localllm.part_path().parent.mkdir(parents=True); localllm.part_path().write_bytes(b"junk" * 100)
        out = self.run_dl(opener=self.opener(honour_range=False))
        self.assertEqual(out.read_bytes(), DATA)

    def test_416_restarts_clean_and_an_oversized_part_is_dropped(self):
        localllm.part_path().parent.mkdir(parents=True); localllm.part_path().write_bytes(b"x" * (len(DATA) + 5))
        self.assertEqual(self.run_dl().read_bytes(), DATA)
        self.assertEqual(self.reqs, [None])
        (self.home / "models" / localllm.MODEL_FILE).unlink(); localllm.part_path().write_bytes(b"x" * 10); self.reqs.clear()
        calls = []

        def op(req, timeout=None):
            calls.append(req.get_header("Range"))
            if req.get_header("Range"):
                raise urllib.error.HTTPError(req.full_url, 416, "range", {}, None)
            return FakeResp(DATA)
        self.assertEqual(self.run_dl(opener=op).read_bytes(), DATA)
        self.assertEqual(calls, ["bytes=10-", None])

    def test_cancel_keeps_the_part_then_resumes(self):
        ev = threading.Event()
        n = []

        def cb(d, t):
            n.append(d)
            if d > 0:
                ev.set()
        with mock.patch.object(localllm.time, "monotonic", side_effect=lambda c=iter(range(0, 10**6, 1)): next(c)):  # every chunk reports
            with self.assertRaises(localllm.Cancelled):
                self.run_dl(progress_cb=cb, cancel=ev)
        got = localllm.part_bytes()
        self.assertTrue(0 < got < len(DATA))
        self.assertFalse((self.home / "models" / localllm.MODEL_FILE).exists())
        self.reqs.clear()
        self.assertEqual(self.run_dl().read_bytes(), DATA)
        self.assertEqual(self.reqs, [f"bytes={got}-"])

    def test_checksum_mismatch_deletes_the_part(self):
        with self.assertRaisesRegex(localllm.DownloadError, "corrupted"):
            self.run_dl(opener=self.opener(data=bytes(len(DATA))))
        self.assertFalse(localllm.part_path().exists())
        self.assertFalse((self.home / "models" / localllm.MODEL_FILE).exists())

    def test_disk_space_refusal_names_the_drive_and_downloads_nothing(self):
        with self.assertRaisesRegex(localllm.DownloadError, r"Not enough disk space on .*\(need [\d.]+ GB, 0\.1 GB free\)"):
            self.run_dl(disk_usage=lambda p: mock.Mock(free=100_000_000))
        self.assertEqual(self.reqs, [])

    def test_readable_network_errors(self):
        with self.assertRaisesRegex(localllm.DownloadError, "reach huggingface.co"):
            self.run_dl(opener=self.opener(fail=urllib.error.URLError("dns")))
        with self.assertRaisesRegex(localllm.DownloadError, "Hugging Face answered 503"):
            self.run_dl(opener=self.opener(fail=urllib.error.HTTPError("u", 503, "x", {}, None)))

    def test_a_truncated_stream_keeps_the_part_and_says_so(self):
        with self.assertRaisesRegex(localllm.DownloadError, "ended early"):
            self.run_dl(opener=self.opener(data=DATA[:5000]))
        self.assertEqual(localllm.part_bytes(), 5000)

    def test_a_complete_part_is_verified_and_renamed_without_a_request(self):
        localllm.part_path().parent.mkdir(parents=True); localllm.part_path().write_bytes(DATA)
        self.assertEqual(self.run_dl().read_bytes(), DATA)
        self.assertEqual(self.reqs, [])

    def test_disk_full_while_writing_is_readable(self):
        class Boom(FakeResp):
            def read(self, n=-1):
                raise OSError(errno.ENOSPC, "No space left")
        with self.assertRaisesRegex(localllm.DownloadError, "disk space"):
            self.run_dl(opener=lambda req, timeout=None: Boom(DATA))

    def test_find_model_ignores_part_files(self):
        localllm.part_path().parent.mkdir(parents=True); localllm.part_path().write_bytes(b"x")
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertIsNone(localllm.find_model({}))

    def test_pins_are_sane(self):
        self.assertTrue(localllm.MODEL_URL.endswith(f"/resolve/{localllm.MODEL_REV}/{localllm.MODEL_FILE}"))


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


class Streams(unittest.TestCase):
    def test_windowed_exe_without_std_streams_gets_devnull(self):
        with mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in ("numpy", "onnx_asr")}):
            from murmur import __main__ as m
        import logging
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            m.ensure_std_streams()
            print("ok"); sys.stderr.write("x")  # would raise AttributeError on None
            h = logging.StreamHandler(sys.stdout)
            h.emit(logging.LogRecord("t", 20, "p", 1, "msg", None, None))
            sys.stdout.close(); sys.stderr.close()
        with mock.patch.object(sys, "stdout", io.StringIO()) as o:
            m.ensure_std_streams()
            self.assertIs(sys.stdout, o)  # a real stream is left alone


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
