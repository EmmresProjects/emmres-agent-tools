#!/usr/bin/env python3
"""tools-oaa-workflow.py -- durable workflow engine with approvals, retries and recovery.

Loaded by `tools-oaa.py`. Registers:

  workflow.validate  validate a workflow definition without executing anything
  workflow.run       execute a workflow with budgets, retries, checkpoints and audit
  workflow.resume    continue a run from its last validated checkpoint
  workflow.status    report one run
  workflow.list      list recent runs
  workflow.cancel    request cancellation of a running workflow (approval required)

Engine guarantees
-----------------
* Only registered operations can run. A workflow can never execute shell code,
  arbitrary Python, or an unregistered capability.
* Steps run in dependency order; cycles, unknown dependencies and unknown operations
  are rejected during validation.
* Every completed step is checkpointed. A resumed run replays the stored result of
  each completed step instead of re-running its side effects.
* Failed steps are retried for the failure classes named in the step's retry policy,
  using bounded exponential backoff with jitter.
* When a run fails or is cancelled, declared compensations run in reverse order, the
  failure is written to the dead-letter queue, and the run status is recorded.
* Steps may declare an approval gate. Without `--approve` the run pauses as
  `awaiting-approval` and resumes only after a human decision.
"""
from __future__ import annotations

import importlib.util as _ilu
import os
import signal
import sys as _sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel",
                                         os.path.join(_HERE, "tools-oaa-kernel.py"))
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)


def _load_sibling(name, filename):
    if name in _sys.modules:
        return _sys.modules[name]
    spec = _ilu.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    _sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load_sibling("oaa_guard", "tools-oaa-guard.py")

WORKFLOW_SCHEMA = K.WORKFLOW_SCHEMA_FILENAME
ALLOWED_RETRY_CLASSES = ("transient", "tool", "environment", "logic", "input",
                         "data", "state", "resource", "permission", "integrity")


class CancellationFlag:
    """File-based cancellation so a second process can stop a running workflow."""

    def __init__(self, path):
        self.path = path

    def is_set(self):
        return os.path.exists(self.path)

    def set(self):
        K.ensure_parent(self.path)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(K.now_iso() + "\n")

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)


class StepTimeout:
    """Best-effort hard timeout for a single step using SIGALRM on POSIX.

    On platforms without SIGALRM (Windows) the timeout is advisory: the engine still
    checks the remaining budget before and after every step.
    """

    def __init__(self, seconds):
        self.seconds = float(seconds or 0)
        self.enabled = (self.seconds > 0 and hasattr(signal, "SIGALRM")
                        and threading.current_thread() is threading.main_thread())
        self._previous = None

    def __enter__(self):
        if not self.enabled:
            return self

        def _raise(signum, frame):
            raise K.BudgetError("step exceeded its timeout",
                                timeout_seconds=self.seconds)

        self._previous = signal.signal(signal.SIGALRM, _raise)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if self._previous is not None:
                signal.signal(signal.SIGALRM, self._previous)
        return False


# --------------------------------------------------------------------------------------
# Definition loading and validation
# --------------------------------------------------------------------------------------

TEMPLATE_RE = __import__("re").compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def load_definition(ctx, params):
    if params.get("file"):
        try:
            path = ctx.workspace.resolve_asset(params["file"], must_exist=True)
        except K.OAAError:
            raise K.NotFoundError("workflow file not found", file=params["file"],
                                  read_roots=ctx.workspace.read_scope.describe())
        return K.read_json(path), ctx.workspace.source_id(path)
    if isinstance(params.get("workflow"), dict):
        return params["workflow"], "<inline>"
    raise K.InputError("workflow.run requires params.file or params.workflow")


def validate_definition(ctx, registry, definition):
    errors = list(GUARD.validate_schema(
        definition,
        K.read_json(K.resource_path(WORKFLOW_SCHEMA))))
    steps = definition.get("steps", []) if isinstance(definition, dict) else []
    ids = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id")
        if step_id in ids:
            errors.append({"path": "$.steps", "message": "duplicate step id",
                           "property": step_id})
        ids.append(step_id)
    known = set(ids)
    for step in steps:
        if not isinstance(step, dict):
            continue
        for dependency in step.get("depends_on", []):
            if dependency not in known:
                errors.append({"path": "$.steps[%s].depends_on" % step.get("id"),
                               "message": "unknown dependency",
                               "property": dependency})
        op = step.get("op")
        if op not in registry.ops:
            errors.append({"path": "$.steps[%s].op" % step.get("id"),
                           "message": "operation is not registered",
                           "property": op})
        compensate = step.get("compensate") or {}
        if compensate.get("op") and compensate["op"] not in registry.ops:
            errors.append({"path": "$.steps[%s].compensate.op" % step.get("id"),
                           "message": "compensation operation is not registered",
                           "property": compensate["op"]})
        retry = step.get("retry") or {}
        for cls in retry.get("on", []):
            if cls not in ALLOWED_RETRY_CLASSES:
                errors.append({"path": "$.steps[%s].retry.on" % step.get("id"),
                               "message": "unknown failure class",
                               "property": cls})
    max_steps = int(K.get_path(ctx.cfg, "limits.max_steps", 200))
    if len(steps) > max_steps:
        errors.append({"path": "$.steps", "message": "workflow exceeds max_steps",
                       "limit": max_steps, "actual": len(steps)})
    order, cycle = topological_order(steps)
    if cycle:
        errors.append({"path": "$.steps", "message": "dependency cycle detected",
                       "cycle": cycle})
    return {"valid": not errors, "error_count": len(errors), "errors": errors,
            "step_count": len(steps), "execution_order": order,
            "max_steps_allowed": max_steps}


def topological_order(steps):
    """Kahn topological sort with deterministic tie-breaking. Detects cycles."""
    ids = []
    graph = {}
    indegree = {}
    for step in steps:
        if not isinstance(step, dict) or "id" not in step:
            continue
        ids.append(step["id"])
        graph[step["id"]] = list(step.get("depends_on", []))
        indegree[step["id"]] = len(graph[step["id"]])
    ready = sorted([node for node in ids if indegree[node] == 0])
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for other in ids:
            if node in graph[other]:
                indegree[other] -= 1
                if indegree[other] == 0:
                    ready.append(other)
        ready.sort()
    if len(order) != len(ids):
        cycle = sorted(set(ids) - set(order))
        return order, cycle
    return order, None


def resolve_templates(value, results):
    """Substitute {{ step.<id>.<dotted.path> }} references with prior step results."""
    if isinstance(value, dict):
        return {key: resolve_templates(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, results) for item in value]
    if not isinstance(value, str):
        return value
    whole = TEMPLATE_RE.fullmatch(value.strip())
    if whole:
        # A parameter that is exactly one template keeps the resolved value's type so
        # lists and objects can flow between steps.
        resolved = _resolve_reference(whole.group(1), results)
        return resolved

    def _replace(match):
        found = _resolve_reference(match.group(1), results)
        return found if isinstance(found, str) else K.canonical_json(found)

    return TEMPLATE_RE.sub(_replace, value)


def _resolve_reference(reference, results):
    parts = reference.split(".")
    if parts[0] not in ("step", "steps") or len(parts) < 3:
        raise K.InputError("unsupported template reference", reference=reference)
    step_id = parts[1]
    if step_id not in results:
        raise K.InputError("template references a step with no result",
                           reference=reference, step=step_id)
    found = K.get_path(results[step_id], ".".join(parts[2:]))
    if found is None:
        raise K.InputError("template reference did not resolve", reference=reference)
    return found


# --------------------------------------------------------------------------------------
# Execution engine
# --------------------------------------------------------------------------------------

def _run_step(ctx, registry, step, params, results, run_state):
    op_name = step["op"]
    entry = registry.get(op_name)
    ctx.check_cancel()
    if entry["approval_action"]:
        ctx.guard(entry["approval_action"], approval_token=params.get("approval_token"))
    if step.get("approval"):
        gate = step["approval"]
        reason = gate.get("reason") or "step %s requires approval" % step["id"]
        decided = {record["subject"] for record in
                   ctx.store.approval_list(status="approved")
                   if record["action"] == gate["action"]}
        if step["id"] in decided:
            ctx.emit("workflow.approval_satisfied", action=gate["action"],
                     target=step["id"], detail={"source": "approval_record"})
            params = dict(params)
            params["approval_token"] = None
        else:
            try:
                ctx.guard(gate["action"],
                          approval_token=params.get("approval_token"))
            except K.ApprovalRequired:
                record = ctx.store.approval_request(gate["action"], step["id"],
                                                    reason, gate.get("evidence") or {})
                ctx.emit("workflow.approval_pending", action=gate["action"],
                         target=step["id"], approval_id=record["id"])
                raise
    resolved = resolve_templates(params, results)
    idem_key = step.get("idempotency_key")
    if idem_key:
        key = str(resolve_templates(idem_key, results))
        existing = ctx.store.idempotency_get(key)
        if existing is not None:
            ctx.emit("workflow.step_replayed", action=op_name, target=step["id"],
                     detail={"idempotency_key": key})
            return {"status": "completed", "op": op_name,
                    "attempts": existing["result"].get("attempts", 1),
                    "duration_ms": 0.0, "replayed": True,
                    "result": existing["result"].get("result")}
    started = time.time()
    retry_cfg = step.get("retry") or {}
    attempts = int(retry_cfg.get("attempts", 1))
    base = float(retry_cfg.get("base_seconds",
                               K.get_path(ctx.cfg, "limits.retry_base_seconds", 0.05)))
    max_delay = float(retry_cfg.get("max_seconds",
                                    K.get_path(ctx.cfg, "limits.retry_max_seconds", 5.0)))
    retry_classes = tuple(retry_cfg.get("on") or ["transient", "tool"])
    timeout = float(step.get("timeout_seconds",
                             K.get_path(ctx.cfg, "limits.step_timeout_seconds", 120)))
    if ctx.remaining_seconds() <= 0:
        raise K.BudgetError("workflow time budget exhausted",
                            remaining_seconds=0)
    timeout = min(timeout, max(0.5, ctx.remaining_seconds()))
    last_error = None
    for attempt in range(1, attempts + 1):
        ctx.check_cancel()
        try:
            with StepTimeout(timeout):
                result = entry["handler"](ctx, resolved)
            duration = round((time.time() - started) * 1000, 2)
            if idem_key:
                ctx.store.idempotency_put(key, op_name,
                                          {"attempts": attempt, "result": result})
            return {"status": "completed", "op": op_name, "attempts": attempt,
                    "duration_ms": duration, "result": result}
        except K.CancelledError:
            raise
        except K.OAAError as exc:
            failure_class = K.classify_failure(exc)
            last_error = exc
            retryable = failure_class in retry_classes and attempt < attempts
            ctx.emit("workflow.step_attempt", action=op_name, target=step["id"],
                     attempt=attempt, failure_class=failure_class,
                     retryable=retryable, error=exc.message)
            if not retryable:
                raise
            delay = min(max_delay, base * (2 ** (attempt - 1)))
            delay *= 1.0 + K.random.uniform(-0.25, 0.25)
            time.sleep(max(0.0, delay))
    raise K.WorkflowError("step failed after retries", step=step["id"],
                          error=last_error.to_dict() if last_error else None)


def execute(ctx, registry, definition, run_id, resume=False, force=False,
            source=None):
    steps = definition.get("steps", [])
    order, cycle = topological_order(steps)
    if cycle:
        raise K.InputError("workflow has a dependency cycle", cycle=cycle)
    by_id = {step["id"]: step for step in steps}
    budget = definition.get("budget") or {}
    max_seconds = float(min(
        float(budget.get("max_seconds", K.get_path(ctx.cfg,
                                                   "limits.workflow_timeout_seconds",
                                                   900))),
        ctx.remaining_seconds()))
    max_steps = int(budget.get("max_steps",
                               K.get_path(ctx.cfg, "limits.max_steps", 200)))
    if len(steps) > max_steps:
        raise K.BudgetError("workflow exceeds max_steps", steps=len(steps),
                            max_steps=max_steps)
    ctx.deadline = min(ctx.deadline, time.time() + max_seconds)
    completed = {}
    if resume:
        for row in ctx.store.checkpoint_load(run_id):
            completed[row["step_id"]] = row["state"]
    results = {step_id: state.get("result") for step_id, state in completed.items()}
    step_report = []
    executed_order = []
    compensation_stack = []
    cancelled = False
    failure = None
    status = "completed"
    approval_gate = None
    started = time.time()
    for step_id in order:
        step = by_id[step_id]
        if step_id in completed and not force:
            step_report.append({"id": step_id, "op": step["op"],
                                "status": "replayed-from-checkpoint",
                                "attempts": completed[step_id].get("attempts", 1),
                                "result": completed[step_id].get("result")})
            executed_order.append({"step": step_id, "status": "replayed"})
            if step.get("compensate"):
                compensation_stack.append(step)
            continue
        if ctx.cancellation is not None and ctx.cancellation.is_set():
            cancelled = True
            break
        if ctx.remaining_seconds() <= 0:
            failure = K.BudgetError("workflow time budget exhausted",
                                    max_seconds=max_seconds)
            break
        ctx.store.run_upsert(run_id, definition.get("workflow", "unnamed"),
                             str(definition.get("version", "1.0")), "running",
                             {"steps": len(steps), "budget": budget},
                             ctx.correlation_id)
        try:
            outcome = _run_step(ctx, registry, step, step.get("params") or {},
                                results, {})
        except K.ApprovalRequired as exc:
            approval_gate = exc.details.get("action")
            status = "awaiting-approval"
            step_report.append({"id": step_id, "op": step["op"],
                                "status": "awaiting-approval",
                                "action": approval_gate,
                                "detail": exc.details})
            failure = None
            break
        except K.CancelledError:
            cancelled = True
            break
        except K.OAAError as exc:
            failure_class = K.classify_failure(exc)
            ctx.store.dlq_add(run_id, step_id, exc.to_dict().get("error"))
            step_report.append({"id": step_id, "op": step["op"], "status": "failed",
                                "failure_class": failure_class,
                                "error": exc.to_dict().get("error")})
            if step.get("continue_on_error"):
                results[step_id] = {"ok": False,
                                    "error": exc.to_dict().get("error")}
                executed_order.append({"step": step_id,
                                       "status": "failed-continued"})
                continue
            failure = exc
            break
        if outcome["result"] is None:
            outcome["result"] = {}
        results[step_id] = outcome["result"]
        executed_order.append({"step": step_id, "status": "completed"})
        ctx.store.checkpoint_save(run_id, step_id, {
            "op": step["op"], "attempts": outcome["attempts"],
            "result": outcome["result"], "saved_at": K.now_iso()})
        report_entry = {"id": step_id, "op": step["op"], "status": "completed",
                        "attempts": outcome["attempts"],
                        "duration_ms": outcome["duration_ms"],
                        "result": outcome["result"]}
        if outcome.get("replayed"):
            report_entry["replayed_from_idempotency_key"] = True
        step_report.append(report_entry)
        if step.get("compensate"):
            compensation_stack.append(step)
    if (failure is not None or cancelled) and compensation_stack:
        for step in reversed(compensation_stack):
            comp = step.get("compensate") or {}
            if not comp.get("op"):
                continue
            try:
                entry = registry.get(comp["op"])
                ctx.dry_run = False
                outcome = entry["handler"](ctx, comp.get("params") or {})
                step_report.append({"id": step["id"] + ":compensate",
                                    "op": comp["op"], "status": "compensated",
                                    "result": outcome})
            except K.OAAError as exc:
                step_report.append({"id": step["id"] + ":compensate",
                                    "op": comp.get("op"),
                                    "status": "compensation-failed",
                                    "error": exc.to_dict().get("error")})
    if cancelled:
        status = "cancelled"
    elif failure is not None:
        status = "failed"
    elif approval_gate:
        status = "awaiting-approval"
    duration = round((time.time() - started) * 1000, 2)
    manifest = {
        "run_id": run_id,
        "workflow": definition.get("workflow"),
        "version": definition.get("version"),
        "status": status,
        "started_at": K.now_iso(started),
        "finished_at": K.now_iso(),
        "duration_ms": duration,
        "correlation_id": ctx.correlation_id,
        "definition_digest": K.sha256_obj(definition),
        "source": str(source) if source else None,
        "config_digest": K.sha256_obj(ctx.cfg.get("limits")),
        "provider": ctx.provider.info(),
        "kernel_version": K.VERSION,
        "steps": step_report,
        "order": executed_order,
        "failure": failure.to_dict().get("error") if failure else None,
        "approval_required": approval_gate,
    }
    ctx.store.run_upsert(run_id, definition.get("workflow", "unnamed"),
                         str(definition.get("version", "1.0")), status, manifest,
                         ctx.correlation_id)
    ctx.audit.append({"event": "workflow.finished", "action": "workflow.run",
                      "target": run_id, "decision": status,
                      "correlation_id": ctx.correlation_id,
                      "detail": {"workflow": definition.get("workflow"),
                                 "duration_ms": duration,
                                 "failure": manifest["failure"]}})
    return manifest


def _register(registry):
    reg = registry

    @reg.op("workflow.validate",
            "Validate a workflow definition and print its execution order.",
            params=["file", "workflow"], group="workflow",
            examples=['{"file": "asset-golden-workflow.json"}'])
    def workflow_validate(ctx, params):
        definition, source = load_definition(ctx, params)
        report = validate_definition(ctx, reg, definition)
        report["source"] = source
        ctx.emit("workflow.validate", action="workflow.validate",
                 target=str(source), valid=report["valid"])
        return report

    @reg.op("workflow.run",
            "Execute a workflow with budgets, retries, checkpoints and audit.",
            params=["file", "workflow", "force", "approval_token"],
            group="workflow", mutating=True, approval_action="workflow.run",
            examples=['{"file": "asset-golden-workflow.json"}'])
    def workflow_run(ctx, params):
        definition, source = load_definition(ctx, params)
        report = validate_definition(ctx, reg, definition)
        if not report["valid"]:
            raise K.InputError("workflow definition is invalid",
                               errors=report["errors"], source=source)
        run_id = ctx.run_id or K.new_id("run")
        ctx.run_id = run_id
        flag = CancellationFlag(os.path.join(ctx.workspace.root, "runs",
                                             "%s.cancel" % run_id))
        flag.clear()
        ctx.cancellation = flag
        ctx.store.run_upsert(run_id, definition.get("workflow", "unnamed"),
                             str(definition.get("version", "1.0")), "running",
                             {"source": str(source)}, ctx.correlation_id)
        ctx.emit("workflow.started", action="workflow.run", target=run_id,
                 workflow=definition.get("workflow"), source=str(source))
        manifest = execute(ctx, reg, definition, run_id, resume=False,
                           force=bool(params.get("force")), source=source)
        if manifest["status"] == "failed":
            raise K.WorkflowError("workflow failed", run_id=run_id,
                                  failure=manifest["failure"],
                                  steps=manifest["steps"])
        return manifest

    @reg.op("workflow.resume",
            "Resume a workflow run from its last validated checkpoint.",
            params=["run_id", "approval_token", "force"], group="workflow",
            mutating=True, approval_action="workflow.resume",
            examples=['{"run_id": "run-0123456789ab"}'])
    def workflow_resume(ctx, params):
        run_id = params.get("run_id")
        if not run_id:
            raise K.InputError("workflow.resume requires params.run_id")
        record = ctx.store.run_get(run_id)
        manifest_data = record["manifest"]
        source = manifest_data.get("source")
        definition = None
        if source and source != "<inline>":
            candidate = ctx.workspace.source_path(source)
            if candidate and os.path.isfile(candidate):
                definition = K.read_json(candidate)
        if definition is None and params.get("file"):
            definition, _ = load_definition(ctx, params)
        if definition is None:
            raise K.StateError(
                "cannot locate the workflow definition for this run",
                run_id=run_id, source=source,
                remediation="pass file=<path> so the definition can be reloaded")
        report = validate_definition(ctx, reg, definition)
        if not report["valid"]:
            raise K.InputError("workflow definition is no longer valid",
                               errors=report["errors"])
        ctx.run_id = run_id
        flag = CancellationFlag(os.path.join(ctx.workspace.root, "runs",
                                             "%s.cancel" % run_id))
        ctx.cancellation = flag
        ctx.emit("workflow.resumed", action="workflow.resume", target=run_id,
                 previous_status=record["status"])
        manifest = execute(ctx, reg, definition, run_id, resume=True,
                           force=bool(params.get("force")), source=source)
        if manifest["status"] == "failed":
            raise K.WorkflowError("resumed workflow failed", run_id=run_id,
                                  failure=manifest["failure"])
        return manifest

    @reg.op("workflow.status", "Report one workflow run.",
            params=["run_id"], group="workflow",
            examples=['{"run_id": "run-0123456789ab"}'])
    def workflow_status(ctx, params):
        run_id = params.get("run_id")
        if not run_id:
            raise K.InputError("workflow.status requires params.run_id")
        record = ctx.store.run_get(run_id)
        checkpoints = [{"step_id": row["step_id"],
                        "created_at": row["created_at"]}
                       for row in ctx.store.checkpoint_load(run_id)]
        return {"run": record, "checkpoints": checkpoints}

    @reg.op("workflow.list", "List recent workflow runs.",
            params=["limit", "status"], group="workflow",
            examples=['{"limit": 10}'])
    def workflow_list(ctx, params):
        rows = ctx.store.run_list(limit=int(params.get("limit", 20)),
                                  status=params.get("status"))
        return {"count": len(rows), "runs": rows}

    @reg.op("workflow.cancel",
            "Request cancellation of a workflow run; it stops at the next step boundary.",
            params=["run_id"], group="workflow", mutating=True,
            approval_action="workflow.cancel",
            examples=['{"run_id": "run-0123456789ab"}'])
    def workflow_cancel(ctx, params):
        run_id = params.get("run_id")
        if not run_id:
            raise K.InputError("workflow.cancel requires params.run_id")
        ctx.guard("workflow.cancel", approval_token=params.get("approval_token"))
        record = ctx.store.run_get(run_id)
        flag = CancellationFlag(os.path.join(ctx.workspace.root, "runs",
                                             "%s.cancel" % run_id))
        flag.set()
        ctx.emit("workflow.cancel_requested", action="workflow.cancel",
                 target=run_id, previous_status=record["status"])
        return {"run_id": run_id, "cancellation_requested": True,
                "cancellation_flag": ctx.workspace.rel(flag.path),
                "previous_status": record["status"],
                "note": "a running workflow stops at its next step boundary and "
                        "runs declared compensations"}


def register(registry):
    _register(registry)
    return registry
