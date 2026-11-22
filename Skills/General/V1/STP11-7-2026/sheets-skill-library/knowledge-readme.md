# sheets-skill-library - Knowledge and Operations Guide

`sheets-skill-library` is a reusable **Skill Tool component** for Google Sheets capability work. It turns the
*Google Sheets Agent Skill Catalog* (28 categories, 280 named capabilities) into something an agent or a human
can actually execute, review and audit:

* browse and search the capability catalog,
* parse any plain-text / Markdown / copied-HTML capability list into the same catalog shape,
* generate per-capability **activation plans** (purpose, surfaces, workflow, inputs, outputs, validation rules,
  limits, fallbacks, tests),
* attach **human-approval gates** that block high-risk capabilities until a named approver signs off,
* produce a **review workbook** (`.xlsx`) plus Markdown/CSV/JSON artefacts a reviewer can act on,
* **audit** text and workbooks for PII, secrets, destructive, publishing and permission risk,
* generate and validate **Google Sheets formulas** from a maintainable pattern pack.

This folder is a component of a larger Copilot skill. It intentionally contains **no `SKILL.md`**: the parent
package owns that file, and this folder is copied beside it unchanged.

---

## 1. Contents and roles

| File | Role |
| --- | --- |
| `tools-spreadsheet-skill-library.py` | **Primary entry point.** Python 3.9+ CLI implementing every operation (`catalog`, `extract`, `plan`, `build`, `approve`, `audit`, `formulas`, `validate-workbook`, `check`, `capabilities`, `self-test`). Standard library only; `openpyxl` optional for workbooks. |
| `tools-catalog-parser.mjs` | Secondary parser entry point for JavaScript/Node hosts (Node 18+, zero dependencies). Parses the same lists and validates a catalog: `extract`, `verify`, `capabilities`. Writes catalog JSON the Python tool accepts unchanged. |
| `tools-config.json` | Operational configuration: paths, limits, timeouts, retries, integration switches, output filenames, approval TTL. Loaded automatically from the folder; override with `--config`. |
| `asset-skill-catalog.json` | The parsed catalog: 28 categories / 280 capabilities with id, name, slug, description, keywords. Validated on every load. |
| `asset-capability-map.json` | Per-capability execution semantics keyed by **id + name + slug**: risk tier, surface codes, workflow, inputs, validation, limits, fallbacks, Google APIs. |
| `asset-approval-policy.json` | Gate definitions and keyword triggers. Decisions about what needs approval live here, not in code. |
| `asset-formula-patterns.json` | Formula pattern pack for `formulas` (12 patterns). Extends and overrides the built-in pack. |
| `asset-output-contract.schema.json` | JSON Schema for the success/error envelopes every command emits, including the full error-code list. |
| `asset-capability-input.schema.json` | JSON Schema for `asset-capability-map.json` entries (surface codes, risk tiers, required arrays). |
| `asset-plan-template.md` | Section skeleton for plan documents, with `{{placeholder}}` slots. |
| `asset-governance-checklist.md` | Ten-point pre-handoff checklist rendered into review packs and referenced by the plan. |

Execution order at runtime: `tools-config.json` → `asset-skill-catalog.json` (or `--catalog`) →
`asset-capability-map.json` → `asset-approval-policy.json` → `asset-formula-patterns.json`.
The `.mjs` parser is independent and only needs a source file; its output can be fed back through `--catalog`.

Everything in this folder is flat: no subdirectories, no `SKILL.md`, no archives.

---

## 2. Setup and dependencies

* **Python 3.9+** (tested on 3.13.16). No third-party package is required for `catalog`, `extract`, `plan`,
  `approve`, `audit`, `formulas`, `check`, `capabilities`, `self-test`.
* **`openpyxl` 3.1.x** (tested with 3.1.5) is needed only to *write* or *inspect* `.xlsx` files
  (`build` workbook, `validate-workbook`, `audit --workbook`). Install with:

```sh
python3 -m pip install "openpyxl==3.1.5"
```

* **Node 18+** (tested with Node 20.20.2) is needed only for `tools-catalog-parser.mjs`.

Without `openpyxl` the tool degrades honestly: `build` still writes the JSON plan, Markdown plan and CSV
fallback, and reports `workbook: openpyxl not installed` in the summary and manifest. No capability is
silently faked.

All commands below assume `<skill-root>` is the folder that will contain `SKILL.md`, with this folder inside it:

```sh
cd <skill-root>
```

---

## 3. Exact commands

Primary entry point (shell shown explicitly):

```sh
# bash / zsh
python3 sheets-skill-library/tools-spreadsheet-skill-library.py --help
python3 sheets-skill-library/tools-spreadsheet-skill-library.py capabilities

# Windows PowerShell
python sheets-skill-library\tools-spreadsheet-skill-library.py --help
```

From inside the component folder the same commands shorten to `python3 tools-spreadsheet-skill-library.py ...`.
The tool resolves all assets relative to its own location, so the working directory does not matter.

Secondary parser:

```sh
node sheets-skill-library/tools-catalog-parser.mjs --help
node sheets-skill-library/tools-catalog-parser.mjs verify --catalog sheets-skill-library/asset-skill-catalog.json
```

Exit codes are stable across both entry points: `0` success, `2` invalid input/usage, `3` blocked by approval
or by a failing audit, `4` internal error, `130` cancelled (`Ctrl+C` / `SIGTERM`).

---

## 4. Capability list of the tool

Run `capabilities` for the live list. Summary:

| Operation | What it does |
| --- | --- |
| `catalog` | Print/browse/search the catalog; filter by category number, range or name fragment, or free text. |
| `extract` | Parse plain text, Markdown or pasted HTML into catalog JSON with slugs, keywords and stable ids. |
| `plan` | Build activation plans + gate register as JSON and Markdown, without a workbook. |
| `build` | Produce the full bundle: plan JSON/Markdown, review `.xlsx` (4 sheets), manifest, risk audit, CSV fallback. |
| `approve` | Read-only gate status, or record scoped, expiring approvals (`--confirm-approval`). |
| `audit` | Scan text or a workbook for PII, secrets, destructive/publishing/permission/financial risk. |
| `formulas` | List patterns, generate a formula from a pattern, or validate a literal formula. |
| `validate-workbook` | Inspect an `.xlsx`: sheets, dimensions, formulas, freeze panes, validations, protection. |
| `check` | Validate config, catalog, capability-map coverage, assets, knowledge references, package shape. |
| `capabilities` | List the operations above. |
| `self-test` | Run the built-in 22-check verification suite end to end. |

The catalog itself is the capability list *of the subject matter*: 280 capabilities in 28 categories
(file lifecycle, sharing, editing, view ergonomics, formatting, number formats, conditional formatting,
validation, sorting/filtering, cleanup, formula engineering, lookup/logic, arrays/QUERY, function families,
named ranges, charts, pivots, external data, Forms, Apps Script, add-ins, dashboards, printing/export,
accessibility, security/governance, testing/audit, templates, orchestration). `catalog --format text` prints
all of them with ids.

---

## 5. Inputs, outputs, parameters

### 5.1 Global options (before the subcommand)

| Option | Meaning |
| --- | --- |
| `--config PATH` | Config file to use instead of `tools-config.json`. |
| `--skill-root PATH` | Component folder holding `tools-*`/`asset-*` files (default: the script's own folder). |
| `--format text\|json` | Human text (default) or the structured envelope described by `asset-output-contract.schema.json`. |
| `--quiet` | Suppress human-readable text (still emits JSON when `--format json`). |
| `--debug` | Print a traceback for unexpected failures. |
| `--version` / `--help` | Version / help. |

### 5.2 Capability selection (shared by `plan` and `build`)

| Option | Meaning |
| --- | --- |
| `--all` | Select everything (280 capabilities). |
| `--category SPEC` | Category number, range (`3,7-9`) or name fragment (`permissions`). Repeatable; comma lists are accepted. |
| `--skill ID` | Capability id (`13.2`), slug (`filter-formula-designer`) or exact name. Repeatable; comma lists (`12.3,13.2`) are accepted. |
| `--search TEXT` | Free-text match over name, description and category name. |
| `--list-file PATH` | File of newline-separated ids/slugs/names; unknown lines are reported, not fatal. |
| `--max-skills N` | Hard cap on the selection. |
| `--catalog PATH` | Use another catalog JSON (for example one produced by `extract`). |
| `--allow-derived` | Allow capabilities absent from `asset-capability-map.json` to run on derived baseline semantics. |
| `--out DIR` | Output directory (default `runs/`). |
| `--subject NAME` | Label used in the plan title and manifest. |
| `--fail-on-blocked` | Exit `3` while any approval gate is pending. |
| `--reset-approvals` | Discard the approval store in `--out` before use. |
| `--no-workbook` / `--no-workbook-overwrite` | Skip the `.xlsx`, or refuse to replace an existing one. |

### 5.3 Outputs

`build --out DIR` writes, using the filenames in `tools-config.json`:

| File | Content |
| --- | --- |
| `activation-plan.json` | Machine-readable plans, gate register, gate summary, timestamp. |
| `activation-plan.md` | Reviewable plan: risk table, pending approvals, per-capability blocks, safety rules. |
| `activation-review.xlsx` | 4 sheets: **Activation Plan** (12 columns incl. status validation list), **Capability Index**, **Gate Register**, **Run Log**. Frozen headers, wrapped rule columns, status colour fills. |
| `bundle-manifest.json` | Counts, origins, enabled/disabled integrations, approval summary, artifact sizes, limits. |
| `risk-audit.json` | Audit of the produced workbook (PII/secrets/risk) plus `structure` (sheets, hidden sheets, protected sheets, external links, defined names). |
| `activation-plan.csv` | CSV fallback (UTF-8, LF) when `write_csv_fallback` is on or `openpyxl` is missing. |
| `approvals.json` | Created by `approve`; scoped, expiring approval records. |

`extract` writes a single catalog JSON (`--out` file, or `DIR/extracted-catalog.json`).
`formulas --out DIR` writes `DIR/formula.json`. `audit --out` writes `risk-audit.json`.

### 5.4 Structured output

With `--format json` every command prints exactly one envelope:

```json
{"ok": true, "tool": "sheets-skill-library", "version": "1.0.0", "command": "build", "data": { ... }}
```

Errors use:

```json
{"ok": false, "tool": "sheets-skill-library", "version": "1.0.0",
 "error": {"code": "APPROVAL_PENDING", "message": "...", "hint": "...", "exit_code": 3}}
```

Codes are enumerated in `asset-output-contract.schema.json`. Every error carries a hint that names the next
action. `check` cross-validates the contract: every code the tool raises must be declared in the schema's
`enum` (undeclared codes fail the check; unused declared codes are reported).

---

## 6. Capability-by-capability usage

### 6.1 `catalog`

```sh
python3 tools-spreadsheet-skill-library.py catalog                       # everything, text
python3 tools-spreadsheet-skill-library.py catalog --category 25         # one category
python3 tools-spreadsheet-skill-library.py catalog --category 3,7-9      # a range
python3 tools-spreadsheet-skill-library.py catalog --category permissions
python3 tools-spreadsheet-skill-library.py catalog --search "pivot total"
python3 tools-spreadsheet-skill-library.py --format json catalog --search "de-ident" > hits.json
```

Use it to discover ids before selecting. Unknown categories raise `CATEGORY_NOT_FOUND` with a pointer back
to the listing command.

### 6.2 `extract`

Accepts three shapes; tags, CSS classes, data attributes, numbering marks, bullets, `&nbsp;` and smart quotes
are stripped before parsing:

* numbered catalog form: `1. Skill Name: Name` + `Description: ...` (continuation lines are joined),
* `Name: description` pairs,
* category headings (`## 12. Cleaning and Parsing`, `12. Cleaning and Parsing`, `12) Cleaning and Parsing`).

```sh
python3 tools-spreadsheet-skill-library.py extract --input my-list.md --out my-catalog.json
node tools-catalog-parser.mjs extract --input copied.html --out my-catalog.json
```

Guarantees:

* ids are normalised to `N.M` and renumbered per category, so re-used numbers cannot collide,
* slugs are lowercase-hyphenated; keywords are extracted for search,
* output is validated against the same rules as the shipped catalog before it is written,
* empty or unrecognisable input fails with `INPUT_EMPTY` / `EXTRACT_EMPTY`, oversize input with
  `EXTRACT_TOO_LARGE` and the configured limits,
* capabilities with no description are kept and reported in `extraction_warnings` rather than dropped.

### 6.3 `plan` and `build`

```sh
# plan only (fast, no workbook)
python3 tools-spreadsheet-skill-library.py plan --category 13 --out runs/arrays

# full review bundle for two categories and a named capability
python3 tools-spreadsheet-skill-library.py build --category 13,25 --skill 27.4 \
  --out runs/q4-hardening --subject "Q4 workbook hardening"

# a specific capability set (ids, slugs or names, comma-separated or repeated flags)
python3 tools-spreadsheet-skill-library.py build --skill 12.3,13.2,17.10 --out runs/lookups --subject "Lookup review"

# everything, with every gate visible
python3 tools-spreadsheet-skill-library.py build --all --out runs/full-catalog
```

Each plan entry contains: purpose (the catalogued description), risk tier, surface list, unavailable
integrations, workflow steps, inputs, outputs, validation rules, limits, fallbacks, required Google APIs, and
`semantics: catalogued|derived`. Selection decisions are echoed in `selection.notes` (why each capability was
picked) and recorded in the manifest so a reviewer can see *why* a capability is in the bundle.

Performance observed: a full 280-capability build (plan JSON/MD, workbook with 281 plan rows and 351 gate
rows, audit, manifest, CSV) completes in about 0.85 s locally.

### 6.4 `approve`

```sh
# read-only status
python3 tools-spreadsheet-skill-library.py approve --out runs/q4-hardening

# record approvals for specific capabilities and gates
python3 tools-spreadsheet-skill-library.py approve --out runs/q4-hardening \
  --capability 25.1 --gate backup_before_change --gate approval_required \
  --approver "Ana Reyes" --note "Copy at /Backups/q4-pre" --confirm-approval

# clear the store (also requires confirmation)
python3 tools-spreadsheet-skill-library.py approve --out runs/q4-hardening --reset-approvals --confirm-approval
```

Rules: approvals are scoped to one capability **and** one gate, expire after `approvals.ttl_hours`
(default 24 h, override with `--hours`), are written atomically, and the previous store is copied to
`approvals.backup.json` before it is replaced. Without `--confirm-approval` the command never writes; it only
prints the gate register. Re-running `build` re-reads the store, so approvals survive rebuilds and expired
approvals revert to `pending`.

### 6.5 `audit`

```sh
python3 tools-spreadsheet-skill-library.py audit --input runs/q4-hardening/activation-plan.md --fail-on-high
python3 tools-spreadsheet-skill-library.py audit --workbook runs/q4-hardening/activation-review.xlsx --out runs/q4-hardening
```

Rules: `PII_EMAIL`, `PII_PHONE`, `PII_ID_PH`, `SECRET_TOKEN` (AWS/GitHub/OpenAI/Google key shapes),
`SECRET_ASSIGN`, `DESTRUCTIVE`, `PUBLISH`, `PERMISSION`, `FINANCIAL`, `EXTERNAL_FETCH`. Findings are capped by
`limits.max_findings_per_audit`. Secrets and identifiers are never downgraded; action verbs **are**
downgraded to `context_only` when the same line carries advisory language ("require approval before delete",
"run against a copy"), so the tool's own safety guidance does not block its own output. `--fail-on-high`
exits `3`; `audit --workbook` also reports hidden sheets, protected sheets, external links and defined names,
because hiding is not a security boundary.

### 6.6 `formulas`

```sh
python3 tools-spreadsheet-skill-library.py formulas --list
python3 tools-spreadsheet-skill-library.py formulas --pattern lookup --arg key=B2 --arg key_range=Ref!A:A
python3 tools-spreadsheet-skill-library.py formulas --pattern query_summary --arg 'where=A is not null'
python3 tools-spreadsheet-skill-library.py formulas --validate-formula '=SUMIFS(Data!H2:H9,Data!B2:B9,"East")'
```

Patterns: `lookup`, `conditional_sum`, `dynamic_filter`, `query_summary`, `array_row`, `error_guard`,
`date_math`, `text_clean`, `percent_of_total`, `regex_extract` (built-in) plus `grade_band` and
`duplicate_flag` from `asset-formula-patterns.json`.

Validation checks: leading `=`, balanced parentheses and quotes, empty arguments, mixed `,`/`;` separators
(locale risk), `VLOOKUP` without an exact-match flag, and single-criteria functions where a multi-criteria
form is safer. Unknown patterns or arguments are refused with the list of valid names.

### 6.7 `validate-workbook`

```sh
python3 tools-spreadsheet-skill-library.py validate-workbook --workbook runs/q4-hardening/activation-review.xlsx
```

Reports per sheet: dimensions, row/column counts, formula cells, freeze panes, data-validation count,
protection and visibility, plus defined names and external links. `.xls` files are refused with an
instruction to re-save as `.xlsx` (the tool never claims to read a format it cannot read).

### 6.8 `check`

```sh
python3 tools-spreadsheet-skill-library.py check
```

Verifies: no `SKILL.md`, no subfolders, all filenames prefixed (`tools-`/`knowledge-`/`asset-`), exactly one
`knowledge-*.md`, a primary `tools-` entry point present, no nested archives, path depth ≤ 1 under the
component folder, config parses, catalog validates, capability-map coverage is complete, every JSON asset
parses, the output contract declares every error code the tool can raise, a formula-pattern count is reported,
the Markdown template, governance checklist and `.mjs` parser exist, **every component filename referenced in
this document resolves**, no absolute paths appear in this document, and the folder name matches
`^[a-z0-9]+(?:-[a-z0-9]+)*$`. Exit `2` if any check fails.

### 6.9 `self-test`

```sh
python3 tools-spreadsheet-skill-library.py self-test
```

Runs 22 assertions through the real code paths (catalog validation, coverage, HTML and Markdown extraction,
invalid input, selection resolution, invalid category, plan generation, approval lifecycle including expiry,
id-collision safety, plan rendering from the template/checklist assets, workbook write + audit, risk detection,
formula generation/validation, asset pattern loading, argument rejection, input-change sensitivity, config
validation, error envelope shape, corrupt-JSON recovery, cleanup registry, asset reference resolution) and exits
`4` if any fail. Latest run: **22/22 passed**.

---

## 7. Configuration reference (`tools-config.json`)

```json
{
  "paths":      { "catalog": "...", "policy": "...", "contract": "...", "formula_patterns": "...",
                  "worksheet_template": "...", "governance_checklist": "...",
                  "default_output_dir": "runs", "approval_store": "runs/approvals.json" },
  "limits":     { "max_input_bytes": 4000000, "max_skills_per_build": 280, "max_catalog_categories": 60,
                  "max_skills_per_category": 40, "max_findings_per_audit": 200, "workbook_row_cap": 5000 },
  "timeouts":   { "read_seconds": 30, "write_seconds": 30, "workbook_seconds": 120 },
  "retries":    { "file_write": 3, "backoff_seconds": 0.2 },
  "integrations": { "drive": true, "sheets_api": true, "forms": false, "apps_script": false,
                    "gmail": false, "calendar": false, "bigquery": false, "looker": false, "addons": false },
  "outputs":    { "write_workbook": true, "write_csv_fallback": true, "plan_json": "activation-plan.json",
                  "plan_markdown": "activation-plan.md", "workbook": "activation-review.xlsx",
                  "manifest": "bundle-manifest.json", "audit_report": "risk-audit.json",
                  "csv_fallback": "activation-plan.csv" },
  "capabilities": { "allow_derived_semantics": false, "default_risk": "medium" },
  "approvals":  { "ttl_hours": 24, "require_human_confirmation": true, "gate_scope": "*" }
}
```

Rules the validator enforces: sections must be objects; `max_input_bytes`, `max_skills_per_build` and
`workbook_row_cap` must be positive integers; `max_skills_per_build` ≤ 1000; every integration flag must be a
boolean; output names must be bare filenames (no directories). Violations raise `CONFIG_INVALID` with the
offending key.

Practical knobs: turn `integrations.forms|gmail|calendar|bigquery|looker|apps_script|addons` on **only** when
the host tenant really provides that access — the plan text changes from "manual steps" to "executable" for
those surfaces. Raise `limits.max_skills_per_build` above 280 only when you intend to build from a larger
extracted catalog.

---

## 8. Data contracts

* **Catalog** (`asset-skill-catalog.json`): `{category_count, skill_count, categories:[{number, name, slug,
  description, skills:[{id, number, name, slug, description, keywords}]}]}`. Loaded and validated on every run;
  declared counts must match actual counts.
* **Capability map** (`asset-capability-map.json`): `{version, surfaces_legend, capabilities:[{id, name, slug,
  risk, surfaces, workflow, inputs, validation, limits, fallbacks, google_apis}]}` as constrained by
  `asset-capability-input.schema.json`. Identity is `id + name + slug`; a capability whose name changed does not
  inherit old semantics.
* **Plan document**: `render_plan_markdown` fills `asset-plan-template.md` (`{{subject}}`, `{{generated}}`,
  `{{tool}}`, `{{version}}`, `{{summary_line}}`, `{{risk_table}}`, `{{gate_section}}`, `{{body}}`,
  `{{checklist}}`, `{{rules}}`) and injects `asset-governance-checklist.md` verbatim. Editing either asset
  changes every future plan; a template without `{{body}}` falls back to the built-in skeleton instead of
  producing a broken document.
* **Policy** (`asset-approval-policy.json`): `gates{gate:{approval_required, reason, evidence, blocks}}` and
  `triggers[{gate, keywords}]`. `approval_required` decides blocking; keyword triggers add gates when the
  catalogued name/description mentions the topic.
* **Surface codes**: `A` copilot_answer, `L` local_docs, `S` sheets_cells, `W` workbook_batch, `D` drive_file,
  `X` pdf_export, `C` csv_export, `H` chart, `V` pivot, `F` form, `G` gmail, `K` calendar, `B` bigquery,
  `R` looker, `P` apps_script, `N` addon.
* **Risk tiers**: `low` → no baseline gate; `medium` → `review_before_publish`; `high` → + `backup_before_change`
  + `approval_required`; `critical` → + `security_sign_off`.
* **Approval store**: `{version, updated_at, approvals:{"<capability-id>:<gate>":{approver, approved_at,
  expires_at, scope, note, capability}}}`.

---

## 9. Safety and approval rules

1. **Nothing high or critical runs unapproved.** Gates are derived from the risk tier and from the catalogued
   wording, then written to the plan, the workbook Gate Register and the manifest. `--fail-on-blocked` turns
   this into a machine-checkable exit code `3`.
2. **Approvals are human, named, scoped and expiring.** `--confirm-approval` plus `--approver` is mandatory;
   there is no "approve everything forever" switch. The previous store is backed up before replacement.
3. **Destructive work is copy-first.** `backup_before_change` is attached to every high/critical capability and
   to anything whose description mentions delete/drop/overwrite/wipe.
4. **Publishing is review-first.** Sharing, email, PDF/HTML export and link changes carry
   `review_before_publish` (and `publishing_action` when the wording says so).
5. **Disabled integrations are never simulated.** If `forms`, `gmail`, `bigquery`, `looker`, `apps_script` or
   `addons` is off, the plan says so and gives manual steps; it never produces fake automation.
6. **No secrets, no network, no arbitrary execution.** The tool makes no network calls, evaluates no
   user-supplied code and ships no credentials. It cannot be used to execute Apps Script, call APIs or run shell
   commands; `audit` flags credential-like strings rather than using them.
7. **Privacy first.** `audit` detects e-mail addresses, phone-like identifiers, 12-digit ID-like strings and
   credential shapes before an artefact leaves the workbook; retention and de-identification capabilities are
   `high`/`critical` so they require sign-off.
8. **Data stays local.** All reads/writes are inside `--out` and the component folder; paths in this document and
   in code are relative to the future skill root.

---

## 10. Limits and unavailable integrations

* **No live Google Sheets/Drive/Forms/Gmail/Calendar/BigQuery/Looker/Apps Script calls.** Every such surface is
  described and gated, then handed to a human or to an integration the host skill must supply. The
  `integrations` flags describe what the surrounding tenant *may* do; this tool itself never calls them.
* **PDF rendering is not local.** Print/PDF capabilities produce layout specifications and checks, not rendered
  PDFs (no headless browser or LibreOffice is bundled or required).
* **Charts and pivot tables are specified, not rendered.** The workbook writer does not synthesise chart or
  pivot objects; it writes the specification, source-range rules and verification steps.
* **Workbook volume** is capped by `limits.workbook_row_cap` (5,000 default) and input by
  `limits.max_input_bytes` (4 MB). Very large `.xlsx` files should be narrowed before auditing.
* **`.xls`, Google Sheets native URLs and password-protected workbooks are out of scope**; re-save as `.xlsx`
  and export to a local file first.
* **Catalog scope.** The shipped catalog covers the 280 catalogued capabilities. A different source list can be
  used via `extract` + `--catalog`; without `--allow-derived` its semantics must be added to
  `asset-capability-map.json` first.
* **Audit is heuristic.** Regex rules catch common shapes, not every private detail; it supplements, never
  replaces, human review.
* **No LibreOffice/Excel render check** was possible here (not installed in the build environment); workbook
  content was verified by re-opening with `openpyxl` and by `validate-workbook`, not by visual rendering.
* **The capability map is a curated baseline.** Risk tiers, surfaces and workflow steps were authored per
  capability (163 low / 69 medium / 39 high / 9 critical) from the catalogued names and descriptions; treat them
  as reviewable starting points, and tune `asset-approval-policy.json` to the organisation's own policy.

---

## 11. Validation and tests actually completed

Environment: Python 3.13.16, openpyxl 3.1.5, Node 20.20.2, Linux.

1. `--help` for both entry points; `capabilities` for both; `catalog` text and JSON forms.
2. `self-test` → **22/22 checks passed** (list in §6.9), including approval expiry, id-collision safety,
   template rendering, corrupt-JSON recovery and cleanup.
3. `check` → all structural/reference checks pass once this document is present (no `SKILL.md`, no subfolders,
   prefixes correct, one knowledge file, references resolve, no absolute paths).
4. `catalog --category 25` (10 capabilities) and `catalog --search "pivot total"` (1 hit) returned the expected
   rows.
5. `build --category 13,25 --out /tmp/e2e` → 20 capabilities, 48 gate rows, 38 pending; plan JSON/MD, workbook
   (12,388 B), manifest, audit and CSV all written and re-opened.
6. `build --all --out /tmp/all280` → 280 capabilities, workbook with 281 plan rows / 351 gate rows (49,819 B),
   manifest `required 226, pending 226, advisory 124`, plan JSON 1.13 MB; completion ≈ 0.85 s.
7. Approval lifecycle: `approve` without `--confirm-approval` wrote nothing and printed `pending`;
   `--confirm-approval` recorded 2 approvals with a 24 h expiry, created `approvals.json`, and a rebuild reported
   `2 approved / 36 pending`.
8. Error paths (all verified with exact exit codes): missing input file, invalid catalog JSON, unknown skill id,
   empty selection, unknown formula pattern, non-`.xlsx` workbook, existing workbook with
   `--no-workbook-overwrite`, missing audit input, `--fail-on-blocked` (exit 3), JSON error envelope, unknown
   subcommand.
9. `audit`: the generated plan is clean (`blocking: no`; one contextual `DESTRUCTIVE` note); a synthetic file
   containing an e-mail, a key-shaped token, `password=...` and "delete all rows" produced 4 findings and exit 3
   with `--fail-on-high`.
10. `formulas`: generated lookups and site-specific overrides; `--validate-formula` correctly flagged
    `=SUM(A1:A2` as unbalanced; unknown pattern/argument rejected; changing `criteria1` changed the formula.
11. `extract`: pasted HTML with `<style>`, classes, `data-*` attributes, `&amp;`, `<p>` descriptions and
    unnumbered `Skill Name:` labels produced the correct 2 categories / 5 capabilities with stable ids
    (`7.1`, `8.3`, …); Markdown numbering produced the same shape in `self-test`.
12. Cross-source safety: a foreign catalog re-using ids `1.x` produced `CAPABILITY_DOC_MISSING_ENTRY` without
    `--allow-derived`, and `semantics: derived` with it — no shipped semantics leaked.
13. Node parser: `extract` and `verify` on the same HTML; `verify` reported "28 categories / 280 capabilities".
14. Cancellation: `SIGINT` during a build printed `Cancelled.` and exited `130`.
15. Portability: the tool was started from a different working directory via a relative path and still
    resolved its assets.
16. The shipped catalog was regenerated from the source PDF text and asserted at 28 categories × 10
    capabilities = 280, every description non-empty; the capability map was asserted to have 280 unique
    id+name+slug keys with distribution 163 low / 69 medium / 39 high / 9 critical.
17. Fuzzing: 400 random/malformed inputs through `extract_catalog` produced 0 crashes (347 clean
    `ToolError`s, 36 valid catalogs, 17 accepted-but-invalid which callers reject via `validate_catalog`);
    300 random formulas through `validate_formula` and 200 random risk texts through `audit_text` produced no
    exceptions; 60 malformed HTML files through the Node parser produced no unexpected exit codes.
18. Packaging: the folder was copied into a clean root beside a single test `SKILL.md` and re-run there with
    relative paths (`capabilities`, `check`, `build`, `self-test` 22/22); the ZIP was then extracted into a fresh
    directory, re-checked (`check` 0 errors, Node `verify` 28/280) and a real build was produced from the
    extracted files.

---

## 12. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `CONFIG_INVALID: limits.max_input_bytes must be a positive integer` | Edited config with a string | Use a number; run `check` after editing. |
| `CATALOG_INVALID ... declared skill_count X != actual Y` | Hand-edited catalog | Re-run `extract`, or fix the counts and re-run `check`. |
| `CAPABILITY_DOC_MISSING_ENTRY` | Capability from another source list | Add it to `asset-capability-map.json`, or pass `--allow-derived`. |
| `APPROVAL_PENDING` (exit 3) | Gates awaiting sign-off | Review the plan, then `approve ... --confirm-approval`; rebuild. |
| Approvals vanished | `--reset-approvals` used, TTL expired, or a new `--out` | Re-approve; `approvals.backup.json` holds the previous store. |
| `OUTPUT_EXISTS` | `--no-workbook-overwrite` with an existing workbook | Delete the file or use a fresh `--out`. |
| `WORKBOOK_WRITE_FAILED` | Workbook open in Excel, or disk full | Close the file, free space; retries already attempted 3×. |
| `OPENPYXL_MISSING` | openpyxl not installed | `python3 -m pip install "openpyxl==3.1.5"`, or use the CSV/Markdown artefacts. |
| `AUDIT_BLOCKING` (exit 3) | Real high/critical finding | Fix the item; document exceptions and re-run without `--fail-on-high`. |
| `INPUT_TOO_LARGE` | Source bigger than 4 MB | Raise `limits.max_input_bytes` or split the list. |
| `check` fails on knowledge references | A referenced file was renamed | Update this document and the filename together; references are case-sensitive. |
| Corrupt `activation-plan.json` | Interrupted write | Writes are atomic (temp + `os.replace`); regenerate with `plan`/`build`. `JSON_INVALID` includes line/column. |

Recovery principles: outputs are rewritten in one atomic step, so a partial file never survives; the approval
store is backed up before replacement; every error names the next action; `--debug` prints a traceback without
changing behaviour.

---

## 13. Extension and maintenance

* **Add a capability to the shipped catalog:** add it to `asset-skill-catalog.json` (id, name, slug,
  description) *and* add the matching id+name+slug entry to `asset-capability-map.json`, then run `check` and
  `self-test`. Keep `skill_count`/`category_count` consistent.
* **Change the plan document or the handoff checklist:** edit `asset-plan-template.md` (keep `{{body}}`) or
  `asset-governance-checklist.md`; both are read on every `plan`/`build`.
* **Add a gate or change what needs approval:** edit `asset-approval-policy.json` only (`gates` for definitions,
  `triggers` for wording that should add a gate). No code change is needed; the plan, workbook and manifest pick
  it up on the next run.
* **Add formula patterns:** edit `asset-formula-patterns.json` (`template`, `args`, `required`, `notes`) and run
  `formulas --list`; asset patterns override built-ins of the same name.
* **Change output names or limits:** edit `tools-config.json`. Output names must stay bare filenames.
* **Use a different source list:** `extract` it, then `build --catalog <file> --allow-derived` for a first pass,
  and promote the semantics you want to keep into `asset-capability-map.json`.
* **Maintain the package shape:** keep this folder flat, keep exactly one `knowledge-*.md`, keep the
  `tools-`/`asset-` prefixes, keep all references relative, and run `check` before delivery. The parent package
  must contain exactly one `SKILL.md` at its root; this component never provides one.
* **Versioning:** `VERSION` in `tools-spreadsheet-skill-library.py` and `tools-catalog-parser.mjs` should move
  together with any schema change; `asset-capability-map.json` and `asset-formula-patterns.json` carry their own
  `version` fields for the same reason.
