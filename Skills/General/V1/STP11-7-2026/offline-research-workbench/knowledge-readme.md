# Offline Research Workbench — knowledge base

One Skill Tool for the whole source-to-publication chain, assembled from the 288 skills in the
*Emmres Full Offline Research, Writing and Translation Skill Catalog*. Every catalog skill is
mapped to a runnable command in this folder; the catalog category is the umbrella, so you never
choose a tool. Everything runs locally with the Python standard library only: no network calls,
no API keys, no third-party packages, no telemetry.

- **Entry point:** `tools-offline-research.py`
- **Capabilities:** 288 catalog skills across 24 categories, expressed as 162 commands (160 in 16 working groups plus `capabilities` and `apply`)
- **State:** one SQLite workspace (default `rwt-workspace/`, created on first run) holding sources, segments, claims, citations, translation memory, jobs, snapshots and a hash-chained audit log
- **Deliverables:** Markdown/CSV/TMX/JSON/JSONL files written under `outputs/`, plus ZIP packages under `outputs/`

## 1. Requirements and quick start

Python 3.10 or newer, no installation step and no dependencies. Copy the folder anywhere, including
a path with spaces, and run from the folder:

```bash
python3 tools-offline-research.py --help
python3 tools-offline-research.py capabilities --list
python3 tools-offline-research.py eval health
```

The first command creates `rwt-workspace/` in the current directory and initialises the database.
Point everything at a different location with `-w` (or `--workspace`), and load an alternative
policy file with `--config`:

```bash
python3 tools-offline-research.py -w ~/research/acme corpus list
python3 tools-offline-research.py --config asset-default-config.json eval health
```

All input paths, output paths and document arguments are resolved relative to the workspace first and
then relative to the current directory, so both of these work:

```bash
python3 tools-offline-research.py -w ws corpus ingest --input asset-fixture-corpus-a.md --doctype journal-article
python3 tools-offline-research.py -w ws corpus ingest --input /home/me/docs/paper.md --doctype journal-article
```

Reusable files that ship with the tool (`asset-*.json`, `asset-fixture-*.md`) are always resolved
against the tool folder, so the fixtures work from any workspace.

## 2. Files in this Skill Tool

| File | Role |
| --- | --- |
| `knowledge-readme.md` | This document: capabilities, command reference, configuration, limits, tests, limitations |
| `tools-offline-research.py` | CLI entry point: global options, `capabilities`, `apply`, 16 group dispatchers, error envelope, exit codes |
| `tools-rwt-store.py` | Workspace, SQLite schema, context, config loading, audit chain, approval gate, path/ID helpers, corpus (incl. gated delete), registry, jobs, state and backup handlers |
| `tools-rwt-adapters.py` | Local adapter registry and subprocess contract (MT, embedding, OCR, ASR, extract, rerank); validates and times out adapters, never invents output |
| `tools-rwt-retrieval.py` | Segmentation index, BM25/FTS5 search, hybrid scoring, facets, query expansion, boolean strategy, recall benchmarks |
| `tools-rwt-extract.py` | Claim, entity, date, quantity, methodology, funding, transcript and OCR-evaluation extraction; evidence linking |
| `tools-rwt-citation.py` | Reference extraction, 7 citation styles, in-text checking, reference validation, citation graph, cache |
| `tools-rwt-verify.py` | Claim verification, corroboration, quotations, number audit, chronology, contradiction, authority, freshness, provenance, abstention, evidence chains |
| `tools-rwt-synthesis.py` | Protocol/strategy, screening and PRISMA flow, evidence tables, risk of bias, narrative synthesis, meta-analysis (fixed/random), robustness, gap analysis |
| `tools-rwt-writing.py` | Outline, evidence-linked drafting, readability, style, redundancy, flow, summary, conclusion boundary, tone, consistency, proofreading, tracked revision |
| `tools-rwt-translation.py` | Terminology, translation memory (import/match/export/clean), QA, scoring, alignment, MT dispatch, language ID, locale, script checks, transliteration, pretranslation |
| `tools-rwt-guard.py` | Quotation limits, licence checks, similarity and paraphrase risk, redaction, injection/document scanning, export gate, retention, deletion, audit verification |
| `tools-rwt-workflow.py` | Projects, staged workflow, baselines and change control, review queues, approvals, readiness, preflight, publishing, packaging, handoff, audit report, postmortem |
| `tools-rwt-eval.py` | Health checks, clean-state audit, leakage scan, benchmark suite runner, regression release gate, acceptance checklist |
| `tools-rwt-selftest.py` | Executable test suite: drives the real CLI in a temporary workspace and asserts outcomes for every capability family (63 checks) |
| `asset-capability-registry.json` | Machine-readable map of all 288 catalog skills: id, name, description, `op`, default arguments, notes |
| `asset-default-config.json` | Default policy: limits, languages, search, quoting, licensing, approval gates, adapter commands, translation, evaluation thresholds, privacy patterns, delivery |
| `asset-access-policy.json` | Access, sensitivity, retention and export rules used by the guard group |
| `asset-style-guide.json` | House style rules consumed by `writing style` and `writing proofread` |
| `asset-benchmark-suite.json` | Reference benchmark suite for `eval benchmark` (factcheck, retrieval, citation, translation, OCR, readability, numbers, style) |
| `asset-fixture-corpus-a.md` | Fixture source: peer-reviewed journal article, CC BY 4.0 |
| `asset-fixture-corpus-b.md` | Fixture source: news report, all rights reserved (used for rights and similarity checks) |
| `asset-fixture-bilingual.json` | Clean English/Filipino bilingual pair for QA |
| `asset-fixture-bilingual-errors.json` | Deliberately defective pair (number, terminology and omission errors) |
| `asset-fixture-ocr-reference.txt`, `asset-fixture-ocr-hypothesis.txt` | OCR reference/hypothesis pair with one character and one word difference |
| `asset-fixture-effects.json` | Five study effect sizes for meta-analysis |
| `asset-fixture-quality.json` | Study quality ratings for robustness scenarios |
| `asset-fixture-terms.csv` | Sample terminology base |
| `asset-fixture-tm.csv` | Sample translation memory with an exact match |

## 3. Command line contract

```
python3 tools-offline-research.py [--workspace/-w DIR] [--config JSON] [--actor NAME] [--json] <group> <command> [options]
```

Global options may appear before or after the group name.

| Option | Effect |
| --- | --- |
| `--workspace`, `-w` | Workspace directory (default `rwt-workspace/`). Created if absent |
| `--config` | Alternative config JSON; defaults to `asset-default-config.json` next to the entry point |
| `--actor` | Actor recorded in the audit log (default `operator`) |
| `--json` | Machine-readable envelope instead of the human summary |

Every command returns the same envelope, in both modes:

```json
{"status": "ok", "message": "...", "exit_code": 0, "...": "command specific fields"}
```

Errors are structured and never leak tracebacks unless `RWT_DEBUG=1`:

```json
{"status": "error", "message": "unknown source: src-missing", "type": "ToolError", "hint": "...", "exit_code": 2}
```

### Exit codes

| Code | Meaning | Typical cause |
| --- | --- | --- |
| 0 | Success | Command completed; advisories may still be attached |
| 2 | Usage or input error | Missing/invalid argument, unknown ID, missing file, failed validation |
| 3 | Approval required | A gated action was attempted without `--approve --approver` |
| 4 | Adapter unavailable | A local engine (MT, OCR, ASR, embed, rerank, extract) is not configured |
| 5 | Internal error | Unexpected failure, reported with type and hint |

Exit codes are also present in the JSON payload as `exit_code`, so scripts can branch on either.

## 4. Capabilities and the catalog mapping

```bash
python3 tools-offline-research.py capabilities --list                        # all 288 skills, grouped by category
python3 tools-offline-research.py capabilities --category verification       # one category
python3 tools-offline-research.py capabilities --skill evidence-synthesis    # one skill: op, arguments, notes
python3 tools-offline-research.py capabilities --search "translation memory" # substring search
python3 tools-offline-research.py capabilities --list --json                 # machine-readable registry view
```

Each skill record carries the catalog number, id, name, description, the command it maps to (`op`),
default arguments and any caveat. `apply` runs a skill by id with defaults applied:

```bash
python3 tools-offline-research.py -w ws apply --skill offline-scholarly-database --input asset-fixture-corpus-a.md --set doctype=journal-article
python3 tools-offline-research.py -w ws apply --skill translation-memory --set action=match --set source="Corrections are published with a DOI."
```

`--set key=value` overrides a default (repeatable, `true`/`false` become flags), and `--dry-run`
prints the resolved command without running it. This is the recommended way to script the catalog:
one id, no menu.

## 5. Validated walkthrough

The sequence below was executed end to end on the shipped fixtures; every command returned exit 0.

```bash
W="-w ws"
python3 tools-offline-research.py $W corpus ingest --input asset-fixture-corpus-a.md --doctype journal-article \
  --language en --license cc-by-4.0 --publisher "Philippine Journal of Scholarly Publishing" --issued 2024-03-05 \
  --peer-status peer-reviewed --copy
python3 tools-offline-research.py $W corpus ingest --input asset-fixture-corpus-b.md --doctype news \
  --language en --license all-rights-reserved --publisher "Daily Wire" --issued 2024-03-05 \
  --peer-status editorially-reviewed --jurisdiction PH --copy
python3 tools-offline-research.py $W corpus integrity          # hashes and database verified
python3 tools-offline-research.py $W search index              # 3 segments indexed via sqlite-fts5
python3 tools-offline-research.py $W search query --query "retraction investigation" --facet
python3 tools-offline-research.py $W extract claims --store    # claims stored with segment evidence
python3 tools-offline-research.py $W verify claim --claim "The median interval was 148 days."   # -> supported
python3 tools-offline-research.py $W verify quote --quote "Peer review records are retained for ten years" --source-id <id>
python3 tools-offline-research.py $W citation extract --store
python3 tools-offline-research.py $W synthesis screen --decision include --ref <id> --reviewer a.reviewer
python3 tools-offline-research.py $W synthesis meta --effects asset-fixture-effects.json         # pooled -0.398306, I2 0.0
python3 tools-offline-research.py $W writing outline --question "How quickly do journals retract papers?"
python3 tools-offline-research.py $W writing draft --section Findings --output draft-findings.md
python3 tools-offline-research.py $W translate tm --action import --input asset-fixture-tm.csv
python3 tools-offline-research.py $W translate qa --input asset-fixture-bilingual-errors.json     # 3 critical findings
python3 tools-offline-research.py $W guard similarity --input asset-fixture-corpus-b.md
python3 tools-offline-research.py $W workflow ready --draft outputs/draft-findings.md
python3 tools-offline-research.py $W workflow package --output report.zip --draft outputs/draft-findings.md --title "Retraction intervals"
python3 tools-offline-research.py $W eval health
python3 tools-offline-research.py $W eval benchmark --suite asset-benchmark-suite.json --name reference   # 12/12 pass
```

## 6. Command reference

160 group commands are listed below in addition to `capabilities`, `apply` and the global options.
Option values marked `?` are optional; everything else is required. Choices are shown in brackets.

#### `corpus`

Corpus ingestion, installation, integrity, dedupe, licensing, snapshots

- **`corpus ingest`** — Ingest a text-bearing file or directory into the workspace corpus
  - `--input INPUT_PATH` · `--title TITLE`? · `--doctype DOCTYPE`? · `--language LANGUAGE`? · `--license LICENSE`? · `--publisher PUBLISHER`? · `--issued ISSUED`? · `--jurisdiction JURISDICTION`? · `--uri URI`? · `--copy` · `--peer-status PEER_STATUS`? · `--max-words MAX_WORDS`?
- **`corpus list`** — List corpus sources
  - `--doctype DOCTYPE`? · `--language LANGUAGE`? · `--limit LIMIT`?
- **`corpus show`** — Show one source, its versions and first segments
  - `--source-id SOURCE_ID`
- **`corpus install`** — Install a checksummed corpus bundle (zip + corpus-manifest.json)
  - `--bundle BUNDLE` · `--confirm`
- **`corpus package`** — Package the workspace corpus into a checksummed bundle
  - `--output OUTPUT` · `--name NAME`? · `--note NOTE`?
- **`corpus dedupe`** — Find exact and near-duplicate sources
  - `--threshold THRESHOLD`?
- **`corpus integrity`** — Audit files, hashes, database integrity and orphans
- **`corpus repair-encoding`** — Repair mojibake and normalize Unicode in a text file
  - `--input INPUT_PATH` · `--output OUTPUT`?
- **`corpus strip-boilerplate`** — Remove navigation/boilerplate and normalize to plain text
  - `--input INPUT_PATH` · `--output OUTPUT`?
- **`corpus coverage`** — Coverage audit by type, language, license and year
- **`corpus version-link`** — Link a newer version to the version it supersedes
  - `--source-id SOURCE_ID` · `--version-label VERSION_LABEL` · `--supersedes SUPERSEDES`? · `--note NOTE`?
- **`corpus update`** — Apply an incremental add/replace/delete corpus manifest
  - `--manifest MANIFEST_PATH` · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`corpus delete`** — Delete a source and its derived records (approval gated)
  - `--source-id SOURCE_ID` · `--approve` · `--approver APPROVER`? · `--note NOTE`? · `--keep-file`
- **`corpus snapshot`** — Create a verifiable corpus snapshot
  - `--note NOTE`?
- **`corpus rollback`** — Restore a previous snapshot (approval gated)
  - `--snapshot-id SNAPSHOT_ID` · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`corpus structure`** — Extract headings, footnotes, captions and page breaks
  - `--source-id SOURCE_ID`
- **`corpus license`** — Set license and quotation rights for a source
  - `--source-id SOURCE_ID` · `--license LICENSE`? · `--max-quote-words MAX_QUOTE_WORDS`? · `--redistribute`
- **`corpus usage`** — Licensed-content usage meter

#### `registry`

Model, corpus, prompt and tool registries

- **`registry add`** — Register or update a registry entry
  - `--kind KIND` · `--key KEY` · `--value VALUE` · `--version VERSION`?
- **`registry list`** — List registry entries
  - `--kind KIND`

#### `jobs`

Bounded local job scheduler

- **`jobs add`** — Queue a scheduled job (integrity|coverage|reindex|backup|freshness-report)
  - `--name NAME` · `--kind KIND` · `--due DUE`? · `--payload PAYLOAD`?
- **`jobs list`** — List queued/running jobs
  - `--status STATUS`?
- **`jobs run-due`** — Run due jobs with retry and per-job failure isolation
  - `--max-jobs MAX_JOBS`?

#### `state`

Durable research state and checkpoints

- **`state save`** — Save a named state blob
  - `--key KEY` · `--value VALUE`
- **`state load`** — Load a named state blob
  - `--key KEY`
- **`state checkpoint`** — Snapshot the workspace and record a checkpoint
  - `--label LABEL`

#### `backup`

Backup, restore verification and offline dependency mirror

- **`backup restore-verify`** — Extract a snapshot to a temp dir and verify every checksum
  - `--snapshot-id SNAPSHOT_ID`?
- **`backup mirror`** — Inventory a local offline dependency mirror
  - `--mirror-dir MIRROR_DIR`?

#### `adapters`

Inspect, test and use configured local model/tool adapters

- **`adapters check`** — Report which local adapters are configured and installed
- **`adapters run`** — Invoke a configured adapter with a JSON payload
  - `--kind KIND` (mt|embed|ocr|asr|extract|rerank) · `--payload PAYLOAD`
- **`adapters extract-text`** — Extract text from PDF/Office/EPUB via the local extract adapter
  - `--input INPUT_PATH` · `--output OUTPUT`?

#### `search`

Local full-text search, hybrid retrieval, reranking and query planning

- **`search index`** — Rebuild the FTS5 index over local segments
  - `--no-rebuild`
- **`search query`** — Full-text search with filters, facets and highlighting
  - `--query QUERY` · `--limit LIMIT`? · `--doctype DOCTYPE`? · `--language LANGUAGE`? · `--source-id SOURCE_ID`? · `--year-from YEAR_FROM`? · `--year-to YEAR_TO`? · `--facet` · `--explain`
- **`search hybrid`** — Blend BM25 and dense similarity with source diversification
  - `--query QUERY` · `--limit LIMIT`? · `--alpha ALPHA`? · `--no-diversify` · `--doctype DOCTYPE`? · `--language LANGUAGE`?
- **`search embed`** — Embed text locally (adapter or hashed-TF baseline)
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`search rerank`** — Rerank candidate passages for a query
  - `--query QUERY` · `--passages PASSAGES`? · `--input INPUT_PATH`? · `--top-k TOP_K`?
- **`search decompose`** — Decompose a research question into concepts and facets
  - `--question QUESTION`
- **`search boolean`** — Build a reproducible boolean search strategy
  - `--concepts CONCEPTS` · `--mode MODE`? (AND|OR) · `--synonyms SYNONYMS`? · `--year-from YEAR_FROM`? · `--year-to YEAR_TO`? · `--language LANGUAGE`?
- **`search expand`** — Expand a query with approved terminology and variants
  - `--query QUERY` · `--target-lang TARGET_LANG`?
- **`search recall`** — Run a retrieval recall benchmark from a JSON case file
  - `--cases CASES_PATH` · `--limit LIMIT`?
- **`search explain`** — Explain why a segment matched a query
  - `--query QUERY` · `--segment-id SEGMENT_ID`

#### `extract`

Extraction of entities, claims, events, quantities, dates, media and tables

- **`extract entities`** — Extract people, organizations, places, laws, standards, identifiers, dates, quantities
  - `--store` · `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract link`** — Link an entity mention to stable local identities
  - `--mention MENTION` · `--limit LIMIT`?
- **`extract relations`** — Extract typed relations (affiliation, funding, amendment, citation, causation)
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract claims`** — Extract and classify claims with source coordinates
  - `--store` · `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract evidence-link`** — Link a stored claim to supporting/opposing passages
  - `--claim-id CLAIM_ID` · `--limit LIMIT`?
- **`extract events`** — Extract events with actors, time, place and sequence
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract dates`** — Normalize absolute and relative date expressions
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`? · `--reference REFERENCE`?
- **`extract quantities`** — Extract values, units, comparators and denominators
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract methodology`** — Extract design, sampling, instruments, analysis and limitations cues
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract funding`** — Extract funders, grant ids and conflict-of-interest statements
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract definitions`** — Extract explicit definitions with context
  - `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`extract review`** — Approve, reject, merge, split or correct extracted records
  - `--kind KIND` (entity|claim|relation) · `--ref REF` · `--decision DECISION` (approve|reject|merge|split|correct) · `--note NOTE`?
- **`extract transcript`** — Parse SRT/WebVTT into cleaned, timecoded cues
  - `--input INPUT_PATH` · `--output OUTPUT`? · `--no-clean`
- **`extract timecode-cite`** — Build a quotable transcript segment with exact timecodes
  - `--source-id SOURCE_ID` · `--start-ms START_MS` · `--end-ms END_MS`? · `--output OUTPUT`?
- **`extract ocr-eval`** — Compute CER/WER for OCR or ASR output and optionally queue review
  - `--reference REFERENCE_PATH` · `--hypothesis HYPOTHESIS_PATH` · `--queue-review`
- **`extract tables`** — Parse markdown/CSV tables with captions and notes
  - `--input INPUT_PATH` · `--output OUTPUT`?
- **`extract figure-alt`** — Generate accessible alt text and long description from a figure spec
  - `--spec SPEC_PATH` · `--output OUTPUT`?

#### `citation`

Citation extraction, reference styles, identifier cache, graphs, bibliography QA

- **`citation extract`** — Extract inline, numeric, footnote, legal and standard citations with context
  - `--store` · `--source-id SOURCE_ID`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`citation format`** — Format and validate references in a supported style
  - `--ref REF`? · `--ref-file REF_FILE`? · `--style STYLE`? · `--output OUTPUT`? · `--no-sort`
- **`citation normalize`** — Normalize bibliographic metadata
  - `--ref REF`
- **`citation cache`** — Local identifier resolution cache (get|put|list)
  - `--action ACTION`? (get|put|list) · `--identifier IDENTIFIER`? · `--payload PAYLOAD`?
- **`citation validate`** — Bibliography completeness and consistency check
  - `--source-id SOURCE_ID`? · `--text-path TEXT_PATH`?
- **`citation references`** — Parse a reference list into structured records
  - `--input INPUT_PATH` · `--output OUTPUT`?
- **`citation graph`** — Build the local citation graph
  - `--source-id SOURCE_ID`? · `--output OUTPUT`?
- **`citation selfcitation`** — Flag concentrated self/venue citation behaviour
  - `--author AUTHOR`? · `--venue VENUE`?
- **`citation disambiguate`** — Group author/venue name variants for human resolution
  - `--kind KIND`? (author|venue) · `--name NAME`?

#### `verify`

Claim verification, corroboration, quotation and number checks, provenance, freshness

- **`verify claim`** — Verify one claim against local evidence
  - `--claim CLAIM` · `--limit LIMIT`? · `--min-coverage MIN_COVERAGE`?
- **`verify corroborate`** — Separate independent corroboration from repeated text
  - `--claim CLAIM` · `--limit LIMIT`? · `--similarity-cap SIMILARITY_CAP`?
- **`verify quote`** — Verify quotation wording, context and omissions
  - `--quote QUOTE` · `--source-id SOURCE_ID`? · `--context-words CONTEXT_WORDS`?
- **`verify numbers`** — Recompute stated percentages, ratios and unit conversions
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`verify chronology`** — Find impossible sequences, anachronisms and date confusion
  - `--source-id SOURCE_ID`?
- **`verify contradiction`** — Map incompatible claims with source quality
  - `--topic TOPIC` · `--limit LIMIT`?
- **`verify authority`** — Score source authority from metadata
  - `--source-id SOURCE_ID`
- **`verify freshness`** — Score staleness risk and update actions
  - `--source-id SOURCE_ID`? · `--as-of AS_OF`?
- **`verify provenance`** — Check provenance completeness per source
  - `--source-id SOURCE_ID`?
- **`verify chain`** — Validate file -> segment -> claim -> citation lineage
  - `--claim-id CLAIM_ID`
- **`verify abstain`** — Decide answer/qualify/escalate/abstain for a claim
  - `--claim CLAIM` · `--min-evidence MIN_EVIDENCE`?

#### `synthesis`

Literature review planning, screening, PRISMA, evidence tables and synthesis

- **`synthesis plan`** — Create a reproducible review plan
  - `--question QUESTION` · `--scope SCOPE`? · `--databases DATABASES`? · `--date-from DATE_FROM`? · `--date-to DATE_TO`? · `--languages LANGUAGES`? · `--source-types SOURCE_TYPES`? · `--method METHOD`? · `--output OUTPUT`?
- **`synthesis strategy`** — Build a systematic search strategy per database
  - `--concepts CONCEPTS` · `--databases DATABASES`? · `--vocab VOCAB`?
- **`synthesis screen`** — Record an inclusion/exclusion decision
  - `--decision DECISION` (include|exclude|maybe) · `--ref REF` · `--reason REASON`? · `--stage STAGE`? · `--reviewer REVIEWER`? · `--no-blind`
- **`synthesis prisma`** — Compute PRISMA flow counts from local records
- **`synthesis evidence-table`** — Build a traceable evidence table
  - `--output OUTPUT`? · `--format FMT`? (md|csv) · `--source-ids SOURCE_IDS`?
- **`synthesis risk-of-bias`** — Record a domain-level risk-of-bias judgement
  - `--ref REF` · `--judgements JUDGEMENTS` · `--rationale RATIONALE`? · `--reviewer REVIEWER`?
- **`synthesis narrative`** — Group evidence by direction without vote counting
  - `--topic TOPIC` · `--limit LIMIT`?
- **`synthesis meta`** — Pool effect sizes (fixed or random effects)
  - `--effects EFFECTS_PATH` · `--model MODEL`? (fixed|random) · `--output OUTPUT`?
- **`synthesis gaps`** — Map coverage gaps across dimensions
- **`synthesis robustness`** — Run sensitivity analysis on pooled estimates
  - `--effects EFFECTS_PATH` · `--quality QUALITY_PATH`?
- **`synthesis update-check`** — Compare claim snapshots for living-review updates
  - `--previous PREVIOUS_CLAIMS_PATH` · `--current CURRENT_CLAIMS_PATH`

#### `writing`

Drafting scaffolds, editing, style, readability, revisions and boundary checks

- **`writing outline`** — Build an evidence-mapped outline
  - `--question QUESTION` · `--kind KIND`? (academic|technical|policy|legal|plain) · `--source-ids SOURCE_IDS`?
- **`writing draft`** — Assemble an evidence-bound section skeleton from stored claims
  - `--section SECTION` · `--source-ids SOURCE_IDS`? · `--max-claims MAX_CLAIMS`? · `--style STYLE`? · `--output OUTPUT`?
- **`writing readability`** — Measure readability and structure
  - `--threshold-grade THRESHOLD_GRADE`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`writing style`** — Lint for weasel words, overclaims, passives and house style
  - `--profile PROFILE`? · `--max-findings MAX_FINDINGS`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`writing redundancy`** — Find repeated sentences and paragraphs
  - `--threshold THRESHOLD`? · `--text TEXT`? · `--input INPUT_PATH`?
- **`writing flow`** — Audit premises, transitions and causal language
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`writing revise`** — Produce a tracked revision with per-edit reasons
  - `--original ORIGINAL_PATH` · `--revised REVISED_PATH` · `--output OUTPUT`?
- **`writing boundary`** — Check conclusion scope against the evidence
  - `--draft DRAFT_PATH` · `--evidence-max-year EVIDENCE_MAX_YEAR`?
- **`writing summary`** — Create a source-linked extractive summary
  - `--input INPUT_PATH` · `--words WORDS`? · `--output OUTPUT`?
- **`writing tone`** — Check register against a target tone
  - `--target TARGET`? (formal|neutral|cautious|persuasive|instructional|conversational) · `--text TEXT`? · `--input INPUT_PATH`?
- **`writing consistency`** — Check abbreviations, units, numbering and references
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`writing proofread`** — Final proofreading pass for placeholders and typography
  - `--text TEXT`? · `--input INPUT_PATH`?

#### `translate`

Terminology, translation memory, alignment, QA, MT orchestration and locale checks

- **`translate terms`** — Manage the terminology base
  - `--action ACTION`? (add|list|get) · `--term TERM`? · `--lang LANG`? · `--definition DEFINITION`? · `--equivalents EQUIVALENTS`? · `--forbidden FORBIDDEN`? · `--domain DOMAIN`? · `--owner OWNER`?
- **`translate term-extract`** — Extract candidate terms for expert validation
  - `--input INPUT_PATH` · `--language LANGUAGE`? · `--min-freq MIN_FREQ`? · `--max-terms MAX_TERMS`?
- **`translate term-check`** — Check terminology compliance between source and target
  - `--source SOURCE` · `--target TARGET` · `--src-lang SRC_LANG`? · `--tgt-lang TGT_LANG`?
- **`translate tm`** — Manage the translation memory (add|list|import|export|match)
  - `--action ACTION`? (add|list|import|export|match|clean) · `--source SOURCE`? · `--target TARGET`? · `--src-lang SRC_LANG`? · `--tgt-lang TGT_LANG`? · `--input INPUT_PATH`? · `--output OUTPUT`? · `--domain DOMAIN`? · `--threshold THRESHOLD`? · `--limit LIMIT`? · `--project PROJECT`? · `--apply` · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`translate align`** — Align bilingual sentences into translation units
  - `--source-file SOURCE_PATH` · `--target-file TARGET_PATH` · `--src-lang SRC_LANG`? · `--tgt-lang TGT_LANG`? · `--output OUTPUT`? · `--add-to-tm`
- **`translate qa`** — Run mechanical translation QA over a bilingual file
  - `--input INPUT_PATH` · `--output OUTPUT`? · `--fail-on FAIL_ON`? (minor|major|critical)
- **`translate score`** — Compute the severity-weighted language score
  - `--input INPUT_PATH` · `--output OUTPUT`? · `--human-errors HUMAN_ERRORS`?
- **`translate mt`** — Translate locally through the configured MT adapter (or TM prefill)
  - `--input INPUT_PATH` · `--src-lang SRC_LANG`? · `--tgt-lang TGT_LANG`? · `--output OUTPUT`? · `--tm-only` · `--domain DOMAIN`? · `--max-segments MAX_SEGMENTS`?
- **`translate backtranslate`** — Back-translate high-risk segments and compare
  - `--input INPUT_PATH` · `--src-lang SRC_LANG`? · `--tgt-lang TGT_LANG`?
- **`translate langid`** — Identify language, script and mixed-language spans
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`translate locale`** — Check locale-specific numbers, dates and currency
  - `--text TEXT`? · `--input INPUT_PATH`? · `--locale LOCALE`?
- **`translate transliterate`** — Romanize Cyrillic/Greek text (other scripts need an approved standard)
  - `--text TEXT`? · `--input INPUT_PATH`?
- **`translate plan`** — Create a translation project plan
  - `--target-lang TARGET_LANG` · `--src-lang SRC_LANG`? · `--domain DOMAIN`? · `--files FILES`? · `--risk RISK`? (standard|high)
- **`translate pretranslate`** — Source-side pre-translation editing checks
  - `--input INPUT_PATH` · `--src-lang SRC_LANG`?
- **`translate query`** — Record, list or resolve translation queries
  - `--action ACTION`? (add|list|resolve) · `--question QUESTION`? · `--resolution RESOLUTION`? · `--owner OWNER`?
- **`translate script-check`** — RTL, CJK and complex-script typography checks
  - `--input INPUT_PATH`? · `--text TEXT`? · `--lang LANG`?
- **`translate publish-check`** — Verify language editions match the approved source version
  - `--project PROJECT_PATH`

#### `guard`

Rights, quotation limits, similarity, privacy, security scanning and export gates

- **`guard quote-guard`** — Enforce quotation length, ratio and licence limits
  - `--quote QUOTE` · `--source-id SOURCE_ID`? · `--output OUTPUT`? · `--max-words MAX_WORDS`?
- **`guard similarity`** — Measure overlap between a draft and the local corpus
  - `--input INPUT_PATH` · `--threshold THRESHOLD`? · `--include-self`
- **`guard patchwriting`** — Detect paraphrases that stay too close to a source
  - `--input INPUT_PATH` · `--source-id SOURCE_ID` · `--min-overlap MIN_OVERLAP`?
- **`guard attribution`** — Check quotations and figures for required attribution
  - `--input INPUT_PATH`
- **`guard license-check`** — Evaluate a licence against an intended use
  - `--license LICENSE` · `--use USE`? (internal|commercial|publish) · `--derivatives` · `--redistribute` · `--attribution-given`
- **`guard pd-status`** — Record a public-domain basis for a source
  - `--source-id SOURCE_ID` · `--basis BASIS` · `--jurisdiction JURISDICTION`? · `--evidence EVIDENCE`?
- **`guard fair-use`** — Assemble a fair-use review packet for human judgement
  - `--purpose PURPOSE` · `--source-id SOURCE_ID` · `--amount-words AMOUNT_WORDS` · `--alternatives ALTERNATIVES`? · `--market-effect MARKET_EFFECT`? · `--output OUTPUT`?
- **`guard redact`** — Irreversibly redact identifiers (approval gated when committing)
  - `--input INPUT_PATH` · `--output OUTPUT`? · `--commit` · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`guard deid-risk`** — Assess re-identification risk
  - `--input INPUT_PATH`
- **`guard classify`** — Suggest or apply a sensitivity label
  - `--source-id SOURCE_ID`? · `--input INPUT_PATH`? · `--label LABEL`?
- **`guard access`** — Check role permissions
  - `--role ROLE` · `--action ACTION`
- **`guard injection-scan`** — Detect prompt-injection instructions in source text
  - `--input INPUT_PATH`? · `--text TEXT`? · `--sanitize` · `--output OUTPUT`?
- **`guard docscan`** — Pre-ingestion safety scan of documents and archives
  - `--input INPUT_PATH` · `--max-ratio MAX_RATIO`? · `--max-mb MAX_MB`?
- **`guard retention`** — List or purge records past retention (purge is gated)
  - `--action ACTION`? (list|purge) · `--days DAYS`? · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`guard export-gate`** — Redact, licence-check, sign and approve an export package
  - `--files FILES` · `--name PACKAGE_NAME`? · `--approve` · `--approver APPROVER`? · `--note NOTE`? · `--include-originals`
- **`guard audit`** — Show or verify the hash-chained audit log
  - `--action ACTION`? (show|verify) · `--limit LIMIT`?
- **`guard incident`** — Record a research data incident
  - `--summary SUMMARY` · `--kind KIND`? · `--severity SEVERITY`? (minor|major|critical) · `--containment CONTAINMENT`?

#### `workflow`

Projects, reviews, approvals, change control, packaging and publication gates

- **`workflow project`** — Create, inspect or list project workspaces
  - `--action ACTION`? (create|status|list) · `--name NAME`? · `--question QUESTION`? · `--kind KIND`?
- **`workflow queue`** — Manage the human review queue
  - `--action ACTION`? (add|list|decide|assign) · `--kind KIND`? · `--ref REF`? · `--priority PRIORITY`? · `--note NOTE`? · `--assignee ASSIGNEE`? · `--review-id REVIEW_ID`? · `--decision DECISION`?
- **`workflow approve`** — Record, list or check approvals
  - `--action ACTION`? (record|list|check) · `--target TARGET`? · `--role ROLE`? · `--note NOTE`? · `--action-name ACTION_NAME`?
- **`workflow decision`** — Log a decision with rationale
  - `--summary SUMMARY` · `--rationale RATIONALE` · `--kind KIND`? · `--ref REF`?
- **`workflow decisions`** — List logged decisions
  - `--kind KIND`? · `--limit LIMIT`?
- **`workflow baseline`** — Freeze an approved baseline for change control
  - `--name NAME`? · `--label LABEL`?
- **`workflow change`** — Request (and optionally approve) a post-baseline change
  - `--description DESCRIPTION` · `--impact IMPACT`? · `--approve` · `--approver APPROVER`? · `--name NAME`?
- **`workflow ready`** — Run the publication readiness gate
  - `--draft DRAFT_PATH`? · `--bilingual BILINGUAL_PATH`?
- **`workflow links`** — Check link, anchor and figure integrity
  - `--input INPUT_PATH`
- **`workflow accessible`** — Check accessibility requirements
  - `--input INPUT_PATH`
- **`workflow preflight`** — Full preflight check for a deliverable
  - `--input INPUT_PATH`
- **`workflow handoff`** — Package sources, outputs, state and decisions for handoff
  - `--output OUTPUT`? · `--no-outputs`
- **`workflow package`** — Build a report package (markdown, HTML, evidence table, bibliography)
  - `--output OUTPUT`? · `--draft DRAFT_PATH`? · `--title TITLE`?
- **`workflow archive`** — Build a preservable archive with checksums (approval gated)
  - `--output OUTPUT`? · `--approve` · `--approver APPROVER`? · `--note NOTE`?
- **`workflow versions`** — Manage versioned publications
  - `--action ACTION`? (list|add) · `--artifact ARTIFACT`? · `--version VERSION`? · `--notes NOTES`? · `--supersedes SUPERSEDES`?
- **`workflow multilingual`** — Check multilingual editions consistency
  - `--editions-dir EDITIONS_DIR`
- **`workflow feedback`** — Intake a reader correction report
  - `--summary SUMMARY` · `--source-id SOURCE_ID`? · `--version VERSION`? · `--decision DECISION`? (open|accepted|rejected)
- **`workflow run-manifest`** — Record a reproducible run manifest
  - `--output OUTPUT`?
- **`workflow embargo`** — Set, show or lift an embargo (lift is gated)
  - `--action ACTION`? (set|show|lift) · `--target TARGET`? · `--until UNTIL`? · `--approve` · `--approver APPROVER`? · `--note NOTE`?

#### `eval`

Health checks, benchmarks, leakage tests, clean-state validation and release gates

- **`eval health`** — Run the environment health check
- **`eval benchmark`** — Run a capability benchmark suite
  - `--suite SUITE_PATH` · `--name NAME`? · `--threshold THRESHOLD`?
- **`eval regression`** — Compare two benchmark runs and gate the release
  - `--baseline BASELINE_RUN` · `--current CURRENT_RUN` · `--tolerance TOLERANCE`?
- **`eval cleanstate`** — Validate a clean skill root and fresh workspace
  - `--required REQUIRED_PATHS`? · `--output OUTPUT`?
- **`eval leakage`** — Test outputs for restricted corpus leakage
  - `--outputs-dir OUTPUTS_DIR`? · `--labels RESTRICTED_LABELS`?
- **`eval acceptance`** — Record a human acceptance rating
  - `--reviewer REVIEWER` · `--task TASK` · `--score SCORE` · `--notes NOTES`? · `--role ROLE`?

## 7. Configuration and policy

`asset-default-config.json` is the single policy file; copy it, edit, and pass `--config`.
Nested keys can be overridden per call with `--set-config dotted.key=value` on any command.

| Block | Controls |
| --- | --- |
| `workspace.default` | Default workspace directory name |
| `limits` | `max_file_mb` (64), `max_files_per_ingest` (500), `max_scan_depth` (6), `max_words_per_segment` (220), `max_query_terms` (32), `max_document_words` (200000), `timeout_seconds` (30), `adapter_timeout_seconds` (60), `retries` (1), `max_jobs_per_run` (50) |
| `languages` | Default language, 18 supported tags, required coverage languages, stopword sets |
| `search` | `default_limit`, BM25 `k1`/`b`, embedding dimensions, minimum relevance, diversity penalty, proximity window |
| `quoting` | Default quote limit in words (120) and ratio of source (0.1), context requirement, hard cap (400 words) |
| `licensing` | Restricted, share-alike, noncommercial and no-derivative licence lists used by `guard license-check` |
| `approval` | `gated_actions`, `required_role` (`approver`), actions needing a second reviewer |
| `adapters` | Local command, timeout, retry and size caps per adapter kind; `command: null` means "not installed" |
| `translation` | Default language pair, fuzzy threshold, length ratios, protected-token patterns, severity weights, acceptance score (95) |
| `evaluation` | Thresholds for retrieval recall (0.8), fact-check accuracy (0.85), citation accuracy (0.9), language score (95), clean-state health (0.9); leakage action; benchmark item cap |
| `privacy` | Identifier regexes (email, phones including PH mobile, national ID, card, IBAN, IPv4, ORCID, DOI), redaction token, salt environment variable, quasi-identifier list |
| `delivery` | Archive format, manifest inclusion, package size cap, accessibility checks |
| `sensitivity_labels` | Allowed classification labels |

`asset-access-policy.json` holds the access matrix (who may read which sensitivity label), retention
rules and export restrictions enforced by `guard` and `workflow`.

### Adapters (local engines are optional)

`translate mt`, `adapters run` and the embedding/OCR/ASR/extract/rerank paths never call a remote
service. They invoke a command you configure locally:

```json
"adapters": {"kinds": {"mt": {"command": ["/usr/local/bin/local-mt", "--json"], "timeout_seconds": 60, "retries": 0}}}
```

The contract is JSON on stdin, JSON on stdout, per kind (see the `description` fields in the config).
If no command is configured the command exits **4** with guidance and does not fabricate output.
`adapters check` reports which kinds are installed, and `translate mt --tm-only` produces
translation-memory prefill marked `draft-untranslated` when no MT engine exists.

## 8. Approval gate

Destructive, publishing and sensitive actions are gated by config (`approval.gated_actions`):
`corpus.delete` (`corpus delete`), `corpus.rollback`, `guard.export`, `guard.redact-commit`, `guard.retention-purge`,
`workflow.publish`, `workflow.archive-sign`, `workflow.embargo-lift`, `eval.release-gate-override`.
Without approval they stop with exit **3** and change nothing:

```bash
$ python3 tools-offline-research.py -w ws corpus rollback --snapshot-id snap-20261106T174415Z
{"status": "error", "message": "approval required for corpus.rollback", "exit_code": 3, ...}

$ python3 tools-offline-research.py -w ws corpus rollback --snapshot-id snap-20261106T174415Z \
    --approve --approver approver:a.reviewer
{"status": "ok", "restored_snapshot": "snap-20261106T174415Z", ...}
```

Approvals are recorded in the audit chain with actor, role, timestamp and action; `guard audit
--action verify` re-walks the hash chain and reports any broken entry. `guard export-gate` packages
only after approval and re-checks licence, provenance and leakage status first.

## 9. Limits, failures and recovery

- **Input validation first.** Missing files, unknown IDs, empty queries, unsupported styles and
  malformed JSON return exit 2 with a message that names the offending value. No partial writes.
- **Size limits.** Files above `limits.max_file_mb`, ingests above `max_files_per_ingest`, documents
  above `max_document_words` and queries above `max_query_terms` are rejected, not truncated.
- **Timeouts and retries.** Every adapter call is bounded by `adapter_timeout_seconds` with
  `retries` attempts; CLI work is bounded by `timeout_seconds`. Timeouts are reported as structured
  errors, never as empty success.
- **Atomic writes.** Outputs are written to a temporary file in `outputs/` and renamed into place;
  a failed command leaves no half-written deliverable. Workspace writes are committed in a single
  SQLite transaction.
- **Snapshots and rollback.** `corpus snapshot --note "before edits"` captures database, corpus and
  state with checksums; `corpus rollback --snapshot-id <id>` (approval-gated) restores it, and
  `backup restore-verify` extracts a snapshot into a temporary directory and re-checks every checksum
  before anyone relies on it. `workflow baseline` and `state checkpoint` create the same kind of
  checkpoint.
- **Integrity and repair.** `corpus integrity` compares recorded content hashes with the stored files
  and lists orphans; `corpus repair-encoding` fixes mojibake; `corpus strip-boilerplate` normalises
  scraped text; `search index` rebuilds the index from stored segments; `corpus install --bundle <zip>
  --confirm` reinstalls a checksummed corpus bundle on another machine.
- **Crash recovery.** The database runs in SQLite WAL mode and every command commits once; an
  interrupted run leaves either the previous or the committed state. Re-running a command is safe:
  ingest deduplicates by content hash, `extract claims` replaces the prior records for a source, and
  outputs are atomically replaced.
- **Cleanup.** `guard retention --action purge` (approval-gated), `corpus update` with delete
  operations, `jobs run-due --max-jobs`, `backup restore-verify` temporary directories (removed
  automatically) and deleting the workspace directory all remove intermediate artefacts. A deleted
  workspace returns the tool to its shipped state, which is what `eval cleanstate` verifies.

## 10. Testing this Skill Tool

```bash
python3 tools-rwt-selftest.py            # 63 checks, exit 0 when everything passes
python3 tools-rwt-selftest.py --json     # machine-readable results
python3 tools-rwt-selftest.py --keep     # keep the temporary workspace for inspection
python3 tools-rwt-selftest.py --section translate
```

Execution record for this build: **63/63 self-test checks pass**, and a separate full-surface sweep
ran **all 160 group commands** (173 invocations including setup and repeat calls) with **0 failures**,
covering every catalog mapping. Every command in section 5 was executed against the shipped fixtures.

The suite creates a temporary workspace, drives the real CLI as a subprocess, and asserts exit codes
and payloads for: help and the 288-entry capability registry (every mapping is parsed back through
the real parser with its defaults), `apply` dispatch, corpus ingest/list/show/integrity/coverage/
dedupe/structure/snapshot/rollback/package/install/encoding repair/gated delete, retrieval index/query/hybrid/
expansion/recall, extraction (claims, entities, dates, quantities, methodology, funding, evidence
links, transcripts, OCR evaluation), citation extraction/formatting/validation/graph, verification
(support, abstention, quotations, numbers, provenance, freshness, chains), synthesis (screening,
PRISMA, evidence tables, meta-analysis, robustness), writing (outline, draft, editing suite, tracked
revision), translation (TM, QA, scoring, alignment, MT honesty, language tools), rights and privacy
(quotation limits, similarity, irreversible redaction, injection and document scanning, export gate,
audit chain), workflow (project, baseline, change control, readiness, package, preflight),
evaluation (health, benchmark, regression gate, leakage, clean state) and the error paths.

`eval benchmark --suite asset-benchmark-suite.json` runs 12 reference items (fact-check support and
abstention, two retrieval recall cases, APA citation output, clean and defective translation pairs,
OCR WER, readability grade, two arithmetic audits, style budget). The latest run: **12/12 passed**,
gate `allow`. `eval regression --baseline <run> --current <run>` compares runs and sets the release
gate; `eval acceptance` prints the publication checklist; `eval cleanstate` verifies the tool folder
runs from relative paths only.

## 11. Honest limitations

- **No language engines ship with this tool.** Machine translation, OCR, speech recognition,
  embeddings, reranking and PDF/Office/EPUB text extraction are adapter slots. Without a configured
  local command they exit 4 and say so. `translate mt --tm-only` offers memory prefill marked
  `draft-untranslated`.
- **Extraction and analysis are deterministic heuristics.** Claim, entity, methodology, bias and
  authority detection use rules and lexicons; scores are advisory. Every generated artefact says so,
  and `workflow ready` lists the human steps still required.
- **Transcripts.** `.srt` and `.vtt` files are parsed into timecoded segments at ingest, so
  `extract timecode-cite` works offline; other media still needs the ASR adapter.
- **Search is lexical.** BM25 over SQLite FTS5 with a hashed-bag-of-words vector baseline and
  optional embedding adapter; it does not understand paraphrase the way a neural retriever would.
  `eval benchmark` reports retrieval recall so the gap is visible.
- **Verification is local.** `verify claim` only knows what is in the workspace corpus: it abstains
  rather than answering from outside knowledge, and never contacts the internet.
- **Meta-analysis** supports standardised mean differences with fixed-effect and random-effects
  (DerSimonian-Laird) pooling; it is not a substitute for a specialist statistics package.
- **Rights checks are advisory.** `guard quote-guard`, `license-check` and `similarity` flag risk;
  they cannot grant permission. Publishing, exporting and redaction remain approval-gated.
- **Accessibility and delivery checks** cover heading structure, alt text, language attributes,
  table headers and link text only.
- **Scope of the 288 skills.** Each catalog skill has one command and documented defaults. Commands
  whose behaviour would need a model or a licensed dataset (for example neural paraphrase detection
  or paywalled metadata lookup) either use a documented heuristic or route through an adapter slot
  and exit 4 when unconfigured; the `note` field in `asset-capability-registry.json` records which.

## 12. Human review, provenance and versioning

- Claim verification, citation formatting, translation QA, bias screening and readiness gates all
  end with an explicit human step: `workflow review queue`, `workflow approve`, `workflow publish`
  (gated), `eval acceptance`.
- Provenance is recorded at ingest (acquisition route, original filename, publisher, issue date,
  licence, retrieval time, checksums, operator) and can be re-audited with `verify provenance`.
- The audit log is append-only and hash-chained; `guard audit --action export` produces a
  verification bundle.
- Version: `1.0` (`schema_version` 1.0, `registry_version` 1.0). `eval health` and `corpus integrity`
  report schema and data state; `asset-capability-registry.json` and this document are updated
  together when the catalog or the command surface changes.
