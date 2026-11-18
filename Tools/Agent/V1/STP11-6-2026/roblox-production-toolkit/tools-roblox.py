#!/usr/bin/env python3
"""roblox-production-toolkit: primary command-line entry point.

Python 3.10+ standard library only. Run `python3 tools-roblox.py help` for usage.

Responsibilities:
- Load the shared runtime (tools-core.py as rbx_core) and every family module.
- Route each of the 334 algorithm labels in asset-recipes.json to exactly one handler.
- Parse user-supplied capability lists (plain text, Markdown, numbered lists, copied HTML) with
  tools-source.py, and resolve them to catalog capabilities.
- Validate inputs, run one capability or a unified batch, and commit results transactionally.
"""
import json
import os
import re
import signal
import sys
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
CAP_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FAMILY_FILES = [
    ("engine", "tools-engine.py", "rbx_engine"),
    ("structure", "tools-structure.py", "rbx_structure"),
    ("simulate", "tools-simulate.py", "rbx_simulate"),
    ("content", "tools-content.py", "rbx_content"),
    ("code", "tools-code.py", "rbx_code"),
    ("cloud", "tools-cloud.py", "rbx_cloud"),
]


def _load_core():
    name = "rbx_core"
    if name in sys.modules:
        return sys.modules[name]
    path = HERE / "tools-core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()
_STATE = {}


def _source_module():
    return core.load_component("tools-source.py", "rbx_source")


def _load_routes():
    """Build label -> (family, handler). Every label must resolve to exactly one handler."""
    if "routes" in _STATE:
        return _STATE["routes"]
    routes, owners = {}, {}
    for family, filename, module_name in FAMILY_FILES:
        module = core.load_component(filename, module_name)
        for label, fn in module.HANDLERS.items():
            if label in routes:
                core.fail("ROUTE_CONFLICT", "Two handlers claim the same label.", label=label, families=[owners[label], family])
            routes[label] = (family, fn)
            owners[label] = family
    _STATE["routes"] = routes
    return routes


def _catalog():
    if "catalog" not in _STATE:
        _STATE["catalog"] = core.res_json("asset-catalog.json")
        _STATE["recipes"] = core.res_json("asset-recipes.json")["recipes"]
    return _STATE["catalog"], _STATE["recipes"]


def _config(path=None):
    base = core.res_json("asset-default-config.json")
    if path:
        p = Path(path).expanduser()
        if not p.is_file() or p.is_symlink():
            core.fail("RESOURCE_MISSING", "Configuration file not found.", path=str(path))
        if p.stat().st_size > 256 * 1024:
            core.fail("INPUT_LIMIT", "Configuration file is too large.")
        override = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            core.fail("CONFIG_INVALID", "Configuration must be a JSON object.")
        base = core.deep_merge(base, override)
    for key in ("limits", "network", "studio", "policy", "paths"):
        if key not in base or not isinstance(base[key], dict):
            core.fail("CONFIG_INVALID", "Configuration is missing a required section.", section=key)
    return base


def _capability(cap_id):
    catalog, recipes = _catalog()
    if not isinstance(cap_id, str) or not CAP_ID_RE.match(cap_id):
        core.fail("INPUT_INVALID", "Capability ids are lowercase words joined by hyphens.", capability=str(cap_id)[:80])
    row = next((c for c in catalog["capabilities"] if c[0] == cap_id), None)
    if row is None:
        core.fail("NOT_FOUND", "Unknown capability. Run `capabilities` to list ids.", capability=cap_id)
    recipe = recipes[cap_id]
    cats = {c["id"]: c for c in catalog["categories"]}
    family, fn = _load_routes()[recipe["algorithm"]]
    category = cats[recipe["category_id"]]
    return {"id": row[0], "name": row[1], "description": row[2], "category_id": category["id"], "category": category["name"],
            "engine": recipe["engine"], "algorithm": recipe["algorithm"], "family": family, "focus": recipe["focus"],
            "controls": recipe["controls"], "example": recipe["example"], "acceptance": recipe["acceptance"],
            "effect": recipe["effect"], "handler": fn}


def _all_capabilities():
    catalog, _ = _catalog()
    return [_capability(c[0]) for c in catalog["capabilities"]]


def _slug(value):
    return _source_module().slug(value)


def _parse_json_arg(value, field):
    if value is None:
        return None
    if value.startswith("@"):
        p = Path(value[1:]).expanduser()
        if not p.is_file() or p.is_symlink():
            core.fail("RESOURCE_MISSING", "Input file not found.", path=value[1:])
        if p.stat().st_size > core.MAX_BYTES:
            core.fail("INPUT_LIMIT", "Input file exceeds the input size limit.")
        text = p.read_text(encoding="utf-8")
    elif value == "-":
        text = sys.stdin.read(core.MAX_BYTES + 1)
        if len(text) > core.MAX_BYTES:
            core.fail("INPUT_LIMIT", "Standard input exceeds the input size limit.")
    else:
        text = value
    if len(text.encode("utf-8")) > core.MAX_BYTES:
        core.fail("INPUT_LIMIT", f"{field} exceeds the input size limit.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        core.fail("INPUT_INVALID", f"{field} is not valid JSON.", line=exc.lineno, column=exc.colno)
    if not isinstance(data, dict):
        core.fail("INPUT_INVALID", f"{field} must be a JSON object.")
    core.check_json(data)
    return data


def _apply_sets(data, sets):
    for item in sets or []:
        if "=" not in item:
            core.fail("INPUT_INVALID", "--set expects key=value.", value=item[:80])
        key, raw = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            core.fail("INPUT_INVALID", "--set key must be a simple name.", key=key[:80])
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        data[key] = value
    return data


def _check_output(path):
    p = Path(path).expanduser().resolve()
    if HERE == p or HERE in p.parents:
        core.fail("OUTPUT_PATH", "Output must be outside the toolkit folder.", path=str(p))
    return p


def _envelope_check(envelope):
    core.validate_schema(envelope, core.res_json("asset-request-schema.json"))
    return envelope


def _need(opts, name):
    if name not in opts:
        core.fail("USAGE", f"{name} is required.")
    return opts[name][0]


def _run_handler(cap, input_data, ctx):
    ctx.check()
    result = cap["handler"](input_data, cap, ctx)
    if not isinstance(result, dict) or "result" not in result or "findings" not in result:
        core.fail("INTERNAL", "Handler returned an unexpected structure.", capability=cap["id"])
    for f in result["findings"]:
        if f.get("status") not in ("pass", "fail", "review"):
            core.fail("INTERNAL", "Handler returned an invalid finding.", capability=cap["id"])
    return result


def _summary(findings):
    return {"passed": sum(f["status"] == "pass" for f in findings), "failed": sum(f["status"] == "fail" for f in findings),
            "review": sum(f["status"] == "review" for f in findings), "total": len(findings)}


BOUNDARIES = [
    "Offline computation and generation only; no Roblox Studio, DCC, or live service is contacted by this capability.",
    "Generated Luau is text. It is not compiled or executed here; run luau-analyze or Studio tests before use.",
    "Heuristic checks are lint aids; policy and compliance checks require human review.",
]


def _write_run(cap, input_data, ctx, out_dir, result, overwrite, approve):
    findings = result["findings"]
    summary = _summary(findings)
    payload = {"schema_version": SCHEMA_VERSION, "status": "ok", "capability": cap["id"], "name": cap["name"],
               "category": cap["category"], "family": cap["family"], "algorithm": cap["algorithm"],
               "result": result["result"], "findings": findings, "summary": summary,
               "warnings": result["warnings"] + ctx.warnings, "artifacts": sorted(result["artifacts"].keys())}
    writer = core.Writer(out_dir, ctx, max_files=ctx.config["limits"]["output_files"], max_bytes=ctx.config["limits"]["output_bytes"])
    writer.add("result.json", payload, "json")
    writer.add("report.md", core.report(cap["name"], result["result"], findings, BOUNDARIES + [f"Input summary keys: {', '.join(sorted(input_data)) or 'none'}."]))
    for name, content in result["artifacts"].items():
        if isinstance(content, (dict, list)):
            writer.add("artifacts/" + name, content, "json")
        else:
            writer.add("artifacts/" + name, str(content))
    writer.commit()
    return [str(out_dir / n) for n in writer.files], summary


def _error_payload(exc):
    if isinstance(exc, core.ToolError):
        return {"schema_version": SCHEMA_VERSION, "status": "error", "error": exc.dict(), "exit_code": exc.status}
    return {"schema_version": SCHEMA_VERSION, "status": "error",
            "error": {"code": "INTERNAL", "message": "Unexpected internal error.", "details": {"type": type(exc).__name__}}, "exit_code": 70}


def _emit(payload, code=0):
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=False) + "\n")
    return code


# ---------------------------------------------------------------- commands

HELP = """roblox-production-toolkit (tools-roblox.py)

Usage: python3 tools-roblox.py <command> [options]

Commands:
  help                         Show this help.
  capabilities [--category X] [--family F] [--engine E] [--query TEXT] [--json]
                               List the 340 capabilities with ids, categories and families.
  describe ID [--json]         Show a capability's purpose, controls, acceptance and example keys.
  example ID                   Print the capability's example input (JSON).
  inspect --input TEXT|@FILE|-  Parse a plain, Markdown, numbered, or copied-HTML list and resolve it.
  validate --capability ID --input JSON|@FILE|- [--set k=v] [--config FILE]
                               Validate and dry-run one capability in memory; writes nothing.
  run --capability ID --input JSON|@FILE|- --output DIR [--set k=v] [--config FILE]
      [--approve] [--overwrite] [--timeout SECONDS] [--dry-run] [--use-examples] [--request FILE]
                               Run one capability and write result.json, report.md and artifacts/.
  batch --input TEXT|@FILE|- [--category X ...] [--capability ID ...] [--inputs-file FILE]
      [--use-examples] --output DIR [--approve] [--overwrite] [--timeout SECONDS] [--config FILE]
                               Run a unified set of capabilities into DIR/<umbrella>/.
  self-test                    Run the built-in tests (tools-selftest.py) against this folder.
  check-package [--root DIR]   Verify the component folder layout, references and routing.

Exit codes: 0 success; 2 usage or validation error; 3 findings failed with --fail-on-findings;
77 approval required; 73 output exists without --overwrite; 124 timeout; 130 cancelled.
Outputs default to ./roblox-output/<capability>; outputs inside this folder are refused.
Run `describe ID` before `run` for the exact input fields.
"""


def cmd_help(args):
    sys.stdout.write(HELP)
    return 0


def cmd_capabilities(args):
    opts = _options(args, {"--category": "many", "--family": "one", "--engine": "one", "--query": "one", "--json": "flag"})
    rows = _all_capabilities()
    cats = [c.lower() for c in opts.get("--category", [])]
    if cats:
        rows = [r for r in rows if r["category"].lower() in cats or str(r["category_id"]) in cats or _slug(r["category"]) in cats]
    if "--family" in opts:
        rows = [r for r in rows if r["family"] == opts["--family"][0]]
    if "--engine" in opts:
        rows = [r for r in rows if r["engine"] == opts["--engine"][0]]
    if "--query" in opts:
        q = opts["--query"][0].lower()
        rows = [r for r in rows if q in r["name"].lower() or q in r["id"] or q in r["description"].lower()]
    if "--json" in opts:
        return _emit({"schema_version": SCHEMA_VERSION, "count": len(rows), "capabilities": [
            {k: r[k] for k in ("id", "name", "category", "engine", "algorithm", "family")} for r in rows]})
    lines = [f"{len(rows)} capability(ies)", ""]
    for r in rows:
        lines.append(f"{r['id']:<42} {r['category'][:34]:<34} {r['family']:<10} {r['name']}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_describe(args):
    opts = _options(args, {"--json": "flag"}, positional=1)
    cap = _capability(opts["_pos"][0])
    info = {k: cap[k] for k in ("id", "name", "description", "category", "engine", "algorithm", "family", "focus", "controls", "effect", "acceptance")}
    info["example_keys"] = sorted(cap["example"].keys())
    info["example_input"] = cap["example"]
    info["run"] = f"python3 tools-roblox.py run --capability {cap['id']} --use-examples --output ./roblox-output/{cap['id']}"
    return _emit({"schema_version": SCHEMA_VERSION, "status": "ok", "capability": info})


def cmd_example(args):
    opts = _options(args, {}, positional=1)
    cap = _capability(opts["_pos"][0])
    sys.stdout.write(json.dumps(cap["example"], indent=2, ensure_ascii=True) + "\n")
    return 0


def _resolve_selection(text, cat_names, cap_ids):
    catalog, _ = _catalog()
    source = _source_module()
    selected = []
    unresolved = []
    if text:
        try:
            parsed = source.parse_source(text, known_categories=[c["name"] for c in catalog["categories"]])
        except ValueError as exc:
            core.fail("INPUT_INVALID", "The list could not be parsed.", reason=str(exc))
        sel = source.select_capabilities(parsed, catalog, _category_map())
        selected.extend(sel["capabilities"])
        unresolved.extend(sel["unresolved"])
        parsed_info = {"categories": [c["name"] for c in parsed["categories"]], "capability_count": parsed["capability_count"],
                       "warnings": parsed["warnings"][:50]}
    else:
        parsed_info = {"categories": [], "capability_count": 0, "warnings": []}
    for name in cat_names:
        matched = _find_category(name)
        if matched is None:
            core.fail("NOT_FOUND", "Unknown category.", category=name, hint="Use the category name or id from `capabilities`.")
        selected.extend(r[0] for r in catalog["capabilities"] if _category_map()[r[0]] == matched["id"])
        parsed_info["categories"].append(matched["name"])
    for cid in cap_ids:
        _capability(cid)
        selected.append(cid)
    ids = list(dict.fromkeys(selected))
    if not ids:
        core.fail("INPUT_INVALID", "No capabilities were selected.", hint="Pass --input with a list, --category, or --capability.")
    return ids, unresolved, parsed_info


def _category_map():
    if "catmap" not in _STATE:
        catalog, recipes = _catalog()
        _STATE["catmap"] = {cid: recipes[cid]["category_id"] for cid, *_ in catalog["capabilities"]}
    return _STATE["catmap"]


def _find_category(name):
    catalog, _ = _catalog()
    key = re.sub(r"\s+", " ", name.strip().lower())
    for c in catalog["categories"]:
        if key in (c["name"].lower(), str(c["id"]), _slug(c["name"])):
            return c
    return None


def _umbrella(ids):
    catalog, recipes = _catalog()
    cats = {c["id"]: c for c in catalog["categories"]}
    used = list(dict.fromkeys(recipes[i]["category_id"] for i in ids))
    if len(used) == 1:
        return _slug(cats[used[0]]["name"])
    engines = {}
    for i in ids:
        engines[recipes[i]["engine"]] = engines.get(recipes[i]["engine"], 0) + 1
    top = sorted(engines.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return _slug(top + " suite")


def cmd_inspect(args):
    opts = _options(args, {"--input": "one", "--category": "many", "--capability": "many"})
    text = _text_input(opts["--input"][0] if "--input" in opts else None)
    ids, unresolved, info = _resolve_selection(text, opts.get("--category", []), opts.get("--capability", []))
    return _emit({"schema_version": SCHEMA_VERSION, "status": "ok", "umbrella": _umbrella(ids), "parsed": info,
                  "selected": [{"id": i, "name": _capability(i)["name"], "family": _capability(i)["family"]} for i in ids],
                  "unresolved": unresolved})


def _text_input(value):
    if value is None:
        return None
    if value.startswith("@"):
        p = Path(value[1:]).expanduser()
        if not p.is_file() or p.is_symlink():
            core.fail("RESOURCE_MISSING", "Input file not found.", path=value[1:])
        if p.stat().st_size > core.MAX_BYTES:
            core.fail("INPUT_LIMIT", "Input file exceeds the input size limit.")
        return p.read_text(encoding="utf-8")
    if value == "-":
        return sys.stdin.read(core.MAX_BYTES)
    if len(value.encode("utf-8")) > core.MAX_BYTES:
        core.fail("INPUT_LIMIT", "Input exceeds the input size limit.")
    return value


def _resolve_input(opts, cap):
    options, data = {}, None
    if "--request" in opts:
        env = _envelope_check(_parse_json_arg(opts["--request"][0], "request"))
        if env.get("capability") != cap["id"]:
            core.fail("INPUT_INVALID", "Request capability does not match the selected capability.", request=env.get("capability"), capability=cap["id"])
        options = env.get("options", {})
        data = dict(env["input"])
    if "--input" in opts:
        data = _parse_json_arg(opts["--input"][0], "input")
    if data is None:
        if "--use-examples" in opts:
            data = json.loads(json.dumps(cap["example"]))
        else:
            core.fail("INPUT_REQUIRED", "Input is required. Pass --input, use --use-examples, or run `example ID`.", capability=cap["id"])
    data = _apply_sets(dict(data), opts.get("--set", []))
    return data, options


def _ctx(cfg, output, timeout, approve, overwrite):
    ctx = core.Context(cfg, output, timeout=timeout, approved=approve, approve_overwrite=overwrite)
    return ctx


def _timeout(opts, cfg):
    if "--timeout" in opts:
        t = float(opts["--timeout"][0])
        if not 1 <= t <= cfg["limits"]["max_timeout_seconds"]:
            core.fail("INPUT_INVALID", "--timeout must be between 1 and the configured maximum.", maximum=cfg["limits"]["max_timeout_seconds"])
        return t
    return float(cfg["limits"]["timeout_seconds"])


def cmd_validate(args):
    opts = _options(args, {"--capability": "one", "--input": "one", "--request": "one", "--set": "many", "--config": "one",
                           "--use-examples": "flag", "--timeout": "one"})
    cap = _capability(_need(opts, "--capability"))
    cfg = _config(opts["--config"][0] if "--config" in opts else None)
    data, _ = _resolve_input(opts, cap)
    ctx = _ctx(cfg, Path.cwd(), _timeout(opts, cfg), False, False)
    result = _run_handler(cap, data, ctx)
    return _emit({"schema_version": SCHEMA_VERSION, "status": "ok", "capability": cap["id"], "valid": True,
                  "writes": False, "summary": _summary(result["findings"]), "findings": result["findings"], "result": result["result"]})


def cmd_run(args):
    opts = _options(args, {"--capability": "one", "--input": "one", "--request": "one", "--output": "one", "--set": "many",
                           "--config": "one", "--approve": "flag", "--overwrite": "flag", "--timeout": "one",
                           "--dry-run": "flag", "--use-examples": "flag", "--fail-on-findings": "flag"})
    if "--capability" in opts:
        cap_id = opts["--capability"][0]
    elif "--request" in opts:
        cap_id = _envelope_check(_parse_json_arg(opts["--request"][0], "request")).get("capability")
    else:
        core.fail("USAGE", "--capability or --request is required.")
    cap = _capability(cap_id)
    cfg = _config(opts["--config"][0] if "--config" in opts else None)
    data, env_options = _resolve_input(opts, cap)
    approve = "--approve" in opts or bool(env_options.get("approve"))
    overwrite = "--overwrite" in opts or bool(env_options.get("overwrite"))
    dry = "--dry-run" in opts or bool(env_options.get("dry_run"))
    out_dir = _check_output(opts["--output"][0]) if "--output" in opts else _check_output(Path.cwd() / "roblox-output" / cap["id"])
    timeout = _timeout(opts, cfg) if "--timeout" in opts else float(env_options.get("timeout_seconds", cfg["limits"]["timeout_seconds"]))
    ctx = _ctx(cfg, out_dir, timeout, approve, overwrite)
    result = _run_handler(cap, data, ctx)
    if dry:
        return _emit({"schema_version": SCHEMA_VERSION, "status": "ok", "capability": cap["id"], "dry_run": True, "writes": False,
                      "would_write": sorted(["result.json", "report.md"] + ["artifacts/" + a for a in result["artifacts"]]),
                      "summary": _summary(result["findings"]), "result": result["result"]})
    written, summary = _write_run(cap, data, ctx, out_dir, result, overwrite, approve)
    payload = {"schema_version": SCHEMA_VERSION, "status": "ok", "capability": cap["id"], "dry_run": False, "output": str(out_dir),
               "written": written, "summary": summary, "warnings": ctx.warnings}
    code = 3 if "--fail-on-findings" in opts and summary["failed"] else 0
    return _emit(payload, code)


def cmd_batch(args):
    opts = _options(args, {"--input": "one", "--category": "many", "--capability": "many", "--inputs-file": "one",
                           "--use-examples": "flag", "--output": "one", "--approve": "flag", "--overwrite": "flag",
                           "--timeout": "one", "--config": "one", "--fail-on-findings": "flag"})
    cfg = _config(opts["--config"][0] if "--config" in opts else None)
    text = _text_input(opts["--input"][0] if "--input" in opts else None)
    ids, unresolved, info = _resolve_selection(text, opts.get("--category", []), opts.get("--capability", []))
    if len(ids) > cfg["limits"]["batch_capabilities"]:
        core.fail("INPUT_LIMIT", "Too many capabilities for one batch.", selected=len(ids), limit=cfg["limits"]["batch_capabilities"])
    inputs_map = {}
    if "--inputs-file" in opts:
        inputs_map = _parse_json_arg(opts["--inputs-file"][0], "inputs-file")
    umbrella = _umbrella(ids)
    out_root = _check_output(opts["--output"][0]) if "--output" in opts else _check_output(Path.cwd() / "roblox-output" / "batch")
    unit = _timeout(opts, cfg)
    approve = "--approve" in opts
    overwrite = "--overwrite" in opts
    rows, artifacts_all = [], {}
    cancelled = False
    for cap_id in ids:
        if core.CANCEL.is_set():
            cancelled = True
            break
        cap = _capability(cap_id)
        entry = {"id": cap_id, "name": cap["name"], "category": cap["category"], "family": cap["family"]}
        try:
            if cap_id in inputs_map:
                data = inputs_map[cap_id]
                if not isinstance(data, dict):
                    core.fail("INPUT_INVALID", "Each inputs-file entry must be an object.", capability=cap_id)
            elif "--use-examples" in opts:
                data = json.loads(json.dumps(cap["example"]))
            else:
                core.fail("INPUT_REQUIRED", "No input for this capability; pass --inputs-file or --use-examples.", capability=cap_id)
            ctx = _ctx(cfg, out_root / umbrella, unit, approve, overwrite)
            result = _run_handler(cap, data, ctx)
            entry.update(status="ok", summary=_summary(result["findings"]), result=result["result"], findings=result["findings"])
            artifacts_all[cap_id] = result["artifacts"]
        except core.ToolError as exc:
            if exc.code == "CANCELLED":
                cancelled = True
                break
            entry.update(status="error", error=exc.dict(), exit_code=exc.status)
        except Exception as exc:  # noqa: BLE001 - recorded as structured error, batch continues
            entry.update(status="error", error={"code": "INTERNAL", "message": "Unexpected internal error.", "details": {"type": type(exc).__name__}})
        rows.append(entry)
    totals = {"requested": len(ids), "completed": sum(r["status"] == "ok" for r in rows), "errors": sum(r["status"] == "error" for r in rows),
              "not_run": len(ids) - len(rows), "findings_failed": sum(r.get("summary", {}).get("failed", 0) for r in rows)}
    document = {"schema_version": SCHEMA_VERSION, "umbrella": umbrella, "cancelled": cancelled, "totals": totals,
                "source": {"parsed": info, "unresolved": unresolved}, "capabilities": rows}
    bundle = out_root / umbrella
    writer = core.Writer(bundle, _ctx(cfg, bundle, unit, approve, overwrite), max_files=cfg["limits"]["batch_output_files"], max_bytes=cfg["limits"]["output_bytes"])
    writer.add("unified-results.json", document, "json")
    lines = [f"# Unified report: {umbrella}", "", f"- Capabilities requested: {totals['requested']}", f"- Completed: {totals['completed']}",
             f"- Errors: {totals['errors']}", f"- Not run (cancelled): {totals['not_run']}", f"- Findings failed: {totals['findings_failed']}", "",
             "## Capabilities", "", "| Capability | Category | Family | Status | Pass | Fail | Review |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        s = r.get("summary", {"passed": 0, "failed": 0, "review": 0})
        status = r["status"] if r["status"] == "ok" else f"error: {r['error']['code']}"
        lines.append(f"| {r['id']} | {r['category']} | {r['family']} | {status} | {s['passed']} | {s['failed']} | {s['review']} |")
    lines += ["", "## Boundaries", ""] + ["- " + b for b in BOUNDARIES]
    writer.add("unified-report.md", "\n".join(lines) + "\n")
    for cap_id, arts in artifacts_all.items():
        for name, content in arts.items():
            if isinstance(content, (dict, list)):
                writer.add(f"artifacts/{cap_id}/{name}", content, "json")
            else:
                writer.add(f"artifacts/{cap_id}/{name}", str(content))
    writer.commit()
    payload = {"schema_version": SCHEMA_VERSION, "status": "ok" if not cancelled else "cancelled", "umbrella": umbrella,
               "output": str(bundle), "written": [str(bundle / n) for n in writer.files], "totals": totals}
    code = 130 if cancelled else (3 if "--fail-on-findings" in opts and totals["findings_failed"] else 0)
    return _emit(payload, code)


def cmd_self_test(args):
    selftest = core.load_component("tools-selftest.py", "rbx_selftest")
    report = selftest.run_all(HERE)
    _emit(report)
    return 0 if report["status"] == "ok" else 1


def cmd_check_package(args):
    opts = _options(args, {"--root": "one"})
    root = Path(opts["--root"][0]).expanduser().resolve() if "--root" in opts else HERE
    selftest = core.load_component("tools-selftest.py", "rbx_selftest")
    result = selftest.check_package(root)
    _emit(result)
    return 0 if result["status"] == "ok" else 1


COMMANDS = {"help": cmd_help, "capabilities": cmd_capabilities, "describe": cmd_describe, "example": cmd_example,
            "inspect": cmd_inspect, "validate": cmd_validate, "run": cmd_run, "batch": cmd_batch,
            "self-test": cmd_self_test, "check-package": cmd_check_package}


def _options(args, spec, positional=0):
    out = {"_pos": []}
    i = 0
    while i < len(args):
        a = args[i]
        if a in spec:
            kind = spec[a]
            if kind == "flag":
                out[a] = True
            else:
                if i + 1 >= len(args):
                    core.fail("USAGE", "Option needs a value.", option=a)
                value = args[i + 1]
                i += 1
                if kind == "many":
                    out.setdefault(a, []).append(value)
                else:
                    out[a] = [value]
        elif a.startswith("--"):
            core.fail("USAGE", "Unknown option.", option=a, hint="Run `help`.")
        else:
            out["_pos"].append(a)
        i += 1
    if len(out["_pos"]) != positional:
        core.fail("USAGE", f"Expected {positional} positional argument(s).", got=len(out["_pos"]))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGINT, lambda *_: core.CANCEL.set())
    if not argv or argv[0] in ("help", "-h", "--help"):
        return cmd_help([])
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        return _emit(_error_payload(core.ToolError("USAGE", "Unknown command.", {"command": command, "commands": sorted(COMMANDS)}, 2)), 2)
    try:
        return COMMANDS[command](rest) or 0
    except core.ToolError as exc:
        return _emit(_error_payload(exc), exc.status)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _emit(_error_payload(core.ToolError("IO_ERROR", "Input or output could not be read.", {"type": type(exc).__name__}, 2)), 2)


if __name__ == "__main__":
    sys.exit(main())
