"""Capability registry for the accessibility-localization Skill Tool.

Loads the 220-entry catalog asset and derives input/output contracts,
standards, procedures, pass/fail criteria, and examples for each capability.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools_core import ToolError

REGISTRY_NAME = "asset-capability-registry.json"
CATEGORY_ORDER = [
    "governance", "automated-wcag", "screen-reader-at", "keyboard-input", "visual",
    "forms-critical-journeys", "documents", "images-charts", "media", "platforms",
    "cognitive", "i18n-architecture", "terminology-language-quality", "filipino-english",
    "rtl-scripts-fonts", "pseudo-functional-qa", "research-human-validation",
    "procurement-operations",
]

INPUT_KIND = {
    "static_audit": "html", "contrast_audit": "html", "media_audit": "html",
    "font_coverage": "html", "document_audit": "document", "i18n_audit": "source",
    "pseudo_generate": "locale", "locale_format": "json", "termbase_audit": "csv",
    "tm_match": "csv", "review_workbench": "review", "readability": "text",
    "caption_qa": "captions", "audio_description": "csv", "loudness": "audio",
    "data_alternative": "data", "governance_artifact": "brief", "plan_artifact": "brief",
    "risk_register": "findings", "release_gate": "findings", "maturity_assess": "json",
    "crosswalk": "brief", "wcag_map": "brief", "kpi_design": "brief",
    "exception_review": "request", "evidence_repo": "evidence", "defect_triage": "defects",
    "remediation_verify": "retest", "procurement_review": "vendor", "ci_generate": "config",
    "traceability": "findings",
}
OPTIONAL_INPUT = {
    "governance_artifact", "plan_artifact", "crosswalk", "wcag_map", "kpi_design",
    "ci_generate", "maturity_assess", "evidence_repo", "locale_format",
}
OUTPUT_KIND = {
    "pseudo_generate": "json+md+locale-artifact", "ci_generate": "json+md+yaml",
    "data_alternative": "json+md+description-artifact", "document_audit": "json+md",
}
CATEGORY_STANDARDS = {
    "governance": ["WCAG 2.2", "EN 301 549", "Section 508", "ISO 30071-1"],
    "automated-wcag": ["WCAG 2.2 A/AA"],
    "screen-reader-at": ["WCAG 2.2", "NVDA/JAWS/VoiceOver/TalkBack/Narrator guidance"],
    "keyboard-input": ["WCAG 2.2 2.1, 2.4, 2.5"],
    "visual": ["WCAG 2.2 1.4, 2.3, 2.4"],
    "forms-critical-journeys": ["WCAG 2.2 2.5, 3.3, 4.1"],
    "documents": ["PDF/UA-1", "WCAG 2.2", "EPUB 3", "ISO 14289"],
    "images-charts": ["WCAG 2.2 1.1, 1.4"],
    "media": ["WCAG 2.2 1.2, 1.4, 2.2"],
    "platforms": ["WCAG 2.2", "Platform accessibility APIs"],
    "cognitive": ["WCAG 2.2 3.1, 3.2, 3.3", "COGA guidance"],
    "i18n-architecture": ["ISO 17100", "ASTM F2575", "WCAG 2.2 3.1", "Unicode"],
    "terminology-language-quality": ["ISO 17100", "ASTM F2575", "ISO 704"],
    "filipino-english": ["ISO 17100", "WCAG 2.2 3.1", "KWF/UP dictionary references"],
    "rtl-scripts-fonts": ["Unicode", "WCAG 2.2 1.3, 1.4"],
    "pseudo-functional-qa": ["WCAG 2.2 1.4", "ISO 17100"],
    "research-human-validation": ["WCAG 2.2", "ISO 30071-1", "ISO 17100"],
    "procurement-operations": ["EN 301 549", "Section 508", "WCAG 2.2", "ISO 30071-1"],
}
OPERATION_PROCEDURE = {
    "static_audit": [
        "Discover HTML, CSS, and client-side source files within the configured limits.",
        "Parse markup and style declarations without executing scripts or making network requests.",
        "Run the capability's selected checks and normalize findings with locations and WCAG references.",
        "Write the JSON envelope and Markdown report; list every limitation and manual check still required.",
    ],
    "contrast_audit": [
        "Extract foreground/background color pairs from CSS declarations and inline styles.",
        "Compute WCAG 2.x contrast ratios and classify text as normal or large.",
        "Report failing pairs with the computed ratio, threshold, and a suggested compliant color.",
    ],
    "document_audit": [
        "Identify the document format from the file signature and extension.",
        "Inspect document structure, language, metadata, headings, tables, links, and reading order.",
        "Report findings with locations and remediation; never claim full PDF/UA validation without a validator.",
    ],
    "media_audit": [
        "Parse HTML media elements, images, SVG icons, figures, and MathML.",
        "Check alternatives, captions, transcripts, descriptions, and text equivalents.",
        "Report findings and generate a review workbench for items that need human judgment.",
    ],
    "i18n_audit": [
        "Discover locale resources and source files within the configured limits.",
        "Extract strings, placeholders, keys, and direction markers without executing code.",
        "Validate parity, placeholders, Unicode safety, and key governance; report locale-specific findings.",
    ],
    "pseudo_generate": [
        "Read source strings from a JSON or properties resource.",
        "Apply a deterministic pseudo-locale transformation that preserves placeholders.",
        "Write the pseudo-localized resource and a verification report; never overwrite source files.",
    ],
    "locale_format": [
        "Read the supplied values or use the configured locale defaults.",
        "Format or validate dates, times, numbers, currency, addresses, names, and phone numbers.",
        "Report ambiguous or invalid values with a corrected suggestion.",
    ],
    "termbase_audit": [
        "Read the termbase CSV and validate required columns and language codes.",
        "Check duplicates, forbidden variants, inconsistent translations, and missing definitions.",
        "Report a structured termbase quality result and a cleaned candidate CSV when requested.",
    ],
    "tm_match": [
        "Read translation memory and source segments.",
        "Score fuzzy matches with a deterministic token/character similarity method.",
        "Report the best matches, scores, and exact/placeholder mismatches for translator review.",
    ],
    "review_workbench": [
        "Read the supplied review data or prepare an empty review workbench.",
        "Apply the selected review template and severity model.",
        "Compute a scorecard and write a reviewer-ready CSV/Markdown workbench.",
    ],
    "readability": [
        "Read the supplied text or the configured brief.",
        "Compute sentence, word, and syllable metrics and flag difficult constructions.",
        "Produce a readability profile and, when requested, a rule-based plain-language rewrite for human review.",
    ],
    "font_coverage": [
        "Inspect the supplied HTML/CSS or text for language and script usage.",
        "Check font-family declarations against Unicode script ranges and required characters.",
        "Report coverage risks and fallback recommendations; do not claim binary font coverage without font inspection.",
    ],
    "caption_qa": [
        "Parse WebVTT or SRT cues and validate timing, overlap, reading speed, and line length.",
        "Check speaker labels, sound cues, punctuation, and completeness.",
        "Report cue-level findings and a corrected caption file when the mode supports it.",
    ],
    "audio_description": [
        "Read the media duration and dialogue/gap data or the supplied script.",
        "Validate description timing, reading speed, and overlap with dialogue.",
        "Report a description plan or timing findings for human review.",
    ],
    "loudness": [
        "Read a WAV file with the standard-library wave module.",
        "Compute integrated RMS, peak, and approximate loudness; report clipping and dynamic range.",
        "State that compressed formats and perceptual loudness require an external tool such as ffmpeg.",
    ],
    "data_alternative": [
        "Read the supplied chart, map, or diagram data.",
        "Compute summary statistics and generate a text alternative or data table.",
        "Report the generated artifact and the human review required for context and accuracy.",
    ],
    "governance_artifact": [
        "Read the optional brief and merge it with the configured defaults.",
        "Generate the selected governance artifact with scope, roles, requirements, and review dates.",
        "Write Markdown and JSON outputs; route decisions to the named accountable owner.",
    ],
    "plan_artifact": [
        "Read the optional brief and merge it with the configured defaults.",
        "Generate the selected test or research protocol with objective, roles, steps, evidence, and pass/fail criteria.",
        "Write Markdown and JSON outputs; flag every step that requires a human specialist.",
    ],
    "risk_register": [
        "Read findings or a risk CSV and normalize severity, reach, and legal exposure.",
        "Compute a risk score and suggested due date; assign an owner placeholder.",
        "Write a risk register CSV and a JSON summary.",
    ],
    "release_gate": [
        "Read a findings JSON file from a previous run.",
        "Apply the configured release thresholds and blocking rules.",
        "Return an allow/block decision with the exact blocking findings and required exceptions.",
    ],
    "maturity_assess": [
        "Read the supplied evidence or use the configured baseline.",
        "Score eight governance dimensions against a 1-5 maturity rubric.",
        "Produce a staged roadmap with quick wins and dependencies.",
    ],
    "crosswalk": [
        "Load the bundled WCAG 2.2 and standards crosswalk assets.",
        "Filter by the requested scope and map each criterion to related clauses.",
        "Report the crosswalk with a verification warning; exact clause text must be checked against current editions.",
    ],
    "wcag_map": [
        "Read the supplied journey or feature list.",
        "Map each item to applicable WCAG 2.2 A/AA criteria using the bundled criteria asset.",
        "Report coverage gaps, evidence needs, and accountable owner placeholders.",
    ],
    "kpi_design": [
        "Read the optional brief and merge it with the configured outcome set.",
        "Generate outcome-based KPIs and OKRs with baselines, targets, and measurement methods.",
        "Reject vanity metrics and report the measurement plan.",
    ],
    "exception_review": [
        "Read the exception request and validate required fields.",
        "Check scope, duration, equivalent access, risk, and expiry.",
        "Return an approve/reject/revise recommendation with mandatory conditions; a human owner must decide.",
    ],
    "evidence_repo": [
        "Scan the supplied evidence directory or output directory.",
        "Inventory artifacts by type, capability, and date without reading secrets.",
        "Write an evidence manifest and a gap report.",
    ],
    "defect_triage": [
        "Read defects from CSV or JSON.",
        "Score user blockage, reach, journey importance, workaround quality, and release risk.",
        "Write a prioritized defect queue with rationale.",
    ],
    "remediation_verify": [
        "Read the original findings and the retest evidence.",
        "Match defects by ID and verify that the exact issue, boundaries, and regressions are covered.",
        "Return verified/unverified/reopened decisions with missing evidence.",
    ],
    "procurement_review": [
        "Read the vendor or VPAT/ACR document and extract claims and evidence.",
        "Check scope, conformance levels, exceptions, testing credibility, and remediation terms.",
        "Return a review with unsupported claims and required follow-up questions.",
    ],
    "ci_generate": [
        "Read the optional pipeline configuration and merge it with the configured checks.",
        "Generate a CI configuration that runs the local Skill Tool and publishes artifacts.",
        "Write YAML and JSON outputs; do not include secrets or paid services.",
    ],
    "traceability": [
        "Read findings and requirement evidence.",
        "Link each finding to a requirement, owner, and verification method.",
        "Write a traceability matrix and report orphan findings or requirements.",
    ],
}
OPERATION_LIMITATIONS = {
    "static_audit": [
        "Static analysis cannot observe runtime behavior, focus after interaction, or actual screen-reader speech.",
        "CSS cascade and browser-specific rendering are approximated; confirm visual issues in a browser.",
        "No JavaScript is executed and no network requests are made.",
    ],
    "contrast_audit": [
        "Only colors that can be extracted from CSS/inline styles are checked; images, gradients, and overlays need manual review.",
        "Opacity, blend modes, and background images are not fully resolved.",
    ],
    "document_audit": [
        "PDF inspection is a structural metadata check, not a replacement for PDF/UA validation or a screen-reader test.",
        "OOXML checks inspect XML parts; they do not render the document.",
    ],
    "media_audit": [
        "Alt-text quality and caption accuracy require human review.",
        "No OCR, speech recognition, or audio decoding is performed.",
    ],
    "i18n_audit": [
        "Extraction is heuristic and may miss dynamically constructed strings.",
        "Linguistic quality and cultural fit require human review.",
    ],
    "pseudo_generate": [
        "Pseudo-localization tests layout resilience; it does not validate translation quality.",
    ],
    "locale_format": [
        "Formatting rules are configurable defaults; verify official Philippine and target-locale conventions.",
    ],
    "termbase_audit": [
        "Termbase quality checks are structural; subject-matter approval is still required.",
    ],
    "tm_match": [
        "Fuzzy scores are deterministic heuristics, not a substitute for translator judgment.",
    ],
    "review_workbench": [
        "The workbench organizes review; it does not replace qualified bilingual or disabled-user review.",
    ],
    "readability": [
        "Readability formulas are proxies; they do not measure comprehension.",
        "Filipino readability is heuristic and requires a Filipino language specialist.",
    ],
    "font_coverage": [
        "The tool checks declared font stacks and script ranges, not binary glyph coverage.",
        "Shaping and rendering require a browser or typesetting engine.",
    ],
    "caption_qa": [
        "Timing and text checks do not verify caption accuracy against audio.",
    ],
    "audio_description": [
        "Description quality and cultural fit require human review.",
    ],
    "loudness": [
        "Only uncompressed WAV audio is analyzed; perceptual loudness and compressed formats require ffmpeg or an equivalent tool.",
    ],
    "data_alternative": [
        "Generated descriptions are drafts; a domain expert must verify accuracy and context.",
    ],
    "governance_artifact": [
        "Generated policies and plans are drafts; legal, procurement, and accessibility owners must approve them.",
    ],
    "plan_artifact": [
        "Generated protocols are drafts; they do not replace human research or AT testing.",
    ],
    "risk_register": [
        "Risk scoring is a structured heuristic; legal exposure requires legal review.",
    ],
    "release_gate": [
        "The gate only evaluates supplied evidence; it cannot discover unknown barriers.",
    ],
    "maturity_assess": [
        "Maturity scores depend on the quality of supplied evidence.",
    ],
    "crosswalk": [
        "Clause families are a planning aid; verify exact clause text against current published standards.",
    ],
    "wcag_map": [
        "Mapping is a planning aid; criterion applicability depends on the actual product and jurisdiction.",
    ],
    "kpi_design": [
        "Targets must be validated against organizational baselines.",
    ],
    "exception_review": [
        "The tool recommends; an accountable human owner must approve exceptions.",
    ],
    "evidence_repo": [
        "The manifest does not verify the authenticity or completeness of evidence.",
    ],
    "defect_triage": [
        "Prioritization is a decision aid; product and accessibility owners remain accountable.",
    ],
    "remediation_verify": [
        "Verification depends on the quality of supplied retest evidence.",
    ],
    "procurement_review": [
        "The review is not a legal opinion or a substitute for vendor testing.",
    ],
    "ci_generate": [
        "The generated pipeline must be reviewed before deployment; no secrets are included.",
    ],
    "traceability": [
        "Traceability depends on complete requirement and finding data.",
    ],
}
OPERATION_EVIDENCE = {
    "static_audit": ["HTML/CSS source snapshot", "JSON findings", "Markdown report", "File hashes"],
    "contrast_audit": ["Computed color pairs", "Contrast ratios", "CSS excerpts"],
    "document_audit": ["Document metadata", "Structure findings", "File hash"],
    "media_audit": ["Media inventory", "Alternative text findings", "Review workbench"],
    "i18n_audit": ["Locale resource inventory", "String/placeholder findings", "Parity report"],
    "pseudo_generate": ["Pseudo-localized resource", "Placeholder preservation check"],
    "locale_format": ["Formatted values", "Validation findings"],
    "termbase_audit": ["Termbase validation report", "Cleaned candidate CSV"],
    "tm_match": ["Match scores", "Segment alignment"],
    "review_workbench": ["Review workbench CSV", "Scorecard", "Reviewer notes"],
    "readability": ["Readability metrics", "Flagged passages", "Rewrite draft"],
    "font_coverage": ["Font stack inventory", "Script coverage findings"],
    "caption_qa": ["Cue-level findings", "Corrected caption file"],
    "audio_description": ["Description plan", "Timing validation"],
    "loudness": ["WAV measurements", "Clipping report"],
    "data_alternative": ["Generated text alternative", "Data table"],
    "governance_artifact": ["Approved artifact", "Owner sign-off", "Review date"],
    "plan_artifact": ["Protocol", "Session recordings/notes (with consent)", "Evidence checklist"],
    "risk_register": ["Risk register CSV", "Scoring rationale"],
    "release_gate": ["Gate decision", "Blocking findings", "Exception records"],
    "maturity_assess": ["Maturity scores", "Evidence references", "Roadmap"],
    "crosswalk": ["Crosswalk table", "Source editions"],
    "wcag_map": ["Journey-to-criterion map", "Evidence gaps"],
    "kpi_design": ["KPI/OKR set", "Measurement plan"],
    "exception_review": ["Exception decision", "Conditions", "Expiry date"],
    "evidence_repo": ["Evidence manifest", "Gap report"],
    "defect_triage": ["Prioritized queue", "Scoring rationale"],
    "remediation_verify": ["Verification decision", "Retest evidence"],
    "procurement_review": ["Vendor review", "Claim verification", "Follow-up questions"],
    "ci_generate": ["CI YAML", "Check configuration"],
    "traceability": ["Traceability matrix", "Orphan report"],
}


def _derive(cap: dict[str, Any]) -> dict[str, Any]:
    op = cap["operation"]
    params = cap.get("params", {})
    checks = cap.get("checks", [])
    input_kind = INPUT_KIND.get(op, "brief")
    required = op not in OPTIONAL_INPUT
    standards = list(CATEGORY_STANDARDS.get(cap["category"], ["WCAG 2.2"]))
    if params.get("locale") == "fil-PH":
        standards = list(dict.fromkeys(standards + ["ISO 17100", "WCAG 2.2 3.1.2"]))
    if op in ("static_audit", "contrast_audit"):
        standards = list(dict.fromkeys(standards + ["WCAG 2.2 A/AA"]))
    if op == "document_audit":
        standards = list(dict.fromkeys(standards + [f"format:{params.get('format', 'auto')}"]))
    cap = dict(cap)
    cap.update({
        "input_kind": input_kind,
        "input_required": required,
        "output_kind": OUTPUT_KIND.get(op, "json+md"),
        "standards": standards,
        "procedure": OPERATION_PROCEDURE.get(op, []),
        "limitations": OPERATION_LIMITATIONS.get(op, []),
        "evidence": OPERATION_EVIDENCE.get(op, []),
        "human_review": (
            "No human review is required for the automated result; review findings before acting."
            if cap["status"] == "auto" else
            "A qualified human specialist must review the generated artifact and record decisions."
        ),
        "example": example_command(cap),
    })
    return cap


def example_command(cap: dict[str, Any]) -> str:
    op = cap["operation"]
    params = cap.get("params", {})
    if op in ("static_audit", "contrast_audit", "media_audit", "font_coverage"):
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input page.html --output output/{cap['id']}"
    if op == "document_audit":
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input document.pdf --output output/{cap['id']}"
    if op in ("i18n_audit", "pseudo_generate"):
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input locale.json --output output/{cap['id']}"
    if op in ("termbase_audit", "tm_match"):
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input termbase.csv --output output/{cap['id']}"
    if op == "review_workbench":
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input review.csv --output output/{cap['id']}"
    if op == "caption_qa":
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input captions.vtt --output output/{cap['id']}"
    if op == "readability":
        return f"python3 tools-accessibility-localization.py run {cap['id']} --input content.txt --output output/{cap['id']}"
    if op in ("governance_artifact", "plan_artifact", "crosswalk", "wcag_map", "kpi_design", "ci_generate"):
        return f"python3 tools-accessibility-localization.py run {cap['id']} --brief \"{cap['name']}\" --output output/{cap['id']}"
    return f"python3 tools-accessibility-localization.py run {cap['id']} --input input.json --output output/{cap['id']}"


def load_registry(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise ToolError("registry-missing", f"Capability registry not found: {p}",
                        "Keep asset-capability-registry.json beside the tool files.", str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError("registry-invalid", f"Capability registry is not valid JSON: {exc}",
                        "Restore the registry asset or regenerate it from the catalog.", str(p))
    if not isinstance(data, list) or not data:
        raise ToolError("registry-invalid", "Capability registry must be a non-empty JSON array.",
                        "Restore the registry asset.", str(p))
    required = {"id", "name", "description", "category", "category_title", "operation", "status"}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ToolError("registry-invalid", f"Registry entry {i} is not an object.",
                            "Restore the registry asset.", str(p))
        missing = required - set(item)
        if missing:
            raise ToolError("registry-invalid", f"Registry entry {i} is missing fields: {sorted(missing)}",
                            "Restore the registry asset.", str(p))
        if item["id"] in seen:
            raise ToolError("registry-invalid", f"Duplicate capability id: {item['id']}",
                            "Restore the registry asset.", str(p))
        if item["status"] not in ("auto", "assisted", "human"):
            raise ToolError("registry-invalid", f"Invalid status for {item['id']}: {item['status']}",
                            "Use auto, assisted, or human.", str(p))
        seen.add(item["id"])
        out.append(_derive(item))
    out.sort(key=lambda c: (CATEGORY_ORDER.index(c["category"]) if c["category"] in CATEGORY_ORDER else 99,
                            c["name"]))
    return out


def categories(registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slug in CATEGORY_ORDER:
        items = [c for c in registry if c["category"] == slug]
        if not items:
            continue
        result.append({
            "slug": slug,
            "title": items[0]["category_title"],
            "count": len(items),
            "automated": sum(1 for c in items if c["status"] == "auto"),
            "assisted": sum(1 for c in items if c["status"] == "assisted"),
            "human": sum(1 for c in items if c["status"] == "human"),
            "capabilities": [c["id"] for c in items],
        })
    return result


def find(registry: list[dict[str, Any]], capability_id: str) -> dict[str, Any]:
    for cap in registry:
        if cap["id"] == capability_id:
            return cap
    raise ToolError("capability-not-found", f"Unknown capability id: {capability_id}",
                    "Run `capabilities` to list valid ids, or `search <text>`.", capability_id)


def search(registry: list[dict[str, Any]], query: str = "", category: str = "",
           status: str = "") -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    terms = [t for t in q.split() if t]
    result = []
    for cap in registry:
        if category and cap["category"] != category:
            continue
        if status and cap["status"] != status:
            continue
        if not terms:
            result.append(cap)
            continue
        haystack = " ".join([cap["name"], cap["description"], cap["category_title"],
                             cap["operation"], " ".join(cap.get("checks", []))]).lower()
        if all(t in haystack for t in terms):
            result.append(cap)
    return result


def describe(cap: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": cap["id"],
        "name": cap["name"],
        "category": cap["category_title"],
        "description": cap["description"],
        "status": cap["status"],
        "operation": cap["operation"],
        "parameters": cap.get("params", {}),
        "checks": cap.get("checks", []),
        "input": {
            "kind": cap["input_kind"],
            "required": cap["input_required"],
            "accepted": _accepted_input(cap),
        },
        "output": {"kind": cap["output_kind"], "files": ["<id>.json", "<id>.md"]},
        "standards": cap["standards"],
        "procedure": cap["procedure"],
        "pass_criteria": _pass_criteria(cap),
        "fail_criteria": _fail_criteria(cap),
        "human_review": cap["human_review"],
        "evidence": cap["evidence"],
        "limitations": cap["limitations"],
        "example": cap["example"],
        "version": "1.0.0",
    }


def _accepted_input(cap: dict[str, Any]) -> list[str]:
    kind = cap["input_kind"]
    mapping = {
        "html": [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"],
        "document": [".pdf", ".docx", ".xlsx", ".pptx", ".epub"],
        "locale": [".json", ".properties", ".csv", ".txt"],
        "json": [".json"],
        "csv": [".csv"],
        "review": [".csv", ".json"],
        "text": [".txt", ".md", ".html"],
        "captions": [".vtt", ".srt"],
        "audio": [".wav"],
        "data": [".csv", ".json"],
        "findings": [".json"],
        "evidence": [".json", ".csv", ".md", ".txt"],
        "defects": [".csv", ".json"],
        "retest": [".json"],
        "vendor": [".json", ".txt", ".md", ".html"],
        "request": [".json"],
        "brief": [".json", ".txt", ".md"],
        "config": [".json"],
    }
    return mapping.get(kind, [".json", ".txt"])


def _pass_criteria(cap: dict[str, Any]) -> list[str]:
    op = cap["operation"]
    base = ["The tool returns a valid JSON envelope and a readable Markdown report.",
            "All required inputs are present and validated before work begins."]
    if op in ("static_audit", "contrast_audit", "media_audit", "document_audit", "i18n_audit"):
        base.append("Every reported finding has a location, severity, and WCAG or standard reference where applicable.")
        base.append("No critical or serious finding remains without a remediation recommendation.")
    if op in ("governance_artifact", "plan_artifact"):
        base.append("The artifact names an accountable owner, review date, and evidence requirement.")
    if op == "release_gate":
        base.append("The gate decision is reproducible from the supplied findings and configuration.")
    if op == "pseudo_generate":
        base.append("All placeholders and ICU variables are preserved exactly.")
    return base


def _fail_criteria(cap: dict[str, Any]) -> list[str]:
    op = cap["operation"]
    base = ["A required input is missing, unreadable, or invalid.",
            "The engine cannot complete within the configured timeout or limits."]
    if op in ("static_audit", "contrast_audit", "media_audit", "document_audit", "i18n_audit"):
        base.append("A critical barrier is present and the capability is used as release evidence without remediation.")
    if op == "pseudo_generate":
        base.append("A placeholder is altered, removed, or duplicated.")
    if op == "release_gate":
        base.append("A critical or serious finding is unresolved and no approved exception exists.")
    return base
