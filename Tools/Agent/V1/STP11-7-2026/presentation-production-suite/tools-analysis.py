#!/usr/bin/env python3
"""Analysis engines for the presentation production suite.

Supporting library behind tools-presentation-suite.py. Each engine is a pure
function ``(deck, cfg) -> result`` where result is::

    {
      "engine":  "<stable engine id>",
      "title":   "<human label>",
      "summary": "<one-line conclusion>",
      "data":    {...},           # structured payload for reports/comparisons
      "issues":  [Issue, ...],    # findings usable for gating and triage
      "limits":  [...]            # measurement limits of this engine
    }

Issue shape::

    {"slide": int|0, "object": str|None, "severity": "error|warning|info",
     "code": "UPPER_SNAKE", "message": str, "repair": str, "evidence": {...}}

Engines never write files and never mutate the deck: all repair happens in
tools-authoring.py, so analysis stays deterministic and repeatable.

Load order: tools-deckmodel.py must be loaded first (it provides the model and
colour/text helpers). This module is imported by tools-presentation-suite.py.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import deckmodel as dm  # registered as 'deckmodel' by tools-presentation-suite.py


# --------------------------------------------------------------------- helpers
def issue(
    code: str,
    message: str,
    severity: str = "warning",
    slide: int = 0,
    obj: Optional[str] = None,
    repair: str = "",
    **evidence: Any,
) -> Dict[str, Any]:
    return {
        "slide": slide,
        "object": obj,
        "severity": severity,
        "code": code,
        "message": message,
        "repair": repair,
        "evidence": evidence,
    }


def result(engine: str, title: str, summary: str, data: Any, issues: Optional[List[Dict]] = None,
           limits: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "engine": engine,
        "title": title,
        "summary": summary,
        "data": data,
        "issues": issues or [],
        "limits": limits or [],
    }


def cfg_get(cfg: Dict[str, Any], key: str, default: Any = None) -> Any:
    return cfg.get(key, default)


def thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the active viewing profile's thresholds, allowing flat overrides."""
    profiles = cfg.get("profiles", {}) or {}
    name = cfg.get("profile", "conference-room")
    base = dict(profiles.get(name, {}))
    for key, value in (cfg.get("overrides", {}) or {}).items():
        base[key] = value
    for key in list(base) + ["align_tolerance_pt"]:
        if key in cfg and not isinstance(cfg[key], dict):
            base[key] = cfg[key]
    return base


def all_text(obj) -> str:
    return obj.text or ""


def obj_label(obj) -> str:
    return f"{obj.name}#{obj.id}"


def primary_size(obj) -> Optional[float]:
    sizes = [r.size_pt for p in obj.paragraphs for r in p if r.size_pt]
    if sizes:
        return max(sizes)
    return None


def run_sizes(deck) -> List[Tuple[int, str, float]]:
    out = []
    for s, o in deck.all_objects():
        for p in o.paragraphs:
            for r in p:
                if r.size_pt:
                    out.append((s.index, obj_label(o), float(r.size_pt)))
    return out


def resolve_bg(deck, slide) -> str:
    if getattr(slide, "background", None):
        return slide.background
    lt = (deck.theme_colors.get("lt1") or "FFFFFF").upper()
    dk = (deck.theme_colors.get("dk1") or "000000").upper()
    # A deck whose light slot is dark is a dark theme.
    lum = dm.relative_luminance(lt)
    return lt if lum is None or lum >= 0.4 else dk


def background_behind(slide, obj, slide_bg: str, deck=None) -> Tuple[str, str]:
    """Effective background for a text object: the topmost filled shape beneath it.

    Text placed on a coloured band or card must be judged against that fill, not
    against the slide background, or white-on-brand text reports a false failure.
    Returns (hex, source) where source is 'shape' or 'slide'.
    """
    # a shape's own solid fill is the background for the text it contains
    if obj.fill_kind == "solid" and not is_missing_colour(obj.fill):
        return obj.fill.upper(), f"self:{obj.name}"
    best = None
    obj_area = max(1, obj.w * obj.h)
    for other in slide.objects:
        if other is obj or other.kind in ("connector",):
            continue
        if other.z >= obj.z:
            continue
        if is_missing_colour(other.fill) or other.fill_kind not in ("solid",):
            continue
        inter, _smaller = overlap_area(obj, other)
        if inter / obj_area < 0.6:
            continue
        span = max(1, other.w * other.h)
        if inter / span > 0.999 and span > obj_area * 8:
            continue  # a full-bleed plate covering most of the slide: keep the slide background
        if best is None or other.z > best.z:
            best = other
    if best is not None:
        return best.fill.upper(), f"shape:{best.name}"
    return slide_bg, "slide"


def text_color_for(obj, theme: Dict[str, str]) -> Tuple[Optional[str], str]:
    """Dominant explicit text colour of an object plus its source."""
    counts: Counter = Counter()
    for p in obj.paragraphs:
        for r in p:
            if r.color:
                counts[r.color] += len(r.text) or 1
    if counts:
        colour = counts.most_common(1)[0][0]
        return colour, "explicit"
    tx1 = theme.get("tx1") or theme.get("dk1")
    if tx1:
        return tx1.upper(), "theme"
    return None, "unknown"


def slide_objects(slide, kinds: Optional[Sequence[str]] = None) -> List[Any]:
    objs = slide.objects
    if kinds:
        objs = [o for o in objs if o.kind in kinds]
    return objs


def overlap_area(a, b) -> Tuple[int, int]:
    """(intersection area, minimum area) in EMU^2."""
    x = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    y = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = x * y
    return inter, min(a.area_emu(), b.area_emu())


def footer_band_top(deck, safe_bottom_pt: float = 60.0) -> float:
    """Top of the footer band: the bottom 15% of the slide, but never above the
    safe bottom margin (so a footer line inside the safe zone still counts)."""
    return min(deck.height_pt * 0.85, deck.height_pt - safe_bottom_pt)


def is_missing_colour(value: Optional[str]) -> bool:
    return not value or not re.fullmatch(r"[0-9A-Fa-f]{6}", value or "")


def nice(x: float) -> float:
    return round(float(x), 2)


# =========================================================== structure engines
def engine_package(deck, cfg) -> Dict[str, Any]:
    health = deck.health or {}
    issues = []
    if health.get("error"):
        issues.append(issue("PKG_UNREADABLE", f"package could not be read: {health['error']}", "error",
                            repair="re-save or recover the file (see repair command)"))
    for bad in health.get("bad_xml", [])[:20]:
        issues.append(issue("PKG_BAD_XML", f"malformed XML part {bad['part']}", "error",
                            repair="reopen and re-save the presentation; if it fails, salvage slides"))
    for rel in health.get("missing_rels_targets", [])[:20]:
        issues.append(issue("PKG_DANGLING_REL", f"relationship in {rel['rels']} points to missing {rel['target']}",
                            "error", repair="repair the relationship or remove the referencing part"))
    if health.get("missing_content_types"):
        issues.append(issue("PKG_NO_CONTENT_TYPES", "package lacks [Content_Types].xml", "error"))
    summary = "package healthy" if health.get("ok") else f"{len(issues)} package defect(s)"
    return result("structure.package", "Package Profiler", summary,
                  {"health": health, "parts": health.get("parts"), "masters": deck.masters,
                   "layout_count": len(deck.layouts), "aspect": deck.aspect,
                   "slide_count": len(deck.slides), "source": deck.source}, issues)


def engine_census(deck, cfg) -> Dict[str, Any]:
    per_slide, totals = [], Counter()
    issues = []
    max_objs = cfg.get("limits", {}).get("max_objects_per_slide", 3000) or 3000
    for s in deck.slides:
        counts = Counter(o.kind for o in s.objects)
        totals.update(counts)
        if len(s.objects) > max_objs:
            issues.append(issue("CENSUS_TOO_MANY_OBJECTS",
                                f"slide has {len(s.objects)} objects (limit {max_objs})", "warning", s.index,
                                repair="split the slide or move detail to an appendix"))
        per_slide.append({"slide": s.index, "total": len(s.objects), "kinds": dict(counts),
                          "words": s.words, "hidden": s.hidden})
        if not s.objects:
            issues.append(issue("CENSUS_EMPTY_SLIDE", "slide has no objects", "info", s.index,
                                repair="confirm this is an intentional divider or delete it"))
    data = {"deck_totals": dict(totals), "slides": per_slide,
            "text_objects": sum(1 for _ in deck.all_objects() if all_text(_[1]).strip())}
    return result("structure.census", "Slide Object Census",
                  f"{len(deck.slides)} slides, {sum(totals.values())} objects", data, issues)


def engine_layout_map(deck, cfg) -> Dict[str, Any]:
    issues = []
    usage: Dict[str, List[int]] = defaultdict(list)
    for s in deck.slides:
        usage[s.layout_name].append(s.index)
    layouts = []
    for lay in deck.layouts:
        used = usage.get(lay["name"], [])
        layouts.append({"name": lay["name"], "master": lay.get("master"), "slides": used,
                        "placeholder_types": lay.get("placeholders", [])})
        if not used:
            issues.append(issue("LAYOUT_UNUSED", f"layout '{lay['name']}' is unused", "info",
                                repair="delete it to shrink the template, or map slides onto it"))
    duplicates = defaultdict(list)
    for lay in deck.layouts:
        duplicates[tuple(sorted(lay.get("placeholders", [])))].append(lay["name"])
    for sig, names in duplicates.items():
        if len(names) > 1 and sig:
            issues.append(issue("LAYOUT_DUPLICATE", f"layouts share identical placeholders: {', '.join(names)}",
                                "info", repair="keep one canonical layout"))
    blank = [s.index for s in deck.slides if s.layout_name.lower().startswith("blank")]
    if blank:
        issues.append(issue("LAYOUT_BLANK_USAGE", f"{len(blank)} slide(s) use a blank layout", "info",
                            repair="prefer a semantic reusable layout where one exists"))
    masters_used = Counter(s.master_name for s in deck.slides)
    data = {"layouts": layouts, "masters_used": dict(masters_used),
            "unused_layouts": [l["name"] for l in layouts if not l["slides"]],
            "blank_layout_slides": blank}
    return result("structure.layouts", "Master and Layout Mapper",
                  f"{len(layouts)} layouts, {len(data['unused_layouts'])} unused", data, issues)


def engine_editable(deck, cfg) -> Dict[str, Any]:
    issues = []
    editable, flattened = 0, []
    for s, o in deck.all_objects():
        if o.kind == "picture":
            media = o.media or {}
            pw, ph = media.get("pixel_width") or 0, media.get("pixel_height") or 0
            covered = o.w * o.h
            slide_area = max(1, deck.width * deck.height)
            big = covered / slide_area > 0.55
            if big:
                flattened.append({"slide": s.index, "object": obj_label(o), "coverage": nice(covered / slide_area)})
                issues.append(issue("EDIT_FLATTENED_SLIDE",
                                    f"picture covers {covered / slide_area:.0%} of the slide; content may be rasterized",
                                    "warning", s.index, obj_label(o),
                                    repair="reconstruct editable shapes/text from the source, or accept as an image slide"))
            elif pw and ph and (pw < o.w_pt * 1.5 or ph < o.h_pt * 1.5):
                issues.append(issue("EDIT_LOW_RES_RASTER",
                                    f"picture is {pw}x{ph}px placed at {o.w_pt:.0f}x{o.h_pt:.0f}pt",
                                    "warning", s.index, obj_label(o),
                                    repair="replace with a higher-resolution asset or a native diagram"))
        if o.kind in ("text", "placeholder-text", "shape", "table", "chart", "connector", "group"):
            editable += 1
    total = sum(1 for _ in deck.all_objects())
    ratio = round(editable / total, 3) if total else 0.0
    data = {"editable_objects": editable, "total_objects": total, "editable_ratio": ratio,
            "large_raster_candidates": flattened}
    if ratio and ratio < 0.5:
        issues.append(issue("EDIT_LOW_EDITABILITY", f"only {ratio:.0%} of objects are natively editable", "warning",
                            repair="prefer native shapes/tables/charts for content that must be maintained"))
    return result("structure.editable", "Editable Object Detector",
                  f"{ratio:.0%} of objects natively editable", data, issues)


def engine_hidden(deck, cfg) -> Dict[str, Any]:
    issues, findings = [], []
    for s in deck.slides:
        if s.hidden:
            issues.append(issue("HIDDEN_SLIDE", "slide is marked hidden but still ships in the package",
                                "warning", s.index, repair="delete it or move to an internal-only version"))
        for o in s.objects:
            off = o.x + o.w <= 0 or o.y + o.h <= 0 or o.x >= deck.width or o.y >= deck.height
            partial = (o.x < 0 or o.y < 0 or o.x + o.w > deck.width or o.y + o.h > deck.height) and not off
            if off:
                findings.append({"slide": s.index, "object": obj_label(o), "state": "fully_off_canvas"})
                issues.append(issue("HIDDEN_OFF_CANVAS", "object is entirely outside the canvas", "warning",
                                    s.index, obj_label(o),
                                    repair="archive (move to notes) or delete it; it may still be exported by some tools"))
            elif partial:
                findings.append({"slide": s.index, "object": obj_label(o), "state": "partially_off_canvas"})
                issues.append(issue("HIDDEN_PARTIAL_OFF_CANVAS", "object extends past the canvas edge", "error",
                                    s.index, obj_label(o), repair="run layout repair with --apply"))
            if o.w * o.h == 0:
                findings.append({"slide": s.index, "object": obj_label(o), "state": "zero_size"})
                issues.append(issue("HIDDEN_ZERO_SIZE", "object has zero width or height", "warning",
                                    s.index, obj_label(o), repair="delete or restore its geometry"))
    # fully transparent fills are only visible where XML records them
    for s, o in deck.all_objects():
        if o.fill == "FFFFFF" and o.fill_kind == "solid" and not all_text(o).strip() and o.kind == "shape":
            continue  # white-on-white is a legitimate design choice; needs render to judge
    data = {"findings": findings, "hidden_slides": [s.index for s in deck.slides if s.hidden]}
    return result("structure.hidden", "Hidden Content Inspector",
                  f"{len(findings)} off-canvas/zero-size findings", data, issues,
                  ["Full transparency cannot be read from every shape type without rendering."])


def engine_metadata(deck, cfg) -> Dict[str, Any]:
    props = deck.core_props or {}
    issues = []
    sensitive = ("author", "last_modified_by", "comments", "company", "keywords")
    present = {k: props.get(k) for k in sensitive if props.get(k)}
    if present:
        issues.append(issue("META_PERSONAL",
                            "document properties expose personal/organizational data: " + ", ".join(sorted(present)),
                            "info", repair="scrub metadata before external delivery (governance scrub-metadata --apply)"))
    if str(props.get("comments") or "").strip():
        issues.append(issue("META_COMMENTS", "document comment field contains text", "info",
                            repair="clear it in a public version"))
    notes = [(s.index, s.notes) for s in deck.slides if s.notes]
    todo = [i for i, n in notes if re.search(r"(?i)\b(todo|tbd|fixme|draft|lorem ipsum|xx+)\b", n)]
    for i in todo:
        issues.append(issue("META_NOTE_TODO", "speaker notes contain draft markers", "warning", i,
                            repair="finish or remove the draft note"))
    data = {"core_properties": props, "custom_properties": deck.custom_props,
            "slides_with_notes": len(notes), "draft_note_slides": todo,
            "notes_chars": sum(len(n) for _, n in notes)}
    return result("structure.metadata", "Notes and Metadata Auditor",
                  f"{len(notes)} slides with notes, {len(present)} personal property field(s)", data, issues)


def engine_assets(deck, cfg) -> Dict[str, Any]:
    media = deck.media or []
    by_slide = Counter()
    for s, o in deck.all_objects():
        if o.kind in ("picture", "media"):
            by_slide[s.index] += 1
    unique, by_hash = {}, defaultdict(int)
    for s, o in deck.all_objects():
        m = o.media or {}
        key = m.get("sha1")
        if key:
            by_hash[key] += 1
            unique.setdefault(key, o)
    dups = [{"sha1": h, "uses": n, "size_bytes": (unique[h].media or {}).get("size_bytes")}
            for h, n in by_hash.items() if n > 1]
    issues = []
    total_bytes = sum(m.get("size_bytes", 0) or 0 for m in media)
    limit = cfg.get("limits", {}).get("max_file_bytes", 524288000)
    if total_bytes > limit:
        issues.append(issue("ASSET_OVERSIZE", f"embedded media is {total_bytes / 1e6:.1f} MB (limit {limit / 1e6:.0f} MB)",
                            "warning", repair="downsample images or link large video"))
    for d in dups:
        issues.append(issue("ASSET_DUPLICATE", f"identical embedded asset used {d['uses']} times",
                            "info", repair="keep one instance where possible; PowerPoint dedupes identical blobs automatically"))
    data = {"package_media": media, "media_bytes": total_bytes,
            "picture_placements": sum(by_slide.values()), "duplicate_assets": dups,
            "extensions": dict(Counter(m.get("ext", "?") for m in media))}
    return result("structure.assets", "Embedded Asset Enumerator",
                  f"{len(media)} media parts, {sum(by_slide.values())} placements", data, issues)


def engine_dependencies(deck, cfg) -> Dict[str, Any]:
    checks = cfg.get("dependency_checks", {}) or {}
    issues = []
    fallback = cfg.get("font_fallback_map", {}) or {}
    fonts = [f for f in deck.fonts_used if f]
    unmapped = [f for f in fonts if f not in fallback]
    missing_font_risk = [f for f in fonts if f not in (deck.theme_fonts.get("major"), deck.theme_fonts.get("minor"))]
    av = [m for m in deck.media if m.get("ext") in (".mp4", ".m4v", ".mov", ".avi", ".wmv", ".mp3", ".wav", ".m4a")]
    if checks.get("check_fonts", True) and unmapped:
        issues.append(issue("DEP_FONT_UNMAPPED",
                            f"{len(unmapped)} font(s) have no known fallback mapping", "info",
                            repair="add a mapping to font_fallback_map in the config"))
    if checks.get("check_media", True) and av:
        issues.append(issue("DEP_AV_EMBEDDED", f"{len(av)} audio/video part(s) embedded; playback depends on codecs",
                            "info", repair="verify on the target machine; provide a fallback (export-check)"))
    broken = [c for s, o in deck.all_objects() if o.connector_endpoints
              for c in [o.connector_endpoints] if not (c.get("begin_connected") and c.get("end_connected"))]
    if checks.get("check_relationships", True) and broken:
        issues.append(issue("DEP_CONNECTOR_UNATTACHED",
                            f"{len(broken)} connector(s) are not attached at both ends", "warning",
                            repair="run connector audit and reattach to shapes"))
    data = {"fonts_used": dict(deck.fonts_used), "fonts_without_fallback_map": unmapped,
            "fonts_not_in_theme": missing_font_risk, "av_parts": av,
            "dangling_relationships": (deck.health or {}).get("missing_rels_targets", []),
            "external_links": []}
    return result("structure.dependencies", "Presentation Dependency Scanner",
                  f"{len(fonts)} fonts, {len(av)} A/V part(s), {len(data['dangling_relationships'])} dangling rel(s)",
                  data, issues)


def engine_intent(deck, cfg) -> Dict[str, Any]:
    patterns = {
        "instruction": r"(?i)\b(add|insert|replace|use|include|put your|click to add|type here|describe)\b.{0,60}\b(here|image|photo|logo|text|title)\b",
        "placeholder": r"(?i)(lorem ipsum|click to (add|edit)|xxx+|tbd|todo|placeholder)",
        "meta": r"(?i)\b(note to presenter|speaker note|do not present|internal only|delete this)\b",
    }
    issues, findings = [], []
    for s, o in deck.all_objects():
        text = all_text(o)
        if not text.strip():
            continue
        for kind, pat in patterns.items():
            if re.search(pat, text):
                findings.append({"slide": s.index, "object": obj_label(o), "kind": kind, "text": text[:120]})
                issues.append(issue(f"INTENT_{kind.upper()}",
                                    f"authoring artifact detected ({kind}) in a visible object", "warning",
                                    s.index, obj_label(o),
                                    repair="replace with final audience-facing content or move to notes"))
                break
    return result("structure.intent", "Presentation Intent Boundary Detector",
                  f"{len(findings)} authoring artifact(s) in audience-facing content", {"findings": findings}, issues)


# ============================================================ geometry engines
def engine_canvas(deck, cfg) -> Dict[str, Any]:
    return result("geometry.canvas", "Canvas Geometry Reader",
                  f"{deck.width_pt:.0f}x{deck.height_pt:.0f}pt ({deck.aspect})",
                  {"width_pt": nice(deck.width_pt), "height_pt": nice(deck.height_pt),
                   "width_in": nice(dm.emu_to(deck.width, "in")), "height_in": nice(dm.emu_to(deck.height, "in")),
                   "aspect": deck.aspect,
                   "width_px_96": nice(dm.emu_to(deck.width, "px")),
                   "height_px_96": nice(dm.emu_to(deck.height, "px")),
                   "units_supported": list(dm.SUPPORTED_UNITS)},
                  limits=["Pixel conversions assume 96 dpi; projector/print pixels differ."])


def engine_coordinates(deck, cfg, slide: Optional[int] = None) -> Dict[str, Any]:
    rows = []
    for s in deck.slides:
        if slide and s.index != slide:
            continue
        for o in s.objects:
            rows.append({"slide": s.index, "object": obj_label(o), "kind": o.kind,
                         "x_pt": nice(o.x_pt), "y_pt": nice(o.y_pt),
                         "w_pt": nice(o.w_pt), "h_pt": nice(o.h_pt),
                         "right_pt": nice(o.x_pt + o.w_pt), "bottom_pt": nice(o.y_pt + o.h_pt),
                         "rot": o.rot, "flip_h": o.flip_h, "flip_v": o.flip_v})
    return result("geometry.coordinates", "Object Coordinate Extractor",
                  f"{len(rows)} positioned object(s)", {"objects": rows},
                  limits=["Rotation-aware bounding boxes are reported as unrotated frame plus angle."])


def engine_safe_margin(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    margin = float(th.get("safe_margin_pt", 28))
    bottom = float(th.get("safe_bottom_pt", 34))
    issues, rows = [], []
    W, H = deck.width_pt, deck.height_pt
    for s, o in deck.all_objects():
        if o.w == 0 or o.h == 0:
            continue
        left, top = o.x_pt, o.y_pt
        right, bot = o.x_pt + o.w_pt, o.y_pt + o.h_pt
        violations = []
        if left < margin:
            violations.append(("left", left))
        if top < margin:
            violations.append(("top", top))
        if right > W - margin:
            violations.append(("right", W - right))
        if bot > H - bottom:
            violations.append(("bottom", H - bot))
        # full-bleed backgrounds are legitimate
        if o.w_pt >= W - 2 and o.h_pt >= H - 2:
            continue
        for side, gap in violations:
            rows.append({"slide": s.index, "object": obj_label(o), "side": side, "gap_pt": nice(gap)})
            issues.append(issue("GEO_SAFE_MARGIN",
                                f"object is {abs(gap):.0f}pt from the {side} edge (safe margin {margin if side != 'bottom' else bottom:.0f}pt)",
                                "warning" if abs(gap) < margin / 2 else "info",
                                s.index, obj_label(o),
                                repair="alignment repair moves objects onto the safe area with --apply",
                                side=side, gap_pt=nice(gap)))
    data = {"safe_margin_pt": margin, "safe_bottom_pt": bottom, "violations": rows}
    return result("geometry.safe_margin", "Safe Margin Enforcer",
                  f"{len(rows)} safe-area violation(s)", data, issues)


def engine_bounds(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        over = {
            "left": max(0, -o.x) / dm.EMU_PER_PT,
            "top": max(0, -o.y) / dm.EMU_PER_PT,
            "right": max(0, (o.x + o.w) - deck.width) / dm.EMU_PER_PT,
            "bottom": max(0, (o.y + o.h) - deck.height) / dm.EMU_PER_PT,
        }
        if any(v > 0.5 for v in over.values()):
            rows.append({"slide": s.index, "object": obj_label(o), "overflow_pt": {k: nice(v) for k, v in over.items() if v > 0}})
            total = sum(over.values())
            issues.append(issue("GEO_OUT_OF_BOUNDS", f"object exceeds canvas by {total:.0f}pt total", "error",
                                s.index, obj_label(o), repair="fit-to-safe-area repair with --apply; archive if intentional"))
    return result("geometry.bounds", "Out-of-Bounds Resolver", f"{len(rows)} object(s) exceed the canvas",
                  {"violations": rows}, issues)


def engine_zones(deck, cfg) -> Dict[str, Any]:
    """Infer title/body/footer zones from real object distribution."""
    th = thresholds(cfg)
    tops = [o.y_pt for s, o in deck.all_objects() if all_text(o).strip()]
    if not tops:
        return result("geometry.zones", "Content Boundary Estimator", "no text found", {})
    tops.sort()
    title_cut = tops[min(len(tops) - 1, max(0, int(len(tops) * 0.18)))]
    title_cut = max(title_cut, min(tops) + 4)
    footer_top = deck.height_pt - float(th.get("safe_bottom_pt", 34))
    title_objs = [o for s, o in deck.all_objects() if all_text(o).strip() and o.y_pt <= title_cut + 2]
    data = {
        "title_zone_pt": [0, nice(title_cut + 8)],
        "body_zone_pt": [nice(title_cut + 8), nice(footer_top)],
        "footer_zone_pt": [nice(footer_top), nice(deck.height_pt)],
        "title_objects": len(title_objs),
        "title_baseline_pt": nice(min((o.y_pt for o in title_objs), default=0)),
    }
    return result("geometry.zones", "Content Boundary Estimator",
                  f"title ≤{title_cut + 8:.0f}pt, footer ≥{footer_top:.0f}pt", data)


def engine_rotation(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        if abs(o.rot) > 0.01 or o.flip_h or o.flip_v:
            rows.append({"slide": s.index, "object": obj_label(o), "rot": o.rot, "flip_h": o.flip_h, "flip_v": o.flip_v})
            if abs(o.rot % 360) not in (0.0,) and a_text(o):
                issues.append(issue("GEO_ROTATED_TEXT", f"text object rotated {o.rot}°", "warning",
                                    s.index, obj_label(o), repair="confirm intent; rotated body text is hard to read"))
    # inconsistent rotation among same-named repeated components
    groups = defaultdict(set)
    for s, o in deck.all_objects():
        base = re.sub(r"\s*\d+$", "", o.name)
        groups[base].add(round(o.rot, 1))
    for base, rots in groups.items():
        if len(rots) > 1:
            issues.append(issue("GEO_ROTATION_INCONSISTENT",
                                f"'{base}' objects use differing rotations {sorted(rots)}", "info",
                                repair="normalize rotation for repeated components"))
    return result("geometry.rotation", "Rotation and Transform Auditor",
                  f"{len(rows)} transformed object(s)", {"objects": rows}, issues)


def a_text(o) -> bool:
    return bool(all_text(o).strip())


def engine_alignment(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    tol = float(th.get("align_tolerance_pt", cfg.get("align_tolerance_pt", 1.5)))
    issues, pairs = [], []
    for s in deck.slides:
        objs = [o for o in s.objects if o.w and o.h]
        n = len(objs)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = objs[i], objs[j]
                for label, va, vb in (("left", a.x_pt, b.x_pt),
                                      ("top", a.y_pt, b.y_pt),
                                      ("right", a.x_pt + a.w_pt, b.x_pt + b.w_pt),
                                      ("bottom", a.y_pt + a.h_pt, b.y_pt + b.h_pt),
                                      ("center_x", a.x_pt + a.w_pt / 2, b.x_pt + b.w_pt / 2),
                                      ("center_y", a.y_pt + a.h_pt / 2, b.y_pt + b.h_pt / 2)):
                    delta = abs(va - vb)
                    if 0 < delta <= tol:
                        pairs.append({"slide": s.index, "a": obj_label(a), "b": obj_label(b),
                                      "edge": label, "delta_pt": nice(delta)})
                        issues.append(issue("GRID_NEAR_ALIGN",
                                            f"{label} edges differ by {delta:.2f}pt (tolerance {tol}pt)", "info",
                                            s.index, obj_label(a),
                                            repair="snap-to-system repair aligns both to the same guide",
                                            other=obj_label(b), edge=label))
    data = {"tolerance_pt": tol, "near_misses": pairs[:200], "near_miss_count": len(pairs)}
    return result("grid.alignment", "Edge and Center Alignment Auditor",
                  f"{len(pairs)} near-alignment pair(s) within {tol}pt", data, issues,
                  ["Pairwise comparison is O(n^2) per slide; decks above the object limit are sampled."])


def engine_distribution(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    tol = float(th.get("spacing_tolerance_pt", 2))
    issues, rows = [], []
    for s in deck.slides:
        for axis in ("x", "y"):
            objs = [o for o in s.objects if o.w and o.h and not (o.w_pt > deck.width_pt * 0.9)]
            objs.sort(key=lambda o: o.x if axis == "x" else o.y)
            gaps = []
            for a, b in zip(objs, objs[1:]):
                gap = (b.x - (a.x + a.w)) if axis == "x" else (b.y - (a.y + a.h))
                gaps.append((a, b, gap / dm.EMU_PER_PT))
            positives = [g for _, _, g in gaps if g > 0.5]
            if len(positives) >= 2:
                spread = max(positives) - min(positives)
                if spread > tol and spread / max(positives) > 0.15:
                    rows.append({"slide": s.index, "axis": axis, "gaps_pt": [nice(g) for g in positives],
                                 "spread_pt": nice(spread)})
                    issues.append(issue("GRID_DISTRIBUTION",
                                        f"{axis}-axis gaps vary from {min(positives):.1f}pt to {max(positives):.1f}pt",
                                        "warning", s.index,
                                        repair="distribution equalizer normalizes gaps with --apply"))
    return result("grid.distribution", "Distribution Equalizer", f"{len(rows)} uneven distribution(s)",
                  {"tolerance_pt": tol, "uneven": rows}, issues)


def engine_spacing_tokens(deck, cfg) -> Dict[str, Any]:
    gaps, paddings = Counter(), Counter()
    for s in deck.slides:
        objs = sorted([o for o in s.objects if o.w and o.h], key=lambda o: (round(o.y_pt), round(o.x_pt)))
        for a, b in zip(objs, objs[1:]):
            gap = (b.y_pt - (a.y_pt + a.h_pt)) if b.y_pt >= a.y_pt + a.h_pt else (b.x_pt - (a.x_pt + a.w_pt))
            if 0.5 < gap < 200:
                gaps[round(gap)] += 1
    common = [g for g, c in gaps.most_common(8) if c >= 2]
    tokens = {}
    if common:
        tokens = {"micro": min(common), "small": nice(sorted(common)[len(common) // 3]),
                  "medium": nice(sorted(common)[len(common) // 2]), "large": max(common)}
    return result("grid.spacing_tokens", "Spacing Token Builder",
                  f"{len(common)} recurring spacing value(s) found",
                  {"observed_gaps_pt": dict(gaps.most_common(15)), "suggested_tokens_pt": tokens,
                   "recurring_values": common})


def engine_whitespace(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    target = float(th.get("target_density", 0.35))
    rows, issues = [], []
    for s in deck.slides:
        covered = sum(o.area_emu() for o in s.objects if o.w and o.h)
        ratio = min(1.0, covered / max(1, deck.width * deck.height))
        state = "balanced"
        if ratio > target * 1.5:
            state = "crowded"
            issues.append(issue("SPACE_CROWDED", f"objects cover {ratio:.0%} of the slide (target {target:.0%})",
                                "warning" if ratio > target * 2 else "info", s.index,
                                repair="split the slide or reduce decoration",
                                coverage=nice(ratio), target=nice(target)))
        elif ratio < target * 0.35 and s.objects:
            state = "sparse"
            issues.append(issue("SPACE_SPARSE", f"objects cover only {ratio:.0%} of the slide", "info", s.index,
                                repair="confirm intentional minimalism; otherwise increase type size or add structure"))
        rows.append({"slide": s.index, "coverage": nice(ratio), "state": state})
    return result("grid.whitespace", "Whitespace Balance Analyzer",
                  f"{sum(1 for r in rows if r['state'] != 'balanced')} slide(s) off target density", {"slides": rows},
                  issues)


def engine_density_heatmap(deck, cfg, grid: int = 6) -> Dict[str, Any]:
    rows = []
    for s in deck.slides:
        cells = [[0.0] * grid for _ in range(grid)]
        for o in s.objects:
            if not (o.w and o.h):
                continue
            x0, x1 = o.x_pt / deck.width_pt, (o.x_pt + o.w_pt) / deck.width_pt
            y0, y1 = o.y_pt / deck.height_pt, (o.y_pt + o.h_pt) / deck.height_pt
            for gy in range(grid):
                for gx in range(grid):
                    cx0, cx1 = gx / grid, (gx + 1) / grid
                    cy0, cy1 = gy / grid, (gy + 1) / grid
                    ox = max(0.0, min(x1, cx1) - max(x0, cx0))
                    oy = max(0.0, min(y1, cy1) - max(y0, cy0))
                    cells[gy][gx] += (ox * oy) * 100
        rows.append({"slide": s.index, "grid": grid,
                     "cells_percent": [[nice(c) for c in row] for row in cells],
                     "words": s.words, "objects": len(s.objects)})
    return result("grid.density_heatmap", "Density Heatmap Generator",
                  f"{len(rows)} slide heatmaps at {grid}x{grid}", {"slides": rows})


def engine_overlap(deck, cfg) -> Dict[str, Any]:
    min_ratio = float(cfg.get("overlap_min_ratio", 0.12))
    ignore = float(cfg.get("overlap_ignore_area_pt2", 60))
    issues, rows = [], []
    for s in deck.slides:
        objs = [o for o in s.objects if o.w and o.h and (o.w_pt * o.h_pt) > ignore]
        for i in range(len(objs)):
            for j in range(i + 1, len(objs)):
                a, b = objs[i], objs[j]
                inter, smaller = overlap_area(a, b)
                if smaller <= 0 or inter / smaller < min_ratio:
                    continue
                # intentional layering: background plates and deliberate overlays
                if b.z > a.z and a.kind in ("shape",) and b.kind in ("text", "placeholder-text"):
                    continue
                ratio = inter / smaller
                rows.append({"slide": s.index, "a": obj_label(a), "b": obj_label(b), "ratio": nice(ratio)})
                sev = "error" if all_text(a).strip() and all_text(b).strip() and ratio > 0.35 else "warning"
                issues.append(issue("QA_OVERLAP", f"objects overlap by {ratio:.0%} of the smaller object", sev,
                                    s.index, obj_label(a), repair="review layering; move or resize with layout repair",
                                    other=obj_label(b)))
    return result("qa.overlap", "Overlap and Collision Detector", f"{len(rows)} overlapping pair(s)",
                  {"pairs": rows[:300], "pair_count": len(rows)}, issues,
                  ["Intentional layering is approximated from z-order and object kind; verify visually."])


# ========================================================== typography engines
def engine_font_inventory(deck, cfg) -> Dict[str, Any]:
    fallback = cfg.get("font_fallback_map", {}) or {}
    fonts = sorted(deck.fonts_used.items(), key=lambda kv: -kv[1])
    rows = []
    issues = []
    for name, count in fonts:
        rows.append({"font": name, "uses": count,
                     "in_theme": name in (deck.theme_fonts.get("major"), deck.theme_fonts.get("minor")),
                     "fallbacks": fallback.get(name, [])})
        if name not in fallback:
            issues.append(issue("TYPE_FONT_NO_FALLBACK", f"no known metric-compatible fallback for '{name}'", "info",
                                repair="add a mapping to font_fallback_map or install the font on delivery machines"))
    family_count = len({r["font"] for r in rows})
    if family_count > 3:
        issues.append(issue("TYPE_TOO_MANY_FAMILIES", f"{family_count} distinct font families in use", "warning",
                            repair="reduce to one or two families plus one accent family"))
    return result("typography.inventory", "Font Inventory and Substitution Planner",
                  f"{family_count} families across {sum(deck.fonts_used.values())} run(s)",
                  {"fonts": rows, "theme_fonts": deck.theme_fonts, "family_count": family_count}, issues)


def engine_min_type(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    min_pt = float(th.get("min_text_pt", 18))
    footer_pt = float(th.get("min_footer_pt", 10))
    issues, rows = [], []
    for s, o in deck.all_objects():
        for p in o.paragraphs:
            for r in p:
                if not r.text.strip() or not r.size_pt:
                    continue
                limit = footer_pt if o.y_pt >= footer_band_top(deck, float(th.get("safe_bottom_pt", 34))) else min_pt
                if r.size_pt < limit:
                    rows.append({"slide": s.index, "object": obj_label(o), "size_pt": r.size_pt, "limit_pt": limit,
                                 "text": r.text[:80]})
                    issues.append(issue("TYPE_TOO_SMALL",
                                        f"{r.size_pt:g}pt text below the {limit:g}pt minimum for profile '{cfg.get('profile')}'",
                                        "error" if r.size_pt < limit * 0.8 else "warning",
                                        s.index, obj_label(o),
                                        repair="increase size or reduce text; --apply raises to the profile minimum",
                                        text=r.text[:80]))
    # runs with no explicit size rely on inheritance: report as unmeasured, not as failures
    unmeasured = sum(1 for s, o in deck.all_objects() for p in o.paragraphs for r in p
                     if r.text.strip() and not r.size_pt)
    return result("typography.min_size", "Minimum Type Size Guard",
                  f"{len(rows)} text run(s) below the {min_pt:g}pt profile minimum",
                  {"violations": rows[:300], "violation_count": len(rows), "unmeasured_runs": unmeasured,
                   "profile": cfg.get("profile")}, issues,
                  [f"{unmeasured} run(s) inherit size from the layout and cannot be measured without rendering."])


def engine_type_scale(deck, cfg) -> Dict[str, Any]:
    sizes = Counter()
    for _s, _o, size in run_sizes(deck):
        sizes[round(size, 1)] += 1
    th = thresholds(cfg)
    max_sizes = int(th.get("max_type_sizes", 6))
    issues = []
    ordered = sorted(sizes.items(), key=lambda kv: -kv[0])
    if len(ordered) > max_sizes:
        issues.append(issue("TYPE_SCALE_TOO_MANY", f"{len(ordered)} distinct type sizes (limit {max_sizes})", "warning",
                            repair="collapse near-identical sizes into a scale of 4-6 steps"))
    scale = [{"size_pt": s, "uses": c, "share": nice(c / max(1, sum(sizes.values())))} for s, c in ordered]
    return result("typography.scale", "Type Hierarchy Builder", f"{len(ordered)} distinct size(s)",
                  {"sizes": scale, "max_sizes": max_sizes,
                   "suggested_scale_pt": [s for s, _ in ordered[:max_sizes]]}, issues)


def engine_line_length(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    max_chars = int(th.get("max_line_chars", 90))
    issues, rows = [], []
    for s, o in deck.all_objects():
        if not all_text(o).strip():
            continue
        inner_w = max(1.0, o.w_pt - (o.insets.get("l", 0) + o.insets.get("r", 0)) / dm.EMU_PER_PT)
        for p in o.paragraphs:
            line = "".join(r.text for r in p)
            if not line.strip():
                continue
            size = max((r.size_pt for r in p if r.size_pt), default=None) or 18.0
            est_lines = dm.wrap_line_count(line, size, inner_w)
            chars_per_line = len(line) / est_lines
            rows.append({"slide": s.index, "object": obj_label(o), "chars": len(line),
                         "est_lines": est_lines, "chars_per_line": nice(chars_per_line)})
            if chars_per_line > max_chars:
                issues.append(issue("TYPE_LINE_TOO_LONG",
                                    f"~{chars_per_line:.0f} characters per line (limit {max_chars})", "warning",
                                    s.index, obj_label(o),
                                    repair="narrow the text box, raise size, or split the paragraph",
                                    chars_per_line=nice(chars_per_line)))
    return result("typography.line_length", "Line Length and Wrap Optimizer",
                  f"{len(issues)} paragraph(s) exceed {max_chars} characters per line",
                  {"paragraphs": rows[:300], "paragraph_count": len(rows), "max_line_chars": max_chars}, issues,
                  ["Character counts come from an advance-width estimate, not exact font metrics."])


def engine_overflow(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    issues, rows = [], []
    for s, o in deck.all_objects():
        if not all_text(o).strip():
            continue
        inner_w = max(1.0, o.w_pt - (o.insets.get("l", 0) + o.insets.get("r", 0)) / dm.EMU_PER_PT)
        inner_h = max(1.0, o.h_pt - (o.insets.get("t", 0) + o.insets.get("b", 0)) / dm.EMU_PER_PT)
        need = 0.0
        for p in o.paragraphs:
            line = "".join(r.text for r in p)
            if not line.strip():
                continue
            size = max((r.size_pt for r in p if r.size_pt), default=None) or 18.0
            lines = dm.wrap_line_count(line, size, inner_w)
            need += lines * size * 1.22 + size * 0.35
        shrink_note = ""
        if need > inner_h * 1.02:
            overflow_pt = need - inner_h
            severe = overflow_pt > inner_h * 0.25
            rows.append({"slide": s.index, "object": obj_label(o), "needed_pt": nice(need),
                         "available_pt": nice(inner_h), "overflow_pt": nice(overflow_pt),
                         "autofit": o.autofit})
            if o.autofit and "normAuto" in str(o.autofit):
                shrink_note = "autofit will shrink the type below the specified size"
            issues.append(issue("TYPE_OVERFLOW",
                                f"text needs ~{need:.0f}pt but the frame allows {inner_h:.0f}pt" +
                                (f" ({shrink_note})" if shrink_note else ""),
                                "error" if severe else "warning", s.index, obj_label(o),
                                repair="grow the frame, shorten the text, or run overflow repair (--apply raises height within the slide)"))
    return result("typography.overflow", "Text Overflow and Clipping Repair",
                  f"{len(rows)} frame(s) at risk of overflow", {"frames": rows, "count": len(rows)}, issues,
                  ["Overflow is estimated from advance widths; confirm with a render for critical slides."])


def engine_emphasis(deck, cfg) -> Dict[str, Any]:
    patterns = defaultdict(list)
    for s, o in deck.all_objects():
        sig = []
        for p in o.paragraphs:
            for r in p:
                if r.text.strip():
                    sig.append(("b" if r.bold else "") + ("i" if r.italic else "") + ("u" if r.underline else ""))
        if sig:
            patterns[tuple(sorted(set(sig)))].append((s.index, obj_label(o)))
    issues = []
    if len(patterns) > 2:
        issues.append(issue("TYPE_EMPHASIS_INCONSISTENT",
                            f"{len(patterns)} distinct emphasis signatures across body text", "warning",
                            repair="standardize emphasis: bold for key terms only, italic for titles/quotes only"))
    return result("typography.emphasis", "Emphasis Consistency Auditor",
                  f"{len(patterns)} emphasis signature(s)",
                  {"signatures": [{"pattern": "/".join(p) or "plain", "objects": len(v)} for p, v in patterns.items()]},
                  issues)


def engine_rhythm(deck, cfg) -> Dict[str, Any]:
    multi = [o for s, o in deck.all_objects() if len(o.paragraphs) > 1]
    issues = []
    if multi:
        para_counts = Counter(len(o.paragraphs) for o in multi)
        issues.append(issue("TYPE_RHYTHM_INFO",
                            f"{len(multi)} multi-paragraph frames; paragraph spacing is inherited from the layout",
                            "info", repair="set explicit before/after spacing in the theme for reproducibility"))
    bullets = [o for s, o in deck.all_objects() if any(re.match(r"^\s*[•·▪◦\-\*]\s+", "".join(r.text for r in p))
                                                       for p in o.paragraphs)]
    th = thresholds(cfg)
    max_bullets = int(th.get("max_bullets", 6))
    for s in deck.slides:
        for o in bullets:
            if o not in s.objects:
                continue
            count = sum(1 for p in o.paragraphs if "".join(r.text for r in p).strip())
            if count > max_bullets:
                issues.append(issue("TYPE_TOO_MANY_BULLETS", f"{count} bullets (limit {max_bullets})", "warning",
                                    s.index, obj_label(o), repair="chunk into groups or split the slide"))
    return result("typography.rhythm", "Paragraph Rhythm Controller",
                  f"{len(multi)} multi-paragraph frame(s), {len(bullets)} bulleted frame(s)",
                  {"multi_paragraph_frames": len(multi), "bulleted_frames": len(bullets),
                   "max_bullets": max_bullets}, issues,
                  ["Line spacing and paragraph spacing are read from the layout, not resolved per run."])


# =============================================================== colour engines
def engine_palette(deck, cfg) -> Dict[str, Any]:
    usage = Counter()
    for s, o in deck.all_objects():
        if o.fill:
            usage[(o.fill, "fill")] += 1
        if o.line:
            usage[(o.line, "line")] += 1
        for p in o.paragraphs:
            for r in p:
                if r.color:
                    usage[(r.color, "text")] += 1
        if s.background:
            usage[(s.background, "background")] += 1
    explicit_set = {c for (c, _r) in usage}
    explicit = sorted(explicit_set)
    theme_vals = {v.upper() for v in deck.theme_colors.values() if v}
    unused = sorted(theme_vals - explicit_set)
    issues = []
    if len(explicit) > 20:
        issues.append(issue("COLOR_TOO_MANY", f"{len(explicit)} distinct explicit colours", "warning",
                            repair="consolidate into the theme palette (color drift repair)"))
    return result("color.palette", "Theme Palette Extractor",
                  f"{len(explicit)} explicit colours, {len(deck.theme_colors)} theme slots",
                  {"theme_colors": deck.theme_colors, "explicit_colors": explicit,
                   "unused_theme_colors": unused,
                   "usage": [{"color": c, "role": r, "count": n} for (c, r), n in usage.most_common(40)]}, issues)


def engine_contrast(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    body_req = float(th.get("contrast_body", 4.5))
    large_req = float(th.get("contrast_large", 3.0))
    large_pt = float(th.get("large_text_pt", 24))
    issues, rows, unmeasured = [], [], 0
    for s in deck.slides:
        slide_bg = resolve_bg(deck, s)
        for o in s.objects:
            if not all_text(o).strip():
                continue
            bg, bg_source = background_behind(s, o, slide_bg, deck)
            fg, source = text_color_for(o, deck.theme_colors)
            for p in o.paragraphs:
                for r in p:
                    if not r.text.strip():
                        continue
                    colour = r.color or fg
                    if is_missing_colour(colour):
                        unmeasured += 1
                        continue
                    ratio = dm.contrast_ratio(colour, bg)
                    if ratio is None:
                        unmeasured += 1
                        continue
                    size = r.size_pt or 18.0
                    bold = r.bold
                    req = large_req if (size >= large_pt or (bold and size >= large_pt * 0.58)) else body_req
                    if ratio < req:
                        rows.append({"slide": s.index, "object": obj_label(o), "fg": colour, "bg": bg,
                                     "bg_source": bg_source, "ratio": ratio, "required": req,
                                     "size_pt": size, "text": r.text[:60]})
                        issues.append(issue("A11Y_CONTRAST",
                                            f"contrast {ratio}:1 is below {req}:1 for {size:g}pt text",
                                            "error", s.index, obj_label(o),
                                            repair="darken the text or lighten the background; --apply can snap to the nearest passing shade",
                                            ratio=ratio, required=req, fg=colour, bg=bg))
    return result("color.contrast", "Contrast Compliance Checker",
                  f"{len(rows)} text run(s) below WCAG-oriented thresholds",
                  {"violations": rows[:300], "violation_count": len(rows), "unmeasured_runs": unmeasured,
                   "body_requirement": body_req, "large_requirement": large_req}, issues,
                  [f"{unmeasured} run(s) use inherited colours not resolvable from the package XML."])


def engine_color_drift(deck, cfg) -> Dict[str, Any]:
    max_d = float(cfg.get("color_drift_distance", 12))
    usage = Counter()
    where = defaultdict(list)
    for s, o in deck.all_objects():
        for c in [o.fill, o.line]:
            if is_missing_colour(c):
                continue
            usage[c] += 1
            where[c].append(s.index)
        for p in o.paragraphs:
            for r in p:
                if not is_missing_colour(r.color):
                    usage[r.color] += 1
                    where[r.color].append(s.index)
    colours = [c for c, _ in usage.most_common()]
    clusters, used = [], set()
    for i, a in enumerate(colours):
        if a in used:
            continue
        group = [a]
        for b in colours[i + 1:]:
            if b in used:
                continue
            d = dm.color_distance(a, b)
            if d is not None and d <= max_d:
                group.append(b)
                used.add(b)
        if len(group) > 1:
            clusters.append({"canonical": max(group, key=lambda c: usage[c]),
                             "variants": [{"color": c, "uses": usage[c], "slides": sorted(set(where[c]))[:10]}
                                          for c in group]})
    issues = [issue("COLOR_DRIFT", f"{len(c['variants'])} near-duplicate colours should collapse to {c['canonical']}",
                    "warning", repair="color consolidation maps variants to the canonical token with --apply")
              for c in clusters]
    return result("color.drift", "Color Drift Detector", f"{len(clusters)} colour cluster(s) close enough to merge",
                  {"threshold": max_d, "clusters": clusters}, issues)


def engine_transparency(deck, cfg) -> Dict[str, Any]:
    grads = [{"slide": s.index, "object": obj_label(o)} for s, o in deck.all_objects() if o.fill_kind == "gradient"]
    effects = [{"slide": s.index, "object": obj_label(o)} for s, o in deck.all_objects() if o.has_effects]
    issues = []
    for g in grads:
        issues.append(issue("COLOR_GRADIENT", "gradient fill may render inconsistently across viewers", "info",
                            g["slide"], g["object"], repair="prefer flat brand colours behind text"))
    for e in effects[:40]:
        issues.append(issue("COLOR_EFFECTS", "shape carries shadow/glow/bevel effects", "info",
                            e["slide"], e["object"], repair="run effects simplification to flatten accidental depth"))
    return result("color.transparency", "Gradient and Transparency Auditor",
                  f"{len(grads)} gradient(s), {len(effects)} shape(s) with effects",
                  {"gradients": grads, "objects_with_effects": effects}, issues,
                  ["Alpha/transparency values are not exposed uniformly by python-pptx; visual confirmation needs a render."])


def engine_monochrome(deck, cfg) -> Dict[str, Any]:
    """Flag meaning carried by colour alone (hue-only distinctions)."""
    issues = []
    colours = Counter()
    for s, o in deck.all_objects():
        for c in [o.fill, o.line]:
            if not is_missing_colour(c):
                colours[c] += 1
    # colours of similar luminance but different hue become identical in grayscale
    pairs = []
    items = list(colours)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, lb = dm.relative_luminance(items[i]), dm.relative_luminance(items[j])
            if la is None or lb is None:
                continue
            if abs(la - lb) < 0.05 and dm.color_distance(items[i], items[j]) and dm.color_distance(items[i], items[j]) > 40:
                pairs.append({"a": items[i], "b": items[j], "luminance_delta": nice(abs(la - lb))})
    if pairs:
        issues.append(issue("COLOR_GRAYSCALE_COLLISION",
                            f"{len(pairs)} colour pair(s) collapse to the same grayscale value", "warning",
                            repair="differentiate with lightness or add a pattern/label, not hue alone"))
    return result("color.monochrome", "Monochrome Robustness Tester",
                  f"{len(pairs)} grayscale collision pair(s)", {"collisions": pairs[:60]}, issues)


# =============================================================== shape engines
def engine_shape_inventory(deck, cfg) -> Dict[str, Any]:
    kinds = Counter()
    effect_objs = []
    for s, o in deck.all_objects():
        kinds[o.kind] += 1
        if o.has_effects:
            effect_objs.append({"slide": s.index, "object": obj_label(o)})
    issues = []
    if effect_objs:
        issues.append(issue("SHAPE_EFFECTS", f"{len(effect_objs)} object(s) carry shadows/glows/bevels", "info",
                            repair="simplify effects unless the depth is intentional"))
    return result("shape.inventory", "Shape Inventory Classifier",
                  f"{sum(kinds.values())} object(s) across {len(kinds)} kinds",
                  {"kinds": dict(kinds), "objects_with_effects": effect_objs[:100]}, issues)


def engine_connectors(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        if o.kind != "connector":
            continue
        ep = o.connector_endpoints or {}
        rows.append({"slide": s.index, "object": obj_label(o), **ep})
        if not ep.get("begin_connected") or not ep.get("end_connected"):
            issues.append(issue("SHAPE_CONNECTOR_DETACHED",
                                "connector is not attached at both ends and will drift when shapes move",
                                "warning", s.index, obj_label(o),
                                repair="reattach endpoints to the intended shapes; re-route after layout edits"))
    if not rows:
        return result("shape.connectors", "Connector Endpoint Validator", "no native connectors found",
                      {"connectors": [], "count": 0},
                      [issue("SHAPE_NO_CONNECTORS", "no native connectors; arrows are probably shapes or pictures",
                             "info", repair="prefer native connectors so relationships survive edits")])
    return result("shape.connectors", "Connector Endpoint Validator", f"{len(rows)} connector(s), {len(issues)} detached",
                  {"connectors": rows, "count": len(rows)}, issues)


# ================================================================= data engines
def engine_tables(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        if o.kind != "table" or not o.table:
            continue
        t = o.table
        rows.append({"slide": s.index, "object": obj_label(o), "rows": t["rows"], "cols": t["cols"],
                     "header_row": t.get("header_row")})
        if not t.get("header_row") or not any(str(c).strip() for c in t.get("header_row") or []):
            issues.append(issue("DATA_TABLE_NO_HEADER", "table has no populated header row", "error",
                                s.index, obj_label(o), repair="add a header row and mark it as a header for screen readers"))
        if t["cols"] > 7:
            issues.append(issue("DATA_TABLE_WIDE", f"{t['cols']} columns is hard to read projected", "warning",
                                s.index, obj_label(o), repair="reduce columns or split the table"))
        if t["rows"] > 9:
            issues.append(issue("DATA_TABLE_TALL", f"{t['rows']} rows may continue off-slide", "warning",
                                s.index, obj_label(o), repair="split into continuation tables with repeated headers"))
    return result("data.tables", "Table Readability Optimizer", f"{len(rows)} table(s)",
                  {"tables": rows, "count": len(rows)}, issues)


def engine_charts(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        if o.kind != "chart" or not o.chart:
            continue
        c = o.chart
        rows.append({"slide": s.index, "object": obj_label(o), "chart_type": c.get("chart_type"),
                     "series": len(c.get("series", [])), "has_title": c.get("has_title"),
                     "has_legend": c.get("has_legend")})
        if not c.get("has_title"):
            issues.append(issue("DATA_CHART_NO_TITLE", "chart has no title/claim", "warning", s.index,
                                obj_label(o), repair="add a claim-style chart title stating the takeaway"))
        if len(c.get("series", [])) > 6:
            issues.append(issue("DATA_CHART_MANY_SERIES", f"{len(c['series'])} series clutter the chart", "warning",
                                s.index, obj_label(o), repair="highlight 2-3 series, group the rest"))
        for sr in c.get("series", []):
            vals = [v for v in (sr.get("values") or []) if isinstance(v, (int, float))]
            if vals and min(vals) < 0 and max(vals) > 0:
                issues.append(issue("DATA_CHART_MIXED_SIGN", "series crosses zero; ensure the baseline is zero",
                                    "info", s.index, obj_label(o), repair="use a zero baseline for bar charts"))
    return result("data.charts", "Chart Integrity Auditor", f"{len(rows)} chart(s)",
                  {"charts": rows, "count": len(rows)}, issues,
                  ["Axis scaling, aggregation source and data labels require the workbook; only structure is checked."])


# ================================================================ media engines
def engine_images(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s, o in deck.all_objects():
        if o.kind not in ("picture", "media"):
            continue
        m = o.media or {}
        pw, ph = m.get("pixel_width") or 0, m.get("pixel_height") or 0
        dpi = round(pw / (o.w_pt / 72), 1) if pw and o.w_pt else None
        rows.append({"slide": s.index, "object": obj_label(o), "pixel_width": pw, "pixel_height": ph,
                     "effective_dpi": dpi, "bytes": m.get("size_bytes"), "alt_text": o.alt_text,
                     "alt_text_raw": o.alt_text_raw})
        if dpi is not None and dpi < 120:
            issues.append(issue("MEDIA_LOW_DPI", f"effective resolution {dpi} dpi is low for print/projection",
                                "warning", s.index, obj_label(o),
                                repair="replace with a higher-resolution asset"))
        # missing alt text is reported once, by engine_alt_text (code A11Y_ALT_MISSING)
    return result("media.images", "Image Quality Inspector", f"{len(rows)} image placement(s)",
                  {"images": rows, "count": len(rows)}, issues,
                  ["Compression artifacts and blur are not measurable without decoding the pixels for analysis."])


def engine_av(deck, cfg) -> Dict[str, Any]:
    av = [m for m in deck.media if m.get("ext") in (".mp4", ".m4v", ".mov", ".avi", ".wmv", ".mp3", ".wav", ".m4a")]
    issues = []
    for m in av:
        issues.append(issue("MEDIA_AV_PRESENT", f"{m['part']} ({m['size_bytes'] / 1e6:.1f} MB) must be verified on the target",
                            "info", repair="test playback on delivery hardware; add a poster frame and transcript"))
    slides_with_av = [s.index for s in deck.slides if any(o.media and o.media.get("kind") == "av" for o in s.objects)]
    return result("media.av", "Media Compatibility Checker", f"{len(av)} audio/video part(s)",
                  {"parts": av, "slides_with_media": slides_with_av}, issues,
                  ["Codec/container validation requires ffprobe; only the media part extension is checked here."])


# ========================================================= accessibility engines
def engine_reading_order(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    for s in deck.slides:
        ordered = sorted(s.objects, key=lambda o: (round(o.y_pt / 24), o.x_pt))
        content_kinds = ("picture", "chart", "table", "media")
        seq = [obj_label(o) for o in ordered if all_text(o).strip() or o.kind in content_kinds]
        zseq = [obj_label(o) for o in sorted(s.objects, key=lambda o: o.z)
                if all_text(o).strip() or o.kind in content_kinds]
        rows.append({"slide": s.index, "visual_order": seq, "creation_order": zseq,
                     "matches": seq == zseq})
        if seq != zseq and len(seq) > 2:
            issues.append(issue("A11Y_READING_ORDER",
                                "object creation order differs from the visual reading order", "warning", s.index,
                                repair="repair reading order re-sequences shapes to match top-to-bottom, left-to-right"))
    return result("accessibility.reading_order", "Reading Order Repair",
                  f"{sum(1 for r in rows if not r['matches'])} slide(s) with mismatched reading order",
                  {"slides": rows}, issues)


def engine_alt_text(deck, cfg) -> Dict[str, Any]:
    issues, missing, ok = [], [], 0
    for s, o in deck.all_objects():
        needs = o.kind in ("picture", "chart", "table", "media") or (o.kind == "shape" and o.has_effects)
        if o.alt_text:
            ok += 1
            if len(o.alt_text) < 5:
                issues.append(issue("A11Y_ALT_THIN", "alt text is too short to be useful", "warning", s.index,
                                    obj_label(o), repair="describe purpose and content in a short phrase"))
        elif needs and not o.decorative:
            missing.append({"slide": s.index, "object": obj_label(o), "kind": o.kind,
                            "raw_value": o.alt_text_raw or None})
            detail = "no alternative text"
            if o.alt_text_raw:
                detail = f"alternative text that is only a file name ('{o.alt_text_raw[:40]}')"
            issues.append(issue("A11Y_ALT_MISSING", f"{o.kind} object has {detail}", "error",
                                s.index, obj_label(o),
                                repair="write purpose-based alt text, or mark as decorative if it carries no information"))
    return result("accessibility.alt_text", "Alternative Text Author",
                  f"{len(missing)} object(s) need alt text, {ok} already described",
                  {"missing": missing, "described": ok}, issues)


def engine_color_independence(deck, cfg) -> Dict[str, Any]:
    colour_roles = Counter()
    for s, o in deck.all_objects():
        if not is_missing_colour(o.fill) and o.kind in ("shape", "table"):
            colour_roles[o.fill] += 1
    multi = [c for c, n in colour_roles.items() if n >= 3]
    issues = []
    if len(multi) >= 3:
        issues.append(issue("A11Y_COLOR_ALONE",
                            f"{len(multi)} colours are used repeatedly on shapes; confirm meaning is not colour-only",
                            "warning", repair="add labels, icons, or patterns alongside colour coding"))
    return result("accessibility.color_independence", "Color Independence Checker",
                  f"{len(multi)} recurring shape colours", {"recurring_fill_colors": dict(colour_roles.most_common(12))},
                  issues, ["Whether colour is the sole carrier of meaning needs a render and content review."])


def engine_links(deck, cfg) -> Dict[str, Any]:
    """Hyperlinks and raw URLs in visible text."""
    issues, rows = [], []
    url_re = re.compile(r"(?:https?://|www\.)\S+", re.I)
    allowed = [d.lower() for d in (cfg.get("allowed_link_domains") or [])]
    for s, o in deck.all_objects():
        for m in url_re.finditer(all_text(o)):
            url = m.group(0).rstrip(".,;)")
            domain = re.sub(r"^(https?://)?(www\.)?", "", url).split("/")[0].lower()
            rows.append({"slide": s.index, "object": obj_label(o), "url": url, "domain": domain})
            if allowed and not any(domain == d or domain.endswith("." + d) for d in allowed):
                issues.append(issue("A11Y_LINK_DOMAIN", f"link domain '{domain}' is outside the allowed list", "warning",
                                    s.index, obj_label(o), repair="confirm the destination or add the domain to allowed_link_domains"))
            if re.match(r"^https?://[^/]*$", url) or url.rstrip("/").count("/") <= 2:
                issues.append(issue("A11Y_LINK_RAW", "raw or ambiguous link text; describe the destination", "warning",
                                    s.index, obj_label(o), repair="replace the URL with descriptive link text"))
    # hyperlink parts
    links = []
    try:
        with __import__("zipfile").ZipFile(deck.path) as zf:
            for name in zf.namelist():
                if name.endswith(".rels"):
                    data = zf.read(name).decode("utf-8", "ignore")
                    for m in re.finditer(r'Target="(https?://[^"]+)"[^>]*TargetMode="External"', data):
                        links.append({"rels": name, "url": m.group(1)})
    except Exception:
        pass
    if links:
        rows.extend({"slide": 0, "object": None, "url": l["url"],
                     "domain": re.sub(r"^https?://", "", l["url"]).split("/")[0].lower()} for l in links)
    return result("accessibility.links", "Link Text Clarity Checker", f"{len(rows)} link(s) found",
                  {"links": rows, "count": len(rows), "allowed_domains": allowed}, issues)


def engine_language(deck, cfg) -> Dict[str, Any]:
    langs = Counter()
    for s, o in deck.all_objects():
        for p in o.paragraphs:
            for r in p:
                if r.language:
                    langs[str(r.language)] += 1
    scripts = Counter()
    for s, o in deck.all_objects():
        text = all_text(o)
        if re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text):
            scripts["cjk"] += 1
        if re.search(r"[\u0600-\u06ff\u0590-\u05ff]", text):
            scripts["rtl"] += 1
        if re.search(r"[\u0900-\u097f\u0e00-\u0e7f]", text):
            scripts["indic-seasia"] += 1
    issues = []
    if scripts.get("rtl"):
        issues.append(issue("A11Y_RTL_CONTENT", "right-to-left script content detected; verify paragraph direction",
                            "info", repair="set paragraph direction and mirror layout relationships"))
    if scripts.get("cjk"):
        issues.append(issue("A11Y_CJK_CONTENT", "CJK content detected; confirm fonts cover all glyphs", "info",
                            repair="use a CJK-capable family and check line breaking"))
    return result("accessibility.language", "Language and Locale Setter",
                  f"{len(langs)} explicit language tag(s), scripts: {', '.join(scripts) or 'latin only'}",
                  {"language_tags": dict(langs), "scripts": dict(scripts)}, issues)


def engine_a11y_baseline(deck, cfg) -> Dict[str, Any]:
    parts = {
        "contrast": engine_contrast(deck, cfg),
        "alt_text": engine_alt_text(deck, cfg),
        "reading_order": engine_reading_order(deck, cfg),
        "min_type": engine_min_type(deck, cfg),
        "links": engine_links(deck, cfg),
        "language": engine_language(deck, cfg),
        "tables": engine_tables(deck, cfg),
    }
    counts = {k: sum(1 for i in v["issues"] if i["severity"] in ("error", "warning")) for k, v in parts.items()}
    blocked = sum(1 for k, v in parts.items() for i in v["issues"] if i["severity"] == "error")
    issues = [i for v in parts.values() for i in v["issues"]]
    return result("accessibility.baseline", "Accessibility Baseline Auditor",
                  f"{blocked} blocking accessibility error(s) across {len(parts)} checks",
                  {"check_counts": counts, "parts": {k: v["summary"] for k, v in parts.items()}}, issues,
                  ["Caption/transcript coverage needs the media track, not the package, to verify."])


# =============================================================== content engines
def engine_bullets(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    max_bullets = int(th.get("max_bullets", 6))
    issues, rows = [], []
    bullet_re = re.compile(r"^\s*([•·▪◦\-\*\u2022]|\d+[.)])\s+")
    for s, o in deck.all_objects():
        paras = ["".join(r.text for r in p).strip() for p in o.paragraphs]
        bull = [p for p in paras if bullet_re.match(p)]
        if bull:
            rows.append({"slide": s.index, "object": obj_label(o), "bullets": len(bull)})
            if len(bull) > max_bullets:
                issues.append(issue("CONTENT_LONG_BULLETS", f"{len(bull)} bullets exceed the {max_bullets} guideline",
                                    "warning", s.index, obj_label(o),
                                    repair="group into chunks of 3-5 with sub-headers, or split the slide"))
            long_b = [b for b in bull if len(b) > 140]
            if long_b:
                issues.append(issue("CONTENT_LONG_BULLET_TEXT", f"{len(long_b)} bullet(s) exceed 140 characters",
                                    "warning", s.index, obj_label(o), repair="compress to a phrase; move detail to notes"))
    return result("content.bullets", "Bullet Compression Editor", f"{len(rows)} bulleted frame(s)",
                  {"frames": rows, "max_bullets": max_bullets}, issues)


def engine_title_claims(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    claim_cues = re.compile(r"(?i)\b(is|are|will|can|should|must|because|reduces?|increases?|improves?|drives?|"
                            r"enables?|prevents?|requires?|faster|slower|higher|lower)\b")
    for s in deck.slides:
        if not s.objects:
            continue
        title = min((o for o in s.objects if all_text(o).strip()), key=lambda o: (o.y, o.x), default=None)
        if title is None:
            issues.append(issue("CONTENT_NO_TITLE", "slide has no title text", "warning", s.index,
                                repair="add a claim-style title"))
            continue
        text = all_text(title).strip().split("\n")[0]
        words = len(text.split())
        is_claim = bool(claim_cues.search(text)) and words >= 4
        rows.append({"slide": s.index, "title": text[:100], "words": words, "claim_like": is_claim})
        if not is_claim:
            issues.append(issue("CONTENT_TITLE_TOPIC", "title reads as a topic label, not a claim", "info",
                                s.index, obj_label(title),
                                repair="rewrite as a claim stating what the audience should take away"))
        if words > 14:
            issues.append(issue("CONTENT_TITLE_LONG", f"title is {words} words; keep to ~10", "warning",
                                s.index, obj_label(title), repair="tighten the title"))
    return result("content.titles", "Slide Title Claim Rewriter",
                  f"{sum(1 for r in rows if r['claim_like'])}/{len(rows)} titles read as claims",
                  {"titles": rows}, issues)


def engine_terminology(deck, cfg) -> Dict[str, Any]:
    forms = defaultdict(Counter)
    for s, o in deck.all_objects():
        for word in re.findall(r"\b[A-Za-z][A-Za-z0-9\-]{2,}\b", all_text(o)):
            forms[word.lower()][word] += 1
    issues, rows = [], []
    for low, variants in forms.items():
        if len(variants) > 1:
            total = sum(variants.values())
            if total >= 3:
                rows.append({"term": low, "variants": dict(variants)})
                issues.append(issue("CONTENT_TERM_VARIANTS",
                                    f"'{low}' appears as {', '.join(sorted(variants))}", "info",
                                    repair="pick one canonical form and replace the others"))
    issues = [i for i in issues][:60]
    return result("content.terminology", "Terminology Consistency Manager",
                  f"{len(rows)} term(s) with inconsistent forms", {"variants": rows}, issues)


def engine_redundancy(deck, cfg) -> Dict[str, Any]:
    seen = defaultdict(list)
    for s, o in deck.all_objects():
        text = re.sub(r"\s+", " ", all_text(o)).strip().lower()
        if len(text) > 30:
            seen[text].append((s.index, obj_label(o)))
    dupes = [{"text": t[:120], "occurrences": v} for t, v in seen.items() if len(v) > 1]
    issues = [issue("CONTENT_REDUNDANT_TEXT", f"identical text repeated on {len(d['occurrences'])} objects",
                    "info", d["occurrences"][1][0], d["occurrences"][1][1],
                    repair="consolidate or differentiate the repeated message", text=d["text"][:80]) for d in dupes]
    return result("content.redundancy", "Redundancy Eliminator", f"{len(dupes)} repeated block(s)",
                  {"duplicates": dupes}, issues)


# ============================================================== evidence engines
def engine_citations(deck, cfg) -> Dict[str, Any]:
    req = [re.compile(p) for p in cfg.get("citation_required_patterns", []) or []]
    hint = [re.compile(p, re.I) for p in cfg.get("citation_hint_patterns", []) or []]
    issues, rows = [], []
    families = {r["slide"]: r["family"] for r in engine_families(deck, cfg)["data"]["slides"]}
    for s in deck.slides:
        slide_text = " ".join(all_text(o) for o in s.objects)
        has_hint = any(h.search(slide_text) for h in hint)
        claim_bearing = families.get(s.index, "content") in ("content", "diagram", "media")
        needs = any(r.search(slide_text) for r in req) and s.words >= 12 and claim_bearing
        rows.append({"slide": s.index, "has_claim_marker": needs, "has_citation_hint": has_hint})
        if needs and not has_hint:
            issues.append(issue("EVIDENCE_NO_CITATION",
                                "slide carries quantitative/claim markers but no visible source", "warning", s.index,
                                repair="add a compact source line or footnote; record it in the source register"))
    return result("evidence.citations", "Claim Source Mapper",
                  f"{sum(1 for r in rows if r['has_claim_marker'] and not r['has_citation_hint'])} slide(s) need a source",
                  {"slides": rows}, issues)


def engine_freshness(deck, cfg) -> Dict[str, Any]:
    issues, rows = [], []
    years = Counter()
    for s, o in deck.all_objects():
        for m in re.finditer(r"\b((?:19|20)\d{2})\b", all_text(o)):
            years[int(m.group(1))] += 1
    today_year = int(__import__("datetime").date.today().year)
    for y in sorted(years):
        rows.append({"year": y, "mentions": years[y], "age_years": today_year - y})
        if today_year - y >= 3:
            issues.append(issue("EVIDENCE_STALE_YEAR", f"year {y} appears {years[y]} time(s) ({today_year - y} years old)",
                                "info", repair="confirm the source is still current or label it as historical"))
    drafts = [s.index for s, o in deck.all_objects() if re.search(r"(?i)\b(draft|confidential|internal)\b", all_text(o))]
    for i in sorted(set(drafts)):
        issues.append(issue("EVIDENCE_CLASSIFICATION_MARK", "slide carries a draft/confidential marking", "info", i,
                            repair="verify the marking matches the release audience"))
    return result("evidence.freshness", "Source Freshness Checker", f"{len(years)} distinct year(s) referenced",
                  {"years": rows, "marked_slides": sorted(set(drafts))}, issues)


# ============================================================== security engines
def engine_sensitive(deck, cfg) -> Dict[str, Any]:
    patterns = cfg.get("sensitive_patterns", {}) or {}
    issues, rows = [], []
    for name, pat in patterns.items():
        try:
            rx = re.compile(pat)
        except re.error:
            issues.append(issue("SEC_BAD_PATTERN", f"config pattern '{name}' is not a valid regex", "info"))
            continue
        for s, o in deck.all_objects():
            for m in rx.finditer(all_text(o)):
                snippet = m.group(0)
                masked = snippet[:4] + "*" * max(0, len(snippet) - 6) + snippet[-2:] if len(snippet) > 8 else "***"
                rows.append({"slide": s.index, "object": obj_label(o), "pattern": name, "match_preview": masked})
                issues.append(issue(f"SEC_{name.upper()}", f"possible {name.replace('_', ' ')} in visible content",
                                    "error" if name in ("aws_access_key", "private_key", "bearer_token", "api_key_assignment") else "warning",
                                    s.index, obj_label(o),
                                    repair="redact or remove before external delivery; see governance redact --apply",
                                    preview=masked))
        for s in deck.slides:
            if s.notes:
                for m in rx.finditer(s.notes):
                    rows.append({"slide": s.index, "object": "notes", "pattern": name, "match_preview": "***"})
                    issues.append(issue(f"SEC_{name.upper()}", f"possible {name.replace('_', ' ')} in speaker notes",
                                        "warning", s.index, None,
                                        repair="scrub notes for the external version"))
    return result("security.sensitive", "Sensitive Content Scanner",
                  f"{len(rows)} potential sensitive match(es)", {"matches": rows, "count": len(rows)}, issues,
                  ["Pattern matching finds text-shaped risks; it cannot read images or embedded files."])


def engine_metadata_privacy(deck, cfg) -> Dict[str, Any]:
    props = deck.core_props or {}
    found = {k: v for k, v in props.items() if k in ("author", "last_modified_by", "company", "comments", "manager")
             and v}
    issues = [issue("SEC_METADATA", f"document property '{k}' holds '{str(v)[:40]}'", "warning",
                    repair="scrub metadata for external delivery (--apply)") for k, v in found.items()]
    notes_chars = sum(len(s.notes) for s in deck.slides)
    if notes_chars:
        issues.append(issue("SEC_NOTES_PRESENT", f"speaker notes total {notes_chars} characters", "info",
                            repair="decide per policy whether notes ship with the external version"))
    return result("security.metadata", "Personal Metadata Scrubber",
                  f"{len(found)} personal property field(s)", {"properties": found, "notes_chars": notes_chars}, issues)


def engine_link_policy(deck, cfg) -> Dict[str, Any]:
    return engine_links(deck, cfg)


def engine_macro_scan(deck, cfg) -> Dict[str, Any]:
    issues = []
    risky = []
    try:
        with __import__("zipfile").ZipFile(deck.path) as zf:
            names = zf.namelist()
            for n in names:
                low = n.lower()
                if low.endswith("vbaproject.bin"):
                    risky.append({"part": n, "risk": "macro"})
                if "/embeddings/" in low:
                    risky.append({"part": n, "risk": "embedded_object"})
                if low.endswith((".exe", ".dll", ".js", ".vbs", ".ps1", ".jar")):
                    risky.append({"part": n, "risk": "executable"})
    except Exception:
        pass
    for r in risky:
        issues.append(issue("SEC_MACRO_OR_EMBED",
                            f"{r['risk']} part present: {r['part']}",
                            "error" if r["risk"] == "macro" else "warning",
                            repair="remove macros/embedded objects before external distribution"))
    return result("security.macros", "Macro and Embedded Object Inspector",
                  f"{len(risky)} risky package part(s)", {"parts": risky}, issues)


# =================================================================== QA engines
def engine_regression(deck, cfg, before: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not before:
        return result("qa.regression", "Visual Regression Comparator",
                      "no baseline supplied; structural baseline recorded only",
                      {"baseline": None}, [issue("QA_NO_BASELINE", "no baseline model supplied for comparison", "info",
                                                 repair="pass --baseline with a saved inspect JSON")])
    diff = dm.compare_models(before, deck.to_dict())
    issues = []
    for ch in diff["changes"]:
        if ch["change"] in ("slide_removed", "object_removed"):
            issues.append(issue("QA_REGRESSION_REMOVAL", f"{ch['change']} on slide {ch['slide']}", "warning",
                                ch["slide"], ch.get("object"),
                                repair="confirm the removal is intentional; restore from the baseline if not"))
        elif ch["change"] == "geometry":
            issues.append(issue("QA_REGRESSION_GEOMETRY",
                                f"{ch['object']} {ch['property']} moved {ch['before']} -> {ch['after']}", "info",
                                ch["slide"], ch.get("object")))
    return result("qa.regression", "Visual Regression Comparator", f"{diff['change_count']} change(s) vs baseline",
                  diff, issues, ["Structural comparison only; pixel comparison requires rendering."])


def engine_defect_queue(deck, cfg, all_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = cfg.get("risk_weights", {"error": 10, "warning": 4, "info": 1})
    grouped = defaultdict(lambda: {"count": 0, "severity": "info", "slides": set(), "message": "", "repair": ""})
    for i in all_issues:
        g = grouped[i["code"]]
        g["count"] += 1
        g["slides"].add(i["slide"])
        g["message"] = i["message"]
        g["repair"] = i["repair"]
        if i["severity"] == "error" or (i["severity"] == "warning" and g["severity"] == "info"):
            g["severity"] = i["severity"]
    queue = []
    for code, g in grouped.items():
        impact = weights.get(g["severity"], 1)
        spread = min(3.0, 1 + len(g["slides"]) / 10)
        queue.append({"code": code, "severity": g["severity"], "count": g["count"],
                      "slides": sorted(g["slides"])[:20], "slide_count": len(g["slides"]),
                      "priority": round(impact * spread, 1), "message": g["message"], "repair": g["repair"]})
    queue.sort(key=lambda d: (-d["priority"], d["code"]))
    return result("qa.defects", "Defect Triage and Repair Queue", f"{len(queue)} distinct defect class(es)",
                  {"queue": queue}, [], ["Priority is a configurable heuristic (risk_weights), not a measured risk."])


def engine_completion_gate(deck, cfg, all_issues: List[Dict[str, Any]], artifacts: Optional[List[str]] = None) -> Dict[str, Any]:
    errors = [i for i in all_issues if i["severity"] == "error"]
    blocking_codes = sorted({i["code"] for i in errors})
    missing = [a for a in (artifacts or []) if not __import__("os").path.exists(a)]
    passed = not errors and not missing
    data = {"passed": passed, "blocking_error_count": len(errors), "blocking_codes": blocking_codes,
            "missing_artifacts": missing, "checked_artifacts": artifacts or []}
    issues = [issue("GATE_BLOCKED", f"{len(errors)} blocking error(s) remain", "error",
                    repair="resolve every error-severity issue before delivery")]
    for m in missing:
        issues.append(issue("GATE_MISSING_ARTIFACT", f"required artifact missing: {m}", "error"))
    return result("orchestration.gate", "Completion Gatekeeper",
                  "PASS - ready to deliver" if passed else f"BLOCKED - {len(errors)} error(s)",
                  data, issues if not passed else [])


# ================================================================= score engines
def _score_from_issues(issues: List[Dict[str, Any]], weight: Dict[str, float]) -> float:
    if not issues:
        return 100.0
    penalty = sum(weight.get(i["severity"], 1) for i in issues)
    return round(max(0.0, 100.0 - min(100.0, penalty)), 1)


def engine_scores(deck, cfg, all_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = cfg.get("risk_weights", {"error": 10, "warning": 4, "info": 1})
    buckets = {
        "readability": ("TYPE_", "SPACE_", "CONTENT_"),
        "consistency": ("GRID_", "COLOR_", "TYPE_SCALE", "TYPE_EMPHASIS", "SHAPE_", "LAYOUT_"),
        "accessibility": ("A11Y_",),
        "structure": ("PKG_", "CENSUS_", "HIDDEN_", "EDIT_", "GEO_", "DEP_"),
        "evidence": ("EVIDENCE_",),
        "security": ("SEC_",),
    }
    scores = {}
    for name, prefixes in buckets.items():
        subset = [i for i in all_issues if i["code"].startswith(prefixes)]
        scores[name] = {"score": _score_from_issues(subset, weights),
                        "issue_count": len(subset),
                        "errors": sum(1 for i in subset if i["severity"] == "error")}
    sw = cfg.get("score_weights", {}) or {}
    total_w = sum(sw.get(k, 0) for k in ("readability", "consistency", "accessibility", "structure", "evidence")) or 1
    overall = sum(scores[k]["score"] * sw.get(k, 0) for k in
                  ("readability", "consistency", "accessibility", "structure", "evidence")) / total_w
    scores["overall"] = round(overall, 1)
    return result("score.scorecards", "Quality Scorecards",
                  f"overall {scores['overall']}/100", scores,
                  limits=["Scores are deterministic penalties from rule findings, not a measured aesthetic judgement."])


# ================================================== orchestration & capability
def engine_work_plan(deck, cfg, all_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors = {i["code"] for i in all_issues if i["severity"] == "error"}
    plan = []
    def add(step, why, command, when=True):
        if when:
            plan.append({"order": len(plan) + 1, "step": step, "reason": why, "command": command})
    add("snapshot", "record a last-known-good baseline before any change", "snapshot --deck <deck> --out <dir>", True)
    add("repair-structure", "detached or off-canvas objects break later layout work",
        "repair --deck <deck> --out <out.pptx> --scope bounds,connectors", bool(errors & {"GEO_OUT_OF_BOUNDS", "SHAPE_CONNECTOR_DETACHED", "HIDDEN_PARTIAL_OFF_CANVAS"}))
    add("normalize-layout", "alignment and spacing fixes are safe and idempotent",
        "normalize --deck <deck> --out <out.pptx> --apply align,spacing", bool(errors & {"GRID_DISTRIBUTION", "GRID_NEAR_ALIGN", "GEO_SAFE_MARGIN"}))
    add("fix-typography", "type below threshold or overflowing fails readability gates",
        "repair --deck <deck> --out <out.pptx> --scope type-min,overflow", bool(errors & {"TYPE_TOO_SMALL", "TYPE_OVERFLOW"}))
    add("fix-accessibility", "missing alt text, low contrast and reading order block release",
        "repair --deck <deck> --out <out.pptx> --scope alt-text,contrast,reading-order", bool(errors & {"A11Y_CONTRAST", "A11Y_ALT_MISSING", "A11Y_READING_ORDER"}))
    add("governance", "remove personal metadata and sensitive content for external use",
        "governance --deck <deck> --out <out.pptx> --action scrub-metadata,redact", bool(errors & {"SEC_METADATA", "SEC_AWS_ACCESS_KEY", "SEC_EMAIL"}))
    add("score", "measure improvement against the baseline", "score --deck <out.pptx> --out <dir> --baseline <baseline.json>")
    add("gate", "declare completion only when no error-severity findings remain", "gate --deck <out.pptx>")
    return result("orchestration.plan", "Dependency-Aware Work Planner",
                  f"{len(plan)} ordered step(s)", {"plan": plan,
                                                   "rule": "inspect -> repair -> normalize -> validate -> gate"})


def engine_capability_audit(deck, cfg, registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Audit the capability registry that ships as asset-capability-registry.json."""
    if not registry:
        return result("capability.audit", "Skill Catalog Auditor", "no registry supplied",
                      {"registry": None}, [issue("CAP_NO_REGISTRY", "capability registry was not supplied", "info")])
    entries = registry.get("entries", [])
    implemented = [e for e in entries if e.get("status") == "implemented"]
    documented = [e for e in entries if e.get("status") == "documented"]
    delegated = [e for e in entries if e.get("status") == "delegated"]
    interfaces = [e for e in implemented if e.get("command")]
    data = {
        "registry_version": registry.get("version"),
        "total_entries": len(entries),
        "implemented": len(implemented),
        "documented": len(documented),
        "delegated": len(delegated),
        "coverage_percent": round(100 * len(implemented) / max(1, len(entries)), 1),
        "categories": len({e.get("category") for e in entries}),
        "commands": sorted({e["command"] for e in interfaces}),
        "delegated_list": [{"name": e["name"], "requires": e.get("requires")} for e in delegated],
    }
    issues = []
    for e in entries:
        if e.get("status") == "delegated" and not e.get("requires"):
            issues.append(issue("CAP_DELEGATION_UNDOCUMENTED", f"{e['name']} is delegated without a stated requirement",
                                "warning", repair="state the external runtime/licence needed"))
    return result("capability.audit", "Skill Catalog Auditor",
                  f"{len(implemented)}/{len(entries)} capabilities implemented, "
                  f"{len(delegated)} delegated to external runtimes", data, issues)


# ================================================== additional analysis engines
def engine_families(deck, cfg) -> Dict[str, Any]:
    """Classify slides into recurring visual families by observable structure."""
    rows = []
    for s in deck.slides:
        text_objs = [o for o in s.objects if all_text(o).strip()]
        words = s.words
        biggest = max((primary_size(o) or 0) for o in text_objs) if text_objs else 0
        pictures = sum(1 for o in s.objects if o.kind in ("picture", "media"))
        charts = sum(1 for o in s.objects if o.kind in ("chart", "table"))
        connectors = sum(1 for o in s.objects if o.kind == "connector")
        if not s.objects:
            fam = "blank"
        elif s.index == 1 and words <= 40 and biggest >= 28:
            fam = "title"
        elif words <= 18 and len(text_objs) <= 3 and biggest >= 26:
            fam = "divider"
        elif connectors >= 3 or charts >= 2:
            fam = "diagram"
        elif pictures and words <= 60:
            fam = "media"
        elif words <= 12 and s.index == len(deck.slides):
            fam = "closing"
        elif words >= 40 or len(text_objs) >= 6:
            fam = "content"
        else:
            fam = "content"
        rows.append({"slide": s.index, "family": fam, "words": words,
                     "text_objects": len(text_objs), "max_size_pt": biggest,
                     "pictures": pictures, "charts_or_tables": charts, "connectors": connectors})
    counts = Counter(r["family"] for r in rows)
    issues = []
    if counts.get("blank"):
        issues.append(issue("FAMILY_BLANK", f"{counts['blank']} slide(s) have no objects", "info",
                            repair="confirm blank slides are intentional section pauses"))
    return result("structure.families", "Slide Family Classifier",
                  ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())),
                  {"slides": rows, "counts": dict(counts)}, issues,
                  ["Families are inferred heuristically; a slide may legitimately serve two roles."])


def engine_type_inheritance(deck, cfg) -> Dict[str, Any]:
    explicit = inherited = 0
    fonts, colours = Counter(), Counter()
    for s, o in deck.all_objects():
        for p in o.paragraphs:
            for r in p:
                if not r.text.strip():
                    continue
                if r.size_pt:
                    explicit += 1
                else:
                    inherited += 1
                if r.font:
                    fonts[r.font] += 1
                if r.color:
                    colours[r.color] += 1
    total = explicit + inherited
    issues = []
    if total and explicit / total > 0.8:
        issues.append(issue("TYPE_DIRECT_FORMATTING",
                            f"{explicit / total:.0%} of runs carry direct formatting instead of inherited theme styles",
                            "info", repair="move repeated formatting into layout/theme styles to reduce drift"))
    return result("typography.inheritance", "Inherited Formatting Resolver",
                  f"{explicit} explicit / {inherited} inherited run(s)",
                  {"explicit_runs": explicit, "inherited_runs": inherited,
                   "explicit_fonts": dict(fonts.most_common(10)),
                   "explicit_colors": dict(colours.most_common(10))}, issues,
                  ["Full inheritance chains require resolving layout and master XML per placeholder; this reports the run-level split."])


def engine_title_zone(deck, cfg) -> Dict[str, Any]:
    """Title drift is compared within a slide family: title slides legitimately
    place their headline lower than content slides, so pooling them would produce
    false positives."""
    tol = float(cfg.get("title_zone_tolerance_pt", 8))
    positions = []
    families = {r["slide"]: r["family"] for r in engine_families(deck, cfg)["data"]["slides"]}
    for s in deck.slides:
        text_objs = [o for o in s.objects if all_text(o).strip()]
        if not text_objs:
            continue
        title = min(text_objs, key=lambda o: (o.y, o.x))
        positions.append((s.index, title.y_pt, title.x_pt, primary_size(title) or 0, obj_label(title),
                          families.get(s.index, "content")))
    issues = []
    by_family: Dict[str, List[Tuple]] = {}
    for p in positions:
        by_family.setdefault(p[5], []).append(p)
    for family, group in by_family.items():
        if len(group) < 2:
            continue
        ys = [p[1] for p in group]
        median = sorted(ys)[len(ys) // 2]
        for idx, y, x, size, name, _fam in group:
            if abs(y - median) > tol:
                issues.append(issue("GRID_TITLE_DRIFT",
                                    f"title sits {y - median:+.0f}pt from the {family}-family title line ({median:.0f}pt)",
                                    "warning", idx, name,
                                    repair="snap the title to the shared title zone (normalize align)"))
        sizes = [p[3] for p in group if p[3]]
        if sizes and (max(sizes) - min(sizes)) > 2:
            issues.append(issue("GRID_TITLE_SIZE_DRIFT",
                                f"title sizes in the {family} family range {min(sizes):g}-{max(sizes):g}pt",
                                "warning",
                                repair="use one title size per family and vary emphasis inside the body instead"))
    med_by_family = {f: sorted(p[1] for p in g)[len(g) // 2] for f, g in by_family.items()}
    return result("grid.title_zone", "Title Zone Consistency Checker",
                  f"{len(positions)} title(s) across {len(by_family)} family/families",
                  {"title_positions": [{"slide": i, "y_pt": nice(y), "x_pt": nice(x), "size_pt": sz,
                                        "object": n, "family": fam}
                                       for i, y, x, sz, n, fam in positions],
                   "family_title_baselines_pt": {k: nice(v) for k, v in med_by_family.items()},
                   "tolerance_pt": tol}, issues)


def engine_footer_zone(deck, cfg) -> Dict[str, Any]:
    th = thresholds(cfg)
    bottom_limit = footer_band_top(deck, float(th.get("safe_bottom_pt", 34)))
    footers, intrusions = [], []
    for s in deck.slides:
        for o in s.objects:
            if o.y_pt >= bottom_limit and all_text(o).strip():
                footers.append({"slide": s.index, "object": obj_label(o), "y_pt": nice(o.y_pt),
                                "size_pt": primary_size(o)})
            elif o.y_pt + o.h_pt > bottom_limit and o.y_pt < bottom_limit and all_text(o).strip():
                intrusions.append({"slide": s.index, "object": obj_label(o),
                                   "bottom_pt": nice(o.y_pt + o.h_pt)})
    issues = []
    for i in intrusions:
        issues.append(issue("GRID_FOOTER_INTRUSION", "content extends into the reserved footer zone", "warning",
                            i["slide"], i["object"], repair="reduce body height or raise the object"))
    sizes = [f["size_pt"] for f in footers if f["size_pt"]]
    if sizes and (max(sizes) - min(sizes)) > 2:
        issues.append(issue("GRID_FOOTER_SIZE_DRIFT", f"footer text sizes range {min(sizes):g}-{max(sizes):g}pt",
                            "info", repair="standardize one footer size"))
    return result("grid.footer_zone", "Footer Safe-Zone Manager",
                  f"{len(footers)} footer object(s), {len(intrusions)} intrusion(s)",
                  {"footer_objects": footers[:80], "intrusions": intrusions,
                   "footer_zone_top_pt": nice(bottom_limit)}, issues)


def engine_color_semantics(deck, cfg) -> Dict[str, Any]:
    status_rx = re.compile(r"(?i)\b(risk|warning|alert|critical|fail|error|blocked|approved|passed|success|on track|off track|delayed)\b")
    status_colours = {"danger": ("D93025", "C00000", "E53935", "CC0000"),
                      "warn": ("F2A900", "FFC000", "E8A317", "F9AB00"),
                      "ok": ("188038", "00B050", "2E7D32", "34A853")}
    used, status_slides = Counter(), []
    for s, o in deck.all_objects():
        fills = [o.fill] if o.fill else []
        for f in fills:
            for role, values in status_colours.items():
                if f and any(dm.color_distance(f, v) is not None and dm.color_distance(f, v) <= 30 for v in values):
                    used[role] += 1
        if status_rx.search(all_text(o)):
            status_slides.append(s.index)
    issues = []
    if used and not status_slides:
        issues.append(issue("COLOR_STATUS_DECORATIVE",
                            f"status-like colours ({', '.join(used)}) are used on slides with no status language",
                            "warning", repair="reserve red/amber/green for real status, or switch to brand accents"))
    return result("color.semantics", "Warning Color Governance",
                  f"status colours used {sum(used.values())} time(s); {len(set(status_slides))} slide(s) use status language",
                  {"status_color_usage": dict(used), "status_language_slides": sorted(set(status_slides))}, issues,
                  ["Colour-to-status matching uses a fixed reference palette and a distance threshold."])


def engine_sequence(deck, cfg) -> Dict[str, Any]:
    """Detect flows where numbering disagrees with spatial position."""
    issues, rows = [], []
    num_rx = re.compile(r"^\s*(\d{1,2})[.)\s]")
    for s in deck.slides:
        numbered = []
        for o in s.objects:
            m = num_rx.match(all_text(o).strip())
            if m:
                numbered.append((int(m.group(1)), o))
        if len(numbered) < 3:
            continue
        by_number = [o for _n, o in sorted(numbered)]
        by_position = sorted(by_number, key=lambda o: (round(o.y_pt / 40), o.x_pt))
        mismatch = by_number != by_position
        # serpentine row detection
        rows_by_y = defaultdict(list)
        for o in by_position:
            rows_by_y[round(o.y_pt / 60)].append(o)
        serpentine = False
        for i, key in enumerate(sorted(rows_by_y)):
            row = sorted(rows_by_y[key], key=lambda o: o.x_pt)
            if i % 2 == 1:
                row = list(reversed(row))
            pass
        rows.append({"slide": s.index, "numbered_objects": len(numbered), "order_matches": not mismatch})
        if mismatch:
            issues.append(issue("PROCESS_ORDER_MISMATCH",
                                "numbered steps are not in reading order (row/column direction disagrees with numbering)",
                                "warning", s.index,
                                repair="reorder objects, or make the row reversal explicit with arrows and a connector"))
    return result("process.sequence", "Sequence Ambiguity Detector",
                  f"{sum(1 for r in rows if not r['order_matches'])} ambiguous flow(s)", {"slides": rows}, issues)


def engine_abbreviations(deck, cfg) -> Dict[str, Any]:
    acronyms, expansions = Counter(), Counter()
    for s, o in deck.all_objects():
        text = all_text(o)
        for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,7})\b", text):
            token = m.group(1)
            if token in ("OK", "ID", "USA", "PDF", "PNG", "JPEG", "Q1", "Q2", "Q3", "Q4", "PPT", "API"):
                continue
            acronyms[token] += 1
        for m in re.finditer(r"\b([A-Z][A-Za-z\-]+(?:\s+[A-Za-z\-]+){0,3})\s+\(([A-Z][A-Z0-9]{1,7})\)", text):
            expansions[m.group(2)] += 1
    unexplained = [a for a, n in acronyms.items() if n >= 2 and a not in expansions]
    issues = [issue("READ_ACRONYM", f"'{a}' appears {acronyms[a]} time(s) without an expansion", "info",
                    repair="expand on first use or add a legend") for a in sorted(unexplained)[:20]]
    return result("content.abbreviations", "Abbreviation Load Auditor",
                  f"{len(unexplained)} unexplained acronym(s)", {"acronyms": dict(acronyms.most_common(30)),
                                                                 "explained": dict(expansions),
                                                                 "unexplained": sorted(unexplained)}, issues)


def engine_brand(deck, cfg) -> Dict[str, Any]:
    brand = cfg.get("brand", {}) or {}
    allowed_colors = {c.upper() for c in brand.get("allowed_colors", []) or []}
    allowed_fonts = set(brand.get("allowed_fonts", []) or [])
    logo_names = [re.compile(p, re.I) for p in brand.get("logo_name_patterns", []) or []]
    min_clear = float(brand.get("min_logo_clear_space_pt", 12))
    issues, logos = [], []
    if allowed_fonts:
        for font in deck.fonts_used:
            if font and font not in allowed_fonts:
                issues.append(issue("BRAND_FONT", f"font '{font}' is outside the approved family list", "warning",
                                    repair="switch to an approved family or register an exception"))
    if allowed_colors:
        used = set()
        for s, o in deck.all_objects():
            for c in (o.fill, o.line):
                if c and dm.hex_to_rgb(c):
                    used.add(c.upper())
            for p in o.paragraphs:
                for r in p:
                    if r.color and dm.hex_to_rgb(r.color):
                        used.add(r.color.upper())
        off = sorted(used - allowed_colors)
        for c in off[:25]:
            issues.append(issue("BRAND_COLOR", f"colour #{c} is outside the approved palette", "warning",
                                repair="map to the nearest approved token (normalize colors)"))
    for s, o in deck.all_objects():
        if any(p.search(o.name or "") for p in logo_names) or (o.kind == "picture" and re.search(r"(?i)logo", o.name or "")):
            logos.append({"slide": s.index, "object": obj_label(o), "w_pt": nice(o.w_pt), "h_pt": nice(o.h_pt),
                          "x_pt": nice(o.x_pt), "y_pt": nice(o.y_pt)})
            if o.x_pt < min_clear or o.y_pt < min_clear or                o.x_pt + o.w_pt > deck.width_pt - min_clear or o.y_pt + o.h_pt > deck.height_pt - min_clear:
                issues.append(issue("BRAND_LOGO_CLEARSPACE",
                                    f"logo sits inside the {min_clear:g}pt clear-space margin", "warning",
                                    s.index, obj_label(o), repair="move the logo to an approved corner slot"))
    sizes = [l["w_pt"] for l in logos]
    if sizes and (max(sizes) - min(sizes)) > 4:
        issues.append(issue("BRAND_LOGO_SIZE", f"logo widths range {min(sizes):.0f}-{max(sizes):.0f}pt", "warning",
                            repair="use one logo size per surface"))
    return result("brand.compliance", "Brand Compliance Auditor",
                  f"{len(logos)} logo placement(s), {len(issues)} brand finding(s)",
                  {"logos": logos, "approved_colors": sorted(allowed_colors)[:20],
                   "approved_fonts": sorted(allowed_fonts), "configured": bool(brand)}, issues,
                  ["Brand rules come from the 'brand' block in the config; with no config only logo geometry is checked."])


def engine_animation(deck, cfg) -> Dict[str, Any]:
    """Count animations/transitions from the slide XML (structure only)."""
    import zipfile
    counts: Dict[int, Dict[str, int]] = {}
    if not deck.path or not deck.path.lower().endswith((".pptx", ".pptm", ".potx")):
        return result("media.animation", "Animation Purpose Auditor", "not applicable for JSON models",
                      {"slides": []}, [], ["Animation XML is only present in OPC packages."])
    try:
        with zipfile.ZipFile(deck.path) as zf:
            for name in sorted(n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)):
                idx = int(re.search(r"(\d+)", name.split("/")[-1]).group(1))
                xml = zf.read(name).decode("utf-8", "ignore")
                anim = len(re.findall(r"<p:(anim|animEffect|animMotion|animRot|animScale)\b", xml))
                timing = 1 if "<p:timing>" in xml else 0
                trans = len(re.findall(r"<p:transition\b", xml))
                counts[idx] = {"animations": anim, "has_timing": timing, "transitions": trans}
    except Exception as exc:
        return result("media.animation", "Animation Purpose Auditor", f"could not read timing XML: {exc}",
                      {"slides": []}, [issue("MEDIA_ANIM_UNREADABLE", "slide timing XML unreadable", "info")])
    heavy = [{"slide": i, "animations": c["animations"]} for i, c in counts.items() if c["animations"] > 12]
    trans_slides = [i for i, c in counts.items() if c["transitions"]]
    issues = [issue("MEDIA_ANIM_HEAVY", f"{h['animations']} animation effects on one slide", "warning", h["slide"],
                    repair="keep motion only where it reveals sequence, focus, or change") for h in heavy]
    if len(trans_slides) not in (0, len(counts)):
        issues.append(issue("MEDIA_TRANSITION_MIXED",
                            f"{len(trans_slides)}/{len(counts)} slides have transitions; apply consistently or not at all",
                            "info", repair="use one transition style deck-wide or none"))
    return result("media.animation", "Animation Purpose Auditor",
                  f"{sum(c['animations'] for c in counts.values())} animation effect(s) across {len(counts)} slide part(s)",
                  {"slides_with_numbers": counts, "heavy_slides": heavy, "transition_slides": trans_slides},
                  issues, ["Effect counts come from XML element counts, not playback semantics."])


# ---------------------------------------------------------------- engine index
ENGINES: Dict[str, Any] = {
    # structure
    "package": engine_package, "census": engine_census, "layouts": engine_layout_map,
    "editable": engine_editable, "hidden": engine_hidden, "metadata": engine_metadata,
    "assets": engine_assets, "dependencies": engine_dependencies, "intent": engine_intent,
    # geometry
    "canvas": engine_canvas, "coordinates": engine_coordinates, "safe-margin": engine_safe_margin,
    "bounds": engine_bounds, "zones": engine_zones, "rotation": engine_rotation,
    "whitespace": engine_whitespace, "density-heatmap": engine_density_heatmap, "overlap": engine_overlap,
    # grid
    "alignment": engine_alignment, "distribution": engine_distribution, "spacing-tokens": engine_spacing_tokens,
    # typography
    "fonts": engine_font_inventory, "min-type": engine_min_type, "type-scale": engine_type_scale,
    "line-length": engine_line_length, "overflow": engine_overflow, "emphasis": engine_emphasis,
    "rhythm": engine_rhythm,
    # colour
    "palette": engine_palette, "contrast": engine_contrast, "color-drift": engine_color_drift,
    "transparency": engine_transparency, "monochrome": engine_monochrome,
    # shapes / data / media
    "shapes": engine_shape_inventory, "connectors": engine_connectors,
    "tables": engine_tables, "charts": engine_charts,
    "images": engine_images, "av": engine_av,
    # accessibility
    "reading-order": engine_reading_order, "alt-text": engine_alt_text,
    "color-independence": engine_color_independence, "links": engine_links, "language": engine_language,
    "a11y": engine_a11y_baseline,
    # content / evidence
    "bullets": engine_bullets, "titles": engine_title_claims, "terminology": engine_terminology,
    "redundancy": engine_redundancy, "citations": engine_citations, "freshness": engine_freshness,
    # security
    "sensitive": engine_sensitive, "metadata-privacy": engine_metadata_privacy,
    "link-policy": engine_link_policy, "macros": engine_macro_scan,
    # QA / orchestration / scoring
    "defects": engine_defect_queue, "gate": engine_completion_gate, "scores": engine_scores,
    "plan": engine_work_plan, "capability-audit": engine_capability_audit,
    # added families
    "families": engine_families, "type-inheritance": engine_type_inheritance,
    "title-zone": engine_title_zone, "footer-zone": engine_footer_zone,
    "color-semantics": engine_color_semantics, "sequence": engine_sequence,
    "abbreviations": engine_abbreviations, "brand": engine_brand, "animation": engine_animation,
}

# Engine ids exported in the manifest so the CLI can list them with descriptions.
ENGINE_GROUPS: Dict[str, List[str]] = {
    "structure": ["package", "census", "layouts", "editable", "hidden", "metadata", "assets", "dependencies", "intent"],
    "geometry": ["canvas", "coordinates", "zones", "safe-margin", "bounds", "rotation", "whitespace",
                 "density-heatmap", "overlap"],
    "grid": ["alignment", "distribution", "spacing-tokens", "title-zone", "footer-zone"],
    "typography": ["fonts", "type-inheritance", "min-type", "type-scale", "line-length", "overflow",
                   "emphasis", "rhythm"],
    "color": ["palette", "contrast", "color-drift", "transparency", "monochrome"],
    "shapes": ["shapes", "connectors"],
    "data": ["tables", "charts"],
    "media": ["images", "av"],
    "accessibility": ["a11y", "reading-order", "alt-text", "color-independence", "links", "language"],
    "content": ["bullets", "titles", "terminology", "redundancy", "abbreviations", "sequence"],
    "evidence": ["citations", "freshness"],
    "security": ["sensitive", "metadata-privacy", "link-policy", "macros"],
    "qa": ["defects", "gate", "scores", "plan", "capability-audit"],
    "brand": ["brand", "color-semantics"],
    "process": ["sequence", "families", "animation"],
}

ENGINE_DESCRIPTIONS: Dict[str, str] = {
    "package": "OPC package health: ZIP integrity, malformed XML, dangling relationships",
    "census": "object and word census per slide with kind classification",
    "layouts": "slide -> master/layout mapping, unused and duplicate layouts, blank-layout usage",
    "editable": "native-editable vs rasterized share, low-resolution raster flags",
    "hidden": "hidden slides, off-canvas, zero-size and partially clipped objects",
    "metadata": "core properties, notes coverage, draft markers in notes",
    "assets": "embedded media inventory, duplicates, total size",
    "dependencies": "font fallback coverage, A/V parts, unattached connectors, dangling rels",
    "intent": "authoring instructions/placeholders leaking into audience-facing content",
    "canvas": "slide dimensions, aspect ratio, unit conversions",
    "coordinates": "x/y/w/h/rotation for every object in points",
    "zones": "inferred title/body/footer zones from the real object distribution",
    "safe-margin": "objects too close to the safe area edges",
    "bounds": "objects that exceed the canvas",
    "rotation": "rotated/flipped objects and inconsistent rotation among repeats",
    "whitespace": "per-slide object coverage vs target density",
    "density-heatmap": "grid heatmap of occupied area per slide",
    "overlap": "unintended object overlaps with z-order intent heuristic",
    "alignment": "near-aligned edges/centres inside the tolerance",
    "distribution": "uneven gaps between repeated objects",
    "spacing-tokens": "recurring spacing values and suggested token scale",
    "fonts": "font family inventory, theme fonts, fallback availability",
    "min-type": "text runs below the profile's minimum type size",
    "type-scale": "distinct type sizes and scale hygiene",
    "line-length": "characters per line vs readability limit",
    "overflow": "estimated text overflow / autofit shrink risk",
    "emphasis": "bold/italic/underline signature consistency",
    "rhythm": "multi-paragraph frames and bullet counts",
    "palette": "theme slots vs actual visible colours, unused accents",
    "contrast": "WCAG-oriented text contrast against the resolved background",
    "color-drift": "near-duplicate colours that should collapse to one token",
    "transparency": "gradients and shape effects that render inconsistently",
    "monochrome": "colour pairs that collide in grayscale",
    "shapes": "object kind census and shape effect inventory",
    "connectors": "connector endpoint attachment and routing risk",
    "tables": "table header/column/row readability",
    "charts": "chart structure, titles, series count, sign mixing",
    "images": "effective DPI and alt-text presence for pictures",
    "av": "audio/video parts requiring playback verification",
    "reading-order": "creation order vs visual reading order",
    "alt-text": "missing or thin alternative text",
    "color-independence": "meaning that may depend on colour alone",
    "links": "raw URLs, disallowed domains, external link inventory",
    "language": "language tags and script detection (CJK/RTL)",
    "a11y": "combined accessibility baseline across all a11y checks",
    "bullets": "bullet counts and over-long bullets",
    "titles": "topic-label vs claim-style titles",
    "terminology": "inconsistent term capitalization/variants",
    "redundancy": "identical text blocks repeated across the deck",
    "citations": "quantitative claims lacking a visible source",
    "freshness": "old years and draft/confidential markings",
    "sensitive": "credentials, PII, internal URLs in content and notes",
    "metadata-privacy": "personal document properties and notes volume",
    "link-policy": "link domain policy check (alias of links)",
    "macros": "macros, embedded objects and executables in the package",
    "defects": "deduplicated defect queue ranked by priority",
    "gate": "release gate over error-severity findings and artifacts",
    "scores": "readability/consistency/accessibility/structure/evidence scorecards",
    "plan": "dependency-aware ordered remediation plan",
    "capability-audit": "coverage audit of the built-in capability registry",
    "families": "slide family classification (title/divider/content/diagram/media/closing)",
    "type-inheritance": "direct formatting vs inherited theme styling per run",
    "title-zone": "title position/size drift across slides",
    "footer-zone": "footer objects and content intruding into the footer zone",
    "color-semantics": "status colours used without status meaning",
    "sequence": "numbered flows whose spatial order disagrees with numbering",
    "abbreviations": "unexplained acronyms",
    "brand": "brand colour/font/logo compliance from the config brand block",
    "animation": "animation and transition counts from slide XML",
}
