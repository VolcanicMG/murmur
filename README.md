# murmur

Local voice dictation for Windows: hold a key, talk, and cleaned text is pasted into the active app. Parakeet (ONNX, CPU) for speech-to-text, a local LM Studio model for cleanup. Nothing leaves the machine.

## Install
```
python -m venv %USERPROFILE%\.murmur\venv
%USERPROFILE%\.murmur\venv\Scripts\pip install -r requirements.txt
```
Needs LM Studio installed with `google/gemma-4-e4b` downloaded; murmur starts the server and loads the model via `lms` if they are not already up.

## Run
`run.cmd` (tray app), `run.cmd --selftest`, `run.cmd learn` — from Windows, e.g. `\\wsl.localhost\Ubuntu-24.04\home\ethan\repos\murmur\run.cmd`. (`run.ps1` directly is blocked by the execution policy because the WSL path counts as remote; `run.cmd` bypasses it for that one file.)

## Hotkeys
- Hold `right ctrl`: dictate. Release to paste.
- Hold `right alt`: command mode. Speak an instruction; applies to the selection (or generates text if none). The selection is copied on release; in terminals (`[apps] no_copy`) it is skipped and speech is treated as a generate instruction.
- Only the side-specific key triggers (left ctrl/alt are ignored).
- The overlay shows `● Recording → <app> — <window title>`, a live mic level bar, and the raw/cleaned text; it never takes focus.
- `ctrl+alt+m`: fix the last dictation; saved corrections teach the dictionary.

## Config (`~/.murmur/`)
`config.toml` (hotkeys, llm, stt, per-app tones), `dictionary.txt` (preferred spellings), `snippets.toml` (say "insert sig"), `style.md` (generated), `history.jsonl`, `murmur.log`.

`learn` summarizes recent history into `style.md`, which is fed into the cleanup prompt.

## Customising
Everything lives in `~/.murmur/`: `config.toml` (hotkeys, LLM provider, `[tones]`, `[apps]`, `[output]`), `prompt.md` (cleanup rules), `dictionary.txt`, `snippets.toml`, `style.md`.
