"""Quality assurance, governance and release-control engine.

Covers catalog categories 28, 29, 30: review workflow artefacts, security and
privacy scanning, redaction, metadata scrubbing, risk classification, quality
gates, reconciliation and automation regression checks.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import zipfile
from datetime import date, datetime
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
F = _sdf_import("sdf_formula", "tools-sdf-formula.py")
SkillToolError = C.SkillToolError

PII_PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)\d{3}[\s.-]?\d{3,4}(?!\d)"),
    "credit_card": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    "ssn_like": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "national_id_ph": re.compile(r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)"),
    "postal_code": re.compile(r"\b\d{4,6}(?:-\d{4})?\b"),
    "dob_hint": re.compile(r"\b(?:dob|date of birth|birthdate)\b", re.I),
    "salary_hint": re.compile(r"\b(?:salary|compensation|pay rate|base pay)\b", re.I),
    "password_hint": re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\b", re.I),
}

SENSITIVE_HEADERS = {"name", "first name", "last name", "full name", "email", "phone", "mobile",
                     "address", "ssn", "national id", "tax id", "tin", "dob", "date of birth",
                     "salary", "iban", "account number", "card number", "passport", "gender",
                     "race", "religion", "health", "diagnosis", "medical"}


# ==========================================================================
# privacy / security scanning
# ==========================================================================


def privacy_scan(path, max_findings=400, sample_free=True):
    """Scan cells, headers, comments and metadata for personal/confidential data."""
    from openpyxl import load_workbook

    p = Path(path)
    wb = load_workbook(filename=str(p), data_only=True)
    findings = []
    per_sheet = {}
    for ws in wb.worksheets:
        sheet_findings = []
        headers = {}
        for cell in ws[1]:
            if isinstance(cell.value, str):
                headers[cell.column_letter] = cell.value.strip()
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str):
                    for kind, pat in PII_PATTERNS.items():
                        if kind.endswith("_hint"):
                            if pat.search(v):
                                sheet_findings.append({"kind": kind, "sheet": ws.title,
                                                       "cell": cell.coordinate, "severity": "medium",
                                                       "detail": "Header/label suggests sensitive content nearby."})
                            continue
                        m = pat.search(v)
                        if m:
                            masked = _mask(m.group(0))
                            sheet_findings.append({"kind": kind, "sheet": ws.title, "cell": cell.coordinate,
                                                   "severity": "high" if kind in {"credit_card", "ssn_like", "iban", "national_id_ph"} else "medium",
                                                   "match_masked": masked})
            if len(sheet_findings) > max_findings:
                break
        for letter, header in headers.items():
            if header.lower().strip() in SENSITIVE_HEADERS:
                sheet_findings.append({"kind": "sensitive_header", "sheet": ws.title, "column": letter,
                                       "header": header, "severity": "medium",
                                       "detail": "Column header declares a personal or confidential attribute."})
        for row in ws.iter_rows():
            for cell in row:
                if getattr(cell, "comment", None) is not None:
                    text = cell.comment.text or ""
                    if any(pat.search(text) for k, pat in PII_PATTERNS.items() if not k.endswith("_hint")):
                        sheet_findings.append({"kind": "pii_in_comment", "sheet": ws.title,
                                               "cell": cell.coordinate, "severity": "high"})
        if sheet_findings:
            per_sheet[ws.title] = len(sheet_findings)
        findings.extend(sheet_findings)
    props = wb.properties
    meta = []
    for field in ("creator", "lastModifiedBy", "title", "description", "subject", "keywords", "category"):
        val = getattr(props, field, None)
        if val:
            meta.append({"field": field, "value": str(val)[:120]})
    wb.close()
    severity_counts = {}
    for f in findings:
        severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1
    return {
        "file": p.name,
        "finding_count": len(findings),
        "findings": findings[:max_findings],
        "truncated": len(findings) > max_findings,
        "per_sheet": per_sheet,
        "severity_counts": severity_counts,
        "metadata": meta,
        "verdict": "high_risk" if severity_counts.get("high") else ("review" if findings else "clean"),
        "note": "Regex detection is indicative, not a compliance certification. Confirm findings with the data owner.",
    }


def _mask(value: str) -> str:
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def redact(path, output, columns=None, patterns=("email", "phone", "credit_card"), mode="mask",
           max_cells=100000, confirm=False):
    """Approval-gated redaction. mode: mask | remove | hash | tokenize."""
    if not confirm:
        raise SkillToolError("APPROVAL_REQUIRED", "Redaction removes or alters data and needs explicit confirmation.",
                             "Re-run with --approve after reviewing the privacy-scan output.")
    from openpyxl import load_workbook

    src = Path(path)
    out = Path(output)
    if out.exists():
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {out}.", "Choose a new --output path.")
    if src.resolve() == out.resolve():
        raise SkillToolError("UNSAFE_TARGET", "Redaction must write to a new file; the source stays untouched.",
                             "Pass a different --output.")
    wb = load_workbook(filename=str(src))
    changes = []
    token_map = {}
    target_cols = set()
    if columns:
        for ws in wb.worksheets:
            for cell in ws[1]:
                if isinstance(cell.value, str) and cell.value.strip() in columns:
                    target_cols.add((ws.title, cell.column_letter))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                if columns:
                    if (ws.title, cell.column_letter) not in target_cols:
                        continue
                    new = _apply_mode(v, mode, token_map)
                else:
                    new = v
                    for kind in patterns:
                        pat = PII_PATTERNS.get(kind)
                        if not pat:
                            continue
                        new = pat.sub(lambda m: _apply_mode(m.group(0), mode, token_map), new)
                if new != v:
                    cell.value = new
                    changes.append({"sheet": ws.title, "cell": cell.coordinate, "mode": mode,
                                    "before_len": len(v)})
                    if len(changes) > max_cells:
                        raise SkillToolError("REDACTION_LIMIT",
                                             f"Redaction touched more than {max_cells} cells.",
                                             "Narrow the scope with --columns and retry.")
    wb.security.lockStructure = True
    wb.save(str(out))
    return {"output": out.name, "changes": len(changes), "sample": changes[:200], "mode": mode,
            "columns_scoped": sorted({c for _, c in target_cols}) if columns else None,
            "tokens_created": len(token_map) if mode == "tokenize" else None,
            "warning": "Verify the redacted copy before sharing; the original file is unchanged by design."}


def _apply_mode(value, mode, token_map):
    if mode == "mask":
        return _mask(value)
    if mode == "remove":
        return None
    if mode == "hash":
        import hashlib

        return "h_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    if mode == "tokenize":
        if value not in token_map:
            token_map[value] = f"tok_{len(token_map) + 1:05d}"
        return token_map[value]
    raise SkillToolError("BAD_ARGUMENT", f"Unknown redaction mode {mode!r}.", "Use mask, remove, hash or tokenize.")


def scrub_metadata(path, output=None, remove_comments=True, remove_hidden_sheets=False, keep_properties=()):
    """Approval-gated metadata/hidden-data scrubber producing a clean copy."""
    from openpyxl import load_workbook

    src = Path(path)
    out = Path(output) if output else src.with_name(src.stem + "-clean" + src.suffix)
    if out.exists():
        raise SkillToolError("OUTPUT_EXISTS", f"Refusing to overwrite {out}.", "Choose a new --output path.")
    wb = load_workbook(filename=str(src))
    removed = {"comments": 0, "hidden_sheets": [], "defined_names": 0, "properties": []}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if remove_comments and getattr(cell, "comment", None) is not None:
                    cell.comment = None
                    removed["comments"] += 1
    if remove_hidden_sheets:
        for ws in list(wb.worksheets):
            if ws.sheet_state != "visible":
                removed["hidden_sheets"].append(ws.title)
                wb.remove(ws)
    if not keep_properties:
        props = wb.properties
        for field in ("creator", "lastModifiedBy", "description", "keywords", "subject", "category", "title"):
            if getattr(props, field, None):
                removed["properties"].append(field)
                setattr(props, field, None)
    wb.save(str(out))
    return {"output": out.name, "removed": removed,
            "note": "Cell values are preserved; only metadata, comments and optional hidden sheets are removed."}


def export_gate(path, recipients=None, allow_external_links=False, allow_hidden=True,
                allow_formulas_in_values=False, max_sensitivity="medium"):
    """Pre-release check. Returns a pass/block verdict; never sends anything."""
    from openpyxl import load_workbook

    p = Path(path)
    privacy = privacy_scan(p, max_findings=100)
    links = F_hidden = None
    wb = load_workbook(filename=str(p), data_only=False)
    hidden_sheets = [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]
    external = []
    errors = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str):
                    if v.startswith("=") and "[" in v:
                        external.append({"sheet": ws.title, "cell": cell.coordinate})
                    if v.startswith("=") and re.search(r"#(REF!|VALUE!|N/A|DIV/0!|NAME\?)", v):
                        errors.append({"sheet": ws.title, "cell": cell.coordinate, "formula": v[:120]})
    filter_state = []
    for ws in wb.worksheets:
        if ws.auto_filter and ws.auto_filter.ref:
            filter_state.append({"sheet": ws.title, "filter": ws.auto_filter.ref})
    wb.close()
    blockers = []
    warnings = []
    sensitivity = privacy["verdict"]
    rank = {"clean": 0, "review": 1, "high_risk": 2}
    if rank.get(sensitivity, 0) > rank.get(max_sensitivity, 1):
        blockers.append(f"Sensitivity {sensitivity} exceeds the allowed {max_sensitivity}.")
    if external and not allow_external_links:
        blockers.append(f"{len(external)} formulas reference external workbooks.")
    if hidden_sheets and not allow_hidden:
        blockers.append(f"Hidden sheets present: {', '.join(hidden_sheets)}")
    if errors:
        blockers.append(f"{len(errors)} formula error values present.")
    if recipients:
        for r in recipients:
            if "@" not in str(r):
                warnings.append(f"Recipient {r!r} is not an email address; confirm the distribution list.")
    if privacy["severity_counts"].get("high"):
        warnings.append("High-severity personal data detected - redact before external release.")
    if filter_state:
        warnings.append("Filters are active: the recipient may see a subset of rows and believe it is the whole dataset.")
    verdict = "blocked" if blockers else "pass_with_warnings" if warnings else "pass"
    return {"file": p.name, "verdict": verdict, "blockers": blockers, "warnings": warnings,
            "privacy_verdict": sensitivity, "hidden_sheets": hidden_sheets,
            "external_references": external[:50], "formula_errors": errors[:50],
            "active_filters": filter_state, "recipients": recipients or [],
            "policy": "A 'pass' is a technical pre-flight result. Sign-off remains with the document owner."}


# ==========================================================================
# risk classification and control register
# ==========================================================================


def risk_classify(path, purpose=None, downstream=None, regulated=False):
    from openpyxl import load_workbook

    p = Path(path)
    st = None
    try:
        import importlib.util as _u

        spec = _u.spec_from_file_location("sdf_intake_rc", Path(__file__).resolve().parent / "tools-sdf-intake.py")
        intake = _u.module_from_spec(spec)
        spec.loader.exec_module(intake)
        st = intake.structure(p)
    except Exception:
        st = None
    privacy = privacy_scan(p, max_findings=50)
    wb = load_workbook(filename=str(p), data_only=False)
    formulas = 0
    hardcoded_regions = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas += 1
    wb.close()
    score = 0
    drivers = []
    if privacy["severity_counts"].get("high"):
        score += 3
        drivers.append("high-severity personal or financial data")
    elif privacy["finding_count"]:
        score += 1
        drivers.append("personal data present")
    if regulated:
        score += 3
        drivers.append("regulated process")
    if purpose and re.search(r"(payroll|tax|audit|financial statement|regulatory|valuation|pricing)", str(purpose), re.I):
        score += 2
        drivers.append("financially consequential purpose")
    if downstream and re.search(r"(board|regulator|filing|public|investor)", str(downstream), re.I):
        score += 2
        drivers.append("high-stakes downstream use")
    if formulas > 2000:
        score += 2
        drivers.append(f"{formulas} formula cells (complex logic surface)")
    elif formulas > 300:
        score += 1
        drivers.append(f"{formulas} formula cells")
    if st and st["totals"]["hidden_sheets"]:
        score += 1
        drivers.append("hidden sheets present")
    if st and st["external_link_parts"]:
        score += 2
        drivers.append("external workbook links")
    if st and st["vba_project"]:
        score += 2
        drivers.append("VBA project embedded")
    level = "critical" if score >= 8 else "high" if score >= 5 else "medium" if score >= 3 else "low"
    controls = {
        "low": ["Version-controlled file name", "Owner recorded in the register"],
        "medium": ["Peer review of formulas", "Backup before each release", "Input validation on entry cells"],
        "high": ["Independent recalculation check", "Locked formula regions", "Documented assumptions register",
                 "Change log with approver"],
        "critical": ["Full end-user-computing control pack", "Model review sign-off", "Access restrictions",
                     "Evidence retained per retention policy", "Rollback copy archived"],
    }[level]
    return {"file": p.name, "risk_level": level, "score": score, "drivers": drivers,
            "minimum_controls": controls, "privacy_verdict": privacy["verdict"],
            "formula_cells": formulas,
            "review_frequency": {"low": "annual", "medium": "semi-annual", "high": "quarterly",
                                 "critical": "monthly or per release"}[level],
            "note": "Classification supports control decisions; it does not certify compliance."}


# ==========================================================================
# quality gate
# ==========================================================================


def quality_gate(path, spec=None):
    """Structural, formula, data, accessibility, print and security pre-delivery checks."""
    from openpyxl import load_workbook

    spec = spec or {}
    p = Path(path)
    checks = []

    def add(name, status, detail, severity="medium"):
        checks.append({"check": name, "status": status, "detail": detail, "severity": severity})

    wb = load_workbook(filename=str(p), data_only=False)
    sheets = wb.sheetnames
    # 1. empty sheets / no content
    empty = [ws.title for ws in wb.worksheets if ws.max_row <= 1 and ws.max_column <= 1
             and all(c.value is None for row in ws.iter_rows() for c in row)]
    add("no_empty_sheets", "fail" if empty else "pass",
        f"Empty sheets: {', '.join(empty)}" if empty else "All sheets contain content.", "low")
    # 2. formula errors + names
    scan = F.scan_workbook(p)
    add("formula_errors", "fail" if scan["error_count"] else "pass",
        f"{scan['error_count']} cells contain formula error values." if scan["error_count"]
        else "No formula error values found.", "high")
    add("unknown_functions", "warn" if scan["unknown_functions"] else "pass",
        f"Unrecognised functions: {', '.join(scan['unknown_functions'])}" if scan["unknown_functions"]
        else "All functions are in the known library.", "low")
    # 3. hardcodes inside formula columns
    hc = F.hardcode_scan(p)
    add("hardcoded_overrides", "warn" if hc["count"] else "pass",
        f"{hc['count']} numeric constants sit in formula columns." if hc["count"]
        else "No suspicious constants found.", "medium")
    # 4. merged cells
    merged = sum(len(list(ws.merged_cells.ranges)) for ws in wb.worksheets)
    add("merged_cells", "warn" if merged else "pass", f"{merged} merged regions present.", "low")
    # 5. accessibility basics
    a11y = []
    for ws in wb.worksheets:
        if ws.max_row > 3:
            header_cells = [c for c in ws[1] if c.value is not None]
            if not header_cells:
                a11y.append(f"{ws.title}: no header row")
        if ws.sheet_state != "visible":
            a11y.append(f"{ws.title}: hidden sheet needs a documented reason")
    add("accessibility_structure", "warn" if a11y else "pass",
        "; ".join(a11y) if a11y else "Header rows present and no undocumented hidden sheets.", "medium")
    # 6. print setup
    no_print = [ws.title for ws in wb.worksheets
                if not ws.page_setup.fitToWidth and ws.max_column > 8]
    add("print_setup", "warn" if no_print else "pass",
        f"Wide sheets without fit-to-width: {', '.join(no_print)}" if no_print else "Print setup adequate.", "low")
    # 7. protection on formula-heavy sheets
    unprotected = [ws.title for ws in wb.worksheets
                   if not ws.protection.sheet and any(isinstance(c.value, str) and c.value.startswith("=")
                                                      for row in ws.iter_rows() for c in row)]
    add("formula_protection", "warn" if unprotected else "pass",
        f"Unprotected formula sheets: {', '.join(unprotected)}" if unprotected else "Formula sheets protected.", "medium")
    # 8. external links
    ext = [ws.title for ws in wb.worksheets
           if any(isinstance(c.value, str) and c.value.startswith("=") and "[" in c.value
                  for row in ws.iter_rows() for c in row)]
    add("external_links", "warn" if ext else "pass",
        f"External references on: {', '.join(ext)}" if ext else "No external workbook references.", "medium")
    wb.close()
    # 9. privacy
    try:
        priv = privacy_scan(p, max_findings=50)
        add("privacy_scan", "warn" if priv["finding_count"] else "pass",
            f"{priv['finding_count']} potential personal/confidential items.", "high" if priv["severity_counts"].get("high") else "medium")
    except Exception as exc:
        add("privacy_scan", "skip", f"Privacy scan unavailable: {type(exc).__name__}", "low")
    # 10. required content from the caller's spec
    for req in spec.get("required_sheets") or []:
        add(f"required_sheet:{req}", "pass" if req in sheets else "fail",
            "present" if req in sheets else "missing", "high")
    for req in spec.get("required_cells") or []:
        sname = req.get("sheet")
        ref = req.get("cell")
        ok = sname in sheets and wb_value(p, sname, ref) is not None
        add(f"required_cell:{sname}!{ref}", "pass" if ok else "fail",
            "value present" if ok else "cell is empty or missing", "high")
    # 11. reconciliation if requested
    for recon in spec.get("reconciliations") or []:
        try:
            from openpyxl import load_workbook as _lw

            _wb = _lw(filename=str(p), data_only=True)
            a = _cell_value(_wb, recon["a"])
            b = _cell_value(_wb, recon["b"])
            tol = float(recon.get("tolerance", 0.005))
            ok = a is not None and b is not None and abs(float(a) - float(b)) <= tol
            add(f"reconcile:{recon.get('name', 'check')}", "pass" if ok else "fail",
                f"{recon['a']}={a} vs {recon['b']}={b} (tolerance {tol})", "high")
            _wb.close()
        except Exception as exc:
            add(f"reconcile:{recon.get('name', 'check')}", "skip", f"could not evaluate: {exc}", "low")
    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    score = max(0, 100 - sum({"high": 25, "medium": 10, "low": 4}[c["severity"]] for c in failed)
                - sum({"high": 8, "medium": 4, "low": 1}[c["severity"]] for c in warned))
    return {"file": p.name, "checks": checks, "failed": len(failed), "warnings": len(warned),
            "quality_score": score,
            "verdict": "blocked" if failed else ("pass_with_warnings" if warned else "pass"),
            "note": "Values cached by Excel are not recalculated here; recalc-sensitive checks use formula text."}


def wb_value(path, sheet, cell):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), data_only=True)
    try:
        return wb[sheet][cell].value
    finally:
        wb.close()


def _cell_value(wb, ref):
    if "!" not in ref:
        raise SkillToolError("BAD_ARGUMENT", f"Cell reference {ref!r} must look like Sheet1!B12.")
    sheet, cell = ref.rsplit("!", 1)
    sheet = sheet.strip("'")
    if sheet not in wb.sheetnames:
        raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sheet!r} not found in reconciliation reference.")
    return wb[sheet][cell].value


def reconcile(pairs, tolerance=0.005):
    """Tie out named values: [{"name","a","b","tolerance"?}] with numeric inputs supplied."""
    results = []
    for pair in pairs:
        a = C.to_number(pair.get("a"))
        b = C.to_number(pair.get("b"))
        tol = float(pair.get("tolerance", tolerance))
        if a is None or b is None:
            results.append({"name": pair.get("name"), "status": "unchecked",
                            "detail": "both sides must be numeric", "a": pair.get("a"), "b": pair.get("b")})
            continue
        diff = a - b
        results.append({"name": pair.get("name"), "a": a, "b": b, "difference": round(diff, 6),
                        "tolerance": tol, "status": "tied" if abs(diff) <= tol else "difference",
                        "material": abs(diff) > tol})
    differences = [r for r in results if r["status"] == "difference"]
    return {"results": results, "checked": len(results), "differences": len(differences),
            "verdict": "tied_out" if not differences else "differences_found",
            "note": "Every difference needs an owner and an explanation before sign-off."}


def regression_check(baseline_path, candidate_path, tolerance=0.01, sheet_limit=20):
    """Compare two workbooks for structural, formula and value regressions."""
    from openpyxl import load_workbook

    base = load_workbook(filename=str(baseline_path), data_only=False)
    cand = load_workbook(filename=str(candidate_path), data_only=False)
    issues = []
    if base.sheetnames != cand.sheetnames:
        issues.append({"kind": "sheet_set_changed", "baseline": base.sheetnames, "candidate": cand.sheetnames})
    for name in base.sheetnames[:sheet_limit]:
        if name not in cand.sheetnames:
            continue
        a, b = base[name], cand[name]
        for row in a.iter_rows():
            for cell in row:
                other = b[cell.coordinate]
                if cell.value != other.value:
                    kind = "formula_changed" if (isinstance(cell.value, str) and str(cell.value).startswith("=")) else "value_changed"
                    if isinstance(cell.value, (int, float)) and isinstance(other.value, (int, float)):
                        if cell.value and abs((other.value - cell.value) / cell.value) <= tolerance:
                            continue
                        kind = "numeric_drift"
                    issues.append({"kind": kind, "sheet": name, "cell": cell.coordinate,
                                   "baseline": cell.value if not isinstance(cell.value, str) else cell.value[:120],
                                   "candidate": other.value if not isinstance(other.value, str) else str(other.value)[:120]})
                elif cell.number_format != other.number_format:
                    issues.append({"kind": "format_changed", "sheet": name, "cell": cell.coordinate,
                                   "baseline": cell.number_format, "candidate": other.number_format})
    base.close()
    cand.close()
    counts = {}
    for i in issues:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"issues": issues[:500], "issue_count": len(issues), "by_kind": counts,
            "tolerance": tolerance,
            "verdict": "regression_detected" if issues else "no_regression",
            "note": "Run this after any automation change with a known-good baseline workbook."}


def review_pack(spec, output_dir):
    """Generate the review/approval artefacts (CSV-ready tables) for a workbook release."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    required = ["title", "owner", "reviewer", "approver", "purpose", "as_of"]
    missing = [f for f in required if not spec.get(f)]
    if missing:
        raise SkillToolError("INCOMPLETE_SPEC", f"Review pack needs: {', '.join(missing)}.",
                             "Provide owner, reviewer, approver, purpose and as-of date.")
    checklist = spec.get("checklist") or [
        "Inputs verified against source", "Formulas reviewed by a second person",
        "Control totals reconcile", "Assumptions documented", "Sensitive data reviewed",
        "Print/PDF layout checked", "Version name follows the convention",
    ]
    rows = [{"item": i + 1, "control": c, "status": "pending", "owner": spec.get("owner"),
             "evidence": ""} for i, c in enumerate(checklist)]
    table = C.make_table(["item", "control", "status", "owner", "evidence"],
                         [[r["item"], r["control"], r["status"], r["owner"], r["evidence"]] for r in rows])
    return {"table": table, "output_dir": str(out), "checklist_items": len(rows),
            "required_fields": required}


def write_table_files(table, out_dir, stem):
    """Write a table as CSV and JSON inside out_dir (used by CLI --format csv,json)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    import importlib.util as _u

    spec = _u.spec_from_file_location("sdf_intake_wt", Path(__file__).resolve().parent / "tools-sdf-intake.py")
    intake = _u.module_from_spec(spec)
    spec.loader.exec_module(intake)
    csv_path = out / f"{stem}.csv"
    json_path = out / f"{stem}.json"
    C.atomic_write_text(csv_path, intake.table_to_csv_text(table))
    C.atomic_write_json(json_path, {"columns": table["columns"], "rows": table["rows"]})
    return [str(csv_path), str(json_path)]
