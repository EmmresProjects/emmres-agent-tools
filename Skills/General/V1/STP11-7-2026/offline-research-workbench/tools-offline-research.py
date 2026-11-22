#!/usr/bin/env python3
"""tools-offline-research.py - primary entry point for the offline-research-workbench Skill Tool.

One offline workbench for evidence-grounded research, writing and translation.
288 catalog capabilities are reachable either through grouped sub-commands or through
`apply --skill <id>`, which dispatches a catalog skill id to its documented operation.

Usage
  python3 tools-offline-research.py --help
  python3 tools-offline-research.py capabilities --list
  python3 tools-offline-research.py apply --skill offline-scholarly-database --input paper.md
  python3 tools-offline-research.py search query --query "retraction policy" --json

Exit codes: 0 ok | 2 invalid input/usage | 3 approval required or gate blocked |
            4 local model/tool adapter unavailable | 5 internal error
"""

from __future__ import annotations

import argparse
import contextlib
import io
import importlib.util
import json
import os
import sys
import traceback

VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_ORDER = [
    "store", "adapters", "retrieval", "extract", "citation", "verify",
    "synthesis", "writing", "translation", "guard", "workflow", "eval",
]
MODULE_FILES = {name: os.path.join(BASE_DIR, f"tools-rwt-{name}.py") for name in MODULE_ORDER}
REGISTRY_PATH = os.path.join(BASE_DIR, "asset-capability-registry.json")

LOADED: dict = {}
GROUP_OWNER: dict = {}


# ---------------------------------------------------------------- module loading

def load_module(name: str):
    if name in LOADED:
        return LOADED[name]
    path = MODULE_FILES[name]
    if not os.path.exists(path):
        raise SystemExit(f"internal error: missing module {os.path.basename(path)}")
    mod_name = "rwt_" + name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    module.deps = LOADED
    LOADED[name] = module
    return module


def load_all():
    for name in MODULE_ORDER:
        load_module(name)
    return LOADED


# ---------------------------------------------------------------- rendering

def render(payload, indent: int = 0, max_items: int = 12) -> str:
    pad = "  " * indent
    lines = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.startswith("_"):
                continue
            label = str(key).replace("_", " ")
            if isinstance(value, dict):
                lines.append(f"{pad}{label}:")
                sub = render(value, indent + 1, max_items)
                if sub:
                    lines.append(sub)
            elif isinstance(value, list):
                if not value:
                    lines.append(f"{pad}{label}: (none)")
                else:
                    lines.append(f"{pad}{label} ({len(value)}):")
                    for item in value[:max_items]:
                        if isinstance(item, (dict, list)):
                            sub = render(item, indent + 1, max_items)
                            first = sub.strip().splitlines()[0].strip() if sub else ""
                            lines.append(f"{pad}  - {first}")
                            rest = sub.strip().splitlines()[1:]
                            lines.extend(f"{pad}    {r.strip()}" for r in rest)
                        else:
                            lines.append(f"{pad}  - {item}")
                    if len(value) > max_items:
                        lines.append(f"{pad}  ... (+{len(value) - max_items} more; use --json for all)")
            elif isinstance(value, bool):
                lines.append(f"{pad}{label}: {'yes' if value else 'no'}")
            elif value is None:
                lines.append(f"{pad}{label}: -")
            else:
                lines.append(f"{pad}{label}: {value}")
    elif isinstance(payload, list):
        for item in payload[:max_items]:
            lines.append(f"{pad}- {item}")
    else:
        lines.append(f"{pad}{payload}")
    return "\n".join(lines)


def emit(payload: dict, as_json: bool, headline: str | None = None) -> int:
    code = int(payload.get("_exit", payload.get("exit_code", 0)) or 0)
    if as_json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_exit"},
                         ensure_ascii=False, indent=2, default=str))
    else:
        if headline:
            print(f"== {headline} ==")
        print(render(payload))
    return code


# ---------------------------------------------------------------- capability registry

def load_registry() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        raise SystemExit(f"capability registry missing: {os.path.basename(REGISTRY_PATH)}")
    with open(REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def all_skills(registry: dict) -> list:
    out = []
    for cat in registry.get("categories", []):
        for skill in cat.get("skills", []):
            out.append({**skill, "category_number": cat["number"],
                        "category_slug": cat["slug"], "category_name": cat["name"]})
    return out


def find_skill(registry: dict, skill_id: str) -> dict | None:
    needle = skill_id.strip().lower()
    candidates = all_skills(registry)
    for skill in candidates:
        if needle in (skill["id"], str(skill["number"]), f"{skill['category_number']}.{skill['number']}"):
            return skill
    for skill in candidates:
        if needle == skill["name"].lower().replace(" ", "-"):
            return skill
    for skill in candidates:
        if needle in skill["id"] or needle in skill["name"].lower():
            return skill
    return None


def cmd_capabilities(args) -> int:
    registry = load_registry()
    skills = all_skills(registry)
    if args.skill:
        skill = find_skill(registry, args.skill)
        if not skill:
            return emit({"status": "error", "message": f"unknown skill id: {args.skill}",
                         "_exit": 2}, args.json)
        return emit({"status": "ok", "skill": skill}, args.json)
    if args.category is not None:
        needle = str(args.category).strip().lower()
        for cat in registry["categories"]:
            if needle in (str(cat["number"]), cat["slug"], cat["name"].lower()):
                skills = [{**s, "category_number": cat["number"], "category_slug": cat["slug"],
                           "category_name": cat["name"]} for s in cat["skills"]]
                break
        else:
            return emit({"status": "error", "message": f"unknown category: {args.category}",
                         "_exit": 2}, args.json)
    if args.search:
        q = args.search.lower()
        skills = [s for s in skills
                  if q in s["name"].lower() or q in s["description"].lower() or q in s["op"]]
    payload = {
        "status": "ok",
        "tool": "offline-research-workbench",
        "registry_version": registry.get("registry_version"),
        "catalog": registry.get("source"),
        "categories": len(registry["categories"]),
        "capabilities": len(all_skills(registry)),
        "matched": len(skills),
        "skills": [{"id": s["id"], "category": f"{s['category_number']}. {s['category_name']}",
                    "number": s["number"], "name": s["name"], "op": s["op"],
                    "description": s["description"]} for s in skills],
    }
    if args.ids:
        payload = {"status": "ok", "matched": len(skills), "ids": [s["id"] for s in skills]}
        payload["skills"] = payload.pop("ids")
    return emit(payload, args.json)


def build_apply_argv(skill: dict, args) -> list:
    op = skill["op"]
    parts = op.split()
    if len(parts) != 2:
        raise SystemExit(f"registry op malformed for {skill['id']}: {op}")
    argv = list(parts)
    for key, value in (skill.get("defaults") or {}).items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value not in (None, False, ""):
            argv.extend([flag, str(value)])
    for key, value in parse_set_pairs(args.set).items():
        flag = "--" + key.replace("_", "-")
        if value.lower() in ("true", "yes"):
            argv.append(flag)
        elif value.lower() not in ("false", "no"):
            argv.extend([flag, value])
    if args.input:
        argv.extend(["--input", args.input])
    if args.output:
        argv.extend(["--output", args.output])
    if args.workspace:
        argv.extend(["--workspace", args.workspace])
    if args.config:
        argv.extend(["--config", args.config])
    if args.json:
        argv.append("--json")
    if args.actor:
        argv.extend(["--actor", args.actor])
    return argv


def parse_set_pairs(pairs) -> dict:
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_apply(args) -> int:
    registry = load_registry()
    skill = find_skill(registry, args.skill)
    if not skill:
        return emit({"status": "error", "message": f"unknown skill id: {args.skill}",
                     "hint": "run 'capabilities --list' to see every skill id", "_exit": 2}, args.json)
    skill = next({**s, "category_number": c["number"], "category_slug": c["slug"],
                  "category_name": c["name"]}
                 for c in registry["categories"] for s in c["skills"] if s["id"] == skill["id"])
    argv = build_apply_argv(skill, args)
    if args.dry_run:
        return emit({"status": "ok", "skill": skill["id"], "op": skill["op"],
                     "command": "python3 tools-offline-research.py " + " ".join(argv)}, args.json)
    sys.stderr.write(f"[apply] {skill['id']} -> {' '.join(argv[:2])}\n")
    return dispatch(argv, apply_context=skill)


# ---------------------------------------------------------------- parser

class CommonParser(argparse.ArgumentParser):
    """Leaf commands accept the shared options before or after the command name."""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", "-w", default=argparse.SUPPRESS,
                        help="workspace directory (default: rwt-workspace or RWT_WORKSPACE)")
    common.add_argument("--config", default=argparse.SUPPRESS, help="JSON config overriding asset-default-config.json")
    common.add_argument("--actor", default=argparse.SUPPRESS, help="actor recorded in the audit log")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit structured JSON")

    top = argparse.ArgumentParser(
        prog="tools-offline-research.py",
        description="Offline research, writing and translation workbench (288 catalog capabilities).",
        epilog="Run 'capabilities --list' for every skill id, or 'corpus --help' style group help.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    top.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    top.add_argument("--workspace", "-w", default=None, help="workspace directory")
    top.add_argument("--config", default=None, help="JSON config file")
    top.add_argument("--actor", default="operator", help="actor name for audit entries")
    top.add_argument("--json", action="store_true", help="emit structured JSON")
    subs = top.add_subparsers(dest="group", metavar="<group>")

    cap = subs.add_parser("capabilities", help="List, search and inspect catalog capabilities")
    cap.add_argument("--list", action="store_true", help="list all capabilities (default action)")
    cap.add_argument("--category", help="category number, slug or name")
    cap.add_argument("--skill", help="skill id, name or number (e.g. 1.3)")
    cap.add_argument("--search", help="substring filter over name/description/op")
    cap.add_argument("--ids", action="store_true", help="print skill ids only")
    cap.add_argument("--json", action="store_true", default=False)
    cap.set_defaults(handler=cmd_capabilities)

    app = subs.add_parser("apply", help="Run a catalog capability by its skill id")
    app.add_argument("--skill", required=True, help="skill id from 'capabilities --list'")
    app.add_argument("--input", "-i", help="input path for the underlying operation")
    app.add_argument("--output", "-o", help="output path for the underlying operation")
    app.add_argument("--set", action="append", metavar="KEY=VALUE",
                     help="override an operation option, e.g. --set doctype=statute (repeatable)")
    app.add_argument("--workspace", "-w", default=argparse.SUPPRESS)
    app.add_argument("--config", default=argparse.SUPPRESS)
    app.add_argument("--actor", default=argparse.SUPPRESS)
    app.add_argument("--json", action="store_true")
    app.add_argument("--dry-run", action="store_true", help="print the resolved command only")
    app.set_defaults(handler=cmd_apply)

    # module groups -------------------------------------------------
    modules = [load_module(n) for n in MODULE_ORDER]
    group_parsers, helps = {}, []
    for module in modules:
        for group_name, help_text in getattr(module, "GROUPS", []):
            if group_name in group_parsers:
                raise SystemExit(f"duplicate command group: {group_name}")
            group_parser = subs.add_parser(group_name, help=help_text, description=help_text,
                                           formatter_class=argparse.RawDescriptionHelpFormatter)
            group_parsers[group_name] = (group_parser.add_subparsers(dest=f"{group_name}_command",
                                                                    metavar="<command>"), module)
            GROUP_OWNER[group_name] = module.NAME
            helps.append(group_name)
    for module in modules:
        mine = {g: p for g, (p, owner) in group_parsers.items() if owner is module}
        if mine:
            module.register(mine, common)
    return top


def make_ctx(namespace, extra=None):
    store = LOADED["store"]
    cfg = getattr(namespace, "config", None) if getattr(namespace, "config", None) else None
    if extra and extra.get("config"):
        cfg = cfg or extra["config"]
    ctx = store.Ctx(workspace=getattr(namespace, "workspace", None),
                    config=cfg, actor=getattr(namespace, "actor", None) or "operator")
    return ctx


def dispatch(argv: list, apply_context: dict | None = None) -> int:
    parser = build_parser()
    as_json_hint = "--json" in argv
    try:
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        raw = " ".join(captured.getvalue().split()) or "invalid command line"
        if "error:" in raw:
            raw = raw.split("error:", 1)[1].strip()
        message = ("usage error: " + raw)[:400]
        if as_json_hint:
            print(json.dumps({"status": "error", "message": message,
                              "type": "UsageError", "exit_code": 2,
                              "hint": "run with --help for the command surface"}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    as_json = bool(getattr(args, "json", False))
    handler = args.handler
    name = getattr(handler, "__name__", "handler")
    try:
        kwargs = {k: v for k, v in vars(args).items()
                  if k not in ("handler", "group", "json", "workspace", "config", "actor")
                  and not k.endswith("_command")}
        if name == "cmd_capabilities":
            return handler(args)
        if name == "cmd_apply":
            return handler(args)
        ctx = make_ctx(args)
        for key, value in list(kwargs.items()):
            if isinstance(value, str) and key != "output" and (
                    key.endswith("_path") or key in ("bundle", "suite", "effects", "quality",
                                                     "original", "revised", "draft", "project",
                                                     "cases", "reference", "hypothesis",
                                                     "manifest", "spec", "editions_dir")):
                kwargs[key] = ctx.resolve_input(value)
        payload = handler(ctx, **kwargs)
        payload.setdefault("_exit", 0)
        if apply_context:
            payload["skill"] = apply_context["id"]
            payload["skill_name"] = apply_context["name"]
        code = emit(payload, as_json)
        ctx.close()
        return code
    except LOADED["store"].ToolError as exc:
        code = emit(exc.payload(), as_json)
        return code
    except BrokenPipeError:  # pragma: no cover
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level guard
        if os.environ.get("RWT_DEBUG"):
            traceback.print_exc()
        return emit({"status": "error", "message": f"internal error: {exc}",
                     "type": type(exc).__name__,
                     "hint": "set RWT_DEBUG=1 for a traceback", "_exit": 5}, as_json)


def main(argv: list | None = None) -> int:
    return dispatch(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
