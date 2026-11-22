#!/usr/bin/env python3
"""Deck editing engine for the powerpoint-agent skill tool.

Houses the operations that change an existing deck rather than build one:

* ``format``   – normalise typography, geometry, placeholders, footer, numbering
* ``replace``  – find/replace across runs with a paragraph fallback
* ``search``   – read-only text search with slide/shape locations
* ``notes``    – coverage check, structured note scaffolding, Markdown export
* ``sections`` – read or rewrite the PowerPoint section list
* ``compare``  – structural diff between two decks

Every write operation targets a new output path; the engine never edits the
input file in place.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

CORE = None


def bind(core_module) -> None:
    global CORE
    CORE = core_module


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

@dataclass
class FormatStats:
    fonts_changed: int = 0
    sizes_raised: int = 0
    geometry_normalised: int = 0
    placeholders_removed: int = 0
    footers_added: int = 0
    numbers_added: int = 0
    titles_styled: int = 0
    margins_normalised: int = 0
    slides_touched: int = 0
    backgrounds_repainted: int = 0
    backgrounds_kept: int = 0
    colors_adjusted: int = 0


def _is_title(shape) -> bool:
    return CORE.is_title_placeholder(shape) if CORE else False


def _title_geometry_broken(title, slide_w: float, slide_h: float, margin_x: float, title_y: float) -> bool:
    """Only repair a title box that is objectively broken.

    A designed title (title-slide placement, a narrower measure beside an accent
    panel) is left alone: moving a valid box is what collides with kicker text.
    """

    try:
        left = CORE.emu_to_inches(title.left) or 0.0
        top = CORE.emu_to_inches(title.top) or 0.0
        width = CORE.emu_to_inches(title.width) or 0.0
        height = CORE.emu_to_inches(title.height) or 0.0
    except Exception:
        return False
    if width <= 0.2 or height <= 0.05:
        return True
    if left < -0.01 or top < -0.01:
        return True
    if left + width > slide_w + 0.05 or top + height > slide_h + 0.05:
        return True
    if abs(left - margin_x) > 0.75:
        return True
    if top > title_y + 2.0:
        return True
    return False


def _title_is_centered(title) -> bool:
    """True when every non-empty title paragraph is centre-aligned."""

    try:
        paragraphs = [p for p in title.text_frame.paragraphs if p.text.strip()]
        if not paragraphs:
            return False
        for paragraph in paragraphs:
            alignment = paragraph.alignment
            if alignment is None:
                return False
            if "CENTER" not in str(alignment):
                return False
    except Exception:
        return False
    return True


def _bg_is_dark(slide, prs, scheme) -> bool:
    return CORE.is_dark(CORE.slide_background_hex(slide, prs, scheme))


def _has_named_shape(slide, name: str) -> bool:
    for shape in CORE.iter_shapes(slide.shapes):
        if getattr(shape, "name", "") == name:
            return True
    return False


def format_deck(
    path: str,
    out_path: str,
    *,
    config: Dict[str, Any],
    theme_name: Optional[str] = None,
    force_fonts: Optional[bool] = None,
    min_font_pt: Optional[float] = None,
    add_footer: bool = True,
    add_slide_numbers: bool = True,
    footer_text: str = "",
    remove_empty_placeholders: Optional[bool] = None,
    repaint_backgrounds: Optional[Any] = None,
    overwrite: bool = False,
    log=None,
    deadline=None,
    core=None,
) -> Dict[str, Any]:
    """Apply theme-aware formatting and normalisation to a deck."""

    core = core or CORE
    core.require_pptx()
    log = log or core.RunLog()
    source = core.resolve_input(path)
    destination = core.resolve_output(out_path, overwrite=overwrite, exts=(".pptx",), label="output presentation")
    if os.path.abspath(source) == os.path.abspath(destination):
        raise core.SafetyError(
            "Format must write to a new file.",
            hint="Pick a different --out path; the source presentation stays untouched.",
        )

    theme = core.load_theme(theme_name or core.cfg(config, "defaults.theme", "corporate-blue"))
    settings = core.cfg(config, "format", {}) or {}
    force = bool(settings.get("force_fonts", False) if force_fonts is None else force_fonts)
    minimum = float(min_font_pt if min_font_pt is not None else settings.get("min_font_pt", 14))
    remove_empty = bool(
        settings.get("remove_empty_placeholders", True) if remove_empty_placeholders is None else remove_empty_placeholders
    )
    # "auto" repaints only plain solid slide-level fills, so decks built from
    # solid canvases follow the theme while picture/gradient backgrounds are
    # preserved. true forces a repaint, false never repaints.
    repaint = settings.get("repaint_backgrounds", "auto") if repaint_backgrounds is None else repaint_backgrounds
    if isinstance(repaint, str):
        repaint = repaint.strip().lower()
    prs = core.open_presentation(source)
    scheme = core.theme_color_scheme(prs)
    width_in = core.emu_to_inches(prs.slide_width) or 13.333
    height_in = core.emu_to_inches(prs.slide_height) or 7.5
    margin_x = theme.grid_in("margin_x_in", 0.62)
    content_w = width_in - 2 * margin_x
    title_y = theme.grid_in("title_top_in", 0.45)
    content_top = theme.grid_in("content_top_in", 1.5)
    body_font = theme.font("body")
    heading_font = theme.font("heading")
    stats = FormatStats()
    per_slide: List[Dict[str, Any]] = []

    for index, slide in enumerate(prs.slides, start=1):
        if deadline:
            deadline.check("formatting slide %d" % index)
        slide_changes = {
            "slide": index,
            "fonts": 0,
            "sizes": 0,
            "geometry": 0,
            "placeholders_removed": 0,
            "footer": False,
            "slide_number": False,
            "title": False,
            "margins": 0,
            "background": False,
        }
        solid_background = core.slide_solid_background(slide)
        wants_repaint = repaint is True or (repaint == "auto" and solid_background is not None)
        if wants_repaint and solid_background is not None:
            target_canvas = theme.color("background")
            if solid_background.upper() != target_canvas.upper():
                core._paint_background(slide, target_canvas)
                stats.backgrounds_repainted += 1
                slide_changes["background"] = True
        elif solid_background is not None:
            stats.backgrounds_kept += 1
        dark = core.is_dark(core.slide_background_hex(slide, prs, scheme))
        title = None
        try:
            title = slide.shapes.title
        except Exception:
            title = None
        if title is not None:
            centered = _title_is_centered(title)
            if not centered and _title_geometry_broken(
                title, width_in, height_in, margin_x, title_y
            ):
                try:
                    existing_w = CORE.emu_to_inches(title.width) or 0.0
                    target_w = content_w * 0.92
                    if 1.5 < existing_w < target_w:
                        # Keep a designed (narrower) title measure instead of
                        # stretching it across the slide.
                        target_w = existing_w
                    target_w = min(target_w, CORE.width_before_panel(slide, title, scheme, target_w))
                    if target_w < 1.5:
                        target_w = content_w * 0.92
                    title.left, title.top, title.width, title.height = (
                        CORE.Inches(margin_x),
                        CORE.Inches(title_y),
                        CORE.Inches(target_w),
                        CORE.Inches(0.95),
                    )
                    stats.geometry_normalised += 1
                    slide_changes["geometry"] += 1
                except Exception:
                    pass
            # Ink is chosen by measured contrast against this slide's own canvas:
            # "text_inverse" is not guaranteed to be the light colour in a preset.
            target_color = CORE.readable_on(theme, CORE.slide_background_hex(slide, prs, scheme))
            for paragraph in title.text_frame.paragraphs:
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    if run.font.name != heading_font:
                        run.font.name = heading_font
                        stats.fonts_changed += 1
                        slide_changes["fonts"] += 1
                    current = run.font.size.pt if run.font.size is not None else None
                    if current is None or current < theme.pt("title_pt", 32) - 0.5:
                        run.font.size = CORE.Pt(theme.pt("title_pt", 32))
                        stats.sizes_raised += 1
                        slide_changes["sizes"] += 1
                    if run.font.bold is not True:
                        run.font.bold = True
                    try:
                        run.font.color.rgb = CORE.RGBColor.from_string(CORE.parse_hex(target_color))
                    except Exception:
                        pass
            slide_changes["title"] = True
            stats.titles_styled += 1

        for shape in list(core.iter_shapes(slide.shapes)):
            if not getattr(shape, "has_text_frame", False):
                continue
            if _is_title(shape):
                continue
            if shape.text_frame.text.strip() and shape.width and shape.height:
                width_shape = core.emu_to_inches(shape.width) or 0
                left_shape = core.emu_to_inches(shape.left) or 0
                if width_shape >= content_w * 0.7 and abs(left_shape - margin_x) <= 1.0:
                    if abs(left_shape - margin_x) > 0.02 or abs(width_shape - content_w) > 0.02:
                        shape.left = CORE.Inches(margin_x)
                        shape.width = CORE.Inches(content_w)
                        stats.geometry_normalised += 1
                        slide_changes["geometry"] += 1
                try:
                    shape.text_frame.margin_left = CORE.Inches(0.05)
                    shape.text_frame.margin_right = CORE.Inches(0.05)
                    slide_changes["margins"] += 1
                    stats.margins_normalised += 1
                except Exception:
                    pass
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    if force or not run.font.name:
                        if run.font.name != body_font:
                            run.font.name = body_font
                            stats.fonts_changed += 1
                            slide_changes["fonts"] += 1
                    current = run.font.size.pt if run.font.size is not None else None
                    if current is not None and current < minimum:
                        run.font.size = CORE.Pt(minimum)
                        stats.sizes_raised += 1
                        slide_changes["sizes"] += 1

        if slide_changes["background"]:
            # A repainted canvas invalidates every explicit ink colour that was
            # chosen for the previous background, so re-map those runs. Ink is
            # measured against whatever is actually behind the text: an opaque
            # card fill when the shape has one, otherwise the slide canvas.
            canvas = theme.color("background")
            for shape in list(core.iter_shapes(slide.shapes)):
                if not getattr(shape, "has_text_frame", False):
                    continue
                try:
                    behind = core.effective_backdrop(slide, shape, scheme, canvas)
                except Exception:
                    behind = canvas
                ink = core.readable_on(theme, behind)
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        if not run.text.strip():
                            continue
                        try:
                            rgb = run.font.color.rgb
                            current = "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
                        except Exception:
                            continue
                        if core.contrast_ratio(current, behind) >= 4.5:
                            continue
                        try:
                            run.font.color.rgb = CORE.RGBColor.from_string(CORE.parse_hex(ink))
                        except Exception:
                            continue
                        stats.colors_adjusted += 1
                        slide_changes["colors"] = slide_changes.get("colors", 0) + 1

        if remove_empty:
            for shape in list(slide.shapes):
                try:
                    if shape.is_placeholder and shape.has_text_frame and not shape.text_frame.text.strip() and not _is_title(shape):
                        core._remove_shape(shape)
                        stats.placeholders_removed += 1
                        slide_changes["placeholders_removed"] += 1
                except Exception:
                    continue

        if add_footer and footer_text and not _has_named_shape(slide, "pptx-agent-footer"):
            try:
                box = core._add_textbox(slide, margin_x, height_in - 0.42, content_w * 0.7, 0.28, name="pptx-agent-footer")
                core._write_paragraph(
                    box.text_frame.paragraphs[0], footer_text, font=body_font, size_pt=theme.pt("small_pt", 11),
                    color=theme.color("muted"), space_after_pt=0, line_spacing=1.0,
                )
                stats.footers_added += 1
                slide_changes["footer"] = True
            except Exception:
                pass
        if add_slide_numbers and not _has_named_shape(slide, "pptx-agent-slidenumber"):
            try:
                box = core._add_textbox(slide, width_in - margin_x - 0.7, height_in - 0.42, 0.7, 0.28,
                                        name="pptx-agent-slidenumber")
                core._write_paragraph(
                    box.text_frame.paragraphs[0], str(index), font=body_font, size_pt=theme.pt("small_pt", 11),
                    color=theme.color("muted"), space_after_pt=0, line_spacing=1.0, align=CORE.PP_ALIGN.RIGHT,
                )
                stats.numbers_added += 1
                slide_changes["slide_number"] = True
            except Exception:
                pass
        if any(
            slide_changes[key]
            for key in ("fonts", "sizes", "geometry", "placeholders_removed", "footer", "slide_number",
                        "title", "background", "colors")
        ):
            stats.slides_touched += 1
        per_slide.append(slide_changes)

    core.save_presentation(prs, destination)
    verification = {"reopened": False, "slide_count": 0}
    try:
        check = core.open_presentation(destination)
        verification = {"reopened": True, "slide_count": len(check.slides)}
    except Exception as exc:
        verification = {"reopened": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    if log:
        log.info("format complete", slides=stats.slides_touched, output=os.path.basename(destination))
    return {
        "file": os.path.basename(source),
        "output": os.path.basename(destination),
        "output_path": destination,
        "theme": theme.name,
        "settings": {"force_fonts": force, "min_font_pt": minimum, "add_footer": bool(add_footer),
                     "add_slide_numbers": bool(add_slide_numbers), "remove_empty_placeholders": remove_empty,
                     "repaint_backgrounds": repaint},
        "stats": {
            "slides_touched": stats.slides_touched,
            "fonts_changed": stats.fonts_changed,
            "sizes_raised": stats.sizes_raised,
            "geometry_normalised": stats.geometry_normalised,
            "placeholders_removed": stats.placeholders_removed,
            "footers_added": stats.footers_added,
            "slide_numbers_added": stats.numbers_added,
            "titles_styled": stats.titles_styled,
            "backgrounds_repainted": stats.backgrounds_repainted,
            "backgrounds_kept": stats.backgrounds_kept,
            "colors_adjusted": stats.colors_adjusted,
        },
        "slides": per_slide,
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# search / replace
# ---------------------------------------------------------------------------

def search_text(
    prs,
    pattern: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    max_matches: int = 2000,
    core=None,
) -> Dict[str, Any]:
    core = core or CORE
    if not pattern:
        raise core.ValidationError("Search pattern is empty.")
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise core.ValidationError("Invalid regular expression: %s" % exc)
        matcher = lambda text: compiled.search(text) is not None
    else:
        needle = pattern if case_sensitive else pattern.lower()
        matcher = lambda text: needle in (text if case_sensitive else text.lower())

    matches: List[Dict[str, Any]] = []
    slide_hits: Dict[int, int] = {}
    truncated = False
    for index, slide in enumerate(prs.slides, start=1):
        for shape in core.iter_shapes(slide.shapes):
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph_index, paragraph in enumerate(shape.text_frame.paragraphs):
                text = paragraph.text
                if text and matcher(text):
                    if len(matches) >= max_matches:
                        truncated = True
                        break
                    matches.append(
                        {
                            "slide": index,
                            "shape": getattr(shape, "name", ""),
                            "paragraph": paragraph_index,
                            "text": core.truncate(text, 240),
                        }
                    )
                    slide_hits[index] = slide_hits.get(index, 0) + 1
            if truncated:
                break
        if truncated:
            break
    return {
        "pattern": pattern,
        "regex": bool(regex),
        "case_sensitive": bool(case_sensitive),
        "match_count": len(matches),
        "slides_with_matches": sorted(slide_hits.items()),
        "truncated": truncated,
        "matches": matches,
    }


def replace_text(
    prs,
    pattern: str,
    replacement: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    paragraph_fallback: bool = True,
    max_replacements: int = 5000,
    core=None,
) -> Dict[str, Any]:
    core = core or CORE
    if not pattern:
        raise core.ValidationError("Replace pattern is empty.")
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = None
    if regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise core.ValidationError("Invalid regular expression: %s" % exc)

    run_level = 0
    paragraph_level = 0
    slides_touched: set = set()
    formatting_warnings: List[Dict[str, Any]] = []

    def substitute(text: str) -> Optional[str]:
        if compiled is not None:
            return compiled.sub(replacement, text)
        if case_sensitive:
            return text.replace(pattern, replacement) if pattern in text else None
        result = re.sub(re.escape(pattern), replacement, text, flags=re.IGNORECASE)
        return result if result != text else None

    for index, slide in enumerate(prs.slides, start=1):
        for shape in core.iter_shapes(slide.shapes):
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                paragraph_changed = False
                for run in paragraph.runs:
                    new_text = substitute(run.text)
                    if new_text is not None and new_text != run.text and run_level < max_replacements:
                        run.text = new_text
                        run_level += 1
                        paragraph_changed = True
                        slides_touched.add(index)
                if paragraph_fallback and not paragraph_changed and paragraph.runs:
                    current = "".join(run.text for run in paragraph.runs)
                    new_text = substitute(current)
                    if new_text is not None and new_text != current and paragraph_level < max_replacements:
                        first = paragraph.runs[0]
                        first.text = new_text
                        for extra in paragraph.runs[1:]:
                            extra.text = ""
                        paragraph_level += 1
                        slides_touched.add(index)
                        if len(paragraph.runs) > 1:
                            formatting_warnings.append(
                                {
                                    "slide": index,
                                    "shape": getattr(shape, "name", ""),
                                    "note": "phrase spanned multiple runs; formatting collapsed into the first run",
                                }
                            )
    return {
        "run_level_replacements": run_level,
        "paragraph_level_replacements": paragraph_level,
        "total_replacements": run_level + paragraph_level,
        "slides_touched": sorted(slides_touched),
        "formatting_warnings": formatting_warnings[:50],
    }


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

def notes_status(prs, *, words_per_minute: float = 130.0, core=None) -> Dict[str, Any]:
    core = core or CORE
    slides: List[Dict[str, Any]] = []
    covered = 0
    total_words = 0
    for index, slide in enumerate(prs.slides, start=1):
        text = ""
        try:
            if slide.has_notes_slide:
                text = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            text = ""
        words = len(text.split())
        total_words += words
        if text:
            covered += 1
        slides.append(
            {
                "slide": index,
                "title": core.slide_title_text(slide),
                "has_notes": bool(text),
                "words": words,
                "speaking_seconds": int(round(words / max(1.0, words_per_minute) * 60.0)),
            }
        )
    return {
        "slide_count": len(slides),
        "slides_with_notes": covered,
        "coverage_percent": round(100.0 * covered / max(1, len(slides)), 1),
        "total_notes_words": total_words,
        "estimated_speaking_minutes": round(total_words / max(1.0, words_per_minute), 1),
        "slides": slides,
    }


def scaffold_notes(prs, *, max_bullets: int = 4, core=None) -> Dict[str, Any]:
    """Write structured note scaffolds for slides that have no notes."""

    core = core or CORE
    added = 0
    scaffolded: List[int] = []
    for index, slide in enumerate(prs.slides, start=1):
        existing = ""
        try:
            if slide.has_notes_slide:
                existing = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            existing = ""
        if existing:
            continue
        title = core.slide_title_text(slide) or "Untitled slide"
        bullets: List[str] = []
        for shape in core.iter_shapes(slide.shapes):
            if not getattr(shape, "has_text_frame", False) or _is_title(shape):
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if text and len(bullets) < max_bullets:
                    bullets.append(text)
        lines = ["TITLE: %s" % title, "", "TALKING POINTS:"]
        if bullets:
            lines.extend("- %s" % bullet for bullet in bullets)
        else:
            lines.append("- (this slide has no body text; describe the visual or data shown here)")
        lines.extend(
            [
                "",
                "TRANSITION: connect this slide to the next question the audience will ask.",
                "",
                "TIMING: target 45-90 seconds.",
            ]
        )
        try:
            slide.notes_slide.notes_text_frame.text = "\n".join(lines)
            added += 1
            scaffolded.append(index)
        except Exception:
            continue
    return {"notes_added": added, "slides": scaffolded}


def export_notes_markdown(prs, *, title: str = "Speaker notes", core=None) -> str:
    core = core or CORE
    lines = ["# %s" % title, ""]
    any_notes = False
    for index, slide in enumerate(prs.slides, start=1):
        text = ""
        try:
            if slide.has_notes_slide:
                text = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            text = ""
        heading = core.slide_title_text(slide) or "(untitled slide %d)" % index
        lines.append("## Slide %d — %s" % (index, heading))
        lines.append("")
        if text:
            any_notes = True
            for paragraph in text.split("\n"):
                lines.append(paragraph)
        else:
            lines.append("_No speaker notes._")
        lines.append("")
    if not any_notes:
        lines.append("> This deck has no speaker notes on any slide.")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def sections_list(prs, core=None) -> Dict[str, Any]:
    core = core or CORE
    sections = core.read_sections(prs)
    return {
        "section_count": len(sections),
        "sections": sections,
        "slide_count": len(prs.slides),
        "slides_in_sections": sum(len(section["slide_indexes"]) for section in sections),
    }


def sections_set(prs, mapping: Sequence[Dict[str, Any]], core=None) -> Dict[str, Any]:
    """Write sections from ``[{name, slide_indexes}]``; positions are 1-based."""

    core = core or CORE
    slide_count = len(prs.slides)
    assigned: Dict[int, str] = {}
    clean: List[Dict[str, Any]] = []
    problems: List[str] = []
    for item in mapping:
        name = str(item.get("name") or "").strip()
        indexes = item.get("slide_indexes") or []
        if not name:
            problems.append("section without a name")
            continue
        if not indexes:
            problems.append("section '%s' has no slide indexes" % name)
            continue
        normalized: List[int] = []
        for value in indexes:
            try:
                number = int(value)
            except (TypeError, ValueError):
                problems.append("section '%s' has a non-numeric slide index '%s'" % (name, value))
                continue
            if number < 1 or number > slide_count:
                problems.append("section '%s' references slide %d outside 1..%d" % (name, number, slide_count))
                continue
            if number in assigned:
                problems.append("slide %d appears in sections '%s' and '%s'" % (number, assigned[number], name))
                continue
            assigned[number] = name
            normalized.append(number)
        clean.append({"name": name[:255], "slide_indexes": sorted(normalized)})
    if problems:
        raise core.ValidationError("Section mapping is invalid.", details={"problems": problems})
    ordered = [{"name": section["name"], "slide_indexes": section["slide_indexes"]} for section in clean]
    built = [{"spec_id": "s%03d" % number, "slide_index": number} for number in range(1, slide_count + 1)]
    spec_sections = [
        {"name": section["name"], "slide_ids": ["s%03d" % number for number in section["slide_indexes"]]}
        for section in ordered
    ]
    core.write_sections(prs, spec_sections, built)
    return {"sections_written": len(ordered), "sections": ordered, "slides_assigned": len(assigned)}


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def compare_decks(path_a: str, path_b: str, *, core=None) -> Dict[str, Any]:
    core = core or CORE
    core.require_pptx()
    a = core.open_presentation(core.resolve_input(path_a, label="baseline presentation"))
    b = core.open_presentation(core.resolve_input(path_b, label="comparison presentation"))
    stats_a = core.deck_stats(a, core.resolve_input(path_a, label="baseline presentation"))
    stats_b = core.deck_stats(b, core.resolve_input(path_b, label="comparison presentation"))
    titles_a = [core.slide_title_text(slide) for slide in a.slides]
    titles_b = [core.slide_title_text(slide) for slide in b.slides]
    added_titles = [title for title in titles_b if title and title not in titles_a]
    removed_titles = [title for title in titles_a if title and title not in titles_b]
    changed_positions = [
        {"slide": index + 1, "before": titles_a[index], "after": titles_b[index]}
        for index in range(min(len(titles_a), len(titles_b)))
        if titles_a[index] != titles_b[index]
    ]
    return {
        "baseline": {
            "file": os.path.basename(path_a),
            "slides": len(a.slides),
            "bytes": stats_a.get("bytes"),
            "words": stats_a.get("total_words"),
            "pictures": stats_a.get("pictures"),
            "charts": stats_a.get("charts"),
            "tables": stats_a.get("tables"),
            "media_bytes": stats_a.get("media_bytes"),
        },
        "comparison": {
            "file": os.path.basename(path_b),
            "slides": len(b.slides),
            "bytes": stats_b.get("bytes"),
            "words": stats_b.get("total_words"),
            "pictures": stats_b.get("pictures"),
            "charts": stats_b.get("charts"),
            "tables": stats_b.get("tables"),
            "media_bytes": stats_b.get("media_bytes"),
        },
        "delta": {
            "slides": len(b.slides) - len(a.slides),
            "bytes": (stats_b.get("bytes") or 0) - (stats_a.get("bytes") or 0),
            "words": (stats_b.get("total_words") or 0) - (stats_a.get("total_words") or 0),
            "pictures": (stats_b.get("pictures") or 0) - (stats_a.get("pictures") or 0),
            "charts": (stats_b.get("charts") or 0) - (stats_a.get("charts") or 0),
            "tables": (stats_b.get("tables") or 0) - (stats_a.get("tables") or 0),
        },
        "added_titles": added_titles,
        "removed_titles": removed_titles,
        "changed_positions": changed_positions[:100],
        "fonts_added": sorted(set(stats_b.get("fonts", {})) - set(stats_a.get("fonts", {}))),
        "fonts_removed": sorted(set(stats_a.get("fonts", {})) - set(stats_b.get("fonts", {}))),
    }


def compare_markdown(result: Dict[str, Any]) -> str:
    lines = ["# Deck comparison", ""]
    lines.append("| Metric | Baseline | Comparison | Delta |")
    lines.append("| --- | --- | --- | --- |")
    for key in ("slides", "bytes", "words", "pictures", "charts", "tables"):
        lines.append(
            "| %s | %s | %s | %s |"
            % (key, result["baseline"].get(key), result["comparison"].get(key), result["delta"].get(key))
        )
    lines.append("")
    if result.get("added_titles"):
        lines.append("## Added slides (by title)")
        lines.append("")
        for title in result["added_titles"][:50]:
            lines.append("- %s" % title)
        lines.append("")
    if result.get("removed_titles"):
        lines.append("## Removed slides (by title)")
        lines.append("")
        for title in result["removed_titles"][:50]:
            lines.append("- %s" % title)
        lines.append("")
    if result.get("changed_positions"):
        lines.append("## Changed titles at the same position")
        lines.append("")
        for item in result["changed_positions"][:50]:
            lines.append("- Slide %s: '%s' -> '%s'" % (item["slide"], item["before"], item["after"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
