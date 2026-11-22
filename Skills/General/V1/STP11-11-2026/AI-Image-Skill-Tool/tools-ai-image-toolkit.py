#!/usr/bin/env python3
"""AI Image Skill Tool - unified command-line entry point.

Examples:
  python3 tools-ai-image-toolkit.py help
  python3 tools-ai-image-toolkit.py list
  python3 tools-ai-image-toolkit.py list --category 8 --mode local
  python3 tools-ai-image-toolkit.py describe 8.1
  python3 tools-ai-image-toolkit.py run 8.1 -p image=photo.png -p width=1024
  python3 tools-ai-image-toolkit.py run 18.8 -p action=publish --approve
  python3 tools-ai-image-toolkit.py check-config --config my-settings.json

Exit codes: 0 ok, 2 invalid input, 3 approval required, 4 dependency missing,
5 unavailable (provider/config), 6 runtime error, 7 not found, 8 cancelled, 9 timeout.
"""
import argparse
import importlib.util
import json
import os
import platform
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__ folders
HERE = Path(__file__).resolve().parent
MIN_PYTHON = (3, 9)


def _load_common():
    name = "aiit_common"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / "tools-common.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_common()
ToolError = C.ToolError

ENGINE_FILES = [
    "tools-prompt-engine.py",
    "tools-compute-engine.py",
    "tools-policy-engine.py",
    "tools-image-engine.py",
    "tools-provider-adapter.py",
]
CATALOG_FILE = "asset-capability-catalog.json"
ASSET_FILES = [
    "asset-capability-catalog.json", "asset-config-default.json", "asset-prompt-vocabulary.json",
    "asset-plan-templates.json", "asset-model-profiles.json", "asset-policy-rules.json",
    "asset-layout-presets.json", "asset-sample-sweep.json",
]

MODE_NOTICES = {
    "local": "Runs locally on the files you provide. Outputs go to the output directory.",
    "heuristic": "Rule-based advisory output. A human must review it before relying on it.",
    "engine": "Builds job specs or graphs. Nothing runs on a GPU or model unless a provider is "
              "enabled, configured, and approved (--execute --approve).",
    "policy": "Rule-based screen with disclaimers. Not legal advice and not a compliance certification.",
    "blueprint": "Structured plan from a category template. It does not generate images.",
}


# ----------------------------------------------------------------- loading

def load_catalog():
    cat = C.load_asset(CATALOG_FILE)
    caps = cat.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise ToolError("runtime_error", f"{CATALOG_FILE} has no capabilities list")
    return cat, caps


def load_registry():
    reg = {}
    for fn in ENGINE_FILES:
        mod = C.load_sibling(fn)
        for name, entry in mod.OPS.items():
            if name in reg:
                raise ToolError("runtime_error", f"duplicate operation '{name}' in {fn}")
            reg[name] = entry
    return reg


def load_config(path):
    try:
        return C.load_config(path)
    except ToolError:
        raise
    except (OSError, ValueError) as e:
        raise ToolError("invalid_input", f"could not load config: {e}")


def resolve_target(caps, reg, key):
    """Return a capability dict. Registry-only utility operations (e.g. image.inspect) get a synthetic entry."""
    key = str(key).strip()
    try:
        return find_capability(caps, key)
    except ToolError as e:
        if e.code == "not_found" and key in reg:
            e_ = reg[key]
            return {"number": "utility", "id": key, "name": e_["summary"], "description": e_["summary"],
                    "category_number": 0, "category_name": "Utility (not in the 184-item catalog)",
                    "operation": key, "execution_mode": "local", "approval": "none", "risk": "low",
                    "default_params": {}}
        raise


def find_capability(caps, key):
    key = str(key).strip()
    for c in caps:
        if c["id"] == key or c["number"] == key:
            return c
    low = key.lower()
    matches = [c for c in caps if low in c["id"] or low in c["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(f"{c['number']} {c['id']}" for c in matches[:6])
        raise ToolError("invalid_input", f"'{key}' matches several capabilities: {ids}",
                        hint="Use the number (e.g. 8.1) or the full id.")
    raise ToolError("not_found", f"no capability matches '{key}'", hint="Run 'list' to see all capabilities.")


def _jdump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ----------------------------------------------------------------- commands

def cmd_help(args, parser):
    parser.print_help()
    return 0


def cmd_list(args):
    _, caps = load_catalog()
    rows = caps
    if args.category:
        cat = str(args.category).strip().lower()
        rows = [c for c in rows if str(c["category_number"]) == cat or c["category_name"].lower() == cat
                or c["category_name"].lower().startswith(cat)]
        if not rows:
            raise ToolError("not_found", f"no category matches '{args.category}'",
                            hint="Use a category number 1-23.")
    if args.mode:
        rows = [c for c in rows if c["execution_mode"] == args.mode]
    if args.json:
        print(_jdump([{k: c[k] for k in ("number", "id", "name", "category_number", "category_name",
                                         "operation", "execution_mode", "approval", "risk")} for c in rows]))
        return 0
    print(f"{'NUM':<6}{'MODE':<11}{'APPROVAL':<13}{'RISK':<8}{'OPERATION':<28}NAME")
    for c in rows:
        print(f"{c['number']:<6}{c['execution_mode']:<11}{c['approval']:<13}{c['risk']:<8}"
              f"{c['operation']:<28}{c['name']}")
    print(f"\n{len(rows)} capability(ies) shown. Use 'describe <NUM>' for parameters.")
    return 0


def cmd_ops(args):
    reg = load_registry()
    _, caps = load_catalog()
    used = {c["operation"] for c in caps}
    rows = [{"operation": n, "in_catalog": n in used, "summary": e["summary"],
             "params": [p["name"] for p in e["params"]]} for n, e in sorted(reg.items())]
    if args.json:
        print(_jdump(rows))
        return 0
    for r in rows:
        flag = "" if r["in_catalog"] else "  (utility, not in catalog)"
        print(f"{r['operation']:<24}{r['summary']}{flag}")
    return 0


def cmd_describe(args):
    _, caps = load_catalog()
    reg = load_registry()
    cap = resolve_target(caps, reg, args.capability)
    entry = reg.get(cap["operation"])
    if entry is None:
        raise ToolError("runtime_error", f"operation '{cap['operation']}' is not registered")
    params = []
    for s in entry["params"]:
        d = {"name": s["name"], "type": s["type"], "required": s.get("required", False), "help": s.get("help", "")}
        for k in ("default", "choices", "min", "max"):
            if k in s:
                d[k] = s[k]
        params.append(d)
    info = {"number": cap["number"], "id": cap["id"], "name": cap["name"], "description": cap["description"],
            "category": f"{cap['category_number']}. {cap['category_name']}", "operation": cap["operation"],
            "execution_mode": cap["execution_mode"], "approval": cap["approval"], "risk": cap["risk"],
            "notice": MODE_NOTICES.get(cap["execution_mode"], ""),
            "default_params": cap.get("default_params", {}), "parameters": params,
            "example": f"python3 tools-ai-image-toolkit.py run {cap['number']} " +
                       " ".join(f"-p {p['name']}=<{p['type']}>" for p in params if p["required"])}
    if args.json:
        print(_jdump(info))
        return 0
    print(f"{cap['number']}  {cap['name']}  [{cap['id']}]")
    print(f"Category:  {info['category']}")
    print(f"Operation: {cap['operation']}   Mode: {cap['execution_mode']}   Approval: {cap['approval']}   Risk: {cap['risk']}")
    print(f"Notice:    {info['notice']}")
    print(f"\n{cap['description']}\n")
    print("Parameters:")
    if not params:
        print("  (none)")
    for p in params:
        extras = []
        if p["required"]:
            extras.append("required")
        if "default" in p:
            extras.append(f"default={json.dumps(p['default'])}")
        if "choices" in p:
            extras.append("choices=" + "|".join(p["choices"]))
        if "min" in p or "max" in p:
            extras.append(f"range={p.get('min', '-inf')}..{p.get('max', 'inf')}")
        print(f"  {p['name']:<18}{p['type']:<10}{'; '.join(extras)}")
        if p["help"]:
            print(f"  {'':<18}{p['help']}")
    print(f"\nExample:\n  {info['example']}")
    return 0


def _parse_params(args):
    raw = {}
    if args.params_json:
        val = C.parse_json_value(args.params_json, "--params-json")
        if not isinstance(val, dict):
            raise ToolError("invalid_input", "--params-json must be a JSON object", field="params-json")
        raw.update(val)
    raw.update(C.parse_kv_pairs(args.param))
    return raw


def cmd_run(args):
    cfg = load_config(args.config)
    _, caps = load_catalog()
    reg = load_registry()
    cap = resolve_target(caps, reg, args.capability)
    entry = reg.get(cap["operation"])
    if entry is None:
        raise ToolError("runtime_error", f"operation '{cap['operation']}' is not registered")
    if cap["approval"] == "required" and not args.approve:
        raise ToolError("approval_required", f"{cap['number']} {cap['name']} requires explicit approval",
                        hint="Review the parameters, then re-run with --approve.")
    raw = _parse_params(args)
    if entry.get("needs") == "pillow":
        C.require_pillow()
    p = C.validate_params(entry, raw, cap.get("default_params"))
    output_dir = args.output_dir or cfg.get("output_dir") or "ai-image-toolkit-output"
    ctx = C.Context(cfg, cap, output_dir, approve=args.approve, overwrite=args.overwrite)
    ctx.protect_inputs(p, entry["params"])
    result = entry["fn"](p, ctx)
    if not isinstance(result, dict):
        result = {"value": result}
    extra_warnings = result.pop("warnings_list", []) or []
    warnings = list(ctx.warnings) + list(extra_warnings)
    envelope = {
        "ok": True,
        "capability": {"number": cap["number"], "id": cap["id"], "name": cap["name"]},
        "operation": cap["operation"],
        "execution_mode": cap["execution_mode"],
        "notice": MODE_NOTICES.get(cap["execution_mode"], ""),
        "result": result,
        "warnings": warnings,
        "artifacts": ctx.artifacts,
        "toolkit_version": C.TOOLKIT_VERSION,
    }
    print(_jdump(envelope))
    return 0


def _check(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def cmd_check_config(args):
    checks = []
    py = sys.version_info
    checks.append(_check("python", "pass" if py >= MIN_PYTHON else "fail",
                         f"{platform.python_version()} (needs >= {'.'.join(map(str, MIN_PYTHON))})"))
    for name in ASSET_FILES:
        path = HERE / name
        if not path.is_file():
            checks.append(_check(f"asset:{name}", "fail", "missing"))
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            checks.append(_check(f"asset:{name}", "pass", "valid JSON"))
        except (ValueError, OSError) as e:
            checks.append(_check(f"asset:{name}", "fail", str(e)))
    try:
        cfg = load_config(args.config)
        checks.append(_check("config", "pass", f"loaded{' with ' + args.config if args.config else ' defaults'}"))
    except ToolError as e:
        cfg = None
        checks.append(_check("config", "fail", e.message))
    try:
        _, caps = load_catalog()
        reg = load_registry()
        ids = [c["id"] for c in caps]
        numbers = [c["number"] for c in caps]
        problems = []
        if len(caps) != 184:
            problems.append(f"expected 184 capabilities, found {len(caps)}")
        if len(set(ids)) != len(ids) or len(set(numbers)) != len(numbers):
            problems.append("duplicate capability id or number")
        for c in caps:
            e = reg.get(c["operation"])
            if e is None:
                problems.append(f"{c['number']}: operation {c['operation']} not registered")
                continue
            declared = {s["name"] for s in e["params"]}
            stray = set(c.get("default_params", {})) - declared
            if stray:
                problems.append(f"{c['number']}: default_params not declared: {sorted(stray)}")
        checks.append(_check("catalog", "fail" if problems else "pass",
                             "; ".join(problems[:5]) if problems else
                             f"{len(caps)} capabilities, {len(reg)} registered operations"))
    except ToolError as e:
        checks.append(_check("catalog", "fail", e.message))
    try:
        import PIL
        checks.append(_check("pillow", "pass", f"PIL {PIL.__version__} (image.* operations available)"))
    except ImportError:
        checks.append(_check("pillow", "warn", "not installed; image.* operations will fail with dependency_missing. "
                                               "Install: python3 -m pip install -r tools-requirements.txt"))
    if cfg:
        prov = cfg["provider"]
        if not prov.get("enabled"):
            checks.append(_check("provider", "pass", "disabled (model-dependent items write job specs only)"))
        else:
            env = prov.get("api_key_env") or ""
            key_ok = (not env) or bool(os.environ.get(env))
            checks.append(_check("provider", "pass" if prov.get("endpoint") and key_ok else "fail",
                                 f"enabled; endpoint={'set' if prov.get('endpoint') else 'MISSING'}; "
                                 f"credential env {env or '(none)'}={'set' if key_ok else 'NOT SET'}"))
        out_dir = Path(args.output_dir or cfg.get("output_dir") or "ai-image-toolkit-output")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            probe = out_dir / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(_check("output_dir", "pass", f"writable: {out_dir.resolve()}"))
        except OSError as e:
            checks.append(_check("output_dir", "fail", f"not writable: {out_dir} ({e})"))
        lim = cfg["limits"]
        bad = [k for k, v in lim.items() if not isinstance(v, (int, float)) or v <= 0]
        checks.append(_check("limits", "fail" if bad else "pass",
                             f"non-positive or non-numeric: {bad}" if bad else "all limits positive"))
    failed = [c for c in checks if c["status"] == "fail"]
    summary = {"ok": not failed, "failed": len(failed), "warnings": sum(c["status"] == "warn" for c in checks),
               "toolkit_version": C.TOOLKIT_VERSION, "checks": checks}
    print(_jdump(summary))
    if failed:
        raise ToolError("invalid_input", f"{len(failed)} check(s) failed; see the report above")
    return 0


def cmd_version(args):
    print(f"ai-image-toolkit {C.TOOLKIT_VERSION}")
    return 0


# ----------------------------------------------------------------- argument parsing

def build_parser():
    p = argparse.ArgumentParser(
        prog="tools-ai-image-toolkit.py",
        description="AI Image Skill Tool: 184 catalogued capabilities behind one entry point.",
        epilog="Start with 'list', then 'describe <NUM>', then 'run <NUM> -p key=value'. "
               "Outputs default to ./ai-image-toolkit-output/<capability-id>/.")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("help", help="Show this help")
    sp = sub.add_parser("list", help="List capabilities (filter by --category / --mode)")
    sp.add_argument("--category", help="Category number (1-23) or name prefix")
    sp.add_argument("--mode", choices=sorted(MODE_NOTICES), help="Execution mode filter")
    sp.add_argument("--json", action="store_true")
    so = sub.add_parser("ops", help="List the operation registry (engine functions)")
    so.add_argument("--json", action="store_true")
    sd = sub.add_parser("describe", help="Show parameters and example for one capability")
    sd.add_argument("capability", help="Number (8.1), id, or part of the name")
    sd.add_argument("--json", action="store_true")
    sr = sub.add_parser("run", help="Run one capability")
    sr.add_argument("capability", help="Number (8.1), id, or part of the name")
    sr.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE",
                    help="Parameter (repeatable). Lists: a,b,c or JSON. JSON objects: inline or @file.json")
    sr.add_argument("--params-json", metavar="JSON|@FILE", help="All parameters as one JSON object")
    sr.add_argument("--output-dir", help="Override output directory (default from config)")
    sr.add_argument("--config", help="JSON file that overrides asset-config-default.json")
    sr.add_argument("--approve", action="store_true", help="Grant approval for conditional/required actions")
    sr.add_argument("--overwrite", action="store_true", help="Allow replacing an explicit existing output (needs --approve)")
    sc = sub.add_parser("check-config", aliases=["doctor"], help="Validate assets, config, dependencies and paths")
    sc.add_argument("--config")
    sc.add_argument("--output-dir")
    sub.add_parser("version", help="Print toolkit version")
    return p


COMMANDS = {
    "list": cmd_list, "ops": cmd_ops, "describe": cmd_describe, "run": cmd_run,
    "check-config": cmd_check_config, "doctor": cmd_check_config, "version": cmd_version,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None or args.command == "help":
        return cmd_help(args, parser)
    if sys.version_info < MIN_PYTHON:
        print(json.dumps({"ok": False, "error": {"code": "dependency_missing",
              "message": f"Python {'.'.join(map(str, MIN_PYTHON))}+ is required"}}), file=sys.stderr)
        return C.EXIT_CODES["dependency_missing"]
    try:
        return COMMANDS[args.command](args) or 0
    except ToolError as e:
        err = e.as_dict()
        # stderr carries exactly one JSON document so scripts can parse it.
        print(json.dumps({"ok": False, "error": err}, indent=2, ensure_ascii=False), file=sys.stderr)
        return C.EXIT_CODES.get(e.code, 6)
    except UnicodeDecodeError as e:
        print(json.dumps({"ok": False, "error": {"code": "invalid_input",
              "message": f"input is not UTF-8 text: {e}"}}, indent=2), file=sys.stderr)
        return C.EXIT_CODES["invalid_input"]
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "cancelled", "message": "cancelled by user"}}),
              file=sys.stderr)
        return C.EXIT_CODES["cancelled"]
    except Exception as e:  # unexpected: never show a bare traceback unless asked
        if os.environ.get("AIIMG_DEBUG"):
            traceback.print_exc()
        print(json.dumps({"ok": False, "error": {"code": "runtime_error",
              "message": f"unexpected {type(e).__name__}: {e}",
              "hint": "Set AIIMG_DEBUG=1 to see the traceback."}}, indent=2), file=sys.stderr)
        return C.EXIT_CODES["runtime_error"]


if __name__ == "__main__":
    sys.exit(main())
