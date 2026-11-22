# app-factory-delivery

A reusable Skill Tool component for catalog-driven app-factory delivery: it routes a delivery request to the right capability, produces the artifacts each factory stage needs, gates them, runs bounded verification only after explicit approval, and reports release readiness from checks that actually ran.

This folder is a **Skill Tool component**, not a complete skill. It contains no `SKILL.md`; copy the folder beside a parent package's root `SKILL.md` unchanged.

---

## 1. Purpose

The supplied capability catalog lists 30 categories and 244 proposed specialist skills for universal application creation, software engineering and delivery. Those 244 entries are subject-matter specifications: each one needs its own documentation research and expert execution. What a single Skill Tool can own honestly is the **factory layer that the catalog itself describes in category 29 "Skill Orchestration and App-Factory Operations"**: routing, state, stage artifacts, quality gates, packaging/provenance, bounded verification and capability-boundary escalation.

So this Skill Tool implements the following, for all 30 catalog categories:

* the catalog is a first-class, queryable data asset (all 30 categories / 244 skills) so a request can be routed, filtered and composed;
* the six factory stages of the catalog's own Appendix A composition (intake → design → build-support → verify → deliver → operate) exist as runnable, gated stages;
* every artifact the stages produce is derived from real inputs (requirements text, preset data, evidence files, commits, the project tree on disk) — nothing is a canned sample;
* nothing is ever reported as passing unless the corresponding check ran.

**What this tool does not do:** it does not author application source code for arbitrary languages or frameworks, it does not replace the 244 specialist skills, it does not submit to app stores, and it does not claim a build/test result that was not executed. Section 9 states these boundaries precisely.

---

## 2. Files, roles and execution order

Every file sits directly in `app-factory-delivery/`. No subfolders, no extra knowledge files.

| File | Type | Role |
| --- | --- | --- |
| `tools-app-factory.py` | tool (primary entry point) | CLI: argument parsing, asset loading and cross-validation, project state read/write, stage orchestration, gate recording, `verify` approval flow, `status`, text/JSON rendering, structured errors and exit codes. Run this first; it imports the other tools. |
| `tools-factory-engine.py` | tool (shared module) | Pure domain logic and no I/O: catalog indexing/IDF term scoring, routing and preset recommendation, weighted stack selection, backlog/architecture/test-strategy generation, security control evaluation, project inventory + SHA-256 manifest + tree digest, semver/changelog/release notes, stage gate scoring, readiness aggregation, boundary checks. Imported by the CLI and the self-test. |
| `tools-verification-runner.py` | tool (isolated executor) | The only component that starts child processes. Builds a frozen verification plan (allowlist, timeouts, retry caps, command cap), derives the approval token, validates commands, executes with a minimal environment and truncated output, and renders the report. Separated because it is the security-sensitive surface and is also usable standalone. |
| `tools-catalog-normalizer.py` | tool (input adapter) | Converts a capability list supplied as plain text, Markdown or copied HTML into normalized catalog JSON (categories, skill names, descriptions), stripping tags, attributes, CSS, scripts, numbering marks and stray spacing. Standalone CLI and importable module. |
| `tools-selftest.py` | tool (validation suite) | Runs the real CLI as a subprocess against temporary fixtures and checks 82 assertions across every capability, gate, approval rule and error path. Reusable after any edit. |
| `asset-factory-workflow.json` | asset | Stage definitions, per-stage gate criteria and severities, readiness weights/thresholds, hard limits (file, size, timeout, retry, command caps, excluded dirs, sensitive-file patterns), verification allowlist, and the doc/NFR/test-level templates the engine renders from. |
| `asset-stack-presets.json` | asset | 11 delivery presets: platform, languages, architecture profile, toolchain expectations, artifact checks, bounded verification commands, and 33 candidate stacks scored against 8 weighted criteria. |
| `asset-security-controls.json` | asset | 26 security/privacy/supply-chain controls (4 critical, 12 high, 9 medium, 1 low) with applicability per preset and an evidence hint per control. |
| `asset-skill-catalog.json` | asset | The normalized 30-category / 244-skill catalog extracted from the supplied PDF, with provenance and the six documentation-only appendix lines recorded as ignored. |

**Load order at runtime.** `tools-app-factory.py` resolves its own directory (so it works from any working directory), loads the four assets, cross-validates them (candidate scores complete, preset `catalog_categories` and stage `catalog_skills` resolvable, verification allowlist non-empty), then loads `tools-factory-engine.py`, `tools-catalog-normalizer.py` and `tools-verification-runner.py` by path. No module imports another by package name, so the folder needs no installation and no `PYTHONPATH` change.

**Dependencies:** Python 3.9+ and the standard library only. No third-party packages, no dependency file, no network access, no services, no credentials. Developed and tested with CPython 3.13.16.

---

## 3. Quick start

Run from anywhere; paths are resolved relative to the tool folder and to the project you name.

```bash
# discovery
python3 app-factory-delivery/tools-app-factory.py --help
python3 app-factory-delivery/tools-app-factory.py capabilities

# route a request to skills, a preset and the stage composition
python3 app-factory-delivery/tools-app-factory.py route \
  --request "offline-first Flutter app with conflict-safe sync" --platform mobile --language dart

# start a project (works on an existing tree; refuses only to overwrite existing factory state)
python3 app-factory-delivery/tools-app-factory.py init \
  --project-dir ./inventory --name "Inventory" --preset python-service --target-toolchain python3.13

# six stages, in order
python3 app-factory-delivery/tools-app-factory.py stage requirements --project-dir ./inventory --input reqs.md
python3 app-factory-delivery/tools-app-factory.py stage architecture --project-dir ./inventory
python3 app-factory-delivery/tools-app-factory.py stage tests        --project-dir ./inventory
python3 app-factory-delivery/tools-app-factory.py stage security     --project-dir ./inventory --evidence evidence.json
python3 app-factory-delivery/tools-app-factory.py stage packaging    --project-dir ./inventory --commits commits.txt \
  --rollback-note "Reinstall the previous artefact."
python3 app-factory-delivery/tools-app-factory.py stage readiness    --project-dir ./inventory --accept-advisories

# verification: review, then approve the exact plan
python3 app-factory-delivery/tools-app-factory.py verify --project-dir ./inventory --dry-run
python3 app-factory-delivery/tools-app-factory.py verify --project-dir ./inventory --approve APP-XXXXXXXXXX

python3 app-factory-delivery/tools-app-factory.py status --project-dir ./inventory
```

`--format json` may be placed before or after the subcommand. The self-test suite:

```bash
python3 app-factory-delivery/tools-selftest.py            # 82 checks
python3 app-factory-delivery/tools-selftest.py --keep --verbose
```

---

## 4. Capability reference

Every capability below is implemented and exercised by `tools-selftest.py`.

### 4.1 `capabilities` — discovery

Prints the tool version, Python version, catalog coverage, the operation list and the presets; `--format json` returns the same as a machine-readable document. This is the capability-list command required by the interface contract.

### 4.2 `catalog list | search | show | import` — catalog and list intake

* `catalog list [--category <index|slug|name>] [--brief]` — 30 categories, 244 skills.
* `catalog search --query "<terms>" [--category ...] [--limit N]` — term-weighted search over skill names, descriptions and category names. Tokens are normalized (lowercase, stopwords removed, light singularization) and weighted by inverse document frequency, so "application" and "engineer" do not dominate.
* `catalog show --skill "<slug or distinctive fragment>"` — full description of one skill; ambiguity is an error rather than a guess.
* `catalog import --input <file.txt|.md|.html> [--out catalog.json] [--merge base.json] [--default-category NAME] [--source-format auto|text|markdown|html]` — normalizes a pasted capability list.

Input handling rules (all verified): HTML tags, attributes, CSS classes, `data-*` attributes, `<script>`/`<style>` content, Markdown emphasis, bullets and repeated numbering marks are ignored; real category names, skill names and descriptions are extracted. Recognized shapes:

```text
1. Category Name                    (numbered category)
## Category Name                    (Markdown heading)
Category: Category Name             (explicit category)

1. Skill Name: Something            (numbered skill)
- **Skill Name:** Something         (bullet skill)
Description: what it does.          (explicit description, wrapped lines joined)
- Something — what it does.         (inline item with an em/en dash separator)
- Something: what it does.          (inline item with a colon)
```

Output schema (`schema_version` 1.0): `source{kind,path,sha256}`, `counts{categories,skills,ignored,description_missing}`, `categories[{index,number,name,slug,skills[{index,number,name,slug,category_slug,description,description_source}]}]`, `ignored[{reason,text}]`. `description_source` is `explicit`, `inline`, `continuation` or `missing`. Appendix/annex regions are ignored rather than mistaken for categories. `--merge` de-duplicates on category and skill slug and reports `merge_report{added_categories,added_skills}`.

The bundled catalog was produced with exactly this command from the supplied PDF text: `catalog import --input <pdf text> --counts-only` returns **30 categories, 244 skills, 0 missing descriptions**.

### 4.3 `route` — request routing and stage composition

`route --request "<text>" | --request-file <file> [--platform web|backend|mobile|desktop] [--language <name>] [--category ...] [--limit N]`

Returns: ranked skills with relevance, matched terms and a preset boost for the top presets; ranked preset recommendations with human-readable reasons; and a six-stage composition listing each stage's required catalog skills plus the best-matching specialists. Unknown platforms produce a note instead of a failure. This implements the catalog's "Language and Framework Router" and "Technology Stack Selector" entry points without pretending to be each specialist.

### 4.4 `init` — project state

`init --project-dir DIR [--name NAME] [--preset KEY] [--stack CANDIDATE] [--weights criterion=value,...] [--version 1.0.0] [--target-toolchain python3.13] [--reason "<request>"] [--force --approve overwrite]`

Creates `<project>/.factory-state.json`. An existing project tree is a supported starting point; existing factory state is only replaced with `--force --approve overwrite`. Weights must reference known criteria and sum positive; `--stack` must be a candidate of the preset; `--version` must be semver.

### 4.5 `stage requirements` — intake to backlog

`stage requirements --project-dir DIR [--input reqs.md] [--requirement "..."]... [--persona NAME] [--out DIR] [--strict]`

Parses headings as epics, bullets/numbered lines/`As a … I want … so that …` statements as stories, `AC:`/`Acceptance:` lines as explicit acceptance criteria, `Assumption:`/`Question:` lines as tracked items. Titles are cleaned (leading infinitive, trailing punctuation, capitalization) and capped at 160 characters.

Outputs `requirements-backlog.md` and `.json`: personas with counts, epics, stories with priority (`P0/P1/P2` from keyword rules), NFR tags, acceptance criteria (explicit ones preserved, template-derived ones flagged `generated: true` so they are never mistaken for approved text), NFR coverage across 12 categories with matched-keyword evidence, assumptions (marked `supplied` or `derived`) and open questions.

Gate (5 criteria): `stories_present`, `acceptance_criteria_complete`, `nfr_coverage`, `priorities_assigned`, `assumptions_tracked`.

### 4.6 `stage architecture` — design and stack decision

`stage architecture --project-dir DIR [--preset KEY] [--stack CANDIDATE] [--weights ...] [--target-toolchain ...] [--notes "..."] [--strict]`

Scores every candidate of the preset against 8 criteria with configurable weights (defaults: platform_fit .20, team_skill .15, performance .15, security .15, accessibility .10, maintainability .10, cost .05, ecosystem .10), ranks them, and records the choice. Emits components and interfaces from the preset's architecture profile (`web-static`, `web-spa`, `web-ssr`, `backend`, `cli`, `mobile`, `desktop`, `container`), data model, deployment, version/toolchain policy, risks with mitigations (including a real risk inserted when a required toolchain executable is missing from this environment), milestones, delivery acceptance checks, written factory rules and `ADR-0001`.

Gate (7 criteria): `stack_decision_ranked`, `components_defined`, `data_and_storage`, `deployment_target`, `risks_with_mitigation`, `adr_recorded`, `version_policy` (fails while the target toolchain version is unresolved — pass `--target-toolchain`).

### 4.7 `stage tests` — risk-based test strategy

`stage tests --project-dir DIR [--strict]` (requires the backlog)

Maps every acceptance criterion to test levels by intent (`e2e` for journeys, `integration` for persistence/contracts, `unit` for validation/limits), assigns ids, priorities and an automation flag, and adds six cross-cutting suites (security ×2, accessibility, performance, recovery, contract). Includes test-data/isolation/determinism policy, a defect-to-regression policy, exit criteria and an explicit unmapped-criteria list.

Gate (5 criteria): `criteria_mapped`, `levels_covered`, `cross_cutting_suite`, `data_and_isolation`, `regression_policy`.

### 4.8 `stage security` — security and privacy review

`stage security --project-dir DIR --evidence evidence.json [--strict]`

Evaluates the 26 controls applicable to the recorded preset. Evidence schema:

```json
{
  "reviewer": "release-engineer",
  "notes": "optional",
  "controls": {
    "SEC-01": {"status": "pass", "note": "grep reviewed", "owner": "release-engineer"},
    "SEC-08": {"status": "not_applicable", "note": "no network traffic"}
  }
}
```

`status` ∈ `pass | partial | fail | not_applicable | unknown`. Unknown control ids and invalid statuses are rejected with the allowed set. **A control with no evidence is `unknown`, never `pass`** — that is what makes the gate meaningful. Output rolls up by severity and lists open critical/high items, unevidenced controls, untracked medium findings, privacy gaps and secret-handling gaps.

Gate (5 criteria): `no_open_critical_high`, `evidence_complete`, `medium_tracked`, `privacy_disclosure`, `secrets_hygiene`.

### 4.9 `stage packaging` — provenance, semver and handoff

`stage packaging --project-dir DIR [--commits commits.txt] [--bump auto|major|minor|patch|none] [--current-version X.Y.Z] [--version X.Y.Z] [--rollback-note "..."] [--notes "..."] [--strict]`

* Hashes the release set with SHA-256, excluding the state file, the artifact directory, `catalog-import.json` and the configured excluded directories (`.git`, `node_modules`, `dist`, `build`, `venv`, `__pycache__`, …); enforces `max_files_scanned`, `max_file_bytes_hashed` and `max_total_bytes_hashed`.
* Flags credential-like paths (`.env`, private keys, keystores, `.npmrc`, `.netrc`, `credentials*`) as blocker findings.
* Applies conventional-commit semver rules (`BREAKING`→major, `feat`→minor, `fix/perf/refactor/security`→patch) unless overridden; validates semver.
* Emits `release-manifest.json`, `checksums-sha256.txt` (`sha256sum` format) and `release-notes.md` with grouped changelog, inventory-lite SBOM (file hashes + detected dependency manifests), limits hit and a rollback/recovery section.

Gate (6 criteria): `manifest_complete`, `version_valid`, `changelog_generated`, `sbom_written`, `sensitive_files`, `rollback_notes` (only passes with a supplied `--rollback-note`).

### 4.10 `verify` — bounded verification with approval

`verify --project-dir DIR [--preset KEY] [--dry-run] [--approve TOKEN] [--extra-command "<cmd>"]...`

`--dry-run` prints the frozen plan, its limits and the approval token `APP-<sha256[0:10] of the plan>`, and executes nothing. Execution requires the exact token; a missing or wrong token exits `5` and prints the token to review. Because the token is derived from the plan, the plan that was reviewed is the plan that runs. Any change to the command set (including `--extra-command`) changes the token.

Safety rules enforced by `tools-verification-runner.py`:

* commands come from preset data; `--extra-command` is accepted only when its base executable is on the allowlist (`python3`, `node`, `npm`, `pytest`, `git`, `make`, `java`, `dotnet`, `cargo`, `go`, `gcc`, `cmake`, `docker`, `./gradlew`, …);
* absolute executable paths are rejected, and so are shell fragments (`&&`, `||`, `;`, `|`, backticks, `$(`, `>`, `<`, embedded newlines) — nothing runs through a shell;
* per-command timeout (≤300 s), total budget (900 s), retry cap (2), command cap (12), output truncation (6000 chars per stream);
* minimal child environment (`PATH`, `HOME`, locale, temp dirs, `CI=1`, no bytecode writes);
* a required command whose executable or local wrapper is missing is reported `blocked` — never `passed`;
* the run records a digest of the project tree; readiness later proves the tree has not changed since.

Report statuses: `passed`, `failed`, `timeout`, `blocked`, `skipped` (budget exhausted), `error`. Score weights required commands ×2 and optional ×1.

### 4.11 `stage readiness` — release gate

`stage readiness --project-dir DIR [--accept-advisories] [--require-verification|--no-require-verification] [--strict]`

Aggregates the six stage gates weighted by configured weight (requirements 18, architecture 18, tests 18, security 20, packaging 16), detects artifact drift by re-hashing every recorded artifact, and requires a verification run whose tree digest still matches. `overall = 0.85 × weighted stage average + 0.15 × verification score`; a missing stage scores 0 and is a blocker.

Report: score, pass/strict thresholds (80/95), per-stage table, blockers, advisories, drift and missing artifacts, and concrete recommendations. `release_ready` is true only when the score clears the threshold **and** no blocker remains.

### 4.12 `boundary` — capability boundary and escalation

`boundary [--preset KEY] [--project-dir DIR] [--verbose]`

Checks required/optional executables with `shutil.which`, checks the preset's artifact globs in the project tree, and reports project-size limits. Missing required toolchains produce an explicit escalation ("provide `flutter` in PATH …; until then keep the verification gate blocked") and exit `6`. This is the mechanism that stops the tool from claiming a build it cannot run.

### 4.13 `status` — cross-step state

`status --project-dir DIR` shows project, preset, stack, version, each stage's status/score/failed criteria, whether recorded artifacts still match their checksums (`ok` / `MISSING/CHANGED`), verification state and the next concrete action.

---

## 5. Project state and artifact layout

```
<project>/
├── .factory-state.json          # factory state (delete to reset; not part of the release set)
├── <your source tree>           # never modified by this tool
├── evidence.json, commits.txt   # inputs you supply (names are yours)
└── factory/                     # artifact root, override with --out
    ├── requirements-backlog.md / .json
    ├── architecture-decision.md / .json
    ├── test-strategy.md / .json
    ├── security-review.md / .json
    ├── release-manifest.json, release-notes.md, checksums-sha256.txt
    ├── verification-plan.md | verification-report.md / .json
    └── readiness-report.md / .json
```

State schema (abridged):

```json
{
  "schema_version": "1.0", "tool_version": "1.0.0",
  "name": "Inventory", "preset": "python-service", "stack": "fastapi-uvicorn",
  "weights": {"performance": 0.25}, "version": "1.1.0", "target_toolchain": "python3.13",
  "created_utc": "...", "updated_utc": "...",
  "stages": {
    "requirements": {
      "status": "complete", "completed_utc": "...",
      "artifacts": [{"path": "factory/requirements-backlog.md", "sha256": "...", "bytes": 1234}],
      "gate": {"score": 92.9, "pass": true, "blockers": [], "failed": [], "criteria": [...]},
      "facts": {...}, "details": {...}
    }
  },
  "verification": {"token": "APP-...", "pass": true, "score": 100.0, "tree_digest": "...", "files_digested": 8},
  "history": [{"utc": "...", "event": "stage:packaging", "detail": "gate=pass score=100.0"}]
}
```

Writes are atomic (`tempfile` + `os.replace`), artifact paths are stored relative to the project directory so the state is portable, and state history is capped at 60 entries.

---

## 6. Configuration

All behaviour that could vary per team lives in the assets, not in code.

**`asset-factory-workflow.json`**

| Key | Meaning |
| --- | --- |
| `stages[]` | stage id/title/order, `requires`, `produces`, `catalog_skills`, gate `weight` and `criteria[{id,label,severity}]` |
| `readiness` | `severity_weight` (blocker 5, major 3, minor 1), `pass_threshold` 80, `strict_threshold` 95, `verification_share` 0.15, `require_verification` |
| `limits` | `max_input_bytes` 2 MB, `max_request_chars` 8000, `max_stories` 400, `max_files_scanned` 5000, `max_file_bytes_hashed` 64 MB, `max_total_bytes_hashed` 512 MB, `excluded_dir_names`, `sensitive_file_patterns` |
| `limits.verify` | `per_command_timeout_seconds` 300, `total_timeout_seconds` 900, `max_retries` 2, `max_commands` 12, `max_output_chars` 6000, `allowed_executables` |
| `templates` | acceptance-criteria patterns, personas, priority rules, 12 NFR keyword sets, architecture sections, factory rules, 9 test levels |

**`asset-stack-presets.json`** — `criteria[]` (8 weighted criteria) and `presets{key: {label, platform, component_set, languages, catalog_categories, required_executables, optional_executables, artifact_checks[{id,label,glob,severity}], verification[{id,label,command,required,timeout}], candidates[{key,label,notes,scores}]}}`. Shipped presets: `web-static`, `web-spa`, `nextjs-app`, `node-service`, `python-cli`, `python-service`, `android-kotlin`, `flutter`, `ios-swift`, `desktop-electron`, `docker-service` (33 candidates, 20 verification commands).

**`asset-security-controls.json`** — `controls[{id, category, control, severity, applies_to[], evidence_hint}]` plus `status_values`.

Assets are cross-validated on every run: an unknown preset category slug, an unknown stage catalog skill, an incomplete candidate score set or an empty verification allowlist aborts with a precise error instead of producing a half-working run.

To add a preset: add a preset block with a `component_set` that exists in `tools-factory-engine.py` (`web-static`, `web-spa`, `web-ssr`, `backend`, `cli`, `mobile`, `desktop`, `container`) and scores for all 8 criteria; verification commands must start with an allowlisted executable. To add a gate criterion: add the criterion to the stage and compute its fact in the matching `stage_*` handler in `tools-app-factory.py`.

---

## 7. Safety and approval rules

* **Verification executes only with the exact plan token.** No token → exit `5`, nothing runs. Wrong token → exit `5`, nothing runs.
* **No shell, no arbitrary commands.** Commands are tokenized with `shlex` and run with `shell=False`; shell metacharacters and absolute executable paths are rejected; the base executable must be on the allowlist.
* **Destructive/state-replacing action is gated.** Re-initialising over existing factory state requires `--force --approve overwrite`; without approval the command exits `5`.
* **The tool never deletes or rewrites your source.** It writes only under the artifact root and the state file, atomically.
* **No fabricated success.** Unevidenced security controls are `unknown`; missing executables are `blocked`; missing stages score 0; a changed tree invalidates the recorded verification; `release_ready` requires both threshold and zero blockers.
* **No secrets in the package.** No credentials, endpoints or paid services are referenced. Credential-like files in the *project* are detected and reported as blockers, never read into artifacts.
* **No network access, no telemetry, no external services** from any file in this tool.
* **Bounded execution:** every limit in section 6 is enforced before work starts, and output is truncated before it reaches a report.

---

## 8. Exit codes and error format

| Code | Meaning |
| --- | --- |
| `0` | success |
| `2` | invalid input/arguments (message + hint) |
| `3` | gate failure (stage or verification not passing; also `--strict` failures) |
| `4` | missing prerequisite (no state, no backlog, no evidence, no requirements input) |
| `5` | approval required or approval token mismatch |
| `6` | blocked: required toolchain missing (from `boundary`) |
| `130` | interrupted |

Errors print `error: <message>` and `hint: <action>` on stderr; with `--format json` the error is a JSON object `{"ok": false, "error": ..., "hint": ..., "exit_code": ...}` on stderr, while success payloads on stdout are `{"ok": true, ...}`.

---

## 9. Limits and unavailable integrations

* **No application source generation.** The tool produces plans, contracts, checklists, manifests, provenance and gate reports; it does not write your app's code for you and does not implement the 244 specialist skills.
* **Subject-matter depth.** Architecture components, risks and acceptance-check templates are preset-driven proposals that a specialist reviews; they are not a substitute for the specialist skills in categories 1–28.
* **Toolchain-dependent verification.** In the current environment `python3`, `node`, `npm`, `pytest`, `git`, `gcc`, `java` and `make` exist; `flutter`, `dart`, `cargo`, `go`, `docker`, `dotnet`, `xcodebuild` and Android SDK tools do not. Presets needing them plan commands fine but report `blocked` (never `passed`), and `boundary` states the exact escalation. Store submission, code signing, notarization, device farms and marketplace publishing are **not implemented** — they require credentials, hardware and vendor accounts this tool does not have.
* **SBOM depth.** The SBOM is "inventory-lite": SHA-256 of the delivered tree plus detected dependency manifests. It is **not** a transitive dependency SBOM; use the ecosystem tool (`npm ls --all`, `pip-licenses`, `cyclonedx-*`) for that and attach its output.
* **Catalog data is reference data.** `asset-skill-catalog.json` reproduces the supplied catalog's names and descriptions; it is not an endorsement that each specialist capability is available.
* **No hosted lookup.** The tool never fetches documentation or version matrices over the network; target versions must be supplied (`--target-toolchain`) and verified by you.
* **Single-project scope per state file;** one artifact root per project, and `--out` must stay inside the project directory to keep artifacts portable.

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Recovery |
| --- | --- | --- |
| `no factory state found` (exit 4) | `init` not run for that directory | `init --project-dir <dir> --preset <key>` |
| `tests stage needs the requirements backlog` | stage order | Run `stage requirements` first |
| `security stage needs an evidence file` | no evidence supplied | Provide JSON with `controls{SEC-xx:{status,note}}`; unevidenced controls stay `unknown` |
| Gate FAIL on `version_policy` | target toolchain unresolved | Re-run `stage architecture --target-toolchain python3.13` (or your target) |
| Gate FAIL on `version_valid` | no change set for `--bump auto` | Pass `--commits file` or force `--bump patch` |
| Gate FAIL on `sensitive_files` | credential-like file in the tree | Remove it, rotate the exposed secret, re-run `stage packaging` |
| `verify` exit 5 | approval missing/mismatched | Run `--dry-run`, review the commands, pass `--approve <token>` |
| `blocked` verification result | executable not installed | Install it or run verification where it exists; use `boundary` for the exact escalation |
| readiness `verification_stale` blocker | tree changed after `verify` | Re-run `verify` after the change |
| readiness `artifacts_stable` blocker | stage artifact edited/moved | Re-run the stage that produced it (or `status` to see which artifact changed) |
| `--out must stay inside the project directory` | artifact root escaped the project | Use `<project>/factory` or a subdirectory |
| interrupted run | Ctrl-C | Nothing partial is left behind; the previous state file is intact — re-run the command |

Reset a project's factory state: delete `<project>/.factory-state.json` and the `factory/` directory, then `init` again (source files are untouched).

---

## 11. Validation actually completed

Environment: CPython 3.13.16, Linux sandbox; `python3`, `node`, `npm`, `pytest` 9.0.3, `git`, `gcc`, `java`, `make` available.

**Automated suite — `python3 tools-selftest.py`: 82 checks, 82 passed, 0 failed, 0 skipped.** It covers: `--help` and capability list; `capabilities --format json` (30 categories / 244 skills / 11 presets); HTML, Markdown and plain-text catalog import with markup-stripping assertions; `--merge`; `search`/`show`/`list`; `route` with platform+language and without a preset; `boundary` escalation; the full six-stage pipeline on a real fixture project; manifest/checksum correctness against the file on disk; release-note semver bump; dry-run plan and token; unapproved and wrong-token rejections; allowlist and shell-fragment rejections; real `verify` execution with `python3 -m compileall` and `python3 -m pytest`; readiness pass; drift detection after editing an artifact; recovery after re-running the stage; unknown preset, missing state, empty route, invalid evidence status, strict-mode exit 3, credential-like file detection, `status`; and backlog determinism across two projects.

**Manual checks beyond the suite:**

* catalog normalization of the supplied PDF text reproduced 30 categories / 244 skills / 0 missing descriptions (the source of `asset-skill-catalog.json`);
* JSON output for `capabilities`, `catalog list/search/show`, `route`, `status`, `boundary` inspected and parsed;
* artifacts opened and read: backlog, architecture decision, test strategy, security review, release manifest, checksums, release notes, verification plan/report, readiness report (JSON parsed; Markdown rendered);
* a full pipeline on a real small Python CLI (`python3 -m pytest -q` → 2 passed) reached `release_ready: YES` at 96.6/100 after a passing verification run, and correctly flipped to `NO` after a tree change until `verify` was re-approved;
* package compatibility: the folder was copied beside a test `SKILL.md` in a clean temporary root and re-run using only relative paths (see section 12).

Not performed (and therefore not claimed): building Android/iOS/Flutter/Rust/Go/Docker artifacts (toolchains absent), any code-signing, notarization or store submission, and any multi-machine or network-based verification.

---

## 12. Package compatibility

* The folder contains **no `SKILL.md`**; it can be copied directly beside a parent package's root `SKILL.md`.
* All references are relative to the future skill root, e.g. `app-factory-delivery/asset-factory-workflow.json`; the tools resolve their own directory at runtime, so no installation or working-directory assumption exists.
* Paths are at most two levels deep from the skill root (`app-factory-delivery/<file>`); there are no nested archives and no subfolders.
* File count: **10 files** (5 `tools-`, 4 `asset-`, 1 `knowledge-readme.md`), well within the 500-file limit.
* Compatibility check performed: the folder was copied to a temporary root containing a single test `SKILL.md`, then `python3 app-factory-delivery/tools-app-factory.py capabilities`, a full pipeline and `python3 app-factory-delivery/tools-selftest.py` were run from that root using only relative paths. All passed, and the temporary root was deleted afterwards.

---

## 13. Maintenance

* Re-run `python3 tools-selftest.py` after any edit; it is the regression gate for this component.
* Keep assets and code in step: preset `component_set` values must exist in the engine's `ARCHITECTURE_PROFILES`; stage criteria ids must be computed by the matching stage handler; `applies_to` values in the security asset must be preset keys or `"*"`. Cross-validation catches the first two at start-up; the third is caught by the security rollup.
* Adding a stage: add it to `stages[]` with gate criteria and weight, add a `stage_<id>` handler computing `facts`/`details`, register it in `STAGE_HANDLERS`, and document it here.
* Adding a catalog skill/category: update `asset-skill-catalog.json` (or import an updated list with `catalog import --merge`) and re-check any `catalog_skills`/`catalog_categories` references.
* Keep the verification allowlist narrow. Widen it only for an executable whose commands are understood, and never add a shell interpreter.
* Version notes: bump `TOOL_VERSION` in `tools-app-factory.py` when behaviour changes, and state schema changes in `schema_version` so old state files can be recognised.
