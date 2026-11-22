# Research Workspace Creator (`research-workspace-creator`)

## 1. Purpose and Architecture

`research-workspace-creator` is a reusable, self-contained production Skill Tool component designed to sit directly beside a parent Copilot skill root `SKILL.md`. It unifies **all 41 categories** and **all 15 specialist roles** (**615 capabilities** total) from the *Comprehensive Research & Workspace Creator Skill Catalog*, alongside a multi-format capability specification parser (`parse-spec`) that extracts categories, skill names, and descriptions from plain text, Markdown, copied HTML, JSON, or PDF specifications.

Every capability inspects real inputs, performs bounded and permission-aware operations, records chained SHA-256 provenance, maintains truthful accounting of searches/downloads/installations/files/subprocesses, validates structured outputs against JSON schemas, supports atomic checkpoint recovery, and produces portable relative-path deliverables.

### Tool Files and Deterministic Loading Order

All files reside flat inside `research-workspace-creator/`. The primary runnable entry point loads supporting modules in strict dependency order with `sys.dont_write_bytecode = True` so execution never pollutes the Skill Tool folder with `__pycache__` subdirectories:

1. `research-workspace-creator/tools-research-workspace.py` (**Primary Entry Point**): Parses CLI subcommands and options, enforces top-level policy flags, bootstraps sibling modules in deterministic order, formats `json` / `markdown` / `text` outputs, and returns structured exit codes (`0` success, `2` validation error, `3` approval required, `4` policy/runtime error).
2. `research-workspace-creator/tools-core-runtime.py` (**Loaded 1st**): Implements skill-root-relative path resolution, path-traversal guards, `RuntimeConfig`, `SafetyGovernor` (approval gates, SSRF protection, read-only SQLite guards, secret/PII detection and redaction), `TruthfulnessTracker`, `SchemaValidator`, `ProvenanceLedger`, `EventLogger`, `ContentAddressedCache`, `CheckpointManager`, and bounded retry/timeout helpers.
3. `research-workspace-creator/tools-spec-parser.py` (**Loaded 2nd**): Implements `parse_capability_spec` (stripping HTML tags, CSS classes, `data-*` attributes, numbering prefixes, and excess whitespace from plain text, Markdown, copied HTML, JSON, or PDF text) and `CatalogRegistry` (indexing all 41 categories, 15 roles, and 615 capabilities).
4. `research-workspace-creator/tools-ingestion-extractors.py` (**Loaded 3rd**): Implements concrete domain handlers for **Categories 1–15**, including investigation design, search discovery, HTML link intelligence, technical docs analysis, BibTeX/DOI parsing, RSS/Atom news deduplication, connected workspace search, local file/folder hashing, native `.txt`/`.md`/`.html`/`.csv`/`.json`/`.xml`/`.docx`/`.xlsx`/`.pptx`/`.pdf` extraction, post-OCR text recovery, binary PNG/JPEG/GIF/BMP/SVG/Mermaid inspection, WAV/MP3/MP4/SRT/VTT multimedia analysis, download validation, structured API projection, and `robots.txt`-compliant HTML scraping.
5. `research-workspace-creator/tools-knowledge-index.py` (**Loaded 4th**): Implements concrete domain handlers for **Categories 16–23**, including read-only guarded SQLite introspection and querying, tabular data cleaning and IQR outlier profiling, entity and relation-triple capture, multi-style citation formatting (`apa`, `mla`, `chicago`, `ieee`, `bibtex`, `markdown`), cross-source claim verification, comparative synthesis, SQLite + graph Knowledge Base construction, and hybrid **SQLite FTS5 (BM25) + TF-IDF cosine** retrieval with **Reciprocal Rank Fusion (RRF)**.
6. `research-workspace-creator/tools-workspace-ops.py` (**Loaded 5th**): Implements concrete domain handlers for **Categories 24–41**, native stdlib OOXML `.docx` and `.xlsx` writers, self-contained inline-SVG HTML report generation, all **15 specialist role transformers**, quality scorecard computation, and the topological multi-agent DAG workflow orchestrator.
7. `research-workspace-creator/tools-test-suite.py` (**Loaded on demand by `self-test` or executed standalone**): Runs automated regression tests across all 41 categories, 15 roles, safety gates, deliverables round-tripping, checkpoint recovery, and packaging hygiene inside an isolated temporary directory.

### Reusable Asset Files

- `research-workspace-creator/asset-default-config.json`: Default operational limits, timeouts, retries, chunking weights, workspace zones, citation styles, configurable provider environment variable names, and safety policy switches.
- `research-workspace-creator/asset-capability-catalog.json`: Full operational registry of all 41 categories, 15 specialist role contracts, and 615 capabilities (`global_id` `1` through `615`).
- `research-workspace-creator/asset-policy-rules.json`: Secret detection regexes, PII patterns, SSRF blocked hosts/CIDRs, SQL read-only rules, SPDX license compatibility rules, and 6 domain workbench presets.
- `research-workspace-creator/asset-output-schemas.json`: JSON Schema definitions enforced before and after capability and workflow runs.
- `research-workspace-creator/asset-prompt-and-report-templates.json`: Structured prompt templates for configurable LLM synthesis and inline-styled Markdown/HTML report templates.
- `research-workspace-creator/asset-sample-research-bundle.json`: Multi-format offline research dataset, claims set, BibTeX/RSS/HTML/Mermaid/SRT fixtures, and 6-stage DAG workflow specification.

---

## 2. Complete Capability List (41 Categories x 15 Specialist Roles = 615 Capabilities)

Every category below supports all **15 specialist roles**, forming a deterministic capability slug `<category-slug>-<role-slug>` and sequential `global_id` (`1`–`615`).

### The 15 Specialist Roles

| Role Index | Role Slug | Role Name | Operational Responsibility |
| :--- | :--- | :--- | :--- |
| 1 | `architect` | Architect | Produces structural blueprints, data contract schemas, isolation boundaries, and state digests. |
| 2 | `researcher` | Researcher | Executes discovery, source inspection, query expansion, and evidence digesting. |
| 3 | `planner` | Planner | Generates phased execution plans, milestone checklists, and resource schedules. |
| 4 | `operator` | Operator | Executes the primary category handler over real files, queries, records, or artifacts. |
| 5 | `analyzer` | Analyzer | Profiles statistical metrics, structural dimensions, and quantitative distributions. |
| 6 | `builder` | Builder | Constructs durable workspace artifacts, databases, graphs, or formatted deliverables. |
| 7 | `indexer` | Indexer | Extracts distinct lexical vocabularies, searchable indexes, and content hashes. |
| 8 | `verifier` | Verifier | Evaluates schema, digest, and truthfulness assertions against produced outputs. |
| 9 | `coordinator` | Coordinator | Prepares inter-stage routing contracts and downstream role handoff recommendations. |
| 10 | `manager` | Manager | Audits quota compliance, approval gate states, and network/execution governance. |
| 11 | `auditor` | Auditor | Issues truthfulness attestations verifying completed vs skipped searches, downloads, and installs. |
| 12 | `automation-agent` | Automation Agent | Computes idempotency digests and executes repeatable batch or DAG tasks. |
| 13 | `recovery-specialist` | Recovery Specialist | Repairs corrupted checkpoints, enforces atomic writes, and manages fallback execution. |
| 14 | `packager` | Packager | Verifies relative-path portability, secret hygiene, and SHA-256 export manifests. |
| 15 | `quality-evaluator` | Quality Evaluator | Scores completeness, provenance integrity, schema validity, and benchmark gates. |

### All 41 Unified Categories

| Cat ID | Global IDs | Category Slug | Category Name | Core Capability Implemented |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 1–15 | `research-strategy-and-investigation-design` | Research Strategy and Investigation Design | Decomposes questions into testable sub-questions, boolean query matrices, falsification rules, and stage plans. |
| 2 | 16–30 | `web-search-and-discovery` | Web Search and Discovery | Ranks local corpus sources and optionally queries configurable HTTP JSON search endpoints (`RWC_SEARCH_API_URL`). |
| 3 | 31–45 | `website-and-link-intelligence` | Website and Link Intelligence | Extracts HTML titles, OpenGraph/canonical metadata, internal vs external links, and domain distributions. |
| 4 | 46–60 | `documentation-and-technical-reference-research` | Documentation and Technical Reference Research | Extracts heading outlines, CLI `--flags`, function/class signatures, and fenced code examples. |
| 5 | 61–75 | `scholarly-and-scientific-research` | Scholarly and Scientific Research | Parses BibTeX entries, authors, publication venues, years, and DOIs. |
| 6 | 76–90 | `news-public-records-and-current-intelligence` | News, Public Records, and Current Intelligence | Parses RSS 2.0 / Atom XML feeds into timelines and detects syndicated duplicates via 3-gram Jaccard similarity. |
| 7 | 91–105 | `enterprise-and-connected-workspace-search` | Enterprise and Connected Workspace Search | Searches mounted workspace exports and configurable `RWC_CONNECTORS_JSON` endpoints while truthfully reporting unavailable apps. |
| 8 | 106–120 | `local-file-and-folder-research` | Local File and Folder Research | Walks directories within depth/file caps, computes SHA-256 hashes, finds duplicate clusters, and runs regex line scans. |
| 9 | 121–135 | `document-reading-and-extraction` | Document Reading and Extraction | Extracts text, headings, tables, and metadata from `.txt`, `.md`, `.html`, `.csv`, `.tsv`, `.json`, `.xml`, `.docx`, `.xlsx`, `.pptx`, and `.pdf`. |
| 10 | 136–150 | `ocr-scans-and-visual-text-recovery` | OCR, Scans, and Visual Text Recovery | Repairs ligatures, broken hyphenation, and OCR spacing; extracts PNG/SVG text chunks and checks `tesseract` availability. |
| 11 | 151–165 | `image-diagram-and-visual-evidence-analysis` | Image, Diagram, and Visual Evidence Analysis | Parses binary headers of PNG, JPEG, GIF, BMP, SVG XML, and Mermaid/DOT node-edge graphs. |
| 12 | 166–180 | `audio-video-and-multimedia-research` | Audio, Video, and Multimedia Research | Parses RIFF/WAV audio parameters and duration, MP3 ID3 headers, MP4 `ftyp` brands, and `.srt`/`.vtt` timecoded transcripts. |
| 13 | 181–195 | `data-acquisition-and-download-management` | Data Acquisition and Download Management | Enforces SSRF guards, byte limits, retries, ETag capture, and approval-gated HTTP/HTTPS downloads. |
| 14 | 196–210 | `web-apis-and-structured-data-retrieval` | Web APIs and Structured Data Retrieval | Validates API endpoints, projects structured JSON fields, and profiles record schemas. |
| 15 | 211–225 | `web-crawling-and-responsible-scraping` | Web Crawling and Responsible Scraping | Evaluates `robots.txt` directives, enforces crawl delays, and scrapes structured HTML tables and headings. |
| 16 | 226–240 | `database-and-query-research` | Database and Query Research | Introspects SQLite schemas or loads CSV/JSON tables and executes read-only guarded `SELECT`/`WITH`/`EXPLAIN` queries. |
| 17 | 241–255 | `data-cleaning-and-transformation` | Data Cleaning and Transformation | Normalizes strings, coerces scalars, deduplicates composite keys, counts nulls, and detects IQR numeric outliers. |
| 18 | 256–270 | `knowledge-extraction-and-structured-capture` | Knowledge Extraction and Structured Capture | Extracts DOIs, URLs, quantitative metrics with units, years, and subject-predicate-object triples. |
| 19 | 271–285 | `evidence-provenance-and-citation-management` | Evidence, Provenance, and Citation Management | Links source SHA-256 digests and formats citations in `apa`, `mla`, `chicago`, `ieee`, `bibtex`, and `markdown`. |
| 20 | 286–300 | `source-evaluation-and-fact-verification` | Source Evaluation and Fact Verification | Cross-checks claims and embedded numeric values against source evidence (`supported`, `contested`, `contradicted`, `unverified`). |
| 21 | 301–315 | `synthesis-analysis-and-reasoning` | Synthesis, Analysis, and Reasoning | Builds multi-source comparison matrices, consensus findings, and explicit evidence gap lists. |
| 22 | 316–330 | `knowledge-base-and-memory-construction` | Knowledge Base and Memory Construction | Persists `knowledge-store.sqlite3` document tables and `kb-graph.json` typed entity-relation graphs. |
| 23 | 331–345 | `semantic-indexing-and-retrieval` | Semantic Indexing and Retrieval | Builds hybrid SQLite FTS5 (BM25) + TF-IDF cosine indexes and retrieves passages via Reciprocal Rank Fusion (RRF). |
| 24 | 346–360 | `workspace-and-sandbox-architecture` | Workspace and Sandbox Architecture | Scaffolds isolated workspace zones (`inputs`, `staging`, `kb`, `indexes`, `checkpoints`, `cache`, `logs`, `deliverables`). |
| 25 | 361–375 | `environment-and-dependency-engineering` | Environment and Dependency Engineering | Audits Python/SQLite runtime versions, installed distributions, optional packages, and requirement constraints. |
| 26 | 376–390 | `package-repository-and-tool-acquisition` | Package, Repository, and Tool Acquisition | Validates repository URLs and executes approval-gated shallow `git clone` or offline acquisition plans. |
| 27 | 391–405 | `code-execution-and-research-automation` | Code Execution and Research Automation | Runs approval-gated Python scripts in a temporary directory with stripped secret env vars and strict timeouts. |
| 28 | 406–420 | `reproducibility-and-experiment-tracking` | Reproducibility and Experiment Tracking | Appends seeded, config-hashed experiment records and metrics to `kb/experiments.jsonl`. |
| 29 | 421–435 | `caching-performance-and-scale` | Caching, Performance, and Scale | Manages content-addressed SHA-256 disk caching with TTL expiration and LRU byte-budget pruning. |
| 30 | 436–450 | `monitoring-logging-and-observability` | Monitoring, Logging, and Observability | Aggregates `events.jsonl` telemetry by level and computes `p50` and `p95` latency percentiles. |
| 31 | 451–465 | `security-privacy-and-access-control` | Security, Privacy, and Access Control | Scans content for API keys/tokens and PII, generates redacted previews, and audits SSRF/path-jail settings. |
| 32 | 466–480 | `copyright-licensing-and-research-ethics` | Copyright, Licensing, and Research Ethics | Evaluates SPDX licenses (`MIT`, `Apache-2.0`, `GPL-3.0`, `CC-BY-4.0`, `CC-BY-NC-4.0`) for commercial/copyleft compatibility. |
| 33 | 481–495 | `research-collaboration-and-handoff` | Research Collaboration and Handoff | Writes structured `deliverables/handoff-packet.json` bundles with verified source lists and next steps. |
| 34 | 496–510 | `reporting-and-deliverable-creation` | Reporting and Deliverable Creation | Generates `.md`, offline self-contained `.html` (with inline CSS and SVG charts), `.json`, `.csv`, `.docx`, and `.xlsx` reports. |
| 35 | 511–525 | `file-export-packaging-and-sharing` | File Export, Packaging, and Sharing | Performs secret pre-flight scans, builds SHA-256 export manifests, and creates approval-gated flat `.zip` exports. |
| 36 | 526–540 | `research-update-and-knowledge-maintenance` | Research Update and Knowledge Maintenance | Computes unified diffs between baseline and updated texts and flags sources exceeding staleness thresholds. |
| 37 | 541–555 | `failure-recovery-and-resilient-research` | Failure Recovery and Resilient Research | Persists atomic `.json` + `.json.bak` checkpoints and automatically restores corrupted run states. |
| 38 | 556–570 | `research-quality-assurance-and-benchmarking` | Research Quality Assurance and Benchmarking | Runs retrieval accuracy and latency benchmarks and computes rubric scores. |
| 39 | 571–585 | `domain-specific-research-workbenches` | Domain-Specific Research Workbenches | Activates specialized domain presets (`software-engineering`, `scientific-literature`, `market-competitive`, `policy-regulatory`, `security-threat`, `data-journalism`). |
| 40 | 586–600 | `agent-knowledge-improvement-and-learning-loops` | Agent Knowledge Improvement and Learning Loops | Analyzes gated/skipped operational events and generates parameter and prompt-template refinements. |
| 41 | 601–615 | `orchestration-and-multi-agent-research` | Orchestration and Multi-Agent Research | Validates multi-step DAG schemas, topologically orders tasks, enforces cycle checks, and orchestrates checkpointed multi-role runs. |

---

## 3. Setup, Dependencies, and Exact Commands

### Runtime Requirements and Optional Dependencies

- **Required Runtime:** Python `>= 3.10` (tested on Python `3.13`) with standard library `sqlite3` (including FTS5). **Zero third-party packages are required** for core operation, hybrid FTS5 + TF-IDF indexing, HTML/XML/CSV/JSON parsing, or native `.docx`/`.xlsx` reading and generation.
- **Optional Enhancer Packages:** Automatically detected at runtime when installed:
  - `pypdf >= 4.0,< 7.0` — Enhanced multi-page PDF text extraction (`bash -c 'python3 -m pip install "pypdf>=4.0,<7.0"'`). Without `pypdf`, Category 9 falls back to native stdlib PDF stream/FlateDecode extraction and records `"extractor": "stdlib-pdf-stream-fallback"`.

### Core CLI Commands (Run from the Parent Skill Root)

```bash
# 1. Built-in help
bash -c 'python3 research-workspace-creator/tools-research-workspace.py --help'

# 2. List summary of all 41 categories and 15 specialist roles
bash -c 'python3 research-workspace-creator/tools-research-workspace.py list-capabilities --summary'

# 3. Search capabilities by category, role, or keyword
bash -c 'python3 research-workspace-creator/tools-research-workspace.py list-capabilities --category 23 --role indexer'

# 4. Inspect full capability contract (triggers, inputs, outputs, permissions, recovery)
bash -c 'python3 research-workspace-creator/tools-research-workspace.py inspect-capability semantic-indexing-and-retrieval-indexer'

# 5. Parse a plain text, Markdown, copied HTML, JSON, or PDF capability list
bash -c 'python3 research-workspace-creator/tools-research-workspace.py parse-spec'

# 6. Initialize a clean isolated workspace
bash -c 'python3 research-workspace-creator/tools-research-workspace.py init-workspace --workspace-dir workspace-runs/demo'

# 7. Run any capability by slug, global ID (1..615), or --category + --role
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --capability semantic-indexing-and-retrieval-indexer --query "hybrid BM25 TF-IDF recall" --workspace-dir workspace-runs/demo --output workspace-runs/demo/index-result.json'

# 8. Execute the 6-stage multi-agent DAG workflow with checkpointing
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-workflow --workspace-dir workspace-runs/wf-demo --checkpoint-id wf-01 --output workspace-runs/wf-demo/summary.json'

# 9. Verify a result envelope JSON or workspace provenance hash chain
bash -c 'python3 research-workspace-creator/tools-research-workspace.py verify --input workspace-runs/demo/index-result.json'

# 10. Run the isolated built-in self-test suite
bash -c 'python3 research-workspace-creator/tools-research-workspace.py self-test'
```

---

## 4. Inputs, Outputs, Parameters, Schemas, and Configuration

### Global and Subcommand Parameters

- `--config <rel-path>`: Relative path to JSON configuration (defaults to `research-workspace-creator/asset-default-config.json`).
- `--capability <id|slug|name>`: Capability numeric ID (`1`–`615`), hyphenated slug, or exact catalog Skill Name.
- `--category <id|slug|name>` and `--role <role-slug>`: Select capability by category (`1`–`41`) and specialist role (`operator` default).
- `--input <rel-path>` / `--input-text <string>`: Input file/directory or inline payload.
- `--query <string>`: Research question, retrieval query, or read-only SQL statement.
- `--params <json|@rel-file>`: Handler-specific JSON object (for example `'{"citation_style":"ieee"}'`).
- `--workspace-dir <rel-path>`: Isolated workspace root for durable KB, indexes, logs, checkpoints, and deliverables.
- `--output <rel-path>`: Optional path to write the structured JSON result envelope.
- `--approve <action>`: Repeatable flag granting explicit permission for gated operations.
- `--enable-network`: Opt-in switch enabling bounded HTTP/HTTPS network operations (disabled by default).
- `--format json|markdown|text`: Output presentation mode (`json` default).

### Result Envelope Schema (`capability_result_envelope`)

Every `run-capability` call validates its output against `research-workspace-creator/asset-output-schemas.json` before returning:

- `status`: `"ok"`, `"approval_required"`, `"validation_error"`, `"recovered"`, or `"error"`.
- `capability`: Exact `global_id`, `slug`, `name`, `category_id`, `category_slug`, `role_slug`, and `role_name`.
- `role_execution`: Specialist role output (`Architect` blueprint, `Verifier` verdict, `Auditor` attestation, etc.).
- `domain_result`: Category-specific structured output.
- `truthfulness_accounting`: Exact real counts for `network_enabled`, `network_used`, `searches_executed`, `downloads_completed`, `installations_performed`, `subprocesses_executed`, `cache_hits`, `files_created`, and `skipped_or_gated`.
- `provenance`: Chained SHA-256 entry (`entry_id`, `source_refs`, `output_refs`, `payload_digest`, `prev_sha256`, `sha256`).
- `quality_evaluation`: Quantitative `0`–`100` scorecard (`overall_score`, `grade`, `dimensions`, `passed_gate`).
- `artifacts`: Portable forward-slash relative paths of all created files.

---

## 5. Explanation and Examples Across Capability Domains

### Categories 1–7: Research Strategy, Web, Docs, Scholarly, News, and Enterprise Discovery

```bash
# Category 1 (Architect): Decompose an investigation into hypotheses, boolean queries, and milestones
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 1 --role architect --query "Compare FTS5 lexical ranking and vector cosine fusion"'

# Category 3 (Analyzer): Extract internal/external link intelligence and OpenGraph tags from HTML
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 3 --role analyzer'

# Category 5 (Researcher) & Category 6 (Operator): Parse BibTeX DOIs and deduplicate RSS feed timelines
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 5 --role researcher'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 6 --role operator'
```

### Categories 8–15: Local Files, Documents, OCR, Visual/Audio Evidence, Downloads, APIs, and Scraping

```bash
# Category 8 (Indexer): Hash files, detect duplicate SHA-256 clusters, and scan regex matches
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 8 --role indexer --input research-workspace-creator --params "{\"pattern\":\"ProvenanceLedger\"}"'

# Category 9 (Operator): Extract structured text/tables from .md, .html, .csv, .json, .docx, .xlsx, .pptx, or .pdf
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 9 --role operator --input research-workspace-creator/knowledge-readme.md'

# Categories 10, 11, 12: Post-OCR text repair, Mermaid/PNG/JPEG/SVG visual inspection, and WAV/SRT transcript parsing
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 10 --role recovery-specialist'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 11 --role analyzer'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 12 --role operator'

# Categories 13, 14, 15: Validated download manifests, structured API projection, and robots.txt-compliant scraping
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 15 --role auditor'
```

### Categories 16–23: SQLite Queries, Cleaning, Extraction, Citations, Fact Verification, KB, and Hybrid Indexing

```bash
# Category 16 (Operator): Execute read-only guarded SQL query over benchmark records
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 16 --role operator --query "SELECT engine, recall_top5, latency_ms FROM benchmarks WHERE recall_top5 > 85 ORDER BY recall_top5 DESC"'

# Category 17 (Operator) & Category 18 (Builder): Clean dirty tabular data and extract metrics/triples
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 17 --role operator'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 18 --role builder'

# Category 19 (Packager) & Category 20 (Verifier): Format IEEE/APA/BibTeX citations and verify claims against sources
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 19 --role packager --params "{\"citation_style\":\"ieee\"}"'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 20 --role verifier'

# Category 22 & Category 23: Build SQLite+Graph KB and query hybrid FTS5 + TF-IDF RRF search index
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 22 --role builder --workspace-dir workspace-runs/kb-test'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 23 --role indexer --workspace-dir workspace-runs/kb-test --query "atomic checkpoint recovery provenance"'
```

### Categories 24–41: Workspaces, Code Execution, Security, Licensing, Deliverables, Recovery, and Multi-Agent DAGs

```bash
# Category 27 (Automation Agent): Approval-gated sandboxed Python execution
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 27 --role automation-agent --approve code_or_subprocess_execution --input-text "print(sum([82.1, 84.6, 91.4]) / 3)"'

# Categories 31 & 32: Secret/PII security audit and SPDX license compatibility check
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 31 --role auditor'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 32 --role manager'

# Category 34 (Builder): Generate .md, offline SVG .html, .json, .csv, .docx, and .xlsx deliverables
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 34 --role builder --workspace-dir workspace-runs/reports'

# Category 37 (Recovery Specialist) & Category 41 (Coordinator): Corrupt-checkpoint recovery and multi-agent orchestration
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 37 --role recovery-specialist --params "{\"simulate_corruption\":true}"'
bash -c 'python3 research-workspace-creator/tools-research-workspace.py run-capability --category 41 --role coordinator --workspace-dir workspace-runs/orchestrated'
```

---

## 6. Safety and Approval Rules

1. **Relative Path Jail:** Parent-directory traversal (`..`), null bytes, drive-letter paths, and absolute paths are rejected by default (`ValidationError`, exit code `2`). Overriding requires both `--allow-external-paths` and `--approve credential_or_policy_override`.
2. **Explicit Approval Gates (Exit Code `3` when unapproved):**
   - `code_or_subprocess_execution`: Required before Category 27 runs Python scripts or subprocesses.
   - `sql_write_if_non_select`: Required before Category 16 runs any SQL statement outside `SELECT`, `WITH`, `EXPLAIN`, or read-only introspection `PRAGMA`s.
   - `package_or_repo_acquisition`: Required (with `--enable-network`) before Category 26 clones remote repositories.
   - `network_download_when_enabled`: Required (with `--enable-network`) before Category 13 downloads remote URLs.
   - `external_publish_or_overwrite_archive`: Required before Category 35 writes `.zip` distribution bundles.
   - `overwrite_protected` / `destructive_delete`: Required when `protect_existing` is set on existing deliverables.
3. **SSRF & Network Isolation:** Outbound network access is disabled by default. All URLs are validated against `http`/`https` schemes and blocked if targeting `localhost`, loopback (`127.0.0.0/8`, `::1`), RFC1918 private ranges, or cloud metadata endpoints (`169.254.169.254`).
4. **Secret Hygiene:** Outputs are scanned against `research-workspace-creator/asset-policy-rules.json` and automatically scrub matching secret tokens (`[REDACTED:<rule_id>]`). Subprocess environments strip variables containing `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, or `CREDENTIAL`.

---

## 7. Limits and Unavailable Integrations

- **Configurable Resource Bounds (`asset-default-config.json`):** `timeout_seconds` (`30`), `max_retries` (`2`), `max_input_bytes` (`25 MB`), `max_download_bytes` (`10 MB`), `max_files_scanned` (`2000`), `max_directory_depth` (`8`), `max_sql_rows` (`500`), `max_code_exec_seconds` (`10`), `cache_max_bytes` (`50 MB`).
- **External APIs and Enterprise Connectors:** No private endpoints, paid API keys, or tenant credentials are embedded. Live search (`RWC_SEARCH_API_URL`), LLM endpoints (`RWC_LLM_API_URL`), and enterprise connectors (`RWC_CONNECTORS_JSON`) remain configurable via environment variables; when unconfigured, capabilities execute deterministic local corpus/fixture analysis and record skipped external calls in `truthfulness_accounting.skipped_or_gated`.
- **Host Binary Availability:** Raster image OCR (`tesseract`) and container stream probing (`ffprobe`) are optional host binaries. When absent on the host machine, Categories 10 and 12 execute embedded chunk/header/transcript parsers and post-OCR normalization while reporting `"tesseract_cli_available": false` / `"ffprobe_available": false`.
- **Platform and Hosting Caveats:** Local validation verifies relative paths from a parent skill root (`<skill-root>/SKILL.md` + `<skill-root>/research-workspace-creator/`). If a hosted catalog, tenant sandbox, permission policy, or network firewall blocks access to external URLs or mounted files at runtime, treat that failure as a hosting, catalog, permission, or platform environment constraint rather than a local package path defect.

---

## 8. Validation and Tests Completed

All 16 validation gates were executed and verified against the packaged files:

1. **Help and Capability Listing:** Verified `--help`, `help`, `list-capabilities --summary` (`41` categories, `15` roles, `615` capabilities), and filtered queries.
2. **All 41 Categories & 15 Specialist Roles:** Executed every category (`1`–`41`) and cycled all `15` specialist roles through `run_single_capability` and CLI commands, confirming `100%` schema conformance and SHA-256 provenance recording.
3. **Input Handling (`parse-spec`):** Verified extraction on plain text, Markdown numbered lists, noisy copied HTML (with CSS classes, `data-*` attributes, and numbering prefixes), JSON, and the 78-page `615`-skill catalog PDF.
4. **Input Sensitivity:** Verified that changing queries (`"BM25 TF-IDF recall"` vs `"robots crawl delay ETag"`) and claims (`91.4%` vs `45.0%`) deterministically changes top-ranked sources (`src-001` vs `src-003`) and fact-verification verdicts (`supported` vs `contradicted`).
5. **Negative, Missing, Safety, and Recovery Paths:** Verified structured errors and exit codes on missing inputs, `../` path traversal (`exit 2`), `http://169.254.169.254` SSRF (`exit 4`), unapproved `DROP TABLE` SQL (`exit 3`), unapproved Python execution (`exit 3`), approved sandboxed Python execution (`exit 0`), corrupted primary checkpoint automatic `.bak` restoration (`was_recovered: true`), and 6-step DAG checkpoint `--resume`.
6. **Artifact Inspection & Round-Trip Parsing:** Opened and parsed generated `.md`, self-contained `.html` (confirming inline CSS and `<svg>` charts with zero external network dependencies), `.json`, `.csv`, `.docx`, and `.xlsx` deliverables using `extract_document_file`.
7. **Clean Skill-Root & ZIP Extraction Re-Runs:** Tested from a clean temporary parent skill root beside a test `SKILL.md` using strictly relative paths, verified `0` subfolders (`0` `__pycache__`), `0` nested archives, and re-ran after extracting `research-workspace-creator-skill-tool.zip` in a separate temporary directory.

---

## 9. Troubleshooting, Recovery, Extension, and Maintenance

- **`approval_required` (Exit Code `3`):** Inspect `details.required_approval` in the JSON response, review the target SQL/script/URL/archive action, and pass `--approve <required_approval>` only when authorized.
- **Interrupted or Corrupted Workflow State:** Run `bash -c 'python3 research-workspace-creator/tools-research-workspace.py recover --workspace-dir <dir> --checkpoint-id <id>'` to restore from the atomic `.json.bak` checkpoint, then resume with `run-workflow --resume --checkpoint-id <id>`.
- **Extending Categories or Workbenches:** Add domain workbench presets in `research-workspace-creator/asset-policy-rules.json` under `domain_workbenches`, or add category entries in `research-workspace-creator/asset-capability-catalog.json` mapped to a handler in `tools-ingestion-extractors.py`, `tools-knowledge-index.py`, or `tools-workspace-ops.py`. Always keep filenames lowercase hyphenated with `tools-`, `knowledge-`, or `asset-` prefixes, preserve `sys.dont_write_bytecode = True`, and re-run `self-test` after any change.
