#!/usr/bin/env python3
"""Audit engine for the powerpoint-agent skill tool.

Runs the rule pack in ``asset-audit-rules.json`` against a presentation and
returns structured findings, a quality score, and a pass/fail gate.  Loaded by
``tools-pptx-agent.py`` through ``tools-deck-core.py``; it is never executed on
its own.

Every rule is a pure function of the loaded presentation, so audits never
modify the input file.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

CORE = None  # set by bind()


def bind(core_module) -> None:
    """Attach the shared core module (must be called before use)."""

    global CORE
    CORE = core_module


SEVERITIES = ("error", "warning", "info")
SEVERITY_RANK = {"error": 3, "warning": 2, "info": 1}
SEVERITY_PENALTY = {"error": 8.0, "warning": 3.0, "info": 0.5}


# ---------------------------------------------------------------------------
# rule pack loading
# ---------------------------------------------------------------------------

def load_rule_pack(path: Optional[str] = None, *, core=None) -> Dict[str, Any]:
    core = core or CORE
    if path:
        pack = core.load_json(core.resolve_input(path, exts=(".json",), label="rule pack"))
    else:
        pack = core.load_json(core.asset_path("asset-audit-rules.json"))
    if not isinstance(pack, dict) or not isinstance(pack.get("rules"), list):
        raise core.ValidationError("Rule pack must be an object with a 'rules' array.")
    by_id: Dict[str, Dict[str, Any]] = {}
    for rule in pack["rules"]:
        if not isinstance(rule, dict) or not rule.get("id"):
            raise core.ValidationError("Every rule needs an 'id'.")
        rule_id = str(rule["id"])
        if rule_id in by_id:
            raise core.ValidationError("Duplicate rule id in rule pack: %s" % rule_id)
        severity = str(rule.get("severity", "warning")).lower()
        if severity not in SEVERITIES:
            raise core.ValidationError("Rule %s has an invalid severity '%s'." % (rule_id, severity))
        rule = dict(rule)
        rule["severity"] = severity
        by_id[rule_id] = rule
    pack["by_id"] = by_id
    pack["disabled"] = {str(item) for item in pack.get("disabled_rules") or []}
    pack["overrides"] = {str(k): str(v).lower() for k, v in (pack.get("severity_overrides") or {}).items()}
    pack["params"] = dict(pack.get("params") or {})
    return pack


def effective_severity(rule: Dict[str, Any], pack: Dict[str, Any]) -> str:
    override = pack["overrides"].get(rule["id"])
    if override in SEVERITIES:
        return override
    return rule["severity"]


def merged_params(pack: Dict[str, Any], config: Dict[str, Any], core=None) -> Dict[str, Any]:
    core = core or CORE
    """Merge defaults in this order: rule pack -> config -> safety policy.

    The rule pack supplies baseline thresholds, the config file supplies
    per-deployment overrides (it wins), and the safety block is applied last so
    link policy cannot be silently loosened by a pack.
    """

    params: Dict[str, Any] = {}
    params.update(pack.get("params") or {})
    audit_cfg = core.cfg(config, "audit", {}) or {}
    for key, value in audit_cfg.items():
        if value is not None:
            params[key] = value
    safety_cfg = core.cfg(config, "safety", {}) or {}
    if safety_cfg.get("blocked_protocols"):
        params["blocked_protocols"] = list(safety_cfg["blocked_protocols"])
    if "max_external_links" in safety_cfg:
        params["max_external_links"] = safety_cfg["max_external_links"]
    return params


# ---------------------------------------------------------------------------
# audit context
# ---------------------------------------------------------------------------

@dataclass
class AuditContext:
    prs: Any
    path: str
    config: Dict[str, Any]
    pack: Dict[str, Any]
    params: Dict[str, Any]
    core: Any
    log: Any
    findings: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)
    checks_run: int = 0
    deadline: Any = None
    scheme: Dict[str, str] = field(default_factory=dict)
    slide_cache: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    media_parts: List[Dict[str, Any]] = field(default_factory=list)
    referenced_media: set = field(default_factory=set)
    font_usage: Dict[str, int] = field(default_factory=dict)
    unverified_colors: int = 0
    spec: Optional[Dict[str, Any]] = None

    # -- finding helpers -------------------------------------------------
    def add(
        self,
        rule_id: str,
        message: str,
        *,
        slide: Optional[int] = None,
        shape: str = "",
        fix_hint: str = "",
        evidence: Optional[Dict[str, Any]] = None,
        severity: Optional[str] = None,
    ) -> None:
        rule = self.pack["by_id"].get(rule_id)
        if rule is None:
            return
        if rule_id in self.pack["disabled"]:
            return
        self.findings.append(
            {
                "rule_id": rule_id,
                "title": rule.get("title", rule_id),
                "category": rule.get("category", "general"),
                "severity": severity or effective_severity(rule, self.pack),
                "slide": slide,
                "location": shape,
                "message": message,
                "fix_hint": fix_hint or rule.get("fix", ""),
                "fixable": bool(rule.get("fixable")),
                "evidence": evidence or {},
            }
        )

    def skip(self, rule_id: str, reason: str) -> None:
        self.skipped.append({"rule_id": rule_id, "reason": reason})

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def check_deadline(self, label: str) -> None:
        if self.deadline:
            self.deadline.check(label)

    # -- per-slide cache -------------------------------------------------
    def cache_for(self, index: int) -> Dict[str, Any]:
        if index in self.slide_cache:
            return self.slide_cache[index]
        slide = self.prs.slides[index - 1]
        shapes = list(self.core.iter_shapes(slide.shapes))
        runs: List[Dict[str, Any]] = []
        for info in self.core.iter_text_runs(slide):
            style = self.core.resolve_run_style(self.prs, slide, info["shape"], info["paragraph"], info["run"], self.scheme)
            entry = dict(info)
            entry["style"] = style
            runs.append(entry)
        background = self.core.slide_background_hex(slide, self.prs, self.scheme)
        title = self.core.slide_title_text(slide)
        cache = {
            "slide": slide,
            "index": index,
            "shapes": shapes,
            "runs": runs,
            "background": background,
            "title": title,
        }
        self.slide_cache[index] = cache
        return cache


def _is_title_shape(shape) -> bool:
    return CORE.is_title_placeholder(shape) if CORE else False


FURNITURE_PREFIXES = (
    "pptx-agent-footer",
    "pptx-agent-slidenumber",
    "pptx-agent-caption",
    "pptx-agent-eyebrow",
    "pptx-agent-kpi-detail",
)


def _is_furniture(shape, ctx: "AuditContext") -> bool:
    """Deck furniture: footers, slide numbers, captions, and design eyebrows."""

    name = _shape_name(shape).lower()
    prefixes = tuple(str(item).lower() for item in (ctx.param("furniture_name_prefixes") or FURNITURE_PREFIXES))
    for prefix in prefixes:
        if name.startswith(prefix):
            return True
    slide_h = CORE.emu_to_inches(ctx.prs.slide_height) or 7.5
    try:
        top = CORE.emu_to_inches(shape.top) or 0
        height = CORE.emu_to_inches(shape.height) or 0
    except Exception:
        return False
    footer_band = float(ctx.param("footer_band_in", 0.55))
    return bool(top >= slide_h - footer_band and height <= footer_band)


def _shape_name(shape) -> str:
    try:
        return str(shape.name)
    except Exception:
        return "shape"


def _text_shapes(slide) -> List[Any]:
    found = []
    for shape in CORE.iter_shapes(slide.shapes):
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            found.append(shape)
    return found


def _is_background_shape(shape, ctx: AuditContext) -> bool:
    name = _shape_name(shape)
    if name.startswith("pptx-agent-background") or name.startswith("pptx-agent-band"):
        return True
    try:
        if shape.left is None or shape.top is None:
            return False
        covers_x = shape.left <= 0 and shape.width >= ctx.prs.slide_width * 0.98
        covers_y = shape.top <= 0 and shape.height >= ctx.prs.slide_height * 0.98
        return bool(covers_x and covers_y)
    except Exception:
        return False


def _effective_text_background(shape, ctx: AuditContext, cache: Dict[str, Any]) -> str:
    """Colour actually behind the run: the shape's own fill, an opaque card
    underneath it, or the slide canvas."""

    fill = CORE.shape_fill_hex(shape, ctx.scheme)
    if fill:
        return fill
    try:
        return CORE.effective_backdrop(cache["slide"], shape, ctx.scheme, cache["background"])
    except Exception:
        return cache["background"]


def _estimate_overflow(shape, ctx: AuditContext, cache: Dict[str, Any]) -> Tuple[float, float, float]:
    """Return (needed_in, available_in, ratio) for a text-bearing shape."""

    width_in = CORE.emu_to_inches(shape.width) or 0.0
    height_in = CORE.emu_to_inches(shape.height) or 0.0
    if width_in <= 0.05 or height_in <= 0.05:
        return 0.0, height_in, 0.0
    paragraphs: List[Dict[str, Any]] = []
    for run in cache["runs"]:
        if run["shape"] is not shape:
            continue
        paragraphs.append(
            {
                "text": run["text"],
                "size_pt": run["style"]["size_pt"],
                "bold": run["style"]["bold"],
                "indent_in": 0.0,
                "space_after_pt": 4.0,
            }
        )
    if not paragraphs:
        return 0.0, height_in, 0.0
    needed = CORE.estimate_text_block_height_in(paragraphs, width_in - 0.1, 16.0, 1.15, ctx.config)
    ratio = needed / height_in if height_in else 0.0
    return round(needed, 3), round(height_in, 3), round(ratio, 3)


# ---------------------------------------------------------------------------
# rule implementations
# ---------------------------------------------------------------------------

def rule_geo001(ctx: AuditContext) -> None:
    """Shapes that fall outside the slide canvas or have no usable geometry."""

    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if _is_background_shape(shape, ctx):
                continue
            bounds = CORE.shape_bounds_in(shape)
            if bounds["left"] is None or bounds["top"] is None:
                continue
            right = (bounds["left"] or 0) + (bounds["width"] or 0)
            bottom = (bounds["top"] or 0) + (bounds["height"] or 0)
            slide_w = CORE.emu_to_inches(ctx.prs.slide_width) or 0
            slide_h = CORE.emu_to_inches(ctx.prs.slide_height) or 0
            tolerance = 0.02
            outside = (
                (bounds["left"] or 0) < -tolerance
                or (bounds["top"] or 0) < -tolerance
                or right > slide_w + tolerance
                or bottom > slide_h + tolerance
            )
            if outside:
                ctx.add(
                    "GEO001",
                    "Shape '%s' extends outside the slide canvas." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"bounds": bounds, "slide": {"width_in": slide_w, "height_in": slide_h}},
                )


def rule_geo002(ctx: AuditContext) -> None:
    margin = float(ctx.param("edge_margin_in", 0.3))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if _is_background_shape(shape, ctx):
                continue
            if not getattr(shape, "has_text_frame", False) or not shape.text_frame.text.strip():
                continue
            if _is_furniture(shape, ctx):
                continue
            bounds = CORE.shape_bounds_in(shape)
            if bounds["left"] is None:
                continue
            slide_w = CORE.emu_to_inches(ctx.prs.slide_width) or 0
            slide_h = CORE.emu_to_inches(ctx.prs.slide_height) or 0
            too_close = (
                (bounds["left"] or 0) < margin
                or (bounds["top"] or 0) < margin
                or (bounds["left"] or 0) + (bounds["width"] or 0) > slide_w - margin
                or (bounds["top"] or 0) + (bounds["height"] or 0) > slide_h - margin
            )
            if too_close:
                ctx.add(
                    "GEO002",
                    "Text '%s' sits inside the %.2fin safe-margin band." % (CORE.truncate(shape.text_frame.text, 50), margin),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"bounds": bounds, "margin_in": margin},
                )


def rule_geo003(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        text_shapes = _text_shapes(cache["slide"])
        for first_pos in range(len(text_shapes)):
            for second_pos in range(first_pos + 1, len(text_shapes)):
                first, second = text_shapes[first_pos], text_shapes[second_pos]
                if _is_background_shape(first, ctx) or _is_background_shape(second, ctx):
                    continue
                if _is_furniture(first, ctx) or _is_furniture(second, ctx):
                    continue
                try:
                    left = max(first.left, second.left)
                    top = max(first.top, second.top)
                    right = min(first.left + first.width, second.left + second.width)
                    bottom = min(first.top + first.height, second.top + second.height)
                except Exception:
                    continue
                if right <= left or bottom <= top:
                    continue
                overlap = (right - left) * (bottom - top)
                smaller = min(max(first.width * first.height, 1), max(second.width * second.height, 1))
                ratio = overlap / smaller
                if ratio >= 0.2:
                    ctx.add(
                        "GEO003",
                        "Text frames '%s' and '%s' overlap by %.0f%%." % (_shape_name(first), _shape_name(second), ratio * 100),
                        slide=index,
                        shape="%s | %s" % (_shape_name(first), _shape_name(second)),
                        evidence={"overlap_ratio": round(ratio, 3)},
                    )


def rule_geo004(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            try:
                width = shape.width or 0
                height = shape.height or 0
            except Exception:
                continue
            if width <= 0 or height <= 0:
                ctx.add(
                    "GEO004",
                    "Shape '%s' has zero or negative size." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                )


def rule_geo005(ctx: AuditContext) -> None:
    """Text hidden behind an opaque shape that is higher in the z-order."""

    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        shapes = cache["shapes"]
        for position, shape in enumerate(shapes):
            if not getattr(shape, "has_text_frame", False) or not shape.text_frame.text.strip():
                continue
            try:
                area = float(shape.width) * float(shape.height)
            except Exception:
                continue
            if area <= 0:
                continue
            for covering in shapes[position + 1:]:
                if covering is shape:
                    continue
                if not CORE.shape_fill_hex(covering, ctx.scheme):
                    continue
                try:
                    left = max(shape.left, covering.left)
                    top = max(shape.top, covering.top)
                    right = min(shape.left + shape.width, covering.left + covering.width)
                    bottom = min(shape.top + shape.height, covering.top + covering.height)
                except Exception:
                    continue
                if right <= left or bottom <= top:
                    continue
                covered = float(right - left) * float(bottom - top) / area
                if covered >= 0.95:
                    ctx.add(
                        "GEO005",
                        "Text '%s' is hidden behind the opaque shape '%s'."
                        % (CORE.truncate(shape.text_frame.text, 45), _shape_name(covering)),
                        slide=index,
                        shape=_shape_name(shape),
                        evidence={"covering_shape": _shape_name(covering), "covered_ratio": round(covered, 3)},
                    )
                    break


def rule_typ001(ctx: AuditContext) -> None:
    minimum = float(ctx.param("min_font_pt", 14))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        reported = set()
        for run in cache["runs"]:
            style = run["style"]
            if _is_furniture(run["shape"], ctx):
                continue
            if style["size_pt"] < minimum and style["size_source"] != "default-estimate":
                key = (_shape_name(run["shape"]), round(style["size_pt"], 1))
                if key in reported:
                    continue
                reported.add(key)
                ctx.add(
                    "TYP001",
                    "Text at %.1fpt is below the %.0fpt minimum: '%s'" % (style["size_pt"], minimum, CORE.truncate(run["text"], 60)),
                    slide=index,
                    shape=_shape_name(run["shape"]),
                    evidence={"size_pt": style["size_pt"], "minimum_pt": minimum, "size_source": style["size_source"]},
                )


def rule_typ002(ctx: AuditContext) -> None:
    allowlist = {str(item).lower() for item in (ctx.param("font_allowlist") or [])}
    if not allowlist:
        ctx.skip("TYP002", "no font_allowlist configured")
        return
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        reported = set()
        for run in cache["runs"]:
            name = run["style"]["font_name"]
            if not name or name.lower() in allowlist or name in reported:
                continue
            reported.add(name)
            ctx.add(
                "TYP002",
                "Font '%s' is outside the approved font list." % name,
                slide=index,
                shape=_shape_name(run["shape"]),
                evidence={"font": name},
            )


def rule_typ003(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        by_shape: Dict[str, set] = {}
        for run in cache["runs"]:
            name = run["style"]["font_name"]
            if name:
                by_shape.setdefault(_shape_name(run["shape"]), set()).add(name.lower())
        for shape_name, fonts in by_shape.items():
            if len(fonts) > 2:
                ctx.add(
                    "TYP003",
                    "Text box '%s' mixes %d font families." % (shape_name, len(fonts)),
                    slide=index,
                    shape=shape_name,
                    evidence={"fonts": sorted(fonts)},
                )


def rule_typ004(ctx: AuditContext) -> None:
    tolerance = float(ctx.param("overflow_tolerance", 1.12))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_text_frame", False) or not shape.text_frame.text.strip():
                continue
            if _is_background_shape(shape, ctx):
                continue
            needed, available, ratio = _estimate_overflow(shape, ctx, cache)
            if ratio <= tolerance:
                continue
            title_shape = _is_title_shape(shape)
            ctx.add(
                "TYP004",
                "%s text likely overflows (needs %.2fin, has %.2fin)." % ("Title" if title_shape else "Body", needed, available),
                slide=index,
                shape=_shape_name(shape),
                evidence={"needed_in": needed, "available_in": available, "ratio": ratio, "estimated": True},
            )


def rule_typ005(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            try:
                if not shape.is_placeholder:
                    continue
            except Exception:
                continue
            if getattr(shape, "has_text_frame", False) and not shape.text_frame.text.strip():
                ctx.add(
                    "TYP005",
                    "Empty placeholder '%s' left on the slide." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                )


def rule_typ006(ctx: AuditContext) -> None:
    minimum = float(ctx.param("min_title_pt", 24))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for run in cache["runs"]:
            if not _is_title_shape(run["shape"]):
                continue
            style = run["style"]
            if style["size_pt"] < minimum and style["size_source"] != "default-estimate":
                ctx.add(
                    "TYP006",
                    "Slide title is %.1fpt; the minimum is %.0fpt." % (style["size_pt"], minimum),
                    slide=index,
                    shape=_shape_name(run["shape"]),
                    evidence={"size_pt": style["size_pt"], "minimum_pt": minimum},
                )
                break


def rule_col001(ctx: AuditContext) -> None:
    normal = float(ctx.param("min_contrast_normal", 4.5))
    large_threshold = float(ctx.param("min_contrast_large", 3.0))
    large_text = float(ctx.param("large_text_pt", 18))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        reported = set()
        for run in cache["runs"]:
            style = run["style"]
            if not style["color_hex"]:
                ctx.unverified_colors += 1
                continue
            background = _effective_text_background(run["shape"], ctx, cache)
            ratio = CORE.contrast_ratio(style["color_hex"], background)
            size = style["size_pt"]
            bold = style["bold"]
            required = large_threshold if (size >= large_text or (size >= 14 and bold)) else normal
            if ratio + 0.01 < required:
                key = (_shape_name(run["shape"]), style["color_hex"], background)
                if key in reported:
                    continue
                reported.add(key)
                ctx.add(
                    "COL001",
                    "Contrast %.2f:1 is below the %.1f:1 requirement for %s." % (ratio, required, CORE.truncate(run["text"], 40)),
                    slide=index,
                    shape=_shape_name(run["shape"]),
                    evidence={
                        "foreground": style["color_hex"],
                        "background": background,
                        "ratio": ratio,
                        "required": required,
                        "size_pt": size,
                    },
                )


def rule_col002(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for run in cache["runs"]:
            style = run["style"]
            if not style["color_hex"]:
                continue
            background = _effective_text_background(run["shape"], ctx, cache)
            if style["color_hex"].upper() == background.upper():
                ctx.add(
                    "COL002",
                    "Text colour matches its background exactly: '%s'" % CORE.truncate(run["text"], 50),
                    slide=index,
                    shape=_shape_name(run["shape"]),
                    evidence={"color": style["color_hex"], "background": background},
                )


def rule_a11y001(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        if not cache["title"].strip():
            ctx.add("A11Y001", "Slide has no detectable title.", slide=index, shape="")


def rule_a11y002(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            shape_type = str(getattr(shape, "shape_type", ""))
            needs_alt = shape_type.startswith("PICTURE") or shape_type.startswith("CHART") or shape_type.startswith("MEDIA")
            if not needs_alt:
                continue
            descr = ""
            try:
                descr = shape._element.nvPicPr.cNvPr.get("descr") or ""
            except Exception:
                try:
                    descr = shape._element.nvGraphicFramePr.cNvPr.get("descr") or ""
                except Exception:
                    descr = ""
            if not descr.strip():
                ctx.add(
                    "A11Y002",
                    "Shape '%s' is missing alt text." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"shape_type": shape_type},
                )


def rule_a11y003(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        slide = cache["slide"]
        order = [shape for shape in slide.shapes if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip()]
        if not order:
            continue
        title_position = None
        for position, shape in enumerate(order):
            if _is_title_shape(shape):
                title_position = position
                break
        if title_position is None:
            continue
        if title_position != 0:
            ctx.add(
                "A11Y003",
                "Title is reading-order position %d instead of 1." % (title_position + 1),
                slide=index,
                shape=_shape_name(order[title_position]),
                evidence={"position": title_position + 1},
            )


def rule_a11y004(ctx: AuditContext) -> None:
    seen: Dict[str, List[int]] = {}
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        title = re.sub(r"\s+", " ", cache["title"]).strip().lower()
        if title:
            seen.setdefault(title, []).append(index)
    for title, indexes in seen.items():
        if len(indexes) > 1:
            ctx.add(
                "A11Y004",
                "Duplicate slide title '%s' on slides %s." % (CORE.truncate(title, 60), ", ".join(str(i) for i in indexes)),
                slide=indexes[1],
                evidence={"slides": indexes},
            )


def rule_a11y005(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_table", False):
                continue
            table = shape.table
            if not table.first_row:
                ctx.add(
                    "A11Y005",
                    "Table '%s' does not mark its first row as a header row." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                )


def rule_a11y006(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_chart", False):
                continue
            try:
                chart = shape.chart
                has_title = bool(chart.has_title and chart.chart_title.text_frame.text.strip())
            except Exception:
                has_title = False
            alt_text = ""
            try:
                alt_text = shape._element.nvGraphicFramePr.cNvPr.get("descr") or ""
            except Exception:
                alt_text = ""
            if not has_title and not alt_text.strip():
                ctx.add(
                    "A11Y006",
                    "Chart '%s' has no chart title." % _shape_name(shape),
                    slide=index,
                    shape=_shape_name(shape),
                )


def rule_a11y007(ctx: AuditContext) -> None:
    missing = []
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        try:
            has_notes = cache["slide"].has_notes_slide and cache["slide"].notes_slide.notes_text_frame.text.strip()
        except Exception:
            has_notes = False
        if not has_notes:
            missing.append(index)
    if missing and len(missing) > len(ctx.prs.slides) * 0.5:
        ctx.add(
            "A11Y007",
            "%d of %d slides have no speaker notes." % (len(missing), len(ctx.prs.slides)),
            slide=None,
            evidence={"slides": missing[:50], "count": len(missing)},
        )


def rule_a11y008(ctx: AuditContext) -> None:
    url_re = re.compile(r"^(https?://|www\.)", re.I)
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for run in cache["runs"]:
            try:
                address = run["run"].hyperlink.address
            except Exception:
                address = None
            if address and url_re.match(run["text"].strip()):
                ctx.add(
                    "A11Y008",
                    "Hyperlink text is a raw URL; use descriptive link text instead.",
                    slide=index,
                    shape=_shape_name(run["shape"]),
                    evidence={"url": address, "text": CORE.truncate(run["text"], 80)},
                )


def rule_str001(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        has_content = False
        for shape in cache["shapes"]:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                has_content = True
                break
            shape_type = str(getattr(shape, "shape_type", ""))
            if shape_type.startswith(("PICTURE", "CHART", "TABLE", "MEDIA")):
                has_content = True
                break
        if not has_content:
            ctx.add("STR001", "Slide %d is empty: no text, pictures, charts, or tables." % index, slide=index)


def rule_str002(ctx: AuditContext) -> None:
    budget = int(ctx.param("max_slide_budget", 40))
    count = len(ctx.prs.slides)
    if count > budget:
        ctx.add(
            "STR002",
            "Deck has %d slides, above the %d-slide budget." % (count, budget),
            slide=None,
            evidence={"slide_count": count, "budget": budget},
        )


def rule_str003(ctx: AuditContext) -> None:
    patterns = [str(item).lower() for item in (ctx.param("placeholder_patterns") or [])]
    if not patterns:
        ctx.skip("STR003", "no placeholder_patterns configured")
        return
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text_frame.text.lower()
            hit = next((pattern for pattern in patterns if pattern in text), None)
            if hit:
                ctx.add(
                    "STR003",
                    "Placeholder text ('%s') found on slide %d." % (hit, index),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"pattern": hit, "text": CORE.truncate(text, 120)},
                )


def rule_str004(ctx: AuditContext) -> None:
    limit = int(ctx.param("max_title_chars", 90))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        title = cache["title"]
        if len(title) > limit:
            ctx.add(
                "STR004",
                "Title is %d characters (limit %d)." % (len(title), limit),
                slide=index,
                shape="title",
                evidence={"title": CORE.truncate(title, 120)},
            )


def rule_str005(ctx: AuditContext) -> None:
    limit = int(ctx.param("max_bullets_per_shape", 7))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_text_frame", False) or _is_title_shape(shape):
                continue
            paragraphs = [p for p in shape.text_frame.paragraphs if p.text.strip()]
            if len(paragraphs) > limit:
                ctx.add(
                    "STR005",
                    "%d bullets in one text box (guideline: %d)." % (len(paragraphs), limit),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"bullet_count": len(paragraphs)},
                )


def rule_cnt001(ctx: AuditContext) -> None:
    limit = int(ctx.param("max_bullet_chars", 180))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not getattr(shape, "has_text_frame", False) or _is_title_shape(shape):
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if len(text) > limit:
                    ctx.add(
                        "CNT001",
                        "Bullet is %d characters (limit %d)." % (len(text), limit),
                        slide=index,
                        shape=_shape_name(shape),
                        evidence={"text": CORE.truncate(text, 160)},
                    )
                    break


def _iter_hyperlink_targets(prs):
    """Yield (slide_index, shape_name, target, action, resolved) per hyperlink."""

    for index, slide in enumerate(prs.slides, start=1):
        for shape in CORE.iter_shapes(slide.shapes):
            try:
                nv = shape._element
            except Exception:
                continue
            for element in nv.iter():
                if not str(getattr(element, "tag", "")).endswith("}hlinkClick"):
                    continue
                rel_id = element.get(CORE._qn("r:id"))
                action = element.get("action") or ""
                target = ""
                resolved = False
                if rel_id:
                    try:
                        rel = slide.part.rels[rel_id]
                        target = str(getattr(rel, "target_ref", "") or "")
                    except Exception:
                        target = ""
                    # target_part raises ValueError for external relationships,
                    # so it must be probed independently of the target string
                    try:
                        resolved = rel.target_part is not None and not getattr(rel, "is_external", False)
                    except Exception:
                        resolved = False
                yield index, _shape_name(shape), target, action, resolved


def rule_lnk001(ctx: AuditContext) -> None:
    blocked = [str(item).lower() for item in (ctx.param("blocked_protocols") or [])]
    for index, shape_name, target, action, _resolved in _iter_hyperlink_targets(ctx.prs):
        lowered = (target or "").strip().lower()
        if any(lowered.startswith(protocol) for protocol in blocked):
            ctx.add(
                "LNK001",
                "Blocked hyperlink protocol in '%s'." % shape_name,
                slide=index,
                shape=shape_name,
                evidence={"target": CORE.truncate(target, 120), "action": action},
            )


def rule_lnk002(ctx: AuditContext) -> None:
    external = 0
    examples: List[Dict[str, Any]] = []
    for index, shape_name, target, action, _resolved in _iter_hyperlink_targets(ctx.prs):
        if (target or "").lower().startswith(("http://", "https://")):
            external += 1
            if len(examples) < 10:
                examples.append({"slide": index, "shape": shape_name, "target": CORE.truncate(target, 120)})
    if external:
        ctx.add(
            "LNK002",
            "Deck contains %d external web link(s); they will not work offline." % external,
            slide=None,
            evidence={"count": external, "examples": examples},
        )


def rule_lnk003(ctx: AuditContext) -> None:
    for index, shape_name, target, action, resolved in _iter_hyperlink_targets(ctx.prs):
        if "hlinksldjump" in (action or "").lower() and not resolved:
            ctx.add(
                "LNK003",
                "Internal slide-jump action on '%s' does not resolve to an existing slide." % shape_name,
                slide=index,
                shape=shape_name,
                evidence={"action": action, "target": CORE.truncate(target, 120)},
            )


def rule_med001(ctx: AuditContext) -> None:
    for part in ctx.media_parts:
        if part["bytes"] <= 0:
            ctx.add(
                "MED001",
                "Media part is empty: %s" % part["part"],
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_med002(ctx: AuditContext) -> None:
    threshold = float(ctx.param("large_media_mb", 10)) * 1024 * 1024
    for part in ctx.media_parts:
        if part["bytes"] > threshold:
            ctx.add(
                "MED002",
                "Large media part %s (%.1f MB)." % (part["part"], part["bytes"] / (1024 * 1024)),
                slide=None,
                evidence={"part": part["part"], "bytes": part["bytes"]},
            )


def rule_med003(ctx: AuditContext) -> None:
    max_pixels = int(ctx.param("max_image_pixels", 3840))
    for index in range(1, len(ctx.prs.slides) + 1):
        cache = ctx.cache_for(index)
        for shape in cache["shapes"]:
            if not str(getattr(shape, "shape_type", "")).startswith("PICTURE"):
                continue
            try:
                blob = shape.image.blob
                width_px, height_px = shape.image.size
            except Exception:
                continue
            if max(width_px, height_px) > max_pixels:
                ctx.add(
                    "MED003",
                    "Picture '%s' is %dx%d px; the configured maximum edge is %d px." % (_shape_name(shape), width_px, height_px, max_pixels),
                    slide=index,
                    shape=_shape_name(shape),
                    evidence={"width_px": width_px, "height_px": height_px, "bytes": len(blob)},
                )


def _chart_data_parts(ctx: AuditContext) -> set:
    """Embedded workbooks that back native charts (not user OLE objects)."""

    parts: set = set()
    try:
        for part in ctx.prs.part.package.iter_parts():
            name = str(part.partname)
            if not name.startswith("/ppt/charts/"):
                continue
            try:
                for rel in part.rels.values():
                    target = getattr(rel, "target_part", None)
                    if target is not None and "/embeddings/" in str(target.partname):
                        parts.add(str(target.partname))
            except Exception:
                continue
    except Exception:
        return parts
    return parts


def rule_cmp001(ctx: AuditContext) -> None:
    chart_data = _chart_data_parts(ctx)
    for part in ctx.media_parts:
        if part["part"] in chart_data:
            continue
        if "/embeddings/" in part["part"]:
            ctx.add(
                "CMP001",
                "Embedded OLE object present: %s" % part["part"],
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_cmp002(ctx: AuditContext) -> None:
    for part in ctx.media_parts:
        if part["part"].lower().endswith((".glb", ".3d")):
            ctx.add(
                "CMP002",
                "3D model part present; older PowerPoint builds and most web viewers cannot render it.",
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_cmp003(ctx: AuditContext) -> None:
    for part in ctx.media_parts:
        if part["part"].lower().endswith((".emf", ".wmf", ".svg")):
            ctx.add(
                "CMP003",
                "Vector media part %s may render differently across platforms." % os.path.basename(part["part"]),
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_cmp004(ctx: AuditContext) -> None:
    allowlist = {str(item).lower() for item in (ctx.param("font_allowlist") or [])}
    if not allowlist:
        ctx.skip("CMP004", "no font_allowlist configured")
        return
    unlisted = {name: count for name, count in ctx.font_usage.items() if name.lower() not in allowlist}
    if unlisted:
        ctx.add(
            "CMP004",
            "Deck uses %d font family(ies) outside the approved list." % len(unlisted),
            slide=None,
            evidence={"fonts": unlisted},
        )


def rule_cmp005(ctx: AuditContext) -> None:
    for index in range(1, len(ctx.prs.slides) + 1):
        slide = ctx.prs.slides[index - 1]
        try:
            xml = slide._element.xml
        except Exception:
            continue
        if "p14:morph" in xml or ("morph" in xml.lower() and "<p:transition" in xml):
            ctx.add(
                "CMP005",
                "Slide %d uses a Morph transition, which requires PowerPoint 2019 or Microsoft 365." % index,
                slide=index,
                evidence={"transition": "morph"},
            )


def rule_perf001(ctx: AuditContext) -> None:
    limit_mb = float(ctx.param("max_file_mb", 60))
    size = os.path.getsize(ctx.path)
    if size > limit_mb * 1024 * 1024:
        ctx.add(
            "PERF001",
            "File is %s, above the %s budget." % (CORE.human_bytes(size), CORE.human_bytes(int(limit_mb * 1024 * 1024))),
            slide=None,
            evidence={"bytes": size, "limit_mb": limit_mb},
        )


def rule_perf002(ctx: AuditContext) -> None:
    minimum = float(ctx.param("duplicate_media_min_kb", 64)) * 1024
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for part in ctx.media_parts:
        if part["bytes"] < minimum:
            continue
        digest = part.get("sha256") or ""
        if digest:
            groups.setdefault(digest, []).append(part)
    duplicates = {digest: parts for digest, parts in groups.items() if len(parts) > 1}
    if duplicates:
        wasted = sum(sum(part["bytes"] for part in parts[1:]) for parts in duplicates.values())
        ctx.add(
            "PERF002",
            "%d duplicate media group(s), about %.1f MB reclaimable." % (len(duplicates), wasted / (1024 * 1024)),
            slide=None,
            evidence={"groups": {digest[:12]: [p["part"] for p in parts] for digest, parts in list(duplicates.items())[:10]},
                      "reclaimable_bytes": wasted},
        )


def rule_perf003(ctx: AuditContext) -> None:
    chart_data = _chart_data_parts(ctx)
    unused = [
        part
        for part in ctx.media_parts
        if part["part"] not in ctx.referenced_media
        and part["part"] not in chart_data
        and not part["part"].lower().endswith(".bin")
    ]
    if unused:
        ctx.add(
            "PERF003",
            "%d media part(s) are not referenced by any slide (about %.1f MB)."
            % (len(unused), sum(part["bytes"] for part in unused) / (1024 * 1024)),
            slide=None,
            evidence={"parts": [part["part"] for part in unused][:25]},
        )


def rule_sec001(ctx: AuditContext) -> None:
    for part in ctx.media_parts:
        if "vbaproject" in part["part"].lower():
            ctx.add(
                "SEC001",
                "Presentation contains a VBA macro project.",
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_sec002(ctx: AuditContext) -> None:
    externals: List[str] = []
    try:
        for part in ctx.prs.part.package.iter_parts():
            try:
                rels = part.rels
            except Exception:
                continue
            for rel in rels.values():
                if getattr(rel, "is_external", False):
                    target = str(getattr(rel, "target_ref", ""))
                    if target and target not in externals:
                        externals.append(target)
    except Exception:
        ctx.skip("SEC002", "package relationships could not be iterated")
        return
    externals = [item for item in externals if not item.lower().startswith("mailto:")]
    if externals:
        ctx.add(
            "SEC002",
            "Deck links to %d external relationship target(s)." % len(externals),
            slide=None,
            evidence={"targets": externals[:20]},
        )


def rule_sec003(ctx: AuditContext) -> None:
    chart_data = _chart_data_parts(ctx)
    for part in ctx.media_parts:
        if part["part"] in chart_data:
            continue
        if "/embeddings/" in part["part"]:
            ctx.add(
                "SEC003",
                "Embedded file/object must be reviewed before distribution: %s" % part["part"],
                slide=None,
                evidence={"part": part["part"]},
            )


def rule_sec004(ctx: AuditContext) -> None:
    hits = []
    try:
        for part in ctx.prs.part.package.iter_parts():
            name = str(part.partname).lower()
            if name.endswith((".docm", ".xlsm", ".pptm", ".exe", ".dll", ".js", ".vbs")):
                hits.append(str(part.partname))
    except Exception:
        return
    if hits:
        ctx.add(
            "SEC004",
            "Deck embeds executable or macro-enabled content.",
            slide=None,
            evidence={"parts": hits[:20]},
        )


RULE_FUNCTIONS: Dict[str, Callable[[AuditContext], None]] = {
    "GEO001": rule_geo001,
    "GEO002": rule_geo002,
    "GEO003": rule_geo003,
    "GEO004": rule_geo004,
    "GEO005": rule_geo005,
    "TYP001": rule_typ001,
    "TYP002": rule_typ002,
    "TYP003": rule_typ003,
    "TYP004": rule_typ004,
    "TYP005": rule_typ005,
    "TYP006": rule_typ006,
    "COL001": rule_col001,
    "COL002": rule_col002,
    "A11Y001": rule_a11y001,
    "A11Y002": rule_a11y002,
    "A11Y003": rule_a11y003,
    "A11Y004": rule_a11y004,
    "A11Y005": rule_a11y005,
    "A11Y006": rule_a11y006,
    "A11Y007": rule_a11y007,
    "A11Y008": rule_a11y008,
    "STR001": rule_str001,
    "STR002": rule_str002,
    "STR003": rule_str003,
    "STR004": rule_str004,
    "STR005": rule_str005,
    "CNT001": rule_cnt001,
    "LNK001": rule_lnk001,
    "LNK002": rule_lnk002,
    "LNK003": rule_lnk003,
    "MED001": rule_med001,
    "MED002": rule_med002,
    "MED003": rule_med003,
    "CMP001": rule_cmp001,
    "CMP002": rule_cmp002,
    "CMP003": rule_cmp003,
    "CMP004": rule_cmp004,
    "CMP005": rule_cmp005,
    "PERF001": rule_perf001,
    "PERF002": rule_perf002,
    "PERF003": rule_perf003,
    "SEC001": rule_sec001,
    "SEC002": rule_sec002,
    "SEC003": rule_sec003,
    "SEC004": rule_sec004,
}


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def _collect_package(ctx: AuditContext) -> None:
    package = ctx.prs.part.package
    for part in package.iter_parts():
        name = str(part.partname)
        try:
            blob = part.blob
        except Exception:
            blob = b""
        if name.startswith("/ppt/media/") or "/embeddings/" in name or "vbaproject" in name.lower():
            ctx.media_parts.append(
                {
                    "part": name,
                    "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest() if blob else "",
                }
            )
    referenced = set()
    for slide in ctx.prs.slides:
        try:
            for rel in slide.part.rels.values():
                target = getattr(rel, "target_part", None)
                if target is not None:
                    referenced.add(str(target.partname))
        except Exception:
            continue
    ctx.referenced_media = referenced


def audit_deck(
    path: str,
    *,
    config: Dict[str, Any],
    pack: Optional[Dict[str, Any]] = None,
    log=None,
    deadline=None,
    fail_on: Optional[str] = None,
    core=None,
) -> Dict[str, Any]:
    """Audit a presentation and return a structured report."""

    core = core or CORE
    core.require_pptx()
    resolved = core.resolve_input(path)
    prs = core.open_presentation(resolved)
    pack = pack or load_rule_pack(core=core)
    params = merged_params(pack, config, core)
    ctx = AuditContext(
        prs=prs,
        path=resolved,
        config=config,
        pack=pack,
        params=params,
        core=core,
        log=log or core.RunLog(),
        deadline=deadline,
    )
    ctx.log.info("audit started", file=os.path.basename(resolved), rules=len(pack["by_id"]))
    ctx.scheme = core.theme_color_scheme(prs)
    _collect_package(ctx)

    for index in range(1, len(prs.slides) + 1):
        ctx.check_deadline("auditing slide %d" % index)
        cache = ctx.cache_for(index)
        for run in cache["runs"]:
            name = run["style"]["font_name"]
            if name:
                ctx.font_usage[name] = ctx.font_usage.get(name, 0) + 1

    for rule_id, rule in pack["by_id"].items():
        if rule_id in pack["disabled"]:
            ctx.skip(rule_id, "disabled in rule pack")
            continue
        function = RULE_FUNCTIONS.get(rule_id)
        if function is None:
            ctx.skip(rule_id, "no implementation in this build")
            continue
        ctx.check_deadline("running rule %s" % rule_id)
        ctx.checks_run += 1
        try:
            function(ctx)
        except core.AgentError:
            raise
        except Exception as exc:  # a broken rule must not abort the audit
            ctx.skip(rule_id, "rule error: %s: %s" % (type(exc).__name__, exc))
            if ctx.log:
                ctx.log.warn("rule failed", rule=rule_id, error=str(exc))

    counts = {severity: 0 for severity in SEVERITIES}
    for finding in ctx.findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    score = 100.0
    for finding in ctx.findings:
        score -= SEVERITY_PENALTY.get(finding["severity"], 1.0)
    score = round(max(0.0, score), 1)

    gate_setting = (fail_on or core.cfg(config, "audit.fail_on", "error") or "error").lower()
    if gate_setting not in ("error", "warning", "info", "none"):
        gate_setting = "error"
    if gate_setting == "none":
        passed = True
    else:
        threshold = SEVERITY_RANK[gate_setting]
        passed = not any(SEVERITY_RANK.get(finding["severity"], 0) >= threshold for finding in ctx.findings)

    findings_sorted = sorted(
        ctx.findings,
        key=lambda item: (-SEVERITY_RANK.get(item["severity"], 0), item["slide"] if item["slide"] is not None else 0, item["rule_id"]),
    )
    report = {
        "generated_at": core.utc_now_iso(),
        "tool": "powerpoint-agent %s" % core.TOOL_VERSION,
        "file": os.path.basename(resolved),
        "path": resolved,
        "bytes": os.path.getsize(resolved),
        "slide_count": len(prs.slides),
        "rule_pack": {"name": pack.get("pack_name", "core"), "version": pack.get("pack_version", "")},
        "checks_run": ctx.checks_run,
        "checks_skipped": ctx.skipped,
        "counts": counts,
        "score": score,
        "gate": {"fail_on": gate_setting, "passed": passed},
        "unverified_color_runs": ctx.unverified_colors,
        "findings": findings_sorted,
    }
    if ctx.log:
        ctx.log.info("audit finished", score=score, counts=counts, passed=passed)
    return report


# ---------------------------------------------------------------------------
# report writers
# ---------------------------------------------------------------------------

def report_to_markdown(report: Dict[str, Any], *, title: str = "") -> str:
    lines: List[str] = []
    lines.append("# %s" % (title or "Presentation audit report"))
    lines.append("")
    lines.append("- File: `%s`" % report.get("file", ""))
    lines.append("- Slides: %s" % report.get("slide_count", 0))
    lines.append("- Size: %s" % (CORE.human_bytes(report.get("bytes") or 0) if CORE else report.get("bytes")))
    lines.append("- Generated: %s" % report.get("generated_at", ""))
    lines.append("- Tool: %s" % report.get("tool", ""))
    lines.append("- Rule pack: %s %s" % (report.get("rule_pack", {}).get("name", ""), report.get("rule_pack", {}).get("version", "")))
    lines.append("")
    counts = report.get("counts", {})
    gate = report.get("gate", {})
    lines.append("## Result")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append("| Quality score | %s / 100 |" % report.get("score", ""))
    lines.append("| Errors | %s |" % counts.get("error", 0))
    lines.append("| Warnings | %s |" % counts.get("warning", 0))
    lines.append("| Info | %s |" % counts.get("info", 0))
    lines.append("| Gate (fail on %s) | %s |" % (gate.get("fail_on", ""), "PASS" if gate.get("passed") else "FAIL"))
    lines.append("| Rules executed | %s |" % report.get("checks_run", 0))
    lines.append("")
    findings = report.get("findings", [])
    if not findings:
        lines.append("No findings. Every executed rule passed.")
        lines.append("")
    for severity in SEVERITIES:
        group = [item for item in findings if item["severity"] == severity]
        if not group:
            continue
        lines.append("## %s (%d)" % (severity.capitalize() + "s", len(group)))
        lines.append("")
        for finding in group:
            slide = finding.get("slide")
            where = "slide %s" % slide if slide else "deck"
            lines.append(
                "- **%s** [%s] (%s): %s" % (finding["rule_id"], finding["category"], where, finding["message"])
            )
            if finding.get("fix_hint"):
                lines.append("  - Fix: %s" % finding["fix_hint"])
        lines.append("")
    skipped = report.get("checks_skipped") or []
    if skipped:
        lines.append("## Skipped checks")
        lines.append("")
        for item in skipped:
            lines.append("- `%s`: %s" % (item.get("rule_id", ""), item.get("reason", "")))
        lines.append("")
    if report.get("unverified_color_runs"):
        lines.append("## Notes")
        lines.append("")
        lines.append(
            "- %d text run(s) inherit their colour from the theme; contrast was not verified for them."
            % report["unverified_color_runs"]
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_to_csv(report: Dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["rule_id", "severity", "category", "slide", "location", "message", "fix_hint", "fixable"])
    for finding in report.get("findings", []):
        writer.writerow(
            [
                finding.get("rule_id", ""),
                finding.get("severity", ""),
                finding.get("category", ""),
                "" if finding.get("slide") is None else finding.get("slide"),
                finding.get("location", ""),
                finding.get("message", ""),
                finding.get("fix_hint", ""),
                "yes" if finding.get("fixable") else "no",
            ]
        )
    return buffer.getvalue()


def findings_by_rule(report: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for finding in report.get("findings", []):
        grouped.setdefault(finding["rule_id"], []).append(finding)
    return grouped


def summarize_counts(report: Dict[str, Any]) -> str:
    counts = report.get("counts", {})
    gate = report.get("gate", {})
    return "score %s/100 · %s errors · %s warnings · %s info · %s" % (
        report.get("score", ""),
        counts.get("error", 0),
        counts.get("warning", 0),
        counts.get("info", 0),
        "PASS" if gate.get("passed") else "FAIL",
    )
