#!/usr/bin/env python3
"""
tools-music_ai_studio.py  --  core engine for the music-ai-skill-studio Skill Tool.

Standard-library only (Python 3.9+). This module owns everything that is NOT a
command-line concern:

  * catalog parsing      : raw text / Markdown / HTML -> Catalog / Category / Capability
  * case planning        : capability -> operation template -> deliverables + checks
  * validation           : intake, rights, required fields, limits, approvals
  * rendering            : Markdown brief, JSON plan, editable checklist CSV, handoff pack
  * audio QC             : optional, dependency-free WAV file-integrity + basic measurements
  * archiving            : deterministic ZIP write/read with path-traversal guards

The command-line surface lives in tools-music_ai_studio_cli.py, which imports
this module. Nothing here reads argv, writes to stdout without being asked, or
exits the process.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import time
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STUDIO_NAME = "music-ai-skill-studio"
SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------
# Constants, limits and defaults (all overridable from CLI / workflow config)
# --------------------------------------------------------------------------

DEFAULT_LIMITS = {
    "max_input_bytes": 4_000_000,      # parsed catalog / brief text
    "max_catalog_entries": 2_000,      # capabilities parsed from one source
    "max_categories": 200,
    "max_batch_jobs": 200,             # jobs accepted by `batch`
    "max_audio_bytes": 256 * 1024 * 1024,
    "max_audio_scan_seconds": 30.0,    # wall-clock budget per file
    "max_string_length": 4_000,
    "max_name_length": 120,
}

DEFAULT_WORKFLOW = {
    "limits": DEFAULT_LIMITS,
    "retries": 0,
    "timeout_seconds": 60,
    "output": {
        "markdown": True,
        "json": True,
        "checklist_csv": True,
        "archive": True,
    },
    "approvals": {
        # actions that require an explicit --approve token before they may run
        "required_for": [
            "release_publish",
            "rights_signing",
            "payment",
            "credential_use",
            "irreversible_delete",
        ]
    },
    "qc": {
        "clip_ceiling_dbfs": -0.3,
        "minimum_duration_seconds": 1.0,
        "maximum_duration_seconds": 3600.0,
        "require_non_silent": True,
    },
    "naming": {
        "max_slug_words": 6,
    },
    "delivery": {
        "default_profile": "general"
    },
}

APPROVAL_TOKENS = {
    "release_publish": "APPROVE-RELEASE",
    "rights_signing": "APPROVE-RIGHTS",
    "payment": "APPROVE-PAYMENT",
    "credential_use": "APPROVE-CREDENTIALS",
    "irreversible_delete": "APPROVE-DELETE",
}

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class StudioError(Exception):
    """Base class. `code` is a stable, machine-readable identifier."""

    code = "studio_error"
    http_like_status = 400

    def __init__(self, message: str, *, code: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


class InputError(StudioError):
    code = "input_error"


class ValidationError(StudioError):
    code = "validation_error"


class ApprovalRequired(StudioError):
    code = "approval_required"
    http_like_status = 403


class LimitExceeded(StudioError):
    code = "limit_exceeded"
    http_like_status = 413


class NotSupported(StudioError):
    code = "not_supported"
    http_like_status = 501


# --------------------------------------------------------------------------
# Text hygiene: strip HTML, normalize whitespace, slugify
# --------------------------------------------------------------------------

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_BLOCK_BREAK = re.compile(r"</?(p|div|li|tr|h[1-6]|br|section|article|table|ul|ol)\b[^>]*>", re.I)
_ENTITY = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&mdash;": "-", "&ndash;": "-",
}
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u25aa\u00b7\u2013\u2014]+|\d+[.)]|[a-zA-Z][.)])\s+")
_NUMBERING = re.compile(r"^\s*(?:\d+[.)]\s*)+")


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*[a-zA-Z][^>]*>", text))


def html_to_text(html: str, *, max_bytes: int = DEFAULT_LIMITS["max_input_bytes"]) -> str:
    """Convert HTML (or copied rich text) to plain text lines, no third-party deps."""
    if len(html.encode("utf-8", "ignore")) > max_bytes:
        raise LimitExceeded("HTML input exceeds max_input_bytes")
    s = _HTML_COMMENT.sub(" ", html)
    s = _SCRIPT_STYLE.sub(" ", s)
    s = _BLOCK_BREAK.sub("\n", s)
    s = _TAG.sub(" ", s)
    for k, v in _ENTITY.items():
        s = s.replace(k, v)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return s


def normalize_blank_lines(text: str) -> str:
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(lines)


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str, *, max_words: int = 6, max_len: int = 60) -> str:
    """Short lowercase hyphenated slug: ^[a-z0-9]+(-[a-z0-9]+)*$"""
    s = text.lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    words = [w for w in s.split("-") if w][:max_words]
    s = "-".join(words)
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "untitled"


def clean_skill_name(raw: str, *, max_len: int = DEFAULT_LIMITS["max_name_length"]) -> str:
    s = collapse_ws(raw)
    s = _BULLET.sub("", s)
    s = s.strip(" .:;-\u2013\u2014\"'()[]")
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


# --------------------------------------------------------------------------
# Catalog model
# --------------------------------------------------------------------------

@dataclass
class Capability:
    name: str
    description: str = ""
    slug: str = ""
    category_slug: str = ""
    source_index: int = 0

    def __post_init__(self) -> None:
        self.name = clean_skill_name(self.name)
        if not self.slug:
            self.slug = slugify(self.name)


@dataclass
class Category:
    name: str
    focus: str = ""
    slug: str = ""
    capabilities: List[Capability] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = collapse_ws(self.name).strip(" .:;-\u2013\u2014")
        if not self.slug:
            self.slug = slugify(self.name)

    def add(self, cap: Capability) -> None:
        cap.category_slug = self.slug
        self.capabilities.append(cap)


@dataclass
class Catalog:
    categories: List[Category] = field(default_factory=list)
    source_kind: str = "text"          # pdf-text | html | markdown | text
    warnings: List[str] = field(default_factory=list)

    # -- queries ---------------------------------------------------------
    def all_capabilities(self) -> List[Capability]:
        out: List[Capability] = []
        for c in self.categories:
            out.extend(c.capabilities)
        return out

    def category(self, key: str) -> Optional[Category]:
        """Find a category by number, slug, or (case-insensitive) name fragment."""
        k = collapse_ws(key).lower().strip(" .")
        if re.fullmatch(r"\d{1,3}", k):
            idx = int(k) - 1
            if 0 <= idx < len(self.categories):
                return self.categories[idx]
            return None
        for c in self.categories:
            if c.slug == slugify(key) or c.name.lower() == k:
                return c
        for c in self.categories:
            if k and k in c.name.lower():
                return c
        return None

    def capability(self, key: str) -> Optional[Capability]:
        k = collapse_ws(key)
        for cap in self.all_capabilities():
            if cap.name.lower() == k.lower():
                return cap
        sk = slugify(key)
        for cap in self.all_capabilities():
            if cap.slug == sk:
                return cap
        for cap in self.all_capabilities():
            if k.lower() in cap.name.lower():
                return cap
        return None

    def stats(self) -> Dict[str, Any]:
        return {
            "categories": len(self.categories),
            "capabilities": len(self.all_capabilities()),
            "category_names": [c.name for c in self.categories],
            "source_kind": self.source_kind,
            "warnings": list(self.warnings),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_kind": self.source_kind,
            "warnings": list(self.warnings),
            "categories": [
                {
                    "name": c.name,
                    "slug": c.slug,
                    "focus": c.focus,
                    "capabilities": [
                        {"name": k.name, "slug": k.slug,
                         "description": k.description, "source_index": k.source_index}
                        for k in c.capabilities
                    ],
                }
                for c in self.categories
            ],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Catalog":
        cat = Catalog(source_kind=d.get("source_kind", "text"),
                      warnings=list(d.get("warnings") or []))
        for cd in d.get("categories") or []:
            c = Category(name=cd.get("name", "untitled"), focus=cd.get("focus", ""),
                         slug=cd.get("slug") or "")
            for kd in cd.get("capabilities") or []:
                c.add(Capability(name=kd.get("name", ""), description=kd.get("description", ""),
                                 slug=kd.get("slug") or "",
                                 source_index=int(kd.get("source_index") or 0)))
            cat.categories.append(c)
        return cat


# --------------------------------------------------------------------------
# Catalog parsing
# --------------------------------------------------------------------------

_CAT_HEADER = re.compile(
    r"^(?:#{1,6}\s*)?(?:\d{1,3}\s*[.)\-\u2013]?\s*)?(?P<name>[A-Z][A-Za-z0-9&,'\-\u2013/() ]{2,90}?)"
    r"(?:\s*\(\s*\d+\s*skills?\s*\))?\s*:?\s*$"
)
_CAT_FOCUS = re.compile(r"^(?:this\s+)?categor(?:y|ies)\s+(?:covers?|includes?|focuses? on)\s+(?P<focus>.+)$", re.I)
_SKILL_NAME = re.compile(
    r"^(?:\d{1,3}\s*[.)\-\u2013]\s*)?(?:\*\*)?skill\s*name\s*(?:\*\*)?\s*:\s*(?P<name>.+)$", re.I)
_SKILL_NAME_ALT = re.compile(
    r"^(?:\d{1,3}\s*[.)\-\u2013]\s*)?(?:\*\*)?(?P<name>[A-Z][A-Za-z0-9'&/\- ]{2,70}?)(?:\*\*)?\s*$")
_DESC = re.compile(r"^(?:\*\*)?description(?:\*\*)?\s*:\s*(?P<desc>.+)$", re.I)
_DESC_CONT = re.compile(r"^(?!\d{1,3}\s*[.)])(?!\s*$)[A-Za-z(\"'].+")
_BOILERPLATE = re.compile(
    r"^(?:music\s+ai\s+skill\s+catalog\s*\|\s*\d+"
    r"|page\s+\d+(?:\s+of\s+\d+)?"
    r"|table\s+of\s+contents"
    r"|contents)$", re.I)
_STOP_HEADINGS = re.compile(
    r"^(?:category\s+index|music\s+ai\s+orientation|purpose\s*:|"
    r"recommended\s+skill\s+construction\s+pattern|"
    r"implementation\s+checklist.*)$", re.I)


def _next_nonempty(lines: Sequence[str], start: int) -> Optional[str]:
    for j in range(start, len(lines)):
        cand = lines[j]
        if cand and not _is_numbering_only(cand) and not _BOILERPLATE.match(cand):
            return cand
    return None


def _is_numbering_only(line: str) -> bool:
    return bool(re.fullmatch(r"[\d.)\-\u2013\s]+", line))


_STANDARD_SCOPE = re.compile(
    r"^I?t\s+defines\s+required\s+inputs\s+and\s+constraints.*$", re.I)


def _join_paragraph(lines: Sequence[str], start: int, first: str) -> Tuple[str, int]:
    """Merge hard-wrapped continuation lines into one logical line.

    Returns (joined_text, index_of_next_unconsumed_line). Continuation lines are
    lines that start like running prose, not like a new numbered item, skill
    name, "Description:" label or category header.
    """
    parts = [first]
    i = start + 1
    while i < len(lines):
        nxt = lines[i]
        if not nxt or _is_numbering_only(nxt):
            break
        bare_nxt = _BULLET.sub("", nxt).strip()
        if (_SKILL_NAME.match(bare_nxt) or _DESC.match(bare_nxt) or _CAT_FOCUS.match(bare_nxt)
                or _BOILERPLATE.match(nxt) or _STOP_HEADINGS.match(nxt)):
            break
        # A short, capitalized, punctuation-free line is a title (section, category
        # or capability heading), not a continuation of a wrapped sentence.
        if _CAT_HEADER.match(bare_nxt) and not bare_nxt.endswith((".", ",", ";", ":")):
            break
        if not re.match(r"^[a-z(\"'\u2018\u201c]", nxt):   # prose continues lowercase
            break
        if parts[-1].rstrip().endswith(".") and re.search(r"\(\s*\d+\s+skills?\s*\)$", parts[-1]):
            break
        parts.append(nxt)
        # A scope sentence is complete once it ends with a period.
        if parts[0].startswith(("This category", "This Category")) and parts[-1].rstrip().endswith("."):
            break
        i += 1
    return collapse_ws(" ".join(parts)), i


def _split_scope(text: str) -> str:
    """Keep only the scope clause, dropping the repeated boilerplate sentence."""
    merged = collapse_ws(text)
    m = re.search(_STANDARD_SCOPE, merged)
    scope = merged[:m.start()].strip() if m else merged
    scope = re.sub(r"^This\s+categor(?:y|ies)\s+(?:covers?|includes?|focuses? on)\s+", "",
                   scope, flags=re.I).strip()
    return scope.rstrip(" .;") + ("." if scope else "")


def _dedupe_slug(cat: Category, name: str) -> str:
    base = slugify(name)
    existing = {k.slug for k in cat.capabilities}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def parse_catalog(raw: str, *, source_kind: str = "auto",
                  max_entries: int = DEFAULT_LIMITS["max_catalog_entries"],
                  max_bytes: int = DEFAULT_LIMITS["max_input_bytes"],
                  default_category: str = "general") -> Catalog:
    """Parse a capability list from plain text, Markdown, copied HTML or PDF text.

    Tolerates: numbering marks, bullets, HTML tags/classes, bold Markdown, hard
    line wraps inside descriptions, repeated page headers, and category index
    listings that duplicate the real category headers.
    """
    if raw is None or not str(raw).strip():
        raise InputError("empty source: no text to parse", code="empty_source")
    raw = str(raw)
    if len(raw.encode("utf-8", "ignore")) > max_bytes:
        raise LimitExceeded(f"source text exceeds max_input_bytes={max_bytes}")

    if source_kind == "auto":
        source_kind = "html" if looks_like_html(raw) else ("markdown" if re.search(r"^\s*#{1,6}\s", raw, re.M) else "text")
    text = html_to_text(raw, max_bytes=max_bytes) if source_kind == "html" else raw
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("**", "").replace("__", "")
    lines = normalize_blank_lines(text).split("\n")

    catalog = Catalog(source_kind=source_kind)
    current: Optional[Category] = None
    pending_desc_for: Optional[Capability] = None
    in_index_block = False
    preamble_caps: List[Capability] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        idx = i
        i += 1
        if not line or _is_numbering_only(line):
            pending_desc_for = None
            continue
        if _BOILERPLATE.match(line):
            continue
        if _STOP_HEADINGS.match(line):
            # A "Category Index" heading is followed by "(n skills)" lines we skip.
            in_index_block = bool(re.match(r"^category\s+index", line, re.I))
            continue

        # -- Category index entries look like "12. Some Name (10 skills)"
        if in_index_block:
            if re.search(r"\(\s*\d+\s+skills?\s*\)\s*$", line):
                continue
            in_index_block = False

        # Bullets, numbering marks and stray marks are stripped before matching
        # so "- Mixing", "• Mixing" and "12. Mixing" behave identically.
        bare = _BULLET.sub("", line).strip()

        m = _SKILL_NAME.match(bare)
        if m:
            name = clean_skill_name(m.group("name"))
            if not name:
                continue
            cat = current
            if cat is None:
                cat = Category(name=_titlecase(default_category.replace("-", " ")))
                catalog.categories.append(cat)
                current = cat
            cap = Capability(name=name, slug=_dedupe_slug(cat, name), source_index=idx + 1)
            cat.add(cap)
            pending_desc_for = cap
            continue

        m = _DESC.match(bare)
        if m:
            joined, i = _join_paragraph(lines, idx, m.group("desc"))
            target = pending_desc_for
            if target is None and current is not None and current.capabilities:
                target = current.capabilities[-1]
            if target is not None:
                target.description = joined[:DEFAULT_LIMITS["max_string_length"]]
            pending_desc_for = target
            continue

        m = _CAT_FOCUS.match(bare)
        if m:
            joined, i = _join_paragraph(lines, idx, m.group("focus"))
            scope = _split_scope(joined)
            if current is None:
                current = Category(name=_titlecase(default_category.replace("-", " ")))
                catalog.categories.append(current)
            if not current.focus:
                current.focus = scope[:DEFAULT_LIMITS["max_string_length"]]
            pending_desc_for = None
            continue

        # Description continuation of a hard-wrapped paragraph. Title-like lines
        # (short, capitalized, no trailing punctuation) always end a paragraph.
        title_like = bool(_CAT_HEADER.match(bare)) and not bare.endswith((".", ",", ";", ":"))
        if (pending_desc_for is not None and not title_like
                and _DESC_CONT.match(bare) and not _SKILL_NAME_ALT.match(bare)):
            merged, i = _join_paragraph(lines, idx, bare)
            merged = _split_scope(pending_desc_for.description + " " + merged)
            pending_desc_for.description = merged[:DEFAULT_LIMITS["max_string_length"]]
            continue

        # --- classify a bare line as a category header or a capability name ---
        nxt = _next_nonempty(lines, i)
        heading = bool(re.match(r"^#{1,6}\s", line))
        nxt_focus = bool(nxt and _CAT_FOCUS.match(_BULLET.sub("", nxt).strip()))
        nxt_skill1 = bool(nxt and re.match(r"^\s*1\s*[.)\-\u2013]\s*(?:\*\*)?skill\s*name\s*:", nxt, re.I))
        nxt_counted = bool(nxt and re.search(r"\(\s*\d+\s+skills?\s*\)\s*$", nxt))
        skills_marker = bool(re.search(r"\(\s*\d+\s+skills?\s*\)\s*$", bare))

        # A bare line is only a category header with positive evidence: a Markdown
        # heading, a counted index entry, or the line before a scope sentence or a
        # "1. Skill Name:" item. Otherwise it is treated as a capability name.
        is_category = heading or nxt_focus or skills_marker or nxt_skill1 or nxt_counted

        hm = _CAT_HEADER.match(bare)
        if hm and is_category:
            name = _titlecase(hm.group("name").strip())
            if name and len(name) <= 90 and not _DESC.match(bare):
                existing = next((c for c in catalog.categories
                                 if c.name.lower() == name.lower()), None)
                if existing is not None:
                    current = existing
                else:
                    current = Category(name=name)
                    catalog.categories.append(current)
                pending_desc_for = None
                continue

        # Capability name without the "Skill Name:" keyword (e.g. "- Song Sketch Builder")
        if 2 <= len(bare) <= 70:
            alt = _SKILL_NAME_ALT.match(bare)
            if alt:
                name = clean_skill_name(alt.group("name"))
                words = name.split()
                if 1 < len(words) <= 8 and not name.endswith(":"):
                    if current is None:
                        # Before the first category header we are in the document
                        # preamble: titles and orientation bullets are not skills.
                        # Keep them aside in case the source is a flat list.
                        preamble_caps.append(Capability(name=name, slug=slugify(name),
                                                        source_index=idx + 1))
                        continue
                    cat = current
                    if cat is None:
                        cat = Category(name=_titlecase(default_category.replace("-", " ")))
                        catalog.categories.append(cat)
                        current = cat
                    cap = Capability(name=name, slug=_dedupe_slug(cat, name), source_index=idx + 1)
                    cat.add(cap)
                    pending_desc_for = cap
                    continue
        pending_desc_for = None

    # Drop empty categories; keep the ones with real content.
    catalog.categories = [c for c in catalog.categories if c.capabilities]
    if not catalog.categories and preamble_caps:
        # Flat list with no category structure at all: use the umbrella category.
        cat = Category(name=_titlecase(default_category.replace("-", " ")))
        for cap in preamble_caps:
            cap.slug = _dedupe_slug(cat, cap.name)
            cat.add(cap)
        catalog.categories.append(cat)
    elif preamble_caps and catalog.categories:
        catalog.warnings.append(
            f"ignored {len(preamble_caps)} line(s) before the first category header "
            "(document title / orientation text)")
    total = len(catalog.all_capabilities())
    if total == 0:
        raise InputError(
            "no capability names found; supply lines such as "
            "'1. Skill Name: Mix Bus Reviewer' or '- Mix Bus Reviewer'",
            code="no_capabilities_found")
    if total > max_entries:
        raise LimitExceeded(f"parsed {total} capabilities, above max_catalog_entries={max_entries}")
    if len(catalog.categories) > DEFAULT_LIMITS["max_categories"]:
        raise LimitExceeded("too many categories in source")

    # Merge duplicate category names case-insensitively (page-break artefacts).
    merged: List[Category] = []
    for c in catalog.categories:
        hit = next((m for m in merged if m.name.lower() == c.name.lower()), None)
        if hit is None:
            merged.append(c)
        else:
            if not hit.focus and c.focus:
                hit.focus = c.focus
            for cap in c.capabilities:
                existing = {k.slug for k in hit.capabilities}
                if cap.slug in existing:
                    cap.slug = _dedupe_slug(hit, cap.name)
                hit.add(cap)
    catalog.categories = merged

    # Drop duplicate capability names inside a category.
    for c in catalog.categories:
        seen, keep = set(), []
        for cap in c.capabilities:
            key = cap.name.lower()
            if key in seen:
                catalog.warnings.append(f"duplicate capability removed: {c.name} / {cap.name}")
                continue
            seen.add(key)
            keep.append(cap)
        c.capabilities = keep
    catalog.categories = [c for c in catalog.categories if c.capabilities]
    if not catalog.categories:
        raise InputError("source parsed to zero capabilities after de-duplication",
                         code="no_capabilities_found")
    return catalog


def _titlecase(name: str) -> str:
    name = collapse_ws(name).strip(" .:;-\u2013\u2014")
    small = {"and", "or", "of", "for", "the", "a", "an", "in", "on", "to", "with"}
    parts = name.split(" ")
    out = []
    for i, w in enumerate(parts):
        if w.isupper() and len(w) > 1:      # keep AI, MIDI, DAW, EQ, QA
            out.append(w)
        elif i > 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


# --------------------------------------------------------------------------
# Operation planning: category -> default deliverable set + QA checks
# --------------------------------------------------------------------------

# Ordered rules: first regex that matches the capability name wins. Each rule
# contributes operation family, deliverable lines and QA checks. A capability can
# gain extra checks from its category profile as well.
_OP_RULES: List[Tuple[str, Dict[str, Any]]] = [
    (r"\b(analyst|analysis|analyser|measur\w*|examiner|profiler|detector)\b", {
        "family": "analysis",
        "deliverables": [
            "Measurement sheet (metric, method, value, tolerance, verdict)",
            "Observation log separated from interpretation",
            "Recommended corrective actions with priority",
        ],
        "checks": ["measurement method cited", "tolerances stated", "interpretation flagged as subjective"],
    }),
    (r"\b(curator|dataset|auditor|license|planner|architect|designer|strategist|builder|design)\b", {
        "family": "planning",
        "deliverables": [
            "Assumptions and open questions",
            "Step-by-step implementation plan with owners and durations",
            "Risk register (risk, likelihood, impact, mitigation, owner)",
            "Acceptance criteria and validation method",
        ],
        "checks": ["assumptions listed", "rights status recorded", "acceptance criteria testable"],
    }),
    (r"\b(writer|lyricist|composer|generator|prompt\w*|hook|verse|chorus|adapter|translator)\b", {
        "family": "generation",
        "deliverables": [
            "Creative brief (audience, mood, references, constraints)",
            "Three controlled variations (A/B/C) with one changed variable each",
            "Selection rationale and rejected-direction notes",
            "Prompt/seed/settings record for reproducibility",
        ],
        "checks": ["no direct imitation of a named living artist", "lyric content reviewed", "settings recorded"],
    }),
    (r"\b(mix\w*|master\w*|stem|isolat\w*|restor\w*|editing|editor|fader|bus)\b", {
        "family": "audio_processing",
        "deliverables": [
            "Source inventory (file, sample rate, bit depth, channels, duration, LUFS)",
            "Processing chain in order with parameter values",
            "Before/after measurement table (peak, true peak, LUFS, phase)",
            "Preserved unprocessed reference copy",
        ],
        "checks": ["no clipped samples", "phase correlation >= 0", "reference copy retained"],
    }),
    (r"\b(review\w*|qa|test\w*|recovery|checker|validator|conformance|diagnos\w*)\b", {
        "family": "verification",
        "deliverables": [
            "Test matrix (case, input, expected, actual, verdict)",
            "Defect list with severity and reproduction steps",
            "Recovery/rollback procedure",
            "Known-passing reference and rerun evidence",
        ],
        "checks": ["empty input tested", "malformed input tested", "recovery path exercised"],
    }),
    (r"\b(rights|license|licensing|publish\w*|royalt\w*|clearance|consent|governance|ethic\w*)\b", {
        "family": "rights_and_release",
        "deliverables": [
            "Rights matrix (asset, owner, license, territory, term, restrictions)",
            "Consent records for voices, performers and identifiable persons",
            "Disclosure text for AI-assisted elements",
            "Approval gate record before publication or signing",
        ],
        "checks": ["every asset has a rights row", "voice consent on file", "human approval recorded"],
    }),
    (r"\b(teach\w*|curricul\w*|lesson\w*|education|tutor\w*|practice|exercise\w*|explain\w*)\b", {
        "family": "education",
        "deliverables": [
            "Learning objectives mapped to activities",
            "Lesson or practice sequence with timings",
            "Assessment rubric with observable criteria",
            "Accessibility and accommodations notes",
        ],
        "checks": ["objectives observable", "rubric has 3+ levels", "accessibility addressed"],
    }),
    (r"\b(deploy\w*|install\w*|stream\w*|network\w*|live|coding|performance|concert|stage)\b", {
        "family": "performance",
        "deliverables": [
            "Technical rider (inputs, outputs, channels, power, network)",
            "Cue sheet with timings and owners",
            "Rehearsal and fallback plan",
            "Show-day checklist and stop conditions",
        ],
        "checks": ["fallback plan present", "cue timings confirmed", "sound level safety limits set"],
    }),
    (r"\b(tag\w*|catalog\w*|archive\w*|metadata|schema|knowledge|transcri\w*|notat\w*)\b", {
        "family": "documentation",
        "deliverables": [
            "Field/term schema with required and optional fields",
            "Controlled vocabulary or notation conventions",
            "Sample record(s) fully populated",
            "Validation rules and naming conventions",
        ],
        "checks": ["every required field populated", "naming convention stated", "sample validates"],
    }),
    (r"\b(market\w*|audience|brand\w*|campaign|distribut\w*|release|promotion)\b", {
        "family": "marketing",
        "deliverables": [
            "Audience segments and message map",
            "Channel plan with cadence and owners",
            "Asset list (copy, art, short-form video, metadata)",
            "Measurement plan (metric, target, cadence)",
        ],
        "checks": ["claims backed by evidence", "metadata complete", "rights cleared for promo assets"],
    }),
    (r"\b(voice|vocal|sing\w*|timbre|instrument|synthesis|patch|sample)\b", {
        "family": "sound_design",
        "deliverables": [
            "Sound/timbre specification (range, register, articulation, space)",
            "Source and synthesis method description",
            "Variation matrix for performance realism",
            "Consent/rights note for any voice source",
        ],
        "checks": ["no unconsented voice cloning", "articulation mapped", "range limits respected"],
    }),
]

_CATEGORY_PROFILES: List[Tuple[str, Dict[str, Any]]] = [
    (r"rights|licens|royalt|governance|ethic", {
        "deliverables": ["Provenance ledger entry (source, chain, date, actor)"],
        "checks": ["provenance recorded", "territory/term stated", "no unlicensed training data asserted"],
        "risk": "high",
    }),
    (r"voice|vocal", {
        "deliverables": ["Voice consent checklist (identity, scope, term, revocation)"],
        "checks": ["consent scope covers intended use", "deception risk reviewed"],
        "risk": "high",
    }),
    (r"mastering|release|distribution|marketing", {
        "deliverables": ["Delivery profile (loudness target, true peak, format, sample rate, naming)"],
        "checks": ["loudness target met", "true peak <= -1.0 dBTP", "file naming matches profile"],
        "risk": "medium",
    }),
    (r"recording|daw|mixing|audio editing|stem", {
        "deliverables": ["Session file list and versioning note"],
        "checks": ["session opens in recipient workflow", "no missing media references"],
        "risk": "medium",
    }),
    (r"accessibility|wellness|hearing|education", {
        "deliverables": ["Accessibility and safety note (captions, contrast, level limits, breaks)"],
        "checks": ["accessibility addressed", "hearing-safety limits stated"],
        "risk": "medium",
    }),
    (r"research|musicology|catalog|archives|collaboration|project management", {
        "deliverables": ["Citation and attribution list"],
        "checks": ["claims cited", "attribution complete"],
        "risk": "medium",
    }),
]

_FALLBACK_RULE = {
    "family": "general",
    "deliverables": [
        "Task scope and required inputs",
        "Draft deliverable with human-reviewable edits",
        "Validation checklist result",
    ],
    "checks": ["inputs validated", "human review recorded"],
}

# Universal checks applied to every case document.
UNIVERSAL_CHECKS = [
    "inputs validated before work began",
    "outputs editable and traceable to the request",
    "rights and consent status recorded",
    "accessibility and safety notes present",
    "human review recorded before handoff",
]


def _match_category_profile(category_name: str, focus: str) -> Optional[Dict[str, Any]]:
    hay = f"{category_name} {focus}".lower()
    for pattern, profile in _CATEGORY_PROFILES:
        if re.search(pattern, hay):
            return profile
    return None


def profile_capability(cap: Capability, category: Category) -> Dict[str, Any]:
    """Return the operation template for one capability."""
    name = cap.name.lower()
    rule = _FALLBACK_RULE
    matched = "fallback"
    for pattern, candidate in _OP_RULES:
        if re.search(pattern, name):
            rule, matched = candidate, pattern
            break
    profile = _match_category_profile(category.name, category.focus) or {}

    deliverables = list(rule.get("deliverables", [])) + \
        [d for d in profile.get("deliverables", []) if d not in rule.get("deliverables", [])]
    checks = list(rule.get("checks", [])) + \
        [c for c in profile.get("checks", []) if c not in rule.get("checks", [])]

    return {
        "capability": cap.name,
        "capability_slug": cap.slug,
        "category": category.name,
        "category_slug": category.slug,
        "family": rule.get("family", "general"),
        "risk": profile.get("risk", "low" if rule.get("family") in ("analysis", "documentation", "education") else "medium"),
        "matched_rule": matched,
        "deliverables": deliverables,
        "checks": checks + [c for c in UNIVERSAL_CHECKS if c not in checks],
        "description": cap.description or (
            f"{cap.name} operates within {category.name.lower()}"
            + (f": {category.focus}" if category.focus else ".")
        ),
    }


# --------------------------------------------------------------------------
# Case intake: validation, defaults, approval gates
# --------------------------------------------------------------------------

_INTAKE_REQUIRED = ("goal", "inputs")


def _coerce_list(value: Any, field_name: str, *, max_items: int = 50) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[;\n]", value) if p.strip()]
    elif isinstance(value, (list, tuple)):
        parts = [collapse_ws(str(v)) for v in value if str(v).strip()]
    else:
        raise ValidationError(f"{field_name} must be a string or list of strings",
                              details={"field": field_name, "received": type(value).__name__})
    if len(parts) > max_items:
        raise LimitExceeded(f"{field_name} has {len(parts)} items, limit is {max_items}")
    for p in parts:
        if len(p) > DEFAULT_LIMITS["max_string_length"]:
            raise LimitExceeded(f"{field_name} item exceeds max_string_length")
    return parts


def _clean_string(value: Any, field_name: str, *, required: bool = False,
                  max_len: int = DEFAULT_LIMITS["max_string_length"]) -> str:
    if value is None:
        if required:
            raise ValidationError(f"missing required field: {field_name}", details={"field": field_name})
        return ""
    if not isinstance(value, (str, int, float)):
        raise ValidationError(f"{field_name} must be a string", details={"field": field_name})
    s = collapse_ws(str(value))
    if required and not s:
        raise ValidationError(f"field is empty: {field_name}", details={"field": field_name})
    if len(s) > max_len:
        raise LimitExceeded(f"{field_name} exceeds {max_len} characters")
    return s


def validate_intake(intake: Optional[Dict[str, Any]], *,
                    limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize and validate a case intake block. Raises ValidationError/InputError."""
    limits = limits or DEFAULT_LIMITS
    if intake is None:
        intake = {}
    if not isinstance(intake, dict):
        raise InputError("intake must be a JSON object", code="intake_not_object")
    if len(intake) > 200:
        raise LimitExceeded("intake has too many fields (limit 200)")

    out: Dict[str, Any] = {
        "goal": _clean_string(intake.get("goal"), "goal", required=True),
        "inputs": _coerce_list(intake.get("inputs"), "inputs"),
        "constraints": _coerce_list(intake.get("constraints"), "constraints"),
        "references": _coerce_list(intake.get("references"), "references"),
        "audience": _clean_string(intake.get("audience"), "audience"),
        "deadline": _clean_string(intake.get("deadline"), "deadline"),
        "platform": _clean_string(intake.get("platform"), "platform"),
        "tempo_bpm": _clean_string(intake.get("tempo_bpm"), "tempo_bpm"),
        "key": _clean_string(intake.get("key"), "key"),
        "meter": _clean_string(intake.get("meter"), "meter"),
        "duration": _clean_string(intake.get("duration"), "duration"),
        "stem_format": _clean_string(intake.get("stem_format"), "stem_format"),
        "rights_status": _clean_string(intake.get("rights_status") or "unknown", "rights_status"),
        "voice_consent": _clean_string(intake.get("voice_consent") or "not_applicable", "voice_consent"),
        "model": _clean_string(intake.get("model"), "model"),
        "seed": _clean_string(intake.get("seed"), "seed"),
        "prompt": _clean_string(intake.get("prompt"), "prompt"),
        "notes": _clean_string(intake.get("notes"), "notes"),
        "actions": _coerce_list(intake.get("actions"), "actions", max_items=20),
    }
    if not out["inputs"]:
        raise ValidationError("missing required field: inputs (list what you already have)",
                              details={"field": "inputs"})

    def _norm(v: str) -> str:
        return re.sub(r"[\s_-]+", " ", v.strip().lower())

    rs = _norm(out["rights_status"])
    allowed_rights = {"original", "cleared", "public domain", "cc0", "cc by", "cc by-sa",
                      "licensed", "unknown", "pending", "restricted"}
    if rs not in allowed_rights:
        raise ValidationError(
            "rights_status must be one of: " + ", ".join(sorted(allowed_rights)),
            details={"field": "rights_status", "received": out["rights_status"]})

    vc = _norm(out["voice_consent"])
    if vc not in {"not applicable", "consented", "public domain", "synthetic none required",
                  "missing", "pending"}:
        raise ValidationError(
            "voice_consent must be one of: not_applicable, consented, public domain, "
            "synthetic none required, missing, pending",
            details={"field": "voice_consent", "received": out["voice_consent"]})

    if out["tempo_bpm"]:
        tb = out["tempo_bpm"].lower().replace(" bpm", "").strip()
        try:
            val = float(tb)
        except ValueError:
            raise ValidationError("tempo_bpm must be a number", details={"field": "tempo_bpm"})
        if not (20 <= val <= 400):
            raise ValidationError("tempo_bpm must be between 20 and 400", details={"field": "tempo_bpm"})
        out["tempo_bpm"] = f"{val:g}"

    if out["meter"] and not re.fullmatch(r"\d{1,2}/\d{1,2}(?:\s*(\+|,)\s*\d{1,2}/\d{1,2})*", out["meter"]):
        raise ValidationError("meter must look like 4/4 or 7/8", details={"field": "meter"})

    if out["duration"] and not re.search(r"\d", out["duration"]):
        raise ValidationError("duration must contain a number, e.g. 3:30 or 210s",
                             details={"field": "duration"})

    out["blocking_flags"] = []
    if rs in {"unknown", "pending", "restricted"}:
        out["blocking_flags"].append(f"rights_status is '{out['rights_status']}' - clear before publishing")
    if vc in {"missing", "pending"}:
        out["blocking_flags"].append(
            f"voice_consent is '{out['voice_consent']}' - do not synthesize or publish")
    out["requires_human_review"] = bool(out["blocking_flags"]) or True
    return out


def check_approvals(actions: Sequence[str], approvals: Sequence[str]) -> Dict[str, Any]:
    """Gate destructive/irreversible/publishing actions behind explicit approval tokens."""
    needed = []
    provided = {a.strip().upper() for a in approvals or ()}
    for act in actions or ():
        key = act.strip().lower()
        token = APPROVAL_TOKENS.get(key)
        if token:
            needed.append({"action": key, "token": token, "approved": token in provided})
    missing = [n for n in needed if not n["approved"]]
    if missing:
        raise ApprovalRequired(
            "approval required for: " + ", ".join(m["action"] for m in missing),
            details={"missing": [m["action"] for m in missing],
                     "tokens_expected": [m["token"] for m in missing],
                     "note": "Re-run with --approve " + " ".join(m["token"] for m in missing)})
    return {"actions": needed, "approved_count": len(needed)}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _safe_ts(value: str) -> str:
    s = str(value or "")
    if not s:
        raise ValidationError("timestamp is required", details={"field": "timestamp"})
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", s):
        raise ValidationError("timestamp must look like 2026-11-09T10:00:00Z",
                              details={"field": "timestamp"})
    return s


def new_case_id(cap_slug: str, nonce: int = 0) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tail = f"-{nonce}" if nonce else ""
    return f"{cap_slug}-{stamp}{tail}"


def build_plan(*, kind: str, title: str, slug: str, category: Optional[str],
               capability: Optional[str], intake: Dict[str, Any],
               profiles: List[Dict[str, Any]], workflow: Dict[str, Any],
               timestamp: str, case_id: str,
               approvals: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble the structured, machine-readable case plan."""
    deliverables: List[str] = []
    checks: List[str] = []
    for p in profiles:
        for d in p["deliverables"]:
            if d not in deliverables:
                deliverables.append(d)
        for c in p["checks"]:
            if c not in checks:
                checks.append(c)
    risks = sorted({p["risk"] for p in profiles},
                   key=lambda r: {"low": 0, "medium": 1, "high": 2}.get(r, 1))
    risk_level = risks[-1] if risks else "low"

    plan = {
        "schema_version": SCHEMA_VERSION,
        "studio": STUDIO_NAME,
        "case_id": case_id,
        "timestamp_utc": timestamp,
        "kind": kind,
        "title": title,
        "slug": slug,
        "category": category,
        "capability": capability,
        "intake": intake,
        "profiles": profiles,
        "coverage": {
            "capabilities_in_case": len(profiles),
            "families": sorted({p["family"] for p in profiles}),
            "risk_level": risk_level,
            "blocking_flags": intake.get("blocking_flags", []),
        },
        "deliverables": deliverables,
        "checks": checks,
        "workflow": {
            "limits": workflow.get("limits", DEFAULT_LIMITS),
            "retries": workflow.get("retries", 0),
            "timeout_seconds": workflow.get("timeout_seconds", 60),
            "output": workflow.get("output", DEFAULT_WORKFLOW["output"]),
        },
        "approvals": approvals or {"actions": [], "approved_count": 0},
        "limitations": [
            "No network calls, model inference or audio DSP are performed by this tool.",
            "LUFS and true-peak figures in any deliverable must come from a dedicated "
            "meter; qc-audio reports sample peak and RMS only.",
            "Judgement-based checks are checklists for a human reviewer, not automated verdicts.",
            "Catalog parser is heuristic; verify counts with `list` before planning.",
        ],
    }
    return plan


def render_markdown(plan: Dict[str, Any]) -> str:
    """Human-readable brief: the primary editable deliverable."""
    it = plan["intake"]
    L: List[str] = []
    L.append(f"# {plan['title']}")
    L.append("")
    L.append(f"- **Case ID:** `{plan['case_id']}`")
    L.append(f"- **Generated (UTC):** {plan['timestamp_utc']}")
    L.append(f"- **Kind:** {plan['kind']}")
    if plan.get("category"):
        L.append(f"- **Category:** {plan['category']}")
    if plan.get("capability"):
        L.append(f"- **Capability:** {plan['capability']}")
    L.append(f"- **Risk level:** {plan['coverage']['risk_level']}")
    L.append("")

    L.append("## 1. Brief")
    L.append("")
    L.append(f"**Goal.** {it['goal']}")
    if it.get("audience"):
        L.append(f"**Audience.** {it['audience']}")
    if it.get("platform"):
        L.append(f"**Target platform.** {it['platform']}")
    if it.get("deadline"):
        L.append(f"**Deadline.** {it['deadline']}")
    L.append("")
    L.append("**Inputs supplied.**")
    L.extend([f"- {x}" for x in it["inputs"]])
    if it.get("constraints"):
        L.append("")
        L.append("**Constraints.**")
        L.extend([f"- {x}" for x in it["constraints"]])
    if it.get("references"):
        L.append("")
        L.append("**References (describe, do not imitate).**")
        L.extend([f"- {x}" for x in it["references"]])
    L.append("")

    L.append("## 2. Musical and technical parameters")
    L.append("")
    L.append("| Parameter | Value |")
    L.append("| --- | --- |")
    for label, key in (("Tempo (BPM)", "tempo_bpm"), ("Key", "key"), ("Meter", "meter"),
                       ("Duration", "duration"), ("Stem format", "stem_format"),
                       ("Model", "model"), ("Seed / settings", "seed")):
        L.append(f"| {label} | {it.get(key) or '_not specified_'} |")
    L.append(f"| Prompt record | {it.get('prompt') or '_not supplied_'} |")
    L.append("")

    L.append("## 3. Work plan")
    L.append("")
    step = 1
    for p in plan["profiles"]:
        L.append(f"### 3.{step} {p['capability']}")
        L.append("")
        L.append(f"_Family: {p['family']} · risk: {p['risk']} · rule: `{p['matched_rule']}`_")
        L.append("")
        L.append("Deliverables:")
        L.extend([f"- [ ] {d}" for d in p["deliverables"]])
        L.append("")
        L.append("Checks:")
        L.extend([f"- [ ] {c}" for c in p["checks"]])
        L.append("")
        step += 1

    L.append("## 4. Consolidated deliverable checklist")
    L.append("")
    L.extend([f"- [ ] {d}" for d in plan["deliverables"]])
    L.append("")
    L.append("## 5. Validation checklist")
    L.append("")
    L.extend([f"- [ ] {c}" for c in plan["checks"]])
    L.append("")

    L.append("## 6. Rights, consent and disclosure")
    L.append("")
    L.append(f"- Rights status: **{it['rights_status']}**")
    L.append(f"- Voice consent: **{it['voice_consent']}**")
    L.append("- Disclosure line for AI-assisted elements: "
             "\"Contains AI-assisted elements. Human-reviewed and edited.\" "
             "(adapt to the target platform's policy)")
    if it.get("blocking_flags"):
        L.append("")
        L.append("**Blocking flags - resolve before publication:**")
        L.extend([f"- {f}" for f in it["blocking_flags"]])
    L.append("")

    L.append("## 7. Decision log")
    L.append("")
    L.append("| # | Decision | Rationale | Owner | Date |")
    L.append("| --- | --- | --- | --- | --- |")
    L.append("| 1 | _record the first decision here_ | | | |")
    L.append("")
    L.append("## 8. Approval gates")
    L.append("")
    acts = plan.get("approvals", {}).get("actions") or []
    if acts:
        L.append("| Action | Token | Status |")
        L.append("| --- | --- | --- |")
        for a in acts:
            L.append(f"| {a['action']} | `{a['token']}` | {'approved' if a['approved'] else 'pending'} |")
    else:
        L.append("No gated actions requested for this case.")
    L.append("")
    L.append("Publishing, signing, payment, credential use and irreversible deletes always need "
             "an explicit approval token and a named human approver.")
    L.append("")
    L.append("## 9. Handoff")
    L.append("")
    L.append(f"- Session files, stems and intermediate renders: `asset-handoff-manifest.csv` "
             f"row set for case `{plan['case_id']}`.")
    L.append("- Recipient must open the delivery in their own workflow and confirm "
             "levels, metadata and naming before release.")
    L.append("")
    L.append("## 10. Limits of this document")
    L.append("")
    L.extend([f"- {x}" for x in plan["limitations"]])
    L.append("")
    return "\n".join(L)


CHECKLIST_COLUMNS = ["block", "item", "owner", "status", "evidence", "notes"]


def render_checklist_rows(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for d in plan["deliverables"]:
        rows.append({"block": "deliverable", "item": d, "owner": "", "status": "open",
                     "evidence": "", "notes": ""})
    for c in plan["checks"]:
        rows.append({"block": "validation", "item": c, "owner": "", "status": "open",
                     "evidence": "", "notes": ""})
    for f in plan["intake"].get("blocking_flags", []):
        rows.append({"block": "blocking", "item": f, "owner": "", "status": "open",
                     "evidence": "", "notes": "must clear before publishing"})
    rows.append({"block": "review", "item": "human review signed off before handoff",
                 "owner": "", "status": "open", "evidence": "", "notes": ""})
    return rows


def render_checklist_csv(plan: Dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CHECKLIST_COLUMNS, lineterminator="\n")
    w.writeheader()
    for row in render_checklist_rows(plan):
        w.writerow(row)
    return buf.getvalue()


def render_file_manifest(plan: Dict[str, Any], *, files: Sequence[Tuple[str, str]]) -> str:
    """Asset manifest: delivery package -> case -> file -> role -> status."""
    buf = io.StringIO()
    cols = ["case_id", "kind", "file", "role", "status"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for name, role in files:
        w.writerow({"case_id": plan["case_id"], "kind": plan["kind"], "file": name,
                    "role": role, "status": "generated"})
    return buf.getvalue()


# --------------------------------------------------------------------------
# JSON output helpers
# --------------------------------------------------------------------------

def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False, default=str)


def load_json_file(path: str, *, max_bytes: int = DEFAULT_LIMITS["max_input_bytes"]) -> Any:
    if not os.path.exists(path):
        raise InputError(f"file not found: {path}", code="file_not_found", details={"path": path})
    if os.path.isdir(path):
        raise InputError(f"expected a file, got a directory: {path}", code="is_directory")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise LimitExceeded(f"{path} is {size} bytes, above limit {max_bytes}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {path}: {exc.msg} at line {exc.lineno}",
                         code="invalid_json", details={"path": path, "line": exc.lineno})


def read_text_file(path: str, *, max_bytes: int = DEFAULT_LIMITS["max_input_bytes"]) -> str:
    if not os.path.exists(path):
        raise InputError(f"file not found: {path}", code="file_not_found", details={"path": path})
    if os.path.isdir(path):
        raise InputError(f"expected a file, got a directory: {path}", code="is_directory")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise LimitExceeded(f"{path} is {size} bytes, above limit {max_bytes}")
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if data.startswith(b"%PDF"):
        raise NotSupported(
            "PDF input is not parsed (no PDF library in the stdlib). Convert first, e.g. "
            "`python3 -c \"import pypdf,sys;print(chr(10).join((p.extract_text() or '') "
            "for p in pypdf.PdfReader(sys.argv[1]).pages))\" in.pdf > catalog.txt`, then pass "
            "the .txt file.", code="pdf_requires_conversion", details={"path": path})
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise InputError(f"could not decode {path} as text", code="decode_error")


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def write_text(path: str, content: str) -> str:
    ensure_parent_dir(path)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# Optional WAV quality control (dependency-free)
# --------------------------------------------------------------------------

_WAV_SUPPORTED = (1, 3)  # PCM, IEEE float


def wav_inspect(path: str, *, limits: Optional[Dict[str, Any]] = None,
                qc: Optional[Dict[str, Any]] = None,
                budget_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Inspect a RIFF/WAVE file: header validity + basic level/DC/clip measurements.

    Pure standard library. Reports what it can and clearly marks anything it
    could not measure (compressed WAV codecs, unreadable chunks).
    """
    limits = limits or DEFAULT_LIMITS
    qc = qc or DEFAULT_WORKFLOW["qc"]
    budget = budget_seconds if budget_seconds is not None else limits["max_audio_scan_seconds"]
    started = time.monotonic()

    if not os.path.exists(path):
        raise InputError(f"audio file not found: {path}", code="file_not_found", details={"path": path})
    size = os.path.getsize(path)
    if size > limits["max_audio_bytes"]:
        raise LimitExceeded(f"audio file is {size} bytes, above max_audio_bytes={limits['max_audio_bytes']}")

    result: Dict[str, Any] = {
        "file": os.path.basename(path),
        "path": path,
        "size_bytes": size,
        "format": None,
        "integrity": {"ok": False, "problems": []},
        "measurements": {},
        "verdict": {},
        "limitations": [],
    }

    def problem(msg: str) -> None:
        result["integrity"]["problems"].append(msg)

    fmt = None
    data_offset = None
    data_len = None
    truncated = False
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise NotSupported("only uncompressed RIFF/WAVE files are inspected by qc-audio; "
                               "convert with an external tool first",
                               code="unsupported_audio_format", details={"file": os.path.basename(path)})
        declared_riff = struct.unpack("<I", header[4:8])[0] + 8
        pos = 12
        while pos + 8 <= size:
            fh.seek(pos)
            cid = fh.read(4)
            clen_raw = fh.read(4)
            if len(clen_raw) < 4:
                truncated = True
                break
            clen = struct.unpack("<I", clen_raw)[0]
            body_pos = pos + 8
            if body_pos + clen > size:
                truncated = True
            if cid == b"fmt ":
                body = fh.read(min(clen, 64))
                if len(body) >= 16:
                    audio_format, channels, rate, byte_rate, block_align, bits = \
                        struct.unpack("<HHIIHH", body[:16])
                    fmt = {"audio_format": audio_format, "channels": channels,
                           "sample_rate": rate, "byte_rate": byte_rate,
                           "block_align": block_align, "bits_per_sample": bits,
                           "codec": {1: "pcm", 3: "float"}.get(audio_format, f"unknown-{audio_format}")}
            elif cid == b"data" and data_offset is None:
                data_offset = body_pos
                data_len = min(clen, max(0, size - body_pos))
            pos = body_pos + clen + (clen % 2)

    if declared_riff != size:
        problem(f"RIFF size field ({declared_riff}) does not match file size ({size})")
    if truncated:
        problem("file is shorter than its chunk headers declare (truncated download?)")
    if fmt is None:
        problem("no 'fmt ' chunk found")
    if data_offset is None:
        problem("no 'data' chunk found")

    result["format"] = fmt
    if fmt:
        result["limitations"].append(
            "Measurements use uniform sampling of frames; extremely short transients may be missed.")

    measured = False
    if fmt and data_offset is not None and data_len and fmt["codec"] in ("pcm", "float"):
        channels = fmt["channels"]
        bits = fmt["bits_per_sample"]
        rate = fmt["sample_rate"]
        block = fmt["block_align"] or max(1, channels * max(1, bits // 8))
        if rate and block and channels:
            total_frames = data_len // block
            duration = total_frames / float(rate)
            target_frames = 200_000
            stride = max(1, total_frames // target_frames)
            peak = 0.0
            ssum = 0.0
            n = 0
            clipped = 0
            first_sample = None
            last_sample = None
            window_bytes = block * stride
            with open(path, "rb") as fh:
                fh.seek(data_offset)
                frames_done = 0
                while frames_done < total_frames:
                    if time.monotonic() - started > budget:
                        result["limitations"].append(
                            f"scan stopped early at {frames_done}/{total_frames} frames "
                            f"(budget {budget}s); levels are approximate")
                        break
                    chunk_frames = min(stride, total_frames - frames_done)
                    raw = fh.read(window_bytes if chunk_frames == stride else chunk_frames * block)
                    if not raw:
                        break
                    frames_done += chunk_frames
                    step = 8 if bits == 64 else (4 if bits == 32 else (2 if bits == 16 else 1))
                    if bits == 8:
                        samples = [(b - 128) / 128.0 for b in raw]
                    elif bits == 16:
                        count = len(raw) // 2
                        samples = [v / 32768.0 for v in struct.unpack(f"<{count}h", raw[:count * 2])]
                    elif bits in (24, 32, 64):
                        samples = _decode_wide(raw, bits, fmt["codec"])
                    else:
                        samples = _decode_wide(raw, 32, fmt["codec"])
                    if not samples:
                        continue
                    if first_sample is None:
                        first_sample = samples[0]
                    last_sample = samples[-1]
                    for v in samples:
                        av = -v if v < 0 else v
                        if av > peak:
                            peak = av
                        ssum += v * v
                        n += 1
                        if av >= 0.999:
                            clipped += 1
            if n:
                measured = True
                rms = math.sqrt(ssum / n)
                dbfs = lambda x: (20 * math.log10(x) if x > 0 else float("-inf"))
                peak_db = dbfs(peak)
                rms_db = dbfs(rms)
                peak_dbfs = round(peak_db, 3) if peak_db != float("-inf") else None
                rms_dbfs = round(rms_db, 3) if rms_db != float("-inf") else None
                result["measurements"] = {
                    "duration_seconds": round(duration, 3),
                    "sample_rate": rate,
                    "channels": channels,
                    "bits_per_sample": bits,
                    "codec": fmt["codec"],
                    "frames": total_frames,
                    "sampled_values": n,
                    "peak_dbfs": peak_dbfs,
                    "rms_dbfs": rms_dbfs,
                    "crest_factor_db": (round(peak_db - rms_db, 2)
                                        if peak_dbfs is not None and rms_dbfs is not None else None),
                    "clipped_sample_ratio": round(clipped / n, 6),
                    "dc_offset": (round(sum([first_sample, last_sample]) / 2.0, 6)
                                  if first_sample is not None else None),
                    "sample_rate_plausible": 8000 <= rate <= 768000,
                }
                result["limitations"].append(
                    "Loudness is RMS and does not equal LUFS/BS.1770; use a dedicated "
                    "loudness tool for delivery compliance.")
                result["limitations"].append(
                    "True peak and inter-sample overs are not measured (sample peak only).")
                result["limitations"].append(
                    "Sample rate must be validated as a fixed-problem check, not a measurement.")

    verdict = {"passed": True, "issues": []}
    for p in result["integrity"]["problems"]:
        verdict["passed"] = False
        verdict["issues"].append(p)
    if not fmt:
        verdict["passed"] = False
        verdict["issues"].append("incomplete WAV header")
    if result["format"] and result["format"]["codec"].startswith("unknown"):
        verdict["passed"] = False
        verdict["issues"].append(f"unsupported WAV codec: {result['format']['audio_format']}")
    if measured:
        m = result["measurements"]
        if m["peak_dbfs"] is not None and m["peak_dbfs"] > qc["clip_ceiling_dbfs"]:
            verdict["issues"].append(f"peak {m['peak_dbfs']} dBFS above ceiling {qc['clip_ceiling_dbfs']}")
            verdict["passed"] = False
        if m["duration_seconds"] < qc["minimum_duration_seconds"]:
            verdict["issues"].append("shorter than minimum_duration_seconds")
            verdict["passed"] = False
        if m["duration_seconds"] > qc["maximum_duration_seconds"]:
            verdict["issues"].append("longer than maximum_duration_seconds")
            verdict["passed"] = False
        if qc["require_non_silent"] and (m["rms_dbfs"] is None or m["rms_dbfs"] < -90):
            verdict["issues"].append("appears silent (RMS below -90 dBFS)")
            verdict["passed"] = False
        if m["clipped_sample_ratio"] > 0.001:
            verdict["issues"].append(f"clipped sample ratio {m['clipped_sample_ratio']} > 0.1%")
            verdict["passed"] = False
    elif fmt:
        result["limitations"].append("No sample measurements: codec or header prevented decoding.")
        verdict["issues"].append("not measured")
        verdict["passed"] = False

    result["integrity"]["ok"] = not result["integrity"]["problems"]
    result["verdict"] = verdict
    return result


def _decode_wide(raw: bytes, bits: int, codec: str) -> List[float]:
    out: List[float] = []
    if codec == "float" and bits == 32:
        count = len(raw) // 4
        return list(struct.unpack(f"<{count}f", raw[:count * 4]))
    if codec == "float" and bits == 64:
        count = len(raw) // 8
        return [float(v) for v in struct.unpack(f"<{count}d", raw[:count * 8])]
    step = 3 if bits == 24 else 4 if bits == 32 else 2
    if bits == 24:
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 1 << 24
            out.append(v / 8388608.0)
        return out
    if bits == 32:
        count = len(raw) // 4
        return [v / 2147483648.0 for v in struct.unpack(f"<{count}i", raw[:count * 4])]
    count = len(raw) // 2
    return [v / 32768.0 for v in struct.unpack(f"<{count}h", raw[:count * 2])]


def wav_silence(path: str, *, out_path: str, threshold_dbfs: float = -60.0,
                limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write a 16-bit mono copy with silence trimmed from head and tail."""
    limits = limits or DEFAULT_LIMITS
    info = wav_inspect(path, limits=limits, qc={"clip_ceiling_dbfs": 0.0,
                                                "minimum_duration_seconds": 0.0,
                                                "maximum_duration_seconds": 86400.0,
                                                "require_non_silent": False})
    fmt = info["format"]
    if not fmt or fmt["bits_per_sample"] != 16:
        raise NotSupported("trim-silence currently supports 16-bit PCM WAV input only",
                           code="unsupported_audio_format")
    thr = 10 ** (threshold_dbfs / 20.0) * 32768.0
    with open(path, "rb") as fh:
        data = fh.read(limits["max_audio_bytes"] + 1)
    if len(data) > limits["max_audio_bytes"]:
        raise LimitExceeded("audio file above max_audio_bytes")
    # locate data chunk again (cheap)
    off = data.find(b"data")
    if off < 4:
        raise InputError("no data chunk", code="invalid_wav")
    start = off + 8
    block = fmt["block_align"] or 2
    frames = (len(data) - start) // block
    first_keep, last_keep = None, None
    for i in range(frames):
        base = start + i * block
        v = struct.unpack("<h", data[base:base + 2])[0]
        if abs(v) >= thr:
            if first_keep is None:
                first_keep = i
            last_keep = i
    if first_keep is None:
        keep = b""
        kept_frames = 0
    else:
        keep = data[start + first_keep * block: start + (last_keep + 1) * block]
        kept_frames = last_keep - first_keep + 1

    channels, rate = fmt["channels"], fmt["sample_rate"]
    hdr = _wav_header(keep, channels, rate, 16)
    ensure_parent_dir(out_path)
    with open(out_path, "wb") as fh:
        fh.write(hdr + keep)
    return {
        "file": os.path.basename(path),
        "output": out_path,
        "trimmed_frames_head": first_keep or 0,
        "trimmed_frames_tail": frames - (last_keep + 1) if first_keep is not None else frames,
        "kept_frames": kept_frames,
        "kept_seconds": round(kept_frames / rate, 3) if rate else None,
        "threshold_dbfs": threshold_dbfs,
        "note": "output re-written as 16-bit PCM with a fresh 44-byte header",
    }


def _wav_header(payload: bytes, channels: int, rate: int, bits: int) -> bytes:
    block = max(1, channels * bits // 8)
    return (b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                    rate * block, block, bits)
            + b"data" + struct.pack("<I", len(payload)))


def make_test_wav(path: str, *, seconds: float = 1.0, freq: float = 440.0,
                  rate: int = 48000, channels: int = 2, bits: int = 16,
                  leading_s: float = 0.0, amplitude: float = 0.5) -> str:
    """Deterministic test-signal generator (used by tests and qc demos)."""
    import array
    total = int(seconds * rate)
    lead = int(leading_s * rate)
    samples = array.array("h")
    maxv = int(amplitude * 32767)
    for i in range(total):
        v = int(maxv * math.sin(2 * math.pi * freq * i / rate)) if i >= lead else 0
        for _ in range(channels):
            samples.append(max(-32768, min(32767, v)))
    payload = samples.tobytes()
    ensure_parent_dir(path)
    with open(path, "wb") as fh:
        fh.write(_wav_header(payload, channels, rate, bits) + payload)
    return path


# --------------------------------------------------------------------------
# Archive helpers
# --------------------------------------------------------------------------

_ARCHIVE_TOP = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*/$")
_FORBIDDEN_NAMES = {"skill.md", "core.md", "manifest.json", "index.json"}


def validate_archive_entry(name: str, *, package_layout: bool = True) -> str:
    """Reject traversal, absolute paths and nested archives.

    With package_layout=True (the default) it additionally enforces the Skill Tool
    packaging rules: one top-level folder, no subfolders, prefixed file names and
    no SKILL.md/MANIFEST.json. Deliveries produced from user cases are written with
    package_layout=False so normal project file names (e.g. "master.wav") are legal.
    """
    if name.startswith("/") or re.match(r"^[a-zA-Z]:", name) or ".." in name.split("/"):
        raise ValidationError(f"unsafe archive path: {name}", code="unsafe_path")
    if name.endswith("/"):
        if package_layout and not _ARCHIVE_TOP.match(name):
            raise ValidationError(f"unexpected folder in package: {name}", code="bad_structure")
        return name
    if re.search(r"\.(zip|tar|gz|7z|rar|bz2|xz)$", name, re.I):
        raise ValidationError(f"nested archives are not allowed: {name}", code="nested_archive")
    if not package_layout:
        return name
    depth = name.count("/")
    if depth < 1:
        raise ValidationError(f"file must live inside the package folder: {name}",
                              code="bad_structure")
    if depth > 1:
        raise ValidationError(f"nested subfolders are not allowed: {name}", code="bad_structure")
    base = name.rsplit("/", 1)[-1]
    if base.lower() in _FORBIDDEN_NAMES:
        raise ValidationError(f"{base} is not allowed inside a Skill Tool folder",
                              code="forbidden_file")
    if not re.match(r"^(?:tools|knowledge|asset)-[a-z0-9._-]+$", base, re.I):
        raise ValidationError(f"file name must start with tools-, knowledge- or asset-: {base}",
                              code="bad_prefix")
    if base.lower().endswith((".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz")):
        raise ValidationError(f"nested archives are not allowed: {base}", code="nested_archive")
    return name


def write_archive(path: str, files: Sequence[Tuple[str, bytes]],
                  *, compress: bool = True, package_layout: bool = True) -> Dict[str, Any]:
    """Deterministic ZIP: fixed timestamps, sorted entries, validated paths."""
    ensure_parent_dir(path)
    entries = []
    for name, payload in files:
        validate_archive_entry(name, package_layout=package_layout)
        entries.append((name, payload))
    seen = set()
    for name, _ in entries:
        if name in seen:
            raise ValidationError(f"duplicate archive entry: {name}", code="duplicate_entry")
        seen.add(name)
    entries.sort(key=lambda e: e[0])
    tmp = f"{path}.tmp-{os.getpid()}"
    with zipfile.ZipFile(tmp, "w",
                         compression=zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED,
                         allowZip64=True) as zf:
        for name, payload in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            zf.writestr(info, payload)
    os.replace(tmp, path)
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise StudioError(f"archive failed integrity check at {bad}", code="archive_corrupt")
    return {"archive": path, "entries": len(entries), "bytes": os.path.getsize(path),
            "names": [n for n, _ in entries]}


def read_archive(path: str, *, max_entries: int = 500,
                 max_bytes: int = 64 * 1024 * 1024) -> List[Tuple[str, bytes]]:
    if not os.path.exists(path):
        raise InputError(f"archive not found: {path}", code="file_not_found")
    out: List[Tuple[str, bytes]] = []
    total = 0
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if len(names) > max_entries:
            raise LimitExceeded(f"archive has {len(names)} entries, limit {max_entries}")
        for info in zf.infolist():
            if info.is_dir():
                continue
            if ".." in info.filename.split("/") or info.filename.startswith("/"):
                raise ValidationError(f"unsafe path in archive: {info.filename}", code="unsafe_path")
            if info.file_size > max_bytes:
                raise LimitExceeded(f"entry too large: {info.filename}")
            total += info.file_size
            if total > max_bytes:
                raise LimitExceeded("archive expands beyond the configured byte budget")
            out.append((info.filename, zf.read(info)))
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
