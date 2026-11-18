#!/usr/bin/env python3
"""powerful-work-splitter :: verification and failure-recovery layer.

Responsibilities
----------------
* Assert artifact state (CSV, JSON with a schema subset, binary signatures,
  manifest completeness, cross-file references).
* Run the final verification gate set (coverage, gates, files, artifacts,
  delivery hygiene).
* Watch a workspace until writes settle, bounded by a timeout.
* Propose and, only after an explicit approval token, perform temp-file
  cleanup.
* Provide bounded repair-attempt bookkeeping helpers used by the primary tool.

This module is importable by tools-work-splitter.py and also runs as a
standalone CLI for debugging a package without touching checkpoint state.
Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.dont_write_bytecode = True

CORE_FILENAME = "tools-work-splitter-core.py"


def _load_core():
    existing = sys.modules.get("pws_core")
    if existing is not None and hasattr(existing, "ToolError"):
        return existing
    path = Path(__file__).resolve().parent / CORE_FILENAME
    spec = importlib.util.spec_from_file_location("pws_core", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Unable to load core module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pws_core"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()

EXIT_OK = core.EXIT_OK
EXIT_USAGE = core.EXIT_USAGE
EXIT_MISSING = core.EXIT_MISSING
EXIT_GATE = core.EXIT_GATE
EXIT_APPROVAL = core.EXIT_APPROVAL
EXIT_LIMIT = core.EXIT_LIMIT
EXIT_PARTIAL = core.EXIT_PARTIAL
ToolError = core.ToolError

MAGIC_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    "pdf": (b"%PDF",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "docx": (b"PK\x03\x04",),
    "xlsx": (b"PK\x03\x04",),
    "pptx": (b"PK\x03\x04",),
    "odt": (b"PK\x03\x04",),
    "ods": (b"PK\x03\x04",),
    "epub": (b"PK\x03\x04",),
    "wav": (b"RIFF",),
    "mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    "mp4": (b"ftyp",),
    "mov": (b"ftyp",),
    "webp": (b"RIFF",),
    "ogg": (b"OggS",),
    "flac": (b"fLaC",),
    "sqlite": (b"SQLite format 3\x00",),
}

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_PATH_RE = re.compile(r"`([^`\n]{3,200})`")
BARE_PATH_RE = re.compile(r"(?<![\w/.-])((?:\.{1,2}/)?[A-Za-z0-9][A-Za-z0-9._/-]*\.[A-Za-z0-9]{1,8})(?![\w/.-])")


# ---------------------------------------------------------------------------
# JSON schema subset
# ---------------------------------------------------------------------------


def validate_json_schema(instance: Any, schema: Dict[str, Any], *, path: str = "$") -> List[str]:
    """Validate the common JSON Schema keywords used by the packaged schema.

    Supported: type, required, properties, items, enum, pattern, minimum,
    maximum, minLength, minItems, maxItems, additionalProperties (ignored when
    true or object). Unknown keywords are ignored rather than failing, so a
    richer schema still yields useful errors.
    """
    errors: List[str] = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(instance, t) for t in types):
            errors.append(f"{path}: expected type {expected}, got {_type_name(instance)}")
            return errors

    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        errors.append(f"{path}: value {instance!r} is not one of {enum}")

    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, instance):
            errors.append(f"{path}: string {instance!r} does not match pattern {pattern}")
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            errors.append(f"{path}: string is shorter than minLength {min_length}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{path}: {instance} is less than minimum {minimum}")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"{path}: {instance} is greater than maximum {maximum}")

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            errors.append(f"{path}: has {len(instance)} item(s), fewer than minItems {min_items}")
        if isinstance(max_items, int) and len(instance) > max_items:
            errors.append(f"{path}: has {len(instance)} item(s), more than maxItems {max_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_json_schema(item, item_schema, path=f"{path}[{index}]"))

    if isinstance(instance, dict):
        for key in schema.get("required", []) or []:
            if key not in instance:
                errors.append(f"{path}: missing required key '{key}'")
        properties = schema.get("properties") or {}
        for key, sub_schema in properties.items():
            if key in instance and isinstance(sub_schema, dict):
                errors.extend(validate_json_schema(instance[key], sub_schema, path=f"{path}.{key}"))
    return errors


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _read_bytes(path: Path, *, limit: int = 268435456) -> bytes:
    if not path.exists():
        raise ToolError(
            f"Artifact not found: {path}",
            code="artifact-missing",
            exit_code=EXIT_MISSING,
            hint="Produce the artifact before verifying it.",
        )
    if path.is_dir():
        raise ToolError(f"Expected a file but got a directory: {path}", code="artifact-not-file")
    size = path.stat().st_size
    if size > limit:
        raise ToolError(
            f"Artifact exceeds the read limit of {limit} bytes",
            code="artifact-too-large",
            exit_code=EXIT_LIMIT,
            details={"bytes": size, "limit": limit},
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ToolError(
            f"Artifact is not readable: {path}",
            code="artifact-unreadable",
            exit_code=EXIT_MISSING,
            details={"error": str(exc)},
        ) from exc


def assert_file(
    path: Path,
    *,
    min_bytes: int = 0,
    signature: bool = False,
    expected_ext: Optional[str] = None,
) -> Dict[str, Any]:
    data = _read_bytes(path)
    problems: List[str] = []
    if len(data) < min_bytes:
        problems.append(f"size {len(data)} bytes is below the required {min_bytes} bytes")
    if expected_ext and path.suffix.lower().lstrip(".") != expected_ext.lower().lstrip("."):
        problems.append(f"extension {path.suffix or '(none)'} does not match {expected_ext}")
    if signature:
        ext = path.suffix.lower().lstrip(".")
        magics = MAGIC_SIGNATURES.get(ext)
        head = data[:16]
        if ext in ("mp4", "mov"):
            head = data[4:12]
        if magics is None:
            problems.append(f"no signature rule for extension '{ext or '(none)'}'")
        elif not any(head.startswith(magic) for magic in magics):
            problems.append(f"file signature does not match '{ext}' (expected one of {[m.hex() for m in magics]})")
    if path.suffix.lower() in (".md", ".txt", ".csv", ".json", ".html", ".htm", ".svg", ".xml", ".py", ".js", ".ts", ".css"):
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            problems.append("text artifact is not valid UTF-8")
    return {
        "kind": "file",
        "path": path.as_posix(),
        "bytes": len(data),
        "status": "pass" if not problems else "fail",
        "problems": problems,
    }


def assert_csv(
    path: Path,
    *,
    require_header: Sequence[str] = (),
    min_rows: int = 1,
    expect_rows: Optional[int] = None,
) -> Dict[str, Any]:
    import csv
    import io

    data = _read_bytes(path)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"kind": "csv", "path": path.as_posix(), "status": "fail", "problems": ["file is not valid UTF-8"]}
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    problems: List[str] = []
    if not rows:
        return {"kind": "csv", "path": path.as_posix(), "status": "fail", "problems": ["file has no non-empty rows"]}
    header = [cell.strip() for cell in rows[0]]
    body = rows[1:]
    for column in require_header:
        if column not in header:
            problems.append(f"missing required column '{column}'")
    widths = {len(row) for row in body}
    if len(widths) > 1:
        problems.append(f"ragged rows: field counts {sorted(widths)}")
    if len(body) < min_rows:
        problems.append(f"only {len(body)} data row(s); at least {min_rows} required")
    if expect_rows is not None and len(body) != expect_rows:
        problems.append(f"expected exactly {expect_rows} data row(s), found {len(body)}")
    return {
        "kind": "csv",
        "path": path.as_posix(),
        "status": "pass" if not problems else "fail",
        "rows": len(body),
        "columns": header,
        "problems": problems,
    }


def assert_json(
    path: Path,
    *,
    schema: Optional[Dict[str, Any]] = None,
    require_keys: Sequence[str] = (),
) -> Dict[str, Any]:
    data = _read_bytes(path)
    try:
        instance = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "kind": "json",
            "path": path.as_posix(),
            "status": "fail",
            "problems": [f"invalid JSON: {exc}"],
        }
    problems: List[str] = []
    for key in require_keys:
        if not isinstance(instance, dict) or key not in instance:
            problems.append(f"missing required key '{key}'")
    if schema is not None:
        problems.extend(validate_json_schema(instance, schema))
    return {
        "kind": "json",
        "path": path.as_posix(),
        "status": "pass" if not problems else "fail",
        "problems": problems,
    }


def assert_manifest(root: Path, *, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not root.exists() or not root.is_dir():
        raise ToolError(f"Workspace root not found: {root}", code="root-missing", exit_code=EXIT_MISSING)
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    problems: List[str] = []
    if plan:
        present = {Path(f).name.lower() for f in files}
        for item in plan.get("files", []):
            if item.get("required", True) and Path(item["path"]).name.lower() not in present:
                problems.append(f"declared file missing from package: {item['path']}")
        for stage in plan.get("stages", []):
            for output in stage.get("outputs", []):
                if Path(output).name.lower() not in present:
                    problems.append(f"declared output missing: {stage['id']} -> {output}")
    return {
        "kind": "manifest",
        "path": root.as_posix(),
        "root": root.as_posix(),
        "count": len(files),
        "files": files[:200],
        "truncated": len(files) > 200,
        "status": "pass" if not problems else "fail",
        "problems": problems,
    }


def scan_references(path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
    data = _read_bytes(path)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"kind": "references", "path": path.as_posix(), "status": "fail", "problems": ["reference source is not valid UTF-8"]}
    base = root or path.parent
    candidates: List[str] = []
    for regex in (MARKDOWN_LINK_RE, CODE_PATH_RE):
        candidates.extend(match.strip() for match in regex.findall(text))
    candidates.extend(BARE_PATH_RE.findall(text))

    resolved: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen = set()
    for raw in candidates:
        target = raw.strip().strip("<>\"'")
        if not target or target in seen:
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
            continue
        if "<" in target or ">" in target or " " in target and "/" not in target:
            continue
        seen.add(target)
        candidate = (base / target).resolve() if not Path(target).is_absolute() else Path(target)
        try:
            candidate.relative_to(base.resolve())
            inside = True
        except ValueError:
            inside = False
        exists = candidate.exists()
        resolved.append({"reference": target, "resolved": candidate.as_posix(), "inside_root": inside, "exists": exists})
        if not exists:
            problems.append(f"unresolved reference '{target}'")
        elif not inside:
            problems.append(f"reference '{target}' resolves outside the root")
    return {
        "kind": "references",
        "path": path.as_posix(),
        "checked": len(resolved),
        "status": "pass" if not problems else "fail",
        "references": resolved,
        "problems": problems,
    }


def run_assert(
    kind: str,
    path: Path,
    *,
    min_bytes: int = 0,
    signature: bool = False,
    expected_ext: Optional[str] = None,
    require_header: Sequence[str] = (),
    min_rows: int = 1,
    expect_rows: Optional[int] = None,
    schema_path: Optional[Path] = None,
    require_keys: Sequence[str] = (),
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    if kind == "file":
        return assert_file(path, min_bytes=min_bytes, signature=signature, expected_ext=expected_ext)
    if kind == "csv":
        return assert_csv(path, require_header=require_header, min_rows=min_rows, expect_rows=expect_rows)
    if kind == "json":
        schema = core.load_json_file(schema_path, label="Schema") if schema_path else None
        return assert_json(path, schema=schema, require_keys=require_keys)
    if kind == "manifest":
        return assert_manifest(path, plan=None)
    if kind == "reference":
        return scan_references(path, root=root)
    raise ToolError(
        f"Unknown assertion kind: {kind}",
        code="assert-kind-invalid",
        hint="Use one of: file, csv, json, manifest, reference.",
    )


# ---------------------------------------------------------------------------
# Verification gates
# ---------------------------------------------------------------------------


def verify_package(
    *,
    plan: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    root: Path,
    policy: Dict[str, Any],
    require_artifacts: bool = True,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    # 1. Requirement coverage
    problems: List[str] = []
    stage_ids = {stage["id"] for stage in plan.get("stages", [])}
    for req in plan.get("requirements", []):
        owner = req.get("owner_stage")
        if owner not in stage_ids:
            problems.append(f"{req.get('id')} has no valid owner stage")
        if not req.get("acceptance"):
            problems.append(f"{req.get('id')} has no acceptance check")
    if state:
        for stage in state.get("stages", []):
            for req_id in stage.get("requirements", []):
                if req_id not in {r["id"] for r in plan.get("requirements", [])}:
                    problems.append(f"state stage {stage['id']} references unknown requirement {req_id}")
    checks.append(
        {
            "check": "coverage",
            "status": "pass" if not problems else "fail",
            "detail": f"{len(plan.get('requirements', []))} requirement(s) mapped to {len(stage_ids)} stage(s)",
            "problems": problems,
            "next_action": "Assign the orphan requirement to a stage or remove it deliberately",
        }
    )

    # 2. Milestone gates
    problems = []
    stages = state.get("stages", []) if state else []
    if state:
        for index, stage in enumerate(stages):
            if stage.get("status") == "complete" and not stage.get("verified"):
                problems.append(f"stage {stage['id']} was completed without recorded verification")
            if stage.get("status") == "complete" and index + 1 < len(stages):
                nxt = stages[index + 1]
                if nxt.get("status") == "complete" and stage.get("status") != "complete":
                    problems.append(f"stage {nxt['id']} completed before {stage['id']}")
        open_stages = [s["id"] for s in stages if s.get("status") != "complete"]
        if open_stages:
            problems.append("stage(s) still open: " + ", ".join(open_stages))
        final = stages[-1] if stages else None
        if final and final.get("status") == "complete":
            incomplete = [s["id"] for s in stages[:-1] if s.get("status") != "complete"]
            if incomplete:
                problems.append("final assembly closed while stage(s) remain incomplete: " + ", ".join(incomplete))
    else:
        problems.append("no checkpoint state supplied; gate evidence cannot be confirmed")
    checks.append(
        {
            "check": "gates",
            "status": "pass" if not problems else "repair",
            "detail": f"Stage completion and evidence ordering inspected ({len(stages)} stage(s))" if state else "no checkpoint state supplied",
            "problems": problems,
            "next_action": "Record evidence with 'checkpoint continue --verified' before closing a stage",
        }
    )

    # 3. File contract
    problems = []
    present: Dict[str, bool] = {}
    if root.exists():
        present = {p.name.lower(): True for p in root.rglob("*") if p.is_file()}
    for item in plan.get("files", []):
        name = Path(item["path"]).name.lower()
        if item.get("present") and name not in present:
            problems.append(f"file recorded as present but not found under root: {item['path']}")
        if item.get("required", True) and name not in present:
            problems.append(f"required file missing: {item['path']}")
    checks.append(
        {
            "check": "files",
            "status": "pass" if not problems else "blocked",
            "detail": f"{len(plan.get('files', []))} file(s) in the file contract; root {root.as_posix()}",
            "problems": problems,
            "next_action": "Request the missing file from the user; never substitute invented content",
        }
    )

    # 4. Declared artifacts (state records the outputs actually produced)
    problems = []
    declared = 0
    seen_outputs = set()
    sources = list(plan.get("stages", []))
    if state:
        sources += [stage for stage in state.get("stages", []) if stage not in plan.get("stages", [])]
    for stage in sources:
        for output in stage.get("outputs", []):
            if (stage["id"], output) in seen_outputs:
                continue
            seen_outputs.add((stage["id"], output))
            declared += 1
            if Path(output).name.lower() not in present:
                problems.append(f"{stage['id']} declares output that is absent: {output}")
    status = "pass"
    if problems:
        status = "fail"
    elif declared == 0:
        status = "warn"
        problems.append("no stage declares outputs yet; record them with 'checkpoint continue --output'")
    checks.append(
        {
            "check": "artifacts",
            "status": status if require_artifacts else "warn",
            "detail": f"{declared} declared artifact(s) checked against the root",
            "problems": problems,
            "next_action": "Declare stage outputs with 'checkpoint continue --output' or remove the stale declaration",
        }
    )

    # 5. Delivery hygiene
    problems = []
    limits = policy["files"]
    temp_hits: List[str] = []
    too_deep: List[str] = []
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            total += 1
            relative = path.relative_to(root)
            if any(relative.match(pattern) if False else path.match(pattern) for pattern in core.TEMP_PATTERNS):
                temp_hits.append(relative.as_posix())
            if any(part in core.TEMP_DIR_NAMES for part in relative.parts):
                temp_hits.append(relative.as_posix())
            if len(relative.parts) > int(limits["max_depth"]):
                too_deep.append(relative.as_posix())
    if total > int(limits["max_files"]):
        problems.append(f"package has {total} files, above the limit of {limits['max_files']}")
    if temp_hits:
        problems.append("temporary files present: " + ", ".join(sorted(set(temp_hits))[:10]))
    if too_deep:
        problems.append("paths exceed the configured depth: " + ", ".join(sorted(set(too_deep))[:5]))
    checks.append(
        {
            "check": "delivery",
            "status": "pass" if not problems else "repair",
            "detail": f"{total} file(s) scanned for temp artifacts, depth, and size limits",
            "problems": problems,
            "next_action": "Run 'cleanup --approve cleanup' after reviewing the proposal",
        }
    )

    failed = [c for c in checks if c["status"] == "fail"]
    blocked = [c for c in checks if c["status"] == "blocked"]
    repair = [c for c in checks if c["status"] == "repair"]
    overall = "blocked" if blocked else ("fail" if failed else ("repair" if repair else "pass"))
    return {
        "status": overall,
        "checks": checks,
        "counts": {"pass": sum(1 for c in checks if c["status"] == "pass"), "warn": sum(1 for c in checks if c["status"] == "warn"), "fail": len(failed), "blocked": len(blocked), "repair": len(repair)},
        "next_action": {
            "pass": "Package is ready for delivery",
            "fail": "Repair the failing checks before claiming completion",
            "blocked": "Stop and recover the missing prerequisites",
            "repair": "Repair the listed items or disclose them to the user",
        }[overall],
    }


# ---------------------------------------------------------------------------
# Watch, cleanup and recovery helpers
# ---------------------------------------------------------------------------


def fingerprint(root: Path, *, limit: int = 20000) -> Tuple[Tuple[str, int, int], ...]:
    entries: List[Tuple[str, int, int]] = []
    if not root.exists():
        raise ToolError(f"Workspace root not found: {root}", code="root-missing", exit_code=EXIT_MISSING)
    for count, path in enumerate(sorted(root.rglob("*"))):
        if count > limit:
            raise ToolError(
                f"Watch limit of {limit} entries exceeded",
                code="watch-limit",
                exit_code=EXIT_LIMIT,
            )
        if path.is_file():
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.relative_to(root).as_posix(), stat.st_size, int(stat.st_mtime)))
    return tuple(entries)


def watch(root: Path, *, settle_seconds: float, timeout_seconds: float, poll_seconds: float = 0.25) -> Dict[str, Any]:
    if settle_seconds < 0 or timeout_seconds <= 0:
        raise ToolError("settle must be >= 0 and timeout must be > 0", code="input-invalid")
    start = time.monotonic()
    previous = fingerprint(root)
    stable_since = time.monotonic()
    polls = 0
    while True:
        time.sleep(poll_seconds)
        polls += 1
        current = fingerprint(root)
        now = time.monotonic()
        if current != previous:
            previous = current
            stable_since = now
        elapsed = now - start
        if now - stable_since >= settle_seconds:
            return {
                "status": "settled",
                "stable_for_seconds": round(now - stable_since, 2),
                "elapsed_seconds": round(elapsed, 2),
                "polls": polls,
                "entries": len(current),
            }
        if elapsed >= timeout_seconds:
            return {
                "status": "timeout",
                "stable_for_seconds": round(now - stable_since, 2),
                "elapsed_seconds": round(elapsed, 2),
                "polls": polls,
                "entries": len(current),
                "next_action": "Writes are still in progress; rerun watch or raise --timeout",
            }


def find_temp_files(root: Path, *, patterns: Sequence[str] = core.TEMP_PATTERNS, limit: int = 500) -> List[str]:
    if not root.exists() or not root.is_dir():
        raise ToolError(f"Workspace root not found: {root}", code="root-missing", exit_code=EXIT_MISSING)
    hits: List[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in core.TEMP_DIR_NAMES for part in relative.parts):
            hits.append(relative.as_posix())
            continue
        if path.is_file() and any(path.match(pattern) for pattern in patterns):
            hits.append(relative.as_posix())
        if len(hits) > limit:
            raise ToolError(
                f"Cleanup proposal exceeds the limit of {limit} entries",
                code="cleanup-limit",
                exit_code=EXIT_LIMIT,
                hint="Narrow the root or pass a specific --pattern.",
            )
    return hits


def cleanup(
    root: Path,
    *,
    patterns: Sequence[str],
    approve: Optional[str],
    policy: Dict[str, Any],
    backup_dir: Optional[Path] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    hits = find_temp_files(root, patterns=patterns, limit=limit)
    allowed = policy.get("approval_required_actions", [])
    proposal = {
        "root": root.as_posix(),
        "patterns": list(patterns),
        "candidates": hits,
        "candidate_count": len(hits),
        "approval_required": "cleanup",
        "dry_run": True,
    }
    if not hits:
        proposal.update({"status": "nothing-to-do", "applied": False})
        return proposal
    if approve is None:
        proposal.update(
            {
                "status": "approval-required",
                "applied": False,
                "message": "No files were removed. Re-run with --approve cleanup after reviewing the candidates.",
            }
        )
        return proposal
    if approve not in allowed:
        raise ToolError(
            f"Approval token '{approve}' is not an approved action",
            code="approval-invalid",
            exit_code=EXIT_APPROVAL,
            details={"allowed": allowed},
        )
    if approve != "cleanup":
        raise ToolError(
            "Temp cleanup requires the 'cleanup' approval token",
            code="approval-mismatch",
            exit_code=EXIT_APPROVAL,
        )

    removed: List[str] = []
    restored: List[str] = []
    resolved_root = root.resolve()
    for relative in hits:
        target = (resolved_root / relative).resolve()
        if target != resolved_root and resolved_root not in target.parents:
            raise ToolError(
                f"Refusing to touch a path outside the root: {relative}",
                code="cleanup-escape",
                exit_code=EXIT_APPROVAL,
            )
        try:
            if backup_dir is not None:
                destination = backup_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                target.replace(destination)
                restored.append(destination.as_posix())
            elif target.is_dir():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                target.rmdir()
            else:
                target.unlink()
            removed.append(relative)
        except OSError as exc:
            raise ToolError(
                f"Cleanup failed on {relative}: {exc}",
                code="cleanup-failed",
                exit_code=EXIT_PARTIAL,
                details={"removed": removed, "failed": relative},
            ) from exc
    proposal.update(
        {
            "status": "applied",
            "dry_run": False,
            "applied_count": len(removed),
            "removed": removed,
            "backed_up": restored,
        }
    )
    return proposal


def record_repair_attempt(state: Dict[str, Any], *, stage_id: str, cause: str, action: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    """Increment and bound repair attempts for one stage. Mutates the state copy."""
    limit = int(policy["recovery"]["max_repair_attempts"])
    stage = next((s for s in state.get("stages", []) if s["id"] == stage_id), None)
    if stage is None:
        raise ToolError(
            f"Unknown stage: {stage_id}",
            code="stage-unknown",
            exit_code=EXIT_USAGE,
            details={"stages": [s["id"] for s in state.get("stages", [])]},
        )
    stage["attempts"] = int(stage.get("attempts", 0)) + 1
    if stage["attempts"] > limit:
        raise ToolError(
            f"Stage {stage_id} exceeded {limit} repair attempts; stop and escalate",
            code="repair-limit",
            exit_code=EXIT_PARTIAL,
            hint="Preserve successful work, request a decision, and do not repeat the unchanged failed action.",
            details={"attempts": stage["attempts"], "limit": limit, "last_cause": cause},
        )
    backoff = policy["recovery"].get("backoff_seconds", [0.5])
    return {
        "stage": stage_id,
        "attempts": stage["attempts"],
        "limit": limit,
        "remaining": limit - stage["attempts"],
        "cause": cause,
        "action": action,
        "backoff_seconds": backoff[min(stage["attempts"] - 1, len(backoff) - 1)] if backoff else 0,
    }


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-skill-validator.py",
        description="Verification and failure-recovery helpers for the powerful-work-splitter Skill Tool.",
        epilog="Run 'python3 tools-work-splitter.py capabilities' for the capability list.",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    sub = parser.add_subparsers(dest="command")

    assert_parser = sub.add_parser("assert", help="assert the state of one artifact")
    assert_parser.add_argument("--kind", required=True, choices=["file", "csv", "json", "manifest", "reference"])
    assert_parser.add_argument("--path", required=True)
    assert_parser.add_argument("--root", default=None)
    assert_parser.add_argument("--min-bytes", type=int, default=0)
    assert_parser.add_argument("--signature", action="store_true")
    assert_parser.add_argument("--expected-ext", default=None)
    assert_parser.add_argument("--require-header", action="append", default=[])
    assert_parser.add_argument("--min-rows", type=int, default=1)
    assert_parser.add_argument("--expect-rows", type=int, default=None)
    assert_parser.add_argument("--schema", default=None)
    assert_parser.add_argument("--require-key", action="append", default=[])

    verify_parser = sub.add_parser("verify", help="run the verification gate set")
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--state", default=None)
    verify_parser.add_argument("--root", required=True)
    verify_parser.add_argument("--policy", default=None)
    verify_parser.add_argument("--allow-missing-artifacts", action="store_true")

    watch_parser = sub.add_parser("watch", help="wait until workspace writes settle")
    watch_parser.add_argument("--root", required=True)
    watch_parser.add_argument("--settle", type=float, default=1.0)
    watch_parser.add_argument("--timeout", type=float, default=30.0)
    watch_parser.add_argument("--poll", type=float, default=0.25)

    cleanup_parser = sub.add_parser("cleanup", help="propose or apply temp-file cleanup")
    cleanup_parser.add_argument("--root", required=True)
    cleanup_parser.add_argument("--pattern", action="append", default=[])
    cleanup_parser.add_argument("--approve", default=None)
    cleanup_parser.add_argument("--backup", default=None)
    cleanup_parser.add_argument("--policy", default=None)
    return parser


def emit(payload: Dict[str, Any], *, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for line in render_text(payload):
            print(line)


def render_text(payload: Dict[str, Any]) -> List[str]:
    if "status" in payload and "checks" in payload:
        lines = [f"Verification: {payload['status'].upper()}"]
        for check in payload["checks"]:
            lines.append(f"  [{check['status']:<7}] {check['check']}: {check['detail']}")
            for problem in check.get("problems", []):
                lines.append(f"            - {problem}")
        lines.append(f"Next action: {payload['next_action']}")
        return lines
    if "candidates" in payload:
        lines = [
            f"Cleanup proposal on {payload['root']}",
            f"Candidates: {payload['candidate_count']}",
        ]
        for candidate in payload["candidates"][:25]:
            lines.append(f"  - {candidate}")
        lines.append(f"Status: {payload['status']}")
        if payload.get("message"):
            lines.append(payload["message"])
        return lines
    return [json.dumps(payload, indent=2, ensure_ascii=False)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    try:
        if args.command == "assert":
            payload = run_assert(
                args.kind,
                Path(args.path),
                min_bytes=args.min_bytes,
                signature=args.signature,
                expected_ext=args.expected_ext,
                require_header=args.require_header,
                min_rows=args.min_rows,
                expect_rows=args.expect_rows,
                schema_path=Path(args.schema) if args.schema else None,
                require_keys=args.require_key,
                root=Path(args.root) if args.root else None,
            )
            emit({"ok": payload["status"] == "pass", "command": "assert", "data": payload}, fmt=args.format)
            return EXIT_OK if payload["status"] == "pass" else EXIT_GATE
        if args.command == "verify":
            policy = core.load_policy(args.policy)
            plan = core.load_json_file(Path(args.plan), label="Plan")
            state = core.load_json_file(Path(args.state), label="Checkpoint state") if args.state else None
            payload = verify_package(
                plan=plan,
                state=state,
                root=Path(args.root),
                policy=policy,
                require_artifacts=not args.allow_missing_artifacts,
            )
            emit({"ok": payload["status"] in ("pass", "repair"), "command": "verify", "data": payload}, fmt=args.format)
            return EXIT_OK if payload["status"] == "pass" else EXIT_GATE
        if args.command == "watch":
            payload = watch(Path(args.root), settle_seconds=args.settle, timeout_seconds=args.timeout, poll_seconds=args.poll)
            emit({"ok": payload["status"] == "settled", "command": "watch", "data": payload}, fmt=args.format)
            return EXIT_OK if payload["status"] == "settled" else EXIT_PARTIAL
        if args.command == "cleanup":
            policy = core.load_policy(args.policy)
            patterns = args.pattern or list(core.TEMP_PATTERNS)
            payload = cleanup(
                Path(args.root),
                patterns=patterns,
                approve=args.approve,
                policy=policy,
                backup_dir=Path(args.backup) if args.backup else None,
            )
            emit({"ok": payload["status"] in ("applied", "nothing-to-do"), "command": "cleanup", "data": payload}, fmt=args.format)
            return EXIT_OK if payload["status"] in ("applied", "nothing-to-do") else EXIT_APPROVAL
    except ToolError as exc:
        emit(exc.as_dict(), fmt="json" if args.format == "json" or args.format == "text" else "json")
        return exc.exit_code
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
