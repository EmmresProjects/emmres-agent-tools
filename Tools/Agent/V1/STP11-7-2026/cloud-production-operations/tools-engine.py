#!/usr/bin/env python3
"""Reusable engine for the cloud-production-operations Skill Tool.

The engine is intentionally provider-neutral and offline. It resolves catalog
selections, validates requests, creates reviewable plans, and assesses evidence.
It never executes cloud changes, provider CLIs, arbitrary code, or network calls.
"""
from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Iterable

TOOL_NAME = "cloud-production-operations"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential)(?:$|[_-])",
    re.IGNORECASE,
)
PLACEHOLDERS = {"", "tbd", "todo", "unknown", "unassigned", "unnamed", "none", "null", "changeme"}
ACTION_VERBS = {
    "assess", "assign", "audit", "author", "automate", "bring", "build", "calculate", "capture",
    "catalog", "centralize", "choose", "classify", "collect", "compare", "configure", "connect",
    "coordinate", "create", "define", "deliver", "deploy", "design", "detect", "discover", "document",
    "enable", "encrypt", "enforce", "establish", "evaluate", "exercise", "expose", "find", "forecast",
    "generate", "identify", "implement", "integrate", "inventory", "issue", "keep", "maintain", "manage",
    "map", "measure", "migrate", "model", "monitor", "move", "normalize", "observe", "operate", "prepare",
    "produce", "protect", "provide", "provision", "publish", "record", "replace", "request", "require",
    "restore", "retest", "review", "rotate", "route", "run", "score", "select", "shift", "standardize",
    "stop", "store", "summarize", "suspend", "test", "track", "train", "translate", "tune", "upgrade",
    "use", "validate", "verify", "version",
}
CATEGORY_EVIDENCE = {
    "cloud-strategy-accounts-and-organization": "Provider configuration, ownership record, architecture decision, and reviewed organizational policy.",
    "billing-finops-and-capacity-economics": "Cost export, budget or forecast calculation, allocation record, and finance-owner review.",
    "identity-access-and-privileged-operations": "Access policy, identity inventory, approval record, simulation result, and audit event.",
    "domains-dns-certificates-and-edge-routing": "Zone or certificate inventory, DNS/query result, routing test, renewal record, and rollback record.",
    "network-architecture-and-connectivity": "Network diagram, route and policy export, connectivity test, flow log, and security review.",
    "compute-hosting-containers-and-orchestration": "Workload configuration, image provenance, health and scaling telemetry, and deployment review.",
    "ci-cd-release-engineering-and-supply-chain": "Pipeline definition, immutable artifact identifier, provenance, test result, approval, and release observation.",
    "databases-caches-messaging-and-data-services": "Engine configuration, query or lag metrics, backup/restore evidence, access review, and recovery test.",
    "storage-backup-retention-and-recovery": "Storage policy, immutable backup record, restore test, retention evidence, and measured recovery result.",
    "secrets-keys-configuration-and-trust": "Non-sensitive key metadata, rotation record, access audit, configuration diff, and verification result; never secret values.",
    "observability-monitoring-logging-and-alerts": "Telemetry configuration, redacted query result, alert test, dashboard reference, and ownership record.",
    "security-engineering-and-protection": "Security test result, detection record, scoped findings, control configuration, and authorized remediation evidence.",
    "infrastructure-as-code-and-configuration-automation": "Reviewed plan or what-if output, policy result, state/drift evidence, test result, and rollback assessment.",
    "reliability-incidents-and-production-operations": "Runbook, incident or exercise timeline, service-level telemetry, action record, and recovery verification.",
    "testing-validation-and-production-acceptance": "Versioned test definition, sanitized result, observed metrics, defect reference, and acceptance decision.",
    "privacy-compliance-governance-and-documentation": "Approved documentation, control mapping, data-flow record, evidence locator, owner attestation, and exception record.",
    "application-platform-and-runtime-engineering": "Application configuration, compatibility/security test, runtime telemetry, rollout record, and isolation verification.",
    "business-continuity-communications-and-service-management": "Service record, approved communication, exercise/contact test, support handoff, and continuity review.",
}


class CloudOpsError(Exception):
    """Expected operational error with stable machine-readable details."""

    def __init__(self, code: str, message: str, *, exit_code: int = 2, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


class Deadline:
    """Cooperative deadline and cancellation check for bounded local work."""

    def __init__(self, seconds: float, cancelled: Callable[[], bool] | None = None):
        if seconds <= 0:
            raise CloudOpsError("invalid-timeout", "Timeout must be greater than zero.")
        self.seconds = seconds
        self.started = time.monotonic()
        self.expires = self.started + seconds
        self.cancelled = cancelled or (lambda: False)

    def check(self) -> None:
        if self.cancelled():
            raise CloudOpsError("cancelled", "Operation cancelled; no partial output was committed.", exit_code=130)
        if time.monotonic() > self.expires:
            raise CloudOpsError(
                "timeout",
                f"Operation exceeded the {self.seconds:g}-second cooperative deadline; no partial output was committed.",
                exit_code=6,
            )


class _TextExtractor(HTMLParser):
    BLOCKS = {"br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs  # Classes, data attributes, and every other attribute are deliberately ignored.
        tag = tag.lower()
        if tag in {"script", "style", "template"}:
            self.ignored_depth += 1
        elif tag in self.BLOCKS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "template"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in self.BLOCKS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    if not re.search(r"<\s*[a-zA-Z][^>]*>", value):
        return html.unescape(value)
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:  # HTMLParser errors are input errors, not internal failures.
        raise CloudOpsError("invalid-html", f"Could not parse copied HTML: {exc}") from exc
    return "".join(parser.parts)


def normalize_label(value: str) -> str:
    value = html_to_text(value)
    value = re.sub(r"[*_`~]", "", value)
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def slugify(value: str) -> str:
    return normalize_label(value).replace(" ", "-")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_time(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str, path: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise CloudOpsError("invalid-datetime", f"{path} must be an ISO 8601 date-time.", details={"path": path}) from exc
    if parsed.tzinfo is None:
        raise CloudOpsError("invalid-datetime", f"{path} must include a timezone (use Z or an offset).", details={"path": path})
    return parsed.astimezone(dt.timezone.utc)


def read_text(path: Path, max_bytes: int, *, label: str = "input") -> str:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise CloudOpsError(
                "input-too-large",
                f"The {label} is {size} bytes; the configured limit is {max_bytes} bytes.",
                details={"path": str(path), "size": size, "limit": max_bytes},
            )
        return path.read_text(encoding="utf-8")
    except CloudOpsError:
        raise
    except FileNotFoundError as exc:
        raise CloudOpsError("input-not-found", f"The {label} file was not found: {path}", exit_code=4) from exc
    except (OSError, UnicodeError) as exc:
        raise CloudOpsError("input-read-error", f"Could not read {label} file {path}: {exc}", exit_code=4) from exc


def load_json(path: Path, max_bytes: int, *, label: str) -> Any:
    raw = read_text(path, max_bytes, label=label)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CloudOpsError(
            "invalid-json",
            f"Invalid JSON in {label} file {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}.",
            details={"path": str(path), "line": exc.lineno, "column": exc.colno},
        ) from exc


def load_policy(path: Path) -> dict[str, Any]:
    policy = load_json(path, 1_048_576, label="policy")
    if not isinstance(policy, dict) or policy.get("policy_version") != "1.0":
        raise CloudOpsError("invalid-policy", "Policy must be an object with policy_version '1.0'.")
    limits = policy.get("limits")
    required = {
        "max_input_bytes", "max_catalog_bytes", "max_capabilities_per_request", "max_evidence_items",
        "max_output_bytes", "default_timeout_seconds", "max_timeout_seconds", "max_text_characters",
        "max_string_characters",
    }
    if not isinstance(limits, dict) or not required.issubset(limits):
        raise CloudOpsError("invalid-policy", f"Policy limits must include: {', '.join(sorted(required))}.")
    ceilings = {
        "max_input_bytes": 67_108_864,
        "max_catalog_bytes": 67_108_864,
        "max_capabilities_per_request": 1_000,
        "max_evidence_items": 10_000,
        "max_output_bytes": 134_217_728,
        "default_timeout_seconds": 3_600,
        "max_timeout_seconds": 3_600,
        "max_text_characters": 200_000,
        "max_string_characters": 100_000,
    }
    for key in required:
        value = limits[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise CloudOpsError("invalid-policy", f"Policy limit {key} must be a positive number.")
        if key not in {"default_timeout_seconds", "max_timeout_seconds"} and not isinstance(value, int):
            raise CloudOpsError("invalid-policy", f"Policy limit {key} must be an integer.")
        if value > ceilings[key]:
            raise CloudOpsError("invalid-policy", f"Policy limit {key} exceeds the hard safety ceiling {ceilings[key]}.")
    if limits["default_timeout_seconds"] > limits["max_timeout_seconds"]:
        raise CloudOpsError("invalid-policy", "default_timeout_seconds cannot exceed max_timeout_seconds.")
    for section in ("approval", "assessment", "output"):
        if not isinstance(policy.get(section), dict):
            raise CloudOpsError("invalid-policy", f"Policy section {section} must be an object.")
    numeric_rules = {
        "approval": {"maximum_age_hours": (0, 8_760)},
        "assessment": {"maximum_evidence_age_hours": (0, 87_600)},
    }
    for section, rules in numeric_rules.items():
        for key, (minimum, maximum) in rules.items():
            value = policy[section].get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum < value <= maximum:
                raise CloudOpsError("invalid-policy", f"Policy {section}.{key} must be greater than {minimum} and at most {maximum}.")
    # future_skew_seconds may legitimately be zero.
    skew = policy["assessment"].get("future_skew_seconds")
    if isinstance(skew, bool) or not isinstance(skew, (int, float)) or not 0 <= skew <= 3_600:
        raise CloudOpsError("invalid-policy", "Policy assessment.future_skew_seconds must be between 0 and 3600.")
    boolean_fields = {
        "approval": {"production_execution_requires_approval", "high_risk_execution_requires_approval", "allow_expired"},
        "assessment": {"allow_not_applicable_for_required", "require_change_ticket_for_execution", "require_rollback_for_high_risk_execution", "require_backup_for_destructive_execution", "require_success_criteria_for_execution", "require_monitoring_for_production_execution"},
        "output": {"redact_sensitive_keys", "atomic_writes", "overwrite_requires_force"},
    }
    for section, keys in boolean_fields.items():
        for key in keys:
            if key in policy[section] and not isinstance(policy[section][key], bool):
                raise CloudOpsError("invalid-policy", f"Policy {section}.{key} must be boolean.")
    if policy["approval"].get("allow_expired", False):
        raise CloudOpsError("invalid-policy", "Expired approvals cannot be enabled by policy.")
    for key in ("redact_sensitive_keys", "atomic_writes", "overwrite_requires_force"):
        if policy["output"].get(key) is not True:
            raise CloudOpsError("invalid-policy", f"Safety invariant output.{key} must remain true.")
    return policy


class Catalog:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.categories: list[dict[str, Any]] = []
        self.capabilities: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.capability_aliases: dict[str, str] = {}
        self.category_aliases: dict[str, str] = {}
        self.category_by_id: dict[str, dict[str, Any]] = {}
        self._validate_and_index()

    @classmethod
    def load(cls, path: Path, max_bytes: int) -> "Catalog":
        data = load_json(path, max_bytes, label="capability catalog")
        if not isinstance(data, dict):
            raise CloudOpsError("invalid-catalog", "Capability catalog must be a JSON object.")
        return cls(data)

    def _validate_and_index(self) -> None:
        categories = self.data.get("categories")
        if not isinstance(categories, list) or not categories:
            raise CloudOpsError("invalid-catalog", "Capability catalog has no categories.")
        seen_ids: set[str] = set()
        for category in categories:
            if not isinstance(category, dict):
                raise CloudOpsError("invalid-catalog", "Every catalog category must be an object.")
            cid, name, caps = category.get("id"), category.get("name"), category.get("capabilities")
            if not isinstance(cid, str) or not ID_PATTERN.fullmatch(cid) or not isinstance(name, str) or not name.strip():
                raise CloudOpsError("invalid-catalog", f"Invalid category identity: {cid!r}.")
            if not isinstance(caps, list) or not caps:
                raise CloudOpsError("invalid-catalog", f"Category {cid} has no capabilities.")
            cat = {"id": cid, "name": name, "catalog_number": category.get("catalog_number")}
            self.categories.append(cat)
            self.category_by_id[cid] = cat
            for alias in (cid, name):
                normalized = normalize_label(alias)
                if normalized in self.category_aliases and self.category_aliases[normalized] != cid:
                    raise CloudOpsError("invalid-catalog", f"Ambiguous category alias: {alias}.")
                self.category_aliases[normalized] = cid
            for cap in caps:
                if not isinstance(cap, dict):
                    raise CloudOpsError("invalid-catalog", f"Invalid capability entry in {cid}.")
                cap_id, cap_name, description = cap.get("id"), cap.get("name"), cap.get("description")
                if not isinstance(cap_id, str) or not ID_PATTERN.fullmatch(cap_id):
                    raise CloudOpsError("invalid-catalog", f"Invalid capability ID: {cap_id!r}.")
                if cap_id in seen_ids:
                    raise CloudOpsError("invalid-catalog", f"Duplicate capability ID: {cap_id}.")
                if not isinstance(cap_name, str) or not cap_name.strip() or not isinstance(description, str) or not description.strip():
                    raise CloudOpsError("invalid-catalog", f"Capability {cap_id} needs a name and description.")
                item = {
                    "id": cap_id,
                    "name": cap_name,
                    "description": description,
                    "catalog_number": cap.get("catalog_number"),
                    "category_id": cid,
                    "category_name": name,
                }
                item.update(classify_capability(item))
                seen_ids.add(cap_id)
                self.capabilities.append(item)
                self.by_id[cap_id] = item
                for alias in (cap_id, cap_name):
                    normalized = normalize_label(alias)
                    if normalized in self.capability_aliases and self.capability_aliases[normalized] != cap_id:
                        raise CloudOpsError("invalid-catalog", f"Ambiguous capability alias: {alias}.")
                    self.capability_aliases[normalized] = cap_id
        if self.data.get("capability_count") != len(self.capabilities) or self.data.get("category_count") != len(self.categories):
            raise CloudOpsError("invalid-catalog", "Catalog counts do not match the actual entries.")

    def capability(self, value: str) -> dict[str, Any]:
        normalized = normalize_label(value)
        cap_id = self.capability_aliases.get(normalized)
        if not cap_id:
            suggestion = difflib.get_close_matches(normalized, list(self.capability_aliases), n=3, cutoff=0.65)
            names = [self.by_id[self.capability_aliases[item]]["id"] for item in suggestion]
            raise CloudOpsError(
                "unknown-capability",
                f"Unknown capability {value!r}." + (f" Close matches: {', '.join(names)}." if names else ""),
                details={"value": value, "close_matches": names},
            )
        return self.by_id[cap_id]

    def category(self, value: str) -> dict[str, Any]:
        normalized = normalize_label(value)
        cid = self.category_aliases.get(normalized)
        if not cid:
            suggestion = difflib.get_close_matches(normalized, list(self.category_aliases), n=3, cutoff=0.6)
            names = [self.category_aliases[item] for item in suggestion]
            raise CloudOpsError(
                "unknown-category",
                f"Unknown category {value!r}." + (f" Close matches: {', '.join(names)}." if names else ""),
                details={"value": value, "close_matches": names},
            )
        return self.category_by_id[cid]

    def in_category(self, cid: str) -> list[dict[str, Any]]:
        return [cap for cap in self.capabilities if cap["category_id"] == cid]

    def parse_selection_text(self, raw: str, deadline: Deadline | None = None) -> dict[str, Any]:
        if len(raw) > 200_000:
            raise CloudOpsError("input-too-large", "Selection text exceeds 200,000 characters.")
        text = html_to_text(raw).replace("\r\n", "\n").replace("\r", "\n")
        found_caps: list[str] = []
        found_cats: list[str] = []
        warnings: list[str] = []

        def add_cap(value: str) -> bool:
            try:
                cap = self.capability(value.strip())
            except CloudOpsError:
                return False
            if cap["id"] not in found_caps:
                found_caps.append(cap["id"])
            return True

        def add_cat(value: str) -> bool:
            try:
                cat = self.category(value.strip())
            except CloudOpsError:
                return False
            if cat["id"] not in found_cats:
                found_cats.append(cat["id"])
            return True

        for line_number, original in enumerate(text.split("\n"), start=1):
            if deadline and line_number % 100 == 0:
                deadline.check()
            line = original.strip()
            if not line or re.fullmatch(r"[\s*#_`~|:=+\-.]+", line):
                continue
            line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
            line = re.sub(r"^\s*(?:[-*+>]|\d{1,3}[.)])\s+", "", line)
            line = re.sub(r"^\s*\[[ xX]\]\s*", "", line)
            line = re.sub(r"[*_`~]", "", line).strip()
            declared_name = bool(re.match(r"^(?:skill\s+tool\s+name|skill\s+name|capability|category)\s*:", line, re.I))
            line = re.sub(r"^(?:skill\s+tool\s+name|skill\s+name|capability|category)\s*:\s*", "", line, flags=re.I).strip()
            if re.match(r"^description\s*:", line, re.I) or normalize_label(line) in {"description", "skill name", "category"}:
                continue
            # A copied one-line record may include its description; only the name is significant.
            candidate = re.split(r"\s+Description\s*:\s*", line, maxsplit=1, flags=re.I)[0].strip()
            candidate = re.sub(r"^\d{1,3}[.)]\s*", "", candidate).strip()
            if add_cat(candidate) or add_cap(candidate):
                continue
            if declared_name:
                warnings.append(f"Line {line_number}: declared name did not match the catalog and was ignored: {candidate[:120]}")
        if not found_caps and not found_cats:
            raise CloudOpsError(
                "no-capability-selected",
                "No catalog capability or category name was found. Use exact names/IDs, optionally with list numbers, Markdown, or HTML.",
                details={"warnings": warnings},
            )
        return {"capabilities": found_caps, "categories": found_cats, "warnings": warnings}

    def resolve(self, selection: dict[str, Any], deadline: Deadline | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(selection, dict):
            raise CloudOpsError("invalid-selection", "selection must be an object.")
        direct_caps = selection.get("capabilities", [])
        direct_cats = selection.get("categories", [])
        if not isinstance(direct_caps, list) or not all(isinstance(item, str) for item in direct_caps):
            raise CloudOpsError("invalid-selection", "selection.capabilities must be an array of strings.")
        if not isinstance(direct_cats, list) or not all(isinstance(item, str) for item in direct_cats):
            raise CloudOpsError("invalid-selection", "selection.categories must be an array of strings.")
        cap_ids: list[str] = list(direct_caps)
        cat_ids: list[str] = list(direct_cats)
        warnings: list[str] = []
        if selection.get("text"):
            if not isinstance(selection["text"], str):
                raise CloudOpsError("invalid-selection", "selection.text must be a string.")
            parsed = self.parse_selection_text(selection["text"], deadline)
            cap_ids.extend(parsed["capabilities"])
            cat_ids.extend(parsed["categories"])
            warnings.extend(parsed["warnings"])
        resolved: dict[str, dict[str, Any]] = {}
        resolved_categories: list[str] = []
        for value in cat_ids:
            cat = self.category(value)
            if cat["id"] not in resolved_categories:
                resolved_categories.append(cat["id"])
            for cap in self.in_category(cat["id"]):
                resolved[cap["id"]] = cap
            if deadline:
                deadline.check()
        for value in cap_ids:
            cap = self.capability(value)
            resolved[cap["id"]] = cap
            if deadline:
                deadline.check()
        if not resolved:
            raise CloudOpsError("no-capability-selected", "Select at least one capability or category.")
        info = {
            "requested_capabilities": list(direct_caps),
            "requested_categories": list(direct_cats),
            "resolved_categories": resolved_categories,
            "warnings": warnings,
        }
        return list(resolved.values()), info


def classify_capability(cap: dict[str, Any]) -> dict[str, Any]:
    text = f"{cap['name']} {cap['description']}".casefold()
    cid = cap["category_id"]
    destructive = bool(re.search(
        r"\b(delete|deletion|decommission|revoke|revocation|disable|schedule deletion|purge|retire|retirement|remove infrastructure|automatic cleanup)\b",
        text,
    ))
    financial = cid == "billing-finops-and-capacity-economics" or bool(re.search(r"\b(payment|purchase|billing|budget|cost|savings|service credits)\b", text))
    security = cid in {
        "identity-access-and-privileged-operations", "secrets-keys-configuration-and-trust", "security-engineering-and-protection",
    } or bool(re.search(r"\b(authentication|authorization|credential|secret|encryption|signing key|certificate|firewall|tenant isolation|session|privacy|personal data)\b", text))
    publishing = bool(re.search(r"\b(status page|outbound email|release notes|incident communication|publish|public communication)\b", text))
    critical = bool(re.search(
        r"\b(ransomware recovery|data deletion|service decommissioning|break-glass|code-signing credentials|hardware security module|encryption key management|token signing key rotation|secret rotation|immutable backup|legal hold)\b",
        text,
    ))
    high_categories = {
        "identity-access-and-privileged-operations", "domains-dns-certificates-and-edge-routing",
        "network-architecture-and-connectivity", "compute-hosting-containers-and-orchestration",
        "ci-cd-release-engineering-and-supply-chain", "databases-caches-messaging-and-data-services",
        "storage-backup-retention-and-recovery", "secrets-keys-configuration-and-trust",
        "security-engineering-and-protection", "infrastructure-as-code-and-configuration-automation",
        "reliability-incidents-and-production-operations", "application-platform-and-runtime-engineering",
    }
    low_pattern = bool(re.search(r"\b(documentation|training|knowledge base|decision records|provider selection|engine selection|inventory|review|assessment|design|notes automation|catalog management)\b", cap["name"].casefold()))
    if critical:
        risk = "critical"
    elif cid in high_categories or destructive or security:
        risk = "high"
    elif low_pattern and not financial and not publishing:
        risk = "low"
    else:
        risk = "medium"
    return {"risk": risk, "flags": {"destructive": destructive, "financial": financial, "security_sensitive": security, "publishing": publishing}}


def apply_defaults(value: Any, schema: dict[str, Any]) -> Any:
    if schema.get("type") == "object":
        source = value if isinstance(value, dict) else {}
        result = dict(source)
        for key, subschema in schema.get("properties", {}).items():
            if key in source:
                result[key] = apply_defaults(source[key], subschema)
            elif "default" in subschema:
                result[key] = apply_defaults(copy.deepcopy(subschema["default"]), subschema)
        return result
    if schema.get("type") == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [apply_defaults(item, schema["items"]) for item in value]
    return copy.deepcopy(value)


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    """Validate the bounded JSON Schema subset used by asset-request-schema.json."""
    findings: list[dict[str, Any]] = []

    def error(code: str, message: str, at: str = path) -> None:
        findings.append({"severity": "error", "code": code, "path": at, "message": message})

    if "const" in schema and value != schema["const"]:
        error("schema-const", f"Expected constant {schema['const']!r}.")
    if "enum" in schema and value not in schema["enum"]:
        error("schema-enum", f"Expected one of: {', '.join(map(str, schema['enum']))}.")
    expected = schema.get("type")
    type_map = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if expected:
        python_type = type_map[expected]
        valid_type = isinstance(value, python_type) and not (expected in {"integer", "number"} and isinstance(value, bool))
        if not valid_type:
            error("schema-type", f"Expected {expected}.")
            return findings
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                error("schema-required", f"Missing required property {key!r}.", f"{path}.{key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    error("schema-additional-property", f"Unknown property {key!r}.", f"{path}.{key}")
        for key, subschema in properties.items():
            if key in value:
                findings.extend(validate_schema(value[key], subschema, f"{path}.{key}"))
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            error("schema-max-items", f"Array has {len(value)} items; maximum is {schema['maxItems']}.")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for index, item in enumerate(value):
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                if marker in seen:
                    error("schema-unique-items", "Array items must be unique.", f"{path}[{index}]")
                seen.add(marker)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                findings.extend(validate_schema(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            error("schema-min-length", f"String must contain at least {schema['minLength']} character(s).")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            error("schema-max-length", f"String exceeds {schema['maxLength']} characters.")
        if schema.get("format") == "date-time" and value:
            try:
                parse_time(value, path)
            except CloudOpsError as exc:
                error(exc.code, exc.message)
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            error("schema-minimum", f"Value must be at least {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            error("schema-maximum", f"Value must be at most {schema['maximum']}.")
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if not any(not validate_schema(value, option, path) for option in alternatives):
            error("schema-any-of", "Value does not satisfy any allowed alternative.")
    return findings


def sensitive_key_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if SENSITIVE_KEY.search(str(key)):
                found.append(child)
            found.extend(sensitive_key_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(sensitive_key_paths(item, f"{path}[{index}]"))
    return found


def oversized_string_paths(value: Any, maximum: int, path: str = "$") -> list[str]:
    """Apply the policy string ceiling everywhere except the dedicated selection text field."""
    found: list[str] = []
    if isinstance(value, str):
        if path != "$.selection.text" and len(value) > maximum:
            found.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(oversized_string_paths(item, maximum, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(oversized_string_paths(item, maximum, f"{path}[{index}]"))
    return found


def normalize_request(raw: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    request = apply_defaults(raw, schema)
    # Keep the evidence array independent from caller-owned data.
    return copy.deepcopy(request)


def risk_rank(capabilities: Iterable[dict[str, Any]]) -> int:
    ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return max((ranks[cap["risk"]] for cap in capabilities), default=0)


def has_flag(capabilities: Iterable[dict[str, Any]], flag: str) -> bool:
    return any(cap["flags"][flag] for cap in capabilities)


def execution_blockers(request: dict[str, Any], capabilities: list[dict[str, Any]], policy: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    if request.get("change_intent") != "execute":
        return []
    blockers: list[dict[str, Any]] = []
    context = request["context"]
    rules = policy["assessment"]

    def block(code: str, path: str, message: str) -> None:
        blockers.append({"severity": "execution-blocker", "code": code, "path": path, "message": message})

    production = context["environment"] == "production"
    high = risk_rank(capabilities) >= 2
    destructive = has_flag(capabilities, "destructive")
    sensitive = any(has_flag(capabilities, flag) for flag in ("security_sensitive", "financial", "publishing"))
    if context["provider"] == "unspecified":
        block("provider-required", "$.context.provider", "Execution handoff requires an explicit provider; 'unspecified' is planning-only.")
    if rules.get("require_change_ticket_for_execution", True) and not context.get("change_ticket", "").strip():
        block("change-ticket-required", "$.context.change_ticket", "Execution handoff requires a change ticket/reference.")
    if (production or high) and not context.get("blast_radius", "").strip():
        block("blast-radius-required", "$.context.blast_radius", "Production or high-risk execution requires a bounded blast-radius statement.")
    if (production or high) and rules.get("require_rollback_for_high_risk_execution", True) and not request["recovery"].get("rollback_strategy", "").strip():
        block("rollback-required", "$.recovery.rollback_strategy", "Production or high-risk execution requires a rollback strategy.")
    if destructive and rules.get("require_backup_for_destructive_execution", True) and not request["recovery"].get("backup_reference", "").strip():
        block("backup-required", "$.recovery.backup_reference", "Destructive execution requires a non-sensitive backup reference.")
    if rules.get("require_success_criteria_for_execution", True) and not request["verification"]["success_criteria"]:
        block("success-criteria-required", "$.verification.success_criteria", "Execution requires at least one measurable success criterion.")
    if production and rules.get("require_monitoring_for_production_execution", True) and not request["verification"]["monitoring"]:
        block("monitoring-required", "$.verification.monitoring", "Production execution requires at least one monitoring/observation criterion.")
    approval_rules = policy["approval"]
    approval_needed = (
        (production and approval_rules.get("production_execution_requires_approval", True))
        or ((high or destructive or sensitive) and approval_rules.get("high_risk_execution_requires_approval", True))
    )
    if approval_needed:
        approval = request["approval"]
        if not approval.get("approved"):
            block("approval-required", "$.approval.approved", "This execution requires explicit approval; generating a plan does not grant approval.")
        else:
            if not approval.get("approver", "").strip() or not approval.get("reference", "").strip():
                block("approval-identity-required", "$.approval", "Approved execution requires both approver and approval reference.")
            if not approval.get("approved_at") or not approval.get("expires_at"):
                block("approval-time-required", "$.approval", "Approved execution requires approved_at and expires_at timestamps.")
            else:
                approved_at = parse_time(approval["approved_at"], "$.approval.approved_at")
                expires_at = parse_time(approval["expires_at"], "$.approval.expires_at")
                if approved_at > now + dt.timedelta(minutes=5):
                    block("approval-in-future", "$.approval.approved_at", "Approval time is in the future.")
                if expires_at <= now:
                    block("approval-expired", "$.approval.expires_at", "Approval has expired; obtain fresh approval before handoff.")
                if expires_at <= approved_at:
                    block("approval-invalid-window", "$.approval.expires_at", "Approval expiry must be later than the approval time.")
                max_age = dt.timedelta(hours=float(approval_rules.get("maximum_age_hours", 24)))
                if now - approved_at > max_age:
                    block("approval-too-old", "$.approval.approved_at", f"Approval exceeds the maximum age of {max_age}.")
    return blockers


def validate_request(
    raw: Any,
    schema: dict[str, Any],
    catalog: Catalog,
    policy: dict[str, Any],
    deadline: Deadline,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    now = now or utc_now()
    deadline.check()
    findings: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        findings.append({"severity": "error", "code": "request-not-object", "path": "$", "message": "Request must be a JSON object."})
        report = {"artifact_type": "validation-report", "valid": False, "valid_for_planning": False, "valid_for_execution": False, "findings": findings, "execution_blockers": [], "resolved_capability_count": 0}
        return report, None, [], {}
    normalized = normalize_request(raw, schema)
    findings.extend(validate_schema(normalized, schema))
    for path in sensitive_key_paths(raw):
        findings.append({"severity": "error", "code": "sensitive-key-forbidden", "path": path, "message": "Secret/credential values are forbidden. Supply a non-sensitive reference instead."})
    for path in oversized_string_paths(normalized, int(policy["limits"]["max_string_characters"])):
        findings.append({"severity": "error", "code": "string-too-long", "path": path, "message": f"String exceeds the policy limit of {policy['limits']['max_string_characters']} characters."})
    capabilities: list[dict[str, Any]] = []
    selection_info: dict[str, Any] = {}
    if isinstance(normalized.get("selection"), dict):
        try:
            selection_text = normalized["selection"].get("text", "")
            if isinstance(selection_text, str) and len(selection_text) > int(policy["limits"]["max_text_characters"]):
                raise CloudOpsError("input-too-large", "Selection text exceeds the configured policy character limit.")
            capabilities, selection_info = catalog.resolve(normalized["selection"], deadline)
            maximum = int(policy["limits"]["max_capabilities_per_request"])
            if len(capabilities) > maximum:
                findings.append({"severity": "error", "code": "too-many-capabilities", "path": "$.selection", "message": f"Selection resolves to {len(capabilities)} capabilities; maximum is {maximum}."})
            if len(normalized.get("evidence", [])) > int(policy["limits"]["max_evidence_items"]):
                findings.append({"severity": "error", "code": "too-much-evidence", "path": "$.evidence", "message": "Evidence count exceeds the configured policy limit."})
        except CloudOpsError as exc:
            findings.append({"severity": "error", "code": exc.code, "path": "$.selection", "message": exc.message, "details": exc.details})
    # Placeholder-like ownership values are not safe operational identities.
    if isinstance(normalized.get("context"), dict):
        for key in ("service", "owner"):
            value = normalized["context"].get(key)
            if isinstance(value, str) and value.strip().casefold() in PLACEHOLDERS:
                findings.append({"severity": "error", "code": f"real-{key}-required", "path": f"$.context.{key}", "message": f"Provide a real, accountable {key}; placeholder values are not accepted."})
    valid_planning = not any(item["severity"] == "error" for item in findings)
    blockers = execution_blockers(normalized, capabilities, policy, now) if valid_planning else []
    report = {
        "artifact_type": "validation-report",
        "artifact_version": "1.0",
        "valid": valid_planning,
        "valid_for_planning": valid_planning,
        "valid_for_execution": valid_planning and normalized.get("change_intent") == "execute" and not blockers,
        "change_intent": normalized.get("change_intent"),
        "resolved_capability_count": len(capabilities),
        "resolved_capabilities": [cap["id"] for cap in capabilities],
        "selection": selection_info,
        "findings": findings,
        "execution_blockers": blockers,
        "checked_at": iso_time(now),
    }
    return report, normalized if valid_planning else None, capabilities, selection_info


def split_work_items(description: str) -> list[str]:
    # Catalog descriptions are curated action statements. Commas delimit their advertised work items.
    segments = [part.strip() for part in re.split(r",|;", description) if part.strip()]
    if not segments:
        return [description]
    first_match = re.match(r"^([A-Za-z]+(?:\s+and\s+[A-Za-z]+)?)\s+", segments[0])
    inherited = first_match.group(1) if first_match else "Address"
    result: list[str] = []
    for index, segment in enumerate(segments):
        segment = re.sub(r"^and\s+", "", segment, flags=re.I).strip()
        first_word = segment.split(" ", 1)[0].casefold() if segment else ""
        if index and first_word not in ACTION_VERBS and not segment.casefold().startswith(("using ", "with ", "without ", "according to ", "based on ")):
            segment = f"{inherited} {segment}"
        result.append(segment[0].upper() + segment[1:] if segment else segment)
    return result


def global_controls(request: dict[str, Any], capabilities: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    execute = request["change_intent"] == "execute"
    production = request["context"]["environment"] == "production"
    high = risk_rank(capabilities) >= 2
    destructive = has_flag(capabilities, "destructive")
    sensitive = any(has_flag(capabilities, flag) for flag in ("security_sensitive", "financial", "publishing"))
    approval_rules = policy["approval"]
    approval_needed = execute and (
        (production and approval_rules.get("production_execution_requires_approval", True))
        or ((high or destructive or sensitive) and approval_rules.get("high_risk_execution_requires_approval", True))
    )

    def control(name: str, required: bool, description: str) -> dict[str, Any]:
        return {"id": f"global:{name}", "kind": "input", "required": bool(required), "description": description}

    return [
        control("ownership", True, "A real service and accountable owner are recorded."),
        control("provider", execute, "An explicit provider is selected for execution handoff."),
        control("change-ticket", execute and policy["assessment"].get("require_change_ticket_for_execution", True), "A change ticket/reference is recorded."),
        control("blast-radius", execute and (production or high), "The affected scope and blast radius are bounded."),
        control("approval", approval_needed, "Current, attributable approval covers the requested execution handoff."),
        control("rollback", execute and (production or high) and policy["assessment"].get("require_rollback_for_high_risk_execution", True), "A rollback strategy is recorded for production or high-risk work."),
        control("backup", execute and destructive and policy["assessment"].get("require_backup_for_destructive_execution", True), "A non-sensitive backup reference is recorded for destructive work."),
        control("success-criteria", execute and policy["assessment"].get("require_success_criteria_for_execution", True), "Measurable success criteria are recorded."),
        control("monitoring", execute and production and policy["assessment"].get("require_monitoring_for_production_execution", True), "Production monitoring and observation criteria are recorded."),
    ]


def build_plan(
    request: dict[str, Any],
    capabilities: list[dict[str, Any]],
    validation: dict[str, Any],
    policy: dict[str, Any],
    deadline: Deadline,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    execute = request["change_intent"] == "execute"
    plan_caps: list[dict[str, Any]] = []
    for cap in capabilities:
        deadline.check()
        work_items = split_work_items(cap["description"])
        tasks: list[dict[str, Any]] = []
        controls: list[dict[str, Any]] = []
        for index, item in enumerate(work_items, start=1):
            task_id = f"task-{index:02d}"
            control_id = f"{cap['id']}:{task_id}"
            tasks.append({
                "id": task_id,
                "instruction": item,
                "status": "pending-external-handoff" if execute else "preview-item",
                "control_id": control_id,
            })
            controls.append({"id": control_id, "kind": "evidence", "required": execute, "description": f"Evidence for: {item}"})
        verification_text = f"Compare the observed result with every advertised work item for {cap['name']}; record discrepancies, residual risk, and owner acceptance."
        verification_id = f"{cap['id']}:verification"
        controls.append({"id": verification_id, "kind": "evidence", "required": execute, "description": verification_text})
        safeguards = [
            "Review a provider-specific preview and preserve least privilege; this tool does not call provider APIs.",
            "Record only non-sensitive evidence locators and summaries; never include secret values.",
        ]
        if cap["flags"]["destructive"]:
            safeguards.append("Destructive scope requires an isolated backup, restore consideration, bounded scope, and explicit approval before external execution.")
        if cap["flags"]["financial"]:
            safeguards.append("Financial commitments or budget changes require the accountable finance approver outside this tool.")
        if cap["flags"]["security_sensitive"]:
            safeguards.append("Security-sensitive work requires least privilege, separation of duties, and redacted audit evidence.")
        if cap["flags"]["publishing"]:
            safeguards.append("External communication or publishing requires content approval and a verified audience/channel outside this tool.")
        plan_caps.append({
            "id": cap["id"], "name": cap["name"], "category_id": cap["category_id"], "category_name": cap["category_name"],
            "description": cap["description"], "risk": cap["risk"], "flags": cap["flags"],
            "preflight": [
                f"Confirm that {request['context']['service']} and its in-scope {request['context']['provider']} resources are owned by {request['context']['owner']}.",
                f"Confirm the requested {request['context']['environment']} scope and obtain the provider-specific read-only preview before any external change.",
            ],
            "tasks": tasks,
            "safeguards": safeguards,
            "verification": {"control_id": verification_id, "instruction": verification_text},
            "suggested_evidence": CATEGORY_EVIDENCE.get(cap["category_id"], "Versioned configuration, test results, owner review, and observed-state evidence."),
            "controls": controls,
        })
    stable_request = copy.deepcopy(request)
    stable_request["evidence"] = []
    plan_seed = json.dumps(stable_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    plan_id = "cpo-" + hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "artifact_type": "cloud-production-operations-plan",
        "artifact_version": "1.0",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "plan_id": plan_id,
        "generated_at": iso_time(now),
        "change_intent": request["change_intent"],
        "execution": {
            "performed_by_this_tool": False,
            "live_provider_calls": 0,
            "shell_commands_executed": 0,
            "status": "approval-gated-external-handoff" if execute else "preview-only",
            "note": "This artifact is a validated plan, not proof that any external change occurred.",
        },
        "context": request["context"],
        "recovery": request["recovery"],
        "verification": request["verification"],
        "approval": request["approval"],
        "selection": validation.get("selection", {}),
        "policy_version": policy["policy_version"],
        "evidence_supplied": len(request.get("evidence", [])),
        "global_controls": global_controls(request, capabilities, policy),
        "capabilities": plan_caps,
        "validation": {
            "valid_for_planning": validation["valid_for_planning"],
            "valid_for_execution_inputs": not validation.get("execution_blockers"),
            "execution_blockers": validation.get("execution_blockers", []),
        },
        "summary": {
            "category_count": len({cap["category_id"] for cap in capabilities}),
            "capability_count": len(capabilities),
            "task_count": sum(len(cap["tasks"]) for cap in plan_caps),
            "required_evidence_control_count": sum(1 for cap in plan_caps for control in cap["controls"] if control["required"]),
            "highest_risk": (["low", "medium", "high", "critical"][risk_rank(capabilities)]),
        },
    }


def assess_evidence(
    request: dict[str, Any],
    plan: dict[str, Any],
    policy: dict[str, Any],
    deadline: Deadline,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    execute = request["change_intent"] == "execute"
    controls = {control["id"]: control for control in plan["global_controls"]}
    for cap in plan["capabilities"]:
        for control in cap["controls"]:
            controls[control["id"]] = control
    evidence_by_control: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for index, evidence in enumerate(request.get("evidence", [])):
        deadline.check()
        cid = evidence["control_id"]
        if cid not in controls:
            failures.append({"code": "unknown-evidence-control", "control_id": cid, "message": "Evidence references a control outside this plan."})
            continue
        if controls[cid]["kind"] != "evidence":
            failures.append({"code": "input-control-evidence-not-used", "control_id": cid, "message": "This control is evaluated from request inputs, not external evidence."})
            continue
        if cid in evidence_by_control:
            failures.append({"code": "duplicate-evidence-control", "control_id": cid, "message": "Duplicate evidence for one control is ambiguous; provide one current consolidated item."})
            continue
        evidence_by_control[cid] = evidence
    blocker_codes = {item["code"] for item in plan["validation"]["execution_blockers"]}
    global_code_map = {
        "global:provider": {"provider-required"},
        "global:change-ticket": {"change-ticket-required"},
        "global:blast-radius": {"blast-radius-required"},
        "global:approval": {"approval-required", "approval-identity-required", "approval-time-required", "approval-in-future", "approval-expired", "approval-invalid-window", "approval-too-old"},
        "global:rollback": {"rollback-required"},
        "global:backup": {"backup-required"},
        "global:success-criteria": {"success-criteria-required"},
        "global:monitoring": {"monitoring-required"},
    }
    results: list[dict[str, Any]] = []
    for control in plan["global_controls"]:
        deadline.check()
        required = control["required"]
        related = global_code_map.get(control["id"], set())
        failed = bool(related & blocker_codes)
        status = "failed" if failed else "satisfied-by-input"
        if not required and not failed:
            status = "not-required" if control["id"] != "global:ownership" else status
        results.append({"control_id": control["id"], "required": required, "status": status, "source": "request"})
        if failed:
            failures.append({"code": "input-control-failed", "control_id": control["id"], "message": control["description"]})
    max_age = dt.timedelta(hours=float(policy["assessment"].get("maximum_evidence_age_hours", 720)))
    future_skew = dt.timedelta(seconds=float(policy["assessment"].get("future_skew_seconds", 300)))
    allow_na = bool(policy["assessment"].get("allow_not_applicable_for_required", False))
    for cid, control in controls.items():
        if control["kind"] != "evidence":
            continue
        deadline.check()
        evidence = evidence_by_control.get(cid)
        required = control["required"]
        if evidence is None:
            status = "missing" if required else "pending-optional"
            results.append({"control_id": cid, "required": required, "status": status, "source": ""})
            if required:
                failures.append({"code": "required-evidence-missing", "control_id": cid, "message": control["description"]})
            continue
        observed = parse_time(evidence["observed_at"], f"evidence[{cid}].observed_at")
        stale = now - observed > max_age
        future = observed > now + future_skew
        status = "passed" if evidence["status"] == "passed" and not stale and not future else evidence["status"]
        if stale:
            status = "stale"
            failures.append({"code": "stale-evidence", "control_id": cid, "message": f"Evidence is older than the configured {max_age}."})
        if future:
            status = "future-dated"
            failures.append({"code": "future-evidence", "control_id": cid, "message": "Evidence observation time is beyond the allowed future skew."})
        if evidence["status"] == "failed":
            failures.append({"code": "evidence-failed", "control_id": cid, "message": "The supplied evidence explicitly reports failure."})
        if evidence["status"] == "not-applicable" and required:
            if not allow_na or not evidence.get("justification", "").strip():
                failures.append({"code": "required-control-not-applicable", "control_id": cid, "message": "Not-applicable evidence does not satisfy this required control under the active policy."})
        results.append({
            "control_id": cid, "required": required, "status": status, "source": evidence["source"],
            "observed_at": evidence["observed_at"], "summary": evidence["summary"],
        })
    # Blockers not mapped to a control still prevent handoff.
    mapped_codes = set().union(*global_code_map.values()) if global_code_map else set()
    for blocker in plan["validation"]["execution_blockers"]:
        if blocker["code"] not in mapped_codes:
            failures.append({"code": blocker["code"], "control_id": "", "message": blocker["message"]})
    # De-duplicate repeated messages while preserving order.
    unique_failures: list[dict[str, Any]] = []
    seen_failures: set[str] = set()
    for failure in failures:
        marker = json.dumps(failure, sort_keys=True)
        if marker not in seen_failures:
            seen_failures.add(marker)
            unique_failures.append(failure)
    ready = not unique_failures
    if ready:
        status = "ready-for-external-handoff" if execute else "preview-assessment-passed"
    else:
        status = "not-ready"
    return {
        "artifact_type": "cloud-production-operations-assessment",
        "artifact_version": "1.0",
        "plan_id": plan["plan_id"],
        "assessed_at": iso_time(now),
        "change_intent": request["change_intent"],
        "ready": ready,
        "status": status,
        "external_execution_performed": False,
        "summary": {
            "capability_count": len(plan["capabilities"]),
            "control_count": len(controls),
            "evidence_count": len(request.get("evidence", [])),
            "failure_count": len(unique_failures),
            "passed_evidence_count": sum(1 for result in results if result["status"] == "passed"),
            "required_missing_count": sum(1 for result in results if result["status"] == "missing"),
        },
        "failures": unique_failures,
        "controls": results,
        "note": "Readiness authorizes no action by itself. An approved external executor must still enforce provider-specific controls.",
    }


def make_run_artifact(plan: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "cloud-production-operations-run",
        "artifact_version": "1.0",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "status": assessment["status"],
        "ready": assessment["ready"],
        "plan": plan,
        "assessment": assessment,
    }


def md(value: Any) -> str:
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(artifact: dict[str, Any]) -> str:
    kind = artifact.get("artifact_type", "")
    lines: list[str] = []
    if kind == "cloud-production-operations-run":
        lines.append("# Cloud Production Operations Run")
        lines.append("")
        lines.append(f"- Status: **{md(artifact['status'])}**")
        lines.append(f"- Ready: **{str(artifact['ready']).lower()}**")
        lines.append("- External execution performed: **no**")
        lines.append("")
        lines.append(render_markdown(artifact["plan"]))
        lines.append("")
        lines.append(render_markdown(artifact["assessment"]))
        return "\n".join(lines).rstrip() + "\n"
    if kind == "cloud-production-operations-plan":
        lines.extend(["# Cloud Production Operations Plan", ""])
        lines.append(f"- Plan ID: `{md(artifact['plan_id'])}`")
        lines.append(f"- Intent: **{md(artifact['change_intent'])}**")
        lines.append(f"- Service: {md(artifact['context']['service'])}")
        lines.append(f"- Owner: {md(artifact['context']['owner'])}")
        lines.append(f"- Provider / environment: {md(artifact['context']['provider'])} / {md(artifact['context']['environment'])}")
        lines.append(f"- Capabilities / tasks: {artifact['summary']['capability_count']} / {artifact['summary']['task_count']}")
        lines.append("- Live provider calls and shell commands executed: **0 / 0**")
        lines.append("")
        if artifact["validation"]["execution_blockers"]:
            lines.extend(["## Execution input blockers", ""])
            for item in artifact["validation"]["execution_blockers"]:
                lines.append(f"- **{md(item['code'])}**: {md(item['message'])}")
            lines.append("")
        lines.extend(["## Global controls", ""])
        for control in artifact["global_controls"]:
            lines.append(f"- `{md(control['id'])}` ({'required' if control['required'] else 'conditional'}): {md(control['description'])}")
        for cap in artifact["capabilities"]:
            lines.extend(["", f"## {md(cap['name'])}", ""])
            lines.append(f"`{md(cap['id'])}` · {md(cap['category_name'])} · risk: **{md(cap['risk'])}**")
            lines.append("")
            lines.append(md(cap["description"]))
            lines.extend(["", "### Preflight", ""])
            lines.extend(f"- {md(item)}" for item in cap["preflight"])
            lines.extend(["", "### Work items", ""])
            for task in cap["tasks"]:
                lines.append(f"- [ ] `{md(task['control_id'])}` — {md(task['instruction'])}")
            lines.extend(["", "### Safeguards", ""])
            lines.extend(f"- {md(item)}" for item in cap["safeguards"])
            lines.extend(["", "### Verification and evidence", ""])
            lines.append(f"- [ ] `{md(cap['verification']['control_id'])}` — {md(cap['verification']['instruction'])}")
            lines.append(f"- Suggested evidence: {md(cap['suggested_evidence'])}")
        return "\n".join(lines).rstrip() + "\n"
    if kind == "cloud-production-operations-assessment":
        lines.extend(["# Cloud Production Operations Assessment", ""])
        lines.append(f"- Plan ID: `{md(artifact['plan_id'])}`")
        lines.append(f"- Status: **{md(artifact['status'])}**")
        lines.append(f"- Ready: **{str(artifact['ready']).lower()}**")
        lines.append("- External execution performed: **no**")
        lines.append(f"- Controls / evidence / failures: {artifact['summary']['control_count']} / {artifact['summary']['evidence_count']} / {artifact['summary']['failure_count']}")
        if artifact["failures"]:
            lines.extend(["", "## Failures", ""])
            for failure in artifact["failures"]:
                lines.append(f"- **{md(failure['code'])}** `{md(failure['control_id'])}`: {md(failure['message'])}")
        lines.extend(["", "## Controls", "", "| Control | Required | Status | Source |", "|---|---:|---|---|"])
        for result in artifact["controls"]:
            lines.append(f"| `{md(result['control_id'])}` | {str(result['required']).lower()} | {md(result['status'])} | {md(result.get('source', ''))} |")
        return "\n".join(lines).rstrip() + "\n"
    if kind == "capability-list":
        lines.extend(["# Capability List", "", f"Total capabilities: **{artifact['capability_count']}**", ""])
        for category in artifact["categories"]:
            lines.extend([f"## {md(category['name'])}", ""])
            for cap in category["capabilities"]:
                lines.append(f"- `{md(cap['id'])}` — {md(cap['name'])} ({md(cap['risk'])} risk)")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    if kind == "selection-result":
        lines.extend(["# Resolved Selection", "", f"Capabilities selected: **{artifact['capability_count']}**", ""])
        lines.extend(f"- `{md(cap['id'])}` — {md(cap['name'])}" for cap in artifact["capabilities"])
        if artifact.get("warnings"):
            lines.extend(["", "## Warnings", ""])
            lines.extend(f"- {md(item)}" for item in artifact["warnings"])
        return "\n".join(lines).rstrip() + "\n"
    if kind == "validation-report":
        lines.extend(["# Request Validation", ""])
        lines.append(f"- Valid for planning: **{str(artifact['valid_for_planning']).lower()}**")
        lines.append(f"- Valid execution inputs: **{str(artifact['valid_for_execution']).lower()}**")
        lines.append(f"- Resolved capabilities: **{artifact['resolved_capability_count']}**")
        for title, key in (("Errors", "findings"), ("Execution blockers", "execution_blockers")):
            if artifact.get(key):
                lines.extend(["", f"## {title}", ""])
                for finding in artifact[key]:
                    lines.append(f"- **{md(finding['code'])}** `{md(finding.get('path', ''))}`: {md(finding['message'])}")
        return "\n".join(lines).rstrip() + "\n"
    raise CloudOpsError("render-error", f"No Markdown renderer for artifact type {kind!r}.", exit_code=5)


def atomic_write(path: Path, content: str, *, force: bool, max_bytes: int, deadline: Deadline | None = None) -> None:
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        raise CloudOpsError("output-too-large", f"Rendered output is {len(data)} bytes; configured limit is {max_bytes}.", exit_code=4)
    if path.exists() and not force:
        raise CloudOpsError("output-exists", f"Output already exists: {path}. Review it and rerun with --force to authorize replacement.", exit_code=4)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if deadline:
            deadline.check()
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if deadline:
                deadline.check()
            if path.exists() and not force:
                raise CloudOpsError("output-exists", f"Output appeared during processing: {path}. No file was replaced.", exit_code=4)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    except CloudOpsError:
        raise
    except OSError as exc:
        raise CloudOpsError("output-write-error", f"Could not write output {path}: {exc}", exit_code=4) from exc


def error_envelope(exc: CloudOpsError) -> dict[str, Any]:
    return {
        "status": "error",
        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        "external_execution_performed": False,
    }
