#!/usr/bin/env python3
"""spreadsheet-data-finance Skill Tool - primary entry point.

Run `python3 tools-sdf.py --help` for the command list and
`python3 tools-sdf.py capabilities` for the machine-readable capability map.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sdf_import(alias, filename):
    if alias in sys.modules:
        return sys.modules[alias]
    path = HERE / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


C = _sdf_import("sdf_common", "tools-sdf-common.py")
SkillToolError = C.SkillToolError

MODULES = {
    "intake": "tools-sdf-intake.py",
    "formula": "tools-sdf-formula.py",
    "data": "tools-sdf-data.py",
    "stats": "tools-sdf-stats.py",
    "finance": "tools-sdf-finance.py",
    "workbook": "tools-sdf-workbook.py",
    "viz": "tools-sdf-viz.py",
    "qa": "tools-sdf-qa.py",
}

_loaded = {}


def mod(name):
    if name not in _loaded:
        _loaded[name] = _sdf_import(f"sdf_{name}", MODULES[name])
    return _loaded[name]


ASSET_DIR = HERE


def asset_json(name):
    path = ASSET_DIR / name
    if not path.exists():
        raise SkillToolError("MISSING_ASSET", f"Asset file not found next to the tool: {path.name}",
                             "Restore the asset file into the skill-tool folder.")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------


def emit(result, args, tables=None):
    """Print the structured result and optionally persist it plus any tables."""
    saved = []
    embedded = result.get("data", {}).pop("_tables", None)
    if embedded:
        tables = {**(tables or {}), **embedded}
    if args.save_result:
        p = C.output_path(args.save_result, "result.json", overwrite=True)
        C.atomic_write_json(p, result)
        saved.append(str(p))
    if tables and args.export:
        out_dir = Path(args.out_dir or ".")
        stem = args.export_stem or result.get("operation", "table")
        for name, table in tables.items():
            suffixes = [s.strip().lower() for s in args.export.split(",") if s.strip()]
            for suffix in suffixes:
                if suffix == "csv":
                    path = out_dir / f"{stem}-{name}.csv"
                    C.atomic_write_text(path, mod("intake").table_to_csv_text(table))
                    saved.append(str(path))
                elif suffix == "json":
                    path = out_dir / f"{stem}-{name}.json"
                    C.atomic_write_json(path, {"columns": table["columns"], "rows": table["rows"]})
                    saved.append(str(path))
                elif suffix == "md":
                    path = out_dir / f"{stem}-{name}.md"
                    C.atomic_write_text(path, table_to_markdown(table))
                    saved.append(str(path))
                else:
                    raise SkillToolError("BAD_ARGUMENT", f"Unsupported export format {suffix!r}.",
                                         "Use csv, json or md.")
    if saved:
        result.setdefault("meta", {})["saved"] = saved
    if args.output_format == "text":
        print(render_text(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def table_to_markdown(table, limit=40):
    cols = table["columns"]
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for row in table["rows"][:limit]:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    if len(table["rows"]) > limit:
        lines.append(f"\n_({len(table['rows']) - limit} more rows omitted)_")
    return "\n".join(lines) + "\n"


def render_text(result):
    head = f"[{result['status']}] {result['operation']}"
    parts = [head]
    if result.get("errors"):
        for e in result["errors"]:
            parts.append(f"  error {e.get('code')}: {e.get('message')}")
            if e.get("hint"):
                parts.append(f"  hint: {e['hint']}")
    data = result.get("data") or {}
    for key, value in data.items():
        if isinstance(value, dict) and "columns" in value and "rows" in value:
            parts.append(f"  {key}:")
            parts.append("    " + table_to_markdown(value, limit=15).replace("\n", "\n    ").rstrip())
        elif isinstance(value, (dict, list)):
            parts.append(f"  {key}: {json.dumps(value, default=str)[:1200]}")
        else:
            parts.append(f"  {key}: {value}")
    for w in result.get("warnings") or []:
        parts.append(f"  ! {w}")
    return "\n".join(parts)


def load_table(args, path_attr="input"):
    path = getattr(args, path_attr, None)
    p = C.input_path(path)
    table, warnings = mod("intake").read_table(
        p,
        sheet=getattr(args, "sheet", None),
        delimiter=getattr(args, "delimiter", None),
        encoding=getattr(args, "encoding", None),
        header=not getattr(args, "no_header", False),
        decimal=getattr(args, "decimal", "."),
        thousands=getattr(args, "thousands", ","),
        html_index=getattr(args, "table_index", 0),
        max_rows=getattr(args, "max_rows", None),
    )
    return table, warnings


def csv_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


def json_arg(value, default=None):
    if value is None:
        return default
    return C.load_json_arg(value)


def ok_data(operation, data, warnings=None, meta=None):
    return C.ok(operation, data, warnings, meta)


# --------------------------------------------------------------------------
# command implementations
# --------------------------------------------------------------------------

CAPABILITIES = None


def cmd_capabilities(args):
    global CAPABILITIES
    if CAPABILITIES is None:
        CAPABILITIES = asset_json("asset-capabilities.json")
    cats = CAPABILITIES["categories"]
    if args.category:
        needle = args.category.lower()
        cats = [c for c in cats if needle in c["category"].lower() or needle == str(c["index"])]
        if not cats:
            raise SkillToolError("UNKNOWN_CATEGORY", f"No category matched {args.category!r}.",
                                 "Run 'capabilities' without --category to list all 34 categories.")
    if args.command_name:
        needle = args.command_name.lower()
        out = []
        for cat in cats:
            hits = [s for s in cat["skills"] if needle in s["command"].lower() or needle in s["name"].lower()]
            if hits:
                out.append({"category": cat["category"], "skills": hits})
        if not out:
            raise SkillToolError("UNKNOWN_CAPABILITY", f"No capability matched {args.command_name!r}.")
        return ok_data("capabilities", {"matches": out})
    summary = {
        "tool": C.TOOL_NAME,
        "version": C.VERSION,
        "category_count": len(cats),
        "skill_count": sum(len(c["skills"]) for c in cats),
        "commands": sorted({s["command"] for c in CAPABILITIES["categories"] for s in c["skills"]}),
    }
    if args.full:
        summary["categories"] = cats
    else:
        summary["categories"] = [{"index": c["index"], "category": c["category"],
                                  "skills": len(c["skills"]),
                                  "commands": sorted({s["command"] for s in c["skills"]})} for c in cats]
    return ok_data("capabilities", summary,
                   meta={"note": "Every listed command is runnable in this build; see knowledge-readme.md for limits."})


def cmd_selftest(args):
    """Run the packaged verification suite (formula library + numeric invariants)."""
    checks = []

    def check(name, passed, detail=None):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    suite = asset_json("asset-formula-library.json")["suite"]
    for case in suite:
        try:
            got = mod("formula").evaluate_formula(case["formula"], cells=case.get("cells") or {})
            value = got["value"]
            expected = case["expect"]
            ok = (abs(float(value) - float(expected)) < 1e-6
                  if isinstance(expected, (int, float)) and isinstance(value, (int, float))
                  else value == expected)
            check(f"formula:{case['id']}", ok, {"expected": expected, "actual": value})
        except SkillToolError as exc:
            check(f"formula:{case['id']}", False, exc.message)
    try:
        jc = mod("finance").journal_check([
            {"reference": "J1", "date": "2025-01-01", "account": "1100", "debit": 100, "credit": 0},
            {"reference": "J1", "date": "2025-01-01", "account": "4000", "debit": 0, "credit": 100},
        ])
        check("finance:journal_balances", jc["balanced"] and jc["problem_count"] == 0)
        bad = mod("finance").journal_check([
            {"reference": "J2", "date": "2025-01-01", "account": "1100", "debit": 100, "credit": 0},
            {"reference": "J2", "date": "2025-01-01", "account": "4000", "debit": 0, "credit": 90},
        ])
        check("finance:journal_detects_imbalance", not bad["balanced"] and bad["problem_count"] >= 1)
    except SkillToolError as exc:
        check("finance:journal_rules", False, exc.message)
    try:
        loan = mod("finance").loan_schedule(100000, 0.06, 12)
        check("finance:loan_amortises_to_zero", abs(loan["final_balance"]) < 0.01, loan["final_balance"])
        alloc = mod("data").allocate(1000, [1, 1, 1])
        check("data:allocation_exact", sum(alloc["allocated"]) == 1000, alloc["allocated"])
        j = mod("data").join(C.make_table(["k", "v"], [["a", 1], ["b", 2]]),
                             C.make_table(["k", "w"], [["a", 9]]), ["k"], ["k"], how="left")
        check("data:left_join_keeps_rows", len(j["table"]["rows"]) == 2, j["stats"])
        prof = mod("data").profile(C.make_table(["id", "n"], [["0012", 1], ["0013", 2]]))
        check("data:identifier_not_numeric",
              all(c["inferred_type"] != "integer" for c in prof["columns_profile"] if c["name"] == "id"))
    except SkillToolError as exc:
        check("data:invariants", False, exc.message)
    try:
        t = mod("stats").t_test([1, 2, 3, 4, 5], [2, 3, 4, 5, 6])
        check("stats:t_test_runs", t["p_value"] is not None and t["df"] > 0)
        r = mod("stats").regress(C.make_table(["x", "y"], [[1, 2], [2, 4.1], [3, 5.9], [4, 8.2], [5, 9.8]]), "y", ["x"])
        check("stats:regression_recovers_slope", abs(r["coefficients"][1]["coefficient"] - 1.96) < 0.1,
              r["coefficients"][1]["coefficient"])
    except SkillToolError as exc:
        check("stats:invariants", False, exc.message)
    try:
        svg = mod("viz").svg_chart(C.make_table(["k", "v"], [["a", 1], ["b", 2]]), kind="column",
                                   x="k", y="v", title="self test")
        check("viz:svg_renders", svg.startswith("<svg") and "<rect" in svg)
    except SkillToolError as exc:
        check("viz:svg_renders", False, exc.message)
    try:
        parsed = mod("intake").parse_catalog("1. Sample Category\n1. Skill Name: Thing One Description: Does a thing.")
        check("intake:catalog_parser", parsed["skill_count"] == 1 and parsed["skill_names_ok"] is not False
              if False else parsed["skill_count"] == 1, parsed["skill_count"])
    except SkillToolError as exc:
        check("intake:catalog_parser", False, exc.message)
    passed = sum(1 for c in checks if c["passed"])
    return ok_data("selftest", {"total": len(checks), "passed": passed, "failed": len(checks) - passed,
                                "checks": checks,
                                "verdict": "pass" if passed == len(checks) else "fail"})


def cmd_catalog_parse(args):
    raw = args.text or ""
    if args.input:
        p = C.input_path(args.input)
        raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        raise SkillToolError("MISSING_INPUT", "No catalog text supplied.",
                             "Pass --input <file.md|.html|.txt> or --text '...'.")
    parsed = mod("intake").parse_catalog(raw)
    if parsed["skill_count"] == 0:
        raise SkillToolError("NO_SKILLS_FOUND", "No 'Skill Name:' entries were recognised.",
                             "Check that descriptions follow the 'Skill Name: X Description: Y' pattern.")
    return ok_data("catalog-parse", parsed,
                   meta={"detected_categories": parsed["category_count"]})


def cmd_plan(args):
    """Turn a request/catalog into an executable build plan mapped to commands."""
    source_text = args.text or ""
    if args.input:
        source_text = C.input_path(args.input).read_text(encoding="utf-8", errors="replace")
    if not source_text.strip():
        raise SkillToolError("MISSING_INPUT", "Pass --text or --input describing the work.")
    cats = asset_json("asset-capabilities.json")["categories"]
    if args.catalog:
        parsed = mod("intake").parse_catalog(source_text)
        names = [s["name"].lower() for s in parsed["skills"]]
        cats_match = [c for c in cats
                      if any(any(n in s["name"].lower() for n in names) for s in c["skills"])]
    else:
        tokens = [t.strip().lower() for t in source_text.replace("\n", ",").split(",") if t.strip()]
        cats_match = [c for c in cats
                      if any(tok in c["category"].lower() or any(tok in s["name"].lower() for s in c["skills"])
                             for tok in tokens)]
    if not cats_match:
        cats_match = [c for c in cats if c["index"] in (1, 2, 30)]
        note = "No direct category match; defaulting to intake, authoring and QA stages."
    else:
        note = None
    stages = []
    for i, cat in enumerate(cats_match, start=1):
        commands = sorted({s["command"] for s in cat["skills"]})
        stages.append({"stage": i, "category": cat["category"], "commands": commands,
                       "purpose": cat.get("purpose")})
    plan = {"stages": stages, "stage_count": len(stages),
            "stage_commands": ["build", "qa-gate", "export-gate"],
            "sequence": [c for s in stages for c in s["commands"]][:40],
            "note": note or "Plan stages follow catalog order; review before execution."}
    return ok_data("plan", plan)


def cmd_read(args):
    table, warnings = load_table(args)
    preview = {**table, "rows": table["rows"][: args.preview]}
    return ok_data("read", {"columns": table["columns"], "row_count": len(table["rows"]),
                            "preview": preview, "meta": table.get("meta", {})}, warnings)


def cmd_dialect(args):
    p = C.input_path(args.input)
    raw = p.read_bytes()
    text, enc, warnings = mod("intake").decode_bytes(raw)
    dialect = mod("intake").sniff_dialect(text)
    enc_info = mod("intake").inspect_encoding(raw)
    return ok_data("dialect", {"dialect": dialect, "encoding": enc_info,
                               "first_rows": text.splitlines()[:5]}, warnings)


def cmd_encoding(args):
    p = C.input_path(args.input)
    info = mod("intake").inspect_encoding(p.read_bytes())
    return ok_data("encoding", info, info.get("warnings"))


def cmd_structure(args):
    p = C.input_path(args.input)
    data = mod("intake").structure(p)
    return ok_data("structure", data)


def cmd_compare(args):
    a = C.input_path(args.baseline)
    b = C.input_path(args.candidate)
    data = mod("intake").compare_workbooks(a, b, sample_limit=args.limit)
    return ok_data("compare", data)


def cmd_triage(args):
    data = mod("intake").triage(C.input_path(args.input))
    return ok_data("triage", data)


def cmd_links(args):
    data = mod("intake").external_links(C.input_path(args.input))
    return ok_data("links", data, data.get("risk_notes"))


def cmd_hidden(args):
    data = mod("intake").hidden_content(C.input_path(args.input))
    return ok_data("hidden", data)


def cmd_manifest(args):
    paths = [C.input_path(p, must_exist=True) for p in args.inputs]
    data = mod("intake").build_manifest(paths, include_hashes=not args.no_hash)
    return ok_data("manifest", data, data.get("warnings"))


def cmd_convert(args):
    src = C.input_path(args.input)
    out = C.output_path(args.output, "converted.xlsx", overwrite=args.overwrite)
    data = mod("intake").convert_workbook(src, out)
    data["output"] = out.name
    return ok_data("convert", data, data.get("warnings"))


def cmd_flatten(args):
    if not args.input:
        raise SkillToolError("MISSING_INPUT", "flatten needs --input with .json/.jsonl/.xml.")
    table, warnings = load_table(args)
    return ok_data("flatten", {"columns": table["columns"], "row_count": len(table["rows"]),
                               "preview": {**table, "rows": table["rows"][: args.preview]}}, warnings)


# ---- authoring -----------------------------------------------------------


def cmd_build(args):
    spec = json_arg(args.spec)
    if not spec:
        raise SkillToolError("MISSING_INPUT", "build needs --spec (JSON file or inline JSON).",
                             'Example: --spec {"title":"Budget","sheets":[{"name":"Inputs","columns":["Item","Amount"]}]}')
    out = C.output_path(args.output, "workbook.xlsx", overwrite=args.overwrite)
    theme = json_arg(args.theme, None)
    data = mod("workbook").build_workbook(out, spec, overwrite=args.overwrite, theme=theme)
    return ok_data("build", data, data.get("notes"))


def cmd_set_cells(args):
    edits = json_arg(args.edits)
    if not edits:
        raise SkillToolError("MISSING_INPUT", "set-cells needs --edits JSON.",
                             'Example: --edits [{"sheet":"Inputs","cell":"B2","value":100}]')
    data = mod("workbook").set_cells(C.input_path(args.input), edits, output=args.output,
                                     overwrite=args.overwrite)
    warnings = [f"edit problem: {p}" for p in data.get("problems", [])]
    return ok_data("set-cells", data, warnings)


def cmd_bulk(args):
    rules = json_arg(args.rules)
    if not rules:
        raise SkillToolError("MISSING_INPUT", "bulk needs --rules JSON list.",
                             'Example: --rules [{"action":"trim","columns":["Name"]}]')
    data = mod("workbook").bulk_transform(C.input_path(args.input), rules, output=args.output,
                                          dry_run=not args.apply, overwrite=args.overwrite)
    return ok_data("bulk", data, meta={"dry_run": data["dry_run"],
                                       "approve_required": "pass --apply --approve to write changes"})


def cmd_layout(args):
    spec = json_arg(args.spec) or {}
    data = mod("workbook").apply_layout(C.input_path(args.input), spec, output=args.output)
    return ok_data("layout", data)


def cmd_cond_format(args):
    rules = json_arg(args.rules)
    if not rules:
        raise SkillToolError("MISSING_INPUT", "cond-format needs --rules JSON list.")
    data = mod("workbook").apply_conditional_format(C.input_path(args.input), rules,
                                                    output=args.output, overwrite=args.overwrite)
    return ok_data("cond-format", data)


def cmd_validations(args):
    rules = json_arg(args.rules)
    if not rules:
        raise SkillToolError("MISSING_INPUT", "validations needs --rules JSON list.")
    data = mod("workbook").add_validations(C.input_path(args.input), rules, output=args.output)
    return ok_data("validations", data)


def cmd_protect(args):
    spec = json_arg(args.spec) or {}
    data = mod("workbook").protect_workbook(C.input_path(args.input), spec, output=args.output)
    return ok_data("protect", data)


def cmd_print_setup(args):
    spec = json_arg(args.spec) or {}
    data = mod("workbook").setup_print(C.input_path(args.input), spec, output=args.output)
    return ok_data("print-setup", data)


def cmd_nav(args):
    data = mod("workbook").build_navigation(C.input_path(args.input), output=args.output)
    return ok_data("nav", data)


def cmd_merge_audit(args):
    data = mod("workbook").merge_audit(C.input_path(args.input))
    return ok_data("merge-audit", data)


def cmd_sort(args):
    data = mod("workbook").sort_range(C.input_path(args.input), args.sheet, args.range,
                                      csv_list(args.keys), has_header=not args.no_header,
                                      output=args.output, descending=args.descending)
    return ok_data("sort", data)


def cmd_number_format(args):
    pattern = C.money_pattern(args.currency, args.decimals,
                              negative="parens" if args.parens else "minus",
                              zero=args.zero)
    if args.pattern:
        pattern = args.pattern
    data = {"pattern": pattern, "example_1000_5": _format_example(1000.5, pattern),
            "example_negative": _format_example(-1234.5, pattern),
            "example_zero": _format_example(0, pattern),
            "guidance": ["Use a separate section for negatives and zeros so sign errors are visible",
                         "Avoid 'General' for money: it hides cents and sign conventions",
                         "Keep unit suffixes in the header, not in the number format, for calculability"]}
    if args.apply_to:
        rules = [{"action": "number_format", "sheets": csv_list(args.sheets) or None,
                  "columns": csv_list(args.columns), "pattern": pattern}]
        data["applied"] = mod("workbook").bulk_transform(C.input_path(args.apply_to), rules,
                                                         output=args.output, dry_run=not args.apply)
    return ok_data("number-format", data)


def _format_example(value, pattern):
    """Approximate an Excel format for a sample value (documentation only)."""
    positive, negative, zero = (pattern.split(";") + ["", ""])[:3]
    section = positive if value > 0 else negative if value < 0 else zero
    if not section:
        section = positive
    decimals = len(section.split(".")[1].split(";")[0]) if "." in section else 0
    decimals = len([c for c in section.split(".")[-1] if c in "0#"]) if "." in section else 0
    body = abs(value)
    text = f"{body:,.{decimals}f}"
    symbol = ""
    if '"' in section:
        symbol = section.split('"')[1]
    return ("-" if value < 0 and "(" not in negative else "") + symbol + text + \
        (")" if value < 0 and "(" in negative else "")


def cmd_chart(args):
    table, warnings = load_table(args)
    palette = json_arg(args.palette, None)
    if palette is None:
        pal = asset_json("asset-chart-palettes.json")
        palette = pal["palettes"].get(args.palette_name, pal["palettes"]["corporate"])
    svg = mod("viz").svg_chart(table, kind=args.kind, x=args.x, y=args.y, title=args.title,
                               subtitle=args.subtitle, width=args.width, height=args.height,
                               palette=palette, show_values=not args.no_values,
                               x_label=args.x_label, y_label=args.y_label, footnote=args.footnote)
    out = C.output_path(args.output, "chart.svg", overwrite=args.overwrite)
    C.atomic_write_text(out, svg)
    return ok_data("chart", {"output": out.name, "kind": args.kind, "bytes": len(svg),
                             "categories": len(table["rows"])}, warnings)


def cmd_chart_advice(args):
    data = mod("viz").chart_selection_advisor(args.intent, categories=args.categories,
                                              series=args.series, has_time=args.has_time)
    return ok_data("chart-advice", data)


def cmd_viz_audit(args):
    spec = json_arg(args.spec)
    if not spec:
        raise SkillToolError("MISSING_INPUT", "viz-audit needs --spec JSON.",
                             'Example: --spec {"kind":"column","title":"Revenue","y_axis_min":500}')
    data = mod("viz").viz_audit(spec)
    return ok_data("viz-audit", data)


def cmd_dashboard(args):
    spec = json_arg(args.spec)
    if not spec:
        raise SkillToolError("MISSING_INPUT", "dashboard needs --spec JSON with kpis and data.",
                             'Example: --spec {"kpis":[{"label":"Revenue","value":1200000}],'
                             '"data":[{"name":"By Region","path":"sales.csv","chart":{"kind":"column","x":"Region","y":"Revenue"}}]}')
    kpis = spec.get("kpis") or []
    data_sheets = []
    for entry in spec.get("data") or []:
        if entry.get("path"):
            p = C.input_path(entry["path"])
            table, _ = mod("intake").read_table(p, sheet=entry.get("sheet"))
            n = entry.get("limit")
            if n:
                table["rows"] = table["rows"][: int(n)]
        elif entry.get("table"):
            table = C.make_table(entry["table"]["columns"], entry["table"]["rows"])
        else:
            raise SkillToolError("BAD_SPEC", "Each data entry needs 'path' or 'table'.")
        data_sheets.append({"name": entry.get("name", "Data"), "table": table, "chart": entry.get("chart")})
    out = C.output_path(args.output, "dashboard.xlsx", overwrite=args.overwrite)
    data = mod("viz").build_dashboard(out, kpis, data_sheets, title=spec.get("title", "Dashboard"),
                                      palette=spec.get("palette"))
    return ok_data("dashboard", data)


# ---- formula -------------------------------------------------------------


def cmd_formula_eval(args):
    cells = json_arg(args.cells, {}) or {}
    names = json_arg(args.names, {}) or {}
    if args.input:
        table, warnings = load_table(args)
        mapped = {}
        for ri, row in enumerate(table["rows"], start=2):
            for ci, val in enumerate(row, start=1):
                if val is not None:
                    mapped[f"{_col(ci)}{ri}"] = val
        cells = {**mapped, **{k.replace("$", "").upper(): v for k, v in cells.items()}}
    data = mod("formula").evaluate_formula(args.formula, cells=cells, names=names)
    return ok_data("eval", data, data.get("warnings"))


def _col(n):
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def cmd_formula_explain(args):
    names = json_arg(args.names, {}) or {}
    data = mod("formula").explain_formula(args.formula, names=names)
    return ok_data("explain", data)


def cmd_formula_deps(args):
    names = json_arg(args.names, None)
    data = mod("formula").dependencies(args.formula, names=names)
    dependents = None
    if args.input:
        scan = mod("formula").scan_workbook(C.input_path(args.input))
        targets = set(data["cells"])
        dependents = []
        from openpyxl import load_workbook

        wb = load_workbook(filename=str(C.input_path(args.input)), data_only=False)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith("="):
                        try:
                            deps = mod("formula").dependencies(v)
                        except SkillToolError:
                            continue
                        if targets & set(deps["cells"]):
                            dependents.append({"sheet": ws.title, "cell": cell.coordinate, "formula": v[:160]})
        wb.close()
    data.pop("ast", None)
    if dependents is not None:
        data["dependents"] = dependents[:200]
        data["dependent_count"] = len(dependents)
    return ok_data("deps", data)


def cmd_scan(args):
    data = mod("formula").scan_workbook(C.input_path(args.input), max_findings=args.limit)
    return ok_data("scan", data)


def cmd_hardcode(args):
    p = C.input_path(args.input)
    data = mod("formula").hardcode_scan(p, max_findings=args.limit)
    data["formula_pattern_issues"] = mod("formula").diff_formula_patterns(p)
    return ok_data("hardcode", data)


# ---- data ---------------------------------------------------------------


def cmd_profile(args):
    table, warnings = load_table(args)
    data = mod("data").profile(table, top_n=args.top, sample_values=args.samples)
    cols = ["name", "inferred_type", "non_empty", "missing", "missing_pct", "distinct", "unique_candidate"]
    rows = [[c["name"], c["inferred_type"], c["non_empty"], c["missing"], c["missing_pct"],
             c["distinct"], c["unique_candidate"]] for c in data["columns_profile"]]
    data["_tables"] = {"column-profile": C.make_table(cols, rows)}
    return ok_data("profile", data, warnings)


def cmd_clean(args):
    table, warnings = load_table(args)
    options = json_arg(args.options) or {}
    for kv in args.type or []:
        col, _, kind = kv.partition("=")
        options.setdefault("types", {})[col.strip()] = kind.strip()
    if args.fill:
        options.setdefault("fill_missing", {}).update(json_arg(args.fill))
    if args.dedupe:
        options["dedupe"] = True
        if args.dedupe_keys:
            options["dedupe_keys"] = csv_list(args.dedupe_keys)
    result = mod("data").clean(table, options)
    return ok_data("clean", {"preview": _preview(result["table"], args.preview),
                             "columns": result["table"]["columns"],
                             "change_count": result["change_count"],
                             "changes": result["changes"][:200],
                             "steps": result["steps"],
                             "rows_before": result["rows_before"],
                             "rows_after": result["rows_after"],
                             "_tables": {"cleaned": result["table"]}},
                   warnings, meta={"rows_after": result["rows_after"]})


def _preview(table, n):
    return {**table, "rows": table["rows"][: n]}


def cmd_dedupe(args):
    table, warnings = load_table(args)
    keys = csv_list(args.keys) or table["columns"]
    data = mod("data").dedupe_scores(table, keys, fuzzy_threshold=args.fuzzy)
    return ok_data("dedupe", data, warnings)


def cmd_join(args):
    left, w1 = load_table(args)
    args.input = args.right
    right, w2 = load_table(args)
    lkeys = csv_list(args.left_keys)
    rkeys = csv_list(args.right_keys)
    if not lkeys:
        raise SkillToolError("MISSING_INPUT", "join needs --left-keys.", "Name the join key column(s).")
    data = mod("data").join(left, right, lkeys, rkeys or lkeys, how=args.how)
    return ok_data("join", {"stats": data["stats"], "columns": data["table"]["columns"],
                            "preview": _preview(data["table"], args.preview),
                            "_tables": {"joined": data["table"]}},
                   w1 + w2 + data["warnings"])


def cmd_pivot(args):
    table, warnings = load_table(args)
    out = mod("data").pivot(table, csv_list(args.index), args.columns, args.values, agg=args.agg)
    return ok_data("pivot", {"columns": out["columns"], "row_count": len(out["rows"]),
                             "preview": _preview(out, args.preview), "_tables": {"pivot": out}},
                   warnings)


def cmd_unpivot(args):
    table, warnings = load_table(args)
    out = mod("data").unpivot(table, csv_list(args.id_columns), csv_list(args.value_columns) or None,
                              var_name=args.var_name, value_name=args.value_name)
    return ok_data("unpivot", {"columns": out["columns"], "row_count": len(out["rows"]),
                               "preview": _preview(out, args.preview), "_tables": {"unpivoted": out}},
                   warnings)


def cmd_aggregate(args):
    table, warnings = load_table(args)
    measures = json_arg(args.measures)
    if not measures:
        raise SkillToolError("MISSING_INPUT", "aggregate needs --measures JSON.",
                             'Example: --measures [{"column":"amount","agg":"sum"}]')
    out = mod("data").aggregate(table, csv_list(args.group_by), measures)
    return ok_data("aggregate", {"columns": out["columns"], "row_count": len(out["rows"]),
                                 "preview": _preview(out, args.preview), "_tables": {"aggregated": out}},
                   warnings)


def cmd_window(args):
    table, warnings = load_table(args)
    fns = json_arg(args.functions) or csv_list(args.functions_csv) or ["running_total"]
    out = mod("data").window(table, args.column, fns, order_by=args.order_by,
                             partition_by=csv_list(args.partition_by) or None,
                             date_column=args.date_column)
    return ok_data("window", {"columns": out["columns"], "row_count": len(out["rows"]),
                              "preview": _preview(out, args.preview), "_tables": {"windowed": out}},
                   warnings)


def cmd_resample(args):
    table, warnings = load_table(args)
    out = mod("data").resample(table, args.date_column, args.value_column, freq=args.freq, agg=args.agg)
    return ok_data("resample", {"columns": out["columns"], "row_count": len(out["rows"]),
                                "preview": _preview(out, args.preview), "_tables": {"resampled": out}},
                   warnings)


def cmd_sample(args):
    table, warnings = load_table(args)
    data = mod("data").sample(table, n=args.size, frac=args.frac, method=args.method,
                              stratify_by=args.stratify_by, seed=args.seed,
                              review_column=args.review_column)
    return ok_data("sample", {"sampled_rows": data["sampled_rows"], "method": data["method"],
                              "seed": data["seed"], "columns": data["table"]["columns"],
                              "preview": _preview(data["table"], args.preview),
                              "_tables": {"sample": data["table"]}},
                   warnings + ([data["note"]] if data.get("note") else []))


def cmd_crosswalk(args):
    table, warnings = load_table(args)
    mapping = json_arg(args.mapping)
    if not mapping:
        raise SkillToolError("MISSING_INPUT", "crosswalk needs --mapping JSON (source value -> target value).")
    data = mod("data").crosswalk(table, args.column, mapping, target_name=args.target_name,
                                 strict=args.strict)
    return ok_data("crosswalk", {"unmapped_count": data["unmapped_count"],
                                 "unmapped_top": dict(list(data["unmapped"].items())[:25]),
                                 "mapping_size": data["mapping_size"],
                                 "columns": data["table"]["columns"],
                                 "preview": _preview(data["table"], args.preview),
                                 "_tables": {"mapped": data["table"]}}, warnings)


def cmd_allocate(args):
    table, warnings = load_table(args)
    out = mod("data").allocate_table(table, args.amount_column, csv_list(args.drivers),
                                     group_by=csv_list(args.group_by) or None,
                                     rounding=args.rounding)
    return ok_data("allocate", {"columns": out["columns"], "row_count": len(out["rows"]),
                                "preview": _preview(out, args.preview),
                                "_tables": {"allocated": out}}, warnings)


def cmd_outliers(args):
    table, warnings = load_table(args)
    data = mod("data").outliers(table, csv_list(args.columns) or None, method=args.method,
                                threshold=args.threshold, sd_threshold=args.sd)
    return ok_data("outliers", data, warnings)


def cmd_enforce(args):
    table, warnings = load_table(args)
    schema = json_arg(args.schema)
    if not schema:
        raise SkillToolError("MISSING_INPUT", "enforce needs --schema JSON {column: type}.")
    data = mod("data").type_enforcement(table, schema)
    return ok_data("enforce", {"exception_count": data["exception_count"],
                               "exceptions": data["exceptions"][:50],
                               "columns": data["table"]["columns"],
                               "preview": _preview(data["table"], args.preview),
                               "_tables": {"enforced": data["table"]}}, warnings)


# ---- statistics ---------------------------------------------------------


def cmd_describe(args):
    table, warnings = load_table(args)
    data = mod("stats").describe(table, csv_list(args.columns) or None, group_by=args.group_by)
    return ok_data("describe", data, warnings)


def cmd_correlate(args):
    table, warnings = load_table(args)
    data = mod("stats").correlate(table, args.x, args.y, method=args.method)
    return ok_data("correlate", data, warnings)


def cmd_corrmatrix(args):
    table, warnings = load_table(args)
    data = mod("stats").correlation_matrix(table, csv_list(args.columns) or None)
    return ok_data("corrmatrix", data, warnings)


def cmd_regress(args):
    table, warnings = load_table(args)
    data = mod("stats").regress(table, args.y, csv_list(args.predictors))
    return ok_data("regress", data, warnings)


def cmd_ttest(args):
    table, warnings = load_table(args)
    ci = C.column_index(table, args.column)
    if args.group_column:
        gi = C.column_index(table, args.group_column)
        groups = {}
        for row in table["rows"]:
            groups.setdefault(str(row[gi]), []).append(C.to_number(row[ci]))
        if len(groups) < 2:
            raise SkillToolError("INSUFFICIENT_DATA", "Need two groups for a two-sample test.",
                                 "Check the values in --group-column.")
        keys = sorted(groups)[:2]
        data = mod("stats").t_test(groups[keys[0]], groups[keys[1]], alpha=args.alpha)
        data["groups"] = keys
    else:
        data = mod("stats").t_test([C.to_number(r[ci]) for r in table["rows"]], None,
                                   mu=args.mu, alpha=args.alpha)
    return ok_data("ttest", data, warnings)


def cmd_proportions(args):
    data = mod("stats").proportion_test(args.successes_a, args.n_a, args.successes_b, args.n_b,
                                        alpha=args.alpha)
    return ok_data("proportions", data)


def cmd_chisquare(args):
    table, warnings = load_table(args)
    data = mod("stats").chi_square(table, args.row_column, args.column_column, alpha=args.alpha)
    return ok_data("chisquare", data, warnings + data.get("warnings", []))


def cmd_abtest(args):
    data = mod("stats").ab_test(args.visitors_a, args.conversions_a, args.visitors_b,
                                args.conversions_b, alpha=args.alpha)
    return ok_data("abtest", data)


def cmd_samplesize(args):
    data = mod("stats").sample_size(effect_size=args.effect_size, power=args.power, alpha=args.alpha,
                                    baseline_rate=args.baseline_rate, mde=args.mde,
                                    finite_population=args.population)
    return ok_data("samplesize", data)


def cmd_control_chart(args):
    table, warnings = load_table(args)
    ci = C.column_index(table, args.column)
    data = mod("stats").control_chart([C.to_number(r[ci]) for r in table["rows"]],
                                      subgroup=args.subgroup, sigma_rule=args.sigma,
                                      label=args.column)
    return ok_data("control-chart", data, warnings)


def cmd_montecarlo(args):
    variables = json_arg(args.variables)
    data = mod("stats").monte_carlo(iterations=args.iterations, seed=args.seed,
                                    variables=variables, formula=args.formula, buckets=args.buckets)
    return ok_data("montecarlo", data)


def cmd_forecast(args):
    variables = json_arg(args.values) if args.values else None
    if variables is None and args.input:
        table, warnings = load_table(args)
        ci = C.column_index(table, args.column)
        variables = [C.to_number(r[ci]) for r in table["rows"]]
    else:
        warnings = []
    if not variables:
        raise SkillToolError("MISSING_INPUT", "forecast needs --input with --column, or --values JSON list.")
    data = mod("stats").forecast(variables, periods=args.periods, method=args.method,
                                 seasonality=args.seasonality)
    return ok_data("forecast", data, warnings)


def cmd_accuracy(args):
    data = mod("stats").forecast_accuracy(json_arg(args.actuals), json_arg(args.predictions))
    return ok_data("accuracy", data)


def cmd_rfm(args):
    table, warnings = load_table(args)
    data = mod("stats").rfm(table, args.customer_column, args.date_column, args.amount_column,
                            as_of=args.as_of, quintiles=args.quintiles)
    rows = data.pop("rows")
    data["top_rows"] = rows[:50]
    return ok_data("rfm", data, warnings)


def cmd_cohort(args):
    table, warnings = load_table(args)
    data = mod("stats").cohort(table, args.customer_column, args.date_column,
                               value_column=args.value_column, period=args.period,
                               max_periods=args.max_periods)
    table_out = data.pop("table")
    data["_tables"] = {"cohort": table_out}
    return ok_data("cohort", data, warnings)


def cmd_basket(args):
    table, warnings = load_table(args)
    data = mod("stats").basket(table, args.transaction_column, args.item_column,
                               min_support=args.min_support)
    return ok_data("basket", data, warnings)


def cmd_scorecard(args):
    table, warnings = load_table(args)
    weights = json_arg(args.weights)
    if not weights:
        raise SkillToolError("MISSING_INPUT", "scorecard needs --weights JSON {column: weight}.")
    data = mod("stats").score_records(table, weights, output_column=args.output_column,
                                      transform=args.transform)
    return ok_data("scorecard", {"weights": data["weights"],
                                 "columns": data["table"]["columns"],
                                 "preview": _preview(data["table"], args.preview),
                                 "note": data["note"],
                                 "_tables": {"scored": data["table"]}}, warnings)


def cmd_assumptions(args):
    table, warnings = load_table(args)
    data = mod("stats").assumption_audit(table, csv_list(args.columns) or table["columns"])
    return ok_data("assumptions", data, warnings)


# ---- finance ------------------------------------------------------------


def cmd_coa(args):
    accounts = json_arg(args.accounts)
    if accounts is None and args.input:
        table, warnings = load_table(args)
        accounts = [dict(zip(table["columns"], row)) for row in table["rows"]]
    else:
        warnings = []
    if not accounts:
        raise SkillToolError("MISSING_INPUT", "coa needs --accounts JSON or --input CSV with code,name,type.")
    data = mod("finance").validate_coa(accounts)
    return ok_data("coa", data, warnings)


def cmd_journal_check(args):
    table, warnings = load_table(args)
    entries = [dict(zip(table["columns"], row)) for row in table["rows"]]
    data = mod("finance").journal_check(entries, tolerance=args.tolerance)
    return ok_data("journal-check", data, warnings)


def cmd_trial_balance(args):
    table, warnings = load_table(args)
    entries = [dict(zip(table["columns"], row)) for row in table["rows"]]
    accounts = json_arg(args.accounts)
    if accounts is None and args.coa:
        coa_table, _ = mod("intake").read_table(C.input_path(args.coa))
        accounts = [dict(zip(coa_table["columns"], r)) for r in coa_table["rows"]]
    data = mod("finance").trial_balance(entries, accounts=accounts, as_of=args.as_of,
                                        opening=json_arg(args.opening, {}) or {})
    return ok_data("trial-balance", data, warnings)


def cmd_gl_summary(args):
    table, warnings = load_table(args)
    entries = [dict(zip(table["columns"], row)) for row in table["rows"]]
    accounts = None
    if args.coa:
        coa_table, _ = mod("intake").read_table(C.input_path(args.coa))
        accounts = [dict(zip(coa_table["columns"], r)) for r in coa_table["rows"]]
    data = mod("finance").gl_summary(entries, group_by=csv_list(args.group_by) or ("account",),
                                     sign_by_type=args.sign_by_type, accounts=accounts)
    return ok_data("gl-summary", {"columns": data["table"]["columns"],
                                  "row_count": len(data["table"]["rows"]),
                                  "periods": data["periods"],
                                  "preview": _preview(data["table"], args.preview),
                                  "sign_policy": data["sign_policy"],
                                  "_tables": {"gl_summary": data["table"]}}, warnings)


def cmd_income_statement(args):
    table, warnings = load_table(args)
    tb_rows = [dict(zip(table["columns"], row)) for row in table["rows"]]
    mapping = json_arg(args.mapping)
    if not mapping:
        raise SkillToolError("MISSING_INPUT", "income-statement needs --mapping JSON.",
                             'Example: --mapping {"Revenue":{"group":"revenue","accounts":["4000","4*"]}}')
    data = mod("finance").income_statement(tb_rows, mapping)
    return ok_data("income-statement", data, warnings)


def cmd_balance_sheet(args):
    table, warnings = load_table(args)
    tb_rows = [dict(zip(table["columns"], row)) for row in table["rows"]]
    mapping = json_arg(args.mapping)
    if not mapping:
        raise SkillToolError("MISSING_INPUT", "balance-sheet needs --mapping JSON.")
    data = mod("finance").balance_sheet(tb_rows, mapping)
    return ok_data("balance-sheet", data, warnings)


def cmd_cash_flow(args):
    spec = json_arg(args.inputs)
    if not spec:
        raise SkillToolError("MISSING_INPUT", "cash-flow needs --inputs JSON",
                             'Example: --inputs {"net_profit":100,"depreciation_amortization":20}')
    data = mod("finance").cash_flow(spec)
    return ok_data("cash-flow", data)


def cmd_variance(args):
    actual, w1 = load_table(args)
    args.input = args.budget
    budget, w2 = load_table(args)
    a_rows = [dict(zip(actual["columns"], r)) for r in actual["rows"]]
    b_rows = [dict(zip(budget["columns"], r)) for r in budget["rows"]]
    data = mod("finance").variance(a_rows, b_rows, key=args.key,
                                   higher_is_better=not args.lower_is_better)
    return ok_data("variance", data, w1 + w2)


def cmd_drivers(args):
    drivers = json_arg(args.drivers)
    bases = json_arg(args.base_values)
    if not drivers or not bases:
        raise SkillToolError("MISSING_INPUT", "drivers needs --drivers and --base-values JSON.",
                             'Example: --drivers {"Revenue":["units","price"]} '
                             '--base-values {"units":1000,"price":25,"units_growth":0.03}')
    data = mod("finance").driver_forecast(drivers, bases, periods=args.periods)
    return ok_data("drivers", {"columns": data["table"]["columns"],
                               "row_count": len(data["table"]["rows"]),
                               "preview": _preview(data["table"], args.preview),
                               "assumptions": data["assumptions"],
                               "_tables": {"projection": data["table"]}})


def cmd_dcf(args):
    data = mod("finance").dcf(json_arg(args.fcf), args.rate,
                              terminal_growth=args.terminal_growth,
                              terminal_multiple=args.terminal_multiple,
                              mid_year=args.mid_year, net_debt=args.net_debt,
                              shares=args.shares)
    return ok_data("dcf", data)


def cmd_loan(args):
    data = mod("finance").loan_schedule(args.principal, args.rate, args.periods,
                                        periods_per_year=args.frequency,
                                        payment=args.payment, extra_payment=args.extra,
                                        start_date=args.start_date)
    return ok_data("loan", {"columns": data["table"]["columns"],
                            "row_count": len(data["table"]["rows"]),
                            "preview": _preview(data["table"], args.preview),
                            "periodic_payment": data["periodic_payment"],
                            "total_interest": data["total_interest"],
                            "final_balance": data["final_balance"],
                            "policy": data["policy"]})


def cmd_lease(args):
    data = mod("finance").lease_schedule(json_arg(args.payments), args.rate,
                                         initial_liability=args.liability,
                                         start_date=args.start_date, periodicity=args.periodicity)
    return ok_data("lease", {"columns": data["table"]["columns"],
                             "row_count": len(data["table"]["rows"]),
                             "preview": _preview(data["table"], args.preview),
                             "_tables": {"schedule": data["table"]},
                             "total_interest": data["total_interest"],
                             "closing_liability": data["closing_liability"], "policy": data["policy"]})


def cmd_depreciation(args):
    data = mod("finance").depreciation(args.cost, salvage=args.salvage, life_years=args.life,
                                       method=args.method, periods_per_year=args.frequency,
                                       rate=args.declining_rate)
    return ok_data("depreciation", data)


def cmd_aging(args):
    table, warnings = load_table(args)
    rows = [dict(zip(table["columns"], r)) for r in table["rows"]]
    data = mod("finance").aging(rows, as_of=args.as_of, amount_column=args.amount_column,
                                due_column=args.due_column, party_column=args.party_column,
                                bucket_days=tuple(int(b) for b in csv_list(args.buckets)) or (0, 30, 60, 90),
                                paid_column=args.paid_column or None)
    return ok_data("aging", {"columns": data["table"]["columns"],
                             "preview": _preview(data["table"], args.preview),
                             "buckets": data["buckets"], "totals": data["totals"],
                             "as_of": data["as_of"], "skipped_rows": data["skipped_rows"],
                             "policy": data["policy"]}, warnings)


def cmd_dup_invoices(args):
    table, warnings = load_table(args)
    rows = [dict(zip(table["columns"], r)) for r in table["rows"]]
    data = mod("finance").duplicate_invoices(rows, amount_column=args.amount_column,
                                             vendor_column=args.vendor_column,
                                             date_column=args.date_column,
                                             number_column=args.number_column)
    return ok_data("dup-invoices", data, warnings)


def cmd_po_match(args):
    pos, w1 = load_table(args)
    args.input = args.invoices
    invoices, w2 = load_table(args)
    receipts = None
    w3 = []
    if args.receipts:
        args.input = args.receipts
        rt, w3 = load_table(args)
        receipts = [dict(zip(rt["columns"], r)) for r in rt["rows"]]
    data = mod("finance").po_match([dict(zip(pos["columns"], r)) for r in pos["rows"]],
                                   [dict(zip(invoices["columns"], r)) for r in invoices["rows"]],
                                   receipts=receipts, price_tolerance=args.price_tolerance,
                                   qty_tolerance=args.qty_tolerance)
    return ok_data("po-match", data, w1 + w2 + w3)


def cmd_vat(args):
    data = mod("finance").vat_reconciliation(args.output_tax, args.input_tax,
                                             ledger_tax=args.ledger_tax, sales_amount=args.sales,
                                             rate=args.rate, tolerance=args.tolerance)
    return ok_data("vat-recon", data)


def cmd_bank_recon(args):
    bank, w1 = load_table(args)
    args.input = args.ledger
    ledger, w2 = load_table(args)
    data = mod("finance").bank_reconciliation([dict(zip(bank["columns"], r)) for r in bank["rows"]],
                                              [dict(zip(ledger["columns"], r)) for r in ledger["rows"]],
                                              amount_column=args.amount_column,
                                              date_column=args.date_column,
                                              ref_column=args.ref_column,
                                              date_window_days=args.window)
    return ok_data("bank-recon", data, w1 + w2)


def cmd_portfolio(args):
    values = json_arg(args.values) or []
    flows = json_arg(args.flows) if args.flows else None
    bench = json_arg(args.benchmark) if args.benchmark else None
    data = mod("finance").portfolio_performance(values, cash_flows=flows, benchmark=bench)
    return ok_data("portfolio", data)


def cmd_risk_metrics(args):
    data = mod("finance").risk_metrics(json_arg(args.returns), risk_free=args.risk_free,
                                       periods_per_year=args.frequency,
                                       benchmark_returns=json_arg(args.benchmark) if args.benchmark else None)
    return ok_data("risk-metrics", data)


def cmd_debt(args):
    data = mod("finance").debt_schedule(args.principal, args.rate, args.periods,
                                        periods_per_year=args.frequency,
                                        amortising=not args.interest_only,
                                        grace_periods=args.grace)
    return ok_data("debt-schedule", {"columns": ["period", "opening", "interest", "principal", "cash_due", "closing"],
                                     "row_count": len(data["rows"]), "rows": data["rows"][:60],
                                     "periodic_payment": data["periodic_payment"],
                                     "total_interest": data["total_interest"],
                                     "policy": data["policy"]})


def cmd_ratios(args):
    data = mod("finance").financial_ratios(json_arg(args.income) or {}, json_arg(args.balance) or {})
    return ok_data("ratios", data)


def cmd_kpi(args):
    data = mod("finance").kpi_evaluate(args.actual, args.target, tolerance=args.tolerance,
                                       direction=args.direction, name=args.name)
    return ok_data("kpi", data)


def cmd_revenue_recognition(args):
    data = mod("finance").revenue_recognition(args.total, args.periods, method=args.method,
                                              milestones=json_arg(args.milestones) if args.milestones else None)
    return ok_data("revenue-recognition", {"rows": data["rows"], "total": data["total"],
                                           "method": data["method"], "policy": data["policy"]})


# ---- governance ---------------------------------------------------------


def cmd_qa_gate(args):
    spec = json_arg(args.spec) or {}
    data = mod("qa").quality_gate(C.input_path(args.input), spec)
    return ok_data("qa-gate", data)


def cmd_privacy(args):
    data = mod("qa").privacy_scan(C.input_path(args.input), max_findings=args.limit)
    return ok_data("privacy-scan", data)


def cmd_redact(args):
    data = mod("qa").redact(C.input_path(args.input), args.output,
                            columns=csv_list(args.columns) or None,
                            patterns=csv_list(args.patterns) or ("email", "phone", "credit_card"),
                            mode=args.mode, confirm=args.approve)
    return ok_data("redact", data)


def cmd_scrub(args):
    data = mod("qa").scrub_metadata(C.input_path(args.input), output=args.output,
                                    remove_comments=not args.keep_comments,
                                    remove_hidden_sheets=args.remove_hidden_sheets)
    return ok_data("scrub", data)


def cmd_export_gate(args):
    data = mod("qa").export_gate(C.input_path(args.input), recipients=csv_list(args.recipients) or None,
                                 allow_external_links=args.allow_external_links,
                                 allow_hidden=not args.block_hidden,
                                 max_sensitivity=args.max_sensitivity)
    return ok_data("export-gate", data)


def cmd_risk(args):
    data = mod("qa").risk_classify(C.input_path(args.input), purpose=args.purpose,
                                   downstream=args.downstream, regulated=args.regulated)
    return ok_data("risk-classify", data)


def cmd_reconcile(args):
    pairs = json_arg(args.pairs)
    if not pairs:
        raise SkillToolError("MISSING_INPUT", "reconcile needs --pairs JSON.",
                             'Example: --pairs [{"name":"TB","a":1000,"b":1000}]')
    data = mod("qa").reconcile(pairs, tolerance=args.tolerance)
    return ok_data("reconcile", data)


def cmd_regression(args):
    data = mod("qa").regression_check(C.input_path(args.baseline), C.input_path(args.candidate),
                                      tolerance=args.tolerance)
    return ok_data("regression", data)


def cmd_review_pack(args):
    spec = json_arg(args.spec)
    if not spec:
        raise SkillToolError("MISSING_INPUT", "review-pack needs --spec JSON with owner/reviewer/approver.")
    data = mod("qa").review_pack(spec, args.out_dir or ".")
    table = data.pop("table")
    return ok_data("review-pack", {**data, "columns": table["columns"],
                                   "preview": _preview(table, args.preview),
                                   "_tables": {"checklist": table}})


# ---- workflow -----------------------------------------------------------


def cmd_batch(args):
    ops = json_arg(args.ops)
    if not ops:
        raise SkillToolError("MISSING_INPUT", "batch needs --ops JSON list of {command, args}.",
                             'Example: --ops [{"command":"profile","args":{"input":"data.csv"}}]')
    results = []
    backups = []
    started = time.time()
    if not args.dry_run:
        for op in ops:
            if op.get("backup") and op.get("args", {}).get("input"):
                try:
                    src = C.input_path(op["args"]["input"])
                    dst = src.with_suffix(src.suffix + f".bak-{int(time.time())}")
                    shutil.copy2(src, dst)
                    backups.append(str(dst))
                except SkillToolError:
                    pass
    for i, op in enumerate(ops, start=1):
        name = op.get("command")
        op_args = dict(op.get("args") or {})
        if name not in COMMANDS:
            results.append({"step": i, "command": name, "status": "error",
                            "error": f"unknown command {name!r}"})
            if not args.keep_going:
                break
            continue
        if args.dry_run:
            results.append({"step": i, "command": name, "status": "planned", "args": op_args})
            continue
        try:
            ns = build_parser().parse_args(_argv_for(name, op_args))
            result = COMMANDS[name](ns)
            results.append({"step": i, "command": name, "status": result["status"],
                            "errors": result.get("errors"), "warnings": result.get("warnings")})
            if result["status"] == "error" and not args.keep_going:
                break
        except SkillToolError as exc:
            results.append({"step": i, "command": name, "status": "error", "errors": [exc.as_dict()]})
            if not args.keep_going:
                break
    total = len(results)
    errors = sum(1 for r in results if r["status"] == "error")
    return ok_data("batch", {"dry_run": args.dry_run, "steps": total, "errors": errors,
                             "results": results, "backups": backups,
                             "elapsed_s": round(time.time() - started, 3)},
                   meta={"policy": "Every step is validated before execution; sources are backed up on request."})


def _argv_for(command, op_args):
    argv = [command]
    for key, value in op_args.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        elif isinstance(value, (list, dict)):
            argv.extend([flag, json.dumps(value)])
        elif value is not None:
            argv.extend([flag, str(value)])
    return argv


def cmd_task_plan(args):
    request = args.text or ""
    if args.input:
        request = C.input_path(args.input).read_text(encoding="utf-8", errors="replace")
    if not request.strip():
        raise SkillToolError("MISSING_INPUT", "task-plan needs --text or --input describing the job.")
    stages = []
    req = request.lower()
    if any(w in req for w in ["csv", "xlsx", "workbook", "import", "receive", "upload", "data"]):
        stages.append({"stage": "intake", "commands": ["read", "dialect", "structure", "manifest"],
                       "exit_criteria": "source inventory and parse rules confirmed"})
    if any(w in req for w in ["clean", "duplicate", "messy", "missing", "standard"]):
        stages.append({"stage": "prepare", "commands": ["profile", "clean", "dedupe", "enforce"],
                       "exit_criteria": "exceptions queue reviewed and typed"})
    if any(w in req for w in ["join", "merge", "combine", "map", "lookup"]):
        stages.append({"stage": "transform", "commands": ["join", "crosswalk", "aggregate", "pivot"],
                       "exit_criteria": "row counts and totals reconcile"})
    if any(w in req for w in ["model", "forecast", "budget", "statement", "valuation", "finance", "cash"]):
        stages.append({"stage": "model", "commands": ["drivers", "variance", "income-statement", "dcf"],
                       "exit_criteria": "assumptions registered and control totals pass"})
    if any(w in req for w in ["chart", "dashboard", "visual", "kpi"]):
        stages.append({"stage": "visualise", "commands": ["chart-advice", "chart", "dashboard", "viz-audit"],
                       "exit_criteria": "chart integrity audit passes"})
    if any(w in req for w in ["review", "audit", "release", "share", "distribute", "publish"]):
        stages.append({"stage": "assure", "commands": ["qa-gate", "privacy-scan", "export-gate"],
                       "exit_criteria": "no blockers from export-gate"})
    if not stages:
        stages = [
            {"stage": "intake", "commands": ["read", "profile"], "exit_criteria": "data understood"},
            {"stage": "build", "commands": ["build"], "exit_criteria": "workbook spec implemented"},
            {"stage": "assure", "commands": ["qa-gate", "export-gate"], "exit_criteria": "gates pass"},
        ]
    return ok_data("task-plan", {"request": request.strip()[:400], "stages": stages,
                                 "stage_count": len(stages),
                                 "note": "Approval is required before any mutating command in the plan."})


def cmd_assist_build(args):
    """Turn a plain-language request into a review-ready workbook scaffold."""
    request = args.text or ""
    if args.input:
        request = C.input_path(args.input).read_text(encoding="utf-8", errors="replace")
    if not request.strip():
        raise SkillToolError("MISSING_INPUT", "assist-build needs --text describing the workbook.")
    lines = [l.strip(" -*•\t") for l in request.replace(";", "\n").split("\n") if l.strip()]
    items = []
    for line in lines:
        for part in line.split(","):
            p = part.strip(" .")
            if p:
                items.append(p[:60])
    rates = []
    for m in __import__("re").finditer(r"(\d+(?:\.\d+)?)\s*%", request):
        rates.append(float(m.group(1)) / 100.0)
    title = (lines[0][:80] if lines else "Generated workbook")
    spec = {
        "title": title,
        "nav": True,
        "print": {"orientation": "landscape", "fit_width": 1, "repeat_rows": "1:1"},
        "properties": {"title": title, "creator": "spreadsheet-data-finance assistant",
                       "description": "Draft scaffold generated from a plain-language request. Review every formula."},
        "sheets": [
            {"name": "Instructions", "title": title, "role": "doc",
             "intro": "Draft generated by assist-build. Review assumptions, formulas and controls before use.",
             "rows": [["Source request", request.strip()[:500]],
                      ["Generated by", f"spreadsheet-data-finance {C.VERSION}"],
                      ["Status", "DRAFT - requires human review and sign-off"],
                      ["Approval required", "Yes: this scaffold changes files and must be reviewed before release"]]},
            {"name": "Inputs", "title": "Inputs (blue cells are user inputs)",
             "intro": "Enter one row per line item. Do not type into calculation sheets.",
             "columns": ["Line item", "Amount", "Growth rate", "Notes"],
             "rows": [[it, None, rates[0] if rates else None, ""] for it in items] or [["Item 1", None, None, ""]],
             "number_formats": {"B": "#,##0.00", "C": "0.0%"},
             "validations": [{"type": "decimal", "operator": "greaterThanOrEqual", "formula1": "0",
                              "range": f"B2:B{max(2, len(items) + 1)}",
                              "error_title": "Amount must be positive",
                              "error": "Enter a positive amount, or leave blank.",
                              "prompt_title": "Amount",
                              "prompt": "Enter the value in the reporting currency."}],
             "freeze": "A2"},
            {"name": "Calculations", "title": "Projection and controls",
             "columns": ["Line item", "Base", "Projected (1 period)", "Share of total"],
             "rows": [[it, f"=Inputs!B{i + 2}",
                       f"=Inputs!B{i + 2}*(1+IFERROR(Inputs!C{i + 2},0))",
                       f"=IFERROR(Inputs!B{i + 2}/SUM(Inputs!B$2:B${max(2, len(items) + 1)}),0)"]
                      for i, it in enumerate(items or ["Item 1"])],
             "number_formats": {"B": "#,##0.00", "C": "#,##0.00", "D": "0.0%"},
             "cells": [{"cell": f"B{len(items or ['Item 1']) + 3}", "formula": f"=SUM(B2:B{len(items or ['Item 1']) + 1})",
                        "role": "total", "number_format": "#,##0.00"},
                       {"cell": f"C{len(items or ['Item 1']) + 3}", "formula": f"=SUM(C2:C{len(items or ['Item 1']) + 1})",
                        "role": "total", "number_format": "#,##0.00"},
                       {"cell": f"A{len(items or ['Item 1']) + 3}", "value": "Total", "role": "total"}]},
            {"name": "Assumptions", "title": "Assumption register",
             "columns": ["Assumption", "Value", "Source", "Owner", "Review date"],
             "rows": [["Growth rate applied uniformly", f"{rates[0]:.2%}" if rates else "not stated", "request text", "", ""],
                      ["Currency", "not stated", "request text", "", ""],
                      ["Period length", "1 period (verify)", "generated default", "", ""]]},
            {"name": "Checks", "title": "Control checks",
             "columns": ["Check", "Result", "Status"],
             "rows": [["Inputs total equals calculation total",
                       f"=IF(ROUND(SUM(Inputs!B2:B{max(2, len(items) + 1)})-"
                       f"Calculations!B{len(items or ['Item 1']) + 3},2)=0,\"OK\",\"BREAK\")", "computed"],
                      ["No blank input amounts",
                       f"=IF(COUNTBLANK(Inputs!B2:B{max(2, len(items) + 1)})>0,\"review\",\"OK\")", "computed"]]},
        ],
    }
    out = C.output_path(args.output, "generated-workbook.xlsx", overwrite=args.overwrite)
    data = mod("workbook").build_workbook(out, spec, overwrite=args.overwrite)
    return ok_data("assist-build", {"file": data["file"], "sheets": data["sheets"],
                                   "line_items": len(items or ["Item 1"]),
                                   "spec": spec,
                                   "status": "DRAFT",
                                   "review_required": ["Every formula", "Assumption register",
                                                       "Currency and period length",
                                                       "Approval before distribution"]})


def cmd_narrate(args):
    table, warnings = load_table(args)
    if not table["rows"]:
        raise SkillToolError("EMPTY_INPUT", "Nothing to narrate: the table has no rows.")
    findings = []
    measure = args.measure
    if measure is None:
        numeric = [c for c in table["columns"] if _numeric_candidates(table, c)]
        if not numeric:
            raise SkillToolError("NO_NUMERIC_COLUMNS", "narrate needs a numeric measure column.",
                                 "Pass --measure.")
        measure = numeric[0]
    mi = C.column_index(table, measure)
    values = [(str(r[0]), C.to_number(r[mi])) for r in table["rows"]]
    values = [(k, v) for k, v in values if v is not None]
    total = sum(v for _, v in values)
    findings.append({"finding": "total", "value": round(total, 2), "measure": measure,
                     "statement": f"{measure} totals {total:,.2f} across {len(values)} rows."})
    if values:
        top = max(values, key=lambda kv: kv[1])
        share = C.safe_div(top[1], total) if total else None
        findings.append({"finding": "largest_contributor", "label": top[0], "value": round(top[1], 2),
                         "share": share,
                         "statement": f"{top[0]} is the largest contributor at {top[1]:,.2f}"
                                      + (f" ({share:.1%} of total)." if share else ".")})
        if share and share > 0.5:
            findings.append({"finding": "concentration", "share": share,
                             "statement": f"Concentration risk: a single row drives {share:.1%} of the total."})
        if args.order_column:
            oi = C.column_index(table, args.order_column)
            ordered = [(C.parse_date(r[oi]), C.to_number(r[mi])) for r in table["rows"]]
            ordered = [(d, v) for d, v in ordered if d and v is not None]
            ordered.sort()
            if len(ordered) >= 2:
                first, last = ordered[0][1], ordered[-1][1]
                change = C.safe_div(last - first, abs(first)) if first else None
                findings.append({"finding": "trend", "from": round(first, 2), "to": round(last, 2),
                                 "change": change,
                                 "statement": f"{measure} moved from {first:,.2f} to {last:,.2f}"
                                              + (f" ({change:+.1%})." if change is not None else ".")})
    total_sum = total
    findings.append({"finding": "grounding", "rows_used": len(values),
                     "statement": "Every statement above is computed from the supplied file; no external facts were added."})
    return ok_data("narrate", {"measure": measure, "findings": findings,
                               "narrative": " ".join(f["statement"] for f in findings)}, warnings)


def _numeric_candidates(table, name):
    ci = C.column_index(table, name)
    vals = [r[ci] for r in table["rows"] if not C.is_empty(r[ci])]
    if not vals:
        return False
    return sum(1 for v in vals if C.to_number(v) is not None) / len(vals) >= 0.8


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="tools-sdf.py",
        description="spreadsheet-data-finance Skill Tool: intake, authoring, editing, formula analysis, data "
                    "preparation, statistics, finance, visualisation, QA and governance for spreadsheets.",
        epilog="Every command supports --output-format text|json, --save-result and dry-run where it mutates files. "
               "Run 'capabilities' for the command-to-catalog map.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"{C.TOOL_NAME} {C.VERSION}")
    p.add_argument("--output-format", choices=["json", "text"], default="json")
    p.add_argument("--save-result", help="Write the structured result JSON to this path.")
    p.add_argument("--approve", action="store_true",
                   help="Confirm a mutating/irreversible action (see knowledge-readme.md approval rules).")
    p.add_argument("--audit-log", help="Append the action to this audit log (default ./sdf-audit.jsonl).")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def add(name, func, help_text, **kwargs):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(func=func)
        common_out(sp)
        for arg, kw in kwargs.items():
            sp.add_argument(*kw.pop("flags"), **kw)
        return sp

    def common_out(sp):
        # repeated on each subcommand so flags work before or after the command name
        sp.add_argument("--output-format", choices=["json", "text"], default=argparse.SUPPRESS)
        sp.add_argument("--save-result", default=argparse.SUPPRESS)
        sp.add_argument("--approve", action="store_true", default=argparse.SUPPRESS)
        sp.add_argument("--audit-log", default=argparse.SUPPRESS)
        sp.add_argument("--export", help="Comma list of formats to write table output: csv,json,md")
        sp.add_argument("--export-stem", help="Filename stem for exported tables.")
        sp.add_argument("--out-dir", help="Directory for exported tables.")
        sp.add_argument("--preview", type=int, default=15, help="Rows of preview in the structured output.")
        sp.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output file.")

    def table_in(sp, required=True, multi=False):
        sp.add_argument("--input", required=required, help="Input file (.csv/.tsv/.xlsx/.json/.html/.xml).")
        sp.add_argument("--sheet", help="Sheet name for workbook input.")
        sp.add_argument("--delimiter", help="Field delimiter for text input (auto-detected when omitted).")
        sp.add_argument("--encoding", help="Force text encoding (auto-detected when omitted).")
        sp.add_argument("--no-header", action="store_true", help="Treat the first row as data.")
        sp.add_argument("--decimal", default=".", help="Decimal separator in the source (default '.').")
        sp.add_argument("--thousands", default=",", help="Thousands separator in the source (default ',').")
        sp.add_argument("--table-index", type=int, default=0, help="HTML table index (0-based).")
        sp.add_argument("--max-rows", type=int, help="Stop reading after N rows.")

    # meta
    sp = add("capabilities", cmd_capabilities, "List capabilities/commands and the catalog mapping.")
    sp.add_argument("--full", action="store_true", help="Include every capability entry.")
    sp.add_argument("--category", help="Filter by category name or index.")
    sp.add_argument("--command-name", help="Filter by command or capability name.")
    sp = add("selftest", cmd_selftest, "Run the packaged verification suite and report pass/fail.")
    sp = add("catalog-parse", cmd_catalog_parse, "Parse a Skill catalog (Markdown/HTML/plain) into JSON.")
    sp.add_argument("--input")
    sp.add_argument("--text")
    sp = add("plan", cmd_plan, "Map a request or catalog to an executable stage/command plan.")
    sp.add_argument("--input")
    sp.add_argument("--text")
    sp.add_argument("--catalog", action="store_true", help="Interpret --input as a Skill catalog.")

    # intake
    sp = add("read", cmd_read, "Read any supported source into a normalised table.")
    table_in(sp)
    sp = add("dialect", cmd_dialect, "Infer delimiter, quoting, header and encoding before importing.")
    sp.add_argument("--input", required=True)
    sp = add("encoding", cmd_encoding, "Report encoding, BOM, line endings and decode warnings.")
    sp.add_argument("--input", required=True)
    sp = add("structure", cmd_structure, "Inventory sheets, formulas, names, objects, links and protection.")
    sp.add_argument("--input", required=True)
    sp = add("compare", cmd_compare, "Compare two workbook versions at cell/formula/format level.")
    sp.add_argument("--baseline", required=True)
    sp.add_argument("--candidate", required=True)
    sp.add_argument("--limit", type=int, default=200)
    sp = add("triage", cmd_triage, "Diagnose a damaged workbook and produce a repair plan.")
    sp.add_argument("--input", required=True)
    sp = add("links", cmd_links, "Audit external links, URLs and connected sources.")
    sp.add_argument("--input", required=True)
    sp = add("hidden", cmd_hidden, "Reveal hidden sheets, rows, columns, names and comments.")
    sp.add_argument("--input", required=True)
    sp = add("manifest", cmd_manifest, "Build a reproducible import manifest with hashes and parse rules.")
    sp.add_argument("--inputs", nargs="+", required=True, help="One or more files or directories.")
    sp.add_argument("--no-hash", action="store_true")
    sp = add("convert", cmd_convert, "Convert legacy/plain formats into .xlsx (approval-gated write).")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output", required=True)
    sp = add("flatten", cmd_flatten, "Flatten nested JSON/XML into relational rows.")
    table_in(sp)

    # authoring
    sp = add("build", cmd_build, "Build a workbook from a JSON specification.")
    sp.add_argument("--spec", required=True, help="JSON spec file or inline JSON.")
    sp.add_argument("--output", required=True)
    sp.add_argument("--theme", help="JSON theme overrides (ink, accent, fills).")
    sp = add("set-cells", cmd_set_cells, "Edit specific cells, formulas, formats and comments.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--edits", required=True)
    sp.add_argument("--output")
    sp = add("bulk", cmd_bulk, "Apply auditable rules across ranges/columns with a dry-run preview.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--rules", required=True)
    sp.add_argument("--output")
    sp.add_argument("--apply", action="store_true", help="Write the changes (default is dry-run).")
    sp = add("layout", cmd_layout, "Set widths, heights, freeze, wrapping, grouping and hidden rows.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--spec", required=True)
    sp.add_argument("--output")
    sp = add("cond-format", cmd_cond_format, "Add priority-aware conditional formatting rules.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--rules", required=True)
    sp.add_argument("--output")
    sp = add("validations", cmd_validations, "Add data validation rules with prompts and error messages.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--rules", required=True)
    sp.add_argument("--output")
    sp = add("protect", cmd_protect, "Protect sheets/structure and unlock controlled input ranges (approval-gated).")
    sp.add_argument("--input", required=True)
    sp.add_argument("--spec")
    sp.add_argument("--output")
    sp = add("print-setup", cmd_print_setup, "Configure page size, orientation, scaling and repeating headers.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--spec")
    sp.add_argument("--output")
    sp = add("nav", cmd_nav, "Build an Index sheet with hyperlinks and return links.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output")
    sp = add("merge-audit", cmd_merge_audit, "Report merged regions and safe remediation options.")
    sp.add_argument("--input", required=True)
    sp = add("sort", cmd_sort, "Sort a range while refusing formula/merge-bearing ranges (approval-gated).")
    sp.add_argument("--input", required=True)
    sp.add_argument("--sheet", required=True)
    sp.add_argument("--range", required=True, help="Cell range such as A1:D50.")
    sp.add_argument("--keys", required=True, help="Comma list of header names or column numbers.")
    sp.add_argument("--descending", action="store_true")
    sp.add_argument("--no-header", action="store_true")
    sp.add_argument("--output")
    sp = add("number-format", cmd_number_format, "Design a number-format pattern and optionally apply it.")
    sp.add_argument("--currency", default="USD")
    sp.add_argument("--decimals", type=int, default=2)
    sp.add_argument("--parens", action="store_true", help="Show negatives in parentheses.")
    sp.add_argument("--zero", default="dash", choices=["dash", "zero", "full"])
    sp.add_argument("--pattern", help="Explicit Excel format pattern (overrides the generated one).")
    sp.add_argument("--apply-to", help="Workbook to apply the pattern to.")
    sp.add_argument("--sheets", help="Comma list of sheets to apply to.")
    sp.add_argument("--columns", help="Comma list of columns to apply to.")
    sp.add_argument("--output")
    sp.add_argument("--apply", action="store_true")
    sp = add("chart", cmd_chart, "Render a publication-ready SVG chart from tabular data.")
    table_in(sp)
    sp.add_argument("--kind", default="column",
                    help="column|bar|stacked_column|line|area|scatter|pie|donut|waterfall")
    sp.add_argument("--x", help="Category column.")
    sp.add_argument("--y", help="Value column(s); join multiple with '-'.")
    sp.add_argument("--title", default="")
    sp.add_argument("--subtitle", default="")
    sp.add_argument("--x-label", default="")
    sp.add_argument("--y-label", default="")
    sp.add_argument("--footnote", default="")
    sp.add_argument("--width", type=int, default=920)
    sp.add_argument("--height", type=int, default=520)
    sp.add_argument("--palette", help="Inline JSON array of hex colours.")
    sp.add_argument("--palette-name", default="corporate")
    sp.add_argument("--no-values", action="store_true")
    sp.add_argument("--output", required=True)
    sp = add("chart-advice", cmd_chart_advice, "Recommend a chart form for a stated analytical intent.")
    sp.add_argument("--intent", required=True)
    sp.add_argument("--categories", type=int)
    sp.add_argument("--series", type=int, default=1)
    sp.add_argument("--has-time", action="store_true")
    sp = add("viz-audit", cmd_viz_audit, "Audit a chart/visual spec for misleading or inaccessible design.")
    sp.add_argument("--spec", required=True)
    sp = add("dashboard", cmd_dashboard, "Build an Excel dashboard with KPI cards and native charts.")
    sp.add_argument("--spec", required=True)
    sp.add_argument("--output", required=True)

    # formula
    sp = add("eval", cmd_formula_eval, "Evaluate a formula in a sandboxed engine.")
    sp.add_argument("--formula", required=True)
    sp.add_argument("--input", help="Optional workbook/table used to populate cell values.")
    sp.add_argument("--sheet")
    sp.add_argument("--cells", help="JSON {cell: value} overrides.")
    sp.add_argument("--names", help="JSON {name: ref-or-value} defined names.")
    sp = add("explain", cmd_formula_explain, "Explain a formula step by step with risk notes.")
    sp.add_argument("--formula", required=True)
    sp.add_argument("--names")
    sp = add("deps", cmd_formula_deps, "List precedent cells and, for a workbook, dependent formulas.")
    sp.add_argument("--formula", required=True)
    sp.add_argument("--input")
    sp.add_argument("--names")
    sp = add("scan", cmd_scan, "Scan a workbook for formula errors, externals, volatility and names.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--limit", type=int, default=200)
    sp = add("hardcode", cmd_hardcode, "Detect constants inside formula columns and inconsistent patterns.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--limit", type=int, default=200)

    # data
    sp = add("profile", cmd_profile, "Profile columns: types, missingness, distribution and keys.")
    table_in(sp)
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--samples", type=int, default=3)
    sp = add("clean", cmd_clean, "Clean and coerce values with a full change log.")
    table_in(sp)
    sp.add_argument("--options", help="JSON cleaning options.")
    sp.add_argument("--type", action="append", help="column=type coercion, repeatable.")
    sp.add_argument("--fill", help="JSON {column: fill_value}.")
    sp.add_argument("--dedupe", action="store_true")
    sp.add_argument("--dedupe-keys")
    sp = add("dedupe", cmd_dedupe, "Find exact and fuzzy duplicates with survivorship proposals.")
    table_in(sp)
    sp.add_argument("--keys", help="Comma list of key columns (default: all).")
    sp.add_argument("--fuzzy", type=float, default=0.0, help="Similarity threshold 0..1 for near matches.")
    sp = add("join", cmd_join, "Join two tables with cardinality and unmatched diagnostics.")
    sp.add_argument("--input", required=True, help="Left table.")
    sp.add_argument("--right", required=True, help="Right table.")
    sp.add_argument("--left-keys", required=True)
    sp.add_argument("--right-keys")
    sp.add_argument("--how", default="inner",
                    choices=["inner", "left", "right", "full", "anti", "semi"])
    sp.add_argument("--sheet")
    sp.add_argument("--delimiter")
    sp = add("pivot", cmd_pivot, "Pivot rows into a cross-tab with a chosen aggregation.")
    table_in(sp)
    sp.add_argument("--index", required=True, help="Comma list of row grouping columns.")
    sp.add_argument("--columns", help="Column to spread across headers.")
    sp.add_argument("--values", help="Value column to aggregate.")
    sp.add_argument("--agg", default="sum")
    sp = add("unpivot", cmd_unpivot, "Reshape wide data into long form preserving keys.")
    table_in(sp)
    sp.add_argument("--id-columns", required=True)
    sp.add_argument("--value-columns")
    sp.add_argument("--var-name", default="variable")
    sp.add_argument("--value-name", default="value")
    sp = add("aggregate", cmd_aggregate, "Group and aggregate with multiple measures.")
    table_in(sp)
    sp.add_argument("--group-by", required=True)
    sp.add_argument("--measures", required=True, help='JSON [{"column":"x","agg":"sum"}]')
    sp = add("window", cmd_window, "Add running totals, ranks, moving averages, lag/lead and shares.")
    table_in(sp)
    sp.add_argument("--column", required=True)
    sp.add_argument("--functions", help='JSON list or {"type":"moving_average","size":3}')
    sp.add_argument("--functions-csv", help="Comma list of function names.")
    sp.add_argument("--order-by")
    sp.add_argument("--date-column")
    sp.add_argument("--partition-by")
    sp = add("resample", cmd_resample, "Aggregate event data to day/week/month/quarter/year periods.")
    table_in(sp)
    sp.add_argument("--date-column", required=True)
    sp.add_argument("--value-column", required=True)
    sp.add_argument("--freq", default="month", choices=["day", "week", "month", "quarter", "year"])
    sp.add_argument("--agg", default="sum")
    sp = add("sample", cmd_sample, "Draw reproducible random/systematic/stratified/review samples.")
    table_in(sp)
    sp.add_argument("--size", type=int)
    sp.add_argument("--frac", type=float)
    sp.add_argument("--method", default="random",
                    choices=["random", "systematic", "stratified", "review"])
    sp.add_argument("--stratify-by")
    sp.add_argument("--review-column")
    sp.add_argument("--seed", type=int, default=42)
    sp = add("crosswalk", cmd_crosswalk, "Apply a controlled mapping with an exceptions queue.")
    table_in(sp)
    sp.add_argument("--column", required=True)
    sp.add_argument("--mapping", required=True, help="JSON {source: target}")
    sp.add_argument("--target-name")
    sp.add_argument("--strict", action="store_true")
    sp = add("allocate", cmd_allocate, "Allocate amounts by driver weights with exact remainders.")
    table_in(sp)
    sp.add_argument("--amount-column", required=True)
    sp.add_argument("--drivers", required=True, help="Comma list of weight columns.")
    sp.add_argument("--group-by")
    sp.add_argument("--rounding", type=int, default=2)
    sp = add("outliers", cmd_outliers, "Flag statistical/rule outliers for review (never deletes).")
    table_in(sp)
    sp.add_argument("--columns")
    sp.add_argument("--method", default="iqr", choices=["iqr", "zscore", "mad"])
    sp.add_argument("--threshold", type=float, default=1.5)
    sp.add_argument("--sd", type=float, default=3.0)
    sp = add("enforce", cmd_enforce, "Coerce columns to declared types and collect exceptions.")
    table_in(sp)
    sp.add_argument("--schema", required=True, help='JSON {"column":"number|integer|date|boolean|text|percent"}')

    # statistics
    sp = add("describe", cmd_describe, "Descriptive statistics with interpretation limits.")
    table_in(sp)
    sp.add_argument("--columns")
    sp.add_argument("--group-by")
    sp = add("correlate", cmd_correlate, "Pearson/Spearman correlation with significance and caveats.")
    table_in(sp)
    sp.add_argument("--x", required=True)
    sp.add_argument("--y", required=True)
    sp.add_argument("--method", default="pearson", choices=["pearson", "spearman"])
    sp = add("corrmatrix", cmd_corrmatrix, "Correlation matrix across numeric columns.")
    table_in(sp)
    sp.add_argument("--columns")
    sp = add("regress", cmd_regress, "OLS regression with coefficients, fit and diagnostics.")
    table_in(sp)
    sp.add_argument("--y", required=True)
    sp.add_argument("--predictors", required=True)
    sp = add("ttest", cmd_ttest, "One-sample, two-sample or paired t-test.")
    table_in(sp)
    sp.add_argument("--column", required=True)
    sp.add_argument("--group-column")
    sp.add_argument("--mu", type=float, default=0.0)
    sp.add_argument("--alpha", type=float, default=0.05)
    sp = add("proportions", cmd_proportions, "Two-proportion z-test with confidence interval.")
    for flag, typ in (("--successes-a", int), ("--n-a", int), ("--successes-b", int), ("--n-b", int)):
        sp.add_argument(flag, type=typ, required=True)
    sp.add_argument("--alpha", type=float, default=0.05)
    sp = add("chisquare", cmd_chisquare, "Chi-square independence test with Cramer's V.")
    table_in(sp)
    sp.add_argument("--row-column", required=True)
    sp.add_argument("--column-column", required=True)
    sp.add_argument("--alpha", type=float, default=0.05)
    sp = add("abtest", cmd_abtest, "A/B test analysis with lift, CI and power context.")
    for flag in ("--visitors-a", "--conversions-a", "--visitors-b", "--conversions-b"):
        sp.add_argument(flag, type=int, required=True)
    sp.add_argument("--alpha", type=float, default=0.05)
    sp = add("samplesize", cmd_samplesize, "Required sample size for a stated effect and power.")
    sp.add_argument("--effect-size", type=float)
    sp.add_argument("--baseline-rate", type=float)
    sp.add_argument("--mde", type=float)
    sp.add_argument("--power", type=float, default=0.8)
    sp.add_argument("--alpha", type=float, default=0.05)
    sp.add_argument("--population", type=int)
    sp = add("control-chart", cmd_control_chart, "Control chart limits and special-cause signals.")
    table_in(sp)
    sp.add_argument("--column", required=True)
    sp.add_argument("--subgroup", type=int, default=1)
    sp.add_argument("--sigma", type=float, default=3)
    sp = add("montecarlo", cmd_montecarlo, "Seeded Monte Carlo simulation with percentiles.")
    sp.add_argument("--variables", required=True)
    sp.add_argument("--iterations", type=int, default=10000)
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--formula", default="sum", choices=["sum", "product"])
    sp.add_argument("--buckets", type=int, default=20)
    sp = add("forecast", cmd_forecast, "Naive/linear/Holt/drift forecasts with in-sample accuracy.")
    sp.add_argument("--input")
    sp.add_argument("--column")
    sp.add_argument("--sheet")
    sp.add_argument("--values", help="JSON list of historical values.")
    sp.add_argument("--periods", type=int, default=3)
    sp.add_argument("--method", default="auto",
                    choices=["auto", "naive", "seasonal_naive", "mean", "linear", "holt", "drift"])
    sp.add_argument("--seasonality", type=int)
    sp = add("accuracy", cmd_accuracy, "Forecast accuracy metrics (MAE/RMSE/MAPE/bias).")
    sp.add_argument("--actuals", required=True)
    sp.add_argument("--predictions", required=True)
    sp = add("rfm", cmd_rfm, "RFM segmentation with explainable scores.")
    table_in(sp)
    sp.add_argument("--customer-column", required=True)
    sp.add_argument("--date-column", required=True)
    sp.add_argument("--amount-column", required=True)
    sp.add_argument("--as-of")
    sp.add_argument("--quintiles", type=int, default=5)
    sp = add("cohort", cmd_cohort, "Cohort retention matrix by period.")
    table_in(sp)
    sp.add_argument("--customer-column", required=True)
    sp.add_argument("--date-column", required=True)
    sp.add_argument("--value-column")
    sp.add_argument("--period", default="month")
    sp.add_argument("--max-periods", type=int, default=12)
    sp = add("basket", cmd_basket, "Item co-occurrence: support, confidence and lift.")
    table_in(sp)
    sp.add_argument("--transaction-column", required=True)
    sp.add_argument("--item-column", required=True)
    sp.add_argument("--min-support", type=float, default=0.01)
    sp = add("scorecard", cmd_scorecard, "Transparent weighted scoring for review queues.")
    table_in(sp)
    sp.add_argument("--weights", required=True, help="JSON {column: weight}")
    sp.add_argument("--output-column", default="score")
    sp.add_argument("--transform", choices=["none", "log"])
    sp = add("assumptions", cmd_assumptions, "Audit statistical assumptions per column.")
    table_in(sp)
    sp.add_argument("--columns")

    # finance
    sp = add("coa", cmd_coa, "Validate a chart of accounts (types, hierarchy, duplicates).")
    sp.add_argument("--input")
    sp.add_argument("--accounts", help="JSON list of accounts.")
    sp = add("journal-check", cmd_journal_check, "Validate double-entry balance per journal reference.")
    table_in(sp)
    sp.add_argument("--tolerance", type=float, default=0.01)
    sp = add("trial-balance", cmd_trial_balance, "Build a trial balance with imbalance and abnormal-balance checks.")
    table_in(sp)
    sp.add_argument("--coa", help="Chart of accounts file (csv with code,type).")
    sp.add_argument("--accounts", help="JSON accounts list.")
    sp.add_argument("--as-of")
    sp.add_argument("--opening", help="JSON {account: opening_balance}")
    sp = add("gl-summary", cmd_gl_summary, "Summarise ledger detail by dimension and period.")
    table_in(sp)
    sp.add_argument("--group-by")
    sp.add_argument("--coa")
    sp.add_argument("--sign-by-type", action="store_true")
    sp = add("income-statement", cmd_income_statement, "Map a trial balance into an income statement.")
    table_in(sp)
    sp.add_argument("--mapping", required=True)
    sp = add("balance-sheet", cmd_balance_sheet, "Map a trial balance into a balance sheet with a balance check.")
    table_in(sp)
    sp.add_argument("--mapping", required=True)
    sp = add("cash-flow", cmd_cash_flow, "Indirect-method cash flow with reconciliation check.")
    sp.add_argument("--inputs", required=True)
    sp = add("variance", cmd_variance, "Actual vs budget variance with materiality flags.")
    sp.add_argument("--input", required=True, help="Actuals table.")
    sp.add_argument("--budget", required=True)
    sp.add_argument("--key", default="account")
    sp.add_argument("--lower-is-better", action="store_true")
    sp.add_argument("--sheet")
    sp.add_argument("--delimiter")
    sp = add("drivers", cmd_drivers, "Driver-based multi-period projection.")
    sp.add_argument("--drivers", required=True)
    sp.add_argument("--base-values", required=True)
    sp.add_argument("--periods", type=int, default=12)
    sp = add("dcf", cmd_dcf, "Discounted cash flow valuation with terminal value and sensitivities.")
    sp.add_argument("--fcf", required=True, help="JSON list of free cash flows.")
    sp.add_argument("--rate", type=float, required=True)
    sp.add_argument("--terminal-growth", type=float)
    sp.add_argument("--terminal-multiple", type=float)
    sp.add_argument("--mid-year", action="store_true")
    sp.add_argument("--net-debt", type=float, default=0.0)
    sp.add_argument("--shares", type=float)
    sp = add("loan", cmd_loan, "Amortisation schedule for a loan with interest and principal split.")
    sp.add_argument("--principal", type=float, required=True)
    sp.add_argument("--rate", type=float, required=True, help="Annual nominal rate, e.g. 0.075")
    sp.add_argument("--periods", type=int, required=True)
    sp.add_argument("--frequency", type=int, default=12, help="Periods per year (default 12).")
    sp.add_argument("--payment", type=float)
    sp.add_argument("--extra", type=float, default=0.0)
    sp.add_argument("--start-date")
    sp = add("lease", cmd_lease, "Lease liability amortisation from supplied payments.")
    sp.add_argument("--payments", required=True, help="JSON list of periodic payments.")
    sp.add_argument("--rate", type=float, required=True)
    sp.add_argument("--liability", type=float)
    sp.add_argument("--start-date")
    sp.add_argument("--periodicity", default="monthly")
    sp = add("depreciation", cmd_depreciation, "Depreciation schedule (straight line/declining/sum-of-years).")
    sp.add_argument("--cost", type=float, required=True)
    sp.add_argument("--salvage", type=float, default=0.0)
    sp.add_argument("--life", type=float, required=True)
    sp.add_argument("--method", default="straight_line",
                    choices=["straight_line", "double_declining", "sum_of_years", "declining_balance"])
    sp.add_argument("--frequency", type=int, default=12)
    sp.add_argument("--declining-rate", type=float)
    sp = add("aging", cmd_aging, "AR/AP aging by configurable buckets.")
    table_in(sp)
    sp.add_argument("--as-of")
    sp.add_argument("--amount-column", default="amount")
    sp.add_argument("--due-column", default="due_date")
    sp.add_argument("--party-column", default="customer")
    sp.add_argument("--buckets", default="0,30,60,90")
    sp.add_argument("--paid-column", default="paid",
                    help="Column marking settled items (set to an empty value to ignore).")
    sp = add("dup-invoices", cmd_dup_invoices, "Detect duplicate invoice candidates for review.")
    table_in(sp)
    sp.add_argument("--amount-column", default="amount")
    sp.add_argument("--vendor-column", default="vendor")
    sp.add_argument("--date-column", default="date")
    sp.add_argument("--number-column", default="invoice_number")
    sp = add("po-match", cmd_po_match, "Two-way/three-way PO-invoice-receipt matching.")
    sp.add_argument("--input", required=True, help="Purchase orders table.")
    sp.add_argument("--invoices", required=True)
    sp.add_argument("--receipts")
    sp.add_argument("--price-tolerance", type=float, default=0.02)
    sp.add_argument("--qty-tolerance", type=float, default=0.0)
    sp = add("vat-recon", cmd_vat, "VAT/GST reconciliation with rate and ledger checks.")
    sp.add_argument("--output-tax", type=float, required=True)
    sp.add_argument("--input-tax", type=float, required=True)
    sp.add_argument("--ledger-tax", type=float)
    sp.add_argument("--sales", type=float)
    sp.add_argument("--rate", type=float)
    sp.add_argument("--tolerance", type=float, default=0.01)
    sp = add("bank-recon", cmd_bank_recon, "Match bank and ledger lines with an open-item list.")
    sp.add_argument("--input", required=True, help="Bank statement lines.")
    sp.add_argument("--ledger", required=True)
    sp.add_argument("--amount-column", default="amount")
    sp.add_argument("--date-column", default="date")
    sp.add_argument("--ref-column", default="reference")
    sp.add_argument("--window", type=int, default=3)
    sp = add("portfolio", cmd_portfolio, "Time-weighted, money-weighted and benchmark returns.")
    sp.add_argument("--values", required=True, help="JSON list of period-end values.")
    sp.add_argument("--flows", help="JSON list of net contributions.")
    sp.add_argument("--benchmark", help="JSON list of benchmark index values.")
    sp = add("risk-metrics", cmd_risk_metrics, "Volatility, drawdown, Sharpe/Sortino and beta.")
    sp.add_argument("--returns", required=True, help="JSON list of period returns (0.01 = 1%).")
    sp.add_argument("--risk-free", type=float, default=0.0)
    sp.add_argument("--frequency", type=int, default=12)
    sp.add_argument("--benchmark", help="JSON list of benchmark returns.")
    sp = add("debt-schedule", cmd_debt, "Debt amortisation or interest-only schedule.")
    sp.add_argument("--principal", type=float, required=True)
    sp.add_argument("--rate", type=float, required=True)
    sp.add_argument("--periods", type=int, required=True)
    sp.add_argument("--frequency", type=int, default=12)
    sp.add_argument("--interest-only", action="store_true")
    sp.add_argument("--grace", type=int, default=0)
    sp = add("ratios", cmd_ratios, "Financial ratio pack from statement inputs.")
    sp.add_argument("--income", required=True, help="JSON of income lines.")
    sp.add_argument("--balance", required=True, help="JSON of balance lines.")
    sp = add("kpi", cmd_kpi, "Evaluate a KPI against target, tolerance and direction.")
    sp.add_argument("--actual", required=True)
    sp.add_argument("--target", required=True)
    sp.add_argument("--tolerance", type=float)
    sp.add_argument("--direction", default="higher_is_better",
                    choices=["higher_is_better", "lower_is_better"])
    sp.add_argument("--name", default="kpi")
    sp = add("revenue-recognition", cmd_revenue_recognition, "Recognition schedule (straight line/milestones).")
    sp.add_argument("--total", type=float, required=True)
    sp.add_argument("--periods", type=int, required=True)
    sp.add_argument("--method", default="straight_line", choices=["straight_line", "milestones"])
    sp.add_argument("--milestones", help="JSON weights per period.")

    # governance
    sp = add("qa-gate", cmd_qa_gate, "Run the pre-delivery quality gate across structure/formula/a11y/security.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--spec", help="JSON with required_sheets, required_cells, reconciliations.")
    sp = add("privacy-scan", cmd_privacy, "Detect personal/confidential data in cells, headers and metadata.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--limit", type=int, default=300)
    sp = add("redact", cmd_redact, "Mask/remove/hash/tokenize sensitive values into a new file (approval-gated).")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output", required=True)
    sp.add_argument("--columns", help="Comma list of columns to redact (default: pattern scan).")
    sp.add_argument("--patterns", help="Comma list: email,phone,credit_card,ssn_like,iban,ipv4,national_id_ph")
    sp.add_argument("--mode", default="mask", choices=["mask", "remove", "hash", "tokenize"])
    sp = add("scrub", cmd_scrub, "Remove comments/metadata/hidden sheets into a clean copy (approval-gated).")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output")
    sp.add_argument("--keep-comments", action="store_true")
    sp.add_argument("--remove-hidden-sheets", action="store_true")
    sp = add("export-gate", cmd_export_gate, "Pre-release check: sensitivity, links, hidden data, filters.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--recipients", help="Comma list of intended recipients.")
    sp.add_argument("--allow-external-links", action="store_true")
    sp.add_argument("--block-hidden", action="store_true")
    sp.add_argument("--max-sensitivity", default="medium", choices=["clean", "review", "high_risk"])
    sp = add("risk-classify", cmd_risk, "Classify workbook risk and list minimum controls.")
    sp.add_argument("--input", required=True)
    sp.add_argument("--purpose")
    sp.add_argument("--downstream")
    sp.add_argument("--regulated", action="store_true")
    sp = add("reconcile", cmd_reconcile, "Tie out named values with tolerances.")
    sp.add_argument("--pairs", required=True, help='JSON [{"name":"x","a":1,"b":1}]')
    sp.add_argument("--tolerance", type=float, default=0.005)
    sp = add("regression", cmd_regression, "Compare baseline and candidate workbooks for regressions.")
    sp.add_argument("--baseline", required=True)
    sp.add_argument("--candidate", required=True)
    sp.add_argument("--tolerance", type=float, default=0.01)
    sp = add("review-pack", cmd_review_pack, "Generate a review/approval checklist artefact.")
    sp.add_argument("--spec", required=True)

    # workflow
    sp = add("batch", cmd_batch, "Run a validated pipeline of commands with dry-run, backups and recovery.")
    sp.add_argument("--ops", required=True, help='JSON [{"command":"profile","args":{"input":"x.csv"}}]')
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--keep-going", action="store_true")
    sp = add("task-plan", cmd_task_plan, "Decompose a plain-language request into staged commands.")
    sp.add_argument("--input")
    sp.add_argument("--text")
    sp = add("assist-build", cmd_assist_build, "Generate a review-ready workbook from a plain-language request.")
    sp.add_argument("--text")
    sp.add_argument("--input")
    sp.add_argument("--output", required=True)
    sp = add("narrate", cmd_narrate, "Grounded narrative findings from a table.")
    table_in(sp)
    sp.add_argument("--measure", help="Numeric measure column.")
    sp.add_argument("--order-column", help="Date/period column for trend commentary.")
    return p


COMMANDS = {
    "capabilities": cmd_capabilities,
    "catalog-parse": cmd_catalog_parse,
    "selftest": cmd_selftest,
    "plan": cmd_plan,
    "read": cmd_read,
    "dialect": cmd_dialect,
    "encoding": cmd_encoding,
    "structure": cmd_structure,
    "compare": cmd_compare,
    "triage": cmd_triage,
    "links": cmd_links,
    "hidden": cmd_hidden,
    "manifest": cmd_manifest,
    "convert": cmd_convert,
    "flatten": cmd_flatten,
    "build": cmd_build,
    "set-cells": cmd_set_cells,
    "bulk": cmd_bulk,
    "layout": cmd_layout,
    "cond-format": cmd_cond_format,
    "validations": cmd_validations,
    "protect": cmd_protect,
    "print-setup": cmd_print_setup,
    "nav": cmd_nav,
    "merge-audit": cmd_merge_audit,
    "sort": cmd_sort,
    "number-format": cmd_number_format,
    "chart": cmd_chart,
    "chart-advice": cmd_chart_advice,
    "viz-audit": cmd_viz_audit,
    "dashboard": cmd_dashboard,
    "eval": cmd_formula_eval,
    "explain": cmd_formula_explain,
    "deps": cmd_formula_deps,
    "scan": cmd_scan,
    "hardcode": cmd_hardcode,
    "profile": cmd_profile,
    "clean": cmd_clean,
    "dedupe": cmd_dedupe,
    "join": cmd_join,
    "pivot": cmd_pivot,
    "unpivot": cmd_unpivot,
    "aggregate": cmd_aggregate,
    "window": cmd_window,
    "resample": cmd_resample,
    "sample": cmd_sample,
    "crosswalk": cmd_crosswalk,
    "allocate": cmd_allocate,
    "outliers": cmd_outliers,
    "enforce": cmd_enforce,
    "describe": cmd_describe,
    "correlate": cmd_correlate,
    "corrmatrix": cmd_corrmatrix,
    "regress": cmd_regress,
    "ttest": cmd_ttest,
    "proportions": cmd_proportions,
    "chisquare": cmd_chisquare,
    "abtest": cmd_abtest,
    "samplesize": cmd_samplesize,
    "control-chart": cmd_control_chart,
    "montecarlo": cmd_montecarlo,
    "forecast": cmd_forecast,
    "accuracy": cmd_accuracy,
    "rfm": cmd_rfm,
    "cohort": cmd_cohort,
    "basket": cmd_basket,
    "scorecard": cmd_scorecard,
    "assumptions": cmd_assumptions,
    "coa": cmd_coa,
    "journal-check": cmd_journal_check,
    "trial-balance": cmd_trial_balance,
    "gl-summary": cmd_gl_summary,
    "income-statement": cmd_income_statement,
    "balance-sheet": cmd_balance_sheet,
    "cash-flow": cmd_cash_flow,
    "variance": cmd_variance,
    "drivers": cmd_drivers,
    "dcf": cmd_dcf,
    "loan": cmd_loan,
    "lease": cmd_lease,
    "depreciation": cmd_depreciation,
    "aging": cmd_aging,
    "dup-invoices": cmd_dup_invoices,
    "po-match": cmd_po_match,
    "vat-recon": cmd_vat,
    "bank-recon": cmd_bank_recon,
    "portfolio": cmd_portfolio,
    "risk-metrics": cmd_risk_metrics,
    "debt-schedule": cmd_debt,
    "ratios": cmd_ratios,
    "kpi": cmd_kpi,
    "revenue-recognition": cmd_revenue_recognition,
    "qa-gate": cmd_qa_gate,
    "privacy-scan": cmd_privacy,
    "redact": cmd_redact,
    "scrub": cmd_scrub,
    "export-gate": cmd_export_gate,
    "risk-classify": cmd_risk,
    "reconcile": cmd_reconcile,
    "regression": cmd_regression,
    "review-pack": cmd_review_pack,
    "batch": cmd_batch,
    "task-plan": cmd_task_plan,
    "assist-build": cmd_assist_build,
    "narrate": cmd_narrate,
}


def table_result(out, args):
    """Collect table outputs for --export."""
    return out


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.audit_log:
        os.environ[C.AUDIT_ENV] = args.audit_log
    operation = args.command
    started = time.time()
    tables = {}
    try:
        dry_only = bool(getattr(args, "dry_run", False)) or \
            (operation == "bulk" and not getattr(args, "apply", False))
        C.require_approval(operation, args.approve or dry_only, _approval_reason(operation))
        result = args.func(args)
        if isinstance(result, tuple):
            result, tables = result
        if args.approve:
            result.setdefault("meta", {})["approved"] = True
        C.record_audit(operation, {"status": result.get("status"),
                                   "command": operation}, args.approve)
        emit(result, args, tables)
        return 0 if result["status"] == "ok" else 2
    except SkillToolError as exc:
        result = C.fail(operation, exc)
        C.record_audit(operation, {"status": "error", "code": exc.code}, args.approve)
        emit(result, args)
        return 2
    except KeyboardInterrupt:
        result = C.fail(operation, SkillToolError("CANCELLED", "Operation cancelled by the user."))
        emit(result, args)
        return 130
    except FileNotFoundError as exc:
        result = C.fail(operation, SkillToolError("INPUT_NOT_FOUND", str(exc)))
        emit(result, args)
        return 2
    except Exception as exc:  # unexpected: report cleanly, never a raw traceback
        result = C.fail(operation, SkillToolError("INTERNAL_ERROR",
                                                  f"{type(exc).__name__}: {exc}",
                                                  "Re-run with --output-format text; report this message if it persists."))
        emit(result, args)
        return 3


def _approval_reason(operation):
    return {
        "build": "writes a new workbook",
        "dashboard": "writes a new workbook",
        "assist-build": "writes a generated workbook",
        "set-cells": "modifies cell content",
        "bulk": "may rewrite many cells",
        "sort": "reorders rows",
        "protect": "changes protection state",
        "convert": "writes a converted file",
        "redact": "alters sensitive values",
        "scrub": "removes metadata and hidden content",
        "export-gate": "releases a file for distribution",
        "batch": "runs several mutating steps",
    }.get(operation, "this operation")


if __name__ == "__main__":
    sys.exit(main())
