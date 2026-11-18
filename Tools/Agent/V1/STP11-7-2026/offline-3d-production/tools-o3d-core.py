"""Shared runtime for the offline-3d-production Skill Tool.

This module is loaded by the CLI dispatcher through the file-based loader in
``tools-o3d.py`` and registered in ``sys.modules`` as ``o3d_core``. Sibling
modules import it with ``core = sys.modules["o3d_core"]``.

It contains no 3D logic. It only provides the guarded runtime used by every
other tool file: error taxonomy and exit codes, deadlines, path guarding,
atomic JSON I/O, configuration loading, hashing, and audit logging.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
import traceback

TOOL_NAME = "offline-3d-production"
VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

# Exit codes (documented in knowledge-readme.md).
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_INVALID_INPUT = 2
EXIT_APPROVAL_REQUIRED = 3
EXIT_LIMIT = 4

MB = 1024 * 1024

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class O3DError(Exception):
    """Structured, user-facing error.

    Every error carries a stable ``code`` (machine readable), a ``message``
    (human readable), an optional ``hint`` with a recovery path, and optional
    ``details`` used for structured output.
    """

    def __init__(self, code, message, hint=None, details=None, exit_code=EXIT_RUNTIME):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.exit_code = exit_code

    def to_dict(self):
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = json_safe(self.details)
        return out


def invalid(message, hint=None, details=None):
    return O3DError("invalid_input", message, hint, details, EXIT_INVALID_INPUT)


def approval_required(action, hint=None):
    return O3DError(
        "approval_required",
        "Refusing to run '{0}' without explicit approval.".format(action),
        hint or "Re-run the command with --approve after reviewing --dry-run output.",
        {"action": action},
        EXIT_APPROVAL_REQUIRED,
    )


def limit_exceeded(message, hint=None, details=None):
    return O3DError("limit_exceeded", message, hint, details, EXIT_LIMIT)


# ---------------------------------------------------------------------------
# Deadlines / cancellation
# ---------------------------------------------------------------------------


class Deadline:
    """Wall-clock budget for a single command, plus cooperative cancellation."""

    def __init__(self, seconds=None):
        self.seconds = seconds
        self.started = time.monotonic()
        self._cancel = False

    def elapsed(self):
        return time.monotonic() - self.started

    def remaining(self):
        if not self.seconds:
            return None
        return max(0.0, self.seconds - self.elapsed())

    def check(self, stage="operation"):
        if self._cancel:
            raise O3DError(
                "cancelled",
                "Run cancelled during {0}.".format(stage),
                "Partial artifacts were written to the output directory; inspect "
                "the *_partial files and re-run.",
                {"stage": stage},
                EXIT_LIMIT,
            )
        if self.seconds is not None and self.elapsed() > self.seconds:
            raise limit_exceeded(
                "Timeout of {0}s exceeded during {1}.".format(self.seconds, stage),
                "Raise --timeout or reduce the workload (frames, samples, triangles).",
                {"stage": stage, "timeout_seconds": self.seconds},
            )

    def cancel(self):
        self._cancel = True


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def json_safe(value):
    """Recursively convert numpy scalars/arrays and non-finite floats."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # numpy scalar / array duck typing without importing numpy here
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def human_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return "{0:.1f} {1}".format(n, unit)
        n /= 1024.0
    return "{0:.1f} TB".format(n)


def fmt(value, digits=3):
    if isinstance(value, float):
        return ("{0:." + str(digits) + "f}").format(value)
    return str(value)


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def slugify(text, fallback="item"):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or fallback


# ---------------------------------------------------------------------------
# Path guarding
# ---------------------------------------------------------------------------


class PathGuard:
    """Resolves user paths against a base directory and refuses escapes.

    Absolute paths are rejected unless ``allow_outside`` is set, and any path
    that leaves the base directory through ``..`` is refused. This keeps every
    run inside the workspace it was given, which matters for air-gapped
    workstations where a typo must not touch the rest of the machine.
    """

    def __init__(self, base_dir=None, allow_outside=False):
        self.base = os.path.abspath(base_dir or os.getcwd())
        self.allow_outside = bool(allow_outside)

    def resolve(self, path, purpose="path", must_exist=False, create=False):
        if path is None:
            raise invalid("{0} is required.".format(purpose), "Pass a path with the matching option.")
        raw = str(path)
        if not raw.strip():
            raise invalid("{0} is empty.".format(purpose), "Pass a non-empty path.")
        if os.path.isabs(raw) or re.match(r"^[A-Za-z]:[\\/]", raw):
            if not self.allow_outside and not within(self.base, raw):
                raise invalid(
                    "Absolute {0} is not allowed: {1}".format(purpose, raw),
                    "Use a path relative to the workspace, or pass --allow-outside to opt in.",
                )
            full = os.path.abspath(raw)
        else:
            normalized = raw.replace("\\", "/")
            parts = [p for p in normalized.split("/") if p not in ("", ".")]
            if ".." in parts:
                raise invalid(
                    "{0} may not contain '..': {1}".format(purpose, raw),
                    "Keep inputs and outputs inside the workspace.",
                )
            full = os.path.abspath(os.path.join(self.base, *parts))
        if not self.allow_outside and not within(self.base, full):
            raise invalid(
                "{0} resolves outside the workspace: {1}".format(purpose, raw),
                "Use a relative path inside the workspace or pass --allow-outside.",
            )
        if must_exist and not os.path.exists(full):
            raise invalid(
                "{0} does not exist: {1}".format(purpose, raw),
                "Check the path. Run the 'capabilities' command for expected inputs.",
                {"resolved": full},
            )
        if create:
            ensure_dir(full if os.path.splitext(full)[1] == "" else os.path.dirname(full))
        return full


def within(root, path):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives on Windows
        return False


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def read_bytes(path, max_mb=128):
    size = os.path.getsize(path)
    if size > max_mb * MB:
        raise limit_exceeded(
            "Input {0} is {1}, above the {2} MB limit.".format(os.path.basename(path), human_bytes(size), max_mb),
            "Raise limits.max_input_mb in the configuration or split the input.",
            {"size_bytes": size, "limit_mb": max_mb},
        )
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path, max_mb=64, encoding=None):
    data = read_bytes(path, max_mb=max_mb)
    for enc in ([encoding] if encoding else []) + ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise invalid("Could not decode {0} as text.".format(os.path.basename(path)))


def write_text_atomic(path, text):
    ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".o3d-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def write_bytes_atomic(path, data):
    ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".o3d-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def dump_json(path, payload, indent=2):
    text = json.dumps(json_safe(payload), indent=indent, ensure_ascii=False) + "\n"
    write_text_atomic(path, text)
    return path


def load_json(path, purpose="JSON file"):
    if not os.path.exists(path):
        raise invalid("{0} not found: {1}".format(purpose, path), "Check the path or regenerate it.")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise invalid(
            "{0} is not valid JSON: {1}".format(purpose, exc),
            "Fix the JSON syntax, or regenerate the file with the command that produced it.",
        )


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Retries (I/O only; never used to mask logic errors)
# ---------------------------------------------------------------------------


def retry_call(fn, attempts=2, delay=0.05, retry_on=(OSError,)):
    last = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except retry_on as exc:  # pragma: no cover - exercised only under I/O faults
            last = exc
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    raise last


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_FILENAME = "asset-default-config.json"


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            if key in out:
                out[key] = _deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return override


class Config:
    """Resolved configuration: defaults < user file < command-line overrides."""

    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.base_dir = os.path.dirname(path) if path else os.getcwd()

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def resource(self, relative_path, must_exist=True):
        """Resolve a resource referenced by the config, relative to the config file."""
        if os.path.isabs(relative_path):
            raise invalid(
                "Configuration resources must use relative paths: {0}".format(relative_path),
                "Reference the bundled file relative to the Skill Tool folder.",
            )
        full = os.path.abspath(os.path.join(self.base_dir, relative_path))
        if must_exist and not os.path.exists(full):
            raise invalid(
                "Configured resource is missing: {0}".format(relative_path),
                "The Skill Tool folder must be copied whole. Re-check the file name and case.",
                {"resolved": full},
            )
        return full


def find_default_config(explicit=None):
    here = os.path.dirname(os.path.abspath(__file__))
    if explicit:
        return os.path.abspath(explicit)
    candidate = os.path.join(here, DEFAULT_CONFIG_FILENAME)
    if os.path.exists(candidate):
        return candidate
    return None


def load_config(explicit=None, overrides=None):
    config_path = find_default_config(explicit)
    data = {}
    if config_path:
        data = load_json(config_path, "configuration file")
        if not isinstance(data, dict):
            raise invalid("Configuration file must contain a JSON object.", "Regenerate it from the bundled default.")
    if overrides:
        data = _deep_merge(data, overrides)
    cfg = Config(data, config_path)
    _validate_config(cfg)
    return cfg


def _validate_config(cfg):
    required_sections = ("project", "limits", "mesh", "texture", "render", "animation", "qa")
    missing = [s for s in required_sections if not isinstance(cfg.get(s), dict)]
    if missing:
        raise invalid(
            "Configuration is missing required sections: {0}".format(", ".join(missing)),
            "Start from the bundled asset-default-config.json and edit values only.",
        )
    fps = cfg.get("animation.fps", cfg.get("project.fps"))
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise invalid("animation.fps must be a positive number.", "Set a frame rate such as 24 or 30.")
    timeout = cfg.get("limits.timeout_seconds")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise invalid("limits.timeout_seconds must be a positive number or null.")
    if cfg.get("project.up_axis") not in ("X", "Y", "Z"):
        raise invalid("project.up_axis must be one of X, Y, Z.")
    if cfg.get("project.forward_axis") not in ("-X", "X", "-Y", "Y", "-Z", "Z"):
        raise invalid("project.forward_axis must be one of -X, X, -Y, Y, -Z, Z.")
    if cfg.get("project.up_axis") == cfg.get("project.forward_axis"):
        raise invalid("project.forward_axis cannot equal project.up_axis.", "Pick two different axes.")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def audit_log(directory, event, **fields):
    """Append one JSONL audit record. Never raises (logging must not break a run)."""
    try:
        if not directory:
            return None
        ensure_dir(directory)
        path = os.path.join(directory, "audit-log.jsonl")
        record = {"timestamp": now_iso(), "event": event, "tool": TOOL_NAME, "version": VERSION}
        record.update(json_safe(fields))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except Exception:  # pragma: no cover - logging is best effort
        return None


def environment_info():
    return {
        "tool": TOOL_NAME,
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "utc": now_iso(),
        "network_used": False,
    }


def vector(value, size=3, purpose="vector"):
    if value is None:
        raise invalid("{0} is required.".format(purpose))
    if isinstance(value, str):
        parts = [p for p in re.split(r"[,\s]+", value.strip()) if p]
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise invalid("{0} must be numeric: {1}".format(purpose, value))
    else:
        try:
            nums = [float(v) for v in value]
        except (TypeError, ValueError):
            raise invalid("{0} must be a list of numbers.".format(purpose))
    if len(nums) != size:
        raise invalid("{0} must have exactly {1} numbers, got {2}.".format(purpose, size, len(nums)))
    return nums


def error_from_exception(exc):
    if isinstance(exc, O3DError):
        return exc
    return O3DError(
        "runtime_error",
        "{0}: {1}".format(type(exc).__name__, exc),
        "Re-run with --verbose to see the traceback. If it persists, capture the command and report it.",
        {"traceback": traceback.format_exc(limit=6)},
        EXIT_RUNTIME,
    )
