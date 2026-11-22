#!/usr/bin/env python3
"""Shared runtime for the video-production-orchestrator Skill Tool.

Module role: infrastructure only. Path resolution, atomic writes, structured
errors, a minimal JSON-Schema subset validator, timecode math, checksums,
approval tokens, bounded subprocess execution and logging.

All paths handed to this module are resolved relative to the current working
directory of the caller. Nothing here assumes an installation location.

Load this module from a sibling file with:

    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "video_core", pathlib.Path(__file__).with_name("tools-video-core.py"))
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

TOOL_VERSION = "1.0.0"

# ---------------------------------------------------------------- exit codes
EXIT_OK = 0
EXIT_USAGE = 2        # bad arguments / invalid input
EXIT_NOT_FOUND = 3    # missing file or resource
EXIT_GATE = 4         # approval required / quality gate failed
EXIT_EXTERNAL = 5     # ffmpeg / ffprobe failure
EXIT_TIMEOUT = 6      # bounded operation exceeded its budget
EXIT_INTERNAL = 7     # unexpected internal error


class ToolError(Exception):
    """Structured, user-facing failure."""

    def __init__(self, code, message, hint=None, details=None, exit_code=EXIT_USAGE):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.exit_code = exit_code

    def as_dict(self):
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


def usage_error(message, hint=None, details=None):
    return ToolError("usage_error", message, hint, details, EXIT_USAGE)


def validation_error(message, hint=None, details=None):
    return ToolError("validation_error", message, hint, details, EXIT_USAGE)


def not_found(message, hint=None, details=None):
    return ToolError("not_found", message, hint, details, EXIT_NOT_FOUND)


def gate_error(message, hint=None, details=None):
    return ToolError("gate_failed", message, hint, details, EXIT_GATE)


def external_error(message, hint=None, details=None):
    return ToolError("external_failure", message, hint, details, EXIT_EXTERNAL)


# ------------------------------------------------------------------ logging
class Log:
    """Minimal leveled logger writing to stderr, optional run log file."""

    LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40, "quiet": 100}

    def __init__(self, level="info", path=None):
        self.level = self.LEVELS.get(level, 20)
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, tag, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {tag:<5} {msg}"
        if self.LEVELS.get(tag, 20) >= self.level:
            print(line, file=sys.stderr)
        if self.path:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def debug(self, m):
        self._emit("debug", m)

    def info(self, m):
        self._emit("info", m)

    def warn(self, m):
        self._emit("warn", m)

    def error(self, m):
        self._emit("error", m)


# -------------------------------------------------------------- path helpers
def resolve(path, base=None):
    """Resolve a caller-supplied path against the working directory."""
    p = Path(path).expanduser()
    if not p.is_absolute() and base is not None:
        p = Path(base) / p
    return p.resolve()


def ensure_file(path, what="file"):
    p = Path(path)
    if not p.exists():
        raise not_found(f"{what} not found: {p}", "Check the path or generate it first.")
    if not p.is_file():
        raise usage_error(f"{what} is not a regular file: {p}")
    return p


def ensure_dir(path, create=False):
    p = Path(path)
    if p.exists() and not p.is_dir():
        raise usage_error(f"expected a directory but found a file: {p}")
    if not p.exists():
        if create:
            p.mkdir(parents=True, exist_ok=True)
        else:
            raise not_found(f"directory not found: {p}", "Create it or pass --create.")
    return p


def safe_stem(name, fallback="item"):
    """ASCII, hyphenated, filesystem-safe stem (never empty)."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9.]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text[:80] or fallback


def atomic_write_text(path, text, encoding="utf-8"):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=p.suffix)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def atomic_write_json(path, obj):
    return atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_output(path, payload, fmt="json"):
    """Write ``payload`` (dict/list) or raw text to ``path`` and return the path."""
    if fmt == "json":
        return atomic_write_json(path, payload)
    if fmt in ("md", "markdown"):
        return atomic_write_text(path, render_markdown(payload))
    if fmt in ("text", "txt"):
        return atomic_write_text(path, render_text(payload))
    raise usage_error(f"unsupported output format: {fmt}")


def read_json(path, what="JSON file"):
    p = ensure_file(path, what)
    try:
        with p.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise validation_error(
            f"{what} is not valid JSON: {p}",
            hint="Fix the syntax (line %d, column %d)." % (exc.lineno, exc.colno),
        ) from exc


def read_text(path, what="text file", limit_bytes=8 * 1024 * 1024):
    p = ensure_file(path, what)
    size = p.stat().st_size
    if size > limit_bytes:
        raise usage_error(
            f"{what} is larger than the {limit_bytes} byte limit: {p}",
            hint="Split or trim the input before retrying.",
        )
    return p.read_text(encoding="utf-8", errors="replace")


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------- minimal schema validator
def validate_schema(data, schema, path="$", errors=None):
    """Validate with a JSON-Schema subset: type, required, properties,
    additionalProperties(false), enum, minimum, maximum, minLength, pattern,
    items, minItems, maxItems, anyOf/oneOf (first match wins for anyOf)."""
    errors = errors if errors is not None else []
    if schema.get("type"):
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(data, t) for t in types):
            errors.append(f"{path}: expected type {types}, got {type(data).__name__}")
            return errors
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} is not one of {schema['enum']}")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"{path}: {data} < minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"{path}: {data} > maximum {schema['maximum']}")
    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if schema.get("pattern") and not re.search(schema["pattern"], data):
            errors.append(f"{path}: {data!r} does not match pattern {schema['pattern']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in data:
                validate_schema(data[key], sub, f"{path}.{key}", errors)
        if schema.get("additionalProperties") is False:
            extra = [k for k in data if k not in props]
            if extra:
                errors.append(f"{path}: unexpected properties {sorted(extra)}")
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(data):
                validate_schema(item, schema["items"], f"{path}[{i}]", errors)
    if "anyOf" in schema:
        if not any(not validate_schema(data, s, path, []) for s in schema["anyOf"]):
            errors.append(f"{path}: matches none of the anyOf branches")
    return errors


def _type_ok(data, t):
    if t == "object":
        return isinstance(data, dict)
    if t == "array":
        return isinstance(data, list)
    if t == "string":
        return isinstance(data, str)
    if t == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if t == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if t == "boolean":
        return isinstance(data, bool)
    if t == "null":
        return data is None
    return True


def require_schema_file(data, schema_path, what):
    schema = read_json(schema_path, f"{what} schema")
    errors = validate_schema(data, schema)
    if errors:
        raise validation_error(
            f"{what} failed schema validation ({len(errors)} problem(s))",
            hint="Fix the listed fields and retry.",
            details={"errors": errors[:40]},
        )
    return schema


# ---------------------------------------------------------------- timecode
NDF_RATES = {23.976: 24, 24.0: 24, 25.0: 25, 29.97: 30, 30.0: 30, 47.952: 48,
             48.0: 48, 50.0: 50, 59.94: 60, 60.0: 60}


def normalise_fps(fps):
    try:
        value = float(fps)
    except (TypeError, ValueError):
        raise validation_error(f"frame rate is not a number: {fps!r}")
    if not 1.0 <= value <= 240.0:
        raise validation_error(f"frame rate out of supported range 1-240: {value}")
    return value


def is_drop_frame(fps):
    """Drop-frame timecode applies to 29.97 and 59.94 (and their multiples)."""
    value = normalise_fps(fps)
    return any(abs(value - rate) < 0.02 for rate in (29.97, 59.94))


def frames_to_tc(frames, fps=25.0):
    """Convert a frame count to HH:MM:SS:FF (drop-frame for 29.97)."""
    fps = normalise_fps(fps)
    frames = int(frames)
    negative = frames < 0
    frames = abs(frames)
    nominal = int(NDF_RATES.get(round(fps * 1000) / 1000, round(fps)))
    drop = is_drop_frame(fps)
    if drop:
        drop_frames = 4 if nominal == 60 else 2
        frames_per_10min = nominal * 600 - 9 * drop_frames
        frames_per_min = nominal * 60 - drop_frames
        tens, rem = divmod(frames, frames_per_10min)
        frames = frames + 9 * drop_frames * tens
        if rem >= drop_frames:
            frames += drop_frames * ((rem - drop_frames) // frames_per_min)
    ff = frames % nominal
    total_seconds = frames // nominal
    ss, mm, hh = total_seconds % 60, (total_seconds // 60) % 60, total_seconds // 3600
    sep = ";" if drop else ":"
    tc = f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"
    return ("-" + tc) if negative else tc


def tc_to_frames(tc, fps=25.0):
    """Parse HH:MM:SS:FF / HH:MM:SS;FF (or MM:SS:FF) into a frame count."""
    fps = normalise_fps(fps)
    raw = str(tc).strip()
    if not raw:
        raise validation_error("empty timecode")
    negative = raw.startswith("-")
    raw = raw.lstrip("-")
    if ";" in raw or "|" in raw:
        drop = True
    else:
        drop = False
    parts = re.split(r"[:;|.]", raw)
    if len(parts) == 4:
        hh, mm, ss, ff = parts
    elif len(parts) == 3:
        hh, mm, ss, ff = "0", parts[0], parts[1], parts[2]
    else:
        raise validation_error(f"unrecognised timecode: {tc!r}", "Use HH:MM:SS:FF.")
    try:
        hh, mm, ss, ff = (int(x) for x in (hh, mm, ss, ff))
    except ValueError as exc:
        raise validation_error(f"non-numeric timecode: {tc!r}") from exc
    nominal = int(NDF_RATES.get(round(fps * 1000) / 1000, round(fps)))
    if not (0 <= ff < nominal):
        raise validation_error(f"frame field {ff} out of range for {fps} fps timecode {tc!r}")
    if not (0 <= ss < 60 and 0 <= mm < 60):
        raise validation_error(f"timecode {tc!r} has out-of-range seconds/minutes")
    total = ((hh * 60 + mm) * 60 + ss) * nominal + ff
    if drop and is_drop_frame(fps):
        total_minutes = hh * 60 + mm
        total -= (4 if nominal == 60 else 2) * (total_minutes - total_minutes // 10)
    return -total if negative else total


def seconds_to_tc(seconds, fps=25.0):
    return frames_to_tc(round(float(seconds) * normalise_fps(fps)), fps)


def tc_seconds(tc, fps=25.0):
    return tc_to_frames(tc, fps) / normalise_fps(fps)


def format_seconds(seconds):
    seconds = float(seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h}h {m:02d}m {s:04.1f}s"
    if m:
        return f"{m}m {s:04.1f}s"
    return f"{s:04.1f}s"


def parse_duration(text):
    """Parse '90', '90s', '1m30s', '01:30', '00:01:30' into seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip().lower()
    if not raw:
        raise validation_error("empty duration")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    if ":" in raw:
        parts = [float(p) for p in raw.split(":")]
        total = 0.0
        for part in parts:
            total = total * 60 + part
        return total
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?", raw)
    if not m or not any(m.groups()):
        raise validation_error(f"unrecognised duration: {text!r}", "Use 90, 90s, 1m30s or 00:01:30.")
    h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s


# ------------------------------------------------------------ subprocess I/O
def find_binary(*names, env_var=None):
    """Locate an executable without assuming an install path."""
    if env_var and os.environ.get(env_var):
        candidate = os.environ[env_var]
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise not_found(f"{env_var} points to a missing executable: {candidate}")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def run_bounded(cmd, timeout=120.0, cwd=None, env=None, input_text=None):
    """Run a subprocess with a hard timeout. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        raise not_found(f"executable not found: {cmd[0]}", "Install the tool or pass its path.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "timeout",
            f"command exceeded {timeout}s: {cmd[0]}",
            hint="Raise --timeout, shorten the input, or use a proxy/smaller sample.",
            details={"command": cmd[0]},
            exit_code=EXIT_TIMEOUT,
        ) from exc


# ------------------------------------------------------------ approval gates
APPROVAL_SALT = "video-production-orchestrator/approval/v1"


def approval_token(command, targets):
    """Deterministic token proving the caller saw and accepted the exact targets."""
    material = APPROVAL_SALT + "|" + command + "|" + "|".join(sorted(str(t) for t in targets))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def require_approval(command, targets, supplied, log=None, reason="destructive action"):
    """Enforce two-step approval. Raises gate_error with the expected token."""
    expected = approval_token(command, targets)
    if supplied and str(supplied).strip().lower() == expected:
        if log:
            log.info(f"approval accepted for '{command}' (token {expected})")
        return expected
    raise gate_error(
        f"approval required for {reason}: '{command}'",
        hint=(
            "Re-run the same command with --approve-plan to print the token, "
            "then add --approve <token>."
        ),
        details={"command": command, "targets": sorted(str(t) for t in targets),
                 "expected_token": expected},
    )


# ------------------------------------------------------------- shared shapes
def finding(severity, code, message, **extra):
    item = {"severity": severity, "code": code, "message": message}
    item.update(extra)
    return item


def summarise_findings(findings):
    counts = {"info": 0, "warn": 0, "fail": 0}
    for f in findings:
        key = f.get("severity", "info")
        counts[key] = counts.get(key, 0) + 1
    status = "fail" if counts.get("fail") else ("warn" if counts.get("warn") else "pass")
    return {"status": status, **counts, "total": len(findings)}


def render_markdown(payload):
    """Generic Markdown renderer for results that are not hand-templated."""
    lines = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                lines.append(f"## {key}")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(value, indent=2, ensure_ascii=False))
                lines.append("```")
            else:
                lines.append(f"- **{key}**: {value}")
            lines.append("")
    else:
        lines.append("```json")
        lines.append(json.dumps(payload, indent=2, ensure_ascii=False))
        lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def render_text(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def emit(result, as_json=True):
    """Print the machine-readable result to stdout."""
    print(json.dumps(result, indent=2, ensure_ascii=False) if as_json else render_text(result))


def cleanup_temp(paths):
    for path in paths:
        try:
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
        except OSError:
            pass


def errno_name(exc):
    return errno.errorcode.get(getattr(exc, "errno", 0), "unknown")
