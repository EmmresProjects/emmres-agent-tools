#!/usr/bin/env python3
"""Shared engine for the Execution Operations skill tool.

File name: tools-nexuslib.py. It is loaded by path from tools-nexus.py and registered
in sys.modules as "nexuslib", because the tools- prefix is not a valid Python module name.

This module holds every reusable implementation detail: configuration, path
scope validation, command construction, policy scanning, secret redaction,
exit-code interpretation, bounded process execution with stall detection and
retries, DAG orchestration, dataset transforms, SQLite access, HTTP access with
an explicit host allowlist, and report rendering.

It is imported by tools-nexus.py (the CLI) and is not meant to be run directly.
Standard library only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import random
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
TOOL_NAME = "execution-operations"

# --------------------------------------------------------------------------- #
# Errors and structured output
# --------------------------------------------------------------------------- #


class NexusError(Exception):
    """Structured, user-facing error with a stable code and process exit code."""

    def __init__(self, message, code="error", details=None, exit_code=2):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.exit_code = exit_code

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_correlation_id(prefix="nex"):
    return "%s-%s-%04x" % (prefix, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), random.getrandbits(16))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def sha256_file(path, chunk=131072):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write(path, text, encoding="utf-8"):
    """Write via a sibling temp file then replace, so readers never see a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(tmp, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return str(path)


def read_text(path, max_bytes=5 * 1024 * 1024):
    p = Path(path)
    if not p.is_file():
        raise NexusError("file not found: %s" % path, code="file_not_found", details={"path": str(path)})
    size = p.stat().st_size
    if size > max_bytes:
        raise NexusError(
            "file exceeds the readable limit",
            code="file_too_large",
            details={"path": str(path), "size_bytes": size, "limit_bytes": max_bytes},
        )
    return p.read_text(encoding="utf-8", errors="replace")


def tail_lines(text, limit):
    if limit is None or limit <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    head = max(1, limit // 4)
    body = lines[:head] + ["... [%d lines elided] ..." % (len(lines) - limit)] + lines[-(limit - head):]
    return "\n".join(body)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

CONFIG_ENV = "NEXUS_CONFIG"
DEFAULT_SCRIPTS_DIR = Path(__file__).resolve().parent


def _deep_merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Layered configuration: defaults -> --config file -> explicit overrides."""

    def __init__(self, config_path=None, overrides=None, scripts_dir=None):
        self.scripts_dir = Path(scripts_dir or DEFAULT_SCRIPTS_DIR)
        path = config_path or os.environ.get(CONFIG_ENV)
        base_path = self.scripts_dir / "asset-default-config.json"
        if not base_path.is_file():
            raise NexusError(
                "default configuration asset is missing",
                code="config_missing",
                details={"expected": "execution-operations/asset-default-config.json"},
            )
        self.data = json.loads(base_path.read_text(encoding="utf-8"))
        self.sources = [str(base_path.name)]
        if path:
            user = Path(path)
            if not user.is_file():
                raise NexusError("config file not found", code="config_not_found", details={"path": str(user)})
            try:
                overlay = json.loads(user.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise NexusError(
                    "config file is not valid JSON",
                    code="config_invalid",
                    details={"path": str(user), "line": exc.lineno, "column": exc.colno, "message": exc.msg},
                )
            self.data = _deep_merge(self.data, overlay)
            self.sources.append(str(user))
        if overrides:
            self.data = _deep_merge(self.data, overrides)

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def asset_path(self, relative):
        return self.resolve_asset(self.data.get(relative) or relative)

    def resolve_asset(self, name):
        p = self.scripts_dir / name
        if not p.is_file():
            raise NexusError(
                "referenced asset is missing",
                code="asset_missing",
                details={"asset": name, "expected": "execution-operations/" + name},
            )
        return p

    def load_asset(self, relative):
        return json.loads(self.asset_path(relative).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Paths and scope
# --------------------------------------------------------------------------- #


def resolve_path(raw, base=None):
    """Resolve a user path relative to base (or cwd) without requiring existence."""
    if raw is None:
        return None
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = Path(base or os.getcwd()) / p
    return p.resolve()


def _within(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def check_scope(path, config, policy=None, label="path", allow_create=False):
    """Validate a path against configured allowed/denied roots and ambiguity rules."""
    p = resolve_path(path)
    rules = (policy or {}).get("scope_rules", {})
    denied = [resolve_path(d) for d in config.get("scope.denied_roots", [])]
    for d in denied:
        if d and _within(p, d):
            raise NexusError(
                "%s is inside a denied root" % label,
                code="scope_denied",
                details={"label": label, "path": str(p), "denied_root": str(d)},
                exit_code=3,
            )
    allowed = [resolve_path(a) for a in config.get("scope.allowed_roots", [])]
    if allowed:
        if not any(_within(p, a) for a in allowed if a):
            raise NexusError(
                "%s is outside the allowed roots" % label,
                code="scope_outside_allowed",
                details={"label": label, "path": str(p), "allowed_roots": [str(a) for a in allowed]},
                exit_code=3,
            )
    elif not config.get("scope.allow_outside_work_root", False):
        # Default posture: stay inside the work root unless explicitly permitted.
        work_root = resolve_path(config.get("paths.work_root", "."))
        if work_root and not _within(p, work_root):
            raise NexusError(
                "%s is outside the work root and scope.allow_outside_work_root is false" % label,
                code="scope_outside_work_root",
                details={
                    "label": label,
                    "path": str(p),
                    "work_root": str(work_root),
                    "fix": "Set paths.work_root or scope.allowed_roots, or pass --allow-outside.",
                },
                exit_code=3,
            )
    if rules.get("require_explicit_target"):
        raw = str(p)
        for pattern in rules.get("ambiguous_globs", []):
            if raw.endswith(pattern.strip("*")) and ("*" in raw or raw.rstrip("/\\") in ("", "/", "~")):
                raise NexusError(
                    "target is ambiguous or too broad",
                    code="scope_ambiguous",
                    details={"path": raw, "reason": rules.get("reason")},
                    exit_code=3,
                )
    if not allow_create and not p.exists():
        raise NexusError(
            "%s does not exist" % label,
            code="path_not_found",
            details={"label": label, "path": str(p)},
        )
    return p


def work_root(config):
    return resolve_path(config.get("paths.work_root", "."))


def state_path(config, filename, create=True):
    root = resolve_path(config.get("paths.state_dir", ".nexus/state"), base=work_root(config))
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root / filename


def run_path(config, filename, create=True):
    root = resolve_path(config.get("paths.run_dir", ".nexus/runs"), base=work_root(config))
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root / filename


# --------------------------------------------------------------------------- #
# Secrets and redaction
# --------------------------------------------------------------------------- #


def compile_patterns(specs):
    out = []
    for spec in specs:
        out.append((spec.get("id", "pattern"), re.compile(spec["pattern"], re.IGNORECASE if spec.get("ignore_case") else 0), spec.get("replacement", "[REDACTED]")))
    return out


def redact(text, config):
    if not text:
        return text
    policy = config.load_asset("policy_file")
    for _pid, rx, repl in compile_patterns(policy["secret_patterns"]):
        text = rx.sub(repl, text)
    return text


def scan_secrets(text):
    """Return findings without printing the matched secret value."""
    findings = []
    for pid, rx, repl in compile_patterns(json.loads((Path(__file__).resolve().parent / "asset-policy-rules.json").read_text(encoding="utf-8"))["secret_patterns"]):
        for match in rx.finditer(text):
            value = match.group(0)
            line = text[: match.start()].count("\n") + 1
            findings.append(
                {
                    "pattern_id": pid,
                    "line": line,
                    "length": len(value),
                    "fingerprint": sha256_text(value)[:12],
                    "preview": repl,
                }
            )
    return findings


# --------------------------------------------------------------------------- #
# Risk policy
# --------------------------------------------------------------------------- #


def assess_risk(command_text, policy, requested_level=None):
    matches = []
    top = "none"
    ranks = policy["risk_levels"]

    def rank(level):
        return ranks.get(level, {"rank": 0})["rank"]

    for rule in policy["rules"]:
        try:
            rx = re.compile(rule["pattern"], re.IGNORECASE if rule.get("ignore_case") else 0)
        except re.error as exc:  # pragma: no cover - guarded by asset validation
            raise NexusError("invalid policy pattern", code="policy_invalid", details={"rule": rule.get("id"), "error": str(exc)})
        if rx.search(command_text):
            matches.append(
                {
                    "rule_id": rule.get("id"),
                    "level": rule["level"],
                    "category": rule.get("category"),
                    "reason": rule.get("reason"),
                    "safer": rule.get("safer"),
                }
            )
            if rank(rule["level"]) > rank(top):
                top = rule["level"]
    if requested_level and rank(requested_level) > rank(top):
        top = requested_level
        matches.append({"rule_id": "requested", "level": requested_level, "category": "declared", "reason": "Risk declared by the caller", "safer": None})
    flags = [m["rule_id"] for m in matches]
    return {"level": top, "rank": rank(top), "matches": matches, "flags": flags}


def required_approval(risk, config):
    levels = config.get("approval.required_levels", ["high", "critical"])
    ranks = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    threshold = min([ranks.get(l, 4) for l in levels]) if levels else 99
    return risk["rank"] >= threshold


# --------------------------------------------------------------------------- #
# Shells and command construction
# --------------------------------------------------------------------------- #

DIALECT_EXECUTABLES = {
    "bash": lambda: shutil.which("bash"),
    "sh": lambda: shutil.which("sh"),
    "zsh": lambda: shutil.which("zsh"),
    "powershell": lambda: shutil.which("pwsh") or shutil.which("powershell"),
    "cmd": lambda: shutil.which("cmd") if os.name == "nt" else None,
}


def normalize_dialect(name, config):
    table = config.load_asset("shell_dialects_file")
    aliases = table.get("aliases", {})
    key = (name or "auto").strip().lower()
    if key == "auto":
        auto = (config.get("execution.default_shell") or "auto").lower()
        if auto != "auto":
            key = auto
        else:
            if os.name == "nt":
                key = "powershell" if DIALECT_EXECUTABLES["powershell"]() else "cmd"
            else:
                key = "bash" if DIALECT_EXECUTABLES["bash"]() else "sh"
    key = aliases.get(key, key)
    if key not in table["dialects"]:
        raise NexusError(
            "unsupported shell dialect",
            code="invalid_shell",
            details={"shell": name, "supported": table["dialects"]},
        )
    exe = DIALECT_EXECUTABLES.get(key, lambda: None)()
    if not exe:
        raise NexusError(
            "shell executable is not available on this host",
            code="shell_unavailable",
            details={"shell": key, "fix": "Install the shell or pass --shell with an available dialect."},
        )
    return key, exe


def build_argv(command, shell_key, exe):
    if shell_key == "cmd":
        return [exe, "/d", "/s", "/c", command]
    if shell_key == "powershell":
        return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    return [exe, "-c", command]


# --------------------------------------------------------------------------- #
# Exit codes and diagnosis
# --------------------------------------------------------------------------- #


def interpret(exit_code, stderr_text, signal_number, exit_table):
    codes = exit_table.get("exit_codes", {})
    signals = exit_table.get("signals", {})
    result = {
        "exit_code": exit_code,
        "signal": None,
        "state": "unknown",
        "category": "unknown",
        "retryable": False,
        "meaning": None,
        "actions": [],
        "pattern_matches": [],
    }
    if exit_code is not None and str(exit_code) in codes:
        entry = codes[str(exit_code)]
        result.update(
            {
                "state": entry["state"],
                "category": entry["category"],
                "retryable": bool(entry["retryable"]),
                "meaning": entry["meaning"],
            }
        )
        result["actions"].append(entry["action"])
    if signal_number:
        for sig, meta in signals.items():
            if meta["number"] == signal_number:
                result["signal"] = sig
                result["state"] = meta["state"]
                result["retryable"] = result["retryable"] or bool(meta["retryable"])
                result["actions"].append("Process ended on %s; confirm cleanup and resume from a checkpoint." % sig)
                break
    if exit_code not in (None, 0) and stderr_text:
        for spec in exit_table.get("stderr_patterns", []):
            if re.search(spec["pattern"], stderr_text, re.IGNORECASE):
                hit = {"id": spec["id"], "state": spec["state"], "category": spec["category"], "retryable": bool(spec["retryable"])}
                result["pattern_matches"].append(hit)
                if result["state"] in ("unknown", "failure", "usage_error"):
                    result["state"] = spec["state"]
                    result["category"] = spec["category"]
                result["retryable"] = result["retryable"] or bool(spec["retryable"])
                result["actions"].append(spec["action"])
    if exit_code == 0:
        result.update({"state": "success", "category": "completion", "retryable": False})
        result["actions"].append("Verify declared artifacts, then record completion evidence.")
    elif exit_code is not None and result["state"] == "unknown":
        result.update({"state": "failure", "category": "general", "retryable": False, "meaning": "Unlisted non-zero exit code"})
        result["actions"].append("Treat as a general failure: read the first error line and probe the earliest bad state.")
    if not result["actions"]:
        result["actions"].append("Capture a diagnostic bundle and the minimal reproducer before changing anything.")
    result["actions"] = list(dict.fromkeys(result["actions"]))
    return result


TRIAGE_LAYERS = [
    ("input", "Input or payload is wrong, incomplete, or malformed."),
    ("state", "Persisted state, cache, lock file, or partial output is inconsistent."),
    ("syntax", "Command, code, or configuration text is invalid."),
    ("dependency", "A required package, binary, or service is missing or the wrong version."),
    ("permissions", "Identity, ownership, ACL, capability, or sandbox policy blocks the action."),
    ("environment", "Runtime version, OS, locale, timezone, or resource limits differ from the expectation."),
    ("network", "Connectivity, DNS, TLS, proxy, or a remote service is unavailable."),
    ("logic", "Code or query behaviour is wrong; the environment is fine."),
    ("resource", "CPU, memory, disk, file handles, or quota are exhausted."),
    ("export", "Output cannot be written, encoded, or serialized."),
    ("presentation", "Output renders incorrectly for the recipient or consumer."),
]

PROBES = {
    "input": ["Re-read the input file and confirm size, encoding, and delimiter", "Validate against the declared contract before rerunning"],
    "state": ["List lock files and checkpoint timestamps", "Compare the last successful artifact hash with the current one"],
    "syntax": ["Run the parser or linter only, with no side effects", "Compare the failing line with the last known-good revision"],
    "dependency": ["Print resolved versions of the top-level dependencies", "Reinstall from the lock file, not floating latest"],
    "permissions": ["Check identity, ownership, and ACLs on the target", "Confirm the process is not confined by a sandbox policy"],
    "environment": ["Print runtime versions, locale, timezone, and limits", "Run the same command in a clean container or venv"],
    "network": ["Resolve the host, then check the port with a bounded timeout", "Confirm the allowlist and proxy settings"],
    "logic": ["Reduce to a minimal reproducer with fixed inputs", "Add one assertion at the earliest wrong state"],
    "resource": ["Check free disk, memory headroom, and open file count", "Reduce batch size or concurrency, then retry"],
    "export": ["Check output path permissions and free space", "Write to a temp path and swap atomically"],
    "presentation": ["Open the produced artifact with its real consumer", "Compare against an approved baseline"],
}


# --------------------------------------------------------------------------- #
# Process execution
# --------------------------------------------------------------------------- #


class _Cancel:
    def __init__(self):
        self.event = threading.Event()
        self.reason = None

    def set(self, reason="requested"):
        self.reason = reason
        self.event.set()


def _terminate(proc, grace):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_command(
    command,
    config,
    shell="auto",
    cwd=None,
    timeout=None,
    heartbeat_enforced=False,
    stall_timeout=None,
    cancel=None,
    capture_dir=None,
    env_extra=None,
    argv_override=None,
):
    """Run one command with a hard deadline, optional heartbeat gating, and cleanup.

    When argv_override is supplied the process is started directly without a shell.
    """
    if argv_override is not None:
        if not isinstance(argv_override, list) or not argv_override or not all(isinstance(a, str) and a for a in argv_override):
            raise NexusError("argv must be a non-empty array of non-empty strings", code="invalid_argv", details={"argv": argv_override})
        if len(argv_override) > 512:
            raise NexusError("argv has too many elements", code="invalid_argv", details={"count": len(argv_override), "limit": 512})
        shell_key, exe, argv = "direct", argv_override[0], list(argv_override)
    else:
        shell_key, exe = normalize_dialect(shell, config)
        argv = build_argv(command, shell_key, exe)

    timeout = float(timeout if timeout is not None else config.get("execution.default_timeout_seconds", 120))
    max_timeout = float(config.get("execution.max_timeout_seconds", 3600))
    if timeout <= 0 or timeout > max_timeout:
        raise NexusError(
            "timeout is outside the permitted range",
            code="invalid_timeout",
            details={"timeout_seconds": timeout, "max_timeout_seconds": max_timeout},
        )
    if len(command) > int(config.get("limits.max_command_length", 8000)):
        raise NexusError(
            "command exceeds the permitted length",
            code="command_too_long",
            details={"length": len(command), "limit": int(config.get("limits.max_command_length", 8000))},
        )
    stall_timeout = float(stall_timeout if stall_timeout is not None else config.get("execution.stall_timeout_seconds", 300))
    grace = float(config.get("execution.kill_grace_seconds", 5))
    max_bytes = int(config.get("execution.max_output_bytes", 200000))
    hb_rx = re.compile(config.get("execution.heartbeat_pattern") or r"\[\?heartbeat\]\?")

    workdir = resolve_path(cwd, base=work_root(config)) if cwd else work_root(config)
    if not Path(workdir).is_dir():
        raise NexusError("working directory does not exist", code="workdir_missing", details={"workdir": str(workdir)})
    check_scope(workdir, config, config.load_asset("policy_file"), label="working directory", allow_create=False)

    env = dict(os.environ)
    for key, value in (env_extra or {}).items():
        env[str(key)] = str(value)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("NO_COLOR", "1")

    captured = {"stdout": [], "stderr": [], "bytes": 0, "heartbeats": 0, "last_line_at": None, "truncated": False}
    lock = threading.Lock()
    started = time.time()

    popen_kwargs = dict(
        cwd=str(workdir),
        env=env,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except FileNotFoundError:
        raise NexusError("shell executable could not be started", code="shell_unavailable", details={"shell": shell_key})
    except OSError as exc:
        raise NexusError("failed to start the command", code="spawn_failed", details={"error": str(exc)})

    def pump(stream, name):
        for line in stream:
            with lock:
                captured["bytes"] += len(line)
                if captured["bytes"] > max_bytes:
                    captured["truncated"] = True
                    continue
                captured[name].append(line.rstrip("\n"))
                captured["last_line_at"] = time.time()
                if name == "stdout" and hb_rx.search(line):
                    captured["heartbeats"] += 1
        stream.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for t in threads:
        t.start()

    deadline = started + timeout
    timed_out = False
    stalled = False
    cancelled = False
    idle_last = started

    while True:
        if proc.poll() is not None:
            break
        now = time.time()
        if now >= deadline:
            timed_out = True
            _terminate(proc, grace)
            break
        if cancel is not None and cancel.event.is_set():
            cancelled = True
            _terminate(proc, grace)
            break
        with lock:
            activity = captured["last_line_at"]
        if activity and activity > idle_last:
            idle_last = activity
        if heartbeat_enforced and (now - idle_last) > stall_timeout:
            stalled = True
            _terminate(proc, grace)
            break
        time.sleep(0.05)

    exit_code = proc.wait()
    for t in threads:
        t.join(timeout=2.0)
    duration = round(time.time() - started, 3)

    stdout_text = redact("\n".join(captured["stdout"]), config)
    stderr_text = redact("\n".join(captured["stderr"]), config)
    idle = round(time.time() - idle_last, 3) if captured["last_line_at"] else round(duration, 3)

    record = {
        "command": redact(command, config),
        "argv": [redact(a, config) for a in argv],
        "shell": shell_key,
        "shell_executable": exe,
        "workdir": str(workdir),
        "exit_code": exit_code,
        "signal": (-exit_code) if exit_code is not None and exit_code < 0 else None,
        "duration_seconds": duration,
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "stalled": stalled,
        "cancelled": cancelled,
        "heartbeats": captured["heartbeats"],
        "idle_seconds": idle,
        "stdout_lines": len(captured["stdout"]),
        "stderr_lines": len(captured["stderr"]),
        "output_truncated": captured["truncated"],
        "stdout": stdout_text,
        "stderr": stderr_text,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    if capture_dir:
        cap_dir = Path(capture_dir)
        cap_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(cap_dir / "stdout.log", stdout_text + "\n")
        atomic_write(cap_dir / "stderr.log", stderr_text + "\n")
        record["captured_to"] = str(cap_dir)
    return record


def backoff_delay(attempt, retry_cfg, rand=None):
    rand = rand or random.random
    base = float(retry_cfg.get("base_delay_seconds", 0.5))
    factor = float(retry_cfg.get("factor", 2.0))
    cap = float(retry_cfg.get("max_delay_seconds", 20.0))
    jitter = float(retry_cfg.get("jitter_ratio", 0.25))
    raw = min(cap, base * (factor ** max(0, attempt - 1)))
    spread = raw * jitter
    return round(max(0.0, raw + rand() * 2 * spread - spread), 3)


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #


def checkpoint_load(path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"corrupt": True, "path": str(p)}


def checkpoint_write(path, payload):
    data = dict(payload)
    data["updated_at"] = now_iso()
    atomic_write(path, json.dumps(data, indent=1, sort_keys=True) + "\n")
    return str(path)


# --------------------------------------------------------------------------- #
# Orchestration (DAG)
# --------------------------------------------------------------------------- #


def default_workflow_path(config):
    return config.asset_path("workflow_schema_file")


def validate_workflow(doc, config):
    """Structural validation without a third-party schema library."""
    errors = []
    warnings = []
    limits = config.get("orchestration", {})
    max_nodes = int(limits.get("max_nodes", 200))
    if not isinstance(doc, dict):
        return ["workflow must be a JSON object"], warnings
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["workflow.nodes must be a non-empty array"], warnings
    if len(nodes) > max_nodes:
        errors.append("workflow has %d nodes, above the limit of %d" % (len(nodes), max_nodes))
    ids = []
    allowed_keys = {"id", "label", "depends_on", "command", "dialect", "argv", "op", "op_args", "noop", "timeout_seconds", "workdir", "retries", "checkpoint", "requires_approval", "artifacts", "success_codes", "continue_on_error"}
    for index, node in enumerate(nodes):
        where = "nodes[%d]" % index
        if not isinstance(node, dict):
            errors.append("%s must be an object" % where)
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", node_id or ""):
            errors.append("%s.id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$" % where)
        else:
            if node_id in ids:
                errors.append("%s.id duplicates '%s'" % (where, node_id))
            ids.append(node_id)
        for key in node:
            if key not in allowed_keys:
                errors.append("%s has unknown property '%s'" % (where, key))
        kinds = [k for k in ("command", "argv", "op", "noop") if k in node]
        if len(kinds) != 1:
            errors.append("%s must define exactly one of command, argv, op, noop (found %s)" % (where, kinds or "none"))
        if "dialect" in node and "command" not in node:
            errors.append("%s.dialect is only valid together with command" % where)
        if "argv" in node and (not isinstance(node["argv"], list) or not node["argv"] or not all(isinstance(a, str) for a in node["argv"])):
            errors.append("%s.argv must be a non-empty array of strings" % where)
        if "timeout_seconds" in node:
            try:
                t = float(node["timeout_seconds"])
                if t <= 0 or t > float(config.get("execution.max_timeout_seconds", 3600)):
                    errors.append("%s.timeout_seconds is outside 0..%s" % (where, config.get("execution.max_timeout_seconds")))
            except (TypeError, ValueError):
                errors.append("%s.timeout_seconds must be a number" % where)
        if "retries" in node and (not isinstance(node["retries"], int) or node["retries"] < 0 or node["retries"] > 10):
            errors.append("%s.retries must be an integer 0..10" % where)
        if "depends_on" in node and not isinstance(node["depends_on"], list):
            errors.append("%s.depends_on must be an array" % where)
    idset = set(ids)
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for dep in node.get("depends_on", []) or []:
            if dep not in idset:
                errors.append("nodes[%d].depends_on references unknown node '%s'" % (index, dep))
            if dep == node.get("id"):
                errors.append("nodes[%d] depends on itself" % index)
    # cycle detection (Kahn)
    graph = {n.get("id"): set(n.get("depends_on", []) or []) for n in nodes if isinstance(n, dict) and n.get("id")}
    removed = set()
    progress = True
    while progress:
        progress = False
        for node_id, deps in graph.items():
            if node_id in removed:
                continue
            if deps <= removed:
                removed.add(node_id)
                progress = True
    leftover = set(graph) - removed
    if leftover:
        errors.append("workflow contains a dependency cycle among: %s" % ", ".join(sorted(leftover)))
    return errors, warnings


def orchestrate(doc, config, cancel=None, resume=True, approve=False, approval_ref=None, capture_root=None):
    errors, _warnings = validate_workflow(doc, config)
    if errors:
        raise NexusError("workflow is invalid", code="workflow_invalid", details={"errors": errors, "schema": "execution-operations/asset-workflow-schema.json"})
    policy = config.load_asset("policy_file")
    exit_table = config.load_asset("exit_codes_file")
    defaults = doc.get("defaults", {}) or {}
    nodes = doc["nodes"]
    max_parallel = int(config.get("orchestration.max_parallel", 4))
    block = bool(config.get("orchestration.block_dependents_on_failure", True))
    ckpt_name = str(doc.get("meta", {}).get("checkpoint", "orchestrator.json"))
    ckpt_file = run_path(config, re.sub(r"[^A-Za-z0-9._-]", "_", ckpt_name))
    state = {"completed": [], "results": {}}
    if resume:
        stored = checkpoint_load(ckpt_file)
        if stored and not stored.get("corrupt"):
            state.update({k: stored.get(k, state[k]) for k in state})

    results = state["results"]
    completed = set(state["completed"])
    order = []
    started = time.time()
    running = {}
    attempts = {n["id"]: 0 for n in nodes}

    def node_ready(node):
        return all(d in completed for d in node.get("depends_on", []) or [])

    def blocked_deps(node):
        return [d for d in node.get("depends_on", []) or [] if results.get(d, {}).get("status") in ("failed", "blocked", "cancelled")]

    pending = [n for n in nodes if n["id"] not in completed]
    while pending or running:
        if cancel is not None and cancel.event.is_set():
            for node in pending:
                results[node["id"]] = {"status": "cancelled", "reason": "cancellation requested"}
                order.append(node["id"])
            pending = []
            break
        launched = False
        for node in list(pending):
            if len(running) >= max_parallel:
                break
            bad = blocked_deps(node)
            if bad:
                results[node["id"]] = {"status": "blocked", "reason": "dependency failed: %s" % ", ".join(bad), "depends_on": bad}
                order.append(node["id"])
                pending.remove(node)
                continue
            if not node_ready(node):
                continue
            if node.get("noop"):
                results[node["id"]] = {"status": "ok", "mode": "noop", "duration_seconds": 0}
                completed.add(node["id"])
                order.append(node["id"])
                pending.remove(node)
                launched = True
                continue
            if node.get("op"):
                results[node["id"]] = _run_op_node(node, config, results)
                status = results[node["id"]]["status"]
                if status == "ok":
                    completed.add(node["id"])
                order.append(node["id"])
                pending.remove(node)
                launched = True
                continue
            # shell / argv node: run in a worker thread
            timeout = float(node.get("timeout_seconds") or defaults.get("timeout_seconds") or config.get("execution.default_timeout_seconds"))
            command = node.get("command")
            text_for_risk = command if command else " ".join(node.get("argv", []))
            risk = assess_risk(text_for_risk, policy, "high" if node.get("requires_approval") else None)
            if (required_approval(risk, config) or node.get("requires_approval")) and not approve:
                results[node["id"]] = {
                    "status": "blocked",
                    "reason": "approval required",
                    "risk": risk,
                    "fix": "Re-run with --approve and --approval-ref after reviewing the risk matches.",
                }
                order.append(node["id"])
                pending.remove(node)
                continue
            retries = int(node.get("retries", defaults.get("retries", 0)))
            attempts[node["id"]] = attempts.get(node["id"], 0) + 1
            key = node["id"]
            remaining = max(1, int(1 + retries - (attempts[key] - 1)))
            holder = {}

            def worker(node=node, timeout=timeout, remaining=remaining, holder=holder):
                last = None
                for attempt in range(1, remaining + 1):
                    if cancel is not None and cancel.event.is_set():
                        break
                    try:
                        if node.get("argv"):
                            rec = run_command(
                                " ".join(node["argv"]),
                                config,
                                cwd=node.get("workdir") or defaults.get("workdir"),
                                timeout=timeout,
                                cancel=cancel,
                                argv_override=list(node["argv"]),
                            )
                            rec["mode"] = "argv"
                        else:
                            rec = run_command(
                                node["command"],
                                config,
                                shell=node.get("dialect") or defaults.get("dialect") or "auto",
                                cwd=node.get("workdir") or defaults.get("workdir"),
                                timeout=timeout,
                                cancel=cancel,
                            )
                            rec["mode"] = "shell"
                    except NexusError as exc:
                        rec = {"error": exc.as_dict(), "exit_code": None, "stderr": exc.message}
                    rec["attempt"] = attempt
                    last = rec
                    interp = interpret(rec.get("exit_code"), rec.get("stderr", "") or "", rec.get("signal"), exit_table)
                    rec["interpretation"] = interp
                    if interp["state"] == "success" or attempt == remaining:
                        break
                    if interp["state"] not in config.get("retry.retry_on_states", []) and not interp["retryable"]:
                        break
                    time.sleep(backoff_delay(attempt, config.get("retry", {})))
                holder["record"] = last

            th = threading.Thread(target=worker, daemon=True)
            th.start()
            running[node["id"]] = (node, th, holder)
            pending.remove(node)
            launched = True
        if running:
            time.sleep(0.05)
            for node_id in list(running):
                node, th, holder = running[node_id]
                if th.is_alive():
                    continue
                th.join(timeout=1)
                rec = holder.get("record") or {"exit_code": None}
                status = "ok"
                if rec.get("error"):
                    status = "failed"
                elif rec.get("exit_code") not in (0, None):
                    status = "failed"
                elif rec.get("timed_out") or rec.get("stalled") or rec.get("cancelled"):
                    status = "cancelled" if rec.get("cancelled") else "failed"
                artifacts = node.get("artifacts") or []
                if status == "ok" and artifacts:
                    missing = [a for a in artifacts if not resolve_path(a, base=work_root(config)).exists()]
                    if missing:
                        status = "failed"
                        rec["missing_artifacts"] = missing
                results[node_id] = {
                    "status": status,
                    "exit_code": rec.get("exit_code"),
                    "duration_seconds": rec.get("duration_seconds"),
                    "timed_out": rec.get("timed_out"),
                    "stalled": rec.get("stalled"),
                    "mode": rec.get("mode"),
                    "attempts": rec.get("attempt"),
                    "state": (rec.get("interpretation") or {}).get("state"),
                    "stderr_tail": tail_lines(rec.get("stderr") or "", 10),
                    "stdout_tail": tail_lines(rec.get("stdout") or "", 10),
                }
                if rec.get("error"):
                    results[node_id]["error"] = rec["error"]
                if results[node_id]["status"] == "ok":
                    completed.add(node_id)
                order.append(node_id)
                del running[node_id]
        elif not launched:
            for node in list(pending):
                results[node["id"]] = {"status": "blocked", "reason": "unresolvable dependencies", "depends_on": node.get("depends_on", [])}
                order.append(node["id"])
                pending.remove(node)
            break

    checkpoint_write(ckpt_file, {"completed": sorted(completed), "results": {k: {"status": v.get("status")} for k, v in results.items()}})

    statuses = [v.get("status") for v in results.values()]
    if all(s == "ok" for s in statuses):
        overall = "success"
    elif any(s == "failed" for s in statuses):
        overall = "failed"
    elif any(s == "blocked" for s in statuses):
        overall = "blocked"
    elif any(s == "cancelled" for s in statuses):
        overall = "cancelled"
    else:
        overall = "partial"
    return {
        "status": overall,
        "node_count": len(nodes),
        "completed": sorted(completed),
        "order": order,
        "results": results,
        "checkpoint": str(ckpt_file),
        "duration_seconds": round(time.time() - started, 3),
        "max_parallel": max_parallel,
        "block_dependents_on_failure": block,
    }


def _quote_argv(argv, config):
    if os.name == "nt":
        return subprocess.list2cmdline(list(argv))
    import shlex

    return " ".join(shlex.quote(str(a)) for a in argv)


OP_NODE_HANDLERS = {}


def register_op(name):
    def deco(fn):
        OP_NODE_HANDLERS[name] = fn
        return fn

    return deco


def _run_op_node(node, config, results):
    name = node.get("op")
    handler = OP_NODE_HANDLERS.get(name)
    started = time.time()
    if handler is None:
        return {"status": "failed", "error": {"code": "unknown_op", "message": "unsupported op '%s'" % name, "details": {"supported": sorted(OP_NODE_HANDLERS)}}}
    try:
        out = handler(node.get("op_args") or {}, config, {"results": results})
        return {"status": "ok", "op": name, "duration_seconds": round(time.time() - started, 3), "output": out}
    except NexusError as exc:
        return {"status": "failed", "op": name, "duration_seconds": round(time.time() - started, 3), "error": exc.as_dict()}


def _op_validate(args, config, _ctx):
    paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
    artifacts = []
    for raw in paths:
        p = resolve_path(raw, base=work_root(config))
        if not p.exists():
            raise NexusError("artifact missing", code="artifact_missing", details={"path": str(p)})
        artifacts.append({"path": str(p), "exists": True, "size_bytes": p.stat().st_size, "sha256": sha256_file(p) if p.is_file() else None})
    return {"artifacts": artifacts}


def _op_trace(args, config, _ctx):
    return {"recorded": True, "note": "trace node records the surrounding workflow evidence", "args": args}


OP_NODE_HANDLERS["validate"] = _op_validate
OP_NODE_HANDLERS["trace"] = _op_trace


# --------------------------------------------------------------------------- #
# Estimate
# --------------------------------------------------------------------------- #


def estimate_duration(units, unit_seconds, concurrency=1, warmup_seconds=0.0, latency_seconds=0.0, verify_ratio=0.25, retry_allowance=0.1):
    units = max(0.0, float(units))
    unit_seconds = max(0.0, float(unit_seconds))
    concurrency = max(1.0, float(concurrency))
    base = (units * unit_seconds) / concurrency
    verification = base * max(0.0, float(verify_ratio))
    retries = base * max(0.0, float(retry_allowance))
    total = base + max(0.0, float(warmup_seconds)) + max(0.0, float(latency_seconds)) + verification + retries
    confidence = "high" if units and unit_seconds else "low"
    if float(concurrency) > 1:
        confidence = "medium" if confidence == "high" else confidence
    stages = {
        "setup": round(max(0.0, float(warmup_seconds)) + max(0.0, float(latency_seconds)), 3),
        "execution": round(base, 3),
        "verification": round(verification, 3),
        "repair_retry": round(retries, 3),
        "export_cleanup": round(max(0.5, total * 0.02), 3),
    }
    return {
        "total_seconds": round(total, 3),
        "total_human": _human_duration(total),
        "stages": stages,
        "confidence": confidence,
        "assumptions": {
            "units": units,
            "unit_seconds": unit_seconds,
            "concurrency": concurrency,
            "verify_ratio": float(verify_ratio),
            "retry_allowance": float(retry_allowance),
        },
        "disclaimer": "Deterministic arithmetic estimate, not a guarantee. Revise from observed progress.",
    }


def _human_duration(seconds):
    seconds = float(seconds)
    if seconds < 60:
        return "%.1fs" % seconds
    if seconds < 3600:
        return "%dm %02ds" % (int(seconds // 60), int(seconds % 60))
    return "%dh %02dm" % (int(seconds // 3600), int((seconds % 3600) // 60))


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #


def parse_log(text, config, max_records=None):
    max_records = int(max_records or config.get("limits.max_log_records", 20000))
    lines = text.splitlines()
    levels = {}
    records = []
    malformed = 0
    for index, line in enumerate(lines[:max_records], start=1):
        stripped = line.strip()
        if not stripped:
            continue
        rec = None
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    rec = {
                        "line": index,
                        "level": str(obj.get("level") or obj.get("severity") or "info").lower(),
                        "message": str(obj.get("message") or obj.get("msg") or ""),
                        "timestamp": obj.get("timestamp") or obj.get("time") or obj.get("@timestamp"),
                        "job": obj.get("job") or obj.get("job_id"),
                        "stage": obj.get("stage"),
                        "correlation_id": obj.get("correlation_id") or obj.get("trace_id"),
                        "error_id": obj.get("error_id") or obj.get("error_code"),
                    }
            except json.JSONDecodeError:
                malformed += 1
        if rec is None:
            m = re.match(r"^\s*(?:\[?(\d{4}-\d\d-\d\d[ T][\d:.,]+Z?)\]?\s+)?(?:\[?(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|FATAL|CRITICAL)\]?\s*[:|-]?\s*)?(.*)$", line, re.IGNORECASE)
            ts, level, message = (m.group(1), m.group(2), m.group(3)) if m else (None, None, line)
            rec = {
                "line": index,
                "level": (level or "info").lower(),
                "message": message,
                "timestamp": ts,
                "job": None,
                "stage": None,
                "correlation_id": None,
                "error_id": None,
            }
        rec["message"] = redact(rec["message"], config)[:2000]
        levels[rec["level"]] = levels.get(rec["level"], 0) + 1
        records.append(rec)
    errors = [r for r in records if r["level"] in ("error", "fatal", "critical", "err")]
    warnings = [r for r in records if r["level"] in ("warn", "warning")]
    ids = sorted({r["correlation_id"] for r in records if r.get("correlation_id")})
    return {
        "records_read": len(records),
        "lines_scanned": min(len(lines), max_records),
        "total_lines": len(lines),
        "truncated": len(lines) > max_records,
        "malformed_json_lines": malformed,
        "levels": levels,
        "first_error": errors[0] if errors else None,
        "first_warning": warnings[0] if warnings else None,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "correlation_ids": ids[:20],
        "error_excerpts": errors[:5],
        "records": records if len(records) <= 200 else None,
    }


def dedupe_records(records, key_fields=("level", "message")):
    seen = {}
    for rec in records or []:
        key = tuple(str(rec.get(f)) for f in key_fields)
        if key in seen:
            seen[key]["count"] += 1
        else:
            entry = dict(rec)
            entry["count"] = 1
            seen[key] = entry
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Data transforms
# --------------------------------------------------------------------------- #

SUPPORTED_FORMATS = ("json", "csv", "tsv", "jsonl")


def detect_format(path, explicit=None):
    if explicit:
        fmt = explicit.lower()
        if fmt not in SUPPORTED_FORMATS:
            raise NexusError("unsupported data format", code="invalid_format", details={"format": fmt, "supported": list(SUPPORTED_FORMATS)})
        return fmt
    suffix = Path(path).suffix.lower().lstrip(".")
    mapping = {"json": "json", "csv": "csv", "tsv": "tsv", "jsonl": "jsonl", "ndjson": "jsonl"}
    if suffix in mapping:
        return mapping[suffix]
    raise NexusError(
        "cannot infer data format from the file extension",
        code="format_unknown",
        details={"path": str(path), "fix": "Pass --format json|csv|tsv|jsonl", "supported": list(SUPPORTED_FORMATS)},
    )


def load_dataset(path, fmt, config):
    text = read_text(path, max_bytes=int(config.get("limits.max_workflow_bytes", 1048576)) * 5)
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NexusError("dataset is not valid JSON", code="data_invalid", details={"line": exc.lineno, "column": exc.colno, "message": exc.msg})
        if isinstance(data, dict):
            for key in ("rows", "data", "records", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        if not isinstance(data, list):
            raise NexusError("JSON dataset must be an array or an object with rows/data/records/items", code="data_invalid", details={"type": type(data).__name__})
        return data
    if fmt == "jsonl":
        rows = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise NexusError("JSONL line is not valid JSON", code="data_invalid", details={"line": number, "message": exc.msg})
        return rows
    delimiter = "," if fmt == "csv" else "\t"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise NexusError("delimited dataset has no header row", code="data_invalid", details={"format": fmt})
    return [dict(row) for row in reader]


def dump_dataset(rows, fmt, fields=None):
    fields = fields or (list(rows[0].keys()) if rows else [])
    if fmt == "json":
        return json.dumps(rows, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    if fmt == "jsonl":
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    delim = "," if fmt == "csv" else "\t"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, delimiter=delim, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _flatten_cell(row.get(k)) for k in fields})
    return buf.getvalue()


def verify_json_lines(text, fmt):
    """Re-parse serialized output to prove it is valid for the declared format."""
    try:
        if fmt == "json":
            json.loads(text)
            return True
        if fmt == "jsonl":
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
            return True
        rows = list(csv.DictReader(io.StringIO(text), delimiter="," if fmt == "csv" else "\t"))
        return True if rows is not None else False
    except (json.JSONDecodeError, csv.Error, TypeError):
        return False


def _flatten_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def apply_transform_spec(rows, spec, config):
    """Deterministic pipeline: filter -> map -> dedupe -> sort -> limit -> aggregate."""
    if not isinstance(spec, dict):
        raise NexusError("transform spec must be a JSON object", code="spec_invalid", details={"keys": ["filter", "map", "select", "dedupe", "sort", "limit", "aggregate"]})
    known = {"filter", "map", "select", "rename", "dedupe", "sort", "limit", "aggregate", "drop_empty"}
    unknown = set(spec) - known
    if unknown:
        raise NexusError("transform spec has unknown steps", code="spec_invalid", details={"unknown": sorted(unknown), "supported": sorted(known)})
    steps = []
    max_rows = int(config.get("data.max_rows", 100000))
    if len(rows) > max_rows:
        raise NexusError("dataset exceeds the row limit", code="data_too_large", details={"rows": len(rows), "limit": max_rows})

    if "filter" in spec:
        conds = spec["filter"]
        if not isinstance(conds, list):
            conds = [conds]
        for cond in conds:
            if not isinstance(cond, dict) or "field" not in cond or "op" not in cond:
                raise NexusError("filter conditions need field and op", code="spec_invalid", details={"received": cond})
        before = len(rows)
        rows = [r for r in rows if all(_match_cond(r, c) for c in conds)]
        steps.append({"step": "filter", "rows_before": before, "rows_after": len(rows)})

    if "map" in spec:
        for mapping in spec["map"]:
            src = mapping.get("from")
            dst = mapping.get("to")
            if not src or not dst:
                raise NexusError("map entries need from and to", code="spec_invalid", details={"received": mapping})
            for row in rows:
                row[dst] = _apply_map_fn(row.get(src), mapping.get("fn", "identity"), mapping)
        steps.append({"step": "map", "count": len(spec["map"])})

    if "rename" in spec:
        for row in rows:
            for old, new in spec["rename"].items():
                if old in row:
                    row[new] = row.pop(old)
        steps.append({"step": "rename", "count": len(spec["rename"])})

    if "select" in spec:
        fields = spec["select"]
        if not isinstance(fields, list) or not fields:
            raise NexusError("select must be a non-empty array of field names", code="spec_invalid", details={"received": fields})
        rows = [{k: r.get(k) for k in fields} for r in rows]
        steps.append({"step": "select", "fields": fields})

    if "drop_empty" in spec:
        field = spec["drop_empty"]
        before = len(rows)
        rows = [r for r in rows if r.get(field) not in (None, "", [])]
        steps.append({"step": "drop_empty", "field": field, "removed": before - len(rows)})

    if "dedupe" in spec:
        key = spec["dedupe"]
        keys = key if isinstance(key, list) else [key]
        seen = set()
        out = []
        for row in rows:
            marker = tuple(json.dumps(row.get(k), sort_keys=True) for k in keys)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(row)
        steps.append({"step": "dedupe", "keys": keys, "removed": len(rows) - len(out)})
        rows = out

    if "sort" in spec:
        sort_spec = spec["sort"]
        entries = sort_spec if isinstance(sort_spec, list) else [sort_spec]
        for entry in reversed(entries):
            field = entry.get("field") if isinstance(entry, dict) else entry
            desc = bool(entry.get("desc")) if isinstance(entry, dict) else False
            if not field:
                raise NexusError("sort entries need a field", code="spec_invalid", details={"received": entry})
            rows = sorted(rows, key=lambda r, f=field: (r.get(f) is None, _sort_key(r.get(f))), reverse=desc)
        steps.append({"step": "sort", "entries": entries})

    if "aggregate" in spec:
        agg = spec["aggregate"]
        group_by = agg.get("group_by") or []
        metrics = agg.get("metrics") or []
        if not metrics:
            raise NexusError("aggregate needs at least one metric", code="spec_invalid", details={"received": agg})
        buckets = {}
        for row in rows:
            key = tuple(json.dumps(row.get(g), sort_keys=True) for g in group_by)
            buckets.setdefault(key, []).append(row)
        out = []
        for key, group in buckets.items():
            item = {g: group[0].get(g) for g in group_by}
            item["count"] = len(group)
            for metric in metrics:
                field = metric.get("field")
                fn = metric.get("fn", "sum")
                values = [v for v in (_numeric(g.get(field)) for g in group) if v is not None]
                if fn == "sum":
                    item[metric.get("as") or "%s_sum" % field] = round(sum(values), 6)
                elif fn == "avg":
                    item[metric.get("as") or "%s_avg" % field] = round(sum(values) / len(values), 6) if values else None
                elif fn == "min":
                    item[metric.get("as") or "%s_min" % field] = min(values) if values else None
                elif fn == "max":
                    item[metric.get("as") or "%s_max" % field] = max(values) if values else None
                elif fn == "count_distinct":
                    item[metric.get("as") or "%s_distinct" % field] = len({json.dumps(g.get(field), sort_keys=True) for g in group})
                else:
                    raise NexusError("unsupported aggregate function", code="spec_invalid", details={"fn": fn, "supported": ["sum", "avg", "min", "max", "count_distinct"]})
            out.append(item)
        rows = out
        steps.append({"step": "aggregate", "group_by": group_by, "groups": len(out)})

    if "limit" in spec:
        limit = int(spec["limit"])
        if limit < 0:
            raise NexusError("limit must be >= 0", code="spec_invalid", details={"limit": limit})
        rows = rows[:limit]
        steps.append({"step": "limit", "limit": limit, "rows": len(rows)})

    return rows, steps


def _sort_key(value):
    """Sort numerics (including numeric strings) before text, with None first."""
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (int, float)):
        return (1, float(value), "")
    as_num = _numeric(value)
    if as_num is not None:
        return (1, as_num, "")
    return (2, 0.0, str(value))


def _match_cond(row, cond):
    field = cond["field"]
    op = cond["op"]
    expected = cond.get("value")
    actual = row.get(field)
    if op == "exists":
        return field in row
    if op == "not_exists":
        return field not in row
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return _numeric(actual) is not None and _numeric(expected) is not None and _numeric(actual) > _numeric(expected)
    if op == "gte":
        return _numeric(actual) is not None and _numeric(expected) is not None and _numeric(actual) >= _numeric(expected)
    if op == "lt":
        return _numeric(actual) is not None and _numeric(expected) is not None and _numeric(actual) < _numeric(expected)
    if op == "lte":
        return _numeric(actual) is not None and _numeric(expected) is not None and _numeric(actual) <= _numeric(expected)
    if op == "contains":
        return expected is not None and str(expected) in str(actual or "")
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op == "matches":
        return expected is not None and re.search(str(expected), str(actual or "")) is not None
    if op == "is_null":
        return actual is None
    raise NexusError("unsupported filter operator", code="spec_invalid", details={"op": op, "supported": ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in", "matches", "exists", "not_exists", "is_null"]})


def _numeric(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _apply_map_fn(value, fn, spec):
    if fn == "identity":
        return value
    if fn == "lower":
        return str(value).lower() if value is not None else None
    if fn == "upper":
        return str(value).upper() if value is not None else None
    if fn == "trim":
        return str(value).strip() if value is not None else None
    if fn == "int":
        n = _numeric(value)
        return int(n) if n is not None else None
    if fn == "float":
        return _numeric(value)
    if fn == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
    if fn == "len":
        return len(value) if value is not None and hasattr(value, "__len__") else None
    if fn == "concat":
        sep = spec.get("sep", " ")
        parts = [str(value)] + [str(v) for v in spec.get("with", [])]
        return sep.join(p for p in parts if p != "None")
    if fn == "substr":
        start = int(spec.get("start", 0))
        end = spec.get("end")
        return str(value)[start : int(end) if end is not None else None] if value is not None else None
    if fn == "split":
        return str(value).split(spec.get("sep", ",")) if value is not None else None
    raise NexusError("unsupported map function", code="spec_invalid", details={"fn": fn, "supported": ["identity", "lower", "upper", "trim", "int", "float", "bool", "len", "concat", "substr", "split"]})


def validate_dataset(rows, spec, config, delimited=False):
    if not isinstance(spec, dict):
        raise NexusError("validation spec must be a JSON object", code="spec_invalid", details={"keys": ["required", "types", "unique", "ranges", "patterns", "max_rows", "min_rows"]})
    max_rows = int(config.get("data.max_rows", 100000))
    issues = []
    if len(rows) > max_rows:
        issues.append({"code": "too_many_rows", "detail": "%d rows exceeds limit %d" % (len(rows), max_rows)})
    min_rows = spec.get("min_rows")
    if min_rows is not None and len(rows) < int(min_rows):
        issues.append({"code": "too_few_rows", "detail": "%d rows is below the minimum %s" % (len(rows), min_rows)})
    for field in spec.get("required", []):
        missing = [i for i, row in enumerate(rows) if row.get(field) in (None, "", [])]
        if missing:
            issues.append({"code": "missing_required", "field": field, "count": len(missing), "first_rows": missing[:5]})
    type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list, "null": type(None)}
    for field, type_name in (spec.get("types") or {}).items():
        expected = type_map.get(type_name)
        if expected is None:
            raise NexusError("unsupported type name", code="spec_invalid", details={"field": field, "type": type_name, "supported": sorted(type_map)})
        bad = []
        for i, row in enumerate(rows):
            value = row.get(field)
            if value is None:
                continue
            if type_name in ("number", "integer") and isinstance(value, bool):
                bad.append(i)
            elif delimited and type_name in ("number", "integer"):
                # Every cell in CSV/TSV is text, so the check means "must parse as a number".
                num = _numeric(value)
                if num is None or (type_name == "integer" and not float(num).is_integer()):
                    bad.append(i)
            elif delimited and type_name == "boolean":
                if str(value).strip().lower() not in ("true", "false", "1", "0", "yes", "no", "y", "n", "on", "off"):
                    bad.append(i)
            elif not isinstance(value, expected):
                bad.append(i)
        if bad:
            issues.append({"code": "type_mismatch", "field": field, "expected": type_name, "count": len(bad), "first_rows": bad[:5]})
    for field in spec.get("unique", []):
        seen = {}
        for i, row in enumerate(rows):
            marker = json.dumps(row.get(field), sort_keys=True)
            seen.setdefault(marker, []).append(i)
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        if dupes:
            issues.append({"code": "duplicate_values", "field": field, "count": len(dupes), "first_rows": [v for v in list(dupes.values())[:3]]})
    for rule in spec.get("ranges", []):
        field = rule.get("field")
        bad = []
        for i, row in enumerate(rows):
            value = _numeric(row.get(field))
            if value is None:
                continue
            if "min" in rule and value < _numeric(rule["min"]):
                bad.append(i)
            elif "max" in rule and value > _numeric(rule["max"]):
                bad.append(i)
        if bad:
            issues.append({"code": "out_of_range", "field": field, "count": len(bad), "first_rows": bad[:5]})
    for rule in spec.get("patterns", []):
        field = rule.get("field")
        rx = re.compile(rule["pattern"])
        bad = [i for i, row in enumerate(rows) if row.get(field) is not None and not rx.search(str(row.get(field)))]
        if bad:
            issues.append({"code": "pattern_mismatch", "field": field, "count": len(bad), "first_rows": bad[:5]})
    return {"valid": not issues, "row_count": len(rows), "issue_count": len(issues), "issues": issues}


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #


def _sql_statement_kind(sql):
    stripped = re.sub(r"^\s*(--[^\n]*\n|/\*.*?\*/\s*)*", "", sql, flags=re.S).strip()
    if not stripped:
        raise NexusError("query is empty", code="query_empty")
    first = stripped.split(None, 1)[0].upper()
    if ";" in stripped.rstrip(";").replace("';", ""):
        raise NexusError(
            "multiple statements are not permitted",
            code="query_multiple_statements",
            details={"fix": "Submit one statement per call."},
        )
    return first


def db_query(db_path, sql, config, params=None, allow_write=False, approve=False, approval_ref=None, max_rows=None):
    policy = config.load_asset("policy_file")
    allowed_roots = [resolve_path(r) for r in config.get("database.allowed_roots", [])]
    path = resolve_path(db_path)
    if allowed_roots and not any(_within(path, r) for r in allowed_roots if r):
        raise NexusError(
            "database is outside database.allowed_roots",
            code="scope_outside_allowed",
            details={"path": str(path), "allowed_roots": [str(r) for r in allowed_roots]},
            exit_code=3,
        )
    if not path.is_file():
        raise NexusError("database file not found", code="path_not_found", details={"path": str(path)})
    kind = _sql_statement_kind(sql)
    write_kinds = {"INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "REPLACE", "TRUNCATE", "VACUUM", "PRAGMA"}
    read_only = bool(config.get("database.read_only_by_default", True)) and not allow_write
    if kind in write_kinds and read_only:
        raise NexusError(
            "statement is mutating but the database is opened read-only",
            code="db_write_blocked",
            details={"statement": kind, "fix": "Pass --allow-write (and --approve for destructive statements) after taking a backup."},
            exit_code=3,
        )
    if kind in write_kinds:
        risk = assess_risk(sql, policy, "high")
        if required_approval(risk, config) and not approve:
            raise NexusError(
                "destructive database statement requires approval",
                code="approval_required",
                details={"risk": risk, "fix": "Re-run with --approve --approval-ref <who/why>.", "statement": kind},
                exit_code=3,
            )
    max_rows = int(max_rows or config.get("database.max_rows", 5000))
    started = time.time()
    try:
        if read_only:
            conn = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=10.0)
        else:
            conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.Error as exc:
        raise NexusError("cannot open the database", code="db_open_failed", details={"error": str(exc), "path": str(path)})
    try:
        cur = conn.cursor()
        bound = dict(params or {})
        if bound:
            cur.execute(sql, bound)
        else:
            cur.execute(sql)
        if cur.description:
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            payload = [dict(zip(columns, r)) for r in rows]
        else:
            conn.commit()
            columns = []
            payload = []
            truncated = False
        rowcount = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None
    except sqlite3.Error as exc:
        raise NexusError("query failed", code="query_failed", details={"error": str(exc), "statement": kind})
    finally:
        conn.close()
    return {
        "database": str(path),
        "statement": kind,
        "read_only": read_only,
        "columns": columns,
        "rows": payload,
        "row_count": len(payload),
        "truncated": truncated,
        "row_limit": max_rows,
        "affected_rows": rowcount,
        "duration_seconds": round(time.time() - started, 3),
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def http_request(url, config, method="GET", headers=None, body=None, allow_host=None, approve=False, approval_ref=None):
    net = config.get("network", {})
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise NexusError("only http and https URLs are supported", code="invalid_url", details={"url": url, "scheme": parsed.scheme})
    host = parsed.hostname or ""
    if not host:
        raise NexusError("URL has no host", code="invalid_url", details={"url": url})
    allowed = [h.lower() for h in net.get("allowed_hosts", [])]
    if allow_host:
        allowed = allowed + [allow_host.lower()]
    if not net.get("allow_network", False) or host.lower() not in allowed:
        raise NexusError(
            "network access is disabled for this host",
            code="network_blocked",
            details={
                "host": host,
                "allow_network": net.get("allow_network", False),
                "allowed_hosts": allowed,
                "fix": "Set network.allow_network=true and add the host to network.allowed_hosts, or pass --allow-host.",
            },
            exit_code=3,
        )
    method = (method or "GET").upper()
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        raise NexusError("unsupported HTTP method", code="invalid_method", details={"method": method})
    policy = config.load_asset("policy_file")
    risk = assess_risk("%s %s" % (method, url), policy, "medium" if method not in ("GET", "HEAD") else None)
    if method not in ("GET", "HEAD"):
        risk["matches"].append(
            {
                "rule_id": "mutating-http",
                "level": "high",
                "category": "network",
                "reason": "A %s request can change remote state" % method,
                "safer": "Send the request to a test endpoint first and confirm the exact target and payload.",
            }
        )
        risk["level"] = "high"
        risk["rank"] = max(risk["rank"], 3)
        risk["flags"] = list(dict.fromkeys(risk["flags"] + ["mutating-http"]))
        risk["approval_required"] = True
    risk["approval_required"] = required_approval(risk, config)
    if risk["approval_required"] and not approve:
        raise NexusError(
            "mutating HTTP request requires approval",
            code="approval_required",
            details={"risk": risk, "fix": "Re-run with --approve --approval-ref <who/why>."},
            exit_code=3,
        )
    timeout = float(net.get("timeout_seconds", 15))
    attempts_cfg = max(1, int(net.get("max_attempts", 2)))
    max_bytes = int(net.get("max_response_bytes", 100000))
    req = urllib.request.Request(url, method=method, data=body.encode("utf-8") if body else None)
    for key, value in (headers or {}).items():
        if key.lower() in ("authorization", "cookie", "x-api-key"):
            raise NexusError("refusing to send credentials from the command line", code="secret_in_command", details={"header": key}, exit_code=3)
        req.add_header(key, str(value))
    attempts = []
    for attempt in range(1, attempts_cfg + 1):
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                truncated = len(raw) > max_bytes
                text = raw[:max_bytes].decode("utf-8", "replace")
                return {
                    "url": url,
                    "method": method,
                    "status": resp.status,
                    "reason": resp.reason,
                    "headers": {k.lower(): v for k, v in resp.headers.items()},
                    "body_truncated": truncated,
                    "body": redact(text, config),
                    "duration_seconds": round(time.time() - started, 3),
                    "attempts": attempts + [{"attempt": attempt, "status": resp.status}],
                    "retries": attempt - 1,
                }
        except urllib.error.HTTPError as exc:
            detail = exc.read(4000).decode("utf-8", "replace") if hasattr(exc, "read") else ""
            attempts.append({"attempt": attempt, "status": exc.code, "error": redact(str(exc.reason), config)})
            mutating = method not in ("GET", "HEAD")
            if mutating:
                return {
                    "url": url,
                    "method": method,
                    "status": exc.code,
                    "reason": str(exc.reason),
                    "body": redact(detail, config),
                    "attempts": attempts,
                    "retries": attempt - 1,
                    "note": "Mutating request was not retried after a server response, because replay could duplicate the side effect.",
                }
            if exc.code < 500 and exc.code != 429:
                return {
                    "url": url,
                    "method": method,
                    "status": exc.code,
                    "reason": str(exc.reason),
                    "body": redact(detail, config),
                    "attempts": attempts,
                    "retries": attempt - 1,
                    "note": "Client error; not retried.",
                }
            if attempt == attempts_cfg:
                raise NexusError(
                    "HTTP request failed after retries",
                    code="http_failed",
                    details={"status": exc.code, "url": url, "attempts": attempts},
                    exit_code=4,
                )
            time.sleep(backoff_delay(attempt, config.get("retry", {})))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            attempts.append({"attempt": attempt, "error": redact(str(getattr(exc, "reason", exc)), config)})
            if attempt == attempts_cfg:
                raise NexusError(
                    "HTTP request could not be completed",
                    code="http_unreachable",
                    details={"url": url, "attempts": attempts},
                    exit_code=4,
                )
            time.sleep(backoff_delay(attempt, config.get("retry", {})))
    raise NexusError("HTTP request failed", code="http_failed", details={"url": url, "attempts": attempts}, exit_code=4)


# --------------------------------------------------------------------------- #
# Shell dialect translation
# --------------------------------------------------------------------------- #


def translate_command(command, source, target, config):
    """Translate in one pass so a rule never re-processes another rule's output."""
    table = config.load_asset("shell_dialects_file")
    aliases = table.get("aliases", {})

    def norm(name):
        key = aliases.get((name or "").lower(), (name or "").lower())
        if key not in table["dialects"]:
            raise NexusError("unsupported shell dialect", code="invalid_shell", details={"shell": name, "supported": table["dialects"]})
        return key

    source, target = norm(source), norm(target)
    if source == target:
        return {
            "source": source,
            "target": target,
            "command": command,
            "applied": [],
            "warnings": ["Source and target dialects are identical; nothing was changed."],
            "quoting_notes": table.get("quoting_notes", []),
        }

    active = []
    for rule in table["rules"]:
        from_map = rule.get("from", {})
        to_map = rule.get("to", {})
        if source not in from_map or target not in to_map:
            continue
        try:
            rx = re.compile(from_map[source], re.MULTILINE)
        except re.error as exc:
            raise NexusError("invalid translation rule", code="asset_invalid", details={"rule": rule.get("id"), "error": str(exc)})
        preserve_lead = from_map[source].startswith("(?:(?<=^)|(?<=[;&|]))")
        active.append((rule, rx, to_map[target], target in (rule.get("unsupported_targets") or []), preserve_lead))
    if not active:
        raise NexusError("no translation rules cover this direction", code="no_translation", details={"source": source, "target": target})

    applied = []
    warnings = []

    def expand(rule, rx, replacement, match_text):
        """Fill {1}..{n} from the rule's own capture groups, collapsing unmatched alternation slots."""
        local = rx.search(match_text)
        raw = list(local.groups() if local else [])
        filled = [g if g is not None else "" for g in raw]
        collapsed = [g for g in raw if g is not None]
        out = replacement
        for idx in range(len(filled), 0, -1):
            use = collapsed[idx - 1] if len(collapsed) == len([g for g in filled if g != ""]) and idx - 1 < len(collapsed) else filled[idx - 1]
            out = out.replace("{%d}" % idx, use)
        return out.replace("{0}", match_text)

    combined = re.compile(
        "|".join("(?P<R%d>%s)" % (i, rx.pattern) for i, (_rule, rx, _to, _un, _pl) in enumerate(active)),
        re.MULTILINE,
    )

    def repl(match):
        for i, (rule, rx, replacement, unsupported, preserve_lead) in enumerate(active):
            if match.group("R%d" % i) is None:
                continue
            text = match.group(0)
            lead = re.match(r"[ \t]*", text).group(0)
            out = expand(rule, rx, replacement, text)
            if preserve_lead and lead:
                out = lead + out
            applied.append(rule["id"])
            if unsupported:
                warnings.append("Rule '%s' is not supported on %s: %s" % (rule["id"], target, replacement))
            elif rule.get("approximate"):
                warnings.append("Rule '%s' is approximate for %s: %s" % (rule["id"], target, rule.get("note", "semantics differ")))
            elif rule.get("note"):
                warnings.append("Rule '%s': %s" % (rule["id"], rule["note"]))
            return out
        return match.group(0)

    text = combined.sub(repl, command)
    if target in ("cmd", "powershell") and re.search(r"(?<![\w:\]])/(?:tmp|home|usr|var|etc)\b", text):
        warnings.append("POSIX absolute paths remain in the output; map them to Windows paths before running.")
    if source in ("cmd", "powershell") and re.search(r"\b[A-Za-z]:[\\/]", text):
        warnings.append("Windows drive paths remain in the output; map them to POSIX paths before running.")
    if not applied:
        warnings.append("No rule matched; the command was returned unchanged and may not be valid for %s." % target)
    return {
        "source": source,
        "target": target,
        "command": text,
        "applied": applied,
        "warnings": list(dict.fromkeys(warnings)),
        "quoting_notes": table.get("quoting_notes", []),
    }


# --------------------------------------------------------------------------- #
# Scheduler generation (writes text, never installs)
# --------------------------------------------------------------------------- #


def _cron_field(value, name, low, high):
    if value == "*":
        return "*"
    parts = str(value).split(",")
    out = []
    for part in parts:
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            num = int(part)
            if num < low or num > high:
                raise NexusError("cron field out of range", code="schedule_invalid", details={"field": name, "value": part, "range": [low, high]})
            out.append(str(num))
        elif re.fullmatch(r"\d+-\d+", part):
            a, b = [int(x) for x in part.split("-")]
            if a < low or b > high or a > b:
                raise NexusError("cron range out of range", code="schedule_invalid", details={"field": name, "value": part, "range": [low, high]})
            out.append(part)
        elif re.fullmatch(r"\*/\d+", part) or re.fullmatch(r"\d+-\d+/\d+", part):
            step = int(part.split("/")[1])
            if step <= 0:
                raise NexusError("cron step must be positive", code="schedule_invalid", details={"field": name, "value": part})
            out.append(part)
        else:
            raise NexusError("unsupported cron token", code="schedule_invalid", details={"field": name, "value": part, "supported": "*, n, a-b, */n, a-b/n, comma lists"})
    return ",".join(out)


def build_schedule(spec):
    command = (spec.get("command") or "").strip()
    if not command:
        raise NexusError("schedule requires a command", code="schedule_invalid")
    every = spec.get("every")
    minute = spec.get("minute", "0")
    hour = spec.get("hour", "*")
    dom = spec.get("day_of_month", "*")
    month = spec.get("month", "*")
    dow = spec.get("day_of_week", "*")
    if every:
        unit = (spec.get("unit") or "minutes").lower()
        if unit.startswith("min"):
            minute, hour, dom, month, dow = "*/%d" % int(every), "*", "*", "*", "*"
        elif unit.startswith("hour"):
            minute, hour, dom, month, dow = str(spec.get("minute", 0)), "*/%d" % int(every), "*", "*", "*"
        elif unit.startswith("day"):
            dom_field = "*" if int(every) == 1 else "*/%d" % int(every)
            minute, hour, dom, month, dow = str(spec.get("minute", 0)), str(spec.get("hour", 3)), dom_field, "*", "*"
        else:
            raise NexusError("unsupported schedule unit", code="schedule_invalid", details={"unit": unit, "supported": ["minutes", "hours", "days"]})
    minute = _cron_field(minute, "minute", 0, 59)
    hour = _cron_field(hour, "hour", 0, 23)
    dom = _cron_field(dom, "day_of_month", 1, 31)
    month = _cron_field(month, "month", 1, 12)
    dow = _cron_field(dow, "day_of_week", 0, 6)
    cron_expr = "%s %s %s %s %s" % (minute, hour, dom, month, dow)
    cron = "%s %s" % (cron_expr, command)
    tz = spec.get("timezone") or "UTC"
    overlap = spec.get("overlap", "skip")
    if overlap not in ("skip", "queue", "replace", "allow"):
        raise NexusError("unsupported overlap policy", code="schedule_invalid", details={"overlap": overlap, "supported": ["skip", "queue", "replace", "allow"]})
    missed = spec.get("missed", "skip")
    if missed not in ("skip", "run_once", "catch_up"):
        raise NexusError("unsupported missed-run policy", code="schedule_invalid", details={"missed": missed, "supported": ["skip", "run_once", "catch_up"]})
    retries = int(spec.get("retries", 0))
    name = re.sub(r"[^a-z0-9-]+", "-", (spec.get("name") or "nexus-job").lower()).strip("-") or "nexus-job"
    safe_command = command.replace("%", "\\%")
    locked_command = command
    if overlap in ("skip", "queue"):
        locked_command = ("flock -n /tmp/%s.lock %s" % (name, command)) if overlap == "skip" else ("flock /tmp/%s.lock %s" % (name, command))
    cron_entry = "# %s | tz=%s overlap=%s missed=%s retries=%d\n%s %s" % (name, tz, overlap, missed, retries, cron_expr, locked_command)
    systemd_timer = "\n".join(
        [
            "# systemd/%s.service" % name,
            "[Unit]",
            "Description=%s" % name,
            "",
            "[Service]",
            "Type=oneshot",
            "WorkingDirectory=%s" % (spec.get("workdir") or "."),
            "ExecStart=/bin/sh -c '%s'" % safe_command.replace("'", "'\\''"),
            "TimeoutStartSec=%d" % int(spec.get("timeout_seconds", 1800)),
            "StandardOutput=journal",
            "StandardError=journal",
            "",
            "# systemd/%s.timer" % name,
            "[Unit]",
            "Description=%s schedule" % name,
            "",
            "[Timer]",
            "OnCalendar=%s" % _systemd_calendar(minute, hour, dom, month, dow, tz),
            "Persistent=%s" % ("true" if missed in ("run_once", "catch_up") else "false"),
            "RandomizedDelaySec=0",
            "",
            "[Install]",
            "WantedBy=timers.target",
        ]
    )
    ci = "\n".join(
        [
            "# .github/workflows/%s.yml" % name,
            "name: %s" % name,
            "on:",
            "  schedule:",
            "    - cron: '%s'" % cron_expr,
            "      timezone: '%s'" % tz,
            "  workflow_dispatch: {}",
            "concurrency:",
            "  group: %s" % name,
            "  cancel-in-progress: %s" % ("true" if overlap == "replace" else "false"),
            "jobs:",
            "  run:",
            "    runs-on: ubuntu-latest",
            "    timeout-minutes: %d" % max(1, int(spec.get("timeout_seconds", 1800)) // 60),
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - name: Execute",
            "        run: |",
            "          %s" % safe_command,
        ]
    )
    schtasks = "schtasks /Create /TN \"%s\" /SC DAILY /ST %02d:%02d /TR \"%s\" /F  # review before running" % (
        name,
        int(hour if hour.isdigit() else 3),
        int(minute if minute.isdigit() else 0),
        safe_command.replace('"', '\\"'),
    )
    return {
        "name": name,
        "cron": cron,
        "cron_expression": cron_expr,
        "timezone": tz,
        "overlap": overlap,
        "missed": missed,
        "retries": retries,
        "linux_cron_line": cron_entry,
        "linux_cron_locked_command": locked_command,
        "systemd": systemd_timer,
        "github_actions": ci,
        "windows_schtasks": schtasks,
        "note": "These are text artifacts only. Nothing is installed, enabled, or registered on any host.",
    }


def _systemd_calendar(minute, hour, dom, month, dow, tz):
    if minute.startswith("*/"):
        return "*-*-* *:%s:00" % ("00/%s" % minute.split("/")[1])
    if hour.startswith("*/"):
        return "*-*-* %s:%s:00" % ("00/%s" % hour.split("/")[1], minute.zfill(2) if minute.isdigit() else "00")
    h = hour if hour.isdigit() else "*"
    m = minute if minute.isdigit() else "0"
    if hour == "*":
        return "*-*-* *:%s:00" % m.zfill(2)
    return "*-*-* %s:%s:00" % (h.zfill(2), m.zfill(2))


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_report(template_text, context):
    def lookup(key):
        value = context.get(key)
        if value is None:
            return "-"
        if isinstance(value, (dict, list)):
            return "`%s`" % json.dumps(value, ensure_ascii=False)
        return str(value)

    rendered = re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m: lookup(m.group(1)), template_text)
    return rendered


def markdown_table(headers, rows):
    if not rows:
        return "_None._"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return "\n".join(out)


def write_audit(config, record):
    path = state_path(config, config.get("approval.audit_file", "audit.jsonl"))
    line = json.dumps(record, ensure_ascii=True, sort_keys=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return str(path)


def append_trace(config, record):
    path = run_path(config, "trace.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    return str(path)


def read_trace(config, limit=20):
    path = run_path(config, "trace.jsonl", create=False)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------------------- #
# Environment doctor
# --------------------------------------------------------------------------- #


def doctor(config, operations=None):
    checks = []

    def add(name, status, detail, hint=None):
        checks.append({"check": name, "status": status, "detail": detail, "hint": hint})

    py = platform.python_version()
    add("python", "ok" if sys.version_info >= (3, 8) else "fail", "Python %s on %s" % (py, platform.platform()), None if sys.version_info >= (3, 8) else "Use Python 3.8 or newer.")
    for dialect in ("bash", "sh", "zsh", "powershell", "cmd"):
        exe = DIALECT_EXECUTABLES[dialect]()
        add("shell:%s" % dialect, "ok" if exe else "warn", exe or "not available on this host", None if exe else "Install it or avoid --shell %s." % dialect)
    try:
        add("sqlite", "ok", "sqlite3 %s" % sqlite3.sqlite_version)
    except Exception as exc:  # pragma: no cover
        add("sqlite", "fail", str(exc))
    root = work_root(config)
    add("work_root", "ok" if root.is_dir() else "fail", str(root), None if root.is_dir() else "paths.work_root must be an existing directory.")
    try:
        probe = state_path(config, ".write-probe")
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("state_dir_writable", "ok", str(probe.parent))
    except OSError as exc:
        add("state_dir_writable", "fail", str(exc), "Fix permissions or point paths.state_dir at a writable directory.")
    for key, asset in (
        ("policy", "policy_file"),
        ("exit_codes", "exit_codes_file"),
        ("shell_dialects", "shell_dialects_file"),
        ("skill_map", "skill_map_file"),
        ("workflow_schema", "workflow_schema_file"),
        ("report_template", "report_template_file"),
    ):
        try:
            asset_path = config.asset_path(asset)
            if asset_path.suffix == ".json":
                json.loads(asset_path.read_text(encoding="utf-8"))
            add("asset:%s" % key, "ok", config.data.get(asset))
        except NexusError as exc:
            add("asset:%s" % key, "fail", exc.message, "Restore the referenced asset file.")
        except json.JSONDecodeError as exc:
            add("asset:%s" % key, "fail", "%s is not valid JSON (%s)" % (asset, exc.msg), "Repair the asset file.")
    smap = config.load_asset("skill_map_file")
    referenced = sorted({op for cat in smap.get("categories", []) for sk in cat.get("skills", []) for op in sk.get("operations", [])})
    if operations:
        missing = [op for op in referenced if op not in operations]
        add(
            "skill_map_operations",
            "fail" if missing else "ok",
            "%d operations referenced, %d unavailable" % (len(referenced), len(missing)),
            ("Unknown operations in the skill map: %s" % ", ".join(missing)) if missing else None,
        )
    add("skill_map", "ok", "%d categories, %d skills" % (smap.get("total_categories", 0), smap.get("total_skills", 0)))
    net = config.get("network", {})
    add("network", "warn" if not net.get("allow_network") else "ok", "allow_network=%s hosts=%s" % (net.get("allow_network"), len(net.get("allowed_hosts", []))), None if net.get("allow_network") else "Network operations stay blocked until allow_network is true and hosts are listed.")
    failures = [c for c in checks if c["status"] == "fail"]
    return {"status": "fail" if failures else "ok", "checks": checks, "config_sources": config.sources, "version": VERSION}


# --------------------------------------------------------------------------- #
# Skill map helpers
# --------------------------------------------------------------------------- #


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "unnamed"


def load_skill_map(config):
    return config.load_asset("skill_map_file")


def find_category(skill_map, query):
    q = (query or "").strip().lower()
    if not q:
        return None
    for cat in skill_map["categories"]:
        if cat["category"].lower() == q:
            return cat
    for cat in skill_map["categories"]:
        if q in cat["category"].lower():
            return cat
    return None


def find_skills(skill_map, query, limit=25):
    q = (query or "").strip().lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if t and len(t) > 1]
    scored = []
    for cat in skill_map["categories"]:
        for skill in cat["skills"]:
            haystack = " ".join([skill["name"], skill["description"], " ".join(skill.get("domains", [])), cat["category"]]).lower()
            score = 0
            if q and q in haystack:
                score += 5
            for token in tokens:
                if token in haystack:
                    score += 2 if token in skill["name"].lower() else 1
            if skill["name"].lower() == q:
                score += 10
            if score:
                scored.append((score, cat, skill))
    exact = [t for t in scored if t[2]["name"].lower() == q]
    if exact:
        scored = exact
    scored.sort(key=lambda t: (-t[0], t[1]["index"], t[2]["name"]))
    out = []
    for score, cat, skill in scored[:limit]:
        entry = dict(skill)
        entry["category"] = cat["category"]
        entry["category_index"] = cat["index"]
        entry["slug"] = slugify(skill["name"])
        entry["score"] = score
        out.append(entry)
    return out
