#!/usr/bin/env python3
"""Shared core for the Google Slides Agent Catalog Skill Tool.

Responsibilities
----------------
* Locate and load the configuration file (asset-default-config.json) next to this file.
* Read catalog sources: plain text, Markdown, HTML (tags/CSS/attributes stripped) and,
  when the optional ``pypdf`` package is installed, PDF.
* Parse catalog text into a normalised catalog structure (categories -> skills).
* Validate a catalog structure and return actionable issues.
* Load, save and index the packaged catalog asset and JSON documents.
* Provide the shared error type and JSON output envelope used by every tool file.

This module is not an entry point. It is loaded by ``tools-gslides-catalog.py`` and
``tools-plan-engine.py`` through an explicit importlib file loader so that the
hyphenated, prefix-compliant filename stays importable on every platform.

Standard library only (PDF support is optional and imported lazily).
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
from html.parser import HTMLParser

CORE_VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_NAME = "asset-default-config.json"

# Hard safety ceilings applied even if a config file is missing or hostile.
ABSOLUTE_MAX_SOURCE_BYTES = 32 * 1024 * 1024
ABSOLUTE_MAX_SKILLS = 5000
ABSOLUTE_MAX_REPORT_ITEMS = 2000


class SkillToolError(Exception):
    """Structured, user-facing error carrying a stable machine code."""

    def __init__(self, code, message, hint=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def as_dict(self):
        payload = {"ok": False, "error": {"code": self.code, "message": self.message}}
        if self.hint:
            payload["error"]["hint"] = self.hint
        if self.details:
            payload["error"]["details"] = self.details
        return payload


# --------------------------------------------------------------------------------------
# Paths and configuration
# --------------------------------------------------------------------------------------

def sibling(name):
    """Absolute path of a file that lives directly beside this module."""
    return os.path.join(HERE, name)


def resolve_path(value, base=None):
    """Resolve a user-supplied path against ``base`` (default: current directory)."""
    if value is None:
        raise SkillToolError("missing-path", "A path value is required.")
    value = os.path.expanduser(str(value))
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(base or os.getcwd(), value))


def load_config(path=None):
    """Load and sanity-check the configuration document."""
    config_path = resolve_path(path) if path else sibling(DEFAULT_CONFIG_NAME)
    if not os.path.isfile(config_path):
        raise SkillToolError(
            "config-not-found",
            f"Configuration file not found: {config_path}",
            hint=f"Pass --config with a path to {DEFAULT_CONFIG_NAME} or restore the packaged asset.",
        )
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillToolError(
            "config-invalid",
            f"Configuration file could not be read as JSON: {exc}",
            hint="Fix the JSON syntax in the configuration file and retry.",
        )
    if not isinstance(config, dict):
        raise SkillToolError("config-invalid", "Configuration root must be a JSON object.")
    config.setdefault("config_version", "1.0")
    config["_config_path"] = config_path
    config["_config_dir"] = os.path.dirname(config_path)
    return config


def config_path(config, key, default_relative):
    """Resolve a configured resource path. Relative values resolve beside the config."""
    resources = config.get("paths") or {}
    value = resources.get(key, default_relative)
    return resolve_path(value, base=config.get("_config_dir") or HERE)


def limits(config):
    """Return the effective limits block, clamped to absolute ceilings."""
    raw = config.get("limits") or {}
    out = {
        "max_source_bytes": int(raw.get("max_source_bytes", 8 * 1024 * 1024)),
        "max_skills": int(raw.get("max_skills", 2000)),
        "max_matches": int(raw.get("max_matches", 40)),
        "max_batch_requests": int(raw.get("max_batch_requests", 100)),
        "api_timeout_seconds": float(raw.get("api_timeout_seconds", 20)),
        "api_max_retries": int(raw.get("api_max_retries", 3)),
        "api_backoff_seconds": float(raw.get("api_backoff_seconds", 0.5)),
        "max_report_items": int(raw.get("max_report_items", 500)),
    }
    out["max_source_bytes"] = max(1024, min(out["max_source_bytes"], ABSOLUTE_MAX_SOURCE_BYTES))
    out["max_skills"] = max(1, min(out["max_skills"], ABSOLUTE_MAX_SKILLS))
    out["max_matches"] = max(1, min(out["max_matches"], 500))
    out["max_batch_requests"] = max(1, min(out["max_batch_requests"], 500))
    out["api_timeout_seconds"] = max(1.0, min(out["api_timeout_seconds"], 120.0))
    out["api_max_retries"] = max(0, min(out["api_max_retries"], 8))
    out["api_backoff_seconds"] = max(0.0, min(out["api_backoff_seconds"], 10.0))
    out["max_report_items"] = max(10, min(out["max_report_items"], ABSOLUTE_MAX_REPORT_ITEMS))
    return out


# --------------------------------------------------------------------------------------
# Source reading and HTML normalisation
# --------------------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Convert HTML to plain text: drops styling/attributes, keeps block boundaries."""

    SKIP_TAGS = {"script", "style", "noscript", "template"}
    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "tbody", "thead",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
        "dt", "dd", "dl", "blockquote", "pre", "hr", "figure", "figcaption", "main",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK_TAGS or tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def strip_html(text):
    """Remove HTML tags, CSS, data attributes and entities, keeping readable text."""
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    cleaned = parser.text()
    cleaned = re.sub(r"<![^>]*>", "", cleaned)
    return html_module.unescape(cleaned)


def _looks_like_html(text):
    head = text[:4096].lower()
    markers = ("<html", "<div", "<p>", "<p ", "<li", "<span", "<table", "<!doctype", "<h1", "<h3")
    return sum(1 for marker in markers if marker in head) >= 2


def read_source_text(path, max_bytes=None):
    """Read a catalog source file and return (text, meta)."""
    max_bytes = int(max_bytes or ABSOLUTE_MAX_SOURCE_BYTES)
    path = resolve_path(path)
    if not os.path.exists(path):
        raise SkillToolError(
            "source-not-found",
            f"Catalog source not found: {path}",
            hint="Check the path, or omit --source to use the packaged catalog asset.",
        )
    if os.path.isdir(path):
        raise SkillToolError("source-is-directory", f"Expected a file but found a directory: {path}")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise SkillToolError(
            "source-too-large",
            f"Source is {size} bytes which exceeds the {max_bytes} byte limit.",
            hint="Raise limits.max_source_bytes in the configuration or split the source.",
        )
    if size == 0:
        raise SkillToolError("source-empty", f"Source file is empty: {path}")

    ext = os.path.splitext(path)[1].lower()
    meta = {"path": path, "bytes": size, "format": ext.lstrip(".") or "text"}

    if ext == ".pdf":
        text = _read_pdf(path)
        meta["format"] = "pdf"
        return text, meta

    with open(path, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    text = raw.decode("utf-8", errors="replace")
    if ext in (".html", ".htm", ".xhtml") or _looks_like_html(text):
        meta["format"] = "html"
        text = strip_html(text)
    return text, meta


def _read_pdf(path):
    """Extract text from a PDF using the optional pypdf dependency."""
    try:
        import pypdf  # noqa: F401  (optional dependency)
    except ImportError:
        raise SkillToolError(
            "pdf-support-missing",
            "PDF sources need the optional 'pypdf' package, which is not installed.",
            hint="Run: python3 -m pip install 'pypdf>=3.9,<7'  (or convert the PDF to .txt/.md first).",
        )
    import pypdf
    try:
        reader = pypdf.PdfReader(path)
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # a single damaged page must not abort the whole ingest
                pages.append("")
    except Exception as exc:
        raise SkillToolError(
            "pdf-unreadable",
            f"PDF could not be parsed: {exc}",
            hint="Export the catalog to text/Markdown and ingest that instead.",
        )
    return "\n".join(pages)


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------

_NOISE_LINE_PATTERNS = (
    re.compile(r"^\s*google slides capability catalog\s*\|.*$", re.IGNORECASE),
    re.compile(r"^\s*google slides agent skill catalog\s*$", re.IGNORECASE),
    re.compile(r"^\s*page\s+\d+\s*(of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d{1,4}\s*$"),
)

_CATEGORY_RE = re.compile(r"^\s*category\s+(\d+)\s*[:\-\u2013\u2014]\s*(.+?)\s*$", re.IGNORECASE)
_PURPOSE_RE = re.compile(r"^\s*category purpose\s*[:\-\u2013\u2014]\s*(.+?)\s*$", re.IGNORECASE)
# The leading number is optional: HTML tables and Markdown lists may omit it.
_SKILL_RE = re.compile(
    r"^\s*(?:(\d+)\s*[.)]\s*)?skill\s*name\s*[:\-\u2013\u2014]\s*(.+?)\s*$", re.IGNORECASE
)
_BARE_SKILL_RE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+?)\s*$")
_DESCRIPTION_RE = re.compile(r"^\s*description\s*[:\-\u2013\u2014]\s*(.*)$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^\s*(category|skill name|description|category purpose)\b", re.IGNORECASE)

# Non-capability sections (appendices, summaries, indexes) that must not be read as skills.
_STOP_SECTION_RE = re.compile(
    r"^\s*(implementation specification template|implementation spec template|catalog summary|"
    r"appendix\b|table of contents|contents\b|catalog index|index\b|glossary|references)\s*[:.]?\s*$",
    re.IGNORECASE,
)

# Numbering marks such as "1.", "1)", "(1)", "Skill 1:" and bullets are stripped from names.
_NAME_PREFIX_RE = re.compile(
    r"^(?:skill\s*(?:no\.?|number|id)?\s*\d+\s*[:\-]\s*|(?:\(\s*\d+\s*\)|\d+\s*[.)])\s*|[\-\u2022\*\u00b7]\s*)",
    re.IGNORECASE,
)


def normalize_text(text):
    """Normalise line endings, entities, bullets and irregular spacing."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = text.replace("\ufeff", "")
    text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    lines = []
    for raw_line in text.split("\n"):
        line = raw_line.replace("\t", " ")
        # Markdown/HTML-copied markup never carries meaning for catalog structure.
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*[*+\u2022]\s+(?=(?:skill\s*name|category|description|category purpose|\d))",
                      "", line, flags=re.IGNORECASE)
        line = re.sub(r"[ ]{2,}", " ", line).strip()
        if any(pattern.match(line) for pattern in _NOISE_LINE_PATTERNS):
            continue
        lines.append(line)
    return "\n".join(lines)


def slugify(value, fallback="item"):
    """Lowercase ASCII hyphenated slug used for stable identifiers."""
    value = (value or "").strip().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or fallback


def clean_name(value):
    """Remove numbering marks and stray punctuation from a capability name."""
    name = _NAME_PREFIX_RE.sub("", (value or "").strip())
    name = name.strip(" \t:;-\u2013\u2014")
    name = re.sub(r"\s{2,}", " ", name)
    return name


def _join_wrapped(parts):
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith("-"):
            out = out + part
        else:
            out = out + " " + part
    return re.sub(r"\s{2,}", " ", out).strip()


def parse_catalog_text(text, source_name="catalog", max_skills=None):
    """Parse catalogue-formatted text into {'categories': [...], 'warnings': [...]}.

    Recognised layout (label driven):
        Category 1: Name
        Category purpose: text
        1. Skill Name: Name
        Description: text (may wrap over several lines)

    A tolerant fallback also accepts numbered name lines followed by a prose line when
    the explicit "Skill Name:"/"Description:" labels are absent (common in HTML tables and
    Markdown copies).
    """
    max_skills = int(max_skills or ABSOLUTE_MAX_SKILLS)
    text = normalize_text(text)
    if not text.strip():
        raise SkillToolError("source-empty-text", "No readable text found in the catalog source.")

    categories = []
    warnings = []
    current_category = None
    current_skill = None
    desc_parts = []
    pending_numbered_name = None
    total_skills = 0
    stopped = False

    def flush_skill():
        nonlocal current_skill, desc_parts, total_skills
        if current_skill is not None:
            current_skill["description"] = _join_wrapped(desc_parts)
            if current_category is not None:
                current_category["skills"].append(current_skill)
            total_skills += 1
        current_skill = None
        desc_parts = []

    def new_category(number, name):
        nonlocal current_category
        flush_skill()
        current_category = {
            "number": int(number) if str(number).isdigit() else len(categories) + 1,
            "name": clean_name(name),
            "slug": slugify(clean_name(name), fallback=f"category-{len(categories) + 1}"),
            "purpose": "",
            "skills": [],
        }
        categories.append(current_category)

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        match_category = _CATEGORY_RE.match(line)
        if match_category:
            # A real category heading always resumes parsing, even after an index/appendix.
            stopped = False
            new_category(match_category.group(1), match_category.group(2))
            continue

        if _STOP_SECTION_RE.match(line):
            # Only a heading that appears after catalogue content ends the capability list;
            # an index at the front is skipped without ending parsing.
            if categories:
                flush_skill()
                pending_numbered_name = None
                stopped = True
            continue

        if stopped:
            continue

        match_purpose = _PURPOSE_RE.match(line)
        if match_purpose and current_category is not None:
            current_category["purpose"] = match_purpose.group(1).strip()
            continue

        match_skill = _SKILL_RE.match(line)
        if match_skill:
            flush_skill()
            if current_category is None:
                new_category(len(categories) + 1, f"Category {len(categories) + 1}")
            current_skill = {
                "number": int(match_skill.group(1)) if match_skill.group(1) else 0,
                "name": clean_name(match_skill.group(2)),
                "description": "",
            }
            continue

        match_description = _DESCRIPTION_RE.match(line)
        if match_description:
            if current_skill is None:
                warnings.append(f"Description without a skill name near: {line[:80]!r}")
            else:
                desc_parts.append(match_description.group(1))
            continue

        bare = _BARE_SKILL_RE.match(line)
        if bare and not _SECTION_RE.match(line):
            candidate_name = clean_name(bare.group(2))
            if current_skill is not None and not desc_parts and len(candidate_name) > 40:
                pending_numbered_name = candidate_name
                continue
            if current_skill is not None and 0 < len(candidate_name) <= 90:
                pending_numbered_name = None
                flush_skill()
                if current_category is None:
                    new_category(len(categories) + 1, f"Category {len(categories) + 1}")
                current_skill = {
                    "number": int(bare.group(1)),
                    "name": candidate_name,
                    "description": "",
                }
                continue

        if pending_numbered_name is not None and current_skill is None:
            if current_category is None:
                new_category(len(categories) + 1, f"Category {len(categories) + 1}")
            current_skill = {"number": 0, "name": pending_numbered_name, "description": ""}
            pending_numbered_name = None
            desc_parts.append(line)
            continue

        if current_skill is not None:
            desc_parts.append(line)

    flush_skill()

    categories = [category for category in categories if category["skills"] or category["name"]]

    # Assign stable skill identifiers, numbers and category links.
    seen_names = set()
    for category in categories:
        category["id"] = category["slug"]
        category["name"] = clean_name(category["name"]) or category["id"].replace("-", " ").title()
        for index, skill in enumerate(category["skills"], start=1):
            if not skill.get("number"):
                skill["number"] = index
            skill["name"] = clean_name(skill["name"])
            skill["description"] = _join_wrapped([skill.get("description", "")])
            base_id = slugify(skill["name"], fallback=f"{category['id']}-skill-{index}")
            skill_id = base_id
            suffix = 2
            while skill_id in seen_names:
                skill_id = f"{base_id}-{suffix}"
                suffix += 1
            seen_names.add(skill_id)
            skill["id"] = skill_id
            skill["category_id"] = category["id"]
            skill["category_name"] = category["name"]
        category["skill_count"] = len(category["skills"])
        if category["skill_count"] > max_skills:
            raise SkillToolError(
                "too-many-skills",
                f"Category {category['name']!r} has {category['skill_count']} skills, above the limit of {max_skills}.",
                hint="Raise limits.max_skills in the configuration or split the source.",
            )

    catalog = {
        "schema_version": "1.0",
        "source_name": source_name,
        "categories": categories,
        "counts": {
            "categories": len(categories),
            "skills": sum(category["skill_count"] for category in categories),
        },
        "warnings": warnings[:50],
    }
    return catalog


def validate_catalog(catalog, strict=False):
    """Return a list of issue dicts for a parsed catalog. Empty list means valid."""
    issues = []

    def add(severity, code, message, **extra):
        entry = {"severity": severity, "code": code, "message": message}
        entry.update(extra)
        issues.append(entry)

    if not isinstance(catalog, dict):
        add("error", "not-an-object", "Catalog must be a JSON object.")
        return issues
    categories = catalog.get("categories")
    if not isinstance(categories, list) or not categories:
        add(
            "error",
            "no-categories",
            "No categories were recognised. Sources must contain 'Category N: Name' headings.",
            hint="Check the source formatting, or ingest a plain-text/Markdown copy of the catalog.",
        )
        return issues

    seen_category_ids = set()
    seen_skill_ids = set()
    for category in categories:
        if not isinstance(category, dict):
            add("error", "bad-category", "Category entries must be objects.")
            continue
        name = str(category.get("name", "")).strip()
        if not name:
            add("error", "category-without-name", "A category has no name.")
        category_id = category.get("id") or slugify(name)
        if category_id in seen_category_ids:
            add("error", "duplicate-category-id", f"Duplicate category id: {category_id}", id=category_id)
        seen_category_ids.add(category_id)
        skills = category.get("skills")
        if not isinstance(skills, list) or not skills:
            add("error", "category-without-skills", f"Category {name!r} has no skills.", category=name)
            continue
        for skill in skills:
            if not isinstance(skill, dict):
                add("error", "bad-skill", f"Skill entries in {name!r} must be objects.")
                continue
            skill_name = str(skill.get("name", "")).strip()
            skill_id = str(skill.get("id", "")).strip()
            if not skill_name:
                add("error", "skill-without-name", f"A skill in {name!r} has no name.", category=name)
            if not skill_id:
                add("error", "skill-without-id", f"Skill {skill_name!r} has no id.", skill=skill_name)
            elif skill_id in seen_skill_ids:
                add("error", "duplicate-skill-id", f"Duplicate skill id: {skill_id}", id=skill_id)
            seen_skill_ids.add(skill_id)
            description = str(skill.get("description", "")).strip()
            if not description:
                add(
                    "warning",
                    "skill-without-description",
                    f"Skill {skill_name!r} has no description.",
                    skill=skill_id,
                )
            elif len(description) < 12:
                add(
                    "warning",
                    "skill-description-short",
                    f"Skill {skill_name!r} has a very short description.",
                    skill=skill_id,
                )
    if strict and any(issue["severity"] == "warning" for issue in issues):
        add("error", "strict-warnings", "Strict mode treats warnings as errors.")
    return issues


def index_catalog(catalog):
    """Build lookup indexes: categories by id, skills by id, ordered skill list."""
    categories = {}
    skills = {}
    ordered = []
    for category in catalog.get("categories", []):
        categories[category["id"]] = category
        for skill in category.get("skills", []):
            skills[skill["id"]] = skill
            ordered.append(skill)
    return {"categories": categories, "skills": skills, "ordered": ordered}


# --------------------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------------------

def load_json(path, what="JSON document"):
    path = resolve_path(path)
    if not os.path.isfile(path):
        raise SkillToolError("file-not-found", f"{what} not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise SkillToolError(
            "json-invalid",
            f"{what} is not valid JSON: {exc}",
            hint="Validate the file with a JSON linter, or regenerate it with this tool.",
        )


def save_json(path, payload):
    path = resolve_path(path)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        raise SkillToolError(
            "output-dir-missing",
            f"Output directory does not exist: {directory}",
            hint="Create the directory first; this tool never creates directories implicitly.",
        )
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, path)
    return path


def save_text(path, text):
    path = resolve_path(path)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        raise SkillToolError(
            "output-dir-missing",
            f"Output directory does not exist: {directory}",
            hint="Create the directory first; this tool never creates directories implicitly.",
        )
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp_path, path)
    return path


def load_catalog_asset(config, path=None):
    """Load the packaged catalog asset and validate it before use."""
    catalog_path = resolve_path(path) if path else config_path(config, "catalog", "asset-slides-catalog.json")
    catalog = load_json(catalog_path, "Catalog asset")
    issues = validate_catalog(catalog)
    errors = [issue for issue in issues if issue["severity"] == "error"]
    if errors:
        raise SkillToolError(
            "catalog-invalid",
            f"Catalog asset failed validation with {len(errors)} error(s).",
            hint="Re-ingest the source catalog to regenerate the asset.",
            details={"issues": errors[:20], "catalog": catalog_path},
        )
    catalog.setdefault("counts", {})
    catalog["counts"]["categories"] = len(catalog.get("categories", []))
    catalog["counts"]["skills"] = sum(
        len(category.get("skills", [])) for category in catalog.get("categories", [])
    )
    catalog["_path"] = catalog_path
    return catalog


def emit(payload, as_json=False, stream=None):
    """Write a result envelope either as JSON or as human-readable lines."""
    stream = stream or sys.stdout
    if as_json:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        return
    for line in payload.get("lines", []):
        stream.write(str(line) + "\n")


def ok(payload, lines):
    payload = dict(payload)
    payload["ok"] = True
    payload["lines"] = lines
    return payload
