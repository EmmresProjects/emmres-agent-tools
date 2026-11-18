#!/usr/bin/env python3
"""Voice Capability Toolkit - primary entry point.

Turns a voice capability list (plain text, Markdown, copied HTML, or a catalog
JSON file) into a validated, recipe-driven implementation blueprint, and
validates catalog documents.

Commands
--------
    extract     Parse text / Markdown / HTML capability lists into a catalog JSON
    build       Produce implementation blueprints (Markdown + JSON) from a catalog
    validate    Validate a catalog JSON file against the shipped schema
    lookup      Search capabilities by keyword or list categories
    capabilities  Machine-readable list of this tool's commands
    help        Full help text

Run `python3 tools-catalog-builder.py --help` for the full option list and
`python3 tools-catalog-builder.py capabilities` for the machine-readable index.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import os
import re
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load(filename, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("tools-runtime-common.py", "_voice_rt")

PROG = "tools-catalog-builder.py"

CAPABILITIES = [
    {"command": "extract", "summary": "Parse plain text, Markdown, copied HTML or a JSON catalog into a normalized catalog document.", "inputs": ["--input", "--text", "stdin"], "outputs": ["catalog JSON"]},
    {"command": "build", "summary": "Generate implementation blueprints (umbrella or per-capability) using asset-build-recipes.json.", "inputs": ["--input catalog", "--mode", "--emit"], "outputs": ["Markdown spec", "JSON spec"]},
    {"command": "validate", "summary": "Validate a catalog JSON file against $defs/catalog in asset-schemas.json.", "inputs": ["--input catalog.json"], "outputs": ["validation report"]},
    {"command": "lookup", "summary": "Search the catalog by keyword, recipe, or category.", "inputs": ["--catalog", "--query"], "outputs": ["matching capabilities"]},
    {"command": "capabilities", "summary": "Print this machine-readable command index.", "inputs": [], "outputs": ["JSON"]},
]

# ---------------------------------------------------------------------------
# text normalization
# ---------------------------------------------------------------------------

_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|li|ul|ol|tr|td|th|table|thead|tbody|section|article|header|footer|"
    r"h[1-6]|br|hr|dl|dt|dd|figure|figcaption)\b[^>]*>",
    re.IGNORECASE,
)
_ENTITY_TAGS = re.compile(r"<[^>]{1,400}>")
_WS = re.compile(r"[ \t\u00a0\u2007\u202f]+")

_NUMBERING = re.compile(
    r"^\s*(?:"
    r"(?:>=|>|[-*\u2022\u2023\u25aa\u25cf])\s*"          # bullets
    r"|(?:\d{1,3})\s*[.)\u2013\u2014:-]\s+"               # 1. 1) 1 -
    r"|(?:\d{1,3})\s+(?=\S)"                              # "1 Voice Creation"
    r")+",
)

_CATEGORY_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:category|section|group|cluster|pack)\s*"
    r"(?:no\.?\s*)?(\d{1,3})?\s*[:\-–—.]?\s*(.{2,160}?)\s*$",
    re.IGNORECASE,
)

_SKILL_PREFIX_RE = re.compile(
    r"^\s*(?:skill\s*name|skill|capability|tool\s*name|name)\s*[:\-–—]\s*(.+)$",
    re.IGNORECASE,
)
_DESC_PREFIX_RE = re.compile(
    r"^\s*(?:description|desc|what it does|purpose)\s*[:\-–—]\s*(.+)$",
    re.IGNORECASE,
)

_HEADING_IGNORE = {
    "catalog purpose", "recommended skill contract", "category index",
    "implementation grouping suggestions", "catalog boundary",
    "ultimate voice skill catalog", "contents", "table of contents",
    "purpose", "overview", "introduction", "notes", "summary",
}


def read_source(raw_input, text_value=None):
    """Read a text, Markdown, HTML, JSON or PDF source into plain text.

    PDF support is optional and uses pypdf when it is installed; every other
    format is read with the standard library only.
    """
    if raw_input:
        if not os.path.isfile(raw_input):
            raise RT.ToolError(f"Input file not found: {raw_input}", code=RT.EXIT_NOT_FOUND)
        if os.path.getsize(raw_input) > RT.MAX_TEXT_BYTES:
            raise RT.ToolError(
                f"Input '{raw_input}' exceeds the {RT.MAX_TEXT_BYTES} byte text limit.",
                code=RT.EXIT_INVALID_INPUT)
        with open(raw_input, "rb") as handle:
            head = handle.read(1024)
        if head.startswith(b"%PDF"):
            return extract_pdf_text(raw_input), raw_input
        if b"\x00" in head:
            raise RT.ToolError(
                f"Input '{raw_input}' looks like a binary file, not text.",
                code=RT.EXIT_INVALID_INPUT,
                hint="Convert it to .txt, .md, .html or .json first (PDF is "
                     "supported when pypdf is installed).")
        with open(raw_input, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), raw_input
    if text_value:
        return text_value, "<--text>"
    if sys.stdin is not None and not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data, "stdin"
    raise RT.ToolError(
        "No input supplied.",
        code=RT.EXIT_INVALID_INPUT,
        hint="Pass --input <file>, --text '<content>', or pipe text on stdin.")


def extract_pdf_text(path):
    """Extract text from a PDF with pypdf (optional dependency)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RT.ToolError(
            "PDF input requires the optional 'pypdf' dependency.",
            code=RT.EXIT_UNAVAILABLE,
            hint="python3 -m pip install 'pypdf>=3.9'  (or convert the PDF to "
                 ".txt/.md and pass that instead)",
            details={"input": path},
        )
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise RT.ToolError(
                f"PDF '{path}' is encrypted and cannot be read.",
                code=RT.EXIT_INVALID_INPUT,
                hint="Decrypt it or export the relevant pages as text.")
        pages = []
        for page in reader.pages[:400]:
            pages.append(page.extract_text() or "")
    except RT.ToolError:
        raise
    except Exception as exc:
        raise RT.ToolError(
            f"Could not parse PDF '{path}': {exc}",
            code=RT.EXIT_INVALID_INPUT,
            hint="Export the document as .txt or .md instead.")
    return "\n\n".join(pages)


def strip_markup_lines(raw):
    """Remove HTML tags and Markdown decorations, keeping heading structure.

    Returns a list of ``(text, kind)`` pairs where ``kind`` is ``"heading"``,
    ``"text"`` or ``"blank"``.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<style\b.*?</style>", "\n", text, flags=re.S | re.I)
    text = re.sub(r"<script\b.*?</script>", "\n", text, flags=re.S | re.I)
    text = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1>", lambda m: "\n\x01" + m.group(2) + "\n",
                  text, flags=re.S | re.I)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _ENTITY_TAGS.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    out = []
    for line in text.split("\n"):
        kind = "text"
        line = line.strip()
        if line.startswith("\x01"):
            kind = "heading"
            line = line[1:].strip()
        elif re.match(r"^#{1,6}\s*\S", line):
            kind = "heading"
        if not line:
            out.append(("", "blank"))
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^\|+|\|+$", " ", line).strip()
        line = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", line)
        line = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = _WS.sub(" ", line).strip()
        if line and set(line) <= set("-=_: "):
            out.append(("", "blank"))
            continue
        out.append((line, kind))
    return out


def strip_markup(raw):
    """Plain normalized lines (tags and Markdown decoration removed)."""
    return [text for text, _ in strip_markup_lines(raw)]


# ---------------------------------------------------------------------------
# table extraction
# ---------------------------------------------------------------------------

_NAME_COLUMNS = {"skill", "skill name", "capability", "capability name", "name", "tool",
                 "tool name", "feature", "item", "title"}
_DESC_COLUMNS = {"description", "desc", "what it does", "purpose", "summary", "details",
                 "responsibility"}
_CATEGORY_COLUMNS = {"category", "group", "section", "pack", "cluster", "area"}
_INDEX_COLUMNS = {"#", "no", "no.", "number", "num", "id", "index"}


def extract_tables(raw):
    """Pull rows out of HTML/Markdown tables and return (rows, cleaned_text).

    Each row is ``{"category": str|None, "number": int|None, "name": str,
    "description": str}``. Table markup is blanked out of the returned text so
    the line parser does not see it twice.
    """
    rows = []
    cleaned = raw

    def add_row(cells, header_map=None, default_category=None):
        if not any(c.strip() for c in cells):
            return
        if header_map is None:
            values = [c.strip() for c in cells]
            if len(values) >= 3 and re.fullmatch(r"\d{1,3}", values[0] or ""):
                number, name, desc = int(values[0]), values[1], " ".join(values[2:])
            elif len(values) >= 2:
                number, name, desc = None, values[0], " ".join(values[1:])
            else:
                return
            category = default_category
        else:
            values = [c.strip() for c in cells]
            def pick(kind, fallback=None):
                index = header_map.get(kind)
                if index is not None and index < len(values):
                    return values[index]
                return fallback
            number_text = pick("index") or ""
            number = int(number_text) if re.fullmatch(r"\d{1,3}", number_text) else None
            name = pick("name", "")
            desc = pick("description", "")
            category = pick("category") or default_category
        name = _clean_name(name)
        if not _plausible_name(name):
            return
        rows.append({
            "category": _clean_name(category) if category else None,
            "number": number,
            "name": name,
            "description": _clean_desc(desc),
        })

    # HTML tables
    for table_match in re.finditer(r"<table\b.*?</table>", raw, flags=re.S | re.I):
        table_html = table_match.group(0)
        header_map = None
        for row_match in re.finditer(r"<tr\b.*?</tr>", table_html, flags=re.S | re.I):
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_match.group(0),
                               flags=re.S | re.I)
            cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]
            if not cells:
                continue
            mapped = _map_header(cells)
            if mapped and header_map is None:
                header_map = mapped
                continue
            add_row(cells, header_map)
        cleaned = cleaned.replace(table_html, "\n")
    # HTML definition lists can also carry name/description pairs
    for dl_match in re.finditer(r"<dl\b.*?</dl>", raw, flags=re.S | re.I):
        block = dl_match.group(0)
        for term, definition in re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>",
                                           block, flags=re.S | re.I):
            add_row([re.sub(r"<[^>]+>", " ", term), re.sub(r"<[^>]+>", " ", definition)])
        cleaned = cleaned.replace(block, "\n")

    # Markdown pipe tables: only treat a block as a table when it has a
    # separator row or a recognizable header row. Running headers that happen
    # to contain pipes stay as ordinary text.
    blocks = []
    current_block = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.count("|") >= 2 and line.strip():
            current_block.append(line)
        elif current_block:
            blocks.append(current_block)
            current_block = []
    if current_block:
        blocks.append(current_block)
    for block in blocks:
        if len(block) < 2:
            continue
        first_cells = [c.strip() for c in block[0].strip().strip("|").split("|")]
        separator = all(re.fullmatch(r":?-{2,}:?", c or "") for c in
                        [c.strip() for c in block[1].strip().strip("|").split("|")] if c)
        header_map = _map_header(first_cells)
        if not separator and not header_map:
            continue
        body = block[2:] if separator else block[1:]
        for line in body:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells if c):
                continue
            add_row(cells, header_map)
            cleaned = cleaned.replace(line, "\n")
        if header_map:
            cleaned = cleaned.replace(block[0], "\n")
    return rows, cleaned


def _map_header(cells):
    mapping = {}
    for index, cell in enumerate(cells):
        key = _WS.sub(" ", (cell or "").strip().lower())
        key = key.strip("*_ ")
        if key in _NAME_COLUMNS and "name" not in mapping:
            mapping["name"] = index
        elif key in _DESC_COLUMNS and "description" not in mapping:
            mapping["description"] = index
        elif key in _CATEGORY_COLUMNS and "category" not in mapping:
            mapping["category"] = index
        elif key in _INDEX_COLUMNS and "index" not in mapping:
            mapping["index"] = index
    if "name" in mapping and ("description" in mapping or "category" in mapping):
        return mapping
    return None


_BULLET_CHARS = "\u2022\u2023\u25aa\u25cf\u25e6\u2043\uf0b7\uf0a7\uf076\uf0d8-*+"


def _clean_name(value):
    value = _WS.sub(" ", value or "").strip()
    value = value.lstrip(_BULLET_CHARS + " ").strip()
    value = re.sub(r"^(?:category|section|group|cluster|pack)\s*(?:no\.?\s*)?"
                   r"\d{0,3}\s*[:\-\u2013\u2014.]\s*", "", value, flags=re.I)
    value = re.sub(r"^[\d]{1,3}\s*[.)-]\s*", "", value)
    value = re.sub(r"^(?:skill\s*name|skill|capability|tool\s*name|name)\s*[:\-]\s*",
                   "", value, flags=re.I)
    return value.strip(" -:.–—|\"'")


def _clean_desc(value):
    value = _WS.sub(" ", value or "").strip()
    value = re.sub(r"^(?:description|desc|what it does|purpose)\s*[:\-]\s*", "",
                   value, flags=re.I)
    value = value.strip("|").strip()
    return value.rstrip("-\u2013\u2014 ").strip()


def _plausible_name(name):
    if not name or len(name) < 3 or len(name) > 120:
        return False
    if "|" in name or re.match(r"^\d+\s", name):
        return False
    lowered = name.lower().strip(" .:")
    if lowered in _HEADING_IGNORE or lowered in _NOISE_NAMES:
        return False
    if re.fullmatch(r"[\d\s.,%()+/-]+", name):
        return False
    if any("\uf000" <= ch <= "\uf8ff" for ch in name):
        return False
    if name.endswith(","):
        return False
    tail = re.split(r"[\s/]", name)[-1].strip(".,:;!?").lower()
    if tail in {"and", "or", "the", "a", "an", "of", "to", "in", "into", "with",
                "for", "while", "from", "as", "at", "on", "by"}:
        return False
    if len(name.split()) > 14:
        return False
    return True


_NOISE_NAMES = {
    "inputs", "outputs", "controls", "validation", "recovery", "notes", "purpose",
    "description", "total", "total skills", "contents", "index", "category index",
    "catalog purpose", "recommended skill contract", "catalog boundary",
    "implementation grouping suggestions", "skills", "skill name", "summary",
    "overview", "introduction", "how to use", "example", "examples",
}


def _boilerplate_keys(lines):
    """Identify repeated page furniture (headers, footers, running titles)."""
    counts = {}
    for text, kind in lines:
        if kind != "text" or len(text) < 4:
            continue
        key = re.sub(r"\d{1,4}", "#", text.lower())
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= 3}


def parse_capabilities(raw_text, *, source_label="<input>", heading_mode="auto",
                       default_category=None, merge_by_name=True):
    """Parse arbitrary capability text into the catalog structure.

    Handles numbered lists ("1. Skill Name: X" + "Description: Y"), bullet
    lists ("- X: Y"), Markdown headings as categories, Markdown/HTML tables,
    wrapped PDF text, and page furniture from paginated documents.
    """
    table_rows, body_text = extract_tables(raw_text)
    lines = strip_markup_lines(body_text)
    boilerplate = _boilerplate_keys(lines)

    explicit_category = any(
        _CATEGORY_RE.match(text) and not re.match(r"^\s*\d{1,3}\s*[.)]", text)
        for text, kind in lines
        if kind == "text" and _CATEGORY_RE.match(text)
    )
    use_headings = heading_mode == "always" or (
        heading_mode == "auto" and not explicit_category)

    categories = []
    current = None
    pending = None        # skill whose description is still being accumulated
    last_number = None

    def open_category(name, number=None):
        nonlocal current
        cleaned = _clean_name(name)
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered in _HEADING_IGNORE or lowered in _NOISE_NAMES:
            return None
        for existing in categories:
            if existing["name"].lower() == cleaned.lower():
                current = existing
                return current
        current = {
            "number": number or len(categories) + 1,
            "name": cleaned,
            "slug": RT.slugify(cleaned, max_length=72),
            "skills": [],
        }
        categories.append(current)
        return current

    def ensure_skill(name, number=None, description=""):
        nonlocal current, pending, last_number
        name = _clean_name(name)
        if not _plausible_name(name):
            pending = None
            return None
        if current is None:
            open_category(default_category or "uncategorized")
        if current is None:
            pending = None
            return None
        if merge_by_name:
            for item in current["skills"]:
                if item["name"].lower() == name.lower():
                    pending = item
                    if description:
                        item["description"] = _clean_desc(description)
                    last_number = number if number is not None else last_number
                    return item
        entry = {
            "number": number if number is not None else len(current["skills"]) + 1,
            "name": name,
            "description": _clean_desc(description),
        }
        current["skills"].append(entry)
        pending = entry
        last_number = number if number is not None else last_number
        return entry

    def append_description(text):
        """Append a wrapped line to the pending description."""
        nonlocal pending
        if pending is None:
            return False
        text = _clean_desc(text)
        if not text:
            return False
        existing = pending.get("description", "")
        if existing and existing.rstrip().endswith((".", "!", "?", ":")):
            pending = None      # the previous description is complete
            return False
        if len(existing) >= 1200:
            pending = None
            return True
        pending["description"] = (existing + " " + text).strip()[:1200]
        if pending["description"].rstrip().endswith((".", "!", "?", ":")):
            pending = None
        return True

    if default_category:
        open_category(default_category)

    index = 0
    total_lines = len(lines)
    while index < total_lines:
        text, kind = lines[index]
        index += 1
        if not text:
            continue
        key = re.sub(r"\d{1,4}", "#", text.lower())
        if key in boilerplate:
            continue

        if kind == "heading" and use_headings:
            title = text.strip(" #")
            heading_category = _CATEGORY_RE.match(title)
            if heading_category and not re.match(r"^\s*\d{1,3}\s*[.)]", title):
                title = heading_category.group(2)
            window = [t for t, k in lines[index:index + 6] if t]
            looks_like_container = bool(
                re.search(r"^\d{1,3}\s*[.)]\s*\S", " ".join(window[:2]) or "")
                or any(_CATEGORY_RE.match(w) for w in window[:2])
                or any(_is_skill_line(w) for w in window[:3]))
            if title and looks_like_container and not re.fullmatch(
                    r"index|contents|summary", title, re.I):
                if open_category(title) is not None:
                    pending = None
                    last_number = None
                    continue

        category_match = _CATEGORY_RE.match(text)
        if category_match and not re.match(r"^\s*\d{1,3}\s*[.)]", text):
            name = category_match.group(2)
            if name and not re.search(r"\d{2,}", name):
                number = int(category_match.group(1)) if category_match.group(1) else None
                if open_category(name, number) is not None:
                    pending = None
                    last_number = None
                    continue

        if text[0] in _BULLET_CHARS:
            bullet_body = text.lstrip(_BULLET_CHARS + " ").strip()
            name, description = _split_name_description(bullet_body)
            if description and _plausible_name(name):
                ensure_skill(name, None, description)
            elif _plausible_name(name) and current is not None:
                ensure_skill(name, None, description)
            continue

        numbered = re.match(r"^\s*(\d{1,3})\s*[.)\u2013\u2014:]\s*(\S.*)$", text)
        if numbered:
            number = int(numbered.group(1))
            rest = numbered.group(2)
            sequential = last_number is None or number == last_number + 1 or number == 1
            prefix = _SKILL_PREFIX_RE.match(rest)
            if sequential or prefix:
                body = prefix.group(1) if prefix else rest
                name, description = _split_name_description(body)
                if _plausible_name(name):
                    ensure_skill(name, number, description)
                    continue
            append_description(rest)
            continue

        prefix = _SKILL_PREFIX_RE.match(text)
        if prefix:
            name, description = _split_name_description(prefix.group(1))
            if _plausible_name(name):
                ensure_skill(name, None, description)
                continue

        if _DESC_PREFIX_RE.match(text) and (pending is not None or current is not None):
            appended = append_description(_DESC_PREFIX_RE.match(text).group(1))
            if not appended and pending is None:
                continue
            continue

        if pending is not None:
            # Wrapped continuation of the current description.
            if kind == "heading":
                pending = None
                continue
            append_description(text)
            continue

        name, description = _split_name_description(text)
        if description and _plausible_name(name):
            ensure_skill(name, None, description)
            continue
        # A bare name on its own line, with the description following.
        if _plausible_name(text) and len(text.split()) <= 12 and current is not None:
            lookahead = [t for t, k in lines[index:index + 1] if t]
            if lookahead and _DESC_PREFIX_RE.match(lookahead[0]):
                ensure_skill(text, None, "")
                continue

    # category column / table rows
    for row in table_rows:
        if row["category"]:
            open_category(row["category"])
        else:
            if current is None and default_category:
                open_category(default_category)
        ensure_skill(row["name"], row["number"], row["description"])

    categories = [c for c in categories if c["skills"]]
    for position, category in enumerate(categories, start=1):
        category.setdefault("number", position)
        seen = {}
        for skill in category["skills"]:
            base = RT.slugify(skill["name"], max_length=56)
            seen[base] = seen.get(base, 0) + 1
            skill["slug"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
            if not skill.get("description"):
                skill["description"] = f"Implements the {skill['name']} capability."
    return {
        "catalog_version": "1.0.0",
        "source": source_label,
        "categories": categories,
    }


def _split_name_description(text):
    """Split 'Name: description' / 'Name - description' into its two parts."""
    text = text.strip()
    prefix = _SKILL_PREFIX_RE.match(text)
    if prefix:
        text = prefix.group(1)
    inline = re.split(r"\bdescription\s*[:\-]\s*", text, maxsplit=1, flags=re.I)
    if len(inline) > 1:
        return _clean_name(inline[0]), _clean_desc(inline[1])
    split = re.match(r"^(.{3,110}?)\s*[:\-\u2013\u2014]\s+(.{15,})$", text)
    if split and not re.fullmatch(r"[\d\s.,%()+/-]+", split.group(1)):
        return _clean_name(split.group(1)), _clean_desc(split.group(2))
    return _clean_name(text), ""


def _is_skill_line(line):
    if re.match(r"^\s*\d{1,3}\s*[.)\u2013]\s*\S", line):
        return True
    if _SKILL_PREFIX_RE.match(line):
        return True
    split = re.match(r"^(.{3,110}?)\s*[:\-\u2013\u2014]\s+(.{15,})$", line)
    if split and not _CATEGORY_RE.match(line):
        return _plausible_name(_clean_name(split.group(1)))
    return False


def _looks_like_skill_numbered(line):
    return bool(re.match(r"^\s*\d{1,3}\s*[.)]\s*[A-Za-z]", line))


def catalog_stats(catalog):
    categories = catalog.get("categories", [])
    skills = [s for c in categories for s in c.get("skills", [])]
    with_desc = [s for s in skills if len(s.get("description", "")) >= 15]
    return {
        "categories": len(categories),
        "capabilities": len(skills),
        "with_description": len(with_desc),
        "without_description": len(skills) - len(with_desc),
        "names": [s["name"] for s in skills],
    }


# ---------------------------------------------------------------------------
# recipes and blueprint generation
# ---------------------------------------------------------------------------

def load_recipes(assets_dir=None):
    recipes, path = RT.load_asset_json("asset-build-recipes.json", assets_dir)
    return recipes, path


def normalize_key(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def resolve_recipe(skill_name, description, category_slug, recipes):
    """Pick the recipe for one capability and report how it was chosen."""
    target = normalize_key(skill_name)
    for rule in recipes.get("name_matches", []):
        for candidate in rule.get("any", []):
            key = normalize_key(candidate)
            if key and (key == target or key in target or target in key):
                return rule["recipe"], "name-match"
    blob = normalize_key(f"{skill_name} {description}")
    for rule in recipes.get("keyword_rules", []):
        for keyword in rule.get("any", []):
            if normalize_key(keyword) in blob:
                return rule["recipe"], "keyword"
    for slug, recipe_id in recipes.get("category_defaults", {}).items():
        if slug == category_slug:
            return recipe_id, "category-default"
    return "voice-profile-spec", "fallback"


def tool_commands_for(recipe, context):
    """Concrete, relative commands that exercise the recipe for one capability."""
    entry = recipe.get("artifact", "spec")
    commands = [
        f"python3 voice-capability-toolkit/tools-catalog-builder.py validate --input {context['spec_json']}",
    ]
    ops = recipe.get("analysis_ops") or []
    if ops:
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-analysis.py metrics "
            f"--input <rendered.wav> --ops {','.join(ops)}")
    if recipe.get("rendering"):
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-synthesis.py render-brief "
            f"--input {context['brief']} --out-dir {context['audio_dir']}")
    if recipe.get("gates") and any("consent" in g or "real-person" in g for g in recipe["gates"]):
        commands.append(
            "python3 voice-capability-toolkit/tools-voice-profile.py check-use "
            f"--input {context['profile']} --request {context['request']}")
        commands.append(
            "python3 voice-capability-toolkit/tools-voice-profile.py verify-consent "
            f"--input {context['consent']} --request {context['request']}")
    if recipe.get("artifact") in ("delivery-format-preset", "production-workflow-brief"):
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-delivery.py encode "
            f"--input <master.wav> --preset podcast --out-dir {context['audio_dir']}")
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-delivery.py validate-package "
            f"--input {context['audio_dir']}")
    if recipe.get("artifact") == "pronunciation-toolkit":
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-synthesis.py apply-dictionary "
            f"--input {context['dict']} --text \"<script>\"")
    if recipe.get("artifact") == "qa-evaluation-plan":
        commands.append(
            "python3 voice-capability-toolkit/tools-audio-delivery.py checklist "
            f"--name {context['checklist']} --input <master.wav>")
    return commands


def build_blueprint(catalog, recipes, *, umbrella=None, emit="umbrella",
                    assets_dir=None, deadline=None):
    """Turn a catalog into one or more implementation blueprints."""
    categories = catalog.get("categories", [])
    if not categories:
        raise RT.ToolError("Catalog contains no categories; run 'extract' first.",
                           code=RT.EXIT_INVALID_INPUT)
    stats = catalog_stats(catalog)
    tools = recipes.get("artifact_conventions", {}).get("entry_points", {})

    if umbrella:
        umbrella_name = umbrella
        umbrella_slug = RT.slugify(umbrella, max_length=56)
    else:
        shared = os.path.commonprefix([normalize_key(c["name"]) for c in categories]).strip() \
            if len(categories) > 1 else normalize_key(categories[0]["name"])
        if len(categories) == 1:
            umbrella_name = categories[0]["name"]
        elif shared and len(shared.split()) >= 2:
            umbrella_name = shared.title()
        else:
            umbrella_name = "voice-capability-toolkit"
        umbrella_slug = RT.slugify(umbrella_name, max_length=56)

    grouped = {}
    unmapped = []
    for category in categories:
        if deadline:
            deadline.check("recipe mapping")
        for skill in category.get("skills", []):
            recipe_id, how = resolve_recipe(skill["name"], skill.get("description", ""),
                                            category.get("slug", ""), recipes)
            recipe = recipes.get("recipes", {}).get(recipe_id)
            if recipe is None:
                unmapped.append(skill["name"])
                recipe_id, how = "voice-profile-spec", "fallback"
                recipe = recipes["recipes"][recipe_id]
            if how == "fallback":
                unmapped.append(skill["name"])
            grouped.setdefault(category["name"], []).append({
                "category": category["name"],
                "category_number": category.get("number"),
                "category_slug": category.get("slug"),
                "name": skill["name"],
                "description": skill.get("description", ""),
                "recipe": recipe_id,
                "recipe_basis": how,
                "recipe_data": recipe,
            })

    blueprints = []
    if emit == "per-capability":
        for category_name, entries in grouped.items():
            for entry in entries:
                blueprints.append(_render_blueprint(
                    umbrella_name, umbrella_slug, [entry], tools, stats,
                    single=True))
    else:
        for category_name, entries in grouped.items():
            blueprints.append(_render_blueprint(
                umbrella_name, umbrella_slug, entries, tools, stats,
                category_name=category_name, single=False))

    return blueprints, {"umbrella": umbrella_name, "umbrella_slug": umbrella_slug,
                        "unmapped": sorted(set(unmapped))}


def _render_blueprint(umbrella_name, umbrella_slug, entries, tools, stats, *,
                      category_name=None, single=False):
    first = entries[0]
    if single:
        slug = RT.slugify(f"{first['name']}-spec", max_length=64)
        title = first["name"]
        scope = first["name"]
    else:
        slug = RT.slugify(f"{category_name}-blueprint", max_length=64)
        title = f"{category_name} - unified Skill Tool blueprint"
        scope = category_name
    recipes_used = sorted({e["recipe"] for e in entries})
    primary = entries[0]["recipe_data"]

    context_dict = "asset-demo-brand-dictionary.json"
    context = {
        "spec_json": f"voice-capability-toolkit/asset-{slug}.json",
        "brief": "voice-capability-toolkit/asset-fixtures.json",
        "profile": "voice-capability-toolkit/asset-fixtures.json",
        "request": "voice-capability-toolkit/asset-fixtures.json",
        "consent": "voice-capability-toolkit/asset-fixtures.json",
        "dict": context_dict,
        "audio_dir": f"out/{slug}",
        "checklist": "advertising",
    }

    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Umbrella:** `{umbrella_slug}`  ")
    lines.append(f"**Scope:** {scope}  ")
    lines.append(f"**Capabilities covered:** {len(entries)}  ")
    lines.append(f"**Recipes used:** {', '.join(recipes_used)}  ")
    lines.append("**Generated by:** `voice-capability-toolkit/tools-catalog-builder.py`  ")
    lines.append("**Status:** implementation plan (run the commands below to execute it)")
    lines.append("")
    lines.append("## 1. Deliverable")
    lines.append("")
    if single:
        lines.append(f"{first['name']}: {first['description']}")
        lines.append("")
        lines.append(f"Recipe shape: **{first['recipe']}** - {primary.get('summary', '')}")
    else:
        lines.append(f"One unified Skill Tool folder, `{umbrella_slug}/`, implementing every capability "
                     f"in {scope}. Capabilities share the runtime, profile, synthesis, delivery and "
                     f"analysis tools; each keeps its own responsibility, acceptance criteria and gate "
                     f"so nothing is merged away.")
        lines.append("")
        lines.append(f"Recipe shape: **{entries[0]['recipe']}** - {primary.get('summary', '')}")
    lines.append("")

    lines.append("## 2. Capability coverage")
    lines.append("")
    lines.append("| # | Capability | Responsibility | Recipe |")
    lines.append("|---|------------|----------------|--------|")
    for position, entry in enumerate(entries, start=1):
        desc = entry["description"].replace("|", "/").strip()
        lines.append(f"| {position} | **{entry['name']}** | {desc} | `{entry['recipe']}` |")
    lines.append("")

    lines.append("## 3. Measurable controls")
    lines.append("")
    for entry in entries:
        focus = entry["recipe_data"].get("controls_focus", [])
        lines.append(f"- **{entry['name']}** (`{entry['recipe']}`): " +
                     (", ".join(f"`{f}`" for f in focus) if focus else "policy and record fields only"))
    lines.append("")

    defaults = primary.get("profile_defaults") or {}
    if defaults:
        lines.append("### Default profile baseline")
        lines.append("")
        lines.append("```json")
        lines.append(RT.dumps(defaults))
        lines.append("```")
        lines.append("")

    if primary.get("emotion_arc"):
        lines.append("### Emotion arc")
        lines.append("")
        lines.append("Map every beat to a preset in `asset-voice-presets.json` (`emotions` / `attitudes`), "
                     "then render the arc with the intensity curve shown in the beat sheet.")
        lines.append("")

    lines.append("## 4. Runnable commands")
    lines.append("")
    lines.append("All paths are relative to the future skill root (the folder that will hold `SKILL.md`).")
    lines.append("")
    lines.append("```bash")
    lines.append("# 0. Confirm the toolkit is intact")
    lines.append("python3 voice-capability-toolkit/tools-voice-profile.py capabilities")
    lines.append("")
    for command in tool_commands_for(primary, context):
        lines.append(command)
    lines.append("")
    lines.append("# 1. Validate the generated spec document")
    lines.append(f"python3 voice-capability-toolkit/tools-catalog-builder.py validate "
                 f"--input voice-capability-toolkit/asset-{slug}.json")
    lines.append("```")
    lines.append("")

    lines.append("## 5. Test plan")
    lines.append("")
    for entry in entries:
        checks = entry["recipe_data"].get("acceptance", [])
        lines.append(f"**{entry['name']}**")
        for check in checks:
            lines.append(f"- [ ] {check}")
        commands = tool_commands_for(entry["recipe_data"], context)
        for command in commands[:2]:
            lines.append(f"  - run: `{command}`")
        lines.append("")

    lines.append("## 6. Acceptance criteria")
    lines.append("")
    seen = set()
    for entry in entries:
        for check in entry["recipe_data"].get("acceptance", []):
            if check in seen:
                continue
            seen.add(check)
            lines.append(f"- {check}")
    lines.append("")

    lines.append("## 7. Safety and rights gates")
    lines.append("")
    gate_map = {
        "rights-error-if-real-person-without-consent": "Block when a real person is represented without a consent record: `tools-voice-profile.py verify-consent`.",
        "consent-required": "Require a consent record whose scope covers the requested use, project, territory and medium.",
        "approval-required-conversion": "Require `--approve` and a matching consent record before any conversion render.",
        "approval-required-publish": "Require explicit approval before publishing or releasing artifacts.",
        "disclosure-status-in-manifest": "Record the synthetic-voice disclosure in the delivery manifest.",
        "sensitive-content-review": "Route sensitive-domain uses through the policy in `asset-policy.json`.",
        "real-person-name-review": "Confirm pronunciation entries for real people are approved, not guessed.",
        "rights-territory-check": "Check territory and medium against the consent or licence scope.",
        "cultural-review": "Have a target-locale reviewer approve idiom, politeness and humour changes.",
        "musical-rights-check": "Confirm lyrics, melody and performance rights before rendering.",
        "inclusive-language-review": "Review language for inclusive and respectful wording.",
        "keep-source-unmodified": "Never overwrite source audio; write repairs to a new path.",
        "release-gate": "Run the release gate in `tools-audio-delivery.py validate-package` before delivery.",
        "fictional-identity-statement": "State that the voice is fictional and not a real person.",
        "no-identity-claims": "Never claim speaker identity from acoustic analysis alone.",
    }
    gates = []
    for entry in entries:
        for gate in entry["recipe_data"].get("gates", []):
            if gate not in gates:
                gates.append(gate)
    for gate in gates:
        lines.append(f"- **{gate}** - {gate_map.get(gate, 'Review before release.')}")
    if not gates:
        lines.append("- No additional gates; the default disclosure and provenance rules still apply.")
    lines.append("")
    lines.append("Approval is always explicit: `--approve` on the command line, or "
                 "`VOICE_TOOLKIT_APPROVAL_TOKEN` in a pre-approved pipeline.")
    lines.append("")

    lines.append("## 8. Provenance")
    lines.append("")
    fingerprint = RT.stable_fingerprint([e["name"] for e in entries] + recipes_used, length=20)
    lines.append(f"- Recipe set fingerprint: `{fingerprint}`")
    lines.append(f"- Catalogue source: `{scope}`")
    lines.append(f"- Recipe basis per capability: " +
                 ", ".join(f"{e['name']} -> {e['recipe']} ({e['recipe_basis']})" for e in entries[:6]) +
                 (" ..." if len(entries) > 6 else ""))
    lines.append(f"- Runtime version: `{RT.TOOLKIT_VERSION}`")
    lines.append("")
    body = "\n".join(lines)

    spec_json = {
        "status": "ok",
        "artifact": primary.get("artifact", "spec"),
        "umbrella": umbrella_slug,
        "scope": scope,
        "slug": slug,
        "capability_count": len(entries),
        "recipes_used": recipes_used,
        "fingerprint": fingerprint,
        "tools": tools,
        "capabilities": [
            {
                "name": e["name"],
                "description": e["description"],
                "category": e["category"],
                "recipe": e["recipe"],
                "recipe_basis": e["recipe_basis"],
                "controls_focus": e["recipe_data"].get("controls_focus", []),
                "acceptance": e["recipe_data"].get("acceptance", []),
                "gates": e["recipe_data"].get("gates", []),
            }
            for e in entries
        ],
        "markdown": body,
    }
    return {"slug": slug, "title": title, "markdown": body, "json": spec_json}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_extract(args):
    raw, source = read_source(args.input, args.text)
    deadline = RT.Deadline(args.timeout)
    catalog = parse_capabilities(
        raw,
        source_label=os.path.basename(source) if source not in ("<--text>", "stdin") else source,
        heading_mode=args.heading_mode,
        default_category=args.category,
    )
    deadline.check("extract")
    stats = catalog_stats(catalog)
    if stats["capabilities"] == 0:
        raise RT.ToolError(
            "No capabilities were found in the input.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Use lines like '1. Skill Name: X' + 'Description: Y', "
                 "'- X: Y', or a Markdown table with name and description columns.",
            details={"source": source, "inspected_lines": len(strip_markup(raw))},
        )
    warnings = []
    if stats["without_description"]:
        warnings.append(f"{stats['without_description']} capabilities had no description and were given a placeholder.")
    problems = RT.validate_schema(catalog, RT.schema_for(
        RT.load_asset_json("asset-schemas.json", args.assets_dir)[0], "catalog"))
    result = {
        "status": "ok",
        "command": "extract",
        "source": source,
        "catalog": catalog,
        "stats": {k: v for k, v in stats.items() if k != "names"},
        "schema_problems": problems,
        "warnings": warnings,
    }
    if args.output_catalog:
        RT.write_json_file(args.output_catalog, catalog, overwrite=args.force)
        result["catalog_path"] = args.output_catalog
    RT.emit(result, output=args.output, quiet=args.quiet, overwrite=args.force)
    return RT.EXIT_OK


def cmd_build(args):
    deadline = RT.Deadline(args.timeout)
    schema_doc = RT.load_asset_json("asset-schemas.json", args.assets_dir)[0]
    recipes, recipes_path = load_recipes(args.assets_dir)
    if args.input and args.input.endswith(".json"):
        catalog = RT.read_json_file(args.input)
        source = args.input
        if isinstance(catalog, dict) and "categories" not in catalog and isinstance(
                catalog.get("catalog"), dict):
            catalog = catalog["catalog"]      # accept an extract envelope too
    else:
        raw, source = read_source(args.input, args.text)
        catalog = parse_capabilities(
            raw,
            source_label=os.path.basename(source),
            heading_mode=args.heading_mode,
            default_category=args.category,
        )
    RT.assert_valid(catalog, RT.schema_for(schema_doc, "catalog"), label="Catalog")

    if args.categories:
        wanted = {RT.slugify(c) for c in args.categories}
        catalog["categories"] = [c for c in catalog["categories"]
                                 if RT.slugify(c["name"]) in wanted]
        if not catalog["categories"]:
            raise RT.ToolError(
                "No categories matched --categories.",
                code=RT.EXIT_NOT_FOUND,
                details={"requested": args.categories,
                         "available": [c["name"] for c in RT.read_json_file(args.input)["categories"]]
                         if args.input and args.input.endswith(".json") else None},
            )
    if args.skills:
        wanted = {normalize_key(s) for s in args.skills}
        for category in catalog["categories"]:
            category["skills"] = [s for s in category["skills"]
                                  if normalize_key(s["name"]) in wanted
                                  or any(w == normalize_key(s.get("slug", "")) for w in wanted)]
        catalog["categories"] = [c for c in catalog["categories"] if c["skills"]]
        if not catalog["categories"]:
            raise RT.ToolError("No capabilities matched --skills.",
                               code=RT.EXIT_NOT_FOUND,
                               details={"requested": args.skills})

    limit = int(recipes.get("artifact_conventions", {}).get("entry_points", {}).get("_x", 0) or 0)
    del limit
    blueprints, meta = build_blueprint(
        catalog, recipes, umbrella=args.umbrella, emit=args.emit,
        assets_dir=args.assets_dir, deadline=deadline)
    if len(blueprints) > args.max_documents:
        raise RT.ToolError(
            f"Generated {len(blueprints)} documents, above --max-documents {args.max_documents}.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Use --emit umbrella, narrow with --categories, or raise --max-documents.",
        )

    out_dir = args.out_dir or "catalog-output"
    RT.ensure_dir(out_dir)
    written = []
    for blueprint in blueprints:
        md_path = os.path.join(out_dir, f"asset-{blueprint['slug']}.md")
        json_path = os.path.join(out_dir, f"asset-{blueprint['slug']}.json")
        if args.format in ("md", "both"):
            if os.path.exists(md_path) and not args.force:
                raise RT.ToolError(f"Refusing to overwrite {md_path}",
                                   code=RT.EXIT_INVALID_INPUT,
                                   hint="Pass --force to overwrite.")
            RT.atomic_write_text(md_path, blueprint["markdown"] + "\n")
            written.append(md_path)
        if args.format in ("json", "both"):
            RT.write_json_file(json_path, blueprint["json"], overwrite=args.force)
            written.append(json_path)

    result = {
        "status": "ok",
        "command": "build",
        "catalog_source": source if not args.input or not args.input.endswith(".json") else args.input,
        "umbrella": meta["umbrella"],
        "umbrella_slug": meta["umbrella_slug"],
        "emit": args.emit,
        "documents": [{"slug": b["slug"], "title": b["title"],
                       "capabilities": b["json"]["capability_count"],
                       "recipes": b["json"]["recipes_used"]} for b in blueprints],
        "files": written,
        "catalog_stats": {k: v for k, v in catalog_stats(catalog).items() if k != "names"},
        "unmapped_capabilities": meta["unmapped"],
        "next_commands": [
            f"python3 voice-capability-toolkit/tools-audio-synthesis.py capabilities",
            f"python3 voice-capability-toolkit/tools-voice-profile.py capabilities",
        ],
    }
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_validate(args):
    schema_doc = RT.load_asset_json("asset-schemas.json", args.assets_dir)[0]
    document = RT.read_json_file(args.input)
    target = args.schema or ("catalog" if "categories" in document else
                             "voice_profile" if "profile_id" in document else
                             "production_brief" if "segments" in document else
                             "delivery_manifest" if "files" in document else
                             "consent_record" if "consent_id" in document else
                             "usage_request" if "territory" in document else
                             "pronunciation_dictionary" if "entries" in document else None)
    if not target:
        raise RT.ToolError(
            "Could not infer which schema to validate against.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Pass --schema with one of: " +
                 ", ".join(sorted(schema_doc.get("$defs", {}))),
        )
    problems = RT.validate_schema(document, RT.schema_for(schema_doc, target))
    result = {
        "status": "ok" if not problems else "invalid",
        "command": "validate",
        "input": args.input,
        "schema": target,
        "valid": not problems,
        "problems": problems[:50],
        "problem_count": len(problems),
    }
    if problems:
        RT.emit(result, output=args.output, quiet=args.quiet)
        return RT.EXIT_INVALID_INPUT
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_lookup(args):
    catalog = RT.read_json_file(args.catalog)
    schema_doc = RT.load_asset_json("asset-schemas.json", args.assets_dir)[0]
    RT.assert_valid(catalog, RT.schema_for(schema_doc, "catalog"), label="Catalog")
    query = normalize_key(args.query) if args.query else ""
    categories = []
    for category in catalog["categories"]:
        if args.category and RT.slugify(args.category) not in (
                category["slug"], RT.slugify(category["name"])):
            continue
        hits = []
        for skill in category["skills"]:
            blob = normalize_key(f"{skill['name']} {skill.get('description', '')}")
            if not query or query in blob:
                hits.append(skill)
        if hits or not query:
            categories.append({"name": category["name"], "slug": category["slug"],
                               "matches": hits})
    total = sum(len(c["matches"]) for c in categories)
    if query and total == 0:
        raise RT.ToolError(
            f"No capability matched '{args.query}'.",
            code=RT.EXIT_NOT_FOUND,
            hint="Try a single keyword such as 'sing', 'loudness' or 'consent'.",
            details={"categories_searched": len(catalog["categories"])},
        )
    RT.emit({"status": "ok", "command": "lookup", "query": args.query,
             "match_count": total, "categories": categories},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG, CAPABILITIES,
        notes={"primary_entry_point": True,
               "sibling_tools": [
                   "tools-voice-profile.py", "tools-audio-synthesis.py",
                   "tools-audio-delivery.py", "tools-audio-analysis.py",
                   "tools-selftest.py", "tools-runtime-common.py"],
               "assets": ["asset-catalog.json", "asset-build-recipes.json",
                          "asset-schemas.json", "asset-voice-presets.json",
                          "asset-policy.json", "asset-checklists.json",
                          "asset-fixtures.json"]}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - primary entry point: parse capability lists "
        "and build recipe-driven implementation blueprints.",
        epilog="Examples:\n"
               "  python3 tools-catalog-builder.py extract --input catalog.txt "
               "--output-catalog catalog.json\n"
               "  python3 tools-catalog-builder.py build --input catalog.json "
               "--out-dir out --format both\n"
               "  python3 tools-catalog-builder.py lookup --catalog catalog.json "
               "--query loudness\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_extract = sub.add_parser("extract", help="Parse capability text into catalog JSON.")
    RT.add_input_args(p_extract, text_help="Inline capability list.")
    p_extract.add_argument("--output-catalog", help="Write the parsed catalog JSON here.")
    p_extract.add_argument("--category", help="Umbrella category when the source has none.")
    p_extract.add_argument("--heading-mode", choices=["auto", "always", "never"],
                           default="auto", help="Treat Markdown headings as categories.")
    RT.add_output_args(p_extract)
    RT.add_common_args(p_extract)
    p_extract.set_defaults(func=cmd_extract)

    p_build = sub.add_parser("build", help="Generate implementation blueprints.")
    RT.add_input_args(p_build, text_help="Inline capability list or catalog.")
    p_build.add_argument("--formats-input", dest="formats_input", help=argparse.SUPPRESS)
    p_build.add_argument("--emit", choices=["umbrella", "per-capability"],
                         default="umbrella",
                         help="One unified blueprint per category (default) or one "
                              "blueprint per capability.")
    p_build.add_argument("--umbrella", help="Umbrella Skill Tool name to print in the blueprints.")
    p_build.add_argument("--categories", nargs="+", help="Only these categories (name or slug).")
    p_build.add_argument("--skills", nargs="+", help="Only these capability names.")
    p_build.add_argument("--out-dir", default="catalog-output", help="Directory for generated specs.")
    p_build.add_argument("--format", choices=["md", "json", "both"], default="both",
                         help="Output formats (default both).")
    p_build.add_argument("--max-documents", type=int, default=60,
                         help="Safety limit on generated documents (default 60).")
    p_build.add_argument("--heading-mode", choices=["auto", "always", "never"], default="auto")
    p_build.add_argument("--category", help="Umbrella category when the source has none.")
    RT.add_output_args(p_build)
    RT.add_common_args(p_build)
    p_build.set_defaults(func=cmd_build)

    p_validate = sub.add_parser("validate", help="Validate a JSON document against a schema.")
    p_validate.add_argument("-i", "--input", required=True, help="JSON file to validate.")
    p_validate.add_argument("--schema", help="Schema name from asset-schemas.json.")
    RT.add_output_args(p_validate)
    RT.add_common_args(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_lookup = sub.add_parser("lookup", help="Search the catalog.")
    p_lookup.add_argument("--catalog", required=True, help="Catalog JSON path.")
    p_lookup.add_argument("--query", help="Keyword to search for.")
    p_lookup.add_argument("--category", help="Restrict to one category.")
    RT.add_output_args(p_lookup)
    RT.add_common_args(p_lookup)
    p_lookup.set_defaults(func=cmd_lookup)

    p_caps = sub.add_parser("capabilities", help="Print the machine-readable command index.")
    RT.add_output_args(p_caps)
    RT.add_common_args(p_caps)
    p_caps.set_defaults(func=cmd_capabilities)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    for attribute in ("timeout", "max_documents"):
        if not hasattr(args, attribute):
            setattr(args, attribute, 120.0 if attribute == "timeout" else 60)
    if not hasattr(args, "assets_dir"):
        args.assets_dir = None
    if not hasattr(args, "force"):
        args.force = False
    return args.func(args)


if __name__ == "__main__":
    sys.exit(RT.guard(main)())
