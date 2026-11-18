#!/usr/bin/env python3
"""tools-skillforge-selftest.py - end-to-end self test for the Aphelion Skill Forge.

Runs the forge through its real command line interface, checks exit codes and
structured output, then extracts the produced ZIP into a clean temporary skill
root beside a test SKILL.md and re-runs the extracted tool using only relative
paths. Nothing outside a temporary directory is modified.

Usage
    python3 tools-skillforge-selftest.py            # full suite
    python3 tools-skillforge-selftest.py --verbose  # echo each command result
Exit codes: 0 all tests passed, 1 a test failed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORGE = HERE / "tools-skillforge.py"
EXAMPLE = HERE / "asset-spec-example.md"
VERBOSE = "--verbose" in sys.argv

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    if VERBOSE or not ok:
        print("%-6s %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def forge(args, expect=0, stdin_text=None, cwd=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-B", os.fspath(FORGE)] + args,
                          input=stdin_text, capture_output=True, text=True,
                          timeout=300, env=env, cwd=cwd or os.fspath(HERE))
    if VERBOSE:
        print("$ forge %s -> rc=%d" % (" ".join(args), proc.returncode))
        head = (proc.stdout or "").strip().splitlines()[:6]
        for line in head:
            print("    %s" % line)
    return proc


def as_json(proc):
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def test_help_and_capabilities() -> None:
    proc = forge(["help"])
    record("help runs and lists commands", proc.returncode == 0 and "scaffold" in proc.stdout,
           "rc=%d" % proc.returncode)
    proc = forge(["capabilities"])
    payload = as_json(proc)
    record("capabilities returns structured output",
           proc.returncode == 0 and payload and len(payload.get("forge_commands", [])) == 8,
           "commands=%s" % (len(payload.get("forge_commands", [])) if payload else "n/a"))
    proc = forge(["capabilities", "--catalog-list", "--format", "json"])
    payload = as_json(proc)
    record("capability list includes the bundled catalog",
           bool(payload) and payload.get("categories") == 34,
           "categories=%s" % (payload or {}).get("categories"))
    proc = forge(["doctor", "--format", "json"])
    payload = as_json(proc)
    failed = [c["name"] for c in (payload or {}).get("checks", []) if not c["ok"]]
    record("doctor reports a healthy environment", proc.returncode == 0 and not failed,
           "failing=%s" % (",".join(failed) or "none"))


def test_extract_variants(tmp: Path) -> None:
    proc = forge(["extract", "--input", os.fspath(EXAMPLE)])
    payload = as_json(proc)
    record("extract reads the example specification",
           proc.returncode == 0 and payload and payload["items"] == 5,
           "items=%s" % (payload or {}).get("items"))

    html_path = tmp / "spec.html"
    html_path.write_text(
        "<html><head><style>.x{color:red}</style></head><body class='c' data-k='v'>"
        "<h2 class='h'>Field Operations Dispatch</h2>"
        "<p>Focus: Dispatch windows, technician safety, and service evidence.</p>"
        "<ol><li data-id='1'>Skill Name: Dispatch Window Optimizer</li>"
        "<p>Description: Allocates dispatch windows from travel time, part availability and "
        "technician certification, and lists every job it could not place with the reason.</p>"
        "<li data-id='2'>Skill Name: Safety Briefing Composer</li>"
        "<p>Description: Composes the pre-visit safety briefing from the job hazard profile and "
        "confirms the technician acknowledged each control before the window opens.</p>"
        "</ol></body></html>", encoding="utf-8")
    proc = forge(["extract", "--input", os.fspath(html_path)])
    payload = as_json(proc)
    names = [c["name"] for c in (payload or {}).get("capabilities", [])]
    record("extract ignores HTML tags, CSS and data attributes",
           proc.returncode == 0 and names == ["Dispatch Window Optimizer", "Safety Briefing Composer"],
           "names=%s" % names)

    md_path = tmp / "spec.md"
    md_path.write_text(
        "## Claims Evidence Review\n\nFocus: Source-backed claim checking.\n\n"
        "1. Skill Name: Claim Source Tracer\n"
        "Description: Traces every claim to a dated source and marks unsupported claims.\n\n"
        "2. Skill Name: Conflicting Source Comparator\n"
        "Description: Compares conflicting sources and reports which one is better evidenced.\n",
        encoding="utf-8")
    proc = forge(["extract", "--input", os.fspath(md_path)])
    payload = as_json(proc)
    record("extract handles markdown categories",
           proc.returncode == 0 and payload and payload["categories"]
           and payload["categories"][0]["name"] == "Claims Evidence Review",
           "categories=%s" % (payload or {}).get("categories"))

    proc = forge(["extract", "--input", "-"], stdin_text=md_path.read_text(encoding="utf-8"))
    payload = as_json(proc)
    record("extract reads stdin with --input -",
           proc.returncode == 0 and (payload or {}).get("items") == 2,
           "items=%s" % (payload or {}).get("items"))


def test_invalid_inputs(tmp: Path) -> None:
    proc = forge(["extract", "--input", os.fspath(tmp / "missing.md")])
    payload = as_json(proc)
    record("missing input returns E_INPUT_MISSING and exit 3",
           proc.returncode == 3 and (payload or {}).get("error", {}).get("code") == "E_INPUT_MISSING",
           "rc=%d" % proc.returncode)

    empty = tmp / "empty.md"
    empty.write_text("   \n\n\t\n", encoding="utf-8")
    proc = forge(["extract", "--input", os.fspath(empty)])
    payload = as_json(proc)
    record("empty input returns a structured error",
           proc.returncode == 3 and (payload or {}).get("error", {}).get("code") == "E_EMPTY_INPUT",
           "rc=%d code=%s" % (proc.returncode, ((payload or {}).get("error") or {}).get("code")))

    binary = tmp / "binary.bin"
    binary.write_bytes(b"\x00\x01\x02\x03")
    proc = forge(["extract", "--input", os.fspath(binary)])
    payload = as_json(proc)
    record("binary input returns E_BINARY_INPUT",
           proc.returncode == 3 and (payload or {}).get("error", {}).get("code") == "E_BINARY_INPUT",
           "rc=%d" % proc.returncode)

    proc = forge(["extract", "--input", os.fspath(tmp)])
    record("directory input is rejected", proc.returncode == 3, "rc=%d" % proc.returncode)

    big = tmp / "big.md"
    big.write_text("x" * 5000, encoding="utf-8")
    small_policy = tmp / "policy-small.json"
    small_policy.write_text(json.dumps({"defaults": {"max_input_bytes": 1000}}), encoding="utf-8")
    proc = forge(["extract", "--input", os.fspath(big), "--policy", os.fspath(small_policy)])
    payload = as_json(proc)
    record("input over the policy cap returns E_INPUT_TOO_LARGE",
           proc.returncode == 3
           and (payload or {}).get("error", {}).get("code") == "E_INPUT_TOO_LARGE",
           "rc=%d" % proc.returncode)

    proc = forge(["unify", "--input", os.fspath(EXAMPLE), "--generator", "cobol"])
    record("unknown generator is rejected", proc.returncode == 2, "rc=%d" % proc.returncode)


def test_unify(tmp: Path) -> None:
    proc = forge(["unify", "--input", os.fspath(EXAMPLE), "--format", "json"])
    payload = as_json(proc)
    spec = (payload or {}).get("spec", {})
    record("unify produces one unified specification",
           proc.returncode == 0
           and spec.get("name") == "turnaround-reporting-and-evidence"
           and len(spec.get("capabilities", [])) == 5,
           "name=%s caps=%s" % (spec.get("name"), len(spec.get("capabilities", []))))
    record("unify detects the approval trigger in the text",
           spec.get("approval_required") is True and bool(spec.get("approval_reasons")),
           "reasons=%s" % spec.get("approval_reasons"))

    proc = forge(["unify", "--input", os.fspath(EXAMPLE), "--name", "weekly-evidence-kit"])
    payload = as_json(proc)
    record("--name overrides the folder name",
           (payload or {}).get("spec", {}).get("name") == "weekly-evidence-kit",
           "name=%s" % (payload or {}).get("spec", {}).get("name"))

    proc = forge(["unify", "--input", os.fspath(EXAMPLE), "--name", "Bad Name"])
    record("invalid --name is rejected", proc.returncode != 0, "rc=%d" % proc.returncode)

    proc = forge(["unify", "--catalog", "7", "--format", "json"])
    payload = as_json(proc)
    spec = (payload or {}).get("spec", {})
    record("catalog category drives a unified spec",
           proc.returncode == 0 and len(spec.get("capabilities", [])) == 12
           and "presentation" in spec.get("name", ""),
           "name=%s caps=%s" % (spec.get("name"), len(spec.get("capabilities", []))))

    proc = forge(["unify", "--catalog", "999", "--format", "json"])
    payload = as_json(proc)
    record("unknown catalog number returns a structured error",
           proc.returncode == 2
           and (payload or {}).get("error", {}).get("code") == "E_CATALOG_SELECTION",
           "rc=%d" % proc.returncode)


def test_approval_gate(tmp: Path) -> None:
    out = tmp / "gated"
    proc = forge(["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out)])
    payload = as_json(proc)
    record("scaffold blocks unapproved high-risk generation with exit 4",
           proc.returncode == 4
           and (payload or {}).get("error", {}).get("code") == "E_APPROVAL_REQUIRED",
           "rc=%d" % proc.returncode)
    record("blocked generation writes no folder", not out.exists(),
           "folder exists=%s" % out.exists())

    proc = forge(["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out),
                  "--approve", "   "])
    record("a blank approval does not satisfy the gate", proc.returncode == 4,
           "rc=%d" % proc.returncode)

    proc = forge(["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out),
                  "--approve", "publishing or external distribution"],
                  "--format", "json" if False else None) if False else forge(
        ["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out),
         "--approve", "publishing or external distribution", "--format", "json"])
    payload = as_json(proc)
    record("approval is case-insensitive and unblocks the gate",
           proc.returncode == 0 and (payload or {}).get("verification", {}).get("passed") is True,
           "rc=%d" % proc.returncode)


def test_scaffold_and_run(tmp: Path) -> str:
    out = tmp / "delivery"
    zip_path = tmp / "aphelion-skill-tool.zip"
    proc = forge(["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out),
                  "--approve", "Publishing or external distribution", "--package",
                  "--zip", os.fspath(zip_path), "--checkpoint", "--format", "json"])
    payload = as_json(proc)
    folder = Path((payload or {}).get("folder", ""))
    record("scaffold writes and verifies a runnable folder",
           proc.returncode == 0 and folder.is_dir()
           and (payload or {}).get("verification", {}).get("passed") is True,
           "folder=%s" % folder.name)
    files = sorted(p.name for p in folder.glob("*")) if folder.is_dir() else []
    record("folder holds the four required files",
           files == ["asset-turnaround-reporting-and-evidence-manifest.json",
                     "knowledge-readme.md",
                     "tools-turnaround-reporting-and-evidence-checks.py",
                     "tools-turnaround-reporting-and-evidence.py"],
           "files=%s" % files)
    record("folder contains no SKILL.md and no subfolder",
           folder.is_dir() and not (folder / "SKILL.md").exists()
           and not [p for p in folder.iterdir() if p.is_dir()],
           "")
    record("generated tool was executed during verification",
           len((payload or {}).get("verification", {}).get("steps", [])) >= 6,
           "steps=%s" % len((payload or {}).get("verification", {}).get("steps", [])))
    record("package shape is valid",
           (payload or {}).get("package", {}).get("shape_ok") is True,
           "problems=%s" % (payload or {}).get("package", {}).get("problems"))

    # Re-running without --force must refuse rather than overwrite.
    proc = forge(["scaffold", "--input", os.fspath(EXAMPLE), "--out", os.fspath(out),
                  "--approve", "Publishing or external distribution"])
    payload = as_json(proc)
    record("existing output folder is protected without --force",
           proc.returncode == 2
           and (payload or {}).get("error", {}).get("code") == "E_OUTPUT_EXISTS",
           "rc=%d" % proc.returncode)

    # Drive the generated tool directly through its own CLI.
    tool = folder / ("tools-%s.py" % folder.name)
    run_dir = tmp / "run"
    brief = tmp / "brief.md"
    brief.write_text("# Brief\n\nWeekly turnaround review for the NCR logistics desk.\n",
                     encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", os.fspath(tool), "run", "--input",
                           os.fspath(brief), "--output-dir", os.fspath(run_dir),
                           "--approve", "Publishing or external distribution",
                           "--format", "text"],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    record("generated tool runs and writes its artefacts",
           proc.returncode == 0 and (run_dir / "report.md").exists()
           and (run_dir / "plan.json").exists(),
           "rc=%d" % proc.returncode)
    report = (run_dir / "report.md").read_text(encoding="utf-8") if (run_dir / "report.md").exists() else ""
    record("report lists steps, checks and declared artifacts",
           all(marker in report for marker in ("## Steps", "## Checks", "## Declared artifacts")),
           "len=%d" % len(report))

    proc = subprocess.run([sys.executable, "-B", os.fspath(tool), "validate", "--output-dir",
                           os.fspath(run_dir), "--format", "text"],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    record("generated tool validates its own output folder", proc.returncode == 0,
           "rc=%d" % proc.returncode)
    return os.fspath(folder)


def test_result_changes_with_input(tmp: Path) -> None:
    a = forge(["unify", "--input", os.fspath(EXAMPLE), "--format", "json"])
    other = tmp / "other.md"
    other.write_text(
        "# Slides Review\n\nFocus: Deck review.\n\n"
        "1. Skill Name: Slide Object Inventory Builder\n"
        "Description: Inventories every object on every slide with its editable type, and flags "
        "flattened artwork that should have stayed a native object.\n\n"
        "2. Skill Name: Protected Slide Enforcer\n"
        "Description: Enforces that protected slides stay untouched and reports any attempted "
        "change before the deck is handed back.\n", encoding="utf-8")
    b = forge(["unify", "--input", os.fspath(other), "--format", "json"])
    spec_a = (as_json(a) or {}).get("spec", {})
    spec_b = (as_json(b) or {}).get("spec", {})
    record("different input yields a different unified spec",
           spec_a.get("name") != spec_b.get("name")
           and spec_a.get("domains") != spec_b.get("domains"),
           "a=%s b=%s" % (spec_a.get("name"), spec_b.get("name")))
    record("matching input selects the presentation profile",
           spec_b.get("domains", [""])[0] == "presentation-engineering",
           "domains=%s" % spec_b.get("domains"))


def test_clean_room(tmp: Path, folder: str) -> None:
    """Extract the ZIP beside a test SKILL.md and re-run using only relative paths."""
    zip_path = tmp / "aphelion-skill-tool.zip"
    root = tmp / "clean-skill-root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "---\nname: test-parent-skill\ndescription: Temporary parent used to prove the Skill "
        "Tool folder sits beside a single root SKILL.md.\n---\n\nSee "
        "turnaround-reporting-and-evidence/knowledge-readme.md.\n", encoding="utf-8")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        bad = zf.testzip()
        zf.extractall(root)
    record("archive opens and passes its CRC test", bad is None, "bad=%s" % bad)
    record("archive places the folder directly at its root",
           all(n.startswith("turnaround-reporting-and-evidence/") for n in names)
           and len({n.split("/", 1)[0] for n in names}) == 1,
           "entries=%d" % len(names))
    record("archive contains no nested archive", not any(
        n.lower().endswith((".zip", ".tar", ".gz", ".7z")) for n in names), "")
    record("archive contains no SKILL.md", not any(
        n.lower().endswith("skill.md") for n in names), "")

    skill_root = root
    tool = skill_root / "turnaround-reporting-and-evidence" / (
        "tools-turnaround-reporting-and-evidence.py")
    proc = subprocess.run([sys.executable, "-B",
                           "turnaround-reporting-and-evidence/tools-turnaround-reporting-and-evidence.py",
                           "capabilities", "--format", "json"],
                          capture_output=True, text=True, timeout=120, cwd=os.fspath(skill_root),
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    payload = as_json(proc) if proc.stdout else None
    record("extracted tool runs from a clean skill root with relative paths",
           proc.returncode == 0 and (payload or {}).get("count") == 5,
           "rc=%d" % proc.returncode)

    brief_rel = "turnaround-reporting-and-evidence/../brief.md"
    (skill_root / "brief.md").write_text("# Brief\n\nClean-room re-run.\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B",
                           "turnaround-reporting-and-evidence/tools-turnaround-reporting-and-evidence.py",
                           "run", "--input", brief_rel, "--output-dir", "cleanroom-out",
                           "--approve", "Publishing or external distribution", "--format", "text"],
                          capture_output=True, text=True, timeout=120, cwd=os.fspath(skill_root),
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    record("extracted tool completes a run using relative paths",
           proc.returncode == 0 and (skill_root / "cleanroom-out" / "report.md").exists(),
           "rc=%d" % proc.returncode)

    skill_md = list(skill_root.rglob("SKILL.md"))
    record("clean root holds exactly one SKILL.md, outside the Skill Tool",
           len(skill_md) == 1 and skill_md[0].parent == skill_root,
           "found=%s" % [str(p.relative_to(skill_root)) for p in skill_md])

    depth_ok = all(len(Path(n).parts) <= 3 for n in names)
    record("every archive path is at most three levels deep", depth_ok, "")


def main() -> int:
    if not FORGE.exists():
        print("forge entry point missing: %s" % FORGE.name)
        return 1
    print("Aphelion Skill Forge self test")
    print("python: %s" % sys.version.split()[0])
    print("")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        try:
            test_help_and_capabilities()
            test_extract_variants(tmp)
            test_invalid_inputs(tmp)
            test_unify(tmp)
            test_approval_gate(tmp)
            folder = test_scaffold_and_run(tmp)
            test_result_changes_with_input(tmp)
            test_clean_room(tmp, folder)
        except subprocess.TimeoutExpired as exc:
            record("suite completed without a timeout", False, str(exc))
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = [r for r in RESULTS if not r["ok"]]
    print("")
    print("%d passed, %d failed, %d total" % (passed, len(failed), len(RESULTS)))
    for row in failed:
        print("  FAIL %s - %s" % (row["name"], row["detail"]))
    leftovers = [p.name for p in HERE.glob("__pycache__")]
    if leftovers:
        print("note: local %s present and excluded from delivery" % ", ".join(leftovers))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
