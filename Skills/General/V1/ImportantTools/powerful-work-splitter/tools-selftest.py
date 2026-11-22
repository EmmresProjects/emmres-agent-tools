#!/usr/bin/env python3
"""powerful-work-splitter :: regression suite.

Runs the shipped Skill Tool through its real command-line interface in a
disposable workspace and asserts every capability, including the failure and
recovery paths. No network access, no third-party packages, no writes outside
the temporary workspace.

Usage
-----
    python3 tools-selftest.py                 # full suite, summary only
    python3 tools-selftest.py --verbose       # print the failing command output
    python3 tools-selftest.py --keep          # keep the workspace for inspection
    python3 tools-selftest.py --suite smoke   # structural checks only (no CLI runs)

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
PRIMARY = HERE / "tools-work-splitter.py"
PY = sys.executable
sys.dont_write_bytecode = True

Result = Tuple[bool, str, str]


class Suite:
    def __init__(self, *, verbose: bool) -> None:
        self.verbose = verbose
        self.results: List[Result] = []

    def record(self, ok: bool, label: str, detail: str = "") -> None:
        self.results.append((ok, label, detail))
        if not ok:
            print(f"FAIL  {label}")
            if self.verbose and detail:
                print("      " + detail.replace("\n", "\n      "))

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.record(bool(ok), label, detail)
        return bool(ok)

    def run(self, args: Sequence[str], *, expect: int, cwd: Optional[Path] = None, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
        command = [PY, str(PRIMARY), *args]
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=stdin,
            cwd=str(cwd or HERE),
            timeout=180,
        )
        detail = (proc.stderr or proc.stdout or "")[-600:]
        self.check(proc.returncode == expect, f"{' '.join(args)}  (exit {proc.returncode}, expected {expect})", detail)
        return proc

    def summary(self) -> int:
        failed = [item for item in self.results if not item[0]]
        total = len(self.results)
        print(f"\n{total - len(failed)}/{total} checks passed")
        if failed:
            print("Failed checks:")
            for _, label, _ in failed:
                print(f"  - {label}")
        return 0 if not failed else 1


def structural_checks(suite: Suite) -> None:
    files = sorted(p.name for p in HERE.iterdir() if p.is_file())
    suite.check(all(name.startswith(("tools-", "knowledge-", "asset-")) for name in files),
                "every file uses a tools-/knowledge-/asset- prefix", str(files))
    suite.check(not (HERE / "SKILL.md").exists(), "Skill Tool folder contains no SKILL.md")
    subfolders = [p.name for p in HERE.iterdir() if p.is_dir() and p.name != "__pycache__"]
    suite.check(not subfolders, "Skill Tool folder contains no subfolders", str(subfolders))
    suite.check(sum(1 for name in files if name == "knowledge-readme.md") == 1,
                "exactly one knowledge-readme.md is present")
    suite.check(any(name.startswith("tools-") and name.endswith(".py") for name in files),
                "at least one runnable tools- entry point is present")

    expected_assets = [
        "asset-continue-intents.json",
        "asset-default-policy.json",
        "asset-example-request.md",
        "asset-gate-criteria.json",
        "asset-plan-schema.json",
        "asset-plan-template.json",
        "asset-requirement-ledger-template.csv",
    ]
    for asset in expected_assets:
        suite.check((HERE / asset).is_file(), f"asset present and case-sensitive: {asset}")
    for asset in expected_assets:
        if asset.endswith(".json"):
            try:
                json.loads((HERE / asset).read_text(encoding="utf-8"))
                suite.check(True, f"valid JSON: {asset}")
            except json.JSONDecodeError as exc:
                suite.check(False, f"valid JSON: {asset}", str(exc))

    for tool in ("tools-work-splitter.py", "tools-work-splitter-core.py", "tools-skill-validator.py", "tools-selftest.py"):
        suite.check((HERE / tool).is_file(), f"tool present: {tool}")
    for tool in ("tools-work-splitter.py", "tools-work-splitter-core.py", "tools-skill-validator.py"):
        try:
            compile((HERE / tool).read_text(encoding="utf-8"), tool, "exec")
            suite.check(True, f"compiles: {tool}")
        except SyntaxError as exc:
            suite.check(False, f"compiles: {tool}", str(exc))

    readme = HERE / "knowledge-readme.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        for asset in expected_assets:
            suite.check(asset in text, f"knowledge-readme.md references {asset}")
        for command in ("capabilities", "plan", "assess", "estimate", "negotiate", "decompose",
                        "scheduler", "gate", "validate-plan", "ledger", "files", "checkpoint",
                        "recover", "verify", "assert", "watch", "cleanup", "demo"):
            suite.check(command in text, f"knowledge-readme.md documents command '{command}'")


def cli_checks(suite: Suite, work: Path) -> None:
    request = HERE / "asset-example-request.md"
    request_copy = work / "request.md"
    shutil.copyfile(request, request_copy)
    (work / "quarterly-figures.xlsx").write_bytes(b"PK\x03\x04" + b"0" * 64)
    (work / "quarterly-figures-v2.xlsx").write_bytes(b"PK\x03\x04" + b"0" * 64)
    (work / "brand-notes.md").write_text("# brand notes\n", encoding="utf-8")

    # --- help, capabilities, version
    help_proc = suite.run(["help"], expect=0)
    for command in ("capabilities", "plan", "assess", "estimate", "negotiate", "decompose", "scheduler",
                    "gate", "validate-plan", "ledger", "files", "checkpoint", "recover", "verify",
                    "assert", "watch", "cleanup", "demo"):
        suite.check(command in help_proc.stdout, f"help lists '{command}'")
    caps = suite.run(["capabilities", "--format", "json"], expect=0)
    try:
        payload = json.loads(caps.stdout)
        suite.check(payload["data"]["capability_count"] == 12, "capabilities reports 12 capabilities",
                    caps.stdout[-300:])
        suite.check(all(cap["commands"] for cap in payload["data"]["capabilities"]),
                    "every capability declares reachable commands")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "capabilities JSON is well formed", str(exc))
    suite.run(["--version"], expect=0)

    # --- plan
    plan_file = work / "plan.json"
    suite.run(["plan", "--request", str(request_copy), "--count", "5", "--save-plan", str(plan_file)], expect=0)
    suite.check(plan_file.is_file(), "plan --save-plan writes the plan file")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    suite.check(plan["stage_count"] == 5, "user-selected stage count is honoured")
    suite.check(len(plan["requirements"]) >= 3, "requirements extracted from HTML request",
                str([r["text"] for r in plan["requirements"]]))
    suite.check(all(item["present"] for item in plan["files"]), "declared files detected as present in --root",
                str(plan["files"]))
    suite.check(plan["stages"][0]["role"] == "discovery" and plan["stages"][-1]["role"] == "assembly",
                "plan starts with discovery and ends with assembly")
    suite.check({r["id"] for r in plan["requirements"]} ==
                {rid for stage in plan["stages"] for rid in stage["requirements"]},
                "every requirement has exactly one owner stage")

    # deterministic output apart from timestamps
    first = suite.run(["plan", "--request", str(request_copy), "--count", "5", "--format", "json", "--root", str(work)], expect=0)
    second = suite.run(["plan", "--request", str(request_copy), "--count", "5", "--format", "json", "--root", str(work)], expect=0)
    try:
        a, b = json.loads(first.stdout)["data"], json.loads(second.stdout)["data"]
        for doc in (a, b):
            doc.pop("created_at", None)
            doc.pop("updated_at", None)
        suite.check(a == b, "identical input produces identical plan output")
    except json.JSONDecodeError as exc:
        suite.check(False, "plan JSON parses twice", str(exc))

    # input change changes the result
    low = suite.run(["estimate", "--request", str(request_copy), "--root", str(work), "--format", "json"], expect=0)
    high = suite.run(["estimate", "--request", str(request_copy), "--root", str(work),
                      "--uncertainty", "0.9", "--verification", "1.0", "--tool-calls", "25", "--format", "json"], expect=0)
    try:
        low_data, high_data = json.loads(low.stdout)["data"], json.loads(high.stdout)["data"]
        suite.check(high_data["recommended"] > low_data["recommended"],
                    "raising uncertainty, verification and tool calls raises the recommended count",
                    f"{low_data['recommended']} -> {high_data['recommended']}")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "estimate JSON parses", str(exc))

    # --- invalid and missing input
    suite.run(["plan"], expect=2)
    suite.run(["plan", "--request", str(work / "nope.md")], expect=3)
    suite.run(["plan", "--request", str(request_copy), "--count", "99"], expect=2)
    suite.run(["plan", "--request", str(request_copy), "--count", "0"], expect=2)
    suite.run(["plan", "--request", str(request_copy), "--uncertainty", "2"], expect=2)
    suite.run(["plan", "--request-text", "   "], expect=2)
    empty = work / "empty.md"
    empty.write_text("<!-- only a comment -->\n", encoding="utf-8")
    suite.run(["plan", "--request", str(empty)], expect=2)
    suite.run(["plan", "--request", str(request_copy), "--format", "json"], expect=0)  # default count path stays valid
    stdin_plan = suite.run(["plan", "--request", "-", "--format", "json", "--root", str(work)],
                           expect=0, stdin="# Goal\n\nWrite a one page brief with a chart and a table.\n")
    try:
        suite.check(json.loads(stdin_plan.stdout)["data"]["stage_count"] >= 1, "stdin request is accepted")
    except json.JSONDecodeError as exc:
        suite.check(False, "stdin request JSON", str(exc))
    suite.run(["plan", "--request", str(request_copy), "--format", "json", "--root", str(work / "missing-root")], expect=3)

    # explicit requirements and assumptions
    explicit = suite.run(["plan", "--request-text", "Prepare the launch pack.", "--requirement", "Write the launch memo",
                          "--requirement", "Verify every figure", "--assume", "The audience is the board",
                          "--count", "3", "--format", "json", "--root", str(work)], expect=0)
    try:
        data = json.loads(explicit.stdout)["data"]
        suite.check(len(data["requirements"]) == 2, "explicit --requirement replaces inferred requirements",
                    str(data["requirements"]))
        suite.check(any("audience" in a["text"] for a in data["assumptions"]), "assumption recorded in the plan")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "explicit requirement JSON", str(exc))

    # --- assess
    single = suite.run(["assess", "--request-text", "Write one short paragraph.", "--root", str(work), "--format", "json"], expect=0)
    try:
        suite.check(json.loads(single.stdout)["data"]["decision"] == "single_response",
                    "a tiny request is judged feasible in one response")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "assess single-response JSON", str(exc))
    tiny = suite.run(["plan", "--request-text", "Write one short paragraph.", "--root", str(work), "--format", "json"], expect=0)
    try:
        tiny_plan = json.loads(tiny.stdout)["data"]
        suite.check(tiny_plan["stage_count"] == 1 and tiny_plan["stages"][0]["role"] == "assembly",
                    "a feasible request produces a single-stage plan instead of manufactured stages")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "single-stage plan JSON", str(exc))
    staged = suite.run(["assess", "--request", str(request_copy), "--root", str(work), "--format", "json"], expect=0)
    try:
        suite.check(json.loads(staged.stdout)["data"]["decision"] == "staged", "a large request requires staged work")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "assess staged JSON", str(exc))
    suite.run(["estimate", "--request", str(request_copy), "--root", str(work)], expect=0)

    # --- negotiate: expansion, compression, hold, bad direction
    expanded = suite.run(["negotiate", "--plan", str(plan_file), "--count", "9", "--save-plan",
                          str(work / "plan9.json"), "--format", "json"], expect=0)
    try:
        expanded_plan = json.loads(expanded.stdout)["data"]["plan"]
        suite.check(expanded_plan["stage_count"] == 9, "expansion reaches the requested stage count")
        suite.check({r["id"] for r in expanded_plan["requirements"]} ==
                    {rid for stage in expanded_plan["stages"] for rid in stage["requirements"]},
                    "expansion keeps exactly one owner per requirement")
        suite.check(any(stage["exit_gate"] == "verification" for stage in expanded_plan["stages"]),
                    "expansion keeps a verification gate")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "negotiate expand JSON", str(exc))
    compressed = suite.run(["negotiate", "--plan", str(work / "plan9.json"), "--count", "4", "--format", "json"], expect=0)
    try:
        compressed_plan = json.loads(compressed.stdout)["data"]["plan"]
        suite.check(compressed_plan["stage_count"] == 4, "compression reaches the requested stage count")
        suite.check(any(stage["exit_gate"] == "verification" for stage in compressed_plan["stages"]),
                    "compression protects the verification gate")
        suite.check(any("trade-off" in item.lower() or "checkpoint" in item.lower()
                        for item in json.loads(compressed.stdout)["data"]["tradeoffs"]),
                    "compression discloses trade-offs")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "negotiate compress JSON", str(exc))
    suite.run(["negotiate", "--plan", str(plan_file), "--count", "5"], expect=0)
    suite.run(["negotiate", "--plan", str(plan_file), "--count", "9", "--direction", "compress"], expect=2)
    suite.run(["negotiate", "--plan", str(plan_file), "--count", "3", "--direction", "expand"], expect=2)
    suite.run(["negotiate", "--plan", str(plan_file), "--count", "99"], expect=2)
    suite.run(["negotiate", "--plan", str(work / "plan.json"), "--count", "2"], expect=4)

    # --- decompose and scheduler
    graph = suite.run(["decompose", "--plan", str(plan_file), "--parallel", "--format", "json"], expect=0)
    try:
        graph_data = json.loads(graph.stdout)["data"]
        suite.check(graph_data["node_count"] == len(plan["requirements"]), "decompose covers every requirement")
        suite.check(len(graph_data["merge_contracts"]) == plan["stage_count"],
                    "parallel mode emits one merge contract per stage")
        flat = [node for wave in graph_data["waves"] for node in wave]
        suite.check(sorted(flat) == sorted(r["id"] for r in plan["requirements"]),
                    "every work package appears in exactly one wave")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "decompose JSON", str(exc))
    schedule = suite.run(["scheduler", "--plan", str(plan_file), "--format", "json"], expect=0)
    try:
        schedule_data = json.loads(schedule.stdout)["data"]
        suite.check(len(schedule_data["critical_path"]) >= 2, "critical path spans multiple stages")
        suite.check(schedule_data["committed_total_units"] >= schedule_data["total_estimate_units"],
                    "committed work includes the uncertainty buffer")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "scheduler JSON", str(exc))

    # --- gates
    suite.run(["gate", "--list"], expect=0)
    evidence_ok = work / "evidence-ok.json"
    evidence_ok.write_text(json.dumps({
        "D1": {"passed": True, "evidence": "goal quoted back"},
        "D2": {"passed": True, "evidence": "ledger rows R1-R5"},
        "D3": {"passed": True, "evidence": "constraints separated"},
        "D4": {"passed": True, "evidence": "assumption A1 recorded"},
    }), encoding="utf-8")
    gate_pass = suite.run(["gate", "--gate", "discovery", "--evidence", str(evidence_ok), "--require", "--format", "json"], expect=0)
    try:
        suite.check(json.loads(gate_pass.stdout)["data"]["status"] == "pass", "complete evidence passes the gate")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "gate pass JSON", str(exc))
    evidence_bad = work / "evidence-bad.json"
    evidence_bad.write_text(json.dumps({"D1": {"passed": False}, "D2": {"passed": True}}), encoding="utf-8")
    blocked = suite.run(["gate", "--gate", "discovery", "--evidence", str(evidence_bad), "--require", "--format", "json"], expect=4)
    try:
        payload = json.loads(blocked.stdout)["data"]
        suite.check(payload["status"] in ("blocked", "repair"), "incomplete evidence blocks the gate")
        suite.check(bool(payload["missing_evidence"]), "gate names the criteria lacking evidence")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "gate blocked JSON", str(exc))
    suite.run(["gate", "--gate", "discovery"], expect=2)
    suite.run(["gate", "--gate", "no-such-gate", "--evidence", str(evidence_ok)], expect=2)

    # --- validate-plan and ledger
    suite.run(["validate-plan", "--plan", str(plan_file)], expect=0)
    suite.run(["validate-plan", "--plan", str(HERE / "asset-plan-template.json"),
               "--schema", str(HERE / "asset-plan-schema.json")], expect=0)
    broken_plan = work / "broken-plan.json"
    broken_plan.write_text(json.dumps({"goal": "x", "stages": [], "requirements": []}), encoding="utf-8")
    suite.run(["validate-plan", "--plan", str(broken_plan)], expect=2)
    ledger_csv = work / "ledger.csv"
    suite.run(["ledger", "--plan", str(plan_file), "--out", str(ledger_csv)], expect=0)
    suite.check(ledger_csv.is_file() and "owner_stage" in ledger_csv.read_text(encoding="utf-8"),
                "ledger CSV is written with the expected header")
    suite.run(["ledger", "--plan", str(plan_file), "--ledger-format", "markdown"], expect=0)

    # --- files
    inventory = suite.run(["files", "--root", str(work), "--declared", "quarterly-figures.xlsx",
                           "--declared", "missing-report.pdf", "--required", "missing-report.pdf",
                           "--format", "json"], expect=0)
    try:
        payload = json.loads(inventory.stdout)["data"]
        suite.check("missing-report.pdf" in payload["missing_required"], "missing required files are named")
        suite.check(payload["missing_required"], "re-upload is requested instead of inventing content")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "files JSON", str(exc))
    suite.run(["files", "--root", str(work / "absent")], expect=3)

    # --- checkpoint lifecycle
    state = work / "state.json"
    suite.run(["checkpoint", "start", "--plan", str(plan_file), "--state", str(state), "--root", str(work)], expect=0)
    suite.check(state.is_file(), "checkpoint start writes state")
    suite.run(["checkpoint", "start", "--plan", str(plan_file), "--state", str(state)], expect=2)
    suite.run(["checkpoint", "status", "--state", str(state)], expect=0)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue"], expect=2)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "ok"], expect=2)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue", "--dry-run"], expect=0)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "maybe", "--dry-run"], expect=2)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "actually use 6 stages"], expect=2)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "stop"], expect=7)
    artifact = work / "s1-artifact.md"
    artifact.write_text("stage one output\n", encoding="utf-8")
    advanced = suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue",
                          "--verified", "artifact reopened", "--output", "s1-artifact.md", "--root", str(work),
                          "--format", "json"], expect=0)
    try:
        payload = json.loads(advanced.stdout)["data"]["state"]
        suite.check(payload["current_stage_index"] == 1, "continuation advances exactly one stage")
        suite.check(payload["stages"][0]["status"] == "complete", "closed stage is recorded complete")
        suite.check(payload["paused"] is True, "the plan pauses at the next safe boundary")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "checkpoint continue JSON", str(exc))
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue",
               "--verified", "checked", "--output", "ghost.md", "--root", str(work)], expect=4)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue",
               "--verified", "checked", "--output", "ghost.md", "--root", str(work), "--force"], expect=0)
    suite.run(["checkpoint", "block", "--state", str(state), "--reason", "source workbook unreadable"], expect=4)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "continue", "--verified", "x"], expect=4)
    suite.run(["recover", "--state", str(state), "--stage", "S3", "--cause", "unreadable workbook", "--action", "retry"], expect=0)
    suite.run(["checkpoint", "continue", "--state", str(state), "--reply", "proceed", "--verified", "retried with v2 file",
               "--root", str(work)], expect=0)
    suite.run(["checkpoint", "set-count", "--state", str(state), "--count", "8"], expect=0)
    suite.run(["checkpoint", "set-count", "--state", str(state), "--count", "2"], expect=4)
    suite.run(["checkpoint", "reopen", "--state", str(state), "--stage", "S1", "--reason", "regression"], expect=0)
    reopened = suite.run(["checkpoint", "status", "--state", str(state), "--format", "json"], expect=0)
    try:
        payload = json.loads(reopened.stdout)["data"]["state"]
        suite.check(payload["current_stage_index"] == 0, "reopen rewinds the checkpoint to the repaired stage")
        suite.check(payload["completed_count"] == 0, "reopen resets completion accounting")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "checkpoint reopen JSON", str(exc))
    suite.run(["checkpoint", "bogus", "--state", str(state)], expect=2)
    suite.run(["checkpoint", "continue", "--state", str(work / "no-state.json"), "--reply", "continue"], expect=3)

    # repair attempts are bounded
    for attempt in range(3):
        suite.run(["recover", "--state", str(state), "--stage", "S4", "--cause", f"attempt {attempt}", "--action", "retry"], expect=0)
    suite.run(["recover", "--state", str(state), "--stage", "S4", "--cause", "one too many", "--action", "retry"], expect=7)

    # --- verify
    suite.run(["verify", "--plan", str(plan_file), "--root", str(work)], expect=7)
    forced = suite.run(["verify", "--plan", str(plan_file), "--state", str(state), "--root", str(work),
                        "--format", "json"], expect=4)
    try:
        payload = json.loads(forced.stdout)["data"]
        artifacts = next(check for check in payload["checks"] if check["check"] == "artifacts")
        suite.check(any("ghost.md" in problem for problem in artifacts["problems"]),
                    "verification detects a declared output that does not exist")
    except (json.JSONDecodeError, KeyError, StopIteration) as exc:
        suite.check(False, "verify detects missing declared output", str(exc))
    demo_dir = work / "verifyroom"
    suite.run(["demo", "--workdir", str(demo_dir)], expect=0)
    final = suite.run(["verify", "--plan", str(demo_dir / "plan.json"), "--state", str(demo_dir / "state.json"),
                       "--root", str(demo_dir), "--format", "json"], expect=0)
    try:
        payload = json.loads(final.stdout)["data"]
        suite.check(payload["status"] == "pass", "a complete workspace passes verification")
        suite.check({check["check"] for check in payload["checks"]} ==
                    {"coverage", "gates", "files", "artifacts", "delivery"},
                    "verification runs all five gate groups")
    except (json.JSONDecodeError, KeyError) as exc:
        suite.check(False, "verify JSON", str(exc))
    missing_artifact_plan = json.loads((demo_dir / "plan.json").read_text(encoding="utf-8"))
    missing_artifact_plan["stages"][-1]["outputs"] = ["never-created.pdf"]
    (work / "plan-missing-output.json").write_text(json.dumps(missing_artifact_plan), encoding="utf-8")
    suite.run(["verify", "--plan", str(work / "plan-missing-output.json"), "--state", str(demo_dir / "state.json"),
               "--root", str(demo_dir)], expect=4)

    # --- assertions
    suite.run(["assert", "--kind", "file", "--path", str(work / "quarterly-figures.xlsx"), "--signature"], expect=0)
    suite.run(["assert", "--kind", "file", "--path", str(work / "brand-notes.md"), "--signature"], expect=4)
    suite.run(["assert", "--kind", "file", "--path", str(work / "quarterly-figures.xlsx"), "--signature",
               "--expected-ext", "xlsx"], expect=0)
    suite.run(["assert", "--kind", "file", "--path", str(work / "ghost.bin")], expect=3)
    suite.run(["assert", "--kind", "csv", "--path", str(ledger_csv), "--require-header", "id",
               "--require-header", "owner_stage", "--expect-rows", str(len(plan["requirements"]))], expect=0)
    suite.run(["assert", "--kind", "csv", "--path", str(ledger_csv), "--require-header", "nope"], expect=4)
    suite.run(["assert", "--kind", "json", "--path", str(plan_file), "--schema", str(HERE / "asset-plan-schema.json")], expect=0)
    suite.run(["assert", "--kind", "json", "--path", str(plan_file), "--require-key", "nonexistent"], expect=4)
    refs_bad = work / "refs-bad.md"
    refs_bad.write_text("See `plan.json` and `ghost-reference.csv`.\n", encoding="utf-8")
    suite.run(["assert", "--kind", "reference", "--path", str(refs_bad), "--root", str(work)], expect=4)
    refs_ok = work / "refs-ok.md"
    refs_ok.write_text("See `plan.json` and [notes](./brand-notes.md).\n", encoding="utf-8")
    suite.run(["assert", "--kind", "reference", "--path", str(refs_ok), "--root", str(work)], expect=0)
    suite.run(["assert", "--kind", "manifest", "--path", str(work)], expect=0)

    # --- watch and cleanup
    suite.run(["watch", "--root", str(work), "--settle", "0.2", "--timeout", "15"], expect=0)
    suite.run(["watch", "--root", str(work), "--settle", "5", "--timeout", "1"], expect=7)
    (work / "scratch.tmp").write_text("temp\n", encoding="utf-8")
    suite.run(["cleanup", "--root", str(work)], expect=5)
    suite.run(["cleanup", "--root", str(work), "--approve", "publish"], expect=5)
    suite.check((work / "scratch.tmp").exists(), "cleanup removes nothing without the cleanup approval token")
    suite.run(["cleanup", "--root", str(work), "--approve", "cleanup"], expect=0)
    suite.check(not (work / "scratch.tmp").exists(), "approved cleanup removes the temp file")

    # --- relative paths from a different working directory
    rel_root = work / "relroot"
    rel_root.mkdir(exist_ok=True)
    (rel_root / "chatty.log").write_text("noise\n", encoding="utf-8")
    suite.run(["cleanup", "--root", "relroot", "--approve", "cleanup"], expect=0, cwd=work)
    suite.check(not (rel_root / "chatty.log").exists(), "cleanup succeeds with a relative --root")
    suite.run(["demo", "--workdir", "reldemo", "--stages", "3"], expect=0, cwd=work)
    suite.check((work / "reldemo" / "plan.json").is_file(), "demo succeeds with a relative --workdir")

    # --- demo
    suite.run(["demo", "--stages", "3"], expect=0)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Regression suite for the powerful-work-splitter Skill Tool.")
    parser.add_argument("--verbose", action="store_true", help="print failing command output")
    parser.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    parser.add_argument("--suite", choices=["all", "smoke"], default="all",
                        help="smoke runs structural checks only")
    args = parser.parse_args(argv)

    suite = Suite(verbose=args.verbose)
    structural_checks(suite)
    if args.suite == "all":
        if not PRIMARY.is_file():
            suite.check(False, "primary entry point exists", str(PRIMARY))
        else:
            work = Path(tempfile.mkdtemp(prefix="pws-selftest-"))
            try:
                cli_checks(suite, work)
            finally:
                if args.keep:
                    print(f"Workspace kept: {work.as_posix()}")
                else:
                    shutil.rmtree(work, ignore_errors=True)
    return suite.summary()


if __name__ == "__main__":
    sys.exit(main())
