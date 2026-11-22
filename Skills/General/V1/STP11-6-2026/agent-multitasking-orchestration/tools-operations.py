#!/usr/bin/env python3
"""Capability handlers, approval gates, and local deterministic analyzers.

Every catalog capability has a distinct registered handler closure. Handlers
share reusable context analysis and 25 category engines so overlapping concepts
remain available without duplicating orchestration logic. No hidden chain of
thought, shell, arbitrary code, network, or external tool execution is used.
"""

from __future__ import annotations

import re
import secrets
import sys as _sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import import_module as _import_module
from typing import Any, Callable

_sys.modules.setdefault("tools_common", _import_module("tools-common"))
from tools_common import (
    EXIT_APPROVAL,
    EXIT_CANCELLED,
    EXIT_OK,
    EXIT_RUNTIME,
    ToolError,
    canonical_json,
    clip,
    config_path,
    current_paths,
    deadline,
    load_json,
    load_schema,
    now_iso,
    require_schema,
    safe_path,
    sha256_text,
)


class TransientHandlerError(Exception):
    """A bounded, retryable condition raised only by pure local handlers."""


@dataclass(frozen=True)
class ContextFacts:
    goal: str
    tasks: list[dict[str, Any]]
    workers: list[dict[str, Any]]
    constraints: list[str]
    acceptance_criteria: list[str]
    dependency_edges: list[tuple[str, str]]
    layers: list[list[str]]
    cycle_detected: bool
    risk_counts: dict[str, int]
    role_names: list[str]
    context_fingerprint: str


@dataclass(frozen=True)
class ToolOutcome:
    payload: dict[str, Any]
    exit_code: int = EXIT_OK


def parse_iso8601(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(
            "INVALID_APPROVAL_TIMESTAMP",
            "Approval timestamps must be ISO 8601 values.",
            details={"value": value, "reason": str(exc)},
            recovery="Use a UTC timestamp such as 2030-01-01T00:00:00Z.",
            exit_code=EXIT_APPROVAL,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_expiry(hours: int = 24) -> str:
    value = datetime.now(timezone.utc) + timedelta(hours=hours)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def pending_packet(profile: dict[str, Any], policy: dict[str, Any], *, expires_at: str | None = None) -> dict[str, Any]:
    approval = policy.get("approval", {})
    return {
        "schema_version": "1.0",
        "status": approval.get("pending_packet_status", "pending"),
        "packet_type": "human-approval-request",
        "capability": {
            "slug": profile["slug"],
            "name": profile["name"],
            "risk": profile["risk"],
            "description": profile["description"],
        },
        "expected_action": "capability.execute",
        "expected_scope": profile["approval_scope"],
        "required_fields": approval.get("required_fields", []),
        "required_status": "approved",
        "expires_at": expires_at or default_expiry(int(approval.get("default_validity_hours", 24))),
        "nonce": secrets.token_hex(8),
        "instructions": "A human or trusted approval authority must supply every required field. This pending packet is not approval.",
        "side_effects_before_approval": "none",
    }


def approved_record(
    profile: dict[str, Any],
    *,
    approved_by: str,
    reason: str,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "approved",
        "action": "capability.execute",
        "scope": profile["approval_scope"],
        "approved_by": approved_by,
        "reason": reason,
        "issued_at": now_iso(),
        "expires_at": expires_at or default_expiry(),
        "nonce": secrets.token_hex(8),
    }


def verify_approval_record(
    approval_file: str,
    *,
    expected_action: str,
    expected_scope: str,
    paths: Any,
    schemas: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    path = paths.safe(approval_file, must_exist=True)
    record = load_json(path, label="Approval record")
    require_schema(record, schemas["approval"], label="Approval record")
    now = now or datetime.now(timezone.utc)
    issued = parse_iso8601(record["issued_at"])
    expires = parse_iso8601(record["expires_at"])
    problems: list[str] = []
    if record["action"] != expected_action:
        problems.append(f"action must be {expected_action}")
    if record["scope"] != expected_scope:
        problems.append(f"scope must be {expected_scope}")
    if issued > now:
        problems.append("issued_at is in the future")
    if expires <= now:
        problems.append("approval has expired")
    if not record["approved_by"].strip():
        problems.append("approved_by is empty")
    if not record["reason"].strip():
        problems.append("reason is empty")
    if problems:
        raise ToolError(
            "APPROVAL_INVALID",
            "The approval record does not authorize this action.",
            details={"problems": problems, "path": approval_file},
            recovery="Use approval request to create a pending packet, then have a human complete and sign an approved record.",
            exit_code=EXIT_APPROVAL,
        )
    return {
        "valid": True,
        "path": approval_file,
        "action": record["action"],
        "scope": record["scope"],
        "approved_by": record["approved_by"],
        "issued_at": record["issued_at"],
        "expires_at": record["expires_at"],
        "seconds_remaining": max(0, int((expires - now).total_seconds())),
        "authentication": "not-provided-by-local-tool",
    }


def _topological_layers(tasks: list[dict[str, Any]]) -> tuple[list[list[str]], list[tuple[str, str]], bool]:
    ids = [str(task.get("task_id", f"task-{index + 1}")) for index, task in enumerate(tasks)]
    id_set = set(ids)
    edges: list[tuple[str, str]] = []
    indegree = {task_id: 0 for task_id in ids}
    children: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for task, task_id in zip(tasks, ids):
        for dependency in task.get("depends_on", []) if isinstance(task.get("depends_on", []), list) else []:
            dependency_id = str(dependency)
            if dependency_id in id_set:
                edges.append((dependency_id, task_id))
                indegree[task_id] += 1
                children[dependency_id].append(task_id)
    remaining = {task_id: count for task_id, count in indegree.items() if count == 0}
    layers: list[list[str]] = []
    processed = 0
    while remaining:
        layer = sorted(remaining)
        layers.append(layer)
        processed += len(layer)
        next_remaining: dict[str, int] = {}
        for task_id in layer:
            for child in children.get(task_id, []):
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_remaining[child] = 0
        remaining = next_remaining
    return layers, edges, processed != len(ids)


def build_facts(context: dict[str, Any], blueprints: dict[str, Any]) -> ContextFacts:
    if not isinstance(context, dict):
        raise ToolError("CONTEXT_NOT_OBJECT", "Capability context must be a JSON object.")
    tasks = context.get("tasks", [])
    workers = context.get("workers", [])
    constraints = context.get("constraints", [])
    acceptance = context.get("acceptance_criteria", [])
    if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
        raise ToolError("INVALID_TASKS", "context.tasks must be an array of objects.")
    if not isinstance(workers, list) or not all(isinstance(item, dict) for item in workers):
        raise ToolError("INVALID_WORKERS", "context.workers must be an array of objects.")
    if not isinstance(constraints, list) or not all(isinstance(item, str) for item in constraints):
        raise ToolError("INVALID_CONSTRAINTS", "context.constraints must be an array of strings.")
    if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
        raise ToolError("INVALID_ACCEPTANCE_CRITERIA", "context.acceptance_criteria must be an array of strings.")
    risks: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for task in tasks:
        risk = str(task.get("risk", "low"))
        if risk in risks:
            risks[risk] += 1
    layers, edges, cycle = _topological_layers(tasks)
    return ContextFacts(
        goal=str(context.get("goal", context.get("mission_id", "Unspecified local goal"))),
        tasks=tasks,
        workers=workers,
        constraints=[str(item) for item in constraints],
        acceptance_criteria=[str(item) for item in acceptance],
        dependency_edges=edges,
        layers=layers,
        cycle_detected=cycle,
        risk_counts=risks,
        role_names=sorted(blueprints.get("roles", {}).keys()),
        context_fingerprint=sha256_text(canonical_json(context)),
    )


def _task_names(facts: ContextFacts, maximum: int = 8) -> list[str]:
    return [str(task.get("name", task.get("task_id", "unnamed"))) for task in facts.tasks[:maximum]]


def _engine_core_instance_and_worker_orchestration(facts, profile, config, policy, prompts, blueprints):
    lifecycle = ["proposed", "ready", "running", "reviewing", "completed"]
    return {
        "engine": "instance-worker-orchestration",
        "capability_mode": profile["slug"],
        "instance_states": lifecycle,
        "instance_count": min(len(facts.tasks), 8),
        "worker_count": len(facts.workers),
        "role_templates": len(facts.role_names),
        "assignment_candidates": _task_names(facts, 4),
        "isolation_boundary": "task-local-unless-explicit-handoff",
        "resource_limit": config["limits"]["max_workers"],
    }


def _engine_task_decomposition_and_planning(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "decomposition-planning",
        "capability_mode": profile["slug"],
        "work_breakdown": _task_names(facts, 12),
        "dependency_edges": [{"from": a, "to": b} for a, b in facts.dependency_edges[:24]],
        "execution_layers": facts.layers,
        "milestones": [f"checkpoint-{index + 1}" for index in range(len(facts.layers))],
        "assumptions": facts.constraints,
        "definition_of_done": facts.acceptance_criteria,
        "cycle_detected": facts.cycle_detected,
    }


def _engine_parallel_execution_and_concurrency(facts, profile, config, policy, prompts, blueprints):
    independent = [task for task in facts.tasks if not task.get("depends_on")]
    return {
        "engine": "parallel-execution",
        "capability_mode": profile["slug"],
        "parallel_groups": facts.layers,
        "concurrency_budget": min(config["runtime"]["concurrency"], config["limits"]["max_workers"]),
        "independent_tasks": len(independent),
        "shared_constraints": facts.constraints[:8],
        "merge_boundaries": [f"merge-{index + 1}" for index in range(len(facts.layers))],
        "conflict_risk": facts.risk_counts["high"] + facts.risk_counts["critical"],
        "max_tasks": config["limits"]["max_tasks"],
    }


def _engine_reasoning_lenses_and_thought_operations(facts, profile, config, policy, prompts, blueprints):
    lens = prompts.get("lenses", {}).get(profile["slug"], {
        "instruction": "Produce a structured conclusion from the supplied local context.",
        "output_contract": ["conclusion", "checks", "uncertainty"],
    })
    return {
        "engine": "reasoning-lens",
        "capability_mode": profile["slug"],
        "lens": lens.get("instruction"),
        "input_contract": lens.get("input_contract", []),
        "conclusion_fields": lens.get("output_contract", []),
        "structured_conclusion": {
            field: f"derived-from-{len(facts.tasks)}-local-tasks"
            for field in lens.get("output_contract", ["conclusion"])
        },
        "minority_evidence": facts.constraints[:3],
        "hidden_chain_of_thought_emitted": False,
        "provider": config["provider"]["name"],
        "model": config["provider"]["model"],
    }


def _engine_task_routing_and_scheduling(facts, profile, config, policy, prompts, blueprints):
    queue = sorted(
        facts.tasks,
        key=lambda task: (-int(task.get("priority", 0) if isinstance(task.get("priority", 0), int) else 0), str(task.get("task_id", ""))),
    )
    priorities = [int(task.get("priority", 0)) for task in facts.tasks if isinstance(task.get("priority", 0), int)]
    return {
        "engine": "routing-scheduling",
        "capability_mode": profile["slug"],
        "priority_queue": [str(task.get("task_id", "task")) for task in queue[:16]],
        "average_priority": round(sum(priorities) / len(priorities), 3) if priorities else 0,
        "fairness_rotation": [worker.get("worker_id", "worker") for worker in facts.workers],
        "locality_groups": facts.layers,
        "risk_routes": facts.risk_counts,
        "deadline_seconds": min(config["runtime"]["max_runtime_seconds"], 300),
        "batching_limit": config["runtime"]["concurrency"],
    }


def _engine_communication_and_coordination(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "communication-coordination",
        "capability_mode": profile["slug"],
        "briefs": [{"task": name, "output": "structured-json", "deadline": "bounded"} for name in _task_names(facts, 5)],
        "status_fields": ["completed", "next_action", "blockers", "evidence", "forecast"],
        "recipients": [worker.get("worker_id", "worker") for worker in facts.workers],
        "handoff_packet_fields": ["artifacts", "decisions", "assumptions", "open_issues", "provenance", "next_steps"],
        "canonical_terms": ["task_id", "worker_id", "risk", "priority", "acceptance_criteria"],
        "communication_load": min(len(facts.tasks) * len(facts.workers), 128),
    }


def _engine_context_and_memory_management(facts, profile, config, policy, prompts, blueprints):
    context_size = len(canonical_json(facts.tasks)) + len(facts.goal)
    return {
        "engine": "context-memory",
        "capability_mode": profile["slug"],
        "context_bundle_chars": context_size,
        "memory_tiers": ["working", "checkpoint", "long-term-local"],
        "relevance_terms": profile["focus_terms"][:5],
        "checkpoint_count": len(facts.layers),
        "firewall": "explicit-validated-handoff-only",
        "freshness": "per-invocation",
        "fingerprint": facts.context_fingerprint[:16],
    }


def _engine_collaboration_and_team_structures(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "team-structures",
        "capability_mode": profile["slug"],
        "roles": facts.role_names,
        "team_size": len(facts.workers),
        "pairings": [[facts.workers[index].get("worker_id", f"worker-{index}"), facts.workers[index + 1].get("worker_id", f"worker-{index + 1}")] for index in range(0, max(0, len(facts.workers) - 1), 2)],
        "review_circles": min(3, len(facts.layers)),
        "decision_rights": profile["risk"],
        "ephemeral_by_default": blueprints.get("defaults", {}).get("ephemeral", True),
    }


def _engine_verification_review_and_quality(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "verification-quality",
        "capability_mode": profile["slug"],
        "traceability_matrix": [{"task": name, "criteria": facts.acceptance_criteria[:2]} for name in _task_names(facts, 6)],
        "quality_gates": ["schema-valid", "dependencies-acyclic", "acceptance-tested", "evidence-present"],
        "coverage": round(len(facts.acceptance_criteria) / max(1, len(facts.tasks)), 3),
        "exceptions": ["cycle-detected"] if facts.cycle_detected else [],
        "independent_review": True,
    }


def _engine_conflict_detection_and_resolution(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "conflict-resolution",
        "capability_mode": profile["slug"],
        "output_pairs": min(len(facts.layers), 6),
        "assumption_clashes": facts.constraints[:4],
        "priority_conflicts": max(0, len(facts.tasks) - len(set(str(task.get("priority", "")) for task in facts.tasks))),
        "resource_contention": max(0, len(facts.tasks) - len(facts.workers)),
        "consensus_threshold": "majority-with-minority-evidence",
        "merge_boundary": "explicit-non-overlapping-sections",
    }


def _engine_failure_recovery_and_resilience(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "failure-recovery",
        "capability_mode": profile["slug"],
        "retry_budget": config["runtime"]["retries"],
        "checkpoint_count": len(facts.layers),
        "fallback_mode": "local-deterministic-partial-result",
        "recovery_state": "atomic-json-checkpoint",
        "rollback_requires_approval": True,
        "transient_failure_budget": sum(int(task.get("transient_failures", 0)) for task in facts.tasks if isinstance(task.get("transient_failures", 0), int)),
    }


def _engine_resource_cost_and_performance(facts, profile, config, policy, prompts, blueprints):
    estimated = sum(float(task.get("estimated_seconds", 0)) for task in facts.tasks if isinstance(task.get("estimated_seconds", 0), (int, float)))
    return {
        "engine": "resource-performance",
        "capability_mode": profile["slug"],
        "estimated_work_units": round(estimated, 6),
        "concurrency": config["runtime"]["concurrency"],
        "estimated_wall_seconds": round(estimated / max(1, config["runtime"]["concurrency"]), 6),
        "task_timeout_seconds": config["runtime"]["task_timeout_seconds"],
        "max_runtime_seconds": config["runtime"]["max_runtime_seconds"],
        "cost_units": len(facts.tasks) * len(facts.workers),
        "local_cost_currency": 0,
    }


def _engine_safety_permissions_and_governance(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "safety-governance",
        "capability_mode": profile["slug"],
        "risk_counts": facts.risk_counts,
        "approval_required_risks": policy.get("approval", {}).get("required_risks", []),
        "denied_actions": ["shell", "network", "arbitrary-code", "external-tool"],
        "audit_events": ["validation", "approval-check", "handler-selection", "result-schema-check"],
        "separation_of_duties": "reviewer-distinct-from-producer",
        "policy_version": policy.get("schema_version"),
    }


def _engine_research_and_evidence_operations(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "research-evidence",
        "capability_mode": profile["slug"],
        "research_questions": _task_names(facts, 5),
        "source_classes": ["local-input", "structured-output", "checkpoint-state"],
        "claim_evidence_links": len(facts.dependency_edges),
        "contradictions": ["cycle-detected"] if facts.cycle_detected else [],
        "evidence_gaps": [criterion for criterion in facts.acceptance_criteria if "evidence" in criterion.lower()][:5],
        "citation_checks": "local-provenance-only",
    }


def _engine_data_and_analytics_workflows(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "data-analytics",
        "capability_mode": profile["slug"],
        "pipeline_stages": ["ingest", "normalize", "transform", "validate", "aggregate", "narrate"],
        "schema_fields": sorted({key for task in facts.tasks for key in task.get("payload", {}) if isinstance(task.get("payload", {}), dict)})[:16],
        "transformations": [str(task.get("operation", "")) for task in facts.tasks if task.get("operation")],
        "quality_checks": len(facts.acceptance_criteria),
        "provenance_chain": facts.context_fingerprint[:16],
    }


def _engine_software_and_engineering_coordination(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "software-engineering",
        "capability_mode": profile["slug"],
        "work_items": _task_names(facts, 8),
        "ownership": {worker.get("worker_id", "worker"): worker.get("role", "unknown") for worker in facts.workers[:8]},
        "interface_contracts": ["structured-output", "dependency-results", "acceptance-criteria"],
        "test_matrix": facts.acceptance_criteria,
        "release_gates": ["no-open-blockers", "schema-valid", "approval-if-sensitive"],
        "external_repository_access": False,
    }


def _engine_document_and_knowledge_production(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "document-knowledge",
        "capability_mode": profile["slug"],
        "sections": _task_names(facts, 8),
        "outline": [f"section-{index + 1}" for index in range(len(facts.layers))],
        "style_rules": ["concise", "structured", "case-sensitive-references"],
        "fact_checks": len(facts.acceptance_criteria),
        "cross_references": len(facts.dependency_edges),
        "publication_requires_approval": True,
    }


def _engine_media_and_creative_production_coordination(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "media-creative",
        "capability_mode": profile["slug"],
        "creative_brief": clip(facts.goal, 160),
        "asset_schedule": _task_names(facts, 8),
        "variants": min(4, len(facts.layers)),
        "consistency_checks": facts.acceptance_criteria[:5],
        "rights_checks": "local-metadata-only",
        "render_queue": config["runtime"]["concurrency"],
        "delivery_preflight": "no-external-publishing",
    }


def _engine_workflow_automation_and_tool_use(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "bounded-automation-plan",
        "capability_mode": profile["slug"],
        "mode": "plan-only-dry-run",
        "allowed_local_operations": config["safety"]["allowed_task_operations"],
        "connector_calls": 0,
        "external_tool_calls": 0,
        "idempotency_key": sha256_text(f"{profile['slug']}:{facts.context_fingerprint}")[:24],
        "rate_limit": config["runtime"]["concurrency"],
        "dry_run_required": True,
    }


def _engine_decision_support_and_strategy(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "decision-support",
        "capability_mode": profile["slug"],
        "decision_frame": clip(facts.goal, 180),
        "criteria": facts.acceptance_criteria,
        "scenarios": [f"layer-{index + 1}" for index in range(len(facts.layers))],
        "options": _task_names(facts, 5),
        "tradeoffs": facts.risk_counts,
        "reversibility": "local-json-only" if profile["risk"] == "low" else "approval-gated",
        "decision_record": f"decision-{facts.context_fingerprint[:12]}",
    }


def _engine_learning_and_capability_improvement(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "learning-improvement",
        "capability_mode": profile["slug"],
        "performance_metrics": {"tasks": len(facts.tasks), "workers": len(facts.workers), "dependency_edges": len(facts.dependency_edges)},
        "skill_gaps": [role for role in facts.role_names if not any(worker.get("role") == role for worker in facts.workers)],
        "practice_tasks": _task_names(facts, 4),
        "feedback": facts.acceptance_criteria[:4],
        "benchmark_count": len(facts.layers),
        "certification": "schema-and-handler-coverage",
    }


def _engine_human_collaboration_and_oversight(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "human-oversight",
        "capability_mode": profile["slug"],
        "approval_packet_fields": policy.get("approval", {}).get("required_fields", []),
        "reviewer_load": {worker.get("worker_id", "worker"): min(2, len(facts.tasks)) for worker in facts.workers[:6]},
        "expert_routes": facts.role_names,
        "sign_off": "explicit-human-record",
        "override_protection": "scope-and-expiry-checked",
        "no_auto_approval": policy.get("approval", {}).get("no_auto_approval", True),
    }


def _engine_monitoring_observability_and_control(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "monitoring-control",
        "capability_mode": profile["slug"],
        "dashboard_metrics": {"tasks": len(facts.tasks), "layers": len(facts.layers), "workers": len(facts.workers)},
        "heartbeat": "cooperative-cancellation-check",
        "service_level_seconds": config["runtime"]["max_runtime_seconds"],
        "alerts": ["deadline", "cycle", "approval-missing", "schema-failure"],
        "progress_evidence": _task_names(facts, 5),
        "event_correlation": facts.context_fingerprint[:12],
    }


def _engine_scale_distribution_and_federation(facts, profile, config, policy, prompts, blueprints):
    shards = max(1, min(config["runtime"]["concurrency"], len(facts.layers)))
    return {
        "engine": "scale-federation",
        "capability_mode": profile["slug"],
        "hierarchy_depth": len(facts.layers),
        "federated_workers": len(facts.workers),
        "shards": [f"shard-{index + 1}" for index in range(shards)],
        "consensus": "local-deterministic-quorum",
        "replication": "checkpoint-only",
        "tenant_isolation": "separate-context-fingerprints",
        "partition_recovery": "resume-from-atomic-state",
    }


def _engine_advanced_adaptive_orchestration(facts, profile, config, policy, prompts, blueprints):
    return {
        "engine": "adaptive-orchestration",
        "capability_mode": profile["slug"],
        "recomposition_triggers": ["risk-change", "deadline-pressure", "failed-check", "new-constraint"],
        "intent": clip(facts.goal, 160),
        "uncertainty": facts.constraints[:4],
        "anytime_solution": "best-completed-layer",
        "review_intensity": profile["risk"],
        "bottlenecks": max(0, len(facts.tasks) - len(facts.workers)),
        "mission_evolution": "versioned-local-plan",
    }


CATEGORY_ENGINES: dict[str, Callable[..., dict[str, Any]]] = {
    "core-instance-and-worker-orchestration": _engine_core_instance_and_worker_orchestration,
    "task-decomposition-and-planning": _engine_task_decomposition_and_planning,
    "parallel-execution-and-concurrency": _engine_parallel_execution_and_concurrency,
    "reasoning-lenses-and-thought-operations": _engine_reasoning_lenses_and_thought_operations,
    "task-routing-and-scheduling": _engine_task_routing_and_scheduling,
    "communication-and-coordination": _engine_communication_and_coordination,
    "context-and-memory-management": _engine_context_and_memory_management,
    "collaboration-and-team-structures": _engine_collaboration_and_team_structures,
    "verification-review-and-quality": _engine_verification_review_and_quality,
    "conflict-detection-and-resolution": _engine_conflict_detection_and_resolution,
    "failure-recovery-and-resilience": _engine_failure_recovery_and_resilience,
    "resource-cost-and-performance": _engine_resource_cost_and_performance,
    "safety-permissions-and-governance": _engine_safety_permissions_and_governance,
    "research-and-evidence-operations": _engine_research_and_evidence_operations,
    "data-and-analytics-workflows": _engine_data_and_analytics_workflows,
    "software-and-engineering-coordination": _engine_software_and_engineering_coordination,
    "document-and-knowledge-production": _engine_document_and_knowledge_production,
    "media-and-creative-production-coordination": _engine_media_and_creative_production_coordination,
    "workflow-automation-and-tool-use": _engine_workflow_automation_and_tool_use,
    "decision-support-and-strategy": _engine_decision_support_and_strategy,
    "learning-and-capability-improvement": _engine_learning_and_capability_improvement,
    "human-collaboration-and-oversight": _engine_human_collaboration_and_oversight,
    "monitoring-observability-and-control": _engine_monitoring_observability_and_control,
    "scale-distribution-and-federation": _engine_scale_distribution_and_federation,
    "advanced-adaptive-orchestration": _engine_advanced_adaptive_orchestration,
}


class CapabilityRuntime:
    def __init__(self, config: dict[str, Any], registry: Any, *, paths: Any = None) -> None:
        self.config = config
        self.registry = registry
        self.paths = paths or current_paths()
        self.policy = registry.policy
        self.prompts = load_json(config_path(config, "prompts", paths=self.paths), label="Reasoning lenses")
        self.blueprints = load_json(config_path(config, "role_blueprints", paths=self.paths), label="Role blueprints")
        self.schemas = load_json(config_path(config, "schemas", paths=self.paths), label="Schema bundle")
        self.handlers = self._build_handlers()
        self.check_runtime_constraints()

    def check_runtime_constraints(self) -> None:
        provider = self.config["provider"]
        if provider["name"] != "local-deterministic":
            raise ToolError(
                "UNSUPPORTED_PROVIDER",
                "Only the local-deterministic provider is implemented.",
                details={"requested": provider["name"]},
                recovery="Set provider.name to local-deterministic or extend the tool with a validated adapter.",
                exit_code=EXIT_RUNTIME,
            )
        if provider["network_access"] or provider["max_external_calls"] != 0 or provider["endpoint"]:
            raise ToolError(
                "EXTERNAL_PROVIDER_DISABLED",
                "Network endpoints and external model calls are not implemented.",
                details={"provider": provider["name"]},
                recovery="Clear provider.endpoint and keep network_access=false, max_external_calls=0.",
                exit_code=EXIT_RUNTIME,
            )
        safety = self.config["safety"]
        if safety["allow_shell"] or safety["allow_network"] or safety["allow_arbitrary_code"]:
            raise ToolError(
                "UNSAFE_CONFIGURATION",
                "Shell, network, and arbitrary code execution are not implemented.",
                details={"safety": safety},
                recovery="Keep all three allow_* flags false.",
                exit_code=EXIT_RUNTIME,
            )
        if self.config["runtime"]["storage"] not in {"auto", "memory", "filesystem-json"}:
            raise ToolError(
                "UNSUPPORTED_STORAGE",
                "Only auto, memory, and filesystem-json storage are implemented.",
                exit_code=EXIT_RUNTIME,
            )

    def _build_handlers(self) -> dict[str, Callable[["CapabilityRuntime", dict[str, Any]], dict[str, Any]]]:
        handlers: dict[str, Callable[["CapabilityRuntime", dict[str, Any]], dict[str, Any]]] = {}

        def make(profile: dict[str, Any]):
            def handler(runtime: "CapabilityRuntime", context: dict[str, Any]) -> dict[str, Any]:
                return runtime._execute_profile(profile, context)
            handler.__name__ = "handle_" + profile["slug"].replace("-", "_")
            handler.__doc__ = f"Execute {profile['name']}: {profile['description']}"
            return handler

        for profile in self.registry.profiles["profiles"]:
            handlers[profile["handler"]] = make(profile)
        return handlers

    def default_context(self) -> dict[str, Any]:
        return load_json(config_path(self.config, "sample_mission", paths=self.paths), label="Sample mission")

    def load_context(self, input_path: str | None) -> dict[str, Any]:
        if not input_path:
            return self.default_context()
        path = self.paths.safe(input_path, must_exist=True)
        context = load_json(path, label="Capability context")
        if not isinstance(context, dict):
            raise ToolError("CONTEXT_NOT_OBJECT", "Capability context must be a JSON object.", details={"path": input_path})
        if len(canonical_json(context).encode("utf-8")) > self.config["limits"]["max_input_bytes"]:
            raise ToolError("CONTEXT_TOO_LARGE", "Capability context exceeds max_input_bytes.", details={"path": input_path})
        build_facts(context, self.blueprints)
        return context

    def _base_limits(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.config["provider"]["name"],
            "model": self.config["provider"]["model"],
            "timeout_seconds": min(profile.get("timeout_seconds", 2.0), self.config["runtime"]["task_timeout_seconds"]),
            "retries": min(profile.get("retries", 1), self.config["runtime"]["retries"]),
            "max_result_bytes": self.config["limits"]["max_result_bytes"],
            "network_access": False,
            "external_tool_calls": 0,
            "approval_required": profile["risk"] in self.policy.get("approval", {}).get("required_risks", []),
            "cleanup": "no-persistent-side-effects",
        }

    def _audit(self, profile: dict[str, Any], facts: ContextFacts, approval: dict[str, Any] | None, started: float) -> dict[str, Any]:
        event_seed = f"{now_iso()}:{profile['slug']}:{facts.context_fingerprint}:{time.monotonic() - started:.6f}"
        return {
            "event_id": sha256_text(event_seed)[:24],
            "timestamp": now_iso(),
            "handler": profile["handler"],
            "approval": approval or {"required": False, "status": "not-required"},
            "context_fingerprint": facts.context_fingerprint,
            "side_effects": ["stdout-or-configured-json-output"],
            "external_calls": 0,
            "private_reasoning_emitted": False,
        }

    def _generic_result(
        self,
        status: str,
        profile: dict[str, Any],
        facts: ContextFacts,
        summary: str,
        artifact: dict[str, Any],
        checks: list[dict[str, Any]],
        next_actions: list[str],
        approval: dict[str, Any] | None,
        started: float,
        exit_code: int,
    ) -> ToolOutcome:
        payload = {
            "schema_version": "1.0",
            "status": status,
            "command": "run-capability",
            "timestamp": now_iso(),
            "capability": {
                "id": profile["id"],
                "slug": profile["slug"],
                "name": profile["name"],
                "description": profile["description"],
                "risk": profile["risk"],
                "artifact_type": profile["artifact_type"],
            },
            "category": profile["category"],
            "handler": profile["handler"],
            "summary": summary,
            "artifact": artifact,
            "checks": checks,
            "next_actions": next_actions,
            "limits": self._base_limits(profile),
            "audit": self._audit(profile, facts, approval, started),
        }
        require_schema(payload, self.schemas["capability_result"], label="Capability result")
        return ToolOutcome(payload=payload, exit_code=exit_code)

    def _execute_profile(self, profile: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        facts = build_facts(context, self.blueprints)
        engine = CATEGORY_ENGINES.get(profile["category_slug"])
        if engine is None:
            raise ToolError(
                "CATEGORY_ENGINE_MISSING",
                f"No category engine for {profile['category_slug']}.",
                exit_code=EXIT_RUNTIME,
            )
        metrics = engine(facts, profile, self.config, self.policy, self.prompts, self.blueprints)
        focus = list(profile["focus_terms"])
        findings = [
            f"{profile['operation'].capitalize()} local orchestration evidence for {term}."
            for term in focus[:4]
        ] or [f"Applied the {profile['name']} contract to the supplied local context."]
        checks = [
            {"name": "context_is_object", "status": "pass", "evidence": facts.context_fingerprint[:16]},
            {"name": "handler_registered", "status": "pass", "evidence": profile["handler"]},
            {"name": "category_engine_resolved", "status": "pass", "evidence": metrics["engine"]},
            {"name": "structured_output_only", "status": "pass", "evidence": "no-private-reasoning-field"},
            {"name": "dependency_graph_acyclic", "status": "fail" if facts.cycle_detected else "pass", "evidence": f"{len(facts.dependency_edges)} edges"},
        ]
        artifact = {
            "type": profile["artifact_type"],
            "operation": profile["operation"],
            "responsibility": profile["description"],
            "input_summary": {
                "goal": clip(facts.goal, 180),
                "task_count": len(facts.tasks),
                "worker_count": len(facts.workers),
                "constraint_count": len(facts.constraints),
                "acceptance_criteria_count": len(facts.acceptance_criteria),
                "context_fingerprint": facts.context_fingerprint,
            },
            "focus": focus,
            "metrics": metrics,
            "findings": findings,
            "checks": checks,
            "recommendation": f"Use the {profile['name']} report for {clip(facts.goal, 100)}; escalate only unresolved failed checks.",
            "structured_conclusion_only": True,
        }
        next_actions = [
            f"Inspect artifact.type={profile['artifact_type']} and checks.",
            "Run mission for an end-to-end bounded orchestration when task execution is needed.",
            "Use approval request before rerunning any high or critical risk capability without a valid approval record.",
        ]
        result = self._generic_result(
            "ok",
            profile,
            facts,
            f"{profile['name']} executed locally with {len(facts.tasks)} tasks and {len(facts.workers)} workers.",
            artifact,
            checks,
            next_actions,
            None,
            started,
            EXIT_OK,
        )
        return result.payload

    def _blocked_result(
        self,
        profile: dict[str, Any],
        context: dict[str, Any],
        *,
        reason: str,
        status: str,
        approval: dict[str, Any] | None,
        exit_code: int,
    ) -> ToolOutcome:
        started = time.monotonic()
        facts = build_facts(context, self.blueprints)
        packet = pending_packet(profile, self.policy) if status == "approval_required" else None
        artifact = {
            "type": f"{profile['slug']}-blocked-report",
            "operation": "blocked",
            "responsibility": profile["description"],
            "input_summary": {"context_fingerprint": facts.context_fingerprint, "task_count": len(facts.tasks)},
            "focus": list(profile["focus_terms"]),
            "metrics": {"blocked_reason": reason, "side_effects": "none"},
            "findings": [reason],
            "checks": [
                {"name": "execution_gate", "status": "blocked", "evidence": status},
                {"name": "no_side_effects", "status": "pass", "evidence": "handler-not-invoked"},
            ],
            "recommendation": "Resolve the gate and rerun the exact capability.",
            "pending_approval_packet": packet,
        }
        checks = artifact["checks"]
        next_actions = (
            [
                "Have a human create an approved record from the pending packet.",
                "Pass --approval-file with the approved record.",
                "Rerun the same capability after verification.",
            ]
            if status == "approval_required"
            else ["Remove or replace the cancellation marker.", "Rerun the same capability."]
        )
        summary = f"{profile['name']} was not executed: {reason}."
        return self._generic_result(status, profile, facts, summary, artifact, checks, next_actions, approval, started, exit_code)

    def execute(
        self,
        capability_key: str,
        *,
        input_path: str | None = None,
        approval_file: str | None = None,
        cancel_file: str | None = None,
    ) -> ToolOutcome:
        profile = self.registry.resolve(capability_key)
        context = self.load_context(input_path)
        facts = build_facts(context, self.blueprints)
        if cancel_file and self.paths.safe(cancel_file).exists():
            return self._blocked_result(
                profile,
                context,
                reason=f"cancellation marker exists: {cancel_file}",
                status="cancelled",
                approval=None,
                exit_code=EXIT_CANCELLED,
            )
        approval_state: dict[str, Any] | None = None
        required_risks = set(self.policy.get("approval", {}).get("required_risks", []))
        if profile["risk"] in required_risks:
            if not approval_file:
                return self._blocked_result(
                    profile,
                    context,
                    reason=f"{profile['risk']} risk capability requires human approval",
                    status="approval_required",
                    approval={"required": True, "status": "missing"},
                    exit_code=EXIT_APPROVAL,
                )
            approval_state = verify_approval_record(
                approval_file,
                expected_action="capability.execute",
                expected_scope=profile["approval_scope"],
                paths=self.paths,
                schemas=self.schemas,
            )
            approval_state.update({"required": True, "status": "approved"})
        handler = self.handlers.get(profile["handler"])
        if handler is None:
            raise ToolError(
                "HANDLER_NOT_REGISTERED",
                f"No runtime handler for {profile['handler']}.",
                exit_code=EXIT_RUNTIME,
            )
        end = deadline(self._base_limits(profile)["timeout_seconds"])
        retries = int(self._base_limits(profile)["retries"])
        last_error: TransientHandlerError | None = None
        payload: dict[str, Any] | None = None
        for attempt in range(retries + 1):
            if time.monotonic() > end:
                raise ToolError(
                    "CAPABILITY_TIMEOUT",
                    "Capability exceeded its configured timeout.",
                    details={"capability": profile["slug"], "timeout_seconds": self._base_limits(profile)["timeout_seconds"]},
                    exit_code=EXIT_RUNTIME,
                )
            try:
                payload = handler(self, context)
                break
            except TransientHandlerError as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(min(self.config["runtime"]["retry_backoff_seconds"], 0.05))
        if payload is None:
            raise ToolError(
                "TRANSIENT_HANDLER_RETRIES_EXHAUSTED",
                "A transient handler condition exhausted the retry budget.",
                details={"capability": profile["slug"], "retries": retries, "last_error": str(last_error)},
                exit_code=EXIT_RUNTIME,
            )
        if approval_state:
            payload["audit"]["approval"] = approval_state
            payload["limits"]["approval_required"] = True
        payload["audit"]["context_fingerprint"] = facts.context_fingerprint
        require_schema(payload, self.schemas["capability_result"], label="Capability result")
        return ToolOutcome(payload=payload, exit_code=EXIT_OK)
