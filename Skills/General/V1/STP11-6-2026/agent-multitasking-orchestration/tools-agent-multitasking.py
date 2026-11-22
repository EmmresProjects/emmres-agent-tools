#!/usr/bin/env python3
"""Primary CLI for the agent-multitasking-orchestration Skill Tool.

Run from the future skill root:
python3 agent-multitasking-orchestration/tools-agent-multitasking.py help
python3 agent-multitasking-orchestration/tools-agent-multitasking.py capabilities
python3 agent-multitasking-orchestration/tools-agent-multitasking.py run-capability --capability goal-decomposer
"""

from __future__ import annotations

import argparse
import json
import re
import sys

sys.dont_write_bytecode = True  # Keep the delivered Skill Tool folder free of __pycache__ subfolders.
from importlib import import_module
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_NAME = PACKAGE_DIR.name

# Hyphenated filenames are required by the Skill Tool contract. Register stable
# module aliases before importing supporting modules.
_MODULE_FILES = [
    ("tools_common", "tools-common"),
    ("tools_parser", "tools-parser"),
    ("tools_registry", "tools-registry"),
    ("tools_operations", "tools-operations"),
    ("tools_orchestrator", "tools-orchestrator"),
]
for _alias, _filename in _MODULE_FILES:
    sys.modules.setdefault(_alias, import_module(_filename))

from tools_common import (  # noqa: E402
    EXIT_APPROVAL,
    EXIT_CANCELLED,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_VALIDATION,
    ToolError,
    canonical_json,
    current_paths,
    load_config,
    load_json,
    load_schema,
    now_iso,
    require_schema,
    sha256_text,
    write_json_atomic,
)
from tools_operations import approved_record, pending_packet, verify_approval_record  # noqa: E402
from tools_parser import merge_catalogs, parse_catalog, read_source  # noqa: E402
from tools_registry import load_registry  # noqa: E402
from tools_orchestrator import MissionEngine, validate_mission_document  # noqa: E402

from tools_operations import CapabilityRuntime  # noqa: E402

VERSION = "1.0.0"
HELP_TOPICS = ["commands", "inputs", "safety", "capabilities", "examples", "all"]
SOURCE_FORMATS = ["auto", "text", "markdown", "html", "pdf", "docx"]
VALIDATE_KINDS = ["auto", "json", "config", "catalog", "profiles", "mission", "approval", "source", "package"]


class StructuredArgumentParser(argparse.ArgumentParser):
    """Emit argument errors as JSON instead of argparse's plain stderr text."""

    def error(self, message: str) -> None:
        raise ToolError(
            "ARGUMENT_ERROR",
            "Command-line arguments are invalid.",
            details={"message": message},
            recovery="Run help --topic commands for the exact command syntax.",
            exit_code=EXIT_VALIDATION,
        )


def add_common(parser: argparse.ArgumentParser, *, output: bool = True) -> None:
    parser.add_argument("--config", help="Root-relative path to an alternate tools-config.json.", default=None)
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of indented JSON.")
    if output:
        parser.add_argument("--output", help="Root-relative output path outside the Skill Tool folder.", default=None)


def output_path(value: str, paths: Any) -> Path:
    resolved = paths.safe(value)
    if resolved == paths.package or paths.package in resolved.parents:
        raise ToolError(
            "OUTPUT_INSIDE_PACKAGE_REJECTED",
            "Do not write runtime outputs inside the Skill Tool folder.",
            details={"path": value},
            recovery="Use a root-relative path such as output/result.json or validation-output/result.json.",
        )
    return resolved


def envelope(command: str, result: dict[str, Any] | None = None, *, status: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status or (result.get("status", "ok") if isinstance(result, dict) else "ok"),
        "command": command,
        "timestamp": now_iso(),
        "tool_version": VERSION,
    }
    if result is not None:
        payload["result"] = result
    return payload


def _file_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Write the reusable artifact itself, not the CLI envelope, to --output."""
    inner_command = result.get("command")
    if inner_command in {"parse", "merge"} and isinstance(result.get("catalog"), dict):
        return result["catalog"]
    if inner_command == "catalog" and "data" in result:
        return result["data"]
    if inner_command == "approval.request" and isinstance(result.get("packet"), dict):
        return result["packet"]
    if inner_command == "approval.grant" and isinstance(result.get("record"), dict):
        return result["record"]
    return result


def emit(
    command: str,
    result: dict[str, Any],
    args: argparse.Namespace,
    paths: Any,
    *,
    exit_code: int = EXIT_OK,
    file_payload: dict[str, Any] | None = None,
) -> int:
    written = file_payload if file_payload is not None else _file_payload(result)
    payload = envelope(command, result, status=result.get("status") if isinstance(result, dict) else None)
    if getattr(args, "output", None):
        target = output_path(args.output, paths)
        write_json_atomic(target, written)
        receipt = {
            "status": result.get("status", "ok"),
            "command": command,
            "timestamp": now_iso(),
            "tool_version": VERSION,
            "result": {
                "output_path": args.output,
                "result_bytes": len(canonical_json(written).encode("utf-8")),
                "result_sha256": sha256_text(canonical_json(written)),
                "result_status": result.get("status", "ok"),
                "stdout_detail": "receipt-only",
            },
        }
        payload = receipt
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=False))
    return exit_code


def error_emit(command: str, error: ToolError, args: argparse.Namespace) -> int:
    payload = {
        "status": "error",
        "command": command,
        "timestamp": now_iso(),
        "tool_version": VERSION,
        "error": error.payload()["error"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=None if getattr(args, "compact", False) else 2))
    return error.exit_code


def config_for(args: argparse.Namespace, paths: Any) -> dict[str, Any]:
    return load_config(getattr(args, "config", None), paths=paths)


def command_help(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    topic = args.topic or "all"
    commands = [
        {"name": "help", "purpose": "Show built-in structured help.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py help --topic commands"},
        {"name": "capabilities", "aliases": ["capability-list", "list-capabilities"], "purpose": "List all 256 registered capabilities with handlers and risks.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py capabilities --category task-decomposition-and-planning"},
        {"name": "catalog", "purpose": "Inspect umbrella, categories, capability data, and coverage.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py catalog --view coverage"},
        {"name": "parse", "purpose": "Extract a unified catalog from text, Markdown, copied HTML, PDF, or DOCX.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py parse --input agent-multitasking-orchestration/asset-sample-source.html"},
        {"name": "merge", "purpose": "Merge multiple capability sources into one unified catalog.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py merge --input agent-multitasking-orchestration/asset-sample-source.md --input agent-multitasking-orchestration/asset-sample-source.txt"},
        {"name": "validate", "purpose": "Validate JSON, config, catalog, profiles, mission, approval, source, or package.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py validate --kind package"},
        {"name": "run-capability", "aliases": ["run"], "purpose": "Execute one of the 256 capability handlers with structured output.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py run-capability --capability goal-decomposer"},
        {"name": "mission", "purpose": "Run a bounded dependency-aware local mission with retries, cancellation, and resume.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py mission --input agent-multitasking-orchestration/asset-sample-mission.json --state validation-output/mission-state.json"},
        {"name": "approval request|grant|verify", "purpose": "Create pending packets, record explicit human approval, and verify approval records.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py approval request --capability permission-boundary-manager --output approval/pending.json"},
        {"name": "selftest", "purpose": "Run the packaged validation suite through the real CLI and handler interfaces.", "example": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py selftest"},
    ]
    sections = {
        "commands": {"commands": commands},
        "inputs": {
            "accepted_source_formats": SOURCE_FORMATS,
            "accepted_catalog_shapes": ["single capability", "numbered skill list", "one category", "several categories"],
            "parser_ignores": ["HTML tags", "CSS classes", "data attributes", "numbering marks", "page furniture", "extra spacing"],
            "path_rule": "All paths are relative to the future skill root; absolute paths and parent traversal are rejected.",
        },
        "safety": {
            "provider": "local-deterministic only",
            "external_calls": 0,
            "approval_required_for": ["high risk", "critical risk", "explicit approval tasks"],
            "prohibited": ["shell", "network", "arbitrary code", "external tools"],
            "recovery": ["atomic JSON checkpoint", "--resume", "cooperative cancellation"],
        },
        "capabilities": {"count": 256, "command": "capabilities", "coverage": "25 category engines and 256 distinct registered handlers"},
        "examples": {
            "parse_pdf": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py parse --input uploads/catalog.pdf --input-format pdf",
            "run_reasoning": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py run-capability --capability adversarial-critique-processor",
            "run_mission": "python3 agent-multitasking-orchestration/tools-agent-multitasking.py mission --input agent-multitasking-orchestration/asset-sample-mission.json",
        },
    }
    result = {"topic": topic, "tool": PACKAGE_NAME, "version": VERSION, "sections": sections if topic == "all" else {topic: sections[topic]}}
    return result, EXIT_OK


def command_capabilities(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    registry = load_registry(config, paths=paths)
    items = registry.list_capabilities(category=args.category, query=args.query, risk=args.risk)
    data = [item["slug"] for item in items] if args.brief else registry.compact(items)
    result = {
        "status": "ok",
        "command": "capabilities",
        "count": len(items),
        "filters": {"category": args.category, "query": args.query, "risk": args.risk, "brief": args.brief},
        "capabilities": data,
        "coverage_total": registry.coverage()["profile_counts"],
    }
    return result, EXIT_OK


def command_catalog(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    registry = load_registry(config, paths=paths)
    view = args.view
    if view == "umbrella":
        data = registry.catalog["umbrella"]
    elif view == "categories":
        data = [
            {"index": item["index"], "name": item["name"], "slug": item["slug"], "capabilities": len(item["capabilities"])}
            for item in registry.catalog["categories"]
        ]
    elif view == "capabilities":
        data = registry.compact()
    elif view == "coverage":
        data = registry.coverage()
    else:
        data = {
            "umbrella": registry.catalog["umbrella"],
            "source": registry.catalog["source"],
            "counts": registry.catalog["counts"],
            "categories": registry.catalog["categories"],
            "coverage": registry.coverage(),
        }
    return {"status": "ok", "command": "catalog", "view": view, "data": data}, EXIT_OK


def command_parse(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    document = read_source(args.input, source_format=args.input_format, limits=config["limits"], root=paths.root)
    parsed = parse_catalog(
        document,
        umbrella_name=args.umbrella_name,
        umbrella_slug=args.umbrella_slug,
        limits=config["limits"],
    )
    require_schema(parsed.data, load_schema("catalog", paths=paths), label="Parsed catalog")
    return {"status": "ok", "command": "parse", "catalog": parsed.data}, EXIT_OK


def command_merge(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    parsed_list = []
    for source in args.input:
        document = read_source(source, source_format=args.input_format, limits=config["limits"], root=paths.root)
        parsed_list.append(
            parse_catalog(
                document,
                umbrella_name=args.umbrella_name,
                umbrella_slug=args.umbrella_slug,
                limits=config["limits"],
            )
        )
    merged = merge_catalogs(
        parsed_list,
        umbrella_name=args.umbrella_name,
        umbrella_slug=args.umbrella_slug,
    )
    require_schema(merged, load_schema("catalog", paths=paths), label="Merged catalog")
    return {"status": "ok", "command": "merge", "sources": len(parsed_list), "catalog": merged}, EXIT_OK


def _package_reference_check(paths: Any) -> dict[str, Any]:
    package = paths.package
    files = sorted(item for item in package.iterdir() if item.is_file())
    directories = sorted(item.name for item in package.iterdir() if item.is_dir())
    names = [item.name for item in files]
    knowledge = [name for name in names if name.startswith("knowledge-")]
    tools = [name for name in names if name.startswith("tools-")]
    assets = [name for name in names if name.startswith("asset-")]
    unclassified = [name for name in names if not name.startswith(("tools-", "knowledge-", "asset-"))]
    archives = [name for name in names if Path(name).suffix.lower() in {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"}]
    hidden = [name for name in names if name.startswith(".") or name.startswith("~") or name.endswith((".tmp", ".log"))]
    checks = [
        {"name": "no_subfolders", "passed": not directories, "evidence": directories},
        {"name": "no_skill_md_inside_folder", "passed": "SKILL.md" not in names, "evidence": "SKILL.md" in names},
        {"name": "exactly_one_knowledge_readme", "passed": knowledge == ["knowledge-readme.md"], "evidence": knowledge},
        {"name": "primary_entry_point_present", "passed": "tools-agent-multitasking.py" in names, "evidence": "tools-agent-multitasking.py" in names},
        {"name": "all_files_classified", "passed": not unclassified, "evidence": unclassified},
        {"name": "no_nested_archives", "passed": not archives, "evidence": archives},
        {"name": "no_temp_or_log_files", "passed": not hidden, "evidence": hidden},
        {"name": "file_count_under_500", "passed": len(files) <= 500, "evidence": len(files)},
        {"name": "paths_max_three_levels", "passed": all(len(item.relative_to(paths.root).parts) <= 3 for item in files), "evidence": "folder/file"},
    ]
    reference_pattern = re.compile(r"agent-multitasking-orchestration/((?:tools|knowledge|asset)-[A-Za-z0-9][A-Za-z0-9._-]*)")
    referenced: set[str] = set()
    scan_files = [package / "tools-config.json", package / "knowledge-readme.md"] + [
        package / name for name in names if name.endswith((".py", ".json", ".md", ".txt", ".html"))
    ]
    for source in scan_files:
        if source.is_file():
            referenced.update(reference_pattern.findall(source.read_text(encoding="utf-8", errors="ignore")))
    missing_references = sorted(name for name in referenced if not (package / name).is_file())
    checks.append({"name": "internal_references_resolve", "passed": not missing_references, "evidence": missing_references})
    module_pattern = re.compile(r"import_module\(\s*[\"'](tools-[A-Za-z0-9-]+)[\"']\s*\)")
    missing_modules: list[str] = []
    for source in [package / name for name in names if name.endswith(".py")]:
        for module in module_pattern.findall(source.read_text(encoding="utf-8", errors="ignore")):
            if not (package / f"{module}.py").is_file():
                missing_modules.append(module)
    checks.append({"name": "hyphenated_module_references_resolve", "passed": not missing_modules, "evidence": sorted(set(missing_modules))})
    return {
        "valid": all(check["passed"] for check in checks),
        "checks": checks,
        "counts": {"files": len(files), "tools": len(tools), "knowledge": len(knowledge), "assets": len(assets)},
        "files": names,
        "referenced_resources": sorted(referenced),
    }


def command_validate(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    schemas = load_json(paths.safe(config["paths"]["schemas"], must_exist=True), label="Schema bundle")
    kind = args.kind
    checks: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    if kind == "package":
        package = _package_reference_check(paths)
        result = {"status": "ok", "command": "validate", "kind": kind, "valid": package["valid"], "details": package}
        if not package["valid"]:
            raise ToolError(
                "PACKAGE_VALIDATION_FAILED",
                "Package structure or references are invalid.",
                details={"failed_checks": [check for check in package["checks"] if not check["passed"]]},
                recovery="Fix the listed files or references before delivery.",
            )
        return result, EXIT_OK
    if not args.input:
        raise ToolError("INPUT_REQUIRED", "--input is required for this validation kind.", details={"kind": kind})
    path = paths.safe(args.input, must_exist=True)
    if kind == "source":
        document = read_source(args.input, source_format=args.input_format, limits=config["limits"], root=paths.root)
        parsed = parse_catalog(document, limits=config["limits"])
        require_schema(parsed.data, schemas["catalog"], label="Parsed source catalog")
        details = {"counts": parsed.data["counts"], "umbrella": parsed.data["umbrella"], "warnings": parsed.data["warnings"]}
        checks = [{"name": "source_parsed", "passed": True, "evidence": document.source_format}]
    else:
        data = load_json(path, label="Validation input")
        inferred = kind
        if kind == "auto":
            if isinstance(data, dict) and "tasks" in data and "mission_id" in data:
                inferred = "mission"
            elif isinstance(data, dict) and "profiles" in data:
                inferred = "profiles"
            elif isinstance(data, dict) and "categories" in data and "capabilities" in data:
                inferred = "catalog"
            elif isinstance(data, dict) and "paths" in data and "provider" in data:
                inferred = "config"
            elif isinstance(data, dict) and "status" in data and "scope" in data:
                inferred = "approval"
            else:
                inferred = "json"
        if inferred == "json":
            details = {"type": type(data).__name__}
            checks = [{"name": "json_parsed", "passed": True, "evidence": path.name}]
        elif inferred == "config":
            require_schema(data, schemas["config"], label="Configuration")
            details = {"tool": data["tool"]}
            checks = [{"name": "config_schema", "passed": True, "evidence": schemas["config"]["type"]}]
        elif inferred == "catalog":
            require_schema(data, schemas["catalog"], label="Catalog")
            details = {"counts": data["counts"]}
            checks = [{"name": "catalog_schema", "passed": True, "evidence": len(data["capabilities"])}]
        elif inferred == "profiles":
            require_schema(data, schemas["capability_profiles"], label="Capability profiles")
            details = {"counts": data["counts"]}
            checks = [{"name": "profiles_schema", "passed": True, "evidence": len(data["profiles"])}]
        elif inferred == "mission":
            engine = MissionEngine(config, paths=paths)
            details = validate_mission_document(data, config=config, schemas=schemas)
            checks = [{"name": "mission_schema_and_graph", "passed": True, "evidence": len(details["dependency_layers"])}]
        elif inferred == "approval":
            require_schema(data, schemas["approval"], label="Approval record")
            expected_action = args.action or data["action"]
            expected_scope = args.scope or data["scope"]
            verified = verify_approval_record(
                args.input,
                expected_action=expected_action,
                expected_scope=expected_scope,
                paths=paths,
                schemas=schemas,
            )
            details = verified
            checks = [{"name": "approval_schema_scope_expiry", "passed": True, "evidence": expected_scope}]
        else:
            raise ToolError("UNSUPPORTED_VALIDATE_KIND", f"Unsupported validation kind: {inferred}")
    return {
        "status": "ok",
        "command": "validate",
        "kind": kind,
        "resolved_kind": kind if kind != "source" else "source",
        "input": args.input,
        "valid": all(check["passed"] for check in checks),
        "checks": checks,
        "details": details,
    }, EXIT_OK


def command_run_capability(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    registry = load_registry(config, paths=paths)
    runtime = CapabilityRuntime(config, registry, paths=paths)
    outcome = runtime.execute(
        args.capability,
        input_path=args.input,
        approval_file=args.approval_file,
        cancel_file=args.cancel_file,
    )
    return outcome.payload, outcome.exit_code


def command_mission(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    if args.concurrency is not None:
        config["runtime"]["concurrency"] = max(1, min(args.concurrency, config["limits"]["max_workers"]))
    if args.max_runtime_seconds is not None:
        config["runtime"]["max_runtime_seconds"] = max(0.1, min(args.max_runtime_seconds, 300))
    engine = MissionEngine(config, paths=paths)
    outcome = engine.run(
        input_path=args.input,
        state_path=args.state,
        resume=args.resume,
        approval_file=args.approval_file,
        cancel_file=args.cancel_file,
        max_runtime_seconds=args.max_runtime_seconds,
        concurrency=args.concurrency,
    )
    return outcome.payload, outcome.exit_code


def _approval_profile(args: argparse.Namespace, config: dict[str, Any], paths: Any) -> dict[str, Any]:
    if args.capability:
        registry = load_registry(config, paths=paths)
        return registry.resolve(args.capability)
    scope = args.scope or f"custom:{sha256_text(args.name or 'custom')[:12]}"
    return {
        "slug": re.sub(r"[^a-z0-9]+", "-", (args.name or scope).lower()).strip("-") or "custom-action",
        "name": args.name or "Custom sensitive action",
        "risk": args.risk,
        "description": args.description or "Custom sensitive local orchestration action.",
        "approval_scope": scope,
    }


def command_approval(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    config = config_for(args, paths)
    schemas = load_json(paths.safe(config["paths"]["schemas"], must_exist=True), label="Schema bundle")
    action = args.action or ("mission.execute" if (args.scope or "").startswith("mission:") else "capability.execute")
    profile = _approval_profile(args, config, paths)
    if args.approval_command == "request":
        packet = pending_packet(profile, load_json(paths.safe(config["paths"]["policy"], must_exist=True)), expires_at=args.expires_at)
        packet["expected_action"] = action
        packet["expected_scope"] = args.scope or profile["approval_scope"]
        result = {"status": "pending", "command": "approval.request", "packet": packet, "human_approval_recorded": False}
        return result, EXIT_OK
    if args.approval_command == "grant":
        if not args.confirm_human_approval:
            raise ToolError(
                "EXPLICIT_HUMAN_CONFIRMATION_REQUIRED",
                "Approval was not recorded because --confirm-human-approval was not supplied.",
                recovery="Only a human or trusted approval authority should rerun with --confirm-human-approval.",
                exit_code=EXIT_APPROVAL,
            )
        if not args.approved_by or not args.reason:
            raise ToolError(
                "APPROVAL_FIELDS_REQUIRED",
                "--approved-by and --reason are required.",
                exit_code=EXIT_VALIDATION,
            )
        record = approved_record(profile, approved_by=args.approved_by, reason=args.reason, expires_at=args.expires_at)
        record["action"] = action
        record["scope"] = args.scope or profile["approval_scope"]
        require_schema(record, schemas["approval"], label="Approved record")
        result = {"status": "approved", "command": "approval.grant", "record": record, "human_approval_recorded": True}
        return result, EXIT_OK
    if args.approval_command == "verify":
        if not args.file:
            raise ToolError("APPROVAL_FILE_REQUIRED", "--file is required for approval verify.")
        record = load_json(paths.safe(args.file, must_exist=True), label="Approval record")
        expected_action = args.action or record.get("action", "capability.execute")
        expected_scope = args.scope or record.get("scope") or profile["approval_scope"]
        verified = verify_approval_record(
            args.file,
            expected_action=expected_action,
            expected_scope=expected_scope,
            paths=paths,
            schemas=schemas,
        )
        result = {"status": "ok", "command": "approval.verify", "valid": True, "details": verified}
        return result, EXIT_OK
    raise ToolError("UNKNOWN_APPROVAL_COMMAND", f"Unknown approval command: {args.approval_command}", exit_code=EXIT_VALIDATION)


def command_selftest(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    module = import_module("tools-selftest")
    sys.modules.setdefault("tools_selftest", module)
    result = module.run_selftest(paths=paths, config_path=getattr(args, "config", None))
    return result, EXIT_OK if result.get("passed") else EXIT_RUNTIME


def build_parser() -> argparse.ArgumentParser:
    parser = StructuredArgumentParser(
        prog="tools-agent-multitasking.py",
        description="Unified Agent Multitasking Skill Tool: 25 categories, 256 capability handlers, source parsing, bounded mission orchestration, safety gates, and recovery.",
    )
    parser.add_argument("--version", action="version", version=f"{PACKAGE_NAME} {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    add_common(common, output=True)
    common_no_output = argparse.ArgumentParser(add_help=False)
    add_common(common_no_output, output=False)

    help_parser = subparsers.add_parser("help", parents=[common_no_output], help="Built-in structured help.")
    help_parser.add_argument("--topic", choices=HELP_TOPICS, default="all")

    capabilities = subparsers.add_parser(
        "capabilities",
        aliases=["capability-list", "list-capabilities"],
        parents=[common],
        help="List registered capabilities (capability-list alias).",
    )
    capabilities.add_argument("--category", help="Filter by category slug.")
    capabilities.add_argument("--query", help="Filter by case-insensitive text.")
    capabilities.add_argument("--risk", choices=["low", "medium", "high", "critical"], help="Filter by risk level.")
    capabilities.add_argument("--brief", action="store_true", help="Return capability slugs only.")

    catalog = subparsers.add_parser("catalog", parents=[common], help="Inspect catalog, categories, capabilities, and coverage.")
    catalog.add_argument("--view", choices=["umbrella", "categories", "capabilities", "coverage", "all"], default="all")

    parse = subparsers.add_parser("parse", parents=[common], help="Parse a capability source into one unified catalog.")
    parse.add_argument("--input", required=True, help="Root-relative source path.")
    parse.add_argument("--input-format", choices=SOURCE_FORMATS, default="auto")
    parse.add_argument("--umbrella-name", help="Override the umbrella name.")
    parse.add_argument("--umbrella-slug", help="Override the lowercase hyphenated umbrella slug.")

    merge = subparsers.add_parser("merge", parents=[common], help="Merge multiple capability sources into one unified catalog.")
    merge.add_argument("--input", action="append", required=True, help="Repeatable root-relative source path.")
    merge.add_argument("--input-format", choices=SOURCE_FORMATS, default="auto")
    merge.add_argument("--umbrella-name", help="Override the merged umbrella name.")
    merge.add_argument("--umbrella-slug", help="Override the merged umbrella slug.")

    validate = subparsers.add_parser("validate", parents=[common], help="Validate inputs and package compatibility.")
    validate.add_argument("--kind", choices=VALIDATE_KINDS, default="auto")
    validate.add_argument("--input", help="Root-relative input path; not required for --kind package.")
    validate.add_argument("--input-format", choices=SOURCE_FORMATS, default="auto")
    validate.add_argument("--capability", help="Capability for approval validation.")
    validate.add_argument("--scope", help="Expected approval scope.")
    validate.add_argument("--action", help="Expected approval action.")

    run_capability = subparsers.add_parser("run-capability", aliases=["run"], parents=[common], help="Execute one capability handler.")
    run_capability.add_argument("--capability", required=True, help="Capability slug, id, or exact name.")
    run_capability.add_argument("--input", help="Root-relative JSON context path; defaults to sample mission.")
    run_capability.add_argument("--approval-file", help="Root-relative approved record for high or critical risk.")
    run_capability.add_argument("--cancel-file", help="Root-relative cooperative cancellation marker.")

    mission = subparsers.add_parser("mission", parents=[common], help="Run a bounded local mission.")
    mission.add_argument("--input", required=True, help="Root-relative mission JSON path.")
    mission.add_argument("--state", help="Root-relative atomic checkpoint path.")
    mission.add_argument("--resume", action="store_true", help="Resume from --state, skipping completed tasks.")
    mission.add_argument("--approval-file", help="Root-relative approved record for sensitive missions.")
    mission.add_argument("--cancel-file", help="Root-relative cooperative cancellation marker.")
    mission.add_argument("--max-runtime-seconds", type=float, help="Override the global mission deadline.")
    mission.add_argument("--concurrency", type=int, help="Override configured concurrency.")

    approval = subparsers.add_parser("approval", help="Request, explicitly grant, or verify human approval.")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
    for name, help_text in [
        ("request", "Create a pending approval packet; this is not approval."),
        ("grant", "Create an approved record only with explicit human confirmation."),
        ("verify", "Verify an approved record, scope, and expiry."),
    ]:
        child = approval_sub.add_parser(name, parents=[common], help=help_text)
        child.add_argument("--capability", help="Capability slug, id, or exact name.")
        child.add_argument("--scope", help="Custom expected scope, for example mission:identifier.")
        child.add_argument("--action", help="Custom action, for example mission.execute.")
        child.add_argument("--name", help="Custom action name.")
        child.add_argument("--description", help="Custom action description.")
        child.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="high")
        child.add_argument("--expires-at", help="ISO 8601 expiry timestamp.")
        child.add_argument("--file", help="Approval file for verify.")
        child.add_argument("--approved-by", help="Human approver identifier for grant.")
        child.add_argument("--reason", help="Human approval reason for grant.")
        child.add_argument("--confirm-human-approval", action="store_true", help="Explicitly confirm that a human approved this action.")

    subparsers.add_parser("selftest", parents=[common], help="Run the packaged validation suite.")
    return parser


def dispatch(args: argparse.Namespace, paths: Any) -> tuple[dict[str, Any], int]:
    command = args.command
    if command == "help":
        return command_help(args, paths)
    if command in {"capabilities", "capability-list", "list-capabilities"}:
        return command_capabilities(args, paths)
    if command == "catalog":
        return command_catalog(args, paths)
    if command == "parse":
        return command_parse(args, paths)
    if command == "merge":
        return command_merge(args, paths)
    if command == "validate":
        return command_validate(args, paths)
    if command in {"run-capability", "run"}:
        return command_run_capability(args, paths)
    if command == "mission":
        return command_mission(args, paths)
    if command == "approval":
        return command_approval(args, paths)
    if command == "selftest":
        return command_selftest(args, paths)
    raise ToolError("UNKNOWN_COMMAND", f"Unknown command: {command}", exit_code=EXIT_VALIDATION)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    paths = current_paths()
    try:
        args = parser.parse_args(argv)
    except ToolError as exc:
        placeholder = argparse.Namespace(command="argument-parsing", compact=False, output=None)
        return error_emit("argument-parsing", exc, placeholder)
    try:
        result, exit_code = dispatch(args, paths)
        return emit(args.command, result, args, paths, exit_code=exit_code)
    except ToolError as exc:
        return error_emit(args.command, exc, args)
    except KeyboardInterrupt:
        error = ToolError(
            "CANCELLED_BY_USER",
            "Execution was interrupted.",
            recovery="Remove any cancellation marker and rerun with --resume if a state path was used.",
            exit_code=EXIT_CANCELLED,
        )
        return error_emit(args.command, error, args)
    except Exception as exc:  # Defensive structured boundary for unexpected runtime faults.
        error = ToolError(
            "UNEXPECTED_RUNTIME_ERROR",
            "An unexpected runtime error occurred.",
            details={"type": type(exc).__name__, "message": str(exc)},
            recovery="Run validate --kind package and selftest; inspect the input and configuration.",
            exit_code=EXIT_RUNTIME,
        )
        return error_emit(args.command, error, args)


if __name__ == "__main__":
    raise SystemExit(main())
