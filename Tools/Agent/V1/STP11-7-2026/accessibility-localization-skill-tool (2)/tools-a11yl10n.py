#!/usr/bin/env python3
"""accessibility-and-localization Skill Tool - primary entry point.

One unified, data-driven Skill Tool built from the Accessibility and
Localization Skill Catalog (25 categories, 970 skill concepts). Every catalog
skill resolves to a real, deterministic operation through tools-catalog.py.

Usage
  python3 tools-a11yl10n.py --help
  python3 tools-a11yl10n.py capabilities
  python3 tools-a11yl10n.py list --category 9
  python3 tools-a11yl10n.py describe C09-S01
  python3 tools-a11yl10n.py run --skill "Captions and Subtitle Engine" --input page.vtt
  python3 tools-a11yl10n.py run --skill 2.25 --input page.html --set conformance_level=aa --out results
  python3 tools-a11yl10n.py validate --skill 5.1 --input page.html
  python3 tools-a11yl10n.py doctor
  python3 tools-a11yl10n.py self-test

Standard library only. Python 3.8+ (validated on 3.13).
"""

import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

MODULE_FILES = [
    ("a11y_support", "tools-support.py", ()),
    ("a11y_text", "tools-text.py", ("support",)),
    ("a11y_html", "tools-html.py", ("support", "text")),
    ("a11y_catalog", "tools-catalog.py", ("support",)),
    ("a11y_engines_web", "tools-engines-web.py", ("support", "text", "html")),
    ("a11y_engines_media", "tools-engines-media.py", ("support", "text", "html")),
    ("a11y_engines_l10n", "tools-engines-l10n.py", ("support", "text", "html")),
    ("a11y_engines_content", "tools-engines-content.py", ("support", "text", "html")),
    ("a11y_engines_gov", "tools-engines-gov.py", ("support", "text", "html")),
    ("a11y_selftest", "tools-selftest.py", ("support", "text", "html", "catalog", "engines_web",
                                            "engines_media", "engines_l10n", "engines_content",
                                            "engines_gov")),
]

ASSET_FILES = {
    "wcag": "asset-wcag-2.2.json",
    "locale_profiles": "asset-locale-profiles.json",
    "terminology": "asset-fil-en-terminology.json",
    "mqm": "asset-mqm-typology.json",
    "at_matrix": "asset-at-test-matrix.json",
}

CONFIG_FILE = "asset-config.json"

KIND_BY_EXT = {
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".css": "css",
    ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".vtt": "captions", ".srt": "captions",
    ".tmx": "tmx", ".tbx": "tbx",
    ".pdf": "document", ".docx": "document", ".pptx": "document", ".xlsx": "document", ".epub": "document",
    ".ttf": "font", ".otf": "font", ".ttc": "font", ".woff": "font", ".woff2": "font",
    ".properties": "strings", ".po": "strings", ".ini": "strings", ".resx": "strings",
    ".txt": "text", ".md": "text", ".csv": "text",
}

KIND_FALLBACKS = {
    "json": ("strings",), "html": ("text",), "text": ("strings", "json"),
    "strings": ("json",), "jsonl": ("json",), "css": ("html",), "captions": (), "tmx": (),
    "tbx": ("json",), "document": (), "font": (),
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INPUT = 2
EXIT_APPROVAL = 3
EXIT_NOT_FOUND = 4
EXIT_LIMIT = 5
EXIT_TIMEOUT = 6
EXIT_GATE_FAIL = 7


# --------------------------------------------------------------------------- #
# Module loading
# --------------------------------------------------------------------------- #
def load_modules(verbose=False):
    mods = {}
    for modname, filename, deps in MODULE_FILES:
        path = os.path.join(ROOT, filename)
        if not os.path.exists(path):
            raise SystemExit("error: required component missing: %s" % filename)
        spec = importlib.util.spec_from_file_location(modname, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover - import-time failure
            raise SystemExit("error: cannot load %s: %s" % (filename, exc))
        mods[modname] = module
        if verbose:
            print("loaded %s as %s" % (filename, modname), file=sys.stderr)
    support = mods["a11y_support"]
    deps = {"support": support, "text": mods["a11y_text"], "html": mods["a11y_html"],
            "catalog": mods["a11y_catalog"], "selftest": mods["a11y_selftest"],
            "engines_web": mods["a11y_engines_web"], "engines_media": mods["a11y_engines_media"],
            "engines_l10n": mods["a11y_engines_l10n"], "engines_content": mods["a11y_engines_content"],
            "engines_gov": mods["a11y_engines_gov"]}
    for modname, filename, moddeps in MODULE_FILES:
        module = mods[modname]
        if hasattr(module, "init"):
            module.init({k: deps[k] for k in moddeps if k in deps})
    return deps


def merge_ops(deps):
    ops = {}
    sources = {}
    for key in ("engines_web", "engines_media", "engines_l10n", "engines_content", "engines_gov"):
        module = deps[key]
        for name, meta in getattr(module, "OPS", {}).items():
            if name in ops:
                raise SystemExit("error: operation %r is defined twice (%s and %s)" % (
                    name, sources[name], key))
            ops[name] = meta
            sources[name] = key
    return ops


# --------------------------------------------------------------------------- #
# Config and assets
# --------------------------------------------------------------------------- #
def load_config(deps, path=None):
    S = deps["support"]
    default_path = os.path.join(ROOT, CONFIG_FILE)
    config = S.load_json(default_path) if os.path.exists(default_path) else {}
    if path:
        override = S.load_json(path)
        config = S.deep_merge(config, override)
    problems = S.validate_config(config)
    if problems:
        raise S.InputError("Invalid configuration: %s" % "; ".join(problems),
                           hint="Fix %s or the file passed to --config." % CONFIG_FILE)
    return config


def load_assets(deps, config):
    S = deps["support"]
    assets = {}
    for key, filename in ASSET_FILES.items():
        path = S.resolve_asset_path(ROOT, filename)
        try:
            assets[key] = S.load_json(path)
        except S.SkillToolError as exc:
            raise S.InputError("Asset %s could not be loaded: %s" % (filename, exc.message))
    assets["severity"] = config.get("severity_model", {})
    assets["maturity"] = config.get("maturity_model", {})
    return assets


def limits_from(config, args):
    S = deps_support
    limits = dict(S.DEFAULT_LIMITS)
    limits.update(config.get("limits", {}))
    if getattr(args, "timeout", None) is not None:
        limits["timeout_seconds"] = int(args.timeout)
    return limits


# --------------------------------------------------------------------------- #
# Input loading
# --------------------------------------------------------------------------- #
def detect_kind(path):
    ext = os.path.splitext(path)[1].lower()
    return KIND_BY_EXT.get(ext)


def load_input(deps, path, kind, limits):
    S, T, H = deps["support"], deps["text"], deps["html"]
    if not os.path.exists(path):
        raise S.InputError("Input file not found: %s" % path,
                           hint="Check the path (case-sensitive) or pass an existing artifact.")
    size = S.check_size(path, limits)
    digest = S.sha256_bytes(S.read_bytes(path, limits=limits))
    if kind == "html" or kind == "css":
        text = S.read_text(path, limits=limits)
        return S.InputDoc(path, kind, size, digest, text=text, data={"document": H.parse(text, path)})
    if kind == "json":
        return S.InputDoc(path, kind, size, digest, data=S.load_json(path, retries=int(limits.get("read_retries", 2))))
    if kind == "jsonl":
        text = S.read_text(path, limits=limits)
        records = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise S.InputError("Invalid JSON on line %d of %s: %s" % (i + 1, path, exc.msg),
                                   hint="Each JSONL line must be a complete JSON object.")
            if len(records) > int(limits.get("max_records", 50000)):
                raise S.LimitExceeded("JSONL records exceed limits.max_records.")
        return S.InputDoc(path, kind, size, digest, text=text, data=records)
    if kind in ("document", "font"):
        return S.InputDoc(path, kind, size, digest, data={"raw": S.read_bytes(path, limits=limits)})
    text = S.read_text(path, limits=limits)
    data = None
    if kind == "strings" and path.lower().endswith(".json"):
        data = S.load_json(path)
    return S.InputDoc(path, kind, size, digest, text=text, data=data)


def check_kind(op, kind, path):
    if kind is None:
        raise InputKindError(path)
    if kind in op["accepts"]:
        return kind
    for alt in KIND_FALLBACKS.get(kind, ()):
        if alt in op["accepts"]:
            return alt
    raise InputKindError(path, op)


class InputKindError(Exception):
    def __init__(self, path, op=None):
        self.path = path
        self.op = op
        self.ext = os.path.splitext(path)[1].lower()
        msg = "Input type %r is not usable for this operation." % (self.ext or "unknown")
        if op:
            msg += " Accepted inputs: %s." % ", ".join(op["accepts"])
        else:
            msg += " Unsupported file extension %r." % self.ext
        super().__init__(msg)


# --------------------------------------------------------------------------- #
# Parameter handling
# --------------------------------------------------------------------------- #
def parse_set_value(raw, spec, key):
    S = deps_support
    kind = spec.get("type", "str")
    if kind == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            raise S.ParamError("--set %s expects an integer, got %r." % (key, raw))
    if kind == "float":
        try:
            return float(raw)
        except ValueError:
            raise S.ParamError("--set %s expects a number, got %r." % (key, raw))
    if kind == "bool":
        low = str(raw).lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise S.ParamError("--set %s expects true/false, got %r." % (key, raw))
    if kind == "list":
        if raw.startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return [p.strip() for p in raw.split(",") if p.strip()]
    if kind == "enum":
        value = str(raw).strip()
        allowed = spec.get("values") or []
        if allowed and value not in allowed:
            raise S.ParamError("--set %s must be one of %s, got %r." % (key, ", ".join(allowed), value),
                               hint="Values are case-sensitive.")
        return value
    return raw


def build_params(op, set_pairs):
    S = deps_support
    params = {}
    for key, spec in (op.get("params") or {}).items():
        if "default" in spec:
            params[key] = spec["default"]
    for pair in set_pairs or []:
        if "=" not in pair:
            raise S.ParamError("--set expects key=value, got %r." % pair)
        key, raw = pair.split("=", 1)
        key = key.strip()
        if key not in (op.get("params") or {}):
            raise S.ParamError("Unknown parameter %r for operation %r." % (key, op["name"]),
                              hint="Known parameters: %s" % (", ".join(sorted(op.get("params") or {})) or "(none)"))
        params[key] = parse_set_value(raw.strip(), op["params"][key], key)
    for key, spec in (op.get("params") or {}).items():
        if spec.get("required") and params.get(key) in (None, ""):
            raise S.ParamError("Parameter %r is required for operation %r." % (key, op["name"]))
        if isinstance(params.get(key), (int, float)) and not isinstance(params.get(key), bool):
            if "min" in spec and params[key] < spec["min"]:
                raise S.ParamError("Parameter %r must be >= %s." % (key, spec["min"]))
            if "max" in spec and params[key] > spec["max"]:
                raise S.ParamError("Parameter %r must be <= %s." % (key, spec["max"]))
    return params


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
deps = None
deps_support = None
OPS = {}


def cmd_capabilities(args):
    S = deps["support"]
    catalog = deps["catalog"].load(ROOT)
    summary = catalog.categories_summary()
    if args.category:
        summary = [c for c in summary if c["number"] == args.category]
        if not summary:
            raise S.ResolutionError("No category numbered %s." % args.category,
                                    hint="Categories are 1-%d." % len(catalog.categories))
    payload = {
        "skill_tool": "accessibility-and-localization",
        "catalog": catalog.data.get("name"),
        "category_count": len(catalog.categories),
        "skill_count": len(catalog.skills),
        "operation_count": len(OPS),
        "categories": summary,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    print("accessibility-and-localization  |  %d categories, %d skill concepts, %d operations" % (
        payload["category_count"], payload["skill_count"], payload["operation_count"]))
    print("")
    print("%-4s %-58s %5s  %s" % ("#", "Category", "Skills", "Operations"))
    print("-" * 110)
    for c in summary:
        ops = ", ".join("%s(%d)" % (k, v) for k, v in sorted(c["operations"].items(), key=lambda kv: -kv[1]))
        print("%-4d %-58s %5d  %s" % (c["number"], c["name"][:58], c["skills"], ops))
    print("")
    print("Next: `list --category N`, `describe <skill>`, `run --skill <skill> --input <artifact>`")
    return EXIT_OK


def cmd_list(args):
    S = deps["support"]
    catalog = deps["catalog"].load(ROOT)
    rows = []
    for sk in catalog.skills:
        if args.category and sk["category"]["number"] != args.category:
            continue
        op_name, how = catalog.op_for(sk)
        if args.op and op_name != args.op:
            continue
        rows.append({"id": sk["id"], "index": sk["index"], "category": sk["category"]["number"],
                     "name": sk["name"], "op": op_name, "via": how,
                     "implemented": op_name in OPS})
    rows = rows[args.offset: args.offset + (args.limit or len(rows))]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return EXIT_OK
    print("%-9s %-4s %-56s %-24s %s" % ("Skill id", "Cat", "Skill name", "Operation", "Route"))
    print("-" * 120)
    for r in rows:
        print("%-9s %-4d %-56s %-24s %s%s" % (r["id"], r["category"], r["name"][:56], r["op"], r["via"],
                                              "" if r["implemented"] else "  [MISSING]"))
    print("")
    print("%d skill(s) listed. Operations are shared engines; see `describe op:<name>`." % len(rows))
    return EXIT_OK


def cmd_search(args):
    catalog = deps["catalog"].load(ROOT)
    matches = catalog.search(args.text, limit=args.limit)
    if not matches:
        print("No skills match %r." % args.text)
        return EXIT_OK
    for sk in matches:
        op_name, _ = catalog.op_for(sk)
        print("%-9s %-56s [%s] %s" % (sk["id"], sk["name"][:56], sk["category"]["name"][:28], op_name))
    return EXIT_OK


def cmd_describe(args):
    S = deps["support"]
    catalog = deps["catalog"].load(ROOT)
    target = args.target
    if target.lower().startswith("op:"):
        target = target[3:]
    if target in OPS:
        op_name = target
        op = OPS.get(op_name)
        if not op:
            raise S.ResolutionError("Unknown operation %r." % op_name,
                                    hint="Operations: %s" % ", ".join(sorted(OPS)))
        skills = [s for s in catalog.skills if catalog.op_for(s)[0] == op_name]
        payload = {"operation": op_name, "engine": op["engine"], "summary": op["summary"],
                   "doc": op["doc"], "accepts": op["accepts"],
                   "parameters": op["params"], "requires_approval": bool(op.get("approval")),
                   "approval_modes": op.get("modes") or [],
                   "skills_routed_here": len(skills),
                   "sample_skills": [{"id": s["id"], "name": s["name"]} for s in skills[:12]]}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return EXIT_OK
        print("Operation: %s  (engine: %s)" % (op_name, op["engine"]))
        print("", op["doc"])
        print("Accepts:", ", ".join(op["accepts"]))
        print("Approval required:", "yes - modes %s" % (op.get("modes") or ["always"])
              if op.get("approval") else "no")
        print("Skills routed here: %d" % len(skills))
        print("Parameters:")
        if not op["params"]:
            print("  (none)")
        for key, spec in op["params"].items():
            print("  --set %-28s %-6s default=%s%s" % (key, spec.get("type", "str"),
                                                       spec.get("default"),
                                                       "" if "values" not in spec else "  values=" + ",".join(map(str, spec["values"]))))
            if spec.get("doc"):
                print("      %s" % spec["doc"])
        return EXIT_OK
    skill = catalog.resolve(target)
    op_name, how = catalog.op_for(skill)
    op = OPS.get(op_name, {})
    payload = {
        "skill": {"id": skill["id"], "name": skill["name"], "slug": skill["slug"],
                  "category": skill["category"], "description": skill.get("description")},
        "operation": op_name, "routed_by": how, "implemented": bool(op),
        "accepts": op.get("accepts", []), "parameters": op.get("params", {}),
        "engine": op.get("engine"),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    print("%s  %s" % (skill["id"], skill["name"]))
    print("Category : %d. %s" % (skill["category"]["number"], skill["category"]["name"]))
    print("Purpose  : %s" % skill.get("description"))
    print("Operation: %s (%s, engine %s)" % (op_name, how, op.get("engine", "?")))
    print("Accepts  : %s" % ", ".join(op.get("accepts", [])))
    print("")
    print("Run:")
    print("  python3 tools-a11yl10n.py run --skill %s --input <artifact>" % skill["id"])
    if op.get("params"):
        print("Parameters: %s" % ", ".join(sorted(op["params"])))
    return EXIT_OK


def _prepare_run(args):
    S = deps["support"]
    catalog = deps["catalog"].load(ROOT)
    skill = catalog.resolve(args.skill)
    op_name, how = catalog.op_for(skill)
    op = OPS.get(op_name)
    if not op:
        raise S.ResolutionError("Skill %s routes to unimplemented operation %r." % (skill["id"], op_name))
    config = load_config(deps, args.config)
    limits = limits_from(config, args)
    assets = load_assets(deps, config)
    params = build_params(op, args.set)
    return S, catalog, skill, op_name, how, op, config, limits, assets, params


def _inputs(args, op, limits):
    paths = []
    if args.input:
        paths.append(args.input)
    if args.reference:
        paths.append(args.reference)
    if not paths:
        raise deps_support.InputError("--input is required for this command.",
                                      hint="Example: --input page.html")
    primary_kind = check_kind(op, detect_kind(paths[0]), paths[0])
    docs = [load_input(deps, paths[0], primary_kind, limits)]
    for extra in paths[1:]:
        kind = detect_kind(extra)
        if kind is None:
            raise deps_support.InputError("Reference file type is not supported: %s" % extra)
        docs.append(load_input(deps, extra, kind, limits))
    return docs


def _approval_needed(op, params, approved):
    if not op.get("approval"):
        return False
    modes = op.get("modes") or []
    if not modes:
        return True
    for key in ("mode", "store_mode"):
        if params.get(key) in modes:
            return True
    return False


def cmd_run(args):
    S, catalog, skill, op_name, how, op, config, limits, assets, params = _prepare_run(args)
    params["_dry_run"] = bool(getattr(args, "dry_run", False))
    params["_approved"] = bool(getattr(args, "approve", False))
    if _approval_needed(op, params, args.approve) and not args.approve:
        raise S.ApprovalRequired("%s(%s)" % (op_name, params.get("mode") or "always"))
    docs = _inputs(args, op, limits)
    ctx = S.Context(skill, op_name, params, limits, config, assets,
                    ruleset={"tool": "accessibility-and-localization", "catalog": catalog.data.get("name"),
                             "edition": catalog.data.get("edition"), "operation": op_name,
                             "engine": op.get("engine")})
    ctx.docs = docs
    for d in docs:
        if d.kind == "json" and isinstance(d.data, dict) and "schema" in d.data:
            ctx.ruleset.setdefault("upstream_schema", d.data.get("schema"))
    status = "ok"
    error = None
    started = time.time()
    out_dir = args.out or config.get("output", {}).get("dir") or "a11yl10n-out"
    written = []
    try:
        op["fn"](ctx)
    except S.DeadlineExceeded as exc:
        status, error = "timeout", exc.as_dict()
    except S.SkillToolError as exc:
        status, error = "error", exc.as_dict()
    except KeyboardInterrupt:
        status, error = "cancelled", {"error": {"code": "cancelled", "message": "Cancelled by operator."}}
    except Exception as exc:  # defensive: never lose evidence silently
        status = "error"
        error = {"error": {"code": "internal_error", "message": str(exc),
                           "trace": S.tail_trace()}}
    result = S.build_result(ctx, status=status, error=error)
    result["approval"] = {"approved": bool(args.approve), "operation_requires_approval": bool(op.get("approval")),
                          "modes_requiring_approval": op.get("modes") or []} if op.get("approval") else None
    if result["approval"] is None:
        result.pop("approval")
    if not args.no_write:
        fmt = args.format
        base = os.path.join(out_dir, skill["slug"])
        os.makedirs(out_dir, exist_ok=True)
        if fmt in ("both", "json"):
            written.append(S.atomic_write(base + ".json", S.render_result(result, "json")))
        if fmt in ("both", "md"):
            written.append(S.atomic_write(base + ".md", S.render_result(result, "md")))
        if fmt == "sarif":
            written.append(S.atomic_write(base + ".sarif", S.render_result(result, "sarif")))
        if fmt == "junit":
            written.append(S.atomic_write(base + ".xml", S.render_result(result, "junit")))
    summary = result["summary"]
    if args.json or args.quiet:
        print(json.dumps(result if args.json else
                         {"skill": skill["id"], "operation": op_name, "status": status,
                          "findings": summary["findings"], "by_severity": summary["by_severity"],
                          "pass": summary["pass"], "written": written,
                          "error": error}, indent=2, ensure_ascii=False))
    else:
        print("%s  %s  ->  %s" % (skill["id"], skill["name"], op_name))
        print("status: %s   findings: %d (critical %d, high %d, medium %d, low %d, info %d)" % (
            status, summary["findings"], summary["by_severity"]["critical"], summary["by_severity"]["high"],
            summary["by_severity"]["medium"], summary["by_severity"]["low"], summary["by_severity"]["info"]))
        print("checks: %d run / %d passed   gate: %s" % (summary["checks_run"], summary["checks_passed"],
                                                         "PASS" if summary["pass"] else "FAIL"))
        if result.get("notes"):
            for n in result["notes"][:6]:
                print("note: %s" % n["message"])
        for path in written:
            print("wrote: %s" % path)
        if error:
            print("error: %s" % json.dumps(error, ensure_ascii=False))
    if status == "timeout":
        return EXIT_TIMEOUT
    if status in ("error", "cancelled"):
        return EXIT_ERROR
    if not summary["pass"] and args.fail_on_findings:
        return EXIT_GATE_FAIL
    return EXIT_OK


def cmd_validate(args):
    S = deps["support"]
    report = {"command": "validate", "checks": [], "ok": True}
    try:
        S2, catalog, skill, op_name, how, op, config, limits, assets, params = _prepare_run(args)
    except S.SkillToolError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, indent=2))
        return exc.exit_code
    report["skill"] = {"id": skill["id"], "name": skill["name"], "operation": op_name}
    report["checks"].append({"check": "skill_resolved", "ok": True, "detail": "%s via %s" % (op_name, how)})
    report["checks"].append({"check": "parameters_valid", "ok": True,
                             "detail": {k: v for k, v in params.items() if not k.startswith("_")}})
    report["checks"].append({"check": "config_valid", "ok": True, "detail": CONFIG_FILE})
    report["checks"].append({"check": "assets_loaded", "ok": True, "detail": sorted(assets.keys())})
    needs_approval = _approval_needed(op, params, args.approve)
    report["checks"].append({"check": "approval", "ok": not needs_approval or bool(args.approve),
                             "detail": "required" if needs_approval else "not required"})
    if args.input:
        try:
            docs = _inputs(args, op, limits)
            report["checks"].append({"check": "input_accepted", "ok": True,
                                     "detail": [d.record() for d in docs]})
        except S.InputKindError as exc:
            report["ok"] = False
            report["checks"].append({"check": "input_accepted", "ok": False, "detail": str(exc)})
        except S.SkillToolError as exc:
            report["ok"] = False
            report["checks"].append({"check": "input_accepted", "ok": False, "detail": exc.message})
    else:
        report["checks"].append({"check": "input_accepted", "ok": True, "detail": "no input supplied"})
    if any(not c["ok"] for c in report["checks"]):
        report["ok"] = False
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return EXIT_OK if report["ok"] else EXIT_INPUT


def cmd_doctor(args):
    S = deps["support"]
    catalog = deps["catalog"].load(ROOT)
    report = {"python": sys.version.split()[0], "root": ".", "checks": [], "ok": True}

    def add(name, ok, detail):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            report["ok"] = False

    add("python_version", sys.version_info >= (3, 8), "requires 3.8+, found %s" % sys.version.split()[0])
    try:
        config = load_config(deps, args.config)
        add("config", True, "%d keys" % len(config))
    except Exception as exc:
        add("config", False, str(exc))
    for key, filename in ASSET_FILES.items():
        path = os.path.join(ROOT, filename)
        add("asset:%s" % key, os.path.exists(path), filename)
    add("catalog", True, "%d categories / %d skills" % (len(catalog.categories), len(catalog.skills)))
    routing = deps["catalog"].op_routing_report(catalog, OPS)
    missing = [r for r in routing if not r["implemented"]]
    add("routing", not missing, "%d/%d skills routed to implemented operations" % (
        len(routing) - len(missing), len(routing)))
    add("operations", len(OPS) >= 40, "%d operations across %d engines" % (
        len(OPS), len({m["engine"] for m in OPS.values()})))
    out_dir = args.out or "a11yl10n-out"
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".write-probe")
        S.atomic_write(probe, "ok")
        os.unlink(probe)
        add("output_writable", True, out_dir)
    except OSError as exc:
        add("output_writable", False, str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return EXIT_OK if report["ok"] else EXIT_ERROR


def cmd_routing(args):
    catalog = deps["catalog"].load(ROOT)
    rows = deps["catalog"].op_routing_report(catalog, OPS)
    if args.unimplemented:
        rows = [r for r in rows if not r["implemented"]]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return EXIT_OK
    for r in rows:
        print("%-9s %-56s -> %s" % (r["id"], r["name"][:56], r["op"]))
    print("\n%d row(s); %d implemented operations available." % (len(rows), len(OPS)))
    return EXIT_OK


def cmd_selftest(args):
    module = deps["selftest"]
    return module.run(deps, OPS, ROOT, verbose=args.verbose, keep=args.keep)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-a11yl10n.py",
        description="Unified accessibility and localization Skill Tool: 25 categories, 970 catalog skills, "
                    "one deterministic operation registry.",
        epilog="Examples:\n"
               "  python3 tools-a11yl10n.py capabilities\n"
               "  python3 tools-a11yl10n.py list --category 9 --op captions-audit\n"
               "  python3 tools-a11yl10n.py describe 2.25\n"
               "  python3 tools-a11yl10n.py run --skill 2.25 --input page.html\n"
               "  python3 tools-a11yl10n.py run --skill 'Captions and Subtitle Engine' --input subs.vtt --format both\n"
               "  python3 tools-a11yl10n.py validate --skill 5.1 --input page.html\n"
               "  python3 tools-a11yl10n.py doctor\n"
               "  python3 tools-a11yl10n.py self-test --verbose\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to a config JSON merged over asset-config.json")
    parser.add_argument("--timeout", type=int, help="override limits.timeout_seconds for this run")
    parser.add_argument("--verbose", action="store_true", help="print module loading detail to stderr")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("capabilities", help="list categories with skill counts and operations")
    p.add_argument("--category", type=int, help="restrict to one category number")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("list", help="list skills with their routed operation")
    p.add_argument("--category", type=int)
    p.add_argument("--op", help="only skills routed to this operation")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="search skills by name, description or category")
    p.add_argument("text")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("describe", help="describe a skill (id/slug/name) or an operation (op:<name>)")
    p.add_argument("target")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("run", help="execute the operation a skill routes to")
    p.add_argument("--skill", required=True, help="skill id (C02-S25), list number (2.25), slug, or name")
    p.add_argument("--input", help="primary input artifact")
    p.add_argument("--reference", help="secondary input (source bundle, baseline, memory)")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="operation parameter (repeatable)")
    p.add_argument("--out", help="output directory (default: config output.dir)")
    p.add_argument("--format", default="both", choices=["both", "json", "md", "sarif", "junit"])
    p.add_argument("--dry-run", action="store_true", help="compute results without state-changing writes")
    p.add_argument("--approve", action="store_true", help="approve a state-changing or publishing operation")
    p.add_argument("--no-write", action="store_true", help="do not write result files")
    p.add_argument("--json", action="store_true", help="print the full result document")
    p.add_argument("--quiet", action="store_true", help="print a one-line JSON summary")
    p.add_argument("--fail-on-findings", action="store_true",
                   help="exit 7 when blocking findings are present (CI gate behaviour)")
    p.add_argument("--timeout", type=int, default=argparse.SUPPRESS,
                   help="override limits.timeout_seconds for this run")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("validate", help="validate skill, parameters, config and input without executing")
    p.add_argument("--skill", required=True)
    p.add_argument("--input")
    p.add_argument("--reference")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.add_argument("--approve", action="store_true")
    p.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("doctor", help="check runtime, assets, routing and output writability")
    p.add_argument("--out", help="directory to test for writability (default: config output.dir)")
    p.add_argument("--json", action="store_true", help="accepted for symmetry; doctor always emits JSON")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("routing", help="print the skill -> operation routing table")
    p.add_argument("--json", action="store_true")
    p.add_argument("--unimplemented", action="store_true")
    p.set_defaults(func=cmd_routing)

    p = sub.add_parser("self-test", help="run the built-in validation suite against every engine")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--keep", action="store_true", help="keep temp fixtures for inspection")
    p.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None):
    global deps, deps_support, OPS
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    deps = load_modules(verbose=args.verbose)
    deps_support = deps["support"]
    OPS = merge_ops(deps)
    try:
        return args.func(args)
    except deps_support.SkillToolError as exc:
        payload = exc.as_dict()
        payload["exit_code"] = exc.exit_code
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return exc.exit_code
    except InputKindError as exc:
        payload = {"error": {"code": "input_type_error", "message": str(exc),
                             "hint": "Convert the artifact or choose a skill that accepts this input type."}}
        print(json.dumps(payload, indent=2))
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
