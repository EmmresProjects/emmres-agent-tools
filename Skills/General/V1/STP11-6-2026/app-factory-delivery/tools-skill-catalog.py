#!/usr/bin/env python3
"""agent-skill-catalog - primary CLI entry point.

A production Skill Tool for agent skill catalogs: ingest catalog sources
(plain text / Markdown / copied HTML), validate and normalize them into a
structured dataset, browse and search the catalog, route a user request to
the smallest covering set of skills, draft skill specifications from the
catalog's recommended fields, and export the dataset.

Usage:
    python3 agent-skill-catalog/tools-skill-catalog.py <command> [options]
    python3 agent-skill-catalog/tools-skill-catalog.py capabilities
    python3 agent-skill-catalog/tools-skill-catalog.py <command> --help

Standard library only (Python 3.9+). No network access, no shell execution,
no secrets. Exit codes: 0 ok | 1 structured tool error | 2 usage error |
3 unexpected internal error | 130 cancelled (SIGINT).
"""

import argparse
import importlib.util
import json
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = "agent-skill-catalog"

# Defaults live beside this script, so the folder can be copied anywhere and
# run from any working directory. ASC_* environment variables override them
# without editing the files (useful for CI and for alternate catalogs).
DEFAULT_ASSETS = {
    "dataset": os.environ.get("ASC_DATASET") or os.path.join(
        HERE, "asset-catalog-dataset.json"),
    "schema": os.environ.get("ASC_SCHEMA") or os.path.join(
        HERE, "asset-catalog-schema.json"),
    "signals": os.environ.get("ASC_SIGNALS") or os.path.join(
        HERE, "asset-routing-signals.json"),
    "template": os.environ.get("ASC_TEMPLATE") or os.path.join(
        HERE, "asset-spec-template.md"),
}


def _load_core():
    core_path = os.path.join(HERE, "tools-catalog-core.py")
    if not os.path.isfile(core_path):
        sys.stderr.write(json.dumps({
            "ok": False, "tool": TOOL, "command": None,
            "error": {"code": "E_MISSING_ASSET",
                      "message": "Core module missing: tools-catalog-core.py",
                      "hint": "Keep all agent-skill-catalog files together in "
                              "one folder."}}, indent=2) + "\n")
        sys.exit(3)
    spec = importlib.util.spec_from_file_location("catalog_core", core_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def emit(args, data, text_renderer) -> int:
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps({"ok": True, "tool": TOOL,
                          "command": args.command, "data": data},
                         indent=2, ensure_ascii=False))
    else:
        text_renderer(data, fmt)
    return 0


def fail(args, err: "core.ToolError") -> int:
    payload = {"ok": False, "tool": TOOL,
               "command": getattr(args, "command", None),
               "error": err.to_dict()}
    if getattr(args, "format", "text") == "json":
        sys.stderr.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stderr.write("ERROR [%s]: %s\n" % (err.code, err.message))
        if err.hint:
            sys.stderr.write("HINT: %s\n" % err.hint)
    return 1


def _need_asset(args, key: str) -> str:
    path = getattr(args, key, None) or DEFAULT_ASSETS[key]
    if not os.path.isfile(path):
        raise core.ToolError(
            "E_MISSING_ASSET",
            "Required asset not found: %s" % path,
            "Restore %s/%s beside the tools, or pass --%s <path>."
            % (TOOL, os.path.basename(DEFAULT_ASSETS[key]), key),
        )
    return path


def _load_cfg(args, key: str, what: str):
    return core.load_json_file(_need_asset(args, key), what)


def _handle_output(args, text) -> dict:
    out = getattr(args, "output", None)
    if out:
        return core.atomic_write_text(out, text,
                                      force=getattr(args, "force", False),
                                      dry_run=getattr(args, "dry_run", False))
    sys.stdout.write(text)
    return {"path": None, "stdout": True, "bytes": len(text.encode("utf-8"))}


# --------------------------------------------------------------------------
# Text renderers
# --------------------------------------------------------------------------

def render_capabilities(data, fmt):
    print("agent-skill-catalog capabilities (%d):" % len(data["capabilities"]))
    for cap in data["capabilities"]:
        print("  %-12s  %s" % (cap["command"], cap["summary"]))
    print("\nRun '<command> --help' for options. "
          "Add --format json to any command for structured output.")


def render_validate(data, fmt):
    status = "VALID" if data["valid"] else "INVALID"
    print("Dataset: %s" % data["dataset"])
    print("Result: %s (%d issue(s))" % (status, data["issue_count"]))
    for issue in data["issues"][:50]:
        print("  - [%s] %s: %s" % (issue.get("level", "error"),
                                   issue["path"], issue["message"]))
    if data["issue_count"] > 50:
        print("  ... %d more issues (use --format json for all)"
              % (data["issue_count"] - 50))


def render_list(data, fmt):
    if fmt == "md":
        sys.stdout.write(data["markdown"])
        return
    if data.get("mode") == "skills":
        print("Skills in category: %s (%d shown of %d)"
              % (data["category"]["name"], len(data["skills"]),
                 data["total"]))
        for sk in data["skills"]:
            print("  %2d. %-52s %s" % (sk["number"], sk["name"], sk["slug"]))
            if data.get("descriptions"):
                print("      %s" % sk["description"])
    else:
        print("Categories (%d) - %d skills total:"
              % (data["total_categories"], data["total_skills"]))
        for cat in data["categories"]:
            print("  %2d. %-58s %2d skills  %s"
                  % (cat["number"], cat["name"], cat["skill_count"],
                     cat["slug"]))


def render_search(data, fmt):
    if fmt == "md":
        sys.stdout.write(data["markdown"])
        return
    print("Search '%s': %d match(es), showing %d"
          % (data["query"], data["total_matches"], data["returned"]))
    for r in data["results"]:
        print("  [%5.2f] %-52s (%s)" % (r["score"], r["name"], r["slug"]))
        print("          %s" % r["category"])
        print("          %s" % r["description"])


def render_route(data, fmt):
    if fmt == "md":
        sys.stdout.write(data["markdown"])
        return
    print("Request: %s" % data["request"])
    sig = data["matched_signals"]
    if sig:
        print("Matched signals:")
        for group, values in sorted(sig.items()):
            print("  %-12s %s" % (group + ":", ", ".join(values)))
    else:
        print("Matched signals: none (falling back to keyword scoring)")
    print("Selected %d skill(s) (smallest covering set, max %d):"
          % (len(data["selected"]), data["max_skills"]))
    for i, s in enumerate(data["selected"], 1):
        print("  %d. %-50s [%5.2f]  %s" % (i, s["name"], s["score"], s["slug"]))
        print("     %s" % s["category"])
        for reason in s["reasons"]:
            print("     - %s" % reason)
    if data["uncovered_signals"]:
        print("Uncovered signals (no catalog skill matched): %s"
              % ", ".join(data["uncovered_signals"]))
    if data["orchestration_hint"]:
        print("Orchestration hint: %s (%s) - %s"
              % (data["orchestration_hint"]["name"],
                 data["orchestration_hint"]["slug"],
                 data["orchestration_hint"]["why"]))
    if data["runners_up"]:
        print("Runners-up: %s" % ", ".join(
            "%s (%.2f)" % (r["slug"], r["score"]) for r in data["runners_up"]))


def render_stats(data, fmt):
    print("Title: %s" % data["title"])
    print("Source: %s (%s)" % (data["source"] or "-", data["source_format"] or "-"))
    print("Schema version: %s" % data["schema_version"])
    print("Categories: %d | Skills: %d | Appendices: %d"
          % (data["categories"], data["skills"], data["appendices"]))
    dl = data["description_length"]
    print("Description length: min %d / avg %.1f / max %d chars"
          % (dl["min"], dl["avg"], dl["max"]))
    print("Per category:")
    for cat in data["per_category"]:
        print("  %2d. %-58s %2d" % (cat["number"], cat["name"], cat["skills"]))


def render_generic_write(data, fmt):
    if data.get("dry_run"):
        print("Dry run: would write %d bytes to %s (overwrite: %s)"
              % (data["bytes"], data["path"], data.get("would_overwrite")))
    elif data.get("written"):
        print("Wrote %d bytes to %s" % (data["bytes"], data["path"]))
    elif data.get("stdout"):
        pass  # payload already printed
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------
# Command implementations
# --------------------------------------------------------------------------

def cmd_capabilities(args) -> int:
    data = {"capabilities": core.CAPABILITIES,
            "primary_entry_point": "tools-skill-catalog.py",
            "runtime": "python3 (>=3.9, standard library only)",
            "formats": {"ingest_input": ["text", "markdown", "html"],
                        "output": ["text", "json", "csv", "md"]}}
    return emit(args, data, render_capabilities)


def cmd_ingest(args) -> int:
    deadline = core.Deadline(args.timeout)
    text = core.read_text_with_retry(args.input, args.max_input_bytes)
    deadline.check("reading input")
    raw = core.parse_catalog(text, fmt=args.input_format, deadline=deadline)
    if not raw["categories"]:
        raise core.ToolError(
            "E_PARSE_EMPTY",
            "No catalog entries found in the input.",
            "Expected lines like '1. Category Title' and "
            "'1. Skill Name: X' / 'Description: Y'. Copied HTML and Markdown "
            "are auto-detected; override with --input-format.",
        )
    if args.input == "-" and not args.source_name:
        raise core.ToolError(
            "E_MISSING_ARG",
            "ingest from stdin needs --source-name.",
            "Pass --source-name '<document name>' so the written dataset "
            "records a portable provenance value instead of a machine path.",
        )
    source_name = args.source_name or os.path.basename(args.input)
    dataset = core.normalize_catalog(raw, title=args.title, source=source_name)
    deadline.check("normalizing")
    schema_path = DEFAULT_ASSETS["schema"]
    schema = core.load_json_file(schema_path, "schema") \
        if os.path.isfile(schema_path) else None
    report = core.validate_dataset(dataset, schema)
    dataset_text = json.dumps(dataset, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        out_info = core.atomic_write_text(args.output, dataset_text,
                                          force=args.force,
                                          dry_run=args.dry_run)
    else:
        out_info = {"path": None, "stdout": True,
                    "bytes": len(dataset_text.encode("utf-8"))}
    data = {
        "source": source_name,
        "source_format": raw["format"],
        "ignored_noise_lines": raw["ignored_lines"],
        "categories": dataset["catalog"]["stats"]["categories"],
        "skills": dataset["catalog"]["stats"]["skills"],
        "appendices": dataset["catalog"]["stats"]["appendices"],
        "validation": {"valid": report["valid"],
                       "issue_count": report["issue_count"]},
        "output": out_info,
    }
    if not args.output:
        if args.format == "json":
            # single parseable envelope: the dataset rides inside data
            data["dataset"] = dataset
        else:
            # text mode: dataset JSON on stdout, summary lines follow
            sys.stdout.write(dataset_text)

    def render(d, fmt):
        print("Ingested %s (detected format: %s)"
              % (d["source"], d["source_format"]))
        print("  categories: %d | skills: %d | appendices: %d"
              % (d["categories"], d["skills"], d["appendices"]))
        print("  noise lines removed: %d" % d["ignored_noise_lines"])
        print("  validation: %s (%d issue(s))"
              % ("VALID" if d["validation"]["valid"] else "INVALID",
                 d["validation"]["issue_count"]))
        o = d["output"]
        if o.get("written"):
            print("  dataset written to: %s" % o["path"])
        elif o.get("dry_run"):
            print("  dry run: would write %d bytes to %s (overwrite: %s)"
                  % (o["bytes"], o["path"], o.get("would_overwrite")))
        else:
            print("  dataset JSON printed to stdout (use --output <path> to "
                  "save)")

    return emit(args, data, render)


def cmd_validate(args) -> int:
    dataset_path = args.dataset or DEFAULT_ASSETS["dataset"]
    dataset = core.load_json_file(dataset_path, "dataset")
    schema_path = args.schema or DEFAULT_ASSETS["schema"]
    schema = None
    if os.path.isfile(schema_path):
        schema = core.load_json_file(schema_path, "schema")
    report = core.validate_dataset(dataset, schema)
    report["dataset"] = dataset_path
    report["schema"] = schema_path if schema else None

    def render(data, fmt):
        render_validate(data, fmt)

    if not report["valid"]:
        if args.format == "json":
            sys.stderr.write(json.dumps(
                {"ok": False, "tool": TOOL, "command": "validate",
                 "error": {"code": "E_DATASET_INVALID",
                           "message": "%d validation issue(s)"
                                      % report["issue_count"]},
                 "report": report}, indent=2, ensure_ascii=False) + "\n")
        else:
            render_validate(report, "text")
        return 1
    return emit(args, report, render)


def cmd_list(args) -> int:
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args))
    if args.category:
        if args.limit < 1 or args.limit > 500:
            raise core.ToolError("E_BAD_LIMIT",
                                 "--limit must be between 1 and 500.",
                                 "Adjust --limit.")
        if args.offset < 0:
            raise core.ToolError("E_BAD_LIMIT", "--offset cannot be negative.",
                                 "Pass --offset 0 or higher.")
        cat = core._resolve_category(dataset, args.category)
        skills = [sk for sk in dataset["skills"]
                  if sk["category_id"] == cat["id"]]
        total = len(skills)
        page = skills[args.offset:args.offset + args.limit]
        data = {"mode": "skills", "category": cat, "total": total,
                "skills": page, "descriptions": args.descriptions}
        if args.format == "md":
            lines = ["# %s" % cat["name"], "",
                     "Category `%s` - %d skills" % (cat["slug"], total), ""]
            for sk in page:
                lines.append("%d. **%s** (`%s`)" % (sk["number"], sk["name"],
                                                    sk["slug"]))
                lines.append("   %s" % sk["description"])
            data["markdown"] = "\n".join(lines) + "\n"
        if args.format == "csv":
            data = {"mode": "skills", "category": cat, "total": total,
                    "skills": page, "descriptions": args.descriptions,
                    "csv": core.export_dataset(
                        {"catalog": dataset["catalog"],
                         "categories": [cat], "skills": page,
                         "appendices": []}, "csv")}
            if args.format == "csv":
                sys.stdout.write(data["csv"])
                return 0
    else:
        cat_by_id = {c["id"]: c for c in dataset["categories"]}
        counts = {}
        for sk in dataset["skills"]:
            counts[sk["category_id"]] = counts.get(sk["category_id"], 0) + 1
        cats = [dict(c, skill_count=counts.get(c["id"], 0))
                for c in dataset["categories"]]
        data = {"mode": "categories", "total_categories": len(cats),
                "total_skills": len(dataset["skills"]), "categories": cats}
        if args.format == "md":
            lines = ["# Categories", ""]
            for c in cats:
                lines.append("- **%d. %s** (`%s`) - %d skills"
                             % (c["number"], c["name"], c["slug"],
                                c["skill_count"]))
            data["markdown"] = "\n".join(lines) + "\n"
        if args.format == "csv":
            import csv as _csv
            import io as _io
            buf = _io.StringIO()
            w = _csv.writer(buf, lineterminator="\n")
            w.writerow(["number", "id", "name", "slug", "skill_count"])
            for c in cats:
                w.writerow([c["number"], c["id"], c["name"], c["slug"],
                            c["skill_count"]])
            sys.stdout.write(buf.getvalue())
            return 0

    def render(d, fmt):
        render_list(d, fmt)

    return emit(args, data, render)


def _optional_schema(args):
    path = getattr(args, "schema", None) or DEFAULT_ASSETS["schema"]
    if os.path.isfile(path):
        return core.load_json_file(path, "schema")
    return None


def cmd_search(args) -> int:
    deadline = core.Deadline(args.timeout)
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args),
                                deadline=deadline)
    data = core.search_skills(dataset, args.query, limit=args.limit,
                              category=args.category, deadline=deadline)
    if args.format == "md":
        lines = ["# Search: %s" % data["query"], "",
                 "%d match(es), showing %d" % (data["total_matches"],
                                               data["returned"]), ""]
        for r in data["results"]:
            lines.append("## %s (`%s`) - score %.2f" % (r["name"], r["slug"],
                                                        r["score"]))
            lines.append("")
            lines.append("Category: %s" % r["category"])
            lines.append("")
            lines.append(r["description"])
            lines.append("")
        data["markdown"] = "\n".join(lines)

    def render(d, fmt):
        render_search(d, fmt)

    return emit(args, data, render)


def cmd_route(args) -> int:
    deadline = core.Deadline(args.timeout)
    if args.request and args.request != "-":
        request = args.request
    elif args.request_file:
        request = core.read_text_with_retry(args.request_file,
                                            args.max_input_bytes)
    elif args.request == "-":
        request = core.read_text_with_retry("-", args.max_input_bytes)
    else:
        raise core.ToolError(
            "E_EMPTY_QUERY", "No routing request supplied.",
            "Pass --request '<text>', --request-file <path>, or --request - "
            "(stdin).")
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args),
                                deadline=deadline)
    signals = _load_cfg(args, "signals", "signals")
    data = core.route_request(dataset, signals, request,
                              max_skills=args.max_skills, deadline=deadline)
    if args.format == "md":
        lines = ["# Routing plan", "", "Request: %s" % data["request"], "",
                 "## Matched signals", ""]
        sig = data["matched_signals"]
        if sig:
            for group, values in sorted(sig.items()):
                lines.append("- **%s**: %s" % (group, ", ".join(values)))
        else:
            lines.append("- none (keyword fallback)")
        lines += ["", "## Selected skills (%d)" % len(data["selected"]), ""]
        for i, s in enumerate(data["selected"], 1):
            lines.append("%d. **%s** (`%s`) - score %.2f - %s"
                         % (i, s["name"], s["slug"], s["score"],
                            s["category"]))
            for reason in s["reasons"]:
                lines.append("   - %s" % reason)
        if data["uncovered_signals"]:
            lines += ["", "## Uncovered signals", ""]
            lines += ["- %s" % t for t in data["uncovered_signals"]]
        if data["orchestration_hint"]:
            lines += ["", "## Orchestration hint", "",
                      "- %s (`%s`): %s"
                      % (data["orchestration_hint"]["name"],
                         data["orchestration_hint"]["slug"],
                         data["orchestration_hint"]["why"])]
        data["markdown"] = "\n".join(lines) + "\n"

    def render(d, fmt):
        render_route(d, fmt)

    return emit(args, data, render)


def cmd_spec(args) -> int:
    deadline = core.Deadline(args.timeout)
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args),
                                deadline=deadline)
    signals = _load_cfg(args, "signals", "signals")
    template_text = None
    if args.format == "md":
        template_path = args.template or DEFAULT_ASSETS["template"]
        if not os.path.isfile(template_path):
            raise core.ToolError(
                "E_MISSING_ASSET",
                "Spec template not found: %s" % template_path,
                "Restore %s/asset-spec-template.md or pass --template <path>, "
                "or use --format json." % TOOL)
        template_text = core.read_text_with_retry(template_path, 1024 * 1024)
    data = core.generate_spec(dataset, signals, template_text, args.skill,
                              fmt=args.format)
    if args.format == "md":
        out_info = _handle_output(args, data["markdown"])
        if out_info.get("written"):
            print("Wrote spec draft for '%s' to %s" % (data["skill"],
                                                       out_info["path"]))
        elif out_info.get("dry_run"):
            print("Dry run: would write %d bytes to %s"
                  % (out_info["bytes"], out_info["path"]))
        return 0
    return emit(args, data, lambda d, f: print(json.dumps(d, indent=2,
                                                          ensure_ascii=False)))


def cmd_export(args) -> int:
    deadline = core.Deadline(args.timeout)
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args),
                                deadline=deadline)
    text = core.export_dataset(dataset, args.format)
    deadline.check("export")
    out_info = _handle_output(args, text)
    if out_info.get("written"):
        print(json.dumps({"ok": True, "tool": TOOL, "command": "export",
                          "data": {"format": args.format,
                                   "output": out_info}}, indent=2)
              if args.json_summary else
              "Exported %s (%d bytes) to %s"
              % (args.format, out_info["bytes"], out_info["path"]))
    elif out_info.get("dry_run"):
        print("Dry run: would export %s (%d bytes) to %s"
              % (args.format, out_info["bytes"], out_info["path"]))
    return 0


def cmd_stats(args) -> int:
    dataset = core.load_dataset(args.dataset or DEFAULT_ASSETS["dataset"],
                                schema=_optional_schema(args))
    data = core.dataset_stats(dataset)
    return emit(args, data, render_stats)


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """Usage errors exit 2 with a hint, matching the documented codes."""

    def error(self, message):
        sys.stderr.write("ERROR [E_USAGE]: %s\nHINT: run with --help for "
                         "usage.\n" % message)
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="tools-skill-catalog.py",
        description="agent-skill-catalog: ingest, validate, browse, search, "
                    "route, spec-draft, and export agent skill catalogs.",
        epilog="Every command accepts --format json for structured output. "
               "Exit codes: 0 ok, 1 tool error, 2 usage, 3 internal, "
               "130 cancelled.")
    p.add_argument("--version", action="version",
                   version="agent-skill-catalog 1.0.0 (schema %s)"
                           % core.SCHEMA_VERSION)
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add_common(sp, dataset=True, timeout=True, fmt=("text", "json")):
        if dataset:
            sp.add_argument("--dataset", default=None,
                            help="dataset JSON path (default: the bundled "
                                 "asset-catalog-dataset.json beside this "
                                 "script)")
            sp.add_argument("--schema", default=None,
                            help="schema JSON path (default: bundled "
                                 "asset-catalog-schema.json)")
        if timeout:
            sp.add_argument("--timeout", type=float, default=60.0,
                            help="time budget in seconds (default 60)")
        sp.add_argument("--format", choices=fmt, default=fmt[0],
                        help="output format (default %s)" % fmt[0])

    sp = sub.add_parser("capabilities", help="list every capability")
    add_common(sp, dataset=False, timeout=False)
    sp.set_defaults(func=cmd_capabilities)

    sp = sub.add_parser("ingest",
                        help="parse a text/markdown/html catalog source into "
                             "a normalized dataset JSON")
    sp.add_argument("--input", required=True,
                    help="source file path, or '-' for stdin")
    sp.add_argument("--input-format", choices=["auto", "text", "markdown",
                                               "html"], default="auto",
                    help="source format; 'auto' detects html/markdown/text "
                         "(default auto)")
    sp.add_argument("--title", default=None, help="catalog title override")
    sp.add_argument("--source-name", default=None,
                    help="provenance name recorded in the dataset")
    sp.add_argument("--output", default=None,
                    help="write dataset JSON here instead of stdout")
    sp.add_argument("--force", action="store_true",
                    help="allow overwriting an existing output file "
                         "(destructive - explicit approval)")
    sp.add_argument("--dry-run", action="store_true",
                    help="report what would be written without writing")
    sp.add_argument("--timeout", type=float, default=60.0)
    sp.add_argument("--max-input-bytes", type=int, default=5 * 1024 * 1024,
                    help="input size cap (default 5242880)")
    sp.add_argument("--format", choices=["text", "json"], default="text",
                    help="summary output format (default text)")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("validate", help="validate a dataset (schema + "
                                         "semantic checks)")
    add_common(sp)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("list", help="list categories, or skills in one "
                                     "category")
    add_common(sp, fmt=["text", "json", "csv", "md"])
    sp.add_argument("--category", default=None,
                    help="category slug, id, number, or name")
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--descriptions", action="store_true",
                    help="include descriptions in text mode")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="keyword search over skills")
    add_common(sp, fmt=["text", "json", "md"])
    sp.add_argument("--query", required=True, help="search terms")
    sp.add_argument("--category", default=None,
                    help="restrict to one category (slug/name/number)")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("route", help="map a request to the smallest "
                                      "covering set of skills")
    add_common(sp, fmt=["text", "json", "md"])
    sp.add_argument("--request", default=None,
                    help="request text, or '-' to read stdin")
    sp.add_argument("--request-file", default=None,
                    help="read the request from a file")
    sp.add_argument("--max-skills", type=int, default=None,
                    help="selection cap (default from signals config: 6)")
    sp.add_argument("--signals", default=None,
                    help="routing signals config (default: bundled "
                         "asset-routing-signals.json)")
    sp.add_argument("--max-input-bytes", type=int, default=5 * 1024 * 1024)
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("spec", help="draft a skill specification (9 "
                                     "recommended fields)")
    add_common(sp, timeout=True, fmt=["md", "json"])
    sp.add_argument("--skill", required=True,
                    help="skill slug, id, or name")
    sp.add_argument("--template", default=None,
                    help="markdown template (default: bundled "
                         "asset-spec-template.md)")
    sp.add_argument("--output", default=None, help="write spec here")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_spec)

    sp = sub.add_parser("export", help="export the dataset (json/csv/md)")
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--schema", default=None)
    sp.add_argument("--format", choices=["json", "csv", "md"], default="json")
    sp.add_argument("--output", default=None)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--timeout", type=float, default=60.0)
    sp.add_argument("--json-summary", action="store_true",
                    help="print a JSON summary instead of one text line")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("stats", help="dataset statistics")
    add_common(sp, timeout=False)
    sp.set_defaults(func=cmd_stats)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return 2
    signal.signal(signal.SIGINT,
                  lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return args.func(args)
    except core.ToolError as err:
        return fail(args, err)
    except KeyboardInterrupt:
        payload = {"ok": False, "tool": TOOL, "command": args.command,
                   "error": {"code": "E_CANCELLED",
                             "message": "Cancelled by user (SIGINT).",
                             "hint": "Partial outputs are cleaned up "
                                     "automatically; re-run when ready."}}
        sys.stderr.write(json.dumps(payload, indent=2) + "\n")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as err:  # unexpected internal error - structured report
        payload = {"ok": False, "tool": TOOL, "command": args.command,
                   "error": {"code": "E_INTERNAL",
                             "message": "%s: %s" % (type(err).__name__, err),
                             "hint": "This is a bug; report the command and "
                                     "input. No partial state is left "
                                     "behind."}}
        sys.stderr.write(json.dumps(payload, indent=2) + "\n")
        return 3


if __name__ == "__main__":
    sys.exit(main())
