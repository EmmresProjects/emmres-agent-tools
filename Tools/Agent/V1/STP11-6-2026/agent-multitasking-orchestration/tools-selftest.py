#!/usr/bin/env python3
"""Packaged validation suite for the Agent Multitasking Skill Tool.

Runs the real primary CLI via bounded subprocesses, exercises every registered
capability handler in-process, validates package compatibility from a clean
temporary skill root, and checks recovery, approval, cancellation, custom
configuration, source parsing, structured errors, and output files. No shell,
network, or external service is used.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
for _alias, _filename in [
    ("tools_common", "tools-common"),
    ("tools_parser", "tools-parser"),
    ("tools_registry", "tools-registry"),
    ("tools_operations", "tools-operations"),
    ("tools_orchestrator", "tools-orchestrator"),
]:
    sys.modules.setdefault(_alias, import_module(_filename))

from tools_common import (  # noqa: E402
    ToolError,
    current_paths,
    load_config,
    load_json,
    load_schema,
    now_iso,
    schema_errors,
    write_json_atomic,
    write_text_atomic,
)
from tools_operations import CapabilityRuntime, approved_record  # noqa: E402
from tools_registry import load_registry  # noqa: E402


class Report:
    def __init__(self) -> None:
        self.tests: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, evidence: Any = None, *, skipped: bool = False) -> None:
        self.tests.append(
            {
                "name": name,
                "status": "skipped" if skipped else ("pass" if passed else "fail"),
                "passed": None if skipped else bool(passed),
                "evidence": evidence,
            }
        )

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.tests if item["status"] == "fail"]

    @property
    def passed(self) -> bool:
        return not self.failed


def run_cli(args: list[str], *, cwd: Path, package_name: str, timeout: int = 90) -> tuple[int, dict[str, Any], str]:
    command = [sys.executable, f"{package_name}/tools-agent-multitasking.py", *args]
    process = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = {"status": "unparseable", "stdout": process.stdout[:500], "stderr": process.stderr[:500]}
    return process.returncode, payload, process.stderr


def write_relative(root: Path, relative: str, value: Any) -> str:
    target = root / relative
    write_json_atomic(target, value)
    return relative


def read_relative(root: Path, relative: str) -> Any:
    return load_json(root / relative, label=f"Output {relative}")


def run_selftest(*, paths: Any = None, config_path: str | None = None) -> dict[str, Any]:
    paths = paths or current_paths()
    root = paths.root
    package_name = paths.package.name
    report = Report()
    work_dir = Path(tempfile.mkdtemp(prefix="validation-output-", dir=str(root)))
    work_rel = work_dir.relative_to(root).as_posix()
    package_rel = f"{package_name}"
    cli = f"{package_rel}/tools-agent-multitasking.py"
    sample_mission_rel = f"{package_rel}/asset-sample-mission.json"
    html_rel = f"{package_rel}/asset-sample-source.html"
    markdown_rel = f"{package_rel}/asset-sample-source.md"
    text_rel = f"{package_rel}/asset-sample-source.txt"
    counts: dict[str, Any] = {}

    try:
        # 1. Built-in help and capability list through the real primary interface.
        rc, out, _ = run_cli(["help", "--topic", "all"], cwd=root, package_name=package_name)
        report.check("primary help command", rc == 0 and out.get("status") == "ok" and "sections" in out.get("result", {}), {"exit_code": rc, "status": out.get("status")})
        rc, out, _ = run_cli(["capabilities", "--brief"], cwd=root, package_name=package_name)
        brief_count = out.get("result", {}).get("count")
        report.check("capabilities command lists 256", rc == 0 and brief_count == 256, {"exit_code": rc, "count": brief_count})
        rc, out, _ = run_cli(["capability-list", "--brief"], cwd=root, package_name=package_name)
        report.check("capability-list alias", rc == 0 and out.get("result", {}).get("count") == 256, {"exit_code": rc})
        rc, out, _ = run_cli(["catalog", "--view", "coverage"], cwd=root, package_name=package_name)
        coverage = out.get("result", {}).get("data", {})
        report.check(
            "catalog coverage is complete",
            rc == 0 and coverage.get("all_capabilities_have_handlers") is True and coverage.get("profile_counts", {}).get("capabilities") == 256,
            {"categories": coverage.get("profile_counts", {}).get("categories"), "capabilities": coverage.get("profile_counts", {}).get("capabilities")},
        )

        # 2. Every important packaged asset opens and parses.
        json_assets = sorted(path for path in paths.package.iterdir() if path.is_file() and path.suffix == ".json")
        asset_errors: list[str] = []
        for asset in json_assets:
            try:
                load_json(asset, label=asset.name)
            except (ToolError, OSError) as exc:
                asset_errors.append(f"{asset.name}: {exc}")
        text_assets = [html_rel, markdown_rel, text_rel, f"{package_rel}/tools-requirements.txt"]
        for relative in text_assets:
            try:
                if not (root / relative).read_text(encoding="utf-8").strip():
                    asset_errors.append(f"{relative}: empty")
            except OSError as exc:
                asset_errors.append(f"{relative}: {exc}")
        report.check("all packaged assets open and parse", not asset_errors, {"json_assets": len(json_assets), "text_assets": len(text_assets), "errors": asset_errors})

        # 3. Source parsing: copied HTML, Markdown, plain text, merge, and direct PDF when available.
        html_out = f"{work_rel}/parsed-html.json"
        rc, out, _ = run_cli(["parse", "--input", html_rel, "--umbrella-name", "Context Memory Excerpt", "--umbrella-slug", "context-memory-excerpt", "--output", html_out], cwd=root, package_name=package_name)
        html_catalog = read_relative(root, html_out) if rc == 0 else {}
        html_names = [item["name"] for item in html_catalog.get("capabilities", [])]
        report.check(
            "copied HTML parsing ignores markup and numbering",
            rc == 0 and html_catalog.get("counts") == {"categories": 1, "capabilities": 3} and html_names == ["Context Packager", "Checkpoint State Saver", "Cross-Task Memory Firewall"] and not any("class=" in value or "data-" in value for value in html_names),
            {"counts": html_catalog.get("counts"), "names": html_names, "warnings": html_catalog.get("warnings")},
        )
        md_out = f"{work_rel}/parsed-md.json"
        rc, out, _ = run_cli(["parse", "--input", markdown_rel, "--output", md_out], cwd=root, package_name=package_name)
        md_catalog = read_relative(root, md_out) if rc == 0 else {}
        report.check("Markdown table and bold-label parsing", rc == 0 and md_catalog.get("counts") == {"categories": 2, "capabilities": 4}, {"counts": md_catalog.get("counts")})
        txt_out = f"{work_rel}/parsed-txt.json"
        rc, out, _ = run_cli(["parse", "--input", text_rel, "--output", txt_out], cwd=root, package_name=package_name)
        txt_catalog = read_relative(root, txt_out) if rc == 0 else {}
        report.check("single plain-text capability parsing", rc == 0 and txt_catalog.get("counts", {}).get("capabilities") == 1 and txt_catalog.get("umbrella", {}).get("slug") == "general-capabilities", {"umbrella": txt_catalog.get("umbrella")})
        merged_out = f"{work_rel}/merged.json"
        rc, out, _ = run_cli(["merge", "--input", html_rel, "--input", markdown_rel, "--umbrella-name", "Merged Excerpt", "--umbrella-slug", "merged-excerpt", "--output", merged_out], cwd=root, package_name=package_name)
        merged_catalog = read_relative(root, merged_out) if rc == 0 else {}
        category_capabilities = [len(category.get("capabilities", [])) for category in merged_catalog.get("categories", [])]
        report.check("merge produces one unified catalog", rc == 0 and merged_catalog.get("counts") == {"categories": 3, "capabilities": 7} and all(value > 0 for value in category_capabilities), {"counts": merged_catalog.get("counts"), "category_capabilities": category_capabilities})

        pdf_rel = "uploads/Agent_Multitasking_Skill_Catalog.pdf"
        pdf_out = f"{work_rel}/parsed-pdf.json"
        if (root / pdf_rel).is_file():
            rc, out, _ = run_cli(["parse", "--input", pdf_rel, "--input-format", "pdf", "--umbrella-name", "Agent Multitasking", "--umbrella-slug", "agent-multitasking-orchestration", "--output", pdf_out], cwd=root, package_name=package_name)
            pdf_catalog = read_relative(root, pdf_out) if rc == 0 else {}
            report.check("direct PDF parses the full source catalog", rc == 0 and pdf_catalog.get("counts") == {"categories": 25, "capabilities": 256} and not pdf_catalog.get("warnings"), {"counts": pdf_catalog.get("counts"), "warnings": pdf_catalog.get("warnings")})
        else:
            report.check("direct PDF parses the full source catalog", False, {"reason": "source PDF not present at uploads/Agent_Multitasking_Skill_Catalog.pdf"}, skipped=True)

        # 4. Valid, invalid, missing, and malformed inputs produce structured results.
        rc, out, _ = run_cli(["validate", "--kind", "mission", "--input", sample_mission_rel], cwd=root, package_name=package_name)
        report.check("valid mission input", rc == 0 and out.get("result", {}).get("valid") is True, {"exit_code": rc})
        invalid_mission = {"mission_id": "invalid", "goal": "missing tasks"}
        invalid_rel = write_relative(root, f"{work_rel}/invalid-mission.json", invalid_mission)
        rc, out, _ = run_cli(["validate", "--kind", "mission", "--input", invalid_rel], cwd=root, package_name=package_name)
        error = out.get("error", {})
        report.check("invalid mission returns structured error", rc == 2 and out.get("status") == "error" and error.get("code") == "SCHEMA_VALIDATION_FAILED" and bool(error.get("recovery")), {"exit_code": rc, "code": error.get("code")})
        cycle_mission = {
            "mission_id": "cycle",
            "goal": "cycle",
            "tasks": [
                {"task_id": "A", "name": "A", "operation": "validate", "payload": {"checks": {"ok": True}}, "depends_on": ["B"], "priority": 1, "risk": "low"},
                {"task_id": "B", "name": "B", "operation": "validate", "payload": {"checks": {"ok": True}}, "depends_on": ["A"], "priority": 1, "risk": "low"},
            ],
        }
        cycle_rel = write_relative(root, f"{work_rel}/cycle-mission.json", cycle_mission)
        rc, out, _ = run_cli(["validate", "--kind", "mission", "--input", cycle_rel], cwd=root, package_name=package_name)
        report.check("cyclic dependencies are rejected", rc == 2 and out.get("error", {}).get("code") == "CYCLIC_DEPENDENCIES", {"exit_code": rc, "code": out.get("error", {}).get("code")})
        missing_rel = f"{work_rel}/does-not-exist.json"
        rc, out, _ = run_cli(["validate", "--kind", "mission", "--input", missing_rel], cwd=root, package_name=package_name)
        report.check("missing input returns structured error", rc == 2 and out.get("error", {}).get("code") == "FILE_NOT_FOUND", {"exit_code": rc, "code": out.get("error", {}).get("code")})
        rc, out, _ = run_cli(["run-capability"], cwd=root, package_name=package_name)
        report.check("missing required argument returns JSON error", rc == 2 and out.get("error", {}).get("code") == "ARGUMENT_ERROR", {"exit_code": rc, "code": out.get("error", {}).get("code")})

        # 5. Changed input changes capability output.
        base_context = load_json(root / sample_mission_rel, label="Sample mission")
        context_a = copy.deepcopy(base_context)
        context_a["goal"] = "Alpha goal: verify a short local evidence chain"
        context_a["tasks"] = context_a["tasks"][:3]
        context_b = copy.deepcopy(context_a)
        context_b["goal"] = "Beta goal: verify a different local evidence chain"
        context_b["workers"] = context_b["workers"][:2]
        context_b["constraints"] = ["Changed constraint"]
        context_a_rel = write_relative(root, f"{work_rel}/context-a.json", context_a)
        context_b_rel = write_relative(root, f"{work_rel}/context-b.json", context_b)
        run_a_out = f"{work_rel}/run-a.json"
        run_b_out = f"{work_rel}/run-b.json"
        rc_a, _, _ = run_cli(["run-capability", "--capability", "goal-decomposer", "--input", context_a_rel, "--output", run_a_out], cwd=root, package_name=package_name)
        rc_b, _, _ = run_cli(["run-capability", "--capability", "goal-decomposer", "--input", context_b_rel, "--output", run_b_out], cwd=root, package_name=package_name)
        result_a = read_relative(root, run_a_out) if rc_a == 0 else {}
        result_b = read_relative(root, run_b_out) if rc_b == 0 else {}
        changed = (
            result_a.get("artifact", {}).get("input_summary", {}).get("context_fingerprint")
            != result_b.get("artifact", {}).get("input_summary", {}).get("context_fingerprint")
            and result_a.get("artifact", {}).get("input_summary", {}).get("goal")
            != result_b.get("artifact", {}).get("input_summary", {}).get("goal")
            and result_a.get("artifact", {}).get("metrics", {}).get("work_breakdown")
            == result_b.get("artifact", {}).get("metrics", {}).get("work_breakdown")
        )
        report.check("changing input changes the structured result", rc_a == 0 and rc_b == 0 and changed, {"fingerprints": [result_a.get("artifact", {}).get("input_summary", {}).get("context_fingerprint", "")[:12], result_b.get("artifact", {}).get("input_summary", {}).get("context_fingerprint", "")[:12]]})

        # 6. All 256 capability handlers execute through the real runtime interface.
        config = load_config(config_path, paths=paths)
        registry = load_registry(config, paths=paths)
        runtime = CapabilityRuntime(config, registry, paths=paths)
        approval_rel = f"{work_rel}/handler-approval.json"
        handler_failures: list[str] = []
        executed = 0
        approved_executions = 0
        required_risks = set(registry.policy.get("approval", {}).get("required_risks", []))
        for profile in registry.profiles["profiles"]:
            try:
                approval_file = None
                if profile["risk"] in required_risks:
                    write_json_atomic(root / approval_rel, approved_record(profile, approved_by="selftest-human", reason="Handler coverage validation"))
                    approval_file = approval_rel
                    approved_executions += 1
                outcome = runtime.execute(profile["slug"], input_path=context_a_rel, approval_file=approval_file)
                payload = outcome.payload
                if not (
                    outcome.exit_code == 0
                    and payload.get("status") == "ok"
                    and payload.get("handler") == profile["handler"]
                    and payload.get("artifact", {}).get("type") == profile["artifact_type"]
                    and payload.get("capability", {}).get("slug") == profile["slug"]
                ):
                    handler_failures.append(profile["slug"])
                else:
                    executed += 1
            except Exception as exc:
                handler_failures.append(f"{profile['slug']}:{type(exc).__name__}")
        report.check("all 256 capability handlers execute", len(handler_failures) == 0 and executed == 256 and len(runtime.handlers) == 256, {"executed": executed, "approved_executions": approved_executions, "handler_count": len(runtime.handlers), "failures": handler_failures[:10]})
        counts["handlers"] = executed

        # 7. One CLI representative per category engine.
        category_failures: list[str] = []
        for category in registry.catalog["categories"]:
            profile = next((item for item in registry.profiles["profiles"] if item["category_slug"] == category["slug"]), None)
            if profile is None:
                category_failures.append(category["slug"])
                continue
            arguments = ["run-capability", "--capability", profile["slug"]]
            if profile["risk"] in required_risks:
                write_json_atomic(root / approval_rel, approved_record(profile, approved_by="selftest-human", reason="Category CLI validation"))
                arguments += ["--approval-file", approval_rel]
            rc, out, _ = run_cli(arguments, cwd=root, package_name=package_name)
            if rc != 0 or out.get("status") != "ok" or out.get("result", {}).get("status") != "ok":
                category_failures.append(f"{category['slug']}:{rc}:{out.get('error', {}).get('code', out.get('status'))}")
        report.check("25 category engines reachable through CLI", not category_failures, {"categories_tested": len(registry.catalog["categories"]), "failures": category_failures})

        # 8. Approval gates, pending packets, explicit grant, verification, and expiry.
        sensitive = "permission-boundary-manager"
        rc, out, _ = run_cli(["run-capability", "--capability", sensitive], cwd=root, package_name=package_name)
        report.check("critical capability blocked without approval", rc == 3 and out.get("status") == "approval_required" and out.get("result", {}).get("artifact", {}).get("metrics", {}).get("blocked_reason"), {"exit_code": rc, "status": out.get("status")})
        pending_rel = f"{work_rel}/pending.json"
        rc, out, _ = run_cli(["approval", "request", "--capability", sensitive, "--output", pending_rel], cwd=root, package_name=package_name)
        pending = read_relative(root, pending_rel) if rc == 0 else {}
        report.check("approval request creates pending packet only", rc == 0 and pending.get("status") == "pending" and pending.get("side_effects_before_approval") == "none", {"status": pending.get("status")})
        rc, out, _ = run_cli(["approval", "grant", "--capability", sensitive, "--approved-by", "selftest-human", "--reason", "approval gate validation", "--output", f"{work_rel}/unconfirmed.json"], cwd=root, package_name=package_name)
        report.check("grant refuses without explicit confirmation", rc == 3 and out.get("error", {}).get("code") == "EXPLICIT_HUMAN_CONFIRMATION_REQUIRED", {"exit_code": rc, "code": out.get("error", {}).get("code")})
        grant_rel = f"{work_rel}/approved.json"
        rc, out, _ = run_cli(["approval", "grant", "--capability", sensitive, "--confirm-human-approval", "--approved-by", "selftest-human", "--reason", "approval gate validation", "--output", grant_rel], cwd=root, package_name=package_name)
        granted = read_relative(root, grant_rel) if rc == 0 else {}
        report.check("explicit human grant creates approved record", rc == 0 and granted.get("status") == "approved" and granted.get("scope", "").endswith(sensitive), {"status": granted.get("status"), "scope": granted.get("scope")})
        rc, out, _ = run_cli(["approval", "verify", "--file", grant_rel, "--capability", sensitive], cwd=root, package_name=package_name)
        report.check("approval verification succeeds", rc == 0 and out.get("result", {}).get("valid") is True, {"exit_code": rc})
        expired = copy.deepcopy(granted)
        expired["expires_at"] = "2020-01-01T00:00:00Z"
        expired_rel = write_relative(root, f"{work_rel}/expired.json", expired)
        rc, out, _ = run_cli(["approval", "verify", "--file", expired_rel, "--capability", sensitive], cwd=root, package_name=package_name)
        report.check("expired approval is rejected", rc == 3 and out.get("error", {}).get("code") == "APPROVAL_INVALID", {"exit_code": rc, "code": out.get("error", {}).get("code")})
        approved_run_out = f"{work_rel}/approved-run.json"
        rc, out, _ = run_cli(["run-capability", "--capability", sensitive, "--approval-file", grant_rel, "--output", approved_run_out], cwd=root, package_name=package_name)
        approved_result = read_relative(root, approved_run_out) if rc == 0 else {}
        report.check("approved critical capability executes", rc == 0 and approved_result.get("status") == "ok" and approved_result.get("audit", {}).get("approval", {}).get("status") == "approved", {"exit_code": rc, "status": approved_result.get("status")})

        # 9. Cancellation for a single capability.
        capability_cancel_rel = f"{work_rel}/capability-cancel.flag"
        write_text_atomic(root / capability_cancel_rel, "cancel\n")
        rc, out, _ = run_cli(["run-capability", "--capability", "goal-decomposer", "--cancel-file", capability_cancel_rel], cwd=root, package_name=package_name)
        report.check("capability cancellation marker blocks execution", rc == 5 and out.get("status") == "cancelled", {"exit_code": rc, "status": out.get("status")})
        (root / capability_cancel_rel).unlink(missing_ok=True)

        # 10. Mission execution, output schema, cancellation, checkpoint resume, and repeated recovery.
        mission_out = f"{work_rel}/mission.json"
        state_rel = f"{work_rel}/mission-state.json"
        rc, out, _ = run_cli(["mission", "--input", sample_mission_rel, "--state", state_rel, "--output", mission_out], cwd=root, package_name=package_name)
        mission_result = read_relative(root, mission_out) if rc == 0 else {}
        execution = mission_result.get("execution", {})
        report.check(
            "sample mission completes all tasks with one retry",
            rc == 0 and mission_result.get("status") == "ok" and execution.get("tasks_total") == 7 and execution.get("tasks_completed") == 7 and execution.get("tasks_retried") == 1 and mission_result.get("verification", {}).get("passed") is True,
            {"exit_code": rc, "tasks": execution.get("tasks_completed"), "retries": execution.get("tasks_retried"), "verification": mission_result.get("verification", {}).get("passed")},
        )
        mission_schema_errors = schema_errors(mission_result, load_schema("mission_result", paths=paths)) if mission_result else ["missing result"]
        report.check("mission result validates packaged schema", bool(mission_result) and not mission_schema_errors, {"errors": mission_schema_errors[:5]})

        mission_cancel_rel = f"{work_rel}/mission-cancel.flag"
        cancelled_state_rel = f"{work_rel}/cancelled-state.json"
        cancelled_out = f"{work_rel}/mission-cancelled.json"
        write_text_atomic(root / mission_cancel_rel, "cancel\n")
        rc, out, _ = run_cli(["mission", "--input", sample_mission_rel, "--state", cancelled_state_rel, "--cancel-file", mission_cancel_rel, "--output", cancelled_out], cwd=root, package_name=package_name)
        cancelled_result = read_relative(root, cancelled_out) if rc in (0, 5) else {}
        report.check("mission cancellation writes recoverable checkpoint", rc == 5 and cancelled_result.get("status") == "cancelled" and (root / cancelled_state_rel).is_file(), {"exit_code": rc, "status": cancelled_result.get("status")})
        (root / mission_cancel_rel).unlink(missing_ok=True)
        resumed_out = f"{work_rel}/mission-resumed.json"
        rc, out, _ = run_cli(["mission", "--input", sample_mission_rel, "--state", cancelled_state_rel, "--resume", "--output", resumed_out], cwd=root, package_name=package_name)
        resumed_result = read_relative(root, resumed_out) if rc == 0 else {}
        report.check("resume recovers from cancellation", rc == 0 and resumed_result.get("status") == "ok" and resumed_result.get("recovery", {}).get("resumed") is True and resumed_result.get("execution", {}).get("tasks_completed") == 7, {"exit_code": rc, "tasks": resumed_result.get("execution", {}).get("tasks_completed")})
        recovered_out = f"{work_rel}/mission-recovered.json"
        rc, out, _ = run_cli(["mission", "--input", sample_mission_rel, "--state", cancelled_state_rel, "--resume", "--output", recovered_out], cwd=root, package_name=package_name)
        recovered_result = read_relative(root, recovered_out) if rc == 0 else {}
        report.check("second resume skips checkpointed work", rc == 0 and recovered_result.get("execution", {}).get("tasks_recovered") == 7 and recovered_result.get("execution", {}).get("tasks_retried") == 0, {"recovered": recovered_result.get("execution", {}).get("tasks_recovered"), "retries": recovered_result.get("execution", {}).get("tasks_retried")})

        # 11. Sensitive mission requires a mission-scoped human approval.
        sensitive_mission = {
            "mission_id": "sensitive-001",
            "goal": "Run a local sensitive validation only after approval",
            "constraints": ["No external action"],
            "workers": [{"worker_id": "reviewer-1", "role": "reviewer", "capabilities": ["validate"], "capacity": 1}],
            "tasks": [{
                "task_id": "S1",
                "name": "Sensitive local validation",
                "operation": "validate",
                "payload": {"checks": {"policy_ok": True}},
                "depends_on": [],
                "priority": 1,
                "risk": "high",
                "required_role": "reviewer",
                "estimated_seconds": 0.001,
                "transient_failures": 0,
                "requires_approval": True,
            }],
            "acceptance_criteria": ["Sensitive task completes only after approval"],
        }
        sensitive_mission_rel = write_relative(root, f"{work_rel}/sensitive-mission.json", sensitive_mission)
        rc, out, _ = run_cli(["mission", "--input", sensitive_mission_rel], cwd=root, package_name=package_name)
        report.check("sensitive mission blocked without approval", rc == 3 and out.get("status") == "approval_required" and out.get("result", {}).get("recovery", {}).get("pending_approval_packet", {}).get("expected_action") == "mission.execute", {"exit_code": rc})
        mission_approval_rel = f"{work_rel}/mission-approval.json"
        rc, out, _ = run_cli(["approval", "grant", "--scope", "mission:sensitive-001", "--action", "mission.execute", "--confirm-human-approval", "--approved-by", "selftest-human", "--reason", "Sensitive mission validation", "--output", mission_approval_rel], cwd=root, package_name=package_name)
        sensitive_out = f"{work_rel}/sensitive-mission-result.json"
        rc, out, _ = run_cli(["mission", "--input", sensitive_mission_rel, "--approval-file", mission_approval_rel, "--output", sensitive_out], cwd=root, package_name=package_name)
        sensitive_result = read_relative(root, sensitive_out) if rc == 0 else {}
        report.check("approved sensitive mission executes", rc == 0 and sensitive_result.get("status") == "ok" and sensitive_result.get("execution", {}).get("tasks_completed") == 1, {"exit_code": rc, "status": sensitive_result.get("status")})

        # 12. Configurable provider metadata, prompts, policy, paths, and storage.
        custom_config = copy.deepcopy(config)
        custom_prompts = load_json(root / custom_config["paths"]["prompts"], label="Reasoning lenses")
        custom_prompts["lenses"]["adversarial-critique-processor"]["instruction"] = "CUSTOM LENS: enumerate bounded local failure modes."
        custom_prompts_rel = write_relative(root, f"{work_rel}/custom-lenses.json", custom_prompts)
        custom_policy = load_json(root / custom_config["paths"]["policy"], label="Safety policy")
        custom_policy["approval"]["required_risks"] = ["critical"]
        custom_policy_rel = write_relative(root, f"{work_rel}/custom-policy.json", custom_policy)
        custom_config["provider"]["model"] = "configured-none"
        custom_config["runtime"]["concurrency"] = 1
        custom_config["runtime"]["storage"] = "memory"
        custom_config["paths"]["prompts"] = custom_prompts_rel
        custom_config["paths"]["policy"] = custom_policy_rel
        custom_config_rel = write_relative(root, f"{work_rel}/custom-config.json", custom_config)
        custom_lens_out = f"{work_rel}/custom-lens.json"
        rc, out, _ = run_cli(["run-capability", "--config", custom_config_rel, "--capability", "adversarial-critique-processor", "--output", custom_lens_out], cwd=root, package_name=package_name)
        custom_lens_result = read_relative(root, custom_lens_out) if rc == 0 else {}
        report.check("custom prompts and provider model are honored", rc == 0 and custom_lens_result.get("artifact", {}).get("metrics", {}).get("model") == "configured-none" and "CUSTOM LENS" in custom_lens_result.get("artifact", {}).get("metrics", {}).get("lens", ""), {"exit_code": rc, "model": custom_lens_result.get("artifact", {}).get("metrics", {}).get("model")})
        rc, out, _ = run_cli(["run-capability", "--config", custom_config_rel, "--capability", "tool-invocation-planner"], cwd=root, package_name=package_name)
        report.check("custom approval policy changes the gate", rc == 0 and out.get("status") == "ok", {"exit_code": rc, "status": out.get("status")})
        custom_mission_out = f"{work_rel}/custom-mission.json"
        rc, out, _ = run_cli(["mission", "--config", custom_config_rel, "--input", sample_mission_rel, "--state", f"{work_rel}/ignored-state.json", "--output", custom_mission_out], cwd=root, package_name=package_name)
        custom_mission = read_relative(root, custom_mission_out) if rc == 0 else {}
        report.check("custom concurrency and memory storage are honored", rc == 0 and custom_mission.get("execution", {}).get("concurrency") == 1 and custom_mission.get("execution", {}).get("storage") == "memory" and not (root / f"{work_rel}/ignored-state.json").exists(), {"concurrency": custom_mission.get("execution", {}).get("concurrency"), "storage": custom_mission.get("execution", {}).get("storage")})

        # 13. Package compatibility from the original root and a clean temporary skill root.
        rc, out, _ = run_cli(["validate", "--kind", "package"], cwd=root, package_name=package_name)
        package_result = out.get("result", {}).get("details", {})
        report.check("package validation passes in source root", rc == 0 and out.get("result", {}).get("valid") is True, {"valid": out.get("result", {}).get("valid"), "counts": package_result.get("counts"), "failed": [item for item in package_result.get("checks", []) if not item.get("passed")]})
        with tempfile.TemporaryDirectory(prefix="amt-clean-root-") as temp_root_name:
            clean_root = Path(temp_root_name)
            (clean_root / "SKILL.md").write_text("# Test skill root\nThis file exists only to verify package compatibility.\n", encoding="utf-8")
            shutil.copytree(paths.package, clean_root / package_name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            clean_help_rc, clean_help, _ = run_cli(["help", "--topic", "commands"], cwd=clean_root, package_name=package_name)
            clean_package_rc, clean_package, _ = run_cli(["validate", "--kind", "package"], cwd=clean_root, package_name=package_name)
            clean_caps_rc, clean_caps, _ = run_cli(["capabilities", "--brief"], cwd=clean_root, package_name=package_name)
            clean_run_out = "validation-output/clean-root-result.json"
            clean_run_rc, clean_run, _ = run_cli(["run-capability", "--capability", "synthesis-processor", "--input", f"{package_name}/asset-sample-mission.json", "--output", clean_run_out], cwd=clean_root, package_name=package_name)
            clean_result = load_json(clean_root / clean_run_out, label="Clean root result") if clean_run_rc == 0 else {}
            clean_subfolders = [item.name for item in (clean_root / package_name).iterdir() if item.is_dir()]
            report.check(
                "clean skill root with one SKILL.md works using relative paths",
                clean_help_rc == 0
                and clean_package_rc == 0
                and clean_package.get("result", {}).get("valid") is True
                and clean_caps_rc == 0
                and clean_caps.get("result", {}).get("count") == 256
                and clean_run_rc == 0
                and clean_result.get("status") == "ok"
                and (clean_root / "SKILL.md").is_file()
                and not (clean_root / package_name / "SKILL.md").exists()
                and not clean_subfolders,
                {"help": clean_help_rc, "package": clean_package_rc, "capabilities": clean_caps_rc, "run": clean_run_rc, "folder_files": len(list((clean_root / package_name).iterdir())), "subfolders": clean_subfolders},
            )

        # 14. Inspect every generated JSON output and report output counts.
        output_files = sorted(path for path in work_dir.rglob("*.json"))
        unreadable_outputs: list[str] = []
        for output in output_files:
            try:
                load_json(output, label=output.name)
            except (ToolError, OSError) as exc:
                unreadable_outputs.append(f"{output.name}: {exc}")
        report.check("all generated outputs open and parse", not unreadable_outputs, {"output_files": len(output_files), "errors": unreadable_outputs})
        counts["output_files"] = len(output_files)
        counts["source_assets"] = len(json_assets) + len(text_assets)
        counts["categories"] = 25
        counts["capabilities"] = 256
        counts["tests"] = len(report.tests)
        counts["passed"] = sum(1 for item in report.tests if item["status"] == "pass")
        counts["failed"] = len(report.failed)
        counts["skipped"] = sum(1 for item in report.tests if item["status"] == "skipped")
        return {
            "passed": report.passed,
            "status": "ok" if report.passed else "failed",
            "command": "selftest",
            "timestamp": now_iso(),
            "tool_version": "1.0.0",
            "summary": counts,
            "tests": report.tests,
            "failures": report.failed,
            "limitations": [
                "Direct PDF parsing requires the optional pypdf dependency; PDF text extraction and OCR are not bundled.",
                "No external model, connector, shell, network, or arbitrary-code integration is implemented.",
                "Approval records are scope/expiry checked locally; the tool does not authenticate the human approver.",
            ],
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
