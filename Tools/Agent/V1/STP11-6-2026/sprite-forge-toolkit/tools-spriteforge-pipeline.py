#!/usr/bin/env python3
"""spriteforge pipeline: orchestration, recovery, sharding and watch planning.

Operations
----------
  run       execute a multi-step pipeline spec with per-step logs and recovery
  recover   resume a failed pipeline from its state journal
  farm      split render jobs across deterministic workers (plans + shards)
  watch     bounded polling loop that validates and plans export on change

A pipeline spec is JSON::

    {
      "name": "hero-set",
      "steps": [
        {"id": "frames", "module": "author", "op": "frames",
         "args": {"spec": "spec.json", "output": "out/frames"}},
        {"id": "sheet", "module": "sheet", "op": "compose",
         "args": {"input": "out/frames", "output": "out/sheet.png",
                  "metadata": "out/sheet.json"}, "depends_on": ["frames"]},
        {"id": "validate", "module": "validate", "op": "sheet",
         "args": {"input": "out/sheet.png", "metadata": "out/sheet.json"},
         "allow_failure": true}
      ]
    }

Modules resolve to ``tools-spriteforge-<module>.py`` beside this file. Steps run
as isolated subprocesses with a timeout, so one crash cannot take down the run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_pipeline", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_pipeline"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError

MODULES = {"author", "plan", "sheet", "anim", "edit", "convert", "validate",
           "analyze", "engine", "govern", "pipeline"}
RESERVED_ARGS = {"root", "report", "json", "timeout", "approve", "dry_run",
                 "allow_outside_root", "max_frames", "max_pixels", "quiet", "version", "operation"}


# --------------------------------------------------------------------------- #
# spec validation
# --------------------------------------------------------------------------- #

def resolve_spec(path: Path) -> Path:
    """Resolve a spec path against --root, then against the shipped examples."""
    if path.exists():
        return path
    fallback = core.bundled(path.name)
    return fallback if fallback is not None else path


def load_spec(path: Path) -> dict:
    path = resolve_spec(path)
    spec = core.load_json(path)
    if not isinstance(spec, dict):
        raise SFError("BAD_SPEC", "pipeline spec root must be an object")
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SFError("BAD_SPEC", "pipeline spec needs a non-empty 'steps' list")
    ids = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SFError("BAD_SPEC", f"step {i} must be an object")
        sid = step.get("id") or f"step{i}"
        step["id"] = sid
        if sid in ids:
            raise SFError("BAD_SPEC", f"duplicate step id '{sid}'")
        ids.add(sid)
        module = step.get("module")
        if module not in MODULES:
            raise SFError("BAD_SPEC", f"step '{sid}' uses unknown module '{module}'",
                          details={"allowed": sorted(MODULES)})
        if not step.get("op"):
            raise SFError("BAD_SPEC", f"step '{sid}' needs an 'op'")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise SFError("BAD_SPEC", f"step '{sid}' args must be an object")
        bad = [k for k in args if k.replace("-", "_") in RESERVED_ARGS]
        if bad:
            raise SFError("BAD_SPEC", f"step '{sid}' args use reserved global keys: {bad}",
                          hint="global flags are controlled by the pipeline, not the step")
        if not (HERE / f"tools-spriteforge-{module}.py").exists():
            raise SFError("MISSING_MODULE", f"module file missing: tools-spriteforge-{module}.py")
    graph = {s["id"]: s for s in steps}
    for step in steps:
        for dep in step.get("depends_on", []) or []:
            if dep not in graph:
                raise SFError("BAD_SPEC", f"step '{step['id']}' depends on unknown step '{dep}'")
    # cycle detection
    state: dict[str, int] = {}
    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == 1:
            raise SFError("CYCLE", f"dependency cycle: {' -> '.join(stack + [node])}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph[node].get("depends_on", []) or []:
            visit(dep, stack + [node])
        state[node] = 2
    for node in graph:
        visit(node, [])
    return spec


def step_approvals(step: dict, approve: list[str]) -> list[str]:
    """Global --approve tokens plus any ``approve`` list declared on the step."""
    tokens = list(approve)
    for token in step.get("approve") or []:
        if isinstance(token, str) and token not in tokens:
            tokens.append(token)
    return tokens


def build_command(step: dict, root: Path, *, approve: list[str]) -> list[str]:
    module_file = HERE / f"tools-spriteforge-{step['module']}.py"
    cmd = [sys.executable, str(module_file), "--root", str(root), step["op"]]
    for key, value in (step.get("args") or {}).items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                cmd.append(flag)
        elif isinstance(value, list):
            for item in value:
                cmd.extend([flag, str(item)])
        elif value is not None:
            cmd.extend([flag, str(value)])
    for token in step_approvals(step, approve):
        cmd.extend(["--approve", token])
    return cmd


def run_step(step: dict, root: Path, *, timeout: float, approve: list[str]) -> dict:
    cmd = build_command(step, root, approve=approve)
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or None,
                              cwd=str(root), check=False)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        report = None
        try:
            report = json.loads(out) if out.startswith("{") else None
        except json.JSONDecodeError:
            report = None
        return {
            "id": step["id"], "module": step["module"], "op": step["op"],
            "exit_code": proc.returncode, "ok": proc.returncode == 0,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": out[-2000:] if not report else "",
            "stderr_tail": err[-2000:],
            "report": report,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"id": step["id"], "module": step["module"], "op": step["op"],
                "exit_code": 5, "ok": False, "duration_s": round(time.time() - started, 3),
                "stderr_tail": f"step timed out after {timeout}s", "command": " ".join(cmd)}


def journal_path(args, root: Path) -> Path:
    p = Path(args.journal) if Path(args.journal).is_absolute() else root / args.journal
    return core.guard_output(p, root, args.allow_outside_root)


def write_journal(path: Path, data: dict) -> None:
    core.save_json(data, path)


def op_run(args, root: Path, rep: core.Reporter):
    """Execute a multi-step pipeline spec with per-step logs and recovery state."""
    spec_path = resolve_spec(Path(args.spec) if Path(args.spec).is_absolute() else root / args.spec)
    spec = load_spec(spec_path)
    steps = spec["steps"]
    journal = journal_path(args, root)
    approve = list(args.approve or [])
    state = {
        "pipeline": spec.get("name", spec_path.stem),
        "spec": str(spec_path),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": [],
        "status": "running",
    }
    done = set()
    failed_at = None
    order = _topo_order(steps)
    for step in order:
        rep.deadline.check()
        deps = step.get("depends_on") or []
        if any(d not in done for d in deps):
            state["steps"].append({"id": step["id"], "status": "skipped", "reason": "dependency failed"})
            continue
        if args.dry_run:
            state["steps"].append({"id": step["id"], "status": "planned",
                                   "command": " ".join(build_command(step, root, approve=approve))})
            done.add(step["id"])
            continue
        result = run_step(step, root, timeout=args.step_timeout, approve=approve)
        result["status"] = "ok" if result["ok"] else "failed"
        state["steps"].append(result)
        write_journal(journal, state)
        name = f"{step['module']}:{step['op']}"
        if result["ok"]:
            done.add(step["id"])
            if not args.quiet:
                rep.progress(f"[ok]   {step['id']} ({name}) {result['duration_s']}s")
        else:
            if not args.quiet:
                rep.progress(f"[fail] {step['id']} ({name}) exit={result['exit_code']}", err=True)
            if step.get("allow_failure"):
                done.add(step["id"])
                state["steps"][-1]["status"] = "failed_allowed"
                continue
            failed_at = step["id"]
            break
    state["status"] = "completed" if failed_at is None else "failed"
    state["failed_step"] = failed_at
    state["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not args.dry_run:
        write_journal(journal, state)
        rep.add_output(journal, "journal")
    report = core.guard_output(root / args.report_path, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(state, report)
        rep.add_output(report, "pipeline-report")
    ok = sum(1 for s in state["steps"] if s["status"] in {"ok", "failed_allowed", "planned"})
    if failed_at:
        failed = next((s for s in state["steps"] if s["id"] == failed_at), {})
        tail = (failed.get("stderr_tail") or failed.get("stdout_tail")
                or "").strip().splitlines()[-4:]
        raise SFError("PIPELINE_FAILED",
                      f"step '{failed_at}' failed (exit {failed.get('exit_code')}); "
                      f"earlier steps are preserved",
                      hint=f"fix it, then run: python3 {os.path.basename(sys.argv[0])} "
                           f"recover --journal {args.journal} --only-failed",
                      details={"step": failed_at, "exit_code": failed.get("exit_code"),
                               "output_tail": tail},
                      exit_code=6)
    rep.stat("steps_ok", ok)
    return {"summary": {"pipeline": state["pipeline"], "steps": len(steps), "ok": ok,
                        "status": state["status"], "journal": str(journal)}}


def _topo_order(steps: list[dict]) -> list[dict]:
    by_id = {s["id"]: s for s in steps}
    out: list[dict] = []
    seen: set[str] = set()
    def visit(node: str) -> None:
        if node in seen:
            return
        for dep in by_id[node].get("depends_on", []) or []:
            visit(dep)
        seen.add(node)
        out.append(by_id[node])
    for s in steps:
        visit(s["id"])
    return out


def op_recover(args, root: Path, rep: core.Reporter):
    """Resume a failed pipeline from its state journal, rerunning only what failed."""
    jpath = Path(args.journal) if Path(args.journal).is_absolute() else root / args.journal
    state = core.load_json(jpath)
    if not isinstance(state, dict) or "steps" not in state:
        raise SFError("BAD_JOURNAL", "journal must contain a pipeline state object")
    if state.get("status") == "completed":
        return {"summary": {"status": "completed", "note": "nothing to recover",
                            "pipeline": state.get("pipeline")}}
    spec = load_spec(Path(state["spec"]) if Path(state["spec"]).is_absolute() else root / state["spec"])
    steps = spec["steps"]
    first_failure = state.get("failed_step")
    completed = {s["id"] for s in state["steps"] if s["status"] in {"ok", "failed_allowed"}}
    if first_failure is None:
        first_failure = next((s["id"] for s in steps if s["id"] not in completed), steps[0]["id"])
    order = _topo_order(steps)
    idx = next(i for i, s in enumerate(order) if s["id"] == first_failure)
    to_run = order[idx:]
    if args.only_failed:
        to_run = [s for s in to_run if s["id"] not in completed]
    approve = list(args.approve or [])
    results = []
    failed_at = None
    for step in to_run:
        if args.dry_run:
            results.append({"id": step["id"], "status": "planned"})
            continue
        result = run_step(step, root, timeout=args.step_timeout, approve=approve)
        result["status"] = "ok" if result["ok"] else "failed"
        results.append(result)
        if not args.quiet:
            rep.progress(f"[{'ok' if result['ok'] else 'fail'}] {step['id']}")
        if not result["ok"] and not step.get("allow_failure"):
            failed_at = step["id"]
            break
    state["recovery"] = {
        "resumed_from": first_failure,
        "ran": [r["id"] for r in results],
        "failed_step": failed_at,
        "recovered_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state["status"] = "completed" if failed_at is None else "failed"
    state["failed_step"] = failed_at
    state["steps"] = [s for s in state["steps"] if s["id"] not in {r["id"] for r in results}] + results
    if not args.dry_run:
        write_journal(jpath, state)
    if failed_at:
        raise SFError("RECOVERY_FAILED", f"step '{failed_at}' still fails after recovery",
                      details={"steps": results}, exit_code=6)
    return {"summary": {"resumed_from": first_failure, "ran": len(results), "status": state["status"]}}


def op_farm(args, root: Path, rep: core.Reporter):
    """Split render jobs across deterministic workers and keep file order."""
    spec_path = resolve_spec(Path(args.spec) if Path(args.spec).is_absolute() else root / args.spec)
    spec = core.load_json(spec_path)
    jobs = spec.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise SFError("BAD_SPEC", "farm spec needs a non-empty 'jobs' list")
    if args.workers < 1:
        raise SFError("BAD_ARGUMENT", "--workers must be >= 1")
    seed = int(args.seed or 0)
    indexed = list(enumerate(jobs))
    if seed:
        import random
        rng = random.Random(seed)
        rng.shuffle(indexed)
        indexed.sort(key=lambda pair: pair[0])
    shards: list[list[dict]] = [[] for _ in range(args.workers)]
    load = [0] * args.workers
    for idx, job in indexed:
        weight = float(job.get("weight", 1))
        target = min(range(args.workers), key=lambda i: (load[i], i))
        shards[target].append({"order": idx, **job})
        load[target] += weight
    checkout = core.guard_output(Path(args.output) if Path(args.output).is_absolute() else root / args.output,
                                 root, args.allow_outside_root, as_dir=True)
    manifest = {"spec": str(spec_path), "workers": args.workers, "seed": seed,
                "jobs": len(jobs), "shards": []}
    for i, shard in enumerate(shards):
        manifest["shards"].append({"worker": i, "jobs": len(shard),
                                   "load": round(load[i], 2),
                                   "files": [job.get("output") or job.get("id") for job in shard]})
        if not args.dry_run:
            core.save_json({"worker": i, "seed": seed, "jobs": shard},
                           checkout / f"worker-{i:02d}.json")
    manifest["join_order"] = [job.get("output") or job.get("id") for _, job in indexed]
    manifest["deterministic"] = True
    manifest["note"] = "each worker reads its shard file; outputs are joined by order, not arrival"
    if not args.dry_run:
        core.save_json(manifest, checkout / "farm-manifest.json")
        rep.add_output(checkout, "directory", shards=args.workers)
    return {"summary": {"jobs": len(jobs), "workers": args.workers,
                        "shard_sizes": [len(s) for s in shards], "out": str(checkout)}}


def op_watch(args, root: Path, rep: core.Reporter):
    """Bounded polling loop that reports changed files and queues validation."""
    core.require_approval(args, "pipeline-watch",
                          "automated watching can trigger exports; confirm it should run")
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.is_dir():
        raise SFError("INPUT_NOT_DIR", f"--input must be a folder: {src}")
    state_path = Path(args.state) if Path(args.state).is_absolute() else root / args.state
    state = core.load_json(state_path) if state_path.exists() else {"files": {}}
    previous = state.get("files", {})
    cycles = max(1, args.cycles)
    interval = max(0.0, args.interval)
    events: list[dict] = []
    for cycle in range(cycles):
        current = {}
        for p in core.list_images(src, recursive=True):
            current[p.relative_to(src).as_posix()] = [p.stat().st_mtime_ns, p.stat().st_size]
        changed = [k for k, v in current.items() if previous.get(k) != v]
        added = [k for k in current if k not in previous]
        removed = [k for k in previous if k not in current]
        if changed or added or removed:
            events.append({"cycle": cycle, "changed": changed, "added": added, "removed": removed,
                           "queued_action": args.action})
        previous = current
        if cycle < cycles - 1:
            time.sleep(interval)
    if not args.dry_run:
        target_state = state_path if state_path.is_absolute() else core.guard_output(state_path, root, args.allow_outside_root)
        core.save_json({"files": previous,
                        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, target_state)
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        core.save_json({"cycles": cycles, "interval_s": interval, "action": args.action,
                        "events": events}, out)
        rep.add_output(out, "watch-report")
    rep.stat("events", len(events))
    return {"summary": {"cycles": cycles, "events": len(events), "action": args.action}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_run(p):
    p.add_argument("--spec", required=True, help="pipeline spec JSON")
    p.add_argument("--journal", default="pipeline-state.json", help="resumable state file")
    p.add_argument("--report-path", default="pipeline-report.json", help="final report file")
    p.add_argument("--step-timeout", type=float, default=600, help="per-step timeout in seconds")
    p.add_argument("--quiet", action="store_true", default=False)


def _p_recover(p):
    p.add_argument("--journal", required=True)
    p.add_argument("--step-timeout", type=float, default=600)
    p.add_argument("--only-failed", action="store_true", help="run only steps not yet completed")


def _p_farm(p):
    p.add_argument("--spec", required=True, help="farm spec JSON with a jobs list")
    p.add_argument("--output", "-o", required=True, help="output folder for shard files")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)


def _p_watch(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--state", default="watch-state.json")
    p.add_argument("--output", "-o", default="watch-report.json")
    p.add_argument("--cycles", type=int, default=1, help="bounded number of poll cycles")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between cycles")
    p.add_argument("--action", default="validate", help="what a change should queue")


OPS = {
    "run": (_p_run, op_run),
    "recover": (_p_recover, op_recover),
    "farm": (_p_farm, op_farm),
    "watch": (_p_watch, op_watch),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "pipeline",
        "spriteforge pipeline: orchestration, recovery, sharding and watch plans",
        OPS,
    ))
