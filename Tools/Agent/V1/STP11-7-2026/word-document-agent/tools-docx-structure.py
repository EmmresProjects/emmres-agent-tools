#!/usr/bin/env python3
"""Structural operations for the word-document-agent skill tool.

Creates documents and mutates page/paragraph structure: content blocks,
lists, tables, images, headers/footers, watermarks, table of contents,
form content controls, page geometry and sections.

Loaded by the entry point as ``tools_docx_structure``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from xml.etree import ElementTree as ET

import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def _bootstrap(module_name, filename):
    """Load a sibling tool file (hyphenated filename) under a plain module name."""
    if module_name in _sys.modules:
        return _sys.modules[module_name]
    spec = _ilu.spec_from_file_location(module_name, _os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    _sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


C = _bootstrap("tools_docx_core", "tools-docx-core.py")
for _name in dir(C):
    if not _name.startswith("_"):
        globals()[_name] = getattr(C, _name)

B = _bootstrap("tools_docx_build", "tools-docx-build.py")

PAGE_SIZES_CM = {
    "a4": (21.0, 29.7),
    "a4-landscape": (29.7, 21.0),
    "letter": (21.59, 27.94),
    "legal": (21.59, 35.56),
    "a3": (29.7, 42.0),
    "a5": (14.8, 21.0),
    "tabloid": (27.94, 43.18),
}
CM_PER_INCH = 2.54


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def _length_to_twips(value, unit_default="cm") -> int:
    """Accept 2.5, '2.5cm', '1in', '720twip', '1440dxa', '18pt'."""
    if value is None:
        raise ToolError("USAGE", "a length value is required")
    if isinstance(value, (int, float)):
        return int(round(float(value) * B.TWIPS_PER_CM))
    text = str(value).strip().lower()
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(cm|mm|in|inch|inches|pt|tw|twip|twips|dxa|emu)?", text)
    if not match:
        raise ToolError("USAGE", "cannot parse length: %r (try 2.5cm, 1in, 18pt, 720twips)" % value)
    number = float(match.group(1))
    unit = match.group(2) or unit_default
    if unit == "cm":
        return int(round(number * B.TWIPS_PER_CM))
    if unit == "mm":
        return int(round(number * B.TWIPS_PER_CM / 10.0))
    if unit in ("in", "inch", "inches"):
        return int(round(number * B.TWIPS_PER_INCH))
    if unit == "pt":
        return int(round(number * 20))
    if unit in ("tw", "twip", "twips", "dxa"):
        return int(round(number))
    if unit == "emu":
        return int(round(number / B.EMU_PER_CM * B.TWIPS_PER_CM))
    raise ToolError("USAGE", "unsupported length unit: %s" % unit)


def _points_to_twips(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 20))
    text = str(value).strip().lower()
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(pt|cm|mm|in)?", text)
    if not match:
        raise ToolError("USAGE", "cannot parse spacing value: %r" % value)
    number = float(match.group(1))
    unit = match.group(2) or "pt"
    if unit == "pt":
        return int(round(number * 20))
    return _length_to_twips("%s%s" % (number, unit))


def _body_insert_index(body) -> int:
    """Insert before a trailing body-level ``sectPr`` if present."""
    children = list(body)
    if children and children[-1].tag == W_SECTPR:
        return len(children) - 1
    return len(children)


def _insert_block(body, element):
    body.insert(_body_insert_index(body), element)
    return element


def section_props(doc):
    body = doc.body()
    children = list(body)
    if children and children[-1].tag == W_SECTPR:
        return children[-1]
    sect = ET.SubElement(body, W_SECTPR)
    return sect


def paragraph_page_props(list_sectpr):
    return list_sectpr


# --------------------------------------------------------------------------
# Content blocks
# --------------------------------------------------------------------------

def _heading_style(level: int) -> str:
    level = max(1, min(6, int(level)))
    return "Heading%d" % level


def _table_from_source(params) -> tuple[list[str], list[list[str]]]:
    headers, rows = [], []
    data = params.get("data")
    source = params.get("data_file") or params.get("source_file")
    if source:
        path = os.path.abspath(os.path.expanduser(str(source)))
        if not os.path.isfile(path):
            raise ToolError("NOT_FOUND", "table data file not found: %s" % source)
        raw = open(path, "r", encoding="utf-8-sig").read()
        if path.lower().endswith(".json"):
            data = json.loads(raw)
        else:
            reader = list(csv.reader(io.StringIO(raw)))
            if not reader:
                raise ToolError("USAGE", "CSV data file is empty: %s" % source)
            headers, rows = reader[0], [r for r in reader[1:]]
    if isinstance(data, dict):
        headers = [str(h) for h in (data.get("headers") or [])]
        rows = [[str(c) for c in row] for row in (data.get("rows") or [])]
    elif isinstance(data, list):
        if data and isinstance(data[0], dict):
            headers = list(data[0].keys())
            rows = [[str(item.get(h, "")) for h in headers] for item in data]
        else:
            rows = [[str(c) for c in row] for row in data]
            if params.get("headers"):
                headers = [str(h) for h in params["headers"]]
    if not headers and not rows:
        raise ToolError("USAGE", "table needs --data-file (csv/json) or a data param")
    width = max([len(headers)] + [len(r) for r in rows])
    if headers:
        headers = headers + [""] * (width - len(headers))
    rows = [r + [""] * (width - len(r)) for r in rows]
    return headers, rows


def insert_blocks(doc, blocks, params=None) -> list[dict]:
    """Append blocks (heading/paragraph/table/list/image/toc/...) to the body."""
    params = params or {}
    body = doc.body()
    created = []
    numbering = None
    for block in blocks:
        kind = (block.get("type") or "paragraph").lower()
        if kind in ("heading", "title"):
            text = block.get("text", "")
            style = _heading_style(block.get("level", 1)) if kind == "heading" else "Title"
            p = ET.Element(W_P)
            ppr = B._sub(p, "w:pPr")
            B._sub(ppr, "w:pStyle", val=style)
            B.add_inline(p, text, doc=doc)
            _insert_block(body, p)
            created.append({"type": kind, "style": style, "text": B_para(p)})
        elif kind in ("paragraph", "para", "text"):
            p = ET.Element(W_P)
            ppr = B._sub(p, "w:pPr")
            style = block.get("style")
            if style:
                B._sub(ppr, "w:pStyle", val=style)
            align = block.get("align")
            if align:
                B._sub(ppr, "w:jc", val={"left": "left", "center": "center",
                                         "right": "right", "justify": "both"}.get(align, align))
            if block.get("keep_next"):
                B._sub(ppr, "w:keepNext")
            B.add_inline(p, block.get("text", ""), doc=doc)
            _insert_block(body, p)
            created.append({"type": "paragraph", "text": B_para(p),
                            "style": style or "Normal"})
        elif kind in ("quote", "blockquote"):
            p = B.make_paragraph(block.get("text", ""), style="Quote")
            _insert_block(body, p)
            created.append({"type": "quote", "text": B_para(p)})
        elif kind == "code":
            for line in (block.get("text") or "").split("\n"):
                p = ET.Element(W_P)
                B.add_inline(p, "`%s`" % line if line else "` `", doc=doc)
                _insert_block(body, p)
            created.append({"type": "code", "lines": len((block.get("text") or "").split("\n"))})
        elif kind == "list":
            if numbering is None:
                numbering = B.ensure_numbering(doc)
            num_id = numbering["decimal" if block.get("ordered") else "bullet"]
            for item in block.get("items") or []:
                p = ET.Element(W_P)
                ppr = B._sub(p, "w:pPr")
                B._sub(ppr, "w:pStyle", val="ListParagraph")
                numpr = B._sub(ppr, "w:numPr")
                B._sub(numpr, "w:ilvl", val=block.get("level", 0))
                B._sub(numpr, "w:numId", val=num_id)
                B.add_inline(p, str(item), doc=doc)
                _insert_block(body, p)
            created.append({"type": "list", "items": len(block.get("items") or [])})
        elif kind == "table":
            headers = [str(h) for h in (block.get("headers") or [])]
            rows = [[str(c) for c in row] for row in (block.get("rows") or [])]
            widths = block.get("widths_cm")
            tbl = B.make_table(headers, rows, style=block.get("style") or "TableGrid",
                               repeat_header=block.get("repeat_header", True),
                               widths_cm=[float(w) for w in widths] if widths else None,
                               doc=doc)
            _insert_block(body, tbl)
            created.append({"type": "table", "rows": len(rows), "columns":
                            len(headers) or (len(rows[0]) if rows else 0)})
        elif kind == "pagebreak":
            _insert_block(body, B.make_page_break_paragraph())
            created.append({"type": "pagebreak"})
        elif kind == "caption":
            p = B.make_paragraph("", style="Caption")
            label = block.get("label") or "Table"
            text = block.get("text") or ""
            p.append(B.make_run("%s " % label))
            for run in B.make_field_code("SEQ %s \\* ARABIC" % label, display="1"):
                p.append(run)
            if text:
                p.append(B.make_run(": %s" % text))
            _insert_block(body, p)
            created.append({"type": "caption", "text": text})
        elif kind == "toc":
            depth = int(block.get("depth") or 3)
            heading = B.make_paragraph(block.get("heading") or "Table of Contents",
                                       style="TOCHeading")
            entry = ET.Element(W_P)
            ppr = B._sub(entry, "w:pPr")
            tabs = B._sub(ppr, "w:tabs")
            B._sub(tabs, "w:tab", val="right", leader="dot", pos="9350")
            B.append_field(entry, 'TOC \\o "1-%d" \\h \\z \\u' % depth,
                           display="Right-click and choose Update Field.")
            _insert_block(body, heading)
            _insert_block(body, entry)
            _mark_update_fields(doc)
            created.append({"type": "toc", "depth": depth})
        elif kind == "field":
            p = B.make_paragraph("")
            B.append_field(p, block.get("code") or "DATE", display=block.get("display") or "")
            _insert_block(body, p)
            created.append({"type": "field", "code": block.get("code")})
        elif kind == "image":
            image_path = block.get("path")
            if not image_path:
                raise ToolError("USAGE", "image block needs 'path'")
            para = B.insert_image(doc, image_path, alt=block.get("alt") or "",
                                  width_cm=float(block.get("width_cm") or 12.0),
                                  body=body, index=_body_insert_index(body))
            created.append({"type": "image", "path": str(image_path),
                            "alt": block.get("alt") or ""})
        elif kind == "control":
            sdt = B.make_content_control(block.get("spec") or block, doc=doc)
            _insert_block(body, sdt)
            created.append({"type": "control",
                            "alias": (block.get("spec") or block).get("alias")})
        else:
            raise ToolError("USAGE", "unknown content block type: %r" % kind,
                            {"supported": ["heading", "paragraph", "quote", "code", "list",
                                           "table", "pagebreak", "caption", "toc", "field",
                                           "image", "control"]})
    doc.mark_dirty(doc.main_part)
    return created


def B_para(p) -> str:
    return para_text(p)


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

def create(cfg, params) -> tuple:
    fmt = (params.get("format") or "docx").lower()
    if fmt not in ("docx", "docm", "dotx", "dotm"):
        raise ToolError("USAGE", "create --format must be docx, docm, dotx or dotm")
    title = params.get("title") or cfg["defaults"]["title"]
    author = params.get("author") or cfg["defaults"]["sanitized_author"]
    doc = B.new_blank(cfg, fmt=fmt, title=title, author=author)
    meta = {"format": fmt, "title": title, "blocks": []}
    template = params.get("template")
    if template:
        meta.update(_apply_template(doc, template))
    content = params.get("content") or params.get("markdown") or params.get("content_file")
    if isinstance(content, str) and os.path.exists(os.path.expanduser(str(content))):
        raw = open(os.path.expanduser(str(content)), "r", encoding="utf-8-sig").read()
        if str(content).lower().endswith(".json"):
            content = json.loads(raw)
        else:
            content = raw
    if content:
        blocks = B.normalize_blocks(content)
        meta["blocks"] = insert_blocks(doc, blocks, params)
    if params.get("page_size") or params.get("orientation") or params.get("margins"):
        meta["geometry"] = set_page_geometry(doc, params)
    if params.get("header") or params.get("footer"):
        meta["header_footer"] = set_header_footer(doc, {"header_text": params.get("header"),
                                                        "footer_text": params.get("footer")})
    return doc, meta


def _apply_template(doc, template_path) -> dict:
    """Copy styles/settings/numbering from a base .dotx/.docx package."""
    from tools_docx_core import Docx, validate_input_path
    path = validate_input_path(str(template_path), doc.cfg)
    base = Docx(path, doc.cfg)
    copied = []
    for part, content_type in (
        ("word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
        ("word/settings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"),
        ("word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"),
        ("word/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
    ):
        if base.has(part):
            doc.add_bytes(part, base.entries[part], content_type)
            copied.append(part)
    for part in base.order:
        if re.match(r"^word/(header|footer)\d*\.xml$", part):
            doc.add_bytes(part, base.entries[part],
                          CT_HEADER if "header" in part else
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml")
            rel_type = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                        + ("header" if "header" in part else "footer"))
            rid = doc.add_relationship(doc.main_part, rel_type, os.path.basename(part))
            target = section_props(doc)
            tag = qn("w:headerReference") if "header" in part else qn("w:footerReference")
            el = ET.SubElement(target, tag)
            el.set(qn("w:type"), "default")
            el.set(qn("r:id"), rid)
            copied.append(part)
    # base document body is intentionally not copied: templates supply the frame only
    doc.mark_dirty(doc.main_part)
    return {"template": os.path.basename(path), "template_parts_copied": copied}


# --------------------------------------------------------------------------
# insert
# --------------------------------------------------------------------------

def insert(doc, params) -> dict:
    kind = (params.get("type") or "paragraph").lower()
    anchor = params.get("anchor") or "end"
    body = doc.body()
    if kind == "pagebreak":
        el = B.make_page_break_paragraph()
    elif kind == "columnbreak":
        p = ET.Element(W_P)
        r = B._sub(p, "w:r")
        B._sub(r, "w:br", type="column")
        el = p
    elif kind == "linebreak":
        p = ET.Element(W_P)
        r = B._sub(p, "w:r")
        B._sub(r, "w:br", type="textWrapping", clear="all")
        el = p
    elif kind == "cover":
        title = params.get("title") or "Document title"
        el = None
        blocks = [
            {"type": "paragraph", "text": "", "style": "Normal"},
            {"type": "paragraph", "text": title, "style": "Title", "align": "center"},
        ]
        if params.get("subtitle"):
            blocks.append({"type": "paragraph", "text": params["subtitle"],
                           "style": "Subtitle", "align": "center"})
        if params.get("author"):
            blocks.append({"type": "paragraph", "text": params["author"], "align": "center"})
        if params.get("date", ""):
            blocks.append({"type": "field", "code": "DATE \\@ \"yyyy-MM-dd\"",
                           "display": "date"})
        blocks.append({"type": "pagebreak"})
        created = insert_blocks(doc, blocks, params)
        return {"operation": "insert", "type": "cover", "blocks": created}
    elif kind == "bookmark":
        name = params.get("name") or "Bookmark1"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", name):
            raise ToolError("USAGE", "bookmark name must start with a letter and use letters, "
                                     "digits or underscore (max 40): %r" % name)
        existing = {attr(b, "w:name") for b in doc.document().iter(qn("w:bookmarkStart"))}
        if name in existing:
            raise ToolError("USAGE", "bookmark already exists: %s" % name)
        ids = [int(attr(b, "w:id")) for b in doc.document().iter(qn("w:bookmarkStart"))
               if (attr(b, "w:id") or "").isdigit()]
        new_id = str(max(ids) + 1 if ids else 0)
        p = ET.Element(W_P)
        start = ET.SubElement(p, qn("w:bookmarkStart"))
        start.set(qn("w:id"), new_id)
        start.set(qn("w:name"), name)
        p.append(B.make_run(params.get("text") or name))
        end = ET.SubElement(p, qn("w:bookmarkEnd"))
        end.set(qn("w:id"), new_id)
        el = p
    elif kind == "caption":
        created = insert_blocks(doc, [{"type": "caption", "label": params.get("label") or "Table",
                                       "text": params.get("text") or ""}], params)
        return {"operation": "insert", "type": "caption", "blocks": created}
    elif kind == "control":
        spec = dict(params)
        spec["control_type"] = params.get("control_type") or params.get("alias_type") or "text"
        el = B.make_content_control(spec, doc=doc)
    elif kind == "table":
        headers, rows = _table_from_source(params)
        el = B.make_table(headers, rows, style=params.get("style") or "TableGrid", doc=doc)
    elif kind == "list":
        items = params.get("items")
        if isinstance(items, str):
            items = [i.strip() for i in re.split(r"[;\n]", items) if i.strip()]
        if not items:
            raise ToolError("USAGE", "insert --type list needs --param items=a;b;c")
        return {"operation": "insert", "type": "list", "blocks": insert_blocks(
            doc, [{"type": "list", "items": items,
                   "ordered": str(params.get("ordered", "false")).lower() in ("1", "true", "yes")}],
            params)}
    elif kind == "text":
        blocks = B.parse_markdown(params.get("text") or params.get("markdown") or "")
        return {"operation": "insert", "type": "text", "blocks": insert_blocks(doc, blocks, params)}
    else:
        raise ToolError("USAGE", "unknown insert type: %r" % kind,
                        {"supported": ["pagebreak", "columnbreak", "linebreak", "cover",
                                       "bookmark", "caption", "control", "table", "list", "text"]})
    _place(doc, el, anchor)
    doc.mark_dirty(doc.main_part)
    return {"operation": "insert", "type": kind, "anchor": anchor}


def _place(doc, element, anchor: str):
    """Insert ``element`` at an anchor: end|start|index:N|after-heading:TEXT|before-heading:TEXT."""
    body = doc.body()
    if not anchor or anchor == "end":
        body.insert(_body_insert_index(body), element)
        return
    if anchor == "start":
        body.insert(0, element)
        return
    if anchor.startswith("index:"):
        idx = int(anchor.split(":", 1)[1])
        body.insert(max(0, min(idx, _body_insert_index(body))), element)
        return
    mode, _, needle = anchor.partition(":")
    if mode not in ("after-heading", "before-heading"):
        raise ToolError("USAGE", "unknown anchor: %r" % anchor,
                        {"supported": ["end", "start", "index:N", "after-heading:TEXT",
                                       "before-heading:TEXT"]})
    children = list(body)
    target_index = None
    for idx, child in enumerate(children):
        if child.tag != W_P:
            continue
        style = None
        ppr = child.find(W_PPR)
        if ppr is not None and ppr.find(qn("w:pStyle")) is not None:
            style = attr(ppr.find(qn("w:pStyle")), "w:val")
        if style and style.lower().startswith("heading") and needle.lower() in para_text(child).lower():
            target_index = idx
            break
    if target_index is None:
        raise ToolError("NOT_FOUND", "anchor heading not found: %s" % needle,
                        {"anchor": anchor})
    if mode == "after-heading":
        # skip the heading paragraph and any immediately following paragraph(s) until next heading?
        body.insert(target_index + 1, element)
    else:
        body.insert(target_index, element)


# --------------------------------------------------------------------------
# format (page geometry, sections, columns, paragraph and character defaults)
# --------------------------------------------------------------------------

def set_page_geometry(doc, params) -> dict:
    sect = section_props(doc)
    changed = []
    pg_sz = ensure_child(sect, qn("w:pgSz"))
    if params.get("page_size"):
        key = str(params["page_size"]).lower().replace(" ", "-")
        if key in PAGE_SIZES_CM:
            width, height = PAGE_SIZES_CM[key]
            pg_sz.set(qn("w:w"), str(int(round(width * B.TWIPS_PER_CM))))
            pg_sz.set(qn("w:h"), str(int(round(height * B.TWIPS_PER_CM))))
            changed.append("page_size=%s" % key)
        elif re.fullmatch(r"\d+(\.\d+)?x\d+(\.\d+)?", key):
            width, height = key.split("x")
            pg_sz.set(qn("w:w"), str(_length_to_twips(width + "cm")))
            pg_sz.set(qn("w:h"), str(_length_to_twips(height + "cm")))
            changed.append("page_size=%sx%s cm" % (width, height))
        else:
            raise ToolError("USAGE", "unknown page size: %r" % params["page_size"],
                            {"supported": sorted(PAGE_SIZES_CM) + ["WxCm"]})
    if params.get("orientation"):
        orientation = str(params["orientation"]).lower()
        if orientation not in ("portrait", "landscape"):
            raise ToolError("USAGE", "orientation must be portrait or landscape")
        width = int(pg_sz.get(qn("w:w")) or 11906)
        height = int(pg_sz.get(qn("w:h")) or 16838)
        if orientation == "landscape" and width < height:
            width, height = height, width
        if orientation == "portrait" and width > height:
            width, height = height, width
        pg_sz.set(qn("w:w"), str(width))
        pg_sz.set(qn("w:h"), str(height))
        pg_sz.set(qn("w:orient"), orientation)
        changed.append("orientation=%s" % orientation)
    margins = params.get("margins")
    if margins:
        pg_mar = ensure_child(sect, qn("w:pgMar"))
        if isinstance(margins, str) and "=" in margins:
            for part in margins.split(","):
                key, _, value = part.partition("=")
                key = key.strip().lower()
                if key not in ("top", "bottom", "left", "right", "header", "footer", "gutter"):
                    raise ToolError("USAGE", "unknown margin edge: %s" % key)
                pg_mar.set(qn("w:" + key), str(_length_to_twips(value.strip())))
                changed.append("margin_%s=%s" % (key, value.strip()))
        else:
            twips = _length_to_twips(margins)
            for edge in ("top", "bottom", "left", "right"):
                pg_mar.set(qn("w:" + edge), str(twips))
            changed.append("margins=%s" % margins)
    if params.get("gutter"):
        pg_mar = ensure_child(sect, qn("w:pgMar"))
        pg_mar.set(qn("w:gutter"), str(_length_to_twips(params["gutter"])))
        changed.append("gutter=%s" % params["gutter"])
    doc.mark_dirty(doc.main_part)
    return {"scope": "page", "changes": changed}


def set_columns(doc, params) -> dict:
    sect = section_props(doc)
    cols = ensure_child(sect, qn("w:cols"))
    count = params.get("columns") or params.get("count") or 2
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ToolError("USAGE", "columns must be an integer")
    if not 1 <= count <= 12:
        raise ToolError("LIMIT", "column count must be between 1 and 12", {"value": count})
    cols.set(qn("w:num"), str(count))
    if params.get("space"):
        cols.set(qn("w:space"), str(_length_to_twips(params["space"])))
    if str(params.get("line_between", "")).lower() in ("1", "true", "yes"):
        cols.set(qn("w:sep"), "1")
    elif cols.get(qn("w:sep")):
        del cols.attrib[qn("w:sep")]
    doc.mark_dirty(doc.main_part)
    return {"scope": "columns", "columns": count,
            "space_twips": cols.get(qn("w:space")),
            "line_between": cols.get(qn("w:sep")) == "1"}


def set_paragraph_format(doc, params) -> dict:
    before = params.get("space_before")
    after = params.get("space_after")
    line = params.get("line_spacing")
    indent = params.get("first_line_indent")
    align = params.get("align")
    keep = params.get("keep_next")
    target_style = params.get("style")
    touched = 0
    for p in iter_paragraphs(doc.document()):
        ppr = ensure_child(p, W_PPR)
        if target_style:
            style_el = ppr.find(qn("w:pStyle"))
            if style_el is None or attr(style_el, "w:val") != target_style:
                continue
        if before is not None or after is not None:
            spacing = ensure_child(ppr, qn("w:spacing"))
            if before is not None:
                spacing.set(qn("w:before"), str(_points_to_twips(before)))
            if after is not None:
                spacing.set(qn("w:after"), str(_points_to_twips(after)))
        if line is not None:
            spacing = ensure_child(ppr, qn("w:spacing"))
            value = str(line).lower()
            if value in ("single", "double", "1.0", "2.0", "1.5"):
                mult = {"single": 240, "double": 480, "1.0": 240, "1.5": 360, "2.0": 480}[value]
                spacing.set(qn("w:line"), str(mult))
                spacing.set(qn("w:lineRule"), "auto")
            else:
                spacing.set(qn("w:line"), str(_points_to_twips(line)))
                spacing.set(qn("w:lineRule"), "exact")
        if indent is not None:
            ind = ensure_child(ppr, qn("w:ind"))
            ind.set(qn("w:firstLine"), str(_length_to_twips(indent)))
        if align:
            jc = ensure_child(ppr, qn("w:jc"))
            jc.set(qn("w:val"), {"left": "left", "center": "center", "right": "right",
                                 "justify": "both", "both": "both"}.get(str(align).lower(),
                                                                        str(align).lower()))
        if keep is not None:
            existing = ppr.find(qn("w:keepNext"))
            wanted = str(keep).lower() in ("1", "true", "yes")
            if wanted and existing is None:
                ppr.insert(0, ET.Element(qn("w:keepNext")))
            elif not wanted and existing is not None:
                ppr.remove(existing)
        touched += 1
    doc.mark_dirty(doc.main_part)
    return {"scope": "paragraph", "paragraphs_updated": touched,
            "filter_style": target_style or "all"}


def set_character_defaults(doc, params) -> dict:
    styles = doc.styles()
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is None:
        defaults = ET.SubElement(styles, qn("w:docDefaults"))
    rpr_default = ensure_child(defaults, qn("w:rPrDefault"))
    rpr = ensure_child(rpr_default, qn("w:rPr"))
    changed = []
    if params.get("font") or params.get("font_face"):
        face = params.get("font") or params.get("font_face")
        fonts = ensure_child(rpr, qn("w:rFonts"))
        for key in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(key), str(face))
        changed.append("font=%s" % face)
    if params.get("font_size"):
        size = float(str(params["font_size"]).replace("pt", ""))
        if not 4 <= size <= 200:
            raise ToolError("LIMIT", "font size must be between 4 and 200 pt")
        half = int(round(size * 2))
        ensure_child(rpr, qn("w:sz")).set(qn("w:val"), str(half))
        ensure_child(rpr, qn("w:szCs")).set(qn("w:val"), str(half))
        changed.append("font_size=%gpt" % size)
    if params.get("language"):
        lang = ensure_child(rpr, qn("w:lang"))
        lang.set(qn("w:val"), str(params["language"]))
        lang.set(qn("w:eastAsia"), str(params["language"]))
        changed.append("language=%s" % params["language"])
    if params.get("text_color"):
        color = str(params["text_color"]).lstrip("#").upper()
        ensure_child(rpr, qn("w:color")).set(qn("w:val"), color)
        changed.append("text_color=#%s" % color)
    doc.mark_dirty("word/styles.xml")
    return {"scope": "character", "changes": changed}


def add_section(doc, params) -> dict:
    """Insert a section break before the last paragraph, copying geometry."""
    body = doc.body()
    children = list(body)
    old_sect = children[-1] if children and children[-1].tag == W_SECTPR else None
    break_type = str(params.get("break_type") or "nextPage").lower()
    mapping = {"next-page": "nextPage", "nextpage": "nextPage", "continuous": "continuous",
               "even-page": "evenPage", "evenpage": "evenPage", "odd-page": "oddPage",
               "oddpage": "oddPage"}
    if break_type not in mapping.values() and break_type not in mapping:
        raise ToolError("USAGE", "break_type must be nextPage, continuous, evenPage or oddPage")
    wtype = mapping.get(break_type, break_type)
    new_sect = ET.Element(W_SECTPR)
    if old_sect is not None:
        for child in list(old_sect):
            if child.tag in (qn("w:headerReference"), qn("w:footerReference")):
                continue
            new_sect.append(child.__copy__() if hasattr(child, "__copy__") else child)
    t = ensure_child(new_sect, qn("w:type"))
    t.set(qn("w:val"), wtype)
    last_p = None
    for child in reversed(list(body)):
        if child.tag == W_P:
            last_p = child
            break
    if last_p is None:
        last_p = ET.Element(W_P)
        body.insert(_body_insert_index(body), last_p)
    last_p_ppr = ensure_child(last_p, W_PPR)
    existing = last_p_ppr.find(W_SECTPR)
    if existing is not None:
        last_p_ppr.remove(existing)
    last_p_ppr.append(new_sect)
    doc.mark_dirty(doc.main_part)
    return {"scope": "section", "break_type": wtype,
            "sections_before": len([1 for _ in doc.document().iter(W_SECTPR)]) - 1}


def format_document(doc, params) -> dict:
    scope = (params.get("scope") or "").lower()
    if not scope:
        if params.get("margins") or params.get("page_size") or params.get("orientation"):
            scope = "page"
        elif params.get("columns") or params.get("count"):
            scope = "columns"
        elif params.get("font") or params.get("font_size") or params.get("language"):
            scope = "character"
        elif params.get("break_type"):
            scope = "section"
        else:
            scope = "paragraph"
    if scope in ("page", "geometry"):
        return set_page_geometry(doc, params)
    if scope == "columns":
        return set_columns(doc, params)
    if scope == "character":
        return set_character_defaults(doc, params)
    if scope == "section":
        return add_section(doc, params)
    if scope == "paragraph":
        return set_paragraph_format(doc, params)
    raise ToolError("USAGE", "unknown format scope: %r" % scope,
                    {"supported": ["page", "columns", "character", "section", "paragraph"]})


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

def table(doc, params) -> dict:
    mode = (params.get("mode") or "create").lower()
    body = doc.body()
    if mode == "create":
        headers, rows = _table_from_source(params)
        style = params.get("style") or "TableGrid"
        widths = params.get("widths_cm")
        if isinstance(widths, str):
            widths = [float(w) for w in widths.replace(" ", "").split(",") if w]
        tbl = B.make_table(headers, rows, style=style,
                           repeat_header=str(params.get("repeat_header", "true")).lower()
                           in ("1", "true", "yes"),
                           widths_cm=widths, doc=doc)
        _insert_block(body, tbl)
        doc.mark_dirty(doc.main_part)
        return {"operation": "table", "mode": "create", "rows": len(rows),
                "columns": len(headers) or (len(rows[0]) if rows else 0), "style": style}
    tables = [el for el in doc.document().iter(W_TBL)]
    if not tables:
        raise ToolError("NOT_FOUND", "document contains no tables")
    if mode == "structure":
        result = []
        for idx, tbl in enumerate(tables):
            rows = tbl.findall(W_TR)
            cols = max((len(r.findall(W_TC)) for r in rows), default=0)
            header = rows[0].find(qn("w:trPr")) if rows else None
            result.append({"table_index": idx, "rows": len(rows), "columns": cols,
                           "repeat_header": bool(header is not None and
                                                 header.find(qn("w:tblHeader")) is not None)})
        if params.get("repeat_header") is not None:
            want = str(params["repeat_header"]).lower() in ("1", "true", "yes")
            target = tables[int(params.get("table_index", 0))]
            B.set_table_header_repeat(target, want)
            doc.mark_dirty(doc.main_part)
            return {"operation": "table", "mode": "structure", "tables": result,
                    "repeat_header_set": want}
        return {"operation": "table", "mode": "structure", "tables": result}
    if mode == "sort":
        target = tables[int(params.get("table_index", 0))]
        column = int(params.get("column", 0))
        reverse = str(params.get("descending", "false")).lower() in ("1", "true", "yes")
        rows = target.findall(W_TR)
        if len(rows) < 3:
            return {"operation": "table", "mode": "sort", "rows_sorted": 0,
                    "note": "header row only or empty table"}
        header, data = rows[0], rows[1:]

        def key(tr):
            cells = tr.findall(W_TC)
            return para_text(cells[column]).lower() if column < len(cells) else ""

        for tr in data:
            target.remove(tr)
        for tr in sorted(data, key=key, reverse=reverse):
            target.append(tr)
        doc.mark_dirty(doc.main_part)
        return {"operation": "table", "mode": "sort", "table_index":
                int(params.get("table_index", 0)), "rows_sorted": len(data),
                "column": column, "descending": reverse}
    if mode == "design":
        target = tables[int(params.get("table_index", 0))]
        style = params.get("style")
        tblpr = ensure_child(target, qn("w:tblPr"))
        if style:
            ensure_child(tblpr, qn("w:tblStyle")).set(qn("w:val"), str(style))
        if params.get("shade_header"):
            color = str(params["shade_header"]).lstrip("#").upper()
            rows = target.findall(W_TR)
            if rows:
                for tc in rows[0].findall(W_TC):
                    tcpr = ensure_child(tc, qn("w:tcPr"))
                    shd = ensure_child(tcpr, qn("w:shd"))
                    shd.set(qn("w:val"), "clear")
                    shd.set(qn("w:color"), "auto")
                    shd.set(qn("w:fill"), color)
        doc.mark_dirty(doc.main_part)
        return {"operation": "table", "mode": "design", "style": style,
                "shade_header": params.get("shade_header")}
    if mode == "formula":
        target = tables[int(params.get("table_index", 0))]
        rows = target.findall(W_TR)
        if not rows:
            raise ToolError("NOT_FOUND", "table has no rows")
        skip_header = str(params.get("header_row", "true")).lower() in ("1", "true", "yes")
        data_rows = rows[1:] if skip_header and len(rows) > 1 else rows
        numbers = []
        for row in data_rows:
            for cell in row.findall(W_TC):
                text = para_text(cell).strip().replace(",", "")
                try:
                    numbers.append(float(text))
                except ValueError:
                    continue
        if not numbers:
            raise ToolError("USAGE", "no numeric cells found to total")
        total = sum(numbers)
        total_row = ET.SubElement(target, W_TR)
        trpr = B._sub(total_row, "w:trPr")
        B._sub(trpr, "w:cantSplit")
        width = max((len(r.findall(W_TC)) for r in rows), default=1)
        for index in range(width):
            cell = ET.SubElement(total_row, W_TC)
            B._sub(cell, "w:tcPr")
            para = B._sub(cell, "w:p")
            if index == 0:
                B.add_inline(para, params.get("label") or "Total", doc=doc, base={"bold": True})
            elif index == width - 1:
                B.append_field(para, "=SUM(ABOVE)", display=("%g" % total))
        doc.mark_dirty(doc.main_part)
        return {"operation": "table", "mode": "formula", "sum": round(total, 6),
                "numeric_cells": len(numbers), "total_row_added": True,
                "display_value": "%g" % total}
    raise ToolError("USAGE", "unknown table mode: %r" % mode,
                    {"supported": ["create", "structure", "sort", "design", "formula"]})


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def image(doc, params) -> dict:
    path = params.get("image") or params.get("path")
    if not path:
        raise ToolError("USAGE", "image operation needs --param image=PATH")
    alt = params.get("alt") or params.get("alt_text") or ""
    width = float(params.get("width_cm") or 12.0)
    if not 0.5 <= width <= 60:
        raise ToolError("LIMIT", "image width must be between 0.5 and 60 cm", {"value": width})
    para = B.insert_image(doc, str(path), alt=str(alt), width_cm=width,
                          body=doc.body(), index=_body_insert_index(doc.body()))
    doc.mark_dirty(doc.main_part)
    return {"operation": "image", "path": os.path.basename(str(path)), "alt": alt,
            "width_cm": width, "alt_text_present": bool(alt.strip())}


# --------------------------------------------------------------------------
# header / footer
# --------------------------------------------------------------------------

def set_header_footer(doc, params) -> dict:
    result = {}
    for kind, text_key, num_key in (("header", "header_text", "header_page_numbers"),
                                    ("footer", "footer_text", "footer_page_numbers")):
        text = params.get(text_key)
        numbering = str(params.get(num_key, "false")).lower() in ("1", "true", "yes")
        if text is None and not numbering and not params.get("clear"):
            continue
        part, root, rid = B.ensure_header_footer(doc, kind)
        for child in list(root):
            root.remove(child)
        p = ET.SubElement(root, W_P)
        ppr = B._sub(p, "w:pPr")
        B._sub(ppr, "w:pStyle", val="Header" if kind == "header" else "Footer")
        align = params.get("align") or ("right" if numbering else None)
        if align:
            B._sub(ppr, "w:jc", val="right" if align == "right" else align)
        if text:
            B.add_inline(p, str(text), doc=doc)
        if numbering:
            if text:
                p.append(B.make_run("\t"))
            p.append(B.make_run("Page "))
            B.append_field(p, "PAGE", display="1")
            p.append(B.make_run(" of "))
            B.append_field(p, "NUMPAGES", display="1")
        if not text and not numbering:
            B.add_inline(p, "", doc=doc)
        B.attach_header_footer_refs(doc, header=rid if kind == "header" else None,
                                    footer=rid if kind == "footer" else None)
        doc.mark_dirty(part)
        result[kind] = {"part": part, "text": text, "page_numbers": numbering}
    if not result:
        raise ToolError("USAGE", "header/footer needs --param header_text=..., "
                                 "footer_text=..., footer_page_numbers=true or clear=true")
    return {"operation": "headerfooter", **result}


# --------------------------------------------------------------------------
# watermark
# --------------------------------------------------------------------------

def watermark(doc, params, remove: bool = False) -> dict:
    part, root, rid = B.ensure_header_footer(doc, "header")
    removed = 0
    for p in list(root):
        pict = p.find(".//" + qn("w:pict"))
        if pict is not None and 'PowerPlusWaterMarkObject' in ET.tostring(pict, encoding="unicode"):
            root.remove(p)
            removed += 1
    if remove:
        B.attach_header_footer_refs(doc, header=rid)
        doc.mark_dirty(part)
        if not removed:
            return {"operation": "watermark", "removed": 0,
                    "note": "no tool-generated watermark found"}
        return {"operation": "watermark", "removed": removed}
    text = params.get("text") or params.get("watermark") or doc.cfg["defaults"]["watermark_text"]
    if len(str(text)) > 40:
        raise ToolError("LIMIT", "watermark text is limited to 40 characters")
    color = params.get("color") or doc.cfg["defaults"]["watermark_color"]
    size = int(params.get("size_pt") or 72)
    diagonal = str(params.get("diagonal", "true")).lower() in ("1", "true", "yes")
    para = B.watermark_paragraph(str(text), color=color, size_pt=size, diagonal=diagonal)
    root.append(para)
    B.attach_header_footer_refs(doc, header=rid)
    doc.mark_dirty(part)
    return {"operation": "watermark", "text": str(text), "color": color,
            "size_pt": size, "diagonal": diagonal, "header_part": part,
            "replaced_previous": removed}


# --------------------------------------------------------------------------
# table of contents
# --------------------------------------------------------------------------

def collect_outline(doc, depth: int = 3) -> list[dict]:
    outline = []
    for idx, p in enumerate(iter_paragraphs(doc.document(), include_tables=False)):
        ppr = p.find(W_PPR)
        level = None
        if ppr is not None:
            style_el = ppr.find(qn("w:pStyle"))
            if style_el is not None:
                style = attr(style_el, "w:val") or ""
                match = re.match(r"^(?:Heading|heading|Ttulo|Kop|)(\d)$", style)
                if style.lower().startswith("heading"):
                    match = re.match(r"^heading(\d)$", style.lower())
                if match:
                    level = int(match.group(1))
            outline_el = ppr.find(qn("w:outlineLvl"))
            if outline_el is not None and attr(outline_el, "w:val") is not None:
                level = int(attr(outline_el, "w:val")) + 1
        if level and level <= depth:
            outline.append({"level": level, "text": para_text(p).strip(),
                            "paragraph_index": idx})
    return outline


def toc(doc, params) -> dict:
    depth = int(params.get("depth") or doc.cfg["defaults"]["toc_depth"])
    outline = collect_outline(doc, depth)
    existing = [f for f in _all_fields(doc) if f["type"] == "TOC"]
    if str(params.get("mode", "insert")).lower() in ("insert", "build", "create"):
        heading = B.make_paragraph(params.get("heading") or "Table of Contents",
                                   style="TOCHeading")
        entry = ET.Element(W_P)
        ppr = B._sub(entry, "w:pPr")
        tabs = B._sub(ppr, "w:tabs")
        B._sub(tabs, "w:tab", val="right", leader="dot", pos="9350")
        B.append_field(entry, 'TOC \\o "1-%d" \\h \\z \\u' % depth,
                       display="Right-click and choose Update Field to build the entries.")
        body = doc.body()
        anchor = params.get("anchor") or "end"
        _place(doc, heading, anchor)
        _place(doc, entry, anchor)
        doc.mark_dirty(doc.main_part)
        _mark_update_fields(doc)
        return {"operation": "toc", "mode": "insert", "depth": depth,
                "headings_found": len(outline), "outline": outline[:50],
                "note": "Word builds TOC entries when fields update (F9 / print preview).",
                "existing_toc_fields": len(existing)}
    if str(params.get("mode")).lower() in ("remove", "delete"):
        removed = 0
        body = doc.body()
        for p in list(body.findall(W_P)):
            text = " ".join((el.text or "") for el in p.iter(qn("w:instrText")))
            if text.strip().upper().startswith("TOC"):
                body.remove(p)
                removed += 1
        doc.mark_dirty(doc.main_part)
        return {"operation": "toc", "mode": "remove", "removed": removed}
    return {"operation": "toc", "mode": "outline", "depth": depth,
            "headings_found": len(outline), "outline": outline,
            "existing_toc_fields": len(existing)}


def _all_fields(doc):
    from tools_docx_core import field_codes_from_paragraphs, field_type
    return [{"type": field_type(f["code"]), "code": f["code"], "locked": f["locked"],
             "simple": f["simple"]} for f in field_codes_from_paragraphs(doc.document())]


def _mark_update_fields(doc):
    """Set ``w:updateFields`` so Word refreshes fields when the file opens."""
    settings = doc.settings()
    if settings.find(qn("w:updateFields")) is None:
        el = ET.SubElement(settings, qn("w:updateFields"))
        el.set(qn("w:val"), "true")
        doc.mark_dirty("word/settings.xml")


# --------------------------------------------------------------------------
# form content controls
# --------------------------------------------------------------------------

CONTROL_TYPES = ("text", "plain", "richtext", "rich", "date", "checkbox", "check",
                 "dropdown", "combo", "repeating")


def form(doc, params) -> dict:
    spec = params.get("spec")
    spec_file = params.get("spec_file")
    if spec_file:
        path = os.path.abspath(os.path.expanduser(str(spec_file)))
        if not os.path.isfile(path):
            raise ToolError("NOT_FOUND", "form spec file not found: %s" % spec_file)
        try:
            spec = json.loads(open(path, "r", encoding="utf-8-sig").read())
        except json.JSONDecodeError as exc:
            raise ToolError("USAGE", "form spec is not valid JSON: %s" % exc)
    if isinstance(spec, dict):
        controls = spec.get("controls") or []
        title = spec.get("title")
    elif isinstance(spec, list):
        controls, title = spec, None
    elif params.get("type"):
        controls = [{"type": params.get("type"), "alias": params.get("alias") or "Field",
                     "value": params.get("value"), "items": params.get("items")}]
        title = None
    else:
        raise ToolError("USAGE", "form needs --param spec-file=... or --param type=text")
    if not controls:
        raise ToolError("USAGE", "form spec contains no controls")
    if len(controls) > 200:
        raise ToolError("LIMIT", "a form may define at most 200 controls", {"count": len(controls)})
    blocks = []
    if title:
        blocks.append({"type": "heading", "level": 1, "text": title})
    created = []
    for control in controls:
        if not isinstance(control, dict):
            raise ToolError("USAGE", "each control must be an object")
        if control.get("label"):
            blocks.append({"type": "paragraph", "text": "**%s**" % control["label"]})
        block_spec = dict(control)
        block_spec["control_type"] = block_spec.pop("type", "text")
        blocks.append({"type": "control", "spec": block_spec})
        created.append({"alias": control.get("alias") or control.get("label"),
                        "type": control.get("type", "text")})
    result = insert_blocks(doc, blocks, params)
    if str(params.get("protect", "false")).lower() in ("1", "true", "yes"):
        from tools_docx_edit import apply_protection
        apply_protection(doc, {"mode": "forms"})
    return {"operation": "form", "controls": created, "blocks": result}


# --------------------------------------------------------------------------
# lists and numbering
# --------------------------------------------------------------------------

def lists(doc, params) -> dict:
    """Inspect, create, restyle or restart bullet/numbered lists."""
    mode = str(params.get("mode") or "insert").lower()
    if mode in ("report", "list", "inspect"):
        entries = []
        for idx, p in enumerate(iter_paragraphs(doc.document(), include_tables=False)):
            ppr = p.find(W_PPR)
            if ppr is None:
                continue
            numpr = ppr.find(qn("w:numPr"))
            if numpr is None:
                continue
            entries.append({
                "paragraph_index": idx,
                "text": para_text(p)[:120],
                "num_id": attr(numpr.find(qn("w:numId")), "w:val"),
                "level": int(attr(numpr.find(qn("w:ilvl")), "w:val") or 0),
            })
        numbering = doc.numbering(create=False)
        definitions = []
        if numbering is not None:
            for abstract in numbering.findall(qn("w:abstractNum")):
                formats = []
                for level in abstract.findall(qn("w:lvl")):
                    fmt = attr(level.find(qn("w:numFmt")), "w:val")
                    text = attr(level.find(qn("w:lvlText")), "w:val")
                    formats.append("%s %r" % (fmt, text))
                definitions.append({"abstract_num_id": attr(abstract, "w:abstractNumId"),
                                    "levels": formats[:3]})
        counts = {}
        for entry in entries:
            counts[entry["num_id"]] = counts.get(entry["num_id"], 0) + 1
        return {"operation": "list", "mode": "report", "list_paragraphs": len(entries),
                "by_num_id": counts, "definitions": definitions,
                "entries": entries[:100]}
    if mode in ("insert", "create"):
        items = params.get("items")
        if isinstance(items, str):
            items = [part.strip() for part in re.split(r"[;\n]", items) if part.strip()]
        if not items:
            raise ToolError("USAGE", 'list needs --param items="first;second"')
        numbering = B.ensure_numbering(doc)
        ordered = str(params.get("ordered", "false")).lower() in ("1", "true", "yes")
        level = int(params.get("level") or 0)
        if not 0 <= level <= 2:
            raise ToolError("LIMIT", "list level must be 0, 1 or 2", {"level": level})
        num_id = numbering["decimal" if ordered else "bullet"]
        body = doc.body()
        for item in items:
            p = ET.Element(W_P)
            ppr = B._sub(p, "w:pPr")
            B._sub(ppr, "w:pStyle", val="ListParagraph")
            numpr = B._sub(ppr, "w:numPr")
            B._sub(numpr, "w:ilvl", val=level)
            B._sub(numpr, "w:numId", val=num_id)
            B.add_inline(p, str(item), doc=doc)
            _insert_block(body, p)
        doc.mark_dirty(doc.main_part)
        return {"operation": "list", "mode": "insert", "items": len(items),
                "ordered": ordered, "level": level, "num_id": num_id}
    if mode in ("restart", "continue", "renumber"):
        restart = mode == "restart"
        numbering = doc.numbering(create=True)
        num_id = str(params.get("num_id") or 1)
        target = None
        for num in numbering.findall(qn("w:num")):
            if attr(num, "w:numId") == num_id:
                target = num
                break
        if target is None:
            raise ToolError("NOT_FOUND", "no numbering instance with numId %s" % num_id,
                            {"hint": "list --param mode=report to see num_id values"})
        for override in list(target.findall(qn("w:lvlOverride"))):
            target.remove(override)
        if restart:
            override = ET.SubElement(target, qn("w:lvlOverride"))
            override.set(qn("w:ilvl"), str(int(params.get("level") or 0)))
            B._sub(override, "w:startOverride", val=1)
        doc.mark_dirty("word/numbering.xml")
        return {"operation": "list", "mode": mode, "num_id": num_id,
                "start_override": bool(restart)}
    if mode in ("style", "apply"):
        style_id = params.get("style") or "ListParagraph"
        ordered = str(params.get("ordered", "true")).lower() in ("1", "true", "yes")
        numbering = B.ensure_numbering(doc)
        num_id = numbering["decimal" if ordered else "bullet"]
        match = params.get("match")
        touched = 0
        for p in iter_paragraphs(doc.document(), include_tables=False):
            if match and match.lower() not in para_text(p).lower():
                continue
            ppr = ensure_child(p, W_PPR)
            numpr = ensure_child(ppr, qn("w:numPr"))
            ensure_child(numpr, qn("w:ilvl")).set(qn("w:val"), str(int(params.get("level") or 0)))
            ensure_child(numpr, qn("w:numId")).set(qn("w:val"), str(num_id))
            pstyle = ppr.find(qn("w:pStyle"))
            if pstyle is None:
                pstyle = ET.Element(qn("w:pStyle"))
                ppr.insert(0, pstyle)
            pstyle.set(qn("w:val"), style_id)
            touched += 1
        doc.mark_dirty(doc.main_part)
        return {"operation": "list", "mode": "style", "paragraphs_styled": touched,
                "num_id": num_id, "style": style_id}
    raise ToolError("USAGE", "unknown list mode: %r" % mode,
                    {"supported": ["report", "insert", "restart", "continue", "style"]})
