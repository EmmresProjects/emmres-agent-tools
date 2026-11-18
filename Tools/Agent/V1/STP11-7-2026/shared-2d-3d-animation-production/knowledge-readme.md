# shared-2d-3d-animation-production — Skill Tool

Unified offline Skill Tool implementing the deterministic manifest/script
capabilities of the **Shared 2D and 3D Animation Production Skill Catalog**
(November 2026 edition). One folder, no SKILL.md (this folder is meant to sit
beside a future parent skill's root `SKILL.md`).

## Files

| File | Role |
|---|---|
| `tools-production-cli.py` | **Primary runnable entry point.** Argparse CLI exposing every capability as a subcommand; prints structured JSON; exit 0 = ok, 2 = validation/input failure, 1 = internal error. |
| `tools-core.py` | Shared logic library imported by the CLI (scaffolding, config validation, naming rules, sidecars, shot lists, versioning, checksums, delivery, rights, sign-off gate). Standard library only. |
| `knowledge-readme.md` | This documentation (the single knowledge file). |
| `asset-default-config.json` | Reusable project-standards preset (frame rates, naming patterns, defaults). |
| `asset-rights-template.json` | Reusable template for license/provenance manifest entries. |

## Setup / dependencies

- Python 3.9+ (uses only the standard library: `json`, `csv`, `hashlib`, `argparse`, `pathlib`, `re`, `datetime`). No pip install, no network.
- Run from the Skill Tool folder or reference it by path; the CLI adds its own directory to `sys.path` to import `tools-core.py`.

## Commands (run with `python3`)

```
python3 tools-production-cli.py capabilities
python3 tools-production-cli.py scaffold --project-root ./my-show
python3 tools-production-cli.py init-config --project-root ./my-show --name my-show
python3 tools-production-cli.py validate-config --project-root ./my-show
python3 tools-production-cli.py check-naming --project-root ./my-show
python3 tools-production-cli.py sidecar --project-root ./my-show --asset assets/hero-character_design_v003.png --metadata '{"type":"character"}'
python3 tools-production-cli.py verify-sidecars --project-root ./my-show
python3 tools-production-cli.py shotlist --project-root ./my-show --csv-path shots.csv
python3 tools-production-cli.py validate-shotlist --project-root ./my-show
python3 tools-production-cli.py publish-version --project-root ./my-show --version v001 --note "first assembly"
python3 tools-production-cli.py backup-plan --project-root ./my-show --copies 3
python3 tools-production-cli.py write-checksums --project-root ./my-show --subdir delivery
python3 tools-production-cli.py verify-checksums --project-root ./my-show --subdir delivery
python3 tools-production-cli.py assemble-delivery --project-root ./my-show --files "delivery/master.mov,delivery/shot-list.json"
python3 tools-production-cli.py rights-manifest --project-root ./my-show --json-path rights.json
python3 tools-production-cli.py release-gate --project-root ./my-show
```

All commands accept `--project-root` (default: current directory) and emit
JSON on stdout. `capabilities` and `--help` / `<command> --help` are built in.

## Capabilities mapped to catalog categories

- **Cat 2 Project Architecture:** `scaffold`, `init-config`, `validate-config`
- **Cat 3 Naming/Identity/Metadata:** `check-naming`, `sidecar`, `verify-sidecars`
- **Cat 4 Version Control:** `publish-version` (manifest + changelog + release tag dir)
- **Cat 5 Backup/DR:** `backup-plan` (3-2-1), `verify-checksums` (integrity verifier)
- **Cat 7 Shot Lists:** `shotlist`, `validate-shotlist`
- **Cat 23 Review/Approval:** `release-gate` (final sign-off gate)
- **Cat 24 Rights:** `rights-manifest`, plus rights checks inside `release-gate`
- **Cat 26 Delivery:** `assemble-delivery`, `write-checksums`, `verify-checksums`

## Inputs, outputs, schemas

- `project-config.json` (written by `init-config`, required by most commands):
  `project_name` (lowercase hyphenated), `frame_rate` (one of 23.976/24/25/29.97/30/48/50/59.94/60),
  `resolution` `{width,height}` ints, `aspect_ratio` `"W:H"`, `color_space`, `timecode_start`.
- Asset filenames: `<slug>_<type>_v###.<ext>`, lowercase hyphenated (e.g. `hero-character_design_v003.png`).
- Shot ids: `<sequence>_s###t###` (e.g. `seq01_s010t02`). Shot-list CSV columns: `shot_id,scene,description,frames`.
- Outputs: `shots/shot-list.json`, `versions/v###/version-manifest.json`, `CHANGELOG.json`,
  `archives/backup-plan.json`, `<subdir>/checksums.sha256`, `delivery/delivery-manifest.json`,
  `licenses/rights-manifest.json`, `review/approvals.json` (read by the gate).

## Safety and approval rules

- `release-gate` **blocks release** (exit 2, `released:false`) when the rights manifest is
  missing, any asset has license `none/unknown/tbd`, required approvals
  (default `director,producer,qc-lead`) are absent, or naming violations exist.
- No command deletes, overwrites outside its declared output, executes shell/network code,
  or touches credentials. All work is local and deterministic.

## Limits and unavailable integrations

- Catalog entries requiring DCC applications, render farms, GPU runtimes, neural voice
  engines, or actual media decoding (e.g. pixel-level QC, loudness metering) are **not
  implemented here**; this tool covers the offline manifest/config/validation layer.
  No network access is used or required (air-gap safe).

## Validation actually completed

- `capabilities` and `--help` run; every subcommand exercised end-to-end on a scratch project:
  scaffold → init-config → validate-config (valid and invalid config) → sidecar →
  verify-sidecars (pass and tamper-detect) → check-naming (clean and violating tree) →
  shotlist + validate-shotlist (valid and duplicate-id recovery) → publish-version →
  backup-plan → assemble-delivery → write/verify-checksums (pass and corrupted-file
  detection) → rights-manifest → release-gate (blocked without approvals, passing with).
- Re-ran the CLI from a ZIP-extracted copy in a clean temp skill root beside a test SKILL.md.

## Troubleshooting

- `missing-file: project-config.json` → run `init-config` first.
- `invalid-json` → fix the named file; JSON must be valid UTF-8.
- `verify-checksums` failures → a file changed after checksums were written; re-run `write-checksums`.
- Exit code 2 with `error.code` gives a machine-readable reason.
