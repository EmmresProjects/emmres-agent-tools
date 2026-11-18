"""Text and language utilities shared by every engine.

Covers normalisation, sentence/word segmentation, readability metrics, script
detection, bidi controls, ICU MessageFormat parsing, term matching and
similarity scoring. No I/O of its own: engines pass text in, get facts out.
"""

import math
import re
import unicodedata
from difflib import SequenceMatcher

S = None  # support module, injected by init()


def init(deps):
    global S
    S = deps["support"]


# --------------------------------------------------------------------------- #
# Normalisation and segmentation
# --------------------------------------------------------------------------- #
_WS = re.compile(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+|\n{2,}")


def truncate(text, n=140):
    return S.truncate(text, n)


def slugify(text, maxlen=72):
    return S.slugify(text, maxlen)


def norm_ws(text):
    return _WS.sub(" ", (text or "")).strip()


def is_blank(text):
    return not norm_ws(text)


def sentences(text):
    text = norm_ws((text or "").replace("\r\n", "\n"))
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ\u00f1\u00d1]+(?:['\u2019-][A-Za-zÀ-ÖØ-öø-ÿ]+)*|\d+(?:[.,]\d+)*",
                   re.UNICODE)


def words(text):
    return _WORD.findall(text or "")


def word_count(text):
    return len(words(text))


def syllables_en(word):
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    groups = re.findall(r"[aeiouy]{1,2}", w)
    return max(1, len(groups))


def flesch_reading_ease(text):
    sents = sentences(text)
    wds = words(text)
    if not sents or not wds:
        return None
    syl = sum(syllables_en(w) for w in wds)
    return round(206.835 - 1.015 * (len(wds) / float(len(sents))) - 84.6 * (syl / float(len(wds))), 2)


def flesch_kincaid_grade(text):
    sents = sentences(text)
    wds = words(text)
    if not sents or not wds:
        return None
    syl = sum(syllables_en(w) for w in wds)
    return round(0.39 * (len(wds) / float(len(sents))) + 11.8 * (syl / float(len(wds))) - 15.59, 2)


def lexicon_metrics(text, lexicon=None):
    """Report objective readability facts used by cognitive and content checks."""
    sents = sentences(text)
    wds = words(text)
    lexicon = lexicon or {}
    jargon = set(w.lower() for w in lexicon.get("jargon", []))
    long_words = [w for w in wds if len(w) > 12]
    found_jargon = sorted({w.lower() for w in wds if w.lower() in jargon})
    passive = len(re.findall(r"\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b", text or "", re.I))
    nominal = len(re.findall(r"\b\w{4,}(?:tion|ment|ance|ence|ility|ness)\s+of\b", text or "", re.I))
    upper_runs = len(re.findall(r"\b[A-Z]{4,}\b", text or ""))
    return {
        "sentences": len(sents),
        "words": len(wds),
        "avg_sentence_words": round(len(wds) / float(len(sents)), 2) if sents else 0,
        "avg_word_length": round(sum(len(w) for w in wds) / float(len(wds)), 2) if wds else 0,
        "long_words_over_12": len(long_words),
        "flesch_reading_ease": flesch_reading_ease(text),
        "flesch_kincaid_grade": flesch_kincaid_grade(text),
        "passive_constructions": passive,
        "nominalisations": nominal,
        "all_caps_runs": upper_runs,
        "jargon_terms": found_jargon,
        "longest_sentence_words": max([word_count(s) for s in sents], default=0),
        "paragraph_breaks": (text or "").count("\n\n"),
    }


# --------------------------------------------------------------------------- #
# Scripts, characters, bidi
# --------------------------------------------------------------------------- #
BIDI_CONTROLS = {
    "\u200e": "LRM", "\u200f": "RLM", "\u202a": "LRE", "\u202b": "RLE",
    "\u202c": "PDF", "\u202d": "LRO", "\u202e": "RLO", "\u2066": "LRI",
    "\u2067": "RLI", "\u2068": "FSI", "\u2069": "PDI", "\u061c": "ALM",
}

SCRIPT_RANGES = [
    ("latin", 0x0041, 0x024F), ("greek", 0x0370, 0x03FF), ("cyrillic", 0x0400, 0x04FF),
    ("hebrew", 0x0590, 0x05FF), ("arabic", 0x0600, 0x06FF), ("arabic_supplement", 0x0750, 0x077F),
    ("devanagari", 0x0900, 0x097F), ("bengali", 0x0980, 0x09FF), ("tamil", 0x0B80, 0x0BFF),
    ("thai", 0x0E00, 0x0E7F), ("lao", 0x0E80, 0x0EFF), ("tibetan", 0x0F00, 0x0FFF),
    ("myanmar", 0x1000, 0x109F), ("georgian", 0x10A0, 0x10FF), ("ethiopic", 0x1200, 0x137F),
    ("han", 0x4E00, 0x9FFF), ("hiragana", 0x3040, 0x309F), ("katakana", 0x30A0, 0x30FF),
    ("hangul", 0xAC00, 0xD7AF), ("khmer", 0x1780, 0x17FF),
    ("tagalog_baybayin", 0x1700, 0x171F), ("halfwidth_forms", 0xFF00, 0xFFEF),
    ("emoji", 0x1F300, 0x1FAFF), ("math", 0x2200, 0x22FF),
]

RTL_SCRIPTS = {"hebrew", "arabic", "arabic_supplement", "syriac", "thaana", "nko"}


def script_of(ch):
    cp = ord(ch)
    for name, lo, hi in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    return "other"


def script_profile(text):
    counts = {}
    for ch in text or "":
        if ch.isalpha() or unicodedata.category(ch).startswith("S"):
            name = script_of(ch)
            if name == "other" and ch.isalpha():
                name = "latin" if ch.isascii() else "other_letters"
            counts[name] = counts.get(name, 0) + 1
    total = sum(counts.values()) or 1
    return {k: round(v / float(total), 4) for k, v in sorted(counts.items(), key=lambda x: -x[1])}


def bidi_profile(text):
    """Counts of bidi control characters and a naive base-direction guess."""
    controls = {}
    for ch in text or "":
        if ch in BIDI_CONTROLS:
            controls[BIDI_CONTROLS[ch]] = controls.get(BIDI_CONTROLS[ch], 0) + 1
    strong_rtl = sum(1 for ch in (text or "") if script_of(ch) in RTL_SCRIPTS)
    strong_ltr = sum(1 for ch in (text or "") if ch.isalpha() and script_of(ch) in ("latin", "greek", "cyrillic"))
    base = "rtl" if strong_rtl > strong_ltr else "ltr"
    isolates = sum(controls.get(k, 0) for k in ("LRI", "RLI", "FSI"))
    terminates = controls.get("PDI", 0)
    embeddings = sum(controls.get(k, 0) for k in ("LRE", "RLE", "LRO", "RLO"))
    pdfs = controls.get("PDF", 0)
    return {
        "controls": controls,
        "strong_rtl_chars": strong_rtl,
        "strong_ltr_chars": strong_ltr,
        "base_direction_guess": base,
        "unbalanced_isolates": isolates - terminates,
        "unbalanced_embeddings": embeddings - pdfs,
        "mirror_candidates": sorted(set(re.findall(r"[()\[\]{}<>\u00ab\u00bb\u2039\u203a]", text or ""))),
    }


def combining_marks(text):
    return [ch for ch in (text or "") if unicodedata.category(ch).startswith("M")]


def unmapped_chars(text, allowed_codepoints=None):
    """Characters that commonly render as tofu without an explicit font."""
    allowed = set(allowed_codepoints or [])
    bad = []
    for ch in set(text or ""):
        if ch in "\n\r\t":
            continue
        if ord(ch) in allowed:
            continue
        if unicodedata.category(ch) in ("Cn", "Co", "Cs"):
            bad.append({"char": ch, "codepoint": "U+%04X" % ord(ch), "category": unicodedata.category(ch)})
    return bad


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #
def similarity(a, b):
    if not a and not b:
        return 1.0
    return round(SequenceMatcher(None, (a or "").lower(), (b or "").lower(), autojunk=False).ratio(), 4)


def normalize_for_match(text, keep_case=False):
    t = norm_ws(text)
    if not keep_case:
        t = t.lower()
    t = re.sub(r"\{\d+\}|\{[A-Za-z0-9_]+\}|%\d*\$?[sd]|</?\w+[^>]*>", "\u0001", t)
    t = re.sub(r"[\s\.,;:!\?\u2014\u2013\"'\(\)\[\]]+", " ", t)
    return t.strip()


def placeholder_set(text):
    found = set()
    found.update(re.findall(r"%\d*\$?[sdif]|%[sdif]", text or ""))
    found.update(re.findall(r"\{[A-Za-z0-9_.]+\}", text or ""))
    found.update(re.findall(r"\{\d+\}", text or ""))
    found.update(re.findall(r"</?[A-Za-z][^>]*>", text or ""))
    return found


# --------------------------------------------------------------------------- #
# ICU MessageFormat parsing
# --------------------------------------------------------------------------- #
ICU_TYPES = {"plural", "select", "selectordinal", "number", "date", "time", "duration", "spellout",
             "ordinal", "choice"}


class ICUError(Exception):
    pass


def parse_icu(message):
    """Parse an ICU MessageFormat string into an argument tree.

    Raises ICUError for malformed syntax. Missing 'other' branches are *not*
    syntax errors; they are reported by the validation layer so callers can
    route them to a specific rule id.
    """
    message = message or ""

    def skip_quoted(text, i):
        i += 1
        while i < len(text):
            if text[i] == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return i

    def parse_block(text, i, top_level=True):
        args = []
        while i < len(text):
            ch = text[i]
            if ch == "'":
                i = skip_quoted(text, i)
                continue
            if ch == "}":
                if top_level:
                    raise ICUError("Unmatched closing brace at offset %d" % i)
                return args, i
            if ch == "{":
                arg, i = parse_arg(text, i)
                args.append(arg)
                continue
            i += 1
        if not top_level:
            raise ICUError("Unclosed argument block at offset %d" % i)
        return args, i

    def split_top(inner):
        parts, depth, buf, k = [], 0, "", 0
        while k < len(inner):
            ch = inner[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if ch == "," and depth == 0 and len(parts) < 2:
                parts.append(buf)
                buf = ""
                k += 1
                continue
            buf += ch
            k += 1
        parts.append(buf)
        return parts

    def parse_arg(text, i):
        start, depth, j = i, 0, i
        while j < len(text):
            if text[j] == "'":
                j = skip_quoted(text, j)
                continue
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise ICUError("Unclosed argument starting at offset %d" % start)
        inner = text[i + 1:j]
        parts = split_top(inner)
        name = parts[0].strip()
        if not name:
            raise ICUError("Empty argument name at offset %d" % start)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", name):
            raise ICUError("Invalid argument name %r at offset %d" % (name, start))
        arg = {"name": name, "type": "simple", "style": None, "options": {}}
        if len(parts) > 1:
            arg["type"] = parts[1].strip()
            if arg["type"] not in ICU_TYPES:
                raise ICUError("Unknown argument type %r for %r" % (arg["type"], name))
            if arg["type"] in ("plural", "select", "selectordinal", "choice"):
                body = inner.split(",", 2)[2] if inner.count(",") >= 2 else ""
                if not body.strip():
                    raise ICUError("Missing options for %s argument %r" % (arg["type"], name))
                opts = {}
                k = 0
                while k < len(body):
                    if body[k].isspace() or body[k] == ",":
                        k += 1
                        continue
                    m = re.match(r"(=\d+|[A-Za-z0-9_\-]+)\s*\{", body[k:])
                    if not m:
                        raise ICUError("Malformed option in %s argument %r" % (arg["type"], name))
                    key = m.group(1)
                    brace_start = k + m.end() - 1
                    sub, close = parse_block(body, brace_start + 1, top_level=False)
                    opts[key] = sub
                    k = close + 1
                arg["options"] = opts
            else:
                style = ",".join(parts[2:]).strip()
                arg["style"] = style or None
        return arg, j + 1

    args, _ = parse_block(message, 0)
    return args


def icu_argument_names(message):
    names = set()

    def walk(args):
        for a in args:
            names.add(a["name"])
            for sub in (a.get("options") or {}).values():
                walk(sub)

    try:
        walk(parse_icu(message))
    except ICUError:
        names.update(re.findall(r"\{\s*([A-Za-z_][A-Za-z0-9_.]*)", message or ""))
    return names


# --------------------------------------------------------------------------- #
# Term matching
# --------------------------------------------------------------------------- #
def find_terms(text, terms, case_sensitive=False):
    """Return matches of a term list. Longest terms win to avoid double flags."""
    hay = text if case_sensitive else (text or "").lower()
    out = []
    for term in sorted(terms, key=len, reverse=True):
        needle = term if case_sensitive else term.lower()
        if not needle:
            continue
        for m in re.finditer(re.escape(needle), hay):
            s, e = m.start(), m.end()
            if not case_sensitive:
                if s > 0 and hay[s - 1].isalnum():
                    continue
                if e < len(hay) and hay[e].isalnum():
                    continue
            if any(not (e <= o["start"] or s >= o["end"]) for o in out):
                continue
            out.append({"term": term, "start": s, "end": e, "surface": (text or "")[s:e]})
    return sorted(out, key=lambda x: x["start"])


def taglish_markers(text, markers):
    return sorted({m for m in find_terms(text or "", markers)} and
                  {x["surface"].lower() for x in find_terms(text or "", markers)})


def levenshtein(a, b, cap=2000):
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def token_similarity(src, tgt, target_lang="en"):
    """Content-overlap heuristic used by translation QA and fuzzy matching."""
    src_t = [w.lower() for w in words(src)]
    tgt_t = [w.lower() for w in words(tgt)]
    if not src_t or not tgt_t:
        return 0.0
    common = len(set(src_t) & set(tgt_t))
    return round(common / float(max(len(set(src_t)), 1)), 4)


# --------------------------------------------------------------------------- #
# Number / date formatting checks
# --------------------------------------------------------------------------- #
NUM_PATTERNS = {
    "grouping_comma": re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"),
    "grouping_space": re.compile(r"\d{1,3}(?:[\u00a0\u202f ]\d{3})+(?:,\d+)?"),
    "grouping_dot": re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?"),
    "decimal_comma": re.compile(r"\d+,\d{1,2}\b"),
    "currency_symbol": re.compile(r"[\u20b1$\u20ac\u00a3\u00a5]\s?\d"),
    "currency_code": re.compile(r"\b(?:PHP|USD|EUR|GBP|JPY)\b"),
    "percent_glued": re.compile(r"\d%"),
    "percent_spaced": re.compile(r"\d\s%"),
    "iso_date": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "slash_date": re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    "month_name_date": re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s*\d{2,4}\b"),
    "dmy_order": re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{2,4}\b"),
    "time_12h": re.compile(r"\b\d{1,2}:\d{2}\s?(?:[ap]\.?m\.?)?\b", re.I),
    "time_24h": re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b"),
    "phone_ph": re.compile(r"(?:\+63[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{4}|\b09\d{2}[\s-]?\d{3}[\s-]?\d{4}\b)"),
}


def format_signals(text):
    return {name: len(rx.findall(text or "")) for name, rx in NUM_PATTERNS.items()}


def looks_like_sentence(text):
    return bool(re.search(r"[.!?\u3002\uff01\uff1f]\s*$", norm_ws(text) or ""))
