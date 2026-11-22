#!/usr/bin/env python3
"""
tools-selftest.py — Deterministic end-to-end self-test for tools-offline-app-dev.py.

Runs the real CLI through a temporary workspace and asserts exit codes, output
structure, file outputs, overwrite protection, and failure paths. Standard
library only. No network access. Cleans up the temporary workspace on exit
(success or failure).

Usage: python3 tools-selftest.py [--keep] [-v]
  --keep   keep the temporary workspace and print its path (for debugging)
  -v       print every command and its captured output on failure only (default)
           or always when passed twice

Exit codes: 0 = all tests passed; 1 = at least one failure; 2 = usage error.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "tools-offline-app-dev.py"

PASS, FAIL = "PASS", "FAIL"
results = []
VERBOSE = 0


def run(*argv, expect=None, contains=None, cwd=None):
    cmd = [sys.executable, str(TOOL)] + [str(a) for a in argv]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    ok = True
    notes = []
    if expect is not None and proc.returncode != expect:
        ok = False
        notes.append("exit=%d expected=%d" % (proc.returncode, expect))
    for needle in (contains or []):
        if needle not in proc.stdout and needle not in proc.stderr:
            ok = False
            notes.append("missing %r in output" % needle)
    return ok, proc, notes


def check(name, *argv, **kw):
    ok, proc, notes = run(*argv, **kw)
    results.append((ok, name, notes, proc))
    print("[%s] %s%s" % (PASS if ok else FAIL, name, (" — " + "; ".join(notes)) if notes else ""))
    if not ok and VERBOSE:
        print("---- stdout ----\n%s\n---- stderr ----\n%s" % (proc.stdout, proc.stderr))
    return proc


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Self-test for tools-offline-app-dev.py")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-v", action="count", default=0)
    args = ap.parse_args()
    VERBOSE = args.v

    if not TOOL.is_file():
        print("FATAL: %s not found beside this script" % TOOL.name)
        return 2

    work = Path(tempfile.mkdtemp(prefix="oad-selftest-"))
    try:
        # ---- help / version / capabilities -------------------------------
        check("help lists commands", "--help", expect=0, contains=["capabilities", "accept", "verify"])
        check("version prints", "--version", expect=0, contains=["1.0.0"])
        check("capabilities text", "capabilities", expect=0, contains=["1095"])
        proc = check("capabilities json parses", "capabilities", "--format", "json", expect=0)
        cap = json.loads(proc.stdout)
        assert cap["catalog"]["skills"] == 1095 and cap["catalog"]["categories"] == 27

        # ---- catalog browsing --------------------------------------------
        check("list categories", "list", expect=0, contains=["Android Offline Development Stack"])
        check("list count-only", "list", "--count-only", expect=0, contains=["1095"])
        check("list category 6", "list", "--category", "6", expect=0, contains=["Android Keystore Manager"])
        check("list archetype evaluate count", "list", "--archetype", "evaluate", "--count-only", expect=0, contains=["204"])
        check("list bad category", "list", "--category", "zzz-no-such-category", expect=2)
        check("search finds keystore", "search", "keystore", "--limit", "5", expect=0, contains=["6.25"])
        proc = check("search json", "search", "acceptance", "--format", "json", "--limit", "5", expect=0)
        assert json.loads(proc.stdout)["count"] >= 1
        check("search unknown archetype", "search", "x", "--archetype", "bogus", expect=2)
        check("show by ref", "show", "6.25", expect=0, contains=["Android Keystore Adapter"])
        check("show by exact name", "show", "Android Keystore Adapter", expect=0, contains=["6.25"])
        check("show ambiguous refused", "show", "shrinker", expect=2)
        check("show bad ref", "show", "99.99", expect=2)

        # ---- briefs for every archetype -----------------------------------
        archetype_refs = [("1.1", "select-configure"), ("1.8", "plan"), ("3.1", "build"),
                          ("1.37", "implement"), ("6.6", "operate"), ("17.1", "evaluate"),
                          ("6.25", "adapter")]
        for ref, arch in archetype_refs:
            proc = check("brief %s (%s)" % (ref, arch), "brief", ref, "--format", "json", expect=0)
            payload = json.loads(proc.stdout)
            assert payload["archetype"] == arch and payload["sections"], "brief payload invalid"
            check("brief %s md" % ref, "brief", ref, "--format", "md", expect=0, contains=["## ", "Contract"])

        out_file = work / "brief-out.md"
        check("brief writes output", "brief", "6.25", "--output", str(out_file), expect=0)
        first = out_file.read_text()
        check("brief refuses overwrite without --force", "brief", "6.25", "--output", str(out_file), expect=2)
        assert out_file.read_text() == first
        check("brief overwrites with --force", "brief", "6.25", "--output", str(out_file), "--force", expect=0)

        # ---- decision matrix ------------------------------------------------
        proc = check("matrix json parses", "matrix", "--format", "json", expect=0)
        mx = json.loads(proc.stdout)
        assert len(mx["ranking"]) == 16
        scores = [r["weighted_score"] for r in mx["ranking"]]
        assert scores == sorted(scores, reverse=True)
        proc = check("matrix limited targets", "matrix", "--targets", "android,ios,flutter", "--format", "json", expect=0)
        mx = json.loads(proc.stdout)
        assert [r["target"] for r in mx["ranking"]] == ["flutter", "android", "ios"]
        override = work / "criteria.json"
        override.write_text(json.dumps({"criteria": [{"id": "performance", "weight": 60},
                                                     {"id": "offline_fit", "weight": 40}]}))
        proc = check("matrix criteria override", "matrix", "--targets", "electron,tauri,qt",
                     "--criteria", str(override), "--format", "json", expect=0)
        assert json.loads(proc.stdout)["ranking"][0]["target"] == "qt"
        check("matrix unknown target", "matrix", "--targets", "not-a-target", expect=2)
        check("matrix missing override file", "matrix", "--criteria", str(work / "nope.json"), expect=2)

        # ---- profiles --------------------------------------------------------
        check("profile known id", "profile", "android", expect=0, contains=["TalkBack"])
        check("profile list", "profile", "--list", expect=0, contains=["flutter", "tauri"])
        check("profile unknown id", "profile", "plan9-from-outer-space", expect=2)

        # ---- plans -----------------------------------------------------------
        check("plan list", "plan", "--list", expect=0, contains=["poc-spine", "rollback-recovery"])
        check("plan md contains completion condition", "plan", "clean-architecture", "--format", "md",
              expect=0, contains=["## Completion condition"])
        check("plan --set context shown", "plan", "poc-spine", "--set", "project=Fieldnotes",
              expect=0, contains=["project=Fieldnotes"])
        check("plan unknown template", "plan", "nope", expect=2)

        # ---- ADR lifecycle ---------------------------------------------------
        adr_store = work / "adr"
        check("adr new 1", "adr", "new", "--title", "Use Tauri", "--status", "accepted",
              "--store", str(adr_store), expect=0, contains=["adr-0001"])
        check("adr new 2", "adr", "new", "--title", "SQLite storage", "--store", str(adr_store), expect=0)
        adr_file = adr_store / "adr-0001-use-tauri.md"
        assert adr_file.is_file(), "ADR file missing"
        assert adr_file.read_text().startswith("# ADR-0001:")
        check("adr list", "adr", "list", "--store", str(adr_store), expect=0, contains=["ADR-0001", "ADR-0002"])
        check("adr show", "adr", "show", "1", "--store", str(adr_store), expect=0, contains=["Use Tauri"])
        check("adr supersede", "adr", "supersede", "1", "--by", "2", "--store", str(adr_store), expect=0)
        assert "superseded by ADR-0002" in adr_file.read_text()
        check("adr supersede missing target", "adr", "supersede", "7", "--by", "1", "--store", str(adr_store), expect=2)

        # ---- risk register ----------------------------------------------------
        reg_store = work / "registers"
        check("risk add", "risk", "add", "--title", "WebView2 missing on endpoints", "--category", "dependency",
              "--severity", "4", "--likelihood", "3", "--owner", "release", "--store", str(reg_store),
              expect=0, contains=["R-0001"])
        check("risk add second", "risk", "add", "--title", "Bundle mismatch", "--category", "toolchain",
              "--severity", "5", "--likelihood", "2", "--owner", "build", "--store", str(reg_store), expect=0)
        proc = check("risk list json", "risk", "list", "--store", str(reg_store), "--format", "json", expect=0)
        assert json.loads(proc.stdout)["records"][0]["exposure"] == 12
        check("risk update status", "risk", "update", "R-0001", "--status", "mitigating",
              "--store", str(reg_store), expect=0, contains=["status"])
        check("risk close", "risk", "close", "R-0002", "--evidence", "verified on clean VM",
              "--store", str(reg_store), expect=0)
        proc = check("risk export json", "risk", "export", "--store", str(reg_store), "--format", "json", expect=0)
        assert len(json.loads(proc.stdout)["records"]) == 2
        check("risk export csv", "risk", "export", "--store", str(reg_store), "--format", "csv",
              expect=0, contains=["id,title,category"])
        check("risk invalid severity", "risk", "add", "--title", "x", "--severity", "9",
              "--likelihood", "1", "--owner", "o", "--store", str(reg_store), expect=2)
        check("risk bad register name", "risk", "add", "--title", "x", "--severity", "1", "--likelihood", "1",
              "--owner", "o", "--register", "Bad Name!", "--store", str(reg_store), expect=2)

        # ---- acceptance evaluation ---------------------------------------------
        results_tmpl = work / "results.json"
        check("accept init-results", "accept", "--init-results", str(results_tmpl), expect=0)
        doc = json.loads(results_tmpl.read_text())
        doc.update({"platform": "Android 15", "toolchain": "studio-2025.3 / gradle-8.14",
                    "device_or_simulator": "Pixel 7", "tester": "qa", "build": "1.4.0-rc.2"})
        for k in doc["results"]:
            doc["results"][k] = {"result": "pass", "observation": "ok", "evidence": "ev-" + k}
        results_tmpl.write_text(json.dumps(doc))
        check("accept all-pass verdict", "accept", "--results", str(results_tmpl), expect=0, contains=["PASS"])
        doc["results"]["ui-tests"] = {"result": "fail", "observation": "focus order broken", "evidence": "vid.mp4"}
        fail_results = work / "results-fail.json"
        fail_results.write_text(json.dumps(doc))
        check("accept fail verdict exits 4", "accept", "--results", str(fail_results), expect=4, contains=["FAIL"])
        doc["results"]["ui-tests"] = {"result": "warn", "observation": "minor", "evidence": ""}
        del doc["results"]["unit-tests"]
        warn_results = work / "results-warn.json"
        warn_results.write_text(json.dumps(doc))
        check("accept warn/untested verdict exits 3", "accept", "--results", str(warn_results), expect=3, contains=["WARN"])
        check("accept unknown check id rejected", "accept", "--results",
              str(_write_json(work, "bad.json", {"results": {"nope": {"result": "pass"}}})), expect=2)
        check("accept invalid result rejected", "accept", "--results",
              str(_write_json(work, "bad2.json", {"results": {"unit-tests": {"result": "fine"}}})), expect=2)

        # ---- offline verification ----------------------------------------------
        check("verify python3 present", "verify", "--tools", "python3", expect=0)
        check("verify missing tool exits 1", "verify", "--tools", "definitely-not-a-real-tool-xyz", expect=1)
        bundle = work / "bundle"
        (bundle / "sub").mkdir(parents=True)
        (bundle / "a.txt").write_text("alpha")
        (bundle / "sub" / "b.txt").write_text("beta")
        manifest = work / "manifest.json"
        check("verify --init-manifest", "verify", "--root", str(bundle), "--init-manifest", str(manifest), expect=0)
        check("verify manifest round-trip", "verify", "--root", str(bundle), "--manifest", str(manifest), expect=0)
        (bundle / "a.txt").write_text("tampered")
        check("verify detects tamper", "verify", "--root", str(bundle), "--manifest", str(manifest), expect=1)
        (bundle / "extra.bin").write_text("untracked")
        check("verify --strict rejects untracked", "verify", "--root", str(bundle), "--manifest", str(manifest),
              "--strict", expect=1)
        check("verify without args is usage error", "verify", expect=2)

        # ---- summary -----------------------------------------------------------
        total = len(results)
        failed = [n for ok, n, _, _ in results if not ok]
        print("\n%d checks: %d passed, %d failed" % (total, total - len(failed), len(failed)))
        if failed:
            for n in failed:
                print("  FAILED: %s" % n)
            return 1
        return 0
    finally:
        if args.keep:
            print("workspace kept: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def _write_json(work, name, payload):
    p = work / name
    p.write_text(json.dumps(payload))
    return p


if __name__ == "__main__":
    sys.exit(main())
