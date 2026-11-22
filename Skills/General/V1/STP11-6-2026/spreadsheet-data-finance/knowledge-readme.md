# spreadsheet-data-finance — Knowledge and Operating Guide

This is the single knowledge file for the `spreadsheet-data-finance` Skill Tool. It documents
purpose, every capability, exact commands, inputs and outputs, configuration, safety rules, the
tests that were actually executed, and the limits of this build.

The tool was built from **Spreadsheet, Data & Finance Skill Catalog — 340 proposed skills across 34
categories**. All 340 entries are unified into one Skill Tool folder with **105 runnable commands**
behind one primary entry point.

---

## 1. Purpose

Give an agent or operator one dependable, local, open-source-only runtime for spreadsheet, data and
finance work:

* open and inspect any practical tabular/Workbook source without silently changing meaning,
* build and edit `.xlsx` workbooks non-destructively with real formulas, validation, protection,
  printing and navigation,
* author, trace, explain and sanity-check formulas without executing workbook code,
* clean, join, reshape, aggregate and profile data with a full change log,
* run statistics, experiments, forecasting, segmentation and transparent scoring,
* produce ledger checks, statements, valuation, treasury, AR/AP and tax-support arithmetic,
* render honest charts (dependency-free SVG) and editable dashboards,
* scan for privacy risk, redact, protect, classify risk and gate releases,
* run all of the above as one validated batch with dry runs, backups and an audit trail.

Two rules shape the whole design:

1. **Nothing consequential happens without an explicit approval flag.** Mutating, irreversible,
   privacy-affecting and release-affecting commands refuse to run without `--approve`.
2. **Every number is traceable.** Calculations return the policy or sign convention they assumed;
   data operations return lineage steps and change logs; statements return control totals and
   say when they do not balance.

---

## 2. What is inside the folder

| File | Kind | Role |
|---|---|---|
| `tools-sdf.py` | tools (primary entry point) | CLI, command dispatch, structured envelopes, approval gate, audit trail |
| `tools-sdf-common.py` | tools (shared runtime) | errors, envelopes, path/limit validation, atomic writes, table helpers, locale parsing, statistics primitives, financial math, audit |
| `tools-sdf-intake.py` | tools (subsystem) | readers (csv/tsv/json/jsonl/xml/html/xlsx), dialect + encoding inference, structure map, triage, links, hidden content, comparison, manifest, legacy conversion, catalog parsing |
| `tools-sdf-formula.py` | tools (subsystem) | formula tokenizer/parser/evaluator, dependency graph, explainer, workbook formula scanning, hardcode + pattern audits |
| `tools-sdf-data.py` | tools (subsystem) | profile, clean, dedupe, join, pivot/unpivot, aggregate, window, resample, sample, crosswalk, allocate, outliers, type enforcement |
| `tools-sdf-stats.py` | tools (subsystem) | descriptive statistics, correlation, OLS, tests, A/B analysis, sample size, control charts, Monte Carlo, forecasts, RFM, cohorts, basket analysis, scoring, assumption audit |
| `tools-sdf-finance.py` | tools (subsystem) | chart of accounts, journals, trial balance, GL summary, statements, variance, drivers, DCF, loans, leases, depreciation, aging, duplicate invoices, PO matching, VAT, bank reconciliation, portfolio/risk metrics, debt schedules, ratios, KPI evaluation, revenue recognition |
| `tools-sdf-workbook.py` | tools (subsystem) | workbook builder, cell/range editing, bulk transforms, layout, conditional formats, validations, protection, print setup, navigation, sorting, merge audit |
| `tools-sdf-viz.py` | tools (subsystem) | SVG chart renderer, chart selection advisor, visualization integrity audit, Excel dashboard builder |
| `tools-sdf-qa.py` | tools (subsystem) | privacy scan, redaction, metadata scrub, export gate, risk classification, quality gate, reconcile, regression comparison, review pack |
| `knowledge-readme.md` | knowledge | this file |
| `asset-capabilities.json` | asset | the 340 catalog capabilities mapped to commands, with status and status notes |
| `asset-workbook-spec.schema.json` | asset | JSON Schema for `build` specifications, with an example |
| `asset-formula-library.json` | asset | verified formula suite (executed by `selftest`) plus authoring patterns and engine limits |
| `asset-finance-policy.json` | asset | accounting policy presets (accrual, IFRS 16, ASC 842, PH, cash basis), journal rules, review triggers |
| `asset-style-presets.json` | asset | style vocabulary and three layout/theme presets for workbooks |
| `asset-chart-palettes.json` | asset | colour-blind-safe and grayscale palettes, theme tokens, charting rules |
| `asset-sample-ledger.csv` | asset (fixture) | 26-line balanced general ledger with 13 journals |
| `asset-sample-coa.csv` | asset (fixture) | 9-account chart of accounts |
| `asset-sample-sales.csv` | asset (fixture) | 26-row sales dataset used in examples |
| `asset-sample-ar-aging.csv` | asset (fixture) | 8 open/settled AR invoices for aging examples |

Load/execution order: `tools-sdf.py` is started first. It loads `tools-sdf-common.py`, then loads
each `tools-sdf-*.py` subsystem on first use (each subsystem loads `common`, and `data`/`stats`/
`finance`/`qa` load their own dependencies the same way). No subsystem needs to be started
manually, and none of them writes files unless the command you ran was designed to.

Total: **21 files** (10 `tools-*`, 1 `knowledge-*`, 10 `asset-*`), no subfolders, no `SKILL.md`.

---

## 3. Setup

Requires **Python 3.10+** (`python3`) and these packages:

```
python -m pip install "openpyxl>=3.1,<4"
```

Optional, only for the specific features noted:

```
python -m pip install "xlrd>=2.0"     # legacy .xls -> .xlsx conversion (convert)
```

Everything else is the Python standard library: `csv`, `json`, `zipfile`, `xml.etree`, `html.parser`,
`argparse`, `hashlib`, `decimal`, `math`, `random`, `datetime`, `difflib`. Charts are hand-written
SVG, so no plotting library is needed. There is no network access, no database driver and no
credential handling anywhere in the code.

Check the runtime before doing real work:

```
python3 spreadsheet-data-finance/tools-sdf.py --version
python3 spreadsheet-data-finance/tools-sdf.py --help
python3 spreadsheet-data-finance/tools-sdf.py selftest
```

`selftest` runs the packaged verification suite (13 formula cases + numeric invariants) and returns
`{"verdict": "pass"}`. It exited `pass` (23/23 checks) in this build.

---

## 4. Invocation model

```
python3 spreadsheet-data-finance/tools-sdf.py <command> [command options] [common options]
```

* **Primary entry point:** `tools-sdf.py`. Use `python3` explicitly (or `py -3` on Windows).
* **Structured output** is JSON by default: `status`, `operation`, `tool`, `version`, `data`,
  `warnings`, `errors`, `meta`. `--output-format text` prints a compact human summary instead.
* **Exit codes:** `0` ok, `2` validation/usage error (typed, with a code and hint), `3` unexpected
  internal error, `130` cancelled.
* **Common options on every command:** `--output-format {json|text}`, `--save-result PATH`,
  `--approve`, `--audit-log PATH`, `--export {csv,json,md}`, `--export-stem NAME`, `--out-dir DIR`,
  `--preview N`, `--overwrite`. These work before or after the command name.
* **Table outputs** (cleaned data, joins, pivots, schedules, aging, cohorts, and so on) are written
  when you pass `--export`: `--export csv,md --out-dir out`. They are always *also* present as a
  preview inside the JSON result.
* **Path resolution:** every `--input`/`--output`/`--spec` argument is resolved relative to the
  **current working directory** (or as absolute). Paths written *inside* a JSON spec (for example
  `data[].path` in a `dashboard` spec) follow the same rule. If you run the tool from a directory
  other than the one holding your data, use `spreadsheet-data-finance/asset-sample-sales.csv`-style
  relative or absolute paths.
* **Every call appends a line** to an audit log (`./sdf-audit.jsonl`, or `--audit-log` /
  `SDF_AUDIT_LOG`). Auditing failure never breaks an operation.

Run against the packaged fixtures (from the folder that contains the Skill Tool):

```
python3 spreadsheet-data-finance/tools-sdf.py journal-check --input spreadsheet-data-finance/asset-sample-ledger.csv --output-format text
python3 spreadsheet-data-finance/tools-sdf.py trial-balance --input spreadsheet-data-finance/asset-sample-ledger.csv --coa spreadsheet-data-finance/asset-sample-coa.csv --as-of 2025-03-31
python3 spreadsheet-data-finance/tools-sdf.py chart --input spreadsheet-data-finance/asset-sample-sales.csv --kind column --x region --y units --title "Units by region" --output chart.svg
```

---

## 5. Capability coverage

`python3 tools-sdf.py capabilities --full` returns the machine-readable map. Every one of the 340
catalog capabilities is present with one of three honest statuses:

* **`implemented`** — the named command performs the work natively in this build.
* **`assisted`** — the named command performs the deterministic part (arithmetic, structure,
  validation, preparation); the remaining judgement or platform step is documented in that entry's
  `note`.
* **`external`** — needs a platform, credential or runtime this tool does not ship (VBA, Office
  Scripts, Power Query/DAX data model, network/database/mailbox/PDF-extraction connectors,
  geocoding, native pivot tables/slicers, Excel's recalculation engine, secure deletion and
  retention enforcement). The named command still plans or prepares the work.

Current tally across the 340 entries: 138 `implemented`, 183 `assisted`, 19 `external`.

Look up any category or capability:

```
python3 spreadsheet-data-finance/tools-sdf.py capabilities --category "Formula Engineering and Calculation Logic"
python3 spreadsheet-data-finance/tools-sdf.py capabilities --command-name vat
```

### 5.1 Category-by-category summary

| # | Category | How it is covered |
|---|---|---|
| 1 | Universal Spreadsheet Intake and Compatibility | `read`, `dialect`, `encoding`, `structure`, `triage`, `links`, `hidden`, `compare`, `manifest`, `convert`, `flatten` |
| 2 | Workbook Creation and Layout Design | `build` (spec-driven sheets, styles, tables, validation, navigation, print), `dashboard`, `layout`, `nav`, `print-setup`, `qa-gate` accessibility checks |
| 3 | Cell Editing, Formatting, and Precision | `set-cells`, `bulk` (dry-run + apply), `number-format`, `cond-format`, `merge-audit`, `sort` (refuses formula/merge ranges), `deps` impact tracing |
| 4 | Formula Engineering and Calculation Logic | `eval`, `explain`, `deps`, `scan`, `hardcode` (includes inconsistent-pattern detection) |
| 5 | Tables, Named Ranges, and Structured Models | `build` (Excel tables + named ranges), `profile`, `compare`, `dedupe`, `window` |
| 6 | Data Cleaning and Preparation | `profile`, `clean`, `dedupe`, `outliers`, `crosswalk`, `enforce`, `reconcile` |
| 7 | Data Validation, Forms, and Controlled Entry | `validations`, `build`, `protect`, `review-pack`, `join` (form consolidation) |
| 8 | Data Integration, Import, and Export | `manifest`, `read`, `flatten`, `clean`, `--export`, `plan`/`task-plan` for connector work |
| 9 | Joins, Reshaping, and Data Transformation | `join`, `pivot`, `unpivot`, `aggregate`, `window`, `crosswalk`, `allocate`, `resample`, `sample` |
| 10 | Pivot Tables, Cubes, and Interactive Analysis | `pivot` (crosstab), `chart`, `dashboard`, `deps` drill paths; data-model/DAX/CUBE are `external` |
| 11 | Charts, Graphs, and Data Visualization | `chart` (9 chart forms), `chart-advice`, `viz-audit`, `cond-format` heatmaps |
| 12 | Dashboard and KPI Systems | `dashboard` (KPI cards + native charts), `kpi`, `reconcile`, `build` scorecards |
| 13 | Accounting and General Ledger | `coa`, `journal-check`, `gl-summary`, `trial-balance`, `loan`, `depreciation`, `lease`, `bank-recon`, `review-pack` |
| 14 | Financial Statements and Reporting | `income-statement`, `balance-sheet`, `cash-flow`, `reconcile`, `narrate`, `build` support schedules |
| 15 | Budgeting, Forecasting, and Planning | `drivers`, `forecast`, `variance`, `build`, `join` (submission consolidation) |
| 16 | Financial Modeling and Valuation | `dcf`, `drivers`, `ratios`, `loan`, `scan` (circularity/error detection), `build` linking structure |
| 17 | Treasury, Cash, and Banking | `bank-recon`, `debt-schedule`, `dashboard`, `review-pack`, `aggregate` fee analysis |
| 18 | Accounts Receivable, Payable, and Billing | `aging`, `dup-invoices`, `po-match`, `revenue-recognition`, `scorecard` (collection ranking), `dashboard` |
| 19 | Tax, Compliance, and Regulatory Support | `vat-recon`, `crosswalk` (return mapping), `reconcile`, `build` trackers, `manifest` evidence packs |
| 20 | Investment, Portfolio, and Market Analysis | `portfolio`, `risk-metrics`, `build` position registers, `enforce` identifier checks |
| 21 | Sales, CRM, and Revenue Operations | `build` pipeline/tracker structure, `forecast`, `variance`, `aggregate`, `allocate`, `chart` waterfall, `profile` hygiene |
| 22 | Operations, Inventory, and Supply Chain | `drivers` (reorder/purchase plans), `forecast`, `scorecard`, `sample` (cycle count), `montecarlo`, `dashboard` |
| 23 | Project, Resource, and Work Management | `build` WBS/Gantt/registers, `allocate` capacity, `variance`, `scorecard` prioritisation, `reconcile` |
| 24 | Human Resources and Workforce Planning | `build` registers/matrices, `reconcile` headcount, `drivers` workforce models, `privacy-scan` |
| 25 | Statistical Analysis and Experimentation | `describe`, `correlate`, `corrmatrix`, `regress`, `ttest`, `proportions`, `chisquare`, `abtest`, `samplesize`, `control-chart`, `montecarlo`, `assumptions` |
| 26 | Data Mining, Segmentation, and Predictive Models | `rfm`, `cohort`, `basket`, `outliers`, `scorecard`, `forecast`, `accuracy`, `window` (feature lags), `regress` drift checks |
| 27 | Automation, Macros, Scripts, and Office Integration | `batch` (validated pipeline, dry run, backups, resume), `regression`, `structure` (reports VBA presence); VBA/Office Scripts/Power Automate/R are `external` |
| 28 | Collaboration, Review, and Workflow Control | `review-pack`, `compare` (conflict review), `export-gate`, `qa-gate`, `build` trackers, `set-cells` evidence links |
| 29 | Security, Privacy, and Governance | `privacy-scan`, `redact`, `scrub`, `protect`, `export-gate`, `risk-classify`, `review-pack` |
| 30 | Quality Assurance, Audit, and Error Prevention | `qa-gate`, `scan`, `hardcode`, `reconcile`, `regression`, `enforce` boundary reporting, `eval` recalculation |
| 31 | Templates for Personal, Academic, and Small-Business Use | `build`-driven structures, `loan` (debt payoff), `drivers` (savings goals), `assist-build` scaffolds |
| 32 | Spatial, Scientific, Engineering, and Specialized Sheets | `build` unit-aware sheets, `enforce` unit/precision typing, `outliers`, `control-chart`; coordinate conversion is `assisted` (validation only, no geocoding) |
| 33 | AI-Assisted Spreadsheet Intelligence | `assist-build` (plain language → reviewable workbook), `task-plan`, `explain`, `narrate`, `eval`, `triage`, `qa-gate` |
| 34 | End-to-End Spreadsheet Power Tools | `task-plan` decompositions, `batch` runs, `assist-build`, `risk-classify`, `qa-gate` + `export-gate` delivery checks |

---

## 6. Command reference

Grouped by the catalog category the command primarily serves. Each command is listed once, under the first catalog category that uses it. Options in bold are required. Every command also accepts the common options `--output-format`, `--save-result`, `--approve`, `--audit-log`, `--export`, `--export-stem`, `--out-dir`, `--preview`, `--overwrite`.


#### Category 1: Universal Spreadsheet Intake and Compatibility

- `read` - Read any supported source into a normalised table.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS

- `structure` - Inventory sheets, formulas, names, objects, links and protection.  
  Options: **--input INPUT**

- `convert` - Convert legacy/plain formats into .xlsx (approval-gated write).  
  Options: **--input INPUT**, **--output OUTPUT**

- `dialect` - Infer delimiter, quoting, header and encoding before importing.  
  Options: **--input INPUT**

- `encoding` - Report encoding, BOM, line endings and decode warnings.  
  Options: **--input INPUT**

- `triage` - Diagnose a damaged workbook and produce a repair plan.  
  Options: **--input INPUT**

- `links` - Audit external links, URLs and connected sources.  
  Options: **--input INPUT**

- `hidden` - Reveal hidden sheets, rows, columns, names and comments.  
  Options: **--input INPUT**

- `compare` - Compare two workbook versions at cell/formula/format level.  
  Options: **--baseline BASELINE**, **--candidate CANDIDATE**, --limit LIMIT

- `manifest` - Build a reproducible import manifest with hashes and parse rules.  
  Options: **--inputs INPUTS ...**, --no-hash


#### Category 2: Workbook Creation and Layout Design

- `build` - Build a workbook from a JSON specification.  
  Options: **--spec SPEC**, **--output OUTPUT**, --theme THEME

- `dashboard` - Build an Excel dashboard with KPI cards and native charts.  
  Options: **--spec SPEC**, **--output OUTPUT**

- `layout` - Set widths, heights, freeze, wrapping, grouping and hidden rows.  
  Options: **--input INPUT**, **--spec SPEC**, --output OUTPUT

- `nav` - Build an Index sheet with hyperlinks and return links.  
  Options: **--input INPUT**, --output OUTPUT

- `print-setup` - Configure page size, orientation, scaling and repeating headers.  
  Options: **--input INPUT**, --spec SPEC, --output OUTPUT

- `qa-gate` - Run the pre-delivery quality gate across structure/formula/a11y/security.  
  Options: **--input INPUT**, --spec SPEC


#### Category 3: Cell Editing, Formatting, and Precision

- `set-cells` - Edit specific cells, formulas, formats and comments.  
  Options: **--input INPUT**, **--edits EDITS**, --output OUTPUT

- `bulk` - Apply auditable rules across ranges/columns with a dry-run preview.  
  Options: **--input INPUT**, **--rules RULES**, --output OUTPUT, --apply

- `number-format` - Design a number-format pattern and optionally apply it.  
  Options: --currency CURRENCY, --decimals DECIMALS, --parens, --zero {dash|zero|full}, --pattern PATTERN, --apply-to APPLY_TO, --sheets SHEETS, --columns COLUMNS, --output OUTPUT, --apply

- `cond-format` - Add priority-aware conditional formatting rules.  
  Options: **--input INPUT**, **--rules RULES**, --output OUTPUT

- `merge-audit` - Report merged regions and safe remediation options.  
  Options: **--input INPUT**

- `sort` - Sort a range while refusing formula/merge-bearing ranges (approval-gated).  
  Options: **--input INPUT**, **--sheet SHEET**, **--range RANGE**, **--keys KEYS**, --descending, --no-header, --output OUTPUT

- `deps` - List precedent cells and, for a workbook, dependent formulas.  
  Options: **--formula FORMULA**, --input INPUT, --names NAMES


#### Category 4: Formula Engineering and Calculation Logic

- `eval` - Evaluate a formula in a sandboxed engine.  
  Options: **--formula FORMULA**, --input INPUT, --sheet SHEET, --cells CELLS, --names NAMES

- `explain` - Explain a formula step by step with risk notes.  
  Options: **--formula FORMULA**, --names NAMES

- `hardcode` - Detect constants inside formula columns and inconsistent patterns.  
  Options: **--input INPUT**, --limit LIMIT

- `scan` - Scan a workbook for formula errors, externals, volatility and names.  
  Options: **--input INPUT**, --limit LIMIT


#### Category 5: Tables, Named Ranges, and Structured Models

- `profile` - Profile columns: types, missingness, distribution and keys.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --top TOP, --samples SAMPLES

- `window` - Add running totals, ranks, moving averages, lag/lead and shares.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--column COLUMN**, --functions FUNCTIONS, --functions-csv FUNCTIONS_CSV, --order-by ORDER_BY, --date-column DATE_COLUMN, --partition-by PARTITION_BY

- `dedupe` - Find exact and fuzzy duplicates with survivorship proposals.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --keys KEYS, --fuzzy FUZZY


#### Category 6: Data Cleaning and Preparation

- `clean` - Clean and coerce values with a full change log.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --options OPTIONS, --type TYPE, --fill FILL, --dedupe, --dedupe-keys DEDUPE_KEYS

- `outliers` - Flag statistical/rule outliers for review (never deletes).  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --columns COLUMNS, --method {iqr|zscore|mad}, --threshold THRESHOLD, --sd SD

- `crosswalk` - Apply a controlled mapping with an exceptions queue.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--column COLUMN**, **--mapping MAPPING**, --target-name TARGET_NAME, --strict

- `enforce` - Coerce columns to declared types and collect exceptions.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--schema SCHEMA**

- `reconcile` - Tie out named values with tolerances.  
  Options: **--pairs PAIRS**, --tolerance TOLERANCE


#### Category 7: Data Validation, Forms, and Controlled Entry

- `validations` - Add data validation rules with prompts and error messages.  
  Options: **--input INPUT**, **--rules RULES**, --output OUTPUT

- `review-pack` - Generate a review/approval checklist artefact.  
  Options: **--spec SPEC**

- `protect` - Protect sheets/structure and unlock controlled input ranges (approval-gated).  
  Options: **--input INPUT**, --spec SPEC, --output OUTPUT

- `join` - Join two tables with cardinality and unmatched diagnostics.  
  Options: **--input INPUT**, **--right RIGHT**, **--left-keys LEFT_KEYS**, --right-keys RIGHT_KEYS, --how {inner|left|right|full|anti|semi}, --sheet SHEET, --delimiter DELIMITER


#### Category 8: Data Integration, Import, and Export

- `plan` - Map a request or catalog to an executable stage/command plan.  
  Options: --input INPUT, --text TEXT, --catalog

- `flatten` - Flatten nested JSON/XML into relational rows.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS


#### Category 9: Joins, Reshaping, and Data Transformation

- `pivot` - Pivot rows into a cross-tab with a chosen aggregation.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--index INDEX**, --columns COLUMNS, --values VALUES, --agg AGG

- `unpivot` - Reshape wide data into long form preserving keys.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--id-columns ID_COLUMNS**, --value-columns VALUE_COLUMNS, --var-name VAR_NAME, --value-name VALUE_NAME

- `aggregate` - Group and aggregate with multiple measures.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--group-by GROUP_BY**, **--measures MEASURES**

- `allocate` - Allocate amounts by driver weights with exact remainders.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--amount-column AMOUNT_COLUMN**, **--drivers DRIVERS**, --group-by GROUP_BY, --rounding ROUNDING

- `resample` - Aggregate event data to day/week/month/quarter/year periods.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--date-column DATE_COLUMN**, **--value-column VALUE_COLUMN**, --freq {day|week|month|quarter|year}, --agg AGG

- `sample` - Draw reproducible random/systematic/stratified/review samples.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --size SIZE, --frac FRAC, --method {random|systematic|stratified|review}, --stratify-by STRATIFY_BY, --review-column REVIEW_COLUMN, --seed SEED


#### Category 10: Pivot Tables, Cubes, and Interactive Analysis

- `chart` - Render a publication-ready SVG chart from tabular data.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --kind KIND, --x X, --y Y, --title TITLE, --subtitle SUBTITLE, --x-label X_LABEL, --y-label Y_LABEL, --footnote FOOTNOTE, --width WIDTH, --height HEIGHT, --palette PALETTE, --palette-name PALETTE_NAME, --no-values, **--output OUTPUT**

- `drivers` - Driver-based multi-period projection.  
  Options: **--drivers DRIVERS**, **--base-values BASE_VALUES**, --periods PERIODS


#### Category 11: Charts, Graphs, and Data Visualization

- `chart-advice` - Recommend a chart form for a stated analytical intent.  
  Options: **--intent INTENT**, --categories CATEGORIES, --series SERIES, --has-time

- `viz-audit` - Audit a chart/visual spec for misleading or inaccessible design.  
  Options: **--spec SPEC**


#### Category 12: Dashboard and KPI Systems

- `kpi` - Evaluate a KPI against target, tolerance and direction.  
  Options: **--actual ACTUAL**, **--target TARGET**, --tolerance TOLERANCE, --direction {higher_is_better|lower_is_better}, --name NAME


#### Category 13: Accounting and General Ledger

- `coa` - Validate a chart of accounts (types, hierarchy, duplicates).  
  Options: --input INPUT, --accounts ACCOUNTS

- `journal-check` - Validate double-entry balance per journal reference.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --tolerance TOLERANCE

- `gl-summary` - Summarise ledger detail by dimension and period.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --group-by GROUP_BY, --coa COA, --sign-by-type

- `trial-balance` - Build a trial balance with imbalance and abnormal-balance checks.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --coa COA, --accounts ACCOUNTS, --as-of AS_OF, --opening OPENING

- `loan` - Amortisation schedule for a loan with interest and principal split.  
  Options: **--principal PRINCIPAL**, **--rate RATE**, **--periods PERIODS**, --frequency FREQUENCY, --payment PAYMENT, --extra EXTRA, --start-date START_DATE

- `depreciation` - Depreciation schedule (straight line/declining/sum-of-years).  
  Options: **--cost COST**, --salvage SALVAGE, **--life LIFE**, --method {straight_line|double_declining|sum_of_years|declining_balance}, --frequency FREQUENCY, --declining-rate DECLINING_RATE

- `lease` - Lease liability amortisation from supplied payments.  
  Options: **--payments PAYMENTS**, **--rate RATE**, --liability LIABILITY, --start-date START_DATE, --periodicity PERIODICITY

- `bank-recon` - Match bank and ledger lines with an open-item list.  
  Options: **--input INPUT**, **--ledger LEDGER**, --amount-column AMOUNT_COLUMN, --date-column DATE_COLUMN, --ref-column REF_COLUMN, --window WINDOW


#### Category 14: Financial Statements and Reporting

- `income-statement` - Map a trial balance into an income statement.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--mapping MAPPING**

- `balance-sheet` - Map a trial balance into a balance sheet with a balance check.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--mapping MAPPING**

- `cash-flow` - Indirect-method cash flow with reconciliation check.  
  Options: **--inputs INPUTS**

- `narrate` - Grounded narrative findings from a table.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --measure MEASURE, --order-column ORDER_COLUMN


#### Category 15: Budgeting, Forecasting, and Planning

- `forecast` - Naive/linear/Holt/drift forecasts with in-sample accuracy.  
  Options: --input INPUT, --column COLUMN, --sheet SHEET, --values VALUES, --periods PERIODS, --method {auto|naive|seasonal_naive|mean|linear|holt|drift}, --seasonality SEASONALITY

- `variance` - Actual vs budget variance with materiality flags.  
  Options: **--input INPUT**, **--budget BUDGET**, --key KEY, --lower-is-better, --sheet SHEET, --delimiter DELIMITER


#### Category 16: Financial Modeling and Valuation

- `dcf` - Discounted cash flow valuation with terminal value and sensitivities.  
  Options: **--fcf FCF**, **--rate RATE**, --terminal-growth TERMINAL_GROWTH, --terminal-multiple TERMINAL_MULTIPLE, --mid-year, --net-debt NET_DEBT, --shares SHARES

- `ratios` - Financial ratio pack from statement inputs.  
  Options: **--income INCOME**, **--balance BALANCE**


#### Category 17: Treasury, Cash, and Banking

- `debt-schedule` - Debt amortisation or interest-only schedule.  
  Options: **--principal PRINCIPAL**, **--rate RATE**, **--periods PERIODS**, --frequency FREQUENCY, --interest-only, --grace GRACE


#### Category 18: Accounts Receivable, Payable, and Billing

- `aging` - AR/AP aging by configurable buckets.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --as-of AS_OF, --amount-column AMOUNT_COLUMN, --due-column DUE_COLUMN, --party-column PARTY_COLUMN, --buckets BUCKETS, --paid-column PAID_COLUMN

- `scorecard` - Transparent weighted scoring for review queues.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--weights WEIGHTS**, --output-column OUTPUT_COLUMN, --transform {none|log}

- `dup-invoices` - Detect duplicate invoice candidates for review.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --amount-column AMOUNT_COLUMN, --vendor-column VENDOR_COLUMN, --date-column DATE_COLUMN, --number-column NUMBER_COLUMN

- `po-match` - Two-way/three-way PO-invoice-receipt matching.  
  Options: **--input INPUT**, **--invoices INVOICES**, --receipts RECEIPTS, --price-tolerance PRICE_TOLERANCE, --qty-tolerance QTY_TOLERANCE

- `revenue-recognition` - Recognition schedule (straight line/milestones).  
  Options: **--total TOTAL**, **--periods PERIODS**, --method {straight_line|milestones}, --milestones MILESTONES


#### Category 19: Tax, Compliance, and Regulatory Support

- `vat-recon` - VAT/GST reconciliation with rate and ledger checks.  
  Options: **--output-tax OUTPUT_TAX**, **--input-tax INPUT_TAX**, --ledger-tax LEDGER_TAX, --sales SALES, --rate RATE, --tolerance TOLERANCE


#### Category 20: Investment, Portfolio, and Market Analysis

- `portfolio` - Time-weighted, money-weighted and benchmark returns.  
  Options: **--values VALUES**, --flows FLOWS, --benchmark BENCHMARK

- `risk-metrics` - Volatility, drawdown, Sharpe/Sortino and beta.  
  Options: **--returns RETURNS**, --risk-free RISK_FREE, --frequency FREQUENCY, --benchmark BENCHMARK


#### Category 22: Operations, Inventory, and Supply Chain

- `montecarlo` - Seeded Monte Carlo simulation with percentiles.  
  Options: **--variables VARIABLES**, --iterations ITERATIONS, --seed SEED, --formula {sum|product}, --buckets BUCKETS


#### Category 24: Human Resources and Workforce Planning

- `privacy-scan` - Detect personal/confidential data in cells, headers and metadata.  
  Options: **--input INPUT**, --limit LIMIT


#### Category 25: Statistical Analysis and Experimentation

- `describe` - Descriptive statistics with interpretation limits.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --columns COLUMNS, --group-by GROUP_BY

- `assumptions` - Audit statistical assumptions per column.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --columns COLUMNS

- `correlate` - Pearson/Spearman correlation with significance and caveats.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--x X**, **--y Y**, --method {pearson|spearman}

- `corrmatrix` - Correlation matrix across numeric columns.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, --columns COLUMNS

- `regress` - OLS regression with coefficients, fit and diagnostics.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--y Y**, **--predictors PREDICTORS**

- `ttest` - One-sample, two-sample or paired t-test.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--column COLUMN**, --group-column GROUP_COLUMN, --mu MU, --alpha ALPHA

- `proportions` - Two-proportion z-test with confidence interval.  
  Options: **--successes-a SUCCESSES_A**, **--n-a N_A**, **--successes-b SUCCESSES_B**, **--n-b N_B**, --alpha ALPHA

- `chisquare` - Chi-square independence test with Cramer's V.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--row-column ROW_COLUMN**, **--column-column COLUMN_COLUMN**, --alpha ALPHA

- `abtest` - A/B test analysis with lift, CI and power context.  
  Options: **--visitors-a VISITORS_A**, **--conversions-a CONVERSIONS_A**, **--visitors-b VISITORS_B**, **--conversions-b CONVERSIONS_B**, --alpha ALPHA

- `samplesize` - Required sample size for a stated effect and power.  
  Options: --effect-size EFFECT_SIZE, --baseline-rate BASELINE_RATE, --mde MDE, --power POWER, --alpha ALPHA, --population POPULATION

- `control-chart` - Control chart limits and special-cause signals.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--column COLUMN**, --subgroup SUBGROUP, --sigma SIGMA


#### Category 26: Data Mining, Segmentation, and Predictive Models

- `rfm` - RFM segmentation with explainable scores.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--customer-column CUSTOMER_COLUMN**, **--date-column DATE_COLUMN**, **--amount-column AMOUNT_COLUMN**, --as-of AS_OF, --quintiles QUINTILES

- `cohort` - Cohort retention matrix by period.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--customer-column CUSTOMER_COLUMN**, **--date-column DATE_COLUMN**, --value-column VALUE_COLUMN, --period PERIOD, --max-periods MAX_PERIODS

- `basket` - Item co-occurrence: support, confidence and lift.  
  Options: **--input INPUT**, --sheet SHEET, --delimiter DELIMITER, --encoding ENCODING, --no-header, --decimal DECIMAL, --thousands THOUSANDS, --table-index TABLE_INDEX, --max-rows MAX_ROWS, **--transaction-column TRANSACTION_COLUMN**, **--item-column ITEM_COLUMN**, --min-support MIN_SUPPORT

- `accuracy` - Forecast accuracy metrics (MAE/RMSE/MAPE/bias).  
  Options: **--actuals ACTUALS**, **--predictions PREDICTIONS**


#### Category 27: Automation, Macros, Scripts, and Office Integration

- `batch` - Run a validated pipeline of commands with dry-run, backups and recovery.  
  Options: **--ops OPS**, --dry-run, --keep-going

- `regression` - Compare baseline and candidate workbooks for regressions.  
  Options: **--baseline BASELINE**, **--candidate CANDIDATE**, --tolerance TOLERANCE


#### Category 28: Collaboration, Review, and Workflow Control

- `export-gate` - Pre-release check: sensitivity, links, hidden data, filters.  
  Options: **--input INPUT**, --recipients RECIPIENTS, --allow-external-links, --block-hidden, --max-sensitivity {clean|review|high_risk}


#### Category 29: Security, Privacy, and Governance

- `redact` - Mask/remove/hash/tokenize sensitive values into a new file (approval-gated).  
  Options: **--input INPUT**, **--output OUTPUT**, --columns COLUMNS, --patterns PATTERNS, --mode {mask|remove|hash|tokenize}

- `scrub` - Remove comments/metadata/hidden sheets into a clean copy (approval-gated).  
  Options: **--input INPUT**, --output OUTPUT, --keep-comments, --remove-hidden-sheets

- `risk-classify` - Classify workbook risk and list minimum controls.  
  Options: **--input INPUT**, --purpose PURPOSE, --downstream DOWNSTREAM, --regulated


#### Category 33: AI-Assisted Spreadsheet Intelligence

- `assist-build` - Generate a review-ready workbook from a plain-language request.  
  Options: --text TEXT, --input INPUT, **--output OUTPUT**

- `task-plan` - Decompose a plain-language request into staged commands.  
  Options: --input INPUT, --text TEXT


#### Meta and workflow commands

- `capabilities` - List capabilities/commands and the catalog mapping.  
  Options: --full, --category CATEGORY, --command-name COMMAND_NAME

- `selftest` - Run the packaged verification suite and report pass/fail.  
  Options: (none)

- `catalog-parse` - Parse a Skill catalog (Markdown/HTML/plain) into JSON.  
  Options: --input INPUT, --text TEXT

- `capabilities` - List capabilities/commands and the catalog mapping.  
  Options: --full, --category CATEGORY, --command-name COMMAND_NAME

- `selftest` - Run the packaged verification suite and report pass/fail.  
  Options: (none)

- `catalog-parse` - Parse a Skill catalog (Markdown/HTML/plain) into JSON.  
  Options: --input INPUT, --text TEXT

---

## 7. Configuration

### 7.1 Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SDF_MAX_ROWS` | `200000` | Hard row ceiling for delimited reads; exceeding it truncates with a warning |
| `SDF_MAX_FILE_MB` | `200` | Refuses to read a file larger than this (`INPUT_TOO_LARGE`) |
| `SDF_TIMEOUT_S` | `120` | Advisory operation budget reported to the caller |
| `SDF_AUDIT_LOG` | `./sdf-audit.jsonl` | Audit trail destination (`--audit-log` overrides) |

### 7.2 Assets you can point commands at

* `asset-workbook-spec.schema.json` — write a spec that validates against it, then
  `build --spec my-spec.json`.
* `asset-formula-library.json` — `suite` is executed by `selftest`; `patterns` are copy-ready
  formulas; `engine_limits` states what `eval` will not do.
* `asset-finance-policy.json` — pick a preset and pass its values as arguments (currency, decimals,
  tolerances, aging buckets, materiality, required notes). The tool never invents tax rates: you
  supply them, and the policy file records which ones you used.
* `asset-style-presets.json` / `asset-chart-palettes.json` — pass `--theme` (JSON) for workbooks and
  `--palette-name` / `--palette` for charts.
* `asset-capabilities.json` — the authoritative command↔capability map.

### 7.3 Approval policy (enforced in code)

`--approve` is required for: `build`, `dashboard`, `assist-build`, `set-cells`, `bulk`,
`protect`, `convert`, `redact`, `scrub`, `export-gate`, `sort`, `batch`, and the write half of
`number-format --apply`. Dry runs are exempt (`bulk` without `--apply`, `batch --dry-run`) because
they cannot change data. `redact` requires `--approve` **and** a distinct `--output` path; it refuses
to overwrite its own source.

---

## 8. Safety, privacy and review rules

1. **Non-destructive by default.** Every write goes to a new path or needs `--overwrite`; `build`
   keeps a `.bak` copy when overwriting; `bulk` and `batch` support dry runs.
2. **Atomic writes.** Files are written to a temporary sibling and moved into place, so a crash
   cannot leave a half-written workbook.
3. **No secrets, no network.** The code contains no credentials, endpoints or telemetry. Nothing is
   uploaded; nothing calls an API.
4. **No formula execution from files.** `eval` runs an AST interpreter over a closed function
   library. Macros are never executed; `structure` only reports whether `vbaProject.bin` exists.
5. **Privacy findings are indicative.** `privacy-scan` uses regex patterns (email, phone, card,
   SSN-like, IBAN, IPv4, PH national ID) plus sensitive-header detection. It is a review aid, not a
   compliance certification, and `redact`/`scrub` output must be re-checked before sharing.
6. **Consequential finance output needs a human.** Statements, valuations, provisions, tax support,
   payroll, commission and scoring outputs are decision *support*. The tool states the policy it
   assumed and returns control totals; it never posts, files, pays or publishes.
7. **Predictive/scoring output is for humans.** `rfm` and `scorecard` rank records for review and
   explicitly do not make eligibility, credit or employment decisions.
8. **Release gating is technical.** `export-gate` checks sensitivity, external links, hidden sheets,
   error values and active filters. It returns `pass`, `pass_with_warnings` or `blocked`; sign-off
   stays with the document owner.

---

## 9. Validation actually performed

All of the following was run in the build environment (Python 3.13.16, openpyxl 3.1.5) against real
fixtures; nothing below is claimed without having executed it.

1. `--help` and `capabilities` — rendered; 105 commands listed; `capabilities --full` returns 34
   categories and 340 capabilities; `capabilities --category`/`--command-name` filters tested.
2. **Command suite: 111 invocations, 111 passed (`status: ok`, exit code 0).** Coverage spans every
   command family: intake (`read` csv/xlsx/json/html, `dialect`, `encoding`, `structure`, `compare`,
   `triage`, `links`, `hidden`, `manifest`, `convert`, `flatten`), authoring (`build`, `set-cells`,
   `bulk` dry-run and apply, `layout`, `cond-format`, `validations`, `protect`, `print-setup`, `nav`,
   `merge-audit`, `sort`, `number-format`, `chart`, `chart-advice`, `viz-audit`, `dashboard`),
   formulas (`eval` with cells and with a source file, `explain`, `deps`, `scan`, `hardcode`), data
   (`profile`, `clean`, `dedupe`, `join`, `pivot`, `unpivot`, `aggregate`, `window`, `resample`,
   `sample`, `crosswalk`, `allocate`, `outliers`, `enforce`), statistics (`describe`, `correlate`,
   `corrmatrix`, `regress`, `ttest`, `proportions`, `chisquare`, `abtest`, `samplesize`,
   `control-chart`, `montecarlo`, `forecast`, `accuracy`, `rfm`, `cohort`, `basket`, `scorecard`,
   `assumptions`), finance (`coa`, `journal-check`, `trial-balance`, `gl-summary`,
   `income-statement`, `balance-sheet`, `cash-flow`, `variance`, `drivers`, `dcf`, `loan`, `lease`,
   `depreciation`, `aging`, `dup-invoices`, `po-match`, `vat-recon`, `bank-recon`, `portfolio`,
   `risk-metrics`, `debt-schedule`, `ratios`, `kpi`, `revenue-recognition`), governance (`qa-gate`,
   `privacy-scan`, `redact`, `scrub`, `export-gate`, `risk-classify`, `reconcile`, `regression`,
   `review-pack`), workflow (`batch` dry-run and real run, `task-plan`, `assist-build`, `narrate`).
3. **Verification suite: 74 assertions, all passed.** Includes:
   * *Error handling:* missing file, malformed JSON spec, malformed formula, unknown function
     (`#NAME?` surfaces as a value, not a crash), circular reference detection, non-numeric
     `describe`, unknown column, unknown sheet, mismatched lengths, `terminal_growth >= rate`,
     overwrite refusal, and approval refusals for `redact` and `protect`.
   * *Recovery:* an invalid spec was rejected, then the corrected spec produced a valid workbook.
   * *Arithmetic checks against independently computed references:* trial balance balances; income
     statement revenue/gross profit/net profit (383,000 / 283,000 / 150,000); balance sheet balances;
     cash-flow operating total (45,000) and a deliberately injected 5,000 cash difference flagged;
     DCF present value, enterprise value and equity bridge recomputed by hand and matched; loan
     payment matched an independent PMT calculation (5,009.49) with a zero closing balance; aging
     bracket boundaries and totals; A/B test rates, direction and lift; Welch t-test; pivot total
     equals the equivalent aggregate; allocation sums exactly to the group total; basket lift;
     RFM/cohort counts; regression R² bounds and coefficient count; journal debit = credit.
   * *Determinism and sensitivity:* Monte Carlo reproduced byte-identical results with the same seed
     and changed with a different input mean; sampling reproduced with the same seed; changing the
     as-of date changed the trial balance.
   * *Output inspection:* every generated `.xlsx` re-opened in openpyxl; the dashboard showed 2
     native charts at distinct anchors; the navigation workbook contained an Index sheet with
     links; protection, validations and conditional formats were present after writing; the
     redacted copy showed masked values; the scrubbed copy had no comments and no creator metadata;
     the SVG chart parsed as XML; exported CSV/MD/JSON files were read back.
   * *Chart rendering:* all nine chart kinds were rendered to SVG and rasterised to PNG for visual
     inspection (fixing two real defects found that way: repeated categories were being drawn as
     separate bars instead of being aggregated, and donut percentage labels overlapped the hole).
   * *Type safety:* `INV-2001`-style identifiers stay text; leading-zero codes stay text; date
     columns are inferred as dates; text columns are not mislabelled as identifiers.
4. **Packaged `selftest`: 23/23 checks passed** (13 formula cases from
   `asset-formula-library.json`, journal balance/detection, loan amortisation to zero, exact
   allocation, left-join cardinality, identifier typing, t-test, regression slope recovery, SVG
   rendering, catalog parsing).
5. **Negative/recovery paths and structured errors** verified: every failure returns
   `status: error` with a stable `code`, a message and a hint, and exit code 2 (or 3 only for an
   unexpected internal fault).
6. **Isolation test:** the folder was copied into a clean temporary skill root beside a test
   `SKILL.md` and driven using relative paths only; then the ZIP was extracted into a separate clean
   directory and the primary entry point was re-run from the extracted copy.

Not performed (and therefore not claimed): recalculation of formulas by Excel itself, rendering of
`.xlsx` to pixels/PDF (no LibreOffice in the environment), execution of VBA/Office Scripts/Power
Query/DAX, and any network, database, mailbox, geocoding or PDF-table extraction.

---

## 10. Limits and unavailable integrations

**Deliberately out of scope** (each is marked `external` in `asset-capabilities.json`):

* **Excel recalculation and cached values.** `structure`, `scan` and `qa-gate` read formulas as
  text; openpyxl cannot compute them. Cells written by this tool have no cached value, so a
  spreadsheet application must open the file once before value-based readers see results.
* **VBA, Office Scripts, Power Automate, R, Power Query M, DAX, CUBE functions, the workbook data
  model, native pivot tables/slicers/timelines.** No runtime is bundled and no code is generated by
  a Windows/Excel-only engine. Use `plan`/`task-plan` to prepare, then build in Excel.
* **Connectors:** no SQL, REST/API, mailbox, ERP/CRM API, PDF-table extraction or geocoding. Local
  exports from those systems are fully supported as files.
* **Rendering to PDF/PNG of workbooks.** SVG chart output is provided instead, plus print setup that
  makes the app's own PDF export sane.
* **Retention/legal-hold enforcement and secure deletion.** `risk-classify` and `review-pack` record
  the decision; execution belongs to the operator.
* **Legacy formats.** `.xls` conversion needs the optional `xlrd` package and preserves values only
  (no formulas/VBA). `.xlsb` cannot be read by this stack. `.ods` conversion is not implemented in
  this build even with `odfpy` present.
* **Engine scope of `eval`:** classic formulas over a closed function library (SUM/AVERAGE/IF/
  IFERROR/COUNTIF/SUMIF/SUMIF/ROUND/PMT/NPV/IRR/lookups/text and date helpers, and more — see the
  `FUNCTIONS` set). No dynamic arrays, no `LET`/`LAMBDA`, no implicit intersection, no iterative
  calculation, `RATE` returns `#N/A`.
* **Statistical scope:** two-group comparisons only (no post-hoc multiple-comparison correction),
  OLS without heteroskedasticity-robust standard errors, logistic regression not implemented,
  forecasts are pattern-based (no driver decomposition), and Monte Carlo assumes independent draws.
* **Scale:** single-machine, in-memory processing. Defaults cap input at 200 MB and 200,000 rows;
  huge workbooks should be aggregated before they reach this tool.

---

## 11. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `OUTPUT_EXISTS` (exit 2) | the target file already exists | pass `--overwrite`, or choose another `--output` |
| `APPROVAL_REQUIRED` (exit 2) | a mutating/release command without `--approve` | review the dry run, then re-run with `--approve` |
| `INPUT_NOT_FOUND` / `INPUT_IS_DIRECTORY` | wrong path or a folder was passed | pass a file; `manifest --inputs` accepts folders |
| `INPUT_TOO_LARGE` | file above `SDF_MAX_FILE_MB` | raise the variable or split the file |
| `UNSUPPORTED_FORMAT` / `LEGACY_FORMAT_UNSUPPORTED` | `.xlsb`, `.ods`, or an unknown extension | convert to `.xlsx`/`.csv` first (`convert` handles `.xls` with `xlrd`) |
| `UNKNOWN_SHEET` / `UNKNOWN_COLUMN` | typo, or a header row that is not row 1 | the error lists what actually exists; use `structure` or `read` to inspect |
| `MISSING_DEPENDENCY` | optional package absent (e.g. `xlrd`) | install the package named in the hint |
| `NO_NUMERIC_COLUMNS` | values are text, or the wrong column was named | run `enforce --schema` or `clean --type col=number` first |
| `SINGULAR_MATRIX` | constant or duplicated predictors | drop the collinear column |
| `DEGENERATE_DATA` | constant series, zero totals | the statistic is undefined; fix the data, do not force it |
| `FORMULA_IN_SORT_RANGE` / `MERGED_CELLS_BLOCK_SORT` | sort target is not a values-only table | sort source data before formulas, or unmerge (`merge-audit`) |
| `CIRCULAR_REFERENCE` | formula chain loops | break the loop with an explicit input cell |
| `BATCH` step failed | one op has a bad argument | re-run with `--keep-going` to see all failures; the result lists each step |
| Numbers look wrong after import | locale (decimal comma) or identifier coercion | pass `--decimal ","`/`--thousands "."`; identifiers are preserved as text by design |
| A date column is text | mixed formats | `clean --type date_col=date` reports unparseable values as exceptions |

Recovery pattern: **prompts are always non-destructive.** Re-run the same command against the
original input, adjust one option, and compare with `--save-result` snapshots. For workbooks, keep the
`.bak` that `build --overwrite` writes, and use `compare --baseline ... --candidate ...` to see
exactly what changed.

---

## 12. Extension and maintenance

* **Add a command:** write the implementation in the matching `tools-sdf-<area>.py`, add a
  `cmd_*` wrapper in `tools-sdf.py` returning `ok_data(...)` or raising `SkillToolError`, register it
  in `COMMANDS`, and add its parser with `add(...)`. Re-run `selftest` and the two suites.
* **Keep capabilities honest:** when you implement something previously marked `assisted`/`external`,
  update `asset-capabilities.json` (`status`, and remove or rewrite `note`) so the
  `capabilities` output never overstates support.
* **Style of new outputs:** always return `columns`/`rows` table payloads through `_tables` so
  `--export` works, always attach the policy or convention used, and never drop warnings.
* **Dependencies:** prefer the standard library. If a package is unavoidable, add it to §3 with a
  pinned range and to the `MISSING_DEPENDENCY` hint of the command that needs it.
* **Versioning:** bump `VERSION` in `tools-sdf-common.py`; `--version`, every result envelope and the
  audit log carry it.
