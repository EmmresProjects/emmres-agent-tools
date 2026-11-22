# knowledge-readme.md - Automation Skill Foundry

Reusable Skill Tool component for the AutoForge **Master Automation Skill Catalog**
(`AutoForge_Automation_Skill_Catalog.pdf`, 198 skills across 20 categories).

This folder is a Skill Tool component, not a complete Copilot skill. It contains no
`SKILL.md`. Copy the folder `automation-skill-foundry/` beside a parent package's root
`SKILL.md` and it works unchanged.

---

## 1. Purpose

The catalog is a menu of 198 automation capabilities. This Skill Tool turns that menu
into working operations:

1. **Read the catalog** - list, search, and inspect all 20 categories and 198 skills.
2. **Parse any supplied list** - plain text, Markdown, or copied HTML, from a file or
   the PDF extracted to text, ignoring tags, CSS classes, data attributes, numbering
   marks, and extra spacing.
3. **Route a goal to the smallest safe bundle** - curated multi-skill recipes plus
   catalog search, risk scoring, and lifecycle ordering.
4. **Plan the build** - lifecycle stages, tasks, acceptance checks, dependency edges,
   dry-run design, and ROI.
5. **Schedule it correctly** - real next-run timestamps with time zones, DST gaps and
   overlaps, holiday skips, and launcher exports for cron, systemd, and Windows.
6. **Generate a runnable project** - entry point, config, README, fixtures, launchers,
   verification, and re-build archiving.
7. **Govern it** - approval gates, audit records with hashed secrets, credential
   scanning, and a seven-dimension test plan.

Unified umbrella name: **automation-skill-foundry** (from the catalog's shared purpose:
automation, bots, schedulers, mining, integration, and reliable operations).

### Capability list (all reachable from the CLI)

| # | Capability | Command |
|---|---|---|
| 1 | Built-in help for every tool | `<tool> --help`, `<tool> --version` |
| 2 | List 20 categories with skill counts and ranges | `tools_catalog.py list` |
| 3 | List skills in one category | `tools_catalog.py list --category N` |
| 4 | Free-text skill search with scoring | `tools_catalog.py search --query TEXT` |
| 5 | Inspect one skill (#number, id, or name) | `tools_catalog.py info SKILL` |
| 6 | Parse plain text / Markdown / HTML skill lists | `tools_catalog.py parse --input FILE` |
| 7 | Route an automation goal to a bundle | `tools_catalog.py route --goal TEXT` |
| 8 | Explicit skill selection with risk still scored | `tools_catalog.py route --goal TEXT --skills 12,41,96` |
| 9 | Integrity check of catalog and folder layout | `tools_catalog.py check` |
| 10 | Lifecycle workflow plan with gates | `tools_plan.py workflow --input BUNDLE` |
| 11 | ROI estimate (time, cost, payback) | `tools_plan.py estimate --input BUNDLE` |
| 12 | Dry-run / preview design | `tools_plan.py dryrun --input BUNDLE` |
| 13 | Next-run computation for 7 trigger types | `tools_schedule.py next --cadence ...` |
| 14 | Trigger plan + launcher export for a bundle | `tools_schedule.py plan --input BUNDLE` |
| 15 | Generate a runnable project (dry-run first) | `tools_scaffold.py build --input BUNDLE --out-dir DIR` |
| 16 | Write real files with backup/archive safety | `tools_scaffold.py build ... --apply --force` |
| 17 | Run and verify a generated project | `tools_scaffold.py verify --project DIR` |
| 18 | Export cron / systemd / PowerShell / CRON launchers | `tools_scaffold.py export --project DIR` |
| 19 | Approval gate evaluation | `tools_governance.py gate --input BUNDLE` |
| 20 | Structured audit record with hashed secrets | `tools_governance.py audit --action TEXT` |
| 21 | Credential scan of a project | `tools_governance.py secrets --project DIR` |
| 22 | Seven-dimension test plan with real commands | `tools_governance.py test-plan --project DIR` |
| 23 | End-to-end self-test of the whole component | `tools_selftest.py run` |

---

## 2. Setup and dependencies

* **Runtime:** Python 3.9+ (developed and tested on CPython 3.13.16).
* **Dependencies:** standard library only. `zoneinfo` (3.9+) needs the system IANA
  time-zone database; on slim Linux images install `tzdata` (`apt-get install tzdata`).
  On Windows the `tzdata` PyPI package is required for `zoneinfo`.
* **No network, no credentials, no services, no installs.** Nothing in this component
  registers a scheduler job, sends a message, or contacts a host.

```sh
# from the future skill root (the folder that holds automation-skill-foundry/)
cd automation-skill-foundry

# nothing to install; confirm the runtime
python3 -c "import sys, zoneinfo; print(sys.version)"
```

Supported commands below use `sh`/`bash` or PowerShell explicitly.

---

## 3. Files in this Skill Tool

| File | Prefix | Role |
|---|---|---|
| `tools_catalog.py` | tools- | **Primary entry point.** Parse supplied lists, list/search/info the catalog, route goals to bundles, integrity check. |
| `tools_common.py` | tools- | Shared runtime: catalog loading, resource resolution, JSON/text output, atomic writes, secret scanning, risk scoring, bundle validation, CLI plumbing. |
| `tools_plan.py` | tools- | Lifecycle plan, ROI estimate, dry-run design. |
| `tools_schedule.py` | tools- | Real next-run math (DST/holidays), cron/systemd/Windows launchers, scheduler profiles. |
| `tools_scaffold.py` | tools- | Generate, verify, and export a runnable automation project. |
| `tools_governance.py` | tools- | Approval gates, audit records, credential scans, test plans. |
| `tools_selftest.py` | tools- | End-to-end test harness (36 checks) for the whole component. |
| `knowledge-readme.md` | knowledge- | This file; the only documentation. |
| `asset-skill-catalog.json` | asset- | The full parsed catalog: 20 categories, 198 skills with ids, descriptions, tags. |
| `asset-automation-rules.json` | asset- | Lifecycle stages, stage checks, risk dimensions, approval policy, numeric limits. |
| `asset-recipe-patterns.json` | asset- | 14 curated multi-skill recipes (nightly invoices, uptime, API sync, cleanup, ...). |
| `asset-governance-checks.json` | asset- | Gate rules, seven test dimensions, audit field policy, sensitive-data handling. |
| `asset-scheduler-profiles.json` | asset- | Six trigger presets used by `--profile`. |
| `asset-scaffold-blueprint.json` | asset- | Files the scaffold emits, required markers, post-build checks. |
| `asset-runner-skeleton.py` | asset- | Template copied into generated projects (validated, placeholder-substituted). |
| `asset-default-config.json` | asset- | Packaged defaults; copy, edit the `DEFAULTS` block, pass with `--config`. |
| `asset-test-plan-template.json` | asset- | Fill-in report structure for a signed-off test run. |
| `asset-goal-fixtures.json` | asset- | Known-good routing, parsing, and secret-scan expectations used by the self-test. |

Execution order matters only in that `tools_common.py` and the assets must sit beside
the entry point; run any `tools_*.py` directly. `tools_selftest.py` assumes the folder
is intact and drives the other five through their CLIs.

---

## 4. Quick start

```sh
# 1. Read the catalog
python3 tools_catalog.py list
python3 tools_catalog.py list --category 14          # Business and Workplace Automation
python3 tools_catalog.py search --query "nightly invoice OCR"

# 2. Parse a list you were given (text, Markdown, or HTML)
python3 tools_catalog.py parse --input my-skill-ideas.md

# 3. Route a goal, see the risk, and save the bundle
python3 tools_catalog.py route --goal "process new invoices every night" --out bundle.json

# 4. Plan, schedule, generate, govern
python3 tools_plan.py workflow --input bundle.json
python3 tools_plan.py estimate --input bundle.json --runs-per-month 22 --minutes-saved 15 --labor-rate 40
python3 tools_schedule.py plan --input bundle.json --profile nightly-batch --timezone Asia/Manila
python3 tools_scaffold.py build --input bundle.json --out-dir automation-workspace --apply
python3 tools_scaffold.py verify --project automation-workspace
python3 tools_governance.py gate --input bundle.json --project automation-workspace --owner ops-lead
python3 tools_governance.py test-plan --input bundle.json --project automation-workspace

# 5. Prove the component is intact
python3 tools_selftest.py run --verbose
```

PowerShell equivalent for step 3-4 (paths use the same relative references):

```powershell
python tools_catalog.py route --goal "process new invoices every night" --out bundle.json
python tools_plan.py workflow --input bundle.json
python tools_scaffold.py build --input bundle.json --out-dir automation-workspace --apply
```

---

## 5. Primary entry point: `tools_catalog.py`

### `list`
```sh
python3 tools_catalog.py list                 # 20 categories, counts, number ranges
python3 tools_catalog.py list --category 5    # skills in File, Folder, and Storage Automation
python3 tools_catalog.py list --expand        # every category with every skill
```
Output: `category_total`, `catalog` (with `checksum_sha256`), `categories[]`.

### `search`
```sh
python3 tools_catalog.py search --query "bulk rename files" --limit 5
```
Scoring: exact name word 6, name substring 4, tag 2, description 1. `--category N`
restricts the search. An empty result is a valid answer.

### `parse`
```sh
python3 tools_catalog.py parse --input skills.txt
python3 tools_catalog.py parse catalog.html --category-name "Support Desk Skills"
cat notes.md | python3 tools_catalog.py parse --input /dev/stdin
```
Handles:

* `1. Skill Name: X` + `Description: Y` (the catalog's own format, including PDF text)
* Markdown `1. **Skill Name:** X` + `**Description:** Y`
* HTML lists, tables, and `<div>` blocks; tags, `class`, `data-*`, CSS text are stripped
* Numbered plain names (`12. Bulk Rename Bot`) and pipe tables (`| 12 | Bulk Rename Bot | ... |`)
* Prose, page footers, banners, "Focused skills..." filler, and trailing sections are ignored

Returns `skills[]` (`number`, `id`, `name`, `description`), `categories[]`,
`umbrella` (name plus a `<slug>-skills` id), `warnings[]` for short numbering or
missing descriptions. `--max-skills` (default 500) refuses oversized input.

### `route`
```sh
python3 tools_catalog.py route --goal "process new invoices every night"
python3 tools_catalog.py route --goal "sync new leads into the CRM" --skills 99,105,106
python3 tools_catalog.py route --goal "delete old customer records" --approve risk-review-board
```
Selection order: explicit `--skills`, then the best recipe from
`asset-recipe-patterns.json`, then catalog search (minimum 3 candidates, otherwise
`no_bundle` and a `--skills` hint). Results are deduplicated, mapped to lifecycle
stages, sorted plan → build → trigger → guard → verify → operate → handoff, and capped
by `max_items` (safety stages survive trimming; trimmed skills are listed).

Risk levels: `low`, `medium`, `high`, `critical` from keyword dimensions in
`asset-automation-rules.json`. **High and critical bundles set
`approval_required: true`; critical routing refuses to run without `--approve NAME`.**

### `info`, `check`
```sh
python3 tools_catalog.py info #132
python3 tools_catalog.py check
```
`check` verifies sequential numbering 1-198, unique ids, declared category counts,
unique descriptions, presence of every managed asset and tool, no `SKILL.md`, no
internal subfolders, and every file prefixed. Exit code 1 when a check fails.

---

## 6. `tools_plan.py`

* `workflow --input BUNDLE` → stages with tasks, acceptance checks, artifacts,
  dependency edges, `limits` (retries, timeout, concurrency), `definition_of_done`, and
  `execution_allowed` (false until a high/critical bundle records an approver).
* `estimate --input BUNDLE --runs-per-month N --minutes-saved M --labor-rate R` →
  hours saved, labor saving, maintenance cost, build cost, `payback_months`, verdict
  (`viable` ≤ 12 months, `review`, `not-viable`). Defaults for build hours
  (`2 + 1.5 × skills`) and maintenance (`0.25 × skills`) are labelled as planning
  assumptions; override with `--build-hours` / `--maintenance-hours`.
* `dryrun --input BUNDLE` → the preview contract: validate, resolve targets, compute
  the change set, compare an approved hash, apply, verify, audit. Lists which skills
  may change external state and therefore require a preview. **It never applies
  changes** (`applies_changes: false`).

Unapproved high/critical bundles are refused by `workflow`/`estimate`
(`approval_required`, exit 2) but accepted by `dryrun` so the review can be prepared.

---

## 7. `tools_schedule.py`

```sh
python3 tools_schedule.py next --cadence daily --at 02:30 --timezone Asia/Manila --count 3
python3 tools_schedule.py next --cadence weekly --weekdays mon,tue,wed,thu,fri --at 09:00
python3 tools_schedule.py next --cadence monthly --day-of-month 1 --at 06:00
python3 tools_schedule.py next --cadence hourly --every-hours 4 --at 00:15
python3 tools_schedule.py next --cadence once --on 2026-12-24 --at 18:00
python3 tools_schedule.py next --cadence cron --cron "*/15 9-17 * * mon-fri"
python3 tools_schedule.py plan --input bundle.json --profile nightly-batch --timezone Asia/Manila
```

* Cadences: `hourly`, `daily`, `weekly`, `monthly`, `once`, `cron` (5-field parser),
  `event` (file change, webhook, email, form, database, device).
* **Real timestamps** are computed in the requested IANA zone, not just cron strings.
* **DST gap** (wall time does not exist): the run shifts forward and a `dst_gap` note
  records requested vs resolved time.
* **DST overlap** (wall time occurs twice): one run at the first occurrence, recorded
  as `dst_overlap` with both offsets.
* `--skip-weekends` and `--skip-date YYYY-MM-DD` (repeatable) skip days and report
  `skipped_days`.
* Cron day-of-week is converted correctly (cron 0 = Sunday, Python 0 = Monday), e.g.
  `mon-fri` → `1,2,3,4,5`.
* Policy block: overlap (`skip`/`queue`), missed-run behaviour, retries, timeout, lock
  file, concurrency.
* `launchers[]` contains copy-paste crontab lines, a systemd `OnCalendar` line with
  `Persistent=true`, and a `schtasks` command. Nothing is installed or enabled.
* `--profile` reads `asset-scheduler-profiles.json` (`nightly-batch`,
  `weekday-business-hours`, `hourly-folder-watch`, `monthly-report`,
  `one-time-migration`, `event-webhook`).

Limits: at most 40 runs per call, a 730-day horizon, unknown time zones fail with
`bad_timezone` rather than guessing.

---

## 8. `tools_scaffold.py`

```sh
python3 tools_scaffold.py build --input bundle.json --out-dir automation-workspace          # dry-run
python3 tools_scaffold.py build --input bundle.json --out-dir automation-workspace --apply
python3 tools_scaffold.py verify --project automation-workspace
python3 tools_scaffold.py export --project automation-workspace
```

Generated project: `automation_entry.py` (from `asset-runner-skeleton.py`, placeholder
substitution verified and syntax-checked before writing), `config.json` (paths, limits,
risk, skills, secrets policy), `README.md` (install/run/recover/owner), `requirements.txt`,
`fixtures/sample.csv`, and `run_history.json` (keeps the last five builds).

Guards:
* Dry-run is the default; `--apply` is required to write anything.
* An existing non-empty target is refused (`target_exists`, exit 2) unless `--force`,
  which archives it to `<dir>.bak-N` (max 5 backups).
* Credential patterns in generated text abort the build (`secret_detected`).
* Writes are atomic (temp file + rename) and confined to `--out-dir`.

`verify` runs the entry point in dry-run mode inside the project, creating configured
directories first, with a hard subprocess timeout (default 120 s, max 600 s) and reports
`exit_code`, `stdout`, `stderr`, and per-check results.

`export` writes `launch.sh`, `launch.ps1`, `systemd-user.service`, and
`crontab.snippet`. It never enables or registers them.

---

## 9. `tools_governance.py`

```sh
# Gates: exit 1 while blocked, 0 when cleared
python3 tools_governance.py gate --input bundle.json --project automation-workspace \
  --owner ops-lead --rollback "trash/ restore" --idempotent --stop-control

# Audit: sensitive keys are hashed, never stored raw
python3 tools_governance.py audit --actor ci --action "process invoices" --target inbox \
  --result ok --input api_token=abc123 --input count=42 --append-to logs/audit.jsonl

# Credential scan of a generated project (read-only)
python3 tools_governance.py secrets --project automation-workspace

# Seven-dimension test plan with runnable commands
python3 tools_governance.py test-plan --input bundle.json --project automation-workspace
```

* Gate rules (from `asset-governance-checks.json`): approval, dry-run path, rollback,
  secrets, owner, idempotency, stop control — each tagged with the risk levels it
  applies to. Blocking gates name the remedy.
* Audit records carry `event_id`, `timestamp`, `actor`, `action`, `result`, `approval`,
  optional `target`/`error`, `inputs`, `risk_level`, `retention_days`; the record is
  re-scanned for secrets before it is returned or appended.
* Secret detection: private-key blocks, AWS access keys and secret-like values, bearer
  tokens, password/api-key/secret/token/credential literals, URLs with inline
  credentials, Slack tokens. Clean scans exit 0, findings exit 1, environment
  references such as `os.environ['API_TOKEN']` are not flagged.
* Test dimensions: normal, boundary, failure, repeat, concurrency, recovery,
  clean_state. The tool generates the plan and **never claims a test passed**.

---

## 10. Configuration

Precedence: built-in defaults ← `asset-default-config.json` (`DEFAULTS` block) ←
`--config PATH` ← command-line flags.

```sh
cp automation-skill-foundry/asset-default-config.json ./config.json
# edit the DEFAULTS block, then:
python3 tools_catalog.py route --goal "..." --config ./config.json
```

Keys: `format` (`json`/`text`), `max_items` (bundle cap, default 12),
`max_output_bytes`, `ask_skill_limit` (search candidates, default 7), `secret_scan`,
`require_approval`, `scheduler_name`, `scheduler_owner`, `workspace_dir`,
`python_executable`, `allowed_risk_levels`, `deny_risk_levels` (add `critical` to
refuse critical goals outright), `catalog_file` (swap in your own catalog asset).

Every command also accepts `--format json|text`, `--out PATH`, and `--catalog PATH`.

---

## 11. Data contracts

**Bundle** (written by `route`/`parse`, read by every other tool):

```json
{
  "ok": true, "tool": "tools_catalog.py", "generated_at": "ISO-8601Z",
  "goal": "process new invoices every night",
  "risk": {"level": "high", "score": 6, "drivers": [{"dimension": "financial", "...": "..."}]},
  "approved_by": null, "approval_required": true,
  "stages": ["plan", "build", "trigger", "guard", "verify", "operate", "handoff"],
  "skills": [{"number": 132, "id": "invoice-processing-bot", "name": "Invoice Processing Bot",
              "category": "Business and Workplace Automation",
              "description": "...", "stage": "build", "why": "..."}],
  "skill_count": 9, "per_category": [{"category": "...", "count": 3}],
  "gates": ["..."], "next_commands": ["..."],
  "limits": {"max_items": 12, "bundle_size": 9, "truncated": false, "trimmed_skills": []}
}
```
Minimum the readers need: an object with `goal` (or `title`/`umbrella.name`) and a
non-empty `skills[]` where each item has a `name`. `approved_by` unblocks high/critical
bundles.

**Errors** (stdout, exit 2) and **results**: `{"ok": false, "tool": "...", "error":
{"code": "...", "message": "...", "detail": {...}}}`. Validation failures exit 2;
blocked governance results exit 1; success exits 0; `Ctrl-C` exits 130.

Codes: `empty_input`, `no_skills_found`, `too_many_skills`, `missing_input`,
`missing_file`, `missing_asset`, `bad_json`, `bad_config`, `bad_arguments`,
`bad_input`, `bad_output_dir`, `unknown_skill`, `unknown_category`, `unknown_profile`,
`no_bundle`, `approval_required`, `risk_denied`, `empty_bundle`, `bad_bundle`,
`missing_input`, `target_exists`, `too_many_backups`, `secret_detected`,
`bad_time`, `bad_timezone`, `bad_cron`, `bad_weekday`, `bad_cadence`, `bad_date`,
`output_too_large`, `verify_timeout`, `bad_project`.

---

## 12. Safety and approval rules

* **No destructive action is executed by this component.** The most it writes is a
  project directory (with archive-before-replace) and files inside it.
* **High/critical risk needs a named approver** before planning execution; critical
  routing refuses to run at all without `--approve`.
* **Dry-run first everywhere**: `route`, `scaffold build`, `plan dryrun`, and the
  generated entry point all support preview-before-apply.
* **Secrets never land in artifacts**: generated text is scanned before writing; audit
  inputs with sensitive key names are hashed with SHA-256; environment references are
  preferred; `secrets` exits 1 on findings with a rotate-then-remove remedy.
* **Least privilege in generated code**: bounded retries (default 3), a timeout
  (default 900 s), a lock file, bounded concurrency, and an audit line per run.
* **Nothing is installed, enabled, registered, or sent.** Launchers are files only.
* **Bounded work**: 40 runs per schedule call, 12 skills per bundle by default,
  8 MB parse input, 400 files / 2 MB per file per scan, 600 s verification timeout.

---

## 13. Validation and tests actually completed

Run on CPython 3.13.16 (Linux, `zoneinfo` tzdata present) with `bash`.

| Test | Result |
|---|---|
| `python3 tools_selftest.py run --verbose` | **36 checks, 36 pass, 0 fail** |
| `--help` and `--version` for all six tools | pass (usage text present) |
| Catalog integrity (`tools_catalog.py check`) | pass: 198 skills numbered 1-198, 20/20 category counts match, ids unique, all assets and tools present, no `SKILL.md`, no subfolders, all files prefixed |
| Parser vs source PDF | Extracting the PDF text and re-parsing reproduces all **198 names and 198 descriptions exactly** (byte-for-byte description equality); also round-trips from the packaged asset |
| Parser formats | Plain text, Markdown, HTML, and mixed-with-prose inputs each returned the expected skill name/description pairs; filler lines and prose ignored |
| Routing | 4 recipe goals (invoices, uptime, CRM sync, duplicate cleanup) select the expected recipe, include the required skills, and are ordered by lifecycle stage |
| Risk gating | Critical goal refused with `approval_required`; same goal with `--approve` returns `approval_required: true` and a recorded approver; `tools_plan.py workflow` refuses the unapproved bundle and allows the approved one |
| Error paths | `unknown_skill`, `missing_file`, `bad_json`, `empty_bundle`, `bad_time`, `bad_timezone`, `bad_cron`, `bad_weekday`, `unknown_profile`, `target_exists`, `bad_input`, `missing_input`, `bad_arguments` all returned the documented code and exit status |
| Output format changes | `--format text` renders readable text; `--out` writes the same content to a file; changing `--runs-per-month`/`--minutes-saved` changes the ROI result as expected |
| Config precedence | Packaged `asset-default-config.json` defaults load; an edited copy overrides them (`max_items` 12 → 5, trimmed skills listed); a plain `--config` file overrides built-ins |
| Schedule math | `Asia/Manila` daily 02:30 → `30 2 * * *` at `+08:00`; `America/New_York` 02:30 on 2027-03-14 resolved to `03:30-04:00` with a `dst_gap` note; 01:30 on 2026-11-01 produced a `dst_overlap` note with both offsets; `mon-fri` exported as `1,2,3,4,5` and cron `0 9 * * 1-5` produced only weekdays; `--skip-weekends` skipped Sat/Sun; holiday skip verified |
| Scaffold | Dry-run wrote nothing; `--apply` created 6 files; template placeholders fully substituted and the entry point passed `compile()`; `verify` exit 0 in dry-run; a second build without `--force` refused; `--force` archived to `.bak-1`; `export` wrote all 4 launchers |
| Generated entry point | `--dry-run` exit 0 with `dry_run: true` and an audit line written; apply path exit 0; missing config exit 2; `--stages` prints the stage list |
| Governance | Gate blocked (exit 1) then cleared (exit 0) with the same bundle plus evidence; audit hashed `api_token`, kept `count=42`, and its own secret scan was clean; credential scan clean on the project, then detected `aws_access_key`, `password_literal`, and `url_with_credentials` while ignoring `os.environ['API_TOKEN']` |
| Packaging | `tools_catalog.py check` plus the clean-room run in section 15 |

Known limits of this validation: no Windows, macOS, or PowerShell host was available,
so Windows/PowerShell artifacts were inspected as text only; PDF extraction used
`pypdf` locally to produce the input text (the Skill Tool itself does not read PDFs);
no external service, network call, or scheduler registration was exercised because the
component does none.

---

## 14. Limits and unavailable integrations

* Reads **text-like** sources. PDF, DOCX, and image input must be converted to text
  first (copy-paste, or `pdftotext`/`pypdf` outside this component).
* Risk scoring is a **keyword heuristic** over `asset-automation-rules.json`; it is
  triage, not an assessment of your real systems.
* Recipe coverage is the 14 curated patterns plus search fallback; goals far outside
  them need `--skills` or a new recipe entry.
* ROI numbers depend on the inputs you supply; defaults are labelled assumptions.
* Time-zone math depends on the host IANA database. Without it, `bad_timezone` is
  returned instead of a guessed result (install `tzdata`).
* Launchers are text. Actually registering cron, systemd, Task Scheduler, Power
  Automate, Zapier, Make, or n8n is out of scope and requires a human with host access.
* No live connectors (Slack, Teams, email, cloud APIs), no browser automation, no OCR,
  and no media processing are bundled: the corresponding catalog skills are routed,
  planned, and scaffolded, not executed here.
* The generated project is a **contract-shaped skeleton**: validation, dry-run, retries,
  timeout, lock, and audit are real, while the domain-specific `apply()` step is marked
  `skipped-placeholder` and must be replaced with your business logic.

---

## 15. Packaging and compatibility

Folder layout is flat — every file sits directly in `automation-skill-foundry/`:

```
automation-skill-foundry/
|-- tools_catalog.py            (primary entry point)
|-- tools_common.py
|-- tools_plan.py
|-- tools_schedule.py
|-- tools_scaffold.py
|-- tools_governance.py
|-- tools_selftest.py
|-- knowledge-readme.md
|-- asset-*.json / asset-runner-skeleton.py
```

* 18 files total, depth ≤ 2 from the future skill root (assets are flat, all ≤ 1
  level), no internal subfolders, no
  `SKILL.md`, no archives inside, no unprefixed files.
* Delivered as `automation-skill-foundry-skill-tool.zip`; extracting it puts
  `automation-skill-foundry/` directly at the ZIP root with no wrapper folder.
* Relative references used everywhere: `asset-skill-catalog.json`,
  `asset-automation-rules.json`, `asset-recipe-patterns.json`,
  `asset-governance-checks.json`, `asset-scheduler-profiles.json`,
  `asset-scaffold-blueprint.json`, `asset-runner-skeleton.py`,
  `asset-default-config.json`, `asset-test-plan-template.json`,
  `asset-goal-fixtures.json`. All resolve from the future skill root with exact
  case-sensitive spelling, verified by `tools_catalog.py check` and by the clean-room
  copy test (extract to a temporary root with a test `SKILL.md` beside the folder, run
  `tools_selftest.py run` and the quick-start commands with relative paths).

---

## 16. Troubleshooting, recovery, and maintenance

| Symptom | Cause | Fix |
|---|---|---|
| `bad_timezone` | zone name unknown or tzdata missing | use an IANA name (`Asia/Manila`); install `tzdata` |
| `no_skills_found` | input is prose or an unsupported shape | pass `Skill Name:`/`Description:` lines, or a pipe table |
| `empty_input` | file is empty or only page furniture | check the extracted text |
| `no_bundle` from `route` | fewer than 3 search candidates | rephrase, or pass `--skills 12,41,96` |
| `approval_required` | high/critical bundle without an approver | `--approve NAME` after human review, or set `approved_by` |
| `target_exists` in scaffold | project dir already has files | choose another `--out-dir` or re-run with `--force` |
| `secret_detected` in scaffold | generated text matched a credential pattern | remove the literal from the bundle/config |
| `verify_timeout` | generated project hung | raise `--timeout` (max 600) or fix the loop |
| `__pycache__` inside the folder | Python ran there | ignore it or delete it; snapshots exclude it |

Recovery: every failure is a non-zero exit with a structured payload, and every write
is atomic. If a scaffold build is interrupted, delete the temporary `.foundry-*` files
in the target directory and re-run; if a project was replaced, restore the `.bak-N`
folder produced by `--force`.

Extension: add a skill by editing `asset-skill-catalog.json` (keep sequential numbers
and the declared `skill_count`), add a recipe to `asset-recipe-patterns.json` (numbers
must exist), add risk keywords to `asset-automation-rules.json`, add a trigger preset to
`asset-scheduler-profiles.json`, or add an expectation to `asset-goal-fixtures.json`.
Then run `tools_catalog.py check` and `tools_selftest.py run` — both fail loudly if an
asset reference, count, or behaviour drifts.

Maintenance: owner `automation-owner` (rename in
`asset-default-config.json`), quarterly review, and a deprecation trigger of "the
AutoForge catalog changes or the host Python drops below 3.9". Keep the tool files
stdlib-only so the folder stays copyable with no install step.
