#!/usr/bin/env python3
"""Offline Engineering Workbench - primary entry point.

A reusable, offline-only Skill Tool that turns the 32-category, 480-skill
"Full Offline Code, Data, and Scientific Work" catalog into an executable
workflow: capability discovery, deterministic task planning, project checks,
provenance manifests, bounded command execution, clean-room validation, and
Markdown reporting.

Quick start (from the future skill root):
    python3 offline-engineering-workbench/tools-workbench.py capabilities --summary
    python3 offline-engineering-workbench/tools-workbench.py plan --task "train a reproducible churn model"
    python3 offline-engineering-workbench/tools-workbench.py check --root . --format json
    python3 offline-engineering-workbench/tools-workbench.py selftest

No network access, no credentials, no shell execution, no writes to the audited
tree. Destructive or privileged commands require explicit approval.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent


def _load(stem: str, module_name: Optional[str] = None):
    """Load a sibling tools-* module by path (hyphens are not importable)."""
    path = HERE / ("tools-%s.py" % stem)
    if not path.is_file():
        raise RuntimeError("Required tool file not found: %s" % path)
    name = module_name or ("wb_core" if stem == "workbench-core" else "wb_%s" % stem.replace("-", "_"))
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "VERSION", None):
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("workbench-core")
checks = _load("workbench-checks")
runner = _load("workbench-runner")
planner = _load("workbench-planner")


# --------------------------------------------------------------------------- #
# Argument parsing helpers
# --------------------------------------------------------------------------- #

def add_common(parser: argparse.ArgumentParser, with_output: bool = True) -> None:
    parser.add_argument("--format", choices=("table", "json", "markdown"),
                        default=argparse.SUPPRESS, help="Output format (default: table).")
    if with_output:
        parser.add_argument("--output", default=argparse.SUPPRESS,
                            help="Write output to this file instead of stdout.")
    parser.add_argument("--config", default=argparse.SUPPRESS,
                        help="Policy file path (default: asset-check-policy.json).")
    parser.add_argument("--catalog", default=argparse.SUPPRESS,
                        help="Catalog asset path (default: asset-skill-catalog.json).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-workbench.py",
        description="Offline Engineering Workbench: plan, check, run, verify, and report offline "
                    "code, data, and scientific work using the 480-skill catalog.",
        epilog="Every command is local-only. Use --format json for machine-readable output and "
               "--output FILE to write results. Exit codes: 0 ok, 2 usage, 3 approval required, "
               "4 quality gate failed, 5 runtime error, 6 verification failed, 10 self-test failed.",
    )
    parser.add_argument("--version", action="version",
                        version="%(prog)s " + core.VERSION)
    parser.add_argument("--format", choices=("table", "json", "markdown"), default="table",
                        help="Output format (default: table).")
    parser.add_argument("--output", default=None, help="Write output to this file instead of stdout.")
    parser.add_argument("--config", default=None, help="Policy file path.")
    parser.add_argument("--catalog", default=None, help="Catalog asset path.")
    parser.add_argument("--verbose", action="store_true", help="Print progress notes to stderr.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    capabilities = sub.add_parser("capabilities", help="List the registered catalog capabilities.")
    add_common(capabilities)
    capabilities.add_argument("--category", help="Restrict to one category (number, id, or slug).")
    capabilities.add_argument("--search", help="Case-insensitive search over names and descriptions.")
    capabilities.add_argument("--summary", action="store_true", help="Show category counts only.")
    capabilities.add_argument("--limit", type=int, default=0, help="Maximum rows (0 = all).")

    category = sub.add_parser("category", help="Show one category and its 15 skills.")
    add_common(category)
    category.add_argument("query", help="Category number, id, slug, or name.")

    skill = sub.add_parser("skill", help="Show one skill and its category context.")
    add_common(skill)
    skill.add_argument("query", help="Skill id, slug, name, or unique description fragment.")

    plan = sub.add_parser("plan", help="Build a deterministic phased plan from a task description.")
    add_common(plan)
    plan.add_argument("--task", help="Task description in plain text or Markdown.")
    plan.add_argument("--task-file", help="Read the task description from a file.")
    plan.add_argument("--max-skills", type=int, default=12, help="Maximum implementation skills (1-40).")
    plan.add_argument("--max-per-category", type=int, default=2, help="Maximum skills per category (1-5).")
    plan.add_argument("--root", default=".", help="Project root the plan applies to.")

    check = sub.add_parser("check", help="Run offline engineering checks against a project tree.")
    add_common(check)
    check.add_argument("--root", default=".", help="Project root to audit (default: current directory).")
    check.add_argument("--only", action="append", default=[], help="Run only these rule ids or categories.")
    check.add_argument("--exclude", action="append", default=[], help="Skip these rule ids or categories.")
    check.add_argument("--fail-on", action="append", default=[],
                       help="Override the fail-on severities for this run.")

    manifest = sub.add_parser("manifest", help="Write a SHA-256 provenance manifest for a project.")
    add_common(manifest, with_output=False)
    manifest.add_argument("--root", default=".", help="Project root to fingerprint.")
    manifest.add_argument("--output", default="reports/manifest.json", help="Manifest output path.")
    manifest.add_argument("--exclude", action="append", default=[], help="Extra exclude glob.")
    manifest.add_argument("--deterministic", action="store_true",
                          help="Omit the creation timestamp for byte-identical output.")

    verify = sub.add_parser("verify", help="Verify a manifest against the project on disk.")
    add_common(verify)
    verify.add_argument("--manifest", required=True, help="Manifest JSON to verify.")
    verify.add_argument("--root", help="Project root (default: the manifest root).")

    run = sub.add_parser("run", help="Run a bounded command with timeout, retries, and approval gate.")
    add_common(run)
    run.add_argument("--cwd", default=".", help="Working directory for the command.")
    run.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (1-3600).")
    run.add_argument("--retries", type=int, default=0, help="Retries for normal-risk commands (0-5).")
    run.add_argument("--max-output-bytes", type=int, default=200000, help="Captured output cap per stream.")
    run.add_argument("--approve", action="store_true", help="Acknowledge a risky command.")
    run.add_argument("--dry-run", action="store_true", help="Resolve and classify without executing.")
    run.add_argument("--log", help="Append JSONL audit records to this file.")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="Command argv after --.")

    cleanroom = sub.add_parser("cleanroom", help="Copy the project to a temp dir, check it, and clean up.")
    add_common(cleanroom)
    cleanroom.add_argument("--root", default=".", help="Project root to copy.")
    cleanroom.add_argument("--workdir", help="Parent directory for the temporary clean room.")
    cleanroom.add_argument("argv", nargs=argparse.REMAINDER, help="Optional command argv after --.")
    cleanroom.add_argument("--timeout", type=int, default=300, help="Command timeout in seconds.")
    cleanroom.add_argument("--keep", action="store_true", help="Keep the temporary directory for inspection.")

    report = sub.add_parser("report", help="Render a Markdown report from a JSON result file.")
    add_common(report, with_output=False)
    report.add_argument("--result", required=True, help="Result JSON produced by check or plan.")
    report.add_argument("--template", help="Report template (default: asset-report-template.md).")
    report.add_argument("--output", default="reports/report.md", help="Markdown output path.")

    doctor = sub.add_parser("doctor", help="Report runtime, policy, catalog, and tool availability.")
    add_common(doctor)

    config = sub.add_parser("config", help="Show the effective, validated policy configuration.")
    add_common(config)

    rules = sub.add_parser("rules", help="List every check rule with severity and catalog mapping.")
    add_common(rules)

    selftest = sub.add_parser("selftest", help="Run the built-in end-to-end self-test suite.")
    add_common(selftest)

    help_cmd = sub.add_parser("help", help="Show this help text.")
    help_cmd.add_argument("topic", nargs="?", help="Optional command name.")

    return parser


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _policy(args: argparse.Namespace) -> Dict[str, Any]:
    return core.load_policy(getattr(args, "config", None))


def _catalog(args: argparse.Namespace) -> Dict[str, Any]:
    return core.load_catalog(getattr(args, "catalog", None))


def _fmt(args: argparse.Namespace) -> str:
    return getattr(args, "format", None) or "table"


def _out(args: argparse.Namespace) -> Optional[str]:
    return getattr(args, "output", None)


def _write_lines(lines: Sequence[str], args: argparse.Namespace, title: str) -> None:
    fmt = _fmt(args)
    text = "\n".join(lines)
    if fmt == "json":
        core.write_output(core.json.dumps({"title": title, "lines": lines}, indent=2), _out(args))
    elif fmt == "markdown":
        core.write_output("# %s\n\n```\n%s\n```\n" % (title, text), _out(args))
    else:
        core.write_output(text, _out(args))


def cmd_capabilities(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    if args.summary:
        rows = [{
            "id": category["id"], "number": category["number"], "name": category["name"],
            "skills": category["skill_count"], "scope": category["scope"],
        } for category in catalog["categories"]]
        if _fmt(args) == "json":
            core.emit({"tool": core.TOOL_NAME, "totals": catalog["totals"], "categories": rows},
                      "json", _out(args))
        else:
            core.emit(rows, _fmt(args), _out(args), columns=["id", "number", "name", "skills"])
        return core.EXIT_OK

    rows: List[Dict[str, Any]] = []
    for category in catalog["categories"]:
        if args.category and str(category["number"]) != str(args.category) and \
                category["id"] != args.category and category["slug"] != args.category:
            continue
        for skill in category["skills"]:
            if args.search:
                needle = args.search.lower()
                haystack = (skill["name"] + " " + skill["description"] + " " +
                            category["name"] + " " + category["scope"]).lower()
                if needle not in haystack:
                    continue
            rows.append({
                "id": skill["id"], "category": category["name"],
                "name": skill["name"], "scope": skill["scope"],
            })
    if args.limit and args.limit > 0:
        rows = rows[:args.limit]
    if _fmt(args) == "json":
        core.emit({"tool": core.TOOL_NAME, "totals": catalog["totals"],
                   "returned": len(rows), "skills": rows}, "json", _out(args))
    else:
        core.emit(rows, _fmt(args), _out(args), columns=["id", "category", "name"])
    return core.EXIT_OK


def cmd_category(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    category = core.find_category(catalog, args.query)
    if _fmt(args) == "json":
        core.emit(category, "json", _out(args))
        return core.EXIT_OK
    lines = [
        "Category %d: %s" % (category["number"], category["name"]),
        "Scope: %s" % category["scope"],
        "Skills: %d" % category["skill_count"],
        "",
        core.render_table(category["skills"], ["id", "name"], max_width=70),
    ]
    _write_lines(lines, args, category["name"])
    return core.EXIT_OK


def cmd_skill(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    category, skill = core.find_skill(catalog, args.query)
    if _fmt(args) == "json":
        core.emit({"category": category, "skill": skill}, "json", _out(args))
        return core.EXIT_OK
    lines = [
        "Skill: %s" % skill["name"],
        "Id: %s" % skill["id"],
        "Category: %s" % category["name"],
        "Scope: %s" % skill["scope"],
        "",
        "Description:",
        skill["description"],
        "",
        "Related skills in this category:",
        core.render_table([s for s in category["skills"] if s["id"] != skill["id"]],
                          ["id", "name"], max_width=70),
    ]
    _write_lines(lines, args, skill["name"])
    return core.EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    if args.task_file:
        path = Path(args.task_file).expanduser()
        if not path.is_file():
            raise core.WorkbenchError("task-file-missing", "Task file not found: %s" % path,
                                      {"path": str(path)}, core.EXIT_USAGE)
        task = path.read_text(encoding="utf-8")
    else:
        task = args.task or ""
    catalog = _catalog(args)
    policy = _policy(args)
    plan = planner.build_plan(task, catalog, policy, args.max_skills, args.max_per_category,
                              args.root)
    if _fmt(args) == "json":
        core.emit(plan, "json", _out(args))
        return core.EXIT_OK
    lines = [
        "Plan %s (confidence: %s)" % (plan["plan_id"], plan["confidence"]),
        "Task: %s" % plan["task"],
        "Selected %d of %d registered skills across %d categories."
        % (plan["totals"]["selected_skills"], plan["totals"]["registered_skills"],
           plan["totals"]["categories_covered"]),
        "",
    ]
    for phase in plan["phases"]:
        lines.append("%s - %s" % (phase["id"].upper(), phase["name"]))
        lines.append("  gate: %s" % phase["gate"])
        if phase["skills"]:
            lines.append(core.render_table(phase["skills"], ["id", "name", "score"], max_width=60))
        else:
            lines.append("  (no additional skills selected)")
        lines.append("")
    lines.append("Approval-required command classes: %s" % ", ".join(plan["approval_required"]))
    _write_lines(lines, args, "Plan %s" % plan["plan_id"])
    return core.EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    root = core.require_directory(args.root, "root")
    policy = _policy(args)
    if args.fail_on:
        invalid = [item for item in args.fail_on if item not in core.SEVERITIES]
        if invalid:
            raise core.WorkbenchError("fail-on-invalid", "Unknown severity: %s" % ", ".join(invalid),
                                      {"allowed": list(core.SEVERITIES)}, core.EXIT_USAGE)
        policy["quality_gate"]["fail_on"] = list(args.fail_on)
    catalog = _catalog(args)
    result = checks.run_checks(root, policy, args.only, args.exclude, catalog)
    fmt = _fmt(args)
    if fmt == "json":
        core.emit(result, "json", _out(args))
    else:
        summary = result["summary"]
        lines = [
            "Offline engineering check: %s" % result["status"].upper(),
            "Root: %s" % result["root"],
            "Files: %d total, %d text, %d skipped | rules run: %d | findings: %d"
            % (summary["files_total"], summary["files_text"], summary["files_binary_or_skipped"],
               summary["rules_run"], summary["findings"]),
            "Severity: %s" % ", ".join("%s=%d" % (k, v) for k, v in summary["by_severity"].items()),
            "Gate: %s (fail-on: %s)" % (result["gate"], ", ".join(result["policy"]["fail_on"])),
            "",
        ]
        if result["findings"]:
            lines.append(checks.format_findings(result))
        else:
            lines.append("No findings.")
        _write_lines(lines, args, "Check report")
    return core.EXIT_GATE if result["status"] == "fail" else core.EXIT_OK


def cmd_manifest(args: argparse.Namespace) -> int:
    root = core.require_directory(args.root, "root")
    policy = _policy(args)
    output = Path(args.output).expanduser()
    extra = tuple(args.exclude or [])
    if extra:
        policy["exclude_globs"] = list(policy.get("exclude_globs", [])) + list(extra)
    files: List[Dict[str, Any]] = []
    total_bytes = 0
    output_resolved = output.resolve() if output.is_absolute() else (Path.cwd() / output).resolve()
    for entry in core.iter_project_files(root, policy):
        if output_resolved == entry["path"] or entry["path"] == output_resolved:
            continue
        if entry["too_large"]:
            continue
        files.append({
            "path": entry["rel"],
            "size": entry["size"],
            "sha256": core.sha256_file(entry["path"]),
        })
        total_bytes += entry["size"]
    manifest = {
        "schema_version": "1.0",
        "tool": core.TOOL_NAME,
        "tool_version": core.VERSION,
        "root": str(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "environment": core.build_environment(),
        "deterministic": bool(args.deterministic),
    }
    if not args.deterministic:
        manifest["created_at"] = core.now_iso()
    if _fmt(args) == "json" and not _out(args):
        core.emit(manifest, "json", None)
    else:
        output.parent.mkdir(parents=True, exist_ok=True) if output.parent else None
        output.write_text(core.json.dumps(manifest, indent=2), encoding="utf-8")
        print("wrote %s (%d files, %s)" % (output, len(files), core.format_bytes(total_bytes)))
    return core.EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser()
    manifest = core.load_json(manifest_path, "manifest")
    schema = core.load_json(core.DEFAULT_MANIFEST_SCHEMA, "manifest schema")
    schema_errors = core.validate_manifest(manifest, schema)
    if schema_errors:
        result = {"status": "invalid", "manifest": str(manifest_path), "schema_errors": schema_errors,
                  "missing": [], "modified": [], "added": [], "verified": 0}
        core.emit(result, _fmt(args), _out(args))
        return core.EXIT_VERIFY
    root = core.require_directory(args.root or manifest.get("root", "."), "root")
    expected = {entry["path"]: entry for entry in manifest.get("files", [])}
    actual: Dict[str, Dict[str, Any]] = {}
    policy = _policy(args)
    for entry in core.iter_project_files(root, policy):
        actual[entry["rel"]] = entry
    missing = sorted(path for path in expected if path not in actual)
    added = sorted(path for path in actual if path not in expected)
    modified: List[Dict[str, Any]] = []
    verified = 0
    for path, entry in sorted(expected.items()):
        if path not in actual:
            continue
        target = core.safe_child(root, path)
        if not target.is_file():
            missing.append(path)
            continue
        digest = core.sha256_file(target)
        if digest != entry["sha256"] or target.stat().st_size != entry["size"]:
            modified.append({"path": path, "expected": entry["sha256"], "actual": digest})
        else:
            verified += 1
    result = {
        "status": "ok" if not (missing or modified or added) else "mismatch",
        "manifest": str(manifest_path),
        "root": str(root),
        "verified": verified,
        "expected_files": len(expected),
        "missing": missing,
        "modified": modified,
        "added": added,
        "environment": manifest.get("environment", {}),
        "generated_at": core.now_iso(),
    }
    core.emit(result, _fmt(args), _out(args))
    return core.EXIT_OK if result["status"] == "ok" else core.EXIT_VERIFY


def cmd_run(args: argparse.Namespace) -> int:
    argv = list(args.argv or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    policy = _policy(args)
    result = runner.run_command(
        argv, args.cwd, timeout=args.timeout, retries=args.retries,
        max_output_bytes=args.max_output_bytes, approve=args.approve,
        dry_run=args.dry_run, log_path=args.log, policy=policy,
    )
    fmt = _fmt(args)
    if fmt == "json":
        core.emit(result, "json", _out(args))
    else:
        lines = [
            "Command: %s" % result["command_display"],
            "Status: %s | risk: %s | exit: %s | duration: %d ms"
            % (result["status"], result["risk"], result["exit_code"], result["duration_ms"]),
        ]
        if result.get("reasons"):
            for reason in result["reasons"]:
                lines.append("  approval reason: %s (%s)" % (reason["id"], reason["reason"]))
        if result.get("stdout"):
            lines.append("stdout:")
            lines.extend("  " + line for line in result["stdout"].splitlines()[:40])
        if result.get("stderr"):
            lines.append("stderr:")
            lines.extend("  " + line for line in result["stderr"].splitlines()[:40])
        if result.get("output_truncated"):
            lines.append("warning: output was truncated at the configured cap.")
        _write_lines(lines, args, "Command result")
    if result["status"] == "blocked":
        return core.EXIT_BLOCKED
    if result["status"] == "failed":
        return core.EXIT_RUNTIME
    return core.EXIT_OK


def cmd_cleanroom(args: argparse.Namespace) -> int:
    root = core.require_directory(args.root, "root")
    policy = _policy(args)
    workdir = Path(args.workdir).expanduser() if args.workdir else Path(tempfile.gettempdir())
    if not workdir.is_dir():
        raise core.WorkbenchError("workdir-missing", "Clean-room parent directory not found: %s" % workdir,
                                  {"path": str(workdir)}, core.EXIT_USAGE)
    room = Path(tempfile.mkdtemp(prefix="workbench-cleanroom-", dir=str(workdir)))
    copied = 0
    skipped = 0
    try:
        ignore_dirs = set(policy.get("exclude_dirs", []))
        ignore_globs = tuple(policy.get("exclude_globs", []))
        max_bytes = int(policy.get("thresholds", {}).get("max_file_bytes", 52428800))

        def ignore(directory: str, names: Sequence[str]) -> set:
            ignored = set()
            for name in names:
                full = os.path.join(directory, name)
                rel = os.path.relpath(full, str(root)).replace(os.sep, "/")
                if name in ignore_dirs or name == ".git" or os.path.islink(full):
                    ignored.add(name)
                elif any(core.fnmatch.fnmatch(rel, pattern) or core.fnmatch.fnmatch(name, pattern)
                         for pattern in ignore_globs):
                    ignored.add(name)
            return ignored

        shutil.copytree(root, room, symlinks=True, ignore=ignore, dirs_exist_ok=True)
        for dirpath, dirnames, filenames in os.walk(room):
            for name in filenames:
                target = Path(dirpath) / name
                try:
                    if target.stat().st_size > max_bytes:
                        target.unlink()
                        skipped += 1
                    else:
                        copied += 1
                except OSError:
                    skipped += 1
        check_result = checks.run_checks(room, policy, catalog=_catalog(args))
        command_result = None
        if args.argv:
            argv = list(args.argv)
            if argv and argv[0] == "--":
                argv = argv[1:]
            if argv:
                command_result = runner.run_command(
                    argv, room, timeout=args.timeout, retries=0,
                    max_output_bytes=int(policy.get("thresholds", {}).get(
                        "run_max_output_bytes", 200000)),
                    approve=False, dry_run=False, log_path=None, policy=policy)
        result = {
            "status": "ok" if check_result["status"] == "pass" else "gate-failed",
            "root": str(root),
            "cleanroom": str(room),
            "copied_files": copied,
            "skipped_files": skipped,
            "kept": bool(args.keep),
            "check": {
                "status": check_result["status"],
                "findings": check_result["summary"]["findings"],
                "by_severity": check_result["summary"]["by_severity"],
                "rules_run": check_result["summary"]["rules_run"],
            },
            "command": command_result,
            "generated_at": core.now_iso(),
        }
        if _fmt(args) == "json":
            core.emit(result, "json", _out(args))
        else:
            lines = [
                "Clean-room status: %s" % result["status"],
                "Source: %s" % root,
                "Room: %s" % room,
                "Copied %d files (%d skipped)." % (copied, skipped),
                "Check findings: %d %s" % (check_result["summary"]["findings"],
                                           check_result["summary"]["by_severity"]),
            ]
            if command_result:
                lines.append("Command: %s -> %s (exit %s)"
                             % (command_result["command_display"], command_result["status"],
                                command_result["exit_code"]))
            _write_lines(lines, args, "Clean-room result")
        return core.EXIT_GATE if result["status"] == "gate-failed" else core.EXIT_OK
    finally:
        if args.keep:
            print("clean room kept at %s" % room, file=sys.stderr)
        else:
            shutil.rmtree(room, ignore_errors=True)


def cmd_report(args: argparse.Namespace) -> int:
    result = core.load_json(args.result, "result")
    template_path = Path(args.template).expanduser() if args.template else core.DEFAULT_TEMPLATE
    if not template_path.is_file():
        raise core.WorkbenchError("template-missing", "Report template not found: %s" % template_path,
                                  {"path": str(template_path)}, core.EXIT_USAGE)
    template = template_path.read_text(encoding="utf-8")
    findings = result.get("findings", [])
    summary = result.get("summary", {})
    if result.get("phases"):
        capabilities = "\n".join(
            "- **%s** - %s" % (phase["name"], ", ".join(item["name"] for item in phase["skills"]))
            for phase in result["phases"])
        status = result.get("confidence", "planned")
        root = result.get("root", ".")
        generated = result.get("generated_at", core.now_iso())
        summary_text = ("Task: %s. Selected %d of %d registered skills across %d categories."
                        % (result.get("task", ""), result.get("totals", {}).get("selected_skills", 0),
                           result.get("totals", {}).get("registered_skills", 0),
                           result.get("totals", {}).get("categories_covered", 0)))
        limitations = "Plan only; run check, run, manifest, and verify to produce evidence."
    else:
        capabilities = "\n".join(
            "- %s" % item["name"] for item in result.get("catalog_categories_applied", [])) or "- None"
        status = result.get("status", "unknown")
        root = result.get("root", ".")
        generated = result.get("generated_at", core.now_iso())
        summary_text = ("%d files scanned, %d rules run, %d findings (%d failing). Gate: %s."
                        % (summary.get("files_total", 0), summary.get("rules_run", 0),
                           summary.get("findings", 0), summary.get("failing_findings", 0),
                           result.get("gate", "unknown")))
        limitations = ("Findings are deterministic pattern and structure checks, not a substitute "
                       "for the language-specific analyzers listed in catalog categories 25-30. "
                       "Blocked integrations are reported by doctor.")
    context = {
        "title": "Offline Engineering Report",
        "status": status,
        "generated": generated,
        "tool_name": core.TOOL_NAME,
        "tool_version": core.VERSION,
        "root": root,
        "command": core.redact(" ".join(sys.argv), 300),
        "summary": summary_text,
        "severity_table": core.severity_table(findings),
        "findings_table": core.findings_table(findings),
        "capabilities": capabilities,
        "limitations": limitations,
    }
    rendered = core.render_template(template, context)
    core.write_output(rendered, args.output)
    return core.EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    policy = _policy(args)
    catalog = _catalog(args)
    rows: List[Dict[str, Any]] = []
    rows.append({"check": "python", "required": ">=%s" % policy["environment"].get("python_min", "3.9"),
                 "found": core.platform.python_version(),
                 "status": "ok" if core.require_python_ok() else "missing"})
    for module in ("json", "hashlib", "subprocess", "tempfile", "importlib.util"):
        try:
            __import__(module)
            rows.append({"check": "module " + module, "required": "stdlib", "found": "ok", "status": "ok"})
        except ImportError:
            rows.append({"check": "module " + module, "required": "stdlib", "found": "missing",
                         "status": "missing"})
    for tool in core.OPTIONAL_TOOLS:
        path = core.shutil.which(tool)
        rows.append({"check": "tool " + tool, "required": "optional", "found": path or "not found",
                     "status": "ok" if path else "optional"})
    for label, path in (("catalog", core.DEFAULT_CATALOG), ("policy", core.DEFAULT_POLICY),
                        ("template", core.DEFAULT_TEMPLATE), ("schema", core.DEFAULT_MANIFEST_SCHEMA)):
        rows.append({"check": label, "required": "asset", "found": str(path),
                     "status": "ok" if path.is_file() else "missing"})
    rows.append({"check": "catalog categories", "required": "32", "found": str(len(catalog["categories"])),
                 "status": "ok" if len(catalog["categories"]) == 32 else "warning"})
    rows.append({"check": "catalog skills", "required": "480",
                 "found": str(core.catalog_skill_count(catalog)),
                 "status": "ok" if core.catalog_skill_count(catalog) == 480 else "warning"})
    rows.append({"check": "network access", "required": "not used", "found": "no network calls implemented",
                 "status": "ok"})
    if _fmt(args) == "json":
        core.emit({"tool": core.TOOL_NAME, "version": core.VERSION, "checks": rows}, "json", _out(args))
    else:
        core.emit(rows, "table", _out(args), columns=["check", "required", "found", "status"])
    missing = [row for row in rows if row["status"] == "missing"]
    return core.EXIT_RUNTIME if missing else core.EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    policy = _policy(args)
    view = {key: value for key, value in policy.items() if not key.startswith("_")}
    if _fmt(args) == "json":
        core.emit({"tool": core.TOOL_NAME, "version": core.VERSION, "policy": view},
                  "json", _out(args))
    else:
        core.emit(view, "markdown", _out(args), title="Effective policy")
    return core.EXIT_OK


def cmd_rules(args: argparse.Namespace) -> int:
    rules = checks.rule_catalog()
    if _fmt(args) == "json":
        core.emit({"tool": core.TOOL_NAME, "count": len(rules), "rules": rules}, "json", _out(args))
    else:
        core.emit(rules, "table", _out(args),
                  columns=["id", "severity", "category", "kind"])
    return core.EXIT_OK


def cmd_selftest(args: argparse.Namespace) -> int:
    path = HERE / "tools-workbench-selftest.py"
    spec = importlib.util.spec_from_file_location("wb_selftest", path)
    if spec is None or spec.loader is None:
        raise core.WorkbenchError("selftest-missing", "Self-test module not found: %s" % path, {}, 5)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wb_selftest"] = module
    spec.loader.exec_module(module)
    return int(module.main())


def cmd_help(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if getattr(args, "topic", None):
        try:
            sub = parser.parse_args([args.topic, "--help"])
        except SystemExit:
            return core.EXIT_OK
        return core.EXIT_OK
    parser.print_help()
    return core.EXIT_OK


DISPATCH = {
    "capabilities": cmd_capabilities,
    "category": cmd_category,
    "skill": cmd_skill,
    "plan": cmd_plan,
    "check": cmd_check,
    "manifest": cmd_manifest,
    "verify": cmd_verify,
    "run": cmd_run,
    "cleanroom": cmd_cleanroom,
    "report": cmd_report,
    "doctor": cmd_doctor,
    "config": cmd_config,
    "rules": cmd_rules,
    "selftest": cmd_selftest,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return core.EXIT_USAGE
    if args.command == "help":
        return cmd_help(args, parser)
    handler = DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        return core.EXIT_USAGE
    try:
        return handler(args)
    except core.WorkbenchError as exc:
        sys.stderr.write(core.json.dumps(exc.to_dict(), indent=2) + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130
    except BrokenPipeError:
        return core.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
