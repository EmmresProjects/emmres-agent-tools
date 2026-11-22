#!/usr/bin/env python3
"""Self-test suite for the app-factory-delivery Skill Tool.

Runs the real CLI (``tools-app-factory.py``) as a subprocess against temporary
fixtures and checks exit codes, structured output, generated artifacts, gate
behaviour, approval rules, error handling and one recovery path.

    python3 tools-selftest.py            # run everything, exit 1 on failure
    python3 tools-selftest.py --keep     # keep the temporary work directory
    python3 tools-selftest.py --verbose  # echo each command line

Standard library only. The suite never touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "tools-app-factory.py")
NORMALIZER = os.path.join(HERE, "tools-catalog-normalizer.py")

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"[{marker}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line, flush=True)


def check(name: str, condition: bool, detail: str = "") -> bool:
    record(name, "pass" if condition else "fail", detail)
    return condition


class Runner:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def __call__(self, args: list[str], workdir: str) -> tuple[int, str, str]:
        command = [sys.executable, CLI, *args]
        if self.verbose:
            print("    $ " + " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=workdir, capture_output=True, text=True, timeout=300, check=False)
        return completed.returncode, completed.stdout, completed.stderr

    def json(self, args: list[str], workdir: str) -> tuple[int, dict | None, str]:
        code, out, err = self(["--format", "json", *args], workdir)
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
        return code, payload, err


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")
    return path


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


HTML_FIXTURE = """<html><head><style>.x{color:red}</style><script>var a=1;</script></head><body>
<h2 class="cat" data-id="c1">1. Data Platform Engineering</h2>
<ul>
  <li data-skill="s1"><strong>Skill Name:</strong> Streaming Pipeline Engineer<br>
      <em>Description:</em> Builds event ingestion, exactly-once processing and replayable pipelines.</li>
  <li data-skill="s2"><strong>Skill Name:</strong> Warehouse Modeling Engineer<br>
      <em>Description:</em> Designs star schemas, slowly changing dimensions, and incremental models.</li>
</ul>
<h2>2. Report Automation</h2>
<ul>
  <li><b>Skill Name:</b> Scheduled Report Builder</li>
  <li><b>Description:</b> Generates recurring reports with templates and failure alerts.</li>
</ul>
</body></html>
"""

MARKDOWN_FIXTURE = """# Localization and Migration

1. **Skill Name:** String Externalization Engineer
   **Description:** Extracts user-facing strings into resource bundles with placeholders.

## Compatibility Hardening

- Capability: Legacy Browser Adapter — Adapts a modern codebase to older engines with documented degradation.
"""

PLAIN_FIXTURE = """1. Secure Delivery Operations
1. Skill Name: Secret Rotation Engineer
Description: Rotates credentials on a schedule with an audited break-glass path.
2. Skill Name: Pipeline Permission Auditor
Description: Reviews workflow permissions and pins third-party actions.

2. Release Evidence
1. Skill Name: Checksum and Provenance Engineer
Description: Produces signed checksums and provenance metadata for each release artifact.
"""

REQUIREMENTS = """# Epic: Inventory Service

- As a warehouse operator, I want to record stock movements so that counts stay accurate.
  AC1: Given a valid movement, when it is submitted, then it is stored exactly once.
  AC2: Given a negative quantity, when it is submitted, then it is rejected with a field error.
- As an administrator, I want an audit trail so that every change can be reviewed.
- The service must enforce authentication and authorization, keep p95 latency under 250ms, meet WCAG AA on the operator screens, and log structured security events.
- Data must survive a restart, and a restore drill must be possible.
Assumption: the service runs on a single node for the first release.
Question: which identity provider is available?
"""

APP_PY = '''"""Fixture service module used by the self-test."""

import argparse
import sys


def apply_movement(quantity: int) -> int:
    """Return the new stock level; reject negative quantities."""
    if quantity < 0:
        raise ValueError("quantity must be zero or positive")
    return quantity


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="inventory")
    parser.add_argument("quantity", type=int)
    args = parser.parse_args(argv)
    try:
        print(apply_movement(args.quantity))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

TEST_PY = '''from app import apply_movement


def test_apply_movement_positive():
    assert apply_movement(5) == 5


def test_apply_movement_rejects_negative():
    try:
        apply_movement(-1)
    except ValueError:
        return
    raise AssertionError("negative quantity was accepted")
'''

COMMITS = """feat(inventory): record stock movements
fix(inventory): reject negative quantities with exit code 2
docs: describe the run and test commands
"""

EVIDENCE = {
    "reviewer": "selftest",
    "controls": {
        "SEC-01": {"status": "pass", "note": "no credentials in tree"},
        "SEC-02": {"status": "not_applicable", "note": "no runtime secrets"},
        "SEC-03": {"status": "pass", "note": "token auth reviewed, 30 minute lifetime"},
        "SEC-04": {"status": "pass", "note": "authorization matrix recorded per action"},
        "SEC-16": {"status": "pass", "note": "rate limits set per endpoint class"},
        "SEC-17": {"status": "not_applicable", "note": "no file uploads in this release"},
        "SEC-19": {"status": "pass", "note": "sessions expire and rotate on privilege change"},
        "SEC-05": {"status": "pass", "note": "argument type validation"},
        "SEC-06": {"status": "not_applicable", "note": "no SQL, shell or template construction"},
        "SEC-08": {"status": "not_applicable", "note": "no network traffic"},
        "SEC-09": {"status": "not_applicable", "note": "no stored personal data"},
        "SEC-10": {"status": "pass", "note": "no personal data collected"},
        "SEC-11": {"status": "not_applicable", "note": "no telemetry"},
        "SEC-12": {"status": "pass", "note": "standard library only"},
        "SEC-13": {"status": "pass", "note": "manifest and checksums written by stage packaging"},
        "SEC-14": {"status": "pass", "note": "argparse usage errors only"},
        "SEC-15": {"status": "pass", "note": "structured stderr diagnostics"},
        "SEC-18": {"status": "pass", "note": "secure defaults, no unsafe flags"},
        "SEC-20": {"status": "not_applicable", "note": "no cryptography"},
        "SEC-21": {"status": "pass", "note": "workflow permissions reviewed"},
        "SEC-22": {"status": "pass", "note": "restore drill documented in release notes"},
        "SEC-23": {"status": "pass", "note": "standard library only"},
        "SEC-25": {"status": "pass", "note": "no external dependencies"},
        "SEC-26": {"status": "pass", "note": "keyboard-only CLI covered by tests"},
    },
}


def build_project(root: str) -> str:
    project = os.path.join(root, "inventory")
    write(os.path.join(project, "app.py"), APP_PY)
    write(os.path.join(project, "test_app.py"), TEST_PY)
    write(os.path.join(project, "README.md"), "# Inventory fixture\n\nRun: `python3 app.py 5`\n\nTest: `python3 -m pytest -q`\n")
    write(os.path.join(project, ".github", "workflows", "ci.yml"), "name: ci\non: [push]\npermissions:\n  contents: read\n")
    return project


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools-selftest.py", description="Run the app-factory-delivery validation suite.")
    parser.add_argument("--keep", action="store_true", help="keep the temporary work directory")
    parser.add_argument("--verbose", action="store_true", help="echo each command line")
    args = parser.parse_args(argv)

    run = Runner(verbose=args.verbose)
    root = tempfile.mkdtemp(prefix="app-factory-selftest-")
    pytest_available = importlib.util.find_spec("pytest") is not None

    # ---- 1. help and capability discovery ---------------------------------
    code, out, err = run(["--help"], root)
    check("help exits 0", code == 0, f"exit={code}")
    for keyword in ["capabilities", "stage", "verify", "boundary", "status", "catalog", "route"]:
        check(f"help lists '{keyword}'", keyword in out)

    code, payload, err = run.json(["--format", "json", "capabilities"], root)
    ok = check("capabilities --format json parses", code == 0 and payload is not None, err.strip()[:120])
    if ok and payload:
        check("capabilities reports 30 catalog categories", payload.get("catalog", {}).get("categories") == 30, str(payload.get("catalog")))
        check("capabilities reports 244 catalog skills", payload.get("catalog", {}).get("skills") == 244, str(payload.get("catalog")))
        check("capabilities lists 12+ operations", len(payload.get("capabilities", [])) >= 12, str(len(payload.get("capabilities", []))))
        check("capabilities lists 11 presets", len(payload.get("presets", [])) == 11, str(len(payload.get("presets", []))))

    # ---- 2. catalog normalization: HTML, Markdown, plain text -------------
    html_path = write(os.path.join(root, "inputs", "list.html"), HTML_FIXTURE)
    md_path = write(os.path.join(root, "inputs", "list.md"), MARKDOWN_FIXTURE)
    plain_path = write(os.path.join(root, "inputs", "list.txt"), PLAIN_FIXTURE)

    for label, path, expect_cats, expect_skills in [
        ("html", html_path, 2, 3),
        ("markdown", md_path, 2, 2),
        ("plain text", plain_path, 2, 3),
    ]:
        out_path = os.path.join(root, "inputs", f"catalog-{label.replace(' ', '-')}.json")
        code, stdout, err = run(["catalog", "import", "--input", path, "--out", out_path], root)
        ok = check(f"catalog import ({label}) exits 0", code == 0, err.strip()[:160])
        if ok and os.path.isfile(out_path):
            with open(out_path, "r", encoding="utf-8") as handle:
                catalog = json.load(handle)
            check(
                f"catalog import ({label}) finds {expect_cats} categories / {expect_skills} skills",
                catalog["counts"]["categories"] == expect_cats and catalog["counts"]["skills"] == expect_skills,
                str(catalog["counts"]),
            )
            names = [skill["name"] for category in catalog["categories"] for skill in category["skills"]]
            check(
                f"catalog import ({label}) strips markup and numbering",
                all("<" not in name and not name[0].isdigit() for name in names),
                str(names),
            )
            check(
                f"catalog import ({label}) captures descriptions",
                all(skill["description"] for category in catalog["categories"] for skill in category["skills"]),
            )

    normalized = os.path.join(root, "inputs", "catalog-html.json")
    merge_out = os.path.join(root, "inputs", "catalog-merged.json")
    code, _, err = run(["catalog", "import", "--input", md_path, "--merge", normalized, "--out", merge_out], root)
    ok = check("catalog import --merge exits 0", code == 0, err.strip()[:160])
    if ok:
        with open(merge_out, "r", encoding="utf-8") as handle:
            merged = json.load(handle)
        check("merge adds the second list's skills", merged["counts"]["skills"] == 5, str(merged["counts"]))
        check("merge reports what it added", merged.get("merge_report", {}).get("added_skills") == 2, str(merged.get("merge_report")))

    # ---- 3. catalog query, route, boundary --------------------------------
    code, payload, err = run.json(["catalog", "search", "--query", "offline first sync conflict", "--limit", "5"], root)
    ok = check("catalog search exits 0", code == 0 and payload is not None, err.strip()[:160])
    if ok and payload:
        check("catalog search ranks the offline-first skill first", payload["results"][0]["name"] == "Mobile Offline-First Engineer", payload["results"][0]["name"])

    code, payload, err = run.json(["catalog", "show", "--skill", "debugger and profiler"], root)
    check("catalog show resolves a fragment", code == 0 and payload is not None and payload["skill"]["slug"] == "debugger-and-profiler-toolsmith", err.strip()[:120])

    code, payload, err = run.json(["catalog", "list", "--category", "29", "--brief"], root)
    check("catalog list resolves a category index", code == 0 and payload is not None and payload["categories"][0]["skill_count"] == 8, err.strip()[:120])

    code, payload, err = run.json(["route", "--request", "kotlin android app with offline sync", "--platform", "mobile", "--limit", "6"], root)
    ok = check("route exits 0", code == 0 and payload is not None, err.strip()[:160])
    if ok and payload:
        check("route recommends the android preset", payload["presets"][0]["key"] == "android-kotlin", str(payload["presets"][:2]))
        check("route returns ranked skills with explanations", all(item["matched_terms"] for item in payload["skills"]), str(len(payload["skills"])))
        check("route composes the six factory stages", len(payload["stage_composition"]) == 6, str(len(payload["stage_composition"])))

    code, payload, err = run.json(["route", "--request", "rust cli that cross-compiles", "--language", "rust"], root)
    check("route handles a language without a preset", code == 0 and payload is not None, err.strip()[:120])

    code, payload, err = run.json(["boundary", "--preset", "flutter", "--format", "json"], root)
    ok = check("boundary reports a missing toolchain", code in {0, 6} and payload is not None, f"exit={code}")
    if ok and payload:
        check("boundary states an escalation for the missing executable", bool(payload["escalations"]) == (not payload["can_verify_here"]), str(payload["escalations"][:1]))

    # ---- 4. full factory pipeline ----------------------------------------
    project = build_project(root)
    evidence_path = write(os.path.join(project, "evidence.json"), json.dumps(EVIDENCE, indent=2))
    commits_path = write(os.path.join(project, "commits.txt"), COMMITS)
    requirements_path = write(os.path.join(project, "requirements.md"), REQUIREMENTS)

    code, out, err = run(["init", "--project-dir", project, "--name", "Inventory", "--preset", "python-service", "--target-toolchain", "python3.13"], root)
    check("init exits 0 on an existing project tree", code == 0, err.strip()[:160])
    check("init writes the state file", os.path.isfile(os.path.join(project, ".factory-state.json")))

    code, _, err = run(["stage", "requirements", "--project-dir", project, "--input", requirements_path], root)
    backlog_path = os.path.join(project, "factory", "requirements-backlog.json")
    ok = check("stage requirements exits 0", code == 0 and os.path.isfile(backlog_path), err.strip()[:160])
    if ok:
        with open(backlog_path, "r", encoding="utf-8") as handle:
            backlog = json.load(handle)
        check("backlog captures the supplied acceptance criteria", any(not ac["generated"] for story in backlog["stories"] for ac in story["acceptance_criteria"]))
        check("backlog derives stories from 'As a ...' lines", len(backlog["stories"]) >= 3, str(len(backlog["stories"])))
        check("backlog records NFR coverage", sum(1 for entry in backlog["nfr_coverage"].values() if entry["addressed"]) >= 3, str([k for k, v in backlog["nfr_coverage"].items() if v["addressed"]]))

    for stage_args, artifact in [
        (["stage", "architecture", "--project-dir", project], "architecture-decision.json"),
        (["stage", "tests", "--project-dir", project], "test-strategy.json"),
        (["stage", "security", "--project-dir", project, "--evidence", evidence_path], "security-review.json"),
        (["stage", "packaging", "--project-dir", project, "--commits", commits_path, "--rollback-note", "Reinstall the previous artefact."], "release-manifest.json"),
    ]:
        code, out, err = run(stage_args, root)
        check(f"{stage_args[1]} stage exits 0", code == 0 and os.path.isfile(os.path.join(project, "factory", artifact)), err.strip()[:160])

    architecture_path = os.path.join(project, "factory", "architecture-decision.json")
    if os.path.isfile(architecture_path):
        with open(architecture_path, "r", encoding="utf-8") as handle:
            architecture = json.load(handle)
        check("architecture ranks candidate stacks", len(architecture["stack_decision"]["candidates"]) >= 3)
        check("architecture uses the backend component profile", architecture["component_set"] == "backend", architecture.get("component_set"))
        check("architecture records a resolved toolchain version", architecture["version_policy"]["target_version"] == "python3.13")

    manifest_path = os.path.join(project, "factory", "release-manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        hashed_paths = {entry["path"] for entry in manifest["files"]}
        check("manifest hashes the release set", "app.py" in hashed_paths and "test_app.py" in hashed_paths, str(sorted(hashed_paths)))
        check("manifest excludes factory artifacts", not any(path.startswith("factory/") for path in hashed_paths))
        check("manifest excludes the state file", ".factory-state.json" not in hashed_paths)
        on_disk = sha256(os.path.join(project, "app.py"))
        recorded = next(entry["sha256"] for entry in manifest["files"] if entry["path"] == "app.py")
        check("manifest checksum matches the file on disk", on_disk == recorded)

    notes_path = os.path.join(project, "factory", "release-notes.md")
    if os.path.isfile(notes_path):
        with open(notes_path, "r", encoding="utf-8") as handle:
            notes = handle.read()
        check("release notes apply a minor bump for features", "1.0.0 -> 1.1.0" in notes or "Previous version: 1.0.0" in notes, notes.splitlines()[0])
        check("release notes list conventional changes", "feat" in notes and "fix" in notes)

    # ---- 5. verification approval flow -----------------------------------
    code, payload, err = run.json(["verify", "--project-dir", project, "--dry-run"], root)
    ok = check("verify --dry-run exits 0", code == 0 and payload is not None, err.strip()[:160])
    token = None
    if ok and payload:
        token = payload.get("report", {}).get("token")
        check("dry run issues an approval token", bool(token) and token.startswith("APP-"), str(token))
        check("dry run executes nothing", payload["report"]["mode"] == "dry-run" and payload["report"]["results"] == [])

    code, _, err = run(["verify", "--project-dir", project], root)
    check("verify without approval exits 5", code == 5, f"exit={code}")
    check("unapproved verify explains the token", "APP-" in err, err.strip()[:120])

    code, _, err = run(["verify", "--project-dir", project, "--approve", "APP-BADTOKEN"], root)
    check("verify rejects a wrong token", code == 5, f"exit={code}")

    code, _, err = run(["verify", "--project-dir", project, "--extra-command", "curl https://example.com", "--dry-run"], root)
    check("verify rejects a non-allowlisted executable", code == 2 and "allowlist" in err, err.strip()[:120])

    code, _, err = run(["verify", "--project-dir", project, "--extra-command", "python3 -c \"print(1)\" && whoami", "--dry-run"], root)
    check("verify rejects shell chaining", code == 2 and "shell fragment" in err, err.strip()[:120])

    if token and pytest_available:
        code, payload, err = run.json(["verify", "--project-dir", project, "--approve", token], root)
        ok = check("verify executes with the exact token", code == 0 and payload is not None, err.strip()[:160])
        if ok and payload:
            report = payload["report"]
            check("verification report records real command results", all(item["attempts"] >= 1 for item in report["results"]), str([(i["id"], i["status"]) for i in report["results"]]))
            check("verification passed", report["pass"] is True, report["detail"])
    elif token:
        record("verify executes with the exact token", "skip", "pytest is not installed; required commands cannot pass")

    # ---- 6. readiness gate, drift detection and recovery ------------------
    code, out, err = run(["stage", "readiness", "--project-dir", project, "--accept-advisories"], root)
    readiness_path = os.path.join(project, "factory", "readiness-report.json")
    ok = check("stage readiness exits 0", code == 0 and os.path.isfile(readiness_path), err.strip()[:160])
    if ok:
        with open(readiness_path, "r", encoding="utf-8") as handle:
            readiness = json.load(handle)
        check("readiness score is above the pass threshold", readiness["score"] >= readiness["pass_threshold"], f"score={readiness['score']}")
        if pytest_available and not readiness["verification_stale"]:
            check("readiness reports release_ready", readiness["release_ready"] is True, str(readiness["blockers"])[:160])
        else:
            check("readiness blocks on a stale or missing verification run", any(b["id"] in {"verification_current", "verification_stale"} for b in readiness["blockers"]), str(readiness["blockers"])[:200])

    # drift: change an artifact, then readiness must flag instability
    backlog_file = os.path.join(project, "factory", "requirements-backlog.md")
    if os.path.isfile(backlog_file):
        with open(backlog_file, "a", encoding="utf-8") as handle:
            handle.write("\n<!-- edited after the stage ran -->\n")
        code, out, err = run(["stage", "readiness", "--project-dir", project, "--accept-advisories", "--format", "json"], root)
        drifted = None
        if code in {0, 3}:
            try:
                drifted = json.loads(out)["gate"]
            except (json.JSONDecodeError, KeyError):
                drifted = None
        check("readiness detects artifact drift", bool(drifted) and "artifacts_stable" in drifted.get("failed", []), str(drifted))

    # recovery: re-run the stage and confirm the tree is stable again
    code, _, err = run(["stage", "requirements", "--project-dir", project, "--input", requirements_path], root)
    code2, out2, _ = run(["stage", "readiness", "--project-dir", project, "--accept-advisories", "--format", "json"], root)
    recovered = False
    if code2 in {0, 3}:
        try:
            recovered = "artifacts_stable" not in json.loads(out2)["gate"]["failed"]
        except (json.JSONDecodeError, KeyError):
            recovered = False
    check("recovery: re-running the stage restores artifact stability", code == 0 and recovered)

    # ---- 7. error handling ------------------------------------------------
    code, _, err = run(["init", "--project-dir", os.path.join(root, "bad"), "--preset", "not-a-preset"], root)
    check("unknown preset exits 2 with a hint", code == 2 and "hint:" in err, err.strip().splitlines()[0] if err.strip() else "")

    code, _, err = run(["stage", "tests", "--project-dir", os.path.join(root, "empty-project")], root)
    check("missing state exits 4 with a hint", code == 4 and "hint:" in err, err.strip().splitlines()[0] if err.strip() else "")

    code, _, err = run(["route", "--request", ""], root)
    check("empty route request exits 2", code == 2 and "route needs" in err, err.strip().splitlines()[0] if err.strip() else "")

    bad_evidence = write(os.path.join(root, "inputs", "bad-evidence.json"), json.dumps({"controls": {"SEC-01": {"status": "maybe"}}}))
    code, _, err = run(["stage", "security", "--project-dir", project, "--evidence", bad_evidence], root)
    check("invalid evidence status is rejected", code == 2 and "invalid status" in err, err.strip()[:120])

    failing_evidence = write(
        os.path.join(root, "inputs", "failing-evidence.json"),
        json.dumps({"controls": {"SEC-01": {"status": "fail", "note": "credential committed"}, "SEC-02": {"status": "fail", "note": "key logged"}}}),
    )
    code, out, err = run(["stage", "security", "--project-dir", project, "--evidence", failing_evidence, "--strict"], root)
    check("strict mode exits 3 when a blocker fails", code == 3 and "gate: FAIL" in out, f"exit={code}")

    sensitive_project = os.path.join(root, "sensitive")
    write(os.path.join(sensitive_project, ".env"), "API_KEY=secret\n")
    write(os.path.join(sensitive_project, "README.md"), "# sensitive fixture\n")
    run(["init", "--project-dir", sensitive_project, "--name", "Sensitive", "--preset", "web-static"], root)
    code, out, err = run(["stage", "packaging", "--project-dir", sensitive_project, "--bump", "none", "--strict"], root)
    check("packaging flags credential-like files", code == 3 and "sensitive_files" in out and ".env" in out, f"exit={code}")

    code, out, err = run(["status", "--project-dir", project], root)
    check("status reports stage progress", code == 0 and "readiness" in out and "verification" in out, err.strip()[:120])

    # ---- 8. determinism and idempotence ----------------------------------
    first = os.path.join(root, "det-a")
    second = os.path.join(root, "det-b")
    for target in (first, second):
        write(os.path.join(target, "app.py"), APP_PY)
        write(os.path.join(target, "README.md"), "# det\n")
    digests = []
    for target in (first, second):
        run(["init", "--project-dir", target, "--name", "Det", "--preset", "python-cli"], root)
        run(["stage", "requirements", "--project-dir", target, "--input", requirements_path], root)
        with open(os.path.join(target, "factory", "requirements-backlog.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        digests.append(
            (
                payload["counts"] if "counts" in payload else None,
                len(payload["stories"]),
                [story["title"] for story in payload["stories"]],
                [ac["text"] for story in payload["stories"] for ac in story["acceptance_criteria"]],
            )
        )
    check("same input produces the same backlog", digests[0] == digests[1])

    # ---- summary ---------------------------------------------------------
    passed = sum(1 for _, status, _ in RESULTS if status == "pass")
    failed = sum(1 for _, status, _ in RESULTS if status == "fail")
    skipped = sum(1 for _, status, _ in RESULTS if status == "skip")
    print("-" * 70)
    print(f"{passed} passed, {failed} failed, {skipped} skipped, {len(RESULTS)} checks")
    if failed:
        print("\nfailed checks:")
        for name, status, detail in RESULTS:
            if status == "fail":
                print(f"  - {name} {detail}")
    if args.keep:
        print(f"work directory kept: {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
