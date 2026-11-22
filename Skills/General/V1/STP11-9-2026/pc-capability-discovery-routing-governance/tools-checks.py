#!/usr/bin/env python3
"""Self-verification suite for the capability-discovery-routing-governance skill tool.

Runs the primary entry point through its real command interface using only
relative paths from the future skill root, checks package structure, validates
contract fields, exercises error and approval paths, and confirms one recovery
round-trip. Exits 0 only when every check passes.

Usage (from the skill root, POSIX sh):
  python3 capability-discovery-routing-governance/tools-checks.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL_DIR_NAME = "capability-discovery-routing-governance"
SKILL_DIR = Path(__file__).resolve().parent
ROOT = SKILL_DIR.parent
ENTRY = f"{SKILL_DIR_NAME}/tools-capability-router.py"
CHECK_TIMEOUT = 120

CONTRACT_FIELDS = [
    "capability_id", "status", "detected_command", "detected_version",
    "host_type", "architecture", "requirements", "test_command",
    "exit_code", "evidence_path", "recommended_action",
]

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def run(args, timeout=CHECK_TIMEOUT):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, ENTRY] + list(args),
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_json(args, timeout=CHECK_TIMEOUT):
    code, out, err = run(args, timeout)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout is not JSON for {args}: {exc}; stdout={out[:300]!r} stderr={err[:300]!r}")
    return code, data, err


def expect(cond, message):
    if not cond:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@check("structure: every packaged file uses a tools-/knowledge-/asset- prefix")
def _structure_prefixes():
    for entry in sorted(SKILL_DIR.iterdir()):
        name = entry.name
        expect(name.startswith("tools-") or name.startswith("knowledge-")
               or name.startswith("asset-"),
               f"unprefixed or wrongly prefixed entry: {name}")


@check("structure: no subdirectories inside the skill tool folder")
def _structure_no_subdirs():
    dirs = [e.name for e in SKILL_DIR.iterdir() if e.is_dir()]
    expect(not dirs, f"unexpected subdirectories: {dirs}")


@check("structure: no SKILL.md inside the skill tool folder")
def _structure_no_skill_md():
    expect(not (SKILL_DIR / "SKILL.md").exists(),
           "SKILL.md must not exist inside the skill tool folder")


@check("structure: exactly one knowledge file")
def _structure_one_knowledge():
    knowledge = [e.name for e in SKILL_DIR.iterdir()
                 if e.name.startswith("knowledge-")]
    expect(len(knowledge) == 1, f"expected 1 knowledge file, found {knowledge}")


@check("structure: primary entry point and shared core exist")
def _structure_entry():
    expect((SKILL_DIR / "tools-capability-router.py").is_file(),
           "tools-capability-router.py missing")
    expect((SKILL_DIR / "tools-governance-core.py").is_file(),
           "tools-governance-core.py missing")
    expect((SKILL_DIR / "tools-checks.py").is_file(), "tools-checks.py missing")


@check("knowledge: every referenced skill-root path exists (case-sensitive)")
def _knowledge_references():
    knowledge = next((e for e in SKILL_DIR.iterdir()
                      if e.name.startswith("knowledge-")), None)
    expect(knowledge is not None, "knowledge file missing")
    text = knowledge.read_text(encoding="utf-8")
    refs = sorted(set(re.findall(
        rf"{SKILL_DIR_NAME}/[A-Za-z0-9._-]+", text)))
    expect(refs, "knowledge file references no skill-root paths")
    missing = [r for r in refs if not (ROOT / r).exists()]
    expect(not missing, f"referenced paths do not exist: {missing}")


@check("knowledge: no machine-specific absolute paths are baked in")
def _knowledge_no_abs_paths():
    knowledge = next(e for e in SKILL_DIR.iterdir()
                     if e.name.startswith("knowledge-"))
    text = knowledge.read_text(encoding="utf-8")
    for needle in ("/home/", "C:\\", "/Users/"):
        expect(needle not in text, f"machine-specific path in knowledge: {needle}")


# ---------------------------------------------------------------------------
# Help and capability listing
# ---------------------------------------------------------------------------

@check("help: built-in help exits 0 and shows usage")
def _help():
    code, out, _ = run(["help"])
    expect(code == 0, f"exit {code}")
    expect("USAGE" in out and "selftest" in out, "help text incomplete")


@check("help: no arguments prints usage and exits 2")
def _no_args():
    code, out, _ = run([])
    expect(code == 2, f"exit {code}")
    expect("USAGE" in out, "usage not printed")


@check("capabilities: full catalog lists 30 categories and 400 skills")
def _capabilities_summary():
    code, data, _ = run_json(["capabilities", "--json"])
    expect(code == 0 and data["ok"], "not ok")
    res = data["result"]
    expect(res["category_count"] == 30, f"categories={res['category_count']}")
    expect(res["skill_count"] == 400, f"skills={res['skill_count']}")
    expect(len(res["categories"]) == 30, "category list length")


@check("capabilities: category selector by number returns 14 PE skills")
def _capabilities_category_number():
    code, data, _ = run_json(["capabilities", "--category", "8", "--json"])
    res = data["result"]
    expect(res["skill_count"] == 14, f"count={res['skill_count']}")
    expect(res["skills"][0]["id"] == "c08s01", f"first={res['skills'][0]['id']}")


@check("capabilities: category selector by name substring works")
def _capabilities_category_name():
    code, data, _ = run_json(["capabilities", "--category", "PE, COFF", "--json"])
    expect(data["result"]["skill_count"] == 14, "name selector mismatch")


@check("capabilities: search returns PE Header Inspector first")
def _capabilities_search():
    code, data, _ = run_json(["capabilities", "--search", "PE Header", "--json"])
    matches = data["result"]["matches"]
    expect(matches and matches[0]["id"] == "c08s01",
           f"top={matches[0]['id'] if matches else None}")


@check("capabilities: invalid category selector exits 2")
def _capabilities_bad_category():
    code, data, _ = run_json(["capabilities", "--category", "no-such-category", "--json"])
    expect(code == 2 and not data["ok"], f"exit={code}")


@check("capabilities: Markdown list input is parsed with category grouping")
def _capabilities_from_markdown(tmp):
    path = tmp / "skills.md"
    path.write_text(
        "## Custom Category\n"
        "1. Skill Name: Widget Inspector\nDescription: Reports widget headers.\n"
        "2. Skill Name: Widget Repairer\nDescription: Applies reversible fixes.\n",
        encoding="utf-8")
    code, data, _ = run_json(["capabilities", "--from", str(path), "--json"])
    res = data["result"]
    expect(code == 0 and res["entry_count"] == 2, f"entries={res.get('entry_count')}")
    expect(res["format"] == "markdown", f"format={res['format']}")
    expect(res["entries"][0]["category"] == "Custom Category",
           f"category={res['entries'][0].get('category')}")


@check("capabilities: copied HTML input is stripped and parsed")
def _capabilities_from_html(tmp):
    path = tmp / "skills.html"
    path.write_text(
        "<div><h3>Copied List</h3><ul>"
        "<li><b>Alpha Parser</b> — Parses alpha records</li>"
        "<li><b>Beta Checker</b>: Checks beta invariants</li>"
        "</ul></div>", encoding="utf-8")
    code, data, _ = run_json(["capabilities", "--from", str(path), "--json"])
    res = data["result"]
    expect(res["format"] == "html", f"format={res['format']}")
    names = [e["name"] for e in res["entries"]]
    expect(names == ["Alpha Parser", "Beta Checker"], f"names={names}")
    expect(res["entries"][0]["description"] == "Parses alpha records",
           "description not extracted")


@check("capabilities: single plain-text capability is accepted")
def _capabilities_from_single(tmp):
    path = tmp / "single.txt"
    path.write_text("PC Capability Inventory", encoding="utf-8")
    code, data, _ = run_json(["capabilities", "--from", str(path), "--json"])
    expect(data["result"]["entry_count"] == 1, "single entry not parsed")
    expect(data["result"]["entries"][0]["name"] == "PC Capability Inventory",
           "name mismatch")


@check("capabilities: missing input file exits 2")
def _capabilities_from_missing():
    code, data, _ = run_json(["capabilities", "--from", "/no/such/file.txt", "--json"])
    expect(code == 2 and not data["ok"], f"exit={code}")


@check("capabilities: --out writes parseable JSON to a configurable path")
def _capabilities_out(tmp):
    out = tmp / "caps-out.json"
    code, data, _ = run_json(["capabilities", "--json", "--out", str(out)])
    expect(out.is_file(), "--out file not written")
    written = json.loads(out.read_text(encoding="utf-8"))
    expect(written["skill_count"] == 400, "written file content mismatch")


# ---------------------------------------------------------------------------
# Inventory and self-test
# ---------------------------------------------------------------------------

@check("inventory: 40 probe rows carry every contract field")
def _inventory_contract():
    code, data, _ = run_json(["inventory"])
    res = data["result"]
    expect(res["probe_count"] == 40, f"rows={res['probe_count']}")
    for row in res["rows"]:
        for field in CONTRACT_FIELDS:
            expect(field in row, f"row {row.get('capability_id')} missing {field}")
    py = next(r for r in res["rows"] if r["capability_id"] == "tool:python3")
    expect(py["status"] == "FOUND", f"python3 status={py['status']}")
    expect(py["detected_version"], "python3 version not detected")


@check("inventory: --out path is recorded in evidence_path")
def _inventory_out(tmp):
    out = tmp / "inv.json"
    code, data, _ = run_json(["inventory", "--out", str(out)])
    expect(out.is_file(), "inventory output not written")
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"]
    expect(rows[0]["evidence_path"] == str(out), "evidence_path mismatch")


@check("selftest: governance capability is FOUND on this host")
def _selftest_found():
    code, data, _ = run_json(["selftest", "--skill", "c01s01"])
    row = data["result"]["rows"][0]
    expect(row["status"] == "FOUND", f"status={row['status']}")
    expect(row["capability_id"] == "c01s01", "id mismatch")


@check("selftest: Windows-only capability reports WINDOWS HOST REQUIRED")
def _selftest_windows():
    code, data, _ = run_json(["selftest", "--skill", "c05s01"])
    row = data["result"]["rows"][0]
    expect(row["status"] == "WINDOWS HOST REQUIRED", f"status={row['status']}")
    expect("windows" in row["recommended_action"].lower(),
           "recommended action should mention a Windows worker")


@check("selftest: license-gated capability reports LICENSE REQUIRED")
def _selftest_license():
    code, data, _ = run_json(["selftest", "--skill", "c13s02"])
    row = data["result"]["rows"][0]
    expect(row["status"] == "LICENSE REQUIRED", f"status={row['status']}")


@check("selftest: unknown skill id exits 2 with a useful error")
def _selftest_unknown():
    code, data, _ = run_json(["selftest", "--skill", "c99s99"])
    expect(code == 2 and not data["ok"], f"exit={code}")
    expect("unknown skill id" in data["errors"][0]["message"], "message quality")


@check("selftest: category scope with limit returns exactly the limit")
def _selftest_limit():
    code, data, _ = run_json(["selftest", "--category", "29", "--limit", "3"])
    res = data["result"]
    expect(len(res["rows"]) == 3, f"rows={len(res['rows'])}")
    expect(all(r["status"] == "FOUND" for r in res["rows"]),
           f"statuses={[r['status'] for r in res['rows']]}")


@check("selftest: --assume changes the evaluated host and result")
def _selftest_assume():
    code, data, _ = run_json(["selftest", "--skill", "c01s01", "--assume", "windows"])
    row = data["result"]["rows"][0]
    expect(row["host_type"] == "windows", f"host={row['host_type']}")
    expect(row["status"] != "FOUND",
           "linux-lane capability must not be FOUND on an assumed windows host")


@check("selftest: --out changes rows and records evidence_path")
def _selftest_out(tmp):
    out = tmp / "st.json"
    code, data, _ = run_json(["selftest", "--skill", "c01s01", "--out", str(out)])
    row = json.loads(out.read_text(encoding="utf-8"))["rows"][0]
    expect(row["evidence_path"] == str(out), "evidence_path not set to --out")
    expect(data["result"]["rows"][0]["evidence_path"] == str(out),
           "in-memory row evidence_path mismatch")


@check("selftest: --fail-on-not-found exits 5 when readiness fails")
def _selftest_fail_flag():
    code, data, _ = run_json(["selftest", "--skill", "c05s01",
                              "--fail-on-not-found"])
    expect(code == 5, f"exit={code}")
    expect(data["result"]["readiness"] == "fail", "readiness should be fail")


@check("selftest: full catalog run yields 400 rows and a status summary")
def _selftest_full():
    code, data, _ = run_json(["selftest"])
    res = data["result"]
    expect(len(res["rows"]) == 400, f"rows={len(res['rows'])}")
    expect(res["summary"]["total"] == 400, "summary total mismatch")
    expect(sum(res["summary"]["by_status"].values()) == 400,
           "status counts do not sum to 400")
    expect(res["contract_fields"] == CONTRACT_FIELDS, "contract field list mismatch")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@check("route: runnable capability routes to the local host")
def _route_local():
    code, data, _ = run_json(["route", "--skill", "c01s01"])
    routing = data["result"]["routings"][0]["routing"]
    expect(routing["runnable_now"] is True, "should be runnable")
    expect(routing["worker_recommendation"] == ["local-linux"]
           or routing["worker_recommendation"][0].startswith("local-"),
           f"workers={routing['worker_recommendation']}")


@check("route: Windows capability routes to a windows worker")
def _route_windows():
    code, data, _ = run_json(["route", "--skill", "c11s01"])
    routing = data["result"]["routings"][0]["routing"]
    expect(routing["runnable_now"] is False, "must not be runnable locally")
    expect("windows-worker" in routing["worker_recommendation"],
           f"workers={routing['worker_recommendation']}")


@check("route: job text routes PE inspection to c08s01")
def _route_job():
    code, data, _ = run_json(["route", "--job", "inspect PE headers"])
    matches = data["result"]["matches"]
    expect(matches and matches[0]["skill"]["id"] == "c08s01",
           f"top={matches[0]['skill']['id'] if matches else None}")


@check("route: changing the job text changes the routing result")
def _route_job_changes():
    _, first, _ = run_json(["route", "--job", "inspect PE headers"])
    _, second, _ = run_json(["route", "--job", "archive extraction safety"])
    top1 = first["result"]["matches"][0]["skill"]["id"]
    top2 = second["result"]["matches"][0]["skill"]["id"]
    expect(top1 != top2, f"both routed to {top1}")


@check("route: requires exactly one of --skill or --job")
def _route_args():
    code, data, _ = run_json(["route"])
    expect(code == 2, f"no-args exit={code}")
    code, data, _ = run_json(["route", "--skill", "c01s01", "--job", "x"])
    expect(code == 2, f"both-args exit={code}")


# ---------------------------------------------------------------------------
# Gate, risk, boundary
# ---------------------------------------------------------------------------

@check("gate: affirmed lawful action is allowed")
def _gate_allow():
    code, data, _ = run_json([
        "gate", "--action", "read and hash the installer",
        "--authorized", "yes", "--owns-artifact", "yes",
        "--license-ok", "yes", "--privacy-ok", "yes"])
    expect(code == 0 and data["result"]["decision"] == "ALLOW",
           f"exit={code} decision={data.get('result', {}).get('decision')}")


@check("gate: missing declarations are incomplete and exit 4")
def _gate_incomplete():
    code, data, _ = run_json(["gate", "--action", "read and hash the installer"])
    expect(code == 4, f"exit={code}")
    expect(data["errors"][0]["details"]["decision"] == "INCOMPLETE", "decision")


@check("gate: prohibited request is denied even with all affirmations")
def _gate_deny_boundary():
    code, data, _ = run_json([
        "gate", "--action", "bypass drm on this ebook",
        "--authorized", "yes", "--owns-artifact", "yes",
        "--license-ok", "yes", "--privacy-ok", "yes"])
    expect(code == 4, f"exit={code}")
    expect(data["errors"][0]["details"]["decision"] == "DENY", "decision")


@check("gate: refused declaration denies the action")
def _gate_deny_refusal():
    code, data, _ = run_json([
        "gate", "--action", "read and hash the installer",
        "--authorized", "no", "--owns-artifact", "yes",
        "--license-ok", "yes", "--privacy-ok", "yes"])
    expect(code == 4, f"exit={code}")
    expect(data["errors"][0]["details"]["decision"] == "DENY", "decision")


@check("gate: invalid declaration value exits 2")
def _gate_bad_bool():
    code, data, _ = run_json(["gate", "--action", "x", "--authorized", "maybe"])
    expect(code == 2 and not data["ok"], f"exit={code}")


@check("risk: read-only action needs no approval")
def _risk_read_only():
    code, data, _ = run_json(["risk", "--action", "read-only inventory report"])
    res = data["result"]
    expect(code == 0 and res["risk_class"] == "read_only",
           f"exit={code} class={res.get('risk_class')}")
    expect(res["approval_required"] == "none", "approval should be none")


@check("risk: destructive action without approval exits 3")
def _risk_unapproved():
    code, data, _ = run_json(["risk", "--action", "delete all user profile files"])
    expect(code == 3, f"exit={code}")
    details = data["errors"][0]["details"]
    expect(details["risk_class"] == "destructive", f"class={details['risk_class']}")
    expect("--approve" in details["missing"], "missing --approve")


@check("risk: destructive action with full approval is satisfied")
def _risk_approved():
    code, data, _ = run_json([
        "risk", "--action", "delete all user profile files",
        "--approve", "--approver", "ops-lead", "--reason", "sanctioned cleanup"])
    expect(code == 0 and data["result"]["satisfied"] is True, f"exit={code}")


@check("boundary: credential dumping request is blocked with exit 4")
def _boundary_block():
    code, data, _ = run_json(["boundary", "--request",
                              "dump the password hashes for escalation"])
    expect(code == 4 and not data["ok"], f"exit={code}")
    expect(data["errors"][0]["details"]["matches"], "no boundary matches reported")


@check("boundary: legitimate inspection request is allowed")
def _boundary_allow():
    code, data, _ = run_json(["boundary", "--request",
                              "list archive entries and hash the originals"])
    expect(code == 0 and data["result"]["allowed"] is True, f"exit={code}")


# ---------------------------------------------------------------------------
# Reversible change planning and evidence (recovery path)
# ---------------------------------------------------------------------------

@check("plan-change: creating a change plan without approval exits 3")
def _plan_unapproved(tmp):
    target = tmp / "file.cfg"
    target.write_text("original content\n", encoding="utf-8")
    code, data, _ = run_json(["plan-change", "--target", str(target),
                              "--action", "modify configuration file"])
    expect(code == 3, f"exit={code}")
    expect("--approve" in data["errors"][0]["details"]["missing"], "missing flag")


@check("plan-change: plan, modify, rollback restores the original bytes")
def _plan_roundtrip(tmp):
    target = tmp / "roundtrip.txt"
    target.write_text("original content\n", encoding="utf-8")
    code, data, _ = run_json(["plan-change", "--target", str(target),
                              "--action", "modify configuration file", "--approve"])
    expect(code == 0, f"create exit={code}")
    plan_path = data["result"]["plan_path"]
    backup_path = data["result"]["backup_path"]
    expect(Path(plan_path).is_file(), "plan file missing")
    expect(Path(backup_path).is_file(), "backup file missing")

    target.write_text("MODIFIED content\n", encoding="utf-8")
    code, data, _ = run_json(["plan-change", "--rollback", plan_path,
                              "--approve", "--approver", "ops-lead",
                              "--reason", "restore verified backup"])
    expect(code == 0, f"rollback exit={code}")
    expect(target.read_text(encoding="utf-8") == "original content\n",
           "target not restored")
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    expect(plan["status"] == "rolled_back", f"plan status={plan['status']}")
    expect(data["result"]["target_modified_before_rollback"] is True,
           "modified-before flag should be true")


@check("plan-change: missing target file exits 2")
def _plan_missing_target():
    code, data, _ = run_json(["plan-change", "--target", "/no/such/file.cfg",
                              "--approve"])
    expect(code == 2, f"exit={code}")


@check("plan-change: rolling back twice is rejected")
def _plan_double_rollback(tmp):
    target = tmp / "twice.txt"
    target.write_text("v1\n", encoding="utf-8")
    _, data, _ = run_json(["plan-change", "--target", str(target),
                           "--action", "edit file", "--approve"])
    plan_path = data["result"]["plan_path"]
    _, data, _ = run_json(["plan-change", "--rollback", plan_path,
                           "--approve", "--approver", "a", "--reason", "r"])
    code, data, _ = run_json(["plan-change", "--rollback", plan_path,
                              "--approve", "--approver", "a", "--reason", "r"])
    expect(code == 2 and "already been rolled back" in data["errors"][0]["message"],
           f"exit={code}")


@check("plan-change: requires exactly one of --target or --rollback")
def _plan_args():
    code, data, _ = run_json(["plan-change"])
    expect(code == 2, f"exit={code}")


@check("provenance: record then show --validate returns a valid ledger")
def _provenance_roundtrip(tmp):
    ledger = tmp / "evidence.jsonl"
    code, data, _ = run_json(["provenance", "record", "--event", "manual",
                              "--evidence", str(ledger), "--source", str(ledger.parent),
                              "--command", "selftest --skill c01s01",
                              "--note", "suite record", "--actor", "checks"])
    expect(code == 0 and data["result"]["line_appended"], f"exit={code}")
    code, data, _ = run_json(["provenance", "show", "--evidence", str(ledger),
                              "--validate"])
    view = data["result"]
    expect(view["valid"] is True, f"valid={view['valid']}")
    expect(view["record_count"] == 1, f"count={view['record_count']}")


@check("provenance: reading a missing ledger exits 2")
def _provenance_missing():
    code, data, _ = run_json(["provenance", "show", "--evidence",
                              "/no/such/evidence.jsonl"])
    expect(code == 2, f"exit={code}")


@check("provenance: unknown skill id is rejected before writing")
def _provenance_bad_skill(tmp):
    ledger = tmp / "ev2.jsonl"
    code, data, _ = run_json(["provenance", "record", "--event", "manual",
                              "--evidence", str(ledger), "--skill", "c99s99"])
    expect(code == 2, f"exit={code}")
    expect(not ledger.exists(), "ledger must not be created on validation failure")


# ---------------------------------------------------------------------------
# Artifact routing, decomposition, clean state, version policy
# ---------------------------------------------------------------------------

@check("artifact-route: ELF executable is detected with machine details")
def _artifact_elf():
    code, data, _ = run_json(["artifact-route", "--file", sys.executable])
    res = data["result"]
    expect(res["format"] == "elf", f"format={res['format']}")
    expect(res["format_details"].get("machine") in ("x86_64", "arm64", "x86", "arm"),
           f"machine={res['format_details'].get('machine')}")
    expect(res["sha256"] and len(res["sha256"]) == 64, "sha256 missing")


@check("artifact-route: crafted PE DLL is detected as dll")
def _artifact_pe(tmp):
    path = tmp / "sample.dll"
    blob = bytearray(512)
    blob[0:2] = b"MZ"
    blob[0x3C:0x40] = (0x80).to_bytes(4, "little")
    blob[0x80:0x84] = b"PE\x00\x00"
    blob[0x84:0x86] = (0x8664).to_bytes(2, "little")
    # FileHeader.Characteristics sits at e_lfanew + 4 + 22
    blob[0x9A:0x9C] = (0x2000).to_bytes(2, "little")
    path.write_bytes(bytes(blob))
    code, data, _ = run_json(["artifact-route", "--file", str(path)])
    res = data["result"]
    expect(res["format"] == "pe", f"format={res['format']}")
    expect(res["format_details"]["subsystem_kind"] == "dll", "should be dll")
    expect(res["format_details"]["machine"] == "x86_64", "machine mismatch")


@check("artifact-route: JAR container routes to list-first-archive")
def _artifact_jar(tmp):
    path = tmp / "sample.jar"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    code, data, _ = run_json(["artifact-route", "--file", str(path)])
    res = data["result"]
    expect(res["format"] == "jar", f"format={res['format']}")
    expect(res["handler"] == "list-first-archive", f"handler={res['handler']}")
    expect("traversal" in res["extraction_policy"], "safety policy missing")


@check("artifact-route: plain text is unknown but exits 0 with a triage handler")
def _artifact_unknown(tmp):
    path = tmp / "plain.txt"
    path.write_text("just text\n", encoding="utf-8")
    code, data, _ = run_json(["artifact-route", "--file", str(path)])
    expect(code == 0, f"exit={code}")
    expect(data["result"]["format"] == "unknown", "format")
    expect(data["result"]["handler"] == "manual-triage", "handler")


@check("artifact-route: missing file exits 2")
def _artifact_missing():
    code, data, _ = run_json(["artifact-route", "--file", "/no/such.bin"])
    expect(code == 2, f"exit={code}")


@check("decompose: goal expands to the eight ordered stages")
def _decompose():
    code, data, _ = run_json(["decompose", "--goal", "repair the launcher crash"])
    res = data["result"]
    stages = [s["stage"] for s in res["stages"]]
    expect(stages == ["inspect", "preserve", "reproduce", "repair", "test",
                      "package", "verify", "handoff"], f"stages={stages}")
    expect(res["goal"] == "repair the launcher crash", "goal not recorded")


@check("decompose: empty goal exits 2")
def _decompose_empty():
    code, data, _ = run_json(["decompose", "--goal", " "])
    expect(code == 2, f"exit={code}")


@check("clean-state: command execution requires --approve and exits 3")
def _clean_unapproved():
    code, data, _ = run_json(["clean-state", "--argv",
                              json.dumps(["python3", "-c", "print(1)"])])
    expect(code == 3, f"exit={code}")
    expect("--approve" in data["errors"][0]["details"]["missing"], "missing flag")


@check("clean-state: identical clean runs report hidden_state none")
def _clean_identical():
    code, data, _ = run_json(["clean-state", "--argv",
                              json.dumps(["python3", "-c", "print(42)"]),
                              "--repeat", "2", "--approve"])
    res = data["result"]
    expect(res["hidden_state"] == "none", f"hidden_state={res['hidden_state']}")
    expect(res["identical_stdout"] is True, "identical flag")
    expect(res["runs"][0]["exit_code"] == 0, "exit code")


@check("clean-state: volatile output is detected as hidden state")
def _clean_volatile():
    code, data, _ = run_json([
        "clean-state", "--argv",
        json.dumps(["python3", "-c", "import time; print(time.time_ns())"]),
        "--repeat", "2", "--approve"])
    res = data["result"]
    expect(res["hidden_state"] == "detected",
           f"hidden_state={res['hidden_state']}")


@check("clean-state: invalid argv JSON and bad repeat values exit 2")
def _clean_invalid():
    code, data, _ = run_json(["clean-state", "--argv", "not-json", "--approve"])
    expect(code == 2, f"bad json exit={code}")
    code, data, _ = run_json(["clean-state", "--argv",
                              json.dumps(["python3", "-c", "print(1)"]),
                              "--repeat", "1", "--approve"])
    expect(code == 2, f"bad repeat exit={code}")


@check("version-policy: selected tools report pins and pin status")
def _version_policy():
    code, data, _ = run_json(["version-policy", "--tool", "git", "--tool", "python3"])
    res = data["result"]
    expect(len(res["rows"]) == 2, f"rows={len(res['rows'])}")
    for row in res["rows"]:
        expect(row["pin_status"] in ("supported", "unsupported", "missing",
                                     "broken", "unversioned"),
               f"pin_status={row['pin_status']}")
        for field in CONTRACT_FIELDS:
            expect(field in row, f"missing contract field {field}")
    expect(res["summary"]["tools_checked"] == 2, "summary mismatch")
    expect(res["combinations"], "combinations missing")


@check("version-policy: unknown tool exits 2")
def _version_policy_unknown():
    code, data, _ = run_json(["version-policy", "--tool", "no-such-tool"])
    expect(code == 2, f"exit={code}")


# ---------------------------------------------------------------------------
# Structured output and envelope shape
# ---------------------------------------------------------------------------

@check("output: every operational command returns the standard envelope")
def _envelope_shape(tmp):
    sample = tmp / "plain.txt"
    sample.write_text("x\n", encoding="utf-8")
    cases = [
        ["decompose", "--goal", "verify envelope"],
        ["risk", "--action", "read-only check"],
        ["artifact-route", "--file", str(sample)],
        ["capabilities", "--json"],
    ]
    for args in cases:
        code, data, _ = run_json(args)
        for key in ("ok", "command", "result", "errors", "warnings"):
            expect(key in data, f"{args[0]}: envelope missing {key}")
        expect(isinstance(data["errors"], list), "errors must be a list")
        expect(data["command"] == args[0], f"command echo mismatch: {data['command']}")


@check("output: errors are structured with message, exit_code, details")
def _error_shape():
    code, data, _ = run_json(["selftest", "--skill", "bad-id"])
    expect(code == 2, f"exit={code}")
    err = data["errors"][0]
    for key in ("message", "exit_code", "details"):
        expect(key in err, f"error missing {key}")
    expect(err["exit_code"] == 2, "exit_code mismatch")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    import shutil
    tmp_root = Path(tempfile.mkdtemp(prefix="pc-capability-checks-"))
    passed = 0
    failed = []
    try:
        import inspect
        for name, fn in CHECKS:
            try:
                if inspect.signature(fn).parameters:
                    fn(tmp_root)
                else:
                    fn()
                passed += 1
                print(f"PASS  {name}")
            except AssertionError as exc:
                failed.append((name, str(exc)))
                print(f"FAIL  {name}: {exc}")
            except Exception as exc:  # noqa: BLE001 — report and continue
                failed.append((name, f"{type(exc).__name__}: {exc}"))
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    total = len(CHECKS)
    print()
    print(f"checks: {total} | passed: {passed} | failed: {len(failed)}")
    if failed:
        print("failures:")
        for name, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
