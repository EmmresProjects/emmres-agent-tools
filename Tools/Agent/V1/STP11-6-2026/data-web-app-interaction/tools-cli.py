#!/usr/bin/env python3
"""Primary runnable entry point for the data-web-app-interaction Skill Tool.

Unified CLI and programmatic dispatcher covering all 33 categories and 330
capabilities from the Data, Web, App, Game, File, and Interactive Systems
Skill Catalog with read-only defaults, pre-execution validation, approval
gatekeeping, secret redaction, bounded timeouts/retries, checkpoints, and
hash-chained action ledgers.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _bootstrap_core() -> Any:
    if "dwai_tools_core" in sys.modules:
        return sys.modules["dwai_tools_core"]
    import importlib.util

    for cand in (Path("data-web-app-interaction/tools-core.py"), Path(__file__).parent / "tools-core.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("dwai_tools_core", str(cand))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["dwai_tools_core"] = mod
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("Cannot locate data-web-app-interaction/tools-core.py from current working directory.")


core = _bootstrap_core()
catalog_mod = core.load_sibling_module("tools-catalog-parser.py")

DOMAIN_MODULES = [
    "tools-governance-search.py",
    "tools-web-api-infra.py",
    "tools-media-docs-files.py",
    "tools-data-science-sim.py",
    "tools-apps-games-qa.py",
    "tools-net-sec-ops.py",
]


def load_all_handlers() -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    dispatch: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
    for mod_name in DOMAIN_MODULES:
        mod = core.load_sibling_module(mod_name)
        handlers = getattr(mod, "SKILL_HANDLERS", {})
        for slug, fn in handlers.items():
            if slug in dispatch:
                raise core.SkillToolError(f"Duplicate handler registration for slug '{slug}' in {mod_name}")
            dispatch[slug] = fn
    return dispatch


def _write_output(payload: Any, output_path: Optional[str], fmt: str = "json", pretty: bool = True) -> None:
    if fmt == "json":
        rendered = json.dumps(payload, indent=2 if pretty else None, ensure_ascii=True) + "\n"
    elif fmt == "markdown" and isinstance(payload, dict):
        lines = [
            f"# Skill Result: {payload.get('skill_name', payload.get('command', 'Output'))}",
            f"- **Status**: `{payload.get('status', 'completed')}`",
            f"- **Skill ID**: `{payload.get('skill_id', 'n/a')}` (`{payload.get('slug', 'n/a')}`)",
            f"- **Category**: {payload.get('category_number', '')} {payload.get('category_name', '')}",
            f"- **Observation Mode**: `{payload.get('observation_mode', 'directly_observed')}`",
            "",
            "## Structured Data",
            "```json",
            json.dumps(payload.get("data", payload), indent=2, ensure_ascii=True),
            "```",
        ]
        rendered = "\n".join(lines) + "\n"
    else:
        rendered = str(payload) + ("\n" if not str(payload).endswith("\n") else "")

    if output_path:
        out_file = core.validate_safe_path(output_path, must_exist=False, allow_dir=False)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def execute_single_skill(
    skill_identifier: str,
    input_path_str: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    approve: bool = False,
    approval_note: str = "",
    dry_run: bool = False,
    timeout_override: Optional[float] = None,
    retries_override: Optional[int] = None,
    ledger_path_str: Optional[str] = None,
    checkpoint_path_str: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    """Validate, gatekeep, execute, redact, and wrap one capability invocation."""
    t_start = time.perf_counter()
    params = dict(params or {})
    cfg, used_cfg_path = core.load_runtime_config(config_path)
    policy = core.load_safety_policy(cfg)
    redactor = core.SecretRedactor(policy, redact_pii=False)

    registry = catalog_mod.SkillRegistry(cfg["paths"]["catalog_path"])
    skill_meta = registry.resolve(skill_identifier)

    max_bytes = int(cfg["execution"]["max_file_bytes"])
    resolved_input: Optional[Path] = None
    input_sha: Optional[str] = None
    if input_path_str:
        resolved_input = core.validate_safe_path(input_path_str, must_exist=True, allow_dir=True, max_bytes=max_bytes)
        if resolved_input.is_file():
            input_sha = core.sha256_file(resolved_input)

    # Check checkpoint/idempotency store before executing consequential actions
    chk_path = core.validate_safe_path(checkpoint_path_str, must_exist=False, allow_dir=False) if checkpoint_path_str else None
    checkpoint = core.CheckpointStore(chk_path)
    if idempotency_key:
        cached = checkpoint.check_idempotency(idempotency_key)
        if cached is not None:
            envelope = {
                "ok": True,
                "status": "completed",
                "skill_id": skill_meta["skill_id"],
                "slug": skill_meta["slug"],
                "skill_name": skill_meta["name"],
                "category_number": skill_meta["category_number"],
                "category_name": skill_meta["category_name"],
                "observation_mode": "directly_observed",
                "timestamp_utc": core.utc_now_iso(),
                "duration_ms": round((time.perf_counter() - t_start) * 1000.0, 3),
                "data": {"idempotent_replay_skipped": True, "prior_execution": cached},
                "metrics": {"replayed": 0},
                "warnings": ["Skipped duplicate execution because idempotency_key was already recorded in checkpoint."],
                "limitations": [],
                "redactions_applied": 0,
                "provenance": {
                    "input_path": input_path_str,
                    "input_sha256": input_sha,
                    "config_path": used_cfg_path,
                    "engine_module": skill_meta["module"],
                    "python_version": sys.version.split()[0],
                },
            }
            return envelope, 0

    # Check human-handoff triggers (CAPTCHA, MFA, legal acceptance, payment challenge)
    preview_txt = ""
    if resolved_input and resolved_input.is_file() and resolved_input.suffix.lower() in (".html", ".htm", ".txt"):
        preview_txt = resolved_input.read_text(encoding="utf-8", errors="ignore")[:8192]
    handoff = core.check_human_handoff_triggers(params, preview_txt, policy)
    if handoff and skill_meta["slug"] != "human-handoff-and-escalation-controller":
        raise core.HumanHandoffError(
            f"Human handoff required before running '{skill_meta['name']}': {handoff['handoff_reason']}",
            handoff,
        )

    # Check consequential/mutating approval gate
    gate_info = core.enforce_approval_gate(skill_meta, params, approve, approval_note, policy)

    if dry_run:
        envelope = {
            "ok": True,
            "status": "completed",
            "skill_id": skill_meta["skill_id"],
            "slug": skill_meta["slug"],
            "skill_name": skill_meta["name"],
            "category_number": skill_meta["category_number"],
            "category_name": skill_meta["category_name"],
            "observation_mode": "tested",
            "timestamp_utc": core.utc_now_iso(),
            "duration_ms": round((time.perf_counter() - t_start) * 1000.0, 3),
            "data": {"dry_run": True, "validated_input": str(resolved_input) if resolved_input else None, "approval_gate": gate_info},
            "metrics": {"dry_run": 1},
            "warnings": [],
            "limitations": ["Dry-run mode validated inputs and policy gates without invoking handler."],
            "redactions_applied": 0,
            "provenance": {
                "input_path": input_path_str,
                "input_sha256": input_sha,
                "config_path": used_cfg_path,
                "engine_module": skill_meta["module"],
                "python_version": sys.version.split()[0],
            },
        }
        return envelope, 0

    mod = core.load_sibling_module(skill_meta["module"])
    handlers = getattr(mod, "SKILL_HANDLERS", {})
    handler = handlers.get(skill_meta["slug"])
    if handler is None:
        raise core.SkillToolError(f"Handler not implemented for slug '{skill_meta['slug']}' in {skill_meta['module']}")

    timeout_s = min(
        float(timeout_override if timeout_override is not None else cfg["execution"]["timeout_seconds"]),
        float(cfg["execution"]["max_timeout_seconds"]),
    )
    retries = int(retries_override if retries_override is not None else cfg["execution"]["max_retries"])

    ctx = {
        "skill_meta": skill_meta,
        "input_path": resolved_input,
        "params": params,
        "cfg": cfg,
        "policy": policy,
        "approve": approve,
        "approval_note": approval_note,
    }

    raw_res, exec_stats = core.run_bounded(
        lambda: handler(ctx),
        timeout_seconds=timeout_s,
        max_retries=retries,
        backoff_seconds=float(cfg["execution"]["retry_backoff_seconds"]),
        backoff_multiplier=float(cfg["execution"]["retry_backoff_multiplier"]),
    )

    scrubbed_data = redactor.scrub_obj(raw_res.get("data", {})) if cfg["output"].get("redact_secrets", True) else raw_res.get("data", {})
    warnings = redactor.scrub_obj(raw_res.get("warnings", []))
    limitations = redactor.scrub_obj(raw_res.get("limitations", []))

    if ledger_path_str:
        l_path = core.validate_safe_path(ledger_path_str, must_exist=False, allow_dir=False)
        ledger = core.ActionLedger(l_path)
        ledger.append(
            step_id=params.get("step_id", skill_meta["skill_id"]),
            skill_id=skill_meta["skill_id"],
            observed_state=f"input={input_path_str or 'default_fixture'}",
            action_taken=f"executed:{skill_meta['slug']}",
            resulting_state="completed",
        )

    if chk_path or idempotency_key:
        checkpoint.record(
            step_id=str(params.get("step_id", skill_meta["skill_id"])),
            summary={"skill_id": skill_meta["skill_id"], "status": "completed"},
            idem_key=idempotency_key,
        )

    envelope = {
        "ok": True,
        "status": "completed",
        "skill_id": skill_meta["skill_id"],
        "slug": skill_meta["slug"],
        "skill_name": skill_meta["name"],
        "category_number": skill_meta["category_number"],
        "category_name": skill_meta["category_name"],
        "observation_mode": raw_res.get("observation_mode", "directly_observed"),
        "timestamp_utc": core.utc_now_iso(),
        "duration_ms": exec_stats["duration_ms"],
        "attempts": exec_stats["attempts"],
        "data": scrubbed_data,
        "metrics": raw_res.get("metrics", {}),
        "warnings": warnings,
        "limitations": limitations,
        "redactions_applied": redactor.redaction_count,
        "provenance": {
            "input_path": input_path_str,
            "input_sha256": input_sha,
            "config_path": used_cfg_path,
            "engine_module": skill_meta["module"],
            "python_version": sys.version.split()[0],
        },
    }
    return envelope, 0


def _error_envelope(exc: Exception, skill_hint: str = "") -> Tuple[Dict[str, Any], int]:
    status = getattr(exc, "status", "failed")
    details = getattr(exc, "details", {})
    code_map = {
        "validation_error": 2,
        "approval_required": 3,
        "human_handoff_required": 3,
        "blocked_by_policy": 3,
        "failed": 1,
    }
    env = {
        "ok": False,
        "status": status,
        "skill_id": details.get("skill_id", skill_hint or "unresolved"),
        "slug": details.get("slug", skill_hint or "unresolved"),
        "skill_name": details.get("skill_name", "Error Handler"),
        "category_number": 1,
        "category_name": "Authorization, Scope, and Safe Operation",
        "observation_mode": "blocked" if status in ("approval_required", "human_handoff_required", "blocked_by_policy") else "directly_observed",
        "timestamp_utc": core.utc_now_iso(),
        "data": {"error": str(exc), "details": details},
        "metrics": {},
        "warnings": [],
        "limitations": [],
        "redactions_applied": 0,
        "provenance": {"python_version": sys.version.split()[0]},
    }
    return env, code_map.get(status, 1)


# ===================== CLI SUBCOMMANDS =====================

def cmd_capabilities(args: argparse.Namespace) -> int:
    cfg, _ = core.load_runtime_config(args.config)
    registry = catalog_mod.SkillRegistry(cfg["paths"]["catalog_path"])
    skills = registry.filter_skills(category=args.category, search=args.search)

    if args.format == "table":
        lines = [f"{'ID':<9} {'CAT':<5} {'SLUG':<46} {'RISK':<22} NAME", "-" * 115]
        for s in skills:
            lines.append(f"{s['skill_id']:<9} {s['category_number']:<5} {s['slug']:<46} {s['risk_level']:<22} {s['name']}")
        lines.append(f"\nTotal matched capabilities: {len(skills)} / {len(registry.skills)} across {len(registry.categories)} categories.")
        _write_output("\n".join(lines), args.output, fmt="text")
        return 0

    if args.format == "markdown":
        lines = ["# Unified Capability List (`data-web-app-interaction`)", "", f"Matched **{len(skills)}** of **{len(registry.skills)}** capabilities.", ""]
        by_cat: Dict[int, List[Dict[str, Any]]] = {}
        for s in skills:
            by_cat.setdefault(s["category_number"], []).append(s)
        for cnum in sorted(by_cat):
            cname = by_cat[cnum][0]["category_name"]
            lines.append(f"## Category {cnum}: {cname}")
            for s in by_cat[cnum]:
                lines.append(f"- `{s['skill_id']}` (`{s['slug']}`) — **{s['name']}**: {s['description']}")
            lines.append("")
        _write_output("\n".join(lines), args.output, fmt="text")
        return 0

    payload = {
        "skill_tool_name": core.SKILL_TOOL_NAME,
        "total_categories": len(registry.categories),
        "total_capabilities": len(registry.skills),
        "matched_count": len(skills),
        "categories": registry.categories if not (args.category or args.search) else sorted({s["category_name"] for s in skills}),
        "capabilities": skills,
    }
    _write_output(payload, args.output, fmt="json")
    return 0


def cmd_inspect_skill(args: argparse.Namespace) -> int:
    cfg, _ = core.load_runtime_config(args.config)
    registry = catalog_mod.SkillRegistry(cfg["paths"]["catalog_path"])
    skill = registry.resolve(args.skill)
    handlers = load_all_handlers()
    payload = {
        "ok": True,
        "skill": skill,
        "handler_registered": skill["slug"] in handlers,
        "example_command": f"bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill {skill['slug']}'",
    }
    _write_output(payload, args.output, fmt="json")
    return 0


def cmd_parse_catalog(args: argparse.Namespace) -> int:
    if args.text:
        raw = args.text
    elif args.input:
        in_file = core.validate_safe_path(args.input, must_exist=True, allow_dir=False)
        raw = in_file.read_text(encoding="utf-8", errors="replace")
    else:
        raise core.InputValidationError("Provide either --input <file> or --text '<specification>' for parse-catalog.")
    parsed = catalog_mod.parse_skill_specification(raw)
    _write_output({"ok": True, "status": "completed", "parsed": parsed}, args.output, fmt="json")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {}
    if args.params_file:
        pfile = core.validate_safe_path(args.params_file, must_exist=True, allow_dir=False)
        params.update(json.loads(pfile.read_text(encoding="utf-8")))
    if args.params:
        try:
            inline_p = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise core.InputValidationError(f"Invalid JSON passed to --params: {exc}") from exc
        if not isinstance(inline_p, dict):
            raise core.InputValidationError("--params must decode to a JSON object.")
        params.update(inline_p)

    envelope, code = execute_single_skill(
        skill_identifier=args.skill,
        input_path_str=args.input,
        params=params,
        config_path=args.config,
        approve=args.approve,
        approval_note=args.approval_note,
        dry_run=args.dry_run,
        timeout_override=args.timeout,
        retries_override=args.max_retries,
        ledger_path_str=args.ledger_path,
        checkpoint_path_str=args.checkpoint_path,
        idempotency_key=args.idempotency_key,
    )
    _write_output(envelope, args.output, fmt=args.format)
    return code


def cmd_workflow(args: argparse.Namespace) -> int:
    if args.workflow_file:
        wf_path = core.validate_safe_path(args.workflow_file, must_exist=True, allow_dir=False)
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
    elif args.steps_json:
        wf = json.loads(args.steps_json)
    else:
        raise core.InputValidationError("Provide --workflow-file or --steps-json for workflow execution.")

    if not isinstance(wf, dict) or not isinstance(wf.get("steps"), list) or not wf["steps"]:
        raise core.InputValidationError("Workflow JSON must contain a non-empty 'steps' array.")

    chk_path = core.validate_safe_path(args.checkpoint_path, must_exist=False, allow_dir=False) if args.checkpoint_path else None
    checkpoint = core.CheckpointStore(chk_path)
    l_path = core.validate_safe_path(args.ledger_path, must_exist=False, allow_dir=False) if args.ledger_path else None
    ledger = core.ActionLedger(l_path)

    step_results = []
    skipped_resumed = 0
    overall_ok = True

    for step in wf["steps"]:
        step_id = str(step.get("step_id") or step.get("skill"))
        if checkpoint.is_completed(step_id):
            skipped_resumed += 1
            step_results.append({"step_id": step_id, "status": "resumed_skipped", "prior": checkpoint.state["completed_steps"][step_id]})
            continue

        try:
            env, code = execute_single_skill(
                skill_identifier=str(step["skill"]),
                input_path_str=step.get("input_path"),
                params=step.get("params", {}),
                config_path=args.config,
                approve=args.approve or bool(step.get("approve", False)),
                approval_note=args.approval_note,
            )
        except Exception as exc:
            env, code = _error_envelope(exc, str(step.get("skill", "")))

        step_results.append({"step_id": step_id, "status": env["status"], "skill_id": env["skill_id"], "metrics": env.get("metrics", {})})
        ledger.append(
            step_id=step_id,
            skill_id=env["skill_id"],
            observed_state=f"step_input={step.get('input_path', 'default')}",
            action_taken=f"workflow_step:{step.get('skill')}",
            resulting_state=env["status"],
        )
        if code == 0:
            checkpoint.record(step_id, {"skill_id": env["skill_id"], "status": "completed"})
        else:
            overall_ok = False
            if wf.get("stop_on_error", True):
                break

    summary = {
        "ok": overall_ok,
        "status": "completed" if overall_ok else "failed",
        "workflow_name": wf.get("workflow_name", "unnamed-workflow"),
        "steps_total": len(wf["steps"]),
        "steps_executed": len(step_results) - skipped_resumed,
        "steps_resumed_from_checkpoint": skipped_resumed,
        "ledger_entries": len(ledger.entries),
        "results": step_results,
    }
    _write_output(summary, args.output, fmt="json")
    return 0 if overall_ok else 1


def cmd_verify_all(args: argparse.Namespace) -> int:
    cfg, _ = core.load_runtime_config(args.config)
    registry = catalog_mod.SkillRegistry(cfg["paths"]["catalog_path"])
    handlers = load_all_handlers()

    missing_handlers = [s["slug"] for s in registry.skills if s["slug"] not in handlers]
    passed = 0
    failures: List[Dict[str, Any]] = []
    by_category_passed: Dict[int, int] = {}

    for s in registry.skills:
        try:
            env, code = execute_single_skill(skill_identifier=s["skill_id"], config_path=args.config)
            if code == 0 and env.get("ok"):
                passed += 1
                by_category_passed[s["category_number"]] = by_category_passed.get(s["category_number"], 0) + 1
            else:
                failures.append({"skill_id": s["skill_id"], "slug": s["slug"], "status": env.get("status")})
        except Exception as exc:
            failures.append({"skill_id": s["skill_id"], "slug": s["slug"], "error": str(exc)})

    report = {
        "ok": len(missing_handlers) == 0 and len(failures) == 0 and passed == len(registry.skills),
        "status": "completed" if (not missing_handlers and not failures) else "failed",
        "total_catalog_skills": len(registry.skills),
        "registered_handlers": len(handlers),
        "passed_executions": passed,
        "missing_handlers": missing_handlers,
        "failures": failures,
        "categories_verified": len(by_category_passed),
    }
    _write_output(report, args.output, fmt="json")
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-cli.py",
        description="Unified production Skill Tool for Data, Web, App, Game, File, and Interactive Systems (33 categories, 330 capabilities).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_cap = sub.add_parser("capabilities", help="List all 33 categories and 330 capabilities with optional filtering.")
    p_cap.add_argument("--category", help="Filter by category number (1-33), ID (cat01), slug, or name substring.")
    p_cap.add_argument("--search", help="Filter capabilities by keyword across ID, slug, name, or description.")
    p_cap.add_argument("--format", choices=["json", "table", "markdown"], default="json", help="Output presentation format.")
    p_cap.add_argument("--config", help="Optional JSON config path.")
    p_cap.add_argument("--output", help="Optional output file path.")

    p_ins = sub.add_parser("inspect-skill", help="Show detailed specification and example invocation for one capability.")
    p_ins.add_argument("--skill", required=True, help="Skill ID (e.g. cat01-01), slug, or exact name.")
    p_ins.add_argument("--config", help="Optional JSON config path.")
    p_ins.add_argument("--output", help="Optional output file path.")

    p_parse = sub.add_parser("parse-catalog", help="Extract categories, skill names, and descriptions from text, Markdown, or HTML.")
    p_parse.add_argument("--input", help="Path to plain text, Markdown, or copied HTML specification file.")
    p_parse.add_argument("--text", help="Inline plain text, Markdown, or HTML specification string.")
    p_parse.add_argument("--output", help="Optional output JSON file path.")

    p_run = sub.add_parser("run", help="Execute a capability with pre-validation, safety gates, and structured output.")
    p_run.add_argument("--skill", required=True, help="Skill ID (cat01-01..cat33-10), slug, or exact skill name.")
    p_run.add_argument("--input", help="Input file or directory path.")
    p_run.add_argument("--output", help="Output file path for structured result.")
    p_run.add_argument("--config", help="Custom JSON configuration file path.")
    p_run.add_argument("--params", help="Inline JSON object of capability parameters.")
    p_run.add_argument("--params-file", help="Path to JSON file containing capability parameters.")
    p_run.add_argument("--approve", action="store_true", help="Explicitly approve consequential, publishing, financial, or mutating actions.")
    p_run.add_argument("--approval-note", default="", help="Reason/scope note recorded alongside --approve.")
    p_run.add_argument("--dry-run", action="store_true", help="Validate inputs and safety gates without executing handler.")
    p_run.add_argument("--timeout", type=float, help="Override execution timeout in seconds.")
    p_run.add_argument("--max-retries", type=int, help="Override bounded retry count.")
    p_run.add_argument("--ledger-path", help="Optional JSONL path to append hash-chained observation/action ledger entry.")
    p_run.add_argument("--checkpoint-path", help="Optional JSON path for checkpoint and idempotency tracking.")
    p_run.add_argument("--idempotency-key", help="Optional unique key preventing duplicate execution on rerun.")
    p_run.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format (default: json).")

    p_wf = sub.add_parser("workflow", help="Execute a multi-step capability pipeline with checkpoint resume and action ledger.")
    p_wf.add_argument("--workflow-file", help="Path to workflow JSON file.")
    p_wf.add_argument("--steps-json", help="Inline workflow JSON string.")
    p_wf.add_argument("--checkpoint-path", help="Path to checkpoint JSON file for crash recovery and resume.")
    p_wf.add_argument("--ledger-path", help="Path to append-only hash-chained ledger JSONL file.")
    p_wf.add_argument("--config", help="Optional JSON config path.")
    p_wf.add_argument("--approve", action="store_true", help="Approve consequential workflow steps.")
    p_wf.add_argument("--approval-note", default="", help="Approval note.")
    p_wf.add_argument("--output", help="Optional output JSON file path.")

    p_ver = sub.add_parser("verify-all", help="Execute deterministic smoke verification across all 330 capabilities.")
    p_ver.add_argument("--config", help="Optional JSON config path.")
    p_ver.add_argument("--output", help="Optional output JSON file path.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "capabilities": cmd_capabilities,
        "inspect-skill": cmd_inspect_skill,
        "parse-catalog": cmd_parse_catalog,
        "run": cmd_run,
        "workflow": cmd_workflow,
        "verify-all": cmd_verify_all,
    }
    try:
        return dispatch[args.subcommand](args)
    except Exception as exc:
        env, code = _error_envelope(exc, getattr(args, "skill", ""))
        out_p = getattr(args, "output", None)
        _write_output(env, out_p, fmt="json")
        return code


if __name__ == "__main__":
    sys.exit(main())
