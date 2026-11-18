#!/usr/bin/env python3
"""Safety, configuration, validation, limits, retries, and atomic I/O.

This module is loaded dynamically by tools-document-productivity.py because the
packaged filenames contain hyphens. It intentionally uses only the standard
library.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "1.0"
TOOL_FOLDER_NAME = "document-productivity"


class ToolError(Exception):
    """Structured, user-facing failure."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None,
                 exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code


def json_ready(value: Any) -> Any:
    """Convert common runtime objects into JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: json_ready(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(v) for v in value]
    if hasattr(value, "to_dict"):
        return json_ready(value.to_dict())
    return str(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str, fallback: str = "capability") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return text[:80] or fallback


def root_relative(ref: str | Path, tool_dir: Path, future_root: Path | None = None) -> Path:
    """Resolve an internal skill-root-relative reference.

    A leading slash does NOT mean filesystem root. It means the future skill
    root. Both `/asset-default-config.json` and
    `/document-productivity/asset-default-config.json` resolve to the same
    packaged configuration, so the folder can be copied beside a parent
    SKILL.md without changing any internal reference.
    """
    raw = str(ref).replace("\\", "/")
    if not raw.strip():
        raise ToolError("invalid_internal_path", "An internal path is empty.")
    if "://" in raw or raw.startswith("//") or raw.startswith("~"):
        raise ToolError("invalid_internal_path",
                        "URLs, UNC paths, and home-directory shortcuts are not internal references.",
                        {"path": raw})
    if re.match(r"^[A-Za-z]:/", raw):
        raise ToolError("invalid_internal_path", "Drive-letter paths are not portable.",
                        {"path": raw})
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ToolError("invalid_internal_path", "Parent traversal is not allowed.",
                        {"path": raw})
    if not parts:
        raise ToolError("invalid_internal_path", "An internal path has no components.",
                        {"path": raw})
    if parts[0] == TOOL_FOLDER_NAME or parts[0] == tool_dir.name:
        parts = parts[1:]
    if not parts:
        return tool_dir.resolve()
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", p) for p in parts):
        raise ToolError("invalid_internal_path", "An internal path contains unsupported characters.",
                        {"path": raw})
    first = parts[0]
    if first.startswith(("asset-", "tools-", "knowledge-")):
        return (tool_dir / Path(*parts)).resolve()
    if future_root is not None:
        return (future_root / Path(*parts)).resolve()
    return (tool_dir / Path(*parts)).resolve()


def resolve_user_path(path: str | Path, tool_dir: Path | None = None,
                      cwd: Path | None = None) -> Path:
    """Resolve a user-supplied input/output path.

    Absolute user paths are allowed at runtime, but the packaged configuration
    and documentation never contain machine-specific absolute paths. Internal
    root-relative references preserve their special meaning.
    """
    raw = str(path)
    if not raw.strip():
        raise ToolError("invalid_path", "A user path is empty.")
    if "://" in raw or raw.startswith("~") or re.match(r"^[A-Za-z]:", raw):
        raise ToolError("invalid_path", "URLs, home shortcuts, and drive-letter paths are not accepted.",
                        {"path": raw})
    normalized = raw.replace("\\", "/")
    if ".." in normalized.split("/"):
        raise ToolError("invalid_path", "Parent traversal is not allowed in user paths.",
                        {"path": raw})
    if raw.startswith("/") and tool_dir is not None:
        first = [p for p in normalized.split("/") if p and p != "."][0]
        if first in (TOOL_FOLDER_NAME, tool_dir.name) or first.startswith(("asset-", "tools-", "knowledge-")):
            return root_relative(raw, tool_dir)
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    base = (cwd or Path.cwd()).resolve()
    return (base / candidate).resolve()


def display_path(path: str | Path, cwd: Path | None = None) -> str:
    """Return a stable, mostly-relative display path for structured output."""
    p = Path(path)
    try:
        base = (cwd or Path.cwd()).resolve()
        rel = p.resolve().relative_to(base)
        return "./" + rel.as_posix() if rel.as_posix() != "." else "."
    except Exception:
        return p.as_posix()


class SimpleSchemaValidator:
    """A small deterministic JSON-Schema subset validator.

    Supported keywords: type, enum, required, properties, items, minimum,
    maximum, minLength, pattern, additionalProperties, and local $ref.
    """

    def __init__(self, document: dict[str, Any]):
        self.document = document
        self.defs = document.get("$defs", {})
        self.schemas = document.get("schemas", {})

    def _resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        ref = schema.get("$ref")
        if not ref:
            return schema
        if not ref.startswith("#/$defs/"):
            raise ToolError("schema_error", "Only local #/$defs references are supported.",
                            {"ref": ref})
        name = ref[len("#/$defs/"):]
        if name not in self.defs:
            raise ToolError("schema_error", "Unknown schema definition.", {"ref": ref})
        merged = dict(self.defs[name])
        merged.update({k: v for k, v in schema.items() if k != "$ref"})
        return merged

    @staticmethod
    def _type_ok(value: Any, expected: str) -> bool:
        mapping = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None,
        }
        return mapping[expected](value) if expected in mapping else True

    def validate(self, instance: Any, schema: dict[str, Any], where: str = "$") -> None:
        schema = self._resolve(schema)
        expected = schema.get("type")
        if expected:
            types = expected if isinstance(expected, list) else [expected]
            if not any(self._type_ok(instance, t) for t in types):
                raise ToolError("validation_error", f"{where}: expected type {expected}.",
                                {"path": where, "expected": expected,
                                 "actual": type(instance).__name__})
        if "enum" in schema and instance not in schema["enum"]:
            raise ToolError("validation_error", f"{where}: value is not in the allowed set.",
                            {"path": where, "allowed": schema["enum"], "actual": instance})
        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                raise ToolError("validation_error", f"{where}: string is too short.", {"path": where})
            if "pattern" in schema and not re.search(schema["pattern"], instance):
                raise ToolError("validation_error", f"{where}: string does not match the pattern.",
                                {"path": where, "pattern": schema["pattern"]})
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                raise ToolError("validation_error", f"{where}: value is below minimum.", {"path": where})
            if "maximum" in schema and instance > schema["maximum"]:
                raise ToolError("validation_error", f"{where}: value is above maximum.", {"path": where})
        if isinstance(instance, dict):
            for field in schema.get("required", []):
                if field not in instance:
                    raise ToolError("validation_error", f"{where}: missing required field '{field}'.",
                                    {"path": where, "field": field})
            properties = schema.get("properties", {})
            for key, value in instance.items():
                if key in properties:
                    self.validate(value, properties[key], f"{where}.{key}")
                elif schema.get("additionalProperties") is False:
                    raise ToolError("validation_error", f"{where}: unexpected field '{key}'.",
                                    {"path": where, "field": key})
        if isinstance(instance, list) and "items" in schema:
            for index, item in enumerate(instance):
                self.validate(item, schema["items"], f"{where}[{index}]")

    def validate_named(self, instance: Any, name: str) -> None:
        if name not in self.schemas:
            raise ToolError("validation_error", f"Unknown schema '{name}'.",
                            {"available": sorted(self.schemas)})
        self.validate(instance, self.schemas[name], "$")


class Config:
    def __init__(self, data: dict[str, Any], path: Path, tool_dir: Path):
        self.data = data
        self.path = path
        self.tool_dir = tool_dir
        self.limits = dict(data.get("limits", {}))

    @classmethod
    def load(cls, path: Path, tool_dir: Path, overrides: Iterable[str] = ()) -> "Config":
        if not path.is_file():
            raise ToolError("config_not_found", "Configuration file does not exist.",
                            {"path": display_path(path)})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError("config_parse_error", "Configuration file could not be parsed.",
                            {"path": display_path(path), "error": str(exc)})
        obj = cls(data, path, tool_dir)
        for override in overrides:
            if "=" not in override:
                raise ToolError("config_override_error",
                                "Overrides must use dotted.key=value syntax.",
                                {"override": override})
            key, raw = override.split("=", 1)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            obj.set_override(key, value)
        obj.limits = dict(obj.data.get("limits", {}))
        return obj

    def set_override(self, dotted: str, value: Any) -> None:
        parts = [p for p in dotted.split(".") if p]
        if not parts or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
            raise ToolError("config_override_error", "Invalid dotted override key.",
                            {"key": dotted})
        node = self.data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def internal_path(self, key: str) -> Path:
        value = self.get(key)
        if not isinstance(value, str):
            raise ToolError("config_error", f"Config key '{key}' must be a path string.",
                            {"key": key})
        return root_relative(value, self.tool_dir)

    def redacted(self) -> dict[str, Any]:
        data = copy.deepcopy(self.data)
        approval = data.setdefault("approval", {})
        approval.pop("token", None)
        approval["token_value"] = "<read-from-environment-only>"
        return data


class Policy:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path
        self.required = set(data.get("required_approval_classes", []))
        self.patterns = {k: re.compile(v, re.I) for k, v in data.get("class_patterns", {}).items()}
        self.blocked = set(data.get("blocked_actions", []))

    @classmethod
    def load(cls, path: Path) -> "Policy":
        if not path.is_file():
            raise ToolError("policy_not_found", "Safety policy file does not exist.",
                            {"path": display_path(path)})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError("policy_parse_error", "Safety policy file could not be parsed.",
                            {"path": display_path(path), "error": str(exc)})
        return cls(data, path)

    def classify(self, *texts: str) -> list[str]:
        haystack = " ".join(t or "" for t in texts).lower()
        return sorted(k for k, pattern in self.patterns.items() if pattern.search(haystack))

    def required_classes(self, capability: dict[str, Any], category: dict[str, Any],
                         overwrite: bool, action: str | None = None) -> list[str]:
        classes = set(capability.get("approval_classes") or [])
        classes.update(self.classify(capability.get("name", ""), capability.get("description", ""),
                                     category.get("name", ""), action or ""))
        if overwrite:
            classes.add("destructive")
        return sorted(classes & self.required)

    def check_blocked(self, action: str) -> None:
        if action in self.blocked:
            raise ToolError("blocked_action", f"Action '{action}' is blocked by policy.",
                            {"action": action}, exit_code=3)

    def check_approval(self, classes: list[str], token: str | None, reason: str | None,
                       token_env: str, dry_run: bool) -> dict[str, Any]:
        required = sorted(set(classes) & self.required)
        if not required:
            return {"required": False, "classes": [], "status": "not-required"}
        if dry_run:
            return {"required": True, "classes": required, "status": "deferred-dry-run",
                    "note": "No files are written during a dry run; approval is required for real writes."}
        expected = os.environ.get(token_env)
        if not expected:
            raise ToolError("approval_token_not_configured",
                            "Sensitive action approval is configured but the token environment variable is unset.",
                            {"token_env": token_env, "classes": required}, exit_code=3)
        if not token or not hmac.compare_digest(str(token), str(expected)):
            raise ToolError("approval_denied",
                            "A valid --approval-token is required for this sensitive action.",
                            {"classes": required, "token_env": token_env}, exit_code=3)
        if not reason or not reason.strip():
            raise ToolError("approval_reason_required",
                            "An --approval-reason is required for this sensitive action.",
                            {"classes": required}, exit_code=3)
        return {"required": True, "classes": required, "status": "approved",
                "reason": reason.strip(), "token_env": token_env}


class RunGuard:
    """Bounded execution with timeout, cancellation, and cooperative checks."""

    def __init__(self, timeout_seconds: float = 60.0):
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.deadline = 0.0
        self.cancelled = False
        self.timed_out = False
        self._old_alarm = None
        self._old_int = None
        self._timer_set = False
        self._in_main_thread = True

    def _on_alarm(self, signum, frame):
        self.timed_out = True
        raise ToolError("timeout", "Operation exceeded the configured timeout.",
                        {"timeout_seconds": self.timeout_seconds}, exit_code=5)

    def _on_sigint(self, signum, frame):
        self.cancelled = True
        raise ToolError("cancelled", "Operation was cancelled.", {}, exit_code=4)

    def __enter__(self):
        self.deadline = time.monotonic() + self.timeout_seconds
        try:
            if hasattr(signal, "SIGALRM"):
                self._old_alarm = signal.getsignal(signal.SIGALRM)
                signal.signal(signal.SIGALRM, self._on_alarm)
                signal.setitimer(signal.ITIMER_REAL, self.timeout_seconds)
                self._timer_set = True
            self._old_int = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._on_sigint)
        except (ValueError, OSError):
            self._in_main_thread = False
        return self

    def check(self, point: str = "operation") -> None:
        if self.cancelled:
            raise ToolError("cancelled", "Operation was cancelled.", {"point": point}, exit_code=4)
        if self.timed_out or (self.deadline and time.monotonic() > self.deadline):
            raise ToolError("timeout", "Operation exceeded the configured timeout.",
                            {"point": point, "timeout_seconds": self.timeout_seconds}, exit_code=5)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._timer_set and hasattr(signal, "SIGALRM"):
                signal.setitimer(signal.ITIMER_REAL, 0)
                if self._old_alarm is not None:
                    signal.signal(signal.SIGALRM, self._old_alarm)
            if self._old_int is not None:
                signal.signal(signal.SIGINT, self._old_int)
        except (ValueError, OSError):
            pass
        return False


def retry_call(func: Callable[[], Any], attempts: int = 2, delay_ms: int = 100,
               guard: RunGuard | None = None, label: str = "I/O") -> Any:
    attempts = max(1, min(10, int(attempts)))
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if guard:
            guard.check(label)
        try:
            return func()
        except ToolError:
            raise
        except (OSError, TimeoutError) as exc:
            last = exc
            if attempt < attempts:
                time.sleep(max(0, min(2.0, delay_ms / 1000.0)))
    raise ToolError("io_retry_failed", f"{label} failed after {attempts} attempts.",
                    {"error": str(last) if last else "unknown"}, exit_code=1)


def load_json(path: Path, guard: RunGuard | None = None, attempts: int = 2,
              delay_ms: int = 100) -> Any:
    def read():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return retry_call(read, attempts, delay_ms, guard, f"read {path.name}")
    except json.JSONDecodeError as exc:
        raise ToolError("json_parse_error", "JSON could not be parsed.",
                        {"path": display_path(path), "error": str(exc)})


def reject_symlink(path: Path, label: str = "path") -> None:
    if path.is_symlink():
        raise ToolError("unsafe_symlink", f"Symlinks are not accepted for {label}.",
                        {"path": display_path(path)})


def atomic_write(path: Path, content: bytes, overwrite: bool = False, backup: bool = True,
                 backup_suffix: str = ".bak", max_bytes: int = 104857600,
                 guard: RunGuard | None = None, attempts: int = 2,
                 delay_ms: int = 100) -> dict[str, Any]:
    if len(content) > max_bytes:
        raise ToolError("output_too_large", "Rendered output exceeds the configured byte limit.",
                        {"bytes": len(content), "max_bytes": max_bytes})
    path = Path(path)
    reject_symlink(path, "output")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(parent, "output parent")
    existed = path.exists()
    backup_path = Path(str(path) + backup_suffix)
    partial = Path(str(path) + ".partial")
    made_backup = False
    if existed:
        if not overwrite:
            raise ToolError("output_exists", "Output exists. Pass --overwrite and approval for replacement.",
                            {"path": display_path(path)})
        if backup:
            if backup_path.exists():
                raise ToolError("backup_exists",
                                "A backup already exists. Run recover or choose another output path first.",
                                {"backup": display_path(backup_path)})
            shutil.copy2(path, backup_path)
            made_backup = True
    try:
        def write_partial():
            with tempfile.NamedTemporaryFile(dir=parent, prefix=".dp-", suffix=".tmp", delete=False) as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                temp_name = tmp.name
            os.replace(temp_name, partial)
            if guard:
                guard.check("atomic write")
            os.replace(partial, path)
        retry_call(write_partial, attempts, delay_ms, guard, f"write {path.name}")
        return {"path": display_path(path), "existed": existed,
                "backup": display_path(backup_path) if made_backup else None,
                "bytes": len(content), "sha256": sha256_bytes(content)}
    except Exception as exc:
        try:
            if partial.exists():
                partial.unlink()
        except OSError:
            pass
        if made_backup and backup_path.exists():
            try:
                shutil.copy2(backup_path, path)
            except OSError as restore_exc:
                raise ToolError("recovery_failed",
                                "Write failed and the backup could not be restored.",
                                {"backup": display_path(backup_path),
                                 "write_error": str(exc), "restore_error": str(restore_exc)},
                                exit_code=1) from exc
        if isinstance(exc, ToolError):
            raise
        raise ToolError("write_failed", "Atomic write failed.",
                        {"path": display_path(path), "error": str(exc)}, exit_code=1) from exc
    finally:
        try:
            if partial.exists():
                partial.unlink()
        except OSError:
            pass


def recover_backup(backup: Path, output: Path, overwrite: bool = False,
                   backup_suffix: str = ".bak", guard: RunGuard | None = None) -> dict[str, Any]:
    backup = Path(backup)
    output = Path(output)
    if not backup.is_file():
        raise ToolError("backup_not_found", "Backup file does not exist.",
                        {"backup": display_path(backup)})
    reject_symlink(backup, "backup")
    reject_symlink(output, "output")
    if output.exists() and not overwrite:
        raise ToolError("output_exists", "Recovery target exists. Pass --overwrite and approval.",
                        {"path": display_path(output)})
    pre_recovery = Path(str(output) + ".pre-recovery.bak")
    if output.exists():
        if pre_recovery.exists():
            raise ToolError("backup_exists", "A pre-recovery backup already exists.",
                            {"backup": display_path(pre_recovery)})
        shutil.copy2(output, pre_recovery)
    data = backup.read_bytes()
    if guard:
        guard.check("recovery")
    info = atomic_write(output, data, overwrite=True, backup=False,
                        max_bytes=max(104857600, len(data)), guard=guard)
    info.update({"recovered_from": display_path(backup),
                 "pre_recovery_backup": display_path(pre_recovery) if output.exists() and pre_recovery.exists() else None})
    return info


def verify_package(tool_dir: Path, config: Config, policy: Policy,
                   expected_commands: Iterable[str] = ()) -> dict[str, Any]:
    """Check Copilot-skill package compatibility from the tool folder itself."""
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, fatal: bool = True) -> None:
        checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})
        if not ok and fatal:
            errors.append({"code": "package_check_failed", "message": detail,
                           "details": {"check": name}})

    entries = sorted(p for p in tool_dir.iterdir() if not p.name.startswith("."))
    files = [p for p in entries if p.is_file()]
    dirs = [p for p in entries if p.is_dir()]
    names = [p.name for p in files]
    check("one-folder-no-subfolders", not dirs,
          f"{len(dirs)} subfolders; every packaged file is directly in the tool folder.")
    check("no-skill-md", "SKILL.md" not in names,
          "No SKILL.md inside the Skill Tool folder.")
    check("no-nested-archives",
          not any(p.suffix.lower() in {".zip", ".tar", ".gz", ".7z", ".rar"} for p in files),
          "No nested archives in the package.")
    check("file-count-within-limit", len(files) <= int(config.limits.get("max_package_files", 500)),
          f"{len(files)} packaged files; limit {config.limits.get('max_package_files', 500)}.")
    check("classified-filenames",
          all(n.startswith(("tools-", "knowledge-", "asset-")) for n in names),
          "Every file uses tools-, knowledge-, or asset- prefix.")
    knowledge = [n for n in names if n.startswith("knowledge-")]
    check("exactly-one-knowledge-file", knowledge == ["knowledge-readme.md"],
          f"Knowledge files: {knowledge}")
    primary = "tools-document-productivity.py"
    check("primary-entry-point", primary in names,
          f"Primary entry point {primary} is present.")
    tools = [n for n in names if n.startswith("tools-")]
    assets = [n for n in names if n.startswith("asset-")]
    check("at-least-one-tool", len(tools) >= 1, f"{len(tools)} tool files.")
    check("asset-count-reflects-use", len(assets) >= 1, f"{len(assets)} asset files.")
    check("path-depth-within-limit",
          all(len(p.relative_to(tool_dir).parts) <= 1 for p in files),
          "All files are one level below the tool folder; two levels below the future skill root.")
    for key in ("catalog_path", "policy_path", "provider_presets_path", "prompt_set_path",
                "schemas_path", "document_template_path", "sample_data_path", "source_catalog_path"):
        try:
            p = config.internal_path(key)
            check(f"config-path-{key}", p.is_file(),
                  f"{key} resolves to {display_path(p, tool_dir)}")
        except ToolError as exc:
            check(f"config-path-{key}", False, exc.message)
    docs = tool_dir / "knowledge-readme.md"
    doc_text = docs.read_text(encoding="utf-8") if docs.is_file() else ""
    check("docs-present", bool(doc_text), "knowledge-readme.md is non-empty.")
    for name in names:
        check(f"docs-mentions-{name}", name in doc_text,
              f"knowledge-readme.md mentions {name}.")
    for command in expected_commands:
        check(f"docs-command-{command}", command in doc_text,
              f"knowledge-readme.md documents command '{command}'.")
    bad_patterns = {
        "parent-traversal": r"\.\./",
        "drive-letter": r"[A-Za-z]:\\",
        "home-shortcut": r"(?<![\w])~/",
        "url": r"https?://|file://",
        "machine-absolute": r"(?<![\w])/(?:home|Users|mnt|var|etc|tmp)/",
    }
    for label, pattern in bad_patterns.items():
        found = re.search(pattern, doc_text + "\n" + json.dumps(config.data))
        check(f"no-{label}-in-docs", not found,
              f"No {label} found in packaged docs/config." if not found else
              f"Found {label} pattern: {found.group(0) if found else ''}")
    refs = set(re.findall(r"/(?:document-productivity/)?(?:asset-|tools-|knowledge-)[A-Za-z0-9._/-]+", doc_text))
    refs.update(re.findall(r"/(?:asset-|tools-|knowledge-)[A-Za-z0-9._/-]+", json.dumps(config.data)))
    for ref in sorted(refs):
        clean = ref.rstrip(".,;:)`")
        try:
            target = root_relative(clean, tool_dir)
            check(f"reference-{clean}", target.is_file(),
                  f"{clean} resolves to {display_path(target, tool_dir)}")
        except ToolError as exc:
            check(f"reference-{clean}", False, exc.message)
    return {
        "ok": not errors,
        "command": "verify",
        "tool_folder": tool_dir.name,
        "file_count": len(files),
        "tool_files": tools,
        "knowledge_files": knowledge,
        "asset_files": assets,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "limits": {"max_package_files": config.limits.get("max_package_files", 500),
                   "max_path_depth": config.limits.get("max_path_depth", 3)},
    }


def structured_error(exc: ToolError, command: str = "unknown") -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        "errors": [{"code": exc.code, "message": exc.message, "details": exc.details}],
        "artifacts": [], "checks": [], "warnings": [], "metrics": {},
        "limits": {},
    }
