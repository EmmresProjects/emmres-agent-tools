#!/usr/bin/env python3
"""Ultimate Text Skill Tool - primary entry point.

One unified Skill Tool for all 20 categories / 240 capabilities of the
Ultimate Text Skill Catalog. Run ``--help`` for usage, or ``list`` for the
capability index.

Examples
--------
    python3 tools-ultimate-text.py list --category unicode
    python3 tools-ultimate-text.py run typography-typography-cleaner --text "Hello  world--ok"
    python3 tools-ultimate-text.py run analysis.key-information-extractor --text-file note.txt --json
    python3 tools-ultimate-text.py contract creation.universal-text-composer --set audience=children
    python3 tools-ultimate-text.py validate creation.universal-text-composer --text-file draft.md
    python3 tools-ultimate-text.py selftest --verbose

Python 3.9+ standard library only. No network access is used anywhere.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

TOOL_FILE = os.path.abspath(__file__)
ROOT = os.path.dirname(TOOL_FILE)
TOOL_NAME = "ultimate-text"

EXIT = {
    "ok": 0,
    "contract_ready": 0,
    "validation_failed": 1,
    "error": 4,
}


# --------------------------------------------------------------------------
# module bootstrap
# --------------------------------------------------------------------------


def _load_module(path, alias):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def bootstrap():
    loader = _load_module(os.path.join(ROOT, "tools-loader.py"), "ult_loader")
    return loader.load_all(ROOT)


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------


def envelope(capability, operation, params, status="ok", output=None, data=None,
             warnings=None, diagnostics=None, started=None, inputs=None, error=None,
             extra=None):
    env = {
        "tool": TOOL_NAME,
        "version": "1.0.0",
        "operation": operation,
        "status": status,
        "capability": None if capability is None else {
            "id": capability["id"],
            "name": capability["name"],
            "category": capability["category"],
            "engine": capability["engine"],
            "requires_approval": capability.get("requires_approval", False),
        },
        "params": params or {},
        "inputs": inputs or [],
    }
    if output is not None:
        env["output"] = output
    if data is not None:
        env["data"] = data
    env["warnings"] = warnings or []
    env["diagnostics"] = diagnostics or []
    if started is not None:
        env["elapsed_ms"] = round((time.time() - started) * 1000, 1)
    if error is not None:
        env["error"] = error
    if extra:
        env.update(extra)
    return env


def render_json(env):
    return json.dumps(env, indent=2, ensure_ascii=False)


def render_human(env, show_output=True):
    lines = []
    if env.get("capability"):
        cap = env["capability"]
        lines.append("%s  [%s]  %s" % (cap["id"], cap["engine"], cap["name"]))
    lines.append("status: %s" % env["status"])
    if env.get("elapsed_ms") is not None:
        lines.append("elapsed: %sms" % env["elapsed_ms"])
    for warn in env.get("warnings", []):
        lines.append("warning: %s" % warn)
    if env.get("error"):
        err = env["error"]
        lines.append("error: %s (%s)" % (err.get("message"), err.get("code")))
        if err.get("hint"):
            lines.append("hint: %s" % err["hint"])
    if show_output and env.get("output"):
        lines.append("")
        lines.append(env["output"])
    elif env.get("data") is not None and not env.get("output"):
        lines.append("")
        lines.append(json.dumps(env["data"], indent=2, ensure_ascii=False))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def add_common(parser):
    g = parser.add_argument_group("input")
    g.add_argument("--text", default=None, help="inline input text")
    g.add_argument("--text-file", "--input", dest="text_file", default=None,
                   help="read input text from a file (relative to --cwd)")
    g.add_argument("--text2", default=None, help="second input text (diff, merge, compare)")
    g.add_argument("--text2-file", dest="text2_file", default=None, help="second input file")
    g.add_argument("--text3", default=None, help="third input text (3-way merge base)")
    g.add_argument("--text3-file", dest="text3_file", default=None, help="third input file")
    g.add_argument("--stdin", action="store_true", help="read the primary input from stdin")

    p = parser.add_argument_group("parameters")
    p.add_argument("--set", dest="sets", action="append", default=[],
                   metavar="KEY=VALUE", help="set a capability parameter (repeatable)")
    p.add_argument("--params", default=None, metavar="JSON",
                   help="capability parameters as a JSON object")
    p.add_argument("--config", default=None, help="runtime config JSON file")
    p.add_argument("--cwd", default=None, help="base directory for relative paths")
    p.add_argument("--encoding", default=None, help="input/output text encoding")

    r = parser.add_argument_group("output")
    r.add_argument("--json", action="store_true", help="emit the full JSON envelope")
    r.add_argument("--out", default=None, metavar="FILE",
                   help="write the capability output text to FILE")
    r.add_argument("--overwrite", action="store_true", help="allow replacing an existing --out file")

    s = parser.add_argument_group("safety")
    s.add_argument("--approve", action="store_true",
                   help="approve destructive / publishing actions for this run")
    s.add_argument("--dry-run", action="store_true", help="preview without writing anything")
    s.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                   help="time budget for the capability (1-300)")
    s.add_argument("--quiet", action="store_true", help="suppress the human summary")
    s.add_argument("--verbose", action="store_true", help="print diagnostics to stderr")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-ultimate-text.py",
        description="Ultimate Text Skill Tool: %d capabilities across 20 text categories."
        % 240,
        epilog="Every command accepts the global options shown below. "
               "Start with: python3 tools-ultimate-text.py list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    h = sub.add_parser("help", help="show this help and the command list")
    h.add_argument("topic", nargs="?", help="capability id or command name to explain")

    ls = sub.add_parser("list", help="list capabilities (the capability-list command)")
    ls.add_argument("--category", default=None, help="category slug or number (see --categories)")
    ls.add_argument("--engine", choices=["local", "agent", "all"], default="all")
    ls.add_argument("--search", default=None, help="filter by keyword")
    ls.add_argument("--format", choices=["table", "json", "markdown", "ids"], default="table")
    ls.add_argument("--limit", type=int, default=0, help="limit rows (0 = all)")
    ls.add_argument("--categories", action="store_true", help="list the 20 categories instead")

    se = sub.add_parser("search", help="search capabilities by keyword")
    se.add_argument("term", nargs="?", default="")
    se.add_argument("--limit", type=int, default=15)

    inf = sub.add_parser("info", help="show one capability in detail")
    inf.add_argument("capability")
    inf.add_argument("--format", choices=["text", "json", "markdown"], default="text")

    run = sub.add_parser("run", help="execute a capability")
    run.add_argument("capability")

    con = sub.add_parser("contract", help="emit the task contract for a capability")
    con.add_argument("capability")
    con.add_argument("--format", choices=["json", "markdown", "text"], default="markdown")

    val = sub.add_parser("validate", help="validate produced text against the capability contract")
    val.add_argument("capability")

    st = sub.add_parser("selftest", help="run the bundled regression fixtures")
    st.add_argument("--filter", default=None, help="only fixtures whose id starts with this prefix")
    st.add_argument("--json", action="store_true", dest="selftest_json")

    sub.add_parser("config", help="print the effective runtime configuration")
    sub.add_parser("assets", help="list the shipped asset files")
    sub.add_parser("capabilities", help="alias of 'list --format table'")
    return parser


# --------------------------------------------------------------------------
# main dispatch
# --------------------------------------------------------------------------


def _parse_params(args, catalog, skill):
    supplied = {}
    if args.params:
        try:
            parsed = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise catalog_error("--params is not valid JSON: %s" % exc)
        if not isinstance(parsed, dict):
            raise catalog_error("--params must be a JSON object")
        supplied.update(parsed)
    for item in args.sets or []:
        if "=" not in item:
            raise catalog_error("--set expects KEY=VALUE, got %r" % item)
        key, value = item.split("=", 1)
        supplied[key.strip()] = value
    return supplied


def catalog_error(message):
    return {"error": {"code": "INVALID_PARAM", "message": message,
                      "hint": "Run 'list' for capability ids and 'info <id>' for parameters.",
                      "details": {}}, "status": "error"}


def gather_texts(core, args, cfg, mods):
    base = os.path.abspath(args.cwd or cfg.get("workspace_root") or os.getcwd())
    texts = []
    enc = args.encoding or cfg.get("default_encoding", "utf-8")
    errors = cfg.get("encoding_errors", "strict")
    if args.text is not None:
        texts.append(args.text)
    elif args.text_file:
        texts.append(core.read_text_file(core.resolve_path(args.text_file, base, must_exist=True),
                                         encoding=enc, errors=errors,
                                         max_bytes=int(cfg.get("max_input_bytes", core.HARD_LIMITS["max_input_bytes"]))))
    elif args.stdin:
        texts.append(sys.stdin.read())
    else:
        texts.append("")
    for inline, path in ((args.text2, args.text2_file), (args.text3, args.text3_file)):
        if inline is not None:
            texts.append(inline)
        elif path:
            texts.append(core.read_text_file(core.resolve_path(path, base, must_exist=True),
                                             encoding=enc, errors=errors))
        else:
            texts.append("")
    return texts, base, enc


def execute(core, mods, catalog, assets, args, cfg, capability, params, texts, base, enc):
    handler = mods.handlers.get(capability["id"])
    if capability["engine"] == "local" and handler is None:
        raise core.SkillError(
            "capability %r is declared local but no handler is bound" % capability["id"],
            code="HANDLER_MISSING",
            hint="Run 'selftest' or 'config' to inspect the installation.",
        )
    timeout = args.timeout or capability.get("timeout_s") or cfg.get("default_timeout_s", 20)
    timeout = core.clamp_int(timeout, 1, core.HARD_LIMITS["max_timeout_s"], "--timeout")
    ctx = core.Context(
        core, assets, catalog, capability, params, cfg, texts, base,
        approve=bool(args.approve), dry_run=bool(args.dry_run),
        verbose=bool(args.verbose), timeout=timeout, encoding=enc,
    )
    started = time.time()
    inputs = []
    for position, label in enumerate(("primary", "secondary", "tertiary")):
        value = texts[position] if position < len(texts) else ""
        if value:
            inputs.append({"role": label, "chars": len(value),
                           "sha256": core.sha256_text(value)})

    if capability["engine"] == "agent":
        contract = mods.helper("build_contract")(ctx)
        return envelope(
            capability, "contract", params, status="contract_ready",
            output=contract.get("prompt_markdown") if contract else None,
            data={"contract": contract}, warnings=ctx.warnings, inputs=inputs,
            diagnostics=["agent-assisted capability: the calling assistant produces the "
                         "text, then 'validate' checks it against the contract"],
            started=started,
        )

    def _run():
        result = handler(ctx)
        if result is None:
            raise core.SkillError("handler returned no result", code="EMPTY_RESULT")
        if not isinstance(result, dict):
            return {"output": str(result)}
        return result

    result = core.run_timed(_run, timeout, capability["id"],
                            retries=int(cfg.get("retries", 0)))
    output = result.get("output")
    data = result.get("data")
    warnings = list(ctx.warnings) + list(result.get("warnings") or [])
    diagnostics = list(result.get("diagnostics") or [])
    extra = {}
    for key in ("approval", "files", "written", "notes"):
        if key in result:
            extra[key] = result[key]
    return envelope(capability, "run", params, status="ok", output=output, data=data,
                    warnings=warnings, diagnostics=diagnostics, started=started, extra=extra,
                    inputs=inputs)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common)
    try:
        gargs, rest = common.parse_known_args(argv)
    except SystemExit:
        raise
    parser = build_parser()
    if not rest:
        parser.print_help()
        return 0
    args = parser.parse_args(rest)
    for dest, value in vars(gargs).items():
        if not hasattr(args, dest) or getattr(args, dest) in (None, False, [], ""):
            setattr(args, dest, value)
    command = args.command

    try:
        mods = bootstrap()
    except Exception as exc:  # loader failure must be readable
        sys.stderr.write("FATAL: cannot load the Skill Tool modules: %s\n" % exc)
        return 8
    core = mods.core
    assets = core.Assets(ROOT)
    catalog = mods.modules["ult_registry"].load_catalog(assets)

    try:
        cfg = core.load_config(args.config, assets,
                               overrides={"allow_overwrite": bool(args.overwrite)} if args.overwrite else None)
    except core.SkillError as exc:
        return _fail(exc, args)

    try:
        if command == "help":
            return cmg_help(parser, mods, catalog, args)
        if command in ("list", "capabilities"):
            return cmd_list(assets, catalog, args, mods)
        if command == "search":
            return cmd_search(catalog, args)
        if command == "info":
            return cmd_info(catalog, args)
        if command == "config":
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
            return 0
        if command == "assets":
            print("\n".join(assets.names()))
            return 0
        if command == "selftest":
            selftest_mod = mods.modules["ult_selftest"]

            def _exec(capability, params, texts, base=None, approve=None):
                saved = args.approve
                if approve is not None:
                    args.approve = bool(approve)
                try:
                    return execute(core, mods, catalog, assets, args, cfg, capability,
                                   params, texts, base or os.getcwd(), "utf-8")
                finally:
                    args.approve = saved

            return selftest_mod.run_selftest(core, catalog, assets, _exec,
                                             filter_prefix=args.filter,
                                             as_json=args.selftest_json)
        if command in ("run", "contract", "validate"):
            return cmd_capability(core, mods, catalog, assets, args, cfg, command)
        parser.print_help()
        return 2
    except mods.modules["ult_registry"].ParamError as exc:
        return _fail(core.InvalidInput(exc.message, hint=exc.hint, details=exc.details), args)
    except core.ApprovalRequired as exc:
        return _fail(exc, args, status="approval_required")
    except core.SkillError as exc:
        return _fail(exc, args)
    except ModuleNotFoundError as exc:
        return _fail(core.SkillError("missing module: %s" % exc, code="MISSING_DEPENDENCY",
                                     hint="This Skill Tool needs only the Python standard library."),
                     args)
    except KeyboardInterrupt:
        sys.stderr.write("cancelled by user\n")
        return 130


def _fail(exc, args, status="error"):
    env = envelope(_LAST["capability"], "error", _LAST["params"], status=status,
                   error=exc.to_dict())
    if getattr(args, "json", False):
        print(render_json(env))
    else:
        sys.stderr.write("error: %s (%s)\n" % (exc.message, exc.code))
        if exc.hint:
            sys.stderr.write("hint: %s\n" % exc.hint)
    return exc.exit_code


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


COMMANDS_HELP = """commands
  help [topic]        show this help, or explain a capability / command
  list                capability index (--category, --engine, --search, --format, --categories)
  search <term>       keyword search over id, name, description
  info <capability>   full detail: parameters, defaults, acceptance checks
  run <capability>    execute a capability (local) or emit its contract (agent)
  contract <capability>   emit the validated task contract for the assistant
  validate <capability>   check produced text against the capability contract
  selftest            run the bundled regression fixtures
  config              print the effective runtime configuration
  assets              list the shipped asset files
"""


def cmg_help(parser, mods, catalog, args):
    topic = getattr(args, "topic", None)
    if not topic:
        parser.print_help()
        print("\n" + COMMANDS_HELP)
        print("capability index: %d capabilities, %d local, %d agent-assisted"
              % (catalog.skill_count, len(catalog.local()), len(catalog.agent())))
        return 0
    try:
        cap = catalog.resolve(topic)
    except Exception:
        for name in COMMANDS_HELP.split("\n"):
            if topic in name:
                print(name.strip())
                return 0
        sys.stderr.write("no help topic %r\n" % topic)
        return 2
    return cmd_info(catalog, argparse.Namespace(capability=cap["id"], format="text"))


def cmd_list(assets, catalog, args, mods):
    if getattr(args, "categories", False):
        rows = [[c["number"], c["slug"], c["name"], c["skill_count"]] for c in catalog.categories]
        print(core_table(rows, ["#", "slug", "category", "skills"]))
        return 0
    skills = list(catalog.by_id.values())
    if args.category:
        cat = catalog.category(args.category)
        skills = [s for s in cat["skills"]]
    if args.engine != "all":
        skills = [s for s in skills if s["engine"] == args.engine]
    if args.search:
        found = {s["id"] for s in catalog.search(args.search, limit=1000)}
        skills = [s for s in skills if s["id"] in found]
    skills.sort(key=lambda s: (s["category_slug"], s["number"]))
    if args.limit:
        skills = skills[: args.limit]
    if args.format == "ids":
        print("\n".join(s["id"] for s in skills))
        return 0
    if args.format == "json":
        print(json.dumps(skills, indent=2, ensure_ascii=False))
        return 0
    if args.format == "markdown":
        print("| id | capability | engine | category |")
        print("| --- | --- | --- | --- |")
        for s in skills:
            print("| `%s` | %s | %s | %s |" % (s["id"], s["name"], s["engine"], s["category"]))
        return 0
    rows = [[s["id"], s["name"], s["engine"]] for s in skills]
    print(core_table(rows, ["id", "capability", "engine"]))
    print("\n%d capabilit%s shown | local = deterministic, agent = contract + validate"
          % (len(skills), "y" if len(skills) == 1 else "ies"))
    return 0


def core_table(rows, headers):
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(out)


def cmd_search(catalog, args):
    results = catalog.search(args.term, limit=args.limit)
    if not results:
        sys.stderr.write("no capability matched %r\n" % args.term)
        return 5
    for skill in results:
        print("%-46s %s" % (skill["id"], skill["name"]))
        print("    %s" % skill["description"][:150])
    return 0


def cmd_info(catalog, args):
    cap = catalog.resolve(args.capability)
    if args.format == "json":
        print(json.dumps(cap, indent=2, ensure_ascii=False))
        return 0
    lines = [
        "id:          %s" % cap["id"],
        "name:        %s" % cap["name"],
        "category:    %s (%s)" % (cap["category"], cap["category_slug"]),
        "engine:      %s" % cap["engine"],
        "description: %s" % cap["description"],
        "approval:    %s" % ("required for write actions" if cap["requires_approval"] else "not required"),
        "timeout:     %ss" % cap["timeout_s"],
    ]
    if cap["engine"] == "agent":
        lines.append("usage:       run/contract emits a task contract; validate checks the result")
    lines.append("")
    lines.append("parameters:")
    if not cap["params"]:
        lines.append("  (none)")
    for name, spec in cap["params"].items():
        extra = ""
        if "values" in spec:
            extra = " one of: %s" % ", ".join(spec["values"])
        if "min" in spec or "max" in spec:
            extra += " range: %s..%s" % (spec.get("min", "-inf"), spec.get("max", "inf"))
        lines.append("  %-22s %-8s default=%s%s" % (name, spec.get("type"), spec.get("default"), extra))
    lines.append("")
    lines.append("acceptance checks:")
    for check in cap["acceptance_checks"]:
        lines.append("  - %-16s %s" % (check["kind"], check["description"]))
    text = "\n".join(lines)
    if args.format == "markdown":
        text = "```text\n" + text + "\n```"
    print(text)
    return 0


_LAST = {"capability": None, "params": {}}


def _record(cap, params=None):
    """Remember the capability being processed so error envelopes can name it."""
    _LAST["capability"] = cap
    _LAST["params"] = params or {}


def cmd_capability(core, mods, catalog, assets, args, cfg, command):
    cap = catalog.resolve(args.capability)
    _record(cap)
    try:
        supplied = _parse_params(args, catalog, cap)
        params = catalog.validate_params(cap, supplied)
        _record(cap, params)
    except mods.modules["ult_registry"].ParamError as exc:
        raise core.InvalidParam(exc.message, hint=exc.hint, details=exc.details)
    except dict as exc:  # catalog_error dict
        raise core.InvalidParam(str(exc))

    texts, base, enc = gather_texts(core, args, cfg, mods)

    if command == "validate":
        result = mods.helper("validate_output")(
            core, assets, catalog, cap, params, texts[0],
            cwd=base, approve=bool(args.approve))
        env = envelope(cap, "validate", params, status=result["status"],
                       output=None, data=result, warnings=result.get("warnings"))
        if args.json:
            print(render_json(env))
        else:
            print(render_human(env, show_output=False))
            print("")
            print(result.get("summary", ""))
        return 0 if result["status"] == "ok" else 1

    if command == "contract":
        contract = mods.helper("build_contract")(
            core.Context(core, assets, catalog, cap, params, cfg, texts, base,
                         approve=bool(args.approve), dry_run=True,
                         verbose=bool(args.verbose), encoding=enc))
        fmt = args.format
        if fmt == "json":
            print(json.dumps(contract, indent=2, ensure_ascii=False))
        elif fmt == "text":
            print(contract["prompt_markdown"])
        else:
            print(contract["prompt_markdown"])
        return 0

    env = execute(core, mods, catalog, assets, args, cfg, cap, params, texts, base, enc)

    if args.out and env.get("output") is not None:
        ctx = core.Context(core, assets, catalog, cap, params, cfg, texts, base,
                           approve=bool(args.approve), dry_run=bool(args.dry_run),
                           encoding=enc)
        info = ctx.write_output(args.out, env["output"])
        env["written"] = info
        if not args.json and not args.quiet:
            sys.stderr.write("%s %s\n" % ("would write" if not info["written"] else "wrote", info["path"]))

    if args.json:
        print(render_json(env))
    elif args.quiet:
        if env.get("output"):
            sys.stdout.write(env["output"])
    else:
        if env.get("output") is not None and not args.out:
            sys.stdout.write(env["output"])
            if not env["output"].endswith("\n"):
                sys.stdout.write("\n")
        else:
            print(render_human(env, show_output=False))
        if env.get("warnings"):
            for warn in env["warnings"]:
                sys.stderr.write("warning: %s\n" % warn)
        if env.get("diagnostics") and args.verbose:
            for line in env["diagnostics"]:
                sys.stderr.write("note: %s\n" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
