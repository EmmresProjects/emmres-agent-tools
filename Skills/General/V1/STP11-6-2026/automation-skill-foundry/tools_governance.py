#!/usr/bin/env python3
"""tools_governance.py - safety gates, audit records, secret scanning, test plans.

Implements the governance half of the catalog: approvals before high-impact
actions, structured audit trails that never store raw secrets, credential scans
over a project directory, and a seven-dimension test plan with runnable commands.

Commands
  gate        evaluate required gates for a bundle and report pass/fail
  audit       build a structured audit record (hashes sensitive values)
  secrets     scan files for embedded credentials (read-only)
  test-plan   generate a runnable test plan for a bundle or project

No command here executes the automation or contacts a network service.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_common as C

PROG = "tools_governance.py"
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_SCAN_FILES = 400
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}


def _checks():
    return C.load_json(C.asset(C.GOVERNANCE_FILE), "governance checks")


def _risk(bundle, rules):
    return C.score_risk(C.bundle_text(bundle), rules)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_gate(args, cfg):
    bundle = C.load_bundle(args.input)
    rules = C.load_rules(root=C.tool_root())
    checks = _checks()
    risk = _risk(bundle, rules)
    level = risk["level"]
    evidence = {
        "approval": args.approve or bundle.get("approved_by"),
        "dry_run": bool(args.has_dry_run or bundle.get("gates")),
        "rollback": bool(args.rollback),
        "secrets": None,
        "owner": args.owner or bundle.get("owner"),
        "idempotency": bool(args.idempotent),
        "stop_control": bool(args.stop_control),
    }
    if args.project:
        scan = _scan_dir(os.path.abspath(args.project), cfg)
        evidence["secrets"] = not scan["findings"]
        evidence["_scan"] = {"files": scan["files_scanned"],
                             "findings": len(scan["findings"])}
    results = []
    for rule in checks["gate_rules"]:
        applies = level in rule.get("applies_to_levels", [])
        value = evidence.get(rule["id"])
        passed = None
        if rule["id"] == "secrets" and value is None:
            passed = None  # unknown: no project supplied
        elif value is None or value is False:
            passed = False
        else:
            passed = True
        results.append({
            "gate": rule["id"],
            "label": rule["label"],
            "applies": applies,
            "state": "pass" if passed else ("unknown" if passed is None else "fail"),
            "evidence": evidence.get(rule["id"]),
            "required": rule["required"],
        })
    blocking = [r for r in results if r["applies"] and r["required"] and r["state"] != "pass"]
    return {
        "ok": not blocking,
        "_exit_code": 0 if not blocking else 1,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": bundle["goal"],
        "risk": {"level": level, "score": risk["score"]},
        "gates": results,
        "blocking": [b["gate"] for b in blocking],
        "verdict": "cleared" if not blocking else "blocked",
        "remedy": ("Supply the missing evidence: --approve/approved_by, --rollback, "
                   "--owner, --idempotent, --stop-control, and a clean --project scan."
                   if blocking else None),
    }


def cmd_audit(args, cfg):
    rules = C.load_rules(root=C.tool_root())
    policy = _checks()["sensitive_input_policy"]
    inputs = {}
    sensitive_seen = []
    for item in args.input or []:
        if "=" not in item:
            raise C.FoundryError("--input must be KEY=VALUE, got %r" % item,
                                code="bad_input")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise C.FoundryError("empty key in --input", code="bad_input")
        lowered = key.lower()
        is_sensitive = any(token in lowered for token in
                          ("password", "secret", "token", "key", "credential",
                           "authorization", "cookie"))
        if is_sensitive:
            sensitive_seen.append(key)
            inputs[key] = {"hashed": policy["hash_algorithm"],
                           "sha256": C.sha256_of(value), "length": len(value)}
        else:
            inputs[key] = value
    if not args.action:
        raise C.FoundryError("--action is required (what the automation did)",
                            code="bad_input")
    result = str(args.result)
    if result not in ("ok", "failed", "skipped", "blocked"):
        raise C.FoundryError("--result must be ok|failed|skipped|blocked",
                            code="bad_input")
    risk = C.score_risk(" ".join([args.action, result, " ".join(args.input or [])]), rules)
    record = {
        "event_id": C.sha256_of("%s|%s|%s" % (args.actor, args.action, C.now_iso()))[:16],
        "timestamp": C.now_iso(),
        "actor": args.actor,
        "action": args.action,
        "target": args.target,
        "result": result,
        "approval": {"approved_by": args.approve,
                     "required": risk["level"] in ("high", "critical"),
                     "recorded": bool(args.approve)},
        "error": args.error,
        "inputs": inputs,
        "sensitive_inputs_hashed": sensitive_seen,
        "risk_level": risk["level"],
        "retention_days": int(args.retention_days or policy["retention_days_default"]),
    }
    checks = _checks()
    missing = [f for f in checks["audit_required_fields"] if record.get(f) in (None, "", {})]
    optional_missing = [f for f in checks.get("audit_optional_fields", [])
                        if record.get(f) in (None, "", {})]
    record_ok = not missing
    payload = {
        "ok": record_ok,
        "tool": PROG,
        "record": record,
        "missing_required_fields": missing,
        "missing_optional_fields": optional_missing,
        "secret_scan": C.scan_for_secrets(C.stable_json(record)),
        "note": "The record contains hashed values only; raw secrets are never written.",
    }
    if args.append_to:
        path = os.path.abspath(args.append_to)
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(C.stable_json(record))
        payload["appended_to"] = path
        payload["appended_bytes"] = len(C.stable_json(record))
    return payload


def _scan_dir(root, cfg, limit_files=MAX_SCAN_FILES):
    if not os.path.isdir(root):
        raise C.FoundryError("project directory not found: %s" % root, code="missing_file")
    findings = []
    scanned = 0
    skipped = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if scanned >= limit_files:
                skipped.append("file limit %d reached" % limit_files)
                break
            path = os.path.join(current, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > MAX_SCAN_BYTES:
                skipped.append("%s (%d bytes > limit)" % (rel, size))
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="strict") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                skipped.append("%s (binary or unreadable)" % rel)
                continue
            scanned += 1
            for hit in C.scan_for_secrets(text):
                findings.append({"file": rel, **hit})
    return {"root": root, "files_scanned": scanned, "findings": findings,
            "skipped": skipped, "limit_files": limit_files,
            "limit_bytes_per_file": MAX_SCAN_BYTES}


def cmd_secrets(args, cfg):
    if not args.project:
        raise C.FoundryError("--project DIR is required", code="missing_input")
    result = _scan_dir(os.path.abspath(args.project), cfg)
    result.update({
        "ok": not result["findings"],
        "_exit_code": 1 if result["findings"] else 0,
        "tool": PROG,
        "verdict": "clean" if not result["findings"] else "credentials-found",
        "remedy": ("Remove the literal, load it from an environment variable or vault "
                   "reference, then rotate the exposed credential."
                   if result["findings"] else None),
    })
    return result


def cmd_test_plan(args, cfg):
    checks = _checks()
    bundle = C.load_bundle(args.input) if args.input else None
    project = os.path.abspath(args.project) if args.project else None
    if not bundle and not project:
        raise C.FoundryError("provide --input BUNDLE and/or --project DIR",
                            code="missing_input")
    entry = "automation_entry.py"
    if project:
        if not os.path.isdir(project):
            raise C.FoundryError("project directory not found: %s" % project,
                                code="missing_file")
        if not os.path.isfile(os.path.join(project, entry)):
            raise C.FoundryError("%s not found in project" % entry, code="bad_project")
    steps = []
    for dim in checks["test_dimensions"]:
        commands = []
        if project:
            if dim["id"] == "normal":
                commands = ["cd %s && python3 %s --config config.json --dry-run" % (project, entry)]
            elif dim["id"] == "boundary":
                commands = [
                    "mkdir -p /tmp/empty-inbox && python3 %s --config /tmp/empty-config.json "
                    "--dry-run" % entry,
                    "printf 'a' > /tmp/one-byte.bin && python3 %s --config /tmp/one-byte-config.json "
                    "--dry-run" % entry,
                ]
            elif dim["id"] == "failure":
                commands = ["python3 %s --config /tmp/missing-config.json; echo exit=$?" % entry]
            elif dim["id"] == "repeat":
                commands = ["python3 %s --config config.json --dry-run > /tmp/run1.json" % entry,
                            "python3 %s --config config.json --dry-run > /tmp/run2.json" % entry,
                            "python3 -c \"import json;a=json.load(open('/tmp/run1.json'));"
                            "b=json.load(open('/tmp/run2.json'));"
                            "print('same plan:', a['plan']==b['plan'])\""]
            elif dim["id"] == "concurrency":
                commands = ["python3 %s --config config.json --dry-run & python3 %s "
                            "--config config.json --dry-run & wait" % (entry, entry)]
            elif dim["id"] == "recovery":
                commands = ["python3 -c \"import os,signal,subprocess,time;"
                            "p=subprocess.Popen(['python3','%s','--config','config.json']);"
                            "time.sleep(1);p.send_signal(signal.SIGINT)\" ; "
                            "echo exit=$?" % entry]
            elif dim["id"] == "clean_state":
                commands = ["cp -r %s /tmp/fresh-project && cd /tmp/fresh-project && "
                            "python3 %s --config config.json --dry-run" % (project, entry)]
        steps.append({
            "dimension": dim["id"],
            "label": dim["label"],
            "intent": dim["description"],
            "example": dim["example"],
            "commands": commands,
            "status": "not-run",
        })
    return {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": bundle["goal"] if bundle else os.path.basename(project or ""),
        "project": project,
        "dimension_count": len(steps),
        "steps": steps,
        "report_template": "asset-test-plan-template.json",
        "note": "This tool generates the plan; it does not claim the tests passed.",
    }


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Safety gates, audit records, secret scans and test plans.",
        epilog="Examples:\n"
               "  python3 tools_governance.py gate --input bundle.json --project workspace "
               "--owner ops-lead --rollback documented --idempotent --stop-control\n"
               "  python3 tools_governance.py audit --actor ci --action 'process invoices' "
               "--target inbox --result ok --input api_token=abc123\n"
               "  python3 tools_governance.py secrets --project workspace\n"
               "  python3 tools_governance.py test-plan --project workspace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (C.TOOL_NAME, C.TOOL_VERSION))
    C.add_common_arguments(parser)
    common = argparse.ArgumentParser(add_help=False)
    C.add_common_arguments(common, suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("gate", parents=[common], help="evaluate required safety gates")
    p.add_argument("--input", required=True, metavar="BUNDLE", help="bundle JSON file")
    p.add_argument("--project", metavar="DIR", help="generated project to scan for secrets")
    p.add_argument("--owner", metavar="NAME", help="named owner of the automation")
    p.add_argument("--approve", metavar="NAME", help="approver name to record as evidence")
    p.add_argument("--rollback", metavar="NOTE", help="rollback/restore evidence")
    p.add_argument("--idempotent", action="store_true", help="repeat runs are proven safe")
    p.add_argument("--stop-control", action="store_true", help="stop/drain path documented")
    p.add_argument("--has-dry-run", action="store_true", help="dry-run path exists")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("audit", parents=[common], help="build a structured audit record")
    p.add_argument("--actor", default=os.environ.get("USER", "unknown"), metavar="NAME")
    p.add_argument("--action", required=True, metavar="TEXT")
    p.add_argument("--target", default="", metavar="TEXT")
    p.add_argument("--result", default="ok", metavar="STATE",
                   help="ok|failed|skipped|blocked (default ok)")
    p.add_argument("--approve", metavar="NAME", help="approver on record")
    p.add_argument("--error", metavar="TEXT", help="failure detail (no secrets)")
    p.add_argument("--input", action="append", metavar="KEY=VALUE",
                   help="input value; sensitive keys are hashed automatically")
    p.add_argument("--retention-days", type=int, metavar="N")
    p.add_argument("--append-to", metavar="PATH", help="append the record as JSONL")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("secrets", parents=[common], help="scan a project for credentials")
    p.add_argument("--project", required=True, metavar="DIR")
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser("test-plan", parents=[common], help="seven-dimension test plan")
    p.add_argument("--input", metavar="BUNDLE", help="bundle JSON file")
    p.add_argument("--project", metavar="DIR", help="generated project directory")
    p.set_defaults(func=cmd_test_plan)
    return parser


def main(argv=None):
    return C.run_cli(PROG, build_parser, lambda a, c: a.func(a, c), argv)


if __name__ == "__main__":
    sys.exit(main())
