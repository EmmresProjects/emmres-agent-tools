#!/usr/bin/env python3
"""caw_runtime - shared runtime helpers for the console-agent-workbench Skill Tool.

Standard library only. This module is imported by the primary entry point
(``tools-console-agent.py``); it is not meant to be executed directly.

Responsibilities
----------------
* skill-root relative resource resolution (no absolute paths in the package)
* structured JSON envelope + exit-code contract
* bounded filesystem walking, hashing primitives, entropy sampling
* bounded, timeout-guarded external command probes
* validation helpers shared by every tool module
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import zlib
from datetime import datetime, timezone

VERSION = "1.0.0"
SKILL_NAME = "console-agent-workbench"

# ---------------------------------------------------------------------------
# Exit-code contract (documented in knowledge-readme.md)
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_POLICY_BLOCK = 3
EXIT_APPROVAL = 4
EXIT_NOT_FOUND = 5
EXIT_CHECK_FAILED = 6

# Startup self-test status vocabulary, taken from the source catalog contract.
STATUS_VALUES = (
    "FOUND",
    "MISSING",
    "WRONG VERSION",
    "BROKEN",
    "BIOS OR FIRMWARE REQUIRED FROM USER",
    "USER-OWNED MEDIA REQUIRED",
    "OFFICIAL SDK REQUIRED",
    "LICENSE REQUIRED",
    "WINDOWS HOST REQUIRED",
    "MACOS HOST REQUIRED",
    "PHYSICAL HARDWARE REQUIRED",
    "OFFICIAL DEVKIT REQUIRED",
    "GPU REQUIRED",
    "FPGA HARDWARE REQUIRED",
    "VIRTUALIZATION REQUIRED",
    "NETWORK REQUIRED",
)

DEFAULT_LIMITS = {
    "max_files": 20000,
    "max_depth": 24,
    "max_read_bytes": 65536,
    "max_hash_bytes": 0,          # 0 = no per-file cap
    "command_timeout_seconds": 10.0,
    "overall_timeout_seconds": 300.0,
    "retries": 0,
}


class ValidationError(Exception):
    """Raised for malformed operator input; mapped to EXIT_USAGE."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or []


class NotFoundError(Exception):
    """Raised when a required file/resource is absent; mapped to EXIT_NOT_FOUND."""


class ApprovalRequired(Exception):
    """Raised when a gated action lacks a valid approval token (EXIT_APPROVAL)."""

    def __init__(self, message, action=None, problems=None):
        super().__init__(message)
        self.message = message
        self.action = action
        self.problems = list(problems or [])


# ---------------------------------------------------------------------------
# Time and identity
# ---------------------------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    dt = dt or utc_now()
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted). Returns aware dt."""
    if not isinstance(text, str) or not text:
        raise ValidationError("timestamp must be a non-empty ISO-8601 string")
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise ValidationError(f"invalid ISO-8601 timestamp: {text!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_id(prefix="run"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Skill-root relative resource resolution
# ---------------------------------------------------------------------------
def skill_root():
    """Folder holding the Skill Tool files (script directory)."""
    return os.path.dirname(os.path.abspath(__file__))


def resource_default(filename):
    """Default path for a packaged resource, always relative to the skill root."""
    return os.path.join(skill_root(), filename)


def resolve_input(path, label="path"):
    """Resolve an operator-supplied path; accepts ``-`` only where documented."""
    if path is None:
        raise ValidationError(f"{label} is required")
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return p


def require_existing(path, label="path", kind=None):
    p = resolve_input(path, label)
    if not os.path.exists(p):
        raise NotFoundError(f"{label} does not exist: {path}")
    if kind == "file" and not os.path.isfile(p):
        raise ValidationError(f"{label} must be a file: {path}")
    if kind == "dir" and not os.path.isdir(p):
        raise ValidationError(f"{label} must be a directory: {path}")
    return p


def load_json(path, label="JSON file"):
    p = require_existing(path, label, kind="file")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} is not valid JSON ({path}): {exc}")


def write_json(path, obj, indent=2):
    p = resolve_input(path)
    ensure_dir(os.path.dirname(p))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def write_text(path, text):
    p = resolve_input(path)
    ensure_dir(os.path.dirname(p))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, p)
    return p


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def workdir(path, *parts):
    base = resolve_input(path or "caw-work")
    target = os.path.join(base, *parts) if parts else base
    ensure_dir(target)
    return target


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------
ALGORITHMS = ("crc32", "md5", "sha1", "sha256", "blake2b")
_LEGACY = ("md5", "sha1")


def empty_digests(algorithms):
    return {a: (0 if a == "crc32" else None) for a in algorithms}


def hash_file(path, algorithms=("crc32", "sha1", "sha256"), block_size=1 << 20,
              max_bytes=0, deadline=None):
    """Hash one file in a single pass.

    ``crc32`` is tracked as a rolling integer, other algorithms via hashlib.
    ``max_bytes`` > 0 stops after that many bytes (partial=True).
    ``deadline`` is a monotonic timestamp; exceeding it raises TimeoutError.
    """
    algos = [a.lower() for a in algorithms]
    unknown = [a for a in algos if a not in ALGORITHMS]
    if unknown:
        raise ValidationError(f"unsupported algorithms: {', '.join(unknown)}",
                              [f"choose from {', '.join(ALGORITHMS)}"])
    crc = 0
    hashers = {a: hashlib.new("blake2b" if a == "blake2b" else a) for a in algos if a != "crc32"}
    size = 0
    partial = False
    with open(path, "rb") as fh:
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"hashing deadline exceeded while reading {path}")
            if max_bytes and size >= max_bytes:
                partial = True
                break
            want = block_size
            if max_bytes:
                want = min(want, max_bytes - size)
            chunk = fh.read(want)
            if not chunk:
                break
            size += len(chunk)
            crc = zlib.crc32(chunk, crc)
            for h in hashers.values():
                h.update(chunk)
    out = {}
    for a in algos:
        out[a] = f"{(crc & 0xFFFFFFFF):08x}" if a == "crc32" else hashers[a].hexdigest()
    return {"size": size, "hashes": out, "partial": partial}


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_obj(obj):
    return sha256_text(canonical_json(obj))


def entropy(sample):
    """Shannon entropy in bits/byte of a byte string (0.0 - 8.0)."""
    if not sample:
        return 0.0
    counts = [0] * 256
    for b in sample:
        counts[b] += 1
    n = len(sample)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return round(ent, 4)


def human_bytes(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TiB"


# ---------------------------------------------------------------------------
# Bounded filesystem walking
# ---------------------------------------------------------------------------
def walk_bounded(root, max_files=20000, max_depth=24, follow_symlinks=False,
                 skip_names=(".git", "__pycache__", ".arena", "node_modules", ".venv")):
    """Yield ``(abs_path, rel_path, depth)`` for files under *root*, bounded.

    Returns a dict summary via StopIteration value when iterated with ``collect``.
    Never follows symlinks unless explicitly requested, never crosses devices.
    """
    root = resolve_input(root)
    if os.path.isfile(root):
        yield root, os.path.basename(root), 0
        return
    if not os.path.isdir(root):
        raise NotFoundError(f"path does not exist: {root}")
    root_dev = os.stat(root).st_dev
    seen = 0
    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in skip_names:
                continue
            is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
            if entry.is_symlink() and not follow_symlinks:
                yield entry.path, os.path.relpath(entry.path, root), depth + 1
                seen += 1
                if seen >= max_files:
                    return
                continue
            if is_dir:
                if depth + 1 > max_depth:
                    continue
                try:
                    if os.stat(entry.path).st_dev != root_dev:
                        continue
                except OSError:
                    continue
                stack.append((entry.path, depth + 1))
            else:
                yield entry.path, os.path.relpath(entry.path, root), depth + 1
                seen += 1
                if seen >= max_files:
                    return


# ---------------------------------------------------------------------------
# External command probes (bounded, timeout-guarded, no shell)
# ---------------------------------------------------------------------------
def run_command(args, timeout=10.0, cwd=None, stdin_bytes=None, env_extra=None):
    """Run a bounded external command without a shell.

    Returns ``{executed, returncode, timed_out, stdout, stderr, duration_ms}``.
    """
    if isinstance(args, str):
        args = shlex.split(args)
    if not args:
        raise ValidationError("command must not be empty")
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")
    env.setdefault("NO_COLOR", "1")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    started = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            input=stdin_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.1, float(timeout)),
            cwd=cwd,
            env=env,
            shell=False,
            check=False,
        )
        rc = proc.returncode
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        rc = None
        out = (exc.stdout or b"").decode("utf-8", "replace") if exc.stdout else ""
        err = (exc.stderr or b"").decode("utf-8", "replace") if exc.stderr else ""
        timed_out = True
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return {
            "executed": False,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "executed": True,
        "returncode": rc,
        "timed_out": timed_out,
        "stdout": out[:4000],
        "stderr": err[:2000],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def which(binary):
    return shutil.which(binary)


def first_line(*texts):
    for t in texts:
        if not t:
            continue
        for line in t.splitlines():
            line = line.strip()
            if line:
                return line[:200]
    return ""


def probe_binary(spec, timeout=10.0, host_type=None):
    """Probe one binary described by the probe manifest.

    Returns a record carrying every field required by the catalog's startup
    self-test contract.
    """
    host = host_type or host_os()
    record = {
        "capability_id": spec.get("capability_id", spec.get("binary", "unknown")),
        "status": "MISSING",
        "command_or_probe": " ".join([spec.get("binary", "?")] + list(spec.get("version_args", []))),
        "detected_version": "",
        "host_type": host,
        "architecture": host_arch(),
        "license_state": "unknown",
        "hardware_state": "not-applicable",
        "required_user_material": spec.get("required_user_material", ""),
        "test_result": "",
        "evidence_path": "",
        "recommended_action": spec.get("remediation", ""),
        "priority": spec.get("priority", "P2"),
        "capability_class": spec.get("capability_class", "tool"),
        "duration_ms": 0,
    }
    required_host = spec.get("host_type", "any")
    if required_host not in ("any", host):
        record["status"] = "WINDOWS HOST REQUIRED" if required_host == "windows" else (
            "MACOS HOST REQUIRED" if required_host == "macos" else "PHYSICAL HARDWARE REQUIRED")
        record["test_result"] = f"skipped: requires {required_host} host, current host is {host}"
        return record
    binary = spec.get("binary")
    if not binary:
        record["status"] = "MISSING"
        record["test_result"] = "probe manifest entry has no binary"
        return record
    found = which(binary)
    if not found:
        # allow a second candidate name (e.g. analyzeHeadless vs ghidraRun)
        for alt in spec.get("alternatives", []) or []:
            found = which(alt)
            if found:
                break
    if not found:
        record["status"] = "MISSING"
        record["test_result"] = "binary not found on PATH"
        return record
    result = run_command([found] + list(spec.get("version_args", ["--version"])), timeout=timeout)
    record["duration_ms"] = result["duration_ms"]
    if result["timed_out"]:
        record["status"] = "BROKEN"
        record["test_result"] = f"probe timed out after {timeout}s"
        return record
    if not result["executed"]:
        record["status"] = "BROKEN"
        record["test_result"] = result["stderr"][:300]
        return record
    record["detected_version"] = first_line(result["stdout"], result["stderr"])
    expected = spec.get("expected_version_prefix")
    if result["returncode"] not in (0, None) and not record["detected_version"]:
        record["status"] = "BROKEN"
        record["test_result"] = f"exit code {result['returncode']}"
    elif expected and not record["detected_version"].startswith(expected) and expected not in record["detected_version"]:
        record["status"] = "WRONG VERSION"
        record["test_result"] = f"version string does not contain {expected!r}"
    else:
        record["status"] = "FOUND"
        record["test_result"] = "version command executed"
    if spec.get("requires_license"):
        record["license_state"] = "required-not-verified"
    if spec.get("requires_hardware"):
        record["hardware_state"] = spec["requires_hardware"]
    return record


def host_os():
    platform = sys.platform
    if platform.startswith("linux"):
        return "linux"
    if platform.startswith("darwin"):
        return "macos"
    if platform.startswith("win"):
        return "windows"
    return platform


def host_arch():
    import platform as _p
    machine = (_p.machine() or "unknown").lower()
    return {"amd64": "x86_64", "x64": "x86_64"}.get(machine, machine)


def host_info():
    import platform as _p
    info = {
        "os": host_os(),
        "architecture": host_arch(),
        "python": _p.python_version(),
        "kernel": _p.release(),
        "cpu_count": os.cpu_count() or 1,
    }
    if info["os"] == "linux":
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        info["distribution"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
    return info


def hardware_probe(paths=None):
    """Read-only detection of generic device classes.

    Only presence checks on well-known device nodes are performed. This makes
    no claim about which console hardware is attached; the operator confirms
    the identity of physical devices (see knowledge-readme.md).
    """
    if paths is None:
        import glob
        paths = {
            "optical_drives": sorted(glob.glob("/dev/sr*")),
            "serial_devices": sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")),
            "card_readers": sorted(glob.glob("/dev/sd[a-z]")),
            "usb_devices": sorted(glob.glob("/sys/bus/usb/devices/*/idVendor")),
        }
    summary = {
        "optical_drive_present": bool(paths.get("optical_drives")),
        "serial_reader_present": bool(paths.get("serial_devices")),
        "removable_block_device_present": bool(paths.get("card_readers")),
        "usb_device_count": len(paths.get("usb_devices", [])),
        "gpu_present": os.path.exists("/dev/dri") or bool(
            shutil.which("nvidia-smi") or shutil.which("vulkaninfo")),
        "detection_scope": "device-node presence only; identity requires operator confirmation",
    }
    return summary


# ---------------------------------------------------------------------------
# Envelope / output
# ---------------------------------------------------------------------------
def envelope(command, data, code=EXIT_OK, warnings=None, errors=None, **extra):
    body = {
        "ok": code == EXIT_OK,
        "skill": SKILL_NAME,
        "skill_version": VERSION,
        "command": command,
        "exit_code": code,
        "generated_at": iso(),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "data": data,
    }
    body.update(extra)
    return body


def emit(body, human_text=None, fmt="json", stream=None):
    stream = stream or sys.stdout
    if fmt == "text" and human_text:
        stream.write(human_text.rstrip() + "\n")
    else:
        stream.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    return body["exit_code"]


def log(message, verbose=False):
    if verbose:
        sys.stderr.write(f"[console-agent-workbench] {message}\n")


def parse_pairs(values, label="item"):
    """Parse repeated ``key=value`` options into a dict (rejects malformed)."""
    out = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValidationError(f"{label} must use key=value form: {raw!r}")
        k, v = raw.split("=", 1)
        k = k.strip()
        if not k:
            raise ValidationError(f"{label} has an empty key: {raw!r}")
        out[k] = v.strip()
    return out


def clamp_int(value, name, low, high):
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer, got {value!r}")
    if v < low or v > high:
        raise ValidationError(f"{name} must be between {low} and {high}, got {v}")
    return v
