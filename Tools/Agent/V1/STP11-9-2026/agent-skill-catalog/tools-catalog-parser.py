#!/usr/bin/env python3
"""Catalog parsing and normalisation module.

Loaded by the primary entry point ``agent-skill-catalog/tools-skillcatalog.py``
through ``importlib`` (the file name uses a hyphen, so it is never imported with
a plain ``import`` statement).

Responsibilities
----------------
* Convert copied HTML into plain text (stdlib ``html.parser`` only).
* Detect the shape of a pasted catalog (PDF-style ``Skill Name:`` blocks,
  Markdown headings, bold lists, plain lists).
* Parse categories, skill names and descriptions into a JSON-native structure.
* Render the structure back out as JSON, Markdown, text or HTML.
* Enforce an input size ceiling and raise ``CatalogError`` with actionable text.

Only the standard library is used.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser

__all__ = [
    "CatalogError",
    "MAX_TEXT_BYTES",
    "catalog_stats",
    "detect_format",
    "flatten_skills",
    "html_to_text",
    "normalize_name",
    "normalize_ws",
    "parse_catalog",
    "read_text",
    "slugify",
    "strip_inline_tags",
    "to_html",
    "to_json",
    "to_markdown",
    "to_text",
    "tokenize",
]

MAX_TEXT_BYTES = 8 * 1024 * 1024  # 8 MiB ceiling for pasted catalog text

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "for", "from",
    "in", "into", "is", "it", "its", "of", "on", "or", "so", "that", "the",
    "their", "then", "to", "with", "without", "each", "one", "when", "while",
    "which", "who", "how", "what", "why", "not", "no", "do", "does", "can",
}


class CatalogError(ValueError):
    """Raised when catalog input cannot be read or parsed."""


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #
def read_text(path: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Read a catalog file with an explicit byte ceiling.

    ``path`` may be ``-`` for standard input.
    """
    if path == "-":
        import sys

        raw = sys.stdin.read()
        if len(raw.encode("utf-8", "replace")) > max_bytes:
            raise CatalogError(
                "stdin exceeds the %d byte input limit; split the catalog or raise --max-bytes"
                % max_bytes
            )
        return raw
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except FileNotFoundError:
        raise CatalogError("input file not found: %s" % path)
    except IsADirectoryError:
        raise CatalogError("input path is a directory, expected a file: %s" % path)
    except OSError as exc:
        raise CatalogError("cannot read input file %s: %s" % (path, exc))
    if len(data) > max_bytes:
        raise CatalogError(
            "input file %s exceeds the %d byte limit; pass --max-bytes to raise it"
            % (path, max_bytes)
        )
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


class _TextExtractor(HTMLParser):
    """Collect text from HTML, emitting one line per block-level element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401 - parser callback
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if data.strip():
            self.chunks.append(data)

    def text(self) -> str:
        return "".join(self.chunks)


def looks_like_html(raw: str) -> bool:
    """Heuristic: does this text carry real HTML markup?"""
    probe = raw[:4000].lower()
    if "<" not in probe:
        return False
    markers = (
        "<li", "<ul", "<ol", "<div", "<p>", "<p ", "<span", "<h1", "<h2", "<h3",
        "<table", "<br", "<strong", "<td", "<section", "<article", "<!doctype",
        "<html",
    )
    return any(marker in probe for marker in markers)


def html_to_text(raw: str) -> str:
    """Strip tags, CSS classes, data attributes and scripts from copied HTML."""
    extractor = _TextExtractor()
    try:
        extractor.feed(raw)
        extractor.close()
    except Exception as exc:  # pragma: no cover - defensive
        raise CatalogError("could not parse HTML input: %s" % exc)
    text = extractor.text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_inline_tags(text: str) -> str:
    """Remove leftover inline markup from a line of otherwise plain text."""
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"</?[A-Za-z][^>]{0,400}>", " ", text)
    return html.unescape(text)


def normalize_ws(text: str) -> str:
    """Collapse runs of whitespace into single spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_name(text: str) -> str:
    """Clean a candidate skill or category name for display and matching."""
    text = strip_inline_tags(text or "")
    text = re.sub(r"^\s*(?:#{1,6}|[-*+•])\s*", "", text)
    text = re.sub(r"^\s*\d{1,3}(?:\.\d{1,3})?[.)]?\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = normalize_ws(text)
    text = re.sub(r"^[\s:;,\-–—|]+", "", text)
    text = re.sub(r"[\s:;,\-–—|]+$", "", text)
    text = re.sub(r"\s*\((?:category|cat\.?)\s*\d+\)\s*$", "", text, flags=re.I)
    return text.strip().rstrip(".").strip()


def slugify(text: str) -> str:
    """Lowercase hyphenated ASCII slug matching ``^[a-z0-9]+(?:-[a-z0-9]+)*$``."""
    text = normalize_name(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "item"


def tokenize(text: str) -> list[str]:
    """Tokenise lowercased words, dropping stopwords and normalising suffixes."""
    words = re.findall(r"[a-z0-9][a-z0-9+#.-]*", (text or "").lower())
    tokens = []
    for word in words:
        word = word.strip(".-")
        if len(word) < 2 or word in _STOPWORDS:
            continue
        for suffix in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        if word not in tokens:
            tokens.append(word)
    return tokens


# --------------------------------------------------------------------------- #
# Line classification
# --------------------------------------------------------------------------- #
_RE_SKILL_MARKER = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\d{1,3}(?:\.\d{1,3})?[.)]\s*)?[*_`\s]*skill\s*name\s*[*_`\s]*:\s*(.+)$", re.I)
_RE_DESC_MARKER = re.compile(r"^\s*[*_`\s]*(?:description|what it does|summary)\s*[*_`\s]*:\s*(.*)$", re.I)
_RE_HEADING = re.compile(r"^\s*(#{1,6})\s*(.+?)\s*#*\s*$")
_RE_SUB_NUMBERED = re.compile(r"^(\d{1,3})\.(\d{1,3})[.)]?\s+(.+)$")
_RE_NUMBERED = re.compile(r"^(\d{1,3})[.)]\s+(.+)$")
_RE_BULLET_BOLD = re.compile(r"^\s*[-*+•]\s*\*\*(.+?)\*\*\s*(.*)$")
_RE_NUMBERED_BOLD = re.compile(r"^\s*(\d{1,3})[.)]\s*\*\*(.+?)\*\*\s*(.*)$")
_RE_BULLET_NAMED = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+([A-Z][^:—–\n]{2,80}?)\s*[:—–]\s+(.+)$")
_RE_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_RE_CATEGORY_SKILLS = re.compile(r"\s*\(\s*\d+\s+skills?\s*\)\s*$", re.I)

_SEP_LEAD = re.compile(r"^[\s—–\-:|]+")


def _clean_inline_desc(text: str) -> str:
    return _SEP_LEAD.sub("", normalize_ws(strip_inline_tags(text or "")))


def _plausible_name(name: str) -> bool:
    if not name:
        return False
    if len(name) < 3 or len(name) > 90:
        return False
    if "\n" in name:
        return False
    lowered = name.lower()
    if lowered.startswith(("http://", "https://", "www.")):
        return False
    if name.count(".") > 0 and re.search(r"\.\s", name):
        return False
    if name.endswith((".", ",", ";")):
        return False
    return 2 <= len(name.split()) <= 14


def _plausible_category(name: str) -> bool:
    """Category names may be a single word, unlike skill names."""
    if not name or len(name) < 3 or len(name) > 90:
        return False
    if name.endswith((".", ",", ";")):
        return False
    return 1 <= len(name.split()) <= 14


def detect_format(text: str) -> str:
    """Return one of ``catalog``, ``heading``, ``bold-list``, ``plain-list``, ``prose``."""
    sample = "\n".join(text.splitlines()[:400])
    if re.search(r"skill\s*name\s*:", sample, re.I):
        return "catalog"
    if re.search(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s*\*\*.+\*\*", sample, re.M):
        return "bold-list"
    if re.search(r"^\s*#{1,6}\s+\S", sample, re.M) and re.search(r"^\s*(?:\*\*)?description", sample, re.I | re.M):
        return "heading"
    if re.search(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+[A-Z].{2,80}?[:—–]\s+\S", sample, re.M):
        return "plain-list"
    return "prose"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _new_category(name: str, index: int) -> dict:
    name = normalize_name(name) or "Uncategorized"
    return {"index": index, "name": name, "slug": slugify(name), "skills": []}


def _push_skill(category: dict, name: str, description: str, warnings: list[str]) -> dict | None:
    name = normalize_name(name)
    if not _plausible_name(name):
        warnings.append("skipped implausible skill name: %r" % name[:120])
        return None
    skill = {
        "index": len(category["skills"]) + 1,
        "name": name,
        "slug": slugify(name),
        "description": normalize_ws(description),
    }
    category["skills"].append(skill)
    return skill


def parse_catalog(text: str, fmt: str = "auto") -> dict:
    """Parse catalog text into ``{"format", "categories", "skills", "warnings"}``.

    ``fmt`` is ``auto``, ``text``/``catalog``, ``markdown``/``heading``,
    ``list``/``bold-list``, ``plain-list`` or ``html``.
    """
    if not text or not text.strip():
        raise CatalogError("input is empty: supply a catalog, a category, or a skill list")

    cleaned_input = text
    if fmt in ("html", "auto-html"):
        cleaned_input = html_to_text(text)
        fmt = "auto"
    elif fmt == "auto" and looks_like_html(text):
        cleaned_input = html_to_text(text)

    if len(cleaned_input.encode("utf-8", "replace")) > MAX_TEXT_BYTES:
        raise CatalogError("catalog text exceeds the %d byte limit" % MAX_TEXT_BYTES)

    lines = [line.rstrip() for line in cleaned_input.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if fmt == "auto":
        fmt = detect_format(cleaned_input)
    aliases = {
        "text": "catalog", "pdf": "catalog", "catalog": "catalog",
        "markdown": "heading", "md": "heading", "heading": "heading",
        "list": "bold-list", "bold-list": "bold-list", "bullets": "bold-list",
        "plain-list": "plain-list", "plain": "plain-list",
        "prose": "catalog",
    }
    if fmt not in aliases:
        raise CatalogError("unknown --format %r (use auto, text, markdown, list, plain-list or html)" % fmt)
    fmt = aliases[fmt]

    warnings: list[str] = []
    categories: list[dict] = []
    front_matter: list[str] = []
    title: str | None = None
    headings_seen = False
    current_cat: dict | None = None
    current_skill: dict | None = None
    pending_cat: str | None = None

    front_matter_is_closed = False

    def ensure_cat() -> dict:
        nonlocal current_cat
        if current_cat is None:
            current_cat = _new_category(pending_cat or "Uncategorized", len(categories) + 1)
            categories.append(current_cat)
        return current_cat

    def next_nonblank(index: int) -> str:
        for candidate in lines[index + 1:]:
            if candidate.strip():
                return candidate.strip()
        return ""

    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        line = strip_inline_tags(line)
        if re.match(r"^agent skill catalog\s*\|", line, re.I):
            continue  # page headers from copied PDF text
        if re.match(r"^page\s+\d+(\s+of\s+\d+)?$", line, re.I):
            continue

        # 1. explicit "Skill Name:" marker
        match = _RE_SKILL_MARKER.match(line)
        if match:
            target = ensure_cat()
            current_skill = _push_skill(target, match.group(1), "", warnings)
            continue

        # 2. explicit "Description:" marker
        match = _RE_DESC_MARKER.match(line)
        if match:
            if current_skill is None:
                target = ensure_cat()
                current_skill = _push_skill(target, "Untitled capability %d" % (len(target["skills"]) + 1), "", warnings)
            body = match.group(1) or ""
            current_skill["description"] = normalize_ws((current_skill["description"] + " " + body).strip())
            continue

        # 3. markdown heading: "### Name" or "## 1.2 Name" or "## 1. Category"
        match = _RE_HEADING.match(line)
        if match:
            level = len(match.group(1))
            body = match.group(2).strip()
            first_heading = not headings_seen
            headings_seen = True
            if level == 1 and first_heading and not _RE_SUB_NUMBERED.match(body) and not _RE_NUMBERED.match(body):
                title = normalize_name(body)
                continue
            sub = _RE_SUB_NUMBERED.match(body)
            if sub:
                target = ensure_cat()
                current_skill = _push_skill(target, sub.group(3), "", warnings)
                continue
            numbered = _RE_NUMBERED.match(body)
            cat_name = _RE_CATEGORY_SKILLS.sub("", numbered.group(2) if numbered else body)
            cat_name = normalize_name(cat_name)
            if _plausible_category(cat_name) and not re.search(r"\bskill\s*name", cat_name, re.I):
                current_cat = _new_category(cat_name, len(categories) + 1)
                categories.append(current_cat)
                current_skill = None
                continue
            current_skill = _push_skill(ensure_cat(), body, "", warnings)
            continue

        # 4. numbered or bold list entries
        match = _RE_NUMBERED_BOLD.match(line) or _RE_BULLET_BOLD.match(line)
        if match:
            groups = match.groups()
            name = groups[1] if len(groups) == 3 else groups[0]
            rest = groups[2] if len(groups) == 3 else groups[1]
            target = ensure_cat()
            current_skill = _push_skill(target, name, _clean_inline_desc(rest or ""), warnings)
            continue

        match = _RE_BULLET_NAMED.match(line)
        if match:
            target = ensure_cat()
            current_skill = _push_skill(target, match.group(1), _clean_inline_desc(match.group(2)), warnings)
            continue

        # 5. numbered line that is not a skill marker -> category candidate
        match = _RE_NUMBERED.match(line)
        if match:
            candidate = normalize_name(_RE_CATEGORY_SKILLS.sub("", match.group(2)))
            upcoming = next_nonblank(line_index)
            follows_section_note = bool(re.match(r"^(this|these)\s+(category|section)", upcoming, re.I))
            opens_skill_block = bool(_RE_SKILL_MARKER.match(upcoming) or _RE_SUB_NUMBERED.match(upcoming or "-"))
            empty_category_open = current_cat is not None and not current_cat["skills"]
            restarts_numbering = match.group(1) == "1" and current_cat is not None and current_cat["skills"]
            if (_plausible_category(candidate) and not _RE_LIST_MARK.match(candidate)
                    and (follows_section_note or opens_skill_block or empty_category_open
                         or current_skill is None or restarts_numbering)):
                pending_cat = candidate
                current_cat = _new_category(candidate, len(categories) + 1)
                categories.append(current_cat)
                current_skill = None
                continue

        # 6. continuation of the current description
        if current_skill is not None and not _RE_LIST_MARK.match(line) and not line.startswith("#"):
            if re.search(r"\b(category index|how to use|implementation checklist)\b", line, re.I):
                current_skill = None
                continue
            current_skill["description"] = normalize_ws(
                (current_skill["description"] + " " + line).strip()
            )
            continue
        if not categories and not front_matter_is_closed:
            front_matter.append(line)
            continue
        warnings.append("ignored unrecognised line: %r" % line[:120])

    front_matter_is_closed = bool(categories)
    categories = [cat for cat in categories if cat["skills"]]
    if not categories:
        raise CatalogError(
            "no skill entries found in the input (detected format: %s). "
            "Expected lines such as '1. Skill Name: Example' with a 'Description:' line, "
            "'## Category' headings, or Markdown/HTML list items." % fmt
        )
    for cat in categories:
        cat["skills"] = [skill for skill in cat["skills"] if skill["name"]]
        for position, skill in enumerate(cat["skills"], start=1):
            skill["index"] = position
            skill["category"] = cat["name"]
            skill["category_index"] = cat["index"]
            skill["category_slug"] = cat["slug"]

    skills = flatten_skills({"categories": categories})
    return {
        "format": fmt,
        "title": title or "Skill Catalog",
        "front_matter": front_matter,
        "category_count": len(categories),
        "skill_count": len(skills),
        "categories": categories,
        "skills": skills,
        "warnings": warnings,
    }


def flatten_skills(catalog: dict) -> list[dict]:
    """Return a flat list of skill records (already flat input is accepted)."""
    if "skills" in catalog and isinstance(catalog["skills"], list) and catalog["skills"]:
        return catalog["skills"]
    flat = []
    for cat in catalog.get("categories", []):
        for skill in cat.get("skills", []):
            record = dict(skill)
            record.setdefault("category", cat.get("name"))
            record.setdefault("category_index", cat.get("index"))
            record.setdefault("category_slug", cat.get("slug"))
            flat.append(record)
    return flat


def catalog_stats(catalog: dict) -> dict:
    """Summarise a parsed catalog."""
    skills = flatten_skills(catalog)
    per_category = [
        {"category_index": cat["index"], "category": cat["name"], "skills": len(cat["skills"])}
        for cat in catalog.get("categories", [])
    ]
    words = sum(len((skill.get("description") or "").split()) for skill in skills)
    vocab = sorted({token for skill in skills for token in tokenize(skill["name"] + " " + (skill.get("description") or ""))})
    return {
        "format": catalog.get("format", "unknown"),
        "categories": len(catalog.get("categories", [])),
        "skills": len(skills),
        "description_words": words,
        "average_description_words": round(words / len(skills), 1) if skills else 0,
        "vocabulary_size": len(vocab),
        "per_category": per_category,
        "warnings": catalog.get("warnings", []),
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def to_json(catalog: dict, indent: int = 2) -> str:
    """Render the parsed catalog as JSON."""
    payload = {
        "format": catalog.get("format"),
        "category_count": len(catalog.get("categories", [])),
        "skill_count": len(flatten_skills(catalog)),
        "categories": catalog.get("categories", []),
        "warnings": catalog.get("warnings", []),
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def to_markdown(catalog: dict) -> str:
    """Render the canonical Markdown catalog format (round-trips through the parser)."""
    out = ["# %s" % catalog.get("title", "Skill Catalog"), ""]
    out.append("Canonical capability data: %d skills across %d categories."
               % (len(flatten_skills(catalog)), len(catalog.get("categories", []))))
    out.append("")
    for cat in catalog.get("categories", []):
        out.append("## %d. %s" % (cat["index"], cat["name"]))
        out.append("")
        for skill in cat["skills"]:
            out.append("%d. Skill Name: %s" % (skill["index"], skill["name"]))
            out.append("Description: %s" % skill["description"])
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_text(catalog: dict) -> str:
    """Render a compact plain-text listing."""
    out = []
    for cat in catalog.get("categories", []):
        out.append("%d. %s" % (cat["index"], cat["name"]))
        for skill in cat["skills"]:
            out.append("  %d.%d %s :: %s" % (cat["index"], skill["index"], skill["name"], skill["description"]))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_html(catalog: dict, title: str = "Skill Catalog") -> str:
    """Render a self-contained HTML index (inline CSS, no external assets)."""
    rows = []
    for cat in catalog.get("categories", []):
        rows.append('<tr class="cat"><th colspan="3">%d. %s</th></tr>' % (cat["index"], html.escape(cat["name"])))
        for skill in cat["skills"]:
            rows.append(
                "<tr><td>%d.%d</td><td>%s</td><td>%s</td></tr>"
                % (
                    cat["index"],
                    skill["index"],
                    html.escape(skill["name"]),
                    html.escape(skill["description"]),
                )
            )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>%s</title><style>"
        "body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#182026}"
        "table{border-collapse:collapse;width:100%%}td,th{border-bottom:1px solid #dbe2e8;"
        "padding:.45rem .6rem;text-align:left;vertical-align:top}"
        "tr.cat th{background:#f3f6f9;font-size:1rem;padding-top:1rem}"
        "td:first-child{color:#5c7080;white-space:nowrap;width:4.5rem}"
        "</style></head><body><h1>%s</h1><table>%s</table></body></html>\n"
        % (html.escape(title), html.escape(title), "\n".join(rows))
    )
