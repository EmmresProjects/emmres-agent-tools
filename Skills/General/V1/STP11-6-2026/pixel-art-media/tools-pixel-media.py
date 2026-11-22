#!/usr/bin/env python3
"""pixel-art-media — unified Skill Tool for pixel art, sprites, voxels,
animation, and pixel media production.

Primary entry point. Implements the Pixel Art, Sprite, Voxel, Animation, and
Pixel Media Skill Catalog (29 categories / 318 skills) as one reusable tool:
60+ CLI operations plus catalog-driven planning/guidance.

Usage (from the future skill root):
    python3 pixel-art-media/tools-pixel-media.py --help
    python3 pixel-art-media/tools-pixel-media.py capabilities
    python3 pixel-art-media/tools-pixel-media.py convert in.png out.png --width 64

Output is structured JSON on stdout; errors are structured JSON on stderr.
Exit codes: 0 ok, 2 bad input/args, 3 not found, 4 limit exceeded,
5 approval required (overwrite), 6 dependency missing, 7 validation failed,
8 write failure, 130 cancelled.

Requires: Python 3.9+, Pillow >= 10 (see tools-requirements.txt).
No network access. Outputs never overwrite existing files without --force.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep the skill folder clean at runtime

import argparse
import importlib.util
import json
import time
import traceback
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
VERSION = "1.0.0"

def exit_code_for(code):
    if code == "output-exists":
        return 5
    if code == "dependency-missing" or "unavailable" in code:
        return 6
    if "not-found" in code or code.startswith("missing"):
        return 3
    if code.startswith("limit") or code == "timeout":
        return 4
    if code.startswith("write"):
        return 8
    return 2


def load_module(name):
    mod_name = "tools_" + name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = TOOL_DIR / f"tools-{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"Required module missing: {path.name}")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m
    spec.loader.exec_module(m)
    return m


MODULES = ["imaging", "palettes", "sheets", "generators", "delivery", "quality", "planning"]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-pixel-media.py",
        description="Unified pixel-art media Skill Tool: create, edit, animate, convert, "
                    "package, validate, preserve, and publish pixel-based media. "
                    "All commands print structured JSON. Run 'capabilities' for the full map.",
        epilog="Examples:\n"
               "  convert photo.jpg out/sprite.png --width 64 --colors 16 --dither bayer4 --preview-scale 4\n"
               "  sprite-generate out/sprites --kind creature --count 8 --seed 21 --sheet out/sprites.png\n"
               "  sheet-pack frames/ --output out/atlas.png --meta out/atlas.json --extrude 1 --power-of-two\n"
               "  anim-encode frames/ --output out/loop.gif --fps 12 --scale 4\n"
               "  validate out/atlas.json --sheet out/atlas.png\n"
               "  guide out/guide.md --skill \"Walk Cycle Animator\"\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"pixel-art-media {VERSION}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Explicit JSON output (default; accepted for clarity)")
    common.add_argument("--config", help="User config JSON merged over asset-config.json")
    common.add_argument("--force", action="store_true",
                        help="APPROVAL: allow overwriting existing outputs / in-place sidecar edits")
    common.add_argument("--dry-run", action="store_true", help="Validate and report without writing files")
    common.add_argument("--quiet", action="store_true", help="Suppress stdout on success")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    registry = []

    def help_map():
        return {act.dest: (act.help or "") for act in sub._choices_actions}

    for name in MODULES:
        m = load_module(name)
        before = set(sub.choices)
        m.register(sub, common)
        hm = help_map()
        for new in set(sub.choices) - before:
            registry.append((new, hm.get(new, ""), name))
    # batch + selftest live here (need the parser / sibling test module)
    p = sub.add_parser("batch", parents=[common],
                       help="Run a JSON job pipeline (deterministic, stop-on-error, structured report)")
    p.add_argument("jobs", help="Job file: {defaults:{}, jobs:[{id, op, positional:[], args:{}} | {argv:[]}]}")
    p.add_argument("--report", help="Write the full run report here")
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=cmd_batch)
    registry.append(("batch", p.description or "Run a JSON job pipeline", "entry"))
    p = sub.add_parser("selftest", parents=[common],
                       help="Run the built-in test suite against the real CLI")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_selftest)
    registry.append(("selftest", p.description or "Run built-in tests", "entry"))
    registry.sort(key=lambda r: r[0])
    return parser, sub, registry, common


def make_ctx(args, imaging):
    cfg = imaging.load_config(getattr(args, "config", None))
    return imaging.Ctx(cfg, force=getattr(args, "force", False),
                       dry_run=getattr(args, "dry_run", False),
                       json_mode=True, quiet=getattr(args, "quiet", False))


def args_to_argv(op, job):
    if "argv" in job:
        argv = [op] + [str(v) for v in job["argv"]]
        return argv
    positional = [str(v) for v in job.get("positional", [])]
    opts = []
    for k, v in (job.get("args") or {}).items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                opts.append(flag)
        elif isinstance(v, list):
            if k == "inputs":
                positional.extend(str(x) for x in v)
            else:
                for item in v:
                    opts.extend([flag, str(item)])
        elif v is not None:
            opts.extend([flag, str(v)])
    return [op] + positional + opts


def cmd_batch(a, ctx):
    imaging = load_module("imaging")
    spec = imaging.read_json(a.jobs)
    jobs = spec.get("jobs", [])
    if not jobs:
        raise imaging.ToolError("bad-arg", "Job file contains no jobs.")
    if len(jobs) > 200:
        raise imaging.ToolError("limit-jobs", "Batch limited to 200 jobs per run.")
    defaults = spec.get("defaults", {})
    parser, sub, registry, common = build_parser()
    valid_ops = set(sub.choices)
    results = []
    ok_all = True
    for i, job in enumerate(jobs):
        op = job.get("op")
        jid = job.get("id", f"job-{i}")
        if op not in valid_ops or op in ("batch", "selftest"):
            results.append({"id": jid, "op": op, "ok": False,
                            "error": {"code": "bad-op", "message": f"Unknown or non-batchable op: {op}"}})
            ok_all = False
            if not a.continue_on_error:
                break
            continue
        merged = dict(job)
        merged_args = dict(defaults.get("args", {}))
        merged_args.update(job.get("args") or {})
        merged["args"] = merged_args
        argv = args_to_argv(op, merged)
        for flag, on in (("--force", ctx.force), ("--dry-run", ctx.dry_run), ("--quiet", True)):
            if on and flag not in argv:
                argv.append(flag)
        entry = {"id": jid, "op": op, "argv": argv}
        try:
            jargs = parser.parse_args(argv)
            jctx = make_ctx(jargs, imaging)
            jctx.commands = registry
            res = jargs.func(jargs, jctx)
            passed = res.get("pass", True) if isinstance(res, dict) else True
            entry.update(ok=bool(passed), result=res, written=jctx.written, warnings=jctx.warnings)
            if not passed:
                ok_all = False
        except SystemExit as e:
            entry.update(ok=False, error={"code": "bad-argv", "message": f"Argument parsing failed (exit {e.code})"})
            ok_all = False
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", "error")
            entry.update(ok=False, error={"code": code, "message": str(e),
                                          "hint": getattr(e, "hint", "")})
            ok_all = False
        results.append(entry)
        if not ok_all and not a.continue_on_error:
            break
    report = {"jobs_run": len(results), "jobs_total": len(jobs), "pass": ok_all, "results": results}
    if a.report:
        imaging.write_json(report, a.report, ctx)
    return report


def cmd_selftest(a, ctx):
    tester = load_module("selftest")
    return tester.run(verbose=a.verbose)


def emit(obj, quiet=False):
    if not quiet:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv=None):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print(json.dumps({"ok": False, "error": {
            "code": "dependency-missing",
            "message": "Pillow is required but not installed.",
            "hint": "python3 -m pip install -r pixel-art-media/tools-requirements.txt"}}),
            file=sys.stderr)
        return 6
    parser, sub, registry, common = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    imaging = load_module("imaging")
    try:
        ctx = make_ctx(args, imaging)
        ctx.commands = registry
        # Enforce the configured wall-clock budget on POSIX (SIGALRM).
        import signal
        timeout = int(ctx.cfg["limits"].get("timeout_seconds", 180) or 0)
        armed = False
        if timeout > 0 and hasattr(signal, "SIGALRM"):
            def _on_timeout(signum, frame):
                raise imaging.ToolError("timeout", f"Operation exceeded {timeout}s budget.",
                                        "Reduce dimensions/frames or raise limits.timeout_seconds.")
            signal.signal(signal.SIGALRM, _on_timeout)
            signal.alarm(timeout)
            armed = True
        try:
            result = args.func(args, ctx)
        finally:
            if armed:
                signal.alarm(0)
        if isinstance(result, dict):
            result.setdefault("ok", True)
            result.setdefault("command", args.command)
            if ctx.written:
                result.setdefault("written_files", ctx.written)
            if ctx.warnings:
                result["warnings"] = ctx.warnings
            result["elapsed_ms"] = int((time.time() - ctx.started) * 1000)
        emit(result, ctx.quiet)
        if isinstance(result, dict) and result.get("pass") is False:
            return 7
        return 0
    except imaging.ToolError as e:
        print(json.dumps({"ok": False, "command": getattr(args, "command", None),
                          "error": {"code": e.code, "message": e.message, "hint": e.hint}},
                         ensure_ascii=False), file=sys.stderr)
        return exit_code_for(e.code)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "cancelled",
                          "message": "Interrupted; partial outputs are atomic (temp files cleaned)."}}),
              file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "command": getattr(args, "command", None),
                          "error": {"code": "internal-error", "message": str(e),
                                    "traceback": traceback.format_exc(limit=3)}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
