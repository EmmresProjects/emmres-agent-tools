# Orchestration, Planning, and Work Splitting — Skill Tool

**Type:** reusable Skill Tool component folder for a future Copilot skill. It is **not** a complete skill: there is deliberately no `SKILL.md` in this folder. Copy this folder directly beside a parent package's root `SKILL.md`.

**Primary entry point:** `orchestration-planning-and-work-splitting/tools-work-splitter.py`

**Exact run command (bash):**

```bash
bash -lc 'cd <future-skill-root> && python3 orchestration-planning-and-work-splitting/tools-work-splitter.py help'
```

`cd <future-skill-root>` means the directory that contains both the parent `SKILL.md` and this folder. All internal references in code and config are relative to that root, e.g. `orchestration-planning-and-work-splitting/asset-skill-catalog.json`.

---

## 1. What this is and why this capability

The source blueprint (`Ultimate_Agent_Skill_Catalog.pdf`, 38 pages, "Ultimate Agent Skill Catalog | Capability Blueprint") is a catalog of **242 skills in 24 categories**. It states: *"This catalog defines skill concepts only. It does not create or install the skills"*, and its completion note recommends converting **one category at a time into tested skill packages, beginning with the orchestration foundation**. It also gives Category 1 — **"Orchestration, Planning, and Work Splitting"** — a full flagship specification ("Powerful Work Splitter").

Following the one-unified-folder default, this Skill Tool is **one** folder whose umbrella is that category. Its runtime implements **all 12 capabilities of Category 1**, including the flagship, as commands on one shared engine. In addition, it carries the **entire 24-category / 242-skill catalog as a live, configurable registry**, so `intake`, `catalog` and `brief` can parse, select and stage-plan any category or several categories from the source without needing another folder.

Folder name: `orchestration-planning-and-work-splitting` (matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`).

### Complete capability list (Category 1, all implemented)

| # | Capability | Command(s) | What it actually does |
|---|---|---|---|
| 1 | Powerful Work Splitter | `assess`, `worked-example`, `run`, `recover` | Scores scope, file volume, dependencies, tool calls, uncertainty, verification burden and context size; decides one response vs staged work; recommends a stage count; asks the user to choose; labels `Stage X of Y`; enforces the file contract, progress contract, final assembly stage and recovery behavior. `worked-example` reproduces the blueprint's 8 → 5 → 10 example. |
| 2 | Single-Prompt Feasibility Assessor | `assess` | Compares artifacts, context size, tool limits, risk and validation steps to a single-turn budget; returns a one-turn decision with reasons and a fallback staged plan. |
| 3 | Adaptive Prompt Count Estimator | `estimate` | Converts work packages, review gates, file groups and integration steps into `recommended`, `minimum`, `maximum`; checks the count is neither inflated nor too small for safe verification. |
| 4 | User-Controlled Stage Negotiator | `negotiate`, `assess --stages N` | Rebalances every requirement and quality gate across a user-chosen stage count; returns the revised stage map, explicit trade-offs, and a `requirements_dropped: 0` guarantee. |
| 5 | Checkpoint and Continue Controller | `checkpoint`, `continue` | Records a checkpoint receipt (completed work, verified checks, state path, blockers, next actions), pauses at a safe boundary, recognises `Continue/proceed/go on/next/resume/carry on`, and rejects ambiguous or scope-changing replies while preserving state. |
| 6 | Dynamic Stage Expansion Manager | `expand` | Extends an active plan, preserves completed stages byte-for-byte, renumbers future stages, redistributes remaining work and adds validation gates. |
| 7 | Dynamic Stage Compression Manager | `compress` | Merges compatible work packages while protecting critical checks and final assembly; discloses quality trade-offs. |
| 8 | Dependency-Aware Task Decomposer | `plan` | Breaks the goal into atomic ordered packages with inputs, outputs, pass conditions, successor links, a cycle-checked dependency graph and an executable sequence. |
| 9 | Parallel Workstream Coordinator | `plan`, `run` | Groups independent packages into research / drafting / coding / media / validation streams and emits a merge contract (naming, schema, versioning, compatibility). |
| 10 | Critical Path Scheduler | `plan` | Computes earliest start, slack, total duration, buffer and escalation triggers for the longest dependency chain. |
| 11 | Milestone Gatekeeper | `gate`, `checkpoint` | Applies entry/exit criteria per phase (discovery/build/test/release); requires concrete, reproducible evidence; returns `pass`, `repair` or `blocked` with a next action. |
| 12 | Final Assembly Orchestrator | `assemble` | Combines stage outputs, detects naming conflicts / broken references / forbidden or empty artifacts, builds a requirement coverage matrix, verifies the final stage includes assembly and polish, and returns a completion summary or a defect list. |

### Supporting operations (documented functional reasons)

| Command | Responsibility |
|---|---|
| `intake` | **Input handling.** Parses a single capability, a numbered list, one category or several categories from plain text, Markdown or copied HTML. Strips tags, CSS rules, `class`/`data-*`/`style` attributes, comments, numbering marks and extra spacing; extracts real category names, Skill Tool names and descriptions; outputs **one unified** spec (`umbrella`, `unified.folder_name`, `capabilities`). |
| `catalog` | `list` / `category` / `search` / `brief` / `source` over the 24-category, 242-skill registry. `brief` produces a capability brief plus a real dependency graph, critical path, parallel streams and stage plan for any category. `source` re-parses `asset-catalog-source.txt` and verifies it matches the registry. |
| `validate` | Schema + limit validation for `project`, `state`, `config`, `spec`, `registry`. |
| `state` | Long-project continuation state: `show`, `snapshot`, `reset`. |
| `recover` | Recovery behavior: preserve successful work, isolate the earliest bad state, restore the last safe checkpoint, revalidate the file inventory and rerun only affected checks. |
| `cleanup` | Removes generated `.bak` / `.tmp` working files (approval required). |
| `capabilities`, `help`, `version` | Built-in help, capability-list command and version. |

---

## 2. Setup and dependencies

**Runtime:** Python 3.9+ (developed and tested on Python 3.13). **Standard library only** — `argparse`, `json`, `re`, `pathlib`, `importlib`, `tempfile`, `os`, `time`, `datetime`, `html`, `subprocess` (only in `tools-selftest.py`), `copy`, `math`, `sys`, `shutil`, `collections`.

**No third-party packages, no install step, no build step, no network access, no credentials, no environment variables.** There is nothing to `pip install`. `pypdf` was used once at build time to extract the source PDF into `asset-catalog-source.txt` / `asset-skill-catalog.json`; it is **not** a runtime dependency and is not shipped.

```bash
bash -lc 'cd <future-skill-root> && python3 --version'
bash -lc 'cd <future-skill-root> && python3 orchestration-planning-and-work-splitting/tools-work-splitter.py capabilities'
bash -lc 'cd <future-skill-root> && python3 orchestration-planning-and-work-splitting/tools-selftest.py'
```

Filenames contain hyphens, so the CLI loads sibling modules through `importlib.util.spec_from_file_location` (see `_load_sibling` at the top of `orchestration-planning-and-work-splitting/tools-work-splitter.py`). `sys.dont_write_bytecode = True` keeps the folder free of generated `__pycache__` subfolders.

### Loading / execution order

1. `orchestration-planning-and-work-splitting/tools-work-splitter.py` — CLI, dispatch, envelope rendering, approval checks.
2. `orchestration-planning-and-work-splitting/tools-workflow-engine.py` — loaded first as `tools_workflow_engine`; state, validation, scoring, planning, gates, assembly, recovery, limits.
3. `orchestration-planning-and-work-splitting/tools-catalog.py` — loaded second as `tools_catalog`; intake parser and registry adapter; reuses the already-loaded engine.
4. `orchestration-planning-and-work-splitting/tools-default-config.json` — loaded by `--config` (default); points to the `asset-*.json` resources.
5. `orchestration-planning-and-work-splitting/tools-selftest.py` — independent validation runner, invokes step 1 as a subprocess.

---

## 3. Files, roles and references (12 files, no subfolders)

| File | Role |
|---|---|
| `tools-work-splitter.py` | **Primary runnable entry point.** All commands, help, capability list, structured output/errors, approval gate. |
| `tools-workflow-engine.py` | Shared engine: `Budget` (deadline/cancel/cleanup), `with_retries`, JSON Schema subset validator, `validate_project`, `file_inventory`, `decompose`, `topological_order`, `dependency_graph`, `critical_path`, `parallel_streams`, `score_request`, `estimate_counts`, `build_stages`, `tradeoffs`, `build_plan`, `checkpoint_receipt`, `classify_reply`, `gate_check`, `assemble`, `recover_state`, `StateStore`, atomic writes, `resolve_reference`. |
| `tools-catalog.py` | `strip_markup`, `normalize_lines`, `parse_source`, `load_registry`, `validate_registry`, `registry_categories`, `find_category`, `search_registry`, `project_from_spec`, `capability_brief`. |
| `tools-selftest.py` | End-to-end validation suite (127 assertions). |
| `tools-default-config.json` | Operational configuration: scoring weights, stage limits, gates, approval policy, storage, artifact policy, catalog/schema references, output settings. |
| `knowledge-readme.md` | This file — the only knowledge file. |
| `asset-skill-catalog.json` | Full registry: 24 categories, 242 skills, exact names/descriptions from the blueprint. Used at runtime by `capabilities`, `catalog`, `intake` and `brief`. |
| `asset-catalog-source.txt` | Cleaned plain-text body of the blueprint (pages 4–27, headers/page markers removed). Used by `catalog --action source` and as a real fixture for `intake`. |
| `asset-state-schema.json` | JSON Schema subset consumed by `validate --kind state` via `validate_schema`. |
| `asset-config-preset-strict.json` | Alternative config preset (stricter threshold, smaller stage budget, 15 s timeout, extra `review` gate evidence). Used with `--config` to prove configurability. |
| `asset-sample-project.json` | Valid project fixture: 6 requirements, 4 work packages with dependencies, 2 files, artifacts, scoring inputs. |
| `asset-gate-evidence.json` | Reusable gate/checkpoint evidence fixture (artifact, check, review, coverage; concrete and reproducible). |

Every internal reference uses the exact case-sensitive, forward-slash, skill-root-relative form `orchestration-planning-and-work-splitting/<file>`. `resolve_reference` rejects absolute paths, drive letters, URLs, backslashes and `..` in internal references, and resolves from the future skill root (the parent of the tool folder), from inside the folder, or from the current working directory. User-supplied `--input` / `--output` / `--state` / `--config` paths are runtime arguments and may be absolute.

---

## 4. Inputs, outputs, parameters and schemas

### Envelope (every command)

```json
{
  "schema_version": "1.0", "tool": "orchestration-planning-and-work-splitting",
  "version": "1.0.0", "command": "plan", "capability": "...", "status": "ok",
  "data": {}, "warnings": [], "errors": [],
  "budget": {"timeout_seconds": 30, "elapsed_seconds": 0.01,
             "remaining_seconds": 29.99, "cancel_file": null, "cleanup_on_exit": true}
}
```

`status` ∈ `ok | error | needs_approval | blocked | cancelled | dry_run | pass | repair | recovered`. Errors are `{code, message, field?, details?}`. Exit codes: `0` ok/dry-run/pass, `2` validation/structured error, `3` approval required, `4` gate/continue blocked, `5` timeout/cancellation, `1` unexpected. Error and approval envelopes are **always JSON**, even with `--out-format text`.

### Global options (accepted before or after the subcommand)

| Option | Default | Meaning |
|---|---|---|
| `--config` | `orchestration-planning-and-work-splitting/tools-default-config.json` | Config file or skill-root-relative reference. |
| `--state` | `./work-splitter-state.json` | Continuation state file (JSON). |
| `--output` | stdout | Write the rendered result to a file; overwriting needs `--approve`. |
| `--out-format` | `json` | `json` or `text` (human summary). |
| `--timeout` | `30` (config `limits.timeout_seconds`) | Wall-clock deadline in seconds; checked at every I/O and action. |
| `--retries` | `2` (config) | Bounded retries for transient file I/O, 0–10. |
| `--cancel-file` | none | Cooperative cancellation: if this file exists, the operation stops with `cancelled`. |
| `--approve` | off | Explicit approval for destructive/publishing/privileged actions. |
| `--dry-run` | off | Show what an approval-gated action would do, change nothing. |

### Project input (`--input` JSON, or `--text` inline)

```json
{
  "title": "string", "goal": "string (required, >= 3 chars)",
  "requirements": ["text", {"id": "R001", "text": "...", "critical": false}],
  "files": [{"name": "source-data.csv", "size_bytes": 20480, "present": true,
             "readable": true, "corrupt": false, "stale": false, "replaced": false,
             "intended_use": "analysis input"}],
  "work_packages": [{"id": "WP001", "name": "File intake", "depends_on": ["WP000"],
                     "duration": 1, "stream": "research", "requirement_ids": ["R001"],
                     "inputs": [], "outputs": ["inventory.json"],
                     "pass_conditions": ["every named file present"], "critical": false}],
  "artifacts": [{"name": "analysis.xlsx", "type": "workbook", "required": true}],
  "tool_calls": 9, "uncertainty": 0.35, "verification_burden": 6,
  "context_chars": 48000, "parallel_streams": 3, "source_format": "json"
}
```

Validation rejects: missing/invalid `goal`, wrong types, duplicate requirement ids, unknown dependency ids, unknown requirement references, cycles, unsafe file names (path traversal, separators), out-of-range numeric fields, > 200 requirements/packages, > 2 MB inputs. `--input-format` is `auto | json | text | markdown | html`; `auto` detects by extension. A spec file (unified `capabilities` object) is converted into a project automatically.

### State output (`--state`)

`asset-state-schema.json` defines `schema_version`, `project`, `plan`, `completed_stages`, `current_stage`, `checkpoints`, `history`, `blockers`, `next_actions`. The state also stores `assessment`, `artifacts`, `actions`, `decisions`, `open_issues`, `recovery` and a bounded 200-entry history. Writes are atomic (temp file in the destination directory + `os.replace`), with a `.bak` copy, bounded retries and cleanup in `finally`.

### Config sections

`scoring` (7 weights and divisors, `one_turn_threshold`, `hard_factor_ceiling`, `max_context_chars`), `stages` (`min`, `max`, `units_per_stage`, `gates_per_stage`, `files_per_group`, `max_units_per_stage`, `buffer_factor`, `critical_slack_threshold`, `merge_contract`), `limits`, `approval`, `continuation` (phrases, scope-change words, pause instruction), `gates.phases`, `storage`, `artifacts` (allowed extensions, forbidden name patterns), `catalog` (registry/source paths), `schemas` (state schema path), `output`. Providers/models/prompts/policies/paths/storage are all configurable here; **no provider or model is hard-coded**.

---

## 5. Examples for every capability

All examples are bash; run from the future skill root. `F=orchestration-planning-and-work-splitting` is used only as a readability shorthand in this section — type the full folder name.

```bash
# help, capability list, version
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py help
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py capabilities

# 1/2: Powerful Work Splitter + Single-Prompt Feasibility Assessor
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py assess \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json
# -> first_response.one_turn_feasible, reasons, recommended/minimum/maximum, major_stages,
#    fallback_staged_plan, file_contract, next_action=choose_stage_count

# 1 (flagship worked example: recommend 8, user chooses 5, expands to 10)
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py worked-example

# 3: Adaptive Prompt Count Estimator
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py estimate \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json

# 8/9/10: decomposer, parallel streams, critical path; 4: user-chosen stage count
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py plan \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json \
  --input-format json --stages 5 --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py negotiate --stages 3 --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py expand  --stages 7 --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py compress --stages 4 --state ./state.json

# 11: Milestone Gatekeeper (concrete, reproducible evidence)
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py gate \
  --stage 1 --state ./state.json \
  --evidence orchestration-planning-and-work-splitting/asset-gate-evidence.json

# 5: Checkpoint and Continue Controller
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py checkpoint \
  --stage 1 --state ./state.json \
  --evidence orchestration-planning-and-work-splitting/asset-gate-evidence.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py continue \
  --state ./state.json --reply "Continue"
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py continue \
  --state ./state.json --reply "Continue but add a stage and drop the summary"   # blocked

# 1: bounded declarative stage execution (no shell, no arbitrary code, no network)
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py run --stage 1 --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py run --stage 1 --state ./state.json \
  --apply --approve --output-dir ./out

# 12: Final Assembly Orchestrator (after all stages are run + checkpointed)
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py assemble --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py assemble --state ./state.json \
  --publish --approve

# Recovery, state, cleanup, validation
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py recover --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py state --action snapshot --state ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py cleanup --state ./state.json --approve
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py validate --kind state --input ./state.json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py validate --kind registry

# Input handling: plain text / Markdown / copied HTML / whole catalog / one category
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake --input spec.txt --input-format text
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake --input spec.md --input-format markdown
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake --input copied.html --input-format html
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake \
  --input orchestration-planning-and-work-splitting/asset-catalog-source.txt --input-format text --limit 3
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake \
  --input orchestration-planning-and-work-splitting/asset-catalog-source.txt --input-format text \
  --category "Orchestration, Planning, and Work Splitting" --limit 12
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py intake \
  --input spec.html --input-format html --name my-unified-tool   # explicit shared-purpose slug

# Several categories / other categories via the registry
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py catalog --action list
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py catalog --action category --name "Security, Privacy, and Safety"
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py catalog --action search --query recovery
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py catalog --action brief --name "Research, Search, and Evidence" --stages 6
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py catalog --action source

# Configurable policy, limits, cancellation, output path
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py assess \
  --config orchestration-planning-and-work-splitting/asset-config-preset-strict.json \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py plan \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json \
  --stages 5 --state ./state.json --timeout 15 --retries 3 --cancel-file ./stop.flag
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py help \
  --out-format text --output ./help.txt --approve
```

**Typical full run (4 stages):**

```bash
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py plan \
  --input orchestration-planning-and-work-splitting/asset-sample-project.json \
  --input-format json --stages 4 --state ./state.json
for n in 1 2 3 4; do
  python3 orchestration-planning-and-work-splitting/tools-work-splitter.py run --stage "$n" \
    --state ./state.json --apply --approve --output-dir ./out
  python3 orchestration-planning-and-work-splitting/tools-work-splitter.py checkpoint --stage "$n" \
    --state ./state.json --evidence orchestration-planning-and-work-splitting/asset-gate-evidence.json
  python3 orchestration-planning-and-work-splitting/tools-work-splitter.py continue \
    --state ./state.json --reply Continue
done
python3 orchestration-planning-and-work-splitting/tools-work-splitter.py assemble \
  --state ./state.json --publish --approve
```

---

## 6. Safety, approval and boundaries

- **Approval required** (`--approve`, else status `needs_approval` / exit 3, or `--dry-run`): `run --apply` (writes artifacts + mutates state), `assemble --publish` (publishes a final package), `state --action reset` (deletes continuation state), `cleanup` (deletes `.bak`/`.tmp` files), and overwriting an existing `--output` file. Nothing is executed or written before approval.
- **No unsupported action classes:** the tool performs no financial, credential, network, shell, publishing-to-service or arbitrary-code execution. `run` executes only three declarative, validated action types (`check`, `artifact`, `note`); unknown types are rejected. No `eval`, no `subprocess` in the runtime, no `os.system`, no sockets. `subprocess` appears only in `tools-selftest.py`, which invokes this CLI with fixed argv and a 25 s timeout.
- **Read-only by default:** `assess`, `estimate`, `gate`, `continue`, `intake`, `catalog`, `validate`, `worked-example`, `capabilities`, `help`, `version` never write external files (they may write `--output` and, for `gate`/`checkpoint`, the state file).
- **File contract:** before any file-dependent stage, the file inventory is checked. Absent, unreadable, corrupt, stale or replaced files produce a precise `reupload_request` and block `assess` / `run` rather than fabricating content.
- **Path safety:** internal references are skill-root-relative only; absolute paths, drive letters, URLs, backslashes and `..` are rejected. Embedded file and artifact names must be portable (`[A-Za-z0-9._-]`, no separators). Artifact extensions are allow-listed; temporary/`.bak`/`.log`/dotfile names are rejected at assembly.
- **Limits:** 2 MB input, 5 MB generated output, 200 requirements, 200 work packages, 24 stages, 500 evidence items, 200 actions per stage, 30 s default timeout, 2 retries with exponential backoff capped at 0.5 s. Grounds: `max_actions`, `max_evidence_items`, `max_stages`, `max_input_bytes`.
- **Cancellation / cleanup / recovery:** `Budget.check()` is called at every retryable I/O and every action; a `--cancel-file` or the deadline raises `Cancelled` (exit 5); temp files are tracked and removed in `finally`; `recover` restores the last safe checkpoint, isolates the earliest bad state, revalidates the file inventory and reruns only affected exit criteria without repeating an unchanged failed action.
- **Continue safety:** replies that mix Continue with scope-change words (`add/remove/expand/compress/change/drop/skip/replace/instead`) or that are questions/ambiguous are rejected with the preserved state, per the flagship anti-patterns.

---

## 7. Validation and tests actually completed

`orchestration-planning-and-work-splitting/tools-selftest.py` runs the **real CLI through subprocess**, parses every JSON envelope, and covers: help, capability list (12, all implemented), version; assess/estimate/plan outputs, requirement ownership, state file creation; intake on plain-text single capability, Markdown category+numbered list, copied HTML with `<style>`/`class`/`data-*`, the full 24/242 source and a one-category filter; catalog list/brief/search/source consistency; gate pass and blocked evidence; checkpoint receipt; recognized Continue and rejected scope-changing reply; negotiate 5→3, expand 3→7, compress 7→4 with no lost requirements; run dry-run, approval refusal, approved artifact writes and reopening written artifacts; a complete 4-stage loop; assembly pass with 100 % requirement coverage; publish approval refusal and approval; recovery; worked-example 8→5→10; `validate` for project/state/config/registry/spec; ten structured error cases (missing input, malformed JSON, invalid project, dependency cycle, missing state, stage count out of range, unknown category, missing file, …); cancel file, timeout, strict-config output change, changed-input output change; `--output` text rendering and overwrite approval; cleanup approval; a clean temporary skill root containing exactly one test `SKILL.md` beside the copied folder with relative-path resolution; and package structure assertions.

**Result of the final run:** `127 passed, 0 failed` (exit 0). Also verified manually: `python3 -m py_compile` on all three runtime modules, direct inspection of every JSON output, parsing of written artifact files, `catalog --action source` consistency (`24/242` from both text and registry), and help/capability-list commands from the extracted ZIP in a clean directory.

Clean-state compatibility: the tool folder was copied beside a single `SKILL.md` in a temporary root; `python3 orchestration-planning-and-work-splitting/tools-work-splitter.py capabilities` and `catalog --action source` both succeeded using only relative paths. The combined package has **12 files, depth 2** (`<folder>/<file>`), well under the 500-file limit, with exactly one `SKILL.md` (the parent's) and one `knowledge-readme.md`.

---

## 8. Limits and unavailable integrations

- **No model / LLM provider.** This is a deterministic planning and state engine. It does not call any model, API, search service, catalog service, tenant endpoint or hosted resource. "Providers, models, prompts and policies" are represented only as configurable config sections; there is no live provider adapter. If a hosted resource is unreachable in a future parent skill, that is a hosting/catalog/permission issue, not something this folder can prevent.
- **PDF/DOCX binaries are not parsed at runtime.** `intake` handles plain text, Markdown and copied HTML. The source PDF was converted into `asset-catalog-source.txt` and `asset-skill-catalog.json` at build time; those assets are what the tool reads. No PDF library is bundled or required.
- **No media/voice/document generation**, no spreadsheets, no speech, no browser automation — those belong to other catalog categories, which this tool can *plan and brief* but does not execute.
- **Heuristic intake.** `intake` recognises categories by known registry names, `Category:` / `Section:` / `Group:` prefixes, markdown `#`/`##` headings, and `| N skills` hints; for multi-category sources without a known title it derives a short slug from shared-purpose words. Use `--name` for a deterministic custom slug and `--category` to select one category. Lines that cannot be attached to a capability are reported in `warnings` rather than silently used.
- **Deterministic scoring.** Stage counts and scores come from configurable weights and divisors, not from a learned model. They are reproducible and auditable but should be tuned with `--config` for a specific parent skill.
- **Single-user, local state.** State is a local JSON file with atomic writes and `.bak` copies; there is no locking for concurrent writers, no encryption, and no durable remote storage.
- No timestamps, placeholders, secrets, machine-specific paths, nested archives or generated caches are shipped.

---

## 9. Troubleshooting, recovery, extension, maintenance

| Symptom | Cause / fix |
|---|---|
| `ERR_REF_MISSING` for an asset | Run from the future skill root, or keep the folder name exactly `orchestration-planning-and-work-splitting`. `resolve_reference` lists the tried relative paths in the error details. |
| `ModuleNotFoundError` | Do not rename the `tools-*.py` files; the `importlib` sibling loader uses their exact hyphenated names. |
| `ERR_INPUT_MISSING` | Pass `--input <file>` or `--text "..."`. Use `--input-format json` for a project JSON; text/md/html for a capability source. |
| `ERR_PROJECT_INVALID` | Read `errors[0].details.defects`; fix the listed goal/requirement/dependency/numeric problems. |
| `ERR_DEPENDENCY_CYCLE` | Remove the cyclic `depends_on` entries listed in `details.units`. |
| `ERR_FILE_CONTRACT` | Re-upload or correct the named files; `assess` and `run` will not fabricate their content. |
| `ERR_STATE_MISSING` | Run `plan` (or `assess --stages N`) first to create `--state`. |
| Gate `repair`/`blocked` | Evidence must include all `required_evidence_types` for the phase, a non-empty `value`, `reproducible: true`, and `status: pass`. Use `asset-gate-evidence.json` as a template. |
| `continue` blocked | Reply with a pure Continue phrase; use `negotiate`/`expand`/`compress` for scope changes. |
| `needs_approval` (exit 3) | Re-run with `--approve` after reviewing, or use `--dry-run` to inspect the action first. |
| `cancelled` (exit 5) | Remove the `--cancel-file` marker or raise `--timeout`; successful work is preserved in state. |
| Stage fails mid-run | `recover --state <file>` restores the last safe checkpoint, revalidates files and reruns only affected checks; then `continue`. |
| Assembly defects | Read `data.defects`; fix missing requirement evidence, broken references, naming conflicts, empty/forbidden artifacts, or an absent final assembly stage. |
| Output looks wrong after config change | `validate --kind config`; check `scoring` weights, `stages.units_per_stage`, and `limits`. The strict preset demonstrates the difference. |

**Recovery procedure:** `state --action snapshot` → `recover` → `gate` on the affected stage → `checkpoint` → `continue`. Completed stages are never lost; `history` records every command, and `checkpoints` hold deep snapshots of the plan, completed stages, artifacts and blockers.

**Extension:** add a new command in `tools-work-splitter.py` (dispatch table `HANDLERS` + `SUPPORTING_COMMANDS`), put shared logic in `tools-workflow-engine.py`, and add new capability sources to the config's `catalog.registry_path` or a new `asset-*.json` registry. Add checks to `tools-selftest.py` for any new command. Update this single knowledge file; do not add a second `knowledge-*` file or any internal subfolder. Keep every new reference skill-root-relative and case-exact.

**Maintenance:** `asset-skill-catalog.json` and `asset-catalog-source.txt` must stay consistent — `catalog --action source` verifies `24` categories and `242` capabilities and reports `consistent: true`. To refresh from a new blueprint, replace both assets together, keep `category_count`/`skill_count` accurate, and rerun `validate --kind registry` and `tools-selftest.py`. Bump `VERSION` in `tools-work-splitter.py` and `schema_version` in the config/state schema only when the envelope or state shape changes. Avoid adding dependencies; if one becomes necessary, prefix its declaration with `tools-` and document install commands and supported versions here.

**Delivery:** the completed folder is placed directly at the root of one descriptively named ZIP (`orchestration-planning-and-work-splitting-skill-tool.zip`). The ZIP contains no wrapper folder, no nested archives, and no `SKILL.md`; extracting it produces this folder immediately.
