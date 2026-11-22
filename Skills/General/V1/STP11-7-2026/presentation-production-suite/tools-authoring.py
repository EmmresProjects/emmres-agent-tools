#!/usr/bin/env python3
"""Authoring and repair engine for the presentation production suite.

Supporting library behind tools-presentation-suite.py. Every function here
except the planners takes an open python-pptx ``Presentation`` plus the analyzed
DeckCtx and mutates the presentation in place. The CLI owns file I/O, backups,
approval flags, verification, and reporting.

Design rules enforced in this module
------------------------------------
1. Idempotent: running the same operation twice produces the same geometry. Each
   transform snaps to the same target it computed the first time, and repairs
   skip objects that already satisfy the rule.
2. Clamped: no transform pushes an object off the canvas; height/width growth is
   limited to the safe area, so a repair never creates a new defect.
3. Reversible: callers snapshot the package first (see the CLI snapshot/repair
   commands) and repair reports list every change with before/after values.
4. No network, no shell, no external renderer. Only python-pptx object edits.

Operations
----------
repair_bounds           pull partially off-canvas objects back onto the canvas
repair_safe_margin      move objects inside the configured safe area
repair_connectors       re-attach detached connector endpoints to nearest shapes
repair_type_min         raise text below the profile minimum to the minimum
repair_overflow         grow text frames (inside the safe area) to fit content
repair_contrast         darken/lighten text to reach the required ratio
repair_alt_text         write deterministic alt text from object context
repair_reading_order    re-sequence slide shapes to visual reading order
repair_effects          remove accidental shadow/glow/soft-edge effects
normalize_alignment     snap near-aligned edges to a shared guide
normalize_spacing       equalize gaps between repeated objects on one axis
normalize_colors        map near-duplicate colours to a canonical token
scrub_metadata          clear personal document properties
strip_notes             remove speaker notes (external release)
redact_matches          replace sensitive text matches with a token
set_hidden_slides       show/hide slides by index for a release
Every mutation appends to ``changes`` so the CLI can report exactly what moved.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import deckmodel as dm

try:  # python-pptx is optional at import time so read-only commands still work without it
    from pptx.dml.color import RGBColor
    from pptx.util import Pt
    PPTX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the dependency-absent self-test
    RGBColor = Pt = None  # type: ignore[assignment]
    PPTX_AVAILABLE = False

EMU = dm.EMU_PER_PT
MAX_GROWTH_PT = 240  # never grow a text frame by more than this in one repair


class AuthoringError(Exception):
    """Raised when an operation cannot be applied safely."""


def require_pptx() -> None:
    """Fail with one clear message instead of an ImportError deep in an edit."""
    if not PPTX_AVAILABLE:
        raise AuthoringError(
            "python-pptx is required to edit presentations (python -m pip install \"python-pptx>=0.6.21\"). "
            "Read-only commands (inspect, audit, analyze, report, extract-text, convert-units, catalog, "
            "validate-config, capabilities, selfcheck) work without it.")


# --------------------------------------------------------------------- helpers
def _slides(prs):
    return list(prs.slides)


def _top_level_shapes(slide):
    """Top-level shapes only: group children are handled through their group."""
    return [sh for sh in slide.shapes]


def _obj_for(deck_ctx, slide_index: int, shape_id: int):
    for s in deck_ctx.slides:
        if s.index == slide_index:
            for o in s.objects:
                if o.id == shape_id:
                    return o
    return None


def _record(changes: List[Dict[str, Any]], **kw) -> None:
    changes.append(kw)


def _set_geom(shape, left=None, top=None, width=None, height=None) -> None:
    if left is not None:
        shape.left = int(round(left))
    if top is not None:
        shape.top = int(round(top))
    if width is not None:
        shape.width = max(1, int(round(width)))
    if height is not None:
        shape.height = max(1, int(round(height)))


def _slide_canvas(prs) -> Tuple[int, int]:
    return int(prs.slide_width or 0), int(prs.slide_height or 0)


def _safe_area(prs, th: Dict[str, Any]) -> Tuple[int, int, int, int]:
    W, H = _slide_canvas(prs)
    m = int(float(th.get("safe_margin_pt", 28)) * EMU)
    b = int(float(th.get("safe_bottom_pt", 34)) * EMU)
    return m, m, max(m, W - m), max(m, H - b)


def _is_full_bleed(shape, prs) -> bool:
    W, H = _slide_canvas(prs)
    try:
        return (shape.width or 0) >= W * 0.98 and (shape.height or 0) >= H * 0.98
    except Exception:
        return False


# ------------------------------------------------------------------- repairs
def repair_bounds(prs, deck_ctx, cfg, changes, th) -> int:
    """Pull partially or fully off-canvas objects back onto the canvas."""
    require_pptx()
    W, H = _slide_canvas(prs)
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            try:
                left, top, w, h = shape.left or 0, shape.top or 0, shape.width or 0, shape.height or 0
            except Exception:
                continue
            if w <= 0 or h <= 0 or _is_full_bleed(shape, prs):
                continue
            new_left, new_top = left, top
            if w <= W:
                if left < 0:
                    new_left = 0
                elif left + w > W:
                    new_left = W - w
            else:  # object wider than the canvas: shrink to the canvas
                new_left = 0
                _set_geom(shape, width=W)
                w = W
            if h <= H:
                if top < 0:
                    new_top = 0
                elif top + h > H:
                    new_top = H - h
            else:
                new_top = 0
                _set_geom(shape, height=H)
                h = H
            if (new_left, new_top) != (left, top):
                _set_geom(shape, left=new_left, top=new_top)
                _record(changes, op="repair_bounds", slide=si, object=shape.name, id=shape.shape_id,
                        before={"x_pt": round(left / EMU, 2), "y_pt": round(top / EMU, 2)},
                        after={"x_pt": round(new_left / EMU, 2), "y_pt": round(new_top / EMU, 2)})
                count += 1
    return count


def repair_safe_margin(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    sl, st, sr, sb = _safe_area(prs, th)
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            try:
                left, top, w, h = shape.left or 0, shape.top or 0, shape.width or 0, shape.height or 0
            except Exception:
                continue
            if w <= 0 or h <= 0 or _is_full_bleed(shape, prs):
                continue
            # only nudge objects that cross the safe line but are otherwise sane
            new_left, new_top = left, top
            if left < sl and left + w <= (sr - sl) + sl:
                new_left = sl
            if left + w > sr and w <= sr - sl:
                new_left = max(sl, sr - w)
            if top < st and h <= sb - st:
                new_top = st
            if top + h > sb and h <= sb - st:
                new_top = max(st, sb - h)
            if (new_left, new_top) != (left, top):
                _set_geom(shape, left=new_left, top=new_top)
                _record(changes, op="repair_safe_margin", slide=si, object=shape.name, id=shape.shape_id,
                        before={"x_pt": round(left / EMU, 2), "y_pt": round(top / EMU, 2)},
                        after={"x_pt": round(new_left / EMU, 2), "y_pt": round(new_top / EMU, 2)})
                count += 1
    return count


def repair_connectors(prs, deck_ctx, cfg, changes, th) -> int:
    """Attach loose connector endpoints to the nearest shape edge."""
    require_pptx()
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        shapes = [sh for sh in _top_level_shapes(slide) if sh.width and sh.height]
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        for shape in _top_level_shapes(slide):
            if shape.shape_type != MSO_SHAPE_TYPE.LINE:
                continue
            begin = getattr(shape, "begin_connect", None)
            end = getattr(shape, "end_connect", None)
            for which, conn in (("begin", begin), ("end", end)):
                if conn is None or conn.connected_shape is not None:
                    continue
                if which == "begin":
                    px, py = shape.left or 0, shape.top or 0
                else:
                    px, py = (shape.left or 0) + (shape.width or 0), (shape.top or 0) + (shape.height or 0)
                best, best_d = None, None
                for cand in shapes:
                    if cand is shape or cand.name == shape.name:
                        continue
                    cx, cy = (cand.left or 0) + (cand.width or 0) / 2, (cand.top or 0) + (cand.height or 0) / 2
                    d = math.hypot(px - cx, py - cy)
                    if best_d is None or d < best_d:
                        best, best_d = cand, d
                if best is None:
                    continue
                try:
                    conn.begin_connect(best, 0) if which == "begin" else conn.end_connect(best, 0)
                    _record(changes, op="repair_connectors", slide=si, object=shape.name, id=shape.shape_id,
                            attached_to=best.name, end=which, distance_pt=round((best_d or 0) / EMU, 1))
                    count += 1
                except Exception as exc:
                    _record(changes, op="repair_connectors", slide=si, object=shape.name, id=shape.shape_id,
                            skipped=f"cannot attach: {exc}")
    return count


def _iter_text_frames(prs):
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            if getattr(shape, "has_text_frame", False) and shape.text_frame is not None:
                yield si, slide, shape, shape.text_frame


def repair_type_min(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    min_pt = float(th.get("min_text_pt", 18))
    footer_pt = float(th.get("min_footer_pt", 10))
    W, H = _slide_canvas(prs)
    count = 0
    for si, slide, shape, tf in _iter_text_frames(prs):
        in_footer = (shape.top or 0) > H - 60 * EMU
        limit = footer_pt if in_footer else min_pt
        if not (tf.text or "").strip():
            continue
        changed = False
        for para in tf.paragraphs:
            for run in para.runs:
                if not (run.text or "").strip():
                    continue
                size = run.font.size.pt if run.font.size else None
                if size is not None and size < limit:
                    run.font.size = Pt(limit)
                    changed = True
                    _record(changes, op="repair_type_min", slide=si, object=shape.name, id=shape.shape_id,
                            before={"size_pt": size}, after={"size_pt": limit},
                            text=(run.text or "")[:60])
        if changed:
            count += 1
    return count


def repair_overflow(prs, deck_ctx, cfg, changes, th) -> int:
    """Grow text frames down toward the safe bottom until estimated text fits."""
    require_pptx()
    sl, st, sr, sb = _safe_area(prs, th)
    count = 0
    for si, slide, shape, tf in _iter_text_frames(prs):
        if not (tf.text or "").strip():
            continue
        left, top, w, h = shape.left or 0, shape.top or 0, shape.width or 0, shape.height or 0
        if w <= 0 or h <= 0:
            continue
        inner_w = (w - (tf.margin_left or 0) - (tf.margin_right or 0)) / EMU
        need_pt = 0.0
        for para in tf.paragraphs:
            line = "".join(r.text or "" for r in para.runs) or para.text or ""
            if not line.strip():
                continue
            size = max([r.font.size.pt for r in para.runs if r.font.size] or [18.0])
            lines = dm.wrap_line_count(line, size, max(1.0, inner_w))
            need_pt += lines * size * 1.22 + size * 0.35
        inner_h = (h - (tf.margin_top or 0) - (tf.margin_bottom or 0)) / EMU
        if need_pt <= inner_h * 1.02:
            continue
        target_h_emu = int((need_pt + 2) * EMU) + (tf.margin_top or 0) + (tf.margin_bottom or 0)
        max_bottom = min(sb, top + int(MAX_GROWTH_PT * EMU) + h)
        new_h = min(target_h_emu, max_bottom - top)
        if new_h <= h + 0.5 * EMU:
            _record(changes, op="repair_overflow", slide=si, object=shape.name, id=shape.shape_id,
                    skipped="no room to grow within the safe area; shorten the text instead")
            continue
        _set_geom(shape, height=new_h)
        _record(changes, op="repair_overflow", slide=si, object=shape.name, id=shape.shape_id,
                before={"h_pt": round(h / EMU, 2)}, after={"h_pt": round(new_h / EMU, 2)},
                estimated_need_pt=round(need_pt, 1))
        count += 1
    return count


def accessible_fill(base_hex: str, text_hex: str = "FFFFFF", required: float = 4.5,
                    prefer_dark: bool = True) -> str:
    """Return a fill colour, starting from base_hex, that meets a contrast target.

    Authoring must not emit its own accessibility defects, so every generated
    band/card/chevron fill is shifted in small lightness steps until the planned
    text colour passes the requested ratio (or the shift budget is exhausted).
    """
    colour = (base_hex or "1F5C9E").lstrip("#").upper()
    if dm.contrast_ratio(text_hex, colour) is None:
        return colour
    if (dm.contrast_ratio(text_hex, colour) or 0) >= required:
        return colour
    for _ in range(60):
        colour = _shift_lightness(colour, toward_dark=prefer_dark, step=6)
        if (dm.contrast_ratio(text_hex, colour) or 0) >= required:
            return colour
    return colour


def _shift_lightness(hex_colour: str, toward_dark: bool, step: int = 12) -> str:
    rgb = dm.hex_to_rgb(hex_colour)
    if rgb is None:
        return hex_colour
    if toward_dark:
        rgb = tuple(max(0, c - step) for c in rgb)
    else:
        rgb = tuple(min(255, c + step) for c in rgb)
    return dm.rgb_to_hex(rgb)


def repair_contrast(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    body_req = float(th.get("contrast_body", 4.5))
    large_req = float(th.get("contrast_large", 3.0))
    large_pt = float(th.get("large_text_pt", 24))
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        sctx = next((s for s in deck_ctx.slides if s.index == si), None)
        bg = None
        if sctx is not None:
            bg = sctx.background or (deck_ctx.theme_colors.get("lt1") or "FFFFFF").upper()
        if not bg or dm.relative_luminance(bg) is None:
            continue
        dark_bg = (dm.relative_luminance(bg) or 1.0) < 0.4
        for shape in _top_level_shapes(slide):
            if not getattr(shape, "has_text_frame", False) or shape.text_frame is None:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if not (run.text or "").strip():
                        continue
                    try:
                        current = str(run.font.color.rgb) if run.font.color and run.font.color.type is not None else None
                    except Exception:
                        current = None
                    if not current or dm.relative_luminance(current) is None:
                        continue
                    size = run.font.size.pt if run.font.size else 18.0
                    req = large_req if size >= large_pt else body_req
                    colour = current.upper()
                    steps = 0
                    while steps < 20 and (dm.contrast_ratio(colour, bg) or 0) < req:
                        colour = _shift_lightness(colour, toward_dark=not dark_bg)
                        steps += 1
                    if steps == 0:
                        continue
                    if (dm.contrast_ratio(colour, bg) or 0) < req:
                        _record(changes, op="repair_contrast", slide=si, object=shape.name, id=shape.shape_id,
                                skipped="no passing shade reachable by lightness shift")
                        continue
                    run.font.color.rgb = RGBColor.from_string(colour)
                    count += 1
                    _record(changes, op="repair_contrast", slide=si, object=shape.name, id=shape.shape_id,
                            before={"color": current.upper(),
                                    "ratio": dm.contrast_ratio(current.upper(), bg)},
                            after={"color": colour, "ratio": dm.contrast_ratio(colour, bg)},
                            background=bg, text=(run.text or "")[:60])
    return count


def _alt_for(shape, kind: str = "") -> str:
    """Deterministic, content-derived alt text (never invents semantic meaning)."""
    if getattr(shape, "has_text_frame", False) and (shape.text_frame.text or "").strip():
        snippet = " ".join(shape.text_frame.text.split())[:110]
        return f"Text panel: {snippet}"
    try:
        if getattr(shape, "has_chart", False) and shape.has_chart:
            ch = shape.chart
            n = len(list(ch.series))
            title = ch.chart_title.text_frame.text if ch.has_title else ""
            return f"Chart ({ch.chart_type.name.lower() if hasattr(ch.chart_type,'name') else 'chart'}) with {n} series" + \
                   (f": {title}" if title.strip() else "")
    except Exception:
        pass
    try:
        if getattr(shape, "has_table", False) and shape.has_table:
            t = shape.table
            return f"Table with {len(t.rows)} rows and {len(t.columns)} columns"
    except Exception:
        pass
    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                img = shape.image
                return f"Image, {img.size[0]} by {img.size[1]} pixels" + (f" ({img.filename})" if img.filename else "")
            except Exception:
                return "Image"
    except Exception:
        pass
    return f"{shape.shape_type} element" if shape.shape_type else "Graphic element"


def repair_alt_text(prs, deck_ctx, cfg, changes, th, decorative_names: bool = True) -> int:
    require_pptx()
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            needs = False
            try:
                from pptx.enum.shapes import MSO_SHAPE_TYPE
                needs = shape.shape_type in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.CHART, MSO_SHAPE_TYPE.TABLE)
            except Exception:
                pass
            if not needs:
                continue
            cNvPr = None
            try:
                cNvPr = shape._element._nvXxPr.cNvPr
            except Exception:
                cNvPr = None
            if cNvPr is None:
                continue
            existing = (cNvPr.get("descr") or "").strip()
            # a bare file name is not usable alt text; overwrite it
            usable = bool(existing) and not re.fullmatch(
                r"[^\\/]+\.(png|jpe?g|gif|bmp|tiff?|svg|emf|wmf|webp)", existing, re.I)
            if usable:
                continue
            if decorative_names and re.search(r"(?i)\b(background|bg|decorative|spacer|divider|gray[-_ ]?line)\b", shape.name):
                cNvPr.set("descr", "")
                try:
                    cNvPr.set("title", "Decorative")
                except Exception:
                    pass
                _record(changes, op="repair_alt_text", slide=si, object=shape.name, id=shape.shape_id,
                        action="marked_decorative")
                count += 1
                continue
            text = _alt_for(shape)
            cNvPr.set("descr", text)
            _record(changes, op="repair_alt_text", slide=si, object=shape.name, id=shape.shape_id,
                    action="set_alt_text", alt_text=text)
            count += 1
    return count


def repair_reading_order(prs, deck_ctx, cfg, changes, th) -> int:
    """Re-sequence slide shapes so creation order matches visual order."""
    require_pptx()
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        shapes = list(slide.shapes)
        visual = sorted(shapes, key=lambda sh: (round((sh.top or 0) / (24 * EMU)), sh.left or 0))
        if [id(s) for s in shapes] == [id(s) for s in visual]:
            continue
        tree = slide.shapes._spTree
        for sh in shapes:
            el = sh._element
            tree.remove(el)
        for sh in visual:
            tree.append(sh._element)
        _record(changes, op="repair_reading_order", slide=si,
                before=[s.name for s in shapes][:12], after=[s.name for s in visual][:12])
        count += 1
    return count


def repair_effects(prs, deck_ctx, cfg, changes, th, keep_shadow_names: str = "") -> int:
    require_pptx()
    count = 0
    keep = re.compile(keep_shadow_names, re.I) if keep_shadow_names else None
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            if keep and keep.search(shape.name or ""):
                continue
            try:
                spPr = shape._element.spPr
            except Exception:
                continue
            removed = []
            for tag in ("effectLst", "effectDag"):
                for node in spPr.findall(f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}{tag}"):
                    spPr.remove(node)
                    removed.append(tag)
            if removed:
                _record(changes, op="repair_effects", slide=si, object=shape.name, id=shape.shape_id,
                        removed=removed)
                count += 1
    return count


# --------------------------------------------------------------- normalizers
def normalize_alignment(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    tol = float(cfg.get("align_tolerance_pt", 1.5)) * EMU
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        shapes = [sh for sh in _top_level_shapes(slide) if (sh.width or 0) and (sh.height or 0)]
        if len(shapes) < 2 or len(shapes) > 120:
            continue
        clusters: Dict[Tuple[str, int], List[Any]] = defaultdict(list)
        for sh in shapes:
            clusters[("left", round((sh.left or 0) / max(1, tol)))].append(sh)
            clusters[("top", round((sh.top or 0) / max(1, tol)))].append(sh)
        for (edge, bucket), group in clusters.items():
            if len(group) < 2:
                continue
            values = [(sh.left if edge == "left" else sh.top) or 0 for sh in group]
            median = sorted(values)[len(values) // 2]
            if max(values) - min(values) > tol * 3:
                continue
            for sh, val in zip(group, values):
                if val == median:
                    continue
                if edge == "left":
                    _set_geom(sh, left=median)
                else:
                    _set_geom(sh, top=median)
                count += 1
                _record(changes, op="normalize_alignment", slide=si, object=sh.name, id=sh.shape_id,
                        edge=edge, before_pt=round(val / EMU, 2), after_pt=round(median / EMU, 2))
    return count


def normalize_spacing(prs, deck_ctx, cfg, changes, th) -> int:
    """Equalize gaps along the dominant axis of >=3 repeated objects."""
    require_pptx()
    tol = float(cfg.get("spacing_tolerance_pt", 2)) * EMU
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        shapes = [sh for sh in _top_level_shapes(slide) if (sh.width or 0) and (sh.height or 0)]
        if len(shapes) < 3 or len(shapes) > 40:
            continue
        for axis in ("left", "top"):
            ordered = sorted(shapes, key=lambda sh: (sh.left or 0) if axis == "left" else (sh.top or 0))
            gaps = []
            for a, b in zip(ordered, ordered[1:]):
                if axis == "left":
                    gaps.append((b.left or 0) - ((a.left or 0) + (a.width or 0)))
                else:
                    gaps.append((b.top or 0) - ((a.top or 0) + (a.height or 0)))
            if not gaps or min(gaps) < 0 or max(gaps) - min(gaps) < tol:
                continue
            # only equalize when the objects clearly form a row/column
            mean_gap = sum(gaps) / len(gaps)
            if mean_gap <= 0 or max(gaps) - min(gaps) < 0.15 * mean_gap:
                continue
            cursor = (ordered[0].left or 0) if axis == "left" else (ordered[0].top or 0)
            for sh in ordered[1:]:
                cursor += ((ordered[0].width if axis == "left" else ordered[0].height) or 0) + int(mean_gap)
                before = (sh.left or 0) if axis == "left" else (sh.top or 0)
                if abs(before - cursor) < 0.5 * EMU:
                    continue
                if axis == "left":
                    _set_geom(sh, left=cursor)
                else:
                    _set_geom(sh, top=cursor)
                count += 1
                _record(changes, op="normalize_spacing", slide=si, object=sh.name, id=sh.shape_id,
                        axis=axis, before_pt=round(before / EMU, 2), after_pt=round(cursor / EMU, 2),
                        target_gap_pt=round(mean_gap / EMU, 2))
    return count


def normalize_colors(prs, deck_ctx, cfg, changes, th) -> int:
    """Collapse near-duplicate explicit colours onto the most-used variant."""
    require_pptx()
    max_d = float(cfg.get("color_drift_distance", 12))
    usage: Counter = Counter()
    for s, o in deck_ctx.all_objects():
        for c in (o.fill, o.line):
            if c and dm.hex_to_rgb(c):
                usage[c] += 1
        for p in o.paragraphs:
            for r in p:
                if r.color and dm.hex_to_rgb(r.color):
                    usage[r.color] += 1
    canonical: Dict[str, str] = {}
    colours = [c for c, _ in usage.most_common()]
    for i, a in enumerate(colours):
        for b in colours[i + 1:]:
            d = dm.color_distance(a, b)
            if d is not None and d <= max_d and b not in canonical:
                canonical[b] = a
    if not canonical:
        return 0
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            try:
                fill = shape.fill
                if fill.type is not None:
                    rgb = str(fill.fore_color.rgb).upper()
                    if rgb in canonical:
                        fill.fore_color.rgb = RGBColor.from_string(canonical[rgb])
                        count += 1
                        _record(changes, op="normalize_colors", slide=si, object=shape.name, id=shape.shape_id,
                                target="fill", before=rgb, after=canonical[rgb])
            except Exception:
                pass
            if getattr(shape, "has_text_frame", False) and shape.text_frame is not None:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        try:
                            if run.font.color and run.font.color.type is not None:
                                rgb = str(run.font.color.rgb).upper()
                                if rgb in canonical:
                                    run.font.color.rgb = RGBColor.from_string(canonical[rgb])
                                    count += 1
                                    _record(changes, op="normalize_colors", slide=si, object=shape.name,
                                            id=shape.shape_id, target="text_run", before=rgb, after=canonical[rgb])
                        except Exception:
                            continue
    return count


# --------------------------------------------------------------- governance
def scrub_metadata(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    props = prs.core_properties
    fields = ["author", "last_modified_by", "comments", "keywords", "category", "subject", "title"]
    count = 0
    for field in fields:
        try:
            current = getattr(props, field)
        except Exception:
            continue
        if current:
            setattr(props, field, "")
            count += 1
            _record(changes, op="scrub_metadata", field=field, before=str(current)[:60])
    try:
        props.company = ""
    except Exception:
        pass
    return count


def strip_notes(prs, deck_ctx, cfg, changes, th) -> int:
    require_pptx()
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        if not slide.has_notes_slide:
            continue
        tf = slide.notes_slide.notes_text_frame
        if tf is None or not (tf.text or "").strip():
            continue
        chars = len(tf.text)
        tf.clear()
        count += 1
        _record(changes, op="strip_notes", slide=si, removed_chars=chars)
    return count


def redact_matches(prs, deck_ctx, cfg, changes, th, token: str = "[REDACTED]") -> int:
    require_pptx()
    patterns = cfg.get("sensitive_patterns", {}) or {}
    regexes = []
    for name, pat in patterns.items():
        try:
            regexes.append((name, re.compile(pat)))
        except re.error:
            continue
    count = 0
    for si, slide, shape, tf in _iter_text_frames(prs):
        for para in tf.paragraphs:
            for run in para.runs:
                text = run.text or ""
                new = text
                hits = []
                for name, rx in regexes:
                    for m in rx.finditer(new):
                        hits.append(name)
                    new = rx.sub(token, new)
                if new != text:
                    run.text = new
                    count += 1
                    _record(changes, op="redact_matches", slide=si, object=shape.name, id=shape.shape_id,
                            patterns=sorted(set(hits)))
    for si, slide in enumerate(_slides(prs), start=1):
        if not slide.has_notes_slide:
            continue
        tf = slide.notes_slide.notes_text_frame
        if tf is None:
            continue
        text = tf.text or ""
        new = text
        for _name, rx in regexes:
            new = rx.sub(token, new)
        if new != text:
            tf.text = new
            count += 1
            _record(changes, op="redact_matches", slide=si, object="notes")
    return count


def set_hidden_slides(prs, deck_ctx, cfg, changes, th, show: Optional[Sequence[int]] = None,
                      hide: Optional[Sequence[int]] = None) -> int:
    require_pptx()
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        target = None
        if hide and si in hide:
            target = "0"
        if show and si in show:
            target = "1"
        if target is None:
            continue
        if slide._element.get("show") != target:
            slide._element.set("show", target)
            count += 1
            _record(changes, op="set_hidden_slides", slide=si, show=(target == "1"))
    return count


# ------------------------------------------------------------------ authoring
def build_title_slide(prs, deck_ctx, changes, th, title: str, subtitle: str = "",
                      presenter: str = "", date_text: str = "", brand_hex: Optional[str] = None):
    """Create a title slide on the deck canvas using native text boxes."""
    require_pptx()
    from pptx.util import Emu
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    slide = prs.slides.add_slide(layout)
    W, H = _slide_canvas(prs)
    accent = accessible_fill(brand_hex or (deck_ctx.theme_colors.get("accent1") or "1F5C9E"), "FFFFFF", 4.5)
    band = slide.shapes.add_shape(1, Emu(0), Emu(int(H * 0.62)), Emu(W), Emu(int(H * 0.38)))
    try:
        band.fill.solid()
        band.fill.fore_color.rgb = RGBColor.from_string(accent.upper())
        band.line.fill.background()
    except Exception:
        pass
    band.name = "Decorative Band"
    tb = slide.shapes.add_textbox(Emu(int(W * 0.08)), Emu(int(H * 0.24)), Emu(int(W * 0.84)), Emu(int(H * 0.2)))
    tb.name = "Title"
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = title
    tf.paragraphs[0].runs[0].font.size = Pt(float(th.get("title_pt", 40)))
    tf.paragraphs[0].runs[0].font.bold = True
    if subtitle:
        sb = slide.shapes.add_textbox(Emu(int(W * 0.08)), Emu(int(H * 0.46)), Emu(int(W * 0.84)), Emu(int(H * 0.12)))
        sb.name = "Subtitle"
        sb.text_frame.word_wrap = True
        sb.text_frame.text = subtitle
        sb.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("subtitle_pt", 22)))
    foot_bits = [b for b in (presenter, date_text) if b]
    if foot_bits:
        footer_size = max(float(th.get("min_footer_pt", 10)), 12.0)
        fh = int(footer_size * 1.6 * EMU)
        ftop = int(H - float(th.get("safe_bottom_pt", 34)) * EMU - fh)
        fb = slide.shapes.add_textbox(Emu(int(W * 0.08)), Emu(ftop), Emu(int(W * 0.84)), Emu(fh))
        fb.name = "Presenter Line"
        fb.text_frame.word_wrap = True
        fb.text_frame.text = "  |  ".join(foot_bits)
        fb.text_frame.paragraphs[0].runs[0].font.size = Pt(footer_size)
        try:
            fb.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string("FFFFFF")
        except Exception:
            pass
    _record(changes, op="build_title_slide", slide=len(prs.slides), title=title[:80])
    return slide


def build_content_slide(prs, deck_ctx, changes, th, title: str, bullets: Sequence[str],
                        claim: str = "", footnote: str = ""):
    """Create a content slide with a claim title, body bullets and a footer."""
    require_pptx()
    from pptx.util import Emu
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    slide = prs.slides.add_slide(layout)
    W, H = _slide_canvas(prs)
    margin = int(float(th.get("safe_margin_pt", 28)) * EMU)
    title_box = slide.shapes.add_textbox(Emu(margin), Emu(int(H * 0.08)), Emu(W - 2 * margin), Emu(int(H * 0.16)))
    title_box.name = "Title"
    title_box.text_frame.word_wrap = True
    title_box.text_frame.text = title
    title_box.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("slide_title_pt", 30)))
    title_box.text_frame.paragraphs[0].runs[0].font.bold = True
    y = int(H * 0.26)
    if claim:
        cb = slide.shapes.add_textbox(Emu(margin), Emu(y), Emu(W - 2 * margin), Emu(int(H * 0.1)))
        cb.name = "Key Message"
        cb.text_frame.word_wrap = True
        cb.text_frame.text = claim
        cb.text_frame.paragraphs[0].runs[0].font.size = Pt(max(float(th.get("claim_pt", 22)), _body_pt(th)))
        y += int(H * 0.13)
    body = slide.shapes.add_textbox(Emu(margin), Emu(y), Emu(W - 2 * margin), Emu(int(H * 0.45)))
    body.name = "Body"
    tf = body.text_frame
    tf.word_wrap = True
    for i, b in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = f"•  {b}"
        run.font.size = Pt(_body_pt(th))
        para.space_after = Pt(8)
    if footnote:
        fb = slide.shapes.add_textbox(Emu(margin), Emu(H - int(float(th.get("safe_bottom_pt", 34)) * EMU)),
                                      Emu(int(W * 0.7)), Emu(int(H * 0.06)))
        fb.name = "Source Note"
        fb.text_frame.word_wrap = True
        fb.text_frame.text = footnote
        fb.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("footer_pt", 10)))
    _record(changes, op="build_content_slide", title=title[:80], bullets=len(bullets))
    return slide


def _body_pt(th, key: str = "body_pt", floor: float = 18.0) -> float:
    """Authored body text must satisfy the readability floor, so clamp upward."""
    return max(float(th.get(key, floor)), max(floor, float(th.get("min_text_pt", 18))))


def build_kpi_slide(prs, deck_ctx, changes, th, title: str, cards: Sequence[Dict[str, str]], footnote: str = ""):
    """Create a KPI card row slide. Each card: {value, label, delta?, unit?}."""
    require_pptx()
    from pptx.util import Emu
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    slide = prs.slides.add_slide(layout)
    W, H = _slide_canvas(prs)
    margin = int(float(th.get("safe_margin_pt", 28)) * EMU)
    t = slide.shapes.add_textbox(Emu(margin), Emu(int(H * 0.08)), Emu(W - 2 * margin), Emu(int(H * 0.14)))
    t.name = "Title"
    t.text_frame.word_wrap = True
    t.text_frame.text = title
    t.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("slide_title_pt", 30)))
    t.text_frame.paragraphs[0].runs[0].font.bold = True
    n = max(1, len(cards))
    gap = int(0.02 * W)
    card_w = int((W - 2 * margin - gap * (n - 1)) / n)
    card_h = int(H * 0.34)
    top = int(H * 0.34)
    accent = accessible_fill((deck_ctx.theme_colors.get("accent1") or "1F5C9E"), "FFFFFF", 3.0)
    for i, card in enumerate(cards):
        left = margin + i * (card_w + gap)
        box = slide.shapes.add_shape(1, Emu(left), Emu(top), Emu(card_w), Emu(card_h))
        box.name = f"KPI Card {i + 1}"
        try:
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor.from_string("F2F5F9")
            box.line.color.rgb = RGBColor.from_string(accent)
            box.line.width = Pt(1.0)
        except Exception:
            pass
        pad = int(card_w * 0.08)
        vb = slide.shapes.add_textbox(Emu(left + pad), Emu(top + int(card_h * 0.16)),
                                      Emu(card_w - 2 * pad), Emu(int(card_h * 0.36)))
        vb.name = f"KPI Value {i + 1}"
        vb.text_frame.word_wrap = True
        vb.text_frame.text = f"{card.get('value', '')}{card.get('unit', '')}"
        vb.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("kpi_pt", 34)))
        vb.text_frame.paragraphs[0].runs[0].font.bold = True
        lb = slide.shapes.add_textbox(Emu(left + pad), Emu(top + int(card_h * 0.54)),
                                      Emu(card_w - 2 * pad), Emu(int(card_h * 0.2)))
        lb.name = f"KPI Label {i + 1}"
        lb.text_frame.word_wrap = True
        lb.text_frame.text = card.get("label", "")
        lb.text_frame.paragraphs[0].runs[0].font.size = Pt(_body_pt(th))
        if card.get("delta"):
            db = slide.shapes.add_textbox(Emu(left + pad), Emu(top + int(card_h * 0.76)),
                                          Emu(card_w - 2 * pad), Emu(int(card_h * 0.16)))
            db.name = f"KPI Delta {i + 1}"
            db.text_frame.word_wrap = True
            db.text_frame.text = card["delta"]
            db.text_frame.paragraphs[0].runs[0].font.size = Pt(_body_pt(th))
    if footnote:
        fb = slide.shapes.add_textbox(Emu(margin), Emu(H - int(float(th.get("safe_bottom_pt", 34)) * EMU)),
                                      Emu(int(W * 0.8)), Emu(int(H * 0.06)))
        fb.name = "Source Note"
        fb.text_frame.word_wrap = True
        fb.text_frame.text = footnote
        fb.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("footer_pt", 10)))
    _record(changes, op="build_kpi_slide", title=title[:80], cards=len(cards))
    return slide


def build_process_slide(prs, deck_ctx, changes, th, title: str, steps: Sequence[str], footnote: str = ""):
    """Create a linear left-to-right process slide with native chevrons."""
    require_pptx()
    from pptx.util import Emu
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    slide = prs.slides.add_slide(layout)
    W, H = _slide_canvas(prs)
    margin = int(float(th.get("safe_margin_pt", 28)) * EMU)
    t = slide.shapes.add_textbox(Emu(margin), Emu(int(H * 0.08)), Emu(W - 2 * margin), Emu(int(H * 0.14)))
    t.name = "Title"
    t.text_frame.word_wrap = True
    t.text_frame.text = title
    t.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("slide_title_pt", 30)))
    t.text_frame.paragraphs[0].runs[0].font.bold = True
    steps = list(steps)[:6]
    n = max(1, len(steps))
    gap = int(0.012 * W)
    box_w = int((W - 2 * margin - gap * (n - 1)) / n)
    box_h = int(H * 0.3)
    top = int(H * 0.36)
    accent = accessible_fill((deck_ctx.theme_colors.get("accent1") or "1F5C9E"), "FFFFFF", 4.5)
    alternate = accessible_fill(_shift_lightness(accent, toward_dark=False, step=14), "FFFFFF", 4.5)
    for i, step in enumerate(steps):
        left = margin + i * (box_w + gap)
        chev = slide.shapes.add_shape(52, Emu(left), Emu(top), Emu(box_w), Emu(box_h))  # CHEVRON
        chev.name = f"Step {i + 1}"
        try:
            chev.fill.solid()
            chev.fill.fore_color.rgb = RGBColor.from_string(accent if i % 2 == 0 else alternate)
            chev.line.fill.background()
        except Exception:
            pass
        tf = chev.text_frame
        tf.word_wrap = True
        tf.text = f"{i + 1}. {step}"
        for para in tf.paragraphs:
            for run in para.runs:
                run.font.size = Pt(_body_pt(th))
                try:
                    run.font.color.rgb = RGBColor.from_string("FFFFFF")
                except Exception:
                    pass
    if footnote:
        fb = slide.shapes.add_textbox(Emu(margin), Emu(H - int(float(th.get("safe_bottom_pt", 34)) * EMU)),
                                      Emu(int(W * 0.8)), Emu(int(H * 0.06)))
        fb.name = "Source Note"
        fb.text_frame.word_wrap = True
        fb.text_frame.text = footnote
        fb.text_frame.paragraphs[0].runs[0].font.size = Pt(float(th.get("footer_pt", 10)))
    _record(changes, op="build_process_slide", title=title[:80], steps=len(steps))
    return slide


def apply_theme_colors(prs, deck_ctx, changes, colors: Dict[str, str]):
    """Rewrite theme colour slots in place (most robust portable recolour path)."""
    require_pptx()
    from pptx.oxml.ns import qn
    count = 0
    for master in prs.slide_masters:
        theme = master.part.part_related_by(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme")
        if theme is None:
            continue
        xml = theme.blob.decode("utf-8")
        for slot, value in colors.items():
            value = value.lstrip("#").upper()
            pattern = re.compile(rf'(<a:{slot}>\s*<a:srgbClr val=")([0-9A-Fa-f]{{6}})(")')
            new_xml, n = pattern.subn(rf"\g<1>{value}\g<3>", xml)
            if n:
                xml = new_xml
                count += n
                _record(changes, op="apply_theme_colors", slot=slot, after=value)
        try:
            theme._blob = xml.encode("utf-8")
        except Exception as exc:
            _record(changes, op="apply_theme_colors", skipped=f"theme part not writable: {exc}")
    return count


def rescale_canvas(prs, deck_ctx, cfg, changes, th, width_pt: float, height_pt: float):
    """Reflow every slide to a new canvas size, preserving relative positions."""
    require_pptx()
    old_w, old_h = _slide_canvas(prs)
    if old_w <= 0 or old_h <= 0:
        raise AuthoringError("current canvas has no size")
    new_w, new_h = int(width_pt * EMU), int(height_pt * EMU)
    sx, sy = new_w / old_w, new_h / old_h
    for si, slide in enumerate(_slides(prs), start=1):
        for shape in _top_level_shapes(slide):
            try:
                shape.left = int((shape.left or 0) * sx)
                shape.top = int((shape.top or 0) * sy)
                shape.width = max(1, int((shape.width or 0) * sx))
                shape.height = max(1, int((shape.height or 0) * sy))
            except Exception:
                continue
        _record(changes, op="rescale_canvas", slide=si, scale={"x": round(sx, 4), "y": round(sy, 4)})
    prs.slide_width = new_w
    prs.slide_height = new_h
    return len(list(prs.slides))


# -------------------------------------------------------------------- registry
OPERATIONS = {
    # scope name -> (function, needs_approval, description)
    "bounds": (repair_bounds, False, "pull off-canvas objects back onto the canvas"),
    "safe-margin": (repair_safe_margin, False, "move objects inside the configured safe area"),
    "connectors": (repair_connectors, False, "attach detached connector endpoints to nearest shapes"),
    "type-min": (repair_type_min, False, "raise text below the profile minimum type size"),
    "overflow": (repair_overflow, False, "grow text frames that would overflow, inside the safe area"),
    "contrast": (repair_contrast, True, "shift text lightness until WCAG-oriented contrast passes"),
    "alt-text": (repair_alt_text, False, "write content-derived alt text for images/charts/tables"),
    "reading-order": (repair_reading_order, False, "re-sequence shapes to visual reading order"),
    "effects": (repair_effects, True, "remove shadows/glows/bevels"),
    "scrub-metadata": (scrub_metadata, False, "clear personal document properties"),
    "strip-notes": (strip_notes, True, "delete all speaker notes"),
    "redact": (redact_matches, True, "replace sensitive text matches with [REDACTED]"),
}

def normalize_names(prs, deck_ctx, cfg, changes, th) -> int:
    """Rename generic objects to stable semantic identifiers.

    Naming scheme: <role>-<slide>-<index>, where role is derived from the object
    kind, placeholder type and content:
        title, subtitle, body, footer, note, source, image, chart, table,
        connector, card, panel, deco
    Names are deterministic for a given deck, so repeated runs produce the same
    identifiers (idempotent) and later automation can target them.
    """
    require_pptx()
    import re as _re
    count = 0
    for si, slide in enumerate(_slides(prs), start=1):
        counters: Dict[str, int] = defaultdict(int)
        texts = [(sh, (sh.text_frame.text if getattr(sh, "has_text_frame", False) and sh.text_frame else ""))
                 for sh in _top_level_shapes(slide)]
        top_text_y = min([(sh.top or 0) for sh, t in texts if t.strip()], default=0)
        bottom_band = (prs.slide_height or 0) * 0.88
        for sh, text in texts:
            counters_kind = None
            try:
                from pptx.enum.shapes import MSO_SHAPE_TYPE
                if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    counters_kind = "image"
                elif sh.shape_type == MSO_SHAPE_TYPE.CHART or getattr(sh, "has_chart", False):
                    counters_kind = "chart"
                elif sh.shape_type == MSO_SHAPE_TYPE.TABLE or getattr(sh, "has_table", False):
                    counters_kind = "table"
            except Exception:
                counters_kind = None
            if counters_kind is None:
                ph_type = ""
                try:
                    ph_type = str(sh.placeholder_format.type) if sh.is_placeholder else ""
                except Exception:
                    ph_type = ""
                if "TITLE" in ph_type:
                    counters_kind = "title"
                elif "SUBTITLE" in ph_type:
                    counters_kind = "subtitle"
                elif ph_type:
                    counters_kind = "body"
                elif text.strip():
                    if (sh.top or 0) <= top_text_y + 4 * EMU:
                        counters_kind = "title"
                    elif (sh.top or 0) >= bottom_band:
                        counters_kind = "footer"
                    elif _re.search(r"(?i)^(source|note|via|data)[: ]", text.strip()):
                        counters_kind = "source"
                    else:
                        counters_kind = "body"
                elif _re.search(r"(?i)\b(deco|background|bg|line|divider|band|accent)\b", sh.name or ""):
                    counters_kind = "deco"
                else:
                    counters_kind = "panel"
            counters[counters_kind] += 1
            new_name = f"{counters_kind}-{si:02d}-{counters[counters_kind]:02d}"
            if sh.name != new_name:
                old = sh.name
                try:
                    sh.name = new_name
                    count += 1
                    _record(changes, op="normalize_names", slide=si, id=sh.shape_id,
                            before=old, after=new_name)
                except Exception as exc:
                    _record(changes, op="normalize_names", slide=si, id=sh.shape_id,
                            skipped=f"cannot rename: {exc}")
    return count


NORMALIZERS = {
    "align": (normalize_alignment, False, "snap near-aligned edges to a shared guide"),
    "spacing": (normalize_spacing, False, "equalize gaps between repeated objects"),
    "colors": (normalize_colors, True, "collapse near-duplicate colours to a canonical token"),
    "names": (normalize_names, False, "rename generic objects to stable semantic identifiers"),
}
