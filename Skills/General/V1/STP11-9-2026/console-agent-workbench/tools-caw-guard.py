#!/usr/bin/env python3
"""caw_guard - legal guardrails for the console-agent-workbench Skill Tool.

Implements the catalog's foundation skills:

* Lawful Ownership and Authorization Gate
* Restricted Material Boundary Enforcer
* Artifact Minimization Policy / Private SDK Data Redactor
* approval tokens for destructive or security-sensitive actions

All logic is local, deterministic, and standard-library only. Nothing here
downloads, decrypts, extracts, or redistributes material; it classifies,
blocks, redacts, and records operator attestations.
"""

from __future__ import annotations

import datetime as _dt
import os
import re

import caw_runtime as rt

POLICY_FILENAME = "asset-guard-policy.json"

DEFAULT_VERDICT_BY_MODE = {
    "package": {"block": ("critical", "high"), "review": ("medium", "low")},
    "redistribute": {"block": ("critical", "high"), "review": ("medium",)},
    "bundle": {"block": ("critical",), "review": ("high", "medium", "low")},
    "report": {"block": (), "review": ("critical", "high", "medium", "low")},
}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
_REQUIRED_POLICY_KEYS = (
    "policy_id",
    "file_rules",
    "content_rules",
    "redaction_rules",
    "approval_required_actions",
    "retention",
)


def _compile_rule(rule):
    rule = dict(rule)
    kind = rule.get("kind")
    if kind in ("filename_pattern", "content_regex"):
        try:
            rule["_regex"] = re.compile(rule["pattern"])
        except (KeyError, re.error) as exc:
            raise rt.ValidationError(f"policy rule {rule.get('id')!r} has an invalid pattern: {exc}")
    elif kind == "extension":
        values = rule.get("values") or []
        if not values:
            raise rt.ValidationError(f"policy rule {rule.get('id')!r} has no extensions")
        rule["_extensions"] = {v.lower() if v.startswith(".") else "." + v.lower() for v in values}
    elif kind == "size_threshold":
        rule["_min_bytes"] = int(rule.get("min_bytes", 0))
    elif kind != "always":
        raise rt.ValidationError(f"policy rule {rule.get('id')!r} has unknown kind {kind!r}")
    return rule


def load_policy(path=None):
    """Load and validate the guard policy. Falls back to the packaged policy."""
    path = path or rt.resource_default(POLICY_FILENAME)
    raw = rt.load_json(path, "guard policy")
    missing = [k for k in _REQUIRED_POLICY_KEYS if k not in raw]
    if missing:
        raise rt.ValidationError("guard policy is missing required keys",
                                 [f"missing: {', '.join(missing)}"])
    policy = dict(raw)
    policy["_path"] = os.path.abspath(rt.resolve_input(path))
    policy["file_rules"] = [_compile_rule(r) for r in raw["file_rules"]]
    policy["content_rules"] = [_compile_rule(r) for r in raw["content_rules"]]
    redactions = []
    for rule in raw["redaction_rules"]:
        item = dict(rule)
        try:
            item["_regex"] = re.compile(item["pattern"])
        except (KeyError, re.error) as exc:
            raise rt.ValidationError(f"redaction rule {item.get('id')!r} is invalid: {exc}")
        redactions.append(item)
    policy["redaction_rules"] = redactions
    return policy


def policy_summary(policy):
    return {
        "policy_id": policy["policy_id"],
        "policy_version": policy.get("schema_version", "1.0"),
        "source": policy["_path"],
        "file_rules": len(policy["file_rules"]),
        "content_rules": len(policy["content_rules"]),
        "redaction_rules": len(policy["redaction_rules"]),
        "approval_required_actions": policy["approval_required_actions"],
        "materials_requiring_authorization": policy.get("authorization_materials", []),
    }


# ---------------------------------------------------------------------------
# Restricted material boundary scan
# ---------------------------------------------------------------------------
def verdict_for(policy, mode, severities):
    sev = set(severities)
    table = policy.get("verdicts_by_mode", DEFAULT_VERDICT_BY_MODE).get(mode)
    if table is None:
        raise rt.ValidationError(f"unknown scan mode {mode!r}",
                                 [f"choose from {', '.join(DEFAULT_VERDICT_BY_MODE)}"])
    if sev & set(table["block"]):
        return "block"
    if sev & set(table["review"]):
        return "review"
    return "clear"


# Magic numbers used to disambiguate extensions that collide with ordinary files
# (for example ".md" is both a Genesis image and a Markdown document).
CONSOLE_SIGNATURES = (
    b"NES\x1a",          # iNES / NES 2.0
    b"FDS\x1a",          # Famicom Disk System with copier header
    b"\x01*NINTENDO-HVC*",  # raw FDS disk
    b"\x24\xff\xae\x51",  # Game Boy Advance logo head
    b"\x80\x37\x12\x40",  # Nintendo 64 (big endian)
    b"\x37\x80\x40\x12",  # Nintendo 64 (byte swapped)
    b"\x40\x12\x37\x80",  # Nintendo 64 (little endian)
    b"CD001",             # ISO 9660 volume descriptor
)


def _signature_matches(sample):
    """True when the sample carries a documented console media signature."""
    if not sample:
        return False
    for magic in CONSOLE_SIGNATURES:
        if magic in sample[:4096] or magic in sample[0x8000:0x8008]:
            return True
    if sample[0x100:0x104] == b"SEGA":
        return True
    if len(sample) > 0x134 and sample[0x104:0x108] == bytes.fromhex("CEED6666"):
        return True
    return False


def _match_file_rules(policy, path, rel, size, sample=b""):
    hits = []
    name = os.path.basename(path)
    lower = name.lower()
    for rule in policy["file_rules"]:
        kind = rule["kind"]
        if kind == "filename_pattern" and rule["_regex"].search(name):
            hits.append((rule, "filename matches"))
        elif kind == "extension":
            ext = os.path.splitext(lower)[1]
            if ext in rule["_extensions"]:
                ambiguous = rule.get("ambiguous_extensions") or []
                if ext in ambiguous and not _signature_matches(sample):
                    hits.append((dict(rule, severity="low"),
                                 f"extension {ext} is ambiguous and no console signature matched"))
                else:
                    hits.append((rule, f"extension {ext}"))
        elif kind == "always":
            hits.append((rule, "rule applies to all files"))
        elif kind == "size_threshold" and size >= rule["_min_bytes"]:
            hits.append((rule, f"size {size} >= {rule['_min_bytes']}"))
    return hits


def _read_sample(path, max_bytes):
    try:
        with open(path, "rb") as fh:
            return fh.read(max_bytes), ""
    except OSError as exc:
        return b"", f"unreadable: {exc}"


def _match_content_rules(policy, sample):
    hits = []
    text = sample.decode("utf-8", "replace")
    for rule in policy["content_rules"]:
        if rule["kind"] == "content_regex" and rule["_regex"].search(text):
            hits.append((rule, "content signature found"))
    return hits


def scan(paths, policy, mode="bundle", limits=None, timeout_seconds=None):
    """Scan one or more paths for restricted material.

    Reads at most ``max_read_bytes`` per file, visits at most ``max_files``
    entries, honours symlink policy, and returns structured findings.
    """
    import time as _time

    limits = dict(rt.DEFAULT_LIMITS, **(limits or {}))
    deadline = _time.monotonic() + float(timeout_seconds or limits["overall_timeout_seconds"])
    findings = []
    skipped = []
    scanned = 0
    seen_files = 0
    for raw_path in paths:
        root = rt.require_existing(raw_path, "scan path")
        for abs_path, rel, depth in rt.walk_bounded(
                root, max_files=limits["max_files"], max_depth=limits["max_depth"],
                follow_symlinks=bool(limits.get("follow_symlinks"))):
            if _time.monotonic() > deadline:
                skipped.append({"path": rel, "reason": "overall scan deadline reached"})
                break
            seen_files += 1
            if seen_files > limits["max_files"]:
                skipped.append({"path": rel, "reason": f"max_files limit {limits['max_files']} reached"})
                break
            try:
                size = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
            except OSError:
                size = 0
            if os.path.islink(abs_path):
                findings.append({
                    "path": rel, "root": root, "rule_id": "symlink-skipped",
                    "severity": "medium", "category": "link",
                    "reason": "symlink not followed during boundary scan",
                    "remediation": "scan the resolved target explicitly if it is in scope",
                    "size": size,
                })
                continue
            scanned += 1
            exempt = policy.get("scan_exempt_filenames") or []
            if os.path.basename(abs_path) in exempt:
                skipped.append({"path": rel,
                                "reason": "excluded by policy scan_exempt_filenames "
                                          "(packaged documentation source)"})
                continue
            sample, content_note = _read_sample(abs_path, limits["max_read_bytes"])
            hits = _match_file_rules(policy, abs_path, rel, size, sample)
            content_hits = _match_content_rules(policy, sample)
            hits.extend(content_hits)
            for rule, why in hits:
                findings.append({
                    "path": rel,
                    "root": root,
                    "rule_id": rule["id"],
                    "severity": rule["severity"],
                    "category": rule.get("category", "restricted-material"),
                    "reason": f"{why}: {rule.get('reason', '')}".strip(": "),
                    "remediation": rule.get("remediation", ""),
                    "size": size,
                })
            if content_note:
                skipped.append({"path": rel, "reason": content_note})
    severities = [f["severity"] for f in findings if f["severity"] != "low"]
    verdict = verdict_for(policy, mode, severities)
    by_rule = {}
    for f in findings:
        by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1
    return {
        "mode": mode,
        "verdict": verdict,
        "policy_id": policy["policy_id"],
        "roots": [rt.resolve_input(p) for p in paths],
        "files_scanned": scanned,
        "finding_count": len(findings),
        "findings_by_rule": by_rule,
        "severities": {s: sum(1 for f in findings if f["severity"] == s)
                       for s in ("critical", "high", "medium", "low")},
        "findings": findings[:500],
        "findings_truncated": max(0, len(findings) - 500),
        "skipped": skipped[:200],
        "limits": {k: limits[k] for k in ("max_files", "max_depth", "max_read_bytes")},
    }


# ---------------------------------------------------------------------------
# Redaction (artifact minimization / private SDK data redactor)
# ---------------------------------------------------------------------------
def redact_text(text, policy, extra_patterns=None):
    hits = {}
    out = text
    rules = list(policy["redaction_rules"])
    for pattern, replacement, rid in extra_patterns or []:
        rules.append({"id": rid, "pattern": pattern, "replacement": replacement,
                      "_regex": re.compile(pattern)})
    for rule in rules:
        replacement = rule.get("replacement", "<REDACTED>")
        out, count = rule["_regex"].subn(replacement, out)
        if count:
            hits[rule["id"]] = hits.get(rule["id"], 0) + count
    return out, hits


def redact_obj(obj, policy):
    """Recursively redact every string in a JSON-compatible structure."""
    totals = {}

    def walk(node):
        if isinstance(node, str):
            clean, hits = redact_text(node, policy)
            for k, v in hits.items():
                totals[k] = totals.get(k, 0) + v
            return clean
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(obj), totals


def redact_file(input_path, output_path, policy, check_only=False):
    src = rt.require_existing(input_path, "input", kind="file")
    with open(src, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise rt.ValidationError("redact supports UTF-8 text or JSON files only",
                                 ["binary media is never rewritten by this tool"])
    clean, hits = redact_text(text, policy)
    result = {
        "input": src,
        "output": None if check_only else (output_path or None),
        "characters": len(text),
        "hits": hits,
        "total_hits": sum(hits.values()),
        "clean": sum(hits.values()) == 0,
    }
    if not check_only:
        if not output_path:
            raise rt.ValidationError("--out is required unless --check is used")
        result["output"] = rt.write_text(output_path, clean)
    return result


# ---------------------------------------------------------------------------
# Authorization gate
# ---------------------------------------------------------------------------
ATTESTATION_TEXT = (
    "I confirm I lawfully own or am authorized to use every listed material, "
    "that no restricted material will be bundled or disclosed, and that "
    "official SDKs, devkits, and platform services are used only under the "
    "terms of their programs."
)


def _sign_record(record, hash_field):
    body = {k: v for k, v in record.items() if k != hash_field}
    record[hash_field] = rt.sha256_obj(body)
    return record


def _check_record_hash(record, hash_field):
    stored = record.get(hash_field)
    body = {k: v for k, v in record.items() if k != hash_field}
    return stored == rt.sha256_obj(body)


def create_authorization(operator, items, statement=None, ttl_hours=24, notes="",
                         attest=False, requires=None):
    """Build a signed authorization record from operator-supplied materials."""
    if not attest:
        raise rt.ValidationError(
            "authorization requires --attest (the operator must explicitly confirm lawful ownership)")
    if not operator or not operator.strip():
        raise rt.ValidationError("--operator is required")
    if not items:
        raise rt.ValidationError("at least one --material type:reference entry is required",
                                 ["example: --material media:owner-supplied-cartridge-1"])
    normalized = []
    for raw in items:
        if ":" not in raw:
            raise rt.ValidationError(f"material must use type:reference form: {raw!r}")
        kind, ref = raw.split(":", 1)
        kind, ref = kind.strip().lower(), ref.strip()
        if not kind or not ref:
            raise rt.ValidationError(f"material has an empty type or reference: {raw!r}")
        normalized.append({"material": kind, "reference": ref})
    ttl = rt.clamp_int(ttl_hours, "ttl_hours", 1, 24 * 365)
    now = rt.utc_now()
    record = {
        "record_type": "authorization",
        "record_id": rt.new_id("authz"),
        "operator": operator.strip(),
        "created_at": rt.iso(now),
        "expires_at": rt.iso(now + _dt.timedelta(hours=ttl)),
        "attestation": ATTESTATION_TEXT,
        "statement": (statement or "").strip(),
        "items": normalized,
        "required_materials": sorted(requires or []),
        "notes": notes.strip(),
        "tool": f"{rt.SKILL_NAME} {rt.VERSION}",
    }
    return _sign_record(record, "record_sha256")


def verify_authorization(record, required=None, now=None):
    problems = []
    if not isinstance(record, dict) or record.get("record_type") != "authorization":
        return {"valid": False, "problems": ["record_type is not 'authorization'"], "covered": []}
    if not _check_record_hash(record, "record_sha256"):
        problems.append("record_sha256 mismatch: record was modified after signing")
    now = now or rt.utc_now()
    try:
        expires = rt.parse_iso(record.get("expires_at"))
        created = rt.parse_iso(record.get("created_at"))
    except rt.ValidationError as exc:
        problems.append(str(exc))
        expires = created = None
    if expires and now > expires:
        problems.append(f"authorization expired at {record.get('expires_at')}")
    if created and expires and expires < created:
        problems.append("expires_at is before created_at")
    covered = sorted({i.get("material") for i in record.get("items", []) if i.get("material")})
    missing = sorted(set(required or []) - set(covered))
    if missing:
        problems.append("authorization does not cover required materials: " + ", ".join(missing))
    return {
        "valid": not problems,
        "problems": problems,
        "record_id": record.get("record_id"),
        "operator": record.get("operator"),
        "expires_at": record.get("expires_at"),
        "covered": covered,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Approval tokens for destructive / security-sensitive actions
# ---------------------------------------------------------------------------
MAX_SCOPE_ENTRIES = 20000


def normalize_scope(entries):
    """Normalize a scope of ``{path, size}`` entries (relative, sorted)."""
    return sorted(
        ({"path": str(e["path"]).replace(os.sep, "/"), "size": int(e.get("size", 0))}
         for e in entries),
        key=lambda e: (e["path"], e["size"]),
    )


def scope_digest(entries):
    """Digest a scope of ``{path, size}`` entries (relative, sorted)."""
    normalized = normalize_scope(entries)
    return rt.sha256_obj(normalized), len(normalized)


def create_approval(policy, action, entries, operator, ttl_minutes=30, note=""):
    if action not in policy["approval_required_actions"]:
        raise rt.ValidationError(
            f"action {action!r} is not a gated action in this policy",
            ["gated actions: " + ", ".join(policy["approval_required_actions"])])
    if not operator or not operator.strip():
        raise rt.ValidationError("--operator is required to issue an approval")
    normalized = normalize_scope(entries)
    digest, count = scope_digest(normalized)
    if count == 0:
        raise rt.ValidationError("approval scope must contain at least one path")
    if count > MAX_SCOPE_ENTRIES:
        raise rt.ValidationError(
            f"approval scope has {count} entries; split the scope into smaller batches",
            [f"maximum {MAX_SCOPE_ENTRIES} entries per approval"])
    ttl = rt.clamp_int(ttl_minutes, "ttl_minutes", 1, 60 * 24 * 30)
    now = rt.utc_now()
    record = {
        "record_type": "approval",
        "approval_id": rt.new_id("appr"),
        "action": action,
        "operator": operator.strip(),
        "created_at": rt.iso(now),
        "expires_at": rt.iso(now + _dt.timedelta(minutes=ttl)),
        "scope": {"entries": count, "scope_sha256": digest,
                  "entry_list": normalized[:MAX_SCOPE_ENTRIES]},
        "note": note.strip(),
        "policy_id": policy["policy_id"],
        "tool": f"{rt.SKILL_NAME} {rt.VERSION}",
    }
    return _sign_record(record, "payload_sha256")


def verify_approval(policy, record, action, entries, now=None):
    problems = []
    if not isinstance(record, dict) or record.get("record_type") != "approval":
        return {"valid": False, "problems": ["record_type is not 'approval'"]}
    if not _check_record_hash(record, "payload_sha256"):
        problems.append("payload_sha256 mismatch: approval payload was modified")
    if record.get("action") != action:
        problems.append(f"approval is for action {record.get('action')!r}, not {action!r}")
    if record.get("policy_id") and record["policy_id"] != policy["policy_id"]:
        problems.append("approval was issued under a different policy")
    now = now or rt.utc_now()
    try:
        if now > rt.parse_iso(record.get("expires_at")):
            problems.append(f"approval expired at {record.get('expires_at')}")
    except rt.ValidationError as exc:
        problems.append(str(exc))
    scope = record.get("scope") or {}
    approved = scope.get("entry_list") or []
    recomputed, approved_count = scope_digest(approved)
    if approved and scope.get("scope_sha256") != recomputed:
        problems.append("approval scope hash does not match the approved entry list "
                        "(the token was modified)")
    approved_keys = {(e["path"], e["size"]) for e in approved}
    targets = normalize_scope(entries)
    uncovered = [t for t in targets if (t["path"], t["size"]) not in approved_keys]
    if uncovered:
        preview = ", ".join(f"{t['path']} ({t['size']} B)" for t in uncovered[:3])
        problems.append(
            f"{len(uncovered)} of {len(targets)} target(s) are not covered by the approved scope "
            f"[{preview}{'…' if len(uncovered) > 3 else ''}]; "
            f"{approved_count} entries were approved")
    return {
        "valid": not problems,
        "problems": problems,
        "approval_id": record.get("approval_id"),
        "operator": record.get("operator"),
        "action": record.get("action"),
        "expires_at": record.get("expires_at"),
        "scope_entries": len(targets),
        "approved_entries": approved_count,
    }


def require_approval(policy, action, token_path, entries):
    """Raise unless a valid approval token covers *action* over *entries*."""
    if action not in policy["approval_required_actions"]:
        return {"required": False, "action": action}
    if not token_path:
        raise rt.ApprovalRequired(
            f"action {action!r} requires an approval token; issue one with "
            f"`gate --action approve --gated-action {action}`", action)
    record = rt.load_json(token_path, "approval token")
    result = verify_approval(policy, record, action, entries)
    if not result["valid"]:
        raise rt.ApprovalRequired(
            "approval token rejected: " + "; ".join(result["problems"]), action,
            problems=result["problems"])
    result["required"] = True
    return result
