# evals: score a prompt, guard or model change

Replays cases through the REAL `murmur/brain.py` (`apply_scratch` -> `clean_auto` -> `fit`, plus `plausible`, `command_intent`), mirroring `Session.finish`. Runs in a fresh temp HOME (no personal dictionary/style/history) unless `--real-home`. Defaults to LM Studio + `google/gemma-4-e4b` unless `--provider/--url/--model` are given (`--real-home` uses your own config instead). Fallback providers are always disabled so eval text never leaves the machine.

```
python -m evals.run --cases core --no-llm                 # deterministic kinds only, no LLM: the regression check (exit 1 on any miss with --min-pass 1)
python -m evals.run --cases core --n 3 --label baseline   # full run (Windows venv, LM Studio up; ~7 min for 62 LLM cases x3)
python -m evals.compare evals/results/A.json evals/results/B.json
python -m evals.build                                     # history.jsonl -> evals/cases/local/history.jsonl
```
From WSL use the Windows venv, see CLAUDE.md. Flags: `--cases core|local|all|PATH`, `--filter kind=dictate` or `id~tone`, `--n`, `--model`, `--provider`, `--url`, `--prompt-file F` (installed as prompt.md for the run), `--label`, `--real-home`, `--no-llm`, `--min-pass 0.9`.

`python -m evals.run --cases core --no-llm` is the regression check for the deterministic code (scratch, fit, intents, guard); CI can run it with `--min-pass 1`.

## Case format (`evals/cases/*.jsonl`, one object per line)
`{id, kind, raw, tone, before?, after?, terms?, selected?, output?, checks, note}`
- `dictate`: full pipeline, LLM, run `--n` times (stochastic). `terms` = extra dictionary words.
- `scratch`: `apply_scratch(raw)`. `fit`: `fit(raw, before, tone)` (raw = the cleaned text being joined). `intent`: `command_intent`, output is `undo`, `rewrite`, `learn`, `learn:<word>` or `scratch:<rest>` (`selected` = a selection exists). `guard`: `plausible(raw, output)` with a canned `output`; use the `plausible` check.

Checks (all must pass; `contains`/`not_contains`/`ends_with` use the output after the pipeline):
`equals`, `equals_norm` (case/punctuation/space-insensitive), `contains` (list, case-insensitive; `"one|1"` = either), `contains_exact` (case-sensitive), `not_contains`, `starts_upper`/`starts_lower` (first letter), `ends_with`, `max_extra_words` N (output words not derivable from raw after number normalisation), `is_question`, `empty`, `lines` N, `min_lines` N, `regex` (`re.search`), `plausible` (guard kind), `guard_ok` (dictate: the guard accepted the LLM output, no fallback to raw), `contains_content_words_of_expected` (local cases: >=80% of the expected text's content words appear).

To add a case: append a line, give it a one-line `note` of what it protects, run it with `--filter id~yourid --n 5`. Prefer a real failure verbatim. Keep checks loose enough that a correct answer in another style passes (digits or words, "forty-five" or "45").

## A/B
Different model: `--model qwen/... --label qwen`. Edited prompt: copy `prompt.md`, edit, `--prompt-file my.md --label v2`. Then `python -m evals.compare evals/results/<A>.json evals/results/<B>.json`: per-kind deltas, regressions first, then improvements. Use `--n 3`+ for dictate cases; one run is noise.

## Output
Table of pass rate per kind, median/p95 latency per LLM call, guard stats (rejected->raw, retry used, errors), failing cases with the failed checks and a failing output. `evals/results/<UTC>-<label>.json` has every run (output, per-check result, call latencies); the `.md` next to it is the printed summary. Results are gitignored.

## Privacy
`evals/cases/local/` and `evals/results/` are gitignored: they contain your real dictations. Never copy local cases into `core.jsonl`, paste their text into issues/PRs/reports, or commit them. Only share aggregate numbers. `core.jsonl` holds non-sensitive text only.
