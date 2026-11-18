#!/usr/bin/env python3
"""spriteforge: primary entry point for the whole sprite production toolkit.

This is the file to run first. It lists the capabilities, proves the runtime is
healthy, routes a plain-language request to the right modules, and dispatches to
any module operation while passing through exit codes and reports.

Quick start
-----------
    python3 tools-spriteforge.py --help
    python3 tools-spriteforge.py doctor
    python3 tools-spriteforge.py capabilities --category "Animation and Motion"
    python3 tools-spriteforge.py route --query "eight direction walk cycle atlas unity"
    python3 tools-spriteforge.py sheet compose -i frames/ -o hero.png --metadata hero.json

Every module can also be run directly, for example
``python3 tools-spriteforge-sheet.py compose --help``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_hub", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_hub"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
CAPABILITY_ASSET = HERE / "asset-sf-capability-catalog.json"

MODULES = {
    "author": ("tools-spriteforge-author.py",
               "create: briefs, shape-layer sprites, palettes, voxel renders, tokens, rig plans"),
    "plan": ("tools-spriteforge-plan.py",
             "plan: catalog parsing, routing, matrices, estimates, records, handoff"),
    "sheet": ("tools-spriteforge-sheet.py",
              "assemble: compose, extract, atlas pack, metadata, previews, tiles"),
    "anim": ("tools-spriteforge-anim.py",
             "animate: timing, tweens, loop repair, baked paths, playback review"),
    "edit": ("tools-spriteforge-edit.py",
             "edit: crop/scale/outline, cleanup, recolour, compositing"),
    "convert": ("tools-spriteforge-convert.py",
                "export: formats, animated output, SVG subset, naming, batch"),
    "validate": ("tools-spriteforge-validate.py",
                 "check: sheets, flicker, silhouette, palette, anchors, regression, policy"),
    "analyze": ("tools-spriteforge-analyze.py",
                "inspect: catalogues, duplicates, masks, depth maps, review pages"),
    "engine": ("tools-spriteforge-engine.py",
               "integrate: Unity, Godot, Unreal, GameMaker, RPG Maker, web, mobile, retro"),
    "govern": ("tools-spriteforge-govern.py",
               "govern: style gates, licence rollups, batch write approval"),
    "pipeline": ("tools-spriteforge-pipeline.py",
                 "orchestrate: multi-step runs, recovery, sharding, watch plans"),
    "selftest": ("tools-spriteforge-selftest.py",
                 "verify: run every operation against a throwaway project (no subcommand)"),
}


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #

def load_capabilities() -> list[dict]:
    if not CAPABILITY_ASSET.exists():
        raise SFError("ASSET_MISSING", f"missing {CAPABILITY_ASSET.name} beside this file")
    data = core.load_json(CAPABILITY_ASSET)
    if not isinstance(data, list):
        raise SFError("ASSET_INVALID", f"{CAPABILITY_ASSET.name} must be a JSON list")
    return data


def flatten(caps: list[dict]) -> list[dict]:
    out = []
    for cat in caps:
        for skill in cat.get("skills", []):
            entry = dict(skill)
            entry["category"] = cat["name"]
            entry["category_id"] = cat.get("id")
            out.append(entry)
    return out


def cmd_capabilities(args, root: Path, rep: core.Reporter) -> dict:
    """List the bundled capability catalog, optionally filtered."""
    caps = load_capabilities()
    skills = flatten(caps)
    if args.category:
        wanted = {core.slugify(c) for c in args.category}
        skills = [s for s in skills if core.slugify(s["category"]) in wanted]
    if args.module:
        skills = [s for s in skills if s["module"].endswith(f"{args.module}.py")]
    if args.search:
        needle = args.search.lower()
        skills = [s for s in skills if needle in s["name"].lower() or needle in s["description"].lower()]
    if args.id:
        skills = [s for s in skills if s["id"] in set(core.parse_int_list(args.id, name="ids"))]
    if not skills:
        raise SFError("NO_MATCH", "no capability matched the filters",
                      hint="try --search with a shorter word, or run without filters")
    payload = {
        "total": len(skills),
        "categories": len(caps),
        "capabilities": [
            {"id": s["id"], "category": s["category"], "name": s["name"],
             "description": s["description"], "primitive": s["primitive"],
             "module": s["module"], "operation": s["operation"],
             "implementation": s["implementation"],
             "approval_required": s.get("approval_required", False),
             "run": f"python3 {s['module']} {s['operation']} --help"}
            for s in skills
        ],
    }
    if not args.quiet and not args.json:
        if args.full:
            for s in payload["capabilities"]:
                print(f"[{s['id']:3d}] {s['category']} / {s['name']}")
                print(f"      {s['description']}")
                print(f"      how: {s['implementation']}")
                print(f"      -> {s['primitive']}  ({s['run']})")
        else:
            by_cat: dict[str, list[dict]] = {}
            for s in payload["capabilities"]:
                by_cat.setdefault(s["category"], []).append(s)
            for cat, items in by_cat.items():
                print(f"\n{cat} ({len(items)})")
                for s in items:
                    flag = " [approval]" if s["approval_required"] else ""
                    print(f"  {s['id']:3d}  {s['name']:<44} {s['primitive']}{flag}")
            print(f"\n{payload['total']} capabilities shown. Use --full for descriptions, "
                  f"--json for machine output.")
    if args.output:
        out = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                root, args.allow_outside_root)
        if not args.dry_run:
            core.save_json(payload, out)
            rep.add_output(out, "capability-list")
    rep.stat("capabilities", payload["total"])
    return {"summary": {"capabilities": payload["total"], "categories": len(caps)}}


def cmd_modules(args, root: Path, rep: core.Reporter) -> dict:
    """List the runnable modules and their operations."""
    rows = []
    for name, (filename, purpose) in MODULES.items():
        path = HERE / filename
        ops = _ops_of(filename)
        rows.append({"module": name, "file": filename, "purpose": purpose,
                     "operations": ops, "exists": path.exists()})
        if not args.quiet and not args.json:
            print(f"{name:<9} {filename:<34} {len(ops):2d} ops  {purpose}")
            if args.verbose:
                for op in ops:
                    doc = _op_doc(filename, op)
                    print(f"    {op:<16} {doc}")
    missing = [r["file"] for r in rows if not r["exists"]]
    if missing:
        raise SFError("MODULE_MISSING", f"missing module files: {missing}")
    return {"summary": {"modules": len(rows),
                        "operations": sum(len(r["operations"]) for r in rows)},
            "modules": rows}


def _ops_of(filename: str) -> list[str]:
    text = (HERE / filename).read_text(encoding="utf-8")
    start = text.find("OPS = {")
    if start < 0:
        return []
    block = text[start:text.find("}", start)]
    import re
    return re.findall(r'^\s{4}"([a-z0-9\-]+)":', block, re.M)


def _op_doc(filename: str, op: str) -> str:
    text = (HERE / filename).read_text(encoding="utf-8")
    import re
    m = re.search(rf'\ndef op_{op.replace("-", "_")}\(.*?\n\s+"""(.*?)"""', text, re.S)
    return (m.group(1).strip().splitlines()[0] if m else "")


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def cmd_doctor(args, root: Path, rep: core.Reporter) -> dict:
    """Verify the runtime, assets and modules before real work."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "", fatal: bool = True) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "fatal": fatal})

    check("python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        import PIL
        from PIL import Image, features
        check("pillow", tuple(int(v) for v in PIL.__version__.split(".")[:2]) >= (10, 0),
              f"Pillow {PIL.__version__}")
        check("png", True, "PNG read/write")
        check("webp", features.check("webp"), "WebP encode")
        check("gif", True, "GIF read/write")
        import io
        buf = io.BytesIO()
        im = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
        im.save(buf, format="PNG", save_all=True, append_images=[im], duration=[50, 50])
        check("apng", len(buf.getvalue()) > 0, "APNG via Pillow save_all")
    except Exception as exc:  # noqa: BLE001
        check("pillow", False, f"{type(exc).__name__}: {exc}")
    missing = [f for f in (HERE / name).name for name in ()] if False else []
    module_missing = [f for f, _ in MODULES.values() if not (HERE / f).exists()]
    check("modules", not module_missing, f"{len(MODULES)} modules" if not module_missing
          else f"missing {module_missing}")
    check("capability-asset", CAPABILITY_ASSET.exists(),
          f"{len(flatten(load_capabilities())) if CAPABILITY_ASSET.exists() else 0} capabilities")
    check("ffmpeg", shutil.which("ffmpeg") is not None,
          "present (optional: video export)" if shutil.which("ffmpeg") else
          "not installed - MP4 export unavailable, GIF/APNG used instead", fatal=False)
    try:
        with core.temp_dir("sf-doctor-") as tmp:
            test = tmp / "probe.png"
            core.save_image(core.Image.new("RGBA", (8, 8), (0, 0, 0, 0)), test)
            core.load_image(test)
            core.save_animated([test and core.Image.new("RGBA", (4, 4), (9, 9, 9, 255))] * 2,
                               tmp / "probe.gif", [60, 60])
        check("write-access", True, "temp directory round-trip")
    except Exception as exc:  # noqa: BLE001
        check("write-access", False, f"{type(exc).__name__}: {exc}")
    check("root", root.is_dir(), str(root))
    fatal_failures = [c for c in checks if not c["ok"] and c["fatal"]]
    if not args.quiet and not args.json:
        for c in checks:
            status = "PASS" if c["ok"] else ("FAIL" if c["fatal"] else "WARN")
            print(f"[{status}] {c['check']:<18} {c['detail']}")
    rep.stat("checks", len(checks))
    return {"summary": {"ok": not fatal_failures, "checks": len(checks),
                        "failures": len(fatal_failures)},
            "checks": checks}


# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #

def cmd_route(args, root: Path, rep: core.Reporter) -> dict:
    """Route a plain-language request to the smallest set of module operations."""
    plan_file = HERE / "tools-spriteforge-plan.py"
    spec = importlib.util.spec_from_file_location("sf_plan_route", plan_file)
    plan = importlib.util.module_from_spec(spec)
    sys.modules["sf_plan_route"] = plan
    spec.loader.exec_module(plan)  # type: ignore[union-attr]
    ns = argparse.Namespace(query=args.query, text=None, input=None, limit=args.limit,
                            output=None, approve=[], timeout=0, dry_run=False,
                            allow_outside_root=False, max_frames=4096, max_pixels=64_000_000,
                            quiet=True, json=False, report=None)
    result = plan.op_router(ns, root, rep)
    caps = result.get("capabilities", [])
    if not args.quiet and not args.json:
        print(f"request: {args.query}\n")
        for i, c in enumerate(caps, 1):
            print(f"{i}. {c['skill']}  [{c['category']}]")
            print(f"   {c['command']}")
        print("\nHandoff order:", " -> ".join(c["primitive"] for c in caps))
    return {"summary": {"selected": len(caps),
                        "order": [c["primitive"] for c in caps]},
            "capabilities": caps}


# --------------------------------------------------------------------------- #
# run / dispatch
# --------------------------------------------------------------------------- #

def cmd_run(args, root: Path, rep: core.Reporter) -> dict:
    """Dispatch to a module operation, preserving its exit code and report."""
    if args.module not in MODULES:
        raise SFError("UNKNOWN_MODULE", f"unknown module '{args.module}'",
                      details={"modules": sorted(MODULES)})
    filename = MODULES[args.module][0]
    path = HERE / filename
    if not path.exists():
        raise SFError("MODULE_MISSING", f"module file missing: {filename}")
    rest = list(args.passthrough or [])
    operation = args.operation or (rest.pop(0) if rest else "")
    if args.module == "selftest" and not operation:
        proc = subprocess.run([sys.executable, str(path)] + [str(a) for a in rest])
        return {"summary": {"module": "selftest", "operation": "selftest",
                            "exit_code": proc.returncode},
                "_exit_code": proc.returncode}
    if not operation:
        raise SFError("MISSING_OPERATION", f"give an operation, e.g. `{args.module} <op>`",
                      hint=f"run `python3 {filename} --help` for the operation list")
    cmd = [sys.executable, str(path), "--root", str(root), operation] + [str(a) for a in rest]
    # add a leading dash argument straight after the operation, never before the root flags
    idx = cmd.index(operation)
    if idx < len(cmd) - 1 and str(cmd[idx + 1]).startswith("-"):
        pass
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(cmd, env=env)
    return {"summary": {"module": args.module, "operation": operation,
                        "exit_code": proc.returncode},
            "_exit_code": proc.returncode}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_cli() -> argparse.ArgumentParser:
    parser = core.build_parser(
        "spriteforge: sprite, sprite sheet, animation and production toolkit\n"
        "\n"
        "Start here:\n"
        "  tools-spriteforge.py doctor          check the runtime\n"
        "  tools-spriteforge.py capabilities    list all bundled capabilities\n"
        "  tools-spriteforge.py modules         list modules and operations\n"
        "  tools-spriteforge.py route --query \"...\"\n"
        "  tools-spriteforge.py <module> <op> [args]\n"
        "\n"
        "Direct module use: python3 tools-spriteforge-sheet.py compose --help",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def global_flags(sp):
        """Mirror the reporting flags so they work on either side of the subcommand."""
        sp.add_argument("--json", action="store_true", dest="sub_json",
                        help="emit the JSON report on stdout (also accepted before the command)")
        sp.add_argument("--report", dest="sub_report", metavar="PATH",
                        help="write the JSON report here (also accepted before the command)")
        sp.add_argument("--quiet", "-q", action="store_true", dest="sub_quiet",
                        help="suppress human output (also accepted before the command)")
        sp.add_argument("--timeout", type=float, dest="sub_timeout",
                        help="wall clock budget in seconds (also accepted before the command)")
        return sp

    sp = sub.add_parser("capabilities", help="list bundled capabilities")
    sp.add_argument("--category", action="append", default=[],
                    help="filter by category name or number-free substring (repeatable)")
    sp.add_argument("--module", help="filter by module (author, sheet, validate, ...)")
    sp.add_argument("--search", help="substring search in name and description")
    sp.add_argument("--id", help="comma separated catalog ids, e.g. 1,25,99")
    sp.add_argument("--full", action="store_true", help="print descriptions too")
    sp.add_argument("--output", "-o", help="write the filtered list as JSON")
    global_flags(sp)
    sp.set_defaults(_fn=cmd_capabilities)

    sp = sub.add_parser("modules", help="list modules, files and operations")
    sp.add_argument("--verbose", "-v", action="store_true", help="show each operation's summary")
    global_flags(sp)
    sp.set_defaults(_fn=cmd_modules)

    sp = sub.add_parser("doctor", help="verify runtime, assets and modules")
    global_flags(sp)
    sp.set_defaults(_fn=cmd_doctor)

    sp = sub.add_parser("route", help="route a request to the right operations")
    sp.add_argument("--query", required=True)
    sp.add_argument("--limit", type=int, default=8)
    global_flags(sp)
    sp.set_defaults(_fn=cmd_route)

    sp = sub.add_parser("run", help="dispatch to a module operation")
    sp.add_argument("module", choices=sorted(MODULES))
    sp.add_argument("operation", nargs="?", default=None,
                    help="operation name; omit for modules that take no subcommand (selftest)")
    sp.add_argument("passthrough", nargs=argparse.REMAINDER,
                    help="arguments forwarded to the module (include --root yourself if needed)")
    global_flags(sp)
    sp.set_defaults(_fn=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_cli()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    if not hasattr(args, "_fn"):
        # short form: `tools-spriteforge.py <module> <op> ...`
        if args.command in MODULES:
            rest = argv if argv is not None else sys.argv[1:]
            args = argparse.Namespace(command="run", module=args.command,
                                      operation=rest[1] if len(rest) > 1 else "",
                                      passthrough=rest[2:], root=args.root, report=args.report,
                                      json=args.json, approve=args.approve, timeout=args.timeout,
                                      dry_run=args.dry_run, allow_outside_root=args.allow_outside_root,
                                      max_frames=args.max_frames, max_pixels=args.max_pixels,
                                      quiet=args.quiet, _fn=cmd_run)
            args.timeout = args.timeout
        else:
            parser.print_help()
            return 2
    # merge the mirrored sub-command flags into the shared namespace
    if getattr(args, "sub_json", False):
        args.json = True
    if getattr(args, "sub_report", None):
        args.report = args.sub_report
    if getattr(args, "sub_quiet", False):
        args.quiet = True
    if getattr(args, "sub_timeout", None) and not args.timeout:
        args.timeout = args.sub_timeout
    root = Path(args.root).resolve()
    rep = core.Reporter("hub", args.command, args, root)
    try:
        outcome = args._fn(args, root, rep)
        exit_override = outcome.pop("_exit_code", None) if isinstance(outcome, dict) else None
        data = rep.finish(outcome if isinstance(outcome, dict) else {})
        code = core.emit(data, args, root)
        return int(exit_override) if exit_override is not None else code
    except SFError as exc:
        data = rep.finish({"ok": False, "error": exc.to_dict(), "exit_code": exc.exit_code})
        core.emit(data, args, root)
        if not args.quiet:
            print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    # Accept `hub <module> <op> ...` in addition to `hub run <module> <op> ...`.
    # Global flags written before the module are preserved.
    raw = sys.argv[1:]
    globals_part: list[str] = []
    while raw and raw[0].startswith("-"):
        flag = raw.pop(0)
        globals_part.append(flag)
        if flag in {"--root", "--report", "--timeout", "--approve", "--max-frames", "--max-pixels"} and raw:
            globals_part.append(raw.pop(0))
    if raw and raw[0] in MODULES:
        sys.argv = [sys.argv[0]] + globals_part + ["run"] + raw
    else:
        sys.argv = [sys.argv[0]] + globals_part + raw
    raise SystemExit(main())
