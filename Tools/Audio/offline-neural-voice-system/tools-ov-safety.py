#!/usr/bin/env python3
"""Safety, consent and real-person protection gates.

Policy data lives in asset-safety-policy.json. Consent records are local JSON
files under a consent directory (default: ./consent-records, override with
--consent-dir or OV_CONSENT_DIR). Nothing here grants legal authority; the
consent record is an operator-maintained artifact and must be genuine.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
POLICY_PATH = SKILL_ROOT / "asset-safety-policy.json"


def load_policy() -> dict:
    with open(POLICY_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def consent_dir_from(args_dir=None) -> Path:
    import os
    d = args_dir or os.environ.get("OV_CONSENT_DIR") or "consent-records"
    p = Path(d).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_consents(directory: Path) -> list[dict]:
    records = []
    for f in sorted(directory.glob("consent-*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                records.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            records.append({"id": f.stem, "status": "unreadable", "path": str(f)})
    return records


def find_valid_consent(directory: Path, speaker_name: str):
    key = re.sub(r"\s+", " ", (speaker_name or "").strip().lower())
    for rec in list_consents(directory):
        if rec.get("status") != "active":
            continue
        name = re.sub(r"\s+", " ", str(rec.get("speaker_name", "")).strip().lower())
        if name and name == key:
            expiry = rec.get("expires")
            if expiry:
                try:
                    if time.strftime("%Y-%m-%d") > expiry:
                        continue
                except Exception:
                    pass
            return rec
    return None


def check_request(spec: dict, directory: Path) -> dict:
    """Evaluate a synthesis request against the policy. Returns a decision dict."""
    policy = load_policy()
    reasons, actions = [], []
    decision = "allow"

    real_person = bool(spec.get("real_person"))
    speaker_name = str(spec.get("speaker_name") or "").strip()
    involves_minor = bool(spec.get("involves_minor"))
    context = str(spec.get("context") or "general").strip().lower()
    requested_use = str(spec.get("requested_use") or "").strip().lower()

    if involves_minor:
        decision = "deny"
        reasons.append("Voice synthesis involving minors is prohibited by policy.")

    haystack = f"{requested_use} {context}".lower()
    for entry in policy.get("prohibited_uses", []):
        if isinstance(entry, str):
            triggers = [entry.lower()]
            label = entry
        else:
            triggers = [str(t).lower() for t in entry.get("triggers", [])]
            label = entry.get("id", "unknown")
        for trigger in triggers:
            if trigger and trigger in haystack:
                decision = "deny"
                reasons.append(f"Requested use matches prohibited category: {label}")
                break

    sensitive = [c.lower() for c in policy.get("sensitive_contexts", [])]
    for ctx in sensitive:
        if ctx and ctx in context:
            if decision != "deny":
                decision = "review"
            reasons.append(f"Context {ctx!r} is sensitive; human review required before publishing.")

    if real_person:
        if not speaker_name:
            decision = "deny"
            reasons.append("real_person=true requires speaker_name to identify the person.")
        else:
            rec = find_valid_consent(directory, speaker_name)
            if rec:
                reasons.append(f"Active consent record {rec.get('id')} found for {speaker_name}.")
                actions.append("Bundle the consent record id with any published output; keep the disclosure line.")
            else:
                decision = "deny"
                reasons.append(f"No active consent record for {speaker_name!r}; cloning/impersonating a real person requires one.")
                actions.append("Obtain written consent, then record it: tools-offline-voice.py consent --action grant ...")
    elif speaker_name and re.search(r"(clone|voice of|sound like|in the voice of|impersonat)", requested_use, re.I):
        rec = find_valid_consent(directory, speaker_name)
        if not rec and decision != "deny":
            decision = "deny"
            reasons.append(f"Voice-clone style request naming {speaker_name!r} without an active consent record.")

    if decision == "allow":
        actions.append("Include the synthetic-voice disclosure line with published output.")
        actions.append("Keep provenance sidecar files (provenance verify) for auditability.")

    return {"ok": decision == "allow", "decision": decision, "reasons": reasons,
            "required_actions": actions,
            "disclosure": policy.get("disclosure_line"),
            "spec": {"real_person": real_person, "speaker_name": speaker_name or None,
                     "involves_minor": involves_minor, "context": context,
                     "requested_use": requested_use or None}}


def grant_consent(directory: Path, speaker_name: str, grantor: str, scope: str,
                  expires: str | None, notes: str | None) -> dict:
    if not speaker_name.strip() or not grantor.strip() or not scope.strip():
        raise ValueError("speaker_name, grantor and scope are all required")
    if expires:
        try:
            time.strptime(expires, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("expires must be YYYY-MM-DD") from exc
    rec = {"id": "consent-" + uuid.uuid4().hex[:12],
           "type": "voice-clone-consent",
           "speaker_name": speaker_name.strip(),
           "grantor": grantor.strip(),
           "scope": scope.strip(),
           "expires": expires,
           "notes": notes or "",
           "status": "active",
           "recorded_at": _now_iso(),
           "recorded_by": "tools-offline-voice consent",
           "warning": "Operator-maintained record. It is not legal advice and not proof by itself; keep the underlying written authorization."}
    path = directory / f"{rec['id']}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    return rec


def revoke_consent(directory: Path, consent_id: str) -> dict:
    path = directory / f"{consent_id}.json"
    candidates = list(directory.glob(f"{consent_id}*.json")) if not path.is_file() else [path]
    if not candidates:
        raise FileNotFoundError(f"consent record not found: {consent_id}")
    target = candidates[0]
    with open(target, "r", encoding="utf-8") as fh:
        rec = json.load(fh)
    rec["status"] = "revoked"
    rec["revoked_at"] = _now_iso()
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    return rec
