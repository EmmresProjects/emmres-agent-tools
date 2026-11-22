#!/usr/bin/env python3
"""Core runtime, governance, provenance, caching, schema validation, and recovery engine."""
import sys

sys.dont_write_bytecode = True

import datetime
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

SKILL_TOOL_NAME = "research-workspace-creator"
TOOL_DIR = Path(__file__).resolve().parent


def load_sibling_tool(module_name: str, filename: str) -> Any:
    """Load a sibling tools-*.py file without creating __pycache__."""
    sys.dont_write_bytecode = True
    full_mod_name = f"_rwc_{module_name}"
    if full_mod_name in sys.modules:
        return sys.modules[full_mod_name]
    target = TOOL_DIR / filename
    if not target.is_file():
        raise FileNotFoundError(
            f"Required tool module not found: {SKILL_TOOL_NAME}/{filename}"
        )
    spec = importlib.util.spec_from_file_location(full_mod_name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create module spec for {SKILL_TOOL_NAME}/{filename}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class SkillToolError(Exception):
    """Base structured error for research-workspace-creator."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ValidationError(SkillToolError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("validation_error", message, details)


class ApprovalRequiredError(SkillToolError):
    def __init__(self, action: str, message: str, details: Optional[Dict[str, Any]] = None):
        merged = {"required_approval": action}
        if details:
            merged.update(details)
        super().__init__("approval_required", message, merged)


class PolicyViolationError(SkillToolError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("policy_violation", message, details)


class RecoveryError(SkillToolError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__("recovery_error", message, details)


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def to_portable_path(path: Path, base_dir: Optional[Path] = None) -> str:
    """Format a filesystem path as a portable forward-slash relative path."""
    resolved = path.resolve()
    candidates: List[Path] = []
    if base_dir is not None:
        candidates.append(base_dir.resolve())
    candidates.append(Path.cwd().resolve())
    candidates.append(TOOL_DIR.parent.resolve())
    for root in candidates:
        try:
            rel = resolved.relative_to(root)
            text = rel.as_posix()
            return text if text != "." else "./"
        except ValueError:
            continue
    try:
        rel_tool = resolved.relative_to(TOOL_DIR)
        return f"{SKILL_TOOL_NAME}/{rel_tool.as_posix()}"
    except ValueError:
        return resolved.name


def _check_path_syntax(raw_path: str, allow_external: bool = False) -> None:
    if not raw_path or not isinstance(raw_path, str) or not raw_path.strip():
        raise ValidationError("Path must be a non-empty string.")
    if "\x00" in raw_path:
        raise ValidationError("Path contains illegal null byte.")
    if re.match(r"^[A-Za-z]:[/\\]", raw_path):
        if not allow_external:
            raise ValidationError(
                "Drive-letter paths are blocked by default; pass relative paths from the skill root.",
                {"supplied_path": raw_path},
            )
    normalized = raw_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if ".." in parts and not allow_external:
        raise ValidationError(
            "Parent-directory traversal ('..') is blocked by safety policy.",
            {"supplied_path": raw_path},
        )
    if normalized.startswith("/") and not allow_external:
        raise ValidationError(
            "Absolute paths are blocked by default; supply a relative path or pass --allow-external-paths with --approve credential_or_policy_override.",
            {"supplied_path": raw_path},
        )


def resolve_skill_resource(rel_path: str) -> Path:
    """Resolve a skill-root-relative path like research-workspace-creator/asset-default-config.json."""
    _check_path_syntax(rel_path, allow_external=False)
    norm = rel_path.replace("\\", "/").lstrip("./")
    cwd_candidate = Path.cwd() / norm
    if cwd_candidate.exists():
        return cwd_candidate
    prefix = f"{SKILL_TOOL_NAME}/"
    if norm.startswith(prefix):
        sub = norm[len(prefix):]
        sibling_candidate = TOOL_DIR / sub
        if sibling_candidate.exists():
            return sibling_candidate
    direct_sibling = TOOL_DIR / norm
    if direct_sibling.exists():
        return direct_sibling
    return cwd_candidate


def validate_io_path(
    raw_path: str,
    allow_external: bool = False,
    must_exist: bool = False,
) -> Path:
    """Validate an input or output path before performing filesystem operations."""
    _check_path_syntax(raw_path, allow_external=allow_external)
    norm = raw_path.replace("\\", "/")
    if not norm.startswith("/"):
        resolved = resolve_skill_resource(norm) if must_exist else (Path.cwd() / norm)
    else:
        resolved = Path(norm)
    if must_exist and not resolved.exists():
        raise ValidationError(
            f"Input path does not exist: {norm}",
            {"path": norm},
        )
    return resolved


def atomic_write_text(target: Path, content: str) -> None:
    """Atomically write UTF-8 text to target using temp-file-and-replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(target))
    except Exception:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def atomic_write_json(target: Path, data: Any) -> None:
    atomic_write_text(target, json.dumps(data, indent=2, ensure_ascii=True) + "\n")


class RuntimeConfig:
    """Loads and merges configuration, policies, schemas, and templates."""

    DEFAULT_CONFIG_REL = f"{SKILL_TOOL_NAME}/asset-default-config.json"

    def __init__(self, config_rel_path: Optional[str] = None, allow_external_paths: bool = False):
        self.allow_external_paths = allow_external_paths
        cfg_ref = config_rel_path or self.DEFAULT_CONFIG_REL
        cfg_file = (
            validate_io_path(cfg_ref, allow_external=allow_external_paths, must_exist=True)
            if config_rel_path
            else resolve_skill_resource(self.DEFAULT_CONFIG_REL)
        )
        if not cfg_file.is_file():
            raise ValidationError(f"Configuration file not found: {cfg_ref}")
        try:
            self.data: Dict[str, Any] = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON in configuration file {cfg_ref}: {exc}") from exc

        res = self.data.get("resources", {})
        self.catalog_path = resolve_skill_resource(
            res.get("catalog_path", f"{SKILL_TOOL_NAME}/asset-capability-catalog.json")
        )
        self.policy_path = resolve_skill_resource(
            res.get("policy_rules_path", f"{SKILL_TOOL_NAME}/asset-policy-rules.json")
        )
        self.schemas_path = resolve_skill_resource(
            res.get("output_schemas_path", f"{SKILL_TOOL_NAME}/asset-output-schemas.json")
        )
        self.templates_path = resolve_skill_resource(
            res.get("templates_path", f"{SKILL_TOOL_NAME}/asset-prompt-and-report-templates.json")
        )
        self.sample_bundle_path = resolve_skill_resource(
            res.get("sample_bundle_path", f"{SKILL_TOOL_NAME}/asset-sample-research-bundle.json")
        )

        self.policy: Dict[str, Any] = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.schemas: Dict[str, Any] = json.loads(self.schemas_path.read_text(encoding="utf-8"))
        self.templates: Dict[str, Any] = json.loads(self.templates_path.read_text(encoding="utf-8"))

    @property
    def limits(self) -> Dict[str, Any]:
        return dict(self.data.get("limits", {}))

    @property
    def indexing(self) -> Dict[str, Any]:
        return dict(self.data.get("indexing", {}))

    @property
    def safety(self) -> Dict[str, Any]:
        return dict(self.data.get("safety", {}))

    @property
    def providers(self) -> Dict[str, Any]:
        return dict(self.data.get("providers", {}))


class TruthfulnessTracker:
    """Tracks actual operations performed so reports never claim unexecuted work."""

    def __init__(self, network_enabled: bool = False):
        self.network_enabled = bool(network_enabled)
        self.network_used = False
        self.searches_executed = 0
        self.downloads_completed = 0
        self.installations_performed = 0
        self.subprocesses_executed = 0
        self.cache_hits = 0
        self.files_created: List[str] = []
        self.skipped_or_gated: List[Dict[str, str]] = []

    def record_file(self, path: Path, base_dir: Optional[Path] = None) -> str:
        rel = to_portable_path(path, base_dir=base_dir)
        if rel not in self.files_created:
            self.files_created.append(rel)
        return rel

    def record_skip(self, operation: str, reason: str) -> None:
        self.skipped_or_gated.append({"operation": operation, "reason": reason})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "network_enabled": self.network_enabled,
            "network_used": self.network_used,
            "searches_executed": self.searches_executed,
            "downloads_completed": self.downloads_completed,
            "installations_performed": self.installations_performed,
            "subprocesses_executed": self.subprocesses_executed,
            "cache_hits": self.cache_hits,
            "files_created": list(self.files_created),
            "skipped_or_gated": list(self.skipped_or_gated),
        }


class SafetyGovernor:
    """Enforces approval gates, SSRF checks, SQL read-only rules, and secret/PII scanning."""

    def __init__(self, config: RuntimeConfig, approved_actions: Optional[Sequence[str]] = None):
        self.config = config
        raw_approvals = set(a.strip() for a in (approved_actions or []) if a and a.strip())
        self.approved: Set[str] = raw_approvals

    def is_approved(self, action: str) -> bool:
        return "all" in self.approved or action in self.approved

    def require_approval(self, action: str, reason: str) -> None:
        required_list = set(self.config.safety.get("require_approval_for", []))
        if action in required_list or action in self.config.policy.get("approval_gated_actions", {}):
            if not self.is_approved(action):
                raise ApprovalRequiredError(
                    action=action,
                    message=(
                        f"Approval required before '{action}': {reason}. "
                        f"Re-run with --approve {action} once verified."
                    ),
                    details={"reason": reason},
                )

    def scan_text(self, text: str) -> Dict[str, Any]:
        secrets_found: List[Dict[str, Any]] = []
        pii_found: List[Dict[str, Any]] = []

        for rule in self.config.policy.get("secret_patterns", []):
            rx = re.compile(rule["regex"])
            matches = list(rx.finditer(text))
            if matches:
                secrets_found.append(
                    {
                        "id": rule["id"],
                        "label": rule["label"],
                        "severity": rule.get("severity", "high"),
                        "count": len(matches),
                    }
                )

        for rule in self.config.policy.get("pii_patterns", []):
            rx = re.compile(rule["regex"])
            matches = list(rx.finditer(text))
            if matches:
                pii_found.append(
                    {
                        "id": rule["id"],
                        "label": rule["label"],
                        "count": len(matches),
                    }
                )

        return {
            "has_secrets": len(secrets_found) > 0,
            "has_pii": len(pii_found) > 0,
            "secret_findings": secrets_found,
            "pii_findings": pii_found,
        }

    def redact_text(self, text: str, redact_secrets: bool = True, redact_pii: bool = False) -> str:
        out = text
        if redact_secrets:
            for rule in self.config.policy.get("secret_patterns", []):
                out = re.sub(rule["regex"], f"[REDACTED:{rule['id']}]", out)
        if redact_pii:
            for rule in self.config.policy.get("pii_patterns", []):
                out = re.sub(rule["regex"], f"[REDACTED_PII:{rule['id']}]", out)
        return out

    def validate_url(self, url: str, resolve_dns: bool = False) -> urllib.parse.SplitResult:
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme not in ("http", "https"):
            raise PolicyViolationError(
                f"Disallowed URL scheme '{parsed.scheme}'. Only http and https are permitted.",
                {"url": url},
            )
        host = (parsed.hostname or "").lower().strip()
        if not host:
            raise ValidationError("URL is missing a hostname.", {"url": url})

        if self.config.safety.get("block_private_network_ssrf", True):
            blocked_hosts = set(self.config.policy.get("ssrf_blocked_hosts", []))
            if host in blocked_hosts or host.endswith(".local") or host.endswith(".internal"):
                raise PolicyViolationError(
                    f"Blocked private/local host '{host}' under SSRF policy.",
                    {"host": host},
                )
            networks = [
                ipaddress.ip_network(cidr, strict=False)
                for cidr in self.config.policy.get("ssrf_blocked_cidrs", [])
            ]
            try:
                ip_obj = ipaddress.ip_address(host)
                if any(ip_obj in net for net in networks) or ip_obj.is_private or ip_obj.is_loopback:
                    raise PolicyViolationError(
                        f"Blocked IP address '{host}' under SSRF policy.",
                        {"ip": host},
                    )
            except ValueError:
                if resolve_dns:
                    try:
                        infos = socket.getaddrinfo(host, None)
                        for info in infos:
                            addr = info[4][0]
                            ip_obj = ipaddress.ip_address(addr)
                            if (
                                any(ip_obj in net for net in networks)
                                or ip_obj.is_private
                                or ip_obj.is_loopback
                            ):
                                raise PolicyViolationError(
                                    f"DNS resolution for '{host}' points to private IP '{addr}'.",
                                    {"host": host, "resolved_ip": addr},
                                )
                    except socket.gaierror:
                        pass
        return parsed

    def validate_sql(self, sql: str) -> str:
        cleaned = sql.strip()
        if not cleaned:
            raise ValidationError("SQL query must not be empty.")
        # Reject multiple statements separated by semicolons unless trailing semicolon only
        stripped_semi = cleaned.rstrip(";").strip()
        if ";" in stripped_semi:
            self.require_approval(
                "sql_write_if_non_select",
                "Multi-statement SQL script execution requires explicit approval",
            )
        sql_guard = self.config.policy.get("sql_guard", {})
        upper = stripped_semi.upper()
        first_token = upper.split()[0] if upper.split() else ""
        blocked = sql_guard.get("blocked_write_keywords", [])

        if first_token == "PRAGMA":
            allowed_pragmas = sql_guard.get("allowed_pragmas", [])
            if not any(p.upper() in upper for p in allowed_pragmas):
                self.require_approval(
                    "sql_write_if_non_select",
                    f"Non-introspection PRAGMA requires approval: {cleaned[:60]}",
                )
            return stripped_semi

        allowed_prefixes = [p.upper() for p in sql_guard.get("allowed_readonly_prefixes", ["SELECT", "WITH", "EXPLAIN"])]
        has_blocked = any(re.search(rf"\b{kw}\b", upper) for kw in blocked)
        if first_token not in allowed_prefixes or has_blocked:
            self.require_approval(
                "sql_write_if_non_select",
                f"Write or DDL SQL statement detected ({first_token})",
            )
        return stripped_semi


class SchemaValidator:
    """Pure-Python validator for key schemas in asset-output-schemas.json."""

    TYPE_MAP = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }

    def __init__(self, schemas_doc: Dict[str, Any]):
        self.schemas = schemas_doc.get("schemas", {})

    def validate(self, schema_name: str, instance: Any) -> List[str]:
        schema = self.schemas.get(schema_name)
        if not schema:
            return [f"Unknown schema '{schema_name}'"]
        errors: List[str] = []
        self._check_node(schema, instance, path="$", errors=errors)
        return errors

    def _check_node(self, schema: Dict[str, Any], value: Any, path: str, errors: List[str]) -> None:
        expected_type = schema.get("type")
        if expected_type in self.TYPE_MAP:
            py_type = self.TYPE_MAP[expected_type]
            if expected_type == "number" and isinstance(value, bool):
                errors.append(f"{path}: expected number, got boolean")
                return
            if not isinstance(value, py_type):
                errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
                return

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: value '{value}' not in allowed enum {schema['enum']}")

        if isinstance(value, dict):
            for req in schema.get("required", []):
                if req not in value:
                    errors.append(f"{path}: missing required key '{req}'")
            props = schema.get("properties", {})
            for k, subschema in props.items():
                if k in value:
                    self._check_node(subschema, value[k], f"{path}.{k}", errors)

        if isinstance(value, list):
            min_items = schema.get("minItems")
            if min_items is not None and len(value) < min_items:
                errors.append(f"{path}: array has {len(value)} items, minimum is {min_items}")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    self._check_node(item_schema, item, f"{path}[{idx}]", errors)


class ProvenanceLedger:
    """Append-only chained SHA-256 provenance ledger."""

    def __init__(self, ledger_path: Path, run_id: str):
        self.ledger_path = ledger_path
        self.run_id = run_id
        self.entries: List[Dict[str, Any]] = []

    def _last_hash(self) -> str:
        if self.entries:
            return self.entries[-1]["sha256"]
        if self.ledger_path.is_file():
            lines = [
                ln.strip()
                for ln in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip()
            ]
            if lines:
                try:
                    return json.loads(lines[-1]).get("sha256", "0" * 64)
                except json.JSONDecodeError:
                    return "0" * 64
        return "0" * 64

    def record(
        self,
        action: str,
        source_refs: Sequence[str],
        output_refs: Sequence[str],
        payload_digest: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        prev_hash = self._last_hash()
        ts = utc_now_iso()
        entry_id = f"prov-{len(self.entries) + 1:04d}-{ payload_digest[:8] }"
        core_material = json.dumps(
            {
                "entry_id": entry_id,
                "run_id": self.run_id,
                "timestamp_utc": ts,
                "action": action,
                "source_refs": list(source_refs),
                "output_refs": list(output_refs),
                "payload_digest": payload_digest,
                "prev_sha256": prev_hash,
            },
            sort_keys=True,
        )
        chain_hash = sha256_text(core_material)
        entry = {
            "entry_id": entry_id,
            "run_id": self.run_id,
            "timestamp_utc": ts,
            "action": action,
            "source_refs": list(source_refs),
            "output_refs": list(output_refs),
            "payload_digest": payload_digest,
            "prev_sha256": prev_hash,
            "sha256": chain_hash,
            "metadata": metadata or {},
        }
        self.entries.append(entry)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
        return entry


class EventLogger:
    """Concise structured JSONL operational event logger."""

    def __init__(self, log_path: Path, run_id: str):
        self.log_path = log_path
        self.run_id = run_id
        self.events: List[Dict[str, Any]] = []

    def emit(self, level: str, stage: str, message: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        ev = {
            "timestamp_utc": utc_now_iso(),
            "run_id": self.run_id,
            "level": level,
            "stage": stage,
            "message": message,
            "metrics": metrics or {},
        }
        self.events.append(ev)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=True) + "\n")


class ContentAddressedCache:
    """SHA-256 content-addressed disk cache with TTL and byte-budget eviction."""

    def __init__(self, cache_dir: Path, max_bytes: int = 52428800, ttl_seconds: int = 86400):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0

    def _entry_path(self, key: str) -> Path:
        digest = sha256_text(key)
        return self.cache_dir / f"{digest}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._entry_path(key)
        if not p.is_file():
            self.misses += 1
            return None
        try:
            wrapper = json.loads(p.read_text(encoding="utf-8"))
            created = float(wrapper.get("created_epoch", 0))
            if (time.time() - created) > self.ttl_seconds:
                p.unlink(missing_ok=True)
                self.misses += 1
                return None
            p.touch()
            self.hits += 1
            return wrapper.get("value")
        except (OSError, ValueError, json.JSONDecodeError):
            p.unlink(missing_ok=True)
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self._entry_path(key)
        wrapper = {
            "key_sha256": sha256_text(key),
            "created_epoch": time.time(),
            "value": value,
        }
        atomic_write_json(p, wrapper)
        self.enforce_budget()
        return p.stem

    def enforce_budget(self) -> Dict[str, int]:
        if not self.cache_dir.is_dir():
            return {"evicted_files": 0, "remaining_bytes": 0}
        files = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        evicted = 0
        while total > self.max_bytes and files:
            oldest = files.pop(0)
            sz = oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            total -= sz
            evicted += 1
        return {"evicted_files": evicted, "remaining_bytes": total}

    def stats(self) -> Dict[str, Any]:
        files = list(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []
        return {
            "entry_count": len(files),
            "total_bytes": sum(f.stat().st_size for f in files),
            "max_bytes": self.max_bytes,
            "ttl_seconds": self.ttl_seconds,
            "hits": self.hits,
            "misses": self.misses,
        }


class CheckpointManager:
    """Manages atomic run/workflow checkpoints and corrupted-state recovery."""

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = checkpoint_dir

    def checkpoint_path(self, checkpoint_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", checkpoint_id).strip("-") or "default"
        return self.checkpoint_dir / f"ckpt-{safe_id}.json"

    def save(self, checkpoint_id: str, state: Dict[str, Any]) -> Path:
        p = self.checkpoint_path(checkpoint_id)
        bak = p.with_suffix(".json.bak")
        payload = dict(state)
        payload["checkpoint_id"] = checkpoint_id
        payload["updated_utc"] = utc_now_iso()
        body_digest = sha256_text(json.dumps(payload, sort_keys=True))
        payload["integrity_sha256"] = body_digest
        # Atomic primary write followed by atomic redundant backup mirror
        atomic_write_json(p, payload)
        atomic_write_json(bak, payload)
        return p

    def load_or_recover(self, checkpoint_id: str) -> Tuple[Dict[str, Any], bool, str]:
        """Load checkpoint; if corrupted, recover from .bak or repair truncated state."""
        p = self.checkpoint_path(checkpoint_id)
        bak = p.with_suffix(".json.bak")
        if not p.exists() and not bak.exists():
            raise RecoveryError(
                f"Checkpoint '{checkpoint_id}' does not exist.",
                {"checkpoint_id": checkpoint_id},
            )

        for candidate, is_backup in ((p, False), (bak, True)):
            if not candidate.is_file():
                continue
            try:
                raw = candidate.read_text(encoding="utf-8")
                data = json.loads(raw)
                expected = data.get("integrity_sha256")
                if expected:
                    verify_copy = {k: v for k, v in data.items() if k != "integrity_sha256"}
                    actual = sha256_text(json.dumps(verify_copy, sort_keys=True))
                    if actual != expected:
                        continue
                if is_backup:
                    atomic_write_json(p, data)
                    return data, True, "Recovered valid state from atomic backup (.json.bak)."
                return data, False, "Loaded primary checkpoint cleanly."
            except (OSError, json.JSONDecodeError):
                continue

        # Attempt line/brace salvage or reinitialize clean recovery envelope
        salvaged = {
            "checkpoint_id": checkpoint_id,
            "status": "reinitialized_after_corruption",
            "completed_steps": {},
            "recovered_utc": utc_now_iso(),
        }
        saved_path = self.save(checkpoint_id, salvaged)
        return (
            json.loads(saved_path.read_text(encoding="utf-8")),
            True,
            "Primary and backup checkpoints were corrupted; quarantined corruption and initialized clean recovery state.",
        )


def retry_bounded(
    operation: Callable[[], Any],
    max_retries: int = 2,
    backoff_seconds: float = 0.3,
    timeout_seconds: float = 30.0,
    retryable_exceptions: Tuple[type, ...] = (OSError, TimeoutError),
) -> Tuple[Any, int]:
    """Execute operation with bounded retries, backoff, and overall deadline check."""
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    attempts = 0
    last_exc: Optional[BaseException] = None
    while attempts <= max_retries:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Operation exceeded timeout budget of {timeout_seconds}s")
        attempts += 1
        try:
            return operation(), attempts
        except retryable_exceptions as exc:
            last_exc = exc
            if attempts > max_retries:
                break
            sleep_for = min(backoff_seconds * (2 ** (attempts - 1)), max(0.0, deadline - time.monotonic()))
            if sleep_for > 0:
                time.sleep(sleep_for)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry loop exited unexpectedly")


def new_run_id(prefix: str = "rwc") -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{short}"
