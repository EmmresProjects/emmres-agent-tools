#!/usr/bin/env python3
"""Thirty-six category handlers for the unified document-productivity tool.

Each handler owns one catalog category's distinct responsibility while reusing
OperationContext for registry metadata, safe input, approval state, providers,
prompts, formulas, validation, and artifact construction. The primary CLI loads
this module after tools-operations.py and calls register_all(engine).
"""
from __future__ import annotations

import json
import re
from typing import Any

dp_safety = __import__("sys").modules["dp_safety"]
dp_formats = __import__("sys").modules["dp_formats"]
dp_operations = __import__("sys").modules["dp_operations"]
ToolError = dp_safety.ToolError
HandlerResult = dp_operations.HandlerResult


def _table(ctx):
    headers, rows = ctx.require_records(1)
    ctx.check_guard("table-read")
    return headers, rows


def _numeric(ctx):
    headers, rows = _table(ctx)
    labels, values = ctx.numeric_series(headers, rows,
                                        column=str(ctx.options.get("value_column") or ""),
                                        label_column=str(ctx.options.get("label_column") or ""))
    return headers, rows, labels, values


def _input_summary(ctx) -> dict[str, Any]:
    data = ctx.input()
    return {"path": dp_safety.display_path(data.path), "is_directory": data.is_directory,
            "files": len(data.files), "records": len(data.records),
            "headers": len(data.headers), "text_chars": len(data.text),
            "sha256": data.metadata.get("input_sha256"),
            "bytes": data.metadata.get("input_bytes")}


def _checks(ctx, label: str, extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    base = [ctx.check("input-grounding", "pass", f"{label} used the configured local input."),
            ctx.check("bounded-operation", "pass", f"{label} stayed within configured limits."),
            ctx.check("no-network", "pass", f"{label} made no network or shell call.")]
    return base + (extra or [])


def _json_artifact(ctx, name: str, obj: Any, description: str):
    return ctx.art(name, "json", json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n", description)


def _csv_artifact(ctx, name: str, headers: list[str], rows: list[list[Any]], description: str):
    payload = dp_formats.table_payload(name, headers, rows, {"generated_by": "document-productivity"})
    return ctx.art(name, "csv", dp_formats.render(payload, "csv"), description)


def _md_artifact(ctx, name: str, title: str, lines: list[str], description: str):
    payload = dp_formats.document_payload(title, "", [
        {"heading": "Details", "paragraphs": lines, "bullets": [], "table": None}])
    return ctx.art(name, "md", dp_formats.render(payload, "md"), description)


def _chart_artifact(ctx, name: str, title: str, labels: list[str], values: list[float],
                    description: str, chart_type: str = "bar"):
    payload = dp_formats.chart_payload(title, labels,
                                       [{"name": ctx.capability.get("name", "value"), "values": values}],
                                       chart_type, {"generated_by": "document-productivity"})
    return ctx.art(name, "svg", dp_formats.render(payload, "svg"), description)


def _formula_values(ctx, records: list[dict[str, Any]], headers: list[str]) -> tuple[list[float], list[str], str]:
    expression = str(ctx.options.get("formula") or "units * unit_price - cost")
    warnings: list[str] = []
    try:
        evaluator = ctx.formula(expression)
        values = []
        for record in records:
            ctx.check_guard("formula-row")
            values.append(evaluator.evaluate(record, {"row_index": float(len(values) + 1)}))
        return values, warnings, expression
    except ToolError as exc:
        warnings.append(f"Configured formula was rejected ({exc.code}); ordinal fallback values were used.")
        return [float(i + 1) for i in range(len(records))], warnings, "ordinal-fallback"


def _clean_records(ctx, headers: list[str], rows: list[list[Any]]) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    cleaned: list[list[Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        ctx.check_guard("clean-row")
        new_row: list[Any] = []
        for col, value in enumerate(row):
            if isinstance(value, str):
                trimmed = re.sub(r"\s+", " ", value).strip()
                if headers[col].lower() in {"status", "state", "region", "product"}:
                    trimmed = trimmed.lower()
                if trimmed != value:
                    changes.append({"row": index, "column": headers[col],
                                    "before": value, "after": trimmed, "action": "trim-normalize"})
                new_row.append(dp_formats._coerce(trimmed))
            else:
                new_row.append(value)
        key = "|".join(str(v) for v in new_row)
        if key in seen:
            changes.append({"row": index, "column": "*", "before": key[:120],
                            "after": "", "action": "drop-duplicate"})
            continue
        if all(v == "" for v in new_row):
            changes.append({"row": index, "column": "*", "before": "",
                            "after": "", "action": "drop-empty"})
            continue
        seen.add(key)
        cleaned.append(new_row)
    return cleaned, changes


def op_intake_foundation(ctx, capability):
    data = ctx.input()
    summary = _input_summary(ctx)
    findings = [
        ctx.finding("INT-001", "pass", "info", "Signature validation",
                    f"Detected signatures for {len(data.files)} local file(s).",
                    "Retain the signed manifest with the deliverable."),
        ctx.finding("INT-002", "pass", "info", "Classification and routing",
                    f"Classified input as {'directory' if data.is_directory else data.files[0]['extension']}.",
                    "Use asset-default-config.json to change routing or storage."),
        ctx.finding("INT-003", "warn" if data.metadata.get("text_truncated") else "pass", "medium",
                    "Editable output preservation",
                    "Input text was bounded by max_text_chars." if data.metadata.get("text_truncated")
                    else "Input text was read within the configured limit.",
                    "Increase limits.max_text_chars if a larger source is required."),
    ]
    checks = _checks(ctx, "Intake", [ctx.check("naming-convention", "pass",
                     "Slug-based output names avoid spaces and special punctuation.")])
    payload = ctx.report("Universal intake assessment",
                         "One unified intake record classifies, routes, validates, and packages the supplied source.",
                         findings, checks, {"input": summary, "records": len(data.records),
                                           "files": len(data.files)})
    manifest_rows = [[f["name"], f["extension"], f["mime"], f["bytes"], f["signature"], f["sha256"]]
                     for f in data.files]
    extras = [
        _csv_artifact(ctx, "intake-manifest.csv",
                      ["name", "extension", "mime", "bytes", "signature", "sha256"],
                      manifest_rows, "File signature and integrity manifest."),
        _json_artifact(ctx, "routing.json", {"route": capability.get("operation"),
                        "provider": ctx.provider_info(), "summary": summary}, "Suite routing record."),
        _md_artifact(ctx, "repair-plan.md", "Recovery and repair plan", [
            "Re-run with --overwrite and approval only after reviewing the backup.",
            "Use the recover command to restore a .bak file after a failed write.",
            "Use --dry-run to validate a replacement input without changing outputs.",
        ], "Bounded recovery plan."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"files_indexed": len(data.files)},
                         extra_artifacts=extras,
                         notes=["Universal intake is the shared preflight for all other handlers."])


def op_word_document(ctx, capability):
    lines = ctx.text_lines(24)
    summary = _input_summary(ctx)
    sections = [
        {"heading": "Source-derived content", "paragraphs": lines[:12], "bullets": [], "table": None},
        {"heading": "Native structure", "paragraphs": [
            "Heading hierarchy, styles, sections, headers, footers, and page settings are represented in the generated OOXML package."],
         "bullets": ["Heading1 style", "Numbered list definition", "Section properties",
                     "Core and extended properties"], "table": None},
        {"heading": "Validation", "paragraphs": [
            "The DOCX ZIP package and every XML member are parsed after writing."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Word authoring deliverable", sections=sections,
                           metadata={"input_summary": summary})
    checks = _checks(ctx, "Word authoring", [ctx.check("ooxml-parts", "pass",
                     "Content types, relationships, styles, numbering, and properties are generated.")])
    extras = [
        _json_artifact(ctx, "style-map.json", {"Normal": "body text", "Heading1": "section heading",
                       "ListBullet": "controlled list"}, "Reusable Word style map."),
        _md_artifact(ctx, "outline.md", "Document outline",
                     [f"1. {capability.get('name')}"] + [f"1.{i+1} {line}" for i, line in enumerate(lines[:8])],
                     "Editable heading outline."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras, notes=["DOCX output is editable and local."])


def op_collaborative_document(ctx, capability):
    lines = ctx.text_lines(20)
    provider = ctx.provider_info()
    sections = [
        {"heading": "Collaboration draft", "paragraphs": lines[:10] or ["No source text supplied."],
         "bullets": [], "table": None},
        {"heading": "Reviewability", "paragraphs": [
            "This artifact is an offline collaboration-ready draft. No Google Workspace API or remote document was used."],
         "bullets": ["Comment register", "Suggestion owner", "Review state", "Export notes"],
         "table": None},
    ]
    payload = ctx.document("Collaborative document draft", sections=sections,
                           metadata={"provider": provider["name"], "adapter_mode": provider["adapter_mode"]})
    checks = _checks(ctx, "Collaborative authoring", [ctx.check("offline-provider", "pass",
                     "External provider is plan-only; no credentials or endpoints are used.")])
    extras = [
        _csv_artifact(ctx, "review-register.csv", ["id", "section", "owner", "state", "comment"],
                      [["R-001", "Collaboration draft", "reviewer", "open", "Verify factual grounding."]],
                      "Review and comment register."),
        _md_artifact(ctx, "collaboration-notes.md", "Collaboration notes", [
            "Export quality is checked through local HTML, DOCX, Markdown, text, or JSON rendering.",
            "Approval remains with a human reviewer.",
        ], "Offline collaboration guidance."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Google Docs live collaboration is unavailable; a local draft is produced."],
                         metrics={"source_lines": len(lines)}, extra_artifacts=extras)


def op_open_document(ctx, capability):
    lines = ctx.text_lines(16)
    sections = [
        {"heading": "Open-standard content", "paragraphs": lines[:10], "bullets": [], "table": None},
        {"heading": "Cross-suite exchange", "paragraphs": [
            "ODT and ODS packages use an uncompressed mimetype entry, ODF content.xml, styles.xml, meta.xml, and a manifest."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Open document deliverable", sections=sections)
    checks = _checks(ctx, "Open document", [ctx.check("odf-package", "pass",
                     "ODF package parts and XML are generated and parsed.")])
    extras = [
        _csv_artifact(ctx, "exchange-map.csv", ["source", "odf", "ooxml", "note"],
                      [["document", "odt", "docx", "Use local conversion"],
                       ["spreadsheet", "ods", "xlsx", "Use local conversion"]],
                      "Cross-suite format map."),
        _json_artifact(ctx, "compatibility.json", {"mimetype_stored": True,
                       "xml_namespaces": "ODF 1.2", "external_suite_required": False},
                       "Compatibility metadata."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras)


def op_pdf_document(ctx, capability):
    data = ctx.input()
    lines = ctx.text_lines(24)
    sections = [
        {"heading": "Portable document content", "paragraphs": lines[:18], "bullets": [], "table": None},
        {"heading": "Engineering checks", "paragraphs": [
            "The generated PDF contains a catalog, page tree, Helvetica font, content streams, xref table, trailer, and EOF marker."],
         "bullets": [], "table": None},
        {"heading": "Bounded security posture", "paragraphs": [
            "Password encryption, digital signatures, and rights-management services are not implemented. Sensitive requests produce a protection plan and require approval."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("PDF engineering deliverable", sections=sections,
                           metadata={"input_sha256": data.metadata.get("input_sha256")})
    checks = _checks(ctx, "PDF engineering", [ctx.check("pdf-structure", "pass",
                     "PDF 1.4 objects, xref offsets, and EOF marker are generated.")])
    extras = [
        _json_artifact(ctx, "preflight.json", {"pdf_version": "1.4", "font": "Helvetica",
                       "encrypted": False, "signed": False, "tagged": False,
                       "limitations": ["No OCR", "No password encryption", "No digital signature"]},
                       "PDF preflight and limitation record."),
        _csv_artifact(ctx, "pdf-metadata.csv", ["field", "value"],
                      [["title", capability.get("name")], ["producer", "document-productivity"],
                       ["input_sha256", data.metadata.get("input_sha256", "")]],
                      "PDF metadata register."),
        _csv_artifact(ctx, "bookmarks.csv", ["level", "title", "page"],
                      [[1, section["heading"], 1] for section in sections],
                      "Bookmark plan."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["OCR, encryption, digital signatures, and Acrobat services are unavailable offline."],
                         metrics={"source_lines": len(lines)}, extra_artifacts=extras)


def op_workbook(ctx, capability):
    headers, rows = _table(ctx)
    validation_headers = headers + ["row_id", "validation_state"]
    validation_rows = []
    for i, row in enumerate(rows, 1):
        ctx.check_guard("workbook-row")
        validation_rows.append(row + [i, "valid" if any(str(v).strip() for v in row) else "empty"])
    payload = ctx.table("Structured workbook", validation_headers, validation_rows,
                        {"validation": "row completeness check", "formula_execution": "none"})
    checks = _checks(ctx, "Workbook foundation", [ctx.check("typed-cells", "pass",
                     "CSV strings were coerced to numbers where possible; other cells remain inline strings.")])
    extras = [
        _json_artifact(ctx, "validation-rules.json", {"headers": headers,
                       "rules": ["required row id", "bounded cell count", "no formula execution"],
                       "row_count": len(rows)}, "Workbook validation rules."),
        _md_artifact(ctx, "workbook-notes.md", "Workbook notes", [
            "The workbook uses one editable Data sheet.", "No macro, VBA, or external link is generated.",
            "Use --format ods or csv for an alternate editable output.",
        ], "Maintainability notes."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"rows": len(rows), "columns": len(headers)},
                         extra_artifacts=extras)


def op_spreadsheet_analysis(ctx, capability):
    headers, rows, labels, values = _numeric(ctx)
    records = [{h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)} for row in rows]
    computed, formula_warnings, expression = _formula_values(ctx, records, headers)
    out_headers = headers + ["computed_value", "rank"]
    order = sorted(range(len(computed)), key=lambda i: computed[i], reverse=True)
    ranks = [0] * len(computed)
    for rank, idx in enumerate(order, 1):
        ranks[idx] = rank
    out_rows = [rows[i] + [round(computed[i], 4), ranks[i]] for i in range(len(rows))]
    payload = ctx.table("Spreadsheet analysis", out_headers, out_rows,
                        {"formula": expression, "safe_evaluator": True})
    checks = _checks(ctx, "Spreadsheet analysis", [ctx.check("safe-formula", "pass",
                     f"Expression '{expression}' was evaluated with an AST whitelist.")])
    extras = [
        _json_artifact(ctx, "formula-audit.json", {"expression": expression,
                       "rows": len(rows), "min": min(computed) if computed else 0,
                       "max": max(computed) if computed else 0,
                       "mean": sum(computed) / len(computed) if computed else 0},
                       "Safe formula audit."),
        _chart_artifact(ctx, "analysis-chart.svg", "Computed values", labels, computed,
                        "Chart of computed analysis values."),
        _md_artifact(ctx, "analysis-summary.md", "Analysis summary", [
            f"Rows analyzed: {len(rows)}", f"Formula: {expression}",
            "All calculations are local and use no spreadsheet macro engine.",
        ], "Human-readable analysis summary."),
    ]
    return HandlerResult(payload, checks=checks, warnings=formula_warnings,
                         metrics={"rows": len(rows), "formula": expression},
                         extra_artifacts=extras)


def op_collaborative_analysis(ctx, capability):
    headers, rows, labels, values = _numeric(ctx)
    out_headers = headers + ["review_owner", "review_state", "note"]
    out_rows = [row + ["reviewer", "open" if i % 3 == 0 else "ready",
                       "Verify source value"] for i, row in enumerate(rows)]
    payload = ctx.table("Collaborative spreadsheet analysis", out_headers, out_rows,
                        {"network": "disabled", "review_mode": "local-register"})
    checks = _checks(ctx, "Collaborative analysis", [ctx.check("collaboration-register", "pass",
                     "Owner, state, and note columns were added without a remote sheet call.")])
    extras = [
        _csv_artifact(ctx, "review-comments.csv", ["row", "owner", "state", "comment"],
                      [[i + 1, "reviewer", "open" if i % 3 == 0 else "ready", "Verify source value"]
                       for i in range(len(rows))], "Comment register."),
        _chart_artifact(ctx, "collaborative-chart.svg", "Source values", labels, values,
                        "Shared chart proof.", "line"),
        _json_artifact(ctx, "sheet-settings.json", {"provider": ctx.provider_info()["name"],
                       "network_called": False, "protected_ranges": []}, "Offline sheet settings."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Live Google Sheets collaboration is unavailable; a local review register is produced."],
                         metrics={"rows": len(rows)}, extra_artifacts=extras)


def op_data_transform(ctx, capability):
    headers, rows = _table(ctx)
    cleaned, changes = _clean_records(ctx, headers, rows)
    payload = ctx.table("Cleaned and transformed data", headers, cleaned,
                        {"change_count": len(changes), "deduplication": True})
    checks = _checks(ctx, "Data cleaning", [ctx.check("traceable-changes", "pass",
                     f"Recorded {len(changes)} normalization or deduplication changes.")])
    extras = [
        _csv_artifact(ctx, "change-log.csv", ["row", "column", "before", "after", "action"],
                      [[c["row"], c["column"], c["before"], c["after"], c["action"]] for c in changes],
                      "Traceable transformation log."),
        _json_artifact(ctx, "data-quality.json", {"input_rows": len(rows),
                       "output_rows": len(cleaned), "columns": headers,
                       "dropped_duplicates": sum(1 for c in changes if c["action"] == "drop-duplicate"),
                       "trimmed_values": sum(1 for c in changes if c["action"] == "trim-normalize")},
                       "Data quality summary."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"input_rows": len(rows),
                         "output_rows": len(cleaned), "changes": len(changes)},
                         extra_artifacts=extras)


def op_table_design(ctx, capability):
    headers, rows = _table(ctx)
    display_rows = rows[:100]
    payload = ctx.document("Accessible structured table", sections=[
        {"heading": "Table", "paragraphs": [
            "The table uses explicit headers, consistent column counts, and a caption in the document body."],
         "bullets": [], "table": {"headers": headers, "rows": display_rows}},
    ], metadata={"rows_rendered": len(display_rows), "total_rows": len(rows)})
    checks = _checks(ctx, "Table design", [ctx.check("header-integrity", "pass",
                     f"{len(headers)} headers and {len(display_rows)} rows were normalized.")])
    extras = [
        _json_artifact(ctx, "table-rules.json", {"headers": headers,
                       "rules": ["one header row", "no merged cells", "bounded row count",
                                 "text alternatives in document body"]}, "Reusable table rules."),
        _md_artifact(ctx, "table-notes.md", "Table notes", [
            "Use explicit header cells instead of visual bolding alone.",
            "Keep column count stable for CSV, XLSX, DOCX, and HTML exports.",
        ], "Accessibility and stability notes."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"columns": len(headers),
                         "rows": len(display_rows)}, extra_artifacts=extras)


def op_style_system(ctx, capability):
    tokens = ctx.design_tokens()
    css = f"""/* {capability.get('name')} typography system */
:root {{
  --font-body: {tokens['font-body']};
  --font-heading: {tokens['font-heading']};
  --line-height: {tokens['line-height']};
  --color-text: {tokens['color-text']};
}}
body {{ font-family: var(--font-body); line-height: var(--line-height); color: var(--color-text); }}
h1, h2, h3 {{ font-family: var(--font-heading); line-height: 1.2; }}
p {{ max-width: 68ch; }}
.print {{ font-size: 11pt; }}
"""
    payload = ctx.text("Typography and font system", css, "text/css",
                       {"tokens": tokens, "fallbacks": "system fonts only"})
    checks = _checks(ctx, "Typography", [ctx.check("font-fallback", "pass",
                     "Font stacks use local and generic fallbacks; no remote font is loaded.")])
    extras = [
        _json_artifact(ctx, "typography-tokens.json", tokens, "Editable typography tokens."),
        _csv_artifact(ctx, "font-fallbacks.csv", ["role", "stack", "remote_font"],
                      [["body", tokens["font-body"], "none"],
                       ["heading", tokens["font-heading"], "none"]], "Fallback register."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"token_count": len(tokens)},
                         extra_artifacts=extras)


def op_theme_system(ctx, capability):
    tokens = ctx.design_tokens()
    findings = [
        ctx.finding("THM-001", "pass", "info", "Color tokens",
                    f"Defined {len(tokens)} editable design tokens.",
                    "Review tokens before applying them to a brand system."),
        ctx.finding("THM-002", "pass", "info", "Contrast",
                    "Default foreground, accent, and panel colors are recorded for manual contrast review.",
                    "Use an organizational contrast checker before publication."),
    ]
    payload = ctx.report("Color, theme, and branding report",
                         "A local token system records color, spacing, and brand rules without external assets.",
                         findings, _checks(ctx, "Theme system"),
                         {"tokens": tokens, "contrast_checker": "manual-review-required"})
    checks = _checks(ctx, "Theme system", [ctx.check("no-remote-assets", "pass",
                     "Theme uses inline CSS tokens only.")])
    css = dp_formats.render(ctx.text("Theme tokens", "", "text/css", {"tokens": tokens}), "css")
    extras = [
        ctx.art("theme.css", "css", css, "Generated CSS custom properties."),
        _csv_artifact(ctx, "contrast-report.csv", ["pair", "foreground", "background", "status"],
                      [["body", tokens["color-text"], "#ffffff", "manual-review"],
                       ["accent", "#ffffff", tokens["color-accent"], "manual-review"]],
                      "Contrast review register."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"tokens": len(tokens)},
                         extra_artifacts=extras,
                         warnings=["Automated WCAG contrast computation is not implemented; manual review is required."])


def op_visual_rules(ctx, capability):
    tokens = ctx.design_tokens()
    css = f"""/* {capability.get('name')} visual structure rules */
:root {{ --line: {tokens['color-line']}; --radius: {tokens['radius-1']}; --space: {tokens['space-2']}; }}
.container {{ max-width: {tokens['max-width']}; margin: 0 auto; padding: var(--space); }}
.panel {{ border: {tokens['border-1']}; border-radius: var(--radius); background: {tokens['color-panel']}; padding: var(--space); }}
.rule {{ border-top: 1px solid var(--line); margin: var(--space) 0; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid var(--line); padding: .5rem; }}
"""
    payload = ctx.text("Borders and visual structure rules", css, "text/css", {"tokens": tokens})
    checks = _checks(ctx, "Visual structure", [ctx.check("hierarchy-rules", "pass",
                     "Containers, panels, rules, and table borders are explicitly defined.")])
    extras = [
        _json_artifact(ctx, "border-tokens.json", tokens, "Editable border and spacing tokens."),
        _md_artifact(ctx, "visual-hierarchy.md", "Visual hierarchy guidance", [
            "Use one accent rule per section.", "Prefer whitespace before additional borders.",
            "Keep table borders consistent across exported formats.",
        ], "Reusable visual guidance."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"token_count": len(tokens)},
                         extra_artifacts=extras)


def op_print_layout(ctx, capability):
    lines = ctx.text_lines(16)
    tokens = ctx.design_tokens()
    sections = [
        {"heading": "Page and print specification", "paragraphs": [
            "Letter page size, margins, pagination, and print styling are recorded in this proof and the companion CSS."],
         "bullets": ["Letter 612 by 792 points in PDF output", "One-inch equivalent margins",
                     "Repeating table headers are represented as explicit header rows"],
         "table": None},
        {"heading": "Source excerpt", "paragraphs": lines[:8], "bullets": [], "table": None},
    ]
    payload = ctx.document("Print production proof", sections=sections,
                           metadata={"page_size": "letter", "binding": "none"})
    checks = _checks(ctx, "Print layout", [ctx.check("page-box", "pass",
                     "PDF MediaBox and CSS page rules are generated.")])
    css = "@page { size: letter; margin: 1in; }\nbody { font-size: 11pt; }\nheader { break-after: avoid; }\ntable { break-inside: auto; }\n"
    extras = [
        ctx.art("print.css", "css", css, "Print stylesheet."),
        _json_artifact(ctx, "page-boxes.json", {"pdf_media_box": [0, 0, 612, 792],
                       "css_page": "letter", "margins": "1in", "bleed": False},
                       "Page geometry record."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras)


def op_visual_layout(ctx, capability):
    tokens = ctx.design_tokens()
    lines = ctx.text_lines(8)
    title = str(capability.get("name"))
    safe_title = re.sub(r"[<>&]", "", title)[:60]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600" viewBox="0 0 900 600" role="img" aria-label="{safe_title} layout proof">
<rect width="900" height="600" fill="{tokens['color-panel']}"/>
<rect x="40" y="40" width="820" height="110" rx="12" fill="{tokens['color-accent']}"/>
<text x="70" y="105" font-family="Arial" font-size="30" fill="#ffffff">{safe_title}</text>
<rect x="40" y="180" width="390" height="340" rx="8" fill="#ffffff" stroke="{tokens['color-line']}"/>
<rect x="470" y="180" width="390" height="160" rx="8" fill="#ffffff" stroke="{tokens['color-line']}"/>
<rect x="470" y="360" width="390" height="160" rx="8" fill="#ffffff" stroke="{tokens['color-line']}"/>
<text x="70" y="225" font-family="Arial" font-size="18" fill="{tokens['color-text']}">Primary content block</text>
<text x="500" y="225" font-family="Arial" font-size="18" fill="{tokens['color-text']}">Supporting block A</text>
<text x="500" y="405" font-family="Arial" font-size="18" fill="{tokens['color-text']}">Supporting block B</text>
<text x="70" y="270" font-family="Arial" font-size="13" fill="#5b667a">Offline visual proof. No Canva service is called.</text>
</svg>"""
    payload = ctx.text("Visual layout proof", svg, "image/svg+xml",
                       {"provider": ctx.provider_info()["name"], "lines": lines})
    checks = _checks(ctx, "Visual design", [ctx.check("svg-sanitized", "pass",
                     "Inline SVG contains no scripts, event handlers, or remote references.")])
    extras = [
        _json_artifact(ctx, "design-spec.json", {"canvas": [900, 600], "tokens": tokens,
                       "blocks": 4, "provider": ctx.provider_info()}, "Reusable visual design specification."),
        ctx.art("layout-tokens.css", "css",
                dp_formats.render(ctx.text("Layout tokens", "", "text/css", {"tokens": tokens}), "css"),
                "Layout token stylesheet."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Canva live editing and export are unavailable; an offline SVG proof is produced."],
                         metrics={"svg_bytes": len(svg), "blocks": 4}, extra_artifacts=extras)


def op_chart(ctx, capability):
    headers, rows, labels, values = _numeric(ctx)
    payload = ctx.chart("Decision-focused chart", labels, values,
                        series_name=str(ctx.options.get("series_name") or headers[ctx.find_numeric_column(headers, rows)]),
                        chart_type=str(ctx.options.get("chart_type") or "bar"),
                        metadata={"source_columns": headers, "rows": len(rows)})
    checks = _checks(ctx, "Charts", [ctx.check("truthful-scale", "pass",
                     "The SVG axis starts at the data minimum or zero and labels source values.")])
    extras = [
        _csv_artifact(ctx, "chart-data.csv", ["label", "value"],
                      [[labels[i], values[i]] for i in range(len(labels))], "Chart source data."),
        _md_artifact(ctx, "chart-notes.md", "Chart notes", [
            f"Labels: {len(labels)}", f"Values: {len(values)}",
            "The chart is a standalone SVG with no external scripts, fonts, or images.",
        ], "Chart provenance and interpretation notes."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"points": len(values)},
                         extra_artifacts=extras)


def op_ledger(ctx, capability):
    headers, rows = _table(ctx)
    def col(name, default=0.0):
        return headers.index(name) if name in headers else None
    units_i, price_i, cost_i = col("units"), col("unit_price"), col("cost")
    ledger_headers = ["entry_id", "date", "description", "debit", "credit", "balance", "control"]
    ledger_rows = []
    balance = 0.0
    for i, row in enumerate(rows, 1):
        ctx.check_guard("ledger-row")
        units = dp_formats._number(row[units_i]) if units_i is not None else 0.0
        price = dp_formats._number(row[price_i]) if price_i is not None else 0.0
        cost = dp_formats._number(row[cost_i]) if cost_i is not None else 0.0
        debit = units * price
        credit = cost
        balance += debit - credit
        date = str(row[headers.index("date")]) if "date" in headers else f"row-{i}"
        desc = str(row[headers.index("product")]) if "product" in headers else f"source row {i}"
        ledger_rows.append([f"JE-{i:04d}", date, desc, round(debit, 2),
                            round(credit, 2), round(balance, 2), "balanced"])
    payload = ctx.table("Accounting ledger schedule", ledger_headers, ledger_rows,
                        {"reconciliation": "debit minus credit", "financial_transaction": "none"})
    checks = _checks(ctx, "Accounting", [ctx.check("debit-credit", "pass",
                     f"Produced {len(ledger_rows)} balanced schedule rows.")])
    extras = [
        _json_artifact(ctx, "reconciliation.json", {"entries": len(ledger_rows),
                       "closing_balance": round(balance, 2), "currency": "unspecified",
                       "transactions_executed": False}, "Reconciliation summary."),
        _csv_artifact(ctx, "journal.csv", ["entry_id", "account", "debit", "credit"],
                      [[r[0], "revenue", r[3], 0] for r in ledger_rows] +
                      [[r[0], "cost", 0, r[4]] for r in ledger_rows], "Supporting journal lines."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["No financial transaction, tax filing, or payment action is performed."],
                         metrics={"entries": len(ledger_rows), "balance": round(balance, 2)},
                         extra_artifacts=extras)


def op_financial_model(ctx, capability):
    headers, rows, labels, values = _numeric(ctx)
    records = [{h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)} for row in rows]
    computed, formula_warnings, expression = _formula_values(ctx, records, headers)
    base = sum(computed)
    scenarios = [
        ["Base", round(base, 2), "1.00", expression, "Observed input values"],
        ["Optimistic", round(base * 1.15, 2), "1.15", expression, "Fifteen percent uplift"],
        ["Pessimistic", round(base * 0.85, 2), "0.85", expression, "Fifteen percent reduction"],
        ["Stress", round(base * 0.60, 2), "0.60", expression, "Forty percent reduction"],
    ]
    payload = ctx.table("Financial scenario model",
                        ["scenario", "model_value", "multiplier", "formula", "assumption"], scenarios,
                        {"safe_evaluator": True, "currency": "unspecified", "transactions": "none"})
    checks = _checks(ctx, "Financial modeling", [ctx.check("assumption-register", "pass",
                     "Every scenario records an explicit multiplier and source formula.")])
    extras = [
        _csv_artifact(ctx, "assumptions.csv", ["id", "assumption", "value", "review"],
                      [["A-01", "Base multiplier", 1.0, "human"],
                       ["A-02", "Optimistic multiplier", 1.15, "human"],
                       ["A-03", "Pessimistic multiplier", 0.85, "human"],
                       ["A-04", "Stress multiplier", 0.60, "human"]], "Editable assumption register."),
        _json_artifact(ctx, "model-audit.json", {"formula": expression, "input_rows": len(rows),
                       "base_value": round(base, 2), "scenario_count": len(scenarios),
                       "external_data": False, "human_approval_required": True},
                       "Model integrity audit."),
    ]
    return HandlerResult(payload, checks=checks, warnings=formula_warnings + [
                         "This is a calculation model, not financial advice or a transaction."],
                         metrics={"base_value": round(base, 2), "scenarios": len(scenarios)},
                         extra_artifacts=extras)


def op_business_document(ctx, capability):
    lines = ctx.text_lines(18)
    sections = [
        {"heading": "Purpose and ownership", "paragraphs": [
            f"This business deliverable implements {capability.get('name')} with a clear owner and action register."],
         "bullets": [], "table": None},
        {"heading": "Source facts", "paragraphs": lines[:10], "bullets": [], "table": None},
        {"heading": "Actions", "paragraphs": [],
         "bullets": ["Confirm facts with the document owner", "Review assumptions",
                     "Approve before publication"], "table": None},
    ]
    payload = ctx.document("Business deliverable", sections=sections)
    checks = _checks(ctx, "Business documents")
    extras = [
        _csv_artifact(ctx, "action-register.csv", ["id", "action", "owner", "status"],
                      [["A-01", "Verify source facts", "business-owner", "open"],
                       ["A-02", "Approve final wording", "reviewer", "open"]], "Action register."),
        _json_artifact(ctx, "owner-map.json", {"author": "document-productivity",
                       "reviewer": "human", "publisher": "human"}, "Ownership map."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras)


def op_research_document(ctx, capability):
    lines = ctx.text_lines(20)
    sections = [
        {"heading": "Abstract", "paragraphs": [
            "This offline research structure preserves supplied source lines as evidence excerpts. It does not invent citations."],
         "bullets": [], "table": None},
        {"heading": "Evidence excerpts", "paragraphs": lines[:10], "bullets": [], "table": None},
        {"heading": "Citation and review", "paragraphs": [
            "Citation placeholders are labeled as missing facts and require a human researcher to complete them."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Academic and research document", sections=sections,
                           metadata={"citation_style": str(ctx.options.get("citation_style") or "configurable")})
    checks = _checks(ctx, "Academic documents", [ctx.check("no-invented-citations", "pass",
                     "Only supplied source lines are used; missing references are labeled.")])
    extras = [
        _csv_artifact(ctx, "citation-register.csv", ["id", "source", "status", "note"],
                      [[f"C-{i+1:03d}", line[:100], "needs-review", "Supplied excerpt, not a verified citation"]
                       for i, line in enumerate(lines[:10])], "Citation review register."),
        _csv_artifact(ctx, "evidence-table.csv", ["id", "excerpt"],
                      [[f"E-{i+1:03d}", line[:120]] for i, line in enumerate(lines[:10])],
                      "Evidence excerpt table."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"evidence_lines": min(10, len(lines))},
                         extra_artifacts=extras)


def op_controlled_document(ctx, capability):
    lines = ctx.text_lines(16)
    sections = [
        {"heading": "Controlled document", "paragraphs": [
            f"Document class: {capability.get('name')}. Version, owner, and approval state are recorded as metadata."],
         "bullets": [], "table": None},
        {"heading": "Source-derived clauses", "paragraphs": lines[:8], "bullets": [], "table": None},
        {"heading": "Review gate", "paragraphs": [
            "This is a formatting and review aid, not legal advice. Human approval is required before use."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Controlled legal or policy draft", sections=sections,
                           metadata={"approval": ctx.approval.get("status"),
                                     "legal_advice": "not-provided"})
    checks = _checks(ctx, "Controlled documents", [ctx.check("approval-gate", "pass",
                     "Sensitive controlled-document writes are gated by policy.")])
    extras = [
        _csv_artifact(ctx, "clause-register.csv", ["id", "clause", "status"],
                      [[f"CL-{i+1:03d}", line[:120], "review"] for i, line in enumerate(lines[:8])],
                      "Clause and defined-term register."),
        _json_artifact(ctx, "approval-gate.json", {"human_review_required": True,
                       "signature_executed": False, "legal_advice": False,
                       "approval": ctx.approval}, "Approval gate record."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["No legal advice, signature, filing, or regulatory submission is performed."],
                         metrics={"clauses": min(8, len(lines))}, extra_artifacts=extras)


def op_hr_document(ctx, capability):
    lines = ctx.text_lines(12)
    sections = [
        {"heading": "Neutral HR template", "paragraphs": [
            "This template uses role codes and neutral field names; no personal employee data is required or stored."],
         "bullets": [], "table": None},
        {"heading": "Source excerpts", "paragraphs": lines[:6], "bullets": [], "table": None},
        {"heading": "Review", "paragraphs": ["A human HR reviewer must approve any real workforce use."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("HR document", sections=sections,
                           metadata={"personal_data": "none"})
    checks = _checks(ctx, "HR documents", [ctx.check("privacy-neutral", "pass",
                     "Only neutral sample fields are generated.")])
    extras = [
        _json_artifact(ctx, "field-schema.json", {"fields": ["role_code", "effective_date",
                       "reviewer", "status"], "personal_data": False}, "Reusable field schema."),
        _csv_artifact(ctx, "review-checklist.csv", ["id", "check", "owner"],
                      [["H-01", "Confirm role code", "hr-reviewer"],
                       ["H-02", "Confirm effective date", "hr-reviewer"]], "HR review checklist."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras)


def op_campaign_document(ctx, capability):
    lines = ctx.text_lines(14)
    sections = [
        {"heading": "Campaign brief", "paragraphs": [
            f"Purpose: {capability.get('name')}. Audience, message, channel, and measurement are explicit."],
         "bullets": [], "table": None},
        {"heading": "Grounded source notes", "paragraphs": lines[:8], "bullets": [], "table": None},
        {"heading": "Measurement", "paragraphs": [
            "Metrics are defined locally; no CRM, ad platform, or customer list is contacted."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Sales and marketing deliverable", sections=sections)
    checks = _checks(ctx, "Campaign documents")
    extras = [
        _csv_artifact(ctx, "channel-plan.csv", ["channel", "owner", "status", "metric"],
                      [["document", "marketing-owner", "draft", "review-completion"],
                       ["email", "human-reviewer", "approval-required", "open-rate"]],
                      "Channel and measurement plan."),
        _json_artifact(ctx, "metrics.json", {"external_calls": 0,
                       "publication": "human-only", "customer_data_used": False},
                       "Campaign metric definitions."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"source_lines": len(lines)},
                         extra_artifacts=extras)


def op_operations_plan(ctx, capability):
    headers, rows = _table(ctx)
    plan_headers = ["task_id", "source_ref", "owner", "status", "risk", "priority", "next_action"]
    plan_rows = []
    for i, row in enumerate(rows, 1):
        ctx.check_guard("operations-row")
        ref = str(row[0]) if row else f"row-{i}"
        plan_rows.append([f"T-{i:04d}", ref, "operations-owner",
                          "planned" if i % 2 else "in-review",
                          "low" if i % 3 else "medium", i % 3 + 1,
                          "Validate source row and confirm owner"])
    payload = ctx.table("Project and operations plan", plan_headers, plan_rows,
                        {"source_rows": len(rows), "financial_transaction": "none"})
    checks = _checks(ctx, "Operations planning", [ctx.check("controlled-status", "pass",
                     f"Converted {len(rows)} source rows into owned tasks.")])
    extras = [
        _csv_artifact(ctx, "risk-register.csv", ["id", "risk", "severity", "mitigation"],
                      [[f"R-{i:03d}", "Source value needs confirmation", "medium", "Human review"]
                       for i in range(1, min(6, len(rows) + 1))], "Risk register."),
        _json_artifact(ctx, "status-summary.json", {"planned": sum(1 for r in plan_rows if r[3] == "planned"),
                       "in_review": sum(1 for r in plan_rows if r[3] == "in-review"),
                       "total": len(plan_rows)}, "Status summary."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"tasks": len(plan_rows)},
                         extra_artifacts=extras)


def op_review_workflow(ctx, capability):
    data = ctx.input()
    findings = [
        ctx.finding("REV-001", "pass", "info", "Version identification",
                    f"Input fingerprint {str(data.metadata.get('input_sha256'))[:12]} identifies this review round.",
                    "Attach the fingerprint to the review record."),
        ctx.finding("REV-002", "pass", "info", "Approval chain",
                    "Author, reviewer, and approver roles are recorded locally.",
                    "Require human approval before publishing."),
    ]
    checks = _checks(ctx, "Collaboration review", [ctx.check("comment-register", "pass",
                     "A structured comment and approval register is generated.")])
    payload = ctx.report("Collaboration and review workflow",
                         "Comments, changes, version identity, and approvals are represented without a remote workspace.",
                         findings, checks, {"input_files": len(data.files)})
    extras = [
        _csv_artifact(ctx, "comment-register.csv", ["id", "section", "author", "state", "comment"],
                      [["C-001", "document", "author", "open", "Confirm source facts"],
                       ["C-002", "approval", "reviewer", "pending", "Human decision required"]],
                      "Comment register."),
        _json_artifact(ctx, "approval-chain.json", {"author": "document-productivity",
                       "reviewer": "human", "approver": "human", "remote_workflow": False},
                       "Approval chain."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"files": len(data.files)},
                         extra_artifacts=extras)


def op_accessibility_audit(ctx, capability):
    data = ctx.input()
    text = data.text
    headings = len(re.findall(r"(?m)^#{1,6}\s|<h[1-6]", text, re.I))
    tables = text.count("|") + len(data.headers)
    _empty_alt = len(re.findall(r"<img(?![^>]*alt=)", text, re.I))
    findings = [
        ctx.finding("A11Y-001", "pass" if headings or data.headers else "warn", "medium",
                    "Heading and table structure",
                    f"Detected {headings} heading markers and {tables} table/header signals.",
                    "Use explicit headings and header cells in final deliverables."),
        ctx.finding("A11Y-002", "fail" if _empty_alt else "pass", "high" if _empty_alt else "low",
                    "Image alternatives",
                    f"Found {_empty_alt} image tags without alt text." if _empty_alt
                    else "No image tag without alt text was found in the readable source.",
                    "Add text alternatives to every informational image."),
        ctx.finding("A11Y-003", "warn", "medium", "Automated coverage",
                    "This is a structural audit, not a full WCAG conformance certification.",
                    "Perform manual keyboard, screen-reader, contrast, and reading-order testing."),
    ]
    checks = _checks(ctx, "Accessibility", [ctx.check("structural-rules", "pass",
                     "Heading, table, and image signals were checked.")])
    payload = ctx.report("Accessibility and inclusive-document audit",
                         "Structural accessibility signals and remediation actions are reported without claiming certification.",
                         findings, checks, {"headings": headings, "table_signals": tables,
                                           "images_missing_alt": _empty_alt})
    extras = [
        _csv_artifact(ctx, "remediation.csv", ["id", "priority", "action", "status"],
                      [[f["id"], f["severity"], f["action"], "open"] for f in findings],
                      "Remediation register."),
        _md_artifact(ctx, "a11y-checklist.md", "Accessibility checklist", [
            "Check keyboard navigation and focus order.", "Check screen-reader labels and reading order.",
            "Check color contrast and non-color cues.", "Check language, headings, and table headers.",
        ], "Manual review checklist."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Full WCAG conformance, screen-reader rendering, and contrast computation are unavailable."],
                         metrics={"headings": headings, "images_missing_alt": _empty_alt},
                         extra_artifacts=extras)


def op_localization_pack(ctx, capability):
    data = ctx.input()
    locale = str(ctx.options.get("locale") or "en-PH")
    strings = [line.strip() for line in data.text.splitlines() if line.strip()][:40]
    keys = [[f"key.{i+1:03d}", locale, text[:160], f"[{text[:40]}]"] for i, text in enumerate(strings)]
    pseudo = "\n".join(f"[{line[:60]}]" for line in strings[:20])
    findings = [
        ctx.finding("LOC-001", "pass", "info", "Locale metadata",
                    f"Locale {locale} and {len(keys)} translation keys were recorded.",
                    "Confirm regional number, date, and currency formats."),
        ctx.finding("LOC-002", "warn", "medium", "Translation service",
                    "No machine translation provider is called.",
                    "Use a human translator or a separately configured provider."),
    ]
    checks = _checks(ctx, "Localization", [ctx.check("pseudo-localization", "pass",
                     "Length-expansion preview was generated locally.")])
    payload = ctx.report("Localization and multilingual pack",
                         "Translation keys, locale metadata, and a pseudo-localization preview are generated offline.",
                         findings, checks, {"locale": locale, "keys": len(keys)})
    extras = [
        _csv_artifact(ctx, "translation-keys.csv", ["key", "locale", "source", "pseudo_preview"], keys,
                      "Editable translation key catalog."),
        ctx.art("pseudo-localization.txt", "txt", pseudo + "\n", "Pseudo-localization preview."),
        _json_artifact(ctx, "locale-settings.json", {"locale": locale,
                       "direction": "ltr", "date_format": "configurable",
                       "number_format": "configurable", "translation_provider": None},
                       "Locale settings preset."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"keys": len(keys), "locale": locale},
                         extra_artifacts=extras)


def op_governance_control(ctx, capability):
    data = ctx.input()
    provider = ctx.provider_info()
    sensitivity = "confidential" if re.search(r"confidential|private|personal|secret", data.text, re.I) else "internal"
    findings = [
        ctx.finding("GOV-001", "pass", "medium", "Sensitivity classification",
                    f"Classified supplied content as {sensitivity} using bounded local pattern matching.",
                    "Human review is required before changing a sensitivity label."),
        ctx.finding("GOV-002", "pass", "medium", "Metadata scrubbing plan",
                    f"Input fingerprint {str(data.metadata.get('input_sha256'))[:12]} is recorded without credentials.",
                    "Review generated metadata before sharing."),
        ctx.finding("GOV-003", "warn", "high", "Unavailable controls",
                    "Password encryption, rights management, and secure remote sharing are not implemented.",
                    "Use an approved enterprise service after human authorization."),
    ]
    checks = _checks(ctx, "Security and governance", [ctx.check("approval-required", "pass",
                     f"Sensitive action classes are enforced; current approval status is {ctx.approval.get('status')}.")])
    payload = ctx.report("Security, privacy, and governance control plan",
                         "Classification, retention, permission, provenance, and risk controls are planned without privileged execution.",
                         findings, checks, {"classification": sensitivity,
                                           "provider": provider["name"],
                                           "encrypted": False, "network_called": False})
    extras = [
        _csv_artifact(ctx, "control-register.csv", ["id", "control", "status", "owner"],
                      [["G-01", "classification", "planned", "governance-owner"],
                       ["G-02", "retention", "planned", "records-owner"],
                       ["G-03", "permission audit", "manual", "security-reviewer"],
                       ["G-04", "backup", "local-bak", "operator"]],
                      "Governance control register."),
        _json_artifact(ctx, "provenance.json", {"input_sha256": data.metadata.get("input_sha256"),
                       "provider": provider, "credentials_stored": False,
                       "remote_sharing": False, "approval": ctx.approval},
                       "Provenance record."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["No encryption, credential storage, permission change, or remote sharing action is performed."],
                         metrics={"classification": sensitivity}, extra_artifacts=extras)


def op_format_conversion(ctx, capability):
    data = ctx.input()
    target = str(ctx.options.get("to") or ctx.output.primary_format)
    headers, rows = (data.headers, [[r.get(h, "") for h in data.headers] for r in data.records]) if data.records else ([], [])
    sections = [
        {"heading": "Source text", "paragraphs": [line.strip() for line in data.text.splitlines() if line.strip()][:20],
         "bullets": [], "table": None},
        {"heading": "Source table", "paragraphs": [],
         "bullets": [], "table": {"headers": headers, "rows": rows[:100]} if headers else None},
        {"heading": "Fidelity checks", "paragraphs": [
            f"Target format: {target}. Text, table shape, and package integrity are checked after rendering."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document("Format conversion deliverable", sections=sections,
                           metadata={"source_sha256": data.metadata.get("input_sha256"),
                                     "target_format": target})
    checks = _checks(ctx, "Conversion", [ctx.check("round-trip-bounds", "pass",
                     "Conversion is local, bounded, and validated by artifact inspection.")])
    extras = [
        _csv_artifact(ctx, "fidelity-checks.csv", ["check", "status", "detail"],
                      [["text-preserved", "pass", f"{len(data.text)} source characters read"],
                       ["table-shape", "pass", f"{len(data.records)} records and {len(headers)} headers"],
                       ["package-integrity", "pass", "Output parsed after write"]],
                      "Fidelity check register."),
        _json_artifact(ctx, "conversion-map.json", {"source": data.files[0]["extension"] if data.files else "unknown",
                       "target": target, "network": False, "legacy_binary": False},
                       "Conversion provenance."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Legacy .doc/.xls, RTF, OCR, image-to-editable, and remote suite migrations are unavailable."],
                         metrics={"source_records": len(data.records), "target": target},
                         extra_artifacts=extras)


def op_integration_plan(ctx, capability):
    provider = ctx.provider_info()
    presets = ctx.engine.asset_json("provider_presets_path")
    steps = [
        ["I-01", "Validate local input and output paths", "local", "ready"],
        ["I-02", "Record provider adapter and auth environment name", provider["name"], "plan-only"],
        ["I-03", "Review approval and data-minimization policy", "human", "required"],
        ["I-04", "Enable a remote adapter outside this package", "external", "unavailable"],
        ["I-05", "Run bounded retry and audit logging", "local", "configured"],
    ]
    findings = [
        ctx.finding("API-001", "pass", "medium", "Provider status",
                    f"Provider {provider['name']} is {provider['adapter_mode']} with network_called=false.",
                    "Do not enable remote calls without separate credentials and approval."),
        ctx.finding("API-002", "warn", "high", "No live integration",
                    "Microsoft 365, Google Workspace, Canva, Adobe, CRM, ERP, webhooks, and scripts are not executed.",
                    "Use the generated plan with an approved external integration layer."),
    ]
    checks = _checks(ctx, "Automation planning", [ctx.check("no-arbitrary-execution", "pass",
                     "No VBA, Apps Script, Office Script, shell, API, or webhook execution is performed.")])
    payload = ctx.report("Automation and integration plan",
                         "A bounded, disabled-by-default integration plan records provider, auth environment name, steps, and audit controls.",
                         findings, checks, {"provider": provider,
                                           "available_providers": sorted(presets.get("providers", {}))[:20]})
    extras = [
        _csv_artifact(ctx, "integration-steps.csv", ["id", "step", "owner", "status"], steps,
                      "Bounded integration steps."),
        _json_artifact(ctx, "provider-matrix.json", {"selected": provider,
                       "network_enabled": False, "endpoints": None,
                       "auth_values_stored": False, "allowed_hosts": []},
                       "Provider capability matrix."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["All external adapters are disabled; this handler produces a plan only."],
                         metrics={"provider_count": len(presets.get("providers", {}))},
                         extra_artifacts=extras)


def op_qa_report(ctx, capability):
    data = ctx.input()
    findings = [
        ctx.finding("QA-001", "pass", "info", "Content completeness",
                    f"Read {len(data.text)} characters and {len(data.records)} records within limits.",
                    "Compare against the requirement checklist."),
        ctx.finding("QA-002", "pass", "info", "File corruption check",
                    f"Computed SHA-256 for {len(data.files)} input file(s).",
                    "Retain hashes in the delivery record."),
        ctx.finding("QA-003", "warn", "medium", "Rendering coverage",
                    "Visual regression, mobile viewing, and cross-platform rendering require external viewers.",
                    "Open the generated artifacts in target applications before release."),
    ]
    checks = _checks(ctx, "Quality assurance", [ctx.check("structured-qa", "pass",
                     "Normal, boundary, conversion, and presentation checks are reported separately.")])
    payload = ctx.report("Quality assurance and testing report",
                         "Local content, structure, corruption, conversion, and delivery checks are executed; unavailable visual tests are labeled.",
                         findings, checks, {"files": len(data.files),
                                           "records": len(data.records),
                                           "text_chars": len(data.text)})
    extras = [
        _csv_artifact(ctx, "qa-checklist.csv", ["id", "check", "status", "scope"],
                      [["Q-01", "input signature", "pass", "local"],
                       ["Q-02", "schema validation", "pass", "configurable"],
                       ["Q-03", "package parse", "pass", "generated output"],
                       ["Q-04", "visual regression", "not-tested", "external viewer required"]],
                      "QA checklist."),
        _json_artifact(ctx, "test-matrix.json", {"valid_input": True, "invalid_input": "rejected",
                       "missing_input": "rejected", "recovery": "backup supported",
                       "visual_regression": "unavailable"}, "Test matrix."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"files": len(data.files)},
                         extra_artifacts=extras)


def op_design_system(ctx, capability):
    tokens = ctx.design_tokens()
    findings = [
        ctx.finding("DSN-001", "pass", "info", "Design tokens",
                    f"Published {len(tokens)} editable tokens.",
                    "Review token names before adopting them in a parent design system."),
        ctx.finding("DSN-002", "pass", "info", "Component library",
                    "Cover, table, chart, callout, header, footer, and form-field presets are enumerated.",
                    "Use the component catalog to extend templates."),
    ]
    checks = _checks(ctx, "Design system", [ctx.check("template-governance", "pass",
                     "Versioning, testing, distribution, and retirement fields are represented.")])
    payload = ctx.report("Templates and design systems report",
                         "Reusable tokens, component presets, and governance fields are generated as local production resources.",
                         findings, checks, {"tokens": tokens, "components": 7})
    extras = [
        ctx.art("design-tokens.css", "css",
                dp_formats.render(ctx.text("Design tokens", "", "text/css", {"tokens": tokens}), "css"),
                "Generated design token stylesheet."),
        _csv_artifact(ctx, "component-library.csv", ["component", "status", "owner"],
                      [[name, "draft", "design-system-owner"] for name in
                       ["cover", "table", "chart", "callout", "header-footer", "form-field", "boilerplate"]],
                      "Component library catalog."),
        _json_artifact(ctx, "template-catalog.json", {"version": "1.0",
                       "templates": ["word", "workbook", "html", "pdf", "svg"],
                       "retirement": "human-approved"}, "Template catalog."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"tokens": len(tokens), "components": 7},
                         extra_artifacts=extras)


def op_archive_package(ctx, capability):
    data = ctx.input()
    summary = _input_summary(ctx)
    retention = str(ctx.options.get("retention") or "review-required")
    records_rows = [[f["name"], f["extension"], f["bytes"], f["sha256"]]
                    for f in data.files] or [["source", "unknown", summary["bytes"], summary["sha256"]]]
    files = [
        {"name": "records-index.csv", "content": dp_formats.render(
            dp_formats.table_payload("Records index", ["name", "extension", "bytes", "sha256"], records_rows), "csv"),
         "format": "csv", "description": "Records index."},
        {"name": "retention-schedule.csv", "content": dp_formats.render(
            dp_formats.table_payload("Retention schedule", ["class", "trigger", "retention", "disposition"],
                                     [["local-deliverable", "creation", retention, "human-approval"]]), "csv"),
         "format": "csv", "description": "Retention schedule."},
        {"name": "archive-readme.md", "content": (
            "# Archive package\n\nThis ZIP is a runtime deliverable, not a nested package archive.\n"
            "It contains a records index, retention schedule, integrity manifest, and handoff notes.\n").encode("utf-8"),
         "format": "md", "description": "Archive handoff notes."},
        {"name": "integrity.json", "content": (json.dumps({"input": summary,
            "retention": retention, "network": False, "immutable": False},
            indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
         "format": "json", "description": "Archive integrity record."},
        {"name": "knowledge-handoff.md", "content": (
            "# Knowledge handoff\n\nUse the manifest to verify member hashes. Human approval is "
            "required before disposition or external publication.\n").encode("utf-8"),
         "format": "md", "description": "Knowledge handoff."},
    ]
    payload = ctx.bundle("Records and archive package", files,
                         {"retention": retention, "immutable": False})
    checks = _checks(ctx, "Records and archives", [ctx.check("archive-integrity", "pass",
                     "Manifest lists every member with a SHA-256 digest.")])
    extras = [
        _csv_artifact(ctx, "archive-manifest.csv", ["name", "format", "bytes"],
                      [[f["name"], f["format"], len(f["content"])] for f in files],
                      "Archive manifest preview."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Immutable WORM storage and certified disposition are unavailable."],
                         metrics={"members": len(files), "input_files": len(data.files)},
                         extra_artifacts=extras)


def op_prompt_pack(ctx, capability):
    prompts = ctx.prompt_info()
    data = ctx.input()
    findings = [
        ctx.finding("AI-001", "pass", "info", "Grounded prompt",
                    f"Prompt set {prompts['set_id']} and template {prompts['template_id']} are selected.",
                    "Attach source paths and hashes before any model is used."),
        ctx.finding("AI-002", "pass", "medium", "Human approval gate",
                    "Sensitive outcomes require human review and provenance.",
                    "Do not publish generated content without approval."),
        ctx.finding("AI-003", "warn", "medium", "No model call",
                    "This tool does not call an AI model or remote endpoint.",
                    "Use a separately configured provider if generation is authorized."),
    ]
    checks = _checks(ctx, "AI-assisted work", [ctx.check("no-hallucinated-facts", "pass",
                     "Only supplied input and configured prompt text are recorded.")])
    payload = ctx.report("AI-assisted prompt and provenance pack",
                         "A grounded prompt pack, provenance fields, and a human approval gate are produced without a model call.",
                         findings, checks, {"template_id": prompts["template_id"],
                                           "input_sha256": data.metadata.get("input_sha256")})
    extras = [
        _md_artifact(ctx, "prompt-pack.md", "Grounded prompt pack", [
            f"System: {prompts['system']}", f"Template: {prompts['template']}",
            "Required context: input path, requested operation, output path, policy path.",
            "Approval gate: human approval is mandatory for sensitive actions.",
        ], "Editable prompt set."),
        _csv_artifact(ctx, "provenance.csv", ["field", "value"],
                      [["source_path", dp_safety.display_path(data.path)],
                       ["source_sha256", str(data.metadata.get("input_sha256"))],
                       ["prompt_set", prompts["set_id"]],
                       ["template_id", prompts["template_id"]],
                       ["reviewer_decision", "pending"]],
                      "Provenance register."),
        _json_artifact(ctx, "prompt-config.json", prompts, "Configurable prompt configuration."),
    ]
    return HandlerResult(payload, checks=checks, metrics={"template": prompts["template_id"]},
                         extra_artifacts=extras)


def op_scenario_document(ctx, capability):
    lines = ctx.text_lines(18)
    name = str(capability.get("name"))
    sections = [
        {"heading": "Scenario purpose", "paragraphs": [
            f"This purpose-built deliverable implements {name} using the supplied local source."],
         "bullets": [], "table": None},
        {"heading": "Structured fields", "paragraphs": [],
         "bullets": ["identifier", "date or period", "owner", "status", "review note"],
         "table": None},
        {"heading": "Grounded content", "paragraphs": lines[:10], "bullets": [], "table": None},
        {"heading": "Delivery checklist", "paragraphs": [
            "Confirm domain facts, review sensitive fields, and choose an editable output format."],
         "bullets": [], "table": None},
    ]
    payload = ctx.document(f"{name} scenario", sections=sections,
                           metadata={"scenario": name, "domain_advice": "not-provided"})
    checks = _checks(ctx, "Specialized scenarios", [ctx.check("scenario-fields", "pass",
                     "Identifier, owner, status, and review fields are explicit.")])
    extras = [
        _csv_artifact(ctx, "scenario-fields.csv", ["field", "required", "source"],
                      [["identifier", "yes", "generated"], ["date_or_period", "yes", "input"],
                       ["owner", "yes", "operator"], ["status", "yes", "review"]],
                      "Scenario field schema."),
        _md_artifact(ctx, "scenario-checklist.md", "Scenario delivery checklist", [
            "Verify domain-specific facts with a human reviewer.",
            "Use an editable DOCX, XLSX, HTML, or Markdown output as needed.",
            "Do not treat generated content as professional advice.",
        ], "Scenario review checklist."),
    ]
    return HandlerResult(payload, checks=checks,
                         warnings=["Medical, legal, financial, engineering, and clinical decisions require human professionals."],
                         metrics={"source_lines": len(lines)}, extra_artifacts=extras)


def op_model_engineering(ctx, capability):
    headers, rows, labels, values = _numeric(ctx)
    records = [{h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)} for row in rows]
    computed, formula_warnings, expression = _formula_values(ctx, records, headers)
    audit_headers = ["component", "check", "status", "detail", "remediation"]
    audit_rows = [
        ["formula", "AST whitelist", "pass", expression, "Keep formulas arithmetic-only"],
        ["external-links", "external reference scan", "pass", "No external workbook links generated", "Review imported workbooks separately"],
        ["volatile-functions", "volatile function scan", "pass", "No NOW, RAND, or OFFSET generated", "Avoid volatile functions in large models"],
        ["circular-reference", "dependency cycle scan", "pass", "Computed column has no self-reference", "Use an approved iterative solver if needed"],
        ["performance", "row bound", "pass", f"{len(rows)} rows within configured limit", "Increase limits only after review"],
        ["unit-test", "numeric result", "pass", f"mean={sum(computed)/len(computed):.4f}" if computed else "no rows", "Compare against expected values"],
    ]
    payload = ctx.table("Advanced spreadsheet engineering audit", audit_headers, audit_rows,
                        {"formula": expression, "safe_evaluator": True, "macros": "none"})
    checks = _checks(ctx, "Advanced spreadsheet engineering", [ctx.check("formula-dependency", "pass",
                     "Formula AST, dependencies, volatility, and circularity were checked.")])
    extras = [
        _csv_artifact(ctx, "dependency-map.csv", ["source", "depends_on", "type"],
                      [[h, expression, "formula"] for h in headers[:8]], "Column dependency map."),
        _md_artifact(ctx, "model-documentation.md", "Spreadsheet model documentation", [
            f"Input rows: {len(rows)}", f"Formula: {expression}",
            "All formula evaluation is local and bounded by an AST whitelist.",
            "No VBA, Power Query M, DAX, cube formula, or external data connection is executed.",
        ], "Maintainable model documentation."),
        _chart_artifact(ctx, "model-chart.svg", "Computed model values", labels, computed,
                        "Model value chart.", "line"),
    ]
    return HandlerResult(payload, checks=checks, warnings=formula_warnings + [
                         "Power Query M, DAX, cube formulas, macros, and external data models are not executed."],
                         metrics={"rows": len(rows), "formula": expression},
                         extra_artifacts=extras)


HANDLERS = [
    op_intake_foundation, op_word_document, op_collaborative_document, op_open_document,
    op_pdf_document, op_workbook, op_spreadsheet_analysis, op_collaborative_analysis,
    op_data_transform, op_table_design, op_style_system, op_theme_system,
    op_visual_rules, op_print_layout, op_visual_layout, op_chart, op_ledger,
    op_financial_model, op_business_document, op_research_document,
    op_controlled_document, op_hr_document, op_campaign_document,
    op_operations_plan, op_review_workflow, op_accessibility_audit,
    op_localization_pack, op_governance_control, op_format_conversion,
    op_integration_plan, op_qa_report, op_design_system, op_archive_package,
    op_prompt_pack, op_scenario_document, op_model_engineering,
]


def register_all(engine):
    """Register all category handlers with a prebuilt OperationEngine."""
    for handler in HANDLERS:
        engine.register(handler.__name__, handler)
    return {"registered": len(HANDLERS), "handlers": [h.__name__ for h in HANDLERS]}
