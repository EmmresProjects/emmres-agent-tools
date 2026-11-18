"""Workbook authoring and editing engine.

Creates and modifies .xlsx workbooks non-destructively: a specification drives
sheet structure, styles, number formats, validation, named ranges, navigation,
print setup and protection. Editing helpers touch only the cells named in the
request and always report what changed.

Covers catalog categories 2, 3, 5, 7, 8 (authoring/editing/structured models/
controlled entry) at workbook level.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path


def _sdf_import(alias, filename):
    if alias in sys.modules:
        return sys.modules[alias]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


C = _sdf_import("sdf_common", "tools-sdf-common.py")
SkillToolError = C.SkillToolError

STYLE_ROLES = {"title", "subtitle", "header", "input", "formula", "output", "total",
               "warning", "assumption", "note", "label"}


def _opts():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="D0D7DE")
    return {
        "Alignment": Alignment,
        "Border": Border,
        "Font": Font,
        "PatternFill": PatternFill,
        "thin": thin,
    }


def style_library(theme=None):
    t = theme or {}
    ink = t.get("ink", "1B1F24")
    muted = t.get("muted", "5B6573")
    accent = t.get("accent", "1F4E79")
    accent2 = t.get("accent2", "2E75B6")
    positive = t.get("positive", "1E6B3A")
    warn = t.get("warning", "B45309")
    danger = t.get("danger", "B42318")
    input_bg = t.get("input_fill", "FFF7DB")
    formula_bg = t.get("formula_fill", "EEF4FB")
    total_bg = t.get("total_fill", "DCE6F1")
    note_bg = t.get("note_fill", "F5F5F0")
    return {
        "title": {"font": {"size": 18, "bold": True, "color": ink}},
        "subtitle": {"font": {"size": 11, "color": muted}},
        "header": {"font": {"size": 10, "bold": True, "color": "FFFFFF"},
                   "fill": accent, "align": "left"},
        "label": {"font": {"size": 10, "color": ink}},
        "input": {"fill": input_bg, "font": {"size": 10, "color": ink}, "border": True},
        "formula": {"fill": formula_bg, "font": {"size": 10, "color": accent2}},
        "output": {"font": {"size": 10, "bold": True, "color": ink}},
        "total": {"fill": total_bg, "font": {"size": 10, "bold": True, "color": ink},
                  "border_top": True},
        "warning": {"font": {"size": 10, "bold": True, "color": danger}},
        "assumption": {"font": {"size": 10, "italic": True, "color": warn}},
        "note": {"fill": note_bg, "font": {"size": 9, "italic": True, "color": muted}},
        "positive": positive,
        "danger": danger,
    }


def apply_role(cell, role, theme=None):
    if role not in STYLE_ROLES:
        raise SkillToolError("UNKNOWN_STYLE_ROLE", f"Unknown style role {role!r}.",
                             "Use one of: " + ", ".join(sorted(STYLE_ROLES)))
    o = _opts()
    spec = style_library(theme)[role]
    if "font" in spec:
        cell.font = o["Font"](**spec["font"])
    if spec.get("fill"):
        cell.fill = o["PatternFill"]("solid", fgColor=spec["fill"])
    if spec.get("border"):
        cell.border = o["Border"](left=o["thin"], right=o["thin"], top=o["thin"], bottom=o["thin"])
    if spec.get("border_top"):
        cell.border = o["Border"](top=o["thin"])
    if spec.get("align"):
        cell.alignment = o["Alignment"](horizontal=spec["align"], vertical="center")


# ==========================================================================
# workbook construction from a specification
# ==========================================================================


def build_workbook(path, spec, overwrite=False, theme=None):
    """spec = {"title","sheets":[{"name","role","title","intro","columns","rows","cells",
    "freeze","widths","table":{"name","style"},"validations":[...],"number_formats":{"col":"pattern"},
    "rows_to_style":[{"row":1,"role":"header"}]}],"named_ranges":[{"name","ref"}],
    "nav":true,"print":{"orientation","fit_width"},"properties":{...},"protect":{"sheet":true}}"""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    target = Path(path)
    if target.exists() and not overwrite:
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {target}.",
                             "Pass --overwrite to replace it.")
    if target.exists() and overwrite:
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)

    sheets = spec.get("sheets") or []
    if not sheets:
        raise SkillToolError("BAD_SPEC", "Specification contains no sheets.", "Provide at least one entry in 'sheets'.")
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    created = []
    notes = []
    for s in sheets:
        name = str(s.get("name") or f"Sheet{len(created) + 1}")[:31]
        ws = wb.create_sheet(name)
        created.append(name)
        row_cursor = 1
        if s.get("title"):
            ws.cell(row=row_cursor, column=1, value=s["title"])
            apply_role(ws.cell(row=row_cursor, column=1), "title", theme)
            row_cursor += 1
        if s.get("intro"):
            cell = ws.cell(row=row_cursor, column=1, value=s["intro"])
            apply_role(cell, "note", theme)
            cell.alignment = Alignment(wrap_text=False)
            row_cursor += 1
        header_row = row_cursor
        columns = s.get("columns") or []
        if columns:
            for ci, cname in enumerate(columns, start=1):
                cell = ws.cell(row=header_row, column=ci, value=cname)
                apply_role(cell, "header", theme)
            row_cursor += 1
        for r in s.get("rows") or []:
            for ci, v in enumerate(r, start=1):
                ws.cell(row=row_cursor, column=ci, value=v)
            row_cursor += 1
        for c in s.get("cells") or []:
            ref = c.get("cell") or c.get("ref")
            if not ref:
                raise SkillToolError("BAD_SPEC", "Each cell entry needs a 'cell' address.")
            cell = ws[ref]
            cell.value = c.get("value", c.get("formula"))
            if c.get("role"):
                apply_role(cell, c["role"], theme)
            if c.get("number_format"):
                cell.number_format = c["number_format"]
            if c.get("comment"):
                from openpyxl.comments import Comment

                cell.comment = Comment(str(c["comment"]), "sdf")
        for col, fmt in (s.get("number_formats") or {}).items():
            for r in range(header_row + 1, row_cursor):
                ws[f"{col}{r}"].number_format = fmt
        for role_spec in s.get("rows_to_style") or []:
            r = int(role_spec["row"])
            role = role_spec.get("role", "output")
            last_col = role_spec.get("last_column") or max(1, len(columns))
            for ci in range(1, last_col + 1):
                apply_role(ws.cell(row=r, column=ci), role, theme)
        if s.get("freeze"):
            ws.freeze_panes = s["freeze"]
        elif columns:
            ws.freeze_panes = f"A{header_row + 1}"
        for col, width in (s.get("widths") or {}).items():
            ws.column_dimensions[col].width = float(width)
        if columns and not s.get("widths"):
            for ci in range(1, len(columns) + 1):
                ws.column_dimensions[get_column_letter(ci)].width = max(12, min(34, len(str(columns[ci - 1])) + 6))
        table_spec = s.get("table")
        if table_spec and columns:
            from openpyxl.worksheet.table import Table, TableStyleInfo

            ref = f"A{header_row}:{get_column_letter(len(columns))}{max(header_row + 1, row_cursor - 1)}"
            tname = re.sub(r"\W", "_", str(table_spec.get("name") or f"tbl_{name}"))
            if not tname[0].isalpha():
                tname = "tbl_" + tname
            try:
                tbl = Table(displayName=tname, ref=ref)
                tbl.tableStyleInfo = TableStyleInfo(name=table_spec.get("style", "TableStyleMedium2"),
                                                    showRowStripes=True)
                ws.add_table(tbl)
            except Exception as exc:
                notes.append(f"Table {tname} could not be added to {name}: {exc}")
        for v in s.get("validations") or []:
            dv = DataValidation(type=v.get("type", "list"), operator=v.get("operator", "between"),
                                formula1=v.get("formula1"), formula2=v.get("formula2"),
                                allow_blank=bool(v.get("allow_blank", True)),
                                showErrorMessage=True,
                                errorTitle=v.get("error_title", "Invalid entry"),
                                error=v.get("error", "This value does not meet the validation rule."),
                                promptTitle=v.get("prompt_title"),
                                prompt=v.get("prompt"))
            ws.add_data_validation(dv)
            dv.add(v.get("range", "A1"))
        if s.get("protection"):
            ws.protection.sheet = bool(s["protection"].get("sheet", True))
            ws.protection.formatCells = bool(s["protection"].get("format_cells", False))
        if s.get("tab_color"):
            ws.sheet_properties.tabColor = s["tab_color"]

    # navigation index sheet
    if spec.get("nav") and len(created) > 1:
        idx = wb.create_sheet("Index", 0)
        idx.cell(row=1, column=1, value=spec.get("title") or "Workbook index")
        apply_role(idx.cell(row=1, column=1), "title", theme)
        idx.cell(row=3, column=1, value="Sheet").font = Font(bold=True)
        idx.cell(row=3, column=2, value="Purpose").font = Font(bold=True)
        for i, name in enumerate(created, start=4):
            c = idx.cell(row=i, column=1, value=name)
            c.hyperlink = f"#'{name}'!A1"
            c.font = Font(color="0563C1", underline="single")
            purpose = next((str(x.get("purpose") or "") for x in sheets if str(x.get("name"))[:31] == name), "")
            idx.cell(row=i, column=2, value=purpose)
        idx.column_dimensions["A"].width = 28
        idx.column_dimensions["B"].width = 70
        for ws in wb.worksheets[1:]:
            ws.cell(row=1, column=max(2, ws.max_column + 1), value="← Index")
            link = ws.cell(row=1, column=max(2, ws.max_column))
            link.hyperlink = "#'Index'!A1"
            link.font = Font(color="0563C1", size=9, underline="single")
            ws.sheet_properties.tabColor = ws.sheet_properties.tabColor or "1F4E79"
        wb.active = 0

    for nr in spec.get("named_ranges") or []:
        try:
            from openpyxl.workbook.defined_name import DefinedName

            wb.defined_names.add(DefinedName(nr["name"], attr_text=nr["ref"]))
        except Exception as exc:
            notes.append(f"Named range {nr.get('name')} skipped: {exc}")
    pr = spec.get("print") or {}
    if pr:
        for ws in wb.worksheets:
            ws.page_setup.orientation = pr.get("orientation", "portrait")
            ws.page_setup.fitToWidth = int(pr.get("fit_width", 1))
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            if pr.get("repeat_rows"):
                ws.print_title_rows = pr["repeat_rows"]
            ws.page_margins.left = ws.page_margins.right = float(pr.get("margin", 0.5))
            ws.oddFooter.center.text = pr.get("footer", "Page &P of &N")
            ws.oddHeader.right.text = pr.get("header", spec.get("title") or "")
    props = spec.get("properties") or {}
    if props:
        p = wb.properties
        p.title = props.get("title", spec.get("title"))
        p.creator = props.get("creator", "")
        p.description = props.get("description", "")
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(target))
    return {"file": target.name, "sheets": wb.sheetnames, "created_sheets": created, "notes": notes}


# ==========================================================================
# cell editing
# ==========================================================================


def set_cells(path, edits, output=None, overwrite=False):
    from openpyxl import load_workbook
    from openpyxl.comments import Comment

    src = Path(path)
    out = Path(output) if output else src
    if out != src and out.exists() and not overwrite:
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {out}.", "Pass --overwrite.")
    wb = load_workbook(filename=str(src))
    applied = []
    problems = []
    for e in edits:
        sheet = e.get("sheet")
        ref = e.get("cell") or e.get("ref")
        if not ref or not re.fullmatch(r"[A-Za-z]{1,3}\d{1,7}(:[A-Za-z]{1,3}\d{1,7})?", str(ref)):
            problems.append({"edit": e, "issue": "invalid_cell_reference"})
            continue
        if sheet is None:
            ws = wb.worksheets[0]
        else:
            if sheet not in wb.sheetnames:
                problems.append({"edit": e, "issue": "unknown_sheet", "available": wb.sheetnames})
                continue
            ws = wb[sheet]
        before = ws[ref].value
        if "formula" in e and e["formula"]:
            value = e["formula"] if str(e["formula"]).startswith("=") else "=" + str(e["formula"])
        else:
            value = e.get("value")
        ws[ref].value = value
        if e.get("number_format"):
            ws[ref].number_format = e["number_format"]
        if e.get("comment"):
            ws[ref].comment = Comment(str(e["comment"]), "sdf")
        if e.get("role"):
            apply_role(ws[ref], e["role"])
        applied.append({"sheet": ws.title, "cell": ref, "before": before, "after": value})
    wb.save(str(out))
    return {"file": out.name, "applied": applied, "applied_count": len(applied), "problems": problems,
            "changed_sheets": sorted({a["sheet"] for a in applied})}


def bulk_transform(path, rules, output=None, dry_run=True, overwrite=False):
    """Apply column-scoped rules across sheets: trim, replace, number_format, fill_blanks, recase."""
    from openpyxl import load_workbook

    src = Path(path)
    out = Path(output) if output else src
    if not dry_run and out != src and out.exists() and not overwrite:
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {out}.", "Pass --overwrite.")
    wb = load_workbook(filename=str(src))
    changes = []
    for rule in rules:
        action = rule.get("action")
        explicit_sheets = bool(rule.get("sheets"))
        sheets = rule.get("sheets") or wb.sheetnames
        columns = rule.get("columns") or []
        rng = rule.get("range")
        for sname in sheets:
            if sname not in wb.sheetnames:
                raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sname!r} not found.", "Check --rules sheets.")
            ws = wb[sname]
            targets = []
            if rng:
                for row in ws[rng]:
                    targets.extend(row)
            else:
                if not columns:
                    raise SkillToolError("BAD_RULE", "Each rule needs 'range' or 'columns'.", "Add a scope to the rule.")
                header_row = rule.get("header_row", 1)
                headers = {}
                for cell in ws[header_row]:
                    if cell.value:
                        headers[str(cell.value).strip()] = cell.column_letter
                for col in columns:
                    letter = headers.get(str(col))
                    if letter is None:
                        if re.fullmatch(r"[A-Za-z]{1,3}", str(col)):
                            letter = str(col)
                        elif explicit_sheets:
                            raise SkillToolError("UNKNOWN_COLUMN", f"Column {col!r} not found in {sname}.",
                                                 "Available: " + ", ".join(k for k in headers if k))
                        else:
                            # unnamed sheet without this column: skip it, do not fail the whole batch
                            break
                    for r in range(header_row + 1, ws.max_row + 1):
                        targets.append(ws[f"{letter}{r}"])
            for cell in targets:
                before = cell.value
                after = before
                if action == "trim" and isinstance(before, str):
                    after = re.sub(r"\s+", " ", before).strip()
                elif action == "replace" and isinstance(before, str):
                    after = before.replace(str(rule.get("find", "")), str(rule.get("replace_with", "")))
                elif action == "number_format":
                    cell.number_format = str(rule.get("pattern", "General"))
                    continue
                elif action == "fill_blanks":
                    if C.is_empty(before):
                        after = rule.get("value")
                elif action == "recase" and isinstance(before, str):
                    mode = rule.get("mode", "upper")
                    after = before.upper() if mode == "upper" else before.lower() if mode == "lower" else before.title()
                elif action == "round" and C.to_number(before) is not None:
                    after = C.round_half_up(C.to_number(before), int(rule.get("places", 2)))
                else:
                    continue
                if after != before:
                    changes.append({"sheet": sname, "cell": cell.coordinate, "before": before, "after": after,
                                    "rule": action})
                    if len(changes) >= 20000:
                        break
                    if not dry_run:
                        cell.value = after
    if not dry_run:
        wb.save(str(out))
    return {"dry_run": dry_run, "file": out.name, "change_count": len(changes),
            "changes": changes[:1000], "truncated": len(changes) > 1000}


def _header_map(ws):
    return [str(c.value) if c.value else "" for c in ws[1]]


# ==========================================================================
# layout, formatting, validation, protection, print
# ==========================================================================


def apply_layout(path, spec, output=None):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = load_workbook(filename=str(path))
    out = Path(output or path)
    notes = []
    for sname in spec.get("sheets") or wb.sheetnames:
        ws = wb[sname]
        for col, width in (spec.get("widths") or {}).items():
            ws.column_dimensions[col].width = float(width)
        for row, height in (spec.get("heights") or {}).items():
            ws.row_dimensions[int(row)].height = float(height)
        if spec.get("autofit"):
            for ci in range(1, min(ws.max_column, 60) + 1):
                longest = 0
                for r in range(1, min(ws.max_row, 400) + 1):
                    v = ws.cell(row=r, column=ci).value
                    if isinstance(v, str):
                        longest = max(longest, min(len(v), 60))
                ws.column_dimensions[get_column_letter(ci)].width = max(10, min(48, longest + 3))
        if spec.get("wrap"):
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and len(cell.value) > 40:
                        cell.alignment = cell.alignment.copy(wrap_text=True, vertical="top")
        if spec.get("freeze"):
            ws.freeze_panes = spec["freeze"]
        for r in spec.get("group_rows") or []:
            ws.row_dimensions[int(r)].outlineLevel = int(spec.get("outline_level", 1))
            ws.row_dimensions[int(r)].hidden = bool(spec.get("collapse", False))
        for r in spec.get("hide_rows") or []:
            ws.row_dimensions[int(r)].hidden = True
        for c in spec.get("hide_columns") or []:
            ws.column_dimensions[c].hidden = True
        if spec.get("header_rows"):
            ws.print_title_rows = spec["header_rows"]
        notes.append(f"{sname}: layout updated")
    wb.save(str(out))
    return {"file": out.name, "sheets": spec.get("sheets") or wb.sheetnames, "notes": notes}


def apply_conditional_format(path, rules, output=None, overwrite=False):
    from openpyxl import load_workbook
    from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule, FormulaRule, IconSetRule

    out = Path(output or path)
    if out != Path(path) and out.exists() and not overwrite:
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {out}.", "Pass --overwrite.")
    wb = load_workbook(filename=str(path))
    applied = []
    for rule in sorted(rules, key=lambda r: int(r.get("priority", 10))):
        sname = rule.get("sheet")
        if sname not in wb.sheetnames:
            raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sname!r} not found.", "Check --rules sheet values.")
        ws = wb[sname]
        rng = rule.get("range", "A1")
        kind = rule.get("type", "cell_is")
        fmt = rule.get("format") or {"fill": "FFC7CE", "font_color": "9C0006"}
        from openpyxl.styles import Font, PatternFill

        fill = PatternFill(start_color=fmt.get("fill", "FFC7CE"), end_color=fmt.get("fill", "FFC7CE"), fill_type="solid")
        font = Font(color=fmt.get("font_color", "9C0006"), bold=bool(fmt.get("bold", False)))
        if kind == "cell_is":
            cf = CellIsRule(operator=rule.get("operator", "greaterThan"),
                            formula=[str(rule.get("formula", "0"))], fill=fill, font=font)
        elif kind == "formula":
            cf = FormulaRule(formula=[str(rule.get("formula"))], fill=fill, font=font)
        elif kind == "color_scale":
            cf = ColorScaleRule(start_type="min", start_color=fmt.get("start", "F8696B"),
                                mid_type="percentile", mid_value=50, mid_color=fmt.get("mid", "FFEB84"),
                                end_type="max", end_color=fmt.get("end", "63BE7B"))
        elif kind == "data_bar":
            cf = DataBarRule(start_type="num", start_value=0, end_type="max", color=fmt.get("fill", "5B9BD5"))
        elif kind == "icon_set":
            cf = IconSetRule(icon_style=rule.get("icon_style", "3TrafficLights1"), type="percent",
                             values=[0, 33, 67])
        else:
            raise SkillToolError("BAD_RULE", f"Unknown conditional format type {kind!r}.",
                                 "Use cell_is, formula, color_scale, data_bar or icon_set.")
        ws.conditional_formatting.add(rng, cf)
        applied.append({"sheet": sname, "range": rng, "type": kind, "priority": rule.get("priority", 10)})
    wb.save(str(out))
    return {"file": out.name, "applied": applied, "count": len(applied),
            "note": "Rules apply in priority order; the first matching rule wins for overlapping formats."}


def add_validations(path, rules, output=None):
    from openpyxl import load_workbook
    from openpyxl.worksheet.datavalidation import DataValidation

    out = Path(output or path)
    wb = load_workbook(filename=str(path))
    added = []
    for r in rules:
        sname = r.get("sheet")
        if sname not in wb.sheetnames:
            raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sname!r} not found.", "Check --rules sheet values.")
        ws = wb[sname]
        dv = DataValidation(type=r.get("type", "list"), operator=r.get("operator", "between"),
                            formula1=r.get("formula1"), formula2=r.get("formula2"),
                            allow_blank=bool(r.get("allow_blank", True)), showErrorMessage=True,
                            errorTitle=r.get("error_title", "Invalid entry"),
                            error=r.get("error", "This value does not meet the validation rule."),
                            promptTitle=r.get("prompt_title"), prompt=r.get("prompt"))
        ws.add_data_validation(dv)
        dv.add(r.get("range", "A1"))
        added.append({"sheet": sname, "range": r.get("range"), "type": r.get("type", "list")})
    wb.save(str(out))
    return {"file": out.name, "validations": added, "count": len(added),
            "guidance": "Every validation should carry an input prompt and an actionable error message."}


def protect_workbook(path, spec, output=None):
    """Approval-gated. Protects sheets/structure without inventing passwords."""
    from openpyxl import load_workbook

    out = Path(output or path)
    wb = load_workbook(filename=str(path))
    summary = []
    unlock = set(spec.get("unlocked_ranges") or [])
    for sname in spec.get("sheets") or wb.sheetnames:
        ws = wb[sname]
        ws.protection.sheet = True
        ws.protection.insertRows = bool(spec.get("allow_insert_rows", False))
        ws.protection.formatCells = bool(spec.get("allow_format_cells", False))
        ws.protection.sort = bool(spec.get("allow_sort", False))
        ws.protection.autoFilter = bool(spec.get("allow_filter", False))
        unlocked = 0
        for rng in unlock:
            if "!" in rng:
                sheet_part, cells = rng.split("!", 1)
                if sheet_part.strip("'") != sname:
                    continue
            else:
                cells = rng
            for row in ws[cells]:
                for cell in row:
                    cell.protection = cell.protection.copy(locked=False)
                    unlocked += 1
        summary.append({"sheet": sname, "protected": True, "unlocked_cells": unlocked})
    wb.security.lockStructure = bool(spec.get("protect_structure", True))
    wb.save(str(out))
    return {"file": out.name, "sheets": summary,
            "recovery": "Protection here is usability guidance, not cryptography. Keep an unprotected master copy; "
                        "any password is applied by the operator after delivery."}


def setup_print(path, spec, output=None):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path))
    out = Path(output or path)
    applied = []
    for sname in spec.get("sheets") or wb.sheetnames:
        ws = wb[sname]
        ws.page_setup.orientation = spec.get("orientation", "landscape")
        ws.page_setup.paperSize = {"A4": 9, "Letter": 1, "Legal": 5}.get(str(spec.get("paper", "A4")).upper(), 9)
        ws.page_setup.fitToWidth = int(spec.get("fit_width", 1))
        ws.page_setup.fitToHeight = int(spec.get("fit_height", 0))
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = float(spec.get("margin", 0.4))
        ws.page_margins.top = ws.page_margins.bottom = float(spec.get("margin_top", 0.6))
        if spec.get("repeat_rows"):
            ws.print_title_rows = spec["repeat_rows"]
        if spec.get("print_area"):
            ws.print_area = spec["print_area"]
        ws.oddHeader.left.text = spec.get("header_left", "")
        ws.oddHeader.center.text = spec.get("header_center", "&A")
        ws.oddFooter.right.text = spec.get("footer_right", "Page &P of &N")
        ws.oddFooter.left.text = spec.get("footer_left", "&D")
        if spec.get("breaks"):
            from openpyxl.worksheet.pagebreak import Break

            for row in spec["breaks"]:
                ws.row_breaks.append(Break(id=int(row)))
        applied.append({"sheet": sname, "orientation": ws.page_setup.orientation,
                        "fit_to_width": ws.page_setup.fitToWidth})
    wb.save(str(out))
    return {"file": out.name, "sheets": applied,
            "note": "Fitted width and repeated header rows are what keep printed/PDF output readable."}


def build_navigation(path, output=None):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path))
    out = Path(output or path)
    created = False
    if "Index" not in wb.sheetnames:
        idx = wb.create_sheet("Index", 0)
        created = True
    else:
        idx = wb["Index"]
    idx.cell(row=1, column=1, value="Workbook index").font = style_font(16, bold=True)
    idx.cell(row=3, column=1, value="Sheet").font = style_font(10, bold=True)
    idx.cell(row=3, column=2, value="Rows").font = style_font(10, bold=True)
    row = 4
    for ws in wb.worksheets:
        if ws.title == "Index":
            continue
        c = idx.cell(row=row, column=1, value=ws.title)
        c.hyperlink = f"#'{ws.title}'!A1"
        c.font = style_font(10, color="0563C1", underline="single")
        idx.cell(row=row, column=2, value=ws.max_row)
        link = ws.cell(row=1, column=max(2, ws.max_column + 1), value="← Index")
        link.hyperlink = "#'Index'!A1"
        link.font = style_font(9, color="0563C1", underline="single")
        row += 1
    idx.column_dimensions["A"].width = 30
    idx.column_dimensions["B"].width = 12
    wb.save(str(out))
    return {"file": out.name, "index_created": created, "linked_sheets": row - 4}


def style_font(size=10, bold=False, color=None, underline=None):
    from openpyxl.styles import Font

    return Font(size=size, bold=bold, color=color, underline=underline)


def sort_range(path, sheet, cell_range, keys, has_header=True, output=None, descending=False):
    """Sort a rectangular range by key columns, refusing when formulas or merges are inside."""
    from openpyxl import load_workbook
    from openpyxl.utils import range_boundaries

    wb = load_workbook(filename=str(path))
    if sheet not in wb.sheetnames:
        raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sheet!r} not found.")
    ws = wb[sheet]
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    merged_inside = [str(r) for r in ws.merged_cells.ranges
                     if not (r.max_row < min_row or r.min_row > max_row or r.max_col < min_col or r.min_col > max_col)]
    if merged_inside:
        raise SkillToolError("MERGED_CELLS_BLOCK_SORT",
                             "Sorting a range containing merged cells corrupts layout.",
                             "Unmerge the range first ('merge-audit' lists them).",
                             {"merged": merged_inside[:20]})
    formulas = []
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                formulas.append(cell.coordinate)
    if formulas:
        raise SkillToolError("FORMULA_IN_SORT_RANGE",
                             f"{len(formulas)} formula cells are inside the sort range; row order would break them.",
                             "Sort a values-only table, or sort source data before formulas are applied.",
                             {"formula_cells": formulas[:20]})
    data = [[c.value for c in row] for row in ws.iter_rows(min_row=min_row, max_row=max_row,
                                                           min_col=min_col, max_col=max_col)]
    header, body = (data[0], data[1:]) if has_header else (None, data)
    headers = header or ["" for _ in range(max_col - min_col + 1)]
    idxs = []
    for k in keys:
        if isinstance(k, int):
            idxs.append(k - min_col if k >= min_col else k)
        else:
            if k not in headers:
                raise SkillToolError("UNKNOWN_COLUMN", f"Key {k!r} is not a header in row {min_row}.",
                                     "Headers: " + ", ".join(str(h) for h in headers))
            idxs.append(headers.index(k))
    body.sort(key=lambda r: [(_sort_key(r[i])) for i in idxs], reverse=descending)
    for r_off, row_vals in enumerate(body):
        for c_off, val in enumerate(row_vals):
            ws.cell(row=min_row + (1 if has_header else 0) + r_off, column=min_col + c_off, value=val)
    out = Path(output or path)
    wb.save(str(out))
    return {"file": out.name, "sheet": sheet, "range": cell_range, "sorted_rows": len(body),
            "keys": keys, "descending": descending}


def _sort_key(v):
    n = C.to_number(v)
    if n is not None:
        return (0, n, "")
    d = C.parse_date(v)
    if d:
        return (1, d.toordinal(), "")
    return (2, 0, str(v or ""))


def merge_audit(path):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path))
    findings = []
    for ws in wb.worksheets:
        ranges = list(ws.merged_cells.ranges)
        if ranges:
            findings.append({"sheet": ws.title, "merged_count": len(ranges),
                             "ranges": [str(r) for r in ranges[:50]],
                             "risk": "Merged cells break sorting, filtering, pivot sources and screen readers."})
    return {"findings": findings, "total_merged": sum(f["merged_count"] for f in findings),
            "remediation": ["Unmerge and use 'Center Across Selection' for visual centring",
                            "Use one header row with freeze panes instead of merged title blocks",
                            "Keep data tables free of any merged region"]}
