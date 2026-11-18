#!/usr/bin/env python3
"""Shared runtime, configuration, safety approval gate, checkpoint recovery, and structured I/O."""
import sys
sys.dont_write_bytecode = True

import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL_TOOL_NAME = "software-engineering"
TOOL_DIR = Path(__file__).resolve().parent
_LOADED_MODULES: Dict[str, Any] = {}
_ACTIVE_TEMP_DIRS: List[Path] = []
_CANCELLED = False


class WorkbenchError(Exception):
    """Structured error raised during input validation, policy gating, or execution."""

    def __init__(self, code: str, message: str, exit_code: int = 2, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}


def _handle_termination(signum: int, _frame: Any) -> None:
    global _CANCELLED
    _CANCELLED = True
    cleanup_temp_dirs()
    raise WorkbenchError(
        "CANCELLED",
        f"Execution cancelled by signal {signum}; temporary resources were cleaned up.",
        exit_code=130,
    )


def install_signal_handlers() -> None:
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_termination)
            except (ValueError, OSError):
                pass


def is_cancelled() -> bool:
    return _CANCELLED


def create_managed_temp_dir(prefix: str = "se-wb-") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    _ACTIVE_TEMP_DIRS.append(tmp)
    return tmp


def cleanup_temp_dirs() -> None:
    while _ACTIVE_TEMP_DIRS:
        tmp = _ACTIVE_TEMP_DIRS.pop()
        shutil.rmtree(tmp, ignore_errors=True)


def resolve_resource_path(rel_or_user_path: str) -> Path:
    """Resolve a path relative to the current skill root first, or within the Skill Tool folder."""
    if "\x00" in rel_or_user_path:
        raise WorkbenchError("INVALID_PATH", "Path must not contain null bytes.")
    candidate = Path(rel_or_user_path)
    if candidate.exists():
        return candidate
    prefix = f"{SKILL_TOOL_NAME}/"
    if rel_or_user_path.startswith(prefix):
        inner_name = rel_or_user_path[len(prefix):]
        if "/" not in inner_name and "\\" not in inner_name:
            sibling = TOOL_DIR / inner_name
            if sibling.exists():
                return sibling
    sibling_direct = TOOL_DIR / rel_or_user_path
    if "/" not in rel_or_user_path and sibling_direct.exists():
        return sibling_direct
    return candidate


def load_sibling_tool(filename: str) -> Any:
    """Dynamically load a hyphen-named tools-*.py module without creating __pycache__."""
    if filename in _LOADED_MODULES:
        return _LOADED_MODULES[filename]
    target = resolve_resource_path(f"{SKILL_TOOL_NAME}/{filename}")
    if not target.is_file():
        raise WorkbenchError("MISSING_TOOL_MODULE", f"Required tool module not found: {SKILL_TOOL_NAME}/{filename}")
    mod_name = "se_wb_" + re.sub(r"[^a-z0-9]+", "_", filename.lower())
    spec = importlib.util.spec_from_file_location(mod_name, str(target))
    if spec is None or spec.loader is None:
        raise WorkbenchError("MODULE_LOAD_ERROR", f"Unable to create module spec for {SKILL_TOOL_NAME}/{filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _LOADED_MODULES[filename] = module
    return module


def load_json_resource(path_str: str) -> Dict[str, Any]:
    path = resolve_resource_path(path_str)
    if not path.is_file():
        raise WorkbenchError("MISSING_RESOURCE", f"Resource file does not exist: {path_str}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkbenchError("INVALID_JSON_RESOURCE", f"Invalid JSON in {path_str}: {exc}") from exc


def load_runtime_config(config_path: Optional[str] = None, policy_path: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cfg_ref = config_path or f"{SKILL_TOOL_NAME}/asset-default-config.json"
    cfg = load_json_resource(cfg_ref)
    pol_ref = policy_path or cfg.get("paths", {}).get("safety_policy", f"{SKILL_TOOL_NAME}/asset-safety-policy.json")
    policy = load_json_resource(pol_ref)

    # Allow safe environment overrides
    env_timeout = os.environ.get("SE_WORKBENCH_TIMEOUT")
    if env_timeout and env_timeout.isdigit():
        cfg.setdefault("limits", {})["command_timeout_seconds"] = max(1, min(600, int(env_timeout)))
    env_provider = os.environ.get("SE_WORKBENCH_PROVIDER")
    if env_provider:
        cfg.setdefault("providers", {})["provider_name"] = env_provider
    env_model = os.environ.get("SE_WORKBENCH_MODEL")
    if env_model:
        cfg.setdefault("providers", {})["model_name"] = env_model
    return cfg, policy


def validate_input_path(input_path: Optional[str], max_bytes: int) -> Optional[Path]:
    if not input_path:
        return None
    if "\x00" in input_path:
        raise WorkbenchError("INVALID_INPUT_PATH", "Input path contains forbidden null byte.")
    resolved = resolve_resource_path(input_path)
    if not resolved.exists():
        raise WorkbenchError("INPUT_NOT_FOUND", f"Input path does not exist: {input_path}")
    if resolved.is_file():
        size = resolved.stat().st_size
        if size > max_bytes:
            raise WorkbenchError(
                "INPUT_TOO_LARGE",
                f"Input file {input_path} ({size} bytes) exceeds configured max_input_bytes ({max_bytes}).",
            )
    return resolved


def validate_output_path(
    output_path: Optional[str],
    policy: Dict[str, Any],
    force_overwrite: bool = False,
    approved: bool = False,
) -> Optional[Path]:
    if not output_path:
        return None
    if "\x00" in output_path:
        raise WorkbenchError("INVALID_OUTPUT_PATH", "Output path contains forbidden null byte.")
    out = Path(output_path)
    abs_str = str(out.resolve())
    for forbidden_root in policy.get("path_confinement", {}).get("forbid_system_write_roots", []):
        if abs_str == forbidden_root or abs_str.startswith(forbidden_root + "/"):
            raise WorkbenchError(
                "FORBIDDEN_OUTPUT_PATH",
                f"Refusing to write inside protected system path: {forbidden_root}",
                exit_code=3,
            )
    sensitive_names = set(policy.get("path_confinement", {}).get("sensitive_filenames", []))
    if out.name in sensitive_names and not approved:
        raise WorkbenchError(
            "APPROVAL_REQUIRED",
            f"Writing to sensitive filename '{out.name}' requires --approve and --approval-reason.",
            exit_code=4,
        )
    if out.exists() and out.is_file() and force_overwrite:
        req_force = policy.get("approval_policy", {}).get("require_approval_for_force_overwrite", True)
        if req_force and not approved:
            raise WorkbenchError(
                "APPROVAL_REQUIRED",
                f"Overwriting existing file '{output_path}' with --force-overwrite requires --approve and --approval-reason.",
                exit_code=4,
            )
    return out


def enforce_approval_gate(
    skill_meta: Dict[str, Any],
    action_mode: str,
    approved: bool,
    approval_reason: str,
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Check whether execution requires explicit human approval and gate before work starts."""
    scope = skill_meta.get("approval_scope", "none")
    requires_on_apply = bool(skill_meta.get("requires_approval_on_apply", False))
    gated_scopes = set(policy.get("approval_policy", {}).get("require_approval_for_apply_scopes", []))

    needs_approval = (action_mode == "apply" and (requires_on_apply or scope in gated_scopes))
    if needs_approval and not approved:
        raise WorkbenchError(
            "APPROVAL_REQUIRED",
            (
                f"Skill '{skill_meta['slug']}' ({skill_meta['coordinate']}) in --action-mode apply touches "
                f"'{scope}' operations and requires explicit human approval (--approve --approval-reason \"...\"). "
                f"Use --action-mode plan (default) for safe non-destructive planning and verification."
            ),
            exit_code=4,
            details={
                "skill": skill_meta["slug"],
                "coordinate": skill_meta["coordinate"],
                "scope": scope,
                "action_mode": action_mode,
            },
        )
    if needs_approval and policy.get("approval_policy", {}).get("require_non_empty_reason", True):
        if not approval_reason or not approval_reason.strip():
            raise WorkbenchError(
                "APPROVAL_REASON_REQUIRED",
                f"Skill '{skill_meta['slug']}' requires a non-empty --approval-reason alongside --approve.",
                exit_code=4,
            )
    return {
        "required": needs_approval or requires_on_apply,
        "granted": bool(approved) if needs_approval else False,
        "scope": scope,
        "action_mode": action_mode,
        "reason": approval_reason.strip() if approval_reason else (
            "Safe read-only/plan mode; live mutation/publishing not executed." if requires_on_apply else "Not required for non-sensitive operation."
        ),
    }


def run_with_retries(
    fn: Callable[[], Any],
    max_retries: int = 2,
    backoff_seconds: float = 0.25,
    backoff_multiplier: float = 2.0,
) -> Tuple[Any, int]:
    """Execute an operation with bounded retries and cooperative cancellation checks."""
    attempt = 0
    delay = max(0.0, backoff_seconds)
    last_exc: Optional[Exception] = None
    while attempt <= max_retries:
        if is_cancelled():
            raise WorkbenchError("CANCELLED", "Operation cancelled before retry.", exit_code=130)
        try:
            return fn(), attempt
        except WorkbenchError:
            raise
        except Exception as exc:
            last_exc = exc
            attempt += 1
            if attempt > max_retries:
                break
            time.sleep(delay)
            delay *= backoff_multiplier
    raise WorkbenchError(
        "EXECUTION_FAILED_AFTER_RETRIES",
        f"Operation failed after {attempt} attempt(s): {last_exc}",
        exit_code=1,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_artifact(target_path: Path, content: bytes, kind: str) -> Dict[str, Any]:
    """Atomically write an output artifact and return structured provenance metadata."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".tmp-{uuid.uuid4().hex[:8]}-{target_path.name}"
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return {
        "path": target_path.as_posix(),
        "kind": kind,
        "bytes": len(content),
        "sha256": sha256_bytes(content),
    }


def redact_secrets(text: str, policy: Dict[str, Any], enabled: bool = True) -> Tuple[str, List[Dict[str, Any]]]:
    """Scan text for secret patterns and optionally redact matches."""
    findings: List[Dict[str, Any]] = []
    redacted = text
    for rule in policy.get("secret_patterns", []):
        pat = rule.get("pattern", "")
        if not pat:
            continue
        for m in re.finditer(pat, text):
            snippet = m.group(0)
            masked = snippet[:4] + "...[REDACTED]..." + snippet[-2:] if len(snippet) > 8 else "[REDACTED]"
            line_no = text.count("\n", 0, m.start()) + 1
            findings.append({
                "secret_id": rule["secret_id"],
                "title": rule["title"],
                "line": line_no,
                "redacted_preview": masked,
            })
        if enabled and findings:
            redacted = re.sub(pat, "[REDACTED-SECRET]", redacted)
    return redacted, findings


def save_checkpoint(
    checkpoint_dir: Path,
    run_id: str,
    workflow_name: str,
    completed_steps: Dict[str, Any],
    pending_steps: List[str],
    status: str = "in_progress",
) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cp_path = checkpoint_dir / "checkpoint-state.json"
    payload = {
        "run_id": run_id,
        "workflow": workflow_name,
        "status": status,
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "completed_steps": completed_steps,
        "pending_steps": pending_steps,
    }
    write_artifact(cp_path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"), "checkpoint-journal")
    return cp_path


def load_checkpoint(checkpoint_path: str) -> Dict[str, Any]:
    resolved = resolve_resource_path(checkpoint_path)
    if not resolved.is_file():
        raise WorkbenchError("CHECKPOINT_NOT_FOUND", f"Checkpoint file not found: {checkpoint_path}")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkbenchError("CORRUPT_CHECKPOINT", f"Checkpoint JSON is malformed: {exc}") from exc
    for req_key in ("run_id", "workflow", "completed_steps", "pending_steps"):
        if req_key not in data:
            raise WorkbenchError("INVALID_CHECKPOINT_SCHEMA", f"Checkpoint missing required field '{req_key}'.")
    return data


def build_envelope(
    *,
    status: str,
    command: str,
    start_time: float,
    inputs_validated: bool,
    approval: Optional[Dict[str, Any]] = None,
    results: Optional[Dict[str, Any]] = None,
    verification_checks: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    limitations: Optional[List[str]] = None,
    errors: Optional[List[Dict[str, str]]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    elapsed_ms = round(max(0.0, (time.perf_counter() - start_time) * 1000.0), 3)
    envelope = {
        "status": status,
        "run_id": run_id or f"run-{uuid.uuid4().hex[:12]}",
        "tool": SKILL_TOOL_NAME,
        "command": command,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "duration_ms": elapsed_ms,
        "inputs_validated": inputs_validated,
        "approval": approval or {
            "required": False,
            "granted": False,
            "scope": "none",
            "action_mode": "plan",
            "reason": "Not applicable",
        },
        "results": results or {},
        "verification_checks": verification_checks or [],
        "artifacts": artifacts or [],
        "limitations": limitations or [],
        "errors": errors or [],
    }
    validate_envelope_structure(envelope)
    return envelope


def validate_envelope_structure(envelope: Dict[str, Any]) -> None:
    """Validate runtime output envelope against asset-output-schema.json invariants."""
    required_keys = (
        "status", "run_id", "tool", "command", "timestamp_utc", "duration_ms",
        "inputs_validated", "approval", "results", "verification_checks",
        "artifacts", "limitations", "errors",
    )
    for k in required_keys:
        if k not in envelope:
            raise WorkbenchError("SCHEMA_VIOLATION", f"Output envelope missing required field: {k}")
    if envelope["status"] not in ("ok", "failed", "approval_required", "recovered"):
        raise WorkbenchError("SCHEMA_VIOLATION", f"Invalid envelope status: {envelope['status']}")
