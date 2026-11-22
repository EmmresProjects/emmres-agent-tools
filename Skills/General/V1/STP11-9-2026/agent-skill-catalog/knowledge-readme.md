# knowledge-readme.md — agent-skill-catalog

Single documentation file for this Skill Tool component. It is part of a future Copilot skill: this
folder is copied beside the parent package's root `SKILL.md`. This folder contains **no** `SKILL.md`.

---

## 1. What this Skill Tool is

`agent-skill-catalog` turns a *catalog of agent capabilities* into operating machinery. The catalog
that ships with it (`agent-skill-catalog/asset-catalog-240-skills.md`) is the one from the Agent Skill
Catalog briefing: **240 skills across 24 categories**, covering reference intelligence, research,
knowledge management, documents, education, software, data, cloud, security, web, automation, agent
design, writing, projects, business, customer service, finance, legal, science, creative, games,
personal productivity, quality assurance and reference domain packs.

The tool does five things with such a catalog, all offline and deterministic:

1. **Ingest** any catalog text — copied PDF text, Markdown, or pasted HTML with tags, CSS classes,
   data attributes and numbering marks — into a clean JSON registry.
2. **Validate** the registry: duplicates, missing or thin descriptions, markup leaks, empty
   categories, with stable error codes and non-zero exit codes.
3. **Use** the registry: keyword search, intent routing with an editable signal map, generated
   implementation briefs, coverage and gap analysis, and a claim-by-source pre-screen matrix.
4. **Operate the saved-reference-library pattern** the catalog describes: category-named Markdown
   files, a 15-field entry schema, URL canonicalisation, non-destructive dedupe, staleness and link
   audits, and topic packs for progressive loading.
5. **Package**: scaffold a working starter Skill Tool folder for any catalog capability, and check
   any folder or ZIP against the packaging contract (no `SKILL.md`, no subfolders, prefixes, one
   knowledge file, depth, file count, resolvable references).

### Honest scope statement

The 240 catalog entries are **domain capabilities** ("Threat Model Facilitator", "Lab Protocol
Author"). This Skill Tool does not attempt to be 240 domain engines. What it does, and what is
tested, is: every one of the 240 entries is a first-class registry record that can be **searched,
routed to, briefed, and scaffolded**; and the catalog's cross-cutting capabilities — reference
libraries, source priority, dedupe, provenance, staleness, coverage, QA, packaging, approval gates —
are **implemented as running code**. A scaffolded folder implements its own `capabilities`, `config`
and `verify` commands, while its domain `run` body is deliberately left unimplemented and exits 3.
Nothing in this package claims a domain capability it does not perform.

---

## 2. Files and load order

All files sit flat in this folder. No subfolders.

| File | Kind | Role |
| --- | --- | --- |
| `agent-skill-catalog/tools-skillcatalog.py` | tool | **Primary entry point.** Loads the modules, owns the CLI, exit codes, atomic writes, signal handling. |
| `agent-skill-catalog/tools-catalog-parser.py` | tool | Input handling: HTML→text, format detection, parsing, normalisation, rendering (JSON/Markdown/text/HTML). Loaded first. |
| `agent-skill-catalog/tools-catalog-planner.py` | tool | Search, intent routing, brief generation, validation, coverage/gaps, evidence pre-screen. Loaded second; imports helpers from the parser module. |
| `agent-skill-catalog/tools-reference-library.py` | tool | Library build/audit/dedupe/package, URL canonicalisation, entry schema checks, optional bounded online checks. Loaded third. |
| `agent-skill-catalog/tools-skill-packager.py` | tool | Package validation (folder and ZIP) and scaffold generation. Loaded fourth. |
| `agent-skill-catalog/tools-selftest.py` | tool | Test suite: drives the real CLI in subprocesses in a temporary directory. |
| `agent-skill-catalog/knowledge-readme.md` | knowledge | This document. |
| `agent-skill-catalog/asset-catalog-240-skills.md` | asset | Canonical catalog data: 24 categories × 10 skills, with descriptions. Human-editable. |
| `agent-skill-catalog/asset-reference-schema.json` | asset | The 15-field reference entry schema plus rules; rendered into every generated library as the schema page. |
| `agent-skill-catalog/asset-reference-seed.json` | asset | 18 real seed sources, one or more per reference domain pack, used as the default `library build` input. |
| `agent-skill-catalog/asset-fixture-library.json` | asset | Deliberately imperfect entries (duplicate pair with tracking parameters, near duplicate, stale dates, dead link) used by the test suite. |
| `agent-skill-catalog/asset-intent-keywords.json` | asset | Routing signal map: 24 intents, each with natural-language signals, category boosts and skill boosts. |
| `agent-skill-catalog/asset-plan-template.md` | asset | The implementation-brief template filled by `plan`. Placeholders are double-brace tokens (field names only, no code required). |

**Load order** (handled automatically by the primary entry point through `importlib`; the hyphenated
filenames are never imported with a plain `import`):

```
tools-skillcatalog.py
  -> tools-catalog-parser.py        registered as module "tools_catalog_parser"
  -> tools-catalog-planner.py       imports tools_catalog_parser
  -> tools-reference-library.py     imports tools_catalog_parser (or self-loads it)
  -> tools-skill-packager.py        imports tools_catalog_parser (or self-loads it)
```

The planner, library and packager modules fall back to self-loading the parser module from the same
directory, so each can also be loaded on its own. Running any module except the primary entry point
directly is not supported; use the primary entry point.

**File count: 13 files** (6 tools, 1 knowledge, 6 assets). Maximum path depth inside the folder: 1 level.

---

## 3. Requirements and setup

* **Python 3.9 or newer** (tested on CPython 3.13.16). No third-party packages, no `pip install`,
  no virtualenv required. Everything uses the standard library: `argparse`, `importlib`, `json`,
  `re`, `urllib`, `html.parser`, `zipfile`, `csv`, `difflib`, `datetime`, `subprocess`.
* **No network access is needed.** Only `library audit --online` contacts URLs, and only when that
  flag is passed.
* No credentials, API keys, paid services or private endpoints are used or required.

Run every example from the future skill root (the folder that holds the parent `SKILL.md` and this
Skill Tool folder). The shell is stated explicitly in each command.

```bash
# bash, from the future skill root
python3 agent-skill-catalog/tools-skillcatalog.py --help
python3 agent-skill-catalog/tools-skillcatalog.py capabilities
```

The entry point resolves every bundled asset relative to its own directory, so it works from any
working directory, and every documented reference is written relative to the future skill root, for
example `agent-skill-catalog/asset-catalog-240-skills.md`.

---

## 4. Quick start

```bash
# bash, from the future skill root
python3 agent-skill-catalog/tools-skillcatalog.py capabilities                       # 14 capabilities
python3 agent-skill-catalog/tools-skillcatalog.py validate --json                    # bundled catalog: 24 / 240
python3 agent-skill-catalog/tools-skillcatalog.py search --query "dead link" --limit 3
python3 agent-skill-catalog/tools-skillcatalog.py route --intent "our invoices do not match the purchase orders"
python3 agent-skill-catalog/tools-skillcatalog.py plan --skill "Invoice Validation Assistant" --out ./plans/invoice.md
python3 agent-skill-catalog/tools-skillcatalog.py library build --out ./library
python3 agent-skill-catalog/tools-skillcatalog.py library audit --library ./library --today 2026-11-09
python3 agent-skill-catalog/tools-skillcatalog.py scaffold --name invoice-check --from-skill "Invoice Validation Assistant" --out ./build --zip ./build/invoice-check.zip
python3 agent-skill-catalog/tools-skillcatalog.py check --zip ./build/invoice-check.zip
python3 agent-skill-catalog/tools-skillcatalog.py selftest
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Ran correctly but findings exist: validation errors, no search matches, audit errors, package check failures. |
| 2 | Bad input: missing file, empty input, unparseable catalog, unknown skill, bad flag combination. |
| 3 | Blocked pending approval: overwriting existing outputs, `dedupe --apply` without `--approve`, replacing a non-empty scaffold folder, replacing an existing ZIP. |
| 130 | Interrupted with Ctrl-C; no partial output is kept. |

Every command that takes `--json` prints a single JSON object with an `ok` field. Errors go to
stderr as `error: ...` and, with `--json`, also as `{"ok": false, "error": "..."}`.

---

## 5. Command reference

Global: `--help`, `--version`. Each subcommand has its own `--help`.

### 5.1 `capabilities`

Prints the capability list with the exact command for each, or `--json` for a machine-readable list
that includes the catalog skills each capability derives from.

```bash
python3 agent-skill-catalog/tools-skillcatalog.py capabilities --json
```

### 5.2 `parse` — ingest any catalog text

| Flag | Default | Meaning |
| --- | --- | --- |
| `--input` | required | Catalog file, or `-` for stdin. |
| `--format` | `auto` | `auto`, `text`, `markdown`, `list`, `plain-list`, `html`. |
| `--emit` | `json` | `json`, `md`, `text`, `html`. |
| `--out` | stdout | Output path. |
| `--title` | `Skill Catalog` | Title used by `md`/`html` output. |
| `--stats` | off | Print category/skill/word/vocabulary counts. |
| `--max-bytes` | 8388608 | Input ceiling. |
| `--json` | off | With `--stats`, emit stats as JSON. |
| `--force` / `--approve` | off | Allow replacing an existing output file. |

Recognised input shapes (all four are parsed by the same normaliser):

1. **Catalog text** — `1. Skill Name: X` followed by `Description: ...`, optionally wrapped across
   lines, with `## 1. Category` or `1. Category` headings, and PDF page headers such as
   `Agent Skill Catalog | 7` ignored.
2. **Markdown headings** — `## Category` / `### 1.2 Skill` with `Description:` lines.
3. **Markdown lists** — `- **Skill Name** — description`, `- **Skill Name**: description`.
4. **Copied HTML** — tags, CSS classes, `data-*` attributes, `<ol start=…>`, nested `<span>`/`<em>`,
   `<style>` and `<script>` blocks are stripped with `html.parser` before the text normaliser runs.

```bash
python3 agent-skill-catalog/tools-skillcatalog.py parse --input ./pasted.html --format html --emit md --out ./catalog.md
python3 agent-skill-catalog/tools-skillcatalog.py parse --input - --stats < ./copied-catalog.txt
```

Behaviour notes: the first top-level heading becomes the document title (recorded, not turned into a
category); prose before the first category is kept as front matter, not as a skill; implausible names
are dropped with a warning; category and skill indexes are renumbered sequentially so downstream
counts are stable. Markdown output round-trips back through the parser (tested).

### 5.3 `validate` — structural validation

```bash
python3 agent-skill-catalog/tools-skillcatalog.py validate --catalog agent-skill-catalog/asset-catalog-240-skills.md --strict --json
```

Codes: `CATALOG_EMPTY`, `NO_SKILLS`, `CATEGORY_NAME_MISSING`, `CATEGORY_DUPLICATE`, `CATEGORY_EMPTY`,
`CATEGORY_THIN`, `SKILL_NAME_MISSING`, `SKILL_NAME_TOO_LONG`, `SKILL_NAME_MARKUP`, `SKILL_DUPLICATE`,
`SKILL_DESCRIPTION_MISSING`, `SKILL_DESCRIPTION_SHORT`, `SKILL_DESCRIPTION_DUPLICATE`,
`SKILL_DESCRIPTION_CASE`, `SKILL_INDEX_NONSEQUENTIAL`. `--strict` promotes warnings to errors and
therefore changes the exit code from 0 to 1.

### 5.4 `search` — keyword ranking

Explainable lexical scoring: name tokens weigh 3, description tokens 1.5, category tokens 0.75, a
substring phrase hit in the name adds 3, prefix matches count half, and a coverage term rewards
queries whose terms all matched. Output includes `score` and `matched_terms` so the ranking is
auditable.

```bash
python3 agent-skill-catalog/tools-skillcatalog.py search --query "access review" --category 9 --limit 5 --json
```

`--category` accepts an index (`9`), a name or a slug. No matches exits 1 with a hint.

### 5.5 `route` — intent routing

Combines the signal map in `agent-skill-catalog/asset-intent-keywords.json` with lexical scoring.
A signal matches either as a substring or when all of its words appear in the intent text, so
"triage our vulnerability findings" hits the `vulnerability triage` signal. Matched intents boost
their category and named skills. Output explains the signals (`reasons`) and prints a ready-to-run
`plan` command per match.

```bash
python3 agent-skill-catalog/tools-skillcatalog.py route --intent "check who has excess access before the audit" --top 5 --json
```

With no signal match it degrades to pure lexical ranking. Matches below `--min-score` (default 0.75)
are dropped, so a nonsense intent exits 1 with "no skill scored at or above the confidence floor"
instead of returning a bogus best guess; pass `--min-score 0` to see everything. The asset is data:
edit signals, add intents, or point `--keywords` at another file.

### 5.6 `plan` — implementation brief

```bash
python3 agent-skill-catalog/tools-skillcatalog.py plan --skill "Redaction and Privacy Reviewer" --out ./plans/redaction.md
python3 agent-skill-catalog/tools-skillcatalog.py plan --category 23 --out ./plans/qa     # one brief per skill
```

Skill lookup order: exact name, substring, then closest match (`difflib`, cutoff 0.6) with a note.
The brief is filled from `agent-skill-catalog/asset-plan-template.md` and derived from the catalog
description itself:

* **Trigger phrases** — the lowercased name plus phrasings built from the first description clause.
* **Procedure** — the description is split into clauses on `;`, `, and`, `, ` and sentence
  boundaries; each clause becomes a required step and a required output.
* **Inputs** — extracted from a lexicon (`link`→links and URLs, `log`→logs or telemetry, `invoice`→
  invoices or purchase orders, …) plus two base inputs.
* **Approval gates** — derived by scanning the description for destructive, publishing, financial,
  credential/security, personal-data and high-stakes verbs; a matching capability gets an explicit
  gate with the reason and the rollback requirement.
* **Verification checklist** — eight base checks (help, valid input, missing input, malformed input,
  structured output, artifact inspection, recovery) plus one confirmation per clause plus one per
  approval gate.
* **Limits and recovery**, and the exact `scaffold` + `check` commands to continue.

Unresolved template placeholders are reported at the end of the file rather than silently ignored.
Existing output files are protected unless `--force` is passed.

### 5.7 `coverage` — coverage and gaps

```bash
python3 agent-skill-catalog/tools-skillcatalog.py coverage
python3 agent-skill-catalog/tools-skillcatalog.py coverage --goal "measure quantum decoherence in trapped ions" --json
```

Without `--goal`: category counts, vocabulary size, top terms. With `--goal`: tokenises the goal,
reports the coverage ratio, lists covered terms, and lists gap terms with their nearest catalog
terms (`difflib`, cutoff 0.75) — targeted additions instead of broad searching.

### 5.8 `evidence` — claim-by-source pre-screen

```bash
python3 agent-skill-catalog/tools-skillcatalog.py evidence --claims ./claims.json --library ./library \
  --out ./matrix.md --csv ./matrix.csv --min-score 0.2
```

`claims.json` is a JSON array of strings or `{"id": "...", "claim": "..."}` objects (an object with a
`claims` array is also accepted). Sources come from `--library DIR` or `--entries FILE`.

`--min-score` defaults to 0.05, so sources with no lexical overlap are omitted; pass `--min-score 0`
for a full claim × source grid. Output rows carry the claim, source, lexical overlap ratio, an overlap band
(`strong`/`partial`/`weak`), the overlapping terms, and **empty** `reviewer_support` /
`reviewer_confidence` columns. The disclaimer is printed in the Markdown head and in JSON: overlap is
lexical only and a human must confirm support, contradiction and authority. This tool never decides
that a claim is supported.

### 5.9 `library build` — create the reference library

```bash
python3 agent-skill-catalog/tools-skillcatalog.py library build --out ./library
python3 agent-skill-catalog/tools-skillcatalog.py library build --entries ./my-entries.json --out ./library --approve --force
```

Writes one Markdown file per category slug (`general.md`, `games.md`, `technology.md`, `studies.md`,
`research.md`, `science.md`, `business.md`, `legal-policy.md`, `health-safety.md`,
`creative-media.md`, … derived from the entry data), plus `archive-index.md` (counts, active versus
stale) and `source-schema.md` generated from `agent-skill-catalog/asset-reference-schema.json`.

Entries are validated **before** anything is written; any error means exit 1 and no directory is
created. Writes are atomic (`.tmp-partial` then `os.replace`). Overwriting an existing library needs
`--approve` (and `--force`), otherwise exit 3.

### 5.10 `library audit` — staleness, structure, optional link health

```bash
python3 agent-skill-catalog/tools-skillcatalog.py library audit --library ./library --today 2026-11-09 --max-age-days 365
python3 agent-skill-catalog/tools-skillcatalog.py library audit --library ./library --online --max-urls 10 --timeout 5 --retries 1 --out ./audit.md
```

Offline by default. Findings: `STALE_ENTRY`, `STATUS_INACTIVE` (dead, moved, merged, superseded),
`NO_CHECK_DATE`, `CHECK_DATE_FUTURE`, `NO_ARCHIVE`, `ANNOTATION_LONG`, and when `--online` is given
`LINK_DEAD`, `LINK_RESTRICTED` (401/403/429), `LINK_REDIRECTED`, `LINK_UNREACHABLE`, plus
`ONLINE_CAP` when the URL limit truncated the run. The report ends with a **repair queue** section
and states that nothing is edited automatically — dead links stay in the file marked `dead`.

Online checks are bounded: `--max-urls` (default 25), `--timeout` (5 s), `--retries` (1),
0.2 s spacing, `Range: bytes=0-2048`, an identifying User-Agent, and no credential handling. Exit 1
when any `error`-level finding exists, 0 otherwise.

### 5.11 `library dedupe` — non-destructive duplicate handling

```bash
python3 agent-skill-catalog/tools-skillcatalog.py library dedupe --library ./library            # dry run
python3 agent-skill-catalog/tools-skillcatalog.py library dedupe --library ./library --apply --approve
```

Canonicalisation strips tracking parameters (`utm_*`, `gclid`, `fbclid`, `ref`, `si`, `spm`, …),
lowercases scheme and host, drops default ports and fragments, sorts query parameters and normalises
slashes. Exact canonical duplicates become merge groups: the richest record (most populated fields,
then authority, then earliest date) is kept; its tags, longer annotation and archive link absorb the
others; the rest are set to `status: merged` with a recorded successor. Same-host/same-title pairs
with different URLs are reported as near duplicates and are **not** merged automatically. `--apply`
requires `--approve`, otherwise exit 3.

### 5.12 `library package` — topic pack for progressive loading

```bash
python3 agent-skill-catalog/tools-skillcatalog.py library package --library ./library --name security-review \
  --category health-safety --category legal-policy --limit 40 --out ./packs
```

Writes `<name>-index.md` (load order, file table, top priority entries, "read this index only, then
the one matching category file" instructions), one `<category>.md` per selected category ordered by
authority then recency, and `<name>-entries.json` for machine use. `dead` and `merged` records are
excluded.

### 5.13 `scaffold` — working starter Skill Tool folder

```bash
python3 agent-skill-catalog/tools-skillcatalog.py scaffold --name invoice-check \
  --from-skill "Invoice Validation Assistant" --out ./build --zip ./build/invoice-check.zip
```

Creates `invoice-check/` containing:

* `tools-invoice-check.py` — runnable CLI: `--help`, `--version`, `capabilities`, `config`,
  `verify` (re-checks prefix/subfolder/knowledge/file-count rules), `run --input --out` (validates
  the path and size, then exits 3 until the body is implemented).
* `asset-config.json` — name, capability, catalog reference, limits (timeout, input ceiling,
  retries, network policy), approval policy, output format.
* `knowledge-readme.md` — capability, files, exact commands, status statement, the full
  implementation brief, and a maintenance checklist.
* `tools-selftest.py` — subprocess checks for help, capabilities, config, verify and missing-input.

`--name` must match `^[a-z0-9]+(?:-[a-z0-9]+)*$`. Replacing a non-empty target needs
`--approve --force`. `--zip` writes the folder at the archive root.

### 5.14 `check` — packaging contract

```bash
python3 agent-skill-catalog/tools-skillcatalog.py check --path ./build/invoice-check
python3 agent-skill-catalog/tools-skillcatalog.py check --zip ./build/invoice-check.zip --json
```

Folder codes: `SKILL_MD_PRESENT`, `SUBFOLDER_FORBIDDEN`, `NESTED_PATH`, `DEPTH_EXCEEDED`,
`FILENAME_PREFIX`, `FILENAME_SPACE`, `KNOWLEDGE_FILE`, `NO_TOOLS`, `NO_RUNNABLE`, `TOO_MANY_FILES`,
`EMPTY_PACKAGE`, `NESTED_ARCHIVE`, `EMPTY_FILE`, `UNREADABLE_FILE`, `REFERENCE_UNRESOLVED` (folder-
qualified references such as `agent-skill-catalog/asset-...` must exist), `PLACEHOLDER_TEXT`
(skipped for `*template*` files), `FILENAME_STYLE`.

ZIP codes: `ZIP_CORRUPT`, `ZIP_ROOT` (exactly one top-level folder), `ZIP_WRAPPER` (no files directly
under that folder), `ZIP_NO_TOOLS`, `NESTED_ARCHIVE`, `TOO_MANY_FILES`. The ZIP is inspected without
extracting it.

### 5.15 `selftest`

```bash
python3 agent-skill-catalog/tools-skillcatalog.py selftest --verbose
python3 agent-skill-catalog/tools-selftest.py --json --keep
```

Runs the shipped suite against the real CLI. `--keep` preserves the temporary workspace for
inspection, `--online` adds the bounded live-link check, `--json` emits a machine-readable summary.

---

## 6. Data formats

### 6.1 Canonical catalog format

```markdown
## 1. Reference Intelligence and Source Libraries

1. Skill Name: Reference Library Navigator
Description: Search saved reference files before open-web research, ...

2. Skill Name: Reference Catalog Builder
Description: Create and maintain category-named Markdown or text files ...
```

Parsed JSON shape:

```json
{
  "format": "catalog",
  "title": "Agent Skill Catalog",
  "front_matter": ["..."],
  "category_count": 24,
  "skill_count": 240,
  "categories": [
    {"index": 1, "name": "Reference Intelligence and Source Libraries", "slug": "reference-intelligence-and-source-libraries",
     "skills": [{"index": 1, "name": "Reference Library Navigator", "slug": "reference-library-navigator",
                 "description": "...", "category": "...", "category_index": 1, "category_slug": "..."}]}
  ],
  "skills": ["...flat list of the same skill records..."],
  "warnings": []
}
```

### 6.2 Reference entry schema

`agent-skill-catalog/asset-reference-schema.json` defines 15 fields: `id`, `title`, `url`,
`category`, `tags`, `source-type`, `authority`, `added-by`, `date-added`, `last-checked`, `status`,
`archive-url`, `license`, `annotation`, `related`. Required: `title`, `url`, `category`,
`source-type`, `authority`, `date-added`, `status`. Authority tiers: `official`, `primary`,
`peer-reviewed`, `institutional`, `reference`, `community`, `unverified`. Status values: `active`,
`stale`, `moved`, `dead`, `merged`, `superseded`, `archived`.

Entries live in Markdown as editable blocks:

```markdown
### Project Gutenberg
- id: src-0001
- url: https://www.gutenberg.org/
- category: general
- tags: books, public-domain, text
- source-type: archive
- authority: institutional
- added-by: seed
- date-added: 2025-02-11
- last-checked: 2026-09-30
- status: active
- archive-url: https://web.archive.org/web/*/gutenberg.org
- license: Public domain texts; site terms apply
- annotation: Starting point for public-domain books and full texts that can be quoted directly.
- related: -
```

`tags` and `related` are comma-separated in the file and become lists in memory. `-` means empty.
Unknown keys are preserved in place; malformed blocks are reported, never deleted.

### 6.3 Routing keywords

```json
{"version": "1.0.0",
 "intents": [{"id": "security-privacy",
              "signals": ["threat model", "vulnerability triage", "access review"],
              "boost_categories": [9],
              "boost_skills": ["Threat Model Facilitator"]}]}
```

`boost_categories` are 1-based category indexes. 24 intents ship, one per catalog category, so every
category is reachable by routing.

### 6.4 Brief template

Placeholders are written with double braces around a field name: `skill_name`, `skill_slug`,
`category`, `category_index`, `description`, `catalog`, `generated`, `trigger_phrases`,
`non_triggers`, `inputs`, `steps`, `outputs`, `approval_gates`, `verification`, `limits`,
`next_commands`. Pass a different file with `--template`; unknown fields are reported at the end of
the generated brief instead of being dropped.

---

## 7. Capability coverage map

| # | Capability | Command | Catalog skills it operationalises |
| --- | --- | --- | --- |
| 1 | Ingest plain text / Markdown / HTML catalogs | `parse` | Reference Catalog Builder, Reference Ingestion Assistant, Document Structure Extractor |
| 2 | Structural validation with stable codes | `validate` | Template Compliance Checker, Requirement Coverage Auditor |
| 3 | Explainable keyword search | `search` | Reference Library Navigator, Skill Discovery Router |
| 4 | Intent routing with editable signals | `route` | Skill Discovery Router, Source Priority Router, Multi-Agent Task Router |
| 5 | Implementation briefs | `plan` | Agent Capability Architect, Instruction Set Optimizer, Agent Safety Boundary Reviewer |
| 6 | Coverage and gap analysis | `coverage` | Reference Gap Finder, Knowledge Frontier Mapper, Requirement Coverage Auditor |
| 7 | Claim-by-source pre-screen | `evidence` | Evidence Matrix Builder, Claim Verification Analyst, Citation Context Checker |
| 8 | Reference library creation | `library build` | Reference Catalog Builder, Source Provenance Recorder |
| 9 | Staleness and link audit | `library audit` | Link Health Auditor, Knowledge Decay Auditor, Web Archive Locator |
| 10 | Non-destructive dedupe | `library dedupe` | Reference Deduplicator, Note Consolidation Assistant |
| 11 | Topic packs for progressive loading | `library package` | Topic Reference Packager, Context Pack Builder |
| 12 | Starter Skill Tool generation | `scaffold` | Agent Capability Architect, Agent Handoff Protocol Designer |
| 13 | Packaging contract verification | `check` | Artifact Inspection Agent, Quality Scorecard Assessor, Clean-State Reproducer |
| 14 | Self-verification | `selftest` | Golden Reference Maintainer, Regression Test Curator, Repair Loop Coordinator |

The remaining catalog entries (for example "Lab Protocol Author", "Game Economy Balancer") are
reachable through `search`, `route`, `plan` and `scaffold`. They are not implemented as domain
engines; see §1.

---

## 8. Safety and approval rules

**Approval is required, and enforced in code, for:**

| Action | Mechanism | Exit when missing |
| --- | --- | --- |
| `library dedupe --apply` (rewrites records) | `--approve` | 3 |
| Overwriting an existing library | `--approve --force` | 3 |
| Replacing a non-empty scaffold folder | `--approve --force` | 3 |
| Replacing an existing output file (`parse --out`, `plan --out`, reports) | `--force` | 3 |
| Replacing an existing ZIP | `--approve` | 3 |
| Any network access | `--online` (explicit, bounded) | offline by default |

Additional rules the tool follows:

* **Never delete history.** Dedupe marks records `merged` with a successor; audits only propose.
  Dead links stay with `status: dead`.
* **Validate before writing.** Library entries are schema-checked first; an invalid entry set writes
  nothing.
* **Atomic writes.** Outputs go to `<path>.tmp-partial` and are `os.replace`d; Ctrl-C removes the
  temporary file and exits 130, so no half-written artifact is left behind.
* **Bounded I/O.** 8 MiB input ceiling (`--max-bytes`), `--max-urls`/`--timeout`/`--retries` for
  network work, 500-file packaging ceiling.
* **No code execution from data.** Catalog and entry files are parsed as data only. The single
  subprocess call in the tool is `selftest`, which runs the shipped `tools-selftest.py` with the same
  interpreter.
* **No secrets.** No credentials, tokens or private endpoints exist in any file; the tool prints the
  point at which a human must supply approval, not credentials.
* **High-stakes content.** The plan generator always attaches approval gates to capabilities whose
  descriptions mention health, safety, legal, regulatory, financial, credential or personal-data
  work, and `evidence` refuses to declare support automatically.

---

## 9. Limits, timeouts, retries, cancellation, cleanup

| Limit | Value | Where to change |
| --- | --- | --- |
| Input text ceiling | 8 MiB | `--max-bytes` |
| Name length | 90 characters per skill name | `_plausible_name` in `tools-catalog-parser.py` |
| Search results | 10 default | `--limit` |
| Route results | 5 default | `--top` |
| Library audit staleness | 365 days | `--max-age-days` |
| Online URL checks | 25 URLs, 5 s timeout, 1 retry, 0.2 s spacing | `--max-urls`, `--timeout`, `--retries` |
| Package file ceiling | 500 files | `--max-files` |
| Package depth | 3 levels | `MAX_DEPTH` in `tools-skill-packager.py` |
| Zip compression | DEFLATE, single archive, no nesting | `archive_folder` |

Cancellation: Ctrl-C raises a handled interrupt, prints `interrupted: no partial outputs were kept`,
and exits 130. Recovery: rerun the same command; because writes are atomic and validation precedes
writes, a rerun is safe. `dedupe --apply` is idempotent for already-merged records (their canonical
URL is unchanged and the merge target is the same record).

---

## 10. Validation and tests actually performed

Environment: CPython 3.13.16 on Linux, standard library only, no network for the default run.

### 10.1 Commands executed

```bash
# bash, from the future skill root
python3 agent-skill-catalog/tools-skillcatalog.py --help
python3 agent-skill-catalog/tools-skillcatalog.py capabilities
python3 agent-skill-catalog/tools-skillcatalog.py capabilities --json
python3 agent-skill-catalog/tools-skillcatalog.py parse --input agent-skill-catalog/asset-catalog-240-skills.md --emit json
python3 agent-skill-catalog/tools-skillcatalog.py parse --input agent-skill-catalog/asset-catalog-240-skills.md --emit md
python3 agent-skill-catalog/tools-skillcatalog.py parse --input agent-skill-catalog/asset-catalog-240-skills.md --emit html
python3 agent-skill-catalog/tools-skillcatalog.py validate --strict
python3 agent-skill-catalog/tools-skillcatalog.py search --query "threat model"
python3 agent-skill-catalog/tools-skillcatalog.py route --intent "triage our vulnerability findings and check who has excess access"
python3 agent-skill-catalog/tools-skillcatalog.py plan --skill "Redaction and Privacy Reviewer"
python3 agent-skill-catalog/tools-skillcatalog.py plan --category 1 --out ./plans
python3 agent-skill-catalog/tools-skillcatalog.py coverage --goal "measure quantum decoherence in trapped ions"
python3 agent-skill-catalog/tools-skillcatalog.py library build --out ./library
python3 agent-skill-catalog/tools-skillcatalog.py library audit --library ./library --today 2026-11-09
python3 agent-skill-catalog/tools-skillcatalog.py library dedupe --library ./fixture-library --apply --approve
python3 agent-skill-catalog/tools-skillcatalog.py library package --library ./library --name security-review --out ./packs
python3 agent-skill-catalog/tools-skillcatalog.py evidence --claims ./claims.json --library ./library
python3 agent-skill-catalog/tools-skillcatalog.py scaffold --name topic-triage --from-skill "Support Intake Triage" --out ./build --zip ./build/topic-triage.zip
python3 agent-skill-catalog/tools-skillcatalog.py check --path ./build/topic-triage
python3 agent-skill-catalog/tools-skillcatalog.py check --zip ./build/topic-triage.zip
python3 agent-skill-catalog/tools-skillcatalog.py selftest
```

### 10.2 Results

* `tools-selftest.py` runs **72 checks**; all pass in the delivered package (see §10.3 for what each
  group proves). Run `python3 agent-skill-catalog/tools-skillcatalog.py selftest` to reproduce; the
  suite exits non-zero if any check fails.
* Bundled catalog: 24 categories, 240 skills, 0 validation errors, 0 warnings, detected format
  `catalog`.
* Markdown round trip: `parse --emit md` of the bundled catalog re-validates as 24 / 240 with 0
  errors.
* Generated scaffold: its own `verify` passes, its own `tools-selftest.py` reports 5/5, and the
  extracted ZIP re-runs `verify` successfully.
* `check --path agent-skill-catalog` passes on this folder itself (13 files: 6 tools, 1 knowledge,
  6 assets; 0 subfolders, 0 unresolved references).

### 10.3 What the suite proves

| Group | Checks |
| --- | --- |
| Help and capability list | `--help` exits 0 and lists commands; `capabilities --json` exposes ≥14 operations and the entry-point name |
| Ingestion | bundled catalog → 24/240 with detected format; copied HTML with classes/data attributes/`<style>` → 2 categories, 3 skills, clean names and descriptions; Markdown bullet catalog → 3 skills with the category name preserved; empty file, missing file and unstructured prose each exit 2 with actionable messages |
| Validation | bundled catalog clean; deliberately broken catalog (duplicate names, thin description) exits 1 under `--strict` with `SKILL_DUPLICATE`; the corrected file re-validates with exit 0 (recovery path) |
| Search | "threat model" ranks Threat Model Facilitator first; a different query returns a different result set; `--category 9` restricts results; a nonsense query exits 1 with a hint |
| Routing | a security intent routes to Vulnerability Triage Analyst and reports matched signals; a finance intent routes to Invoice Validation Assistant or Vendor Proposal Comparator; a nonsense intent falls below the confidence floor and exits 1 |
| Briefs | one brief written and inspected for required sections, for derived description content, for zero unresolved placeholders, and for a derived `personal-data` approval gate on a redaction capability; `--category 1` writes exactly 10 briefs; unknown skill exits 2; an existing output is protected (exit 3) and `--force` replaces it |
| Coverage | 24/240 counts; a goal containing out-of-catalog terms produces gap entries with nearest terms |
| Reference library | build writes category files plus `archive-index.md` and `source-schema.md`; rebuild without approval exits 3; invalid entries exit 1 and create nothing; audit flags `STALE_ENTRY` and `STATUS_INACTIVE`, is offline by default, and writes a Markdown repair queue; the fixture library (canonical duplicate with tracking parameters, near duplicate, stale dates, dead link) builds, dedupes in dry run without writing, refuses `--apply` without `--approve` (exit 3), then applies with approval and leaves the tombstone in place; topic pack writes an index with progressive-loading instructions and honours the category filter |
| Evidence | matrix built for 2 claims with ≥2 rows, disclaimer requires human judgement, Markdown and CSV written, missing library exits 2 |
| Scaffold | folder contains exactly the four expected prefixed files, structure check clean, ZIP valid with the folder at its root; the generated tool's `--help`, `capabilities`, `verify`, missing-input rejection (exit 2) and its own 5/5 self-test all pass |
| Package checks | scaffolded folder and ZIP validate; a folder containing `SKILL.md`, a subfolder, an unprefixed file and no knowledge file is rejected with `SKILL_MD_PRESENT`, `SUBFOLDER_FORBIDDEN`, `FILENAME_PREFIX`, `KNOWLEDGE_FILE`; this Skill Tool folder passes its own check |
| Clean room | the folder copied beside a test root `SKILL.md` runs `capabilities`, `search` and `check` using **relative paths only** |
| ZIP round trip | no nested archives, folder directly at the archive root, extracted tool re-runs |

### 10.4 Checked but not claimed

* `library audit --online` **was executed against live URLs** during validation:
  `--max-urls 2 --timeout 6 --retries 0` returned `ok 200` for the SEC EDGAR endpoint and
  `ok 206` for the W3C WCAG quick reference, confirming the request path, the range header and the
  result states. It is error-tolerant by construction: when an environment has no egress the same
  command returns `LINK_UNREACHABLE` findings and still exits 0/1 normally, so the default test suite
  runs offline. Reachability of any individual host remains an environment/platform property that
  this package cannot guarantee.
* No domain capability (for example "Lab Protocol Author") is claimed as implemented; see §1.
* No browser rendering of the `--emit html` output was performed; the HTML is a self-contained
  document with inline CSS and no external resources, and it was inspected as text.

---

## 11. Known limitations

* **Evidence pre-screen is lexical.** It measures token overlap only. It cannot detect contradiction,
  methodology quality, jurisdiction or date relevance. The reviewer columns are intentionally blank.
* **Search and routing are lexical, not semantic.** Synonyms outside the signal map are missed; extend
  `agent-skill-catalog/asset-intent-keywords.json` instead of expecting embeddings.
* **The parser is heuristic.** Exotic catalog layouts (nested tables, multi-column PDF text, name and
  description on the same line without a separator) may need `--format` or a small asset edit. Slash
  separated names, unusual numbering (`1)`, `1.1.1`) and non-Latin names are handled only as far as
  the tests cover.
* **`library build` derives file names from entry categories.** Unusual category labels become their
  slugged form; nothing maps them onto the 12 suggested library files automatically.
* **Online checks are single-request HEAD-less GETs** with a 2 KB range; they cannot detect
  JavaScript-only paywalls or soft-404 pages that answer 200.
* **No connectors.** No Notion/Slack/GitHub/Drive integration exists in this Skill Tool; sources are
  supplied as files. A connector would need its own credentials, scopes and approval policy.
* **ZIP validation inspects names and integrity**, not file contents; run `check --path` after
  extracting for the full contract.

---

## 12. Troubleshooting and recovery

| Symptom | Cause | Action |
| --- | --- | --- |
| `error: input is empty` (exit 2) | Empty file or stdin | Supply the catalog text, or use `--input -` with content on stdin. |
| `no skill entries found (detected format: prose)` | Text has no `Skill Name:` markers, headings or usable list items | Pass `--format text`/`markdown`/`list`, or restructure the paste. `parse --stats` shows what was detected. |
| `required module missing: tools-...` | A prefixed file was renamed, moved or deleted | Restore the exact filename; no module may live in a subfolder. |
| Only one category parsed from an HTML paste | Category headings had no numbering and no following skill marker | Add `## Category` headings, or pass `--format markdown` after converting. |
| Names include numbering or markup | Input uses an unusual list marker | Check `--emit text`; names are normalised by stripping numbering, `**`, backticks and tags. |
| `already exists ... pass --force` (exit 3) | Output protection | Add `--force` (files) or `--approve` (libraries, scaffolds, ZIPs) after reviewing what will be replaced. |
| `library audit` shows many `STALE_ENTRY` | Old `last-checked` values | Verify each URL, then update `last-checked` and `status`; or raise `--max-age-days` for archival libraries. |
| `LINK_UNREACHABLE` for every URL | No outbound network in the environment | This is an environment limit, not a data error. Re-run without `--online`, or from a host with egress. |
| `check` reports `REFERENCE_UNRESOLVED` | A folder-qualified reference names a missing file | Fix the reference or the filename; keep references relative to the future skill root. |
| `check` reports `SUBFOLDER_FORBIDDEN` | A subfolder was created (often `__pycache__`) | Remove it. The entry point disables bytecode writing; run Python with `-B` if another tool creates it. |
| Generated `run` exits 3 | Starter body not implemented | Implement `cmd_run` in the generated tool and extend its self-test. |

Recovery pattern used throughout: validate first, write atomically, keep history, and rerun the same
command after fixing the input.

---

## 13. Extension and maintenance

* **New catalog content** — append entries to `agent-skill-catalog/asset-catalog-240-skills.md` in the
  canonical format, then run `validate --strict` and `coverage`.
* **New routing intent** — add an object to `intents` in
  `agent-skill-catalog/asset-intent-keywords.json` with signals, `boost_categories` (1-based) and
  `boost_skills`, then verify with `route --json`.
* **New approval lexicon entry** — extend `APPROVAL_LEXICON` in
  `agent-skill-catalog/tools-catalog-planner.py`; add a matching human-readable action in
  `APPROVAL_ACTIONS` so briefs stay specific.
* **New input lexicon entry** — extend `INPUT_LEXICON` the same way; briefs pick it up immediately.
* **New brief section** — add a placeholder to `agent-skill-catalog/asset-plan-template.md` and a
  matching key in `build_plan`; unresolved placeholders are reported rather than dropped.
* **New library category** — add entries with the new `category` value; `library build` creates the
  file and indexes it.
* **New packaging rule** — add it in `validate_package` in
  `agent-skill-catalog/tools-skill-packager.py`, then add a case to `tools-selftest.py` in the same
  change so the rule is proven.
* **Maintenance cadence** — run `selftest` after every edit; run `check --path` before delivering;
  re-run `library audit` on each library review cycle and record dates in the entries.

Version: 1.0.0. Catalog data: 24 categories, 240 skills.

---

## 14. Packaging compatibility

* This folder contains **no** `SKILL.md`; the parent package owns the single root `SKILL.md`.
* No subfolders, no nested archives, no unprefixed files: 6 `tools-` files, 1 `knowledge-` file,
  6 `asset-` files = **13 files**, all at depth 1.
* The folder can be copied directly beside a parent `SKILL.md` without modification; the suite proves
  this in a clean root containing a test `SKILL.md`, using relative paths only.
* The delivery ZIP contains the folder `agent-skill-catalog/` directly at its root, with no wrapper
  folder and no archive inside it.
