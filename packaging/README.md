# Packaging: single-exe Windows build

Builds murmur into one windowed `murmur.exe` (no console window).

## Build
From WSL in the repo root:
```bash
./packaging/build.sh
```
This stages `murmur/`, `murmur.spec` and `packaging/entry.py` to `%LOCALAPPDATA%\murmur-build`, runs PyInstaller through the Windows venv Python (`C:\Users\ethan\.murmur\venv`, PyInstaller 6.22.3), and copies `dist/murmur.exe` to `%LOCALAPPDATA%\Programs\murmur\murmur.exe` (a Desktop shortcut points there; the exe is kept out of OneDrive because OneDrive serves a stale icon for synced exes and would upload 60 MB per build). The build runs on the Windows filesystem because the `\\wsl$` share is slow and flaky for PyInstaller; WSL only orchestrates. ~20 s, ~60 MB.

## Embedded vs downloaded
- Embedded: Python, onnxruntime, numpy, tkinter/Tcl, pystray/Pillow, uiautomation/comtypes, onnx-asr (its preprocessor `.onnx` data and dist-info metadata, which it reads at import).
- Downloaded on first run: the Parakeet model (~600 MB) into the Hugging Face cache via onnx-asr. The log says "loading speech model (the first run downloads ~600 MB, one time)". The window-less exe shows nothing while this happens; watch `~/.murmur/murmur.log`.
- Not included: LM Studio and the LLM. Start LM Studio separately (murmur loads the configured model through `lms` if needed).

## Runtime notes
- Config and data live in `~/.murmur` (config.toml, prompt.md, dictionary.txt, snippets.toml, history.jsonl, murmur.log). The exe never writes next to itself.
- The exe must be started by you. No autostart, no service, by design.
- `murmur.exe --settings` opens the settings window only. `--selftest`/`learn` print to stdout, which a windowed exe doesn't have; use `run.cmd` (Python) for those.
- Entry is `packaging/entry.py`, not `murmur/__main__.py`, because the latter uses relative imports and can't be a PyInstaller script.
- A startup crash in a windowed exe is invisible. To debug, copy the spec, set `console=True`, rebuild and run from a terminal.

## Distribution notes
- Unsigned: SmartScreen shows "Windows protected your PC > More info > Run anyway" on first launch.
- One-file PyInstaller exes can trip antivirus heuristics; UPX is disabled to reduce that. A folder build is the usual fix if it happens.
