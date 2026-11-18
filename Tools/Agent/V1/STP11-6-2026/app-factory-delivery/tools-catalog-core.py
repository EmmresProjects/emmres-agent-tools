#!/usr/bin/env python3
"""Core library for the agent-skill-catalog Skill Tool.

Provides every shared operation used by the CLI entry point
(tools-skill-catalog.py) and the self-test harness (tools-selftest.py):

  * source normalization  - HTML / Markdown / plain text -> clean lines
  * catalog parsing       - lines -> categories, skills, appendices
  * normalization         - slugs, stable ids, statistics
  * schema validation     - built-in JSON-schema-subset validator
  * search                - weighted token scoring over the dataset
  * routing               - request -> smallest covering set of skills
  * spec drafting         - catalog entry -> skill specification draft
  * export                - dataset -> json / csv / markdown

Standard library only. Python 3.9+. No network access, no shell
execution, no embedded secrets. All file writes performed through
atomic_write_text() with overwrite protection handled by the caller.
"""

import csv
import html as html_mod
import io
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "ToolError",
    "Deadline",
    "read_text_with_retry",
    "atomic_write_text",
    "detect_format",
    "normalize_source",
    "parse_catalog",
    "normalize_catalog",
    "validate_dataset",
    "search_skills",
    "route_request",
    "resolve_skill",
    "generate_spec",
    "export_dataset",
    "dataset_stats",
    "CAPABILITIES",
]

TOOL_NAME = "agent-skill-catalog"
SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------
# Errors, deadlines, safe IO
# --------------------------------------------------------------------------


class ToolError(Exception):
    """Structured tool error with a machine-readable code and a hint."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> Dict[str, str]:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class Deadline:
    """Cooperative time budget. check() raises E_TIMEOUT when exceeded."""

    def __init__(self, seconds: Optional[float]) -> None:
        self.seconds = float(seconds) if seconds and seconds > 0 else None
        self._start = time.monotonic()

    def expired(self) -> bool:
        if self.seconds is None:
            return False
        return (time.monotonic() - self._start) > self.seconds

    def check(self, stage: str = "operation") -> None:
        if self.expired():
            raise ToolError(
                "E_TIMEOUT",
                "Time budget of %.1fs exceeded during %s." % (self.seconds, stage),
                "Re-run with a larger --timeout value or a smaller input.",
            )


def read_text_with_retry(path: str, max_bytes: int, attempts: int = 3) -> str:
    """Read a text file with a bounded retry and a hard size limit."""
    if path == "-":
        data = sys.stdin.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ToolError(
                "E_INPUT_TOO_LARGE",
                "stdin input exceeds the %d byte limit." % max_bytes,
                "Raise --max-input-bytes or split the input.",
            )
        return data
    if not os.path.isfile(path):
        raise ToolError(
            "E_MISSING_INPUT",
            "Input file not found: %s" % path,
            "Check the path (case-sensitive) or pass '-' to read stdin.",
        )
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ToolError(
            "E_INPUT_TOO_LARGE",
            "Input file is %d bytes, above the %d byte limit." % (size, max_bytes),
            "Raise --max-input-bytes if this input is intentional.",
        )
    last_err: Optional[OSError] = None
    for attempt in range(attempts):
        try:
            with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError as exc:  # transient read problem: one bounded retry
            last_err = exc
            if attempt + 1 < attempts:
                time.sleep(0.05 * (attempt + 1))
    raise ToolError(
        "E_READ_FAILED",
        "Could not read %s: %s" % (path, last_err),
        "Verify permissions and that the file is valid UTF-8 text.",
    )


def atomic_write_text(path: str, text: str, force: bool = False,
                      dry_run: bool = False) -> Dict[str, Any]:
    """Write text atomically (temp file + os.replace). Never clobbers an
    existing file unless force=True. dry_run reports without writing."""
    info: Dict[str, Any] = {"path": path, "bytes": len(text.encode("utf-8"))}
    if dry_run:
        info["dry_run"] = True
        info["would_overwrite"] = os.path.exists(path)
        return info
    if os.path.exists(path) and not force:
        raise ToolError(
            "E_OUTPUT_EXISTS",
            "Output file already exists: %s" % path,
            "Re-run with --force to overwrite (destructive, needs your approval).",
        )
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".asc-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        # cleanup: never leave partial output behind (cancel/failure path)
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise
    info["written"] = True
    return info


# --------------------------------------------------------------------------
# Source normalization: HTML / Markdown / plain text -> clean lines
# --------------------------------------------------------------------------

# Noise lines commonly produced by PDF-to-text extraction, copied web pages,
# and printed catalogs. Configurable via parse_catalog(ignore_patterns=...).
DEFAULT_IGNORE_PATTERNS = [
    r"^\s*AGENT SKILL CATALOG\s*\|.*$",          # running page header
    r"^\s*Agent Skill Catalog\s*\|\s*\d+\s*$",   # running page footer
    r"^\s*===\s*PAGE \d+\s*===\s*$",             # extraction page marker
    r"^\s*Page \d+( of \d+)?\s*$",               # printed page number
    r"^\s*\d+ / \d+\s*$",                        # printed page fraction
]

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAG_RE = re.compile(
    r"</?(p|div|li|ul|ol|h[1-6]|tr|td|th|table|section|article|header|footer|"
    r"br|hr|blockquote|pre|figure|figcaption|nav|aside|main|dt|dd|dl)\b[^>]*>",
    re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<[^<>]{0,2000}>")
_MD_FENCE_RE = re.compile(r"^\s*(```|~~~).*$")
_MD_HEADING_RE = re.compile(r"^(\s*)#{1,6}\s+")
_MD_QUOTE_RE = re.compile(r"^(\s*)>\s?")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\\?\[([^\]]*)\]\([^)]*\)")
_MD_EMPH_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_RULE_RE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_MD_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_html(text: str) -> str:
    """Remove HTML tags, CSS/style/script blocks, and entity escapes while
    preserving line structure introduced by block-level elements."""
    text = _SCRIPT_STYLE_RE.sub("\n", text)
    text = _MD_HTML_COMMENT_RE.sub("\n", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)
    text = html_mod.unescape(text)
    return text


def strip_markdown(text: str) -> str:
    """Remove Markdown decoration but KEEP ordered-list numbering, which the
    catalog structure parser relies on ('1. Skill Name: ...')."""
    out_lines: List[str] = []
    for line in text.splitlines():
        if _MD_FENCE_RE.match(line):
            continue
        if _MD_RULE_RE.match(line):
            continue
        line = _MD_HTML_COMMENT_RE.sub("", line)
        line = _MD_HEADING_RE.sub(r"\1", line)
        line = _MD_QUOTE_RE.sub(r"\1", line)
        line = _MD_IMAGE_RE.sub(r"\1", line)
        line = _MD_LINK_RE.sub(r"\1", line)
        line = _MD_INLINE_CODE_RE.sub(r"\1", line)
        line = _MD_EMPH_RE.sub(r"\2", line)
        out_lines.append(line)
    joined = "\n".join(out_lines)
    if "<" in joined and ">" in joined:
        joined = strip_html(joined)  # copied Markdown often embeds raw HTML
    return joined


def detect_format(text: str, hint: str = "auto") -> str:
    """Return 'html', 'markdown', or 'text' for the given source."""
    if hint and hint != "auto":
        if hint not in ("html", "markdown", "text"):
            raise ToolError(
                "E_BAD_FORMAT",
                "Unknown format '%s'." % hint,
                "Use one of: auto, html, markdown, text.",
            )
        return hint
    head = text[:4096]
    if re.search(r"<\s*(html|body|div|p|li|ul|ol|h[1-6]|table|br|span|strong|em)\b",
                 head, re.IGNORECASE):
        return "html"
    if re.search(r"^\s{0,3}#{1,6}\s", text, re.MULTILINE) or "```" in text \
            or re.search(r"\[[^\]]+\]\([^)]+\)", head) \
            or re.search(r"^\s*>\s", text, re.MULTILINE):
        return "markdown"
    return "text"


def normalize_source(text: str, fmt: str = "auto") -> Tuple[List[str], str]:
    """Normalize any supported source into clean candidate lines.
    Returns (lines, resolved_format)."""
    resolved = detect_format(text, fmt)
    if resolved == "html":
        text = strip_html(text)
    elif resolved == "markdown":
        text = strip_markdown(text)
    lines: List[str] = []
    for raw in text.splitlines():
        line = raw.replace("\t", " ")
        line = re.sub(r"[\u00a0\u2007\u202f]", " ", line)   # exotic spaces
        # bullet-like glyphs (incl. PDF Symbol-font \uf0b7) -> list marker
        line = re.sub(r"[\u2022\u2023\u2043\u25aa\u25cf\uf0a7\uf0b7\uf0fc]",
                      "-", line)
        # smart quotes / dashes -> ASCII equivalents
        line = line.replace("\u2018", "'").replace("\u2019", "'")
        line = line.replace("\u201c", '"').replace("\u201d", '"')
        line = line.replace("\u2013", "-").replace("\u2014", " - ")
        # remaining private-use-area glyphs (PDF font artifacts) are dropped
        line = re.sub(r"[\ue000-\uf8ff]", "", line)
        line = re.sub(r" {2,}", " ", line).strip()
        if line:
            lines.append(line)
    return lines, resolved


# --------------------------------------------------------------------------
# Catalog structure parser
# --------------------------------------------------------------------------

_SKILL_LINE_RE = re.compile(r"^(\d+)\s*[.)]\s*Skill\s+Name\s*[:\-]\s*(.+)$",
                            re.IGNORECASE)
_DESC_LINE_RE = re.compile(r"^Description\s*[:\-]\s*(.*)$", re.IGNORECASE)
_CATEGORY_LINE_RE = re.compile(r"^(\d+)\s*[.)]\s+(.+)$")
_APPENDIX_LINE_RE = re.compile(r"^Appendix\s+([A-Za-z])\s*[:.\-]?\s*(.*)$",
                               re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r"^[-*]\s+(.*)$")


def _join_text(parts: Sequence[str]) -> str:
    text = " ".join(p.strip() for p in parts if p.strip())
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def parse_catalog(text: str, fmt: str = "auto",
                  ignore_patterns: Optional[Sequence[str]] = None,
                  deadline: Optional[Deadline] = None,
                  max_lines: int = 200000) -> Dict[str, Any]:
    """Parse a normalized catalog source into a raw structure:
    {categories: [{number, name, skills: [{number, name, description}]}],
     appendices: [{letter, title, items: [str]}], preamble: [str],
     ignored_lines: int, format: str}
    """
    lines, resolved = normalize_source(text, fmt)
    if len(lines) > max_lines:
        raise ToolError(
            "E_INPUT_TOO_LARGE",
            "Source has %d lines, above the %d line limit." % (len(lines), max_lines),
            "Split the catalog into smaller sources.",
        )
    patterns = [re.compile(p) for p in
                (ignore_patterns if ignore_patterns is not None
                 else DEFAULT_IGNORE_PATTERNS)]

    raw: Dict[str, Any] = {
        "format": resolved,
        "preamble": [],
        "categories": [],
        "appendices": [],
        "ignored_lines": 0,
    }
    ctx = "PREAMBLE"          # PREAMBLE | CAT_HEAD | DESC | APPENDIX
    cur_cat: Optional[Dict[str, Any]] = None
    cur_skill: Optional[Dict[str, Any]] = None
    cur_app: Optional[Dict[str, Any]] = None
    app_items_numbered = False
    title_parts: List[str] = []
    desc_parts: List[str] = []

    def flush_desc() -> None:
        nonlocal desc_parts
        if cur_skill is not None and desc_parts:
            cur_skill["description"] = _join_text(desc_parts)
        desc_parts = []

    for line in lines:
        if deadline is not None:
            deadline.check("parsing")
        if any(p.match(line) for p in patterns):
            raw["ignored_lines"] += 1
            continue

        m_app = _APPENDIX_LINE_RE.match(line)
        m_skill = _SKILL_LINE_RE.match(line)
        m_desc = _DESC_LINE_RE.match(line)
        m_cat = _CATEGORY_LINE_RE.match(line)
        m_bul = _BULLET_LINE_RE.match(line)

        if m_app:
            flush_desc()
            ctx = "APPENDIX"
            cur_cat = cur_skill = None
            app_items_numbered = False
            cur_app = {"letter": m_app.group(1).upper(),
                       "title": (m_app.group(2) or "").strip(),
                       "items": []}
            raw["appendices"].append(cur_app)
            continue

        if ctx == "APPENDIX" and cur_app is not None:
            if m_cat and not m_skill:
                cur_app["items"].append(m_cat.group(2).strip())
                app_items_numbered = True
            elif m_bul:
                cur_app["items"].append(m_bul.group(1).strip())
            else:
                items = cur_app["items"]
                starts_upper = bool(line[:1].isupper())
                ends_sentence = bool(items) and items[-1][-1:] in ".:!?"
                if items and app_items_numbered:
                    items[-1] = (items[-1] + " " + line).strip()  # wrap cont.
                elif items and not (starts_upper and ends_sentence):
                    items[-1] = (items[-1] + " " + line).strip()
                else:
                    items.append(line)
            continue

        if m_skill:
            flush_desc()
            if cur_cat is None:                       # skill before any category
                cur_cat = {"number": 0, "name": "Uncategorized", "skills": []}
                raw["categories"].append(cur_cat)
            ctx = "DESC"
            cur_skill = {"number": int(m_skill.group(1)),
                         "name": m_skill.group(2).strip(),
                         "description": ""}
            cur_cat["skills"].append(cur_skill)
            continue

        if m_cat and not m_skill and ctx in ("PREAMBLE", "CAT_HEAD", "DESC"):
            candidate = m_cat.group(2).strip()
            # A numbered line that looks like 'Label: rest' inside an appendix-
            # style block is not a category; categories never carry a colon.
            if ":" not in candidate and len(candidate) <= 120:
                flush_desc()
                cur_cat = {"number": int(m_cat.group(1)),
                           "name": candidate, "skills": []}
                raw["categories"].append(cur_cat)
                title_parts = [candidate]
                ctx = "CAT_HEAD"
                cur_skill = None
                continue

        if m_desc and ctx in ("DESC", "CAT_HEAD"):
            if cur_skill is not None:
                desc_parts = [m_desc.group(1)]
                ctx = "DESC"
            continue

        # Non-structural line.
        if ctx == "CAT_HEAD" and cur_cat is not None:
            # wrapped category title continuation (e.g. '... Developer'
            # followed by 'Experience')
            title_parts.append(line)
            cur_cat["name"] = _join_text(title_parts)
        elif ctx == "DESC" and cur_skill is not None:
            if not desc_parts:
                desc_parts = [line]     # description without 'Description:'
            else:
                desc_parts.append(line)
        elif ctx == "PREAMBLE":
            if len(raw["preamble"]) < 500:
                raw["preamble"].append(line)
        # else: stray line in unknown context -> ignored

    flush_desc()
    return raw


# --------------------------------------------------------------------------
# Normalization: slugs, ids, statistics
# --------------------------------------------------------------------------


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "unnamed"


def normalize_catalog(raw: Dict[str, Any], title: Optional[str] = None,
                      source: Optional[str] = None) -> Dict[str, Any]:
    """Turn a raw parse structure into the canonical dataset shape."""
    used_slugs: Dict[str, int] = {}
    categories: List[Dict[str, Any]] = []
    skills: List[Dict[str, Any]] = []
    total_skills = sum(len(c["skills"]) for c in raw["categories"])
    pad = max(3, len(str(total_skills)))
    skill_no = 0
    for cat_index, cat in enumerate(raw["categories"], start=1):
        cat_slug_base = slugify(cat["name"])
        cat_slug = cat_slug_base
        n = 2
        while any(c["slug"] == cat_slug for c in categories):
            cat_slug = "%s-%d" % (cat_slug_base, n)
            n += 1
        cat_id = "cat-%02d" % cat_index
        categories.append({
            "id": cat_id,
            "number": cat.get("number", cat_index),
            "name": _join_text([cat["name"]]),
            "slug": cat_slug,
        })
        for sk in cat["skills"]:
            skill_no += 1
            base = slugify(sk["name"])
            slug = base
            k = 2
            while slug in used_slugs:
                slug = "%s-%d" % (base, k)
                k += 1
            used_slugs[slug] = 1
            skills.append({
                "id": "skill-%s" % str(skill_no).zfill(pad),
                "category_id": cat_id,
                "number": sk.get("number", 0),
                "name": _join_text([sk["name"]]),
                "slug": slug,
                "description": _join_text([sk.get("description", "")]),
            })
    appendices = []
    for app in raw.get("appendices", []):
        letter = app.get("letter", "x").upper()
        appendices.append({
            "id": "appendix-%s" % letter.lower(),
            "letter": letter,
            "title": _join_text([app.get("title", "")]),
            "items": [_join_text([i]) for i in app.get("items", []) if str(i).strip()],
        })
    dataset: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "catalog": {
            "title": title or "Skill Catalog",
            "source": source or "",
            "source_format": raw.get("format", "text"),
            "schema": "asset-catalog-schema.json",
            "stats": {
                "categories": len(categories),
                "skills": len(skills),
                "appendices": len(appendices),
            },
        },
        "categories": categories,
        "skills": skills,
        "appendices": appendices,
    }
    if raw.get("preamble"):
        dataset["catalog"]["preamble"] = raw["preamble"][:500]
    return dataset


# --------------------------------------------------------------------------
# Validation (built-in JSON-schema-subset validator + semantic checks)
# --------------------------------------------------------------------------

_TYPE_MAP = {
    "object": dict, "array": list, "string": str,
    "integer": int, "number": (int, float), "boolean": bool,
}


def _validate_node(node: Any, schema: Dict[str, Any], path: str,
                   issues: List[Dict[str, str]]) -> None:
    stype = schema.get("type")
    if stype:
        expected = _TYPE_MAP.get(stype)
        if expected is not None:
            ok = isinstance(node, expected) and not (
                stype in ("integer", "number") and isinstance(node, bool))
            if not ok:
                issues.append({"path": path or "$",
                               "message": "expected type %s" % stype})
                return
    if "enum" in schema and node not in schema["enum"]:
        issues.append({"path": path or "$",
                       "message": "value not in enum %s" % schema["enum"]})
    if isinstance(node, str):
        if "pattern" in schema and not re.search(schema["pattern"], node):
            issues.append({"path": path or "$",
                           "message": "does not match pattern %s" % schema["pattern"]})
        if "minLength" in schema and len(node) < schema["minLength"]:
            issues.append({"path": path or "$",
                           "message": "shorter than minLength %d" % schema["minLength"]})
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:
            issues.append({"path": path or "$",
                           "message": "below minimum %s" % schema["minimum"]})
    if isinstance(node, dict):
        for req in schema.get("required", []):
            if req not in node:
                issues.append({"path": path or "$",
                               "message": "missing required property '%s'" % req})
        for prop, sub in schema.get("properties", {}).items():
            if prop in node:
                _validate_node(node[prop], sub,
                               "%s.%s" % (path, prop) if path else prop, issues)
    if isinstance(node, list):
        if "minItems" in schema and len(node) < schema["minItems"]:
            issues.append({"path": path or "$",
                           "message": "fewer than minItems %d" % schema["minItems"]})
        if "maxItems" in schema and len(node) > schema["maxItems"]:
            issues.append({"path": path or "$",
                           "message": "more than maxItems %d" % schema["maxItems"]})
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(node):
                _validate_node(item, item_schema, "%s[%d]" % (path, i), issues)


def validate_dataset(dataset: Any, schema: Optional[Dict[str, Any]] = None,
                     semantic: bool = True) -> Dict[str, Any]:
    """Validate a dataset against the schema subset, then run semantic
    cross-checks. Returns {valid, issues:[{path,message,level}]}."""
    issues: List[Dict[str, str]] = []
    if schema:
        _validate_node(dataset, schema, "", issues)
    if semantic and isinstance(dataset, dict):
        skills = dataset.get("skills")
        cats = dataset.get("categories")
        if isinstance(skills, list) and isinstance(cats, list):
            cat_ids = {c.get("id") for c in cats if isinstance(c, dict)}
            seen_ids = set()
            seen_slugs = set()
            for i, sk in enumerate(skills):
                if not isinstance(sk, dict):
                    issues.append({"path": "skills[%d]" % i,
                                   "message": "skill entry is not an object"})
                    continue
                where = "skills[%d]" % i
                if sk.get("id") in seen_ids:
                    issues.append({"path": where,
                                   "message": "duplicate skill id '%s'" % sk.get("id")})
                seen_ids.add(sk.get("id"))
                if sk.get("slug") in seen_slugs:
                    issues.append({"path": where,
                                   "message": "duplicate slug '%s'" % sk.get("slug")})
                seen_slugs.add(sk.get("slug"))
                if sk.get("category_id") not in cat_ids:
                    issues.append({"path": where,
                                   "message": "category_id '%s' does not exist"
                                              % sk.get("category_id")})
                if not str(sk.get("name", "")).strip():
                    issues.append({"path": where, "message": "empty skill name"})
                if not str(sk.get("description", "")).strip():
                    issues.append({"path": where,
                                   "message": "empty description for '%s'"
                                              % sk.get("name"),
                                   })
        stats = (dataset.get("catalog") or {}).get("stats") \
            if isinstance(dataset.get("catalog"), dict) else None
        if isinstance(stats, dict) and isinstance(skills, list) \
                and isinstance(cats, list):
            if stats.get("skills") != len(skills):
                issues.append({"path": "catalog.stats.skills",
                               "message": "stats say %s but %d skills present"
                                          % (stats.get("skills"), len(skills))})
            if stats.get("categories") != len(cats):
                issues.append({"path": "catalog.stats.categories",
                               "message": "stats say %s but %d categories present"
                                          % (stats.get("categories"), len(cats))})
    for issue in issues:
        issue.setdefault("level", "error")
    return {"valid": not issues, "issue_count": len(issues), "issues": issues}


# --------------------------------------------------------------------------
# Dataset loading helpers
# --------------------------------------------------------------------------


def load_json_file(path: str, what: str, max_bytes: int = 10 * 1024 * 1024,
                   deadline: Optional[Deadline] = None) -> Any:
    text = read_text_with_retry(path, max_bytes)
    if deadline is not None:
        deadline.check("loading %s" % what)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ToolError(
            "E_BAD_JSON",
            "%s file is not valid JSON (%s): %s" % (what.capitalize(), path, exc),
            "Fix the JSON syntax or regenerate the file with the ingest command.",
        )


def load_dataset(path: str, schema: Optional[Dict[str, Any]] = None,
                 validate: bool = True,
                 deadline: Optional[Deadline] = None) -> Dict[str, Any]:
    dataset = load_json_file(path, "dataset", deadline=deadline)
    if not isinstance(dataset, dict) or "skills" not in dataset:
        raise ToolError(
            "E_BAD_DATASET",
            "File is not a catalog dataset (no 'skills' key): %s" % path,
            "Point --dataset at a file produced by the ingest command.",
        )
    if validate:
        result = validate_dataset(dataset, schema)
        if not result["valid"]:
            first = result["issues"][0]
            raise ToolError(
                "E_DATASET_INVALID",
                "Dataset failed validation with %d issue(s); first: %s at %s"
                % (result["issue_count"], first["message"], first["path"]),
                "Run: tools-skill-catalog.py validate --dataset <path> "
                "for the full report, or pass --no-validate to proceed anyway.",
            )
    return dataset


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

STOPWORDS = frozenset("""a an the and or but if then else of to in on at by for
with without from into onto over under about across after before during within
is are was were be been being am do does did done have has had having i you he
she it we they me him her us them my your his its our their this that these
those there here as not no nor so such can could will would shall should may
might must please want wants need needs like make makes using use used using
me us ours mine yours best better good great nice
build builds building create creates creating""".split())


def tokenize(text: str) -> List[str]:
    s = unicodedata.normalize("NFKD", text.lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    tokens = re.split(r"[^a-z0-9+#.]+", s)
    out = []
    for t in tokens:
        t = t.strip(".")
        if t and t not in STOPWORDS and len(t) > 1:
            out.append(t)
    return out


def _token_in_text(token: str, text: str) -> bool:
    """True when token appears in text delimited by non-alphanumerics, so the
    tail of a hyphenated word ('match' in 'no-match') never counts as a hit."""
    if not token:
        return False
    pat = r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])"
    return re.search(pat, text) is not None


def search_skills(dataset: Dict[str, Any], query: str, limit: int = 10,
                  category: Optional[str] = None,
                  deadline: Optional[Deadline] = None) -> Dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise ToolError("E_EMPTY_QUERY", "Search query is empty.",
                        "Pass --query with at least one search term.")
    if limit < 1 or limit > 500:
        raise ToolError("E_BAD_LIMIT", "Limit must be between 1 and 500.",
                        "Adjust --limit.")
    terms = tokenize(query)
    lowered = query.lower()
    if not terms:
        raise ToolError(
            "E_EMPTY_QUERY",
            "Query '%s' contains no searchable terms." % query,
            "Use letters, digits, or technical tokens like 'react', 'pwa', 'c++'.",
        )
    cat_filter = _resolve_category(dataset, category) if category else None
    cat_by_id = {c["id"]: c for c in dataset["categories"]}
    results = []
    for sk in dataset["skills"]:
        if deadline is not None:
            deadline.check("search")
        if cat_filter and sk["category_id"] != cat_filter["id"]:
            continue
        name_l = sk["name"].lower()
        desc_l = sk["description"].lower()
        cat_name_l = cat_by_id.get(sk["category_id"], {}).get("name", "").lower()
        score = 0.0
        matched = []
        if lowered in name_l:
            score += 12.0
            matched.append("phrase-in-name")
        # A literal query string inside the text is real evidence; the old
        # substring-only path scored the tail of "no-match" and returned
        # unrelated skills, so require a word-boundary hit to earn points.
        name_tokens = set(tokenize(sk["name"]))
        desc_tokens = set(tokenize(sk["description"]))
        cat_tokens = set(tokenize(cat_name_l))
        if len(lowered) >= 3 and len(terms) > 1:
            pat = r"(?<![a-z0-9])" + re.escape(lowered) + r"(?![a-z0-9])"
            if re.search(pat, desc_l):
                score += 5.0
                matched.append("phrase-in-description")
        for t in terms:
            if t in name_tokens:
                score += 4.0
                matched.append("name:%s" % t)
            elif _token_in_text(t, name_l):
                score += 2.5
                matched.append("name-sub:%s" % t)
            if t in desc_tokens:
                score += 1.5
                matched.append("desc:%s" % t)
            elif _token_in_text(t, desc_l):
                score += 0.75
                matched.append("desc-sub:%s" % t)
            if t in cat_tokens:
                score += 0.5
                matched.append("cat:%s" % t)
        if score > 0:
            results.append({
                "id": sk["id"], "slug": sk["slug"], "name": sk["name"],
                "category": cat_by_id.get(sk["category_id"], {}).get("name", ""),
                "category_slug": cat_by_id.get(sk["category_id"], {}).get("slug", ""),
                "description": sk["description"],
                "score": round(score, 2), "matched": matched,
            })
    results.sort(key=lambda r: (-r["score"], r["id"]))
    return {"query": query, "total_matches": len(results),
            "returned": min(limit, len(results)),
            "results": results[:limit]}


def _resolve_category(dataset: Dict[str, Any], key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    key_s = key.strip()
    for c in dataset["categories"]:
        if c["slug"] == slugify(key_s) or c["id"] == key_s \
                or c["name"].lower() == key_s.lower() \
                or str(c["number"]) == key_s:
            return c
    # unique substring fallback
    hits = [c for c in dataset["categories"]
            if key_s.lower() in c["name"].lower() or key_s.lower() in c["slug"]]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(
            "E_UNKNOWN_CATEGORY",
            "No category matches '%s'." % key,
            "Run: tools-skill-catalog.py list  (to see category slugs).",
        )
    raise ToolError(
        "E_AMBIGUOUS_CATEGORY",
        "Category '%s' is ambiguous (%d matches)." % (key, len(hits)),
        "Use the exact slug, e.g. --category %s." % hits[0]["slug"],
    )


def resolve_skill(dataset: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Resolve a skill by slug, id, exact name, or unique name substring."""
    key_s = (key or "").strip()
    if not key_s:
        raise ToolError("E_EMPTY_QUERY", "No skill selector given.",
                        "Pass --skill <slug|id|name>.")
    slug_key = slugify(key_s)
    for sk in dataset["skills"]:
        if sk["slug"] == slug_key or sk["id"] == key_s:
            return sk
    for sk in dataset["skills"]:
        if sk["name"].lower() == key_s.lower():
            return sk
    hits = [sk for sk in dataset["skills"] if key_s.lower() in sk["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(
            "E_UNKNOWN_SKILL",
            "No skill matches '%s'." % key,
            "Run: tools-skill-catalog.py search --query '<terms>' to find it.",
        )
    raise ToolError(
        "E_AMBIGUOUS_SKILL",
        "Skill '%s' is ambiguous (%d matches, e.g. %s)."
        % (key, len(hits), hits[0]["slug"]),
        "Use the exact slug from the search results.",
    )


# --------------------------------------------------------------------------
# Routing: request -> smallest covering set of skills
# --------------------------------------------------------------------------

DEFAULT_SIGNALS: Dict[str, Any] = {"signal_groups": {}, "weights": {},
                                   "limits": {}, "category_affinities": {},
                                   "lifecycle_categories": {},
                                   "composition": {}, "spec_defaults": {}}


def _extract_signals(request_l: str, groups: Dict[str, Dict[str, List[str]]],
                     ) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for group, values in groups.items():
        for value, phrases in values.items():
            for phrase in [value] + list(phrases):
                p = phrase.lower().strip()
                if not p:
                    continue
                if re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])",
                             request_l):
                    found.setdefault(group, [])
                    if value not in found[group]:
                        found[group].append(value)
                    break
    return found


def route_request(dataset: Dict[str, Any], signals_cfg: Dict[str, Any],
                  request: str, max_skills: Optional[int] = None,
                  deadline: Optional[Deadline] = None) -> Dict[str, Any]:
    request = (request or "").strip()
    if not request:
        raise ToolError("E_EMPTY_QUERY", "Routing request is empty.",
                        "Pass --request '<text>' or --request-file <path> "
                        "or --request - for stdin.")
    limits = signals_cfg.get("limits", {})
    max_chars = int(limits.get("max_request_chars", 4000))
    if len(request) > max_chars:
        raise ToolError(
            "E_INPUT_TOO_LARGE",
            "Request is %d chars, above the %d limit." % (len(request), max_chars),
            "Shorten the request or raise limits.max_request_chars in the "
            "signals configuration.",
        )
    if max_skills is None:
        max_skills = int(limits.get("default_max_skills", 6))
    if max_skills < 1 or max_skills > 25:
        raise ToolError("E_BAD_LIMIT", "max-skills must be between 1 and 25.",
                        "Adjust --max-skills.")
    groups = signals_cfg.get("signal_groups", {})
    weights = signals_cfg.get("weights", {})
    w_name = float(weights.get("name_token", 3.0))
    w_desc = float(weights.get("desc_token", 1.0))
    w_sig = float(weights.get("signal_match", 2.5))
    w_aff = float(weights.get("category_affinity", 1.0))
    w_life = float(weights.get("lifecycle_category", 1.5))
    w_comp = float(weights.get("composition", 2.0))
    w_subject = float(weights.get("subject_term_factor", 0.15))
    group_prio = signals_cfg.get("signal_group_priority", {})
    group_affinities = signals_cfg.get("group_affinities", {})
    w_group_aff = float(weights.get("group_affinity", 1.2))
    affinities = signals_cfg.get("category_affinities", {})
    lifecycle_cats = signals_cfg.get("lifecycle_categories", {})
    composition = signals_cfg.get("composition", {})

    request_l = request.lower()
    found = _extract_signals(request_l, groups)
    req_tokens = set(tokenize(request))
    if not found and not req_tokens:
        raise ToolError(
            "E_EMPTY_QUERY",
            "Request contains no usable terms.",
            "Describe the target app, platform, language, or task.",
        )

    cat_by_id = {c["id"]: c for c in dataset["categories"]}
    # composition boost slugs for detected lifecycle stages
    comp_slugs: Dict[str, List[str]] = {}
    for stage in found.get("lifecycle", []):
        for name_or_slug in composition.get(stage, []):
            comp_slugs.setdefault(slugify(name_or_slug), []).append(stage)

    # Ubiquitous catalog words ("app", "engineer", "web") must not dominate a
    # ranking just because many skill names contain them; rare words such as
    # "flutter" or "wcag" carry the real intent.
    name_df: Dict[str, int] = {}
    for sk in dataset["skills"]:
        for t in set(tokenize(sk["name"])):
            name_df[t] = name_df.get(t, 0) + 1
    total_skills = max(len(dataset["skills"]), 1)

    def token_factor(token: str) -> float:
        ratio = name_df.get(token, 0) / total_skills
        if ratio <= 0.03:
            return 1.0
        if ratio <= 0.12:
            return 0.6
        return 0.25

    scored = []
    for sk in dataset["skills"]:
        if deadline is not None:
            deadline.check("routing")
        cat = cat_by_id.get(sk["category_id"], {})
        cat_slug = cat.get("slug", "")
        name_l = sk["name"].lower()
        desc_l = sk["description"].lower()
        name_tokens = tokenize(sk["name"])
        desc_tokens = set(tokenize(sk["description"]))
        score = 0.0
        reasons: List[str] = []
        covered: List[str] = []

        overlap = req_tokens.intersection(set(name_tokens))
        if overlap:
            score += w_name * sum(token_factor(t) for t in overlap)
            reasons.append("name matches request terms: %s"
                           % ", ".join(sorted(overlap)))
        d_overlap = req_tokens.intersection(desc_tokens)
        if d_overlap:
            score += w_desc * sum(sorted((token_factor(t) for t in d_overlap),
                                         reverse=True)[:4])
            reasons.append("description matches: %s"
                           % ", ".join(sorted(d_overlap)[:4]))

        for group, values in found.items():
            if group == "lifecycle":
                continue
            for value in values:
                phrases = [value] + list(
                    (groups.get(group, {}) or {}).get(value, []))
                in_name = False
                in_desc = False
                for phrase in phrases:
                    p = phrase.lower()
                    if p and p in name_l:
                        in_name = True
                        break
                    if p and p in desc_l:
                        in_desc = True
                if in_name or in_desc:
                    # A signal named in the skill NAME is the strongest claim
                    # (a Flutter skill for a Flutter request). A signal merely
                    # echoed in a description earns the discounted factor, so
                    # every mobile skill mentioning "offline" cannot tie.
                    # Groups are then prioritised: a framework/language choice
                    # outranks a platform mention, which outranks lifecycle.
                    base = w_sig * float(group_prio.get(group, 1.0))
                    score += base if in_name else base * w_subject
                    tag = "%s:%s" % (group, value)
                    covered.append(tag)
                    reasons.append("covers %s signal (%s)"
                                   % (tag, "named in skill name" if in_name
                                      else "mentioned in description"))
                elif cat_slug in affinities.get(value, []):
                    score += w_aff
                    covered.append("%s:%s" % (group, value))
                    reasons.append("subject area '%s' fits %s"
                                   % (cat.get("name", ""), value))
                elif cat_slug in group_affinities.get(group, {}).get(value, []):
                    # fallback area for a signal no skill names outright,
                    # e.g. "auth" -> security category, "django" -> backend
                    score += w_aff * w_group_aff
                    covered.append("%s:%s" % (group, value))
                    reasons.append("subject area '%s' is the fallback area for "
                                   "%s:%s" % (cat.get("name", ""), group, value))
        for stage in found.get("lifecycle", []):
            if cat_slug in lifecycle_cats.get(stage, []):
                score += w_life
                covered.append("lifecycle:%s" % stage)
                reasons.append("category serves the '%s' stage" % stage)
        if sk["slug"] in comp_slugs:
            score += w_comp
            for stage in comp_slugs[sk["slug"]]:
                covered.append("composition:%s" % stage)
            reasons.append("named in the app-factory composition for: %s"
                           % ", ".join(sorted(set(comp_slugs[sk["slug"]]))))
        if score > 0:
            scored.append({
                "id": sk["id"], "slug": sk["slug"], "name": sk["name"],
                "category": cat.get("name", ""), "category_slug": cat_slug,
                "description": sk["description"],
                "score": round(score, 2),
                "reasons": reasons, "covers": sorted(set(covered)),
            })
    scored.sort(key=lambda r: (-r["score"], r["id"]))

    # Greedy smallest-covering-set selection: always keep the best overall
    # match, then add the best skill for each still-uncovered signal value.
    selected: List[Dict[str, Any]] = []
    selected_slugs = set()

    def take(entry: Dict[str, Any]) -> None:
        if entry["slug"] not in selected_slugs and len(selected) < max_skills:
            selected.append(entry)
            selected_slugs.add(entry["slug"])

    pending: List[str] = []
    for group, values in found.items():
        for value in values:
            pending.append("%s:%s" % (group, value)
                           if group != "lifecycle" else "lifecycle:%s" % value)

    # Walk candidates in score order and take any that covers a signal that is
    # still uncovered, then fill the remaining budget by score. This keeps the
    # ranking honest (no low-scoring skill outranks a strong one) while still
    # assembling a covering set across the request's skill areas.
    pending_set = set(pending)
    selected_tags: set = set()
    for cand in scored:
        if len(selected) >= max_skills:
            break
        if (set(cand["covers"]) & pending_set) - selected_tags:
            take(cand)
            selected_tags.update(cand["covers"])
    for cand in scored:                    # fill remaining budget by score
        if len(selected) >= max_skills:
            break
        take(cand)
    selected.sort(key=lambda r: (-r["score"], r["id"]))

    covered_tags = set()
    for s in selected:
        covered_tags.update(s["covers"])
    uncovered = [t for t in pending if t not in covered_tags]

    orchestration = None
    stages = set(found.get("lifecycle", []))
    groups_hit = {g for g in found if g != "lifecycle"}
    if len(stages) >= 3 or ("build" in stages and groups_hit):
        orch_slug = slugify(str(limits.get("orchestrator_skill",
                                           "App Factory Orchestrator")))
        for sk in dataset["skills"]:
            if sk["slug"] == orch_slug:
                orchestration = {
                    "slug": sk["slug"], "name": sk["name"],
                    "why": "Request spans multiple lifecycle stages/areas; the "
                           "catalog recommends orchestrating through this skill.",
                }
                break
    return {
        "request": request,
        "matched_signals": found,
        "max_skills": max_skills,
        "selected": selected,
        "uncovered_signals": uncovered,
        "orchestration_hint": orchestration,
        "candidates_considered": len(scored),
        "runners_up": [
            {"slug": r["slug"], "name": r["name"], "score": r["score"]}
            for r in scored if r["slug"] not in selected_slugs
        ][:5],
    }


# --------------------------------------------------------------------------
# Specification drafting (catalog "Recommended Catalog Fields")
# --------------------------------------------------------------------------

SPEC_FIELDS = [
    "skill_name", "trigger_description", "inputs", "outputs", "toolchain",
    "version_policy", "workflow", "verification", "boundaries",
]

_STAGE_BY_CATEGORY_KEYWORDS = [
    (("strategy", "architecture", "requirements", "product"), "design"),
    (("testing", "verification", "quality"), "verify"),
    (("security", "privacy"), "secure"),
    (("documentation", "technical-writing", "developer"), "document"),
    (("packaging", "distribution", "stores"), "package"),
    (("devops", "ci-cd", "release"), "deliver"),
    (("observability", "reliability", "operations"), "operate"),
    (("repair", "modernization", "migration", "compatibility"), "modernize"),
    (("orchestration", "app-factory"), "orchestrate"),
]


def _primary_stage(category_name: str, description: str) -> str:
    hay = (slugify(category_name) + " " + description.lower())
    for keys, stage in _STAGE_BY_CATEGORY_KEYWORDS:
        if any(k in hay for k in keys):
            return stage
    return "build"


def generate_spec(dataset: Dict[str, Any], signals_cfg: Dict[str, Any],
                  template_text: Optional[str], skill_key: str,
                  fmt: str = "md") -> Dict[str, Any]:
    sk = resolve_skill(dataset, skill_key)
    cat_by_id = {c["id"]: c for c in dataset["categories"]}
    cat = cat_by_id.get(sk["category_id"], {"name": "", "slug": ""})
    text = (sk["name"] + " " + sk["description"]).lower()
    spec_defaults = signals_cfg.get("spec_defaults", {})

    # toolchain: known technology terms detected in name + description,
    # scanned only over groups listed in spec_defaults.toolchain_groups
    toolchain_groups = spec_defaults.get(
        "toolchain_groups", ["platform", "language", "framework", "delivery"])
    tech_terms: List[str] = []
    all_groups = signals_cfg.get("signal_groups", {})
    for group in toolchain_groups:
        for value, phrases in (all_groups.get(group, {}) or {}).items():
            tech_terms.append(value)
            tech_terms.extend(str(p) for p in phrases)
    tech_terms.extend(str(t) for t in spec_defaults.get("extra_tech_terms", []))
    toolchain = sorted({t for t in tech_terms
                        if t and re.search(r"(?<![a-z0-9])" + re.escape(t.lower())
                                           + r"(?![a-z0-9])", text)})

    stage = _primary_stage(cat.get("name", ""), sk["description"])
    inputs = list(spec_defaults.get("common_inputs", []))
    stage_io = spec_defaults.get("stage_io", {}).get(stage, {})
    inputs.extend(stage_io.get("inputs", []))
    if toolchain:
        inputs.append("Exact target versions for: %s" % ", ".join(toolchain[:6]))
    outputs = list(spec_defaults.get("common_outputs", []))
    outputs.extend(stage_io.get("outputs", []))

    trigger = ("Use when the user asks to work on %s within the scope: %s "
               "Category: %s." % (sk["name"].lower(), sk["description"],
                                  cat.get("name", "")))
    version_policy = str(spec_defaults.get(
        "version_policy",
        "Resolve the exact target version before writing or changing any "
        "code; consult authoritative documentation for that version; never "
        "mix syntax, APIs, build files, or guidance from incompatible "
        "versions; record the supported version range in the skill docs."))
    workflow_steps = list(spec_defaults.get("workflow_common", []))
    workflow_steps.extend(spec_defaults.get("stage_workflow", {}).get(stage, []))
    verification = list(spec_defaults.get("verification_common", []))
    verification.extend(stage_io.get("verification", []))
    boundaries = list(spec_defaults.get("boundaries_common", []))
    boundaries.extend(stage_io.get("boundaries", []))

    fields = {
        "skill_name": {"display_name": sk["name"], "package_slug": sk["slug"],
                       "catalog_id": sk["id"], "category": cat.get("name", ""),
                       "category_slug": cat.get("slug", "")},
        "trigger_description": trigger,
        "inputs": _dedupe(inputs),
        "outputs": _dedupe(outputs),
        "toolchain": toolchain if toolchain else
            ["(none detected automatically - fill in required compilers, "
             "SDKs, package managers, and services)"],
        "version_policy": version_policy,
        "workflow": _dedupe(workflow_steps),
        "verification": _dedupe(verification),
        "boundaries": _dedupe(boundaries),
    }
    if fmt == "json":
        return {"format": "json", "draft": True, "skill": sk["slug"],
                "template": None, "fields": fields}
    if template_text is None:
        raise ToolError("E_MISSING_INPUT",
                        "Spec template not found; cannot render markdown.",
                        "Restore agent-skill-catalog/asset-spec-template.md "
                        "or pass --template <path>.")
    rendered = template_text
    rendered = rendered.replace("{{display_name}}", sk["name"])
    rendered = rendered.replace("{{package_slug}}", sk["slug"])
    rendered = rendered.replace("{{catalog_id}}", sk["id"])
    rendered = rendered.replace("{{category}}", cat.get("name", ""))
    rendered = rendered.replace("{{category_slug}}", cat.get("slug", ""))
    rendered = rendered.replace("{{catalog_description}}", sk["description"])
    rendered = rendered.replace("{{trigger_description}}", fields["trigger_description"])
    rendered = rendered.replace("{{version_policy}}", fields["version_policy"])
    for key in ("inputs", "outputs", "toolchain", "workflow", "verification",
                "boundaries"):
        rendered = rendered.replace(
            "{{%s}}" % key,
            "\n".join("- %s" % item for item in fields[key]))
    return {"format": "md", "draft": True, "skill": sk["slug"],
            "template": "asset-spec-template.md", "fields": fields,
            "markdown": rendered}


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        item = str(item).strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# Export and statistics
# --------------------------------------------------------------------------

CSV_COLUMNS = ["id", "category_number", "category_id", "category",
               "category_slug", "number", "name", "slug", "description"]


def export_dataset(dataset: Dict[str, Any], fmt: str) -> str:
    cat_by_id = {c["id"]: c for c in dataset["categories"]}
    if fmt == "json":
        return json.dumps(dataset, indent=2, ensure_ascii=False) + "\n"
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for sk in dataset["skills"]:
            cat = cat_by_id.get(sk["category_id"], {})
            writer.writerow({
                "id": sk["id"], "category_number": cat.get("number", ""),
                "category_id": sk["category_id"], "category": cat.get("name", ""),
                "category_slug": cat.get("slug", ""), "number": sk["number"],
                "name": sk["name"], "slug": sk["slug"],
                "description": sk["description"],
            })
        return buf.getvalue()
    if fmt == "md":
        title = (dataset.get("catalog") or {}).get("title", "Skill Catalog")
        stats = (dataset.get("catalog") or {}).get("stats", {})
        lines = ["# %s" % title, "",
                 "Categories: %s - Skills: %s" % (stats.get("categories", "?"),
                                                  stats.get("skills", "?")), ""]
        for cat in dataset["categories"]:
            lines.append("## %d. %s" % (cat["number"], cat["name"]))
            lines.append("")
            for sk in dataset["skills"]:
                if sk["category_id"] == cat["id"]:
                    lines.append("%d. **%s** (`%s`)" % (sk["number"], sk["name"],
                                                        sk["slug"]))
                    lines.append("   %s" % sk["description"])
            lines.append("")
        for app in dataset.get("appendices", []):
            lines.append("## Appendix %s: %s" % (app["letter"], app["title"]))
            lines.append("")
            for i, item in enumerate(app["items"], 1):
                lines.append("%d. %s" % (i, item))
            lines.append("")
        return "\n".join(lines)
    raise ToolError("E_BAD_FORMAT", "Unknown export format '%s'." % fmt,
                    "Use one of: json, csv, md.")


def dataset_stats(dataset: Dict[str, Any]) -> Dict[str, Any]:
    cat_by_id = {c["id"]: c for c in dataset["categories"]}
    per_cat = []
    desc_lens = [len(sk["description"]) for sk in dataset["skills"]]
    for cat in dataset["categories"]:
        n = sum(1 for sk in dataset["skills"] if sk["category_id"] == cat["id"])
        per_cat.append({"id": cat["id"], "number": cat["number"],
                        "name": cat["name"], "slug": cat["slug"], "skills": n})
    return {
        "title": (dataset.get("catalog") or {}).get("title", ""),
        "source": (dataset.get("catalog") or {}).get("source", ""),
        "source_format": (dataset.get("catalog") or {}).get("source_format", ""),
        "schema_version": dataset.get("schema_version", ""),
        "categories": len(dataset["categories"]),
        "skills": len(dataset["skills"]),
        "appendices": len(dataset.get("appendices", [])),
        "description_length": {
            "min": min(desc_lens) if desc_lens else 0,
            "max": max(desc_lens) if desc_lens else 0,
            "avg": round(sum(desc_lens) / len(desc_lens), 1) if desc_lens else 0,
        },
        "per_category": per_cat,
    }


# --------------------------------------------------------------------------
# Capability registry (single source of truth for help/capabilities)
# --------------------------------------------------------------------------

CAPABILITIES: List[Dict[str, str]] = [
    {"command": "capabilities",
     "summary": "List every capability of this Skill Tool (text or JSON)."},
    {"command": "ingest",
     "summary": "Parse a catalog source (plain text, Markdown, or copied "
                "HTML) into a normalized dataset JSON. Strips tags, CSS "
                "classes, data attributes, numbering noise, and spacing."},
    {"command": "validate",
     "summary": "Validate a dataset against asset-catalog-schema.json plus "
                "semantic cross-checks (unique ids/slugs, references, stats)."},
    {"command": "list",
     "summary": "Browse categories, or skills inside one category, with "
                "counts. Formats: text, json, csv, md."},
    {"command": "search",
     "summary": "Keyword search across skill names, descriptions, and "
                "categories with weighted scoring."},
    {"command": "route",
     "summary": "Map a free-text user request to the smallest covering set "
                "of catalog skills, with matched signals and reasons."},
    {"command": "spec",
     "summary": "Draft a full skill specification (the catalog's 9 "
                "recommended fields) for one skill, as Markdown or JSON."},
    {"command": "export",
     "summary": "Export the whole dataset as json, csv, or md."},
    {"command": "stats",
     "summary": "Dataset statistics: totals, per-category counts, "
                "description lengths, provenance."},
]
