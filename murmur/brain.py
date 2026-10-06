"""Prompting, snippets, dictionary/style learning, LLM calls. No win32."""
import re
from . import llm, store

COMMAND_INTRO = ("You edit or write text by voice. You get an instruction and optionally the "
                 "selected text. Apply the instruction to the selection and output the result; "
                 "with no selection, write the text the instruction asks for.")


def tone_for(app, cfg):
    return cfg.get("apps", {}).get(app.lower(), "neutral")


def _tone(ctx):
    tones = {**store.DEFAULT_TONES, **(ctx.get("cfg") or store.load_config()).get("tones", {})}
    return tones.get(ctx.get("tone"), tones["neutral"])


def _dictionary(ctx):
    terms = list(dict.fromkeys(store.load_dictionary() + list(ctx.get("terms", []))))[-300:]
    return "Preferred spellings — fix phonetically similar words to these: " + ", ".join(terms) if terms else ""


def _snippets(ctx):
    names = list(store.load_snippets())
    return ("If the speaker says 'insert <name>' or clearly asks for one of these snippets, "
            "output the exact token {{snippet:<name>}}. Snippets: " + ", ".join(names)) if names else ""


def _style(ctx):
    return "Speaker's style profile:\n" + s if (s := store.load_style()) else ""


def _corrections(ctx):
    pairs = [h for h in store.read_history() if h.get("corrected")][-8:]
    return ("How I corrected past outputs (before → after):\n" +
            "\n".join(f"{h['final'][:300]} → {h['corrected'][:300]}" for h in pairs)) if pairs else ""


def _context(ctx):
    """Existing text around the cursor (dictate mode only)."""
    if ctx.get("mode") == "command":
        return ""
    c = (ctx.get("cfg") or {}).get("context", {})
    before, after = ctx.get("before", "")[-c.get("before_chars", 600):], ctx.get("after", "")[:c.get("after_chars", 200)]
    parts = []
    if before or after:
        mid = before.rstrip()[-1:] not in ".!?" and not before.endswith("\n")
        case = ("You are continuing mid-sentence: start lowercase, no leading capital or punctuation." if mid else
                "The previous sentence is complete: start a new sentence with a capital.")
        parts.append("The cursor is inside existing text. "
                     + (f"Text BEFORE the cursor (continue from it; do not repeat it): «{before}» " if before else "")
                     + (f"Text AFTER: «{after}» " if after else "")
                     + (case + " " if before else "")
                     + "Match the surrounding list/format style; spell names the way they appear in the surrounding text.")
    if ctx.get("selected"):
        parts.append(f"Text is selected and this dictation will REPLACE it: «{ctx['selected'][:300]}»")
    return "\n".join(parts)


def _drop_repeat(final, before):
    """Strip a leading re-emission of the tail of `before` (>= 8 chars, case-insensitive)."""
    tail = before.rstrip()
    for k in range(min(len(tail), len(final)), 7, -1):
        if tail[-k:].lower() == final[:k].lower():
            return final[k:].lstrip()
    return final


def fit(final, before):
    """Deterministic joint with the text before the cursor: drop repeats, fix capital and spacing."""
    if not before or not final:
        return final
    final = _drop_repeat(final, before)
    if final and before.rstrip()[-1:] in tuple(".!?"):
        final = final[0].upper() + final[1:]
    if not final or before.endswith("\n"):
        return final
    b = before[-1]
    if b == " ":
        return final.lstrip(" ")
    closing = b in "\"'" and before[-2:-1] not in (" ", "", "\n")  # an opening quote gets no space
    if (b.isalnum() or b in "_)]}\u201d\u2019.,;:!?" or closing) and (final[0].isalnum() or final[0] == "_"):
        return " " + final
    return final


def _outro(ctx):
    return "Output ONLY the final text."


SECTIONS = [_tone, _dictionary, _snippets, _style, _corrections, _context, _outro]  # add a prompt section = add a function here


def build_system_prompt(ctx, mode="dictate"):
    """ctx: {tone, cfg?, terms?: extra dictionary terms}. mode 'command' = rewrite/generate instead of clean."""
    ctx = {**ctx, "mode": mode}
    head = COMMAND_INTRO if mode == "command" else store.load_prompt()
    return "\n\n".join(p for p in [head] + [f(ctx) for f in SECTIONS] if p)


def apply_snippets(text, snippets):
    """Replace {{snippet:name}} tokens (case-insensitive name)."""
    low = {k.lower(): v for k, v in snippets.items()}
    return re.sub(r"\{\{snippet:([^}]+)\}\}", lambda m: low.get(m.group(1).strip().lower(), m.group(0)), text)


def snippet_only(raw, snippets):
    """Expansion if raw is exactly 'insert <name>' (ignoring case/punctuation), else None."""
    m = re.fullmatch(r"insert (.+)", re.sub(r"[^\w\s]", "", raw.lower()).strip())
    low = {k.lower(): v for k, v in snippets.items()}
    return low.get(m.group(1).strip()) if m else None


PREAMBLE = re.compile(r"here(?:'s| is| are)\b.*\b(text|version|rewrite|result|output)\b.*:$", re.I)
QUOTES = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}


def tidy(text):
    """Strip LLM chatter: 'Here is the ... text:' preamble line, one wrapping quote pair, trailing whitespace."""
    text = text.strip()
    head, _, rest = text.partition("\n")
    if rest.strip() and PREAMBLE.match(head.strip()):
        text = rest.strip()
    a, b = text[:1], text[-1:]
    if len(text) > 1 and QUOTES.get(a) == b and text.count(a) + (text.count(b) if b != a else 0) == 2:
        text = text[1:-1]
    return text.rstrip()


def _chat(cfg, system, user):
    return tidy(llm.chat(cfg, system, user))


def clean(raw, ctx):
    """Cleaned text with snippets applied. Raises on LLM failure; caller falls back to raw."""
    snippets = store.load_snippets()
    if (s := snippet_only(raw, snippets)) is not None:
        return s
    return apply_snippets(_chat(ctx["cfg"], build_system_prompt(ctx), raw), snippets)


def rewrite(selection, instruction, ctx):
    user = (f"Selected text:\n{selection}\n\nInstruction: {instruction}" if selection
            else f"No selection. Instruction: {instruction}")
    return apply_snippets(_chat(ctx["cfg"], build_system_prompt(ctx, "command"), user), store.load_snippets())


COMMON = set("""the a an and or but if so to of in on at for with is are was were be been it this that i you he
she we they my your our me us do does did have has had not no yes will would can could should just very really
also then there here what when where which who how why from by as about up out all some any more most other than
too into over after before again now well okay ok hey hi hello thanks thank please sure right good great yeah
let lets get got go going like know think want need see make take come one two""".split())
WORD = r"\w+(?:[-.]\w+)*"


def _looks_like_term(w):
    return any(c.isupper() or c.isdigit() or c in "_-." for c in w)


def learn_dictionary(final, edited):
    """Words added in `edited` that look like names/terms; ordinary sentence-start words are filtered by COMMON."""
    seen, out = set(re.findall(WORD, final)), []
    for m in re.finditer(WORD, edited):
        w = m.group()
        if w in seen or w in out or w.lower() in COMMON or not _looks_like_term(w):
            continue
        out.append(w)
    return out


def learn_style(history, cfg):
    lines, total = [], 0
    for h in reversed(history[-150:]):  # newest first, ~20k chars cap
        t = h.get("corrected") or h["final"]
        if total + len(t) > 20000:
            break
        lines.append(t); total += len(t)
    system = ("From these dictated messages, write a compact style profile of the speaker: tone, "
              "punctuation habits, favourite phrasings, sign-offs, vocabulary. At most 25 lines. "
              "Output ONLY the profile.")
    return _chat(cfg, system, "\n---\n".join(lines))
