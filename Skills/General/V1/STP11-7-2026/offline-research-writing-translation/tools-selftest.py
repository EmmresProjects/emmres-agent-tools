#!/usr/bin/env python3
"""Packaged validation suite for the unified offline research Skill Tool.

Runs the real CLI from a clean temporary skill root with exactly one test
SKILL.md beside the Skill Tool folder. All Skill Tool paths in commands are
relative. Uses only the Python standard library and no network or shell.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

sys.dont_write_bytecode = True


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class Runner:
    def __init__(self, root: pathlib.Path, folder_name: str) -> None:
        self.root = root
        self.folder_name = folder_name
        self.primary = pathlib.Path(folder_name) / "tools-offline-research.py"
        self.workspace = "runtime-workspace"
        self.env = dict(os.environ)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.results: list[dict[str, Any]] = []

    def record(self, name: str, passed: bool, detail: Any = None) -> None:
        self.results.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise AssertionError(f"{name}: {detail}")

    def call(self, args: list[str], expect: int = 0, parse: str = "json") -> Any:
        cmd = [sys.executable, str(self.primary)] + args
        completed = subprocess.run(
            cmd,
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != expect:
            raise AssertionError({"args": args, "expected": expect, "actual": completed.returncode, "stdout": completed.stdout[:500], "stderr": completed.stderr[:500]})
        if parse == "text":
            return completed.stdout
        if parse == "error":
            return json.loads(completed.stderr)
        return json.loads(completed.stdout)

    def common(self, output: str, run_id: str | None = None) -> list[str]:
        args = ["--workspace", self.workspace, "--output", output, "--timeout", "120"]
        if run_id:
            args += ["--run-id", run_id]
        return args


def build_fixture_files(runner: Runner) -> None:
    ws = runner.root / runner.workspace
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "plain-catalog.txt").write_text(
        "1. Offline Installation, Dependency, and Corpus Deployment\n"
        "1. Skill Name: Air-Gapped Research Stack Installer\n"
        "Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for offline installation, dependency, and corpus deployment.\n"
        "2. Skill Name: Clean Offline Corpus Installer\n"
        "Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for offline installation, dependency, and corpus deployment.\n",
        encoding="utf-8",
    )
    (ws / "markdown-catalog.md").write_text(
        "## Scholarly Databases and Academic Corpora\n"
        "- **Skill Name:** Offline Scholarly Database Manager\n"
        "Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for scholarly databases and academic corpora.\n"
        "- **Skill Name:** Journal Article Corpus Manager\n"
        "Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for scholarly databases and academic corpora.\n",
        encoding="utf-8",
    )
    (ws / "copied-catalog.html").write_text(
        '<div class="catalog-row" data-row-id="7" style="font-size:14px">\n'
        "  <span>Category: Translation Models and Multilingual Language Systems</span>\n"
        "  <p>1. Skill Name: Local Translation Model Manager</p>\n"
        "  <p>Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for translation models and multilingual language systems.</p>\n"
        "  <p>2. Skill Name: Multilingual Language Model Manager</p>\n"
        "  <p>Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for translation models and multilingual language systems.</p>\n"
        "</div>\n",
        encoding="utf-8",
    )
    (ws / "single-capability.txt").write_text(
        "Skill Name: Grounded Citation Validator\n"
        "Description: Designs, executes, or validates the complete offline research, writing, and translation workflow for citation validation.\n",
        encoding="utf-8",
    )
    (ws / "invalid.jsonl").write_text("not json\n", encoding="utf-8")
    batch_lines = [
        {"operation": "retrieval"},
        {"operation": "does-not-exist", "input": f"{runner.folder_name}/asset-sample-corpus.jsonl"},
        {"operation": "retrieval", "input": f"{runner.folder_name}/asset-sample-corpus.jsonl", "params": {"query": "checksum"}},
    ]
    (ws / "batch.jsonl").write_text("\n".join(json.dumps(x) for x in batch_lines) + "\n", encoding="utf-8")
    source = runner.root / runner.folder_name / "asset-sample-corpus.jsonl"
    changed: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["id"] == "src-001":
            record["text"] = "Offline research requires a pinned corpus snapshot and a provenance ledger. The 2026 field guide records a retrieval recall target of 0.80 for English."
        changed.append(json.dumps(record, ensure_ascii=False))
    (ws / "changed-corpus.jsonl").write_text("\n".join(changed) + "\n", encoding="utf-8")


def run_suite(root: pathlib.Path, folder_name: str) -> dict[str, Any]:
    runner = Runner(root, folder_name)
    build_fixture_files(runner)
    corpus = f"{folder_name}/asset-sample-corpus.jsonl"
    ws = "runtime-workspace"

    help_text = runner.call(["help"], parse="text")
    runner.record("built-in help", folder_name in help_text and "capabilities" in help_text, {"chars": len(help_text)})

    ops = runner.call(["operations"] + runner.common(f"{ws}/operations.json"))
    runner.record("operation list", ops["ok"] and ops["result"]["operation_count"] == 32, ops["result"]["operation_count"])

    caps = runner.call(["capabilities", "--verify", "--limit", "3", "--format", "json"] + runner.common(f"{ws}/capabilities.json"))
    verification = caps["result"]["verification"]
    runner.record(
        "capability list and verify",
        caps["ok"] and caps["result"]["capability_count"] == 480 and caps["result"]["category_count"] == 32 and verification["passed"],
        {"capabilities": caps["result"]["capability_count"], "categories": caps["result"]["category_count"], "verification": verification},
    )

    for name, expected_caps, expected_umbrella in [
        ("plain-catalog.txt", 2, "offline-installation-dependency-and-corpus-deployment"),
        ("markdown-catalog.md", 2, "scholarly-databases-and-academic-corpora"),
        ("copied-catalog.html", 2, "translation-models-and-multilingual-language-systems"),
    ]:
        parsed = runner.call(["catalog", "parse", "--input", f"{ws}/{name}"] + runner.common(f"{ws}/{name}.parsed.json"))["result"]["parsed"]
        runner.record(f"catalog parse {name}", parsed["capability_count"] == expected_caps and parsed["skill_tool_name"] == expected_umbrella, {"count": parsed["capability_count"], "umbrella": parsed["skill_tool_name"]})
    single = runner.call(["catalog", "parse", "--input", f"{ws}/single-capability.txt", "--category-hint", "Citation Validation"] + runner.common(f"{ws}/single.parsed.json"))["result"]["parsed"]
    runner.record("single capability input", single["capability_count"] == 1 and single["skill_tool_name"] == "citation-validation", single["skill_tool_name"])
    unified = runner.call(["catalog", "unify", "--input", f"{ws}/copied-catalog.html", "--source-label", "HTML selftest", "--edition", "test-1"] + runner.common(f"{ws}/unified.json"))["result"]["unified_spec"]
    runner.record("catalog unify", unified["capability_count"] == 2 and unified["operation_count"] == 1, {"capabilities": unified["capability_count"], "operations": unified["operation_count"]})

    retrieval_a = runner.call(["run", "--operation", "retrieval", "--input", corpus, "--params", '{"query":"checksum manifest","limit":5}'] + runner.common(f"{ws}/retrieval-a.json"))["result"]
    retrieval_b = runner.call(["run", "--operation", "retrieval", "--input", f"{ws}/changed-corpus.jsonl", "--params", '{"query":"checksum manifest","limit":5}'] + runner.common(f"{ws}/retrieval-b.json"))["result"]
    retrieval_c = runner.call(["run", "--operation", "retrieval", "--input", corpus, "--params", '{"query":"seagrass","limit":5}'] + runner.common(f"{ws}/retrieval-c.json"))["result"]
    runner.record("retrieval valid output", retrieval_a["result_count"] > 0 and {"src-001", "src-007"}.issubset({r["source_id"] for r in retrieval_a["results"]}), [r["source_id"] for r in retrieval_a["results"]])
    runner.record("changed input changes result", retrieval_a["results"] != retrieval_b["results"], {"before": [r["source_id"] for r in retrieval_a["results"]], "after": [r["source_id"] for r in retrieval_b["results"]]})
    runner.record("changed query changes result", retrieval_a["results"] != retrieval_c["results"], {"before": [r["source_id"] for r in retrieval_a["results"]], "after": [r["source_id"] for r in retrieval_c["results"]]})

    missing = runner.call(["run", "--operation", "retrieval", "--input", f"{ws}/missing.jsonl"] + runner.common(f"{ws}/missing-output.json"), expect=2, parse="error")
    invalid = runner.call(["run", "--operation", "retrieval", "--input", f"{ws}/invalid.jsonl"] + runner.common(f"{ws}/invalid-output.json"), expect=2, parse="error")
    runner.record("missing input structured error", missing["error"]["code"] == "INVALID_INPUT", missing["error"]["code"])
    runner.record("invalid input structured error", invalid["error"]["code"] == "INVALID_INPUT", invalid["error"]["code"])
    batch = runner.call(["batch", "--input", f"{ws}/batch.jsonl"] + runner.common(f"{ws}/batch-results.jsonl"))["result"]
    runner.record("batch missing/invalid/valid", batch["line_count"] == 3 and batch["succeeded"] == 1 and batch["failed"] == 2, {"succeeded": batch["succeeded"], "failed": batch["failed"]})

    request_schema = json.loads((root / folder_name / "asset-request-schema.json").read_text(encoding="utf-8"))
    operations = request_schema["properties"]["operation"]["enum"]
    operation_summaries: dict[str, int] = {}
    evaluation_result: dict[str, Any] | None = None
    for index, operation in enumerate(operations, 1):
        params = {"query": "checksum manifest", "limit": 5, "target_language": "es", "package_path": f"{ws}/op-handoff-{index}.zip"}
        payload = runner.call(["run", "--operation", operation, "--input", corpus, "--params", json.dumps(params)] + runner.common(f"{ws}/operation-{index}.json", f"selftest-op-{index}"))["result"]
        operation_summaries[operation] = len(payload)
        if operation == "evaluation":
            evaluation_result = payload
    runner.record("all 32 operations through run interface", len(operation_summaries) == 32 and all(v > 5 for v in operation_summaries.values()), {"operation_count": len(operation_summaries)})

    catalog = json.loads((root / folder_name / "asset-capability-catalog.json").read_text(encoding="utf-8"))
    picks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for capability in catalog["capabilities"]:
        if capability["category"] not in seen:
            seen.add(capability["category"])
            picks.append(capability)
    for index, capability in enumerate(picks, 1):
        params = {"query": "checksum manifest", "limit": 5, "target_language": "es", "package_path": f"{ws}/capability-handoff-{index}.zip"}
        payload = runner.call(["run-capability", "--name", capability["name"], "--input", corpus, "--params", json.dumps(params)] + runner.common(f"{ws}/capability-{index}.json", f"selftest-cap-{index}"))["result"]
        if payload["capability"]["exact_name"] != capability["name"] or payload["execution"]["operation"] != capability["operation"]:
            raise AssertionError(f"Exact capability dispatch failed: {capability['name']}")
    runner.record("exact-name dispatch for all 32 categories", len(picks) == 32, {"picks": len(picks)})

    plan = runner.call(["run-capability", "--name", picks[0]["name"], "--plan-only"] + runner.common(f"{ws}/plan.json"))["result"]
    runner.record("plan-only declaration", plan["plan_only"] and plan["capability"]["status"] == "declared-plan-only", plan["capability"]["status"])

    pipeline = runner.call(["pipeline", "--input", corpus, "--query", "checksum manifest"] + runner.common(f"{ws}/pipeline.json", "selftest-pipeline"))["result"]
    runner.record("full 32-stage pipeline", pipeline["status"] == "completed" and pipeline["stages_completed"] == 32, {"stages": pipeline["stages_completed"]})

    cancellation = runner.call(["pipeline", "--input", corpus, "--query", "seagrass", "--cancel-after", "5"] + runner.common(f"{ws}/cancel.json", "selftest-cancel"), expect=5, parse="error")
    checkpoint = json.loads((root / ws / "checkpoint-selftest-cancel.json").read_text(encoding="utf-8"))
    runner.record("cancellation checkpoint", cancellation["error"]["code"] == "CANCELLED" and checkpoint["status"] == "cancelled" and checkpoint["stage_index"] == 5, {"status": checkpoint["status"], "stage": checkpoint["stage_index"]})
    recovered = runner.call(["recover", "--resume", "--clear-cancel"] + runner.common(f"{ws}/recovered.json", "selftest-cancel"))["result"]
    runner.record("recovery resume path", recovered["status"] == "completed" and recovered["stages_completed"] == 32 and recovered["recovery"]["resumed_from_stage"] == 6, {"status": recovered["status"], "resumed_from": recovered["recovery"]["resumed_from_stage"]})

    evaluation = evaluation_result or {}
    runner.record("evaluation fixtures", evaluation.get("overall_gate", {}).get("passed") is True, {"pass_rate": evaluation.get("pass_rate")})

    tokens: dict[str, str] = {}
    for action in ("export", "publish", "purge"):
        ticket = f"SELFTEST-{action.upper()}"
        runner.call(["review", "request", "--action", action, "--ticket", ticket, "--notes", "human inspected"] + runner.common(f"{ws}/request-{action}.json"))
        approved = runner.call(["review", "approve", "--ticket", ticket, "--approver", "Selftest Reviewer", "--confirm", "APPROVE"] + runner.common(f"{ws}/approve-{action}.json"))
        tokens[action] = approved["result"]["record"]["approval_token"]
    missing_approval = runner.call(["export", "--input", corpus, "--package", f"{ws}/no-token.zip", "--approval-token", ""] + runner.common(f"{ws}/no-token.json"), expect=4, parse="error")
    runner.record("approval gate blocks export", missing_approval["error"]["code"] == "APPROVAL_REQUIRED", missing_approval["error"]["code"])
    export = runner.call(["export", "--input", corpus, "--package", f"{ws}/approved-export.zip", "--approval-token", tokens["export"]] + runner.common(f"{ws}/export-result.json"))["result"]
    runner.record("approved local export", export["entry_count"] > 4 and export["network_export_performed"] is False, {"entries": export["entry_count"]})
    publish = runner.call(["publish", "--input", corpus, "--approval-token", tokens["publish"]] + runner.common(f"{ws}/publish-result.json"))["result"]
    runner.record("approved local publication record", publish["record"]["published_externally"] is False, publish["record"]["status"])
    bad_confirm = runner.call(["purge", "--target", f"{ws}/export-result.json", "--action", "purge", "--approval-token", tokens["purge"], "--confirm", "NO"] + runner.common(f"{ws}/bad-purge.json"), expect=2, parse="error")
    runner.record("delete confirmation gate", bad_confirm["error"]["code"] == "INVALID_INPUT", bad_confirm["error"]["code"])
    purge = runner.call(["purge", "--target", f"{ws}/export-result.json", "--action", "purge", "--approval-token", tokens["purge"], "--confirm", "DELETE"] + runner.common(f"{ws}/purge-result.json"))["result"]
    runner.record("approved purge", not (root / ws / "export-result.json").exists() and purge["deleted_file"] == "export-result.json", purge["deleted_file"])

    audit = runner.call(["validate-package", "--path", folder_name, "--strict"] + runner.common(f"{ws}/folder-audit.json"))["result"]
    runner.record("folder compatibility audit", audit["passed"] and audit["folder_audit"]["checks"]["no_skill_md"] and audit["folder_audit"]["checks"]["no_internal_subfolders"], {"file_count": audit["folder_audit"]["file_count"]})

    structure = {
        "one_test_skill_md_at_root": (root / "SKILL.md").is_file(),
        "skill_tool_folder_at_root": (root / folder_name).is_dir(),
        "no_skill_md_inside_tool_folder": not (root / folder_name / "SKILL.md").exists(),
        "no_pycache_inside_tool_folder": not any((root / folder_name).glob("__pycache__")),
        "knowledge_file_count": len(list((root / folder_name).glob("knowledge-*"))) == 1,
        "tool_file_count": len(list((root / folder_name).glob("tools-*"))),
        "asset_file_count": len(list((root / folder_name).glob("asset-*"))),
    }
    runner.record("clean future skill root structure", all(v for k, v in structure.items() if isinstance(v, bool)), structure)

    passed = sum(1 for r in runner.results if r["passed"])
    return {
        "ok": True,
        "tool": folder_name,
        "python": sys.version.split()[0],
        "tests_run": len(runner.results),
        "tests_passed": passed,
        "tests_failed": len(runner.results) - passed,
        "results": runner.results,
        "clean_root": str(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools-selftest.py", description="Validate the Skill Tool from a clean temporary skill root.")
    parser.add_argument("--output", help="Write the JSON test report to this path.")
    parser.add_argument("--keep-root", action="store_true", help="Keep the temporary skill root for inspection.")
    args = parser.parse_args(argv)
    source = pathlib.Path(__file__).resolve().parent
    folder_name = source.name
    temp = tempfile.TemporaryDirectory(prefix="orwt-selftest-")
    root = pathlib.Path(temp.name)
    report: dict[str, Any]
    try:
        shutil.copytree(source, root / folder_name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (root / "SKILL.md").write_text("# Test parent skill root\n\nExactly one test SKILL.md beside the Skill Tool folder.\n", encoding="utf-8")
        report = run_suite(root, folder_name)
    except Exception as exc:
        report = {"ok": False, "tool": folder_name, "error": {"type": type(exc).__name__, "message": str(exc)}, "recovery": "Inspect the failing test, restore the exact prefixed files, and rerun tools-selftest.py."}
    finally:
        if args.keep_root:
            temp.cleanup = lambda: None  # type: ignore[method-assign]
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text, end="")
    if not args.keep_root:
        temp.cleanup()
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
