import argparse, logging, subprocess, sys, time, wave
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


def selftest():
    import os
    store.ensure()
    cfg = store.load_config()
    t = time.time(); stt.load_model(cfg); print(f"stt load {time.time() - t:.1f}s")
    with wave.open(str(store.HOME / "test.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    t = time.time(); raw = stt.recognize(audio); print(f"raw ({time.time() - t:.2f}s): {raw}")
    ctx = {"cfg": cfg, "tone": "casual", "terms": ["Podstead"]}
    t = time.time(); c = brain.clean(raw, ctx); print(f"cleaned ({time.time() - t:.2f}s): {c}")
    from . import context
    f = context.read_field(cfg)
    print("context:", {k: len(v) for k, v in f.items()} or "none")
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
    ap = argparse.ArgumentParser(prog="murmur", description="Local voice dictation")
    ap.add_argument("cmd", nargs="?", choices=["learn"], help="learn: rebuild style.md from history")
    ap.add_argument("--settings", action="store_true", help="open the settings window only")
    ap.add_argument("--selftest", action="store_true", help="transcribe+clean test.wav, no hotkeys")
    a = ap.parse_args()
    store.ensure()
    if a.selftest:
        return selftest()
    if a.settings:
        from . import settings
        from .app import fix_last
        return settings.run_standalone(store.load_config(), fix_last)
    setup_logging()
    cfg = store.load_config()
    if a.cmd == "learn":
        return learn(cfg)
    if cfg.get("context", {}).get("enabled", True) and cfg["llm"].get("provider", "openai") != "openai":
        log.warning("[context] enabled with a remote LLM provider: text around your cursor is sent to it")
    stt.load_model(cfg)
    start_llm(cfg)
    from .app import tray_app
    tray_app(cfg, learn)


if __name__ == "__main__":
    main()
