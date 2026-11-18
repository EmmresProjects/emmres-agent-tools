#!/usr/bin/env python3
"""Repair engine for the powerpoint-agent skill tool.

Turns audit findings into concrete, reversible fixes.  The engine is
conservative by design:

* every change is recorded with before/after values in an undo manifest
* a dry run is the default; writing requires explicit approval
* only rules listed in ``asset-repair-policy.json`` are auto-fixed
* the repaired deck is re-audited before it is published to the output path
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

CORE = None
AUDIT = None


def bind(core_module, audit_module) -> None:
    global CORE, AUDIT
    CORE = core_module
    AUDIT = audit_module


DEFAULT_POLICY: Dict[str, Any] = {
    "policy_version": "1.0",
    "max_changes": 500,
    "fixers": {},
}


def load_policy(path: Optional[str] = None, *, core=None) -> Dict[str, Any]:
    core = core or CORE
    if path:
        policy = core.load_json(core.resolve_input(path, exts=(".json",), label="repair policy"))
    else:
        asset = core.asset_path("asset-repair-policy.json")
        if not os.path.isfile(asset):
            return dict(DEFAULT_POLICY)
        policy = core.load_json(asset)
    if not isinstance(policy, dict):
        raise core.ValidationError("Repair policy must be a JSON object.")
    fixers = policy.get("fixers")
    if not isinstance(fixers, dict):
        raise core.ValidationError("Repair policy needs a 'fixers' object mapping rule ids to fixer definitions.")
    unknown = [rule_id for rule_id in fixers if rule_id not in FIXERS]
    if unknown:
        raise core.ValidationError(
            "Repair policy references unknown fixers: %s" % ", ".join(sorted(unknown)),
            hint="Use rule ids implemented by tools-repair-engine.py: %s" % ", ".join(sorted(FIXERS)),
        )
    return policy


# ---------------------------------------------------------------------------
# change records
# ---------------------------------------------------------------------------

@dataclass
class Change:
    rule_id: str
    slide: Optional[int]
    shape: str
    action: str
    before: str
    after: str
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "slide": self.slide,
            "shape": self.shape,
            "action": self.action,
            "before": self.before,
            "after": self.after,
            "note": self.note,
        }


@dataclass
class RepairState:
    prs: Any
    config: Dict[str, Any]
    theme: Any
    core: Any
    log: Any
    changes: List[Change] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    max_changes: int = 500
    deadline: Any = None

    def record(self, change: Change) -> bool:
        if len(self.changes) >= self.max_changes:
            self.skipped.append({"rule_id": change.rule_id, "reason": "max_changes reached"})
            return False
        self.changes.append(change)
        return True

    def check_deadline(self, label: str) -> None:
        if self.deadline:
            self.deadline.check(label)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _slide(prs, index: Optional[int]):
    if not index:
        return None
    if index < 1 or index > len(prs.slides):
        return None
    return prs.slides[index - 1]


def _find_shape(slide, name: str):
    if slide is None or not name:
        return None
    for shape in CORE.iter_shapes(slide.shapes):
        if shape.name == name:
            return shape
    return None


def _first_font_from_theme(theme, allowlist: Sequence[str]) -> str:
    preferred = theme.font("body")
    if not allowlist or preferred.lower() in {item.lower() for item in allowlist}:
        return preferred
    return allowlist[0]


def _readable_text_color(background: str, theme) -> str:
    light = theme.color("text_inverse")
    dark = theme.color("text")
    return light if CORE.contrast_ratio(light, background) >= CORE.contrast_ratio(dark, background) else dark


def _set_alt_text(shape, value: str) -> bool:
    for attribute in ("nvPicPr", "nvGraphicFramePr", "nvSpPr"):
        holder = getattr(shape._element, attribute, None)
        if holder is not None and getattr(holder, "cNvPr", None) is not None:
            holder.cNvPr.set("descr", value)
            return True
    try:
        shape._element._nvXxPr.cNvPr.set("descr", value)
        return True
    except Exception:
        return False


def _read_alt_text(shape) -> str:
    for attribute in ("nvPicPr", "nvGraphicFramePr", "nvSpPr"):
        holder = getattr(shape._element, attribute, None)
        if holder is not None and getattr(holder, "cNvPr", None) is not None:
            return holder.cNvPr.get("descr") or ""
    return ""


def _find_caption_near(slide, shape) -> str:
    """Look for a caption text box below the shape (used for generated alt text)."""

    try:
        shape_bottom = shape.top + shape.height
    except Exception:
        return ""
    best = None
    best_delta = None
    for candidate in CORE.iter_shapes(slide.shapes):
        if candidate is shape or not getattr(candidate, "has_text_frame", False):
            continue
        name = getattr(candidate, "name", "")
        text = candidate.text_frame.text.strip()
        if not text:
            continue
        delta = abs(candidate.top - shape_bottom)
        if "caption" in name.lower() or delta < CORE.inches_to_emu(0.6):
            if best_delta is None or delta < best_delta:
                best, best_delta = text, delta
    return best or ""


# ---------------------------------------------------------------------------
# fixers
# ---------------------------------------------------------------------------

FixerResult = Tuple[bool, str, str, str]
# (applied, before, after, note)


def fix_raise_font_size(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    minimum = float(CORE.cfg(state.config, "audit.min_font_pt", 14))
    changed = 0
    before_values = set()
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            current = run.font.size.pt if run.font.size is not None else None
            if current is None or current >= minimum:
                continue
            before_values.add(round(current, 1))
            run.font.size = CORE.Pt(minimum)
            changed += 1
    if not changed:
        return False, "", "", "no explicit small runs to raise"
    return (
        True,
        ",".join("%.0fpt" % value for value in sorted(before_values)),
        "%.0fpt" % minimum,
        "%d run(s) raised to the minimum size; re-check the layout for overflow" % changed,
    )


def fix_substitute_font(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    fonts = set()
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.name:
                fonts.add(run.font.name)
    if not fonts:
        return False, "", "", "no explicit fonts to substitute"
    allowlist = [str(item) for item in (CORE.cfg(state.config, "audit.font_allowlist", []) or [])]
    target = _first_font_from_theme(state.theme, allowlist)
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.name and run.font.name.lower() not in {item.lower() for item in allowlist}:
                run.font.name = target
    return True, ", ".join(sorted(fonts)), target, "unapproved fonts replaced with the theme body font"


def fix_normalize_fonts_in_shape(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    counts: Dict[str, int] = {}
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.name:
                counts[run.font.name] = counts.get(run.font.name, 0) + 1
    if len(counts) < 2:
        return False, "", "", "fewer than two font families"
    target = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.name and run.font.name != target:
                run.font.name = target
    return True, ", ".join(sorted(counts)), target, "mixed fonts unified to the dominant family"


def fix_remove_empty_placeholder(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None or not getattr(shape, "is_placeholder", False):
        return False, "", "", "placeholder not found"
    if shape.has_text_frame and shape.text_frame.text.strip():
        return False, "", "", "placeholder is no longer empty"
    CORE._remove_shape(shape)
    return True, "empty placeholder", "removed", "empty placeholder deleted"


def fix_contrast(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    evidence = finding.get("evidence") or {}
    background = str(evidence.get("background") or "FFFFFF")
    target = _readable_text_color(background, state.theme)
    changed = 0
    before = set()
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            try:
                current = run.font.color.rgb
                current_hex = "%02X%02X%02X" % (current[0], current[1], current[2])
            except Exception:
                current_hex = ""
            if current_hex and CORE.contrast_ratio(current_hex, background) < float(evidence.get("required", 4.5)):
                before.add(current_hex)
                run.font.color.rgb = CORE.RGBColor.from_string(CORE.parse_hex(target))
                changed += 1
    if not changed:
        return False, "", "", "no explicit low-contrast runs could be adjusted"
    return True, ", ".join(sorted(before)) or "inherited", target, "text recoloured for WCAG contrast on %s" % background


def fix_move_inside_slide(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    width = state.prs.slide_width
    height = state.prs.slide_height
    before = CORE.shape_bounds_in(shape)
    left = max(0, min(shape.left, width - shape.width)) if shape.width <= width else 0
    top = max(0, min(shape.top, height - shape.height)) if shape.height <= height else 0
    if (left, top) == (shape.left, shape.top):
        return False, "", "", "shape already inside the canvas"
    shape.left, shape.top = int(left), int(top)
    after = CORE.shape_bounds_in(shape)
    return True, "x=%s y=%s in" % (before["left"], before["top"]), "x=%s y=%s in" % (after["left"], after["top"]), "shape nudged inside the slide"


def fix_reading_order(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    if slide is None:
        return False, "", "", "slide not found"
    try:
        title = slide.shapes.title
    except Exception:
        title = None
    if title is None:
        return False, "", "", "no title placeholder to promote"
    tree = slide.shapes._spTree
    element = title._element
    children = [child for child in tree if child.tag.endswith("}sp") or child.tag.endswith("}pic") or child.tag.endswith("}graphicFrame")]
    position = children.index(element) if element in children else -1
    if position <= 0:
        return False, "", "", "title already first in reading order"
    tree.remove(element)
    tree.insert(2, element)
    return True, "reading position %d" % (position + 1), "reading position 1", "title moved to the front of the shape tree"


def fix_table_header(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None or not getattr(shape, "has_table", False):
        return False, "", "", "table not found"
    if shape.table.first_row:
        return False, "", "", "header row already enabled"
    shape.table.first_row = True
    return True, "first_row=False", "first_row=True", "table header row enabled"


def fix_alt_text(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None:
        return False, "", "", "shape not found"
    existing = _read_alt_text(shape)
    if existing.strip():
        return False, "", "", "alt text already present"
    caption = _find_caption_near(slide, shape)
    if caption:
        generated = caption
        note = "alt text generated from the nearest caption; review it for accuracy"
    else:
        try:
            name = os.path.splitext(os.path.basename(shape.image.filename or "image"))[0]
        except Exception:
            name = ""
        generated = ("%s on slide %d" % (name.replace("_", " ").replace("-", " ").strip(), finding.get("slide") or 0)).strip()
        note = "alt text generated from the file name; replace it with a real description"
    if not _set_alt_text(shape, generated):
        return False, "", "", "shape has no alt-text property"
    return True, "", generated, note


def fix_remove_blocked_link(state: RepairState, finding: Dict[str, Any]) -> FixerResult:
    slide = _slide(state.prs, finding.get("slide"))
    shape = _find_shape(slide, finding.get("location", ""))
    if shape is None or not getattr(shape, "has_text_frame", False):
        return False, "", "", "shape not found"
    removed = 0
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            try:
                address = run.hyperlink.address
            except Exception:
                address = None
            if address:
                run.hyperlink.address = None
                removed += 1
    if not removed:
        return False, "", "", "no hyperlink address could be removed"
    return True, "%d link(s)" % removed, "removed", "blocked hyperlink protocol stripped from the run"


FIXERS: Dict[str, Callable[[RepairState, Dict[str, Any]], FixerResult]] = {
    "TYP001": fix_raise_font_size,
    "TYP002": fix_substitute_font,
    "TYP003": fix_normalize_fonts_in_shape,
    "TYP005": fix_remove_empty_placeholder,
    "COL001": fix_contrast,
    "COL002": fix_contrast,
    "GEO001": fix_move_inside_slide,
    "A11Y003": fix_reading_order,
    "A11Y005": fix_table_header,
    "A11Y002": fix_alt_text,
    "LNK001": fix_remove_blocked_link,
}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def repair_deck(
    path: str,
    out_path: str,
    *,
    config: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
    pack: Optional[Dict[str, Any]] = None,
    rules: Optional[Sequence[str]] = None,
    approve: bool = False,
    dry_run: bool = True,
    theme_name: Optional[str] = None,
    log=None,
    deadline=None,
    overwrite: bool = False,
    core=None,
    audit=None,
) -> Dict[str, Any]:
    """Audit, apply safe fixes, verify, and (when approved) write the result."""

    core = core or CORE
    audit = audit or AUDIT
    core.require_pptx()
    policy = policy or load_policy(core=core)
    pack = pack or audit.load_rule_pack(core=core)
    log = log or core.RunLog()

    if not dry_run and not approve:
        raise core.SafetyError(
            "Repair writes a modified deck and requires --approve.",
            hint="Run with --dry-run first to review the planned changes, then rerun with --approve.",
            details={"command": "repair"},
        )

    source = core.resolve_input(path)
    destination = core.resolve_output(out_path, overwrite=overwrite, exts=(".pptx",), label="output presentation")
    if os.path.abspath(source) == os.path.abspath(destination):
        raise core.SafetyError(
            "Repair must write to a new file; in-place repair is not supported.",
            hint="Choose a different --out path; the original stays untouched.",
        )

    before_report = audit.audit_deck(source, config=config, pack=pack, log=log, deadline=deadline, core=core)
    prs = core.open_presentation(source)
    theme = core.load_theme(theme_name or core.cfg(config, "defaults.theme", "corporate-blue"))

    state = RepairState(
        prs=prs,
        config=config,
        theme=theme,
        core=core,
        log=log,
        max_changes=int(policy.get("max_changes", 500)),
        deadline=deadline,
    )

    fixers = policy.get("fixers") or {}
    selected = [str(item) for item in rules] if rules else list(fixers.keys())
    unknown = [rule_id for rule_id in selected if rule_id not in FIXERS]
    if unknown:
        raise core.ValidationError(
            "Unknown repair rule(s): %s" % ", ".join(sorted(unknown)),
            hint="Available fixers: %s" % ", ".join(sorted(FIXERS)),
        )

    findings_by_rule = audit.findings_by_rule(before_report)
    applied_rules: List[str] = []
    for rule_id in selected:
        definition = fixers.get(rule_id)
        if not definition:
            state.skipped.append({"rule_id": rule_id, "reason": "rule has no fixer in the active policy"})
            continue
        fixer = FIXERS.get(rule_id)
        if fixer is None:
            state.skipped.append({"rule_id": rule_id, "reason": "no fixer implementation"})
            continue
        group = findings_by_rule.get(rule_id) or []
        if not group:
            continue
        # Approval-gated fixers are planned during a dry run (nothing is written)
        # but never applied to a published file without --approve.
        if definition.get("requires_approval") and not approve and not dry_run:
            state.skipped.append({"rule_id": rule_id, "reason": "fixer requires explicit approval (--approve)"})
            continue
        for finding in group:
            state.check_deadline("repairing %s" % rule_id)
            try:
                applied, before, after, note = fixer(state, finding)
            except core.AgentError:
                raise
            except Exception as exc:
                state.skipped.append(
                    {"rule_id": rule_id, "slide": finding.get("slide"), "reason": "%s: %s" % (type(exc).__name__, exc)}
                )
                continue
            if applied:
                state.record(
                    Change(
                        rule_id=rule_id,
                        slide=finding.get("slide"),
                        shape=finding.get("location", ""),
                        action=str(definition.get("fixer", rule_id)),
                        before=before,
                        after=after,
                        note=note,
                    )
                )
        if rule_id not in applied_rules:
            applied_rules.append(rule_id)

    manual = [
        finding
        for finding in before_report.get("findings", [])
        if finding["rule_id"] not in ({change.rule_id for change in state.changes})
    ]

    temp_path = None
    post_report: Optional[Dict[str, Any]] = None
    published = False
    if state.changes:
        directory = os.path.dirname(destination) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".pptx-agent-repair-", suffix=".pptx", dir=directory)
        os.close(fd)
        core.save_presentation(prs, temp_path)
        post_report = audit.audit_deck(temp_path, config=config, pack=pack, log=log, deadline=deadline, core=core)
        if not dry_run:
            os.replace(temp_path, destination)
            published = True
            temp_path = None
    if temp_path and os.path.exists(temp_path):
        os.unlink(temp_path)

    result = {
        "file": os.path.basename(source),
        "output": os.path.basename(destination) if published else "",
        "output_path": destination if published else "",
        "dry_run": bool(dry_run),
        "approved": bool(approve),
        "applied_rule_ids": applied_rules,
        "change_count": len(state.changes),
        "changes": [change.as_dict() for change in state.changes],
        "skipped": state.skipped,
        "manual_findings": manual[:200],
        "manual_count": len(manual),
        "before": {
            "score": before_report.get("score"),
            "counts": before_report.get("counts"),
            "gate": before_report.get("gate"),
        },
        "after": (
            {
                "score": post_report.get("score"),
                "counts": post_report.get("counts"),
                "gate": post_report.get("gate"),
            }
            if post_report
            else None
        ),
    }
    return result


def repair_report_markdown(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Repair report")
    lines.append("")
    lines.append("- Input: `%s`" % result.get("file", ""))
    lines.append("- Output: %s" % ("`%s`" % result["output"] if result.get("output") else "_dry run, nothing written_"))
    lines.append("- Approved: %s" % ("yes" if result.get("approved") else "no"))
    lines.append("- Changes: %d" % result.get("change_count", 0))
    before = result.get("before") or {}
    after = result.get("after") or {}
    lines.append("")
    lines.append("## Before / after")
    lines.append("")
    lines.append("| Metric | Before | After (verified on a temp copy) |")
    lines.append("| --- | --- | --- |")
    counts_before = before.get("counts") or {}
    counts_after = after.get("counts") or {}
    lines.append("| Score | %s | %s |" % (before.get("score"), after.get("score") if after else "-"))
    lines.append("| Errors | %s | %s |" % (counts_before.get("error", 0), counts_after.get("error", 0) if after else "-"))
    lines.append("| Warnings | %s | %s |" % (counts_before.get("warning", 0), counts_after.get("warning", 0) if after else "-"))
    lines.append("| Info | %s | %s |" % (counts_before.get("info", 0), counts_after.get("info", 0) if after else "-"))
    lines.append("")
    changes = result.get("changes") or []
    if changes:
        lines.append("## Applied changes")
        lines.append("")
        lines.append("| Rule | Slide | Shape | Action | Before | After | Note |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for change in changes:
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (
                    change["rule_id"],
                    change.get("slide") or "-",
                    (change.get("shape") or "-").replace("|", "/"),
                    change["action"],
                    (change.get("before") or "-").replace("|", "/")[:60],
                    (change.get("after") or "-").replace("|", "/")[:60],
                    (change.get("note") or "").replace("|", "/")[:80],
                )
            )
        lines.append("")
    manual = result.get("manual_findings") or []
    if manual:
        lines.append("## Remaining manual work (%d)" % result.get("manual_count", len(manual)))
        lines.append("")
        for finding in manual[:60]:
            where = "slide %s" % finding["slide"] if finding.get("slide") else "deck"
            lines.append("- **%s** (%s): %s" % (finding["rule_id"], where, finding["message"]))
        lines.append("")
    skipped = result.get("skipped") or []
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for item in skipped[:40]:
            lines.append("- `%s`: %s" % (item.get("rule_id", ""), item.get("reason", "")))
        lines.append("")
    if result.get("dry_run"):
        lines.append("## Next step")
        lines.append("")
        lines.append("Review the changes above, then rerun the same command with `--approve` and without `--dry-run`.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
