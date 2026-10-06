import argparse, logging, os, subprocess, sys, tempfile, time, wave
from pathlib import Path
import numpy as np
from . import brain, store, stt

log = logging.getLogger("murmur")


def setup_logging():
    fmt = "%(asctime)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout), logging.FileHandler(store.HOME / "murmur.log", encoding="utf-8")])


def start_llm(cfg):
    """Start the LM Studio server if needed, load the configured model (never unloads), then warm it."""
    llm = cfg["llm"]
    try:
        if llm.get("provider", "openai") == "openai":  # lms only manages the local LM Studio
            subprocess.run([llm["lms"], "server", "start"], capture_output=True, timeout=60)  # wakes the daemon; idempotent
            ps = subprocess.run([llm["lms"], "ps"], capture_output=True, text=True, timeout=20).stdout
            if llm["model"] not in ps:
                import shlex
                log.info("loading %s", llm["model"])
                subprocess.run(shlex.split(llm["lms_load"]), timeout=300)
        brain._chat(cfg, "Reply with one word.", "ping")
    except Exception as e:
        log.info("llm warmup failed: %s", e)


def boot(cfg):
    """Load the speech model (first run downloads ~600 MB) and make sure the LLM is up."""
    log.info("loading speech model (the first run downloads ~600 MB, one time)")
    stt.load_model(cfg)
    try:
        import comtypes.client
        comtypes.client.GetModule("oleacc.dll")  # warm the MSAA typelib so the first dictation doesn't pay for it
    except Exception as e:
        log.info("oleacc warm-up failed: %s", e)
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
    ap.add_argument("cmd", nargs="?", choices=["learn"], help="learn: rebuild style.md from history")
    ap.add_argument("--settings", nargs="?", const="History", metavar="TAB", help="open the settings window only (optionally on TAB)")
    ap.add_argument("--demo-card", nargs="?", const="", metavar="STATE", help="show the caption card with fake data (cycles states, or freezes STATE)")
    ap.add_argument("--demo-tray", metavar="STATE", help="save the tray icon for STATE to ~/.murmur/shots/")
    ap.add_argument("--selftest", action="store_true", help="transcribe+clean test.wav, no hotkeys (fresh temp HOME)")
    ap.add_argument("--real", action="store_true", help="with --selftest: use ~/.murmur instead of a fresh temp HOME")
    a = ap.parse_args()
    store.ensure()
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
    remote = cfg["llm"].get("provider", "openai") != "openai" or (
        cfg["llm"].get("fallback") and cfg.get("llm_fallback", {}).get("provider", "openai") != "openai")
    if cfg.get("context", {}).get("enabled", True) and remote:
        log.warning("[context] enabled with a remote LLM provider (primary or fallback): text around your cursor is sent to it")
    from .app import tray_app
    tray_app(cfg, learn, boot)


if __name__ == "__main__":
    main()
