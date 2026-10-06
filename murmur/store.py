"""~/.murmur files: config, dictionary, snippets, style, history. No win32."""
import json, os, re, threading, tomllib
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home() / ".murmur"  # tests repoint this
_hist_lock = threading.Lock()  # session thread appends while fix-last rewrites


@contextmanager
def _locked():
    """History lock: thread lock plus, on Windows, a file lock so a second murmur process is excluded too."""
    with _hist_lock:
        if os.name != "nt":
            yield
            return
        import msvcrt
        with open(HOME / "history.lock", "a+b") as f:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


DEFAULT_CONFIG = """\
[hotkeys]
dictate = "right ctrl"
command = "right alt"
fix_last = "ctrl+alt+m"

[llm]
provider = "openai"  # or "anthropic" (url + api_key_env)
url = "http://localhost:1234/v1/chat/completions"
model = "google/gemma-4-e4b"
live_clean = true
timeout = 15
lms = "C:/Users/ethan/.lmstudio/bin/lms.exe"
lms_load = "C:/Users/ethan/.lmstudio/bin/lms.exe load google/gemma-4-e4b --gpu max -y --context-length 8192"

[context]
enabled = true  # read text around the cursor via UI Automation
before_chars = 600
after_chars = 200

[output]
method = "paste"  # or "type" for apps that block paste

[tones]
casual = "Tone: casual. Relaxed, contractions, short sentences."
formal = "Tone: formal. Complete sentences, professional, no slang."
neutral = "Tone: neutral. Plain, natural writing."
code = "Tone: code context. Preserve identifiers (camelCase, snake_case, kebab-case) and CLI commands verbatim; no trailing period on a lone command; write spoken file names like 'app dot py' as `app.py`."

[stt]
model = "nemo-parakeet-tdt-0.6b-v3"
quantization = "int8"

[apps]
"slack.exe" = "casual"
"discord.exe" = "casual"
"signal.exe" = "casual"
"whatsapp.exe" = "casual"
"outlook.exe" = "formal"
"olk.exe" = "formal"
"winword.exe" = "formal"
"thunderbird.exe" = "formal"
"code.exe" = "code"
"cursor.exe" = "code"
"windsurf.exe" = "code"
"windowsterminal.exe" = "code"
"wt.exe" = "code"
no_copy = ["windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe"]
"""
DEFAULT_TONES = tomllib.loads(DEFAULT_CONFIG)["tones"]  # fallback for configs written before [tones]
DEFAULT_PROMPT = """You clean up raw speech-to-text dictation into the text the speaker meant to write.
- Remove filler words (um, uh, like, you know).
- Add punctuation and paragraph breaks.
- Apply self-corrections ("X, actually Y", "no wait Y", "scratch that") and keep only the final intent.
- Turn spoken lists into formatted lists.
- Honour spoken punctuation such as "comma", "period", "new paragraph".
- Keep the speaker's voice; do not add content or answer questions in the text.
"""
DEFAULT_SNIPPETS = '[snippets]\nsig = "Best,\\nEthan"\n'


DEFAULT_FILES = {"config.toml": DEFAULT_CONFIG, "snippets.toml": DEFAULT_SNIPPETS,
                 "dictionary.txt": "# one preferred spelling per line\n", "prompt.md": DEFAULT_PROMPT}


def ensure():
    """Create ~/.murmur and any missing default files."""
    HOME.mkdir(exist_ok=True)
    missing = {n: t for n, t in DEFAULT_FILES.items() if not (HOME / n).exists()}
    for name, text in missing.items():
        (HOME / name).write_text(text, encoding="utf-8")


def load_config():
    return tomllib.loads((HOME / "config.toml").read_text(encoding="utf-8"))


def load_prompt():
    p = HOME / "prompt.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else DEFAULT_PROMPT.strip()


def load_snippets():
    p = HOME / "snippets.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")).get("snippets", {}) if p.exists() else {}


def load_dictionary():
    p = HOME / "dictionary.txt"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def add_terms(terms):
    """Append terms not already in dictionary.txt (case-sensitive)."""
    have = set(load_dictionary())
    new = [t for t in dict.fromkeys(terms) if t not in have]
    if new:
        p = HOME / "dictionary.txt"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        with open(p, "a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + "\n".join(new) + "\n")
    return new


def load_style():
    p = HOME / "style.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def append_history(entry):
    with _locked(), open(HOME / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(n=None):
    with _locked():
        return _read_history_unlocked(n)


def _read_history_unlocked(n=None):
    p = HOME / "history.jsonl"
    if not p.exists():
        return []
    rows = []
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(l))
        except ValueError:
            pass  # torn/corrupt line
    return rows[-n:] if n else rows


def _edit_row(ts, final, change):
    """Apply change(rows, row) to the newest history row matching ts and final, then rewrite atomically."""
    with _locked():
        rows = _read_history_unlocked()
        for r in reversed(rows):
            if r.get("ts") == ts and r.get("final") == final:
                change(rows, r)
                tmp = HOME / "history.jsonl.tmp"
                tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
                os.replace(tmp, HOME / "history.jsonl")
                return


def correct_last(ts, final, edited):
    """Set `corrected` on the newest history entry matching ts and final (atomic rewrite)."""
    _edit_row(ts, final, lambda rows, r: r.update(corrected=edited))


def delete_row(ts, final):
    _edit_row(ts, final, lambda rows, r: rows.remove(r))


def stats(rows, now=None):
    """Dictation counts and time saved (typing 40 wpm vs speaking 150 wpm)."""
    now, week = now or datetime.now(), 0
    words = [len(r.get("final", "").split()) for r in rows]
    for r, w in zip(rows, words):
        try:
            week += w if now - datetime.fromisoformat(r["ts"]) < timedelta(days=7) else 0
        except (KeyError, ValueError):
            pass
    total = sum(words)
    return {"dictations": len(rows), "words": total, "minutes_saved": total / 40 - total / 150, "week_words": week}


def remove_term(term):
    """Drop a term from dictionary.txt, keeping comment lines."""
    p = HOME / "dictionary.txt"
    keep = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip() != term]
    p.write_text("\n".join(keep) + "\n", encoding="utf-8")


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        raise ValueError("nested tables are not supported")
    return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings


def _key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def dump_toml(d):
    """Minimal TOML writer: scalars, list-of-scalars and one level of tables (all DEFAULT_CONFIG uses)."""
    out = [f"{_key(k)} = {_val(v)}" for k, v in d.items() if not isinstance(v, dict)]
    for k, t in d.items():
        if isinstance(t, dict):
            out += ["", f"[{_key(k)}]"] + [f"{_key(a)} = {_val(b)}" for a, b in t.items()]
    return "\n".join(out).strip() + "\n"


def save_snippets(snips):
    (HOME / "snippets.toml").write_text(dump_toml({"snippets": snips}), encoding="utf-8")


def save_tones(tones):
    """Rewrite config.toml with a new [tones] table (comments are lost). Raises ValueError if the file can't round-trip."""
    cfg = load_config()
    cfg["tones"] = tones
    msg = "config has structures the Tones tab can't write; edit [tones] in the Config tab"
    try:
        out = dump_toml(cfg)
        ok = tomllib.loads(out) == cfg
    except (ValueError, TypeError):  # TypeError: dates/times in the config
        ok = False
    if not ok:
        raise ValueError(msg)
    tmp = HOME / "config.toml.tmp"
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, HOME / "config.toml")


def key_matches(event_name, key):
    """keyboard matches hooks by scan code (left ctrl fires 'right ctrl'); compare the sided event name."""
    return (event_name or "").lower() == key.lower()
