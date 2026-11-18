#!/usr/bin/env python3
"""tools_selftest.py - end-to-end verification harness for the Skill Tool.

Runs every capability through its real command-line interface inside a throwaway
temporary directory, checks the JSON contract of each result, exercises the error
paths, and reports pass/fail counts. Nothing is installed, registered, or sent.

Commands
  run     execute all checks (default when no command is given)
  list    list check names without running them

Exit codes: 0 all checks passed, 1 at least one check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_common as C

PROG = "tools_selftest.py"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ("tools_catalog.py", "tools_plan.py", "tools_schedule.py",
         "tools_scaffold.py", "tools_governance.py", "tools_selftest.py")


def cli(tool, args, timeout=300):
    return subprocess.run([sys.executable, os.path.join(HERE, tool)] + args,
                          cwd=HERE, capture_output=True, text=True, timeout=timeout)


def ok_json(proc, expect_exit=0):
    assert proc.returncode == expect_exit, (
        "exit %s != %s; stderr=%s" % (proc.returncode, expect_exit,
                                      proc.stderr[-400:]))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError("stdout is not JSON: %r" % proc.stdout[:200])
    assert data.get("tool"), "missing tool field"
    return data


def fail_json(proc, code, expect_exit=2):
    data = ok_json(proc, expect_exit=expect_exit)
    assert data.get("ok") is False, "error payload must set ok=false"
    assert data["error"]["code"] == code, (
        "error code %r != %r" % (data["error"]["code"], code))
    return data


def build_checks(workspace, fixtures, state):
    """Return an ordered list of (name, callable) checks sharing `state`."""

    def help_commands():
        sizes = {}
        for tool in TOOLS:
            proc = cli(tool, ["--help"])
            assert proc.returncode == 0, "%s --help exited %s" % (tool, proc.returncode)
            assert "usage:" in proc.stdout, "%s --help lacks usage" % tool
            sizes[tool] = len(proc.stdout.splitlines())
        return sizes

    # -- catalog ---------------------------------------------------------
    def catalog_list():
        data = ok_json(cli("tools_catalog.py", ["list"]))
        assert data["category_total"] == 20, data["category_total"]
        assert data["catalog"]["skill_total"] == 198
        return {"categories": 20, "skills": 198}

    def catalog_list_category():
        data = ok_json(cli("tools_catalog.py", ["list", "--category", "3"]))
        assert data["category"]["name"] == "Task Scheduling and Event Automation"
        assert len(data["skills"]) == 9
        assert all(s["description"] for s in data["skills"])
        return {"skills": len(data["skills"])}

    def catalog_search():
        data = ok_json(cli("tools_catalog.py",
                           ["search", "--query", "bulk rename files", "--limit", "5"]))
        ids = [row["id"] for row in data["results"]]
        assert "bulk-rename-bot" in ids, ids
        assert data["result_count"] <= 5
        return {"top": ids[:3]}

    def catalog_info():
        data = ok_json(cli("tools_catalog.py", ["info", "invoice-processing-bot"]))
        assert data["skill"]["number"] == 132
        assert data["same_category"], "expected sibling skills"
        return {"stage": data["stage"]}

    def catalog_check():
        data = ok_json(cli("tools_catalog.py", ["check"]))
        assert data["ok"] is True, [c for c in data["checks"] if not c["ok"]]
        names = {c["check"] for c in data["checks"]}
        for required in ("skill_numbers_sequential", "category_counts_match",
                         "no_skill_md_in_tool_folder", "no_internal_subfolders",
                         "all_files_prefixed"):
            assert required in names, "missing integrity check %s" % required
        return {"checks": len(data["checks"])}

    # -- parse -----------------------------------------------------------
    def parse_formats():
        out = {}
        for case in fixtures["parse_cases"]:
            path = os.path.join(workspace, "parse-%s.txt" % case["name"])
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(case["body"])
            data = ok_json(cli("tools_catalog.py", ["parse", "--input", path]))
            got = [[s["name"], s["description"]] for s in data["skills"]]
            assert got == case["expect_skills"], "%s -> %r" % (case["name"], got)
            out[case["name"]] = len(got)
        return out

    def parse_ignores_noise():
        path = os.path.join(workspace, "mixed.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# File, Folder, and Storage Automation\n\n"
                     "Focused skills for this part of the workflow.\n"
                     "1. Skill Name: Smart File Organizer\n"
                     "Description: Sorts files by type, date, and project.\n"
                     "## Notes\n"
                     "2. Skill Name: Folder Watch Bot\n"
                     "Description: Watches folders for new or changed files.\n"
                     "unrelated prose that must be ignored\n")
        data = ok_json(cli("tools_catalog.py", ["parse", "--input", path]))
        assert data["skill_count"] == 2, data["skill_count"]
        assert data["umbrella"]["id"] == "file-folder-and-storage-automation-skills"
        assert len(data["categories"]) == 1, data["categories"]
        return data["umbrella"]["id"]

    def parse_reproduces_packaged_catalog():
        """Re-parsing the catalog text rebuilt from the asset must round-trip."""
        catalog = C.Catalog()
        lines = []
        current = None
        for skill in catalog.skills:
            if skill["category"] != (current or {}).get("name"):
                current = skill
                lines.append("\n%d. %s" % (skill["category_number"], skill["category"]))
            lines.append("%d. Skill Name: %s" % (skill["number"], skill["name"]))
            lines.append("Description: %s" % skill["description"])
        path = os.path.join(workspace, "roundtrip.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        data = ok_json(cli("tools_catalog.py", ["parse", "--input", path,
                                                "--max-skills", "400"]))
        assert data["skill_count"] == 198, data["skill_count"]
        got = [(s["number"], s["name"], s["description"]) for s in data["skills"]]
        want = [(s["number"], s["name"], s["description"]) for s in catalog.skills]
        assert got == want, "round-trip mismatch"
        return {"skills": len(got)}

    # -- route -----------------------------------------------------------
    def route_recipe_cases():
        out = {}
        order = ["plan", "build", "trigger", "guard", "verify", "operate", "handoff"]
        for case in fixtures["route_cases"]:
            data = ok_json(cli("tools_catalog.py", ["route", "--goal", case["goal"],
                                                    "--approve", "selftest"]))
            assert data["selection_source"] == "recipe:%s" % case["expect_recipe"], (
                "%r -> %s" % (case["goal"], data["selection_source"]))
            numbers = [s["number"] for s in data["skills"]]
            missing = [n for n in case["must_include"] if n not in numbers]
            assert not missing, "missing %s for %r" % (missing, case["goal"])
            idx = [order.index(s["stage"]) for s in data["skills"]]
            assert idx == sorted(idx), "skills out of lifecycle order"
            out[case["expect_recipe"]] = data["risk"]["level"]
        return out

    def route_explicit_skills():
        data = ok_json(cli("tools_catalog.py",
                           ["route", "--goal", "custom bundle", "--skills", "12,41,96"]))
        assert sorted(s["number"] for s in data["skills"]) == [12, 41, 96]
        assert data["selection_source"] == "explicit"
        return [12, 41, 96]

    def test_harness_tmpdir(base, name):
        """Create an isolated directory under the harness temp root."""
        path = os.path.join(base, name)
        os.makedirs(path, exist_ok=True)
        return path

    def route_high_risk_gate():
        # critical goals are refused outright until a human approves them
        refused = fail_json(cli("tools_catalog.py",
                                ["route", "--goal",
                                 "delete old customer records and publish results"]),
                            code="approval_required")
        assert refused["error"]["detail"]["risk"]["level"] == "critical"
        data = ok_json(cli("tools_catalog.py",
                           ["route", "--goal",
                            "delete old customer records and publish results",
                            "--approve", "risk-review-board"]))
        assert data["approval_required"] is True, data["risk"]
        assert data["approved_by"] == "risk-review-board"
        bundle = os.path.join(workspace, "high-risk-unapproved.json")
        with open(bundle, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(data, approved_by=None)))
        blocked = fail_json(cli("tools_plan.py", ["workflow", "--input", bundle]),
                            code="approval_required")
        assert blocked["error"]["detail"]["risk_level"] in ("high", "critical")
        dry = ok_json(cli("tools_plan.py", ["dryrun", "--input", bundle]))
        assert dry["approval"]["required"] is True
        assert dry["approval"]["recorded_approver"] is None
        data["approved_by"] = "selftest-approver"
        approved = os.path.join(workspace, "high-risk-approved.json")
        with open(approved, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        plan = ok_json(cli("tools_plan.py", ["workflow", "--input", approved]))
        assert plan["execution_allowed"] is True
        return {"risk": data["risk"]["level"], "refused_then_approved": True}

    def route_errors():
        no_bundle = fail_json(cli("tools_catalog.py", ["route", "--goal", "zzzz qqqq wwww"]),
                              code="no_bundle")
        assert "hint" in no_bundle["error"]["detail"]
        fail_json(cli("tools_catalog.py", ["info", "999"]), code="unknown_skill")
        fail_json(cli("tools_catalog.py", ["parse", "--input",
                                           os.path.join(workspace, "nope.txt")]),
                  code="missing_file")
        return True

    def make_bundle():
        path = os.path.join(workspace, "bundle.json")
        proc = cli("tools_catalog.py", ["route", "--goal",
                                        "process new invoices every night",
                                        "--approve", "selftest", "--out", path])
        assert proc.returncode == 0, proc.stderr[-300:]
        assert os.path.isfile(path), "--out did not write the bundle"
        data = json.load(open(path, encoding="utf-8"))
        assert data["approved_by"] == "selftest" and data["skills"]
        state["bundle"] = path
        return path

    # -- plan ------------------------------------------------------------
    def plan_workflow():
        data = ok_json(cli("tools_plan.py", ["workflow", "--input", state["bundle"]]))
        assert data["dependencies"], "expected dependency edges"
        assert data["limits"]["max_retries"] >= 1
        assert data["definition_of_done"]
        assert data["execution_allowed"] is True, "approved bundle must be allowed"
        return {"stages": [s["stage"] for s in data["stages"]]}

    def plan_estimate():
        data = ok_json(cli("tools_plan.py",
                           ["estimate", "--input", state["bundle"],
                            "--runs-per-month", "22", "--minutes-saved", "15",
                            "--labor-rate", "40"]))
        assert data["monthly"]["hours_saved"] == 5.5, data["monthly"]
        assert data["monthly"]["labor_saving"] == 220.0
        assert data["verdict"] in ("viable", "review", "not-viable")
        return {"verdict": data["verdict"], "payback_months": data["payback_months"]}

    def plan_estimate_changes_with_input():
        low = ok_json(cli("tools_plan.py", ["estimate", "--input", state["bundle"],
                                            "--runs-per-month", "4",
                                            "--minutes-saved", "10"]))
        high = ok_json(cli("tools_plan.py", ["estimate", "--input", state["bundle"],
                                             "--runs-per-month", "40",
                                             "--minutes-saved", "30"]))
        assert high["monthly"]["hours_saved"] > low["monthly"]["hours_saved"]
        return {"low": low["monthly"]["hours_saved"], "high": high["monthly"]["hours_saved"]}

    def plan_dryrun():
        base = ok_json(cli("tools_plan.py", ["dryrun", "--input", state["bundle"]]))
        assert base["applies_changes"] is False
        assert base["mode"] == "dry-run" and base["steps"]
        assert base["approval"]["recorded_approver"] == "selftest"
        cleanup = os.path.join(workspace, "cleanup-bundle.json")
        ok_json(cli("tools_catalog.py", ["route", "--goal",
                                         "clean up duplicate files on the shared drive",
                                         "--approve", "selftest", "--out", cleanup]))
        data = ok_json(cli("tools_plan.py", ["dryrun", "--input", cleanup]))
        assert data["skills_requiring_preview"], "cleanup bundle needs a preview step"
        assert data["approval"]["required"] is True
        return {"steps": len(data["steps"]),
                "preview_skills": [m["skill"] for m in data["skills_requiring_preview"]]}

    def plan_errors():
        fail_json(cli("tools_plan.py", ["workflow", "--input",
                                        os.path.join(workspace, "missing.json")]),
                  code="missing_file")
        empty = os.path.join(workspace, "empty-bundle.json")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"goal": "nothing", "skills": []}))
        fail_json(cli("tools_plan.py", ["workflow", "--input", empty]), code="empty_bundle")
        bad = os.path.join(workspace, "bad-bundle.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        fail_json(cli("tools_plan.py", ["workflow", "--input", bad]), code="bad_json")
        return True

    # -- schedule --------------------------------------------------------
    def schedule_daily():
        data = ok_json(cli("tools_schedule.py",
                           ["next", "--cadence", "daily", "--at", "02:30",
                            "--timezone", "Asia/Manila", "--count", "3"]))
        assert data["cron_expression"] == "30 2 * * *"
        assert len(data["next_runs"]) == 3
        assert all(run.endswith("+08:00") for run in data["next_runs"])
        return data["next_runs_local"]

    def schedule_dst_gap():
        # 2027-03-14 is beyond a 40-run window, so assert the mechanism directly
        # through the module API and also through the CLI (overlap case).
        import datetime as dt
        import zoneinfo
        import tools_schedule as S
        ny = zoneinfo.ZoneInfo("America/New_York")
        spec = {"cadence": "daily", "timezone": "America/New_York", "minute": 30,
                "hour": 2, "times": [(2, 30)], "weekdays": [], "dom": None,
                "date": None, "cron": None, "skip_weekends": False,
                "skip_dates": [], "every_hours": 1, "horizon_days": 400}
        runs, notes, _ = S._runs_for(spec, ny,
                                     dt.datetime(2027, 3, 13, 12, tzinfo=dt.timezone.utc), 2)
        gaps = [n for n in notes if n["kind"] == "dst_gap"]
        assert gaps and gaps[0]["action"] == "shift_forward", notes
        assert runs[0].isoformat() == "2027-03-14T03:30:00-04:00", runs[0].isoformat()
        fall = dict(spec, times=[(1, 30)], hour=1, minute=30)
        _, fall_notes, _ = S._runs_for(fall, ny,
                                       dt.datetime(2026, 10, 31, 12, tzinfo=dt.timezone.utc), 2)
        assert any(n["kind"] == "dst_overlap" for n in fall_notes), fall_notes
        data = ok_json(cli("tools_schedule.py",
                           ["next", "--cadence", "daily", "--at", "02:30",
                            "--timezone", "America/New_York", "--count", "40"]))
        assert len(data["next_runs"]) == 40
        assert gaps[0]["action"] == "shift_forward"
        return gaps[0]

    def schedule_cron_weekdays():
        import datetime as dt
        data = ok_json(cli("tools_schedule.py",
                           ["next", "--cadence", "cron", "--cron", "0 9 * * 1-5",
                            "--count", "5"]))
        for stamp in data["next_runs"]:
            assert dt.datetime.fromisoformat(stamp).weekday() < 5, stamp
        weekly = ok_json(cli("tools_schedule.py",
                             ["next", "--cadence", "weekly", "--weekdays",
                              "mon,tue,wed,thu,fri", "--at", "09:00"]))
        assert weekly["cron_expression"] == "0 9 * * 1,2,3,4,5", weekly["cron_expression"]
        return {"cron": weekly["cron_expression"]}

    def schedule_holiday_skip():
        data = ok_json(cli("tools_schedule.py",
                           ["next", "--cadence", "daily", "--at", "09:00",
                            "--skip-weekends", "--count", "5"]))
        import datetime as dt
        for stamp in data["next_runs"]:
            assert dt.datetime.fromisoformat(stamp).weekday() < 5, stamp
        return data["next_runs_local"]

    def schedule_plan_profile():
        data = ok_json(cli("tools_schedule.py",
                           ["plan", "--input", state["bundle"], "--profile",
                            "nightly-batch", "--name", "selftest-job"]))
        assert data["trigger"]["cadence"] == "daily"
        assert data["policy"]["lock_file"] == "selftest-job.lock"
        launchers = "\n".join(data["launchers"])
        for marker in ("crontab", "OnCalendar", "schtasks"):
            assert marker in launchers, "launcher export missing %s" % marker
        return {"launcher_lines": len(data["launchers"])}

    def schedule_errors():
        fail_json(cli("tools_schedule.py", ["next", "--cadence", "daily",
                                            "--at", "25:99"]), code="bad_time")
        fail_json(cli("tools_schedule.py", ["next", "--timezone", "Mars/Olympus"]),
                  code="bad_timezone")
        fail_json(cli("tools_schedule.py", ["next", "--cadence", "cron",
                                            "--cron", "bad"]), code="bad_cron")
        fail_json(cli("tools_schedule.py", ["next", "--profile", "does-not-exist"]),
                  code="unknown_profile")
        fail_json(cli("tools_schedule.py", ["next", "--cadence", "weekly",
                                            "--weekdays", "funday"]), code="bad_weekday")
        return True

    # -- scaffold --------------------------------------------------------
    def scaffold_build_and_verify():
        project = os.path.join(workspace, "project")
        preview = ok_json(cli("tools_scaffold.py", ["build", "--input", state["bundle"],
                                                    "--out-dir", project]))
        assert preview["mode"] == "dry-run"
        assert not os.path.exists(project), "dry-run must not create the project"
        applied = ok_json(cli("tools_scaffold.py", ["build", "--input", state["bundle"],
                                                    "--out-dir", project, "--apply"]))
        assert applied["mode"] == "apply"
        for name in ("automation_entry.py", "config.json", "README.md",
                     "requirements.txt", "fixtures/sample.csv", "run_history.json"):
            assert os.path.isfile(os.path.join(project, name)), "missing %s" % name
        entry = open(os.path.join(project, "automation_entry.py"), encoding="utf-8").read()
        assert "{{" not in entry, "unreplaced template placeholder"
        compile(entry, "automation_entry.py", "exec")
        verify = ok_json(cli("tools_scaffold.py", ["verify", "--project", project]))
        assert verify["ok"] is True, verify["stderr"]
        export = ok_json(cli("tools_scaffold.py", ["export", "--project", project]))
        for name in ("launch.sh", "launch.ps1", "systemd-user.service", "crontab.snippet"):
            assert os.path.isfile(os.path.join(project, name)), "missing %s" % name
        assert len(export["written"]) == 4
        state["project"] = project
        return project

    def scaffold_guards():
        project = state["project"]
        fail_json(cli("tools_scaffold.py", ["build", "--input", state["bundle"],
                                            "--out-dir", project, "--apply"]),
                  code="target_exists")
        forced = ok_json(cli("tools_scaffold.py", ["build", "--input", state["bundle"],
                                                   "--out-dir", project, "--apply",
                                                   "--force"]))
        assert forced["archive"] and os.path.isdir(forced["archive"])
        shutil.rmtree(forced["archive"], ignore_errors=True)
        return "archived-and-replaced"

    def scaffold_entry_runtime():
        project = state["project"]
        for name in ("inbox", "outbox", "quarantine", "logs"):
            os.makedirs(os.path.join(project, name), exist_ok=True)
        proc = subprocess.run([sys.executable, "automation_entry.py", "--config",
                               "config.json", "--dry-run"], cwd=project,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-400:]
        data = json.loads(proc.stdout)
        assert data["dry_run"] is True and data["ok"] is True
        audit = os.path.join(project, "logs", "audit.jsonl")
        assert os.path.isfile(audit), "audit log was not written"
        stages = subprocess.run([sys.executable, "automation_entry.py", "--stages"],
                                cwd=project, capture_output=True, text=True, timeout=120)
        assert stages.returncode == 0 and stages.stdout.strip().startswith("[")
        for name in ("inbox", "outbox", "quarantine", "logs"):
            os.makedirs(os.path.join(project, name), exist_ok=True)
        applied = subprocess.run([sys.executable, "automation_entry.py", "--config",
                                  "config.json"], cwd=project,
                                 capture_output=True, text=True, timeout=120)
        assert applied.returncode == 0, applied.stderr[-400:]
        assert json.loads(applied.stdout)["dry_run"] is False
        missing_cfg = subprocess.run([sys.executable, "automation_entry.py", "--config",
                                      "absent.json"], cwd=project,
                                     capture_output=True, text=True, timeout=120)
        assert missing_cfg.returncode == 2, "invalid input must exit 2"
        return {"run_id": data["run_id"], "audit_bytes": os.path.getsize(audit)}

    # -- governance ------------------------------------------------------
    def governance_gate():
        blocked = cli("tools_governance.py", ["gate", "--input", state["bundle"],
                                              "--owner", "ops"])
        data = ok_json(blocked, expect_exit=1)
        assert data["verdict"] == "blocked" and data["blocking"]
        cleared = ok_json(cli("tools_governance.py",
                              ["gate", "--input", state["bundle"], "--owner", "ops",
                               "--rollback", "trash/ restore", "--idempotent",
                               "--stop-control", "--project", state["project"]]))
        assert cleared["verdict"] == "cleared", cleared["blocking"]
        return {"blocked_gates": data["blocking"], "cleared": True}

    def governance_audit():
        path = os.path.join(workspace, "audit.jsonl")
        data = ok_json(cli("tools_governance.py",
                           ["audit", "--actor", "selftest", "--action", "process invoices",
                            "--result", "ok", "--input", "api_token=abc123secret",
                            "--input", "count=42", "--append-to", path]))
        record = data["record"]
        assert record["inputs"]["api_token"]["hashed"] == "sha256"
        assert "abc123secret" not in json.dumps(record), "raw secret leaked"
        assert record["inputs"]["count"] == "42"
        assert data["secret_scan"] == []
        assert os.path.isfile(path) and os.path.getsize(path) > 0
        return {"hashed": record["sensitive_inputs_hashed"]}

    def governance_secrets():
        project = state["project"]
        clean = ok_json(cli("tools_governance.py", ["secrets", "--project", project]))
        assert clean["verdict"] == "clean", clean["findings"]
        leaky = os.path.join(project, "leaky.py")
        try:
            for case in fixtures["secret_cases"]:
                with open(leaky, "w", encoding="utf-8") as fh:
                    fh.write(case["text"] + "\n")
                proc = cli("tools_governance.py", ["secrets", "--project", project])
                if case["expect"] is None:
                    data = ok_json(proc, expect_exit=0)
                    assert not data["findings"], "false positive on %r" % case["text"]
                else:
                    data = ok_json(proc, expect_exit=1)
                    found = {f["check"] for f in data["findings"]}
                    assert case["expect"] in found, "%r -> %s" % (case["text"], found)
        finally:
            if os.path.exists(leaky):
                os.unlink(leaky)
        return "clean+dirty cases"

    def governance_test_plan():
        data = ok_json(cli("tools_governance.py",
                           ["test-plan", "--input", state["bundle"],
                            "--project", state["project"]]))
        dims = {s["dimension"] for s in data["steps"]}
        expected = {"normal", "boundary", "failure", "repeat", "concurrency",
                    "recovery", "clean_state"}
        assert dims == expected, dims
        assert all(s["commands"] for s in data["steps"])
        return {"dimensions": len(dims)}

    def governance_errors():
        fail_json(cli("tools_governance.py", ["secrets", "--project",
                                              os.path.join(workspace, "nope")]),
                  code="missing_file")
        fail_json(cli("tools_governance.py", ["audit", "--action", "x",
                                              "--input", "novalue"]), code="bad_input")
        fail_json(cli("tools_governance.py", ["test-plan"]), code="missing_input")
        return True

    # -- cross-cutting ---------------------------------------------------
    def text_format_and_out():
        path = os.path.join(workspace, "list.txt")
        proc = cli("tools_catalog.py", ["list", "--format", "text", "--out", path])
        assert proc.returncode == 0
        assert "categories:" in proc.stdout, proc.stdout[:200]
        assert os.path.isfile(path) and "categories:" in open(path).read()
        return {"stdout_lines": len(proc.stdout.splitlines())}

    def config_override():
        cfg = os.path.join(workspace, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"ask_skill_limit": 4, "max_items": 6, "format": "json"}, fh)
        data = ok_json(cli("tools_catalog.py",
                           ["route", "--goal", "tidy up the downloads folder",
                            "--config", cfg]))
        assert data["limits"]["max_items"] == 6, data["limits"]
        default = ok_json(cli("tools_catalog.py",
                              ["route", "--goal", "tidy up the downloads folder"]))
        assert default["limits"]["max_items"] == 12, default["limits"]
        assert len(data["skills"]) <= 6
        return {"max_items": data["limits"]["max_items"],
                "selected": len(data["skills"])}

    def bundle_roundtrip():
        for args in (["workflow", "--input", state["bundle"]],
                     ["dryrun", "--input", state["bundle"]]):
            ok_json(cli("tools_plan.py", args))
        ok_json(cli("tools_schedule.py", ["plan", "--input", state["bundle"],
                                          "--cadence", "weekly", "--weekdays", "mon",
                                          "--at", "09:00"]))
        ok_json(cli("tools_governance.py", ["test-plan", "--input", state["bundle"]]))
        return "plan+schedule+governance accepted the bundle"

    return [
        ("help_commands", help_commands),
        ("catalog_list", catalog_list),
        ("catalog_list_category", catalog_list_category),
        ("catalog_search", catalog_search),
        ("catalog_info", catalog_info),
        ("catalog_check", catalog_check),
        ("parse_formats", parse_formats),
        ("parse_ignores_noise", parse_ignores_noise),
        ("parse_reproduces_packaged_catalog", parse_reproduces_packaged_catalog),
        ("route_recipe_cases", route_recipe_cases),
        ("route_explicit_skills", route_explicit_skills),
        ("route_high_risk_gate", route_high_risk_gate),
        ("route_errors", route_errors),
        ("catalog_writes_bundle", make_bundle),
        ("bundle_roundtrip", bundle_roundtrip),
        ("plan_workflow", plan_workflow),
        ("plan_estimate", plan_estimate),
        ("plan_estimate_changes_with_input", plan_estimate_changes_with_input),
        ("plan_dryrun", plan_dryrun),
        ("plan_errors", plan_errors),
        ("schedule_daily", schedule_daily),
        ("schedule_dst_gap", schedule_dst_gap),
        ("schedule_cron_weekdays", schedule_cron_weekdays),
        ("schedule_holiday_skip", schedule_holiday_skip),
        ("schedule_plan_profile", schedule_plan_profile),
        ("schedule_errors", schedule_errors),
        ("scaffold_build_and_verify", scaffold_build_and_verify),
        ("scaffold_guards", scaffold_guards),
        ("scaffold_entry_runtime", scaffold_entry_runtime),
        ("governance_gate", governance_gate),
        ("governance_audit", governance_audit),
        ("governance_secrets", governance_secrets),
        ("governance_test_plan", governance_test_plan),
        ("governance_errors", governance_errors),
        ("text_format_and_out", text_format_and_out),
        ("config_override", config_override),
    ]


def run_all(verbose=False):
    fixtures = C.load_json(C.asset(C.GOAL_FILE), "goal fixtures")
    workspace = tempfile.mkdtemp(prefix="foundry-selftest-")
    state = {}
    results = []
    try:
        for name, fn in build_checks(workspace, fixtures, state):
            try:
                detail = fn()
                results.append({"check": name, "ok": True, "detail": detail})
            except AssertionError as exc:
                results.append({"check": name, "ok": False, "detail": "assertion: %s" % exc})
            except Exception as exc:  # noqa: BLE001 - harness records any failure
                results.append({"check": name, "ok": False,
                                "detail": "%s: %s" % (type(exc).__name__, exc)})
            if verbose:
                last = results[-1]
                print("%-4s %s" % ("PASS" if last["ok"] else "FAIL", last["check"]),
                      flush=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    failed = [r for r in results if not r["ok"]]
    return {
        "ok": not failed,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "check_count": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
        "workspace": "temporary (removed after the run)",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Run end-to-end checks for every Automation Skill Foundry capability.",
        epilog="Examples:\n  python3 tools_selftest.py run\n"
               "  python3 tools_selftest.py run --verbose\n"
               "  python3 tools_selftest.py list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (C.TOOL_NAME, C.TOOL_VERSION))
    C.add_common_arguments(parser)
    common = argparse.ArgumentParser(add_help=False)
    C.add_common_arguments(common, suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("run", parents=[common], help="run all checks")
    p.add_argument("--verbose", action="store_true", help="print each check as it runs")
    p.set_defaults(func=lambda a, c: run_all(verbose=a.verbose))

    p = sub.add_parser("list", parents=[common], help="list check names")
    p.set_defaults(func=lambda a, c: {
        "ok": True, "tool": PROG,
        "checks": [name for name, _ in build_checks(tempfile.gettempdir(), {}, {})],
    })

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and "--version" not in argv):
        argv = ["run"] + argv
    args = parser.parse_args(argv)
    try:
        payload = args.func(args, C.resolve_settings(args))
    except C.FoundryError as exc:
        sys.stderr.write(C.stable_json({"ok": False, "tool": PROG,
                                        "error": {"code": exc.code,
                                                  "message": exc.message}}))
        return exc.exit_code
    C.emit(payload, getattr(args, "format", None) or "json",
           getattr(args, "out", None))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
