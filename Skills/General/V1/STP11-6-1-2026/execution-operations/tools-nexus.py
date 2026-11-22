#!/usr/bin/env python3
"""Execution Operations skill tool: primary runnable entry point.

Usage
-----
    python3 execution-operations/tools-nexus.py --help
    python3 execution-operations/tools-nexus.py capabilities
    python3 execution-operations/tools-nexus.py exec --command "python3 -V"

Every command prints a structured JSON envelope on stdout:
    {"ok": bool, "operation": str, "tool": "execution-operations", "version": str,
     "data": {...}, "error": null, "warnings": [], "artifacts": [], "steps": [...],
     "correlation_id": str}

Exit codes: 0 ok, 2 invalid input/usage, 3 blocked by policy or missing approval,
4 executed but failed, 5 internal error, 130 cancelled.

Standard library only. See knowledge-readme.md for the full capability list.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# The shared engine is named with the tools- prefix, which is not a valid Python module
# name, so it is loaded by path and registered under the module name "nexuslib".
import importlib.util  # noqa: E402

_LIB_PATH = SCRIPT_DIR / "tools-nexuslib.py"
if not _LIB_PATH.is_file():
    sys.stderr.write("missing shared engine: expected execution-operations/tools-nexuslib.py next to tools-nexus.py\n")
    raise SystemExit(5)
_spec = importlib.util.spec_from_file_location("nexuslib", _LIB_PATH)
nx = importlib.util.module_from_spec(_spec)
sys.modules["nexuslib"] = nx
_spec.loader.exec_module(nx)


# --------------------------------------------------------------------------- #
# Envelope and output helpers
# --------------------------------------------------------------------------- #


class Result:
    def __init__(self, operation):
        self.operation = operation
        self.data = {}
        self.warnings = []
        self.artifacts = []
        self.steps = []
        self.correlation_id = nx.new_correlation_id()
        self.next_actions = []

    def warn(self, message):
        if message and message not in self.warnings:
            self.warnings.append(message)

    def artifact(self, path):
        if path and str(path) not in self.artifacts:
            self.artifacts.append(str(path))

    def step(self, name, **info):
        entry = {"step": name}
        entry.update(info)
        self.steps.append(entry)

    def envelope(self, ok=True):
        return {
            "ok": ok,
            "operation": self.operation,
            "tool": nx.TOOL_NAME,
            "version": nx.VERSION,
            "correlation_id": self.correlation_id,
            "data": self.data,
            "error": None,
            "warnings": self.warnings,
            "artifacts": self.artifacts,
            "steps": self.steps,
            "next_actions": self.next_actions,
        }


def emit_error(operation, exc, correlation_id, envelope_extra=None):
    payload = {
        "ok": False,
        "operation": operation,
        "tool": nx.TOOL_NAME,
        "version": nx.VERSION,
        "correlation_id": correlation_id,
        "data": envelope_extra or {},
        "error": exc.as_dict() if isinstance(exc, nx.NexusError) else {"code": "internal_error", "message": str(exc), "details": {}},
        "warnings": [],
        "artifacts": [],
        "steps": [],
        "next_actions": [],
    }
    if isinstance(exc, nx.NexusError) and exc.code == "approval_required":
        payload["next_actions"] = ["Review the risk matches, then rerun with --approve --approval-ref \"<who approved and why>\"."]
    if isinstance(exc, nx.NexusError) and exc.code in ("scope_denied", "scope_outside_allowed", "scope_outside_work_root", "network_blocked", "db_write_blocked"):
        payload["next_actions"] = ["Widen scope explicitly in the configuration file, or narrow the target."]
    return payload


def output(payload, args, text_renderer=None):
    emit_json = not getattr(args, "text", False)
    if emit_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        if text_renderer and payload.get("ok"):
            sys.stdout.write(text_renderer(payload) + "\n")
        else:
            sys.stdout.write(render_human(payload) + "\n")
    if not payload.get("ok"):
        return 1


def render_human(payload):
    err = payload.get("error") or {}
    lines = []
    if not payload.get("ok"):
        lines.append("BLOCKED [%s] %s" % (payload.get("operation"), err.get("code")))
        lines.append("  %s" % err.get("message"))
        details = err.get("details") or {}
        if details:
            for key, value in details.items():
                if key in ("fix", "reason", "errors"):
                    lines.append("  %s: %s" % (key, value))
        for action in payload.get("next_actions", []):
            lines.append("  next: %s" % action)
        return "\n".join(lines)
    lines.append("OK [%s] correlation=%s" % (payload.get("operation"), payload.get("correlation_id")))
    for key, value in (payload.get("data") or {}).items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered) > 400:
                rendered = rendered[:400] + "..."
            lines.append("  %s: %s" % (key, rendered))
        else:
            lines.append("  %s: %s" % (key, value))
    for warning in payload.get("warnings", []):
        lines.append("  warning: %s" % warning)
    for action in payload.get("next_actions", []):
        lines.append("  next: %s" % action)
    return "\n".join(lines)


def fail(result, exc):
    """Raise a NexusError while preserving the data already gathered."""
    exc.partial = result
    raise exc


def write_out(path, text, result):
    resolved = nx.resolve_path(path)
    nx.atomic_write(resolved, text)
    result.artifact(resolved)
    return str(resolved)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="JSON config overlay merged over asset-default-config.json (env NEXUS_CONFIG)")
    common.add_argument("--work-root", default=argparse.SUPPRESS, help="override paths.work_root for this invocation")
    common.add_argument("--state-dir", default=argparse.SUPPRESS, help="override paths.state_dir for this invocation")
    common.add_argument("--json", dest="text", action="store_false", default=argparse.SUPPRESS, help="print the structured JSON envelope (default)")
    common.add_argument("--text", dest="text", action="store_true", default=argparse.SUPPRESS, help="print a compact human summary instead of JSON")

    parser = argparse.ArgumentParser(
        parents=[common],
        prog="tools-nexus.py",
        description="Execution Operations skill tool: plan, gate, run, verify, diagnose, orchestrate, and report command work.",
        epilog=(
            "Start with 'capabilities' for the capability list, 'route --query \"...\"' to map an objective onto "
            "catalogued capabilities, and 'exec' to run one bounded command. Read knowledge-readme.md for every "
            "operation, parameter, limit, and safety rule."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="execution-operations %s" % nx.VERSION)
    sub = parser.add_subparsers(dest="operation", metavar="<operation>")

    def add(name, help_text, func):
        p = sub.add_parser(name, parents=[common], help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    p = add("capabilities", "List every operation, category, and catalogued capability of this skill tool.", cmd_capabilities)
    p.add_argument("--category", help="filter to one category name or fragment")
    p.add_argument("--skill", help="filter to matching catalogued capability names")
    p.add_argument("--domain", help="filter to a domain tag, e.g. recovery or security")
    p.add_argument("--out", help="write the capability listing as JSON to this path")

    p = add("route", "Map an objective or category onto catalogued capabilities and the operations that implement them.", cmd_route)
    p.add_argument("--query", help="free-text objective, e.g. 'deploy a canary and roll back on error'")
    p.add_argument("--category", help="category name or fragment")
    p.add_argument("--limit", type=int, default=10, help="maximum matches to return (default 10)")
    p.add_argument("--out", help="write the routing result as JSON to this path")

    p = add("plan", "Compile an objective, category, or capability into an executable plan with risk pre-checks.", cmd_plan)
    p.add_argument("--objective", help="natural-language objective")
    p.add_argument("--category", help="category name or fragment")
    p.add_argument("--skill", help="catalogued capability name")
    p.add_argument("--commands", nargs="*", default=[], help="explicit commands, in order, to include in the plan")
    p.add_argument("--shell", default="auto", help="shell dialect for the plan (default auto)")
    p.add_argument("--timeout", type=float, help="per-step timeout in seconds")
    p.add_argument("--out", help="write the plan JSON to this path")

    p = add("risk", "Assess a command or script for destructive, privileged, publishing, or secret-exposing risk.", cmd_risk)
    p.add_argument("command_text", nargs="?", help="command text to assess")
    p.add_argument("--file", help="read the command or script from this file instead")
    p.add_argument("--level", choices=["none", "low", "medium", "high", "critical"], help="declare an extra minimum risk level")

    p = add("scope-check", "Validate target paths against allowed roots, denied roots, and ambiguity rules.", cmd_scope_check)
    p.add_argument("paths", nargs="+", help="paths to check")
    p.add_argument("--allow-create", action="store_true", help="do not require the path to exist yet")

    p = add("contract", "Validate an execution contract before a critical workflow uses it.", cmd_contract)
    p.add_argument("--file", help="contract JSON file")
    p.add_argument("--contract-json", dest="inline_json", help="contract JSON given inline")

    p = add("approve", "Record an explicit approval decision in the audit trail (never executes anything).", cmd_approve)
    p.add_argument("--command", required=True, help="the exact command or change being approved")
    p.add_argument("--approval-ref", required=True, help="who approved it and why")
    p.add_argument("--decision", choices=["approved", "denied"], default="approved")
    p.add_argument("--risk-level", choices=["none", "low", "medium", "high", "critical"], help="declared risk level")

    p = add("exec", "Run one bounded command with risk gating, timeout, stall detection, retries, and redaction.", cmd_exec)
    p.add_argument("--command", help="command text to run")
    p.add_argument("--file", help="read the command or script from this file instead of --command")
    p.add_argument("--shell", default="auto", help="auto|bash|sh|zsh|cmd|powershell (default auto)")
    p.add_argument("--cwd", help="working directory (must be inside the permitted scope)")
    p.add_argument("--timeout", type=float, help="hard deadline in seconds")
    p.add_argument("--retries", type=int, help="maximum retries after a retryable failure (default from config)")
    p.add_argument("--heartbeat", action="store_true", help="enforce the heartbeat pattern and kill stalled jobs")
    p.add_argument("--heartbeat-pattern", help="regular expression that marks a progress heartbeat")
    p.add_argument("--stall-timeout", type=float, help="seconds without a heartbeat before a stall is declared")
    p.add_argument("--checkpoint", help="checkpoint file used to record or resume completion")
    p.add_argument("--resume", action="store_true", help="skip work already marked complete in --checkpoint")
    p.add_argument("--artifact", action="append", default=[], help="path that must exist after the run (repeatable)")
    p.add_argument("--env", action="append", default=[], help="extra environment variable KEY=VALUE (repeatable)")
    p.add_argument("--capture-dir", help="write stdout.log and stderr.log here")
    p.add_argument("--report", help="render a Markdown report to this path")
    p.add_argument("--title", help="report title used with --report")
    p.add_argument("--no-trace", action="store_true", help="do not append to the execution trace")
    p.add_argument("--dry-run", action="store_true", help="validate and show the plan without executing")
    p.add_argument("--approve", action="store_true", help="record explicit approval for a risky command")
    p.add_argument("--approval-ref", help="who approved the action and why (required with --approve)")
    p.add_argument("--allow-outside", action="store_true", help="allow a working directory outside the work root")

    p = add("run-plan", "Execute a plan produced by 'plan': sequential bounded steps with per-step gating.", cmd_run_plan)
    p.add_argument("--file", required=True, help="plan JSON file")
    p.add_argument("--dry-run", action="store_true", help="validate every step without executing")
    p.add_argument("--approve", action="store_true", help="approve the risky steps of this plan")
    p.add_argument("--approval-ref", help="who approved and why")
    p.add_argument("--report", help="render a Markdown report to this path")
    p.add_argument("--title", help="report title used with --report")
    p.add_argument("--stop-on-error", action="store_true", help="stop at the first failed step (default)")

    p = add("orchestrate", "Execute a workflow DAG with bounded parallelism, retries, checkpoints, and evidence.", cmd_orchestrate)
    p.add_argument("--workflow", required=True, help="workflow JSON file (see asset-workflow-schema.json)")
    p.add_argument("--max-parallel", type=int, help="override orchestration.max_parallel")
    p.add_argument("--dry-run", action="store_true", help="validate the workflow without executing")
    p.add_argument("--no-resume", action="store_true", help="ignore an existing orchestration checkpoint")
    p.add_argument("--approve", action="store_true", help="approve nodes that require approval")
    p.add_argument("--approval-ref", help="who approved and why")
    p.add_argument("--report", help="render a Markdown report to this path")
    p.add_argument("--title", help="report title used with --report")

    p = add("validate-workflow", "Validate a workflow DAG against the published schema rules without running it.", cmd_validate_workflow)
    p.add_argument("--workflow", required=True, help="workflow JSON file")

    p = add("estimate", "Estimate duration and allocate a stage budget from workload parameters.", cmd_estimate)
    p.add_argument("--units", type=float, required=True, help="number of work units, e.g. files or records")
    p.add_argument("--unit-seconds", type=float, required=True, help="measured seconds per unit")
    p.add_argument("--concurrency", type=float, default=1, help="parallel workers (default 1)")
    p.add_argument("--warmup", type=float, default=0, help="setup seconds (default 0)")
    p.add_argument("--latency", type=float, default=0, help="network latency seconds (default 0)")
    p.add_argument("--verify-ratio", type=float, default=0.25, help="verification cost as a ratio of execution (default 0.25)")
    p.add_argument("--retry-allowance", type=float, default=0.1, help="retry budget as a ratio of execution (default 0.1)")

    p = add("parse-log", "Parse JSONL or plain logs: levels, errors, correlation ids, and a noise-reduced excerpt.", cmd_parse_log)
    p.add_argument("--file", required=True, help="log file")
    p.add_argument("--max-records", type=int, help="maximum records to parse")
    p.add_argument("--dedupe", action="store_true", help="group repeated level+message pairs")
    p.add_argument("--out", help="write the parsed summary as JSON to this path")

    p = add("trace", "Record or read the reproducible execution trace.", cmd_trace)
    p.add_argument("--show", action="store_true", help="show the most recent trace entries")
    p.add_argument("--limit", type=int, default=10, help="entries to show (default 10)")
    p.add_argument("--note", help="record a manual trace note")
    p.add_argument("--command", help="command text to record")
    p.add_argument("--exit-code", type=int, help="exit code to record")

    p = add("diagnose", "Classify a failure by layer from an exit code, signal, or stderr text, with probes to run.", cmd_diagnose)
    p.add_argument("--exit-code", type=int, help="return code")
    p.add_argument("--signal", type=int, help="signal number")
    p.add_argument("--stderr", help="stderr file")
    p.add_argument("--stderr-text", dest="stderr_text", help="stderr text given inline")

    p = add("secrets-scan", "Scan files or directories for credential patterns and print redacted findings.", cmd_secrets_scan)
    p.add_argument("paths", nargs="+", help="files or directories to scan")
    p.add_argument("--max-bytes", type=int, default=2000000, help="per-file byte limit (default 2000000)")
    p.add_argument("--out", help="write findings as JSON to this path")

    p = add("translate", "Translate a command between bash, sh, zsh, cmd, and PowerShell dialects.", cmd_translate)
    p.add_argument("--command", required=True, help="command text to translate")
    p.add_argument("--from", dest="source", required=True, help="bash|sh|zsh|cmd|powershell")
    p.add_argument("--to", dest="target", required=True, help="bash|sh|zsh|cmd|powershell")
    p.add_argument("--out", help="write the translated command to this file")

    p = add("schedule", "Generate cron, systemd, GitHub Actions, and schtasks definitions as text (installs nothing).", cmd_schedule)
    p.add_argument("--spec", help="schedule spec JSON file")
    p.add_argument("--command", help="command to schedule")
    p.add_argument("--every", type=int, help="interval size")
    p.add_argument("--unit", default="minutes", choices=["minutes", "hours", "days"], help="interval unit")
    p.add_argument("--minute", help="cron minute field")
    p.add_argument("--hour", help="cron hour field")
    p.add_argument("--day-of-week", help="cron day-of-week field")
    p.add_argument("--timezone", help="IANA timezone name recorded in the definition (default UTC)")
    p.add_argument("--overlap", choices=["skip", "queue", "replace", "allow"], default="skip", help="overlap policy")
    p.add_argument("--missed", choices=["skip", "run_once", "catch_up"], default="skip", help="missed-run policy")
    p.add_argument("--retries", type=int, default=0, help="retry count recorded in the definition")
    p.add_argument("--name", help="job name")
    p.add_argument("--timeout-seconds", type=int, default=1800, help="job timeout recorded in the definition")
    p.add_argument("--format", dest="out_format", default="all", choices=["all", "cron", "systemd", "github", "schtasks"], help="which artifact to emit as text")
    p.add_argument("--out", help="write the definitions to this file")

    p = add("data-transform", "Transform JSON, CSV, TSV, or JSONL data with a deterministic step spec.", cmd_data_transform)
    p.add_argument("--input", required=True, help="input dataset")
    p.add_argument("--format", dest="in_format", help="json|csv|tsv|jsonl (default from extension)")
    p.add_argument("--spec", help="transform spec JSON file")
    p.add_argument("--spec-json", help="transform spec given inline")
    p.add_argument("--output", help="write the transformed dataset here")
    p.add_argument("--output-format", default="json", choices=list(nx.SUPPORTED_FORMATS), help="output format (default json)")

    p = add("data-validate", "Validate a dataset against required fields, types, uniqueness, ranges, and patterns.", cmd_data_validate)
    p.add_argument("--input", required=True, help="input dataset")
    p.add_argument("--format", dest="in_format", help="json|csv|tsv|jsonl (default from extension)")
    p.add_argument("--spec", required=True, help="validation spec JSON file")
    p.add_argument("--out", help="write the validation report as JSON to this path")

    p = add("db-query", "Run one SQL statement against a SQLite database inside an allowed root (read-only by default).", cmd_db_query)
    p.add_argument("--db", required=True, help="SQLite database file")
    p.add_argument("--sql", help="SQL statement")
    p.add_argument("--sql-file", help="read the SQL statement from this file")
    p.add_argument("--param", action="append", default=[], help="named parameter name=value (repeatable)")
    p.add_argument("--allow-write", action="store_true", help="permit a mutating statement")
    p.add_argument("--max-rows", type=int, help="row limit for the result set")
    p.add_argument("--out", help="write the result rows as JSON to this path")
    p.add_argument("--approve", action="store_true", help="approve a destructive statement")
    p.add_argument("--approval-ref", help="who approved and why")

    p = add("http", "Perform one bounded HTTP request against an explicitly allowed host.", cmd_http)
    p.add_argument("--url", required=True, help="http or https URL")
    p.add_argument("--method", default="GET", help="GET|HEAD|POST|PUT|PATCH|DELETE (default GET)")
    p.add_argument("--header", action="append", default=[], help="request header KEY=VALUE (repeatable, no credentials)")
    p.add_argument("--body", help="request body text")
    p.add_argument("--allow-host", help="add this host to the allowlist for this invocation")
    p.add_argument("--out", help="write the response body to this file")
    p.add_argument("--approve", action="store_true", help="approve a mutating request")
    p.add_argument("--approval-ref", help="who approved and why")

    p = add("verify", "Verify artifacts exist, are non-empty, parse as JSON, and record their hashes.", cmd_verify)
    p.add_argument("artifacts", nargs="+", help="artifact paths")
    p.add_argument("--min-bytes", type=int, default=1, help="minimum size for a file artifact (default 1)")
    p.add_argument("--require-json", action="store_true", help="require each artifact to parse as JSON")
    p.add_argument("--out", help="write the verification record as JSON to this path")

    p = add("report", "Render a Markdown execution report from a run record using the report template asset.", cmd_report)
    p.add_argument("--run", help="run record JSON produced by exec or orchestrate (use --out on those commands)")
    p.add_argument("--from-trace", action="store_true", help="build the report from the most recent trace entry")
    p.add_argument("--output", required=True, help="write the Markdown report here")
    p.add_argument("--title", help="report title")

    add("doctor", "Check the runtime, shells, SQLite, writable state, assets, and network posture.", cmd_doctor)

    p = add("checkpoint", "Inspect, update, or reset a checkpoint file used by resumable work.", cmd_checkpoint)
    p.add_argument("--file", required=True, help="checkpoint JSON file")
    p.add_argument("--show", action="store_true", help="show the checkpoint contents")
    p.add_argument("--set", action="append", default=[], help="set a key=value entry (repeatable)")
    p.add_argument("--complete", action="append", default=[], help="mark a work unit complete (repeatable)")
    p.add_argument("--reset", action="store_true", help="delete the checkpoint file")

    return parser


# --------------------------------------------------------------------------- #
# Command implementations
# --------------------------------------------------------------------------- #

OPERATIONS = [
    ("capabilities", "List operations, categories, and catalogued capabilities."),
    ("route", "Map an objective onto catalogued capabilities and implementing operations."),
    ("plan", "Compile an objective or category into an executable, risk-checked plan."),
    ("run-plan", "Execute a plan sequentially with per-step gating and evidence."),
    ("exec", "Run one bounded command with timeout, stall detection, retries, redaction."),
    ("orchestrate", "Execute a workflow DAG with bounded parallelism and checkpoints."),
    ("validate-workflow", "Validate a workflow DAG without executing it."),
    ("risk", "Assess destructive, privileged, publishing, or secret-exposing risk."),
    ("scope-check", "Validate paths against allowed and denied roots."),
    ("contract", "Validate an execution contract for a critical workflow."),
    ("approve", "Record an explicit approval decision in the audit trail."),
    ("estimate", "Estimate duration and allocate a stage time budget."),
    ("parse-log", "Parse logs into levels, errors, correlation ids, and excerpts."),
    ("trace", "Record or read the reproducible execution trace."),
    ("diagnose", "Classify a failure by layer with concrete probes."),
    ("secrets-scan", "Find credential patterns in files without printing them."),
    ("translate", "Translate commands across shell dialects."),
    ("schedule", "Generate cron, systemd, CI, and schtasks definitions as text."),
    ("data-transform", "Deterministic filter/map/dedupe/sort/aggregate pipeline."),
    ("data-validate", "Validate datasets against a field and type contract."),
    ("db-query", "Run one bounded SQLite statement with a read-only default."),
    ("http", "Perform one bounded HTTP request against an allowed host."),
    ("verify", "Verify artifacts exist, are non-empty, and parse."),
    ("report", "Render a Markdown execution report."),
    ("doctor", "Check runtime, shells, state, assets, and network posture."),
    ("checkpoint", "Inspect, update, or reset resumable checkpoints."),
]


def cmd_capabilities(args, config, result):
    skill_map = nx.load_skill_map(config)
    categories = skill_map["categories"]
    if args.category:
        cat = nx.find_category(skill_map, args.category)
        if not cat:
            raise nx.NexusError(
                "no category matched the filter",
                code="category_not_found",
                details={"filter": args.category, "available": [c["category"] for c in categories]},
            )
        categories = [cat]
    skills = []
    if args.skill:
        matches = nx.find_skills(skill_map, args.skill, limit=200)
        if not matches:
            raise nx.NexusError("no capability matched the filter", code="skill_not_found", details={"filter": args.skill})
        names = {m["name"] for m in matches}
        skills = matches
        categories = [c for c in categories if any(s["name"] in names for s in c["skills"])]
    if args.domain:
        dom = args.domain.lower()
        categories = [
            {**c, "skills": [s for s in c["skills"] if dom in [d.lower() for d in s.get("domains", [])]]}
            for c in categories
        ]
        categories = [c for c in categories if c["skills"]]
        if not categories:
            raise nx.NexusError("no capability matched the domain filter", code="domain_not_found", details={"filter": args.domain})
    payload_categories = []
    total = 0
    for cat in categories:
        entry_skills = skills or cat["skills"]
        if skills:
            entry_skills = [s for s in skills if s["category"] == cat["category"]]
        payload_categories.append(
            {
                "index": cat["index"],
                "category": cat["category"],
                "skill_count": len(entry_skills),
                "skills": [
                    {
                        "name": s["name"],
                        "slug": s.get("slug") or nx.slugify(s["name"]),
                        "description": s["description"],
                        "operations": s.get("operations", []),
                        "domains": s.get("domains", []),
                        "risk": s.get("risk"),
                    }
                    for s in entry_skills
                ],
            }
        )
        total += len(entry_skills)
    result.data = {
        "tool": nx.TOOL_NAME,
        "version": nx.VERSION,
        "operations": [{"name": n, "summary": s} for n, s in OPERATIONS],
        "operation_count": len(OPERATIONS),
        "catalog": {
            "source": skill_map.get("source"),
            "total_categories": skill_map.get("total_categories"),
            "total_skills": skill_map.get("total_skills"),
            "returned_categories": len(payload_categories),
            "returned_skills": total,
            "categories": payload_categories,
        },
        "config": {"sources": config.sources, "required_approval_levels": config.get("approval.required_levels")},
    }
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(result.data, indent=1), result)


def cmd_route(args, config, result):
    skill_map = nx.load_skill_map(config)
    if not args.query and not args.category:
        raise nx.NexusError("route needs --query or --category", code="missing_input", details={"example": "--query \"parallel build and test\""})
    matches = []
    categories = []
    if args.category:
        cat = nx.find_category(skill_map, args.category)
        if not cat:
            raise nx.NexusError("no category matched", code="category_not_found", details={"filter": args.category})
        categories.append({"index": cat["index"], "category": cat["category"], "skill_count": len(cat["skills"])})
        for skill in cat["skills"]:
            matches.append({**skill, "category": cat["category"], "slug": nx.slugify(skill["name"])})
    if args.query:
        found = nx.find_skills(skill_map, args.query, limit=max(1, args.limit))
        if not found:
            raise nx.NexusError(
                "nothing in the catalog matched that objective",
                code="no_route",
                details={"query": args.query, "fix": "Try a broader term such as 'recovery', 'testing', 'deploy', or 'logs'."},
            )
        seen = {m["name"] for m in matches}
        for skill in found:
            if skill["name"] not in seen:
                matches.append(skill)
        cat_hits = [c for c in skill_map["categories"] if any(m["category"] == c["category"] for m in matches)]
        categories = [{"index": c["index"], "category": c["category"], "skill_count": len([m for m in matches if m["category"] == c["category"]])} for c in cat_hits]
    operations = sorted({op for m in matches for op in m.get("operations", [])})
    result.data = {
        "query": args.query,
        "matches": [
            {
                "name": m["name"],
                "slug": m["slug"],
                "category": m["category"],
                "risk": m.get("risk"),
                "operations": m.get("operations", []),
                "description": m["description"],
            }
            for m in matches[: max(1, args.limit) if args.query else len(matches)]
        ],
        "categories": categories,
        "recommended_operations": operations,
        "next_step": "Use 'plan' with the same objective to build an executable plan, then 'run-plan' with --dry-run.",
    }
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(result.data, indent=1), result)


def cmd_plan(args, config, result):
    if not any([args.objective, args.category, args.skill, args.commands]):
        raise nx.NexusError(
            "plan needs --objective, --category, --skill, or --commands",
            code="missing_input",
            details={"example": "--objective \"run tests and verify artifacts\" --commands \"pytest -q\""},
        )
    skill_map = nx.load_skill_map(config)
    matched = []
    category_name = None
    if args.category:
        cat = nx.find_category(skill_map, args.category)
        if not cat:
            raise nx.NexusError("no category matched", code="category_not_found", details={"filter": args.category})
        category_name = cat["category"]
        matched = [{**s, "category": cat["category"]} for s in cat["skills"]]
    if args.skill:
        found = nx.find_skills(skill_map, args.skill, limit=5)
        if not found:
            raise nx.NexusError("no capability matched", code="skill_not_found", details={"filter": args.skill})
        if found[0]["name"].lower() != args.skill.lower() and not found[0]["score"] >= 5:
            result.warn("no exact capability name matched; using the closest: %s" % found[0]["name"])
        matched = matched or [{**found[0], "category": found[0]["category"]}]
        category_name = category_name or found[0]["category"]
    if args.objective:
        found = nx.find_skills(skill_map, args.objective, limit=3)
        matched = matched or [{**s, "category": s["category"]} for s in found]
        if found and not category_name:
            category_name = found[0]["category"]
    policy = config.load_asset("policy_file")
    steps = []
    for index, command in enumerate(args.commands, start=1):
        risk = nx.assess_risk(command, policy)
        steps.append(
            {
                "id": "step-%02d" % index,
                "command": command,
                "shell": args.shell,
                "timeout_seconds": args.timeout or config.get("execution.default_timeout_seconds"),
                "risk": {"level": risk["level"], "flags": risk["flags"]},
                "requires_approval": nx.required_approval(risk, config),
                "artifacts": [],
            }
        )
    if not steps and matched:
        # Derive suggested steps from the matched capabilities' operations.
        for op in sorted({op for m in matched for op in m.get("operations", [])}):
            steps.append({"id": "step-%s" % op, "operation": op, "note": "Run 'tools-nexus.py %s --help' for the exact arguments." % op})
    objective_text = args.objective or ""
    text_risk = nx.assess_risk(objective_text, policy) if objective_text else {"level": "none", "flags": [], "matches": []}
    plan = {
        "plan_version": "1.0",
        "created_at": nx.now_iso(),
        "objective": args.objective,
        "category": category_name,
        "capabilities": [{"name": m["name"], "slug": nx.slugify(m["name"]), "risk": m.get("risk")} for m in matched],
        "executable": bool(args.commands),
        "steps": steps,
        "objective_risk": {"level": text_risk["level"], "flags": text_risk.get("flags", [])},
        "approval_policy": {"required_levels": config.get("approval.required_levels")},
        "notes": [
            "Steps are executed in order by 'run-plan'. Each step is risk-checked again at execution time.",
            "Approval is never implied by planning: pass --approve --approval-ref at execution time.",
        ],
    }
    if not plan["executable"]:
        result.warn("The plan contains no runnable commands; pass --commands to make it executable through run-plan.")
    result.data = plan
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(plan, indent=1), result)


def cmd_risk(args, config, result):
    if args.file:
        text = nx.read_text(nx.resolve_path(args.file))
        source = str(nx.resolve_path(args.file))
    elif args.command_text:
        text = args.command_text
        source = "inline"
    else:
        raise nx.NexusError("risk needs command text or --file", code="missing_input")
    policy = config.load_asset("policy_file")
    risk = nx.assess_risk(text, policy, args.level)
    result.data = {
        "source": source,
        "risk_level": risk["level"],
        "risk_rank": risk["rank"],
        "approval_required": nx.required_approval(risk, config),
        "matches": risk["matches"],
        "flags": risk["flags"],
        "secrets_found": nx.scan_secrets(text),
        "safe": risk["level"] in ("none", "low"),
    }
    if risk["level"] in ("high", "critical"):
        result.next_actions.append("Review each match, prefer the safer alternative, then require --approve with a recorded reference.")
    if result.data["secrets_found"]:
        result.warn("Secret-like values were detected in the input; they are redacted in output and must not be committed.")


def cmd_scope_check(args, config, result):
    policy = config.load_asset("policy_file")
    checked = []
    failures = []
    for raw in args.paths:
        try:
            p = nx.check_scope(raw, config, policy, label="path", allow_create=args.allow_create)
            checked.append({"path": str(p), "status": "allowed", "exists": p.exists()})
        except nx.NexusError as exc:
            checked.append({"path": raw, "status": "denied", "code": exc.code, "detail": exc.as_dict()})
            failures.append(exc.as_dict())
    result.data = {
        "work_root": str(nx.work_root(config)),
        "allowed_roots": config.get("scope.allowed_roots"),
        "denied_roots": config.get("scope.denied_roots"),
        "checked": checked,
        "denied_count": len(failures),
    }
    if failures:
        result.data["failures"] = failures
        fail(
            result,
            nx.NexusError(
                "%d of %d paths failed the scope check" % (len(failures), len(args.paths)),
                code="scope_check_failed",
                details={"failures": failures},
                exit_code=3,
            ),
        )


def cmd_contract(args, config, result):
    if args.file:
        raw = nx.read_text(nx.resolve_path(args.file))
    elif args.inline_json:
        raw = args.inline_json
    else:
        raise nx.NexusError("contract needs --file or --contract-json", code="missing_input")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise nx.NexusError("contract is not valid JSON", code="contract_invalid", details={"line": exc.lineno, "message": exc.msg})
    if isinstance(doc, dict) and "contract" in doc:
        doc = doc["contract"]
    required = ["command", "expected_inputs", "permitted_side_effects", "success_signals", "failure_signals", "output_paths", "cleanup"]
    errors = []
    if not isinstance(doc, dict):
        errors.append("contract must be a JSON object")
    else:
        for field in required:
            if field not in doc:
                errors.append("missing required field: %s" % field)
        for field in ("expected_inputs", "permitted_side_effects", "success_signals", "failure_signals", "output_paths"):
            if field in doc and not isinstance(doc[field], list):
                errors.append("%s must be an array" % field)
            if field in doc and isinstance(doc[field], list) and not doc[field] and field not in ("expected_inputs", "permitted_side_effects"):
                errors.append("%s must not be empty" % field)
        if isinstance(doc.get("command"), str):
            policy = config.load_asset("policy_file")
            risk = nx.assess_risk(doc["command"], policy)
        else:
            risk = {"level": "unknown", "matches": [], "flags": []}
        if "success_signals" in doc and isinstance(doc.get("success_signals"), list) and not any(
            isinstance(s, str) and ("exit" in s.lower() or "0" in s or "artifact" in s.lower() or "file" in s.lower()) for s in doc["success_signals"]
        ):
            errors.append("success_signals should name an observable signal such as exit code 0 or an artifact path")
    result.data = {
        "valid": not errors,
        "errors": errors,
        "required_fields": required,
        "contract": doc if isinstance(doc, dict) else None,
        "risk": {"level": risk.get("level"), "flags": risk.get("flags", []), "matches": risk.get("matches", [])},
        "approval_required": bool(risk.get("level") in config.get("approval.required_levels", [])),
        "checklist": [
            "Inputs are enumerated and validated before execution.",
            "Side effects are scoped and reversible or explicitly unrecoverable.",
            "Success and failure signals are observable, not assumed.",
            "Output paths are declared and verified afterwards.",
            "Cleanup runs on success, failure, and cancellation.",
        ],
    }
    if errors:
        fail(result, nx.NexusError("contract is incomplete", code="contract_invalid", details={"errors": errors}))


def cmd_approve(args, config, result):
    policy = config.load_asset("policy_file")
    risk = nx.assess_risk(args.command, policy, args.risk_level)
    if args.decision == "approved" and risk["level"] in ("high", "critical"):
        result.warn("Recording approval for a %s-risk action; the audit entry will show the matched rules." % risk["level"])
    record = {
        "recorded_at": nx.now_iso(),
        "decision": args.decision,
        "approval_ref": args.approval_ref,
        "risk_level": risk["level"],
        "risk_flags": risk["flags"],
        "command_sha256": nx.sha256_text(args.command),
        "command_preview": nx.redact(args.command, config)[:400],
        "operator": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
        "correlation_id": result.correlation_id,
    }
    path = nx.write_audit(config, record)
    result.data = {"audit_file": path, "record": record}
    result.artifact(path)
    result.next_actions.append("Pass --approve --approval-ref at execution time; the audit entry is evidence, not an enforcement token.")


def _validate_approval(args, config):
    if args.approve and not args.approval_ref:
        raise nx.NexusError(
            "--approve requires --approval-ref",
            code="approval_reference_missing",
            details={"fix": "Pass --approval-ref \"<who approved and why>\"."},
            exit_code=3,
        )


def cmd_exec(args, config, result):
    if args.file:
        command = nx.read_text(nx.resolve_path(args.file))
        source = "file:%s" % args.file
    elif args.command:
        command = args.command
        source = "inline"
    else:
        raise nx.NexusError("exec needs --command or --file", code="missing_input", details={"example": "--command \"python3 -V\""})
    command = command.strip("\n")
    if not command.strip():
        raise nx.NexusError("command is empty", code="empty_command")
    _validate_approval(args, config)
    policy = config.load_asset("policy_file")
    exit_table = config.load_asset("exit_codes_file")
    risk = nx.assess_risk(command, policy)
    needs_approval = nx.required_approval(risk, config)
    result.step("validate", command_length=len(command), source=source)
    result.step("risk", level=risk["level"], flags=risk["flags"])

    cwd = args.cwd
    if args.allow_outside:
        override = dict(config.data)
        override["scope"] = {**override.get("scope", {}), "allow_outside_work_root": True}
        config = nx.Config(config_path=None, overrides=override, scripts_dir=config.scripts_dir)
    workdir = nx.resolve_path(cwd, base=nx.work_root(config)) if cwd else nx.work_root(config)
    if not workdir.is_dir():
        raise nx.NexusError("working directory does not exist", code="workdir_missing", details={"workdir": str(workdir)})
    nx.check_scope(workdir, config, policy, label="working directory")

    if needs_approval and not args.approve:
        result.data = {
            "dry_run": True,
            "blocked_reason": "approval_required",
            "command": nx.redact(command, config),
            "risk": risk,
            "would_run": {"shell": args.shell, "cwd": str(workdir), "timeout_seconds": args.timeout or config.get("execution.default_timeout_seconds")},
            "approval_policy": {"required_levels": config.get("approval.required_levels")},
        }
        fail(
            result,
            nx.NexusError(
                "command requires approval before execution (%s risk)" % risk["level"],
                code="approval_required",
                details={
                    "risk": risk,
                    "command_preview": nx.redact(command, config)[:500],
                    "fix": "Review the matches and rerun with --approve --approval-ref \"<who/why>\", or use the safer alternative listed for each match.",
                },
                exit_code=3,
            ),
        )
    if needs_approval and args.approve:
        nx.write_audit(
            config,
            {
                "recorded_at": nx.now_iso(),
                "decision": "approved_at_exec",
                "approval_ref": args.approval_ref,
                "risk_level": risk["level"],
                "risk_flags": risk["flags"],
                "command_sha256": nx.sha256_text(command),
                "correlation_id": result.correlation_id,
                "operation": "exec",
            },
        )
        result.step("approval", level=risk["level"], reference=args.approval_ref)

    if args.checkpoint and args.resume:
        stored = nx.checkpoint_load(args.checkpoint)
        if stored and not stored.get("corrupt"):
            done = stored.get("completed") or []
            marker = "exec:" + nx.sha256_text(command)[:16]
            if marker in done:
                result.data = {
                    "resumed": True,
                    "skipped": True,
                    "reason": "checkpoint already records this exact command as complete",
                    "checkpoint": str(args.checkpoint),
                    "completed_units": done,
                }
                result.next_actions.append("Use 'checkpoint --reset' or a different --checkpoint to force a rerun.")
                return

    if args.heartbeat_pattern:
        override = dict(config.data)
        override["execution"] = {**override.get("execution", {}), "heartbeat_pattern": args.heartbeat_pattern}
        config = nx.Config(config_path=None, overrides=override, scripts_dir=config.scripts_dir)

    retries = args.retries if args.retries is not None else int(config.get("retry.max_attempts", 3)) - 1
    if retries < 0 or retries > 10:
        raise nx.NexusError("retries must be between 0 and 10", code="invalid_retries", details={"retries": retries})
    attempts_allowed = retries + 1
    env_extra = {}
    for item in args.env:
        if "=" not in item:
            raise nx.NexusError("--env expects KEY=VALUE", code="invalid_env", details={"received": item})
        key, value = item.split("=", 1)
        if not key.strip():
            raise nx.NexusError("--env key is empty", code="invalid_env", details={"received": item})
        env_extra[key.strip()] = value

    if args.dry_run:
        result.data = {
            "dry_run": True,
            "command": nx.redact(command, config),
            "shell": args.shell,
            "cwd": str(workdir),
            "timeout_seconds": args.timeout or config.get("execution.default_timeout_seconds"),
            "max_attempts": attempts_allowed,
            "risk": risk,
            "approval_required": needs_approval,
            "approval_satisfied": bool(args.approve),
            "planned_env_keys": sorted(env_extra),
            "artifacts_expected": args.artifact,
        }
        result.next_actions.append("Remove --dry-run to execute.")
        return

    cancel = nx._Cancel()
    previous = {}

    def handler(signum, frame):
        cancel.set("signal:%d" % signum)
        result.warn("Cancellation requested by signal %d; the child is being stopped and cleaned up." % signum)

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass
    attempts = []
    final = None
    try:
        for attempt in range(1, attempts_allowed + 1):
            record = nx.run_command(
                command,
                config,
                shell=args.shell,
                cwd=workdir,
                timeout=args.timeout,
                heartbeat_enforced=args.heartbeat,
                stall_timeout=args.stall_timeout,
                cancel=cancel,
                capture_dir=args.capture_dir,
                env_extra=env_extra,
            )
            interp = nx.interpret(record["exit_code"], record.get("stderr", ""), record.get("signal"), exit_table)
            record["interpretation"] = interp
            record["attempt"] = attempt
            attempts.append(
                {
                    "attempt": attempt,
                    "exit_code": record["exit_code"],
                    "state": interp["state"],
                    "duration_seconds": record["duration_seconds"],
                    "timed_out": record["timed_out"],
                    "stalled": record["stalled"],
                }
            )
            final = record
            if interp["state"] == "success":
                break
            if attempt == attempts_allowed:
                break
            if record["timed_out"] or record["cancelled"] or record["stalled"]:
                break
            if not interp["retryable"] and interp["state"] not in config.get("retry.retry_on_states", []):
                break
            delay = nx.backoff_delay(attempt, config.get("retry", {}))
            result.step("retry", attempt=attempt + 1, delay_seconds=delay, reason=interp["state"])
            time.sleep(delay)
    finally:
        for sig, old in previous.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):  # pragma: no cover
                pass

    interp = final["interpretation"]
    artifacts_report = []
    missing = []
    for raw in args.artifact:
        p = nx.resolve_path(raw, base=nx.work_root(config))
        exists = p.exists()
        entry = {"path": str(p), "exists": exists, "size_bytes": p.stat().st_size if exists else 0,
                 "sha256": nx.sha256_file(p) if exists and p.is_file() else None}
        artifacts_report.append(entry)
        if not exists:
            missing.append(str(p))

    success = interp["state"] == "success" and not missing
    if missing:
        interp = dict(interp)
        interp["state"] = "artifact_missing"
        interp["actions"] = list(interp["actions"]) + ["Declared artifacts are missing: %s" % ", ".join(missing)]
    if final["timed_out"]:
        interp = dict(interp)
        interp["state"] = "timed_out"
        interp["actions"] = ["Increase --timeout only when progress evidence exists; otherwise reduce the payload."] + list(interp["actions"])
    if final["stalled"]:
        interp = dict(interp)
        interp["state"] = "stalled"
        interp["actions"] = ["No heartbeat within the stall window; check for deadlock, blocked I/O, or a wrong heartbeat pattern."] + list(interp["actions"])
    if final["cancelled"]:
        interp = dict(interp)
        interp["state"] = "cancelled"
        interp["actions"] = ["Work was cancelled cooperatively; resume from a checkpoint if the job is resumable."] + list(interp["actions"])

    run_record = {
        "correlation_id": result.correlation_id,
        "recorded_at": nx.now_iso(),
        "command": final["command"],
        "shell": final["shell"],
        "workdir": final["workdir"],
        "exit_code": final["exit_code"],
        "state": interp["state"],
        "category": interp["category"],
        "retryable": interp["retryable"],
        "duration_seconds": final["duration_seconds"],
        "attempts": attempts,
        "timed_out": final["timed_out"],
        "stalled": final["stalled"],
        "cancelled": final["cancelled"],
        "heartbeats": final["heartbeats"],
        "idle_seconds": final["idle_seconds"],
        "output_truncated": final["output_truncated"],
        "artifacts": artifacts_report,
        "risk": {"level": risk["level"], "flags": risk["flags"], "approval_satisfied": bool(args.approve) or not needs_approval},
        "interpretation": interp,
        "stdout_tail": nx.tail_lines(final["stdout"], 40),
        "stderr_tail": nx.tail_lines(final["stderr"], 40),
    }

    if args.checkpoint:
        stored = nx.checkpoint_load(args.checkpoint) or {}
        if stored.get("corrupt"):
            stored = {"previous_corrupt": True}
        completed = list(stored.get("completed") or [])
        marker = "exec:" + nx.sha256_text(command)[:16]
        if success and marker not in completed:
            completed.append(marker)
        nx.checkpoint_write(
            args.checkpoint,
            {
                "command_sha256": nx.sha256_text(command),
                "completed": completed,
                "last_state": interp["state"],
                "last_exit_code": final["exit_code"],
                "config_sha256": nx.sha256_text(json.dumps(config.data, sort_keys=True)),
            },
        )
        run_record["checkpoint"] = str(args.checkpoint)
        result.artifact(args.checkpoint)

    if not args.no_trace:
        trace_path = nx.append_trace(config, run_record)
        run_record["trace_file"] = trace_path
        result.artifact(trace_path)
    run_file = nx.run_path(config, "run-%s.json" % result.correlation_id)
    nx.atomic_write(run_file, json.dumps(run_record, indent=1) + "\n")
    result.artifact(run_file)

    if args.capture_dir:
        cap = nx.resolve_path(args.capture_dir)
        for name in ("stdout.log", "stderr.log"):
            candidate = cap / name
            if candidate.is_file():
                result.artifact(candidate)

    result.data = {
        "dry_run": False,
        "command": final["command"],
        "shell": final["shell"],
        "workdir": final["workdir"],
        "exit_code": final["exit_code"],
        "state": interp["state"],
        "category": interp["category"],
        "retryable": interp["retryable"],
        "meaning": interp.get("meaning"),
        "duration_seconds": final["duration_seconds"],
        "attempts": attempts,
        "timed_out": final["timed_out"],
        "stalled": final["stalled"],
        "cancelled": final["cancelled"],
        "retried": len(attempts) > 1,
        "heartbeats": final["heartbeats"],
        "idle_seconds": final["idle_seconds"],
        "output_truncated": final["output_truncated"],
        "artifacts": artifacts_report,
        "missing_artifacts": missing,
        "risk": {"level": risk["level"], "flags": risk["flags"], "approval_satisfied": bool(args.approve) or not needs_approval},
        "recommended_actions": interp["actions"],
        "stdout_tail": nx.tail_lines(final["stdout"], 40),
        "stderr_tail": nx.tail_lines(final["stderr"], 40),
        "run_record": str(run_file),
        "trace_file": run_record.get("trace_file"),
        "checkpoint": str(args.checkpoint) if args.checkpoint else None,
    }
    result.next_actions.extend(interp["actions"][:3])
    if args.report:
        _write_report(config, run_record, args.report, getattr(args, "title", None), result)
    if not success:
        fail(
            result,
            nx.NexusError(
                "command did not complete successfully",
                code="execution_failed",
                details={
                    "state": interp["state"],
                    "exit_code": final["exit_code"],
                    "recommended_actions": interp["actions"],
                    "run_record": str(run_file),
                },
                exit_code=130 if interp["state"] == "cancelled" else 4,
            ),
        )


def cmd_run_plan(args, config, result):
    path = nx.resolve_path(args.file)
    raw = nx.read_text(path)
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise nx.NexusError("plan is not valid JSON", code="plan_invalid", details={"line": exc.lineno, "message": exc.msg})
    steps = plan.get("steps") or []
    if not steps:
        raise nx.NexusError("plan has no steps", code="plan_empty")
    _validate_approval(args, config)
    policy = config.load_asset("policy_file")
    exit_table = config.load_asset("exit_codes_file")
    executed = []
    status = "success"
    for step in steps:
        command = step.get("command")
        if not command:
            executed.append({"id": step.get("id"), "status": "skipped", "reason": "no command in this step"})
            continue
        risk = nx.assess_risk(command, policy)
        needs = nx.required_approval(risk, config) or bool(step.get("requires_approval"))
        if args.dry_run:
            executed.append(
                {
                    "id": step.get("id"),
                    "status": "validated",
                    "command": nx.redact(command, config),
                    "risk": risk["level"],
                    "approval_required": needs,
                    "would_block_without_approval": bool(needs and not args.approve),
                }
            )
            continue
        if needs and not args.approve:
            executed.append({"id": step.get("id"), "status": "blocked", "reason": "approval required", "risk": risk})
            status = "blocked"
            break
        record = nx.run_command(
            command,
            config,
            shell=step.get("shell") or "auto",
            timeout=step.get("timeout_seconds"),
        )
        interp = nx.interpret(record["exit_code"], record.get("stderr", ""), record.get("signal"), exit_table)
        entry = {
            "id": step.get("id"),
            "status": "ok" if interp["state"] == "success" else "failed",
            "exit_code": record["exit_code"],
            "state": interp["state"],
            "duration_seconds": record["duration_seconds"],
            "stdout_tail": nx.tail_lines(record["stdout"], 10),
            "stderr_tail": nx.tail_lines(record["stderr"], 10),
        }
        if step.get("artifacts"):
            missing = [a for a in step["artifacts"] if not nx.resolve_path(a, base=nx.work_root(config)).exists()]
            entry["missing_artifacts"] = missing
            if missing:
                entry["status"] = "failed"
        executed.append(entry)
        nx.append_trace(config, {"correlation_id": result.correlation_id, "recorded_at": nx.now_iso(), "plan_step": entry, "command": nx.redact(command, config)})
        if entry["status"] != "ok":
            status = "failed"
            if args.stop_on_error:
                break
    if args.dry_run:
        status = "validated"
    result.data = {
        "plan": str(path),
        "objective": plan.get("objective"),
        "dry_run": bool(args.dry_run),
        "status": status,
        "step_count": len(steps),
        "executed": executed,
    }
    if args.report and not args.dry_run:
        _write_report(config, {"correlation_id": result.correlation_id, "command": "run-plan: %s" % path, "state": status, "steps": executed, "artifacts": []}, args.report, getattr(args, "title", None), result)
    if status in ("failed", "blocked"):
        fail(
            result,
            nx.NexusError(
                "plan did not complete",
                code="plan_failed" if status == "failed" else "approval_required",
                details={"executed": executed},
                exit_code=4 if status == "failed" else 3,
            ),
        )


def cmd_orchestrate(args, config, result):
    path = nx.resolve_path(args.workflow)
    raw = nx.read_text(path)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise nx.NexusError("workflow is not valid JSON", code="workflow_invalid", details={"line": exc.lineno, "message": exc.msg})
    errors, warnings = nx.validate_workflow(doc, config)
    for warning in warnings:
        result.warn(warning)
    if errors:
        raise nx.NexusError("workflow is invalid", code="workflow_invalid", details={"errors": errors})
    _validate_approval(args, config)
    if args.max_parallel:
        override = dict(config.data)
        override["orchestration"] = {**override.get("orchestration", {}), "max_parallel": max(1, args.max_parallel)}
        config = nx.Config(config_path=None, overrides=override, scripts_dir=config.scripts_dir)
    if args.dry_run:
        result.data = {
            "dry_run": True,
            "workflow": str(path),
            "node_count": len(doc["nodes"]),
            "nodes": [{"id": n["id"], "depends_on": n.get("depends_on", []), "kind": next(k for k in ("command", "argv", "op", "noop") if k in n)} for n in doc["nodes"]],
            "max_parallel": config.get("orchestration.max_parallel"),
        }
        result.next_actions.append("Remove --dry-run to execute the workflow.")
        return
    cancel = nx._Cancel()

    def handler(signum, frame):
        cancel.set("signal:%d" % signum)
        result.warn("Workflow cancellation requested; running nodes are being stopped.")

    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass
    try:
        outcome = nx.orchestrate(doc, config, cancel=cancel, resume=not args.no_resume, approve=args.approve, approval_ref=args.approval_ref)
    finally:
        for sig, old in previous.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):  # pragma: no cover
                pass
    result.data = {"workflow": str(path), "dry_run": False, **outcome}
    result.artifact(outcome.get("checkpoint"))
    run_record = {
        "correlation_id": result.correlation_id,
        "recorded_at": nx.now_iso(),
        "command": "orchestrate: %s" % path,
        "state": outcome["status"],
        "artifacts": [],
        "attempts": [],
        "duration_seconds": outcome["duration_seconds"],
        "workflow": outcome,
    }
    run_file = nx.run_path(config, "orchestrate-%s.json" % result.correlation_id)
    nx.atomic_write(run_file, json.dumps(run_record, indent=1) + "\n")
    result.artifact(run_file)
    result.data["run_record"] = str(run_file)
    if args.report:
        _write_report(config, run_record, args.report, getattr(args, "title", None), result)
    if outcome["status"] != "success":
        fail(
            result,
            nx.NexusError(
                "workflow finished with status %s" % outcome["status"],
                code="workflow_failed",
                details={"status": outcome["status"], "results": {k: v.get("status") for k, v in outcome["results"].items()}},
                exit_code=130 if outcome["status"] == "cancelled" else 4,
            ),
        )


def cmd_validate_workflow(args, config, result):
    path = nx.resolve_path(args.workflow)
    raw = nx.read_text(path)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise nx.NexusError("workflow is not valid JSON", code="workflow_invalid", details={"line": exc.lineno, "message": exc.msg})
    errors, warnings = nx.validate_workflow(doc, config)
    result.data = {
        "workflow": str(path),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(doc.get("nodes", [])) if isinstance(doc, dict) else 0,
        "max_parallel": config.get("orchestration.max_parallel"),
        "schema": "execution-operations/asset-workflow-schema.json",
    }
    for warning in warnings:
        result.warn(warning)
    if errors:
        raise nx.NexusError("workflow is invalid", code="workflow_invalid", details={"errors": errors})


def cmd_estimate(args, config, result):
    if args.units < 0 or args.unit_seconds < 0:
        raise nx.NexusError("units and unit-seconds must be non-negative", code="invalid_input", details={"units": args.units, "unit_seconds": args.unit_seconds})
    if args.concurrency <= 0:
        raise nx.NexusError("concurrency must be greater than zero", code="invalid_input", details={"concurrency": args.concurrency})
    estimate = nx.estimate_duration(
        args.units,
        args.unit_seconds,
        concurrency=args.concurrency,
        warmup_seconds=args.warmup,
        latency_seconds=args.latency,
        verify_ratio=args.verify_ratio,
        retry_allowance=args.retry_allowance,
    )
    estimate["recommended_timeout_seconds"] = round(estimate["total_seconds"] * 1.5, 3)
    estimate["recommended_max_parallel"] = int(min(config.get("orchestration.max_parallel", 4), max(1, args.concurrency)))
    result.data = estimate


def cmd_parse_log(args, config, result):
    path = nx.resolve_path(args.file)
    text = nx.read_text(path)
    summary = nx.parse_log(text, config, args.max_records)
    if args.dedupe and summary.get("records"):
        summary["deduped"] = nx.dedupe_records(summary["records"])
        summary["deduped_count"] = len(summary["deduped"])
    summary["file"] = str(path)
    summary["size_bytes"] = path.stat().st_size
    result.data = summary
    result.artifact(path)
    if summary["error_count"]:
        result.warn("%d error-level records found; see error_excerpts." % summary["error_count"])
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=1), result)


def cmd_trace(args, config, result):
    if args.show or not (args.note or args.command):
        entries = nx.read_trace(config, limit=args.limit)
        result.data = {"entries": entries, "count": len(entries), "trace_file": str(nx.run_path(config, "trace.jsonl", create=False))}
        if not entries:
            result.warn("No trace entries yet; run 'exec' or record one with --command.")
        return
    record = {
        "correlation_id": result.correlation_id,
        "recorded_at": nx.now_iso(),
        "command": nx.redact(args.command, config) if args.command else None,
        "exit_code": args.exit_code,
        "note": nx.redact(args.note, config) if args.note else None,
        "cwd": os.getcwd(),
        "source": "manual",
    }
    path = nx.append_trace(config, record)
    result.data = {"recorded": record, "trace_file": path}
    result.artifact(path)


def cmd_diagnose(args, config, result):
    stderr_text = ""
    if args.stderr:
        stderr_text = nx.read_text(nx.resolve_path(args.stderr))
    if getattr(args, "stderr_text", None):
        stderr_text = (stderr_text + "\n" + args.stderr_text).strip()
    if args.exit_code is None and not stderr_text and args.signal is None:
        raise nx.NexusError("diagnose needs --exit-code, --signal, --stderr, or --stderr-text", code="missing_input")
    exit_table = config.load_asset("exit_codes_file")
    interpretation = nx.interpret(args.exit_code, stderr_text, args.signal, exit_table)
    layers = []
    for name, description in nx.TRIAGE_LAYERS:
        relevant = interpretation["category"] == name or any(m["category"] == name for m in interpretation["pattern_matches"])
        layers.append(
            {
                "layer": name,
                "description": description,
                "suspected": bool(relevant),
                "probes": nx.PROBES.get(name, []),
            }
        )
    suspected = [l["layer"] for l in layers if l["suspected"]]
    result.data = {
        "interpretation": interpretation,
        "suspected_layers": suspected or ["unknown"],
        "triage": layers,
        "next_probes": [p for l in layers if l["suspected"] for p in l["probes"]][:6] or nx.PROBES["input"],
        "stderr_excerpt": nx.tail_lines(nx.redact(stderr_text, config), 20),
        "secrets_found": nx.scan_secrets(stderr_text) if stderr_text else [],
    }
    if not suspected:
        result.warn("No layer matched with confidence; start with the earliest bad state and a minimal reproducer.")


def cmd_secrets_scan(args, config, result):
    policy = config.load_asset("policy_file")
    targets = []
    for raw in args.paths:
        p = nx.resolve_path(raw)
        if not p.exists():
            raise nx.NexusError("scan target not found", code="path_not_found", details={"path": str(p)})
        if p.is_dir():
            for candidate in sorted(p.rglob("*")):
                if candidate.is_file() and ".git" not in candidate.parts:
                    targets.append(candidate)
        else:
            targets.append(p)
    if len(targets) > 5000:
        raise nx.NexusError("too many files to scan", code="scan_too_large", details={"files": len(targets), "limit": 5000})
    findings = []
    scanned = 0
    skipped = []
    for target in targets:
        try:
            if target.stat().st_size > args.max_bytes:
                skipped.append({"path": str(target), "reason": "larger than --max-bytes"})
                continue
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            skipped.append({"path": str(target), "reason": str(exc)})
            continue
        scanned += 1
        for finding in nx.scan_secrets(text):
            findings.append({"file": str(target), **finding})
    result.data = {
        "files_scanned": scanned,
        "files_skipped": skipped,
        "finding_count": len(findings),
        "findings": findings[:500],
        "clean": not findings,
        "patterns_evaluated": len(policy.get("secret_patterns", [])),
        "note": "Matched values are never printed; only a length and truncated hash are reported.",
    }
    if findings:
        result.warn("Secret-like patterns found; rotate the credential if it was real, then remove it from history and logs.")
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(result.data, indent=1), result)


def cmd_translate(args, config, result):
    out = nx.translate_command(args.command, args.source, args.target, config)
    result.data = out
    for warning in out["warnings"]:
        result.warn(warning)
    if args.out:
        result.data["written_to"] = write_out(args.out, out["command"] + "\n", result)


def cmd_schedule(args, config, result):
    if args.spec:
        spec = json.loads(nx.read_text(nx.resolve_path(args.spec)))
    else:
        if not args.command:
            raise nx.NexusError("schedule needs --spec or --command", code="missing_input")
        spec = {
            "command": args.command,
            "every": args.every,
            "unit": args.unit,
            "minute": args.minute or "0",
            "hour": args.hour or "*",
            "day_of_week": args.day_of_week or "*",
            "timezone": args.timezone,
            "overlap": args.overlap,
            "missed": args.missed,
            "retries": args.retries,
            "name": args.name,
            "timeout_seconds": args.timeout_seconds,
        }
        spec = {k: v for k, v in spec.items() if v is not None}
    built = nx.build_schedule(spec)
    policy = config.load_asset("policy_file")
    risk = nx.assess_risk(built["cron"], policy)
    built["risk"] = {"level": risk["level"], "flags": risk["flags"]}
    result.data = built
    if risk["level"] in ("high", "critical"):
        result.warn("The scheduled command is %s risk; scheduler creation is a deliberate change that needs the same approval as running it once." % risk["level"])
    mapping = {"cron": "linux_cron_line", "systemd": "systemd", "github": "github_actions", "schtasks": "windows_schtasks"}
    if args.out:
        if args.out_format == "all":
            text = "\n\n".join(
                [
                    "## Linux cron\n\n" + built["linux_cron_line"],
                    "## systemd\n\n" + built["systemd"],
                    "## GitHub Actions\n\n" + built["github_actions"],
                    "## Windows schtasks\n\n" + built["windows_schtasks"],
                ]
            )
        else:
            text = built[mapping[args.out_format]]
        result.data["written_to"] = write_out(args.out, text + "\n", result)


def cmd_data_transform(args, config, result):
    path = nx.resolve_path(args.input)
    if not path.is_file():
        raise nx.NexusError("input dataset not found", code="path_not_found", details={"path": str(path)})
    fmt = nx.detect_format(path, args.in_format)
    rows = nx.load_dataset(path, fmt, config)
    if args.spec:
        spec = json.loads(nx.read_text(nx.resolve_path(args.spec)))
    elif args.spec_json:
        spec = json.loads(args.spec_json)
    else:
        raise nx.NexusError("data-transform needs --spec or --spec-json", code="missing_input", details={"steps": ["filter", "map", "rename", "select", "drop_empty", "dedupe", "sort", "aggregate", "limit"]})
    transformed, steps = nx.apply_transform_spec(rows, spec, config)
    text = nx.dump_dataset(transformed, args.output_format)
    result.data = {
        "input": str(path),
        "input_format": fmt,
        "input_rows": len(rows),
        "output_rows": len(transformed),
        "output_format": args.output_format,
        "steps": steps,
        "preview": transformed[:5],
    }
    if args.output:
        result.data["written_to"] = write_out(args.output, text, result)
        if nx.verify_json_lines(text, args.output_format):
            result.step("verify", output_format=args.output_format, reparsed=True)
    else:
        result.data["output_text"] = text if len(text) < 20000 else text[:20000] + "\n... truncated in the envelope; pass --output to write the full result."


def cmd_data_validate(args, config, result):
    path = nx.resolve_path(args.input)
    if not path.is_file():
        raise nx.NexusError("input dataset not found", code="path_not_found", details={"path": str(path)})
    fmt = nx.detect_format(path, args.in_format)
    rows = nx.load_dataset(path, fmt, config)
    spec = json.loads(nx.read_text(nx.resolve_path(args.spec)))
    report = nx.validate_dataset(rows, spec, config, delimited=fmt in ("csv", "tsv"))
    report["input"] = str(path)
    report["input_format"] = fmt
    report["delimited_input"] = fmt in ("csv", "tsv")
    result.data = report
    if not report["valid"]:
        result.warn("Dataset failed %d checks." % report["issue_count"])
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(report, indent=1), result)
    if not report["valid"]:
        fail(result, nx.NexusError("dataset failed validation", code="data_invalid", details={"issues": report["issues"]}))


def cmd_db_query(args, config, result):
    if args.sql_file:
        sql = nx.read_text(nx.resolve_path(args.sql_file))
    elif args.sql:
        sql = args.sql
    else:
        raise nx.NexusError("db-query needs --sql or --sql-file", code="missing_input")
    _validate_approval(args, config)
    params = {}
    for item in args.param:
        if "=" not in item:
            raise nx.NexusError("--param expects name=value", code="invalid_param", details={"received": item})
        key, value = item.split("=", 1)
        params[key.lstrip(":")] = value
    out = nx.db_query(
        args.db,
        sql,
        config,
        params=params,
        allow_write=args.allow_write,
        approve=args.approve,
        approval_ref=args.approval_ref,
        max_rows=args.max_rows,
    )
    if out["read_only"] is False:
        nx.write_audit(
            config,
            {
                "recorded_at": nx.now_iso(),
                "decision": "db_write",
                "approval_ref": args.approval_ref,
                "statement": out["statement"],
                "database": out["database"],
                "correlation_id": result.correlation_id,
            },
        )
    result.data = out
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps({"columns": out["columns"], "rows": out["rows"]}, indent=1), result)


def cmd_http(args, config, result):
    _validate_approval(args, config)
    headers = {}
    for item in args.header:
        if "=" not in item:
            raise nx.NexusError("--header expects KEY=VALUE", code="invalid_header", details={"received": item})
        key, value = item.split("=", 1)
        headers[key.strip()] = value
    out = nx.http_request(
        args.url,
        config,
        method=args.method,
        headers=headers,
        body=args.body,
        allow_host=args.allow_host,
        approve=args.approve,
        approval_ref=args.approval_ref,
    )
    result.data = out
    if args.out:
        result.data["written_to"] = write_out(args.out, out.get("body") or "", result)


def cmd_verify(args, config, result):
    records = []
    failures = []
    for raw in args.artifacts:
        p = nx.resolve_path(raw, base=nx.work_root(config))
        entry = {"path": str(p), "exists": p.exists()}
        if not p.exists():
            entry["status"] = "missing"
            failures.append({"path": str(p), "problem": "missing"})
        elif p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file()]
            entry.update({"type": "directory", "file_count": len(files), "size_bytes": sum(f.stat().st_size for f in files)})
            entry["status"] = "ok" if files else "empty"
            if not files:
                failures.append({"path": str(p), "problem": "empty_directory"})
        else:
            size = p.stat().st_size
            entry.update({"type": "file", "size_bytes": size, "sha256": nx.sha256_file(p)})
            if size < args.min_bytes:
                entry["status"] = "too_small"
                failures.append({"path": str(p), "problem": "smaller than --min-bytes", "size_bytes": size, "min_bytes": args.min_bytes})
            else:
                entry["status"] = "ok"
            if args.require_json:
                try:
                    json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    entry["json_valid"] = True
                except json.JSONDecodeError as exc:
                    entry["json_valid"] = False
                    entry["json_error"] = {"line": exc.lineno, "message": exc.msg}
                    entry["status"] = "invalid_json"
                    failures.append({"path": str(p), "problem": "invalid_json", "line": exc.lineno})
        records.append(entry)
    result.data = {
        "artifact_count": len(records),
        "verified": records,
        "failure_count": len(failures),
        "failures": failures,
        "all_ok": not failures,
        "note": "A structural check is not a semantic review; open each artifact with its real consumer.",
    }
    if args.out:
        result.data["written_to"] = write_out(args.out, json.dumps(result.data, indent=1), result)
    if failures:
        fail(result, nx.NexusError("artifact verification failed", code="verification_failed", details={"failures": failures}, exit_code=4))


def _write_report(config, run_record, output, title, result):
    template = config.asset_path(config.data.get("report_template_file", "asset-report-template.md")).read_text(encoding="utf-8")
    interp = run_record.get("interpretation") or {}
    artifacts = run_record.get("artifacts") or []
    context = {
        "title": title or ("Run %s" % (run_record.get("correlation_id") or "report")),
        "correlation_id": run_record.get("correlation_id"),
        "generated_at": nx.now_iso(),
        "tool": nx.TOOL_NAME,
        "version": nx.VERSION,
        "category": run_record.get("category") or "-",
        "status": run_record.get("state") or run_record.get("status") or "unknown",
        "command": run_record.get("command") or "-",
        "shell": run_record.get("shell") or "-",
        "workdir": run_record.get("workdir") or "-",
        "exit_code": run_record.get("exit_code"),
        "state": run_record.get("state"),
        "duration_seconds": run_record.get("duration_seconds"),
        "attempts": len(run_record.get("attempts") or []) or 1,
        "timed_out": run_record.get("timed_out"),
        "stalled": run_record.get("stalled"),
        "retried": (len(run_record.get("attempts") or []) > 1),
        "checkpoint": run_record.get("checkpoint") or "none",
        "risk_level": (run_record.get("risk") or {}).get("level", "-"),
        "approval_required": (run_record.get("risk") or {}).get("level") in config.get("approval.required_levels", []),
        "approval_recorded": (run_record.get("risk") or {}).get("approval_satisfied"),
        "risk_flags": ", ".join((run_record.get("risk") or {}).get("flags", [])) or "none",
        "artifacts_table": nx.markdown_table(
            ["Path", "Exists", "Bytes", "SHA-256"],
            [[a.get("path"), a.get("exists"), a.get("size_bytes"), (a.get("sha256") or "-")[:16]] for a in artifacts],
        ),
        "diagnosis_table": nx.markdown_table(
            ["State", "Category", "Retryable", "Meaning"],
            [[interp.get("state"), interp.get("category"), interp.get("retryable"), (interp.get("meaning") or "-")[:120]]],
        )
        if interp
        else "_No interpretation recorded._",
        "verification_table": nx.markdown_table(
            ["Check", "Result"],
            [[a.get("path"), "ok" if a.get("exists") else "missing"] for a in artifacts] or [["-", "-"]],
        ),
        "output_excerpt": nx.tail_lines((run_record.get("stdout_tail") or "") + ("\n" + run_record["stderr_tail"] if run_record.get("stderr_tail") else ""), 40) or "_No captured output._",
        "next_actions": "\n".join("- " + a for a in (interp.get("actions") or ["Verify artifacts and hand off the run record."])),
    }
    text = nx.render_report(template, context)
    path = nx.resolve_path(output)
    nx.atomic_write(path, text)
    result.artifact(path)
    result.data["report"] = str(path)
    return str(path)


def cmd_report(args, config, result):
    if args.run:
        record = json.loads(nx.read_text(nx.resolve_path(args.run)))
    elif args.from_trace:
        entries = nx.read_trace(config, limit=1)
        if not entries:
            raise nx.NexusError("trace has no entries to report on", code="trace_empty")
        record = entries[-1]
    else:
        raise nx.NexusError("report needs --run or --from-trace", code="missing_input")
    path = _write_report(config, record, args.output, args.title, result)
    result.data["output"] = path
    result.data["status"] = record.get("state") or record.get("status")


def cmd_doctor(args, config, result):
    report = nx.doctor(config, operations=[name for name, _summary in OPERATIONS])
    result.data = report
    for check in report["checks"]:
        if check["status"] == "fail":
            result.warn("%s: %s" % (check["check"], check["detail"]))
    if report["status"] == "fail":
        fail(result, nx.NexusError("environment doctor found blocking problems", code="doctor_failed", details={"checks": [c for c in report["checks"] if c["status"] == "fail"]}))


def cmd_checkpoint(args, config, result):
    path = nx.resolve_path(args.file)
    if args.reset:
        if path.is_file():
            path.unlink()
            result.data = {"reset": True, "path": str(path)}
        else:
            result.data = {"reset": False, "path": str(path), "reason": "file did not exist"}
        return
    if args.set or args.complete:
        stored = nx.checkpoint_load(path) or {}
        if stored.get("corrupt"):
            result.warn("Existing checkpoint was corrupt and has been replaced.")
            stored = {}
        values = dict(stored.get("values") or {})
        for item in args.set:
            if "=" not in item:
                raise nx.NexusError("--set expects key=value", code="invalid_input", details={"received": item})
            key, value = item.split("=", 1)
            values[key] = value
        completed = list(stored.get("completed") or [])
        for unit in args.complete:
            if unit not in completed:
                completed.append(unit)
        nx.checkpoint_write(path, {"values": values, "completed": completed})
        result.data = {"path": str(path), "values": values, "completed": completed}
        result.artifact(path)
        return
    stored = nx.checkpoint_load(path)
    if stored is None:
        result.data = {"path": str(path), "exists": False, "completed": [], "values": {}}
        result.warn("Checkpoint does not exist yet.")
        return
    result.data = {"path": str(path), "exists": True, "contents": stored, "completed": stored.get("completed") or [], "values": stored.get("values") or {}}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "operation", None):
        parser.print_help()
        return 0
    overrides = {}
    work_root_arg = getattr(args, "work_root", None)
    state_dir_arg = getattr(args, "state_dir", None)
    if work_root_arg:
        overrides.setdefault("paths", {})["work_root"] = work_root_arg
    if state_dir_arg:
        overrides.setdefault("paths", {})["state_dir"] = state_dir_arg
    if not hasattr(args, "text"):
        args.text = False
    result = Result(args.operation)
    try:
        config = nx.Config(config_path=getattr(args, "config", None), overrides=overrides or None)
        args.func(args, config, result)
        payload = result.envelope(ok=True)
        output(payload, args, text_renderer=_text_renderer(args.operation, args))
        return 0
    except nx.NexusError as exc:
        payload = emit_error(args.operation, exc, result.correlation_id)
        partial = getattr(exc, "partial", None)
        if partial is not None:
            payload["data"] = partial.data
            payload["warnings"] = partial.warnings
            payload["artifacts"] = partial.artifacts
            payload["steps"] = partial.steps
            payload["next_actions"] = list(dict.fromkeys(payload.get("next_actions", []) + partial.next_actions))
        output(payload, args, text_renderer=None)
        return exc.exit_code
    except KeyboardInterrupt:
        payload = emit_error(args.operation, nx.NexusError("interrupted by the operator", code="cancelled", exit_code=130), result.correlation_id)
        output(payload, args)
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort structured error
        import traceback

        payload = emit_error(args.operation, exc, result.correlation_id)
        payload["error"]["traceback"] = traceback.format_exc().splitlines()[-6:]
        payload["next_actions"] = ["This is an internal error. Re-run with --text for a short summary and report the traceback."]
        output(payload, args)
        return 5


def _text_renderer(command, args=None):
    def renderer(payload):
        data = payload.get("data") or {}
        lines = ["OK [%s] correlation=%s" % (payload.get("operation"), payload.get("correlation_id"))]
        if command == "schedule":
            chosen = getattr(args, "out_format", "all") if args is not None else "all"
            fields = {
                "all": ["linux_cron_line", "systemd", "github_actions", "windows_schtasks"],
                "cron": ["linux_cron_line"],
                "systemd": ["systemd"],
                "github": ["github_actions"],
                "schtasks": ["windows_schtasks"],
            }.get(chosen, ["linux_cron_line"])
            for field in fields:
                lines.append(data.get(field, ""))
                lines.append("")
        elif command == "translate":
            lines.append(data.get("command", ""))
        elif command == "capabilities":
            lines.append("operations: %d | catalog: %s categories, %s skills"
                         % (data.get("operation_count", 0), (data.get("catalog") or {}).get("total_categories"), (data.get("catalog") or {}).get("total_skills")))
            for op in data.get("operations", []):
                lines.append("  %-18s %s" % (op["name"], op["summary"]))
        elif command in ("exec", "run-plan", "orchestrate"):
            lines.append("state: %s | exit: %s | duration: %ss" % (data.get("state") or data.get("status"), data.get("exit_code"), data.get("duration_seconds")))
            if data.get("stderr_tail"):
                lines.append("stderr tail:")
                lines.append(data["stderr_tail"])
        else:
            for key in ("valid", "status", "state", "risk_level", "output", "written_to", "total_seconds", "clean", "all_ok"):
                if key in data:
                    lines.append("%s: %s" % (key, data[key]))
        for warning in payload.get("warnings", []):
            lines.append("warning: %s" % warning)
        return "\n".join(lines)

    return renderer


if __name__ == "__main__":
    sys.exit(main())
