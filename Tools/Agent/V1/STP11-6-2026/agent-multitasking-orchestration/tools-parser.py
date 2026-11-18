#!/usr/bin/env python3
"""Source ingestion and capability-catalog extraction.

Accepts plain text, Markdown, copied HTML, PDF, and DOCX. PDF support uses the
optional pypdf dependency declared in tools-requirements.txt; DOCX, HTML, text,
and Markdown use the Python standard library. No network or shell execution is
used. The parser extracts category names, Skill Tool Names, and Descriptions
while discarding numbering, markup, CSS/data attributes, page furniture, and
extra spacing.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import sys as _sys
from importlib import import_module as _import_module

# Hyphenated filenames are required by the Skill Tool naming contract. Register
# a conventional module alias once so supporting modules can import each other.
_sys.modules.setdefault("tools_common", _import_module("tools-common"))
from tools_common import (
    ToolError,
    canonical_json,
    clip,
    load_text,
    read_bytes,
    safe_path,
    sha256_text,
    unique,
)

SCHEMA_VERSION = "1.0"
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "template"}
ATTRIBUTE_FRAGMENT = re.compile(
    r"\b(?:class|id|style|for|role|data-[a-z0-9_-]+|aria-[a-z0-9_-]+)\s*=\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
TAG_FRAGMENT = re.compile(r"<[^>]*>")
PAGE_MARKER = re.compile(r"<<<\s*PAGE\s+\d+\s*>>>", re.IGNORECASE)
CATALOG_HEADER = re.compile(r"^Agent\s+Multitasking\s+Skill\s+Catalog\s*\|\s*\d+\s*$", re.IGNORECASE)
NUMBERING = re.compile(r"^\s*(?:[-*•▪◦‣]|\(?\d+[.)]|\(?[A-Za-z][.)])\s+")
IGNORED_HEADING_WORDS = {
    "catalog guide",
    "recommended catalog fields for future builds",
    "category index",
    "agent multitasking skill catalog",
    "agent multitasking",
    "skill catalog",
    "instances workers thought processors and multi agent orchestration",
}
STOPWORDS = {
    "a", "about", "above", "across", "after", "against", "all", "also", "an",
    "and", "any", "are", "as", "at", "based", "be", "before", "being", "between",
    "both", "but", "by", "can", "each", "for", "from", "has", "have", "into",
    "its", "more", "not", "of", "on", "one", "or", "other", "over", "rather",
    "so", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "to", "under", "until", "using", "when", "where",
    "while", "with", "without", "skill", "skills", "tool", "tools", "name",
    "description", "catalog", "category", "capability", "capabilities",
}


class HTMLTextExtractor(HTMLParser):
    """Convert copied HTML into plain lines while ignoring attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.chunks.append(data)

    def text(self) -> str:
        return "".join(self.chunks)


@dataclass
class SourceDocument:
    path: str
    source_format: str
    raw_text: str
    normalized_text: str
    warnings: list[str] = field(default_factory=list)
    page_count: int = 1


@dataclass
class ParsedCatalog:
    document: SourceDocument
    data: dict[str, Any]


def slugify(value: str) -> str:
    """Return a portable, lowercase ASCII hyphenated identifier."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("&", " and ").replace("+", " and ")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)
    return text or "capability"


def clean_lines(value: str) -> list[str]:
    value = PAGE_MARKER.sub("\n", value)
    value = re.sub(r"<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = TAG_FRAGMENT.sub(" ", value)
    value = ATTRIBUTE_FRAGMENT.sub(" ", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\ufeff]", "", value)
    result: list[str] = []
    for raw_line in value.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if CATALOG_HEADER.match(line) or re.fullmatch(r"Page\s+\d+\s*(?:/\s*\d+)?", line, re.IGNORECASE):
            continue
        line = line.replace("**", "").replace("__", "")
        line = NUMBERING.sub("", line)
        line = ATTRIBUTE_FRAGMENT.sub(" ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line or re.fullmatch(r"[-:|=\s]+", line):
            continue
        result.append(line)
    return result


def _title_from(lines: list[str]) -> str:
    joined = " ".join(lines[:8]).lower()
    if "agent multitasking" in joined:
        return "Agent Multitasking"
    for line in lines[:5]:
        cleaned = re.sub(r"^#{1,6}\s*", "", line).strip(" #:-")
        if len(cleaned) >= 4 and not cleaned.lower().startswith(("skill name", "description", "category")):
            return clip(cleaned, 100)
    return "Unified Capability Set"


def _tokens(value: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", value.lower())
    return [word for word in words if word not in STOPWORDS and len(word) > 2]


def _shared_purpose(skills: list[dict[str, str]], title: str) -> tuple[str, str]:
    if title and title != "Unified Capability Set":
        name = title
        slug = slugify(name)
        if slug not in {"capability", "unified-capability-set"}:
            return name, slug
    frequency: dict[str, int] = {}
    for skill in skills:
        for token in unique(_tokens(f"{skill['name']} {skill['description']}")):
            frequency[token] = frequency.get(token, 0) + 1
    ranked = sorted(frequency.items(), key=lambda item: (-item[1], item[0]))
    chosen = [word for word, _ in ranked[:3]]
    if not chosen:
        return "General Capabilities", "general-capabilities"
    name = " ".join(word.capitalize() for word in chosen)
    return name, slugify("-".join(chosen))


def _is_category_line(line: str) -> str | None:
    stripped = line.strip()
    lower = stripped.lower().strip(" #:-")
    if lower in IGNORED_HEADING_WORDS or lower.startswith("recommended catalog fields"):
        return None
    patterns = [
        r"^Category\s+\d+\s*[:\-–]\s*(.+)$",
        r"^Category\s*[:\-–]\s*(.+)$",
        r"^#{1,6}\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if match:
            name = re.sub(r"\s*\((\d+)\s+skills?\)\s*$", "", match.group(1), flags=re.IGNORECASE).strip(" #:-")
            if name and name.lower() not in IGNORED_HEADING_WORDS:
                return name
    match = re.match(r"^(.+?)\s*\((\d+)\s+skills?\)\s*$", stripped, re.IGNORECASE)
    if match:
        name = match.group(1).strip(" #:-")
        if name and name.lower() not in IGNORED_HEADING_WORDS and len(name) <= 120:
            return name
    return None


def _skill_start(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    match = re.match(
        r"^(?:Skill(?:\s+Tool)?\s+Name|Skill|Name|Capability|Tool)\s*[:\-–]\s*(.+)$",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return None
    remainder = match.group(1).strip()
    desc_match = re.search(r"\bDescription\s*[:\-–]\s*(.*)$", remainder, re.IGNORECASE)
    if desc_match:
        name = remainder[: desc_match.start()].strip(" -–:\t")
        description = desc_match.group(1).strip()
    else:
        name = remainder.strip(" -–:\t")
        description = ""
    if not name or len(name) > 160:
        return None
    return name, description


def _table_skill(line: str, header: list[str] | None) -> tuple[str, str] | None:
    if not (line.startswith("|") and line.endswith("|")):
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    cells = [cell for cell in cells if cell]
    if len(cells) < 2:
        return None
    lowered = [cell.lower() for cell in cells]
    if any("skill" in cell and "name" in cell for cell in lowered) and any("description" in cell for cell in lowered):
        return None
    if header and len(cells) >= len(header):
        name_index = next((i for i, cell in enumerate(header) if "name" in cell or "skill" in cell), 0)
        desc_index = next((i for i, cell in enumerate(header) if "description" in cell), 1)
        return cells[name_index], " ".join(cells[desc_index:])
    if re.fullmatch(r"[\d.]+", cells[0]) and len(cells) >= 3:
        return cells[1], " ".join(cells[2:])
    return cells[0], " ".join(cells[1:])


def _is_guide_line(line: str) -> bool:
    lower = line.lower()
    return (
        lower.startswith("each entry is")
        or lower.startswith("names can be used")
        or lower.startswith("trigger conditions")
        or lower.startswith("expected inputs")
        or lower.startswith("allowed tools")
        or lower.startswith("workflow steps")
        or lower.startswith("quality gates")
        or lower.startswith("resource limits")
        or lower.startswith("artifacts,")
        or lower.startswith("observability,")
        or lower.startswith("25 categories")
        or lower.startswith("instances, workers")
        or lower.startswith("a comprehensive catalog")
    )


def _detect_format(path: Path, requested: str, raw: str) -> str:
    if requested != "auto":
        return requested
    suffix = path.suffix.lower()
    suffix_map = {
        ".txt": "text", ".text": "text", ".md": "markdown", ".markdown": "markdown",
        ".html": "html", ".htm": "html", ".pdf": "pdf", ".docx": "docx",
    }
    if suffix in suffix_map:
        return suffix_map[suffix]
    sample = raw[:4096].lower()
    if "<html" in sample or "<div" in sample:
        return "html"
    if re.search(r"^#{1,6}\s|\|\s*---", sample, re.MULTILINE):
        return "markdown"
    return "text"


def _extract_pdf(path: Path, max_bytes: int, max_pages: int, warnings: list[str]) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ToolError(
            "OPTIONAL_DEPENDENCY_MISSING",
            "Direct PDF input requires pypdf.",
            details={"path": path.name, "dependency": "pypdf>=5.0,<7.0"},
            recovery="Install agent-multitasking-orchestration/tools-requirements.txt or copy the PDF text to a .txt file.",
            exit_code=4,
        ) from exc
    data = read_bytes(path, max_bytes=max_bytes)
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)
    except Exception as exc:
        raise ToolError(
            "PDF_PARSE_FAILED",
            "The PDF could not be opened by pypdf.",
            details={"path": path.name, "reason": str(exc)},
            recovery="Save or copy the PDF as UTF-8 text and parse that file instead.",
            exit_code=4,
        ) from exc
    if len(pages) > max_pages:
        raise ToolError(
            "PDF_PAGE_LIMIT_EXCEEDED",
            "The PDF exceeds the configured page limit.",
            details={"pages": len(pages), "max_pages": max_pages},
            recovery="Increase limits.max_pdf_pages in a custom tools-config.json or split the PDF.",
        )
    chunks: list[str] = []
    for index, page in enumerate(pages, 1):
        try:
            chunks.append(f"\n<<<PAGE {index}>>>\n{page.extract_text() or ''}")
        except Exception as exc:
            warnings.append(f"Page {index} could not be extracted: {type(exc).__name__}")
    if not any(chunk.strip() for chunk in chunks):
        warnings.append("PDF contained no extractable text; OCR is not included.")
    return "".join(chunks), len(pages)


def _extract_docx(path: Path, max_bytes: int) -> str:
    data = read_bytes(path, max_bytes=max_bytes)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ToolError(
            "DOCX_PARSE_FAILED",
            "The DOCX document.xml could not be read.",
            details={"path": path.name, "reason": str(exc)},
            recovery="Save the document as UTF-8 text, Markdown, or HTML and parse that file.",
            exit_code=4,
        ) from exc
    lines: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        line = "".join(texts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def read_source(
    path_value: str,
    *,
    source_format: str = "auto",
    limits: dict[str, Any] | None = None,
    root: Path | None = None,
) -> SourceDocument:
    limits = limits or {}
    max_bytes = int(limits.get("max_input_bytes", 10_485_760))
    max_pages = int(limits.get("max_pdf_pages", 500))
    path = safe_path(path_value, root=root, must_exist=True)
    warnings: list[str] = []
    head = read_bytes(path, max_bytes=max_bytes)[:4096].decode("utf-8", errors="ignore")
    fmt = _detect_format(path, source_format, head)
    if fmt not in {"text", "markdown", "html", "pdf", "docx"}:
        raise ToolError(
            "UNSUPPORTED_INPUT_FORMAT",
            f"Unsupported input format: {fmt}",
            details={"supported": ["auto", "text", "markdown", "html", "pdf", "docx"]},
        )
    if fmt == "pdf":
        raw, pages = _extract_pdf(path, max_bytes, max_pages, warnings)
    elif fmt == "docx":
        raw, pages = _extract_docx(path, max_bytes), 1
    else:
        raw = load_text(path, max_bytes=max_bytes)
        pages = 1
        if fmt == "html":
            parser = HTMLTextExtractor()
            parser.feed(raw)
            parser.close()
            raw = parser.text()
    normalized = "\n".join(clean_lines(raw))
    if not normalized.strip():
        raise ToolError(
            "EMPTY_SOURCE",
            "The source contained no usable text after normalization.",
            details={"path": path_value, "source_format": fmt},
            recovery="Provide readable text, Markdown, HTML, PDF text, or DOCX content.",
        )
    return SourceDocument(
        path=path_value,
        source_format=fmt,
        raw_text=raw,
        normalized_text=normalized,
        warnings=warnings,
        page_count=pages,
    )


def parse_catalog(
    document: SourceDocument,
    *,
    umbrella_name: str | None = None,
    umbrella_slug: str | None = None,
    limits: dict[str, Any] | None = None,
) -> ParsedCatalog:
    limits = limits or {}
    max_categories = int(limits.get("max_categories", 100))
    max_capabilities = int(limits.get("max_capabilities", 1000))
    max_description_chars = int(limits.get("max_description_chars", 4000))
    lines = document.normalized_text.split("\n")
    title = _title_from(lines)
    categories: list[dict[str, Any]] = []
    category_by_slug: dict[str, dict[str, Any]] = {}
    flat: list[dict[str, Any]] = []
    warnings = list(document.warnings)
    current: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    description_parts: list[str] = []
    table_header: list[str] | None = None

    def add_category(name: str) -> dict[str, Any]:
        slug = slugify(name)
        existing = category_by_slug.get(slug)
        if existing:
            return existing
        if len(categories) >= max_categories:
            raise ToolError(
                "CATEGORY_LIMIT_EXCEEDED",
                "The source exceeds the configured category limit.",
                details={"limit": max_categories},
                recovery="Increase limits.max_categories in a custom tools-config.json.",
            )
        category = {
            "index": len(categories) + 1,
            "name": name,
            "slug": slug,
            "capabilities": [],
        }
        categories.append(category)
        category_by_slug[slug] = category
        return category

    def flush() -> None:
        nonlocal pending, description_parts
        if pending is None:
            return
        description = " ".join(part.strip() for part in description_parts if part.strip())
        description = re.sub(r"\s{2,}", " ", description).strip()
        if len(description) > max_description_chars:
            description = description[:max_description_chars].rstrip() + "…"
            warnings.append(f"Description for {pending['name']} was truncated to {max_description_chars} characters.")
        if not description:
            description = f"Executes the {pending['name']} capability in the unified orchestration runtime."
            warnings.append(f"Description was missing for {pending['name']}; a functional contract was derived from its name.")
        pending["description"] = description
        if len(flat) >= max_capabilities:
            raise ToolError(
                "CAPABILITY_LIMIT_EXCEEDED",
                "The source exceeds the configured capability limit.",
                details={"limit": max_capabilities},
                recovery="Increase limits.max_capabilities in a custom tools-config.json.",
            )
        target = current or add_category("General Capabilities")
        entry = {
            "id": f"{target['slug']}.{pending['slug']}",
            "name": pending["name"],
            "slug": pending["slug"],
            "description": description,
            "category": target["name"],
            "category_slug": target["slug"],
            "category_index": target["index"],
        }
        target["capabilities"].append(entry)
        flat.append(entry)
        pending = None
        description_parts = []

    for line in lines:
        category_name = _is_category_line(line)
        if category_name:
            flush()
            current = add_category(category_name)
            table_header = None
            continue

        skill_start = _skill_start(line)
        if skill_start:
            flush()
            name, description = skill_start
            if current is None:
                current = add_category("General Capabilities")
            pending = {"name": clip(name, 160), "slug": slugify(name)}
            description_parts = [description] if description else []
            table_header = None
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip().lower() for cell in line.strip().strip("|").split("|")]
            if any("skill" in cell or "name" in cell for cell in cells) and any("description" in cell for cell in cells):
                flush()
                table_header = cells
                continue
            row = _table_skill(line, table_header)
            if row:
                flush()
                name, description = row
                if current is None:
                    current = add_category("General Capabilities")
                pending = {"name": clip(name, 160), "slug": slugify(name)}
                description_parts = [description]
                continue

        if pending is not None:
            if _is_guide_line(line) or line.lower() in IGNORED_HEADING_WORDS:
                flush()
                continue
            continuation = re.sub(r"^Description\s*[:\-–]\s*", "", line, flags=re.IGNORECASE)
            description_parts.append(continuation)
        elif line.lower() in {"skill name | description", "skill tool name | description"}:
            table_header = ["skill name", "description"]

    flush()
    if not flat:
        raise ToolError(
            "NO_CAPABILITIES_FOUND",
            "No Skill Tool Names with descriptions were found.",
            details={
                "source_format": document.source_format,
                "expected_patterns": [
                    "Skill Name: X / Description: Y",
                    "Skill Tool Name: X / Description: Y",
                    "Markdown table with Skill Name and Description columns",
                ],
            },
            recovery="Provide a numbered list, category section, Markdown table, or a single named capability with a description.",
        )
    seen: dict[str, str] = {}
    for capability in flat:
        key = capability["id"]
        if key in seen:
            warnings.append(f"Duplicate capability id {key} was retained with its later description.")
        seen[key] = capability["description"]
    if len(seen) != len(flat):
        kept: dict[str, dict[str, Any]] = {}
        for capability in flat:
            existing = kept.get(capability["id"])
            if existing is None or len(capability["description"]) > len(existing["description"]):
                kept[capability["id"]] = capability
        flat = list(kept.values())
        for category in categories:
            ids = {item["id"]: item for item in category["capabilities"]}
            category["capabilities"] = [ids[item["id"]] for item in flat if item["id"] in ids]

    derived_name, derived_slug = _shared_purpose(flat, title)
    if len(categories) == 1 and not umbrella_name:
        derived_name, derived_slug = categories[0]["name"], categories[0]["slug"]
    elif len(categories) > 1 and "catalog" not in title.lower() and "agent multitasking" not in title.lower():
        # A leading section heading is not an umbrella for several categories.
        derived_name, derived_slug = _shared_purpose(flat, "Unified Capability Set")
    final_name = umbrella_name or derived_name
    final_slug = slugify(umbrella_slug or derived_slug)
    data = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "path": document.path,
            "format": document.source_format,
            "title": title,
            "pages": document.page_count,
            "normalized_fingerprint": sha256_text(document.normalized_text),
        },
        "umbrella": {"name": final_name, "slug": final_slug},
        "counts": {"categories": len(categories), "capabilities": len(flat)},
        "categories": categories,
        "capabilities": flat,
        "warnings": unique(warnings),
    }
    return ParsedCatalog(document=document, data=data)


def merge_catalogs(
    catalogs: list[ParsedCatalog],
    *,
    umbrella_name: str | None = None,
    umbrella_slug: str | None = None,
) -> dict[str, Any]:
    if not catalogs:
        raise ToolError(
            "NO_SOURCES",
            "At least one source is required for merge.",
            recovery="Provide one or more --input values.",
        )
    categories: list[dict[str, Any]] = []
    category_by_slug: dict[str, dict[str, Any]] = {}
    capability_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for parsed in catalogs:
        warnings.extend(parsed.data["warnings"])
        for source_category in parsed.data["categories"]:
            slug = source_category["slug"]
            target = category_by_slug.get(slug)
            if target is None:
                target = {
                    "index": len(categories) + 1,
                    "name": source_category["name"],
                    "slug": slug,
                    "capabilities": [],
                }
                categories.append(target)
                category_by_slug[slug] = target
            for capability in source_category["capabilities"]:
                key = capability["id"]
                prior = capability_by_id.get(key)
                if prior and prior["description"] != capability["description"]:
                    warnings.append(f"Merged duplicate {key}; retained the more complete description.")
                if prior is None or len(capability["description"]) > len(prior["description"]):
                    capability_by_id[key] = dict(capability)
    flat = list(capability_by_id.values())
    rebuilt: list[dict[str, Any]] = []
    for index, category in enumerate(categories, 1):
        category["index"] = index
        entries = [dict(item) for item in flat if item["category_slug"] == category["slug"]]
        for entry in entries:
            entry["category"] = category["name"]
            entry["category_index"] = index
        category["capabilities"] = entries
        rebuilt.extend(entries)
    flat = rebuilt
    derived_name, derived_slug = _shared_purpose(flat, "Unified Capability Set")
    if len(categories) == 1 and not umbrella_name:
        derived_name, derived_slug = categories[0]["name"], categories[0]["slug"]
    name = umbrella_name or derived_name
    slug = slugify(umbrella_slug or derived_slug)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "path": "merged",
            "formats": unique([item.data["source"]["format"] for item in catalogs]),
            "fingerprints": [item.data["source"]["normalized_fingerprint"] for item in catalogs],
            "title": name,
        },
        "umbrella": {"name": name, "slug": slug},
        "counts": {"categories": len(categories), "capabilities": len(flat)},
        "categories": categories,
        "capabilities": flat,
        "warnings": unique(warnings),
    }


def catalog_fingerprint(data: dict[str, Any]) -> str:
    return sha256_text(canonical_json({key: data[key] for key in ("umbrella", "categories", "capabilities") if key in data}))
