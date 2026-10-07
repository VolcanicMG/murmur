"""How the user's speaking changes over time, from the HEARD transcript (independent of the LLM). Pure, no win32.
History rows (dictate mode) carry: duration_s, speech_s (optional), pauses, fillers, self_corrections, words_raw, plus raw/final/ts/app.
Old rows lack those keys and are skipped."""
import difflib, re, statistics
from datetime import date, datetime, timedelta

NEEDED = 50  # dictations before the numbers mean anything
FILLER_WORDS = {"um", "uh", "umm", "uhh", "erm", "hmm"}
_LIKE = re.compile(r"(?:^|[.!?,;]\s*)like\b|\blike\s*,", re.I)  # "like" counts as a filler only at clause start or before a comma
_CORRECTIONS = re.compile(r"\b(?:actually|no wait|i mean|scratch that|strike that)\b", re.I)
VALID_GOALS = ("fillers", "pace", "corrections", "pauses", "clarity", "sentence_length")
KEY = {"fillers": "fillers_per_100", "pace": "wpm", "corrections": "corrections_per_100", "pauses": "pause_ratio",
       "clarity": "cleanup_distance", "sentence_length": "sentence_length"}
LABEL = {"fillers_per_100": ("Fillers", "per 100 words", 1), "wpm": ("Pace", "words per minute", 0), "corrections_per_100": ("Self-corrections", "per 100 words", 1),
         "pause_ratio": ("Pauses", "per 20 words", 2), "cleanup_distance": ("Cleanup distance", "(0 = nothing to clean)", 2),
         "sentence_length": ("Sentence length", "words", 1)}
LOWER_IS_BETTER = {"fillers_per_100", "corrections_per_100", "pause_ratio", "cleanup_distance"}


def count_words(raw):
    return len(raw.split())


def count_fillers(raw):
    """um/uh/umm/uhh/erm/hmm tokens, plus 'like' when it starts a clause or is followed by a comma."""
    return sum(w.strip(".,!?;:").lower() in FILLER_WORDS for w in raw.split()) + len(_LIKE.findall(raw))


def count_corrections(raw):
    """Cue phrases in the raw transcript: actually / no wait / I mean / scratch that / strike that."""
    return len(_CORRECTIONS.findall(raw))


FIELDS = ("duration_s", "speech_s", "pauses", "fillers", "self_corrections", "words_raw")  # the per-row keys capture_fields writes


def capture_fields(raw, duration_s, vad=None):
    """The history-row keys for one dictation. `vad` is the capture.vad state (speech_s, pauses) when the VAD ran."""
    out = {"duration_s": round(duration_s, 2), "fillers": count_fillers(raw), "self_corrections": count_corrections(raw),
           "words_raw": count_words(raw), "pauses": (vad or {}).get("pauses", 0)}
    if vad and vad.get("speech_s", 0) > 0:
        out["speech_s"] = round(vad["speech_s"], 2)
    return out


def _words(text):
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def _day(ts):
    try:
        return datetime.fromisoformat(ts).date()
    except (TypeError, ValueError):
        return None


def row_metrics(row):
    """Metrics for one dictate row, or None if it predates the capture keys or is too short to measure."""
    n, secs = row.get("words_raw"), row.get("speech_s") or row.get("duration_s")
    day = _day(row.get("ts"))
    if row.get("mode", "dictate") != "dictate" or not n or n < 5 or not secs or secs <= 0 or day is None:
        return None
    raw_words = [w for w in _words(row.get("raw", "")) if w not in FILLER_WORDS]
    final_words = _words(row.get("final", ""))
    sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", row.get("final", ""))))
    return {"date": day, "app": row.get("app", ""), "wpm": n / secs * 60, "fillers_per_100": row.get("fillers", 0) / n * 100,
            "corrections_per_100": row.get("self_corrections", 0) / n * 100, "pause_ratio": row.get("pauses", 0) / max(1, n / 20),
            "cleanup_distance": 1 - difflib.SequenceMatcher(None, raw_words, final_words).ratio() if raw_words and final_words else 0.0,
            "sentence_length": len(final_words) / sentences}


def _metrics(rows):
    return [m for m in (row_metrics(r) for r in rows) if m]


def _key(metric):
    return KEY.get(metric, metric)


def ready(rows):
    """(measurable dictations so far, needed)."""
    return len(_metrics(rows)), NEEDED


def series(rows, metric, days=7):
    """[(date, median)] one point per day that has data: the median of the trailing `days` calendar days, only where that
    window holds at least 3 dictations."""
    k, ms = _key(metric), sorted(_metrics(rows), key=lambda m: m["date"])
    out = []
    for d in sorted({m["date"] for m in ms}):
        window = [m[k] for m in ms if d - timedelta(days=days - 1) <= m["date"] <= d]
        if len(window) >= 3:
            out.append((d, statistics.median(window)))
    return out


def baseline(rows):
    """{metric key: median} over the first 14 days or first 50 dictations (whichever ends first), once at least 25 exist; else None."""
    ms = sorted(_metrics(rows), key=lambda m: m["date"])
    if len(ms) < 25:
        return None
    first = ms[0]["date"]
    head = [m for m in ms[:NEEDED] if m["date"] < first + timedelta(days=14)]
    return {k: statistics.median(m[k] for m in head) for k in LABEL}


def trend(rows, metric):
    """{current, baseline, delta_pct, direction ('up'|'down'|'flat', 5% band), better (True/False/None for pace and length), enough}."""
    k, ms = _key(metric), sorted(_metrics(rows), key=lambda m: m["date"])
    base = baseline(rows)
    recent = [m[k] for m in ms if ms and m["date"] > ms[-1]["date"] - timedelta(days=7)]
    cur = statistics.median(recent) if len(recent) >= 3 else None
    b = base[k] if base else None
    out = {"current": cur, "baseline": b, "delta_pct": None, "direction": "flat", "better": None, "enough": b is not None and cur is not None}
    if out["enough"] and b:
        out["delta_pct"] = (cur - b) / b * 100
        out["direction"] = "flat" if abs(out["delta_pct"]) < 5 else ("up" if out["delta_pct"] > 0 else "down")
        if k in LOWER_IS_BETTER and out["direction"] != "flat":
            out["better"] = out["direction"] == "down"
    return out


def _sentence(k, t, weeks):
    """One summary line for metric key k from its trend t."""
    name, unit, digits = LABEL[k]
    sign = "+" if t["delta_pct"] > 0 else "\u2212" if t["delta_pct"] < 0 else ""
    return (f"{name}: {t['baseline']:.{digits}f} \u2192 {t['current']:.{digits}f} {unit} over {weeks} week{'s' if weeks != 1 else ''} "
            f"({sign}{abs(t['delta_pct']):.0f}%)")


def summary(rows, goals):
    """Plain sentences for the chosen goals, e.g. 'Fillers: 6.2 -> 4.1 per 100 words over 4 weeks (-34%)'."""
    ms = sorted(_metrics(rows), key=lambda m: m["date"])
    n, needed = ready(rows)
    weeks = max(1, round((ms[-1]["date"] - ms[0]["date"]).days / 7)) if ms else 1
    trends = [(_key(g), trend(rows, _key(g))) for g in goals if _key(g) in LABEL]
    lines = [_sentence(k, t, weeks) for k, t in trends if t["enough"] and t["delta_pct"] is not None]
    return lines or [f"Keep dictating: {n} of {needed} dictations measured so far."]


def by_app(rows, metric):
    """Top 5 apps by dictation count (at least 3 each): [(app, median, count)]."""
    k, groups = _key(metric), {}
    for m in _metrics(rows):
        groups.setdefault(m["app"], []).append(m[k])
    top = sorted(((a, statistics.median(v), len(v)) for a, v in groups.items() if a and len(v) >= 3), key=lambda x: -x[2])
    return top[:5]


def hint(row, goals):
    """Short card suffix for one fresh row, only for the goal metrics ('um x3 · 142 wpm'); '' if nothing to say."""
    m, parts = row_metrics(row), []
    if not m:
        return ""
    if "fillers" in goals and row.get("fillers"):
        parts.append(f"um ×{row['fillers']}")
    if "pace" in goals:
        parts.append(f"{m['wpm']:.0f} wpm")
    if "corrections" in goals and row.get("self_corrections"):
        parts.append(f"fixed ×{row['self_corrections']}")
    if "pauses" in goals and row.get("pauses"):
        parts.append(f"pauses ×{row['pauses']}")
    return " · ".join(parts)
