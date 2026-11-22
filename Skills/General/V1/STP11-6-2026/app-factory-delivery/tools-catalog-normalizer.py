#!/usr/bin/env python3
"""Catalog normalizer for the app-factory-delivery Skill Tool.

Reads a capability / Skill Tool list supplied as plain text, Markdown or copied
HTML and produces a normalized catalog JSON document.  It ignores HTML tags,
attributes, CSS classes, data attributes, numbering marks and stray spacing, and
extracts the real category names, Skill Tool names and descriptions.

Two ways to use it:

    python3 tools-catalog-normalizer.py --input list.html --out my-catalog.json
    import module -> parse_text(text, ...), parse_file(path, ...)

Output schema (schema_version 1.0):

    {
      "schema_version": "1.0",
      "source": {"kind": "...", "path": "...", "sha256": "..."},
      "counts": {"categories": N, "skills": N, "ignored": N, "description_missing": N},
      "categories": [
        {"index": 1, "number": "1", "name": "...", "slug": "...",
         "skills": [{"index": 1, "number": "1", "name": "...", "slug": "...",
                     "description": "...", "description_source": "explicit|inline|missing"}]}
      ],
      "ignored": [{"reason": "...", "text": "..."}]
    }

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from html.parser import HTMLParser

SCHEMA_VERSION = "1.0"

DEFAULT_LIMITS = {
    "max_input_bytes": 4000000,
    "max_skills": 20000,
    "max_name_chars": 200,
    "max_description_chars": 2000,
}

# Page / export furniture that carries no capability data.
FURNITURE_PATTERNS = [
    re.compile(r"^={2,}\s*page\b", re.I),
    re.compile(r"^agent skill catalog\b\s*\|", re.I),
    re.compile(r"^agent skill catalog\s*\|\s*\d+$", re.I),
    re.compile(r"^-{3,}$"),
    re.compile(r"^page\s+\d+(\s+of\s+\d+)?$", re.I),
]

# Region markers after which numbered lines are documentation, not capabilities.
APPENDIX_PATTERN = re.compile(r"^(appendix|annex|references?|glossary|changelog|bibliography)\b", re.I)

RE_SKILL = re.compile(
    r"^(?:[-*\u2022\u25aa\u25cf]\s*)?(?:\(?\d{1,3}\)?\s*[.)]?\s*)?(?:skill\s*(?:tool\s*)?name|skill|tool\s*name|name)\s*:\s*(?P<name>.+)$",
    re.I,
)
RE_DESC = re.compile(
    r"^(?:[-*\u2022\u25aa\u25cf]\s*)?(?:\(?\d{1,3}\)?\s*[.)]?\s*)?(?:short\s+)?description\s*:\s*(?P<text>.+)$",
    re.I,
)
RE_CATEGORY_EXPLICIT = re.compile(
    r"^(?:\*\*)?(?:category|section|group|domain)(?:\*\*)?\s*:\s*(?P<text>.+)$", re.I
)
RE_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+)$")
RE_NUMBERED = re.compile(r"^\(?(?P<num>\d{1,3})\)?\s*[.)]\s+(?P<text>.+)$")
RE_BULLET = re.compile(r"^(?:[-*\u2022\u25aa\u25cf]|\d{1,3}[.)])\s+(?P<text>.+)$")
RE_SEPARATOR = re.compile(r"\s+(?:\u2014|\u2013)\s+|\s+::\s+|\s+\|\s+|\s+-\s+")

MD_NOISE = re.compile(r"(\*\*|__|`+|~~)")


class _TextExtractor(HTMLParser):
    """Converts HTML to text: keeps content, drops tags, attributes and scripts."""

    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "section",
        "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt",
        "blockquote", "pre", "figure", "figcaption", "hr", "main", "nav", "aside",
    }
    SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(markup: str) -> str:
    """Strip markup (tags, attributes, CSS, scripts) from an HTML fragment."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    line = line.replace("\u2019", "'").replace("\u2018", "'")
    line = line.replace("\u201c", '"').replace("\u201d", '"')
    line = MD_NOISE.sub("", line)
    line = re.sub(r"[ \t]+", " ", line).strip()
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    return line


def slugify(text: str) -> str:
    """Lowercase ASCII slug: letters, digits and single hyphens."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


RE_NUMBER_MARK = re.compile(r"^(?:\(?\d{1,3}\)?\s*[.)]\s*)+")
RE_DESC_PREFIX = re.compile(r"^(?:short\s+)?(?:description|desc)\s*[:\-]\s*", re.I)
RE_NAME_PREFIX = re.compile(r"^(?:capability|skill(?:\s+tool)?|tool|title|name)\s*[:\-]\s*", re.I)


def _strip_numbering(text: str) -> str:
    """Remove repeated leading numbering marks such as '1. ', '(2) ', '3) '."""
    cleaned = RE_NUMBER_MARK.sub("", (text or "").strip())
    return cleaned.strip().strip("\u2022\u25aa-:").strip() or (text or "").strip()


def _split_name_description(text: str):
    """Split 'Name — description' style text when the name looks short."""
    match = RE_SEPARATOR.search(text)
    if not match:
        return None
    name = _strip_numbering(RE_NAME_PREFIX.sub("", text[: match.start()].strip(" -:\u2014\u2013")))
    desc = RE_DESC_PREFIX.sub("", text[match.end():].strip())
    if not name or not desc:
        return None
    if len(name) > 80 or len(name.split()) > 9 or len(desc) < 12:
        return None
    return name, desc


def _classify(line: str):
    """Return (kind, payload) for a cleaned line.

    kind is one of: skill, desc, category, item, text.
    """
    match = RE_SKILL.match(line)
    if match:
        return "skill", _strip_numbering(match.group("name"))

    match = RE_DESC.match(line)
    if match:
        return "desc", match.group("text").strip()

    match = RE_CATEGORY_EXPLICIT.match(line)
    if match:
        return "category", _strip_numbering(match.group("text"))

    match = RE_HEADING.match(line)
    if match:
        body = match.group("text").strip()
        numbered = RE_NUMBERED.match(body)
        body = _strip_numbering(numbered.group("text")) if numbered else body
        split = _split_name_description(body)
        if split:
            return "item", split
        return "category", body

    match = RE_NUMBERED.match(line)
    if match:
        body = _strip_numbering(match.group("text"))
        split = _split_name_description(body)
        if split:
            return "item", split
        if len(body) <= 140:
            return "category", body
        return "text", body

    match = RE_BULLET.match(line)
    if match:
        body = match.group("text").strip()
        split = _split_name_description(body)
        if split:
            return "item", split
        return "text", body

    return "text", line


def _detect_kind(text: str, path: str, hint: str = "auto") -> str:
    if hint and hint != "auto":
        return hint
    lowered = (path or "").lower()
    if lowered.endswith((".html", ".htm", ".xhtml")):
        return "html"
    if lowered.endswith((".md", ".markdown", ".mdx")):
        return "markdown"
    if re.search(r"<(?:p|div|li|ul|ol|h[1-6]|span|table)\b[^>]*>", text[:20000], re.I):
        return "html"
    if re.search(r"^\s{0,3}#{1,6}\s+\S", text, re.M):
        return "markdown"
    return "text"


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: limit - 1].rstrip() + "\u2026"
    return text


def parse_text(
    text: str,
    source_path: str = "",
    source_kind: str = "auto",
    default_category: str = "General Capabilities",
    limits: dict | None = None,
) -> dict:
    """Parse plain text, Markdown or HTML into a normalized catalog dict."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    if text is None:
        raise ValueError("input text is empty")
    if len(text.encode("utf-8", "replace")) > limits["max_input_bytes"]:
        raise ValueError(
            f"input exceeds max_input_bytes ({limits['max_input_bytes']}); "
            "split the source list and import it in parts"
        )

    kind = _detect_kind(text, source_path, source_kind)
    working = html_to_text(text) if kind == "html" else text
    working = working.replace("\r\n", "\n").replace("\r", "\n")

    records: list[dict] = []
    ignored: list[dict] = []
    preamble: list[str] = []
    current: dict | None = None
    in_appendix = False

    for raw_line in working.split("\n"):
        line = _clean_line(raw_line)
        if not line:
            continue
        if any(pattern.search(line) for pattern in FURNITURE_PATTERNS):
            continue
        if APPENDIX_PATTERN.match(line):
            in_appendix = True
            current = None
            continue

        kind_, payload = _classify(line)
        if in_appendix:
            if kind_ in {"skill", "desc", "item", "category"}:
                ignored.append({"reason": "appendix_region", "text": _truncate(payload if isinstance(payload, str) else payload[0], 200)})
            current = None
            continue

        if kind_ == "skill":
            current = {"kind": "skill", "name": payload, "description": "", "extra": "", "inline": False}
            records.append(current)
        elif kind_ == "desc":
            current = {"kind": "desc", "text": payload}
            records.append(current)
        elif kind_ == "category":
            current = {"kind": "category", "text": payload}
            records.append(current)
        elif kind_ == "item":
            name, desc = payload
            current = {"kind": "item", "name": name, "description": desc, "extra": "", "inline": True}
            records.append(current)
        else:  # continuation text
            if current is not None:
                key = "text" if current["kind"] == "desc" else "extra"
                current[key] = (current[key] + " " + payload).strip() if current.get(key) else payload
            elif records:
                records[-1].setdefault("extra", "")
                records[-1]["extra"] = (records[-1].get("extra", "") + " " + payload).strip()
            else:
                preamble.append(payload)

    categories: list[dict] = []
    pending_skills: list[dict] = []
    pending_desc: dict | None = None
    current_category: dict | None = None
    unassigned: list[dict] = []
    categoryless_blocks: list[dict] = []

    # Assemble records into categories.
    buffer: list[dict] = []
    for record in records:
        if record["kind"] == "category":
            if current_category is not None:
                if buffer:
                    current_category["_skills_raw"] = buffer
                    buffer = []
                else:
                    categoryless_blocks.append({"reason": "category_without_skill_entries", "text": current_category["name"]})
            category_name = (record["text"] + " " + record.get("extra", "")).strip()
            current_category = {"name": _truncate(category_name, limits["max_name_chars"]), "skills": [], "_skills_raw": []}
            categories.append(current_category)
            buffer = current_category["_skills_raw"]
            continue

        if record["kind"] in {"skill", "item"}:
            entry = {
                "name": record.get("name", ""),
                "description": record.get("description", ""),
                "description_source": "inline" if record.get("inline") else "explicit",
                "extra": record.get("extra", ""),
                "pending_desc": False,
            }
            if record["kind"] == "skill":
                split = _split_name_description(entry["name"])
                if split:
                    entry["name"], entry["description"] = split
                    entry["description_source"] = "inline"
                entry["pending_desc"] = True
            if current_category is None:
                unassigned.append(entry)
            else:
                buffer.append(entry)
            continue

        if record["kind"] == "desc":
            target = None
            if buffer and buffer[-1].get("pending_desc"):
                target = buffer[-1]
            elif unassigned and unassigned[-1].get("pending_desc"):
                target = unassigned[-1]
            if target is None:
                ignored.append({"reason": "orphan_description", "text": _truncate(record["text"], 200)})
                continue
            target["description"] = (target["description"] + " " + record["text"]).strip()
            target["description_source"] = "explicit"
            target["pending_desc"] = False
            continue

    # Close the trailing category buffer.
    if current_category is not None and buffer:
        current_category["_skills_raw"] = buffer
    if current_category is not None and not buffer:
        categoryless_blocks.append({"reason": "category_without_skill_entries", "text": current_category["name"]})

    def materialize(entries: list[dict]) -> list[dict]:
        out = []
        for entry in entries:
            name = _truncate(entry["name"], limits["max_name_chars"])
            if not name:
                continue
            description = entry["description"]
            source = entry["description_source"]
            if not description and entry.get("extra"):
                description = entry["extra"]
                source = "continuation"
            description = _truncate(description, limits["max_description_chars"])
            if not description:
                source = "missing"
            out.append({"name": name, "slug": slugify(name), "description": description, "description_source": source})
        return out

    final_categories: list[dict] = []
    if unassigned:
        skills = materialize(unassigned)
        if skills:
            final_categories.append(
                {
                    "number": "",
                    "name": _truncate(default_category, limits["max_name_chars"]),
                    "slug": slugify(default_category),
                    "skills": skills,
                }
            )
    for category in categories:
        skills = materialize(category.get("_skills_raw", []))
        if not skills:
            if not any(item.get("text") == category["name"] for item in categoryless_blocks):
                categoryless_blocks.append({"reason": "category_without_skill_entries", "text": category["name"]})
            continue
        final_categories.append(
            {
                "number": "",
                "name": category["name"],
                "slug": slugify(category["name"]),
                "skills": skills,
            }
        )

    # Numbering and duplicate-slug disambiguation.
    seen_slugs: dict[str, int] = {}
    total = 0
    for cat_index, category in enumerate(final_categories, start=1):
        category["index"] = cat_index
        for skill_index, skill in enumerate(category["skills"], start=1):
            skill["index"] = skill_index
            skill["number"] = str(skill_index)
            skill["category_slug"] = category["slug"]
            base = skill["slug"] or f"skill-{total + 1}"
            count = seen_slugs.get(base, 0)
            seen_slugs[base] = count + 1
            skill["slug"] = base if count == 0 else f"{base}-{count + 1}"
            total += 1

    for item in categoryless_blocks:
        ignored.append(item)
    for line in preamble[:40]:
        ignored.append({"reason": "preamble_text", "text": _truncate(line, 200)})

    if total == 0:
        raise ValueError(
            "no capability entries were found; expected markers such as "
            "'Skill Name: ...' / 'Description: ...', a numbered category list, "
            "or bullet items like '- Capability Name — what it does'"
        )
    if total > limits["max_skills"]:
        raise ValueError(f"entry count {total} exceeds max_skills ({limits['max_skills']})")

    missing_desc = sum(1 for cat in final_categories for skill in cat["skills"] if not skill["description"])
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "kind": kind,
            "path": source_path or "",
            "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        },
        "counts": {
            "categories": len(final_categories),
            "skills": total,
            "ignored": len(ignored),
            "description_missing": missing_desc,
        },
        "categories": final_categories,
        "ignored": ignored,
    }


def parse_file(
    path: str,
    source_kind: str = "auto",
    default_category: str = "General Capabilities",
    limits: dict | None = None,
) -> dict:
    """Read and parse a .txt/.md/.html file into a normalized catalog dict."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"input file not found: {path}")
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    size = os.path.getsize(path)
    if size > limits["max_input_bytes"]:
        raise ValueError(f"input file is {size} bytes, above max_input_bytes ({limits['max_input_bytes']})")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if not text.strip():
        raise ValueError(f"input file is empty: {path}")
    return parse_text(
        text,
        source_path=path,
        source_kind=source_kind,
        default_category=default_category,
        limits=limits,
    )


def merge_catalogs(base: dict, extra: dict) -> dict:
    """Merge two normalized catalogs, de-duplicating on category and skill slug."""
    merged = json.loads(json.dumps(base))
    merged.setdefault("schema_version", SCHEMA_VERSION)
    merged.setdefault("categories", [])
    merged["ignored"] = list(merged.get("ignored", []))
    merged["sources"] = list(merged.get("sources", []))

    for key in ("sources", "source"):
        value = extra.get(key)
        if value and value not in merged["sources"]:
            merged["sources"].append(value)

    by_slug = {category["slug"]: category for category in merged["categories"]}
    added_skills = 0
    added_categories = 0
    for category in extra.get("categories", []):
        target = by_slug.get(category["slug"])
        if target is None:
            target = {
                "index": len(merged["categories"]) + 1,
                "number": category.get("number", ""),
                "name": category["name"],
                "slug": category["slug"],
                "skills": [],
            }
            merged["categories"].append(target)
            by_slug[category["slug"]] = target
            added_categories += 1
        existing = {skill["slug"] for skill in target["skills"]}
        for skill in category.get("skills", []):
            slug = skill["slug"]
            if slug in existing:
                continue
            target["skills"].append(
                {
                    "index": len(target["skills"]) + 1,
                    "number": str(len(target["skills"]) + 1),
                    "name": skill["name"],
                    "slug": slug,
                    "category_slug": target["slug"],
                    "description": skill.get("description", ""),
                    "description_source": skill.get("description_source", "missing"),
                }
            )
            existing.add(slug)
            added_skills += 1

    total = sum(len(category["skills"]) for category in merged["categories"])
    merged["counts"] = {
        "categories": len(merged["categories"]),
        "skills": total,
        "ignored": len(merged["ignored"]),
        "description_missing": sum(
            1 for category in merged["categories"] for skill in category["skills"] if not skill.get("description")
        ),
    }
    merged["merge_report"] = {"added_categories": added_categories, "added_skills": added_skills}
    return merged


def load_catalog(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
        raise ValueError(f"not a normalized catalog (missing 'categories' list): {path}")
    return data


def _summarize(catalog: dict) -> str:
    counts = catalog.get("counts", {})
    lines = [
        f"categories: {counts.get('categories', 0)}  skills: {counts.get('skills', 0)}  "
        f"ignored: {counts.get('ignored', 0)}  missing descriptions: {counts.get('description_missing', 0)}"
    ]
    for category in catalog.get("categories", []):
        lines.append(f"  [{category.get('index')}] {category['name']} ({len(category['skills'])} skills)")
    if catalog.get("merge_report"):
        report = catalog["merge_report"]
        lines.append(
            f"  merge: +{report['added_categories']} categories, +{report['added_skills']} skills"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-catalog-normalizer.py",
        description=(
            "Normalize a capability / Skill Tool list supplied as plain text, Markdown or copied HTML "
            "into a catalog JSON document (categories, names, descriptions)."
        ),
        epilog=(
            "examples:\n"
            "  python3 tools-catalog-normalizer.py --input list.html --out my-catalog.json\n"
            "  python3 tools-catalog-normalizer.py --input catalog.txt --merge asset-skill-catalog.json --out merged.json\n"
            "  python3 tools-catalog-normalizer.py --input list.md --counts-only\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="path to the .txt/.md/.html capability list")
    parser.add_argument("--out", help="write the normalized catalog JSON here (default: stdout)")
    parser.add_argument(
        "--format", default="auto", choices=["auto", "text", "markdown", "html"], help="input format (default: auto)"
    )
    parser.add_argument("--default-category", default="General Capabilities", help="category used for entries that appear before any category marker")
    parser.add_argument("--merge", help="merge the parsed result into an existing normalized catalog JSON")
    parser.add_argument("--counts-only", action="store_true", help="print a short summary instead of the full JSON")
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_LIMITS["max_input_bytes"], help="input size limit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    limits = dict(DEFAULT_LIMITS)
    limits["max_input_bytes"] = args.max_input_bytes
    try:
        catalog = parse_file(
            args.input,
            source_kind=args.format,
            default_category=args.default_category,
            limits=limits,
        )
        if args.merge:
            catalog = merge_catalogs(load_catalog(args.merge), catalog)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2), file=sys.stderr)
        return 2

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(catalog, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(_summarize(catalog))
        print(f"written: {args.out}")
    elif args.counts_only:
        print(_summarize(catalog))
    else:
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
