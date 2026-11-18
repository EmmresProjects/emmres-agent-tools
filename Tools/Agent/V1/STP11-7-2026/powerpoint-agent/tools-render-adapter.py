#!/usr/bin/env python3
"""Rendering and export adapter for the powerpoint-agent skill tool.

Two rendering backends:

``internal``
    A Pillow-based approximation of each slide (backgrounds, solid shapes,
    text with real font metrics and wrapping, images, tables, and simple
    charts).  It always works, runs offline, and is meant for geometry,
    overflow, and hierarchy review.  It is **not** pixel-accurate and does not
    render animations, transitions, effects, or exact PowerPoint typography.

``libreoffice``
    Faithful conversion through a locally installed LibreOffice/soffice binary
    (``--headless --convert-to``).  Used for PDF export and, when poppler's
    ``pdftoppm`` is available, high-fidelity PNG export.  When the binary is
    missing the adapter raises a dependency error with install guidance instead
    of pretending the export worked.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

CORE = None


def bind(core_module) -> None:
    global CORE
    CORE = core_module


# ---------------------------------------------------------------------------
# backend discovery
# ---------------------------------------------------------------------------

def find_soffice(config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    config = config or {}
    explicit = str(CORE.cfg(config, "export.libreoffice_binary", "") or "").strip() if CORE else ""
    if explicit:
        return explicit if os.path.isfile(explicit) else shutil.which(explicit)
    candidates = CORE.cfg(config, "export.soffice_candidates", ["soffice", "libreoffice"]) or []
    for candidate in candidates:
        if os.path.isfile(str(candidate)):
            return str(candidate)
        found = shutil.which(str(candidate))
        if found:
            return found
    return None


def find_pdftoppm() -> Optional[str]:
    for candidate in ("pdftoppm", "pdftocairo", "mutool"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def backend_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or {}
    soffice = find_soffice(config)
    pdftoppm = find_pdftoppm()
    return {
        "internal_preview": {"available": bool(CORE and CORE.PIL_AVAILABLE), "kind": "approximate raster preview"},
        "libreoffice": {"available": bool(soffice), "binary": soffice or "", "kind": "faithful PDF conversion"},
        "pdf_rasterizer": {"available": bool(pdftoppm), "binary": pdftoppm or "", "kind": "PDF to PNG"},
        "pdf_export": bool(soffice),
        "png_export": bool(soffice and pdftoppm),
        "preview_pdf": bool(CORE and CORE.PIL_AVAILABLE),
    }


# ---------------------------------------------------------------------------
# internal preview renderer
# ---------------------------------------------------------------------------

def _hex_tuple(value: str) -> Tuple[int, int, int]:
    r, g, b = CORE.rgb_tuple(value)
    return (r, g, b)


def _font(role: str, size_px: float, config: Dict[str, Any]):
    from PIL import ImageFont  # type: ignore

    return CORE._pil_font(role, max(6, int(round(size_px))), config)


def _text_lines(draw, text: str, font, max_width_px: float) -> List[str]:
    words = str(text).split()
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        try:
            width = draw.textlength(trial, font=font)
        except Exception:
            width = len(trial) * 8
        if width <= max_width_px or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_frame(draw, shape, ctx: Dict[str, Any]) -> None:
    from PIL import ImageFont  # type: ignore

    frame = shape.text_frame
    try:
        rect = ctx["rect"](shape)
    except Exception:
        return
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return
    box_w = x1 - x0
    box_h = y1 - y0
    left_pad = 6
    right_pad = 6
    paragraphs: List[Dict[str, Any]] = []
    for paragraph in frame.paragraphs:
        text = paragraph.text
        if not text.strip():
            paragraphs.append({"blank": True, "size": 0, "space": 0})
            continue
        size_pt = None
        bold = None
        color = None
        for run in paragraph.runs:
            if run.font.size is not None:
                size_pt = float(run.font.size.pt)
            if run.font.bold is not None:
                bold = bool(run.font.bold)
            try:
                if run.font.color and run.font.color.type is not None:
                    rgb = run.font.color.rgb
                    color = "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
            except Exception:
                color = None
            if size_pt:
                break
        if size_pt is None:
            style = CORE.resolve_run_style(ctx["prs"], ctx["slide"], shape, paragraph, paragraph.runs[0] if paragraph.runs else None,
                                           ctx["scheme"]) if paragraph.runs else {"size_pt": 18.0, "bold": False, "color_hex": ""}
            size_pt = float(style.get("size_pt") or 18.0)
            if bold is None:
                bold = bool(style.get("bold"))
            if not color and style.get("color_hex"):
                color = style["color_hex"]
        align = str(getattr(paragraph, "alignment", "") or "")
        bullet_char = ""
        marl_in = 0.0
        indent_in = 0.0
        try:
            p_pr = paragraph._p.pPr
            if p_pr is not None:
                marl_in = float(p_pr.get("marL") or 0) / 914400.0
                indent_in = float(p_pr.get("indent") or 0) / 914400.0
                bu_char = p_pr.find(CORE._qn("a:buChar"))
                if bu_char is not None:
                    bullet_char = bu_char.get("char") or "•"
                if p_pr.find(CORE._qn("a:buNone")) is not None:
                    bullet_char = ""
        except Exception:
            pass
        paragraphs.append(
            {
                "text": text,
                "size": size_pt,
                "bold": bool(bold),
                "color": color or ctx["text_default"],
                "align": align,
                "space": float(paragraph.space_after.pt) if paragraph.space_after is not None else 4.0,
                "level": int(getattr(paragraph, "level", 0) or 0),
                "bullet": bullet_char,
                "marl_px": marl_in * ctx["pt_to_px"] * 72.0,
                "indent_px": indent_in * ctx["pt_to_px"] * 72.0,
            }
        )
    if not paragraphs:
        return
    # layout
    rendered: List[Dict[str, Any]] = []
    total_h = 0.0
    for paragraph in paragraphs:
        if paragraph.get("blank"):
            total_h += 8
            continue
        size_px = paragraph["size"] * ctx["pt_to_px"]
        font = _font("heading" if paragraph["bold"] else "body", size_px, ctx["config"])
        marl_px = paragraph.get("marl_px", 0.0)
        indent_px = paragraph.get("indent_px", 0.0)
        lines = _text_lines(draw, paragraph["text"], font, max(10, box_w - left_pad - right_pad - marl_px))
        if not lines:
            continue
        line_h = size_px * 1.28
        for line_index, line in enumerate(lines):
            rendered.append(
                {
                    "text": line,
                    "font": font,
                    "size_px": size_px,
                    "line_h": line_h,
                    "color": paragraph["color"],
                    "align": paragraph["align"],
                    "indent": marl_px,
                    "bullet": paragraph.get("bullet", "") if line_index == 0 else "",
                    "bullet_x": marl_px + indent_px,
                }
            )
            total_h += line_h
        total_h += paragraph["space"] * ctx["pt_to_px"]
    # vertical anchoring
    try:
        anchor = str(shape.text_frame.vertical_anchor)
    except Exception:
        anchor = "TOP (1)"
    y = y0 + 4
    if "MIDDLE" in anchor:
        y = y0 + max(0.0, (box_h - total_h) / 2.0)
    elif "BOTTOM" in anchor:
        y = y0 + max(0.0, box_h - total_h - 2)
    for item in rendered:
        if y > y1:
            break
        text_width = draw.textlength(item["text"], font=item["font"])
        if "CENTER" in item["align"]:
            x = x0 + left_pad + max(0.0, (box_w - left_pad - right_pad - text_width) / 2.0)
        elif "RIGHT" in item["align"]:
            x = x1 - right_pad - text_width
        else:
            x = x0 + left_pad + item["indent"]
        if item.get("bullet"):
            draw.text((x0 + left_pad + item.get("bullet_x", 0.0), y), str(item["bullet"]),
                      font=item["font"], fill=_hex_tuple(item["color"]))
        draw.text((x, y), item["text"], font=item["font"], fill=_hex_tuple(item["color"]))
        y += item["line_h"]


def _draw_table(draw, shape, ctx: Dict[str, Any]) -> None:
    table = shape.table
    x0, y0, x1, y1 = ctx["rect"](shape)
    rows = len(table.rows)
    columns = len(table.columns)
    if not rows or not columns or x1 - x0 < 10 or y1 - y0 < 10:
        return
    column_widths = [table.columns[index].width for index in range(columns)]
    total_w = sum(column_widths) or 1
    row_heights = [table.rows[index].height for index in range(rows)]
    total_h = sum(row_heights) or 1
    font_px = max(9, int(round(11 * ctx["pt_to_px"])))
    text_font = _font("body", font_px, ctx["config"])
    head_font = _font("heading", font_px, ctx["config"])
    header_fill = (11, 37, 69)
    body_fill = (255, 255, 255)
    alt_fill = (242, 245, 249)
    y = y0
    for row_index in range(rows):
        row_h = (row_heights[row_index] / total_h) * (y1 - y0)
        x = x0
        for column_index in range(columns):
            column_w = (column_widths[column_index] / total_w) * (x1 - x0)
            cell = table.cell(row_index, column_index)
            fill = header_fill if row_index == 0 else (alt_fill if row_index % 2 == 0 else body_fill)
            try:
                if cell.fill.type is not None:
                    rgb = cell.fill.fore_color.rgb
                    fill = (rgb[0], rgb[1], rgb[2])
            except Exception:
                pass
            draw.rectangle([x, y, x + column_w, y + row_h], fill=fill, outline=(201, 212, 224))
            text = cell.text.strip()
            if text:
                font = head_font if row_index == 0 else text_font
                ink = (255, 255, 255) if row_index == 0 else (19, 32, 46)
                try:
                    for paragraph in cell.text_frame.paragraphs:
                        for run in paragraph.runs:
                            rgb = run.font.color.rgb
                            ink = (rgb[0], rgb[1], rgb[2])
                            break
                        break
                except Exception:
                    pass
                if row_index == 0 and ink == (19, 32, 46):
                    ink = _hex_tuple(_readable(ink))
                lines = _text_lines(draw, text, font, max(10, column_w - 14))
                line_h = font_px * 1.3
                ty = y + (row_h - line_h * len(lines[:2])) / 2.0
                for line in lines[:2]:
                    draw.text((x + 7, ty), line, font=font, fill=ink)
                    ty += line_h
            x += column_w
        y += row_h


def _readable(colour) -> str:
    """Black or white hex, whichever contrasts more with the given colour."""

    hex_value = "#%02X%02X%02X" % (colour[0], colour[1], colour[2])
    return "FFFFFF" if CORE.contrast_ratio("FFFFFF", hex_value) >= CORE.contrast_ratio("000000", hex_value) else "000000"


def _short_label(text: str, limit: int = 14) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _draw_chart(draw, shape, ctx: Dict[str, Any]) -> None:
    chart = shape.chart
    x0, y0, x1, y1 = ctx["rect"](shape)
    width = x1 - x0
    height = y1 - y0
    if width < 20 or height < 20:
        return
    font_px = max(10, int(round(9 * ctx["pt_to_px"])))
    label_font = _font("body", font_px, ctx["config"])
    margin_left = 62
    margin_right = 14
    margin_top = 14
    margin_bottom = 46
    if x1 - x0 < 140 or y1 - y0 < 110:
        return
    plot = (x0 + margin_left, y0 + margin_top, x1 - margin_right, y1 - margin_bottom)
    px0, py0, px1, py1 = plot
    chart_type = str(chart.chart_type)
    try:
        categories = [str(item) for item in chart.plots[0].categories]
    except Exception:
        categories = []
    series_names: List[str] = []
    series_values: List[List[float]] = []
    try:
        for series in chart.plots[0].series:
            series_names.append(str(getattr(series, "name", "") or "Series"))
            series_values.append([float(value) if value is not None else 0.0 for value in series.values])
    except Exception:
        series_values = []
    if not series_values:
        return
    palette = ctx["palette"]
    grid = _hex_tuple(ctx.get("grid_hex") or "#DEE4EB")
    ink = _hex_tuple(ctx.get("axis_hex") or "#5A6B7B")
    axis_ink = ink
    # Prefer the colours actually stored on the chart: points for pie/doughnut,
    # series for cartesian charts; fall back to the theme palette otherwise.
    try:
        point_colours = []
        series_colours = []
        for series in chart.plots[0].series:
            try:
                rgb = series.format.fill.fore_color.rgb
                series_colours.append((rgb[0], rgb[1], rgb[2]))
            except Exception:
                series_colours.append(None)
            try:
                for point in series.points:
                    rgb = point.format.fill.fore_color.rgb
                    point_colours.append((rgb[0], rgb[1], rgb[2]))
            except Exception:
                continue
        if "PIE" in chart_type or "DOUGHNUT" in chart_type:
            if len(point_colours) >= 2:
                palette = point_colours
        elif any(colour is not None for colour in series_colours):
            palette = [colour if colour is not None else palette[index % len(palette)]
                       for index, colour in enumerate(series_colours)]
    except Exception:
        pass
    slice_label = (255, 255, 255) if not ctx.get("dark_canvas") else _hex_tuple(ctx.get("background") or "#000000")

    if "PIE" in chart_type or "DOUGHNUT" in chart_type:
        values = series_values[0]
        total = sum(abs(value) for value in values) or 1.0
        radius = min(px1 - px0, py1 - py0) / 2.0
        cx = (px0 + px1) / 2.0 - radius * 0.55
        cy = (py0 + py1) / 2.0
        box = [cx - radius, cy - radius, cx + radius, cy + radius]
        start = -90.0
        for index, value in enumerate(values):
            extent = 360.0 * abs(value) / total
            colour = palette[index % len(palette)]
            draw.pieslice(box, start=start, end=start + extent, fill=colour, outline=(255, 255, 255))
            mid = math.radians(start + extent / 2.0)
            lx = cx + radius * 0.62 * math.cos(mid)
            ly = cy + radius * 0.62 * math.sin(mid)
            pct = 100.0 * abs(value) / total
            if pct >= 6:
                draw.text((lx - 10, ly - 6), "%.0f%%" % pct, font=label_font, fill=slice_label)
            start += extent
        # legend
        lx = cx + radius + 30
        ly = cy - (len(categories) * (font_px + 10)) / 2.0
        for index, category in enumerate(categories):
            draw.rectangle([lx, ly + 3, lx + 12, ly + 15], fill=palette[index % len(palette)])
            draw.text((lx + 18, ly), _short_label(category, 18), font=label_font, fill=ink)
            ly += font_px + 10
        return

    max_value = max((max(values) if values else 0.0) for values in series_values) or 1.0
    nice_max = max_value * 1.08
    ticks = 4
    for step in range(ticks + 1):
        value = nice_max * step / ticks
        y = py1 - (value / nice_max) * (py1 - py0)
        draw.line([(px0, y), (px1, y)], fill=grid, width=1)
        draw.text((8, y - font_px / 2 + 1), ("%.4g" % value), font=label_font, fill=axis_ink)

    if "BAR" in chart_type and "COLUMN" not in chart_type:
        group_h = (py1 - py0) / max(1, len(categories))
        bar_h = group_h / max(1, len(series_values) + 0.6)
        for index, category in enumerate(categories):
            y = py0 + index * group_h + group_h / 2.0
            draw.text((8, y - font_px / 2), _short_label(category, 12), font=label_font, fill=ink)
        for series_index, values in enumerate(series_values):
            for index, value in enumerate(values):
                y = py0 + index * group_h + series_index * bar_h + bar_h * 0.3
                width = (abs(value) / nice_max) * (px1 - px0)
                draw.rectangle([px0, y, px0 + width, y + bar_h * 0.7], fill=palette[series_index % len(palette)])
        return

    if "LINE" in chart_type or "AREA" in chart_type:
        step = (px1 - px0) / max(1, len(categories) - 1)
        for series_index, values in enumerate(series_values):
            points = []
            for index, value in enumerate(values):
                x = px0 + index * step
                y = py1 - (abs(value) / nice_max) * (py1 - py0)
                points.append((x, y))
            if len(points) >= 2:
                draw.line(points, fill=palette[series_index % len(palette)], width=3, joint="curve")
            for index, (px, py) in enumerate(points):
                draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=palette[series_index % len(palette)])
                label = "%g" % abs(values[index])
                width = draw.textlength(label, font=label_font)
                draw.text((px - width / 2.0, py - font_px - 5), label, font=label_font, fill=ink)
        for index, category in enumerate(categories):
            label = _short_label(category, 12)
            width = draw.textlength(label, font=label_font)
            draw.text((px0 + index * step - width / 2.0, py1 + 8), label, font=label_font, fill=ink)
        _draw_chart_legend(draw, series_names, palette, label_font, px0, y1 - 16, ink)
        return

    # clustered columns
    group_w = (px1 - px0) / max(1, len(categories))
    bar_w = group_w / max(1, len(series_values) + 0.6)
    for series_index, values in enumerate(series_values):
        for index, value in enumerate(values):
            x = px0 + index * group_w + series_index * bar_w + bar_w * 0.3
            bar_h = (abs(value) / nice_max) * (py1 - py0)
            draw.rectangle([x, py1 - bar_h, x + bar_w * 0.72, py1], fill=palette[series_index % len(palette)])
            label = "%g" % abs(value)
            width = draw.textlength(label, font=label_font)
            draw.text((x + bar_w * 0.36 - width / 2.0, py1 - bar_h - font_px - 2), label, font=label_font, fill=ink)
    for index, category in enumerate(categories):
        label = _short_label(category, 12)
        width = draw.textlength(label, font=label_font)
        draw.text((px0 + index * group_w + group_w / 2.0 - width / 2.0, py1 + 8), label, font=label_font, fill=ink)
    if len(series_values) > 1:
        _draw_chart_legend(draw, series_names, palette, label_font, px0, y1 - 16, ink)


def _draw_chart_legend(draw, names: Sequence[str], palette, font, x: float, y: float, ink) -> None:
    cursor = x
    for index, name in enumerate(names):
        draw.rectangle([cursor, y + 2, cursor + 11, y + 13], fill=palette[index % len(palette)])
        draw.text((cursor + 16, y), _short_label(name, 18), font=font, fill=ink)
        try:
            width = draw.textlength(_short_label(name, 18), font=font)
        except Exception:
            width = len(name) * 7
        cursor += 16 + width + 22


def _render_slide(prs, slide, width_px: int, config: Dict[str, Any], index: int):
    from PIL import Image, ImageDraw  # type: ignore

    slide_w_in = CORE.emu_to_inches(prs.slide_width) or 13.333
    slide_h_in = CORE.emu_to_inches(prs.slide_height) or 7.5
    height_px = int(round(width_px * slide_h_in / slide_w_in))
    px_per_in = width_px / slide_w_in
    scheme = CORE.theme_color_scheme(prs)
    background = CORE.slide_background_hex(slide, prs, scheme)
    canvas = Image.new("RGB", (width_px, height_px), _hex_tuple(background))
    draw = ImageDraw.Draw(canvas)

    def rect(shape) -> Tuple[float, float, float, float]:
        return (
            float(CORE.emu_to_inches(shape.left) or 0) * px_per_in,
            float(CORE.emu_to_inches(shape.top) or 0) * px_per_in,
            float(CORE.emu_to_inches(shape.left) or 0) * px_per_in + float(CORE.emu_to_inches(shape.width) or 0) * px_per_in,
            float(CORE.emu_to_inches(shape.top) or 0) * px_per_in + float(CORE.emu_to_inches(shape.height) or 0) * px_per_in,
        )

    palette = []
    theme = None
    try:
        theme = CORE.load_theme(CORE.cfg(config, "defaults.theme", "corporate-blue"))
        palette = [_hex_tuple(color) for color in theme.palette()]
    except Exception:
        palette = [(47, 111, 176), (51, 70, 92), (155, 182, 210)]
    dark_canvas = CORE.relative_luminance(background) < 0.45
    dark_ink = _hex_tuple(background)
    ink_hex = "#E8EFEF" if dark_canvas else "#13202E"
    axis_hex = "#9FB3B5" if dark_canvas else "#788696"
    grid_hex = "#27424A" if dark_canvas else "#DEE4EB"
    ctx = {
        "prs": prs,
        "slide": slide,
        "config": config,
        "scheme": scheme,
        "rect": rect,
        "pt_to_px": px_per_in / 72.0,
        "border": _hex_tuple(grid_hex),
        "text_default": ink_hex,
        "palette": palette,
        "theme": theme,
        "axis_hex": axis_hex,
        "grid_hex": grid_hex,
        "dark_canvas": dark_canvas,
        "background": background,
    }

    for shape in CORE.iter_shapes(slide.shapes):
        shape_type = str(getattr(shape, "shape_type", ""))
        try:
            if shape_type.startswith("PICTURE"):
                blob = shape.image.blob
                with Image.open(io.BytesIO(blob)) as image:
                    image = image.convert("RGB")
                    x0, y0, x1, y1 = rect(shape)
                    target_w = max(1, int(x1 - x0))
                    target_h = max(1, int(y1 - y0))
                    image.thumbnail((target_w, target_h), Image.LANCZOS)
                    canvas.paste(image, (int(x0) + (target_w - image.width) // 2, int(y0) + (target_h - image.height) // 2))
                continue
            fill = CORE.shape_fill_hex(shape, scheme)
            if fill and shape_type.startswith(("AUTO_SHAPE", "SHAPE")):
                x0, y0, x1, y1 = rect(shape)
                name = str(getattr(shape, "name", "")).lower()
                rounded = "round" in str(getattr(shape, "auto_shape_type", "")).lower()
                if rounded:
                    draw.rounded_rectangle([x0, y0, x1, y1], radius=max(2, int(min(x1 - x0, y1 - y0) * 0.1)), fill=_hex_tuple(fill))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=_hex_tuple(fill))
                _ = name
            elif fill and shape_type.startswith(("FREEFORM", "LINE", "CALLOUT")):
                x0, y0, x1, y1 = rect(shape)
                draw.rectangle([x0, y0, x1, y1], fill=_hex_tuple(fill))
            if getattr(shape, "has_table", False):
                _draw_table(draw, shape, ctx)
                continue
            if getattr(shape, "has_chart", False):
                _draw_chart(draw, shape, ctx)
                continue
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                _draw_text_frame(draw, shape, ctx)
        except Exception:
            continue
    return canvas


def render_previews(
    path: str,
    out_dir: str,
    *,
    config: Dict[str, Any],
    slides: Optional[Sequence[int]] = None,
    width_px: Optional[int] = None,
    log=None,
    deadline=None,
    core=None,
) -> Dict[str, Any]:
    """Render approximate PNG previews for the requested slides."""

    core = core or CORE
    core.require_pptx()
    core.require_pil()
    source = core.resolve_input(path)
    prs = core.open_presentation(source)
    os.makedirs(out_dir, exist_ok=True)
    requested_width = int(width_px or core.cfg(config, "render.width_px", 1280) or 1280)
    scale = float(core.cfg(config, "render.preview_scale", 1.0) or 1.0)
    width = max(480, min(3840, int(round(requested_width * scale))))
    max_slides = int(core.cfg(config, "render.max_preview_slides", 60) or 60)
    total = len(prs.slides)
    selection = list(slides) if slides else list(range(1, min(total, max_slides) + 1))
    if any(index < 1 or index > total for index in selection):
        raise core.ValidationError("Requested preview slides are outside the deck range 1..%d." % total)
    files: List[Dict[str, Any]] = []
    for index in selection:
        if deadline:
            deadline.check("rendering slide %d" % index)
        canvas = _render_slide(prs, prs.slides[index - 1], width, config, index)
        target = os.path.join(out_dir, "slide-%03d.png" % index)
        canvas.save(target, format="PNG", optimize=True)
        files.append({"slide": index, "file": os.path.basename(target), "width_px": canvas.width, "height_px": canvas.height})
    if log:
        log.info("previews rendered", slides=len(files), width_px=width)
    return {
        "file": os.path.basename(source),
        "backend": "internal-approximate",
        "out_dir": out_dir,
        "width_px": width,
        "slide_count": total,
        "rendered": files,
        "note": "Approximate layout render: geometry, text wrapping, and rough colour only; not pixel-accurate.",
    }


def previews_to_pdf(png_paths: Sequence[str], out_pdf: str) -> str:
    """Combine rendered slide PNGs into a multi-page PDF."""

    from PIL import Image  # type: ignore

    if not png_paths:
        raise CORE.ValidationError("No preview images were produced, so no PDF can be assembled.")
    images = []
    for path in png_paths:
        with Image.open(path) as image:
            images.append(image.convert("RGB"))
    directory = os.path.dirname(os.path.abspath(out_pdf)) or "."
    os.makedirs(directory, exist_ok=True)
    images[0].save(out_pdf, "PDF", save_all=True, append_images=images[1:], resolution=150.0)
    return out_pdf


# ---------------------------------------------------------------------------
# LibreOffice export
# ---------------------------------------------------------------------------

def _run(command: Sequence[str], *, timeout: float, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CORE.AgentError(
            "External renderer timed out after %.0fs." % timeout,
            hint="Close other office processes, raise export.timeout_seconds, or export fewer slides.",
            details={"command": " ".join(command)},
        )
    except FileNotFoundError as exc:
        raise CORE.DependencyError("External renderer binary was not found: %s" % exc)


def export_pdf(
    path: str,
    out_pdf: str,
    *,
    config: Dict[str, Any],
    log=None,
    core=None,
) -> Dict[str, Any]:
    """Convert a presentation to PDF with LibreOffice (faithful rendering)."""

    core = core or CORE
    source = core.resolve_input(path)
    destination = core.resolve_output(out_pdf, overwrite=True, exts=(".pdf",), label="output PDF")
    binary = find_soffice(config)
    if not binary:
        raise core.DependencyError(
            "Faithful PDF export needs a LibreOffice/soffice binary, which is not installed here.",
            hint=(
                "Install LibreOffice (for example 'sudo apt-get install libreoffice-impress' on Debian/Ubuntu, "
                "'brew install --cask libreoffice' on macOS, or the Windows installer) and set "
                "export.libreoffice_binary in the config if it is not on PATH. "
                "Meanwhile use 'export --format preview-pdf' for an approximate offline PDF."
            ),
            details={"searched": core.cfg(config, "export.soffice_candidates", [])},
        )
    timeout = float(core.cfg(config, "export.timeout_seconds", 240) or 240)
    workdir = tempfile.mkdtemp(prefix="pptx-agent-lo-")
    profile = os.path.join(workdir, "profile")
    filtered = "%s:%s" % ("pdf", core.cfg(config, "export.pdf_filter", "impress_pdf_Export"))
    command = [
        binary,
        "--headless",
        "--norestore",
        "--invisible",
        "-env:UserInstallation=file://%s" % profile,
        "--convert-to",
        filtered,
        "--outdir",
        workdir,
        source,
    ]
    try:
        completed = _run(command, timeout=timeout)
        produced = os.path.join(workdir, os.path.splitext(os.path.basename(source))[0] + ".pdf")
        if not os.path.isfile(produced):
            stderr = (completed.stderr or b"").decode("utf-8", "replace")[:800]
            raise core.AgentError(
                "LibreOffice did not produce a PDF.",
                hint="Check that the input opens in LibreOffice and that the profile folder is writable.",
                details={"returncode": completed.returncode, "stderr": stderr},
            )
        os.replace(produced, destination)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if log:
        log.info("pdf exported", output=os.path.basename(destination))
    return {
        "file": os.path.basename(source),
        "output": os.path.basename(destination),
        "output_path": destination,
        "bytes": os.path.getsize(destination),
        "backend": "libreoffice",
        "binary": binary,
    }


def export_pngs(
    path: str,
    out_dir: str,
    *,
    config: Dict[str, Any],
    dpi: int = 150,
    log=None,
    core=None,
) -> Dict[str, Any]:
    """Export one PNG per slide through LibreOffice + a PDF rasterizer."""

    core = core or CORE
    source = core.resolve_input(path)
    os.makedirs(out_dir, exist_ok=True)
    rasterizer = find_pdftoppm()
    if not find_soffice(config):
        raise core.DependencyError(
            "PNG export needs LibreOffice; it is not installed here.",
            hint="Install LibreOffice, or use 'export --format preview-png' for approximate PNGs from the internal renderer.",
        )
    if not rasterizer:
        raise core.DependencyError(
            "PNG export needs a PDF rasterizer (pdftoppm/pdftocairo/mutool); none was found.",
            hint="Install poppler-utils, or use 'export --format preview-png' instead.",
            details={"soffice": find_soffice(config)},
        )
    workdir = tempfile.mkdtemp(prefix="pptx-agent-png-")
    try:
        pdf_path = os.path.join(workdir, "deck.pdf")
        export_pdf(source, pdf_path, config=config, log=log, core=core)
        if os.path.basename(rasterizer) == "mutool":
            prefix = os.path.join(out_dir, "slide")
            command = [rasterizer, "draw", "-r", str(dpi), "-o", prefix + "-%03d.png", pdf_path]
            _run(command, timeout=float(core.cfg(config, "export.timeout_seconds", 240) or 240))
            produced = sorted(
                os.path.join(out_dir, name) for name in os.listdir(out_dir) if name.startswith("slide-") and name.endswith(".png")
            )
        else:
            prefix = os.path.join(out_dir, "slide")
            command = [rasterizer, "-png", "-r", str(dpi), pdf_path, prefix]
            if os.path.basename(rasterizer) == "pdftocairo":
                command = [rasterizer, "-png", "-r", str(dpi), pdf_path, prefix]
            _run(command, timeout=float(core.cfg(config, "export.timeout_seconds", 240) or 240))
            produced = sorted(
                os.path.join(out_dir, name) for name in os.listdir(out_dir) if name.startswith("slide") and name.endswith(".png")
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not produced:
        raise core.AgentError("The PDF rasterizer produced no PNG files.", details={"rasterizer": rasterizer})
    return {
        "file": os.path.basename(source),
        "out_dir": out_dir,
        "backend": "libreoffice+%s" % os.path.basename(rasterizer),
        "dpi": dpi,
        "files": [{"file": os.path.basename(item), "bytes": os.path.getsize(item)} for item in produced],
    }


def export_preview_pdf(
    path: str,
    out_pdf: str,
    *,
    config: Dict[str, Any],
    slides: Optional[Sequence[int]] = None,
    log=None,
    deadline=None,
    core=None,
) -> Dict[str, Any]:
    """Build an approximate PDF from internal previews (no external tools)."""

    core = core or CORE
    core.require_pil()
    workdir = tempfile.mkdtemp(prefix="pptx-agent-previewpdf-")
    try:
        result = render_previews(path, workdir, config=config, slides=slides, log=log, deadline=deadline, core=core)
        pngs = [os.path.join(workdir, item["file"]) for item in result["rendered"]]
        destination = core.resolve_output(out_pdf, overwrite=True, exts=(".pdf",), label="output PDF")
        previews_to_pdf(pngs, destination)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {
        "file": os.path.basename(core.resolve_input(path)),
        "output": os.path.basename(destination),
        "output_path": destination,
        "bytes": os.path.getsize(destination),
        "slides": len(pngs),
        "backend": "internal-approximate",
        "note": "Approximate PDF built from the internal renderer; use LibreOffice export for faithful reproduction.",
    }
