"""Shared runtime core for the offline-neural-video-pipeline skill tool.

This module is loaded by ``tools-onv.py`` through a path-based import, so it can
keep the required ``tools-`` filename prefix while still being importable.

Contents
--------
* version and exit-code constants
* ``OnvError`` structured error type
* JSON / hashing / filesystem helpers
* bounded subprocess runner with timeout, retries and cancellation
* job-spec defaults, schema validation and path resolution
* human and JSON output helpers

No third-party dependency is used.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOL_VERSION = "1.0.0"
TOOL_NAME = "offline-neural-video-pipeline"

# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_INPUT = 2          # invalid input, unreadable file, bad spec
EXIT_VALIDATION = 3     # media validation did not meet policy
EXIT_BLOCKED = 4        # approval, consent or backend gate stopped the job
EXIT_RUNTIME = 5        # unexpected internal failure
EXIT_CANCELLED = 6      # operator cancelled the job

ERROR_EXIT = {
    "E_INPUT": EXIT_INPUT,
    "E_USAGE": EXIT_INPUT,
    "E_NOT_FOUND": EXIT_INPUT,
    "E_SPEC": EXIT_INPUT,
    "E_VALIDATION": EXIT_VALIDATION,
    "E_BLOCKED": EXIT_BLOCKED,
    "E_APPROVAL": EXIT_BLOCKED,
    "E_CONSENT": EXIT_BLOCKED,
    "E_BACKEND": EXIT_BLOCKED,
    "E_CANCELLED": EXIT_CANCELLED,
    "E_RUNTIME": EXIT_RUNTIME,
}

MAX_LOG_LINES = 4000


class OnvError(Exception):
    """Structured, user-facing error with a stable machine-readable code."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code if code in ERROR_EXIT else "E_RUNTIME"
        self.message = str(message)
        self.details = details or {}

    @property
    def exit_code(self):
        return ERROR_EXIT.get(self.code, EXIT_RUNTIME)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def fail(code, message, **details):
    raise OnvError(code, message, details)


# ---------------------------------------------------------------------------
# time / formatting helpers
# ---------------------------------------------------------------------------
def utcnow():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_bytes(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return ("%.0f %s" if unit == "B" else "%.2f %s") % (num, unit)
        num /= 1024.0


def human_duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "n/a"
    if seconds < 60:
        return "%.1fs" % seconds
    minutes, sec = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%dh %02dm %02ds" % (hours, minutes, sec)
    return "%dm %02ds" % (minutes, sec)


def parse_ratio(value, default=None):
    """Parse ``16:9``, ``1.85`` or ``1920x1080`` style ratio strings."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if ":" in text:
        a, b = text.split(":", 1)
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return default
    try:
        return float(text)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# JSON / hashing / fs helpers
# ---------------------------------------------------------------------------
def json_load(path, label="file", default=None, required=True):
    p = Path(path)
    if not p.exists():
        if required:
            fail("E_NOT_FOUND", "%s not found: %s" % (label, p), path=str(p))
        return default
    try:
        with p.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        fail("E_INPUT", "%s is not valid JSON: %s" % (label, exc), path=str(p), line=exc.lineno)
    except OSError as exc:
        fail("E_INPUT", "cannot read %s: %s" % (label, exc), path=str(p))


def json_write(path, obj, indent=2):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=indent, sort_keys=False)
        handle.write("\n")
    tmp.replace(p)
    return p


def read_text(path, label="file", required=True):
    p = Path(path)
    if not p.exists():
        if required:
            fail("E_NOT_FOUND", "%s not found: %s" % (label, p), path=str(p))
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        fail("E_INPUT", "cannot read %s: %s" % (label, exc), path=str(p))


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_dir(path):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def disk_free_bytes(path):
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(str(target)).free
    except OSError:
        return None


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def dir_size(path, limit_files=20000):
    total, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += file_size(os.path.join(root, name)) or 0
            count += 1
            if count >= limit_files:
                return total, count
    return total, count


# ---------------------------------------------------------------------------
# bounded subprocess runner
# ---------------------------------------------------------------------------
class Runner:
    """Run local processes with a hard timeout, retries, log capture and cancel."""

    def __init__(self, cancel_event=None, log_dir=None, verbose=False):
        self.cancel_event = cancel_event
        self.log_dir = Path(log_dir) if log_dir else None
        self.verbose = verbose
        self.history = []

    def _cancelled(self):
        return bool(self.cancel_event is not None and self.cancel_event.is_set())

    def run(self, argv, timeout_s=600, retries=0, cwd=None, log_name=None, env=None, stdin_text=None):
        if not argv:
            fail("E_RUNTIME", "empty command")
        attempts = max(0, int(retries)) + 1
        last = None
        for attempt in range(1, attempts + 1):
            started = time.time()
            if self._cancelled():
                fail("E_CANCELLED", "cancelled before running: %s" % " ".join(map(str, argv[:3])))
            popen_env = dict(os.environ)
            popen_env.setdefault("PYTHONIOENCODING", "utf-8")
            if env:
                popen_env.update({k: str(v) for k, v in env.items()})
            try:
                proc = subprocess.Popen(
                    [str(a) for a in argv],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                    cwd=str(cwd) if cwd else None,
                    env=popen_env,
                    start_new_session=True,
                    text=True,
                    errors="replace",
                )
            except FileNotFoundError:
                fail("E_INPUT", "command not found: %s" % argv[0], command=str(argv[0]))
            except OSError as exc:
                fail("E_INPUT", "cannot start command %s: %s" % (argv[0], exc), command=str(argv[0]))

            if stdin_text is not None:
                try:
                    proc.stdin.write(stdin_text)
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            buffers = {"stdout": [], "stderr": []}

            def _reader(stream, key):
                try:
                    for line in stream:
                        buffers[key].append(line)
                except (OSError, ValueError):
                    pass

            readers = [
                threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True),
                threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True),
            ]
            for thread in readers:
                thread.start()

            deadline = started + max(1, float(timeout_s))
            timed_out, cancelled = False, False
            while proc.poll() is None:
                if self._cancelled():
                    cancelled = True
                    _kill_group(proc)
                    break
                if time.time() > deadline:
                    timed_out = True
                    _kill_group(proc)
                    break
                time.sleep(0.05)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
            for thread in readers:
                thread.join(timeout=5)
            out = "".join(buffers["stdout"])
            err = "".join(buffers["stderr"])
            duration = time.time() - started

            result = {
                "argv": [str(a) for a in argv],
                "returncode": proc.returncode,
                "stdout": out or "",
                "stderr": err or "",
                "duration_s": round(duration, 3),
                "timed_out": timed_out,
                "cancelled": cancelled,
                "attempt": attempt,
                "attempts": attempts,
            }
            if self.log_dir and log_name:
                self._write_log(log_name, attempt, argv, result)
            self.history.append({k: result[k] for k in ("argv", "returncode", "duration_s", "timed_out", "cancelled", "attempt")})
            last = result
            if cancelled:
                fail("E_CANCELLED", "command cancelled: %s" % " ".join(map(str, argv[:3])))
            if timed_out:
                if attempt < attempts:
                    time.sleep(min(2.0 * attempt, 5.0))
                    continue
                fail(
                    "E_RUNTIME",
                    "command timed out after %ss: %s" % (timeout_s, " ".join(map(str, argv[:3]))),
                    argv=result["argv"][:12],
                    stderr_tail=tail(result["stderr"], 20),
                )
            if proc.returncode != 0:
                if attempt < attempts:
                    time.sleep(min(2.0 * attempt, 5.0))
                    continue
                fail(
                    "E_RUNTIME",
                    "command failed (exit %s): %s" % (proc.returncode, " ".join(map(str, argv[:3]))),
                    argv=result["argv"][:12],
                    stderr_tail=tail(result["stderr"], 20),
                )
            return result
        return last

    def _write_log(self, log_name, attempt, argv, result):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / ("%s.attempt%d.log" % (log_name, attempt))
            lines = [
                "$ " + " ".join(map(str, argv)),
                "returncode=%s duration=%ss timed_out=%s cancelled=%s"
                % (result["returncode"], result["duration_s"], result["timed_out"], result["cancelled"]),
                "--- stdout ---",
                result["stdout"],
                "--- stderr ---",
                result["stderr"],
            ]
            path.write_text("\n".join(lines)[-2_000_000:], encoding="utf-8", errors="replace")
        except OSError:
            pass


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass


def which(binary):
    if not binary:
        return None
    if os.path.sep in str(binary):
        p = Path(binary)
        return str(p) if p.exists() and os.access(str(p), os.X_OK) else None
    return shutil.which(str(binary))


def tail(text, lines=25):
    if not text:
        return ""
    parts = [line for line in str(text).strip().splitlines() if line.strip()]
    return "\n".join(parts[-lines:])[-MAX_LOG_LINES:]


# ---------------------------------------------------------------------------
# job spec defaults + validation
# ---------------------------------------------------------------------------
SPEC_DEFAULTS = {
    "spec_version": "1.0",
    "job_id": None,
    "title": None,
    "capability": None,
    "op": None,
    "prompt": None,
    "inputs": [],
    "mode": "auto",
    "preset": "preview",
    "params": {},
    "hardware": {"profile": "auto", "vram_gb": None, "ram_gb": None, "allow_cpu": True},
    "models": {"root": "models", "model_id": None, "files": [], "check_hashes": True},
    "backend": {"command": None, "timeout_s": 900, "retries": 0},
    "output": {"dir": "outputs", "filename": None},
    "delivery": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac", "package": False},
    "rights": {
        "ledger": None,
        "require_consent": False,
        "subject_ids": [],
        "allowed_use": None,
        "license": None,
        "disclosure_required": True,
    },
    "limits": {"timeout_s": 3600, "max_frames": 240, "max_output_mb": 2048},
    "proxy": {"allow_placeholder": False},
    "fallback": {"on_backend_unavailable": "fail"},
    "audio": None,
    "captions": None,
    "compose": None,
}

MODE_CHOICES = ("auto", "backend", "proxy", "validate-only")
FALLBACK_CHOICES = ("fail", "proxy", "skip")
SIMPLE_TOP_LEVEL = (
    "spec_version",
    "job_id",
    "capability",
    "title",
    "mode",
    "op",
    "preset",
    "prompt",
    "inputs",
    "params",
    "hardware",
    "models",
    "backend",
    "output",
    "delivery",
    "rights",
    "limits",
    "proxy",
    "fallback",
    "audio",
    "captions",
    "compose",
    "notes",
)


def deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def validate_spec(raw_spec, base_dir=None):
    """Validate a job spec, fill defaults and return ``(spec, warnings)``."""
    if not isinstance(raw_spec, dict):
        fail("E_SPEC", "job spec must be a JSON object", got=type(raw_spec).__name__)
    warnings = []
    unknown = [key for key in raw_spec if key not in SIMPLE_TOP_LEVEL]
    if unknown:
        warnings.append("unknown top-level spec keys ignored by planner: %s" % ", ".join(sorted(unknown)))

    spec = deep_merge({}, SPEC_DEFAULTS)
    for key, value in raw_spec.items():
        if key in SIMPLE_TOP_LEVEL:
            if isinstance(SPEC_DEFAULTS.get(key), dict) and isinstance(value, dict):
                spec[key] = deep_merge(SPEC_DEFAULTS[key], value)
            else:
                spec[key] = value

    if not spec.get("job_id"):
        fail("E_SPEC", "spec.job_id is required (short stable identifier, e.g. 'demo-001')")
    job_id = str(spec["job_id"]).strip()
    if not job_id or len(job_id) > 96 or any(ch in job_id for ch in "/\\\n\t"):
        fail("E_SPEC", "spec.job_id must be 1-96 characters without path separators", job_id=job_id)
    spec["job_id"] = job_id

    if not spec.get("capability"):
        fail("E_SPEC", "spec.capability is required (a catalog concept slug, concept id, category slug or operation name)")
    spec["capability"] = str(spec["capability"]).strip()

    if spec["mode"] not in MODE_CHOICES:
        fail("E_SPEC", "spec.mode must be one of %s" % ", ".join(MODE_CHOICES), mode=spec["mode"])
    if spec["fallback"].get("on_backend_unavailable") not in FALLBACK_CHOICES:
        fail(
            "E_SPEC",
            "spec.fallback.on_backend_unavailable must be one of %s" % ", ".join(FALLBACK_CHOICES),
            value=spec["fallback"].get("on_backend_unavailable"),
        )

    inputs = spec.get("inputs") or []
    if not isinstance(inputs, list):
        fail("E_SPEC", "spec.inputs must be a list")
    normalised_inputs = []
    for index, item in enumerate(inputs):
        if isinstance(item, str):
            item = {"role": "source", "path": item}
        if not isinstance(item, dict) or not item.get("path"):
            fail("E_SPEC", "spec.inputs[%d] must be a path string or an object with a 'path'" % index)
        entry = {
            "role": str(item.get("role") or "source"),
            "path": resolve_path(item["path"], base_dir),
            "sha256": item.get("sha256"),
        }
        normalised_inputs.append(entry)
    spec["inputs"] = normalised_inputs

    params = spec.get("params") or {}
    if not isinstance(params, dict):
        fail("E_SPEC", "spec.params must be an object")
    for key, minimum in (("width", 16), ("height", 16), ("fps", 1), ("frames", 1), ("steps", 1)):
        if key in params and params[key] is not None:
            try:
                value = int(params[key])
            except (TypeError, ValueError):
                fail("E_SPEC", "spec.params.%s must be an integer" % key, value=params[key])
            if value < minimum:
                fail("E_SPEC", "spec.params.%s must be >= %d" % (key, minimum), value=value)
            params[key] = value
    if "duration_s" in params and params["duration_s"] is not None:
        try:
            params["duration_s"] = float(params["duration_s"])
        except (TypeError, ValueError):
            fail("E_SPEC", "spec.params.duration_s must be a number")
        if params["duration_s"] <= 0:
            fail("E_SPEC", "spec.params.duration_s must be > 0")
    if "seed" in params and params["seed"] is not None:
        try:
            params["seed"] = int(params["seed"])
        except (TypeError, ValueError):
            fail("E_SPEC", "spec.params.seed must be an integer")
    spec["params"] = params

    limits = spec["limits"]
    for key in ("timeout_s", "max_frames", "max_output_mb"):
        try:
            limits[key] = int(limits[key])
        except (TypeError, ValueError):
            fail("E_SPEC", "spec.limits.%s must be an integer" % key)
        if limits[key] <= 0:
            fail("E_SPEC", "spec.limits.%s must be > 0" % key)

    backend = spec["backend"]
    if backend.get("command") is not None:
        if isinstance(backend["command"], list):
            if not all(isinstance(part, str) for part in backend["command"]):
                fail("E_SPEC", "spec.backend.command list entries must be strings")
        elif not isinstance(backend["command"], str) or not backend["command"].strip():
            fail("E_SPEC", "spec.backend.command must be a non-empty string or list of strings")
    try:
        backend["timeout_s"] = int(backend["timeout_s"])
        backend["retries"] = int(backend["retries"])
    except (TypeError, ValueError):
        fail("E_SPEC", "spec.backend.timeout_s and spec.backend.retries must be integers")
    if backend["retries"] < 0 or backend["retries"] > 5:
        fail("E_SPEC", "spec.backend.retries must be between 0 and 5", value=backend["retries"])

    rights = spec["rights"]
    if not isinstance(rights.get("subject_ids"), list):
        fail("E_SPEC", "spec.rights.subject_ids must be a list")
    rights["subject_ids"] = [str(x) for x in rights.get("subject_ids") or []]
    if rights.get("ledger"):
        rights["ledger"] = resolve_path(rights["ledger"], base_dir)

    spec["models"]["root"] = resolve_path(spec["models"].get("root") or "models", base_dir)
    spec["output"]["dir"] = resolve_path(spec["output"].get("dir") or "outputs", base_dir)
    if spec["output"].get("filename") and ("/" in spec["output"]["filename"] or "\\" in spec["output"]["filename"]):
        fail("E_SPEC", "spec.output.filename must not contain path separators", filename=spec["output"]["filename"])

    if spec["prompt"] is not None and not isinstance(spec["prompt"], str):
        fail("E_SPEC", "spec.prompt must be a string")
    spec["base_dir"] = str(base_dir) if base_dir else str(Path.cwd())
    return spec, warnings


def resolve_path(value, base_dir):
    p = Path(str(value)).expanduser()
    if p.is_absolute():
        return str(p)
    root = Path(base_dir) if base_dir else Path.cwd()
    return str((root / p).resolve())


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
class Reporter:
    """Emit consistent human or JSON output envelopes."""

    def __init__(self, json_mode=False, quiet=False, command=None):
        self.json_mode = json_mode
        self.quiet = quiet
        self.command = command

    def emit(self, result, human_lines=None, warnings=None):
        payload = {
            "ok": True,
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "command": self.command,
            "generated_at": utcnow(),
            "result": result,
        }
        if warnings:
            payload["warnings"] = list(warnings)
        if self.json_mode:
            print(json.dumps(payload, indent=2))
            return payload
        if not self.quiet:
            for line in human_lines or []:
                print(line)
            for line in warnings or []:
                print("warning: %s" % line)
        return payload

    def error(self, err, context=None):
        payload = {
            "ok": False,
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "command": self.command,
            "generated_at": utcnow(),
            "error": err.as_dict(),
        }
        if context:
            payload["context"] = context
        if self.json_mode:
            print(json.dumps(payload, indent=2), file=sys.stdout)
        else:
            print("ERROR [%s] %s" % (err.code, err.message), file=sys.stderr)
            details = err.details or {}
            for key in sorted(details):
                value = details[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                print("  %s: %s" % (key, value), file=sys.stderr)
        return payload
