#!/usr/bin/env python3
"""Bounded, offline static checks used by the Ultimate Web Maker CLI."""
from __future__ import annotations

import json
import os
import posixpath
import re
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class AuditFailure(Exception):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SENSITIVE_BASENAMES = {
    ".env", ".npmrc", ".netrc", ".git-credentials", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "private.key", "server.key",
}
SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "A private-key marker appears in a text file."),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "A credential-like cloud access identifier appears in a text file."),
    (re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|secret|access[_-]?token)\b\s*[:=]\s*['\"]([^'\"]{12,})['\"]"), "A credential-like assignment appears in a text file."),
]
PLACEHOLDER_SECRET_VALUES = {"changeme", "change-me", "replace-me", "your-secret-here", "example-secret", "placeholder"}


class VisibleHTMLParser(HTMLParser):
    """Collect only visible text from copied HTML; ignore attributes and script/style bodies."""
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "p", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.suppressed += 1
            return
        if not self.suppressed and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1
            return
        if not self.suppressed and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def _line_for(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _is_sensitive_name(name: str) -> bool:
    low = name.casefold()
    if low in SENSITIVE_BASENAMES:
        return True
    if low.startswith(".env.") and not low.endswith(SAFE_ENV_SUFFIXES):
        return True
    if low.endswith((".key", ".p12", ".pfx")):
        return True
    return False


class HTMLInspector(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    CONTROL_TAGS = {"input", "select", "textarea"}
    NAMED_TAGS = {"a", "button"}
    REFERENCE_ATTRS = {
        "a": ("href", "link"),
        "area": ("href", "link"),
        "link": ("href", "stylesheet"),
        "script": ("src", "script"),
        "img": ("src", "image"),
        "source": ("src", "media"),
        "video": ("poster", "media"),
        "audio": ("src", "media"),
        "iframe": ("src", "frame"),
        "form": ("action", "form"),
        "object": ("data", "media"),
    }

    def __init__(self, relpath: str, add_finding) -> None:
        super().__init__(convert_charrefs=True)
        self.relpath = relpath
        self.add_finding = add_finding
        self.ids: set[str] = set()
        self.id_counts: Counter[str] = Counter()
        self.references: list[dict] = []
        self.pending_controls: list[dict] = []
        self.label_for: set[str] = set()
        self.label_depth = 0
        self.heading_levels: list[tuple[int, int]] = []
        self.main_count = 0
        self.html_lang = ""
        self.title_seen = False
        self.title_text = ""
        self.title_depth = 0
        self.description_seen = False
        self.viewport_seen = False
        self.doctype_seen = False
        self.anchor_contexts: list[dict] = []
        self.has_manifest_link = False
        self.json_ld_parts: list[str] = []
        self.in_json_ld = False

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs if k}

    def _reference(self, value: str, kind: str, line: int) -> None:
        if value.strip():
            self.references.append({"url": value.strip(), "kind": kind, "line": line})

    def handle_decl(self, decl: str) -> None:
        if decl.strip().casefold().startswith("doctype html"):
            self.doctype_seen = True

    def handle_starttag(self, tag: str, raw_attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = self._attrs(raw_attrs)
        line = self.getpos()[0]
        if tag == "html":
            self.html_lang = attrs.get("lang", "").strip()
            if not self.html_lang:
                self.add_finding("A11Y001", "medium", "accessibility", self.relpath, line,
                                 "The root html element has no lang attribute.",
                                 "Set lang to the primary content language and verify it after localization.")
        if tag == "main":
            self.main_count += 1
        if tag == "title":
            self.title_seen = True
            self.title_depth += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append((int(tag[1]), line))
        if "id" in attrs and attrs["id"].strip():
            value = attrs["id"].strip()
            self.ids.add(value)
            self.id_counts[value] += 1
        if tag == "meta":
            meta_name = attrs.get("name", "").strip().casefold()
            prop = attrs.get("property", "").strip().casefold()
            if meta_name == "description" and attrs.get("content", "").strip():
                self.description_seen = True
            if meta_name == "viewport" and "width=device-width" in attrs.get("content", "").casefold():
                self.viewport_seen = True
            if meta_name == "description" and not attrs.get("content", "").strip():
                self.add_finding("SEO002", "low", "seo", self.relpath, line,
                                 "The meta description is empty.",
                                 "Add a concise page-specific summary or remove the empty metadata tag.")
        if tag == "link" and "manifest" in attrs.get("rel", "").casefold().split():
            self.has_manifest_link = True
        if tag == "label":
            self.label_depth += 1
            if attrs.get("for", "").strip():
                self.label_for.add(attrs["for"].strip())
        if tag in self.CONTROL_TAGS:
            input_type = attrs.get("type", "").casefold()
            if not (tag == "input" and input_type in {"hidden", "submit", "button", "reset"}):
                self.pending_controls.append({
                    "id": attrs.get("id", "").strip(),
                    "line": line,
                    "inside_label": self.label_depth > 0,
                    "has_aria": bool(attrs.get("aria-label", "").strip() or attrs.get("aria-labelledby", "").strip()),
                    "tag": tag,
                })
        if tag == "img":
            if "alt" not in attrs:
                self.add_finding("A11Y002", "medium", "accessibility", self.relpath, line,
                                 "An img element has no alt attribute.",
                                 "Add meaningful alternative text, or alt=\"\" when the image is decorative.")
            elif attrs.get("alt", "").strip():
                for ctx in self.anchor_contexts:
                    ctx["parts"].append(attrs["alt"])
            if not attrs.get("width") or not attrs.get("height"):
                self.add_finding("PERF001", "low", "performance", self.relpath, line,
                                 "An image has no explicit width and height.",
                                 "Provide intrinsic dimensions or an aspect ratio to reduce layout shifts.")
        if tag == "iframe" and not (attrs.get("title", "").strip() or attrs.get("aria-label", "").strip()):
            self.add_finding("A11Y003", "medium", "accessibility", self.relpath, line,
                             "An iframe has no accessible title.",
                             "Give the embedded frame a concise title describing its content.")
        if tag == "script" and attrs.get("src") and not ("defer" in attrs or "async" in attrs or attrs.get("type", "").casefold() == "module"):
            self.add_finding("PERF002", "low", "performance", self.relpath, line,
                             "A classic external script is not deferred or asynchronous.",
                             "Use defer for order-dependent page scripts, or async when execution order is irrelevant.")
        if tag == "script" and attrs.get("type", "").casefold() == "application/ld+json":
            self.in_json_ld = True
            self.json_ld_parts = []
        if tag == "div" and attrs.get("onclick") and (attrs.get("role", "").casefold() != "button" or not attrs.get("tabindex")):
            self.add_finding("A11Y004", "medium", "accessibility", self.relpath, line,
                             "A clickable div lacks a complete keyboard-operable button pattern.",
                             "Prefer a native button or provide an appropriate role, tabindex, and keyboard behavior.")
        if tag == "a" and attrs.get("target", "").casefold() == "_blank":
            rel = set(attrs.get("rel", "").casefold().split())
            if "noopener" not in rel:
                self.add_finding("SEC002", "low", "security", self.relpath, line,
                                 "A target=_blank link does not declare rel=noopener.",
                                 "Add rel=\"noopener\" (and noreferrer when privacy policy requires it).")
        for attr_name, value in attrs.items():
            if attr_name.startswith("on") and len(attr_name) > 2:
                self.add_finding("SEC004", "medium", "security", self.relpath, line,
                                 "An inline event-handler attribute is present.",
                                 "Move behavior to a reviewed script and keep untrusted values out of executable markup.")
        if tag in self.REFERENCE_ATTRS:
            attr_name, kind = self.REFERENCE_ATTRS[tag]
            if attrs.get(attr_name):
                self._reference(attrs[attr_name], kind, line)
        if tag == "source" and attrs.get("srcset"):
            for candidate in re.split(r",\s*", attrs["srcset"]):
                url = candidate.strip().split()[0] if candidate.strip() else ""
                self._reference(url, "media", line)
        if tag == "link" and "stylesheet" in attrs.get("rel", "").casefold().split() and attrs.get("href"):
            self._reference(attrs["href"], "stylesheet", line)
        if tag in self.NAMED_TAGS:
            self.anchor_contexts.append({"tag": tag, "attrs": attrs, "line": line, "parts": []})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_text += data
        if self.in_json_ld:
            self.json_ld_parts.append(data)
        for ctx in self.anchor_contexts:
            ctx["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "label" and self.label_depth:
            self.label_depth -= 1
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag == "script" and self.in_json_ld:
            raw = "".join(self.json_ld_parts).strip()
            self.in_json_ld = False
            if raw:
                try:
                    json.loads(raw)
                except json.JSONDecodeError as exc:
                    self.add_finding("SEO004", "medium", "seo", self.relpath, exc.lineno,
                                     "A JSON-LD structured-data block is not valid JSON.",
                                     "Correct the JSON-LD syntax and validate its schema with the target search documentation.")
        if tag in self.NAMED_TAGS:
            for index in range(len(self.anchor_contexts) - 1, -1, -1):
                ctx = self.anchor_contexts[index]
                if ctx["tag"] == tag:
                    del self.anchor_contexts[index]
                    attrs = ctx["attrs"]
                    name = " ".join(" ".join(ctx["parts"]).split())
                    labelled = bool(attrs.get("aria-label", "").strip() or attrs.get("aria-labelledby", "").strip() or attrs.get("title", "").strip())
                    if tag == "a" and attrs.get("href") and not (name or labelled):
                        self.add_finding("A11Y005", "medium", "accessibility", self.relpath, ctx["line"],
                                         "A link has no discernible accessible name.",
                                         "Add concise link text or an accessible label; decorative images do not name a link.")
                    if tag == "button" and not (name or labelled or attrs.get("value", "").strip()):
                        self.add_finding("A11Y006", "medium", "accessibility", self.relpath, ctx["line"],
                                         "A button has no discernible accessible name.",
                                         "Add visible text or an accessible label that describes the action.")
                    break

    def finish(self) -> dict:
        if not self.doctype_seen:
            self.add_finding("HTML001", "low", "html", self.relpath, 1,
                             "The document has no HTML doctype declaration.",
                             "Start the document with <!doctype html> for standards mode.")
        if not self.title_seen or not self.title_text.strip():
            self.add_finding("SEO001", "medium", "seo", self.relpath, 1,
                             "The page has no non-empty title element.",
                             "Add a concise, page-specific title element.")
        if not self.description_seen:
            self.add_finding("SEO002", "low", "seo", self.relpath, 1,
                             "The page has no non-empty meta description.",
                             "Add a useful page summary; confirm its content against the rendered page.")
        if not self.viewport_seen:
            self.add_finding("RESP001", "low", "responsive", self.relpath, 1,
                             "The page has no device-width viewport declaration.",
                             "Add a responsive viewport meta element and verify zoom remains enabled.")
        if self.main_count == 0:
            self.add_finding("A11Y007", "low", "accessibility", self.relpath, 1,
                             "No main landmark was found.",
                             "Use one main element for the page's primary content.")
        elif self.main_count > 1:
            self.add_finding("A11Y007", "low", "accessibility", self.relpath, 1,
                             "More than one main landmark was found.",
                             "Use a single visible main landmark for the page content.")
        previous = 0
        for level, line in self.heading_levels:
            if previous and level > previous + 1:
                self.add_finding("A11Y008", "low", "accessibility", self.relpath, line,
                                 "The heading outline skips a level.",
                                 "Review heading hierarchy in reading order; choose levels by structure, not appearance.")
            previous = level
        for value, count in self.id_counts.items():
            if count > 1:
                self.add_finding("HTML002", "low", "html", self.relpath, None,
                                 "The document repeats an id value.",
                                 "Give each id a unique value so fragments and accessible references resolve reliably.")
        for control in self.pending_controls:
            if not (control["has_aria"] or control["inside_label"] or (control["id"] and control["id"] in self.label_for)):
                self.add_finding("A11Y009", "medium", "accessibility", self.relpath, control["line"],
                                 f"A {control['tag']} control has no detectable label association.",
                                 "Use a visible label, wrapping label, or correctly referenced accessible name.")
        for tag, line in self.heading_levels:
            pass
        return {"ids": self.ids, "references": self.references, "has_manifest_link": self.has_manifest_link}


def _add_css_checks(relpath: str, text: str, add_finding) -> None:
    if re.search(r"\b(?:outline\s*:\s*(?:none|0)\b)", text, re.I):
        add_finding("A11Y010", "medium", "accessibility", relpath, None,
                    "CSS removes an outline; keyboard focus may become invisible.",
                    "Keep a visible :focus or :focus-visible indicator for every interactive control.")
    if not re.search(r":focus(?:-visible)?\b", text, re.I):
        add_finding("A11Y011", "low", "accessibility", relpath, None,
                    "No focus selector was found in this stylesheet.",
                    "Verify visible keyboard focus, including high-contrast and forced-colors modes.")
    if not re.search(r"@media\b|@container\b", text, re.I):
        add_finding("RESP002", "low", "responsive", relpath, None,
                    "No media or container query was found in this stylesheet.",
                    "Confirm layout adapts to narrow screens, zoom, and component constraints.")
    if re.search(r"\b(?:animation|transition)\s*:", text, re.I) and not re.search(r"prefers-reduced-motion", text, re.I):
        add_finding("A11Y012", "medium", "accessibility", relpath, None,
                    "Motion styles have no prefers-reduced-motion rule.",
                    "Provide a reduced-motion alternative and test the operating-system preference.")
    for match in re.finditer(r"@import\s+(?:url\(\s*)?['\"]?([^\s'\")]+)", text, re.I):
        url = match.group(1)
        if urlsplit(url).scheme in {"http", "https"} or url.startswith("//"):
            add_finding("PERF003", "low", "performance", relpath, _line_for(text, match.start()),
                        "A remote CSS import is present.",
                        "Review third-party privacy, availability, blocking behavior, and font performance; prefer controlled local assets where appropriate.")


def _add_javascript_checks(relpath: str, text: str, add_finding) -> None:
    dangerous = [
        (re.compile(r"(?<![\w$])eval\s*\("), "SEC005", "Dynamic eval execution appears in JavaScript."),
        (re.compile(r"\bnew\s+Function\s*\("), "SEC005", "Dynamic Function construction appears in JavaScript."),
        (re.compile(r"\bdocument\s*\.\s*write\s*\("), "SEC006", "document.write appears in JavaScript."),
    ]
    for pattern, code, message in dangerous:
        match = pattern.search(text)
        if match:
            add_finding(code, "high", "security", relpath, _line_for(text, match.start()), message,
                        "Remove dynamic execution or document.write; use bounded, reviewed DOM APIs and validated data.")
    for match in re.finditer(r"\b(?:innerHTML|outerHTML)\s*\s*=(?!=)", text):
        add_finding("SEC007", "medium", "security", relpath, _line_for(text, match.start()),
                    "An HTML string sink is assigned in JavaScript.",
                    "Prefer textContent or safe DOM construction; sanitize and context-encode any required untrusted markup.")


def _scan_secret_patterns(relpath: str, text: str, add_finding) -> None:
    for pattern, message in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if match.lastindex:
                value = match.group(1).strip().casefold()
                if value in PLACEHOLDER_SECRET_VALUES or value.startswith("your-") or value.startswith("example-"):
                    continue
            add_finding("SEC008", "high", "security", relpath, _line_for(text, match.start()), message,
                        "Remove the value from source, use an approved secret store, and rotate it if it was real. The report deliberately redacts values.")
            break


def _normalize_local_target(relpath: str, url: str) -> tuple[str | None, str, bool]:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.casefold()
    if scheme == "javascript":
        return None, unquote(parsed.fragment), False
    if parsed.netloc or scheme in {"http", "https", "mailto", "tel", "data", "blob", "sms"}:
        return None, unquote(parsed.fragment), False
    raw_path = unquote(parsed.path)
    if not raw_path:
        return relpath, unquote(parsed.fragment), True
    if raw_path.startswith("/"):
        joined = posixpath.normpath(raw_path.lstrip("/"))
    else:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(relpath), raw_path))
    if joined == ".":
        joined = "index.html"
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None, unquote(parsed.fragment), True
    if raw_path.endswith("/"):
        joined = posixpath.join(joined, "index.html")
    return joined, unquote(parsed.fragment), True


def audit_project(root: Path, config: dict) -> dict:
    """Inspect a local project without executing code or opening network connections."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise AuditFailure("invalid_project", "The project path must be an existing directory and must not be a symbolic link.", "Pass a real project directory with --project.")

    limits = config["limits"]
    scanner = config["scanner"]
    deadline = time.monotonic() + limits["operation_timeout_seconds"]

    def check_deadline() -> None:
        if time.monotonic() > deadline:
            raise AuditFailure("timeout", "The audit exceeded its configured time limit.", "Scope the audit to a smaller project or raise operation_timeout_seconds within the supported maximum.")

    findings: list[dict] = []
    finding_keys: set[tuple] = set()
    partial = False
    truncated_findings = False

    def add_finding(rule_id: str, severity: str, category: str, relpath: str, line: int | None,
                    message: str, recommendation: str) -> None:
        nonlocal partial, truncated_findings
        key = (rule_id, relpath, line, message)
        if key in finding_keys:
            return
        if len(findings) >= limits["max_findings"]:
            partial = True
            truncated_findings = True
            return
        finding_keys.add(key)
        findings.append({
            "id": rule_id,
            "severity": severity,
            "category": category,
            "path": relpath,
            "line": line,
            "message": message,
            "recommendation": recommendation,
        })

    ignored_dirs = {name.casefold() for name in scanner["skip_directories"]}
    extensions = {ext.casefold() for ext in scanner["text_extensions"]}
    all_paths: set[str] = set()
    sizes: dict[str, int] = {}
    texts: dict[str, str] = {}
    scan_warnings: list[dict] = []
    file_count = 0
    entries_seen = 0
    read_count = 0
    total_bytes = 0
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        check_deadline()
        current, depth = stack.pop()
        try:
            entries = []
            with os.scandir(current) as iterator:
                for entry in iterator:
                    if entries_seen >= limits["max_project_entries"]:
                        partial = True
                        scan_warnings.append({"code": "entry_limit", "path": current.relative_to(root).as_posix() or "."})
                        stack.clear()
                        break
                    entries_seen += 1
                    entries.append(entry)
            entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        except OSError:
            scan_warnings.append({"code": "directory_unreadable", "path": current.relative_to(root).as_posix() or "."})
            partial = True
            continue
        for entry in entries:
            check_deadline()
            if entry.is_symlink():
                partial = True
                scan_warnings.append({"code": "symlink_skipped", "path": Path(entry.path).relative_to(root).as_posix()})
                continue
            relpath = Path(entry.path).relative_to(root).as_posix()
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.casefold() in ignored_dirs:
                        continue
                    if depth >= limits["max_depth"]:
                        partial = True
                        scan_warnings.append({"code": "depth_limit", "path": relpath})
                        continue
                    stack.append((Path(entry.path), depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                file_count += 1
                all_paths.add(relpath)
                if file_count > limits["max_project_files"]:
                    partial = True
                    scan_warnings.append({"code": "file_limit", "path": relpath})
                    stack.clear()
                    break
                stat_result = entry.stat(follow_symlinks=False)
                size = stat_result.st_size
                sizes[relpath] = size
                if total_bytes + size > limits["max_total_bytes"]:
                    partial = True
                    scan_warnings.append({"code": "total_byte_limit", "path": relpath})
                    stack.clear()
                    break
                total_bytes += size
                if _is_sensitive_name(entry.name):
                    add_finding("SEC001", "high", "security", relpath, None,
                                "A potentially sensitive credential or private-key file is present; its contents were not read.",
                                "Remove local secrets from the project, add appropriate ignore rules, and rotate any value that was committed.")
                    continue
                suffix = Path(entry.name).suffix.casefold()
                if suffix not in extensions and entry.name.casefold() not in {"dockerfile", "makefile"}:
                    continue
                if size > limits["max_file_bytes"]:
                    partial = True
                    scan_warnings.append({"code": "file_byte_limit", "path": relpath})
                    continue
                with open(entry.path, "rb") as handle:
                    raw = handle.read(limits["max_file_bytes"] + 1)
                if len(raw) > limits["max_file_bytes"]:
                    partial = True
                    scan_warnings.append({"code": "file_grew_during_read", "path": relpath})
                    continue
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    partial = True
                    scan_warnings.append({"code": "non_utf8_text_skipped", "path": relpath})
                    continue
                if "\x00" in text:
                    partial = True
                    scan_warnings.append({"code": "binary_content_skipped", "path": relpath})
                    continue
                texts[relpath] = text
                read_count += 1
                _scan_secret_patterns(relpath, text, add_finding)
            except OSError:
                scan_warnings.append({"code": "file_unreadable", "path": relpath})
                partial = True

    html_info: dict[str, dict] = {}
    local_references: list[dict] = []
    for relpath, text in texts.items():
        check_deadline()
        suffix = Path(relpath).suffix.casefold()
        if suffix in {".html", ".htm"}:
            parser = HTMLInspector(relpath, add_finding)
            try:
                parser.feed(text)
                parser.close()
            except Exception:
                add_finding("HTML003", "low", "html", relpath, None,
                            "The HTML parser could not fully inspect this file.",
                            "Check the markup with a standards-aware validator and a browser.")
            info = parser.finish()
            html_info[relpath] = info
            for ref in info["references"]:
                local_references.append({"source": relpath, **ref})
        elif suffix in {".css", ".scss", ".sass", ".less"}:
            _add_css_checks(relpath, text, add_finding)
            for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", text, re.I):
                url = match.group(2).strip()
                if url and not url.startswith("#"):
                    local_references.append({"source": relpath, "url": url, "kind": "style-asset", "line": _line_for(text, match.start())})
            for match in re.finditer(r"@import\s+(?:url\(\s*)?['\"]?([^\s'\")]+)", text, re.I):
                local_references.append({"source": relpath, "url": match.group(1), "kind": "stylesheet", "line": _line_for(text, match.start())})
        elif suffix in {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}:
            _add_javascript_checks(relpath, text, add_finding)
        elif suffix == ".webmanifest":
            try:
                manifest = json.loads(text)
                if not isinstance(manifest, dict):
                    raise ValueError("manifest root is not an object")
                icons = manifest.get("icons", [])
                if not manifest.get("name") and not manifest.get("short_name"):
                    add_finding("PWA001", "low", "pwa", relpath, 1,
                                "The web manifest has neither name nor short_name.",
                                "Provide an app name and validate required fields for supported target browsers.")
                if isinstance(icons, list):
                    for icon in icons:
                        if isinstance(icon, dict) and icon.get("src"):
                            local_references.append({"source": relpath, "url": str(icon["src"]), "kind": "media", "line": None})
            except (json.JSONDecodeError, ValueError):
                add_finding("PWA002", "medium", "pwa", relpath, 1,
                            "The web manifest is not a valid JSON object.",
                            "Correct the JSON and verify manifest fields and icons in supported browsers.")

    for relpath, text in texts.items():
        if Path(relpath).suffix.casefold() in {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"} and re.search(r"serviceWorker\s*\.\s*register\s*\(", text):
            html_has_manifest = any(info.get("has_manifest_link") for info in html_info.values())
            manifest_path_present = any(Path(path).suffix.casefold() == ".webmanifest" for path in all_paths)
            if not html_has_manifest and not manifest_path_present:
                add_finding("PWA003", "low", "pwa", relpath, None,
                            "A service worker is registered but no manifest link or webmanifest file was found.",
                            "Confirm installability requirements for the target browsers and add valid app metadata when needed.")

    for ref in local_references:
        check_deadline()
        raw_url = ref["url"]
        source = ref["source"]
        kind = ref["kind"]
        line = ref.get("line")
        parsed = urlsplit(raw_url.strip())
        if parsed.scheme.casefold() == "javascript":
            add_finding("SEC003", "high", "security", source, line,
                        "A javascript: URL appears in a local resource reference.",
                        "Remove script URLs; use reviewed event handlers and safe text/DOM APIs.")
            continue
        if parsed.scheme.casefold() == "http" and kind in {"script", "stylesheet", "image", "media", "frame", "style-asset"}:
            add_finding("SEC009", "medium", "security", source, line,
                        "A remote resource is loaded over plain HTTP.",
                        "Use HTTPS and review the provider, integrity, privacy, availability, and content-security policy.")
        if parsed.netloc or parsed.scheme.casefold() in {"http", "https", "mailto", "tel", "data", "blob", "sms"}:
            continue
        target, fragment, is_local = _normalize_local_target(source, raw_url)
        if not is_local or target is None:
            add_finding("LINK001", "medium", "navigation", source, line,
                        "A local URL escapes the project root.",
                        "Use a project-relative URL that resolves inside the site root.")
            continue
        if target not in all_paths:
            add_finding("LINK002", "medium", "navigation", source, line,
                        "A referenced local file does not exist in the scanned project.",
                        "Correct the relative path or add the referenced asset before release.")
            continue
        if fragment and Path(target).suffix.casefold() in {".html", ".htm"} and target in html_info:
            if fragment not in html_info[target]["ids"]:
                add_finding("LINK003", "low", "navigation", source, line,
                            "A local page fragment does not match an id in the target document.",
                            "Update the fragment or add the intended id; verify the route in a browser.")
        if kind in {"image", "media", "style-asset"} and sizes.get(target, 0) > 1_500_000:
            add_finding("PERF004", "low", "performance", source, line,
                        "A referenced local media asset is larger than 1.5 MB.",
                        "Measure transfer cost and consider responsive sizing, compression, or a suitable modern format.")

    if not any(Path(path).suffix.casefold() in {".html", ".htm"} for path in texts):
        add_finding("QUALITY001", "info", "quality", ".", None,
                    "No readable HTML file was found; browser-level HTML checks were not run.",
                    "Use the framework's own build and tests, or provide generated HTML for static inspection.")
    if truncated_findings:
        scan_warnings.append({"code": "finding_limit", "path": "."})

    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 99), item["path"], item["line"] or 0, item["id"]))
    by_severity = Counter(item["severity"] for item in findings)
    by_category = Counter(item["category"] for item in findings)
    return {
        "schema_version": 1,
        "tool": "ultimate-web-maker",
        "operation": "audit",
        "completed": True,
        "project": {"name": root.name or ".", "root": "."},
        "scan": {
            "files_seen": file_count,
            "filesystem_entries_seen": entries_seen,
            "text_files_read": read_count,
            "bytes_considered": total_bytes,
            "partial": bool(partial),
            "network_accessed": False,
            "code_executed": False,
            "limits": {
                "max_project_files": limits["max_project_files"],
                "max_project_entries": limits["max_project_entries"],
                "max_file_bytes": limits["max_file_bytes"],
                "max_total_bytes": limits["max_total_bytes"],
                "max_depth": limits["max_depth"],
                "operation_timeout_seconds": limits["operation_timeout_seconds"],
            },
            "warnings": scan_warnings,
        },
        "summary": {
            "finding_count": len(findings),
            "by_severity": {key: by_severity.get(key, 0) for key in ("critical", "high", "medium", "low", "info")},
            "by_category": dict(sorted(by_category.items())),
            "status": "partial" if partial else "complete",
        },
        "findings": findings,
        "limitations": [
            "Heuristic local source inspection only; no code, shell commands, browsers, APIs, devices, databases, or network services were executed.",
            "This report is not a WCAG certification, penetration test, performance measurement, compatibility guarantee, or deployment health check.",
            "Review findings manually and run the target stack's own tests in a trusted environment.",
        ],
    }
