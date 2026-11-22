"""imagekit pipeline: orchestration, checkpoints, retries, timeouts and recovery.

A pipeline plan is JSON:

{
  "version": 1,
  "name": "campaign-a",
  "defaults": {"project": "acme"},
  "steps": [
    {"id": "brief", "op": "brief", "args": {"brief": "..."}},
    {"id": "prompt", "op": "prompt", "args": {"brief": "text"}, "deps": ["brief"]}
  ]
}

Steps run in dependency order. Each step receives the merged args
(defaults + step args), a fresh context and the shared operation registry.
Checkpoints are written after every step so an interrupted run can resume.
"""

from __future__ import annotations

import copy
import json
import re
import signal
import time
import traceback
from pathlib import Path

import imagekit_core as core

MAX_STEPS = 60


def load_plan(path):
    payload = core.read_json(core.resolve_input(path), required=True)
    if not isinstance(payload, dict):
        raise core.input_error("Pipeline plan must be a JSON object")
    return payload


def validate_plan(plan, registry):
    problems = []
    if not isinstance(plan.get("steps"), list) or not plan["steps"]:
        raise core.input_error("Pipeline plan has no steps", "Add at least one step object.")
    if len(plan["steps"]) > MAX_STEPS:
        raise core.input_error(f"Plan has {len(plan['steps'])} steps; the limit is {MAX_STEPS}")
    ids = []
    ops_used = []
    for index, step in enumerate(plan["steps"]):
        if not isinstance(step, dict):
            problems.append(f"step {index} is not an object")
            continue
        sid = step.get("id")
        if not sid or not isinstance(sid, str):
            problems.append(f"step {index} has no string id")
            continue
        if sid in ids:
            problems.append(f"duplicate step id '{sid}'")
        ids.append(sid)
        op = step.get("op")
        if op not in registry:
            problems.append(f"step '{sid}' uses unknown op '{op}'")
        else:
            ops_used.append(op)
        if "args" in step and not isinstance(step["args"], dict):
            problems.append(f"step '{sid}' args must be an object")
        for retries_key in ("retries", "timeout_s"):
            if retries_key in step and not isinstance(step[retries_key], (int, float)):
                problems.append(f"step '{sid}' {retries_key} must be numeric")
    for step in plan["steps"]:
        for dep in (step.get("deps") or []):
            if dep not in ids:
                problems.append(f"step '{step.get('id')}' depends on unknown step '{dep}'")
    if problems:
        raise core.input_error("Pipeline plan is invalid: " + "; ".join(problems),
                               "Fix the plan and re-run with --dry-run to validate again.")
    order = topological_order(plan["steps"])
    return {"steps": len(plan["steps"]), "ops": sorted(set(ops_used)), "order": order}


def topological_order(steps):
    by_id = {s["id"]: s for s in steps}
    order, state = [], {}

    def visit(node, stack):
        if state.get(node) == "done":
            return
        if state.get(node) == "visiting":
            raise core.input_error(f"Dependency cycle detected: {' -> '.join(stack + [node])}",
                                   "Break the cycle in the plan's deps.")
        state[node] = "visiting"
        for dep in (by_id[node].get("deps") or []):
            visit(dep, stack + [node])
        state[node] = "done"
        order.append(node)

    for step in steps:
        visit(step["id"], [])
    return order


def _run_with_timeout(fn, seconds):
    """POSIX-only hard timeout. Falls back to no timeout where SIGALRM is missing."""
    if not hasattr(signal, "SIGALRM") or seconds <= 0:
        return fn(), False

    def handler(signum, frame):
        raise TimeoutError(f"step exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(), True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run_plan(ctx, plan_path, registry, resume=False, workers=1, parallel=False):
    plan = load_plan(plan_path)
    summary = validate_plan(plan, registry)
    name = plan.get("name") or "pipeline"
    state_path = Path(getattr(ctx, "state_file", None) or
                      (ctx.out_root / f"{core.slugify(name)}-state.json"))
    state = {}
    if resume and state_path.exists():
        state = core.read_json(state_path, required=False) or {}
    else:
        state = {"plan": name, "started_at": core.now_iso(), "steps": [], "status": "running"}
    completed = {s["id"]: s for s in state.get("steps", []) if s.get("status") == "ok"}
    defaults = plan.get("defaults") or {}
    continue_on_error = bool(plan.get("continue_on_error", ctx.config.pipeline.get("continue_on_error", False)))
    default_timeout = int(plan.get("timeout_s") or ctx.config.limit("default_timeout_s", 300))
    default_retries = int(plan.get("retries", ctx.config.limit("max_retries", 3)))
    results = []
    statuses = dict((s["id"], s) for s in state.get("steps", []))
    started = time.time()

    for step_id in summary["order"]:
        step = next(s for s in plan["steps"] if s["id"] == step_id)
        record = {"id": step_id, "op": step.get("op"), "status": "pending"}
        if step_id in completed:
            record = dict(completed[step_id])
            record["status"] = "skipped-resumed"
            results.append(record)
            statuses[step_id] = record
            continue
        if ctx.cancelled:
            record["status"] = "cancelled"
            record["detail"] = "cancelled before execution"
            results.append(record)
            statuses[step_id] = record
            break
        op = step["op"]
        fn = registry[op]
        args = dict(defaults)
        args.update(step.get("args") or {})
        missing = [r for r in (step.get("requires") or [])
                   if not (Path(r).expanduser().is_absolute() and Path(r).expanduser().exists()
                           or (ctx.out_root / r).exists())]
        if missing:
            record.update({"status": "failed", "error_code": "input",
                           "error": f"missing required file(s): {', '.join(missing)}",
                           "hint": "Run the producing step first or fix the 'requires' list."})
            results.append(record)
            statuses[step_id] = record
            state["steps"] = list(statuses.values())
            if ctx.config.pipeline.get("checkpoint", True) and not ctx.dry_run:
                core.write_json(state_path, state)
            if not continue_on_error:
                state["status"] = "failed"
                break
            continue
        step_ctx = ctx
        if args.get("out"):
            step_ctx = copy.copy(ctx)
            step_ctx.out_root = core.ensure_dir(Path(str(args.pop("out"))).expanduser())
        args = resolve_placeholders(args, statuses, ctx.out_root)
        retries = int(step.get("retries", default_retries))
        timeout = int(step.get("timeout_s", default_timeout))
        attempts = 0
        step_started = time.time()
        while True:
            attempts += 1
            try:
                args_ns = build_args(args)
                output, enforced = _run_with_timeout(lambda: fn(step_ctx, args_ns), timeout)
                record.update({
                    "status": "ok", "attempts": attempts,
                    "seconds": round(time.time() - step_started, 3),
                    "hard_timeout_enforced": enforced,
                    "outputs": [core.rel_posix(p, ctx.out_root) for p in (output.get("outputs") or [])],
                    "data_keys": sorted((output.get("data") or {}).keys()),
                    "data": output.get("data") or {},
                    "warnings": output.get("warnings") or [],
                })
                break
            except TimeoutError as exc:
                record.update({"status": "failed", "attempts": attempts, "error": str(exc),
                               "error_code": "timeout"})
                if attempts > retries:
                    break
            except core.ToolError as exc:
                record.update({"status": "failed", "attempts": attempts, "error": exc.message,
                               "error_code": exc.code, "hint": exc.hint})
                transient = exc.code in ("provider_unavailable", "internal", "encode_failed")
                if attempts > retries or not transient:
                    break
                time.sleep(min(6, 2 ** (attempts - 1)))
            except Exception as exc:  # unexpected: keep the run recoverable
                record.update({"status": "failed", "attempts": attempts,
                               "error": f"{type(exc).__name__}: {exc}",
                               "error_code": "internal",
                               "traceback": traceback.format_exc(limit=4)})
                if attempts > retries:
                    break
                time.sleep(min(6, 2 ** (attempts - 1)))
        results.append(record)
        statuses[step_id] = record
        state["steps"] = list(statuses.values())
        state["updated_at"] = core.now_iso()
        if ctx.config.pipeline.get("checkpoint", True) and not ctx.dry_run:
            core.write_json(state_path, state)
        if record["status"] == "failed" and not continue_on_error:
            state["status"] = "failed"
            break
    failed = [r for r in results if r["status"] == "failed"]
    if state.get("status") != "failed":
        state["status"] = "failed" if failed else ("cancelled" if ctx.cancelled else "ok")
    state["finished_at"] = core.now_iso()
    state["seconds"] = round(time.time() - started, 2)
    state["steps"] = results
    if not ctx.dry_run:
        core.write_json(state_path, state)
    return state, state_path, summary


PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def resolve_placeholders(value, statuses, out_root):
    """Expand {{steps.ID.outputs.N}}, {{steps.ID.outputs:match}} and {{steps.ID.data.KEY}}."""
    if isinstance(value, str):
        def repl(match):
            token = match.group(1).strip()
            parts = token.split(".")
            if len(parts) < 3 or parts[0] != "steps":
                if token == "out":
                    return str(out_root)
                return match.group(0)
            record = statuses.get(parts[1]) or {}
            outputs = record.get("outputs") or []
            tail = ".".join(parts[2:])
            if tail.startswith("outputs:"):
                needle = tail.split(":", 1)[1]
                for name in outputs:
                    if needle in name:
                        return name if Path(name).is_absolute() else str(out_root / name)
                return match.group(0)
            if tail.startswith("outputs"):
                index = 0
                if "." in tail:
                    try:
                        index = int(tail.split(".", 1)[1])
                    except ValueError:
                        index = 0
                if index < len(outputs):
                    name = outputs[index]
                    return name if Path(name).is_absolute() else str(out_root / name)
                return match.group(0)
            if tail.startswith("data."):
                node = record.get("data") or {}
                for key in tail.split(".")[1:]:
                    if isinstance(node, dict) and key in node:
                        node = node[key]
                    elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                        node = node[int(key)]
                    else:
                        return match.group(0)
                return node if isinstance(node, str) else json.dumps(node)
            return match.group(0)
        return PLACEHOLDER.sub(repl, value)
    if isinstance(value, list):
        return [resolve_placeholders(v, statuses, out_root) for v in value]
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, statuses, out_root) for k, v in value.items()}
    return value


def build_args(args):
    """Turn a dict of step args into an argparse-like namespace with safe defaults."""
    import argparse
    ns = argparse.Namespace(**args)

    def get(name, default=None):
        return getattr(ns, name, default)

    # defaults every handler tolerates
    for key in ("input", "inputs", "project", "brief", "name", "size", "format", "quality", "seed",
                "palette", "preset", "style", "kind", "action", "mode", "use", "colors", "strategy",
                "subject", "focal", "horizon", "subjects", "region", "strength", "feather", "method",
                "tolerance", "bg_color", "background", "scale", "position", "shadow", "exposure",
                "contrast", "saturation", "temperature", "tint", "gamma", "lift", "vignette", "grain",
                "bloom", "relight", "denoise", "autolevels", "despeckle", "sharpen", "upscale",
                "dpi", "bleed", "overlap", "columns", "rows", "ratios", "width", "duration", "loop",
                "frames", "motion", "direction", "left", "right", "brief_file", "expect_size",
                "expect_format", "expect_transparent", "expect_palette", "state", "library", "target",
                "title", "steps", "data", "note", "headline", "subhead", "cta", "image", "accent",
                "text_color", "scrim", "gradient", "render_guides", "captions", "aspect",
                "cell_width", "frame_width", "frame_height", "lock_palette", "export_frames",
                "labels", "sizes", "glyph", "stroke_scale", "opaque", "density", "refs", "owner",
                "candidate", "direction_file", "threshold", "apply", "publish", "tags", "window",
                "max_dim", "pad", "pad_color", "rotate", "flip", "crop", "fit", "auto_orient",
                "count", "axes", "dialect", "emphasis", "max_kb", "transparent", "version", "status",
                "provider", "prompt_text", "negative", "label", "creator", "lineage", "notes",
                "rights", "disclosure", "consent_subject", "consent_reference", "consent_scope",
                "gate_report", "scan", "fail_on", "no_disclosure", "artwork", "form", "preset_hint",
                "stress", "mask", "lossless", "strip_metadata", "safe_margin", "scan_dir"):
        if not hasattr(ns, key):
            setattr(ns, key, None)
    for key in ("count", "subjects", "columns", "rows", "frames", "duration", "loop", "version"):
        if getattr(ns, key, None) is None:
            setattr(ns, key, 1 if key == "version" else None)
    for flag in ("transparent", "no_disclosure", "lock_palette", "gradient", "render_guides",
                 "denoise", "autolevels", "despeckle", "opaque", "apply", "publish", "stress",
                 "mask", "lossless", "strip_metadata", "auto_orient"):
        if getattr(ns, flag, None) is None:
            setattr(ns, flag, False)
    return ns


def op_pipeline(ctx, a, registry):
    plan_path = getattr(a, "plan", None)
    if not plan_path:
        raise core.usage_error("The 'pipeline' command needs --plan PLAN.json",
                               "See knowledge-readme.md for the plan schema.")
    if getattr(a, "dry_run", False) or (getattr(a, "action", None) or "run") == "validate":
        plan = load_plan(plan_path)
        summary = validate_plan(plan, registry)
        payload = {"operation": "pipeline", "action": "validate", "plan": Path(plan_path).name,
                   "valid": True, **summary,
                   "estimate": {"steps": summary["steps"],
                                "max_seconds": summary["steps"] * int(plan.get("timeout_s", 300))}}
        return {"data": payload, "outputs": []}
    missing = [s["id"] for s in load_plan(plan_path)["steps"]
               if (s.get("requires") and not all((ctx.out_root / r).exists() for r in s["requires"]))]
    if missing:
        raise core.input_error(f"Steps have unmet file requirements: {', '.join(missing)}",
                               "Produce the required files first or remove the 'requires' entries.")
    state, state_path, summary = run_plan(ctx, plan_path, registry,
                                          resume=bool(getattr(a, "resume", False)),
                                          workers=int(getattr(a, "workers", 1) or 1))
    payload = {"operation": "pipeline", "plan": state.get("plan"), "status": state.get("status"),
               "seconds": state.get("seconds"),
               "steps": [{k: s.get(k) for k in ("id", "op", "status", "attempts", "seconds",
                                                "error", "error_code", "outputs")} for s in state.get("steps", [])],
               "state_file": core.rel_posix(state_path, ctx.out_root),
               "summary": summary,
               "resume_available": state.get("status") in ("failed", "cancelled")}
    # run reports are run logs, not delivered assets: a re-run (especially --resume) replaces them
    report_ctx = copy.copy(ctx)
    report_ctx.force = True
    out_json = core.emit_json(report_ctx, core.slugify(f"{state.get('plan')}-pipeline-run"), payload)
    md = [f"# Pipeline run: {state.get('plan')}", "",
          f"Status: **{state.get('status')}** in {state.get('seconds')}s", "",
          "| Step | Op | Status | Attempts | Seconds | Error |", "| --- | --- | --- | --- | --- | --- |"]
    for s in state.get("steps", []):
        md.append(f"| {s.get('id')} | {s.get('op')} | {s.get('status')} | {s.get('attempts')} | "
                  f"{s.get('seconds')} | {s.get('error') or ''} |")
    if payload["resume_available"]:
        md += ["", "Re-run with `--resume` to continue from the recorded checkpoint."]
    out_md = core.emit_text(report_ctx, core.slugify(f"{state.get('plan')}-pipeline-run"), "md",
                            "\n".join(md) + "\n")
    ctx.audit("pipeline.finished", status=state.get("status"))
    return {"data": payload, "outputs": [out_json, out_md]}
