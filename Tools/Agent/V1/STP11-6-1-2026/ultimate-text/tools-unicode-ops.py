"""Unicode (12) and ASCII / legacy character set (12) capabilities.

Authoritative per-character data comes from ``unicodedata``; curated mappings
(confusables, transliteration, standards index, shortcuts) come from
``asset-unicode-tables.json``. Nothing here touches the network.
"""

from __future__ import annotations

import codecs
import json
import re
import shutil
import subprocess
import unicodedata

EXPORTS = ["script_of", "skeleton", "detect_mojibake", "char_report",
           "ascii_safe", "invisible_chars_in"]
TABLES_ASSET = "asset-unicode-tables.json"

CURATED_SYMBOL_RANGES = {
    "math": [(0x2200, 0x22FF), (0x2A00, 0x2AFF), (0x27C0, 0x27EF)],
    "arrow": [(0x2190, 0x21FF), (0x2900, 0x297F), (0x27F0, 0x27FF)],
    "currency": [(0x20A0, 0x20CF)],
    "punctuation": [(0x2000, 0x206F), (0x2E00, 0x2E7F)],
    "box": [(0x2500, 0x257F), (0x2580, 0x259F)],
    "technical": [(0x2300, 0x23FF)],
    "geometric": [(0x25A0, 0x25FF)],
}

_CONTROL_NAMES = {
    0x00: "NUL", 0x01: "SOH", 0x02: "STX", 0x03: "ETX", 0x04: "EOT", 0x05: "ENQ",
    0x06: "ACK", 0x07: "BEL", 0x08: "BS", 0x09: "HT (tab)", 0x0A: "LF (newline)",
    0x0B: "VT", 0x0C: "FF (form feed)", 0x0D: "CR (carriage return)", 0x0E: "SO",
    0x0F: "SI", 0x10: "DLE", 0x11: "DC1", 0x12: "DC2", 0x13: "DC3", 0x14: "DC4",
    0x15: "NAK", 0x16: "SYN", 0x17: "ETB", 0x18: "CAN", 0x19: "EM", 0x1A: "SUB",
    0x1B: "ESC", 0x1C: "FS", 0x1D: "GS", 0x1E: "RS", 0x1F: "US", 0x7F: "DEL",
}


def _tables(ctx):
    return ctx.assets.json(TABLES_ASSET)


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def script_of(char):
    cp = ord(char)
    for start, end, name in [
        (0x0041, 0x024F, "Latin"), (0x0370, 0x03FF, "Greek"), (0x0400, 0x04FF, "Cyrillic"),
        (0x0530, 0x058F, "Armenian"), (0x0590, 0x05FF, "Hebrew"), (0x0600, 0x06FF, "Arabic"),
        (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"), (0x0E00, 0x0E7F, "Thai"),
        (0x10A0, 0x10FF, "Georgian"), (0x1100, 0x11FF, "Hangul"), (0x3040, 0x309F, "Hiragana"),
        (0x30A0, 0x30FF, "Katakana"), (0x3400, 0x9FFF, "Han"), (0x1E00, 0x1EFF, "Latin"),
        (0x1F00, 0x1FFF, "Greek"), (0x2000, 0x206F, "Common"), (0x2070, 0x209F, "Common"),
        (0x20A0, 0x20CF, "Common"), (0x2100, 0x214F, "Common"), (0x2190, 0x2BFF, "Common"),
        (0x1F300, 0x1FAFF, "Common (emoji)"),
    ]:
        if start <= cp <= end:
            return name
    if cp < 0x80:
        return "Latin (ASCII)"
    return "Unknown/Common"


def char_report(char):
    cp = ord(char)
    try:
        name = unicodedata.name(char)
    except ValueError:
        name = "<unnamed>"
    report = {
        "char": char,
        "codepoint": "U+%04X" % cp,
        "decimal": cp,
        "name": name,
        "category": unicodedata.category(char),
        "category_name": unicodedata.category(char),
        "combining_class": unicodedata.combining(char),
        "bidi": unicodedata.bidirectional(char),
        "east_asian_width": unicodedata.east_asian_width(char),
        "script": script_of(char),
        "decimal_value": unicodedata.decimal(char, None),
        "numeric_value": unicodedata.numeric(char, None),
        "is_printable": char.isprintable(),
        "is_control": unicodedata.category(char) == "Cc",
        "nfc": unicodedata.normalize("NFC", char),
        "nfd": " ".join("U+%04X" % ord(c) for c in unicodedata.normalize("NFD", char)),
        "utf8": char.encode("utf-8", "backslashreplace").hex(" "),
        "utf16": char.encode("utf-16-be", "backslashreplace").hex(" "),
        "utf32": "%08X" % cp,
        "html": "&#x%X;" % cp,
        "python_escape": char.encode("unicode_escape").decode("ascii"),
        "json_escape": json.dumps(char)[1:-1],
    }
    return report


def skeleton(text, tables=None):
    """Confusable skeleton: fold lookalikes to a canonical ASCII form."""
    out = []
    for char in text:
        if tables and char in tables.get("confusables", {}):
            out.append(tables["confusables"][char])
            continue
        decomposed = unicodedata.normalize("NFKD", char)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        if not base:
            continue
        mapped = "".join(tables.get("confusables", {}).get(c, c) for c in base) if tables else base
        out.append(mapped.casefold())
    return "".join(out)


def invisible_chars_in(text, tables):
    found = []
    for idx, char in enumerate(text):
        cp = ord(char)
        if char in tables["invisible_chars"]:
            meta = tables["invisible_chars"][char]
            found.append({"index": idx, "char": char, "codepoint": "U+%04X" % cp,
                          "name": meta["name"], "risk": meta["risk"], "note": meta["note"]})
        elif unicodedata.category(char) in ("Cf", "Cc", "Co", "Cs") and char not in "\n\r\t":
            found.append({"index": idx, "char": char, "codepoint": "U+%04X" % cp,
                          "name": unicodedata.name(char, "<unnamed>"),
                          "risk": "medium", "note": "format/control character"})
        elif 0xE0000 <= cp <= 0xE007F:
            found.append({"index": idx, "char": char, "codepoint": "U+%04X" % cp,
                          "name": "TAG character", "risk": "critical",
                          "note": "invisible tag characters can encode hidden text"})
    return found


ASCII_FALLBACK = {
    "\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201b": "'", "\u201c": '"',
    "\u201d": '"', "\u201e": '"', "\u2013": "-", "\u2014": "--", "\u2015": "--",
    "\u2026": "...", "\u00a0": " ", "\u00b7": ".", "\u2022": "*", "\u20ac": "EUR",
    "\u00a3": "GBP", "\u00a5": "JPY", "\u00a9": "(c)", "\u00ae": "(R)",
    "\u2122": "(TM)", "\u00b0": " deg", "\u00bd": "1/2", "\u00bc": "1/4",
    "\u00be": "3/4", "\u2192": "->", "\u2190": "<-", "\u2194": "<->", "\u2260": "!=",
    "\u2264": "<=", "\u2265": ">=", "\u00d7": "x", "\u00f7": "/", "\u2212": "-",
    "\u00e9": "e", "\u00e8": "e", "\u00ea": "e", "\u00eb": "e",
}


def ascii_safe(text, mode="fallback", fallback_char="?", keep=""):
    keep_set = set(keep)
    out = []
    for char in text:
        if ord(char) < 128 or char in keep_set:
            out.append(char)
            continue
        if mode == "map" and char in ASCII_FALLBACK:
            out.append(ASCII_FALLBACK[char])
            continue
        if mode in ("fallback", "map"):
            decomposed = unicodedata.normalize("NFKD", char)
            stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
            if stripped and all(ord(c) < 128 for c in stripped):
                out.append(stripped)
                continue
            if char in ASCII_FALLBACK:
                out.append(ASCII_FALLBACK[char])
                continue
            out.append(fallback_char)
            continue
        out.append(fallback_char if mode != "strip" else "")
    return "".join(out)


MOJIBAKE_MARKERS = ["\u00c3", "\u00c2", "\u00e2\u20ac", "\u00ef\u00bf\u00bd", "\u00f0\u0178"]


def detect_mojibake(text, source="auto", max_candidates=6):
    """Return ranked (repaired_text, encoding, score, confidence) candidates."""
    if not any(marker in text for marker in MOJIBAKE_MARKERS) and source == "auto":
        return []
    encodings = ["cp1252", "latin-1", "cp437"] if source == "auto" else [source]
    candidates = []
    for enc in encodings:
        try:
            repaired = text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired == text:
            continue
        markers_before = sum(text.count(m) for m in MOJIBAKE_MARKERS)
        markers_after = sum(repaired.count(m) for m in MOJIBAKE_MARKERS)
        non_ascii_before = sum(1 for c in text if ord(c) > 127)
        non_ascii_after = sum(1 for c in repaired if ord(c) > 127)
        score = (markers_before - markers_after) * 2 - max(0, non_ascii_after - non_ascii_before)
        candidates.append({"text": repaired, "encoding": enc, "score": score,
                           "markers_before": markers_before, "markers_after": markers_after})
    candidates.sort(key=lambda c: -c["score"])
    for cand in candidates:
        cand["confidence"] = ("high" if cand["score"] >= 2 and cand["markers_after"] == 0
                              else "medium" if cand["score"] > 0 else "low")
    return candidates[:max_candidates]


# --------------------------------------------------------------------------
# unicode capabilities
# --------------------------------------------------------------------------


def character_explorer(ctx):
    query = ctx.params["query"].strip() or ctx.text.strip()
    if not query:
        raise core.InvalidInput("give a search term with --set query=... or input text")
    max_results = int(ctx.params["max_results"])
    plane = ctx.params["plane"]
    results = []
    # direct character input: inspect what was typed
    if len(query) <= 4 and not query.isalpha():
        for char in query:
            results.append(char_report(char))
    else:
        needle = query.lower().strip()
        ranges = []
        if plane in ("any", "bmp"):
            ranges.append((0x0, 0x10000))
        if plane in ("any", "smp"):
            ranges.append((0x10000, 0x20000))
        if plane == "any":
            ranges.append((0x20000, 0x40000))
        for start, end in ranges:
            for cp in range(start, end):
                if 0xD800 <= cp <= 0xDFFF:
                    continue
                try:
                    name = unicodedata.name(chr(cp))
                except ValueError:
                    continue
                if needle in name.lower():
                    results.append({"codepoint": "U+%04X" % cp, "char": chr(cp),
                                    "name": name, "category": unicodedata.category(chr(cp))})
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break
    if not results:
        raise core.InvalidInput("no character matched %r" % query,
                                hint="Try a shorter English name fragment, e.g. 'snowman'.")
    if ctx.params["script"]:
        want = ctx.params["script"].lower()
        results = [r for r in results if want in r.get("name", "").lower()
                   or want in script_of(r.get("char", " ")).lower()]
    data = {"query": query, "count": len(results), "results": results}
    rows = [[r.get("codepoint"), r.get("char"), r.get("name"), r.get("category", "")] for r in results]
    output = core.table(rows, ["code point", "char", "name", "cat"])
    return {"output": output, "data": data}


def character_inspector(ctx):
    text = ctx.require_text(minimum=1)
    limit = int(ctx.params["max_chars"])
    chars = text[:limit]
    reports = [char_report(c) for c in chars if c not in "\r\n"]
    data = {"count": len(reports), "characters": reports,
            "normalized_nfc": unicodedata.normalize("NFC", text),
            "normalized_nfkc": unicodedata.normalize("NFKC", text)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    if ctx.params["format"] == "text":
        lines = []
        for rep in reports:
            lines.append("%s  %s  %s  [%s/%s/%s]" % (rep["char"], rep["codepoint"], rep["name"],
                                                     rep["script"], rep["category"], rep["bidi"]))
        return {"output": "\n".join(lines), "data": data}
    rows = [[r["char"], r["codepoint"], r["name"][:38], r["category"], r["bidi"],
             r["utf8"], r["html"]] for r in reports]
    return {"output": core.table(rows, ["char", "code", "name", "cat", "bidi", "utf-8", "html"]),
            "data": data}


def symbol_finder(ctx):
    query = ctx.params["query"].strip() or ctx.text.strip()
    category = ctx.params["category"]
    max_results = int(ctx.params["max_results"])
    tables = _tables(ctx)
    ranges = CURATED_SYMBOL_RANGES.get(category) or [r for rs in CURATED_SYMBOL_RANGES.values() for r in rs]
    results = []
    needle = query.lower()
    for start, end in ranges:
        for cp in range(start, end + 1):
            char = chr(cp)
            try:
                name = unicodedata.name(char)
            except ValueError:
                continue
            if not needle or needle in name.lower() or needle in ("", "all"):
                results.append({"codepoint": "U+%04X" % cp, "char": char, "name": name})
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break
    if not results and query:
        # fall back to the whole BMP so a symbol outside the curated ranges is still findable
        for cp in range(0x20, 0x3000):
            char = chr(cp)
            try:
                name = unicodedata.name(char)
            except ValueError:
                continue
            if needle in name.lower() and unicodedata.category(char).startswith("S"):
                results.append({"codepoint": "U+%04X" % cp, "char": char, "name": name})
                if len(results) >= max_results:
                    break
    if not results:
        raise core.InvalidInput("no symbol matched %r in category %r" % (query, category))
    rows = [[r["char"], r["codepoint"], r["name"]] for r in results]
    return {"output": core.table(rows, ["char", "code", "name"]),
            "data": {"query": query, "category": category, "count": len(results),
                     "results": results}}


def combining_composer(ctx):
    text = ctx.require_text(minimum=1)
    tables = _tables(ctx)
    requested = [m.strip() for m in re.split(r"[,\s]+", ctx.params["marks"]) if m.strip()]
    marks = []
    for item in requested:
        if item in tables["combining_marks"]:
            marks.append(tables["combining_marks"][item])
        elif len(item) == 1 and unicodedata.combining(item):
            marks.append(item)
        else:
            raise core.InvalidParam("unknown combining mark %r" % item,
                                    hint="Known marks: %s" % ", ".join(sorted(tables["combining_marks"])))
    suffix = "".join(marks)
    raw = "".join((char + suffix) if char.isalpha() else char for char in text)
    output = unicodedata.normalize("NFC", raw) if ctx.params["combine_mode"] == "nfc" else raw
    data = {"marks": requested, "combining_sequence": " ".join("U+%04X" % ord(m) for m in marks),
            "raw": raw, "nfc": unicodedata.normalize("NFC", raw),
            "composed_count": sum(1 for c in output if unicodedata.combining(c) == 0)}
    return {"output": output, "data": data,
            "warnings": [] if unicodedata.normalize("NFC", raw) else []}


def normalization_manager(ctx):
    text = ctx.require_text(minimum=1)
    form = ctx.params["form"]
    normal = unicodedata.normalize(form, text)
    changed = []
    if len(normal) != len(text):
        changed.append({"kind": "length", "before": len(text), "after": len(normal)})
    for idx, (a, b) in enumerate(zip(text, normal)):
        if a != b:
            changed.append({"kind": "char", "index": idx,
                            "before": "U+%04X %s" % (ord(a), unicodedata.name(a, "?")),
                            "after": "U+%04X %s" % (ord(b), unicodedata.name(b, "?"))})
            if len(changed) > 20:
                break
    data = {"form": form, "changed": normal != text, "changes": changed,
            "input_sha256": core.sha256_text(text), "output_sha256": core.sha256_text(normal),
            "all_forms": {f: core.sha256_text(unicodedata.normalize(f, text))
                          for f in ("NFC", "NFD", "NFKC", "NFKD")}}
    warnings = [] if normal == text else ["normalization changed %d character(s)" % len(changed)]
    return {"output": normal, "data": data, "warnings": warnings}


def invisible_detector(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _tables(ctx)
    found = invisible_chars_in(text, tables)
    data = {"count": len(found), "characters": found}
    action = ctx.params["action"]
    if action == "strip":
        stripped = "".join(c for c in text if ord(c) not in {ord(f["char"]) for f in found})
        return {"output": stripped, "data": data,
                "warnings": ["stripped %d invisible character(s)" % len(found)]}
    if action == "visualize":
        out = []
        marked = {f["index"] for f in found}
        for idx, char in enumerate(text):
            if idx in marked:
                out.append("[U+%04X]" % ord(char))
            else:
                out.append(char)
        return {"output": "".join(out), "data": data}
    if not found:
        return {"output": "no invisible characters found", "data": data}
    rows = [[f["codepoint"], f["name"], f["risk"], f["index"], f["note"]] for f in found]
    output = core.table(rows, ["code", "name", "risk", "offset", "note"])
    return {"output": output, "data": data,
            "warnings": ["%d invisible or suspicious character(s) found" % len(found)]}


def confusable_auditor(ctx):
    text = ctx.require_text(minimum=1)
    tables = _tables(ctx)
    mode = ctx.params["mode"]
    sus = []
    for idx, char in enumerate(text):
        if char in tables["confusables"]:
            sus.append({"index": idx, "char": char, "codepoint": "U+%04X" % ord(char),
                        "name": unicodedata.name(char, "<unnamed>"),
                        "looks_like": tables["confusables"][char] or "(invisible)",
                        "script": script_of(char)})
    tokens = re.findall(r"\S+", text)
    mixed = []
    for token in tokens:
        scripts = {script_of(c) for c in token if c.isalpha()}
        scripts = {s for s in scripts if not s.startswith("Common")}
        if len(scripts) > 1:
            mixed.append({"token": token, "scripts": sorted(scripts)})
    data = {"suspects": sus, "mixed_script_tokens": mixed,
            "skeleton": skeleton(text, tables)}
    if mode == "skeleton":
        return {"output": data["skeleton"], "data": data}
    if mode == "mixed-script":
        rows = [[m["token"], ", ".join(m["scripts"])] for m in mixed]
        return {"output": core.table(rows, ["token", "scripts"]) if rows else "no mixed-script tokens",
                "data": data}
    rows = [[s["char"], s["codepoint"], s["looks_like"], s["script"], s["name"][:34]] for s in sus]
    output = core.table(rows, ["char", "code", "looks like", "script", "name"]) if rows else "no confusable characters found"
    if mixed:
        output += "\n\nmixed-script tokens: " + ", ".join(m["token"] for m in mixed[:12])
    return {"output": output, "data": data,
            "warnings": ["%d confusable character(s) detected" % len(sus)] if sus else []}


def bidi_specialist(ctx):
    text = ctx.require_text(minimum=1)
    tables = _tables(ctx)
    action = ctx.params["action"]
    controls = []
    classes = {}
    for idx, char in enumerate(text):
        bidi = unicodedata.bidirectional(char) or "BN"
        classes[bidi] = classes.get(bidi, 0) + 1
        if char in tables["invisible_chars"] and bidi in ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"):
            controls.append({"index": idx, "codepoint": "U+%04X" % ord(char),
                             "bidi": bidi, "name": unicodedata.name(char, "?")})
    has_rtl = any(unicodedata.bidirectional(c) in ("R", "AL") for c in text)
    data = {"bidi_classes": classes, "controls": controls, "has_rtl_content": has_rtl,
            "base_direction": ctx.params["base_dir"]}
    if action == "strip":
        out = "".join(c for c in text
                      if unicodedata.bidirectional(c) not in ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"))
        return {"output": out, "data": data,
                "warnings": ["removed %d bidi control(s)" % len(controls)]}
    if action == "isolate":
        out, run = [], []
        for char in text:
            if unicodedata.bidirectional(char) in ("R", "AL", "AN") or unicodedata.combining(char):
                run.append(char)
            else:
                if run:
                    out.append("\u2067" + "".join(run) + "\u2069")
                    run = []
                out.append(char)
        if run:
            out.append("\u2067" + "".join(run) + "\u2069")
        return {"output": "".join(out), "data": data}
    rows = [[c["codepoint"], c["bidi"], c["name"], c["index"]] for c in controls]
    output = core.table(rows, ["code", "bidi", "name", "offset"]) if rows else "no bidi control characters found"
    output += "\n\nclasses: " + ", ".join("%s=%d" % kv for kv in sorted(classes.items()))
    output += "\nRTL content present: %s" % ("yes" if has_rtl else "no")
    warnings = []
    if any(c["bidi"] in ("RLO", "LRO", "RLE", "LRE") for c in controls):
        warnings.append("embedding/override controls present: review for Trojan Source patterns")
    return {"output": output, "data": data, "warnings": warnings}


def pua_advisor(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _tables(ctx)
    found = []
    for idx, char in enumerate(text):
        cp = ord(char)
        for start, end, label, note in tables["pua_ranges"]:
            if start <= cp <= end:
                found.append({"index": idx, "char": char, "codepoint": "U+%04X" % cp,
                              "range": label, "note": note})
                break
    data = {"count": len(found), "characters": found}
    if ctx.params["action"] == "replace":
        repl = ctx.params["replacement"] or "\ufffd"
        out = "".join(repl if any(f["index"] == i for f in found) else c for i, c in enumerate(text))
        return {"output": out, "data": data}
    if not found:
        return {"output": "no private-use characters found", "data": data,
                "warnings": ["private-use characters render inconsistently across fonts"]}
    rows = [[f["codepoint"], f["char"], f["range"], "U+%04X" % (ord(f["char"]) - 0xE000 + 0x41), f["note"]]
            for f in found]
    return {"output": core.table(rows, ["code", "char", "range", "icon-font guess", "note"]),
            "data": data,
            "warnings": ["%d private-use character(s): text will not render without the source font" % len(found)]}


def escape_converter(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    target = ctx.params["to"]
    direction = ctx.params["direction"]
    encoding = ctx.params["encoding"]
    if direction == "decode":
        decoded = text
        decoded = re.sub(r"\\u\{([0-9a-fA-F]{1,6})\}", lambda m: chr(int(m.group(1), 16)), decoded)
        decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), decoded)
        decoded = re.sub(r"\\U([0-9a-fA-F]{8})", lambda m: chr(int(m.group(1), 16)), decoded)
        decoded = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), decoded)
        decoded = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), decoded)
        decoded = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), decoded)
        decoded = re.sub(r"\\N\{([^}]+)\}", lambda m: _char_by_name(m.group(1)), decoded)
        return {"output": decoded, "data": {"direction": "decode", "length": len(decoded)}}
    out = []
    if target == "escape-u":
        out = ["\\u%04x" % ord(c) if ord(c) <= 0xFFFF else "\\U%08x" % ord(c) for c in text]
    elif target == "escape-x":
        out = ["\\x%02x" % ord(c) if ord(c) < 0x100 else "\\u%04x" % ord(c) for c in text]
    elif target == "html-entity":
        out = ["&#x%X;" % ord(c) if ord(c) > 127 or c in "<>&\"'" else c for c in text]
    elif target == "names":
        out = ["\\N{%s}" % unicodedata.name(c, "?") if ord(c) > 127 else c for c in text]
    elif target == "bytes":
        out = [text.encode(encoding, "backslashreplace").hex(" ")]
        return {"output": out[0], "data": {"direction": "encode", "encoding": encoding}}
    else:  # python
        out = [text.encode("unicode_escape").decode("ascii")]
        return {"output": out[0], "data": {"direction": "encode", "target": target}}
    output = "".join(out)
    return {"output": output, "data": {"direction": "encode", "target": target,
                                       "input_chars": len(text), "output_len": len(output)}}


def _char_by_name(name):
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return "\\N{%s}" % name


def font_coverage(ctx):
    text = ctx.text or ""
    scope = ctx.params["scope"]
    glyphs = {}
    for char in text:
        if char.isspace():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            name = "<unnamed>"
        glyphs.setdefault(char, {"char": char, "codepoint": "U+%04X" % ord(char),
                                 "name": name, "script": script_of(char),
                                 "east_asian_width": unicodedata.east_asian_width(char),
                                 "count": 0})
        glyphs[char]["count"] += 1
    fonts = []
    if ctx.params["probe_system_fonts"] and shutil.which("fc-list"):
        try:
            proc = subprocess.run(["fc-list", "--format", "%{family}\n"], capture_output=True,
                                  text=True, timeout=5)
            fonts = sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()})[:40]
        except (OSError, subprocess.SubprocessError) as exc:
            ctx.warn("font probing failed: %s" % exc)
    required = list(glyphs.values()) if scope == "glyph-set" else [g for g in glyphs.values() if g["count"] > 0]
    risky = [g for g in required if unicodedata.east_asian_width(g["char"]) in ("W", "F")
             or unicodedata.category(g["char"]).startswith("So")
             or 0xE000 <= ord(g["char"]) <= 0xF8FF]
    data = {"glyph_count": len(required), "glyphs": sorted(required, key=lambda g: -g["count"])[:200],
            "risky_glyphs": risky[:80], "system_fonts": fonts,
            "font_probe": bool(fonts)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Font coverage plan", ""]
    lines.append("- distinct glyphs required: %d" % len(required))
    lines.append("- scripts involved: %s" % ", ".join(sorted({g["script"] for g in required})))
    lines.append("- glyphs with rendering risk: %d" % len(risky))
    if risky:
        lines.append("")
        lines.append("| char | code | name | risk |")
        lines.append("| --- | --- | --- | --- |")
        for glyph in risky[:25]:
            lines.append("| %s | %s | %s | wide/symbol/PUA |" % (glyph["char"], glyph["codepoint"], glyph["name"][:40]))
    lines.append("")
    if fonts:
        lines.append("System fonts detected (%d): %s" % (len(fonts), ", ".join(fonts[:12])))
    else:
        lines.append("No font inventory was probed (set probe_system_fonts=true and install fontconfig to list local fonts).")
    lines.append("Plan: ship a web font subset covering the glyph list, or provide an ASCII fallback for every risky glyph.")
    return {"output": "\n".join(lines), "data": data}


def mojibake_repair(ctx):
    text = ctx.require_text(minimum=1)
    candidates = detect_mojibake(text, ctx.params["source_encoding"], int(ctx.params["max_candidates"]))
    if not candidates:
        data = {"detected": False, "candidates": []}
        return {"output": text, "data": data,
                "warnings": ["no mojibake pattern detected; text returned unchanged"]}
    best = candidates[0]
    data = {"detected": True, "candidates": [{k: v for k, v in c.items() if k != "text"} for c in candidates]}
    output = best["text"] if ctx.params["apply"] else json.dumps(
        {"candidates": [{"encoding": c["encoding"], "confidence": c["confidence"],
                         "preview": c["text"][:200]} for c in candidates]}, indent=2, ensure_ascii=False)
    return {"output": output, "data": data,
            "warnings": ["repair confidence: %s (encoding %s)" % (best["confidence"], best["encoding"])]}


# --------------------------------------------------------------------------
# legacy charset capabilities
# --------------------------------------------------------------------------


def _parse_codepoints(spec, default=(32, 127)):
    spec = (spec or "").strip()
    if not spec:
        return list(range(default[0], default[1] + 1))
    values = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            values.extend(range(int(start, 0), int(end, 0) + 1))
        else:
            values.append(int(part, 0))
    if len(values) > 4096:
        raise core.InvalidParam("codepoint range too large", details={"count": len(values)})
    return values


def ascii_table(ctx):
    values = _parse_codepoints(ctx.params["codepoints"])
    rows = []
    for cp in values:
        char = chr(cp)
        printable = char if char.isprintable() else ""
        rows.append([cp, "%02X" % cp, "%03o" % cp, format(cp, "08b"),
                     printable, _CONTROL_NAMES.get(cp, unicodedata.name(char, "<unnamed>"))])
    fmt = ctx.params["format"]
    headers = ["dec", "hex", "oct", "bin", "char", "name"]
    if fmt == "json":
        output = json.dumps([dict(zip(headers, r)) for r in rows], indent=2)
    elif fmt == "csv":
        output = "\n".join(",".join('"%s"' % str(c) for c in r) for r in [headers] + rows)
    elif fmt == "markdown":
        output = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in [headers] + rows)
        output = output.replace("| dec | hex | oct | bin | char | name |",
                                "| dec | hex | oct | bin | char | name |\n| --- | --- | --- | --- | --- | --- |")
    else:
        output = core.table(rows, headers)
    return {"output": output, "data": {"count": len(rows), "range": ctx.params["codepoints"]}}


def extended_ascii_clarifier(ctx):
    spec = ctx.params["codepoint"].strip()
    if not spec:
        raise core.InvalidInput("give --set codepoint=128-255 or a single value like 0x80")
    values = _parse_codepoints(spec)
    encodings = (["cp437", "cp1252", "latin-1", "cp850", "mac_roman"]
                 if ctx.params["encoding"] == "all" else [ctx.params["encoding"].replace("-", "_")])
    rows = []
    for cp in values:
        row = [cp, "0x%02X" % cp]
        for enc in encodings:
            try:
                row.append(bytes([cp]).decode(enc))
            except (UnicodeDecodeError, LookupError):
                row.append("\u2014")
        rows.append(row)
    output = core.table(rows, ["dec", "hex"] + encodings)
    note = ("Extended ASCII is not one standard: bytes 128-255 mean different characters in "
            "cp437 (DOS), cp1252 (Windows), ISO-8859-1 and Mac OS Roman.")
    return {"output": output + "\n\n" + note,
            "data": {"values": values, "encodings": encodings}}


def control_char_interpreter(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    found = []
    for idx, char in enumerate(text):
        cp = ord(char)
        if cp < 0x20 and char not in "\n\t" or cp == 0x7F or 0x80 <= cp <= 0x9F:
            found.append({"index": idx, "codepoint": "U+%04X" % cp,
                          "name": _CONTROL_NAMES.get(cp, unicodedata.name(char, "control")),
                          "hex": "%02X" % cp})
    action = ctx.params["action"]
    if action == "strip":
        out = "".join(c for c in text if ord(c) < 0x20 and c in "\n\t" or ord(c) >= 0x20 and not (0x7F <= ord(c) <= 0x9F))
        return {"output": out, "data": {"removed": len(found)}}
    if action == "expand":
        out = []
        for char in text:
            cp = ord(char)
            if cp < 0x20 and char in "\n\t":
                out.append({"10": "\\n", "9": "\\t"}.get(str(cp), char))
            elif cp < 0x20 or cp == 0x7F or 0x80 <= cp <= 0x9F:
                out.append("<%s>" % _CONTROL_NAMES.get(cp, "U+%04X" % cp))
            else:
                out.append(char)
        return {"output": "".join(out), "data": {"expanded": len(found)}}
    if not found:
        return {"output": "no control characters found", "data": {"count": 0}}
    rows = [[f["codepoint"], f["hex"], f["name"], f["index"]] for f in found]
    return {"output": core.table(rows, ["code", "hex", "name", "offset"]),
            "data": {"count": len(found), "characters": found},
            "warnings": ["%d control character(s) present" % len(found)]}


def alt_code_guide(ctx):
    chars = ctx.params["chars"].strip() or ctx.text.strip()
    if not chars:
        raise core.InvalidInput("give characters to look up (--set chars=£,→ or as input text)")
    limit = int(ctx.params["limit"])
    rows = []
    for char in chars[:limit]:
        if char.isspace():
            continue
        cp = ord(char)
        windows_alt = ""
        for enc in ("cp1252", "cp437"):
            try:
                byte = char.encode(enc)
            except UnicodeEncodeError:
                continue
            if len(byte) == 1:
                windows_alt = "Alt+0%03d (%s)" % (byte[0], enc)
                break
        rows.append([char, "U+%04X" % cp, windows_alt or "Alt+%d (Unicode)" % cp,
                     "Alt+%d (Unicode)" % cp])
    note = ("Windows Alt codes: the 0-prefixed three digit form uses the ANSI code page "
            "(cp1252) or the OEM/DOS code page (cp437); the decimal Unicode form works in "
            "modern Windows applications.")
    return {"output": core.table(rows, ["char", "code point", "Windows Alt code", "Unicode Alt code"])
            + "\n\n" + note,
            "data": {"count": len(rows), "rows": rows}}


def keyboard_mapper(ctx):
    tables = _tables(ctx)
    query = ctx.params["query"].strip() or ctx.text.strip()
    platform = ctx.params["platform"]
    entries = tables["keyboard_shortcuts"]
    if query:
        lowered = query.lower()
        entries = [e for e in entries
                   if lowered in e["symbol"].lower() or lowered in e["name"].lower()
                   or any(lowered in str(e.get(k, "")).lower()
                          for k in ("windows", "macos", "linux", "emacs", "vim"))]
    if not entries:
        raise core.InvalidInput("no shortcut matched %r" % query,
                                hint="Try a symbol name such as 'em dash' or 'degree'.")
    rows = [[e["symbol"], e["name"], e.get(platform, "")] for e in entries]
    return {"output": core.table(rows, ["symbol", "name", platform]),
            "data": {"count": len(entries), "platform": platform, "entries": entries}}


def codepage_converter(ctx):
    source = ctx.params["from_encoding"].strip()
    target = ctx.params["to_encoding"].strip()
    text = ctx.require_text(minimum=1, allow_empty=True)
    errors = ctx.params["errors"]
    if ctx.params["direction"] == "encode":
        try:
            raw = text.encode(source if source else "utf-8")
        except (UnicodeEncodeError, LookupError) as exc:
            raise core.InvalidInput("cannot encode with %s: %s" % (source, exc))
        try:
            output = raw.decode(target)
        except UnicodeDecodeError as exc:
            raise core.InvalidInput("bytes are not valid %s: %s" % (target, exc))
        return {"output": output, "data": {"bytes": raw.hex(" "), "from": source, "to": target}}
    data = text.encode("latin-1", "replace") if all(ord(c) < 256 for c in text) else text.encode("utf-8")
    handler = {"strict": "strict", "replace": "replace", "ignore": "ignore"}.get(errors)
    if errors == "transliterate":
        prepared = ascii_safe(text, mode="fallback", fallback_char="?")
    else:
        prepared = text
    try:
        output = prepared.encode(source, handler or "strict").decode(target, handler or "strict")
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError) as exc:
        raise core.InvalidInput("conversion %s -> %s failed: %s" % (source, target, exc),
                                hint="Use --set errors=transliterate to fall back to ASCII.")
    lost = sorted({c for c in text if c not in output})
    return {"output": output,
            "data": {"from": source, "to": target, "errors": errors,
                     "unrepresentable": lost[:40]},
            "warnings": ["%d character(s) could not be represented and were substituted"
                         % len(lost)] if lost else []}


def ebcdic_assistant(ctx):
    codepage = ctx.params["codepage"]
    codec = {"cp037": "cp037", "cp500": "cp500", "cp1047": "cp500"}[codepage]
    value = ctx.params["value"].strip() or ctx.text.strip()
    if not value:
        raise core.InvalidInput("give --set value=... (hex bytes when decoding, text when encoding)")
    if ctx.params["direction"] == "decode":
        hex_bytes = re.sub(r"[^0-9A-Fa-f]", "", value)
        if len(hex_bytes) % 2:
            raise core.InvalidInput("hex input must have an even number of digits")
        raw = bytes.fromhex(hex_bytes)
        output = raw.decode(codec, "replace")
        data = {"bytes": raw.hex(" "), "decoded": output, "codepage": codepage}
    else:
        raw = value.encode(codec)
        output = raw.hex(" ")
        data = {"text": value, "encoded": output, "codepage": codepage}
    return {"output": output, "data": data}


ANSI_CODES = {
    "reset": 0, "bold": 1, "dim": 2, "italic": 3, "underline": 4, "blink": 5,
    "reverse": 7, "hidden": 8, "strike": 9,
}
ANSI_COLORS = {"black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34,
               "magenta": 35, "cyan": 36, "white": 37, "default": 39}


def terminal_escape(ctx):
    action = ctx.params["action"]
    if action == "catalog":
        rows = [["SGR %d" % v, "\\x1b[%dm" % v, k] for k, v in ANSI_CODES.items()]
        rows += [["foreground %d" % v, "\\x1b[%dm" % v, k] for k, v in ANSI_COLORS.items()]
        rows += [["background %d" % (v + 10), "\\x1b[%dm" % (v + 10), k] for k, v in ANSI_COLORS.items()]
        rows += [["256 colour", "\\x1b[38;5;Nm", "N = 0-255"], ["truecolour", "\\x1b[38;2;R;G;Bm", "RGB"]]
        return {"output": core.table(rows, ["name", "sequence", "value"]),
                "data": {"codes": ANSI_CODES, "colours": ANSI_COLORS}}
    text = ctx.require_text(minimum=1, allow_empty=True)
    if action == "sanitize":
        cleaned = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
        cleaned = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", cleaned)
        return {"output": cleaned,
                "data": {"removed_sequences": len(text) - len(cleaned)}}
    codes = []
    if ctx.params["fg"] != "default":
        if ctx.params["fg"] == "256":
            codes.append("38;5;208")
        else:
            codes.append(str(ANSI_COLORS.get(ctx.params["fg"], 39)))
    if ctx.params["bg"] != "default":
        codes.append(str(ANSI_COLORS.get(ctx.params["bg"], 49) + 10))
    for attr in ctx.params["attrs"]:
        if attr in ANSI_CODES:
            codes.append(str(ANSI_CODES[attr]))
    seq = "\x1b[" + ";".join(codes) + "m" if codes else ""
    output = "%s%s\x1b[0m" % (seq, text)
    data = {"sequence_escaped": seq.encode("unicode_escape").decode("ascii"),
            "codes": codes, "text": text}
    return {"output": output, "data": data,
            "warnings": ["escape sequences must be printed to a terminal; captured output shows raw bytes"]}


def ascii_transliterator(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    mode = ctx.params["mode"]
    output = ascii_safe(text, mode=mode, fallback_char=ctx.params["fallback_char"] or "?",
                        keep=ctx.params["keep"])
    replaced = sum(1 for a, b in zip(text, output) if a != b)
    return {"output": output,
            "data": {"mode": mode, "input_chars": len(text), "output_chars": len(output),
                     "substitutions": replaced},
            "warnings": ["%d character(s) were transliterated" % replaced] if replaced and mode != "map" else []}


def newline_normalizer(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    newline = ctx.params["newline"]
    target = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}.get(newline)
    working = text
    if target:
        working = core.canonical_newlines(working)
    tabstop = int(ctx.params["tabstop"])
    if "\t" in working:
        lines = []
        for line in working.split("\n"):
            expanded, col = [], 0
            for char in line:
                if char == "\t":
                    spaces = tabstop - (col % tabstop)
                    expanded.append(" " * spaces)
                    col += spaces
                else:
                    expanded.append(char)
                    col += 1
            lines.append("".join(expanded))
        working = "\n".join(lines)
    if ctx.params["trim_trailing"]:
        working = "\n".join(line.rstrip() for line in working.split("\n"))
    if target:
        working = working.replace("\n", target)
    if ctx.params["ensure_final_newline"] and not working.endswith(("\n", "\r")):
        working += target or "\n"
    return {"output": working,
            "data": {"newline": newline, "tabstop": tabstop,
                     "crlf_before": text.count("\r\n"), "lf_before": text.count("\n"),
                     "tabs_before": text.count("\t")}}


def byte_visualizer(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    encoding = ctx.params["encoding"]
    width = int(ctx.params["width"])
    max_bytes = int(ctx.params["max_bytes"])
    try:
        raw = text.encode(encoding, "backslashreplace")
    except LookupError:
        raise core.InvalidParam("unknown encoding %r" % encoding)
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    lines = []
    for offset in range(0, len(raw), width):
        chunk = raw[offset:offset + width]
        hex_part = " ".join("%02X" % b for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%08X  %-*s  |%s|" % (offset, width * 3 - 1, hex_part, ascii_part))
    data = {"encoding": encoding, "bytes": len(raw), "truncated": truncated,
            "first_bytes": raw[:16].hex(" ")}
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["byte dump truncated at %d bytes" % max_bytes] if truncated else []}


def char_table_planner(ctx):
    tables = _tables(ctx)
    standard = ctx.params["standard"]
    mapping = {
        "unicode": ["unicode", "uax44", "uax15", "uax39"],
        "ascii": ["unicode"],
        "cp1252": ["unicode"],
        "emoji": ["emoji", "tr51", "unicode"],
        "iso-8859-1": ["unicode", "iso639"],
    }
    ids = mapping.get(standard, ["unicode"])
    entries = [s for s in tables["standards"] if s["id"] in ids]
    data = {"standard": standard, "sources": entries,
            "network_required": True,
            "offline_tables": ["ASCII 0-127", "cp1252 and cp437 sample values",
                               "confusables", "combining marks", "transliteration"]}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Character table retrieval plan: %s" % standard, ""]
    lines.append("The Skill Tool performs no network access. Use the authoritative sources below,")
    lines.append("then feed the downloaded table back through the local capabilities.")
    lines.append("")
    for entry in entries:
        lines.append("- **%s** (%s) - %s" % (entry["name"], entry["org"], entry["url"]))
        lines.append("  - scope: %s" % entry["scope"])
    lines.append("")
    lines.append("Offline data already available in this Skill Tool: %s" % ", ".join(data["offline_tables"]))
    lines.append("")
    lines.append("Suggested sequence:")
    lines.append("1. Download the table (UnicodeData.txt or CodeCharts) once and store it next to your inputs.")
    lines.append("2. Run `run legacy-charsets.ascii-table-reference` for the ASCII subset.")
    lines.append("3. Run `run unicode.unicode-character-explorer --set query=<name>` for name lookups.")
    lines.append("4. Re-verify any version-sensitive statement against the version you downloaded.")
    return {"output": "\n".join(lines), "data": data}


HANDLERS = {
    "unicode.unicode-character-explorer": character_explorer,
    "unicode.character-identity-inspector": character_inspector,
    "unicode.symbol-and-glyph-finder": symbol_finder,
    "unicode.combining-mark-composer": combining_composer,
    "unicode.unicode-normalization-manager": normalization_manager,
    "unicode.invisible-character-detector": invisible_detector,
    "unicode.confusable-and-homoglyph-auditor": confusable_auditor,
    "unicode.bidirectional-text-specialist": bidi_specialist,
    "unicode.private-use-character-advisor": pua_advisor,
    "unicode.unicode-escape-converter": escape_converter,
    "unicode.font-coverage-planner": font_coverage,
    "unicode.mojibake-repair-assistant": mojibake_repair,
    "legacy-charsets.ascii-table-reference": ascii_table,
    "legacy-charsets.extended-ascii-clarifier": extended_ascii_clarifier,
    "legacy-charsets.control-character-interpreter": control_char_interpreter,
    "legacy-charsets.alt-code-and-input-shortcut-guide": alt_code_guide,
    "legacy-charsets.keyboard-symbol-mapper": keyboard_mapper,
    "legacy-charsets.code-page-converter": codepage_converter,
    "legacy-charsets.ebcdic-reference-assistant": ebcdic_assistant,
    "legacy-charsets.terminal-escape-sequence-author": terminal_escape,
    "legacy-charsets.ascii-safe-transliterator": ascii_transliterator,
    "legacy-charsets.newline-and-tab-normalizer": newline_normalizer,
    "legacy-charsets.byte-level-text-visualizer": byte_visualizer,
    "legacy-charsets.character-table-fetch-planner": char_table_planner,
}
