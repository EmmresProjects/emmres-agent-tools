#!/usr/bin/env python3
"""Bounded, approval-gated command runner for the Offline Engineering Workbench.

Design rules:
  * no shell: argv is executed directly (shell=False), so metacharacters are data
  * hard timeout with process-group termination and cleanup
  * stdout/stderr are capped and marked truncated
  * destructive, privileged, publishing, and network commands require --approve
  * every attempt is appended to a JSONL audit log with secrets redacted
  * dry-run resolves the command and risk classification without executing
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

RISK_ORDER = {"normal": 0, "medium": 1, "high": 2, "critical": 3}


def _load_core():
    existing = sys.modules.get("wb_core")
    if existing is not None and getattr(existing, "VERSION", None):
        return existing
    path = Path(__file__).resolve().parent / "tools-workbench-core.py"
    spec = importlib.util.spec_from_file_location("wb_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load tools-workbench-core.py at %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wb_core"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def classify(argv: Sequence[str], policy: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """Return the highest risk level and the matching approval reasons."""
    joined = " ".join(str(item) for item in argv)
    risk = "normal"
    reasons: List[Dict[str, Any]] = []
    for entry in policy.get("approval_required_patterns", []):
        pattern = entry.get("pattern")
        if not pattern:
            continue
        try:
            matched = core.re.search(pattern, joined, core.re.IGNORECASE)
        except core.re.error:
            continue
        if not matched:
            continue
        reasons.append({
            "id": entry.get("id", "unspecified"),
            "risk": entry.get("risk", "high"),
            "reason": entry.get("reason", "Command matches an approval-required pattern."),
            "matched": core.redact(matched.group(0)),
        })
        candidate = entry.get("risk", "high")
        if RISK_ORDER.get(candidate, 2) > RISK_ORDER.get(risk, 0):
            risk = candidate
    return risk, reasons


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _execute(argv: Sequence[str], cwd: Path, timeout: int, max_output: int,
             env: Dict[str, str]) -> Dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen(
        [str(item) for item in argv],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
        shell=False,
    )
    out = bytearray()
    err = bytearray()
    truncated = {"stdout": False, "stderr": False}

    def reader(stream, buffer, key):
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            remaining = max_output - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            if len(buffer) >= max_output:
                truncated[key] = True

    threads = [
        threading.Thread(target=reader, args=(process.stdout, out, "stdout"), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, err, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            exit_code = 124
        if timed_out:
            exit_code = 124
    for thread in threads:
        thread.join(timeout=10)
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": out.decode("utf-8", errors="replace"),
        "stderr": err.decode("utf-8", errors="replace"),
        "output_truncated": truncated["stdout"] or truncated["stderr"],
        "stdout_truncated": truncated["stdout"],
        "stderr_truncated": truncated["stderr"],
    }


def _append_log(log_path: Optional[Path], record: Dict[str, Any]) -> None:
    if not log_path:
        return
    try:
        if log_path.parent and not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        print("warning: could not write run log %s: %s" % (log_path, exc), file=sys.stderr)


def run_command(argv: Sequence[str], cwd: os.PathLike | str,
                timeout: int = 300, retries: int = 0,
                max_output_bytes: int = 200000, approve: bool = False,
                dry_run: bool = False, log_path: Optional[os.PathLike | str] = None,
                policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute or plan a bounded command. Never raises for command failure."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise core.WorkbenchError("command-empty", "A command argv is required.",
                                  {"argv": list(argv or [])}, core.EXIT_USAGE)
    normalized = [str(item) for item in argv]
    if any("\x00" in item for item in normalized):
        raise core.WorkbenchError("command-invalid", "Command arguments may not contain NUL bytes.",
                                  {}, core.EXIT_USAGE)
    if not Path(cwd).is_dir():
        raise core.WorkbenchError("cwd-missing", "Working directory does not exist: %s" % cwd,
                                  {"cwd": str(cwd)}, core.EXIT_USAGE)
    policy = policy or core.load_policy()
    timeout = max(1, min(int(timeout), 3600))
    retries = max(0, min(int(retries), 5))
    max_output_bytes = max(1024, min(int(max_output_bytes), 5242880))
    risk, reasons = classify(normalized, policy)
    log_target = Path(log_path).expanduser() if log_path else None
    base = {
        "tool": core.TOOL_NAME,
        "tool_version": core.VERSION,
        "command": normalized,
        "command_display": core.redact(" ".join(normalized), 400),
        "cwd": str(Path(cwd).resolve()),
        "risk": risk,
        "reasons": reasons,
        "approval": {"required": risk != "normal", "granted": bool(approve)},
        "timeout_seconds": timeout,
        "retries": retries,
        "max_output_bytes": max_output_bytes,
        "log_path": str(log_target) if log_target else None,
        "generated_at": core.now_iso(),
    }

    if dry_run:
        result = dict(base)
        result.update({
            "status": "dry-run",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 0,
            "attempts": [],
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "message": "Dry run: command resolved but not executed.",
            "would_require_approval": risk != "normal",
        })
        _append_log(log_target, {"event": "dry-run", **{k: base[k] for k in
                     ("command_display", "cwd", "risk", "reasons", "generated_at")}})
        return result

    if risk != "normal" and not approve:
        result = dict(base)
        result.update({
            "status": "blocked",
            "exit_code": core.EXIT_BLOCKED,
            "timed_out": False,
            "duration_ms": 0,
            "attempts": [],
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "message": "Command requires explicit approval before it can run.",
            "required_approval": sorted({reason["id"] for reason in reasons}),
        })
        _append_log(log_target, {"event": "blocked", **{k: base[k] for k in
                     ("command_display", "cwd", "risk", "reasons", "generated_at")}})
        return result

    env = dict(os.environ)
    env.setdefault("SOURCE_DATE_EPOCH", "0")
    env["PYTHONUNBUFFERED"] = "1"
    attempts: List[Dict[str, Any]] = []
    final: Optional[Dict[str, Any]] = None
    for attempt in range(retries + 1):
        outcome = _execute(normalized, Path(cwd), timeout, max_output_bytes, env)
        record = {
            "attempt": attempt + 1,
            "exit_code": outcome["exit_code"],
            "timed_out": outcome["timed_out"],
            "duration_ms": outcome["duration_ms"],
            "stdout_bytes": len(outcome["stdout"]),
            "stderr_bytes": len(outcome["stderr"]),
            "output_truncated": outcome["output_truncated"],
        }
        attempts.append(record)
        _append_log(log_target, {"event": "attempt", "attempt": record,
                                 "command": base["command_display"], "cwd": base["cwd"],
                                 "risk": risk, "generated_at": core.now_iso()})
        final = outcome
        if outcome["exit_code"] == 0 or risk != "normal":
            break
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))

    assert final is not None
    result = dict(base)
    result.update({
        "status": "ok" if final["exit_code"] == 0 and not final["timed_out"] else "failed",
        "exit_code": final["exit_code"],
        "timed_out": final["timed_out"],
        "duration_ms": final["duration_ms"],
        "attempts": attempts,
        "stdout": final["stdout"],
        "stderr": final["stderr"],
        "output_truncated": final["output_truncated"],
        "stdout_truncated": final["stdout_truncated"],
        "stderr_truncated": final["stderr_truncated"],
    })
    if final["timed_out"]:
        result["message"] = "Command exceeded the %d second timeout and was terminated." % timeout
    elif final["exit_code"] != 0:
        result["message"] = "Command exited with status %d." % final["exit_code"]
    else:
        result["message"] = "Command completed successfully."
    _append_log(log_target, {"event": "completed", "status": result["status"],
                             "exit_code": result["exit_code"],
                             "timed_out": result["timed_out"],
                             "duration_ms": result["duration_ms"],
                             "attempts": len(attempts),
                             "command": base["command_display"], "cwd": base["cwd"],
                             "generated_at": core.now_iso()})
    return result
