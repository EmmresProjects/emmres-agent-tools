#!/usr/bin/env python3
"""End-to-end self-test for the Offline Engineering Workbench.

Run directly or through the primary CLI:
    python3 offline-engineering-workbench/tools-workbench.py selftest
    python3 offline-engineering-workbench/tools-workbench-selftest.py

The suite builds temporary fixture projects, exercises every major capability
through the real interfaces, verifies structured output and exit codes, checks
a recovery path, and removes all temporary files. It exits 0 on success and 10
on any failure.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE / "tools-workbench.py"


def _load(stem: str, module_name: Optional[str] = None):
    name = module_name or ("wb_core" if stem == "workbench-core" else "wb_%s" % stem.replace("-", "_"))
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "VERSION", None):
        return existing
    path = HERE / ("tools-%s.py" % stem)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("workbench-core")
checks = _load("workbench-checks")
runner = _load("workbench-runner")
planner = _load("workbench-planner")

PASS = 0
FAIL = 0
FAILURES: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print("PASS  %s%s" % (name, (" - " + detail) if detail else ""))
        return True
    FAIL += 1
    FAILURES.append(name)
    print("FAIL  %s%s" % (name, (" - " + detail) if detail else ""))
    return False


def fx(*parts: str) -> str:
    """Join fragments so this self-test source does not trip the rules it tests."""
    return "".join(parts)


def write(root: Path, relative: str, content: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def make_flawed_project(root: Path) -> None:
    write(root, "requirements.txt", fx("flask", ">=2.0\nrequests==2.31.0\n"))
    write(root, "package.json", json.dumps(
        {"name": "demo", "version": "1.0.0",
         "dependencies": {"left-pad": "^1.3.0", "tiny-invariant": "1.0.0"}}, indent=2))
    write(root, "src/app.py",
          "import hashlib\nimport random\n\n"
          + fx("API_", "KEY = \"sk_live_1234567890abcdef\"") + "\n"
          "def run():\n    print(" + fx("random", ".", "random()") + ")\n"
          "    print(" + fx("ev", "al(\"1+1\")") + ")\n"
          "    return " + fx("hashlib", ".", "m", "d5(b\"x\")") + ".hexdigest()\n")
    write(root, "Dockerfile", fx("FROM python:", "latest\nRUN pip install flask\n"))
    write(root, ".env", "SECRET=abc123\n")
    write(root, "install.sh",
          "#!/bin/bash\n" + fx("curl http://127.0.0.1:9/install.sh ", "| bash\n") +
          fx("rm -rf ", "/", "\n"))
    write(root, ".github/workflows/ci.yml",
          "name: ci\non: push\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n")
    write(root, "data.csv", "x,y\n1,2\n")
    write(root, "config.ini",
          fx("de", "bug = true\npath = ", "/home", "/user/project\n"))


def make_clean_project(root: Path) -> None:
    write(root, "README.md", "# Clean demo\n\nBuild and test offline.\n")
    write(root, "LICENSE", "MIT License\n")
    write(root, "requirements.txt", "requests==2.31.0\n")
    write(root, "poetry.lock", "# empty lockfile fixture\n")
    write(root, "sbom.json", json.dumps({"bomFormat": "CycloneDX", "components": []}))
    write(root, "manifest.json", json.dumps({"schema_version": "1.0", "files": []}))
    write(root, ".gitattributes", "*.bin filter=lfs diff=lfs merge=lfs -text\n")
    write(root, "src/app.py", "def add(a, b):\n    return a + b\n")
    write(root, "tests/test_app.py", "from src.app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")


def run_cli(*args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WORKBENCH), *[str(item) for item in args]],
        cwd=str(cwd or HERE), capture_output=True, text=True, timeout=120, check=False,
    )


def assert_exit(name: str, result: subprocess.CompletedProcess, expected: int,
                detail: str = "") -> None:
    check(name, result.returncode == expected,
          "exit=%d expected=%d %s" % (result.returncode, expected, detail))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="workbench-selftest-"))
    try:
        # ------------------------------------------------------------------ #
        # 1. Catalog and policy assets
        # ------------------------------------------------------------------ #
        catalog = core.load_catalog()
        check("catalog loads", core.catalog_skill_count(catalog) == 480,
              "%d skills" % core.catalog_skill_count(catalog))
        check("catalog has 32 categories", len(catalog["categories"]) == 32)
        check("catalog validation accepts asset", core.validate_catalog(catalog) == [])
        check("every skill has name, description, scope",
              all(skill.get("name") and skill.get("description") and skill.get("scope")
                  for _, skill in core.catalog_skills(catalog)))
        policy = core.load_policy()
        check("policy loads", policy["thresholds"]["max_file_bytes"] > 0)
        check("policy validation accepts asset", core.validate_policy(policy) == [])
        bad_policy = dict(policy)
        bad_policy["thresholds"] = dict(policy["thresholds"])
        bad_policy["thresholds"]["max_file_bytes"] = -1
        check("policy validation rejects negative threshold",
              any("max_file_bytes" in error for error in core.validate_policy(bad_policy)))
        manifest_schema = core.load_json(core.DEFAULT_MANIFEST_SCHEMA, "schema")
        check("manifest schema loads", manifest_schema["type"] == "object")

        # ------------------------------------------------------------------ #
        # 2. Flawed project: every major rule family fires
        # ------------------------------------------------------------------ #
        flawed = tmp / "flawed"
        flawed.mkdir()
        make_flawed_project(flawed)
        result = checks.run_checks(flawed, policy, catalog=catalog)
        rule_ids = {finding["rule_id"] for finding in result["findings"]}
        expected_rules = {
            "security/embedded-secret", "security/weak-hash", "security/dynamic-code-execution",
            "build/network-fetch", "build/destructive-command", "build/download-pipe",
            "build/absolute-path", "build/unpinned-base-image", "deps/missing-lockfile",
            "deps/unpinned-python", "deps/unpinned-node", "tests/missing-test-suite",
            "docs/missing-readme", "docs/missing-license", "data/missing-provenance",
            "supply-chain/missing-sbom", "supply-chain/unpinned-action",
            "config/committed-env-file", "repro/unseeded-random",
        }
        missing_rules = sorted(expected_rules - rule_ids)
        check("flawed project triggers all expected rule families", not missing_rules,
              "missing: %s" % ", ".join(missing_rules))
        check("flawed project fails the quality gate", result["status"] == "fail")
        check("structured result has summary and findings",
              isinstance(result["summary"], dict) and isinstance(result["findings"], list))
        check("findings are sorted by severity",
              [f["severity"] for f in result["findings"]] ==
              sorted([f["severity"] for f in result["findings"]],
                     key=lambda s: core.SEVERITY_RANK.get(s, 99)))
        check("secret evidence is redacted",
              all("sk_live_1234567890abcdef" not in finding["evidence"]
                  for finding in result["findings"]))
        check("catalog categories are mapped into the result",
              len(result["catalog_categories_applied"]) > 0)
        check("policy digest is recorded", len(result["policy"]["sha256"]) == 64)

        # Filters
        only_security = checks.run_checks(flawed, policy, only=["security"], catalog=catalog)
        check("--only security restricts rules",
              only_security["findings"] and
              all(f["rule_id"].startswith("security/") for f in only_security["findings"]))
        excluded = checks.run_checks(flawed, policy, exclude=["security", "data", "docs"],
                                     catalog=catalog)
        check("--exclude removes rule families",
              all(not f["rule_id"].startswith(("security/", "data/", "docs/"))
                  for f in excluded["findings"]))

        # Recovery path: fix the fixture and confirm the gate improves
        recovered = tmp / "recovered"
        recovered.mkdir()
        make_clean_project(recovered)
        recovered_check = checks.run_checks(recovered, policy, catalog=catalog)
        check("clean project passes the gate", recovered_check["status"] == "pass",
              "findings=%d" % recovered_check["summary"]["findings"])
        check("clean project has no critical or high findings",
              recovered_check["summary"]["by_severity"].get("critical", 0) == 0 and
              recovered_check["summary"]["by_severity"].get("high", 0) == 0)

        # ------------------------------------------------------------------ #
        # 3. Manifest and verification
        # ------------------------------------------------------------------ #
        manifest_path = tmp / "manifest.json"
        manifest = {
            "schema_version": "1.0", "tool": core.TOOL_NAME, "tool_version": core.VERSION,
            "root": str(recovered), "file_count": 0, "total_bytes": 0, "files": [],
            "environment": core.build_environment(),
        }
        for entry in core.iter_project_files(recovered, policy):
            if entry["too_large"]:
                continue
            manifest["files"].append({"path": entry["rel"], "size": entry["size"],
                                      "sha256": core.sha256_file(entry["path"])})
            manifest["file_count"] += 1
            manifest["total_bytes"] += entry["size"]
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        check("manifest validates against schema",
              core.validate_manifest(manifest, manifest_schema) == [])
        verify_ok = run_cli("verify", "--manifest", manifest_path, "--format", "json")
        assert_exit("verify accepts an unchanged manifest", verify_ok, 0)
        verify_payload = json.loads(verify_ok.stdout)
        check("verify reports all files verified",
              verify_payload["status"] == "ok" and verify_payload["verified"] == manifest["file_count"])
        write(recovered, "src/app.py", "def add(a, b):\n    return a + b + 1\n")
        verify_bad = run_cli("verify", "--manifest", manifest_path, "--format", "json")
        assert_exit("verify detects a modified file", verify_bad, 6)
        check("verify names the modified file",
              any(item["path"] == "src/app.py" for item in json.loads(verify_bad.stdout)["modified"]))
        broken = json.loads(manifest_path.read_text())
        del broken["file_count"]
        broken_path = tmp / "broken-manifest.json"
        broken_path.write_text(json.dumps(broken), encoding="utf-8")
        verify_schema = run_cli("verify", "--manifest", broken_path, "--format", "json")
        assert_exit("verify rejects a schema-invalid manifest", verify_schema, 6)
        check("schema error is reported",
              "file_count" in json.loads(verify_schema.stdout)["schema_errors"][0])

        # ------------------------------------------------------------------ #
        # 4. Planner
        # ------------------------------------------------------------------ #
        task = "Train a reproducible churn model with scikit-learn and validate it offline"
        plan_a = planner.build_plan(task, catalog, policy)
        plan_b = planner.build_plan(task, catalog, policy)
        plan_c = planner.build_plan("Build a local Kafka event pipeline with exactly-once semantics",
                                    catalog, policy)
        check("plan is deterministic", plan_a["plan_id"] == plan_b["plan_id"])
        check("different task produces a different plan", plan_a["plan_id"] != plan_c["plan_id"])
        check("plan selects skills", plan_a["totals"]["selected_skills"] > 0,
              "%d selected" % plan_a["totals"]["selected_skills"])
        check("plan has four lifecycle phases", len(plan_a["phases"]) == 4)
        check("plan includes gates, inputs, outputs, limits, rollback",
              all(plan_a["phases"][0].get("gate") for _ in [0]) and
              bool(plan_a["inputs"] and plan_a["outputs"] and plan_a["limits"] and plan_a["rollback"]))
        check("plan maps to the registered catalog",
              plan_a["totals"]["registered_skills"] == 480)
        check("plan approval classes are reported", len(plan_a["approval_required"]) > 0)
        try:
            planner.build_plan("   ", catalog, policy)
            empty_ok = False
        except core.WorkbenchError:
            empty_ok = True
        check("plan rejects an empty task", empty_ok)

        # ------------------------------------------------------------------ #
        # 5. Bounded runner
        # ------------------------------------------------------------------ #
        dry = runner.run_command(["rm", "-rf", str(tmp)], tmp, dry_run=True, policy=policy)
        check("dry run does not execute", dry["status"] == "dry-run" and dry["would_require_approval"])
        blocked = runner.run_command(["rm", "-rf", str(tmp)], tmp, policy=policy)
        check("risky command is blocked without approval", blocked["status"] == "blocked")
        check("blocked command reports the approval reason",
              blocked["required_approval"] == ["destructive-filesystem"])
        ok = runner.run_command([sys.executable, "-c", "print('ok')"], tmp, policy=policy)
        check("normal command runs", ok["status"] == "ok" and "ok" in ok["stdout"])
        retry = runner.run_command([sys.executable, "-c", "import sys; sys.exit(1)"], tmp,
                                   retries=1, policy=policy)
        check("retries are bounded and recorded", len(retry["attempts"]) == 2)
        timeout = runner.run_command([sys.executable, "-c", "import time; time.sleep(5)"], tmp,
                                     timeout=1, policy=policy)
        check("timeout terminates the command",
              timeout["timed_out"] and timeout["exit_code"] == 124)
        log_path = tmp / "run.jsonl"
        runner.run_command([sys.executable, "-c", "print('logged')"], tmp,
                           log_path=log_path, policy=policy)
        check("audit log is written", log_path.is_file() and "attempt" in log_path.read_text())
        approved = runner.run_command(["curl", "--max-time", "2", "http://127.0.0.1:9/"], tmp,
                                      timeout=5, approve=True, policy=policy)
        check("approval permits a risky command to run", approved["status"] == "failed" and
              approved["approval"]["granted"] is True)
        try:
            runner.run_command([], tmp, policy=policy)
            empty_argv = False
        except core.WorkbenchError:
            empty_argv = True
        check("runner rejects an empty argv", empty_argv)

        # ------------------------------------------------------------------ #
        # 6. Clean room
        # ------------------------------------------------------------------ #
        room_result = checks.run_checks(flawed, policy, catalog=catalog)
        check("clean-room source check is available for comparison",
              room_result["summary"]["findings"] > 0)

        # ------------------------------------------------------------------ #
        # 7. Report rendering
        # ------------------------------------------------------------------ #
        report_result = tmp / "report.md"
        report_cli = run_cli("report", "--result", tmp / "check.json", "--output", report_result)
        # check.json is produced below by the CLI check; create it first if missing
        if not report_result.is_file():
            run_cli("check", "--root", flawed, "--format", "json", "--output", tmp / "check.json")
            report_cli = run_cli("report", "--result", tmp / "check.json", "--output", report_result)
        assert_exit("report renders from a result file", report_cli, 0)
        report_text = report_result.read_text(encoding="utf-8")
        check("report contains the required sections",
              all(section in report_text for section in
                  ("# Offline Engineering Report", "## Summary", "## Findings",
                   "## Capabilities applied", "## Limits and follow-up")))
        check("report lists findings", "security/embedded-secret" in report_text)

        # ------------------------------------------------------------------ #
        # 8. CLI integration and structured errors
        # ------------------------------------------------------------------ #
        version = run_cli("--version")
        assert_exit("version command works", version, 0)
        check("version reports the tool version", core.VERSION in version.stdout)
        summary = run_cli("capabilities", "--summary", "--format", "json")
        assert_exit("capability summary works", summary, 0)
        summary_payload = json.loads(summary.stdout)
        check("capability summary lists 32 categories",
              len(summary_payload["categories"]) == 32)
        all_skills = run_cli("capabilities", "--format", "json")
        assert_exit("capability list works", all_skills, 0)
        check("capability list returns 480 skills",
              json.loads(all_skills.stdout)["returned"] == 480)
        search = run_cli("capabilities", "--search", "kubernetes", "--format", "json")
        check("capability search filters", json.loads(search.stdout)["returned"] > 0)
        category_cli = run_cli("category", "29", "--format", "json")
        check("category command works", json.loads(category_cli.stdout)["number"] == 29)
        skill_cli = run_cli("skill", "cat-29-skill-03", "--format", "json")
        check("skill command works", json.loads(skill_cli.stdout)["skill"]["id"] == "cat-29-skill-03")
        plan_cli = run_cli("plan", "--task", task, "--format", "json")
        assert_exit("plan CLI works", plan_cli, 0)
        check("plan CLI emits a plan id", bool(json.loads(plan_cli.stdout)["plan_id"]))
        check_cli = run_cli("check", "--root", flawed, "--format", "json")
        assert_exit("check CLI fails the gate on a flawed project", check_cli, 4)
        check("check CLI emits structured findings",
              json.loads(check_cli.stdout)["status"] == "fail")
        missing = run_cli("check", "--root", tmp / "does-not-exist")
        assert_exit("check CLI rejects a missing root", missing, 2)
        check("missing-root error is structured",
              json.loads(missing.stderr)["error"]["code"] == "root-missing")
        bad_fail = run_cli("check", "--root", flawed, "--fail-on", "bogus")
        assert_exit("check CLI rejects an unknown severity", bad_fail, 2)
        empty_plan = run_cli("plan", "--task", "")
        assert_exit("plan CLI rejects an empty task", empty_plan, 2)
        blocked_cli = run_cli("run", "--format", "json", "--", "rm", "-rf", str(tmp))
        assert_exit("run CLI blocks risky commands", blocked_cli, 3)
        no_command = run_cli("run", "--format", "json")
        assert_exit("run CLI rejects an empty command", no_command, 2)
        doctor = run_cli("doctor", "--format", "json")
        assert_exit("doctor works", doctor, 0)
        doctor_payload = json.loads(doctor.stdout)
        check("doctor reports 480 registered skills",
              any(row["check"] == "catalog skills" and row["found"] == "480"
                  for row in doctor_payload["checks"]))
        rules_cli = run_cli("rules", "--format", "json")
        check("rules command lists every rule",
              json.loads(rules_cli.stdout)["count"] == len(checks.rule_catalog()))
        config_cli = run_cli("config", "--format", "json")
        check("config command shows the effective policy",
              json.loads(config_cli.stdout)["policy"]["thresholds"]["max_file_bytes"] > 0)
        manifest_cli = run_cli("manifest", "--root", recovered, "--output", tmp / "cli-manifest.json")
        assert_exit("manifest CLI works", manifest_cli, 0)
        check("manifest CLI writes a schema-valid manifest",
              core.validate_manifest(json.loads((tmp / "cli-manifest.json").read_text()),
                                     manifest_schema) == [])
        clean_cli = run_cli("cleanroom", "--root", flawed, "--format", "json")
        assert_exit("cleanroom CLI fails the gate on a flawed project", clean_cli, 4)
        clean_payload = json.loads(clean_cli.stdout)
        check("cleanroom copies the project and checks the copy",
              clean_payload["copied_files"] >= 8 and clean_payload["check"]["findings"] > 0)
        check("cleanroom removes its temporary directory",
              not Path(clean_payload["cleanroom"]).exists())
        help_cli = run_cli("help")
        assert_exit("help command works", help_cli, 0)
        check("help lists the commands", "capabilities" in help_cli.stdout and "selftest" in help_cli.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    print("self-test summary: %d passed, %d failed" % (PASS, FAIL))
    if FAILURES:
        print("failed checks: %s" % ", ".join(FAILURES))
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
