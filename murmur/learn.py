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
