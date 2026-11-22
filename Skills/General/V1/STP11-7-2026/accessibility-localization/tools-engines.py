"""Analysis engines for the accessibility-localization Skill Tool.

All engines are deterministic, local, and standard-library only. They never
execute input code and never make network requests.
"""
from __future__ import annotations

import csv
import html as html_mod
import io
import json
import math
import os
import re
import struct
import unicodedata
import wave
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from tools_core import (
    Config, Finding, Markdown, ToolError, clamp_int, ensure_dir, file_kind,
    finding, human_bytes, normalize_findings, read_csv, read_json, read_text,
    short_hash, slugify, status_from_findings, utcnow, write_json, write_text,
    iter_files, SEVERITIES, SEVERITY_RANK,
)
from tools_templates import AT, DOCUMENTS, PROTOCOLS, REVIEW_TEMPLATES

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
AUTO_CLOSE = {
    "p": {"div", "p", "section", "article", "aside", "header", "footer", "main", "nav", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "form", "fieldset"},
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "tr": {"tr"},
    "option": {"option", "optgroup"},
    "thead": {"tbody", "tfoot"},
    "tbody": {"tbody", "tfoot"},
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None"
    children: list["Node"] = field(default_factory=list)
    text: str = ""
    line: int = 0
    is_comment: bool = False

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def has(self, name: str) -> bool:
        return name in self.attrs

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def text_content(self) -> str:
        parts = [self.text]
        for child in self.children:
            parts.append(child.text_content())
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def find_all(self, tag: str) -> list["Node"]:
        return [n for n in self.iter() if n.tag == tag]

    def classes(self) -> set[str]:
        return set(self.attr("class").split())


class HTMLTree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {}, None, line=1)
        self.stack: list[Node] = [self.root]
        self.raw: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrd = {}
        for k, v in attrs:
            attrd[k.lower()] = v if v is not None else ""
        parent = self.stack[-1]
        if parent.tag in AUTO_CLOSE and tag in AUTO_CLOSE[parent.tag]:
            while len(self.stack) > 1 and self.stack[-1].tag in AUTO_CLOSE and tag in AUTO_CLOSE[self.stack[-1].tag]:
                self.stack.pop()
            parent = self.stack[-1]
        node = Node(tag, attrd, parent, line=self.getpos()[0])
        parent.children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        attrd = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node = Node(tag, attrd, self.stack[-1], line=self.getpos()[0])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].text += data

    def handle_comment(self, data):
        node = Node("#comment", {}, self.stack[-1], line=self.getpos()[0])
        node.text = data
        node.is_comment = True
        self.stack[-1].children.append(node)


def parse_html(text: str) -> HTMLTree:
    parser = HTMLTree()
    parser.feed(text)
    parser.close()
    return parser


def node_location(node: Node) -> str:
    if node.tag == "#document":
        return "document"
    attrs = []
    if node.has("id"):
        attrs.append(f"#{node.attr('id')}")
    if node.has("class"):
        attrs.append("." + ".".join(node.classes()))
    return f"<{node.tag}{''.join(attrs)}> line {node.line}"


def by_tag(root: Node, tag: str) -> list[Node]:
    return [n for n in root.iter() if n.tag == tag]


def by_css(root: Node, selector: str) -> list[Node]:
    """Very small CSS selector support: tag, .class, #id, [attr], and combinations."""
    sel = selector.strip()
    tag = ""
    classes: list[str] = []
    ids: list[str] = []
    attrs: list[tuple[str, str]] = []
    for part in re.split(r"(?=[.#\[])", sel):
        if not part:
            continue
        if part.startswith("."):
            classes.append(part[1:])
        elif part.startswith("#"):
            ids.append(part[1:])
        elif part.startswith("["):
            m = re.match(r"\[([^\]=]+)(?:=[\"']?([^\"']+)[\"']?)?\]", part)
            if m:
                attrs.append((m.group(1), m.group(2) or ""))
        else:
            tag = part
    out = []
    for n in root.iter():
        if n.tag == "#document":
            continue
        if tag and n.tag != tag:
            continue
        if classes and not all(c in n.classes() for c in classes):
            continue
        if ids and not all(n.attr("id") == i for i in ids):
            continue
        if attrs and not all(n.has(a) and (v == "" or n.attr(a) == v) for a, v in attrs):
            continue
        out.append(n)
    return out


def accessible_name(node: Node, root: Node) -> str:
    if node.tag in ("img", "area"):
        return node.attr("alt").strip()
    if node.tag == "input":
        if node.attr("type") in ("button", "submit", "reset"):
            return node.attr("value").strip() or node.attr("aria-label").strip() or node.attr("title").strip()
        for label in by_tag(root, "label"):
            if label.attr("for") == node.attr("id") and node.attr("id"):
                return label.text_content()
            if label.attr("for") == "" and node in label.find_all("input") + label.find_all("select") + label.find_all("textarea"):
                return label.text_content()
        return (node.attr("aria-label") or node.attr("title") or node.attr("placeholder") or "").strip()
    if node.tag in ("button", "a", "summary"):
        return (node.attr("aria-label") or node.text_content() or node.attr("title") or "").strip()
    if node.tag in ("select", "textarea"):
        for label in by_tag(root, "label"):
            if label.attr("for") == node.attr("id") and node.attr("id"):
                return label.text_content()
        return (node.attr("aria-label") or node.attr("title") or "").strip()
    return (node.attr("aria-label") or node.attr("title") or node.text_content()).strip()


# --------------------------------------------------------------------------- #
# Color utilities
# --------------------------------------------------------------------------- #
NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "purple": "#800080", "gray": "#808080",
    "grey": "#808080", "silver": "#c0c0c0", "maroon": "#800000", "olive": "#808000",
    "lime": "#00ff00", "aqua": "#00ffff", "teal": "#008080", "navy": "#000080",
    "fuchsia": "#ff00ff", "orange": "#ffa500", "pink": "#ffc0cb", "brown": "#a52a2a",
    "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "darkgray": "#a9a9a9",
    "darkgrey": "#a9a9a9", "transparent": "transparent",
}


def parse_color(value: str) -> tuple[int, int, int, float] | None:
    value = value.strip().lower()
    if not value or value in ("inherit", "currentcolor", "transparent", "initial", "unset"):
        return None
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            try:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
            except ValueError:
                return None
        if len(h) == 8:
            try:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255
            except ValueError:
                return None
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        try:
            r, g, b = (float(p) for p in parts[:3])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return int(r), int(g), int(b), a
        except (ValueError, IndexError):
            return None
    if value in NAMED_COLORS:
        return parse_color(NAMED_COLORS[value])
    return None


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(size_px: float, bold: bool = False) -> bool:
    return size_px >= 24 or (bold and size_px >= 18.66)


# --------------------------------------------------------------------------- #
# CSS extraction
# --------------------------------------------------------------------------- #
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
DECL_RE = re.compile(r"([-a-zA-Z]+)\s*:\s*([^;]+);?")


def extract_css(text: str) -> str:
    """Return CSS from <style> blocks when present; otherwise the raw text."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I)
    if blocks:
        return "\n".join(blocks)
    return text


def parse_css_rules(css: str) -> list[tuple[str, dict[str, str]]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for m in CSS_RULE_RE.finditer(css):
        selector = m.group(1).strip()
        if selector.startswith("@"):
            continue
        decls = {}
        for dm in DECL_RE.finditer(m.group(2)):
            decls[dm.group(1).lower()] = dm.group(2).strip()
        if decls:
            rules.append((selector, decls))
    return rules


def css_background_for(node: Node, rules: list[tuple[str, dict[str, str]]]) -> str:
    for selector, decls in rules:
        if "background-color" in decls or "background" in decls:
            try:
                if node in by_css(node, selector):
                    return decls.get("background-color", decls.get("background", ""))
            except Exception:
                continue
    return ""


# --------------------------------------------------------------------------- #
# Engine context and result
# --------------------------------------------------------------------------- #
@dataclass
class EngineContext:
    cap: dict[str, Any]
    input_path: str | None
    config: Config
    params: dict[str, Any]
    checks: list[str]
    brief: str
    output_dir: Path
    artifacts_dir: Path
    limits: dict[str, int]
    current_file: str | None = None

    def read_input(self) -> str:
        if self.current_file:
            return read_text(self.current_file, self.limits.get("max_bytes", 8 * 1024 * 1024))
        if not self.input_path:
            raise ToolError("input-required", "This capability requires an input file.",
                            "Pass --input with a supported file, or use a governance/plan capability.")
        p = Path(self.input_path)
        if p.is_dir():
            files = iter_files(p, [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".json", ".txt", ".md"],
                               self.limits.get("max_files", 200), self.limits.get("max_bytes", 8 * 1024 * 1024))
            if not files:
                raise ToolError("input-not-found", f"No readable text file found in {p}.",
                                "Add a text, HTML, CSS, or source file.", str(p))
            return read_text(files[0], self.limits.get("max_bytes", 8 * 1024 * 1024))
        return read_text(p, self.limits.get("max_bytes", 8 * 1024 * 1024))

    def read_input_json(self) -> Any:
        if not self.input_path:
            raise ToolError("input-required", "This capability requires a JSON input file.",
                            "Pass --input with a JSON file.")
        return read_json(self.input_path, self.limits.get("max_bytes", 8 * 1024 * 1024))

    def read_input_csv(self) -> tuple[list[dict[str, str]], list[str]]:
        if not self.input_path:
            raise ToolError("input-required", "This capability requires a CSV input file.",
                            "Pass --input with a CSV file.")
        return read_csv(self.input_path, self.limits.get("max_bytes", 8 * 1024 * 1024))

    def html_files(self) -> list[Path]:
        if not self.input_path:
            raise ToolError("input-required", "This capability requires an HTML or source input.",
                            "Pass --input with an HTML file or a directory of source files.")
        return iter_files(self.input_path, [".html", ".htm", ".xhtml"], self.limits.get("max_files", 200),
                          self.limits.get("max_bytes", 8 * 1024 * 1024))

    def source_files(self) -> list[Path]:
        if not self.input_path:
            raise ToolError("input-required", "This capability requires a source input.",
                            "Pass --input with a file or directory.")
        return iter_files(self.input_path, [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".json", ".properties"],
                          self.limits.get("max_files", 200), self.limits.get("max_bytes", 8 * 1024 * 1024))

    def write_artifact(self, name: str, text: str, kind: str, description: str) -> dict[str, Any]:
        ensure_dir(self.artifacts_dir)
        path = self.artifacts_dir / name
        write_text(path, text)
        return {"path": str(path), "kind": kind, "description": description}


def resolve_input_file(ctx: EngineContext, suffixes: list[str], label: str = "input") -> Path:
    """Return the input file, or the first matching file in a directory."""
    if not ctx.input_path:
        raise ToolError("input-required", f"This capability requires a {label} file.",
                        "Pass --input with a supported file or a directory containing one.")
    p = Path(ctx.input_path)
    if p.is_file():
        return p
    if p.is_dir():
        files = iter_files(p, suffixes, ctx.limits.get("max_files", 200), ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        if files:
            return files[0]
        raise ToolError("input-not-found", f"No {label} file found in {p}.",
                        f"Add a file with one of these extensions: {', '.join(suffixes)}.", str(p))
    raise ToolError("input-missing", f"Input path does not exist: {p}", "Check the path and retry.", str(p))


@dataclass
class EngineResult:
    status: str
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    protocol: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def result(status: str, summary: str, findings: list[Finding | dict[str, Any]] | None = None,
           metrics: dict[str, Any] | None = None, protocol: dict[str, Any] | None = None,
           artifacts: list[dict[str, Any]] | None = None, limitations: list[str] | None = None,
           next_steps: list[str] | None = None) -> EngineResult:
    return EngineResult(status, summary, normalize_findings(findings or []), metrics or {},
                        protocol, artifacts or [], limitations or [], next_steps or [])


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #
def _f(check, severity, title, location="", description="", evidence="", wcag="", remediation="") -> Finding:
    return finding(check, severity, title, location=location, description=description,
                   evidence=evidence, wcag=wcag, remediation=remediation)


def check_document(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    html_nodes = [n for n in doc.root.iter() if n.tag == "html"]
    html = html_nodes[0] if html_nodes else doc.root
    if not html.has("lang") or not html.attr("lang").strip():
        out.append(_f("document", "serious", "Page language is not set", node_location(html),
                      "The root html element has no lang attribute. Screen readers may use the wrong pronunciation.",
                      wcag="3.1.1", remediation="Add a valid BCP 47 language tag, e.g. <html lang=\"en-PH\">."))
    title = by_tag(doc.root, "title")
    if not title or not title[0].text_content().strip():
        out.append(_f("document", "serious", "Document title is missing or empty", "document",
                      "Each page needs a unique, descriptive title.", wcag="2.4.2",
                      remediation="Add a <title> that names the purpose of the page."))
    viewport = by_tag(doc.root, "meta")
    vp = next((m for m in viewport if m.attr("name").lower() == "viewport"), None)
    if vp:
        content = vp.attr("content").lower()
        if "user-scalable=no" in content or "maximum-scale=1" in content.replace(" ", ""):
            out.append(_f("document", "serious", "Zoom is restricted by the viewport meta tag", node_location(vp),
                          "user-scalable=no or maximum-scale=1 prevents users from zooming.", wcag="1.4.4",
                          remediation="Remove user-scalable=no and allow maximum-scale=5 or omit it."))
    if not any(m.attr("charset") for m in viewport):
        out.append(_f("document", "minor", "Character encoding is not declared", "document",
                      "Add <meta charset=\"utf-8\"> to avoid mojibake and encoding loss.", wcag="3.1.2",
                      remediation="Add <meta charset=\"utf-8\"> as the first meta element in <head>."))
    return out


def check_landmarks(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    mains = by_tag(doc.root, "main")
    if not mains:
        out.append(_f("landmarks", "moderate", "No main landmark", "document",
                      "A main landmark helps users skip repeated content and orient themselves.", wcag="1.3.1",
                      remediation="Wrap the primary content in <main>."))
    if len(mains) > 1:
        out.append(_f("landmarks", "moderate", "Multiple main landmarks", node_location(mains[1]),
                      "Only one visible main landmark should exist per page.", wcag="1.3.1",
                      remediation="Keep one <main>; use <section> or <div> for other regions."))
    navs = by_tag(doc.root, "nav")
    for nav in navs:
        if not nav.attr("aria-label") and not nav.attr("aria-labelledby"):
            out.append(_f("landmarks", "minor", "Navigation landmark has no unique label", node_location(nav),
                          "Multiple navigation regions need distinct labels.", wcag="1.3.1",
                          remediation="Add aria-label to each <nav>."))
    labels = [n.attr("aria-label") for n in by_tag(doc.root, "nav") if n.attr("aria-label")]
    if len(labels) != len(set(labels)):
        out.append(_f("landmarks", "minor", "Duplicate navigation labels", "document",
                      "Two navigation landmarks share the same label.", wcag="1.3.1",
                      remediation="Give each navigation region a distinct label."))
    if not by_tag(doc.root, "a") or not any("skip" in a.text_content().lower() or "skip" in a.attr("href").lower() for a in by_tag(doc.root, "a")):
        out.append(_f("landmarks", "info", "No skip link detected", "document",
                      "A skip link is recommended for pages with repeated navigation.", wcag="2.4.1",
                      remediation="Add a skip link as the first focusable element."))
    return out


def check_headings(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    headings = [n for n in doc.root.iter() if re.fullmatch(r"h[1-6]", n.tag)]
    if not headings:
        out.append(_f("headings", "moderate", "No headings found", "document",
                      "Headings provide structure and navigation for assistive technology.", wcag="1.3.1",
                      remediation="Add a logical heading structure with one h1."))
        return out
    h1s = [h for h in headings if h.tag == "h1"]
    if not h1s:
        out.append(_f("headings", "moderate", "No h1 heading", "document",
                      "The page has no top-level heading.", wcag="1.3.1",
                      remediation="Add one h1 that names the page purpose."))
    for h in headings:
        if not h.text_content().strip():
            out.append(_f("headings", "moderate", "Empty heading", node_location(h),
                          "An empty heading is announced as a heading with no content.", wcag="1.3.1",
                          remediation="Add text or remove the heading."))
    levels = [int(h.tag[1]) for h in headings]
    for a, b in zip(levels, levels[1:]):
        if b > a + 1:
            out.append(_f("headings", "minor", f"Heading level jumps from h{a} to h{b}", "document",
                          "Skipped heading levels can confuse screen-reader navigation.", wcag="1.3.1",
                          remediation="Use the next heading level, or restructure the content."))
            break
    return out


def check_semantics(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for p in by_tag(doc.root, "p"):
        for child in p.children:
            if child.tag in ("div", "section", "article", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"):
                out.append(_f("semantics", "moderate", "Block element inside a paragraph", node_location(child),
                              "Invalid nesting can break the accessibility tree.", wcag="1.3.1",
                              remediation="Close the paragraph before the block element."))
                break
    for section in by_tag(doc.root, "section"):
        if not section.text_content().strip():
            out.append(_f("semantics", "minor", "Empty section", node_location(section),
                          "An empty section adds no value and may be announced as a region.", wcag="1.3.1",
                          remediation="Remove the empty section or add meaningful content."))
    for ul in by_tag(doc.root, "ul") + by_tag(doc.root, "ol"):
        for child in ul.children:
            if child.tag not in ("li", "script", "template"):
                out.append(_f("semantics", "moderate", "List contains a non-list child", node_location(child),
                              "Only <li> elements should be direct children of a list.", wcag="1.3.1",
                              remediation="Move the content into an <li> or outside the list."))
    for table in by_tag(doc.root, "table"):
        if table.attr("role") == "presentation":
            continue
        rows = [c for c in table.children if c.tag == "tr"]
        for row in rows:
            cells = [c for c in row.children if c.tag in ("td", "th")]
            if len(cells) > 1 and all(c.attr("colspan") for c in cells):
                out.append(_f("semantics", "info", "Table uses colspan on every cell", node_location(table),
                              "Complex tables need careful header association.", wcag="1.3.1",
                              remediation="Simplify the table or provide a text alternative."))
                break
    return out


def check_names(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    controls = [n for n in doc.root.iter() if n.tag in ("button", "a", "input", "select", "textarea", "summary")]
    for node in controls:
        if node.tag == "input" and node.attr("type") == "hidden":
            continue
        name = accessible_name(node, doc.root)
        if not name:
            typ = node.attr("type", "text") if node.tag == "input" else node.tag
            out.append(_f("names", "serious", f"{node.tag} has no accessible name", node_location(node),
                          f"A {typ} control is announced without a name.", wcag="4.1.2",
                          remediation="Add a visible label, aria-label, aria-labelledby, or associated <label>."))
        elif node.tag == "a" and re.fullmatch(r"(click here|here|read more|more|link|learn more)", name.strip().lower()):
            out.append(_f("names", "moderate", "Link text is not descriptive", node_location(node),
                          f"Link text '{name}' does not describe the destination.", wcag="2.4.4",
                          remediation="Use link text that describes the destination, including context."))
    return out


def check_aria(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    prohibited = {"button": {"a", "button"}, "link": {"a"}, "checkbox": {"input"}, "radio": {"input"}}
    for node in doc.root.iter():
        role = node.attr("role")
        if role in prohibited and node.tag in prohibited[role]:
            out.append(_f("aria", "moderate", f"Redundant role '{role}' on <{node.tag}>", node_location(node),
                          "The native element already exposes the role; the redundant role adds no value.", wcag="4.1.2",
                          remediation="Remove the role attribute and keep the native element."))
        if node.attr("aria-hidden") == "true":
            focusable = node.find_all("a") + node.find_all("button") + node.find_all("input") + node.find_all("select") + node.find_all("textarea")
            focusable = [n for n in focusable if not n.attr("tabindex") == "-1"]
            if focusable:
                out.append(_f("aria", "serious", "Focusable content inside aria-hidden", node_location(node),
                              "Focusable elements inside aria-hidden are hidden from assistive technology but can still receive focus.", wcag="4.1.2",
                              remediation="Remove aria-hidden or remove the focusable descendants."))
        for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
            refs = node.attr(attr)
            if refs:
                for ref in refs.split():
                    if ref and not any(n.attr("id") == ref for n in doc.root.iter()):
                        out.append(_f("aria", "moderate", f"{attr} references a missing id", node_location(node),
                                      f"The id '{ref}' does not exist in the document.", wcag="4.1.2",
                                      remediation="Add the referenced id or remove the attribute."))
        if node.attr("aria-expanded") and not node.attr("aria-controls"):
            out.append(_f("aria", "info", "aria-expanded without aria-controls", node_location(node),
                          "The controlled element is not identified; this is optional but helpful.", wcag="4.1.2",
                          remediation="Add aria-controls referencing the expanded region."))
        if role == "application":
            out.append(_f("aria", "info", "role=application changes screen-reader behavior", node_location(node),
                          "Application role can disable browse mode and should be used only with a full keyboard interaction model.", wcag="4.1.2",
                          remediation="Use native semantics unless a composite widget requires application role."))
    return out


def check_forms(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    labels = by_tag(doc.root, "label")
    for node in by_tag(doc.root, "input") + by_tag(doc.root, "select") + by_tag(doc.root, "textarea"):
        if node.attr("type") in ("hidden", "submit", "button", "reset"):
            continue
        name = accessible_name(node, doc.root)
        if not name:
            out.append(_f("forms", "serious", "Form control has no label", node_location(node),
                          "The control has no associated label, aria-label, or title.", wcag="3.3.2",
                          remediation="Add a persistent visible <label> and associate it with for/id."))
        if node.attr("required") and not node.attr("aria-required"):
            out.append(_f("forms", "info", "Required control does not expose aria-required", node_location(node),
                          "Native required is usually sufficient, but aria-required improves exposure in some widgets.", wcag="3.3.2",
                          remediation="Add aria-required=\"true\" if the control is a custom widget."))
        if node.tag == "input" and node.attr("type") in ("email", "tel", "password", "text") and not node.attr("autocomplete"):
            out.append(_f("forms", "minor", "Input purpose is not exposed with autocomplete", node_location(node),
                          "Autocomplete tokens help browsers and assistive technologies fill identity fields.", wcag="1.3.5",
                          remediation="Add an appropriate autocomplete token, e.g. autocomplete=\"email\"."))
    groups = by_tag(doc.root, "fieldset")
    if not groups and len([n for n in by_tag(doc.root, "input") if n.attr("type") in ("radio", "checkbox")]) > 2:
        out.append(_f("forms", "minor", "Radio or checkbox group has no fieldset", "document",
                      "Grouping related controls improves context.", wcag="1.3.1",
                      remediation="Wrap related controls in a <fieldset> with a <legend>."))
    return out


def check_form_errors(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    invalid = [n for n in doc.root.iter() if n.attr("aria-invalid") == "true"]
    for node in invalid:
        if not node.attr("aria-describedby"):
            out.append(_f("form-errors", "serious", "Invalid field has no error association", node_location(node),
                          "aria-invalid is set but no error message is associated.", wcag="3.3.1",
                          remediation="Add aria-describedby pointing to the error message id."))
    error_nodes = [n for n in doc.root.iter() if n.attr("role") in ("alert", "status") or "error" in n.classes()]
    if invalid and not error_nodes:
        out.append(_f("form-errors", "moderate", "No error message role or text detected", "document",
                      "Errors should be announced and available as text.", wcag="3.3.1",
                      remediation="Add role=\"alert\" or a linked error summary and focus the first error."))
    if not any(n.attr("aria-live") for n in doc.root.iter()):
        out.append(_f("form-errors", "info", "No live region for asynchronous errors", "document",
                      "Asynchronous validation errors should be announced.", wcag="4.1.3",
                      remediation="Add a polite live region for validation status."))
    return out


def check_links(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    seen: dict[str, list[str]] = {}
    for a in by_tag(doc.root, "a"):
        if not a.attr("href"):
            out.append(_f("links", "moderate", "Anchor has no href", node_location(a),
                          "An anchor without href is not a link and may not be keyboard focusable.", wcag="2.4.4",
                          remediation="Use a button for actions, or add a valid href."))
            continue
        name = accessible_name(a, doc.root)
        if not name:
            out.append(_f("links", "serious", "Link has no accessible name", node_location(a),
                          "The link has no text, aria-label, or image alt.", wcag="2.4.4",
                          remediation="Add descriptive link text or an aria-label."))
        if a.attr("target") == "_blank" and "new" not in (a.attr("aria-label") + a.text_content()).lower():
            out.append(_f("links", "info", "Link opens a new window without warning", node_location(a),
                          "Users should be warned when a link opens a new window.", wcag="3.2.5",
                          remediation="Add visually hidden text such as '(opens in a new tab)'."))
        if name:
            seen.setdefault(name.lower(), []).append(a.attr("href"))
    for name, hrefs in seen.items():
        if len(set(hrefs)) > 1:
            out.append(_f("links", "minor", "Duplicate link text with different destinations", "document",
                          f"The link text '{name}' points to multiple destinations.", wcag="2.4.4",
                          remediation="Make the link text unique or add context."))
    return out


def check_images(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for img in by_tag(doc.root, "img"):
        alt = img.attr("alt")
        if alt is None:
            out.append(_f("images", "serious", "Image has no alt attribute", node_location(img),
                          "Missing alt means the image may be announced with its file name or skipped unpredictably.", wcag="1.1.1",
                          remediation="Add alt text, or alt=\"\" if the image is decorative."))
        elif alt.strip() == "":
            if img.attr("role") == "img" or img.attr("aria-label"):
                out.append(_f("images", "moderate", "Decorative image has role=img or aria-label", node_location(img),
                              "The image is marked decorative but also exposed as an image.", wcag="1.1.1",
                              remediation="Remove role=img/aria-label for decorative images."))
        else:
            if re.search(r"\.(png|jpe?g|gif|svg|webp)$", alt, re.I):
                out.append(_f("images", "moderate", "Alt text is a file name", node_location(img),
                              f"Alt text '{alt}' looks like a file name.", wcag="1.1.1",
                              remediation="Describe the purpose of the image in the alt text."))
            if re.match(r"^(image|photo|picture|graphic|screenshot) of\b", alt, re.I):
                out.append(_f("images", "minor", "Alt text starts with redundant phrase", node_location(img),
                              "Screen readers already announce the image role.", wcag="1.1.1",
                              remediation="Remove 'image of' and describe the content directly."))
            if len(alt) > 125:
                out.append(_f("images", "info", "Alt text is long", node_location(img),
                              "Long alt text may be better as a long description.", wcag="1.1.1",
                              remediation="Shorten the alt text and link to a long description."))
    for svg in by_tag(doc.root, "svg"):
        if not svg.attr("aria-hidden") and not svg.attr("role") and not svg.find_all("title"):
            out.append(_f("images", "minor", "SVG has no accessible name or aria-hidden", node_location(svg),
                          "An unlabeled SVG may be announced as an unnamed graphic.", wcag="1.1.1",
                          remediation="Add role=\"img\" and a <title>, or aria-hidden=\"true\" if decorative."))
    return out


def check_icons(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for btn in by_tag(doc.root, "button"):
        has_text = bool(btn.text_content().strip())
        has_img_alt = any(i.attr("alt").strip() for i in btn.find_all("img"))
        if not has_text and not has_img_alt and not btn.attr("aria-label") and not btn.attr("aria-labelledby"):
            out.append(_f("icons", "serious", "Icon-only button has no accessible name", node_location(btn),
                          "The button contains only an icon.", wcag="4.1.2",
                          remediation="Add aria-label or visually hidden text."))
    for svg in by_tag(doc.root, "svg"):
        if svg.attr("aria-hidden") == "true":
            continue
        if svg.attr("role") == "img" and not svg.find_all("title") and not svg.attr("aria-label"):
            out.append(_f("icons", "moderate", "SVG role=img has no title", node_location(svg),
                          "The image role requires an accessible name.", wcag="1.1.1",
                          remediation="Add a <title> or aria-label."))
    return out


def check_tables(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for table in by_tag(doc.root, "table"):
        if table.attr("role") == "presentation":
            continue
        ths = table.find_all("th")
        if not ths:
            out.append(_f("tables", "moderate", "Data table has no header cells", node_location(table),
                          "Headers are needed to associate data cells with their meaning.", wcag="1.3.1",
                          remediation="Use <th> for header cells and set scope."))
        for th in ths:
            if not th.attr("scope") and not th.attr("id"):
                out.append(_f("tables", "minor", "Header cell has no scope or id", node_location(th),
                              "Header association may be ambiguous.", wcag="1.3.1",
                              remediation="Add scope=\"col\" or scope=\"row\"."))
        if not table.find_all("caption"):
            out.append(_f("tables", "info", "Table has no caption", node_location(table),
                          "A caption helps users understand the table purpose.", wcag="1.3.1",
                          remediation="Add a <caption> that names the table."))
    return out


def check_media(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for video in by_tag(doc.root, "video"):
        tracks = video.find_all("track")
        captions = [t for t in tracks if t.attr("kind") in ("captions", "subtitles")]
        if not captions:
            out.append(_f("media", "serious", "Video has no caption track", node_location(video),
                          "Prerecorded video with audio needs captions.", wcag="1.2.2",
                          remediation="Add a <track kind=\"captions\"> with a valid WebVTT file."))
        if video.attr("autoplay") and not video.attr("controls") and not video.attr("muted"):
            out.append(_f("media", "moderate", "Autoplaying video has no controls and is not muted", node_location(video),
                          "Auto-playing audio can startle users and cannot be stopped.", wcag="1.4.2",
                          remediation="Add controls or require a user action to play."))
    for audio in by_tag(doc.root, "audio"):
        if audio.attr("autoplay") and not audio.attr("controls"):
            out.append(_f("media", "moderate", "Autoplaying audio has no controls", node_location(audio),
                          "Users need a way to pause or stop audio.", wcag="1.4.2",
                          remediation="Add controls or remove autoplay."))
    for track in by_tag(doc.root, "track"):
        if track.attr("kind") in ("captions", "subtitles") and not track.attr("src"):
            out.append(_f("media", "moderate", "Caption track has no src", node_location(track),
                          "The track element points to no file.", wcag="1.2.2",
                          remediation="Add a valid src and srclang."))
    return out


def check_math(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for math_node in by_tag(doc.root, "math"):
        if not math_node.attr("aria-label") and not math_node.attr("aria-labelledby") and not math_node.find_all("annotation"):
            out.append(_f("math", "moderate", "Math element has no accessible annotation", node_location(math_node),
                          "MathML without an annotation may not be announced meaningfully.", wcag="1.1.1",
                          remediation="Add an <annotation encoding=\"application/x-tex\"> or aria-label."))
    for img in by_tag(doc.root, "img"):
        alt = img.attr("alt").lower()
        if any(w in alt for w in ("equation", "formula", "math")) and len(alt) < 20:
            out.append(_f("math", "info", "Math image has a short alt", node_location(img),
                          "A short alt may not convey the equation.", wcag="1.1.1",
                          remediation="Provide a full text alternative or MathML."))
    return out


def check_charts(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for figure in by_tag(doc.root, "figure"):
        if not figure.find_all("figcaption"):
            out.append(_f("charts", "minor", "Figure has no caption", node_location(figure),
                          "A caption helps users understand the figure purpose.", wcag="1.1.1",
                          remediation="Add a <figcaption>."))
        if figure.find_all("img") and not figure.find_all("table"):
            out.append(_f("charts", "info", "Chart image has no data table alternative", node_location(figure),
                          "Complex charts need a text or table equivalent.", wcag="1.1.1",
                          remediation="Add a visually hidden or visible data table."))
    for svg in by_tag(doc.root, "svg"):
        if svg.find_all("rect") or svg.find_all("path") or svg.find_all("circle"):
            if not svg.find_all("title") and not svg.attr("aria-label"):
                out.append(_f("charts", "moderate", "Data visualization SVG has no accessible name", node_location(svg),
                              "Charts and graphs need a text alternative.", wcag="1.1.1",
                              remediation="Add role=\"img\", a <title>, and a data table."))
                break
    return out


def check_iframe(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for iframe in by_tag(doc.root, "iframe"):
        if not iframe.attr("title"):
            out.append(_f("iframe", "moderate", "Iframe has no title", node_location(iframe),
                          "The frame content is not identified for screen-reader users.", wcag="4.1.2",
                          remediation="Add a title that names the embedded content."))
        if not iframe.attr("sandbox") and not iframe.attr("srcdoc"):
            out.append(_f("iframe", "info", "Iframe has no sandbox attribute", node_location(iframe),
                          "Sandboxing limits the embedded content's capabilities.", wcag="4.1.2",
                          remediation="Add a sandbox attribute appropriate to the content."))
    return out


def check_focus(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        tab = node.attr("tabindex")
        if tab.isdigit() and int(tab) > 0:
            out.append(_f("focus", "moderate", "Positive tabindex overrides natural focus order", node_location(node),
                          f"tabindex=\"{tab}\" can break the expected focus order.", wcag="2.4.3",
                          remediation="Use tabindex=\"0\" and reorder the DOM instead."))
        if node.attr("aria-hidden") == "true" and (node.tag in ("a", "button", "input", "select", "textarea") or node.attr("tabindex") == "0"):
            out.append(_f("focus", "serious", "Focusable element is hidden from assistive technology", node_location(node),
                          "aria-hidden hides the element while it remains focusable.", wcag="4.1.2",
                          remediation="Remove aria-hidden or remove the element from the tab order."))
    return out


def check_focus_order(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.attr("tabindex") and node.attr("tabindex") not in ("0", "-1"):
            out.append(_f("focus-order", "moderate", "Non-zero tabindex is used", node_location(node),
                          "Positive tabindex values override the DOM focus order.", wcag="2.4.3",
                          remediation="Use tabindex=\"0\" and reorder the DOM."))
    css = ctx.read_input() if ctx.input_path and ctx.input_path.endswith((".css", ".html", ".htm")) else ""
    if re.search(r"flex-direction\s*:\s*row-reverse|order\s*:\s*-?\d", css, re.I):
        out.append(_f("focus-order", "info", "CSS reordering detected", "stylesheet",
                      "flex-direction: row-reverse or order can make visual order differ from DOM order.", wcag="1.3.2",
                      remediation="Verify that the DOM order remains meaningful and matches the visual order."))
    return out


def check_keyboard_trap(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.attr("role") in ("dialog", "alertdialog"):
            if not node.find_all("button") and not node.attr("aria-label") and not node.attr("aria-labelledby"):
                out.append(_f("keyboard-trap", "moderate", "Dialog has no close mechanism", node_location(node),
                              "A dialog must provide a way to close it.", wcag="2.1.2",
                              remediation="Add a close button and return focus to the invoking element."))
    return out


def check_shortcuts(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.has("accesskey"):
            out.append(_f("shortcuts", "moderate", "accesskey shortcut may conflict with assistive technology", node_location(node),
                          f"accesskey=\"{node.attr('accesskey')}\" can override browser or AT shortcuts.", wcag="2.1.4",
                          remediation="Remove accesskey or provide a documented, remappable alternative."))
    return out


def check_target_size(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.tag not in ("button", "a", "input", "select", "summary"):
            continue
        style = node.attr("style").lower().replace(" ", "")
        m = re.search(r"width:(\d+(?:\.\d+)?)px", style)
        h = re.search(r"height:(\d+(?:\.\d+)?)px", style)
        w = float(m.group(1)) if m else None
        hh = float(h.group(1)) if h else None
        if (w is not None and w < 24) or (hh is not None and hh < 24):
            out.append(_f("target-size", "moderate", "Pointer target is smaller than 24 by 24 CSS pixels", node_location(node),
                          f"Inline size is {w or 'auto'} by {hh or 'auto'} px.", wcag="2.5.8",
                          remediation="Increase the target to at least 24 by 24 CSS pixels or provide sufficient spacing."))
    return out


def check_gestures(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"touch(start|move)|swipe|gesture|onpointerdown", text, re.I):
        if not re.search(r"\bclick\b|onClick|onclick", text, re.I):
            out.append(_f("gestures", "moderate", "Gesture handler without a click alternative", "source",
                          "A touch or pointer gesture is used without a simple alternative.", wcag="2.5.1",
                          remediation="Provide a single-pointer alternative such as a button."))
    return out


def check_motion(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"animation|transition|@keyframes", text, re.I) and not re.search(r"prefers-reduced-motion", text, re.I):
        out.append(_f("motion", "moderate", "Animation or transition without reduced-motion support", "stylesheet",
                      "Motion can trigger vestibular symptoms.", wcag="2.3.3",
                      remediation="Add a prefers-reduced-motion media query that disables non-essential motion."))
    for node in doc.root.iter():
        if node.tag in ("marquee", "blink"):
            out.append(_f("motion", "moderate", f"Deprecated <{node.tag}> element", node_location(node),
                          "Moving or blinking content needs a pause mechanism.", wcag="2.2.2",
                          remediation="Replace with a controllable animation or remove it."))
        if node.attr("autoplay") and node.tag == "video":
            out.append(_f("motion", "info", "Autoplaying video", node_location(node),
                          "Auto-playing motion should be pausable and respect reduced motion.", wcag="2.2.2",
                          remediation="Provide controls and honor prefers-reduced-motion."))
    return out


def check_photosensitivity(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"animation(?:-iteration-count)?\s*:\s*infinite", text, re.I):
        out.append(_f("photosensitivity", "moderate", "Infinite animation detected", "stylesheet",
                      "Continuous flashing or rapid motion can affect photosensitive users.", wcag="2.3.1",
                      remediation="Limit flashing to three times per second and provide a pause control."))
    if re.search(r"flash|strobe|flicker", text, re.I):
        out.append(_f("photosensitivity", "info", "Flashing content keyword detected", "source",
                      "Review the flash frequency and area against the threshold.", wcag="2.3.1",
                      remediation="Keep flashes below the threshold or remove them."))
    return out


def check_contrast(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    rules = parse_css_rules(extract_css(text))
    checked = 0
    for selector, decls in rules:
        fg = decls.get("color")
        bg = decls.get("background-color") or decls.get("background")
        if not fg or not bg:
            continue
        f = parse_color(fg)
        b = parse_color(bg)
        if not f or not b:
            continue
        size_match = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", decls.get("font-size", ""))
        size = float(size_match.group(1)) if size_match else 16.0
        bold = "bold" in decls.get("font-weight", "") or (decls.get("font-weight", "").isdigit() and int(decls["font-weight"]) >= 700)
        large = is_large_text(size, bold)
        threshold = 3.0 if large else 4.5
        ratio = contrast_ratio(f[:3], b[:3])
        checked += 1
        if ratio < threshold:
            out.append(_f("contrast", "serious" if not large else "moderate",
                          f"Contrast ratio {ratio:.2f}:1 is below {threshold}:1", selector,
                          f"Foreground {fg} on background {bg} at {size}px{' bold' if bold else ''}.", wcag="1.4.3",
                          evidence=f"{selector} {{ color: {fg}; background-color: {bg}; }}",
                          remediation=f"Increase contrast to at least {threshold}:1 or provide a compliant alternative."))
    for node in doc.root.iter():
        style = node.attr("style")
        if not style:
            continue
        decls = dict((k.strip().lower(), v.strip()) for k, v in (p.split(":", 1) for p in style.split(";") if ":" in p))
        f = parse_color(decls.get("color", ""))
        b = parse_color(decls.get("background-color", ""))
        if f and b:
            ratio = contrast_ratio(f[:3], b[:3])
            if ratio < 4.5:
                out.append(_f("contrast", "serious", f"Inline contrast ratio {ratio:.2f}:1 is below 4.5:1", node_location(node),
                              f"Foreground {decls.get('color')} on background {decls.get('background-color')}.", wcag="1.4.3",
                              remediation="Increase contrast to at least 4.5:1 for normal text."))
    if not checked:
        out.append(_f("contrast", "info", "No explicit color pairs found in CSS", "stylesheet",
                      "Contrast cannot be computed without foreground/background declarations.", wcag="1.4.3",
                      remediation="Review rendered colors with a contrast checker or computed styles."))
    return out


def check_non_color(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"\.(error|success|warning)\s*\{[^}]*color\s*:", text, re.I):
        if not re.search(r"\.(error|success|warning)[^{]*\{[^}]*(border|icon|::before|content)", text, re.I):
            out.append(_f("non-color", "moderate", "Status may be conveyed by color alone", "stylesheet",
                          "Error or success styling changes color without a text or icon cue.", wcag="1.4.1",
                          remediation="Add text, an icon, or a border pattern in addition to color."))
    return out


def check_text_resize(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"font-size\s*:\s*(?:[0-9]|1[0-1])px", text, re.I):
        out.append(_f("text-resize", "moderate", "Very small fixed font sizes detected", "stylesheet",
                      "Text below 12px may be difficult to read and may not scale well.", wcag="1.4.4",
                      remediation="Use relative units and a minimum readable size."))
    if re.search(r"height\s*:\s*\d+px[^}]*overflow\s*:\s*hidden", text, re.I):
        out.append(_f("text-resize", "moderate", "Fixed height with overflow hidden may clip text", "stylesheet",
                      "Text resized to 200% can be clipped.", wcag="1.4.4",
                      remediation="Use min-height and allow content to expand."))
    return out


def check_reflow(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"width\s*:\s*(?:[3-9]\d{2,}|[1-9]\d{3,})px", text, re.I):
        out.append(_f("reflow", "moderate", "Fixed width may prevent reflow at 320 CSS pixels", "stylesheet",
                      "A fixed width wider than the viewport can force two-dimensional scrolling.", wcag="1.4.10",
                      remediation="Use max-width: 100% and responsive units."))
    if re.search(r"overflow-x\s*:\s*hidden", text, re.I):
        out.append(_f("reflow", "info", "Horizontal overflow is hidden", "stylesheet",
                      "Hidden overflow can make content unreachable at small sizes.", wcag="1.4.10",
                      remediation="Allow wrapping or provide a scrollable region with keyboard access."))
    return out


def check_reading_order(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"order\s*:\s*-?\d+|flex-direction\s*:\s*row-reverse|grid-template-areas", text, re.I):
        out.append(_f("reading-order", "info", "CSS reordering may change visual and DOM order", "stylesheet",
                      "Visual order can differ from the DOM order, which affects screen-reader and keyboard order.", wcag="1.3.2",
                      remediation="Verify the meaningful sequence and keep the DOM order logical."))
    return out


def check_typography(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"text-align\s*:\s*justify", text, re.I):
        out.append(_f("typography", "minor", "Justified text can create uneven word spacing", "stylesheet",
                      "Justification can reduce readability, especially for dyslexia.", wcag="1.4.8",
                      remediation="Use left-aligned text for body copy."))
    if re.search(r"text-transform\s*:\s*uppercase", text, re.I):
        out.append(_f("typography", "info", "Uppercase text transform detected", "stylesheet",
                      "Long uppercase passages can be harder to read and may be announced letter by letter.", wcag="1.4.8",
                      remediation="Use uppercase sparingly and provide the normal text in the DOM."))
    if re.search(r"line-height\s*:\s*(?:[01](?:\.\d+)?|1[0-2](?:\.\d+)?)\s*;", text, re.I):
        out.append(_f("typography", "minor", "Tight line height detected", "stylesheet",
                      "Line height below 1.5 can reduce readability.", wcag="1.4.12",
                      remediation="Use a line height of at least 1.5 for body text."))
    return out


def check_plain_language(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = doc.root.text_content()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    long_sentences = [s for s in sentences if len(s.split()) > 25]
    if long_sentences:
        out.append(_f("plain-language", "minor", f"{len(long_sentences)} long sentence(s) detected", "content",
                      "Long sentences can increase cognitive load.", wcag="3.1.5",
                      remediation="Split sentences into shorter, direct statements."))
    jargon = ["utilize", "leverage", "facilitate", "commence", "terminate", "prior to", "in order to", "aforementioned"]
    found = [w for w in jargon if re.search(r"\b" + w + r"\b", text, re.I)]
    if found:
        out.append(_f("plain-language", "minor", "Complex or formal wording detected", "content",
                      f"Words: {', '.join(found)}.", wcag="3.1.5",
                      remediation="Use familiar, direct words."))
    return out


def check_i18n(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"(?:placeholder|title|aria-label)\s*=\s*[\"'][^\"']{3,}[\"']", text, re.I):
        out.append(_f("i18n", "info", "User-facing attributes may contain hard-coded strings", "markup",
                      "Placeholder, title, and aria-label text may need translation.", wcag="3.1.2",
                      remediation="Externalize user-facing strings into locale resources."))
    if re.search(r"\+\s*[\"'][^\"']+[\"']|['\"][^'\"]+['\"]\s*\+", text):
        out.append(_f("i18n", "moderate", "String concatenation detected", "source",
                      "Concatenation can break word order and grammar in other languages.", wcag="3.1.2",
                      remediation="Use full sentences with placeholders or ICU messages."))
    if re.search(r"toLocaleString\(\s*\)|toLocaleDateString\(\s*\)", text):
        out.append(_f("i18n", "minor", "Locale formatting without an explicit locale", "source",
                      "The runtime locale may not match the user's selected language.", wcag="3.1.2",
                      remediation="Pass the selected locale explicitly."))
    return out


def check_externalization(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.input_path:
        return out
    files = ctx.source_files()
    for path in files:
        if path.suffix.lower() not in (".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"):
            continue
        text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        for m in re.finditer(r">([^<>{}\n]{4,})<", text):
            s = m.group(1).strip()
            if s and not s.startswith(("http", "//")):
                out.append(_f("externalization", "moderate", "Hard-coded user-facing text in component", f"{path.name}:{text[:m.start()].count(chr(10))+1}",
                              f"Text '{s[:60]}' is not wrapped in a translation call.", wcag="3.1.2",
                              remediation="Move the string into a locale resource and use a translation key."))
                break
    return out


def check_placeholders(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.input_path:
        return out
    data = read_json(ctx.input_path)
    strings: dict[str, str] = {}
    if isinstance(data, dict):
        def walk(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, str):
                strings[prefix] = obj
        walk(data)
    patterns = [r"\{\{[^}]+\}\}", r"\{[a-zA-Z0-9_]+\}", r"%[sdif]", r"\$\{[^}]+\}", r"\{0\}"]
    for key, value in strings.items():
        found = []
        for pat in patterns:
            found.extend(re.findall(pat, value))
        if not found:
            continue
        if len(found) != len(set(found)):
            out.append(_f("placeholders", "moderate", "Duplicate placeholder in a string", key,
                          f"Placeholders {found} contain duplicates.", wcag="3.1.2",
                          remediation="Use each placeholder once."))
        if re.search(r"\{[a-zA-Z0-9_]+\}", value) and re.search(r"\{\{[^}]+\}\}", value):
            out.append(_f("placeholders", "info", "Mixed placeholder syntax", key,
                          "The string uses both single-brace and double-brace placeholders.", wcag="3.1.2",
                          remediation="Use one placeholder syntax consistently."))
    return out


def check_bidi(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"direction\s*:\s*ltr", text, re.I):
        out.append(_f("bidi", "info", "Explicit direction: ltr detected", "stylesheet",
                      "Hard-coded direction can break right-to-left locales.", wcag="1.3.2",
                      remediation="Use direction: auto or logical properties for user content."))
    if re.search(r"[\u202a-\u202e\u2066-\u2069]", text):
        out.append(_f("bidi", "moderate", "Bidirectional control characters detected", "source",
                      "Embedding characters can be used for spoofing or can garble display.", wcag="1.3.2",
                      remediation="Remove unnecessary bidi controls and use isolates around user content."))
    for node in doc.root.iter():
        if node.tag in ("input", "textarea") and node.attr("dir") in ("ltr", "rtl"):
            out.append(_f("bidi", "info", "Hard-coded text direction on an input", node_location(node),
                          "User input may be in a different script.", wcag="1.3.2",
                          remediation="Use dir=\"auto\" for user-generated text."))
            break
    return out


def check_bidi_debug(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"[\u0600-\u06ff\u0590-\u05ff]", text):
        if not re.search(r"dir\s*=\s*[\"']rtl[\"']|dir\s*:\s*rtl", text, re.I):
            out.append(_f("bidi-debug", "moderate", "RTL script content without a direction declaration", "source",
                          "Right-to-left text may display incorrectly.", wcag="1.3.2",
                          remediation="Add dir=\"rtl\" or dir=\"auto\" to the container."))
    if re.search(r"[\u0660-\u0669\u06f0-\u06f9]", text) and re.search(r"\b\d+\b", text):
        out.append(_f("bidi-debug", "info", "Mixed Arabic-Indic and Western digits detected", "source",
                      "Check number direction and formatting consistency.", wcag="1.3.2",
                      remediation="Use locale-aware number formatting."))
    return out


def check_rtl(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"(?:margin|padding|left|right|border-left|border-right)\s*:", text, re.I):
        if not re.search(r"margin-inline|padding-inline|inset-inline|border-inline", text, re.I):
            out.append(_f("rtl", "info", "Physical direction properties detected", "stylesheet",
                          "left/right properties do not mirror automatically in RTL.", wcag="1.3.2",
                          remediation="Use logical properties (inline-start/inline-end) where appropriate."))
    if re.search(r"direction\s*:\s*ltr", text, re.I):
        out.append(_f("rtl", "moderate", "Hard-coded LTR direction", "stylesheet",
                      "The layout may not mirror for RTL locales.", wcag="1.3.2",
                      remediation="Use direction: auto or set the direction from the locale."))
    return out


def check_unicode(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.input_path:
        return out
    text = ctx.read_input()
    if "\ufffd" in text:
        out.append(_f("unicode", "serious", "Replacement character (mojibake) detected", "input",
                      "The text contains U+FFFD, which indicates an encoding loss.", wcag="3.1.2",
                      remediation="Re-export the file as UTF-8 and verify the source encoding."))
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\r\t" for ch in text):
        out.append(_f("unicode", "moderate", "Control characters detected", "input",
                      "Control characters can break rendering and parsing.", wcag="3.1.2",
                      remediation="Remove or escape control characters."))
    if re.search(r"[\u0300-\u036f]", text):
        out.append(_f("unicode", "info", "Combining marks detected", "input",
                      "Verify normalization and grapheme handling for search and truncation.", wcag="3.1.2",
                      remediation="Normalize to NFC where appropriate and test truncation."))
    return out


def check_key_governance(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.input_path:
        return out
    data = read_json(ctx.input_path)
    keys: list[str] = []
    if isinstance(data, dict):
        def walk(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, str):
                keys.append(prefix)
        walk(data)
    if not keys:
        return out
    if len(keys) != len(set(keys)):
        out.append(_f("key-governance", "moderate", "Duplicate keys detected", "resource",
                      "Duplicate keys can cause inconsistent translations.", wcag="3.1.2",
                      remediation="Remove duplicate keys and keep one stable key."))
    bad = [k for k in keys if re.search(r"[A-Z]|_", k)]
    if bad:
        out.append(_f("key-governance", "info", "Keys are not lower-case hyphenated", "resource",
                      f"Examples: {', '.join(bad[:3])}.", wcag="3.1.2",
                      remediation="Use a consistent lower-case, dot- or hyphen-delimited key convention."))
    return out


def check_dynamic(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    live = [n for n in doc.root.iter() if n.attr("aria-live") or n.attr("role") in ("status", "alert", "log")]
    if not live:
        out.append(_f("dynamic", "info", "No live region detected", "document",
                      "Dynamic updates such as toasts and validation messages should be announced.", wcag="4.1.3",
                      remediation="Add role=\"status\" or aria-live=\"polite\" for non-urgent updates."))
    for node in doc.root.iter():
        if node.attr("aria-expanded") and not node.attr("aria-controls"):
            out.append(_f("dynamic", "minor", "Expander does not identify its controlled region", node_location(node),
                          "aria-expanded is present without aria-controls.", wcag="4.1.2",
                          remediation="Add aria-controls with the region id."))
    return out


def check_consistency(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    navs = by_tag(doc.root, "nav")
    labels: list[str] = []
    for nav in navs:
        for a in nav.find_all("a"):
            name = accessible_name(a, doc.root)
            if name:
                labels.append(name)
    if len(labels) != len(set(labels)):
        out.append(_f("consistency", "minor", "Duplicate navigation labels", "navigation",
                      "Repeated labels can make navigation ambiguous.", wcag="3.2.4",
                      remediation="Use consistent, distinct labels for navigation items."))
    return out


def check_notifications(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.attr("role") == "alert":
            if node.attr("aria-live") == "off":
                out.append(_f("notifications", "moderate", "Alert has aria-live=off", node_location(node),
                              "The alert will not be announced.", wcag="4.1.3",
                              remediation="Remove aria-live=off or use role=status for non-urgent messages."))
    return out


def check_help(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = doc.root.text_content().lower()
    if not any(w in text for w in ("help", "support", "guide", "faq")):
        out.append(_f("help", "info", "No help or support link detected", "document",
                      "Users should be able to find help and a human support channel.", wcag="3.2.6",
                      remediation="Add a help link and an accessible contact channel."))
    return out


def check_truncation(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"text-overflow\s*:\s*ellipsis|white-space\s*:\s*nowrap", text, re.I):
        out.append(_f("truncation", "info", "Truncation CSS detected", "stylesheet",
                      "Ellipsis or nowrap can hide content in localized strings.", wcag="1.4.4",
                      remediation="Allow wrapping and verify the longest localized string."))
    return out


def check_numbers(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?%?", text) and not re.search(r"Intl\.NumberFormat|toLocaleString", text):
        out.append(_f("numbers", "info", "Hard-coded number grouping detected", "source",
                      "Number grouping may differ by locale.", wcag="3.1.2",
                      remediation="Use locale-aware number formatting."))
    return out


def check_data_entry(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in by_tag(doc.root, "input"):
        typ = node.attr("type")
        if typ in ("tel", "email", "number") and not node.attr("inputmode") and not node.attr("autocomplete"):
            out.append(_f("data-entry", "minor", "Input lacks inputmode or autocomplete", node_location(node),
                          "The keyboard and autofill may not match the expected input.", wcag="1.3.5",
                          remediation="Add inputmode and autocomplete tokens."))
    return out


def check_auth(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"onpaste\s*=\s*[\"']?return false|onpaste\s*=\s*[\"']?false", text, re.I):
        out.append(_f("auth", "serious", "Paste is blocked in a form", "source",
                      "Blocking paste prevents password managers and assistive technology.", wcag="3.3.8",
                      remediation="Allow paste and password managers."))
    for node in by_tag(doc.root, "input"):
        if node.attr("type") == "password" and not node.attr("autocomplete"):
            out.append(_f("auth", "minor", "Password field has no autocomplete token", node_location(node),
                          "Password managers need an autocomplete token.", wcag="3.3.8",
                          remediation="Add autocomplete=\"current-password\" or \"new-password\"."))
    if re.search(r"captcha", text, re.I) and not re.search(r"audio|alternative|passkey", text, re.I):
        out.append(_f("auth", "moderate", "CAPTCHA may lack a nonvisual alternative", "source",
                      "A CAPTCHA must have an accessible alternative.", wcag="3.3.8",
                      remediation="Provide a passkey, email link, or support-assisted alternative."))
    return out


def check_timeout(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"session.*timeout|timeout.*session|setTimeout.*logout", text, re.I):
        if not re.search(r"extend|warning|remaining|more time", text, re.I):
            out.append(_f("timeout", "moderate", "Session timeout without an extension control", "source",
                          "Users need to be warned and allowed to extend the session.", wcag="2.2.6",
                          remediation="Warn before timeout and provide an extend or cancel control."))
    return out


def check_financial(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"[$₱€£]\s?\d|\d+[.,]\d{2}", text) and not re.search(r"Intl\.NumberFormat|currency", text, re.I):
        out.append(_f("financial", "info", "Currency formatting may be hard-coded", "source",
                      "Currency placement and separators vary by locale.", wcag="3.1.2",
                      remediation="Use locale-aware currency formatting."))
    return out


def check_health(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"consent|medical|health", text, re.I) and not re.search(r"plain|summary|help", text, re.I):
        out.append(_f("health", "info", "Health content may need a plain-language summary", "source",
                      "Health information must be understandable, especially under stress.", wcag="3.1.5",
                      remediation="Add a plain-language summary and a human support route."))
    return out


def check_education(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"quiz|exam|assessment|question", text, re.I):
        if not re.search(r"time|accommodation|review", text, re.I):
            out.append(_f("education", "info", "Assessment may lack timing or review accommodations", "source",
                          "Timed assessments need accommodations and a review step.", wcag="2.2.1",
                          remediation="Document timing accommodations and a review workflow."))
    return out


def check_government(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"upload|requirements|eligibility|appeal", text, re.I):
        if not re.search(r"assist|help|contact|alternative", text, re.I):
            out.append(_f("government", "info", "Government service may lack an assisted channel", "source",
                          "Public services need an assisted alternative.", wcag="3.3.2",
                          remediation="Provide an assisted channel and clear contact information."))
    return out


def check_mobile(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not by_tag(doc.root, "meta"):
        out.append(_f("mobile", "info", "No viewport meta tag", "document",
                      "Mobile rendering may not adapt to the device width.", wcag="1.4.10",
                      remediation="Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."))
    return out


def check_kiosk(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"kiosk|terminal|touchscreen", text, re.I) and not re.search(r"assist|staff|help", text, re.I):
        out.append(_f("kiosk", "info", "Kiosk content may lack staff assistance", "source",
                      "Public terminals need a staff-assisted alternative.", wcag="2.2.6",
                      remediation="Provide a visible way to request staff assistance."))
    return out


def check_tv(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for video in by_tag(doc.root, "video"):
        if not video.attr("controls"):
            out.append(_f("tv", "moderate", "Video has no controls", node_location(video),
                          "TV and remote users need playback controls.", wcag="2.1.1",
                          remediation="Add controls or a custom remote-operable player."))
    return out


def check_xr(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"vr|xr|webgl|three\.js|canvas", text, re.I):
        if not re.search(r"caption|subtitle|seated|comfort|reduced.?motion", text, re.I):
            out.append(_f("xr", "info", "XR experience may lack comfort and caption options", "source",
                          "XR experiences need seated mode, motion comfort, and captions.", wcag="2.3.3",
                          remediation="Provide seated mode, comfort settings, and captions."))
    return out


def check_embedded(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"embedded|device|firmware", text, re.I) and not re.search(r"status|indicator|audio", text, re.I):
        out.append(_f("embedded", "info", "Embedded interface may lack non-visual status", "source",
                      "Status should not rely on color or a small display alone.", wcag="1.4.1",
                      remediation="Provide audio or tactile status feedback."))
    return out


def check_cognitive(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = doc.root.text_content()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) > 40:
        out.append(_f("cognitive", "info", "Long content may increase cognitive load", "content",
                      f"{len(sentences)} sentences detected.", wcag="3.1.5",
                      remediation="Chunk the content and provide a summary."))
    return out


def check_autism(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"autoplay|animation|sound", text, re.I) and not re.search(r"reduce|pause|mute", text, re.I):
        out.append(_f("autism", "info", "Sensory input may not be controllable", "source",
                      "Auto-playing sound or motion can be distressing.", wcag="2.2.2",
                      remediation="Provide pause, mute, and reduced-motion controls."))
    return out


def check_adhd(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"multi.?step|wizard|form", text, re.I) and not re.search(r"save|resume|progress", text, re.I):
        out.append(_f("adhd", "info", "Multi-step flow may lack save and resume", "source",
                      "Users with attention differences benefit from visible progress and saved state.", wcag="3.3.6",
                      remediation="Add progress indicators, save and resume, and chunk steps."))
    return out


def check_dyscalculia(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"\d{4,}", text) and not re.search(r"Intl\.NumberFormat|toLocaleString|unit", text):
        out.append(_f("dyscalculia", "info", "Large numbers may lack grouping or units", "source",
                      "Grouping and units support number comprehension.", wcag="1.4.8",
                      remediation="Use locale-aware grouping and include units."))
    return out


def check_memory(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"step\s*\d|enter.*again|re-?enter", text, re.I) and not re.search(r"summary|history|saved", text, re.I):
        out.append(_f("memory", "info", "Memory-heavy flow may lack summaries or history", "source",
                      "Users should not have to recall information across steps.", wcag="3.3.6",
                      remediation="Provide summaries, history, and visible choices."))
    return out


def check_component(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for node in doc.root.iter():
        if node.attr("role") in ("button", "link", "checkbox", "radio", "tab", "menuitem"):
            if node.tag not in ("button", "a", "input", "summary"):
                out.append(_f("component", "info", "Custom widget uses a role on a non-native element", node_location(node),
                              "Custom widgets must implement keyboard, focus, and state behavior.", wcag="4.1.2",
                              remediation="Use a native element or implement the full widget pattern."))
                break
    return out


def check_help_center(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if not re.search(r"search|contact|help", text, re.I):
        out.append(_f("help-center", "moderate", "Help center may lack search or contact", "source",
                      "Users need to find help and reach a person.", wcag="3.2.6",
                      remediation="Provide search, a contact channel, and accessible articles."))
    return out


def check_dashboard(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for svg in by_tag(doc.root, "svg"):
        if svg.find_all("rect") or svg.find_all("path"):
            if not svg.find_all("title") and not svg.attr("aria-label"):
                out.append(_f("dashboard", "moderate", "Dashboard visualization has no accessible name", node_location(svg),
                              "Charts and filters need accessible names and data alternatives.", wcag="1.1.1",
                              remediation="Add a title, a data table, and accessible filter controls."))
                break
    return out


def check_electron(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"BrowserWindow|webview|electron", text, re.I):
        if not re.search(r"menu|shortcut|focus", text, re.I):
            out.append(_f("electron", "info", "Electron integration may lack native menu and focus review", "source",
                          "Chromium content plus native menus need separate accessibility review.", wcag="2.1.1",
                          remediation="Review native menus, shortcuts, dialogs, and focus with the platform AT."))
    return out


def check_regression(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.input_path:
        return out
    data = read_json(ctx.input_path)
    baseline = data.get("baseline", data) if isinstance(data, dict) else {}
    current = data.get("current", []) if isinstance(data, dict) else []
    base_ids = {f.get("id") or f.get("title") for f in baseline.get("findings", []) if isinstance(f, dict)}
    cur_ids = {f.get("id") or f.get("title") for f in current if isinstance(f, dict)}
    new = cur_ids - base_ids
    fixed = base_ids - cur_ids
    for item in new:
        out.append(_f("regression", "serious", "New barrier compared with the baseline", str(item),
                      "This finding was not present in the accepted baseline.", wcag="2.4.7",
                      remediation="Investigate the change and fix the regression before release."))
    if fixed:
        out.append(_f("regression", "info", f"{len(fixed)} baseline finding(s) no longer detected", "baseline",
                      "Confirm the fix and update the baseline deliberately."))
    return out


def check_emoji(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    emoji_re = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    for node in doc.root.iter():
        if node.tag in ("script", "style"):
            continue
        text = node.text
        if emoji_re.search(text):
            out.append(_f("emoji", "info", "Emoji may be announced with an unexpected name", node_location(node),
                          "Emoji names vary by platform and may not match the intended meaning.", wcag="1.1.1",
                          remediation="Add an aria-label with the intended meaning or mark the emoji aria-hidden."))
            break
    return out


def check_image_text(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for img in by_tag(doc.root, "img"):
        alt = img.attr("alt").lower()
        if any(w in alt for w in ("text", "screenshot", "code", "label")):
            out.append(_f("image-text", "info", "Image may contain meaningful text", node_location(img),
                          "Text in images is not accessible; provide real text or a full description.", wcag="1.4.5",
                          remediation="Replace the image text with real text or provide an equivalent."))
    return out


def check_science(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"<sup>|<sub>|×|÷|µ|°", text):
        out.append(_f("science", "info", "Scientific notation detected", "source",
                      "Verify that notation is exposed in a readable way.", wcag="1.1.1",
                      remediation="Use MathML or a text alternative for formulas and units."))
    return out


def check_transcript(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    for video in by_tag(doc.root, "video") + by_tag(doc.root, "audio"):
        if video.find_all("track"):
            if not any("transcript" in a.text_content().lower() or "transcript" in a.attr("href").lower() for a in doc.root.find_all("a")):
                out.append(_f("transcript", "info", "Media has captions but no transcript link", node_location(video),
                              "A transcript provides a searchable text alternative.", wcag="1.2.1",
                              remediation="Link to a descriptive transcript."))
    return out


def check_search(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    search_inputs = [n for n in by_tag(doc.root, "input") if n.attr("type") == "search" or "search" in n.attr("name")]
    for node in search_inputs:
        if not accessible_name(node, doc.root):
            out.append(_f("search", "serious", "Search input has no accessible name", node_location(node),
                          "The search field is not identified.", wcag="4.1.2",
                          remediation="Add a label or aria-label."))
    if not search_inputs:
        out.append(_f("search", "info", "No search field detected", "document",
                      "If the help center relies on search, verify it is present and accessible.", wcag="3.2.6",
                      remediation="Add an accessible search field and a no-results state."))
    return out


def check_email(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"<table[^>]*width\s*=\s*[\"']?\d{3,}", text, re.I):
        out.append(_f("email", "info", "Fixed-width email table detected", "email",
                      "Fixed widths can break on small screens.", wcag="1.4.10",
                      remediation="Use fluid widths and test in small viewports."))
    if re.search(r"direction\s*:\s*ltr", text, re.I):
        out.append(_f("email", "info", "Hard-coded LTR direction in email", "email",
                      "RTL locales may render incorrectly.", wcag="1.3.2",
                      remediation="Set direction from the locale and test RTL clients."))
    return out


def check_print(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if "@media print" not in text and "<html" in text.lower():
        out.append(_f("print", "info", "No print stylesheet detected", "source",
                      "Print and export layouts need separate verification.", wcag="1.4.10",
                      remediation="Add a print stylesheet and test pagination and fonts."))
    return out


def check_animation_control(doc: HTMLTree, ctx: EngineContext) -> list[Finding]:
    out: list[Finding] = []
    text = ctx.read_input() if ctx.input_path else ""
    if re.search(r"animation|transition|autoplay|marquee", text, re.I) and not re.search(r"pause|stop|control|prefers-reduced-motion", text, re.I):
        out.append(_f("animation-control", "moderate", "Moving content may lack a pause or stop control", "source",
                      "Animation or auto-playing content needs a mechanism to pause, stop, or hide.", wcag="2.2.2",
                      remediation="Add a pause/stop control and honor prefers-reduced-motion."))
    return out


CHECKS: dict[str, Callable[[HTMLTree, EngineContext], list[Finding]]] = {
    "document": check_document, "landmarks": check_landmarks, "headings": check_headings,
    "semantics": check_semantics, "names": check_names, "aria": check_aria,
    "forms": check_forms, "form-errors": check_form_errors, "links": check_links,
    "images": check_images, "icons": check_icons, "tables": check_tables,
    "media": check_media, "math": check_math, "charts": check_charts,
    "iframe": check_iframe, "focus": check_focus, "focus-order": check_focus_order,
    "keyboard-trap": check_keyboard_trap, "shortcuts": check_shortcuts,
    "target-size": check_target_size, "gestures": check_gestures, "motion": check_motion,
    "photosensitivity": check_photosensitivity, "contrast": check_contrast,
    "non-color": check_non_color, "text-resize": check_text_resize, "reflow": check_reflow,
    "reading-order": check_reading_order, "typography": check_typography,
    "plain-language": check_plain_language, "i18n": check_i18n,
    "externalization": check_externalization, "placeholders": check_placeholders,
    "bidi": check_bidi, "bidi-debug": check_bidi_debug, "rtl": check_rtl,
    "unicode": check_unicode, "key-governance": check_key_governance,
    "dynamic": check_dynamic, "consistency": check_consistency,
    "notifications": check_notifications, "help": check_help,
    "truncation": check_truncation, "numbers": check_numbers,
    "data-entry": check_data_entry, "auth": check_auth, "timeout": check_timeout,
    "financial": check_financial, "health": check_health, "education": check_education,
    "government": check_government, "mobile": check_mobile, "kiosk": check_kiosk,
    "tv": check_tv, "xr": check_xr, "embedded": check_embedded,
    "cognitive": check_cognitive, "autism": check_autism, "adhd": check_adhd,
    "dyscalculia": check_dyscalculia, "memory": check_memory,
    "component": check_component, "help-center": check_help_center,
    "dashboard": check_dashboard, "electron": check_electron, "regression": check_regression,
    "emoji": check_emoji, "image-text": check_image_text, "science": check_science,
    "transcript": check_transcript, "search": check_search, "email": check_email,
    "print": check_print, "animation-control": check_animation_control,
}

# --------------------------------------------------------------------------- #
# Static audit
# --------------------------------------------------------------------------- #
SOURCE_TEXT_CHECKS = {
    "i18n", "externalization", "unicode", "bidi", "bidi-debug", "rtl", "numbers",
    "auth", "timeout", "financial", "health", "education", "government", "kiosk",
    "xr", "embedded", "autism", "adhd", "dyscalculia", "memory", "email", "print",
    "gestures", "help-center", "electron", "science", "animation-control",
}
CSS_TEXT_CHECKS = SOURCE_TEXT_CHECKS | {
    "contrast", "motion", "photosensitivity", "text-resize", "reflow",
    "reading-order", "typography", "non-color", "truncation",
}


def run_static_audit(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires an HTML or source input.",
                        "Pass --input with an HTML file or a directory of source files.")
    files = iter_files(ctx.input_path, [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"],
                       ctx.limits.get("max_files", 200), ctx.limits.get("max_bytes", 8 * 1024 * 1024))
    if not files:
        raise ToolError("input-unsupported", "No HTML, CSS, or source files found in the input.",
                        "Pass an .html file or a directory containing source files.", ctx.input_path)
    selected = list(CHECKS) if ctx.checks == ["all"] else [c for c in ctx.checks if c in CHECKS]
    unknown = [c for c in ctx.checks if c not in CHECKS and c != "all"]
    if unknown:
        raise ToolError("unknown-check", f"Unknown check(s): {', '.join(unknown)}",
                        f"Valid checks: {', '.join(sorted(CHECKS))}.", ", ".join(unknown))
    findings: list[Finding] = []
    parsed = 0
    json_only = {"placeholders", "key-governance", "regression"}
    counts = {"html": 0, "css": 0, "source": 0}
    for path in files:
        suffix = path.suffix.lower()
        is_html = suffix in (".html", ".htm", ".xhtml", ".vue", ".svelte")
        is_css = suffix == ".css"
        is_source = suffix in (".js", ".jsx", ".ts", ".tsx")
        text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        parse_text = f"<html><head><style>{text}</style></head><body></body></html>" if is_css else text
        try:
            doc = parse_html(parse_text)
        except Exception as exc:
            findings.append(_f("document", "minor", f"Could not parse {path.name}", str(path),
                                str(exc), remediation="Fix the markup syntax and retry."))
            continue
        parsed += 1
        counts["html" if is_html else "css" if is_css else "source"] += 1
        ctx.current_file = str(path)
        if is_html:
            checks_to_run = selected
        elif is_css:
            checks_to_run = [c for c in selected if c in CSS_TEXT_CHECKS]
        else:
            checks_to_run = [c for c in selected if c in SOURCE_TEXT_CHECKS]
        for check in checks_to_run:
            if check in json_only and suffix != ".json":
                continue
            try:
                for f in CHECKS[check](doc, ctx):
                    f.location = f"{path.name}: {f.location}" if f.location else path.name
                    findings.append(f)
            except Exception as exc:
                findings.append(_f("document", "info", f"Check '{check}' could not complete", f"{path.name}",
                                    str(exc), remediation="Review the input or report the limitation."))
        ctx.current_file = None
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    severity_counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    summary = (f"Analyzed {parsed} file(s) ({counts['html']} HTML, {counts['css']} CSS, {counts['source']} source) "
               f"with {len(selected)} configured check(s): "
               f"{severity_counts['critical']} critical, {severity_counts['serious']} serious, "
               f"{severity_counts['moderate']} moderate, {severity_counts['minor']} minor.")
    return result(status, summary, findings,
                  metrics={"files": len(files), "parsed": parsed, "checks": selected,
                           "checks_count": len(selected), "file_types": counts},
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=_top_remediations(findings))


def _top_remediations(findings: list[Finding], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for f in findings:
        sev = f.get("severity") if isinstance(f, dict) else f.severity
        rem = f.get("remediation") if isinstance(f, dict) else f.remediation
        title = f.get("title") if isinstance(f, dict) else f.title
        if sev in ("critical", "serious") and rem and rem not in seen:
            seen.add(rem)
            out.append(f"{title}: {rem}")
        if len(out) >= limit:
            break
    if not out:
        out = ["Review the report with the accountable owner and schedule manual AT testing."]
    return out


def run_contrast_audit(ctx: EngineContext) -> EngineResult:
    if ctx.input_path and Path(ctx.input_path).is_dir():
        files = iter_files(ctx.input_path, [".html", ".htm", ".xhtml", ".css"],
                           ctx.limits.get("max_files", 200), ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        if not files:
            raise ToolError("input-not-found", "No HTML or CSS file found in the input directory.",
                            "Add an HTML or CSS file, or pass a file directly.", str(ctx.input_path))
        text = read_text(files[0], ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        ctx.current_file = str(files[0])
    else:
        text = ctx.read_input()
        files = [Path(ctx.input_path)] if ctx.input_path else []
        ctx.current_file = str(files[0]) if files else None
    doc = parse_html(text) if "<html" in text.lower() or "<body" in text.lower() else HTMLTree()
    if doc.root.children:
        findings = CHECKS["contrast"](doc, ctx)
    else:
        findings = []
        rules = parse_css_rules(extract_css(text))
        for selector, decls in rules:
            fg = parse_color(decls.get("color", ""))
            bg = parse_color(decls.get("background-color", decls.get("background", "")))
            if not fg or not bg:
                continue
            size = 16.0
            m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", decls.get("font-size", ""))
            if m:
                size = float(m.group(1))
            threshold = 3.0 if is_large_text(size) else 4.5
            ratio = contrast_ratio(fg[:3], bg[:3])
            if ratio < threshold:
                findings.append(_f("contrast", "serious", f"Contrast ratio {ratio:.2f}:1 is below {threshold}:1", selector,
                                   f"{decls.get('color')} on {decls.get('background-color', decls.get('background'))}",
                                   wcag="1.4.3", remediation="Increase contrast to meet the threshold."))
    if ctx.checks != ["all"] and "focus" in ctx.checks:
        if not re.search(r":focus[^{]*\{[^}]*outline", text, re.I):
            findings.append(_f("contrast", "moderate", "No explicit focus outline style found", "stylesheet",
                                "Keyboard focus may rely on the browser default, which can be low contrast.", wcag="2.4.7",
                                remediation="Define a visible focus indicator with at least 3:1 contrast."))
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    return result(status, f"Checked color pairs and focus styling: {counts['critical']} critical, {counts['serious']} serious, {counts['moderate']} moderate.",
                  findings, metrics={"files": len(files), "rules": len(parse_css_rules(extract_css(text)))},
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=_top_remediations(findings))


# --------------------------------------------------------------------------- #
# Document audit
# --------------------------------------------------------------------------- #
def _pdf_bytes(path: Path) -> bytes:
    return path.read_bytes()


def run_document_audit(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a document file.",
                        "Pass --input with a PDF, DOCX, XLSX, PPTX, or EPUB file.")
    path = resolve_input_file(ctx, [".pdf", ".docx", ".xlsx", ".pptx", ".epub"], "document")
    fmt = ctx.params.get("format", "auto")
    kind = file_kind(path)
    if fmt == "pdf" and kind != "pdf":
        raise ToolError("format-mismatch", f"Expected a PDF but got {kind}.", "Pass a PDF file.", str(path))
    if fmt == "ooxml" and kind != "ooxml":
        raise ToolError("format-mismatch", f"Expected an OOXML file but got {kind}.", "Pass a DOCX, XLSX, or PPTX file.", str(path))
    if fmt == "epub" and kind != "epub":
        raise ToolError("format-mismatch", f"Expected an EPUB but got {kind}.", "Pass an EPUB file.", str(path))
    if kind == "pdf":
        res = _audit_pdf(path, ctx)
    elif kind == "ooxml":
        res = _audit_ooxml(path, ctx)
    elif kind == "epub":
        res = _audit_epub(path, ctx)
    else:
        raise ToolError("format-unsupported", f"Unsupported document format: {path.suffix}",
                        "Supported formats: PDF, DOCX, XLSX, PPTX, EPUB.", str(path))
    res.limitations = list(ctx.cap.get("limitations", []))
    res.next_steps = _top_remediations(res.findings)
    return res


def _audit_pdf(path: Path, ctx: EngineContext) -> EngineResult:
    data = _pdf_bytes(path)
    if not data.startswith(b"%PDF"):
        raise ToolError("invalid-pdf", "File is not a PDF (missing %PDF signature).", "Pass a valid PDF file.", str(path))
    text = data.decode("latin-1", "replace")
    findings: list[Finding] = []
    checks = {
        "Language": (r"/Lang\s*\(", "Set the document language in the catalog /Lang entry."),
        "Structure tree": (r"/StructTreeRoot", "Add a structure tree so reading order and semantics are exposed."),
        "MarkInfo": (r"/MarkInfo", "Set /Marked true in the MarkInfo dictionary."),
        "Tabs": (r"/Tabs", "Set /Tabs to /S or /R for consistent tabbing."),
        "Viewer preferences": (r"/ViewerPreferences", "Add viewer preferences such as /DisplayDocTitle true."),
        "Title": (r"/Title\s*\(", "Set the document title in the metadata."),
    }
    for label, (pattern, remediation) in checks.items():
        if not re.search(pattern, text):
            findings.append(_f("pdf-structure", "serious" if label in ("Language", "Structure tree", "MarkInfo") else "moderate",
                               f"PDF {label.lower()} not detected", str(path.name),
                               f"The {label} entry was not found in the raw PDF.", wcag="1.3.1",
                               remediation=remediation))
    page_count = len(re.findall(r"/Type\s*/Page[^s]", text))
    if page_count:
        findings.append(_f("pdf-structure", "info", f"PDF has approximately {page_count} page(s)", str(path.name),
                            "Page count is approximate and based on raw object scanning.", wcag="1.3.1"))
    if "/AcroForm" in text:
        findings.append(_f("pdf-forms", "info", "AcroForm detected", str(path.name),
                            "Verify field names, tooltips, tab order, required states, and error messages.", wcag="3.3.2",
                            remediation="Test the form with a screen reader and keyboard only."))
    if "/StructTreeRoot" in text and "/MarkInfo" in text:
        findings.append(_f("pdf-structure", "info", "Structure tree and MarkInfo present", str(path.name),
                            "This is a structural signal only; validate with a PDF/UA validator and a screen reader.", wcag="1.3.1"))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"PDF structural check for {path.name}: {len(findings)} finding(s).", findings,
                  metrics={"format": "pdf", "bytes": len(data), "sha256": short_hash(data.decode('latin-1','replace'), 16)},
                  limitations=["Raw PDF inspection is not a replacement for PDF/UA validation or screen-reader testing."])


def _audit_ooxml(path: Path, ctx: EngineContext) -> EngineResult:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            def read(name: str) -> str:
                try:
                    return z.read(name).decode("utf-8", "replace")
                except KeyError:
                    return ""
            core = read("docProps/core.xml")
            app = read("docProps/app.xml")
            lang = re.search(r"<dc:language>([^<]+)</dc:language>", core)
            if not lang:
                findings.append(_f("office-language", "moderate", "Document language is not set", str(path.name),
                                    "No dc:language element found in core properties.", wcag="3.1.1",
                                    remediation="Set the document language in the file properties."))
            title = re.search(r"<dc:title>([^<]+)</dc:title>", core)
            if not title:
                findings.append(_f("office-title", "minor", "Document title is not set", str(path.name),
                                    "A title helps users identify the document.", wcag="2.4.2",
                                    remediation="Set the document title in core properties."))
            if path.suffix.lower() == ".docx":
                doc = read("word/document.xml")
                headings = len(re.findall(r'w:pStyle w:val="Heading', doc))
                if not headings:
                    findings.append(_f("office-headings", "serious", "No heading styles found", "word/document.xml",
                                        "The document may use visual formatting instead of semantic headings.", wcag="1.3.1",
                                        remediation="Apply Heading 1-3 styles to section titles."))
                images = re.findall(r"<wp:docPr[^>]*descr=\"([^\"]*)\"", doc)
                drawings = len(re.findall(r"<w:drawing", doc))
                if drawings and not any(images):
                    findings.append(_f("office-alt-text", "serious", "Images may lack alt text", "word/document.xml",
                                        f"{drawings} drawing(s) found without a descr attribute.", wcag="1.1.1",
                                        remediation="Add alt text to every meaningful image."))
                tables = len(re.findall(r"<w:tbl>", doc))
                if tables:
                    findings.append(_f("office-tables", "info", f"{tables} table(s) found", "word/document.xml",
                                        "Verify header rows, no merged cells, and a caption.", wcag="1.3.1"))
            elif path.suffix.lower() == ".xlsx":
                wb = read("xl/workbook.xml")
                sheets = re.findall(r'<sheet name="([^"]+)"', wb)
                if not sheets:
                    findings.append(_f("office-sheets", "moderate", "No named sheets found", "xl/workbook.xml",
                                        "Workbook structure could not be determined.", wcag="1.3.1",
                                        remediation="Name each sheet meaningfully and order them logically."))
                tables = len(re.findall(r"<table ", read("xl/tables/table1.xml")))
                if not tables:
                    findings.append(_f("office-tables", "info", "No named table detected", "xl/workbook.xml",
                                        "Use a named Excel table with a header row.", wcag="1.3.1",
                                        remediation="Format the data range as a named table."))
            elif path.suffix.lower() == ".pptx":
                slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
                missing_titles = 0
                missing_alt = 0
                for slide in slides:
                    xml = read(slide)
                    if not re.search(r"<p:ph type=\"title\"|<p:ph type=\"ctrTitle\"", xml):
                        missing_titles += 1
                    pics = len(re.findall(r"<p:pic>", xml))
                    descr = len(re.findall(r"descr=\"[^\"]+\"", xml))
                    if pics and not descr:
                        missing_alt += 1
                if missing_titles:
                    findings.append(_f("office-titles", "serious", f"{missing_titles} slide(s) without a title placeholder", "ppt/slides",
                                        "Every slide needs a unique title for navigation.", wcag="1.3.1",
                                        remediation="Add a title placeholder to each slide."))
                if missing_alt:
                    findings.append(_f("office-alt-text", "serious", f"{missing_alt} slide(s) with images lacking alt text", "ppt/slides",
                                        "Images need alt text or decorative marking.", wcag="1.1.1",
                                        remediation="Add alt text to meaningful images."))
            findings.append(_f("office-metadata", "info", f"OOXML package has {len(names)} parts", str(path.name),
                                "Structural checks only; render the document and test with a screen reader.", wcag="1.3.1"))
    except zipfile.BadZipFile as exc:
        raise ToolError("invalid-ooxml", f"File is not a valid OOXML package: {exc}", "Re-export the document.", str(path))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"OOXML structural check for {path.name}: {len(findings)} finding(s).", findings,
                  metrics={"format": "ooxml", "bytes": path.stat().st_size},
                  limitations=["This is a structural XML check, not a rendering or full accessibility validation."])


def _audit_epub(path: Path, ctx: EngineContext) -> EngineResult:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "META-INF/container.xml" not in names:
                findings.append(_f("epub-structure", "serious", "EPUB container.xml is missing", str(path.name),
                                    "The package document cannot be located.", wcag="1.3.1",
                                    remediation="Re-export the EPUB with a valid container.xml."))
            opf = [n for n in names if n.endswith(".opf")]
            if not opf:
                findings.append(_f("epub-structure", "serious", "EPUB package document (.opf) is missing", str(path.name),
                                    "The OPF is required for EPUB 3.", wcag="1.3.1",
                                    remediation="Re-export the EPUB."))
            else:
                content = z.read(opf[0]).decode("utf-8", "replace")
                if "<dc:language>" not in content:
                    findings.append(_f("epub-language", "moderate", "EPUB language is not set", opf[0],
                                        "Set dc:language in the package document.", wcag="3.1.1",
                                        remediation="Add dc:language to the OPF metadata."))
                if "<dc:title>" not in content:
                    findings.append(_f("epub-title", "minor", "EPUB title is not set", opf[0],
                                        "Set dc:title in the package document.", wcag="2.4.2",
                                        remediation="Add dc:title to the OPF metadata."))
                if "properties=\"nav\"" not in content:
                    findings.append(_f("epub-nav", "moderate", "EPUB navigation document is not declared", opf[0],
                                        "EPUB 3 requires a navigation document.", wcag="1.3.1",
                                        remediation="Declare the nav document in the OPF."))
            xhtml = [n for n in names if n.endswith((".xhtml", ".html"))]
            missing_lang = 0
            missing_title = 0
            missing_alt = 0
            for name in xhtml:
                content = z.read(name).decode("utf-8", "replace")
                if not re.search(r"<html[^>]*\slang=", content):
                    missing_lang += 1
                if "<title>" not in content:
                    missing_title += 1
                if re.search(r"<img(?![^>]*alt=)[^>]*>", content):
                    missing_alt += 1
            if missing_lang:
                findings.append(_f("epub-language", "moderate", f"{missing_lang} content document(s) without lang", "EPUB content",
                                    "Set the language on each content document.", wcag="3.1.1",
                                    remediation="Add lang to the html element of each content document."))
            if missing_title:
                findings.append(_f("epub-title", "minor", f"{missing_title} content document(s) without a title", "EPUB content",
                                    "Each content document should have a title.", wcag="2.4.2",
                                    remediation="Add a title to each content document."))
            if missing_alt:
                findings.append(_f("epub-alt-text", "serious", f"{missing_alt} content document(s) with images lacking alt", "EPUB content",
                                    "Images need alt text or decorative marking.", wcag="1.1.1",
                                    remediation="Add alt text to meaningful images."))
    except zipfile.BadZipFile as exc:
        raise ToolError("invalid-epub", f"File is not a valid EPUB package: {exc}", "Re-export the EPUB.", str(path))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"EPUB structural check for {path.name}: {len(findings)} finding(s).", findings,
                  metrics={"format": "epub", "bytes": path.stat().st_size},
                  limitations=["EPUB checks are structural; reading-system behavior requires manual testing."])


# --------------------------------------------------------------------------- #
# Media audit
# --------------------------------------------------------------------------- #
def run_media_audit(ctx: EngineContext) -> EngineResult:
    files = ctx.html_files()
    if not files:
        raise ToolError("input-required", "This capability requires an HTML file or directory.",
                        "Pass --input with an HTML file or a directory of HTML files.")
    selected = list(CHECKS) if ctx.checks == ["all"] else [c for c in ctx.checks if c in CHECKS]
    unknown = [c for c in ctx.checks if c not in CHECKS and c != "all"]
    if unknown:
        raise ToolError("unknown-check", f"Unknown check(s): {', '.join(unknown)}",
                        f"Valid checks: {', '.join(sorted(CHECKS))}.", ", ".join(unknown))
    findings: list[Finding] = []
    inventory = {"images": 0, "videos": 0, "audio": 0, "svg": 0, "math": 0, "tracks": 0}
    for path in files:
        text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        doc = parse_html(text)
        inventory["images"] += len(by_tag(doc.root, "img"))
        inventory["videos"] += len(by_tag(doc.root, "video"))
        inventory["audio"] += len(by_tag(doc.root, "audio"))
        inventory["svg"] += len(by_tag(doc.root, "svg"))
        inventory["math"] += len(by_tag(doc.root, "math"))
        inventory["tracks"] += len(by_tag(doc.root, "track"))
        ctx.current_file = str(path)
        for check in selected:
            try:
                for f in CHECKS[check](doc, ctx):
                    f.location = f"{path.name}: {f.location}" if f.location else path.name
                    findings.append(f)
            except Exception as exc:
                findings.append(_f("media", "info", f"Check '{check}' could not complete", path.name, str(exc)))
        ctx.current_file = None
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Media audit of {len(files)} file(s): {inventory['images']} image(s), {inventory['videos']} video(s), {inventory['audio']} audio, {inventory['tracks']} track(s).",
                  findings, metrics={"inventory": inventory, "files": len(files)},
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=_top_remediations(findings))


# --------------------------------------------------------------------------- #
# i18n audit
# --------------------------------------------------------------------------- #
def _flatten_strings(obj: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten_strings(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten_strings(v, f"{prefix}[{i}]"))
    elif isinstance(obj, str):
        out[prefix] = obj
    return out


PLACEHOLDER_PATTERNS = [r"\{\{[^}]+\}\}", r"(?<!\{)\{[a-zA-Z0-9_]+\}(?!\})", r"%(?:\([^)]+\))?[sdif]", r"\$\{[^}]+\}"]


def _placeholders(value: str) -> list[str]:
    found: list[str] = []
    for pat in PLACEHOLDER_PATTERNS:
        found.extend(re.findall(pat, value))
    return sorted(set(found))


def run_i18n_audit(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a locale resource or source directory.",
                        "Pass --input with a JSON/properties file or a source directory.")
    path = Path(ctx.input_path)
    checks = ctx.checks or ["hardcoded", "placeholders", "unicode", "bidi"]
    findings: list[Finding] = []
    metrics: dict[str, Any] = {"checks": checks}
    source_strings: dict[str, str] = {}
    target_strings: dict[str, str] = {}
    if path.is_dir():
        json_files = sorted(iter_files(path, [".json"], ctx.limits.get("max_files", 200)))
        if len(json_files) >= 2:
            source_strings = _flatten_strings(read_json(json_files[0]))
            for other in json_files[1:]:
                target_strings.update(_flatten_strings(read_json(other)))
            metrics["source_file"] = json_files[0].name
            metrics["target_files"] = [p.name for p in json_files[1:]]
        else:
            for p in ctx.source_files():
                text = read_text(p, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
                if "hardcoded" in checks or "externalization" in checks or "concatenation" in checks:
                    if re.search(r"[\"'][A-Z][^\"']{6,}[\"']", text):
                        findings.append(_f("hardcoded", "moderate", "Possible hard-coded user-facing string", p.name,
                                            "A quoted string may be user-facing text.", wcag="3.1.2",
                                            remediation="Externalize the string into a locale resource."))
                    if re.search(r"\+\s*[\"'][^\"']+[\"']|['\"][^'\"]+['\"]\s*\+", text):
                        findings.append(_f("concatenation", "moderate", "String concatenation detected", p.name,
                                            "Concatenation can break grammar and word order.", wcag="3.1.2",
                                            remediation="Use full sentences with placeholders."))
    else:
        if path.suffix.lower() == ".properties":
            text = read_text(path)
            props = {}
            for line in text.splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
            source_strings = props
            metrics["resource_file"] = path.name
        elif path.suffix.lower() == ".csv":
            rows, headers = read_csv(path)
            if "key" in headers and "value" in headers:
                source_strings = {r.get("key", ""): r.get("value", "") for r in rows}
            elif "source" in headers and "target" in headers:
                source_strings = {r.get("source", ""): r.get("target", "") for r in rows}
            else:
                source_strings = {f"row_{i}": " ".join(r.values()) for i, r in enumerate(rows, 1)}
            metrics["resource_file"] = path.name
        elif path.suffix.lower() == ".txt":
            source_strings = {"text": read_text(path)}
            metrics["resource_file"] = path.name
        else:
            data = read_json(path)
            if isinstance(data, dict) and ("source" in data or "target" in data):
                source_strings = _flatten_strings(data.get("source", {}))
                target_strings = _flatten_strings(data.get("target", {}))
            else:
                source_strings = _flatten_strings(data)
            metrics["resource_file"] = path.name
    if "unicode" in checks or "encoding" in checks:
        raw = path.read_bytes() if path.is_file() else b""
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_f("unicode", "serious", "Resource is not valid UTF-8", str(path),
                                "The file cannot be decoded as UTF-8.", wcag="3.1.2",
                                remediation="Re-export the resource as UTF-8."))
        if b"\xef\xbf\xbd" in raw:
            findings.append(_f("unicode", "serious", "Replacement character (mojibake) detected", str(path),
                                "The file contains U+FFFD.", wcag="3.1.2",
                                remediation="Re-export from the source encoding."))
    if "bilingual-parity" in checks or "untranslated" in checks:
        if source_strings and target_strings:
            missing = sorted(set(source_strings) - set(target_strings))
            extra = sorted(set(target_strings) - set(source_strings))
            for key in missing[:20]:
                findings.append(_f("bilingual-parity", "serious", "Source key is missing in the target", key,
                                    "The target locale does not have this key.", wcag="3.1.2",
                                    remediation="Add the key to the target resource or document the fallback."))
            for key in extra[:10]:
                findings.append(_f("bilingual-parity", "minor", "Target key is not in the source", key,
                                    "The target has an extra key.", wcag="3.1.2",
                                    remediation="Remove the stale key or add it to the source."))
            metrics["source_keys"] = len(source_strings)
            metrics["target_keys"] = len(target_strings)
            metrics["missing_keys"] = len(missing)
            metrics["extra_keys"] = len(extra)
            if "untranslated" in checks:
                for key in sorted(set(source_strings) & set(target_strings)):
                    src = source_strings[key].strip()
                    tgt = target_strings[key].strip()
                    if not tgt:
                        findings.append(_f("untranslated", "serious", "Target string is empty", key,
                                            "The translation is missing.", wcag="3.1.2",
                                            remediation="Translate the string or document the fallback."))
                    elif tgt.lower() == src.lower() and re.search(r"[A-Za-z]{4,}", src):
                        findings.append(_f("untranslated", "moderate", "Target string equals the source", key,
                                            f"Value '{src[:60]}' appears untranslated.", wcag="3.1.2",
                                            remediation="Translate the string or confirm it is intentionally the same."))
    if "placeholders" in checks:
        for key, value in (target_strings or source_strings).items():
            ph = _placeholders(value)
            if not ph:
                continue
            if len(ph) != len(set(ph)):
                findings.append(_f("placeholders", "serious", "Duplicate placeholder", key,
                                    f"Placeholders {ph} contain duplicates.", wcag="3.1.2",
                                    remediation="Use each placeholder once."))
            if source_strings and key in source_strings:
                src_ph = _placeholders(source_strings[key])
                if src_ph != ph:
                    findings.append(_f("placeholders", "serious", "Placeholder mismatch with source", key,
                                        f"Source {src_ph} vs target {ph}.", wcag="3.1.2",
                                        remediation="Preserve the source placeholders exactly."))
    if "key-governance" in checks:
        keys = list((target_strings or source_strings).keys())
        if len(keys) != len(set(keys)):
            findings.append(_f("key-governance", "moderate", "Duplicate keys detected", "resource",
                                "Duplicate keys cause inconsistent translations.", wcag="3.1.2",
                                remediation="Remove duplicate keys."))
        bad = [k for k in keys if re.search(r"[A-Z]|_", k)]
        if bad:
            findings.append(_f("key-governance", "info", "Keys are not lower-case hyphenated", "resource",
                                f"Examples: {', '.join(bad[:3])}.", wcag="3.1.2",
                                remediation="Use a consistent key convention."))
    if "plural" in checks:
        for key, value in (target_strings or source_strings).items():
            if re.search(r"\b(one|other|zero|two|few|many)\b", value, re.I) and "{" not in value:
                findings.append(_f("plural", "moderate", "Plural category word without ICU syntax", key,
                                    "The string may not adapt to plural rules.", wcag="3.1.2",
                                    remediation="Use ICU MessageFormat or the platform plural API."))
    if "rtl" in checks or "bidi" in checks:
        for key, value in (target_strings or source_strings).items():
            if re.search(r"[\u0600-\u06ff\u0590-\u05ff]", value):
                findings.append(_f("bidi", "info", "RTL script content in a resource", key,
                                    "Verify direction, punctuation, and placeholder placement.", wcag="1.3.2",
                                    remediation="Test the string in an RTL layout with a native reviewer."))
                break
    if "layout" in checks:
        for p in ctx.source_files():
            text = read_text(p, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
            if re.search(r"width\s*:\s*\d{3,}px|white-space\s*:\s*nowrap|text-overflow\s*:\s*ellipsis", text, re.I):
                findings.append(_f("layout", "info", "Fixed-width or truncation CSS may break localized text", p.name,
                                    "Expansion can clip labels and controls.", wcag="1.4.10",
                                    remediation="Use flexible widths and test the longest localized string."))
                break
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"i18n audit with {len(checks)} check(s): {len(findings)} finding(s).", findings,
                  metrics=metrics, limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=_top_remediations(findings))


# --------------------------------------------------------------------------- #
# Pseudo-localization
# --------------------------------------------------------------------------- #
PSEUDO_MAP = str.maketrans({
    "a": "á", "b": "ƀ", "c": "ç", "d": "ð", "e": "é", "f": "ƒ", "g": "ĝ",
    "h": "ĥ", "i": "í", "j": "ĵ", "k": "ķ", "l": "ļ", "m": "ɱ", "n": "ñ",
    "o": "ó", "p": "þ", "q": "q", "r": "ŕ", "s": "š", "t": "ţ", "u": "ú",
    "v": "ṽ", "w": "ŵ", "x": "ẋ", "y": "ý", "z": "ž",
})


def _pseudo(value: str, expansion: float) -> str:
    parts = re.split(r"(\{\{[^}]+\}\}|\{[a-zA-Z0-9_]+\}|%s|%d|%i|%f|\$\{[^}]+\})", value)
    translated = "".join(part if _placeholders(part) else part.translate(PSEUDO_MAP) for part in parts)
    if expansion > 1.0:
        filler = "·" * max(0, int(len(translated) * (expansion - 1.0)))
        translated = translated + filler
    return f"⟦{translated}⟧"


def _set_nested(root: dict[str, Any], key: str, value: str) -> None:
    tokens = re.findall(r"[^.\[\]]+|\[\d+\]", key)
    node: Any = root
    for i, token in enumerate(tokens):
        last = i == len(tokens) - 1
        if token.startswith("["):
            idx = int(token[1:-1])
            if not isinstance(node, list):
                return
            while len(node) <= idx:
                node.append({})
            if last:
                node[idx] = value
            else:
                nxt = tokens[i + 1]
                if not isinstance(node[idx], (dict, list)):
                    node[idx] = [] if nxt.startswith("[") else {}
                node = node[idx]
        else:
            if last:
                node[token] = value
            else:
                nxt = tokens[i + 1]
                if token not in node or not isinstance(node[token], (dict, list)):
                    node[token] = [] if nxt.startswith("[") else {}
                node = node[token]


def run_pseudo_generate(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a locale resource.",
                        "Pass --input with a JSON or .properties file.")
    path = resolve_input_file(ctx, [".json", ".properties"], "locale resource")
    stress = ctx.params.get("mode") == "stress"
    expansion = 1.0 if not stress else 1.0
    if stress:
        expansion = 1.6
    findings: list[Finding] = []
    if path.suffix.lower() == ".json":
        data = read_json(path)
        strings = _flatten_strings(data)
        pseudo = {k: _pseudo(v, expansion) for k, v in strings.items()}
        out_name = f"pseudo-{path.stem}.json"
        # rebuild nested structure
        rebuilt: Any = {}
        for key, value in pseudo.items():
            _set_nested(rebuilt, key, value)
        artifact = ctx.write_artifact(out_name, json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n", "json", "Pseudo-localized resource")
        for key, value in strings.items():
            if _placeholders(value) != _placeholders(pseudo[key]):
                findings.append(_f("pseudo-placeholder", "critical", "Placeholder changed by pseudo-localization", key,
                                    f"Source {_placeholders(value)} vs pseudo {_placeholders(pseudo[key])}.", wcag="3.1.2",
                                    remediation="Fix the pseudo transformation to preserve placeholders."))
    else:
        text = read_text(path)
        out_lines = []
        for line in text.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out_lines.append(f"{k}={_pseudo(v, expansion)}")
            else:
                out_lines.append(line)
        artifact = ctx.write_artifact(f"pseudo-{path.stem}.properties", "\n".join(out_lines) + "\n", "properties", "Pseudo-localized properties")
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Generated a pseudo-localized resource with {len(strings) if path.suffix.lower()=='.json' else 'all'} string(s) at {int(expansion*100)}% expansion.",
                  findings, metrics={"strings": len(strings) if path.suffix.lower()=='.json' else None, "expansion": expansion},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Run the localized build smoke test with the pseudo resource.", "Review the longest strings at 320 CSS pixels."])


# --------------------------------------------------------------------------- #
# Locale formatting
# --------------------------------------------------------------------------- #
FILIPINO_MONTHS = ["Enero", "Pebrero", "Marso", "Abril", "Mayo", "Hunyo", "Hulyo", "Agosto", "Setyembre", "Oktubre", "Nobyembre", "Disyembre"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _format_date(value: str, locale: str) -> str:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            months = FILIPINO_MONTHS if locale.startswith("fil") else EN_MONTHS
            return f"{dt.day} {months[dt.month-1]} {dt.year}"
        except ValueError:
            continue
    return value


def _format_number(value: str, locale: str) -> str:
    try:
        num = float(value)
    except ValueError:
        return value
    if num.is_integer():
        return f"{int(num):,}"
    return f"{num:,.2f}"


def run_locale_format(ctx: EngineContext) -> EngineResult:
    mode = ctx.params.get("mode", "format")
    locale = ctx.params.get("locale", ctx.config.get("locale.default", "en-PH"))
    data = read_json(resolve_input_file(ctx, [".json"], "locale values")) if ctx.input_path else {}
    if not isinstance(data, dict):
        data = {"values": data}
    values = data.get("values", data)
    findings: list[Finding] = []
    formatted: dict[str, Any] = {}
    if mode in ("format", "input-validation"):
        for key, value in values.items():
            if not isinstance(value, str):
                formatted[key] = value
                continue
            if re.search(r"date|birth|start|end|deadline", key, re.I):
                out = _format_date(value, locale)
                formatted[key] = out
                if out == value and not re.search(r"\d{4}-\d{2}-\d{2}", value):
                    findings.append(_f("locale-format", "moderate", "Unrecognized date format", key,
                                        f"Value '{value}' could not be formatted.", wcag="3.1.2",
                                        remediation="Use an ISO date (YYYY-MM-DD) or a supported format."))
            elif re.search(r"amount|price|salary|budget|cost", key, re.I):
                formatted[key] = "₱" + _format_number(value, locale) if locale.startswith(("fil", "en-PH")) else _format_number(value, locale)
            elif re.search(r"phone|mobile|contact", key, re.I):
                digits = re.sub(r"\D", "", value)
                if digits.startswith("0") and len(digits) == 10:
                    formatted[key] = f"+63 {digits[1:4]} {digits[4:7]} {digits[7:]}"
                elif digits.startswith("63") and len(digits) == 12:
                    formatted[key] = f"+{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:]}"
                else:
                    formatted[key] = value
                    findings.append(_f("locale-format", "moderate", "Philippine mobile number is not in a supported format", key,
                                        f"Value '{value}' is not a 10-digit local or 12-digit +63 number.", wcag="3.1.2",
                                        remediation="Use +63 9XX XXX XXXX or 09XX XXX XXXX."))
            else:
                formatted[key] = value
    elif mode == "address-name":
        required = data.get("required", ["name", "address", "city", "province", "postal_code"])
        for field in required:
            if not values.get(field):
                findings.append(_f("locale-address", "moderate", "Required address/name field is empty", field,
                                    "Philippine forms must support compound names, barangay, city/municipality, province, and postal code.", wcag="3.3.2",
                                    remediation="Make the field optional or provide a clear format and example."))
        if "name" in values and "," in str(values.get("name", "")):
            findings.append(_f("locale-address", "info", "Compound name may need flexible fields", "name",
                                "Support given name, middle name, family name, suffix, and preferred name.", wcag="3.3.2",
                                remediation="Provide separate fields and do not require a single full-name field."))
    artifact = ctx.write_artifact("locale-format.json", json.dumps(formatted, indent=2, ensure_ascii=False) + "\n", "json", "Formatted locale values")
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Locale formatting for {locale} in {mode} mode: {len(findings)} finding(s).", findings,
                  metrics={"locale": locale, "mode": mode, "fields": len(formatted)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Verify the formatted values with a native reviewer.", "Test the longest localized value in the UI."])


# --------------------------------------------------------------------------- #
# Terminology and translation memory
# --------------------------------------------------------------------------- #
TERM_COLUMNS = ["term", "language", "translation", "definition", "part_of_speech", "domain", "status", "forbidden_forms"]


def run_termbase_audit(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a termbase CSV.",
                        "Pass --input with a CSV file using the termbase columns.")
    resolve_input_file(ctx, [".csv"], "termbase")
    rows, headers = ctx.read_input_csv()
    mode = ctx.params.get("mode", "validate")
    findings: list[Finding] = []
    if not rows:
        raise ToolError("empty-termbase", "The termbase has no rows.", "Add at least one term.", str(ctx.input_path))
    if mode == "extract":
        text = ctx.brief or ""
        if not text:
            text_col = next((h for h in headers if h.lower() in ("text", "source", "content", "segment")), None)
            if text_col:
                text = "\n".join(r.get(text_col, "") for r in rows if r.get(text_col))
        if not text:
            text = " ".join(r.get("term", "") for r in rows if r.get("term"))
        if not text:
            raise ToolError("extract-empty", "No source text found for term extraction.",
                            "Provide a CSV with a text or source column, or use --brief.", str(ctx.input_path))
        words = re.findall(r"[A-Za-zÀ-ÿ]{4,}", text)
        stop = set("this that with from have will your their there which about into over under more most other some such only also than then them they what when where who whom why how all any can may not but for are was were been being".split())
        counts: dict[str, int] = {}
        for w in words:
            lw = w.lower()
            if lw not in stop:
                counts[lw] = counts.get(lw, 0) + 1
        candidates = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
        out_rows = [{"term": t, "language": "en", "translation": "", "definition": "", "part_of_speech": "", "domain": ctx.cap["category"], "status": "candidate", "forbidden_forms": ""} for t, _ in candidates]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=TERM_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)
        artifact = ctx.write_artifact("term-candidates.csv", buf.getvalue(), "csv", "Candidate terms for expert review")
        return result("attention", f"Extracted {len(candidates)} candidate term(s) for expert review.", [],
                      metrics={"candidates": len(candidates), "source_words": len(words)},
                      artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                      next_steps=["Review the candidate list with a linguist and subject expert.", "Approve terms and add them to the termbase."])
    missing_cols = [c for c in ("term", "language", "translation") if c not in headers]
    if missing_cols:
        raise ToolError("termbase-columns", f"Termbase is missing required columns: {missing_cols}",
                        "Include term, language, and translation columns.", str(ctx.input_path))
    seen: dict[tuple[str, str], list[int]] = {}
    for i, row in enumerate(rows, start=2):
        term = row.get("term", "").strip()
        lang = row.get("language", "").strip()
        trans = row.get("translation", "").strip()
        if not term:
            findings.append(_f("termbase", "moderate", "Row has no term", f"row {i}", "The term column is empty.", wcag="3.1.2",
                                remediation="Add the term or remove the row."))
        if not lang:
            findings.append(_f("termbase", "moderate", "Row has no language code", f"row {i}", "A BCP 47 language code is required.", wcag="3.1.2",
                                remediation="Add a language code such as en or fil-PH."))
        if not trans:
            findings.append(_f("termbase", "moderate", "Row has no translation", f"row {i}: {term}", "The translation is empty.", wcag="3.1.2",
                                remediation="Add the approved translation."))
        if term and lang:
            seen.setdefault((term.lower(), lang), []).append(i)
        if row.get("status", "").lower() in ("forbidden", "deprecated") and not row.get("definition"):
            findings.append(_f("termbase", "info", "Forbidden or deprecated term without a definition", f"row {i}: {term}",
                                "Explain why the term is forbidden and what to use instead.", wcag="3.1.2",
                                remediation="Add a definition and the approved alternative."))
    for (term, lang), lines in seen.items():
        if len(lines) > 1:
            findings.append(_f("termbase", "moderate", "Duplicate term in the same language", f"{term} ({lang})",
                                f"Rows {lines} duplicate the term.", wcag="3.1.2",
                                remediation="Keep one approved row per term and language."))
    if mode == "consistency" and rows:
        target_candidates = [r for r in rows if r.get("language", "").lower().startswith("fil")]
        if not target_candidates:
            findings.append(_f("termbase", "info", "No Filipino rows found for consistency checking", "termbase",
                                "Add Filipino terms to check consistency.", wcag="3.1.2",
                                remediation="Add the target-language terms and re-run."))
    if mode == "extract":
        text = ctx.brief or "accessibility localization"
        words = re.findall(r"[A-Za-zÀ-ÿ]{4,}", text)
        stop = set("this that with from have will your their there which about into over under more most other some such only also than then them they what when where who whom why how all any can may not but for are was were been being".split())
        counts: dict[str, int] = {}
        for w in words:
            lw = w.lower()
            if lw not in stop:
                counts[lw] = counts.get(lw, 0) + 1
        candidates = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
        if candidates:
            out_rows = [{"term": t, "language": "en", "translation": "", "definition": "", "part_of_speech": "", "domain": ctx.cap["category"], "status": "candidate", "forbidden_forms": ""} for t, _ in candidates]
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=TERM_COLUMNS)
            writer.writeheader()
            writer.writerows(out_rows)
            artifact = ctx.write_artifact("term-candidates.csv", buf.getvalue(), "csv", "Candidate terms for expert review")
            findings.append(_f("termbase", "info", f"{len(candidates)} candidate term(s) extracted", "termbase",
                                "Candidates require subject-matter review before approval.", wcag="3.1.2",
                                remediation="Review the candidate list with a linguist and subject expert."))
            status = "attention"
            return result(status, f"Extracted {len(candidates)} candidate term(s) for review.", findings,
                          metrics={"candidates": len(candidates)}, artifacts=[artifact],
                          limitations=list(ctx.cap.get("limitations", [])))
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Termbase {mode} for {len(rows)} row(s): {len(findings)} finding(s).", findings,
                  metrics={"rows": len(rows), "columns": headers, "mode": mode},
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=_top_remediations(findings))


def _similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _token_score(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def run_tm_match(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a translation memory CSV.",
                        "Pass --input with a CSV containing source and target columns.")
    resolve_input_file(ctx, [".csv"], "translation memory")
    rows, headers = ctx.read_input_csv()
    mode = ctx.params.get("mode", "match")
    findings: list[Finding] = []
    source_col = next((h for h in headers if h.lower() in ("source", "src", "source_text")), None)
    target_col = next((h for h in headers if h.lower() in ("target", "tgt", "target_text")), None)
    query_col = next((h for h in headers if h.lower() in ("query", "segment", "source_segment")), None)
    if not source_col or not target_col:
        raise ToolError("tm-columns", "Translation memory needs source and target columns.",
                        "Use headers such as source,target or src,tgt.", str(ctx.input_path))
    if mode == "manage":
        seen: dict[str, int] = {}
        for i, row in enumerate(rows, start=2):
            src = row.get(source_col, "").strip()
            if not src:
                findings.append(_f("tm", "moderate", "Empty source segment", f"row {i}", "The source is empty.", wcag="3.1.2",
                                    remediation="Remove or complete the segment."))
            if src in seen:
                findings.append(_f("tm", "minor", "Duplicate source segment", f"row {i}", f"Also on row {seen[src]}.", wcag="3.1.2",
                                    remediation="Keep one translation memory entry per source segment."))
            seen[src] = i
        status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
        return result(status, f"Translation memory validation for {len(rows)} segment(s): {len(findings)} finding(s).", findings,
                      metrics={"segments": len(rows), "columns": headers}, limitations=list(ctx.cap.get("limitations", [])))
    queries: list[str] = []
    if query_col:
        queries = [r.get(query_col, "").strip() for r in rows if r.get(query_col, "").strip()]
        tm = [(r.get(source_col, ""), r.get(target_col, "")) for r in rows]
    else:
        queries = [r.get(source_col, "").strip() for r in rows if r.get(source_col, "").strip()]
        tm = [(r.get(source_col, ""), r.get(target_col, "")) for r in rows[1:]]
    if not queries:
        raise ToolError("tm-empty", "No source segments found to match.", "Add a query column or source segments.", str(ctx.input_path))
    results = []
    for query in queries:
        scored = []
        for src, tgt in tm:
            if not src:
                continue
            score = 0.5 * _similarity(query, src) + 0.5 * _token_score(query, src)
            scored.append((score, src, tgt))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[:5]
        results.append({"query": query, "matches": [{"score": round(s, 3), "source": src, "target": tgt} for s, src, tgt in best]})
        if not best or best[0][0] < 0.5:
            findings.append(_f("tm", "info", "No strong translation memory match", query[:80],
                                "The best score is below 0.5.", wcag="3.1.2",
                                remediation="Translate the segment or add it to the translation memory."))
    artifact = ctx.write_artifact("tm-matches.json", json.dumps(results, indent=2, ensure_ascii=False) + "\n", "json", "Translation memory matches")
    return result("attention" if findings else "passed", f"Matched {len(queries)} query segment(s) against {len(tm)} translation memory entries.",
                  findings, metrics={"queries": len(queries), "tm_entries": len(tm), "matches": len(results)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])))


# --------------------------------------------------------------------------- #
# Review workbench
# --------------------------------------------------------------------------- #
def run_review_workbench(ctx: EngineContext) -> EngineResult:
    template_key = ctx.params.get("template", "human-review")
    template = REVIEW_TEMPLATES.get(template_key)
    if not template:
        raise ToolError("unknown-template", f"Unknown review template: {template_key}",
                        f"Valid templates: {', '.join(sorted(REVIEW_TEMPLATES))}.", template_key)
    rows: list[dict[str, str]] = []
    headers: list[str] = []
    if ctx.input_path:
        path = resolve_input_file(ctx, [".csv", ".json"], "review data")
        if path.suffix.lower() == ".json":
            data = read_json(path)
            if isinstance(data, list):
                rows = [dict(r) for r in data if isinstance(r, dict)]
            elif isinstance(data, dict):
                rows = [dict(r) for r in data.get("rows", data.get("reviews", [data])) if isinstance(r, dict)]
            headers = list(rows[0].keys()) if rows else []
        else:
            rows, headers = ctx.read_input_csv()
    columns = list(template["columns"])
    if rows:
        for col in headers or []:
            if col not in columns:
                columns.append(col)
        for col in template["columns"]:
            for row in rows:
                row.setdefault(col, "")
    else:
        rows = [dict(zip(template["columns"], r)) for r in template.get("sample_rows", [])]
    severity_counts = {s: 0 for s in SEVERITIES}
    for row in rows:
        sev = row.get("severity", "").strip().lower()
        if sev in severity_counts:
            severity_counts[sev] += 1
    weighted = sum(severity_counts[s] * w for s, w in (("critical", 5), ("serious", 3), ("moderate", 2), ("minor", 1)))
    total = max(1, len(rows))
    scorecard = {
        "template": template_key,
        "rows": len(rows),
        "severity_counts": severity_counts,
        "weighted_errors": weighted,
        "weighted_error_rate": round(weighted / total, 3),
        "acceptance_rate": round((total - severity_counts["critical"] - severity_counts["serious"]) / total, 3),
        "decision": "revise" if severity_counts["critical"] or severity_counts["serious"] else "accept",
    }
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    artifact = ctx.write_artifact("review-workbench.csv", buf.getvalue(), "csv", template["title"])
    score_artifact = ctx.write_artifact("review-scorecard.json", json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n", "json", "Review scorecard")
    findings: list[Finding] = []
    for sev in ("critical", "serious"):
        if severity_counts[sev]:
            findings.append(_f("review", sev, f"{severity_counts[sev]} {sev} review issue(s)", template["title"],
                                "Resolve critical and serious issues before acceptance.", wcag="3.1.2",
                                remediation="Adjudicate the issue with the reviewer and update the content."))
    status = "failed" if severity_counts["critical"] or severity_counts["serious"] else ("attention" if severity_counts["moderate"] or severity_counts["minor"] else "passed")
    return result(status, f"{template['title']}: {len(rows)} row(s), {weighted} weighted error(s).", findings,
                  metrics=scorecard, artifacts=[artifact, score_artifact],
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Have the qualified reviewer complete the workbench.", "Record the adjudication and update the termbase."])


# --------------------------------------------------------------------------- #
# Readability
# --------------------------------------------------------------------------- #
def _syllables(word: str) -> int:
    word = word.lower()
    word = re.sub(r"[^a-zà-ÿ]", "", word)
    if not word:
        return 0
    groups = re.findall(r"[aeiouà-ÿ]+", word)
    count = len(groups)
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def core_file_kind(path: str) -> str:
    return file_kind(Path(path))


def run_readability(ctx: EngineContext) -> EngineResult:
    mode = ctx.params.get("mode", "analyze")
    if ctx.input_path:
        path = resolve_input_file(ctx, [".txt", ".md", ".html", ".htm"], "text")
        text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
    else:
        text = ctx.brief
    if not text.strip():
        raise ToolError("input-required", "This capability requires text or a brief.",
                        "Pass --input with a text file or use --brief.")
    if ctx.input_path and core_file_kind(ctx.input_path) in ("html", "htm", "xhtml"):
        try:
            doc = parse_html(text)
            text = doc.root.text_content()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    words = re.findall(r"[A-Za-zÀ-ÿ']+", text)
    syllables = sum(_syllables(w) for w in words)
    sentence_count = max(1, len(sentences))
    word_count = max(1, len(words))
    flesch = 206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllables / word_count)
    grade = 0.39 * (word_count / sentence_count) + 11.8 * (syllables / word_count) - 15.59
    long_words = [w for w in words if len(w) > 12]
    long_sentences = [s for s in sentences if len(s.split()) > 25]
    passive = len(re.findall(r"\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b", text, re.I))
    jargon = ["utilize", "leverage", "facilitate", "commence", "terminate", "prior to", "in order to", "aforementioned", "hereinafter"]
    found_jargon = [w for w in jargon if re.search(r"\b" + w + r"\b", text, re.I)]
    metrics = {
        "mode": mode, "sentences": len(sentences), "words": word_count,
        "avg_sentence_length": round(word_count / sentence_count, 1),
        "flesch_reading_ease": round(flesch, 1), "flesch_kincaid_grade": round(grade, 1),
        "long_words": len(long_words), "long_sentences": len(long_sentences),
        "passive_voice_candidates": passive, "jargon": found_jargon,
    }
    findings: list[Finding] = []
    if flesch < 50:
        findings.append(_f("readability", "moderate", "Reading ease is low", "content",
                            f"Flesch Reading Ease is {flesch:.1f}; aim for 50 or higher for general audiences.", wcag="3.1.5",
                            remediation="Shorten sentences and use familiar words."))
    if long_sentences:
        findings.append(_f("readability", "minor", f"{len(long_sentences)} long sentence(s)", "content",
                            "Split sentences over 25 words.", wcag="3.1.5",
                            remediation="Split long sentences into direct statements."))
    if found_jargon:
        findings.append(_f("readability", "minor", "Jargon or formal wording detected", "content",
                            f"Words: {', '.join(found_jargon)}.", wcag="3.1.5",
                            remediation="Replace with familiar words or define the term."))
    if passive > 2:
        findings.append(_f("readability", "info", "Passive voice is frequent", "content",
                            f"{passive} passive constructions detected.", wcag="3.1.5",
                            remediation="Use active voice where it improves clarity."))
    artifacts = []
    if mode in ("rewrite", "easy-read", "plain-filipino"):
        replacements = {
            "utilize": "use", "leverage": "use", "facilitate": "help", "commence": "start",
            "terminate": "end", "prior to": "before", "in order to": "to", "aforementioned": "this",
            "hereinafter": "below", "approximately": "about", "sufficient": "enough",
        }
        rewritten = text
        for a, b in replacements.items():
            rewritten = re.sub(r"\b" + a + r"\b", b, rewritten, flags=re.I)
        if mode == "easy-read":
            chunks = []
            for sentence in sentences:
                parts = re.split(r"(,|;|\band\b|\bbut\b)", sentence)
                current = ""
                for part in parts:
                    if part in (",", ";", "and", "but"):
                        if current.strip():
                            chunks.append(current.strip())
                        current = ""
                    else:
                        current += part + " "
                if current.strip():
                    chunks.append(current.strip())
            rewritten = "\n".join(f"- {c}" for c in chunks)
        artifact = ctx.write_artifact("plain-language-draft.md", rewritten + "\n", "markdown", "Rule-based plain-language draft for human review")
        artifacts.append(artifact)
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Readability profile: Flesch {flesch:.1f}, grade {grade:.1f}, {len(sentences)} sentence(s).", findings,
                  metrics=metrics, artifacts=artifacts, limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Have a human editor review the draft.", "Test comprehension with the target audience."])


# --------------------------------------------------------------------------- #
# Font coverage
# --------------------------------------------------------------------------- #
SCRIPT_RANGES = [
    ("Latin", (0x0041, 0x024F)), ("Greek", (0x0370, 0x03FF)), ("Cyrillic", (0x0400, 0x04FF)),
    ("Hebrew", (0x0590, 0x05FF)), ("Arabic", (0x0600, 0x06FF)), ("Devanagari", (0x0900, 0x097F)),
    ("Bengali", (0x0980, 0x09FF)), ("Gurmukhi", (0x0A00, 0x0A7F)), ("Gujarati", (0x0A80, 0x0AFF)),
    ("Tamil", (0x0B80, 0x0BFF)), ("Telugu", (0x0C00, 0x0C7F)), ("Kannada", (0x0C80, 0x0CFF)),
    ("Malayalam", (0x0D00, 0x0D7F)), ("Thai", (0x0E00, 0x0E7F)), ("Lao", (0x0E80, 0x0EFF)),
    ("Khmer", (0x1780, 0x17FF)), ("Myanmar", (0x1000, 0x109F)), ("Georgian", (0x10A0, 0x10FF)),
    ("Ethiopic", (0x1200, 0x137F)), ("CJK", (0x4E00, 0x9FFF)), ("Hangul", (0xAC00, 0xD7AF)),
    ("Kana", (0x3040, 0x30FF)), ("Bopomofo", (0x3100, 0x312F)),
]


def _scripts(text: str) -> set[str]:
    found = set()
    for ch in text:
        cp = ord(ch)
        for name, (lo, hi) in SCRIPT_RANGES:
            if lo <= cp <= hi:
                found.add(name)
                break
    return found


def run_font_coverage(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires HTML, CSS, or text input.",
                        "Pass --input with a file or directory.")
    files = ctx.source_files()
    if not files:
        raise ToolError("input-unsupported", "No supported source files found.", "Pass HTML, CSS, or text files.", str(ctx.input_path))
    findings: list[Finding] = []
    scripts: set[str] = set()
    font_stacks: list[str] = []
    for path in files:
        text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
        scripts |= _scripts(text)
        for m in re.finditer(r"font-family\s*:\s*([^;}]+)", text, re.I):
            stack = m.group(1).strip()
            font_stacks.append(stack)
            if not re.search(r"serif|sans-serif|monospace|system-ui|ui-sans-serif", stack, re.I):
                findings.append(_f("font-coverage", "moderate", "Font stack has no generic fallback", path.name,
                                    f"font-family: {stack}", wcag="1.4.4",
                                    remediation="Add a generic fallback such as sans-serif or serif."))
    if not font_stacks:
        findings.append(_f("font-coverage", "info", "No font-family declarations found", "input",
                            "Font coverage cannot be assessed without a font stack.", wcag="1.4.4",
                            remediation="Declare a font stack with fallbacks for each target script."))
    if scripts:
        findings.append(_f("font-coverage", "info", f"Scripts detected: {', '.join(sorted(scripts))}", "input",
                            "Verify that the selected fonts cover these scripts and that shaping is supported.", wcag="1.4.4",
                            remediation="Test with a native speaker and a browser on the target platform."))
    mode = ctx.params.get("mode", "general")
    if mode in ("cjk", "indic", "sea", "shaping"):
        findings.append(_f("font-coverage", "info", f"{mode} shaping review required", "input",
                            "Automated coverage cannot verify joining, ligatures, mark placement, or line breaking.", wcag="1.4.4",
                            remediation="Test shaping with a native reviewer and a rendering engine."))
    if mode == "performance":
        findings.append(_f("font-coverage", "info", "Webfont performance review", "input",
                            "Check subsetting, caching, layout stability, privacy, and offline availability.", wcag="1.4.4",
                            remediation="Use unicode-range subsetting, font-display, and a local fallback."))
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Font coverage review for {len(files)} file(s); scripts: {', '.join(sorted(scripts)) or 'none detected'}.", findings,
                  metrics={"files": len(files), "scripts": sorted(scripts), "font_stacks": len(font_stacks)},
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=_top_remediations(findings))


# --------------------------------------------------------------------------- #
# Captions
# --------------------------------------------------------------------------- #
def _parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(value)


def _parse_captures(text: str) -> list[dict[str, Any]]:
    cues = []
    if text.lstrip().startswith("WEBVTT"):
        blocks = re.split(r"\n\s*\n", text)
        for block in blocks:
            lines = [l for l in block.splitlines() if l.strip()]
            if not lines or lines[0].startswith("WEBVTT"):
                continue
            idx = 0
            if lines and re.match(r"^\d+$", lines[0].strip()):
                idx = 1
            if idx >= len(lines):
                continue
            m = re.match(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})", lines[idx])
            if not m:
                continue
            cues.append({"start": _parse_timestamp(m.group(1)), "end": _parse_timestamp(m.group(2)),
                         "text": " ".join(lines[idx+1:]).strip(), "line": idx})
    else:
        blocks = re.split(r"\n\s*\n", text)
        for block in blocks:
            lines = [l for l in block.splitlines() if l.strip()]
            if not lines:
                continue
            idx = 0
            if re.match(r"^\d+$", lines[0].strip()):
                idx = 1
            if idx >= len(lines):
                continue
            m = re.match(r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})", lines[idx])
            if not m:
                continue
            cues.append({"start": _parse_timestamp(m.group(1)), "end": _parse_timestamp(m.group(2)),
                         "text": " ".join(lines[idx+1:]).strip(), "line": idx})
    return cues


def run_caption_qa(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a caption file.",
                        "Pass --input with a .vtt or .srt file.")
    path = resolve_input_file(ctx, [".vtt", ".srt"], "caption")
    text = read_text(path)
    mode = ctx.params.get("mode", "audit")
    findings: list[Finding] = []
    if mode == "authoring":
        if path.suffix.lower() in (".vtt", ".srt"):
            cues = _parse_captures(text)
            if not cues:
                raise ToolError("caption-parse", "No cues found in the caption file.", "Check the WebVTT/SRT syntax.", str(path))
            findings = _validate_cues(cues)
            status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
            return result(status, f"Caption authoring check for {len(cues)} cue(s).", findings,
                          metrics={"cues": len(cues)}, limitations=list(ctx.cap.get("limitations", [])))
        transcript = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        cues = []
        t = 0.0
        for sentence in transcript:
            words = len(sentence.split())
            duration = max(1.5, min(7.0, words / 2.5))
            cues.append({"start": t, "end": t + duration, "text": sentence})
            t += duration + 0.2
        out = ["WEBVTT", ""]
        for i, cue in enumerate(cues, 1):
            def ts(v):
                h = int(v // 3600); m = int((v % 3600) // 60); s = v % 60
                return f"{h:02d}:{m:02d}:{s:06.3f}"
            out.append(f"{ts(cue['start'])} --> {ts(cue['end'])}")
            out.append(cue["text"])
            out.append("")
        artifact = ctx.write_artifact("generated-captions.vtt", "\n".join(out), "captions", "Draft captions generated from the transcript")
        return result("attention", f"Generated {len(cues)} draft cue(s) from the transcript. Timing must be aligned to audio.",
                      [], metrics={"cues": len(cues)}, artifacts=[artifact],
                      limitations=["Timings are estimated from text length and must be aligned to the audio."],
                      next_steps=["Align the cues to the audio in a caption editor.", "Review accuracy and reading speed."])
    cues = _parse_captures(text)
    if not cues:
        raise ToolError("caption-parse", "No cues found in the caption file.", "Check the WebVTT/SRT syntax.", str(path))
    findings = _validate_cues(cues)
    if mode in ("review", "filipino"):
        for cue in cues:
            if re.search(r"\b(po|ho|nga|ba|na|ng|sa|ang|at|p||)\b", cue["text"], re.I) and not re.search(r"[.,!?]", cue["text"]):
                findings.append(_f("caption-filipino", "minor", "Filipino caption may need punctuation", f"cue {cue['line']}",
                                    "Check punctuation and code-switching readability.", wcag="1.2.2",
                                    remediation="Review with a Filipino captioner."))
                break
    findings = findings[:ctx.limits.get("max_findings", 500)]
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Caption QA for {len(cues)} cue(s): {len(findings)} finding(s).", findings,
                  metrics={"cues": len(cues), "duration": round(max(c['end'] for c in cues), 2) if cues else 0},
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=_top_remediations(findings))


def _validate_cues(cues: list[dict[str, Any]]) -> list[Finding]:
    out: list[Finding] = []
    for i, cue in enumerate(cues):
        if cue["end"] <= cue["start"]:
            out.append(_f("caption-timing", "serious", "Cue ends before it starts", f"cue {i+1}",
                          f"{cue['start']:.2f} -> {cue['end']:.2f}", wcag="1.2.2",
                          remediation="Fix the cue timing."))
        duration = cue["end"] - cue["start"]
        chars = len(cue["text"])
        if duration > 0:
            cps = chars / duration
            if cps > 20:
                out.append(_f("caption-reading-speed", "moderate", "Caption reading speed is high", f"cue {i+1}",
                              f"{cps:.1f} characters per second.", wcag="1.2.2",
                              remediation="Shorten the caption or extend the duration."))
        for line in cue["text"].split("\n"):
            if len(line) > 42:
                out.append(_f("caption-line-length", "minor", "Caption line is too long", f"cue {i+1}",
                              f"{len(line)} characters.", wcag="1.2.2",
                              remediation="Break the line at a natural phrase boundary."))
        if not cue["text"].strip():
            out.append(_f("caption-empty", "moderate", "Empty caption cue", f"cue {i+1}",
                          "The cue has no text.", wcag="1.2.2",
                          remediation="Remove the cue or add the caption text."))
        if i > 0 and cue["start"] < cues[i-1]["end"]:
            out.append(_f("caption-overlap", "moderate", "Caption cues overlap", f"cue {i+1}",
                          f"Starts at {cue['start']:.2f} before the previous cue ends at {cues[i-1]['end']:.2f}.", wcag="1.2.2",
                          remediation="Adjust the timing so cues do not overlap."))
    return out


# --------------------------------------------------------------------------- #
# Audio description and loudness
# --------------------------------------------------------------------------- #
def run_audio_description(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a description script or media timing data.",
                        "Pass --input with a CSV or JSON file.")
    path = resolve_input_file(ctx, [".json", ".csv"], "description script")
    findings: list[Finding] = []
    if path.suffix.lower() == ".json":
        data = read_json(path)
        duration = float(data.get("duration", 0))
        gaps = data.get("gaps", [])
        descriptions = data.get("descriptions", [])
        plan = []
        for desc in descriptions:
            start = float(desc.get("start", 0)); end = float(desc.get("end", 0))
            words = len(desc.get("text", "").split())
            wps = words / max(0.1, end - start)
            if end > duration:
                findings.append(_f("audio-description", "serious", "Description extends beyond the media", desc.get("id", "?"),
                                    f"End {end} exceeds duration {duration}.", wcag="1.2.5",
                                    remediation="Shorten the description or extend the media."))
            if wps > 3.5:
                findings.append(_f("audio-description", "moderate", "Description reading speed is high", desc.get("id", "?"),
                                    f"{wps:.1f} words per second.", wcag="1.2.5",
                                    remediation="Shorten the description or use extended description."))
            for gap in gaps:
                if start < float(gap.get("end", 0)) and end > float(gap.get("start", 0)):
                    findings.append(_f("audio-description", "serious", "Description overlaps dialogue", desc.get("id", "?"),
                                        f"Overlaps gap {gap.get('start')}-{gap.get('end')}.", wcag="1.2.5",
                                        remediation="Move the description into a gap or use extended description."))
                    break
            plan.append({"id": desc.get("id"), "start": start, "end": end, "text": desc.get("text", ""), "words": words, "wps": round(wps, 2)})
        artifact = ctx.write_artifact("audio-description-plan.json", json.dumps(plan, indent=2, ensure_ascii=False) + "\n", "json", "Audio description plan")
        status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
        return result(status, f"Audio description plan for {len(plan)} description(s).", findings,
                      metrics={"descriptions": len(plan), "duration": duration}, artifacts=[artifact],
                      limitations=list(ctx.cap.get("limitations", [])))
    rows, headers = ctx.read_input_csv()
    if not rows:
        raise ToolError("empty-input", "The description script has no rows.", "Add rows with start,end,description.", str(path))
    for i, row in enumerate(rows, start=2):
        try:
            start = float(row.get("start", 0)); end = float(row.get("end", 0))
        except ValueError:
            findings.append(_f("audio-description", "moderate", "Invalid timing", f"row {i}", "Start/end must be numbers.", wcag="1.2.5",
                                remediation="Use numeric seconds."))
            continue
        text = row.get("description", row.get("text", ""))
        words = len(text.split())
        if end <= start:
            findings.append(_f("audio-description", "serious", "Description has no duration", f"row {i}", "End must be greater than start.", wcag="1.2.5",
                                remediation="Fix the timing."))
        elif words / (end - start) > 3.5:
            findings.append(_f("audio-description", "moderate", "Description reading speed is high", f"row {i}",
                                f"{words/(end-start):.1f} words per second.", wcag="1.2.5",
                                remediation="Shorten the description."))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Audio description timing check for {len(rows)} row(s).", findings,
                  metrics={"rows": len(rows)}, limitations=list(ctx.cap.get("limitations", [])))


def run_loudness(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a WAV audio file.",
                        "Pass --input with a .wav file.")
    path = resolve_input_file(ctx, [".wav"], "WAV audio")
    if path.suffix.lower() != ".wav":
        raise ToolError("format-unsupported", "Only uncompressed WAV audio is supported.",
                        "Convert the audio to WAV or use ffmpeg for other formats.", str(path))
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(min(frames, rate * 120))
    except (wave.Error, EOFError) as exc:
        raise ToolError("invalid-audio", f"Could not read WAV file: {exc}", "Check that the file is a valid PCM WAV.", str(path))
    if not raw:
        raise ToolError("empty-audio", "The WAV file has no audio frames.", "Use a non-empty audio file.", str(path))
    fmt = {1: "b", 2: "h", 4: "i"}.get(width)
    if not fmt:
        raise ToolError("unsupported-audio", f"Unsupported sample width: {width}", "Use 8-, 16-, or 32-bit PCM WAV.", str(path))
    count = len(raw) // width
    samples = struct.unpack("<" + fmt * count, raw[:count * width])
    if channels > 1:
        samples = samples[::channels]
    peak = max(abs(s) for s in samples) / (2 ** (width * 8 - 1))
    rms = math.sqrt(sum((s / (2 ** (width * 8 - 1))) ** 2 for s in samples) / len(samples))
    peak_db = 20 * math.log10(peak) if peak else -96.0
    rms_db = 20 * math.log10(rms) if rms else -96.0
    duration = frames / rate if rate else 0
    findings: list[Finding] = []
    if peak_db > -1:
        findings.append(_f("loudness", "moderate", "Audio peaks near 0 dBFS", str(path.name),
                            f"Peak {peak_db:.1f} dBFS; risk of clipping.", wcag="1.2.1",
                            remediation="Reduce gain or apply limiting."))
    if rms_db < -30:
        findings.append(_f("loudness", "info", "Audio is quiet", str(path.name),
                            f"RMS {rms_db:.1f} dBFS.", wcag="1.2.1",
                            remediation="Check the mix level and provide a volume control."))
    if duration > 120:
        findings.append(_f("loudness", "info", "Audio analysis limited to the first 120 seconds", str(path.name),
                            "The measurement covers a 120-second window.", wcag="1.2.1",
                            remediation="Analyze the full file with an external tool if required."))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"WAV analysis: peak {peak_db:.1f} dBFS, RMS {rms_db:.1f} dBFS, duration {duration:.1f}s.",
                  findings, metrics={"channels": channels, "rate": rate, "duration": round(duration, 2),
                                     "peak_dbfs": round(peak_db, 2), "rms_dbfs": round(rms_db, 2)},
                  limitations=["This is an RMS/peak measurement, not a perceptual LUFS measurement. Use ffmpeg loudnorm or an equivalent tool for compliance."])


# --------------------------------------------------------------------------- #
# Data alternatives
# --------------------------------------------------------------------------- #
def run_data_alternative(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires chart, map, or diagram data.",
                        "Pass --input with a CSV or JSON file.")
    path = resolve_input_file(ctx, [".csv", ".json"], "chart, map, or diagram data")
    kind = ctx.params.get("kind", "chart")
    findings: list[Finding] = []
    if path.suffix.lower() == ".json":
        data = read_json(path)
    else:
        rows, headers = ctx.read_input_csv()
        data = rows
    if kind in ("chart", "chart-description"):
        if isinstance(data, list) and data and isinstance(data[0], dict):
            numeric = []
            for row in data:
                for k, v in row.items():
                    try:
                        numeric.append((k, float(v)))
                    except (TypeError, ValueError):
                        continue
            if numeric:
                values = [v for _, v in numeric]
                stats = {"count": len(values), "min": min(values), "max": max(values),
                         "mean": round(sum(values) / len(values), 3), "median": sorted(values)[len(values)//2]}
                trend = "increases" if values[-1] > values[0] else "decreases" if values[-1] < values[0] else "is stable"
                description = (f"This chart shows {stats['count']} data points. The minimum is {stats['min']}, "
                               f"the maximum is {stats['max']}, the mean is {stats['mean']}, and the median is {stats['median']}. "
                               f"The overall trend {trend}.")
                table = "\n".join(f"| {k} | {v} |" for k, v in numeric[:50])
                artifact = ctx.write_artifact("chart-description.md", f"# Chart description\n\n{description}\n\n## Data table\n\n| Series | Value |\n| --- | --- |\n{table}\n", "markdown", "Chart description and data table")
                return result("passed", description, [], metrics=stats, artifacts=[artifact],
                              limitations=["Verify the description against the actual chart and its context."])
        raise ToolError("chart-data", "No numeric chart data found.", "Provide a CSV with numeric columns or a JSON array of objects.", str(path))
    if kind == "map":
        if isinstance(data, list):
            lines = ["# Map alternative: searchable list", ""]
            for i, row in enumerate(data, 1):
                if isinstance(row, dict):
                    name = row.get("name", row.get("place", f"Location {i}"))
                    address = row.get("address", "")
                    coords = row.get("coordinates", row.get("coords", ""))
                    lines.append(f"{i}. **{name}** — {address} {coords}".strip())
            artifact = ctx.write_artifact("map-alternative.md", "\n".join(lines) + "\n", "markdown", "Searchable map alternative")
            return result("passed", f"Generated a searchable list alternative for {len(data)} location(s).", [], metrics={"locations": len(data)}, artifacts=[artifact],
                          limitations=["Verify addresses and directions with a local source."])
        raise ToolError("map-data", "Map data must be a JSON array of locations.", "Provide a JSON array with name, address, and coordinates.", str(path))
    if kind == "diagram":
        if isinstance(data, dict):
            regions = data.get("regions", [])
            relationships = data.get("relationships", [])
            lines = ["# Diagram navigator", "", "## Regions", ""]
            for i, region in enumerate(regions, 1):
                lines.append(f"{i}. **{region.get('name','Region '+str(i))}**: {region.get('description','')}")
            lines += ["", "## Relationships", ""]
            for rel in relationships:
                lines.append(f"- {rel.get('from','?')} → {rel.get('to','?')}: {rel.get('label','')}")
            artifact = ctx.write_artifact("diagram-description.md", "\n".join(lines) + "\n", "markdown", "Ordered diagram description")
            return result("passed", f"Generated a diagram navigator for {len(regions)} region(s).", [], metrics={"regions": len(regions), "relationships": len(relationships)}, artifacts=[artifact],
                          limitations=["Verify the region names and relationships with the diagram owner."])
        raise ToolError("diagram-data", "Diagram data must be a JSON object with regions and relationships.", "Provide regions and relationships arrays.", str(path))
    # long description
    lines = ["# Long description", "", "## Overview", "", "Describe the overall purpose and context of the image.", "", "## Structure", "", "- Region 1: ...", "- Region 2: ...", "", "## Key details", "", "- ...", "", "## Takeaway", "", "..."]
    artifact = ctx.write_artifact("long-description.md", "\n".join(lines) + "\n", "markdown", "Long description template")
    return result("attention", "Generated a long-description template for human completion.", [], artifacts=[artifact],
                  limitations=["The template requires a human author to complete the description."])


# --------------------------------------------------------------------------- #
# Governance and plan artifacts
# --------------------------------------------------------------------------- #
def _fill(text: str, ctx: EngineContext) -> str:
    org = ctx.config.data.get("organization", {})
    values = {
        "org": org.get("name", "The organization"),
        "scope": org.get("scope", "digital products and content"),
        "owner": org.get("owner", "Accessibility lead"),
        "feedback_channel": org.get("feedback_channel", "accessibility@example.org"),
        "response_days": str(org.get("response_days", 5)),
        "escalation_contact": org.get("escalation_contact", "Accessibility lead"),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "wcag": ctx.config.get("standards.wcag", "2.2"),
        "levels": ", ".join(ctx.config.get("standards.levels", ["A", "AA"])),
        "status": "partially conformant",
        "brief": ctx.brief or ctx.cap["description"],
    }
    def repl(m):
        key = m.group(1)
        return values.get(key, m.group(0))
    return re.sub(r"\{\{(\w+)\}\}", repl, text)


def run_governance_artifact(ctx: EngineContext) -> EngineResult:
    template_key = ctx.params.get("template", "policy")
    template = DOCUMENTS.get(template_key)
    if not template:
        raise ToolError("unknown-template", f"Unknown governance template: {template_key}",
                        f"Valid templates: {', '.join(sorted(DOCUMENTS))}.", template_key)
    md = Markdown()
    md.h(1, _fill(template["title"], ctx))
    md.p(f"**Status:** Draft for review · **Owner:** {_fill('{{owner}}', ctx)} · **Date:** {_fill('{{date}}', ctx)}")
    md.p(_fill(template["purpose"], ctx))
    if ctx.brief:
        md.h(2, "Context from the request")
        md.p(ctx.brief)
    for section in template["sections"]:
        md.h(2, _fill(section["heading"], ctx))
        md.p(_fill(section["body"], ctx))
    md.h(2, "Requirements checklist")
    md.bullets([_fill(r, ctx) for r in template["requirements"]])
    md.h(2, "Review and approval")
    md.p(_fill(template["review"], ctx))
    md.p("**Approval:** ______________________  **Role:** ______________________  **Date:** ____________")
    artifact = ctx.write_artifact(f"{slugify(template['title'])}.md", md.render(), "markdown", template["title"])
    protocol = {
        "objective": _fill(template["purpose"], ctx),
        "roles": [ctx.config.get("organization.owner", "Accessibility lead"), "Accountable owner", "Legal/procurement reviewer"],
        "steps": [f"Draft the {template['title'].lower()}.", "Review with the accountable owner and legal/procurement where required.",
                  "Resolve comments and record decisions.", "Publish the approved artifact and set the review date."],
        "pass_criteria": template["requirements"],
        "fail_criteria": ["The artifact is published without owner approval.", "A required legal or procurement review is skipped."],
        "evidence": ["Approved artifact", "Review comments", "Owner sign-off"],
        "human_review": "A qualified human owner must approve this artifact before publication.",
    }
    return result("attention", f"Generated the {template['title']} draft for human review.", [],
                  metrics={"template": template_key, "sections": len(template["sections"])},
                  protocol=protocol, artifacts=[artifact],
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Route the draft to the accountable owner.", "Record the approval and review date."])


def run_plan_artifact(ctx: EngineContext) -> EngineResult:
    template_key = ctx.params.get("template", "research-protocol")
    protocol = PROTOCOLS.get(template_key)
    if not protocol:
        raise ToolError("unknown-template", f"Unknown protocol template: {template_key}",
                        f"Valid templates: {', '.join(sorted(PROTOCOLS))}.", template_key)
    md = Markdown()
    md.h(1, _fill(protocol["title"], ctx))
    md.p(f"**Status:** Draft protocol · **Duration:** {protocol['duration']} · **Owner:** {_fill('{{owner}}', ctx)}")
    md.p(_fill(protocol["objective"], ctx))
    if ctx.brief:
        md.h(2, "Context from the request")
        md.p(ctx.brief)
    md.h(2, "Roles")
    md.bullets(protocol["roles"])
    if template_key == "at-script":
        at_key = ctx.params.get("at", "nvda")
        at = AT.get(at_key, AT["nvda"])
        md.h(2, "Environment")
        md.table(["Field", "Value"], [[k, str(v)] for k, v in at.items()])
    md.h(2, "Procedure")
    for i, step in enumerate(protocol["steps"], 1):
        md.p(f"{i}. {step}")
    md.h(2, "Evidence to retain")
    md.bullets(protocol["evidence"])
    md.h(2, "Pass criteria")
    md.bullets(protocol["pass_criteria"])
    md.h(2, "Fail criteria")
    md.bullets(protocol["fail_criteria"])
    if protocol.get("privacy"):
        md.h(2, "Privacy and consent")
        md.p(protocol["privacy"])
    md.h(2, "Sign-off")
    md.p("**Facilitator:** ______________________  **Reviewer:** ______________________  **Date:** ____________")
    artifact = ctx.write_artifact(f"{slugify(protocol['title'])}.md", md.render(), "markdown", protocol["title"])
    return result("attention", f"Generated the {protocol['title']} protocol for human execution.", [],
                  metrics={"template": template_key, "steps": len(protocol["steps"])},
                  protocol={**protocol, "human_review": "A qualified human specialist must execute this protocol and record the evidence."},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Schedule the session and arrange accommodations.", "Execute the protocol and retain the evidence.", "Review the findings with the accountable owner."])


# --------------------------------------------------------------------------- #
# Risk register
# --------------------------------------------------------------------------- #
SEVERITY_WEIGHT = {"critical": 5, "serious": 4, "moderate": 3, "minor": 2, "info": 1}


def _load_findings(ctx: EngineContext) -> list[dict[str, Any]]:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a findings JSON file.",
                        "Run an analysis capability first, then pass its JSON output with --input.")
    data = read_json(resolve_input_file(ctx, [".json"], "findings"))
    if isinstance(data, dict) and "findings" in data:
        return [f for f in data["findings"] if isinstance(f, dict)]
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict)]
    raise ToolError("findings-format", "Expected a findings array or an envelope with a findings array.",
                    "Use the JSON output of an analysis capability.", str(ctx.input_path))


def run_risk_register(ctx: EngineContext) -> EngineResult:
    findings = _load_findings(ctx)
    if not findings:
        raise ToolError("no-findings", "The findings file contains no findings.", "Run an analysis that produces findings.", str(ctx.input_path))
    rows = []
    for i, f in enumerate(findings, 1):
        sev = f.get("severity", "info")
        weight = SEVERITY_WEIGHT.get(sev, 1)
        reach = 3
        journey = 3
        recurrence = 2
        score = weight * reach * journey * recurrence
        due = {"critical": 7, "serious": 30, "moderate": 90, "minor": 180, "info": 365}.get(sev, 365)
        due_date = (datetime.now(timezone.utc) + timedelta(days=due)).strftime("%Y-%m-%d")
        rows.append({
            "id": f"R-{i:03d}", "title": f.get("title", "Finding"), "severity": sev,
            "check": f.get("check", ""), "location": f.get("location", ""), "wcag": f.get("wcag", ""),
            "user_impact": "Blocks or degrades a user journey" if sev in ("critical", "serious") else "Friction or confusion",
            "legal_exposure": "Review" if sev in ("critical", "serious") else "Low",
            "reach": reach, "journey_importance": journey, "recurrence": recurrence,
            "risk_score": score, "owner": "", "mitigation": f.get("remediation", ""),
            "due_date": due_date, "status": "open",
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader(); writer.writerows(rows)
    artifact = ctx.write_artifact("risk-register.csv", buf.getvalue(), "csv", "Accessibility risk register")
    critical = sum(1 for r in rows if r["severity"] == "critical")
    serious = sum(1 for r in rows if r["severity"] == "serious")
    status = "failed" if critical else ("attention" if serious else "passed")
    return result(status, f"Built a risk register with {len(rows)} entries ({critical} critical, {serious} serious).", [],
                  metrics={"entries": len(rows), "critical": critical, "serious": serious, "max_score": max(r["risk_score"] for r in rows)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Assign an owner to each critical and serious risk.", "Review legal exposure with the legal owner.", "Track mitigation and due dates."])


# --------------------------------------------------------------------------- #
# Release gate
# --------------------------------------------------------------------------- #
def run_release_gate(ctx: EngineContext) -> EngineResult:
    findings = _load_findings(ctx)
    gate = ctx.config.data.get("release_gate", {})
    block_on = gate.get("block_on", ["critical", "serious"])
    blocking = [f for f in findings if f.get("severity") in block_on]
    exceptions = []
    if ctx.input_path:
        data = read_json(ctx.input_path)
        if isinstance(data, dict):
            exceptions = data.get("exceptions", [])
    decision = "blocked" if blocking and not exceptions else "allowed"
    if blocking and exceptions:
        decision = "conditional"
    reasons = [f"{f.get('severity')}: {f.get('title')} ({f.get('location')})" for f in blocking]
    envelope = {
        "decision": decision,
        "policy": gate,
        "findings_considered": len(findings),
        "blocking_findings": blocking,
        "exceptions": exceptions,
        "reasons": reasons,
        "required_actions": ["Resolve each blocking finding and re-run the gate.", "Or record an approved, time-bound exception with equivalent access."] if blocking else ["Publish the release evidence and schedule the next audit."],
        "generated_at": utcnow(),
    }
    artifact = ctx.write_artifact("release-gate-decision.json", json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", "json", "Release gate decision")
    status = "failed" if decision == "blocked" else ("attention" if decision == "conditional" else "passed")
    return result(status, f"Release gate decision: {decision}. {len(blocking)} blocking finding(s).", [],
                  metrics={"decision": decision, "blocking": len(blocking), "exceptions": len(exceptions)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=envelope["required_actions"])


# --------------------------------------------------------------------------- #
# Maturity assessment
# --------------------------------------------------------------------------- #
MATURITY_DIMENSIONS = [
    ("Governance", "Policy, ownership, funding, and executive sponsorship"),
    ("Design", "Accessible design system, tokens, and design review"),
    ("Engineering", "Semantic code, component quality, and CI checks"),
    ("Content", "Plain language, alt text, captions, and document structure"),
    ("Testing", "Automated, manual, and assistive-technology testing"),
    ("Procurement", "Vendor requirements, ACR review, and acceptance tests"),
    ("Support", "Accessible support channels and incident response"),
    ("Culture", "Training, communities, and disabled-user engagement"),
]


def run_maturity_assess(ctx: EngineContext) -> EngineResult:
    evidence = read_json(resolve_input_file(ctx, [".json"], "maturity evidence")) if ctx.input_path else {}
    if not isinstance(evidence, dict):
        evidence = {}
    scores = evidence.get("scores", {})
    result_rows = []
    for name, description in MATURITY_DIMENSIONS:
        raw = scores.get(name, evidence.get(name, 2))
        try:
            score = max(1, min(5, int(raw)))
        except (TypeError, ValueError):
            score = 2
        level = ["Initial", "Developing", "Defined", "Managed", "Optimized"][score-1]
        result_rows.append({"dimension": name, "score": score, "level": level, "description": description,
                            "evidence": evidence.get(f"{name}_evidence", "Evidence not supplied")})
    overall = sum(r["score"] for r in result_rows) / len(result_rows)
    roadmap = []
    for row in sorted(result_rows, key=lambda r: r["score"]):
        if row["score"] < 3:
            roadmap.append(f"Quick win: establish {row['dimension'].lower()} ownership and a 90-day plan.")
        elif row["score"] < 4:
            roadmap.append(f"Improve {row['dimension'].lower()} from Defined to Managed with measurable targets.")
        else:
            roadmap.append(f"Sustain {row['dimension'].lower()} and share the practice across teams.")
    envelope = {"overall_score": round(overall, 2), "level": ["Initial", "Developing", "Defined", "Managed", "Optimized"][min(4, int(overall)-1)],
                "dimensions": result_rows, "roadmap": roadmap}
    artifact = ctx.write_artifact("maturity-assessment.json", json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", "json", "Accessibility maturity assessment")
    return result("attention", f"Maturity score {overall:.2f} of 5 across {len(result_rows)} dimensions.", [],
                  metrics=envelope, artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=roadmap[:5])


# --------------------------------------------------------------------------- #
# Standards crosswalk and WCAG map
# --------------------------------------------------------------------------- #
def _tool_dir() -> Path:
    return Path(__file__).resolve().parent


def run_crosswalk(ctx: EngineContext) -> EngineResult:
    wcag_path = _tool_dir() / "asset-wcag-22.json"
    cross_path = _tool_dir() / "asset-standards-crosswalk.json"
    if not wcag_path.exists() or not cross_path.exists():
        raise ToolError("asset-missing", "The standards assets are missing.",
                        "Keep asset-wcag-22.json and asset-standards-crosswalk.json beside the tool files.", str(_tool_dir()))
    wcag = json.loads(wcag_path.read_text(encoding="utf-8"))
    cross = json.loads(cross_path.read_text(encoding="utf-8"))
    level_filter = ctx.params.get("level", "")
    entries = [e for e in cross["entries"] if not level_filter or e["level"] == level_filter]
    md = Markdown()
    md.h(1, "Accessibility standards crosswalk")
    md.p("Planning aid only. Verify exact clause text against the current published standards before legal or procurement use.")
    md.h(2, "Sources")
    md.bullets(cross["sources"])
    md.h(2, "Crosswalk")
    md.table(["WCAG", "Level", "Title", "EN 301 549", "Section 508", "ISO 30071-1"],
             [[e["wcag"], e["level"], e["wcag_title"], e["en_301_549"], e["section_508"], e["iso_30071_1"]] for e in entries])
    artifact = ctx.write_artifact("standards-crosswalk.md", md.render(), "markdown", "Standards crosswalk")
    return result("passed", f"Crosswalk generated for {len(entries)} WCAG criteria.", [],
                  metrics={"criteria": len(entries), "wcag_version": wcag["standard"]},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])))


WCAG_KEYWORDS = {
    "1.1.1": ["image", "alt", "icon", "chart", "graph", "diagram", "video", "audio", "non-text"],
    "1.2.2": ["caption", "subtitle", "video", "media"],
    "1.2.5": ["audio description", "described video", "video"],
    "1.3.1": ["heading", "table", "list", "structure", "semantic", "form", "label"],
    "1.3.2": ["order", "sequence", "reading order", "layout"],
    "1.4.1": ["color", "status", "error", "selection"],
    "1.4.3": ["contrast", "text", "color"],
    "1.4.4": ["resize", "zoom", "text size"],
    "1.4.10": ["reflow", "responsive", "mobile", "320"],
    "1.4.11": ["non-text contrast", "focus", "control", "icon"],
    "1.4.12": ["text spacing", "typography", "line height"],
    "2.1.1": ["keyboard", "key", "remote", "switch"],
    "2.1.2": ["keyboard trap", "focus trap", "dialog"],
    "2.1.4": ["shortcut", "accesskey"],
    "2.2.1": ["timeout", "time limit", "session"],
    "2.2.2": ["pause", "stop", "hide", "animation", "autoplay"],
    "2.2.6": ["timeout", "session", "time limit"],
    "2.3.1": ["flash", "seizure", "photosensitivity"],
    "2.3.3": ["motion", "animation", "reduced motion"],
    "2.4.1": ["skip", "bypass", "navigation"],
    "2.4.2": ["title", "page title"],
    "2.4.3": ["focus order", "tabindex", "focus"],
    "2.4.4": ["link", "link text"],
    "2.4.6": ["heading", "label"],
    "2.4.7": ["focus", "focus indicator", "keyboard"],
    "2.4.11": ["focus", "obscured", "sticky header"],
    "2.5.1": ["gesture", "swipe", "drag", "pointer"],
    "2.5.2": ["pointer", "click", "activation"],
    "2.5.3": ["label in name", "voice control", "accessible name"],
    "2.5.4": ["motion", "device motion", "tilt"],
    "2.5.7": ["drag", "slider", "pointer"],
    "2.5.8": ["target size", "touch target", "button"],
    "3.1.1": ["language", "lang", "locale"],
    "3.1.2": ["language of parts", "foreign phrase", "locale"],
    "3.2.1": ["on focus", "context change"],
    "3.2.2": ["on input", "context change"],
    "3.2.3": ["navigation", "consistent"],
    "3.2.4": ["consistent", "icon", "label"],
    "3.2.6": ["help", "support", "contact"],
    "3.3.1": ["error", "validation", "form"],
    "3.3.2": ["label", "instruction", "form"],
    "3.3.3": ["error suggestion", "validation"],
    "3.3.4": ["error prevention", "review", "confirm", "financial", "legal"],
    "3.3.6": ["redundant entry", "re-enter", "autocomplete"],
    "3.3.7": ["authentication", "login", "captcha", "password", "mfa"],
    "4.1.2": ["name", "role", "value", "aria", "custom widget", "control"],
    "4.1.3": ["status message", "live region", "toast", "alert"],
}


def run_wcag_map(ctx: EngineContext) -> EngineResult:
    wcag_path = _tool_dir() / "asset-wcag-22.json"
    if not wcag_path.exists():
        raise ToolError("asset-missing", "The WCAG 2.2 asset is missing.", "Keep asset-wcag-22.json beside the tool files.", str(wcag_path))
    wcag = json.loads(wcag_path.read_text(encoding="utf-8"))
    criteria = {c["id"]: c for c in wcag["criteria"]}
    journeys: list[dict[str, Any]] = []
    if ctx.input_path:
        path = resolve_input_file(ctx, [".json", ".txt", ".md"], "journey list")
        try:
            data = read_json(path)
        except ToolError:
            data = None
        if data is None:
            text = read_text(path)
            journeys = [{"name": line.strip(), "description": line.strip()} for line in text.splitlines() if line.strip()]
        elif isinstance(data, list):
            journeys = [{"name": str(x), "description": str(x)} for x in data]
        elif isinstance(data, dict):
            journeys = data.get("journeys", [])
            if not journeys and "name" in data:
                journeys = [data]
    if not journeys:
        journeys = [{"name": name, "description": desc} for name, desc in [
            ("Sign in", "Email, password, multi-factor authentication, and account recovery."),
            ("Search and browse", "Search, filters, results, and navigation."),
            ("Complete a form", "Labels, validation, errors, review, and submission."),
            ("Checkout", "Cart, address, payment, confirmation, and error recovery."),
            ("View media", "Video captions, audio description, transcript, and player controls."),
        ]]
    rows = []
    for journey in journeys:
        text = (str(journey.get("name", "")) + " " + str(journey.get("description", ""))).lower()
        mapped = []
        for cid, words in WCAG_KEYWORDS.items():
            if cid in criteria and any(w in text for w in words):
                mapped.append(cid)
        if not mapped:
            mapped = ["2.1.1", "2.4.3", "3.3.1", "4.1.2"]
        rows.append({"journey": journey.get("name", "Journey"), "description": journey.get("description", ""),
                     "criteria": ", ".join(mapped), "count": len(mapped),
                     "owner": "", "evidence": ""})
    md = Markdown()
    md.h(1, "WCAG 2.2 A/AA requirements map")
    md.p("Mapping is a planning aid. Confirm applicability and evidence with the accountable owner.")
    md.table(["Journey", "Mapped criteria", "Count", "Owner", "Evidence"],
             [[r["journey"], r["criteria"], r["count"], r["owner"], r["evidence"]] for r in rows])
    artifact = ctx.write_artifact("wcag-requirements-map.md", md.render(), "markdown", "WCAG requirements map")
    return result("attention", f"Mapped {len(rows)} journey(s) to WCAG 2.2 A/AA criteria.", [],
                  metrics={"journeys": len(rows), "criteria_used": len({c for r in rows for c in r['criteria'].split(', ')})},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Assign an owner to each journey.", "Attach evidence for each mapped criterion.", "Review the map with the accessibility lead."])


# --------------------------------------------------------------------------- #
# KPI design
# --------------------------------------------------------------------------- #
def run_kpi_design(ctx: EngineContext) -> EngineResult:
    org = ctx.config.data.get("organization", {})
    measures = [
        {"objective": "Remove barriers from critical journeys", "kpi": "Critical journey completion rate with assistive technology",
         "baseline": "Measure in the next audit", "target": "100% of critical journeys", "method": "Task-based AT testing each release"},
        {"objective": "Reduce the time to remediate", "kpi": "Median remediation time for critical and serious findings",
         "baseline": "Measure from the risk register", "target": "Critical 7 days; serious 30 days", "method": "Risk register timestamps"},
        {"objective": "Prevent barriers from escaping", "kpi": "Blocker escape rate", "baseline": "Measure from support tickets and audits",
         "target": "Less than 5% of releases", "method": "Post-release audit and support tagging"},
        {"objective": "Improve disabled-user satisfaction", "kpi": "Accessibility satisfaction score", "baseline": "Survey in the next quarter",
         "target": "At least 80% satisfied", "method": "Accessible survey with disabled participants"},
        {"objective": "Reduce exceptions", "kpi": "Active accessibility exceptions", "baseline": "Count from the exception register",
         "target": "Zero permanent exceptions", "method": "Exception review with expiry dates"},
        {"objective": "Build capability", "kpi": "Role-based training completion", "baseline": "Learning record",
         "target": "90% of required roles", "method": "Training completion report"},
        {"objective": "Improve localization quality", "kpi": "Localized release first-pass acceptance", "baseline": "Scorecard from review",
         "target": "At least 95% first-pass acceptance", "method": "Language quality scorecard"},
        {"objective": "Increase disabled-user engagement", "kpi": "Disabled participants in research", "baseline": "Research panel composition",
         "target": "Representative panel each quarter", "method": "Recruitment and session records"},
    ]
    envelope = {"organization": org.get("name", "The organization"), "measures": measures,
                "reporting_cadence": "Monthly dashboard; quarterly executive review",
                "anti_patterns": ["Counting automated scans as coverage", "Measuring only issue counts", "Reporting training attendance without application"]}
    artifact = ctx.write_artifact("accessibility-kpi-okrs.json", json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", "json", "Accessibility KPI and OKR set")
    return result("passed", f"Designed {len(measures)} outcome-based measures with baselines and targets.", [],
                  metrics={"measures": len(measures)}, artifacts=[artifact],
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Validate the baselines with the accountable owners.", "Add the measures to the monthly dashboard."])


# --------------------------------------------------------------------------- #
# Exception review
# --------------------------------------------------------------------------- #
def run_exception_review(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires an exception request JSON.",
                        "Pass --input with a JSON object containing scope, constraint, equivalent_access, duration_days, and owner.")
    req = read_json(resolve_input_file(ctx, [".json"], "exception request"))
    if not isinstance(req, dict):
        raise ToolError("request-format", "The exception request must be a JSON object.", "Provide the required fields.", str(ctx.input_path))
    required = ["scope", "constraint", "equivalent_access", "duration_days", "owner"]
    missing = [f for f in required if f not in req]
    if missing:
        raise ToolError("request-missing-fields", f"Exception request is missing: {missing}",
                        "Provide scope, constraint, equivalent_access, duration_days, and owner.", str(ctx.input_path))
    findings: list[Finding] = []
    duration = int(req.get("duration_days", 0))
    if duration <= 0:
        findings.append(_f("exception", "serious", "Exception has no positive duration", "request",
                            "Every exception needs an expiry date.", remediation="Set a duration in days."))
    if duration > 365:
        findings.append(_f("exception", "serious", "Exception exceeds 12 months", "request",
                            f"{duration} days is too long for a time-bound exception.", remediation="Reduce the duration or split the exception."))
    if not str(req.get("equivalent_access", "")).strip():
        findings.append(_f("exception", "critical", "No equivalent access plan", "request",
                            "An exception must preserve equivalent access.", remediation="Describe the alternative access and how it will be provided."))
    if not str(req.get("owner", "")).strip():
        findings.append(_f("exception", "serious", "No accountable owner", "request",
                            "Every exception needs an owner.", remediation="Name the accountable owner."))
    if not str(req.get("constraint", "")).strip():
        findings.append(_f("exception", "moderate", "No documented constraint", "request",
                            "Explain why the requirement cannot be met.", remediation="Document the technical or business constraint."))
    decision = "reject" if any(f.severity == "critical" for f in findings) else ("revise" if any(f.severity == "serious" for f in findings) else "approve_with_conditions")
    envelope = {"decision": decision, "request": req, "findings": [f.as_dict() for f in findings],
                "conditions": ["Record the exception in the register with an expiry date.", "Provide equivalent access and monitor its use.", "Re-test at expiry and remediate or renew with approval."],
                "human_owner": req.get("owner", "")}
    artifact = ctx.write_artifact("exception-review.json", json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", "json", "Exception review decision")
    return result("attention", f"Exception review recommendation: {decision}.", findings,
                  metrics={"decision": decision, "duration_days": duration}, artifacts=[artifact],
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Route the recommendation to the accountable owner for a decision.", "Record the decision and expiry date."])


# --------------------------------------------------------------------------- #
# Evidence repository
# --------------------------------------------------------------------------- #
def run_evidence_repo(ctx: EngineContext) -> EngineResult:
    root = Path(ctx.input_path) if ctx.input_path else ctx.output_dir
    if not root.exists():
        raise ToolError("input-missing", f"Evidence path does not exist: {root}", "Check the path and retry.", str(root))
    files = [p for p in iter_files(root, [".json", ".csv", ".md", ".txt", ".html", ".pdf", ".docx", ".xlsx", ".pptx", ".vtt", ".srt", ".wav", ".png", ".jpg"], 500) if p.is_file()]
    rows = []
    for p in files:
        rows.append({"path": str(p), "name": p.name, "kind": file_kind(p), "bytes": p.stat().st_size,
                     "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["path", "name", "kind", "bytes", "modified"])
    writer.writeheader(); writer.writerows(rows)
    artifact = ctx.write_artifact("evidence-manifest.csv", buf.getvalue(), "csv", "Evidence manifest")
    gaps = []
    for kind in ("json", "csv", "markdown", "pdf", "ooxml"):
        if not any(r["kind"] == kind for r in rows):
            gaps.append(f"No {kind} evidence found.")
    return result("attention" if gaps else "passed", f"Inventory of {len(rows)} evidence file(s) with {len(gaps)} gap(s).", [],
                  metrics={"files": len(rows), "gaps": gaps}, artifacts=[artifact],
                  limitations=list(ctx.cap.get("limitations", [])), next_steps=["Close the evidence gaps before the next audit."])


# --------------------------------------------------------------------------- #
# Defect triage and remediation verification
# --------------------------------------------------------------------------- #
def run_defect_triage(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a defect CSV or JSON.",
                        "Pass --input with defects containing title, severity, journey, and workaround fields.")
    rows: list[dict[str, Any]] = []
    path = resolve_input_file(ctx, [".csv", ".json"], "defects")
    if path.suffix.lower() == ".json":
        data = read_json(path)
        rows = data if isinstance(data, list) else data.get("defects", [])
    else:
        rows, _ = read_csv(path)
    if not rows:
        raise ToolError("empty-defects", "No defects found.", "Add defect rows and retry.", str(ctx.input_path))
    weights = {"critical": 5, "serious": 4, "moderate": 3, "minor": 2, "info": 1}
    scored = []
    for i, row in enumerate(rows, 1):
        sev = str(row.get("severity", "moderate")).lower()
        journey = str(row.get("journey", row.get("component", "")))
        workaround = str(row.get("workaround", "")).lower()
        score = weights.get(sev, 3) * 10
        if journey:
            score += 3
        if workaround in ("none", "no", "false", ""):
            score += 4
        scored.append({"rank": 0, "id": row.get("id", f"D-{i:03d}"), "title": row.get("title", "Defect"),
                       "severity": sev, "journey": journey, "score": score,
                       "priority": "P0" if score >= 50 else "P1" if score >= 40 else "P2" if score >= 25 else "P3",
                       "rationale": "High severity, important journey, no workaround" if score >= 50 else "Review with the product owner"})
    scored.sort(key=lambda r: -r["score"])
    for i, row in enumerate(scored, 1):
        row["rank"] = i
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(scored[0].keys()))
    writer.writeheader(); writer.writerows(scored)
    artifact = ctx.write_artifact("defect-triage.csv", buf.getvalue(), "csv", "Prioritized defect queue")
    return result("attention", f"Triaged {len(scored)} defect(s); {sum(1 for r in scored if r['priority']=='P0')} P0.", [],
                  metrics={"defects": len(scored), "p0": sum(1 for r in scored if r['priority']=='P0')},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Review the P0 queue with the product owner.", "Assign owners and due dates."])


def run_remediation_verify(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a retest JSON file.",
                        "Pass --input with original findings and retest results.")
    data = read_json(resolve_input_file(ctx, [".json"], "retest evidence"))
    if not isinstance(data, dict):
        raise ToolError("retest-format", "The retest file must be a JSON object with findings and retest arrays.", "Provide the original findings and retest results.", str(ctx.input_path))
    original = data.get("findings", [])
    retest = data.get("retest", [])
    retest_by_id = {str(r.get("id", r.get("title", ""))): r for r in retest if isinstance(r, dict)}
    results = []
    for f in original:
        key = str(f.get("id", f.get("title", "")))
        r = retest_by_id.get(key)
        if not r:
            results.append({"id": key, "title": f.get("title", ""), "decision": "unverified", "reason": "No retest evidence"})
        elif str(r.get("status", "")).lower() in ("passed", "fixed", "verified"):
            results.append({"id": key, "title": f.get("title", ""), "decision": "verified", "reason": "Retest passed"})
        else:
            results.append({"id": key, "title": f.get("title", ""), "decision": "reopened", "reason": r.get("reason", "Retest failed")})
    unverified = [r for r in results if r["decision"] != "verified"]
    artifact = ctx.write_artifact("remediation-verification.json", json.dumps(results, indent=2, ensure_ascii=False) + "\n", "json", "Remediation verification")
    return result("failed" if unverified else "passed", f"Verified {len(results)-len(unverified)} of {len(results)} finding(s).", [],
                  metrics={"original": len(original), "retest": len(retest), "unverified": len(unverified)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Reopen unverified findings and assign owners.", "Attach the retest evidence to each verified finding."])


# --------------------------------------------------------------------------- #
# Procurement review
# --------------------------------------------------------------------------- #
def run_procurement_review(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a vendor or VPAT/ACR document.",
                        "Pass --input with a JSON, text, Markdown, or HTML document.")
    path = resolve_input_file(ctx, [".json", ".txt", ".md", ".html"], "vendor document")
    text = read_text(path, ctx.limits.get("max_bytes", 8 * 1024 * 1024))
    mode = ctx.params.get("mode", "vpat")
    findings: list[Finding] = []
    claims = []
    for pattern, label in [(r"WCAG\s*2\.\d", "WCAG version"), (r"Level\s*AA", "Level AA claim"), (r"EN\s*301\s*549", "EN 301 549"),
                           (r"Section\s*508", "Section 508"), (r"VPAT|ACR", "VPAT/ACR"), (r"tested with", "Testing method")]:
        if re.search(pattern, text, re.I):
            claims.append(label)
    if "WCAG version" not in claims or "Level AA claim" not in claims:
        findings.append(_f("procurement", "serious", "No complete WCAG conformance claim found", path.name,
                            "The document does not state a WCAG conformance level.", wcag="2.4.5",
                            remediation="Request a current Accessibility Conformance Report with a clear level and scope."))
    if "tested with" not in text.lower():
        findings.append(_f("procurement", "moderate", "No testing method or assistive technology described", path.name,
                            "The vendor does not explain how conformance was tested.", wcag="2.4.5",
                            remediation="Request the test methods, AT used, and tester names."))
    if not re.search(r"exception|limitation|known issue", text, re.I):
        findings.append(_f("procurement", "moderate", "No known limitations or exceptions disclosed", path.name,
                            "A credible ACR discloses limitations.", wcag="2.4.5",
                            remediation="Request a complete list of exceptions and remediation dates."))
    if not re.search(r"remediat|roadmap|fix", text, re.I):
        findings.append(_f("procurement", "minor", "No remediation commitment found", path.name,
                            "The document does not describe how issues will be fixed.", wcag="2.4.5",
                            remediation="Require remediation terms and renewal conditions."))
    if mode in ("component", "vendor"):
        findings.append(_f("procurement", "info", f"{mode} review requires additional evidence", path.name,
                            "Request a demo, a test environment, and a completed accessibility questionnaire.", wcag="2.4.5",
                            remediation="Schedule a technical review and an AT test of the component."))
    status = status_from_findings(normalize_findings([f.as_dict() for f in findings]))
    return result(status, f"Procurement review of {path.name}: {len(findings)} finding(s).", findings,
                  metrics={"mode": mode, "claims": claims, "bytes": path.stat().st_size},
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Send the follow-up questions to the vendor.", "Require a current ACR and remediation terms before renewal."])


# --------------------------------------------------------------------------- #
# CI generation
# --------------------------------------------------------------------------- #
def run_ci_generate(ctx: EngineContext) -> EngineResult:
    tool = "tools-accessibility-localization.py"
    yaml = f"""name: accessibility-localization
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  accessibility:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Run Skill Tool self-test
        run: python3 {tool} self-test
      - name: Static WCAG audit
        run: python3 {tool} run automated-accessibility-scanner --input src --output output/scanner
      - name: Locale placeholder and parity audit
        run: python3 {tool} run placeholder-and-variable-validator --input locales/en.json --output output/placeholders
      - name: Pseudo-localization
        run: python3 {tool} run pseudolocalization-test-suite --input locales/en.json --output output/pseudo
      - name: Upload evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: accessibility-evidence
          path: output/
"""
    artifact = ctx.write_artifact("accessibility-ci.yml", yaml, "yaml", "GitHub Actions accessibility pipeline")
    return result("passed", "Generated a GitHub Actions pipeline that runs the Skill Tool self-test and locale checks.", [],
                  metrics={"steps": 6}, artifacts=[artifact],
                  limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Review the pipeline in a branch.", "Adjust the input paths to the repository layout.", "Add the gate job before deployment."])


# --------------------------------------------------------------------------- #
# Traceability
# --------------------------------------------------------------------------- #
def run_traceability(ctx: EngineContext) -> EngineResult:
    if not ctx.input_path:
        raise ToolError("input-required", "This capability requires a traceability JSON file.",
                        "Pass --input with findings and requirements arrays.")
    data = read_json(resolve_input_file(ctx, [".json"], "traceability data"))
    if not isinstance(data, dict):
        raise ToolError("traceability-format", "The traceability file must be a JSON object.", "Provide findings and requirements arrays.", str(ctx.input_path))
    findings = data.get("findings", [])
    requirements = data.get("requirements", [])
    req_ids = {str(r.get("id", r.get("title", ""))) for r in requirements}
    rows = []
    orphans = []
    for f in findings:
        req = str(f.get("requirement", f.get("requirement_id", "")))
        linked = req in req_ids if req else False
        if not linked:
            orphans.append(f.get("id", f.get("title", "finding")))
        rows.append({"finding_id": f.get("id", ""), "title": f.get("title", ""), "severity": f.get("severity", ""),
                     "requirement": req, "linked": "yes" if linked else "no",
                     "owner": f.get("owner", ""), "verification": f.get("verification", "")})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["finding_id", "title", "severity", "requirement", "linked", "owner", "verification"])
    writer.writeheader(); writer.writerows(rows)
    artifact = ctx.write_artifact("traceability-matrix.csv", buf.getvalue(), "csv", "Traceability matrix")
    return result("attention" if orphans else "passed", f"Traceability matrix with {len(rows)} finding(s); {len(orphans)} orphan(s).", [],
                  metrics={"findings": len(findings), "requirements": len(requirements), "orphans": len(orphans)},
                  artifacts=[artifact], limitations=list(ctx.cap.get("limitations", [])),
                  next_steps=["Link orphan findings to requirements or close them.", "Assign an owner and verification method to each row."])


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
ENGINES: dict[str, Callable[[EngineContext], EngineResult]] = {
    "static_audit": run_static_audit,
    "contrast_audit": run_contrast_audit,
    "document_audit": run_document_audit,
    "media_audit": run_media_audit,
    "i18n_audit": run_i18n_audit,
    "pseudo_generate": run_pseudo_generate,
    "locale_format": run_locale_format,
    "termbase_audit": run_termbase_audit,
    "tm_match": run_tm_match,
    "review_workbench": run_review_workbench,
    "readability": run_readability,
    "font_coverage": run_font_coverage,
    "caption_qa": run_caption_qa,
    "audio_description": run_audio_description,
    "loudness": run_loudness,
    "data_alternative": run_data_alternative,
    "governance_artifact": run_governance_artifact,
    "plan_artifact": run_plan_artifact,
    "risk_register": run_risk_register,
    "release_gate": run_release_gate,
    "maturity_assess": run_maturity_assess,
    "crosswalk": run_crosswalk,
    "wcag_map": run_wcag_map,
    "kpi_design": run_kpi_design,
    "exception_review": run_exception_review,
    "evidence_repo": run_evidence_repo,
    "defect_triage": run_defect_triage,
    "remediation_verify": run_remediation_verify,
    "procurement_review": run_procurement_review,
    "ci_generate": run_ci_generate,
    "traceability": run_traceability,
}


def execute(cap: dict[str, Any], ctx: EngineContext) -> EngineResult:
    engine = ENGINES.get(cap["operation"])
    if not engine:
        raise ToolError("operation-missing", f"No engine implemented for operation: {cap['operation']}",
                        "Add an engine mapping or update the registry.", cap["operation"])
    return engine(ctx)
