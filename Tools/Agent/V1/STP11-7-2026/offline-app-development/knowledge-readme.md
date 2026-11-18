# knowledge-readme.md — offline-app-development (Skill Tool)

**Skill Tool folder:** `offline-app-development/` · **Primary entry point:** `offline-app-development/tools-offline-app-dev.py` · **Supporting tool:** `offline-app-development/tools-selftest.py`
**Source catalog:** *Full Offline App Development Skill Catalog*, Edition 1.0 (November 2026) — 27 categories, **1095 distinct skill concepts**, 7 description archetypes.
**Runtime:** Python 3.8+ standard library only. **No third-party dependencies. No network access is used or required, ever.**

This is a reusable Skill Tool component, not a complete Copilot skill: drop this folder next to your parent package's root `SKILL.md` and reference it from there. It implements the catalog as one unified, executable toolkit instead of 1095 stubs.

---

## 1. Purpose and capability list

The catalog's 1095 concepts collapse into exactly 7 regular **archetypes**. This tool implements each archetype as real, runnable behavior and adds the surrounding engines an offline-first program actually needs:

| Command | Archetype(s) served | What it does |
|---|---|---|
| `capabilities` | all | Built-in capability list; prints catalog coverage and this tool's capabilities |
| `list` / `search` / `show` | all | Query the full 1095-concept catalog (categories, archetype, full-text search, exact lookup) |
| `brief` | all 7 | Render any catalog concept into a complete, deterministic **capability brief** (responsibility, inputs, outputs, offline boundary, security boundaries, tests, acceptance criteria) specialized per archetype |
| `matrix` | select-configure (e.g. 1.1) | Target platform decision matrix: weighted, configurable 1–5 scoring over 16 targets; ranks the smallest maintainable candidate |
| `profile` | select-configure (e.g. 1.2–1.7, 1.22, 1.37-ish, category-2 stacks) | Emit a structured target platform / framework / CPU-architecture profile (compatibility, dependency weight, performance, accessibility, distribution constraints, escape paths) |
| `plan` | plan (all 111 plan concepts) and build/offline-requirements | Generate an owned, verifiable plan from 11 built-in templates with owners, prerequisites, ordered steps, decision points, failure recovery, required evidence, completion condition |
| `adr` | plan/select-configure support, 1.38 | Architecture Decision Record manager: `new`, `list`, `show`, `supersede` — deterministic numbering, versioned local storage, atomic writes |
| `risk` | implement/evaluate support, 1.37, 1.39 | Risk and **offline-constraint** register: `add`, `list`, `update`, `close`, `export` (json/csv/md) with exposure scoring |
| `accept` | evaluate (all 204 evaluate concepts, e.g. 1.41, 6.37, 6.38) | Target-device acceptance evaluation: verify a recorded pass/warn/fail results file against the checklist derived from the catalog's *Core Acceptance Reference*; emits a retained report; never drives devices itself |
| `verify` | build (integrity checks), 3.x, 4.x, 20.x | Offline environment verification: tool presence on PATH, optional bounded `--probe` (`<tool> --version`, 10 s timeout, no shell), and **SHA-256 bundle manifests** (verify / create) with path-traversal protection and optional strict mode |
| `offline-app-development/tools-selftest.py` | QA | 73-check end-to-end self-test of the whole CLI in a temporary workspace |

Catalog archetypes (counts from the parsed Edition 1.0 PDF):

| Archetype | Count | Catalog wording (abbreviated) |
|---|---|---|
| `implement` | 293 | "…as a focused reusable capability with declared inputs and outputs…" |
| `evaluate` | 204 | "Evaluate … using explicit pass, warn, and fail criteria…" |
| `adapter` | 143 | "Provide … behind a stable interface with capability detection…" |
| `build` | 134 | "Create … from pinned local inputs with integrity checks…" |
| `operate` | 134 | "Operate … through versioned configuration…" |
| `plan` | 111 | "Define … with owners, prerequisites, ordered steps…" |
| `select-configure` | 76 | "Use … to select and configure the smallest maintainable solution…" |

`list --archetype <name>` filters by these; `list --count-only --archetype evaluate` prints `204`, and so on. Every one of the 1095 concepts is reachable through `search`/`show`/`brief`.

---

## 2. Setup and dependencies

**Requirements:** Python 3.8+ on `PATH` (`python3 --version`). No pip installs, no SDKs, no network.

**Run commands (shell explicitly):**

```sh
python3 offline-app-development/tools-offline-app-dev.py --help
python3 offline-app-development/tools-offline-app-dev.py capabilities
python3 offline-app-development/tools-selftest.py
```

**Configuration:** assets are resolved relative to the script's own directory. Override with the `OAD_ASSETS_DIR` environment variable when the tool is invoked from elsewhere with assets staged elsewhere:

```sh
OAD_ASSETS_DIR=/path/to/assets python3 offline-app-development/tools-offline-app-dev.py capabilities
```

**Writable storage** (created by the commands that need it, inside the current working directory unless `--store` says otherwise):

- ADR records: `./adr-records/` (files `adr-NNNN-<slug>.md` plus `adr-index.json`)
- Registers: `./registers/<register-name>.json` (default register `technical-risk`; use `--register offline-constraint` for catalog concepts 1.37)

**Limits and timeouts:** verify probes time out after 10 s; no other external process is ever spawned; the only writes the tool performs are to explicit `--output` paths and the stores above; existing output files are never overwritten without `--force`; all writes are atomic (temp file + `os.replace`).

---

## 3. Command reference (inputs, outputs, parameters)

Global conventions: `--format text|json|md` (default `text`, or as noted), `--output PATH`, `--force`. Exit codes: **0** OK · **1** runtime/IO failure · **2** input/usage error (including missing `--force` and unknown refs) · **130** interrupted. `accept` is special: **0** PASS verdict, **3** WARN verdict, **4** FAIL verdict. `verify`: **0** OK, **1** verification failed.

### 3.1 `capabilities`
Prints version, catalog coverage (source, edition, counts, archetypes), asset directory, runtime, and the capability list. `--format json` emits machine-readable output. No parameters.

### 3.2 `list`
- `--category N|<substring>` — e.g. `--category 6` or `--category android`
- `--archetype <implement|evaluate|adapter|build|operate|plan|select-configure>`
- `--count-only` — print just the number
- `--format text|json`
Without filters: lists the 27 categories with skill counts. With filters: lists compact `ref [archetype] name` rows; `json` emits full skill objects.

```sh
python3 offline-app-development/tools-offline-app-dev.py list
python3 offline-app-development/tools-offline-app-dev.py list --category 7 --archetype adapter
python3 offline-app-development/tools-offline-app-dev.py list --count-only --archetype implement   # 293
```

### 3.3 `search <query>`
Full-text AND-search over name, description, category, archetype (case-insensitive, tokenized).
- `--category`, `--archetype` — restrict
- `--limit N` (default 20)
- `--format text|json`

```sh
python3 offline-app-development/tools-offline-app-dev.py search "keystore adapter"
python3 offline-app-development/tools-offline-app-dev.py search offline --archetype evaluate --limit 10 --format json
```

### 3.4 `show <ref>`
`<ref>` is `N.M` (e.g. `6.25`) or an exact skill name (case-insensitive; unique substring used when unambiguous). Ambiguity and missing refs are errors with candidate lists. Emits text, `--format json` (includes the archetype contract), or `--format md`.

### 3.5 `brief <ref>`
Generates the capability brief for any of the 1095 skills. Archetype-specific sections come from `offline-app-development/asset-brief-templates.json`; substitution placeholders `{ref} {name} {category} {description}` are filled deterministically.
- `--format md|json` (default md)
- `--output PATH`, `--force`

```sh
python3 offline-app-development/tools-offline-app-dev.py brief 1.1                 # select-configure brief for the platform decision matrix
python3 offline-app-development/tools-offline-app-dev.py brief 6.25 --format json
python3 offline-app-development/tools-offline-app-dev.py brief 27.41 --output brief-maintenance-release.md
```

Treat the brief as the work order for the concept: it fixes responsibility, declared inputs/outputs, the offline boundary, security boundaries, the test matrix, and the acceptance contract, leaving only project specifics to the implementer.

### 3.6 `matrix`
Weighted decision matrix (catalog concept 1.1 and the `select-configure` archetype).
- `--targets a,b,c` — subset of the 16 scored targets (default: all)
- `--criteria override.json` — override weights/scores or drop criteria:

```json
{
  "criteria": [{"id": "performance", "weight": 60}, {"id": "offline_fit", "weight": 40}],
  "scores": {"electron": {"performance": 1}},
  "drop_criteria": ["platform_reach"]
}
```

Unknown targets are rejected with the known-target list. Output: ranked table (weighted 0–5), the effective weights, and the note that the top rank is the smallest maintainable candidate *under the chosen weights*, not an automatic decision — record the outcome as an ADR.

```sh
python3 offline-app-development/tools-offline-app-dev.py matrix
python3 offline-app-development/tools-offline-app-dev.py matrix --targets flutter,kotlin-multiplatform,qt --format json
```

### 3.7 `profile <id>`
Structured profile for: `windows macos linux android ios cross-platform flutter react-native dotnet-maui qt tauri electron swiftui jetpack-compose kotlin-multiplatform avalonia uno-platform arm64 x64`. `--list` enumerates. Fields: `min_os`, `cpu_architectures`, `dependency_weight`, `compatibility`, `performance`, `accessibility`, `distribution_constraints`, `escape_paths`, `offline_notes`. Values are engineering guidance distilled from public platform documentation — **always re-verify against your pinned SDK/toolchain**. `--format text|json|md`, `--output`, `--force`.

### 3.8 `plan <template-id>`
- `--list` enumerates the 11 templates: `desktop-first mobile-first universal clean-architecture hexagonal offline-requirements poc-spine maintenance-release upgrade-migration rollback-recovery acceptance-signoff`
- `--set key=value` (repeatable) substitutes `{key}` placeholders; `{date}` defaults to today; set values are echoed into a `Context:` line; unresolved placeholders are listed in the output
- `--format md|json`, `--output`, `--force`

Every template carries: owners (role-based), prerequisites, ordered steps, decision points, failure recovery, required evidence, and an objectively checkable completion condition. Templates map directly onto catalog plan concepts (e.g. `poc-spine` ↔ 1.40, `maintenance-release` ↔ 27.41, `release/upgrade/rollback` ↔ 20.x/22.x/27.x).

```sh
python3 offline-app-development/tools-offline-app-dev.py plan acceptance-signoff --set project=Fieldnotes --format md --output plan.md
```

### 3.9 `adr`
Architecture Decision Record manager (catalog 1.38).
- `adr new --title "…" [--status proposed|accepted|rejected|deprecated] [--decision-makers NAMES] [--supersedes N] [--context TEXT] [--decision TEXT] [--consequences TEXT] [--evidence TEXT] [--date YYYY-MM-DD] [--store DIR] [--force]`
- `adr list [--store DIR] [--format text|json]`
- `adr show NUMBER [--store DIR]`
- `adr supersede NUMBER --by OTHER [--store DIR]` — marks `NUMBER` superseded in the index and rewrites its `- **Status:**` line atomically

Deterministic numbering (`max + 1`), one file per record (`adr-NNNN-slug.md`), `adr-index.json` beside them, template from `offline-app-development/asset-adr-template.md`. Rendering is validated **before** any file is written. If the store is missing it is created; nothing outside `--store` is touched.

```sh
python3 offline-app-development/tools-offline-app-dev.py adr new --title "Use Tauri for desktop" --status accepted --decision-makers "arch board" --decision "Adopt Tauri 2 with vendored crates" --store ./adr-records
```

### 3.10 `risk`
Technically the Technical Risk Register (1.39); with `--register offline-constraint` it is the Offline Constraint Register (1.37). Same engine.
- `risk add --title T --severity 1-5 --likelihood 1-5 --owner O [--category technical|schedule|security|compliance|privacy|performance|accessibility|dependency|distribution|offline-constraint|toolchain|data|vendor|other] [--mitigation M] [--contingency C] [--evidence E] [--register NAME] [--store DIR]`
- `risk list [--status open|mitigating|accepted|closed|transferred] [--min-exposure N] [--format text|json]` — sorted by exposure (severity × likelihood) descending
- `risk update ID [--status …] [--severity N] [--likelihood N] [--title …] [--owner …] [--mitigation …] [--contingency …] [--evidence …]`
- `risk close ID [--evidence TEXT]`
- `risk export [--format json|csv|md] [--output PATH] [--force]` — for release records and audit packets

Validation: severity/likelihood 1–5 integers, register names match `^[a-z0-9][a-z0-9-]*$`, exposure recomputed on every mutation, store writes atomic. Schema (portable contract, draft 2020-12): `offline-app-development/asset-risk-register-schema.json`.

### 3.11 `accept`
Acceptance evaluation (catalog 1.41, 6.37, 6.38, 27.x; checklist derived from the *Core Acceptance Reference*; see `offline-app-development/asset-acceptance-checklist.json`). The tool **records results you observed** on devices/simulators/emulators — it never drives devices, and never counts UNTESTED as pass.
- `accept --init-results results.json [--force]` — writes a blank results template covering all 20 checks
- `accept --results results.json [--checklist default|PATH] [--format md|json] [--output PATH] [--force]`
- Verdict rule: any `fail` → **FAIL** (exit 4); else any `warn`/`untested` → **WARN** (exit 3); else **PASS** (exit 0)
- The report retains platform, toolchain, device/simulator, build, tester, date, per-check result+observation+evidence, and a remediation/retest list. Remediation for failures reads like the catalog requires: *fix, attach evidence, re-run this check, then re-run the full evaluation plus a regression sweep.*
- Results files referencing unknown check ids, or carrying values outside `pass|warn|fail|untested`, are rejected with the known-ids list (exit 2). A custom `--checklist` may add org-specific checks (same schema).

```sh
python3 offline-app-development/tools-offline-app-dev.py accept --init-results qa.json     # fill in, then:
python3 offline-app-development/tools-offline-app-dev.py accept --results qa.json --output acceptance-report.md; echo "verdict exit=$?"
```

### 3.12 `verify`
Offline verification for build bundles and toolchains (catalog 3.x, 4.x, 20.x, 21.x).
- `verify --tools a,b,c [--probe]` — checks `PATH` resolution; with `--probe`, runs `<tool> --version` (10 s timeout, `shell=False`, output truncated to 200 chars). `--probe` is **opt-in** precisely because it executes the binary.
- `verify --root DIR --init-manifest manifest.json [--force]` — hashes every file under DIR (SHA-256) and writes a manifest
- `verify --root DIR --manifest manifest.json [--strict]` — re-hashes and reports: missing files, **sha256 mismatch**, manifest entries with `..` traversal (rejected), and untracked files (warn; `--strict` makes them fail). Example manifest format: `offline-app-development/asset-verify-manifest.example.json`
- Exit codes: 0 OK · 1 verification failed · 2 input error. Read-only except `--init-manifest` (writes only the manifest file)

```sh
python3 offline-app-development/tools-offline-app-dev.py verify --tools python3,git,cmake --probe
python3 offline-app-development/tools-offline-app-dev.py verify --root ./offline-bundle --init-manifest bundle.manifest.json
python3 offline-app-development/tools-offline-app-dev.py verify --root ./offline-bundle --manifest bundle.manifest.json --strict
```

---

## 4. Schemas and data assets (load order)

Referenced from the Skill Tool folder root, exactly once each, loaded on demand by the tool:

| Asset | Role | Consumers |
|---|---|---|
| `offline-app-development/asset-skill-catalog.json` | Full parsed catalog: 27 categories → 1095 skills (`ref`, `name`, `description`, `archetype`), archetype contracts, and the edition's appendix (Core Acceptance Reference, Catalog Design Notes) | `list search show brief capabilities` |
| `offline-app-development/asset-brief-templates.json` | The 7 archetype brief templates (sections with `{ref}/{name}/{category}/{description}` placeholders) | `brief` |
| `offline-app-development/asset-decision-matrix.json` | Weighted criteria (sum 100), 1–5 scores for 16 targets, override-file contract | `matrix` |
| `offline-app-development/asset-platform-profiles.json` | 19 structured platform/framework/arch profiles | `profile` |
| `offline-app-development/asset-plan-templates.json` | 11 plan templates (owners/prerequisites/steps/decision points/failure recovery/evidence/completion condition) | `plan` |
| `offline-app-development/asset-acceptance-checklist.json` | 20 acceptance checks with severities and evidence hints (from the appendix Core Acceptance Reference) | `accept` |
| `offline-app-development/asset-adr-template.md` | ADR markdown body with `{{…}}` placeholders | `adr new` |
| `offline-app-development/asset-risk-register-schema.json` | JSON Schema (draft 2020-12) for registers | `risk` (external validation) |
| `offline-app-development/asset-verify-manifest.example.json` | Example SHA-256 manifest | `verify` |

All references use exact, case-sensitive filenames relative to the Skill Tool folder. No paths outside the folder are required at runtime (except `OAD_ASSETS_DIR` if you choose to relocate assets).

### Register record (summary)
`{schema_version:1, register, created, records:[{id:"R-0001", title, category, severity 1-5, likelihood 1-5, exposure 1-25, status, owner, mitigation, contingency, evidence, created, updated, closed}]}` — see `offline-app-development/asset-risk-register-schema.json` for the authoritative schema.

### Acceptance results file (summary)
`{platform, toolchain, device_or_simulator, tester, build, date, results: {check-id: {result: pass|warn|fail|untested, observation, evidence}}}` — create with `accept --init-results`.

---

## 5. Safety and approval rules

- **No network, no external services.** The tool never opens a socket. Nothing is published, submitted, signed, or paid.
- **No devices are driven.** `accept` evaluates results you recorded; it does not touch phones, emulators, or installers.
- **No secrets, ever.** Do not paste keys, store credentials, or signing material into ADRs, registers, briefs, or manifests. Evidence references are paths/IDs, not material.
- **Writes are bounded and atomic.** Only explicit `--output` paths and `--store` directories are written; existing files require `--force`; readers never see half-written files (temp + `os.replace`).
- **Human approval required before anything destructive or privileged** elsewhere in your pipeline (signing, store submission, rollout, rollback execution, payment/ entitlement changes). This tool *plans and records* those steps; it does not perform them. The `plan rollback-recovery` and `plan acceptance-signoff` templates exist precisely so approval gates and rollback triggers are written before they are needed.
- **`verify --probe` executes the probed binary** with a 10 s timeout and no shell; it is opt-in. Without `--probe`, `verify` only resolves PATH entries and hashes files.
- **Registers and ADRs are plain JSON/Markdown in your working tree** — protect them with your normal repo ACLs; they may contain sensitive risk descriptions (no credentials allowed, still internal documents).
- **Exit codes are contractual** (see §3); CI may gate on them. A `grep`-based wrap is *not* equivalent.

---

## 6. Validation and tests actually completed

On 2026-11-06 with `python3` 3.13 (tool targets 3.8+):

1. **Syntax/AST parse** of both tools. 2. **Manual CLI exercises** of every subcommand including `--format` variants, `--output`/`--force`, `--set` substitution, custom criteria override, verdict paths (PASS/WARN/FAIL with exit codes 0/3/4), tamper detection (sha256 mismatch → 1), traversal rejection, and overwrite refusals (exit 2). 3. **`offline-app-development/tools-selftest.py`: 73/73 checks PASS** (help, capabilities, list/search/show incl. error paths, all 7 brief archetypes, brief overwrite protection, matrix determinism + override + error paths, profiles, plans, full ADR lifecycle, risk register lifecycle + export formats + validation, accept verdict matrix + rejection of unknown ids/values, verify tool/manifest/strict/traversal, cleanup of temp workspace). 4. **Catalog data integrity:** all 1095 parsed entries carry `ref`, `name`, `description`, `archetype`; archetype counts match the PDF text (implement 293, evaluate 204, adapter 143, build 134, operate 134, plan 111, select-configure 76); category counts match the PDF index and cover all 27 categories; the single appendix-glued description (27.41) was truncated and the appendix preserved inside `offline-app-development/asset-skill-catalog.json` and summarized in §7.

Not tested / not applicable here: real device acceptance runs, real signing, store submission, and any behavior requiring the actual pinned toolchains the catalog governs — see Limitations.

### Self-test

```sh
python3 offline-app-development/tools-selftest.py          # fresh temp workspace, removed afterwards
python3 offline-app-development/tools-selftest.py --keep   # keep workspace for debugging
python3 offline-app-development/tools-selftest.py -v       # verbose output on failures
```

---

## 7. Catalog appendix (carried into the package)

**Core Acceptance Reference** (the `accept` checklist derives from this):
1. A clean machine installs the complete local toolchain from the offline bundle with no first-run download.
2. Locked dependencies restore from local caches, then debug and release builds succeed with network access blocked.
3. The application installs, launches, stores and migrates data, survives restart, handles denied permissions, and preserves user data through upgrade and rollback.
4. Automated unit, integration, UI, accessibility, security, localization, offline, and performance tests pass on representative simulators, emulators, and physical target devices.
5. Release artifacts are reproducible where the platform permits, signed with controlled keys, accompanied by checksums, SBOM, license manifest, privacy declarations, and rollback instructions.
6. Features that require external infrastructure (push, payments, store submission, remote backend) degrade clearly when offline and are verified separately in controlled connected environments.

**Catalog Design Notes:** keep platform-independent Skills separate from platform-specific toolchain/permission/signing/store rules · treat the offline dependency bundle as a release artifact (pin versions and hashes, collect licenses, prove restoration on a clean machine with the network blocked) · use physical devices for hardware-dependent acceptance (camera, mic, GPS, Bluetooth, biometrics, background execution, battery, thermal, notifications) · never place private signing keys or store/payment credentials inside the catalog or source repository · freeze one passing end-to-end reference build before expanding features, and re-run affected joins after every toolchain/SDK/database/dependency update.

Full text also lives in `offline-app-development/asset-skill-catalog.json → appendix`.

---

## 8. Limitations and unavailable integrations

- **This is not 1095 separate executables.** The catalog's concepts are *responsibilities*; this tool implements their common machinery (catalog, briefs, matrix, profiles, plans, ADRs, registers, acceptance evaluation, offline verification). Platform-specific actions named by a concept — e.g. "Xcode 15 toolchain setup", "notarized DMG sign", "Play upload" — are still performed by your engineers and toolchains; the tool generates the work order, plans, checklists, and evidence records for them.
- **No signing, notarization, store submission, push, payment, or backend operations.** These inherently need external infrastructure and credentials; they are deliberately out of scope offline. They appear in plans as gated steps with rollback instructions.
- **Acceptance evaluation is record-based.** It cannot and does not execute tests on your devices; feed it honest results. Its value is a strict verdict rule, retained evidence, and remediation discipline.
- **Profile/matrix data are guidance, not certification.** Minimum OS versions, weights, and scores drift; override the matrix (`--criteria`) and record the rationale as an ADR. The only source of truth for a release is your pinned toolchain.
- **ADR/register storage is single-writer local JSON.** Concurrent multi-writer use across a team requires the files to live in version control (recommended) or a locking wrapper; the tool does not lock across processes.
- **`--probe` runs arbitrary `<tool> --version`** for tools you explicitly name — that is your own binaries, but treat the list as trusted input.
- **PDF extraction fidelity:** the dataset was extracted programmatically from the source PDF; 1095/1095 entries parsed with exact counts per category. If you have errata for the catalog, edit `offline-app-development/asset-skill-catalog.json` (it is the single source for `list`/`search`/`show`/`brief`).

---

## 9. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `missing asset: asset-*.json (looked in …)` exit 1 | Tool moved without assets, or `OAD_ASSETS_DIR` wrong | Keep the folder intact; set `OAD_ASSETS_DIR` to the folder containing the assets |
| `error: no skill at ref 99.99` exit 2 | Bad ref | `list --category N` / `search <terms>` to find refs |
| `error: ambiguous skill name … close matches: …` exit 2 | Substring matched multiple names | Use the `N.M` ref or the exact name |
| `error: refusing to overwrite existing file …` exit 2 | Output path exists | Pass `--force`, or choose a new path |
| `accept` prints FAIL with exit 4 | A check failed | Read the Remediation section; fix; re-run that check and a regression sweep; attach evidence; do not edit the report — supersede it |
| `verify: FAILED … sha256 mismatch` | Bundle corrupted or manifest stale | Re-restore the bundle from source; if the change was intentional, regenerate the manifest **deliberately** (`--init-manifest`) and record why in the release notes |
| `verify … path traversal outside root rejected` | A manifest entry contains `..` | Rebuild the manifest from the real bundle root; never hand-write paths outside it |
| `matrix --criteria` has no effect | Override file shape wrong | Must be `{"criteria":[{"id":…,"weight":N}],"scores":{…},"drop_criteria":[…]}`; unknown target/criterion ids are reported |
| ADR file written, but heading check fails | `offline-app-development/asset-adr-template.md` edited badly | Restore first line `# ADR-{{number}}: {{title}}`; nothing is indexed when rendering fails |
| Register rejected: `schema_version` | Register file from a newer schema | Keep `schema_version: 1`; migrate manually if you bump it |
| Exit 130 | Ctrl-C | Safe; atomic writes mean no partial files |

**Recovery philosophy:** every write is atomic; every verification is read-only; every gate is rerunnable. If anything looks wrong, discard the output, fix the input, re-run — the tool never half-applies a change.

---

## 10. Extension and maintenance

- **Add/adjust brief sections:** edit `offline-app-development/asset-brief-templates.json` (keep placeholder names `{ref} {name} {category} {description}`).
- **Tune the platform decision:** edit `offline-app-development/asset-decision-matrix.json` weights/scores, or ship an org-specific override file — no code change needed.
- **Add a profile:** append an object to `offline-app-development/asset-platform-profiles.json → profiles` (keep the field set stable); it appears in `profile --list` automatically.
- **Add a plan:** append to `offline-app-development/asset-plan-templates.json → templates` with the same keys; `plan --list` picks it up.
- **Add acceptance checks:** append to `offline-app-development/asset-acceptance-checklist.json → checks` (unique `id`, `severity`, `evidence_hint`); existing results files remain valid (new checks report as `untested`).
- **Bump the catalog:** replace `offline-app-development/asset-skill-catalog.json` with a re-parse (or hand edits) maintaining the schema: `{categories:[{id,name,expected,count,skills:[{num,name,description,archetype,ref}]}], archetypes:{…}, total_skills, total_categories, appendix, source, edition}`.
- **Versioning:** `TOOL_VERSION` in the tool; asset schema versions are carried inside each JSON (`schema_version`) or the asset's `version`/`description` fields. Bump both on breaking changes and note them here.
- **Packaging:** this folder is self-contained; it drops beside a parent skill's root `SKILL.md`. Do not add a nested `SKILL.md`, do not create subfolders, keep filenames lowercase-hyphenated and prefixed (`tools-`, `knowledge-`, `asset-`). Paths between the folder and a future skill root are relative and forward-slashed.

## 11. File inventory (prefix = role)

- `offline-app-development/tools-offline-app-dev.py` — primary CLI, all commands (§3)
- `offline-app-development/tools-selftest.py` — 73-check end-to-end self-test
- `offline-app-development/knowledge-readme.md` — this file (the only knowledge file)
- `offline-app-development/asset-skill-catalog.json` · `offline-app-development/asset-brief-templates.json` · `offline-app-development/asset-decision-matrix.json` · `offline-app-development/asset-platform-profiles.json` · `offline-app-development/asset-plan-templates.json` · `offline-app-development/asset-acceptance-checklist.json` · `offline-app-development/asset-adr-template.md` · `offline-app-development/asset-risk-register-schema.json` · `offline-app-development/asset-verify-manifest.example.json` — see §4
