#!/usr/bin/env python3
"""Standard-library regression and integration tests for tools-cloudops.py.

Tests use temporary directories outside the component and invoke the real CLI
without a shell. No test contacts a provider, network service, or credential.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
TOOL_DIR = Path(__file__).resolve().parent
PRIMARY = TOOL_DIR / "tools-cloudops.py"
SPEC = importlib.util.spec_from_file_location("cloud_production_operations_test_engine", TOOL_DIR / "tools-engine.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load tools-engine.py")
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def cli(*args: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRIMARY), *map(str, args)],
        cwd=str(cwd or TOOL_DIR), text=True, capture_output=True, timeout=45, check=False,
    )


def stdout_json(result: subprocess.CompletedProcess[str]):
    return json.loads(result.stdout)


def stderr_json(result: subprocess.CompletedProcess[str]):
    return json.loads(result.stderr)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class CloudOpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = engine.Catalog.load(TOOL_DIR / "asset-capabilities.json", 8_388_608)
        cls.policy = engine.load_policy(TOOL_DIR / "asset-default-policy.json")
        cls.schema = engine.load_json(TOOL_DIR / "asset-request-schema.json", 1_048_576, label="test schema")
        cls.example = json.loads((TOOL_DIR / "asset-example-request.json").read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cloudops-test-")
        self.temp = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request_file(self, request, name: str = "request.json") -> Path:
        path = self.temp / name
        write_json(path, request)
        return path

    def test_01_help_version_and_capability_list_aliases(self) -> None:
        help_result = cli("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("capabilities", help_result.stdout)
        self.assertIn("never executes cloud/provider changes", help_result.stdout)
        version = cli("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn(engine.TOOL_VERSION, version.stdout)
        listed = cli("capabilities")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        artifact = stdout_json(listed)
        self.assertEqual(artifact["category_count"], 18)
        self.assertEqual(artifact["capability_count"], 249)
        alias = cli("capability-list", "--query", "Deployment Smoke Testing")
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertEqual(stdout_json(alias)["capability_count"], 1)

    def test_02_catalog_integrity_and_every_capability_plan(self) -> None:
        self.assertEqual(len(self.catalog.capabilities), 249)
        self.assertEqual(len(self.catalog.by_id), 249)
        deadline = engine.Deadline(60)
        for cap in self.catalog.capabilities:
            with self.subTest(capability=cap["id"]):
                request = copy.deepcopy(self.example)
                request["selection"] = {"capabilities": [cap["id"]]}
                report, normalized, capabilities, _ = engine.validate_request(
                    request, self.schema, self.catalog, self.policy, deadline,
                )
                self.assertTrue(report["valid_for_planning"], report)
                plan = engine.build_plan(normalized, capabilities, report, self.policy, deadline)
                self.assertEqual(plan["capabilities"][0]["id"], cap["id"])
                self.assertEqual(plan["capabilities"][0]["description"], cap["description"])
                self.assertGreaterEqual(len(plan["capabilities"][0]["tasks"]), 1)
                self.assertTrue(plan["capabilities"][0]["verification"]["control_id"].startswith(cap["id"] + ":"))
                self.assertFalse(plan["execution"]["performed_by_this_tool"])

    def test_03_all_249_capabilities_through_real_plan_interface(self) -> None:
        request = copy.deepcopy(self.example)
        request["selection"] = {"capabilities": [cap["id"] for cap in self.catalog.capabilities]}
        source = self.request_file(request)
        output = self.temp / "all-plan.json"
        result = cli("plan", "--input", source, "--output", output, "--timeout-seconds", 60)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(plan["summary"]["capability_count"], 249)
        self.assertEqual(plan["summary"]["category_count"], 18)
        self.assertEqual({cap["id"] for cap in plan["capabilities"]}, set(self.catalog.by_id))
        control_ids = [control["id"] for cap in plan["capabilities"] for control in cap["controls"]]
        self.assertEqual(len(control_ids), len(set(control_ids)))
        self.assertGreater(plan["summary"]["task_count"], 1000)
        self.assertEqual(plan["execution"]["live_provider_calls"], 0)

    def test_04_plain_markdown_html_and_json_selection(self) -> None:
        plain = self.temp / "selection.txt"
        plain.write_text("1. Cloud Account Provisioning\nDescription: ignored descriptive text\n", encoding="utf-8")
        result = cli("select", "--input", plain)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stdout_json(result)["capability_count"], 1)

        markdown = self.temp / "selection.md"
        markdown.write_text(
            "## 2. Billing, FinOps, and Capacity Economics\n\n"
            "1. **Skill Name:** DNS Management\n"
            "2. **Skill Name:** Not A Real Capability\n"
            "Description: this line is ignored\n", encoding="utf-8",
        )
        result = cli("select", "--input", markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = stdout_json(result)
        self.assertEqual(selected["capability_count"], 13)  # 12 billing capabilities + DNS Management.
        self.assertIn("dns-management", {cap["id"] for cap in selected["capabilities"]})
        self.assertTrue(selected["warnings"])

        copied_html = self.temp / "selection.html"
        copied_html.write_text(
            "<style>.hidden{display:none}</style><script>bad network call</script>"
            "<div class='catalog' data-tenant='ignored'><h2>3. Identity, Access, and Privileged Operations</h2></div>"
            "<ul><li class='skill' data-id='99'>1. Skill Name: DNS Failover Routing</li>"
            "<li>Description: ignored</li></ul>", encoding="utf-8",
        )
        result = cli("select", "--input", copied_html)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = stdout_json(result)
        self.assertEqual(selected["capability_count"], 14)  # 13 identity capabilities + DNS failover.
        self.assertNotIn("bad network call", result.stdout)

        json_selection = self.temp / "selection.json"
        write_json(json_selection, {"capabilities": ["Cloud Account Provisioning"], "categories": []})
        result = cli("select", "--input", json_selection)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stdout_json(result)["capability_count"], 1)

    def test_05_text_selection_through_plan_with_cli_context(self) -> None:
        source = self.temp / "selection.md"
        source.write_text("- Deployment Smoke Testing\n- Rollback Automation\n", encoding="utf-8")
        result = cli(
            "plan", "--input", source, "--service", "orders-api", "--owner", "platform-team",
            "--provider", "azure", "--environment", "staging",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = stdout_json(result)
        self.assertEqual(plan["context"]["provider"], "azure")
        self.assertEqual(plan["summary"]["capability_count"], 2)

    def test_06_validate_valid_invalid_missing_and_secret_input(self) -> None:
        valid = cli("validate", "--input", TOOL_DIR / "asset-example-request.json")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertTrue(stdout_json(valid)["valid_for_planning"])

        invalid = copy.deepcopy(self.example)
        del invalid["selection"]
        result = cli("validate", "--input", self.request_file(invalid, "invalid.json"))
        self.assertEqual(result.returncode, 2)
        report = stdout_json(result)
        self.assertFalse(report["valid_for_planning"])
        self.assertTrue(any(item["code"] == "schema-required" for item in report["findings"]))

        unknown = copy.deepcopy(self.example)
        unknown["selection"] = {"capabilities": ["not-a-real-capability"]}
        result = cli("plan", "--input", self.request_file(unknown, "unknown.json"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "invalid-request")

        missing = cli("plan", "--input", self.temp / "missing.json")
        self.assertEqual(missing.returncode, 4)
        self.assertEqual(stderr_json(missing)["error"]["code"], "input-not-found")

        malformed = self.temp / "malformed.json"
        malformed.write_text('{"selection": ', encoding="utf-8")
        result = cli("plan", "--input", malformed)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "invalid-json")

        secret = copy.deepcopy(self.example)
        secret["api_token"] = "do-not-leak-this-value"
        result = cli("validate", "--input", self.request_file(secret, "secret.json"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("do-not-leak-this-value", result.stdout)
        self.assertTrue(any(item["code"] == "sensitive-key-forbidden" for item in stdout_json(result)["findings"]))

    def test_07_plan_json_markdown_and_input_change(self) -> None:
        first = cli("plan", "--input", TOOL_DIR / "asset-example-request.json")
        self.assertEqual(first.returncode, 0, first.stderr)
        plan1 = stdout_json(first)
        changed = copy.deepcopy(self.example)
        changed["context"]["service"] = "changed-inventory-service"
        second = cli("plan", "--input", self.request_file(changed, "changed.json"))
        self.assertEqual(second.returncode, 0, second.stderr)
        plan2 = stdout_json(second)
        self.assertNotEqual(plan1["plan_id"], plan2["plan_id"])
        self.assertIn("changed-inventory-service", second.stdout)

        markdown_path = self.temp / "plan.md"
        rendered = cli("plan", "--input", TOOL_DIR / "asset-example-request.json", "--format", "markdown", "--output", markdown_path)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# Cloud Production Operations Plan", markdown)
        self.assertIn("Deployment Smoke Testing", markdown)
        self.assertIn("Live provider calls and shell commands executed: **0 / 0**", markdown)

    def test_08_preview_run_failed_evidence_and_recovery(self) -> None:
        request = copy.deepcopy(self.example)
        plan = stdout_json(cli("plan", "--input", self.request_file(request, "preview.json")))
        control = plan["capabilities"][0]["tasks"][0]["control_id"]
        request["evidence"] = [{
            "control_id": control, "status": "failed", "source": "test://smoke/1",
            "observed_at": engine.iso_time(engine.utc_now()), "summary": "Synthetic failed smoke result.",
        }]
        failed = cli("run", "--input", self.request_file(request, "failed-evidence.json"))
        self.assertEqual(failed.returncode, 3, failed.stderr)
        self.assertFalse(stdout_json(failed)["ready"])
        self.assertTrue(any(item["code"] == "evidence-failed" for item in stdout_json(failed)["assessment"]["failures"]))

        request["evidence"][0]["status"] = "passed"
        recovered = cli("run", "--input", self.request_file(request, "recovered-evidence.json"))
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        artifact = stdout_json(recovered)
        self.assertTrue(artifact["ready"])
        self.assertEqual(artifact["status"], "preview-assessment-passed")
        self.assertFalse(artifact["assessment"]["external_execution_performed"])

    def test_09_execution_approval_gate_and_complete_recovery(self) -> None:
        request = copy.deepcopy(self.example)
        request["selection"] = {"capabilities": ["deployment-smoke-testing"]}
        request["change_intent"] = "execute"
        request["context"].update({"environment": "production", "change_ticket": "", "blast_radius": ""})
        request["recovery"]["rollback_strategy"] = ""
        request["verification"] = {"success_criteria": [], "monitoring": []}
        blocked = cli("run", "--input", self.request_file(request, "blocked.json"))
        self.assertEqual(blocked.returncode, 3, blocked.stderr)
        blocked_artifact = stdout_json(blocked)
        codes = {item["code"] for item in blocked_artifact["assessment"]["failures"]}
        self.assertIn("input-control-failed", codes)
        blocker_codes = {item["code"] for item in blocked_artifact["plan"]["validation"]["execution_blockers"]}
        self.assertTrue({"approval-required", "change-ticket-required", "rollback-required", "monitoring-required"}.issubset(blocker_codes))

        now = engine.utc_now()
        request["context"].update({"change_ticket": "CHG-100", "blast_radius": "One canary instance; automatic pause on failed checks."})
        request["recovery"]["rollback_strategy"] = "Restore the previous tested artifact and verify health."
        request["verification"] = {
            "success_criteria": ["Critical smoke checks pass and p95 latency remains below 300 ms for 15 minutes."],
            "monitoring": ["Watch error rate, p95 latency, saturation, and business completion rate."],
        }
        request["approval"] = {
            "approved": True, "approver": "Example Change Approver", "reference": "APPROVAL-100",
            "approved_at": engine.iso_time(now - dt.timedelta(minutes=5)),
            "expires_at": engine.iso_time(now + dt.timedelta(hours=1)),
        }
        required_controls = [
            control["id"] for cap in blocked_artifact["plan"]["capabilities"]
            for control in cap["controls"] if control["required"]
        ]
        request["evidence"] = [
            {
                "control_id": cid, "status": "passed", "source": f"test://evidence/{index}",
                "observed_at": engine.iso_time(now), "summary": "Synthetic test evidence; no provider action performed.",
            }
            for index, cid in enumerate(required_controls, start=1)
        ]
        ready = cli("run", "--input", self.request_file(request, "ready.json"))
        self.assertEqual(ready.returncode, 0, ready.stderr)
        artifact = stdout_json(ready)
        self.assertTrue(artifact["ready"])
        self.assertEqual(artifact["status"], "ready-for-external-handoff")
        self.assertFalse(artifact["plan"]["execution"]["performed_by_this_tool"])

        request["approval"]["expires_at"] = engine.iso_time(now - dt.timedelta(seconds=1))
        expired = cli("assess", "--input", self.request_file(request, "expired.json"))
        self.assertEqual(expired.returncode, 3)
        self.assertFalse(stdout_json(expired)["ready"])

        destructive = copy.deepcopy(request)
        destructive["selection"] = {"capabilities": ["data-deletion-verification"]}
        destructive["context"]["environment"] = "staging"
        destructive["recovery"]["backup_reference"] = ""
        destructive["evidence"] = []
        destructive_plan = stdout_json(cli("plan", "--input", self.request_file(destructive, "destructive.json")))
        self.assertEqual(destructive_plan["summary"]["highest_risk"], "critical")
        self.assertIn("backup-required", {item["code"] for item in destructive_plan["validation"]["execution_blockers"]})
        self.assertTrue(destructive_plan["capabilities"][0]["flags"]["destructive"])

    def test_10_stale_unknown_and_duplicate_evidence(self) -> None:
        request = copy.deepcopy(self.example)
        plan = stdout_json(cli("plan", "--input", self.request_file(request, "evidence-plan.json")))
        cid = plan["capabilities"][0]["tasks"][0]["control_id"]
        old = engine.iso_time(engine.utc_now() - dt.timedelta(days=60))
        request["evidence"] = [{"control_id": cid, "status": "passed", "source": "test://old", "observed_at": old, "summary": "Old synthetic result."}]
        stale = cli("assess", "--input", self.request_file(request, "stale.json"))
        self.assertEqual(stale.returncode, 3)
        self.assertIn("stale-evidence", {item["code"] for item in stdout_json(stale)["failures"]})

        request["evidence"] = [{"control_id": "not-in-plan:task", "status": "passed", "source": "test://bad", "observed_at": engine.iso_time(engine.utc_now()), "summary": "Unknown control."}]
        unknown = cli("evaluate", "--input", self.request_file(request, "unknown-evidence.json"))
        self.assertEqual(unknown.returncode, 3)
        self.assertIn("unknown-evidence-control", {item["code"] for item in stdout_json(unknown)["failures"]})

        fresh = engine.iso_time(engine.utc_now())
        request["evidence"] = [
            {"control_id": cid, "status": "passed", "source": "test://duplicate/1", "observed_at": fresh, "summary": "First duplicate."},
            {"control_id": cid, "status": "passed", "source": "test://duplicate/2", "observed_at": fresh, "summary": "Second duplicate."},
        ]
        duplicate = cli("assess", "--input", self.request_file(request, "duplicate-evidence.json"))
        self.assertEqual(duplicate.returncode, 3)
        self.assertIn("duplicate-evidence-control", {item["code"] for item in stdout_json(duplicate)["failures"]})

        request["evidence"] = [{"control_id": cid, "status": "passed", "source": "test://future", "observed_at": engine.iso_time(engine.utc_now() + dt.timedelta(hours=1)), "summary": "Future-dated result."}]
        future = cli("assess", "--input", self.request_file(request, "future-evidence.json"))
        self.assertEqual(future.returncode, 3)
        self.assertIn("future-evidence", {item["code"] for item in stdout_json(future)["failures"]})

    def test_11_overwrite_protection_force_recovery_and_cleanup(self) -> None:
        output = self.temp / "nested" / "plan.json"
        first = cli("plan", "--input", TOOL_DIR / "asset-example-request.json", "--output", output)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = output.read_bytes()
        changed = copy.deepcopy(self.example)
        changed["context"]["service"] = "replacement-check"
        source = self.request_file(changed, "replacement.json")
        blocked = cli("plan", "--input", source, "--output", output)
        self.assertEqual(blocked.returncode, 4)
        self.assertEqual(stderr_json(blocked)["error"]["code"], "output-exists")
        self.assertEqual(output.read_bytes(), original)
        forced = cli("plan", "--input", source, "--output", output, "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertNotEqual(output.read_bytes(), original)
        self.assertIn("replacement-check", output.read_text(encoding="utf-8"))
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_12_timeout_cancellation_and_atomic_write_unit_paths(self) -> None:
        expired = engine.Deadline(0.001)
        time.sleep(0.003)
        with self.assertRaises(engine.CloudOpsError) as caught:
            expired.check()
        self.assertEqual(caught.exception.code, "timeout")
        cancelled = engine.Deadline(30, cancelled=lambda: True)
        with self.assertRaises(engine.CloudOpsError) as caught:
            cancelled.check()
        self.assertEqual(caught.exception.exit_code, 130)
        target = self.temp / "cancelled.json"
        with self.assertRaises(engine.CloudOpsError):
            engine.atomic_write(target, "{}", force=False, max_bytes=1000, deadline=cancelled)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.temp.glob("*.tmp")), [])

    def test_13_schema_policy_and_markdown_assessment_are_parseable(self) -> None:
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.policy["policy_version"], "1.0")
        result = cli("assess", "--input", TOOL_DIR / "asset-example-request.json", "--format", "markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# Cloud Production Operations Assessment", result.stdout)
        self.assertIn("| Control | Required | Status | Source |", result.stdout)

    def test_14_invalid_catalog_and_no_selection_are_useful_errors(self) -> None:
        bad_catalog = copy.deepcopy(self.catalog.data)
        bad_catalog["capability_count"] = 1
        source = self.request_file(bad_catalog, "bad-catalog.json")
        result = cli("capabilities", "--catalog", source)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "invalid-catalog")
        empty = self.temp / "empty.txt"
        empty.write_text("Description: no selectable name here\n", encoding="utf-8")
        result = cli("select", "--input", empty)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "no-capability-selected")

        bad_policy = copy.deepcopy(self.policy)
        bad_policy["approval"]["allow_expired"] = True
        policy_path = self.request_file(bad_policy, "unsafe-policy.json")
        result = cli("capabilities", "--policy", policy_path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "invalid-policy")

        bad_schema = copy.deepcopy(self.schema)
        bad_schema["properties"]["context"]["properties"]["service"]["pattern"] = "^[a-z]+$"
        schema_path = self.request_file(bad_schema, "unsupported-schema.json")
        result = cli("validate", "--input", TOOL_DIR / "asset-example-request.json", "--schema", schema_path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(stderr_json(result)["error"]["code"], "invalid-schema")


if __name__ == "__main__":
    unittest.main(verbosity=2)
