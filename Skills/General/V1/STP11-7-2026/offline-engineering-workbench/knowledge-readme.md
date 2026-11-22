# Offline Engineering Workbench

A reusable, offline-only Skill Tool that turns the **Full Offline Code, Data, and
Scientific Work** catalog (32 categories, 480 proposed skills) into an executable
engineering workflow. It registers every catalog capability, plans a task against
that catalog, audits a project for offline-readiness and supply-chain risk, writes
and verifies SHA-256 provenance manifests, runs commands under a hard timeout and
approval gate, validates a copy of the project in a clean room, and renders a
Markdown handoff report.

- **Skill Tool name:** `offline-engineering-workbench`
- **Version:** 1.0.0
- **Primary entry point:** `offline-engineering-workbench/tools-workbench.py`
- **Runtime:** Python 3.9+ standard library only. No third-party packages, no
  network access, no credentials, no shell execution.
- **Tested with:** Python 3.13.16 on Linux (the delivery sandbox). The code uses
  only the standard library and falls back gracefully when POSIX process groups
  are unavailable, but Windows and macOS were not tested.
- **Catalog source:** `Full_Offline_Code_Data_Scientific_Work_Skill_Catalog.pdf`
  (52 pages, catalog edition November 2026). The 32 category names and 480 skill
  names/descriptions were extracted verbatim into `asset-skill-catalog.json`.

> **Scope note.** The source catalog is a *planning blueprint* of 480 proposed
> skills. This Skill Tool does not pretend to implement 480 bespoke runtimes. It
> implements the shared lifecycle that every catalog entry requires — discover,
> plan, check, run, manifest, verify, clean-room, report, recover — and registers
> all 480 capabilities so any task can be mapped to the right subset. The 25
> cross-cutting rules below are fully implemented. Category-specific compilers,
> databases, simulators, and ML frameworks are *not* bundled; they are invoked
> through the bounded `run` command when a local toolchain exists. This limitation
> is stated in [Limits](#limits-and-unavailable-integrations).

---

## 1. Package contents and file roles

All files sit directly inside `offline-engineering-workbench/`. There are no
subfolders, no `SKILL.md`, and no unprefixed files.

| File | Prefix | Role | Load order |
| --- | --- | --- | --- |
| `tools-workbench.py` | `tools-` | **Primary entry point.** CLI parser, dispatch, clean-room and report commands. | Loaded first; loads the other four modules by path. |
| `tools-workbench-core.py` | `tools-` | Shared core: catalog/policy loading and validation, project walking, hashing, path safety, output rendering, redaction, manifest-schema validation, report templating. | Loaded by the primary and by every other module. Registered as `wb_core` so all modules share one copy. |
| `tools-workbench-checks.py` | `tools-` | 25-rule offline engineering check engine (12 pattern rules, 13 aggregate rules). | Loaded by the primary; imports the shared core. |
| `tools-workbench-runner.py` | `tools-` | Bounded command runner: risk classification, approval gate, timeout, process-group cleanup, output caps, retries, JSONL audit log. | Loaded by the primary; imports the shared core. |
| `tools-workbench-planner.py` | `tools-` | Deterministic task-to-catalog planner with IDF weighting, lifecycle phases, gates, limits, approvals, and rollback. | Loaded by the primary; imports the shared core. |
| `tools-workbench-selftest.py` | `tools-` | End-to-end self-test suite (77 checks) that builds fixtures, exercises every command, and cleans up. | Run by `selftest` or directly. |
| `knowledge-readme.md` | `knowledge-` | This document: purpose, capabilities, commands, schemas, policy, safety, validation, limits, recovery, extension. | Documentation only. |
| `asset-skill-catalog.json` | `asset-` | The extracted catalog: 32 categories, 480 skills, category scopes, lifecycle phases, totals. | Read by `capabilities`, `category`, `skill`, `plan`, `check`, `doctor`. |
| `asset-check-policy.json` | `asset-` | Default policy: thresholds, quality gate, exclusions, disabled rules, approval-required command patterns. | Read by every command; merge with safe defaults. |
| `asset-report-template.md` | `asset-` | Markdown report template with `{{placeholder}}` slots. | Read by `report`. |
| `asset-manifest.schema.json` | `asset-` | JSON Schema (draft 2020-12 subset) for provenance manifests. | Read by `verify`. |

**File count:** 11 (6 `tools-*`, 1 `knowledge-*`, 4 `asset-*`). This is the actual
Skill Tool file count; the final ZIP contains exactly one top-level folder.

### Loading model

Python cannot import a module whose filename contains a hyphen, so each module
loads its siblings by path with `importlib.util.spec_from_file_location`. The core
is registered in `sys.modules` as `wb_core`; the other modules first check for an
existing `wb_core` and reuse it. This means:

- `python3 offline-engineering-workbench/tools-workbench.py ...` works from the
  future skill root.
- `python3 offline-engineering-workbench/tools-workbench-selftest.py` works
  directly.
- No installation, `PYTHONPATH`, package manager, or generated cache is required.
- Running the tool creates `__pycache__/` beside the modules; this is a normal
  Python artifact and is excluded from the delivered ZIP.

---

## 2. Quick start

From the future skill root (the directory that will contain the parent
`SKILL.md`):

```bash
# 1. Show the runtime, asset, catalog, and optional-tool status
python3 offline-engineering-workbench/tools-workbench.py doctor

# 2. List the 32 categories and 480 registered capabilities
python3 offline-engineering-workbench/tools-workbench.py capabilities --summary
python3 offline-engineering-workbench/tools-workbench.py capabilities --search kubernetes

# 3. Plan a task against the catalog
python3 offline-engineering-workbench/tools-workbench.py plan --task "train a reproducible churn model"

# 4. Audit a project and write a JSON result
python3 offline-engineering-workbench/tools-workbench.py check --root . --format json --output reports/check.json

# 5. Fingerprint and verify the project
python3 offline-engineering-workbench/tools-workbench.py manifest --root . --output reports/manifest.json
python3 offline-engineering-workbench/tools-workbench.py verify --manifest reports/manifest.json

# 6. Run a command under a timeout and approval gate
python3 offline-engineering-workbench/tools-workbench.py run --timeout 900 -- python3 -m pytest -q

# 7. Validate a copy of the project in a clean room
python3 offline-engineering-workbench/tools-workbench.py cleanroom --root . --format json

# 8. Render the handoff report
python3 offline-engineering-workbench/tools-workbench.py report --result reports/check.json --output reports/report.md

# 9. Run the full self-test suite
python3 offline-engineering-workbench/tools-workbench.py selftest
```

`reports/` is created automatically by `--output` when needed. It is not part of
the Skill Tool package.

### Global options

These options may be placed before the command. The `check`, `plan`, `run`, and
other commands also accept `--format`, `--output`, `--config`, and `--catalog`
after the command name.

| Option | Default | Meaning |
| --- | --- | --- |
| `--format {table,json,markdown}` | `table` | Human table, machine JSON, or Markdown. |
| `--output FILE` | stdout | Write output to a file instead of stdout. |
| `--config FILE` | `asset-check-policy.json` | Policy file to load and validate. |
| `--catalog FILE` | `asset-skill-catalog.json` | Catalog asset to load and validate. |
| `--verbose` | off | Reserved for progress notes; no hidden output is produced. |
| `--version` | — | Print `tools-workbench.py 1.0.0`. |

> **Argument order for `run` and `cleanroom`:** options must come **before** the
> `--` separator. Everything after `--` is the command argv. For example,
> `run --format json --timeout 60 -- python3 -c "print(1)"` is correct;
> `run -- python3 -c "print(1)" --format json` passes `--format json` to the
> child command.

---

## 3. Requirements and setup

### Requirements

- Python 3.9 or newer (tested on 3.13.16).
- Standard library only: `argparse`, `hashlib`, `json`, `importlib.util`,
  `pathlib`, `subprocess`, `tempfile`, `threading`, `platform`, `shutil`, `re`,
  `fnmatch`, `os`, `sys`, `time`, `signal`.
- No pip install, no virtualenv, no package mirror, no model weights, no
  database, no container runtime required.
- Optional local tools reported by `doctor` (never required by the core): `git`,
  `node`, `npm`, `gcc`, `make`, `cmake`, `sqlite3`, `docker`, `kubectl`,
  `terraform`, `pandoc`, `R`, `julia`.

### Setup

No setup step is required. Copy the `offline-engineering-workbench/` folder
beside the parent `SKILL.md` and run the commands above. To use a custom policy:

```bash
cp offline-engineering-workbench/asset-check-policy.json my-policy.json
# edit my-policy.json
python3 offline-engineering-workbench/tools-workbench.py check --root . --config my-policy.json
```

The policy is validated before use; an invalid policy exits with code 2 and a
structured error naming the field.

### Install commands

None. There is no dependency file because the runtime is the standard library.
If a future extension needs a third-party package, add a `tools-requirements.txt`
and document the exact pinned versions here; do not add unpinned dependencies.

---

## 4. Command reference

All commands support `--format` and (except `manifest` and `report`, which use
their own output paths) `--output`. All commands are local-only.

### 4.1 `capabilities` — capability list

Lists the registered catalog capabilities.

```bash
python3 offline-engineering-workbench/tools-workbench.py capabilities --summary
python3 offline-engineering-workbench/tools-workbench.py capabilities --category 29
python3 offline-engineering-workbench/tools-workbench.py capabilities --search "lockfile"
python3 offline-engineering-workbench/tools-workbench.py capabilities --limit 20 --format json
```

| Option | Meaning |
| --- | --- |
| `--summary` | Show the 32 categories with skill counts instead of all skills. |
| `--category N` | Restrict to a category number, id (`cat-29`), slug, or name. |
| `--search TEXT` | Case-insensitive search over skill name, description, category, and scope. |
| `--limit N` | Cap the number of rows (0 = all). |

Default output is one row per skill: `id`, `category`, `name`. JSON output is
`{tool, totals:{categories,skills}, returned, skills:[...]}`. `totals` is always
`{categories: 32, skills: 480}` for the shipped asset.

### 4.2 `category` — one category and its skills

```bash
python3 offline-engineering-workbench/tools-workbench.py category 29
python3 offline-engineering-workbench/tools-workbench.py category "Machine Learning" --format json
```

The query accepts a category number, `cat-NN` id, slug, or exact/unique name.
Output includes the category scope, skill count, and the 15 skill ids and names.
JSON output is the full category object.

### 4.3 `skill` — one skill and its context

```bash
python3 offline-engineering-workbench/tools-workbench.py skill cat-29-skill-03
python3 offline-engineering-workbench/tools-workbench.py skill "Dependency Lockfile Manager"
```

The query accepts a skill id, slug, exact name, or a unique description fragment.
Output includes the skill description, category, scope, and related skills.
Errors: `skill-not-found` (exit 2) with candidate ids.

### 4.4 `plan` — deterministic task plan

```bash
python3 offline-engineering-workbench/tools-workbench.py plan \
  --task "Build a local Kafka event pipeline with exactly-once semantics" \
  --max-skills 12 --max-per-category 2 --root .
python3 offline-engineering-workbench/tools-workbench.py plan --task-file task.md --format json --output reports/plan.json
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--task TEXT` | — | Task description (plain text or Markdown; HTML is not parsed). |
| `--task-file FILE` | — | Read the task from a file. |
| `--max-skills N` | 12 | Maximum implementation skills (1–40). |
| `--max-per-category N` | 2 | Maximum skills per category (1–5). |
| `--root PATH` | `.` | Project root recorded in the plan. |

The planner tokenizes the task, removes stopwords, and scores every skill using
name, scope, category, and description tokens with IDF down-weighting for generic
words. It then fills four lifecycle phases:

1. **prepare** — offline runtimes, packages, builds, artifacts.
2. **implement** — the top-ranked implementation skills.
3. **verify** — tests, static analysis, security, quality gates.
4. **deliver** — documentation, packaging, recovery.

Each phase has an objective, a gate, selected skills with scores and matched
terms, and exact relative commands. The plan also records inputs, outputs,
limits, approval classes, and rollback steps. `plan_id` is a SHA-256 prefix of the
task and parameters, so the same request always produces the same plan. Different
tasks produce different plans.

JSON output shape:

```json
{
  "plan_id": "…16 hex…",
  "task": "…",
  "confidence": "high|medium|low",
  "totals": {"registered_skills": 480, "registered_categories": 32,
             "selected_skills": 18, "categories_covered": 12,
             "max_skills": 12, "max_per_category": 2},
  "matched_terms": ["…"],
  "phases": [{"id": "prepare", "name": "…", "objective": "…", "gate": "…",
              "skills": [{"id": "cat-01-skill-03", "name": "…", "score": 4.25,
                          "matched_terms": ["…"], "why": "…"}],
              "commands": ["python3 offline-engineering-workbench/tools-workbench.py …"]}],
  "selected_skills": ["…"],
  "inputs": ["…"], "outputs": ["…"], "limits": {"…": "…"},
  "approval_required": ["destructive-filesystem", "…"],
  "rollback": ["…"]
}
```

Errors: `task-empty`, `task-not-usable` (exit 2).

### 4.5 `check` — offline engineering audit

```bash
python3 offline-engineering-workbench/tools-workbench.py check --root . --format json --output reports/check.json
python3 offline-engineering-workbench/tools-workbench.py check --root . --only security --exclude docs
python3 offline-engineering-workbench/tools-workbench.py check --root . --fail-on critical
```

| Option | Meaning |
| --- | --- |
| `--root PATH` | Project root to audit (default `.`). |
| `--only RULE` | Repeatable. Run only rules whose id contains the value (e.g. `security`, `deps/missing-lockfile`). |
| `--exclude RULE` | Repeatable. Skip matching rules. |
| `--fail-on SEVERITY` | Repeatable. Override `quality_gate.fail_on` for this run. |
| `--config FILE` | Policy file. |

The engine walks the tree (skipping `.git`, `node_modules`, virtualenvs, caches,
build outputs, and policy exclusions), reads text files up to
`max_text_bytes`, runs the 25 rules, and returns a structured result. It never
writes to the audited tree.

Result shape:

```json
{
  "status": "pass|fail",
  "gate": "pass|fail",
  "tool": "offline-engineering-workbench",
  "tool_version": "1.0.0",
  "root": "…",
  "generated_at": "…Z",
  "policy": {"path": "…", "sha256": "…", "fail_on": ["critical","high"],
             "disabled_rules": []},
  "environment": {"python": "3.13.16", "platform": "…", "executable": "…",
                  "toolchain": {"git": "/usr/bin/git", "…": null}},
  "summary": {
    "files_total": 0, "files_text": 0, "files_binary_or_skipped": 0,
    "bytes_scanned": 0, "rules_run": 25, "rules_available": 25,
    "findings": 0, "failing_findings": 0,
    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
    "by_category": {}, "truncated_rules": [], "truncated_total": false
  },
  "catalog_categories_applied": [{"number": 29, "name": "Security, Vulnerability, Secrets, and SBOM"}],
  "findings": [
    {"rule_id": "security/embedded-secret", "title": "…", "severity": "critical",
     "category": "security", "catalog_categories": [29], "path": "src/app.py",
     "line": 3, "evidence": "<redacted>", "message": "…", "remediation": "…"}
  ]
}
```

Exit codes: `0` pass, `4` gate failed (one or more findings at a `fail-on`
severity), `2` invalid input, `5` runtime error.

### 4.6 `manifest` — SHA-256 provenance manifest

```bash
python3 offline-engineering-workbench/tools-workbench.py manifest --root . --output reports/manifest.json
python3 offline-engineering-workbench/tools-workbench.py manifest --root . --output reports/manifest.json --deterministic
python3 offline-engineering-workbench/tools-workbench.py manifest --root . --exclude "*.parquet"
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--root PATH` | `.` | Project root to fingerprint. |
| `--output FILE` | `reports/manifest.json` | Manifest path. |
| `--exclude GLOB` | — | Extra exclude glob (repeatable). |
| `--deterministic` | off | Omit `created_at` for byte-identical output. |

The manifest records `schema_version`, `tool`, `tool_version`, `root`,
`file_count`, `total_bytes`, `files[{path,size,sha256}]`, `environment`
(Python, platform, executable, optional toolchain paths), and `created_at`
unless `--deterministic`. Files larger than `max_file_bytes` and policy
exclusions are skipped. The manifest output file is excluded from the scan.
JSON output is the manifest; table output prints a one-line summary.

### 4.7 `verify` — verify a manifest

```bash
python3 offline-engineering-workbench/tools-workbench.py verify --manifest reports/manifest.json
python3 offline-engineering-workbench/tools-workbench.py verify --manifest reports/manifest.json --root /path/to/project --format json
```

The command validates the manifest against `asset-manifest.schema.json`, then
recomputes SHA-256 for every listed file and compares size and digest. It also
reports files added since the manifest was written.

Result shape:

```json
{
  "status": "ok|mismatch|invalid",
  "manifest": "…", "root": "…",
  "verified": 9, "expected_files": 9,
  "missing": [], "modified": [{"path": "src/app.py", "expected": "…", "actual": "…"}],
  "added": [], "environment": {"…": "…"}, "generated_at": "…Z"
}
```

Exit codes: `0` verified, `6` mismatch or schema-invalid, `2` missing manifest,
`5` runtime error. A schema-invalid manifest returns `status: "invalid"` with
`schema_errors`.

### 4.8 `run` — bounded command execution

```bash
python3 offline-engineering-workbench/tools-workbench.py run --timeout 900 -- python3 -m pytest -q
python3 offline-engineering-workbench/tools-workbench.py run --dry-run -- rm -rf ./scratch-output
python3 offline-engineering-workbench/tools-workbench.py run --approve --timeout 60 -- curl --max-time 10 http://127.0.0.1:9/
python3 offline-engineering-workbench/tools-workbench.py run --retries 2 --log reports/run.jsonl -- make test
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--cwd PATH` | `.` | Working directory (must exist). |
| `--timeout SECONDS` | 300 | Hard timeout, clamped to 1–3600. |
| `--retries N` | 0 | Retries for normal-risk commands, clamped to 0–5. |
| `--max-output-bytes N` | 200000 | Captured stdout/stderr cap per stream, clamped to 1 KiB–5 MiB. |
| `--approve` | off | Acknowledge a risky command. |
| `--dry-run` | off | Resolve and classify without executing. |
| `--log FILE` | — | Append JSONL audit records. |
| `argv` | — | Command after `--`; executed directly, never through a shell. |

Risk classes come from `approval_required_patterns` in the policy:
destructive filesystem, privilege change, service control, network egress,
package install/update/publish, destructive data operations, and container
publish/delete. A command that matches any pattern is **blocked with exit 3**
unless `--approve` is passed. Dry runs never execute and report
`would_require_approval`. Retries are disabled for risky commands even when
approved. On timeout the process group receives SIGTERM, then SIGKILL if it does
not exit; the result reports `timed_out: true` and exit code 124.

Result shape:

```json
{
  "status": "ok|failed|blocked|dry-run",
  "command": ["…"], "command_display": "…redacted…", "cwd": "…",
  "risk": "normal|high|critical",
  "reasons": [{"id": "destructive-filesystem", "risk": "critical",
               "reason": "…", "matched": "<redacted>"}],
  "approval": {"required": true, "granted": false},
  "timeout_seconds": 300, "retries": 0, "max_output_bytes": 200000,
  "exit_code": 0, "timed_out": false, "duration_ms": 16,
  "attempts": [{"attempt": 1, "exit_code": 0, "timed_out": false,
                "duration_ms": 16, "stdout_bytes": 7, "stderr_bytes": 0,
                "output_truncated": false}],
  "stdout": "…", "stderr": "…", "output_truncated": false,
  "message": "Command completed successfully."
}
```

Exit codes: `0` ok or dry-run, `3` blocked, `5` command failed or timed out
(the result also sets `timed_out: true` and `exit_code: 124`), `2` invalid
argv/cwd.

### 4.9 `cleanroom` — copy, check, optionally run, clean up

```bash
python3 offline-engineering-workbench/tools-workbench.py cleanroom --root . --format json
python3 offline-engineering-workbench/tools-workbench.py cleanroom --root . --keep
python3 offline-engineering-workbench/tools-workbench.py cleanroom --root . --timeout 600 -- python3 -m pytest -q
```

The command creates a temporary directory (`workbench-cleanroom-*`) under the
system temp directory or `--workdir`, copies the project with policy exclusions
and without following symlinks, skips files larger than `max_file_bytes`, runs
the full check on the copy, optionally runs one command in the copy (without
approval), writes a structured result, and removes the temporary directory
unless `--keep` is passed.

Result shape:

```json
{
  "status": "ok|gate-failed",
  "root": "…", "cleanroom": "…/workbench-cleanroom-…",
  "copied_files": 9, "skipped_files": 0, "kept": false,
  "check": {"status": "fail", "findings": 21,
            "by_severity": {"critical": 4, "high": 5, "medium": 7, "low": 5, "info": 0},
            "rules_run": 25},
  "command": {"…": "run result when a command was supplied, otherwise null"},
  "generated_at": "…Z"
}
```

Exit codes: `0` clean-room check passed, `4` gate failed, `2` invalid root or
workdir, `5` runtime error. The clean room is a copy, not a VM or container
sandbox; it runs with the same user privileges.

### 4.10 `report` — Markdown handoff report

```bash
python3 offline-engineering-workbench/tools-workbench.py report --result reports/check.json --output reports/report.md
python3 offline-engineering-workbench/tools-workbench.py report --result reports/plan.json --template my-template.md --output reports/plan.md
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--result FILE` | required | JSON produced by `check` or `plan`. |
| `--template FILE` | `asset-report-template.md` | Markdown template. |
| `--output FILE` | `reports/report.md` | Markdown output path. |

The renderer substitutes `{{title}}`, `{{status}}`, `{{generated}}`,
`{{tool_name}}`, `{{tool_version}}`, `{{root}}`, `{{command}}`, `{{summary}}`,
`{{severity_table}}`, `{{findings_table}}`, `{{capabilities}}`, and
`{{limitations}}`. Unresolved placeholders are a hard error (`template-placeholder`,
exit 5). For a plan result, the report lists the selected skills per phase; for
a check result, it lists findings and the catalog categories applied.

### 4.11 `doctor` — runtime and asset status

```bash
python3 offline-engineering-workbench/tools-workbench.py doctor
python3 offline-engineering-workbench/tools-workbench.py doctor --format json
```

Checks the Python version, standard-library modules, optional local tools,
the four assets, catalog counts (32 categories, 480 skills), and records that no
network calls are implemented. Exit `0` when required checks pass, `5` when a
required check is missing.

### 4.12 `config` — effective policy

```bash
python3 offline-engineering-workbench/tools-workbench.py config
python3 offline-engineering-workbench/tools-workbench.py config --format json
```

Shows the policy after merging the shipped asset over safe defaults, including
thresholds, quality gate, exclusions, disabled rules, approval patterns, and
environment requirements.

### 4.13 `rules` — rule catalog

```bash
python3 offline-engineering-workbench/tools-workbench.py rules
python3 offline-engineering-workbench/tools-workbench.py rules --format json
```

Lists all 25 rules with id, title, severity, category, kind (`pattern` or
`aggregate`), and catalog category mapping.

### 4.14 `selftest` — end-to-end validation

```bash
python3 offline-engineering-workbench/tools-workbench.py selftest
python3 offline-engineering-workbench/tools-workbench-selftest.py
```

Builds temporary flawed and clean projects, exercises the catalog, policy,
checks, filters, recovery, manifest/verify (including tamper detection), planner
determinism, runner dry-run/block/approve/retry/timeout/log, report rendering,
and every CLI command and structured error. Prints one line per check and a
summary. Exit `0` when all checks pass, `10` otherwise. Temporary files are
removed in a `finally` block.

### 4.15 `help` — built-in help

```bash
python3 offline-engineering-workbench/tools-workbench.py help
python3 offline-engineering-workbench/tools-workbench.py help check
python3 offline-engineering-workbench/tools-workbench.py --help
```

`help` with no topic prints the full command list. `help COMMAND` prints that
command's help. `--help` and `-h` work on every command.

---

## 5. Complete capability list

### 5.1 Catalog coverage (32 categories, 480 skills)

Every category has exactly 15 skills. All 480 are registered in
`asset-skill-catalog.json` and are reachable through `capabilities`, `category`,
`skill`, and `plan`.

| # | Category | Skills |
| --- | --- | --- |
| 1 | Offline Runtime and Package Supply Chain | 15 |
| 2 | Python, R, Julia, and Scientific Scripting | 15 |
| 3 | C, C++, Fortran, and Native Toolchains | 15 |
| 4 | Java, JVM, and Enterprise Build Systems | 15 |
| 5 | .NET, C Sharp, and PowerShell | 15 |
| 6 | Node.js, TypeScript, and Frontend Tooling | 15 |
| 7 | Rust, Go, and Modern Systems Languages | 15 |
| 8 | Shell, CLI, and Systems Automation | 15 |
| 9 | Source Control and Large File Management | 15 |
| 10 | Build Systems and Reproducible Builds | 15 |
| 11 | Artifact Repositories and Release Engineering | 15 |
| 12 | Database Servers and Client Tools | 15 |
| 13 | Local Data Warehouses and Analytical Storage | 15 |
| 14 | Message Queues, Streams, and Event Systems | 15 |
| 15 | ETL, ELT, and Data Pipeline Engineering | 15 |
| 16 | Data Quality, Governance, and Lineage | 15 |
| 17 | Containers and Local Kubernetes | 15 |
| 18 | Infrastructure as Code and Configuration Management | 15 |
| 19 | GPU Computing and Accelerated Workloads | 15 |
| 20 | Scientific Libraries and Numerical Computing | 15 |
| 21 | Machine Learning and Local Model Development | 15 |
| 22 | Statistics, Experiments, and Research Methods | 15 |
| 23 | Simulation, Modeling, and High-Performance Computing | 15 |
| 24 | Geospatial, Image, Signal, and Domain Data | 15 |
| 25 | Unit, Integration, and System Testing | 15 |
| 26 | Static Analysis, Linters, and Formatters | 15 |
| 27 | Coverage, Profiling, Debugging, and Performance | 15 |
| 28 | Fuzzing, Property Testing, and Adversarial Validation | 15 |
| 29 | Security, Vulnerability, Secrets, and SBOM | 15 |
| 30 | Benchmarking, Validation, and Quality Gates | 15 |
| 31 | Documentation, Notebooks, APIs, and Developer Experience | 15 |
| 32 | Packaging, Backup, Recovery, and Catalog Governance | 15 |

The catalog also defines four lifecycle phases used by `plan`:

| Phase | Categories | Gate |
| --- | --- | --- |
| prepare | 1, 2, 10, 11 | Runtimes, packages, and artifacts resolve from local mirrors with verified signatures and lockfiles. |
| implement | 3–9, 12–24 | Build or pipeline completes deterministically with pinned inputs and recorded provenance. |
| verify | 25–30 | Tests, static analysis, security scans, and quality gates pass or carry an approved exception. |
| deliver | 31, 32 | Reproducible package, checksums, handoff guide, and tested rollback are produced. |

### 5.2 Implemented check rules (25)

| Rule | Severity | Category | Catalog | Detects |
| --- | --- | --- | --- | --- |
| `security/embedded-secret` | critical | security | 29 | Credential-like literals in tracked files. |
| `security/private-key-material` | critical | security | 29 | PEM private-key blocks. |
| `security/private-key-file` | critical | security | 29 | `.pem`, `.key`, or OpenSSH identity files. |
| `security/weak-hash` | medium | security | 29 | MD5/SHA-1 usage. |
| `security/dynamic-code-execution` | high | security | 29 | `eval`, `exec`, `new Function`. |
| `security/debug-enabled` | low | security | 29 | Debug mode on by default. |
| `config/committed-env-file` | critical | security | 29 | Tracked `.env` files (examples excluded). |
| `build/network-fetch` | high | build | 1, 10 | curl/wget/pip/npm/apt/git clone in build files. |
| `build/download-pipe` | critical | build | 1, 29 | Remote script piped into a shell. |
| `build/destructive-command` | critical | build | 8, 10 | `rm -rf`, `mkfs`, `dd`, `shutdown`, `chmod 777`. |
| `build/privilege-escalation` | high | build | 8 | `sudo` in automation. |
| `build/absolute-path` | medium | build | 1, 10 | Host-specific absolute paths. |
| `build/unpinned-base-image` | medium | build | 1, 17 | `FROM latest` or untagged base image. |
| `deps/missing-lockfile` | high | deps | 1, 10 | Python/Node/Rust/Go/.NET manifest without a lockfile. |
| `deps/unpinned-python` | high | deps | 1, 2 | Requirements not pinned with `==`. |
| `deps/unpinned-node` | medium | deps | 1, 6 | package.json ranges/tags instead of exact versions. |
| `tests/missing-test-suite` | medium | tests | 25 | Source files with no test signal. |
| `docs/missing-readme` | low | docs | 31 | No README. |
| `docs/missing-license` | low | docs | 31, 32 | No license/copying file. |
| `data/missing-provenance` | medium | data | 15, 16, 24 | Data files without a manifest/checksums. |
| `data/large-file` | medium | data | 9, 32 | Files over the size threshold without LFS. |
| `supply-chain/missing-sbom` | low | supply-chain | 29 | Dependency manifest without an SBOM. |
| `supply-chain/unpinned-action` | medium | supply-chain | 1, 11, 29 | CI action referenced by tag/branch. |
| `repro/nondeterministic-time` | low | repro | 10, 30 | Wall-clock reads in build/data code. |
| `repro/unseeded-random` | low | repro | 10, 20, 21, 22, 30 | Random generator without a visible seed. |

### 5.3 Implemented commands (15)

`capabilities`, `category`, `skill`, `plan`, `check`, `manifest`, `verify`,
`run`, `cleanroom`, `report`, `doctor`, `config`, `rules`, `selftest`, `help`.

---

## 6. Configuration reference

The policy file is merged over `BASE_POLICY` in `tools-workbench-core.py`; any
missing field falls back to the safe default. The merged policy is validated
before use.

| Field | Default | Meaning |
| --- | --- | --- |
| `thresholds.max_file_bytes` | 52428800 | Files larger than this are skipped by checks and manifests. |
| `thresholds.max_text_bytes` | 2097152 | Maximum bytes read from one text file. |
| `thresholds.max_findings_per_rule` | 5 | Cap per rule; the summary records truncation. |
| `thresholds.max_findings_total` | 200 | Hard cap on findings. |
| `thresholds.run_timeout_seconds` | 300 | Default `run` timeout. |
| `thresholds.run_max_output_bytes` | 200000 | Default output cap per stream. |
| `thresholds.run_retries` | 0 | Default retry count. |
| `quality_gate.fail_on` | `["critical","high"]` | Severities that fail the gate. |
| `quality_gate.warn_on` | `["medium","low","info"]` | Documented warning severities. |
| `exclude_dirs` | VCS, `node_modules`, virtualenvs, caches, build outputs | Directories never walked. |
| `exclude_globs` | minified assets, images, archives, binaries, data blobs | Files never read or hashed. |
| `disabled_rules` | `[]` | Rule ids to disable. |
| `allow_paths` | `[]` | Path globs exempt from pattern rules. |
| `approval_required_patterns` | 7 patterns | Regex/risk/reason entries for the `run` gate. |
| `environment.python_min` | `"3.9"` | Minimum Python version reported by `doctor`. |
| `environment.offline_only` | `true` | Declares the offline posture. |

Example: fail only on critical findings and disable the documentation rules:

```json
{
  "quality_gate": {"fail_on": ["critical"]},
  "disabled_rules": ["docs/missing-readme", "docs/missing-license"]
}
```

Run `config` to see the merged result and `rules` to see the rule catalog.

---

## 7. Safety and approval rules

1. **No shell.** `run` uses `subprocess.Popen(argv, shell=False)`. Metacharacters
   in arguments are data, not shell syntax. There is no `bash -c` path.
2. **No network by default.** The tool makes no HTTP, socket, or package-manager
   calls. `doctor` reports this. Any network command run through `run` matches
   the `network-egress` approval pattern and requires `--approve`.
3. **Approval gate.** Commands matching destructive filesystem, privilege,
   service control, network egress, package install/publish, destructive data,
   or container publish patterns are blocked with exit 3 unless `--approve` is
   passed. Approval is recorded in the audit log. Dry runs are allowed and
   report `would_require_approval` without executing.
4. **Timeout and cleanup.** Every command has a hard timeout (default 300 s,
   maximum 3600 s). On timeout the process group is terminated (SIGTERM, then
   SIGKILL), and the result reports `timed_out` and exit 124.
5. **Output caps.** stdout and stderr are capped per stream (default 200 KB,
   maximum 5 MiB) and marked `output_truncated`; the process keeps draining the
   pipes so it cannot deadlock.
6. **Bounded retries.** Retries are limited to 0–5 and are never applied to
   risky commands, even when approved.
7. **No writes to the audited tree.** `check`, `plan`, `doctor`, `config`, and
   `rules` only read. `manifest`, `verify`, `report`, and `cleanroom` write only
   to the paths you specify or to a temporary directory. `cleanroom` removes its
   copy unless `--keep`.
8. **Secret redaction.** Evidence and command displays pass through
   `redact()`, which masks private-key headers, credential-like assignments,
   bearer tokens, and long opaque tokens. The audited file contents are never
   copied into results.
9. **Path safety.** `safe_child()` refuses manifest paths that escape the
   project root. `cleanroom` copies without following symlinks.
10. **No embedded credentials or private endpoints.** The package contains no
    secrets, tokens, or tenant-specific URLs. The catalog descriptions mention
    security topics but contain no credentials.
11. **Approval is not a sandbox.** The approval gate is a guardrail, not a
    security boundary. For untrusted code, use OS-level isolation (container,
    VM, separate user) in addition to this tool.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, pass, verified, or dry-run. |
| 2 | Invalid or missing input (usage error). |
| 3 | Command blocked by the approval gate. |
| 4 | Quality gate failed (finding at a `fail-on` severity). |
| 5 | Runtime error, command failed, or timed out. |
| 6 | Manifest verification failed or manifest schema invalid. |
| 10 | Self-test failed. |
| 130 | Interrupted by the user. |

---

## 8. Validation actually completed

All validation was run in the delivery sandbox with Python 3.13.16. No result
below is claimed without having been executed.

### 8.1 Self-test suite

```bash
python3 offline-engineering-workbench/tools-workbench.py selftest
```

Observed final output:

```text
self-test summary: 77 passed, 0 failed
```

Exit code `0`. The suite creates temporary flawed and clean projects, exercises
every command and structured error, and removes its temporary files.

### 8.2 Catalog and policy assets

- `capabilities --summary` returned 32 categories, each with 15 skills.
- `capabilities` returned 480 skills.
- `capabilities --search kubernetes` returned a non-empty filtered set.
- `category 29` returned category 29 with 15 skills.
- `skill cat-29-skill-03` returned the skill and its related skills.
- `plan` for a churn-model task selected 16–19 skills across 11–12 categories,
  with four lifecycle phases, deterministic `plan_id`, and a different plan for a
  Kafka task.
- `plan` rejected an empty task with exit 2.
- `config` returned the merged policy; `rules` returned 25 rules.
- `doctor` returned 27 checks and confirmed 32 categories / 480 skills.

### 8.3 Check engine

A deliberately flawed fixture (unpinned Python and Node dependencies, no
lockfiles, committed `.env`, secret literal, `eval`, MD5, debug mode, `FROM
python:latest`, `pip install`, `curl … | bash`, `rm -rf /`, absolute path, CI
action tag, data file without provenance, no tests/README/license, unseeded
random) produced:

- `status: fail`, `gate: fail`, exit code `4`.
- 22 findings: 4 critical, 6 high, 7 medium, 5 low.
- All expected rule families fired, including
  `security/embedded-secret`, `deps/unpinned-python`,
  `build/download-pipe`, `build/destructive-command`,
  `config/committed-env-file`, and `supply-chain/unpinned-action`.
- Secret evidence was redacted (`<redacted>`); no `sk_live_…` literal appeared in
  the output.
- `--only security` returned only `security/*` findings.
- `--exclude security --exclude data --exclude docs` removed those families.
- `--fail-on bogus` exited 2 with a structured `fail-on-invalid` error.
- `--root ./does-not-exist` exited 2 with a structured `root-missing` error.

A clean fixture (README, LICENSE, pinned requirements, `poetry.lock`, SBOM,
provenance manifest, `.gitattributes` with LFS, source, test) produced
`status: pass`, zero findings, exit 0.

### 8.4 Manifest and verification

- `manifest` on the clean fixture wrote 9 files / 312 bytes and validated
  against `asset-manifest.schema.json`.
- `verify` on the unchanged manifest returned `status: ok`, 9 verified, exit 0.
- After modifying `src/app.py`, `verify` returned `status: mismatch`, exit 6,
  and named the modified path with expected/actual SHA-256 values.
- Removing `file_count` from the manifest produced `status: invalid`, exit 6,
  with `$ missing required property 'file_count'`.
- `--deterministic` omitted `created_at`.

### 8.5 Bounded runner

- `run --dry-run -- rm -rf …` returned `status: dry-run`, risk `critical`,
  `would_require_approval: true`, and did not execute.
- `run -- rm -rf …` returned `status: blocked`, exit 3, required approval
  `destructive-filesystem`.
- `run -- python3 -c "print('hello')"` returned `status: ok`, exit 0, captured
  stdout.
- `run --retries 1 -- python3 -c "sys.exit(1)"` recorded 2 attempts.
- `run --timeout 1 -- python3 -c "time.sleep(5)"` returned `timed_out: true`,
  exit code 124, and the timeout message.
- `run --approve -- curl --max-time 2 http://127.0.0.1:9/` was allowed to run
  (risk high, approval granted) and failed with curl exit 7, proving the gate
  permits approved commands.
- `--log` wrote JSONL `attempt` and `completed` records.
- An empty argv exited 2 with `command-empty`.

### 8.6 Clean room and report

- `cleanroom` copied 9 files from the flawed fixture, ran 25 rules on the copy,
  reported 21 findings and `gate-failed`, and removed the temporary directory.
- `--keep` preserved the directory and printed its path.
- A command supplied after `--` ran inside the copy and its stdout was captured.
- `report` rendered a Markdown report with the title, summary, severity table,
  findings table, capabilities, and limits sections.
- A plan result rendered through the same template with the selected skills per
  phase.

### 8.7 CLI and error handling

- `--version` printed `tools-workbench.py 1.0.0`.
- `help` and `help check` printed help and exited 0.
- `capabilities --summary --format json` parsed as JSON with 32 categories.
- `check --root <flawed> --format json` exited 4 with structured findings.
- `run --format json -- rm -rf …` exited 3.
- `run --format json` (no command) exited 2.
- `plan --task ""` exited 2.
- `verify` on a missing manifest exited 2.
- `manifest` and `verify` round-tripped successfully.

### 8.8 Skill Tool self-audit

Running `check --root .` on the delivered folder returns `status: pass` with
only low/medium heuristic findings:

- `data/missing-provenance` on `asset-check-policy.json` (the rule treats JSON
  as data; this is a documented false positive for a tool package),
- `docs/missing-readme` and `docs/missing-license` (the package intentionally has
  `knowledge-readme.md` and no unprefixed `README`/`LICENSE` because the Skill
  Tool structure forbids unprefixed files),
- `repro/nondeterministic-time` on `tools-workbench-core.py` (the tool records
  timestamps by design).

No critical or high findings are present.

---

## 9. Limits and unavailable integrations

Stated plainly so the package is not mistaken for something it is not:

1. **The 480 catalog skills are registered, not each implemented.** The source
   catalog is a blueprint. This tool implements the shared lifecycle and 25
   cross-cutting rules. It does not bundle compilers, JVMs, .NET SDKs, Node,
   Rust, Go, CUDA, ROCm, databases, Kafka, Kubernetes, Terraform, or ML
   frameworks. Those must exist locally and be invoked through `run` with
   approval.
2. **No network, package installs, or credential access.** The tool cannot fetch
   a wheel, pull an image, send a notification, or read a secret store. It is
   offline by design.
3. **Checks are heuristics, not full SAST/DAST.** The 25 rules are deterministic
   pattern and structure checks. They do not parse every language, build a
   semantic model, or replace language-specific analyzers (catalog categories
   25–30). False positives and false negatives are possible; tune the policy.
4. **No actual build, test, or simulation execution by `check`.** `check` is
   read-only. Running tests or builds requires `run` or `cleanroom` with an
   explicit command.
5. **`cleanroom` is a copy, not a VM/container sandbox.** It runs with the same
   user privileges and does not isolate syscalls, network namespaces, or devices.
   Use OS-level isolation for untrusted code.
6. **Manifests are integrity records, not signatures.** `manifest`/`verify` use
   SHA-256 and sizes; they do not sign artifacts or verify publisher identity.
   Combine with the signature and SBOM capabilities in catalog categories 1, 11,
   and 29.
7. **No database, container, GPU, or cloud integration.** `doctor` reports
   whether a local tool exists; it does not install or configure one.
8. **Platform coverage.** Tested on Linux with Python 3.13.16. The code uses
   only the standard library and has a fallback when POSIX process groups are
   unavailable, but Windows and macOS were not tested. `os.killpg` is
   POSIX-specific; the fallback terminates the direct child.
9. **Size and depth limits.** Files over `max_file_bytes` (50 MB) are skipped by
   checks and manifests; text reads are capped at 2 MB per file; findings are
   capped at 5 per rule and 200 total. Clean-room copies skip excluded files and
   files over the size threshold. Very large projects should raise the
   thresholds deliberately.
10. **No exception registry.** The policy can disable rules or change `fail_on`,
    but there is no formal approved-exception workflow. Record exceptions in the
    handoff report or a local policy.
11. **No secret-store integration.** Secrets must be supplied by the caller's
    environment; the tool never asks for or stores them.
12. **No hosted-service dependency.** All assets are local. If a future extension
    references a hosted URL, that is a hosting/network limitation outside this
    package, not something the package can prevent.

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `catalog-missing` | Running from the wrong directory or a renamed asset. | Run from the future skill root, or pass `--catalog offline-engineering-workbench/asset-skill-catalog.json`. |
| `policy-missing` / `policy-invalid` | Wrong `--config` path or invalid JSON. | Use `config` to inspect the merged policy; fix the field named in `errors`. |
| `catalog-invalid` | The catalog asset was edited or truncated. | Restore `asset-skill-catalog.json` from the ZIP; run `doctor`. |
| `root-missing` | The audited path does not exist. | Pass an existing directory to `--root`. |
| `command-empty` | No argv after `--`. | Provide a command, e.g. `-- python3 -m pytest -q`. |
| Options passed to the child | Options placed after `--`. | Move `--format`, `--timeout`, etc. before `--`. |
| `status: blocked`, exit 3 | Command matches an approval pattern. | Remove the risky operation or rerun with `--approve` after reviewing the reason. |
| `status: failed`, exit 5 | Command exited non-zero or timed out. | Inspect `stderr`, `attempts`, and the audit log; fix the command or raise `--timeout`. |
| `status: mismatch`, exit 6 | A file changed after the manifest was written. | Restore the file from the artifact repository, or regenerate the manifest if the change is intentional. |
| `status: invalid`, exit 6 | Manifest does not match the schema. | Regenerate with `manifest`; do not hand-edit required fields. |
| `check` exit 4 | Findings at a `fail-on` severity. | Fix the findings, or adjust `--fail-on`/policy after review. |
| `template-placeholder` | Custom template has an unknown `{{slot}}`. | Use the shipped template or add a value for the slot. |
| `path-escape` | A manifest path points outside the root. | Treat the manifest as untrusted; regenerate it. |
| `PermissionError` in cleanroom | `--workdir` is not writable. | Choose a writable `--workdir`. |
| Self-test exit 10 | An asset or behavior regressed. | Read the `FAIL` lines, restore the affected file, rerun `selftest`. |
| Slow audit on a large tree | Many files or large text files. | Lower `max_text_bytes`, add `exclude_dirs`/`exclude_globs`, or audit a subdirectory. |

**Recovery procedure after a failed gate:**

1. Run `check --format json --output reports/check.json` and inspect the findings.
2. Fix the highest-severity findings first (critical, then high).
3. Re-run `check`; confirm the gate passes.
4. Run `cleanroom` to confirm the fixed project passes in a clean copy.
5. Regenerate the manifest and verify it.
6. Render the report and attach it to the handoff.

**Recovery after a corrupted package:** extract the delivered ZIP into a clean
directory, run `doctor`, then `selftest`. If `selftest` passes, the package is
intact. Do not reuse a partially written ZIP.

---

## 11. Extension and maintenance

### Add a check rule

1. Add a `_rule(...)` entry to `PATTERN_RULES` in
   `tools-workbench-checks.py` (id, title, severity, category, catalog
   categories, applies globs, regex, message, remediation, optional exclude
   path).
2. For a structural rule, add metadata to `RULE_METADATA`, a function returning
   findings, and entries in `AGGREGATE_RULES` and `AGGREGATE_CHECKS`.
3. Run `rules` to confirm the rule appears, then `selftest`.
4. Document the rule in section 5.2 of this file.

### Add a command

1. Add a subparser in `build_parser()` in `tools-workbench.py`.
2. Add a `cmd_*` function and register it in `DISPATCH`.
3. Reuse `_policy`, `_catalog`, `_fmt`, `_out`, and `core.emit` for consistent
   structured output and exit codes.
4. Document it in section 4 and run `help`.

### Change the catalog

The catalog asset is the extracted PDF content. To regenerate it:

1. Extract text from the PDF with a local PDF library (the delivery used
   `pypdf`; no network is required once installed).
2. Remove page headers/footers and page numbers.
3. Parse `N. Skill Name:` blocks and category headings.
4. Keep the schema: `schema_version`, `totals`, `lifecycle_phases`,
   `categories[].skills[]` with `id`, `number`, `name`, `slug`, `scope`,
   `description`.
5. Validate with `doctor` and `capabilities --summary`; the counts must be 32
   and 480 unless the source catalog changes.
6. Update this file's counts and category table.

### Change the policy

Edit `asset-check-policy.json` or pass a custom file with `--config`. Run
`config` to inspect the merged result and `check` on a fixture to confirm the
behavior. Never disable a rule without recording why in the handoff report.

### Maintain the package

- Keep the folder name `offline-engineering-workbench` (lowercase, hyphenated).
- Keep every file prefixed `tools-`, `knowledge-`, or `asset-`.
- Do not add subfolders, `SKILL.md`, or unprefixed files.
- Keep all internal references relative to the future skill root and
  case-sensitive.
- Re-run `selftest` after any change.
- Rebuild the ZIP from the folder, not from a wrapper directory.

---

## 12. Skill package compatibility and delivery

- **Skill Tool folder:** `offline-engineering-workbench/`.
- **Folder name pattern:** `^[a-z0-9]+(?:-[a-z0-9]+)*$` — matches.
- **Files:** 11, all directly inside the folder. No subfolders.
- **Prefixes:** 6 `tools-*`, 1 `knowledge-*`, 4 `asset-*`. No unprefixed files.
- **`SKILL.md`:** none. The folder can be copied directly beside a parent
  package's root `SKILL.md` without modification.
- **Depth:** every file is one level below the Skill Tool folder, well within
  three levels of the future skill root.
- **Combined package size:** 11 files, far below the 500-file limit.
- **References:** all commands and assets use relative paths such as
  `offline-engineering-workbench/tools-workbench.py` and
  `offline-engineering-workbench/asset-check-policy.json`. No absolute paths,
  drive letters, parent traversal, tenant paths, or generated URLs.
- **Clean-root test:** the folder was copied into a temporary skill root beside
  a test `SKILL.md`; the primary entry point and the main commands (`doctor`,
  `capabilities`, `category`, `skill`, `plan`, `check`, `manifest`, `verify`,
  `run`, `cleanroom`, `report`, `config`, `rules`, `selftest`) were run using only
  relative paths from that root. The test `SKILL.md` is not part of the delivered
  ZIP.
- **ZIP:** `offline-engineering-workbench.zip` contains the folder directly at
  the archive root, with no wrapper folder, no nested archive, and no
  `__pycache__` or temporary files. Extracting the ZIP produces
  `offline-engineering-workbench/` immediately.
- **Archive inspection:** the delivered ZIP was listed and extracted into a
  clean temporary location; the extracted folder was re-run with `selftest` and
  the primary commands.

### Delivery contents

```text
offline-engineering-workbench.zip
└── offline-engineering-workbench/
    ├── tools-workbench.py
    ├── tools-workbench-core.py
    ├── tools-workbench-checks.py
    ├── tools-workbench-runner.py
    ├── tools-workbench-planner.py
    ├── tools-workbench-selftest.py
    ├── knowledge-readme.md
    ├── asset-skill-catalog.json
    ├── asset-check-policy.json
    ├── asset-report-template.md
    └── asset-manifest.schema.json
```

---

## 13. Source and attribution

- **Source document:** `Full_Offline_Code_Data_Scientific_Work_Skill_Catalog.pdf`
  (52 pages, catalog edition November 2026).
- **Extraction:** category headings and `Skill Name:` blocks were parsed from
  the PDF text; page headers, footers, and page numbers were removed. Skill
  descriptions are the catalog's text with the leading `Description:` label
  removed. Category scope is the common "Designs, executes, or validates …"
  clause across the category's 15 skills.
- **Catalog totals:** 32 categories, 480 skills, 15 skills per category.
- **No external data or services** are used at runtime. The catalog asset is the
  only extracted data, and it is licensed with the rest of the package for use
  with the parent skill.

---

*End of knowledge-readme.md — the single knowledge file for this Skill Tool.*
