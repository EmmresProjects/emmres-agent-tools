# offline-research-writing-translation

Unified, reusable Skill Tool component for fully offline research, writing, and translation workflows.

This folder is **part of a future Copilot skill**, not a complete skill. It contains no `SKILL.md`. The delivery ZIP is only a container and must not be treated as a complete Copilot skill package. Place this folder directly beside exactly one parent-package root `SKILL.md`; do not modify the folder after copying.

The attached 61-page catalog, edition November 2026, describes 32 categories and 480 proposed skills. All 32 categories are combined into one Skill Tool. All 480 exact category names, Skill Tool Names, and Descriptions are preserved and dispatched. The umbrella name comes from the catalog title: `offline-research-writing-translation`.

## 1. Package shape and compatibility

- Skill Tool folder: `offline-research-writing-translation/`
- Folder-name rule: `^[a-z0-9]+(?:-[a-z0-9]+)*$`
- Actual Skill Tool file count: **16**
  - 6 `tools-*` files
  - 1 `knowledge-readme.md`
  - 9 `asset-*` files
- Maximum path depth from the future skill root: 2 (`offline-research-writing-translation/filename`)
- Internal subfolders: none
- `SKILL.md` inside this folder: none
- Nested archives inside the folder: none
- Unclassified files: none
- Future combined package: exactly one `SKILL.md` at the skill root, beside this folder
- Delivery ZIP root: `offline-research-writing-translation/` appears directly at the ZIP root with no outer wrapper folder and no nested archive
- Extraction result: the completed Skill Tool folder appears immediately

Every package-internal reference in this document uses a forward-slash path relative to the future skill root, for example `offline-research-writing-translation/asset-default-config.json`. No absolute paths, drive-letter paths, parent-directory traversal, tenant-specific paths, generated URLs, or hidden external addresses are used. Runtime output paths such as `runtime-workspace/result.json` are user-configurable relative paths and are not package-internal references.

## 2. Runtime choice, dependencies, and execution environment

Python 3 with the standard library is the practical choice because the target is an offline/air-gapped workstation, the tool must be portable, and its operations are deterministic text, JSON, CSV, ZIP, and filesystem work. No compiler, package install, database service, model server, browser, or network access is required.

- Runtime implemented for Python 3.9+ syntax.
- Actually checked on Python 3.13.16 on Linux.
- Third-party runtime dependencies: **none**.
- Install command: **none**. Do not run `pip install` for this tool.
- Shell: use a POSIX-compatible shell or any shell that can invoke `python3` directly. Every command below explicitly invokes `python3`; no shell built-ins, pipes, background jobs, or shell interpolation are required.
- Network: disabled. The primary tool makes no socket, HTTP, subprocess, or shell calls.
- Bounded subprocess use is limited to `offline-research-writing-translation/tools-selftest.py` and `offline-research-writing-translation/tools-package-audit.py`, which call the exact bundled primary entry point with `python3` through `subprocess.run`, with no shell and a fixed timeout.
- No arbitrary code execution: the primary tool loads only its four exact sibling modules by known filenames.
- The attached PDF was converted to a UTF-8 text stream during authoring to build `offline-research-writing-translation/asset-capability-catalog.json`. Runtime PDF parsing is not bundled; use a UTF-8 text or JSONL sidecar with page anchors.

## 3. File inventory, roles, and loading order

| Exact filename | Role |
|---|---|
| `offline-research-writing-translation/tools-offline-research.py` | **Primary entry point.** CLI, help, capability list, exact-name dispatch, catalog parse/unify, run, batch, pipeline, recovery, approvals, sensitive actions, package validation, structured output/errors. |
| `offline-research-writing-translation/tools-catalog-adapter.py` | Loaded first by the primary tool. Normalizes plain text, Markdown, copied HTML, CSS classes/data attributes/style attributes, numbering marks, PDF copy furniture, and spacing; extracts category names, Skill Tool Names, and Descriptions; derives one lowercase hyphenated umbrella; builds a unified capability specification and maps each entry to an operation. |
| `offline-research-writing-translation/tools-workflow-engine.py` | Loaded second. Shared runtime engine: config/catalog/request validation, structured errors, bounded atomic I/O, source parsing and schema validation, BM25-style and hashed-vector search, glossary/TM helpers, approvals, checkpoints, cancellation, timeouts, cleanup, audit, provenance declarations. |
| `offline-research-writing-translation/tools-domain-operations.py` | Loaded third. Thirty-two real category handlers, one per catalog category. Reuses the engine for all 480 exact capability names; each exact name keeps its own mode, responsibility, and full declaration. |
| `offline-research-writing-translation/tools-package-audit.py` | Loaded fourth by the primary tool and also runnable standalone. Audits folder/ZIP compatibility, reference resolution, root structure, prefixes, `SKILL.md` rules, internal subfolders, nested archives, and clean-root extraction/rerun. |
| `offline-research-writing-translation/tools-selftest.py` | Standalone validation suite. Copies the folder into a clean temporary skill root beside one test `SKILL.md`, then runs the real CLI using relative paths. |
| `offline-research-writing-translation/knowledge-readme.md` | The only knowledge file. Contains all purpose, setup, commands, schemas, examples, safety, validation, limitations, troubleshooting, recovery, extension, and maintenance guidance. |
| `offline-research-writing-translation/asset-capability-catalog.json` | The parsed source catalog: 32 categories, 480 exact capabilities, exact descriptions, operation/mode/responsibility mapping, edition, source summary, and unavailable-integration notes. Used by `capabilities`, `capability`, `run-capability`, and governance. |
| `offline-research-writing-translation/asset-default-config.json` | Default runtime config: limits, timeouts, retries, cancellation, cleanup, recovery, providers/models, policies, approval rules, storage names, and exact asset paths. Override with `--config`. |
| `offline-research-writing-translation/asset-source-schema.json` | Required fields, types, enums, patterns, bounds, and permission object for normalized local source records. Used by every ingestion path. |
| `offline-research-writing-translation/asset-sample-corpus.jsonl` | Nine original short CC0/restricted fixtures with provenance, versions, dates, jurisdictions, statuses, permissions, numeric conflicts, corrections, standards clauses, timestamped transcript segments, and security-test strings. Used by examples, benchmarks, and selftest. |
| `offline-research-writing-translation/asset-glossary.csv` | Reusable English-to-Spanish and English-to-Filipino terminology plus a do-not-translate placeholder row. Used by translation, terminology, editing, and QA. |
| `offline-research-writing-translation/asset-translation-memory.json` | Original exact-match translation-memory fixtures. Used by translation and evaluation. |
| `offline-research-writing-translation/asset-report-template.md` | Reusable grounded report template with title, query, evidence, sections, citations, limitations, and human-review placeholders. Used by composition and specialized writing. |
| `offline-research-writing-translation/asset-acceptance-rubric.json` | Eight offline fixtures, per-language thresholds, and acceptance gates. Used by evaluation and QA. |
| `offline-research-writing-translation/asset-request-schema.json` | Request schema for `run` and `batch`, including the 32-operation enum and allowed fields. Used by validation and governance. |

Loading/execution order: `offline-research-writing-translation/tools-offline-research.py` sets `sys.dont_write_bytecode`, loads `offline-research-writing-translation/tools-catalog-adapter.py`, `offline-research-writing-translation/tools-workflow-engine.py`, `offline-research-writing-translation/tools-domain-operations.py`, and `offline-research-writing-translation/tools-package-audit.py` in that order, then dispatches. `offline-research-writing-translation/tools-selftest.py` is not loaded by the primary tool; it invokes the primary tool as a separate bounded process.

## 4. Setup and exact commands

Copy `offline-research-writing-translation/` directly beside one root `SKILL.md` in the future parent package. Run commands from the future skill root. The tool creates `runtime-workspace/` outside the immutable Skill Tool folder; if the current working directory is the Skill Tool folder itself, the default workspace is redirected to its parent.

```sh
python3 offline-research-writing-translation/tools-offline-research.py help
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py operations --workspace runtime-workspace --output runtime-workspace/operations.json
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py capabilities --verify --limit 10 --workspace runtime-workspace --output runtime-workspace/capabilities.json
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py run --operation retrieval --input offline-research-writing-translation/asset-sample-corpus.jsonl --params '{"query":"checksum manifest","limit":5}' --workspace runtime-workspace --output runtime-workspace/retrieval.json
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py pipeline --input offline-research-writing-translation/asset-sample-corpus.jsonl --query "checksum manifest" --workspace runtime-workspace --output runtime-workspace/pipeline.json --timeout 180 --run-id example-pipeline
```

```sh
python3 offline-research-writing-translation/tools-selftest.py --output runtime-workspace/selftest-results.json
```

```sh
python3 offline-research-writing-translation/tools-package-audit.py --folder offline-research-writing-translation --zip offline-research-writing-translation-skill-tool.zip --extract-to clean-skill-root --rerun --output runtime-workspace/package-audit.json
```

Common options go **after** the subcommand: `--output`, `--workspace`, `--config`, `--catalog`, `--timeout`, `--retries`, `--cancel-file`, `--overwrite`, `--keep-temp`, `--run-id`, and `--format`. Nested commands also accept these options after `catalog parse`, `catalog unify`, `review request`, `review approve`, `review reject`, and `review list`.

Built-in help is available as `help` and `--help`. The capability-list command is `capabilities`; `capabilities --verify` checks every exact name, declaration, and handler. `operations` lists the 32 shared handlers.

## 5. Inputs, outputs, schemas, and configuration

### Source inputs

`run`, `run-capability`, `pipeline`, `batch`, `export`, `publish`, and most handlers accept `--input`. Supported local UTF-8 formats:

- `.jsonl`: one validated source record per line
- `.json`: array, single source object, or object with a `sources` array
- `.csv`: rows converted to source records, including JSON permissions cells
- `.txt`, `.md`, `.html`, `.htm`: normalized into one source record
- `.bib`, `.bibtex`, `.ris`: local bibliographic records
- `.vtt`, `.srt`: timestamped transcript sidecars with speaker labels
- `.pdf`: **not bundled**; convert licensed PDFs to text/JSONL sidecars with page anchors first

`offline-research-writing-translation/asset-source-schema.json` requires `id`, `title`, `text`, `source_type`, `language`, `license`, `jurisdiction`, `version`, `date`, `authority`, `status`, and `permissions`. The permission object requires boolean `search`, `quote`, `redistribute`, `store`, and `export`. Optional fields include `page_anchor`, `edition`, `supersedes`, `retracted_by`, `restricted`, `tags`, and timestamped `segments`. The engine adds a text SHA-256 checksum and normalized text.

### Catalog inputs

`catalog parse` and `catalog unify` accept a single capability, a numbered Skill Tool list, one category, or several categories in plain text, Markdown, or copied HTML. They ignore HTML tags, CSS classes, data attributes, style attributes, numbering marks, page furniture, introductory prose, and extra spacing. They extract real category names, Skill Tool Names, and Descriptions. One category becomes the umbrella name; several related categories are combined into one unified specification; a single capability without a category gets a short lowercase hyphenated name from its shared purpose. Use `--category-hint` when a single-capability source has no explicit category.

### Structured output and errors

Every normal command writes JSON with `schema_version`, `tool`, `ok`, `command`, `operation`, `run_id`, `timestamp_utc`, `config_digest`, `elapsed_seconds`, `output_path`, `warnings`, and `result`. `--output` chooses the file; without it the tool writes a run-scoped file under the workspace. `--overwrite` is required to replace an existing output.

Errors go to stderr as structured JSON with `ok: false` and an `error` object containing `code`, `message`, `details`, `recovery`, and `exit_code`. Common codes include `INVALID_INPUT`, `INVALID_CONFIG`, `APPROVAL_REQUIRED`, `CANCELLED`, `TIMEOUT`, `IO_ERROR`, `OUTPUT_TOO_LARGE`, `PACKAGE_AUDIT_FAILED`, and `INTERNAL_ERROR`. Exit codes are 2 for input errors, 3 for config errors, 4 for approval errors, 5 for cancellation, 6 for timeout, and 70 for internal/module errors.

### Request schema

`offline-research-writing-translation/asset-request-schema.json` defines `run` and `batch` requests. `operation` is required and must be one of the 32 enum values. Optional fields are `input`, `output`, `params`, `timeout_seconds`, `retries`, `cancel_file`, `approval_token`, and `run_id`. Unknown top-level fields are rejected. `batch` reads one JSON object per JSONL line and records per-line structured success or error.

### Configuration

`offline-research-writing-translation/asset-default-config.json` is the portable default. Override it with a JSON file passed to `--config`; the override is deep-merged into the default. Example override saved as `runtime-workspace/config-override.json`:

```json
{
  "runtime": {"timeout_seconds": 60, "retries": 2},
  "policies": {"default_target_language": "fil", "max_quote_chars": 200},
  "storage": {"workspace": "runtime-workspace-alt"}
}
```

Configuration validation rejects network-enabled settings, external addresses, absolute package paths, parent traversal in internal paths, unsupported filenames, and malformed limits. Providers, model identifiers, prompts/policies, language lists, jurisdictions, source hierarchy, quote limits, approval text, token lifetime, storage names, and asset paths remain configurable. No secrets, credentials, private endpoints, or paid services are included.

Key defaults:

- `max_input_bytes`: 10485760
- `max_documents`: 10000
- `max_document_chars`: 500000
- `max_query_chars`: 4096
- `max_output_bytes`: 20971520
- `timeout_seconds`: 30, overridable with `--timeout`
- `retries`: 1, overridable with `--retries`
- `cleanup_temp_on_exit`: true, overridable with `--keep-temp`
- `network`: disabled

Timeouts use a POSIX `SIGALRM` when available plus cooperative deadline checks at operation and pipeline boundaries; on platforms without `SIGALRM`, cooperative checks remain. Cancellation checks `runtime-workspace/CANCEL` and any `--cancel-file` between batch items and pipeline stages. `pipeline --cancel-after N` writes a cancel marker after N stages for bounded recovery testing. Temporary write files are removed in `finally` unless `--keep-temp` is used. Checkpoints are written after every pipeline stage; `recover --resume --clear-cancel` continues from the last completed stage.

## 6. Complete capability list and examples

The 480 exact names, descriptions, categories, responsibilities, modes, and operation mappings are in `offline-research-writing-translation/asset-capability-catalog.json`. Overlapping requested capabilities are not removed. Each exact name has a clear responsibility and dispatch record; the 32 handlers below reuse shared engine logic.

Use `run-capability` for any exact name or slug:

```sh
python3 offline-research-writing-translation/tools-offline-research.py run-capability --name "Air-Gapped Research Stack Installer" --input offline-research-writing-translation/asset-sample-corpus.jsonl --params '{"query":"checksum manifest"}' --workspace runtime-workspace --output runtime-workspace/exact-capability.json
```

Use `run-capability --plan-only` for a validated declaration without execution. `capability --name ...` shows or executes one capability with a slightly more compact result. `capabilities --limit 1000` lists all 480 exact entries; `--category` and `--operation` filter them.

In the examples below, `CORPUS` means the concrete relative path `offline-research-writing-translation/asset-sample-corpus.jsonl`. All `--params` values are inline JSON strings.

| # | Catalog category | Operation and responsibility | Example exact Skill Tool Name | Example invocation |
|---:|---|---|---|---|
| 1 | Offline Installation, Dependency, and Corpus Deployment | `deployment`: bundle hashes, environment baseline, install/rollback plans, health and clean-state checks | Air-Gapped Research Stack Installer | `run --operation deployment --input CORPUS` |
| 2 | Scholarly Databases and Academic Corpora | `scholarly-corpus`: metadata, source hierarchy, corrections, retractions, acceptance gate | Offline Scholarly Database Manager | `run --operation scholarly-corpus --input CORPUS` |
| 3 | Reference Corpora and General Knowledge Libraries | `reference-corpus`: edition registry, exact duplicates, metadata validation | Offline Reference Corpus Manager | `run --operation reference-corpus --input CORPUS` |
| 4 | News, Media, and Event Archives | `news-archive`: versions, corrections, syndication candidates, event timeline | Offline News Archive Manager | `run --operation news-archive --input CORPUS` |
| 5 | Legal, Regulatory, and Policy Corpora | `legal-corpus`: jurisdiction, effective dates, repeal/amendment issues, version resolution | Offline Legal Corpus Manager | `run --operation legal-corpus --input CORPUS` |
| 6 | Standards, Specifications, and Technical Libraries | `standards-library`: editions, supersession, clause extraction, normative references, license audit | Offline Standards Library Manager | `run --operation standards-library --input CORPUS` |
| 7 | Citation Indexes, Bibliographic Metadata, and Identifier Caches | `bibliographic`: BibTeX/RIS/CSL-like parsing, DOI/ISBN candidates, conflict detection | Offline Citation Index Manager | `run --operation bibliographic --input CORPUS --params '{"bibliography_text":"@article{x,title={Local Corpus},year={2026}}"}'` |
| 8 | Full-Text Search, Querying, and Retrieval | `retrieval`: BM25-style index, Boolean/phrase/field/facet/date/language filters, permission filter | Local Full-Text Search Engine Manager | `run --operation retrieval --input CORPUS --params '{"query":"checksum manifest","limit":5}'` |
| 9 | Embeddings, Vector Search, and Reranking | `semantic-retrieval`: deterministic hashed n-gram vectors, hybrid search, feature reranking, threshold and drift | Local Document Embedding Model Manager | `run --operation semantic-retrieval --input CORPUS --params '{"query":"pinned corpus snapshot"}'` |
| 10 | Local Question Answering and Research Agents | `question-answering`: decomposition, evidence plan, extractive cited answer, conflicts, grounded abstention | Local Question-Answering Model Manager | `run --operation question-answering --input CORPUS --params '{"query":"What requires a checksum manifest?"}'` |
| 11 | OCR, Handwriting, Tables, and Document Layout | `document-layout`: text-layer reading order, tables, footnotes, headers, equation candidates | Offline OCR Model Manager | `run --operation document-layout --input CORPUS` |
| 12 | Speech-to-Text, Interviews, and Audio Sources | `audio-transcripts`: timestamped sidecars, speakers, quote windows, validation | Speech-to-Text Interview Corpus Manager | `run --operation audio-transcripts --input CORPUS` |
| 13 | Translation Models and Multilingual Language Systems | `translation`: exact TM, glossary-assisted drafts, placeholder/number preservation, human-review gate | Local Translation Model Manager | `run --operation translation --input CORPUS --params '{"text":"Offline research requires a pinned corpus snapshot and a checksum manifest.","target_language":"es"}'` |
| 14 | Terminology, Glossaries, and Translation Memory | `terminology`: glossary validation, term application, candidate extraction, do-not-translate checks | Bilingual Glossary Manager | `run --operation terminology --input CORPUS --params '{"text":"Keep {NAME} and checksum manifest unchanged.","target_language":"es"}'` |
| 15 | Localization and Language Quality Assurance | `localization-qa`: placeholders, numbers, dates, bidi, encoding, length, untranslated segments | Locale Placeholder Validator | `run --operation localization-qa --input CORPUS --params '{"text":"Offline research requires a checksum manifest.","target_language":"es"}'` |
| 16 | Named Entities, Relationships, and Knowledge Graphs | `knowledge-graph`: bounded entity/relationship extraction and evidence-linked graph export | Entity Relationship Graph Builder | `run --operation knowledge-graph --input CORPUS` |
| 17 | Source Ingest, Parsing, and Normalization | `ingest`: parse supported formats, validate schema, compute checksums, write normalized corpus | Structured Source Ingest Operator | `run --operation ingest --input CORPUS` |
| 18 | Source Deduplication, Clustering, and Canonicalization | `deduplication`: exact/near duplicate clustering and canonical selection | Exact Duplicate Detector | `run --operation deduplication --input CORPUS --params '{"near_threshold":0.45}'` |
| 19 | Citation Extraction, Anchoring, and Validation | `citations`: inline/footnote/author-year parsing, quote alignment, broken citation detection | Inline Citation Parser | `run --operation citations --input CORPUS --params '{"draft":"Offline research requires a checksum manifest [src-001]."}'` |
| 20 | Literature Review and Evidence Synthesis | `evidence-review`: plan, screening, evidence table, thematic synthesis, gap map | Systematic Review Planner | `run --operation evidence-review --input CORPUS --params '{"include_languages":["en"]}'` |
| 21 | Fact Checking, Verification, and Freshness | `fact-check`: numeric/date claims, evidence scores, conflicts, corrections, uncertainty | Numerical Fact Verifier | `run --operation fact-check --input CORPUS --params '{"claim":"The corrected 2025 survey covered 14 transects in Las Piñas."}'` |
| 22 | Research Writing and Long-Form Composition | `composition`: grounded template-based draft, sections, citations, coherence check | Research Writing Planner | `run --operation composition --input CORPUS --params '{"query":"checksum manifest","sections":["Findings","Method","Conclusion"]}'` |
| 23 | Academic, Technical, Legal, and Policy Writing | `specialized-writing`: genre section maps and domain style checks | Technical Report Writer | `run --operation specialized-writing --input CORPUS --params '{"query":"checksum manifest","genre":"technical"}'` |
| 24 | Editing, Revision, Style, and Readability | `editing`: readability, repetition, ambiguity, terminology consistency, revision diff | Readability Analyzer | `run --operation editing --input CORPUS --params '{"draft":"Offline research requires a checksum manifest [src-001].","previous_draft":"Offline research requires a corpus snapshot."}'` |
| 25 | Plagiarism, Similarity, and Originality Controls | `originality`: exact/near overlap, quotation boundaries, citation omissions, self-similarity | Exact Passage Similarity Checker | `run --operation originality --input CORPUS --params '{"draft":"Offline research requires a checksum manifest [src-001]."}'` |
| 26 | Copyright, Quotation, and Rights Management | `rights`: license, quote length, attribution, redistribution, rights evidence | Copyright Status Classifier | `run --operation rights --input CORPUS --params '{"quote":"Offline research requires a pinned corpus snapshot and a checksum manifest.","source_id":"src-001"}'` |
| 27 | Human Review, Approval, and Editorial Workflows | `human-review`: pending requests, conflict declarations, escalation, no self-approval | Human Review Workflow Manager | `run --operation human-review --input CORPUS --params '{"actions":["export","publish"]}'` |
| 28 | Accuracy Benchmarks and Evaluation by Language | `evaluation`: eight offline fixtures, per-language thresholds, promotion gate | Language Accuracy Benchmark Registry | `run --operation evaluation --input CORPUS` |
| 29 | Provenance, Versioning, Audit, and Reproducibility | `provenance`: checksums, versions, audit tail, evidence package, drift, replay descriptor | Source Provenance Manifest Builder | `run --operation provenance --input CORPUS` |
| 30 | Security, Privacy, and Restricted Corpus Handling | `security`: redacted PII-like/secret-like findings, injection terms, isolation, export gate | Sensitive Data Classifier | `run --operation security --input CORPUS` |
| 31 | Packaging, Backup, Archival, and Handoff | `packaging`: bounded local ZIP, license/checksum manifests, install/self-test notes, backup/recovery | Complete Offline Research Corpus ZIP Builder | `run --operation packaging --input CORPUS --params '{"package_path":"runtime-workspace/handoff.zip"}'` |
| 32 | Skill Integration, Documentation, Training, and Governance | `governance`: interface schema, SOP, quick start, troubleshooting, training, dependency graph, catalog governance | Skill Tool Schema Builder | `run --operation governance --input CORPUS` |

`pipeline` runs all 32 operations in a documented workflow order and writes a checkpoint after each stage:

```sh
python3 offline-research-writing-translation/tools-offline-research.py pipeline --input offline-research-writing-translation/asset-sample-corpus.jsonl --query "checksum manifest" --workspace runtime-workspace --output runtime-workspace/pipeline.json --timeout 180 --run-id example-pipeline
```

`batch` runs several validated requests from `runtime-workspace/requests.jsonl`:

```sh
python3 offline-research-writing-translation/tools-offline-research.py batch --input runtime-workspace/requests.jsonl --workspace runtime-workspace --output runtime-workspace/batch-results.jsonl
```

## 7. Safety and approval rules

The tool never grants its own approval. Sensitive actions in `offline-research-writing-translation/asset-default-config.json` are `export`, `publish`, `purge`, `secure-dispose`, and `rights-exception`. Financial, credential, privileged, and true external publishing actions are not implemented; adding them would require the same gate plus a reviewed adapter.

```sh
python3 offline-research-writing-translation/tools-offline-research.py review request --action export --ticket REVIEW-EXPORT-001 --notes "human inspected" --workspace runtime-workspace --output runtime-workspace/review-request.json
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py review approve --ticket REVIEW-EXPORT-001 --approver "Human Reviewer" --confirm APPROVE --workspace runtime-workspace --output runtime-workspace/review-approve.json
```

The approve result returns an `approval_token`. Pass it to the matching action:

```sh
python3 offline-research-writing-translation/tools-offline-research.py export --input offline-research-writing-translation/asset-sample-corpus.jsonl --package runtime-workspace/approved-export.zip --approval-token TOKEN_FROM_APPROVE --workspace runtime-workspace --output runtime-workspace/export-result.json
```

`publish` with an approval token writes only a local publication record and sets `published_externally: false`. `purge` and `secure-dispose` require an action-specific token plus `--confirm DELETE`, accept one existing file inside the workspace, reject directories and parent traversal, and delete only that file. `review reject` records a reason; `review list` shows sanitized records without token hashes.

Other safety behavior:

- Copyright/permission checks run before search, quote, export, and packaging.
- Retracted, superseded, and unverified sources are blocked unless a request explicitly sets `include_blocked`.
- Restricted sources are excluded from search unless their permissions allow it.
- Security findings are redacted in reports; raw secret-like values are not emitted.
- Quotes are bounded by `max_quote_chars`.
- Translation, legal, academic, OCR-sidecar, transcript, and publication outputs are marked for human review.
- Answers abstain when evidence is insufficient or numeric evidence conflicts.
- All I/O is bounded by byte/document/query/output limits; no shell or network execution occurs in the primary tool.

## 8. Limits and unavailable integrations

The catalog describes a larger future stack. This build implements and checks every independent deterministic part and keeps blocked model integrations configurable. It does **not** claim to execute:

- image OCR or handwriting model binaries; `document-layout` processes text-layer sidecars only
- audio decoding or ASR model binaries; `audio-transcripts` processes VTT/SRT/JSONL transcript sidecars only
- neural machine-translation model binaries; `translation` uses exact translation memory and glossary-assisted drafts, with mandatory review
- local LLM generation or cross-encoder reranking; answers and drafts are deterministic and evidence-extractive
- neural embedding models; `semantic-retrieval` uses deterministic hashed character n-gram vectors and lexical-feature reranking
- PDF text extraction; convert licensed PDFs to UTF-8 sidecars
- external corpus-wide accuracy claims; `evaluation` uses eight bundled reproducible fixtures and per-language thresholds, not an external benchmark
- external publishing, network lookups, remote identifier resolution, license servers, model servers, or paid services

Provider entries in `offline-research-writing-translation/asset-default-config.json` name the implemented built-ins and mark unavailable integrations as `configurable-not-executed` with `model_id: null` and `local_path: null`. A future adapter may be configured, but no executable adapter is bundled or invoked. If a hosted resource remains inaccessible after packaging, that is a hosting, catalog, permission, or platform issue; this package cannot prevent an external platform failure.

Additional limits:

- The sample corpus is a small original fixture, not a licensed scholarly/legal/news corpus.
- Bibliographic identifier checks are syntax-level and offline; no remote resolver is contacted.
- Language detection is heuristic; pass `source_language` explicitly for production text.
- Similarity, retrieval, graph extraction, and factuality checks are deterministic heuristics, not trained models.
- Runtime output ZIPs created by `packaging` are user artifacts. The delivered Skill Tool ZIP contains no nested archives.
- No hidden credentials or private endpoints exist.

## 9. Validation and tests actually completed

The packaged suite was run from a clean temporary skill root containing exactly one test `SKILL.md` beside the copied Skill Tool folder. Every Skill Tool path in its commands was relative. It used Python 3.13.16 on Linux with `PYTHONDONTWRITEBYTECODE=1`.

Command:

```sh
python3 offline-research-writing-translation/tools-selftest.py --output runtime-workspace/selftest-results.json
```

Actual result: **28 tests run, 28 passed, 0 failed**. The suite checked:

1. built-in `help`
2. `operations` returning 32 operations
3. `capabilities --verify` returning 480 capabilities, 32 categories, 32 operations, and a passing handler/declaration check
4. `catalog parse` for plain text
5. `catalog parse` for Markdown
6. `catalog parse` for copied HTML with classes/data attributes/style attributes
7. `catalog parse` for a single capability with a category hint
8. `catalog unify`
9. valid structured retrieval output
10. changed input changing retrieval results
11. changed query changing retrieval results
12. missing input producing a structured error
13. invalid JSONL producing a structured error
14. batch with missing, invalid, and valid requests
15. all 32 operations through the real `run` interface
16. exact-name `run-capability` dispatch for one capability from each of 32 categories
17. `run-capability --plan-only`
18. full 32-stage `pipeline`
19. cancellation with a stage-5 checkpoint
20. `recover --resume --clear-cancel` completing all 32 stages from stage 6
21. evaluation fixtures and overall gate
22. approval gate blocking export
23. approved local export
24. approved local publication record
25. delete-confirmation gate
26. approved purge
27. strict folder compatibility audit
28. clean future skill root structure, one knowledge file, no `SKILL.md` inside the folder, and no `__pycache__` inside the folder

Additional manual checks completed during development:

- Parsed the attached 61-page catalog text: 32 categories and 480 exact capabilities.
- Built `offline-research-writing-translation/asset-capability-catalog.json` from that parsed text and verified all 480 exact-name declarations.
- Ran every one of the 32 `run --operation` handlers against `offline-research-writing-translation/asset-sample-corpus.jsonl` and parsed each JSON result.
- Ran `run-capability` for one exact name from each of the 32 categories and verified the returned exact name and operation.
- Ran the full PDF text through `catalog parse` and `catalog unify`: 32 categories, 480 capabilities, 32 operations.
- Ran valid, invalid, and missing input paths; structured error codes were parsed from stderr.
- Changed both the corpus input and query and confirmed retrieval results changed.
- Opened and parsed checkpoint, approval, batch, evaluation, pipeline, and package-audit JSON outputs.
- Tested approval request/approve/export/publish/purge, missing approval, and wrong delete confirmation.
- Tested cancellation and recovery through the real CLI.
- Verified no `SKILL.md`, no internal subfolders, no unclassified files, exactly one knowledge file, 6 tool files, and 9 asset files in the Skill Tool folder.

The final delivery ZIP is audited with:

```sh
python3 offline-research-writing-translation/tools-package-audit.py --folder offline-research-writing-translation --zip offline-research-writing-translation-skill-tool.zip --extract-to clean-skill-root --rerun --output runtime-workspace/package-audit.json
```

The audit checks the folder, the ZIP root, one knowledge file, the primary entry point, prefixes, no nested archives, no unsafe paths, no `SKILL.md` inside the folder, extraction into a clean root, one test `SKILL.md` beside the folder, and rerunning `help` plus `capabilities --limit 3 --format json` using only relative paths.

Actual final audit results: both the standalone `offline-research-writing-translation/tools-package-audit.py --rerun` command and the primary `validate-package --rerun --strict` command passed. The folder audit passed with 16 files; the ZIP audit passed with 16 files and exactly one root folder, `offline-research-writing-translation`; extraction placed that folder directly in a clean root beside one test `SKILL.md`; the rerun used relative paths, returned `help` exit code 0, and returned `capabilities` JSON with `ok: true`, `capability_count: 480`, and `returned_count: 3`. The extracted folder contained no `SKILL.md` and no absolute path appeared in the rerun output. Open inspection of the final ZIP showed 16 file entries, one root directory entry, no nested archive, and `testzip()` returned no corrupt member.

## 10. Troubleshooting, recovery, extension, and maintenance

| Symptom / code | Likely cause | Recovery |
|---|---|---|
| `INVALID_INPUT` | Missing path, unsupported extension, malformed UTF-8/JSON/CSV/JSONL, schema failure, output exists, or output inside the tool folder | Check the exact path and schema in `offline-research-writing-translation/asset-source-schema.json`; use `--overwrite`; choose a workspace outside the tool folder. |
| `INVALID_CONFIG` | Override enables network, adds an external address, uses an absolute internal path, parent traversal, or a bad limit | Edit the override and keep all package paths relative with exact prefixed filenames. |
| `APPROVAL_REQUIRED` | Sensitive action attempted without a matching approved token | Run `review request`, then `review approve` with `--confirm APPROVE`; pass the returned token to the same action. |
| `CANCELLED` | `runtime-workspace/CANCEL` or `--cancel-file` exists | Inspect the reason, remove the cancel marker, then run `recover --resume --clear-cancel --run-id RUN_ID`. |
| `TIMEOUT` | Input or pipeline exceeded `--timeout` | Raise `--timeout`, reduce input, or split requests with `batch`. |
| `IO_ERROR` | Permissions or disk issue after bounded retries | Choose another workspace/output path with `--workspace` and `--output`; check free space. |
| `OUTPUT_TOO_LARGE` | Result exceeds `max_output_bytes` | Reduce `--limit`, split the corpus, or raise the configured limit safely. |
| `PACKAGE_AUDIT_FAILED` | Renamed/missing file, extra subfolder, unprefixed file, nested archive, or bad ZIP root | Restore exact case-sensitive names, remove the extra file/folder, rebuild the ZIP, and rerun `validate-package`. |
| `INTERNAL_ERROR` | Unexpected programming error | Rerun `offline-research-writing-translation/tools-selftest.py`; inspect the exact command and input; restore the exact bundled files if any were edited. |
| `PDF parsing is not bundled` | A `.pdf` path was supplied | Extract licensed PDF text to UTF-8 `.txt` or `.jsonl` with page anchors and pass that sidecar. |

Recovery commands:

```sh
python3 offline-research-writing-translation/tools-offline-research.py recover --run-id example-pipeline --workspace runtime-workspace --output runtime-workspace/checkpoint.json
```

```sh
python3 offline-research-writing-translation/tools-offline-research.py recover --resume --clear-cancel --run-id example-pipeline --workspace runtime-workspace --output runtime-workspace/recovered.json --timeout 180
```

Extension guidance:

- Replace or extend licensed corpora through `--input`; keep the source schema and permission flags.
- Replace catalogs through `--catalog` or regenerate one with `catalog unify`.
- Replace policies, providers, model identifiers, language lists, thresholds, storage names, and asset paths through `--config`.
- Replace terminology through `params.glossary_path`, translation memory through `params.translation_memory_path`, and evaluation fixtures by editing `offline-research-writing-translation/asset-acceptance-rubric.json`.
- Add a genuinely new operation by adding a handler to `offline-research-writing-translation/tools-domain-operations.py`, an operation description to `offline-research-writing-translation/tools-catalog-adapter.py`, an enum value in `offline-research-writing-translation/asset-request-schema.json`, and a capability mapping in `offline-research-writing-translation/asset-capability-catalog.json`. Update this single knowledge file; do not add a second knowledge file.
- Keep every new file directly inside `offline-research-writing-translation/`, use `tools-`, `knowledge-`, or `asset-` prefixes, lowercase ASCII names, single hyphens, correct extensions, and forward-slash relative references.
- Do not add `SKILL.md`, internal subfolders, nested archives, inventories, raw logs, credentials, or duplicated documentation.
- After any change, run `offline-research-writing-translation/tools-selftest.py`, run `validate-package --path offline-research-writing-translation --strict`, rebuild the descriptively named ZIP, and run `offline-research-writing-translation/tools-package-audit.py` with `--zip` and `--rerun`.

## 11. Delivery ZIP

Final container: `offline-research-writing-translation-skill-tool.zip`.

- It contains `offline-research-writing-translation/` directly at the ZIP root.
- It contains no unnecessary outer wrapper folder.
- It contains no nested ZIP or other archive.
- Extracting it produces the completed Skill Tool folder immediately.
- The folder can be copied beside one parent-package root `SKILL.md` without modification.
- All referenced resources exist and resolve from the future skill root with exact case-sensitive spelling.
- The combined package stays far below 500 files: the Skill Tool contributes 16 files plus the parent root `SKILL.md`.
- The final ZIP was opened and inspected: 16 file entries, one root directory entry, no nested archive, and no corrupt member.
- The final ZIP was extracted into clean temporary roots twice: once by `offline-research-writing-translation/tools-package-audit.py --rerun` and once by the primary `validate-package --rerun --strict`. Both reruns passed with relative paths and one test `SKILL.md` beside the extracted folder.
