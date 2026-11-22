#!/usr/bin/env python3
"""tools-safety-engine.py

Safety, scope, urgency, and privacy checks for the HumanSphere Holistic Support
Skill Tool. All rules are data-driven from:
    humansphere-holistic-support/asset-safety-policy.json

The engine never contacts the network and never executes external code; it only
scans bounded text with the regular expressions declared in the policy.

Relative path from the future skill root:
    humansphere-holistic-support/tools-safety-engine.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_SCAN_CHARS = 200_000
SEVERITY_ORDER = {"info": 0, "warn": 1, "block": 2}
DEFAULT_LEVELS = ["none", "elevated", "urgent", "critical"]


class SafetyError(Exception):
    """Structured error for policy problems and invalid review input."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict:
        out = {"status": "error", "code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out


# ---------------------------------------------------------------------------
# Policy loading and validation
# ---------------------------------------------------------------------------
def load_policy(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise SafetyError(
            "policy_missing",
            f"Safety policy not found: {path}",
            "Pass --policy with a valid JSON policy, or use the shipped "
            "humansphere-holistic-support/asset-safety-policy.json.",
        )
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SafetyError(
            "policy_invalid_json",
            f"Safety policy is not valid JSON: {path} ({exc.msg} at line {exc.lineno}).",
        ) from exc
    problems = validate_policy(policy)
    errors = [p for p in problems if p["severity"] == "error"]
    if errors:
        raise SafetyError(
            "policy_invalid",
            "Safety policy failed validation: " + "; ".join(p["message"] for p in errors),
            "Fix the reported keys or restore the shipped policy file.",
        )
    return compile_policy(policy)


def validate_policy(policy: dict) -> list[dict]:
    """Structural + regex validation. Returns findings, not exceptions."""
    problems: list[dict] = []

    def report(severity: str, message: str) -> None:
        problems.append({"severity": severity, "message": message})

    if not isinstance(policy, dict):
        report("error", "Policy root must be a JSON object.")
        return problems
    for key in ("urgency", "response_rules", "conditional_rules", "pii_patterns"):
        if key not in policy:
            report("error", f"Missing required section: {key}")
    urgency = policy.get("urgency") or {}
    levels = urgency.get("levels") or {}
    for name, spec in levels.items():
        if not isinstance(spec, dict) or "rank" not in spec:
            report("error", f"Urgency level '{name}' needs an integer 'rank'.")
        elif not isinstance(spec["rank"], int):
            report("error", f"Urgency level '{name}' rank must be an integer.")
    for signal in urgency.get("signals") or []:
        if signal.get("level") not in levels:
            report("error", f"Signal '{signal.get('id')}' uses unknown level '{signal.get('level')}'.")
        for pattern in signal.get("patterns") or []:
            _check_regex(pattern, f"signal {signal.get('id')}", report)
    for rule in policy.get("response_rules") or []:
        if rule.get("severity") not in SEVERITY_ORDER:
            report("error", f"Rule '{rule.get('id')}' has unknown severity '{rule.get('severity')}'.")
        for pattern in rule.get("patterns") or []:
            _check_regex(pattern, f"rule {rule.get('id')}", report)
    for rule in policy.get("conditional_rules") or []:
        if rule.get("severity") not in SEVERITY_ORDER:
            report("error", f"Conditional rule '{rule.get('id')}' has unknown severity.")
        if rule.get("type") not in ("escalation_missing", "pii_present"):
            report("error", f"Conditional rule '{rule.get('id')}' has unsupported type.")
    for pii in policy.get("pii_patterns") or []:
        _check_regex(pii.get("pattern"), f"pii {pii.get('id')}", report)
    if not problems:
        report("info", "Policy structure and all regular expressions are valid.")
    return problems


def _check_regex(pattern, label: str, report) -> None:
    if not isinstance(pattern, str) or not pattern.strip():
        report("error", f"{label} has an empty pattern.")
        return
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        report("error", f"{label} has an invalid regular expression: {exc}.")


def compile_policy(policy: dict) -> dict:
    """Attach compiled regexes under '_compiled' so scans stay cheap."""
    compiled = dict(policy)
    urgency = dict(policy.get("urgency") or {})
    signals = []
    for signal in urgency.get("signals") or []:
        item = dict(signal)
        item["_compiled"] = [re.compile(p, re.IGNORECASE) for p in signal.get("patterns") or []]
        signals.append(item)
    urgency["signals"] = signals
    compiled["urgency"] = urgency
    rules = []
    for rule in policy.get("response_rules") or []:
        item = dict(rule)
        item["_compiled"] = [re.compile(p, re.IGNORECASE) for p in rule.get("patterns") or []]
        rules.append(item)
    compiled["response_rules"] = rules
    pii = []
    for entry in policy.get("pii_patterns") or []:
        item = dict(entry)
        item["_compiled"] = re.compile(entry["pattern"], re.IGNORECASE)
        pii.append(item)
    compiled["pii_patterns"] = pii
    compiled["_levels"] = urgency.get("levels") or {}
    return compiled


def _bounded(text: str) -> str:
    text = str(text or "")
    return text[:MAX_SCAN_CHARS]


# ---------------------------------------------------------------------------
# Urgency assessment
# ---------------------------------------------------------------------------
def assess_urgency(text: str, policy: dict) -> dict:
    """Detect crisis/urgent/watch signals and return required actions."""
    text = _bounded(text)
    levels = policy.get("_levels") or (policy.get("urgency") or {}).get("levels") or {}
    matched: list[dict] = []
    top_level = "none"
    top_rank = 0
    for signal in (policy.get("urgency") or {}).get("signals") or []:
        compiled = signal.get("_compiled") or [
            re.compile(p, re.IGNORECASE) for p in signal.get("patterns") or []
        ]
        evidence = []
        for regex in compiled:
            found = regex.search(text)
            if found:
                evidence.append(found.group(0).strip())
                if len(evidence) >= 3:
                    break
        if evidence:
            level = signal.get("level", "elevated")
            rank = int((levels.get(level) or {}).get("rank", 0))
            matched.append(
                {
                    "signal_id": signal.get("id"),
                    "level": level,
                    "label": signal.get("label", ""),
                    "evidence": evidence,
                }
            )
            if rank > top_rank:
                top_rank = rank
                top_level = level
    spec = levels.get(top_level) or {"label": "Unknown", "required_actions": [], "rank": 0}
    escalation_text = (policy.get("urgency") or {}).get("escalation_text") or {}
    return {
        "level": top_level,
        "rank": top_rank,
        "label": spec.get("label", ""),
        "required_actions": list(spec.get("required_actions") or []),
        "signals": matched,
        "escalation_required": top_rank >= 1,
        "human_handoff_required": top_rank >= 2,
        "escalation_text": escalation_text.get(top_level, ""),
    }


def requires_human_review(urgency: dict) -> bool:
    return bool(urgency.get("human_handoff_required"))


# ---------------------------------------------------------------------------
# Response review
# ---------------------------------------------------------------------------
def review_response(text: str, policy: dict, user_text: str = "") -> dict:
    """Check a drafted response for harm, missed urgency, false authority, and PII."""
    if not str(text or "").strip():
        raise SafetyError(
            "empty_response",
            "No response text was supplied for review.",
            "Pass --text 'draft' or --input-file path/to/draft.md.",
        )
    text_bounded = _bounded(text)
    findings: list[dict] = []

    for rule in policy.get("response_rules") or []:
        compiled = rule.get("_compiled") or [
            re.compile(p, re.IGNORECASE) for p in rule.get("patterns") or []
        ]
        evidence = []
        for regex in compiled:
            for found in regex.finditer(text_bounded):
                evidence.append(found.group(0).strip())
                if len(evidence) >= 3:
                    break
            if len(evidence) >= 3:
                break
        if evidence:
            findings.append(
                _finding(rule["id"], rule["severity"], rule.get("title", ""), evidence,
                         rule.get("remediation", ""))
            )

    urgency = assess_urgency(user_text or text_bounded, policy)
    for rule in policy.get("conditional_rules") or []:
        kind = rule.get("type")
        if kind == "escalation_missing":
            threshold = int(
                ((policy.get("_levels") or {}).get(rule.get("applies_from_level")) or {}).get("rank", 99)
            )
            if urgency["rank"] >= threshold:
                haystack = text_bounded.lower()
                has_escalation = any(token.lower() in haystack for token in rule.get("require_any") or [])
                if not has_escalation:
                    findings.append(
                        _finding(
                            rule["id"], rule["severity"], rule.get("title", ""),
                            [f"user urgency level: {urgency['level']}"],
                            rule.get("remediation", ""),
                        )
                    )
        elif kind == "pii_present":
            hits = _pii_hits(text_bounded, policy)
            if hits:
                findings.append(
                    _finding(
                        rule["id"], rule["severity"], rule.get("title", ""),
                        [f"{hit['label']}: {hit['value']}" for hit in hits[:3]],
                        rule.get("remediation", ""),
                    )
                )

    block = [f for f in findings if f["severity"] == "block"]
    warn = [f for f in findings if f["severity"] == "warn"]
    verdict = "fail" if block else ("warn" if warn else "pass")
    return {
        "status": "ok",
        "verdict": verdict,
        "exit_code": 2 if verdict == "fail" else (1 if verdict == "warn" else 0),
        "counts": {"block": len(block), "warn": len(warn), "total": len(findings)},
        "findings": findings,
        "urgency": urgency,
        "reviewed_characters": len(text_bounded),
        "checklist": list(policy.get("review_checklist") or []),
    }


def _finding(rule_id: str, severity: str, title: str, evidence: list[str], remediation: str) -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "remediation": remediation,
    }


# ---------------------------------------------------------------------------
# Privacy helpers and governance metadata
# ---------------------------------------------------------------------------
def _pii_hits(text: str, policy: dict) -> list[dict]:
    hits: list[dict] = []
    for entry in policy.get("pii_patterns") or []:
        regex = entry.get("_compiled") or re.compile(entry["pattern"], re.IGNORECASE)
        for found in regex.finditer(text):
            value = found.group(0).strip()
            if entry.get("id") == "PII-PHONE" and len(re.sub(r"\D", "", value)) < 9:
                continue
            if entry.get("id") == "PII-CARD":
                digits = re.sub(r"\D", "", value)
                if not 13 <= len(digits) <= 19:
                    continue
            hits.append({"id": entry.get("id"), "label": entry.get("label", ""), "value": value})
            if len(hits) >= 20:
                return hits
    return hits


def redact_pii(text: str, policy: dict) -> dict:
    """Mask identifiers in bounded text; used before handoff summaries are written."""
    text = _bounded(text)
    counts: dict[str, int] = {}
    redacted = text
    for entry in policy.get("pii_patterns") or []:
        regex = entry.get("_compiled") or re.compile(entry["pattern"], re.IGNORECASE)
        mask = entry.get("mask", "[masked]")

        def _mask(match, mask=mask, entry=entry):
            value = match.group(0)
            if entry.get("id") == "PII-PHONE" and len(re.sub(r"\D", "", value)) < 9:
                return value
            if entry.get("id") == "PII-CARD":
                digits = re.sub(r"\D", "", value)
                if not 13 <= len(digits) <= 19:
                    return value
            counts[entry.get("label", entry.get("id"))] = counts.get(
                entry.get("label", entry.get("id")), 0
            ) + 1
            return mask

        redacted = regex.sub(_mask, redacted)
    return {"text": redacted, "redactions": counts, "total": sum(counts.values())}


def scope_boundaries(policy: dict) -> list[dict]:
    return [dict(item) for item in policy.get("scope_boundaries") or []]


def approval_gates(policy: dict) -> list[dict]:
    return [dict(item) for item in policy.get("approval_gates") or []]


def governance_summary(policy: dict) -> dict:
    return {
        "policy_version": policy.get("version", ""),
        "title": policy.get("title", ""),
        "urgency_levels": [
            {
                "level": name,
                "rank": spec.get("rank", 0),
                "label": spec.get("label", ""),
                "required_actions": list(spec.get("required_actions") or []),
            }
            for name, spec in sorted(
                (policy.get("_levels") or {}).items(), key=lambda kv: kv[1].get("rank", 0)
            )
        ],
        "response_rules": [
            {"rule_id": r.get("id"), "severity": r.get("severity"), "title": r.get("title", "")}
            for r in policy.get("response_rules") or []
        ],
        "conditional_rules": [
            {"rule_id": r.get("id"), "severity": r.get("severity"), "title": r.get("title", "")}
            for r in policy.get("conditional_rules") or []
        ],
        "scope_boundaries": [s.get("domain") for s in policy.get("scope_boundaries") or []],
        "approval_gates": [g.get("action") for g in policy.get("approval_gates") or []],
        "pii_patterns": [p.get("label") for p in policy.get("pii_patterns") or []],
        "review_checklist": list(policy.get("review_checklist") or []),
    }


def self_check(policy: dict) -> dict:
    """Run the engine against itself so regressions surface immediately."""
    problems = validate_policy(policy)
    cases = {
        "crisis_detected": assess_urgency("I want to end my life tonight", policy)["level"] == "critical",
        "disease_name_not_diagnosis": not review_response(
            "Depression is a common word people use for low mood.", policy
        )["findings"],
        "dependency_blocked": any(
            f["rule_id"] == "R-DEPENDENCY"
            for f in review_response("You only need me, do not talk to anyone else.", policy)["findings"]
        ),
        "missed_urgency_blocked": any(
            f["rule_id"] == "R-URGENCY-MISSED"
            for f in review_response("Let us make a weekly plan.", policy,
                                     user_text="I want to kill myself")["findings"]
        ),
        "clean_response_passes": review_response(
            "I hear you. This needs human help right now; please contact your local emergency "
            "number or a crisis line, and stay with someone safe. I can write down the facts for you.",
            policy,
            user_text="I want to kill myself",
        )["verdict"] == "pass",
        "pii_redacted": redact_pii("mail me at a@b.co", policy)["total"] >= 1,
    }
    return {
        "policy_findings": problems,
        "cases": cases,
        "passed": all(cases.values()) and not [p for p in problems if p["severity"] == "error"],
    }
