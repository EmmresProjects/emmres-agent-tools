#!/usr/bin/env python3
"""tools-input-parser.py

Input handling for the HumanSphere Holistic Support Skill Tool.

Accepts a single capability, a numbered Skill Tool list, one category, or
several categories pasted as plain text, Markdown, or copied HTML, then extracts
real category names, Skill Tool names, and descriptions into a normalised
catalog JSON asset.

Copied HTML is normalised to text first: tags, CSS classes, data attributes,
numbering marks, and extra whitespace are removed before extraction.

Relative path from the future skill root:
    humansphere-holistic-support/tools-input-parser.py
It reuses normalisation from:
    humansphere-holistic-support/tools-catalog-engine.py
"""

from __future__ import annotations

import html
import importlib.util
import re
import sys
from pathlib import Path

_ENGINE_FILE = Path(__file__).with_name("tools-catalog-engine.py")


def _load_engine():
    if "hs_catalog_engine" in sys.modules:
        return sys.modules["hs_catalog_engine"]
    if not _ENGINE_FILE.is_file():
        raise SystemExit(
            "Missing required sibling module: "
            f"{_ENGINE_FILE.name} (expected next to tools-input-parser.py)."
        )
    spec = importlib.util.spec_from_file_location("hs_catalog_engine", _ENGINE_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hs_catalog_engine"] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine()
CatalogError = engine.CatalogError

NOISE_PATTERNS = (
    re.compile(r"^humansphere\b", re.I),
    re.compile(r"^page\s+\d+\b", re.I),
    re.compile(r"^focused skills for\b", re.I),
    re.compile(r"^catalog only\b", re.I),
    re.compile(r"^category map\b", re.I),
    re.compile(r"^suggested catalog use\b", re.I),
    re.compile(r"^recommended skill bundle pattern\b", re.I),
    re.compile(r"^build note\b", re.I),
    re.compile(r"^core design rules\b", re.I),
    re.compile(r"^purpose\b$", re.I),
    re.compile(r"^\d+\s+skills?$", re.I),
    re.compile(r"^[|\s\-–—_=*~#+<>·•.]+$"),
    re.compile(r"^[=\-*_~#]{2,}\s*page\s*\d+.*$", re.I),
    re.compile(r"^.*\|\s*page\s*\d+\s*$", re.I),
)

SKILL_LINE = re.compile(
    r"^(?:\d{1,3}\s*[.)]\s*)?(?:skill\s*name|skill|name)\s*[:\-–—]\s*(.+)$", re.I
)
DESC_LINE = re.compile(r"^(?:description|desc|summary|purpose)\s*[:\-–—]\s*(.+)$", re.I)
NUMBERED_HEADING = re.compile(r"^(?:#{1,4}\s*)?(\d{1,2})\s*[.)]\s+(.{2,90})$")
MD_HEADING = re.compile(r"^#{1,4}\s+(.{2,90})$")
MD_BOLD_SKILL = re.compile(r"^(?:[-*+]\s*)?(?:\d{1,3}\s*[.)]\s*)?\*\*(.+?)\*\*\s*(?:[:\-–—]\s*(.*))?$")
DASH_SKILL = re.compile(r"^(?:[-*+]\s*)?(\d{1,3})\s*[.)]\s+(.+?)\s+[—–]\s+(.+)$")
DASH_SKILL_BARE = re.compile(r"^(?:[-*+]\s*)?([A-Z][^—–:\n]{2,80}?)\s+[—–]\s+(.{5,})$")
BULLET_ONLY = re.compile(r"^[-*+]\s+(.{2,120})$")


# ---------------------------------------------------------------------------
# Format normalisation
# ---------------------------------------------------------------------------
def strip_html(raw: str) -> str:
    """Reduce copied HTML to text: drop scripts/styles, keep block structure."""
    text = re.sub(r"(?is)<(script|style|svg|noscript)\b.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section|article|ul|ol|table|dd|dt)>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return text


def normalize_source(raw: str, fmt: str = "auto") -> tuple[str, str]:
    """Return (plain_text, detected_format)."""
    if raw is None:
        raise CatalogError("empty_input", "No input text was supplied.")
    if len(raw.encode("utf-8")) > engine.LIMITS["max_source_bytes"]:
        raise CatalogError(
            "input_too_large",
            f"Input exceeds the {engine.LIMITS['max_source_bytes']} byte limit.",
            "Split the catalog and run parse-source once per part.",
        )
    fmt = (fmt or "auto").lower()
    if fmt not in ("auto", "text", "markdown", "md", "html"):
        raise CatalogError(
            "invalid_format",
            f"Unsupported --format value: {fmt}",
            "Use one of: auto, text, markdown, md, html.",
        )
    looks_html = bool(re.search(r"(?i)<(html|body|div|p|ul|ol|li|table|span|h[1-6])\b", raw))
    use_html = fmt == "html" or (fmt == "auto" and looks_html)
    text = strip_html(raw) if use_html else raw
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        cleaned = line.replace("\t", " ").strip()
        cleaned = re.sub(r"^\s*(?:[-*+•·]|\u2022)\s+", lambda m: m.group(0) if "**" in line else "- ", cleaned) \
            if cleaned.startswith(("-", "*", "+", "•", "·", "\u2022")) else cleaned
        cleaned = re.sub(r"[ ]{2,}", " ", cleaned)
        lines.append(cleaned)
    return "\n".join(lines), ("html" if use_html else "text")


def _is_noise(line: str) -> bool:
    if not line.strip():
        return True
    for pattern in NOISE_PATTERNS:
        if pattern.search(line):
            return True
    return False


def _clean_category_name(value: str) -> str:
    value = value.split("|")[0]
    value = re.sub(r"^\s*[-*+#\d.)\s]+", "", value)
    value = re.sub(r"\s*[-–—]\s*\d+\s+skills?\s*$", "", value, flags=re.I)
    value = re.sub(r"\s{2,}", " ", value).strip(" .:-–—")
    return value


def _clean_skill_name(value: str) -> str:
    value = value.split("|")[0]
    value = re.sub(r"\*\*|__|`", "", value)
    value = re.sub(r"^\s*[-*+#\d.)\s]+", "", value)
    value = re.sub(r"^(?:skill\s*name|skill|name)\s*[:\-–—]\s*", "", value, flags=re.I)
    return re.sub(r"\s{2,}", " ", value).strip(" .:-–—")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def parse_source(raw: str, fmt: str = "auto", source_name: str = "", category_hint: str = "") -> dict:
    """Extract a catalog dict from pasted text, Markdown, or HTML."""
    text, detected = normalize_source(raw, fmt)
    lines = text.split("\n")

    current_category = _clean_category_name(category_hint) if category_hint else ""
    category_order: list[str] = []
    skills: list[dict] = []
    current: dict | None = None
    pending_number: int | None = None
    dropped_continuation = 0

    def open_skill(name: str, description: str = "", number: int | None = None) -> None:
        nonlocal current, pending_number
        clean = _clean_skill_name(name)
        if not clean:
            pending_number = None
            return
        current = {
            "name": clean,
            "description": (description or "").strip(),
            "category": current_category or "Uncategorised",
            "number": number,
        }
        skills.append(current)
        pending_number = None

    def close_skill() -> None:
        nonlocal current
        current = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            close_skill()
            continue
        if _is_noise(line):
            close_skill()
            continue

        match = SKILL_LINE.match(line)
        if match:
            open_skill(match.group(1))
            pending_number = None
            continue

        match = DESC_LINE.match(line)
        if match and current is not None:
            current["description"] = (current["description"] + " " + match.group(1)).strip()
            continue

        match = DASH_SKILL.match(line)
        if match:
            open_skill(match.group(2), match.group(3), int(match.group(1)))
            continue

        match = DASH_SKILL_BARE.match(line)
        if match and not DESC_LINE.match(line):
            open_skill(match.group(1), match.group(2))
            continue

        match = MD_BOLD_SKILL.match(line)
        if match:
            open_skill(match.group(1), match.group(2) or "")
            continue

        match = NUMBERED_HEADING.match(line)
        if match and not DESC_LINE.match(line):
            candidate = _clean_category_name(match.group(2))
            if candidate and not _looks_like_skill_name(candidate):
                close_skill()
                current_category = candidate
                if candidate not in category_order:
                    category_order.append(candidate)
                continue

        match = MD_HEADING.match(line)
        if match:
            candidate = _clean_category_name(match.group(1))
            if candidate:
                close_skill()
                current_category = candidate
                if candidate not in category_order:
                    category_order.append(candidate)
                continue

        match = BULLET_ONLY.match(line)
        if match and current is None:
            open_skill(match.group(1))
            continue

        # Continuation of a wrapped description (including hyphen line-joins).
        if current is not None:
            joined = current["description"]
            if joined.endswith("-"):
                current["description"] = joined + line
            else:
                current["description"] = (joined + " " + line).strip()
            continue
        dropped_continuation += 1

    skills = [s for s in skills if s["name"] and len(s["name"]) <= 120]
    if not skills:
        return _single_capability_catalog(text, source_name, category_hint)

    return _finalise(skills, category_order, text, source_name, detected, mode="structured")


def _looks_like_skill_name(candidate: str) -> bool:
    lowered = candidate.lower()
    return any(
        marker in lowered
        for marker in ("responds", "description", "helps users", "provides", "creates")
    )


def _single_capability_catalog(text: str, source_name: str, category_hint: str) -> dict:
    """No structured list found: treat the input as one capability."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        raise CatalogError(
            "no_content",
            "No usable capability text was found in the input.",
            "Paste a numbered list containing 'Skill Name:' lines, or describe one capability.",
        )
    first_line = next((ln.strip() for ln in text.split("\n") if ln.strip()), collapsed)
    name = re.sub(r"^#{1,6}\s*", "", first_line)
    name = re.sub(r"^\s*(?:[-*+•·]\s+|\d{1,3}\s*[.)]\s+)", "", name)
    name = re.sub(r"\s*:\s*|\s+[–—]\s+|\s+-\s+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > 90:
        words = name.split()
        kept: list[str] = []
        for word in words[:14]:
            if len(" ".join(kept + [word])) > 90:
                break
            kept.append(word)
        name = " ".join(kept).strip(" .,") or "Custom capability"
    name = name[:1].upper() + name[1:] if name else "Custom capability"
    category = _clean_category_name(category_hint) if category_hint else "Custom capability"
    skill = {"name": name, "description": collapsed, "category": category, "number": 1}
    return _finalise([skill], [category], text, source_name, "text", mode="single-capability")


def _finalise(
    skills: list[dict],
    category_order: list[str],
    text: str,
    source_name: str,
    detected_format: str,
    mode: str,
) -> dict:
    # Category ids follow first appearance of a skill, so ids are stable and
    # never shifted by decorative or empty headings.
    ordered: list[str] = []
    for skill in skills:
        clean = _clean_category_name(skill["category"]) or skill["category"]
        skill["category"] = clean
        if clean not in ordered:
            ordered.append(clean)
    category_ids = {name: index + 1 for index, name in enumerate(ordered)}

    used_numbers: set[int] = set()
    next_number = 1
    raw_skills = []
    for skill in skills:
        number = skill.get("number")
        if not isinstance(number, int) or number in used_numbers:
            while next_number in used_numbers:
                next_number += 1
            number = next_number
        used_numbers.add(number)
        raw_skills.append(
            {
                "id": str(number),
                "name": skill["name"],
                "description": skill["description"],
                "category": skill["category"],
                "category_id": category_ids[skill["category"]],
            }
        )
    raw_skills.sort(key=lambda item: int(item["id"]))

    catalog = engine.catalog_from_dict(
        {
            "schema": engine.CATALOG_SCHEMA,
            "title": _derive_title(ordered, source_name),
            "source": source_name or "pasted input",
            "skills": raw_skills,
        }
    )
    catalog["extraction"] = {
        "mode": mode,
        "format": detected_format,
        "characters": len(text),
        "skills": len(raw_skills),
        "categories": len(ordered),
    }
    return catalog


def _derive_title(categories: list[str], source_name: str) -> str:
    if len(categories) == 1 and categories[0] not in ("Uncategorised", "Custom capability"):
        return categories[0]
    if categories and len(categories) <= 3:
        return " + ".join(categories)
    if source_name:
        return Path(source_name).stem.replace("_", " ").replace("-", " ").title()
    return "Uncategorized skill catalog"


def parse_file(path: str | Path, fmt: str = "auto", category_hint: str = "") -> dict:
    path = Path(path)
    if not path.is_file():
        raise CatalogError(
            "input_missing",
            f"Input file not found: {path}",
            "Pass --input with a readable .txt, .md, or .html file, or use --text.",
        )
    size = path.stat().st_size
    if size > engine.LIMITS["max_source_bytes"]:
        raise CatalogError(
            "input_too_large",
            f"Input file is {size} bytes; limit is {engine.LIMITS['max_source_bytes']}.",
            "Split the catalog into smaller parts and parse each one.",
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    return parse_source(raw, fmt=fmt, source_name=path.name, category_hint=category_hint)


def filter_categories(catalog: dict, categories: list[str] | None) -> dict:
    """Keep only the requested categories (one category or several)."""
    if not categories:
        return catalog
    wanted = [engine.normalize_text(c) for c in categories if c and c.strip()]
    kept = [
        skill
        for skill in catalog["skills"]
        if any(w and (w in engine.normalize_text(skill["category"]) or w == str(skill["category_id"]))
               for w in wanted)
    ]
    if not kept:
        available = ", ".join(c["name"] for c in catalog["categories"]) or "none"
        raise CatalogError(
            "category_not_found",
            f"None of the requested categories matched: {', '.join(categories)}",
            f"Available categories in this input: {available}.",
        )
    filtered = dict(catalog)
    filtered["skills"] = kept
    counts: dict[int, dict] = {}
    for skill in kept:
        counts.setdefault(
            skill["category_id"],
            {"id": skill["category_id"], "name": skill["category"], "skill_count": 0},
        )
        counts[skill["category_id"]]["skill_count"] += 1
    filtered["categories"] = [counts[k] for k in sorted(counts)]
    filtered["counts"] = {"skills": len(kept), "categories": len(filtered["categories"])}
    return filtered


# ---------------------------------------------------------------------------
# Standalone help (the primary entry point exposes the real CLI)
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "tools-input-parser.py - catalog extraction module\n\n"
            "Run it through the primary entry point:\n"
            "  python3 humansphere-holistic-support/tools-humansphere.py parse-source --help\n\n"
            "Direct module use:\n"
            "  python3 -c \"import importlib.util,pathlib;"
            "s=importlib.util.spec_from_file_location('p','humansphere-holistic-support/tools-input-parser.py');"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.parse_source('1. Skill Name: Foo\\nDescription: Bar.')['counts'])\""
        )
        return 0
    print("This module has no standalone CLI. Use tools-humansphere.py parse-source.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
