#!/usr/bin/env python3
"""Deck model and geometry primitives for the presentation production suite.

This module is a supporting library, not a CLI entry point. It converts a
PowerPoint (.pptx) package, or a neutral JSON deck model, into one normalized
in-memory model that every engine in tools-analysis.py and tools-authoring.py
reads from. Keeping all python-pptx access here means engines never touch the
raw package and can be tested against fixture models.

Public surface
--------------
UNIT helpers          : EMU_PER_INCH, EMU_PER_PT, to_emu(), emu_to(), convert()
load_deck()           : load a .pptx (or .json neutral model) path -> DeckCtx
deck_from_dict()      : rebuild a DeckCtx from a neutral model dict
deck_to_dict()        : serialize a DeckCtx to a neutral, diffable dict
compare_models()      : structural diff between two neutral models
package_health()      : ZIP/XML/relationship checks on a .pptx package

Every object exposes geometry in EMU plus derived points/inches helpers so
engines never repeat unit arithmetic.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

EMU_PER_INCH = 914400
EMU_PER_PT = 12700
EMU_PER_CM = 360000
EMU_PER_PX = 9525  # 96 dpi reference

P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

SUPPORTED_UNITS = ("emu", "pt", "px", "in", "cm", "mm")


class ModelError(Exception):
    """Raised when a deck cannot be read or a neutral model is malformed."""


# --------------------------------------------------------------------------- units
def convert(value: float, src: str, dst: str) -> float:
    """Convert a length between emu, pt, px (96 dpi), in, cm and mm."""
    src, dst = src.lower(), dst.lower()
    if src not in SUPPORTED_UNITS or dst not in SUPPORTED_UNITS:
        raise ModelError(f"unsupported unit pair {src!r}->{dst!r}; use {SUPPORTED_UNITS}")
    emu = value * {
        "emu": 1.0,
        "pt": EMU_PER_PT,
        "px": EMU_PER_PX,
        "in": EMU_PER_INCH,
        "cm": EMU_PER_CM,
        "mm": EMU_PER_CM / 10.0,
    }[src]
    return emu / {
        "emu": 1.0,
        "pt": EMU_PER_PT,
        "px": EMU_PER_PX,
        "in": EMU_PER_INCH,
        "cm": EMU_PER_CM,
        "mm": EMU_PER_CM / 10.0,
    }[dst]


def to_emu(value: float, unit: str = "pt") -> int:
    return int(round(convert(value, unit, "emu")))


def emu_to(value: float, unit: str = "pt") -> float:
    return convert(value, "emu", unit)


# --------------------------------------------------------------------- structures
@dataclass
class RunCtx:
    text: str
    size_pt: Optional[float] = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: Optional[str] = None      # hex RGB without '#'
    color_source: str = "none"       # srgb | theme | inherited | none
    font: Optional[str] = None
    language: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "size_pt": self.size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "color": self.color,
            "color_source": self.color_source,
            "font": self.font,
            "language": self.language,
        }


@dataclass
class ObjCtx:
    """One positioned object on a slide."""
    id: int
    name: str
    kind: str                      # text | placeholder-text | picture | table | chart | group | connector | media | shape | other
    x: int = 0                     # EMU
    y: int = 0
    w: int = 0
    h: int = 0
    rot: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    text: str = ""
    paragraphs: List[List[RunCtx]] = field(default_factory=list)
    placeholder_type: Optional[str] = None
    placeholder_idx: Optional[int] = None
    fill: Optional[str] = None
    fill_kind: str = "none"        # solid | gradient | picture | none | inherited
    line: Optional[str] = None
    line_width_pt: Optional[float] = None
    alt_text: str = ""
    alt_text_raw: str = ""
    decorative: bool = False
    z: int = 0
    shape_type: str = ""
    has_effects: bool = False
    media: Optional[Dict[str, Any]] = None
    table: Optional[Dict[str, Any]] = None
    chart: Optional[Dict[str, Any]] = None
    connector_endpoints: Optional[Dict[str, Any]] = None
    autofit: Optional[str] = None  # none | normAutofit | spAutoFit
    text_anchor: Optional[str] = None
    insets: Dict[str, int] = field(default_factory=lambda: {"l": 0, "t": 0, "r": 0, "b": 0})

    # geometry helpers -------------------------------------------------------
    @property
    def x_pt(self) -> float:
        return emu_to(self.x, "pt")

    @property
    def y_pt(self) -> float:
        return emu_to(self.y, "pt")

    @property
    def w_pt(self) -> float:
        return emu_to(self.w, "pt")

    @property
    def h_pt(self) -> float:
        return emu_to(self.h, "pt")

    def box(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def area_emu(self) -> int:
        return max(0, self.w) * max(0, self.h)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "z": self.z,
            "x_pt": round(self.x_pt, 2),
            "y_pt": round(self.y_pt, 2),
            "w_pt": round(self.w_pt, 2),
            "h_pt": round(self.h_pt, 2),
            "rot": self.rot,
            "flip_h": self.flip_h,
            "flip_v": self.flip_v,
            "text": self.text,
            "placeholder_type": self.placeholder_type,
            "fill": self.fill,
            "fill_kind": self.fill_kind,
            "line": self.line,
            "line_width_pt": self.line_width_pt,
            "alt_text": self.alt_text,
            "alt_text_raw": self.alt_text_raw,
            "decorative": self.decorative,
            "shape_type": self.shape_type,
            "has_effects": self.has_effects,
            "autofit": self.autofit,
            "insets": self.insets,
            "media": self.media,
            "table": self.table,
            "chart": self.chart,
            "connector_endpoints": self.connector_endpoints,
            "runs": [r.to_dict() for p in self.paragraphs for r in p],
        }


@dataclass
class SlideCtx:
    index: int                     # 1-based, presentation order
    slide_id: int
    layout_name: str
    master_name: str
    hidden: bool = False
    notes: str = ""
    objects: List[ObjCtx] = field(default_factory=list)
    background: Optional[str] = None
    comments: List[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return len(re.findall(r"\S+", " ".join(o.text for o in self.objects if o.text)))

    def text_objects(self) -> List[ObjCtx]:
        return [o for o in self.objects if o.text.strip()]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "slide_id": self.slide_id,
            "layout": self.layout_name,
            "master": self.master_name,
            "hidden": self.hidden,
            "notes": self.notes,
            "background": self.background,
            "comment_count": len(self.comments),
            "object_count": len(self.objects),
            "word_count": self.words,
            "objects": [o.to_dict() for o in sorted(self.objects, key=lambda o: o.z)],
        }


@dataclass
class DeckCtx:
    path: Optional[str]
    width: int                     # EMU
    height: int
    slides: List[SlideCtx] = field(default_factory=list)
    theme_colors: Dict[str, str] = field(default_factory=dict)
    theme_fonts: Dict[str, str] = field(default_factory=dict)
    masters: List[str] = field(default_factory=list)
    layouts: List[Dict[str, Any]] = field(default_factory=list)
    core_props: Dict[str, Any] = field(default_factory=dict)
    custom_props: Dict[str, Any] = field(default_factory=dict)
    media: List[Dict[str, Any]] = field(default_factory=list)
    fonts_used: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    source: str = "pptx"
    health: Dict[str, Any] = field(default_factory=dict)
    text_expansion: float = 1.0    # locale expansion factor applied by transforms

    # convenience -----------------------------------------------------------
    @property
    def aspect(self) -> str:
        if not self.height:
            return "unknown"
        ratio = self.width / self.height
        known = {"4:3": 4 / 3, "16:9": 16 / 9, "16:10": 16 / 10, "1:1": 1.0}
        for label, value in known.items():
            if abs(ratio - value) < 0.02:
                return label
        return f"{ratio:.3f}:1"

    @property
    def width_pt(self) -> float:
        return emu_to(self.width, "pt")

    @property
    def height_pt(self) -> float:
        return emu_to(self.height, "pt")

    def slide(self, index: int) -> SlideCtx:
        for s in self.slides:
            if s.index == index:
                return s
        raise ModelError(f"slide {index} not found (deck has {len(self.slides)})")

    def all_objects(self) -> Iterable[Tuple[SlideCtx, ObjCtx]]:
        for s in self.slides:
            for o in s.objects:
                yield s, o

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width_pt": round(self.width_pt, 2),
            "height_pt": round(self.height_pt, 2),
            "aspect": self.aspect,
            "source": self.source,
            "theme_colors": self.theme_colors,
            "theme_fonts": self.theme_fonts,
            "masters": self.masters,
            "layout_count": len(self.layouts),
            "layouts": self.layouts,
            "core_props": self.core_props,
            "custom_props": self.custom_props,
            "media": self.media,
            "fonts_used": self.fonts_used,
            "warnings": self.warnings,
            "slide_count": len(self.slides),
            "slides": [s.to_dict() for s in self.slides],
        }


# ------------------------------------------------------------------ pptx loading
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _rgb(color) -> Tuple[Optional[str], str]:
    """Best-effort RGB hex + source for a python-pptx color object."""
    if color is None:
        return None, "none"
    try:
        if color.type is None:
            return None, "inherited"
    except Exception:
        pass
    rgb = _safe(lambda: str(color.rgb))
    if rgb:
        return rgb.upper(), "srgb"
    theme = _safe(lambda: str(color.theme_color)) if _safe(lambda: color.theme_color) else None
    if theme:
        return theme, "theme"
    return None, "inherited"


def _shape_kind(shape) -> str:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    st = _safe(lambda: shape.shape_type)
    mapping = {
        MSO_SHAPE_TYPE.PLACEHOLDER: "placeholder",
        MSO_SHAPE_TYPE.PICTURE: "picture",
        MSO_SHAPE_TYPE.TABLE: "table",
        MSO_SHAPE_TYPE.CHART: "chart",
        MSO_SHAPE_TYPE.GROUP: "group",
        MSO_SHAPE_TYPE.MEDIA: "media",
        MSO_SHAPE_TYPE.LINE: "connector",
        MSO_SHAPE_TYPE.FREEFORM: "shape",
        MSO_SHAPE_TYPE.AUTO_SHAPE: "shape",
        MSO_SHAPE_TYPE.TEXT_BOX: "text",
        MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT: "ole",
        MSO_SHAPE_TYPE.LINKED_PICTURE: "picture",
    }
    kind = mapping.get(st, "other")
    if kind == "placeholder":
        kind = "placeholder-text" if _safe(lambda: shape.has_text_frame) else "placeholder"
    if _safe(lambda: shape.has_text_frame) and kind in ("shape", "other"):
        kind = "text" if not _safe(lambda: shape.fill.type) else "shape"
    return kind


def _iter_shapes(shapes, depth: int = 0):
    for shp in shapes:
        yield shp, depth
        if _safe(lambda: shp.shape_type == 6) and depth < 4:  # MSO_SHAPE_TYPE.GROUP
            for inner, d in _iter_shapes(_safe(lambda: shp.shapes, []), depth + 1):
                yield inner, d


def _read_runs(obj: ObjCtx, shape) -> None:
    tf = _safe(lambda: shape.text_frame)
    if tf is None:
        return
    obj.paragraphs = []
    texts: List[str] = []
    for para in _safe(lambda: tf.paragraphs, []):
        runs: List[RunCtx] = []
        for run in _safe(lambda: para.runs, []):
            font = _safe(lambda: run.font)
            color, source = _rgb(_safe(lambda: font.color)) if font is not None else (None, "none")
            runs.append(
                RunCtx(
                    text=run.text or "",
                    size_pt=_safe(lambda: float(font.size.pt)) if _safe(lambda: font.size) else None,
                    bold=bool(_safe(lambda: font.bold, False)),
                    italic=bool(_safe(lambda: font.italic, False)),
                    underline=bool(_safe(lambda: font.underline, False)),
                    color=color,
                    color_source=source,
                    font=_safe(lambda: font.name),
                    language=_safe(lambda: run.font.language_id),
                )
            )
        if runs:
            obj.paragraphs.append(runs)
            texts.append("".join(r.text for r in runs))
        elif _safe(lambda: para.text):
            texts.append(para.text)
    obj.text = "\n".join(texts).strip()
    obj.autofit = _safe(lambda: str(tf.auto_size).split(" ")[0] if tf.auto_size else None)
    obj.text_anchor = _safe(lambda: str(tf.vertical_anchor))
    body = _safe(lambda: tf._bodyPr)
    if body is not None:
        for key, attr in (("l", "lIns"), ("t", "tIns"), ("r", "rIns"), ("b", "bIns")):
            val = body.get(attr)
            if val is not None:
                obj.insets[key] = int(val)


def _read_table(obj: ObjCtx, shape) -> None:
    table = _safe(lambda: shape.table)
    if table is None:
        return
    rows: List[List[str]] = []
    for row in _safe(lambda: table.rows, []):
        cells = [(_safe(lambda c=c: c.text, "") or "").strip() for c in row.cells]
        rows.append(cells)
    obj.table = {
        "rows": len(rows),
        "cols": max((len(r) for r in rows), default=0),
        "header_row": rows[0] if rows else [],
        "cells": rows,
        "first_row_flag": _safe(lambda: table.first_row),
        "has_merged": any("\\" in c or "/" in c for r in rows for c in r) is False,
    }
    obj.text = "\n".join(" | ".join(r) for r in rows)


def _read_chart(obj: ObjCtx, shape) -> None:
    chart = _safe(lambda: shape.chart)
    if chart is None:
        return
    series = []
    for s in _safe(lambda: chart.series, []):
        vals = _safe(lambda s=s: [v for v in s.values], [])
        series.append({"name": _safe(lambda s=s: s.name, ""), "values": vals})
    obj.chart = {
        "chart_type": _safe(lambda: str(chart.chart_type)),
        "series": series,
        "has_title": bool(_safe(lambda: chart.has_title, False)),
        "has_legend": bool(_safe(lambda: chart.has_legend, False)),
    }
    obj.text = _safe(lambda: chart.chart_title.text_frame.text, "") or ""


def _read_media(obj: ObjCtx, shape) -> None:
    if obj.kind not in ("media", "picture", "ole"):
        return
    info: Dict[str, Any] = {}
    image = _safe(lambda: shape.image)
    if image is not None:
        info.update(
            {
                "kind": "image",
                "content_type": _safe(lambda: image.content_type),
                "size_bytes": len(_safe(lambda: image.blob, b"") or b""),
                "pixel_width": _safe(lambda: image.size[0]),
                "pixel_height": _safe(lambda: image.size[1]),
                "sha1": _safe(lambda: image.sha1),
                "filename": _safe(lambda: image.filename),
            }
        )
    if _safe(lambda: shape.shape_type == 16):  # MEDIA
        info.update({"kind": "av", "content_type": "audio/video", "poster": _safe(lambda: shape.media_format)})
    obj.media = info or None


def _read_alt(obj: ObjCtx, shape) -> None:
    """Read alternative text, treating a bare filename as absent.

    Office and python-pptx both seed descr with the source file name (for example
    "image.png"). A file name conveys nothing to a screen reader, so it is kept in
    alt_text_raw for reporting but is not counted as real alt text.
    """
    descr = (_safe(lambda: shape._element._nvXxPr.cNvPr.get("descr")) or "").strip()
    title = (_safe(lambda: shape._element._nvXxPr.cNvPr.get("title")) or "").strip()
    obj.alt_text_raw = descr or title
    filename = _safe(lambda: shape.image.filename) or ""
    candidate = descr or title
    looks_like_filename = bool(candidate) and (
        bool(re.fullmatch(r"[^\\/]+\.(png|jpe?g|gif|bmp|tiff?|svg|emf|wmf|webp)", candidate, re.I))
        or (filename and candidate == os.path.basename(filename))
    )
    obj.alt_text = "" if looks_like_filename else candidate
    obj.decorative = bool(_safe(lambda: shape._element._nvXxPr.cNvPr.get("hidden") in ("1", True), False))
    # a name such as "!!decorative" is a convention some decks use
    if re.search(r"decorative|spacer|background[-_ ]?art", obj.name, re.I) and not obj.alt_text:
        obj.decorative = True


def _read_effects(obj: ObjCtx, shape) -> None:
    xml = _safe(lambda: shape._element.xml, "") or ""
    obj.has_effects = bool(re.search(r"<a:(effectLst|effectDag|outerShdw|glow|softEdge|reflection)", xml))


def load_deck(path: str, config: Optional[Dict[str, Any]] = None) -> DeckCtx:
    """Load a .pptx package or a neutral JSON model into a DeckCtx."""
    if not os.path.isfile(path):
        raise ModelError(f"deck not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return deck_from_dict(data, path=path)
    if ext not in (".pptx", ".pptm", ".potx"):
        raise ModelError(f"unsupported deck type {ext!r}; expected .pptx, .pptm, .potx or .json")

    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover - documented dependency
        raise ModelError("python-pptx is required to read .pptx files (pip install python-pptx)") from exc

    try:
        prs = Presentation(path)
    except Exception as exc:
        raise ModelError(f"cannot open presentation: {exc}") from exc

    deck = DeckCtx(path=path, width=prs.slide_width or 0, height=prs.slide_height or 0)
    deck.health = package_health(path)
    deck.core_props = {
        "title": _safe(lambda: prs.core_properties.title),
        "author": _safe(lambda: prs.core_properties.author),
        "last_modified_by": _safe(lambda: prs.core_properties.last_modified_by),
        "created": str(_safe(lambda: prs.core_properties.created)),
        "modified": str(_safe(lambda: prs.core_properties.modified)),
        "revision": _safe(lambda: prs.core_properties.revision),
        "company": _safe(lambda: prs.core_properties.company),
        "comments": _safe(lambda: prs.core_properties.comments),
        "keywords": _safe(lambda: prs.core_properties.keywords),
        "subject": _safe(lambda: prs.core_properties.subject),
        "category": _safe(lambda: prs.core_properties.category),
    }

    # theme ---------------------------------------------------------------
    master_names: List[str] = []
    for mi, master in enumerate(_safe(lambda: prs.slide_masters, [])):
        master_names.append(_safe(lambda m=master: m.name, f"Master {mi + 1}"))
        theme = _safe(lambda m=master: m.part.part_related_by(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"))
        if theme is not None and not deck.theme_colors:
            try:
                xml = ET.fromstring(theme.blob)
                scheme = xml.find(f".//{A_NS}clrScheme")
                for child in list(scheme) if scheme is not None else []:
                    name = child.tag.split("}")[-1]
                    node = child.find(f"{A_NS}srgbClr")
                    value = node.get("val") if node is not None else None
                    if value is None:
                        sys_node = child.find(f"{A_NS}sysClr")
                        value = sys_node.get("lastClr") if sys_node is not None else None
                    if value:
                        deck.theme_colors[name] = value.upper()
                fonts = xml.find(f".//{A_NS}fontScheme")
                if fonts is not None:
                    for tag, key in (("majorFont", "major"), ("minorFont", "minor")):
                        node = fonts.find(f"{A_NS}{tag}")
                        latin = node.find(f"{A_NS}latin") if node is not None else None
                        if latin is not None:
                            deck.theme_fonts[key] = latin.get("typeface")
            except Exception:
                deck.warnings.append("theme part could not be parsed; colors reported as unknown")
    deck.masters = master_names

    for li, layout in enumerate(_safe(lambda: prs.slide_layouts, [])):
        deck.layouts.append(
            {
                "index": li,
                "name": _safe(lambda l=layout: l.name, f"Layout {li + 1}"),
                "master": _safe(lambda l=layout: l.slide_master.name),
                "placeholders": [
                    _safe(lambda p=ph: str(p.placeholder_format.type)) for ph in _safe(lambda l=layout: l.placeholders, [])
                ],
                "used_by": [],
            }
        )

    # slides --------------------------------------------------------------
    for si, slide in enumerate(prs.slides, start=1):
        layout_name = _safe(lambda s=slide: s.slide_layout.name, "unknown")
        master_name = _safe(lambda s=slide: s.slide_layout.slide_master.name, "unknown")
        hidden = str(_safe(lambda s=slide: s._element.get("show"), "1")) in ("0", "false")
        notes = _safe(lambda s=slide: s.notes_slide.notes_text_frame.text, "") or ""
        sctx = SlideCtx(
            index=si,
            slide_id=_safe(lambda s=slide: s.slide_id, si),
            layout_name=layout_name,
            master_name=master_name,
            hidden=hidden,
            notes=notes.strip(),
        )
        bg = _safe(lambda s=slide: str(s.background.fill.fore_color.rgb) if s.background.fill.type is not None else None)
        sctx.background = bg.upper() if isinstance(bg, str) and len(bg) == 6 else None
        for layout in deck.layouts:
            if layout["name"] == layout_name:
                layout["used_by"].append(si)

        z = 0
        for shape, depth in _iter_shapes(_safe(lambda s=slide: s.shapes, [])):
            z += 1
            obj = ObjCtx(
                id=_safe(lambda sh=shape: sh.shape_id, z),
                name=_safe(lambda sh=shape: sh.name, f"Object {z}"),
                kind=_shape_kind(shape),
                x=_safe(lambda sh=shape: int(sh.left or 0), 0),
                y=_safe(lambda sh=shape: int(sh.top or 0), 0),
                w=_safe(lambda sh=shape: int(sh.width or 0), 0),
                h=_safe(lambda sh=shape: int(sh.height or 0), 0),
                rot=float(_safe(lambda sh=shape: sh.rotation, 0.0) or 0.0),
                flip_h=bool(_safe(lambda sh=shape: sh._element.spPr.xfrm.get("flipH") == "1", False)),
                flip_v=bool(_safe(lambda sh=shape: sh._element.spPr.xfrm.get("flipV") == "1", False)),
                z=z,
            )
            ph = _safe(lambda sh=shape: sh.placeholder_format)
            if ph is not None:
                obj.placeholder_type = _safe(lambda: str(ph.type))
                obj.placeholder_idx = _safe(lambda: ph.idx)
            fill_kind = _safe(lambda sh=shape: str(sh.fill.type))
            obj.fill_kind = {
                "SOLID (1)": "solid",
                "GRADIENT (3)": "gradient",
                "PICTURE (6)": "picture",
                "BACKGROUND (5)": "background",
                "None": "none",
            }.get(fill_kind, "inherited" if fill_kind is None else "other")
            fill, _src = _rgb(_safe(lambda sh=shape: sh.fill.fore_color))
            obj.fill = fill
            obj.line = _rgb(_safe(lambda sh=shape: sh.line.color))[0]
            obj.line_width_pt = _safe(lambda sh=shape: float(sh.line.width.pt) if sh.line.width else None)
            obj.shape_type = _safe(lambda sh=shape: str(sh.shape_type), "")
            if obj.kind == "connector":
                obj.connector_endpoints = {
                    "begin_connected": bool(_safe(lambda sh=shape: sh.begin_connect is not None and sh.begin_connect.connected_shape is not None, False)),
                    "end_connected": bool(_safe(lambda sh=shape: sh.end_connect is not None and sh.end_connect.connected_shape is not None, False)),
                    "begin_shape": _safe(lambda sh=shape: (sh.begin_connect.connected_shape.name if sh.begin_connect and sh.begin_connect.connected_shape else None)),
                    "end_shape": _safe(lambda sh=shape: (sh.end_connect.connected_shape.name if sh.end_connect and sh.end_connect.connected_shape else None)),
                }
            _read_runs(obj, shape)
            _read_table(obj, shape)
            _read_chart(obj, shape)
            _read_media(obj, shape)
            _read_alt(obj, shape)
            _read_effects(obj, shape)
            for run in (r for p in obj.paragraphs for r in p):
                if run.font:
                    deck.fonts_used[run.font] = deck.fonts_used.get(run.font, 0) + 1
            sctx.objects.append(obj)
        deck.slides.append(sctx)

    for key in ("major", "minor"):
        name = deck.theme_fonts.get(key)
        if name and name not in ("+mn-lt", "+mj-lt"):
            deck.fonts_used.setdefault(name, 0)
            deck.fonts_used[name] += 1

    # embedded media inventory from the package itself
    deck.media = list_package_media(path)
    if not deck.slides:
        deck.warnings.append("deck contains no slides")
    return deck


def list_package_media(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.filename.startswith("ppt/media/"):
                    out.append(
                        {
                            "part": info.filename,
                            "size_bytes": info.file_size,
                            "ext": os.path.splitext(info.filename)[1].lower(),
                        }
                    )
    except Exception:
        pass
    return out


# ------------------------------------------------------- neutral model (JSON)
def deck_from_dict(data: Dict[str, Any], path: Optional[str] = None) -> DeckCtx:
    """Build a DeckCtx from a neutral JSON model (used for tests and JSON decks)."""
    if not isinstance(data, dict):
        raise ModelError("neutral model must be a JSON object")
    width_pt = data.get("width_pt", 720)
    height_pt = data.get("height_pt", 405)
    deck = DeckCtx(
        path=path,
        width=to_emu(width_pt, "pt"),
        height=to_emu(height_pt, "pt"),
        theme_colors=data.get("theme_colors", {}) or {},
        theme_fonts=data.get("theme_fonts", {}) or {},
        masters=data.get("masters", []) or [],
        layouts=data.get("layouts", []) or [],
        core_props=data.get("core_props", {}) or {},
        media=data.get("media", []) or [],
        fonts_used=data.get("fonts_used", {}) or {},
        warnings=data.get("warnings", []) or [],
        source="json",
    )
    for raw in data.get("slides", []) or []:
        sctx = SlideCtx(
            index=int(raw.get("index", len(deck.slides) + 1)),
            slide_id=int(raw.get("slide_id", len(deck.slides) + 1)),
            layout_name=raw.get("layout", "unknown"),
            master_name=raw.get("master", "unknown"),
            hidden=bool(raw.get("hidden", False)),
            notes=raw.get("notes", "") or "",
            background=raw.get("background"),
        )
        for z, o in enumerate(raw.get("objects", []) or [], start=1):
            obj = ObjCtx(
                id=int(o.get("id", z)),
                name=o.get("name", f"Object {z}"),
                kind=o.get("kind", "shape"),
                x=to_emu(float(o.get("x_pt", 0)), "pt"),
                y=to_emu(float(o.get("y_pt", 0)), "pt"),
                w=to_emu(float(o.get("w_pt", 0)), "pt"),
                h=to_emu(float(o.get("h_pt", 0)), "pt"),
                rot=float(o.get("rot", 0) or 0),
                text=o.get("text", "") or "",
                placeholder_type=o.get("placeholder_type"),
                fill=o.get("fill"),
                fill_kind=o.get("fill_kind", "solid" if o.get("fill") else "none"),
                line=o.get("line"),
                line_width_pt=o.get("line_width_pt"),
                alt_text=o.get("alt_text", "") or "",
                alt_text_raw=o.get("alt_text_raw", "") or "",
                decorative=bool(o.get("decorative", False)),
                z=int(o.get("z", z)),
                has_effects=bool(o.get("has_effects", False)),
                media=o.get("media"),
                table=o.get("table"),
                chart=o.get("chart"),
                connector_endpoints=o.get("connector_endpoints"),
                autofit=o.get("autofit"),
                insets=o.get("insets", {"l": 0, "t": 0, "r": 0, "b": 0}) or {"l": 0, "t": 0, "r": 0, "b": 0},
            )
            for p in o.get("paragraphs", []) or []:
                obj.paragraphs.append([RunCtx(**r) for r in p])
            if not obj.paragraphs and obj.text:
                obj.paragraphs = [[RunCtx(text=obj.text)]]
            sctx.objects.append(obj)
        deck.slides.append(sctx)
    if not deck.slides:
        deck.warnings.append("deck contains no slides")
    return deck


def compare_models(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Slide/object level diff between two neutral models."""

    def keyed(deck: Dict[str, Any]) -> Dict[str, Dict[Tuple[str, str], Dict[str, Any]]]:
        out = {}
        for s in deck.get("slides", []):
            objs = {}
            for o in s.get("objects", []):
                k = (o.get("name", ""), (o.get("text", "") or "")[:60])
                objs[k] = o
            out[str(s.get("index"))] = objs
        return out

    b, a = keyed(before), keyed(after)
    changes: List[Dict[str, Any]] = []
    for idx in sorted(set(b) | set(a), key=lambda v: int(v)):
        bobj, aobj = b.get(idx, {}), a.get(idx, {})
        if not bobj and aobj:
            changes.append({"slide": int(idx), "change": "slide_added"})
        elif bobj and not aobj:
            changes.append({"slide": int(idx), "change": "slide_removed"})
        for k in sorted(set(bobj) | set(aobj), key=str):
            if k not in aobj:
                changes.append({"slide": int(idx), "change": "object_removed", "object": k[0], "text": k[1]})
            elif k not in bobj:
                changes.append({"slide": int(idx), "change": "object_added", "object": k[0], "text": k[1]})
            else:
                for prop in ("x_pt", "y_pt", "w_pt", "h_pt", "rot"):
                    old, new = bobj[k].get(prop, 0), aobj[k].get(prop, 0)
                    if abs(float(old or 0) - float(new or 0)) > 0.5:
                        changes.append(
                            {
                                "slide": int(idx),
                                "change": "geometry",
                                "object": k[0],
                                "property": prop,
                                "before": round(float(old or 0), 2),
                                "after": round(float(new or 0), 2),
                            }
                        )
                if (bobj[k].get("text") or "") != (aobj[k].get("text") or ""):
                    changes.append({"slide": int(idx), "change": "text", "object": k[0]})
                if (bobj[k].get("fill") or "") != (aobj[k].get("fill") or ""):
                    changes.append({"slide": int(idx), "change": "fill", "object": k[0],
                                    "before": bobj[k].get("fill"), "after": aobj[k].get("fill")})
                if [r.get("size_pt") for r in bobj[k].get("runs", [])] != [r.get("size_pt") for r in aobj[k].get("runs", [])]:
                    changes.append({"slide": int(idx), "change": "typography", "object": k[0]})
    return {
        "slide_count_before": len(before.get("slides", [])),
        "slide_count_after": len(after.get("slides", [])),
        "change_count": len(changes),
        "changes": changes[:500],
        "truncated": len(changes) > 500,
    }


# --------------------------------------------------------------- package health
def package_health(path: str) -> Dict[str, Any]:
    """ZIP + XML + relationship integrity of a .pptx package."""
    report: Dict[str, Any] = {
        "ok": True,
        "parts": 0,
        "zip_test": "unknown",
        "bad_xml": [],
        "missing_rels_targets": [],
        "missing_content_types": [],
        "notes": [],
    }
    if not os.path.isfile(path):
        return {"ok": False, "error": f"not found: {path}"}
    try:
        with zipfile.ZipFile(path) as zf:
            report["zip_test"] = zf.testzip() or "ok"
            names = set(zf.namelist())
            report["parts"] = len(names)
            if "[Content_Types].xml" not in names:
                report["missing_content_types"].append("[Content_Types].xml")
            for name in names:
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                try:
                    ET.fromstring(zf.read(name))
                except Exception as exc:
                    report["bad_xml"].append({"part": name, "error": str(exc)[:160]})
            for name in [n for n in names if n.endswith(".rels")]:
                try:
                    root = ET.fromstring(zf.read(name))
                except Exception:
                    continue
                base = os.path.dirname(os.path.dirname(name))
                for rel in root:
                    if rel.get("TargetMode") == "External":
                        continue
                    target = rel.get("Target") or ""
                    resolved = os.path.normpath(os.path.join(base, target)).replace("\\", "/").lstrip("./")
                    if target.startswith("/"):
                        resolved = target.lstrip("/")
                    if resolved not in names and not target.startswith(("http", "mailto", "#")):
                        report["missing_rels_targets"].append({"rels": name, "target": target})
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": f"not a valid ZIP/OPC package: {exc}"}
    if report["bad_xml"]:
        report["notes"].append(f"{len(report['bad_xml'])} malformed XML part(s)")
    if report["missing_rels_targets"]:
        report["notes"].append(f"{len(report['missing_rels_targets'])} dangling relationship target(s)")
    if report["missing_content_types"]:
        report["notes"].append("missing [Content_Types].xml")
    report["ok"] = (
        report["zip_test"] == "ok" and not report["bad_xml"] and not report["missing_rels_targets"]
        and not report["missing_content_types"]
    )
    return report


# ------------------------------------------------------------ colour utilities
def hex_to_rgb(value: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not value:
        return None
    v = value.strip().lstrip("#")
    if len(v) == 8:      # ARGB from python-pptx sometimes carries alpha
        v = v[2:]
    if len(v) != 6 or re.search(r"[^0-9A-Fa-f]", v):
        return None
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def relative_luminance(value: Optional[str]) -> Optional[float]:
    """WCAG 2.x relative luminance for an sRGB hex colour."""
    rgb = hex_to_rgb(value)
    if rgb is None:
        return None

    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: Optional[str], bg: Optional[str]) -> Optional[float]:
    """WCAG contrast ratio between two hex colours; None when a colour is unknown."""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    if l1 is None or l2 is None:
        return None
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def color_distance(a: Optional[str], b: Optional[str]) -> Optional[float]:
    """Euclidean RGB distance; a cheap perceptual-adjacency proxy (0-441)."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    if ra is None or rb is None:
        return None
    return round(math.sqrt(sum((x - y) ** 2 for x, y in zip(ra, rb))), 2)


# ------------------------------------------------------------ text metrics
# Average glyph advance as a fraction of font size, per coarse script class.
_ADVANCE = {
    "latin": 0.50,
    "cjk": 1.00,
    "wide": 0.62,
    "mono": 0.60,
}


def _script_class(text: str) -> str:
    if re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", text):
        return "cjk"
    if re.search(r"[\u0590-\u05ff\u0600-\u06ff\u0e00-\u0e7f]", text):
        return "wide"
    return "latin"


def estimate_text_width_pt(text: str, size_pt: float, bold: bool = False) -> float:
    """Estimate rendered width of one line of text in points.

    Uses per-script average advances rather than exact font metrics: accurate to
    roughly +/-8% for common UI/office faces, which is enough for overflow risk
    flags. Exact measurement requires a font file and is listed as a limit.
    """
    if not text:
        return 0.0
    adv = _ADVANCE[_script_class(text)]
    factor = 1.06 if bold else 1.0
    # narrow characters are cheaper than the script average
    narrows = sum(1 for ch in text if ch in "iljI.,:;'|!()[]{} ")
    width = len(text) * adv * size_pt * factor
    width -= narrows * adv * size_pt * 0.35
    return round(max(width, 0.0), 2)


def wrap_line_count(text: str, size_pt: float, box_width_pt: float, bold: bool = False) -> int:
    """Greedy word-wrap line count for a given width, for overflow estimation."""
    if not text.strip():
        return 1
    if box_width_pt <= 1:
        return 99
    lines, current = 1, 0.0
    for word in text.split():
        w = estimate_text_width_pt(word + " ", size_pt, bold)
        if current and current + w > box_width_pt:
            lines += 1
            current = w
        else:
            current += w
    return lines


def slug(value: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s[:max_len] or "item"
