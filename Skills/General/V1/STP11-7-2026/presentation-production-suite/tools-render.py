#!/usr/bin/env python3
"""Deterministic raster renderer for the presentation production suite.

Supporting library behind tools-presentation-suite.py. It draws the *analyzed
geometry model* -- not the PowerPoint rendering engine -- so it can run anywhere
with Pillow and no office suite.

What it is good for
-------------------
  * confirming object positions, overlaps, safe-area violations and density
  * producing thumbnails and contact sheets for quick review
  * before/after visual diffing of geometry (moved/resized objects)
  * alt-text-free visual review where a real renderer is unavailable

What it cannot do (documented limit)
------------------------------------
  * font-exact text metrics: text is drawn with Pillow's default bitmap/TrueType
    fallback at the requested point size, so wrapping and glyph widths differ
    from PowerPoint
  * gradients, transparency, group transforms, chart/table internals, and media
    poster frames are approximated with flat fills and bounding boxes
For pixel-parity work, export from PowerPoint/LibreOffice and compare with the
`export-check` command (which measures structural parity, not pixels).

Load order: after tools-deckmodel.py. Imported by tools-presentation-suite.py.
"""

from __future__ import annotations

import io
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import deckmodel as dm

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False

PX_PER_PT = 96.0 / 72.0
MAX_PIXELS = 42_000_000

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]


class RenderError(Exception):
    pass


def _require_pil() -> None:
    if not PIL_AVAILABLE:
        raise RenderError("Pillow is required for rendering (pip install Pillow)")


def _font(size_px: int, bold: bool = False):
    _require_pil()
    for path in (FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size_px)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _kind_color(kind: str) -> Tuple[int, int, int]:
    return {
        "text": (210, 226, 244),
        "placeholder-text": (198, 219, 240),
        "shape": (226, 232, 240),
        "picture": (206, 233, 214),
        "table": (243, 231, 199),
        "chart": (232, 214, 240),
        "connector": (180, 180, 180),
        "media": (240, 205, 205),
        "group": (222, 222, 240),
        "other": (235, 235, 235),
    }.get(kind, (235, 235, 235))


def _outline_color(kind: str) -> Tuple[int, int, int]:
    return {
        "text": (70, 110, 160),
        "placeholder-text": (60, 100, 150),
        "picture": (70, 140, 90),
        "table": (160, 130, 60),
        "chart": (130, 90, 160),
        "connector": (120, 120, 120),
        "media": (170, 80, 80),
    }.get(kind, (170, 175, 185))


def render_slide(deck, slide, width_px: int = 1280, show_text: bool = True,
                 show_labels: bool = True, safe_area_pt: Optional[float] = None,
                 background: Optional[str] = None) -> "Image.Image":
    """Render one slide from the geometry model."""
    _require_pil()
    height_px = int(round(width_px * (deck.height_pt / deck.width_pt))) if deck.width_pt else width_px
    if width_px * height_px > MAX_PIXELS:
        raise RenderError(f"render would exceed {MAX_PIXELS} pixels; reduce --width")
    px_per_pt = width_px / deck.width_pt if deck.width_pt else 1.0
    bg_hex = background or slide.background
    if not bg_hex:
        lt = (deck.theme_colors.get("lt1") or "FFFFFF").upper()
        bg_hex = lt if (dm.relative_luminance(lt) or 1) > 0.4 else (deck.theme_colors.get("dk1") or "000000").upper()
    rgb = dm.hex_to_rgb(bg_hex) or (255, 255, 255)
    img = Image.new("RGB", (max(1, width_px), max(1, height_px)), rgb)
    draw = ImageDraw.Draw(img, "RGBA")

    if safe_area_pt:
        m = safe_area_pt * px_per_pt
        draw.rectangle([m, m, width_px - m, height_px - m], outline=(255, 120, 120), width=2)

    for obj in sorted(slide.objects, key=lambda o: o.z):
        x0 = obj.x_pt * px_per_pt
        y0 = obj.y_pt * px_per_pt
        x1 = (obj.x_pt + obj.w_pt) * px_per_pt
        y1 = (obj.y_pt + obj.h_pt) * px_per_pt
        if x1 <= x0 or y1 <= y0:
            continue
        fill = _kind_color(obj.kind)
        if obj.fill and dm.hex_to_rgb(obj.fill):
            fill = dm.hex_to_rgb(obj.fill)
            alpha = 255
        else:
            alpha = 210 if obj.kind in ("text", "placeholder-text", "shape") else 255
        draw.rectangle([x0, y0, x1, y1], fill=fill + ((alpha,) if len(fill) == 3 else None),
                       outline=_outline_color(obj.kind), width=1)
        if obj.kind == "connector":
            draw.line([x0, y0, x1, y1], fill=(110, 110, 110), width=2)
        if show_text and obj.text:
            size_px = max(6, int((_max_size(obj) or 18) * px_per_pt))
            font = _font(size_px, bold=_is_bold(obj))
            text = obj.text
            if len(text) > 240:
                text = text[:240] + "…"
            draw.multiline_text((x0 + 3, y0 + 2), text, fill=(20, 25, 35), font=font, spacing=2)
        if show_labels:
            lab_font = _font(max(8, int(7 * px_per_pt)), bold=False)
            label = f"{obj.name}"[:26]
            draw.rectangle([x0, y0, x0 + 6.0 * px_per_pt * len(label) / 10 + 4, y0 + 8 * px_per_pt],
                           fill=(255, 255, 255, 190))
            draw.text((x0 + 2, y0), label, fill=(90, 90, 110), font=lab_font)
    return img


def _max_size(obj) -> Optional[float]:
    sizes = [r.size_pt for p in obj.paragraphs for r in p if r.size_pt]
    return max(sizes) if sizes else None


def _is_bold(obj) -> bool:
    return any(r.bold for p in obj.paragraphs for r in p)


def render_deck(deck, out_dir: str, width_px: int = 1280, show_text: bool = True,
                show_labels: bool = False, safe_area_pt: Optional[float] = None,
                fmt: str = "png", slides: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """Render slides to individual image files; returns a manifest."""
    _require_pil()
    os.makedirs(out_dir, exist_ok=True)
    written: List[Dict[str, Any]] = []
    for slide in deck.slides:
        if slides and slide.index not in slides:
            continue
        img = render_slide(deck, slide, width_px=width_px, show_text=show_text,
                           show_labels=show_labels, safe_area_pt=safe_area_pt)
        path = os.path.join(out_dir, f"slide-{slide.index:03d}.{fmt}")
        if fmt.lower() in ("jpg", "jpeg"):
            img.convert("RGB").save(path, "JPEG", quality=88, optimize=True)
        else:
            img.save(path, "PNG", optimize=True)
        written.append({"slide": slide.index, "path": path, "width": img.width, "height": img.height,
                        "bytes": os.path.getsize(path)})
    return {"out_dir": out_dir, "count": len(written), "files": written}


def make_contact_sheet(deck, out_path: str, thumb_width: int = 320, cols: int = 4,
                       show_labels: bool = True) -> Dict[str, Any]:
    """Build a contact sheet of all slides for fast visual review."""
    _require_pil()
    if not deck.slides:
        raise RenderError("deck has no slides to render")
    thumbs = [render_slide(deck, s, width_px=thumb_width, show_text=False, show_labels=show_labels)
              for s in deck.slides]
    w, h = thumbs[0].size
    pad = 10
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + pad * (cols + 1), rows * (h + 18) + pad * (rows + 1)), (245, 246, 248))
    draw = ImageDraw.Draw(sheet)
    font = _font(12)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (w + pad)
        y = pad + r * (h + 18 + pad)
        sheet.paste(t, (x, y))
        draw.text((x, y + h + 3), f"Slide {deck.slides[i].index}", fill=(60, 60, 70), font=font)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    sheet.save(out_path, "PNG", optimize=True)
    return {"path": out_path, "slides": len(thumbs), "width": sheet.width, "height": sheet.height,
            "bytes": os.path.getsize(out_path)}


def diff_images(before_path: str, after_path: str, out_path: Optional[str] = None,
                threshold: int = 24) -> Dict[str, Any]:
    """Whole-image difference between two renders of the same slide."""
    _require_pil()
    if not (os.path.isfile(before_path) and os.path.isfile(after_path)):
        raise RenderError("both images must exist for a visual diff")
    a = Image.open(before_path).convert("RGB")
    b = Image.open(after_path).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    pa, pb = a.load(), b.load()
    changed = 0
    total = a.size[0] * a.size[1]
    bbox = None
    for y in range(a.size[1]):
        for x in range(a.size[0]):
            ca, cb = pa[x, y], pb[x, y]
            if max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]), abs(ca[2] - cb[2])) > threshold:
                changed += 1
                if bbox is None:
                    bbox = [x, y, x, y]
                else:
                    bbox[0] = min(bbox[0], x)
                    bbox[1] = min(bbox[1], y)
                    bbox[2] = max(bbox[2], x)
                    bbox[3] = max(bbox[3], y)
    ratio = changed / total if total else 0
    if out_path:
        overlay = Image.blend(a, b, 0.5)
        overlay.save(out_path, "PNG", optimize=True)
    return {"changed_pixels": changed, "total_pixels": total, "changed_ratio": round(ratio, 5),
            "bbox": bbox, "overlay_path": out_path,
            "identical": changed == 0}


def render_bytes(deck, slide, width_px: int = 800) -> bytes:
    """Render a slide to PNG bytes (used by tests and embedded reports)."""
    img = render_slide(deck, slide, width_px=width_px, show_text=True, show_labels=False)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
