#!/usr/bin/env python3
"""Gate engine and editable-bundle exporter for the ultimate-game-maker Skill Tool.

Two responsibilities:

1. Evidence gates. Build an evidence template for a plan, then evaluate a filled
   evidence document against the policy in asset-gate-policy.json. A check only
   passes with real evidence text; waivers need a named approver; approval
   sensitive actions need approval records before a release gate can pass.
2. Bundle export. Write the editable production bundle (plan, checklist,
   handoff, evidence template, per-capability specs) into a target directory.
   Writes are guarded: a non-empty target needs explicit approval, everything is
   staged in a temporary directory and renamed only when complete, and the
   staging directory is removed on failure.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

SCHEMA_VERSION = "1.0"
GATE_PROFILES = ("draft", "milestone", "release")
MAX_EXPORT_FILES = 1200
WRITE_RETRIES = 3
WRITE_RETRY_DELAY_S = 0.05


class GateError(Exception):
    """Raised for gate/export problems; maps onto the CLI error envelope."""

    def __init__(self, code, message, hint=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
POLICY_KEYS = {"schema_version", "description", "approval_required_actions",
               "evidence", "gate_profiles", "default_profile"}
PROFILE_KEYS = {"description", "block_on", "treat_missing_as_failure", "allow_waivers", "require_approvals"}
ACTION_KEYS = {"id", "match", "reason", "approval_record"}
EVIDENCE_KEYS = {"accepted_statuses", "min_evidence_chars", "waiver_requires",
                 "min_waiver_reason_chars", "notes"}


def validate_policy(policy):
    """Check the policy document shape; returns a list of problems."""
    problems = []
    if not isinstance(policy, dict):
        return ["policy root must be an object"]
    missing = POLICY_KEYS - set(policy)
    for key in sorted(missing - {"description", "default_profile"}):
        problems.append("missing policy key '%s'" % key)
    extra = set(policy) - POLICY_KEYS
    for key in sorted(extra):
        problems.append("unexpected policy key '%s'" % key)
    for action in policy.get("approval_required_actions", []):
        for key in sorted(ACTION_KEYS - set(action)):
            problems.append("approval action '%s' missing key '%s'" % (action.get("id"), key))
        if not action.get("match"):
            problems.append("approval action '%s' has no match tokens" % action.get("id"))
    evidence = policy.get("evidence", {})
    for key in sorted(set(evidence) - EVIDENCE_KEYS):
        problems.append("unexpected evidence key '%s'" % key)
    profiles = policy.get("gate_profiles", {})
    for name in GATE_PROFILES:
        if name not in profiles:
            problems.append("missing gate profile '%s'" % name)
            continue
        for key in sorted(set(profiles[name]) - PROFILE_KEYS):
            problems.append("gate profile '%s' has unexpected key '%s'" % (name, key))
    return problems


def approval_policy(action, policy):
    """Map a capability approval action onto a policy rule (or None)."""
    for rule in policy.get("approval_required_actions", []):
        if action in rule.get("match", []):
            return rule
    for rule in policy.get("approval_required_actions", []):
        if any(token in action for token in rule.get("match", [])):
            return rule
    return None


# --------------------------------------------------------------------------- #
# Evidence template
# --------------------------------------------------------------------------- #
def evidence_template(plan, policy, profile="milestone"):
    checks = {}
    for item in plan["work_items"]:
        for check in item["checks"]:
            checks[check["id"]] = {
                "capability": item["capability"],
                "statement": check["statement"],
                "required": check["required"],
                "class": check["class"],
                "status": "missing",
                "evidence": "",
                "approver": "",
                "reason": "",
            }
    actions = [approval["action"] for approval in plan.get("approvals", [])]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "evidence",
        "plan_id": plan["plan_id"],
        "gate_profile": profile,
        "instructions": ("Set status to pass/fail/waiver/missing. 'pass' needs at least %d characters of "
                         "real evidence text. 'waiver' needs approver and reason. Delete nothing: the gate "
                         "treats unknown or absent checks as missing."
                         % policy.get("evidence", {}).get("min_evidence_chars", 12)),
        "approvals_required": actions,
        "approvals": [],
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# Gate evaluation
# --------------------------------------------------------------------------- #
def _blocked_classes(profile_conf, policy):
    block_on = profile_conf.get("block_on", [])
    if block_on == ["*"] or "*" in block_on:
        return "*"
    return {str(value) for value in block_on}


def evaluate(plan, evidence, policy, profile="milestone"):
    """Evaluate evidence against a plan. Returns a structured report."""
    if profile not in GATE_PROFILES:
        raise GateError("invalid_profile", "Unknown gate profile '%s'" % profile,
                        hint="Use one of: %s" % ", ".join(GATE_PROFILES))
    profile_conf = policy["gate_profiles"][profile]
    evidence_conf = policy.get("evidence", {})
    min_chars = int(evidence_conf.get("min_evidence_chars", 12))
    min_reason = int(evidence_conf.get("min_waiver_reason_chars", 12))
    allow_waivers = bool(profile_conf.get("allow_waivers", True))
    block_classes = _blocked_classes(profile_conf, policy)
    valid_statuses = set(evidence_conf.get("accepted_statuses", ["pass", "fail", "waiver", "missing"]))
    valid_statuses.update({"pass", "fail", "waiver", "missing"})
    treat_missing_as_failure = bool(profile_conf.get("treat_missing_as_failure", False))

    if not isinstance(evidence, dict):
        raise GateError("invalid_evidence", "Evidence document must be a JSON object",
                        hint="Start from the template: evidence-gate --plan <file> --template")
    provided_checks = evidence.get("checks")
    if not isinstance(provided_checks, dict):
        raise GateError("invalid_evidence", "Evidence document needs a 'checks' object",
                        hint="Start from the template: evidence-gate --plan <file> --template")

    warnings = []
    known_ids = {check["id"] for item in plan["work_items"] for check in item["checks"]}
    for unknown in sorted(set(provided_checks) - known_ids):
        warnings.append("evidence contains unknown check id '%s'" % unknown)
    if evidence.get("plan_id") and evidence["plan_id"] != plan["plan_id"]:
        warnings.append("evidence plan_id '%s' does not match plan '%s'"
                        % (evidence["plan_id"], plan["plan_id"]))

    rows = []
    counts = {"pass": 0, "fail": 0, "missing": 0, "waiver": 0, "optional_missing": 0}
    blocking = []
    for item in plan["work_items"]:
        for check in item["checks"]:
            entry = provided_checks.get(check["id"]) or {}
            status = str(entry.get("status", "missing")).strip().lower() or "missing"
            evidence_text = str(entry.get("evidence", "") or "").strip()
            approver = str(entry.get("approver", "") or "").strip()
            reason = str(entry.get("reason", "") or "").strip()
            note = ""
            if status not in valid_statuses:
                note = "unknown status '%s' treated as missing" % status
                status = "missing"
            if status == "pass" and len(evidence_text) < min_chars:
                status = "missing"
                note = "pass downgraded: evidence shorter than %d characters" % min_chars
            if status == "waiver":
                if not allow_waivers:
                    status = "fail"
                    note = "waivers are not allowed at the %s gate" % profile
                elif not approver or len(reason) < min_reason:
                    status = "fail"
                    note = "waiver needs approver and a reason of at least %d characters" % min_reason
            if status in ("pass", "fail", "waiver"):
                counts[status] += 1
            else:
                status = "missing"
                counts["missing" if check["required"] else "optional_missing"] += 1

            is_blocking = False
            if check["required"] and block_classes != set():
                if status == "fail":
                    is_blocking = True
                elif block_classes == "*":
                    is_blocking = status == "missing"
                elif check["class"] in block_classes:
                    is_blocking = status == "missing"
                if status == "missing" and treat_missing_as_failure:
                    is_blocking = True
            if is_blocking:
                blocking.append({"id": check["id"], "capability": item["capability"],
                                 "status": status, "class": check["class"], "note": note})
            rows.append({
                "id": check["id"],
                "work_item": item["id"],
                "capability": item["capability"],
                "statement": check["statement"],
                "required": check["required"],
                "class": check["class"],
                "status": status,
                "blocking": is_blocking,
                "note": note,
            })

    approvals_required = [approval["action"] for approval in plan.get("approvals", [])]
    approval_records = evidence.get("approvals") or []
    seen = {}
    for record in approval_records:
        action = str((record or {}).get("action", "")).strip()
        approver = str((record or {}).get("approver", "")).strip()
        if action and approver:
            seen[action] = {"action": action, "approver": approver,
                            "timestamp": record.get("timestamp", ""),
                            "note": record.get("note", "")}
    missing_approvals = [a for a in approvals_required if a not in seen]
    require_approvals = bool(profile_conf.get("require_approvals", False))
    approval_blocking = bool(missing_approvals) and (require_approvals or profile == "release")

    ok = not blocking and not approval_blocking
    report = {
        "ok": ok,
        "profile": profile,
        "plan_id": plan["plan_id"],
        "policy_version": policy.get("schema_version"),
        "counts": counts,
        "required_checks": sum(1 for row in rows if row["required"]),
        "blocking": blocking,
        "approvals": {
            "required": approvals_required,
            "satisfied": sorted(seen),
            "missing": missing_approvals,
            "blocking": approval_blocking,
        },
        "checks": rows,
        "warnings": warnings,
        "next_step": ("fix the blocking checks or add approval records, then rerun the gate"
                      if not ok else "gate passed; attach this report to the handoff"),
    }
    return report


def render_report_text(report):
    lines = ["Gate report - profile: %s - plan: %s" % (report["profile"], report["plan_id"][:12]), ""]
    counts = report["counts"]
    lines.append("Result: %s" % ("PASS" if report["ok"] else "BLOCKED"))
    lines.append("Checks: %d pass, %d fail, %d waiver, %d missing, %d optional missing" % (
        counts["pass"], counts["fail"], counts["waiver"], counts["missing"], counts["optional_missing"]))
    if report["blocking"]:
        lines.append("")
        lines.append("Blocking:")
        for entry in report["blocking"]:
            lines.append("  - %s (%s, %s): %s%s" % (
                entry["id"], entry["capability"], entry["status"], entry["class"],
                " - " + entry["note"] if entry["note"] else ""))
    approvals = report["approvals"]
    if approvals["required"]:
        lines.append("")
        lines.append("Approvals: %d required, %d satisfied, %d missing" % (
            len(approvals["required"]), len(approvals["satisfied"]), len(approvals["missing"])))
        for action in approvals["missing"]:
            lines.append("  - missing approval: %s" % action)
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for warning in report["warnings"]:
            lines.append("  - %s" % warning)
    lines.append("")
    lines.append("Next: %s" % report["next_step"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _write_text(path, text):
    """Write UTF-8 text with bounded retries; raises GateError after the last try."""
    last_error = None
    for attempt in range(1, WRITE_RETRIES + 1):
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return len(text.encode("utf-8"))
        except OSError as exc:  # transient lock, full disk, permissions
            last_error = exc
            time.sleep(WRITE_RETRY_DELAY_S * attempt)
    raise GateError("write_failed", "Could not write %s after %d attempts: %s"
                    % (Path(path).name, WRITE_RETRIES, last_error),
                    hint="Check free space, directory permissions, and whether the file is locked.")


def plan_export_files(plan, planner, policy, include_specs=True, profile="milestone"):
    """Return [(relative path, content)] for the editable bundle."""
    files = [
        ("plan.json", json.dumps(plan, indent=2, sort_keys=False) + "\n"),
        ("plan.md", planner.render_plan_md(plan)),
        ("checklist.md", planner.render_checklist_md(plan)),
        ("handoff.md", planner.render_handoff_md(plan)),
        ("evidence.json", json.dumps(evidence_template(plan, policy, profile), indent=2) + "\n"),
    ]
    if include_specs:
        for item in plan["work_items"]:
            files.append((item["spec_path"], planner.render_spec_md(item, plan)))
    return files


def export_bundle(plan, planner, policy, out_dir, approve=False, dry_run=False,
                  include_specs=True, overwrite=False, deadline=None, profile="milestone"):
    """Write the editable production bundle atomically."""
    target = Path(out_dir)
    if not str(target).strip():
        raise GateError("invalid_argument", "--out must name an output directory")
    files = plan_export_files(plan, planner, policy, include_specs, profile)
    if len(files) > MAX_EXPORT_FILES:
        raise GateError("too_many_files",
                        "Bundle would contain %d files, above the %d limit" % (len(files), MAX_EXPORT_FILES),
                        hint="Reduce --limit or disable specs with --no-specs.")
    if target.exists() and not target.is_dir():
        raise GateError("invalid_output", "Output path exists and is not a directory: %s" % target)
    populated = target.is_dir() and any(target.iterdir())
    if populated and not (approve or overwrite):
        raise GateError(
            "approval_required",
            "Output directory %s already contains files" % target,
            hint="Re-run with --approve to replace its contents, or choose a new --out directory.",
            details={"path": str(target)},
        )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "output_dir": str(target),
            "plan_id": plan["plan_id"],
            "files": [{"path": str(target / rel), "bytes": len(text.encode("utf-8"))}
                      for rel, text in files],
            "would_replace": populated,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ugm-export-", dir=str(target.parent)))
    written = []
    try:
        for relative, text in files:
            if deadline is not None and time.monotonic() > deadline:
                raise GateError("timeout", "Export exceeded the configured time budget",
                                hint="Raise --timeout or export fewer capabilities.")
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = _write_text(destination, text)
            written.append({"path": relative, "bytes": size})
        # prove the JSON artifacts are parseable before publishing the directory
        for name in ("plan.json",):
            with open(staging / name, "r", encoding="utf-8") as handle:
                json.load(handle)
        if populated:
            shutil.rmtree(str(target))
        os.replace(str(staging), str(target))
    except Exception as exc:  # noqa: BLE001 - clean up then re-raise with context
        shutil.rmtree(str(staging), ignore_errors=True)
        if isinstance(exc, GateError):
            raise
        raise GateError("export_failed", "Export failed and was rolled back: %s" % exc,
                        hint="The previous output directory was preserved when removal had not started.")
    return {
        "ok": True,
        "dry_run": False,
        "output_dir": str(target),
        "plan_id": plan["plan_id"],
        "file_count": len(written),
        "total_bytes": sum(entry["bytes"] for entry in written),
        "files": written,
        "verify_commands": [
            "python3 ultimate-game-maker/tools-game-maker.py evidence-gate --plan %s/plan.json --evidence %s/evidence.json --profile %s"
            % (target, target, profile),
        ],
        "note": "evidence.json is a template with every check set to 'missing'; fill it before gating.",
    }
