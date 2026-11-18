#!/usr/bin/env python3
"""Shared runtime engine for the unified offline research Skill Tool.

Loaded by tools-offline-research.py. Provides configuration, validation,
structured errors, bounded I/O, source parsing, local indexes, approvals,
checkpoints, cancellation, timeouts, cleanup, and audit primitives.
Standard library only; no network, shell, or arbitrary code execution.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable


TOOL_NAME = "offline-research-writing-translation"
RESULT_SCHEMA_VERSION = "1.0"


class ToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: Any | None = None,
        recovery: str | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.recovery = recovery or "Check the command, input, and configuration, then retry."
        self.exit_code = exit_code

    def to_dict(self, command: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "ok": False,
            "command": command,
            "run_id": run_id,
            "timestamp_utc": utc_now(),
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "recovery": self.recovery,
                "exit_code": self.exit_code,
            },
        }


class InputError(ToolError):
    def __init__(self, message: str, details: Any | None = None, recovery: str | None = None) -> None:
        super().__init__("INVALID_INPUT", message, details, recovery, 2)


class ConfigError(ToolError):
    def __init__(self, message: str, details: Any | None = None, recovery: str | None = None) -> None:
        super().__init__("INVALID_CONFIG", message, details, recovery, 3)


class ApprovalError(ToolError):
    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(
            "APPROVAL_REQUIRED",
            message,
            details,
            "Request human review, obtain an approval token, and retry with --approval-token.",
            4,
        )


class CancelledError(ToolError):
    def __init__(self, message: str = "Operation cancelled by cancel file.", details: Any | None = None) -> None:
        super().__init__(
            "CANCELLED",
            message,
            details,
            "Remove the cancel file after review, then use recover --resume or rerun the request.",
            5,
        )


class TimeoutError_(ToolError):
    def __init__(self, message: str = "Operation exceeded its configured timeout.", details: Any | None = None) -> None:
        super().__init__(
            "TIMEOUT",
            message,
            details,
            "Increase --timeout, reduce input size, or split the request into a bounded batch.",
            6,
        )


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _safe_relative_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string.", {"value": value})
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value) or "://" in value:
        raise ConfigError(f"{label} must be relative to the Skill Tool folder.", {"value": value})
    parts = value.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ConfigError(f"{label} must not contain parent traversal or empty segments.", {"value": value})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parts[-1]):
        raise ConfigError(f"{label} has an unsupported filename.", {"value": value})
    return value


def validate_config(config: dict[str, Any]) -> None:
    if config.get("runtime", {}).get("network") != "disabled":
        raise ConfigError("Network access must remain disabled.", {"network": config.get("runtime", {}).get("network")})
    if config.get("runtime", {}).get("standard_library_only") is not True:
        raise ConfigError("This build is standard-library-only.", {"standard_library_only": config.get("runtime", {}).get("standard_library_only")})
    limits = config.get("runtime", {})
    for key in ("max_input_bytes", "max_documents", "max_document_chars", "max_query_chars", "max_output_bytes", "timeout_seconds"):
        if not isinstance(limits.get(key), (int, float)) or limits[key] <= 0:
            raise ConfigError(f"runtime.{key} must be a positive number.", {"value": limits.get(key)})
    if not isinstance(limits.get("retries"), int) or limits["retries"] < 0:
        raise ConfigError("runtime.retries must be a non-negative integer.")
    paths = config.get("asset_paths", {})
    if not isinstance(paths, dict):
        raise ConfigError("asset_paths must be an object.")
    for key, value in paths.items():
        _safe_relative_name(value, f"asset_paths.{key}")
        if not Path(value).name.startswith(("asset-", "tools-", "knowledge-")):
            raise ConfigError("Every packaged asset path must use a required prefix.", {"key": key, "value": value})
    storage = config.get("storage", {})
    for key, value in storage.items():
        if key == "workspace":
            _safe_relative_name(value, "storage.workspace")
        elif isinstance(value, str):
            _safe_relative_name(value, f"storage.{key}")
    providers = config.get("providers", {})
    for provider, settings in providers.items():
        if not isinstance(settings, dict):
            raise ConfigError(f"providers.{provider} must be an object.")
        local = settings.get("local_path")
        if local is not None:
            _safe_relative_name(str(local), f"providers.{provider}.local_path")
        if any("://" in str(v) for v in settings.values() if isinstance(v, str)):
            raise ConfigError("Provider settings must not contain external addresses.", {"provider": provider})
    # External addresses are never accepted in this offline build.
    for raw in _iter_strings(config):
        if "://" in raw and not raw.startswith("regex:"):
            raise ConfigError("External URLs or hidden addresses are not allowed.", {"value": raw[:120]})


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def load_config(tool_dir: Path, override_path: str | None = None) -> dict[str, Any]:
    default_path = tool_dir / "asset-default-config.json"
    if not default_path.is_file():
        raise ConfigError("Default configuration asset is missing.", {"path": default_path.name})
    config = json.loads(default_path.read_text(encoding="utf-8"))
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise ConfigError("Configuration override file does not exist.", {"path": str(path)})
        try:
            override = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("Configuration override is not readable JSON.", {"error": str(exc)})
        if not isinstance(override, dict):
            raise ConfigError("Configuration override must be a JSON object.")
        config = deep_merge(config, override)
    validate_config(config)
    return config


def load_catalog(path: Path, adapter: Any) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError("Capability catalog asset is missing.", {"path": path.name})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError("Capability catalog is not readable JSON.", {"error": str(exc)})
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise ConfigError("Capability catalog must contain a capabilities array.")
    umbrella = payload.get("umbrella_name", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", umbrella):
        raise ConfigError("Capability catalog umbrella name is invalid.", {"umbrella_name": umbrella})
    known = set(adapter.OPERATION_DESCRIPTIONS)
    seen_names: set[str] = set()
    seen_slugs: set[str] = set()
    for i, item in enumerate(payload["capabilities"], 1):
        for key in ("name", "slug", "category", "description", "operation", "responsibility"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ConfigError(f"Capability {i} is missing {key}.", {"index": i})
        if item["operation"] not in known:
            raise ConfigError(f"Capability {i} maps to an unknown operation.", {"operation": item["operation"]})
        lname = item["name"].casefold()
        if lname in seen_names:
            raise ConfigError("Capability names must be unique.", {"name": item["name"]})
        if item["slug"] in seen_slugs:
            raise ConfigError("Capability slugs must be unique.", {"slug": item["slug"]})
        seen_names.add(lname)
        seen_slugs.add(item["slug"])
    payload["_by_name"] = {c["name"].casefold(): c for c in payload["capabilities"]}
    payload["_by_slug"] = {c["slug"]: c for c in payload["capabilities"]}
    return payload


def load_request_schema(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError("Request schema asset is missing.", {"path": path.name})
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError("Request schema is not readable JSON.", {"error": str(exc)})
    if schema.get("type") != "object" or "operation" not in schema.get("required", []):
        raise ConfigError("Request schema is malformed.")
    return schema


def validate_request(request: Any, schema: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise InputError("Each request must be a JSON object.", {"received_type": type(request).__name__})
    allowed = set(schema.get("properties", {}))
    extra = sorted(set(request) - allowed)
    if extra and schema.get("additionalProperties") is False:
        raise InputError("Request contains unsupported fields.", {"fields": extra})
    required = schema.get("required", [])
    missing = [key for key in required if key not in request]
    if missing:
        raise InputError("Request is missing required fields.", {"fields": missing})
    op = request.get("operation")
    if op not in schema.get("properties", {}).get("operation", {}).get("enum", []):
        raise InputError("Unknown operation.", {"operation": op, "allowed_count": len(schema.get("properties", {}).get("operation", {}).get("enum", []))})
    known_ops = {c["operation"] for c in catalog["capabilities"]}
    if op not in known_ops:
        raise InputError("Operation is not present in the loaded capability catalog.", {"operation": op})
    for key in ("input", "output", "cancel_file", "approval_token"):
        if key in request and request[key] is not None and not isinstance(request[key], str):
            raise InputError(f"{key} must be a string or null.", {"field": key})
    if "params" in request and not isinstance(request["params"], dict):
        raise InputError("params must be a JSON object.")
    if "retries" in request and (not isinstance(request["retries"], int) or isinstance(request["retries"], bool) or request["retries"] < 0):
        raise InputError("retries must be a non-negative integer.")
    if "timeout_seconds" in request and (not isinstance(request["timeout_seconds"], (int, float)) or request["timeout_seconds"] <= 0):
        raise InputError("timeout_seconds must be a positive number.")
    return dict(request)


def parse_json_arg(value: str | None, label: str) -> Any:
    if value is None:
        return {}
    raw = value.strip()
    if not raw:
        return {}
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.is_file():
            raise InputError(f"{label} file does not exist.", {"path": str(path)})
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise InputError(f"{label} file is not readable UTF-8.", {"error": str(exc)})
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"{label} is not valid JSON.", {"error": str(exc), "line": exc.lineno, "column": exc.colno})


def structured_result(
    command: str,
    operation: str | None,
    run_id: str,
    result: dict[str, Any],
    warnings: list[str] | None = None,
    output_path: str | None = None,
    config_digest: str | None = None,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": True,
        "command": command,
        "operation": operation,
        "run_id": run_id,
        "timestamp_utc": utc_now(),
        "config_digest": config_digest,
        "elapsed_seconds": round(elapsed_seconds or 0.0, 6),
        "output_path": output_path,
        "warnings": warnings or [],
        "result": result,
    }


class RunContext:
    def __init__(
        self,
        tool_dir: Path,
        workspace: str,
        config: dict[str, Any],
        catalog: dict[str, Any],
        request_schema: dict[str, Any],
        adapter: Any,
        run_id: str,
        timeout_seconds: float,
        retries: int,
        cancel_file: str | None = None,
        overwrite: bool = False,
        keep_temp: bool = False,
        workspace_from_default: bool = False,
    ) -> None:
        self.tool_dir = Path(tool_dir).resolve()
        self.config = config
        self.catalog = catalog
        self.request_schema = request_schema
        self.adapter = adapter
        self.run_id = run_id
        self.workspace = Path(workspace)
        if not self.workspace.is_absolute():
            self.workspace = Path.cwd() / self.workspace
        self.workspace = self.workspace.resolve()
        try:
            inside_tool = self.workspace.is_relative_to(self.tool_dir)
        except AttributeError:  # Python 3.8 fallback; supported minimum is 3.9.
            inside_tool = str(self.workspace).startswith(str(self.tool_dir) + os.sep)
        if inside_tool:
            if not workspace_from_default:
                raise ConfigError("Workspace must be outside the immutable Skill Tool folder.", {"workspace": str(self.workspace.name)})
            self.workspace = (self.tool_dir.parent / self.workspace.name).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config_digest = sha256_text(canonical_json(config))
        self.started = time.monotonic()
        self.deadline = self.started + float(timeout_seconds)
        self.retries = max(0, int(retries))
        self.cancel_file = Path(cancel_file).resolve() if cancel_file else None
        self.overwrite = bool(overwrite)
        self.keep_temp = bool(keep_temp)
        self.temp_files: list[Path] = []
        self._old_alarm = None
        self._alarm_supported = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
        if self._alarm_supported:
            def _on_alarm(_signum: int, _frame: Any) -> None:
                raise TimeoutError_("Operation exceeded its configured timeout.", {"timeout_seconds": timeout_seconds})
            self._old_alarm = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))

    def asset_path(self, key: str) -> Path:
        name = self.config["asset_paths"][key]
        path = self.tool_dir / name
        if not path.is_file():
            raise ConfigError(f"Configured asset is missing: {name}", {"asset_key": key})
        return path

    def workspace_file(self, name: str) -> Path:
        safe = _safe_relative_name(name, "workspace file")
        if "/" in safe or "\\" in safe:
            raise ConfigError("Workspace file names must be flat.", {"name": name})
        return self.workspace / safe

    def check_limits(self) -> None:
        if time.monotonic() > self.deadline:
            raise TimeoutError_()
        self.check_cancel()

    def check_cancel(self) -> None:
        candidates = [self.workspace / "CANCEL"]
        if self.cancel_file:
            candidates.append(self.cancel_file)
        for candidate in candidates:
            if candidate.is_file():
                raise CancelledError(details={"cancel_file": candidate.name})

    def user_path(self, value: str, must_exist: bool = False, for_output: bool = False) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if any(part == ".." for part in Path(value).parts):
            raise InputError("Parent-directory traversal is not accepted in a user path.", {"path": value})
        if must_exist and not path.exists():
            raise InputError("Input path does not exist.", {"path": value})
        if must_exist and path.is_dir():
            raise InputError("Input path must be a file, not a directory.", {"path": value})
        if for_output:
            try:
                inside_tool = path.is_relative_to(self.tool_dir)
            except AttributeError:  # Python 3.8 fallback; supported minimum is 3.9.
                inside_tool = str(path).startswith(str(self.tool_dir) + os.sep)
            if inside_tool:
                raise InputError("Output must be outside the immutable Skill Tool folder.", {"path": value})
            if path.is_dir():
                raise InputError("Output path is a directory, not a file.", {"path": value})
            if path.exists() and not self.overwrite:
                raise InputError("Output already exists; pass --overwrite to replace it explicitly.", {"path": value})
        return path

    def default_output(self, operation: str) -> Path:
        prefix = self.config["storage"].get("output_prefix", "output-")
        return self.workspace_file(f"{prefix}{operation}-{self.run_id}.json")

    def cleanup(self) -> None:
        if self._alarm_supported and self._old_alarm is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._old_alarm)
        if self.keep_temp or not self.config["runtime"].get("cleanup_temp_on_exit", True):
            return
        for path in self.temp_files:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass


def atomic_write(ctx: RunContext, path: Path, data: str, binary: bool = False) -> None:
    max_bytes = int(ctx.config["runtime"]["max_output_bytes"])
    payload = data.encode("utf-8") if not binary else data  # type: ignore[assignment]
    if len(payload) > max_bytes:  # type: ignore[arg-type]
        raise ToolError("OUTPUT_TOO_LARGE", "Output exceeds the configured byte limit.", {"bytes": len(payload), "limit": max_bytes})
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".tmp-{path.name}-{secrets.token_hex(4)}"
    ctx.temp_files.append(temp)
    last_error: Exception | None = None
    for attempt in range(ctx.retries + 1):
        try:
            if binary:
                temp.write_bytes(payload)  # type: ignore[arg-type]
            else:
                text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
            os.replace(temp, path)
            return
        except OSError as exc:
            last_error = exc
            if attempt < ctx.retries:
                time.sleep(float(ctx.config["runtime"].get("retry_backoff_seconds", 0.05)))
    raise ToolError(
        "IO_ERROR",
        "Could not write output after bounded retries.",
        {"path": path.name, "attempts": ctx.retries + 1, "error": str(last_error)},
        "Check filesystem permissions and free space, then retry or choose another output path.",
    )


def write_json(ctx: RunContext, path: Path, payload: Any) -> None:
    atomic_write(ctx, path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def write_text(ctx: RunContext, path: Path, text: str) -> None:
    atomic_write(ctx, path, text)


def append_jsonl(ctx: RunContext, path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def read_text_file(ctx: RunContext, path: Path) -> str:
    max_bytes = int(ctx.config["runtime"]["max_input_bytes"])
    if not path.is_file():
        raise InputError("Input file does not exist.", {"path": path.name})
    size = path.stat().st_size
    if size > max_bytes:
        raise InputError("Input exceeds the configured byte limit.", {"bytes": size, "limit": max_bytes})
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("Input is not valid UTF-8 text.", {"path": path.name, "error": str(exc)}, "Convert the file to UTF-8 or supply a local UTF-8 sidecar.")
    except OSError as exc:
        raise InputError("Input is not readable.", {"path": path.name, "error": str(exc)})


def _default_record(params: dict[str, Any], text: str, title: str) -> dict[str, Any]:
    return {
        "id": params.get("source_id", "input-001"),
        "title": title,
        "text": text,
        "source_type": params.get("source_type", "primary"),
        "language": params.get("language", "en"),
        "license": params.get("license", "operator-supplied"),
        "jurisdiction": params.get("jurisdiction", "operator-configured"),
        "version": params.get("version", "1.0"),
        "date": params.get("date", "2026-01-01"),
        "authority": float(params.get("authority", 0.5)),
        "status": params.get("status", "active"),
        "permissions": params.get(
            "permissions",
            {"search": True, "quote": True, "redistribute": False, "store": True, "export": False},
        ),
        "page_anchor": params.get("page_anchor", "p.1"),
        "local_ref": params.get("local_ref", "operator-input"),
        "tags": params.get("tags", []),
        "restricted": bool(params.get("restricted", False)),
    }


def _parse_bibtex(text: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for block in re.findall(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, flags=re.S | re.I):
        kind, key, body = block
        fields: dict[str, str] = {}
        for matched in re.findall(r"(\w+)\s*=\s*(?:\{([^}]*)\}|\"([^\"]*)\")", body, flags=re.S):
            fields[matched[0].lower()] = (matched[1] or matched[2]).strip()
        year = re.search(r"\d{4}", fields.get("year", ""))
        date = f"{year.group(0)}-01-01" if year else params.get("date", "2026-01-01")
        title = fields.get("title", key)
        abstract = fields.get("abstract", "")
        rec = _default_record(params, f"{title}. {abstract}".strip(), title)
        rec.update({"id": key, "source_type": "secondary", "date": date, "version": fields.get("edition", "1.0")})
        records.append(rec)
    return records


def _parse_ris(text: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    blocks = re.split(r"(?im)^ER\s*-\s*$", text)
    for i, block in enumerate(blocks, 1):
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            match = re.match(r"^([A-Z][A-Z0-9])\s*-\s*(.*)$", line.strip())
            if match:
                fields.setdefault(match.group(1), []).append(match.group(2).strip())
        if not fields:
            continue
        title = " ".join(fields.get("TI", fields.get("T1", [f"RIS record {i}"])))
        abstract = " ".join(fields.get("AB", fields.get("N2", [])))
        year = re.search(r"\d{4}", " ".join(fields.get("PY", fields.get("Y1", []))))
        rec = _default_record(params, f"{title}. {abstract}".strip(), title)
        rec.update({"id": fields.get("ID", [f"ris-{i}"])[0], "source_type": "secondary", "date": f"{year.group(0)}-01-01" if year else params.get("date", "2026-01-01")})
        records.append(rec)
    return records


def _timestamp_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(value)


def _parse_timestamped(text: str, srt: bool, params: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    pattern = re.compile(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = pattern.search(lines[i])
        if not match:
            i += 1
            continue
        start = _timestamp_seconds(match.group(1))
        end = _timestamp_seconds(match.group(2))
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip() and not pattern.search(lines[i]):
            if not (srt and lines[i].strip().isdigit()):
                body.append(lines[i].strip())
            i += 1
        spoken = " ".join(body).strip()
        speaker = "UNKNOWN"
        speaker_match = re.match(r"^([A-Za-z0-9_-]{1,32})\s*:\s*(.+)$", spoken)
        if speaker_match:
            speaker = speaker_match.group(1)
            spoken = speaker_match.group(2)
        if spoken:
            segments.append({"start": start, "end": end, "speaker": speaker, "text": spoken})
    if not segments:
        return []
    rec = _default_record(params, " ".join(s["text"] for s in segments), params.get("title", "Transcript input"))
    rec.update({"source_type": "transcript", "segments": segments, "page_anchor": "00:00:00"})
    return [rec]


def validate_source_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object" or not schema.get("required"):
        raise ConfigError("Source schema must be an object with required fields.")


def validate_source_record(record: Any, schema: dict[str, Any], index: int, ctx: RunContext) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise InputError(f"Source record {index} must be a JSON object.", {"index": index})
    missing = [key for key in schema.get("required", []) if key not in record]
    if missing:
        raise InputError(f"Source record {index} is missing required fields.", {"index": index, "fields": missing})
    props = schema.get("properties", {})
    for key, rule in props.items():
        if key not in record:
            continue
        value = record[key]
        expected = rule.get("type")
        if expected == "string" and not isinstance(value, str):
            raise InputError(f"Source record {index}.{key} must be a string.", {"index": index, "field": key})
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise InputError(f"Source record {index}.{key} must be a number.", {"index": index, "field": key})
        if expected == "boolean" and not isinstance(value, bool):
            raise InputError(f"Source record {index}.{key} must be a boolean.", {"index": index, "field": key})
        if expected == "array" and not isinstance(value, list):
            raise InputError(f"Source record {index}.{key} must be an array.", {"index": index, "field": key})
        if expected == "object" and not isinstance(value, dict):
            raise InputError(f"Source record {index}.{key} must be an object.", {"index": index, "field": key})
        if "enum" in rule and value not in rule["enum"]:
            raise InputError(f"Source record {index}.{key} has an unsupported value.", {"index": index, "field": key, "value": value, "allowed": rule["enum"]})
        if "pattern" in rule and not re.search(rule["pattern"], str(value)):
            raise InputError(f"Source record {index}.{key} does not match its pattern.", {"index": index, "field": key, "value": value})
        if "minimum" in rule and isinstance(value, (int, float)) and value < rule["minimum"]:
            raise InputError(f"Source record {index}.{key} is below the minimum.", {"index": index, "field": key})
        if "maximum" in rule and isinstance(value, (int, float)) and value > rule["maximum"]:
            raise InputError(f"Source record {index}.{key} is above the maximum.", {"index": index, "field": key})
        if "min_length" in rule and isinstance(value, str) and len(value) < rule["min_length"]:
            raise InputError(f"Source record {index}.{key} is too short.", {"index": index, "field": key})
        if "max_length" in rule and isinstance(value, str) and len(value) > rule["max_length"]:
            raise InputError(f"Source record {index}.{key} is too long.", {"index": index, "field": key})
    perms = record.get("permissions", {})
    for key in schema["properties"]["permissions"].get("required", []):
        if key not in perms or not isinstance(perms[key], bool):
            raise InputError(f"Source record {index}.permissions.{key} must be a boolean.", {"index": index})
    max_chars = int(ctx.config["runtime"]["max_document_chars"])
    if len(record["text"]) > max_chars:
        raise InputError(f"Source record {index} exceeds max_document_chars.", {"index": index, "chars": len(record["text"]), "limit": max_chars})
    record = dict(record)
    record["sha256"] = sha256_text(record["text"])
    record["normalized_text"] = normalize_text(record["text"])
    if "segments" in record:
        for j, segment in enumerate(record["segments"], 1):
            for key in ("start", "end", "speaker", "text"):
                if key not in segment:
                    raise InputError(f"Source record {index} segment {j} is missing {key}.", {"index": index, "segment": j})
            if segment["end"] < segment["start"]:
                raise InputError(f"Source record {index} segment {j} ends before it starts.", {"index": index, "segment": j})
    return record


def read_input_records(ctx: RunContext, input_path: str | None, params: dict[str, Any] | None = None, required: bool = True) -> list[dict[str, Any]]:
    params = params or {}
    if input_path is None:
        if required:
            raise InputError("This operation requires --input.", {}, "Supply a local UTF-8 corpus file or a bundled sample corpus path.")
        return []
    path = ctx.user_path(input_path, must_exist=True)
    if path.suffix.lower() == ".pdf":
        raise InputError(
            "PDF parsing is not bundled.",
            {"path": path.name},
            "Extract the licensed PDF to a UTF-8 text or JSONL sidecar with page anchors, then pass that sidecar.",
        )
    text = read_text_file(ctx, path)
    schema = json.loads(ctx.asset_path("source_schema").read_text(encoding="utf-8"))
    validate_source_schema(schema)
    suffix = path.suffix.lower()
    raw: list[Any] = []
    if suffix == ".jsonl":
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise InputError("Corpus JSONL line is invalid JSON.", {"line": line_no, "error": str(exc)})
    elif suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError("Corpus JSON is invalid.", {"error": str(exc)})
        if isinstance(payload, list):
            raw = payload
        elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
            raw = payload["sources"]
        elif isinstance(payload, dict):
            raw = [payload]
        else:
            raise InputError("Corpus JSON must be an array, a source object, or an object with a sources array.")
    elif suffix == ".csv":
        try:
            raw = list(csv.DictReader(text.splitlines()))
        except csv.Error as exc:
            raise InputError("Corpus CSV is malformed.", {"error": str(exc)})
        for row in raw:
            if isinstance(row.get("permissions"), str):
                try:
                    row["permissions"] = json.loads(row["permissions"])
                except json.JSONDecodeError:
                    raise InputError("CSV permissions cell must be a JSON object.")
            for key in ("authority",):
                if key in row and row[key] != "":
                    try:
                        row[key] = float(row[key])
                    except ValueError:
                        raise InputError(f"CSV {key} must be numeric.")
            for key in ("restricted",):
                if key in row and isinstance(row[key], str):
                    row[key] = row[key].strip().lower() in ("1", "true", "yes")
    elif suffix in (".bib", ".bibtex"):
        raw = _parse_bibtex(text, params)
    elif suffix == ".ris":
        raw = _parse_ris(text, params)
    elif suffix in (".vtt", ".srt"):
        raw = _parse_timestamped(text, suffix == ".srt", params)
    elif suffix in (".txt", ".md", ".html", ".htm", ""):
        clean = ctx.adapter.normalize_source(text) if hasattr(ctx.adapter, "normalize_source") else re.sub(r"<[^>]+>", " ", text)
        raw = [_default_record(params, clean, params.get("title", path.stem))]
    else:
        raise InputError("Unsupported input extension.", {"extension": suffix}, "Use .jsonl, .json, .csv, .txt, .md, .html, .bib, .ris, .vtt, or .srt.")
    max_docs = int(ctx.config["runtime"]["max_documents"])
    if len(raw) > max_docs:
        raise InputError("Input exceeds max_documents.", {"count": len(raw), "limit": max_docs})
    if not raw:
        raise InputError("No source records were found in the input.", {"path": path.name})
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw, 1):
        record = validate_source_record(item, schema, i, ctx)
        if record["id"] in seen:
            raise InputError("Duplicate source id.", {"id": record["id"], "index": i})
        seen.add(record["id"])
        records.append(record)
    return records


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w]+(?:[-'][\w]+)?", normalize_text(text).lower(), flags=re.UNICODE)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", normalize_text(text))
    return [p.strip() for p in parts if p.strip()]


def shingles(tokens: list[str], size: int = 5) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(a: set[Any], b: set[Any]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_language(text: str) -> str:
    lowered = normalize_text(text).lower()
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"
    fil_markers = (" ang ", " ng ", " ay ", " dapat ", " pananaliksik ", " pagsasalin ")
    es_markers = (" la ", " el ", " de ", " requiere ", " investigación ", " traducción ")
    if any(marker in f" {lowered} " for marker in fil_markers):
        return "fil"
    if any(marker in f" {lowered} " for marker in es_markers) or any(ch in lowered for ch in "ñáéíóú"):
        return "es"
    return "en"


def extract_numbers(text: str) -> list[str]:
    return re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?=%|\b|\s)", text)


def extract_dates(text: str) -> list[str]:
    return re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{4}|[A-Z][a-z]+ \d{1,2}, \d{4})\b", text)


def placeholder_tokens(text: str) -> list[str]:
    return re.findall(r"\{\{?[A-Za-z0-9_.:-]+\}?\}", text)


def hashed_vector(text: str, dimensions: int = 256) -> dict[int, float]:
    tokens = tokenize(text)
    grams: list[str] = []
    for token in tokens:
        padded = f"^{token}$"
        grams.extend(padded[i : i + 3] for i in range(max(1, len(padded) - 2)))
    vector: dict[int, float] = {}
    for gram in grams:
        bucket = int(hashlib.sha256(gram.encode("utf-8")).hexdigest()[:8], 16) % dimensions
        sign = 1.0 if int(hashlib.sha256(("sign:" + gram).encode("utf-8")).hexdigest()[:1], 16) % 2 == 0 else -1.0
        vector[bucket] = vector.get(bucket, 0.0) + sign
    norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
    return {k: v / norm for k, v in vector.items()}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(key, 0.0) for key, value in a.items())


def parse_query(query: str, max_chars: int) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise InputError("query must be a non-empty string.")
    if len(query) > max_chars:
        raise InputError("query exceeds max_query_chars.", {"chars": len(query), "limit": max_chars})
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]+"', " ", query)
    field_filters: dict[str, str] = {}
    for match in re.findall(r"\b(language|status|jurisdiction|source_type|license)\s*:\s*([^\s]+)", remainder, flags=re.I):
        field_filters[match[0].lower()] = match[1]
    remainder = re.sub(r"\b(?:language|status|jurisdiction|source_type|license)\s*:\s*[^\s]+", " ", remainder, flags=re.I)
    bool_op = "OR" if re.search(r"\bOR\b", remainder, re.I) else "AND"
    negated = [w.lower() for w in re.findall(r"\bNOT\s+([A-Za-z0-9_-]+)", remainder, flags=re.I)]
    clean = re.sub(r"\b(?:AND|OR|NOT)\b", " ", remainder, flags=re.I)
    tokens = [t for t in tokenize(clean) if t not in negated]
    return {"tokens": tokens, "phrases": [p.lower() for p in phrases], "field_filters": field_filters, "boolean": bool_op, "negated": negated}


def bm25_score(query_tokens: list[str], doc_tokens: list[str], doc_freqs: dict[str, int], total_docs: int, avgdl: float, k1: float = 1.5, b: float = 0.75) -> float:
    if not doc_tokens:
        return 0.0
    tf: dict[str, int] = {}
    for token in doc_tokens:
        tf[token] = tf.get(token, 0) + 1
    dl = len(doc_tokens)
    score = 0.0
    for token in set(query_tokens):
        if token not in tf:
            continue
        df = doc_freqs.get(token, 0)
        idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
        numerator = tf[token] * (k1 + 1)
        denominator = tf[token] + k1 * (1 - b + b * dl / (avgdl or 1))
        score += idf * numerator / denominator
    return score


def search_records(ctx: RunContext, records: list[dict[str, Any]], query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    parsed = parse_query(query, int(ctx.config["runtime"]["max_query_chars"]))
    eligible = [r for r in records if r.get("permissions", {}).get("search") is True]
    excluded = [r["id"] for r in records if r.get("permissions", {}).get("search") is not True]
    filtered: list[dict[str, Any]] = []
    for rec in eligible:
        if params.get("include_blocked") is not True and rec.get("status") in ctx.config["policies"].get("blocked_statuses", []):
            continue
        if rec.get("authority", 0) < float(ctx.config["policies"].get("min_authority", 0)):
            continue
        ok = True
        for key, value in parsed["field_filters"].items():
            if str(rec.get(key, "")).lower() != value.lower():
                ok = False
        for key in ("language", "status", "jurisdiction", "source_type"):
            if params.get(key) and str(rec.get(key, "")).lower() != str(params[key]).lower():
                ok = False
        if params.get("date_from") and rec.get("date", "") < str(params["date_from"]):
            ok = False
        if params.get("date_to") and rec.get("date", "") > str(params["date_to"]):
            ok = False
        if ok:
            filtered.append(rec)
    docs = [(r, tokenize(r["text"])) for r in filtered]
    df: dict[str, int] = {}
    for _, tokens in docs:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    avgdl = sum(len(tokens) for _, tokens in docs) / len(docs) if docs else 1.0
    results: list[dict[str, Any]] = []
    for rec, tokens in docs:
        text_lower = rec["text"].lower()
        if parsed["boolean"] == "AND" and parsed["tokens"] and not all(t in tokens for t in parsed["tokens"]):
            continue
        if parsed["boolean"] == "OR" and parsed["tokens"] and not any(t in tokens for t in parsed["tokens"]):
            continue
        if any(term in text_lower for term in parsed["negated"]):
            continue
        phrase_ok = all(phrase in text_lower for phrase in parsed["phrases"]) if parsed["phrases"] else True
        if not phrase_ok:
            continue
        score = bm25_score(parsed["tokens"], tokens, df, len(docs), avgdl)
        score += 0.35 * len(parsed["phrases"])
        score += 0.05 * float(rec.get("authority", 0))
        if rec.get("status") == "corrected":
            score += 0.08
        if score <= 0 and not parsed["phrases"]:
            continue
        snippet = best_sentence(rec["text"], parsed["tokens"])
        results.append(
            {
                "source_id": rec["id"],
                "title": rec["title"],
                "score": round(score, 6),
                "language": rec["language"],
                "date": rec["date"],
                "version": rec["version"],
                "status": rec["status"],
                "authority": rec["authority"],
                "page_anchor": rec.get("page_anchor", "unanchored"),
                "license": rec["license"],
                "snippet": snippet,
                "citation": f"[{rec['id']} {rec.get('page_anchor', 'unanchored')} {rec['version']}]",
            }
        )
    results.sort(key=lambda x: (-x["score"], x["source_id"]))
    limit = max(1, min(int(params.get("limit", 10)), 100))
    facets: dict[str, dict[str, int]] = {}
    for key in ("language", "status", "jurisdiction"):
        counts: dict[str, int] = {}
        for item in results[:limit]:
            value = str(item.get(key, "unknown"))
            counts[value] = counts.get(value, 0) + 1
        facets[key] = counts
    return {
        "query": query,
        "parsed_query": parsed,
        "eligible_source_count": len(eligible),
        "filtered_source_count": len(filtered),
        "excluded_by_search_permission": excluded,
        "result_count": min(len(results), limit),
        "results": results[:limit],
        "facets": facets,
        "index_type": "local BM25-style lexical index",
    }


def best_sentence(text: str, query_tokens: list[str]) -> str:
    candidates = sentences(text)
    if not candidates:
        return normalize_text(text)[:240]
    ranked = sorted(candidates, key=lambda s: sum(1 for t in query_tokens if t in s.lower()), reverse=True)
    return ranked[0][:400]


def semantic_search(ctx: RunContext, records: list[dict[str, Any]], query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    lexical = search_records(ctx, records, query, {**params, "limit": max(int(params.get("limit", 10)), 5)})
    query_vec = hashed_vector(query)
    rows: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("permissions", {}).get("search") is not True:
            continue
        if params.get("include_blocked") is not True and rec.get("status") in ctx.config["policies"].get("blocked_statuses", []):
            continue
        vec = hashed_vector(rec["text"])
        sim = cosine(query_vec, vec)
        overlap = jaccard(set(tokenize(query)), set(tokenize(rec["text"])))
        hybrid = 0.55 * sim + 0.30 * overlap + 0.15 * float(rec.get("authority", 0))
        rows.append(
            {
                "source_id": rec["id"],
                "title": rec["title"],
                "cosine": round(sim, 6),
                "token_jaccard": round(overlap, 6),
                "hybrid_score": round(hybrid, 6),
                "snippet": best_sentence(rec["text"], tokenize(query)),
                "citation": f"[{rec['id']} {rec.get('page_anchor', 'unanchored')} {rec['version']}]",
                "status": rec["status"],
            }
        )
    rows.sort(key=lambda x: (-x["hybrid_score"], x["source_id"]))
    limit = max(1, min(int(params.get("limit", 10)), 100))
    threshold = float(params.get("threshold", 0.05))
    kept = [r for r in rows[:limit] if r["hybrid_score"] >= threshold]
    return {
        "query": query,
        "vector_model": ctx.config["providers"]["embedding"]["model_id"],
        "reranker_model": ctx.config["providers"]["reranker"]["model_id"],
        "note": "Deterministic hashed character n-gram vectors and lexical-feature reranking; no neural embedding model is bundled.",
        "threshold": threshold,
        "result_count": len(kept),
        "results": kept,
        "lexical_baseline_top_ids": [r["source_id"] for r in lexical["results"][:3]],
    }


def read_glossary(ctx: RunContext, path: str | None = None) -> list[dict[str, str]]:
    glossary_path = ctx.user_path(path, must_exist=True) if path else ctx.asset_path("glossary")
    text = read_text_file(ctx, glossary_path)
    rows = list(csv.DictReader(text.splitlines()))
    required = {"source_language", "target_language", "source_term", "target_term", "do_not_translate"}
    if not rows or not required.issubset(set(rows[0])):
        raise InputError("Glossary CSV is missing required columns.", {"required": sorted(required)})
    return rows


def read_translation_memory(ctx: RunContext, path: str | None = None) -> list[dict[str, str]]:
    tm_path = ctx.user_path(path, must_exist=True) if path else ctx.asset_path("translation_memory")
    payload = json.loads(read_text_file(ctx, tm_path))
    entries = payload.get("entries", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise InputError("Translation memory must contain an entries array.")
    return entries


def apply_glossary(text: str, glossary: list[dict[str, str]], source_language: str, target_language: str) -> dict[str, Any]:
    placeholders = placeholder_tokens(text)
    protected: dict[str, str] = {}
    work = text
    for i, token in enumerate(dict.fromkeys(placeholders)):
        key = f"__ORWT_PH_{i}__"
        protected[key] = token
        work = work.replace(token, key)
    applicable = [
        row
        for row in glossary
        if row["target_language"] == target_language
        and row["source_language"] in (source_language, "any", "*")
        and str(row.get("do_not_translate", "false")).lower() not in ("true", "1", "yes")
    ]
    applicable.sort(key=lambda row: len(row["source_term"]), reverse=True)
    applied: list[dict[str, str]] = []
    for row in applicable:
        pattern = re.compile(re.escape(row["source_term"]), re.I)
        if pattern.search(work):
            work = pattern.sub(row["target_term"], work)
            applied.append({"source_term": row["source_term"], "target_term": row["target_term"]})
    for key, token in protected.items():
        work = work.replace(key, token)
    preserved = [token for token in placeholders if token in work]
    return {
        "text": work,
        "applied_terms": applied,
        "placeholders_in_source": placeholders,
        "placeholders_preserved": preserved,
        "do_not_translate_ok": len(preserved) == len(placeholders),
    }


def render_template(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


class ApprovalStore:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.path = ctx.workspace_file(ctx.config["storage"].get("approvals", "approvals.json"))

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": "1.0", "records": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError("APPROVAL_STORE_CORRUPT", "Approval store is unreadable.", {"error": str(exc)}, "Restore approvals.json from a known-good backup or choose a fresh --workspace.")
        if not isinstance(payload.get("records"), list):
            raise ToolError("APPROVAL_STORE_CORRUPT", "Approval store has no records array.")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        write_json(self.ctx, self.path, payload)

    def request(self, action: str, ticket: str, notes: str = "") -> dict[str, Any]:
        if not action or not ticket:
            raise InputError("review request requires action and ticket.")
        payload = self._load()
        if any(r["ticket"] == ticket and r["status"] == "pending" for r in payload["records"]):
            raise InputError("A pending review with this ticket already exists.", {"ticket": ticket})
        record = {
            "id": f"review-{len(payload['records']) + 1:04d}",
            "action": action,
            "ticket": ticket,
            "status": "pending",
            "notes": notes,
            "requested_at": utc_now(),
            "approver": None,
            "approved_at": None,
            "token_hash": None,
            "expires_at": None,
        }
        payload["records"].append(record)
        self._save(payload)
        append_jsonl(self.ctx, self.ctx.workspace_file(self.ctx.config["storage"].get("audit_log", "audit.jsonl")), {"timestamp_utc": utc_now(), "event": "review_requested", "ticket": ticket, "action": action, "run_id": self.ctx.run_id})
        return {k: v for k, v in record.items() if k != "token_hash"}

    def approve(self, ticket: str, approver: str, confirmation: str) -> dict[str, Any]:
        expected = self.ctx.config["approval"]["confirmation_text"]
        if confirmation != expected:
            raise ApprovalError(f"Explicit confirmation must be exactly {expected}.", {"ticket": ticket, "received": confirmation})
        if self.ctx.config["approval"].get("require_nonempty_approver", True) and not approver.strip():
            raise InputError("Approver name must be non-empty.", {"ticket": ticket})
        payload = self._load()
        record = next((r for r in payload["records"] if r["ticket"] == ticket and r["status"] == "pending"), None)
        if not record:
            raise InputError("No pending review request matches this ticket.", {"ticket": ticket})
        token = secrets.token_hex(24)
        ttl = float(self.ctx.config["approval"].get("token_ttl_hours", 24))
        expires = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=ttl)
        record.update(
            {
                "status": "approved",
                "approver": approver.strip(),
                "approved_at": utc_now(),
                "token_hash": sha256_text(token),
                "expires_at": expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        self._save(payload)
        append_jsonl(self.ctx, self.ctx.workspace_file(self.ctx.config["storage"].get("audit_log", "audit.jsonl")), {"timestamp_utc": utc_now(), "event": "review_approved", "ticket": ticket, "action": record["action"], "approver": approver.strip(), "run_id": self.ctx.run_id})
        return {"ticket": ticket, "action": record["action"], "approval_token": token, "expires_at": record["expires_at"], "status": "approved"}

    def reject(self, ticket: str, approver: str, reason: str) -> dict[str, Any]:
        payload = self._load()
        record = next((r for r in payload["records"] if r["ticket"] == ticket and r["status"] == "pending"), None)
        if not record:
            raise InputError("No pending review request matches this ticket.", {"ticket": ticket})
        record.update({"status": "rejected", "approver": approver, "rejected_at": utc_now(), "reason": reason})
        self._save(payload)
        return {k: v for k, v in record.items() if k != "token_hash"}

    def list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "token_hash"} for r in self._load()["records"]]

    def validate(self, action: str, token: str | None) -> dict[str, Any]:
        if not token:
            raise ApprovalError(f"Action '{action}' requires a human approval token.", {"action": action})
        payload = self._load()
        token_hash = sha256_text(token)
        now = _dt.datetime.now(_dt.timezone.utc)
        for record in payload["records"]:
            if record.get("status") != "approved" or record.get("action") != action or not record.get("token_hash"):
                continue
            if not hmac.compare_digest(record["token_hash"], token_hash):
                continue
            expires = _dt.datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
            if expires < now:
                raise ApprovalError("Approval token has expired.", {"ticket": record["ticket"], "action": action})
            return {k: v for k, v in record.items() if k != "token_hash"}
        raise ApprovalError("No matching approved review token was found.", {"action": action})


def require_approval(ctx: RunContext, action: str, token: str | None) -> dict[str, Any]:
    sensitive = set(ctx.config["policies"].get("require_human_review_for", []))
    if action not in sensitive:
        return {"required": False, "action": action}
    record = ApprovalStore(ctx).validate(action, token)
    return {"required": True, "action": action, "ticket": record["ticket"], "approver": record["approver"]}


class CheckpointStore:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        prefix = ctx.config["storage"].get("checkpoint_prefix", "checkpoint-")
        self.path = ctx.workspace_file(f"{prefix}{ctx.run_id}.json")

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError("CHECKPOINT_CORRUPT", "Checkpoint is unreadable.", {"error": str(exc)}, "Choose a fresh --run-id or restore the checkpoint from backup.")

    def save(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["updated_at_utc"] = utc_now()
        write_json(self.ctx, self.path, payload)


def provider_warnings(ctx: RunContext, operation: str) -> list[str]:
    mapping = {
        "document-layout": ("ocr", "handwriting"),
        "audio-transcripts": ("asr",),
        "translation": ("translation",),
        "semantic-retrieval": ("embedding",),
        "question-answering": ("language_model",),
        "composition": ("language_model",),
        "specialized-writing": ("language_model",),
    }
    warnings: list[str] = []
    for provider in mapping.get(operation, ()):
        settings = ctx.config["providers"].get(provider, {})
        if settings.get("status") == "configurable-not-executed":
            warnings.append(f"{provider}: {settings.get('reason', 'external model execution is not bundled')}")
        elif provider == "embedding":
            warnings.append("embedding: deterministic hashed n-grams are used; neural embeddings are not bundled")
    return warnings


def unavailable_integrations(ctx: RunContext) -> list[dict[str, Any]]:
    return [
        {"provider": name, "mode": settings.get("mode"), "status": settings.get("status"), "reason": settings.get("reason", "implemented in this build" if settings.get("status") == "implemented" else "not bundled")}
        for name, settings in ctx.config["providers"].items()
        if settings.get("status") != "implemented"
    ]


def capability_declaration(
    ctx: RunContext,
    capability: dict[str, Any],
    input_path: str | None,
    output_path: str | None,
    params: dict[str, Any],
    plan_only: bool,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if input_path:
        records = read_input_records(ctx, input_path, params, required=True)
    exact_sources = [
        {"id": r["id"], "title": r["title"], "sha256": r["sha256"], "version": r["version"], "license": r["license"], "status": r["status"], "page_anchor": r.get("page_anchor", "unanchored")}
        for r in records
    ]
    versions = {
        "corpus": sorted({r["version"] for r in records}) if records else ["not-read-in-plan-only-mode"],
        "models": {k: v.get("model_id") for k, v in ctx.config["providers"].items()},
        "tool": ctx.config["version"],
        "catalog_edition": ctx.catalog.get("edition"),
    }
    languages = sorted({r["language"] for r in records}) if records else ctx.config["policies"]["languages"]
    jurisdictions = sorted({r["jurisdiction"] for r in records}) if records else [ctx.config["policies"]["jurisdiction"]]
    permissions = {
        "search": all(r["permissions"].get("search") for r in records) if records else "declared-by-input",
        "quote": all(r["permissions"].get("quote") for r in records) if records else "declared-by-input",
        "redistribute": all(r["permissions"].get("redistribute") for r in records) if records else "declared-by-input",
        "export": all(r["permissions"].get("export") for r in records) if records else "declared-by-input",
    }
    confidence = 0.95 if records else 0.55
    if capability["operation"] in {"document-layout", "audio-transcripts", "translation", "semantic-retrieval", "question-answering", "composition", "specialized-writing"}:
        confidence -= 0.15
    return {
        "capability_id": capability["capability_id"],
        "exact_name": capability["name"],
        "slug": capability["slug"],
        "category": capability["category"],
        "description": capability["description"],
        "responsibility": capability["responsibility"],
        "operation": capability["operation"],
        "operation_description": capability["operation_description"],
        "mode": capability["mode"],
        "status": "declared-plan-only" if plan_only else "ready-for-dispatch",
        "exact_sources": exact_sources,
        "corpus_and_model_versions": versions,
        "permissions": permissions,
        "language_and_jurisdiction_scope": {"languages": languages, "jurisdictions": jurisdictions},
        "inputs": {"path": input_path, "params": params, "records_read": len(records)},
        "outputs": {"path": output_path, "structured_json": True},
        "confidence": round(confidence, 2),
        "failure_conditions": [
            "missing or unreadable UTF-8 input",
            "schema or permission validation failure",
            "blocked retracted/superseded/unverified source",
            "timeout, cancellation, or output byte-limit breach",
            "missing approval for a sensitive action",
        ],
        "citations": [f"[{r['id']} {r.get('page_anchor', 'unanchored')} {r['version']}]" for r in records],
        "provenance": {"run_id": ctx.run_id, "config_digest": ctx.config_digest, "catalog_edition": ctx.catalog.get("edition")},
        "copyright_controls": {"max_quote_chars": ctx.config["policies"]["max_quote_chars"], "license_checked": bool(records), "redistribution_checked": True},
        "human_review_points": ctx.config["policies"]["require_human_review_for"],
        "clean_state_checks": ["network disabled", "standard library only", "relative asset references", "no bundled credentials", "bounded I/O"],
        "unavailable_integrations": [w for w in provider_warnings(ctx, capability["operation"])],
    }


__all__ = [
    "ToolError", "InputError", "ConfigError", "ApprovalError", "CancelledError", "TimeoutError_",
    "utc_now", "canonical_json", "sha256_text", "sha256_file", "deep_merge", "load_config",
    "load_catalog", "load_request_schema", "validate_request", "parse_json_arg", "structured_result",
    "RunContext", "atomic_write", "write_json", "write_text", "append_jsonl", "read_text_file",
    "read_input_records", "validate_source_record", "normalize_text", "tokenize", "sentences",
    "shingles", "jaccard", "detect_language", "extract_numbers", "extract_dates", "placeholder_tokens",
    "hashed_vector", "cosine", "parse_query", "search_records", "best_sentence", "semantic_search",
    "read_glossary", "read_translation_memory", "apply_glossary", "render_template", "ApprovalStore",
    "require_approval", "CheckpointStore", "provider_warnings", "unavailable_integrations",
    "capability_declaration",
]
