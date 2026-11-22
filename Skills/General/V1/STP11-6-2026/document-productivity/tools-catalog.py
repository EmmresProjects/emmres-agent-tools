#!/usr/bin/env python3
"""Capability registry and tolerant source parser.

Accepts a single capability, a numbered Skill Tool list, one category, or
several categories in plain text, Markdown, or copied HTML. HTML tags, CSS
classes, data attributes, numbering marks, PDF running headers, and extra
spacing are ignored. Every parsed capability is normalized into one unified
plan rather than several tools.
"""
from __future__ import annotations

import difflib
import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

dp_safety = __import__("sys").modules["dp_safety"]
ToolError = dp_safety.ToolError
slugify = dp_safety.slugify
display_path = dp_safety.display_path
json_ready = dp_safety.json_ready

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is",
    "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "with", "without",
    "using", "use", "used", "across", "clear", "controlled", "validation", "checks",
    "editable", "outputs", "complete", "specialized", "capability", "skill", "category",
    "production", "ready", "reliable", "consistent", "strong", "high", "common", "purpose",
    "built", "deliver", "create", "build", "manage", "support", "maintain", "provide",
    "documents", "document", "content", "workbooks", "workbook", "data", "design",
    "structure", "quality", "based", "where", "possible", "behavior", "settings",
    "inputs", "formatting", "rules", "ongoing", "maintenance", "exceptions", "changes",
    "setup", "defines", "controls", "handles", "produces", "verifies", "evaluates",
    "calculates", "tests", "applies", "turns", "organizes", "protects", "moves",
    "automates", "detects", "uses", "assembles", "implements", "records", "connects",
}

PAGE_HEADER_RE = re.compile(r"^Comprehensive Document and Productivity Skill Catalog\s*\|\s*Page\s+\d+$", re.I)
PAGE_MARKER_RE = re.compile(r"^=====\s*PAGE\s+\d+\s*=====$", re.I)
CATEGORY_INDEX_RE = re.compile(r"^(.+?)\s*\((\d+)\s+skills?\)$", re.I)
CATEGORY_LABEL_RE = re.compile(r"^(?:Category(?:\s+Name)?|Umbrella\s+Category)\s*(?:\d+\s*[:.)-]\s*|\s*:\s*)?(.+)$", re.I)
CATEGORY_NUMBER_RE = re.compile(r"^(\d{1,2})$")
SKILL_LABEL_RE = re.compile(r"^(?:Skill(?:\s+Tool)?\s+Name|Capability\s+Name|Name)\s*:\s*(.+)$", re.I)
DESC_LABEL_RE = re.compile(r"^Description\s*:\s*(.*)$", re.I)
INLINE_DESC_RE = re.compile(r"^(.*?)\s+Description\s*:\s*(.*)$", re.I)
NUMBER_PREFIX_RE = re.compile(r"^(?:\d+[.)]\s*|\(\d+\)\s*|[A-Za-z]\.\s*|[-*+•]\s*)")
HTML_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


class HTMLTextExtractor(HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td",
             "th", "section", "article", "ol", "ul", "header", "footer", "table", "dl", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


@dataclass
class ParsedCapability:
    name: str
    description: str
    category_name: str
    category_slug: str
    source_number: int | None
    id: str
    slug: str
    global_slug: str
    action: str
    operation: str
    handler: str
    default_format: str
    supported_formats: list[str]
    approval_classes: list[str]
    approval_required: bool
    provider_hint: str | None
    external_integration: bool
    responsibility: str
    focus: str
    from_catalog: bool = False

    def to_dict(self) -> dict[str, Any]:
        return json_ready(self.__dict__)


@dataclass
class ParsedSource:
    source_label: str
    source_format: str
    umbrella_name: str
    categories: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"source_label": self.source_label, "source_format": self.source_format,
                "umbrella_name": self.umbrella_name, "categories": self.categories,
                "capabilities": self.capabilities, "warnings": self.warnings,
                "metrics": self.metrics}


@dataclass
class UnifiedPlan:
    schema_version: str
    tool_name: str
    source_label: str
    capability_count: int
    categories: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    operations: list[str]
    handlers: list[str]
    approval_classes: list[str]
    provider_hints: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return json_ready(self.__dict__)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def clean_line(line: str, source_format: str) -> str:
    text = line.replace("\xa0", " ").replace("\t", " ")
    # Copied HTML and Markdown both carry numbering marks; all source styles use
    # the same normalization so tags, classes, and data attributes disappear.
    text = HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = MD_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text)
    text = re.sub(r"^\s*(?:[-*+•]\s*|\d+[.)]\s*|\(\d+\)\s*|[A-Za-z]\.\s*)", "", text)
    text = re.sub(r"^\s*[-*+•]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_source_format(text: str) -> str:
    if re.search(r"<[A-Za-z/!][^>]*>", text) and re.search(r"</?[A-Za-z][^>]*>", text):
        return "html"
    if re.search(r"(?m)^\s{0,3}#{1,6}\s+", text) or "**" in text or re.search(r"\[[^\]]+\]\([^)]*\)", text):
        return "markdown"
    return "text"


def source_lines(text: str, source_format: str) -> list[str]:
    if source_format == "html":
        parser = HTMLTextExtractor()
        parser.feed(text)
        parser.close()
        raw = parser.text()
    else:
        raw = text
    return [clean_line(line, source_format) for line in raw.splitlines()]


def derive_umbrella_name(categories: list[dict[str, Any]], capabilities: list[ParsedCapability],
                         source_text: str, source_format: str,
                         source_has_category: bool = True) -> str:
    if not source_has_category:
        # The source supplied no category label; derive a short name from the
        # shared purpose of the supplied capabilities rather than catalog metadata.
        if len(capabilities) == 1:
            return slugify(capabilities[0].name, "unified-capability")
        counts = Counter()
        for capability in capabilities:
            tokens = set(re.findall(r"[a-z0-9]+", capability.description.lower()))
            for token in tokens:
                if token not in STOPWORDS and len(token) > 2 and not token.isdigit():
                    counts[token] += 1
        common = [token for token, count in counts.items() if count >= 2]
        common.sort(key=lambda t: (-counts[t], len(t), t))
        return slugify("-".join(common[:3]), "unified-capability")
    if len(categories) == 1:
        return slugify(categories[0]["name"], "unified-capability")
    combined = source_text.lower()
    if "productivity" in combined and "document" in combined:
        return "document-productivity"
    if len(categories) > 1:
        counts: Counter[str] = Counter()
        for category in categories:
            tokens = set(re.findall(r"[a-z0-9]+", (category.get("name", "") + " " + category.get("purpose", "")).lower()))
            for token in tokens:
                if token not in STOPWORDS and len(token) > 2:
                    counts[token] += 1
        threshold = max(2, len(categories) // 2)
        common = [token for token, count in counts.items() if count >= threshold]
        common.sort(key=lambda t: (-counts[t], len(t), t))
        if common:
            return slugify("-".join(common[:3]), "unified-document-workflow")
        return "unified-document-workflow"
    if capabilities:
        # No category in the source: derive from the shared purpose of descriptions.
        counts = Counter()
        for capability in capabilities:
            tokens = set(re.findall(r"[a-z0-9]+", capability.description.lower()))
            for token in tokens:
                if token not in STOPWORDS and len(token) > 2 and not token.isdigit():
                    counts[token] += 1
        threshold = 2 if len(capabilities) > 1 else 1
        common = [token for token, count in counts.items() if count >= threshold]
        common.sort(key=lambda t: (-counts[t], len(t), t))
        if common:
            return slugify("-".join(common[:3]), "unified-capability")
        return slugify(capabilities[0].name, "unified-capability")
    return "unified-capability"


def responsibility_from_description(description: str, name: str) -> str:
    if ";" in description:
        tail = description.split(";", 1)[1].strip()
        if tail:
            return tail
    return f"Implements {name} as a bounded, validated document-productivity operation."


class Catalog:
    GENERIC_OPERATION = "custom-capability"
    GENERIC_HANDLER = "op_generic_capability"
    GENERIC_FORMATS = ["json", "html", "md", "csv", "txt", "docx", "pdf"]

    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path
        self.categories = data.get("categories", [])
        self.category_count = int(data.get("category_count", len(self.categories)))
        self.capability_count = int(data.get("capability_count", sum(len(c.get("skills", [])) for c in self.categories)))
        self.by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.by_name: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        self.by_global_slug: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.by_slug: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        self.category_by_norm: dict[str, dict[str, Any]] = {}
        self.category_by_slug: dict[str, dict[str, Any]] = {}
        self.category_by_number: dict[int, dict[str, Any]] = {}
        self._index()

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        if not path.is_file():
            raise ToolError("catalog_not_found", "Capability catalog does not exist.",
                            {"path": display_path(path)})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError("catalog_parse_error", "Capability catalog could not be parsed.",
                            {"path": display_path(path), "error": str(exc)})
        return cls(data, path)

    def _index(self) -> None:
        for category in self.categories:
            self.category_by_norm[norm(category.get("name", ""))] = category
            self.category_by_slug[category.get("slug", "")] = category
            self.category_by_number[int(category.get("number", 0))] = category
            for skill in category.get("skills", []):
                pair = (skill, category)
                self.by_id[str(skill.get("id", ""))] = pair
                self.by_global_slug[str(skill.get("global_slug", ""))] = pair
                self.by_name.setdefault(norm(str(skill.get("name", ""))), []).append(pair)
                self.by_slug.setdefault(str(skill.get("slug", "")), []).append(pair)

    def all_capabilities(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        return [(s, c) for c in self.categories for s in c.get("skills", [])]

    def list_capabilities(self, category: str | None = None, query: str | None = None,
                          limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        category_obj = self.resolve_category(category) if category else None
        query_norm = norm(query or "")
        rows = []
        for skill, cat in self.all_capabilities():
            if category_obj and cat is not category_obj:
                continue
            if query_norm and query_norm not in norm(skill.get("name", "") + " " + skill.get("description", "") + " " + cat.get("name", "")):
                continue
            rows.append(self.public_capability(skill, cat))
        rows = rows[offset:]
        if limit is not None:
            rows = rows[:max(0, int(limit))]
        return rows

    @staticmethod
    def public_capability(skill: dict[str, Any], category: dict[str, Any]) -> dict[str, Any]:
        return {key: skill.get(key) for key in (
            "id", "name", "slug", "global_slug", "description", "responsibility", "action",
            "operation", "handler", "default_format", "supported_formats", "approval_classes",
            "approval_required", "provider_hint", "external_integration")} | {
                "category": category.get("name"), "category_slug": category.get("slug"),
                "category_number": category.get("number")}

    def resolve_category(self, identifier: str | int | None) -> dict[str, Any] | None:
        if identifier is None or str(identifier).strip() == "":
            return None
        raw = str(identifier).strip()
        if CATEGORY_NUMBER_RE.fullmatch(raw):
            found = self.category_by_number.get(int(raw))
            if found:
                return found
        key = norm(raw)
        if key in self.category_by_norm:
            return self.category_by_norm[key]
        if raw.lower() in self.category_by_slug:
            return self.category_by_slug[raw.lower()]
        for cat in self.categories:
            if norm(raw) in norm(cat.get("name", "")) or norm(cat.get("name", "")) in norm(raw):
                return cat
        return None

    def resolve_capability(self, identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = (identifier or "").strip()
        if not raw:
            raise ToolError("capability_not_specified", "A capability name, id, or slug is required.")
        if raw in self.by_id:
            return self.by_id[raw]
        if raw in self.by_global_slug:
            return self.by_global_slug[raw]
        key = norm(raw)
        matches = self.by_name.get(key, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolError("ambiguous_capability", "Capability name is ambiguous; use an id or global slug.",
                            {"identifier": raw, "candidates": [
                                {"id": s.get("id"), "category": c.get("name")} for s, c in matches]})
        slug = raw.lower()
        matches = self.by_slug.get(slug, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolError("ambiguous_capability", "Capability slug is ambiguous; use an id or global slug.",
                            {"identifier": raw, "candidates": [
                                {"id": s.get("id"), "category": c.get("name")} for s, c in matches]})
        names = [s.get("name", "") for s, _ in self.all_capabilities()]
        suggestions = difflib.get_close_matches(raw, names, n=5, cutoff=0.55)
        raise ToolError("unknown_capability", "Capability was not found in the unified catalog.",
                        {"identifier": raw, "suggestions": suggestions})

    def capability_from_catalog(self, skill: dict[str, Any], category: dict[str, Any],
                                source_number: int | None = None) -> ParsedCapability:
        return ParsedCapability(
            name=str(skill["name"]), description=str(skill["description"]),
            category_name=str(category["name"]), category_slug=str(category["slug"]),
            source_number=source_number, id=str(skill["id"]), slug=str(skill["slug"]),
            global_slug=str(skill["global_slug"]), action=str(skill["action"]),
            operation=str(skill["operation"]), handler=str(skill["handler"]),
            default_format=str(skill["default_format"]),
            supported_formats=list(skill["supported_formats"]),
            approval_classes=list(skill["approval_classes"]),
            approval_required=bool(skill["approval_required"]),
            provider_hint=skill.get("provider_hint"),
            external_integration=bool(skill.get("external_integration")),
            responsibility=str(skill.get("responsibility", "")),
            focus=str(skill.get("focus", skill["slug"])), from_catalog=True)

    def generic_capability(self, name: str, description: str, category_name: str,
                           source_number: int | None, policy: Any | None) -> ParsedCapability:
        category_obj = self.resolve_category(category_name)
        category_slug = category_obj["slug"] if category_obj else slugify(category_name or "custom")
        if not category_name:
            category_name = "Custom Capability"
            category_slug = "custom-capability"
            category_obj = None
        slug = slugify(name)
        global_slug = f"{category_slug}--{slug}"
        classes = policy.classify(name, description, category_name) if policy else []
        provider_hint = None
        if category_number := (category_obj or {}).get("number"):
            # Provider hints are only attached when the source category is known.
            for provider, needle in (("pdf", "pdf"), ("canva", "canva"), ("microsoft-word", "word"),
                                     ("microsoft-excel", "excel"), ("google-docs", "google docs"),
                                     ("google-sheets", "google sheets"), ("libreoffice", "libreoffice")):
                if needle in (name + " " + category_name).lower():
                    provider_hint = provider
                    break
        return ParsedCapability(
            name=name, description=description, category_name=category_name,
            category_slug=category_slug, source_number=source_number,
            id=f"custom-{slug}"[:90], slug=slug, global_slug=global_slug,
            action="operate", operation=self.GENERIC_OPERATION,
            handler=self.GENERIC_HANDLER, default_format="json",
            supported_formats=list(self.GENERIC_FORMATS), approval_classes=classes,
            approval_required=bool(classes), provider_hint=provider_hint,
            external_integration=bool(provider_hint),
            responsibility=responsibility_from_description(description, name),
            focus=slug, from_catalog=False)

    def parse_source(self, text: str, source_label: str = "inline", policy: Any | None = None,
                     expand_categories: bool = True) -> ParsedSource:
        if not text or not text.strip():
            raise ToolError("empty_source", "The capability source is empty.",
                            {"source_label": source_label})
        source_format = detect_source_format(text)
        lines = source_lines(text, source_format)
        warnings: list[str] = []
        detected: list[dict[str, Any]] = []
        detected_keys: set[str] = set()
        explicit: list[tuple[str, str, str | None, int | None, list[str]]] = []
        current_category: dict[str, Any] | None = None
        current_purpose: list[str] = []
        current: dict[str, Any] | None = None
        mode = "idle"

        def add_category(name: str, purpose: str = "") -> dict[str, Any]:
            canonical = self.resolve_category(name)
            key = norm(canonical["name"] if canonical else name)
            if key in detected_keys:
                for item in detected:
                    if norm(item["name"]) == key:
                        if purpose and not item.get("purpose"):
                            item["purpose"] = re.sub(r"\s+", " ", purpose).strip()
                        return item
                return detected[0]
            obj = {"name": canonical["name"] if canonical else name,
                   "slug": canonical["slug"] if canonical else slugify(name),
                   "purpose": re.sub(r"\s+", " ", purpose).strip(),
                   "canonical": bool(canonical),
                   "number": canonical.get("number") if canonical else None}
            detected.append(obj)
            detected_keys.add(key)
            return obj

        def flush_current() -> None:
            nonlocal current
            if current:
                desc = re.sub(r"\s+", " ", " ".join(current["description_parts"])).strip()
                if not desc:
                    desc = f"A specialized capability to implement {current['name']} with clear inputs, validation checks, and editable outputs."
                    warnings.append(f"Missing description for '{current['name']}'; a bounded generic description was used.")
                explicit.append((current["name"], desc, current["category"], current["number"], current["description_parts"]))
                current = None

        for original in lines:
            line = original.strip()
            if not line:
                continue
            if PAGE_HEADER_RE.match(line) or PAGE_MARKER_RE.match(line):
                continue
            if re.fullmatch(r"(?:Comprehensive Document and Productivity Skill Catalog|Category Index|Purpose|Catalog scope)", line, re.I):
                continue
            index_match = CATEGORY_INDEX_RE.match(line)
            if index_match:
                add_category(index_match.group(1).strip())
                continue
            category_match = CATEGORY_LABEL_RE.match(line)
            if category_match:
                flush_current()
                name = category_match.group(1).strip()
                current_category = add_category(name)
                current_purpose = []
                mode = "category_purpose"
                continue
            exact_category = self.resolve_category(line)
            if exact_category and not SKILL_LABEL_RE.match(line) and not DESC_LABEL_RE.match(line):
                flush_current()
                current_category = add_category(exact_category["name"], exact_category.get("purpose", ""))
                current_purpose = []
                mode = "category_purpose"
                continue
            inline = INLINE_DESC_RE.match(line)
            if SKILL_LABEL_RE.match(line) or (inline and SKILL_LABEL_RE.match(inline.group(1))):
                flush_current()
                number = None
                raw_number = NUMBER_PREFIX_RE.match(line)
                if raw_number:
                    digits = re.search(r"\d+", raw_number.group(0))
                    if digits:
                        number = int(digits.group(0))
                if inline:
                    skill_line, desc_part = inline.group(1), inline.group(2)
                else:
                    skill_line, desc_part = line, ""
                skill_match = SKILL_LABEL_RE.match(skill_line)
                if not skill_match:
                    continue
                name = re.sub(r"\s+", " ", skill_match.group(1)).strip()
                current = {"name": name, "description_parts": [desc_part] if desc_part else [],
                           "category": current_category["name"] if current_category else None,
                           "number": number}
                mode = "item_description" if desc_part else "item_name"
                continue
            desc_match = DESC_LABEL_RE.match(line)
            if desc_match and current:
                current["description_parts"].append(desc_match.group(1).strip())
                mode = "item_description"
                continue
            if current and mode == "item_description" and line:
                current["description_parts"].append(line)
                continue
            if mode == "category_purpose" and current_category and line and not current:
                current_purpose.append(line)
                continue
        flush_current()
        for cat in detected:
            if cat.get("purpose"):
                continue
            canonical = self.resolve_category(cat["name"])
            if canonical:
                cat["purpose"] = canonical.get("purpose", "")

        parsed_capabilities: list[ParsedCapability] = []
        if not explicit and detected:
            if expand_categories:
                for cat in detected:
                    canonical = self.resolve_category(cat["name"])
                    if canonical:
                        for skill in canonical.get("skills", []):
                            parsed_capabilities.append(self.capability_from_catalog(skill, canonical))
                    else:
                        name = f"{cat['name']} Workflow"
                        desc = cat.get("purpose") or f"A specialized capability to implement {cat['name']} as one unified operation."
                        parsed_capabilities.append(self.generic_capability(name, desc, cat["name"], None, policy))
                        warnings.append(f"Unknown category '{cat['name']}' was represented by one bounded generic capability.")
            else:
                raise ToolError("category_expansion_disabled",
                                "The source contains categories but no listed capabilities, and expansion is disabled.",
                                {"categories": [c["name"] for c in detected]})
        elif explicit:
            seen: set[str] = set()
            for name, desc, category_name, number, _parts in explicit:
                key = f"{norm(category_name or '')}|{norm(name)}"
                if key in seen:
                    warnings.append(f"Duplicate capability '{name}' was combined once.")
                    continue
                seen.add(key)
                exact_matches = self.by_name.get(norm(name), [])
                if len(exact_matches) == 1:
                    skill, cat = exact_matches[0]
                    if not category_name or self.resolve_category(category_name) is cat:
                        parsed_capabilities.append(self.capability_from_catalog(skill, cat, number))
                        continue
                if len(exact_matches) > 1 and category_name:
                    category_obj = self.resolve_category(category_name)
                    if category_obj:
                        candidates = [pair for pair in exact_matches if pair[1] is category_obj]
                        if len(candidates) == 1:
                            skill, cat = candidates[0]
                            parsed_capabilities.append(self.capability_from_catalog(skill, cat, number))
                            continue
                parsed_capabilities.append(self.generic_capability(name, desc, category_name or "", number, policy))
                if not category_name:
                    warnings.append(f"Capability '{name}' had no source category; one unified generic handler was assigned.")
        else:
            raise ToolError("no_capabilities_found",
                            "No category names, Skill Tool Names, or Descriptions were found.",
                            {"source_label": source_label, "source_format": source_format})

        parsed_categories: list[dict[str, Any]] = []
        seen_categories: set[str] = set()
        for capability in parsed_capabilities:
            key = norm(capability.category_name)
            if key in seen_categories:
                continue
            seen_categories.add(key)
            canonical = self.resolve_category(capability.category_name)
            parsed_categories.append({
                "name": canonical["name"] if canonical else capability.category_name,
                "slug": canonical["slug"] if canonical else capability.category_slug,
                "purpose": canonical.get("purpose", "") if canonical else "",
                "canonical": bool(canonical),
                "number": canonical.get("number") if canonical else None,
            })
        if not parsed_categories and detected:
            parsed_categories = detected
        source_has_category = bool(detected)
        if source_has_category and len(parsed_categories) > 1 and all(c.get("canonical") for c in parsed_categories):
            umbrella = slugify(str(self.data.get("tool_name", "document-productivity")),
                               "document-productivity")
        else:
            umbrella = derive_umbrella_name(parsed_categories, parsed_capabilities, text,
                                            source_format, source_has_category)
        return ParsedSource(
            source_label=source_label, source_format=source_format, umbrella_name=umbrella,
            categories=parsed_categories, capabilities=[c.to_dict() for c in parsed_capabilities],
            warnings=warnings,
            metrics={"source_chars": len(text), "source_lines": len(lines),
                     "detected_categories": len(detected), "explicit_capabilities": len(explicit),
                     "unified_capabilities": len(parsed_capabilities),
                     "expanded_from_catalog": bool(not explicit and detected and expand_categories),
                     "canonical_capabilities": sum(1 for c in parsed_capabilities if c.from_catalog)})

    def build_plan(self, parsed: ParsedSource) -> UnifiedPlan:
        capabilities = parsed.capabilities
        operations = sorted({str(c["operation"]) for c in capabilities})
        handlers = sorted({str(c["handler"]) for c in capabilities})
        classes = sorted({cls for c in capabilities for cls in c.get("approval_classes", [])})
        providers = sorted({str(c["provider_hint"]) for c in capabilities if c.get("provider_hint")})
        return UnifiedPlan(
            schema_version="1.0", tool_name=parsed.umbrella_name,
            source_label=parsed.source_label, capability_count=len(capabilities),
            categories=parsed.categories, capabilities=capabilities,
            operations=operations, handlers=handlers, approval_classes=classes,
            provider_hints=providers, warnings=parsed.warnings)

    def validate(self, validator: Any | None = None) -> dict[str, Any]:
        if validator:
            validator.validate_named(self.data, "catalog")
        skills = self.all_capabilities()
        ids = [s.get("id") for s, _ in skills]
        handlers = sorted({s.get("handler") for s, _ in skills})
        missing = [key for s, _ in skills for key in ("name", "description", "operation", "handler", "supported_formats") if key not in s]
        return {"ok": not missing and len(set(ids)) == len(ids),
                "category_count": len(self.categories), "capability_count": len(skills),
                "unique_ids": len(set(ids)), "handlers": handlers,
                "missing_fields": missing[:20]}
