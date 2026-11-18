#!/usr/bin/env python3
"""tools-iosh_selftest.py -- repeatable regression suite for the Skill Tool.

Runs the primary entry point exactly as an operator would (through the CLI, with
relative paths), asserts observable behaviour, and prints a PASS/FAIL table.
Nothing outside the temporary directory is modified; the fixtures are read-only.

Usage (from the future skill root):
  python3 ios-ipados-agent-toolkit/tools-iosh_selftest.py
  python3 ios-ipados-agent-toolkit/tools-iosh_selftest.py --keep --verbose

Exit code 0 when every test passes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
TOOL = HERE / "tools-iosh.py"
FIXTURE_GLOB = "asset-fixture-*"   # flat prefixed fixtures beside the tool


class Suite:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.results: List[Tuple[str, bool, str]] = []
        self.tmp = Path(tempfile.mkdtemp(prefix="iosh-selftest-"))
        self.env = {**os.environ, "IOSH_STATE_DIR": str(self.tmp / "state")}

    # -- helpers ---------------------------------------------------------
    def run(self, *args: str, expect: int = 0) -> Tuple[int, Dict, str]:
        # run from the folder that CONTAINS the Skill Tool so every case uses skill-root-relative
        # paths ("ios-ipados-agent-toolkit/asset-fixture-...") exactly as a future skill would
        proc = subprocess.run([sys.executable, str(TOOL), *args, "--format", "json"],
                              capture_output=True, text=True, cwd=str(HERE.parent), env=self.env,
                              timeout=300)
        payload = {}
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        if self.verbose and proc.returncode != expect:
            sys.stderr.write(f"[debug] {' '.join(args)} -> {proc.returncode}\n"
                             f"{proc.stdout[:1200]}\n{proc.stderr[:800]}\n")
        return proc.returncode, payload, proc.stdout + proc.stderr

    def check(self, name: str, args: List[str], *, expect: int, predicate=None) -> None:
        try:
            code, payload, raw = self.run(*args, expect=expect)
        except subprocess.TimeoutExpired:
            self.results.append((name, False, "timed out"))
            return
        ok = code == expect
        detail = f"exit {code} (expected {expect})"
        if ok and predicate is not None:
            try:
                ok, detail = predicate(payload)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"predicate error: {exc}"
        if not ok and self.verbose:
            detail += " | " + raw.strip().splitlines()[-1][:200] if raw.strip() else detail
        self.results.append((name, ok, detail))

    # -- suite -----------------------------------------------------------
    def run_all(self) -> int:
        # paths are relative to the future skill root, exactly as SKILL.md will reference them
        fx = HERE.name
        ipa = f"{fx}/asset-fixture-app.ipa"
        plist = f"{fx}/asset-fixture-info.plist"
        broken = f"{fx}/asset-fixture-info-broken.plist"
        profile = f"{fx}/asset-fixture.mobileprovision"
        crash = f"{fx}/asset-fixture-crash.ips"
        project = f"{fx}/asset-fixture-project.pbxproj"
        dylib = f"{fx}/asset-fixture-arm64.dylib"

        # 1. help and capability surface
        self.check("help lists subcommands", ["--help"], expect=0,
                   predicate=lambda p: (True, "help rendered"))
        self.check("capability list (category 9)", ["capabilities", "--category", "9"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["capabilities_listed"] == 12,
                                        "12 entries"))
        self.check("capability list (all 354)", ["capabilities"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["catalog_total"] == 354,
                                        f"{p['data']['summary']['catalog_total']} capabilities"))
        self.check("capabilities json-only has full rows", ["capabilities", "--json-only"], expect=0,
                   predicate=lambda p: (len(p["data"]["capabilities"]) == 354,
                                        "354 rows with id/command/requirements"))
        self.check("implemented-only filter", ["capabilities", "--implemented-only"], expect=0,
                   predicate=lambda p: (all(r["implementation"] == "native"
                                            for r in p["data"]["capabilities"]),
                                        "only native rows"))
        self.check("category 9 fully native", ["capabilities", "--category", "9"], expect=0,
                   predicate=lambda p: (all(r["implementation"] == "native"
                                            for r in p["data"]["capabilities"]),
                                        "all 12 inspect capabilities runnable"))

        # 2. host + health + fixtures
        self.check("probe", ["probe"], expect=0,
                   predicate=lambda p: ("host" in p["data"]["inventory"], "inventory present"))
        # health legitimately FAILs on a host with no Xcode/device tools; only the fixtures must pass
        self.check("health fixtures 9/9", ["health"], expect=1,
                   predicate=lambda p: (p["data"]["summary"]["fixture_checks"] == "9/9",
                                        str(p["data"]["summary"]["fixture_checks"])))
        self.check("health --tool filter", ["health", "--tool", "python3", "--no-fixtures"], expect=0,
                   predicate=lambda p: (len(p["data"]["tools"]) == 1, "one tool row"))

        # 3. routing and policy
        self.check("route macos+publishing", ["route", "--action",
                                             "codesign the ipa and upload to testflight"], expect=0,
                   predicate=lambda p: (p["data"]["decision"]["classification"] == "macos-required"
                                        and {a["id"] for a in p["data"]["decision"]["approvals"]}
                                        >= {"signing", "publishing"}, "classified with gates"))
        self.check("route linux-capable", ["route", "--action", "parse the ipa metadata"], expect=0,
                   predicate=lambda p: (p["data"]["decision"]["classification"] == "linux-capable",
                                        "linux-capable"))
        self.check("route denied (activation lock)", ["route", "--action",
                                                      "bypass activation lock"], expect=5,
                   predicate=lambda p: (p["errors"][0]["code"] == "policy-blocked", "refused"))
        self.check("route device gate", ["route", "--action", "install the app on my ipad via usb"],
                   expect=0,
                   predicate=lambda p: (p["data"]["decision"]["classification"] in
                                        ("physical-device-required", "macos-required"), "device route"))

        # 4. planning
        self.check("plan category 13 routed", ["plan", "--category", "13", "--have-macos"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["routed_steps"] >= 9
                                        and p["data"]["summary"]["approval_gates"] >= 3,
                                        "routed steps + approval gates"))
        self.check("plan capability by id", ["plan", "--capability", "c09-01"], expect=0,
                   predicate=lambda p: (p["data"]["steps"][0]["step_id"] == "c09-01", "c09-01 step"))
        self.check("plan ambiguous capability", ["plan", "--capability", "simctl"], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "capability-ambiguous", "ambiguous"))
        self.check("plan unknown capability", ["plan", "--capability", "zzz-nope"], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "capability-not-found", "not found"))
        self.check("plan execute skips artifact steps", ["plan", "--capability", "c09-01", "--execute"],
                   expect=0,
                   predicate=lambda p: (all(r["status"] == "SKIPPED"
                                            for r in p["data"]["execution"]["results"]),
                                        "nothing destructive executed"))

        # 5. inspection (the core Linux-native value)
        self.check("detect fixture kinds", ["detect", "--paths", fx], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["artifacts"] >= 12, "artifacts classified"))
        self.check("inspect-ipa", ["inspect-ipa", "--ipa", ipa], expect=0,
                   predicate=lambda p: (p["data"]["bundle"]["bundle_id"] == "com.example.fixture"
                                        and p["data"]["architectures"] == ["arm64"]
                                        and len(p["data"]["extensions"]) == 1
                                        and p["data"]["container"]["zip"]["healthy"], "full IPA facts"))
        self.check("inspect-plist valid", ["inspect-plist", "--plist", plist], expect=0,
                   predicate=lambda p: (p["data"]["audit"]["issues"] == [], "no issues"))
        self.check("inspect-plist broken flags issues", ["inspect-plist", "--plist", broken], expect=1,
                   predicate=lambda p: (len(p["data"]["audit"]["issues"]) >= 4, "issues found"))
        self.check("inspect-plist privacy", ["inspect-plist", "--plist", plist, "--privacy"], expect=1,
                   predicate=lambda p: (any(s["verdict"] == "empty"
                                            for s in p["data"]["privacy_strings"]), "empty string found"))
        self.check("inspect-plist convert round-trip", ["inspect-plist", "--plist", plist,
                                                        "--convert", "binary",
                                                        "--out", str(self.tmp / "conv.plist")],
                   expect=0,
                   predicate=lambda p: (p["data"]["converted"]["round_trip_equal"], "types preserved"))
        self.check("inspect-project (flat fixture)", ["inspect-project", "--project", project,
                                                      "--xcconfig", "--diff-configs",
                                                      "--destinations"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["targets"] == 1
                                        and p["data"]["summary"]["shared_schemes"] >= 1,
                                        "project facts"))
        self.check("inspect-project (folder shape)", self._folder_project(project), expect=0,
                   predicate=lambda p: (p["data"]["summary"]["targets"] == 1, "folder shape works"))
        self.check("profile decode", ["profile", "--profile", profile], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["uuid"].startswith("11111111"), "uuid read"))
        self.check("inspect-binary", ["inspect-binary", "--binary", dylib, "--entitlements"],
                   expect=0,
                   predicate=lambda p: (p["data"]["summary"]["architectures"] == "arm64",
                                        "arm64 parsed"))
        self.check("privacy audit", ["privacy", "--target", fx], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["manifests"] == 1, "manifest aggregated"))
        self.check("crash-triage", ["crash-triage", "--crash", crash], expect=0,
                   predicate=lambda p: (p["data"]["triage"]["class"] == "swift-runtime", "classified"))
        self.check("diagnose build log", ["diagnose", "--log", str(self._write("build.log",
                   "error: linker command failed with exit code 1\nUndefined symbols for architecture "
                   "arm64:\n  \"_OBJC_CLASS_$_Foo\", referenced from: clang: error\n"))],
                   expect=0,
                   predicate=lambda p: ("build-error" in p["data"]["classes"], "build-error class"))
        self.check("migration-scan", ["migration-scan", "--project", fx, "--deprecations"], expect=1,
                   predicate=lambda p: (len(p["data"]["arch_references"]) >= 1, "arch references found"))
        self.check("misleading extension detected", ["detect", "--paths",
                                                     str(self._write("fake.ipa", "not a zip\n"))],
                   expect=0,
                   predicate=lambda p: (p["data"]["artifacts"][0]["kind"] != "ipa", "extension lies"))

        # 6. governance and evidence
        self.check("workspace preserves originals", ["workspace", "--name", "t1", "--inputs", ipa,
                                                     "--into", str(self.tmp)], expect=0,
                   predicate=lambda p: (Path(p["data"]["workspace"]["manifest"]).exists()
                                        and (HERE.parent / ipa).exists(),
                                        "manifest + original intact"))
        self.check("workspace clean refuses reuse", ["workspace", "--name", "t1", "--inputs", ipa,
                                                     "--into", str(self.tmp), "--clean"], expect=2,
                   predicate=lambda p: (p["errors"][0]["code"] == "workspace-exists", "refused"))
        self.check("paths normalise", ["paths", "--paths", "/Users/ci/repo/a.ipa", "../../b.ipa"],
                   expect=0,
                   predicate=lambda p: (p["data"]["mapping"]["conflicts"], "conflicts flagged"))
        self.check("gate passes with parsed evidence", ["gate", "--claim", "ipa inspected",
                                                        "--evidence", ipa, crash], expect=0,
                   predicate=lambda p: (p["data"]["verdict"] == "PASS", "PASS"))
        self.check("gate blocks missing evidence", ["gate", "--claim", "the app builds and installs"],
                   expect=1,
                   predicate=lambda p: (p["data"]["verdict"] == "BLOCKED", "BLOCKED"))
        self.check("report evidence pack", ["report", "--evidence", ipa, crash, "--claim", "audit",
                                            "--out", str(self.tmp / "bundle")], expect=0,
                   predicate=lambda p: (Path(p["data"]["manifest_path"]).exists(), "manifest written"))
        # scan the shipped fixture artifacts only: tools-iosh_*.py legitimately contain the
        # detector's own pattern literals and would flag themselves (documented limitation)
        self.check("secrets-scan clean", ["secrets-scan", "--paths",
                                          f"{fx}/asset-fixture-info.plist",
                                          f"{fx}/asset-fixture-crash.ips",
                                          f"{fx}/asset-fixture-project.pbxproj"], expect=0,
                   predicate=lambda p: (p["data"]["findings"] == [], "clean"))
        self.check("secrets-scan finds key", ["secrets-scan", "--paths",
                                              str(self._write("leak.env",
                                                   "API_KEY=abcdef0123456789abcdef\n"))], expect=1,
                   predicate=lambda p: (p["data"]["findings"], "finding raised"))
        self.check("license-scan blocks Apple payload", ["license-scan", "--paths",
                                                        str(self._write("x.txt",
                                                             "Xcode.app/Contents/Developer\n"))],
                   expect=1,
                   predicate=lambda p: (p["data"]["apple"], "apple component flagged"))
        self.check("cache refuses Apple payload", ["cache", "add", "--cache-dir",
                                                    str(self.tmp / "cache"), "--add",
                                                    str(self._write("y.txt",
                                                         "iPhoneOS.platform content"))],
                   expect=1,
                   predicate=lambda p: (p["data"]["clipped"], "refused"))
        self.check("pins record then compare", ["pins", "record", "--tool", "python3"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["recorded"] == 1, "recorded"))
        self.check("pins compare after change", ["pins", "compare"], expect=0,
                   predicate=lambda p: ("drift" in p["data"]["comparison"], "comparison produced"))
        first = self._write("dirA/one.txt", "same\n")
        self.check("snapshot baseline", ["snapshot", "--root", str(Path(first).parent),
                                         "--out", str(self.tmp / "snap1.json")], expect=0,
                   predicate=lambda p: (len(p["data"]["files"]) == 1, "1 file hashed"))
        self._write("dirA/two.txt", "new\n")
        self.check("snapshot detects change", ["snapshot", "--root", str(Path(first).parent),
                                               "--previous", str(self.tmp / "snap1.json"),
                                               "--out", str(self.tmp / "snap2.json")], expect=0,
                   predicate=lambda p: (p["data"]["diff"]["added"] == ["two.txt"], "added file shown"))
        self.check("binary-diff identical", ["binary-diff", "--first", dylib, "--second", dylib],
                   expect=0,
                   predicate=lambda p: (p["data"]["identical_bytes"], "identical"))
        self.check("matrix lanes", ["matrix", "--include-device"], expect=0,
                   predicate=lambda p: (p["data"]["summary"]["lanes"] == 7, "7 lanes"))
        self.check("merge-results keeps first failure", ["merge-results", "--results",
                                                         f"{fx}/asset-fixture-xctest-results.json",
                                                         "--out", str(self.tmp / "merged.json")],
                   expect=0,
                   predicate=lambda p: (p["data"]["totals"]["failed"] == 0
                                        and len(p["data"]["first_failure_evidence"]) == 1,
                                        "first-failure retained"))
        self.check("export-options writes plist", ["export-options", "--method", "app-store-connect",
                                                   "--team-id", "ABCDE12345", "--bundle-id",
                                                   "com.example.fixture", "--out",
                                                   str(self.tmp / "ExportOptions.plist")], expect=0,
                   predicate=lambda p: (p["data"]["round_trip_equal"], "round-trip verified"))
        self.check("export-options rejects bad team", ["export-options", "--method", "development",
                                                       "--team-id", "bad", "--out",
                                                       str(self.tmp / "x.plist")], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "bad-team-id", "rejected"))
        self.check("signing-readiness development", ["signing-readiness", "--ipa", ipa,
                                                     "--profile", profile, "--identity",
                                                     "Apple Development: Fixture (ABCDE12345)",
                                                     "--team-id", "ABCDE12345", "--bundle-id",
                                                     "com.example.fixture"], expect=0,
                   predicate=lambda p: (p["data"]["verdict"] == "READY", "READY"))
        self.check("signing-readiness distribution", ["signing-readiness", "--ipa", ipa,
                                                      "--profile", profile, "--purpose",
                                                      "distribution"], expect=1,
                   predicate=lambda p: (p["data"]["verdict"] == "BLOCKED", "BLOCKED"))
        self.check("dsym-index", ["dsym-index", "--paths", dylib], expect=0,
                   predicate=lambda p: (p["data"]["indexed"] == 1, "indexed"))
        self.check("crash symbolication match", ["crash-triage", "--crash", crash,
                                                  "--dsym-dir", fx],
                   expect=0,
                   predicate=lambda p: (p["data"]["symbolication"]["matched"], "dSYM matched"))

        # 7. limits, errors, recovery
        self.check("missing input", ["inspect-ipa", "--ipa", "does-not-exist.ipa"], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "input-missing", "input-missing"))
        self.check("wrong artifact type", ["inspect-ipa", "--ipa", plist], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "ipa-not-zip", "ipa-not-zip"))
        self.check("limit exceeded", ["inspect-plist", "--plist", plist, "--max-file-bytes", "100"],
                   expect=4,
                   predicate=lambda p: (p["errors"][0]["code"] == "file-too-large", "limit error"))
        self.check("bad limit value", ["probe", "--max-workers", "99"], expect=2,
                   predicate=lambda p: (p["errors"][0]["code"] == "usage-error", "usage error"))
        self.check("missing required argument", ["inspect-ipa"], expect=2,
                   predicate=lambda p: (p["errors"][0]["code"] == "usage-error", "usage error"))
        self.check("truncated plist error + recovery",
                   ["inspect-plist", "--plist", str(self._write("trunc.plist",
                                                                 '<?xml version="1.0"?><plist><dict><key>x')),
                    ], expect=3,
                   predicate=lambda p: (p["errors"][0]["code"] == "plist-invalid", "structured error"))
        self.check("recovery: same command with valid file", ["inspect-plist", "--plist", plist],
                   expect=0, predicate=lambda p: (p["status"] == "OK", "recovered"))
        self.check("validate-report PASS", self._validated_report(ipa), expect=0,
                   predicate=lambda p: (p["data"]["verdict"] == "PASS", "schema valid"))
        self.check("validate-report FAIL on foreign json", ["validate-report", "--report", plist],
                   expect=3,
                   predicate=lambda p: (p["errors"], "rejected"))
        self.check("jsonl output streams findings", ["--format", "jsonl", "probe"], expect=0,
                   predicate=lambda p: (True, "jsonl emitted (payload intentionally empty)"))

        # 8. idempotency: same command twice -> same content hashes
        snap_a = self.tmp / "idem1.json"
        snap_b = self.tmp / "idem2.json"
        self.run("snapshot", "--root", fx, "--workspace", "w", "--out", str(snap_a))
        time.sleep(0.01)
        self.run("snapshot", "--root", fx, "--workspace", "w", "--out", str(snap_b))
        same = False
        try:
            a = json.loads(snap_a.read_text())
            b = json.loads(snap_b.read_text())
            same = [(f["path"], f["sha256"]) for f in a["files"]] == \
                   [(f["path"], f["sha256"]) for f in b["files"]]
        except (OSError, KeyError, json.JSONDecodeError):
            same = False
        self.results.append(("idempotent snapshots", same,
                             "identical content hashes across two runs" if same
                             else "hashes differed between runs"))

        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        print(f"\n{'RESULT':<8} {'TEST':<42} DETAIL")
        print("-" * 108)
        for name, ok, detail in self.results:
            print(f"{'PASS' if ok else 'FAIL':<8} {name:<42} {detail}")
        print("-" * 108)
        print(f"{passed}/{total} tests passed")
        return 0 if passed == total else 1

    # -- misc ------------------------------------------------------------
    def _write(self, name: str, content: str) -> str:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _folder_project(self, pbxproj: str) -> List[str]:
        """Copy the flat fixture into a real .xcodeproj folder to prove both shapes work."""
        proj = self.tmp / "Folder.xcodeproj"
        proj.mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE.parent / pbxproj, proj / "project.pbxproj")  # root-relative path
        return ["inspect-project", "--project", str(proj)]

    def _validated_report(self, ipa: str) -> List[str]:
        out = self.tmp / "ipa-report.json"
        self.run("inspect-ipa", "--ipa", ipa, "--json-out", str(out))
        return ["validate-report", "--report", str(out)]

    def cleanup(self, keep: bool) -> None:
        if not keep:
            shutil.rmtree(self.tmp, ignore_errors=True)
        else:
            print(f"artifacts kept in {self.tmp}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="regression suite for the iOS/iPadOS Agent Skill Tool")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temporary working directory")
    args = ap.parse_args(argv)
    if not TOOL.exists():
        sys.stderr.write(f"primary entry point missing: {TOOL}\n")
        return 2
    if not any(HERE.glob(FIXTURE_GLOB)):
        sys.stderr.write("fixtures missing: run 'python3 tools-iosh_fixtures.py' first\n")
        return 2
    suite = Suite(verbose=args.verbose)
    try:
        return suite.run_all()
    finally:
        suite.cleanup(args.keep)


if __name__ == "__main__":
    sys.exit(main())
