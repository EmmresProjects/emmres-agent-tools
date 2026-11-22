#!/usr/bin/env python3
"""Bounded local mission orchestration engine.

Executes only a fixed allowlist of pure task operations with configurable
concurrency, timeouts, retries, cooperative cancellation, atomic checkpoints,
resume/recovery, approval gates, verification, and ephemeral-state cleanup.
"""

from __future__ import annotations

import sys as _sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from importlib import import_module as _import_module
from typing import Any

_sys.modules.setdefault("tools_common", _import_module("tools-common"))
_operations_module = _import_module("tools-operations")
_sys.modules.setdefault("tools_operations", _operations_module)
from tools_common import (
    EXIT_APPROVAL,
    EXIT_CANCELLED,
    EXIT_OK,
    EXIT_RUNTIME,
    ToolError,
    bounded_sleep,
    canonical_json,
    clip,
    config_path,
    current_paths,
    deadline,
    load_json,
    now_iso,
    require_schema,
    sha256_text,
    write_json_atomic,
)
from tools_operations import ToolOutcome, pending_packet, verify_approval_record


class TransientTaskError(Exception):
    pass


class CancelledTaskError(Exception):
    pass


class TaskValidationError(Exception):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class LayerPlan:
    index: int
    task_ids: list[str]
    concurrent: bool


def _numbers(values: list[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TaskValidationError("Numeric payload contains a non-number.", {"value": value})
        result.append(float(value))
    return result


def _strings(values: list[Any]) -> list[str]:
    if not all(isinstance(value, str) for value in values):
        raise TaskValidationError("String payload contains a non-string.")
    return [value.strip() for value in values]


def bounded_operation(task: dict[str, Any], dependency_results: dict[str, Any]) -> dict[str, Any]:
    """Run one fixed, pure operation. No eval, exec, subprocess, or I/O."""
    payload = task.get("payload", {})
    if not isinstance(payload, dict):
        raise TaskValidationError("Task payload must be an object.")
    operation = task["operation"]
    upstream_ids = sorted(dependency_results.keys())
    if operation == "normalize":
        values = _strings(list(payload.get("values", [])))
        return {"normalized": [value.lower() for value in values], "count": len(values), "upstream": upstream_ids}
    if operation == "score":
        values = _numbers(list(payload.get("values", [])))
        return {
            "sum": round(sum(values), 6),
            "mean": round(sum(values) / len(values), 6) if values else 0,
            "count": len(values),
            "upstream": upstream_ids,
        }
    if operation == "compare":
        left, right = payload.get("left"), payload.get("right")
        values = _numbers([left, right])
        return {
            "left": values[0],
            "right": values[1],
            "difference": round(values[0] - values[1], 6),
            "winner": "left" if values[0] >= values[1] else "right",
            "upstream": upstream_ids,
        }
    if operation == "validate":
        checks = payload.get("checks", {})
        if not isinstance(checks, dict) or not all(isinstance(key, str) and isinstance(value, bool) for key, value in checks.items()):
            raise TaskValidationError("validate requires a checks object of boolean values.")
        failed = [key for key, value in checks.items() if not value]
        return {"passed": not failed, "failed": failed, "count": len(checks), "upstream": upstream_ids}
    if operation == "merge":
        parts = _strings(list(payload.get("parts", [])))
        return {"merged": " | ".join(parts), "parts": parts, "upstream": upstream_ids}
    if operation == "summarize":
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise TaskValidationError("summarize requires a string text field.")
        words = text.split()
        return {"summary": " ".join(words[:18]), "characters": len(text), "word_count": len(words), "upstream": upstream_ids}
    if operation == "aggregate":
        values = _numbers(list(payload.get("values", [])))
        upstream_numbers: list[float] = []
        for result in dependency_results.values():
            output = result.get("output", {}) if isinstance(result, dict) else {}
            for key in ("sum", "mean", "difference"):
                value = output.get(key) if isinstance(output, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    upstream_numbers.append(float(value))
        all_values = values + upstream_numbers
        return {
            "sum": round(sum(all_values), 6),
            "mean": round(sum(all_values) / len(all_values), 6) if all_values else 0,
            "input_count": len(values),
            "upstream_number_count": len(upstream_numbers),
            "upstream": upstream_ids,
        }
    raise TaskValidationError("Unsupported bounded operation.", {"operation": operation})


def validate_mission_document(
    mission: dict[str, Any],
    *,
    config: dict[str, Any],
    schemas: dict[str, Any],
) -> dict[str, Any]:
    require_schema(mission, schemas["mission"], label="Mission")
    limits = config["limits"]
    tasks = mission["tasks"]
    workers = mission.get("workers", [])
    if len(tasks) > limits["max_tasks"]:
        raise ToolError(
            "MISSION_TASK_LIMIT_EXCEEDED",
            "Mission exceeds max_tasks.",
            details={"tasks": len(tasks), "limit": limits["max_tasks"]},
        )
    if len(workers) > limits["max_workers"]:
        raise ToolError(
            "MISSION_WORKER_LIMIT_EXCEEDED",
            "Mission exceeds max_workers.",
            details={"workers": len(workers), "limit": limits["max_workers"]},
        )
    task_ids = [str(task["task_id"]) for task in tasks]
    duplicates = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
    if duplicates:
        raise ToolError("DUPLICATE_TASK_IDS", "Task ids must be unique.", details={"duplicates": duplicates})
    allowed_operations = set(config["safety"]["allowed_task_operations"])
    unsupported = sorted({str(task["operation"]) for task in tasks if task["operation"] not in allowed_operations})
    if unsupported:
        raise ToolError(
            "UNSUPPORTED_TASK_OPERATIONS",
            "Mission uses operations outside the fixed local allowlist.",
            details={"unsupported": unsupported, "allowed": sorted(allowed_operations)},
        )
    id_set = set(task_ids)
    missing_dependencies = sorted(
        {
            str(dependency)
            for task in tasks
            for dependency in task.get("depends_on", [])
            if str(dependency) not in id_set
        }
    )
    if missing_dependencies:
        raise ToolError(
            "MISSING_DEPENDENCIES",
            "One or more task dependencies do not exist.",
            details={"missing": missing_dependencies},
        )
    indegree = {task_id: 0 for task_id in task_ids}
    children: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for task in tasks:
        task_id = str(task["task_id"])
        for dependency in task.get("depends_on", []):
            dependency = str(dependency)
            indegree[task_id] += 1
            children[dependency].append(task_id)
    ready = [task_id for task_id, count in indegree.items() if count == 0]
    processed = 0
    layers: list[list[str]] = []
    while ready:
        layer = sorted(ready)
        layers.append(layer)
        processed += len(layer)
        next_ready: list[str] = []
        for task_id in layer:
            for child in children[task_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_ready.append(child)
        ready = next_ready
    cycle = processed != len(task_ids)
    if cycle:
        raise ToolError(
            "CYCLIC_DEPENDENCIES",
            "Mission dependencies contain a cycle.",
            details={"tasks": task_ids, "processed": processed},
        )
    return {
        "valid": True,
        "mission_id": mission["mission_id"],
        "tasks": len(tasks),
        "workers": len(workers),
        "dependency_layers": layers,
        "approval_required": any(
            task.get("requires_approval") is True or task.get("risk") in {"high", "critical"}
            for task in tasks
        ),
        "fingerprint": sha256_text(canonical_json(mission)),
    }


class MissionEngine:
    def __init__(self, config: dict[str, Any], *, paths: Any = None) -> None:
        self.config = config
        self.paths = paths or current_paths()
        self.schemas = load_json(config_path(config, "schemas", paths=paths), label="Schema bundle")
        self.policy = load_json(config_path(config, "policy", paths=paths), label="Safety policy")
        self.blueprints = load_json(config_path(config, "role_blueprints", paths=paths), label="Role blueprints")
        self._check_config()

    def _check_config(self) -> None:
        if self.config["provider"]["name"] != "local-deterministic":
            raise ToolError(
                "UNSUPPORTED_PROVIDER",
                "Mission execution only supports local-deterministic.",
                exit_code=EXIT_RUNTIME,
            )
        if self.config["provider"]["network_access"] or self.config["provider"]["max_external_calls"] != 0:
            raise ToolError(
                "NETWORK_DISABLED",
                "Mission execution cannot use network providers.",
                exit_code=EXIT_RUNTIME,
            )
        if any(self.config["safety"][key] for key in ("allow_shell", "allow_network", "allow_arbitrary_code")):
            raise ToolError(
                "UNSAFE_CONFIGURATION",
                "Shell, network, and arbitrary code execution are disabled.",
                exit_code=EXIT_RUNTIME,
            )

    def load_mission(self, input_path: str) -> dict[str, Any]:
        path = self.paths.safe(input_path, must_exist=True)
        mission = load_json(path, label="Mission")
        if not isinstance(mission, dict):
            raise ToolError("MISSION_NOT_OBJECT", "Mission input must be a JSON object.")
        validate_mission_document(mission, config=self.config, schemas=self.schemas)
        return mission

    def _storage_mode(self, state_path: str | None) -> str:
        configured = self.config["runtime"]["storage"]
        if configured == "filesystem-json" and not state_path:
            raise ToolError(
                "STATE_PATH_REQUIRED",
                "filesystem-json storage requires --state PATH.",
                recovery="Provide a root-relative --state path or set runtime.storage to auto.",
            )
        if configured == "memory":
            return "memory"
        return "filesystem-json" if state_path else "memory"

    def _new_state(self, mission: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "mission_id": mission["mission_id"],
            "status": "running",
            "completed": {},
            "attempts": {},
            "events": [],
            "config_fingerprint": sha256_text(canonical_json(self.config)),
            "mission_fingerprint": sha256_text(canonical_json(mission)),
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }

    def _load_state(self, state_path: str | None, mission: dict[str, Any], resume: bool) -> tuple[dict[str, Any], bool]:
        if not resume or not state_path:
            return self._new_state(mission), False
        path = self.paths.safe(state_path, must_exist=True)
        state = load_json(path, label="Checkpoint state")
        if not isinstance(state, dict) or "completed" not in state:
            raise ToolError("INVALID_CHECKPOINT", "Checkpoint state is not a valid state object.")
        if state.get("mission_id") != mission["mission_id"]:
            raise ToolError(
                "CHECKPOINT_MISSION_MISMATCH",
                "Checkpoint belongs to a different mission.",
                details={"state": state.get("mission_id"), "mission": mission["mission_id"]},
                recovery="Use the matching state path or omit --resume.",
            )
        if state.get("mission_fingerprint") and state.get("mission_fingerprint") != sha256_text(canonical_json(mission)):
            raise ToolError(
                "CHECKPOINT_INPUT_MISMATCH",
                "Mission input changed since the checkpoint was created.",
                recovery="Resume with the same input or start a new state path.",
            )
        state.setdefault("attempts", {})
        state.setdefault("events", [])
        state["status"] = "running"
        state["updated_at"] = now_iso()
        return state, True

    def _save_state(self, state: dict[str, Any], state_path: str | None, mode: str) -> None:
        state["updated_at"] = now_iso()
        if mode == "filesystem-json" and state_path:
            write_json_atomic(self.paths.safe(state_path), state)

    def _assign_worker(self, task: dict[str, Any], workers: list[dict[str, Any]], load: dict[str, int]) -> str:
        required = task.get("required_role")
        candidates = [worker for worker in workers if not required or worker.get("role") == required]
        if not candidates:
            candidates = workers
        if not candidates:
            return f"auto-{task['task_id']}"
        chosen = min(
            candidates,
            key=lambda worker: (load.get(worker.get("worker_id", "worker"), 0), worker.get("worker_id", "worker")),
        )
        worker_id = str(chosen.get("worker_id", "worker"))
        load[worker_id] = load.get(worker_id, 0) + 1
        return worker_id

    def _run_task(
        self,
        task: dict[str, Any],
        *,
        worker_id: str,
        dependency_results: dict[str, Any],
        attempts_start: int,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        max_retries = int(self.config["runtime"]["retries"])
        transient_failures = int(task.get("transient_failures", 0))
        attempts = attempts_start
        retries_used = 0
        for retry_index in range(max_retries + 1):
            if cancel_event.is_set():
                raise CancelledTaskError("cooperative cancellation")
            attempts += 1
            bounded_sleep(float(task.get("estimated_seconds", 0)), maximum=0.05)
            if cancel_event.is_set():
                raise CancelledTaskError("cooperative cancellation")
            try:
                if attempts <= transient_failures:
                    raise TransientTaskError(f"simulated transient failure {attempts}/{transient_failures}")
                output = bounded_operation(task, dependency_results)
                return {
                    "task_id": task["task_id"],
                    "name": task["name"],
                    "status": "ok",
                    "worker_id": worker_id,
                    "attempts": attempts,
                    "retries": retries_used,
                    "output": output,
                    "dependencies": sorted(dependency_results.keys()),
                }
            except TransientTaskError:
                if retry_index >= max_retries:
                    raise
                retries_used += 1
                bounded_sleep(self.config["runtime"]["retry_backoff_seconds"], maximum=0.05)
        raise RuntimeError("unreachable retry state")

    def _pending_packet(self, mission: dict[str, Any]) -> dict[str, Any]:
        profile = {
            "slug": "mission-execution",
            "name": f"Mission {mission['mission_id']}",
            "risk": "critical",
            "description": clip(mission["goal"], 180),
            "approval_scope": f"mission:{mission['mission_id']}",
        }
        packet = pending_packet(profile, self.policy)
        packet["expected_action"] = "mission.execute"
        return packet

    def _result(
        self,
        *,
        status: str,
        mission: dict[str, Any],
        state: dict[str, Any],
        started: float,
        layers: list[LayerPlan],
        resumed: bool,
        state_path: str | None,
        storage_mode: str,
        reason: str,
        cleanup: dict[str, Any],
        approval: dict[str, Any] | None,
        exit_code: int,
        recovered_ids: set[str] | None = None,
    ) -> ToolOutcome:
        completed = state.get("completed", {})
        recovered_ids = recovered_ids or set()
        results = []
        for task_id in [str(task["task_id"]) for task in mission["tasks"]]:
            if task_id in completed:
                item = dict(completed[task_id])
                item["recovered"] = task_id in recovered_ids
                results.append(item)
        total = len(mission["tasks"])
        retries = sum(int(item.get("retries", 0)) for item in results if not item.get("recovered"))
        verification_checks = [
            {"name": "all_tasks_completed", "status": "pass" if len(results) == total else "fail", "evidence": f"{len(results)}/{total}"},
            {"name": "dependency_layers_valid", "status": "pass" if layers and all(layer.task_ids for layer in layers) else "fail", "evidence": f"{len(layers)} layers"},
            {"name": "retries_within_budget", "status": "pass" if retries <= int(self.config["runtime"]["retries"]) * total else "fail", "evidence": str(retries)},
            {"name": "no_external_calls", "status": "pass", "evidence": "0"},
            {"name": "structured_results", "status": "pass" if all(isinstance(item.get("output"), dict) for item in results) else "fail", "evidence": f"{len(results)} outputs"},
        ]
        payload = {
            "schema_version": "1.0",
            "status": status,
            "command": "mission",
            "timestamp": now_iso(),
            "mission_id": mission["mission_id"],
            "execution": {
                "reason": reason,
                "tasks_total": total,
                "tasks_completed": len(results),
                "tasks_recovered": sum(1 for item in results if item.get("recovered")),
                "tasks_retried": retries,
                "workers_used": sorted({str(item.get("worker_id")) for item in results}),
                "dependency_layers": len(layers),
                "concurrency": min(self.config["runtime"]["concurrency"], self.config["limits"]["max_workers"]),
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "storage": storage_mode,
            },
            "results": results,
            "verification": {
                "passed": status == "ok" and all(check["status"] == "pass" for check in verification_checks),
                "checks": verification_checks,
                "acceptance_criteria": mission.get("acceptance_criteria", []),
            },
            "recovery": {
                "resumed": resumed,
                "state_path": state_path,
                "checkpoint_status": state.get("status"),
                "completed_in_checkpoint": len(completed),
                "pending_approval_packet": None,
                "approval": approval,
            },
            "cleanup": cleanup,
            "events": state.get("events", [])[-100:],
        }
        if status == "approval_required":
            payload["recovery"]["pending_approval_packet"] = self._pending_packet(mission)
        require_schema(payload, self.schemas["mission_result"], label="Mission result")
        return ToolOutcome(payload=payload, exit_code=exit_code)

    def run(
        self,
        *,
        input_path: str,
        state_path: str | None = None,
        resume: bool = False,
        approval_file: str | None = None,
        cancel_file: str | None = None,
        max_runtime_seconds: float | None = None,
        concurrency: int | None = None,
    ) -> ToolOutcome:
        started = time.monotonic()
        mission = self.load_mission(input_path)
        validation = validate_mission_document(mission, config=self.config, schemas=self.schemas)
        storage_mode = self._storage_mode(state_path)
        state, resumed = self._load_state(state_path, mission, resume)
        recovered_task_ids = set(state.get("completed", {}).keys()) if resumed else set()
        layers = [LayerPlan(index + 1, layer, len(layer) > 1) for index, layer in enumerate(validation["dependency_layers"])]
        ephemeral_contexts = {str(task["task_id"]): {"scope": "task-local", "worker": None} for task in mission["tasks"]}
        cleanup = {"ephemeral_contexts_removed": 0, "temporary_files_removed": 0, "policy": "remove-on-exit"}
        cancel_event = threading.Event()
        cancel_path = self.paths.safe(cancel_file) if cancel_file else None
        if cancel_path and cancel_path.exists():
            cancel_event.set()
        end = deadline(max_runtime_seconds or self.config["runtime"]["max_runtime_seconds"])
        effective_concurrency = max(1, min(concurrency or self.config["runtime"]["concurrency"], self.config["limits"]["max_workers"]))

        if validation["approval_required"]:
            scope = f"mission:{mission['mission_id']}"
            if not approval_file:
                state["status"] = "approval_required"
                state["events"].append({"timestamp": now_iso(), "type": "approval_required", "scope": scope})
                self._save_state(state, state_path, storage_mode)
                ephemeral_contexts.clear()
                cleanup["ephemeral_contexts_removed"] = len(mission["tasks"])
                return self._result(
                    status="approval_required",
                    mission=mission,
                    state=state,
                    started=started,
                    layers=layers,
                    resumed=resumed,
                    state_path=state_path,
                    storage_mode=storage_mode,
                    reason="mission contains high-risk or explicit approval tasks",
                    cleanup=cleanup,
                    approval={"required": True, "status": "missing"},
                    exit_code=EXIT_APPROVAL,
                    recovered_ids=recovered_task_ids,
                )
            approval = verify_approval_record(
                approval_file,
                expected_action="mission.execute",
                expected_scope=scope,
                paths=self.paths,
                schemas=self.schemas,
            )
            approval.update({"required": True, "status": "approved"})
        else:
            approval = {"required": False, "status": "not-required"}

        if cancel_event.is_set():
            state["status"] = "cancelled"
            state["events"].append({"timestamp": now_iso(), "type": "cancelled", "reason": "marker-present-at-start"})
            self._save_state(state, state_path, storage_mode)
            ephemeral_contexts.clear()
            cleanup["ephemeral_contexts_removed"] = len(mission["tasks"])
            return self._result(
                status="cancelled",
                mission=mission,
                state=state,
                started=started,
                layers=layers,
                resumed=resumed,
                state_path=state_path,
                storage_mode=storage_mode,
                reason="cancellation marker present at start",
                cleanup=cleanup,
                approval=approval,
                exit_code=EXIT_CANCELLED,
                recovered_ids=recovered_task_ids,
            )

        watcher_stop = threading.Event()

        def watch_cancel_file() -> None:
            while not watcher_stop.wait(max(0.001, self.config["runtime"]["cancellation_poll_seconds"])):
                if cancel_path and cancel_path.exists():
                    cancel_event.set()
                    return
                if time.monotonic() > end:
                    cancel_event.set()
                    return

        watcher = threading.Thread(target=watch_cancel_file, name="cancel-file-watcher", daemon=True)
        watcher.start()
        workers = mission.get("workers", [])
        load: dict[str, int] = {}
        failure: dict[str, Any] | None = None
        status = "ok"
        reason = "all dependency layers completed"
        exit_code = EXIT_OK

        try:
            for layer in layers:
                if cancel_event.is_set() or time.monotonic() > end:
                    status, reason, exit_code = "cancelled", "deadline or cancellation marker", EXIT_CANCELLED
                    break
                pending = [task for task in mission["tasks"] if str(task["task_id"]) in layer.task_ids and str(task["task_id"]) not in state["completed"]]
                if not pending:
                    state["events"].append({"timestamp": now_iso(), "type": "layer_recovered", "layer": layer.index})
                    self._save_state(state, state_path, storage_mode)
                    continue
                with ThreadPoolExecutor(max_workers=min(effective_concurrency, len(pending))) as executor:
                    future_map = {}
                    for task in pending:
                        task_id = str(task["task_id"])
                        worker_id = self._assign_worker(task, workers, load)
                        ephemeral_contexts[task_id]["worker"] = worker_id
                        dependency_results = {
                            str(dependency): state["completed"][str(dependency)]
                            for dependency in task.get("depends_on", [])
                            if str(dependency) in state["completed"]
                        }
                        future = executor.submit(
                            self._run_task,
                            task,
                            worker_id=worker_id,
                            dependency_results=dependency_results,
                            attempts_start=int(state.get("attempts", {}).get(task_id, 0)),
                            cancel_event=cancel_event,
                        )
                        future_map[future] = task_id
                    remaining = set(future_map)
                    while remaining:
                        timeout_left = max(0.001, end - time.monotonic())
                        done, remaining = wait(remaining, timeout=min(timeout_left, self.config["runtime"]["task_timeout_seconds"]), return_when=FIRST_COMPLETED)
                        if not done:
                            if time.monotonic() > end or cancel_event.is_set():
                                status, reason, exit_code = "cancelled", "deadline or cancellation marker", EXIT_CANCELLED
                                for future in remaining:
                                    future.cancel()
                                break
                            continue
                        for future in done:
                            task_id = future_map[future]
                            try:
                                result = future.result()
                                result["recovered"] = False
                                state["completed"][task_id] = result
                                state["attempts"][task_id] = result["attempts"]
                                del ephemeral_contexts[task_id]
                                state["events"].append({"timestamp": now_iso(), "type": "task_completed", "task_id": task_id, "worker": result["worker_id"]})
                                self._save_state(state, state_path, storage_mode)
                            except CancelledTaskError:
                                cancel_event.set()
                                status, reason, exit_code = "cancelled", "cooperative task cancellation", EXIT_CANCELLED
                            except TransientTaskError as exc:
                                failure = {"type": "transient_retries_exhausted", "task_id": task_id, "message": str(exc)}
                                status, reason, exit_code = "error", "retry budget exhausted", EXIT_RUNTIME
                            except TaskValidationError as exc:
                                failure = {"type": "task_validation", "task_id": task_id, "message": str(exc), "details": exc.details}
                                status, reason, exit_code = "error", "task payload validation failed", EXIT_RUNTIME
                            except Exception as exc:
                                failure = {"type": "unexpected", "task_id": task_id, "message": type(exc).__name__}
                                status, reason, exit_code = "error", "unexpected bounded runtime failure", EXIT_RUNTIME
                        if status != "ok":
                            for future in remaining:
                                future.cancel()
                            break
                    for future in future_map:
                        if not future.done():
                            future.cancel()
                if status != "ok":
                    break
        finally:
            watcher_stop.set()
            watcher.join(timeout=1.0)
            cleanup["ephemeral_contexts_removed"] = len(mission["tasks"]) - len(ephemeral_contexts)
            ephemeral_contexts.clear()
            if status == "ok":
                state["status"] = "completed"
            elif status == "cancelled":
                state["status"] = "cancelled"
            else:
                state["status"] = "error"
            state["events"].append({"timestamp": now_iso(), "type": "run_finished", "status": status, "reason": reason})
            self._save_state(state, state_path, storage_mode)

        if failure:
            raise ToolError(
                "MISSION_EXECUTION_FAILED",
                "Mission stopped after a bounded task failure.",
                details=failure,
                recovery="Inspect the task payload, fix it, and rerun with --resume and the same state path.",
                exit_code=EXIT_RUNTIME,
            )
        return self._result(
            status=status,
            mission=mission,
            state=state,
            started=started,
            layers=layers,
            resumed=resumed,
            state_path=state_path,
            storage_mode=storage_mode,
            reason=reason,
            cleanup=cleanup,
            approval=approval,
            exit_code=exit_code,
            recovered_ids=recovered_task_ids,
        )
