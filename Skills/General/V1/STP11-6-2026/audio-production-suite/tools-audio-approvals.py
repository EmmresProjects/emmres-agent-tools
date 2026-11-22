#!/usr/bin/env python3
"""tools-audio-approvals.py - human approval ledger for risky audio actions.

The suite will not overwrite masters, publish packages, or touch voice likeness
material without a matching approval record. The ledger is a plain JSON file you
choose the location of; nothing is sent anywhere and no credentials are involved.

Lifecycle:  request (pending) -> grant (granted, expires) -> consume (used)
                              -> revoke (revoked)

Run directly for its own help: python3 tools-audio-approvals.py --help
"""

from __future__ import annotations

import datetime
import os
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
POLICY_ASSET = "asset-approval-policy.json"
LEDGER_VERSION = "1.0.0"


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    if "aps_audio_common" in sys.modules:
        return sys.modules["aps_audio_common"]
    spec = importlib.util.spec_from_file_location("aps_audio_common", os.path.join(here, COMMON_FILENAME))
    module = importlib.util.module_from_spec(spec)
    sys.modules["aps_audio_common"] = module
    spec.loader.exec_module(module)
    return module


common = _load()


def policy():
    return common.load_asset(POLICY_ASSET)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _iso(moment):
    return moment.isoformat()


def _parse(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def ledger_path(value=None):
    if value:
        return common.resolve_path(value)
    default_name = policy().get("ledger_default_filename", "approval-ledger.json")
    return os.path.join(os.getcwd(), default_name)


def load_ledger(path=None, create=False):
    resolved = ledger_path(path)
    if not os.path.isfile(resolved):
        if not create:
            return {"ledger_version": LEDGER_VERSION, "entries": [], "path": resolved, "exists": False}
        return {"ledger_version": LEDGER_VERSION, "entries": [], "path": resolved, "exists": False}
    data = common.read_json(resolved, "approval ledger")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise common.ToolError("invalid_ledger", "Approval ledger must be an object with an entries list",
                               {"path": resolved})
    data["path"] = resolved
    data["exists"] = True
    return data


def save_ledger(data):
    payload = {"ledger_version": data.get("ledger_version", LEDGER_VERSION),
               "updated_at": _iso(_now()),
               "entries": data["entries"]}
    common.write_json(data["path"], payload)
    return data["path"]


def _action(action_id):
    for action in policy()["actions"]:
        if action["id"] == action_id:
            return action
    raise common.ToolError("unknown_action", "Unknown approval action %r" % action_id,
                           {"available": [a["id"] for a in policy()["actions"]]})


def request(action, target, requested_by="", note="", path=None):
    action_def = _action(action)
    if not target:
        raise common.ToolError("invalid_argument", "An approval request needs a target path or description")
    data = load_ledger(path)
    entry = {
        "id": common.new_id("APR"),
        "action": action,
        "label": action_def["label"],
        "risk": action_def["risk"],
        "target": str(target),
        "requested_by": requested_by or "unspecified",
        "requested_at": _iso(_now()),
        "status": "pending",
        "note": note,
        "approver": None,
        "granted_at": None,
        "expires_at": None,
        "consumed_at": None,
        "consumed_by": None,
    }
    data["entries"].append(entry)
    save_ledger(data)
    return {"entry": entry, "ledger": data["path"], "next_step":
            "python3 tools-audio-suite.py approvals grant --id %s --by \"<name>\" --ledger %s"
            % (entry["id"], data["path"])}


def _find(data, approval_id):
    for entry in data["entries"]:
        if entry["id"] == approval_id:
            return entry
    raise common.ToolError("approval_missing", "No approval with id %r" % approval_id,
                           {"ledger": data["path"]},
                           hint="Run the approvals list command to see issued ids.")


def grant(approval_id, approver, note="", path=None):
    if not approver:
        raise common.ToolError("invalid_argument", "grant needs --by so the approver is recorded")
    data = load_ledger(path)
    entry = _find(data, approval_id)
    if entry["status"] == "revoked":
        raise common.ToolError("approval_revoked", "Approval %s was revoked and cannot be granted" % approval_id)
    if entry["status"] == "consumed":
        raise common.ToolError("approval_consumed", "Approval %s was already used" % approval_id)
    ttl = int(policy().get("token_ttl_minutes", 120))
    now = _now()
    entry.update({"status": "granted", "approver": approver, "granted_at": _iso(now),
                  "expires_at": _iso(now + datetime.timedelta(minutes=ttl))})
    if note:
        entry["note"] = note
    save_ledger(data)
    return {"entry": entry, "ledger": data["path"]}


def revoke(approval_id, note="", path=None):
    data = load_ledger(path)
    entry = _find(data, approval_id)
    entry.update({"status": "revoked", "note": note or entry.get("note", "")})
    save_ledger(data)
    return {"entry": entry, "ledger": data["path"]}


def listing(path=None, status=None, action=None, limit=50):
    data = load_ledger(path)
    entries = data["entries"]
    if status:
        entries = [e for e in entries if e["status"] == status]
    if action:
        entries = [e for e in entries if e["action"] == action]
    counts = {}
    for entry in data["entries"]:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {"ledger": data["path"], "exists": data["exists"], "counts": counts,
            "entries": entries[-limit:], "total": len(data["entries"])}


def verify(approval_id, action, target, path=None, consume=False, consumed_by=""):
    """Raise a precise error unless the approval matches action, target, and time window."""
    if not approval_id:
        raise common.ToolError("approval_required",
                               "This action requires human approval and none was supplied",
                               {"action": action, "target": str(target)},
                               hint="Request one with 'approvals request --action %s --target <path>'." % action)
    data = load_ledger(path)
    entry = _find(data, approval_id)
    if entry["status"] == "pending":
        raise common.ToolError("approval_not_granted", "Approval %s is still pending" % approval_id,
                               {"approver_required": _action(entry["action"])["approver"]})
    if entry["status"] == "revoked":
        raise common.ToolError("approval_revoked", "Approval %s was revoked" % approval_id)
    if entry["status"] == "consumed":
        raise common.ToolError("approval_consumed", "Approval %s was already used at %s"
                               % (approval_id, entry.get("consumed_at")))
    if entry["action"] != action:
        raise common.ToolError("approval_action_mismatch",
                               "Approval %s is for %r, not %r" % (approval_id, entry["action"], action),
                               {"action_required": action, "action_granted": entry["action"]})
    expected = os.path.abspath(entry["target"]) if os.sep in entry["target"] else entry["target"]
    supplied = os.path.abspath(target) if isinstance(target, str) and os.sep in target else str(target)
    if entry["target"] != "*" and expected != supplied:
        raise common.ToolError("approval_target_mismatch",
                               "Approval %s was granted for a different target" % approval_id,
                               {"granted_for": entry["target"], "requested_for": str(target)})
    expiry = _parse(entry.get("expires_at"))
    if expiry and _now() > expiry:
        raise common.ToolError("approval_expired",
                               "Approval %s expired at %s" % (approval_id, entry["expires_at"]),
                               hint="Request a fresh approval for this action.")
    if consume:
        entry.update({"status": "consumed", "consumed_at": _iso(_now()),
                      "consumed_by": consumed_by or action})
        save_ledger(data)
    return {"entry": entry, "ledger": data["path"], "consumed": bool(consume)}


def main(argv=None):
    print("""tools-audio-approvals.py - approval ledger (library + help).

Functions: request(), grant(), revoke(), listing(), verify(), load_ledger(), ledger_path()

Use the suite entry point for command line work:
  python3 tools-audio-suite.py approvals request --action destructive-overwrite --target out/master.wav --ledger approvals.json
  python3 tools-audio-suite.py approvals grant --id APR-1A2B3C4D --by "Audio Lead" --ledger approvals.json
  python3 tools-audio-suite.py approvals list --ledger approvals.json
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
