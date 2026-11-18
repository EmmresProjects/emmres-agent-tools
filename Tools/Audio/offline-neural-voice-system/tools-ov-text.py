#!/usr/bin/env python3
"""Text processing for the Offline Neural Voice Skill Tool.

Normalization, verbalization of numbers/dates/currency/units/URLs/symbols,
acronym expansion, pronunciation-dictionary application, sentence
segmentation, length-bounded chunking, word-error rate, phrase matching.

Pure standard library. No network. No code execution of input text.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
PRONUNCIATION_PATH = SKILL_ROOT / "asset-pronunciation-dictionary.json"

# ---------- small number-to-words ----------

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(10 ** 9, "billion"), (10 ** 6, "million"), (10 ** 3, "thousand")]
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
             7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh",
             12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
             16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth",
             20: "twentieth", 30: "thirtieth", 40: "fortieth", 50: "fiftieth",
             60: "sixtieth", 70: "seventieth", 80: "eightieth", 90: "ninetieth",
             100: "hundredth", 1000: "thousandth", 1000000: "millionth"}


def int_to_words(n: int) -> str:
    n = int(n)
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, r = divmod(n, 10)
        return _TENS[t] + (("-" + _ONES[r]) if r else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + ((" " + int_to_words(r)) if r else "")
    for scale_val, scale_name in _SCALES:
        if n >= scale_val:
            q, r = divmod(n, scale_val)
            return int_to_words(q) + " " + scale_name + ((" " + int_to_words(r)) if r else "")
    return str(n)


def int_to_ordinal(n: int) -> str:
    n = int(n)
    if n in _ORDINALS:
        return _ORDINALS[n]
    if n < 100:
        t, r = divmod(n, 10)
        tens_ord = _ORDINALS.get(t * 10)
        if r == 0:
            return tens_ord or (int_to_words(n) + "th")
        return _TENS[t] + "-" + _ORDINALS[r]
    if n < 1000:
        h, r = divmod(n, 100)
        if r == 0:
            return int_to_words(h) + " hundredth"
        return int_to_words(h) + " hundred " + int_to_ordinal(r)
    for scale_val, scale_name in _SCALES:
        if n == scale_val:
            return int_to_words(n // scale_val) + " " + _ORDINALS.get(scale_val, scale_name + "th")
        if n > scale_val:
            q, r = divmod(n, scale_val)
            if r == 0:
                return int_to_words(q) + " " + scale_name + "th"
            return int_to_words(q) + " " + scale_name + " " + int_to_ordinal(r)
    return int_to_words(n) + "th"


def decimal_to_words(text: str) -> str:
    whole, frac = text.split(".", 1)
    frac_words = " ".join(_ONES[int(d)] if d.isdigit() else d for d in frac)
    return f"{int_to_words(int(whole))} point {frac_words}"


def _year_to_words(y: int) -> str:
    if 2000 <= y <= 2009:
        return "two thousand" + ((" " + _ONES[y - 2000]) if y > 2000 else "")
    if 2010 <= y <= 2099 or 1100 <= y <= 1999:
        hi, lo = divmod(y, 100)
        if lo == 0:
            return int_to_words(hi) + " hundred"
        if lo < 10:
            return int_to_words(hi) + " oh " + _ONES[lo]
        return int_to_words(hi) + " " + int_to_words(lo)
    return int_to_words(y)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

# ---------- unicode / whitespace cleanup ----------

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]
_SMART_QUOTES = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                 "\u2013": "-", "\u2014": "--", "\u2026": "..."}


def cleanup_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for z in _ZERO_WIDTH:
        text = text.replace(z, "")
    for k, v in _SMART_QUOTES.items():
        text = text.replace(k, v)
    text = _CONTROL_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


# ---------- verbalizers ----------

_CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"),
             "€": ("euro", "euros", "cent", "cents"),
             "£": ("pound", "pounds", "penny", "pence"),
             "¥": ("yen", "yen", "", "")}

_MONEY_RE = re.compile(r"([$€£¥])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_ORDINAL_RE = re.compile(r"\b(\d{1,6})(st|nd|rd|th)\b", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"(?<![\d.])\d+\.\d+(?!\.?\d)")
_INT_RE = re.compile(r"\b\d[\d,]*\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?:\s?([AaPp])\.?[Mm]\.?)?\b")
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')]+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

_UNIT_MAP = {"kg": "kilograms", "g": "grams", "mg": "milligrams", "cm": "centimeters",
             "mm": "millimeters", "m": "meters", "km": "kilometers", "mi": "miles",
             "ft": "feet", "in": "inches", "lb": "pounds", "oz": "ounces",
             "l": "liters", "ml": "milliliters", "gb": "gigabytes", "mb": "megabytes",
             "tb": "terabytes", "hz": "hertz", "khz": "kilohertz", "db": "decibels",
             "mph": "miles per hour", "kph": "kilometers per hour", "w": "watts",
             "kw": "kilowatts", "v": "volts", "a": "amperes"}
_UNIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(kg|g|mg|cm|mm|km|mi|ft|in|lb|oz|ml|gb|mb|tb|hz|khz|db|mph|kph|kw|[lmsiwav])\b", re.IGNORECASE)

_SYMBOL_MAP = {"&": " and ", "@": " at ", "#": " number ", "%": " percent ",
               "*": " star ", "+": " plus ", "=": " equals ", "/": " slash ",
               "~": " tilde ", "°": " degrees "}

_EMOJI_MAP = {"❤️": "heart", "❤": "heart", "😀": "grinning face", "😂": "face with tears of joy",
              "🙂": "slightly smiling face", "😉": "winking face", "😍": "smiling face with heart eyes",
              "👍": "thumbs up", "👎": "thumbs down", "👏": "clapping hands", "🙌": "raising hands",
              "🔥": "fire", "🎉": "party popper", "✨": "sparkles", "🚀": "rocket",
              "💡": "light bulb", "✅": "check mark", "⭐": "star", "😊": "smiling face"}
_EMOJI_RE = re.compile("|".join(re.escape(k) for k in sorted(_EMOJI_MAP, key=len, reverse=True)))


def _money_sub(m):
    sym, whole, cents = m.group(1), m.group(2).replace(",", ""), m.group(3)
    singular, plural, sub_sing, sub_plural = _CURRENCY[sym]
    words = int_to_words(int(whole)) + " " + (singular if int(whole) == 1 else plural)
    if cents:
        c = int(cents.ljust(2, "0")[:2])
        if sub_sing:
            words += " and " + int_to_words(c) + " " + (sub_sing if c == 1 else sub_plural)
    return words


def _units_sub(m):
    num, unit = m.group(1), m.group(2).lower()
    unit_word = _UNIT_MAP.get(unit)
    if not unit_word:
        return m.group(0)
    number = decimal_to_words(num) if "." in num else int_to_words(int(num))
    return f"{number} {unit_word}"


def _url_to_words(url: str) -> str:
    url = re.sub(r"^https?://", "", url)
    url = url.replace("www.", "")
    url = url.rstrip(".,;:!?)\"'")
    out = []
    for ch in url:
        if ch == ".":
            out.append(" dot ")
        elif ch == "/":
            out.append(" slash ")
        elif ch == "-":
            out.append(" dash ")
        elif ch == "_":
            out.append(" underscore ")
        elif ch == ":":
            out.append(" colon ")
        elif ch == "?":
            out.append(" question mark ")
        elif ch == "=":
            out.append(" equals ")
        elif ch == "&":
            out.append(" and ")
        elif ch == "%":
            out.append(" percent ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _email_to_words(addr: str) -> str:
    local, _, domain = addr.partition("@")
    def speak_part(part):
        return re.sub(r"[._-]", lambda m: {".": " dot ", "_": " underscore ", "-": " dash "}[m.group(0)], part)
    return f"{speak_part(local)} at {_url_to_words(domain)}"


def verbalize_numbers(text: str) -> str:
    text = _MONEY_RE.sub(_money_sub, text)
    text = _PERCENT_RE.sub(lambda m: (decimal_to_words(m.group(1)) if "." in m.group(1) else int_to_words(int(m.group(1)))) + " percent", text)
    text = _ISO_DATE_RE.sub(lambda m: _format_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), text)
    text = _US_DATE_RE.sub(lambda m: _format_date(int(m.group(3)), int(m.group(1)), int(m.group(2))), text)
    text = _TIME_RE.sub(_time_sub, text)
    text = _ORDINAL_RE.sub(lambda m: int_to_ordinal(int(m.group(1))), text)
    text = _DECIMAL_RE.sub(lambda m: decimal_to_words(m.group(0)), text)
    text = _INT_RE.sub(lambda m: int_to_words(int(m.group(0).replace(",", ""))), text)
    return text


def _format_date(y: int, mo: int, d: int) -> str:
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return f"{y}-{mo:02d}-{d:02d}"
    return f"{_MONTHS[mo - 1]} {int_to_ordinal(d)}, {_year_to_words(y)}"


def _time_sub(m):
    hour, minute = int(m.group(1)), int(m.group(2))
    suffix = m.group(3)
    if suffix:
        h12 = hour % 12 or 12
        ampm = "A M" if suffix.lower() == "a" else "P M"
        spoken = int_to_words(h12)
        spoken += " oh " + _ONES[minute] if minute < 10 else " " + int_to_words(minute)
        return f"{spoken} {ampm}"
    spoken = int_to_words(hour)
    spoken += " oh " + _ONES[minute] if minute < 10 else " " + int_to_words(minute)
    return spoken + " hundred hours" if False else spoken


def verbalize_urls_emails(text: str) -> str:
    text = _EMAIL_RE.sub(lambda m: _email_to_words(m.group(0)), text)
    text = _URL_RE.sub(lambda m: _url_to_words(m.group(0)), text)
    return text


def verbalize_units(text: str) -> str:
    return _UNIT_RE.sub(_units_sub, text)


def verbalize_symbols(text: str) -> str:
    for sym, words in _SYMBOL_MAP.items():
        text = text.replace(sym, words)
    return _WS_RE.sub(" ", text)


def verbalize_emoji(text: str) -> tuple[str, int]:
    count = 0
    def repl(m):
        nonlocal count
        count += 1
        return " " + _EMOJI_MAP[m.group(0)] + " "
    return _EMOJI_RE.sub(repl, text), count


def load_pronunciation(path: Path = PRONUNCIATION_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    return data


def expand_acronyms(text: str, dictionary: dict | None = None) -> str:
    d = (dictionary or load_pronunciation()).get("acronyms", {})
    if not d:
        return text
    def repl(m):
        token = m.group(0)
        return d.get(token, d.get(token.upper(), token))
    return re.sub(r"\b[A-Za-z][A-Za-z0-9.-]{1,9}\b", lambda m: d.get(m.group(0).upper(), m.group(0)), text)


def apply_pronunciation_dictionary(text: str, dictionary: dict | None = None) -> str:
    d = (dictionary or load_pronunciation()).get("words", {})
    for word, spoken in sorted(d.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"\b" + re.escape(word) + r"\b", lambda m: spoken, text, flags=re.IGNORECASE)
    return text


def normalize_text(text: str, dictionary: dict | None = None,
                   verbalize_symbols_flag: bool = True,
                   expand_acronyms_flag: bool = True,
                   apply_dictionary_flag: bool = True) -> dict:
    warnings = []
    original = text
    text = cleanup_text(text)
    text = verbalize_urls_emails(text)
    emoji_free, emoji_count = verbalize_emoji(text)
    if emoji_count:
        warnings.append(f"verbalized {emoji_count} emoji")
    text = emoji_free
    if verbalize_symbols_flag:
        text = verbalize_symbols(text)
    text = verbalize_units(text)
    text = verbalize_numbers(text)
    if expand_acronyms_flag:
        text = expand_acronyms(text, dictionary)
    if apply_dictionary_flag:
        text = apply_pronunciation_dictionary(text, dictionary)
    text = _WS_RE.sub(" ", text).strip()
    return {"original": original, "normalized": text, "warnings": warnings,
            "changed": text != original}


# ---------- sentence segmentation & chunking ----------

_ABBREVS = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "St.", "vs.", "etc.",
            "e.g.", "i.e.", "a.m.", "p.m.", "U.S.", "No.", "Fig.", "Inc.", "Ltd."]


def segment_sentences(text: str) -> list[str]:
    text = cleanup_text(text)
    if not text:
        return []
    protected = text
    placeholders = {}
    for i, abbr in enumerate(_ABBREVS):
        ph = f"\x00ABBR{i}\x00"
        if abbr.lower() in protected.lower():
            pattern = re.compile(re.escape(abbr), re.IGNORECASE)

            def _repl(m, _ph=ph):
                placeholders[_ph] = m.group(0)
                return _ph

            protected = pattern.sub(_repl, protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])", protected)
    sentences = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        for ph, original in placeholders.items():
            s = s.replace(ph, original)
        sentences.append(s)
    return sentences or [text]


def chunk_text(text: str, max_chars: int = 600) -> list[dict]:
    if max_chars < 50:
        raise ValueError("max_chars must be >= 50")
    sentences = segment_sentences(text)
    chunks, current = [], ""
    for s in sentences:
        if len(s) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            # hard split long sentence at clause boundaries, then spaces
            pieces = re.split(r"(?<=[,;:—-])\s+", s)
            buf = ""
            for piece in pieces:
                if len(piece) > max_chars:
                    if buf:
                        chunks.append(buf)
                        buf = ""
                    for i in range(0, len(piece), max_chars):
                        chunks.append(piece[i:i + max_chars])
                    continue
                candidate = (buf + " " + piece).strip()
                if buf and len(candidate) > max_chars:
                    chunks.append(buf)
                    buf = piece
                else:
                    buf = candidate
            if buf:
                chunks.append(buf)
            continue
        candidate = (current + " " + s).strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = s
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [{"index": i, "text": c, "chars": len(c)} for i, c in enumerate(chunks)]


# ---------- WER & phrase matching ----------

def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", cleanup_text(text).lower())


def word_error_rate(reference: str, hypothesis: str) -> dict:
    ref, hyp = _words(reference), _words(hypothesis)
    n, m = len(ref), len(hyp)
    dp = list(range(m + 1))
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            if ref[i - 1] == hyp[j - 1]:
                dp[j] = prev
                back[i][j] = "match"
            else:
                sub, delete, insert = prev + 1, dp[j] + 1, dp[j - 1] + 1
                best = min(sub, delete, insert)
                dp[j] = best
                back[i][j] = "sub" if best == sub else ("del" if best == delete else "ins")
            prev = cur
    # backtrace for counts
    i, j = n, m
    subs = dels = ins = 0
    while i > 0 or j > 0:
        op = back[i][j] if i > 0 and j > 0 else ("del" if i > 0 else "ins")
        if op == "match":
            i, j = i - 1, j - 1
        elif op == "sub":
            subs += 1
            i, j = i - 1, j - 1
        elif op == "del":
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    errors = subs + dels + ins
    wer = errors / n if n else (0.0 if m == 0 else 1.0)
    return {"reference_words": n, "hypothesis_words": m, "substitutions": subs,
            "deletions": dels, "insertions": ins, "errors": errors,
            "wer": round(wer, 4), "wer_percent": round(wer * 100, 1)}


def phrase_match(phrase: str, transcript: str) -> dict:
    expected = _words(phrase)
    heard = _words(transcript)
    heard_set_counts = {}
    for w in heard:
        heard_set_counts[w] = heard_set_counts.get(w, 0) + 1
    missing = []
    remaining = dict(heard_set_counts)
    for w in expected:
        if remaining.get(w, 0) > 0:
            remaining[w] -= 1
        else:
            missing.append(w)
    coverage = (len(expected) - len(missing)) / len(expected) if expected else 1.0
    return {"expected_words": expected, "missing_words": missing,
            "coverage": round(coverage, 4), "complete": not missing,
            "heard_words": len(heard)}
