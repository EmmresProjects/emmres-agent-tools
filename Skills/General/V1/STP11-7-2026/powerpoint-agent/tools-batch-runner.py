#!/usr/bin/env python3
"""Batch runner for the powerpoint-agent skill tool.

Executes a validated job manifest through the same dispatcher the CLI uses, so
a batch job can call any supported command.  Safety features:

* the manifest is schema-validated before anything runs
* jobs that modify artifacts (repair/optimize/replace) require ``--approve``
* per-job timeouts (cooperative) and configurable retries
* outputs are staged in a temporary folder and published only when the whole
  batch succeeds, which gives a clean rollback story
* a JSONL log plus a JSON/Markdown summary are written next to the manifest

The runner never invokes a shell: jobs are dispatched as in-process commands
with argument dictionaries.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

CORE = None
APPROVAL_COMMANDS = {"repair", "optimize", "replace"}
# Path arguments that name artifacts the job produces. They are staged
# automatically even when the manifest does not spell out an "outputs" map.
OUTPUT_ARG_KEYS = ("out", "out_dir", "markdown", "csv", "pdf", "report", "json_report", "audit_out")


def bind(core_module) -> None:
    global CORE
    CORE = core_module


@dataclass
class JobResult:
    job_id: str
    command: str
    status: str
    attempts: int
    duration_s: float
    exit_code: int = 0
    data: Any = None
    error: str = ""
    error_code: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "command": self.command,
            "status": self.status,
            "attempts": self.attempts,
            "duration_s": round(self.duration_s, 3),
            "exit_code": self.exit_code,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
        }


@dataclass
class BatchState:
    pending_publishes: List[Dict[str, str]] = field(default_factory=list)


def validate_manifest(manifest: Any, *, allowed_commands: Sequence[str], core=None) -> List[str]:
    core = core or CORE
    problems: List[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    version = str(manifest.get("manifest_version", "1.0"))
    if version != "1.0":
        problems.append("manifest_version '%s' is not supported (expected 1.0)" % version)
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return problems + ["manifest needs a non-empty 'jobs' array"]
    if len(jobs) > 200:
        problems.append("manifest has %d jobs; the limit is 200" % len(jobs))
    seen = set()
    for position, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            problems.append("jobs[%d] must be an object" % position)
            continue
        job_id = str(job.get("id") or "")
        if not job_id:
            problems.append("jobs[%d] needs an 'id'" % position)
        elif job_id in seen:
            problems.append("jobs[%d] duplicates id '%s'" % (position, job_id))
        seen.add(job_id)
        command = str(job.get("command") or "")
        if command not in allowed_commands:
            problems.append(
                "jobs[%d] uses unknown command '%s' (allowed: %s)" % (position, command, ", ".join(sorted(allowed_commands)))
            )
        args = job.get("args")
        if args is not None and not isinstance(args, dict):
            problems.append("jobs[%d].args must be an object" % position)
        outputs = job.get("outputs")
        if outputs is not None:
            if not isinstance(outputs, dict):
                problems.append("jobs[%d].outputs must be an object mapping arg name -> path" % position)
            else:
                for key, value in outputs.items():
                    if not isinstance(value, str) or not value.strip():
                        problems.append("jobs[%d].outputs.%s must be a non-empty path string" % (position, key))
    options = manifest.get("options")
    if options is not None and not isinstance(options, dict):
        problems.append("options must be an object")
    return problems


def _job_timeout(job: Dict[str, Any], manifest: Dict[str, Any], config: Dict[str, Any]) -> float:
    options = manifest.get("options") or {}
    value = job.get("timeout_seconds", options.get("timeout_seconds"))
    if value is None:
        value = CORE.cfg(config, "batch.timeout_seconds", 600)
    return float(value)


def run_batch(
    manifest: Dict[str, Any],
    *,
    manifest_path: str,
    dispatch: Callable[..., Dict[str, Any]],
    allowed_commands: Sequence[str],
    config: Dict[str, Any],
    log=None,
    approve: bool = False,
    dry_run: bool = False,
    out_dir: Optional[str] = None,
    core=None,
) -> Dict[str, Any]:
    """Execute a job manifest. Returns a structured batch report."""

    core = core or CORE
    log = log or core.RunLog()
    problems = validate_manifest(manifest, allowed_commands=allowed_commands, core=core)
    if problems:
        raise core.ValidationError(
            "Batch manifest is invalid.",
            hint="Fix the listed items and rerun; nothing was executed.",
            details={"problems": problems, "manifest": os.path.basename(manifest_path)},
        )

    jobs: List[Dict[str, Any]] = manifest["jobs"]
    options = manifest.get("options") or {}
    stop_on_error = bool(options.get("stop_on_error", CORE.cfg(config, "batch.stop_on_error", False)))
    retries = int(options.get("retries", CORE.cfg(config, "batch.retries", 0)))
    stage_outputs = bool(options.get("stage_outputs", CORE.cfg(config, "batch.stage_outputs", True)))
    publish_at_end = bool(options.get("publish_at_end", True))
    base_dir = os.path.dirname(os.path.abspath(manifest_path))

    needs_approval = [job["id"] for job in jobs if str(job.get("command")) in APPROVAL_COMMANDS]
    if needs_approval and not dry_run and not approve:
        raise core.SafetyError(
            "Batch contains write commands that need explicit approval: %s" % ", ".join(needs_approval),
            hint="Rerun with --approve, or mark those jobs as omitted from the manifest.",
            details={"jobs": needs_approval},
        )

    # Never let a run leave artifacts inside the skill folder itself.
    guard_root = os.path.abspath(out_dir) if out_dir else base_dir
    skill_root = os.path.abspath(core.skill_root())
    if guard_root == skill_root or guard_root.startswith(skill_root + os.sep):
        raise core.UsageError(
            "Batch outputs would be written inside the skill folder: %s" % guard_root,
            hint="Pass --out-dir <dir> so a run never leaves files inside the skill folder.",
            details={"output_root": guard_root, "skill_root": skill_root},
        )

    report_dir = out_dir or base_dir
    os.makedirs(report_dir, exist_ok=True)
    log_path = os.path.join(report_dir, "batch-log.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)

    staging_root = tempfile.mkdtemp(prefix="pptx-agent-batch-")
    state = BatchState()
    results: List[JobResult] = []
    batch_started = time.monotonic()

    # Inputs resolve against the manifest's own folder, so a manifest stays
    # portable. Outputs resolve against --out-dir when the caller supplied one,
    # which is how a batch is run without writing into the skill folder.
    output_root = os.path.abspath(out_dir) if out_dir else base_dir

    def resolve_output(value: str) -> str:
        if os.path.isabs(value):
            return value
        return os.path.normpath(os.path.join(output_root, value))

    def resolve_path(value: str) -> str:
        if os.path.isabs(value):
            return value
        candidate = os.path.normpath(os.path.join(base_dir, value))
        if os.path.exists(candidate) or output_root == base_dir:
            return candidate
        # A later job may consume an earlier job's output, which now lives under
        # --out-dir rather than beside the manifest.
        relocated = os.path.normpath(os.path.join(output_root, value))
        if os.path.exists(relocated):
            return relocated
        return candidate

    try:
        for job in jobs:
            job_id = str(job["id"])
            command = str(job["command"])
            args = dict(job.get("args") or {})
            outputs = dict(job.get("outputs") or {})
            for key in OUTPUT_ARG_KEYS:
                if key in args and isinstance(args[key], str) and args[key] and key not in outputs:
                    outputs[key] = args[key]
            path_keys = (
                "input", "in", "a", "b", "brief", "base", "config", "rules", "policy", "manifest", "spec",
                "out", "out_dir", "markdown", "csv", "pdf", "report", "json_report", "audit_out",
                "rule_pack", "policy", "sections_json",
            )
            for key, value in args.items():
                if key in path_keys and isinstance(value, str) and value:
                    args[key] = resolve_path(value)
            staging_dir = os.path.join(staging_root, job_id)
            os.makedirs(staging_dir, exist_ok=True)
            final_targets: Dict[str, str] = {}
            if stage_outputs and outputs:
                for arg_name, final_path in outputs.items():
                    final_abs = resolve_output(final_path)
                    final_targets[arg_name] = final_abs
                    if arg_name.endswith("_dir"):
                        args[arg_name] = os.path.join(staging_dir, os.path.basename(final_abs) or "out")
                    else:
                        args[arg_name] = os.path.join(staging_dir, os.path.basename(final_abs))
            elif outputs:
                for arg_name, final_path in outputs.items():
                    args[arg_name] = resolve_output(final_path)

            if dry_run:
                results.append(
                    JobResult(
                        job_id=job_id,
                        command=command,
                        status="planned",
                        attempts=0,
                        duration_s=0.0,
                        data={"args": {k: v for k, v in args.items()}, "outputs": outputs},
                    )
                )
                continue

            attempts = 0
            result: Optional[JobResult] = None
            while attempts <= max(0, retries):
                attempts += 1
                deadline = core.Deadline(_job_timeout(job, manifest, config))
                started = time.monotonic()
                try:
                    data = dispatch(command, args, deadline)
                    result = JobResult(
                        job_id=job_id,
                        command=command,
                        status="succeeded",
                        attempts=attempts,
                        duration_s=time.monotonic() - started,
                        data=data,
                    )
                    break
                except core.AgentError as exc:
                    result = JobResult(
                        job_id=job_id,
                        command=command,
                        status="failed",
                        attempts=attempts,
                        duration_s=time.monotonic() - started,
                        exit_code=exc.exit_code,
                        error=exc.message,
                        error_code=exc.code,
                        data=exc.details,
                    )
                    if attempts <= retries:
                        log.warn("job failed, retrying", job=job_id, attempt=attempts, error=exc.message)
                        continue
                except Exception as exc:  # unexpected programming error
                    result = JobResult(
                        job_id=job_id,
                        command=command,
                        status="error",
                        attempts=attempts,
                        duration_s=time.monotonic() - started,
                        exit_code=core.EXIT_RUNTIME,
                        error="%s: %s" % (type(exc).__name__, exc),
                        error_code="E_UNEXPECTED",
                    )
                    if attempts <= retries:
                        continue
            assert result is not None
            results.append(result)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(result.as_dict(), ensure_ascii=False, default=str) + "\n")
            if result.status in ("failed", "error"):
                for staged in _staged_files(staging_dir):
                    if os.path.isfile(staged):
                        os.remove(staged)
                if stop_on_error:
                    log.warn("stopping batch after failure", job=job_id)
                    break
            elif state.pending_publishes or final_targets:
                state.pending_publishes.append({"job_id": job_id, "staging": staging_dir, "targets": final_targets})

        all_ok = all(result.status == "succeeded" for result in results) if not dry_run else True
        published: List[Dict[str, str]] = []
        if not dry_run and publish_at_end and all_ok and not stage_outputs:
            published = []
        if not dry_run and publish_at_end and all_ok:
            for item in state.pending_publishes:
                for arg_name, final_path in item["targets"].items():
                    staged_path = None
                    if os.path.isdir(os.path.join(item["staging"], os.path.basename(final_path))):
                        staged_path = os.path.join(item["staging"], os.path.basename(final_path))
                    for candidate in _staged_files(item["staging"]):
                        if os.path.basename(candidate) == os.path.basename(final_path):
                            staged_path = candidate
                            break
                    if not staged_path or not os.path.exists(staged_path):
                        continue
                    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
                    if os.path.isdir(staged_path):
                        if os.path.isdir(final_path):
                            shutil.rmtree(final_path)
                        shutil.move(staged_path, final_path)
                    else:
                        # the batch owns the paths its manifest declares, so a
                        # successful staged artifact replaces an earlier one
                        if os.path.exists(final_path):
                            os.remove(final_path)
                        shutil.move(staged_path, final_path)
                    published.append({"job_id": item["job_id"], "arg": arg_name, "path": final_path})
        rollback = (not all_ok) and stage_outputs and publish_at_end and not dry_run
        if rollback:
            for item in state.pending_publishes:
                for staged in _staged_files(item["staging"]):
                    if os.path.isfile(staged):
                        os.remove(staged)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    summary = {
        "manifest": os.path.basename(manifest_path),
        "generated_at": core.utc_now_iso(),
        "dry_run": bool(dry_run),
        "approved": bool(approve),
        "job_count": len(jobs),
        "executed": len([r for r in results if r.status != "planned"]),
        "succeeded": len([r for r in results if r.status == "succeeded"]),
        "failed": len([r for r in results if r.status in ("failed", "error")]),
        "duration_s": round(time.monotonic() - batch_started, 3),
        "stop_on_error": stop_on_error,
        "retries": retries,
        "stage_outputs": stage_outputs,
        "publish_at_end": publish_at_end,
        "output_root": output_root,
        "published": published,
        "jobs": [result.as_dict() for result in results],
        "log": log_path,
    }
    return summary


def _staged_files(directory: str) -> List[str]:
    found: List[str] = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            found.append(os.path.join(root, name))
    return found


def batch_report_markdown(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Batch report")
    lines.append("")
    lines.append("- Manifest: `%s`" % summary.get("manifest", ""))
    lines.append("- Jobs: %s (succeeded %s, failed %s)" % (summary.get("job_count"), summary.get("succeeded"), summary.get("failed")))
    lines.append("- Duration: %.2fs" % summary.get("duration_s", 0.0))
    lines.append("- Dry run: %s" % ("yes" if summary.get("dry_run") else "no"))
    lines.append("- Stage outputs: %s · publish at end: %s" % (summary.get("stage_outputs"), summary.get("publish_at_end")))
    if summary.get("output_root"):
        lines.append("- Output root: `%s`" % summary.get("output_root"))
    lines.append("")
    lines.append("| Job | Command | Status | Attempts | Seconds |")
    lines.append("| --- | --- | --- | --- | --- |")
    for job in summary.get("jobs", []):
        lines.append(
            "| %s | %s | %s | %s | %.2f |"
            % (job["job_id"], job["command"], job["status"], job["attempts"], job["duration_s"])
        )
    lines.append("")
    failures = [job for job in summary.get("jobs", []) if job["status"] in ("failed", "error")]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for job in failures:
            lines.append("- **%s** (%s): %s" % (job["job_id"], job.get("error_code", ""), job.get("error", "")))
        lines.append("")
    published = summary.get("published") or []
    if published:
        lines.append("## Published outputs")
        lines.append("")
        for item in published:
            lines.append("- `%s` -> `%s`" % (item["job_id"], item["path"]))
        lines.append("")
    elif summary.get("stage_outputs") and not summary.get("dry_run"):
        lines.append("_No outputs were published; staged files are discarded when a batch fails._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
