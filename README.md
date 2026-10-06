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
- Hold `right alt`: command mode. Speak an instruction. It edits, in order: the real selection (UI Automation's answer is authoritative, even when empty; the ctrl+c fallback only runs when UIA can't say, and never in `[apps] no_copy` apps); else **your last dictation** if it is still right before the cursor (same app, within 10 min; murmur selects it and the result replaces it, so "make that more formal" or "change X to Y" edits instead of appending); else it generates new text at the cursor. The card header says which: `(selection: N chars)`, `(editing last dictation)` or `(new text)`; history rows record `target`.
- Only the side-specific key triggers (left ctrl/alt are ignored).
- **Hands-free:** tap the dictate key (released within `[dictation] tap_ms`, default 300 ms; `toggle = false` disables it) and recording latches (chip: "Listening · hands-free"). Press the key again (that press does not start a new session), Enter (insert now) or Esc (cancel) to end it, or just stop talking: after you have spoken, `silence_ms` (2000) of quiet finishes it, and `no_speech_ms` (6000) with no speech cancels ("No speech", nothing pasted). Holding the key still works exactly as before; the command key is hold-only.
- **Long dictation:** at a pause of 700 ms or more after 8 s of audio the segment is transcribed once and committed, so only the open segment is re-transcribed live (live clean pauses after the first commit). A recording stops itself at 10 minutes. A transcript over ~1200 characters is cleaned in sentence-group chunks, each seeing the previous chunk's tail, with the plausibility guard per chunk, then pasted once.
- While you dictate, the caption card floats above the focused field (see Overlay below).
- `ctrl+alt+m`: fix the last dictation; saved corrections teach the dictionary.
- `ctrl+alt+d` (`[hotkeys] learn_word`): select 1-4 words and press it to add them to the dictionary (UIA selection, clipboard fallback outside `no_copy` apps). The overlay flashes `Learned → "word"`. It fires on key press (the app logs `hotkey learn_word fired`); `trigger_on_release` never fired in the keyboard library.

### Spoken commands
Say exactly "scratch that", "undo that", "undo" or "delete that" (fillers like "um" and punctuation are ignored; extra words mean it's dictation). If murmur pasted within the last 60 s into the app that is still focused ("scratch that" only works in the app you pasted into; elsewhere it says "nothing to undo here") it sends one `ctrl+z`; otherwise it flashes "nothing to undo". Nothing is pasted or logged. "new paragraph"/"new line" are handled by the cleanup prompt.

**Scratch that mid-dictation** is applied deterministically before the LLM: each cue ("scratch that", "scratch that last one/part/bit", "delete that", "never mind that", "strike that") removes itself and the sentence before it (back to the previous . ! ? or the start; filler-only fragments are skipped). Words after the cue in the same sentence are kept as the replacement ("Tuesday, scratch that, let's do Thursday" -> "let's do Thursday."). If the whole utterance was scratched, nothing is pasted and the card says "Scratched". In command mode, exactly "scratch that" undoes the last paste; "scratch that, instead …" corrects your last dictation with the remainder.

**Voice dictionary (command mode):** with 1-4 words selected, "add this word to the dictionary" (also save/put/learn/remember this/that/it [word/term/name] to/in the/my dictionary, or "learn this word") adds the selection; "add Toung to the dictionary" adds a single spoken word. Nothing is pasted or logged.

**Capitalisation:** a sentence starts with a capital unless the text before the cursor is clearly mid-sentence (it ends in , ; : - ( [ { or a quote, or the current line is 4+ words ending in a lowercase letter and doesn't look like code). Code-looking first words (`my_var`, `--flag`, `app.py`, lone CLI commands in code tone) are left alone.

### Auto-learn
If you dictate a near-identical sentence (same app, within 90 s) that differs by 1-3 words, murmur treats it as a correction: changed names/terms go into the dictionary and the earlier history row is marked corrected, which feeds the few-shot corrections in the prompt.

## Config (`~/.murmur/`)
`config.toml` (hotkeys, llm, stt, per-app tones), `dictionary.txt` (preferred spellings), `snippets.toml` (say "insert sig"), `style.md` (generated), `history.jsonl`, `murmur.log`.

`learn` summarizes recent history into `style.md`, which is fed into the cleanup prompt.

## Customising
Tray menu > Settings… (or `.\run.ps1 --settings`) edits all of this in a window: history (Dictation/Command mode per row, a Raw vs Final pane to compare what was heard with what was typed, copy/fix/delete, time-saved stats for dictation), dictionary, snippets, prompt, tones and config (a form of toggles, dropdowns, hotkey recorders and an app-to-tone table; the raw `config.toml` sits under Advanced and keeps comments, a form save drops them). Config/tone saves apply live except hotkeys (restart).

**Single instance:** a second tray launch shows a native "murmur is already running" box and exits (named mutex `Local\murmur-single-instance`); `--settings`, `--selftest` and `learn` are unaffected.

**Sounds:** `[ui] sounds` (default true) and `[ui] sound_volume` (any value from 0 to 1 in config, default 0.25; the Settings form offers 0.1 / 0.25 / 0.5) control the soft cues (`murmur/cues.py`: start when recording begins, stop when it ends, done on paste, error on error/offline/paste failure, cancel on Esc/No speech/Scratched; generated in memory, played via `winsound`). Settings > Config > Dictation has the toggle, a volume select and a Test button.

**LLM fallback:** `[llm] fallback = true` plus an `[llm_fallback]` table (provider/url/model/api_key_env/timeout, default Anthropic Haiku) retries a failed cleanup request (connection refused, timeout, HTTP 5xx) on the second provider. After a primary failure the primary is skipped for 60 s so each dictation doesn't wait out a timeout, then retried. The fallback sends your dictated text (and cursor context, if enabled) to that provider, so the startup warning also fires when it is remote. When the fallback answered, the Pasted card reads "Pasted · fallback".

**File tags (code editors):** in VS Code, Cursor and Windsurf, a spoken file name that matches exactly one file in the open workspace becomes its relative path (`src/app.py`, or `@src/app.py` in an AI chat input); ambiguous or unknown names stay as spoken. `[code] file_tags` = `"off"` | `"chat"` (default: only when the focused control's name/class looks like a chat/composer/copilot/prompt/ask input) | `"always"`. The workspace folder comes from the window title matched against the editor's `storage.json` (WSL windows map to `\\wsl.localhost\<distro>\...`), plus any `[code] roots`. Files are listed with `git ls-files` (2 s cap, else a 5000-file walk), cached 60 s; if the index isn't ready the text is left untouched.

Everything lives in `~/.murmur/`: `config.toml` (hotkeys, LLM provider, `[tones]`, `[apps]`, `[output]`, `[audio] device` (input device name, `""` = system default; pick it from the Config form, which also has a Test mic button; a missing device falls back to the default with one log line), `[context]` for cursor-aware dictation; skipped for `no_copy` apps, and a warning is logged if the LLM provider is remote since field text would leave the machine), `prompt.md` (cleanup rules), `dictionary.txt`, `snippets.toml`, `style.md`.

## Overlay (caption card)
A dark glass card appears just above the focused text field (UI Automation rect; flips below if there is no room, clamped to the monitor). It never takes focus. States: Listening, live transcript (last word dimmed), Polishing, Pasted, Command, Done, plus engine states (Loading model, LM Studio offline, Couldn't paste, No microphone with a Retry button, Learned, Undone). While a dictation card is up, `Enter` inserts now (while polishing it pastes the raw transcript immediately) and `Esc` cancels at any point (nothing pasted or logged); both keys work even while ctrl/alt are held and are swallowed only while the card is up. Hints show only in states where they work. `.\run.cmd --demo-card [state]` previews every state. The card is a per-pixel-alpha layered window (soft shadow, real translucency, anti-aliased shapes drawn by Pillow); if Windows refuses it at startup the card falls back to a chroma-key renderer (no shadow, jagged edges). Settings pills, chips, toggles and panels use the same anti-aliased renderer (`murmur/render.py`).

## Look
The card, tray icon and settings window share one design language (tokens in `murmur/theme.py`). Source of truth: the design handoff at `C:\Users\ethan\Downloads\Dictation software interface design\design_handoff_dictation_caption_card\README.md`. The tray icon is the product speech bubble (`packaging/icon.png`) recoloured per state: periwinkle = ready, red = recording, amber = polishing, grey with a slash = paused, red with a dot = error. Left-click opens Settings; the menu is native Windows and can't be restyled. Set `[ui] startup_toast = false` to skip the "murmur is running" notification.
