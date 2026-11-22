#!/usr/bin/env python3
"""Standalone package/ZIP compatibility auditor and clean-root rerunner.

Can be invoked directly or loaded by tools-offline-research.py for its
validate-package command. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

FOLDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z0-9]+$")
PRIMARY = "tools-offline-research.py"
KNOWLEDGE = "knowledge-readme.md"
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war"}
REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9._-])(?:(?:offline-research-writing-translation)/)?((?:tools|knowledge|asset)-[a-z0-9]+(?:[-.][a-z0-9]+)*\.[a-z0-9]+)"
)


class AuditError(Exception):
    pass


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def audit_references(folder: Path) -> dict[str, Any]:
    config_path = folder / "asset-default-config.json"
    config = json.loads(_text(config_path) or "{}")
    configured = list(config.get("asset_paths", {}).values())
    references: dict[str, set[str]] = {}
    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.suffix.lower() not in {".py", ".md", ".json", ".csv", ".jsonl", ".txt"}:
            continue
        found = set(REFERENCE_RE.findall(_text(item)))
        if found:
            references[item.name] = found
    all_refs = set(configured)
    for names in references.values():
        all_refs.update(names)
    missing = sorted(name for name in all_refs if not (folder / name).is_file())
    return {
        "configured_asset_paths": configured,
        "reference_count": len(all_refs),
        "references_by_file": {k: sorted(v) for k, v in references.items()},
        "missing": missing,
        "passed": not missing,
    }


def audit_folder(folder: str | Path, expected_name: str | None = None) -> dict[str, Any]:
    root = Path(folder).resolve()
    if not root.is_dir():
        raise AuditError(f"Skill Tool folder does not exist: {root.name}")
    name = expected_name or root.name
    if not FOLDER_RE.fullmatch(name):
        raise AuditError("Skill Tool folder name must be lowercase and hyphenated.")
    files = [p for p in sorted(root.iterdir()) if p.is_file()]
    dirs = [p.name for p in sorted(root.iterdir()) if p.is_dir()]
    names = [p.name for p in files]
    unprefixed = [n for n in names if not n.startswith(("tools-", "knowledge-", "asset-"))]
    bad_names = [n for n in names if not FILE_RE.fullmatch(n)]
    nested_archives = [n for n in names if Path(n).suffix.lower() in ARCHIVE_EXT]
    knowledge = [n for n in names if n.startswith("knowledge-")]
    tools = [n for n in names if n.startswith("tools-")]
    assets = [n for n in names if n.startswith("asset-")]
    references = audit_references(root)
    checks = {
        "folder_name_valid": bool(FOLDER_RE.fullmatch(name)),
        "no_skill_md": "SKILL.md" not in names,
        "no_internal_subfolders": not dirs,
        "no_unclassified_files": not unprefixed,
        "filename_policy": not bad_names,
        "exactly_one_knowledge_file": knowledge == [KNOWLEDGE],
        "primary_entry_point_present": PRIMARY in names and (root / PRIMARY).is_file(),
        "at_least_one_tools_file": bool(tools),
        "no_nested_archives": not nested_archives,
        "references_resolve": references["passed"],
    }
    return {
        "folder": name,
        "file_count": len(files),
        "tool_files": tools,
        "knowledge_files": knowledge,
        "asset_files": assets,
        "internal_subfolders": dirs,
        "unclassified_files": unprefixed,
        "bad_filenames": bad_names,
        "nested_archives": nested_archives,
        "references": references,
        "checks": checks,
        "passed": all(checks.values()),
    }


def audit_zip(zip_path: str | Path, expected_name: str | None = None) -> dict[str, Any]:
    path = Path(zip_path).resolve()
    if not path.is_file():
        raise AuditError(f"ZIP does not exist: {path.name}")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        bad_zip = archive.testzip()
    if bad_zip:
        raise AuditError(f"ZIP has a corrupt member: {bad_zip}")
    roots = sorted({n.split("/")[0] for n in names if n})
    if expected_name and roots != [expected_name]:
        raise AuditError("ZIP root does not contain exactly the expected Skill Tool folder.", )
    if len(roots) != 1:
        raise AuditError("ZIP must contain exactly one Skill Tool folder at its root.")
    folder_name = roots[0]
    if not FOLDER_RE.fullmatch(folder_name):
        raise AuditError("ZIP root folder name is not lowercase and hyphenated.")
    root_files = [n for n in names if "/" not in n.rstrip("/") and n and not n.endswith("/")]
    file_entries = [n for n in names if not n.endswith("/") and n.startswith(folder_name + "/")]
    relative = [n[len(folder_name) + 1 :] for n in file_entries]
    internal_dirs = sorted({n for n in relative if "/" in n})
    nested_archives = [n for n in file_entries if Path(n).suffix.lower() in ARCHIVE_EXT]
    bad_paths = [n for n in names if n.startswith(("/", "\\")) or ".." in Path(n).parts or re.match(r"^[A-Za-z]:", n)]
    leaf_names = [Path(n).name for n in file_entries]
    unprefixed = [n for n in leaf_names if not n.startswith(("tools-", "knowledge-", "asset-"))]
    knowledge = [n for n in leaf_names if n.startswith("knowledge-")]
    tools = [n for n in leaf_names if n.startswith("tools-")]
    assets = [n for n in leaf_names if n.startswith("asset-")]
    checks = {
        "one_root_folder": len(roots) == 1,
        "no_root_wrapper_files": not root_files,
        "folder_directly_at_root": all(n.startswith(folder_name + "/") for n in file_entries),
        "no_internal_subfolders": not internal_dirs,
        "no_skill_md": "SKILL.md" not in leaf_names,
        "no_nested_archives": not nested_archives,
        "no_unsafe_paths": not bad_paths,
        "no_unclassified_files": not unprefixed,
        "exactly_one_knowledge_file": knowledge == [KNOWLEDGE],
        "primary_entry_point_present": PRIMARY in leaf_names,
        "at_least_one_tools_file": bool(tools),
    }
    return {
        "zip": path.name,
        "root_folders": roots,
        "root_files": root_files,
        "file_count": len(file_entries),
        "tool_files": sorted(set(tools)),
        "knowledge_files": sorted(set(knowledge)),
        "asset_files": sorted(set(assets)),
        "internal_subfolders": internal_dirs,
        "nested_archives": nested_archives,
        "unsafe_paths": bad_paths,
        "checks": checks,
        "passed": all(checks.values()),
    }


def extract_and_rerun(zip_path: str | Path, extract_to: str | Path, force: bool = False) -> dict[str, Any]:
    zip_file = Path(zip_path).resolve()
    root = Path(extract_to).resolve()
    if root.exists() and any(root.iterdir()) and not force:
        raise AuditError("Extraction root is not empty; choose another --extract-to or pass --force.")
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_file, "r") as archive:
        archive.extractall(root)
    zip_audit = audit_zip(zip_file)
    folder_name = zip_audit["root_folders"][0]
    (root / "SKILL.md").write_text("# Test parent skill root\n\nOne test SKILL.md beside the extracted Skill Tool folder.\n", encoding="utf-8")
    primary_rel = Path(folder_name) / PRIMARY
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    import os
    full_env = dict(os.environ)
    full_env.update(env)
    help_run = subprocess.run(
        [sys.executable, str(primary_rel), "help"],
        cwd=str(root),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    cap_run = subprocess.run(
        [sys.executable, str(primary_rel), "capabilities", "--limit", "3", "--format", "json"],
        cwd=str(root),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    cap_payload: dict[str, Any] = {}
    try:
        cap_payload = json.loads(cap_run.stdout)
    except json.JSONDecodeError:
        cap_payload = {"parse_error": cap_run.stdout[:500], "stderr": cap_run.stderr[:500]}
    combined = (help_run.stdout + help_run.stderr + cap_run.stdout + cap_run.stderr)
    structure = {
        "test_skill_md_at_root": (root / "SKILL.md").is_file(),
        "skill_tool_folder_at_root": (root / folder_name).is_dir(),
        "skill_md_inside_tool_folder": (root / folder_name / "SKILL.md").exists(),
        "primary_exists": (root / primary_rel).is_file(),
        "relative_command_used": not str(primary_rel).startswith("/"),
        "no_absolute_path_in_output": "/home/user/" not in combined,
    }
    return {
        "extract_root": str(root),
        "folder_name": folder_name,
        "zip_audit": zip_audit,
        "help": {"exit_code": help_run.returncode, "first_line": help_run.stdout.splitlines()[0] if help_run.stdout else ""},
        "capabilities": {"exit_code": cap_run.returncode, "ok": cap_payload.get("ok"), "capability_count": cap_payload.get("result", {}).get("capability_count"), "returned_count": len(cap_payload.get("result", {}).get("capabilities", []))},
        "structure": structure,
        "passed": help_run.returncode == 0 and cap_run.returncode == 0 and cap_payload.get("ok") is True and all(v if isinstance(v, bool) else not v for k, v in structure.items() if k != "skill_md_inside_tool_folder") and structure["skill_md_inside_tool_folder"] is False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools-package-audit.py", description="Audit a Skill Tool folder or delivery ZIP.")
    parser.add_argument("--folder", help="Skill Tool folder to audit.")
    parser.add_argument("--zip", help="Delivery ZIP to audit.")
    parser.add_argument("--extract-to", help="Clean temporary skill root for extraction and rerun.")
    parser.add_argument("--rerun", action="store_true", help="Extract the ZIP and rerun the primary tool from a clean root.")
    parser.add_argument("--force", action="store_true", help="Allow a non-empty extraction root.")
    parser.add_argument("--output", help="Write JSON audit report to this path.")
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"ok": True, "tool": "tools-package-audit.py"}
    try:
        if args.folder:
            report["folder_audit"] = audit_folder(args.folder)
        if args.zip:
            report["zip_audit"] = audit_zip(args.zip, expected_name=Path(args.folder).name if args.folder else None)
        if args.rerun:
            if not args.zip or not args.extract_to:
                raise AuditError("--rerun requires --zip and --extract-to.")
            report["extract_and_rerun"] = extract_and_rerun(args.zip, args.extract_to, force=args.force)
        passed = all(
            section.get("passed", True)
            for section in (
                report.get("folder_audit", {}),
                report.get("zip_audit", {}),
                report.get("extract_and_rerun", {}),
            )
        )
        report["ok"] = passed
    except (AuditError, OSError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        report = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}, "recovery": "Fix the reported structure, recreate the ZIP, and rerun the audit."}
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
