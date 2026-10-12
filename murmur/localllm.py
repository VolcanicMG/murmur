"""Bundled LLM: a managed llama.cpp `llama-server` child process (OpenAI-compatible, localhost only).
The runtime zip(s) are fetched on demand to ~/.murmur/runtime (SHA-256 pinned below); the model is never downloaded implicitly."""
import atexit, ctypes, errno, glob, hashlib, http.client, logging, os, re, shutil, socket, subprocess, threading, time, urllib.error, urllib.request, zipfile
from ctypes import wintypes
from pathlib import Path

from . import store

log = logging.getLogger("murmur")
TAG = "b11456"
REL = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/"
# backend -> [(asset, sha256)]; digests are the ones GitHub publishes for the release assets
ASSETS = {
    "vulkan": [(f"llama-{TAG}-bin-win-vulkan-x64.zip", "7c17593521854b6567d0c8037d13a207909bad7557905b4d8c01563031c575a1")],
    "cpu": [(f"llama-{TAG}-bin-win-cpu-x64.zip", "0a7452ae5692479a030d8e33fcc2dd92b91a60e6bbece0c74b1aa57c6f8d9c85")],
    "cuda": [(f"llama-{TAG}-bin-win-cuda-13.4-x64.zip", "0bb100a567a45b79198e9e548c20d646f84f862a1c001bce3fb24ec4aa79f270"),
             ("cudart-llama-bin-win-cuda-13.4-x64.zip", "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668")],
}
MODEL_REPO, MODEL_FILE = "lmstudio-community/gemma-4-E4B-it-GGUF", "gemma-4-E4B-it-Q6_K.gguf"  # Apache-2.0, not gated
# pinned like ASSETS: bumping the model = update all three (HF API: /api/models/{MODEL_REPO}/tree/main shows size and lfs.oid)
MODEL_REV = "99210a71094c6aa8559f764e0cadedd7d89de94d"
MODEL_SIZE = 6217260864
MODEL_SHA256 = "17f0e51b02f755f85c5e5c865dd44f86306cccaa2acfa2ef4a90456acf526c54"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REV}/{MODEL_FILE}"
DISK_MARGIN = 500 * 1000 * 1000  # free space kept beyond the file itself
DEFAULTS = {"enabled": True, "backend": "vulkan", "model": "", "gpu_layers": -1, "ctx": 8192, "port": 0, "start_timeout": 120}
EXE = "llama-server.exe"

_lock = threading.RLock()
_proc = None  # subprocess.Popen of the running server
_url = ""
_crashes = 0
_job = None  # Windows Job Object handle (kept alive for the process lifetime)
_registered = False
_failures, _retry_at = 0, 0.0  # spawn failures (no model, bad runtime, never healthy) back off instead of respawning forever
WARMUP_THREAD = "builtin-warmup"  # the one thread allowed to talk to the model while it is still starting


def _cfg(cfg):
    """[builtin] merged over DEFAULTS."""
    return {**DEFAULTS, **(cfg.get("builtin") or {})}


def enabled(cfg):
    """[builtin] enabled (default on): the switch for the bundled model. Off = never started, and requests to provider "builtin"
    fail over to the fallback."""
    return bool(_cfg(cfg)["enabled"])


def runtime_dir(cfg):
    """Install dir of the pinned release for the configured backend."""
    return store.HOME / "runtime" / f"llama-{TAG}-{_cfg(cfg)['backend']}"


def _fetch(url, dest, progress=None):
    """Stream url to dest, return its SHA-256 hex. progress(done_bytes, total_bytes)."""
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk); h.update(chunk); done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


def _safe_extract(zpath, dest):
    """Extract zip into dest, refusing any member that would land outside it."""
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            if not os.path.realpath(os.path.join(root, m.filename)).startswith(root + os.sep):
                raise RuntimeError(f"unsafe path in archive: {m.filename}")
        z.extractall(root)


def ensure_binary(cfg, progress=None):
    """Path to llama-server.exe for the configured backend, downloading + SHA-256-verifying + extracting the pinned
    release into ~/.murmur/runtime on first use. Never re-downloads when present; a checksum mismatch raises and installs nothing."""
    b = _cfg(cfg)["backend"]
    if b not in ASSETS:
        raise ValueError(f"unknown [builtin] backend {b!r} (use vulkan, cuda or cpu)")
    final = runtime_dir(cfg)
    if (final / EXE).is_file():
        return final / EXE
    tmp = final.with_name(final.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        for name, sha in ASSETS[b]:
            z = tmp / name
            log.info("downloading %s", name)
            got = _fetch(REL + name, z, progress)
            if got != sha:
                raise RuntimeError(f"checksum mismatch for {name}: expected {sha}, got {got}")
            _safe_extract(z, tmp)
            z.unlink()
        if not (tmp / EXE).is_file():
            raise RuntimeError(f"{EXE} missing from the downloaded release")
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final / EXE


def find_model(cfg):
    """GGUF to serve: [builtin] model, else ~/.murmur/models/*.gguf, else LM Studio's gemma-4 e4b. None if nothing is found."""
    p = _cfg(cfg)["model"]
    if p and Path(p).is_file():
        return Path(p)
    ours = sorted(g for g in glob.glob(str(store.HOME / "models" / "*.gguf")) if "mmproj" not in Path(g).name.lower())
    if ours:
        return Path(ours[0])
    lm = glob.glob(str(Path.home() / ".lmstudio" / "models" / "**" / "*.gguf"), recursive=True)
    lm = sorted(g for g in lm if "mmproj" not in Path(g).name.lower() and "gemma-4" in Path(g).name.lower() and "e4b" in Path(g).name.lower())
    return Path(lm[0]) if lm else None


class Cancelled(Exception):
    """The user cancelled the model download; the .part file is kept so it can resume."""


class DownloadError(RuntimeError):
    """A model download failure whose str() is fit to show a person."""


def models_dir():
    return store.HOME / "models"


def part_path():
    """The resumable partial download (find_model only globs *.gguf, so it never mistakes this for a model)."""
    return models_dir() / (MODEL_FILE + ".part")


def part_bytes():
    """Bytes already downloaded into the .part file (0 if none)."""
    try:
        return part_path().stat().st_size
    except OSError:
        return 0


def _hash_part(path, cancel):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            if cancel is not None and cancel.is_set():
                raise Cancelled()
    return h


def _drive(path):
    return Path(path).drive or str(Path(path).anchor) or str(path)


def _open_model(opener, have):
    """Open MODEL_URL (resuming at `have` bytes when > 0); readable DownloadError on failure. Returns the response."""
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "murmur", **({"Range": f"bytes={have}-"} if have else {})})
    try:
        return opener(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            return None  # the part is not a prefix of this file: caller restarts clean
        raise DownloadError(f"Hugging Face answered {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        log.info("model download: could not reach %s: %s", MODEL_URL, e)  # DNS, TLS, proxy or no network at all
        raise DownloadError("Couldn't reach huggingface.co. Check your internet connection.") from e


def download_model(cfg, progress_cb=None, cancel=None, opener=urllib.request.urlopen, disk_usage=shutil.disk_usage):
    """Fetch the pinned GGUF (about 6 GB) into ~/.murmur/models with stdlib urllib; call ONLY on explicit user action.
    Resumes ~/.murmur/models/<file>.part with a Range request (a server that answers 200, or a part that is not a prefix, restarts
    clean), reports progress_cb(done_bytes, total_bytes) at most ~5x/s (first call right away with the resumed size), stops with
    Cancelled when the `cancel` threading.Event is set (the part is kept), checks free disk space first, verifies the whole file's
    SHA-256 (a mismatch deletes the part), then renames it into place. DownloadError messages are readable. Returns the final path."""
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    final, part = d / MODEL_FILE, part_path()
    if final.is_file() and final.stat().st_size == MODEL_SIZE:
        return final
    have = part_bytes()
    if have > MODEL_SIZE:
        part.unlink(); have = 0
    need, free = MODEL_SIZE - have + DISK_MARGIN, disk_usage(d).free
    if free < need:
        raise DownloadError(f"Not enough disk space on {_drive(d)} (need {need / 1e9:.1f} GB, {free / 1e9:.1f} GB free)")
    cancelled = lambda: cancel is not None and cancel.is_set()
    if progress_cb:
        progress_cb(have, MODEL_SIZE)
    h = _hash_part(part, cancel) if have else hashlib.sha256()
    done, last = have, time.monotonic()
    if done < MODEL_SIZE:
        r = _open_model(opener, have)
        if r is None:  # 416
            part.unlink(missing_ok=True); have, h, done = 0, hashlib.sha256(), 0
            r = _open_model(opener, 0)
        with r:
            m = re.match(r"bytes (\d+)-", r.headers.get("Content-Range") or "")
            if have and not (getattr(r, "status", 200) == 206 and m and int(m.group(1)) == have):  # 200 or a different offset: start over
                have, h, done = 0, hashlib.sha256(), 0
            try:
                with open(part, "ab" if have else "wb") as f:
                    while chunk := r.read(1 << 18):
                        if cancelled():
                            raise Cancelled()
                        f.write(chunk); h.update(chunk); done += len(chunk)
                        if progress_cb and time.monotonic() - last >= 0.2:
                            progress_cb(done, MODEL_SIZE); last = time.monotonic()
                    if cancelled():
                        raise Cancelled()
            except (OSError, http.client.HTTPException) as e:
                if getattr(e, "errno", None) == errno.ENOSPC:
                    raise DownloadError(f"Ran out of disk space on {_drive(d)}") from e
                raise DownloadError("The connection was interrupted. Try again to resume.") from e
    if progress_cb:
        progress_cb(done, MODEL_SIZE)
    if done != MODEL_SIZE:
        raise DownloadError("The download ended early. Try again to resume.")
    if h.hexdigest() != MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise DownloadError("The download was corrupted, try again")
    os.replace(part, final)
    return final


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _JobBasic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _JobIO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]


class _JobExt(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("Basic", _JobBasic), ("IO", _JobIO), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job_api():
    """(kernel32 with typed Job Object functions, the extended-limit structure). Windows only."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    return k, _JobExt


def _create_kill_on_close_job(k, Ext):
    """A Job Object whose children die when its last handle closes (i.e. when murmur does, however it exits)."""
    job = k.CreateJobObjectW(None, None)
    info = Ext()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
        log.warning("job object setup failed: %s", ctypes.get_last_error())
    return job


def _kill_with_parent(proc):
    """Windows: put the child in a kill-on-close Job Object so it dies with murmur even on a hard kill."""
    global _job
    if os.name != "nt":
        return
    k, Ext = _job_api()
    if _job is None:
        _job = _create_kill_on_close_job(k, Ext)
    if not k.AssignProcessToJobObject(_job, int(proc._handle)):
        log.warning("could not attach llama-server to the job object: %s", ctypes.get_last_error())


def _log_tail(n=400):
    try:
        return (store.HOME / "llama-server.log").read_text(errors="replace")[-n:]
    except OSError:
        return ""


def _healthy(url):
    try:
        with urllib.request.urlopen(url.rsplit("/v1", 1)[0] + "/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _spawn(cfg):
    """Launch the server and wait for /health. Raises (after killing the child) on exit or timeout."""
    global _proc, _url
    c = _cfg(cfg)
    model = find_model(cfg)
    if not model:  # checked first: don't fetch the 90 MB runtime for a server that cannot start
        raise RuntimeError("no model found: set [builtin] model, or download one (Settings) into ~/.murmur/models")
    exe = ensure_binary(cfg)
    port = int(c["port"]) or _free_port()
    ngl = "all" if int(c["gpu_layers"]) < 0 else str(int(c["gpu_layers"]))
    cmd = [str(exe), "--model", str(model), "--host", "127.0.0.1", "--port", str(port), "-ngl", ngl,
           "--ctx-size", str(int(c["ctx"])), "--reasoning", "off", "--no-webui", "--parallel", "1"]
    store.HOME.mkdir(parents=True, exist_ok=True)
    logf = open(store.HOME / "llama-server.log", "wb")  # fresh log per spawn
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    log.info("starting llama-server: %s", " ".join(cmd))
    _proc = subprocess.Popen(cmd, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(exe.parent))
    logf.close()
    _url = f"http://127.0.0.1:{port}/v1"
    _kill_with_parent(_proc)
    deadline = time.monotonic() + float(c["start_timeout"])
    while not _healthy(_url):
        if _proc.poll() is not None:
            code = _proc.returncode
            _proc = None
            raise RuntimeError(f"llama-server exited ({code}): {_log_tail()}")
        if time.monotonic() > deadline:
            stop()
            raise RuntimeError(f"llama-server not healthy after {c['start_timeout']}s: {_log_tail()}")
        time.sleep(0.25)


def start(cfg):
    """Base URL (…/v1) of a healthy server, launching it if needed. A crashed server is restarted once; a second crash raises
    until stop() resets the count. Safe to call per request."""
    global _crashes, _registered, _failures, _retry_at
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return _url
        if time.monotonic() < _retry_at:
            raise RuntimeError(f"bundled model failed to start {_failures}x; trying again in {int(_retry_at - time.monotonic())}s")
        if _proc is not None:
            _crashes += 1
            log.warning("llama-server died (exit %s)", _proc.returncode)
            if _crashes > 1:
                raise RuntimeError("llama-server crashed twice; check ~/.murmur/llama-server.log")
        if not _registered:
            atexit.register(stop); _registered = True
        try:
            _spawn(cfg)
        except Exception:
            _failures += 1
            _retry_at = time.monotonic() + min(300, 5 * 2 ** _failures)  # 10 s, 20 s, 40 s ... capped at 5 min
            raise
        _failures, _retry_at = 0, 0.0
        return _url


def ready_url(cfg):
    """Base URL of a healthy server WITHOUT blocking: a dictation must never queue behind a start or a crash restart. If the
    server isn't up, a background start is kicked off (unless one is running or backing off) and RuntimeError is raised so the
    caller can use its fallback meanwhile."""
    p = _proc
    if p is not None and p.poll() is None and _url and not _starting():
        return _url
    if not _starting():
        threading.Thread(target=_safe_start, args=(cfg,), name=WARMUP_THREAD + "-boot", daemon=True).start()
    raise RuntimeError("bundled model starting")


def _starting():
    """True while some thread is inside start()'s spawn (the RLock is held for the whole health wait)."""
    if _lock.acquire(blocking=False):
        _lock.release()
        return False
    return True


def _safe_start(cfg):
    try:
        start(cfg)
    except Exception as e:
        log.info("bundled model start failed: %s", e)


def stop():
    """Terminate the child (then kill after 5 s). Idempotent; resets the crash count."""
    global _proc, _crashes
    with _lock:
        p, _proc, _crashes = _proc, None, 0
        if p is None or p.poll() is not None:
            return
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(5)


def status():
    """{"running", "url", "pid"} for the settings window / diagnostics."""
    with _lock:
        up = _proc is not None and _proc.poll() is None
        return {"running": up, "url": _url if up else "", "pid": _proc.pid if up else None}
