# murmur

Local Wispr-Flow-style dictation. **Windows-only runtime, dev from WSL.** No classes where functions do; fewest files; pin every dependency exactly.

## Facts (verified)
- Venv: `C:\Users\ethan\.murmur\venv\Scripts\python.exe` (pins in requirements.txt). CPU only, no CUDA.
- STT: `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")` loads ~2-13 s; transcribes 10 s of audio in ~0.3 s.
- LLM: LM Studio at `http://localhost:1234/v1/chat/completions`, model `google/gemma-4-e4b`. Always send `"reasoning_effort": "none"` and `temperature: 0.2`. stdlib `urllib` only. CLI: `C:\Users\ethan\.lmstudio\bin\lms.exe` (`lms ps`, `lms load google/gemma-4-e4b --gpu max -y --context-length 8192`).
- Test wav: `C:\Users\ethan\.murmur\test.wav` (16 kHz mono). Make more with Windows TTS (System.Speech SpeechSynthesizer.SetOutputToWaveFile).
- Mic, hotkeys and paste cannot be tested from WSL; cover them by `--selftest` + manual use.
- `brain.py` and `store.py` have no win32 imports and run on Linux.

## Run (from WSL)
```
powershell.exe -NoProfile -c 'cd \\wsl.localhost\Ubuntu-24.04\home\ethan\repos\murmur; C:\Users\ethan\.murmur\venv\Scripts\python -m murmur --selftest' | tr -d '\r'
```
cmd.exe can't cd to UNC; PowerShell can.

## Tests
`python3 -m unittest discover tests` (Linux, no network). Expected selftest: raw transcript "Um hey so I was looking at the podstead deploy ... 0.43 actually 0.44 ..." and cleaned text with "0.44" and "Podstead".

## Gotchas
- `keyboard` matches hooks by scan code: always filter on the sided event name (`store.key_matches`).
- Overlay must never deiconify/withdraw after startup (steals focus); it toggles alpha and has NOACTIVATE|TOOLWINDOW|TRANSPARENT styles.
- UIA read runs in a daemon thread with a 0.4 s join — never call UIA on the Tk thread or the hotkey thread.
- The UIA ValuePattern fallback treats the caret as end-of-field.
- Command mode copies the selection on key release (not press) and skips `[apps] no_copy` apps.

## Packaging
`murmur.spec` + `packaging/build.sh` build a one-file windowed exe (entry stub `packaging/entry.py`). Frozen gotchas: onnx-asr needs `copy_metadata` and its data files; a windowed exe hides startup tracebacks, so debug with a `console=True` copy of the spec.

## Overlay / card gotchas
- Design source of truth: `C:\Users\ethan\Downloads\Dictation software interface design\design_handoff_dictation_caption_card\README.md`.
- Tk has no per-pixel alpha, so the card is drawn on a chroma-key window (`-transparentcolor #010203`) with `-alpha 0.96`; rounded shape and tail are polygons. No shadow and no acrylic backdrop (a keyed window can't take either). DWM corner/dark attributes are used only on the settings window.
- The process is DPI-aware (`win.dpi_aware()` before any Tk root); every card/settings pixel size is multiplied by `win.dpi_scale()`.
- Esc/Enter use `keyboard.hook_key(..., suppress=True)` (callback returns False) only while a live session card is up, and `keyboard.unhook` on every state change away from live states and in `hide()`. NEVER use `add_hotkey(suppress=True)` + `remove_hotkey`: it leaves `_listener.blocking_hotkeys` non-empty, which enables the lib's modifier-replay logic and holds back ctrl/alt app-wide.
- The card window is NOACTIVATE|TOOLWINDOW but clickable (Retry); hidden = alpha 0 and moved off-screen, never withdrawn.
- Card state changes come from the session via `ov.post(...)`; never touch Tk off the main thread. `ov.release()` ends a session but keeps self-dismissing states (Pasted, errors) on screen.

## Architecture & extension points
- `__main__.py`: argparse, logging, LM Studio startup, selftest, learn.
- `app.py`: tray wiring, hotkey press/release, fix-last editor, shared state `S`.
- `session.py`: one recording (audio, live clean, finish, insert). `Session` per press.
- `context.py`: per-recording context: `gather` (app, title, tone) and `read_field` (UIA text before/after caret, selection).
- `brain.py`: prompt sections, snippets, dictionary/style learning, output tidy. No win32.
- `llm.py`: provider request shaping only (openai-compatible, anthropic).
- `stt.py`: Parakeet load/recognize.
- `store.py`: `~/.murmur` files, defaults, history, stats, minimal TOML writer (`dump_toml`), `save_config` (round-trip checked, atomic), `form_to_cfg` (form validation), `resolve_device`.
- `settings.py`: tkinter settings/history window (Tk main thread only; each tab builder returns `(frame, load)`). Config is a form of Panel cards over `store.merged_config()`; each control has `get()/set()` and is registered in a `fields` map by `section.key`.
- `card.py`: the caption-card overlay (one Canvas, Tk main thread; `post()` queue). Pure helpers `bar_heights`, `place`, `norm_level` are tested.
- `theme.py`: shared tokens, `blend()`, fonts, `rounded_points()` for card, tray icon and settings (pure).
- `tray.py`: PIL tray icon (product bubble from `packaging/icon.png`, recoloured per state; procedural fallback) + tooltip per state (`mark(state)` in app.py).
- `win.py`: foreground app, clipboard/paste/type, DPI, caret/work-area helpers, dark title bar.

To add: a settings tab -> a `_x_tab` builder in `settings.py` listed in `open_settings`; an STT backend -> `stt.py` `load_model`/`recognize` contract; an LLM provider -> `llm.py` `chat()`; a tone -> `[tones]` in config (+ `[apps]`); a prompt section -> a function in `brain.SECTIONS`; a context source -> `context.gather`; an output method -> `win.insert`; a hotkey trigger mode -> `app.py` `press`/`release`; a spoken command -> a `brain.COMMANDS` phrase entry + a `Session._cmd_<name>` method.
