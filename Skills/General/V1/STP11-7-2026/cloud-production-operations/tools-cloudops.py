#!/usr/bin/env python3
"""Primary CLI for the cloud-production-operations Skill Tool."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys

# Do not create __pycache__ inside the reusable component folder.
sys.dont_write_bytecode = True
TOOL_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cloud_production_operations_engine", TOOL_DIR / "tools-engine.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load tools-engine.py")
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def path(value: str) -> Path:
    return Path(value).expanduser()


def positive_number(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def add_asset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", type=path, default=TOOL_DIR / "asset-capabilities.json", help="Capability catalog JSON (default: sibling asset-capabilities.json).")
    parser.add_argument("--policy", type=path, default=TOOL_DIR / "asset-default-policy.json", help="Operational policy JSON (default: sibling asset-default-policy.json).")
    parser.add_argument("--timeout-seconds", type=positive_number, help="Cooperative local-work deadline; bounded by policy.")


def add_output_arguments(parser: argparse.ArgumentParser, default_format: str = "json") -> None:
    parser.add_argument("--format", choices=("json", "markdown"), default=default_format, help="Structured output format (default: %(default)s).")
    parser.add_argument("--output", type=path, help="Write atomically to this path instead of stdout. Existing files require --force.")
    parser.add_argument("--force", action="store_true", help="Explicitly authorize replacement of the output file; never authorizes cloud changes.")


def add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, type=path, help="Request .json, or a plain-text/Markdown/HTML selection file.")
    parser.add_argument("--schema", type=path, default=TOOL_DIR / "asset-request-schema.json", help="Request schema JSON (default: sibling asset-request-schema.json).")
    parser.add_argument("--capability", action="append", default=[], help="Additional capability ID or exact name; repeatable.")
    parser.add_argument("--category", action="append", default=[], help="Additional category ID or exact name; expands to all category capabilities; repeatable.")
    parser.add_argument("--service", help="Override context.service.")
    parser.add_argument("--owner", help="Override context.owner.")
    parser.add_argument("--provider", choices=("unspecified", "aws", "azure", "gcp", "oracle", "hybrid", "self-hosted", "other"), help="Override context.provider.")
    parser.add_argument("--environment", choices=("development", "test", "staging", "production"), help="Override context.environment.")
    parser.add_argument("--intent", choices=("preview", "execute"), dest="change_intent", help="Override change_intent. 'execute' still creates only an external handoff plan.")
    add_asset_arguments(parser)
    add_output_arguments(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-cloudops.py",
        description="Offline, provider-neutral planning and evidence governance for 249 cloud/server/production capabilities.",
        epilog="Safety: this tool never executes cloud/provider changes. Execution intent produces an approval-gated external handoff only.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {engine.TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capabilities = subparsers.add_parser(
        "capabilities", aliases=["capability-list"], help="List/search every capability; optionally filter by category, capability, or selection file.",
        description="List the complete catalog or resolve filters to a unified selection.",
    )
    capabilities.add_argument("--category", action="append", default=[], help="Category ID or exact name; repeatable.")
    capabilities.add_argument("--capability", action="append", default=[], help="Capability ID or exact name; repeatable.")
    capabilities.add_argument("--select-input", type=path, help="Additional plain-text, Markdown, or copied-HTML selection.")
    capabilities.add_argument("--query", help="Case-insensitive substring over IDs, names, descriptions, and categories.")
    add_asset_arguments(capabilities)
    add_output_arguments(capabilities)

    select = subparsers.add_parser(
        "select", aliases=["resolve"], help="Resolve plain text, Markdown, or copied HTML into exact catalog capabilities.",
        description="Ignore markup, CSS/data attributes, descriptions, list numbers, and extra spacing; expand categories into all related capabilities.",
    )
    select.add_argument("--input", required=True, type=path, help="Selection text/Markdown/HTML file, or JSON containing a selection object.")
    add_asset_arguments(select)
    add_output_arguments(select)

    validate = subparsers.add_parser("validate", help="Validate schema, selection, ownership, and execution input gates.")
    add_request_arguments(validate)
    plan = subparsers.add_parser("plan", help="Validate input and generate a capability-specific plan with tasks, safeguards, controls, and rollback gates.")
    add_request_arguments(plan)
    assess = subparsers.add_parser(
        "assess", aliases=["evaluate"], help="Assess supplied evidence and request inputs against plan controls; no live execution.",
    )
    add_request_arguments(assess)
    run = subparsers.add_parser("run", help="Run validate + plan + evidence assessment and emit one combined artifact.")
    add_request_arguments(run)
    return parser


def validate_schema_compatibility(schema: dict) -> None:
    """Reject unsupported or unsafe custom schemas instead of silently ignoring keywords."""
    allowed = {
        "$schema", "$id", "title", "description", "type", "const", "enum", "default", "properties",
        "required", "additionalProperties", "anyOf", "items", "maxItems", "uniqueItems", "minLength",
        "maxLength", "format", "minimum", "maximum",
    }
    visited = 0

    def walk(node, location: str, depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if visited > 10_000 or depth > 40:
            raise engine.CloudOpsError("invalid-schema", "Custom schema exceeds the supported complexity bound.")
        if not isinstance(node, dict):
            raise engine.CloudOpsError("invalid-schema", f"Schema node {location} must be an object.")
        unsupported = set(node) - allowed
        if unsupported:
            raise engine.CloudOpsError("invalid-schema", f"Unsupported schema keyword(s) at {location}: {', '.join(sorted(unsupported))}.")
        if "format" in node and node["format"] != "date-time":
            raise engine.CloudOpsError("invalid-schema", f"Unsupported string format at {location}: {node['format']!r}.")
        if "type" in node and node["type"] not in {"object", "array", "string", "boolean", "integer", "number"}:
            raise engine.CloudOpsError("invalid-schema", f"Unsupported schema type at {location}: {node['type']!r}.")
        if not isinstance(node.get("properties", {}), dict) or not isinstance(node.get("anyOf", []), list):
            raise engine.CloudOpsError("invalid-schema", f"Schema properties/anyOf at {location} have invalid types.")
        if "items" in node and not isinstance(node["items"], dict):
            raise engine.CloudOpsError("invalid-schema", f"Schema items at {location} must be an object.")
        if "required" in node and (not isinstance(node["required"], list) or not all(isinstance(item, str) for item in node["required"])):
            raise engine.CloudOpsError("invalid-schema", f"Schema required at {location} must be an array of strings.")
        for key, child in node.get("properties", {}).items():
            walk(child, f"{location}.properties.{key}", depth + 1)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{location}.items", depth + 1)
        for index, child in enumerate(node.get("anyOf", [])):
            walk(child, f"{location}.anyOf[{index}]", depth + 1)

    walk(schema, "$")
    properties = schema.get("properties", {})
    top_required = {"schema_version", "selection", "context"}
    top_properties = {"schema_version", "selection", "context", "change_intent", "approval", "recovery", "verification", "evidence"}
    if not top_required.issubset(schema.get("required", [])) or not top_properties.issubset(properties):
        raise engine.CloudOpsError("invalid-schema", "Schema is missing required runtime top-level properties.")
    if properties["schema_version"].get("const") != "1.0":
        raise engine.CloudOpsError("invalid-schema", "Schema must pin schema_version to '1.0'.")
    nested = {
        "selection": {"capabilities", "categories", "text"},
        "context": {"service", "owner", "provider", "environment", "regions", "change_ticket", "data_classification", "blast_radius"},
        "approval": {"approved", "approver", "reference", "approved_at", "expires_at"},
        "recovery": {"rollback_strategy", "backup_reference", "rto_minutes", "rpo_minutes"},
        "verification": {"success_criteria", "monitoring"},
    }
    for section, keys in nested.items():
        section_schema = properties[section]
        if not keys.issubset(section_schema.get("properties", {})):
            raise engine.CloudOpsError("invalid-schema", f"Schema {section} is missing runtime properties: {', '.join(sorted(keys))}.")
    if not {"service", "owner", "provider", "environment"}.issubset(properties["context"].get("required", [])):
        raise engine.CloudOpsError("invalid-schema", "Schema context must require service, owner, provider, and environment.")
    if properties["change_intent"].get("default") != "preview":
        raise engine.CloudOpsError("invalid-schema", "Safety invariant: change_intent must default to preview.")
    if properties["approval"]["properties"]["approved"].get("default") is not False:
        raise engine.CloudOpsError("invalid-schema", "Safety invariant: approval.approved must default to false.")
    evidence_items = properties["evidence"].get("items", {})
    evidence_keys = {"control_id", "status", "source", "observed_at", "summary"}
    if not evidence_keys.issubset(evidence_items.get("properties", {})) or not evidence_keys.issubset(evidence_items.get("required", [])):
        raise engine.CloudOpsError("invalid-schema", "Schema evidence items are missing required runtime fields.")


class Runtime:
    def __init__(self, args: argparse.Namespace):
        self.policy = engine.load_policy(args.policy)
        limits = self.policy["limits"]
        configured_timeout = float(args.timeout_seconds if args.timeout_seconds is not None else limits["default_timeout_seconds"])
        maximum_timeout = float(limits["max_timeout_seconds"])
        if configured_timeout > maximum_timeout:
            raise engine.CloudOpsError("invalid-timeout", f"Timeout {configured_timeout:g}s exceeds the policy maximum {maximum_timeout:g}s.")
        self.deadline = engine.Deadline(configured_timeout)
        self.catalog = engine.Catalog.load(args.catalog, int(limits["max_catalog_bytes"]))
        self.deadline.check()

    def schema(self, args: argparse.Namespace) -> dict:
        schema = engine.load_json(args.schema, 1_048_576, label="request schema")
        if not isinstance(schema, dict):
            raise engine.CloudOpsError("invalid-schema", "Request schema must be a JSON object.")
        validate_schema_compatibility(schema)
        self.deadline.check()
        return schema


def emit(artifact: dict, args: argparse.Namespace, policy: dict) -> None:
    if args.format == "json":
        content = json.dumps(artifact, indent=2, ensure_ascii=False) + "\n"
    else:
        content = engine.render_markdown(artifact)
    if args.output:
        engine.atomic_write(args.output, content, force=args.force, max_bytes=int(policy["limits"]["max_output_bytes"]))
        if args.format == "json":
            receipt = {
                "status": "written",
                "artifact_type": artifact.get("artifact_type"),
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "external_execution_performed": False,
            }
            if "ready" in artifact:
                receipt["ready"] = artifact["ready"]
            print(json.dumps(receipt, ensure_ascii=False))
        else:
            print(f"Wrote {args.output} ({args.output.stat().st_size} bytes). No external execution performed.")
    else:
        sys.stdout.write(content)


def selection_artifact(catalog: engine.Catalog, capabilities: list[dict], selection_info: dict) -> dict:
    categories: list[dict] = []
    for category in catalog.categories:
        caps = [cap for cap in capabilities if cap["category_id"] == category["id"]]
        if caps:
            categories.append({
                "id": category["id"], "name": category["name"],
                "capabilities": [{key: cap[key] for key in ("id", "name", "description", "risk", "flags")} for cap in caps],
            })
    return {
        "artifact_type": "capability-list",
        "artifact_version": "1.0",
        "catalog_version": catalog.data["catalog_version"],
        "category_count": len(categories),
        "capability_count": len(capabilities),
        "selection": selection_info,
        "categories": categories,
    }


def command_capabilities(args: argparse.Namespace) -> tuple[dict, int]:
    runtime = Runtime(args)
    selection_info: dict = {}
    if args.category or args.capability or args.select_input:
        selection = {"capabilities": args.capability, "categories": args.category}
        if args.select_input:
            raw = engine.read_text(args.select_input, int(runtime.policy["limits"]["max_input_bytes"]), label="selection")
            if len(raw) > int(runtime.policy["limits"]["max_text_characters"]):
                raise engine.CloudOpsError("input-too-large", "Selection text exceeds the configured character limit.")
            selection["text"] = raw
        capabilities, selection_info = runtime.catalog.resolve(selection, runtime.deadline)
    else:
        capabilities = list(runtime.catalog.capabilities)
    if args.query:
        query = args.query.casefold()
        capabilities = [
            cap for cap in capabilities
            if query in " ".join((cap["id"], cap["name"], cap["description"], cap["category_name"], cap["category_id"])).casefold()
        ]
        if not capabilities:
            raise engine.CloudOpsError("no-capability-selected", f"No capabilities match query {args.query!r}.")
    runtime.deadline.check()
    return selection_artifact(runtime.catalog, capabilities, selection_info), 0


def command_select(args: argparse.Namespace) -> tuple[dict, int]:
    runtime = Runtime(args)
    raw = engine.read_text(args.input, int(runtime.policy["limits"]["max_input_bytes"]), label="selection")
    if len(raw) > int(runtime.policy["limits"]["max_text_characters"]):
        raise engine.CloudOpsError("input-too-large", "Selection text exceeds the configured character limit.")
    parse_warnings: list[str] = []
    if args.input.suffix.casefold() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise engine.CloudOpsError("invalid-json", f"Invalid selection JSON: line {exc.lineno}, column {exc.colno}.") from exc
        selection = data.get("selection") if isinstance(data, dict) and "selection" in data else data
        if not isinstance(selection, dict):
            raise engine.CloudOpsError("invalid-selection", "Selection JSON must be an object or a request object containing selection.")
    else:
        parsed = runtime.catalog.parse_selection_text(raw, runtime.deadline)
        selection = {"capabilities": parsed["capabilities"], "categories": parsed["categories"]}
        parse_warnings = parsed["warnings"]
    capabilities, info = runtime.catalog.resolve(selection, runtime.deadline)
    info["warnings"] = parse_warnings + info.get("warnings", [])
    artifact = {
        "artifact_type": "selection-result",
        "artifact_version": "1.0",
        "capability_count": len(capabilities),
        "selection": info,
        "capabilities": [{key: cap[key] for key in ("id", "name", "category_id", "category_name", "description", "risk")} for cap in capabilities],
        "warnings": info.get("warnings", []),
    }
    return artifact, 0


def load_request(args: argparse.Namespace, runtime: Runtime) -> dict:
    raw_text = engine.read_text(args.input, int(runtime.policy["limits"]["max_input_bytes"]), label="request")
    runtime.deadline.check()
    if args.input.suffix.casefold() == ".json":
        try:
            request = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise engine.CloudOpsError(
                "invalid-json", f"Invalid request JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}.",
                details={"path": str(args.input), "line": exc.lineno, "column": exc.colno},
            ) from exc
        if not isinstance(request, dict):
            raise engine.CloudOpsError("request-not-object", "Request JSON must contain an object.")
        request = copy.deepcopy(request)
    else:
        if len(raw_text) > int(runtime.policy["limits"]["max_text_characters"]):
            raise engine.CloudOpsError("input-too-large", "Selection text exceeds the configured character limit.")
        request = {
            "schema_version": "1.0",
            "selection": {"text": raw_text},
            "context": {"service": "", "owner": "", "provider": "unspecified", "environment": "production"},
        }
    if args.capability or args.category:
        selection = request.setdefault("selection", {})
        if isinstance(selection, dict):
            if args.capability:
                selection.setdefault("capabilities", [])
                if isinstance(selection["capabilities"], list):
                    selection["capabilities"].extend(args.capability)
            if args.category:
                selection.setdefault("categories", [])
                if isinstance(selection["categories"], list):
                    selection["categories"].extend(args.category)
    overrides = {key: getattr(args, key) for key in ("service", "owner", "provider", "environment") if getattr(args, key) is not None}
    if overrides:
        context = request.setdefault("context", {})
        if isinstance(context, dict):
            context.update(overrides)
    if args.change_intent is not None:
        request["change_intent"] = args.change_intent
    return request


def validated(args: argparse.Namespace, runtime: Runtime):
    request = load_request(args, runtime)
    schema = runtime.schema(args)
    report, normalized, capabilities, _ = engine.validate_request(request, schema, runtime.catalog, runtime.policy, runtime.deadline)
    return report, normalized, capabilities


def invalid_request_error(report: dict) -> engine.CloudOpsError:
    return engine.CloudOpsError("invalid-request", "Request validation failed; see the structured findings.", details=report)


def command_validate(args: argparse.Namespace) -> tuple[dict, int]:
    runtime = Runtime(args)
    report, _, _ = validated(args, runtime)
    return report, 0 if report["valid_for_planning"] else 2


def command_plan(args: argparse.Namespace) -> tuple[dict, int]:
    runtime = Runtime(args)
    report, request, capabilities = validated(args, runtime)
    if not report["valid_for_planning"] or request is None:
        raise invalid_request_error(report)
    plan = engine.build_plan(request, capabilities, report, runtime.policy, runtime.deadline)
    runtime.deadline.check()
    return plan, 0


def command_assess(args: argparse.Namespace, combined: bool = False) -> tuple[dict, int]:
    runtime = Runtime(args)
    report, request, capabilities = validated(args, runtime)
    if not report["valid_for_planning"] or request is None:
        raise invalid_request_error(report)
    plan = engine.build_plan(request, capabilities, report, runtime.policy, runtime.deadline)
    assessment = engine.assess_evidence(request, plan, runtime.policy, runtime.deadline)
    runtime.deadline.check()
    artifact = engine.make_run_artifact(plan, assessment) if combined else assessment
    return artifact, 0 if assessment["ready"] else 3


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"capabilities", "capability-list"}:
            artifact, exit_code = command_capabilities(args)
        elif args.command in {"select", "resolve"}:
            artifact, exit_code = command_select(args)
        elif args.command == "validate":
            artifact, exit_code = command_validate(args)
        elif args.command == "plan":
            artifact, exit_code = command_plan(args)
        elif args.command in {"assess", "evaluate"}:
            artifact, exit_code = command_assess(args, combined=False)
        elif args.command == "run":
            artifact, exit_code = command_assess(args, combined=True)
        else:  # argparse enforces commands; this is a defensive guard.
            raise engine.CloudOpsError("unknown-command", f"Unsupported command {args.command!r}.", exit_code=5)
        emit(artifact, args, engine.load_policy(args.policy))
        return exit_code
    except engine.CloudOpsError as exc:
        if getattr(args, "format", "json") == "json":
            print(json.dumps(engine.error_envelope(exc), ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.details is not None:
                print(json.dumps(exc.details, ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        exc = engine.CloudOpsError("cancelled", "Operation cancelled; no partial output was committed.", exit_code=130)
        print(json.dumps(engine.error_envelope(exc)), file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:  # Stable error boundary; no traceback or sensitive request dump.
        wrapped = engine.CloudOpsError("internal-error", f"Unexpected {type(exc).__name__}; inspect inputs and tool compatibility.", exit_code=5)
        print(json.dumps(engine.error_envelope(wrapped)), file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
