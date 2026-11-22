#!/usr/bin/env python3
"""Shared runtime for the powerful-extractor-compressor Skill Tool.

This module is imported by every other tools-* file. It owns:

* exit codes and the structured error type (:class:`PecError`)
* configuration discovery, merge order and validation
* job envelope creation (id, fingerprint, timing, counters, status)
* JSON-lines logging with correlation fields
* cancellation, deadline and retry helpers
* the local approval gate for destructive / sensitive actions
* small filesystem and formatting helpers shared by all tools

It is never meant to be the primary entry point. Use ``tools-pec.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

SKILL_NAME = "powerful-extractor-compressor"
VERSION = "1.0.0"
RESULT_SCHEMA = "pec.result/1"
MANIFEST_SCHEMA = "pec.manifest/1"
LINEAGE_SCHEMA = "pec.lineage/1"
CHECKPOINT_SCHEMA = "pec.checkpoint/1"

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Exit codes (documented in knowledge-readme.md)
# --------------------------------------------------------------------------
EXIT_OK = 0
EXIT_USAGE = 2          # invalid arguments, invalid configuration, bad input
EXIT_POLICY = 3         # safety / policy refusal (traversal, bomb, deny-list)
EXIT_INTEGRITY = 4      # hash mismatch, verification failure, corrupt archive
EXIT_PARTIAL = 5        # completed with recorded per-entry failures
EXIT_DEPENDENCY = 6     # required backend / codec not available
EXIT_APPROVAL = 7       # approval token missing for a gated action
EXIT_IO = 8             # I/O, capacity or permission problem
EXIT_CANCELLED = 9      # interrupted by signal or deadline
EXIT_INTERNAL = 1       # unexpected failure


class PecError(Exception):
    """Structured, user-facing failure carrying an exit code."""

    def __init__(self, message: str, code: int = EXIT_USAGE, kind: str = "error", detail: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.kind = kind
        self.detail = detail

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "detail": self.detail}


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_job_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def human_bytes(n: Any) -> str:
    try:
        value = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0 or unit == "PiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{n} B"


def parse_size(text: Any) -> int:
    """Parse ``1024``, ``10K``, ``4MiB``, ``2G`` into a byte count."""
    if isinstance(text, (int, float)):
        return int(text)
    if text is None:
        raise PecError("size value is required", EXIT_USAGE)
    s = str(text).strip().lower().replace("_", "")
    if not s:
        raise PecError("empty size value", EXIT_USAGE)
    mult = 1
    for suffix, factor in (
        ("kib", 1024), ("mib", 1024 ** 2), ("gib", 1024 ** 3), ("tib", 1024 ** 4),
        ("kb", 1000), ("mb", 1000 ** 2), ("gb", 1000 ** 3), ("tb", 1000 ** 4),
        ("k", 1024), ("m", 1024 ** 2), ("g", 1024 ** 3), ("t", 1024 ** 4), ("b", 1),
    ):
        if s.endswith(suffix):
            mult = factor
            s = s[: -len(suffix)].strip()
            break
    try:
        return int(float(s) * mult)
    except ValueError as exc:
        raise PecError(f"cannot parse size value {text!r}", EXIT_USAGE, detail=str(exc)) from exc


def parse_duration(text: Any) -> float:
    """Parse ``30``, ``45s``, ``10m``, ``2h`` into seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower()
    mult = 1.0
    for suffix, factor in (("ms", 0.001), ("s", 1.0), ("m", 60.0), ("h", 3600.0), ("d", 86400.0)):
        if s.endswith(suffix):
            mult = factor
            s = s[: -len(suffix)].strip()
            break
    try:
        return float(s) * mult
    except ValueError as exc:
        raise PecError(f"cannot parse duration value {text!r}", EXIT_USAGE, detail=str(exc)) from exc


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def dig(data: Any, dotted: str, default: Any = None) -> Any:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def write_json_atomic(path: str, payload: Any, indent: int = 2) -> str:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(3)}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=indent, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise PecError(f"invalid JSON in {path}: {exc}", EXIT_USAGE) from exc


def append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"kind": "corrupt-checkpoint-line", "raw": line[:200]})
    return records


def load_sibling(filename: str, alias: str | None = None):
    """Load a sibling module whose filename contains hyphens."""
    alias = alias or filename.replace("-", "_").replace(".py", "")
    if alias in sys.modules:
        return sys.modules[alias]
    path = os.path.join(HERE, filename)
    if not os.path.exists(path):
        raise PecError(f"missing companion module: {filename}", EXIT_INTERNAL)
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise PecError(f"cannot load companion module: {filename}", EXIT_INTERNAL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def asset_path(name: str) -> str:
    """Resolve ``asset-*.json`` relative to this tool folder."""
    return os.path.join(HERE, name)


def load_text_asset(name: str, default: str | None = None) -> str:
    """Read a text asset (template) relative to this tool folder."""
    path = asset_path(name)
    if not os.path.exists(path):
        if default is not None:
            return default
        raise PecError(f"missing asset file {name}", EXIT_DEPENDENCY)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_asset(name: str, default: Any = None) -> Any:
    path = asset_path(name)
    if not os.path.exists(path):
        if default is not None:
            return default
        raise PecError(f"missing asset file {name}", EXIT_DEPENDENCY)
    return read_json(path)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
CONFIG_ENV = "PEC_CONFIG"
STATE_ENV = "PEC_STATE_DIR"

BUILTIN_DEFAULTS: dict = {
    "version": VERSION,
    "state_dir": ".pec-state",
    "limits": {
        "max_entries": 200_000,
        "max_total_bytes": 21_474_836_480,
        "max_entry_bytes": 8_589_934_592,
        "max_ratio": 200.0,
        "max_depth": 5,
        "max_path_length": 255,
        "max_seconds": 3600.0,
        "max_scan_bytes": 4_194_304,
        "io_chunk_bytes": 1_048_576,
        "max_workers": 4,
        "retries": 2,
        "retry_backoff_seconds": 0.5,
        "max_report_entries": 50_000,
        "max_volume_size": 0,
    },
    "policy": {
        "overwrite": "never",              # never | suffix | replace (replace needs approval)
        "allow_symlinks": False,
        "allow_hardlinks": False,
        "allow_absolute_paths": False,
        "follow_nested": True,
        "strip_executable_bits": True,
        "quarantine_permissions": "0500",
        "deny_extensions": [".exe", ".dll", ".scr", ".com", ".pif", ".bat", ".cmd",
                            ".vbs", ".ps1", ".msi", ".lnk", ".jar", ".hta", ".reg"],
        "protected_roots": ["/etc", "/usr", "/bin", "/sbin", "/boot", "/var/lib", "C:/Windows", "C:/Program Files"],
        "allowed_output_roots": [],
        "requires_approval": ["overwrite", "delete", "decrypt", "publish-encrypted", "dispose"],
        "metadata_junk": [".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX/", ".git/", "node_modules/", "__pycache__/"],
        "secret_gate": "warn",             # warn | block
        "max_name_length": 200,
    },
    "compression": {
        "format": "zip",
        "level": 6,
        "deterministic": True,
        "store_extensions": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp3", ".mp4",
                             ".m4a", ".avi", ".mkv", ".mov", ".zip", ".gz", ".xz", ".bz2",
                             ".7z", ".rar", ".zst", ".pdf", ".docx", ".xlsx", ".pptx"],
        "volume_size": 0,
        "include_manifest": True,
        "root_folder": True,
    },
    "extraction": {
        "nest": False,
        "quarantine": False,
        "checkpoint": True,
        "publish": "atomic",               # atomic | direct
        "organize": "none",                # none | by-content-type
        "hash_algorithm": "sha256",
        "default_password_env": "PEC_ARCHIVE_PASSWORD",
    },
    "scanner": {
        "enable_secret_scan": True,
        "max_findings": 500,
        "min_confidence": "medium",
        "scan_text_extensions": [".txt", ".md", ".json", ".yaml", ".yml", ".ini", ".cfg",
                                 ".env", ".csv", ".xml", ".py", ".js", ".ts", ".sh", ".ps1",
                                 ".java", ".go", ".rb", ".php", ".sql", ".html", ".css", ".toml"],
    },
    "notify": {
        "webhook_url": "",
        "allowlist": [],
        "timeout_seconds": 5.0,
        "retries": 1,
        "hmac_secret_env": "PEC_WEBHOOK_SECRET",
    },
    "backends": {
        "prefer_native": True,
        "seven_zip": "",
        "unrar": "",
        "sevenzip_module": "py7zr",
        "rar_module": "rarfile",
        "zstd_module": "zstandard",
    },
    "recipes_file": "asset-recipes.json",
    "content_types_file": "asset-content-types.json",
    "secret_patterns_file": "asset-secret-patterns.json",
}


@dataclass
class Config:
    data: dict
    source_chain: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, explicit_path: str | None = None) -> "Config":
        chain: list[str] = ["builtin"]
        env_path = os.environ.get(CONFIG_ENV)
        bundled_path = asset_path("asset-default-config.json")
        data = dict(BUILTIN_DEFAULTS)
        if os.path.exists(bundled_path):
            try:
                data = deep_merge(data, read_json(bundled_path, {}) or {})
                chain.append("asset-default-config.json")
            except PecError:
                chain.append("asset-default-config.json (ignored: invalid)")
        for path, label in ((env_path, f"env:{CONFIG_ENV}"), (explicit_path, explicit_path)):
            if path:
                if not os.path.exists(path):
                    raise PecError(f"config file not found: {path}", EXIT_USAGE)
                data = deep_merge(data, read_json(path, {}) or {})
                chain.append(label or "cli")
        state_env = os.environ.get(STATE_ENV)
        if state_env:
            data["state_dir"] = state_env
            chain.append(f"env:{STATE_ENV}")
        return cls(data=data, source_chain=chain)

    def get(self, dotted: str, default: Any = None) -> Any:
        return dig(self.data, dotted, default)

    # -- convenience accessors -------------------------------------------
    @property
    def limits(self) -> dict:
        return self.data.setdefault("limits", {})

    @property
    def policy(self) -> dict:
        return self.data.setdefault("policy", {})

    def state_dir(self, override: str | None = None) -> str:
        path = os.path.abspath(override or self.get("state_dir", ".pec-state"))
        os.makedirs(path, exist_ok=True)
        return path

    def copy(self) -> "Config":
        return Config(data=json.loads(json.dumps(self.data)), source_chain=list(self.source_chain))


CONFIG_RULES: list[tuple[str, type, Any, Any, str]] = [
    ("state_dir", str, None, None, "directory where jobs, logs and checkpoints live"),
    ("limits.max_entries", int, 1, 50_000_000, "maximum entries accepted from one archive"),
    ("limits.max_total_bytes", int, 1, 10 ** 15, "maximum expanded bytes for one job"),
    ("limits.max_entry_bytes", int, 1, 10 ** 15, "maximum bytes for a single entry"),
    ("limits.max_ratio", (int, float), 1, 1_000_000, "expanded/compressed ratio ceiling"),
    ("limits.max_depth", int, 0, 20, "nested archive recursion depth"),
    ("limits.max_path_length", int, 32, 32_000, "maximum output path length"),
    ("limits.max_seconds", (int, float), 0.1, 604_800, "wall-clock budget per job"),
    ("limits.io_chunk_bytes", int, 4096, 64 * 1024 * 1024, "stream chunk size"),
    ("limits.max_workers", int, 1, 64, "parallel workers for batch jobs"),
    ("limits.retries", int, 0, 10, "retry attempts for transient failures"),
    ("limits.retry_backoff_seconds", (int, float), 0, 300, "base backoff between retries"),
    ("limits.max_volume_size", int, 0, 10 ** 13, "default volume split size in bytes (0 = off)"),
    ("policy.overwrite", str, None, None, "never | suffix | replace"),
    ("policy.allow_symlinks", bool, None, None, "extract symbolic links that stay inside the root"),
    ("policy.allow_hardlinks", bool, None, None, "extract hard links that stay inside the root"),
    ("policy.allow_absolute_paths", bool, None, None, "never enable for untrusted input"),
    ("policy.strip_executable_bits", bool, None, None, "drop exec bits from extracted files"),
    ("policy.secret_gate", str, None, None, "warn | block"),
    ("compression.format", str, None, None, "default archive format"),
    ("compression.level", int, 0, 9, "default compression level"),
    ("compression.deterministic", bool, None, None, "stable timestamps and ordering"),
    ("extraction.publish", str, None, None, "atomic | direct"),
    ("extraction.organize", str, None, None, "none | by-content-type"),
    ("extraction.hash_algorithm", str, None, None, "sha256 | sha512 | blake2b"),
    ("scanner.enable_secret_scan", bool, None, None, "run secret scan during extract"),
    ("notify.timeout_seconds", (int, float), 0.1, 120, "webhook timeout"),
]


def validate_config(data: dict) -> list[dict]:
    problems: list[dict] = []
    for dotted, types, low, high, description in CONFIG_RULES:
        value = dig(data, dotted, None if types is not bool else None)
        if value is None:
            problems.append({"field": dotted, "issue": "missing", "expected": description})
            continue
        if types is not None and not isinstance(value, types):
            problems.append({"field": dotted, "issue": "wrong-type",
                             "expected": types.__name__ if isinstance(types, type) else "number"})
            continue
        if low is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < low or value > high:
                problems.append({"field": dotted, "issue": "out-of-range",
                                 "expected": f"{low} <= value <= {high}", "actual": value})
    if str(dig(data, "policy.overwrite", "never")) not in {"never", "suffix", "replace"}:
        problems.append({"field": "policy.overwrite", "issue": "invalid-choice",
                         "expected": "never | suffix | replace"})
    if str(dig(data, "policy.secret_gate", "warn")) not in {"warn", "block"}:
        problems.append({"field": "policy.secret_gate", "issue": "invalid-choice",
                         "expected": "warn | block"})
    if str(dig(data, "extraction.publish", "atomic")) not in {"atomic", "direct"}:
        problems.append({"field": "extraction.publish", "issue": "invalid-choice",
                         "expected": "atomic | direct"})
    if str(dig(data, "extraction.organize", "none")) not in {"none", "by-content-type"}:
        problems.append({"field": "extraction.organize", "issue": "invalid-choice",
                         "expected": "none | by-content-type"})
    if str(dig(data, "extraction.hash_algorithm", "sha256")) not in {"sha256", "sha512", "blake2b", "md5", "sha1"}:
        problems.append({"field": "extraction.hash_algorithm", "issue": "invalid-choice",
                         "expected": "sha256 | sha512 | blake2b"})
    if int(dig(data, "limits.max_entry_bytes", 0) or 0) > int(dig(data, "limits.max_total_bytes", 0) or 0):
        problems.append({"field": "limits.max_entry_bytes", "issue": "inconsistent",
                         "expected": "must not exceed limits.max_total_bytes"})
    volume = dig(data, "compression.volume_size", 0) or 0
    if isinstance(volume, (int, float)) and volume and volume < 65_536:
        problems.append({"field": "compression.volume_size", "issue": "out-of-range",
                         "expected": "0 or >= 65536 bytes"})
    return problems


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
class Logger:
    """JSON-lines logger correlated by job id."""

    def __init__(self, state_dir: str, job_id: str, echo: bool = True, quiet: bool = False):
        self.state_dir = state_dir
        self.job_id = job_id
        self.echo = echo
        self.quiet = quiet
        log_dir = os.path.join(state_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, f"{job_id}.jsonl")
        self.events = 0

    def write(self, level: str, event: str, message: str = "", **fields: Any) -> None:
        record = {"ts": utcnow(), "job_id": self.job_id, "level": level,
                  "event": event, "message": message}
        record.update({k: v for k, v in fields.items() if v is not None})
        append_jsonl(self.path, record)
        self.events += 1
        if self.echo and not self.quiet and level in {"warning", "error", "notice"}:
            sys.stderr.write(f"[{level}] {event}: {message}\n")

    def info(self, event: str, message: str = "", **fields: Any) -> None:
        self.write("info", event, message, **fields)

    def warn(self, event: str, message: str = "", **fields: Any) -> None:
        self.write("warning", event, message, **fields)

    def error(self, event: str, message: str = "", **fields: Any) -> None:
        self.write("error", event, message, **fields)

    def notice(self, event: str, message: str = "", **fields: Any) -> None:
        self.write("notice", event, message, **fields)


# --------------------------------------------------------------------------
# Cancellation, deadline, retries
# --------------------------------------------------------------------------
class Cancellation:
    """Tracks SIGINT/SIGTERM and an optional wall-clock deadline."""

    def __init__(self, deadline_seconds: float | None = None):
        self.requested = False
        self.reason = ""
        self.started = time.monotonic()
        self.deadline = (self.started + deadline_seconds) if deadline_seconds else None
        self._previous: list[tuple[int, Any]] = []

    def install(self) -> "Cancellation":
        import signal
        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                self._previous.append((sig, signal.getsignal(sig)))
                signal.signal(sig, self._handle)
            except (ValueError, OSError):  # not main thread
                pass
        return self

    def _handle(self, signum, _frame):  # noqa: ANN001
        self.requested = True
        self.reason = f"signal:{signum}"

    def restore(self) -> None:
        import signal
        for sig, previous in self._previous:
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass
        self._previous = []

    # -- checks ----------------------------------------------------------
    def check(self) -> None:
        if self.requested:
            raise PecError(f"job cancelled ({self.reason})", EXIT_CANCELLED, "cancelled")
        if self.deadline and time.monotonic() > self.deadline:
            self.requested = True
            self.reason = "deadline"
            raise PecError("job exceeded its wall-clock deadline", EXIT_CANCELLED, "timeout")

    def remaining(self) -> float | None:
        if not self.deadline:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def elapsed(self) -> float:
        return time.monotonic() - self.started


def with_retries(func: Callable[[], Any], attempts: int, backoff: float,
                 on_retry: Callable[[int, Exception], None] | None = None,
                 retry_on: tuple[type[BaseException], ...] = (OSError, PecError)) -> Any:
    """Retry ``func`` with bounded exponential backoff (no jitter needed locally)."""
    last: BaseException | None = None
    for attempt in range(attempts + 1):
        try:
            return func()
        except retry_on as exc:  # noqa: PERF203 - retry loop is intentional
            if isinstance(exc, PecError) and exc.code in {
                EXIT_POLICY, EXIT_APPROVAL, EXIT_USAGE, EXIT_CANCELLED, EXIT_DEPENDENCY
            }:
                raise
            last = exc
            if attempt >= attempts:
                break
            if on_retry:
                on_retry(attempt + 1, exc)
            time.sleep(backoff * (2 ** attempt))
    assert last is not None
    raise last


# --------------------------------------------------------------------------
# Approval gate
# --------------------------------------------------------------------------
class ApprovalGate:
    """Local guardrail for destructive or sensitive actions.

    The gate is *not* authentication. In an agent context the caller must obtain
    an explicit human decision before passing ``--approve``.
    """

    TOKENS = {
        "overwrite": "OVERWRITE",
        "delete": "DELETE",
        "dispose": "DISPOSE",
        "decrypt": "DECRYPT",
        "publish-encrypted": "PUBLISH-ENCRYPTED",
    }

    def __init__(self, config: Config, granted: list[str] | None = None,
                 approval_file: str | None = None, dry_run: bool = False):
        self.required = set(config.get("policy.requires_approval", []) or [])
        self.granted = {a.strip().lower() for a in (granted or []) if a}
        self.file_records: dict[str, dict] = {}
        self.dry_run = dry_run
        if approval_file:
            payload = read_json(approval_file, None)
            if payload is None:
                raise PecError(f"approval file not found: {approval_file}", EXIT_USAGE)
            records = payload.get("approvals", payload) if isinstance(payload, dict) else payload
            if isinstance(records, dict):
                records = [records]
            for rec in records or []:
                if isinstance(rec, dict) and rec.get("action"):
                    self.file_records[str(rec["action"]).lower()] = rec

    def token_for(self, action: str) -> str:
        return self.TOKENS.get(action, action.upper())

    def require(self, action: str, detail: str = "") -> dict:
        """Raise unless the action is approved. Returns the evidence record."""
        if action not in self.required and action not in self.TOKENS:
            return {"action": action, "required": False}
        if action in self.granted:
            return {"action": action, "required": True, "granted": True,
                    "source": "token", "detail": detail}
        record = self.file_records.get(action)
        if record:
            expires = record.get("expires")
            if expires:
                try:
                    when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    if when < datetime.now(timezone.utc):
                        raise PecError(f"approval for {action!r} expired at {expires}",
                                       EXIT_APPROVAL, "needs-approval")
                except ValueError:
                    pass
            return {"action": action, "required": True, "granted": True,
                    "source": "approval-file", "approved_by": record.get("approved_by"),
                    "detail": detail}
        raise PecError(
            f"action {action!r} requires explicit approval; re-run with "
            f"--approve {self.token_for(action)} after a human reviewer agrees",
            EXIT_APPROVAL, "needs-approval",
            detail={"action": action, "token": self.token_for(action), "context": detail},
        )

    def check(self, action: str) -> bool:
        try:
            self.require(action)
            return True
        except PecError:
            return False


# --------------------------------------------------------------------------
# Job envelope
# --------------------------------------------------------------------------
@dataclass
class Job:
    command: str
    job_id: str = field(default_factory=new_job_id)
    fingerprint: str = ""
    params: dict = field(default_factory=dict)
    status: str = "running"
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    duration_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    state_dir: str = ""
    log_path: str = ""

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def fail(self, message: str, kind: str = "error", detail: Any = None) -> None:
        self.errors.append({"kind": kind, "message": message, "detail": detail})

    def envelope(self, exit_code: int) -> dict:
        return {
            "schema": RESULT_SCHEMA,
            "skill": SKILL_NAME,
            "version": VERSION,
            "job_id": self.job_id,
            "command": self.command,
            "status": self.status,
            "exit_code": exit_code,
            "fingerprint": self.fingerprint,
            "started_at": self.started_at,
            "finished_at": self.finished_at or utcnow(),
            "duration_seconds": round(self.duration_seconds, 3),
            "params": self.params,
            "counts": self.counts,
            "warnings": self.warnings,
            "errors": self.errors,
            "artifacts": self.artifacts,
            "result": self.result,
            "state_dir": self.state_dir,
            "log_path": self.log_path,
        }

    def persist(self, state_dir: str, payload: dict) -> str:
        records = os.path.join(state_dir, "jobs")
        os.makedirs(records, exist_ok=True)
        path = os.path.join(records, f"{self.job_id}.json")
        write_json_atomic(path, payload)
        index_path = os.path.join(records, "index.jsonl")
        append_jsonl(index_path, {
            "ts": payload["finished_at"], "job_id": self.job_id, "command": self.command,
            "status": self.status, "exit_code": payload["exit_code"],
            "fingerprint": self.fingerprint, "record": path,
        })
        return path


def compute_fingerprint(sources: Iterable[str], params: dict) -> str:
    """Immutable-ish fingerprint of source identity plus effective parameters."""
    import hashlib
    h = hashlib.sha256()
    for src in sorted(str(s) for s in sources):
        h.update(b"S\x00")
        h.update(src.encode("utf-8", "replace"))
        try:
            st = os.stat(src)
            h.update(f"\x00{st.st_size}\x00{int(st.st_mtime)}".encode())
        except OSError:
            h.update(b"\x00missing")
    h.update(b"P\x00")
    h.update(json.dumps(params, sort_keys=True, default=str).encode("utf-8", "replace"))
    return "sha256:" + h.hexdigest()


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
def emit(payload: dict, as_json: bool, human: str = "") -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(human or "")
        if human and not human.endswith("\n"):
            sys.stdout.write("\n")


def table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip(),
             "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def ensure_parent(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return path


def rmtree_quiet(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def temp_workdir(state_dir: str, job_id: str, label: str = "work") -> str:
    path = os.path.join(state_dir, "tmp", f"{job_id}-{label}")
    os.makedirs(path, exist_ok=True)
    return path


def diagnose_exception(exc: BaseException) -> dict:
    return {"kind": type(exc).__name__, "message": str(exc),
            "trace": traceback.format_exc(limit=8) if os.environ.get("PEC_DEBUG") else ""}


MAIN_HELP = f"""powerful-extractor-compressor {VERSION}
A production Skill Tool for safe extraction, compression, organization, verification,
recovery and packaging of archives.

USAGE
  python3 tools-pec.py <command> [options]
  python3 tools-pec.py <command> --help

COMMANDS
  caps        List implemented capabilities and map catalog skills to commands
  coverage    Map all 144 catalog skills to commands (full / partial / out-of-scope)
  detect      Identify the real container type from magic bytes and structure
  inspect     Preflight: entries, sizes, ratio, encryption, volumes, resource estimate
  list        List archive entries with filters (glob, regex, size, date, index)
  extract     Safely extract archives, optionally nested, with checkpoints and manifests
  create      Build ZIP/TAR/GZ/BZ2/XZ (7z, Zstd when installed) deterministically, optional split
  verify      Hash/verify, native archive test, completeness audit, round-trip check
  organize    Sort extracted content by content type with collision-safe renames
  scan        Secret/PII scan, file-type mismatch detection, metadata sanitization
  package     Run a packaging recipe (source release, handoff, evidence, dataset, ...)
  batch       Extract many archives with bounded parallelism, retries and summaries
  jobs        List job records, resume from a checkpoint, or clean state (approval)
  config      Show, validate, or write a configuration file
  selftest    Run the built-in end-to-end test battery

GLOBAL OPTIONS
  --config PATH        JSON configuration file (merged over bundled defaults)
  --state DIR          State directory for jobs, logs, checkpoints (default .pec-state)
  --json               Emit the structured result envelope
  --quiet              Suppress non-error stderr messages
  --approve ACTION     Grant a gated action (DELETE, OVERWRITE, DISPOSE, DECRYPT...)
  --approval-file P    JSON file with reviewer approval records
  --dry-run            Plan only; never write or delete
  --deadline SECONDS   Wall-clock budget for the command

EXIT CODES
  0 success   2 usage/config   3 policy refusal   4 integrity failure
  5 partial   6 missing backend 7 approval required 8 I/O or capacity
  9 cancelled 1 unexpected error
"""
