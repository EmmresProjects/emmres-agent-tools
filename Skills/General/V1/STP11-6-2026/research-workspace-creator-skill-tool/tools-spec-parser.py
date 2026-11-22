#!/usr/bin/env python3
"""Input capability specification parser (plain text, Markdown, HTML, JSON, PDF) and catalog registry."""
import sys

sys.dont_write_bytecode = True

import html
import html.parser
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def slugify_name(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned or "unified-skill-tool"


class _CleanHTMLTextExtractor(html.parser.HTMLParser):
    """Strips HTML tags, scripts, styles, CSS classes, and data-* attributes while preserving block breaks."""

    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "aside",
        "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "hr", "dd", "dt",
    }
    SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and t in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth == 0 and t in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_clean_text(self) -> str:
        joined = "".join(self._chunks)
        return html.unescape(joined)


def normalize_raw_spec_text(raw_content: str) -> str:
    """Strip HTML tags/attributes, Markdown formatting decorations, and excessive whitespace."""
    text = raw_content
    if re.search(r"<[a-zA-Z!/][^>]*>", text):
        parser = _CleanHTMLTextExtractor()
        parser.feed(text)
        parser.close()
        text = parser.get_clean_text()
    else:
        text = html.unescape(text)

    # Remove inline markdown bold/italic/code wrappers while keeping inner text
    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.replace("\u00a0", " ")
        line = re.sub(r"[*_`]{1,3}", "", line)
        line = re.sub(r"^#{1,6}\s+", "", line.strip())
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def strip_numbering_prefix(line: str) -> str:
    """Remove leading numbering marks like '1.', '2)', '[03]', '- ', '* '."""
    out = re.sub(r"^(?:[-*+]\s+)?(?:\[\d+\]|\(\d+\)|\d+[.)])\s*", "", line.strip())
    out = re.sub(r"^[-*+]\s+", "", out)
    return out.strip()


def parse_capability_spec(raw_content: str, source_hint: str = "inline") -> Dict[str, Any]:
    """
    Parse a single capability, numbered list, one category, or multiple categories
    from plain text, Markdown, copied HTML, or JSON into normalized categories and skills.
    """
    stripped = raw_content.strip()
    if not stripped:
        return {
            "source_hint": source_hint,
            "umbrella_name": "empty-spec",
            "category_count": 0,
            "capability_count": 0,
            "categories": [],
            "capabilities": [],
        }

    # Support JSON catalog or list input directly
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed_json = json.loads(stripped)
            if isinstance(parsed_json, dict) and "capabilities" in parsed_json:
                cats = parsed_json.get("categories", [])
                caps = parsed_json.get("capabilities", [])
                umbrella = slugify_name(
                    parsed_json.get("skill_tool_name")
                    or parsed_json.get("catalog_title")
                    or (cats[0]["name"] if len(cats) == 1 and "name" in cats[0] else "unified-skill-tool")
                )
                return {
                    "source_hint": source_hint,
                    "umbrella_name": umbrella,
                    "category_count": len(cats),
                    "capability_count": len(caps),
                    "categories": cats,
                    "capabilities": caps,
                }
        except json.JSONDecodeError:
            pass

    clean_text = normalize_raw_spec_text(raw_content)
    lines = clean_text.splitlines()

    categories_map: Dict[str, Dict[str, Any]] = {}
    capabilities: List[Dict[str, Any]] = []
    current_category = "General"
    has_explicit_category = False
    pending_skill: Optional[Dict[str, Any]] = None

    def flush_pending() -> None:
        nonlocal pending_skill
        if pending_skill is not None:
            cat_name = pending_skill["category_name"]
            if cat_name not in categories_map:
                categories_map[cat_name] = {
                    "category_id": len(categories_map) + 1,
                    "name": cat_name,
                    "slug": slugify_name(cat_name),
                    "skill_count": 0,
                }
            categories_map[cat_name]["skill_count"] += 1
            pending_skill["slug"] = slugify_name(pending_skill["name"])
            pending_skill["description"] = " ".join(pending_skill["description"].split()).strip()
            capabilities.append(pending_skill)
            pending_skill = None

    for line in lines:
        # Ignore repetitive catalog page headers if present
        if line in (
            "Comprehensive Research & Workspace Creator Skill Catalog",
            "Agent capability planning catalog",
        ):
            continue

        cat_match = re.match(r"^(?:Category\s*\d*\s*[:\-]\s*)(.+)$", line, flags=re.I)
        if cat_match:
            flush_pending()
            current_category = cat_match.group(1).strip()
            has_explicit_category = True
            if current_category not in categories_map:
                categories_map[current_category] = {
                    "category_id": len(categories_map) + 1,
                    "name": current_category,
                    "slug": slugify_name(current_category),
                    "skill_count": 0,
                }
            continue

        de_numbered = strip_numbering_prefix(line)

        skill_match = re.match(
            r"^(?:Skill(?:\s+Tool)?\s+Name|Capability|Tool\s+Name)\s*:\s*(.+)$",
            de_numbered,
            flags=re.I,
        )
        if skill_match:
            flush_pending()
            rest = skill_match.group(1).strip()
            inline_desc = ""
            # Handle inline "Description: ..." on the same line (e.g. flattened HTML)
            desc_split = re.split(r"\bDescription\s*:\s*", rest, maxsplit=1, flags=re.I)
            sname = desc_split[0].strip(" -:")
            if len(desc_split) > 1:
                inline_desc = desc_split[1].strip()
            pending_skill = {
                "index": len(capabilities) + 1,
                "category_name": current_category,
                "category_slug": slugify_name(current_category),
                "name": sname,
                "description": inline_desc,
            }
            continue

        desc_match = re.match(r"^Description\s*:\s*(.+)$", de_numbered, flags=re.I)
        if desc_match and pending_skill is not None:
            desc_body = desc_match.group(1).strip()
            pending_skill["description"] = (
                f"{pending_skill['description']} {desc_body}".strip()
                if pending_skill["description"]
                else desc_body
            )
            continue

        # Handle numbered list items formatted as "1. Name - Description" or "1. Name: Description"
        is_numbered_item = bool(re.match(r"^(?:[-*+]\s+)?(?:\[\d+\]|\(\d+\)|\d+[.)])\s+", line))
        if is_numbered_item and pending_skill is None:
            pair_match = re.match(r"^([^:\u2013\u2014\-]{3,90})\s*(?:[:\u2013\u2014]|\s-\s)\s*(.+)$", de_numbered)
            if pair_match:
                pending_skill = {
                    "index": len(capabilities) + 1,
                    "category_name": current_category,
                    "category_slug": slugify_name(current_category),
                    "name": pair_match.group(1).strip(),
                    "description": pair_match.group(2).strip(),
                }
                flush_pending()
                continue

        if pending_skill is not None:
            pending_skill["description"] = (
                f"{pending_skill['description']} {line}".strip()
                if pending_skill["description"]
                else line
            )

    flush_pending()

    # Fallback: single plain capability paragraph with no explicit numbering or labels
    if not capabilities and lines:
        title_line = strip_numbering_prefix(lines[0])[:80]
        body_text = " ".join(lines[1:]) if len(lines) > 1 else title_line
        single_cat = current_category if has_explicit_category else title_line
        cat_slug = slugify_name(single_cat)
        categories_map[single_cat] = {
            "category_id": 1,
            "name": single_cat,
            "slug": cat_slug,
            "skill_count": 1,
        }
        capabilities.append(
            {
                "index": 1,
                "category_name": single_cat,
                "category_slug": cat_slug,
                "name": title_line,
                "slug": slugify_name(title_line),
                "description": body_text,
            }
        )

    cat_list = list(categories_map.values())
    if len(cat_list) == 1 and has_explicit_category:
        umbrella = cat_list[0]["slug"]
    elif len(cat_list) > 1:
        # Derive concise shared-purpose hyphenated name
        words: Dict[str, int] = {}
        stop = {"and", "the", "for", "of", "in", "to", "with", "a", "an", "general", "category"}
        for c in cat_list:
            for tok in c["slug"].split("-"):
                if tok and tok not in stop and len(tok) > 2:
                    words[tok] = words.get(tok, 0) + 1
        top_tokens = [w for w, _ in sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        umbrella = "-".join(top_tokens) if top_tokens else "unified-capability-suite"
    else:
        umbrella = capabilities[0]["slug"][:48].strip("-") if capabilities else "unified-skill-tool"

    return {
        "source_hint": source_hint,
        "umbrella_name": umbrella,
        "category_count": len(cat_list),
        "capability_count": len(capabilities),
        "categories": cat_list,
        "capabilities": capabilities,
    }


def extract_text_from_spec_file(path: Path) -> str:
    """Read plain text, Markdown, HTML, JSON, or PDF (if pypdf available or stream fallback)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raw_bytes = path.read_bytes()
            ascii_strings = re.findall(rb"\(([^\(\)\\]{3,})\)", raw_bytes)
            return "\n".join(s.decode("latin-1", errors="ignore") for s in ascii_strings)
    return path.read_text(encoding="utf-8", errors="replace")


class CatalogRegistry:
    """Indexes all 41 categories, 15 roles, and 615 capabilities for fast lookup and filtering."""

    def __init__(self, catalog_path: Path):
        self.catalog_data: Dict[str, Any] = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.roles: Dict[str, Dict[str, Any]] = self.catalog_data.get("roles", {})
        self.categories: List[Dict[str, Any]] = self.catalog_data.get("categories", [])
        self.capabilities: List[Dict[str, Any]] = self.catalog_data.get("capabilities", [])

        self._by_id: Dict[int, Dict[str, Any]] = {}
        self._by_slug: Dict[str, Dict[str, Any]] = {}
        self._by_name_lower: Dict[str, Dict[str, Any]] = {}
        self._by_cat_and_role: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._cat_lookup: Dict[str, Dict[str, Any]] = {}

        for cat in self.categories:
            cid = str(cat["category_id"])
            self._cat_lookup[cid] = cat
            self._cat_lookup[cat["slug"]] = cat
            self._cat_lookup[cat["name"].lower()] = cat

        for cap in self.capabilities:
            gid = int(cap["global_id"])
            self._by_id[gid] = cap
            self._by_slug[cap["slug"]] = cap
            self._by_name_lower[cap["name"].lower()] = cap
            self._by_cat_and_role[(cap["category_slug"], cap["role_slug"])] = cap

    def resolve_category(self, cat_query: str) -> Dict[str, Any]:
        q = str(cat_query).strip()
        if q in self._cat_lookup:
            return self._cat_lookup[q]
        ql = q.lower()
        if ql in self._cat_lookup:
            return self._cat_lookup[ql]
        qslug = slugify_name(q)
        if qslug in self._cat_lookup:
            return self._cat_lookup[qslug]
        for cat in self.categories:
            if qslug in cat["slug"] or ql in cat["name"].lower():
                return cat
        raise KeyError(f"Unknown category '{cat_query}'. Run 'list-capabilities --summary' to view all 41 categories.")

    def resolve_role(self, role_query: str) -> str:
        rslug = slugify_name(role_query)
        if rslug in self.roles:
            return rslug
        for k, v in self.roles.items():
            if v["role_name"].lower() == role_query.strip().lower():
                return k
        raise KeyError(
            f"Unknown role '{role_query}'. Valid roles: {', '.join(sorted(self.roles.keys()))}"
        )

    def resolve_capability(
        self,
        capability_ref: Optional[str] = None,
        category_ref: Optional[str] = None,
        role_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        if capability_ref:
            ref = str(capability_ref).strip()
            if ref.isdigit():
                gid = int(ref)
                if gid in self._by_id:
                    return self._by_id[gid]
            if ref in self._by_slug:
                return self._by_slug[ref]
            if ref.lower() in self._by_name_lower:
                return self._by_name_lower[ref.lower()]
            slug_ref = slugify_name(ref)
            if slug_ref in self._by_slug:
                return self._by_slug[slug_ref]
            # Check if user passed a category slug/name directly; default to 'operator' role
            try:
                cat = self.resolve_category(ref)
                rslug = self.resolve_role(role_ref) if role_ref else "operator"
                return self._by_cat_and_role[(cat["slug"], rslug)]
            except KeyError:
                pass
            raise KeyError(
                f"Unknown capability '{capability_ref}'. Use 'list-capabilities' to inspect valid IDs (1..615) or slugs."
            )

        if category_ref:
            cat = self.resolve_category(category_ref)
            rslug = self.resolve_role(role_ref) if role_ref else "operator"
            return self._by_cat_and_role[(cat["slug"], rslug)]

        raise KeyError("Either a capability identifier or --category must be provided.")

    def filter_capabilities(
        self,
        category: Optional[str] = None,
        role: Optional[str] = None,
        domain: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results = list(self.capabilities)
        if category:
            cat = self.resolve_category(category)
            results = [c for c in results if c["category_slug"] == cat["slug"]]
        if role:
            rslug = self.resolve_role(role)
            results = [c for c in results if c["role_slug"] == rslug]
        if domain:
            dslug = slugify_name(domain)
            results = [c for c in results if dslug in c["domain_slug"]]
        if search:
            q = search.strip().lower()
            results = [
                c
                for c in results
                if q in c["name"].lower()
                or q in c["slug"]
                or q in c["category_name"].lower()
                or q in c["description"].lower()
            ]
        return results

    def inspect_capability(self, cap: Dict[str, Any]) -> Dict[str, Any]:
        cat = self._cat_lookup[str(cap["category_id"])]
        role_info = self.roles.get(cap["role_slug"], {})
        return {
            "capability": cap,
            "category_contract": cat,
            "role_contract": role_info,
            "operational_guarantees": {
                "bounded_execution": True,
                "permission_aware": True,
                "provenance_sha256_chain": True,
                "truthful_action_accounting": True,
                "atomic_checkpoint_recovery": True,
            },
        }
