# Packaging: Windows onedir build

Builds murmur into a folder: `dist/murmur/murmur.exe` (windowed, no console) plus `_internal/`. A folder build has no per-launch unpack step, so it starts much faster than the old one-file exe (cold start to a visible card on this machine, `--demo-card listening`: about 2.5 s one-file vs 0.6 s onedir warm / 1.5 s first cold launch; "ready:" in the log about 5.8 s from launch, dominated by loading the speech model).

## Build + install (from WSL)
```bash
./packaging/build.sh
```
Refuses to run (exit 1, clear message) while `murmur.exe` is running. Otherwise it stages `murmur/`, `murmur.spec` and `packaging/entry.py` to `%LOCALAPPDATA%\murmur-build`, runs PyInstaller through the Windows venv Python (`%USERPROFILE%\.murmur\venv`, PyInstaller 6.22.3), replaces the contents of `%LOCALAPPDATA%\Programs\murmur\` with the new folder (nothing user-owned lives there; settings and data are in `~/.murmur`) and (re)creates the Desktop shortcut `murmur.lnk` (target = that exe, icon = the exe's). The install stays out of OneDrive: it serves a stale icon for synced exes and would upload ~130 MB per build. ~20 s build, ~135 MB folder.

## Build on Windows / CI
```powershell
.\packaging\build.ps1 [-Python python] [-Dist dist]
```
Same PyInstaller invocation without the WSL staging; `.github/workflows/release.yml` calls it, zips `dist/murmur`, writes `SHA256SUMS.txt`, attests the zip (public repos only) and attaches both to the release for `v*` tags, with `RELEASE_NOTES_HEADER.md` (`@REPO@` is substituted) ahead of the generated notes. `THIRD_PARTY_NOTICES.md` is copied next to `murmur.exe`.

The only build-time dependency is pinned in `requirements-build.txt` at the repo root (`pip install -r requirements-build.txt`; PyInstaller 6.22.3).

**Icon:** `murmur.ico` must be written with BMP/DIB entries (PIL `Image.save("murmur.ico", sizes=..., bitmap_format="bmp")`). Explorer ignores PNG-compressed icon sizes below 256 px and shows the default icon for the exe.

## Embedded vs downloaded
- Embedded: Python, onnxruntime, numpy, tkinter/Tcl, pystray/Pillow, uiautomation/comtypes, onnx-asr (its preprocessor `.onnx` data and dist-info metadata, which it reads at import).
- Bundled: the Parakeet model (~640 MB) at `_internal/packaging/models/parakeet`. `build.sh` runs `python -m murmur fetch-model <dir>` once into `MODEL_DIR` (default `%LOCALAPPDATA%\murmur-build-model\parakeet`, kept across builds) and hands it to the spec via `MURMUR_MODEL_DIR` (WSLENV `/p` translates the path); `build.ps1` fetches into `packaging/models/parakeet` (gitignored). The spec refuses to build without it. `stt.load_model` reads the folder offline (`onnx_asr.load_model(name, path)`); only a non-default `[stt] model` or a checkout without the folder touches the HF cache. Delete a half-downloaded folder before re-fetching: onnx-asr treats an existing folder as complete.
- Downloaded on first use (never bundled): the llama.cpp runtime (~90 MB Vulkan build, SHA-256 pinned) into `~/.murmur/runtime`, started as a child process of the exe. The cleanup model GGUF is found automatically (`~/.murmur/models`, then LM Studio's gemma-4 e4b); otherwise Settings offers a ~6 GB download that runs only when you click it (the frozen exe needs `huggingface_hub` and `tqdm` for that button: both are in the spec's hiddenimports).
- LM Studio is optional: with it installed it serves as the fallback (or primary, if the config says `provider = "openai"`).

## Runtime notes
- Config and data live in `~/.murmur` (config.toml, prompt.md, dictionary.txt, snippets.toml, history.jsonl, murmur.log). The exe never writes next to itself.
- The app folder must stay together (`murmur.exe` loads `_internal/` next to it); move or zip the whole folder.
- The exe must be started by you. No autostart, no service, by design.
- `murmur.exe --settings` opens the settings window only. `--selftest`/`learn` print to stdout, which a windowed exe doesn't have; use `run.cmd` (Python) for those.
- Entry is `packaging/entry.py`, not `murmur/__main__.py`, because the latter uses relative imports and can't be a PyInstaller script.
- A startup crash in a windowed exe is invisible. To debug, copy the spec, set `console=True`, rebuild and run from a terminal.

## Distribution notes
- Unsigned: SmartScreen shows "Windows protected your PC > More info > Run anyway" on first launch.
- UPX is disabled (it trips antivirus heuristics); the folder build avoids the self-extracting stub one-file exes use.

## Built-in LLM runtime
The llama.cpp binaries are NOT in the exe: `localllm.ensure_binary` fetches and SHA-256-verifies the pinned release zip to `~/.murmur/runtime` on first use (Vulkan 33 MB zip / 91 MB extracted; CUDA 13.4 576 MB of zips / 712 MB extracted; CPU 19 MB). PyInstaller needs nothing extra: `localllm` imports only stdlib at module level (`huggingface_hub` and `tqdm` are imported lazily inside `download_model`, so add them to the spec's hidden imports if the frozen exe should offer the model download).
