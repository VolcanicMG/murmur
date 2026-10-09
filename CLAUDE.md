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
- Password fields: `context._read` returns only `{"password": True}` when UIA `IsPassword` is set (no text, selection or control name). `Session.password` then means: no LLM/cleanup, no live clean, no fit/file tags, typed output (never the clipboard, and a failed delivery never leaves it there), no history/learning, `pasted(app, "")`, card shows "not saved". Anything new that logs or stores a transcript must check `Session.private` (private mode OR password).
- Clipboard: never save/restore with `pyperclip` text only; use `win.clip_snapshot()/clip_restore()` (`clip.py`: all formats, GDI handles skipped, 64 MB cap -> text only, OpenClipboard retried). CF_HDROP comes back from pywin32 as a tuple and is rebuilt as a DROPFILES block.
- Hotkeys: register only through `app._register` and keep the returned handles (`keyboard.unhook(handle)`); `app.rebind_hotkeys(cfg)` swaps them live (deferred while a session runs). A press in an excluded/fullscreen context returns before touching gesture state (`app._ignored`).
- Learning: `autolearn.py` (edit detection, style refresh) and `phonetic.py` (sound-alike matching) are fed ONLY from Session paths that already passed the private/password checks; keep it that way. The session read is widened to 2000 chars (`context.wide`) for edit detection; prompts still slice to `[context]` chars.
- Paste outcomes: every observed dictate row gets `outcome` unchanged|edited (words changed INSIDE the paste)|updated (only text added around it), plus `edits` [{kind misheard|word_choice|restored|removed, from, to}], `added`, `rewrite`, `sent` (edited only), `outcome_source` watch|claude-code; unobserved rows have no `outcome` (unknown, never guessed). `learn.classify_edit` decides (word alignment, case/punctuation ignored). Learning rule (Ethan): ONLY misheard edits add dictionary terms and set `corrected` (the few-shot source), and never on a rewrite; everything else is data. `autolearn.watch` runs after each readable paste: baseline read at 0.3 s, a passive `keyboard.hook` (never suppresses, unhooked by handle), a re-read after 0.7 s without keys; it ends on Enter, app switch, a newer paste, a new session (whose start read is handed to it via hook (a) `check_and_apply`) or 120 s. Keys without a settled read = unknown; such pastes stay pending for hook (a)'s `detect_edit`. cfg is live (tray private toggle): the watch re-checks private/auto_edits before every read and before recording, and drops the paste entirely (no record, not left pending) if either flips. `judge` needs an anchor (up to 20 chars of baseline beside the paste, or its first/last 3 words at a field edge, on at least one side) in the newest read or hand-off, else unknown: same exe, different field. `context.read_text(cfg, expect_app)` checks the foreground app on its worker BEFORE reading and returns `(app, "")` for no_copy apps or another app. Outcomes are APPENDED (sealed) to `outcomes.jsonl` by `store.set_outcome`, never a history rewrite; `_read_history_unlocked` merges them (ts+final, later wins) and every `_write_history_unlocked` folds them in and deletes the file (unopenable lines kept; `clear_history` deletes it outright). Terminals are never read: `python -m evals.build --claude-code` fills their outcomes from Claude Code transcripts (only the FIRST human message in [ts-5 s, ts+10 min] is a candidate; it teaches only on a strong match: >= 80% of the paste's words, span 0.5x-1.5x its length). murmur.log gets `dictation ...`/`outcome ...` lines with sizes and kinds only, never text.
- Conversation context (`[context] thread`) is collected only when `store.local_only(cfg)`; never log it. `store.runtime["llm_starting"]` drives the "Model starting..." label while the bundled model boots.
- The IsPassword verdict is a `context.Verdict` published right after `_is_password` and awaited alone (1.5 s) at release; `Session._pw` is frozen then. Unknown = `sensitive` (private + local-only + typed). `brain.clean_checked` logs sizes only when `ctx["private"]`. Never show spoken words on the card while `Session.guarded`.
- `clip.snapshot` returns `None` (not `[]`) for failed/over-cap/unsaveable clipboards and `restore(None)` does nothing: only `[]` means "was really empty". Our pasted text is written by `clip.set_text` with the history/cloud exclusion formats.
- The bundled model never blocks a dictation: `llm._call` uses `localllm.ready_url` (non-blocking, kicks a background start) and raises `llm.Starting` while `store.runtime["llm_starting"]` (only the "builtin-warmup" thread may talk to it); `Starting` doesn't trip the 60 s breaker. Spawn failures back off (`_retry_at`).
- Pre-LLM text rewrites live in `prepass.apply_scratch` (re-exported as `brain.apply_scratch`) -> `spoken_forms` (number self-corrections, breaks, addresses) so the eval harness exercises them; `brain.prepare(raw, ctx, terms)` adds the phonetic stage and is what Session._prep and the evals should call. Phonetic: one word is only ever a hint; hard needs 2-3 words, equal sound keys, similarity >= 0.8, no function word, nothing swallowed.
- Command mode copies the selection on key release (not press) and skips `[apps] no_copy` apps.

## Packaging
Build deps are pinned in `requirements-build.txt` (`pip install -r requirements-build.txt` in the Windows venv). `packaging/murmur.ico` must use BMP/DIB entries: Explorer ignores PNG-compressed sizes below 256 px and shows the default icon (regenerate with PIL `save(..., bitmap_format="bmp")`).
`murmur.spec` + `packaging/build.sh` build a one-FOLDER windowed app (`dist/murmur/murmur.exe` + `_internal/`, installed to `%LOCALAPPDATA%\Programs\murmur`; `build.ps1` is the CI/Windows twin; entry stub `packaging/entry.py`). Frozen gotchas: onnx-asr needs `copy_metadata` and its data files; a windowed exe hides startup tracebacks, so debug with a `console=True` copy of the spec.

## Overlay / card gotchas
- Design source of truth: `C:\Users\ethan\Downloads\Dictation software interface design\design_handoff_dictation_caption_card\README.md`.
- Default card renderer: `card.Layered` (`UpdateLayeredWindow` on the Tk toplevel's wrapper hwnd, premultiplied BGRA DIB, fade = `SourceConstantAlpha`, slide = window move) fed by `cardpaint.Painter` (PIL frames at the DPI scale; static layer, chip and text cached, per frame only halo + 64 bars; identical frame = same object = no ULW call). Never call `-alpha`/`-transparentcolor`/`SetLayeredWindowAttributes` on it. Window = card + shadow padding each side (derived from blur 30 / offset 10, at most 40 px); `place()` positions the VISIBLE card, window = card - pad. Fill alpha 0.97 (handoff 0.76 assumes acrylic blur, unavailable). Fallback (`card.LAYERED = False`, or ULW failing at startup): chroma-key Canvas (`-transparentcolor #010203`, `-alpha 0.96`, polygons: jagged, no shadow). DWM corner/dark attributes are used only on the settings window.
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
- `brain.py`: prompt sections, snippets, cleanup calls and the guard, command intents. No win32. `prepass.py`: deterministic text work (number/word normalisation, scratch cues, number corrections, breaks, addresses, prose identifiers). `learn.py`: dictionary/edit learning (`learn_dictionary`, `detect_edit`, `learn_from_edit`, re-dictation). brain re-exports what callers import; the new modules never import brain.
- `llm.py`: provider request shaping only (openai-compatible, anthropic).
- `stt.py`: Parakeet load/recognize.
- `store.py`: `~/.murmur` files, defaults, history, stats, minimal TOML writer (`dump_toml`), `save_config` (round-trip checked, atomic), `form_to_cfg` (form validation), `resolve_device`.
- `settings.py`: tkinter settings/history window (Tk main thread only; each tab builder returns `(frame, load)`). Config is a form of Panel cards over `store.merged_config()`; each control has `get()/set()` and is registered in a `fields` map by `section.key`.
- `card.py`: the caption-card overlay (Tk main thread; `post()` queue; `Layered` ULW driver). Pure helpers `bar_heights`, `place`, `norm_level` are tested. `cardpaint.py`: PIL frame composition. `render.py`: anti-aliased shapes/fonts shared with settings.
- `about.py`: About page (version, paths, redacted diagnostics, log viewer). `setup.py`: first-run 4-step window (`run_standalone()`); `inputs.py`/`widgets.py`/`render.py`: rounded controls shared by settings and setup.
- `theme.py`: shared tokens, `blend()`, fonts, `rounded_points()` for card, tray icon and settings (pure).
- `tray.py`: PIL tray icon (product bubble from `packaging/icon.png`, recoloured per state; procedural fallback) + tooltip per state (`mark(state)` in app.py).
- `capture.py`: pure press/release `gesture` (hold vs tap-latch), `vad_step` (silence auto-stop) and `should_commit` (long-dictation segments); tested without audio.
- `cues.py`: `tone_wav`/`play` audio cues (winsound, async, honours `[ui] sounds`); called from Session only.
- `workspace.py`: editor workspace roots (window title + storage.json), file index and `tag_files` for code-editor file tags. Pure except `index`.
- `autolearn.py`: the paste watch and outcome recording (`watch`, `record`; pure `judge`/`watch_result`/`should_read`/`finish_reason`), hook (a) for pastes left unknown (pending, 30 min), style.md refresh counters (`state.json`). `phonetic.py`: `sound_key`/`phonetic_fix`.
- `speech.py`: speaking metrics from history rows (`row_metrics`, `series`, `baseline`, `trend`, `summary`, `by_app`, `ready`, `hint`); `capture.vad_step` totals `speech_s`/`pauses` for it. Dictate rows get `duration_s, speech_s?, pauses, fillers, self_corrections, words_raw` only in the non-private path.
- `clip.py`: clipboard snapshot/restore across formats (pywin32 lazily imported; pure helpers tested).
- `win.py`: foreground app, clipboard/paste/type, DPI, caret/work-area helpers, dark title bar.

To add: a settings tab -> a `_x_tab` builder in `settings.py` listed in `open_settings`; an STT backend -> `stt.py` `load_model`/`recognize` contract; an LLM provider -> `llm.py` `chat()`; a tone -> `[tones]` in config (+ `[apps]`); a prompt section -> a function in `brain.SECTIONS`; a context source -> `context.gather`; an output method -> `win.insert`; a hotkey trigger mode -> `app.py` `press`/`release`; a spoken command -> a `brain.COMMANDS` phrase entry + a `Session._cmd_<name>` method.

## Built-in LLM (`localllm.py`, provider `"builtin"`)
- Managed llama.cpp `llama-server` child on 127.0.0.1 (random port), pinned release `TAG` (b11456) with SHA-256 per asset in `ASSETS`; runtime fetched on demand to `~/.murmur/runtime/llama-<tag>-<backend>/` (vulkan 91 MB default, cuda 712 MB incl. cudart, cpu small). Never bundled in the exe. Bump the tag = update `TAG` + every digest (GitHub shows them per asset).
- Model: `[builtin] model`, else `~/.murmur/models/*.gguf`, else LM Studio's gemma-4 e4b; `download_model` (HF `lmstudio-community/gemma-4-E4B-it-GGUF` Q6_K, Apache-2.0, ungated) runs only on explicit user action.
- Thinking: `llm._call` sends `reasoning_effort: "none"` (verified to suppress `reasoning_content` on gemma-4); server also runs `--reasoning off`. A request `chat_template_kwargs.enable_thinking=true` would override the flag, murmur never sends it. Without either, gemma-4 thinks (10 s).
- Child is attached to a kill-on-close Job Object (survives nothing: a hard-killed murmur takes the server with it, verified). `start()` is lazy, idempotent, restarts a crash once; startup failures surface as `OSError` so the fallback/breaker apply. Log: `~/.murmur/llama-server.log`.
- Vulkan: the first ever request after install compiles shaders (~12 s once per driver cache); CUDA has no such cost.

## Evaluating changes
`python -m evals.run --cases core --no-llm` (deterministic scratch/fit/intent/guard cases, any OS) is the regression check for brain.py logic; `--n 3 --label X` on the Windows venv runs the LLM cases against LM Studio, and `python -m evals.compare A.json B.json` shows regressions. Cases live in `evals/cases/core.jsonl`; `evals/cases/local/` (built from your history by `python -m evals.build`) and `evals/results/` are gitignored. Details: `evals/README.md`.
