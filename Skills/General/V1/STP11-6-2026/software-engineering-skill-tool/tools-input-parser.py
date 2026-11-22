#!/usr/bin/env python3
"""Input parser for plain text, Markdown, and copied HTML capability specs plus catalog lookup."""
import sys
sys.dont_write_bytecode = True

import html
import re
from typing import Any, Dict, List, Optional, Tuple


def slugify_identifier(text: str) -> str:
    """Convert any title into a valid lowercase hyphenated identifier matching ^[a-z0-9]+(?:-[a-z0-9]+)*$."""
    normalized = (
        text.replace("C++", "cpp")
        .replace("c++", "cpp")
        .replace("C#", "csharp")
        .replace("c#", "csharp")
        .replace("F#", "fsharp")
        .replace("f#", "fsharp")
        .replace(".NET", "dotnet")
        .replace(".net", "dotnet")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "capability"


def strip_html_and_noise(raw_text: str) -> str:
    """Remove HTML scripts/styles, tags, CSS classes, data-* attributes, entities, and extra whitespace."""
    if not raw_text:
        return ""
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", " ", raw_text)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    # Insert line breaks before block-level HTML elements so structure survives tag stripping
    cleaned = re.sub(r"(?i)</?(?:div|p|li|tr|h[1-6]|section|article|br|hr|ul|ol|table|blockquote)\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</?(?:td|th)\b[^>]*>", " | ", cleaned)
    # Strip all remaining HTML tags (including class="", data-*="", style="", id="")
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    out_lines: List[str] = []
    for line in cleaned.splitlines():
        compact = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if compact:
            out_lines.append(compact)
    return "\n".join(out_lines)


def clean_numbering_prefix(line: str) -> str:
    """Strip Markdown bullets, numbering marks (1., 1), (1), [01]), and bold/italic markers."""
    s = line.strip()
    s = re.sub(r"^[#>*\-+]+\s*", "", s)
    s = re.sub(r"^(?:\[\d+\]|\(\d+\)|\d+[.)])\s*", "", s)
    s = re.sub(r"\*\*|__|~~|`", "", s)
    return s.strip()


def parse_capability_specification(raw_input: str, fallback_category: str = "custom-capabilities") -> Dict[str, Any]:
    """
    Parse a single capability, a numbered Skill Tool list, one category, or several categories
    from plain text, Markdown, or copied HTML.
    """
    detected_format = "plain-text"
    if re.search(r"<[a-zA-Z][^>]*>", raw_input):
        detected_format = "html"
    elif re.search(r"(?m)^(?:#{1,6}\s+|\|.*\|.*\||[-*]\s+\*\*)", raw_input):
        detected_format = "markdown"

    normalized = strip_html_and_noise(raw_input)
    if not normalized.strip():
        return {
            "detected_format": detected_format,
            "umbrella_name": slugify_identifier(fallback_category),
            "category_count": 0,
            "skill_count": 0,
            "categories": [],
        }

    lines = normalized.splitlines()
    categories: List[Dict[str, Any]] = []
    current_cat: Optional[Dict[str, Any]] = None
    pending_skill: Optional[Dict[str, str]] = None

    def ensure_category(cat_title: str) -> Dict[str, Any]:
        nonlocal current_cat
        clean_title = re.sub(r"(?i)^category\s*\d*\s*[:\-–]\s*", "", cat_title).strip(" :-–|")
        if not clean_title:
            clean_title = fallback_category.replace("-", " ").title()
        cat_slug = slugify_identifier(clean_title)
        for existing in categories:
            if existing["slug"] == cat_slug:
                current_cat = existing
                return existing
        new_cat = {
            "category_id": len(categories) + 1,
            "slug": cat_slug,
            "name": clean_title,
            "skills": [],
        }
        categories.append(new_cat)
        current_cat = new_cat
        return new_cat

    def flush_pending_skill() -> None:
        nonlocal pending_skill
        if pending_skill and pending_skill.get("name"):
            cat = current_cat or ensure_category(fallback_category)
            sname = pending_skill["name"].strip(" :-–|")
            sdesc = pending_skill.get("description", "").strip() or f"Execute {sname} capability."
            cat["skills"].append({
                "number": len(cat["skills"]) + 1,
                "slug": slugify_identifier(sname),
                "name": sname,
                "description": sdesc,
            })
        pending_skill = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Skip Markdown table separator rows like |---|---|
        if re.match(r"^\|?[\s:\-]+\|[\s:\-|]+$", stripped):
            continue

        # Category header patterns: "Category 14: API Engineering" or "# Category: API Engineering" or "## API Engineering"
        cat_match = re.match(r"^(?:#{1,4}\s*)?Category\s*(?:\d+)?\s*[:\-–]\s*(.+)$", stripped, re.I)
        if cat_match:
            flush_pending_skill()
            ensure_category(cat_match.group(1))
            continue

        md_heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if md_heading and not re.search(r"(?i)\bskill(?:\s+tool)?\s+name\b", stripped):
            flush_pending_skill()
            ensure_category(md_heading.group(2))
            continue

        # Explicit "Skill Name: X | Description: Y" or "1. Skill Name: X"
        explicit_skill = re.match(
            r"^(?:[\d.)(\[\]\-*+]+\s*)?(?:\*\*)?Skill(?:\s+Tool)?\s+Name(?:\*\*)?\s*[:\-–]\s*(.+)$",
            stripped,
            re.I,
        )
        if explicit_skill:
            flush_pending_skill()
            remainder = re.sub(r"\*\*|__", "", explicit_skill.group(1)).strip()
            inline_desc = re.split(r"\bDescription\s*[:\-–]\s*", remainder, maxsplit=1, flags=re.I)
            if len(inline_desc) == 2:
                pending_skill = {"name": inline_desc[0].strip(" |;-"), "description": inline_desc[1].strip()}
                flush_pending_skill()
            else:
                pending_skill = {"name": remainder, "description": ""}
            continue

        # Explicit "Description: ..." continuation
        explicit_desc = re.match(r"^(?:\*\*)?Description(?:\*\*)?\s*[:\-–]\s*(.+)$", stripped, re.I)
        if explicit_desc and pending_skill is not None:
            desc_val = explicit_desc.group(1).strip()
            pending_skill["description"] = (
                (pending_skill["description"] + " " + desc_val).strip()
                if pending_skill["description"]
                else desc_val
            )
            continue

        # Markdown/HTML pipe-separated table row: "1 | REST API Design | Design HTTP APIs"
        if "|" in stripped:
            cells = [clean_numbering_prefix(c) for c in stripped.strip("|").split("|") if c.strip()]
            if len(cells) >= 2:
                low0 = cells[0].lower()
                if low0 in ("#", "id", "no", "number", "skill", "skill name", "skill tool name", "capability"):
                    if len(cells) >= 2 and cells[1].lower() in ("skill", "skill name", "description", "capability"):
                        continue
                if cells[0].isdigit() and len(cells) >= 3:
                    sname, sdesc = cells[1], " ".join(cells[2:])
                else:
                    sname, sdesc = cells[0], " ".join(cells[1:])
                flush_pending_skill()
                pending_skill = {"name": sname, "description": sdesc}
                flush_pending_skill()
                continue

        # Numbered or bulleted list item: "1. Static Code Analysis: Parse AST and detect defects" or "- Cache Builder - ..."
        list_match = re.match(r"^(?:\[\d+\]|\(\d+\)|\d+[.)]|[-*+])\s+(.+)$", stripped)
        if list_match:
            flush_pending_skill()
            item_body = re.sub(r"\*\*|__", "", list_match.group(1)).strip()
            parts = re.split(r"\s*(?::|—|–|\s-\s)\s*", item_body, maxsplit=1)
            if len(parts) == 2:
                pending_skill = {"name": parts[0].strip(), "description": parts[1].strip()}
            else:
                pending_skill = {"name": item_body, "description": ""}
            continue

        # Continuation line or single-capability plain text
        if pending_skill is not None:
            pending_skill["description"] = (pending_skill["description"] + " " + stripped).strip()
        else:
            parts = re.split(r"\s*(?::|—|–|\s-\s)\s*", clean_numbering_prefix(stripped), maxsplit=1)
            if len(parts) == 2 and len(parts[0].split()) <= 8:
                pending_skill = {"name": parts[0], "description": parts[1]}
            else:
                title_words = clean_numbering_prefix(stripped).split()[:6]
                pending_skill = {
                    "name": " ".join(title_words),
                    "description": stripped,
                }

    flush_pending_skill()

    # Ensure unique slugs across parsed items
    seen: Dict[str, int] = {}
    total_skills = 0
    for cat in categories:
        for sk in cat["skills"]:
            total_skills += 1
            base = sk["slug"]
            if base in seen:
                seen[base] += 1
                sk["slug"] = f"{cat['slug']}-{base}"
            else:
                seen[base] = 1

    if len(categories) == 1:
        umbrella = categories[0]["slug"]
    elif len(categories) > 1:
        umbrella = slugify_identifier(fallback_category if fallback_category != "custom-capabilities" else "unified-capability-suite")
    else:
        umbrella = slugify_identifier(fallback_category)

    return {
        "detected_format": detected_format,
        "umbrella_name": umbrella,
        "category_count": len(categories),
        "skill_count": total_skills,
        "categories": categories,
    }


class CatalogIndex:
    """Fast index over the 51-category, 1,020-skill Software Engineering catalog."""

    def __init__(self, catalog_doc: Dict[str, Any]):
        self.doc = catalog_doc
        self.categories: List[Dict[str, Any]] = catalog_doc.get("categories", [])
        self.by_skill_id: Dict[str, Dict[str, Any]] = {}
        self.by_coordinate: Dict[str, Dict[str, Any]] = {}
        self.by_slug: Dict[str, Dict[str, Any]] = {}
        self.by_name_lower: Dict[str, List[Dict[str, Any]]] = {}
        self.cat_by_key: Dict[str, Dict[str, Any]] = {}

        for cat in self.categories:
            cid_str = str(cat["category_id"])
            self.cat_by_key[cid_str] = cat
            self.cat_by_key[f"{cat['category_id']:02d}"] = cat
            self.cat_by_key[cat["category_code"].lower()] = cat
            self.cat_by_key[cat["slug"].lower()] = cat
            self.cat_by_key[cat["name"].lower()] = cat

            for sk in cat.get("skills", []):
                enriched = dict(sk)
                enriched["category_name"] = cat["name"]
                enriched["category_code"] = cat["category_code"]
                self.by_skill_id[sk["skill_id"].upper()] = enriched
                self.by_coordinate[sk["coordinate"]] = enriched
                # Also index non-zero-padded coordinate e.g. "1.1"
                short_coord = f"{cat['category_id']}.{sk['number']}"
                self.by_coordinate[short_coord] = enriched
                self.by_slug[sk["slug"].lower()] = enriched
                self.by_name_lower.setdefault(sk["name"].lower(), []).append(enriched)

    def resolve_category(self, identifier: str) -> Optional[Dict[str, Any]]:
        key = identifier.strip().lower()
        if key in self.cat_by_key:
            return self.cat_by_key[key]
        slug_key = slugify_identifier(identifier)
        return self.cat_by_key.get(slug_key)

    def resolve_skill(self, identifier: str, category_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        q = identifier.strip()
        cat_obj = self.resolve_category(category_filter) if category_filter else None
        allowed_cat_id = cat_obj["category_id"] if cat_obj else None

        def filter_cat(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if allowed_cat_id is None:
                return items
            return [i for i in items if i["category_id"] == allowed_cat_id]

        # Support "cat-29/distributed-tracing" scoped syntax
        if "/" in q:
            cat_part, sk_part = q.split("/", 1)
            scoped_cat = self.resolve_category(cat_part)
            if scoped_cat:
                return self.resolve_skill(sk_part, category_filter= str(scoped_cat["category_id"]))

        if q.upper() in self.by_skill_id:
            return filter_cat([self.by_skill_id[q.upper()]])
        if q in self.by_coordinate:
            return filter_cat([self.by_coordinate[q]])
        low = q.lower()
        if low in self.by_slug:
            return filter_cat([self.by_slug[low]])
        if low in self.by_name_lower:
            return filter_cat(self.by_name_lower[low])
        slug_q = slugify_identifier(q)
        if slug_q in self.by_slug:
            return filter_cat([self.by_slug[slug_q]])
        # Scoped match when slug was overridden for disambiguation
        partial = [
            sk for sk in self.by_slug.values()
            if slug_q == slugify_identifier(sk["name"])
        ]
        return filter_cat(partial)

    def search(self, query: str, category_filter: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
        tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 2]
        cat_obj = self.resolve_category(category_filter) if category_filter else None
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for sk in self.by_slug.values():
            if cat_obj and sk["category_id"] != cat_obj["category_id"]:
                continue
            name_low = sk["name"].lower()
            slug_low = sk["slug"].lower()
            cat_low = sk["category_name"].lower()
            desc_low = (sk["description"] + " " + sk.get("responsibility", "")).lower()
            score = 0.0
            for tok in tokens:
                if tok == slug_low or tok in slug_low.split("-"):
                    score += 6.0
                elif tok in name_low:
                    score += 4.0
                elif tok in cat_low:
                    score += 2.5
                elif tok in desc_low:
                    score += 1.0
            if score > 0:
                scored.append((score, sk))
        scored.sort(key=lambda item: (-item[0], item[1]["coordinate"]))
        return [
            dict(sk, match_score=round(sc, 2))
            for sc, sk in scored[: max(1, limit)]
        ]
