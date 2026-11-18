#!/usr/bin/env python3
"""tools-rwt.py - Primary entry point: Offline Research, Writing & Translation Skill Tool.

One unified offline tool implementing the full research-writing-translation
capability catalog (288 skills). Run `rwt help` for overview,
`rwt capabilities` to list every capability, `rwt skill <id> --help` for a
skill's parameters. No network access is required or used.

Usage:
  rwt init PATH [--name NAME] [--preset NAME] [--force]
  rwt capabilities [--category N] [--automation CLASS] [--search TEXT] [--format text|json]
  rwt skill <id-or-command> [engine args...]
  rwt <engine> [action] [args...]          # direct engine access
  rwt audit verify [args...]                # alias for workflow audit-verify
  rwt doctor [--workspace PATH] [--mirror PATH]
  rwt version

Global options may appear anywhere: --format json|text (default json).
Exit codes: 0 ok | 1 unexpected | 2 validation/usage | 3 unavailable (e.g., no
optional local model configured) | 4 blocked (human gate/approval required).
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback

TOOL_VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# --- module loading -------------------------------------------------------
_MODULES = {}
ENGINES = ["core", "corpus", "search", "citation", "text", "numbers", "claims",
           "translation", "locale", "extract", "media", "collections",
           "workflow", "security", "deliver", "models", "benchmark", "design",
           "selftest"]

def engine_path(name):
    return os.path.join(HERE, f"tools-rwt-{name}.py")

def load_engine(name):
    if name in _MODULES:
        return _MODULES[name]
    import importlib.util
    path = engine_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Engine module missing: {path}")
    spec = importlib.util.spec_from_file_location(f"rwt_engine_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"rwt_engine_{name}"] = mod
    spec.loader.exec_module(mod)
    _MODULES[name] = mod
    return mod

CORE = None
def core():
    global CORE
    if CORE is None:
        CORE = load_engine("core")
    return CORE

def load_catalog():
    path = os.path.join(HERE, "asset-skill-catalog.json")
    if not os.path.isfile(path):
        raise SystemExit("catalog asset missing: asset-skill-catalog.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)

def rwt_error_code(exc):
    """Duck-typed exit code for RWT errors.

    Engines load their own copies of the core module, so isinstance() against
    this file's core fails. The core error contract guarantees a class name
    ending in 'Error' (RwtError/ValidationError/UnavailableError/BlockedError)
    and an integer exit_code attribute — use those.
    """
    name = type(exc).__name__
    if name in ("RwtError", "ValidationError", "UnavailableError", "BlockedError"):
        code = getattr(exc, "exit_code", None)
        if isinstance(code, int):
            return code
        return {"ValidationError": 2, "UnavailableError": 3, "BlockedError": 4}.get(name, 1)
    code = getattr(exc, "exit_code", None)
    if isinstance(code, int) and type(exc).__name__.endswith("Error") and 1 <= code <= 4:
        return code
    return None


# --- catalog command -> engine binding ------------------------------------
# (engine, [action, ...]) overrides where the catalog prefix differs from
# the engine name or the action name differs from the catalog suffix.
BINDINGS = {
    "doctor": (None, ["doctor"]),           # primary built-in
    "doctor.mirror": (None, ["doctor"]),  # pass: rwt skill 23.10 --mirror PATH
    "init": (None, ["init"]),
    "audit.verify": ("workflow", ["audit-verify"]),
    # collections
    "collections.records": ("collections", ["records-list"]),
    # provenance -> collections engine
    "provenance.manifest": ("collections", ["provenance-manifest"]),
    "provenance.verify-chain": ("collections", ["provenance-verify-chain"]),
    "provenance.gaps": ("collections", ["provenance-gaps"]),
    "provenance.snapshot-id": ("collections", ["provenance-snapshot-id"]),
    "provenance.lineage": ("collections", ["provenance-lineage"]),
    "provenance.run-manifest": ("collections", ["provenance-run-manifest"]),
    # freshness -> collections engine
    "freshness.check": ("collections", ["freshness-check"]),
    "freshness.score": ("collections", ["freshness-score"]),
    "freshness.schedule": ("collections", ["freshness-schedule"]),
    # workflow
    "workflow.queue": ("workflow", ["queue-list"]),
    "workflow.decisions": ("workflow", ["decisions-list"]),
    "workflow.schedule": ("workflow", ["schedule-due"]),
    # review (systematic review methodology) lives in workflow engine
    "review.plan": ("workflow", ["review-plan"]),
    "review.prisma": ("workflow", ["prisma"]),
    "review.riskofbias": ("workflow", ["riskofbias"]),
    "review.evidence-table": ("workflow", ["evidence-table"]),
    "review.synthesis": ("workflow", ["synthesis"]),
    "review.living-update": ("workflow", ["living-update"]),
    "review.robustness": ("workflow", ["robustness"]),
    # terms store -> translation engine
    "terms.store": ("translate", ["terms-lookup"]),
    "terms.extract": ("translate", ["terms-extract"]),
    "terms.consistency": ("translate", ["terms-consistency"]),
    "terms.acronyms": ("translate", ["terms-acronyms"]),
    "terms.inflect": ("translate", ["terms-inflect"]),
    "terms.entity-policy": ("translate", ["terms-entity-policy"]),
    "terms.definition-audit": ("translate", ["terms-definition-audit"]),
    "terms.glossary-build": ("translate", ["terms-glossary-build"]),
    # translation memory
    "tm.store": ("translate", ["tm-match"]),
    "tm.align": ("translate", ["tm-align"]),
    "tm.clean": ("translate", ["tm-clean"]),
    "qa.score": ("translate", ["qa-score"]),
    "qa.taxonomy": ("translate", ["qa-taxonomy"]),
    "langid.detect": ("translate", ["langid"]),
    "drift.monitor": ("translate", ["drift"]),
    "translate.document": ("translate", ["translate-document"]),
    "translate.plan": ("translate", ["translate-plan"]),
    "translate.summarize": ("models", ["summarize"]),
    "translate.domain-pack": ("translate", ["domain-pack"]),
    # extract-time
    "time.normalize": ("extract", ["time-normalize"]),
    # similarity attribution -> text engine
    "similarity.scan": ("text", ["similarity-scan"]),
    "similarity.patchwrite": ("text", ["similarity-patchwrite"]),
    "attribution.check": ("text", ["attribution-check"]),
    # security
    "security.import-gate": ("security", ["scan-media"]),
    "security.redact": ("security", ["redact-scan"]),
    # deliver
    "deliver.multilingual-consistency": ("deliver", ["multilingual"]),
    # writing engines -> design engine
    "writing.draft": ("design", ["draft"]),
    "writing.outline": ("design", ["outline"]),
    "writing.methods": ("design", ["methods"]),
}

DEFAULT_ENGINE_ALIASES = {
    "translate": "translation",
    "rwt": "core",
}

def bind_command(command):
    if command in BINDINGS:
        engine, argv = BINDINGS[command]
        return (DEFAULT_ENGINE_ALIASES.get(engine, engine) if engine else None), argv
    if "." in command:
        prefix, rest = command.split(".", 1)
        engine = DEFAULT_ENGINE_ALIASES.get(prefix, prefix)
        return engine, [rest]
    return None, [command]

# --- output ---------------------------------------------------------------
def emit(obj, fmt):
    if fmt == "text":
        print(core().render_text(obj))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

def envelope(command, result, warnings=None, extra=None, duration_ms=None):
    return core().make_result(command, result, warnings=warnings,
                              extra={**(extra or {}), "duration_ms": duration_ms})

def _blocked(result):
    if not isinstance(result, dict):
        return False
    if result.get("gate") == "blocked":
        return True
    if result.get("blocked") is True:
        return True
    return False


def skill_exit_code(env):
    return 4 if env.get("blocked") else 0


def elapsed_ms(start):
    return int((time.time() - start) * 1000)

# --- built-in commands ------------------------------------------------------
def cmd_init(args):
    ws = core().Workspace(args.path)
    if args.preset:
        presets = core().load_asset("asset-config-presets.json").get("presets", [])
        known = [p.get("name") for p in presets]
        if args.preset not in known:
            raise core().ValidationError(f"Unknown preset '{args.preset}'. Available: {known}")
    cfg = ws.create(args.name, preset=args.preset, force=args.force)
    return {"initialized": os.path.join(os.path.abspath(args.path), core().CONFIG_PATH),
            "name": cfg["name"], "preset": cfg["preset"], "roles": cfg["roles"],
            "next": ["rwt capabilities", "rwt corpus ingest --file SOURCE --workspace PATH",
                     "rwt skill 20.3 --workspace PATH"]}

def cmd_capabilities(args, fmt):
    catalog = load_catalog()
    skills = catalog
    if args.category is not None:
        skills = [s for s in skills if s["category"] == args.category]
    if args.automation:
        skills = [s for s in skills if s["automation"] == args.automation.lower()]
    if args.search:
        q = args.search.lower()
        skills = [s for s in skills if q in s["name"].lower() or q in s["description"].lower()
                  or q in s["id"] or q in s["command"].lower()]
    if fmt == "text":
        lines = [f"Offline RWT Skill Tool — {len(skills)} of {len(catalog)} capabilities"]
        by_cat = {}
        for s in skills:
            by_cat.setdefault((s["category"], s["category_name"]), []).append(s)
        for (cat_id, cat_name) in sorted(by_cat):
            lines.append(f"\n[{cat_id}] {cat_name}")
            for s in by_cat[(cat_id, cat_name)]:
                lines.append(f"  {s['id']:>5}  {s['name']}  ({s['command']}, {s['automation']})")
        print("\n".join(lines))
        return None
    by_auto = {}
    by_cat = {}
    for s in catalog:
        by_auto[s["automation"]] = by_auto.get(s["automation"], 0) + 1
        by_cat[str(s["category"])] = by_cat.get(str(s["category"]), 0) + 1
    return {"total": len(catalog), "shown": len(skills),
            "by_automation": by_auto, "by_category": by_cat,
            "skills": skills,
            "honesty": "deterministic = fully implemented offline; assistive = deterministic checks + human review routing; model-adapter = needs a configured local command (rwt models), otherwise exit 3."}

def cmd_skill(args, fmt):
    catalog = load_catalog()
    needle = args.id_or_command
    skill = None
    if needle.count(".") == 1 and needle.split(".")[0].isdigit():
        skill = next((s for s in catalog if s["id"] == needle), None)
    if skill is None:
        matches = [s for s in catalog if s["command"] == needle]
        skill = matches[0] if matches else None
    if skill is None:
        close = [s["id"] for s in catalog if needle.lower() in s["name"].lower()][:8]
        raise core().ValidationError(
            f"No skill with id or command '{needle}'. Name matches: {close}. "
            f"List all with: rwt capabilities")
    engine_name, action_argv = bind_command(skill["command"])
    start = time.time()
    if engine_name is None:  # built-in
        builtin_args = list(args.rest)
        result = dispatch_builtin(action_argv[0], builtin_args, fmt)
        duration = elapsed_ms(start)
        return envelope(f"skill {skill['id']}", result, extra={"skill": skill_summary(skill)}, duration_ms=duration)
    mod = load_engine(engine_name)
    argv = action_argv + list(args.rest)
    result = mod.run(argv)
    duration = elapsed_ms(start)
    if isinstance(result, dict) and result.get("__exit__"):
        raise core().RwtError(result.get("message", "engine failed"), exit_code=result["__exit__"])
    env = envelope(f"skill {skill['id']}", result, extra={"skill": skill_summary(skill)}, duration_ms=duration)
    if _blocked(result):
        env["blocked"] = True
    return env

def skill_summary(skill):
    return {"id": skill["id"], "name": skill["name"],
            "category": skill["category_name"],
            "command": skill["command"], "automation": skill["automation"]}

def dispatch_builtin(name, argv, fmt):
    parser = argparse.ArgumentParser(prog=f"rwt {name}", exit_on_error=False)
    if name == "init":
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("--name"); parser.add_argument("--preset")
        parser.add_argument("--force", action="store_true")
        args = parser.parse_args(argv)
        return cmd_init(args)
    if name == "doctor":
        return cmd_doctor(argv)
    raise core().ValidationError(f"Unknown builtin: {name}")

def cmd_doctor(argv):
    parser = argparse.ArgumentParser(prog="rwt doctor")
    parser.add_argument("--workspace")
    parser.add_argument("--mirror", help="Inventory a local dependency mirror directory")
    args = parser.parse_args(argv)
    checks = []
    py_ok = sys.version_info >= (3, 9)
    checks.append({"check": "python>=3.9", "ok": py_ok, "detail": sys.version.split()[0]})

    for engine in ENGINES:
        path = engine_path(engine)
        ok = os.path.isfile(path)
        detail = path if ok else "MISSING"
        if ok:
            try:
                load_engine(engine)
                detail = "loads"
            except Exception as exc:
                ok = False
                detail = f"load failed: {exc}"
        checks.append({"check": f"engine:{engine}", "ok": ok, "detail": detail})

    for asset in sorted(f for f in os.listdir(HERE) if f.startswith("asset-")):
        path = os.path.join(HERE, asset)
        ok = True
        detail = "present"
        if asset.endswith(".json"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    json.load(fh)
                detail = "parses"
            except Exception as exc:
                ok = False; detail = f"invalid JSON: {exc}"
        checks.append({"check": f"asset:{asset}", "ok": ok, "detail": detail})

    presets_path = os.path.join(HERE, "asset-config-presets.json")
    checks.append({"check": "entry-point", "ok": os.access(os.path.join(HERE, "tools-rwt.py"), os.R_OK),
                   "detail": "tools-rwt.py readable"})

    if args.workspace:
        ws_path = args.workspace
        exists = os.path.isfile(os.path.join(ws_path, core().CONFIG_PATH))
        checks.append({"check": "workspace-initialized", "ok": exists,
                       "detail": core().CONFIG_PATH if exists else f"run: rwt init {ws_path}"})
        if exists:
            try:
                ws = core().Workspace(ws_path)
                ws.load()
                checks.append({"check": "workspace-config-parses", "ok": True, "detail": "ok"})
                try:
                    audit = ws.verify_audit()
                    checks.append({"check": "audit-chain", "ok": audit.get("ok", False),
                                   "detail": f"entries={audit.get('entries')}"})
                except Exception as exc:
                    checks.append({"check": "audit-chain", "ok": False, "detail": str(exc)})
                for sub in (core().STORES_DIR, core().FILES_DIR, core().SNAPSHOTS_DIR, core().EXPORTS_DIR):
                    full = os.path.join(ws_path, sub)
                    checks.append({"check": f"dir-writable:{sub}", "ok": os.path.isdir(full) and os.access(full, os.W_OK),
                                   "detail": full if os.path.isdir(full) else "missing"})
            except Exception as exc:
                checks.append({"check": "workspace-config-parses", "ok": False, "detail": str(exc)})

    mirror_report = None
    if args.mirror:
        mirror_report = inspect_mirror(args.mirror)
        checks.append({"check": "dependency-mirror", "ok": mirror_report["ok"], "detail": mirror_report["verdict"]})

    usage = shutil.disk_usage(".")
    checks.append({"check": "disk-space", "ok": usage.free > 100 * 1024 * 1024,
                   "detail": f"free={usage.free // (1024 * 1024)}MB"})

    hard_fail = [c for c in checks if not c["ok"]]
    report = {"checks": checks, "failed": [c["check"] for c in hard_fail],
              "verdict": "PASS" if not hard_fail else "FAIL",
              "mirror": mirror_report,
              "note": "PASS means this installation is structurally sound; it is not a substitute for running the self-tests (rwt selftest clean)."}
    if hard_fail:
        raise core().ValidationError(f"doctor found failing checks: {', '.join(c['check'] for c in hard_fail)}")
    return report

def inspect_mirror(path):
    if not os.path.isdir(path):
        return {"ok": False, "verdict": f"not a directory: {path}", "files": 0}
    inventory = []
    total_bytes = 0
    by_ext = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            total_bytes += size
            ext = os.path.splitext(name)[1].lower()
            by_ext[ext] = by_ext.get(ext, 0) + 1
            inventory.append((name, size))
    lock_like = [n for n, _ in inventory if n.lower().endswith(("requirements.txt", "constraints.txt", ".lock", "poetry.lock", "pipfile.lock")) or n.lower() == "packages.md"]
    artifacts = sum(by_ext.get(e, 0) for e in (".whl", ".tar.gz", ".gguf", ".bin", ".onnx", ".safetensors"))
    return {"ok": True, "verdict": f"{len(inventory)} files, {total_bytes // (1024 * 1024)}MB; {artifacts} model/package artifacts; lock-like files: {lock_like or 'none'}",
            "files": len(inventory), "bytes": total_bytes, "by_extension": by_ext,
            "lock_files": lock_like,
            "note": "Presence inventory only — completeness cannot be proven without a signed mirror manifest, which this tool does not fabricate. Verify by rehearsing a full offline install from the mirror."}

HELP_TEXT = """Offline RWT — one unified offline Skill Tool for research, writing & translation.

Quick start:
  rwt version                     tool + Python version
  rwt doctor [--workspace DIR]    environment health check
  rwt capabilities                list all 288 capabilities (JSON)
  rwt capabilities --format text  human-readable capability list
  rwt init my-project             create a .rwt workspace in my-project/
  rwt skill 6.1 --workspace my-project --query "electrolysis"   run a catalog skill
  rwt corpus ingest --file paper.txt --workspace my-project      direct engine command

Direct engines (each has --help):
  corpus search citation text numbers claims translate locale extract media
  collections workflow security deliver models benchmark design selftest

Catalog skills: rwt skill <id> with id like 6.1 (see rwt capabilities).
Output: --format json|text (default json). Exit codes: 0 ok, 1 error,
2 validation/usage, 3 unavailable (optional local model missing), 4 blocked (human gate).
"""

def strip_format_args(argv):
    fmt = "json"
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "--format" and i + 1 < len(argv):
            fmt = argv[i + 1].lower()
            if fmt not in ("json", "text"):
                raise core().ValidationError("--format must be json or text")
            i += 2
            continue
        if argv[i].startswith("--format="):
            fmt = argv[i].split("=", 1)[1].lower()
            if fmt not in ("json", "text"):
                raise core().ValidationError("--format must be json or text")
            i += 1
            continue
        out.append(argv[i])
        i += 1
    return out, fmt

def main():
    start = time.time()
    try:
        argv, fmt = strip_format_args(sys.argv[1:])
    except Exception as exc:
        print(json.dumps({"error": str(exc)})); sys.exit(2)

    if not argv or argv[0] in ("help", "--help", "-h"):
        print(HELP_TEXT)
        sys.exit(0 if argv else 1)

    cmd, rest = argv[0], argv[1:]

    try:
        if cmd == "version":
            emit({"tool": "Offline Research, Writing & Translation Skill Tool",
                  "version": TOOL_VERSION,
                  "name": "offline-research-writing-translation",
                  "python": sys.version.split()[0],
                  "runtime": "python3-stdlib-only; no network required"}, fmt)
            sys.exit(0)

        if cmd == "capabilities":
            parser = argparse.ArgumentParser(prog="rwt capabilities")
            parser.add_argument("--category", type=int)
            parser.add_argument("--automation", choices=["deterministic", "assistive", "model-adapter"])
            parser.add_argument("--search")
            ns = parser.parse_args(rest)
            result = cmd_capabilities(ns, fmt)
            if result is not None:
                emit(envelope("capabilities", result, duration_ms=elapsed_ms(start)), fmt)
            sys.exit(0)

        if cmd in ("skill",):
            parser = argparse.ArgumentParser(prog="rwt skill")
            parser.add_argument("id_or_command")
            parser.add_argument("rest", nargs=argparse.REMAINDER)
            ns = parser.parse_args(rest)
            result = cmd_skill(ns, fmt)
            emit(result, fmt)
            sys.exit(skill_exit_code(result))

        if cmd == "audit":
            mod = load_engine("workflow")
            sub = rest[0] if rest else "verify"
            argv2 = (["audit-verify"] + rest[1:]) if sub == "verify" else rest
            out = mod.run(argv2)
            emit(envelope("audit verify", out, duration_ms=elapsed_ms(start)), fmt)
            sys.exit(0)

        if cmd == "init":
            out = dispatch_builtin("init", rest, fmt)
            emit(envelope("init", out, duration_ms=elapsed_ms(start)), fmt)
            sys.exit(0)

        if cmd == "doctor":
            out = cmd_doctor(rest)
            emit(envelope("doctor", out, duration_ms=elapsed_ms(start)), fmt)
            sys.exit(0)

        engine_name = DEFAULT_ENGINE_ALIASES.get(cmd, cmd)
        if engine_name in ENGINES and engine_name != "core":
            mod = load_engine(engine_name)
            out = mod.run(rest)
            if isinstance(out, int):
                sys.exit(out)
            emit(envelope("rwt " + cmd, out, duration_ms=elapsed_ms(start)), fmt)
            sys.exit(4 if _blocked(out) else 0)

        if cmd == "core":
            print("Use: rwt <engine> [args...]; engines:", ", ".join(e for e in ENGINES if e != "core"))
            sys.exit(1)

        print(json.dumps({"error": f"Unknown command '{cmd}'. Try: rwt help"}))
        sys.exit(2)
    except SystemExit:
        raise
    except Exception as exc:
        code = rwt_error_code(exc)
        if code is not None:
            sys.exit(_fail_quiet(f"rwt {cmd}", exc, code, fmt))
        emit(core().make_result(f"rwt {cmd}",
                                {"error": f"{type(exc).__name__}: {exc}",
                                 "traceback_tail": traceback.format_exc().splitlines()[-5:]},
                                warnings=["unexpected error"]), fmt)
        sys.exit(1)


def _fail_quiet(command, exc, code, fmt):
    """RwtError-compatible failure printer that does not depend on class identity."""
    name = type(exc).__name__
    message = getattr(exc, "message", str(exc))
    details = getattr(exc, "details", {}) or {}
    if fmt == "text":
        print(f"ERROR [{name}] {message}", file=sys.stderr)
        if details:
            print(json.dumps(details, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(json.dumps({"ok": False, "command": command,
                          "error": {"type": name, "message": message, "details": details}},
                         ensure_ascii=False, indent=2))
    return code

if __name__ == "__main__":
    main()
