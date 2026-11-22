#!/usr/bin/env python3
"""tools-pm-intake.py - catalog intake parser for the presentation-makers Skill Tool.

Turns pasted plain text, Markdown, or copied HTML into the structured catalog
that the rest of the Skill Tool consumes (asset-skill-catalog.json shape).

Load order: after tools-pm-core.py, before tools-pm-plan.py.

Recognised shapes
-----------------
A) Labeled list (the supplied catalog export):
       1. Presentation Strategy and Tool Selection
       1. Skill Name: Presentation Requirement Interpreter
       Description: Translates audience, purpose, venue, ...
B) Markdown headings + bold entries:
       ## 12. Collaboration and Review
       - **Feedback Synthesis Specialist**: Consolidates conflicting comments ...
C) Copied HTML: tags, classes, data-* attributes, inline styles and list markup
   are removed before parsing, so `data-skill="x"` never leaks into a name.
D) A single capability: one short paragraph with no list markers becomes one
   category (named from --single-name or derived from the text) plus one skill.

Everything is deterministic: no network, no model calls.
"""

from __future__ import annotations

import html as html_mod
import importlib.util
import os
import re
import sys

_TAG = "pm_core"
if _TAG not in sys.modules:  # pragma: no cover - import shim
    _dir = os.path.dirname(os.path.abspath(__file__))
    _spec = importlib.util.spec_from_file_location(_TAG, os.path.join(_dir, "tools-pm-core.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_TAG] = _mod
    _spec.loader.exec_module(_mod)
import pm_core  # noqa: E402

SKILL_LABEL_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*Skill\s+Name\s*[:\-]\s*(?P<name>.+?)\s*$", re.I)
SKILL_BOLD_RE = re.compile(r"^\s*(?:[-*+]\s*|\d{1,3}\s*[.)]\s*)?\*\*(?P<name>[^*]{3,120})\*\*\s*[:\u2014\u2013-]?\s*(?P<desc>.*)$")
SKILL_PLAIN_RES = (
    re.compile(r"^\s*(?:[-*+]\s*)?Skill\s+Name\s*[:\-]\s*(?P<name>.+?)\s*$", re.I),
    re.compile(r"^\s*\d{1,3}\s*[.)]\s*(?P<name>[A-Z][^:\n]{4,90}?)\s*[:\u2014\u2013]\s*(?P<desc>.{10,})$"),
)
DESC_RE = re.compile(r"^\s*(?:[-*+]\s*)?Description\s*[:\-]\s*(?P<desc>.+?)\s*$", re.I)
CATEGORY_RE = re.compile(r"^\s*#{0,6}\s*(?P<num>\d{1,2})\s*[.)]\s*(?P<name>[A-Za-z][^\n]{2,120}?)\s*$")
MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<name>[^\n#]{3,120}?)\s*$")

NOISE_PATTERNS = (
    re.compile(r"^=+\s*PAGE\s+\d+\s*=+$", re.I),
    re.compile(r"^Emmre'?s\s+GPT.*\|\s*\d+\s*$", re.I),
    re.compile(r"^presentation makers agent skill catalog$", re.I),
    re.compile(r"^catalog (purpose|use note|coverage summary)$", re.I),
    re.compile(r"^\d+\s*$"),
)

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "with", "by",
    "skill", "tool", "agent", "capability", "that", "this", "which", "from",
    "into", "using", "use", "creates", "create", "builds", "build", "produces",
}


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def html_to_text(fragment):
    """Convert copied HTML into plain lines, dropping tags/classes/attributes."""
    text = fragment
    # Keep block structure as newlines before stripping tags.
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?is)<h([1-6])[^>]*>", "\n\n", text)
    text = re.sub(r"(?is)</h[1-6]>", "\n", text)
    text = re.sub(r"(?is)</(p|div|tr|section|article|header|footer|table|ul|ol)>", "\n", text)
    text = re.sub(r"(?is)<td[^>]*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return text


def detect_format(text):
    if re.search(r"(?i)<(html|body|div|section|p|ul|li|table|h[1-6])\b", text):
        return "html"
    if re.search(r"(?m)^\s*#{1,6}\s+\S", text) or "**" in text:
        return "markdown"
    return "text"


def normalise_lines(text, source_format="auto"):
    fmt = detect_format(text) if source_format in (None, "auto") else source_format
    if fmt == "html":
        text = html_to_text(text)
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.replace("\u00a0", " ").replace("\t", "    ").rstrip()
        lines.append(line)
    return lines, fmt


def _is_noise(line):
    stripped = line.strip()
    if not stripped:
        return True
    return any(p.match(stripped) for p in NOISE_PATTERNS)


def _join_hyphenation(text):
    """Repair PDF/Markdown line breaks that split a hyphenated word."""
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "-", text)
    text = re.sub(r"(?<=[A-Za-z])\s*\n\s*(?=[a-z]{2,})", " ", text)
    return " ".join(text.split())


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _match_skill_start(line):
    """Return (name, inline_description) when the line opens a skill entry."""
    match = SKILL_LABEL_RE.match(line)
    if match:
        return match.group("name").strip(), ""
    match = SKILL_BOLD_RE.match(line)
    if match and not re.match(r"^\s*(description|skill name)\s*:", match.group("name"), re.I):
        return match.group("name").strip(), (match.group("desc") or "").strip()
    for pattern in SKILL_PLAIN_RES:
        match = pattern.match(line)
        if match:
            groups = match.groupdict()
            return match.group("name").strip(), (groups.get("desc") or "").strip()
    return None


def parse_catalog(text, source_format="auto", single_name=None, default_category=None):
    """Parse catalog text into {'categories': [...], 'source_format': ...}."""
    if not text or not text.strip():
        raise pm_core.PMError(
            "No catalog text supplied",
            code="intake-empty",
            hint="Pass --input with a file, or pipe text on stdin with '-',",
        )
    lines, fmt = normalise_lines(text, source_format)

    categories = []
    current_category = None
    current_skill = None
    pending_name = None
    buffer = []

    def flush_description():
        nonlocal buffer
        if current_skill is not None and buffer:
            extra = _join_hyphenation(" ".join(buffer))
            current_skill["description"] = _join_hyphenation(
                (current_skill.get("description") or "") + " " + extra
            ).strip()
        buffer = []

    def start_category(name, number=None):
        nonlocal current_category, current_skill
        flush_description()
        current_skill = None
        current_category = {
            "id": len(categories) + 1,
            "number": number,
            "name": pm_core.sentence_case(name.strip().rstrip(".:")),
            "slug": pm_core.slugify(name),
            "skills": [],
        }
        categories.append(current_category)

    for raw_line in lines:
        line = raw_line.strip()
        if _is_noise(line):
            continue

        cat = CATEGORY_RE.match(line)
        if cat and "skill name" not in line.lower():
            start_category(cat.group("name"), pm_core.safe_int(cat.group("num"), None))
            continue

        heading = MD_HEADING_RE.match(line)
        if heading and fmt == "markdown":
            level = len(line.split(" ", 1)[0].strip())
            if level == 1 and not categories:
                continue  # a leading H1 names the document, not a category
            start_category(heading.group("name"))
            continue

        skill_start = _match_skill_start(line)
        if skill_start:
            flush_description()
            name, inline_desc = skill_start
            if current_category is None:
                start_category(default_category or "Presentation Capabilities")
            current_skill = {
                "id": len(current_category["skills"]) + 1,
                "name": pm_core.sentence_case(name.strip().rstrip(".:")),
                "slug": pm_core.slugify(name),
                "description": inline_desc,
            }
            current_category["skills"].append(current_skill)
            continue

        desc = DESC_RE.match(line)
        if desc and current_skill is not None:
            flush_description()
            current_skill["description"] = _join_hyphenation(desc.group("desc"))
            continue

        if current_skill is not None:
            buffer.append(line)
            continue

        pending_name = line

    flush_description()

    if not categories:
        categories = _single_capability_fallback(text, single_name)

    categories = merge_duplicate_categories(categories)
    for category in categories:
        for skill in category["skills"]:
            skill["description"] = pm_core.sentence_case(skill.get("description", "").strip())
        category["skill_count"] = len(category["skills"])
        if not category.get("slug"):
            category["slug"] = pm_core.slugify(category["name"])

    return {"source_format": fmt, "categories": categories, "pending_line": pending_name}


def _single_capability_fallback(text, single_name):
    cleaned = _join_hyphenation(html_to_text(text) if detect_format(text) == "html" else text)
    if not cleaned:
        raise pm_core.PMError("Catalog text contained no readable content", code="intake-empty")
    sentence_split = re.split(r"(?<=[.!?])\s+", cleaned)
    description = cleaned
    if single_name:
        name = single_name
        rest = cleaned
        if cleaned.lower().startswith(single_name.lower()):
            rest = cleaned[len(single_name):].lstrip(" :-\u2014")
        description = rest or cleaned
    else:
        name = sentence_split[0]
        if len(name) > 110:
            name = pm_core.truncate(name, 110)
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in STOPWORDS]
    category_name = " ".join(words[:4]).title() or "Presentation Capability"
    return [{
        "id": 1,
        "number": None,
        "name": category_name,
        "slug": pm_core.slugify(category_name),
        "skills": [{
            "id": 1,
            "name": pm_core.sentence_case(name.strip().rstrip(".:")),
            "slug": pm_core.slugify(name),
            "description": pm_core.sentence_case(description.strip()),
        }],
        "skill_count": 1,
    }]


# --------------------------------------------------------------------------
# Catalog assembly
# --------------------------------------------------------------------------
def merge_duplicate_categories(categories):
    """Merge categories that share a slug (index pages repeat category names).

    The first occurrence keeps its position; later occurrences contribute their
    skills and are dropped so an index listing never creates empty duplicates.
    """
    merged, by_slug = [], {}
    for category in categories:
        key = category.get("slug") or pm_core.slugify(category.get("name"))
        if key in by_slug:
            target = by_slug[key]
            if not target.get("number") and category.get("number"):
                target["number"] = category["number"]
            for skill in category["skills"]:
                if skill["slug"] in {s["slug"] for s in target["skills"]}:
                    continue
                skill["id"] = len(target["skills"]) + 1
                target["skills"].append(skill)
            continue
        category["slug"] = key
        by_slug[key] = category
        merged.append(category)
    for index, category in enumerate(merged, start=1):
        category["id"] = index
        for skill_index, skill in enumerate(category["skills"], start=1):
            skill["id"] = skill_index
    return merged


def build_catalog(parsed, coverage_map=None, source_name=None, source_label=None):
    """Assemble the shipped catalog shape, optionally attaching CLI coverage."""
    categories = []
    total = 0
    for category in parsed["categories"]:
        entry = {
            "id": category["id"],
            "number": category.get("number") or category["id"],
            "name": category["name"],
            "slug": category["slug"],
            "skill_count": category.get("skill_count", len(category["skills"])),
            "skills": [],
        }
        coverage = None
        if coverage_map:
            coverage = (coverage_map.get("by_name", {}).get(category["name"])
                        or coverage_map.get("by_slug", {}).get(category["slug"]))
        if coverage:
            entry["coverage"] = coverage
        for skill in category["skills"]:
            entry["skills"].append({
                "id": skill["id"],
                "name": skill["name"],
                "slug": skill["slug"],
                "description": skill.get("description", ""),
            })
            total += 1
        categories.append(entry)
    catalog = {
        "catalog": pm_core.TOOL_NAME,
        "catalog_version": pm_core.SPEC_VERSION,
        "source": source_label or source_name or "inline text",
        "category_count": len(categories),
        "skill_count": total,
        "categories": categories,
    }
    return catalog


def validate_catalog(catalog):
    problems = []
    if not catalog.get("categories"):
        problems.append(pm_core.diag("error", "catalog-empty", "Catalog has no categories"))
    seen = set()
    skill_total = 0
    for category in catalog.get("categories", []):
        if not category.get("name"):
            problems.append(pm_core.diag("error", "catalog-category-name",
                                         "Category without a name", where="category %s" % category.get("id")))
        if not category.get("skills"):
            problems.append(pm_core.diag("warning", "catalog-category-empty",
                                         "Category '%s' has no skills" % category.get("name"),
                                         where="category %s" % category.get("name")))
        for skill in category.get("skills", []):
            skill_total += 1
            key = (category.get("slug"), skill.get("slug"))
            if key in seen:
                problems.append(pm_core.diag("warning", "catalog-duplicate",
                                             "Duplicate skill slug '%s'" % skill.get("slug"),
                                             where=category.get("name")))
            seen.add(key)
            if not (skill.get("description") or "").strip():
                problems.append(pm_core.diag("warning", "catalog-missing-description",
                                             "Skill '%s' has no description" % skill.get("name"),
                                             where=category.get("name")))
            elif len(skill["description"]) < 20:
                problems.append(pm_core.diag("warning", "catalog-thin-description",
                                             "Skill '%s' description is very short" % skill.get("name"),
                                             where=category.get("name")))
    if catalog.get("skill_count") not in (None, skill_total):
        problems.append(pm_core.diag("error", "catalog-count",
                                     "Declared skill_count %s != parsed %d"
                                     % (catalog.get("skill_count"), skill_total)))
    return problems


if __name__ == "__main__":  # pragma: no cover - module smoke test
    sample = """
    <div class="cat" data-cat="1"><h2>1. Strategy and Tool Selection</h2>
    <ol><li class="item"><span class="n">1.</span> <strong>Skill Name:</strong>
    Requirement Interpreter</li><li>Description: Translates audience and purpose into a brief.</li></ol></div>
    """
    parsed = parse_catalog(sample)
    print(parsed["categories"][0]["name"], "|", parsed["categories"][0]["skills"][0]["name"])
