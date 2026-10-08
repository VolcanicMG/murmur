"""Sounds-like dictionary matching, deterministic and before the LLM. Parakeet has no vocabulary biasing, so names come
out as sound-alikes ("pod stead", "get reach"); this swaps confident matches for the dictionary spelling and returns
weaker ones as hints for the prompt. Pure, no win32."""
import difflib, re

_PAIRS = str.maketrans({"b": "p", "d": "t", "g": "k", "c": "k", "q": "k", "v": "f", "z": "s"})  # voiced/unvoiced merge
_SENT_END = re.compile(r"[.!?;:,]$")
_TOKEN = re.compile(r"^(\W*)([A-Za-z0-9'’]*[A-Za-z0-9])(\W*)$")


def sound_key(s):
    """Rough phonetic skeleton: lowercase letters only; ph->f, ck->k, qu->kw, x->ks, kn->n, wr->r (word start), gh silent
    (f at the end), ch/sh/tch/dg->j; b/p d/t g/k/c/q v/f z/s merged; non-leading vowels and h/w/y dropped; repeats collapsed."""
    s = re.sub(r"[^a-z]", "", s.lower())
    s = re.sub(r"^kn", "n", re.sub(r"^wr", "r", s))
    s = re.sub(r"gh$", "f", s).replace("gh", "")
    for a, b in (("tch", "j"), ("dg", "j"), ("ch", "j"), ("sh", "j"), ("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks")):
        s = s.replace(a, b)
    s = s.translate(_PAIRS)
    s = s[:1] + re.sub(r"[aeiouhwy]", "", s[1:])
    return re.sub(r"(.)\1+", r"\1", s)


def _letters(s):
    return re.sub(r"[^a-z]", "", s.lower())


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


FUNCTION_WORDS = {"the", "a", "an", "to", "of", "in", "on", "at", "for", "and", "or"}
_LETTER_NAMES = dict(zip("abcdefghijklmnopqrstuvwxyz", "ay bee see dee ee ef jee aych eye jay kay el em en oh pee kyu ar es tee you vee "
                                                       "doubleyou ex why zee".split()))  # how an acronym is spoken: "LM" -> "el em"


def spoken_forms(term):
    """Letter spellings to compare a dictionary term against: the term itself, plus each 2-4 letter ALL-CAPS token said
    letter by letter ("LM Studio" -> "elemstudio"; Parakeet hears "Elm Studios")."""
    spoken = _letters(" ".join(_say(t) for t in term.split()))
    return [_letters(term)] + ([spoken] if spoken != _letters(term) else [])


def _say(token):
    """'LM' -> 'elem'; any other token unchanged."""
    return "".join(_LETTER_NAMES[c] for c in token.lower()) if re.fullmatch(r"[A-Z]{2,4}", token) else token


def _grade(words, tkey, tl):
    """"hard" | "hint" | None for a window of raw words vs a dictionary term. Only a MULTI-word window can be hard (a split
    name: "pod stead", or an acronym said as letters: "elm studio" vs "el em studio", see spoken_forms): identical sound keys,
    letter similarity >= 0.8, no function word inside, and every word pulls its weight (dropping one must make the match
    worse, i.e. the window swallows nothing extra). One word is only ever a hint. (Keys one edit apart were tried as hard
    matches and rejected: "film studios" and "Helm Studio" became "LM Studio".)"""
    wl = _letters("".join(words))
    wkey, d, sim = sound_key(wl), None, similarity(wl, tl)
    d = edit_distance(wkey, tkey)
    close = d == 0 and sim >= 0.8
    if len(words) > 1 and close and not any(w.lower() in FUNCTION_WORDS for w in words) and all(
            similarity(_letters("".join(words[:k] + words[k + 1:])), tl) < sim for k in range(len(words))):
        return "hard"
    if len(words) > 1 and any(w.lower() in FUNCTION_WORDS for w in words) or wkey[:1] != tkey[:1]:
        return None  # "Pete and" is not a sound-alike; a mishearing keeps its first sound ("tested" is not Podstead)
    return "hint" if d <= (1 if len(tkey) < 5 else 2) and sim >= (0.7 if len(tl) <= 4 else 0.55) else None  # short names: closer


def _tokens(text):
    """[(start, end, lead, core, trail)] for each whitespace token; core = the word without edge punctuation."""
    out = []
    for m in re.finditer(r"\S+", text):
        t = _TOKEN.match(m.group())
        out.append((m.start(), m.end(), *(t.groups() if t else ("", "", m.group()))))
    return out


def _windows(tokens, common):
    """(start, end, words) for every window of 1-3 adjacent words that doesn't cross sentence punctuation, with a possessive 's
    kept outside the last word. Windows that are too short or all common words are skipped."""
    for i in range(len(tokens)):
        for n in (1, 2, 3):
            j = i + n
            if j > len(tokens):
                break
            win = tokens[i:j]
            if n > 1 and any(_SENT_END.search(t[4]) or not t[3] for t in win[:-1]):
                break  # never cross sentence punctuation
            cores = [t[3] for t in win]
            if any(not c for c in cores):
                break
            words = cores[:-1] + [re.sub(r"['\u2019]s$", "", cores[-1])]
            if len(_letters("".join(words))) < 3 or all(_letters(w) in common for w in words):
                continue
            yield i, j, words


def _prepared_terms(terms, common):
    """[(term, letters, sound key)] for the terms worth matching (>= 4 letters, not a common word); a term with an acronym
    gets one row per spoken form."""
    out = []
    for term in terms:
        if len(_letters(term)) >= 4 and _letters(term) not in common:
            out += [(term, tl, sound_key(tl)) for tl in spoken_forms(term)]
    return out


def _best(words, term, tl, tkey):
    """(grade, similarity, plural) of the best way `words` match one spoken form of `term`, or None. A match that needs the
    plural -s dropped is hard only via an acronym's spoken form ("Elm Studios" -> "LM Studio": Parakeet's -s); otherwise it is a
    hint for the PLURAL term (plural=True: "pod sets" -> "Podsteads", a real plural). A window whose singular already IS the
    term ("LM Studios") never matches."""
    forms = _singular(words, tl)
    if any(" ".join(ws).lower() == term.lower() for ws in forms):
        return None  # the term is already there, just plural
    out = []
    for n, ws in enumerate(forms):
        wl = _letters("".join(ws))
        if abs(len(sound_key(wl)) - len(tkey)) > 2:
            continue  # too different to bother
        g = _grade(ws, tkey, tl)
        plural = bool(n) and tl == _letters(term) and g is not None and sound_key(wl) == tkey  # singular sounds like the term
        if plural:
            g = "hint"
        if g:
            out.append((g, similarity(wl, tl), plural))
    return max(out, key=lambda g: (g[0] == "hard", g[1])) if out else None


def _singular(words, tl):
    """The window as heard, plus the same window with a plural -s dropped from the last word when the term has none
    ("Elm Studios" vs "LM Studio")."""
    last = words[-1]
    if len(last) > 3 and last[-1] in "sS" and not tl.endswith("s"):
        return [words, words[:-1] + [last[:-1]]]
    return [words]


def _match(i, j, words, prepared, single=frozenset()):
    """Candidates (start, end, term, grade, sim) for one window against every prepared term. A window that already contains
    the term, or another one-word dictionary term exactly ("Pete and" vs Podstead), never matches."""
    heard, low = " ".join(words), [w.lower() for w in words]
    for term, tl, tkey in prepared:
        if re.search(rf"\b{re.escape(term.lower())}\b", heard.lower()) or any(w in single and w != term.lower() for w in low):
            continue
        if best := _best(words, term, tl, tkey):
            g, sim, plural = best
            yield i, j, term + "s" if plural else term, g, sim  # ponytail: naive plural (+s), fine for names/jargon


def _candidates(tokens, terms, common):
    """Every (start_idx, end_idx, term, grade, sim) over windows of 1-3 adjacent words that don't cross sentence punctuation."""
    prepared, single = _prepared_terms(terms, common), {t.lower() for t in terms if " " not in t}
    return [c for i, j, words in _windows(tokens, common) for c in _match(i, j, words, prepared, single)]


def phonetic_fix(raw, terms, common=frozenset()):
    """(text, hard, hints). hard = [(heard, term)] replaced in text by the term's exact spelling (surrounding punctuation and
    a possessive 's kept); hints = [(heard, term)] weaker matches left alone for the prompt. A window of only common words,
    one already equal to the term, or one crossing sentence punctuation never matches; on overlap the longer term wins."""
    tokens = _tokens(raw)
    cands = sorted(_candidates(tokens, list(dict.fromkeys(terms)), common), key=lambda c: (-len(_letters(c[2])), -c[4], c[1] - c[0], c[0]))  # longest term, best match, shortest window
    taken, hard, best_hint, edits = set(), [], {}, []
    for i, j, term, grade, _ in cands:
        span = set(range(i, j))
        if span & taken:
            continue
        heard = " ".join(t[3] for t in tokens[i:j])
        if grade == "hard":
            taken |= span
            hard.append((heard, term))
            last = tokens[j - 1][3]
            poss = last[-2:] if re.search(r"['’]s$", last) and not term.lower().endswith("s") else ""
            edits.append((tokens[i][0], tokens[j - 1][1], tokens[i][2] + term + poss + tokens[j - 1][4]))
        elif term not in best_hint:  # candidates are sorted best first: keep one hint per term
            best_hint[term] = heard
    text = raw
    for a, b, rep in sorted(edits, reverse=True):
        text = text[:a] + rep + text[b:]
    done = {t for _, t in hard}
    return text, hard, [(h, t) for t, h in best_hint.items() if t not in done and t + "s" not in best_hint]  # plural hint wins
