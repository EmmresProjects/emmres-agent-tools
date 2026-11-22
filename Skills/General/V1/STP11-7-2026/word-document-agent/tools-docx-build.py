#!/usr/bin/env python3
"""Document construction layer for the word-document-agent skill tool.

Builds a valid Word package from scratch and appends native Word
structures (paragraphs, headings, tables, lists, fields, TOC, images,
content controls, headers/footers, watermarks) to an existing package.

The module is imported by ``tools-word-doc-agent.py`` through
``importlib`` (hyphenated filename) as ``tools_docx_build``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import struct
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


EMU_PER_CM = 360000
EMU_PER_INCH = 914400
TWIPS_PER_CM = 567
TWIPS_PER_INCH = 1440

HEADING_STYLE_IDS = ["Heading1", "Heading2", "Heading3", "Heading4", "Heading5", "Heading6"]


# --------------------------------------------------------------------------
# Low-level element builders
# --------------------------------------------------------------------------

def _sub(parent, tag, **attrs):
    """Create a child element and set attributes.

    Unprefixed attribute names are namespace-qualified with ``w:`` only for
    elements in the WordprocessingML namespace; DrawingML/VML elements take
    their attributes literally (``cx``, ``descr``, ``prst``).
    """
    el = ET.SubElement(parent, qn(tag))
    word_element = tag.startswith("w:")
    for key, value in attrs.items():
        if value is None:
            continue
        if ":" in key:
            el.set(qn(key), str(value))
        elif word_element:
            el.set(qn("w:" + key), str(value))
        else:
            el.set(key, str(value))
    return el


def make_run(text: str = "", bold=False, italic=False, underline=False, code=False,
             size_half_points=None, color=None, strike=False, superscript=False,
             lang=None):
    r = ET.Element(W_R)
    rpr = None
    if any([bold, italic, underline, code, size_half_points, color, strike, superscript, lang]):
        rpr = _sub(r, "w:rPr")
        if code:
            _sub(rpr, "w:rStyle", val="VerbatimChar")
            _sub(rpr, "w:rFonts", ascii="Consolas", hAnsi="Consolas")
        if bold:
            _sub(rpr, "w:b")
        if italic:
            _sub(rpr, "w:i")
        if strike:
            _sub(rpr, "w:strike")
        if underline:
            _sub(rpr, "w:u", val="single")
        if color:
            _sub(rpr, "w:color", val=color.lstrip("#").upper())
        if size_half_points:
            _sub(rpr, "w:sz", val=size_half_points)
            _sub(rpr, "w:szCs", val=size_half_points)
        if superscript:
            _sub(rpr, "w:vertAlign", val="superscript")
        if lang:
            _sub(rpr, "w:lang", val=lang)
    t = _sub(r, "w:t")
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(qn("xml:space"), "preserve")
    return r


def make_hyperlink_run(rid: str, text: str, anchor: str | None = None):
    link = ET.Element(qn("w:hyperlink"))
    if anchor:
        link.set(qn("w:anchor"), anchor)
    else:
        link.set(qn("r:id"), rid)
    link.set(qn("w:history"), "1")
    r = ET.SubElement(link, W_R)
    rpr = _sub(r, "w:rPr")
    _sub(rpr, "w:rStyle", val="Hyperlink")
    t = _sub(r, "w:t")
    t.text = text
    return link


def make_paragraph(text: str = "", style: str | None = None, align: str | None = None,
                   num_id: int | None = None, ilvl: int = 0, keep_next=False,
                   page_break_before=False, space_after_twips=None, indent_twips=None,
                   outline_level=None):
    p = ET.Element(W_P)
    ppr = _sub(p, "w:pPr")
    if style:
        _sub(ppr, "w:pStyle", val=style)
    if num_id is not None:
        numpr = _sub(ppr, "w:numPr")
        _sub(numpr, "w:ilvl", val=ilvl)
        _sub(numpr, "w:numId", val=num_id)
    if align:
        _sub(ppr, "w:jc", val=align)
    if keep_next:
        _sub(ppr, "w:keepNext")
    if page_break_before:
        _sub(ppr, "w:pageBreakBefore")
    if space_after_twips is not None:
        _sub(ppr, "w:spacing", after=space_after_twips)
    if indent_twips is not None:
        _sub(ppr, "w:ind", left=indent_twips)
    if outline_level is not None:
        _sub(ppr, "w:outlineLvl", val=outline_level)
    if text:
        p.append(make_run(text))
    return p


def make_page_break_paragraph():
    p = ET.Element(W_P)
    r = _sub(p, "w:r")
    _sub(r, "w:br", type="page")
    return p


def make_field_code(code: str, display: str = "", locked: bool = False) -> list:
    """Complex field: ``begin`` + ``instrText`` + ``separate`` + result + ``end``."""
    runs = []
    begin_run = ET.Element(W_R)
    fc = _sub(begin_run, "w:fldChar", fldCharType="begin")
    if locked:
        fc.set(qn("w:fldLock"), "true")
    runs.append(begin_run)

    instr_run = ET.Element(W_R)
    it = _sub(instr_run, "w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = " %s " % code.strip()
    runs.append(instr_run)

    sep_run = ET.Element(W_R)
    _sub(sep_run, "w:fldChar", fldCharType="separate")
    runs.append(sep_run)

    res_run = make_run(display or "1")
    runs.append(res_run)

    end_run = ET.Element(W_R)
    _sub(end_run, "w:fldChar", fldCharType="end")
    runs.append(end_run)
    return runs


def append_field(p, code: str, display: str = "", locked: bool = False):
    for run in make_field_code(code, display, locked):
        p.append(run)
    return p


def make_simple_field(code: str, display: str = "", locked: bool = False):
    el = ET.Element(W_FLDSIMPLE)
    el.set(qn("w:instr"), code)
    if locked:
        el.set(qn("w:fldLock"), "true")
    el.append(make_run(display or "1"))
    return el


# --------------------------------------------------------------------------
# Inline markup (**bold**, *italic*, `code`, [text](url))
# --------------------------------------------------------------------------

INLINE_RE = re.compile(
    r"(\*\*(?P<bold>[^*]+)\*\*)|(\*(?P<italic>[^*]+)\*)|(`(?P<code>[^`]+)`)"
    r"|(\[(?P<link>[^\]]+)\]\((?P<href>[^)\s]+)\))"
)


def add_inline(parent, text: str, doc=None, base=None):
    """Append runs to ``parent`` for a string containing light markup."""
    base = base or {}
    pos = 0
    for match in INLINE_RE.finditer(text):
        if match.start() > pos:
            parent.append(make_run(text[pos:match.start()], **base))
        if match.group("bold") is not None:
            parent.append(make_run(match.group("bold"), bold=True, **base))
        elif match.group("italic") is not None:
            parent.append(make_run(match.group("italic"), italic=True, **base))
        elif match.group("code") is not None:
            parent.append(make_run(match.group("code"), code=True, **base))
        else:
            label, href = match.group("link"), match.group("href")
            if doc is not None:
                if href.startswith("#"):
                    parent.append(make_hyperlink_run("", label, anchor=href[1:]))
                else:
                    rid = doc.add_relationship(doc.main_part, HYPERLINK_REL, href, "External")
                    parent.append(make_hyperlink_run(rid, label))
            else:
                parent.append(make_run(label))
        pos = match.end()
    if pos < len(text):
        parent.append(make_run(text[pos:], **base))
    return parent


HYPERLINK_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


# --------------------------------------------------------------------------
# Markdown / JSON content parsing
# --------------------------------------------------------------------------

def parse_markdown(text: str) -> list[dict]:
    """Very small Markdown subset -> block list.

    Supported: ``#`` headings, paragraphs, ``-``/``*`` bullets, ``1.``
    ordered lists, ``>`` quotes, fenced code, ``---`` rules, pipe tables.
    """
    blocks: list[dict] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    paragraph: list[str] = []

    def flush():
        if paragraph:
            blocks.append({"type": "paragraph", "text": " ".join(paragraph).strip()})
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            fence = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            blocks.append({"type": "code", "text": "\n".join(body), "lang": fence or None})
            continue
        if not stripped:
            flush()
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            blocks.append({"type": "heading", "level": len(heading.group(1)),
                           "text": heading.group(2).strip()})
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush()
            blocks.append({"type": "pagebreak"})
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(quote)})
            continue
        if re.match(r"^[-*+]\s+", stripped):
            flush()
            items = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"type": "list", "ordered": False, "items": items})
            continue
        if re.match(r"^\d+[.)]\s+", stripped):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"type": "list", "ordered": True, "items": items})
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append({"type": "table", "headers": rows[0], "rows": rows[1:]})
            continue
        paragraph.append(stripped)
        i += 1
    flush()
    return blocks


def normalize_blocks(spec) -> list[dict]:
    """Accept a block list, ``{"blocks": [...]}`` or a markdown string."""
    if isinstance(spec, str):
        return parse_markdown(spec)
    if isinstance(spec, dict):
        if "blocks" in spec:
            return list(spec["blocks"])
        if "paragraphs" in spec:
            return [{"type": "paragraph", "text": t} for t in spec["paragraphs"]]
        if "text" in spec:
            return parse_markdown(spec["text"])
        raise ToolError("USAGE", "content object needs 'blocks', 'paragraphs' or 'text'")
    if isinstance(spec, list):
        return [
            {"type": "paragraph", "text": item} if isinstance(item, str) else dict(item)
            for item in spec
        ]
    raise ToolError("USAGE", "content must be a block list, object or markdown string")


# --------------------------------------------------------------------------
# Numbering
# --------------------------------------------------------------------------

def ensure_numbering(doc) -> dict:
    """Create bullet (numId 1) and decimal (numId 2) list definitions."""
    numbering = doc.numbering(create=True)
    existing_abstract = {attr(el, "w:abstractNumId") for el in numbering.findall(qn("w:abstractNum"))}
    existing_num = {attr(el, "w:numId") for el in numbering.findall(qn("w:num"))}
    made = {}
    for want_id, fmt, text, num_id in ((0, "bullet", "\u2022", "1"), (1, "decimal", "%1.", "2")):
        aid = str(want_id)
        if aid not in existing_abstract:
            abstract = ET.SubElement(numbering, qn("w:abstractNum"))
            abstract.set(qn("w:abstractNumId"), aid)
            _sub(abstract, "w:multiLevelType", val="multilevel")
            for lvl in range(3):
                level = _sub(abstract, "w:lvl", ilvl=lvl)
                _sub(level, "w:start", val="1")
                _sub(level, "w:numFmt", val=fmt)
                _sub(level, "w:lvlText", val=text if lvl == 0 else ("\u25E6" if fmt == "bullet" else "%%%d." % (lvl + 1)))
                _sub(level, "w:lvlJc", val="left")
                ppr = _sub(level, "w:pPr")
                _sub(ppr, "w:ind", left=str(720 * (lvl + 1)), hanging="360")
        if num_id not in existing_num:
            num = ET.SubElement(numbering, qn("w:num"))
            num.set(qn("w:numId"), num_id)
            _sub(num, "w:abstractNumId", val=aid)
        made[fmt] = int(num_id)
    doc.mark_dirty("word/numbering.xml")
    return made


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def make_table(headers: list[str], rows: list[list[str]], style: str = "TableGrid",
               repeat_header: bool = True, widths_cm: list[float] | None = None,
               first_col_bold: bool = False, doc=None):
    tbl = ET.Element(W_TBL)
    tblpr = _sub(tbl, "w:tblPr")
    _sub(tblpr, "w:tblStyle", val=style)
    _sub(tblpr, "w:tblW", w="5000", type="pct")
    _sub(tblpr, "w:tblLook", val="04A0", firstRow="1", lastRow="0", firstColumn="1",
         lastColumn="0", noHBand="0", noVBand="1")
    col_count = len(headers) if headers else max((len(r) for r in rows), default=1) or 1
    grid = _sub(tbl, "w:tblGrid")
    for idx in range(col_count):
        width = int((widths_cm[idx] * TWIPS_PER_CM) if widths_cm and idx < len(widths_cm) else 0)
        _sub(grid, "w:gridCol", w=width or None)

    def add_row(values, header=False):
        tr = ET.SubElement(tbl, W_TR)
        trpr = _sub(tr, "w:trPr")
        if header and repeat_header:
            _sub(trpr, "w:tblHeader")
        _sub(trpr, "w:cantSplit")
        for idx in range(col_count):
            tc = ET.SubElement(tr, W_TC)
            tcpr = _sub(tc, "w:tcPr")
            if widths_cm and idx < len(widths_cm):
                _sub(tcpr, "w:tcW", w=int(widths_cm[idx] * TWIPS_PER_CM), type="dxa")
            value = values[idx] if idx < len(values) else ""
            p = _sub(tc, "w:p")
            if header:
                add_inline(p, str(value), doc=doc, base={"bold": True})
            else:
                add_inline(p, str(value), doc=doc,
                           base={"bold": True} if (first_col_bold and idx == 0) else {})

    if headers:
        add_row([str(h) for h in headers], header=True)
    for row in rows:
        add_row([str(c) for c in row])
    if not headers and not rows:
        add_row([""])
    return tbl


def set_table_header_repeat(tbl, repeat: bool = True):
    for idx, tr in enumerate(tbl.findall(W_TR)):
        trpr = ensure_child(tr, qn("w:trPr"))
        existing = trpr.find(qn("w:tblHeader"))
        if repeat and idx == 0 and existing is None:
            ET.SubElement(trpr, qn("w:tblHeader"))
        elif not repeat and existing is not None:
            trpr.remove(existing)


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def image_size_px(data: bytes):
    """Return (width_px, height_px, dpi_guess) for PNG/JPEG/GIF, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return width, height, 96
    if data[:2] == b"\xff\xd8":
        idx = 2
        while idx < len(data) - 9:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                          0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[idx + 5: idx + 9])
                return width, height, 96
            seg_len = struct.unpack(">H", data[idx + 2: idx + 4])[0]
            idx += 2 + seg_len
        return None
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return width, height, 96
    return None


def insert_image(doc, image_path: str, alt: str = "", width_cm: float = 12.0,
                 body=None, index: int | None = None):
    """Embed an image as a ``w:drawing`` paragraph with alt text."""
    if not os.path.isfile(image_path):
        raise ToolError("NOT_FOUND", "image file not found: %s" % image_path,
                        {"path": image_path})
    data = open(image_path, "rb").read()
    ext = os.path.splitext(image_path)[1].lstrip(".").lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
        raise ToolError("USAGE", "unsupported image extension: .%s (png, jpg, gif, bmp)" % ext)
    dims = image_size_px(data)
    if not dims:
        raise ToolError("USAGE", "could not read image dimensions: %s" % image_path)
    px_w, px_h, dpi = dims
    if not px_w or not px_h:
        raise ToolError("INTEGRITY", "image has zero dimensions: %s" % image_path)
    media_name = "image_%s.%s" % (re.sub(r"[^a-z0-9]", "", os.path.basename(image_path).lower())[:24] or "media", ext)
    part = "word/media/%s" % media_name
    n = 1
    while doc.has(part):
        n += 1
        part = "word/media/%s_%d.%s" % (media_name.rsplit(".", 1)[0], n, ext)
    content_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "bmp": "image/bmp"}[ext]
    doc.add_bytes(part, data, content_type)
    rid = doc.add_relationship(doc.main_part, REL_IMAGE, part.replace("word/", "", 1)
                               if doc.main_part.startswith("word/") else part)
    cy = int(round(width_cm * EMU_PER_CM))
    cx = int(round(cy * (px_w / float(px_h))))
    p = ET.Element(W_P)
    ppr = _sub(p, "w:pPr")
    _sub(ppr, "w:jc", val="center")
    run = ET.SubElement(p, W_R)
    drawing = ET.SubElement(run, W_DRAWING)
    inline = _sub(drawing, "wp:inline", distT=0, distB=0, distL=0, distR=0)
    _sub(inline, "wp:extent", cx=cx, cy=cy)
    _sub(inline, "wp:effectExtent", l=0, t=0, r=0, b=0)
    _sub(inline, "wp:docPr", id="1", name="Picture 1", descr=alt or "Embedded image")
    frame = _sub(inline, "wp:cNvGraphicFramePr")
    _sub(frame, "a:graphicFrameLocks", noChangeAspect="1")
    graphic = _sub(inline, "a:graphic")
    gdata = _sub(graphic, "a:graphicData", uri="http://schemas.openxmlformats.org/drawingml/2006/picture")
    pic = _sub(gdata, "pic:pic")
    nvpic = _sub(pic, "pic:nvPicPr")
    _sub(nvpic, "pic:cNvPr", id="1", name=media_name, descr=alt or "Embedded image")
    _sub(nvpic, "pic:cNvPicPr")
    fill = _sub(pic, "pic:blipFill")
    _sub(fill, "a:blip", **{"r:embed": rid})
    stretch = _sub(fill, "a:stretch")
    _sub(stretch, "a:fillRect")
    sp = _sub(pic, "pic:spPr")
    xfrm = _sub(sp, "a:xfrm")
    _sub(xfrm, "a:off", x=0, y=0)
    _sub(xfrm, "a:ext", cx=cx, cy=cy)
    _sub(sp, "a:prstGeom", prst="rect")
    if body is not None:
        if index is None:
            body.append(p)
        else:
            body.insert(index, p)
    return p


# --------------------------------------------------------------------------
# Content controls and watermarks
# --------------------------------------------------------------------------

def make_content_control(spec: dict, doc=None):
    """Build a ``w:sdt`` from a control spec (forms category)."""
    kind = str(spec.get("control_type") or spec.get("type") or "text").lower()
    alias = spec.get("alias") or spec.get("name") or "Field"
    tag = spec.get("tag") or re.sub(r"\W+", "", alias) or "field"
    sdt = ET.Element(W_SDT)
    pr = _sub(sdt, "w:sdtPr")
    _sub(pr, "w:alias", val=alias)
    _sub(pr, "w:tag", val=tag)
    content = ET.SubElement(sdt, W_SDTCONTENT)
    value = spec.get("value") or ""
    if kind in ("text", "plain", "richtext", "rich"):
        if kind in ("richtext", "rich"):
            _sub(pr, "w:richText")
        else:
            _sub(pr, "w:text")
        p = ET.SubElement(content, W_P)
        add_inline(p, str(value), doc=doc)
        placeholder = spec.get("placeholder")
        if placeholder:
            _sub(pr, "w:showingPlcHdr")
    elif kind == "date":
        date = _sub(pr, "w:date")
        date.set(qn("w:fullDate"), spec.get("full_date") or "2026-01-01T00:00:00Z")
        _sub(date, "w:dateFormat", val=spec.get("format") or "yyyy-MM-dd")
        _sub(date, "w:lid", val=spec.get("lang") or "en-US")
        p = ET.SubElement(content, W_P)
        add_inline(p, str(value or "Click to pick a date"), doc=doc)
    elif kind in ("checkbox", "check"):
        _sub(pr, "w14:checkbox")
        box = ET.SubElement(content, W_P)
        r = _sub(box, "w:r")
        _sub(r, "w:t").text = "\u2612" if str(spec.get("checked")).lower() in ("1", "true", "yes") else "\u2610"
    elif kind in ("dropdown", "combo", "drop-down"):
        el = _sub(pr, "w:combo" if kind == "combo" else "w:dropDownList")
        for item in spec.get("items") or []:
            _sub(el, "w:listItem", displayText=str(item), value=str(item))
        p = ET.SubElement(content, W_P)
        add_inline(p, str(value or (spec.get("items") or [""])[0]), doc=doc)
    elif kind in ("repeating", "repeating-section"):
        p = ET.SubElement(content, W_P)
        add_inline(p, str(value or alias), doc=doc)
    else:
        raise ToolError("USAGE", "unknown content control type: %s" % kind,
                        {"supported": ["text", "richtext", "date", "checkbox",
                                       "dropdown", "combo", "repeating"]})
    return sdt


def watermark_paragraph(text: str, color: str = "#C0C0C0", font: str = "Calibri",
                        size_pt: int = 72, diagonal: bool = True, opacity: str = ".5"):
    """A classic Word watermark: a shape with WordArt text inside the header."""
    escaped = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rotation = 315 if diagonal else 0
    xml = (
        '<w:p xmlns:w="%s" xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word">'
        '<w:pPr><w:pStyle w:val="Header"/></w:pPr>'
        '<w:r><w:rPr><w:noProof/></w:rPr><w:pict>'
        '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" path="m@7,l@8,m@5,21600l@6,21600e">'
        '<v:formulas><v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
        '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
        '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
        '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/>'
        '<v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
        '<v:path textpathok="t" o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" '
        'o:connectangles="270,180,90,0"/><v:textpath on="t" fitshape="t"/>'
        '<v:handles><v:h position="#0,bottomRight" xrange="6629,14971"/></v:handles>'
        '<o:lock v:ext="edit" text="t" shapetype="t"/></v:shapetype>'
        '<v:shape id="PowerPlusWaterMarkObject" o:spid="_x0000_s2049" type="#_x0000_t136" '
        'style="position:absolute;margin-left:0;margin-top:0;width:468pt;height:117pt;'
        'rotation:%d;z-index:-251658752;mso-position-horizontal:center;'
        'mso-position-horizontal-relative:margin;mso-position-vertical:center;'
        'mso-position-vertical-relative:margin" o:allowincell="f" fillcolor="%s" stroked="f">'
        '<v:fill opacity="%s"/><v:textpath style="font-family:&quot;%s&quot;;font-size:%dpt" '
        'string="%s"/></v:shape></w:pict></w:r></w:p>'
        % (NS["w"], rotation, color, opacity, font, size_pt, escaped)
    )
    return ET.fromstring(xml)


# --------------------------------------------------------------------------
# Package skeletons and styles
# --------------------------------------------------------------------------

def _styles_xml():
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:styles xmlns:w="%s">' % NS["w"],
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="en-US" w:bidi="ar-SA"/>'
        '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
        '<w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>',
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>',
        '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
        '<w:name w:val="Default Paragraph Font"/><w:semiHidden/></w:style>',
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="120"/><w:contextualSpacing/>'
        '</w:pPr><w:rPr><w:sz w:val="56"/><w:szCs w:val="56"/></w:rPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/>'
        '<w:qFormat/><w:rPr><w:color w:val="595959"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>',
    ]
    sizes = {1: 32, 2: 28, 3: 26, 4: 24, 5: 22, 6: 22}
    for level in range(1, 7):
        parts.append(
            '<w:style w:type="paragraph" w:styleId="Heading%d"><w:name w:val="heading %d"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="%d" w:after="80"/>'
            '<w:outlineLvl w:val="%d"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr></w:style>'
            % (level, level, 240 if level <= 2 else 160, level - 1, sizes[level], sizes[level])
        )
    parts.extend([
        '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
        '<w:qFormat/><w:pPr><w:ind w:left="720"/><w:spacing w:before="120" w:after="120"/></w:pPr>'
        '<w:rPr><w:i/><w:color w:val="404040"/></w:rPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720"/></w:pPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="200"/></w:pPr>'
        '<w:rPr><w:i/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="Header"><w:name w:val="header"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:tabs><w:tab w:val="center" w:pos="4680"/><w:tab w:val="right" w:pos="9360"/></w:tabs></w:pPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:tabs><w:tab w:val="center" w:pos="4680"/><w:tab w:val="right" w:pos="9360"/></w:tabs></w:pPr></w:style>',
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>',
        '<w:style w:type="character" w:styleId="VerbatimChar"><w:name w:val="Verbatim Char"/>'
        '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr></w:style>',
        '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
        '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/>'
        '<w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
        '<w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>',
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
        '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '</w:tblBorders></w:tblPr></w:style>',
        '<w:style w:type="paragraph" w:styleId="TOCHeading"><w:name w:val="TOC Heading"/>'
        '<w:basedOn w:val="Heading1"/><w:next w:val="Normal"/></w:style>',
    ])
    parts.append('</w:styles>')
    return "".join(parts).encode("utf-8")


CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties xmlns:cp="%s" xmlns:dc="%s" xmlns:dcterms="%s" '
    'xmlns:dcmitype="%s" xmlns:xsi="%s">'
    '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>'
    '<cp:revision>1</cp:revision>'
    '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
    '</cp:coreProperties>'
)

APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="%s" xmlns:vt="%s">'
    '<Application>word-document-agent</Application><DocSecurity>0</DocSecurity>'
    '<AppVersion>16.0000</AppVersion></Properties>'
)


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def new_blank(cfg, fmt: str = "docx", title: str = "Untitled document",
              author: str = "word-document-agent", now: str | None = None):
    """Create a minimal but valid Word package in memory."""
    import datetime

    stamp = now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    main_ct = {"docx": CT_MAIN, "docm": CT_MAIN_MACRO, "dotx": CT_TEMPLATE,
               "dotm": CT_TEMPLATE_MACRO}[fmt]
    entries = {}
    entries["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="%s">'
        '<Default Extension="rels" ContentType="%s"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Default Extension="gif" ContentType="image/gif"/>'
        '<Default Extension="bmp" ContentType="image/bmp"/>'
        '<Override PartName="/word/document.xml" ContentType="%s"/>'
        '<Override PartName="/word/styles.xml" ContentType="%s"/>'
        '<Override PartName="/word/settings.xml" ContentType="%s"/>'
        '<Override PartName="/docProps/core.xml" ContentType="%s"/>'
        '<Override PartName="/docProps/app.xml" ContentType="%s"/>'
        '</Types>' % (NS["ct"], CT_RELATIONSHIPS, main_ct, CT_STYLES, CT_SETTINGS, CT_CORE, CT_APP)
    ).encode("utf-8")
    entries["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>' % (NS["pr"], "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument")
    ).encode("utf-8")
    entries["word/_rels/document.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="%s" Target="settings.xml"/>'
        '</Relationships>' % (NS["pr"], "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
                              "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings")
    ).encode("utf-8")
    entries["word/document.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:wp="%s" xmlns:a="%s" xmlns:pic="%s" '
        'xmlns:v="%s" xmlns:m="%s" xmlns:w14="%s">'
        '<w:body><w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" '
        'w:footer="720" w:gutter="0"/><w:cols w:space="708"/><w:docGrid w:linePitch="360"/>'
        '</w:sectPr></w:body></w:document>'
        % (NS["w"], NS["r"], NS["wp"], NS["a"], NS["pic"], NS["v"], NS["m"], NS["w14"])
    ).encode("utf-8")
    entries["word/styles.xml"] = _styles_xml()
    entries["word/settings.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:settings xmlns:w="%s"><w:zoom w:percent="100"/>'
        '<w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/>'
        '<w:compat><w:compatSetting w:name="compatibilityMode" '
        'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
        '</w:settings>' % NS["w"]
    ).encode("utf-8")
    entries["docProps/core.xml"] = (CORE_XML % (
        NS["cp"], NS["dc"], NS["dcterms"], NS["dcmitype"], NS["xsi"],
        _esc(title), _esc(author), _esc(author), stamp, stamp)).encode("utf-8")
    entries["docProps/app.xml"] = (APP_XML % (NS["ep"], NS["vt"])).encode("utf-8")
    doc = C.Docx.from_entries(entries, cfg)
    return doc


def ensure_core_part(doc):
    if not doc.has("docProps/core.xml"):
        import datetime

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = (CORE_XML % (NS["cp"], NS["dc"], NS["dcterms"], NS["dcmitype"], NS["xsi"],
                            "Untitled document", "", "", stamp, stamp)).encode("utf-8")
        doc.add_bytes("docProps/core.xml", data, CT_CORE)
    return doc.xml("docProps/core.xml")


def ensure_app_part(doc):
    if not doc.has("docProps/app.xml"):
        doc.add_bytes("docProps/app.xml", (APP_XML % (NS["ep"], NS["vt"])).encode("utf-8"), CT_APP)
    return doc.xml("docProps/app.xml")


# --------------------------------------------------------------------------
# Header / footer
# --------------------------------------------------------------------------

def ensure_header_footer(doc, kind: str = "header"):
    """Return ``(part_name, root, rel_id)`` creating the part if needed."""
    rel_type = REL_HEADER if kind == "header" else REL_FOOTER
    content_type = CT_HEADER if kind == "header" else CT_FOOTER
    for rid, info in doc.rels(doc.main_part).items():
        if info["type"] == rel_type:
            return info["part"], doc.xml(info["part"]), rid
    n = 1
    while doc.has("word/%s%d.xml" % (kind, n)):
        n += 1
    part = "word/%s%d.xml" % (kind, n)
    root = ET.Element(qn("w:hdr") if kind == "header" else qn("w:ftr"))
    p = _sub(root, "w:p")
    _sub(p, "w:pPr")
    doc.add_xml(part, root, content_type)
    rid = doc.add_relationship(doc.main_part, rel_type, "%s%d.xml" % (kind, n))
    return part, root, rid


def attach_header_footer_refs(doc, section_index: int | None = None,
                              header=None, footer=None):
    """Point ``sectPr`` header/footer references at the created parts."""
    body = doc.body()
    sect_prs = [el for el in doc.document().iter(W_SECTPR)]
    if not sect_prs:
        raise ToolError("INTEGRITY", "document has no section properties")
    target = sect_prs[-1] if section_index is None else sect_prs[section_index]
    for kind, rid in (("header", header), ("footer", footer)):
        if not rid:
            continue
        tag = qn("w:headerReference") if kind == "header" else qn("w:footerReference")
        existing = target.find(tag)
        if existing is not None:
            target.remove(existing)
        el = ET.Element(tag)
        el.set(qn("w:type"), "default")
        el.set(qn("r:id"), rid)
        target.insert(0, el)
    doc.mark_dirty(doc.main_part)
    return body
