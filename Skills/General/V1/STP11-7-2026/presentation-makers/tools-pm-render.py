#!/usr/bin/env python3
"""tools-pm-render.py - layout engine and format renderers.

Load order: after tools-pm-core.py, tools-pm-intake.py and tools-pm-plan.py;
before tools-pm-audit.py and tools-pm-package.py.

One deterministic layout engine produces a neutral render model
(list of frames -> paragraphs). Four renderers consume it:

    render_pptx()      Office Open XML .pptx with speaker notes (stdlib zipfile)
    render_html()      one self-contained responsive file, inline CSS/JS, no CDN
    render_pdf()       text-and-shape PDF handout (hand-written PDF writer)
    render_markdown()  handout.md, speaker-notes.md, outline.md
    render_srt()       caption file from speaker notes and slide timing

Nothing here needs a third-party package, a network connection, a font file,
or an external converter.
"""

from __future__ import annotations

import html
import importlib.util
import os
import re
import sys
import zipfile
import zlib

for _name, _file in (("pm_core", "tools-pm-core.py"), ("pm_intake", "tools-pm-intake.py"),
                     ("pm_plan", "tools-pm-plan.py")):
    if _name not in sys.modules:  # pragma: no cover - import shim
        _dir = os.path.dirname(os.path.abspath(__file__))
        _spec = importlib.util.spec_from_file_location(_name, os.path.join(_dir, _file))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
import pm_core  # noqa: E402
import pm_plan  # noqa: E402

EMU_PER_INCH = 914400
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
MIN_BODY_PT = 12.0


# ==========================================================================
# Layout engine
# ==========================================================================
def _frame(kind, x, y, w, h, paragraphs=None, fill=None, stroke=None, radius=0.0, name=None,
           ph=None, heading_level=None):
    return {
        "kind": kind,
        "x": round(x, 3), "y": round(y, 3), "w": round(max(0.05, w), 3), "h": round(max(0.05, h), 3),
        "paragraphs": paragraphs or [],
        "fill": fill, "stroke": stroke, "radius": radius,
        "name": name or kind,
        "ph": ph, "heading_level": heading_level,
    }


def _para(text, size, color, bold=False, italic=False, align="left", space_after=4, bullet=False):
    return {"text": text, "size": float(size), "color": color, "bold": bold, "italic": italic,
            "align": align, "space_after": space_after, "bullet": bullet}


def _fit_size(paragraphs, width_in, height_in, max_pt, min_pt=MIN_BODY_PT):
    """Shrink type until the block fits its box."""
    size = float(max_pt)
    while size > min_pt:
        probe = [dict(p, size=size) for p in paragraphs]
        needed = 0.0
        for para in probe:
            prefix = 0.06 if para.get("bullet") else 0.0
            needed += pm_core.text_height_in(para["text"], width_in - 0.12 - prefix, size) + para.get("space_after", 4) / 72.0
        if needed <= height_in:
            return size
        size -= 1.0
    return min_pt


def layout_deck(spec):
    """Return {slide_id: [frames]} for the whole deck."""
    theme = spec.get("theme") or {}
    geo = theme.get("geometry") or {"margin_in": 0.55, "radius_in": 0.1}
    margin = geo.get("margin_in", 0.55)
    radius = geo.get("radius_in", 0.1)
    bg = theme.get("background", "#FFFFFF")
    surface = theme.get("surface", "#F1F4F8")
    text = theme.get("text_primary", "#111827")
    muted = theme.get("text_secondary", "#465465")
    accent = theme.get("accent", "#1749C4")
    accent_alt = theme.get("accent_alt", "#8A4B12")
    on_accent = theme.get("text_on_accent", "#FFFFFF")
    sizes = theme.get("typography_pt") or {}
    title_pt = float(sizes.get("title", 38))
    section_pt = float(sizes.get("section", 42))
    heading_pt = float(sizes.get("heading", 25))
    body_pt = float(sizes.get("body", 19))
    caption_pt = float(sizes.get("caption", 12))
    footer_pt = float(sizes.get("footer", 10))
    metric_pt = float(sizes.get("metric", 38))
    content_w = SLIDE_W_IN - 2 * margin
    deck = spec.get("deck", {})
    footer_text = " | ".join(x for x in [deck.get("organization", ""), deck.get("title", "")] if x)

    layouts = {}
    for index, slide in enumerate(spec.get("slides", []), start=1):
        role = slide.get("role", "concept")
        frames = []
        # background plate
        frames.append(_frame("rect", 0, 0, SLIDE_W_IN, SLIDE_H_IN, fill=bg, name="Background"))
        has_header = role != "title"

        if role == "title":
            frames.append(_frame("rect", margin, 1.62, 1.5, 0.09, fill=accent, name="Title accent rule"))
            title_paras = [_para(slide.get("title", ""), title_pt, text, bold=True, align="left")]
            frames.append(_frame("text", margin, 1.85, content_w * 0.82, 2.2, title_paras, name="Title",
                                 ph="title", heading_level=1))
            sub = slide.get("subtitle") or deck.get("subtitle") or ""
            if sub:
                frames.append(_frame("text", margin, 4.15, content_w * 0.7, 0.9,
                                     [_para(sub, body_pt, muted)], name="Subtitle"))
            org = slide.get("organization") or deck.get("organization") or ""
            line = " | ".join(x for x in [org, deck.get("date", "")] if x)
            if line:
                frames.append(_frame("text", margin, 6.35, content_w, 0.4,
                                     [_para(line, body_pt * 0.8, muted)], name="Byline"))
            frames.append(_frame("rect", SLIDE_W_IN - margin - 0.9, 1.62, 0.9, 0.09, fill=accent_alt,
                                 name="Title accent counter-rule"))
        else:
            heading = slide.get("title", "")
            hsize = heading_pt if len(heading) <= 62 else heading_pt * 0.8
            frames.append(_frame("text", margin, 0.46, content_w, 1.05,
                                 [_para(heading, hsize, text, bold=True)], name="Heading",
                                 ph="title", heading_level=2))
            frames.append(_frame("rect", margin, 1.52, 1.1, 0.055, fill=accent, name="Heading rule"))

        body_top = 1.86
        body_h = SLIDE_H_IN - body_top - 0.75

        if role == "section":
            number = slide.get("title", "").split(" ")[0]
            frames.append(_frame("text", margin, 2.05, 3.0, 1.6,
                                 [_para(number, section_pt * 2.0, accent, bold=True)], name="Section number"))
            frames.append(_frame("text", margin, 3.7, content_w * 0.8, 1.5,
                                 [_para(slide.get("title", ""), section_pt * 0.8, text, bold=True)],
                                 name="Section title", heading_level=2))
            if slide.get("subtitle"):
                frames.append(_frame("text", margin, 5.1, content_w * 0.75, 0.9,
                                     [_para(slide["subtitle"], body_pt, muted)], name="Section purpose"))

        elif role == "metrics" and slide.get("metrics"):
            items = slide["metrics"][:4]
            gap = 0.3
            card_w = (content_w - gap * (len(items) - 1)) / len(items)
            for i, metric in enumerate(items):
                x = margin + i * (card_w + gap)
                frames.append(_frame("rect", x, body_top + 0.25, card_w, 2.75, fill=surface, radius=radius,
                                     name="Metric card %d" % (i + 1)))
                value_text = str(metric.get("value", ""))
                value_size = _fit_size([_para(value_text, metric_pt, accent, bold=True)],
                                       card_w - 0.44, 1.0, metric_pt, 20)
                frames.append(_frame("text", x + 0.22, body_top + 0.5, card_w - 0.44, 1.0,
                                     [_para(value_text, value_size, accent, bold=True)],
                                     name="Metric value %d" % (i + 1)))
                frames.append(_frame("text", x + 0.22, body_top + 1.55, card_w - 0.44, 0.6,
                                     [_para(metric.get("label", ""), body_pt * 0.75, text, bold=True)],
                                     name="Metric label %d" % (i + 1)))
                if metric.get("note"):
                    frames.append(_frame("text", x + 0.22, body_top + 2.1, card_w - 0.44, 0.7,
                                         [_para(metric["note"], caption_pt + 1, muted)],
                                         name="Metric note %d" % (i + 1)))
            src = "Sources: " + "; ".join(slide.get("sources", [])[:2]) if slide.get("sources") else ""
            if src:
                frames.append(_frame("text", margin, SLIDE_H_IN - 1.16, content_w, 0.35,
                                     [_para(src, caption_pt, muted)], name="Source line"))
            if slide.get("bullets"):
                frames.append(_frame("text", margin, body_top + 3.15, content_w, 0.8,
                                     [_para(b, body_pt * 0.85, text, bullet=True) for b in slide["bullets"][:2]],
                                     name="Metric takeaway"))

        elif role == "quote":
            quote = slide.get("quote") or {"text": (slide.get("bullets") or [" "])[0], "attribution": ""}
            frames.append(_frame("rect", margin, body_top + 0.2, 0.07, 2.9, fill=accent, name="Quote rule"))
            qsize = _fit_size([_para(quote.get("text", ""), 30, text, italic=True)],
                              content_w - 0.9, 2.6, 30)
            frames.append(_frame("text", margin + 0.45, body_top + 0.2, content_w - 0.9, 2.9,
                                 [_para("“%s”" % quote.get("text", ""), qsize, text, italic=True)],
                                 name="Quote"))
            if quote.get("attribution"):
                frames.append(_frame("text", margin + 0.45, body_top + 3.2, content_w - 0.9, 0.5,
                                     [_para(quote["attribution"], body_pt * 0.8, muted)], name="Quote attribution"))

        elif role in ("process", "agenda") and (slide.get("steps") or slide.get("bullets")):
            steps = (slide.get("steps") or slide.get("bullets"))[:6]
            count = max(1, len(steps))
            gap = 0.18
            step_w = (content_w - gap * (count - 1)) / count
            for i, step in enumerate(steps):
                x = margin + i * (step_w + gap)
                frames.append(_frame("rect", x, body_top + 0.35, step_w, 2.9, fill=surface, radius=radius,
                                     name="Step %d" % (i + 1)))
                frames.append(_frame("rect", x, body_top + 0.35, step_w, 0.1, fill=accent,
                                     name="Step accent %d" % (i + 1)))
                frames.append(_frame("text", x + 0.2, body_top + 0.6, step_w - 0.4, 0.5,
                                     [_para(str(i + 1), heading_pt, accent, bold=True)],
                                     name="Step number %d" % (i + 1)))
                ssize = _fit_size([_para(str(step), body_pt * 0.85, text)], step_w - 0.4, 1.9,
                                  body_pt * 0.85, 11)
                frames.append(_frame("text", x + 0.2, body_top + 1.15, step_w - 0.4, 1.9,
                                     [_para(str(step), ssize, text)], name="Step text %d" % (i + 1)))

        elif role == "timeline" and slide.get("phases"):
            phases = slide["phases"][:6]
            count = max(1, len(phases))
            gap = 0.16
            block_w = (content_w - gap * (count - 1)) / count
            frames.append(_frame("rect", margin, body_top + 0.35, content_w, 0.045, fill=muted,
                                 name="Timeline axis"))
            for i, phase in enumerate(phases):
                x = margin + i * (block_w + gap)
                frames.append(_frame("rect", x, body_top + 0.33, 0.16, 0.16, fill=accent,
                                     name="Timeline dot %d" % (i + 1)))
                frames.append(_frame("text", x, body_top + 0.6, block_w, 0.4,
                                     [_para(phase.get("date", ""), caption_pt + 1, accent, bold=True)],
                                     name="Timeline date %d" % (i + 1)))
                frames.append(_frame("text", x, body_top + 1.0, block_w, 1.6,
                                     [_para(phase.get("label", ""), body_pt * 0.8, text)],
                                     name="Timeline label %d" % (i + 1)))
                if phase.get("detail"):
                    frames.append(_frame("text", x, body_top + 2.5, block_w, 1.0,
                                         [_para(phase["detail"], caption_pt, muted)],
                                         name="Timeline detail %d" % (i + 1)))

        elif role == "table" and slide.get("table"):
            table = slide["table"]
            headers = table.get("headers", [])
            rows = table.get("rows", [])[:6]
            cols = max(1, len(headers) or (len(rows[0]) if rows else 1))
            col_w = content_w / cols
            frames.append(_frame("rect", margin, body_top, content_w, 0.5, fill=surface, name="Table header plate"))
            for c, header in enumerate(headers):
                frames.append(_frame("text", margin + c * col_w + 0.12, body_top + 0.12, col_w - 0.24, 0.32,
                                     [_para(str(header), body_pt * 0.75, text, bold=True)],
                                     name="Table header %d" % (c + 1)))
            for r, row in enumerate(rows):
                y = body_top + 0.55 + r * 0.62
                if r % 2 == 1:
                    frames.append(_frame("rect", margin, y, content_w, 0.58, fill=surface, name="Table row band %d" % (r + 1)))
                for c, cell in enumerate(row[:cols]):
                    frames.append(_frame("text", margin + c * col_w + 0.12, y + 0.12, col_w - 0.24, 0.38,
                                         [_para(str(cell), body_pt * 0.7, text)],
                                         name="Table cell %d-%d" % (r + 1, c + 1)))
            if table.get("caption"):
                frames.append(_frame("text", margin, SLIDE_H_IN - 1.1, content_w, 0.35,
                                     [_para(table["caption"], caption_pt, muted)], name="Table caption"))

        elif role == "comparison":
            half_gap = 0.35
            col_w = (content_w - half_gap) / 2
            for side, x in (("left", margin), ("right", margin + col_w + half_gap)):
                title_key = "%s_title" % side
                frames.append(_frame("rect", x, body_top, col_w, 0.5, fill=surface, radius=radius,
                                     name="%s plate" % side))
                frames.append(_frame("text", x + 0.16, body_top + 0.1, col_w - 0.32, 0.34,
                                     [_para(slide.get(title_key, side.title()), body_pt * 0.8, accent, bold=True)],
                                     name="%s title" % side))
                items = slide.get(side) or []
                if items:
                    fitsize = _fit_size([_para(i, body_pt * 0.9, text, bullet=True) for i in items],
                                        col_w, body_h - 0.7, body_pt * 0.9, 11)
                    frames.append(_frame("text", x + 0.16, body_top + 0.7, col_w - 0.32, body_h - 0.7,
                                         [_para(i, fitsize, text, bullet=True) for i in items],
                                         name="%s items" % side))

        else:  # bullets, evidence, concept, summary, demonstration, appendix, decision fallback
            items = slide.get("bullets") or slide.get("steps") or []
            if not items and slide.get("notes") and role in ("concept", "summary", "demonstration",
                                                             "appendix", "decision", "evidence"):
                items = [slide["notes"].split(". ")[0]]
            bullet_enabled = slide.get("role") not in ("decision",)
            if items:
                paras = [_para(str(item), body_pt, text, bullet=bullet_enabled) for item in items[:8]]
                fitsize = _fit_size(paras, content_w * 0.92, body_h, body_pt * 1.05, 12)
                paras = [dict(p, size=fitsize) for p in paras]
                frames.append(_frame("text", margin, body_top, content_w * 0.92, body_h, paras, name="Body"))
            if slide.get("sources"):
                frames.append(_frame("text", margin, SLIDE_H_IN - 1.16, content_w * 0.8, 0.35,
                                     [_para("Sources: " + "; ".join(slide["sources"][:2]), caption_pt, muted)],
                                     name="Source line"))
            if role == "decision":
                frames.append(_frame("rect", SLIDE_W_IN - margin - 3.5, body_top, 3.5, 1.5, fill=accent,
                                     radius=radius, name="Decision plate"))
                frames.append(_frame("text", SLIDE_W_IN - margin - 3.28, body_top + 0.22, 3.06, 1.1,
                                     [_para("Decision required", caption_pt + 2, on_accent, bold=True),
                                      _para("Owner and date on this slide", caption_pt, on_accent)],
                                     name="Decision plate text"))

        if has_header:
            frames.append(_frame("rect", 0, SLIDE_H_IN - 0.5, SLIDE_W_IN, 0.012, fill=muted, name="Footer rule"))
            frames.append(_frame("text", margin, SLIDE_H_IN - 0.42, content_w * 0.7, 0.28,
                                 [_para(footer_text, footer_pt, muted)], name="Footer"))
            frames.append(_frame("text", SLIDE_W_IN - margin - 0.9, SLIDE_H_IN - 0.42, 0.9, 0.28,
                                 [_para("%d" % index, footer_pt, muted, align="right")], name="Slide number"))
        layouts[slide.get("id", "slide-%d" % index)] = frames
    return layouts


# ==========================================================================
# PPTX
# ==========================================================================
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS_P = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
CT_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
NS_CT = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "xml": "application/xml",
}


def _esc(value):
    return html.escape(str(value), quote=True)


def _emu(inches):
    return int(round(float(inches) * EMU_PER_INCH))


def _color(hex_value):
    return pm_core.to_hex(pm_core.parse_hex(hex_value)).lstrip("#")


def _frame_to_shape(frame, shape_id):
    x, y = _emu(frame["x"]), _emu(frame["y"])
    cx, cy = _emu(frame["w"]), _emu(frame["h"])
    name = _esc(frame.get("name", "Shape"))
    fill = frame.get("fill")
    if frame["kind"] == "rect":
        geom = "roundRect" if frame.get("radius") else "rect"
        sp_pr = ('<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
                 '<a:prstGeom prst="%s"><a:avLst/></a:prstGeom>%s%s</p:spPr>' % (
                     x, y, cx, cy, geom,
                     '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % _color(fill) if fill else '<a:noFill/>',
                     '<a:ln><a:noFill/></a:ln>'))
        return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
                '%s</p:sp>' % (shape_id, name, sp_pr))

    paragraphs = []
    autofit = '<a:normAutofit/>'
    for para in frame.get("paragraphs", []):
        runs = []
        text_value = para.get("text", "")
        if para.get("bullet"):
            text_value = "• " + text_value
        runs.append(
            '<a:r><a:rPr lang="en-US" sz="%d" b="%d" i="%d" dirty="0">'
            '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
            '<a:latin typeface="+mn-lt"/></a:rPr><a:t>%s</a:t></a:r>' % (
                int(round(para.get("size", 18) * 100)), 1 if para.get("bold") else 0,
                1 if para.get("italic") else 0, _color(para.get("color", "#111827")), _esc(text_value)))
        algn = {"left": "l", "center": "ctr", "right": "r"}.get(para.get("align", "left"), "l")
        spc = int(round(para.get("space_after", 4) * 100))
        paragraphs.append('<a:p><a:pPr algn="%s"><a:spcAft><a:spcPts val="%d"/></a:spcAft></a:pPr>'
                          '<a:endParaRPr lang="en-US"/></a:p>' % (algn, spc))
        # insert the run before the trailing endParaRPr
        paragraphs[-1] = paragraphs[-1].replace('<a:endParaRPr', runs[0] + '<a:endParaRPr')
    sp_pr = ('<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
             '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s<a:ln><a:noFill/></a:ln></p:spPr>' % (
                 x, y, cx, cy,
                 '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % _color(fill) if fill else '<a:noFill/>'))
    body = ('<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t">%s</a:bodyPr>'
            '<a:lstStyle/>%s</p:txBody>' % (autofit, "".join(paragraphs)))
    nv_pr = ('<p:nvPr><p:ph type="title"/></p:nvPr>' if frame.get("ph") == "title" else "<p:nvPr/>")
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/>%s</p:nvSpPr>'
            '%s%s</p:sp>' % (shape_id, name, nv_pr, sp_pr, body))


def _sptree(frames):
    shapes = []
    for index, frame in enumerate(frames, start=2):
        shapes.append(_frame_to_shape(frame, index))
    return ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>%s</p:spTree>'
            % "".join(shapes))


def _theme_xml(theme):
    colors = [
        ("dk1", theme.get("text_primary", "#111827")),
        ("lt1", theme.get("background", "#FFFFFF")),
        ("dk2", theme.get("text_secondary", "#465465")),
        ("lt2", theme.get("surface", "#F1F4F8")),
        ("accent1", theme.get("accent", "#1749C4")),
        ("accent2", theme.get("accent_alt", "#8A4B12")),
    ]
    series = theme.get("chart_series") or []
    for i in range(3, 7):
        colors.append(("accent%d" % i, series[i - 1] if len(series) >= i else "#4B5563"))
    colors.append(("hlink", theme.get("accent", "#1749C4")))
    colors.append(("folHlink", theme.get("accent_alt", "#8A4B12")))
    scheme = "".join('<a:%s><a:srgbClr val="%s"/></a:%s>' % (name, _color(value), name)
                     for name, value in colors)
    heading_font = (theme.get("fonts") or {}).get("heading", "Segoe UI").split(",")[0].strip()
    body_font = (theme.get("fonts") or {}).get("body", "Segoe UI").split(",")[0].strip()
    return (XML_HEAD + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="presentation-makers">'
            '<a:themeElements><a:clrScheme name="%s">%s</a:clrScheme>'
            '<a:fontScheme name="pm"><a:majorFont><a:latin typeface="%s"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="%s"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="pm"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '</a:fillStyleLst><a:lnStyleLst><a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
            '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
            '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
            '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'
            % (_esc(theme.get("id", "pm")), scheme, heading_font, body_font))


def _master_xml(theme):
    bg = _color(theme.get("background", "#FFFFFF"))
    text = _color(theme.get("text_primary", "#111827"))
    body_pt = int((theme.get("typography_pt") or {}).get("body", 19) * 100)
    title_pt = int((theme.get("typography_pt") or {}).get("title", 38) * 100)
    return (XML_HEAD + '<p:sldMaster %s><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
            '<a:effectLst/></p:bgPr></p:bg>%s</p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            '<p:txStyles>'
            '<p:titleStyle><a:lvl1pPr><a:defRPr sz="%d" b="1"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:defRPr></a:lvl1pPr></p:titleStyle>'
            '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:defRPr></a:lvl1pPr></p:bodyStyle>'
            '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1400"/></a:lvl1pPr></p:otherStyle>'
            '</p:txStyles></p:sldMaster>' % (NS_P, bg, _sptree([]), title_pt, text, body_pt, text))


def _layout_xml(theme):
    return (XML_HEAD + '<p:sldLayout %s type="blank" preserve="1"><p:cSld name="Blank">%s</p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
            % (NS_P, _sptree([])))


def _notes_master_xml(theme):
    return (XML_HEAD + '<p:notesMaster %s><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/>'
            '</a:solidFill><a:effectLst/></p:bgPr></p:bg>%s</p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:notesStyle><a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle></p:notesMaster>'
            % (NS_P, _sptree([])))


def _notes_slide_xml(text):
    body = ('<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>')
    paragraphs = "".join(
        '<a:p><a:r><a:rPr lang="en-US" sz="1200" dirty="0"/><a:t>%s</a:t></a:r></a:p>' % _esc(line)
        for line in (text.split("\n") or [""]))
    note = ('<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr>'
            '</p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>%s</p:txBody></p:sp>' % paragraphs)
    return (XML_HEAD + '<p:notes %s><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/>'
            '<p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>%s%s</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>' % (NS_P, body, note))


def render_pptx(spec, out_path, config):
    """Write a valid 16:9 PPTX with one blank layout, rendered frames, and notes."""
    layouts = layout_deck(spec)
    slides = spec.get("slides", [])
    theme = spec.get("theme") or {}
    deck = spec.get("deck", {})
    include_notes = config.get("render", {}).get("include_notes", True)

    content_types = [CT_HEAD,
                     '<Default Extension="rels" ContentType="%s"/>' % NS_CT["rels"],
                     '<Default Extension="xml" ContentType="%s"/>' % NS_CT["xml"],
                     '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
                     '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
                     '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
                     '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
                     '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>',
                     '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>',
                     '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>',
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    if include_notes:
        content_types.append('<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>')
    for index in range(1, len(slides) + 1):
        content_types.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % index)
        if include_notes:
            content_types.append('<Override PartName="/ppt/notesSlides/notesSlide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' % index)
    content_types.append("</Types>")

    root_rels = (XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                 '</Relationships>')

    core = (XML_HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>'
            '<cp:revision>1</cp:revision><dc:subject>%s</dc:subject>'
            '<cp:keywords>presentation-makers</cp:keywords>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
            '</cp:coreProperties>' % (
                _esc(deck.get("title", "Presentation")),
                _esc(config.get("render", {}).get("pptx_creator", "presentation-makers")),
                _esc(config.get("render", {}).get("pptx_creator", "presentation-makers")),
                _esc(deck.get("audience", "")), pm_core.now_iso(), pm_core.now_iso()))

    app = (XML_HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
           '<Application>presentation-makers</Application><Slides>%d</Slides>'
           '<Notes>%d</Notes><PresentationFormat>Widescreen</PresentationFormat>'
           '<Company>%s</Company><AppVersion>1.0</AppVersion></Properties>' % (
               len(slides), len(slides) if include_notes else 0, _esc(deck.get("organization", ""))))

    presentation_rels = [XML_HEAD, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                         '<Relationship Id="rIdMaster" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>',
                         '<Relationship Id="rIdTheme" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>',
                         '<Relationship Id="rIdPresProps" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps" Target="presProps.xml"/>',
                         '<Relationship Id="rIdViewProps" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps" Target="viewProps.xml"/>',
                         '<Relationship Id="rIdTableStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles" Target="tableStyles.xml"/>']
    if include_notes:
        presentation_rels.append('<Relationship Id="rIdNotesMaster" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="notesMasters/notesMaster1.xml"/>')
    sld_ids = []
    for index in range(1, len(slides) + 1):
        presentation_rels.append('<Relationship Id="rIdSlide%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide%d.xml"/>' % (index, index))
        sld_ids.append('<p:sldId id="%d" r:id="rIdSlide%d"/>' % (255 + index, index))
    presentation_rels.append("</Relationships>")

    notes_master_ref = ('<p:notesMasterIdLst><p:notesMasterId r:id="rIdNotesMaster"/></p:notesMasterIdLst>'
                        if include_notes else "")
    presentation = (XML_HEAD + '<p:presentation %s saveSubsetFonts="1">'
                    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdMaster"/></p:sldMasterIdLst>'
                    '%s<p:sldIdLst>%s</p:sldIdLst>'
                    '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/>'
                    '<p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:defaultTextStyle>'
                    '</p:presentation>' % (
                        NS_P, notes_master_ref, "".join(sld_ids),
                        _emu(SLIDE_W_IN), _emu(SLIDE_H_IN)))

    master_rels = (XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
                   '</Relationships>')
    layout_rels = (XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
                   '</Relationships>')
    notes_master_rels = (XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
                         '</Relationships>')
    pres_props = (XML_HEAD + '<p:presentationPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                  'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                  'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    view_props = (XML_HEAD + '<p:viewPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                  'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                  'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                  '<p:normalViewPr><p:restoredLeft sz="15620"/><p:restoredTop sz="94660"/></p:normalViewPr>'
                  '<p:slideViewPr><p:cSldViewPr><p:cViewPr varScale="1"><p:scale><a:sx n="100" d="100"/>'
                  '<a:sy n="100" d="100"/></p:scale><p:origin x="0" y="0"/></p:cViewPr><p:guideLst/>'
                  '</p:cSldViewPr></p:slideViewPr><p:notesTextViewPr><p:cViewPr><p:scale><a:sx n="100" d="100"/>'
                  '<a:sy n="100" d="100"/></p:scale><p:origin x="0" y="0"/></p:cViewPr></p:notesTextViewPr>'
                  '<p:gridSpacing cx="72008" cy="72008"/></p:viewPr>')
    table_styles = (XML_HEAD + '<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                    'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')

    out_path = pm_core.resolve_path(out_path)
    pm_core.ensure_dir(os.path.dirname(out_path) or ".")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", "".join(presentation_rels))
        archive.writestr("ppt/slideMasters/slideMaster1.xml", _master_xml(theme))
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", _layout_xml(theme))
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        archive.writestr("ppt/theme/theme1.xml", _theme_xml(theme))
        archive.writestr("ppt/presProps.xml", pres_props)
        archive.writestr("ppt/viewProps.xml", view_props)
        archive.writestr("ppt/tableStyles.xml", table_styles)
        if include_notes:
            archive.writestr("ppt/notesMasters/notesMaster1.xml", _notes_master_xml(theme))
            archive.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", notes_master_rels)
        for index, slide in enumerate(slides, start=1):
            frames = layouts.get(slide.get("id"), [])
            slide_xml = (XML_HEAD + '<p:sld %s><p:cSld>%s</p:cSld>'
                         '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
                         % (NS_P, _sptree(frames)))
            archive.writestr("ppt/slides/slide%d.xml" % index, slide_xml)
            rels = [XML_HEAD, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>']
            if include_notes:
                rels.append('<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide%d.xml"/>' % index)
            rels.append("</Relationships>")
            archive.writestr("ppt/slides/_rels/slide%d.xml.rels" % index, "".join(rels))
            if include_notes:
                archive.writestr("ppt/notesSlides/notesSlide%d.xml" % index,
                                 _notes_slide_xml(slide.get("notes", "") or ""))
                archive.writestr("ppt/notesSlides/_rels/notesSlide%d.xml.rels" % index,
                                 XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="../notesMasters/notesMaster1.xml"/>'
                                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="../slides/slide%d.xml"/>'
                                 '</Relationships>' % index)
    return out_path


# ==========================================================================
# HTML
# ==========================================================================
HTML_CSS = """
:root{--bg:%(bg)s;--surface:%(surface)s;--text:%(text)s;--muted:%(muted)s;--accent:%(accent)s;--accent-alt:%(accent_alt)s;--on-accent:%(on_accent)s}
*{box-sizing:border-box}
html,body{margin:0;height:100%%;background:#05080c;color:var(--text);font-family:%(body_font)s}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100%%}
#stage{position:relative;width:13.333in;height:7.5in;transform-origin:top left;background:var(--bg);overflow:hidden}
.slide{position:absolute;inset:0;display:none}
.slide[aria-current="true"]{display:block}
.frame{position:absolute;overflow:hidden}
.rect{background:var(--fill,#000)}
.para{margin:0 0 4pt 0;white-space:pre-wrap;word-wrap:break-word}
.para.bullet{padding-left:0.22in;text-indent:-0.22in}
.para.bullet:before{content:"\\2022";color:var(--accent);display:inline-block;width:0.22in;text-indent:0}
#chrome{position:fixed;bottom:0;left:0;right:0;display:flex;gap:1rem;align-items:center;justify-content:center;padding:.5rem;background:#0b1119;color:#c9d6e2;font:12px/1.4 %(body_font)s;z-index:10}
#chrome button{background:#1b2836;color:#dbe7f2;border:1px solid #2d3f52;border-radius:6px;padding:.35rem .7rem;font:inherit;cursor:pointer}
#chrome button:focus-visible{outline:3px solid #57c7ff;outline-offset:2px}
#notes{position:fixed;right:0;top:0;bottom:3rem;width:min(28rem,42vw);background:#0b1119;color:#c9d6e2;border-left:1px solid #23303f;padding:1rem;overflow:auto;font:13px/1.5 %(body_font)s;display:none}
#notes[data-open="true"]{display:block}
#notes h3{margin:.2rem 0 .6rem;font-size:.85rem;letter-spacing:.08em;text-transform:uppercase;color:#7f93a6}
#status{position:fixed;top:0;left:0;padding:.3rem .6rem;color:#8ea3b6;font:11px/1.3 %(body_font)s}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
@media print{html,body{background:#fff}#chrome,#notes,#status{display:none}#stage{transform:none!important;width:100%%;height:auto}.slide{display:block;position:relative;page-break-after:always;height:7.5in;width:13.333in}}
"""

HTML_JS = """
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var stage=document.getElementById('stage');
  var status=document.getElementById('status');
  var notes=document.getElementById('notes');
  var notesBody=document.getElementById('notes-body');
  var i=0;
  function fit(){
    var pad=24, chrome=56;
    var sx=(window.innerWidth-pad*2)/stage.offsetWidth;
    var sy=(window.innerHeight-pad*2-chrome)/stage.offsetHeight;
    var s=Math.min(sx,sy);
    stage.style.transform='scale('+s+')';
    stage.style.marginTop=Math.max(0,(window.innerHeight-chrome-stage.offsetHeight*s)/2)+'px';
  }
  function show(n){
    if(n<0||n>=slides.length) return;
    i=n;
    slides.forEach(function(s,k){s.setAttribute('aria-current',k===i?'true':'false');});
    status.textContent='Slide '+(i+1)+' of '+slides.length;
    notesBody.innerHTML=slides[i].getAttribute('data-notes')||'<p>No notes.</p>';
  }
  function toggleNotes(){notes.setAttribute('data-open',notes.getAttribute('data-open')==='true'?'false':'true');fit();}
  document.addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'||e.key==='PageDown'||e.key===' '){e.preventDefault();show(i+1);}
    else if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();show(i-1);}
    else if(e.key==='Home'){show(0);}
    else if(e.key==='End'){show(slides.length-1);}
    else if(e.key==='n'||e.key==='N'){toggleNotes();}
  });
  document.getElementById('prev').addEventListener('click',function(){show(i-1);});
  document.getElementById('next').addEventListener('click',function(){show(i+1);});
  document.getElementById('toggle-notes').addEventListener('click',toggleNotes);
  window.addEventListener('resize',fit);
  show(0);fit();
})();
"""


def _css_frames(frames):
    out = []
    for frame in frames:
        if frame["kind"] == "rect":
            radius = "border-radius:%.3fin;" % frame["radius"] if frame.get("radius") else ""
            out.append('<div class="frame rect" style="left:%.3fin;top:%.3fin;width:%.3fin;height:%.3fin;'
                       '--fill:%s;%s"></div>' % (frame["x"], frame["y"], frame["w"], frame["h"],
                                                 frame.get("fill") or "transparent", radius))
            continue
        paras = []
        for para in frame.get("paragraphs", []):
            klass = "para bullet" if para.get("bullet") else "para"
            style = "font-size:%.2fpt;color:%s;text-align:%s;margin-bottom:%dpt;%s%s" % (
                para.get("size", 18), para.get("color", "#111827"), para.get("align", "left"),
                para.get("space_after", 4),
                "font-weight:700;" if para.get("bold") else "",
                "font-style:italic;" if para.get("italic") else "")
            paras.append('<p class="%s" style="%s">%s</p>' % (klass, style, html.escape(para.get("text", ""))))
        level = frame.get("heading_level")
        if level:
            out.append('<div class="frame" style="left:%.3fin;top:%.3fin;width:%.3fin;height:%.3fin">'
                       '<h%d style="margin:0">%s</h%d></div>'
                       % (frame["x"], frame["y"], frame["w"], frame["h"], level,
                          "".join(paras).replace('<p class="para"', '<span class="para"').replace("</p>", "</span>"),
                          level))
        else:
            out.append('<div class="frame" style="left:%.3fin;top:%.3fin;width:%.3fin;height:%.3fin">%s</div>'
                       % (frame["x"], frame["y"], frame["w"], frame["h"], "".join(paras)))
    return "".join(out)


def render_html(spec, out_path, config):
    """Write a single self-contained HTML deck: no external fonts, scripts, or images."""
    layouts = layout_deck(spec)
    theme = spec.get("theme") or {}
    deck = spec.get("deck", {})
    fonts = theme.get("fonts") or {}
    css = HTML_CSS % {
        "bg": theme.get("background", "#FFFFFF"),
        "surface": theme.get("surface", "#F1F4F8"),
        "text": theme.get("text_primary", "#111827"),
        "muted": theme.get("text_secondary", "#465465"),
        "accent": theme.get("accent", "#1749C4"),
        "accent_alt": theme.get("accent_alt", "#8A4B12"),
        "on_accent": theme.get("text_on_accent", "#FFFFFF"),
        "body_font": fonts.get("body", "Segoe UI, Helvetica Neue, Arial, sans-serif"),
    }
    slides_html = []
    for index, slide in enumerate(spec.get("slides", []), start=1):
        frames = layouts.get(slide.get("id"), [])
        notes_html = "<p>%s</p>" % html.escape(slide.get("notes", "") or "No notes.")
        if slide.get("sources"):
            notes_html += "<p><strong>Sources:</strong> %s</p>" % html.escape("; ".join(slide["sources"]))
        notes_attr = html.escape(notes_html, quote=True)
        slides_html.append(
            '<section class="slide" role="group" aria-roledescription="slide" aria-label="Slide %d of %d: %s" '
            'aria-current="%s" data-notes="%s">%s</section>' % (
                index, len(spec.get("slides", [])), html.escape(slide.get("title", "")),
                "true" if index == 1 else "false", notes_attr, _css_frames(frames)))
    document = """<!DOCTYPE html>
<html lang="%s">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>%s</style>
</head>
<body>
<div id="status" aria-live="polite"></div>
<main id="stage">%s</main>
<aside id="notes" data-open="false" aria-label="Speaker notes"><h3>Speaker notes</h3><div id="notes-body"></div></aside>
<nav id="chrome" aria-label="Presentation controls">
  <button id="prev" type="button">Previous</button>
  <button id="next" type="button">Next</button>
  <button id="toggle-notes" type="button" aria-pressed="false">Notes (N)</button>
  <span class="sr-only">Use the arrow keys, Home, and End to navigate.</span>
</nav>
<script>%s</script>
</body>
</html>
""" % (html.escape(deck.get("language", "en") or "en"), html.escape(deck.get("title", "Presentation")),
       html.escape(deck.get("subtitle", "") or deck.get("audience", "")), css,
       "".join(slides_html), HTML_JS)
    return pm_core.write_text(out_path, document)


# ==========================================================================
# PDF (hand-written, text + filled rectangles only)
# ==========================================================================
PDF_FONTS = {"regular": "F1", "bold": "F2", "italic": "F3"}
PDF_ASCII = {
    "—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "•": "-",
    "→": "->", "≥": ">=", "≤": "<=", "×": "x", "·": ".", "’": "'", "₱": "PHP ",
}


def _pdf_escape(text):
    for src, dst in PDF_ASCII.items():
        text = text.replace(src, dst)
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf_wrap(text, width_pt, size_pt):
    approx_char = size_pt * 0.5
    max_chars = max(8, int(width_pt / approx_char))
    lines, current = [], ""
    for word in str(text).split():
        candidate = (current + " " + word).strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _hex_to_rgb01(hex_value):
    r, g, b = pm_core.parse_hex(hex_value)
    return r / 255.0, g / 255.0, b / 255.0


def render_pdf(spec, out_path, config):
    """Write a static handout PDF, one page per visible slide."""
    layouts = layout_deck(spec)
    pages = []
    slides = [s for s in spec.get("slides", []) if not s.get("hidden")]
    theme = spec.get("theme") or {}
    bg = _hex_to_rgb01(theme.get("background", "#FFFFFF"))
    for slide in slides:
        frames = layouts.get(slide.get("id"), [])
        ops = ["q %.4f %.4f %.4f rg 0 0 960 540 re f Q" % bg]
        for frame in frames:
            x_pt = frame["x"] * 72.0
            y_pt = 540.0 - (frame["y"] + frame["h"]) * 72.0
            w_pt = frame["w"] * 72.0
            h_pt = frame["h"] * 72.0
            if frame["kind"] == "rect" and frame.get("fill") and frame["w"] > 0.02:
                r, g, b = _hex_to_rgb01(frame["fill"])
                if frame["h"] >= 0.03:
                    ops.append("%.4f %.4f %.4f rg %.2f %.2f %.2f %.2f re f" % (r, g, b, x_pt, y_pt, w_pt, h_pt))
                continue
            cursor = 540.0 - frame["y"] * 72.0
            for para in frame.get("paragraphs", []):
                size = para.get("size", 18)
                font = PDF_FONTS["bold"] if para.get("bold") else (PDF_FONTS["italic"] if para.get("italic") else PDF_FONTS["regular"])
                r, g, b = _hex_to_rgb01(para.get("color", "#111827"))
                prefix = "- " if para.get("bullet") else ""
                for line in _pdf_wrap(prefix + para.get("text", ""), w_pt, size):
                    cursor -= size * 1.18
                    if cursor < frame["y"] * 0 - 20:
                        break
                    ops.append("BT /%s %.2f Tf %.4f %.4f %.4f rg 1 0 0 1 %.2f %.2f Tm (%s) Tj ET"
                               % (font, size, r, g, b, x_pt, cursor, _pdf_escape(line)))
        pages.append("\n".join(ops))

    objects = []
    page_ids = []
    content_ids = []
    # 1 catalog, 2 pages, 3 font regular, 4 font bold, 5 font italic
    next_id = 6
    for page_ops in pages:
        content_ids.append(next_id)
        next_id += 1
        page_ids.append(next_id)
        next_id += 1
    objects.append(("<< /Type /Catalog /Pages 2 0 R >>", 1))
    kids = " ".join("%d 0 R" % pid for pid in page_ids)
    objects.append(("<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), kids), 2))
    objects.append(("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>", 3))
    objects.append(("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>", 4))
    objects.append(("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>", 5))
    for index, (page_ops, content_id, page_id) in enumerate(zip(pages, content_ids, page_ids)):
        stream = page_ops.encode("latin-1")
        objects.append(("<< /Length %d >>\nstream\n" % len(stream) + stream.decode("latin-1") + "\nendstream", content_id))
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents %d 0 R "
                        "/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>" % content_id, page_id))

    info = ("<< /Title (%s) /Producer (presentation-makers %s) /Creator (presentation-makers) >>"
            % (_pdf_escape(spec.get("deck", {}).get("title", "Presentation")), pm_core.TOOL_VERSION))
    info_id = next_id
    next_id += 1
    objects.append((info, info_id))

    objects.sort(key=lambda item: item[1])
    out = ["%PDF-1.4\n"]
    offsets = {}
    position = len(out[0])
    for body, obj_id in objects:
        chunk = "%d 0 obj\n%s\nendobj\n" % (obj_id, body)
        offsets[obj_id] = position
        out.append(chunk)
        position += len(chunk.encode("latin-1"))
    xref_pos = position
    max_id = max(offsets) + 1
    xref = ["xref\n0 %d\n" % max_id, "0000000000 65535 f \n"]
    for obj_id in range(1, max_id):
        xref.append("%010d 00000 n \n" % offsets.get(obj_id, 0))
    trailer = ("trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
               % (max_id, info_id, xref_pos))
    document = ("".join(out) + "".join(xref) + trailer)
    resolved = pm_core.resolve_path(out_path)
    pm_core.ensure_dir(os.path.dirname(resolved) or ".")
    with open(resolved, "w", encoding="latin-1", newline="\n") as fh:
        fh.write(document)
    return resolved


# ==========================================================================
# Markdown / text
# ==========================================================================
def render_handout(spec):
    deck = spec.get("deck", {})
    lines = ["# %s" % deck.get("title", "Presentation"), ""]
    meta = [("Audience", deck.get("audience")), ("Purpose", deck.get("purpose")),
            ("Delivery", deck.get("delivery_mode")), ("Duration",
             "%s minutes" % deck.get("duration_minutes") if deck.get("duration_minutes") else None),
            ("Framework", deck.get("framework")), ("Classification", deck.get("classification"))]
    for label, value in meta:
        if value:
            lines.append("- **%s:** %s" % (label, value))
    lines.append("")
    if spec.get("call_to_action", {}).get("decision"):
        lines += ["## Call to action", "", spec["call_to_action"]["decision"], ""]
    for section in spec.get("sections", []):
        lines += ["## %s" % section.get("title", "Section"), ""]
        if section.get("purpose"):
            lines += ["_%s_" % section["purpose"], ""]
        for slide_id in section.get("slide_ids", []):
            slide = next((s for s in spec.get("slides", []) if s.get("id") == slide_id), None)
            if not slide:
                continue
            lines.append("### %s" % slide.get("title", ""))
            lines.append("")
            if slide.get("metrics"):
                for metric in slide["metrics"]:
                    lines.append("- **%s:** %s %s" % (metric.get("label", ""), metric.get("value", ""),
                                                      metric.get("note", "")))
            for key in ("bullets", "steps"):
                for item in slide.get(key, []) or []:
                    lines.append("- %s" % item)
            if slide.get("left") or slide.get("right"):
                for item in slide.get("left", []) or []:
                    lines.append("- (%s) %s" % (slide.get("left_title", "A"), item))
                for item in slide.get("right", []) or []:
                    lines.append("- (%s) %s" % (slide.get("right_title", "B"), item))
            if slide.get("table"):
                headers = slide["table"].get("headers", [])
                rows = slide["table"].get("rows", [])
                if headers:
                    lines.append("")
                    lines.append("| " + " | ".join(headers) + " |")
                    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
                    for row in rows:
                        cells = list(row) + [""] * (len(headers) - len(row))
                        lines.append("| " + " | ".join(cells[:len(headers)]) + " |")
            if slide.get("quote"):
                lines += ["", "> %s" % slide["quote"].get("text", ""), "> - %s" % slide["quote"].get("attribution", "")]
            if slide.get("sources"):
                lines += ["", "_Sources: %s_" % "; ".join(slide["sources"])]
            lines.append("")
    if spec.get("sources"):
        lines += ["## Sources", ""] + ["- %s" % s for s in spec["sources"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_notes(spec):
    deck = spec.get("deck", {})
    lines = ["# Speaker notes - %s" % deck.get("title", "Presentation"), "",
             "Total planned time: %s minutes." % spec.get("stats", {}).get("planned_minutes", "?"), ""]
    cumulative = 0
    for index, slide in enumerate(spec.get("slides", []), start=1):
        cumulative += int(slide.get("pacing_seconds", 0) or 0)
        lines += ["## %d. %s" % (index, slide.get("title", "")), "",
                  "- Role: %s" % slide.get("role", ""),
                  "- Time: %ds (cumulative %d:%02d)" % (int(slide.get("pacing_seconds", 0) or 0),
                                                        cumulative // 60, cumulative % 60)]
        if slide.get("hidden"):
            lines.append("- Hidden slide (backup)")
        lines += ["", slide.get("notes", "") or "_No notes drafted._", ""]
        if slide.get("sources"):
            lines += ["Sources: %s" % "; ".join(slide["sources"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def render_outline(spec):
    lines = ["# Outline - %s" % spec.get("deck", {}).get("title", "Presentation"), ""]
    for section in spec.get("sections", []):
        lines.append("## %s" % section.get("title", ""))
        for slide_id in section.get("slide_ids", []):
            slide = next((s for s in spec.get("slides", []) if s.get("id") == slide_id), None)
            if slide:
                lines.append("- [%s] %s" % (slide.get("role"), slide.get("title", "")))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_srt(spec):
    """Caption file built from speaker notes and slide pacing (draft, needs review)."""
    def stamp(seconds):
        hours, rest = divmod(int(seconds), 3600)
        minutes, secs = divmod(rest, 60)
        return "%02d:%02d:%02d,000" % (hours, minutes, secs)

    blocks = []
    cursor = 0.0
    index = 1
    for slide in spec.get("slides", []):
        if slide.get("hidden"):
            continue
        duration = max(2, int(slide.get("pacing_seconds", 45) or 45))
        text = " ".join((slide.get("notes") or "").split())
        if not text:
            text = slide.get("title", "")
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()] or [text]
        per = max(2.0, duration / float(len(sentences)))
        for sentence in sentences:
            blocks.append("%d\n%s --> %s\n%s\n" % (index, stamp(cursor), stamp(cursor + per), sentence))
            cursor += per
            index += 1
        cursor += 0.4
    return "\n".join(blocks)


RENDERERS = {
    "pptx": ("deck.pptx", render_pptx),
    "html": ("deck.html", render_html),
    "pdf": ("handout.pdf", render_pdf),
}


def render_all(spec, outdir, formats, config):
    """Render every requested format and return {format: (path, bytes)}."""
    outdir = pm_core.resolve_path(outdir)
    pm_core.ensure_dir(outdir)
    produced = {}
    for fmt in formats:
        fmt = fmt.lower()
        if fmt == "pptx":
            path = render_pptx(spec, os.path.join(outdir, "deck.pptx"), config)
        elif fmt == "html":
            path = render_html(spec, os.path.join(outdir, "deck.html"), config)
        elif fmt == "pdf":
            path = render_pdf(spec, os.path.join(outdir, "handout.pdf"), config)
        elif fmt in ("md", "markdown", "handout"):
            path = pm_core.write_text(os.path.join(outdir, "handout.md"), render_handout(spec))
        elif fmt in ("notes", "speaker-notes"):
            path = pm_core.write_text(os.path.join(outdir, "speaker-notes.md"), render_notes(spec))
        elif fmt == "outline":
            path = pm_core.write_text(os.path.join(outdir, "outline.md"), render_outline(spec))
        elif fmt in ("srt", "captions"):
            path = pm_core.write_text(os.path.join(outdir, "captions.srt"), render_srt(spec))
        else:
            raise pm_core.PMError(
                "Unknown export format '%s'" % fmt, code="format-unknown",
                hint="Supported: pptx, html, pdf, md, notes, outline, srt")
        produced[fmt] = (path, os.path.getsize(path))
    return produced


if __name__ == "__main__":  # pragma: no cover - module smoke test
    cfg = pm_core.load_config()
    brief, _ = pm_plan.load_brief("asset-example-brief.json", cfg)
    spec, _d = pm_plan.build_deck_spec(brief, cfg)
    print(render_all(spec, "/tmp/pm-smoke", ["pptx", "html", "pdf", "md"], cfg))
