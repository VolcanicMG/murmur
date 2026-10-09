"""Learning from text the user fixed: dictionary terms from edits and re-dictations, and hand-edit detection for pasted text.
Pure, no win32; brain.py re-exports what other modules import."""
import difflib, re

from . import phonetic
from .prepass import COMMON

WORD = r"\w+(?:[-.]\w+)*"


def _looks_like_term(w):
    return any(c.isupper() or c.isdigit() or c in "_-." for c in w)


def clean_word(text):
    """A selected word for the dictionary: trimmed, without surrounding punctuation."""
    return text.strip().strip(".,;:!?\"'()[]{}")


def learn_dictionary(final, edited):
    """Words added in `edited` that look like names/terms; ordinary sentence-start words are filtered by COMMON."""
    seen, out = set(re.findall(WORD, final)), []
    low = {x.lower() for x in seen}
    for m in re.finditer(WORD, edited):
        w = m.group()
        if w in seen or w in out or w.lower() in COMMON or re.fullmatch(r"[\d.,:/-]+", w) or not _looks_like_term(w):
            continue
        if w[:1].isupper() and w[1:].islower() and re.search(r"(?:^|[.!?])[^\w]*$", edited[:m.start()]) and (
                w.lower() in low or not any(phonetic.phonetic_fix(final, [w])[1:])):
            continue  # sentence-start capital ("Ask", "Eggs", "🎯 Throw"): a name only if it fixes a sound-alike ("pod stead" -> Podstead)
        out.append(w)
    return out


def is_redictation(prev, new):
    """True if `new` is `prev` with 1-3 replaced words (the user said it again, fixed)."""
    if difflib.SequenceMatcher(None, prev.lower(), new.lower()).ratio() <= 0.8:
        return False
    ops = difflib.SequenceMatcher(None, prev.split(), new.split()).get_opcodes()
    changed = [o for o in ops if o[0] != "equal"]
    return bool(changed) and all(o[0] == "replace" for o in changed) and sum(o[4] - o[3] for o in changed) <= 3


def redictation_terms(prev, new):
    """Dictionary terms learned from a quick re-dictation of the same text."""
    return learn_dictionary(prev, new) if is_redictation(prev, new) else []


EDIT_SIM, EDIT_MAX_WORDS, EDIT_FIELD_CHARS = 0.75, 4, 2000


def _edit_ok(want, win):
    """Word-level opcodes of want -> win that count as a hand edit: only replacements/insertions, at most 4 words changed."""
    ops = [o for o in difflib.SequenceMatcher(None, want, win).get_opcodes() if o[0] != "equal"]
    return bool(ops) and all(o[0] != "delete" for o in ops) and sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops) <= EDIT_MAX_WORDS


def detect_edit(pasted, field_text):
    """The span of `field_text` where the user hand-corrected `pasted`, or None. Windows of words anywhere in the last 2000
    chars are compared (typing after the text doesn't matter); the best one is returned only when it is >= 0.75 similar
    (characters) but not identical, differs by replacements/insertions of at most 4 words, and starts and ends on word
    boundaries. None when the text is unchanged, gone, or rewritten."""
    want = pasted.split()
    ft = field_text[-EDIT_FIELD_CHARS:]
    words = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", ft)]
    if not want or not words:
        return None
    flat, best, span = " ".join(want), 0.0, None
    for lo in range(len(words)):
        for n in range(max(1, len(want) - EDIT_MAX_WORDS), len(want) + EDIT_MAX_WORDS + 1):
            win = [w[2] for w in words[lo:lo + n]]
            if len(win) < n:
                break
            if win == want:
                return None  # still there exactly as pasted
            if difflib.SequenceMatcher(None, want, win).real_quick_ratio() < 0.5 or not _edit_ok(want, win):
                continue
            r = difflib.SequenceMatcher(None, flat, " ".join(win)).ratio()
            if r > best:
                best, span = r, (lo, lo + n)
    if span is None or best < EDIT_SIM:
        return None
    return ft[words[span[0]][0]:words[span[1] - 1][1]]


def _bare(w):
    return re.sub(r"[^\w]", "", w.lower())


MERGE_SIM = 0.75  # words merged/split by an edit must be closer ("pot stead" -> Podstead 0.88; "Can you throw" -> "Throw" 0.63 is a rewrite)


def _close(a, b, sim=0.6):
    """Is word `a` plausibly a misspelling/mishearing of `b`: letters >= `sim` alike, or the same sound key."""
    x, y = _bare(a), _bare(b)
    return x == y or difflib.SequenceMatcher(None, x, y).ratio() >= sim or (bool(x) and phonetic.sound_key(x) == phonetic.sound_key(y))


def edit_is_a_correction(pasted, edited):
    """True only when the edit looks like fixing what murmur heard: at least one replaced word, no insertions, and every
    replaced word pair is orthographically close or sounds alike. A changed mind ("staging" -> "production"), a retyped
    sentence or similar text elsewhere is not a correction and must not feed the few-shot block."""
    a, b = pasted.split(), edited.split()
    ops = [o for o in difflib.SequenceMatcher(None, a, b).get_opcodes() if o[0] != "equal"]
    if not ops or any(tag != "replace" for tag, *_ in ops):
        return False
    for _, i1, i2, j1, j2 in ops:
        if i2 - i1 == j2 - j1:
            if not all(_close(x, y) for x, y in zip(a[i1:i2], b[j1:j2])):
                return False
        elif not _close("".join(a[i1:i2]), "".join(b[j1:j2]), MERGE_SIM):
            return False
    return True


def learn_from_edit(pasted, edited):
    """(terms to add to the dictionary, whether this counts as a correction). A change in letter case alone teaches nothing
    and is not recorded; a change that is not a spelling/hearing fix may still yield dictionary terms (by the usual
    heuristic) but is not recorded as a correction."""
    norm = lambda t: [w.lower() for w in t.split()]
    if norm(pasted) == norm(edited):
        return [], False
    return learn_dictionary(pasted, edited), edit_is_a_correction(pasted, edited)


EDIT_CAP, TEXT_CAP = 8, 200


def _tokens(text):
    """(start, end, normalised word) for each word of `text`; pure punctuation is not a word."""
    return [(m.start(), m.end(), b) for m in re.finditer(r"\S+", text) if (b := _bare(m.group()))]


SOUND_SIM = 0.75  # sound skeletons this alike count as misheard ("bolts" plts / "vaults" flts; "items"/"repos" 0.29)


def _kind(src, dst):
    """'misheard' if the replaced words are close in letters or sound (merged/split words need MERGE_SIM), else 'word_choice'."""
    if len(src) == len(dst):
        near = lambda x, y: _close(x, y) or difflib.SequenceMatcher(None, phonetic.sound_key(x), phonetic.sound_key(y)).ratio() >= SOUND_SIM
        return "misheard" if all(near(x, y) for x, y in zip(src, dst)) else "word_choice"
    return "misheard" if _close("".join(src), "".join(dst), MERGE_SIM) else "word_choice"


def classify_edit(pasted, sent):
    """What the user did to `pasted` in `sent` (the text they kept or sent), by word alignment of lowercased, punctuation-free
    words (case/punctuation-only changes are not edits). The pasted part of `sent` runs from the first to the last matched
    word; words outside it are `added` and never make the paste "edited".

    Returns:
        {outcome: unchanged|edited|updated|missing, rewrite, added (words), edits: [{kind: misheard|word_choice|restored|
        removed, from, to}] (<= 8, strings <= 200 chars), span: the user's version of the pasted part, words / matched /
        span_words: pasted words, how many were found, words in span}. Under half of the
        pasted words matched = edited with rewrite=True and no edits (too different to attribute)."""
    a, b = _tokens(pasted), _tokens(sent)
    na, nb = [t[2] for t in a], [t[2] for t in b]
    ops = difflib.SequenceMatcher(None, na, nb, autojunk=False).get_opcodes()
    eq = [o for o in ops if o[0] == "equal"]
    res = {"outcome": "missing", "rewrite": False, "added": 0, "edits": [], "span": "", "words": len(a), "matched": 0, "span_words": 0}
    if not eq:
        return res
    lo, hi = eq[0][3], eq[-1][4]
    said = lambda i, j: " ".join(pasted[s:e] for s, e, _ in a[i:j])[:TEXT_CAP]
    kept = lambda i, j: " ".join(sent[s:e] for s, e, _ in b[i:j])[:TEXT_CAP]
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            continue
        if k in (0, len(ops) - 1):  # sent words before/after the paste are added; a pasted edge word they replace is an edit
            n = min(i2 - i1, j2 - j1)
            res["added"] += (j2 - j1) - n
            if k == 0:
                lo = j1 = j2 - n
            else:
                hi = j2 = j1 + n
            if i1 == i2:
                continue
            tag = "delete" if j1 == j2 else "replace"
        kind = {"insert": "restored", "delete": "removed"}.get(tag) or _kind(na[i1:i2], nb[j1:j2])
        res["edits"].append({"kind": kind, "from": said(i1, i2), "to": kept(j1, j2)})
    res.update(span=sent[b[lo][0]:b[hi - 1][1]], matched=sum(o[2] - o[1] for o in eq), span_words=hi - lo)
    if res["matched"] * 2 < len(a):
        res.update(outcome="edited", rewrite=True, edits=[])
    else:
        res["outcome"] = "edited" if res["edits"] else "updated" if res["added"] else "unchanged"
    res["edits"] = res["edits"][:EDIT_CAP]
    return res


def learn_from_result(pasted, res):
    """Ethan's rule for a classify_edit result: (dictionary terms, `corrected` text or None). Only "misheard" edits teach
    terms (learn_dictionary over the user's span, kept to the words those edits typed, so its sentence-start and sound-alike
    guards see real context); the row becomes a few-shot correction only with a misheard edit and no rewrite."""
    misheard = [e for e in res["edits"] if e["kind"] == "misheard"]
    if not misheard or res["rewrite"]:
        return [], None
    typed = {w for e in misheard for w in re.findall(WORD, e["to"])}
    return [w for w in learn_dictionary(pasted, res["span"]) if w in typed], res["span"]
