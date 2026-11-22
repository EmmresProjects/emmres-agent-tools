#!/usr/bin/env python3
"""Input catalog parser and 330-skill registry for data-web-app-interaction.

Handles plain text, Markdown, and copied HTML skill specifications (ignoring
HTML tags, CSS classes, data-* attributes, numbering marks, and extra spacing)
to extract clean Category Names, Skill Tool Names, Descriptions, and unified
umbrella identifiers, and indexes asset-skill-catalog.json for fast lookup.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import html.parser
import json
import re
from typing import Any, Dict, List, Optional

from pathlib import Path


def _load_core() -> Any:
    mod_key = "dwai_tools_core"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    import importlib.util

    candidates = [
        Path("data-web-app-interaction/tools-core.py"),
        Path(__file__).parent / "tools-core.py",
    ]
    for cand in candidates:
        if cand.exists():
            spec = importlib.util.spec_from_file_location(mod_key, str(cand))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_key] = mod
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("Unable to locate data-web-app-interaction/tools-core.py")


core = _load_core()


def slugify_identifier(text: str, fallback: str = "unified-skill-tool") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned if cleaned else fallback


class _HTMLStripper(html.parser.HTMLParser):
    """Strip HTML tags, CSS classes, style/script blocks, and data-* attributes cleanly."""

    BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "header"}
    SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[ Any ]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and t in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def clean_raw_specification(raw_text: str) -> str:
    """Normalize plain text, Markdown, or copied HTML into clean line-oriented text."""
    text = raw_text or ""
    if re.search(r"<[a-zA-Z][^>]*>", text):
        stripper = _HTMLStripper()
        stripper.feed(text)
        stripper.close()
        text = "".join(stripper.parts)
    # Strip Markdown formatting decorations while preserving headings/labels
    lines: List[str] = []
    for raw_line in text.splitlines():
        ln = raw_line.strip()
        ln = re.sub(r"^#{1,6}\s*", "", ln)
        ln = re.sub(r"\*\*([^*]+)\*\*", r"\1", ln)
        ln = re.sub(r"__([^_]+)__", r"\1", ln)
        ln = re.sub(r"`([^`]+)`", r"\1", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def parse_skill_specification(raw_text: str) -> Dict[str, Any]:
    """Extract categories, Skill Tool Names, Descriptions, and umbrella name from text/Markdown/HTML."""
    if not raw_text or not raw_text.strip():
        raise core.InputValidationError("Skill specification text cannot be empty.")

    normalized = clean_raw_specification(raw_text)
    lines = normalized.splitlines()

    categories: List[Dict[str, Any]] = []
    current_cat: Dict[str, Any] = {"category_name": "", "skills": []}
    pending_skill: Optional[Dict[str, str]] = None

    cat_re = re.compile(r"^(?:category\s*\d*\s*[:.-]\s*)(.+)$", re.I)
    skill_explicit_re = re.compile(r"^(?:[\d]+[.)]\s*|[-*•]\s*)?(?:skill(?:\s+tool)?\s+name\s*[:.-]\s*)(.+)$", re.I)
    desc_explicit_re = re.compile(r"^(?:description\s*[:.-]\s*)(.+)$", re.I)
    numbered_pair_re = re.compile(r"^(?:[\d]+[.)]\s+|[-*•]\s+)([A-Z][A-Za-z0-9 ,/&()-]{2,80})\s*[:—–-]\s*(.+)$")

    def _flush_skill() -> None:
        nonlocal pending_skill
        if pending_skill and pending_skill.get("name"):
            pending_skill["description"] = re.sub(r"\s+", " ", pending_skill.get("description", "")).strip()
            pending_skill["slug"] = slugify_identifier(pending_skill["name"], "capability")
            current_cat["skills"].append(pending_skill)
        pending_skill = None

    for ln in lines:
        m_cat = cat_re.match(ln)
        if m_cat:
            _flush_skill()
            if current_cat["skills"] or current_cat["category_name"]:
                categories.append(current_cat)
            cname = m_cat.group(1).strip()
            current_cat = {"category_name": cname, "category_slug": slugify_identifier(cname), "skills": []}
            continue

        m_sk = skill_explicit_re.match(ln)
        if m_sk:
            _flush_skill()
            sname = re.sub(r"^(?:[\d]+[.)]\s*|[-*•]\s*)", "", m_sk.group(1)).strip()
            pending_skill = {"name": sname, "description": ""}
            continue

        m_desc = desc_explicit_re.match(ln)
        if m_desc and pending_skill is not None:
            pending_skill["description"] = m_desc.group(1).strip()
            continue

        m_pair = numbered_pair_re.match(ln)
        if m_pair:
            _flush_skill()
            pending_skill = {"name": m_pair.group(1).strip(), "description": m_pair.group(2).strip()}
            continue

        if pending_skill is not None:
            sep = " " if pending_skill["description"] else ""
            pending_skill["description"] += sep + ln

    _flush_skill()
    if current_cat["skills"] or current_cat["category_name"]:
        categories.append(current_cat)

    # Single capability fallback if unstructured prose was given
    if not any(c["skills"] for c in categories) and lines:
        title = re.sub(r"^(?:[\d]+[.)]\s*|[-*•]\s*)", "", lines[0])[:80].strip(" :.-")
        desc = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
        categories = [{
            "category_name": "",
            "category_slug": "",
            "skills": [{
                "name": title,
                "slug": slugify_identifier(title),
                "description": desc,
            }],
        }]

    named_cats = [c["category_name"] for c in categories if c.get("category_name")]
    all_skills = [s for c in categories for s in c.get("skills", [])]
    if len(named_cats) == 1:
        umbrella_slug = slugify_identifier(named_cats[0])
    elif len(named_cats) > 1:
        umbrella_slug = "data-web-app-interaction" if len(named_cats) >= 10 else slugify_identifier(named_cats[0] + "-suite")
    elif len(all_skills) == 1:
        umbrella_slug = all_skills[0]["slug"]
    else:
        umbrella_slug = slugify_identifier(all_skills[0]["name"] + "-toolkit") if all_skills else "unified-skill-tool"

    return {
        "umbrella_skill_tool_name": umbrella_slug,
        "category_count": len(categories),
        "capability_count": len(all_skills),
        "categories": categories,
    }


class SkillRegistry:
    """Fast lookup and search index over asset-skill-catalog.json."""

    def __init__(self, catalog_rel_path: str = core.DEFAULT_CATALOG_REL) -> None:
        cat_path = core.resolve_package_resource(catalog_rel_path)
        if not cat_path.exists():
            raise core.InputValidationError(f"Skill catalog asset not found: {catalog_rel_path}")
        self.raw = json.loads(cat_path.read_text(encoding="utf-8"))
        self.categories: List[Dict[str, Any]] = self.raw.get("categories", [])
        self.skills: List[Dict[str, Any]] = self.raw.get("skills", [])
        self.by_id: Dict[str, Dict[str, Any]] = {s["skill_id"].lower(): s for s in self.skills}
        self.by_slug: Dict[str, Dict[str, Any]] = {s["slug"].lower(): s for s in self.skills}
        self.by_name: Dict[str, Dict[str, Any]] = {s["name"].lower(): s for s in self.skills}

    def resolve(self, identifier: str) -> Dict[str, Any]:
        if not identifier or not identifier.strip():
            raise core.InputValidationError("Skill identifier cannot be empty.")
        key = identifier.strip().lower()
        if key in self.by_id:
            return self.by_id[key]
        if key in self.by_slug:
            return self.by_slug[key]
        if key in self.by_name:
            return self.by_name[key]
        slug_try = slugify_identifier(identifier, "")
        if slug_try in self.by_slug:
            return self.by_slug[slug_try]
        # Suggest closest matches
        suggestions = [
            {"skill_id": s["skill_id"], "slug": s["slug"], "name": s["name"]}
            for s in self.skills
            if key in s["slug"] or key in s["name"].lower()
        ][:5]
        raise core.InputValidationError(
            f"Unknown capability '{identifier}'. Run 'capabilities' to list valid IDs or slugs.",
            {"requested": identifier, "suggestions": suggestions},
        )

    def filter_skills(self, category: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
        results = self.skills
        if category:
            ck = category.strip().lower()
            results = [
                s for s in results
                if ck in (str(s["category_number"]), s["category_id"].lower(), s["category_slug"].lower(), s["category_name"].lower())
                or ck in s["category_name"].lower()
            ]
        if search:
            q = search.strip().lower()
            results = [
                s for s in results
                if q in s["skill_id"].lower()
                or q in s["slug"]
                or q in s["name"].lower()
                or q in s["description"].lower()
                or q in s["category_name"].lower()
            ]
        return results
