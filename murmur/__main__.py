import argparse, logging, os, subprocess, sys, tempfile, threading, time, wave
from pathlib import Path
import numpy as np
from . import brain, store, stt

log = logging.getLogger("murmur")


def setup_logging():
    fmt = "%(asctime)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout), logging.FileHandler(store.HOME / "murmur.log", encoding="utf-8")])


def lms_command(llm):
    """(lms exe path or None, load command list). Empty [llm] lms / lms_load mean auto: find the CLI and build the load command."""
    import shlex
    lms = llm.get("lms") or store.find_lms()
    if llm.get("lms_load"):
        return lms, shlex.split(llm["lms_load"])
    return lms, [lms, "load", llm["model"], "--gpu", "max", "-y", "--context-length", "8192"] if lms else []


def _builtin_wanted(cfg):
    """Is the bundled model the primary or the fallback provider?"""
    from . import localllm
    wanted = cfg["llm"].get("provider") == "builtin" or (cfg["llm"].get("fallback") and cfg.get("llm_fallback", {}).get("provider") == "builtin")
    return bool(wanted) and localllm.enabled(cfg)


def start_builtin(cfg):
    """Launch the bundled llama-server and send ONE throwaway chat so a first-install shader compile (~12 s on Vulkan) isn't
    paid by the first dictation. Runs on a daemon thread; failures are logged, the fallback path covers a dictation meanwhile."""
    store.runtime["llm_starting"] = True
    try:
        from . import localllm
        t = time.time(); localllm.start(cfg); log.info("bundled model up in %.1fs", time.time() - t)
        t = time.time(); brain._chat(cfg, "Reply with one word.", "ping"); log.info("bundled model warm-up %.1fs", time.time() - t)
    except Exception as e:
        log.info("bundled model start failed: %s", e)
    finally:
        store.runtime["llm_starting"] = False


def start_lm_studio(cfg):
    """LM Studio path (provider openai): start its server if needed and load the configured model (never unloads). Without an
    lms CLI the server steps are skipped (logged once)."""
    llm = cfg["llm"]
    lms, load = lms_command(llm)
    if not lms:
        log.info("LM Studio CLI (lms) not found: skipping server/model loading; start LM Studio yourself")
        return
    subprocess.run([lms, "server", "start"], capture_output=True, timeout=60)  # wakes the daemon; idempotent
    ps = subprocess.run([lms, "ps"], capture_output=True, text=True, timeout=20).stdout
    if llm["model"] not in ps:
        log.info("loading %s", llm["model"])
        subprocess.run(load, timeout=300)


def start_llm(cfg):
    """Start whatever serves cleanup, then warm it. The bundled model starts on its own thread (its warm-up chat is part of
    start_builtin); with a builtin primary that is all. LM Studio steps run only for provider openai; the warm-up request and the
    offline card handle a server that isn't up."""
    llm = cfg["llm"]
    if _builtin_wanted(cfg):
        threading.Thread(target=start_builtin, args=(cfg,), name="builtin-warmup", daemon=True).start()  # llm.py lets only this thread talk to a starting model
        if llm.get("provider") == "builtin":
            return
    try:
        if llm.get("provider", "openai") == "openai":  # lms only manages the local LM Studio
            start_lm_studio(cfg)
        brain._chat(cfg, "Reply with one word.", "ping")
    except Exception as e:
        log.info("llm warmup failed: %s", e)


def boot(cfg, progress=lambda frac, text: None):
    """Load the speech model (bundled with the build) and make sure the LLM is up. progress(frac 0..1, step text) drives the
    loading card; each fraction is where that step's share of the bar ends (the card paces the bar there itself)."""
    progress(0.7, "Loading speech model\u2026")
    log.info("loading speech model from %s", stt.model_dir() if stt.model_dir().is_dir() else "the Hugging Face cache")
    stt.load_model(cfg)
    progress(0.85, "Preparing window access\u2026")
    try:
        import comtypes.client
        comtypes.client.GetModule("oleacc.dll")  # warm the MSAA typelib so the first dictation doesn't pay for it
    except Exception as e:
        log.info("oleacc warm-up failed: %s", e)
    progress(1.0, "Starting cleanup model\u2026")
    start_llm(cfg)


def selftest(real=False):
    """Transcribe + clean test.wav. Default runs against a fresh temp HOME with default files so user data
    (style.md, dictionary, history) can't change the result; --real uses ~/.murmur."""
    wav = store.HOME / "test.wav"
    if not real:
        store.HOME = Path(tempfile.mkdtemp(prefix="murmur-selftest-"))
    store.ensure()
    cfg = store.load_config()
    t = time.time(); stt.load_model(cfg); print(f"stt load {time.time() - t:.1f}s")
    with wave.open(str(wav)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    t = time.time(); raw = stt.recognize(audio); print(f"raw ({time.time() - t:.2f}s): {raw}")
    ctx = {"cfg": cfg, "tone": "casual", "terms": ["Podstead"]}
    t = time.time(); c = brain.clean(raw, ctx); print(f"cleaned ({time.time() - t:.2f}s): {c}")
    from . import context
    f = context.read_field(cfg)
    print("context:", {k: (len(v) if isinstance(v, str) else v) for k, v in f.items()} or "none")
    print("snippets:", brain.apply_snippets("Thanks! {{snippet:SIG}}", store.load_snippets()))
    print("shortcut:", brain.snippet_only("Insert sig.", store.load_snippets()))
    sys.stdout.flush(); os._exit(0)  # skip onnxruntime teardown


def shell_verb(a):
    """--exclude FILE / --shell-menu on|off: short-lived process started from Explorer or the build script."""
    from . import shellmenu, win
    if a.shell_menu:
        cfg, want = store.load_config(), a.shell_menu == "on"
        if shellmenu.enabled(cfg) != want:  # only rewrite config.toml (comments are lost) when the value changes
            cfg.setdefault("apps", {})["shell_menu"] = want
            store.save_config(cfg)
        shellmenu.sync(cfg)
        print(f"Explorer menu {'installed' if a.shell_menu == 'on' else 'removed'}"); return
    name, on = shellmenu.exclude_file(a.exclude)
    msg = f"murmur is off in {name}. Right-click it again (or Settings \u203a Config) to turn it back on." if on else f"murmur is back on in {name}."
    print(msg)
    if os.name == "nt" and not win.instance_running():  # otherwise the running tray app shows it on the card
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "murmur", 0x40)


def learn(cfg):
    hist = store.read_history(150)
    if not hist:
        print("no history yet"); return
    text = brain.learn_style(hist, cfg)
    (store.HOME / "style.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main():
    if os.name == "nt":
        from . import win
        win.dpi_aware()  # before any window exists
    ap = argparse.ArgumentParser(prog="murmur", description="Local voice dictation")
    ap.add_argument("cmd", nargs="?", choices=["learn", "fetch-model", "fetch-vad", "seal"], help="learn: rebuild style.md from history; fetch-model / fetch-vad: download the speech model / Silero VAD into the build folder; seal: encrypt plaintext history and recordings (+ DIRs)")
    ap.add_argument("dest", nargs="*", help="with fetch-model / fetch-vad: target folder (default packaging/models/parakeet or /silero); with seal: extra folders of recordings")
    ap.add_argument("--settings", nargs="?", const="History", metavar="TAB", help="open the settings window only (optionally on TAB)")
    ap.add_argument("--demo-card", nargs="?", const="", metavar="STATE", help="show the caption card with fake data (cycles states, or freezes STATE)")
    ap.add_argument("--demo-tray", metavar="STATE", help="save the tray icon for STATE to ~/.murmur/shots/")
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    ap.add_argument("--setup", action="store_true", help="open the first-run setup window only")
    ap.add_argument("--exclude", metavar="FILE", help="toggle FILE's program (an .exe or .lnk) in [apps] exclude and exit (the Explorer right-click verb)")
    ap.add_argument("--shell-menu", choices=["on", "off"], help="add or remove the Explorer right-click verb and exit")
    ap.add_argument("--selftest", action="store_true", help="transcribe+clean test.wav, no hotkeys (fresh temp HOME)")
    ap.add_argument("--real", action="store_true", help="with --selftest: use ~/.murmur instead of a fresh temp HOME")
    a = ap.parse_args()
    if a.version:
        from . import __version__
        print(__version__); return
    if a.cmd == "fetch-model":  # build step: no ~/.murmur needed
        print(stt.fetch(a.dest[0] if a.dest else None)); return
    if a.cmd == "fetch-vad":
        from . import vad
        print(vad.fetch(a.dest[0] if a.dest else None)); return
    first_run = store.ensure()  # True when config.toml did not exist: the tray app opens the setup window once
    if a.cmd == "seal":
        from . import seal
        if not seal.available():
            raise SystemExit("seal needs Windows DPAPI: run it with the Windows venv")
        rows, files = store.seal_existing(a.dest)
        print(f"sealed {rows} history rows and {files} recording files"); return
    if a.exclude or a.shell_menu:
        return shell_verb(a)
    if a.selftest:
        return selftest(a.real)
    if a.demo_card is not None:
        from . import card
        return card.demo(a.demo_card or None)
    if a.demo_tray:
        from .tray import tray_image
        shots = store.HOME / "shots"; shots.mkdir(exist_ok=True)
        for size in (64, 16):
            tray_image(a.demo_tray, size).save(shots / f"tray-{a.demo_tray}-{size}.png")
        return
    if a.setup:
        try:
            from . import setup
        except ImportError:
            print("setup window not available in this build"); return
        return setup.run_standalone()
    if a.settings:
        from . import settings
        from .app import fix_last
        return settings.run_standalone(store.load_config(), fix_last, a.settings)
    setup_logging()
    cfg = store.load_config()
    if a.cmd == "learn":
        return learn(cfg)
    if os.name == "nt":
        from . import win
        if not win.single_instance():  # tray app only: learn, --settings and --selftest return before this
            return
    if cfg.get("context", {}).get("enabled", True) and not store.local_only(cfg):  # "builtin" and localhost openai count as local
        log.warning("[context] enabled with a remote LLM provider (primary or fallback): text around your cursor is sent to it")
    threading.Thread(target=store.seal_existing, daemon=True).start()  # one-time migration of pre-encryption files; idempotent
    from .app import tray_app
    tray_app(cfg, learn, boot, first_run=first_run)


if __name__ == "__main__":
    main()
