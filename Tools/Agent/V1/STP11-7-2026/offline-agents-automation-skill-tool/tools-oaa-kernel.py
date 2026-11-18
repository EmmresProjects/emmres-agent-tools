#!/usr/bin/env python3
"""tools-oaa-kernel.py -- shared infrastructure for the offline-agents-automation Skill Tool.

This module is NOT a standalone CLI. It is loaded by `tools-oaa.py` at runtime through
`importlib` (filename-safe loading) and provides:

  * error taxonomy with stable machine-readable codes plus structured payloads
  * configuration loading/validation/merging (asset-default-config.json is the baseline)
  * workspace layout, lock file, path scoping
  * canonical hashing, tamper-evident audit chain (hash chain + HMAC seal)
  * policy engine (default-deny action allowlist, approval classes, roles)
  * expiring HMAC capability tokens
  * durable state store over the standard-library `sqlite3` module
    (key/value state, classified memories, idempotency keys, checkpoints,
     dead-letter queue, approval records)
  * model-provider adapter (deterministic local provider + external command provider)
  * bounded retry helper and JSON/JSONL I/O helpers

Standard library only. No network access is performed anywhere in this file.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import time
import uuid

VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

DEFAULT_CONFIG_FILENAME = "asset-default-config.json"
CATALOG_FILENAME = "asset-skill-catalog.json"
WORKFLOW_SCHEMA_FILENAME = "asset-workflow-schema.json"
POLICY_SAMPLE_FILENAME = "asset-policy-sample.json"
INJECTION_FIXTURES_FILENAME = "asset-injection-fixtures.json"
EVAL_DATASET_FILENAME = "asset-eval-dataset.jsonl"

# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class OAAError(Exception):
    """Base error with a stable code and a structured payload."""

    code = "OAA_ERROR"
    exit_code = 1
    retryable = False

    def __init__(self, message, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self):
        payload = {
            "ok": False,
            "error": {"code": self.code, "message": self.message,
                      "retryable": self.retryable},
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class InputError(OAAError):
    code = "INPUT_INVALID"
    exit_code = 2


class NotFoundError(OAAError):
    code = "NOT_FOUND"
    exit_code = 3


class PolicyError(OAAError):
    code = "POLICY_DENIED"
    exit_code = 4


class ApprovalRequired(OAAError):
    code = "APPROVAL_REQUIRED"
    exit_code = 5


class BudgetError(OAAError):
    code = "BUDGET_EXCEEDED"
    exit_code = 6


class StateError(OAAError):
    code = "STATE_INVALID"
    exit_code = 7


class TransientError(OAAError):
    code = "TRANSIENT"
    exit_code = 8
    retryable = True


class ProviderError(OAAError):
    code = "PROVIDER_FAILED"
    exit_code = 9
    retryable = True


class IntegrityError(OAAError):
    code = "INTEGRITY_FAILED"
    exit_code = 10


class CancelledError(OAAError):
    code = "CANCELLED"
    exit_code = 11


class WorkflowError(OAAError):
    code = "WORKFLOW_FAILED"
    exit_code = 12


# --------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------


def now_iso(ts=None):
    """UTC timestamp in ISO-8601 with a trailing Z (no local-time ambiguity)."""
    t = time.time() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def new_id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def canonical_json(obj):
    """Deterministic JSON used for hashing and signatures."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_obj(obj):
    return sha256_bytes(canonical_json(obj))


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_text(path, max_bytes=None, encoding="utf-8"):
    size = os.path.getsize(path)
    if max_bytes is not None and size > max_bytes:
        raise InputError(
            "file exceeds max_input_bytes",
            path=path, size_bytes=size, max_bytes=max_bytes,
        )
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes + 1 if max_bytes is not None else -1)
    if max_bytes is not None and len(raw) > max_bytes:
        raise InputError("file exceeds max_input_bytes", path=path,
                         size_bytes=len(raw), max_bytes=max_bytes)
    return raw.decode(encoding, errors="replace")


def read_json(path, max_bytes=8 * 1024 * 1024):
    text = read_text(path, max_bytes=max_bytes)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError("invalid JSON", path=path, error=str(exc),
                         line=exc.lineno, column=exc.colno)


def read_jsonl(path, max_bytes=32 * 1024 * 1024, max_lines=100000):
    text = read_text(path, max_bytes=max_bytes)
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise InputError("invalid JSONL record", path=path, line=lineno,
                             error=str(exc))
        if len(rows) > max_lines:
            raise InputError("JSONL exceeds max_lines", path=path,
                             max_lines=max_lines)
    return rows


def write_json(path, obj, indent=2, sort_keys=False):
    ensure_parent(path)
    tmp = "%s.tmp-%s" % (path, uuid.uuid4().hex[:8])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, sort_keys=sort_keys,
                  ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def append_jsonl(path, obj):
    ensure_parent(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(canonical_json(obj) + "\n")
    return path


def ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def deep_merge(base, override):
    """Recursively merge dicts; lists and scalars from `override` win."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get_path(obj, dotted, default=None):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def set_path(obj, dotted, value):
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    return obj


def coerce_scalar(text):
    """CLI override coercion: true/false/null/numbers/JSON, else string."""
    low = text.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", text.strip()):
        try:
            return int(text)
        except ValueError:
            pass
    if re.fullmatch(r"-?\d+\.\d+", text.strip()):
        try:
            return float(text)
        except ValueError:
            pass
    if text.strip().startswith(("{", "[", '"')):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def skill_root():
    """Directory that contains this file == the Skill Tool folder."""
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(filename):
    return os.path.join(skill_root(), filename)


def load_default_config():
    path = resource_path(DEFAULT_CONFIG_FILENAME)
    if not os.path.isfile(path):
        raise StateError("default configuration asset is missing",
                         path=DEFAULT_CONFIG_FILENAME)
    return read_json(path)


def load_config(config_path=None, overrides=None):
    """Merge defaults <- user config file <- CLI overrides. Returns (cfg, sources)."""
    cfg = load_default_config()
    sources = [DEFAULT_CONFIG_FILENAME]
    if config_path:
        if not os.path.isfile(config_path):
            raise InputError("config file not found", path=config_path)
        user_cfg = read_json(config_path)
        if not isinstance(user_cfg, dict):
            raise InputError("config file must contain a JSON object",
                             path=config_path)
        cfg = deep_merge(cfg, user_cfg)
        sources.append(os.path.abspath(config_path))
    if overrides:
        for key, value in overrides.items():
            set_path(cfg, key, coerce_scalar(value) if isinstance(value, str) else value)
    validate_config(cfg)
    return cfg, sources


CONFIG_REQUIRED = [
    "workspace.root",
    "limits.max_steps",
    "limits.step_timeout_seconds",
    "policy.require_approval",
    "airgap.allowed_hosts",
]


def validate_config(cfg):
    for dotted in CONFIG_REQUIRED:
        if get_path(cfg, dotted) is None:
            raise InputError("missing required configuration key", key=dotted)
    if not re.fullmatch(r"[A-Za-z0-9._\-/]+", str(get_path(cfg, "workspace.root"))):
        raise InputError("workspace.root must be a simple relative path",
                         value=get_path(cfg, "workspace.root"))
    for key in ("limits",):
        node = cfg.get(key, {})
        for name, value in node.items():
            if isinstance(value, (int, float)) and value < 0:
                raise InputError("negative limit is not allowed",
                                 key="%s.%s" % (key, name), value=value)
    provider = get_path(cfg, "provider.type")
    if provider not in ("deterministic", "command"):
        raise InputError("provider.type must be 'deterministic' or 'command'",
                         value=provider)
    if provider == "command":
        cmd = get_path(cfg, "provider.command") or []
        if not isinstance(cmd, list) or not cmd:
            raise InputError("provider.command is required for the command provider")
    mode = get_path(cfg, "airgap.mode")
    if mode not in ("offline", "loopback-only", "allowlisted-lan"):
        raise InputError("airgap.mode must be offline, loopback-only or allowlisted-lan",
                         value=mode)
    return True


# --------------------------------------------------------------------------------------
# Path scoping
# --------------------------------------------------------------------------------------


class Scope:
    """Resolve and validate paths against an allowed root (filesystem scope control)."""

    def __init__(self, root, max_bytes=None):
        self.root = os.path.abspath(root)
        self.max_bytes = max_bytes

    def resolve(self, path, must_exist=False):
        if not isinstance(path, str) or not path.strip():
            raise InputError("path must be a non-empty string", path=path)
        if "\x00" in path:
            raise InputError("path contains a NUL byte", path=path)
        candidate = path if os.path.isabs(path) else os.path.join(self.root, path)
        resolved = os.path.abspath(candidate)
        if resolved != self.root and not resolved.startswith(self.root + os.sep):
            raise PolicyError("path escapes the allowed scope",
                              path=path, scope=self.root)
        if must_exist and not os.path.exists(resolved):
            raise NotFoundError("path does not exist", path=resolved)
        return resolved


class MultiScope:
    """Ordered set of read roots. Used for ingesting local corpora outside the workspace.

    Reads may resolve inside the workspace root or inside any configured read root.
    Writes always use the single workspace `Scope`, so an ingestion root can never be
    modified by the tool.
    """

    def __init__(self, roots):
        self.roots = [os.path.abspath(r) for r in roots if r]

    def describe(self):
        return list(self.roots)

    def resolve(self, path, must_exist=False):
        if not isinstance(path, str) or not path.strip():
            raise InputError("path must be a non-empty string", path=path)
        if "\x00" in path:
            raise InputError("path contains a NUL byte", path=path)
        candidates = []
        if os.path.isabs(path):
            candidates.append(os.path.abspath(path))
        else:
            for root in self.roots:
                candidates.append(os.path.abspath(os.path.join(root, path)))
        for candidate in candidates:
            inside = any(candidate == root or candidate.startswith(root + os.sep)
                         for root in self.roots)
            if not inside:
                continue
            if must_exist and not os.path.exists(candidate):
                continue
            return candidate
        raise PolicyError("path is outside every allowed read root",
                          path=path, read_roots=self.roots)


# --------------------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def workspace_lock(path, timeout_seconds=10.0, poll=0.05):
    """Advisory exclusive lock to prevent concurrent workspace mutation."""
    ensure_parent(path)
    fh = open(path, "a+")
    deadline = time.time() + max(0.0, float(timeout_seconds))
    locked = False
    try:
        while True:
            try:
                _try_lock(fh)
                locked = True
                break
            except OSError:
                if time.time() >= deadline:
                    raise BudgetError("timed out waiting for workspace lock",
                                      lock=os.path.basename(path),
                                      timeout_seconds=timeout_seconds)
                time.sleep(poll)
        yield fh
    finally:
        if locked:
            with contextlib.suppress(OSError):
                _unlock(fh)
        fh.close()


try:  # pragma: no cover - platform dependent
    import fcntl

    def _try_lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows fallback
    import msvcrt

    def _try_lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


# --------------------------------------------------------------------------------------
# Signing keys and HMAC helpers
# --------------------------------------------------------------------------------------


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def load_or_create_key(cfg, workspace, purpose="hmac"):
    """Return a local HMAC key: environment variable first, else a 0600 key file.

    No secret is embedded in this package. The key never leaves the workspace unless
    the operator exports it.
    """
    env_name = get_path(cfg, "policy.hmac_key_env", "OAA_HMAC_KEY")
    value = os.environ.get(env_name) if env_name else None
    if value:
        return value.encode("utf-8"), "env:%s" % env_name
    key_path = workspace.path("%s.key" % purpose)
    if os.path.isfile(key_path):
        with open(key_path, "r", encoding="utf-8") as fh:
            return fh.read().strip().encode("utf-8"), "file"
    key = os.urandom(32)
    ensure_parent(key_path)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_b64e(key) + "\n")
    return _b64e(key).encode("ascii"), "file"


def hmac_sign(key, payload):
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------------------


class Workspace:
    """Workspace layout derived from configuration, rooted at workspace.root."""

    def __init__(self, cfg, base_dir=None):
        self.cfg = cfg
        root = str(get_path(cfg, "workspace.root", ".oaa"))
        self.root = os.path.abspath(os.path.join(base_dir or os.getcwd(), root))
        self.index_dir = self.path(str(get_path(cfg, "workspace.index_dir", "index")))
        self.state_db = self.path(str(get_path(cfg, "workspace.state_db",
                                                   "state.sqlite3")))
        self.audit_log = self.path(str(get_path(cfg, "workspace.audit_log",
                                                  "audit.jsonl")))
        self.artifacts_dir = self.path(str(get_path(cfg, "workspace.artifacts_dir",
                                                      "artifacts")))
        self.checkpoints_dir = self.path(str(get_path(cfg, "workspace.checkpoints_dir",
                                                        "checkpoints")))
        self.lock_file = self.path(str(get_path(cfg, "workspace.lock_file",
                                                  "workspace.lock")))
        self.scope = Scope(self.root, get_path(cfg, "limits.max_input_bytes"))
        base = os.path.abspath(base_dir or os.getcwd())
        read_roots = get_path(cfg, "workspace.read_roots", []) or []
        resolved_roots = [self.root]
        for entry in read_roots:
            resolved_roots.append(entry if os.path.isabs(entry)
                                  else os.path.join(base, entry))
        self.read_scope = MultiScope(resolved_roots)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def ensure(self):
        for directory in (self.root, self.index_dir, self.artifacts_dir,
                          self.checkpoints_dir):
            os.makedirs(directory, exist_ok=True)
        return self

    def rel(self, path):
        try:
            return os.path.relpath(path, self.root)
        except ValueError:  # pragma: no cover - different drives on Windows
            return path

    def source_id(self, path):
        """Stable, root-relative identifier for an ingested source file."""
        absolute = os.path.abspath(path)
        for root in self.read_scope.roots:
            if absolute == root or absolute.startswith(root + os.sep):
                return os.path.relpath(absolute, root)
        return absolute

    def resolve_asset(self, path, must_exist=False):
        """Resolve a readable path against the read roots, then the Skill Tool folder."""
        try:
            return self.read_scope.resolve(path, must_exist=must_exist)
        except OAAError:
            bundled = os.path.join(skill_root(), os.path.basename(str(path)))
            if os.path.isfile(bundled):
                return bundled
            raise

    def source_path(self, source_id):
        """Resolve a source identifier back to a live path, or None if it is gone."""
        try:
            return self.read_scope.resolve(source_id, must_exist=True)
        except OAAError:
            try:
                return self.read_scope.resolve(source_id)
            except OAAError:
                return None


# --------------------------------------------------------------------------------------
# Audit chain
# --------------------------------------------------------------------------------------


class AuditChain:
    """Append-only hash-chained log with an HMAC seal on every record.

    Each record stores `prev` (hash of the previous record) and `seal`
    (HMAC over the record body + prev). Verification detects edits, deletions and
    reordering. The log is JSON Lines so it stays readable and streamable.
    """

    def __init__(self, cfg, workspace):
        self.workspace = workspace
        self.log_path = workspace.audit_log
        self.key, self.key_source = load_or_create_key(cfg, workspace)

    def _last_record(self):
        last = None
        if not os.path.isfile(self.log_path):
            return None
        with open(self.log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return last

    def append(self, event):
        body = {
            "seq": 0,
            "time": now_iso(),
            "event": event.get("event", "unspecified"),
            "actor": event.get("actor", "local-operator"),
            "action": event.get("action", ""),
            "target": event.get("target", ""),
            "decision": event.get("decision", "allow"),
            "correlation_id": event.get("correlation_id", ""),
            "detail": event.get("detail", {}),
        }
        last = self._last_record()
        body["seq"] = (last["seq"] + 1) if last else 1
        body["prev"] = last["hash"] if last else "GENESIS"
        # hash covers the record content; seal covers content plus the hash itself
        body["hash"] = sha256_obj({k: v for k, v in body.items()
                                   if k not in ("hash", "seal")})
        body["seal"] = hmac_sign(self.key, canonical_json(body))
        append_jsonl(self.log_path, body)
        return body

    def verify(self):
        """Re-walk the chain. Returns a structured integrity report."""
        problems = []
        count = 0
        prev_hash = "GENESIS"
        prev_seq = 0
        if not os.path.isfile(self.log_path):
            return {"ok": True, "records": 0, "problems": [],
                    "log": self.workspace.rel(self.log_path)}
        with open(self.log_path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    problems.append({"line": lineno, "issue": "unparsable",
                                     "detail": str(exc)})
                    continue
                count += 1
                if rec.get("prev") != prev_hash:
                    problems.append({"line": lineno, "seq": rec.get("seq"),
                                     "issue": "broken_chain_link"})
                recomputed = sha256_obj({k: v for k, v in rec.items()
                                         if k not in ("hash", "seal")})
                if recomputed != rec.get("hash"):
                    problems.append({"line": lineno, "seq": rec.get("seq"),
                                     "issue": "hash_mismatch"})
                expected_seal = hmac_sign(self.key, canonical_json(
                    {k: v for k, v in rec.items() if k != "seal"}))
                if not hmac.compare_digest(expected_seal, str(rec.get("seal", ""))):
                    problems.append({"line": lineno, "seq": rec.get("seq"),
                                     "issue": "seal_invalid"})
                if rec.get("seq") != prev_seq + 1:
                    problems.append({"line": lineno, "seq": rec.get("seq"),
                                     "issue": "sequence_gap",
                                     "expected": prev_seq + 1})
                prev_hash = rec.get("hash")
                prev_seq = rec.get("seq", prev_seq)
        return {"ok": not problems, "records": count, "problems": problems,
                "log": self.workspace.rel(self.log_path),
                "key_source": self.key_source}

    def tail(self, limit=20):
        if not os.path.isfile(self.log_path):
            return []
        rows = []
        with open(self.log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows[-limit:]

    def timeline(self, correlation_id=None, limit=500):
        rows = self.tail(limit=10 ** 9)
        if correlation_id:
            rows = [r for r in rows if r.get("correlation_id") == correlation_id]
        events = []
        for rec in rows:
            events.append({
                "time": rec.get("time"),
                "seq": rec.get("seq"),
                "event": rec.get("event"),
                "actor": rec.get("actor"),
                "action": rec.get("action"),
                "target": rec.get("target"),
                "decision": rec.get("decision"),
                "correlation_id": rec.get("correlation_id"),
            })
        return events[-limit:]


# --------------------------------------------------------------------------------------
# Policy engine and capability tokens
# --------------------------------------------------------------------------------------


class PolicyEngine:
    """Default-deny action authorization with approval classes and role grants."""

    def __init__(self, cfg, workspace):
        self.cfg = cfg
        self.workspace = workspace
        self.allowed = set(get_path(cfg, "policy.allowed_actions", []))
        self.denied = set(get_path(cfg, "policy.denied_actions", []))
        self.approval_required = set(get_path(cfg, "policy.require_approval", []))
        self.default_deny = bool(get_path(cfg, "policy.default_deny", True))
        self.roles = get_path(cfg, "policy.roles", {}) or {}
        self.roles_authoritative = bool(get_path(
            cfg, "policy.roles_authoritative", True))
        self.read_prefixes = tuple(get_path(cfg, "policy.read_only_prefixes",
                                           ["read.", "query.", "list.", "audit."]))
        self.key, self.key_source = load_or_create_key(cfg, workspace)

    def _matches(self, collection, action):
        if action in collection:
            return True
        for prefix in collection:
            if prefix.endswith("*") and action.startswith(prefix[:-1]):
                return True
        return False

    def requires_approval(self, action):
        return self._matches(self.approval_required, action)

    def is_authorized(self, action, actor="local-operator", roles=None):
        """Static authorization decision (does not consider approval tokens).

        Order of evaluation:
          1. denied_actions always denies.
          2. an explicit role grant allows.
          3. a read-only action prefix allows (read access is the baseline).
          4. when policy.roles_authoritative is false, allowed_actions also allows.
          5. otherwise default_deny decides.
        """
        roles = roles if roles is not None else get_path(
            self.cfg, "policy.default_roles", ["operator"])
        if self._matches(self.denied, action):
            return False, "action is explicitly denied"
        known = [role for role in roles if role in self.roles]
        unknown = [role for role in roles if role not in self.roles]
        if unknown:
            return False, "unknown role requested: %s" % ", ".join(sorted(unknown))
        granted = set()
        for role in known:
            for prefix in self.roles.get(role, []):
                granted.add(prefix)
        if self._matches(granted, action):
            return True, "granted by role"
        if action.startswith(self.read_prefixes) and action not in self.denied:
            return True, "granted by read-only prefix"
        if not self.roles_authoritative and self._matches(self.allowed, action):
            return True, "granted by allowed_actions"
        if self.default_deny:
            return False, "default deny"
        return True, "default allow"

    def check(self, action, actor="local-operator", roles=None, approval_token=None,
              audit=None, approve=False):
        """Full check including approval requirements. Returns a decision dict."""
        ok, reason = self.is_authorized(action, actor=actor, roles=roles)
        if not ok:
            if audit is not None:
                audit.append({"event": "policy.decision", "actor": actor,
                              "action": action, "decision": "deny",
                              "detail": {"reason": reason}})
            raise PolicyError("action denied by policy", action=action,
                              actor=actor, reason=reason)
        if self.requires_approval(action):
            if approve:
                if audit is not None:
                    audit.append({"event": "policy.decision", "actor": actor,
                                  "action": action, "decision": "allow-with-approval",
                                  "detail": {"reason": "explicit --approve flag"}})
                return {"action": action, "decision": "allow",
                        "approved": True, "reason": "explicit approval flag"}
            if approval_token:
                self.verify_token(approval_token, action=action)
                if audit is not None:
                    audit.append({"event": "policy.decision", "actor": actor,
                                  "action": action,
                                  "decision": "allow-with-token",
                                  "detail": {"reason": "capability token"}})
                return {"action": action, "decision": "allow", "approved": True,
                        "reason": "capability token accepted"}
            if audit is not None:
                audit.append({"event": "policy.decision", "actor": actor,
                              "action": action, "decision": "approval-required"})
            raise ApprovalRequired(
                "action requires explicit approval",
                action=action,
                remediation="re-run with --approve, or issue a capability token "
                            "via: tools-oaa.py policy token-issue --action %s" % action,
            )
        if audit is not None:
            audit.append({"event": "policy.decision", "actor": actor,
                          "action": action, "decision": "allow",
                          "detail": {"reason": reason}})
        return {"action": action, "decision": "allow", "approved": False,
                "reason": reason}

    # -- capability tokens ---------------------------------------------------------
    def issue_token(self, action, subject="local-operator", ttl_seconds=None,
                    constraints=None, single_use=True):
        ttl = int(ttl_seconds if ttl_seconds is not None
                  else get_path(self.cfg, "policy.token_ttl_seconds", 600))
        if ttl <= 0 or ttl > 86400:
            raise InputError("token ttl must be between 1 and 86400 seconds", ttl=ttl)
        issued = time.time()
        body = {
            "token_id": new_id("cap"),
            "subject": subject,
            "action": action,
            "issued_at": now_iso(issued),
            "expires_at": now_iso(issued + ttl),
            "expires_epoch": int(issued + ttl),
            "single_use": bool(single_use),
            "constraints": constraints or {},
            "schema": SCHEMA_VERSION,
        }
        body["signature"] = hmac_sign(self.key, canonical_json(body))
        return body

    def verify_token(self, token, action=None):
        if isinstance(token, str):
            try:
                token = json.loads(_b64d(token).decode("utf-8"))
            except Exception:
                raise PolicyError("capability token is not decodable")
        if not isinstance(token, dict):
            raise PolicyError("capability token must be an object")
        signature = str(token.get("signature", ""))
        unsigned = {k: v for k, v in token.items() if k != "signature"}
        expected = hmac_sign(self.key, canonical_json(unsigned))
        if not hmac.compare_digest(expected, signature):
            raise PolicyError("capability token signature is invalid",
                              token_id=token.get("token_id"))
        if float(token.get("expires_epoch", 0)) <= time.time():
            raise PolicyError("capability token has expired",
                              token_id=token.get("token_id"),
                              expires_at=token.get("expires_at"))
        if action and not _action_match(token.get("action", ""), action):
            raise PolicyError("capability token does not cover this action",
                              token_action=token.get("action"), action=action)
        return token


def _action_match(grant, requested):
    if grant == requested:
        return True
    if grant.endswith("*") and requested.startswith(grant[:-1]):
        return True
    return False


def encode_token(token):
    return _b64e(canonical_json(token).encode("utf-8"))


# --------------------------------------------------------------------------------------
# Durable state store (sqlite3)
# --------------------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kv (
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  cls TEXT NOT NULL,
  subject TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence REAL NOT NULL,
  provenance TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  op TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoint (
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, step_id)
);
CREATE TABLE IF NOT EXISTS run_registry (
  run_id TEXT PRIMARY KEY,
  workflow TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  manifest TEXT NOT NULL,
  correlation_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dlq (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  error TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approval (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  subject TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  evidence TEXT NOT NULL
);
"""

MEMORY_CLASSES = {
    "identity": {"retention_days": 3650, "prohibited": False},
    "preference": {"retention_days": 365, "prohibited": False},
    "project": {"retention_days": 180, "prohibited": False},
    "decision": {"retention_days": 730, "prohibited": False},
    "procedure": {"retention_days": 365, "prohibited": False},
    "lesson": {"retention_days": 730, "prohibited": False},
    "temporary": {"retention_days": 1, "prohibited": False},
    "secret": {"retention_days": 0, "prohibited": True},
    "prohibited": {"retention_days": 0, "prohibited": True},
}


class StateStore:
    """SQLite-backed durable state, memories, idempotency, checkpoints and DLQ."""

    def __init__(self, path, cfg=None):
        self.path = path
        ensure_parent(path)
        self.conn = sqlite3.connect(path, timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        self.cfg = cfg or {}

    def close(self):
        with contextlib.suppress(Exception):
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- kv ------------------------------------------------------------------------
    def kv_set(self, scope, key, value):
        self.conn.execute(
            "INSERT INTO kv(scope,key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (scope, key, canonical_json(value), now_iso()))
        self.conn.commit()
        return {"scope": scope, "key": key, "stored": True}

    def kv_get(self, scope, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM kv WHERE scope=? AND key=?", (scope, key)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def kv_list(self, scope):
        rows = self.conn.execute(
            "SELECT key, value, updated_at FROM kv WHERE scope=? ORDER BY key",
            (scope,)).fetchall()
        return [{"key": r["key"], "value": json.loads(r["value"]),
                 "updated_at": r["updated_at"]} for r in rows]

    # -- memory --------------------------------------------------------------------
    def memory_put(self, cls, subject, content, confidence=0.8, provenance=None,
                   ttl_days=None):
        if cls not in MEMORY_CLASSES:
            raise InputError("unknown memory class", cls=cls,
                             allowed=sorted(MEMORY_CLASSES))
        if MEMORY_CLASSES[cls]["prohibited"]:
            raise PolicyError("memory class is prohibited from durable storage",
                              cls=cls,
                              remediation="store secrets via the secret store, or "
                                          "classify as temporary")
        if not (0.0 <= float(confidence) <= 1.0):
            raise InputError("confidence must be within 0..1", confidence=confidence)
        days = MEMORY_CLASSES[cls]["retention_days"] if ttl_days is None else ttl_days
        created = time.time()
        expires = now_iso(created + days * 86400) if days else None
        prov = provenance or {}
        prov.setdefault("created_by", "local-operator")
        prov.setdefault("recorded_at", now_iso(created))
        mem_id = new_id("mem")
        self.conn.execute(
            "INSERT INTO memory(id,cls,subject,content,confidence,provenance,"
            "created_at,updated_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mem_id, cls, subject, content, float(confidence), canonical_json(prov),
             now_iso(created), now_iso(created), expires, "active"))
        self.conn.commit()
        return self.memory_get(mem_id)

    def memory_get(self, mem_id):
        row = self.conn.execute("SELECT * FROM memory WHERE id=?", (mem_id,)).fetchone()
        if row is None:
            raise NotFoundError("memory not found", id=mem_id)
        return _memory_row(row)

    def memory_list(self, cls=None, subject=None, include_expired=False):
        sql = "SELECT * FROM memory WHERE 1=1"
        args = []
        if cls:
            sql += " AND cls=?"
            args.append(cls)
        if subject:
            sql += " AND subject=?"
            args.append(subject)
        if not include_expired:
            sql += " AND status='active' AND (expires_at IS NULL OR expires_at > ?)"
            args.append(now_iso())
        sql += " ORDER BY updated_at DESC"
        return [_memory_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def memory_delete(self, mem_id, hard=False):
        row = self.conn.execute("SELECT id FROM memory WHERE id=?", (mem_id,)).fetchone()
        if row is None:
            raise NotFoundError("memory not found", id=mem_id)
        if hard:
            self.conn.execute("DELETE FROM memory WHERE id=?", (mem_id,))
        else:
            self.conn.execute("UPDATE memory SET status='deleted', updated_at=? "
                              "WHERE id=?", (now_iso(), mem_id))
        self.conn.commit()
        return {"id": mem_id, "deleted": True, "hard": bool(hard)}

    def memory_purge_expired(self):
        cur = self.conn.execute(
            "UPDATE memory SET status='expired', updated_at=? "
            "WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso(), now_iso()))
        self.conn.commit()
        return {"expired": cur.rowcount}

    # -- idempotency ---------------------------------------------------------------
    def idempotency_get(self, key):
        row = self.conn.execute("SELECT op,result,created_at FROM idempotency "
                                "WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return {"key": key, "op": row["op"], "result": json.loads(row["result"]),
                "created_at": row["created_at"]}

    def idempotency_put(self, key, op, result):
        self.conn.execute(
            "INSERT INTO idempotency(key,op,result,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, op, canonical_json(result), now_iso()))
        self.conn.commit()
        return self.idempotency_get(key)

    # -- checkpoints / runs --------------------------------------------------------
    def checkpoint_save(self, run_id, step_id, state):
        self.conn.execute(
            "INSERT INTO checkpoint(run_id,step_id,state,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(run_id,step_id) DO UPDATE SET state=excluded.state, "
            "created_at=excluded.created_at",
            (run_id, step_id, canonical_json(state), now_iso()))
        self.conn.commit()
        return {"run_id": run_id, "step_id": step_id, "saved": True}

    def checkpoint_load(self, run_id):
        rows = self.conn.execute(
            "SELECT step_id,state,created_at FROM checkpoint WHERE run_id=? "
            "ORDER BY created_at", (run_id,)).fetchall()
        return [{"step_id": r["step_id"], "state": json.loads(r["state"]),
                 "created_at": r["created_at"]} for r in rows]

    def run_upsert(self, run_id, workflow, version, status, manifest,
                   correlation_id):
        self.conn.execute(
            "INSERT INTO run_registry(run_id,workflow,version,status,started_at,"
            "updated_at,manifest,correlation_id) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, "
            "updated_at=excluded.updated_at, manifest=excluded.manifest",
            (run_id, workflow, version, status, now_iso(), now_iso(),
             canonical_json(manifest), correlation_id))
        self.conn.commit()
        return {"run_id": run_id, "status": status}

    def run_get(self, run_id):
        row = self.conn.execute("SELECT * FROM run_registry WHERE run_id=?",
                                (run_id,)).fetchone()
        if row is None:
            raise NotFoundError("run not found", run_id=run_id)
        data = dict(row)
        data["manifest"] = json.loads(data["manifest"])
        return data

    def run_list(self, limit=50, status=None):
        sql = "SELECT run_id,workflow,version,status,started_at,updated_at," \
              "correlation_id FROM run_registry"
        args = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # -- dead letter queue ---------------------------------------------------------
    def dlq_add(self, run_id, step_id, error):
        entry = {"id": new_id("dlq"), "run_id": run_id, "step_id": step_id,
                 "error": error, "created_at": now_iso(), "resolved": False}
        self.conn.execute(
            "INSERT INTO dlq(id,run_id,step_id,error,created_at,resolved) "
            "VALUES(?,?,?,?,?,0)",
            (entry["id"], run_id, step_id, canonical_json(error),
             entry["created_at"]))
        self.conn.commit()
        return entry

    def dlq_list(self, include_resolved=False):
        sql = "SELECT * FROM dlq"
        if not include_resolved:
            sql += " WHERE resolved=0"
        sql += " ORDER BY created_at DESC"
        out = []
        for row in self.conn.execute(sql).fetchall():
            out.append({"id": row["id"], "run_id": row["run_id"],
                        "step_id": row["step_id"],
                        "error": json.loads(row["error"]),
                        "created_at": row["created_at"],
                        "resolved": bool(row["resolved"])})
        return out

    def dlq_resolve(self, entry_id):
        cur = self.conn.execute("UPDATE dlq SET resolved=1 WHERE id=?", (entry_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError("dead-letter entry not found", id=entry_id)
        return {"id": entry_id, "resolved": True}

    # -- approvals -----------------------------------------------------------------
    def approval_request(self, action, subject, reason, evidence):
        entry = {"id": new_id("apv"), "action": action, "subject": subject,
                 "reason": reason, "evidence": evidence, "status": "pending",
                 "created_at": now_iso(), "decided_at": None}
        self.conn.execute(
            "INSERT INTO approval(id,action,subject,reason,status,created_at,"
            "evidence) VALUES(?,?,?,?,?,?,?)",
            (entry["id"], action, subject, reason, "pending", entry["created_at"],
             canonical_json(evidence)))
        self.conn.commit()
        return entry

    def approval_decide(self, approval_id, approve, decider="local-operator"):
        row = self.conn.execute("SELECT * FROM approval WHERE id=?",
                                (approval_id,)).fetchone()
        if row is None:
            raise NotFoundError("approval not found", id=approval_id)
        if row["status"] != "pending":
            raise StateError("approval is already decided", id=approval_id,
                             status=row["status"])
        status = "approved" if approve else "denied"
        self.conn.execute("UPDATE approval SET status=?, decided_at=? WHERE id=?",
                          (status, now_iso(), approval_id))
        self.conn.commit()
        entry = dict(row)
        entry.update({"status": status, "decided_at": now_iso(), "decider": decider,
                      "evidence": json.loads(row["evidence"])})
        return entry

    def approval_list(self, status=None):
        sql = "SELECT * FROM approval"
        args = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC"
        out = []
        for row in self.conn.execute(sql, args).fetchall():
            item = dict(row)
            item["evidence"] = json.loads(item["evidence"])
            out.append(item)
        return out

    def describe(self):
        counts = {}
        for table in ("kv", "memory", "idempotency", "checkpoint", "run_registry",
                      "dlq", "approval"):
            counts[table] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM %s" % table).fetchone()["n"]
        return {"path": self.path, "tables": counts, "schema_version": SCHEMA_VERSION}


def _memory_row(row):
    return {
        "id": row["id"],
        "cls": row["cls"],
        "subject": row["subject"],
        "content": row["content"],
        "confidence": row["confidence"],
        "provenance": json.loads(row["provenance"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
        "status": row["status"],
    }


# --------------------------------------------------------------------------------------
# Model provider adapter
# --------------------------------------------------------------------------------------


class DeterministicProvider:
    """Offline reference provider used for validation and self-tests.

    It performs no learning and no inference of a neural network. It produces
    extractive, deterministic answers from the supplied evidence so the surrounding
    governance pipeline (grounding, citation, budgets) can be exercised end to end.
    """

    name = "deterministic"

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def info(self):
        return {"provider": self.name, "model": "extractive-reference",
                "weights": "none", "purpose": "validation and deterministic tests"}

    def generate(self, prompt, evidence=None, max_chars=600, timeout=None):
        evidence = evidence or []
        if not evidence:
            return {"text": "", "provider": self.name,
                    "abstained": True,
                    "reason": "no approved evidence supplied"}
        sentences = []
        for item in evidence:
            text = str(item.get("text", ""))
            for part in re.split(r"(?<=[.!?])\s+", text):
                part = part.strip()
                if part:
                    sentences.append({"text": part,
                                      "source": item.get("source", "unknown"),
                                      "chunk_id": item.get("chunk_id")})
        terms = _terms(prompt)
        scored = []
        for sent in sentences:
            overlap = len(terms & _terms(sent["text"]))
            if overlap:
                scored.append((overlap, sent))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["text"]))
        picked = [s for _, s in scored[:3]]
        if not picked:
            return {"text": "", "provider": self.name, "abstained": True,
                    "reason": "no evidence sentence matched the request"}
        body = " ".join(p["text"] for p in picked)
        return {"text": body[:max_chars], "provider": self.name,
                "abstained": False,
                "citations": [{"source": p["source"], "chunk_id": p["chunk_id"],
                               "text": p["text"][:200]} for p in picked]}


class CommandProvider:
    """Adapter for an approved local runtime executable.

    Protocol: the executable is launched with the configured argument vector, receives
    one JSON object on stdin, and must print one JSON object on stdout:
        {"text": "...", "citations": [...], "abstained": false}
    The call is bounded by `provider.timeout_seconds`, the argument vector must match
    the configured command exactly, and the binary must already exist locally.
    """

    name = "command"

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.command = list(get_path(self.cfg, "provider.command", []) or [])
        self.timeout = float(get_path(self.cfg, "provider.timeout_seconds", 60))
        self.model = get_path(self.cfg, "provider.model", "local-default")

    def info(self):
        return {"provider": self.name, "model": self.model,
                "command": self.command, "timeout_seconds": self.timeout}

    def generate(self, prompt, evidence=None, max_chars=600, timeout=None):
        import subprocess

        if not self.command:
            raise ProviderError("provider.command is not configured")
        exe = self.command[0]
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            raise ProviderError("provider executable is missing or not executable",
                                executable=exe)
        payload = {"model": self.model, "prompt": prompt,
                   "evidence": evidence or [], "max_chars": max_chars}
        limit = float(timeout or self.timeout)
        try:
            proc = subprocess.run(
                self.command,
                input=canonical_json(payload),
                capture_output=True, text=True, timeout=limit,
                shell=False, check=False,
                env={k: v for k, v in os.environ.items()
                     if k in ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR")},
            )
        except subprocess.TimeoutExpired:
            raise TransientError("provider call timed out", timeout_seconds=limit)
        if proc.returncode != 0:
            raise ProviderError("provider exited non-zero",
                                returncode=proc.returncode,
                                stderr=proc.stderr[-800:])
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ProviderError("provider returned invalid JSON",
                                stdout=proc.stdout[-800:])
        if not isinstance(data, dict) or "text" not in data:
            raise ProviderError("provider response must be an object with a text field")
        data.setdefault("provider", self.name)
        data["text"] = str(data["text"])[:max_chars]
        return data


def make_provider(cfg):
    kind = get_path(cfg, "provider.type", "deterministic")
    if kind == "command":
        return CommandProvider(cfg)
    return DeterministicProvider(cfg)


# --------------------------------------------------------------------------------------
# Text helpers and retry
# --------------------------------------------------------------------------------------

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was",
    "were", "be", "with", "as", "by", "at", "from", "that", "this", "it", "its",
    "which", "how", "what", "when", "who", "why", "do", "does", "did", "can", "could",
    "should", "would", "there", "their", "we", "you", "i", "not", "no", "if", "then",
}


def _terms(text):
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_\-']+", str(text).lower())
            if t not in _STOP and len(t) > 1}


def terms(text):
    return _terms(text)


def retry_call(fn, attempts=3, base=0.2, max_delay=30.0, jitter=0.25,
               retry_on=(TransientError, ProviderError), sleeper=time.sleep,
               on_attempt=None):
    """Bounded exponential backoff with jitter. Non-retryable errors propagate."""
    attempts = max(1, int(attempts))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn(attempt)
            return {"ok": True, "attempts": attempt, "result": result}
        except retry_on as exc:
            last = exc
            if on_attempt:
                on_attempt(attempt, exc)
            if attempt >= attempts:
                break
            delay = min(max_delay, base * (2 ** (attempt - 1)))
            delay *= 1.0 + random.uniform(-jitter, jitter)
            sleeper(max(0.0, delay))
        except OAAError:
            raise
    if last is not None:
        # Surface the original error code so callers see PROVIDER_FAILED, not TRANSIENT.
        details = dict(getattr(last, "details", {}) or {})
        details["attempts"] = attempts
        details["retries_exhausted"] = True
        raise last.__class__(last.message, **details)
    raise TransientError("retries exhausted", attempts=attempts)


def classify_failure(exc):
    """Map an exception to a failure class used by retry and escalation policy."""
    if isinstance(exc, InputError):
        return "input"
    if isinstance(exc, NotFoundError):
        return "data"
    if isinstance(exc, PolicyError):
        return "permission"
    if isinstance(exc, ApprovalRequired):
        return "approval"
    if isinstance(exc, BudgetError):
        return "resource"
    if isinstance(exc, StateError):
        return "state"
    if isinstance(exc, TransientError):
        return "transient"
    if isinstance(exc, ProviderError):
        return "tool"
    if isinstance(exc, IntegrityError):
        return "integrity"
    if isinstance(exc, OAAError):
        return "logic"
    if isinstance(exc, (OSError, IOError)):
        return "environment"
    return "logic"


# --------------------------------------------------------------------------------------
# Operation registry
# --------------------------------------------------------------------------------------


class Registry:
    """Name -> (handler, metadata). Ops are callable from CLI and workflows."""

    def __init__(self):
        self.ops = {}

    def op(self, name, summary, params=None, group="general", mutating=False,
           approval_action=None, examples=None):
        def decorator(fn):
            if name in self.ops:
                raise StateError("duplicate operation name", op=name)
            self.ops[name] = {
                "name": name,
                "summary": summary,
                "params": params or [],
                "group": group,
                "mutating": bool(mutating),
                "approval_action": approval_action,
                "examples": examples or [],
                "handler": fn,
            }
            return fn

        return decorator

    def get(self, name):
        entry = self.ops.get(name)
        if entry is None:
            raise NotFoundError("unknown operation", op=name,
                                available=sorted(self.ops))
        return entry

    def names(self):
        return sorted(self.ops)

    def describe(self):
        return [{k: v for k, v in entry.items() if k != "handler"}
                for entry in sorted(self.ops.values(), key=lambda e: e["name"])]


class Context:
    """Execution context passed to every operation."""

    def __init__(self, cfg, workspace, store, audit, policy, provider=None,
                 dry_run=False, approve=False, cancellation=None, run_id=None,
                 correlation_id=None, logger=None):
        self.cfg = cfg
        self.workspace = workspace
        self.store = store
        self.audit = audit
        self.policy = policy
        self.provider = provider or make_provider(cfg)
        self.dry_run = bool(dry_run)
        self.approve = bool(approve)
        self.cancellation = cancellation
        self.run_id = run_id
        self.correlation_id = correlation_id or new_id("cor")
        self.log = logger or (lambda message, level="info": None)
        self.started = time.time()
        self.deadline = self.started + float(
            get_path(cfg, "limits.workflow_timeout_seconds", 1800))

    def guard(self, action, approval_token=None):
        """Authorize an action through the policy engine."""
        return self.policy.check(action, approval_token=approval_token,
                                 audit=self.audit, approve=self.approve)

    def check_cancel(self):
        if self.cancellation is not None and self.cancellation.is_set():
            raise CancelledError("operation cancelled by operator")

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.time())

    def emit(self, event, **detail):
        self.audit.append({"event": event, "action": detail.pop("action", ""),
                           "target": detail.pop("target", ""),
                           "correlation_id": self.correlation_id,
                           "detail": detail})


def ok(payload=None, **extra):
    body = {"ok": True}
    if payload is not None:
        body["result"] = payload
    body.update(extra)
    return body
