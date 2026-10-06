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
- Overlay must never deiconify/withdraw after startup (steals focus); it fades via the layered blend alpha and has NOACTIVATE|TOOLWINDOW|LAYERED|TOPMOST styles (no WS_EX_TRANSPARENT: the Retry button must stay clickable; clicks are hit-tested in `Card._click` against `Painter.retry_box`).
- UIA read runs in a daemon thread — never call UIA on the Tk thread or the hotkey thread. Join caps: 0.4 s in dictate mode, 1.0 s in command mode (still unfinished = the command aborts with "Couldn't read the selection"; the clipboard fallback is never used while the read is merely pending) and for the learn hotkey (`_selection`). The select/collapse helpers cap their own worker threads at 1.5 s / 1.0 s.
- The UIA ValuePattern fallback treats the caret as end-of-field.
- UIA's TextPattern selection is authoritative even when empty (`read_field` returns `sel_known`). Never treat the clipboard fallback as a selection when it is known-empty: VS Code's ctrl+c copies the whole line, and pasting that duplicated text. Command target order lives in `brain.resolve_target`.
- `keyboard.add_hotkey(..., trigger_on_release=True)` never fired (learn_word looked dead); hotkeys fire on press and the worker waits for modifiers. Every handler logs `hotkey <name> fired`, so a dead chord is visible in murmur.log.
- Caret rect chain (`context._read` -> `context._anchor` -> `card.pick_rect`; the line rect is tried before the text read, the rest only if it found nothing): line, collapsed point, TextPattern2, MSAA caret, Win32 caret, then the control rect (`pane` if >200 px tall). Obsidian/Chromium only answer on the point and MSAA steps. **Never inject keystrokes or text into the desktop in smoke tests** (no `keyboard.write/send` into real windows, no launching focus-taking apps): a stray write once landed in Ethan's open Obsidian note. Read-only UIA probes of the focused control, stubs and pure tests only.
- Audio cues play from WAV files cached in `~/.murmur/cues/` (`SND_FILENAME|SND_ASYNC`); `SND_MEMORY|SND_ASYNC` raises on Windows, so never play bytes.
- A chord on the dictate key (any other key down while it is held) is cancelled silently on release, never latched (`capture.gesture` event `other`); `llm.route()` is per thread, so a session reads its own clean's route.
- Command mode copies the selection on key release (not press) and skips `[apps] no_copy` apps.

## Packaging
Build deps are pinned in `requirements-build.txt` (`pip install -r requirements-build.txt` in the Windows venv). `packaging/murmur.ico` must use BMP/DIB entries: Explorer ignores PNG-compressed sizes below 256 px and shows the default icon (regenerate with PIL `save(..., bitmap_format="bmp")`).
`murmur.spec` + `packaging/build.sh` build a one-file windowed exe (entry stub `packaging/entry.py`). Frozen gotchas: onnx-asr needs `copy_metadata` and its data files; a windowed exe hides startup tracebacks, so debug with a `console=True` copy of the spec.

## Overlay / card gotchas
- Design source of truth: `C:\Users\ethan\Downloads\Dictation software interface design\design_handoff_dictation_caption_card\README.md`.
- Default card renderer: `card.Layered` (`UpdateLayeredWindow` on the Tk toplevel's wrapper hwnd, premultiplied BGRA DIB, fade = `SourceConstantAlpha`, slide = window move) fed by `cardpaint.Painter` (PIL frames at the DPI scale; static layer, chip and text cached, per frame only halo + 64 bars; identical frame = same object = no ULW call). Never call `-alpha`/`-transparentcolor`/`SetLayeredWindowAttributes` on it. Window = card + 40 px shadow padding each side; `place()` positions the VISIBLE card, window = card - pad. Fill alpha 0.94 (handoff 0.76 assumes acrylic blur, unavailable). Fallback (`card.LAYERED = False`, or ULW failing at startup): chroma-key Canvas (`-transparentcolor #010203`, `-alpha 0.96`, polygons: jagged, no shadow). DWM corner/dark attributes are used only on the settings window.
- Anti-aliasing: Tk canvas shapes have none. Every curved shape (card, settings pills/chips/toggle/tiles/panels) is a `render.py` image (4x supersample, LANCZOS, LRU-cached shared RGBA: never mutate, `.copy()` first) shown via `ImageTk.PhotoImage` flattened onto the known canvas bg. Draw an overlay (e.g. the chip dot) INTO the shape image, not as a second flattened image, or it gets a dark fringe. Screenshot own windows with `PrintWindow(hwnd, dc, 2)` (settings) or CopyFromScreen (layered card); a venv `pythonw` is a launcher, kill the tree (`taskkill /T`).
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
- `card.py`: the caption-card overlay (Tk main thread; `post()` queue; `Layered` ULW driver). Pure helpers `bar_heights`, `place`, `norm_level` are tested. `cardpaint.py`: PIL frame composition. `render.py`: anti-aliased shapes/fonts shared with settings.
- `theme.py`: shared tokens, `blend()`, fonts, `rounded_points()` for card, tray icon and settings (pure).
- `tray.py`: PIL tray icon (product bubble from `packaging/icon.png`, recoloured per state; procedural fallback) + tooltip per state (`mark(state)` in app.py).
- `capture.py`: pure press/release `gesture` (hold vs tap-latch), `vad_step` (silence auto-stop) and `should_commit` (long-dictation segments); tested without audio.
- `cues.py`: `tone_wav`/`play` audio cues (winsound, async, honours `[ui] sounds`); called from Session only.
- `workspace.py`: editor workspace roots (window title + storage.json), file index and `tag_files` for code-editor file tags. Pure except `index`.
- `win.py`: foreground app, clipboard/paste/type, DPI, caret/work-area helpers, dark title bar.

To add: a settings tab -> a `_x_tab` builder in `settings.py` listed in `open_settings`; an STT backend -> `stt.py` `load_model`/`recognize` contract; an LLM provider -> `llm.py` `chat()`; a tone -> `[tones]` in config (+ `[apps]`); a prompt section -> a function in `brain.SECTIONS`; a context source -> `context.gather`; an output method -> `win.insert`; a hotkey trigger mode -> `app.py` `press`/`release`; a spoken command -> a `brain.COMMANDS` phrase entry + a `Session._cmd_<name>` method.
