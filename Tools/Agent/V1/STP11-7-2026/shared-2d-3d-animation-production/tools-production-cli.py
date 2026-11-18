#!/usr/bin/env python3
"""Primary entry point for the shared-2d-3d-animation-production Skill Tool.

Unified offline production CLI implementing the deterministic manifest/script
capabilities of the Shared 2D and 3D Animation Production Skill Catalog.

Usage:
  python3 tools-production-cli.py <command> [options]

Commands:
  capabilities      List all capabilities (JSON or text)
  scaffold          Create the standard 2D/3D project folder tree
  init-config       Author a machine-readable project-config.json
  validate-config   Validate project-config.json (Project Bootstrap Validator)
  check-naming      Validate asset filenames and shot ids (Filename Compliance)
  sidecar           Write a metadata sidecar for one asset
  verify-sidecars   Verify sidecar checksums (Metadata Integrity Checker)
  shotlist          Build shot-list.json from rows in a CSV file
  validate-shotlist Validate an existing shot list
  publish-version   Publish a version manifest and append a changelog entry
  backup-plan       Write a 3-2-1 backup plan
  write-checksums   Write checksums.sha256 for a directory (default: delivery/)
  verify-checksums  Verify checksums.sha256 (Backup Integrity / Transfer Validator)
  assemble-delivery Assemble delivery-manifest.json + checksums
  rights-manifest   Write the license/provenance manifest
  release-gate      Final sign-off gate (blocks release on rights/QC/approval failure)

All commands print structured JSON to stdout. Exit code 0 = success,
2 = validation/input error, 1 = other failure.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("tools_core", Path(__file__).resolve().parent / "tools-core.py")
core = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(core)

CAPABILITIES = [
    {"id": "scaffold", "category": "2. Project Architecture and Production Templates",
     "skills": ["Unified 2D-3D Project Scaffolder"]},
    {"id": "init-config", "category": "2. Project Architecture and Production Templates",
     "skills": ["Project Configuration Author"]},
    {"id": "validate-config", "category": "2. Project Architecture and Production Templates",
     "skills": ["Project Bootstrap Validator"]},
    {"id": "check-naming", "category": "3. Asset Naming, Identity, and Metadata",
     "skills": ["Filename Compliance Validator", "Shot Identifier Registrar"]},
    {"id": "sidecar", "category": "3. Asset Naming, Identity, and Metadata",
     "skills": ["Metadata Sidecar Writer"]},
    {"id": "verify-sidecars", "category": "3. Asset Naming, Identity, and Metadata",
     "skills": ["Metadata Integrity Checker"]},
    {"id": "shotlist", "category": "7. Shot Lists, Scheduling, and Production Tracking",
     "skills": ["Shot List Builder"]},
    {"id": "validate-shotlist", "category": "7. Shot Lists, Scheduling, and Production Tracking",
     "skills": ["Shot List Validator"]},
    {"id": "publish-version", "category": "4. Version Control and Change Management",
     "skills": ["Version Publication Manager", "Change Log Generator", "Release Tag and Baseline Manager"]},
    {"id": "backup-plan", "category": "5. Backup, Storage, and Disaster Recovery",
     "skills": ["3-2-1 Backup Planner"]},
    {"id": "write-checksums", "category": "26. Final Playback and Delivery Validation",
     "skills": ["Checksum and Transfer Validator"]},
    {"id": "verify-checksums", "category": "5. Backup, Storage, and Disaster Recovery",
     "skills": ["Backup Integrity Verifier"]},
    {"id": "assemble-delivery", "category": "26. Final Playback and Delivery Validation",
     "skills": ["Delivery Package Assembler", "File Naming and Package Validator"]},
    {"id": "rights-manifest", "category": "24. License, Rights, Consent, and Provenance",
     "skills": ["License and Provenance Manifest Builder"]},
    {"id": "release-gate", "category": "23. Review, Notes, Approval, and Change Control",
     "skills": ["Final Sign-Off Gate", "Release Rights Gate"]},
]


def emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def cmd_scaffold(args) -> dict:
    root = Path(args.project_root)
    created = []
    for folder in core.STANDARD_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
        created.append(folder)
    return {"ok": True, "project_root": str(root), "folders_created": created}


def cmd_init_config(args) -> dict:
    root = Path(args.project_root)
    root.mkdir(parents=True, exist_ok=True)
    cfg = core.default_project_config(args.name)
    out = root / core.CONFIG_FILENAME
    core._write_json(out, cfg)
    return {"ok": True, "config": str(out)}


def cmd_validate_config(args) -> dict:
    root = Path(args.project_root)
    cfg = core._read_json(root / core.CONFIG_FILENAME)
    problems = core.validate_project_config(cfg, str(root / core.CONFIG_FILENAME))
    return {"ok": not problems, "problems": problems}


def cmd_check_naming(args) -> dict:
    return core.check_naming(Path(args.project_root))


def cmd_sidecar(args) -> dict:
    meta = json.loads(args.metadata) if args.metadata else {"type": args.type or "asset"}
    return core.write_sidecar(Path(args.project_root), args.asset, meta)


def cmd_verify_sidecars(args) -> dict:
    return core.verify_sidecars(Path(args.project_root))


def cmd_shotlist(args) -> dict:
    rows = []
    with open(args.csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append({k.strip(): v.strip() for k, v in row.items()})
    if not rows:
        raise core.SkillError("invalid-input", "CSV has no data rows")
    return core.build_shotlist(Path(args.project_root), rows)


def cmd_validate_shotlist(args) -> dict:
    return core.validate_shotlist(Path(args.project_root))


def cmd_publish_version(args) -> dict:
    return core.publish_version(Path(args.project_root), args.version, args.note or "")


def cmd_backup_plan(args) -> dict:
    dests = args.destinations.split(",") if args.destinations else None
    return core.backup_plan(Path(args.project_root), copies=args.copies, destinations=dests)


def cmd_write_checksums(args) -> dict:
    return core.write_checksums(Path(args.project_root), args.subdir)


def cmd_verify_checksums(args) -> dict:
    return core.verify_checksums(Path(args.project_root), args.subdir)


def cmd_assemble_delivery(args) -> dict:
    files = [f.strip() for f in args.files.split(",") if f.strip()]
    if not files:
        raise core.SkillError("invalid-input", "--files must list at least one project-relative path")
    return core.assemble_delivery(Path(args.project_root), files)


def cmd_rights_manifest(args) -> dict:
    entries = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise core.SkillError("invalid-input", "rights JSON must be a list of entries")
    return core.rights_manifest(Path(args.project_root), entries)


def cmd_release_gate(args) -> dict:
    required = args.approvals.split(",") if args.approvals else None
    return core.release_gate(Path(args.project_root), required)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools-production-cli.py",
        description="Shared 2D/3D Animation Production Skill Tool (offline, deterministic manifests).")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--project-root", default=".", help="project root directory (default: cwd)")
        sp.set_defaults(func=fn)
        return sp

    sub.add_parser("capabilities", help="list all capabilities").set_defaults(func=lambda a: {"capabilities": CAPABILITIES, "count": len(CAPABILITIES)})
    add("scaffold", cmd_scaffold, "create the standard project folder tree")
    sp = add("init-config", cmd_init_config, "author project-config.json")
    sp.add_argument("--name", required=True, help="lowercase hyphenated project name")
    add("validate-config", cmd_validate_config, "validate project-config.json")
    add("check-naming", cmd_check_naming, "validate asset filenames and shot ids")
    sp = add("sidecar", cmd_sidecar, "write metadata sidecar for an asset")
    sp.add_argument("--asset", required=True, help="project-relative asset path")
    sp.add_argument("--metadata", default="", help="JSON metadata string")
    sp.add_argument("--type", default="", help="asset type label")
    add("verify-sidecars", cmd_verify_sidecars, "verify sidecar checksums")
    sp = add("shotlist", cmd_shotlist, "build shot-list.json from CSV")
    sp.add_argument("--csv-path", required=True, help="CSV with shot_id,scene,description,frames")
    add("validate-shotlist", cmd_validate_shotlist, "validate shot-list.json")
    sp = add("publish-version", cmd_publish_version, "publish version + changelog entry")
    sp.add_argument("--version", required=True, help="e.g. v001")
    sp.add_argument("--note", default="")
    sp = add("backup-plan", cmd_backup_plan, "write 3-2-1 backup plan")
    sp.add_argument("--copies", type=int, default=3)
    sp.add_argument("--destinations", default="", help="comma-separated destinations")
    sp = add("write-checksums", cmd_write_checksums, "write checksums.sha256")
    sp.add_argument("--subdir", default="delivery")
    sp = add("verify-checksums", cmd_verify_checksums, "verify checksums.sha256")
    sp.add_argument("--subdir", default="delivery")
    sp = add("assemble-delivery", cmd_assemble_delivery, "assemble delivery manifest + checksums")
    sp.add_argument("--files", required=True, help="comma-separated project-relative files")
    sp = add("rights-manifest", cmd_rights_manifest, "write rights manifest from JSON file")
    sp.add_argument("--json-path", required=True)
    sp = add("release-gate", cmd_release_gate, "final sign-off gate")
    sp.add_argument("--approvals", default="", help="comma-separated required approvers")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except core.SkillError as exc:
        emit({"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return 2
    except Exception as exc:  # structured catch-all; no raw traceback to user
        emit({"ok": False, "error": {"code": "internal", "message": str(exc)}})
        return 1
    emit(result)
    if isinstance(result, dict) and result.get("ok") is False:
        return 2
    if isinstance(result, dict) and result.get("released") is False:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
