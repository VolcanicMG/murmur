"""Deterministic text work that needs no model: word/number normalisation (spoken numbers, years, phone numbers, money), the
'scratch that' cues, and the pre-LLM rewrites (number self-corrections, paragraph breaks, e-mail addresses and URLs). Pure, no
win32; brain.py re-exports what other modules and the eval harness import."""
import re

COMMON = set("""the a an and or but if so to of in on at for with is are was were be been it this that i you he
she we they my your our me us do does did have has had not no yes will would can could should just very really
also then there here what when where which who how why from by as about up out all some any more most other than
too into over after before again now well okay ok hey hi hello thanks thank please sure right good great yeah
let lets get got go going like know think want need see make take come one two""".split())
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_ORDINALS = {w: str(i + 1) for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth".split())}
_ORDINALS.update({w: str(10 * (i + 2)) for i, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split())})
_SPOKEN_PUNCT = re.compile(r"\b(?:question mark|exclamation (?:mark|point)|full stop|new line|new paragraph)\b|"
                           r"\b(?:comma|period|colon|semicolon|dash|dot)\b")
_SAME = {"okay": "ok"}


def _words(text, raw=False):
    """Lowercase content tokens: spoken numbers/ordinals -> digits ('zero point forty four' -> '0.44', 'twenty first' ->
    '21', 'one hundred' -> '100'), apostrophes stripped, okay=ok; raw=True also drops spoken punctuation ('comma')."""
    text = text.lower().replace("'", "").replace("\u2019", "")
    if raw:
        text = _SPOKEN_PUNCT.sub(" ", text)
    text = _PHONE.sub(lambda m: "".join(g for g in m.groups() if g), text)  # (555) 123-4567 / 555-1234 -> one digit token, like the spoken digits
    text = re.sub(r"(?<=\d)-(?=\d)", "", text)
    toks = re.findall(r"\d+(?:\.\d+)?(?:st|nd|rd|th)?|[a-z]+", text)
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in FILLERS:
            i += 1; continue
        if (run := _digit_run(toks, i)) > 0:  # 'five five five one two three four' -> '5551234'
            out.append("".join(str(_UNITS[x]) for x in toks[i:i + run])); i += run; continue
        if (yr := _year(toks, i)):  # 'nineteen ninety nine' -> '1999', 'twenty twenty six' -> '2026'
            out.append(yr[0]); i = yr[1]; continue
        if re.fullmatch(r"\d+(?:\.\d+)?(?:st|nd|rd|th)", t):
            out.append(re.sub(r"[a-z]+$", "", t)); i += 1; continue
        if t in _ORDINALS:
            out.append(_ORDINALS[t]); i += 1; continue
        num, i = _number(toks, i)
        if num is not None:
            if i + 1 < len(toks) and toks[i] == "point":
                frac, i = _fraction(toks, i + 1)
                if frac:
                    num = f"{num}.{frac}"
            if i < len(toks) and toks[i] in ("dollar", "dollars"):  # 'twelve dollars and fifty cents' -> '12.50', like '$12.50'
                j = i + 1 + (i + 1 < len(toks) and toks[i + 1] == "and")
                cents, k = _number(toks, j) if j < len(toks) else (None, j)
                if cents is not None and k < len(toks) and toks[k] in ("cent", "cents"):
                    num, i = f"{num}.{int(cents):02d}", k + 1
            out.append(num); continue
        out.append(_SAME.get(t, t)); i += 1
    return out


_PHONE = re.compile(r"\(?(\d{3})\)?[-\s.]?(\d{3})[-\s.]?(\d{4})(?!\d)|(?<!\d)(\d{3})[-\s](\d{4})(?!\d)")
_DIGIT_WORDS = {w for w, v in _UNITS.items() if v < 10}


def _digit_run(toks, i):
    """Length of a run of >= 7 spoken single digits at toks[i] (a phone number), else 0."""
    n = 0
    while i + n < len(toks) and toks[i + n] in _DIGIT_WORDS:
        n += 1
    return n if n >= 7 else 0


def _year(toks, i):
    """('1999', next index) for a spoken year at toks[i] ('nineteen ninety nine', 'twenty twenty six'), else None."""
    a, j = _below100(toks, i)
    if a not in (19, 20):
        return None
    b, k = _below100(toks, j)
    return (f"{a}{b:02d}", k) if b is not None and 10 <= b <= 99 else None


def _below100(toks, i):
    """(int, next index) for 0-99 spoken at toks[i] ('forty four', 'twenty first'), else (None, i)."""
    t = toks[i] if i < len(toks) else ""
    nxt = toks[i + 1] if i + 1 < len(toks) else ""
    if t in _TENS:
        if nxt in _UNITS and 1 <= _UNITS[nxt] <= 9:
            return _TENS[t] + _UNITS[nxt], i + 2
        if nxt in _ORDINALS and 1 <= int(_ORDINALS[nxt]) <= 9:
            return _TENS[t] + int(_ORDINALS[nxt]), i + 2
        return _TENS[t], i + 1
    if t in _UNITS:
        return _UNITS[t], i + 1
    return None, i


def _number(toks, i):
    """(digit string, next index) for a spoken number at toks[i], with hundred/thousand multipliers, else (None, i)."""
    val, j = _below100(toks, i)
    if val is None:
        return None, i
    for word, mult in (("thousand", 1000), ("hundred", 100)):
        if j < len(toks) and toks[j] == word:
            tail, k = _below100(toks, j + 1)
            val, j = val * mult + (tail or 0), (k if tail is not None else j + 1)
    return str(val), j


def _fraction(toks, i):
    """Digits after 'point': single digit words concatenate ('four four' -> '44'); 'forty four' -> '44'."""
    frac = ""
    while i < len(toks):
        n, j = _number(toks, i)
        if n is None:
            break
        frac, i = frac + n, j
        if len(n) > 1:
            break
    return frac, i


COMMANDS = {"scratch that": "undo", "undo that": "undo", "undo": "undo", "delete that": "undo"}  # phrase -> Session._cmd_<name>
FILLERS = {"um", "uh", "umm", "uhh", "erm", "hmm"}
CUE = re.compile(r"\b(?:scratch that|delete that|never ?mind that|strike that)(?:\s+last\s+(?:one|part|bit|sentence))?", re.I)
_AFTER_OK = re.compile(r"\s*(?:$|[.,;:!])")  # a cue is its own clause: end of text or . , ; : ! (not ?) follows
_FILL_RE = "|".join(FILLERS)
_BEFORE_OK = re.compile(r"(?:^|[.!?,;:])\s*(?:(?:%s)\b[\s,.]*)*$" % _FILL_RE, re.I)
_END = re.compile(r"[.!?](?=\s|$)")  # a sentence end: not the dot in 0.44 or example.com
_AFTER_END = re.compile(r"[\s.!?,;:]*$")
_FRAME = {"dont", "don't", "do", "not", "didn't", "never", "to", "can", "could", "should", "would", "will", "you", "i", "we",
          "they", "just", "please", "let's", "lets"}  # '... scratch that' after these is ordinary speech
_FILLER_ONLY = re.compile(r"[\W_]*(?:(?:%s)\b[\W_]*)*" % "|".join(FILLERS), re.I)


def spoken_command(raw):
    """Handler name if the whole transcript (minus punctuation/fillers) is exactly a command phrase."""
    words = [w for w in re.sub(r"[^\w\s']", " ", raw.lower()).split() if w not in FILLERS]
    return COMMANDS.get(" ".join(words))


def _prev_word(pre):
    words = [w for w in re.findall(r"[\w']+", pre.lower()) if w not in FILLERS]
    return words[-1] if words else ""


def find_cue(text):
    """First cue that stands as its own clause, else None. A cue must follow the start or clause punctuation (. ! ? , ; :,
    then optional fillers) and be followed by end or . , ; : ! (not ?). 'scratch that' may also close an unpunctuated
    transcript (Parakeet drops punctuation mid-utterance) unless the word before it is a frame word ('don't scratch that').
    Ordinary sentences ('I'll delete that file', 'Did you delete that?') never match."""
    for m in CUE.finditer(text):
        if not _AFTER_OK.match(text, m.end()):
            continue
        pre = text[:m.start()]
        if _BEFORE_OK.search(pre):
            return m
        if m.group().lower().startswith("scratch") and _AFTER_END.match(text, m.end()) and _prev_word(pre) not in _FRAME:
            return m
    return None


def has_scratch(raw):
    """True if the transcript contains a scratch cue ('scratch that', 'delete that', ...) as its own clause."""
    return find_cue(raw) is not None


def _blank(seg):
    """Nothing but fillers/punctuation."""
    return _FILLER_ONLY.fullmatch(seg) is not None


def _last_end(text):
    """Index of the last sentence-ending . ! ? (followed by space or end), or -1."""
    ends = [m.start() for m in _END.finditer(text)]
    return ends[-1] if ends else -1


def apply_scratch(raw):
    """Deterministic 'scratch that': drop each cue and the sentence segment before it (back to the previous . ! ? or the
    start; filler-only fragments are skipped over). Text after the cue in the same sentence is kept as the replacement.
    May return ''."""
    while m := find_cue(raw):
        pre, post = raw[:m.start()], raw[m.end():]
        i = _last_end(pre)
        kept, seg = pre[:i + 1], pre[i + 1:]
        if _blank(seg):  # cue opens its sentence: it scratches the previous sentence instead
            while kept:
                body = kept.rstrip()[:-1]  # drop the final delimiter
                j = _last_end(body)
                gone, kept = body[j + 1:], body[:j + 1]
                if not _blank(gone):
                    break
        post = re.sub(r"^[\s,;:.!?-]+", "", post)
        raw = (kept.rstrip() + " " + post).strip()
    return "" if _blank(raw) else spoken_forms(raw)


# ---- deterministic pre-pass: spoken corrections, breaks, e-mail addresses and URLs (before the LLM, which keeps the same rules)
_NUMWORD = set(_UNITS) | set(_TENS) | {"hundred", "thousand", "point"}
_CORRECTION_CUES = (("actually",), ("no", "wait"), ("wait", "no"), ("i", "mean"))


def _edge(tok):
    return re.sub(r"^[^\w.]+|[.,;:!?]+$", "", tok.lower())


def _numberish(tok):
    c = _edge(tok)
    return c in _NUMWORD or re.fullmatch(r"\d+(?:\.\d+)?", c) is not None


def correct_numbers(text):
    """'... 0.43, actually 0.44 ...' -> '... 0.44 ...': when a number is corrected with actually / no wait / I mean and BOTH
    sides are numbers (digits or spoken), only the second one is kept. Words are left to the LLM."""
    for _ in range(5):
        toks = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text)]
        names = [_edge(t[2]) for t in toks]
        hit = None
        for i in range(len(toks)):
            n = next((len(c) for c in _CORRECTION_CUES if tuple(names[i:i + len(c)]) == c), 0)
            if not n:
                continue
            lo = i
            while lo > 0 and _numberish(toks[lo - 1][2]):
                lo -= 1
            hi = i + n
            while hi < len(toks) and _numberish(toks[hi][2]):
                hi += 1
            if lo < i and hi > i + n and len(_words(" ".join(t[2] for t in toks[lo:i]))) == 1 and len(_words(" ".join(t[2] for t in toks[i + n:hi]))) == 1:
                hit = (toks[lo][0], toks[i + n][0])
                break
        if not hit:
            return text
        text = text[:hit[0]] + text[hit[1]:]
    return text


_BREAK = re.compile(r"(?i)(?P<pre>[,.;]?\s*)\b(?P<w>new paragraph|new line)\b(?P<post>[,.]?)\s*")
_NOT_BEFORE = {"a", "an", "the", "this", "that", "my", "your", "our", "another", "each", "every", "one", "in", "per", "of", "no", "any", "first", "last", "next"}
_NOT_AFTER = {"of", "is", "was", "and", "in", "to", "here", "there", "with", "for", "at", "that", "which"}


def spoken_breaks(text):
    """'new paragraph' -> a blank line, 'new line' -> a line break, when they are commands: next to punctuation or the ends, or
    between words that don't make them a noun phrase ('a new paragraph of text' stays)."""
    def sub(m):
        before, after = text[:m.start()].split(), text[m.end():].split()
        if not before and not after:
            return m.group(0)
        prev = re.sub(r"\W", "", before[-1].lower()) if before else ""
        nxt = re.sub(r"\W", "", after[0].lower()) if after else ""
        explicit = bool(m.group("pre").strip() or m.group("post"))  # punctuation right next to the phrase
        if not explicit and (prev in _NOT_BEFORE or nxt in _NOT_AFTER):
            return m.group(0)
        return m.group("pre").strip() + ("\n\n" if m.group("w").lower() == "new paragraph" else "\n")
    return _BREAK.sub(sub, text)


def restore_breaks(prepared, final):
    """spoken_breaks turned "new line"/"new paragraph" into line breaks before the LLM, which sometimes reflows them away
    ("plums,\nand a bag" -> "plums, and a bag"). For every break the output lost, find the two words before it and put the
    break back after them (and any punctuation that follows). Breaks already present are left alone."""
    if prepared.count("\n") <= final.count("\n"):
        return final
    out = final
    for m in re.finditer(r"(\n\n?)", prepared):
        words = re.findall(r"[A-Za-z0-9']+", prepared[:m.start()])[-2:]
        if not words:
            continue
        anchor = re.compile(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b[,.;:!?]*", re.I)
        hit = anchor.search(out)
        if hit and not out[hit.end():].startswith("\n"):
            out = out[:hit.end()].rstrip(" ") + m.group(1) + out[hit.end():].lstrip(" ")
    return out


_TLD = r"(?:com|org|net|edu|gov|io|co|dev|app|ai|me|us|uk|de|fr)"
_LABEL = r"[a-z0-9][a-z0-9_-]*"
_EMAIL = re.compile(rf"(?i)\b({_LABEL}(?:\s+dot\s+{_LABEL})*)\s+at\s+({_LABEL}(?:\s+dot\s+{_LABEL})*\s+dot\s+{_TLD}\b)")
_DOMAIN = re.compile(rf"(?i)\b(www\.\s*)?({_LABEL}(?:\s+dot\s+{_LABEL})*)\s+dot\s+({_TLD})\b((?:\s+slash\s+{_LABEL}(?:\s+dot\s+{_LABEL})?)*)")


def _dots(s):
    return re.sub(r"(?i)\s+dot\s+", ".", s)


def spoken_addresses(text):
    """'john dot smith at example dot com' -> john.smith@example.com; 'double u double u double u dot x dot com slash a' ->
    www.x.com/a. Conservative: a known TLD is required and no common word may be a label ('the dot com bubble' stays)."""
    def email(m):
        local, domain = _dots(m.group(1)).lower(), _dots(m.group(2)).lower()
        if local in COMMON or any(w in COMMON for w in domain.split(".")[:-1]):
            return m.group(0)
        return f"{local}@{domain}"

    def domain(m):
        labels = (_dots(m.group(2)) + "." + m.group(3)).lower().split(".")
        if any(w in COMMON for w in labels[:-1]):
            return m.group(0)
        path = "".join("/" + _dots(p).lower() for p in re.split(r"(?i)\s+slash\s+", m.group(4))[1:])
        return (m.group(1) or "").replace(" ", "").lower() + ".".join(labels) + path
    text = re.sub(r"(?i)\bdouble u double u double u dot\b", "www.", text)
    return _DOMAIN.sub(domain, _EMAIL.sub(email, text))


_QUOTE = re.compile(r"(?i)\b(?:open |begin |start )?quote\b[,:]?\s+(?P<q>.+?)[,.]?\s+(?:(?:close|end) quotes?|unquote|quotes? (?:close|closed|end)|quote)\b[,.]?")


def spoken_quotes(text):
    """'quote starting end quote' -> '"starting"'; the closer may be end/close quote, unquote, or just 'quote' again
    ('the thing was quote super quote cool'). The quoted words keep their case; a comma/period the model put right before the
    closing cue is dropped, one after it is kept."""
    return _QUOTE.sub(lambda m: '"' + m.group("q").strip() + '"' + (m.group(0)[-1] if m.group(0)[-1] in ".," else ""), text)


def spoken_forms(text):
    """The deterministic pre-LLM rewrites, in order: number self-corrections, quotes, paragraph/line breaks, e-mail addresses and URLs."""
    return spoken_addresses(spoken_breaks(spoken_quotes(correct_numbers(text))))




_SNAKE = re.compile(r"`?\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]+)+)\b`?")
_SPELLED = re.compile(r"underscore|camel|snake|kebab|dash|hyphen|capital|spell", re.I)


def restore_prose_identifiers(raw, out, ctx):
    """Code tone: the model sometimes turns an ordinary noun phrase ('max retry count variable') into an identifier
    (max_retry_count). Put the words back unless the speaker spelled an identifier or it already appears in the surrounding code."""
    if ctx.get("tone") != "code" or _SPELLED.search(raw):
        return out
    nearby, low = (ctx.get("before", "") + " " + ctx.get("after", "")), " ".join(raw.lower().split())

    def sub(m):
        tok = m.group(1)
        words = [w.lower() for w in (tok.split("_") if "_" in tok else re.findall(r"[a-z0-9]+|[A-Z][a-z0-9]*", tok))]
        spoken = " ".join(words)
        return spoken if tok not in nearby and len(words) > 1 and spoken in low else m.group(0)
    return _SNAKE.sub(sub, out)
