#!/usr/bin/env python3
"""tools-audio-common.py - shared plumbing for the Audio Production Suite skill tool.

Every other tools-*.py module in this folder imports this file through
``load_sibling("tools-audio-common.py")`` so that hyphenated filenames keep
working without packaging tricks. Standard library only.

Roles provided here:
  * path resolution anchored to this folder (never to the caller's cwd)
  * a single structured error type and result envelope
  * JSON / text read + atomic write helpers
  * resource limits and their validation
  * sibling module loader shared by all tools
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__
import tempfile
import time
import uuid

VERSION = "1.0.0"
SUITE_NAME = "audio-production-suite"
SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))

# --- limits -----------------------------------------------------------------
LIMITS = {
    "max_seconds": 600,          # default audio length accepted per file
    "hard_max_seconds": 7200,    # absolute ceiling even with --max-seconds
    "max_channels": 32,
    "max_files_per_batch": 500,
    "max_batch_seconds": 3600,   # total decoded audio per batch call
    "resample_half_taps": {"low": 8, "medium": 16, "high": 32},
    "analysis_frame": 4096,
    "timeout_seconds": 300,      # per top level command
    "manifest_max_files": 5000,
}


class ToolError(Exception):
    """Structured, user-facing failure. Carries a stable machine code."""

    def __init__(self, code, message, details=None, hint=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.hint = hint

    def as_dict(self):
        out = {"ok": False, "error": {"code": self.code, "message": self.message}}
        if self.details:
            out["error"]["details"] = self.details
        if self.hint:
            out["error"]["hint"] = self.hint
        return out


class Timer:
    """Wall-clock budget guard so long commands can fail fast and cleanly."""

    def __init__(self, seconds=None):
        self.seconds = seconds or LIMITS["timeout_seconds"]
        self.started = time.time()

    def check(self, stage=""):
        elapsed = time.time() - self.started
        if elapsed > self.seconds:
            raise ToolError(
                "timeout",
                "Command exceeded its time budget of %ss%s" % (self.seconds, (" during " + stage) if stage else ""),
                {"elapsed_seconds": round(elapsed, 2)},
                hint="Raise --timeout, shorten the input, or split the work into smaller files.",
            )
        return elapsed

    @property
    def elapsed(self):
        return round(time.time() - self.started, 3)


def canonical_name(filename):
    """One stable module identity per file so every tool shares one class set."""
    base = os.path.basename(filename)
    if base.startswith("tools-"):
        base = base[len("tools-"):]
    if base.endswith(".py"):
        base = base[:-len(".py")]
    return "aps_" + base.replace("-", "_")


# --- module loading ---------------------------------------------------------
_MODULE_CACHE = {}


def load_sibling(filename):
    """Import a sibling tools-*.py file (hyphenated names need importlib)."""
    if filename in _MODULE_CACHE:
        return _MODULE_CACHE[filename]
    path = os.path.join(SKILL_ROOT, filename)
    if not os.path.isfile(path):
        raise ToolError("missing_module", "Required module not found: %s" % filename,
                        hint="Restore the file inside the skill tool folder; do not rename packaged files.")
    modname = canonical_name(filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE[filename] = module
    return module


# --- paths ------------------------------------------------------------------
def skill_root():
    return SKILL_ROOT


def resolve_path(value, must_exist=False, kind="file"):
    if not value:
        raise ToolError("invalid_path", "An empty path was supplied.")
    path = os.path.abspath(os.path.expanduser(str(value)))
    if must_exist:
        exists = os.path.isfile(path) if kind == "file" else os.path.isdir(path)
        if not exists:
            raise ToolError(
                "not_found",
                "%s not found: %s" % ("File" if kind == "file" else "Directory", value),
                {"resolved": path},
                hint="Check the path and that the file exists before running the command.",
            )
    return path


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise ToolError("io_error", "Could not create directory %s: %s" % (path, exc))
    return path


def asset_path(filename):
    """Resolve a packaged asset by its exact, case-sensitive filename."""
    path = os.path.join(SKILL_ROOT, filename)
    if not os.path.isfile(path):
        raise ToolError("missing_asset", "Packaged asset not found: %s" % filename,
                        hint="The asset must sit directly inside the skill tool folder with its exact name.")
    return path


# --- JSON / text ------------------------------------------------------------
def read_json(path, label="JSON file"):
    path = resolve_path(path, must_exist=True)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_json", "%s is not valid JSON: %s" % (label, exc),
                        {"path": path, "line": exc.lineno, "column": exc.colno})
    except OSError as exc:
        raise ToolError("io_error", "Could not read %s: %s" % (label, exc), {"path": path})


def load_asset(filename):
    return read_json(asset_path(filename), label=filename)


def write_json(path, data, pretty=True):
    text = json.dumps(data, indent=2 if pretty else None, ensure_ascii=False)
    write_text(path, text + "\n")
    return path


def write_text(path, text):
    path = resolve_path(path)
    ensure_dir(os.path.dirname(path) or ".")
    directory = os.path.dirname(path) or "."
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".aps-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        raise ToolError("io_error", "Could not write %s: %s" % (path, exc))
    return path


def new_id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:8].upper())


# --- numbers / formatting ---------------------------------------------------
def db_to_lin(db):
    return 10.0 ** (db / 20.0)


def lin_to_db(value, floor=-120.0):
    if value is None or value <= 0:
        return floor
    return max(floor, 20.0 * math.log10(value))


def r2(value, digits=2):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits)
    return value


def human_seconds(seconds):
    if seconds is None:
        return "unknown"
    seconds = float(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(int(minutes), 60)
        return "%dh %02dm %04.1fs" % (hours, minutes, secs)
    return "%dm %04.1fs" % (int(minutes), secs)


def clip(value, low, high):
    return low if value < low else high if value > high else value


# --- result envelope --------------------------------------------------------
def ok(command, **payload):
    out = {"ok": True, "command": command, "version": VERSION}
    out.update(payload)
    return out


def emit(result, as_json=True, out_path=None, text=None):
    """Print a result envelope and optionally persist it."""
    if out_path:
        if as_json:
            write_json(out_path, result)
        else:
            write_text(out_path, text if text is not None else json.dumps(result, indent=2))
    if as_json:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write((text if text is not None else "") + "\n")
    return result


def warn(message, enabled=True):
    if enabled:
        sys.stderr.write("warning: %s\n" % message)


def validate_choice(value, allowed, label):
    if value not in allowed:
        raise ToolError("invalid_argument",
                        "%s must be one of: %s" % (label, ", ".join(sorted(allowed))),
                        {"received": value})
    return value


def seconds_within_limits(seconds, max_seconds=None, channels=2, frames=None):
    """Validate a duration request against soft and hard limits."""
    limit = float(max_seconds if max_seconds else LIMITS["max_seconds"])
    if limit <= 0:
        raise ToolError("invalid_argument", "--max-seconds must be greater than zero")
    if limit > LIMITS["hard_max_seconds"]:
        raise ToolError("limit_exceeded",
                        "Requested limit %ss exceeds the hard ceiling of %ss"
                        % (limit, LIMITS["hard_max_seconds"]),
                        {"hard_max_seconds": LIMITS["hard_max_seconds"]})
    if seconds is not None and seconds > limit + 1e-9:
        raise ToolError("limit_exceeded",
                        "Audio is %.2fs but the allowed limit is %.0fs" % (seconds, limit),
                        {"seconds": r2(seconds), "limit_seconds": limit},
                        hint="Raise --max-seconds (hard ceiling %ss) or shorten the file."
                             % LIMITS["hard_max_seconds"])
    if channels and channels > LIMITS["max_channels"]:
        raise ToolError("limit_exceeded", "Channel count %s exceeds the supported maximum of %s"
                        % (channels, LIMITS["max_channels"]))
    if frames:
        budget = int(frames) * int(channels or 1) * 8
        if budget > 4 * 1024 ** 3:
            raise ToolError("limit_exceeded", "Decoded audio would need about %.1f GB of memory"
                            % (budget / 1024 ** 3),
                            hint="Process the file in shorter segments.")
    return True
