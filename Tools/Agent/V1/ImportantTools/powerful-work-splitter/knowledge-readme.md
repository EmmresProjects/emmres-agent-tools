# powerful-work-splitter

A single reusable Skill Tool component for **Category 1 — Orchestration, Planning, and Work Splitting**
of the *Ultimate Agent Skill Catalog* (242 skills across 24 categories). It implements all twelve
capabilities of that category as one unified, runnable, standard-library Python tool.

This folder is a **Skill Tool**, not a complete Copilot skill. It contains no `SKILL.md` and no
subfolders: drop it beside a parent package's root `SKILL.md` and the parent skill owns the
instructions while this tool owns the mechanics.

- Category (umbrella name): *Orchestration, Planning, and Work Splitting*
- Skill Tool folder: `powerful-work-splitter/`
- Primary entry point: `powerful-work-splitter/tools-work-splitter.py`
- Language: Python 3.9+ (standard library only, no third-party packages, no network access)

---

## 1. What problem it solves

An agent that guesses "I can do this in one reply" over-commits, drops requirements, fabricates
missing file content, and claims completion it never verified. This Skill Tool replaces that guess
with a measured decision and a resumable contract:

1. **Measure** the request (requirements, files, dependency depth, tool calls, uncertainty,
   verification burden) against a one-response budget.
2. **Recommend** a stage count, and let the user accept, raise, or lower it without losing work.
3. **Record** the plan, the requirement ledger and the file contract as inspectable JSON.
4. **Checkpoint** every stage, pause at safe boundaries, and only advance on recognized
   continuation intent.
5. **Gate** each milestone with explicit entry/exit criteria and evidence.
6. **Verify** coverage, gates, files, artifacts and delivery hygiene before any completion claim.

Everything the tool decides is deterministic, explainable ("why this count"), and re-runnable.

---

## 2. Files in this Skill Tool

| File | Prefix role | Purpose |
| --- | --- | --- |
| `powerful-work-splitter/tools-work-splitter.py` | **primary entry point** | CLI: argument parsing, command wiring, checkpoint controller, recovery command, demo. Run this. |
| `powerful-work-splitter/tools-work-splitter-core.py` | shared domain module | Normalization, requirement/file extraction, feasibility scoring, stage-count estimation, stage construction, rebalancing, decomposition, critical path, gate definitions, plan validation, ledger export, text rendering, state store and locking. Loaded first by the entry point. |
| `powerful-work-splitter/tools-skill-validator.py` | verification module | Artifact assertions (file/CSV/JSON/JSON-Schema subset/manifest/references), the five verification gate groups, workspace watch, approval-gated cleanup, bounded repair bookkeeping. Also runs standalone as a debugging CLI. |
| `powerful-work-splitter/tools-selftest.py` | regression suite | 207 structural and behavioural checks executed through the real CLI in a temporary workspace. |
| `powerful-work-splitter/knowledge-readme.md` | documentation | This file: the only documentation file. |
| `powerful-work-splitter/asset-default-policy.json` | policy preset | Every threshold and bound: stage limits, one-response budget, metric weights, per-stage capacity, file limits, repair limits, lock timings, approval actions. |
| `powerful-work-splitter/asset-continue-intents.json` | lexicon | Continue / stop / scope-change / ambiguous reply patterns used to classify a user's resume instruction. |
| `powerful-work-splitter/asset-gate-criteria.json` | policy | Five milestone gates with required and optional entry/exit criteria and stable criterion ids. |
| `powerful-work-splitter/asset-plan-schema.json` | schema | JSON Schema (2020-12 subset) for a plan document; used by `plan` self-check, `validate-plan --schema` and `assert --kind json --schema`. |
| `powerful-work-splitter/asset-plan-template.json` | template | A complete, valid five-stage worked example used as a fixture and starting point. |
| `powerful-work-splitter/asset-example-request.md` | fixture | Deliberately messy request (HTML tags, CSS classes, `data-` attributes, nested numbering) that exercises normalization; used by the demo and tests. |
| `powerful-work-splitter/asset-requirement-ledger-template.csv` | template | Editable requirement ledger with `id,text,kind,owner_stage,acceptance,status,evidence`. |

**Load / execution order:** `tools-work-splitter.py` (entry point) loads `tools-work-splitter-core.py`
first, then `tools-skill-validator.py`; the validator detects the already-loaded core module and reuses it,
so `ToolError`, exit codes and policy handling are one shared implementation. Assets are read on demand by
name and resolved relative to the folder that contains the tools, so running the commands from any working
directory is safe.

**File count:** 12 files (4 `tools-*`, 1 `knowledge-*`, 7 `asset-*`). No subfolders, no archives inside.

---

## 3. Setup

No installation, no build step, no dependency file.

```bash
python3 --version          # 3.9 or newer
cd <future-skill-root>
python3 powerful-work-splitter/tools-work-splitter.py capabilities
```

Optional check before first use:

```bash
python3 powerful-work-splitter/tools-selftest.py
```

The tool sets `sys.dont_write_bytecode = True`, so running it never creates `__pycache__` inside the
Skill Tool folder. It reads and writes only the paths you pass and the packaged `asset-*` files.

**Dependencies:** CPython standard library only (`argparse`, `csv`, `json`, `re`, `pathlib`, `zipfile`,
`tempfile`, `hashlib`-free by design, `importlib`, `datetime`, `math`). No network use, no shell
execution, no environment variables, no credentials.

---

## 4. Capabilities

Twelve capabilities, one folder. `capabilities` prints this list; `--format json` gives the machine index.

| # | Capability | `id` | Commands |
| --- | --- | --- | --- |
| 1 | Powerful Work Splitter | `powerful-work-splitter` | `plan`, `assess`, `estimate`, `negotiate` |
| 2 | Single-Prompt Feasibility Assessor | `single-prompt-feasibility-assessor` | `assess` |
| 3 | Adaptive Prompt Count Estimator | `adaptive-prompt-count-estimator` | `estimate` |
| 4 | User-Controlled Stage Negotiator | `user-controlled-stage-negotiator` | `negotiate`, `checkpoint set-count` |
| 5 | Checkpoint and Continue Controller | `checkpoint-and-continue-controller` | `checkpoint start/status/continue/reopen/block` |
| 6 | Dynamic Stage Expansion Manager | `dynamic-stage-expansion-manager` | `negotiate --direction expand`, `checkpoint set-count` (increase) |
| 7 | Dynamic Stage Compression Manager | `dynamic-stage-compression-manager` | `negotiate --direction compress`, `checkpoint set-count` (decrease) |
| 8 | Dependency-Aware Task Decomposer | `dependency-aware-task-decomposer` | `decompose` |
| 9 | Parallel Workstream Coordinator | `parallel-workstream-coordinator` | `decompose --parallel` |
| 10 | Critical Path Scheduler | `critical-path-scheduler` | `scheduler` |
| 11 | Milestone Gatekeeper | `milestone-gatekeeper` | `gate` |
| 12 | Final Assembly Orchestrator | `final-assembly-orchestrator` | `verify`, `ledger`, final-stage `checkpoint continue` |

Supporting commands that exist because the twelve capabilities need them: `files` (file contract),
`assert` (artifact assertions used by assembly checks), `watch` (settle detection before reopening
outputs), `cleanup` (approval-gated temporary-material removal), `recover` (bounded repair loop),
`validate-plan`, `capabilities`, `help`, `demo`.

---

## 5. Commands

Run every command from the future skill root using the prefixed entry point. `bash`/`zsh` shown; the same
commands work in PowerShell.

```bash
python3 powerful-work-splitter/tools-work-splitter.py <command> [options]
```

Global option on every command: `--format text|json`, `--out <path>` (also write the rendered result),
`--policy <override.json>` (merged over `asset-default-policy.json`).

| Command | What it returns |
| --- | --- |
| `help` | Full help plus the command list |
| `capabilities` | Twelve capabilities, their commands, and a machine-readable index |
| `plan` | Validated stage plan JSON (requirements, stages, gates, file contract, assumptions, risks, warnings) |
| `assess` | Single-response vs staged decision, reasons, metrics, fallback staged plan |
| `estimate` | Recommended / minimum / maximum stage count, confidence, reasons |
| `negotiate` | Rebalanced plan plus disclosed trade-offs |
| `decompose` | Work packages, dependency edges, execution waves, merge contracts |
| `scheduler` | Critical path, stage durations, buffer, committed total, escalation triggers |
| `gate` | Gate catalogue, or pass / repair / blocked for one gate against supplied evidence |
| `validate-plan` | Structural and (optionally) schema validation result |
| `ledger` | Requirement ledger as CSV or Markdown |
| `files` | Inventory with present / missing / duplicate / unreadable status and next actions |
| `checkpoint` | `start`, `status`, `continue`, `block`, `reopen`, `set-count` |
| `recover` | Bounded repair attempt record plus repaired stage pointer |
| `verify` | Five gate groups: coverage, gates, files, artifacts, delivery |
| `assert` | `file`, `csv`, `json`, `manifest`, `reference` assertions |
| `watch` | Settle / timeout report for a workspace directory |
| `cleanup` | Dry-run proposal, or applied cleanup with the `cleanup` approval token |
| `demo` | Self-contained end-to-end rehearsal in a temporary workspace |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Usage, validation, ambiguous reply, or invalid count |
| `3` | Missing input file, state, or packaged resource |
| `4` | Gate/blocked condition: unverified output, failed gate, blocked stage, compression blocked |
| `5` | Approval required or approval token invalid |
| `6` | Configured limit exceeded (file count, lock timeout, input size) |
| `7` | Partial: work paused, repair attempts exhausted, or verification needs repair |
| `130` | Interrupted by the operator (saved state is preserved) |

Errors are always structured JSON on stderr:
`{"ok": false, "code": "...", "message": "...", "exit_code": N, "hint": "...", "details": {...}}`.

---

## 6. Capability detail and examples

### 6.1 Plan from a pasted request (capabilities 1, 2, 3)

```bash
python3 powerful-work-splitter/tools-work-splitter.py plan \
  --request powerful-work-splitter/asset-example-request.md \
  --count 4 --save-plan plan.json
```

Acceptance rules: Markdown, plain text, copied HTML, or stdin (`--request -`). Tags, CSS attributes,
HTML comments, markdown emphasis, list marks and repeated spacing are stripped before extraction; the
real requirement texts and file names survive. Extraction priority: explicit `--requirement` values →
`Deliverables` / `Constraints` / `Goal` sections → numbered Skill Name / Description pairs → cue-bearing
sentences from free text.

Output sections: goal, single-response feasibility and work units, recommended and chosen stage count,
stage table with entry/exit gates, "why this count" reasons, capabilities engaged, file contract,
assumptions, risks, warnings, next action.

The `plan` command validates the generated document with `asset-plan-schema.json` and with the internal
structural validator before printing it, so a plan that prints is a plan that loads.

### 6.2 Feasibility decision (capability 2)

```bash
python3 powerful-work-splitter/tools-work-splitter.py assess --request-text "Write one short paragraph."
```

Returns `decision: single_response` when the measured work fits the one-response budget, has at most four
deliverables and uncertainty ≤ 0.30; otherwise `decision: staged` with a fallback staged plan whose stages
are named immediately, so the caller never has to write its own decomposition.

### 6.3 Stage-count estimate (capability 3)

```bash
python3 powerful-work-splitter/tools-work-splitter.py estimate --request request.md \
  --tool-calls 10 --uncertainty 0.4 --verification 0.7
```

```text
units = w_scope·scope + w_files·(2·file_units) + w_depth·depth + w_calls·calls
      + w_uncertainty·uncertainty + w_verification·verification
```

Defaults come from `asset-default-policy.json` (`metric_weights`, `metric_caps`, `unit_defaults`). The
count is the maximum of the unit-based requirement and the requirement-count requirement:

```text
if single_response_feasible: recommended = minimum = stage_bounds.min  (one response, no invented stages)
unit_based        = 1                                                     if feasible
                  = ceil((units − one_response_budget) / (capacity − overhead)) + 1   otherwise
requirement_based = ceil(requirements / requirements_per_stage) + 1        (the +1 is assembly)
recommended       = clamp(max(unit_based, requirement_based), min, max)
minimum           = clamp(ceil(units / capacity), min, recommended)
maximum           = clamp(recommended + headroom, recommended, max)
```

The tool reports `recommended`, `minimum`, `maximum`, `confidence` and a reason list; it never inflates the
count silently. A request that fits the one-response budget returns exactly one stage — titled from the
policy's `single_stage` blueprint ("Execute and deliver in one response") — because the catalog's first
rule is to proceed in one response when that is safe.

### 6.4 Accept, raise or lower the count (capabilities 4, 6, 7)

```bash
python3 powerful-work-splitter/tools-work-splitter.py negotiate --plan plan.json --count 9 --save-plan plan9.json
python3 powerful-work-splitter/tools-work-splitter.py negotiate --plan plan9.json --count 4 --direction compress
```

- Every requirement keeps exactly one owner stage; ownership is relinked on every merge or split.
- Compression merges only adjacent build stages, refuses to cross the discovery/assembly boundary, and
  refuses to drop a protected gate — it fails with exit `4` and a concrete hint instead of degrading
  quality silently. Trade-offs are printed (`Merged S7 into S6 …`, `Fewer checkpoints …`).
- Expansion splits requirement-bearing build stages, then adds gate-only stages from the configured
  blueprints; the last build stage keeps the `verification` gate.
- `--direction auto|expand|compress|hold`; mismatched direction (e.g. `expand` to a smaller count) fails
  with exit `2` instead of guessing.

### 6.5 Checkpoint and continue (capability 5)

```bash
python3 powerful-work-splitter/tools-work-splitter.py checkpoint start --plan plan.json --state state.json --root .
python3 powerful-work-splitter/tools-work-splitter.py checkpoint status --state state.json
python3 powerful-work-splitter/tools-work-splitter.py checkpoint continue --state state.json \
  --reply "continue" --verified "figures reconciled against Q2 workbook" --output figures.csv --root .
```

Behaviour:

- `checkpoint continue` classifies the reply with `asset-continue-intents.json` in the order
  hard-stop → scope-change → continue → ambiguous. `continue`, `proceed`, `go on`, `next`, `resume`,
  `stage 3` and similar advance; `stop`/`cancel`/`pause` preserve state and return `7`; anything that
  changes scope (`actually use 6 stages`) returns `2` with the exact follow-up command; `ok`, `yes`,
  `maybe`, `why` return `2` and never advance.
- A stage cannot be closed without at least one `--verified` note (exit `2`), and declared `--output`
  files must exist under `--root` when that flag is given (exit `4`). `--force` records the gap instead of
  hiding it, and the gap is echoed as a warning.
- `started_at`, `completed_at`, `verified`, `outputs`, `blockers`, `attempts`, `notes` and a full
  `history` trail are stored per stage, so a later process can resume from the file alone.
- `state.json` is written atomically under a lock file with a 10 s acquisition timeout and a 60 s stale
  lock takeover; `checkpoint start` refuses to overwrite an existing state without `--force`.
- When the final stage closes, the receipt says to run `verify`; it never claims delivery by itself.

### 6.6 Change the stage count mid-project (capabilities 4, 6, 7)

```bash
python3 powerful-work-splitter/tools-work-splitter.py checkpoint set-count --state state.json --count 8
```

Stages 1..k that are already open or complete stay byte-for-byte identical (outputs, verification notes,
attempts). Only the remaining requirements are replanned into the tail, the tail resumes with build work
(it never repeats discovery), ids are renumbered, requirement ownership is remapped, and the whole plan is
revalidated before the state file is touched. Reducing below the opened stage count fails with exit `4`
and a minimum count hint.

### 6.7 Decompose and schedule (capabilities 8, 9, 10)

```bash
python3 powerful-work-splitter/tools-work-splitter.py decompose --plan plan.json --parallel
python3 powerful-work-splitter/tools-work-splitter.py scheduler --plan plan.json
```

- Requirements inside a stage are parallel unless the text declares a dependency (`after`, `then`,
  `based on`, `depends on`, `once`, `following the`); every stage boundary is a handoff edge from the
  closing stage to each root work package of the next stage. Waves are the topological levels.
- `--parallel` adds one merge contract per stage: merge key, required fields
  (`id, path, status, evidence`), version, and the requirement ids it covers — the check that parallel
  outputs use compatible names and schemas.
- `scheduler` weights each stage by requirement units plus stage overhead, walks the dependency graph,
  and reports the critical path (including gate-only stages that still delay delivery), the uncertainty
  buffer, the committed total, and per-stage escalation triggers at 50 % over estimate.

### 6.8 Milestone gates (capability 11)

```bash
python3 powerful-work-splitter/tools-work-splitter.py gate --list
python3 powerful-work-splitter/tools-work-splitter.py gate --gate discovery --evidence evidence.json --require
```

Gates in `asset-gate-criteria.json`: `file-inventory`, `discovery`, `build`, `verification`,
`final-assembly`. Evidence is `{"<criterion id>": {"passed": true, "evidence": "..."}}`. A required
criterion that fails *or* carries no evidence text blocks the gate (exit `4` with `--require`). Unknown
evidence keys are reported, not silently accepted. Gates scale with the plan shape: 1 stage needs
`final-assembly`, 2+ stages add `file-inventory`, 3+ stages require `verification`.

### 6.9 Final assembly and verification (capability 12)

```bash
python3 powerful-work-splitter/tools-work-splitter.py verify --plan plan.json --state state.json --root . --format json
python3 powerful-work-splitter/tools-work-splitter.py ledger --plan plan.json --out requirement-ledger.csv
```

`verify` runs five groups and returns `pass` / `repair` / `fail` / `blocked`:

| Group | Fails or repairs when |
| --- | --- |
| `coverage` | a requirement has no owner stage, no acceptance check, or a state stage references an unknown requirement |
| `gates` | a stage closed without verification, stages closed out of order, the final stage closed while others are open, or any stage is still open |
| `files` | a required file in the contract is absent, or a file marked present is gone |
| `artifacts` | a stage declares an output that does not exist under the root (state outputs are included) |
| `delivery` | temporary files exist, paths exceed depth 6, or the package exceeds 500 files |

`ledger` exports the traceable requirement-to-output matrix: `id,text,kind,owner_stage,acceptance,status,evidence`.

### 6.10 File contract, assertions, watch, cleanup, recovery

```bash
python3 powerful-work-splitter/tools-work-splitter.py files --root . --declared report.xlsx --required report.xlsx
python3 powerful-work-splitter/tools-work-splitter.py assert --kind file --path out.pdf --signature
python3 powerful-work-splitter/tools-work-splitter.py assert --kind csv --path ledger.csv --require-header id --expect-rows 12
python3 powerful-work-splitter/tools-work-splitter.py assert --kind json --path plan.json --schema powerful-work-splitter/asset-plan-schema.json
python3 powerful-work-splitter/tools-work-splitter.py assert --kind reference --path notes.md --root .
python3 powerful-work-splitter/tools-work-splitter.py assert --kind manifest --path .
python3 powerful-work-splitter/tools-work-splitter.py watch --root . --settle 2 --timeout 60
python3 powerful-work-splitter/tools-work-splitter.py cleanup --root . --approve cleanup --backup .trash
python3 powerful-work-splitter/tools-work-splitter.py recover --state state.json --stage S3 --cause "render crashed" --action reopen
```

- `files` never assumes absent content: missing declared files are named, required ones are re-upload
  requests, duplicates are flagged for a canonical choice.
- `assert --kind file` verifies existence, byte floor, UTF-8 for text, and magic-byte signature for pdf,
  png, jpg, gif, zip/OOXML (docx/xlsx/pptx/odt/ods/epub), wav, mp3, mp4/mov, webp, ogg, flac, sqlite.
  Unknown extensions fail the signature check instead of passing quietly.
- `assert --kind reference` resolves Markdown links, backticked paths and bare relative paths against
  `--root`, ignoring `http(s)`, `mailto`, anchors and templated values, and fails on unresolved or
  root-escaping references.
- `watch` fingerprints the tree and returns `settled` only after `--settle` seconds without change, or
  `timeout` (exit `7`) — bounded, cancellable, no daemon.
- `recover` supports `retry`, `reopen`, `rollback`, increments per-stage attempts, applies the configured
  backoff, preserves verified earlier stages, and escalates with exit `7` after the third attempt instead
  of repeating an unchanged failed action.

---

## 7. Configuration

### 7.1 Policy (`asset-default-policy.json` + override)

Copy the default and pass `--policy my-policy.json`; the override is deep-merged over the packaged file,
so it may contain only the keys you change.

```json
{
  "stage_bounds": { "min": 1, "max": 14 },
  "budget": {
    "single_response_budget_units": 12.0,
    "stage_capacity_units": 8.0,
    "recommended_headroom_stages": 3,
    "requirements_per_stage": 4
  },
  "metric_weights": { "scope": 1.0, "files": 1.5, "dependency_depth": 1.0,
                      "tool_calls": 0.6, "uncertainty": 8.0, "verification": 10.0 },
  "unit_defaults": { "units_per_requirement": 1.0, "units_per_file": 0.5,
                     "uncertainty_default": 0.15, "verification_default": 0.5,
                     "stage_overhead_units": 1.5 },
  "single_stage": { "title": "Execute and deliver in one response", "objective": "...", "role": "assembly" },
  "protected_checks": ["file-inventory", "verification", "final-assembly"],
  "approval_required_actions": ["cleanup", "overwrite", "publish", "credential",
                                "financial", "destructive-delete"],
  "limits": { "max_requirements": 200, "max_stages": 14, "max_artifacts": 500,
              "max_evidence_entries": 500, "max_text_chars": 400000,
              "operation_timeout_seconds": 120 },
  "files": { "max_files": 500, "max_depth": 6, "max_hash_bytes": 268435456,
             "ignore_globs": ["*.log", "*.tmp", "*.pyc", ".git", "node_modules",
                              "__pycache__", ".DS_Store"] },
  "recovery": { "max_repair_attempts": 3, "backoff_seconds": [0.5, 1.0, 2.0],
                "preserve_completed_stages": true },
  "checkpoint": { "schema_version": 3, "lock_timeout_seconds": 10,
                  "lock_stale_seconds": 60, "lock_retry_seconds": 0.05 },
  "output": { "default_format": "text", "max_preview_items": 25 }
}
```

Validated on load: `1 ≤ stage_bounds.min ≤ stage_bounds.max ≤ 14`, and
`stage_capacity_units > stage_overhead_units`. An invalid policy fails immediately with
`policy-invalid` (exit `2`) rather than producing a nonsense plan.

**Lower the budget** (e.g. for a small-context model): set `single_response_budget_units` to `4`. A short
memo that is feasible in one response under the default budget then flips to "staged work required". **Flatten** verification cost: set `verification_default` to `0.2` (lower units, same recommendation shape).
**Cap** stages: set `stage_bounds.max` to `8`. All three are behaviour changes, not code changes — verified
in the test suite for the `--uncertainty/--verification/--tool-calls` input change.

### 7.2 Continue lexicon (`asset-continue-intents.json` + `--intents`)

Four ordered lists of case-insensitive regular expressions: `hard_stop_patterns`, `scope_change_patterns`,
`continue_patterns`, `ambiguous_patterns`. Add local-language replies (for example `"^(sige|tuloy)$"`) with
an override file. Matching runs against the normalized whole reply, so `"ok, continue"` continues while
`"ok"` alone does not.

### 7.3 Gate criteria (`asset-gate-criteria.json` + `--gates`)

Each gate declares `phase`, `blocking`, `entry[]` and `exit[]` criteria with stable ids (`D1`, `V2`, …).
Overrides can add project-specific criteria; ids must stay unique inside a gate because evidence is keyed
by id.

### 7.4 Plan schema (`asset-plan-schema.json`)

JSON Schema 2020-12 subset supported by the built-in validator: `type`, `required`, `properties`, `items`,
`enum`, `pattern`, `minimum`, `maximum`, `minLength`, `minItems`, `maxItems`. Unknown keywords are ignored,
so a richer external schema still yields useful errors instead of crashing.

### 7.5 Data contracts

**Plan document** (`--save-plan`, `asset-plan-template.json`): `schema_version`, `goal`,
`single_response_feasible`, `metrics`, `recommendation{recommended,minimum,maximum,confidence,reasons}`,
`stage_count`, `stage_count_origin`, `user_selected_stage_count`, `stages[]`
(`id,title,role,objective,requirements,outputs,entry_gate,exit_gate,status,depends_on,effort_share,estimated_units`),
`requirements[]` (`id,text,kind,owner_stage,acceptance,status,evidence,source`), `files[]`
(`path,role,required,present,status`), `assumptions[]`, `scope`, `risks`, `capabilities`, `warnings`,
`created_at`, `updated_at`.

**Checkpoint state** (`--state`): `kind: "powerful-work-splitter"`, `schema_version`, `stage_count`,
`current_stage_index`, `completed_count`, `paused`, `next_action`, `plan`, `stages[]`
(`…, started_at, completed_at, verified[], blockers[], attempts, notes`), `files[]`, `history[]`.
State files with a different `schema_version` are rejected with `state-schema-mismatch` rather than
half-migrated.

---

## 8. Safety and approval rules

- **No network, no shell, no arbitrary code execution.** The tool never spawns processes and never
  downloads providers, models or prompts. Nothing here needs credentials.
- **Nothing destructive without an explicit token.** `cleanup` deletes nothing unless `--approve cleanup`
  is passed; the token must appear in `approval_required_actions`. Unapproved calls return exit `5` with
  the candidate list. Paths are confined to `--root`; a path escaping the root aborts with `cleanup-escape`.
  `--backup <dir>` moves files instead of deleting them.
- **Overwrite protection.** `checkpoint start` refuses to replace an existing state without `--force`;
  writes go through a temp file plus atomic replace, so an interrupted write cannot corrupt state.
- **No fabricated content.** Missing files are reported as missing with a re-upload request; a stage whose
  declared output is absent cannot close without `--force`, and forcing is echoed as a warning.
- **No unverified completion claims.** A stage needs a `--verified` record; `verify` fails or repairs until
  coverage, gates, files, artifacts and delivery hygiene all pass.
- **Bounded everything.** Stage count 1–14, requirements 200, files 500, package depth 6, text 400 000
  chars, watch timeout, 3 repair attempts with backoff, 10 s lock acquisition, `KeyboardInterrupt` → exit
  `130` with saved state preserved.
- **Human-owned decisions.** The tool recommends a stage count, it does not impose one; it pauses at every
  checkpoint; scope changes require a replan command rather than being absorbed silently.
- **Delivery actions are out of scope.** Publishing, sending, deploying, paying or changing credentials are
  not implemented at all; if a parent skill performs them, it must obtain its own approval first.

---

## 9. Limits and unavailable integrations

Implemented and verified locally:

- All planning, negotiation, decomposition, scheduling, gating, checkpoint, recovery and verification
  logic, with the packaged assets and the regression suite.
- Local file operations only: read, atomic write, inventory, magic-byte checks, reference resolution.

Deliberately **not** implemented, and not claimed:

- **No model/LLM calls, no web access, no search, no hosted services.** The tool measures and governs work;
  it does not generate content. Any research or generation is performed by the parent skill.
- **No autonomous scheduler or daemon.** `checkpoint` is explicitly driven; `watch` polls with a timeout.
- **No PDF/OOXML/DOCX rendering or editing.** The demo's fixture writer creates a minimal, genuine OOXML
  `.xlsx` for testing only; it is not a spreadsheet engine.
- **Concurrency is single-writer.** The lock file prevents two simultaneous writers on one state file; it
  is not a distributed lock and offers no multi-host guarantee. Stale locks older than 60 s are taken over.
- **No notification, ticketing, or messaging integrations**, and no opinion about where the parent skill
  stores its own artifacts.
- **Timing estimates are unit-based**, not wall-clock; they are relative weights for prioritisation, not a
  schedule in hours.

---

## 10. Validation actually performed

Environment: CPython **3.13.16** on Linux x86-64. Python 3.9+ compatibility is asserted by construction
(`from __future__ import annotations` everywhere, only `typing.Dict/List/Optional/Tuple/Sequence`, and every
file re-parsed with `ast.parse(..., feature_version=(3, 9))`); **no 3.9 interpreter was available to run
the suite**, so 3.9 execution is not claimed as tested.

Executed checks:

1. `python3 tools-selftest.py` — **207/207 checks passed**, covering:
   - structural rules (every filename prefixed, no subfolders, no `SKILL.md`, exactly one
     `knowledge-readme.md`, all seven assets present and valid, all tools compile, documentation references
     every asset and command);
   - `help`, `capabilities` (12 capabilities), `--version`;
   - `plan` on the messy HTML fixture, `--save-plan`, stdin input, explicit `--requirement`/`--assume`,
     invalid count (`99`, `0`), invalid uncertainty (`2`), missing request, empty request, comment-only
     request, missing root;
   - determinism: two identical `plan` runs produce identical JSON apart from timestamps;
   - input sensitivity: raising uncertainty/verification/tool calls raises the recommended count;
   - a feasible request producing a single-stage plan;
   - `assess` single-response vs staged decisions; `estimate`;
   - `negotiate` expand (5→9) and compress (9→4) with ownership and protected-gate invariants, `hold`,
     direction mismatch, out-of-range count, and compression blocked at the stage floor (exit `4`);
   - `decompose` (every requirement in exactly one wave, one merge contract per stage), `scheduler`
     (multi-stage critical path, committed ≥ estimate);
   - `gate` list, pass with full evidence, blocked with incomplete evidence, missing `--evidence`,
     unknown gate;
   - `validate-plan` on the template with `asset-plan-schema.json`, and a broken plan (exit `2`);
   - `ledger` CSV written and asserted, Markdown ledger;
   - `files` with a missing required file and next actions, missing root;
   - the full checkpoint lifecycle: start (and overwrite refusal), status, continue without verification
     (exit `2`), ambiguous `ok` (exit `2`), dry-run, scope change (exit `2`), `stop` (exit `7`), advance with
     verification, missing declared output (exit `4`) and `--force`, block (exit `4`), blocked continue
     (exit `4`), `recover retry`, resume, `set-count` up (and refusal below the opened count), `reopen`,
     unknown action, missing state;
   - bounded repair: three attempts succeed, the fourth exits `7`;
   - `verify` on an incomplete workspace (repair/exit `7`), on a workspace with a declared-but-absent output
     (fail/exit `4`, named in the report), and on a fully completed demo workspace (pass, all five groups);
   - `assert` file signature pass/fail, expected extension, missing file, CSV header/row assertions, JSON
     schema and required key, reference resolution failure and success, manifest;
   - `watch` settled and timeout;
   - `cleanup` unapproved (exit `5`), wrong token (exit `5`), file still present, approved removal, and
     cleanup plus `demo` driven with a relative `--root`/`--workdir` from a different working directory;
   - `demo` with `--workdir`, with `--stages 3`, and with a relative working directory.
2. `python3 tools-work-splitter.py demo --workdir <dir>` — 13-step end-to-end rehearsal (normalize → fixtures
   → extract → plan → decompose/schedule → checkpoint → reply classification → close every stage → ledger
   assert → gate check → verification pass → approval-gated cleanup → bounded repair), final
   `Verification: pass`.
3. Clean-room package test: the Skill Tool folder was copied into a fresh temporary skill root beside a test
   `SKILL.md` and the primary tool was re-run there with relative paths only (`plan`, `checkpoint`,
   `verify`, `assert`, `selftest`), then extracted from the delivered ZIP into a second clean directory and
   re-run again — same results, same file names, no absolute paths required.
4. Manual inspection of every rendered output type: plan table, negotiate trade-offs, decompose waves,
   scheduler path, gate table, inventory, verification report, structured JSON envelopes and error bodies.

Not tested, and therefore not claimed: Windows and macOS execution (the code avoids platform-specific
APIs but only Linux was exercised), a real 3.9/3.10 interpreter, concurrent multi-process writes to the
same state file, and behaviour against non-UTF-8 source requests beyond the decode fallback chain
(`utf-8-sig → utf-8 → cp1252 → latin-1`).

---

## 11. Troubleshooting

| Symptom | Code | Action |
| --- | --- | --- |
| `Packaged resource not found: asset-*.json` | `resource-missing` (3) | The asset must sit beside the tools with exactly that case-sensitive name. Do not rename assets without updating this file and the tool. |
| `--count 99 is outside the allowed range 1-14` | `stage-count-invalid` (2) | Choose a count inside `stage_bounds`, or raise the bound in a policy override deliberately. |
| `No requirements could be extracted` | `requirements-empty` (2) | Add detail, or pass explicit `--requirement` values. |
| `Required file(s) not found` | warning, exit 0 | Expected: the plan still forms, but dependent stages must wait. Ask the user to re-upload; never substitute invented content. |
| `The reply is ambiguous, so the plan was not advanced` | `reply-ambiguous` (2) | Ask for Continue, a stage-count change, or Stop. Extend the lexicon if the user's language is not covered. |
| `The reply changes scope or stage count` | `scope-change` (2) | Run `checkpoint set-count --count N`, then continue. |
| `cannot be closed without a verification record` | `verification-missing` (2) | Record `--verified "what you checked"`, or `--force` and disclose the gap. |
| `Declared outputs are missing` | `output-missing` (4) | Produce the artifact, fix the path, or `--force` and disclose. |
| `Cannot reduce to N stages because k stage(s) are already opened` | `stage-count-invalid` (4) | Pick a count above the opened stage, or reopen earlier stages first. |
| `Cannot compress to N stages … breaking a gate boundary` | `compression-blocked` (4) | Raise the count, or start a fresh plan with `plan --count N`. |
| `Stage Sx exceeded 3 repair attempts` | `repair-limit` (7) | Stop, preserve successful work, escalate with the exact blocker. Do not repeat the unchanged action. |
| `Could not acquire the checkpoint lock within 10s` | `lock-timeout` (6) | Another writer is active or a stale `.lock` exists; retry, or remove the stale lock deliberately. |
| `Approval token 'publish' is not an approved action` / `Temp cleanup requires the 'cleanup' approval token` | `approval-invalid` / `approval-mismatch` (5) | Re-run with `--approve cleanup` after reviewing candidates, or with `--backup` to move instead of delete. |
| `Checkpoint schema version X does not match policy version Y` | `state-schema-mismatch` (2) | Regenerate the checkpoint, or set `checkpoint.schema_version` in a policy override to match an intentionally upgraded state. |
| `Stage selection looks wrong` | — | Inspect with `checkpoint status --format json` and `ledger --ledger-format markdown`, then `negotiate` to a count that fits. |

**Recovery sequence after a failed stage:** `checkpoint block --reason …` → diagnose → `recover --action
reopen|rollback|retry --cause …` → rebuild the affected package → `checkpoint continue --verified …` →
`verify`. Earlier verified stages are never discarded automatically.

**Extension:** add a capability by (1) writing a handler in the primary tool, (2) sharing logic through
`core`, (3) registering it in `core.CAPABILITIES` so `capabilities` lists it, (4) adding a self-test block,
and (5) documenting it in sections 4, 5 and 6 of this file. Add gates by editing
`asset-gate-criteria.json`; add thresholds in a policy override rather than in code.

**Maintenance:** keep the plan and state schema versions in step with the policy
(`schema_version` in `checkpoint`, `PLAN_SCHEMA_VERSION` in core), keep one knowledge file, keep every
filename prefixed, keep the folder free of `SKILL.md` and subfolders, and re-run
`python3 powerful-work-splitter/tools-selftest.py` before every release. The suite is the release gate: it
fails if an asset, a documented command, or a documented invariant drifts.
