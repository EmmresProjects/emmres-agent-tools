"""Text art (12) and emoji (12) capabilities.

Art rendering uses the built-in 3x5 bitmap font and style presets from
``asset-art-tables.json``. Emoji work is metadata-driven: meanings, concepts,
compatibility and fallbacks come from the same asset, while code points come
from the text itself.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zlib

EXPORTS = ["render_banner", "box_frame", "emoji_index", "EMOJI_RE"]
ART_ASSET = "asset-art-tables.json"

EMOJI_RE = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2})|(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\U0001F3FB-\U0001F3FF]+)")


def _art(ctx):
    return ctx.assets.json(ART_ASSET)


def emoji_index(tables):
    index = {}
    for entry in tables["emoji"]:
        index[entry["char"]] = entry
    return index


# --------------------------------------------------------------------------
# rendering primitives
# --------------------------------------------------------------------------


def render_banner(text, style="standard", width=0, spacing=1, tables=None):
    style_def = tables["banner_styles"].get(style, tables["banner_styles"]["standard"])
    font = tables["font_3x5"]
    fill = style_def["fill"]
    rows = [""] * 5
    for char in text.upper():
        glyph = font.get(char, font.get("?"))
        for row_idx in range(5):
            cell = glyph[row_idx].replace("#", fill)
            if style_def["slant"]:
                cell = (" " * row_idx) + cell
            if style_def["double_x"]:
                cell = "".join(ch * 2 if ch != " " else "  " for ch in cell)
            rows[row_idx] += cell + (style_def["separator"] * spacing if spacing else "")
    if width:
        rows = [row[:width] for row in rows]
    return "\n".join(row.rstrip() for row in rows)


def box_frame(lines, style_def, padding=1, align="left", width=0, title=""):
    inner = [str(line) for line in lines]
    text_width = max([core.display_width(line) for line in inner] + [0])
    if title:
        text_width = max(text_width, core.display_width(title))
    if width:
        text_width = max(text_width, width - 2 * padding - 2)
    padded = []
    for line in inner:
        pad = text_width - core.display_width(line)
        if align == "right":
            line = " " * pad + line
        elif align == "center":
            line = " " * (pad // 2) + line + " " * (pad - pad // 2)
        else:
            line = line + " " * pad
        padded.append(" " * padding + line + " " * padding)
    h = style_def["h"]
    top = style_def["tl"] + h * (text_width + 2 * padding) + style_def["tr"]
    bottom = style_def["bl"] + h * (text_width + 2 * padding) + style_def["br"]
    if title:
        label = " %s " % title[: max(0, text_width - 2)]
        top = style_def["tl"] + h + label + h * max(0, text_width + 2 * padding - len(label) - 1) + style_def["tr"]
    body = [style_def["v"] + line + style_def["v"] for line in padded]
    return "\n".join([top] + body + [bottom])


# --------------------------------------------------------------------------
# text art capabilities
# --------------------------------------------------------------------------


def ascii_illustrator(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    ramp = ctx.params["ramp"] or " .:-=+*#%@"
    width = int(ctx.params["width"])
    invert = bool(ctx.params["invert"])
    char_override = ctx.params["char"]
    grid = _parse_grid(text)
    if grid is None:
        # not a level map: render the words as art
        banner = render_banner(text, "standard", width, 1, _art(ctx))
        return {"output": banner, "data": {"mode": "banner-fallback",
                                          "note": "input was not a level map; rendered with the built-in font"}}
    if invert:
        grid = [[1.0 - v for v in row] for row in grid]
    rows_height = len(grid)
    cols_width = len(grid[0]) if grid else 0
    target_width = width or cols_width
    scale = max(1, round(cols_width / target_width)) if target_width else 1
    out_rows = []
    for r in range(0, rows_height, max(1, scale * 2)):
        line = []
        for c in range(0, cols_width, scale):
            block = [grid[rr][c] for rr in range(r, min(r + scale * 2, rows_height))]
            value = sum(block) / len(block)
            if char_override:
                line.append(char_override if value > 0.5 else " ")
            else:
                line.append(ramp[min(len(ramp) - 1, int(value * (len(ramp) - 1)))])
        out_rows.append("".join(line).rstrip())
    data = {"mode": "level-map", "source_size": [cols_width, rows_height],
            "output_size": [target_width, len(out_rows)], "ramp": ramp}
    return {"output": "\n".join(out_rows), "data": data}


def _parse_grid(text):
    lines = [ln for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    grid = []
    numeric = 0
    for line in lines:
        tokens = line.replace(",", " ").split()
        values, ok = [], True
        for token in tokens:
            try:
                values.append(max(0.0, min(1.0, float(token))))
            except ValueError:
                ok = False
                break
        if not ok:
            numeric = 0
            break
        numeric += 1
        grid.append(values)
    if numeric and grid and len({len(r) for r in grid}) == 1:
        return grid
    # character map (any non-space = "on")
    widths = {len(line.rstrip()) for line in lines}
    if len(widths) > 1 and max(widths) - min(widths) > max(widths) * 0.3:
        return None
    width = max(len(line) for line in lines)
    for line in lines:
        grid.append([0.0 if ch == " " else 1.0 for ch in line.ljust(width)])
    if all(all(v == 0.0 for v in row) for row in grid):
        return None
    return grid


def banner_maker(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _art(ctx)
    output = render_banner(text, ctx.params["font"], int(ctx.params["width"]),
                           int(ctx.params["spacing"]), tables)
    data = {"font": ctx.params["font"], "width": int(ctx.params["width"]),
            "lines": len(output.split("\n")),
            "note": "built-in 3x5 bitmap font; layout is FIGlet-style, not .flf compatible"}
    return {"output": output, "data": data}


def unicode_art(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _art(ctx)
    style = ctx.params["style"]
    if style == "braille":
        rendered = _braille_render(text)
        return {"output": rendered, "data": {"style": "braille", "cell": "2x4 dots"}}
    style_map = {"blocks": "block", "shade": "slant", "outline": "small"}
    font = ctx.params["font"]
    output = render_banner(text, style_map.get(style, font), int(ctx.params["width"]), 1, tables)
    if style == "shade":
        output = output.replace("#", "\u2593").replace("=", "\u2592")
    elif style == "outline":
        output = output.replace("o", "\u25a1")
    return {"output": output, "data": {"style": style, "font": font}}


def _braille_render(text):
    """Render a bitmap of the given text using braille patterns (2x4 dots per cell)."""
    bits = {0x01: (0, 0), 0x02: (0, 1), 0x04: (0, 2), 0x08: (1, 0),
            0x10: (1, 1), 0x20: (1, 2), 0x40: (0, 3), 0x80: (1, 3)}
    pixels = []
    for char in text.upper():
        for row in range(5):
            for col in range(3):
                pixels.append(1 if char.isalnum() else 0)
    per_row = max(1, len(text) * 3)
    rows = [pixels[i:i + per_row] for i in range(0, len(pixels), per_row)]
    out = []
    for r in range(0, len(rows), 4):
        line = []
        for c in range(0, per_row, 2):
            code = 0x2800
            for mask, (dx, dy) in bits.items():
                rr, cc = r + dy, c + dx
                if rr < len(rows) and cc < len(rows[rr]) and rows[rr][cc]:
                    code |= mask
            line.append(chr(code) if code != 0x2800 else " ")
        out.append("".join(line).rstrip())
    return "\n".join(out)


def box_builder(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _art(ctx)
    style = tables["box_styles"].get(ctx.params["style"], tables["box_styles"]["single"])
    lines = core.canonical_newlines(text).split("\n")
    output = box_frame(lines, style, int(ctx.params["padding"]), ctx.params["align"],
                       int(ctx.params["width"]), ctx.params["title"])
    return {"output": output, "data": {"style": ctx.params["style"], "lines": len(lines),
                                        "padding": int(ctx.params["padding"])}}


def mosaic_maker(ctx):
    text = ctx.require_text(minimum=1)
    tables = _art(ctx)
    ramp = ctx.params["ramp"] or "".join(tables["mosaic_ramps"].get(ctx.params["char_mode"], tables["mosaic_ramps"]["shade"]))
    grid = _parse_grid(text)
    if grid is None:
        raise core.InvalidInput("mosaic needs a level map: lines of digits or gray values 0-1",
                                hint="Example: '0 0.5 1' rows, or a character grid where non-space means ink.")
    scale = int(ctx.params["scale"])
    if ctx.params["invert"]:
        grid = [[1.0 - v for v in row] for row in grid]
    out = []
    for r in range(0, len(grid), scale):
        cells = []
        for c in range(0, len(grid[0]), scale):
            block = [grid[rr][cc] for rr in range(r, min(r + scale, len(grid)))
                     for cc in range(c, min(c + scale, len(grid[0])))]
            value = sum(block) / len(block)
            cells.append(ramp[min(len(ramp) - 1, int(value * (len(ramp) - 1)))])
        out.append("".join(cells))
    return {"output": "\n".join(out), "data": {"ramp": ramp, "scale": scale,
                                                "size": [len(grid[0]), len(grid)]}}


def kaomoji_composer(ctx):
    tables = _art(ctx)
    parts = tables["kaomoji"]["parts"]
    presets = tables["kaomoji"]["presets"]
    mood = ctx.params["mood"]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"] or mood)
    pool = list(presets.get(mood, presets["happy"]))
    eyes = ctx.params["eyes"]
    mouth = ctx.params["mouth"]
    arms = ctx.params["arms"]
    if eyes or mouth or arms:
        eye = eyes or rng.choice(parts["eyes"].get(mood, parts["eyes"]["happy"]))
        mth = mouth or rng.choice(parts["mouths"]["smile"])
        arm = arms or ""
        base = "(%s%s%s)" % (eye, mth, eye)
        if arm == "up":
            base = "\u2570%s\u256f" % base
        elif arm == "hug":
            base = "(%s)" % base
        pool = [base] + pool
    shuffled = rng.shuffle(pool)
    chosen = [shuffled[i % len(shuffled)] for i in range(count)]
    return {"output": "  ".join(chosen),
            "data": {"mood": mood, "count": count, "pool": len(pool)}}


def emoticon_maker(ctx):
    tables = _art(ctx)
    mood = ctx.params["mood"]
    count = int(ctx.params["count"])
    style = ctx.params["style"]
    rng = core.seeded_rng(ctx.params["seed"] or mood)
    classic = list(tables["emoticons"].get(mood, tables["emoticons"]["happy"]))
    kao = list(tables["kaomoji"]["presets"].get(mood, tables["kaomoji"]["presets"]["happy"]))
    pool = classic if style == "classic" else kao if style == "kaomoji" else classic + kao
    shuffled = rng.shuffle(pool)
    chosen = [shuffled[i % len(shuffled)] for i in range(count)]
    return {"output": "\n".join(chosen), "data": {"mood": mood, "style": style, "count": count}}


def ornament_designer(ctx):
    tables = _art(ctx)
    kind = ctx.params["kind"]
    width = int(ctx.params["width"])
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"] or kind)
    pool = tables["ornaments"].get(kind, tables["ornaments"]["divider"])
    out = []
    for _ in range(count):
        item = rng.choice(pool)
        if kind == "divider":
            if 0 < width < len(item):
                out.append(item[:width])
            elif width > len(item):
                filler = "\u2500"
                pad = (width - len(item)) // 2
                out.append(filler * pad + " " + item + " " + filler * pad)
            else:
                out.append(item)
        else:
            out.append(item)
    return {"output": "\n".join(out), "data": {"kind": kind, "count": count, "width": width}}


def logo_concepts(ctx):
    text = ctx.require_text(minimum=1)
    tables = _art(ctx)
    count = int(ctx.params["count"])
    max_width = int(ctx.params["max_width"])
    style = ctx.params["style"]
    styles = ["banner", "framed", "letterspaced", "stacked"] if style == "mixed" else [style]
    blocks = []
    for idx in range(count):
        chosen = styles[idx % len(styles)]
        if chosen == "banner":
            rendered = render_banner(text, ["standard", "block", "slant", "banner3"][idx % 4],
                                     max_width, 1, tables)
        elif chosen == "framed":
            rendered = box_frame([text.upper()], tables["box_styles"][["single", "double", "rounded", "heavy"][idx % 4]],
                                 padding=2, align="center", width=max_width)
        elif chosen == "letterspaced":
            rendered = "  ".join(list(text.upper()))
            if len(rendered) > max_width:
                rendered = rendered[:max_width]
        else:
            rendered = "\n".join(list(text.upper()))
        blocks.append("### concept %d (%s)\n%s" % (idx + 1, chosen, rendered))
    return {"output": "\n\n".join(blocks),
            "data": {"count": count, "styles": styles, "max_width": max_width}}


def alignment_repairer(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    mode = ctx.params["mode"]
    separator = ctx.params["separator"] or "|"
    align = ctx.params["align"]
    lines = core.canonical_newlines(text).split("\n")
    if mode == "arrows" or (mode == "auto" and text.count("=>") + text.count("->") >= 2):
        pairs = []
        for line in lines:
            if not line.strip():
                continue
            match = re.match(r"^(.*?)\s*(=>|->|:)\s*(.*)$", line)
            if match:
                pairs.append((match.group(1).strip(), match.group(2), match.group(3).strip()))
            else:
                pairs.append((line.strip(), "", ""))
        width = max([core.display_width(p[0]) for p in pairs] + [1])
        out = []
        for left, arrow, right in pairs:
            pad = width - core.display_width(left)
            if arrow:
                out.append("%s%s %s %s" % (left, " " * pad, arrow if arrow != ":" else ":", right))
            else:
                out.append(left)
        return {"output": "\n".join(out), "data": {"mode": "arrows", "rows": len(pairs)}}
    rows = [[cell.strip() for cell in line.split(separator)] for line in lines if line.strip()]
    if not rows:
        raise core.InvalidInput("nothing to align")
    width_count = max(len(r) for r in rows)
    rows = [r + [""] * (width_count - len(r)) for r in rows]
    widths = [0] * width_count
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], core.display_width(cell))
    out = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            pad = widths[idx] - core.display_width(cell)
            if align == "right":
                cells.append(" " * pad + cell)
            elif align == "decimal" and re.search(r"\d", cell):
                left, _, right = cell.partition(".")
                cells.append(" " * (widths[idx] - core.display_width(cell) - 1) + cell if right else " " * pad + cell)
            else:
                cells.append(cell + " " * pad)
        out.append((separator + " ").join(cells).rstrip())
    return {"output": "\n".join(out), "data": {"mode": mode, "rows": len(rows),
                                                "columns": width_count}}


def pattern_maker(ctx):
    text = ctx.require_text(minimum=1) if not ctx.params["motif"] else ctx.params["motif"]
    motif = ctx.params["motif"] or text.strip()
    rows, cols = int(ctx.params["rows"]), int(ctx.params["cols"])
    mode = ctx.params["mode"]
    if mode == "checker":
        unit = [motif[: max(1, len(motif) // 2)], " " * max(1, len(motif) // 2)]
        out = []
        for r in range(rows):
            out.append("".join(unit[(r + c) % 2] for c in range(cols)))
        return {"output": "\n".join(out), "data": {"mode": mode, "rows": rows, "cols": cols}}
    if mode == "border":
        tile = motif * cols
        top = tile[: cols * len(motif)]
        out = [top]
        for _ in range(max(0, rows - 2)):
            out.append(motif[0] + " " * max(0, len(top) - 2) + motif[-1])
        out.append(top)
        return {"output": "\n".join(out), "data": {"mode": mode, "rows": rows, "cols": cols}}
    if mode == "repeat":
        return {"output": "\n".join(motif * cols for _ in range(rows)),
                "data": {"mode": mode, "rows": rows, "cols": cols}}
    unit = motif + " "
    out = []
    for r in range(rows):
        offset = (r * len(unit) // 2) % len(unit)
        line = (unit * (cols + 2))[offset:offset + cols * len(unit)]
        out.append(line.rstrip())
    return {"output": "\n".join(out), "data": {"mode": mode, "rows": rows, "cols": cols}}


def accessible_art_companion(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    role = ctx.params["role"]
    max_chars = int(ctx.params["max_chars"])
    lines = core.canonical_newlines(text).split("\n")
    width = max((core.display_width(line) for line in lines), default=0)
    non_space = sum(1 for ch in text if not ch.isspace())
    density = non_space / max(1, len(text))
    ascii_only = all(ord(ch) < 128 for ch in text)
    fallback = _ascii_art_fallback(text) if not ascii_only else text
    if role == "decorative":
        alt = ("Decorative %dx%d text art. Replace with an empty alt attribute "
               "unless it carries meaning." % (width, len(lines)))
        marker = "<span aria-hidden=\"true\" role=\"presentation\">\n%s\n</span>" % fallback
    else:
        alt = ("Informative text art, %dx%d characters, %d%% ink density. "
               "Describe the idea it conveys rather than the characters." % (width, len(lines), int(density * 100)))
        marker = ("<figure>\n<pre aria-hidden=\"true\">\n%s\n</pre>\n"
                  "<figcaption>%s</figcaption>\n</figure>" % (fallback, alt))
    alt = core.truncate(alt, max_chars)
    data = {"width": width, "height": len(lines), "ink_density": round(density, 3),
            "ascii_only": ascii_only, "role": role, "alt_text": alt}
    output = "alt text:\n%s\n\nas HTML:\n%s" % (alt, marker)
    warnings = [] if ascii_only else ["art contains non-ASCII glyphs; an ASCII fallback was generated"]
    return {"output": output, "data": data, "warnings": warnings}


def _ascii_art_fallback(text):
    mapping = {"\u2588": "#", "\u2593": "%", "\u2592": "*", "\u2591": ":", "\u2500": "-",
               "\u2502": "|", "\u250c": "+", "\u2510": "+", "\u2514": "+", "\u2518": "+",
               "\u2550": "=", "\u2551": "|", "\u256d": "+", "\u256e": "+", "\u2570": "+",
               "\u256f": "+", "\u2501": "-", "\u2503": "|"}
    return "".join(mapping.get(ch, ch if ord(ch) < 128 else "#") for ch in text)


# --------------------------------------------------------------------------
# emoji capabilities
# --------------------------------------------------------------------------


def _find_emoji(tables, query):
    query = query.strip().lower()
    index = emoji_index(tables)
    if not query:
        raise core.InvalidInput("give an emoji, a name, or a concept keyword")
    if query in index:
        return index[query]
    best = None
    for entry in tables["emoji"]:
        haystack = " ".join([entry["name"]] + entry["keywords"]).lower()
        if query == entry["name"].lower():
            return entry
        if query in haystack:
            score = haystack.count(query) + (2 if entry["name"].lower().startswith(query) else 0)
            if best is None or score > best[0]:
                best = (score, entry)
    return best[1] if best else None


def emoji_guide(ctx):
    tables = _art(ctx)
    query = ctx.params["query"].strip() or ctx.text.strip()
    entry = _find_emoji(tables, query)
    if not entry:
        raise core.InvalidInput("no emoji matched %r" % query,
                                hint="Try 'thumbs up', 'launch', 'warning' or paste an emoji.")
    detail = ctx.params["detail"]
    data = {"query": query, "emoji": entry,
            "codepoints": ["U+%04X" % ord(ch) for ch in entry["char"] if ord(ch) > 0x20]}
    if detail == "short":
        return {"output": "%s %s - %s" % (entry["char"], entry["name"], ", ".join(entry["keywords"][:4])),
                "data": data}
    lines = [
        "%s  %s" % (entry["char"], entry["name"]),
        "",
        "- code points: %s" % ", ".join("U+%04X" % ord(ch) for ch in entry["char"]),
        "- category: %s" % entry["category"],
        "- introduced: Emoji %s" % entry["since"],
        "- concepts: %s" % ", ".join(entry["keywords"]),
        "",
        "Usage: use it when you mean one of the concepts above; keep it out of formal legal or",
        "medical text, and always pair it with accessible text for screen readers.",
    ]
    return {"output": "\n".join(lines), "data": data}


def emoji_sequence(ctx):
    tables = _art(ctx)
    base = ctx.params["base"].strip()
    entry = _find_emoji(tables, base) if base else None
    char = entry["char"] if entry else base
    if not char:
        raise core.InvalidInput("give a base emoji with --set base=...")
    tone = int(ctx.params["tone"])
    if tone:
        char = char + tables["emoji_tones"][str(tone)]
    extras = []
    for item in ctx.params["zwj"]:
        found = _find_emoji(tables, item)
        extras.append(found["char"] if found else item)
    if extras:
        char = char + "\u200d" + "\u200d".join(extras)
    if ctx.params["variation"]:
        char = char + "\ufe0f"
    data = {"sequence": char, "codepoints": ["U+%04X" % ord(ch) for ch in char],
            "components": (1 + len(extras)), "tone": tone}
    return {"output": char, "data": data,
            "warnings": ["skin-tone modifiers only apply to human emoji"] if tone and entry and entry["category"] != "people" else []}


def emoji_translator(ctx):
    tables = _art(ctx)
    text = ctx.require_text(minimum=1)
    direction = ctx.params["direction"]
    density = ctx.params["density"]
    if direction == "to-text":
        index = emoji_index(tables)
        out = []
        unknown = []
        for match in EMOJI_RE.finditer(text):
            entry = index.get(match.group(0))
            if entry:
                out.append("[%s]" % entry["name"])
            else:
                unknown.append(match.group(0))
                out.append("[emoji]")
        data = {"direction": "to-text", "emoji_found": len(out), "unknown": unknown}
        return {"output": text.replace("\n", "\n") if not out else
                EMOJI_RE.sub(lambda m: "[%s]" % (index.get(m.group(0), {}).get("name", "emoji")), text),
                "data": data}
    limit = {"light": 2, "medium": 4, "heavy": 99}[density]
    lowered = text.lower()
    out = text
    used = []
    for concept, char in tables["concept_map"].items():
        if len(used) >= limit:
            break
        if re.search(r"\b%s\b" % re.escape(concept), lowered):
            out = re.sub(r"\b%s\b" % re.escape(concept), "%s %s" % (concept, char), out, count=1, flags=re.I)
            used.append({"concept": concept, "emoji": char})
    data = {"direction": "to-emoji", "density": density, "inserted": used}
    warnings = ["no concept mapped to an emoji in this text"] if not used else []
    return {"output": out, "data": data, "warnings": warnings}


def emoji_accessibility(ctx):
    tables = _art(ctx)
    text = ctx.require_text(minimum=1)
    index = emoji_index(tables)
    names = []
    for match in EMOJI_RE.finditer(text):
        entry = index.get(match.group(0))
        names.append(entry["name"] if entry else "emoji")
    if not names:
        raise core.InvalidInput("no emoji found in the input")
    context = ctx.params["context"].strip()
    style = ctx.params["style"]
    joined = ", ".join(names)
    if style == "short":
        out = joined
    elif style == "aria":
        out = '<span role="img" aria-label="%s">%s</span>' % (joined, "".join(names and [m.group(0) for m in EMOJI_RE.finditer(text)]))
    else:
        out = "Image: %s%s" % (joined, (" - " + context) if context else "")
    return {"output": out, "data": {"emoji": names, "count": len(names), "style": style}}


def emoji_compatibility(ctx):
    tables = _art(ctx)
    text = ctx.require_text(minimum=1)
    index = emoji_index(tables)
    target = ctx.params["target"]
    minimum = {"oldest": 0.0, "windows10": 12.0, "android9": 11.0, "ios12": 11.0, "modern": 15.1}[target]
    risky, safe = [], []
    for match in EMOJI_RE.finditer(text):
        char = match.group(0)
        entry = index.get(char)
        since = float(entry["since"]) if entry else None
        record = {"emoji": char, "name": entry["name"] if entry else "unknown",
                  "since": entry["since"] if entry else "unknown"}
        if since is None or since > minimum:
            risky.append(record)
        else:
            safe.append(record)
    data = {"target": target, "minimum_emoji_version": minimum,
            "risky": risky, "safe": safe,
            "fallbacks": {r["emoji"]: _emoji_fallback(r) for r in risky}}
    lines = ["# Emoji compatibility for %s (Emoji %.1f+)" % (target, minimum), ""]
    if risky:
        lines.append("| emoji | name | since | fallback |")
        lines.append("| --- | --- | --- | --- |")
        for r in risky:
            lines.append("| %s | %s | %s | %s |" % (r["emoji"], r["name"], r["since"],
                                                    data["fallbacks"][r["emoji"]]))
    else:
        lines.append("All emoji in the text meet the target platform baseline.")
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["%d emoji may not render on %s" % (len(risky), target)] if risky else []}


def _emoji_fallback(record):
    name = record["name"]
    return "[%s]" % name if name != "unknown" else "[emoji]"


def reaction_palette(ctx):
    tables = _art(ctx)
    mood = ctx.params["mood"]
    count = int(ctx.params["count"])
    pool = tables["mood_reactions"].get(mood, tables["mood_reactions"]["approval"])
    index = emoji_index(tables)
    chosen = [pool[i % len(pool)] for i in range(count)]
    rows = [[ch, (index.get(ch, {}).get("name", "emoji"))] for ch in chosen]
    return {"output": " ".join(chosen) + "\n\n" + core.table(rows, ["emoji", "meaning"]),
            "data": {"mood": mood, "count": count, "palette": chosen}}


def sticker_planner(ctx):
    text = ctx.text.strip()
    theme = ctx.params["theme"] or text or "launch"
    count = int(ctx.params["count"])
    max_chars = int(ctx.params["max_chars"])
    tone = ctx.params["tone"]
    tables = _art(ctx)
    index = emoji_index(tables)
    templates = {
        "playful": ["{t} time!", "guess what: {t}", "{t}? yes. {t}?", "plot twist: {t}"],
        "professional": ["{t}: a quick update", "{t} - what to expect", "Now live: {t}"],
        "warm": ["A little {t} for you", "Thinking of you: {t}", "{t}, with thanks"],
    }[tone if tone in ("playful", "professional", "warm") else "playful"]
    theme_words = theme.split()
    rows = []
    for idx in range(count):
        base = templates[idx % len(templates)].format(t=theme)
        emoji = tables["concept_map"].get(theme_words[0].lower() if theme_words else "", "")
        caption = (base + " " + emoji).strip()[:max_chars]
        rows.append([idx + 1, caption, len(caption)])
    return {"output": core.table(rows, ["#", "caption", "chars"]),
            "data": {"theme": theme, "count": count, "max_chars": max_chars,
                     "captions": [r[1] for r in rows]}}


def moodboard_maker(ctx):
    tables = _art(ctx)
    theme = ctx.params["theme"].strip() or (ctx.text.strip() or "calm focus")
    count = int(ctx.params["count"])
    words = [w.strip().lower() for w in re.split(r"[,;/]+|\s{2,}", theme) if w.strip()]
    symbols = []
    for word in words:
        char = tables["concept_map"].get(word)
        if char:
            symbols.extend([char] * max(2, count // max(1, len(words))))
    for entry in tables["emoji"]:
        if len(symbols) >= count:
            break
        if any(word in " ".join(entry["keywords"]).lower() or word in entry["name"].lower() for word in words):
            symbols.append(entry["char"])
    extras = ["\u25c7", "\u25cb", "\u25b3", "\u2726", "\u2500", "\u2502"]
    while len(symbols) < count:
        symbols.append(extras[len(symbols) % len(extras)])
    symbols = symbols[:count]
    grid_width = min(8, max(3, int(len(symbols) ** 0.5) + 1))
    grid = []
    for idx in range(0, len(symbols), grid_width):
        grid.append("  ".join(symbols[idx:idx + grid_width]))
    index = emoji_index(tables)
    legend = ["%s %s" % (s, index.get(s, {}).get("name", "abstract mark")) for s in symbols[:12]]
    return {"output": "\n".join(grid) + "\n\n" + "\n".join(legend),
            "data": {"theme": theme, "count": count, "symbols": symbols}}


def emoji_search(ctx):
    tables = _art(ctx)
    query = ctx.params["query"].strip() or ctx.text.strip()
    limit = int(ctx.params["limit"])
    if not query:
        raise core.InvalidInput("give a concept to search for")
    tokens = [t for t in re.split(r"\W+", query.lower()) if t]
    scored = []
    for entry in tables["emoji"]:
        haystack = " ".join([entry["name"]] + entry["keywords"] + [entry["category"]]).lower()
        score = sum(3 if t == entry["name"].lower() else 1 for t in tokens if t in haystack)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    if not scored:
        raise core.InvalidInput("no emoji matched %r" % query)
    rows = [[e["char"], e["name"], ", ".join(e["keywords"][:4]), e["since"]] for _, e in scored[:limit]]
    return {"output": core.table(rows, ["emoji", "name", "concepts", "since"]),
            "data": {"query": query, "results": [e for _, e in scored[:limit]]}}


def flag_interpreter(ctx):
    tables = _art(ctx)
    text = ctx.require_text(minimum=1)
    flags = re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", text)
    if not flags:
        raise core.InvalidInput("no regional-indicator flag sequences found in the input")
    rows = []
    for flag in flags:
        code = "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in flag)
        rows.append([flag, code, tables["country_codes"].get(code[2:], tables["country_codes"].get(code, "unknown")),
                     ", ".join("U+%04X" % ord(ch) for ch in flag)])
    data = {"flags": [{"flag": r[0], "code": r[1], "country": r[2]} for r in rows]}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    if ctx.params["format"] == "text":
        return {"output": "\n".join("%s %s %s" % (r[0], r[1], r[2]) for r in rows), "data": data}
    return {"output": core.table(rows, ["flag", "code", "country", "code points"]), "data": data,
            "warnings": ["unknown codes are ISO 3166-1 alpha-2 values not in the bundled table"]}


def emoji_sanitizer(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    mode = ctx.params["mode"]
    tables = _art(ctx)
    index = emoji_index(tables)
    allow = set(ctx.params["allow"])
    if mode == "strip":
        cleaned = EMOJI_RE.sub("", text)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return {"output": cleaned, "data": {"mode": "strip",
                                            "removed": len(EMOJI_RE.findall(text))}}
    if mode == "shortcode":
        def to_short(match):
            entry = index.get(match.group(0))
            name = entry["name"].replace(" ", "_").replace("-", "_") if entry else "emoji"
            return ":%s:" % name
        return {"output": EMOJI_RE.sub(to_short, text),
                "data": {"mode": "shortcode", "converted": len(EMOJI_RE.findall(text))}}
    if not allow:
        raise core.InvalidParam("allow-list mode needs --set allow=👍,✅")
    allowed_chars = []
    for item in allow:
        entry = _find_emoji(tables, item)
        allowed_chars.append(entry["char"] if entry else item)
    removed = []
    out = []
    for match in EMOJI_RE.finditer(text):
        if match.group(0) not in allowed_chars:
            removed.append(match.group(0))
    for match in EMOJI_RE.finditer(text):
        pass
    cleaned = EMOJI_RE.sub(lambda m: m.group(0) if m.group(0) in allowed_chars else "", text)
    return {"output": cleaned, "data": {"mode": "allow-list", "allowed": allowed_chars,
                                        "removed": removed},
            "warnings": ["%d emoji removed by the allow list" % len(removed)] if removed else []}


def textual_fallback(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    tables = _art(ctx)
    index = emoji_index(tables)
    style = ctx.params["style"]
    max_chars = int(ctx.params["max_chars"])
    if style == "escape":
        out = "".join(ch if ord(ch) < 128 else ("\\U%08x" % ord(ch) if ord(ch) > 0xFFFF else "\\u%04x" % ord(ch))
                      for ch in text)
    elif style == "alt-text":
        out = EMOJI_RE.sub(lambda m: "[%s]" % index.get(m.group(0), {}).get("name", "emoji"), text)
    else:
        def ascii_fallback(match):
            entry = index.get(match.group(0))
            return ":%s:" % (entry["name"].replace(" ", "_") if entry else "emoji")
        out = EMOJI_RE.sub(ascii_fallback, text)
        out = "".join(ch if ord(ch) < 128 else "?" for ch in out)
    if max_chars:
        out = core.truncate(out, max_chars)
    return {"output": out, "data": {"style": style, "replaced": len(EMOJI_RE.findall(text))}}


HANDLERS = {
    "text-art.ascii-art-illustrator": ascii_illustrator,
    "text-art.figlet-style-banner-maker": banner_maker,
    "text-art.unicode-text-art-designer": unicode_art,
    "text-art.box-and-border-builder": box_builder,
    "text-art.text-mosaic-and-halftone-maker": mosaic_maker,
    "text-art.kaomoji-composer": kaomoji_composer,
    "text-art.emoticon-and-text-reaction-maker": emoticon_maker,
    "text-art.typographic-ornament-designer": ornament_designer,
    "text-art.text-logo-concept-generator": logo_concepts,
    "text-art.monospace-alignment-repairer": alignment_repairer,
    "text-art.text-pattern-and-tessellation-maker": pattern_maker,
    "text-art.accessible-text-art-companion": accessible_art_companion,
    "emoji.emoji-meaning-and-usage-guide": emoji_guide,
    "emoji.emoji-sequence-composer": emoji_sequence,
    "emoji.emoji-translator": emoji_translator,
    "emoji.emoji-accessibility-writer": emoji_accessibility,
    "emoji.emoji-compatibility-advisor": emoji_compatibility,
    "emoji.reaction-palette-designer": reaction_palette,
    "emoji.sticker-and-caption-text-planner": sticker_planner,
    "emoji.symbolic-moodboard-maker": moodboard_maker,
    "emoji.emoji-search-by-concept": emoji_search,
    "emoji.flag-sequence-interpreter": flag_interpreter,
    "emoji.emoji-sanitizer": emoji_sanitizer,
    "emoji.textual-fallback-generator": textual_fallback,
}
