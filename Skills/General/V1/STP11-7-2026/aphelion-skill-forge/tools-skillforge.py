#!/usr/bin/env python3
"""tools-skillforge.py - primary entry point for the Aphelion Skill Forge.

Turns a plain-text, Markdown or copied-HTML capability specification into a
complete, runnable Skill Tool folder (the reusable component of a future
Copilot skill), then packages it as one ZIP with the folder at the archive root.

Commands
    help             full help for the forge and every command
    capabilities     list the forge's own commands, plus the skill catalog dataset
    extract          parse a specification and report what was found
    unify            parse, unify and print the resolved Skill Tool specification
    scaffold         write a runnable Skill Tool folder and execute it to verify
    package          zip an existing folder and inspect the archive shape
    validate-spec    validate an input file without writing anything
    doctor           environment and asset checks

Standard library only. Python 3.9+.

Examples
    python3 tools-skillforge.py help
    python3 tools-skillforge.py extract --input spec.md
    python3 tools-skillforge.py unify --input spec.md --format text
    python3 tools-skillforge.py scaffold --input spec.md --out ./out
    python3 tools-skillforge.py scaffold --catalog 7 --out ./out --package
    python3 tools-skillforge.py package --folder ./out/presentation-and-slide-engineering
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB_FILE = "tools-skillforge-lib.py"
GEN_FILE = "tools-skillforge-generate.py"
FORGE_VERSION = "1.0.0"
PACKAGE_NAME = "aphelion-skill-forge"
DEFAULT_ZIP = "aphelion-skill-tool.zip"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_APPROVAL = 4
EXIT_CHECK_FAILED = 5


def _load(filename: str):
    """Import a sibling module by path so the folder name can change freely."""
    target = HERE / filename
    if not target.exists():
        print(json.dumps({"ok": False, "error": {
            "code": "E_MODULE_MISSING",
            "message": "Required module missing: %s" % filename,
            "hint": "Keep every tools-* file in the same folder."}}, indent=2))
        raise SystemExit(EXIT_USAGE)
    module_name = "forge_" + filename.replace("-", "_").replace(".", "_")
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, os.fspath(target))
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load %s" % filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


lib = _load(LIB_FILE)
gen = _load(GEN_FILE)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _policy(args) -> dict:
    return lib.load_policy(getattr(args, "policy", None), HERE)


def _dataset(args) -> dict | None:
    return lib.load_catalog_dataset(getattr(args, "catalog_data", None), HERE)


def _spec_from_args(args, policy: dict, dataset: dict | None) -> tuple:
    """Build (spec, ParseResult) from either --input or --catalog."""
    budget = lib.Budget(timeout_seconds=getattr(args, "timeout", 120.0),
                        retries=int(policy["defaults"].get("retries", 2)),
                        backoff_seconds=float(policy["defaults"].get("retry_backoff_seconds", 0.25)))
    budget.check()

    if getattr(args, "catalog", None):
        if dataset is None:
            raise lib.ForgeError("E_ASSET_MISSING", "Catalog dataset not found.",
                                 "Restore asset-aphelion-catalog.json, or pass --input.")
        cat = None
        for entry in dataset.get("categories", []):
            if int(entry.get("number", -1)) == int(args.catalog) or entry.get("slug") == str(args.catalog):
                cat = entry
                break
        if cat is None:
            raise lib.ForgeError("E_CATALOG_SELECTION", "No catalog category matches %r." % args.catalog,
                                 "Run 'capabilities --catalog-list' to see valid numbers and slugs.")
        text_lines = ["Category %d: %s" % (cat["number"], cat["name"]),
                      "Focus: %s" % cat.get("focus", ""), ""]
        for idx, skill in enumerate(cat.get("skills", []), start=1):
            text_lines.append("%d. Skill Name: %s" % (idx, skill["name"]))
            text_lines.append("Description: %s" % skill.get("description", ""))
        parsed = lib.parse_catalog("\n".join(text_lines), "catalog:%s" % cat.get("slug"))
        spec = lib.build_spec(parsed, policy, dataset,
                              generator=getattr(args, "generator", "python"),
                              name_override=getattr(args, "name", None),
                              title_override=getattr(args, "title", None))
        return spec, parsed

    source = getattr(args, "input", None)
    if not source:
        raise lib.ForgeError("E_NO_SOURCE", "No input given.",
                             "Pass --input <file>, --input - for stdin, or --catalog <n>.")
    max_bytes = int(policy["defaults"].get("max_input_bytes", 4000000))
    raw_text, origin = lib.read_input(source, max_bytes)
    parsed = lib.parse_catalog(raw_text, origin)
    spec = lib.build_spec(parsed, policy, dataset,
                          generator=getattr(args, "generator", "python"),
                          name_override=getattr(args, "name", None),
                          title_override=getattr(args, "title", None))
    return spec, parsed


def _approval_gate(spec: dict, args, fmt: str) -> None:
    """Generation that carries risk needs an explicit human approval."""
    if not spec.get("approval_required"):
        return
    granted = {a.strip().lower() for a in (getattr(args, "approve", None) or []) if a and a.strip()}
    needed = {r.strip().lower() for r in spec.get("approval_reasons", []) if r and r.strip()}
    if needed:
        missing = sorted(needed - granted)
    else:
        missing = [] if granted else ["risk level %s" % spec.get("risk")]
    if missing:
        raise lib.ForgeError(
            "E_APPROVAL_REQUIRED",
            "This specification carries approval triggers: %s" % "; ".join(missing),
            "Review the specification, then re-run with --approve \"<reason>\" for each "
            "trigger. Use 'unify' to inspect before approving.")


def _emit(args, payload: dict) -> int:
    fmt = getattr(args, "format", "json")
    try:
        lib.emit(payload, fmt)
    except lib.ForgeError as exc:
        return lib.fail(exc, "json", EXIT_USAGE)
    return EXIT_OK


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_help(args) -> int:
    parser = build_parser()
    parser.print_help()
    print("")
    print("Command details")
    print("  extract          parse only: items, categories, mode, warnings")
    print("  unify            resolve one unified Skill Tool spec (no files written)")
    print("  scaffold         render, write, then execute the generated folder")
    print("  package          zip a folder with the folder at the archive root")
    print("  validate-spec    validate an input file and exit without writing")
    print("  doctor           check python, assets, folder rules")
    print("")
    print("Input formats: Markdown, plain text, copied HTML, or JSON.")
    print("Recognised markers include 'Category 7: Name', 'Focus: ...',")
    print("'1. Skill Name: Name', 'Description: ...', markdown headings, and")
    print("'Name: X' / 'Description: ...' pairs. Numbering, tags, CSS classes and")
    print("data attributes are stripped before parsing.")
    print("")
    print("Tips")
    print("  - Run 'unify --format text' first to see what the forge resolved.")
    print("  - Use --catalog <number|slug> to drive the forge from the bundled")
    print("    Aphelion catalog dataset instead of your own file.")
    print("  - Exit code 4 means an approval gate blocked generation.")
    return EXIT_OK


def cmd_capabilities(args) -> int:
    dataset = _dataset(args)
    commands = [
        {"name": "help", "purpose": "full help for the forge and its commands"},
        {"name": "capabilities", "purpose": "list forge commands and the bundled catalog dataset"},
        {"name": "extract", "purpose": "parse a specification and report discovered items"},
        {"name": "unify", "purpose": "resolve one unified Skill Tool specification"},
        {"name": "scaffold", "purpose": "write a runnable Skill Tool folder and verify it"},
        {"name": "package", "purpose": "zip a Skill Tool folder and inspect the archive"},
        {"name": "validate-spec", "purpose": "validate an input without writing output"},
        {"name": "doctor", "purpose": "environment and asset checks"},
    ]
    generators = [
        {"id": "python", "extension": ".py", "status": "implemented and executed during scaffold"},
        {"id": "node", "extension": ".md", "status": "specification-only (asset-<name>-manifest.json + knowledge-readme.md)"},
        {"id": "shell", "extension": ".md", "status": "specification-only (asset-<name>-manifest.json + knowledge-readme.md)"},
        {"id": "none", "extension": ".md", "status": "specification-only (asset-<name>-manifest.json + knowledge-readme.md)"},
    ]
    if args.catalog_list:
        categories = []
        if dataset:
            categories = [{"number": c["number"], "name": c["name"], "slug": c["slug"],
                           "domain": c.get("domain"), "risk": c.get("risk"),
                           "skills": len(c.get("skills", []))}
                          for c in dataset.get("categories", [])]
        return _emit(args, {"ok": True, "command": "capabilities", "catalog_categories": categories,
                            "categories": len(categories),
                            "dataset_loaded": bool(dataset)})

    payload = {
        "ok": True,
        "command": "capabilities",
        "skill_tool": PACKAGE_NAME,
        "version": FORGE_VERSION,
        "forge_commands": commands,
        "generators": generators,
        "dataset": {
            "loaded": bool(dataset),
            "title": (dataset or {}).get("title"),
            "categories": len((dataset or {}).get("categories", [])),
            "skills": sum(len(c.get("skills", [])) for c in (dataset or {}).get("categories", [])),
        },
    }
    if args.format == "text":
        print("%s v%s" % (PACKAGE_NAME, FORGE_VERSION))
        for row in commands:
            print("  %-15s %s" % (row["name"], row["purpose"]))
        print("  dataset: %d categories, %d skills"
              % (payload["dataset"]["categories"], payload["dataset"]["skills"]))
    else:
        lib.emit(payload, "json")
    return EXIT_OK


def cmd_extract(args) -> int:
    policy = _policy(args)
    if args.catalog:
        spec, parsed = _spec_from_args(args, policy, _dataset(args))
    else:
        if not args.input:
            raise lib.ForgeError("E_NO_SOURCE", "No input given.",
                                 "Pass --input <file>, --input - for stdin, or --catalog <n>.")
        raw, origin = lib.read_input(args.input, int(policy["defaults"]["max_input_bytes"]))
        parsed = lib.parse_catalog(raw, origin)
        spec = None
    payload = {
        "ok": True,
        "command": "extract",
        "mode": parsed.mode,
        "items": len(parsed.items),
        "categories": [{"name": c.name, "focus": c.focus, "items": len(c.items)}
                       for c in parsed.categories],
        "capabilities": [{"name": i.name, "category": i.category,
                          "description": i.description[:300]} for i in parsed.items],
        "warnings": parsed.warnings,
    }
    if spec is not None:
        payload["resolved_name"] = spec.name
    if args.format == "text":
        print("mode: %s" % parsed.mode)
        print("items: %d, categories: %d" % (len(parsed.items), len(parsed.categories)))
        for cat in parsed.categories:
            print("  [%d] %s - %s" % (len(cat.items), cat.name, cat.focus[:70]))
        for item in parsed.items[:50]:
            print("    - %s: %s" % (item.name, item.description[:80]))
        if len(parsed.items) > 50:
            print("    ... %d more" % (len(parsed.items) - 50))
        for warning in parsed.warnings:
            print("warning: %s" % warning)
        return EXIT_OK
    return _emit(args, payload)


def cmd_unify(args) -> int:
    policy = _policy(args)
    spec, parsed = _spec_from_args(args, policy, _dataset(args))
    payload = {"ok": True, "command": "unify", "spec": spec.to_dict(),
               "risk_gate": {"required": spec.approval_required,
                             "reasons": spec.approval_reasons}}
    if args.format == "text":
        print("name:      %s" % spec.name)
        print("title:     %s" % spec.title)
        print("category:  %s" % (spec.category or "-"))
        print("mode:      %s" % spec.mode)
        print("domains:   %s" % ", ".join(spec.domains))
        print("risk:      %s (approval required: %s)"
              % (spec.risk, "yes" if spec.approval_required else "no"))
        if spec.approval_reasons:
            for reason in spec.approval_reasons:
                print("  approval: %s" % reason)
        print("artifacts: %s" % ", ".join(spec.artifacts))
        print("checks:    %s" % "; ".join(spec.checks))
        print("capabilities (%d):" % len(spec.capabilities))
        for cap in spec.capabilities:
            print("  %-38s %-10s %s" % (cap["id"], cap["risk"], cap["name"]))
        for warning in spec.warnings:
            print("warning: %s" % warning)
        return EXIT_OK
    return _emit(args, payload)


def cmd_validate_spec(args) -> int:
    policy = _policy(args)
    dataset = _dataset(args)
    spec, parsed = _spec_from_args(args, policy, dataset)
    problems = lib.validate_name(spec.name)
    payload = {
        "ok": not problems,
        "command": "validate-spec",
        "name": spec.name,
        "problems": problems,
        "items": len(parsed.items),
        "categories": parsed.category_names(),
        "mode": parsed.mode,
        "risk": spec.risk,
        "approval_required": spec.approval_required,
        "approval_reasons": spec.approval_reasons,
        "warnings": spec.warnings,
        "would_write": [
            "tools-%s.py" % spec.name,
            "tools-%s-checks.py" % spec.name,
            "knowledge-readme.md",
            "asset-%s-manifest.json" % spec.name,
        ] if spec.generator == "python" else [
            "knowledge-readme.md", "asset-%s-manifest.json" % spec.name],
    }
    if args.format == "text":
        print("name: %s (%s)" % (spec.name, "valid" if not problems else "; ".join(problems)))
        print("items: %d from %d categories" % (len(parsed.items), len(parsed.categories)))
        print("risk: %s, approval: %s" % (spec.risk, spec.approval_required))
        for path in payload["would_write"]:
            print("  would write %s" % path)
        return EXIT_OK if not problems else EXIT_USAGE
    lib.emit(payload, "json")
    return EXIT_OK if not problems else EXIT_USAGE


def cmd_scaffold(args) -> int:
    policy = _policy(args)
    dataset = _dataset(args)
    spec, parsed = _spec_from_args(args, policy, dataset)
    _approval_gate(spec.to_dict(), args, args.format)

    out_root = Path(args.out).resolve()
    max_items = int(policy["defaults"].get("max_items", 400))
    if len(spec.capabilities) > max_items:
        raise lib.ForgeError("E_TOO_MANY_ITEMS",
                             "%d capabilities exceed max_items=%d." % (len(spec.capabilities), max_items),
                             "Raise max_items in the policy file or split the input.")

    checkpoint = lib.Checkpoint(path=out_root / ".forge-checkpoints.jsonl", enabled=args.checkpoint)
    budget = lib.Budget(timeout_seconds=args.timeout,
                        retries=int(policy["defaults"].get("retries", 2)),
                        backoff_seconds=float(policy["defaults"].get("retry_backoff_seconds", 0.25)))
    checkpoint.write("parse", "ok", items=len(parsed.items))
    budget.check()

    result = gen.scaffold(spec.to_dict(), policy, out_root, force=args.force,
                          verify=not args.no_verify, timeout=min(args.timeout, 180))
    checkpoint.write("scaffold", "ok", folder=Path(result["folder"]).name)

    folder_check = lib.validate_folder(Path(result["folder"]))
    payload = {
        "ok": folder_check["ok"],
        "command": "scaffold",
        "skill_tool": spec.name,
        "folder": result["folder"],
        "files": result["files"],
        "file_count": len(result["files"]),
        "bytes": result["bytes"],
        "spec": {"title": spec.title, "risk": spec.risk,
                 "approval_required": spec.approval_required,
                 "capabilities": len(spec.capabilities),
                 "artifacts": spec.artifacts, "checks": spec.checks},
        "layout_check": folder_check,
        "verification": result["verification"],
        "warnings": spec.warnings,
    }

    if args.package:
        zip_path = Path(args.zip or (out_root / DEFAULT_ZIP))
        zipped = gen.make_zip(Path(result["folder"]), zip_path)
        inspected = gen.inspect_zip(zip_path)
        payload["package"] = {"zip": zipped["zip"], "entries": zipped["count"],
                              "shape_ok": inspected["ok"], "problems": inspected["problems"],
                              "roots": inspected["roots"]}
        payload["ok"] = payload["ok"] and inspected["ok"]
    if args.report:
        Path(args.report).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    if args.format == "text":
        print("%s -> %s" % (spec.name, result["folder"]))
        for filename in result["files"]:
            print("  %s" % filename)
        print("layout: %s" % ("ok" if folder_check["ok"] else "; ".join(folder_check["errors"])))
        print("verification: %s" % ("passed" if result["verification"]["passed"] else "FAILED"))
        for step in result["verification"]["steps"]:
            print("  - %s" % step)
        if "package" in payload:
            print("zip: %s (%d entries, shape %s)"
                  % (payload["package"]["zip"], payload["package"]["entries"],
                     "ok" if payload["package"]["shape_ok"] else "BAD"))
    else:
        lib.emit(payload, "json")
    return EXIT_OK if payload["ok"] else EXIT_CHECK_FAILED


def cmd_package(args) -> int:
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        raise lib.ForgeError("E_FOLDER_MISSING", "Folder not found: %s" % args.folder,
                             "Pass the Skill Tool folder produced by scaffold.")
    zip_path = Path(args.zip).resolve() if args.zip else folder.parent / ("%s.zip" % folder.name)
    layout = lib.validate_folder(folder)
    zipped = gen.make_zip(folder, zip_path)
    inspected = gen.inspect_zip(zip_path)
    payload = {
        "ok": inspected["ok"] and layout["ok"],
        "command": "package",
        "folder": str(folder),
        "zip": zipped["zip"],
        "entries": zipped["count"],
        "roots": inspected["roots"],
        "archive_problems": inspected["problems"],
        "layout": layout,
    }
    if args.format == "text":
        print("zip: %s" % zipped["zip"])
        print("entries: %d, root: %s" % (zipped["count"], ", ".join(inspected["roots"])))
        print("archive: %s" % ("ok" if inspected["ok"] else "; ".join(inspected["problems"])))
        print("layout: %s" % ("ok" if layout["ok"] else "; ".join(layout["errors"])))
        return EXIT_OK if payload["ok"] else EXIT_CHECK_FAILED
    lib.emit(payload, "json")
    return EXIT_OK if payload["ok"] else EXIT_CHECK_FAILED


def cmd_doctor(args) -> int:
    checks = []
    checks.append({"name": "python_version", "ok": sys.version_info >= (3, 9),
                   "detail": sys.version.split()[0]})
    for filename in (LIB_FILE, GEN_FILE, "asset-default-policy.json",
                     "asset-aphelion-catalog.json", "asset-spec-example.md"):
        exists = (HERE / filename).exists()
        checks.append({"name": "asset:%s" % filename, "ok": exists,
                       "detail": "present" if exists else "missing"})
    try:
        policy = _policy(args)
        checks.append({"name": "policy_parse", "ok": True,
                       "detail": "%d rules, %d profiles"
                                 % (len(policy.get("rules", [])), len(policy.get("profiles", [])))})
    except lib.ForgeError as exc:
        checks.append({"name": "policy_parse", "ok": False, "detail": exc.message})
    try:
        dataset = _dataset(args)
        checks.append({"name": "catalog_dataset", "ok": dataset is not None,
                       "detail": "%d categories" % len((dataset or {}).get("categories", []))})
    except lib.ForgeError as exc:
        checks.append({"name": "catalog_dataset", "ok": False, "detail": exc.message})
    layout = lib.validate_folder(HERE)
    knowledge_missing = layout["knowledge"] == []
    checks.append({"name": "self_layout", "ok": not knowledge_missing and not layout["errors"],
                   "detail": "; ".join(layout["errors"]) or
                             "%d files, no SKILL.md, no subfolders" % layout["count"]})
    checks.append({"name": "self_knowledge_readme", "ok": not knowledge_missing,
                   "detail": "present" if not knowledge_missing
                             else "knowledge-readme.md not yet written"})
    checks.append({"name": "generated_folder_rules", "ok": layout["ok"],
                   "detail": "folder satisfies the future skill-package layout rules"
                             if layout["ok"] else "; ".join(layout["errors"])})
    for tool in ("tools-skillforge.py", LIB_FILE, GEN_FILE):
        path = HERE / tool
        checks.append({"name": "readable:%s" % tool, "ok": os.access(path, os.R_OK),
                       "detail": "ok" if path.exists() else "missing"})
    ok = all(c["ok"] for c in checks)
    payload = {"ok": ok, "command": "doctor", "checks": checks, "folder": str(HERE)}
    if args.format == "text":
        for check in checks:
            print("%-6s %-40s %s" % ("PASS" if check["ok"] else "FAIL",
                                     check["name"], check["detail"]))
    else:
        lib.emit(payload, "json")
    return EXIT_OK if ok else EXIT_CHECK_FAILED


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-skillforge.py",
        description="Aphelion Skill Forge v%s - build runnable Skill Tool folders from a "
                    "capability specification." % FORGE_VERSION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'python3 tools-skillforge.py capabilities' to list commands.")
    parser.add_argument("--version", action="version", version="%(prog)s " + FORGE_VERSION)
    sub = parser.add_subparsers(dest="command")

    def common(p, needs_source=True):
        p.add_argument("--format", choices=["json", "text"], default="json",
                       help="output format (default: json)")
        p.add_argument("--policy", default=None, help="policy override JSON file")
        p.add_argument("--catalog-data", default=None, help="path to a catalog dataset JSON")
        p.add_argument("--timeout", type=float, default=120.0, help="wall-clock budget in seconds")
        if needs_source:
            p.add_argument("--input", default=None, help="specification file, or - for stdin")
            p.add_argument("--catalog", default=None,
                           help="use a bundled catalog category by number or slug")
            p.add_argument("--generator", choices=["python", "node", "shell", "none"],
                           default="python", help="emitted runtime (default: python)")
            p.add_argument("--name", default=None, help="override the Skill Tool folder name")
            p.add_argument("--title", default=None, help="override the human title")

    p = sub.add_parser("help", help="show help")
    p.set_defaults(func=cmd_help)

    p = sub.add_parser("capabilities", help="list forge commands and dataset")
    common(p, needs_source=False)
    p.add_argument("--catalog-list", action="store_true",
                   help="list the bundled catalog categories")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("extract", help="parse a specification and report items")
    common(p)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("unify", help="resolve one unified Skill Tool specification")
    common(p)
    p.set_defaults(func=cmd_unify)

    p = sub.add_parser("validate-spec", help="validate an input without writing output")
    common(p)
    p.set_defaults(func=cmd_validate_spec)

    p = sub.add_parser("scaffold", help="write and verify a runnable Skill Tool folder")
    common(p)
    p.add_argument("--out", default="out", help="parent directory for the folder (default: out)")
    p.add_argument("--force", action="store_true", help="replace an existing folder")
    p.add_argument("--no-verify", action="store_true",
                   help="skip executing the generated tool (not recommended)")
    p.add_argument("--checkpoint", action="store_true", help="write .forge-checkpoints.jsonl")
    p.add_argument("--package", action="store_true", help="zip the folder after generation")
    p.add_argument("--zip", default=None, help="zip path when --package is used")
    p.add_argument("--report", default=None, help="write the JSON result to this path")
    p.add_argument("--approve", action="append", metavar="REASON",
                   help="record human approval for a risk trigger; repeatable")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("package", help="zip a Skill Tool folder")
    p.add_argument("--folder", required=True, help="Skill Tool folder to package")
    p.add_argument("--zip", default=None, help="zip output path")
    p.add_argument("--format", choices=["json", "text"], default="json")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("doctor", help="environment and asset checks")
    common(p, needs_source=False)
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return EXIT_OK
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_OK
    fmt = getattr(args, "format", "json")
    try:
        return args.func(args)
    except lib.ForgeError as exc:
        code = EXIT_USAGE
        if exc.code == "E_APPROVAL_REQUIRED":
            code = EXIT_APPROVAL
        elif exc.code.startswith("E_INPUT") or exc.code in (
                "E_EMPTY_INPUT", "E_ENCODING", "E_BINARY_INPUT", "E_NO_CAPABILITIES",
                "E_NO_SOURCE", "E_HTML_PARSE"):
            code = EXIT_INPUT
        return lib.fail(exc, fmt if fmt in ("json", "text") else "json", code)
    except BrokenPipeError:
        # Output was piped into a short reader such as `head`; exit quietly.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return EXIT_OK
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "E_CANCELLED",
                                                 "message": "Cancelled by the operator."}}))
        return 130


if __name__ == "__main__":
    sys.exit(main())
