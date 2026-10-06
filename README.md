# murmur

Local voice dictation for Windows: hold a key, talk, and cleaned text is pasted into the active app. Parakeet (ONNX, CPU) for speech-to-text, a local LM Studio model for cleanup. Nothing leaves the machine.

## Install
```
python -m venv %USERPROFILE%\.murmur\venv
%USERPROFILE%\.murmur\venv\Scripts\pip install -r requirements.txt
```
Needs LM Studio installed with `google/gemma-4-e4b` downloaded; murmur starts the server and loads the model via `lms` if they are not already up.

## Download / build the exe
`./packaging/build.sh` (from WSL) builds a single windowed `murmur.exe` into `%LOCALAPPDATA%\Programs\murmur` (Desktop shortcut `murmur.lnk`); see `packaging/README.md`. The speech model downloads on first run; no autostart.

## Run
`run.cmd` (tray app, runs on pythonw so no console window; any args use python.exe for stdout), `run.cmd --selftest`, `run.cmd learn` — from Windows, e.g. `\\wsl.localhost\Ubuntu-24.04\home\ethan\repos\murmur\run.cmd`. (`run.ps1` directly is blocked by the execution policy because the WSL path counts as remote; `run.cmd` bypasses it for that one file.)

## Hotkeys
- Hold `right ctrl`: dictate. Release to paste.
- Hold `right alt`: command mode. Speak an instruction; applies to the selection (or generates text if none). The selection is copied on release; in terminals (`[apps] no_copy`) it is skipped and speech is treated as a generate instruction.
- Only the side-specific key triggers (left ctrl/alt are ignored).
- While you dictate, the caption card floats above the focused field (see Overlay below).
- `ctrl+alt+m`: fix the last dictation; saved corrections teach the dictionary.
- `ctrl+alt+d` (`[hotkeys] learn_word`): select 1-4 words and press it to add them to the dictionary (UIA selection, clipboard fallback outside `no_copy` apps). The overlay flashes `Learned → "word"`.

### Spoken commands
Say exactly "scratch that", "undo that", "undo" or "delete that" (fillers like "um" and punctuation are ignored; extra words mean it's dictation). If murmur pasted within the last 60 s into the app that is still focused ("scratch that" only works in the app you pasted into; elsewhere it says "nothing to undo here") it sends one `ctrl+z`; otherwise it flashes "nothing to undo". Nothing is pasted or logged. "new paragraph"/"new line" are handled by the cleanup prompt.

### Auto-learn
If you dictate a near-identical sentence (same app, within 90 s) that differs by 1-3 words, murmur treats it as a correction: changed names/terms go into the dictionary and the earlier history row is marked corrected, which feeds the few-shot corrections in the prompt.

## Config (`~/.murmur/`)
`config.toml` (hotkeys, llm, stt, per-app tones), `dictionary.txt` (preferred spellings), `snippets.toml` (say "insert sig"), `style.md` (generated), `history.jsonl`, `murmur.log`.

`learn` summarizes recent history into `style.md`, which is fed into the cleanup prompt.

## Customising
Tray menu > Settings… (or `.\run.ps1 --settings`) edits all of this in a window: history (copy/fix/delete, time-saved stats), dictionary, snippets, prompt, tones and config (a form of toggles, dropdowns, hotkey recorders and an app-to-tone table; the raw `config.toml` sits under Advanced and keeps comments, a form save drops them). Config/tone saves apply live except hotkeys (restart).

Everything lives in `~/.murmur/`: `config.toml` (hotkeys, LLM provider, `[tones]`, `[apps]`, `[output]`, `[audio] device` (input device name, `""` = system default; pick it from the Config form, which also has a Test mic button; a missing device falls back to the default with one log line), `[context]` for cursor-aware dictation; skipped for `no_copy` apps, and a warning is logged if the LLM provider is remote since field text would leave the machine), `prompt.md` (cleanup rules), `dictionary.txt`, `snippets.toml`, `style.md`.

## Overlay (caption card)
A dark glass card appears just above the focused text field (UI Automation rect; flips below if there is no room, clamped to the monitor). It never takes focus. States: Listening, live transcript (last word dimmed), Polishing, Pasted, Command, Done, plus engine states (Loading model, LM Studio offline, Couldn't paste, No microphone with a Retry button, Learned, Undone). While a dictation card is up, `Enter` inserts now (while polishing it pastes the raw transcript immediately) and `Esc` cancels at any point (nothing pasted or logged); both keys work even while ctrl/alt are held and are swallowed only while the card is up. Hints show only in states where they work. `.\run.cmd --demo-card [state]` previews every state.

## Look
The card, tray icon and settings window share one design language (tokens in `murmur/theme.py`). Source of truth: the design handoff at `C:\Users\ethan\Downloads\Dictation software interface design\design_handoff_dictation_caption_card\README.md`. The tray icon is the product speech bubble (`packaging/icon.png`) recoloured per state: periwinkle = ready, red = recording, amber = polishing, grey with a slash = paused, red with a dot = error. Left-click opens Settings; the menu is native Windows and can't be restyled. Set `[ui] startup_toast = false` to skip the "murmur is running" notification.
