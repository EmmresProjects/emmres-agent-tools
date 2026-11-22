#!/usr/bin/env python3
"""Automated validation suite for the Orchestration, Planning, and Work Splitting
Skill Tool. Runs the REAL primary entry point (tools-work-splitter.py) through
subprocess with fixed argv and a timeout, then parses every structured result.

Usage (from any working directory):
    python3 tools-selftest.py
    python3 tools-selftest.py --verbose

Exit code 0 = all checks passed, 1 = at least one check failed.
Standard library only. Temporary files live in a TemporaryDirectory and are
removed automatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
CLI = TOOL_DIR / "tools-work-splitter.py"
FOLDER = TOOL_DIR.name

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append((name, str(detail)[:300]))
    print("%s %s%s" % ("PASS" if condition else "FAIL", name, (" -- %s" % detail)[:300] if detail and not condition else ""))


def run(args, cwd=TOOL_DIR, timeout=25, expect_exit=None):
    proc = subprocess.run([sys.executable, str(CLI)] + [str(a) for a in args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"_raw": proc.stdout[:600], "_stderr": proc.stderr[:600]}
    if expect_exit is not None:
        check("exit code %s for %s" % (expect_exit, args[0]), proc.returncode == expect_exit,
              "got %d; stdout=%s stderr=%s" % (proc.returncode, str(data)[:200], proc.stderr[:200]))
    return proc.returncode, data


def write(path, obj):
    p = Path(path)
    p.write_text(json.dumps(obj, indent=2) + "\n" if not isinstance(obj, str) else obj, encoding="utf-8")
    return p


def main(verbose=False):
    with tempfile.TemporaryDirectory(prefix="ws-selftest-") as tmp:
        T = Path(tmp)
        state = T / "state.json"
        out = T / "out"
        sample = "asset-sample-project.json"
        evidence = "asset-gate-evidence.json"

        # 1. Built-in help + capability list ---------------------------------
        rc, h = run(["help"], expect_exit=0)
        check("help returns ok envelope", h.get("status") == "ok" and "commands" in h.get("data", {}))
        rc, caps = run(["capabilities"], expect_exit=0)
        cl = caps.get("data", {})
        check("capabilities lists 12 implemented capabilities",
              cl.get("capability_count") == 12 and all(c.get("implemented") for c in cl.get("capabilities", [])),
              str(cl.get("capability_count")))
        check("capability list names match category 1 of the source",
              cl.get("capabilities", [{}])[0].get("name") == "Powerful Work Splitter")
        rc, v = run(["version"], expect_exit=0)
        check("version command", v.get("status") == "ok" and bool(v.get("data", {}).get("version")))

        # 2. Assess / estimate / plan / dependency graph ----------------------
        rc, a = run(["assess", "--input", sample, "--input-format", "json"], expect_exit=0)
        d = a.get("data", {})
        check("assess produces one-turn decision and staged fallback",
              "one_turn_feasible" in d.get("first_response", {}) and len(d.get("major_stages", [])) >= 1,
              str(d.get("first_response"))[:200])
        check("assess asks user to choose stage count", d.get("next_action", {}).get("type") == "choose_stage_count")
        check("file contract inventory is present", d.get("file_contract", {}).get("status") == "pass")
        rc, est = run(["estimate", "--input", sample, "--input-format", "json"], expect_exit=0)
        e = est.get("data", {}).get("estimate", {})
        check("estimate returns recommended/minimum/maximum and checks",
              all(k in e for k in ("recommended", "minimum", "maximum", "checks")), str(e)[:200])
        rc, p = run(["plan", "--input", sample, "--input-format", "json", "--stages", "5", "--state", state], expect_exit=0)
        pd = p.get("data", {})
        check("plan writes 5 labelled stages", pd.get("stage_count") == 5 and len(pd.get("stages", [])) == 5)
        check("plan has dependency graph, critical path and parallel streams",
              all(k in pd for k in ("dependency_graph", "critical_path", "parallel_streams")))
        check("plan preserves every requirement owner", len(pd.get("requirement_ownership", {})) == 6)
        check("state file was created and parses", state.exists() and isinstance(json.loads(state.read_text()), dict))

        # 3. Intake: plain text / Markdown / copied HTML / whole catalog ------
        single = write(T / "single.txt", "Powerful Work Splitter\nEvaluates every request before execution and decides whether one response is sufficient or staged work is safer. It returns an approved stage plan and a verified final package.\n")
        md = write(T / "list.md", "# Category: Research, Search, and Evidence\n\n1. Research Question Builder\nTurns a broad topic into answerable subquestions. It defines evidence needs and exclusions. It returns a scoped research plan.\n\n2. Authoritative Source Finder\nPrioritizes primary and official sources. It locates standards and original studies. It returns a source set with relevance notes.\n")
        html = write(T / "copied.html", "<style>.cat{color:#333}.row{padding:2px}</style>\n<div class='cat' data-cat-id='7'><h2>7. Document and PDF Production</h2></div>\n<ul><li class='row' data-id='1'><span>1. Word Document Architect</span><p>Creates editable DOCX documents with professional structure. It uses styles, headings, tables and accessibility settings. It returns a polished editable DOCX.</p></li></ul>\n")
        rc, i1 = run(["intake", "--input", single, "--input-format", "text"], expect_exit=0)
        check("plain text single capability parsed", i1["data"]["counts"]["capabilities"] == 1
              and i1["data"]["umbrella"]["slug"] == "powerful-work-splitter", str(i1["data"]["counts"]))
        rc, i2 = run(["intake", "--input", md, "--input-format", "markdown"], expect_exit=0)
        check("markdown category + numbered list parsed", i2["data"]["counts"]["capabilities"] == 2
              and i2["data"]["counts"]["named_categories"] == 1, str(i2["data"]["counts"]))
        rc, i3 = run(["intake", "--input", html, "--input-format", "html"], expect_exit=0)
        check("copied HTML tags/classes/data attributes ignored", i3["data"]["counts"]["capabilities"] == 1
              and i3["data"]["capabilities"][0]["name"] == "Word Document Architect", str(i3["data"]["capabilities"])[:200])
        rc, i4 = run(["intake", "--input", "asset-catalog-source.txt", "--input-format", "text", "--limit", "2"], expect_exit=0)
        check("full source parses 24 categories / 242 capabilities",
              i4["data"]["counts"]["categories"] == 24 and i4["data"]["counts"]["capabilities"] == 242,
              str(i4["data"]["counts"]))
        rc, i5 = run(["intake", "--input", "asset-catalog-source.txt", "--input-format", "text",
                      "--category", "Orchestration, Planning, and Work Splitting", "--limit", "2"], expect_exit=0)
        check("category filter builds one unified spec",
              i5["data"]["unified"]["capability_count"] == 12
              and i5["data"]["unified"]["folder_name"] == "orchestration-planning-and-work-splitting",
              str(i5["data"]["unified"])[:200])

        # 4. Registry, briefs, source consistency ----------------------------
        rc, c1 = run(["catalog", "--action", "list"], expect_exit=0)
        check("catalog list shows 24 categories / 242 skills",
              c1["data"]["category_count"] == 24 and c1["data"]["skill_count"] == 242)
        rc, c2 = run(["catalog", "--action", "brief", "--name", "Research, Search, and Evidence"], expect_exit=0)
        check("category brief includes capabilities and stage plan",
              len(c2["data"]["capabilities"]) == 10 and "stage_plan" in c2["data"], str(c2.get("errors"))[:200])
        rc, c3 = run(["catalog", "--action", "search", "--query", "recovery"], expect_exit=0)
        check("catalog search returns matches", c3["data"]["matches"] > 0)
        rc, c4 = run(["catalog", "--action", "source"], expect_exit=0)
        check("catalog source asset resolves and matches registry", c4["data"]["consistent"] is True,
              str(c4["data"])[:200])

        # 5. Gate / checkpoint / continue / rebalance ------------------------
        rc, g = run(["gate", "--stage", "1", "--state", state, "--evidence", evidence], expect_exit=0)
        check("milestone gate passes on reproducible evidence", g["data"]["status"] == "pass", str(g.get("errors"))[:200])
        bad_evidence = write(T / "bad-evidence.json", {"evidence": [{"type": "artifact", "value": "", "reproducible": False}]})
        rc, gb = run(["gate", "--stage", "1", "--state", state, "--evidence", bad_evidence])
        check("gate blocks incomplete evidence", gb["data"]["status"] in ("repair", "blocked"), str(gb["data"])[:200])
        rc, ck = run(["checkpoint", "--stage", "1", "--state", state, "--evidence", evidence], expect_exit=0)
        check("checkpoint receipt records stage, verification and pause",
              ck["data"]["checkpoint_receipt"]["stage"] == "Stage 1 of 5"
              and ck["data"]["checkpoint_receipt"]["pause_instruction"], str(ck.get("errors"))[:200])
        rc, cn = run(["continue", "--state", state, "--reply", "Continue"], expect_exit=0)
        check("Continue recognised and next stage reported", cn["status"] == "ok" and cn["data"]["stage_header"] == "Stage 2 of 5")
        rc, ca = run(["continue", "--state", state, "--reply", "Continue but add a new stage and drop the summary"], expect_exit=4)
        check("ambiguous scope-changing reply rejected", ca["status"] == "blocked" and ca["data"]["verdict"]["recognized"] is False)
        rc, n = run(["negotiate", "--stages", "3", "--state", state], expect_exit=0)
        check("negotiate rebalances 5 -> 3 with no lost requirements",
              n["data"]["new_stage_count"] == 3 and n["data"]["requirements_dropped"] == 0
              and n["data"]["final_stage_includes_assembly"] is True, str(n.get("errors"))[:200])
        rc, ex = run(["expand", "--stages", "7", "--state", state], expect_exit=0)
        check("expand preserves completed stages", ex["data"]["new_stage_count"] == 7
              and len(ex["data"]["preserved_stages"]) >= 1, str(ex.get("errors"))[:200])
        rc, co = run(["compress", "--stages", "4", "--state", state], expect_exit=0)
        check("compress protects critical gates", co["data"]["new_stage_count"] == 4
              and co["data"]["critical_units_preserved"] > 0, str(co.get("errors"))[:200])

        # 6. Run (dry / approval / applied), assembly, recovery --------------
        rc, plan2 = run(["plan", "--input", sample, "--input-format", "json", "--stages", "4", "--state", state], expect_exit=0)
        check("fresh 4-stage plan for execution", plan2["data"]["stage_count"] == 4)
        rc, rd = run(["run", "--stage", "1", "--state", state], expect_exit=0)
        check("run dry-run simulates bounded declarative actions", rd["data"]["dry_run"] is True and len(rd["data"]["actions"]) > 0)
        rc, rp = run(["run", "--stage", "1", "--state", state, "--apply"], expect_exit=3)
        check("run --apply requires approval", rp["status"] == "needs_approval")
        rc, rr = run(["run", "--stage", "1", "--state", state, "--apply", "--approve", "--output-dir", out], expect_exit=0)
        check("approved run writes real artifact files",
              bool(rr["data"].get("artifacts_written")) and out.exists() and len(list(out.glob("*.json"))) >= 1,
              str(rr.get("errors"))[:200])
        art = list(out.glob("*.json"))[0]
        check("written artifact opens and parses", isinstance(json.loads(art.read_text()), dict))
        for stage_no in range(1, 5):
            run(["run", "--stage", stage_no, "--state", state, "--apply", "--approve", "--output-dir", out])
            run(["checkpoint", "--stage", stage_no, "--state", state, "--evidence", evidence])
        rc, st = run(["state", "--action", "show", "--state", state], expect_exit=0)
        check("all four stages completed and recorded", st["data"]["completed_stages"] == [1, 2, 3, 4], str(st["data"]["completed_stages"]))
        rc, asm = run(["assemble", "--state", state], expect_exit=0)
        check("assembly passes coverage and artifact checks",
              asm["data"]["status"] == "pass" and asm["data"]["requirement_coverage_rate"] == 1.0,
              str(asm["data"]["defects"])[:200])
        rc, ap = run(["assemble", "--state", state, "--publish"], expect_exit=3)
        check("publishing requires approval", ap["status"] == "needs_approval")
        rc, app = run(["assemble", "--state", state, "--publish", "--approve"], expect_exit=0)
        check("approved publish recorded", app["data"]["published"] is True)
        rc, rec = run(["recover", "--state", state], expect_exit=0)
        check("recovery isolates earliest bad state and preserves work",
              "earliest_bad_state" in rec["data"]["recovery_report"]
              and len(rec["data"]["recovery_report"]["preserved_stages"]) >= 1, str(rec["data"])[:200])

        # 7. Worked example (flagship spec) ---------------------------------
        rc, we = run(["worked-example"], expect_exit=0)
        steps = we["data"]["steps"]
        check("worked example: recommend 8, choose 5, expand to 10",
              steps[0]["recommended_stages"] == 8 and steps[1]["selected_stages"] == 5
              and we["data"]["final_stage_count"] == 10 and we["data"]["requirements_dropped"] == 0,
              str(steps)[:250])

        # 8. Validation command ----------------------------------------------
        for kind, inp in (("project", sample), ("state", state), ("config", None), ("registry", None), ("spec", None)):
            args = ["validate", "--kind", kind]
            if inp is not None:
                args += ["--input", inp]
            if kind == "spec":
                args += ["--input", single]
            rc, vv = run(args, expect_exit=0)
            check("validate --kind %s" % kind, vv.get("data", {}).get("valid") is True, str(vv.get("errors"))[:200])

        # 9. Invalid / missing inputs and structured errors -------------------
        check("missing input -> structured error", run(["assess"], expect_exit=2)[1]["status"] == "error")
        badjson = write(T / "bad.json", "{not json")
        check("malformed JSON -> ERR_INPUT_JSON",
              run(["assess", "--input", badjson, "--input-format", "json"], expect_exit=2)[1]["errors"][0]["code"] == "ERR_INPUT_JSON")
        invalid = write(T / "invalid.json", {"goal": "Bad", "requirements": ["x"],
                                             "work_packages": [{"id": "WP1", "name": "x", "depends_on": ["MISSING"]}],
                                             "tool_calls": -1, "uncertainty": 3})
        check("invalid project -> ERR_PROJECT_INVALID with defects",
              run(["assess", "--input", invalid, "--input-format", "json"], expect_exit=2)[1]["errors"][0]["code"] == "ERR_PROJECT_INVALID")
        cyc = write(T / "cycle.json", {"goal": "Cycle", "requirements": [{"id": "R1", "text": "x"}],
                                       "work_packages": [{"id": "A", "name": "a", "depends_on": ["B"]},
                                                         {"id": "B", "name": "b", "depends_on": ["A"]}]})
        check("dependency cycle -> ERR_DEPENDENCY_CYCLE",
              run(["plan", "--input", cyc, "--input-format", "json", "--stages", "3", "--state", T / "cyc.json"],
                  expect_exit=2)[1]["errors"][0]["code"] == "ERR_DEPENDENCY_CYCLE")
        check("missing state -> ERR_STATE_MISSING",
              run(["gate", "--stage", "1", "--state", T / "nope.json", "--evidence", evidence],
                  expect_exit=2)[1]["errors"][0]["code"] == "ERR_STATE_MISSING")
        check("out-of-range stage count -> ERR_STAGE_COUNT",
              run(["plan", "--input", sample, "--input-format", "json", "--stages", "99", "--state", T / "x.json"],
                  expect_exit=2)[1]["errors"][0]["code"] == "ERR_STAGE_COUNT")
        check("unknown category -> ERR_CATEGORY_NOT_FOUND",
              run(["catalog", "--action", "category", "--name", "No Such Category"],
                  expect_exit=2)[1]["errors"][0]["code"] == "ERR_CATEGORY_NOT_FOUND")
        check("non-existent input file -> ERR_INPUT_MISSING",
              run(["intake", "--input", T / "missing.txt"], expect_exit=2)[1]["errors"][0]["code"] == "ERR_INPUT_MISSING")

        # 10. Limits, cancellation, timeout, config presets -------------------
        flag = T / "cancel.flag"; flag.touch()
        check("cancel file triggers cancellation",
              run(["plan", "--input", sample, "--input-format", "json", "--stages", "3",
                   "--state", T / "c.json", "--cancel-file", flag], expect_exit=5)[1]["status"] == "cancelled")
        check("timeout triggers cancellation",
              run(["plan", "--input", sample, "--input-format", "json", "--stages", "3",
                   "--state", T / "c.json", "--timeout", "0.0001"], expect_exit=5)[1]["status"] == "cancelled")
        rc, strict = run(["assess", "--config", "asset-config-preset-strict.json", "--input", sample,
                          "--input-format", "json"], expect_exit=0)
        rc, default = run(["assess", "--input", sample, "--input-format", "json"], expect_exit=0)
        check("changing config changes the result", strict["data"]["recommended_stages"] != default["data"]["recommended_stages"]
              and strict["data"]["first_response"]["weighted_score"] != default["data"]["first_response"]["weighted_score"],
              "%s vs %s" % (strict["data"]["recommended_stages"], default["data"]["recommended_stages"]))
        changed = write(T / "changed.json", dict(json.loads((TOOL_DIR / sample).read_text()), requirements=[{"id": "R1", "text": "Only one requirement"}], work_packages=[]))
        rc, changed_out = run(["estimate", "--input", changed, "--input-format", "json"], expect_exit=0)
        check("changing input changes the estimate",
              changed_out["data"]["estimate"]["recommended"] != default["data"]["recommended_stages"],
              str(changed_out["data"]["estimate"]["recommended"]))

        # 11. Output paths, formats, approval, cleanup ------------------------
        target = T / "report.txt"
        run(["help", "--out-format", "text", "--output", target], expect_exit=0)
        check("--output writes renderable text", target.exists() and "| help | ok" in target.read_text())
        rc, over = run(["help", "--out-format", "text", "--output", target], expect_exit=3)
        check("overwriting output requires approval", over["status"] == "needs_approval")
        rc, over2 = run(["help", "--out-format", "text", "--output", target, "--approve"], expect_exit=0)
        check("approved overwrite succeeds", rc == 0 and "| help | ok" in target.read_text())
        (T / "work-splitter-state.json.bak").write_text("backup", encoding="utf-8")
        (T / ".ws-leftover.tmp").write_text("temp", encoding="utf-8")
        rc, cln = run(["cleanup", "--state", state], expect_exit=3)
        check("cleanup requires approval", cln["status"] == "needs_approval")
        rc, cln2 = run(["cleanup", "--state", T / "work-splitter-state.json", "--approve"], expect_exit=0)
        check("approved cleanup removes .bak/.tmp files", cln2["data"]["count"] >= 1
              and not (T / ".ws-leftover.tmp").exists(), str(cln2["data"]))

        # 12. Clean skill root with one test SKILL.md beside the folder -------
        root = T / "cleanroot"
        root.mkdir()
        (root / "SKILL.md").write_text("# Test parent skill\nThis file exists only to verify compatibility.\n", encoding="utf-8")
        shutil.copytree(TOOL_DIR, root / FOLDER, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        proc = subprocess.run([sys.executable, FOLDER + "/tools-work-splitter.py", "capabilities"],
                              cwd=str(root), capture_output=True, text=True, timeout=25)
        clean_caps = json.loads(proc.stdout)
        check("clean skill root: exactly one SKILL.md at root", len(list(root.glob("SKILL.md"))) == 1)
        check("clean skill root: no SKILL.md inside tool folder", not (root / FOLDER / "SKILL.md").exists())
        check("clean skill root: relative references resolve", proc.returncode == 0
              and clean_caps["data"]["capability_count"] == 12, proc.stderr[:200])
        proc2 = subprocess.run([sys.executable, FOLDER + "/tools-work-splitter.py", "catalog", "--action", "source"],
                               cwd=str(root), capture_output=True, text=True, timeout=25)
        check("clean skill root: catalog asset resolves via skill-root-relative path",
              proc2.returncode == 0 and json.loads(proc2.stdout)["data"]["consistent"] is True, proc2.stderr[:200])
        check("clean skill root: no internal subfolders in tool folder",
              all(p.is_file() for p in (root / FOLDER).iterdir()))

    # 13. Package structure assertions --------------------------------------
    files = sorted(p.name for p in TOOL_DIR.iterdir() if p.is_file())
    check("exactly one knowledge file", [f for f in files if f.startswith("knowledge-")] == ["knowledge-readme.md"])
    check("no SKILL.md in tool folder", "SKILL.md" not in files)
    check("no unprefixed files", all(f.startswith(("tools-", "knowledge-", "asset-")) for f in files), str(files))
    check("no internal subfolders", all(p.is_file() for p in TOOL_DIR.iterdir()))
    check("folder name is lowercase hyphenated", __import__("re").fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", FOLDER) is not None)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name, detail in FAIL:
            print("  FAILED: %s -- %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.verbose))
