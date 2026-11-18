#!/usr/bin/env python3
"""Primary entry point for the unified offline research Skill Tool.

Run from a future skill root, for example:
python3 offline-research-writing-translation/tools-offline-research.py help

This is a reusable Skill Tool component, not a complete Copilot skill.
It contains no SKILL.md and makes no network, shell, or arbitrary-code calls.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

TOOL_DIR = Path(__file__).resolve().parent
MODULE_FILES = {
    "catalog_adapter": ("tools-catalog-adapter.py", "orwt_catalog_adapter"),
    "engine": ("tools-workflow-engine.py", "orwt_engine"),
    "domain": ("tools-domain-operations.py", "orwt_domain_operations"),
    "audit": ("tools-package-audit.py", "orwt_package_audit"),
}
ACTIVE_CTX: dict[str, Any] = {"ctx": None}
PARSER: argparse.ArgumentParser | None = None


def _load_module(filename: str, module_name: str) -> Any:
    path = TOOL_DIR / filename
    if not path.is_file():
        raise RuntimeError(f"Required module is missing: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


try:
    ADAPTER = _load_module(*MODULE_FILES["catalog_adapter"])
    E = _load_module(*MODULE_FILES["engine"])
    DOMAIN_MODULE = _load_module(*MODULE_FILES["domain"])
    AUDIT = _load_module(*MODULE_FILES["audit"])
    DomainOperations = DOMAIN_MODULE.DomainOperations
except Exception as exc:  # pragma: no cover - fatal packaging error
    print(json.dumps({"ok": False, "error": {"code": "MODULE_LOAD_FAILED", "message": str(exc), "recovery": "Restore the exact prefixed tool files in the Skill Tool folder."}}, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(70)


PIPELINE_ORDER = [
    "deployment",
    "ingest",
    "scholarly-corpus",
    "reference-corpus",
    "news-archive",
    "legal-corpus",
    "standards-library",
    "bibliographic",
    "deduplication",
    "security",
    "rights",
    "provenance",
    "retrieval",
    "semantic-retrieval",
    "question-answering",
    "document-layout",
    "audio-transcripts",
    "citations",
    "evidence-review",
    "fact-check",
    "composition",
    "specialized-writing",
    "editing",
    "originality",
    "translation",
    "terminology",
    "localization-qa",
    "knowledge-graph",
    "evaluation",
    "human-review",
    "packaging",
    "governance",
]


HELP_TEXT = """offline-research-writing-translation | unified offline research, writing, and translation Skill Tool

Primary run command:
  python3 offline-research-writing-translation/tools-offline-research.py <command> [options]

Start here:
  help                                  Show this help and full command help.
  operations                            List the 32 category-level operations.
  capabilities --limit 10               List the 480 exact catalog capabilities.
  capabilities --verify                 Verify every exact capability declaration and handler.
  run --operation retrieval --input offline-research-writing-translation/asset-sample-corpus.jsonl --params '{"query":"checksum manifest"}'
  pipeline --input offline-research-writing-translation/asset-sample-corpus.jsonl --query "offline research"
  tools-selftest.py                     Run the packaged validation suite (invoke with python3).

Use --config, --catalog, --workspace, --input, --output, --params, --timeout,
--retries, --cancel-file, --overwrite, and --run-id after each subcommand.
All results and errors are structured JSON. Sensitive actions require an
explicit human approval token from review approve. No network calls are made.
"""


def make_run_id(value: str | None) -> str:
    raw = value or f"run-{secrets.token_hex(4)}"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    return (cleaned[:64] or f"run-{secrets.token_hex(4)}")


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        rel = os.path.relpath(str(path), str(Path.cwd()))
        if rel.startswith(".."):
            return path.name
        return rel.replace(os.sep, "/")
    except (OSError, ValueError):
        return path.name


def build_context(args: argparse.Namespace) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    config = E.load_config(TOOL_DIR, getattr(args, "config", None))
    catalog_path = Path(args.catalog) if getattr(args, "catalog", None) else TOOL_DIR / config["asset_paths"]["catalog"]
    catalog = E.load_catalog(catalog_path, ADAPTER)
    request_schema_path = TOOL_DIR / config["asset_paths"]["request_schema"]
    request_schema = E.load_request_schema(request_schema_path)
    workspace = getattr(args, "workspace", None) or config["storage"]["workspace"]
    run_id = make_run_id(getattr(args, "run_id", None))
    timeout = float(args.timeout) if getattr(args, "timeout", None) is not None else float(config["runtime"]["timeout_seconds"])
    retries = int(args.retries) if getattr(args, "retries", None) is not None else int(config["runtime"]["retries"])
    ctx = E.RunContext(
        tool_dir=TOOL_DIR,
        workspace=workspace,
        config=config,
        catalog=catalog,
        request_schema=request_schema,
        adapter=ADAPTER,
        run_id=run_id,
        timeout_seconds=timeout,
        retries=retries,
        cancel_file=getattr(args, "cancel_file", None),
        overwrite=bool(getattr(args, "overwrite", False)),
        keep_temp=bool(getattr(args, "keep_temp", False)),
        workspace_from_default=getattr(args, "workspace", None) is None,
    )
    ACTIVE_CTX["ctx"] = ctx
    return ctx, catalog, request_schema


def emit(
    ctx: Any,
    command: str,
    operation: str | None,
    result: dict[str, Any],
    args: argparse.Namespace,
    started: float,
    text_stdout: str | None = None,
) -> None:
    out_arg = getattr(args, "output", None)
    path = ctx.user_path(out_arg, for_output=True) if out_arg else ctx.default_output(operation or command)
    payload = E.structured_result(
        command=command,
        operation=operation,
        run_id=ctx.run_id,
        result=result,
        warnings=result.get("warnings", []),
        output_path=display_path(path),
        config_digest=ctx.config_digest,
        elapsed_seconds=time.monotonic() - started,
    )
    E.write_json(ctx, path, payload)
    if text_stdout is not None:
        print(text_stdout)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def lookup_capability(ctx: Any, name: str) -> dict[str, Any]:
    key = name.strip().casefold()
    capability = ctx.catalog["_by_name"].get(key) or ctx.catalog["_by_slug"].get(name.strip())
    if capability:
        return capability
    names = [c["name"] for c in ctx.catalog["capabilities"]]
    suggestions = difflib.get_close_matches(name, names, n=5, cutoff=0.45)
    raise E.InputError("Exact Skill Tool name was not found.", {"name": name, "suggestions": suggestions}, "Use capabilities to list exact names, or pass the exact slug.")


def execute_request(
    ctx: Any,
    request: dict[str, Any],
    domain: Any,
    started: float,
    command: str,
    write_output: bool = True,
) -> dict[str, Any]:
    request = E.validate_request(request, ctx.request_schema, ctx.catalog)
    operation = request["operation"]
    params = dict(request.get("params", {}))
    input_path = request.get("input")
    output_path: Path | None = None
    if request.get("output"):
        output_path = ctx.user_path(request["output"], for_output=True)
    elif write_output:
        output_path = ctx.default_output(operation)
    result = domain.execute(
        operation,
        params,
        input_path,
        display_path(output_path),
        capability=None,
        approval_token=request.get("approval_token"),
    )
    payload = E.structured_result(
        command=command,
        operation=operation,
        run_id=ctx.run_id,
        result=result,
        warnings=result.get("warnings", []),
        output_path=display_path(output_path) if write_output or output_path else None,
        config_digest=ctx.config_digest,
        elapsed_seconds=time.monotonic() - started,
    )
    if output_path is not None:
        E.write_json(ctx, output_path, payload)
    return payload


# ---------- simple command handlers ----------

def cmd_help(args: argparse.Namespace) -> None:
    print(HELP_TEXT)
    if PARSER is not None:
        print(PARSER.format_help())


def cmd_version(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, catalog, _ = build_context(args)
    result = {
        "tool": ctx.config["tool"],
        "version": ctx.config["version"],
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "catalog_edition": catalog.get("edition"),
        "category_count": catalog.get("category_count"),
        "capability_count": catalog.get("capability_count"),
        "operation_count": catalog.get("operation_count"),
        "skill_tool_file_count": sum(1 for p in TOOL_DIR.iterdir() if p.is_file() and p.name.startswith(("tools-", "knowledge-", "asset-"))),
        "unavailable_integrations": E.unavailable_integrations(ctx),
    }
    emit(ctx, "version", None, result, args, started)


def cmd_operations(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, catalog, _ = build_context(args)
    operations = [{"name": name, "description": desc} for name, desc in sorted(ADAPTER.OPERATION_DESCRIPTIONS.items())]
    result = {"operation_count": len(operations), "operations": operations, "catalog_edition": catalog.get("edition")}
    emit(ctx, "operations", None, result, args, started)


def cmd_capabilities(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, catalog, _ = build_context(args)
    capabilities = list(catalog["capabilities"])
    if args.category:
        needle = args.category.casefold()
        capabilities = [c for c in capabilities if needle == c["category"].casefold() or needle in c["category"].casefold()]
    if args.operation:
        capabilities = [c for c in capabilities if c["operation"] == args.operation]
    total_filtered = len(capabilities)
    offset = max(0, int(args.offset))
    limit = max(0, int(args.limit))
    returned = capabilities[offset : offset + limit] if limit else []
    verification: dict[str, Any] | None = None
    if args.verify:
        domain = DomainOperations(ctx)
        known = set(ADAPTER.OPERATION_DESCRIPTIONS)
        unknown = sorted({c["operation"] for c in catalog["capabilities"]} - known)
        missing_handlers = sorted({c["operation"] for c in catalog["capabilities"] if not hasattr(domain, "op_" + c["operation"].replace("-", "_"))})
        declared = 0
        for i, capability in enumerate(catalog["capabilities"], 1):
            if i % 50 == 0:
                ctx.check_limits()
            declaration = E.capability_declaration(ctx, capability, None, None, {}, True)
            if declaration["exact_name"] == capability["name"] and declaration["operation"] == capability["operation"]:
                declared += 1
        verification = {"declared_count": declared, "expected_count": catalog["capability_count"], "unknown_operations": unknown, "missing_handlers": missing_handlers, "passed": declared == catalog["capability_count"] and not unknown and not missing_handlers}
    operations = [{"name": name, "description": desc} for name, desc in sorted(ADAPTER.OPERATION_DESCRIPTIONS.items())]
    result = {
        "capability_count": catalog["capability_count"],
        "category_count": catalog["category_count"],
        "operation_count": len(operations),
        "filtered_count": total_filtered,
        "returned_count": len(returned),
        "offset": offset,
        "limit": limit,
        "capabilities": returned,
        "operations": operations,
        "verification": verification,
        "catalog_edition": catalog.get("edition"),
        "source": catalog.get("source"),
    }
    text = None
    if args.format == "table":
        lines = ["CATEGORY | EXACT SKILL TOOL NAME | OPERATION"]
        for c in returned:
            lines.append(f"{c['category']} | {c['name']} | {c['operation']}")
        text = "\n".join(lines)
    emit(ctx, "capabilities", None, result, args, started, text_stdout=text)


def cmd_capability(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    capability = lookup_capability(ctx, args.name)
    params = E.parse_json_arg(args.params, "params")
    plan_only = bool(args.plan_only or not args.input)
    declaration = E.capability_declaration(ctx, capability, args.input, args.output, params, plan_only)
    execution = None
    if not plan_only:
        domain = DomainOperations(ctx)
        execution = domain.execute(capability["operation"], params, args.input, args.output, capability=capability)
        declaration["status"] = "executed"
    result = {"capability": declaration, "execution": execution, "plan_only": plan_only}
    emit(ctx, "capability", capability["operation"], result, args, started)


def cmd_run_capability(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    capability = lookup_capability(ctx, args.name)
    if not args.plan_only and not args.input:
        raise E.InputError("run-capability requires --input unless --plan-only is used.", {"name": args.name}, "Supply a local corpus path, or pass --plan-only for a validated declaration.")
    params = E.parse_json_arg(args.params, "params")
    plan_only = bool(args.plan_only)
    declaration = E.capability_declaration(ctx, capability, args.input, args.output, params, plan_only)
    execution = None
    if not plan_only:
        domain = DomainOperations(ctx)
        execution = domain.execute(capability["operation"], params, args.input, args.output, capability=capability, approval_token=args.approval_token)
        declaration["status"] = "executed"
    result = {"capability": declaration, "execution": execution, "plan_only": plan_only, "dispatch": "exact-name-to-shared-handler"}
    emit(ctx, "run-capability", capability["operation"], result, args, started)


def cmd_catalog_parse(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    path = ctx.user_path(args.input, must_exist=True)
    text = E.read_text_file(ctx, path)
    try:
        parsed = ADAPTER.parse_catalog(text, umbrella_name=args.umbrella_name, category_hint=args.category_hint)
    except ValueError as exc:
        raise E.InputError("Catalog source could not be parsed.", {"error": str(exc), "input": path.name}, "Use plain text, Markdown, or copied HTML with category names, Skill Tool Names, and Descriptions.")
    result = {"source": path.name, "parsed": parsed}
    emit(ctx, "catalog parse", None, result, args, started)


def cmd_catalog_unify(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    path = ctx.user_path(args.input, must_exist=True)
    text = E.read_text_file(ctx, path)
    try:
        spec = ADAPTER.parse_and_build(
            text,
            umbrella_name=args.umbrella_name,
            category_hint=args.category_hint,
            source_label=args.source_label,
            edition=args.edition,
        )
    except ValueError as exc:
        raise E.InputError("Catalog source could not be unified.", {"error": str(exc), "input": path.name}, "Supply at least one category or Skill Tool Name and Description.")
    result = {"source": path.name, "unified_spec": spec}
    emit(ctx, "catalog unify", None, result, args, started)


# ---------- single operation, batch, pipeline, recovery ----------

def cmd_run(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    domain = DomainOperations(ctx)
    params = E.parse_json_arg(args.params, "params")
    request: dict[str, Any] = {
        "operation": args.operation,
        "input": args.input,
        "output": args.output,
        "params": params,
        "run_id": ctx.run_id,
    }
    if args.timeout is not None:
        request["timeout_seconds"] = args.timeout
    if args.retries is not None:
        request["retries"] = args.retries
    if args.cancel_file:
        request["cancel_file"] = args.cancel_file
    if args.approval_token:
        request["approval_token"] = args.approval_token
    payload = execute_request(ctx, request, domain, started, "run", write_output=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_batch(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    domain = DomainOperations(ctx)
    input_path = ctx.user_path(args.input, must_exist=True)
    text = E.read_text_file(ctx, input_path)
    out_path = ctx.user_path(args.output, for_output=True) if args.output else ctx.workspace_file(f"batch-results-{ctx.run_id}.jsonl")
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        ctx.check_cancel()
        try:
            request = json.loads(line)
            payload = execute_request(ctx, request, domain, started, "batch", write_output=bool(request.get("output")))
            results.append({"line": line_no, "ok": True, "operation": payload.get("operation"), "result_path": payload.get("output_path"), "result": payload["result"]})
            succeeded += 1
        except E.CancelledError:
            raise
        except (E.ToolError, json.JSONDecodeError, OSError, ValueError) as exc:
            if isinstance(exc, E.ToolError):
                error = exc.to_dict("batch", ctx.run_id)["error"]
            else:
                error = {"code": "BATCH_REQUEST_ERROR", "message": str(exc), "recovery": "Fix the JSONL line and retry."}
            results.append({"line": line_no, "ok": False, "error": error})
            failed += 1
            if args.fail_fast:
                raise E.ToolError(error["code"], error["message"], {"line": line_no}, error.get("recovery", "Fix the request and retry."))
    E.write_text(ctx, out_path, "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) for r in results) + "\n")
    payload = E.structured_result(
        command="batch",
        operation=None,
        run_id=ctx.run_id,
        result={"line_count": len(results), "succeeded": succeeded, "failed": failed, "results_path": display_path(out_path), "results": results},
        output_path=display_path(out_path),
        config_digest=ctx.config_digest,
        elapsed_seconds=time.monotonic() - started,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def run_pipeline(
    ctx: Any,
    domain: Any,
    input_path: str,
    query: str,
    user_params: dict[str, Any],
    output_path: str | None,
    cancel_after: int,
    cancel_file: str | None,
    checkpoint: dict[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    store = E.CheckpointStore(ctx)
    existing = checkpoint or store.load()
    if existing and not resume:
        raise E.InputError("A checkpoint already exists for this run id.", {"run_id": ctx.run_id, "status": existing.get("status")}, "Use recover --resume, or choose a new --run-id.")
    if resume and existing is None:
        raise E.InputError("No checkpoint exists for this run id.", {"run_id": ctx.run_id}, "Run pipeline first, or verify --run-id.")
    base_params: dict[str, Any] = {
        "query": query,
        "limit": 5,
        "text": "Offline research requires a pinned corpus snapshot and a checksum manifest.",
        "source_language": "en",
        "target_language": "es",
        "claim": "The corrected 2025 survey covered 14 transects in Las Piñas.",
        "draft": "Offline research requires a checksum manifest [src-001].",
        "previous_draft": "Offline research requires a corpus snapshot.",
        "quote": "Offline research requires a pinned corpus snapshot and a checksum manifest.",
        "source_id": "src-001",
        "genre": "technical",
        "package_path": str(ctx.workspace / f"handoff-{ctx.run_id}.zip"),
        "actions": list(ctx.config["policies"]["require_human_review_for"]),
    }
    base_params.update(user_params)
    if existing:
        state = dict(existing)
        completed = list(existing.get("completed", []))
        start_index = int(existing.get("stage_index", 0))
        if existing.get("status") == "completed":
            return {"status": "already_completed", "stages_completed": len(completed), "checkpoint": display_path(store.path)}
    else:
        completed = []
        start_index = 0
        state = {
            "schema_version": "1.0",
            "run_id": ctx.run_id,
            "status": "running",
            "stage_index": 0,
            "stages": PIPELINE_ORDER,
            "pipeline_request": {"input_path": input_path, "query": query, "params": base_params, "output": output_path, "cancel_after": cancel_after, "cancel_file": cancel_file},
            "completed": completed,
            "last_error": None,
        }
        store.save(state)
    cancel_path = ctx.cancel_file or (ctx.workspace / "CANCEL")
    created_cancel_file = False
    stage_outputs: dict[str, dict[str, Any]] = {}
    try:
        for index in range(start_index, len(PIPELINE_ORDER)):
            ctx.check_cancel()
            ctx.check_limits()
            operation = PIPELINE_ORDER[index]
            stage_params = dict(base_params)
            if operation in ("citations", "editing", "originality", "fact-check") and "composition" in stage_outputs:
                stage_params["draft"] = stage_outputs["composition"]["draft"]
            if operation == "localization-qa" and "translation" in stage_outputs:
                units = stage_outputs["translation"].get("units", [])
                if units:
                    stage_params["translation"] = units[0]["target_draft"]
            result = domain.execute(operation, stage_params, input_path, None, capability=None, approval_token=None)
            stage_outputs[operation] = result
            digest = E.sha256_text(E.canonical_json(result))
            completed.append({"stage": index + 1, "operation": operation, "result_sha256": digest, "artifact_count": len(result.get("artifacts", []))})
            state.update({"status": "running", "stage_index": index + 1, "completed": completed, "last_error": None})
            store.save(state)
            if cancel_after and index + 1 == cancel_after:
                cancel_path.parent.mkdir(parents=True, exist_ok=True)
                E.write_text(ctx, cancel_path, "cancel requested for bounded cancellation test\n")
                created_cancel_file = True
        state.update({"status": "completed", "stage_index": len(PIPELINE_ORDER), "completed": completed, "last_error": None})
        store.save(state)
        if created_cancel_file and cancel_path.is_file():
            cancel_path.unlink()
        return {
            "status": "completed",
            "run_id": ctx.run_id,
            "stages_completed": len(completed),
            "started_at_stage": start_index + 1,
            "results_digest": E.sha256_text(E.canonical_json([c["result_sha256"] for c in completed])),
            "checkpoint": display_path(store.path),
            "artifacts": [a for c in completed for a in [f"stage-{c['stage']:02d}-{c['operation']}"]],
            "warnings": E.provider_warnings(ctx, "governance"),
        }
    except E.CancelledError as exc:
        state.update({"status": "cancelled", "last_error": {"code": exc.code, "message": exc.message}})
        store.save(state)
        raise
    except E.ToolError as exc:
        state.update({"status": "failed", "last_error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        store.save(state)
        raise


def cmd_pipeline(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    domain = DomainOperations(ctx)
    params = E.parse_json_arg(args.params, "params")
    result = run_pipeline(ctx, domain, args.input, args.query, params, args.output, int(args.cancel_after or 0), args.cancel_file)
    emit(ctx, "pipeline", None, result, args, started)


def cmd_recover(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    store = E.CheckpointStore(ctx)
    checkpoint = store.load()
    if checkpoint is None:
        raise E.InputError("No checkpoint exists for this run id.", {"run_id": ctx.run_id}, "Run pipeline with this --run-id first.")
    if not args.resume:
        emit(ctx, "recover", None, {"status": checkpoint.get("status"), "checkpoint": checkpoint}, args, started)
        return
    request = checkpoint.get("pipeline_request", {})
    cancel_file = args.cancel_file or request.get("cancel_file")
    if cancel_file:
        ctx.cancel_file = Path(cancel_file).resolve()
    if args.clear_cancel:
        candidates = [ctx.workspace / "CANCEL"]
        if ctx.cancel_file:
            candidates.append(ctx.cancel_file)
        for candidate in candidates:
            if candidate.is_file():
                candidate.unlink()
    input_path = args.input or request.get("input_path")
    if not input_path:
        raise E.InputError("Recovery has no stored input path and no --input was supplied.", {"run_id": ctx.run_id})
    query = args.query or request.get("query") or "offline research"
    params = dict(request.get("params", {}))
    params.update(E.parse_json_arg(args.params, "params"))
    domain = DomainOperations(ctx)
    result = run_pipeline(ctx, domain, input_path, query, params, args.output, 0, cancel_file, checkpoint=checkpoint, resume=True)
    result["recovery"] = {"resumed_from_stage": int(checkpoint.get("stage_index", 0)) + 1, "clear_cancel": bool(args.clear_cancel)}
    emit(ctx, "recover", None, result, args, started)


# ---------- approvals, sensitive actions, package audit ----------

def cmd_review_request(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    record = E.ApprovalStore(ctx).request(args.action, args.ticket, args.notes or "")
    emit(ctx, "review request", None, {"record": record}, args, started)


def cmd_review_approve(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    record = E.ApprovalStore(ctx).approve(args.ticket, args.approver, args.confirm)
    emit(ctx, "review approve", None, {"record": record}, args, started)


def cmd_review_reject(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    record = E.ApprovalStore(ctx).reject(args.ticket, args.approver, args.reason)
    emit(ctx, "review reject", None, {"record": record}, args, started)


def cmd_review_list(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    records = E.ApprovalStore(ctx).list()
    emit(ctx, "review list", None, {"record_count": len(records), "records": records}, args, started)


def cmd_export(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    domain = DomainOperations(ctx)
    package = args.package or str(ctx.workspace / f"export-{ctx.run_id}.zip")
    params = {"action": "export", "package_path": package}
    result = domain.execute("packaging", params, args.input, args.output, approval_token=args.approval_token)
    result["network_export_performed"] = False
    emit(ctx, "export", "packaging", result, args, started)


def cmd_publish(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    approval = E.require_approval(ctx, "publish", args.approval_token)
    input_path = ctx.user_path(args.input, must_exist=True)
    raw = E.read_text_file(ctx, input_path)
    record = {
        "schema_version": "1.0",
        "status": "local-publication-record-only",
        "input": input_path.name,
        "input_sha256": E.sha256_text(raw),
        "approval": approval,
        "published_externally": False,
        "human_review_gate": "approved",
        "recorded_at_utc": E.utc_now(),
    }
    artifact = ctx.workspace_file(f"publication-{ctx.run_id}.json")
    E.write_json(ctx, artifact, record)
    emit(ctx, "publish", None, {"record": record, "artifact": artifact.name}, args, started)


def cmd_purge(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    approval = E.require_approval(ctx, args.action, args.approval_token)
    expected = ctx.config["approval"]["delete_confirmation_text"]
    if args.confirm != expected:
        raise E.InputError(f"Destructive confirmation must be exactly {expected}.", {"received": args.confirm})
    target = Path(args.target)
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()
    if any(part == ".." for part in Path(args.target).parts):
        raise E.InputError("Parent-directory traversal is not accepted in a purge target.", {"target": args.target})
    try:
        inside_workspace = target.is_relative_to(ctx.workspace)
    except AttributeError:  # Python 3.8 fallback; supported minimum is 3.9.
        inside_workspace = str(target).startswith(str(ctx.workspace) + os.sep)
    if not inside_workspace or target.is_dir() or not target.is_file():
        raise E.InputError("Purge target must be one existing file inside the workspace.", {"target": args.target})
    size = target.stat().st_size
    target.unlink()
    emit(ctx, "purge", None, {"action": args.action, "approval": approval, "deleted_file": target.name, "bytes_removed": size, "confirmation": args.confirm}, args, started)


def cmd_validate_package(args: argparse.Namespace) -> None:
    started = time.monotonic()
    ctx, _, _ = build_context(args)
    folder = Path(args.path) if args.path else TOOL_DIR
    report: dict[str, Any] = {}
    try:
        report["folder_audit"] = AUDIT.audit_folder(folder)
        if args.zip:
            report["zip_audit"] = AUDIT.audit_zip(args.zip, expected_name=folder.name)
        if args.rerun:
            if not args.zip or not args.extract_to:
                raise E.InputError("validate-package --rerun requires --zip and --extract-to.", {}, "Supply both paths and rerun.")
            report["extract_and_rerun"] = AUDIT.extract_and_rerun(args.zip, args.extract_to, force=args.force)
        passed = all(section.get("passed", True) for section in report.values())
        report["passed"] = passed
        if args.strict and not passed:
            raise E.ToolError("PACKAGE_AUDIT_FAILED", "One or more package checks failed.", report, "Fix the reported files, rebuild the ZIP, and rerun validate-package.")
    except Exception as exc:
        if isinstance(exc, E.ToolError):
            raise
        raise E.ToolError("PACKAGE_AUDIT_FAILED", "Package audit could not complete.", {"error": str(exc), "type": type(exc).__name__}, "Check the folder/ZIP paths and rerun.")
    emit(ctx, "validate-package", None, report, args, started)


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", help="Structured JSON output path.")
    common.add_argument("--workspace", help="Writable runtime workspace outside the Skill Tool folder.")
    common.add_argument("--config", help="JSON configuration override path.")
    common.add_argument("--catalog", help="Capability catalog JSON override path.")
    common.add_argument("--timeout", type=float, help="Cooperative and SIGALRM timeout in seconds.")
    common.add_argument("--retries", type=int, help="Bounded retries for transient output I/O.")
    common.add_argument("--cancel-file", help="File whose presence cancels a batch or pipeline between stages.")
    common.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing output file.")
    common.add_argument("--keep-temp", action="store_true", help="Keep temporary write files for debugging.")
    common.add_argument("--run-id", help="Stable run id for checkpoints and outputs.")
    common.add_argument("--format", choices=["json", "table"], default="json", help="Output format for list commands.")

    parser = argparse.ArgumentParser(prog="tools-offline-research.py", description="Unified offline research, writing, and translation Skill Tool.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("help", help="Show built-in help.")
    p.set_defaults(func=cmd_help)

    p = sub.add_parser("version", parents=[common], help="Show tool, runtime, and catalog versions.")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("operations", parents=[common], help="List category-level operations.")
    p.set_defaults(func=cmd_operations)

    p = sub.add_parser("capabilities", parents=[common], help="List or verify exact catalog capabilities.")
    p.add_argument("--category", help="Filter by exact or partial category name.")
    p.add_argument("--operation", help="Filter by exact operation name.")
    p.add_argument("--limit", type=int, default=1000, help="Maximum capabilities to return.")
    p.add_argument("--offset", type=int, default=0, help="Capability offset.")
    p.add_argument("--verify", action="store_true", help="Validate all exact names, declarations, and handlers.")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("capability", parents=[common], help="Show or execute one exact catalog capability.")
    p.add_argument("--name", required=True, help="Exact Skill Tool name or slug.")
    p.add_argument("--input", help="Local corpus input path.")
    p.add_argument("--params", help="Inline JSON or @file.json operation parameters.")
    p.add_argument("--plan-only", action="store_true", help="Emit a declaration without executing.")
    p.set_defaults(func=cmd_capability)

    p = sub.add_parser("run-capability", parents=[common], help="Dispatch one exact catalog capability to its shared handler.")
    p.add_argument("--name", required=True, help="Exact Skill Tool name or slug.")
    p.add_argument("--input", help="Local corpus input path; required unless --plan-only.")
    p.add_argument("--params", help="Inline JSON or @file.json operation parameters.")
    p.add_argument("--plan-only", action="store_true", help="Emit a validated declaration without executing.")
    p.add_argument("--approval-token", help="Human approval token for a sensitive operation.")
    p.set_defaults(func=cmd_run_capability)

    catalog = sub.add_parser("catalog", help="Parse or unify plain-text, Markdown, or copied-HTML catalogs.")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    p = catalog_sub.add_parser("parse", parents=[common], help="Normalize a catalog and extract categories, names, and descriptions.")
    p.add_argument("--input", required=True, help="Plain text, Markdown, or copied HTML catalog source.")
    p.add_argument("--umbrella-name", help="Override the derived lowercase hyphenated umbrella name.")
    p.add_argument("--category-hint", help="Category name to use when a single-capability source has no explicit category.")
    p.set_defaults(func=cmd_catalog_parse)
    p = catalog_sub.add_parser("unify", parents=[common], help="Build one unified capability specification from a catalog source.")
    p.add_argument("--input", required=True, help="Plain text, Markdown, or copied HTML catalog source.")
    p.add_argument("--umbrella-name", help="Override the derived lowercase hyphenated umbrella name.")
    p.add_argument("--category-hint", help="Category name to use when a single-capability source has no explicit category.")
    p.add_argument("--source-label", default="supplied catalog", help="Label stored in the unified specification.")
    p.add_argument("--edition", help="Optional catalog edition label.")
    p.set_defaults(func=cmd_catalog_unify)

    p = sub.add_parser("run", parents=[common], help="Run one category-level operation.")
    p.add_argument("--operation", required=True, help="Operation name from the operations command.")
    p.add_argument("--input", required=True, help="Local corpus or sidecar input path.")
    p.add_argument("--params", help="Inline JSON or @file.json operation parameters.")
    p.add_argument("--approval-token", help="Human approval token for a sensitive operation.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("batch", parents=[common], help="Run bounded JSONL operation requests.")
    p.add_argument("--input", required=True, help="JSONL request file.")
    p.add_argument("--fail-fast", action="store_true", help="Stop at the first structured error.")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("pipeline", parents=[common], help="Run all 32 category operations with checkpoints.")
    p.add_argument("--input", required=True, help="Local corpus input path.")
    p.add_argument("--query", default="offline research", help="Research query used by retrieval, QA, and writing stages.")
    p.add_argument("--params", help="Inline JSON or @file.json parameter overrides.")
    p.add_argument("--cancel-after", type=int, default=0, help="Write a cancel marker after this many stages for recovery testing.")
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("recover", parents=[common], help="Inspect or resume a pipeline checkpoint.")
    p.add_argument("--input", help="Override the stored pipeline input path.")
    p.add_argument("--query", help="Override the stored pipeline query.")
    p.add_argument("--params", help="Inline JSON or @file.json parameter overrides.")
    p.add_argument("--resume", action="store_true", help="Resume from the last completed stage.")
    p.add_argument("--clear-cancel", action="store_true", help="Remove the cancel marker before resuming.")
    p.set_defaults(func=cmd_recover)

    review = sub.add_parser("review", help="Human review request and approval commands.")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    p = review_sub.add_parser("request", parents=[common], help="Create a pending human-review request.")
    p.add_argument("--action", required=True, help="Action requiring approval.")
    p.add_argument("--ticket", required=True, help="Unique review ticket.")
    p.add_argument("--notes", help="Review notes.")
    p.set_defaults(func=cmd_review_request)
    p = review_sub.add_parser("approve", parents=[common], help="Explicitly approve a pending ticket and issue a token.")
    p.add_argument("--ticket", required=True, help="Pending review ticket.")
    p.add_argument("--approver", required=True, help="Human approver name.")
    p.add_argument("--confirm", required=True, help="Exact confirmation text from asset-default-config.json.")
    p.set_defaults(func=cmd_review_approve)
    p = review_sub.add_parser("reject", parents=[common], help="Reject a pending ticket.")
    p.add_argument("--ticket", required=True, help="Pending review ticket.")
    p.add_argument("--approver", required=True, help="Human reviewer name.")
    p.add_argument("--reason", required=True, help="Rejection reason.")
    p.set_defaults(func=cmd_review_reject)
    p = review_sub.add_parser("list", parents=[common], help="List sanitized review records.")
    p.set_defaults(func=cmd_review_list)

    p = sub.add_parser("export", parents=[common], help="Create a token-gated local export ZIP; no network publishing.")
    p.add_argument("--input", required=True, help="Local corpus input path.")
    p.add_argument("--package", help="Output ZIP path for the local handoff.")
    p.add_argument("--approval-token", required=True, help="Human approval token for action export.")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("publish", parents=[common], help="Write a local, token-gated publication record only.")
    p.add_argument("--input", required=True, help="Local input file to hash into the publication record.")
    p.add_argument("--approval-token", required=True, help="Human approval token for action publish.")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("purge", parents=[common], help="Delete one workspace file after explicit approval and confirmation.")
    p.add_argument("--target", required=True, help="Existing file inside the runtime workspace.")
    p.add_argument("--action", choices=["purge", "secure-dispose"], default="purge", help="Sensitive action name.")
    p.add_argument("--approval-token", required=True, help="Human approval token for the action.")
    p.add_argument("--confirm", required=True, help="Exact delete confirmation text from asset-default-config.json.")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("validate-package", parents=[common], help="Audit folder/ZIP compatibility and rerun from a clean root.")
    p.add_argument("--path", help="Skill Tool folder path; defaults to this folder.")
    p.add_argument("--zip", help="Delivery ZIP path to inspect.")
    p.add_argument("--extract-to", help="Clean temporary skill root for extraction and rerun.")
    p.add_argument("--rerun", action="store_true", help="Extract the ZIP and rerun help/capabilities using only relative paths.")
    p.add_argument("--force", action="store_true", help="Allow a non-empty extraction root.")
    p.add_argument("--strict", action="store_true", help="Return a non-zero exit code if any audit fails.")
    p.set_defaults(func=cmd_validate_package)

    return parser


def main(argv: list[str] | None = None) -> int:
    global PARSER
    PARSER = build_parser()
    args = PARSER.parse_args(argv)
    if not hasattr(args, "func"):
        PARSER.print_help()
        return 0
    try:
        args.func(args)
        return 0
    except E.ToolError as exc:
        error = exc.to_dict(getattr(args, "command", None), make_run_id(getattr(args, "run_id", None)))
        print(json.dumps(error, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        exc = E.CancelledError("Operation interrupted by the operator.")
        print(json.dumps(exc.to_dict(getattr(args, "command", None), make_run_id(getattr(args, "run_id", None))), ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        error = {
            "schema_version": "1.0",
            "tool": "offline-research-writing-translation",
            "ok": False,
            "command": getattr(args, "command", None),
            "error": {"code": "INTERNAL_ERROR", "message": str(exc), "type": type(exc).__name__, "recovery": "Check the exact command and input; rerun tools-selftest.py for a bounded regression."},
        }
        print(json.dumps(error, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return 70
    finally:
        ctx = ACTIVE_CTX.get("ctx")
        if ctx is not None:
            ctx.cleanup()
        ACTIVE_CTX["ctx"] = None


if __name__ == "__main__":
    raise SystemExit(main())
