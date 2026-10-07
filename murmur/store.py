"""~/.murmur files: config, dictionary, snippets, style, history. No win32."""
import json, os, re, shutil, threading, tomllib
from urllib.parse import urlparse
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
learn_word = "ctrl+alt+d"  # add the selected word(s) to the dictionary

[llm]
provider = "builtin"  # "builtin" (bundled llama.cpp, see [builtin]), "openai" (LM Studio/Ollama: url + model) or "anthropic" (url + api_key_env)
url = "http://localhost:1234/v1/chat/completions"
model = "google/gemma-4-e4b"
live_clean = true
timeout = 15
fallback = true  # on a primary failure, retry on [llm_fallback] (LM Studio by default; sends your text there)
lms = ""  # path to LM Studio's lms CLI; "" = find it automatically (store.find_lms)
lms_load = ""  # full load command; "" = load [llm] model with --gpu max -y --context-length 8192

[llm_fallback]
provider = "openai"
url = "http://localhost:1234/v1/chat/completions"
model = "google/gemma-4-e4b"
api_key_env = "ANTHROPIC_API_KEY"
timeout = 15

[builtin]
enabled = true  # bundled llama.cpp runtime (fetched to ~/.murmur/runtime on first use)
backend = "vulkan"  # vulkan | cuda | cpu
model = ""  # GGUF path; "" = auto (~/.murmur/models, then LM Studio's models)
gpu_layers = -1
ctx = 8192
port = 0  # 0 = pick a free port

[privacy]
history_days = 0  # 0 = keep history forever; older rows are purged at startup and once a day
private = false  # private mode: nothing is written to history and nothing is learned

[ui]
startup_toast = true  # one-shot "murmur is running" notification
sounds = true  # soft start/stop/done/error cues
start_sound = true  # the cue when recording begins; turn off to keep only stop/done/error
sound_volume = 0.25  # 0-1

[context]
enabled = true  # read text around the cursor via UI Automation
before_chars = 600
after_chars = 200
thread = false  # use a little of the conversation above the input as reference (local providers only)
thread_chars = 600

[coach]
goals = ["fillers", "pace", "corrections"]  # what to track: fillers, pace, corrections, pauses, clarity, sentence_length
card_hint = false  # after a paste, show a short speaking note on the card ("um x3 · 142 wpm")

[learning]
auto_edits = true  # learn from edits you make to text murmur pasted
phonetic = true  # sounds-like dictionary matching before cleanup
style_auto = true  # refresh style.md automatically
style_every = 50  # dictations between refreshes

[dictation]
toggle = true  # tap the dictate key (< tap_ms) to latch hands-free recording; tap again to stop
tap_ms = 300
silence_ms = 2000  # hands-free: stop after this much silence once you have spoken
no_speech_ms = 6000  # hands-free: cancel if nothing is said within this long

[code]
file_tags = "chat"  # "off" | "chat" (AI chat inputs: @path) | "always" (`path` in the editor, @path in chat)
roots = []  # extra workspace folders to index for file tags

[audio]
device = ""  # input device name; "" = system default (pick one in Settings > Config)

[output]
method = "paste"  # or "type" for apps that block paste

[tones]
casual = "Tone: casual. Relaxed, contractions, short sentences."
formal = "Tone: formal. Complete sentences, professional, no slang."
neutral = "Tone: neutral. Plain, natural writing."
code = "Tone: code context. Preserve identifiers (camelCase, snake_case, kebab-case) and CLI commands verbatim; no trailing period on a lone command; write spoken file names like 'app dot py' as `app.py`. Only format as an identifier what the speaker clearly spells as one (letter by letter, 'underscore', camel case) or that already exists in the surrounding code; ordinary noun phrases like 'max retry count variable' stay prose. Write numbers as digits (5, not five). Treat ordinary sentences as ordinary text."

[stt]
model = "nemo-parakeet-tdt-0.6b-v3"
quantization = "int8"
path = ""  # a folder with your own onnx-asr model (config.json + .onnx); empty = the bundled Parakeet

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
exclude = []  # process names where murmur ignores its hotkeys entirely
pause_fullscreen = true  # ignore hotkeys while a Direct3D exclusive-fullscreen app (a game) is in front
shell_menu = true  # Explorer right-click on an .exe/.lnk: "Don't use murmur in this app" (HKCU, no admin)
"""
NO_COPY = ["windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe"]  # ctrl+c here would SIGINT the running program


def no_copy(cfg):
    """Apps where we never send ctrl+c; configs written before `no_copy` existed get the default."""
    return cfg.get("apps", {}).get("no_copy", NO_COPY)


def apps_exclude(cfg):
    """Process names (lowercase) where murmur ignores its hotkeys."""
    return [a.lower() for a in cfg.get("apps", {}).get("exclude", []) if isinstance(a, str)]


def pause_fullscreen(cfg):
    return bool(cfg.get("apps", {}).get("pause_fullscreen", True))


def is_private(cfg):
    """Private mode: no history rows, no automatic learning."""
    return bool(cfg.get("privacy", {}).get("private", False))


DEFAULT_TONES = tomllib.loads(DEFAULT_CONFIG)["tones"]  # fallback for configs written before [tones]
DEFAULT_PROMPT = """You clean up raw speech-to-text dictation into the text the speaker meant to write.
- Remove filler words (um, uh, like, you know).
- Add punctuation and paragraph breaks.
- Apply self-corrections ("X, actually Y", "no wait Y", "I mean Y") and keep only the final intent: when the speaker corrects a number or word, output ONLY the corrected value, never both.
- Turn spoken lists ("item one, item two, and then item three") into a formatted list in every tone, one item per line.
- Honour spoken punctuation such as "comma", "period", "new paragraph".
- Write dates, times and amounts with digits ("March 3rd", "2:30 PM", "$12.50").
- Remove only fillers, false starts and words the speaker corrected; keep every other content word they said, including 'okay', numbers, lists and phrases like "let me think".
- Keep the speaker's voice; do not add content or answer questions in the text.
"""
DEFAULT_SNIPPETS = '[snippets]\nsig = "Best,\\nEthan"\n'


DEFAULT_FILES = {"config.toml": DEFAULT_CONFIG, "snippets.toml": DEFAULT_SNIPPETS,
                 "dictionary.txt": "# one preferred spelling per line\n", "prompt.md": DEFAULT_PROMPT}


def ensure():
    """Create ~/.murmur and any missing default files. True if config.toml did not exist before (a first run)."""
    HOME.mkdir(exist_ok=True)
    missing = {n: t for n, t in DEFAULT_FILES.items() if not (HOME / n).exists()}
    for name, text in missing.items():
        (HOME / name).write_text(text, encoding="utf-8")
    return "config.toml" in missing


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
        rows = _read_history_unlocked(n)
    return [{"mode": "dictate", **r} for r in rows]  # rows from before command-mode logging have no mode


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
    """Dictation counts and time saved (typing 40 wpm vs speaking 150 wpm); command rows only count in `commands`."""
    now, week = now or datetime.now(), 0
    cmds = [r for r in rows if r.get("mode") == "command"]
    rows = [r for r in rows if r.get("mode") != "command"]
    words = [len(r.get("final", "").split()) for r in rows]
    for r, w in zip(rows, words):
        try:
            week += w if now - datetime.fromisoformat(r["ts"]) < timedelta(days=7) else 0
        except (KeyError, ValueError):
            pass
    total = sum(words)
    return {"dictations": len(rows), "words": total, "minutes_saved": total / 40 - total / 150, "week_words": week, "commands": len(cmds)}


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


def save_config(cfg):
    """Write cfg to config.toml (comments are lost) via tmp + os.replace. Raises ValueError if it can't round-trip."""
    try:
        out = dump_toml(cfg)
        ok = tomllib.loads(out) == cfg
    except (ValueError, TypeError):  # TypeError: dates/times in the config
        ok = False
    if not ok:
        raise ValueError("config has structures the form can't write; edit config.toml in Advanced")
    tmp = HOME / "config.toml.tmp"
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, HOME / "config.toml")


def resolve_device(name, devices):
    """Index of the input device called `name` (exact, else substring, case-insensitive), or None for the default.
    devices: sounddevice.query_devices() dicts; only inputs count."""
    if not name:
        return None
    ins = [(i, d["name"]) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
    low = name.lower()
    return next((i for i, n in ins if n == name), next((i for i, n in ins if low in n.lower()), None))


INT_KEYS = ("builtin.gpu_layers", "builtin.ctx", "builtin.port", "privacy.history_days", "learning.style_every", "context.thread_chars", "dictation.tap_ms", "dictation.silence_ms", "dictation.no_speech_ms", "llm.timeout", "llm_fallback.timeout", "context.before_chars", "context.after_chars")
INT_MIN = {"builtin.gpu_layers": -1}  # -1 = all layers on the GPU
HOTKEYS = ("dictate", "command", "fix_last", "learn_word")


def sec_of(path):
    return path.split(".", 1)[0]


def _int_field(path, v):
    """A whole-number form value >= its minimum (0 unless INT_MIN says otherwise)."""
    try:
        v = int(str(v).strip())
    except ValueError:
        v = -1
    if v < INT_MIN.get(path, 0):
        raise ValueError(f"{path} must be a whole number, {INT_MIN.get(path, 0)} or more")
    return v


def _volume_field(v):
    try:
        v = float(str(v).strip())
    except ValueError:
        v = -1
    if not 0 <= v <= 1:
        raise ValueError("ui.sound_volume must be between 0 and 1")
    return v


def _text_field(path, v, values, base):
    """A stripped string form value: hotkeys can't be empty and URLs must say http (the bundled model has no URL)."""
    v = v.strip()
    sec, key = path.split(".")
    if sec == "hotkeys" and not v:
        raise ValueError(f"hotkey {key} can't be empty")
    tbl = "llm_fallback" if path == "llm_fallback.url" else "llm"
    builtin = (values.get(f"{tbl}.provider") or base.get(tbl, {}).get("provider")) == "builtin"
    if path in ("llm.url", "llm_fallback.url") and not builtin and not v.startswith("http"):
        raise ValueError(f"{path} must start with http")
    return v


def _goals_field(v):
    """[coach] goals: a list of known goal names."""
    from .speech import VALID_GOALS
    bad = [g for g in v if g not in VALID_GOALS]
    if bad:
        raise ValueError(f"coach.goals: unknown {', '.join(map(str, bad))} (use {', '.join(VALID_GOALS)})")
    return list(dict.fromkeys(v))


def _form_value(path, v, values, base):
    """One validated/normalised form value."""
    if path == "coach.goals":
        return _goals_field(v)
    if path in INT_KEYS:
        return _int_field(path, v)
    if path == "ui.sound_volume":
        return _volume_field(v)
    return _text_field(path, v, values, base) if isinstance(v, str) else v


def _apps_table(values, base):
    """The rebuilt [apps] table: the form's tone rows plus no_copy, exclude (lowercased) and pause_fullscreen, which keep their
    old values when the form didn't offer them."""
    old = base.get("apps", {})
    return {**values.get("apps", {}), "no_copy": list(values.get("no_copy", [])),
            "exclude": [a.strip().lower() for a in values.get("exclude", old.get("exclude", [])) if a.strip()],
            "pause_fullscreen": bool(values.get("apps.pause_fullscreen", old.get("pause_fullscreen", True)))}


def form_to_cfg(values, base):
    """New config dict: `base` with the settings form applied. values: "section.key" -> str|bool, plus
    "apps" ({exe: tone}), "no_copy" and "exclude" (lists) and "apps.pause_fullscreen" (bool). Raises ValueError naming the bad field."""
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for path, v in values.items():
        if path in ("apps", "no_copy", "exclude") or sec_of(path) == "apps":
            continue
        sec, key = path.split(".")
        cfg.setdefault(sec, {})[key] = _form_value(path, v, values, base)
    cfg["apps"] = _apps_table(values, base)
    return cfg


def save_tones(tones):
    """Rewrite config.toml with a new [tones] table (comments are lost). Raises ValueError if the file can't round-trip."""
    save_config({**load_config(), "tones": tones})


LEGACY_DEFAULTS = {("llm", "provider"): "openai", ("llm", "fallback"): False, ("builtin", "enabled"): False}  # what a file written before these keys meant


def merged_config(cfg=None):
    """cfg (default: config.toml) over DEFAULT_CONFIG per section so older configs show defaults for new keys.
    [apps] and [tones] are user-owned maps: taken as-is when present (so removed rows stay removed)."""
    base, cfg = tomllib.loads(DEFAULT_CONFIG), cfg if cfg is not None else load_config()
    own = ("apps", "tones")
    out = {k: cfg[k] if k in own and k in cfg else {**v, **cfg.get(k, {})} if isinstance(v, dict) else cfg.get(k, v) for k, v in base.items()}
    out["apps"] = {"exclude": [], "pause_fullscreen": True, **out["apps"]}  # keys added after the user's config was written
    for (sec, key), old in LEGACY_DEFAULTS.items():  # an existing file must not silently switch to the newer defaults
        if key not in cfg.get(sec, {}):
            out.setdefault(sec, {})[key] = old
    return out | {k: v for k, v in cfg.items() if k not in base}


def key_matches(event_name, key):
    """keyboard matches hooks by scan code (left ctrl fires 'right ctrl'); compare the sided event name."""
    return (event_name or "").lower() == key.lower()


def clear_history():
    """Delete every history row (atomic truncate under the history lock). Returns how many were removed."""
    with _locked():
        n = len(_read_history_unlocked())
        tmp = HOME / "history.jsonl.tmp"
        tmp.write_text("", encoding="utf-8")
        os.replace(tmp, HOME / "history.jsonl")
    return n


def strip_history_keys(keys):
    """Remove `keys` from every history row (the Speech page's reset: the dictations stay, the speaking metrics start over)."""
    keys = set(keys)
    with _locked():
        rows = _read_history_unlocked()
        n = sum(1 for r in rows if keys & r.keys())
        tmp = HOME / "history.jsonl.tmp"
        tmp.write_text("".join(json.dumps({k: v for k, v in r.items() if k not in keys}, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        os.replace(tmp, HOME / "history.jsonl")
    return n


def purge_history(days, now=None):
    """Drop history rows older than `days` days (0 = keep everything); atomic rewrite under the history lock.
    Rows with an unreadable timestamp are kept. Returns how many were removed."""
    if not days or days < 0:
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=days)
    with _locked():
        rows = _read_history_unlocked()
        keep = [r for r in rows if _row_time(r) is None or _row_time(r) >= cutoff]
        if len(keep) == len(rows):
            return 0
        tmp = HOME / "history.jsonl.tmp"
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep), encoding="utf-8")
        os.replace(tmp, HOME / "history.jsonl")
    return len(rows) - len(keep)


def _row_time(row):
    try:
        return datetime.fromisoformat(row["ts"])
    except (KeyError, ValueError, TypeError):
        return None


def find_lms(env=None, which=shutil.which):
    """Path of LM Studio's `lms` CLI, or None: %USERPROFILE%/.lmstudio/bin, the app's bundled copy, then PATH."""
    env = os.environ if env is None else env
    home = Path(env.get("USERPROFILE") or Path.home())
    local = Path(env.get("LOCALAPPDATA", "")) if env.get("LOCALAPPDATA") else None
    cands = [home / ".lmstudio" / "bin" / "lms.exe", home / ".lmstudio" / "bin" / "lms"]
    if local:
        cands.append(local / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe")
    found = next((c for c in cands if c.is_file()), None)
    return str(found) if found else which("lms")


def provider_is_local(tbl):
    """Is this [llm]/[llm_fallback] table served from this machine? "builtin", or an openai-style URL on localhost."""
    if tbl.get("provider", "openai") == "builtin":
        return True
    if tbl.get("provider", "openai") != "openai":
        return False
    host = (urlparse(tbl.get("url", "")).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def local_only(cfg):
    """True when no remote provider can be reached: the primary is local and the fallback is off or local."""
    fb = cfg.get("llm", {}).get("fallback") and not provider_is_local(cfg.get("llm_fallback", {}))
    return provider_is_local(cfg.get("llm", {})) and not fb


def learning(cfg, key):
    """[learning] flag with its default on (configs written before the table existed)."""
    return bool(cfg.get("learning", {}).get(key, True))


STATE = "state.json"  # small machine-written bookkeeping (counters), separate from user-edited config


def load_state():
    try:
        return json.loads((HOME / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_state_lock = threading.Lock()


def update_state(fn):
    """Apply fn(state dict) -> None under a lock and write state.json atomically. Returns the new state."""
    with _state_lock:
        st = load_state()
        fn(st)
        tmp = HOME / (STATE + ".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, HOME / STATE)
        return st


runtime = {"llm_starting": False}  # set while the bundled model is starting (the card says "Model starting...")
