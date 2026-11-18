#!/usr/bin/env python3
"""tools-nv-text.py - deterministic text preparation for offline neural voice.

Loaded by the entry point; safe to import. Self test:

    python tools-nv-text.py selftest

Capabilities
------------
normalize      expand numbers, currency, dates, times, units, symbols and
               abbreviations into speakable words (locale aware: en-US, en-GB)
clean          strip markup, HTML tags, emoji, zero-width characters, keep
               intended punctuation, collapse whitespace
lexicon        apply a versioned pronunciation lexicon with [[ipa]] overrides
direct         parse inline delivery directions (*emph*, {pause:250}, {rate:0.9})
chunk          split into sentence chunks under a character budget
analyze        report what a text will sound like before synthesis (counts,
               rewrites, unsupported scripts, aggressive abbreviations)
prepare        run the whole preparation pipeline and emit a JSON plan

Every rewrite is reported individually so a reviewer can see exactly which
characters changed; nothing is silently altered.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if "nvcore" not in sys.modules:
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("nvcore", _HERE / "tools-nvcore.py")
        _module = _ilu.module_from_spec(_spec)
        sys.modules["nvcore"] = _module
        _spec.loader.exec_module(_module)
    except FileNotFoundError:
        pass

import nvcore
from nvcore import (InputError, count_words, normalize_language_tag, script_of_text,
                    split_sentences)

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]

MONTHS_US = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
                 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
                 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
                 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
                 19: "nineteenth", 20: "twentieth", 30: "thirtieth"}

CURRENCY = {"$": ("dollar", "cent"), "€": ("euro", "cent"), "£": ("pound", "penny"),
            "¥": ("yen", "sen"), "₱": ("peso", "centavo"), "₹": ("rupee", "paisa")}

UNIT_WORDS = {
    "km": "kilometres", "kg": "kilograms", "cm": "centimetres", "mm": "millimetres",
    "ml": "millilitres", "GB": "gigabytes", "MB": "megabytes", "TB": "terabytes",
    "KB": "kilobytes", "Hz": "hertz", "kHz": "kilohertz", "MHz": "megahertz",
    "GHz": "gigahertz", "mph": "miles per hour", "ft": "feet", "in": "inches",
    "lb": "pounds", "oz": "ounces", "°C": "degrees Celsius", "°F": "degrees Fahrenheit",
    "ms": "milliseconds", "s": "seconds", "min": "minutes", "hr": "hours",
}

ABBREVIATIONS_DEFAULT = {
    "Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz", "Dr.": "Doctor", "Prof.": "Professor",
    "St.": "Saint", "Jr.": "Junior", "Sr.": "Senior", "vs.": "versus", "etc.": "et cetera",
    "e.g.": "for example", "i.e.": "that is", "approx.": "approximately", "dept.": "department",
    "Inc.": "Incorporated", "Ltd.": "Limited", "Co.": "Company", "No.": "number",
    "Fig.": "figure", "Ave.": "Avenue", "Blvd.": "Boulevard", "Rd.": "Road",
}

SYMBOL_WORDS = {"&": "and", "%": "percent", "+": "plus", "=": "equals", "@": "at",
                "#": "number", "~": "approximately", "×": "times", "÷": "divided by",
                "§": "section", "°": "degrees", "→": "to", "±": "plus or minus"}

ROMAN_MAP = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]

_HTML_TAG = re.compile(r"<[^>]{1,400}>")
_HTML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
                  "&nbsp;": " ", "&mdash;": " - ", "&ndash;": " - ", "&hellip;": "...",
                  "&rsquo;": "'", "&lsquo;": "'", "&ldquo;": '"', "&rdquo;": '"'}
_DIRECTION_PAUSE = re.compile(r"\{\s*pause\s*:\s*(\d{1,4})\s*\}", re.IGNORECASE)
_DIRECTION_RATE = re.compile(r"\{\s*rate\s*:\s*([0-9]*\.?[0-9]+)\s*\}", re.IGNORECASE)
_DIRECTION_EMPHASIS = re.compile(r"\*\*([^*\n]{1,120})\*\*|\*([^*\n]{1,120})\*")
_MARKUP_RESIDUE = re.compile(r"(^#{1,6}\s+)", re.MULTILINE)
_BULLET_RESIDUE = re.compile(r"^\s*(?:[-*•‣◦]|\d{1,2}[.)])\s+", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Number spelling
# --------------------------------------------------------------------------- #

def number_to_words(value, *, locale="en-US", and_comma=True):
    """Spell a non-negative integer (or float) as English words."""
    try:
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
                raise ValueError(cleaned)
            number = float(cleaned) if "." in cleaned else int(cleaned)
        else:
            number = value
    except (TypeError, ValueError):
        raise InputError("not a number: %r" % (value,))
    if isinstance(number, float) and math_isnan(number):
        return "not a number"
    if number < 0:
        return "minus " + number_to_words(-number, locale=locale, and_comma=and_comma)
    if isinstance(number, float):
        if abs(number - round(number)) < 1e-9:
            number = int(round(number))
        else:
            whole = int(number)
            fraction = _decimal_digits(number)
            return "%s point %s" % (number_to_words(whole, locale=locale),
                                    " ".join(ONES[int(d)] if int(d) < 10 else str(d) for d in fraction))
    number = int(number)
    if number < 20:
        return ONES[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return TENS[tens] + ("-" + ONES[rest] if rest else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        tail = " " + number_to_words(rest, locale=locale) if rest else ""
        joiner = " and" if and_comma and locale == "en-GB" and rest else ""
        return ONES[hundreds] + " hundred" + joiner + tail
    for scale_value, scale_name in SCALES:
        if number >= scale_value:
            count, rest = divmod(number, scale_value)
            head = number_to_words(count, locale=locale, and_comma=and_comma) + " " + scale_name
            if rest:
                joiner = " and " if and_comma and locale == "en-GB" and rest < 100 else " "
                return head + joiner + number_to_words(rest, locale=locale, and_comma=and_comma)
            return head
    return str(number)


def math_isnan(value):
    return value != value


def _decimal_digits(number):
    text = ("%.6f" % number).rstrip("0").rstrip(".")
    return text.split(".")[1]


def ordinal_to_words(value):
    value = int(value)
    if value in ORDINAL_WORDS:
        return ORDINAL_WORDS[value]
    if value < 100:
        tens, rest = divmod(value, 10)
        if rest:
            return TENS[tens] + "-" + ORDINAL_WORDS[rest]
        return TENS[tens].rstrip("y") + "ieth"
    return number_to_words(value) + "th"


def digits_to_words(text):
    """Speak a digit string one digit at a time (years, codes, phone numbers)."""
    return " ".join(ONES[int(d)] if d.isdigit() else d for d in str(text))


def money_to_words(amount, currency_symbol="$", *, locale="en-US"):
    major, minor = CURRENCY.get(currency_symbol, ("unit", "cent"))
    plural = major + "s"
    pennies = minor + "s"
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    words = "%s %s" % (number_to_words(whole, locale=locale), major if whole == 1 else plural)
    if cents:
        words += " and %s %s" % (number_to_words(cents, locale=locale),
                                 minor if cents == 1 else pennies)
    return words


def year_to_words(value):
    """Read years the natural way: 1984 -> nineteen eighty-four, 2005 -> two thousand five."""
    year = int(value)
    if year < 1100 or year > 2099:
        return number_to_words(year)
    if year % 100 == 0:
        return number_to_words(year)
    head, tail = divmod(year, 100)
    if tail < 10:
        return "%s oh %s" % (number_to_words(head), ONES[tail])
    return "%s %s" % (number_to_words(head), number_to_words(tail))


def roman_to_int(text):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for char in reversed(text.upper()):
        if char not in values:
            return None
        value = values[char]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


# --------------------------------------------------------------------------- #
# Normalisation rules
# --------------------------------------------------------------------------- #

class PreparationResult:
    def __init__(self, text, rewrites, warnings):
        self.text = text
        self.rewrites = rewrites
        self.warnings = warnings

    def as_dict(self):
        return {"text": self.text, "rewrites": self.rewrites, "warnings": self.warnings,
                "chars": len(self.text), "words": count_words(self.text)}


class TextPreparer:
    """Ordered, inspectable text preparation rules."""

    def __init__(self, *, locale="en-US", expand_numbers=True, expand_currency=True,
                 expand_dates=True, expand_units=True, expand_abbreviations=True,
                 expand_symbols=True, keep_punctuation=True, allow_emoji=False,
                 max_chars=None):
        self.locale = normalize_language_tag(locale) or "en-US"
        # option flags use an `opt_` prefix so they never shadow the rule methods
        self.opt_numbers = bool(expand_numbers)
        self.opt_currency = bool(expand_currency)
        self.opt_dates = bool(expand_dates)
        self.opt_units = bool(expand_units)
        self.opt_abbreviations = bool(expand_abbreviations)
        self.opt_symbols = bool(expand_symbols)
        self.opt_punctuation = bool(keep_punctuation)
        self.allow_emoji = allow_emoji
        self.max_chars = max_chars
        self.rewrites = []
        self.warnings = []

    # -- helpers ---------------------------------------------------------- #
    def _note(self, rule, before, after, index=None):
        self.rewrites.append({"rule": rule, "before": before, "after": after, "index": index})

    def _sub(self, pattern, replacer, text, rule, *, flags=0):
        """Apply a substitution and record each real change exactly once."""
        def swap(match):
            replacement = replacer(match)
            if replacement is None:
                return match.group(0)
            if replacement != match.group(0):
                self._note(rule, match.group(0), replacement, match.start())
            return replacement
        return re.sub(pattern, swap, text, flags=flags)

    # -- stages ----------------------------------------------------------- #
    def strip_markup(self, text):
        if not isinstance(text, str):
            raise InputError("text must be a string")
        original = text
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if "<" in text and ">" in text:
            text = self._sub(_HTML_TAG, lambda m: "", text, "strip-html-tags")
            for entity, value in _HTML_ENTITIES.items():
                if entity in text:
                    self._note("decode-entity", entity, value)
                    text = text.replace(entity, value)
        text = self._sub(_MARKUP_RESIDUE, lambda m: "", text, "strip-markdown-heading")
        text = self._sub(_BULLET_RESIDUE, lambda m: "", text, "strip-list-markers")
        text = text.replace("`", "").replace("**", "").replace("__", " ")
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # markdown links
        text = re.sub(r"https?://\S+", "", text)               # URLs are not spoken
        text = re.sub(r"\S+@\S+\.\S+", "", text)               # email addresses
        if original != text:
            self.warnings.append("markup, links and addresses were removed before synthesis")
        return text

    def strip_invisible(self, text):
        removed = 0
        cleaned = []
        for char in text:
            code = ord(char)
            category = unicodedata.category(char)
            if code in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060):
                removed += 1
                continue
            if category == "Cc" and char not in "\n\t":
                removed += 1
                continue
            if category == "Co" or category == "Cs":
                removed += 1
                continue
            if _is_emoji(char) and not self.allow_emoji:
                removed += 1
                continue
            cleaned.append(char)
        if removed:
            self._note("strip-invisible", "%d invisible or emoji characters" % removed, "")
        return "".join(cleaned)

    def expand_currencies(self, text):
        if not self.opt_currency:
            return text
        symbol_class = "(?:%s)" % "|".join(re.escape(sym) for sym in CURRENCY)
        pattern = r"(%s)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?" % symbol_class
        def replacer(match):
            symbol = match.group(1)
            whole = int(match.group(2).replace(",", ""))
            cents = match.group(3)
            amount = whole + (int(cents) / 100.0 if cents else 0.0)
            words = money_to_words(amount, symbol, locale=self.locale)
            if cents is None and whole >= 1:
                words = words.replace(" dollars", " dollars")  # no-op anchor for clarity
            return words
        return self._sub(pattern, replacer, text, "expand-currency")

    def expand_dates(self, text):
        if not self.opt_dates:
            return text
        def iso(match):
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            return "%s %s %s" % (MONTHS_US[month - 1], ordinal_to_words(day), year_to_words(year))
        text = self._sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", iso, text, "expand-iso-date")

        def us(match):
            month, day, year = int(match.group(1)), int(match.group(2)), match.group(3)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            year_words = year_to_words(int(year)) if year else ""
            return ("%s %s %s" % (MONTHS_US[month - 1], ordinal_to_words(day), year_words)).strip()
        text = self._sub(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", us, text, "expand-numeric-date")

        def month_name(match):
            month = MONTHS_US.index(match.group(1).capitalize()) + 1
            day = int(match.group(2))
            year = match.group(3)
            parts = ["%s %s" % (MONTHS_US[month - 1], ordinal_to_words(day))]
            if year:
                parts.append(year_to_words(int(year)))
            return " ".join(parts)
        month_pattern = r"\b(%s)\s+(\d{1,2})(?:,?\s+(\d{4}))?\b" % "|".join(MONTHS_US)
        text = self._sub(month_pattern, month_name, text, "expand-month-date")
        return text

    def expand_times(self, text):
        def clock_12h(match):
            hour = int(match.group(1))
            minute = match.group(2)
            suffix = match.group(3).lower()
            if not 1 <= hour <= 12 or int(minute) > 59:
                return None
            spoken = number_to_words(hour)
            if minute and minute != "00":
                spoken += " " + number_to_words(int(minute))
            else:
                spoken += " o'clock"
            return "%s %s" % (spoken, "A M" if suffix == "am" else "P M")

        def clock_24h(match):
            hour = int(match.group(1))
            minute = match.group(2)
            if hour > 23 or int(minute) > 59:
                return None
            spoken = number_to_words(hour)
            if minute and minute != "00":
                spoken += " " + number_to_words(int(minute))
            else:
                spoken += " o'clock"
            return spoken

        text = self._sub(r"\b(\d{1,2}):(\d{2})\s?(am|pm|AM|PM)\b", clock_12h, text,
                         "expand-time-12h")
        text = self._sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", clock_24h, text,
                         "expand-time-24h")
        return text

    def expand_units(self, text):
        if not self.opt_units:
            return text
        for unit in sorted(UNIT_WORDS, key=len, reverse=True):
            pattern = r"(\d+(?:\.\d+)?)\s?%s\b" % re.escape(unit)
            def replacer(match, unit=unit):
                value = match.group(1)
                words = number_to_words(value) if "." in value else number_to_words(int(value))
                return "%s %s" % (words, UNIT_WORDS[unit])
            text = self._sub(pattern, replacer, text, "expand-unit")
        return text

    def expand_numbers_rule(self, text):
        if not self.opt_numbers:
            return text

        def ordinal(match):
            return ordinal_to_words(int(match.group(1)))
        text = self._sub(r"\b(\d{1,3})(?:st|nd|rd|th)\b", ordinal, text, "expand-ordinal")

        def year(match):
            return year_to_words(int(match.group(1)))
        text = self._sub(r"\b(1[1-9]\d{2}|20[0-9]{2})\b", year, text, "expand-year")

        def decimal(match):
            return number_to_words(match.group(0))
        text = self._sub(r"(?<![\w.])\d+\.\d+(?![\w.])", decimal, text, "expand-decimal")

        def thousands(match):
            return number_to_words(match.group(0).replace(",", ""))
        text = self._sub(r"\b\d{1,3}(?:,\d{3})+\b", thousands, text, "expand-thousands")

        def percent(match):
            return "%s percent" % number_to_words(match.group(1))
        text = self._sub(r"\b(\d+(?:\.\d+)?)\s?%", percent, text, "expand-percent")

        def plain(match):
            return number_to_words(match.group(0))
        text = self._sub(r"(?<![\w.$/-])\d+(?![\w./-])", plain, text, "expand-integer")
        return text

    def expand_symbols_rule(self, text):
        if not self.opt_symbols:
            return text
        for symbol, word in sorted(SYMBOL_WORDS.items(), key=lambda kv: -len(kv[0])):
            if symbol in text:
                replacement = " %s " % word
                self._note("expand-symbol", symbol, replacement.strip())
                text = text.replace(symbol, replacement)
        return text

    def expand_abbreviations_rule(self, text):
        if not self.opt_abbreviations:
            return text
        for abbr, expansion in ABBREVIATIONS_DEFAULT.items():
            pattern = r"(?<!\w)%s(?!\w)" % re.escape(abbr)
            text = self._sub(pattern, lambda m, exp=expansion: exp, text, "expand-abbreviation")
        # dotted acronyms such as U.S.A.
        text = self._sub(r"\b(?:[A-Z]\.){2,}",
                         lambda m: m.group(0).replace(".", "") and " ".join(m.group(0).replace(".", "")),
                         text, "expand-dotted-acronym")
        # acronyms read letter by letter (NASA, API) stay intact for espeak
        return text

    def apply_lexicon(self, text, lexicon, *, only=None):
        """Apply pronunciation overrides from the lexicon asset.

        Applies whole-word, case-insensitive matches, longest entries first, and
        records each substitution so the review sheet can show it. `only` limits
        the run to specific entry ids.
        """
        if not lexicon:
            return text
        entries = lexicon.get("entries", [])
        if only:
            wanted = set(only)
            entries = [e for e in entries if e.get("id") in wanted]
        ordered = sorted(entries, key=lambda e: -max((len(w) for w in e.get("words", [])), default=0))
        for entry in ordered:
            for word in entry.get("words", []):
                # keep trailing punctuation (pause carriers) inside the match
                pattern = r"(?<![\w-])%s(?![\w-])([,.!?;:]*)" % re.escape(word)
                def replacer(match, entry=entry, word=word):
                    punctuation = match.group(1) or ""
                    if entry.get("ipa"):
                        return "[[%s%s]]" % (entry["ipa"], punctuation)
                    if entry.get("say"):
                        # `_sub` records the rewrite; noting here would double count
                        return entry["say"] + punctuation
                    return None
                text = self._sub(pattern, replacer, text, "lexicon-%s" % entry.get("id", "entry"),
                                 flags=re.IGNORECASE)
        return text

    def apply_spelling_overrides(self, text, spelling):
        """Apply spelling variants (en-GB vs en-US) that change pronunciation."""
        if not spelling:
            return text
        for source, target in spelling.items():
            pattern = r"(?<![\w-])%s(?![\w-])" % re.escape(source)
            text = self._sub(pattern, lambda m, t=target: t, text, "spelling-variant",
                             flags=re.IGNORECASE)
        return text

    def cleanup(self, text):
        text = text.replace("\t", " ")
        text = re.sub(r"[ ]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\.{4,}", "...", text)
        text = re.sub(r"([!?.,;:])(?:\s*\1)+", r"\1", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = text.strip()
        if not self.opt_punctuation:
            text = re.sub(r"[^\w\s'\-]", "", text)
        if self.max_chars and len(text) > self.max_chars:
            raise InputError("prepared text exceeds the %d character limit (%d)"
                             % (self.max_chars, len(text)),
                             hint="shorten the text or raise execution.max_chars")
        return text

    def run(self, text, *, lexicon=None, spelling=None, lexicon_only=None):
        text = self.strip_markup(text)
        text = self.strip_invisible(text)
        text = self.expand_currencies(text)
        text = self.expand_dates(text)
        text = self.expand_times(text)
        text = self.expand_units(text)
        text = self.expand_abbreviations_rule(text)
        text = self.expand_numbers_rule(text)
        text = self.expand_symbols_rule(text)
        text = self.apply_lexicon(text, lexicon, only=lexicon_only)
        text = self.apply_spelling_overrides(text, spelling)
        text = self.cleanup(text)
        return PreparationResult(text, self.rewrites, self.warnings)


def _is_emoji(char):
    code = ord(char)
    return (0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or
            0x1F000 <= code <= 0x1F2FF or code in (0xFE0F, 0x2764, 0x2B50))


def parse_directions(text):
    """Extract inline delivery directions and return (clean_text, directions)."""
    directions = {"pauses": [], "rates": [], "emphasis": []}

    def pause(match):
        directions["pauses"].append({"at_char": match.start(), "ms": int(match.group(1))})
        return " "
    text = _DIRECTION_PAUSE.sub(pause, text)

    def rate(match):
        value = float(match.group(1))
        if not 0.5 <= value <= 2.0:
            raise InputError("inline rate %s is outside 0.5..2.0" % value)
        directions["rates"].append({"at_char": match.start(), "value": value})
        return " "
    text = _DIRECTION_RATE.sub(rate, text)

    def emphasis(match):
        phrase = match.group(1) or match.group(2)
        directions["emphasis"].append({"phrase": phrase, "at_char": match.start()})
        return phrase
    text = _DIRECTION_EMPHASIS.sub(emphasis, text)
    return text, directions


def chunk_text(text, *, max_chars=240):
    """Split prepared text into synthesis chunks at sentence boundaries."""
    if max_chars < 20:
        raise InputError("max_chars must be at least 20")
    sentences = split_sentences(text)
    chunks = []
    current = ""
    for sentence in sentences:
        candidate = (current + " " + sentence).strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(sentence) <= max_chars:
            current = sentence
        else:
            # hard split on word boundaries for over-long sentences
            words = sentence.split()
            buffer = ""
            for word in words:
                if len((buffer + " " + word).strip()) <= max_chars:
                    buffer = (buffer + " " + word).strip()
                else:
                    if buffer:
                        chunks.append(buffer)
                    buffer = word
            current = buffer
    if current.strip():
        chunks.append(current.strip())
    return chunks


#: Scripts a voice language can pronounce. English voices cover latin only; other
#: families are listed so a voice can be added later without touching the guard.
SCRIPT_SUPPORT = {
    "en": {"latin"},
    "de": {"latin"}, "fr": {"latin"}, "es": {"latin"}, "it": {"latin"},
    "pt": {"latin"}, "nl": {"latin"}, "sv": {"latin"}, "da": {"latin"},
    "no": {"latin"}, "fi": {"latin"}, "pl": {"latin"}, "cs": {"latin"},
    "ro": {"latin"}, "tr": {"latin"}, "vi": {"latin"}, "id": {"latin"},
    "ru": {"cyrillic"}, "uk": {"cyrillic"}, "bg": {"cyrillic"},
    "ar": {"arabic"}, "fa": {"arabic"}, "he": {"hebrew"},
    "hi": {"devanagari"}, "mr": {"devanagari"}, "ne": {"devanagari"},
    "el": {"greek"}, "th": {"thai"},
    "zh": {"han"}, "ja": {"han", "kana"}, "ko": {"hangul"},
}

_SCRIPT_LABELS = {
    "latin": "Latin", "cyrillic": "Cyrillic", "arabic": "Arabic", "han": "Han (Chinese)",
    "kana": "Kana (Japanese)", "hangul": "Hangul (Korean)", "thai": "Thai",
    "devanagari": "Devanagari", "hebrew": "Hebrew", "greek": "Greek",
}


def detect_unsupported_script(text, language):
    """Return script groups the voice language cannot pronounce.

    espeak-ng silently drops characters it has no rules for, so without this guard
    a Chinese or Cyrillic sentence would produce near-silence and still report a
    successful synthesis. Characters inside an inline [[ipa]] block are exempt
    because the caller supplied the phonemes explicitly.
    """
    family = (normalize_language_tag(language) or "en").split("-")[0]
    supported = SCRIPT_SUPPORT.get(family, {"latin"})
    stripped = re.sub(r"\[\[.*?\]\]", " ", str(text))
    groups = {}
    for char in stripped:
        if not char.isalpha():
            continue
        script = script_of_text(char)
        if script == "latin" or script in supported:
            continue
        groups.setdefault(script, []).append(char)
    findings = []
    for script, chars in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        findings.append({
            "script": script,
            "label": _SCRIPT_LABELS.get(script, script),
            "count": len(chars),
            "sample": "".join(chars[:8]),
        })
    return {"language": normalize_language_tag(language) or "en",
            "supported_scripts": sorted(supported),
            "unsupported": findings,
            "has_unsupported": bool(findings)}


def analyze_text(text, *, locale="en-US"):
    """Report features that affect synthesis before any audio is produced."""
    sentences = split_sentences(text)
    words = re.findall(r"[A-Za-z']+", text)
    long_words = [w for w in words if len(w) > 12]
    all_caps = re.findall(r"\b[A-Z]{2,}\b", text)
    numbers = re.findall(r"\d+", text)
    return {
        "chars": len(text),
        "words": len(words),
        "sentences": len(sentences),
        "avg_words_per_sentence": round(len(words) / len(sentences), 2) if sentences else 0,
        "longest_sentence_chars": max((len(s) for s in sentences), default=0),
        "long_words": sorted(set(long_words))[:20],
        "acronyms": sorted(set(all_caps))[:20],
        "digits_present": bool(numbers),
        "ipa_overrides": len(re.findall(r"\[\[.*?\]\]", text)),
        "script": script_of_text(text),
        "locale": normalize_language_tag(locale),
        "estimated_speech_seconds": round(len(words) / 2.6, 2) if words else 0.0,
    }


def prepare_text(text, *, cfg=None, lexicon=None, locale="en-US", max_chars=None,
                 apply_lexicon=True, chunk_chars=240, lexicon_only=None,
                 script_guard="block"):
    """Full preparation pipeline returning text, directions, chunks and report."""
    if cfg is not None and max_chars is None:
        max_chars = int(cfg.get("execution.max_chars", 4000))
    cleaner = TextPreparer(locale=locale, max_chars=max_chars)
    stripped, directions = parse_directions(str(text))
    result = cleaner.run(stripped, lexicon=lexicon if apply_lexicon else None,
                         lexicon_only=lexicon_only)
    chunks = chunk_text(result.text, max_chars=chunk_chars)
    script_report = detect_unsupported_script(result.text, locale)
    if script_report["has_unsupported"]:
        details = script_report["unsupported"]
        message = "the text contains characters the voice language (%s) cannot pronounce: %s" % (
            script_report["language"],
            ", ".join("%s %s (%d)" % (d["label"], d["sample"], d["count"]) for d in details))
        if script_guard == "block":
            raise InputError(
                message,
                hint="use a voice for that language, split the request per language, "
                     "transliterate the words, or wrap exact phonemes in [[...]]; set "
                     "execution.script_guard=warn to pass the text through unchanged",
                details=script_report)
        if script_guard == "warn":
            result.warnings.append(message)
    return {
        "text": result.text,
        "chunks": chunks,
        "directions": directions,
        "rewrites": result.rewrites,
        "warnings": result.warnings,
        "analysis": analyze_text(result.text, locale=locale),
        "scripts": script_report,
    }


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nv-text.py selftest")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args(argv)
    import time as _time
    checks = []

    def check(name, fn):
        started = _time.monotonic()
        try:
            detail = fn()
            checks.append({"check": name, "ok": True,
                           "ms": round((_time.monotonic() - started) * 1000, 2), "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "ms": round((_time.monotonic() - started) * 1000, 2),
                           "detail": "%s: %s" % (type(exc).__name__, exc)})

    def t_numbers():
        assert number_to_words(0) == "zero"
        assert number_to_words(21) == "twenty-one"
        assert number_to_words(105) == "one hundred five"
        assert number_to_words(1234) == "one thousand two hundred thirty-four"
        assert number_to_words(1000000) == "one million"
        assert number_to_words(-7) == "minus seven"
        assert number_to_words(3.5) == "three point five"
        return "0-1e6, negative and decimal integers spell correctly"

    def t_ordinals():
        assert ordinal_to_words(1) == "first"
        assert ordinal_to_words(21) == "twenty-first"
        assert ordinal_to_words(100) == "hundredth" or ordinal_to_words(100).endswith("th")
        return "ordinals spell correctly"

    def t_dates():
        prep = TextPreparer()
        out = prep.run("The launch is 2026-11-07 at 09:30 and 3/14/2027.")
        text = out.text
        assert "November seventh" in text, text
        assert "March fourteenth" in text or "March fourteenth 2027" in text, text
        return text

    def t_money():
        prep = TextPreparer()
        out = prep.run("It costs $19.99, £5 and 12% tax.")
        assert "nineteen dollars and ninety-nine cents" in out.text, out.text
        assert "five pounds" in out.text, out.text
        assert "twelve percent" in out.text, out.text
        return out.text

    def t_markup():
        prep = TextPreparer()
        out = prep.run("<p class='x'>Hello&nbsp;<b>world</b></p> **Bold** text",
                       )
        assert "<" not in out.text and "&" not in out.text, out.text
        assert "Hello" in out.text and "world" in out.text
        return out.text

    def t_ipa_and_chunks():
        result = prepare_text("Greetings, friends! Welcome to my video. [[faɪˈnæns]] rocks.")
        assert result["chunks"], result
        assert "[[faɪˈnæns]]" in result["text"]
        assert result["analysis"]["sentences"] >= 2
        return "%d chunk(s); analysis counts %d words" % (len(result["chunks"]),
                                                          result["analysis"]["words"])

    def t_directions():
        text, directions = parse_directions("Halt{pause:250} and *listen* now")
        assert directions["pauses"][0]["ms"] == 250, directions
        assert directions["emphasis"][0]["phrase"] == "listen", directions
        assert "{pause" not in text
        return "pause and emphasis directions parsed"

    def t_reject():
        prep = TextPreparer(max_chars=10)
        try:
            prep.run("This text is far too long for the limit.")
            raise AssertionError("limit not enforced")
        except InputError:
            return "character limit raises a clear InputError"

    def t_lexicon():
        lexicon = {"entries": [{"id": "tomato", "words": ["tomato"], "ipa": "təˈmɑːtoʊ"}]}
        prep = TextPreparer()
        out = prep.run("A tomato a day.", lexicon=lexicon)
        assert "[[təˈmɑːtoʊ]]" in out.text, out.text
        return out.text

    check("numbers", t_numbers)
    check("ordinals", t_ordinals)
    check("dates", t_dates)
    check("money-and-percent", t_money)
    check("markup", t_markup)
    check("ipa-and-chunks", t_ipa_and_chunks)
    check("directions", t_directions)
    check("limit", t_reject)
    check("lexicon", t_lexicon)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": "offline-neural-voice/text", "version": nvcore.__version__,
               "checks": checks}
    if args.format == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d text checks passed" % ("PASS" if ok else "FAIL",
                                                  sum(1 for c in checks if c["ok"]), len(checks)))
    return nvcore.EXIT_OK if ok else nvcore.EXIT_FAIL


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return nvcore.EXIT_OK
    if argv[0] == "selftest":
        return selftest(argv[1:])
    sys.stderr.write("unknown command %r; try `selftest --help`\n" % argv[0])
    return nvcore.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
