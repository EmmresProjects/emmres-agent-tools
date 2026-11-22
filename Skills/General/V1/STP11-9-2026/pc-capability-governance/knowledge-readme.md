# pc-capability-governance — Knowledge and Operating Guide

Unified, lawful capability-discovery, routing and governance layer for PC work.
This folder is a **reusable Skill Tool component**, not a complete skill: it contains
**no `SKILL.md`** and is designed to be copied beside a parent package's root
`SKILL.md`.

| Item | Value |
| --- | --- |
| Skill Tool name | `pc-capability-governance` |
| Umbrella category | Capability Discovery, Routing, and Governance (catalog category 1, 12 skills) |
| Primary entry point | `tools-capability_governance.py` |
| Runtime | Python 3.9+ (3.13.16 used for validation) |
| Third-party dependencies | **None** — standard library only |
| Commands | 11 subcommands covering all 12 category capabilities |
| Contract statuses | 9 (`FOUND` … `NETWORK REQUIRED`) |
| Contract fields | 11, all emitted on every capability record |
| Measured probe time | ~3.4 s for all 40 providers × 2 runs on the validation host |

Everything the tool does is **read-only by default**. The only command that writes
outside its JSON output is `reversible-plan --apply`, which creates a verified
backup copy, and `self-test`/`inventory` when `--evidence` is supplied, which write
evidence records.

---

## 1. Purpose

PC work fails for boring, expensive reasons: an agent assumes a compiler exists,
edits a binary before hashing it, signs an installer without a key owner, or
"repairs" a system with no rollback. This Skill Tool makes those preconditions
explicit and machine-checkable:

1. **Discover** what the machine can actually do (tools, versions, architectures,
   host requirements, licences).
2. **Report** it in the catalog's Startup Self-Test Contract shape, with a
   deterministic status per capability.
3. **Route** a job to the least-privileged worker that satisfies its hard
   requirements.
4. **Gate** the request for lawfulness and for the right approval level before
   anything changes.
5. **Preserve** evidence: hashes, tool versions, recorded commands, rollback steps.

The catalog describes 400 capabilities; this Skill Tool governs *all* of them
without claiming that any of them is installed.

## 2. Capability list

Run `python3 tools-capability_governance.py capabilities` for the machine-readable
list. Every catalog capability of this category is implemented:

| # | Catalog capability | Command | Approval class | Output |
| --- | --- | --- | --- | --- |
| 1 | PC Capability Inventory | `inventory` | read-only | `inventory.json` |
| 2 | Startup Self-Test Orchestrator | `self-test` | read-only | `self-test-report.json` + evidence |
| 3 | Host-Aware Task Router | `route` | read-only | `routing-plan.json` |
| 4 | Lawful Use Gate | `boundary-check` | read-only | `boundary-check.json` |
| 5 | Reversible Change Planner | `reversible-plan` | read-only; copy only with `--apply` | `reversible-plan.json` + backup |
| 6 | Evidence and Provenance Recorder | `provenance` | read-only | `provenance.json` |
| 7 | Risk-Based Execution Approver | `approval-check` | read-only | `approval-decision.json` |
| 8 | Clean-State Validation Manager | `self-test --check-runs N` | read-only | `determinism` block in the report |
| 9 | Toolchain Version Policy Manager | `policy` + version windows in the provider table | read-only | `effective-policy.json` |
| 10 | PC Task Decomposer | `route --decompose --goal "…"` | read-only | `task_plan` block |
| 11 | Artifact Handling Route Selector | `route` + `asset-provider-catalog.json` | read-only | worker + approval class per artifact family |
| 12 | Operational Boundary Enforcer | `boundary-check` + `approval-check` | read-only | decisions with exit codes |

Two supporting capabilities are implemented because the other twelve are unsafe
without them: **Structured Output Validator** (`validate-output`, validates emitted
documents against the packaged schemas) and **Category and Skill Tool Extractor**
(`parse`, turns pasted Markdown/HTML/plain-text catalogs into structured
categories, names and descriptions).

## 3. Setup

No installation, no virtual environment, no packages.

```sh
# POSIX
python3 tools-capability_governance.py help

# Windows
py -3 tools-capability_governance.py help

# Launchers (find a suitable interpreter themselves, pass exit codes through)
sh tools-run.sh help
tools-run.cmd help
```

Requirements: Python **3.9 or newer** with the standard library. The tool refuses
to run on older interpreters only in the sense that `list[...]` type syntax in the
source is not used, so 3.9 is genuinely the floor; the tested interpreter is
3.13.16.

Supported hosts: Linux (tested), Windows and macOS (code paths implemented and
host-neutral, but **not executed** on those hosts during validation — see
§11 Limitations).

### Files in this Skill Tool

| File | Prefix | Role | Loading order |
| --- | --- | --- | --- |
| `tools-capability_governance.py` | tools- | **Primary entry point.** CLI, all 11 subcommands, approval/route/parse orchestration, JSON-Schema-subset validator, help system. | 1 (loads the two modules below itself) |
| `tools-probe-lib.py` | tools- | **Shared library.** Status/field constants, structured errors, JSON I/O, hashing, version parsing and comparison, bounded subprocess capture, host detection, evidence writer, markup normaliser, catalog parser, boundary term matcher. | 2 (imported by both other tools) |
| `tools-provider-commands.py` | tools- | **Data-driven provider table.** 40 probe definitions: id, label, category, tool list, version command, version regex, supported version window, hard host requirements (`os`/`arch`/`gpu`/`virtualization`/`network`), licence flag, install hint. Custom provider files are merged over it. | 3 (imported by the entry point; imports the shared library) |
| `tools-run.sh` | tools- | POSIX launcher; resolves its own directory so it works from any working directory. | any time |
| `tools-run.cmd` | tools- | Windows launcher (`py -3`, falling back to `python`). | any time |
| `knowledge-readme.md` | knowledge- | This document (the only knowledge file). | — |
| `asset-default-config.json` | asset- | Default configuration: timeouts, retries, offline mode, evidence dir, policy paths, text limits, network probe, redaction patterns. | read by every command |
| `asset-provider-catalog.json` | asset- | Category → provider → default approval class map, so an agent can pick the right provider family for a task. | reference data |
| `asset-approval-policy.json` | asset- | Nine approval levels with ranks, required roles, reversibility and controls, plus prohibited classes and the approval-record schema. | read by `approval-check`, `policy` |
| `asset-boundary-terms.json` | asset- | Boundary term categories (DRM, anti-cheat, access control, privacy, licensing, firmware, irreversible storage), severities, decisions, wording variants, exemptions, negation rules. | read by `boundary-check`, `policy` |
| `asset-readiness-profiles.json` | asset- | Six named capability sets (`governance-baseline`, `native-build`, `managed-windows`, `inspection-only`, `compatibility-lab`, `signing-release`) for `self-test --profile`. | read by `self-test --profile` |
| `asset-worker-profiles.json` | asset- | Six worker profiles with privilege ranks and limits, referenced by the routing decisions. | reference data |
| `asset-schema-selftest-report.json` | asset- | JSON Schema for the self-test report (enforces the 9 statuses and 11 contract fields). | read by `validate-output --kind self-test` |
| `asset-schema-provenance.json` | asset- | JSON Schema for provenance records. | read by `validate-output --kind provenance` |
| `asset-schema-category-parse.json` | asset- | JSON Schema for parse results. | read by `validate-output --kind parse` |

Every path inside the tool is resolved **relative to the Skill Tool folder**, so the
folder can be copied beside a parent `SKILL.md` without edits. References in this
document are relative to the future skill root, e.g.
`pc-capability-governance/asset-approval-policy.json`.

## 4. Contract: statuses, fields, exit codes

**Statuses** (exactly one per capability record):
`FOUND`, `MISSING`, `WRONG VERSION`, `BROKEN`, `LICENSE REQUIRED`,
`WINDOWS HOST REQUIRED`, `GPU REQUIRED`, `VIRTUALIZATION REQUIRED`,
`NETWORK REQUIRED`.

**Fields** (all 11 present on every record):

| Field | Meaning |
| --- | --- |
| `capability_id` | Stable id from the provider table (e.g. `dotnet-sdk`). |
| `status` | One of the nine statuses. |
| `detected_command` | Absolute path of the executable actually probed (or `(host state probe)`). |
| `detected_version` | Parsed version string, or `null`. |
| `host_type` | `linux`, `windows`, `macos` or `unknown`. |
| `architecture` | Host machine architecture (`x86_64`, `aarch64`, …). |
| `requirements` | The hard requirements object used for the decision. |
| `test_command` | The exact read-only command that was run. |
| `exit_code` | Exit code of that command (`null` when nothing ran). |
| `evidence_path` | Relative path of the saved evidence record, or `""`. |
| `recommended_action` | Short justified remediation, or `""` when healthy. |

Extra diagnostic fields (`label`, `category`, `detail`, `duration_ms`,
`version_window`, `license_required`) are additive and do not replace the contract.

**Exit codes** (stable, documented in `help`):

| Code | Meaning |
| --- | --- |
| 0 | Success / all required capabilities usable. |
| 1 | Findings: not routable, profile not ready, invalid document, nothing parsed. |
| 2 | Usage or input validation error (also unknown ids, missing files, bad JSON). |
| 3 | Approval required before the action may run. |
| 4 | Blocked by boundary or approval policy. |
| 5 | Internal error. |
| 130 | Interrupted (Ctrl-C). |

## 5. Commands

General form:

```sh
python3 tools-capability_governance.py <command> [options]
```

Common options: `--config PATH`, `--providers PATH`, `--offline`, `--timeout SECONDS`,
`--run-id ID`, `--evidence DIR`, `--output PATH`. Without `--output` the JSON
document goes to stdout; with `--output` it is written atomically (temp file +
rename) and stdout stays clean.

### 5.1 `capabilities` — capability list

```sh
python3 tools-capability_governance.py capabilities
python3 tools-capability_governance.py capabilities --verbose --output caps.json
```

Emits tool identity, version, the nine statuses, the eleven fields, all twelve
implemented capabilities with ids/commands/approval/outputs, and the support-tool
list. `--verbose` adds the effective configuration.

### 5.2 `inventory` — PC Capability Inventory

```sh
python3 tools-capability_governance.py inventory --only gcc,cmake,dotnet-sdk --evidence evidence
python3 tools-capability_governance.py inventory --offline --strict
```

Probes every provider (or `--only` ids) and emits host facts plus one
contract-shaped record each. `--strict` turns any non-`FOUND` capability into exit
code 1. `--evidence DIR` writes `DIR/<run-id>/probe-<id>.json` per capability and
records each file's path and SHA-256 in the payload.

### 5.3 `self-test` — Startup Self-Test Orchestrator + Clean-State Validation

```sh
python3 tools-capability_governance.py self-test --evidence evidence
python3 tools-capability_governance.py self-test --check-runs 2
python3 tools-capability_governance.py self-test --profile governance-baseline
python3 tools-capability_governance.py self-test --list-profiles
```

Runs the deterministic probe set, writes one evidence file per capability, and
writes the report itself into the evidence folder (`report_path`). It also asserts
the contract: `contract.issues` lists any record missing a field or carrying a
status outside the nine, `contract.valid` summarises it, and `ok` is false when the
contract fails or a requested profile is not ready. `preflight` records internal
consistency checks (unique provider ids, sane version windows, offline mode
respected). `--check-runs N` (1–10) repeats the whole probe set and reports
`deterministic.status_drift`, separating hidden/flaky state from true dependencies.

### 5.4 `route` — Host-Aware Task Router + Task Decomposer

```sh
python3 tools-capability_governance.py route --capability gcc,file,cmake
python3 tools-capability_governance.py route --capability kvm,qemu-img --decompose --goal "migrate legacy app"
```

Selects the least-privileged worker in ascending privilege order: `linux-local` →
`network-enabled-worker` → `virtualized-worker` → `arm64-worker` → `gpu-worker` →
`windows-host`. Emits `target_worker`, `hard_requirements`, `assignments`,
`ready`, `not_ready` (with `recommended_action`), `routable` and a one-line
`recommended_action`. Exit 0 only when every requested capability is usable.
`--decompose` adds the eight governed stages (inspect, preserve, reproduce, repair,
test, package, verify, handoff) with an approval class per stage.

### 5.5 `approval-check` — Risk-Based Execution Approver

```sh
python3 tools-capability_governance.py approval-check --action "resize partition" --action-class destructive
python3 tools-capability_governance.py approval-check --action "sign installer" --action-class signing --approval release-owner,security-owner
python3 tools-capability_governance.py approval-check --actions-file actions.json --approval release-owner
```

Action classes: `read-only`, `reversible`, `networked`, `privileged`,
`destructive`, `signing`, `financial`, `credential`, `publishing` — plus the
prohibited class keys `license-bypass`, `access-control-bypass`,
`anti-cheat-bypass`, `credential-theft`, which always return `BLOCKED` (exit 4).
Each decision lists the required roles, the roles present, the missing roles and
the controls that must accompany execution. A batch inherits its worst decision.
`actions.json` is either a list or `{"actions": [...]}`, where each item is
`{"name", "class", "target", "approvals": [], "already_approved": bool}`.

### 5.6 `reversible-plan` — Reversible Change Planner

```sh
python3 tools-capability_governance.py reversible-plan --target ./app.exe
python3 tools-capability_governance.py reversible-plan --target ./build --backup-dir backups --apply
```

Always hashes the target first (SHA-256 per file, plus a deterministic tree hash for
directories), then either prints the plan or, with `--apply`, copies it to the
backup destination and **re-hashes the copy**, refusing to continue if the hashes
differ. `rollback_command` and `rollback_steps` are emitted as ready-to-run shell.
Refusals: a backup destination that already exists (no silent overwrite) and a
destination nested inside the target. The report warns that restoring overwrites
work performed after the snapshot.

### 5.7 `boundary-check` — Lawful Use Gate / Operational Boundary Enforcer

```sh
python3 tools-capability_governance.py boundary-check --text "bypass the license check"
python3 tools-capability_governance.py boundary-check --text-file request.txt --authorization "owner of the device"
printf %s "$REQUEST" | python3 tools-capability_governance.py boundary-check
```

Decisions: `BLOCKED` (exit 4), `REVIEW` (exit 1) or `ALLOW` (exit 0). Matching is
deliberately broader than substring search:

* phrase match, spelling variants folded (`licence`/`license`, `authorisation`, …);
* **prefix** match for morphology (`redistribute` → `redistribution`);
* **proximity** match inside a small sliding token window, so "bypass the license
  check" is recognised by the term `license bypass`;
* negation markers (`prohibited`, `without authorization`, …) disable exemptions;
* exemptions (`preservation`, `compatibility-work`, `own-system-repair`,
  `defensive-security`) only clear `REVIEW` findings they explicitly `covers`, and
  can never clear DRM, anti-cheat or access-control findings.

Output includes every match with its category, matched term, match method, severity,
rationale and the context still required, plus `resolved_by_exemption`,
`negation_markers_found` and `unmet_context`.

### 5.8 `provenance` — Evidence and Provenance Recorder

```sh
python3 tools-capability_governance.py provenance --input ./installer.exe --input ./src \
  --command "cmake --build build" --note "hash before repair"
```

Records SHA-256 per input (deterministic tree hash for directories), the versions of
the relevant toolchain, the **recorded but never executed** commands, and a
chain-of-custody entry. Command strings are redacted before writing: bearer/basic
tokens, `--password`/`--token`/`--secret`/`--key` values, `-u user:pass` pairs,
`key=value` secrets and e-mail addresses. Hashing is deterministic across runs
(verified).

### 5.9 `parse` — Category and Skill Tool Extractor

```sh
python3 tools-capability_governance.py parse --source catalog.md --output parse.json
python3 tools-capability_governance.py parse --source page.html
cat notes.txt | python3 tools-capability_governance.py parse
```

Accepts plain text, Markdown or copied HTML (`--text` for inline input). It strips
tags, `<style>`/`<script>` blocks, CSS classes, `data-*` attributes and numbering
marks; decodes entities; collapses spacing; then extracts categories, Skill Tool
names and descriptions. Robustness features that were all exercised against the
supplied catalog PDF text: repeated page headers/footers are ignored, numbered
headings wrapped across lines are re-joined, index blocks (`… (12 skills)`) are
recorded separately and used to reconcile declared counts, annex sections
(implementation priority, self-test contract) are not read as skills, duplicate
spelling variants of a category are merged, and index-only headings with no entries
are dropped. It reports `count_matches` per category, `count_mismatches`,
`index_entries` and an `umbrella_suggestion` for the future Skill Tool name.

### 5.10 `validate-output` — Structured Output Validator

```sh
python3 tools-capability_governance.py validate-output --kind self-test --input report.json
python3 tools-capability_governance.py validate-output --kind provenance --input prov.json
python3 tools-capability_governance.py validate-output --schema my-schema.json --input doc.json
```

Validates against the packaged schemas using an in-process validator supporting the
subset the schemas use: `type`, `properties`, `required`, `additionalProperties`,
`items`, `minItems`, `enum`, `pattern`, `minLength`, `minimum`, `allOf`, `anyOf`,
`if`/`then`. Errors are reported with JSON paths (`$.capabilities[0].status`).
Exit 0 when valid, 1 when not, 2 for unreadable input.

### 5.11 `policy` — Toolchain Version Policy Manager

```sh
python3 tools-capability_governance.py policy --output effective-policy.json
```

Shows the effective configuration, the approval levels with roles and controls, the
prohibited classes, the boundary categories, provider statistics (`windows_required`,
`virtualization_required`, `licence_required`) and the active limits. Version
windows themselves are enforced during probing: a detected version below
`version_min` or above `version_max` yields `WRONG VERSION` with the window in the
`detail`.

### 5.12 `help`

```sh
python3 tools-capability_governance.py help
python3 tools-capability_governance.py help self-test
```

## 6. Configuration

`asset-default-config.json` is read by every command; override with `--config PATH`.

| Key | Default | Effect |
| --- | --- | --- |
| `timeout_seconds` | 10 | Per-probe subprocess timeout; a timeout is reported as `BROKEN`. |
| `retries` | 1 | Extra attempts for a failing probe before it is accepted. |
| `offline` | false | Disables the TCP reachability probe. |
| `evidence_dir` | `evidence` | Where `self-test`/`inventory` write evidence. |
| `evidence_root` | `.` | Base for relative evidence paths in reports. |
| `provider_file` | null | Extra provider definitions merged over the built-in table. |
| `approval_policy` | `asset-approval-policy.json` | Packaged asset name or a path. |
| `boundary_terms` | `asset-boundary-terms.json` | Packaged asset name or a path. |
| `route_mode` | `least-privilege` | Value recorded in routing plans. |
| `max_text_bytes` | 2000000 | Hard cap for `parse` and `boundary-check` input. |
| `network_probe` | `1.1.1.1:443`, 0.6 s | Endpoint used only for reachability. |
| `redaction.patterns` | see file | Reference list of redaction regexes. |

A custom provider file is `{"providers": [...]}` (or a bare list). Fields match the
built-in table (`id`, `label`, `category`, `tools`, `version_cmd`, `version_regex`,
`version_min`, `version_max`, `requirements`, `license_required`, `missing_status`,
`install_hint`, `verify_cmd`, `notes`, `requires_module`, `timeout_seconds`);
omitted fields fall back to defaults, and a custom entry with an existing `id`
replaces it. Example:

```json
{"providers": [{"id": "go", "label": "Go toolchain", "tools": ["go"],
  "version_cmd": "go version", "version_min": "1.20",
  "requirements": {"os": ["any"], "arch": ["any"]}, "install_hint": "Install Go 1.20+."}]}
```

### Built-in providers (40)

`python3`, `git`, `sha256sum`, `gcc`, `clang`, `cmake`, `ninja`, `make`,
`mingw-w64`, `dotnet-sdk`, `mono`, `java`, `node`, `npm`, `7zip`, `zip`, `ffmpeg`,
`imagemagick`, `exiftool`, `sqlite3`, `readelf`, `file`, `strings`, `pefile`,
`lief`, `radare2`, `detect-it-easy`, `wine`, `qemu-system`, `qemu-img`, `kvm`,
`osslsigncode`, `nsis`, `signtool`, `openssl`, `curl`, `syft`, `trivy`, `docker`,
`wsl`.

## 7. Safety and approval rules

1. **Read-only by default.** `inventory`, `self-test`, `route`, `approval-check`,
   `boundary-check`, `provenance`, `parse`, `validate-output`, `policy` never
   modify the system or the artifacts they inspect.
2. **Hash before change.** `reversible-plan` always hashes first and can verify the
   backup it creates; `provenance` records hashes before any repair begins.
3. **Approval before privileged work.** Anything classified `privileged`,
   `destructive`, `signing`, `financial`, `credential`, `publishing` or `networked`
   requires the human roles named in `asset-approval-policy.json`. The tool
   *evaluates* approvals; it never grants them itself, and an approval record has to
   be attached to the run evidence.
4. **Batches inherit the worst decision.** One missing approval fails the whole
   batch; a missing approval never defaults to allowed.
5. **Prohibited boundaries are absolute.** DRM, activation, licence, anti-cheat and
   access-control bypasses are blocked regardless of approvals, and exemptions
   cannot clear them.
6. **No arbitrary execution.** The only commands ever run are the fixed, read-only
   version probes in the provider table (plus one fixed `python3 -c "import x"` for
   module providers). There is no shell-string execution, no `eval`, no dynamic
   URL, and no command taken from user input. Commands supplied to `provenance` are
   recorded, never executed (`"executed": false` is in the schema).
7. **Bounded network.** Network use is limited to one optional TCP reachability
   check, disabled by `--offline` and bounded by `network_probe.timeout_seconds`.
   Providers that need the network are reported `NETWORK REQUIRED` rather than
   reaching out.
8. **No secrets in output.** Command strings are redacted; probe output is not
   stored beyond the parsed version and short diagnostics.
9. **No overwrite.** `reversible-plan --apply` refuses to overwrite an existing
   backup destination or to nest a backup inside its target.
10. **Evidence stays relative.** Evidence paths are reported relative to the
    evidence root and never contain parent-directory traversal.

## 8. Validation and tests actually completed

All of the following were executed in this environment on Linux x86_64, Python
3.13.16. No test below is claimed without having run it.

**Interface and contract**

* `help`, `help <command>`, `capabilities` (12 capabilities, 9 statuses, 11 fields) — pass.
* `self-test` over all 40 providers: `contract.valid: true`, every record had all 11
  fields, 81 evidence files written, report written into the evidence folder — pass.
  Host result at validation time: 15 `FOUND`, 24 `MISSING`, 1
  `VIRTUALIZATION REQUIRED` (no `/dev/kvm`), network reachable, GPU absent.
* `self-test --check-runs 2` — `deterministic: true`, zero status drift.
* Determinism **failure** path: a deliberately flaky custom provider produced
  `deterministic: false` with 1–4 drift events per 6-run batch across three
  batches — pass.
* `self-test --profile governance-baseline` → `READY`, exit 0;
  `--profile compatibility-lab` → `NOT READY`, exit 1; unknown profile → exit 2
  listing the known profiles; `--list-profiles` → exit 0.
* `inventory --strict` with a missing tool → exit 1; normal inventory → exit 0.

**Status coverage** — all nine statuses were produced by real probes through the
public `inventory`/`self-test` interface, not simulated:

| Status | How it was produced | Result seen |
| --- | --- | --- |
| `FOUND` | built-in probes on the validation host | `gcc 14.2.0-19`, `file 5.46`, `git 2.47.3`, `java 11`, `node 20.20.2`, `python3 3.13.16`, `openssl 3.5.7`, … |
| `MISSING` | tools absent from `PATH` | `cmake`, `dotnet-sdk`, `wine`, `7zip`, `signtool`, … (exit 127) |
| `WRONG VERSION` | custom provider pinning `gcc` to `>= 99.0` | `14.2.0-19`, "below minimum supported version 99.0" |
| `BROKEN` | custom provider whose version command sleeps 30 s, `--timeout 1` | "probe timed out after 1.0s", `exit_code 124` (also reproduced via `timeout_seconds` config) |
| `LICENSE REQUIRED` | custom provider with `license_required: true` whose tool is present | "Obtain and document the vendor licence before use; do not bypass it." |
| `VIRTUALIZATION REQUIRED` | built-in `kvm` provider on a host without `/dev/kvm` | "virtualization unavailable (/dev/kvm absent)" (exit 1) |
| `GPU REQUIRED` | custom provider with `requirements.gpu: true` on this GPU-less host | "no GPU device or GPU inspection tool detected" |
| `NETWORK REQUIRED` | `curl` provider with `--offline` | "provider requires network access but none is usable" |
| `WINDOWS HOST REQUIRED` | `route`/`inventory` on Windows-only providers (`signtool`, `wsl`) from a Linux host | worker `windows-host`, not routable |

The built-in table also contains licence-flagged providers (`detect-it-easy`,
`signtool`) that report `LICENSE REQUIRED` on hosts where they are installed.

**Routing**

* `route --capability gcc,file` → `linux-local`, routable, exit 0.
* `route --capability curl` → `network-enabled-worker`, routable, exit 0.
* `route --capability kvm,qemu-img` → `virtualized-worker`, not routable, exit 1,
  remediation surfaced.
* `route --capability signtool,wsl` → `windows-host`, exit 1.
* `route --decompose` → eight stages with approval classes.
* Unknown id → exit 2 with the known-id list; no arguments → exit 2.

**Approval**

* `read-only` → `ALLOWED`, exit 0. `destructive` without roles → `APPROVAL REQUIRED`,
  exit 3, missing roles listed. Same action with `--approval
  system-owner,data-owner` → `ALLOWED`, exit 0, with a note about attaching the
  approval record. `license-bypass` → `BLOCKED`, exit 4. Unknown class → exit 2.
* Batch file with `read-only` + `signing` + `publishing` and only `release-owner`
  approved → whole batch `APPROVAL REQUIRED` (missing `security-owner`).

**Boundary**

* "bypass the license check" → `BLOCKED` (proximity match), exit 4.
* "remove the DRM protection" (with lawful-purchase words) → `BLOCKED` — exemptions
  cannot clear DRM.
* "export browser history and cookies … for the audit" → `REVIEW`, exit 1 (the
  generic word "audit" no longer exempts it).
* "vendor said redistribution is prohibited" → `REVIEW` (negation marker disables
  exemptions; prefix match on `redistribute`).
* "wine compatibility layer … my own machine … purchased … preserve hash" → `ALLOW`,
  exit 0, exemptions recorded.
* Plain build request → `ALLOW`, no matches. No input → exit 2. A 2,000,001-byte
  file (and the same file under a 500,000-byte config) → exit 2.

**Reversible planning and rollback**

* Directory dry run: tree hash, entry count, rollback steps, no copy performed.
* `--apply`: backup created and verified (`matches_original: true`).
* Round trip: target corrupted and a subdirectory deleted, then the emitted
  `rollback_command` run — restored tree hash **matched** the pre-change hash
  exactly and a phantom directory added after the snapshot was removed.
* File target round trip: corrupted file restored byte-for-byte.
* Re-running `--apply` against an existing backup → refused, exit 2. Missing target
  → exit 2.

**Provenance**

* File + directory inputs hashed; 13 tool versions recorded; commands recorded with
  `executed: false`; schema-valid output.
* Redaction: `--password secret123`, `Authorization: Bearer …`,
  `Authorization: Basic …`, `-u user:pass`, `--token abc999`, `ops@example.com` —
  after the final fix, **0 of 5 secret samples leaked** into the output.
* Same input hashed twice → identical SHA-256 (deterministic).

**Parsing**

* The supplied `PC_Agent_Skill_Catalog.pdf` text: **30 categories, 400 skills, 400
  with descriptions, 0 count mismatches**, index declared total 400 reconciled
  against 400 parsed entries; anneal/priority/self-test sections correctly excluded.
* Markdown input: 2 categories, 5 skills, all described; `.NET` preserved, wrapped
  headings handled.
* Copied HTML with `<style>`, `<script>`, CSS classes, `data-*` attributes, nested
  `<ol>/<li>/<p>` and inline markup: 1 category, 3 skills, declared count 3 matched.
* Piped stdin works; empty input → exit 1 with zero counts.

**Validation and outputs**

* `validate-output --kind parse/self-test/provenance` on real outputs → valid.
* A tampered report (bad status enum, removed `evidence_path`, removed `run_id`) →
  invalid, exit 1, three precise errors with JSON paths.
* Missing document → exit 2; malformed JSON → exit 2 with line/column.
* Every JSON output was opened and parsed programmatically during these tests.
* `sh tools-run.sh` and the shebang `./tools-capability_governance.py` both work and
  pass exit codes through (3 for an approval-required action).

**Packaging and portability**

* The folder was copied into a clean temporary skill root beside a test `SKILL.md`
  and rerun using only relative paths.
* The delivered ZIP was extracted into a clean temporary location and the primary
  tool was rerun from the extracted files.

Regression suite: 23 exit-code checks across all subcommands plus a schema
validation — **all passing** at delivery.

## 9. Limits and unavailable integrations

* **Not a complete skill.** There is intentionally no `SKILL.md`; a parent package
  must supply exactly one at the skill root.
* **Detection ≠ installation.** The tool reports what exists. It never installs
  packages, enables KVM, mounts images, or downloads anything.
* **Windows, macOS, ARM64, GPU and virtualized paths were not executed.** The
  validation host is Linux x86_64 without `/dev/kvm` and without a GPU, so
  `WINDOWS HOST REQUIRED`, `GPU REQUIRED` and `NETWORK REQUIRED` outcomes were
  exercised through the requirement logic and via providers that declare those
  requirements, not by running on those hosts. Only `VIRTUALIZATION REQUIRED` was
  observed from real host state. Windows/macOS detection code is host-neutral
  (`platform.system()`), but claim of support is limited to "implemented, not
  executed there".
* **Third-party scanners are not bundled.** `pefile`, `lief`, `radare2`,
  Detect It Easy, Syft, Trivy, Wine, QEMU, .NET SDK, MinGW-w64, 7-Zip, FFmpeg and
  the rest are only *probed*. Their absence is reported as `MISSING` with an
  install hint; no license-gated tool is installed or assumed.
* **Approval roles are labels.** The policy names roles (`system-owner`,
  `release-owner`, …); it cannot verify that a human actually granted approval.
  Enforcement relies on the caller attaching an approval record to the evidence.
* **Boundary screening is a screen, not a legal opinion.** It is a deterministic
  keyword/phrase matcher over declared categories; a novel phrasing may pass as
  `ALLOW`. Review `asset-boundary-terms.json` for your context and keep a human in
  the loop for high-impact work.
* **Version comparison is numeric.** Pre-release suffixes (`1.2.0-rc1`) compare on
  their numeric release component, which is intentional and conservative.
* **Network reachability** is a single TCP connect. A blocked DNS/TCP path reports
  `network_available: false`, which makes `NETWORK REQUIRED` providers unroutable
  until `--offline` is used or connectivity is restored.

## 10. Troubleshooting and recovery

| Symptom | Cause and fix |
| --- | --- |
| `RuntimeError`/`SyntaxError` on start | Interpreter older than 3.9. Use `py -3` / `python3` 3.9+. |
| `missing required Skill Tool file: tools-probe-lib.py` | Only part of the folder was copied. Copy the whole folder; the three `tools-*.py` files must sit side by side. |
| Every provider `MISSING` | `PATH` is empty in the agent context. Run with an explicit `PATH`, or check `detected_command` (127 means not found). |
| A tool exists but reports `BROKEN` | Its version command failed — often a broken shim, a missing shared library, or a probe timeout. Raise `--timeout`, inspect the evidence record, then repair the tool. |
| `WRONG VERSION` with an unexpected window | The provider table pins min/max versions. Adjust in a custom `--providers` file rather than editing the packaged one. |
| `self-test` not deterministic | Use `--check-runs` and read `deterministic.status_drift`; flaky probes are the usual cause (network or a tool that intermittently fails). |
| Evidence path empty | `--evidence` (or `evidence_dir` in config) was not supplied; `self-test` always writes evidence, `inventory` only with `--evidence`. |
| Approval always `APPROVAL REQUIRED` | Pass the roles named in `roles_required`, or use `already_approved: true` in an actions file when a human already signed off. |
| `BLOCKED` on a legitimate request | Check `matches` for the offending term; if it is a false positive for your context, narrow `asset-boundary-terms.json` deliberately and re-run. Never widen exemptions for DRM/anti-cheat/access-control. |
| Rollback overwrote newer work | Restores are destructive by design; restore into a fresh directory with `cp -a <dest> <newdir>`, compare hashes, then move into place. |
| `parse` finds nothing | The source may be a flat paragraph list. Ensure each entry starts with a number and each description follows on the entry line or the next line; use `--format` to force a parse mode. |
| `parse` under/over-counts | Compare with `index_entries` and `count_mismatches`. Wrapped or hyphenated headings are handled, but heavily formatted tables are not. |

**Recovery.** All commands are safe to re-run; nothing is mutated except
`reversible-plan --apply` (new backup directory) and evidence/report writes (new
run directory per run id). If a run is interrupted, delete the partial evidence run
directory and re-run with the same `--run-id` to overwrite it deliberately.

## 11. Extension and maintenance

* **Add a capability probe:** add an entry to `tools-provider-commands.py` (or ship a
  custom `--providers` file) — no code change needed. Set `requirements` so the
  router can place it, and `version_min`/`version_max` so the policy manager can
  police it.
* **Change approval policy:** edit `asset-approval-policy.json`. Keep `rank`
  monotonic with risk; the batch rule uses the worst decision.
* **Change boundary wording:** edit `asset-boundary-terms.json` — add terms, phrases
  and spelling variants to a category, or add an exemption with explicit `covers`.
  Never add DRM/anti-cheat/access-control items to `exemptions_never_cover`'s
  complement.
* **Add a readiness profile:** append to `asset-readiness-profiles.json`; it becomes
  available to `self-test --profile` and `--list-profiles` immediately.
* **Add an output schema:** drop `asset-schema-<kind>.json` beside the others and
  register the kind in `resolve_schema` in the entry point.
* **Maintenance checklist:** re-run the 23-check regression after any change; confirm
  `capabilities` still reports 12 capabilities, 9 statuses and 11 fields; confirm
  `self-test --check-runs 2` stays deterministic on a healthy host; confirm the
  catalog parse still yields 30/400 on the reference document.
* **Versioning:** `TOOL_VERSION`, `SCHEMA_VERSION`, `config_version`,
  `policy_version`, `terms_version` and the asset `*_version` fields move together
  for breaking output changes.
