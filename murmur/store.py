"""~/.murmur files: config, dictionary, snippets, style, history. No win32."""
import json, os, threading, tomllib
from pathlib import Path

HOME = Path.home() / ".murmur"  # tests repoint this
_hist_lock = threading.Lock()  # session thread appends while fix-last rewrites

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


def ensure():
    """Create ~/.murmur and any missing default files."""
    HOME.mkdir(exist_ok=True)
    for name, text in (("config.toml", DEFAULT_CONFIG), ("snippets.toml", DEFAULT_SNIPPETS),
                       ("dictionary.txt", "# one preferred spelling per line\n"),
                       ("prompt.md", DEFAULT_PROMPT)):
        if not (HOME / name).exists():
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
        with open(HOME / "dictionary.txt", "a", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
    return new


def load_style():
    p = HOME / "style.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def append_history(entry):
    with _hist_lock, open(HOME / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(n=None):
    with _hist_lock:
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


def correct_last(ts, edited):
    """Set `corrected` on the newest history entry with this ts (atomic rewrite)."""
    with _hist_lock:
        rows = _read_history_unlocked()
        for r in reversed(rows):
            if r.get("ts") == ts:
                r["corrected"] = edited
                tmp = HOME / "history.jsonl.tmp"
                tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
                os.replace(tmp, HOME / "history.jsonl")
                return


def key_matches(event_name, key):
    """keyboard matches hooks by scan code (left ctrl fires 'right ctrl'); compare the sided event name."""
    return (event_name or "").lower() == key.lower()
