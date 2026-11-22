#!/usr/bin/env python3
"""Command-line entry point for the Ultimate Thinking Memory Skill Tool.

Usage (run from any folder; paths resolve against the current directory):
  python3 tools-engine.py help
  python3 tools-engine.py capabilities [--category N] [--support LEVEL] [--family X] [--json]
  python3 tools-engine.py categories
  python3 tools-engine.py describe <id-or-slug>
  python3 tools-engine.py run <id-or-slug> [--op FAMILY:op] [--input PATH] [--option key=value ...]
        [--output-dir DIR] [--state-dir DIR] [--as-of YYYY-MM-DD] [--timeout SECONDS]
        [--approve] [--format text|json] [--no-save]
  python3 tools-engine.py selfcheck

Exit codes: 0 ok, 1 tool error, 2 usage error, 3 approval required, 4 timeout, 130 cancelled.
Standard library only. Python 3.9 or newer.
"""
import argparse
import importlib.util
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # keep the Skill Tool folder free of __pycache__
TOOL_DIR = Path(__file__).resolve().parent
MODULE_FILES = ["tools-text.py", "tools-graph.py", "tools-quant.py", "tools-files.py", "tools-state.py"]
PRIMARY_ENTRY = "tools-engine.py"
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_APPROVAL, EXIT_TIMEOUT, EXIT_CANCEL = 0, 1, 2, 3, 4, 130


def _load_core():
    name = "tools_core_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / "tools-core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def _load_handlers():
    registry = {}
    owners = {}
    for filename in MODULE_FILES:
        mod_name = "tools_" + filename[len("tools-"):-3].replace("-", "_") + "_mod"
        spec = importlib.util.spec_from_file_location(mod_name, TOOL_DIR / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        for key, fn in module.HANDLERS.items():
            if key in registry:
                raise RuntimeError(f"handler {key} defined in both {owners[key]} and {filename}")
            registry[key] = fn
            owners[key] = filename
    return registry


HANDLERS = _load_handlers()


def _catalog():
    return core.load_catalog()


def _find(catalog, ident):
    needle = ident.strip().lower()
    for cap in catalog["capabilities"]:
        if needle in (cap["id"].lower(), cap["slug"].lower()):
            return cap
    raise core.UsageError(f"no capability matches {ident!r}; run 'capabilities' to list them")


def _parse_options(pairs):
    options = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise core.UsageError(f"option must be key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key.replace("_", "").replace("-", "").isalnum():
            raise core.UsageError(f"option name {key!r} may only contain letters, digits, _ and -")
        if len(value) > 20000:
            raise core.UsageError(f"option {key} is longer than 20000 characters; put it in a file and use --input")
        options[key.replace("-", "_")] = value
    return options


def cmd_help(args):
    print(__doc__.strip())
    print()
    print("Capability families (prefix: meaning):")
    print("  W worksheet | R research checks | A argument/quality audits | E evidence | X combine/vary")
    print("  C context budget | D dependency graph | N network | P decision scoring | Q quantitative")
    print("  S structure checks | K checkpoints | L ledgers | B library | M memory | V governance")
    print()
    print("Examples:")
    print("  python3 tools-engine.py run C01.01 --option title='Launch plan' --output-dir out")
    print("  python3 tools-engine.py run C35.01 --input actions.json --option approved=A1")
    print("Support levels: automated (deterministic), assisted (heuristic, needs review), worksheet (template to complete).")
    print("Read knowledge-readme.md for limitations and approval rules.")
    return EXIT_OK


def cmd_capabilities(args):
    catalog = _catalog()
    rows = catalog["capabilities"]
    if args.category is not None:
        rows = [c for c in rows if c["category_no"] == args.category]
    if args.support:
        rows = [c for c in rows if c["support_level"] == args.support]
    if args.family:
        rows = [c for c in rows if c["family"] == args.family.upper()]
    if args.json:
        print(json.dumps([{k: c[k] for k in ("id", "slug", "name", "category_no", "family", "default_handler", "support_level")}
                          for c in rows], indent=2))
        return EXIT_OK
    print(f"{'ID':<8} {'SLUG':<38} {'SUPPORT':<10} {'HANDLER':<18} NAME")
    for c in rows:
        print(f"{c['id']:<8} {c['slug']:<38} {c['support_level']:<10} {c['default_handler']:<18} {c['name']}")
    print(f"\n{len(rows)} capability(ies)")
    return EXIT_OK


def cmd_categories(args):
    catalog = _catalog()
    counts = Counter(c["category_no"] for c in catalog["capabilities"])
    for cat in catalog["categories"]:
        print(f"{cat['no']:>2}  {cat['name']}  ({counts.get(cat['no'], 0)})")
    return EXIT_OK


def cmd_describe(args):
    cap = _find(_catalog(), args.capability)
    print(json.dumps(cap, indent=2, ensure_ascii=False))
    fn = HANDLERS.get(cap["default_handler"])
    doc = (fn.__doc__ or "").strip() if fn else ""
    if fn is None:
        print("\nWARNING: no handler is registered for this capability.")
    print(f"\nHandler: {cap['default_handler']}")
    print(f"Run: python3 tools-engine.py run {cap['id']} --input <path> --option key=value")
    extra = _family_ops(cap)
    if extra:
        print("Other operations in this family (use --op): " + ", ".join(extra))
    if doc:
        print(f"Notes: {doc}")
    print(f"Support: {cap['support_level']} - {_support_text(cap['support_level'])}")
    return EXIT_OK


def _family_ops(cap):
    return sorted(k for k in HANDLERS if k.split(":", 1)[0] == cap["family"] and k != cap["default_handler"])


def _support_text(level):
    return {"automated": "deterministic code; results are checkable",
            "assisted": "heuristic or pattern check; a person must review the output",
            "worksheet": "structured template; the human or agent completes the judgment"}.get(level, "unknown")


def cmd_run(args):
    catalog = _catalog()
    cap = _find(catalog, args.capability)
    policy = core.load_policy()
    limits = policy["limits"]
    timeout = args.timeout if args.timeout is not None else policy["limits"]["timeout_default_seconds"]
    if not 0 < timeout <= limits["timeout_max_seconds"]:
        raise core.UsageError(f"--timeout must be > 0 and <= {limits['timeout_max_seconds']}")
    input_path = Path(args.input).resolve() if args.input else None
    if input_path is not None and not input_path.exists():
        raise core.UsageError(f"input path does not exist: {args.input}")
    if input_path is not None and input_path.is_file() and input_path.stat().st_size > limits["max_input_bytes"]:
        raise core.UsageError(f"input file exceeds max_input_bytes ({limits['max_input_bytes']})")
    options = _parse_options(args.option)
    if args.as_of:
        options["as_of"] = args.as_of
    output_dir = Path(args.output_dir).resolve()
    state_dir = Path(args.state_dir).resolve()
    key = cap["default_handler"]
    if args.op:
        if args.op.split(":", 1)[0] != cap["family"] or args.op not in HANDLERS:
            allowed = [cap["default_handler"]] + _family_ops(cap)
            raise core.UsageError(f"--op must be one of {', '.join(allowed)} for this capability")
        key = args.op
    fn = HANDLERS.get(key)
    if fn is None:
        raise core.ToolError(f"no handler registered for {key}")
    budget = core.TimeBudget(timeout)
    ctx = core.Context(capability=cap, handler_key=key, options=options, input_path=input_path,
                       output_dir=output_dir, state_dir=state_dir, approve=args.approve,
                       policy=policy, budget=budget)
    envelope = {"tool": core.TOOL_NAME, "version": core.TOOL_VERSION, "capability": {k: cap[k] for k in (
        "id", "slug", "name", "category", "category_no", "support_level")},
        "op": key, "started": core.now_iso(), "status": "running", "timeout_seconds": timeout,
        "approved_flag": bool(args.approve),
        "input": str(input_path) if input_path else None, "result": None, "warnings": [], "errors": [], "outputs": []}
    saved = core.install_signal_handlers(budget)
    code = EXIT_OK
    try:
        result = fn(ctx)
        if not isinstance(result, dict):
            raise core.ToolError("handler returned a non-dict result")
        result.setdefault("summary", {})
        result.setdefault("items", [])
        extra_warnings = result.pop("warnings_list", [])
        envelope["result"] = result
        envelope["status"] = "completed"
        envelope["warnings"] = list(ctx.warnings) + list(extra_warnings)
    except core.ApprovalRequired as exc:
        envelope["status"] = "approval_required"
        envelope["errors"] = [str(exc)]
        envelope["approval"] = {"reason": str(exc), "details": getattr(exc, "details", {}),
                                "how": "A person must confirm, then re-run the same command with --approve."}
        code = EXIT_APPROVAL
    except core.ToolTimeout as exc:
        envelope["status"] = "timeout"
        envelope["errors"] = [str(exc)]
        code = EXIT_TIMEOUT
    except core.Cancelled as exc:
        envelope["status"] = "cancelled"
        envelope["errors"] = [str(exc)]
        code = EXIT_CANCEL
    except core.UsageError as exc:
        envelope["status"] = "usage-error"
        envelope["errors"] = [str(exc)]
        code = EXIT_USAGE
    except core.ToolError as exc:
        envelope["status"] = "error"
        envelope["errors"] = [str(exc)]
        code = EXIT_ERROR
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
        envelope["status"] = "error"
        envelope["errors"] = [f"unexpected {type(exc).__name__}: {exc}"]
        if args.debug:
            envelope["errors"].append(traceback.format_exc())
        code = EXIT_ERROR
    finally:
        core.restore_signal_handlers(saved)
    envelope["elapsed_ms"] = budget.elapsed_ms()
    envelope["finished"] = core.now_iso()
    envelope["outputs"] = list(ctx.outputs)
    if not args.no_save:
        try:
            result_path = core.atomic_write_json(output_dir / f"{cap['slug']}.result.json", envelope, policy)
            report_path = core.atomic_write_text(output_dir / f"{cap['slug']}.report.md",
                                                 core.render_report(envelope), policy)
            envelope["outputs"] += [str(result_path), str(report_path)]
        except (OSError, core.ToolError) as exc:
            envelope["warnings"].append(f"could not save result files: {exc}")
    if args.format == "json":
        print(json.dumps(envelope, indent=2, ensure_ascii=False, default=str))
    else:
        print(core.render_report(envelope))
        if envelope["status"] == "approval_required":
            print("APPROVAL REQUIRED:", envelope["approval"]["reason"])
            print(json.dumps(envelope["approval"]["details"], indent=2, default=str))
            print("Re-run the same command with --approve only after a person confirms.")
        elif envelope["status"] != "completed":
            print("FAILED:", "; ".join(envelope["errors"]))
    return code


REQUIRED_POLICY_KEYS = ["limits", "defaults", "approval_tiers", "memory_durability", "governance_keywords",
                        "stopwords", "pii_patterns", "fallacy_patterns", "feedback_classes", "unit_dimensions"]


def cmd_selfcheck(args):
    problems, notes = [], []
    catalog = _catalog()
    policy = core.load_policy()
    caps = catalog["capabilities"]
    if len(caps) != 350 or len(catalog["categories"]) != 35:
        problems.append(f"catalog has {len(caps)} capabilities and {len(catalog['categories'])} categories; expected 350 and 35")
    slugs = [c["slug"] for c in caps]
    if len(set(slugs)) != len(slugs):
        problems.append("duplicate capability slugs")
    ids = [c["id"] for c in caps]
    if len(set(ids)) != len(ids):
        problems.append("duplicate capability ids")
    missing = sorted({c["default_handler"] for c in caps if c["default_handler"] not in HANDLERS})
    if missing:
        problems.append(f"handlers not registered: {missing}")
    families = {c["family"] for c in caps}
    orphan = sorted(k for k in HANDLERS if k.split(":", 1)[0] not in families)
    if orphan:
        problems.append(f"handlers whose family has no capability: {orphan}")
    extra = sorted(set(HANDLERS) - {c["default_handler"] for c in caps})
    if extra:
        notes.append(f"operations reachable with --op (not a default mapping): {extra}")
    counts = Counter(c["support_level"] for c in caps)
    notes.append(f"support levels: {dict(counts)}")
    for key in REQUIRED_POLICY_KEYS:
        if key not in policy:
            problems.append(f"policy key missing: {key}")
    for c in caps:
        for field in ("slug", "id", "name", "description", "category", "category_no", "support_level"):
            if not c.get(field) and c.get(field) != 0:
                problems.append(f"{c.get('id')}: missing field {field}")
                break
    template = TOOL_DIR / "asset-worksheet-template.md"
    if not template.exists():
        problems.append("asset-worksheet-template.md is missing")
    stray = [p.name for p in TOOL_DIR.iterdir() if p.is_dir()]
    if stray:
        problems.append(f"subfolders found in the Skill Tool folder: {stray}")
    if (TOOL_DIR / "__pycache__").exists():
        problems.append("__pycache__ folder present in the Skill Tool folder")
    if (TOOL_DIR / "SKILL.md").exists():
        problems.append("SKILL.md must not be present")
    for p in TOOL_DIR.iterdir():
        if p.is_file() and not p.name.startswith(("tools-", "knowledge-", "asset-")):
            problems.append(f"unprefixed file: {p.name}")
    if not (TOOL_DIR / "knowledge-readme.md").exists():
        problems.append("knowledge-readme.md is missing")
    report = {"status": "ok" if not problems else "failed", "problems": problems, "notes": notes,
              "handlers": len(HANDLERS), "capabilities": len(caps)}
    print(json.dumps(report, indent=2))
    return EXIT_OK if not problems else EXIT_ERROR


def build_parser():
    parser = argparse.ArgumentParser(prog="tools-engine.py", description="Ultimate Thinking Memory Skill Tool",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("help", help="show usage and families")
    p = sub.add_parser("capabilities", help="list capabilities")
    p.add_argument("--category", type=int)
    p.add_argument("--support", choices=["automated", "assisted", "worksheet"])
    p.add_argument("--family", help="one-letter family prefix, e.g. Q")
    p.add_argument("--json", action="store_true")
    sub.add_parser("categories", help="list the 35 categories with counts")
    p = sub.add_parser("describe", help="show one capability")
    p.add_argument("capability")
    p = sub.add_parser("run", help="run one capability")
    p.add_argument("capability", help="capability id (e.g. C05.01) or slug")
    p.add_argument("--op", help="run another operation from the same family (see describe), e.g. L:verify")
    p.add_argument("--input", help="file or folder the capability reads")
    p.add_argument("--option", "-o", action="append", help="key=value option (repeatable)")
    p.add_argument("--output-dir", default="thinking-output", help="where results are written (default thinking-output)")
    p.add_argument("--state-dir", default="thinking-state", help="where persistent state lives (default thinking-state)")
    p.add_argument("--as-of", help="reference date YYYY-MM-DD for time-based checks")
    p.add_argument("--timeout", type=float, help="seconds before the run is stopped")
    p.add_argument("--approve", action="store_true", help="record human approval for a gated action")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--no-save", action="store_true", help="do not write result and report files")
    p.add_argument("--debug", action="store_true", help="include tracebacks for unexpected errors")
    sub.add_parser("selfcheck", help="verify the package is complete and consistent")
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else EXIT_OK
    if args.command in (None, "help"):
        return cmd_help(args)
    handlers = {"capabilities": cmd_capabilities, "categories": cmd_categories, "describe": cmd_describe,
                "run": cmd_run, "selfcheck": cmd_selfcheck}
    try:
        return handlers[args.command](args)
    except core.UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except core.ApprovalRequired as exc:
        print(f"approval required: {exc}", file=sys.stderr)
        return EXIT_APPROVAL
    except core.ToolTimeout as exc:
        print(f"timeout: {exc}", file=sys.stderr)
        return EXIT_TIMEOUT
    except core.Cancelled as exc:
        print(f"cancelled: {exc}", file=sys.stderr)
        return EXIT_CANCEL
    except core.ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return EXIT_CANCEL


if __name__ == "__main__":
    sys.exit(main())
