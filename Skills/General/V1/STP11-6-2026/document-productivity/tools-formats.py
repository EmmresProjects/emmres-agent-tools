#!/usr/bin/env python3
"""Local file-format readers, writers, and artifact inspection.

All formats are implemented with the Python standard library. Writers produce
modern OOXML/ODF ZIP packages, a minimal valid PDF 1.4 file, SVG, HTML, CSS,
CSV, Markdown, text, JSON, and ZIP bundles. No external service or command is
used.
"""
from __future__ import annotations

import ast
import csv
import html
import io
import json
import math
import mimetypes
import re
import statistics
import textwrap
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape

dp_safety = __import__("sys").modules["dp_safety"]
ToolError = dp_safety.ToolError
display_path = dp_safety.display_path
sha256_bytes = dp_safety.sha256_bytes
sha256_file = dp_safety.sha256_file
json_ready = dp_safety.json_ready

SUPPORTED_FORMATS = {
    "json", "csv", "html", "md", "txt", "css", "svg", "pdf",
    "docx", "xlsx", "odt", "ods", "zip",
}
TEXT_FORMATS = {"json", "csv", "html", "md", "txt", "css", "svg"}
ARCHIVE_FORMATS = {"zip", "docx", "xlsx", "odt", "ods"}


@dataclass
class Payload:
    kind: str
    title: str
    subtitle: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "subtitle": self.subtitle,
                "metadata": json_ready(self.metadata), "data": json_ready(self.data)}


@dataclass
class Artifact:
    name: str
    format: str
    content: bytes
    primary: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "format": self.format, "bytes": len(self.content),
                "sha256": sha256_bytes(self.content), "primary": self.primary,
                "description": self.description}


@dataclass
class InputData:
    path: Path
    is_directory: bool
    files: list[dict[str, Any]]
    text: str
    records: list[dict[str, Any]]
    headers: list[str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"path": display_path(self.path), "is_directory": self.is_directory,
                "file_count": len(self.files), "files": self.files,
                "text_chars": len(self.text), "record_count": len(self.records),
                "headers": self.headers, "metadata": self.metadata}


def document_payload(title: str, subtitle: str, sections: list[dict[str, Any]],
                     metadata: dict[str, Any] | None = None) -> Payload:
    return Payload("document", title, subtitle, metadata or {}, {"sections": sections})


def table_payload(title: str, headers: list[str], rows: list[list[Any]],
                  metadata: dict[str, Any] | None = None) -> Payload:
    return Payload("table", title, "", metadata or {},
                   {"headers": [str(h) for h in headers],
                    "rows": [[_cell(v) for v in row] for row in rows]})


def report_payload(title: str, summary: str, findings: list[dict[str, Any]],
                   checks: list[dict[str, Any]], metrics: dict[str, Any],
                   metadata: dict[str, Any] | None = None) -> Payload:
    return Payload("report", title, summary, metadata or {},
                   {"summary": summary, "findings": findings,
                    "checks": checks, "metrics": metrics})


def chart_payload(title: str, labels: list[str], series: list[dict[str, Any]],
                  chart_type: str = "bar", metadata: dict[str, Any] | None = None) -> Payload:
    return Payload("chart", title, "", metadata or {},
                   {"labels": [str(x) for x in labels],
                    "series": [{"name": str(s.get("name", "Series")),
                                "values": [_number(v) for v in s.get("values", [])]}
                               for s in series],
                    "chart_type": chart_type})


def text_payload(title: str, text: str, media_type: str = "text/plain",
                 metadata: dict[str, Any] | None = None) -> Payload:
    meta = metadata or {}
    meta["media_type"] = media_type
    return Payload("text", title, "", meta, {"text": text})


def bundle_payload(title: str, files: list[dict[str, Any]],
                   metadata: dict[str, Any] | None = None) -> Payload:
    safe = []
    for item in files:
        name = str(item.get("name", ""))
        if not name or ".." in Path(name).parts or name.startswith("/"):
            raise ToolError("invalid_bundle_name", "Bundle member names must be relative and safe.",
                            {"name": name})
        content = item.get("content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        safe.append({"name": name, "content": content,
                     "format": str(item.get("format", Path(name).suffix.lstrip(".") or "txt")),
                     "description": str(item.get("description", ""))})
    return Payload("bundle", title, "", metadata or {}, {"files": safe})


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        result = float(text)
        return result if math.isfinite(result) else default
    except ValueError:
        return default


def _coerce(value: Any) -> Any:
    if not isinstance(value, str):
        return _cell(value)
    text = value.strip()
    if text == "":
        return ""
    if re.fullmatch(r"[+-]?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def coerce_table(headers: list[str], rows: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    clean_headers = [str(h).strip() for h in headers]
    clean_rows = [[_coerce(v) for v in row] for row in rows]
    width = len(clean_headers)
    if width == 0 and clean_rows:
        width = max(len(r) for r in clean_rows)
        clean_headers = [f"column_{i+1}" for i in range(width)]
    for row in clean_rows:
        while len(row) < width:
            row.append("")
        del row[width:]
    return clean_headers, clean_rows


def _html_text(markup: str) -> str:
    class Extractor(HTMLParser):
        BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                 "tr", "td", "th", "section", "article", "ol", "ul", "header", "footer"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.skip += 1
            elif tag in self.BLOCK:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in {"script", "style"} and self.skip:
                self.skip -= 1
            elif tag in self.BLOCK:
                self.parts.append("\n")

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    parser = Extractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _xlsx_col(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _xml_text(value: Any) -> str:
    return xml_escape(str(value if value is not None else ""))


def _rows_from_payload(payload: Payload) -> tuple[list[str], list[list[Any]]]:
    data = payload.data
    if payload.kind == "table":
        return coerce_table(data.get("headers", []), data.get("rows", []))
    if payload.kind == "chart":
        labels = data.get("labels", [])
        series = data.get("series", [])
        headers = ["label"] + [str(s.get("name", "series")) for s in series]
        rows = []
        for i, label in enumerate(labels):
            rows.append([label] + [_number(s.get("values", [0])[i] if i < len(s.get("values", [])) else 0)
                                   for s in series])
        return coerce_table(headers, rows)
    if payload.kind == "report":
        findings = data.get("findings", [])
        headers = ["id", "status", "severity", "title", "detail", "action"]
        rows = [[f.get("id", ""), f.get("status", ""), f.get("severity", ""),
                 f.get("title", ""), f.get("detail", ""), f.get("action", "")] for f in findings]
        return coerce_table(headers, rows)
    if payload.kind == "bundle":
        files = data.get("files", [])
        return coerce_table(["name", "format", "bytes", "description"],
                            [[f["name"], f["format"], len(f["content"]), f.get("description", "")] for f in files])
    if payload.kind == "document":
        headers = ["section", "type", "content"]
        rows = []
        for section in data.get("sections", []):
            heading = section.get("heading", "")
            for p in section.get("paragraphs", []):
                rows.append([heading, "paragraph", p])
            for b in section.get("bullets", []):
                rows.append([heading, "bullet", b])
            table = section.get("table")
            if table:
                rows.append([heading, "table", json.dumps(table, ensure_ascii=False)])
        return coerce_table(headers, rows)
    return coerce_table(["title", "subtitle", "text"],
                        [[payload.title, payload.subtitle, data.get("text", "")]])


def _sections_from_payload(payload: Payload) -> list[dict[str, Any]]:
    data = payload.data
    if payload.kind == "document":
        return data.get("sections", [])
    sections: list[dict[str, Any]] = []
    if payload.kind == "table":
        headers, rows = _rows_from_payload(payload)
        sections.append({"heading": "Data", "paragraphs": [], "bullets": [],
                         "table": {"headers": headers, "rows": rows}})
    elif payload.kind == "report":
        sections.append({"heading": "Summary", "paragraphs": [data.get("summary", "")],
                         "bullets": [], "table": None})
        findings = data.get("findings", [])
        if findings:
            sections.append({"heading": "Findings", "paragraphs": [], "bullets": [],
                             "table": {"headers": ["ID", "Status", "Severity", "Title", "Detail", "Action"],
                                       "rows": [[f.get("id", ""), f.get("status", ""), f.get("severity", ""),
                                                 f.get("title", ""), f.get("detail", ""), f.get("action", "")]
                                                for f in findings]}})
        checks = data.get("checks", [])
        if checks:
            sections.append({"heading": "Checks", "paragraphs": [], "bullets": [],
                             "table": {"headers": ["Name", "Status", "Detail"],
                                       "rows": [[c.get("name", ""), c.get("status", ""), c.get("detail", "")]
                                                for c in checks]}})
        metrics = data.get("metrics", {})
        if metrics:
            sections.append({"heading": "Metrics", "paragraphs": [], "bullets": [],
                             "table": {"headers": ["Metric", "Value"],
                                       "rows": [[k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v]
                                                for k, v in metrics.items()]}})
    elif payload.kind == "chart":
        headers, rows = _rows_from_payload(payload)
        sections.append({"heading": "Chart data", "paragraphs": [], "bullets": [],
                         "table": {"headers": headers, "rows": rows}})
        sections.append({"heading": "Chart", "paragraphs": [], "bullets": [],
                         "chart_svg": _svg_chart(payload)})
    elif payload.kind == "bundle":
        headers, rows = _rows_from_payload(payload)
        sections.append({"heading": "Bundle contents", "paragraphs": [], "bullets": [],
                         "table": {"headers": headers, "rows": rows}})
    else:
        text = str(data.get("text", ""))
        if text.lstrip().startswith("<svg"):
            sections.append({"heading": payload.title, "paragraphs": [], "bullets": [],
                             "table": None, "chart_svg": _sanitize_svg(text)})
        else:
            sections.append({"heading": "Content",
                             "paragraphs": text.splitlines() or [payload.title],
                             "bullets": [], "table": None})
    return sections


def _lines_from_payload(payload: Payload) -> list[str]:
    lines = [payload.title]
    if payload.subtitle:
        lines.append(payload.subtitle)
    lines.append("")
    if payload.metadata:
        for key, value in payload.metadata.items():
            lines.append(f"{key}: {value}")
        lines.append("")
    for section in _sections_from_payload(payload):
        heading = str(section.get("heading", "")).strip()
        if heading:
            lines.append(heading)
            lines.append("-" * min(80, max(3, len(heading))))
        for paragraph in section.get("paragraphs", []):
            lines.extend(textwrap.wrap(str(paragraph), 100) or [""])
        for bullet in section.get("bullets", []):
            lines.append("- " + str(bullet))
        table = section.get("table")
        if table:
            headers = [str(h) for h in table.get("headers", [])]
            rows = table.get("rows", [])
            if headers:
                lines.append(" | ".join(headers))
                lines.append("-+-".join("-" * max(3, len(h)) for h in headers))
            for row in rows:
                lines.append(" | ".join(str(v) for v in row))
        if section.get("chart_svg"):
            lines.append("[SVG chart embedded in HTML/PDF render; chart data appears above.]")
        lines.append("")
    return lines


def to_json(payload: Payload) -> bytes:
    return (json.dumps(payload.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def to_markdown(payload: Payload) -> bytes:
    out = [f"# {payload.title}"]
    if payload.subtitle:
        out.append("")
        out.append(payload.subtitle)
    if payload.metadata:
        out.append("")
        for key, value in payload.metadata.items():
            out.append(f"- **{key}:** {value}")
    for section in _sections_from_payload(payload):
        heading = str(section.get("heading", "")).strip()
        if heading:
            out.extend(["", f"## {heading}"])
        for paragraph in section.get("paragraphs", []):
            out.extend(["", str(paragraph)])
        for bullet in section.get("bullets", []):
            out.append(f"- {bullet}")
        table = section.get("table")
        if table:
            headers = [str(h) for h in table.get("headers", [])]
            rows = table.get("rows", [])
            if headers:
                out.extend(["", "| " + " | ".join(headers) + " |",
                            "| " + " | ".join("---" for _ in headers) + " |"])
                for row in rows:
                    out.append("| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |")
        if section.get("chart_svg"):
            out.append("")
            out.append("```xml")
            out.append(str(section["chart_svg"])[:10000])
            out.append("```")
    return ("\n".join(out) + "\n").encode("utf-8")


def to_text(payload: Payload) -> bytes:
    return ("\n".join(_lines_from_payload(payload)) + "\n").encode("utf-8")


def to_csv(payload: Payload) -> bytes:
    headers, rows = _rows_from_payload(payload)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    if headers:
        writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _html_table(headers: list[Any], rows: list[list[Any]]) -> str:
    if not headers:
        return ""
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>"
                   for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def to_html(payload: Payload, template: str | None = None, options: dict[str, Any] | None = None) -> bytes:
    options = options or {}
    body_parts: list[str] = []
    for section in _sections_from_payload(payload):
        heading = str(section.get("heading", "")).strip()
        if heading:
            body_parts.append(f"<h2>{html.escape(heading)}</h2>")
        for paragraph in section.get("paragraphs", []):
            body_parts.append(f"<p>{html.escape(str(paragraph)).replace(chr(10), '<br>')}</p>")
        bullets = section.get("bullets", [])
        if bullets:
            body_parts.append("<ul>" + "".join(f"<li>{html.escape(str(b))}</li>" for b in bullets) + "</ul>")
        table = section.get("table")
        if table:
            body_parts.append(_html_table(table.get("headers", []), table.get("rows", [])))
        if section.get("chart_svg"):
            body_parts.append(f'<div class="chart">{section["chart_svg"]}</div>')
    checks = payload.data.get("checks", []) if payload.kind == "report" else []
    for item in checks:
        body_parts.append(f'<div class="check"><strong>{html.escape(str(item.get("name", "check")))}</strong>: '
                          f'{html.escape(str(item.get("status", "")))} — {html.escape(str(item.get("detail", "")))}</div>')
    body = "\n".join(body_parts) or f"<p>{html.escape(payload.subtitle or payload.title)}</p>"
    metadata = ", ".join(f"{html.escape(str(k))}={html.escape(str(v))}" for k, v in payload.metadata.items())
    default_template = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{TITLE}}</title>
<style>body{font:16px/1.5 Arial,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#172033}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9e0ea;padding:.5rem;text-align:left}th{background:#f5f8fc}h2{color:#2457a6}.check{background:#f5f8fc;border-left:4px solid #2457a6;padding:.5rem .75rem;margin:.4rem 0}</style></head>
<body><h1>{{TITLE}}</h1><p class="subtitle">{{SUBTITLE}}</p><main>{{BODY}}</main><footer>{{GENERATED_BY}}<br>{{METADATA}}</footer></body></html>"""
    source = template or default_template
    rendered = (source
                .replace("{{TITLE}}", html.escape(payload.title))
                .replace("{{SUBTITLE}}", html.escape(payload.subtitle))
                .replace("{{BODY}}", body)
                .replace("{{GENERATED_BY}}", html.escape(str(options.get("generated_by", "document-productivity Skill Tool"))))
                .replace("{{METADATA}}", metadata))
    return rendered.encode("utf-8")


def to_css(payload: Payload) -> bytes:
    if payload.kind == "text" and "text" in payload.data:
        return str(payload.data["text"]).encode("utf-8")
    tokens = payload.data.get("tokens", {}) or payload.metadata.get("tokens", {})
    lines = [f"/* {payload.title} */", ":root {"]
    for key, value in tokens.items():
        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "-", str(key))
        lines.append(f"  --{safe_key}: {value};")
    lines.append("}")
    lines.append("body { font-family: var(--font-body, Arial, sans-serif); color: var(--color-text, #172033); }")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _svg_chart(payload: Payload) -> str:
    if payload.kind == "chart":
        labels = payload.data.get("labels", [])
        series = payload.data.get("series", [])
        chart_type = payload.data.get("chart_type", "bar")
    else:
        headers, rows = _rows_from_payload(payload)
        labels = [str(r[0]) if r else "" for r in rows[:20]]
        if len(headers) >= 2:
            values = [_number(r[1]) for r in rows[:20]]
        else:
            values = [float(i + 1) for i in range(len(rows[:20]))]
        series = [{"name": headers[1] if len(headers) >= 2 else "value", "values": values}]
        chart_type = "bar"
    width, height = 900, 480
    left, right, top, bottom = 80, 30, 70, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    all_values = [v for s in series for v in [_number(x) for x in s.get("values", [])]]
    vmax = max([0.0] + all_values)
    vmin = min([0.0] + all_values)
    if vmax == vmin:
        vmax += 1.0
    span = vmax - vmin
    def y(value: float) -> float:
        return top + plot_h - ((value - vmin) / span) * plot_h
    zero_y = y(0.0)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{xml_escape(payload.title)}">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             f'<text x="{width/2}" y="32" text-anchor="middle" font-family="Arial" font-size="20" fill="#172033">{xml_escape(payload.title)}</text>',
             f'<line x1="{left}" y1="{zero_y}" x2="{width-right}" y2="{zero_y}" stroke="#8a94a6"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#8a94a6"/>',
             f'<text x="16" y="{top+4}" font-family="Arial" font-size="12" fill="#5b667a">{vmax:.2f}</text>',
             f'<text x="16" y="{height-bottom+4}" font-family="Arial" font-size="12" fill="#5b667a">{vmin:.2f}</text>']
    n = max(1, len(labels))
    colors = ["#2457a6", "#d97706", "#16836b", "#9b3f95", "#b54747"]
    slot = plot_w / n
    if chart_type == "line":
        for si, s in enumerate(series):
            values = [_number(v) for v in s.get("values", [])][:n]
            points = " ".join(f"{left + slot*(i+0.5):.1f},{y(v):.1f}" for i, v in enumerate(values))
            parts.append(f'<polyline fill="none" stroke="{colors[si % len(colors)]}" stroke-width="3" points="{points}"/>')
    else:
        group_count = max(1, len(series))
        bar_w = max(2.0, slot * 0.72 / group_count)
        for si, s in enumerate(series):
            values = [_number(v) for v in s.get("values", [])][:n]
            for i, v in enumerate(values):
                x = left + slot * i + slot * 0.14 + si * bar_w
                yv = y(v)
                top_y = min(yv, zero_y)
                bar_h = max(1.0, abs(yv - zero_y))
                parts.append(f'<rect x="{x:.1f}" y="{top_y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{colors[si % len(colors)]}" opacity="0.88"/>')
    for i, label in enumerate(labels[:20]):
        x = left + slot * (i + 0.5)
        short = str(label)[:14]
        parts.append(f'<text x="{x:.1f}" y="{height-bottom+22}" text-anchor="end" transform="rotate(-35 {x:.1f},{height-bottom+22})" font-family="Arial" font-size="11" fill="#5b667a">{xml_escape(short)}</text>')
    for si, s in enumerate(series):
        parts.append(f'<rect x="{left + si*160}" y="{height-30}" width="12" height="12" fill="{colors[si % len(colors)]}"/>')
        parts.append(f'<text x="{left + si*160 + 18}" y="{height-20}" font-family="Arial" font-size="12" fill="#172033">{xml_escape(str(s.get("name", "Series")))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _sanitize_svg(markup: str) -> str:
    """Keep generated SVG inline while removing active content and remote refs."""
    clean = re.sub(r"(?is)<script.*?</script>", "", markup)
    clean = re.sub(r"(?is)<foreignObject.*?</foreignObject>", "", clean)
    clean = re.sub(r"(?i)\son[a-z]+\s*=\s*(['\"]).*?\1", "", clean)
    clean = re.sub(r"(?i)javascript:", "", clean)
    clean = re.sub(r"(?i)(xlink:href|href)\s*=\s*(['\"])https?://.*?\2", "", clean)
    return clean.strip()


def to_svg(payload: Payload) -> bytes:
    if payload.kind == "text":
        text = str(payload.data.get("text", ""))
        if text.lstrip().startswith("<svg"):
            return (_sanitize_svg(text) + "\n").encode("utf-8")
    return (_svg_chart(payload) + "\n").encode("utf-8")


def _pdf_escape(text: str) -> str:
    clean = "".join(ch if 32 <= ord(ch) <= 126 else "?" for ch in text)
    return clean.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def to_pdf(payload: Payload) -> bytes:
    raw_lines = _lines_from_payload(payload)
    lines: list[str] = []
    for line in raw_lines:
        wrapped = textwrap.wrap(line, 96) or [""]
        lines.extend(wrapped)
    per_page = 48
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[""]]
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    catalog_id = add(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add(b"")  # placeholder, fixed below
    page_ids: list[int] = []
    content_ids: list[int] = []
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for page_lines in pages:
        commands = ["BT", "/F1 10 Tf", "14 TL", "1 0 0 1 48 750 Tm"]
        for line in page_lines:
            commands.append(f"({_pdf_escape(line)}) Tj T*")
        commands.append("ET")
        stream = ("\n".join(commands) + "\n").encode("latin-1", "replace")
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream")
        page_id = add(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" %
                       (font_id, content_id)).encode("latin-1"))
        content_ids.append(content_id)
        page_ids.append(page_id)
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>").encode("latin-1")
    info_id = add(("<< /Title (" + _pdf_escape(payload.title) + ") /Producer (document-productivity) "
                   "/Creator (document-productivity Skill Tool) >>").encode("latin-1"))
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode("latin-1"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects)+1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    out.write(("trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" %
               (len(objects) + 1, catalog_id, info_id, xref)).encode("latin-1"))
    return out.getvalue()


def _docx_parts(payload: Payload) -> dict[str, bytes]:
    sections = _sections_from_payload(payload)
    body: list[str] = []
    for section in sections:
        heading = str(section.get("heading", "")).strip()
        if heading:
            body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{_xml_text(heading)}</w:t></w:r></w:p>')
        for paragraph in section.get("paragraphs", []):
            body.append(f'<w:p><w:r><w:t xml:space="preserve">{_xml_text(paragraph)}</w:t></w:r></w:p>')
        for bullet in section.get("bullets", []):
            body.append(f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t xml:space="preserve">{_xml_text(bullet)}</w:t></w:r></w:p>')
        table = section.get("table")
        if table:
            rows = table.get("rows", [])
            headers = table.get("headers", [])
            body.append('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>')
            for row in [headers] + rows:
                body.append("<w:tr>" + "".join(
                    f'<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr><w:p><w:r><w:t xml:space="preserve">{_xml_text(v)}</w:t></w:r></w:p></w:tc>'
                    for v in row) + "</w:tr>")
            body.append("</w:tbl>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) +
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
                '</w:body></w:document>').encode("utf-8")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '</w:styles>').encode("utf-8")
    numbering = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>').encode("utf-8")
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                     '</Types>').encode("utf-8")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '</Relationships>').encode("utf-8")
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                '</Relationships>').encode("utf-8")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_xml_text(payload.title)}</dc:title><dc:creator>document-productivity</dc:creator>'
            '</cp:coreProperties>').encode("utf-8")
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           '<Application>document-productivity Skill Tool</Application></Properties>').encode("utf-8")
    return {"[Content_Types].xml": content_types, "_rels/.rels": rels,
            "word/document.xml": document, "word/styles.xml": styles,
            "word/numbering.xml": numbering, "word/_rels/document.xml.rels": doc_rels,
            "docProps/core.xml": core, "docProps/app.xml": app}


def to_docx(payload: Payload) -> bytes:
    return _zip_bytes(_docx_parts(payload))


def _xlsx_parts(payload: Payload) -> dict[str, bytes]:
    headers, rows = _rows_from_payload(payload)
    all_rows = [headers] + rows
    sheet_rows: list[str] = []
    for r_index, row in enumerate(all_rows, 1):
        cells = []
        for c_index, value in enumerate(row):
            ref = f"{_xlsx_col(c_index)}{r_index}"
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{r_index}">' + "".join(cells) + "</row>")
    dimension = f"A1:{_xlsx_col(max(0, len(headers)-1))}{max(1, len(all_rows))}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="{dimension}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/>'
             '<sheetData>' + "".join(sheet_rows) + '</sheetData>'
             '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
             '</worksheet>').encode("utf-8")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>').encode("utf-8")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>').encode("utf-8")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
              '</styleSheet>').encode("utf-8")
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                     '</Types>').encode("utf-8")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '</Relationships>').encode("utf-8")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>'
            f'{_xml_text(payload.title)}</dc:title><dc:creator>document-productivity</dc:creator></cp:coreProperties>').encode("utf-8")
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           '<Application>document-productivity Skill Tool</Application></Properties>').encode("utf-8")
    return {"[Content_Types].xml": content_types, "_rels/.rels": rels,
            "xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": wb_rels,
            "xl/worksheets/sheet1.xml": sheet, "xl/styles.xml": styles,
            "docProps/core.xml": core, "docProps/app.xml": app}


def to_xlsx(payload: Payload) -> bytes:
    return _zip_bytes(_xlsx_parts(payload))


ODF_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
          'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
          'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
          'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
          'xmlns:dc="http://purl.org/dc/elements/1.1/"')


def _odf_manifest(mimetype: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
            '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
            '</manifest:manifest>').encode("utf-8")


def _odf_common(payload: Payload) -> dict[str, bytes]:
    meta = ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-meta {ODF_NS} office:version="1.2">'
            f'<office:meta><dc:title>{_xml_text(payload.title)}</dc:title>'
            '<meta:generator>document-productivity Skill Tool</meta:generator></office:meta>'
            '</office:document-meta>').encode("utf-8")
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              f'<office:document-styles {ODF_NS} office:version="1.2">'
              '<office:styles><style:style style:name="Standard" style:family="paragraph">'
              '<style:paragraph-properties fo:margin-bottom="0.15in"/></style:style>'
              '<style:style style:name="Heading_20_1" style:display-name="Heading 1" style:family="paragraph" '
              'style:parent-style-name="Standard" style:next-style-name="Standard">'
              '<style:text-properties fo:font-size="16pt" fo:font-weight="bold"/></style:style>'
              '</office:styles></office:document-styles>').encode("utf-8")
    return {"meta.xml": meta, "styles.xml": styles, "META-INF/manifest.xml": b""}


def to_odt(payload: Payload) -> bytes:
    common = _odf_common(payload)
    parts: list[str] = []
    for section in _sections_from_payload(payload):
        heading = str(section.get("heading", "")).strip()
        if heading:
            parts.append(f'<text:h text:outline-level="1">{_xml_text(heading)}</text:h>')
        for paragraph in section.get("paragraphs", []):
            parts.append(f'<text:p>{_xml_text(paragraph)}</text:p>')
        for bullet in section.get("bullets", []):
            parts.append(f'<text:list><text:list-item><text:p>{_xml_text(bullet)}</text:p></text:list-item></text:list>')
        table = section.get("table")
        if table:
            parts.append('<table:table table:name="Data">')
            for row in [table.get("headers", [])] + table.get("rows", []):
                parts.append('<table:table-row>' + "".join(
                    f'<table:table-cell office:value-type="string"><text:p>{_xml_text(v)}</text:p></table:table-cell>'
                    for v in row) + '</table:table-row>')
            parts.append('</table:table>')
    content = ('<?xml version="1.0" encoding="UTF-8"?>'
               f'<office:document-content {ODF_NS} office:version="1.2">'
               '<office:automatic-styles/><office:body><office:text>' +
               "".join(parts) + '</office:text></office:body></office:document-content>').encode("utf-8")
    mimetype = "application/vnd.oasis.opendocument.text"
    common["META-INF/manifest.xml"] = _odf_manifest(mimetype)
    return _zip_bytes({"mimetype": mimetype.encode("ascii"), "content.xml": content,
                       **common}, first_uncompressed="mimetype")


def to_ods(payload: Payload) -> bytes:
    common = _odf_common(payload)
    headers, rows = _rows_from_payload(payload)
    cells: list[str] = []
    for row in [headers] + rows:
        cells.append("<table:table-row>" + "".join(
            (f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{_xml_text(v)}</text:p></table:table-cell>'
             if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))
             else f'<table:table-cell office:value-type="string"><text:p>{_xml_text(v)}</text:p></table:table-cell>')
            for v in row) + "</table:table-row>")
    content = ('<?xml version="1.0" encoding="UTF-8"?>'
               f'<office:document-content {ODF_NS} office:version="1.2">'
               '<office:automatic-styles/><office:body><office:spreadsheet>'
               '<table:table table:name="Data">' + "".join(cells) + '</table:table>'
               '</office:spreadsheet></office:body></office:document-content>').encode("utf-8")
    mimetype = "application/vnd.oasis.opendocument.spreadsheet"
    common["META-INF/manifest.xml"] = _odf_manifest(mimetype)
    return _zip_bytes({"mimetype": mimetype.encode("ascii"), "content.xml": content,
                       **common}, first_uncompressed="mimetype")


def _zip_bytes(parts: dict[str, bytes], first_uncompressed: str | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        names = list(parts.keys())
        if first_uncompressed and first_uncompressed in parts:
            names.remove(first_uncompressed)
            names.insert(0, first_uncompressed)
        for name in names:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if name == first_uncompressed else zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, parts[name])
    return stream.getvalue()


def to_zip(payload: Payload, extra_files: list[dict[str, Any]] | None = None) -> bytes:
    members: list[dict[str, Any]] = []
    if payload.kind == "bundle":
        members.extend(payload.data.get("files", []))
    else:
        members.append({"name": f"primary.{_extension(payload)}", "content": to_json(payload),
                        "format": "json", "description": "Structured primary payload"})
    for item in extra_files or []:
        members.append(item)
    manifest_members = [{"name": m["name"], "format": m.get("format", "bin"),
                         "bytes": len(m["content"]), "sha256": sha256_bytes(m["content"]),
                         "description": m.get("description", "")} for m in members]
    members.append({"name": "manifest.json", "content": json.dumps({
        "title": payload.title, "members": manifest_members,
    }, indent=2, ensure_ascii=False).encode("utf-8"), "format": "json",
        "description": "Archive integrity manifest"})
    stream = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            name = str(member["name"])
            pure = Path(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ToolError("invalid_bundle_name", "Unsafe archive member name.", {"name": name})
            if name in seen:
                raise ToolError("duplicate_bundle_name", "Duplicate archive member name.", {"name": name})
            seen.add(name)
            content = member["content"]
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(name, content)
    return stream.getvalue()


def _extension(payload: Payload) -> str:
    return {"text": "txt", "document": "md", "table": "csv", "report": "json",
            "chart": "svg", "bundle": "json"}.get(payload.kind, "json")


def render(payload: Payload, fmt: str, options: dict[str, Any] | None = None,
           template: str | None = None, extra_files: list[dict[str, Any]] | None = None) -> bytes:
    fmt = fmt.lower().lstrip(".")
    options = options or {}
    renderers = {
        "json": to_json, "md": to_markdown, "markdown": to_markdown, "txt": to_text,
        "text": to_text, "csv": to_csv, "html": lambda p: to_html(p, template, options),
        "css": to_css, "svg": to_svg, "pdf": to_pdf, "docx": to_docx,
        "xlsx": to_xlsx, "odt": to_odt, "ods": to_ods,
        "zip": lambda p: to_zip(p, extra_files),
    }
    if fmt not in renderers:
        raise ToolError("unsupported_format", f"Format '{fmt}' is not supported.",
                        {"format": fmt, "supported": sorted(SUPPORTED_FORMATS)})
    return renderers[fmt](payload)


# ----------------------------- Readers -------------------------------------

def _sniff_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        return "\t" if "\t" in text[:4096] else ","


def _csv_records(text: str, delimiter: str | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, delimiter=delimiter or _sniff_delimiter(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    records = []
    for row in reader:
        records.append({(k or "").strip(): (v if isinstance(v, str) else "").strip()
                        for k, v in row.items()})
    return headers, records


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, Any]], str]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        sheet_names = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        if not sheet_names:
            return [], [], ""
        root = ET.fromstring(archive.read(sorted(sheet_names)[0]))
        rows: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                ctype = cell.get("t")
                if ctype == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter("{%s}t" % ns["m"]))
                else:
                    v = cell.find("m:v", ns)
                    value = v.text or "" if v is not None else ""
                    if ctype == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                values.append(value)
            rows.append(values)
    if not rows:
        return [], [], ""
    width = max(len(r) for r in rows)
    headers = [rows[0][i] if i < len(rows[0]) else f"column_{i+1}" for i in range(width)]
    records = []
    for row in rows[1:]:
        padded = row + [""] * (width - len(row))
        records.append({headers[i]: padded[i] for i in range(width)})
    text = "\n".join(" | ".join(r) for r in rows)
    return headers, records, text


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for p in root.iter(ns_w + "p"):
        text = "".join(t.text or "" for t in p.iter(ns_w + "t"))
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_odf(path: Path) -> tuple[list[str], list[dict[str, Any]], str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    table_ns = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    paragraphs = ["".join(p.itertext()).strip() for p in root.iter(text_ns + "p")]
    paragraphs = [p for p in paragraphs if p]
    rows: list[list[str]] = []
    for table in root.iter(table_ns + "table"):
        for row in table.iter(table_ns + "table-row"):
            cells = ["".join(c.itertext()).strip() for c in row.iter(table_ns + "table-cell")]
            if cells:
                rows.append(cells)
        break
    if rows:
        width = max(len(r) for r in rows)
        headers = [rows[0][i] if i < len(rows[0]) else f"column_{i+1}" for i in range(width)]
        records = []
        for row in rows[1:]:
            padded = row + [""] * (width - len(row))
            records.append({headers[i]: padded[i] for i in range(width)})
        return headers, records, "\n".join(paragraphs + [" | ".join(r) for r in rows])
    return [], [], "\n".join(paragraphs)


def _read_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise ToolError("invalid_pdf", "PDF signature is missing.", {"path": display_path(path)})
    page_count = len(re.findall(rb"/Type\s*/Page(?![s])", data))
    streams = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = match.group(1)
        if b"FlateDecode" in data[max(0, match.start()-300):match.start()]:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                pass
        streams.append(raw)
    texts: list[str] = []
    for raw in streams:
        decoded = raw.decode("latin-1", "replace")
        for literal in re.findall(r"\(((?:\\.|[^\\)])*)\)\s*Tj", decoded):
            texts.append(re.sub(r"\\([()\\])", r"\1", literal))
        for array in re.findall(r"\[(.*?)\]\s*TJ", decoded, re.S):
            texts.extend(re.sub(r"\\([()\\])", r"\1", x) for x in re.findall(r"\(((?:\\.|[^\\)])*)\)", array))
    text = "\n".join(t for t in texts if t.strip())
    return text, {"page_count": page_count, "stream_count": len(streams),
                  "has_eof": data.rstrip().endswith(b"%%EOF"),
                  "text_extraction": "limited-uncompressed-streams"}


def _read_zip(path: Path, limits: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], str]:
    entries = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist()[:int(limits.get("max_files", 1000))]:
            entries.append({"name": info.filename, "bytes": info.file_size,
                            "compressed_bytes": info.compress_size})
        csv_members = [i for i in archive.infolist() if i.filename.lower().endswith((".csv", ".tsv"))]
        if csv_members:
            text = archive.read(csv_members[0].filename).decode("utf-8", "replace")
            headers, records = _csv_records(text, "\t" if csv_members[0].filename.lower().endswith(".tsv") else None)
            return headers, records, text
    return [], [], json.dumps(entries, indent=2, ensure_ascii=False)


def _file_meta(path: Path, limits: dict[str, Any], guard=None) -> dict[str, Any]:
    if path.is_symlink():
        raise ToolError("unsafe_symlink", "Input symlinks are not accepted.",
                        {"path": display_path(path)})
    stat = path.stat()
    if stat.st_size > int(limits.get("max_input_bytes", 52428800)):
        raise ToolError("input_too_large", "Input exceeds the configured byte limit.",
                        {"path": display_path(path), "bytes": stat.st_size,
                         "max_bytes": limits.get("max_input_bytes")})
    ext = path.suffix.lower().lstrip(".")
    mime = mimetypes.guess_type(path.name)[0] or {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "odt": "application/vnd.oasis.opendocument.text",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
    }.get(ext, "application/octet-stream")
    signature = "not-checked"
    with path.open("rb") as handle:
        head = handle.read(8)
    if head.startswith(b"%PDF-"):
        signature = "pdf"
    elif head[:2] == b"PK":
        signature = "zip/ooxml/odf"
    elif head[:1] in (b"{", b"["):
        signature = "json"
    elif b"," in head or b"\t" in head:
        signature = "delimited-text"
    else:
        signature = "text-or-binary"
    return {"path": display_path(path), "name": path.name, "extension": ext,
            "mime": mime, "bytes": stat.st_size, "sha256": sha256_file(path),
            "signature": signature}


def read_input(path: Path, options: dict[str, Any] | None = None,
               limits: dict[str, Any] | None = None, guard=None) -> InputData:
    options = options or {}
    limits = limits or {}
    if not path.exists():
        raise ToolError("input_not_found", "Input path does not exist.",
                        {"path": display_path(path)})
    if path.is_symlink():
        raise ToolError("unsafe_symlink", "Input symlinks are not accepted.",
                        {"path": display_path(path)})
    max_chars = int(limits.get("max_text_chars", 2000000))
    files: list[dict[str, Any]] = []
    text = ""
    records: list[dict[str, Any]] = []
    headers: list[str] = []
    metadata: dict[str, Any] = {"reader_limitations": []}
    if path.is_dir():
        candidates = sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink())
        max_files = int(limits.get("max_files", 1000))
        if len(candidates) > max_files:
            raise ToolError("too_many_input_files", "Directory exceeds the configured file limit.",
                            {"count": len(candidates), "max_files": max_files})
        for candidate in candidates:
            if guard:
                guard.check("directory scan")
            files.append(_file_meta(candidate, limits, guard))
        first_table = next((Path(f["path"]) for f in files
                            if f["extension"] in {"csv", "tsv", "xlsx", "ods", "json", "jsonl", "zip"}
                            and Path(f["path"]).exists()), None)
        if first_table:
            nested = read_input(first_table, options, limits, guard)
            text, records, headers = nested.text, nested.records, nested.headers
        return InputData(path, True, files, text, records, headers, metadata)
    info = _file_meta(path, limits, guard)
    files.append(info)
    ext = info["extension"]
    if guard:
        guard.check("input read")
    if ext in {"csv", "tsv"}:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")[:max_chars]
        headers, records = _csv_records(raw, "\t" if ext == "tsv" else None)
        text = raw
    elif ext == "json":
        raw = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
            records = [{str(k): _coerce(v) for k, v in row.items()} for row in parsed]
            headers = list(records[0].keys())
        elif isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
            rows = parsed["rows"]
            headers = [str(h) for h in parsed.get("headers", [])]
            records = [{str(headers[i] if i < len(headers) else f"column_{i+1}"): _coerce(v)
                        for i, v in enumerate(row if isinstance(row, list) else [row])} for row in rows]
        text = raw
    elif ext == "jsonl":
        lines = path.read_text(encoding="utf-8", errors="replace")[:max_chars].splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append({str(k): _coerce(v) for k, v in obj.items()})
        headers = list(records[0].keys()) if records else []
        text = "\n".join(lines)
    elif ext == "xlsx":
        headers, records, text = _read_xlsx(path)
    elif ext == "docx":
        text = _read_docx(path)
    elif ext in {"odt", "ods"}:
        headers, records, text = _read_odf(path)
    elif ext == "pdf":
        text, pdf_meta = _read_pdf(path)
        metadata.update(pdf_meta)
        metadata["reader_limitations"].append(
            "PDF text extraction is limited to literal strings in readable streams; encrypted or complex PDFs are inspected structurally only.")
    elif ext in {"html", "htm"}:
        raw = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        text = _html_text(raw)
    elif ext in {"md", "markdown", "txt", "css", "svg", "xml"}:
        raw = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        text = _html_text(raw) if ext in {"svg", "xml"} else raw
    elif ext == "zip":
        headers, records, text = _read_zip(path, limits)
    else:
        raw = path.read_bytes()[:max_chars].decode("utf-8", "replace")
        text = raw
        metadata["reader_limitations"].append("Unknown extension was decoded as UTF-8 text with replacement.")
    if len(text) > max_chars:
        text = text[:max_chars]
        metadata["text_truncated"] = True
    metadata.update({"record_count": len(records), "header_count": len(headers),
                     "input_sha256": info["sha256"], "input_bytes": info["bytes"]})
    return InputData(path, False, files, text, records, headers, metadata)


def format_from_path(path: Path) -> str | None:
    ext = path.suffix.lower().lstrip(".")
    aliases = {"markdown": "md", "text": "txt", "htm": "html"}
    ext = aliases.get(ext, ext)
    return ext if ext in SUPPORTED_FORMATS else None


def is_artifact_path(path: Path) -> bool:
    return format_from_path(path) is not None


def validate_artifact(path: Path, guard=None) -> dict[str, Any]:
    if not path.is_file():
        raise ToolError("artifact_not_found", "Artifact does not exist.",
                        {"path": display_path(path)})
    fmt = format_from_path(path)
    if not fmt:
        raise ToolError("unsupported_artifact_format", "Artifact extension is not inspectable.",
                        {"path": display_path(path)})
    data = path.read_bytes()
    details: dict[str, Any] = {"format": fmt, "bytes": len(data),
                               "sha256": sha256_bytes(data)}
    warnings: list[str] = []
    if fmt == "json":
        json.loads(data.decode("utf-8"))
        details["parsed"] = "json"
    elif fmt == "csv":
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig", "replace")))
        details["rows"] = sum(1 for _ in reader)
    elif fmt in {"md", "txt", "css"}:
        decoded = data.decode("utf-8")
        details["lines"] = decoded.count("\n") + 1
    elif fmt == "html":
        parser = HTMLParser(convert_charrefs=True)
        parser.feed(data.decode("utf-8", "replace"))
        parser.close()
        details["parsed"] = "html"
    elif fmt == "svg":
        root = ET.fromstring(data)
        details["root_tag"] = root.tag
    elif fmt == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ToolError("invalid_artifact", "PDF header is missing.", {"path": display_path(path)})
        if not data.rstrip().endswith(b"%%EOF"):
            raise ToolError("invalid_artifact", "PDF EOF marker is missing.", {"path": display_path(path)})
        match = re.search(rb"startxref\s+(\d+)", data)
        if not match or int(match.group(1)) >= len(data):
            raise ToolError("invalid_artifact", "PDF startxref is invalid.", {"path": display_path(path)})
        details["pages"] = len(re.findall(rb"/Type\s*/Page(?![s])", data))
        details["startxref"] = int(match.group(1))
    elif fmt in {"zip", "docx", "xlsx", "odt", "ods"}:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ToolError("invalid_artifact", "ZIP member failed CRC validation.",
                                {"path": display_path(path), "member": bad})
            names = archive.namelist()
            details["members"] = len(names)
            if fmt == "docx":
                required = {"[Content_Types].xml", "word/document.xml"}
                ET.fromstring(archive.read("word/document.xml"))
            elif fmt == "xlsx":
                required = {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"}
                ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            elif fmt in {"odt", "ods"}:
                required = {"mimetype", "content.xml", "META-INF/manifest.xml"}
                ET.fromstring(archive.read("content.xml"))
                if archive.read("mimetype").decode("ascii") not in {
                    "application/vnd.oasis.opendocument.text",
                    "application/vnd.oasis.opendocument.spreadsheet"}:
                    raise ToolError("invalid_artifact", "ODF mimetype is incorrect.",
                                    {"path": display_path(path)})
            else:
                required = set()
            missing = required - set(names)
            if missing:
                raise ToolError("invalid_artifact", "Required package member is missing.",
                                {"path": display_path(path), "missing": sorted(missing)})
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(archive.read(name))
    return {"ok": True, "path": display_path(path), "details": details, "warnings": warnings}
