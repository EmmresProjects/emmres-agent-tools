#!/usr/bin/env python3
"""tools_plan.py - lifecycle planning engine for an automation bundle.

Turns a routed/parsed bundle into an implementable plan: lifecycle stages with
tasks and acceptance checks, dependency edges, dry-run design, approval gates,
observability, handoff notes, and bounded retry/timeout settings.

Commands
  workflow  lifecycle plan with tasks, gates and dependencies
  estimate  ROI estimate (time saved, build/maintenance cost, payback)
  dryrun    dry-run / preview design for the bundle

All commands accept --input <bundle.json> or stdin.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_common as C

PROG = "tools_plan.py"

STAGE_TASKS = {
    "plan": [
        "Write the goal as one sentence plus explicit non-goals",
        "Name the trigger, inputs, outputs, owner, and success check",
        "Confirm authorization for every data source and target system",
    ],
    "guard": [
        "Define input validation rules and reject reasons",
        "Decide which steps need a human approval gate",
        "Store secrets as vault/environment references only",
        "Document the rollback or restore path",
    ],
    "verify": [
        "Run the normal path end to end on fixture data",
        "Run invalid, missing, and boundary inputs",
        "Run twice to prove idempotency",
        "Inject one failure and confirm recovery",
    ],
    "build": [
        "Implement the smallest complete entry point with --help",
        "Externalise configuration and paths",
        "Emit structured logs and stable exit codes",
    ],
    "trigger": [
        "Set the schedule, time zone, and overlap policy",
        "Define missed-run and catch-up behaviour",
        "Bound queue depth, concurrency, and per-run timeout",
    ],
    "operate": [
        "Emit metrics: runs, failures, duration, records processed",
        "Route failures to a named owner with deduplicated alerts",
        "Document the stop control and how to drain the queue",
    ],
    "handoff": [
        "Write install, configure, run, recover, and troubleshoot notes",
        "Record owner, review date, and deprecation trigger",
        "Package source, config, fixtures, and tests together",
    ],
}

STAGE_ARTIFACTS = {
    "plan": ["requirements.md", "risk-register.json"],
    "guard": ["validation-rules.json", "approval-policy.json", "rollback-plan.md"],
    "verify": ["test-plan.json", "test-report.json"],
    "build": ["src/", "config.json", "README.md"],
    "trigger": ["schedule.json", "runbook.md"],
    "operate": ["logging.json", "alerts.json"],
    "handoff": ["HANDOFF.md", "CHANGELOG.md"],
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _load(args, cfg, allow_unapproved=False):
    path = args.input
    if not path:
        raise C.FoundryError("provide --input <bundle.json>", code="missing_input")
    if not os.path.isfile(path):
        raise C.FoundryError("bundle file not found: %s" % path, code="missing_file")
    bundle = C.load_bundle(path)
    rules = C.load_rules(root=C.tool_root())
    if not allow_unapproved and bundle.get("approval_required") and not bundle.get("approved_by"):
        risk = bundle.get("risk") or {}
        if risk.get("level") in ("high", "critical"):
            raise C.FoundryError(
                "bundle is %s risk and has no recorded approver; record one with "
                "tools_catalog.py route --approve NAME before planning execution"
                % risk.get("level"), code="approval_required",
                detail={"risk_level": risk.get("level")})
    return bundle, rules


def _stage_buckets(bundle, rules):
    order = C.stages_in_order(rules)
    buckets = {sid: [] for sid in order}
    for skill in bundle["skills"]:
        sid = skill.get("stage")
        if sid not in buckets:
            sid = C.stage_of(skill, rules)
        buckets[sid].append(skill)
    return order, buckets


def _dependencies(order, buckets):
    edges = []
    previous = None
    for sid in order:
        if not buckets[sid]:
            continue
        if previous is not None:
            edges.append({"from": previous, "to": sid, "type": "blocks"})
        previous = sid
    # a guard stage must clear before the build/trigger steps act, and verify
    # must clear before anything is handed to operations
    if buckets["guard"] and buckets["trigger"]:
        edges.append({"from": "guard", "to": "trigger", "type": "gates"})
    elif buckets["guard"] and buckets["build"]:
        edges.append({"from": "guard", "to": "build", "type": "gates"})
    if buckets["verify"] and buckets["operate"]:
        edges.append({"from": "verify", "to": "operate", "type": "gates"})
    return edges


def _acceptance(bundle, rules, stage_id):
    checks = list(C.stage_checks(rules, stage_id))
    checks.append("Stage artifacts present: %s" % ", ".join(STAGE_ARTIFACTS.get(stage_id, [])))
    return checks


def _limits(rules, overrides):
    limits = dict(rules.get("limits") or {})
    limits.update({k: v for k, v in overrides.items() if v is not None})
    return limits


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_workflow(args, cfg):
    bundle, rules = _load(args, cfg)
    order, buckets = _stage_buckets(bundle, rules)
    risk = C.score_risk(C.bundle_text(bundle), rules)
    limits = _limits(rules, {"max_retries": args.max_retries,
                             "default_job_timeout_seconds": args.timeout})
    stages = []
    for sid in order:
        if not buckets[sid] and sid not in ("verify", "operate"):
            continue
        stages.append({
            "stage": sid,
            "skills": [{"number": s["number"], "name": s["name"], "id": s["id"]}
                       for s in buckets[sid]],
            "tasks": STAGE_TASKS[sid],
            "acceptance_checks": _acceptance(bundle, rules, sid),
            "artifacts": STAGE_ARTIFACTS[sid],
            "done": False,
        })
    blocked = risk["level"] in ("high", "critical")
    plan = {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": bundle["goal"],
        "skill_count": len(bundle["skills"]),
        "risk": risk,
        "approval_required": blocked,
        "approved_by": bundle.get("approved_by"),
        "execution_allowed": not blocked or bool(bundle.get("approved_by")),
        "limits": limits,
        "stages": stages,
        "dependencies": _dependencies(order, buckets),
        "gates": bundle.get("gates") or [],
        "definition_of_done": [
            "Normal path runs green on fixtures",
            "Invalid input rejected with a structured error",
            "Repeat run creates no duplicate effect",
            "Failure recovers or stops safely within the retry bound",
            "Owner, review date, and stop control documented",
        ],
    }
    if blocked and not bundle.get("approved_by"):
        plan["next_action"] = ("Record an approver first: re-run tools_catalog.py route "
                               "with --approve NAME, or set approved_by in the bundle.")
    return C.guard_output_size(plan, cfg)


def cmd_estimate(args, cfg):
    bundle, rules = _load(args, cfg)
    runs_per_month = args.runs_per_month
    minutes_saved = args.minutes_saved
    if runs_per_month < 0 or minutes_saved < 0:
        raise C.FoundryError("runs-per-month and minutes-saved must be >= 0",
                             code="bad_input")
    hours_saved = runs_per_month * minutes_saved / 60.0
    labor_rate = args.labor_rate
    monthly_saving = hours_saved * labor_rate
    skill_count = len(bundle["skills"])
    build_hours = args.build_hours if args.build_hours is not None else 2.0 + 1.5 * skill_count
    maintenance_hours = args.maintenance_hours if args.maintenance_hours is not None \
        else max(1.0, 0.25 * skill_count)
    build_cost = build_hours * labor_rate
    maintenance_cost = maintenance_hours * labor_rate
    net_monthly = monthly_saving - maintenance_cost
    risk = C.score_risk(C.bundle_text(bundle), rules)
    if net_monthly <= 0:
        payback_months = None
    else:
        payback_months = round(build_cost / net_monthly, 2)
    return {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": bundle["goal"],
        "assumptions": {
            "runs_per_month": runs_per_month,
            "minutes_saved_per_run": minutes_saved,
            "labor_rate_per_hour": labor_rate,
            "build_hours": build_hours,
            "maintenance_hours_per_month": maintenance_hours,
            "note": "Defaults are planning placeholders derived from bundle size; "
                    "replace them with measured values before committing budget.",
        },
        "monthly": {
            "hours_saved": round(hours_saved, 2),
            "labor_saving": round(monthly_saving, 2),
            "maintenance_cost": round(maintenance_cost, 2),
            "net_saving": round(net_monthly, 2),
        },
        "build": {"hours": round(build_hours, 2), "cost": round(build_cost, 2)},
        "payback_months": payback_months,
        "risk": {"level": risk["level"], "score": risk["score"]},
        "verdict": ("viable" if payback_months is not None and payback_months <= 12
                    else "review" if payback_months is not None else "not-viable"),
    }


def cmd_dryrun(args, cfg):
    bundle, rules = _load(args, cfg, allow_unapproved=True)
    order, buckets = _stage_buckets(bundle, rules)
    mutating = []
    for sid in order:
        for skill in buckets[sid]:
            hay = C._slug(skill["name"] + " " + skill.get("description", ""))
            writes = any(k in hay for k in ("publish", "send", "delete", "write",
                                            "upload", "deploy", "execute", "update",
                                            "sync", "provision", "distribute", "post"))
            mutating.append({
                "skill": skill["name"],
                "stage": sid,
                "may_change_external_state": writes,
                "preview_required": writes,
            })
    steps = [
        {"step": 1, "action": "Load and validate every input; abort on first invalid row",
         "output": "validation report (counts, reject reasons)"},
        {"step": 2, "action": "Resolve all paths, targets, and recipients from config",
         "output": "resolved target list"},
        {"step": 3, "action": "Compute the intended change set without applying it",
         "output": "diff/preview file + sha256"},
        {"step": 4, "action": "Apply only if the preview hash matches the approved hash",
         "output": "apply log with before/after references"},
        {"step": 5, "action": "Verify the effect, then write the audit record",
         "output": "verification result + audit entry"},
    ]
    return {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "goal": bundle["goal"],
        "mode": "dry-run",
        "applies_changes": False,
        "steps": steps,
        "skills_requiring_preview": [m for m in mutating if m["preview_required"]],
        "approval": {
            "required": any(m["preview_required"] for m in mutating),
            "recorded_approver": bundle.get("approved_by"),
            "bundle_flagged_high_risk": bool(bundle.get("approval_required")),
            "approve_with": "record approved_by in the bundle (tools_catalog.py route "
                            "--approve NAME) before the calling automation acts",
            "applied_by_tool": False,
            "note": "This tool never performs the change; it produces the preview the "
                    "calling automation must show before acting.",
        },
    }


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Plan an automation bundle: workflow stages, ROI, dry-run design.",
        epilog="Examples:\n"
               "  python3 tools_plan.py workflow --input bundle.json\n"
               "  python3 tools_plan.py estimate --input bundle.json --runs-per-month 22 "
               "--minutes-saved 15 --labor-rate 35\n"
               "  python3 tools_plan.py dryrun --input bundle.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (C.TOOL_NAME, C.TOOL_VERSION))
    C.add_common_arguments(parser)
    common = argparse.ArgumentParser(add_help=False)
    C.add_common_arguments(common, suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("workflow", parents=[common],
                       help="lifecycle plan with tasks, gates, dependencies")
    p.add_argument("--input", required=True, metavar="BUNDLE", help="bundle JSON file")
    p.add_argument("--max-retries", type=int, metavar="N", help="retry bound (default 3)")
    p.add_argument("--timeout", type=int, metavar="SECONDS",
                   help="per-run timeout (default 900)")
    p.set_defaults(func=cmd_workflow)

    p = sub.add_parser("estimate", parents=[common], help="ROI estimate for the bundle")
    p.add_argument("--input", required=True, metavar="BUNDLE", help="bundle JSON file")
    p.add_argument("--runs-per-month", type=int, default=20, metavar="N")
    p.add_argument("--minutes-saved", type=float, default=10.0, metavar="M")
    p.add_argument("--labor-rate", type=float, default=30.0, metavar="RATE",
                   help="currency per hour used for costing")
    p.add_argument("--build-hours", type=float, metavar="H", help="override build effort")
    p.add_argument("--maintenance-hours", type=float, metavar="H",
                   help="override monthly maintenance effort")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("dryrun", parents=[common],
                       help="dry-run / preview design for the bundle")
    p.add_argument("--input", required=True, metavar="BUNDLE", help="bundle JSON file")
    p.set_defaults(func=cmd_dryrun)
    return parser


def main(argv=None):
    return C.run_cli(PROG, build_parser, lambda a, c: a.func(a, c), argv)


if __name__ == "__main__":
    sys.exit(main())
