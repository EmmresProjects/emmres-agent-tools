"""Notation capabilities (12 handlers): maths, logic, IPA, music, chess, braille.

Notation tables live in asset-unicode-tables.json. Braille uses a grade-1
English table defined here; other languages and grade 2 contractions are out
of scope and reported as such.
"""

from __future__ import annotations

import json
import re

EXPORTS = ["BRAILLE", "braille_encode", "braille_decode", "transpose_note"]
UNI_ASSET = "asset-unicode-tables.json"
ART_ASSET = "asset-art-tables.json"

BRAILLE_LETTERS = {
    "a": 1, "b": 12, "c": 14, "d": 145, "e": 15, "f": 124, "g": 1245, "h": 125,
    "i": 24, "j": 245, "k": 13, "l": 123, "m": 134, "n": 1345, "o": 135, "p": 1234,
    "q": 12345, "r": 1235, "s": 234, "t": 2345, "u": 136, "v": 1236, "w": 2456,
    "x": 1346, "y": 13456, "z": 1356,
}
BRAILLE_DIGITS = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e",
                  "6": "f", "7": "g", "8": "h", "9": "i", "0": "j"}
BRAILLE_PUNCT = {".": 256, ",": 2, ";": 23, ":": 25, "?": 26, "!": 235, "'": 3,
                 "-": 36, "(": 2356, ")": 2356, "/": 34, "\"": 2356}
DOT_VALUES = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x08, 5: 0x10, 6: 0x20}
BRAILLE = BRAILLE_LETTERS


def braille_encode(text, six_dot=True):
    """Encode text as grade-1 English braille cells (U+2800 block)."""
    out = []
    number_mode = False
    for char in text:
        lowered = char.lower()
        if char == "\n":
            number_mode = False
            out.append("\n")
            continue
        if char == " ":
            number_mode = False
            out.append(" ")
            continue
        if lowered in BRAILLE_DIGITS:                      # digits
            dots = BRAILLE_LETTERS[BRAILLE_DIGITS[lowered]]
            if not number_mode:
                out.append(chr(0x2800 + 0x3C))             # number sign
                number_mode = True
        elif lowered in BRAILLE_LETTERS:                   # letters
            number_mode = False
            dots = BRAILLE_LETTERS[lowered]
        elif char in BRAILLE_PUNCT:                        # punctuation
            number_mode = False
            dots = BRAILLE_PUNCT[char]
        else:
            number_mode = False
            out.append("\ufffd")
            continue
        value = 0
        for dot in str(dots):
            value |= DOT_VALUES[int(dot)]
        out.append(chr(0x2800 + value))
    return "".join(out)


def braille_decode(text):
    reverse = {}
    for letter, dots in BRAILLE_LETTERS.items():
        value = 0
        for dot in str(dots):
            value |= DOT_VALUES[int(dot)]
        reverse[value] = letter
    for punct, dots in BRAILLE_PUNCT.items():
        value = 0
        for dot in str(dots):
            value |= DOT_VALUES[int(dot)]
        reverse.setdefault(value, punct)
    out, number_mode = [], False
    reverse_digits = {reverse_value: digit for digit, letter in BRAILLE_DIGITS.items()
                      for reverse_value, l in reverse.items() if l == letter}
    for char in text:
        if char == " " or char == "\n":
            number_mode = False
            out.append(char)
            continue
        code = ord(char) - 0x2800
        if code == 0x3C:
            number_mode = True
            continue
        if code not in reverse:
            out.append("?")
            continue
        if number_mode and code in reverse_digits:
            out.append(reverse_digits[code])
        else:
            out.append(reverse[code])
    return "".join(out)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def transpose_note(note, semitones):
    match = re.match(r"^([A-G])([#b]?)(-?\d)?$", note)
    if not match:
        return None
    base = match.group(1)
    accidental = match.group(2)
    octave = match.group(3)
    if accidental == "#":
        base += "#"
    elif accidental == "b":
        base = NOTE_NAMES[(NOTE_NAMES.index(base) - 1) % 12]
    index = NOTE_NAMES.index(base)
    new_index = (index + semitones) % 12
    return NOTE_NAMES[new_index] + (octave or "")


def _symbol_table(ctx):
    return ctx.assets.json(UNI_ASSET)


def math_notation(ctx):
    tables = _symbol_table(ctx)
    text = ctx.text.strip()
    direction = ctx.params["direction"]
    style = ctx.params["style"]
    latex = tables["math_latex"]
    reverse = {v: k for k, v in latex.items()}
    if direction == "to-unicode":
        if not text:
            raise core.InvalidInput("supply LaTeX such as --text '\\alpha + \\beta'")
        out = text
        for command in sorted(latex, key=len, reverse=True):
            out = re.sub(re.escape(command) + r"(?![A-Za-z])", latex[command], out)
        out = re.sub(r"\^\{(.*?)\}", lambda m: "".join(tables["superscripts"].get(c, c) for c in m.group(1)), out)
        out = re.sub(r"\^(\w)", lambda m: tables["superscripts"].get(m.group(1), "^" + m.group(1)), out)
        out = re.sub(r"_\{(.*?)\}", lambda m: "".join(tables["subscripts"].get(c, c) for c in m.group(1)), out)
        out = out.replace("\\times", "×").replace("\\cdot", "·").replace("\\pm", "±")
        if style == "spaced":
            out = re.sub(r"(?<=.)([=+<>])(?=.)", r" \1 ", out)
            out = re.sub(r"\s{2,}", " ", out)
        used = [c for c in latex if c in text]
        return {"output": out, "data": {"direction": direction, "commands_used": used,
                                        "style": style, "supported_commands": len(latex)}}
    if not text:
        raise core.InvalidInput("supply Unicode maths such as --text 'α + β'")
    out = text
    for symbol, command in sorted(reverse.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(symbol, command + " ")
    data = {"direction": "to-latex", "style": style, "replaced": len([s for s in reverse if s in text])}
    return {"output": re.sub(r"\s{2,}", " ", out).strip(), "data": data,
            "warnings": ["round-tripping is lossy for nested superscripts and custom macros"]}


def scientific_symbols(ctx):
    tables = _symbol_table(ctx)
    query = ctx.params["query"].strip() or ctx.text.strip()
    action = ctx.params["action"]
    direction = ctx.params["direction"]
    pool = {}
    pool.update({k: v for k, v in tables["math_latex"].items()})
    pool.update({("<-%s" % k): v for k, v in tables["superscripts"].items()})
    pool.update({("_%s" % k): v for k, v in tables["subscripts"].items()})
    if action == "lookup":
        if not query:
            raise core.InvalidInput("give --set query=greek or a LaTeX name like \\mu")
        needle = query.lower().lstrip("\\")
        matches = {k: v for k, v in pool.items() if needle in k.lower().lstrip("\\_<")}
        if not matches:
            raise core.InvalidInput("no symbol matched %r" % query,
                                    hint="Try 'alpha', 'sum', 'subscript 2' or 'superscript n'.")
        rows = [[k, v, "U+%04X" % ord(v)] for k, v in sorted(matches.items())[:40]]
        return {"output": core.table(rows, ["name", "symbol", "code point"]),
                "data": {"query": query, "matches": len(matches)}}
    if action == "ascii":
        out = ctx.text
        reverse = {v: k for k, v in tables["math_latex"].items()}
        for symbol, command in reverse.items():
            out = out.replace(symbol, command)
        return {"output": out, "data": {"direction": "to-ascii"}}
    rows = [[k, v] for k, v in sorted(pool.items())]
    return {"output": core.table(rows, ["name", "symbol"]),
            "data": {"count": len(rows), "direction": direction}}


def logic_notation(ctx):
    tables = _symbol_table(ctx)
    action = ctx.params["action"]
    direction = ctx.params["direction"]
    expression = ctx.params["expression"].strip() or ctx.text.strip()
    symbols = tables["logic_symbols"]
    ascii_map = tables["logic_ascii"]
    reverse = {v: k for k, v in symbols.items()}
    if action == "list":
        rows = [[name, symbol, "U+%04X" % ord(symbol)] for name, symbol in sorted(symbols.items())]
        return {"output": core.table(rows, ["name", "symbol", "code point"]),
                "data": {"count": len(rows)}}
    if not expression:
        raise core.InvalidInput("supply an expression, e.g. --set expression='A and B implies C'")
    if direction == "to-unicode" and action == "convert":
        out = expression
        phrases = {"not in": "∉", "in": "∈", "is a subset of": "⊆", "is an element of": "∈"}
        for phrase, symbol in sorted(phrases.items(), key=lambda kv: -len(kv[0])):
            out = re.sub(r"\b%s\b" % re.escape(phrase), symbol, out)
        for token in sorted(ascii_map, key=len, reverse=True):
            out = re.sub(r"(?<![\w])%s(?![\w])" % re.escape(token), symbols.get(ascii_map[token], token), out)
        for name, symbol in sorted(symbols.items(), key=lambda kv: -len(kv[0])):
            out = re.sub(r"\b%s\b" % re.escape(name), symbol, out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        return {"output": out, "data": {"direction": direction, "tokens": len(expression.split())}}
    if action == "parse":
        found = [{"symbol": s, "name": n} for n, s in symbols.items() if s in expression]
        unknown = [t for t in re.findall(r"[^\s\w]+", expression) if t not in reverse
                   and t not in [s for s in symbols.values()]]
        return {"output": core.table([[f["symbol"], f["name"]] for f in found], ["symbol", "name"])
                if found else "no logic symbols recognised",
                "data": {"found": found, "unknown_tokens": unknown}}
    out = expression
    for symbol, name in reverse.items():
        out = out.replace(symbol, name)
    return {"output": out, "data": {"direction": "to-words"}}


IPA_COMMON = {
    "th": "θ", "sh": "ʃ", "ch": "tʃ", "zh": "ʒ", "ng": "ŋ", "ee": "iː",
    "oo": "uː", "ay": "eɪ", "igh": "aɪ", "ow": "aʊ", "oi": "ɔɪ", "ar": "ɑː",
    "er": "ɜː", "or": "ɔː", "air": "eə", "ear": "ɪə", "y": "j", "ph": "f",
    "qu": "kw", "x": "ks", "c": "k", "a": "æ", "e": "e", "i": "ɪ", "o": "ɒ", "u": "ʌ",
}
X_SAMPA = {"θ": "T", "ʃ": "S", "ʒ": "Z", "ŋ": "N", "tʃ": "tS", "dʒ": "dZ", "iː": "i:",
           "uː": "u:", "eɪ": "eI", "aɪ": "aI", "aʊ": "aU", "ɔɪ": "OI", "ɑː": "A:",
           "ɜː": "3:", "ɔː": "O:", "ə": "@", "æ": "{", "ɒ": "Q", "ʌ": "V", "ɪ": "I", "ʊ": "U"}


def ipa_editor(ctx):
    text = ctx.require_text(minimum=1)
    action = ctx.params["action"]
    scheme = ctx.params["scheme"]
    if action == "describe":
        descriptions = []
        for char in text:
            if char in {v for v in IPA_COMMON.values()}:
                descriptions.append(char)
        rows = [[symbol, code] for symbol, code in X_SAMPA.items() if symbol in text]
        return {"output": core.table(rows, ["IPA", "X-SAMPA"]), "data": {"symbols": len(rows)}}
    if action == "from-ipa":
        out = text
        if scheme == "xsampa":
            for symbol, code in sorted(X_SAMPA.items(), key=lambda kv: -len(kv[0])):
                out = out.replace(symbol, code)
        return {"output": out, "data": {"scheme": scheme, "direction": "from-ipa"}}
    words = re.findall(r"[A-Za-z']+", text.lower())
    converted = []
    for word in words:
        out = word
        for english, ipa in sorted(IPA_COMMON.items(), key=lambda kv: -len(kv[0])):
            out = out.replace(english, ipa)
        converted.append("/%s/" % out)
    result = " ".join(converted)
    if scheme == "xsampa" and action == "to-ipa":
        result += "\n\nX-SAMPA: " + " ".join(
            "/" + "".join(X_SAMPA.get(c, c) for c in part.strip("/")) + "/" for part in converted)
    data = {"action": action, "scheme": scheme, "words": len(words),
            "note": "respelling-based approximation, not a phonemic dictionary"}
    return {"output": result, "data": data,
            "warnings": ["IPA output is rule-based from English spelling and is approximate; "
                         "verify against a dictionary for anything published"]}


def music_editor(ctx):
    text = ctx.text.strip()
    action = ctx.params["action"]
    key = ctx.params["key"]
    semitones = int(ctx.params["semitones"])
    if action == "transpose":
        if not text:
            raise core.InvalidInput("give chords in the text, e.g. C G Am F")
        tokens = re.findall(r"[A-G][#b]?m?(?:7|maj7|sus4)?", text)
        transposed = []
        for token in tokens:
            root = token[0] + ("#" if "#" in token else "b" if "b" in token else "")
            rest = token[len(root):]
            moved = transpose_note(root, semitones)
            transposed.append((moved or root) + rest)
        out = text
        for old, new in zip(tokens, transposed):
            out = out.replace(old, new, 1)
        return {"output": out, "data": {"action": action, "from_key": key, "semitones": semitones,
                                        "chords": list(zip(tokens, transposed))},
                "warnings": ["chord quality suffixes are preserved without re-analysis of the key"]}
    if action == "format":
        if not text:
            raise core.InvalidInput("give chord text such as 'C G Am F'")
        sections = [s.strip() for s in re.split(r"\n\s*\n", core.canonical_newlines(text)) if s.strip()]
        lines = []
        for idx, section in enumerate(sections):
            bars = [s.strip() for s in section.split("|")]
            lines.append("  ".join("%-6s" % bar for bar in bars[:4]).rstrip())
            lines.append("")
        rows = []
        for token in re.findall(r"[A-G][#b]?(?:m|maj|min|dim|aug|sus)?\d*", text):
            if token not in [r[0] for r in rows]:
                rows.append([token, "major" if token[1:2] in ("", "m") and "m" != token[1:2] else "minor",
                             len(token)])
        output = "# Chart in %s\n\n" % key + "\n".join(lines).strip()
        return {"output": output, "data": {"action": action, "key": key, "chords": len(rows)}}
    # analyze
    chords = re.findall(r"[A-G][#b]?(?:m|maj|min|dim|aug|sus)?\d*", text)
    if not chords:
        raise core.InvalidInput("no chord symbols found in the text")
    roots = [c[0] + ("#" if "#" in c[:2] else "b" if "b" in c[:2] else "") for c in chords]
    unique = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    numeric = []
    tonic_index = NOTE_NAMES.index(key) if key in NOTE_NAMES else 0
    for root in roots:
        degree = (NOTE_NAMES.index(root if root in NOTE_NAMES else "C") - tonic_index) % 12
        numeric.append({0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}.get(degree, "?%d" % degree))
    rows = [[c, n] for c, n in zip(chords, numeric)]
    return {"output": core.table(rows, ["chord", "scale degree in %s" % key]),
            "data": {"chords": len(chords), "unique_roots": unique, "key": key}}


def chess_notation(ctx):
    text = ctx.require_text(minimum=1)
    action = ctx.params["action"]
    moves = re.findall(r"\b(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?|0-0(?:-0)?)\b", text)
    normalized = []
    problems = []
    for move in moves:
        fixed = move.replace("0-0-0", "O-O-O").replace("0-0", "O-O").replace("x", "x")
        if not re.fullmatch(r"(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?)", fixed):
            problems.append(move)
            continue
        normalized.append(fixed)
    if action == "validate":
        data = {"moves": len(moves), "valid": len(normalized), "problems": problems}
        output = ("%d move(s) parsed, %d valid" % (len(moves), len(normalized)))
        if problems:
            output += "\ninvalid: " + ", ".join(problems[:10])
        return {"output": output, "data": data,
                "warnings": ["%d malformed move(s)" % len(problems)] if problems else []}
    if action == "normalize":
        out = text
        for old, new in zip(moves, normalized):
            out = out.replace(old, new, 1)
        return {"output": out, "data": {"moves": len(normalized)}, "warnings": ["castling normalised to O-O form"]}
    if action == "fen":
        pieces = {"K": "K", "Q": "Q", "R": "R", "B": "B", "N": "N", "P": "P"}
        board = []
        for rank in range(8, 0, -1):
            line = []
            for file_letter in "abcdefgh":
                found = next((m for m in normalized
                              if m.endswith("%s%d" % (file_letter, rank))), None)
                if found:
                    piece = found[0] if found[0] in pieces else "P"
                    line.append(piece)
                else:
                    line.append("")
            board.append(line)
        compressed = []
        for line in board:
            run = 0
            row = ""
            for cell in line:
                if cell:
                    if run:
                        row += str(run)
                        run = 0
                    row += cell
                else:
                    run += 1
            if run:
                row += str(run)
            compressed.append(row)
        fen = "/".join(compressed) + " w - - 0 1"
        return {"output": fen,
                "data": {"fen": fen, "source": "piece placement reconstructed from the move list",
                         "confidence": "low - a move list does not determine the full board"}}
    plies = [1 if move[:1] in "KQRBN" or move.startswith("O-O") or move[0] in "abcdefgh" else 2
             for move in normalized]
    data = {"plies": len(normalized), "full_moves": (len(normalized) + 1) // 2,
            "captures": sum(1 for m in normalized if "x" in m),
            "checks": sum(1 for m in normalized if m.endswith("+")),
            "mates": sum(1 for m in normalized if m.endswith("#")),
            "queenside_castle": sum(1 for m in normalized if m == "O-O-O")}
    output = core.table([[k, v] for k, v in data.items()], ["metric", "value"])
    return {"output": output, "data": data}


def braille_tool(ctx):
    text = ctx.require_text(minimum=1)
    action = ctx.params["action"]
    six_dot = bool(ctx.params["six_dot"])
    if action == "decode":
        decoded = braille_decode(text)
        return {"output": decoded, "data": {"direction": "decode", "cells": sum(1 for c in text if 0x2800 <= ord(c) <= 0x28FF)},
                "warnings": ["grade-1 English only; contractions and other languages are not decoded"]}
    encoded = braille_encode(text, six_dot=six_dot)
    cells = sum(1 for c in encoded if 0x2800 <= ord(c) <= 0x28FF)
    data = {"direction": "encode", "cells": cells, "chars_in": len(text),
            "six_dot": six_dot, "note": "grade-1 English braille with number sign"}
    return {"output": encoded, "data": data,
            "warnings": ["8-dot extensions and contracted (grade 2) braille are out of scope"]}


def box_drawing(ctx):
    art = ctx.assets.json(ART_ASSET)
    text = ctx.require_text(minimum=1)
    kind = ctx.params["kind"]
    style = ctx.params["style"]
    boxes = art["box_styles"]
    glyphs = boxes.get("unicode" if style == "unicode" else "ascii") if isinstance(boxes, dict) else None
    if not glyphs:
        glyphs = {"h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+"}
    lines = [ln.rstrip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    if kind == "tree":
        out = []
        for idx, line in enumerate(lines):
            depth = len(line) - len(line.lstrip(" -\t"))
            label = line.strip(" -\t")
            prefix = "" if depth == 0 else ("  " * (depth // 2)) + ("└─ " if style == "unicode" else "\\- ")
            out.append(prefix + label)
        return {"output": "\n".join(out), "data": {"kind": kind, "nodes": len(lines)}}
    if kind == "flow":
        arrow = " → " if style == "unicode" else " -> "
        return {"output": arrow.join(ln.strip() for ln in lines),
                "data": {"kind": kind, "steps": len(lines)}}
    if kind == "sequence":
        out = []
        for idx, line in enumerate(lines, 1):
            marker = "│" if style == "unicode" else "|"
            out.append("%2d %s %s" % (idx, marker, line.strip()))
        return {"output": "\n".join(out), "data": {"kind": kind, "steps": len(lines)}}
    width = max(len(ln) for ln in lines) + 2
    top = glyphs["tl"] + glyphs["h"] * width + glyphs["tr"]
    bottom = glyphs["bl"] + glyphs["h"] * width + glyphs["br"]
    body = [glyphs["v"] + " " + ln.ljust(width - 1) + glyphs["v"] for ln in lines]
    return {"output": "\n".join([top] + body + [bottom]),
            "data": {"kind": "box", "lines": len(lines), "internal_width": width}}


def currency_guide(ctx):
    tables = _symbol_table(ctx)
    currency = tables["currency"]
    query = (ctx.params["query"] or ctx.text).strip()
    amount = ctx.params["amount"].strip()
    fmt = ctx.params["format"]
    if query:
        needle = query.upper()
        matches = {code: info for code, info in currency.items()
                   if needle in code or needle.lower() in info.get("name", "").lower()
                   or needle == info.get("symbol")}
        if not matches:
            raise core.InvalidInput("no currency matched %r" % query,
                                    hint="Try USD, EUR, JPY or a symbol such as $.")
    else:
        matches = currency
    rows = [[code, info.get("symbol", ""), info.get("name", ""), info.get("minor", ""),
             "U+%04X" % ord(info.get("symbol", " ")[0]) if info.get("symbol") else ""]
            for code, info in sorted(matches.items())]
    output = core.table(rows, ["code", "symbol", "name", "minor unit", "code point"])
    extra = []
    if amount:
        try:
            value = float(amount.replace(",", ""))
        except ValueError:
            raise core.InvalidParam("--set amount must be numeric")
        for code, info in sorted(matches.items()):
            if info.get("minor_per"):
                extra.append([code, "%.2f" % value, "%.0f %s" % (value * info["minor_per"], info.get("minor", ""))])
    if extra:
        output += "\n\n" + core.table(extra, ["code", "amount", "in minor units"])
    data = {"currencies": sorted(matches), "amount": amount or None,
            "note": "symbols are not unique across currencies; always show the ISO code in financial text"}
    return {"output": output, "data": data,
            "warnings": ["symbol collisions are common ($ = USD/CAD/AUD/...); include the ISO code"]
            if len({info.get("symbol") for info in matches.values()}) < len(matches) else []}


def legal_markup(ctx):
    tables = _symbol_table(ctx)
    marks = tables["proofreading_marks"]
    query = (ctx.params["query"] or ctx.text).strip().lower()
    fmt = ctx.params["format"]
    entries = marks
    if query:
        entries = [m for m in marks if query in m["mark"].lower() or query in m["name"].lower()
                   or query in m.get("meaning", "").lower()]
        if not entries:
            raise core.InvalidInput("no editorial mark matched %r" % query)
    if fmt == "json":
        return {"output": json.dumps(entries, indent=2, ensure_ascii=False),
                "data": {"count": len(entries)}}
    rows = [[m["mark"], m["name"], m.get("meaning", "")] for m in entries]
    return {"output": core.table(rows, ["mark", "name", "meaning"]),
            "data": {"count": len(entries)}}


def arrow_relations(ctx):
    tables = _symbol_table(ctx)
    direction = ctx.params["direction"]
    style = ctx.params["style"]
    text = ctx.text.strip()
    arrows = tables["arrows"]
    relations = tables["relations"]
    pool = {}
    if style in ("all", "arrows"):
        pool.update(arrows)
    if style in ("all", "relations"):
        pool.update(relations)
    if direction == "to-unicode":
        if not text:
            rows = [[k, v, "U+%04X" % ord(v)] for k, v in sorted(pool.items())]
            return {"output": core.table(rows, ["ascii", "symbol", "code point"]),
                    "data": {"count": len(rows), "style": style}}
        out = text
        for token in sorted(pool, key=len, reverse=True):
            out = re.sub(r"(?<![\w])%s(?![\w])" % re.escape(token), pool[token], out)
        return {"output": out, "data": {"direction": direction, "style": style}}
    if not text:
        raise core.InvalidInput("supply symbols to convert back to ASCII")
    reverse = {v: k for k, v in pool.items()}
    out = text
    for symbol, ascii_form in sorted(reverse.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(symbol, " %s " % ascii_form)
    return {"output": re.sub(r"\s{2,}", " ", out).strip(),
            "data": {"direction": "to-ascii", "style": style}}


def symbol_pronunciation(ctx):
    tables = _symbol_table(ctx)
    query = (ctx.params["query"] or ctx.text).strip()
    fmt = ctx.params["format"]
    rows = []
    for section in ("arrows", "relations", "logic_symbols", "currency", "math_latex"):
        data = tables.get(section) or {}
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(value, str):
                    continue
                name = key.strip("\\")
                rows.append([value, name, section])
    if query:
        needle = query.lower()
        rows = [r for r in rows if needle in r[1].lower() or needle == r[0]]
        if not rows:
            raise core.InvalidInput("no symbol matched %r" % query,
                                    hint="Try 'alpha', 'implies', '→' or 'sum'.")
    seen = set()
    unique_rows = []
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
    if fmt == "json":
        return {"output": json.dumps([{"symbol": r[0], "name": r[1], "group": r[2]} for r in unique_rows],
                                     indent=2, ensure_ascii=False),
                "data": {"count": len(unique_rows)}}
    return {"output": core.table(unique_rows[:80], ["symbol", "spoken name", "group"]),
            "data": {"count": len(unique_rows), "shown": min(80, len(unique_rows))},
            "warnings": ["names are the conventional readings used by screen readers, not official Unicode names"]
            if unique_rows else []}


HANDLERS = {
    "notation.mathematical-notation-editor": math_notation,
    "notation.scientific-symbol-specialist": scientific_symbols,
    "notation.logic-and-set-notation-composer": logic_notation,
    "notation.phonetic-alphabet-and-ipa-editor": ipa_editor,
    "notation.music-symbol-and-chord-text-editor": music_editor,
    "notation.chess-and-game-notation-editor": chess_notation,
    "notation.braille-pattern-text-specialist": braille_tool,
    "notation.box-drawing-and-diagram-notation-expert": box_drawing,
    "notation.currency-and-financial-symbol-guide": currency_guide,
    "notation.legal-and-editorial-markup-symbol-guide": legal_markup,
    "notation.technical-arrow-and-relation-selector": arrow_relations,
    "notation.symbol-pronunciation-and-naming-guide": symbol_pronunciation,
}
