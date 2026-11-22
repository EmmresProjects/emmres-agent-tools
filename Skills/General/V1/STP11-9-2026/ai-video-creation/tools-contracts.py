"""ai-video-creation / tools-contracts.py

Shared foundation: root-relative resource resolution, configuration loading,
skill contract construction, input/schema validation, approval policy checks,
secret redaction, and the structured response envelope.

Pure Python standard library. No network, no shell, no environment secrets.
Loaded by tools-ai-video.py (the primary entry point); it has no sibling imports.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
from pathlib import Path

TOOL_NAME = "ai-video-creation"
SCHEMA_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent


class ToolError(Exception):
    """Structured, user-visible failure."""

    def __init__(self, code, message, *, path=None, recovery=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.recovery = recovery
        self.details = details or {}

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "recovery": self.recovery,
            "details": self.details,
        }


# --------------------------------------------------------------------------
# Resource resolution. A leading "/" means "relative to the future skill root",
# never a filesystem absolute path. Candidate roots, in order:
#   1. explicit --root
#   2. parent of the Skill Tool folder (the future skill root holding SKILL.md)
#   3. the Skill Tool folder itself (standalone / freshly extracted ZIP)
#   4. current working directory
# --------------------------------------------------------------------------

_BAD_REF = re.compile(r"(^[a-zA-Z]:[\\/])|(\.\.)|(^\\)|(https?://)|(://)|(\x00)")


def check_resource_ref(ref):
    """Reject absolute, drive-letter, traversal, and external-address references."""
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("INVALID_RESOURCE_REF", "Resource reference must be a non-empty string.")
    body = ref[1:] if ref.startswith("/") else ref
    if _BAD_REF.search(ref) or "\\" in ref or body.startswith("/"):
        raise ToolError(
            "INVALID_RESOURCE_REF",
            "Resource references must be skill-root-relative, use forward slashes, "
            "and contain no drive letters, parent traversal, or external addresses.",
            path=ref,
        )
    return ref


def resource_candidates(root=None):
    cands = []
    if root:
        cands.append(Path(root).resolve())
    cands.append(TOOL_DIR.parent.resolve())
    cands.append(TOOL_DIR)
    cands.append(Path.cwd().resolve())
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_resource(ref, root=None):
    """Resolve /asset-x.json (skill-root-relative) to an existing real path."""
    check_resource_ref(ref)
    body = ref.lstrip("/")
    checked = []
    for base in resource_candidates(root):
        p = (base / body).resolve()
        checked.append(str(p))
        if p.is_file():
            return p
    raise ToolError(
        "RESOURCE_NOT_FOUND",
        "Referenced resource was not found in the skill root or the Skill Tool folder.",
        path=ref,
        recovery="Run 'python3 ai-video-creation/tools-ai-video.py doctor' and check --root.",
        details={"checked": checked},
    )


def load_json(ref, root=None):
    p = resolve_resource(ref, root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ToolError("RESOURCE_IO_ERROR", "Could not read %s: %s" % (ref, exc), path=ref)
    except json.JSONDecodeError as exc:
        raise ToolError("RESOURCE_INVALID_JSON", "Invalid JSON in %s at line %d: %s" % (ref, exc.lineno, exc.msg), path=ref)


def load_text(ref, root=None, max_bytes=2 * 1024 * 1024):
    p = resolve_resource(ref, root)
    if p.stat().st_size > max_bytes:
        raise ToolError("RESOURCE_TOO_LARGE", "Resource exceeds %d bytes." % max_bytes, path=ref)
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError("RESOURCE_IO_ERROR", "Could not read %s: %s" % (ref, exc), path=ref)


def load_user_input(path=None, text=None, max_bytes=2 * 1024 * 1024):
    """Read a user-supplied file or inline text. Exactly one source is required."""
    if bool(path) == bool(text):
        raise ToolError(
            "INPUT_SOURCE_ERROR",
            "Supply exactly one of --input PATH or --text VALUE.",
            recovery="Use --input <file> for files, or --text '...' for inline content.",
        )
    if text is not None:
        data = text
        origin = "inline"
    else:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ToolError("INPUT_NOT_FOUND", "Input file does not exist: %s" % p, path=str(p),
                            recovery="Check the path, or use --text for inline content.")
        if p.stat().st_size > max_bytes:
            raise ToolError("INPUT_TOO_LARGE", "Input exceeds %d bytes." % max_bytes, path=str(p))
        try:
            data = p.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolError("INPUT_IO_ERROR", "Could not read input: %s" % exc, path=str(p))
        origin = str(p)
    return data, origin


def load_user_json(path=None, text=None, max_bytes=2 * 1024 * 1024):
    raw, origin = load_user_input(path, text, max_bytes)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("INPUT_INVALID_JSON", "Input is not valid JSON (line %d): %s" % (exc.lineno, exc.msg),
                        path=origin, recovery="Fix the JSON syntax and retry.")
    return value, origin


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(config_path=None, root=None):
    """Default preset + optional user override. Providers/policies stay configurable."""
    default = load_json("/asset-default-config.json", root)
    if not config_path:
        override, origin = {}, "/asset-default-config.json"
    else:
        override, origin = load_user_json(path=config_path)
    cfg = deep_merge(default, override)
    for ref in cfg.get("resources", {}).values():
        check_resource_ref(ref)
    validate_config(cfg)
    return cfg, origin


def validate_config(cfg):
    issues = []
    for key in ("limits", "timeouts", "retries", "providers", "workflow", "output", "resources"):
        if not isinstance(cfg.get(key), (dict, list)):
            issues.append("config.%s is missing or has the wrong type" % key)
    lim = cfg.get("limits", {})
    for key in ("max_input_bytes", "max_capabilities", "max_stages", "max_shots", "max_caption_cues"):
        if not isinstance(lim.get(key), int) or lim[key] < 1:
            issues.append("config.limits.%s must be a positive integer" % key)
    t = cfg.get("timeouts", {})
    if not isinstance(t.get("job_seconds"), (int, float)) or t.get("job_seconds", 0) <= 0:
        issues.append("config.timeouts.job_seconds must be positive")
    if not cfg.get("workflow"):
        issues.append("config.workflow must contain at least one stage")
    if not isinstance(cfg.get("providers", {}).get("enabled"), list):
        issues.append("config.providers.enabled must be a list")
    if issues:
        raise ToolError("INVALID_CONFIG", "Configuration validation failed.", details={"issues": issues})


# --------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


_SECRET_KEY = re.compile(r"(api[_-]?key|secret|password|passwd|token|credential|authorization|private[_-]?key|bearer)", re.I)


def redact(value):
    """Remove secret-like values from anything written to logs/reports."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def envelope(command, status="ok", data=None, errors=None, warnings=None, recovery=None, limits=None, meta=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "command": command,
        "status": status,
        "generated_at": utc_now(),
        "data": data if data is not None else {},
        "errors": errors or [],
        "warnings": warnings or [],
        "recovery": recovery,
        "limits": limits or {},
        "meta": redact(meta or {}),
    }


def deterministic_id(*parts):
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def write_output(path, obj, *, as_text=None, max_bytes=8 * 1024 * 1024):
    """Write JSON or text to a user-chosen output path, atomically."""
    p = Path(path).expanduser()
    if not p.parent.is_dir():
        p.parent.mkdir(parents=True, exist_ok=True)
    body = as_text if as_text is not None else json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if len(body.encode("utf-8")) > max_bytes:
        raise ToolError("OUTPUT_TOO_LARGE", "Output exceeds %d bytes." % max_bytes, path=str(p))
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
    return str(p)


# --------------------------------------------------------------------------
# Minimal JSON-Schema validation (subset used by asset-brief-schema.json)
# --------------------------------------------------------------------------

def schema_validate(value, schema, path="$"):
    errors = []

    def err(code, message, at):
        errors.append({"code": code, "path": at, "message": message})

    typ = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if typ:
        types = typ if isinstance(typ, list) else [typ]
        if not any(checks.get(t, lambda v: True)(value) for t in types):
            err("TYPE_MISMATCH", "Expected %s." % "/".join(types), path)
            return errors
    if "enum" in schema and value not in schema["enum"]:
        err("ENUM_MISMATCH", "Value must be one of %s." % json.dumps(schema["enum"])[:300], path)
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            err("PATTERN_MISMATCH", "String does not match %s." % schema["pattern"], path)
        if "minLength" in schema and len(value) < schema["minLength"]:
            err("TOO_SHORT", "String is shorter than %d characters." % schema["minLength"], path)
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            err("TOO_LONG", "String is longer than %d characters." % schema["maxLength"], path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            err("BELOW_MINIMUM", "Value is below %s." % schema["minimum"], path)
        if "maximum" in schema and value > schema["maximum"]:
            err("ABOVE_MAXIMUM", "Value is above %s." % schema["maximum"], path)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                err("MISSING_REQUIRED", "Required field '%s' is missing." % req, path + "." + req)
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    err("UNKNOWN_FIELD", "Field '%s' is not allowed." % k, path + "." + k)
        for k, sub in props.items():
            if k in value:
                errors.extend(schema_validate(value[k], sub, path + "." + k))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            err("TOO_FEW_ITEMS", "Array needs at least %d items." % schema["minItems"], path)
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            err("TOO_MANY_ITEMS", "Array may contain at most %d items." % schema["maxItems"], path)
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(schema_validate(item, schema["items"], "%s[%d]" % (path, i)))
    return errors


_PATH_UNSAFE = re.compile(r"(^[a-zA-Z]:[\\/])|(^\\)|(\.\.)|(https?://)|(\x00)")


def semantic_validate_brief(brief, cfg, policy, schema=None):
    """Schema + semantic checks. Returns (errors, warnings)."""
    errors, warnings = [], []
    if not isinstance(brief, dict):
        return [{"code": "TYPE_MISMATCH", "path": "$", "message": "Brief must be a JSON object."}], warnings
    if schema:
        errors.extend(schema_validate(brief, schema))
    lim = cfg.get("limits", {})
    caps = brief.get("capabilities") or []
    if len(caps) > lim.get("max_capabilities", 1000):
        errors.append({"code": "TOO_MANY_CAPABILITIES", "path": "$.capabilities",
                       "message": "At most %d capabilities may be requested." % lim["max_capabilities"]})
    for key in ("captions_source", "script_source", "reference_assets_path", "output_path"):
        v = brief.get(key)
        if isinstance(v, str) and (_PATH_UNSAFE.search(v) or "\\" in v):
            errors.append({"code": "UNSAFE_PATH", "path": "$." + key,
                           "message": "Use relative forward-slash paths; no drive letters, traversal, or URLs."})
    for asset in brief.get("reference_assets") or []:
        if isinstance(asset, dict):
            p = asset.get("path")
            if isinstance(p, str) and (_PATH_UNSAFE.search(p) or "\\" in p):
                errors.append({"code": "UNSAFE_PATH", "path": "$.reference_assets.path",
                               "message": "Reference asset path is unsafe."})
            if not asset.get("rights"):
                warnings.append({"code": "MISSING_RIGHTS", "path": "$.reference_assets",
                                 "message": "Reference asset '%s' has no rights/consent metadata." % asset.get("id", "?")})
    for k in brief:
        if _SECRET_KEY.search(str(k)):
            errors.append({"code": "EMBEDDED_SECRET", "path": "$." + str(k),
                           "message": "Credential-like fields must stay outside briefs; use local provider config."})
    prov = brief.get("provider", cfg.get("providers", {}).get("default", "local"))
    enabled = cfg.get("providers", {}).get("enabled", [])
    if prov not in enabled:
        warnings.append({"code": "PROVIDER_NOT_ENABLED", "path": "$.provider",
                         "message": "Provider '%s' is not enabled; only %s are available." % (prov, enabled)})
    if brief.get("publish") and not brief.get("approval"):
        warnings.append({"code": "PUBLISH_NEEDS_APPROVAL", "path": "$.publish",
                         "message": "Publishing requires a recorded approval before execution."})
    if brief.get("locale") and not re.fullmatch(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*", str(brief["locale"])):
        errors.append({"code": "INVALID_LOCALE", "path": "$.locale", "message": "Use a BCP-47-like locale tag."})
    if brief.get("duration_seconds") and brief.get("fps") and brief["duration_seconds"] * brief["fps"] > 1_000_000:
        warnings.append({"code": "LARGE_JOB", "path": "$.duration_seconds",
                         "message": "Very long job; consider splitting into shots."})
    return errors, warnings


# --------------------------------------------------------------------------
# Approval policy and per-capability contracts
# --------------------------------------------------------------------------

def approval_reasons(capability, brief, policy):
    """Reasons an operation must be approved by a human before execution."""
    reasons = []
    if not isinstance(capability, dict):
        return reasons
    text = " ".join(str(capability.get(k, "")) for k in ("name", "slug", "description", "category"))
    kws = policy.get("approval_required", {}).get("capability_keywords", [])
    for kw in kws:
        if kw.lower() in text.lower():
            reasons.append("capability keyword '%s'" % kw)
            break
    if capability.get("safety_class") in policy.get("approval_required", {}).get("safety_classes", []):
        reasons.append("safety class '%s'" % capability.get("safety_class"))
    if capability.get("requires_approval") and not reasons:
        reasons.append("catalog-marked sensitive capability")
    for flag in policy.get("approval_required", {}).get("brief_flags", []):
        if isinstance(brief, dict) and brief.get(flag):
            reasons.append("brief flag '%s'" % flag)
    for action in policy.get("approval_required", {}).get("actions", []):
        if isinstance(brief, dict) and brief.get("action") == action:
            reasons.append("requested action '%s'" % action)
    return reasons


def build_contract(capability, cfg, policy, gates, brief=None):
    """Recommended skill contract from the catalog: trigger, I/O, tools, gates,
    failure behavior, approval, observability, and limits."""
    op = capability.get("local_operation", "contract")
    gate_defs = {g["id"]: g for g in gates.get("gates", [])}
    gate_list = [
        {"id": gid, "name": gate_defs.get(gid, {}).get("name", gid),
         "severity": gate_defs.get(gid, {}).get("severity", "warning")}
        for gid in capability.get("quality_gates", gates.get("default_gate_ids", []))
    ]
    reasons = approval_reasons(capability, brief or {}, policy)
    required = list(gates.get("required_brief_fields", ["title", "objective"]))
    optional = [
        "audience", "duration_seconds", "fps", "aspect_ratio", "resolution", "style",
        "style_preset", "locale", "safety_class", "capabilities", "script", "captions_source",
        "reference_assets", "audio", "delivery_preset", "provider", "seed", "constraints",
        "approval", "publish", "voice_clone", "likeness",
    ]
    inputs = {
        "required": required,
        "optional": optional,
        "asset_constraints": "Relative forward-slash paths only; rights/consent metadata travels with every asset.",
        "max_input_bytes": cfg.get("limits", {}).get("max_input_bytes"),
    }
    outputs = {
        "primary": "structured JSON job/artifact descriptor with status, assumptions, dependencies, and unresolved issues",
        "editable_artifacts": {
            "compile-prompt": "prompt JSON and Markdown",
            "shotlist": "shot list JSON and Markdown EDL",
            "captions": "normalized SRT/VTT and timing report",
            "gates": "quality gate report JSON",
            "validate": "validation report JSON",
            "delivery": "delivery manifest JSON",
            "plan": "production plan JSON",
            "approval": "approval/policy check JSON",
            "contract": "editable job envelope JSON",
        }.get(op, "editable job envelope JSON"),
        "schema": "envelope v%s: status, data, errors, warnings, recovery, limits, meta" % SCHEMA_VERSION,
    }
    timeout = cfg.get("timeouts", {})
    return {
        "capability_id": capability.get("id"),
        "capability_name": capability.get("name"),
        "category": capability.get("category"),
        "family": capability.get("family"),
        "local_operation": op,
        "trigger": "Run when the brief requires '%s'; defer to another capability when its own trigger matches more strongly." % capability.get("name"),
        "inputs": inputs,
        "outputs": outputs,
        "tools": {
            "permitted": ["local-stdlib", cfg.get("providers", {}).get("default", "local")],
            "shell": False,
            "network": bool(cfg.get("providers", {}).get("allow_external", False)),
            "provider_allowlist": cfg.get("providers", {}).get("enabled", []),
            "model_versions": cfg.get("providers", {}).get("model_versions", {}),
        },
        "quality_gates": gate_list,
        "failure_behavior": {
            "preserve_successful_stages": True,
            "isolate_earliest_failure": True,
            "max_attempts": cfg.get("retries", {}).get("max_attempts", 1),
            "retry_on": cfg.get("retries", {}).get("retry_on", []),
            "fallback": "Mark stage spec_ready with a provider_required note and hand off an editable artifact.",
            "cleanup": "Remove .partial files; keep completed stage artifacts and checkpoint.json.",
        },
        "human_approval": {
            "required": bool(reasons),
            "reasons": reasons,
            "mechanism": "approve --confirm --capability <id> --reason <text>, then pass the returned token via --approval-token.",
        },
        "observability": {
            "job_id": "deterministic hash of brief + config version",
            "recorded": ["parameters", "provider", "model version", "warnings", "gate results", "duration"],
            "secrets": "never recorded; secret-like keys are redacted",
        },
        "limits": {
            "stage_seconds": timeout.get("stage_seconds"),
            "job_seconds": timeout.get("job_seconds"),
            "max_output_bytes": cfg.get("limits", {}).get("max_output_bytes"),
            "max_stages": cfg.get("limits", {}).get("max_stages"),
        },
    }


def verify_approval_token(token, approvals_file, capability_id=None, brief=None):
    """Validate a locally recorded approval. No external identity service."""
    if not token:
        return False, "no approval token supplied"
    p = Path(approvals_file).expanduser()
    if not p.is_file():
        return False, "approval store not found: %s" % p
    try:
        store = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "approval store is unreadable"
    for rec in store.get("approvals", []):
        if rec.get("token") != token or rec.get("revoked"):
            continue
        if rec.get("expires_at") and rec["expires_at"] < utc_now():
            continue
        scope = rec.get("capability_id")
        if scope and scope != "*" and capability_id and scope != capability_id:
            continue
        if brief and rec.get("brief_id") and rec["brief_id"] != deterministic_id(json.dumps(brief, sort_keys=True)):
            continue
        return True, "approved by '%s' for %s" % (rec.get("actor", "unknown"), rec.get("reason", "unspecified"))
    return False, "token is unknown, expired, revoked, or out of scope"
