#!/usr/bin/env python3
"""Shared runtime utilities for the agent-multitasking-orchestration Skill Tool.

This module intentionally uses only the Python standard library. It provides
root-relative path resolution, structured errors, atomic JSON IO, a small JSON
Schema validator, and deterministic helpers used by every tool module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_NAME = PACKAGE_DIR.name
SKILL_ROOT = PACKAGE_DIR.parent

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_APPROVAL = 3
EXIT_RUNTIME = 4
EXIT_CANCELLED = 5


class ToolError(Exception):
    """A user-facing, structured error with recovery guidance."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        recovery: str | None = None,
        exit_code: int = EXIT_VALIDATION,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.recovery = recovery or "Check the command arguments and knowledge-readme.md."
        self.exit_code = exit_code

    def payload(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "recovery": self.recovery,
            },
        }


@dataclass(frozen=True)
class Paths:
    root: Path
    package: Path

    def safe(self, value: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
        return safe_path(value, root=self.root, must_exist=must_exist)

    def package_file(self, filename: str) -> Path:
        candidate = self.package / filename
        if not candidate.is_file():
            raise ToolError(
                "MISSING_PACKAGE_FILE",
                f"Required package file is missing: {self.package.name}/{filename}",
                details={"filename": filename},
                recovery="Restore the file from the delivered Skill Tool folder.",
            )
        return candidate


def current_paths() -> Paths:
    return Paths(root=SKILL_ROOT, package=PACKAGE_DIR)


def safe_path(
    value: str | os.PathLike[str],
    *,
    root: Path | None = None,
    must_exist: bool = False,
) -> Path:
    """Resolve a relative path beneath the future skill root.

    Package references are deliberately root-relative. Absolute paths and parent
    traversal are rejected so copied packages remain portable and auditable.
    """
    root = (root or SKILL_ROOT).resolve()
    raw = os.fspath(value)
    if not raw:
        raise ToolError("EMPTY_PATH", "A required path is empty.")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ToolError(
            "ABSOLUTE_PATH_REJECTED",
            "Use a path relative to the future skill root.",
            details={"path": raw},
        )
    if any(part == ".." for part in candidate.parts):
        raise ToolError(
            "PARENT_TRAVERSAL_REJECTED",
            "Parent-directory traversal is not permitted in package references.",
            details={"path": raw},
        )
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ToolError(
            "PATH_OUTSIDE_ROOT",
            "Resolved path is outside the future skill root.",
            details={"path": raw},
        ) from exc
    if must_exist and not resolved.exists():
        raise ToolError(
            "FILE_NOT_FOUND",
            f"Path does not exist: {raw}",
            details={"path": raw},
            recovery="Provide an existing root-relative input path.",
            exit_code=EXIT_VALIDATION,
        )
    return resolved


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_bytes(path: Path, *, max_bytes: int = 10_485_760) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError(
            "FILE_UNREADABLE",
            f"Cannot inspect input file: {path.name}",
            details={"path": path.name, "reason": str(exc)},
            recovery="Check that the file exists and is readable.",
        ) from exc
    if size > max_bytes:
        raise ToolError(
            "INPUT_TOO_LARGE",
            "Input exceeds the configured byte limit.",
            details={"size_bytes": size, "max_bytes": max_bytes},
            recovery="Increase limits.max_input_bytes in a custom tools-config.json or split the input.",
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ToolError(
            "FILE_UNREADABLE",
            f"Cannot read input file: {path.name}",
            details={"path": path.name, "reason": str(exc)},
            recovery="Check file permissions and encoding.",
        ) from exc


def load_json(path: Path, *, label: str = "JSON") -> Any:
    raw = read_bytes(path)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ToolError(
            "INVALID_UTF8",
            f"{label} is not valid UTF-8.",
            details={"path": path.name, "reason": str(exc)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ToolError(
            "INVALID_JSON",
            f"{label} is not valid JSON.",
            details={
                "path": path.name,
                "line": exc.lineno,
                "column": exc.colno,
                "reason": exc.msg,
            },
            recovery="Fix the JSON syntax and retry.",
        ) from exc


def load_text(path: Path, *, max_bytes: int = 10_485_760) -> str:
    return read_bytes(path, max_bytes=max_bytes).decode("utf-8-sig", errors="replace")


def write_json_atomic(path: Path, value: Any, *, indent: int = 2) -> None:
    """Atomically write JSON so interrupted runs leave a recoverable old file."""
    payload = json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    except OSError as exc:
        raise ToolError(
            "OUTPUT_WRITE_FAILED",
            f"Cannot write output: {path.name}",
            details={"path": path.name, "reason": str(exc)},
            recovery="Choose a writable root-relative output path.",
            exit_code=EXIT_RUNTIME,
        ) from exc


def write_text_atomic(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    except OSError as exc:
        raise ToolError(
            "OUTPUT_WRITE_FAILED",
            f"Cannot write output: {path.name}",
            details={"path": path.name, "reason": str(exc)},
            recovery="Choose a writable root-relative output path.",
            exit_code=EXIT_RUNTIME,
        ) from exc


def _resolve_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local JSON Schema references are supported: {ref}")
    node: Any = root_schema
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise ValueError(f"Unresolved schema reference: {ref}")
        node = node[key]
    if not isinstance(node, dict):
        raise ValueError(f"Schema reference is not an object: {ref}")
    return node


def schema_errors(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Validate the practical JSON Schema subset used by packaged schemas.

    Supported keywords: type, $ref/$defs, required, properties, items, enum,
    additionalProperties=false, minimum, maximum, minLength, maxLength,
    minItems, maxItems, and pattern. Unknown keywords are ignored.
    """
    root_schema = root_schema or schema
    if "$ref" in schema:
        try:
            schema = _resolve_ref(str(schema["$ref"]), root_schema)
        except ValueError as exc:
            return [f"{path}: {exc}"]

    expected = schema.get("type")
    errors: list[str] = []
    type_map: dict[str, tuple[type, ...]] = {
        "object": (dict,),
        "array": (list,),
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    if isinstance(expected, list):
        types: list[type] = []
        for item in expected:
            types.extend(type_map.get(str(item), ()))
        if types and not isinstance(value, tuple(types)):
            errors.append(f"{path}: expected one of {expected}")
    elif isinstance(expected, str) and expected in type_map:
        # bool is a subclass of int in Python; exclude it from numeric checks.
        if expected in {"integer", "number"} and isinstance(value, bool):
            errors.append(f"{path}: expected {expected}")
        elif not isinstance(value, type_map[expected]):
            errors.append(f"{path}: expected {expected}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value must be one of {schema['enum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], child, root_schema=root_schema, path=f"{path}.{key}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, root_schema=root_schema, path=f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: expected at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: expected at most {schema['maxLength']} characters")
        pattern = schema.get("pattern")
        if pattern and re.search(str(pattern), value) is None:
            errors.append(f"{path}: does not match pattern {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
    return errors


def require_schema(value: Any, schema: dict[str, Any], *, label: str) -> None:
    errors = schema_errors(value, schema)
    if errors:
        raise ToolError(
            "SCHEMA_VALIDATION_FAILED",
            f"{label} does not match the packaged schema.",
            details={"errors": errors[:50], "error_count": len(errors)},
            recovery="Correct the listed fields and retry validate or the requested command.",
        )


def load_schema(name: str, *, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or current_paths()
    schemas = load_json(paths.package_file("asset-schemas.json"), label="Schema bundle")
    if name not in schemas:
        raise ToolError(
            "SCHEMA_NOT_FOUND",
            f"Schema '{name}' is not defined.",
            details={"available": sorted(schemas.keys())},
            exit_code=EXIT_RUNTIME,
        )
    return schemas[name]


def load_config(config_path: str | None = None, *, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or current_paths()
    default = paths.package_file("tools-config.json")
    source = paths.safe(config_path, must_exist=True) if config_path else default
    config = load_json(source, label="Configuration")
    require_schema(config, load_schema("config", paths=paths), label="Configuration")
    return config


def config_path(config: dict[str, Any], key: str, *, paths: Paths | None = None) -> Path:
    paths = paths or current_paths()
    raw = config.get("paths", {}).get(key)
    if not isinstance(raw, str) or not raw:
        raise ToolError(
            "CONFIG_PATH_MISSING",
            f"Configuration is missing paths.{key}.",
            details={"key": key},
            exit_code=EXIT_RUNTIME,
        )
    return paths.safe(raw, must_exist=True)


def deadline(seconds: float) -> float:
    return time.monotonic() + max(0.01, float(seconds))


def bounded_sleep(seconds: float, *, maximum: float = 0.05) -> float:
    applied = max(0.0, min(float(seconds), maximum))
    if applied:
        time.sleep(applied)
    return applied


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def clip(value: str, maximum: int = 240) -> str:
    text = " ".join(value.split())
    return text if len(text) <= maximum else text[: maximum - 1] + "…"
