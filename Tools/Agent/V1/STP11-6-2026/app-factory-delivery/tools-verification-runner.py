#!/usr/bin/env python3
"""Bounded verification runner for the app-factory-delivery Skill Tool.

Plans and (only after exact approval) executes the verification commands declared
by a delivery preset. Safety rules enforced here:

* commands come from reviewed preset data, never from free-form user strings
  unless the base executable is on the configured allowlist;
* the base executable must be allowlisted, and shell metacharacters are rejected
  because nothing is ever executed through a shell;
* every command has a per-command timeout and retry cap, the whole run has a
  total time budget, and the total run has a maximum command count;
* execution requires the approval token derived from the plan hash, so the exact
  plan reviewed is the plan that runs;
* the child process receives a minimal environment and captured output that is
  truncated before it reaches the report.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

UNSAFE_FRAGMENTS = ["&&", "||", ";", "|", "`", "$(", ">", "<", "\n"]


class ApprovalRequired(RuntimeError):
    """Raised when execution is requested without the exact plan approval token."""

    def __init__(self, message: str, token: str, plan: dict):
        super().__init__(message)
        self.token = token
        self.plan = plan


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plan_token(plan: dict) -> str:
    """Deterministic approval token for a plan (independent of review ordering)."""
    canonical = json.dumps(
        {
            "project": plan.get("project", ""),
            "preset": plan.get("preset", ""),
            "commands": [{"id": item["id"], "command": item["command"]} for item in plan.get("commands", [])],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "APP-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10].upper()


def _validate_command(command: str, allowlist: list[str], command_id: str) -> list[str]:
    if not command or not command.strip():
        raise ValueError(f"command '{command_id}' is empty")
    for fragment in UNSAFE_FRAGMENTS:
        if fragment in command:
            raise ValueError(
                f"command '{command_id}' contains the shell fragment {fragment!r}; "
                "commands run without a shell, so pipe/redirect/chaining syntax is not allowed"
            )
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise ValueError(f"command '{command_id}' cannot be tokenized: {error}") from error
    if not argv:
        raise ValueError(f"command '{command_id}' is empty after tokenization")
    base = argv[0]
    allowed = {item.strip() for item in allowlist if item and item.strip()}
    if base.startswith("./"):
        if base not in allowed:
            raise ValueError(f"command '{command_id}' uses '{base}', which is not on the verification allowlist")
        return argv
    if os.path.isabs(base):
        raise ValueError(f"command '{command_id}' must not use an absolute executable path: {base}")
    if base not in allowed:
        raise ValueError(
            f"command '{command_id}' uses '{base}', which is not on the verification allowlist "
            f"({', '.join(sorted(allowed))})"
        )
    return argv


def _resolve_local_executable(argv: list[str], cwd: str) -> str | None:
    base = argv[0]
    if base.startswith("./"):
        path = os.path.join(cwd, base[2:])
        if not os.path.isfile(path):
            return f"local executable not found: {base}"
        if not os.access(path, os.X_OK):
            return f"local executable is not executable: {base}"
        return None
    if shutil.which(base) is None:
        return f"executable not found on PATH: {base}"
    return None


def build_plan(preset_key: str, preset: dict, limits: dict, project_dir: str, extra_commands: list[str] | None = None, project_name: str = "") -> dict:
    """Validate and freeze the verification plan for one preset."""
    verify_limits = limits.get("verify", {}) if limits else {}
    allowlist = verify_limits.get("allowed_executables", ["python3"])
    per_command_timeout = int(verify_limits.get("per_command_timeout_seconds", 300))
    max_retries = int(verify_limits.get("max_retries", 2))
    max_commands = int(verify_limits.get("max_commands", 12))
    total_budget = int(verify_limits.get("total_timeout_seconds", 900))

    commands = []
    for spec in preset.get("verification", []) or []:
        argv = _validate_command(spec["command"], allowlist, spec["id"])
        timeout = min(int(spec.get("timeout", per_command_timeout)), per_command_timeout)
        retries = min(int(spec.get("retries", 0)), max_retries)
        commands.append(
            {
                "id": spec["id"],
                "label": spec.get("label", spec["id"]),
                "command": spec["command"],
                "argv": argv,
                "required": bool(spec.get("required", False)),
                "timeout_seconds": timeout,
                "retries": retries,
                "origin": "preset",
            }
        )
    for index, command in enumerate(extra_commands or [], start=1):
        command_id = f"extra-{index}"
        argv = _validate_command(command, allowlist, command_id)
        commands.append(
            {
                "id": command_id,
                "label": f"Extra command {index}",
                "command": command,
                "argv": argv,
                "required": False,
                "timeout_seconds": per_command_timeout,
                "retries": 0,
                "origin": "request",
            }
        )

    if not commands:
        raise ValueError(f"preset '{preset_key}' declares no verification commands")
    if len(commands) > max_commands:
        raise ValueError(f"plan has {len(commands)} commands, above max_commands ({max_commands})")

    plan = {
        "schema_version": "1.0",
        "artifact": "verification-plan",
        "project": project_name,
        "preset": preset_key,
        "project_dir": ".",
        "created_utc": _now(),
        "limits": {
            "per_command_timeout_seconds": per_command_timeout,
            "total_timeout_seconds": total_budget,
            "max_retries": max_retries,
            "max_commands": max_commands,
            "max_output_chars": int(verify_limits.get("max_output_chars", 6000)),
        },
        "allowlist": sorted({item.strip() for item in allowlist if item and item.strip()}),
        "commands": commands,
        "approval": {
            "required": True,
            "reason": "Verification executes project toolchains in the workspace; inspect the commands first, then approve the exact plan.",
        },
    }
    plan["token"] = plan_token(plan)
    if not os.path.isdir(project_dir):
        raise ValueError(f"project directory not found: {project_dir}")
    return plan


def _child_env() -> dict:
    keep = ["PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE"]
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "1"
    return env


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True


def execute_plan(plan: dict, project_dir: str, approve_token: str | None, dry_run: bool = False) -> dict:
    """Run (or describe) the frozen plan. Returns a structured report."""
    if dry_run:
        return {
            "schema_version": "1.0",
            "artifact": "verification-report",
            "mode": "dry-run",
            "project": plan.get("project", ""),
            "preset": plan.get("preset", ""),
            "generated_utc": _now(),
            "token": plan["token"],
            "plan": plan,
            "results": [],
            "pass": False,
            "score": 0.0,
            "detail": "dry run only: nothing was executed",
            "next": f"approve with --approve {plan['token']} to execute these commands",
        }

    if not approve_token:
        raise ApprovalRequired(
            "verification execution requires approval: inspect the dry-run plan and pass the exact token",
            plan["token"],
            plan,
        )
    if approve_token.strip().upper() != plan["token"]:
        raise ApprovalRequired(
            f"approval token does not match this plan (expected {plan['token']}); re-run --dry-run and review the current plan",
            plan["token"],
            plan,
        )

    limits = plan["limits"]
    max_output = int(limits["max_output_chars"])
    total_budget = float(limits["total_timeout_seconds"])
    env = _child_env()
    started = time.monotonic()
    results = []

    for command in plan["commands"]:
        elapsed = time.monotonic() - started
        remaining = total_budget - elapsed
        if remaining <= 1:
            results.append(
                {
                    "id": command["id"],
                    "label": command["label"],
                    "command": command["command"],
                    "required": command["required"],
                    "status": "skipped",
                    "reason": "total timeout budget exhausted before this command started",
                    "attempts": 0,
                    "duration_seconds": 0.0,
                }
            )
            continue

        blocked_reason = _resolve_local_executable(command["argv"], project_dir)
        if blocked_reason:
            results.append(
                {
                    "id": command["id"],
                    "label": command["label"],
                    "command": command["command"],
                    "required": command["required"],
                    "status": "blocked",
                    "reason": blocked_reason,
                    "attempts": 0,
                    "duration_seconds": 0.0,
                }
            )
            continue

        attempts = []
        status = "failed"
        for attempt in range(1 + int(command["retries"])):
            timeout = max(1.0, min(float(command["timeout_seconds"]), remaining))
            attempt_start = time.monotonic()
            try:
                completed = subprocess.run(
                    command["argv"],
                    cwd=project_dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                duration = round(time.monotonic() - attempt_start, 2)
                stdout, stdout_cut = _truncate(completed.stdout or "", max_output)
                stderr, stderr_cut = _truncate(completed.stderr or "", max_output)
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "exit_code": completed.returncode,
                        "duration_seconds": duration,
                        "stdout": stdout,
                        "stderr": stderr,
                        "output_truncated": stdout_cut or stderr_cut,
                    }
                )
                status = "passed" if completed.returncode == 0 else "failed"
                if status == "passed":
                    break
            except subprocess.TimeoutExpired as expired:
                duration = round(time.monotonic() - attempt_start, 2)
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "exit_code": None,
                        "duration_seconds": duration,
                        "stdout": (expired.stdout or "") if isinstance(expired.stdout, str) else "",
                        "stderr": f"timed out after {timeout:.0f}s",
                        "output_truncated": False,
                    }
                )
                status = "timeout"
            except FileNotFoundError:
                status = "blocked"
                attempts.append({"attempt": attempt + 1, "exit_code": None, "duration_seconds": 0.0, "stdout": "", "stderr": "executable disappeared before execution", "output_truncated": False})
                break
            except OSError as error:
                status = "error"
                attempts.append({"attempt": attempt + 1, "exit_code": None, "duration_seconds": 0.0, "stdout": "", "stderr": f"could not start process: {error}", "output_truncated": False})
                break
            remaining = total_budget - (time.monotonic() - started)
            if remaining <= 1:
                break

        results.append(
            {
                "id": command["id"],
                "label": command["label"],
                "command": command["command"],
                "required": command["required"],
                "status": status,
                "reason": "" if status == "passed" else (attempts[-1]["stderr"].strip().splitlines()[-1] if attempts and attempts[-1]["stderr"].strip() else "non-zero exit"),
                "attempts": len(attempts),
                "duration_seconds": round(sum(item["duration_seconds"] for item in attempts), 2),
                "attempt_detail": attempts,
            }
        )

    required = [item for item in results if item["required"]]
    optional = [item for item in results if not item["required"]]
    required_passed = [item for item in required if item["status"] == "passed"]
    optional_passed = [item for item in optional if item["status"] == "passed"]
    total_weight = 2 * len(required) + len(optional)
    earned = 2 * len(required_passed) + len(optional_passed)
    score = round(100.0 * earned / total_weight, 1) if total_weight else 0.0
    passed = all(item["status"] == "passed" for item in required) and bool(required)
    blocked = [item["id"] for item in results if item["status"] == "blocked"]

    if not required:
        detail = "no required command in this plan; results are informational only"
    elif passed:
        detail = "all required commands passed"
    elif blocked:
        detail = "blocked: " + ", ".join(blocked)
    else:
        detail = "failed: " + ", ".join(item["id"] for item in required if item["status"] != "passed")

    return {
        "schema_version": "1.0",
        "artifact": "verification-report",
        "mode": "executed",
        "project": plan.get("project", ""),
        "preset": plan.get("preset", ""),
        "generated_utc": _now(),
        "token": plan["token"],
        "approved": True,
        "duration_seconds": round(time.monotonic() - started, 2),
        "results": results,
        "pass": passed,
        "score": score,
        "detail": detail,
        "blocked": blocked,
    }


def render_report_md(report: dict) -> str:
    plan = report.get("plan", {})
    lines = [
        "# Verification report",
        "",
        f"- Generated: {report['generated_utc']}",
        f"- Mode: {report['mode']}",
        f"- Preset: `{report.get('preset') or 'none'}`",
        f"- Approval token: `{report['token']}`",
    ]
    if report["mode"] == "dry-run":
        lines += [
            "- Executed: **no** — dry run only",
            "",
            "## Planned commands",
            "",
            "| Id | Required | Timeout | Command |",
            "| --- | --- | --- | --- |",
        ]
        for command in plan.get("commands", []):
            lines.append(f"| {command['id']} | {'yes' if command['required'] else 'no'} | {command['timeout_seconds']}s | `{command['command']}` |")
        lines += [
            "",
            f"Approve and run: `--approve {report['token']}`",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"- Result: {'PASS' if report['pass'] else 'FAIL'} ({report['score']}/100)",
        f"- Detail: {report['detail']}",
        f"- Duration: {report.get('duration_seconds', 0)}s",
        "",
        "| Id | Required | Status | Attempts | Duration | Command |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in report["results"]:
        lines.append(
            f"| {result['id']} | {'yes' if result['required'] else 'no'} | {result['status']} | {result['attempts']} | "
            f"{result['duration_seconds']}s | `{result['command']}` |"
        )

    failures = [item for item in report["results"] if item["status"] != "passed"]
    if failures:
        lines += ["", "## Non-passing commands", ""]
        for item in failures:
            lines.append(f"### {item['id']} — {item['status']}")
            lines.append("")
            lines.append(f"- Command: `{item['command']}`")
            if item.get("reason"):
                lines.append(f"- Reason: {item['reason']}")
            for attempt in item.get("attempt_detail", [])[-1:]:
                if attempt.get("stderr"):
                    lines += ["", "```text", attempt["stderr"].strip()[:2000], "```"]
                if attempt.get("stdout"):
                    lines += ["", "```text", attempt["stdout"].strip()[:2000], "```"]
            lines.append("")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Small standalone helper: show the plan/token for a preset without the main CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="tools-verification-runner.py",
        description="Inspect verification plans for the app-factory-delivery presets. Execution is driven by tools-app-factory.py verify.",
    )
    parser.add_argument("--presets", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset-stack-presets.json"))
    parser.add_argument("--workflow", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset-factory-workflow.json"))
    parser.add_argument("--preset", required=True)
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args(argv)

    with open(args.presets, "r", encoding="utf-8") as handle:
        presets = json.load(handle)
    with open(args.workflow, "r", encoding="utf-8") as handle:
        workflow = json.load(handle)
    preset = presets.get("presets", {}).get(args.preset)
    if preset is None:
        print(json.dumps({"ok": False, "error": f"unknown preset '{args.preset}'"}), file=sys.stderr)
        return 2
    try:
        plan = build_plan(args.preset, preset, workflow.get("limits", {}), args.project_dir)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "token": plan["token"], "commands": plan["commands"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
