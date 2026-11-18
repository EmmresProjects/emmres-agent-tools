#!/usr/bin/env python3
"""Input adapter for plain-text, Markdown, and copied-HTML Skill Tool catalogs.

This module is loaded by tools-offline-research.py. It normalizes noisy catalog
text, extracts category names / Skill Tool names / descriptions, and builds one
unified capability specification. It uses only the Python standard library.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any


OPERATION_DESCRIPTIONS: dict[str, str] = {
    "deployment": "Validate offline bundles, record an environment baseline, and emit install, rollback, health, and clean-state checks.",
    "scholarly-corpus": "Validate scholarly corpus metadata, peer-review status, corrections, retractions, and acceptance gates.",
    "reference-corpus": "Validate reference editions, deduplicate records, and produce a general-knowledge corpus acceptance report.",
    "news-archive": "Track news versions, corrections, syndication, event timelines, and archive acceptance.",
    "legal-corpus": "Check jurisdiction, effective dates, repeals, amendments, legal versions, and legal provenance.",
    "standards-library": "Check standards editions, supersession, normative references, requirement clauses, and license audits.",
    "bibliographic": "Parse and validate BibTeX, RIS, CSL JSON, identifiers, conflicts, and citation metadata caches.",
    "retrieval": "Build a local BM25-style index and run bounded Boolean, phrase, field, facet, date, language, and permission-aware search.",
    "semantic-retrieval": "Build deterministic local hashed n-gram vectors, run hybrid/vector search, rerank, calibrate thresholds, and detect drift.",
    "question-answering": "Decompose questions, retrieve evidence, produce extractive cited answers, detect conflicts, and abstain when unsupported.",
    "document-layout": "Process text-layer sidecars, reading order, tables, footnotes, headers, equations, and layout anchors without image OCR.",
    "audio-transcripts": "Validate timestamped local transcripts, speakers, quote windows, and diarization sidecars without audio decoding.",
    "translation": "Apply configurable local translation memory and glossary drafting, preserve placeholders and numbers, and flag human review.",
    "terminology": "Validate, extract, apply, and audit glossaries, do-not-translate terms, and translation memories.",
    "localization-qa": "Check placeholders, numbers, dates, encoding, length, bidi controls, untranslated segments, and locale gates.",
    "knowledge-graph": "Extract entities and relationships with bounded rules and export nodes, edges, and graph quality checks.",
    "ingest": "Read local text, Markdown, copied HTML, JSONL, CSV, BibTeX, RIS, VTT, and SRT inputs into validated normalized source records.",
    "deduplication": "Detect exact and near duplicates, cluster them, and select a canonical record using authority, status, and date.",
    "citations": "Extract inline, footnote, and bibliography citations; align quotes; build anchors; and validate citation completeness.",
    "evidence-review": "Plan reviews, screen records, build evidence tables, synthesize themes, map gaps, and apply inclusion gates.",
    "fact-check": "Extract claims, compare numeric/date facts with local evidence, detect conflicts and retractions, and calibrate uncertainty.",
    "composition": "Create a grounded, deterministic long-form draft from retrieved evidence with section-level citations and coherence checks.",
    "specialized-writing": "Render academic, technical, legal, policy, standards, and grant-style drafts from the same grounded evidence.",
    "editing": "Measure readability, repetition, ambiguity, terminology consistency, and revision diffs without rewriting evidence.",
    "originality": "Detect exact and near passage overlap, quotation boundary issues, citation omissions, and self-similarity.",
    "rights": "Classify licenses, control quotation length, check attribution and redistribution permissions, and build rights evidence.",
    "human-review": "Create pending human-review records, track conflicts and comments, and enforce approval-gated actions.",
    "evaluation": "Run offline fixtures for retrieval, translation, terminology, factuality, transcript, and acceptance thresholds by language.",
    "provenance": "Record checksums, versions, prompts, snapshots, audit events, evidence packages, drift, and reproducible run state.",
    "security": "Classify sensitive data, scan for PII-like and secret-like patterns, prompt injection, restricted isolation, and export gates.",
    "packaging": "Build bounded offline ZIP handoffs, license and checksum inventories, install/self-test notes, backup, and recovery records.",
    "governance": "Generate interface schemas, SOPs, quick starts, troubleshooting trees, training exercises, dependency graphs, and governance reports.",
}

CATEGORY_OPERATIONS: dict[str, str] = {
    "offline installation dependency and corpus deployment": "deployment",
    "scholarly databases and academic corpora": "scholarly-corpus",
    "reference corpora and general knowledge libraries": "reference-corpus",
    "news media and event archives": "news-archive",
    "legal regulatory and policy corpora": "legal-corpus",
    "standards specifications and technical libraries": "standards-library",
    "citation indexes bibliographic metadata and identifier caches": "bibliographic",
    "full text search querying and retrieval": "retrieval",
    "embeddings vector search and reranking": "semantic-retrieval",
    "local question answering and research agents": "question-answering",
    "ocr handwriting tables and document layout": "document-layout",
    "speech to text interviews and audio sources": "audio-transcripts",
    "translation models and multilingual language systems": "translation",
    "terminology glossaries and translation memory": "terminology",
    "localization and language quality assurance": "localization-qa",
    "named entities relationships and knowledge graphs": "knowledge-graph",
    "source ingest parsing and normalization": "ingest",
    "source deduplication clustering and canonicalization": "deduplication",
    "citation extraction anchoring and validation": "citations",
    "literature review and evidence synthesis": "evidence-review",
    "fact checking verification and freshness": "fact-check",
    "research writing and long form composition": "composition",
    "academic technical legal and policy writing": "specialized-writing",
    "editing revision style and readability": "editing",
    "plagiarism similarity and originality controls": "originality",
    "copyright quotation and rights management": "rights",
    "human review approval and editorial workflows": "human-review",
    "accuracy benchmarks and evaluation by language": "evaluation",
    "provenance versioning audit and reproducibility": "provenance",
    "security privacy and restricted corpus handling": "security",
    "packaging backup archival and handoff": "packaging",
    "skill integration documentation training and governance": "governance",
}


class _TextExtractor(HTMLParser):
    """Drop tags, comments, scripts, styles, and all attributes from copied HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def slugify(value: str) -> str:
    value = html.unescape(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "capability"


def category_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def strip_markup(text: str) -> str:
    """Remove HTML tags, CSS, data/class/style attributes, and extra spacing."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"<\s*[a-zA-Z!/][^>]*>", text):
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
            text = parser.text()
        except Exception:
            # A malformed copied fragment is still usable after conservative tag removal.
            text = re.sub(r"(?is)<script.*?</script>", " ", text)
            text = re.sub(r"(?is)<style.*?</style>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"(?is)\{[^{}]*\}", " ", text)  # stray CSS rule blocks
    text = re.sub(
        r"(?i)\b(?:class|style|data-[a-z0-9_-]+)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
        " ",
        text,
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"-\n(?=[A-Za-z])", "-", text)  # PDF line-break hyphenation
    return text


def normalize_source(text: str) -> str:
    """Normalize PDF/page copy, Markdown, numbering marks, and whitespace."""
    text = strip_markup(text)
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.replace("\u00a0", " ").replace("\u2022", "- ")
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            lines.append("")
            continue
        if re.fullmatch(r"=+\s*PAGE\s+\d+\s*=+", line, flags=re.I):
            continue
        if re.match(r"^FULL OFFLINE RESEARCH, WRITING, AND TRANSLATION\s*\|\s*SKILL CATALOG$", line, flags=re.I):
            continue
        if re.fullmatch(r"\d{1,3}", line):
            continue
        # Markdown headings become explicit category markers.
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            line = "CATEGORY: " + heading.group(1).strip()
        line = line.replace("**", "").replace("__", "").replace("`", "")
        # Keep numbering marks for list grammar; every extractor ignores them
        # when producing the real category, Skill Tool name, or description.
        line = re.sub(r"^\s*[-*]\s+", "- ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        lines.append(line)
    # Remove repeated blank lines while preserving entry boundaries.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_NUMBER_PREFIX = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*")
_CATEGORY_EXPLICIT = re.compile(r"^(?:category|category name|umbrella|umbrella name)\s*:\s*(.+)$", re.I)
_CATEGORY_NUMBERED = re.compile(r"^\d+[.)]\s+(?P<title>[A-Z][A-Za-z0-9 ,&/’'-]{8,})$")
_SKILL_NAMED = re.compile(
    r"^(?:\d+[.)]\s+|[-*]\s+)?(?:skill(?:\s+tool)?\s+name|tool\s+name|capability)\s*:\s*(.+)$",
    re.I,
)
_DESCRIPTION = re.compile(r"^description\s*:\s*(.*)$", re.I)
_INLINE_SKILL = re.compile(
    r"^(?:\d+[.)]\s+|[-*]\s+)?(?P<name>[^:]{3,120}?)\s*(?:[-–—:]\s*)?description\s*:\s*(?P<description>.+)$",
    re.I,
)
_BOILERPLATE = re.compile(
    r"^(?:purpose and scope|design principles for future skill builders|catalog summary|"
    r"recommended catalog-to-skill package|final acceptance standard|catalog edition|"
    r"comprehensive skill catalog|full offline research, writing, and translation|"
    r"a capability blueprint.*|this document is a planning catalog.*|"
    r"catalog includes .*|a future implementation should be considered complete.*)$",
    re.I,
)
_SECTION_STOP = re.compile(r"^(?:[-*]\s+)?(?:SKILL\.md|agents/|scripts/|references/|assets/|When converting an entry.*|Final Acceptance Standard)", re.I)


def infer_operation(category: str) -> str:
    key = category_key(category)
    if key in CATEGORY_OPERATIONS:
        return CATEGORY_OPERATIONS[key]
    rules = [
        (("install", "dependenc", "deployment", "clean-state"), "deployment"),
        (("scholarly", "academic", "journal", "thesis", "preprint"), "scholarly-corpus"),
        (("reference", "encyclopedia", "dictionary", "general knowledge"), "reference-corpus"),
        (("news", "media", "event", "broadcast", "press"), "news-archive"),
        (("legal", "regulatory", "policy", "case law", "statute"), "legal-corpus"),
        (("standard", "specification", "technical librar"), "standards-library"),
        (("citation index", "bibliographic", "identifier", "metadata cache"), "bibliographic"),
        (("full-text", "query", "retrieval"), "retrieval"),
        (("embedding", "vector", "rerank"), "semantic-retrieval"),
        (("question answering", "research agent"), "question-answering"),
        (("ocr", "handwriting", "table", "layout", "document image"), "document-layout"),
        (("speech", "audio", "interview", "transcript"), "audio-transcripts"),
        (("translation model", "multilingual language"), "translation"),
        (("terminology", "glossar", "translation memory"), "terminology"),
        (("localization", "language quality"), "localization-qa"),
        (("named entit", "relationship", "knowledge graph"), "knowledge-graph"),
        (("ingest", "parsing", "normalization"), "ingest"),
        (("deduplication", "clustering", "canonicalization"), "deduplication"),
        (("citation extraction", "anchoring", "citation validation"), "citations"),
        (("literature review", "evidence synthesis"), "evidence-review"),
        (("fact check", "verification", "freshness"), "fact-check"),
        (("research writing", "long-form", "composition"), "composition"),
        (("academic", "technical", "legal", "policy writing", "specialized writing"), "specialized-writing"),
        (("editing", "revision", "style", "readability"), "editing"),
        (("plagiarism", "similarity", "originality"), "originality"),
        (("copyright", "quotation", "rights"), "rights"),
        (("human review", "approval", "editorial"), "human-review"),
        (("benchmark", "evaluation", "accuracy"), "evaluation"),
        (("provenance", "versioning", "audit", "reproducibility"), "provenance"),
        (("security", "privacy", "restricted"), "security"),
        (("packaging", "backup", "archival", "handoff"), "packaging"),
        (("integration", "documentation", "training", "governance"), "governance"),
    ]
    for needles, operation in rules:
        if any(needle in key for needle in needles):
            return operation
    return "governance"


def derive_umbrella(records: list[dict[str, Any]], explicit: str | None = None) -> str:
    if explicit:
        return slugify(explicit)
    categories = list(dict.fromkeys(r.get("category", "") for r in records if r.get("category")))
    if len(categories) == 1 and categories[0]:
        return slugify(categories[0])
    blob = " ".join(
        [r.get("name", "") for r in records] + [r.get("description", "") for r in records]
    ).lower()
    priority = [p for p in ("offline", "research", "writing", "translation") if p in blob]
    if len(priority) >= 3:
        return "-".join(priority)
    stop = {
        "the", "and", "for", "with", "that", "should", "complete", "workflow", "skill", "skills",
        "declare", "exact", "sources", "source", "corpus", "model", "versions", "permissions",
        "language", "jurisdiction", "scope", "inputs", "outputs", "confidence", "failure",
        "conditions", "citations", "provenance", "copyright", "controls", "human", "review",
        "points", "clean", "state", "checks", "appropriate", "designs", "executes", "validates",
        "offline", "research", "writing", "translation",
    }
    counts: dict[str, int] = {}
    order: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9-]{3,}", blob):
        if token in stop or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
        if token not in order:
            order.append(token)
    chosen = [w for w in order if counts[w] > 1][:4]
    if not chosen:
        chosen = [slugify(records[0].get("name", "unified-capability"))] if records else ["unified-capability"]
    return slugify("-".join(chosen)) or "unified-capability"


def _strip_marker(line: str) -> str:
    return _NUMBER_PREFIX.sub("", line).strip()


def _is_numbered_category(line: str, following: list[str]) -> bool:
    """A numbered non-'Skill Name' title is a category when it heads skill entries."""
    match = _CATEGORY_NUMBERED.match(line)
    if not match:
        return False
    if _SKILL_NAMED.match(line) or _DESCRIPTION.match(line) or _INLINE_SKILL.match(line):
        return False
    nonblank = [x for x in following if x]
    nxt = nonblank[0] if nonblank else ""
    if _SKILL_NAMED.match(nxt) or _DESCRIPTION.match(nxt):
        return True
    nxt2 = nonblank[1] if len(nonblank) > 1 else ""
    # A numbered list of Skill Tool names often has another numbered item next.
    if re.match(r"^\d+[.)]\s+", nxt) and not _SKILL_NAMED.match(nxt) and not _DESCRIPTION.match(nxt):
        return False
    title = match.group("title")
    return bool(
        re.search(
            r"\b(and|libraries|corpora|deployment|retrieval|systems|graphs|controls|workflows|handling|handoff|governance|archives|indexes|writing|composition|synthesis|freshness|quality|management|reproducibility)\b",
            title,
            re.I,
        )
    )


def parse_catalog(text: str, umbrella_name: str | None = None, category_hint: str | None = None) -> dict[str, Any]:
    """Return a normalized catalog with one unified umbrella name.

    Only explicit category markers, Skill Name labels, Description labels, and
    numbered list items create records. Introductory prose, section headings,
    bullets, page furniture, and acceptance-standard prose are ignored.
    """
    normalized = normalize_source(text)
    lines = [x.strip() for x in normalized.split("\n")]
    records: list[dict[str, Any]] = []
    current_category = category_hint or ""
    current_name: str | None = None
    current_description: list[str] = []
    category_order: list[str] = []
    suppress_until_entry = False

    def flush() -> None:
        nonlocal current_name, current_description
        if current_name:
            description = re.sub(r"\s+", " ", " ".join(current_description).strip())
            records.append(
                {
                    "name": re.sub(r"\s+", " ", _strip_marker(current_name)).strip(" -:"),
                    "category": current_category,
                    "description": description,
                }
            )
        current_name = None
        current_description = []

    for index, line in enumerate(lines):
        if not line:
            continue
        explicit = _CATEGORY_EXPLICIT.match(line)
        if explicit:
            flush()
            suppress_until_entry = False
            current_category = _strip_marker(explicit.group(1).strip())
            if current_category not in category_order:
                category_order.append(current_category)
            continue
        skill = _SKILL_NAMED.match(line)
        if skill:
            flush()
            suppress_until_entry = False
            current_name = _strip_marker(skill.group(1).strip())
            current_description = []
            continue
        desc = _DESCRIPTION.match(line)
        if desc:
            if current_name is None:
                current_name = "Unnamed capability"
            suppress_until_entry = False
            current_description = [desc.group(1).strip()] if desc.group(1).strip() else []
            continue
        inline = _INLINE_SKILL.match(line)
        if inline and not _is_numbered_category(line, lines[index + 1 : index + 5]):
            flush()
            suppress_until_entry = False
            current_name = _strip_marker(inline.group("name").strip(" -:"))
            current_description = [inline.group("description").strip()]
            continue
        if _BOILERPLATE.match(line) or _SECTION_STOP.match(line):
            flush()
            suppress_until_entry = True
            continue
        if _is_numbered_category(line, lines[index + 1 : index + 5]):
            flush()
            suppress_until_entry = False
            current_category = _strip_marker(line)
            if current_category not in category_order:
                category_order.append(current_category)
            continue
        if suppress_until_entry:
            continue
        if current_name is not None:
            # Wrapped Description text, or a continuation before a Description label.
            current_description.append(line)
            continue
        # A plain numbered list item with no explicit label is a capability name.
        if _NUMBER_PREFIX.match(line) and len(line) <= 140 and not line.endswith(":") and "." not in _strip_marker(line)[-3:]:
            current_name = _strip_marker(line)
            current_description = []
    flush()
    if not records:
        raise ValueError("No Skill Tool names or descriptions were found in the supplied source.")
    umbrella = derive_umbrella(records, umbrella_name)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", umbrella):
        raise ValueError("The derived Skill Tool name is not a lowercase hyphenated name.")
    return {
        "schema_version": "1.0",
        "skill_tool_name": umbrella,
        "category_count": len(category_order),
        "categories": category_order,
        "capability_count": len(records),
        "source_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "capabilities": records,
    }


def build_unified_spec(
    parsed: dict[str, Any],
    umbrella_name: str | None = None,
    source_label: str = "supplied catalog",
    edition: str | None = None,
) -> dict[str, Any]:
    """Attach exact-name dispatch metadata to parsed capabilities."""
    name = slugify(umbrella_name) if umbrella_name else parsed["skill_tool_name"]
    categories = parsed.get("categories", [])
    cat_index = {c: i + 1 for i, c in enumerate(categories)}
    capabilities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, record in enumerate(parsed["capabilities"], 1):
        cat = record.get("category") or "Uncategorized"
        slug = slugify(record["name"])
        base = slug
        n = 2
        while slug in seen:
            slug = f"{base}-{n}"
            n += 1
        seen.add(slug)
        cid = f"cat-{cat_index.get(cat, 0):02d}-skill-{i:03d}"
        operation = infer_operation(cat)
        description = record.get("description") or "No description was supplied."
        capabilities.append(
            {
                "capability_id": cid,
                "name": record["name"],
                "slug": slug,
                "category": cat,
                "category_slug": slugify(cat) if cat != "Uncategorized" else "uncategorized",
                "description": description,
                "operation": operation,
                "operation_description": OPERATION_DESCRIPTIONS[operation],
                "mode": slug,
                "responsibility": f"{record['name']}: {description}",
                "declared_fields": [
                    "exact_sources",
                    "corpus_and_model_versions",
                    "permissions",
                    "language_and_jurisdiction_scope",
                    "inputs",
                    "outputs",
                    "confidence",
                    "failure_conditions",
                    "citations",
                    "provenance",
                    "copyright_controls",
                    "human_review_points",
                    "clean_state_checks",
                ],
            }
        )
    operations = sorted({c["operation"] for c in capabilities})
    return {
        "schema_version": "1.0",
        "tool": name,
        "source": source_label,
        "edition": edition,
        "umbrella_name": name,
        "unification": "All related listed capabilities are combined into one Skill Tool folder.",
        "category_count": len(categories),
        "categories": categories,
        "capability_count": len(capabilities),
        "operation_count": len(operations),
        "operations": [
            {"name": op, "description": OPERATION_DESCRIPTIONS[op]} for op in operations
        ],
        "capabilities": capabilities,
        "unavailable_integrations": [
            "image OCR model execution",
            "handwriting recognition model execution",
            "audio decoding and ASR model execution",
            "neural machine translation model execution",
            "local LLM generation and cross-encoder model execution",
        ],
    }


def parse_and_build(
    text: str,
    umbrella_name: str | None = None,
    category_hint: str | None = None,
    source_label: str = "supplied catalog",
    edition: str | None = None,
) -> dict[str, Any]:
    parsed = parse_catalog(text, umbrella_name=umbrella_name, category_hint=category_hint)
    return build_unified_spec(parsed, umbrella_name, source_label=source_label, edition=edition)


def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


__all__ = [
    "OPERATION_DESCRIPTIONS",
    "CATEGORY_OPERATIONS",
    "slugify",
    "normalize_source",
    "strip_markup",
    "infer_operation",
    "derive_umbrella",
    "parse_catalog",
    "build_unified_spec",
    "parse_and_build",
    "write_json",
]
