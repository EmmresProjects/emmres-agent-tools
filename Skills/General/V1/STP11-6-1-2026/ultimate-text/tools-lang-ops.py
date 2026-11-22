"""Languages/scripts/localization (12) and typography/editorial style (12).

Transliteration tables, romanization comparisons and casing rules live here;
typography applies deterministic character-level rules so output is byte-stable.
Two language capabilities are agent-assisted (multilingual creation, translation
editing) and therefore run through task contracts.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import unicodedata

EXPORTS = ["smart_quotes", "transliterate_text", "script_name", "locale_casing_rules"]
UNI_ASSET = "asset-unicode-tables.json"
LEX_ASSET = "asset-lexicons-language.json"


def _uni(ctx):
    return ctx.assets.json(UNI_ASSET)


def _lex(ctx):
    return ctx.assets.json(LEX_ASSET)


# --------------------------------------------------------------------------
# languages
# --------------------------------------------------------------------------

SCRIPT_DETECT = [
    ("Latin", lambda c: 0x41 <= ord(c) <= 0x24F or 0x1E00 <= ord(c) <= 0x1EFF),
    ("Greek", lambda c: 0x370 <= ord(c) <= 0x3FF or 0x1F00 <= ord(c) <= 0x1FFF),
    ("Cyrillic", lambda c: 0x400 <= ord(c) <= 0x4FF or 0x500 <= ord(c) <= 0x52F),
    ("Hebrew", lambda c: 0x590 <= ord(c) <= 0x5FF),
    ("Arabic", lambda c: 0x600 <= ord(c) <= 0x6FF or 0x750 <= ord(c) <= 0x77F),
    ("Devanagari", lambda c: 0x900 <= ord(c) <= 0x97F),
    ("Thai", lambda c: 0xE00 <= ord(c) <= 0xE7F),
    ("Hangul", lambda c: 0xAC00 <= ord(c) <= 0xD7AF or 0x1100 <= ord(c) <= 0x11FF),
    ("Hiragana", lambda c: 0x3041 <= ord(c) <= 0x309F),
    ("Katakana", lambda c: 0x30A1 <= ord(c) <= 0x30FF),
    ("Han", lambda c: 0x3400 <= ord(c) <= 0x9FFF),
]


def script_name(char):
    for name, test in SCRIPT_DETECT:
        if test(char):
            return name
    return None


def transliterate_text(text, tables, scheme):
    if scheme == "hangul":
        return _hangul_romanize(text, tables)
    mapping = tables["transliteration"].get(scheme)
    if mapping is None:
        raise core.InvalidParam("unknown transliteration scheme %r" % scheme)
    out = []
    for char in text:
        if char in mapping:
            out.append(mapping[char])
        else:
            decomposed = unicodedata.normalize("NFD", char)
            replaced = False
            for part in decomposed:
                if part in mapping:
                    out.append(mapping[part])
                    replaced = True
                elif unicodedata.combining(part):
                    continue
                elif part in mapping:
                    out.append(mapping[part])
                else:
                    out.append(part)
            if not replaced and not decomposed:
                out.append(char)
    return "".join(out)


def _hangul_romanize(text, tables):
    jamo = tables["hangul_jamo"]
    out = []
    for char in text:
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            index = code - 0xAC00
            initial = index // 588
            medial = (index % 588) // 28
            final = index % 28
            out.append(jamo["initial"][initial] + jamo["medial"][medial] + jamo["final"][final])
        else:
            out.append(char)
    return "".join(out)


def transliteration(ctx):
    text = ctx.require_text(minimum=1)
    tables = _uni(ctx)
    scheme = ctx.params["scheme"]
    if scheme == "auto":
        counts = {}
        for char in text:
            script = script_name(char)
            if script:
                counts[script] = counts.get(script, 0) + 1
        scheme = (max(counts, key=counts.get) if counts else "greek").lower()
        if scheme in ("hiragana", "katakana"):
            scheme = "kana"
        if scheme not in tables["transliteration"]:
            raise core.InvalidInput("cannot auto-detect a supported script (detected %s)" % (scheme or "none"),
                                    hint="Supported: %s" % ", ".join(sorted(tables["transliteration"])))
    if ctx.params["scheme"] == "auto":
        merged = {}
        for table in tables["transliteration"].values():
            for src, dst in table.items():
                merged.setdefault(src, dst)
        out = "".join(merged.get(char, char) for char in text)
        return {"output": out, "data": {"scheme": "auto (combined tables)",
                                        "scripts_covered": sorted(tables["transliteration"])}}
    if ctx.params["reverse"]:
        mapping = tables["transliteration"].get(scheme, {})
        reverse_map = {}
        for src, dst in mapping.items():
            if dst and dst not in reverse_map:
                reverse_map[dst] = src
                reverse_map[dst.capitalize()] = src.upper() if src.islower() else src
        out, idx = [], 0
        keys = sorted(reverse_map, key=len, reverse=True)
        while idx < len(text):
            for key in keys:
                if text.startswith(key, idx):
                    out.append(reverse_map[key])
                    idx += len(key)
                    break
            else:
                out.append(text[idx])
                idx += 1
        return {"output": "".join(out), "data": {"scheme": scheme, "direction": "reverse",
                                                 "note": "reverse transliteration is ambiguous; review the result"}}
    output = transliterate_text(text, tables, scheme)
    changed = sum(1 for a, b in zip(text, output) if a != b)
    return {"output": output, "data": {"scheme": scheme, "characters_converted": changed,
                                       "input_chars": len(text), "output_chars": len(output)}}


def romanization_guide(ctx):
    tables = _uni(ctx)
    script = ctx.params["script"]
    rows = []
    for entry in tables["romanization_systems"].get(script, []):
        systems = {k: v for k, v in entry.items() if k != "char"}
        rows.append([entry["char"]] + list(systems.values()))
    if not rows:
        raise core.InvalidInput("no comparison table for script %r" % script,
                                hint="Available: %s" % ", ".join(sorted(tables["romanization_systems"])))
    headers = ["char"] + list({k for e in tables["romanization_systems"][script] for k in e if k != "char"})
    text = core.table(rows, headers)
    text += ("\n\nSystems differ: pick one, state it, and keep it consistent. Hepburn favours "
             "English pronunciation; Kunrei-shiki follows Japanese morphology; ISO 9 is "
             "reversible with diacritics; GOST/BGN are used in mapping and news contexts.")
    return {"output": text, "data": {"script": script, "systems": headers[1:], "rows": len(rows)}}


def script_segmentation(ctx):
    text = ctx.require_text(minimum=1)
    unit = ctx.params["unit"]
    if unit == "script":
        runs, current, current_script = [], [], None
        for char in text:
            script = script_name(char)
            if script is None:
                current.append(char)
                continue
            if current_script is None or script == current_script:
                current_script = script
                current.append(char)
            else:
                runs.append({"script": current_script, "text": "".join(current)})
                current, current_script = [char], script
        if current:
            runs.append({"script": current_script or "Common", "text": "".join(current)})
        rows = [[r["script"], r["text"][:40], len(r["text"])] for r in runs]
        return {"output": core.table(rows, ["script", "text", "chars"]),
                "data": {"runs": runs, "count": len(runs),
                         "dominant": max(runs, key=lambda r: len(r["text"]))["script"] if runs else None}}
    if unit == "word":
        words = re.findall(r"[^\s]+", text)
        return {"output": "\n".join(words),
                "data": {"words": len(words), "unique": len(set(words))}}
    if unit == "sentence":
        sentences = core.sentence_split(text)
        return {"output": "\n".join(sentences),
                "data": {"sentences": len(sentences)}}
    if unit == "paragraph":
        blocks = re.split(r"\n\s*\n", core.canonical_newlines(text))
        blocks = [b.strip() for b in blocks if b.strip()]
        return {"output": "\n\n".join(blocks), "data": {"paragraphs": len(blocks)}}
    graphemes = []
    for char in text:
        if unicodedata.combining(char) and graphemes:
            graphemes[-1] += char
        else:
            graphemes.append(char)
    return {"output": " | ".join(graphemes),
            "data": {"grapheme_clusters": len(graphemes),
                     "note": "approximate clusters: base character plus combining marks"}}


def diacritic_restorer(ctx):
    text = ctx.require_text(minimum=1)
    lex = _lex(ctx)
    language = ctx.params["language"]
    mapping = dict(lex["diacritics"])
    changes = []
    out = text
    for plain in sorted(mapping, key=len, reverse=True):
        accented = mapping[plain]
        pattern = re.compile(r"\b%s\b" % re.escape(plain), re.I)
        if not pattern.search(out):
            continue
        def repl(match, accented=accented):
            original = match.group(0)
            if original.isupper():
                return accented.upper()
            if original[:1].isupper():
                return accented[:1].upper() + accented[1:]
            return accented
        new = pattern.sub(repl, out)
        if new != out:
            changes.append({"plain": plain, "accented": accented})
            out = new
    if ctx.params["style"] == "combining":
        out = unicodedata.normalize("NFD", out)
    else:
        out = unicodedata.normalize("NFC", out)
    data = {"language": language, "changes": changes, "count": len(changes),
            "form": ctx.params["style"]}
    warnings = [] if changes else ["no known unaccented loanwords found; nothing changed"]
    return {"output": out, "data": data, "warnings": warnings}


def orthography_converter(ctx):
    text = ctx.require_text(minimum=1)
    lex = _lex(ctx)
    variant = ctx.params["variant"]
    changes = []
    if variant in ("us-gb", "gb-us"):
        mapping = lex["us_gb"] if variant == "us-gb" else {v: k for k, v in lex["us_gb"].items()}
        out = text
        for src, dst in mapping.items():
            pattern = re.compile(r"\b%s\b" % re.escape(src), re.I)
            if pattern.search(out):
                out = H.replace_preserving_case(src, dst, out)
                changes.append({"from": src, "to": dst})
    elif variant in ("ss-de", "de-ss"):
        if variant == "ss-de":
            out = re.sub(r"ss", "\u00df", text)
            out = out.replace("SS", "\u1e9e")
        else:
            out = text.replace("\u00df", "ss").replace("\u1e9e", "SS")
        changes.append({"from": "ss/\u00df", "to": "converted"})
    else:  # fr-ligature
        out = text
        for src, dst in lex["orthography"]["fr_ligature"].items():
            if src in out:
                out = out.replace(src, dst)
                changes.append({"from": src, "to": dst})
    data = {"variant": variant, "changes": changes}
    return {"output": out, "data": data}


def rtl_layout(ctx):
    text = ctx.require_text(minimum=1)
    action = ctx.params["action"]
    base_dir = ctx.params["base_dir"]
    rtl_chars = sum(1 for c in text if unicodedata.bidirectional(c) in ("R", "AL", "AN"))
    ltr_chars = sum(1 for c in text if unicodedata.bidirectional(c) == "L")
    detected = "rtl" if rtl_chars > ltr_chars else "ltr"
    direction = base_dir if base_dir != "auto" else detected
    if action == "wrap-html":
        out = '<div dir="%s" lang="%s">%s</div>' % (direction, "ar" if direction == "rtl" else "en", text)
        return {"output": out, "data": {"direction": direction}}
    if action == "pad":
        lines = core.canonical_newlines(text).split("\n")
        width = max((len(line) for line in lines), default=0)
        out = "\n".join((line.rjust(width) if direction == "rtl" else line) for line in lines)
        return {"output": out, "data": {"direction": direction, "width": width}}
    if action == "isolate":
        out = "\u2067" + text + "\u2069" if direction == "rtl" else text
        return {"output": out, "data": {"direction": direction,
                                        "isolate": "RLI ... PDI" if direction == "rtl" else "none needed"}}
    data = {"direction": direction, "rtl_chars": rtl_chars, "ltr_chars": ltr_chars,
            "controls": [c for c in text if unicodedata.bidirectional(c) in
                         ("RLE", "LRE", "RLO", "LRO", "PDF", "RLI", "LRI", "FSI", "PDI")]}
    output = ("direction: %s (%d RTL chars, %d LTR chars)\n"
              "layout advice: set dir=%s on the container, keep numbers and Latin runs in "
              "isolates, and never reverse strings manually." % (direction, rtl_chars, ltr_chars, direction))
    return {"output": output, "data": data}


def input_method_helper(ctx):
    tables = _uni(ctx)
    text = ctx.require_text(minimum=1) if not ctx.params["target"] else ctx.params["target"]
    platform = ctx.params["platform"]
    rows = []
    for char in dict.fromkeys(text):
        if char.isspace():
            continue
        entry = next((e for e in tables["keyboard_shortcuts"] if e["symbol"] == char), None)
        rows.append([char, "U+%04X" % ord(char), entry["name"] if entry else _compose_name(char),
                     (entry or {}).get(platform, "no direct shortcut; use a character map")])
    if not rows:
        raise core.InvalidInput("no characters to map")
    return {"output": core.table(rows, ["char", "code point", "name", platform]),
            "data": {"platform": platform, "rows": rows}}


def _compose_name(char):
    try:
        return unicodedata.name(char).title()
    except ValueError:
        return "<unnamed>"


def locale_casing(ctx):
    text = ctx.require_text(minimum=1)
    locale = ctx.params["locale"]
    case = ctx.params["case"]
    lang = locale.split("-")[0].lower()
    if case == "upper":
        out = text.upper()
        if lang == "tr" or lang == "az":
            out = text.replace("i", "\u0130").replace("ı", "I").upper().replace("I\u0307", "\u0130")
            out = out.replace("i", "\u0130")
        if lang == "de":
            out = out.replace("\u00df", "SS")
        if lang == "lt":
            out = out.replace("i", "i\u0307").upper()
    elif case == "lower":
        out = text.lower()
        if lang == "tr" or lang == "az":
            out = text.replace("I", "\u0131").replace("\u0130", "i").lower()
        if lang == "el":
            out = _greek_final_sigma(out)
    elif case == "title":
        out = H.title_case(text)
        if lang == "nl" and re.search(r"\bij", out, re.I):
            out = re.sub(r"\b([Ii])j", "IJ", out)
        if lang == "en" and locale.endswith(("GB", "IE")):
            pass
    else:
        out = H.sentence_case(text)
    data = {"locale": locale, "case": case, "note": _casing_note(lang, case)}
    return {"output": out, "data": data}


def locale_casing_rules(locale):
    """Return the casing rules that apply to a locale tag."""
    lang = locale.split("-")[0].lower()
    rules = []
    if lang in ("tr", "az"):
        rules.append("dotted/dotless i mapping")
    if lang == "de":
        rules.append("sharp s expands to SS on uppercase")
    if lang == "el":
        rules.append("final sigma word-finally")
    if lang == "nl":
        rules.append("ij digraph capitalises as IJ")
    if lang == "lt":
        rules.append("i keeps a dot above in uppercase")
    return rules or ["context-free Unicode casing"]


def _greek_final_sigma(text):
    out = []
    for idx, char in enumerate(text):
        if char == "\u03c3":
            nxt = text[idx + 1] if idx + 1 < len(text) else ""
            out.append("\u03c2" if not nxt.isalpha() else char)
        else:
            out.append(char)
    return "".join(out)


def _casing_note(lang, case):
    if lang in ("tr", "az") and case in ("upper", "lower"):
        return "dotted/dotless i rules applied (Turkish/Azeri)"
    if lang == "de" and case == "upper":
        return "sharp s expands to SS when uppercasing"
    if lang == "el" and case == "lower":
        return "final sigma applied word-finally"
    return "standard Unicode casing (context-free)"


PLURAL_CATEGORIES = {
    "en": ["one", "other"], "de": ["one", "other"], "es": ["one", "many", "other"],
    "fr": ["one", "many", "other"], "it": ["one", "many", "other"],
    "pt": ["one", "many", "other"], "ru": ["one", "few", "many", "other"],
    "uk": ["one", "few", "many", "other"], "pl": ["one", "few", "many", "other"],
    "ar": ["zero", "one", "two", "few", "many", "other"], "ja": ["other"],
    "zh": ["other"], "ko": ["other"], "tr": ["one", "other"], "id": ["other"],
}


def plural_template(ctx):
    text = ctx.require_text(minimum=1)
    locales = [l.strip() for l in ctx.params["locales"] if l.strip()] or ["en"]
    variable = "count"
    match = re.search(r"\{(\w+)\}", text)
    if match:
        variable = match.group(1)
    format = ctx.params["format"]
    templates = {}
    for locale in locales:
        lang = locale.split("-")[0].lower()
        categories = PLURAL_CATEGORIES.get(lang, ["one", "other"])
        if format == "printf":
            templates[locale] = "%d " + re.sub(r"\{\w+\}", "", text).strip()
            continue
        arms = []
        for cat in categories:
            placeholder = "{%s}" % variable
            body = text
            if cat == "one":
                body = re.sub(r"(\w+)s([.!?]?)$", r"\1\2", body.rstrip())
            if placeholder not in body:
                body = body.rstrip(".") + " " + placeholder + "."
            arms.append("%s {%s}" % (cat, body))
        templates[locale] = "{%s, plural, %s}" % (variable, " ".join(arms))
    balance = {}
    for locale, template in templates.items():
        opens, closes = template.count("{"), template.count("}")
        balance[locale] = "balanced" if opens == closes else "UNBALANCED (%d/%d)" % (opens, closes)
    if format == "json":
        output = json.dumps({"variable": variable, "templates": templates, "balance": balance},
                            indent=2, ensure_ascii=False)
    else:
        output = "\n\n".join("%s:\n  %s" % (locale, template) for locale, template in templates.items())
        output += "\n\nplural categories per locale: " + ", ".join(
            "%s=%s" % (l, "/".join(PLURAL_CATEGORIES.get(l.split("-")[0], ["one", "other"]))) for l in locales)
    warnings = [k for k, v in balance.items() if v != "balanced"]
    return {"output": output, "data": {"templates": templates, "balance": balance,
                                       "variable": variable},
            "warnings": ["unbalanced braces for: %s" % ", ".join(warnings)] if warnings else []}


def language_learning(ctx):
    text = ctx.require_text(minimum=20)
    level = ctx.params["level"]
    annotate = ctx.params["annotate"]
    metrics = H.reading_metrics(text)
    target_words = {"A1": 8, "A2": 12, "B1": 18, "B2": 24, "C1": 30}.get(level, 18)
    sentences = core.sentence_split(text)
    simplified = []
    for sentence in sentences:
        if len(sentence.split()) <= target_words:
            simplified.append(sentence)
        else:
            parts = re.split(r"(?<=;)\s+|,\s+(?=and|but|which|while|because)\s*", sentence)
            buffer = ""
            for part in parts:
                if len((buffer + " " + part).split()) <= target_words:
                    buffer = (buffer + " " + part).strip()
                else:
                    if buffer:
                        simplified.append(buffer)
                    buffer = part.strip()
            if buffer:
                simplified.append(buffer)
    freq = H.word_freq(text)
    glossary = [[word, count] for word, count in sorted(freq.items(), key=lambda kv: -kv[1])[:15]]
    out = []
    if annotate in ("glossary", "all"):
        out.append("## Glossary (most frequent content words)")
        out.append(core.table(glossary, ["word", "count"]))
        out.append("")
    if annotate in ("segmentation", "all"):
        out.append("## Sentence segmentation (target <= %d words)" % target_words)
        out.extend("- %s" % s for s in simplified)
        out.append("")
    if annotate in ("interlinear", "all"):
        out.append("## Interlinear view")
        for sentence in simplified[:6]:
            out.append(sentence)
            out.append(" | ".join("%s" % w for w in sentence.split()[:12]))
            out.append("")
    out.append("## Level check for %s" % level)
    out.append("- vocabulary size (unique content words): %d" % len(freq))
    out.append("- average sentence length: %.1f words (target <= %d)" % (metrics["avg_sentence_length"], target_words))
    out.append("- reading ease: %.1f" % metrics["flesch_reading_ease"])
    data = {"level": level, "metrics": metrics, "glossary": glossary,
            "simplified_sentences": len(simplified)}
    warnings = [] if metrics["avg_sentence_length"] <= target_words else \
        ["source sentences exceed the %s target length; simplified version provided" % level]
    return {"output": "\n".join(out), "data": data, "warnings": warnings}


# --------------------------------------------------------------------------
# typography
# --------------------------------------------------------------------------


OPEN_DQ = {"en": ("\u201c", "\u201d"), "de": ("\u201e", "\u201c"), "fr": ("\u00ab\u00a0", "\u00a0\u00bb"),
           "es": ("\u00ab", "\u00bb"), "ru": ("\u00ab", "\u00bb")}
OPEN_SQ = {"en": ("\u2018", "\u2019"), "de": ("\u201a", "\u2018"), "fr": ("\u2039\u00a0", "\u00a0\u203a"),
           "es": ("\u2039", "\u203a"), "ru": ("\u2039", "\u203a")}


def smart_quotes(text, language="en", direction="to-curly", primes=True):
    if direction == "to-straight":
        return (text.replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2018", "'").replace("\u2019", "'")
                .replace("\u201e", '"').replace("\u201a", "'")
                .replace("\u00ab", '"').replace("\u00bb", '"')
                .replace("\u2039", "'").replace("\u203a", "'"))
    open_dq, close_dq = OPEN_DQ.get(language, OPEN_DQ["en"])
    open_sq, close_sq = OPEN_SQ.get(language, OPEN_SQ["en"])
    out = []
    prev = ""
    for char in text:
        if char == '"':
            out.append(open_dq if (prev == "" or prev in " \t\n([{\u2014\u2013-") else close_dq)
        elif char == "'":
            if prev.isalnum():
                out.append("\u2019")
            else:
                out.append(open_sq if (prev == "" or prev in " \t\n([{") else close_sq)
        elif char == "\u2032":
            out.append("\u2032")
        else:
            out.append(char)
        prev = char
    result = "".join(out)
    if primes:
        result = re.sub(r"(?<=\d)'(?=\d)", "\u2032", result)
        result = re.sub(r"(?<=\d)\"(?=\d)", "\u2033", result)
    return result


def typography_cleaner(ctx):
    text = ctx.require_text(minimum=1)
    profile = H.STYLE_PROFILES.get(ctx.params["profile"], H.STYLE_PROFILES["neutral-us"])
    out = text
    fixes = []

    def record(name, before, after):
        if before != after:
            fixes.append({"fix": name, "changed": True})

    if ctx.params["fix_spacing"]:
        new = re.sub(r"[ \t]{2,}", " ", out)
        new = re.sub(r"\s+([,.;:!?])", r"\1", new)
        new = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", new)
        record("spacing", out, new)
        out = new
    if ctx.params["ellipsis"]:
        new = out.replace("...", "\u2026")
        record("ellipsis", out, new)
        out = new
    if ctx.params["dashes"]:
        new = re.sub(r"(?<=\w)--(?=\w)", "\u2014", out)
        new = re.sub(r"(?<=\s)--(?=\s)", "\u2014", new)
        new = re.sub(r"(?<=\d)-(?=\d)", "\u2013", new)
        record("dashes", out, new)
        out = new
    if ctx.params["smart_quotes"]:
        new = smart_quotes(out, "en", "to-curly" if profile["quotes"] == "curly" else "to-straight")
        record("quotes", out, new)
        out = new
    new = re.sub(r"[ \t]+\n", "\n", out)
    new = re.sub(r"\n{3,}", "\n\n", new)
    record("whitespace", out, new)
    out = new
    data = {"profile": ctx.params["profile"], "fixes": fixes,
            "fix_count": len(fixes),
            "straight_quotes_left": out.count('"'),
            "nbsp_count": out.count("\u00a0")}
    return {"output": out, "data": data}


def punctuation_style(ctx):
    text = ctx.require_text(minimum=1)
    serial = bool(ctx.params["serial_comma"])
    dash = ctx.params["dash"]
    quotes = ctx.params["quotes"]
    spacing = ctx.params["spacing"]
    out = text
    changes = []
    list_pattern = re.compile(r"(\w+),\s+(\w+)\s+and\s+(\w+)")
    if serial:
        if list_pattern.search(out):
            out = list_pattern.sub(r"\1, \2, and \3", out)
            changes.append("added serial comma")
    else:
        if re.search(r"(\w+),\s+(\w+),\s+and\s+(\w+)", out):
            out = re.sub(r"(\w+),\s+(\w+),\s+and\s+(\w+)", r"\1, \2 and \3", out)
            changes.append("removed serial comma")
    dash_map = {"em": "\u2014", "en": "\u2013", "hyphen": "-"}
    target_dash = dash_map.get(dash, "\u2014")
    new = re.sub(r"\s*[\u2014\u2013]\s*", target_dash if dash != "hyphen" else " - ", out)
    if new != out:
        changes.append("dash style -> %s" % dash)
        out = new
    quote_map = {"curly": ("to-curly", "en"), "straight": ("to-straight", "en")}
    if quotes in quote_map:
        out = smart_quotes(out, "en", quote_map[quotes][0])
        changes.append("quote style -> %s" % quotes)
    elif quotes == "guillemet":
        out = smart_quotes(out, "fr", "to-curly")
        changes.append("quote style -> guillemets")
    if spacing == "none":
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    elif spacing == "one":
        out = re.sub(r"\s*([,;:])(?=\S)", r"\1 ", out)
    elif spacing == "nbsp-before":
        out = re.sub(r" ([;:!?])", "\u00a0\\1", out)
    data = {"changes": changes, "serial_comma": serial, "dash": dash, "quotes": quotes,
            "spacing": spacing}
    return {"output": out, "data": data, "warnings": [] if changes else ["text already matched the requested style"]}


def smart_quote_manager(ctx):
    text = ctx.require_text(minimum=1)
    out = smart_quotes(text, ctx.params["language"], ctx.params["direction"], bool(ctx.params["primes"]))
    counts = {name: out.count(ch) for name, ch in
              [("left double", "\u201c"), ("right double", "\u201d"),
               ("left single", "\u2018"), ("right single", "\u2019"),
               ("straight double", '"'), ("straight single", "'")]}
    warnings = []
    if counts["left double"] != counts["right double"] and out.count('"') == 0:
        warnings.append("unbalanced curly double quotes")
    return {"output": out, "data": {"direction": ctx.params["direction"], "counts": counts},
            "warnings": warnings}


def dash_specialist(ctx):
    text = ctx.require_text(minimum=1)
    dash = {"em": "\u2014", "en": "\u2013", "hyphen": "-", "minus": "\u2212"}[ctx.params["dash"]]
    spaced = bool(ctx.params["spaced"])
    out = text
    if ctx.params["ranges"]:
        out = re.sub(r"(?<=\d)-(?=\d)", dash if dash != "-" else "-", out)
    out = re.sub(r"(?<=\s)--(?=\s)", " %s " % dash if spaced else dash, out)
    out = re.sub(r"(?<=[A-Za-z])--(?=[A-Za-z])", dash, out)
    out = re.sub(r"\s*[\u2014\u2013\u2212]\s*", (dash if spaced else " " + dash + " ") if dash != "-" else " - ", out)
    data = {"dash": ctx.params["dash"], "codepoint": "U+%04X" % ord(dash),
            "em_count": out.count("\u2014"), "en_count": out.count("\u2013"),
            "minus_count": out.count("\u2212"), "hyphen_count": out.count("-")}
    return {"output": out, "data": data}


def whitespace_designer(ctx):
    text = ctx.require_text(minimum=1)
    width = int(ctx.params["width"])
    out = text
    if ctx.params["nbsp"]:
        out = re.sub(r"(\d)\s+(km|kg|cm|mm|ml|GB|MB|TB|%|hours?|minutes?|seconds?|days?|weeks?|months?|years?)\b",
                     "\\1\u00a0\\2", out)
        out = re.sub(r"\b(=\s?)\b", "=", out)
        out = re.sub(r"\b(p\.|pp\.|No\.|Fig\.|Dr\.|Mr\.|Ms\.)\s+", lambda m: m.group(0).replace(" ", "\u00a0"), out)
    if ctx.params["prevent_widows"]:
        lines = out.split("\n")
        fixed = []
        for line in lines:
            stripped = line.rstrip()
            if " " in stripped and len(stripped.split()[-1]) <= 3:
                idx = stripped.rfind(" ")
                stripped = stripped[:idx] + "\u00a0" + stripped[idx + 1:]
            fixed.append(stripped)
        out = "\n".join(fixed)
    if ctx.params["hard_wrap"] and width:
        wrapped = []
        for block in re.split(r"\n\s*\n", core.canonical_newlines(out)):
            if not block.strip():
                continue
            wrapped.append("\n".join(_wrap(block.strip(), width)))
        out = "\n\n".join(wrapped)
    data = {"width": width, "nbsp_count": out.count("\u00a0"),
            "longest_line": max((len(line) for line in out.split("\n")), default=0),
            "nbsp": bool(ctx.params["nbsp"]), "hard_wrap": bool(ctx.params["hard_wrap"])}
    return {"output": out, "data": data}


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [text]


LOCALE_NUMBERS = {
    "en-US": {"sep": ",", "dec": ".", "space": ""},
    "en-GB": {"sep": ",", "dec": ".", "space": ""},
    "de-DE": {"sep": ".", "dec": ",", "space": ""},
    "fr-FR": {"sep": "\u202f", "dec": ",", "space": ""},
    "es-ES": {"sep": ".", "dec": ",", "space": ""},
    "ja-JP": {"sep": ",", "dec": ".", "space": ""},
}


def numeral_formatter(ctx):
    text = ctx.require_text(minimum=1)
    locale = ctx.params["locale"]
    style = LOCALE_NUMBERS.get(locale, LOCALE_NUMBERS["en-US"])
    unit_space = {"yes": " ", "no": "", "nbsp": "\u00a0"}[ctx.params["unit_space"]]
    group = bool(ctx.params["group_digits"])
    changes = []

    def fmt_number(match):
        raw = match.group(0)
        integer, _, decimals = raw.partition(".")
        if group:
            sign = ""
            if integer.startswith("-"):
                sign, integer = "-", integer[1:]
            grouped = ""
            while len(integer) > 3:
                grouped = style["sep"] + integer[-3:] + grouped
                integer = integer[:-3]
            integer = sign + integer + grouped
        if decimals:
            return integer + style["dec"] + decimals
        return integer

    def _group_guard(match):
        token = match.group(0)
        digits = token.split(".")[0].lstrip("-")
        if len(digits) == 4 and 1900 <= int(digits) <= 2099 and "." not in token:
            return token  # treat as a year, do not group
        return fmt_number(match)

    out = re.sub(r"(?<![\w.])\d{4,}(?:\.\d+)?", _group_guard, text)
    if out != text:
        changes.append("digit grouping -> %s" % locale)
    out = re.sub(r"(\d)\s*(km|kg|cm|mm|ml|GB|MB|TB|kW|mph|km/h|%|h|min|s)\b",
                 lambda m: m.group(1) + unit_space + m.group(2), out)
    if ctx.params["numerals"] == "arabic-indic":
        out = out.translate({ord(str(d)): chr(0x0660 + d) for d in range(10)})
    elif ctx.params["numerals"] == "roman":
        out = re.sub(r"\b([1-9]|1[0-2])\b", lambda m: _roman(int(m.group(1))), out)
    elif ctx.params["numerals"] == "latin":
        out = out.translate({0x0660 + d: str(d) for d in range(10)})
    data = {"locale": locale, "grouping": style["sep"], "decimal": style["dec"],
            "unit_space": unit_space, "changes": changes}
    return {"output": out, "data": data}


def _roman(number):
    values = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, symbol in values:
        while number >= value:
            out += symbol
            number -= value
    return out


DATE_FORMATS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"), ("m", "d", "y")),
    (re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)"), ("d", "m", "y")),
    (re.compile(r"(?<!\w)(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})(?!\d)"), ("d", "mon", "y")),
    (re.compile(r"(?<!\w)([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})(?!\d)"), ("mon", "d", "y")),
]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


def date_editor(ctx):
    text = ctx.require_text(minimum=4)
    target = ctx.params["target"]
    include_time = bool(ctx.params["include_time"])
    parsed, failures = [], []
    for sentence in core.sentence_split(text):
        found = False
        for pattern, order in DATE_FORMATS:
            for match in pattern.finditer(sentence.strip()):
                parts = dict(zip(order, match.groups()))
                try:
                    year = int(parts.get("y", 0))
                    month_text = parts.get("m") or parts.get("mon")
                    if month_text and not str(month_text).isdigit():
                        month = MONTHS.index(str(month_text).lower()[:9] if str(month_text).lower()[:3] != "sep" else "september") + 1
                    else:
                        month = int(month_text)
                    day = int(parts.get("d", 1))
                    parsed.append((_dt.date(year, month, day), match.group(0)))
                    found = True
                except (ValueError, IndexError):
                    failures.append(match.group(0))
        if not found and re.search(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b", sentence):
            failures.append(sentence[:60])
    if not parsed:
        raise core.InvalidInput("no parseable dates found",
                                hint="Supported inputs include 2024-05-06, 06/05/2024, 6 May 2024, May 6, 2024.")
    rendered = []
    for date, original in parsed:
        iso = date.isoformat()
        if target == "iso":
            rendered.append((original, iso))
        elif target == "rfc":
            rendered.append((original, date.strftime("%a, %d %b %Y") + " 00:00:00 +0000"))
        elif target == "long-us":
            rendered.append((original, "%s %d, %d" % (MONTHS[date.month - 1].capitalize(), date.day, date.year)))
        elif target == "long-gb":
            rendered.append((original, "%d %s %d" % (date.day, MONTHS[date.month - 1].capitalize(), date.year)))
        elif target == "eu":
            rendered.append((original, "%02d.%02d.%d" % (date.day, date.month, date.year)))
        else:  # relative
            delta = (date - _dt.date.today()).days
            rendered.append((original, "%d day(s) from today" % delta))
    out = text
    for original, new in rendered:
        if include_time and target == "iso":
            new = new + "T00:00:00Z"
        out = out.replace(original, new, 1)
    rows = [[o, n] for o, n in rendered]
    data = {"target": target, "converted": len(rendered), "failures": failures}
    warnings = ["%d date-like string(s) could not be parsed" % len(set(failures))] if failures else []
    return {"output": out, "data": data,
            "diagnostics": ["date conversions: %s" % ", ".join("%s->%s" % tuple(r) for r in rows[:8])],
            "warnings": warnings}


def citation_formatter(ctx):
    raw = ctx.params["fields"].strip()
    if not raw:
        raise core.InvalidInput("give citation fields as JSON with --set fields='{...}'",
                                hint='Example: {"authors":["Hopper G."],"title":"...","year":1952,"journal":"...","doi":"..."}')
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise core.InvalidInput("fields must be valid JSON: %s" % exc.msg)
    style = ctx.params["style"]
    source = ctx.params["source_type"]
    authors = fields.get("authors") or fields.get("author") or []
    if isinstance(authors, str):
        authors = [authors]
    title = fields.get("title", "").strip()
    year = str(fields.get("year", "n.d."))
    journal = fields.get("journal") or fields.get("publisher") or fields.get("site", "")
    volume = fields.get("volume", "")
    pages = fields.get("pages", "")
    doi = fields.get("doi", "")
    url = fields.get("url", "")
    accessed = fields.get("accessed", "")
    if style == "apa":
        authors_fmt = _apa_authors(authors)
        out = "%s (%s). %s. %s%s%s%s" % (
            authors_fmt, year, title, journal,
            (", %s" % volume) if volume else "",
            (", %s" % pages) if pages else "",
            (" %s" % doi or url) if (doi or url) else "")
    elif style == "mla":
        author = authors[0] if authors else ""
        out = '%s. "%s." %s, %s%s%s.' % (author, title, journal, year,
                                          (", %s" % volume) if volume else "",
                                          (", pp. %s" % pages) if pages else "")
    elif style == "chicago":
        author = authors[0] if authors else ""
        out = '%s. "%s." %s (%s)%s.' % (author, title, journal, year,
                                        (": %s" % pages) if pages else "")
    elif style == "ieee":
        out = "[1] %s, \"%s,\" %s, %s%s." % (", ".join(authors), title, journal, year,
                                             (", vol. %s" % volume) if volume else "")
    else:  # harvard
        out = "%s (%s) '%s', %s%s%s." % (_apa_authors(authors), year, title, journal,
                                          (", %s" % volume) if volume else "",
                                          (", pp. %s" % pages) if pages else "")
    if accessed and style in ("apa", "harvard"):
        out += " Retrieved %s, from %s" % (accessed, url)
    data = {"style": style, "source_type": source, "citation": out,
            "fields": fields, "missing": [k for k in ("authors", "title", "year") if not fields.get(k)]}
    return {"output": out, "data": data,
            "warnings": ["missing required field(s): %s" % ", ".join(data["missing"])] if data["missing"] else []}


def _apa_authors(authors):
    parts = []
    for author in authors:
        bits = author.replace(",", "").split()
        if len(bits) >= 2:
            parts.append("%s, %s." % (bits[-1], " ".join(b[:1] + "." for b in bits[:-1])))
        else:
            parts.append(author)
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + ", & " + parts[-1]
    return parts[0] if parts else ""


def headline_case(ctx):
    text = ctx.require_text(minimum=1)
    keep = [k for k in re.split(r"[,;]", ctx.params["keep"]) if k]
    style = ctx.params["style"]
    if style == "upper":
        out = text.upper()
    elif style == "lower":
        out = text.lower()
    elif style == "sentence":
        out = H.sentence_case(text)
    else:
        out = H.title_case(text, keep, style)
    return {"output": out, "data": {"style": style, "kept": keep,
                                     "words": len(out.split())}}


def widow_helper(ctx):
    text = ctx.require_text(minimum=1)
    width = int(ctx.params["width"])
    min_words = int(ctx.params["min_words_last_line"])
    mode = ctx.params["mode"]
    lines = core.canonical_newlines(text).split("\n")
    wrapped, widows = [], []
    for idx, line in enumerate(lines):
        if not line.strip():
            wrapped.append(line)
            continue
        chunks = _wrap(line.strip(), width) if mode in ("rewrap", "prevent") else [line]
        if len(chunks) > 1 and len(chunks[-1].split()) < min_words:
            widows.append({"line": idx + 1, "last_line": chunks[-1]})
        wrapped.extend(chunks)
    out = "\n".join(wrapped)
    if mode == "prevent" and widows:
        out = "\n".join(line.rstrip() + "\u00a0" if line.endswith(" ") else line for line in out.split("\n"))
    lengths = [len(line) for line in wrapped if line.strip()]
    rag = round(sum(lengths) / len(lengths), 1) if lengths else 0
    data = {"mode": mode, "width": width, "widows": widows, "widow_count": len(widows),
            "average_line_length": rag,
            "max_line": max(lengths) if lengths else 0}
    warnings = ["%d widow(s) detected (short final line)" % len(widows)] if widows else []
    output = out if mode != "analyze" else (
        "widows: %d\nlongest line: %d\naverage line: %s\n%s" % (
            len(widows), data["max_line"], rag,
            "\n".join("- line %d ends with %r" % (w["line"], w["last_line"]) for w in widows[:10])))
    return {"output": output, "data": data, "warnings": warnings}


def readability_analyzer(ctx):
    text = ctx.require_text(minimum=20)
    metrics = H.reading_metrics(text)
    wanted = [m.lower() for m in ctx.params["metrics"]]
    label_map = {
        "flesch": ("flesch_reading_ease", "Flesch reading ease (0-100, higher is easier)"),
        "fk": ("flesch_kincaid_grade", "Flesch-Kincaid grade level"),
        "fog": ("gunning_fog", "Gunning fog index"),
        "smog": ("smog_index", "SMOG index"),
        "ari": ("automated_readability_index", "Automated readability index"),
        "coleman-liau": ("coleman_liau", "Coleman-Liau index"),
    }
    rows = []
    for key in wanted or list(label_map):
        field, label = label_map.get(key, (None, None))
        if field:
            rows.append([label, metrics[field]])
    rows.append(["average sentence length (words)", metrics["avg_sentence_length"]])
    rows.append(["sentence length variation (stdev)", metrics["sentence_length_stdev"]])
    rows.append(["longest sentence (words)", metrics["longest_sentence_words"]])
    rhythm = []
    lengths = [len(s.split()) for s in core.sentence_split(text)]
    if lengths:
        alternating = sum(1 for a, b in zip(lengths, lengths[1:]) if abs(a - b) >= 8)
        rhythm.append("sentences with a sharp length change: %d" % alternating)
        if metrics["sentence_length_stdev"] < 3 and len(lengths) > 4:
            rhythm.append("rhythm is very uniform; vary sentence length for readability")
        elif metrics["sentence_length_stdev"] > 14:
            rhythm.append("rhythm is erratic; group short sentences deliberately")
        else:
            rhythm.append("rhythm is within a comfortable range")
    data = {"metrics": metrics, "rhythm": rhythm}
    output = core.table([[r[0], r[1]] for r in rows], ["metric", "value"]) + "\n\n- " + "\n- ".join(rhythm)
    return {"output": output, "data": data}


def house_style(ctx):
    text = ctx.require_text(minimum=1)
    profile_name = ctx.params["profile"]
    profile = H.STYLE_PROFILES.get(profile_name)
    if profile is None:
        raise core.InvalidParam("unknown style profile %r" % profile_name,
                                hint="Available: %s" % ", ".join(sorted(H.STYLE_PROFILES)))
    violations = []
    if profile["quotes"] == "curly" and '"' in text:
        violations.append({"rule": "curly quotes", "count": text.count('"')})
    if profile["quotes"] == "straight" and ("\u201c" in text or "\u201d" in text):
        violations.append({"rule": "straight quotes", "count": text.count("\u201c") + text.count("\u201d")})
    if profile["dash"] == "em" and "\u2013" in text:
        violations.append({"rule": "em dash", "count": text.count("\u2013")})
    if profile["dash"] == "en" and "\u2014" in text:
        violations.append({"rule": "en dash", "count": text.count("\u2014")})
    serial = re.findall(r"\w+,\s+\w+,?\s+and\s+\w+", text)
    if profile.get("serial_comma") and any(not re.search(r",\s+and\s", s) for s in serial):
        violations.append({"rule": "serial comma", "count": len(serial)})
    lex = _lex(ctx)
    if profile.get("spell") == "us":
        british = [w for w in lex["us_gb"].values() if re.search(r"\b%s\b" % w, text)]
        if british:
            violations.append({"rule": "US spelling", "count": len(british),
                               "examples": british[:5]})
    elif profile.get("spell") == "gb":
        american = [w for w in lex["us_gb"] if re.search(r"\b%s\b" % w, text)]
        if american:
            violations.append({"rule": "GB spelling", "count": len(american),
                               "examples": american[:5]})
    out = text
    if ctx.params["apply"]:
        if profile["quotes"] == "curly":
            out = smart_quotes(out, "en", "to-curly")
        elif profile["quotes"] == "straight":
            out = smart_quotes(out, "en", "to-straight")
        if profile["dash"] == "em":
            out = out.replace("\u2013", "\u2014")
        elif profile["dash"] == "en":
            out = out.replace("\u2014", "\u2013")
        if profile.get("spell") == "us":
            for us, gb in lex["us_gb"].items():
                out = H.replace_preserving_case(gb, us, out)
        elif profile.get("spell") == "gb":
            for us, gb in lex["us_gb"].items():
                out = H.replace_preserving_case(us, gb, out)
    data = {"profile": profile_name, "violations": violations,
            "violation_count": len(violations), "applied": bool(ctx.params["apply"])}
    if ctx.params["report"] and violations:
        rows = [[v["rule"], v["count"], ", ".join(v.get("examples", []))] for v in violations]
        return {"output": out if ctx.params["apply"] else core.table(rows, ["rule", "count", "examples"]),
                "data": data}
    return {"output": out if ctx.params["apply"] else "style profile %r satisfied" % profile_name,
            "data": data}


HANDLERS = {
    "languages.transliteration-engine": transliteration,
    "languages.romanization-system-guide": romanization_guide,
    "languages.script-detection-and-segmentation": script_segmentation,
    "languages.diacritic-and-accent-restorer": diacritic_restorer,
    "languages.orthography-converter": orthography_converter,
    "languages.right-to-left-layout-editor": rtl_layout,
    "languages.input-method-helper": input_method_helper,
    "languages.locale-aware-casing-specialist": locale_casing,
    "languages.plural-and-inflection-template-builder": plural_template,
    "languages.language-learning-text-adapter": language_learning,
    "typography.typography-cleaner": typography_cleaner,
    "typography.punctuation-style-converter": punctuation_style,
    "typography.smart-quote-manager": smart_quote_manager,
    "typography.dash-and-hyphen-specialist": dash_specialist,
    "typography.whitespace-and-line-break-designer": whitespace_designer,
    "typography.numeral-and-unit-formatter": numeral_formatter,
    "typography.date-and-time-style-editor": date_editor,
    "typography.citation-style-text-formatter": citation_formatter,
    "typography.headline-and-title-case-editor": headline_case,
    "typography.widow-orphan-and-rag-helper": widow_helper,
    "typography.readability-and-rhythm-analyzer": readability_analyzer,
    "typography.house-style-enforcer": house_style,
}
