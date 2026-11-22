#!/usr/bin/env python3
"""Core OOXML (.docx/.docm/.dotx/.dotm) package utilities for the
word-document-agent skill tool.

This module is the shared foundation used by the primary entry point
``tools-word-doc-agent.py`` and by ``tools-docx-audit.py``,
``tools-docx-edit.py``, ``tools-docx-export.py`` and
``tools-selftest.py``.  It is loaded through ``importlib`` because the
packaged filename contains hyphens.

Design rules
------------
* Standard library only (zipfile, xml.etree.ElementTree, hashlib, ...).
* Parts that are not touched keep their original bytes, so unrelated
  document content survives byte-for-byte.
* Every write is atomic (temp file + os.replace) so an interrupted run
  never leaves a half-written document behind.
* Limits, timeouts and paths are configurable through
  ``asset-default-config.json``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------
# Namespaces
# --------------------------------------------------------------------------

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

for _prefix, _uri in NS.items():
    if _prefix == "xml":
        continue
    try:
        ET.register_namespace(_prefix, _uri)
    except ValueError:  # pragma: no cover - defensive
        pass


def qn(tag: str) -> str:
    """``qn('w:p')`` -> ``'{http://.../main}p'``."""
    prefix, _, local = tag.partition(":")
    return "{%s}%s" % (NS[prefix], local)


def local(tag: str) -> str:
    """Element tag without its namespace: ``'{uri}p'`` -> ``'p'``."""
    return tag.rsplit("}", 1)[-1]


def attr(el, name: str, default=None):
    """Read a namespaced attribute (``'w:val'``) or a plain one."""
    if el is None:
        return default
    if ":" in name:
        return el.get(qn(name), default)
    return el.get(name, default)


# Frequently used qualified tags -------------------------------------------
W_P, W_R, W_T, W_TBL, W_TR, W_TC = qn("w:p"), qn("w:r"), qn("w:t"), qn("w:tbl"), qn("w:tr"), qn("w:tc")
W_TAB, W_BR, W_CR, W_INSTR = qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:instrText")
W_INS, W_DEL, W_MOVEFROM, W_MOVETO = qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo")
W_DELTEXT, W_DELINSTR = qn("w:delText"), qn("w:delInstrText")
W_SECTPR, W_SDT, W_SDTCONTENT, W_SDTPR = qn("w:sectPr"), qn("w:sdt"), qn("w:sdtContent"), qn("w:sdtPr")
W_HYPERLINK, W_FLDCHAR, W_FLDSIMPLE = qn("w:hyperlink"), qn("w:fldChar"), qn("w:fldSimple")
W_BOOKMARKSTART, W_BOOKMARKEND = qn("w:bookmarkStart"), qn("w:bookmarkEnd")
W_STYLE, W_NAME, W_BASEDON = qn("w:style"), qn("w:name"), qn("w:basedOn")
W_PPR, W_RPR, W_VANISH, W_COLOR, W_SZ = qn("w:pPr"), qn("w:rPr"), qn("w:vanish"), qn("w:color"), qn("w:sz")
W_LANG, W_RFONTS = qn("w:lang"), qn("w:rFonts")
W_COMMENTRANGESTART = qn("w:commentRangeStart")
W_COMMENTRANGEEND = qn("w:commentRangeEnd")
W_COMMENTREF = qn("w:commentReference")
W_DRAWING, W_PICT, W_OBJECT = qn("w:drawing"), qn("w:pict"), qn("w:object")
W_TXBX = qn("w:txbxContent")
W_FOOTNOTEREF, W_ENDNOTEREF = qn("w:footnoteReference"), qn("w:endnoteReference")
W_DOCPR = qn("wp:docPr")

# Change-tracking bookkeeping elements that describe *previous* state.
PROPERTY_CHANGE_TAGS = {
    qn("w:rPrChange"), qn("w:pPrChange"), qn("w:tblPrChange"), qn("w:trPrChange"),
    qn("w:tcPrChange"), qn("w:sectPrChange"), qn("w:numberingChange"),
}

# Content-type strings -----------------------------------------------------
CT_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CT_MAIN_MACRO = "application/vnd.ms-word.document.macroEnabled.main+xml"
CT_TEMPLATE = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
CT_TEMPLATE_MACRO = "application/vnd.ms-word.template.macroEnabledTemplate.main+xml"
FORMAT_BY_CONTENT_TYPE = {
    CT_MAIN: "docx",
    CT_MAIN_MACRO: "docm",
    CT_TEMPLATE: "dotx",
    CT_TEMPLATE_MACRO: "dotm",
}

REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_HEADER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
REL_FOOTER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_SETTINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
REL_FOOTNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
REL_ENDNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes"
REL_NUMBERING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"

CT_HEADER = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
CT_FOOTER = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
CT_STYLES = "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
CT_SETTINGS = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_FOOTNOTES = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
CT_ENDNOTES = "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"
CT_RELATIONSHIPS = "application/vnd.openxmlformats-package.relationships+xml"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"
CT_APP = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
CT_CUSTOM = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
CT_CONTENT_TYPES = "application/vnd.openxmlformats-officedocument.package.content-types+xml"

XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

# --------------------------------------------------------------------------
# Errors, limits, configuration
# --------------------------------------------------------------------------

EXIT_CODES = {
    "OK": 0,
    "ERROR": 1,
    "USAGE": 2,
    "NOT_FOUND": 3,
    "APPROVAL_REQUIRED": 4,
    "LIMIT": 5,
    "INTEGRITY": 6,
    "EXTERNAL_UNAVAILABLE": 7,
    "POLICY": 8,
}


class ToolError(Exception):
    """Structured, user-facing failure."""

    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code if code in EXIT_CODES else "ERROR"
        self.message = message
        self.details = details or {}

    def as_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message,
                                       "details": self.details}}

    @property
    def exit_code(self):
        return EXIT_CODES.get(self.code, 1)


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(HERE, "asset-default-config.json")

BUILTIN_CONFIG = {
    "paths": {
        "state_dir": ".word-document-agent-state",
        "backup_dir": ".word-document-agent-state/backups",
        "report_dir": ".word-document-agent-state/reports",
    },
    "limits": {
        "max_input_bytes": 104857600,
        "max_zip_entries": 10000,
        "max_part_bytes": 67108864,
        "max_replacements": 20000,
        "max_merge_records": 500,
        "max_pdf_pages": 400,
        "max_scan_text_chars": 4000000,
        "max_seconds": 300,
        "max_backups_per_source": 10,
    },
    "external": {
        "pdf_converter": "",
        "pdf_converter_args": ["{input}", "--headless", "--convert-to", "pdf", "--outdir", "{outdir}"],
        "timeout_seconds": 180,
        "allow_subprocess": False,
    },
    "editing": {
        "require_approval_for": [
            "sanitize", "accept-revisions", "reject-revisions", "unprotect",
            "restore", "in-place", "watermark-remove", "protect",
        ],
        "allowed_input_extensions": [".docx", ".docm", ".dotx", ".dotm"],
        "refuse_output_overwrite": True,
        "allow_in_place": True,
    },
    "defaults": {
        "sanitized_author": "Author",
        "title": "Untitled document",
        "watermark_text": "DRAFT",
        "watermark_color": "#C0C0C0",
        "toc_depth": 3,
        "placeholder_pattern": "{{key}}",
    },
    "policy_files": {"inspection_policy": "asset-inspection-policy.json",
                     "capability_map": "asset-capability-map.json",
                     "catalog": "asset-catalog.json"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | None = None) -> dict:
    """Merge BUILTIN_CONFIG <- asset-default-config.json <- ``path``."""
    cfg = json.loads(json.dumps(BUILTIN_CONFIG))
    if os.path.isfile(DEFAULT_CONFIG_FILE):
        with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as fh:
            cfg = _deep_merge(cfg, json.load(fh))
    if path:
        if not os.path.isfile(path):
            raise ToolError("NOT_FOUND", "config file not found: %s" % path, {"path": path})
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = _deep_merge(cfg, json.load(fh))
        except json.JSONDecodeError as exc:
            raise ToolError("USAGE", "config file is not valid JSON: %s" % exc, {"path": path})
    return cfg


def resolve_asset(name: str, required: bool = True) -> str:
    """Resolve a sibling asset file (relative, case-sensitive)."""
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        if required:
            raise ToolError("NOT_FOUND",
                            "asset file missing next to the tools- files: %s" % name,
                            {"expected": name})
        return ""
    return path


def load_asset_json(name: str, required: bool = True):
    path = resolve_asset(name, required=required)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ToolError("POLICY", "asset file is not valid JSON: %s (%s)" % (name, exc),
                        {"asset": name})


# --------------------------------------------------------------------------
# Paths, deadlines, atomic writes
# --------------------------------------------------------------------------

def validate_input_path(path: str, cfg: dict) -> str:
    if not path:
        raise ToolError("USAGE", "an input document path is required (--input)")
    full = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(full):
        raise ToolError("NOT_FOUND", "input document not found: %s" % path, {"path": path})
    if not os.path.isfile(full):
        raise ToolError("USAGE", "input path is not a file: %s" % path, {"path": path})
    ext = os.path.splitext(full)[1].lower()
    allowed = cfg["editing"]["allowed_input_extensions"]
    if ext not in allowed:
        raise ToolError("USAGE",
                        "unsupported input extension %r (allowed: %s)" % (ext, ", ".join(allowed)),
                        {"extension": ext, "allowed": allowed})
    if ext in (".doc", ".dot", ".rtf", ".pdf"):
        raise ToolError("USAGE", "legacy or non-OOXML input is not supported: %s" % path)
    size = os.path.getsize(full)
    if size > cfg["limits"]["max_input_bytes"]:
        raise ToolError("LIMIT", "input exceeds limits.max_input_bytes",
                        {"bytes": size, "limit": cfg["limits"]["max_input_bytes"]})
    return full


def validate_output_path(path: str, input_path: str | None, cfg: dict,
                         in_place: bool = False) -> str:
    if in_place:
        if not input_path:
            raise ToolError("USAGE", "--in-place requires an input document")
        if not cfg["editing"]["allow_in_place"]:
            raise ToolError("POLICY", "in-place editing is disabled by configuration")
        return os.path.abspath(input_path)
    if not path:
        raise ToolError("USAGE", "an output path is required (--output)")
    full = os.path.abspath(os.path.expanduser(path))
    ext = os.path.splitext(full)[1].lower()
    allowed = cfg["editing"]["allowed_input_extensions"]
    if ext not in allowed:
        raise ToolError("USAGE", "unsupported output extension %r (allowed: %s)"
                        % (ext, ", ".join(allowed)), {"extension": ext})
    if input_path and os.path.abspath(input_path) == full:
        raise ToolError("USAGE",
                        "output path equals the input path; use --in-place --approve to overwrite")
    if cfg["editing"]["refuse_output_overwrite"] and os.path.exists(full) and not in_place:
        raise ToolError("USAGE", "refusing to overwrite existing output: %s" % path,
                        {"path": path, "hint": "remove it or choose another --output"})
    parent = os.path.dirname(full) or "."
    os.makedirs(parent, exist_ok=True)
    return full


def state_path(cfg: dict, kind: str = "state_dir") -> str:
    raw = cfg["paths"].get(kind) or ".word-document-agent-state"
    path = os.path.abspath(os.path.expanduser(raw))
    os.makedirs(path, exist_ok=True)
    return path


class Deadline:
    """Simple cooperative time budget so long loops can be cancelled."""

    def __init__(self, seconds: float):
        self.seconds = float(seconds or 0)
        self.started = time.monotonic()

    @property
    def remaining(self) -> float:
        if self.seconds <= 0:
            return float("inf")
        return self.seconds - (time.monotonic() - self.started)

    def check(self, where: str = "operation"):
        if self.remaining <= 0:
            raise ToolError("LIMIT", "time budget exceeded during %s" % where,
                            {"limit_seconds": self.seconds})


_ACTIVE_DEADLINE = None


def set_deadline(deadline):
    """Register the process-wide cooperative deadline (called by the CLI)."""
    global _ACTIVE_DEADLINE
    _ACTIVE_DEADLINE = deadline
    return deadline


def checkpoint(where: str = "operation"):
    """Abort if the configured time budget is exhausted."""
    if _ACTIVE_DEADLINE is not None:
        _ACTIVE_DEADLINE.check(where)


def atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wda-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------

def register_namespaces_from(raw: bytes) -> None:
    """Register every xmlns declaration found in a part so ElementTree
    preserves the original prefixes when it serializes."""
    head = raw[:8000].decode("utf-8", "replace")
    for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9_.\-]+)="([^"]+)"', head):
        if prefix == "xml":
            continue
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def parse_xml(raw: bytes, part_name: str = "part"):
    register_namespaces_from(raw)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ToolError("INTEGRITY", "malformed XML in %s: %s" % (part_name, exc),
                        {"part": part_name})


def serialize_xml(root) -> bytes:
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return XML_DECL + body


def iter_all(root, tags: set[str]):
    """Yield every element whose tag is in ``tags`` (depth-first)."""
    for el in root.iter():
        if el.tag in tags:
            yield el


def find_child(el, tag: str):
    return el.find(tag) if el is not None else None


def ensure_child(parent, tag: str):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    return el


def strip_ns_decl(text: str) -> str:
    return text


# Suffix that must not contribute displayed text.
TEXT_EXCLUDED = {W_DEL, W_TXBX, W_INSTR, W_DELINSTR}


def para_text(p) -> str:
    """Displayed text of a ``w:p`` (deleted runs and field codes excluded)."""
    chunks: list[str] = []

    def walk(el):
        for child in el:
            tag = child.tag
            if tag == W_T:
                chunks.append(child.text or "")
            elif tag == W_TAB:
                chunks.append("\t")
            elif tag in (W_BR, W_CR):
                chunks.append("\n")
            elif tag in TEXT_EXCLUDED:
                continue
            else:
                walk(child)

    walk(p)
    return "".join(chunks)


def para_style(p):
    ppr = p.find(W_PPR)
    if ppr is None:
        return None
    ps = ppr.find(qn("w:pStyle"))
    return attr(ps, "w:val") if ps is not None else None


def para_outline_level(p):
    ppr = p.find(W_PPR)
    if ppr is None:
        return None
    ol = ppr.find(qn("w:outlineLvl"))
    return attr(ol, "w:val") if ol is not None else None


def iter_body_blocks(root):
    """Yield (kind, element, container) for body-level blocks in order.

    ``kind`` is ``'p'`` or ``'tbl'``; nested tables are yielded with the
    innermost container so callers can report table paths.
    """
    body = root.find(qn("w:body"))
    if body is None:
        return

    def walk(container, depth):
        for el in list(container):
            if el.tag == W_P:
                yield "p", el, depth
            elif el.tag == W_TBL:
                yield "tbl", el, depth
                for tr in el.findall(W_TR):
                    for tc in tr.findall(W_TC):
                        for inner in walk(tc, depth + 1):
                            yield inner
            elif el.tag == W_SDT:
                content = el.find(W_SDTCONTENT)
                if content is not None:
                    for inner in walk(content, depth):
                        yield inner

    for item in walk(body, 0):
        yield item


def iter_paragraphs(root, include_tables: bool = True):
    for kind, el, depth in iter_body_blocks(root):
        if kind == "p":
            yield el
        elif include_tables:
            for tr in el.findall(W_TR):
                for tc in tr.findall(W_TC):
                    for p in tc.iter(W_P):
                        yield p


def iter_text_nodes(p):
    """Return the ``w:t`` nodes of a paragraph in document order."""
    out = []

    def walk(el):
        for child in el:
            if child.tag == W_T:
                out.append(child)
            elif child.tag in (W_DEL, W_TXBX):
                continue
            else:
                walk(child)

    walk(p)
    return out


def table_rows(tbl):
    return tbl.findall(W_TR)


def cell_text(tc) -> str:
    return "\n".join(para_text(p) for p in tc.findall(W_P))


def table_matrix(tbl):
    rows = []
    for tr in table_rows(tbl):
        rows.append([cell_text(tc) for tc in tr.findall(W_TC)])
    return rows


# --------------------------------------------------------------------------
# Field codes
# --------------------------------------------------------------------------

def field_codes_from_paragraphs(root):
    """Collect complex and simple field codes with their run context.

    Returns a list of dicts: ``{'code', 'under_paragraph', 'locked',
    'dirty', 'simple'}``.
    """
    found = []
    for p in iter_paragraphs(root):
        stack = []  # per field: dict(code=..., run=None, ...)
        for el in p.iter():
            if el.tag == W_FLDCHAR:
                kind = attr(el, "w:fldCharType")
                if kind == "begin":
                    stack.append({"code": "", "locked": False, "dirty": attr(el, "w:dirty") == "true"})
                elif kind == "separate":
                    if stack:
                        stack[-1]["in_result"] = True
                elif kind == "end":
                    if stack:
                        info = stack.pop()
                        found.append({"code": info["code"].strip(),
                                      "simple": False,
                                      "locked": bool(info.get("locked")),
                                      "dirty": bool(info.get("dirty"))})
            elif el.tag == W_INSTR:
                if stack:
                    stack[-1]["code"] += el.text or ""
            elif el.tag == qn("w:fldLock") or el.tag == qn("w:rPr"):
                continue
        for el in p.iter(W_FLDSIMPLE):
            found.append({"code": (attr(el, "w:instr") or "").strip(), "simple": True,
                          "locked": attr(el, "w:fldLock") in ("1", "true"), "dirty": False})
    return found


def field_type(code: str) -> str:
    if not code:
        return "UNKNOWN"
    token = code.split()[0].strip().upper()
    return token or "UNKNOWN"


def field_switches(code: str) -> list[str]:
    return [tok for tok in code.split()[1:] if tok.startswith("\\")]


def field_arguments(code: str) -> list[str]:
    return [tok for tok in code.split()[1:] if not tok.startswith("\\")]


# --------------------------------------------------------------------------
# DOCX package
# --------------------------------------------------------------------------

class Docx:
    """A loaded OOXML package.

    Parts are held as raw bytes and only re-serialized when a caller asks
    for a parsed XML tree and the part is saved again, which keeps
    untouched content intact.
    """

    def __init__(self, path: str, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.path = os.path.abspath(path)
        self.entries: dict[str, bytes] = {}
        self.order: list[str] = []
        self.infos: dict[str, zipfile.ZipInfo] = {}
        self._trees: dict[str, object] = {}
        self._dirty: set[str] = set()
        self._load()

    @classmethod
    def from_entries(cls, entries: dict, cfg: dict | None = None):
        """Build an in-memory package (used by document creation)."""
        obj = cls.__new__(cls)
        obj.cfg = cfg or load_config()
        obj.path = "<memory>"
        obj.entries = dict(entries)
        obj.order = list(entries.keys())
        obj.infos = {}
        obj._trees = {}
        obj._dirty = set()
        obj._check_package()
        return obj

    def materialize(self, path: str):
        """Persist an in-memory package so it can be reopened by path."""
        atomic_write_bytes(path, self.to_bytes())
        self.path = os.path.abspath(path)
        return self

    # -- loading -----------------------------------------------------------
    def _load(self):
        limits = self.cfg["limits"]
        try:
            with zipfile.ZipFile(self.path) as zf:
                names = zf.namelist()
                if len(names) > limits["max_zip_entries"]:
                    raise ToolError("LIMIT", "package has too many parts",
                                    {"parts": len(names), "limit": limits["max_zip_entries"]})
                broken = zf.testzip()
                if broken:
                    raise ToolError("INTEGRITY", "corrupt zip member: %s" % broken,
                                    {"part": broken})
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > limits["max_part_bytes"]:
                        raise ToolError("LIMIT", "package part exceeds limits.max_part_bytes",
                                        {"part": info.filename, "bytes": info.file_size})
                    self.entries[info.filename] = zf.read(info.filename)
                    self.order.append(info.filename)
                    self.infos[info.filename] = info
        except zipfile.BadZipFile as exc:
            raise ToolError("INTEGRITY", "not a valid OOXML/zip package: %s" % exc,
                            {"path": self.path})
        except OSError as exc:
            raise ToolError("ERROR", "cannot read package: %s" % exc, {"path": self.path})
        self._check_package()

    def _check_package(self):
        if "[Content_Types].xml" not in self.entries:
            raise ToolError("INTEGRITY", "package has no [Content_Types].xml",
                            {"path": self.path})
        ct = self._content_types()
        main = None
        for override in ct.findall(qn("ct:Override")):
            if attr(override, "ContentType") in FORMAT_BY_CONTENT_TYPE:
                main = attr(override, "PartName")
                break
        self.main_part = (main or "/word/document.xml").lstrip("/")
        if self.main_part not in self.entries:
            raise ToolError("INTEGRITY", "main document part is missing",
                            {"part": self.main_part})

    # -- content types -----------------------------------------------------
    def _content_types(self):
        return self.xml("[Content_Types].xml")

    def format(self) -> str:
        ct = self._content_types()
        for override in ct.findall(qn("ct:Override")):
            value = attr(override, "ContentType")
            if value in FORMAT_BY_CONTENT_TYPE:
                return FORMAT_BY_CONTENT_TYPE[value]
        return "unknown"

    def ensure_override(self, part_name: str, content_type: str):
        ct = self._content_types()
        part_name = "/" + part_name.lstrip("/")
        for override in ct.findall(qn("ct:Override")):
            if attr(override, "PartName") == part_name:
                override.set("ContentType", content_type)
                self._dirty.add("[Content_Types].xml")
                return
        el = ET.SubElement(ct, qn("ct:Override"))
        el.set("PartName", part_name)
        el.set("ContentType", content_type)
        self._dirty.add("[Content_Types].xml")

    def remove_override(self, part_name: str):
        ct = self._content_types()
        part_name = "/" + part_name.lstrip("/")
        for override in list(ct.findall(qn("ct:Override"))):
            if attr(override, "PartName") == part_name:
                ct.remove(override)
                self._dirty.add("[Content_Types].xml")

    # -- trees -------------------------------------------------------------
    def has(self, part: str) -> bool:
        return part in self.entries

    def xml(self, part: str):
        """Parsed tree for ``part`` (cached). Marks nothing dirty until save."""
        part = part.lstrip("/")
        if part in self._trees:
            return self._trees[part]
        if part not in self.entries:
            raise ToolError("NOT_FOUND", "package part not found: %s" % part, {"part": part})
        root = parse_xml(self.entries[part], part)
        self._trees[part] = root
        return root

    def mark_dirty(self, part: str):
        self._dirty.add(part.lstrip("/"))

    def body(self):
        return self.xml(self.main_part).find(qn("w:body"))

    def document(self):
        return self.xml(self.main_part)

    def settings(self, create: bool = True):
        part = "word/settings.xml"
        if not self.has(part):
            if not create:
                return None
            self.add_xml(part, ET.Element(qn("w:settings")), CT_SETTINGS)
        return self.xml(part)

    def styles(self, create: bool = True):
        part = "word/styles.xml"
        if not self.has(part):
            if not create:
                return None
            self.add_xml(part, ET.Element(qn("w:styles")), CT_STYLES)
            self._ensure_rel("styles.xml", REL_STYLES)
        return self.xml(part)

    def numbering(self, create: bool = False):
        part = "word/numbering.xml"
        if not self.has(part):
            if not create:
                return None
            self.add_xml(part, ET.Element(qn("w:numbering")),
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml")
            self._ensure_rel("numbering.xml", REL_NUMBERING)
        return self.xml(part)

    def comments(self):
        return self.xml("word/comments.xml") if self.has("word/comments.xml") else None

    # -- relationships -----------------------------------------------------
    def rels_part_for(self, part: str) -> str:
        directory, name = os.path.split(part.lstrip("/"))
        return "%s/_rels/%s.rels" % (directory, name) if directory else "_rels/%s.rels" % name

    def rels(self, part: str) -> dict[str, dict]:
        """Mapping ``rId -> {type, target, mode, part}`` for ``part``."""
        rels_name = self.rels_part_for(part)
        out: dict[str, dict] = {}
        if rels_name not in self.entries:
            return out
        root = self.xml(rels_name)
        base = os.path.dirname(part)
        for rel in root.findall(qn("pr:Relationship")):
            rid = attr(rel, "Id")
            target = attr(rel, "Target") or ""
            mode = attr(rel, "TargetMode") or "Internal"
            if mode == "Internal":
                resolved = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
            else:
                resolved = target
            out[rid] = {"id": rid, "type": attr(rel, "Type"), "target": target,
                        "mode": mode, "part": resolved}
        return out

    def add_relationship(self, part: str, rel_type: str, target: str,
                         target_mode: str = "Internal") -> str:
        rels_name = self.rels_part_for(part)
        if rels_name not in self.entries:
            root = ET.Element(qn("pr:Relationships"))
            self.add_xml(rels_name, root, CT_RELATIONSHIPS)
        root = self.xml(rels_name)
        used = {attr(rel, "Id") for rel in root.findall(qn("pr:Relationship"))}
        n = 1
        while "rId%d" % n in used:
            n += 1
        rid = "rId%d" % n
        el = ET.SubElement(root, qn("pr:Relationship"))
        el.set("Id", rid)
        el.set("Type", rel_type)
        el.set("Target", target)
        if target_mode != "Internal":
            el.set("TargetMode", target_mode)
        self.mark_dirty(rels_name)
        return rid

    def _ensure_rel(self, target: str, rel_type: str) -> str:
        for rid, info in self.rels(self.main_part).items():
            if info["type"] == rel_type:
                return rid
        return self.add_relationship(self.main_part, rel_type, target)

    def remove_relationship(self, part: str, rid: str):
        rels_name = self.rels_part_for(part)
        if rels_name not in self.entries:
            return
        root = self.xml(rels_name)
        for rel in list(root.findall(qn("pr:Relationship"))):
            if attr(rel, "Id") == rid:
                root.remove(rel)
                self.mark_dirty(rels_name)

    # -- part management ---------------------------------------------------
    def add_xml(self, part: str, root, content_type: str):
        part = part.lstrip("/")
        self.entries[part] = serialize_xml(root)
        if part not in self.order:
            self.order.append(part)
        self._trees[part] = root
        self._dirty.discard(part)
        if content_type:
            self.ensure_override(part, content_type)

    def add_bytes(self, part: str, data: bytes, content_type: str | None = None):
        part = part.lstrip("/")
        self.entries[part] = data
        if part not in self.order:
            self.order.append(part)
        if content_type:
            self.ensure_override(part, content_type)

    def remove_part(self, part: str):
        part = part.lstrip("/")
        self.entries.pop(part, None)
        self._trees.pop(part, None)
        self._dirty.discard(part)
        if part in self.order:
            self.order.remove(part)
        self.remove_override(part)

    def iter_parts(self):
        return list(self.order)

    # -- save --------------------------------------------------------------
    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            order = list(self.order)
            if "[Content_Types].xml" in order:
                order.remove("[Content_Types].xml")
                order.insert(0, "[Content_Types].xml")
            for part in order:
                if part in self._dirty and part in self._trees:
                    data = serialize_xml(self._trees[part])
                else:
                    data = self.entries.get(part, b"")
                info = zipfile.ZipInfo(part, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zf.writestr(info, data)
        return buffer.getvalue()

    def save(self, path: str):
        data = self.to_bytes()
        atomic_write_bytes(path, data)
        return path

    # -- convenience -------------------------------------------------------
    def text(self, include_tables: bool = True, limit_chars: int | None = None) -> str:
        chunks = []
        total = 0
        for p in iter_paragraphs(self.document(), include_tables=include_tables):
            chunks.append(para_text(p))
            total += len(chunks[-1])
            if limit_chars and total >= limit_chars:
                chunks.append("[... truncated ...]")
                break
        return "\n".join(chunks)

    def header_footer_parts(self) -> list[str]:
        return [p for p in self.order if re.match(r"^word/(header|footer)\d*\.xml$", p)]

    def hash(self) -> str:
        return hashlib.sha256(open(self.path, "rb").read()).hexdigest()


# --------------------------------------------------------------------------
# Integrity
# --------------------------------------------------------------------------

def integrity_report(path: str, cfg: dict) -> dict:
    """Structural health check of a package without mutating it."""
    issues: list[dict] = []
    warnings: list[dict] = []
    doc = Docx(path, cfg)
    doc_format = doc.format()
    if doc_format == "unknown":
        issues.append({"code": "unknown-format", "severity": "error",
                       "message": "main document content type is not a Word format"})
    rels = doc.rels(doc.main_part)
    for rid, info in rels.items():
        if info["mode"] == "Internal" and info["part"] not in doc.entries:
            issues.append({"code": "dangling-relationship", "severity": "error",
                           "message": "relationship %s points to missing part %s" % (rid, info["part"])})
    referenced = set()
    for part in doc.order:
        if part.endswith(".rels"):
            continue
        for info in doc.rels(part).values():
            if info["mode"] == "Internal":
                referenced.add(info["part"])
    for part in doc.order:
        if part == "[Content_Types].xml" or part.endswith(".rels"):
            continue
        if part == doc.main_part:
            continue
        if part not in referenced:
            warnings.append({"code": "orphan-part", "severity": "warning",
                             "message": "part is not referenced by any relationship: %s" % part})
    ct = doc._content_types()
    overrides = {attr(o, "PartName").lstrip("/") for o in ct.findall(qn("ct:Override"))}
    defaults = {attr(d, "Extension").lower() for d in ct.findall(qn("ct:Default"))}
    for part in doc.order:
        if part == "[Content_Types].xml":
            continue
        base = part.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        if part not in overrides and ext not in defaults:
            issues.append({"code": "missing-content-type", "severity": "error",
                           "message": "no content type for part %s" % part})
    required = [p for p in ("docProps/core.xml", "docProps/app.xml") if p not in doc.entries]
    for part in required:
        warnings.append({"code": "missing-part", "severity": "warning",
                         "message": "optional metadata part missing: %s" % part})
    for part in doc.order:
        if part.endswith(".xml") or part.endswith(".rels"):
            try:
                parse_xml(doc.entries[part], part)
            except ToolError as exc:
                issues.append({"code": "malformed-xml", "severity": "error",
                               "message": exc.message, "part": part})
    return {
        "path": doc.path,
        "format": doc_format,
        "parts": len(doc.order),
        "bytes": os.path.getsize(doc.path),
        "sha256": hashlib.sha256(open(doc.path, "rb").read()).hexdigest(),
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Small formatting helpers shared by tools
# --------------------------------------------------------------------------

def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.0f %s" % (value, unit) if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024
    return "%d B" % n


def truncate(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def table_text(rows: list[list[str]], headers: list[str] | None = None) -> str:
    """Render a plain-text table that stays readable in a terminal."""
    rows = [[str(c) for c in row] for row in rows]
    if headers:
        rows = [[str(h) for h in headers]] + rows
    if not rows:
        return "(no rows)"
    width = [0] * max(len(r) for r in rows)
    for row in rows:
        for i, cell in enumerate(row):
            width[i] = max(width[i], len(cell))
    lines = []
    for idx, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(width[i]) for i, cell in enumerate(row)).rstrip())
        if headers and idx == 0:
            lines.append("  ".join("-" * width[i] for i in range(len(width))))
    return "\n".join(lines)


def load_self_module(filename: str):
    """Load a sibling ``tools-*.py`` module by its packaged filename."""
    import importlib.util

    name = filename.replace("-", "_").replace(".py", "")
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        raise ToolError("NOT_FOUND", "supporting tool file missing: %s" % filename,
                        {"expected": filename})
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
