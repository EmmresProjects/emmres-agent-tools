#!/usr/bin/env python3
"""Primary CLI entry point for the offline-agents-automation unified Skill Tool (32 categories, 450 capabilities)."""
import sys
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_TOOL_DIR = Path(__file__).resolve().parent


def _load_local(filename: str, alias: str) -> Any:
    target = SKILL_TOOL_DIR / filename
    spec = importlib.util.spec_from_file_location(alias, str(target))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_local("tools-core-foundation.py", "oaa_cli_core")
registry = _load_local("tools-catalog-registry.py", "oaa_cli_registry")

_ENGINE_FILES = {
    "platform-models": "tools-engine-platform-models.py",
    "runtime-security": "tools-engine-runtime-security.py",
    "knowledge-workflows": "tools-engine-knowledge-workflows.py",
    "quality-governance": "tools-engine-quality-governance.py",
}


def _dispatch_engine(engine_name: str) -> Any:
    fname = _ENGINE_FILES.get(engine_name)
    if not fname:
        raise ValueError(f"Unknown engine '{engine_name}'")
    return core.load_tool_module(fname)


def _emit_output(payload: Dict[str, Any], fmt: str = "pretty", output_path: Optional[str] = None) -> None:
    if fmt == "json":
        rendered = json.dumps(payload, sort_keys=False, ensure_ascii=False)
    elif fmt == "summary":
        status = "OK" if payload.get("ok", True) else "ERROR"
        cap = payload.get("capability") or payload.get("command", "cli")
        rendered = f"[{status}] {cap} | trace={payload.get('trace_id', 'n/a')}\n" + json.dumps(payload.get("result", payload), indent=2, ensure_ascii=False)
    else:
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)

    if output_path:
        out_file = core.validate_safe_user_path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = out_file.parent / f".tmp-{uuid.uuid4().hex[:8]}-{out_file.name}"
        tmp_file.write_text(rendered + "\n", encoding="utf-8")
        os.replace(tmp_file, out_file)

    sys.stdout.write(rendered + "\n")


def _load_merged_params(params_json: Optional[str], input_path: Optional[str], max_bytes: int) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if input_path:
        in_file = core.validate_safe_user_path(input_path)
        if not in_file.is_file():
            raise FileNotFoundError(f"Input file does not exist: {input_path}")
        size = in_file.stat().st_size
        if size > max_bytes:
            raise ValueError(f"Input file exceeds max_input_bytes ({size} > {max_bytes}).")
        raw = in_file.read_text(encoding="utf-8")
        if in_file.suffix.lower() == ".json":
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                merged.update(parsed)
            else:
                merged["items"] = parsed
        else:
            merged["content"] = raw
            merged["text"] = raw

    if params_json:
        if len(params_json.encode("utf-8")) > max_bytes:
            raise ValueError("Inline --params exceeds max_input_bytes.")
        parsed_inline = json.loads(params_json)
        if not isinstance(parsed_inline, dict):
            raise ValueError("--params must decode to a JSON object (dictionary).")
        merged.update(parsed_inline)
    return merged


def run_single_capability(
    identifier: str,
    params: Dict[str, Any],
    store: Any,
    bundle: Dict[str, Any],
    principal: str = "operator-local",
    role: str = "operator",
    approval_id: Optional[str] = None,
    dry_run: bool = False,
    idempotency_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
    retries: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    trace = trace_id or ("trc-" + uuid.uuid4().hex[:12])
    cap_meta = registry.resolve_capability(identifier)
    if not cap_meta:
        err_env = {
            "ok": False,
            "error_type": "UnknownCapabilityError",
            "message": f"Capability '{identifier}' not found. Run 'capabilities' to list all 450 capabilities.",
            "trace_id": trace,
            "timestamp_utc": core.utc_now_iso(),
        }
        return 2, err_env

    slug = cap_meta["slug"]
    payload_sha = core.sha256_hex({"capability": slug, "params": params})

    if idempotency_key and not dry_run:
        with store.connect() as conn:
            idem_row = conn.execute("SELECT * FROM idempotency_ledger WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if idem_row:
            cached = json.loads(idem_row["response_json"])
            cached["idempotent_replay"] = True
            return 0, cached

    gate = core.check_rbac_and_approval(
        store=store,
        capability_meta=cap_meta,
        params=params,
        principal=principal,
        principal_role=role,
        approval_id=approval_id,
        dry_run=dry_run,
    )
    if not gate["authorized"]:
        audit = store.append_audit_event(
            trace_id=trace,
            principal=principal,
            capability=slug,
            category_id=cap_meta["category_id"],
            decision="paused-for-approval",
            summary=f"Human approval required for {slug} ({cap_meta['risk_class']})",
            details={"pending_approval": gate["pending_approval"]},
        )
        paused_env = {
            "ok": False,
            "status": "approval-required",
            "capability": slug,
            "category": cap_meta["category_title"],
            "risk_class": cap_meta["risk_class"],
            "trace_id": trace,
            "timestamp_utc": core.utc_now_iso(),
            "pending_approval": gate["pending_approval"],
            "provenance": {
                "input_sha256": payload_sha,
                "policy_version": bundle["security_policy"]["policy_version"],
                "audit_event_hash": audit["event_hash"],
            },
        }
        return 3, paused_env

    if dry_run:
        audit = store.append_audit_event(
            trace_id=trace,
            principal=principal,
            capability=slug,
            category_id=cap_meta["category_id"],
            decision="dry-run-validated",
            summary=f"Dry-run validated for {slug}",
            details={"payload_sha256": payload_sha},
        )
        dry_env = {
            "ok": True,
            "capability": slug,
            "category": cap_meta["category_title"],
            "trace_id": trace,
            "timestamp_utc": core.utc_now_iso(),
            "result": {
                "dry_run": True,
                "would_execute": slug,
                "risk_class": cap_meta["risk_class"],
                "policy_authorized": True,
            },
            "provenance": {
                "input_sha256": payload_sha,
                "policy_version": bundle["security_policy"]["policy_version"],
                "audit_event_hash": audit["event_hash"],
            },
        }
        return 0, dry_env

    engine_mod = _dispatch_engine(cap_meta["engine"])
    exec_wrapped = core.execute_with_retries_and_limits(
        store=store,
        target_id=slug,
        fn=lambda: engine_mod.execute_capability(slug, cap_meta, params, store, bundle),
        max_retries=retries,
        timeout_seconds=timeout_s or cap_meta["default_timeout_seconds"],
    )
    audit = store.append_audit_event(
        trace_id=trace,
        principal=principal,
        capability=slug,
        category_id=cap_meta["category_id"],
        decision="completed",
        summary=f"Executed {slug} in {exec_wrapped['attempts']} attempt(s)",
        details={"payload_sha256": payload_sha, "elapsed_seconds": exec_wrapped["elapsed_seconds"]},
    )

    envelope = {
        "ok": True,
        "capability": slug,
        "category": cap_meta["category_title"],
        "trace_id": trace,
        "timestamp_utc": core.utc_now_iso(),
        "result": exec_wrapped["output"],
        "execution_metrics": {
            "attempts": exec_wrapped["attempts"],
            "elapsed_seconds": exec_wrapped["elapsed_seconds"],
            "risk_class": cap_meta["risk_class"],
        },
        "provenance": {
            "input_sha256": payload_sha,
            "policy_version": bundle["security_policy"]["policy_version"],
            "audit_event_hash": audit["event_hash"],
        },
    }

    schema_errs = core.validate_json_schema(envelope, bundle["schemas"]["schemas"]["ExecutionEnvelope"])
    if schema_errs:
        raise RuntimeError(f"Internal output contract violation: {schema_errs}")

    if idempotency_key:
        with store.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO idempotency_ledger VALUES (?, ?, ?, 'committed', ?, ?)",
                (idempotency_key, slug, payload_sha, core.canonical_json(envelope), core.utc_now_iso()),
            )

    return 0, envelope


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="Path to JSON runtime config (default: offline-agents-automation/asset-default-config.json)")
    common.add_argument("--state-dir", default=None, help="Path to durable offline SQLite & artifact state directory")
    common.add_argument("--input", default=None, help="Input file path (JSON, Markdown, HTML, CSV, EML, or plain text)")
    common.add_argument("--output", default=None, help="Output file path for structured JSON result")
    common.add_argument("--principal", default="operator-local", help="Authenticated caller identity")
    common.add_argument("--role", default="operator", help="Caller RBAC role: viewer, operator, approver, security-reviewer, admin")
    common.add_argument("--format", choices=["pretty", "json", "summary"], default="pretty", help="Output formatting mode")

    parser = argparse.ArgumentParser(
        prog="tools-offline-agent-cli.py",
        description="Unified Offline Agents and Automation Skill Tool (32 categories, 450 production capabilities).",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("help", parents=[common], help="Show detailed help, architecture, and command examples")

    p_cap = sub.add_parser("capabilities", aliases=["list-capabilities"], parents=[common], help="List categories and capabilities")
    p_cap.add_argument("--category", default=None, help="Filter by category number (1-32), slug, or title substring")
    p_cap.add_argument("--search", default=None, help="Search capability names and descriptions")
    p_cap.add_argument("--risk", default=None, choices=["read-only", "reversible", "approval-required", "dual-control"], help="Filter by risk class")
    p_cap.add_argument("--categories-only", action="store_true", help="List only the 32 umbrella categories and counts")

    p_ins = sub.add_parser("inspect", parents=[common], help="Inspect metadata and contract for one capability")
    p_ins.add_argument("capability", help="Capability slug, exact name, '<cat>.<skill>', or global index (1-450)")

    p_parse = sub.add_parser("parse-catalog", parents=[common], help="Parse plain text, Markdown, or copied HTML capability specifications")
    p_parse.add_argument("--text", default=None, help="Inline plain text, Markdown, or HTML catalog snippet")

    p_run = sub.add_parser("run", parents=[common], help="Execute any of the 450 capabilities with validation, policy, and audit")
    p_run.add_argument("capability", help="Capability slug, exact name, '<cat>.<skill>', or global index (1-450)")
    p_run.add_argument("--params", default=None, help="JSON object string of capability parameters")
    p_run.add_argument("--approval-id", default=None, help="Approved human/dual-control approval ID")
    p_run.add_argument("--dry-run", action="store_true", help="Validate inputs and policy without mutating state")
    p_run.add_argument("--idempotency-key", default=None, help="Idempotency key to prevent duplicate execution")
    p_run.add_argument("--timeout", type=float, default=None, help="Hard timeout in seconds")
    p_run.add_argument("--retries", type=int, default=None, help="Maximum retry attempts on transient errors")

    p_apr = sub.add_parser("approve", parents=[common], help="Manage single and dual-control human approvals")
    p_apr.add_argument("action", choices=["request", "decide", "list"], help="Approval operation")
    p_apr.add_argument("--capability", default=None, help="Target capability slug (for 'request')")
    p_apr.add_argument("--params", default=None, help="JSON parameters bound to approval hash (for 'request')")
    p_apr.add_argument("--approval-id", default=None, help="Approval ID (for 'decide')")
    p_apr.add_argument("--reviewer", default="reviewer-1", help="Reviewer principal (must differ from requester)")
    p_apr.add_argument("--reviewer-role", default="approver", help="Reviewer role (approver, security-reviewer, admin)")
    p_apr.add_argument("--decision", choices=["approved", "denied", "revoked"], default="approved", help="Review decision")
    p_apr.add_argument("--rationale", default="Verified offline safety and rollback plan.", help="Decision rationale")

    p_wf = sub.add_parser("workflow", parents=[common], help="Execute, pause, resume, or cancel durable DAG workflows")
    p_wf.add_argument("--preset", default="airgap-grounded-rag-pipeline", help="Workflow preset ID from asset-workflow-presets.json")
    p_wf.add_argument("--params", default=None, help="Shared workflow parameters JSON")
    p_wf.add_argument("--run-id", default=None, help="Durable workflow run ID (required for --resume or --cancel)")
    p_wf.add_argument("--resume", action="store_true", help="Resume workflow from last verified checkpoint")
    p_wf.add_argument("--cancel", action="store_true", help="Cancel running/paused workflow and optionally compensate")
    p_wf.add_argument("--compensate", action="store_true", help="Run reverse saga compensations when cancelling")
    p_wf.add_argument("--approval-id", default=None, help="Approval ID for gated workflow steps")

    sub.add_parser("selftest", parents=[common], help="Run comprehensive end-to-end offline self-test suite")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand or args.subcommand == "help":
        help_payload = {
            "ok": True,
            "tool_name": registry.UMBRELLA_NAME,
            "title": registry.UMBRELLA_TITLE,
            "categories": len(registry.CATEGORIES),
            "capabilities": len(registry.CAPABILITIES),
            "primary_entry_point": "offline-agents-automation/tools-offline-agent-cli.py",
            "commands": [
                "help",
                "capabilities [--category ID_OR_SLUG] [--search TEXT] [--risk CLASS] [--categories-only]",
                "inspect <capability>",
                "parse-catalog [--input FILE | --text STRING]",
                "run <capability> [--params JSON] [--input FILE] [--output FILE] [--approval-id ID] [--dry-run]",
                "approve <request|decide|list> ...",
                "workflow [--preset ID] [--run-id ID] [--resume] [--cancel] [--approval-id ID]",
                "selftest",
            ],
        }
        _emit_output(help_payload, fmt=getattr(args, "format", "pretty"), output_path=getattr(args, "output", None))
        return 0

    try:
        if getattr(args, "output", None):
            core.validate_safe_user_path(args.output)
        bundle = core.load_runtime_bundle(args.config)
        max_input_bytes = int(bundle["config"]["limits_and_recovery"]["max_input_bytes"])
        state_dir_str = args.state_dir or bundle["config"]["runtime"]["default_state_dir"]
        state_dir = core.validate_safe_user_path(state_dir_str)
        store = core.OfflineStateStore(state_dir, bundle["config"], bundle["security_policy"])

        if args.subcommand in ("capabilities", "list-capabilities"):
            if args.categories_only:
                res = {
                    "ok": True,
                    "umbrella": registry.UMBRELLA_NAME,
                    "category_count": len(registry.CATEGORIES),
                    "total_capabilities": len(registry.CAPABILITIES),
                    "categories": registry.CATEGORIES,
                }
            else:
                matched = registry.filter_capabilities(category=args.category, search=args.search, risk_class=args.risk)
                res = {
                    "ok": True,
                    "umbrella": registry.UMBRELLA_NAME,
                    "matched_count": len(matched),
                    "total_catalog_capabilities": len(registry.CAPABILITIES),
                    "capabilities": matched,
                }
            _emit_output(res, fmt=args.format, output_path=args.output)
            return 0

        if args.subcommand == "inspect":
            meta = registry.resolve_capability(args.capability)
            if not meta:
                _emit_output({"ok": False, "error_type": "UnknownCapabilityError", "message": f"Unknown capability: {args.capability}"}, fmt=args.format, output_path=args.output)
                return 2
            _emit_output({"ok": True, "capability_metadata": meta}, fmt=args.format, output_path=args.output)
            return 0

        if args.subcommand == "parse-catalog":
            raw_text = args.text or ""
            if args.input:
                in_p = core.validate_safe_user_path(args.input)
                if not in_p.is_file():
                    raise FileNotFoundError(f"Input file not found: {args.input}")
                raw_text = in_p.read_text(encoding="utf-8")
            if not raw_text.strip():
                raise ValueError("Provide non-empty catalog input via --input <file> or --text <string>.")
            parsed = core.parse_capability_catalog_text(raw_text)
            _emit_output({"ok": True, "command": "parse-catalog", "result": parsed}, fmt=args.format, output_path=args.output)
            return 0

        if args.subcommand == "approve":
            if args.action == "list":
                with store.connect() as conn:
                    rows = conn.execute("SELECT * FROM approvals ORDER BY created_utc DESC LIMIT 50").fetchall()
                items = [{**dict(r), "decisions": json.loads(r["decisions_json"])} for r in rows]
                _emit_output({"ok": True, "approvals": items}, fmt=args.format, output_path=args.output)
                return 0
            if args.action == "request":
                if not args.capability:
                    raise ValueError("--capability is required for 'approve request'.")
                cap_meta = registry.resolve_capability(args.capability)
                if not cap_meta:
                    raise KeyError(f"Unknown capability: {args.capability}")
                params = _load_merged_params(args.params, args.input, max_input_bytes)
                p_hash = core.sha256_hex({"capability": cap_meta["slug"], "params": params})
                req = store.create_approval_request(cap_meta["slug"], p_hash, cap_meta["risk_class"], args.principal)
                _emit_output({"ok": True, "approval_request": req}, fmt=args.format, output_path=args.output)
                return 0
            if args.action == "decide":
                if not args.approval_id:
                    raise ValueError("--approval-id is required for 'approve decide'.")
                dec = store.record_approval_decision(args.approval_id, args.reviewer, args.reviewer_role, args.decision, args.rationale)
                _emit_output({"ok": True, "approval_decision": dec}, fmt=args.format, output_path=args.output)
                return 0

        if args.subcommand == "run":
            params = _load_merged_params(args.params, args.input, max_input_bytes)
            code, env = run_single_capability(
                identifier=args.capability,
                params=params,
                store=store,
                bundle=bundle,
                principal=args.principal,
                role=args.role,
                approval_id=args.approval_id,
                dry_run=args.dry_run,
                idempotency_key=args.idempotency_key,
                timeout_s=args.timeout,
                retries=args.retries,
            )
            _emit_output(env, fmt=args.format, output_path=args.output)
            return code

        if args.subcommand == "workflow":
            kw_mod = _dispatch_engine("knowledge-workflows")
            shared_params = _load_merged_params(args.params, args.input if (args.input and not args.preset) else None, max_input_bytes)
            presets_map = {w["workflow_id"]: w for w in bundle["workflows"]["workflows"]}
            if args.input and Path(args.input).is_file():
                cand_wf = json.loads(Path(args.input).read_text(encoding="utf-8"))
                wf_def = cand_wf if "steps" in cand_wf else presets_map.get(args.preset)
            else:
                wf_def = presets_map.get(args.preset)
            if not wf_def:
                raise KeyError(f"Workflow preset '{args.preset}' not found.")

            errs = core.validate_json_schema(wf_def, bundle["schemas"]["schemas"]["WorkflowDefinition"])
            if errs:
                raise ValueError(f"Invalid workflow contract: {errs}")

            run_id = args.run_id or ("wfrun-" + uuid.uuid4().hex[:10])
            completed_steps: Dict[str, Any] = {}
            side_effects: List[Dict[str, Any]] = []

            if args.resume or args.cancel:
                with store.connect() as conn:
                    saved = conn.execute("SELECT * FROM agent_runs_and_checkpoints WHERE run_id=?", (run_id,)).fetchone()
                if not saved:
                    raise KeyError(f"Workflow run_id '{run_id}' not found in checkpoint store.")
                completed_steps = json.loads(saved["checkpoint_json"]).get("completed_steps", {})
                side_effects = json.loads(saved["side_effects_json"])

            if args.cancel:
                compensated = []
                if args.compensate:
                    step_by_id = {s["step_id"]: s for s in wf_def["steps"]}
                    for sid in reversed(list(completed_steps.keys())):
                        comp_cap = step_by_id.get(sid, {}).get("compensate_capability")
                        if comp_cap:
                            _, comp_env = run_single_capability(comp_cap, shared_params, store, bundle, principal=args.principal, role=args.role)
                            compensated.append({"step_id": sid, "compensate_capability": comp_cap, "ok": comp_env["ok"]})
                with store.connect() as conn:
                    conn.execute("UPDATE agent_runs_and_checkpoints SET status='cancelled', current_state='terminate', updated_utc=? WHERE run_id=?", (core.utc_now_iso(), run_id))
                _emit_output({"ok": True, "run_id": run_id, "status": "cancelled", "compensations": compensated}, fmt=args.format, output_path=args.output)
                return 0

            waves = kw_mod.topological_waves(wf_def["steps"])
            step_map = {s["step_id"]: s for s in wf_def["steps"]}
            trace = "trc-" + uuid.uuid4().hex[:12]

            for wave in waves:
                for sid in wave:
                    if sid in completed_steps:
                        continue
                    step = step_map[sid]
                    step_params = {**step.get("params", {}), **shared_params}
                    code, step_env = run_single_capability(
                        identifier=step["capability"],
                        params=step_params,
                        store=store,
                        bundle=bundle,
                        principal=args.principal,
                        role=args.role,
                        approval_id=args.approval_id if step.get("require_approval") else None,
                        trace_id=trace,
                    )
                    if code != 0:
                        with store.connect() as conn:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO agent_runs_and_checkpoints
                                (run_id, workflow_id, current_state, step_index, status, tenant_id, principal, plan_json, checkpoint_json, side_effects_json, updated_utc)
                                VALUES (?, ?, 'paused', ?, ?, 'default', ?, ?, ?, ?, ?)
                                """,
                                (
                                    run_id,
                                    wf_def["workflow_id"],
                                    len(completed_steps),
                                    step_env.get("status", "failed"),
                                    args.principal,
                                    core.canonical_json(wf_def["steps"]),
                                    core.canonical_json({"completed_steps": completed_steps, "paused_at": sid}),
                                    core.canonical_json(side_effects),
                                    core.utc_now_iso(),
                                ),
                            )
                        out_pause = {
                            "ok": False,
                            "run_id": run_id,
                            "workflow_id": wf_def["workflow_id"],
                            "status": step_env.get("status", "failed"),
                            "paused_at_step": sid,
                            "completed_step_ids": list(completed_steps.keys()),
                            "step_response": step_env,
                        }
                        _emit_output(out_pause, fmt=args.format, output_path=args.output)
                        return code

                    completed_steps[sid] = step_env["result"]
                    side_effects.append({"step_id": sid, "capability": step["capability"], "state": "confirmed"})
                    with store.connect() as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO agent_runs_and_checkpoints
                            (run_id, workflow_id, current_state, step_index, status, tenant_id, principal, plan_json, checkpoint_json, side_effects_json, updated_utc)
                            VALUES (?, ?, 'persist', ?, 'running', 'default', ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                wf_def["workflow_id"],
                                len(completed_steps),
                                args.principal,
                                core.canonical_json(wf_def["steps"]),
                                core.canonical_json({"completed_steps": completed_steps}),
                                core.canonical_json(side_effects),
                                core.utc_now_iso(),
                            ),
                        )

            with store.connect() as conn:
                conn.execute("UPDATE agent_runs_and_checkpoints SET current_state='terminate', status='completed', updated_utc=? WHERE run_id=?", (core.utc_now_iso(), run_id))
            wf_out = {
                "ok": True,
                "run_id": run_id,
                "workflow_id": wf_def["workflow_id"],
                "status": "completed",
                "waves": waves,
                "step_results": completed_steps,
            }
            _emit_output(wf_out, fmt=args.format, output_path=args.output)
            return 0

        if args.subcommand == "selftest":
            checks: Dict[str, bool] = {}
            _, c1 = run_single_capability("installation-integrity-verifier", {}, store, bundle, role="admin")
            checks["integrity_verifier"] = bool(c1["result"]["integrity_valid"])
            _, c2 = run_single_capability("rag-pipeline-architect", {"query": "What are the RPO and RTO targets?"}, store, bundle, role="operator")
            checks["grounded_rag"] = (not c2["result"]["abstained"]) and len(c2["result"]["citations"]) > 0
            _, c3 = run_single_capability("grounded-abstention-controller", {"query": "What is Mars orbital latency?"}, store, bundle, role="operator")
            checks["abstention_control"] = bool(c3["result"]["abstained"])
            _, c4 = run_single_capability("security-regression-suite", {}, store, bundle, role="security-reviewer")
            checks["security_regression"] = bool(c4["result"]["security_regression_passed"])
            _, c5 = run_single_capability("approval-regression-suite", {}, store, bundle, role="security-reviewer")
            checks["approval_sod_and_tamper_binding"] = bool(c5["result"]["regression_passed"])
            _, c6 = run_single_capability("catalog-coverage-auditor", {}, store, bundle, role="operator")
            checks["catalog_450_coverage"] = c6["result"]["coverage_ratio"] == 1.0
            chain = store.verify_audit_chain()
            checks["tamper_evident_audit_chain"] = bool(chain["chain_valid"])
            all_ok = all(checks.values())
            _emit_output({"ok": all_ok, "command": "selftest", "checks": checks, "audit_events_verified": chain["checked_events"]}, fmt=args.format, output_path=args.output)
            return 0 if all_ok else 1

    except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
        safe_out = None
        if getattr(args, "output", None):
            try:
                core.validate_safe_user_path(args.output)
                safe_out = args.output
            except ValueError:
                safe_out = None
        _emit_output(
            {"ok": False, "error_type": type(exc).__name__, "message": str(exc), "timestamp_utc": core.utc_now_iso()},
            fmt=getattr(args, "format", "pretty"),
            output_path=safe_out,
        )
        return 2
    except PermissionError as exc:
        _emit_output(
            {"ok": False, "error_type": "PermissionError", "message": str(exc), "timestamp_utc": core.utc_now_iso()},
            fmt=getattr(args, "format", "pretty"),
            output_path=getattr(args, "output", None),
        )
        return 3
    except Exception as exc:
        _emit_output(
            {"ok": False, "error_type": type(exc).__name__, "message": str(exc), "traceback_tail": traceback.format_exc().splitlines()[-3:], "timestamp_utc": core.utc_now_iso()},
            fmt=getattr(args, "format", "pretty"),
            output_path=getattr(args, "output", None),
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
