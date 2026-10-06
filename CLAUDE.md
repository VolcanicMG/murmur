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
- Command mode copies the selection on key release (not press) and skips `[apps] no_copy` apps.

## Architecture & extension points
- `__main__.py`: argparse, logging, LM Studio startup, selftest, learn.
- `app.py`: tray wiring, hotkey press/release, fix-last editor, shared state `S`.
- `session.py`: one recording (audio, live clean, finish, insert). `Session` per press.
- `context.py`: per-recording context dict (app, title, tone).
- `brain.py`: prompt sections, snippets, dictionary/style learning, output tidy. No win32.
- `llm.py`: provider request shaping only (openai-compatible, anthropic).
- `stt.py`: Parakeet load/recognize.
- `store.py`: `~/.murmur` files, defaults, history.
- `win.py`: foreground app, clipboard/paste/type, overlay.

To add: an STT backend -> `stt.py` `load_model`/`recognize` contract; an LLM provider -> `llm.py` `chat()`; a tone -> `[tones]` in config (+ `[apps]`); a prompt section -> a function in `brain.SECTIONS`; a context source -> `context.gather`; an output method -> `win.insert`; a hotkey trigger mode -> `app.py` `press`/`release`; a spoken command -> `session.COMMANDS` (phase 2c).
