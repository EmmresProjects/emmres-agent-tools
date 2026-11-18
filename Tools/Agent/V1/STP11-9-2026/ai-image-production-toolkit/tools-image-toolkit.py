#!/usr/bin/env python3
"""AI Image Production Toolkit - primary command line entry point.

One unified Skill Tool that covers every capability in the AI Image Skill
Catalog (23 categories / 184 skills).

Usage
-----
    python3 tools-image-toolkit.py --help
    python3 tools-image-toolkit.py capabilities
    python3 tools-image-toolkit.py categories
    python3 tools-image-toolkit.py run <capability-id> [--set key=value ...]
    python3 tools-image-toolkit.py doctor

Every run returns a structured JSON envelope (use --json for machine output)
and a human readable report on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Keep the Skill Tool folder free of __pycache__ directories at runtime.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import importlib.util  # noqa: E402


def _load(stem: str, alias: str):
    """Import a sibling tools-*.py module and register it under a stable alias."""
    path = os.path.join(HERE, f"tools-{stem}.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot load module: tools-{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


core = _load("core", "tools_core")
caps_mod = _load("capabilities", "tools_capabilities")

TOOL_NAME = "ai-image-production-toolkit"
VERSION = "1.0.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID_INPUT = 3
EXIT_APPROVAL = 4
EXIT_FAILED = 5
EXIT_BLOCKED = 6


# ---------------------------------------------------------------------------
# Parameter handling
# ---------------------------------------------------------------------------


def coerce(value: str) -> Any:
    """Coerce a --set value into a JSON-ish Python value."""
    v = value.strip()
    if v == "":
        return ""
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v.startswith("[") or v.startswith("{"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


def parse_set_args(pairs: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise core.InvalidInput(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise core.InvalidInput(f"--set expects a non-empty key, got {pair!r}")
        out[key] = coerce(value)
    return out


def apply_defaults(params: Dict[str, Any], spec_params: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for sp in spec_params:
        if sp["name"] in params:
            merged[sp["name"]] = params[sp["name"]]
        elif sp.get("default") is not None:
            merged[sp["name"]] = sp["default"]
        else:
            merged[sp["name"]] = None
    for key, value in params.items():
        if key not in merged:
            merged[key] = value
    return merged


def resolve_paths(merged: Dict[str, Any], spec_params: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve relative input paths against the working directory, then the Skill Tool folder.

    This lets the documented examples work whether the command is launched from
    the Skill Tool folder or from the parent package root.
    """
    for sp in spec_params:
        name = sp["name"]
        if sp["type"] not in ("path", "path[]"):
            continue
        value = merged.get(name)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        resolved_items = []
        changed = False
        for item in items:
            if not isinstance(item, str) or not item:
                resolved_items.append(item)
                continue
            expanded = os.path.expanduser(item)
            if not os.path.isabs(expanded) and not os.path.exists(expanded):
                candidate = os.path.join(HERE, expanded)
                if os.path.exists(candidate):
                    expanded = candidate
                    changed = True
            resolved_items.append(expanded)
        if changed:
            merged[name] = resolved_items if isinstance(value, list) else resolved_items[0]
    return merged


def validate_params(merged: Dict[str, Any], spec_params: List[Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    for sp in spec_params:
        name = sp["name"]
        value = merged.get(name)
        if sp.get("required") and (value is None or value == ""):
            problems.append(f"missing required parameter: {name} ({sp['type']}) - {sp['description']}")
            continue
        if value is None:
            continue
        choices = sp.get("choices")
        if choices and value not in choices:
            problems.append(f"invalid value for {name}: {value!r} (expected one of {', '.join(choices)})")
        if sp["type"] == "integer":
            try:
                int(value)
            except (TypeError, ValueError):
                problems.append(f"parameter {name} must be an integer, got {value!r}")
        if sp["type"] == "number":
            try:
                float(value)
            except (TypeError, ValueError):
                problems.append(f"parameter {name} must be a number, got {value!r}")
        if sp["type"] == "path" and isinstance(value, str) and value and not os.path.exists(value):
            problems.append(f"parameter {name}: path does not exist: {value}")
    return problems


# ---------------------------------------------------------------------------
# Context passed to every handler
# ---------------------------------------------------------------------------


class Context:
    """Runtime context handed to capability handlers."""

    def __init__(self, cap: Dict[str, Any], params: Dict[str, Any], out_dir: str,
                 dry_run: bool, approved: bool, verbose: bool) -> None:
        self.cap = cap
        self.params = params
        self.out_dir = out_dir
        self.dry_run = dry_run
        self.approved = approved
        self.verbose = verbose
        self.artifacts: List[str] = []
        self.warnings: List[str] = []
        self.started = time.time()

    # -- helpers available to handlers ------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        value = self.params.get(name)
        return default if value is None else value

    def require(self, name: str) -> Any:
        value = self.params.get(name)
        if value is None or value == "":
            raise core.InvalidInput(f"missing required parameter: {name}")
        return value

    def path(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.params.get(name) or default
        return os.path.abspath(os.path.expanduser(value)) if value else None

    def paths(self, name: str) -> List[str]:
        value = self.params.get(name)
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(os.path.abspath(os.path.expanduser(item)))
        return out

    def out(self, filename: str) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        return os.path.join(self.out_dir, filename)

    def emit(self, path: str) -> str:
        rel = core.rel_path(path)
        self.artifacts.append(rel)
        return rel

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def write_json(self, filename: str, payload: Any) -> str:
        path = self.out(filename)
        core.write_json(path, payload)
        return self.emit(path)

    def write_text(self, filename: str, text: str) -> str:
        path = self.out(filename)
        core.write_text(path, text)
        return self.emit(path)

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"  . {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

APPROVAL_RISKS = {"destructive", "external", "publishing"}

# Optional image inputs. When the caller does not supply one, the tool writes a
# deterministic synthetic placeholder so every capability stays runnable, and
# says so in the envelope warnings.
IMAGE_PARAMS = ("image", "before", "after", "mask", "depth", "edges", "segmentation",
                 "matte", "pose", "reference", "background")
IMAGE_LIST_PARAMS = ("images", "references", "exposures")

# Capabilities that need a cut-out with an alpha channel rather than a photo.
PLACEHOLDER_OVERRIDES = {
    "masking-compositing.alpha-matting-specialist": {"image": "matte"},
    "masking-compositing.edge-decontamination-specialist": {"image": "matte"},
}


def fill_placeholders(match: Dict[str, Any], merged: Dict[str, Any],
                      out_dir: str) -> List[str]:
    """Create deterministic placeholder images for unsupplied optional image inputs."""
    notes: List[str] = []
    placeholder_dir = os.path.join(out_dir, "placeholder-inputs")
    supplied = set()

    for param in match["params"]:
        name = param["name"]
        if param["type"] == "path" and name in IMAGE_PARAMS:
            if merged.get(name):
                supplied.add(name)
    for param in match["params"]:
        name = param["name"]
        if param["type"] == "path[]" and name in IMAGE_LIST_PARAMS:
            if merged.get(name):
                supplied.add(name)

    def _make(kind: str, filename: str) -> str:
        os.makedirs(placeholder_dir, exist_ok=True)
        path = os.path.join(placeholder_dir, filename)
        core.write_placeholder_image(path, kind)
        return path

    overrides = PLACEHOLDER_OVERRIDES.get(match["id"], {})
    for param in match["params"]:
        name = param["name"]
        if param["type"] == "path" and name in IMAGE_PARAMS and name not in supplied:
            kind = overrides.get(name, name)
            merged[name] = _make(kind, f"{name}.png")
            notes.append(f"no '{name}' was supplied: a deterministic synthetic placeholder was "
                         f"generated and analysed instead")
            supplied.add(name)
        elif param["type"] == "path[]" and name in IMAGE_LIST_PARAMS and name not in supplied:
            kinds = {"images": ("image", "after"), "references": ("image", "after"),
                     "exposures": ("exposure",)}[name]
            merged[name] = [_make(kind, f"{name}-{i + 1}.png") for i, kind in enumerate(kinds)]
            notes.append(f"no '{name}' list was supplied: deterministic synthetic placeholders "
                         f"were generated and analysed instead")
            supplied.add(name)
    return notes


def run_capability(cap_id: str, params: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    catalog = caps_mod.build_registry()
    match = next((c for c in catalog if c["id"] == cap_id), None)
    if match is None:
        suggestions = [c["id"] for c in catalog if cap_id and (cap_id in c["id"] or c["id"].endswith(cap_id))]
        raise core.InvalidInput(
            f"unknown capability: {cap_id}",
            suggestions=suggestions[:10],
            hint="run `capabilities --search <term>` to find identifiers",
        )

    merged = apply_defaults(params, match["params"])
    merged = resolve_paths(merged, match["params"])
    problems = validate_params(merged, match["params"])
    if problems:
        raise core.InvalidInput("parameter validation failed", problems=problems)

    out_dir = os.path.abspath(os.path.expanduser(args.out_dir or merged.get("out_dir") or "output"))
    ctx = Context(match, merged, out_dir, args.dry_run, args.yes, args.verbose)

    placeholder_notes: List[str] = []

    needs_approval = match["risk"] in APPROVAL_RISKS
    if needs_approval and not (args.yes or args.dry_run):
        return {
            "ok": False,
            "status": "approval_required",
            "capability": match["id"],
            "category": match["category"],
            "skill": match["skill_name"],
            "risk": match["risk"],
            "summary": match["summary"],
            "approval": {
                "required": True,
                "granted": False,
                "instruction": "Re-run with --yes to execute this action, or --dry-run to preview the plan.",
            },
            "plan": {"parameters": merged, "out_dir": core.rel_path(out_dir)},
            "errors": [],
            "warnings": [],
            "artifacts": [],
        }

    if not args.dry_run:
        try:
            core.ensure_dir(out_dir)
        except OSError as exc:
            raise core.InvalidInput(f"cannot create the output directory {out_dir}: {exc}")
        placeholder_notes = fill_placeholders(match, merged, out_dir)
        for note in placeholder_notes:
            ctx.warn(note)

    module_name, func_name = match["handler"].split(":")
    module = core.load_module(module_name)
    handler = getattr(module, func_name, None)
    if handler is None:
        raise core.InvalidInput(f"handler not found: {match['handler']}")

    if ctx.dry_run:
        result = {
            "summary": f"Dry run: {match['summary']}",
            "data": {"would_execute": match["handler"], "parameters": merged},
            "artifacts": [],
            "warnings": [],
            "metrics": {},
        }
    else:
        result = handler(ctx) or {}

    envelope = {
        "ok": True,
        "status": result.get("status", "completed"),
        "tool": TOOL_NAME,
        "version": VERSION,
        "capability": match["id"],
        "category": match["category"],
        "skill": match["skill_name"],
        "kind": match["kind"],
        "risk": match["risk"],
        "summary": result.get("summary", match["summary"]),
        "inputs": {k: v for k, v in merged.items() if k != "out_dir"},
        "data": result.get("data", {}),
        "report": result.get("report", ""),
        "artifacts": ctx.artifacts + list(result.get("artifacts", [])),
        "warnings": ctx.warnings + list(result.get("warnings", [])),
        "metrics": result.get("metrics", {}),
        "approval": {"required": needs_approval, "granted": bool(args.yes) and needs_approval},
        "duration_ms": int((time.time() - ctx.started) * 1000),
    }
    if result.get("errors"):
        envelope["ok"] = False
        envelope["status"] = "completed_with_errors"
        envelope["errors"] = result["errors"]
    if not ctx.dry_run:
        try:
            report_path = os.path.join(out_dir, "report.md")
            core.write_text(report_path, envelope.get("report") or f"# {match['skill_name']}\n")
            ctx.artifacts.append(core.rel_path(report_path))
            result_path = os.path.join(out_dir, "result.json")
            core.write_json(result_path, envelope)
            ctx.artifacts.append(core.rel_path(result_path))
            envelope["artifacts"] = ctx.artifacts + list(result.get("artifacts", []))
        except OSError as exc:  # pragma: no cover - unwritable output directory
            ctx.warn(f"could not write the run files: {exc}")
    return envelope


# ---------------------------------------------------------------------------
# Human readable output
# ---------------------------------------------------------------------------


def print_envelope(envelope: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, indent=2, ensure_ascii=False, default=str))
        return
    status = envelope.get("status")
    icon = {"completed": "OK", "approval_required": "APPROVAL REQUIRED", "completed_with_errors": "DONE (with errors)"}.get(status, status.upper())
    print(f"[{icon}] {envelope.get('capability')}")
    print(f"  {envelope.get('summary')}")
    if envelope.get("category"):
        print(f"  category : {envelope['category']}")
    if envelope.get("risk"):
        print(f"  risk     : {envelope['risk']}")
    report = envelope.get("report")
    if report:
        print()
        print(report)
    data = envelope.get("data")
    if data:
        print()
        print("data:")
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    artifacts = envelope.get("artifacts") or []
    if artifacts:
        print()
        print("artifacts:")
        for art in artifacts:
            print(f"  - {art}")
    warnings = envelope.get("warnings") or []
    if warnings:
        print()
        print("warnings:")
        for w in warnings:
            print(f"  ! {w}")
    errors = envelope.get("errors") or []
    if errors:
        print()
        print("errors:")
        for e in errors:
            print(f"  x {e}")
    if envelope.get("approval", {}).get("required"):
        print()
        print(envelope["approval"].get("instruction", ""))
    print()
    print(f"completed in {envelope.get('duration_ms', 0)} ms")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_capabilities(args: argparse.Namespace) -> int:
    catalog = caps_mod.build_registry()
    items = catalog
    if args.category:
        needle = args.category.strip().lower()
        items = [c for c in items if needle in c["category"].lower() or needle == str(c["category_number"])]
    if args.search:
        needle = args.search.strip().lower()
        items = [c for c in items
                 if needle in c["id"].lower() or needle in c["skill_name"].lower()
                 or needle in c["description"].lower() or needle in c["summary"].lower()]
    if args.kind:
        items = [c for c in items if c["kind"] == args.kind]
    if args.risk:
        items = [c for c in items if c["risk"] == args.risk]

    if args.json:
        payload = {
            "tool": TOOL_NAME,
            "version": VERSION,
            "count": len(items),
            "total": len(catalog),
            "capabilities": [
                {k: c[k] for k in ("id", "category", "skill_name", "description", "kind", "risk", "summary")}
                for c in items
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    current = None
    for cap in items:
        if cap["category"] != current:
            current = cap["category"]
            print(f"\n{current}")
            print("-" * len(current))
        print(f"  {cap['id']}")
        if args.verbose:
            print(f"      kind={cap['kind']} risk={cap['risk']}")
            print(f"      {cap['summary']}")
            for sp in cap["params"]:
                flag = "required" if sp["required"] else "optional"
                choices = f" choices={','.join(sp['choices'])}" if sp.get("choices") else ""
                print(f"      - {sp['name']} ({sp['type']}, {flag}){choices}: {sp['description']}")
    print(f"\n{len(items)} of {len(catalog)} capabilities shown. Use --json for machine output.")
    return EXIT_OK


def cmd_categories(args: argparse.Namespace) -> int:
    index = caps_mod.category_index()
    if args.json:
        print(json.dumps({"tool": TOOL_NAME, "count": len(index), "categories": index}, indent=2, ensure_ascii=False))
        return EXIT_OK
    print("AI Image Skill Catalog - categories and capabilities")
    print("=" * 58)
    for cat in index:
        print(f"\n{cat['number']:>2}. {cat['name']}  ({cat['count']} capabilities, slug: {cat['slug']})")
        for cap_id in cat["capabilities"]:
            print(f"      {cap_id}")
    print(f"\n{len(index)} categories / {sum(c['count'] for c in index)} capabilities.")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {}
    if args.param_file:
        loaded = core.read_json(args.param_file)
        if not isinstance(loaded, dict):
            raise core.InvalidInput(f"--param-file must contain a JSON object: {args.param_file}")
        params.update(loaded)
    params.update(parse_set_args(args.set or []))

    envelope = run_capability(args.capability, params, args)
    print_envelope(envelope, args.json)
    if envelope.get("status") == "approval_required":
        return EXIT_APPROVAL
    return EXIT_OK if envelope.get("ok") else EXIT_FAILED


def cmd_doctor(args: argparse.Namespace) -> int:
    info = {
        "tool": TOOL_NAME,
        "version": VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "pillow": core.pillow_version(),
        "numpy": core.have_numpy(),
        "capabilities": len(caps_mod.build_registry()),
        "assets": sorted(f for f in os.listdir(HERE) if f.startswith("asset-")),
        "modules": sorted(f for f in os.listdir(HERE) if f.startswith("tools-") and f.endswith(".py")),
        "other_files": sorted(f for f in os.listdir(HERE)
                              if f.startswith("tools-") and not f.endswith(".py")),
        "knowledge_files": sorted(f for f in os.listdir(HERE) if f.startswith("knowledge-")),
    }
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return EXIT_OK
    print("AI Image Production Toolkit - environment")
    print("=" * 46)
    print(f"python      : {info['python']} ({info['platform']})")
    print(f"pillow      : {info['pillow'] or 'not installed (optional; stdlib fallbacks active)'}")
    print(f"numpy       : {'available' if info['numpy'] else 'not installed (optional)'}")
    print(f"capabilities: {info['capabilities']}")
    print("modules     : " + ", ".join(info["modules"]))
    if info["other_files"]:
        print("support     : " + ", ".join(info["other_files"]))
    print("knowledge   : " + ", ".join(info["knowledge_files"]))
    print("assets      : " + ", ".join(info["assets"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-image-toolkit.py",
        description="AI Image Production Toolkit - one unified Skill Tool covering 184 image-synthesis capabilities "
                    "from 23 catalog categories: foundations, prompt design, Stable Diffusion operations, conditioning, "
                    "generation, editing, compositing, upscaling, colour, typography, characters, commerce, architecture, "
                    "technical imaging, training, governance, safety, QA, automation, performance, specialised media and delivery.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 tools-image-toolkit.py capabilities --category prompt-design\n"
            "  python3 tools-image-toolkit.py run prompt-design.prompt-architect --set brief='a lighthouse in fog' --json\n"
            "  python3 tools-image-toolkit.py run quality-evaluation.visual-quality-reviewer --set image=photo.png\n"
            "  python3 tools-image-toolkit.py run automation-batch.batch-prompt-runner --set prompts=prompts.json\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_cap = sub.add_parser("capabilities", help="List every capability (id, kind, risk, summary).")
    p_cap.add_argument("--category", help="Filter by category name or number.")
    p_cap.add_argument("--search", help="Filter by id, name or description substring.")
    p_cap.add_argument("--kind", help="Filter by kind (analysis, plan, artifact, transform, audit, compute, report).")
    p_cap.add_argument("--risk", help="Filter by risk (safe, write, destructive, external).")
    p_cap.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    p_cap.add_argument("--verbose", "-v", action="store_true", help="Show kinds, risks and parameters.")
    p_cap.set_defaults(func=cmd_capabilities)

    p_cat = sub.add_parser("categories", help="List the 23 catalog categories and their capability ids.")
    p_cat.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    p_cat.set_defaults(func=cmd_categories)

    p_run = sub.add_parser("run", help="Run one capability by id.")
    p_run.add_argument("capability", help="Capability id, e.g. prompt-design.prompt-architect")
    p_run.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="Set a parameter (repeatable). Values are coerced: numbers, true/false, null, JSON.")
    p_run.add_argument("--param-file", metavar="FILE", help="JSON file with parameter values.")
    p_run.add_argument("--out-dir", metavar="DIR", help="Output directory for generated artefacts (default: output).")
    p_run.add_argument("--json", action="store_true", help="Emit the JSON envelope only.")
    p_run.add_argument("--dry-run", action="store_true", help="Validate and show the plan without executing.")
    p_run.add_argument("--yes", action="store_true", help="Approve destructive / external actions.")
    p_run.add_argument("--verbose", "-v", action="store_true", help="Progress notes on stderr.")
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="Report runtime, optional dependencies and packaged files.")
    p_doc.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except core.InvalidInput as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "status": "invalid_input", "error": payload}, indent=2, ensure_ascii=False))
        else:
            print(f"INVALID INPUT: {exc.message}", file=sys.stderr)
            for problem in payload.get("details", {}).get("problems", []) or []:
                print(f"  x {problem}", file=sys.stderr)
            for hint in payload.get("details", {}).get("suggestions", []) or []:
                print(f"  ? did you mean: {hint}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except core.BlockedIntegration as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "status": "blocked", "error": payload}, indent=2, ensure_ascii=False))
        else:
            print(f"BLOCKED: {exc.message}", file=sys.stderr)
        return EXIT_BLOCKED
    except core.ToolError as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "status": "error", "error": payload}, indent=2, ensure_ascii=False))
        else:
            print(f"ERROR: {exc.message}", file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
