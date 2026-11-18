#!/usr/bin/env python3
"""powerful-work-splitter :: primary entry point.

Unified Skill Tool for Category 1 of the Ultimate Agent Skill Catalog
(Orchestration, Planning, and Work Splitting). Twelve capabilities are exposed
through one command surface:

  capabilities  plan  assess  estimate  negotiate  decompose  scheduler
  gate  validate-plan  ledger  files  checkpoint  recover  verify  assert
  watch  cleanup  demo  help

Run `python3 tools-work-splitter.py help` for the command list, or
`python3 tools-work-splitter.py capabilities` for the capability list.
Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import re
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.dont_write_bytecode = True  # keep the Skill Tool folder free of __pycache__

TOOL_VERSION = "1.0.0"
CORE_FILENAME = "tools-work-splitter-core.py"
VALIDATOR_FILENAME = "tools-skill-validator.py"


def _load_module(filename: str, module_name: str):
    path = Path(__file__).resolve().parent / filename
    if not path.is_file():
        raise SystemExit(f"Required packaged module is missing: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"Unable to load packaged module: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


core = _load_module(CORE_FILENAME, "pws_core")
validator = _load_module(VALIDATOR_FILENAME, "pws_validator")

ToolError = core.ToolError
EXIT_OK = core.EXIT_OK
EXIT_USAGE = core.EXIT_USAGE
EXIT_MISSING = core.EXIT_MISSING
EXIT_GATE = core.EXIT_GATE
EXIT_APPROVAL = core.EXIT_APPROVAL
EXIT_LIMIT = core.EXIT_LIMIT
EXIT_PARTIAL = core.EXIT_PARTIAL
EXIT_INTERRUPTED = core.EXIT_INTERRUPTED

COMMANDS_HELP = """
Commands
  capabilities     List the twelve capabilities, their commands and a machine-readable index
  plan             Normalize a request and produce a validated stage plan (primary command)
  assess           Single-prompt feasibility decision with a fallback staged plan
  estimate         Recommended, minimum and maximum stage counts for a request
  negotiate        Rebalance an existing plan to a user-chosen stage count
  decompose        Dependency graph, execution waves and merge contracts
  scheduler        Critical path, buffers and escalation triggers
  gate             List gates or check entry/exit criteria against supplied evidence
  validate-plan    Validate a plan JSON file against the plan schema and policy
  ledger           Export the requirement ledger as CSV or Markdown
  files            Inventory the workspace and detect missing, duplicate or unreadable files
  checkpoint       start | status | continue | block | reopen | set-count
  recover          Bounded repair loop for a failed stage (retry, reopen, rollback)
  verify           Run the final verification gates (coverage, gates, files, artifacts, delivery)
  assert           Assert artifact state: file, csv, json, manifest, reference
  watch            Wait until workspace writes settle, bounded by a timeout
  cleanup          Propose or (with --approve cleanup) apply temp-file cleanup
  demo             End-to-end self-contained walkthrough in a temporary workspace
  help             This message

Examples
  python3 tools-work-splitter.py plan --request request.md --count 5 --save-plan plan.json
  python3 tools-work-splitter.py checkpoint start --plan plan.json --state state.json
  python3 tools-work-splitter.py checkpoint continue --state state.json --reply "continue" --verified "figures reconciled"
  python3 tools-work-splitter.py verify --plan plan.json --state state.json --root .
  python3 tools-work-splitter.py demo
"""


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _envelope(command: str, data: Any, *, warnings: Optional[Sequence[str]] = None, ok: bool = True) -> Dict[str, Any]:
    payload = {
        "ok": ok,
        "command": command,
        "tool": core.TOOL_NAME,
        "version": TOOL_VERSION,
        "data": data,
        "warnings": list(warnings or []),
    }
    return payload


def _write_out(path: Optional[str], text: str) -> None:
    if not path:
        return
    target = Path(path)
    core.atomic_write(target, text if text.endswith("\n") else text + "\n")


def emit(
    command: str,
    data: Any,
    *,
    fmt: str,
    out: Optional[str],
    text_lines: Sequence[str],
    warnings: Optional[Sequence[str]] = None,
    ok: bool = True,
) -> None:
    if fmt == "json":
        payload = _envelope(command, data, warnings=warnings, ok=ok)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = "\n".join(text_lines)
        if warnings:
            text += "\n\nWarnings:\n" + "\n".join(f"  - {item}" for item in warnings)
    print(text)
    _write_out(out, text)


def _require(args: argparse.Namespace, name: str) -> Any:
    value = getattr(args, name, None)
    if value in (None, "", []):
        raise ToolError(
            f"Missing required argument --{name.replace('_', '-')}",
            code="argument-missing",
            exit_code=EXIT_USAGE,
        )
    return value


# ---------------------------------------------------------------------------
# Request loading
# ---------------------------------------------------------------------------


def load_request_text(args: argparse.Namespace) -> Tuple[str, Optional[Path]]:
    request = getattr(args, "request", None)
    request_text = getattr(args, "request_text", None)
    if request and request_text:
        raise ToolError(
            "Use either --request or --request-text, not both",
            code="argument-conflict",
            exit_code=EXIT_USAGE,
        )
    if request_text is not None:
        source = None
        raw = request_text
    elif request == "-":
        raw = sys.stdin.read()
        source = None
    elif request:
        path = Path(request)
        raw = core.read_text(path, limit=int(args_limit(args)["max_text_chars"]))
        source = path
    else:
        raise ToolError(
            "A request is required: pass --request <file>, --request-text <text>, or --request - for stdin",
            code="request-missing",
            exit_code=EXIT_USAGE,
            hint="Example: --request request.md",
        )
    if not raw or not raw.strip():
        raise ToolError(
            "The supplied request is empty after trimming",
            code="request-empty",
            exit_code=EXIT_USAGE,
        )
    return raw, source


def args_limit(args: argparse.Namespace) -> Dict[str, int]:
    policy = core.load_policy(getattr(args, "policy", None))
    return {"max_text_chars": int(policy["limits"]["max_text_chars"])}


def derive_goal(raw_goal: Optional[str], normalized: str, requirements: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    if raw_goal and raw_goal.strip():
        return raw_goal.strip()[:300]
    deliverables = [r["text"] for r in (requirements or []) if r.get("kind") == "deliverable"][:2]
    if deliverables:
        goal = "; ".join(deliverables)
        if not re.match(r"^(produce|create|deliver|write|build|draft|design|generate|export|prepare)\b", goal, re.IGNORECASE):
            goal = "Deliver " + goal[0].lower() + goal[1:]
        return (goal[:297] + "...") if len(goal) > 300 else goal
    fallback: Optional[str] = None
    for line in normalized.split("\n"):
        candidate = core.LIST_MARK_RE.sub("", line).strip()
        if len(candidate) < 8 or core.is_name_line(line):
            continue
        if fallback is None:
            fallback = candidate
        if not core.looks_like_title(candidate) and len(candidate) >= 20:
            return (candidate[:297] + "...") if len(candidate) > 300 else candidate
    if fallback:
        return (fallback[:297] + "...") if len(fallback) > 300 else fallback
    return "Execute the requested work with verified staged delivery"


def build_file_contract(
    normalized: str,
    *,
    root: Path,
    policy: Dict[str, Any],
    extra_files: Sequence[str] = (),
    optional_files: Sequence[str] = (),
) -> Tuple[List[Dict[str, Any]], List[str]]:
    declared = core.extract_files(normalized, max_files=int(policy["files"]["max_files"]))
    for name in extra_files:
        if name not in declared:
            declared.append(name)
    optional = {Path(name).name.lower() for name in optional_files}
    present_names = set()
    if root.exists():
        present_names = {p.name.lower() for p in root.rglob("*") if p.is_file()}
    files: List[Dict[str, Any]] = []
    missing: List[str] = []
    for name in declared:
        base = Path(name).name.lower()
        is_present = base in present_names
        files.append(
            {
                "path": name,
                "role": "input",
                "required": base not in optional,
                "present": is_present,
                "status": "ok" if is_present else "missing",
            }
        )
        if not is_present and base not in optional:
            missing.append(name)
    return files, missing


def analyse_request(args: argparse.Namespace) -> Dict[str, Any]:
    """Shared front half of plan/assess/estimate: normalize, extract, measure."""
    policy = core.load_policy(getattr(args, "policy", None))
    raw, source = load_request_text(args)
    normalized = core.normalize_input(raw)
    if not normalized.strip():
        raise ToolError(
            "No usable text remained after stripping markup and spacing",
            code="request-empty",
            exit_code=EXIT_USAGE,
        )
    root_value = getattr(args, "root", None)
    root = Path(root_value) if root_value else (source.parent if source else Path.cwd())
    if not root.exists():
        raise ToolError(f"Workspace root not found: {root}", code="root-missing", exit_code=EXIT_MISSING)

    requirements = core.extract_requirements(
        normalized,
        max_requirements=int(policy["limits"]["max_requirements"]),
        explicit=getattr(args, "requirement", None) or None,
    )
    if not requirements:
        raise ToolError(
            "No requirements could be extracted from the request",
            code="requirements-empty",
            exit_code=EXIT_USAGE,
            hint="Add detail to the request or pass one or more --requirement values.",
        )
    files, missing_required = build_file_contract(
        normalized,
        root=root,
        policy=policy,
        extra_files=getattr(args, "file", None) or (),
        optional_files=getattr(args, "optional_file", None) or (),
    )
    metrics = core.compute_metrics(
        requirements,
        files,
        policy=policy,
        tool_calls=int(getattr(args, "tool_calls", 0) or 0),
        dependency_depth=getattr(args, "dependency_depth", None),
        uncertainty=getattr(args, "uncertainty", None),
        verification=getattr(args, "verification", None),
    )
    estimate = core.estimate_counts(float(metrics["units"]), metrics, policy)
    assumptions: List[Dict[str, Any]] = []
    if missing_required:
        assumptions.append(
            {
                "id": "A1",
                "text": "Required files are assumed obtainable from the user before dependent stages run",
                "impact": "high",
                "recheck_trigger": "a stage needs the file and it is still absent",
            }
        )
    if getattr(args, "assume", None):
        for index, text in enumerate(args.assume, start=len(assumptions) + 1):
            assumptions.append(
                {
                    "id": f"A{index}",
                    "text": text,
                    "impact": "medium",
                    "recheck_trigger": "the user supplies contradicting information",
                }
            )
    return {
        "policy": policy,
        "raw": raw,
        "normalized": normalized,
        "requirements": requirements,
        "files": files,
        "missing_required": missing_required,
        "metrics": metrics,
        "estimate": estimate,
        "assumptions": assumptions,
        "root": root,
        "goal": derive_goal(getattr(args, "goal", None), normalized, requirements),
        "capabilities": core.classify_capabilities(normalized),
    }


def resolve_stage_count(args: argparse.Namespace, policy: Dict[str, Any], estimate: Dict[str, Any]) -> Tuple[int, str]:
    bounds = policy["stage_bounds"]
    count = getattr(args, "count", None)
    if count is None:
        return int(estimate["recommended"]), "recommended"
    if count < bounds["min"] or count > bounds["max"]:
        raise ToolError(
            f"--count {count} is outside the allowed range {bounds['min']}-{bounds['max']}",
            code="stage-count-invalid",
            exit_code=EXIT_USAGE,
            hint=f"Choose a count between {bounds['min']} and {bounds['max']}.",
        )
    return int(count), "user-selected"


def make_plan(args: argparse.Namespace) -> Dict[str, Any]:
    analysis = analyse_request(args)
    policy = analysis["policy"]
    count, origin = resolve_stage_count(args, policy, analysis["estimate"])
    plan = core.build_plan(
        goal=analysis["goal"],
        requirements=analysis["requirements"],
        files=analysis["files"],
        metrics=analysis["metrics"],
        estimate=analysis["estimate"],
        policy=policy,
        stage_count=count,
        stage_count_origin=origin,
        capabilities=analysis["capabilities"],
        assumptions=analysis["assumptions"],
        scope={"in": [], "out": [], "deferred": []},
        warnings=[f"Required file(s) not found in {analysis['root'].as_posix()}: " + ", ".join(analysis["missing_required"])]
        if analysis["missing_required"]
        else [],
    )
    plan["source"] = analysis["root"].as_posix()
    schema_path = core.resource_path("asset-plan-schema.json")
    schema = core.load_json_file(schema_path, label="Plan schema")
    schema_errors = validator.validate_json_schema(plan, schema)
    if schema_errors:
        raise ToolError(
            "Plan does not satisfy asset-plan-schema.json",
            code="plan-schema-invalid",
            exit_code=EXIT_USAGE,
            details={"errors": schema_errors},
        )
    return plan


# ---------------------------------------------------------------------------
# Commands: plan, assess, estimate
# ---------------------------------------------------------------------------


def cmd_capabilities(args: argparse.Namespace) -> int:
    data = {
        "tool": core.TOOL_NAME,
        "version": TOOL_VERSION,
        "category": "1. Orchestration, Planning, and Work Splitting",
        "capability_count": len(core.CAPABILITIES),
        "capabilities": core.CAPABILITIES,
        "capability_ids": [cap["id"] for cap in core.CAPABILITIES],
        "primary_entry_point": "tools-work-splitter.py",
        "documentation": "knowledge-readme.md",
    }
    lines = [
        f"{core.TOOL_NAME} {TOOL_VERSION} - Category 1: Orchestration, Planning, and Work Splitting",
        f"Capabilities: {len(core.CAPABILITIES)}",
        "",
    ]
    for index, cap in enumerate(core.CAPABILITIES, start=1):
        lines.append(f"{index:>2}. {cap['name']}  [{cap['id']}]")
        lines.append(f"    {cap['summary']}")
        lines.append(f"    Commands: {cap['commands']}")
    lines.append("")
    lines.append("All commands: run 'python3 tools-work-splitter.py help'.")
    emit("capabilities", data, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    plan = make_plan(args)
    save = getattr(args, "save_plan", None)
    if save:
        core.atomic_write_json(Path(save), plan)
    text = core.render_plan_text(plan)
    if save:
        text += f"\n\nPlan saved: {Path(save).as_posix()}"
    emit("plan", plan, fmt=args.format, out=args.out, text_lines=text.split("\n"))
    return EXIT_OK


def cmd_assess(args: argparse.Namespace) -> int:
    analysis = analyse_request(args)
    policy = analysis["policy"]
    metrics = analysis["metrics"]
    estimate = analysis["estimate"]
    fallback = core.build_plan(
        goal=analysis["goal"],
        requirements=analysis["requirements"],
        files=analysis["files"],
        metrics=metrics,
        estimate=estimate,
        policy=policy,
        stage_count=estimate["recommended"],
        stage_count_origin="recommended",
        capabilities=analysis["capabilities"],
        assumptions=analysis["assumptions"],
        warnings=[],
    )
    decision = "single_response" if metrics["single_response_feasible"] else "staged"
    data = {
        "decision": decision,
        "single_response_feasible": metrics["single_response_feasible"],
        "metrics": metrics,
        "reasons": estimate["reasons"],
        "recommended_stage_count": estimate["recommended"],
        "fallback_staged_plan": {
            "stage_count": fallback["stage_count"],
            "stages": [{"id": s["id"], "title": s["title"], "requirements": s["requirements"]} for s in fallback["stages"]],
        },
        "capabilities": analysis["capabilities"],
    }
    lines = [
        f"Feasibility: {'ONE RESPONSE IS SUFFICIENT' if metrics['single_response_feasible'] else 'STAGED WORK REQUIRED'}",
        f"Work units: {metrics['units']} of {metrics['single_response_budget_units']} one-response budget  |  Risk: {metrics['risk']}",
        f"Requirements: {metrics['inputs']['requirements']} ({metrics['inputs']['deliverables']} deliverables)"
        f"  |  Files: {metrics['inputs']['files']} ({metrics['inputs']['files_missing']} missing)"
        f"  |  Tool calls: {metrics['inputs']['tool_calls']}",
        "",
        "Reasons:",
    ]
    lines.extend(f"  - {reason}" for reason in estimate["reasons"])
    lines.append("")
    lines.append(f"Fallback staged plan ({fallback['stage_count']} stages):")
    for stage in fallback["stages"]:
        lines.append(f"  {stage['id']}  {stage['title']}  [{', '.join(stage['requirements']) or '-'}]")
    lines.append("")
    lines.append("Next action: run 'plan' with --count to lock the stage count the user accepts.")
    emit("assess", data, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


def cmd_estimate(args: argparse.Namespace) -> int:
    analysis = analyse_request(args)
    estimate = analysis["estimate"]
    data = {
        "recommended": estimate["recommended"],
        "minimum": estimate["minimum"],
        "maximum": estimate["maximum"],
        "confidence": estimate["confidence"],
        "units": estimate["units"],
        "reasons": estimate["reasons"],
        "metrics": analysis["metrics"],
    }
    lines = [
        f"Recommended stages: {estimate['recommended']}  (minimum {estimate['minimum']}, maximum {estimate['maximum']}, confidence {estimate['confidence']})",
        f"Work units: {estimate['units']}  |  Single-response feasible: {'yes' if analysis['metrics']['single_response_feasible'] else 'no'}",
        "",
        "Why:",
    ]
    lines.extend(f"  - {reason}" for reason in estimate["reasons"])
    lines.append("")
    lines.append("Next action: accept the recommendation or override it with 'plan --count N'.")
    emit("estimate", data, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Commands: negotiate, decompose, scheduler, gate, validate-plan, ledger
# ---------------------------------------------------------------------------


def load_plan(path: str, *, policy: Dict[str, Any]) -> Dict[str, Any]:
    plan = core.load_json_file(Path(path), label="Plan")
    errors = core.validate_plan(plan, policy=policy)
    if errors:
        raise ToolError(
            "Plan file failed validation",
            code="plan-invalid",
            exit_code=EXIT_USAGE,
            details={"errors": errors},
        )
    return plan


def cmd_negotiate(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    count = int(_require(args, "count"))
    new_plan, warnings, tradeoffs = core.rebalance_plan(
        plan, count, policy=policy, direction=args.direction
    )
    if args.save_plan:
        core.atomic_write_json(Path(args.save_plan), new_plan)
    data = {
        "previous_stage_count": plan["stage_count"],
        "stage_count": new_plan["stage_count"],
        "direction": args.direction,
        "tradeoffs": tradeoffs,
        "plan": new_plan,
    }
    lines = [
        f"Rebalanced plan: {plan['stage_count']} -> {new_plan['stage_count']} stages ({args.direction})",
        "",
    ]
    for stage in new_plan["stages"]:
        lines.append(
            f"  {stage['id']}  {stage['title']:<48} exit gate {stage.get('exit_gate') or '-':<15}"
            f" [{', '.join(stage['requirements']) or 'gate-only'}]"
        )
    lines.append("")
    lines.append("Trade-offs and guarantees:")
    lines.extend(f"  - {item}" for item in tradeoffs)
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in warnings)
    if args.save_plan:
        lines.append("")
        lines.append(f"Plan saved: {Path(args.save_plan).as_posix()}")
    lines.append("")
    lines.append("Next action: every requirement still has one owner stage; continue with 'checkpoint start' or 'checkpoint set-count'.")
    emit("negotiate", data, fmt=args.format, out=args.out, text_lines=lines, warnings=warnings)
    return EXIT_OK


def cmd_decompose(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    graph = core.decompose(plan, parallel=bool(args.parallel))
    lines = [
        f"Dependency graph: {graph['node_count']} work package(s), {graph['edge_count']} edge(s), {graph['wave_count']} wave(s)",
        "",
        "Execution waves:",
    ]
    for index, wave in enumerate(graph["waves"], start=1):
        lines.append(f"  Wave {index}: {', '.join(wave)}")
    lines.append("")
    lines.append("Work packages:")
    for node in graph["nodes"]:
        lines.append(f"  {node['id']:<4} {node['owner_stage']:<4} [{node['kind']:<11}] {node['text'][:80]}")
    if graph["merge_contracts"]:
        lines.append("")
        lines.append("Merge contracts:")
        for contract in graph["merge_contracts"]:
            lines.append(
                f"  {contract['stage']}: key {contract['merge_key']}, fields {', '.join(contract['requires_fields'])}, version {contract['version']}"
            )
    lines.append("")
    lines.append("Next action: run 'scheduler' to rank the work that controls completion time.")
    emit("decompose", graph, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


def cmd_scheduler(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    schedule = core.critical_path(plan, policy=policy, parallel=bool(getattr(args, "parallel", False)))
    lines = [
        f"Critical path: {' -> '.join(item['stage'] for item in schedule['critical_path']) or '(none)'}",
        f"Total estimate: {schedule['total_estimate_units']} units  |  Buffer: {schedule['buffer_units']}  |  Committed: {schedule['committed_total_units']}",
        "",
        "Path stages:",
    ]
    for item in schedule["critical_path"]:
        lines.append(f"  {item['stage']}  {item['duration_units']:>5} units  {item['title']}")
    lines.append("")
    lines.append("Stage durations:")
    for stage_id, duration in schedule["stage_durations"].items():
        lines.append(f"  {stage_id}: {duration} units")
    lines.append("")
    lines.append("Escalation triggers:")
    lines.extend(f"  - {trigger}" for trigger in schedule["escalation_triggers"])
    lines.append("")
    lines.append("Next action: gate each critical stage before starting dependent work.")
    emit("scheduler", schedule, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


def cmd_gate(args: argparse.Namespace) -> int:
    gates_config = core.load_gates(args.gates)
    if args.list or not args.gate:
        data = {
            "gates": {
                name: {
                    "phase": spec.get("phase"),
                    "blocking": spec.get("blocking", True),
                    "entry": [c["id"] for c in spec.get("entry", [])],
                    "exit": [c["id"] for c in spec.get("exit", [])],
                }
                for name, spec in gates_config["gates"].items()
            }
        }
        lines = ["Available gates:"]
        for name, spec in gates_config["gates"].items():
            lines.append(f"  {name:<16} phase {spec.get('phase', '-'):<10} blocking={spec.get('blocking', True)}")
            for criterion in spec.get("exit", []):
                lines.append(f"      {criterion['id']:<4} {'required' if criterion.get('required') else 'optional':<9} {criterion['criterion']}")
        emit("gate", data, fmt=args.format, out=args.out, text_lines=lines)
        return EXIT_OK
    if not args.evidence:
        raise ToolError(
            "Gate checks require --evidence <file> with criterion results",
            code="evidence-missing",
            exit_code=EXIT_USAGE,
            hint="Provide {\"D1\": {\"passed\": true, \"evidence\": \"...\"}} per criterion id.",
        )
    evidence = core.load_json_file(Path(args.evidence), label="Gate evidence")
    result = core.check_gate(args.gate, gates_config=gates_config, evidence=evidence, phase=args.phase)
    lines = [
        f"Gate {result['gate']} ({result['phase']}): {result['status'].upper()}",
        "",
    ]
    for item in result["results"]:
        flag = "pass" if item["passed"] else "fail"
        required = "required" if item["required"] else "optional"
        lines.append(f"  [{flag}] {item['id']:<4} {required:<9} {item['criterion']}")
        if item["evidence"]:
            lines.append(f"         evidence: {item['evidence']}")
    if result["missing_evidence"]:
        lines.append("")
        lines.append("Missing evidence for required criteria: " + ", ".join(result["missing_evidence"]))
    if result["unknown_evidence_keys"]:
        lines.append("Ignored unknown evidence keys: " + ", ".join(result["unknown_evidence_keys"]))
    lines.append("")
    lines.append("Next action: " + result["next_action"])
    emit("gate", result, fmt=args.format, out=args.out, text_lines=lines)
    if args.require and result["status"] != "pass":
        return EXIT_GATE
    return EXIT_OK


def cmd_validate_plan(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = core.load_json_file(Path(_require(args, "plan")), label="Plan")
    errors = core.validate_plan(plan, policy=policy)
    schema_errors: List[str] = []
    if args.schema:
        schema = core.load_json_file(Path(args.schema), label="Schema")
        schema_errors = validator.validate_json_schema(plan, schema)
    passed = not errors and not schema_errors
    data = {"valid": passed, "errors": errors, "schema_errors": schema_errors, "stage_count": plan.get("stage_count")}
    lines = [f"Plan validation: {'PASS' if passed else 'FAIL'}"]
    if errors:
        lines.append("")
        lines.append("Structural errors:")
        lines.extend(f"  - {error}" for error in errors)
    if schema_errors:
        lines.append("")
        lines.append("Schema errors:")
        lines.extend(f"  - {error}" for error in schema_errors)
    if passed:
        lines.append(f"  - {plan.get('stage_count')} stages, {len(plan.get('requirements', []))} requirements, all owners resolved")
    emit("validate-plan", data, fmt=args.format, out=args.out, text_lines=lines, ok=passed)
    return EXIT_OK if passed else EXIT_USAGE


def cmd_ledger(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    if args.ledger_format == "csv":
        content = core.ledger_csv(plan)
        lines = content.rstrip("\n").split("\n")
    else:
        content = core.ledger_markdown(plan)
        lines = content.rstrip("\n").split("\n")
    data = {"format": args.ledger_format, "rows": core.ledger_rows(plan), "content": content}
    emit("ledger", data, fmt="json" if args.format == "json" else args.format, out=args.out, text_lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Command: files
# ---------------------------------------------------------------------------


def cmd_files(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    root = Path(_require(args, "root"))
    inventory = core.inventory_files(
        root,
        policy=policy,
        declared=args.declared or (),
        required=args.required or (),
    )
    lines = [
        f"File inventory: {inventory['root']}",
        f"Files: {inventory['count']}  |  Present: {inventory['present']}  |  Missing: {len(inventory['missing'])}  |  Duplicates: {len(inventory['duplicates'])}",
        "",
    ]
    for entry in inventory["files"][: int(policy["output"]["max_preview_items"])]:
        flag = entry["status"]
        lines.append(f"  [{flag:<9}] {entry['path']}" + (f"  ({entry['size']} bytes)" if entry["size"] else ""))
    if len(inventory["files"]) > int(policy["output"]["max_preview_items"]):
        lines.append(f"  ... {len(inventory['files']) - int(policy['output']['max_preview_items'])} more (use --format json for the full list)")
    if inventory["missing_required"]:
        lines.append("")
        lines.append("Re-upload required before dependent work:")
        lines.extend(f"  - {name}" for name in inventory["missing_required"])
    lines.append("")
    lines.append("Next actions:")
    lines.extend(f"  - {action}" for action in inventory["next_actions"])
    emit("files", inventory, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Command: checkpoint
# ---------------------------------------------------------------------------


def _state_path(args: argparse.Namespace) -> Path:
    return Path(_require(args, "_state"))


def _stage_receipt(state: Dict[str, Any]) -> List[str]:
    return core.render_checkpoint_text(state).split("\n")


def checkpoint_start(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    state_path = _state_path(args)
    if state_path.exists() and not args.force:
        raise ToolError(
            f"Checkpoint state already exists: {state_path}",
            code="state-exists",
            exit_code=EXIT_USAGE,
            hint="Pass --force to overwrite, or choose a new --state path.",
        )
    files_state = plan.get("files", [])
    if args.root:
        inventory = core.inventory_files(
            Path(args.root),
            policy=policy,
            declared=[item["path"] for item in plan.get("files", [])],
            required=[item["path"] for item in plan.get("files", []) if item.get("required", True)],
        )
        files_state = [
            {
                "path": entry["path"],
                "role": "input",
                "required": entry["required"],
                "present": entry["status"] != "missing",
                "status": entry["status"],
            }
            for entry in inventory["files"]
        ]
        plan["files"] = files_state
    state = core.state_from_plan(plan, policy=policy, files_state=files_state)
    core.save_state(state_path, state, policy=policy)
    warnings = plan.get("warnings", [])
    if [f for f in files_state if f.get("required") and not f.get("present")]:
        warnings = list(warnings) + [
            "Required file(s) missing: "
            + ", ".join(f["path"] for f in files_state if f.get("required") and not f.get("present"))
        ]
    data = {"state_path": state_path.as_posix(), "state": state}
    lines = [f"Checkpoint opened: {state_path.as_posix()}"] + [""] + _stage_receipt(state)
    emit("checkpoint-start", data, fmt=args.format, out=args.out, text_lines=lines + ([chr(10) + "Warnings:"] + [f"  - {w}" for w in warnings] if warnings else []))
    return EXIT_OK


def checkpoint_status(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    state_path = _state_path(args)
    state = core.load_state(state_path, policy=policy)
    data = {"state_path": state_path.as_posix(), "state": state}
    emit("checkpoint-status", data, fmt=args.format, out=args.out, text_lines=_stage_receipt(state))
    return EXIT_OK


def checkpoint_continue(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    intents = core.load_intents(args.intents)
    state_path = _state_path(args)
    state = core.load_state(state_path, policy=policy)
    classification = core.classify_reply(_require(args, "reply"), intents)

    if args.dry_run:
        data = {"classification": classification, "state_path": state_path.as_posix(), "advanced": False}
        lines = [
            f"Reply: {classification['reply']!r}",
            f"Recognized intent: {classification['intent']}",
            classification["message"],
        ]
        emit("checkpoint-continue", data, fmt=args.format, out=args.out, text_lines=lines, ok=classification["intent"] == "continue")
        return EXIT_OK if classification["intent"] == "continue" else (
            EXIT_PARTIAL if classification["intent"] == "stop" else EXIT_USAGE
        )

    if classification["intent"] == "stop":
        state["paused"] = True
        state["next_action"] = "Paused by explicit stop request; resume with checkpoint continue"
        state["history"].append({"at": core.now_iso(), "event": "stop", "detail": classification["reply"]})
        core.save_state(state_path, state, policy=policy)
        emit(
            "checkpoint-continue",
            {"classification": classification, "state": state},
            fmt=args.format,
            out=args.out,
            text_lines=["Work paused. State preserved.", classification["message"]],
            ok=False,
        )
        return EXIT_PARTIAL

    if classification["intent"] == "scope_change":
        state["history"].append({"at": core.now_iso(), "event": "scope-change-requested", "detail": classification["reply"]})
        core.save_state(state_path, state, policy=policy)
        raise ToolError(
            "The reply changes scope or stage count, so the plan was not advanced",
            code="scope-change",
            exit_code=EXIT_USAGE,
            hint="Run 'checkpoint set-count --count N' to rebalance, then continue.",
            details={"reply": classification["reply"], "current_stage_count": state["stage_count"]},
        )

    if classification["intent"] == "needs_clarification":
        raise ToolError(
            "The reply is ambiguous, so the plan was not advanced",
            code="reply-ambiguous",
            exit_code=EXIT_USAGE,
            hint="Reply with Continue, a stage count change, or Stop.",
            details={"reply": classification["reply"], "normalized": classification["normalized"]},
        )

    # intent == continue
    total = int(state["stage_count"])
    index = int(state["current_stage_index"])
    if index >= total:
        raise ToolError(
            "Every stage is already complete",
            code="state-complete",
            exit_code=EXIT_USAGE,
            hint="Run 'verify' to confirm the package, or 'checkpoint reopen' to reopen a stage.",
        )
    stage = state["stages"][index]
    if stage.get("status") == "blocked" and not args.force:
        raise ToolError(
            f"Stage {stage['id']} is blocked",
            code="stage-blocked",
            exit_code=EXIT_GATE,
            hint="Run 'recover --stage <id> --action retry|reopen|rollback' before continuing.",
            details={"blockers": stage.get("blockers", [])},
        )
    verified = list(args.verified or [])
    outputs = list(args.output or [])
    if not verified and not args.force:
        raise ToolError(
            f"Stage {stage['id']} cannot be closed without a verification record",
            code="verification-missing",
            exit_code=EXIT_USAGE,
            hint="Pass at least one --verified \"what you checked\" note, or --force after disclosing the gap.",
        )
    missing_outputs: List[str] = []
    if args.root and outputs:
        root = Path(args.root)
        present = {p.name.lower() for p in root.rglob("*") if p.is_file()} if root.exists() else set()
        missing_outputs = [item for item in outputs if Path(item).name.lower() not in present]
        if missing_outputs and not args.force:
            raise ToolError(
                "Declared outputs are missing from the workspace, so the stage cannot be closed",
                code="output-missing",
                exit_code=EXIT_GATE,
                hint="Produce the artifact, correct the path, or pass --force to record the gap.",
                details={"root": root.as_posix(), "missing": missing_outputs},
            )

    stamp = core.now_iso()
    stage["status"] = "complete"
    stage["completed_at"] = stamp
    stage["started_at"] = stage.get("started_at") or stamp
    stage["verified"] = verified
    stage["outputs"] = sorted(set(list(stage.get("outputs", [])) + outputs))
    stage["notes"] = args.note or ""
    state["completed_count"] = sum(1 for s in state["stages"] if s.get("status") == "complete")
    state["current_stage_index"] = min(index + 1, total)
    state["history"].append(
        {
            "at": stamp,
            "event": "stage-complete",
            "detail": f"{stage['id']} closed with {len(verified)} verification note(s)",
            "verified": verified,
            "outputs": outputs,
        }
    )
    warnings: List[str] = []
    if missing_outputs:
        warnings.append("Missing outputs were accepted with --force: " + ", ".join(missing_outputs))
    if not verified:
        warnings.append("Stage closed with --force and no verification record; disclose this in the delivery summary")

    if state["current_stage_index"] >= total:
        state["paused"] = False
        state["next_action"] = "All stages complete: run 'verify' for the final gate set, then deliver"
        receipt = [
            f"Stage {total} of {total} complete: {stage['title']}",
            f"Completed stages: {state['completed_count']} of {total}",
            "Next action: run 'verify --plan <plan.json> --state <state.json> --root <dir>'",
        ]
        data = {"state_path": state_path.as_posix(), "state": state, "closed_stage": stage["id"], "finished": True}
        core.save_state(state_path, state, policy=policy)
        emit("checkpoint-continue", data, fmt=args.format, out=args.out, text_lines=receipt, warnings=warnings)
        return EXIT_OK

    next_stage = state["stages"][state["current_stage_index"]]
    state["paused"] = True
    state["next_action"] = f"Complete Stage {state['current_stage_index'] + 1} of {total}: {next_stage['title']}, then reply Continue"
    state["stages"][state["current_stage_index"]]["status"] = "in-progress"
    state["stages"][state["current_stage_index"]]["started_at"] = stamp
    core.save_state(state_path, state, policy=policy)
    receipt = _stage_receipt(state)
    receipt.insert(0, f"Closed Stage {index + 1} of {total} after verification.")
    data = {"state_path": state_path.as_posix(), "state": state, "closed_stage": stage["id"], "finished": False}
    emit("checkpoint-continue", data, fmt=args.format, out=args.out, text_lines=receipt, warnings=warnings)
    return EXIT_OK


def checkpoint_block(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    state_path = _state_path(args)
    state = core.load_state(state_path, policy=policy)
    index = int(state["current_stage_index"])
    if index >= int(state["stage_count"]):
        raise ToolError("Every stage is already complete", code="state-complete", exit_code=EXIT_USAGE)
    stage = state["stages"][index]
    reason = _require(args, "reason")
    stage["status"] = "blocked"
    stage["blockers"] = list(stage.get("blockers", [])) + [reason]
    state["paused"] = True
    state["next_action"] = f"Recover Stage {index + 1} of {state['stage_count']} before continuing"
    state["history"].append({"at": core.now_iso(), "event": "stage-blocked", "detail": reason, "stage": stage["id"]})
    core.save_state(state_path, state, policy=policy)
    data = {"state_path": state_path.as_posix(), "stage": stage["id"], "blockers": stage["blockers"]}
    lines = [
        f"Stage {index + 1} of {state['stage_count']} blocked: {stage['title']}",
        f"Reason: {reason}",
        "",
        "Recovery options:",
        "  python3 tools-work-splitter.py recover --state <state.json> --stage "
        + stage["id"]
        + " --action retry --cause \"<cause>\"",
        "  python3 tools-work-splitter.py recover --state <state.json> --stage "
        + stage["id"]
        + " --action reopen --cause \"<cause>\"",
    ]
    emit("checkpoint-block", data, fmt=args.format, out=args.out, text_lines=lines, ok=False)
    return EXIT_GATE


def checkpoint_reopen(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    state_path = _state_path(args)
    state = core.load_state(state_path, policy=policy)
    target = args.stage or state["stages"][max(int(state["current_stage_index"]) - 1, 0)]["id"]
    reason = args.reason or "Reopened for repair"
    reopened: List[str] = []
    found = False
    for stage in state["stages"]:
        if stage["id"] == target:
            found = True
        if found:
            if stage["status"] in ("complete", "blocked"):
                reopened.append(stage["id"])
            stage["status"] = "pending"
            stage["completed_at"] = None
            stage["verified"] = []
            stage["blockers"] = []
    if not found:
        raise ToolError(
            f"Unknown stage: {target}",
            code="stage-unknown",
            exit_code=EXIT_USAGE,
            details={"stages": [s["id"] for s in state["stages"]]},
        )
    state["current_stage_index"] = next(i for i, s in enumerate(state["stages"]) if s["id"] == target)
    state["completed_count"] = sum(1 for s in state["stages"] if s["status"] == "complete")
    state["paused"] = True
    state["next_action"] = f"Repair and re-verify Stage {state['current_stage_index'] + 1} of {state['stage_count']}, then reply Continue"
    state["history"].append({"at": core.now_iso(), "event": "stage-reopened", "detail": reason, "stages": reopened})
    core.save_state(state_path, state, policy=policy)
    data = {"state_path": state_path.as_posix(), "reopened": reopened, "state": state}
    lines = [
        f"Reopened stage(s): {', '.join(reopened) or 'none'}",
        f"Reason: {reason}",
        "Successful work in earlier stages is preserved.",
        "",
    ] + _stage_receipt(state)
    emit("checkpoint-reopen", data, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


def checkpoint_set_count(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    state_path = _state_path(args)
    state = core.load_state(state_path, policy=policy)
    bounds = policy["stage_bounds"]
    new_total = int(_require(args, "count"))
    frozen_count = int(state["current_stage_index"])

    if new_total < bounds["min"] or new_total > bounds["max"]:
        raise ToolError(
            f"--count {new_total} is outside the allowed range {bounds['min']}-{bounds['max']}",
            code="stage-count-invalid",
            exit_code=EXIT_USAGE,
        )
    if new_total <= frozen_count:
        raise ToolError(
            f"Cannot reduce to {new_total} stages because {frozen_count} stage(s) are already opened or complete",
            code="stage-count-invalid",
            exit_code=EXIT_GATE,
            hint=f"Choose a count between {frozen_count + 1} and {bounds['max']}, or reopen earlier stages first.",
        )

    plan = state["plan"]
    frozen_stages = state["stages"][:frozen_count]
    frozen_req_ids = {rid for stage in frozen_stages for rid in stage.get("requirements", [])}
    remaining = [req for req in plan["requirements"] if req["id"] not in frozen_req_ids]
    remaining_files = plan.get("files", [])

    if not remaining:
        raise ToolError(
            "No remaining requirements to replan; every requirement is already owned by an opened stage",
            code="replan-empty",
            exit_code=EXIT_USAGE,
        )

    remaining_metrics = core.compute_metrics(remaining, remaining_files, policy=policy)
    remaining_estimate = core.estimate_counts(float(remaining_metrics["units"]), remaining_metrics, policy)
    sub_plan = core.build_plan(
        goal=plan["goal"],
        requirements=remaining,
        files=remaining_files,
        metrics=remaining_metrics,
        estimate=remaining_estimate,
        policy=policy,
        stage_count=new_total - frozen_count,
        stage_count_origin="user-selected",
        capabilities=plan.get("capabilities", []),
        assumptions=plan.get("assumptions", []),
        warnings=[],
    )

    # Discovery already happened in a preserved stage, so the rebuilt tail must
    # resume with build work instead of repeating a discovery checkpoint.
    if frozen_count > 0 and sub_plan["stages"] and sub_plan["stages"][0]["role"] == "discovery":
        head = sub_plan["stages"][0]
        head["role"] = "build"
        head["title"] = "Resume build and evidence work"
        head["objective"] = "Continue the build from the preserved checkpoint without repeating discovery"

    # The sub-plan numbers its own stages from S1; remap ownership onto the
    # absolute stage ids used by the live checkpoint.
    tail_id_map = {
        stage["id"]: f"S{frozen_count + index}"
        for index, stage in enumerate(sub_plan["stages"], start=1)
    }
    for req in sub_plan["requirements"]:
        if req.get("owner_stage") in tail_id_map:
            req["owner_stage"] = tail_id_map[req["owner_stage"]]

    # Splice: frozen stage records stay byte-for-byte, the tail is rebuilt.
    merged_stages = list(frozen_stages)
    for index, stage in enumerate(sub_plan["stages"], start=frozen_count + 1):
        merged_stages.append(
            {
                "id": f"S{index}",
                "title": stage["title"],
                "role": stage["role"],
                "objective": stage["objective"],
                "requirements": list(stage["requirements"]),
                "outputs": list(stage.get("outputs", [])),
                "entry_gate": stage.get("entry_gate"),
                "exit_gate": stage.get("exit_gate"),
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "verified": [],
                "blockers": [],
                "attempts": 0,
                "notes": "",
            }
        )

    for index, stage in enumerate(merged_stages):
        if stage["role"] == "build":
            stage["entry_gate"] = "build"
            stage["exit_gate"] = "verification" if index == len(merged_stages) - 2 else "build"

    requirement_lookup = {req["id"]: req for req in plan["requirements"]}
    for req in sub_plan["requirements"]:
        requirement_lookup[req["id"]] = req
    merged_requirements = list(plan["requirements"])
    for index, req in enumerate(merged_requirements):
        replacement = requirement_lookup.get(req["id"])
        if replacement:
            merged_requirements[index] = replacement

    overrides: Dict[str, Dict[str, Any]] = {}
    for index, stage in enumerate(merged_stages):
        overrides[stage["id"]] = {
            "id": stage["id"],
            "title": stage["title"],
            "role": stage["role"],
            "objective": stage["objective"],
            "requirements": stage["requirements"],
            "outputs": stage["outputs"],
            "entry_gate": stage["entry_gate"],
            "exit_gate": stage["exit_gate"],
            "status": stage["status"],
            "depends_on": [f"S{index}"] if index > 0 else [],
        }

    new_plan = dict(plan)
    new_plan["stages"] = [
        dict(overrides[stage["id"]]) for stage in merged_stages
    ]
    for index, stage in enumerate(new_plan["stages"]):
        stage["effort_share"] = round(1.0 / len(new_plan["stages"]), 3)
        stage["estimated_units"] = round(
            sum(core.KIND_WEIGHTS.get(r["kind"], 1.0) for r in merged_requirements if r["owner_stage"] == stage["id"])
            + float(policy["unit_defaults"]["stage_overhead_units"]),
            2,
        )
    new_plan["requirements"] = merged_requirements
    new_plan["stage_count"] = len(merged_stages)
    new_plan["stage_count_origin"] = "user-selected"
    new_plan["user_selected_stage_count"] = len(merged_stages)
    new_plan["updated_at"] = core.now_iso()
    warnings = [w for w in plan.get("warnings", []) if not w.startswith("Stage(s) ")] + [
        f"Stage count changed mid-stream from {state['stage_count']} to {new_total}; stages 1-{frozen_count} are preserved unchanged"
    ]
    new_plan["warnings"] = warnings

    errors = core.validate_plan(new_plan, policy=policy)
    if errors:
        raise ToolError(
            "Replanned tail failed validation, so the state was not modified",
            code="plan-invalid",
            exit_code=EXIT_USAGE,
            details={"errors": errors},
        )

    previous_total = state["stage_count"]
    state["plan"] = new_plan
    state["stages"] = merged_stages
    state["stage_count"] = len(merged_stages)
    state["completed_count"] = sum(1 for s in merged_stages if s.get("status") == "complete")
    state["paused"] = True
    state["next_action"] = f"Complete Stage {frozen_count + 1} of {state['stage_count']}: {merged_stages[frozen_count]['title']}, then reply Continue"
    state["history"].append(
        {
            "at": core.now_iso(),
            "event": "stage-count-changed",
            "detail": f"{previous_total} -> {new_total} stages; {frozen_count} stage(s) preserved",
        }
    )
    core.save_state(state_path, state, policy=policy)

    data = {
        "previous_stage_count": previous_total,
        "stage_count": state["stage_count"],
        "preserved_stages": frozen_count,
        "state": state,
        "plan": new_plan,
    }
    lines = [
        f"Stage count changed: {previous_total} -> {state['stage_count']}",
        f"Preserved stage(s): 1-{frozen_count} (outputs, verification notes and requirement ownership unchanged)",
        "",
        "Remaining plan:",
    ]
    for index, stage in enumerate(merged_stages[frozen_count:], start=frozen_count + 1):
        lines.append(f"  Stage {index} of {state['stage_count']}  {stage['title']:<44} [{', '.join(stage['requirements']) or 'gate-only'}]")
    lines.append("")
    lines.append("All requirements still have exactly one owner stage, and the final stage still performs assembly.")
    lines.append("")
    lines.append(f"Next action: {state['next_action']}")
    emit("checkpoint-set-count", data, fmt=args.format, out=args.out, text_lines=lines, warnings=warnings)
    return EXIT_OK


def cmd_checkpoint(args: argparse.Namespace) -> int:
    actions = {
        "start": checkpoint_start,
        "status": checkpoint_status,
        "continue": checkpoint_continue,
        "block": checkpoint_block,
        "reopen": checkpoint_reopen,
        "set-count": checkpoint_set_count,
    }
    handler = actions.get(args.action)
    if handler is None:
        raise ToolError(
            f"Unknown checkpoint action: {args.action}",
            code="action-unknown",
            exit_code=EXIT_USAGE,
            hint="Use one of: " + ", ".join(actions),
        )
    return handler(args)


# ---------------------------------------------------------------------------
# Command: recover
# ---------------------------------------------------------------------------


def cmd_recover(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    state_path = Path(_require(args, "state"))
    state = core.load_state(state_path, policy=policy)
    stage_id = _require(args, "stage")
    cause = _require(args, "cause")
    action = args.action

    attempt = validator.record_repair_attempt(
        state, stage_id=stage_id, cause=cause, action=action, policy=policy
    )
    stage = next(s for s in state["stages"] if s["id"] == stage_id)
    reopened: List[str] = []
    if action == "retry":
        stage["status"] = "in-progress"
        stage["blockers"] = []
        next_action = f"Retry Stage {stage_id} with a changed approach, then reply Continue"
    elif action in ("reopen", "rollback"):
        progress = True
        for candidate in state["stages"]:
            if candidate["id"] == stage_id:
                progress = True
            if progress:
                if candidate["status"] in ("complete", "blocked"):
                    reopened.append(candidate["id"])
                candidate["status"] = "pending"
                candidate["completed_at"] = None
                candidate["verified"] = []
                candidate["blockers"] = []
        state["current_stage_index"] = next(i for i, s in enumerate(state["stages"]) if s["id"] == stage_id)
        state["completed_count"] = sum(1 for s in state["stages"] if s["status"] == "complete")
        next_action = f"Rebuild Stage {stage_id} from the last safe checkpoint, then reply Continue"
    else:  # pragma: no cover - argparse restricts choices
        raise ToolError(f"Unknown recovery action: {action}", code="action-unknown")
    stage["notes"] = (stage.get("notes") or "") + f"\nrepair attempt {attempt['attempts']}: {cause}"
    state["paused"] = True
    state["next_action"] = next_action
    state["history"].append(
        {
            "at": core.now_iso(),
            "event": "recovery",
            "detail": f"{action} on {stage_id}: {cause}",
            "attempt": attempt["attempts"],
        }
    )
    core.save_state(state_path, state, policy=policy)
    data = {"action": action, "stage": stage_id, "attempt": attempt, "reopened": reopened, "state": state}
    lines = [
        f"Recovery: {action} on stage {stage_id} (attempt {attempt['attempts']} of {attempt['limit']})",
        f"Cause: {cause}",
        f"Backoff applied: {attempt['backoff_seconds']}s",
        f"Remaining attempts before escalation: {attempt['remaining']}",
    ]
    if reopened:
        lines.append("Reopened stage(s): " + ", ".join(reopened))
    lines.append("Preserved work: earlier verified stages are untouched")
    lines.append("")
    lines.append(f"Next action: {next_action}")
    emit("recover", data, fmt=args.format, out=args.out, text_lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Commands delegated to the validator module
# ---------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    plan = load_plan(_require(args, "plan"), policy=policy)
    state = None
    if args.state:
        state = core.load_state(Path(args.state), policy=policy)
    result = validator.verify_package(
        plan=plan,
        state=state,
        root=Path(_require(args, "root")),
        policy=policy,
        require_artifacts=not args.allow_missing_artifacts,
    )
    lines = validator.render_text(result)
    emit("verify", result, fmt=args.format, out=args.out, text_lines=lines, ok=result["status"] == "pass")
    return EXIT_OK if result["status"] == "pass" else (EXIT_GATE if result["status"] != "repair" else EXIT_PARTIAL)


def cmd_assert(args: argparse.Namespace) -> int:
    result = validator.run_assert(
        args.kind,
        Path(_require(args, "path")),
        min_bytes=args.min_bytes,
        signature=args.signature,
        expected_ext=args.expected_ext,
        require_header=args.require_header or (),
        min_rows=args.min_rows,
        expect_rows=args.expect_rows,
        schema_path=Path(args.schema) if args.schema else None,
        require_keys=args.require_key or (),
        root=Path(args.root) if args.root else None,
    )
    lines = [f"Assertion {args.kind}: {result['status'].upper()} ({result.get('path', args.path)})"]
    for key in ("bytes", "rows", "count", "checked"):
        if key in result:
            lines.append(f"  {key}: {result[key]}")
    for problem in result.get("problems", []):
        lines.append(f"  - {problem}")
    emit("assert", result, fmt=args.format, out=args.out, text_lines=lines, ok=result["status"] == "pass")
    return EXIT_OK if result["status"] == "pass" else EXIT_GATE


def cmd_watch(args: argparse.Namespace) -> int:
    result = validator.watch(
        Path(_require(args, "root")),
        settle_seconds=args.settle,
        timeout_seconds=args.timeout,
        poll_seconds=args.poll,
    )
    lines = [
        f"Watch: {result['status']}",
        f"Stable for: {result['stable_for_seconds']}s  |  Elapsed: {result['elapsed_seconds']}s  |  Polls: {result['polls']}  |  Entries: {result['entries']}",
    ]
    if result.get("next_action"):
        lines.append(result["next_action"])
    emit("watch", result, fmt=args.format, out=args.out, text_lines=lines, ok=result["status"] == "settled")
    return EXIT_OK if result["status"] == "settled" else EXIT_PARTIAL


def cmd_cleanup(args: argparse.Namespace) -> int:
    policy = core.load_policy(args.policy)
    patterns = args.pattern or list(core.TEMP_PATTERNS)
    result = validator.cleanup(
        Path(_require(args, "root")),
        patterns=patterns,
        approve=args.approve,
        policy=policy,
        backup_dir=Path(args.backup) if args.backup else None,
    )
    lines = validator.render_text(result)
    emit("cleanup", result, fmt=args.format, out=args.out, text_lines=lines, ok=result["status"] in ("applied", "nothing-to-do"))
    if result["status"] in ("applied", "nothing-to-do"):
        return EXIT_OK
    return EXIT_APPROVAL


# ---------------------------------------------------------------------------
# Command: demo
# ---------------------------------------------------------------------------


def _write_minimal_xlsx(path: Path, rows: Sequence[Sequence[Any]]) -> None:
    """Write a small but genuine XLSX (OOXML zip) used as a demo fixture."""
    def cell(ref: str, value: Any) -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    row_xml = []
    for r_index, row in enumerate(rows, start=1):
        cells = "".join(cell(f"{chr(64 + c_index)}{r_index}", value) for c_index, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{r_index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="figures" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def cmd_demo(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="pws-demo-"))
    workdir.mkdir(parents=True, exist_ok=True)
    steps: List[str] = []

    def step(number: int, title: str, detail: Sequence[str] = ()) -> None:
        header = f"[{number}] {title}"
        steps.append(header)
        for line in detail:
            steps.append(f"    {line}")

    try:
        request_path = core.resource_path("asset-example-request.md")
        raw = core.read_text(request_path)
        normalized = core.normalize_input(raw)
        step(1, "Normalize a messy pasted request", [f"source: {request_path.name}", f"lines after stripping HTML, CSS classes, data attributes and numbering: {len(normalized.splitlines())}"])

        # Demo fixtures: real, openable files so the file contract can pass.
        _write_minimal_xlsx(
            workdir / "quarterly-figures.xlsx",
            [["quarter", "revenue", "margin"], ["Q1", 120000, 0.31], ["Q2", 138500, 0.33]],
        )
        _write_minimal_xlsx(
            workdir / "quarterly-figures-v2.xlsx",
            [["quarter", "revenue", "margin"], ["Q1", 121000, 0.31], ["Q2", 138500, 0.34]],
        )
        (workdir / "brand-notes.md").write_text("# Brand notes\n\nBoard-ready tone, deep navy palette, no stock photography.\n", encoding="utf-8")
        step(2, "Create demo input fixtures", [".xlsx files are genuine OOXML zips; brand-notes.md is plain markdown"])

        policy = core.load_policy(args.policy)
        requirements = core.extract_requirements(normalized, max_requirements=int(policy["limits"]["max_requirements"]))
        files, missing = build_file_contract(normalized, root=workdir, policy=policy)
        step(
            3,
            "Extract requirements and file contract",
            [
                f"requirements: {len(requirements)} (first: {requirements[0]['text'][:70]}...)",
                f"files declared: {len(files)}; missing: {', '.join(missing) or 'none'}",
            ],
        )

        metrics = core.compute_metrics(requirements, files, policy=policy)
        estimate = core.estimate_counts(float(metrics["units"]), metrics, policy)
        plan = core.build_plan(
            goal="Produce a verified 12-page investor briefing PDF",
            requirements=requirements,
            files=files,
            metrics=metrics,
            estimate=estimate,
            policy=policy,
            stage_count=min(int(args.stages) if args.stages else 4, policy["stage_bounds"]["max"]),
            stage_count_origin="user-selected",
            capabilities=core.classify_capabilities(normalized),
            warnings=[],
        )
        plan_path = workdir / "plan.json"
        core.atomic_write_json(plan_path, plan)
        step(
            4,
            "Plan and save",
            [
                f"single-response feasible: {plan['single_response_feasible']}",
                f"recommended {plan['recommendation']['recommended']} / used {plan['stage_count']} stages",
                f"plan saved: {plan_path.name}",
            ],
        )

        graph = core.decompose(plan, parallel=True)
        schedule = core.critical_path(plan, policy=policy, parallel=True)
        step(
            5,
            "Decompose and schedule",
            [
                f"work packages: {graph['node_count']} in {graph['wave_count']} wave(s)",
                f"critical path: {' -> '.join(item['stage'] for item in schedule['critical_path'])}",
                f"committed units incl. buffer: {schedule['committed_total_units']}",
            ],
        )

        state_path = workdir / "state.json"
        state = core.state_from_plan(plan, policy=policy)
        core.save_state(state_path, state, policy=policy)
        step(6, "Open the checkpoint", [f"state: {state_path.name}", core.render_checkpoint_text(state).split(chr(10))[0]])

        intents = core.load_intents()
        first = core.classify_reply("continue", intents)
        ambiguous = core.classify_reply("ok", intents)
        scope = core.classify_reply("actually use 6 stages", intents)
        step(
            7,
            "Classify continuation replies",
            [
                f"continue -> {first['intent']}",
                f"'ok' -> {ambiguous['intent']} (not advanced)",
                f"'actually use 6 stages' -> {scope['intent']} (rebalance first)",
            ],
        )

        for index in range(plan["stage_count"]):
            stage = state["stages"][index]
            artifact = workdir / f"{stage['id'].lower()}-{core.slugify(stage['title'], max_length=30)}.md"
            artifact.write_text(
                f"# {stage['id']} {stage['title']}\n\nObjective: {stage['objective']}\n\n"
                + "\n".join(f"- {rid}" for rid in stage["requirements"])
                + "\n",
                encoding="utf-8",
            )
            validator.assert_file(artifact)
            with contextlib.redirect_stdout(io.StringIO()):
                stage_complete = checkpoint_continue(
                argparse.Namespace(
                    policy=args.policy,
                    intents=None,
                    _state=state_path.as_posix(),
                    reply="continue",
                    dry_run=False,
                    verified=[f"artifact {artifact.name} written and reopened"],
                    output=[artifact.name],
                    note="demo fixture stage",
                    root=workdir.as_posix(),
                    force=False,
                        format="json",
                        out=None,
                    )
                )
            if stage_complete != EXIT_OK:
                raise ToolError("demo stage rehearsal failed", code="demo-failed", exit_code=EXIT_PARTIAL)
            state = core.load_state(state_path, policy=policy)
        step(8, "Close every stage through the real checkpoint interface", [f"completed {state['completed_count']} of {state['stage_count']} stages"])

        ledger_path = workdir / "requirement-ledger.csv"
        core.atomic_write(ledger_path, core.ledger_csv(plan))
        csv_check = validator.assert_csv(ledger_path, require_header=["id", "text", "owner_stage", "acceptance"], min_rows=1)
        step(9, "Export and assert the requirement ledger", [f"csv check: {csv_check['status']} with {csv_check['rows']} row(s)"])

        gate_check = core.check_gate(
            "build",
            gates_config=core.load_gates(),
            evidence={
                "B0": {"passed": True, "evidence": "requirements locked in plan.json"},
                "B1": {"passed": True, "evidence": "stage artifacts written"},
                "B2": {"passed": True, "evidence": "artifacts reopened by the demo"},
                "B3": {"passed": True, "evidence": "merge contract fields present"},
            },
        )
        step(10, "Check a milestone gate", [f"gate build -> {gate_check['status']}"])

        verification = validator.verify_package(plan=plan, state=state, root=workdir, policy=policy)
        step(
            11,
            "Run the verification gate set",
            [f"overall: {verification['status']}"] + [f"{c['check']}: {c['status']}" for c in verification["checks"]],
        )

        (workdir / "scratch.tmp").write_text("temporary\n", encoding="utf-8")
        proposal = validator.cleanup(workdir, patterns=list(core.TEMP_PATTERNS), approve=None, policy=policy)
        applied = validator.cleanup(workdir, patterns=list(core.TEMP_PATTERNS), approve="cleanup", policy=policy)
        step(
            12,
            "Cleanup with explicit approval",
            [
                f"unapproved -> {proposal['status']} ({proposal['candidate_count']} candidate(s), nothing removed)",
                f"approved -> {applied['status']} ({applied.get('applied_count', 0)} removed)",
            ],
        )

        recovery_state = core.load_state(state_path, policy=policy)
        recovery_state["stages"][-1]["status"] = "blocked"
        recovery_state["stages"][-1]["blockers"] = ["demo: simulated render failure"]
        attempt = validator.record_repair_attempt(
            recovery_state, stage_id=recovery_state["stages"][-1]["id"], cause="simulated render failure", action="reopen", policy=policy
        )
        step(13, "Bounded repair bookkeeping", [f"attempt {attempt['attempts']} of {attempt['limit']}, backoff {attempt['backoff_seconds']}s"])

        data = {
            "workdir": workdir.as_posix(),
            "steps": steps,
            "verification": verification,
            "stage_count": plan["stage_count"],
            "single_response_feasible": plan["single_response_feasible"],
            "recommended_stages": plan["recommendation"]["recommended"],
        }
        lines = ["powerful-work-splitter end-to-end demo", ""] + steps + ["", f"Verification: {verification['status']}"]
        if args.workdir:
            lines.append(f"Workspace kept for inspection: {workdir.as_posix()}")
        else:
            lines.append("Temporary workspace removed; pass --workdir <dir> or --keep to inspect the fixtures.")
        emit("demo", data, fmt=args.format, out=args.out, text_lines=lines, ok=verification["status"] == "pass")
        return EXIT_OK if verification["status"] == "pass" else EXIT_PARTIAL
    finally:
        if not args.workdir and not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - argparse path
        self.print_usage(sys.stderr)
        raise ToolError(message, code="argument-invalid", exit_code=EXIT_USAGE, hint="Run with help for usage.")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="tools-work-splitter.py",
        description=(
            "powerful-work-splitter: a unified Skill Tool for orchestration, planning and work splitting "
            "(Ultimate Agent Skill Catalog, category 1). Decides single-response versus staged work, "
            "recommends and rebalances stage counts, controls checkpoints, decomposes dependencies, "
            "schedules critical work, enforces milestone gates and verifies final assembly."
        ),
        epilog="Run 'python3 tools-work-splitter.py capabilities' for the capability list, or 'demo' for a rehearsal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"powerful-work-splitter {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command")

    def add_common(target: argparse.ArgumentParser, *, policy: bool = True) -> None:
        target.add_argument("--format", choices=["text", "json"], default="text", help="output format")
        target.add_argument("--out", default=None, help="also write the rendered output to this path")
        if policy:
            target.add_argument("--policy", default=None, help="policy override JSON (merged over asset-default-policy.json)")

    def add_request_inputs(target: argparse.ArgumentParser) -> None:
        target.add_argument("--request", default=None, help="request file (Markdown, text, or copied HTML); '-' reads stdin")
        target.add_argument("--request-text", default=None, help="request supplied inline")
        target.add_argument("--goal", default=None, help="explicit goal statement")
        target.add_argument("--requirement", action="append", default=[], help="explicit requirement (repeatable)")
        target.add_argument("--file", action="append", default=[], help="additional declared input file (repeatable)")
        target.add_argument("--optional-file", action="append", default=[], help="declared file that is not required (repeatable)")
        target.add_argument("--assume", action="append", default=[], help="assumption recorded with the plan (repeatable)")
        target.add_argument("--root", default=None, help="workspace root used to check declared file presence")
        target.add_argument("--tool-calls", type=int, default=0, help="number of planned tool calls")
        target.add_argument("--dependency-depth", type=int, default=None, help="dependency depth override")
        target.add_argument("--uncertainty", type=float, default=None, help="uncertainty 0.0-1.0")
        target.add_argument("--verification", type=float, default=None, help="verification burden 0.0-1.0")
        target.add_argument("--count", type=int, default=None, help="user-selected stage count")

    cap = sub.add_parser("capabilities", help="list capabilities")
    add_common(cap)
    cap.set_defaults(func=cmd_capabilities)

    plan = sub.add_parser("plan", help="produce a validated stage plan")
    add_common(plan)
    add_request_inputs(plan)
    plan.add_argument("--save-plan", default=None, help="write the plan JSON to this path")
    plan.set_defaults(func=cmd_plan)

    assess = sub.add_parser("assess", help="single-response feasibility decision")
    add_common(assess)
    add_request_inputs(assess)
    assess.set_defaults(func=cmd_assess)

    estimate = sub.add_parser("estimate", help="stage count estimate")
    add_common(estimate)
    add_request_inputs(estimate)
    estimate.set_defaults(func=cmd_estimate)

    negotiate = sub.add_parser("negotiate", help="rebalance a plan to a chosen stage count")
    add_common(negotiate)
    negotiate.add_argument("--plan", required=True)
    negotiate.add_argument("--count", type=int, required=True)
    negotiate.add_argument("--direction", choices=["auto", "expand", "compress", "hold"], default="auto")
    negotiate.add_argument("--save-plan", default=None)
    negotiate.set_defaults(func=cmd_negotiate)

    decompose = sub.add_parser("decompose", help="dependency graph and waves")
    add_common(decompose)
    decompose.add_argument("--plan", required=True)
    decompose.add_argument("--parallel", action="store_true", help="emit parallel merge contracts")
    decompose.set_defaults(func=cmd_decompose)

    scheduler = sub.add_parser("scheduler", help="critical path and buffers")
    add_common(scheduler)
    scheduler.add_argument("--plan", required=True)
    scheduler.add_argument("--parallel", action="store_true")
    scheduler.set_defaults(func=cmd_scheduler)

    gate = sub.add_parser("gate", help="list or check milestone gates")
    add_common(gate)
    gate.add_argument("--list", action="store_true", help="list gate definitions")
    gate.add_argument("--gate", default=None, help="gate name to check")
    gate.add_argument("--phase", choices=["entry", "exit"], default="exit")
    gate.add_argument("--evidence", default=None, help="JSON file of criterion results")
    gate.add_argument("--gates", default=None, help="gate criteria override JSON")
    gate.add_argument("--require", action="store_true", help="exit non-zero when the gate does not pass")
    gate.set_defaults(func=cmd_gate)

    validate = sub.add_parser("validate-plan", help="validate a plan JSON file")
    add_common(validate)
    validate.add_argument("--plan", required=True)
    validate.add_argument("--schema", default=None, help="JSON Schema file (defaults to none)")
    validate.set_defaults(func=cmd_validate_plan)

    ledger = sub.add_parser("ledger", help="export the requirement ledger")
    add_common(ledger)
    ledger.add_argument("--plan", required=True)
    ledger.add_argument("--ledger-format", choices=["csv", "markdown"], default="csv")
    ledger.set_defaults(func=cmd_ledger)

    files = sub.add_parser("files", help="inventory a workspace and detect missing files")
    add_common(files)
    files.add_argument("--root", required=True)
    files.add_argument("--declared", action="append", default=[], help="file the request names (repeatable)")
    files.add_argument("--required", action="append", default=[], help="subset of declared files that are required (repeatable)")
    files.set_defaults(func=cmd_files)

    checkpoint = sub.add_parser("checkpoint", help="checkpoint and continue control")
    add_common(checkpoint)
    checkpoint.add_argument("action", choices=["start", "status", "continue", "block", "reopen", "set-count"])
    checkpoint.add_argument("--state", dest="_state", default=None, help="checkpoint state JSON path")
    checkpoint.add_argument("--plan", default=None, help="plan JSON for 'start'")
    checkpoint.add_argument("--root", default=None, help="workspace root for output checks")
    checkpoint.add_argument("--reply", default=None, help="user reply to classify (continue/stop/scope change)")
    checkpoint.add_argument("--verified", action="append", default=[], help="what was verified (repeatable)")
    checkpoint.add_argument("--output", action="append", default=[], help="produced artifact path (repeatable)")
    checkpoint.add_argument("--note", default=None)
    checkpoint.add_argument("--reason", default=None)
    checkpoint.add_argument("--stage", default=None)
    checkpoint.add_argument("--count", type=int, default=None)
    checkpoint.add_argument("--intents", default=None, help="continue lexicon override JSON")
    checkpoint.add_argument("--dry-run", action="store_true")
    checkpoint.add_argument("--force", action="store_true")
    checkpoint.set_defaults(func=cmd_checkpoint)

    recover = sub.add_parser("recover", help="bounded repair of a failed stage")
    add_common(recover)
    recover.add_argument("--state", required=True)
    recover.add_argument("--stage", required=True)
    recover.add_argument("--cause", required=True)
    recover.add_argument("--action", choices=["retry", "reopen", "rollback"], default="retry")
    recover.set_defaults(func=cmd_recover)

    verify = sub.add_parser("verify", help="run verification gates")
    add_common(verify)
    verify.add_argument("--plan", required=True)
    verify.add_argument("--state", default=None)
    verify.add_argument("--root", required=True)
    verify.add_argument("--allow-missing-artifacts", action="store_true")
    verify.set_defaults(func=cmd_verify)

    assert_cmd = sub.add_parser("assert", help="assert artifact state")
    add_common(assert_cmd)
    assert_cmd.add_argument("--kind", required=True, choices=["file", "csv", "json", "manifest", "reference"])
    assert_cmd.add_argument("--path", required=True)
    assert_cmd.add_argument("--root", default=None)
    assert_cmd.add_argument("--min-bytes", type=int, default=0)
    assert_cmd.add_argument("--signature", action="store_true")
    assert_cmd.add_argument("--expected-ext", default=None)
    assert_cmd.add_argument("--require-header", action="append", default=[])
    assert_cmd.add_argument("--min-rows", type=int, default=1)
    assert_cmd.add_argument("--expect-rows", type=int, default=None)
    assert_cmd.add_argument("--schema", default=None)
    assert_cmd.add_argument("--require-key", action="append", default=[])
    assert_cmd.set_defaults(func=cmd_assert)

    watch = sub.add_parser("watch", help="wait for workspace writes to settle")
    add_common(watch)
    watch.add_argument("--root", required=True)
    watch.add_argument("--settle", type=float, default=1.0)
    watch.add_argument("--timeout", type=float, default=30.0)
    watch.add_argument("--poll", type=float, default=0.25)
    watch.set_defaults(func=cmd_watch)

    cleanup = sub.add_parser("cleanup", help="propose or apply temp-file cleanup")
    add_common(cleanup)
    cleanup.add_argument("--root", required=True)
    cleanup.add_argument("--pattern", action="append", default=[])
    cleanup.add_argument("--approve", default=None, help="approval token; required to delete anything")
    cleanup.add_argument("--backup", default=None, help="move candidates here instead of deleting")
    cleanup.set_defaults(func=cmd_cleanup)

    demo = sub.add_parser("demo", help="end-to-end rehearsal in a temporary workspace")
    add_common(demo)
    demo.add_argument("--workdir", default=None, help="keep fixtures in this directory")
    demo.add_argument("--stages", type=int, default=4)
    demo.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    demo.set_defaults(func=cmd_demo)

    help_cmd = sub.add_parser("help", help="show command help")
    add_common(help_cmd)
    help_cmd.set_defaults(func=lambda args: _print_help(args, parser))
    return parser


def _print_help(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    text = parser.format_help() + "\n" + COMMANDS_HELP
    emit("help", {"help": text}, fmt=args.format, out=args.out, text_lines=text.split("\n"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except (ValueError, OSError):  # pragma: no cover - platform guard
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except ToolError as exc:
        print(json.dumps(exc.as_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    if not getattr(args, "command", None):
        text = parser.format_help() + "\n" + COMMANDS_HELP
        print(text)
        return EXIT_OK
    try:
        return int(args.func(args))
    except ToolError as exc:
        print(json.dumps(exc.as_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "interrupted",
                    "message": "Interrupted by the operator; completed work is preserved in any saved state file",
                    "exit_code": EXIT_INTERRUPTED,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED
    except BrokenPipeError:  # pragma: no cover - piping guard
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - final guard against unhandled faults
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "internal-error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "exit_code": 1,
                    "hint": "Re-run with --format json and report the command if this persists.",
                },
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
