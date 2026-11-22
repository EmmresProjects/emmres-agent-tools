# Software Engineering Unified Skill Tool (`software-engineering`)

## 1. Purpose and Capability Overview

`software-engineering` is a reusable, self-contained production Skill Tool component implementing the **Comprehensive Software Engineering Skill Catalog** across **51 categories** and **1,020 individual engineering capabilities**. It is engineered to sit directly beside a parent skill package's root `SKILL.md` without internal subfolders, hidden caches, or external package installation requirements.

Core capabilities include:
- **Multi-Format Capability Specification Ingestion**: Parses a single capability, a numbered Skill Tool list, one category, or multi-category catalogs from **plain text, Markdown, or copied HTML** (stripping HTML tags, CSS classes, `data-*` attributes, numbering marks, and irregular whitespace).
- **1,020 Individually Addressable Engineering Capabilities**: Every skill from `01.01` (`S0001`, `requirement-decomposition`) through `51.20` (`S1020`, `final-delivery-orchestrator`) has a unique lowercase hyphenated slug, explicit operational mode, deterministic execution handler, and pass/fail verification checks.
- **Disambiguated Cross-Category Capabilities**: Overlapping skill titles across distinct engineering disciplines are preserved with separate slugs and responsibilities rather than collapsed:
  - `09.18` `mobile-platform-performance-profiling` (Mobile UI thread, 60/120fps frame pacing, battery & lifecycle) vs `36.09` `mobile-engineering-performance-profiling` (Quantitative latency/memory regression gates & statistical p95 benchmarking).
  - `13.19` `software-architecture-documentation` (Structural C4 system/container/component modeling & fitness functions) vs `39.03` `documentation-engineering-architecture-docs` (Docs-as-code authoring, case-sensitive link hygiene & freshness auditing).
  - `29.18` `network-distributed-tracing` (Wire-level W3C `traceparent` context propagation & hop causality) vs `37.03` `observability-distributed-tracing` (OpenTelemetry SDK span instrumentation, head/tail sampling & SLO burn-rate correlation).
  - Language-aware slugging cleanly distinguishes `c-application-builder` (`06.01`), `csharp-application-builder` (`07.01`), `c-standard-version-targeting` (`06.03`), and `cpp-standard-version-targeting` (`06.04`).
- **Multi-Step DAG Workflows & Checkpoint Recovery**: Executes topological skill pipelines with per-step state journaling (`checkpoint-state.json`) and deterministic `recover` resumption.
- **Human Approval Gate & Bounded Verification**: Requires explicit human approval (`--approve` + `--approval-reason`) before any `apply`-mode destructive, irreversible, privileged, publishing, financial, credential, or security-sensitive action.

## 2. Architecture, File Roles, and Execution Order

All 19 files reside flat inside `software-engineering/` with strict `tools-`, `knowledge-`, and `asset-` prefixes:

### Runnable and Supporting `tools-` Files (11 Files)
1. `software-engineering/tools-workbench.py` — **Primary runnable CLI and programmatic entry point** (`Python 3.10+`). Parses arguments, loads runtime config and safety policy, validates inputs and output paths before execution, enforces approval gates, dispatches skills or DAG workflows, writes atomic artifacts, and emits schema-validated envelopes.
2. `software-engineering/tools-run.sh` — **Portable Bash launcher** (`#!/usr/bin/env bash`, `set -euo pipefail`). Exports `PYTHONDONTWRITEBYTECODE=1`, verifies `python3` availability, and invokes `software-engineering/tools-workbench.py`.
3. `software-engineering/tools-core-runtime.py` — **First module loaded by `tools-workbench.py`**. Provides bytecode-free dynamic sibling loading (`sys.dont_write_bytecode = True`), relative resource resolution from the future skill root, path confinement, bounded retry with exponential backoff, `SIGINT`/`SIGTERM` cleanup handlers, secret redaction, checkpoint journaling, and output schema validation.
4. `software-engineering/tools-input-parser.py` — **Loaded second for catalog indexing and `parse-input`**. Strips HTML/CSS/`data-*`/numbering noise from plain text, Markdown, and copied HTML, and builds the O(1) `CatalogIndex` over all 51 categories and 1,020 skills.
5. `software-engineering/tools-runtime-verifier.py` — **Loaded before skill execution**. Probes host toolchains honestly and runs bounded (`shell=False`, timeout-enforced, isolated temp directory) checks across Python `ast`, SQLite3, `node`, `gcc`, `g++`, `javac`, `bash -n`, `perl -c`, and `zipfile`.
6. `software-engineering/tools-node-analyzer.mjs` — **Invoked on-demand by `tools-runtime-verifier.py`** as a separate Node.js ES module when `node` is installed on the host, performing V8 syntax checks, import/export extraction, async loop/blocking Sync inspection, and `package.json` validation.
7. `software-engineering/tools-engine-architecture.py` — **Domain Engine 1** (Categories 01, 13, 46, 49, 51 — 100 skills): Requirement decomposition, ADRs, FMEA, C4 modeling, Python 2-to-3 & data-format migration, Tarjan SCC repo partitioning, diff blast-radius review, Task Router, DAG topological scheduling, parallel workers, and 3-way conflict reconciliation.
8. `software-engineering/tools-engine-languages.py` — **Domain Engine 2** (Categories 02–11, 33 — 220 skills): 35+ language detection, Python AST/type/memory profiling, JS/TS, Java/`javac`, C/C++ `gcc`/`g++`, C#/.NET, Go/Rust ownership & `unsafe` auditing, Mobile frame budgets, WebAssembly WAT checking, Bash/ReDoS regex analysis, and a full lexer -> parser -> optimizer -> type-checker -> bytecode VM compiler pipeline.
9. `software-engineering/tools-engine-data-ai.py` — **Domain Engine 3** (Categories 12, 18–23, 45 — 160 skills): Sorting/Dijkstra/KMP/DP/Convex-Hull algorithms, live SQLite3 DDL/DML/`EXPLAIN QUERY PLAN` execution, NoSQL quorums, ETL & Data Quality expectations, ML ROC-AUC/F1/PSI drift metrics, offline-safe RAG & prompt-injection defense, SVG synthesis, Gaussian linear solver, and RK4 ODE integration.
10. `software-engineering/tools-engine-systems-apps.py` — **Domain Engine 4** (Categories 14–17, 41–44 — 160 skills): OpenAPI 3.1 & token-bucket rate limiting, HTML5 semantic & exact WCAG 2.1 contrast auditing, SHA-256 hash-chained audit logs, full-stack blueprints, embedded CRC16/CRC32 & RTOS Rate-Monotonic bounds, deterministic ECS + A* pathfinding, SHA-256 Merkle proofs + smart-contract static checks, and constant-time HMAC webhook verification.
11. `software-engineering/tools-engine-operations-quality.py` — **Domain Engine 5** (Categories 24–32, 34–40, 47, 48, 50 — 380 skills): Cloud CIDR/SLA math, CI/CD security linting, Dockerfile & Terraform Policy-as-Code scanning, POSIX paging diagnostics, Little's Law & W3C `traceparent`, SAST & secret detection, live `unittest` execution, stack-trace & deadlock cycle detection, SemVer 2.0, McCabe cyclomatic complexity, p95 micro-benchmarking, SRE error-budget burn rates, Git hygiene, relative Markdown link validation, CycloneDX 1.5 SBOMs, runtime currency checks, and Spec-to-Test / ZIP reopen verification.

### Knowledge File (1 File)
12. `software-engineering/knowledge-readme.md` — Consolidated operational documentation, schemas, safety rules, complete 1,020-skill reference, validation report, limitations, and maintenance guide.

### Reusable `asset-` Resources (7 Files)
13. `software-engineering/asset-skill-catalog.json` — Complete machine-readable catalog of all 51 categories and 1,020 skills with coordinates, slugs, responsibilities, engine bindings, and approval scopes.
14. `software-engineering/asset-default-config.json` — Default paths, resource limits, retry/backoff settings, offline-safe provider settings, and verifier toggles.
15. `software-engineering/asset-safety-policy.json` — Approval policy scopes, protected system write roots, allowlisted binaries, SAST regex rules (`SEC-001`..`SEC-007`), and secret detection patterns.
16. `software-engineering/asset-output-schema.json` — JSON Schema (Draft 2020-12) governing all structured execution envelopes.
17. `software-engineering/asset-prompt-templates.json` — Configurable prompt and engineering specification templates for decomposition, ADRs, STRIDE threat models, RCA, RAG synthesis, and agent handoffs.
18. `software-engineering/asset-workflow-presets.json` — Multi-skill DAG workflow definitions (`greenfield-service-delivery`, `security-and-supply-chain-gate`, `incident-root-cause-and-patch`, `data-and-ml-pipeline-validation`).
19. `software-engineering/asset-sample-fixture.json` — Multi-domain test and demonstration fixture dataset (requirements, HTML snippet, clean/flawed code, SQL schema, Dockerfile, Terraform, compiler program, graph/ML/numerical inputs, stack trace, and traceability map).

## 3. Setup, Dependencies, and Exact Run Commands

### Runtime Requirements and Dependencies
- **Required**: `Python >= 3.10` (tested on `Python 3.13.16`), using **100% Python standard library** (`ast`, `sqlite3`, `unittest`, `zipfile`, `difflib`, `hashlib`, `hmac`, `ipaddress`, `concurrent.futures`, `subprocess`, `json`, `csv`, `xml`, `re`, `tracemalloc`, `statistics`). **Zero third-party `pip` or `npm` packages are required.**
- **Optional Host Toolchains (auto-detected via `environment`)**:
  - `node` (`>= 18`, tested with `v20.20.2`) enables live V8 inspection via `software-engineering/tools-node-analyzer.mjs`.
  - `gcc` / `g++` (tested with `Debian 14.2.0`), `javac` (tested with `OpenJDK 11`), `bash` (`5.2`), `git` (`2.47`), `perl`, `Rscript`, and `openssl` enable live syntax/compile checks in their respective categories. When any optional toolchain is absent, the workbench degrades gracefully to static verification and discloses the unavailable binary in `limitations`.

### Exact Commands (Run from the Future Skill Root in `bash`)

```bash
# 1. Built-in help
python3 software-engineering/tools-workbench.py --help

# 2. Portable Bash launcher
bash software-engineering/tools-run.sh capabilities --summary --format table

# 3. List all 1,020 skills (or filter by category, keyword, or approval gate)
python3 software-engineering/tools-workbench.py capabilities
python3 software-engineering/tools-workbench.py capabilities --category 30 --format table
python3 software-engineering/tools-workbench.py capabilities --search "distributed tracing"
python3 software-engineering/tools-workbench.py capabilities --requires-approval

# 4. Inspect a single skill's full contract
python3 software-engineering/tools-workbench.py inspect --skill 33.09

# 5. Parse plain text, Markdown, or copied HTML capability lists (and optionally execute)
python3 software-engineering/tools-workbench.py parse-input --text "1. REST API Design: Build OpenAPI 3.1 endpoints" --execute

# 6. Route a natural-language engineering task to the best skills
python3 software-engineering/tools-workbench.py route --text "Detect SQL injection, audit Dockerfile, and generate SBOM"

# 7. Run a single skill, comma-separated skill list, full category, or multi-step DAG workflow
python3 software-engineering/tools-workbench.py run --skill requirement-decomposition --input software-engineering/asset-sample-fixture.json
python3 software-engineering/tools-workbench.py run --category 18
python3 software-engineering/tools-workbench.py run --workflow greenfield-service-delivery --output se-run-artifacts

# 8. Verify a source file, directory, or final ZIP deliverable
python3 software-engineering/tools-workbench.py verify --input software-engineering/tools-workbench.py

# 9. Resume an interrupted workflow from its checkpoint journal
python3 software-engineering/tools-workbench.py recover --checkpoint se-run-artifacts/checkpoint-state.json --output se-run-artifacts

# 10. Probe host runtime matrix and run built-in validation suite
python3 software-engineering/tools-workbench.py environment
python3 software-engineering/tools-workbench.py selftest
```

## 4. Inputs, Outputs, Parameters, Schemas, and Configuration

### CLI Parameters and Environment Overrides
- `--input <path>`: File (UTF-8 source, JSON, CSV, SQL, Markdown, HTML, or `.zip`) or workspace directory. Validated against `max_input_bytes` (`10,485,760` bytes default) before execution begins.
- `--text <string>`: Inline specification, code snippet, SQL statement, regex, or compiler expression.
- `--param key=value`: Repeatable domain parameter overrides (for example `--param fps=120`, `--param slo_pct=99.95`, `--param fg=#0f172a`, `--param budget_ms=5.0`).
- `--output <path>`: Output directory (writes generated files plus `execution-report.json`) or `.json`/`.md` report path.
- `--config <path>` / `--policy <path>` / `--prompts <path>`: Override `software-engineering/asset-default-config.json`, `software-engineering/asset-safety-policy.json`, or `software-engineering/asset-prompt-templates.json`.
- `--format json|markdown|table`: Structured JSON (default), human-readable Markdown report, or tabular summary.
- Environment variables: `SE_WORKBENCH_TIMEOUT` (seconds, `1..600`), `SE_WORKBENCH_PROVIDER`, `SE_WORKBENCH_MODEL`, `SE_WORKBENCH_MODEL_ENDPOINT`, `SE_WORKBENCH_API_KEY`.

### Structured Output Envelope (`software-engineering/asset-output-schema.json`)
Every command returns a deterministic JSON envelope (or rendered Markdown/table view) containing:
- `status`: `"ok"`, `"failed"`, `"approval_required"`, or `"recovered"`
- `run_id`, `tool` (`"software-engineering"`), `command`, `timestamp_utc`, `duration_ms`, `inputs_validated`
- `approval`: `{ "required", "granted", "scope", "action_mode", "reason" }`
- `results`: Skill-, workflow-, or command-specific structured engineering output
- `verification_checks`: Array of `{ "check_id", "name", "status": "passed|failed|warning|skipped", "details" }`
- `artifacts`: Array of `{ "path", "kind", "bytes", "sha256" }` for every written file
- `limitations`: Explicit runtime or external-service disclosures
- `errors`: Array of `{ "code", "message" }`

### Exit Codes
- `0`: Success (`status` is `"ok"` or `"recovered"`)
- `1`: Verification check failure or post-retry execution failure
- `2`: Input or argument validation error before execution (`INPUT_NOT_FOUND`, `INVALID_INPUT_JSON`, `UNKNOWN_SKILL`, `AMBIGUOUS_SKILL_NAME`)
- `3`: Path confinement violation (`FORBIDDEN_OUTPUT_PATH`)
- `4`: Human Approval Gate blocked sensitive action (`APPROVAL_REQUIRED`, `APPROVAL_REASON_REQUIRED`)
- `5`: Deterministic workflow interruption saved to checkpoint (`SIMULATED_INTERRUPTION`)
- `130`: Signal cancellation (`SIGINT`/`SIGTERM`) with automatic temp-dir cleanup

## 5. Safety, Sandboxing, and Human Approval Rules

1. **Human Approval Gate**: 48 capabilities across 7 sensitive scopes (`destructive`, `irreversible`, `privileged`, `publishing`, `financial`, `credential`, `security-sensitive`)—such as `production-deployment`, `rollback-execution`, `history-rewriting`, `secret-removal-from-history`, `package-publishing`, `artifact-signing`, `key-management`, `privilege-boundary-handling`, `stripe-integration`, and `paypal-integration`—default safely to `--action-mode plan`. Requesting `--action-mode apply` or `--force-overwrite` on an existing report blocks immediately (`exit_code=4`, `status="approval_required"`) unless `--approve` and a non-empty `--approval-reason "..."` are supplied.
2. **Bounded Process Execution**: External compiler/syntax verifiers (`node`, `gcc`, `g++`, `javac`, `bash`, `perl`) are invoked strictly from the `allowed_verifier_binaries` allowlist with `shell=False`, `stdin=DEVNULL`, sanitized environment variables, isolated temporary directories, and `command_timeout_seconds` (`25s` default).
3. **Zero Embedded Secrets or Unbounded Network Calls**: No API keys, tokens, private URLs, or tenant identifiers exist anywhere in the package. Live network execution is disabled by default (`network_execution_enabled: false`). Detected secrets in scanned inputs are automatically masked (`[REDACTED]`).
4. **Filesystem Confinement & Bytecode Hygiene**: System root directories are write-blocked by `software-engineering/asset-safety-policy.json`, and every Python file sets `sys.dont_write_bytecode = True` before imports so `__pycache__/` is never created inside `software-engineering/`.

## 6. Complete Capability Reference and Examples (51 Categories, 1,020 Skills)

### Category 01: Engineering Agent Foundations (`engineering-agent-foundations`)

- **Engine**: `software-engineering/tools-engine-architecture.py`
- **How It Works**: Decomposes engineering requirements into atomic FR IDs, numeric/technology constraints, Given/When/Then acceptance criteria, ADRs, FMEA RPN tables, Tarjan dependency graphs, and Definition-of-Done gates.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill requirement-decomposition --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `01.01` | `S0001` | `requirement-decomposition` | Requirement Decomposition | `analyze` | `none` |
| `01.02` | `S0002` | `technical-constraint-extraction` | Technical Constraint Extraction | `analyze` | `none` |
| `01.03` | `S0003` | `acceptance-criteria-engineering` | Acceptance Criteria Engineering | `analyze` | `none` |
| `01.04` | `S0004` | `assumption-register` | Assumption Register | `analyze` | `none` |
| `01.05` | `S0005` | `risk-driven-planning` | Risk-Driven Planning | `analyze` | `none` |
| `01.06` | `S0006` | `architecture-decision-records` | Architecture Decision Records | `analyze` | `none` |
| `01.07` | `S0007` | `incremental-delivery-planning` | Incremental Delivery Planning | `analyze` | `none` |
| `01.08` | `S0008` | `thin-vertical-slice-builder` | Thin Vertical Slice Builder | `build` | `none` |
| `01.09` | `S0009` | `dependency-mapping` | Dependency Mapping | `analyze` | `none` |
| `01.10` | `S0010` | `interface-contract-definition` | Interface Contract Definition | `analyze` | `none` |
| `01.11` | `S0011` | `failure-mode-analysis` | Failure Mode Analysis | `analyze` | `none` |
| `01.12` | `S0012` | `complexity-estimation` | Complexity Estimation | `analyze` | `none` |
| `01.13` | `S0013` | `technical-feasibility-analysis` | Technical Feasibility Analysis | `analyze` | `none` |
| `01.14` | `S0014` | `build-versus-buy-analysis` | Build-versus-Buy Analysis | `analyze` | `none` |
| `01.15` | `S0015` | `technology-selection` | Technology Selection | `analyze` | `none` |
| `01.16` | `S0016` | `repository-orientation` | Repository Orientation | `analyze` | `none` |
| `01.17` | `S0017` | `codebase-capability-mapping` | Codebase Capability Mapping | `analyze` | `none` |
| `01.18` | `S0018` | `change-impact-analysis` | Change Impact Analysis | `analyze` | `none` |
| `01.19` | `S0019` | `engineering-work-breakdown` | Engineering Work Breakdown | `analyze` | `none` |
| `01.20` | `S0020` | `definition-of-done-enforcement` | Definition of Done Enforcement | `verify` | `none` |

### Category 02: Programming Language Intelligence (`programming-language-intelligence`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Classifies 35+ programming languages via lexical/syntax signatures, detects minimum language version features, and evaluates type, memory, concurrency, and FFI interoperability contracts.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill language-identification --input software-engineering/tools-workbench.py
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `02.01` | `S0021` | `language-identification` | Language Identification | `analyze` | `none` |
| `02.02` | `S0022` | `language-version-detection` | Language Version Detection | `verify` | `none` |
| `02.03` | `S0023` | `syntax-and-semantic-modeling` | Syntax and Semantic Modeling | `analyze` | `none` |
| `02.04` | `S0024` | `type-system-reasoning` | Type System Reasoning | `analyze` | `none` |
| `02.05` | `S0025` | `memory-model-reasoning` | Memory Model Reasoning | `analyze` | `none` |
| `02.06` | `S0026` | `concurrency-model-reasoning` | Concurrency Model Reasoning | `analyze` | `none` |
| `02.07` | `S0027` | `standard-library-navigation` | Standard Library Navigation | `analyze` | `none` |
| `02.08` | `S0028` | `compiler-behavior-interpretation` | Compiler Behavior Interpretation | `analyze` | `none` |
| `02.09` | `S0029` | `runtime-behavior-interpretation` | Runtime Behavior Interpretation | `analyze` | `none` |
| `02.10` | `S0030` | `language-migration-planning` | Language Migration Planning | `transform` | `none` |
| `02.11` | `S0031` | `cross-language-translation` | Cross-Language Translation | `transform` | `none` |
| `02.12` | `S0032` | `polyglot-architecture` | Polyglot Architecture | `analyze` | `none` |
| `02.13` | `S0033` | `language-interoperability` | Language Interoperability | `analyze` | `none` |
| `02.14` | `S0034` | `foreign-function-interface-design` | Foreign Function Interface Design | `build` | `none` |
| `02.15` | `S0035` | `idiomatic-code-generation` | Idiomatic Code Generation | `analyze` | `none` |
| `02.16` | `S0036` | `language-style-guide-enforcement` | Language Style Guide Enforcement | `verify` | `none` |
| `02.17` | `S0037` | `deprecated-feature-detection` | Deprecated Feature Detection | `verify` | `none` |
| `02.18` | `S0038` | `version-specific-feature-selection` | Version-Specific Feature Selection | `analyze` | `none` |
| `02.19` | `S0039` | `language-compatibility-matrix` | Language Compatibility Matrix | `analyze` | `none` |
| `02.20` | `S0040` | `language-documentation-retrieval` | Language Documentation Retrieval | `analyze` | `none` |

### Category 03: Python Engineering (`python-engineering`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Audits Python 3 AST syntax, function/parameter type-annotation coverage, tracemalloc memory & parse timing, dataclasses, async coroutines, CLI/API scaffolds, and pyproject.toml packaging.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill python-type-hinting --input software-engineering/tools-core-runtime.py
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `03.01` | `S0041` | `python-application-builder` | Python Application Builder | `build` | `none` |
| `03.02` | `S0042` | `python-package-builder` | Python Package Builder | `build` | `none` |
| `03.03` | `S0043` | `python-type-hinting` | Python Type Hinting | `analyze` | `none` |
| `03.04` | `S0044` | `python-asyncio-engineering` | Python AsyncIO Engineering | `analyze` | `none` |
| `03.05` | `S0045` | `python-multiprocessing` | Python Multiprocessing | `analyze` | `none` |
| `03.06` | `S0046` | `python-data-classes` | Python Data Classes | `analyze` | `none` |
| `03.07` | `S0047` | `python-descriptor-engineering` | Python Descriptor Engineering | `analyze` | `none` |
| `03.08` | `S0048` | `python-metaprogramming` | Python Metaprogramming | `analyze` | `none` |
| `03.09` | `S0049` | `python-cli-builder` | Python CLI Builder | `build` | `none` |
| `03.10` | `S0050` | `python-web-api-builder` | Python Web API Builder | `build` | `none` |
| `03.11` | `S0051` | `python-testing` | Python Testing | `verify` | `none` |
| `03.12` | `S0052` | `python-packaging-with-pyproject` | Python Packaging with Pyproject | `analyze` | `none` |
| `03.13` | `S0053` | `python-virtual-environment-management` | Python Virtual Environment Management | `analyze` | `none` |
| `03.14` | `S0054` | `python-performance-profiling` | Python Performance Profiling | `optimize` | `none` |
| `03.15` | `S0055` | `python-memory-profiling` | Python Memory Profiling | `optimize` | `none` |
| `03.16` | `S0056` | `python-c-extension-integration` | Python C Extension Integration | `analyze` | `none` |
| `03.17` | `S0057` | `python-scientific-computing` | Python Scientific Computing | `analyze` | `none` |
| `03.18` | `S0058` | `python-data-pipeline-builder` | Python Data Pipeline Builder | `build` | `none` |
| `03.19` | `S0059` | `python-version-migration` | Python Version Migration | `transform` | `none` |
| `03.20` | `S0060` | `python-security-hardening` | Python Security Hardening | `analyze` | `none` |

### Category 04: JavaScript and TypeScript (`javascript-and-typescript`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Invokes software-engineering/tools-node-analyzer.mjs on Node.js V8 when available to check ESM/CJS syntax, imports/exports, async loop hazards, blocking Sync calls, and package manifests.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill nodejs-service-builder --input software-engineering/tools-node-analyzer.mjs
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `04.01` | `S0061` | `javascript-application-builder` | JavaScript Application Builder | `build` | `none` |
| `04.02` | `S0062` | `typescript-application-builder` | TypeScript Application Builder | `build` | `none` |
| `04.03` | `S0063` | `ecmascript-version-targeting` | ECMAScript Version Targeting | `analyze` | `none` |
| `04.04` | `S0064` | `typescript-type-architecture` | TypeScript Type Architecture | `analyze` | `none` |
| `04.05` | `S0065` | `node-js-service-builder` | Node.js Service Builder | `build` | `none` |
| `04.06` | `S0066` | `browser-javascript-engineering` | Browser JavaScript Engineering | `analyze` | `none` |
| `04.07` | `S0067` | `javascript-module-systems` | JavaScript Module Systems | `analyze` | `none` |
| `04.08` | `S0068` | `javascript-async-engineering` | JavaScript Async Engineering | `analyze` | `none` |
| `04.09` | `S0069` | `javascript-event-loop-analysis` | JavaScript Event Loop Analysis | `analyze` | `none` |
| `04.10` | `S0070` | `typescript-monorepo-builder` | TypeScript Monorepo Builder | `build` | `none` |
| `04.11` | `S0071` | `npm-package-builder` | NPM Package Builder | `build` | `none` |
| `04.12` | `S0072` | `javascript-bundler-configuration` | JavaScript Bundler Configuration | `analyze` | `none` |
| `04.13` | `S0073` | `javascript-testing` | JavaScript Testing | `verify` | `none` |
| `04.14` | `S0074` | `javascript-performance-profiling` | JavaScript Performance Profiling | `optimize` | `none` |
| `04.15` | `S0075` | `javascript-memory-leak-debugging` | JavaScript Memory Leak Debugging | `analyze` | `none` |
| `04.16` | `S0076` | `javascript-security-hardening` | JavaScript Security Hardening | `analyze` | `none` |
| `04.17` | `S0077` | `web-worker-engineering` | Web Worker Engineering | `analyze` | `none` |
| `04.18` | `S0078` | `service-worker-engineering` | Service Worker Engineering | `analyze` | `none` |
| `04.19` | `S0079` | `typescript-declaration-authoring` | TypeScript Declaration Authoring | `build` | `none` |
| `04.20` | `S0080` | `javascript-runtime-migration` | JavaScript Runtime Migration | `transform` | `none` |

### Category 05: Java and JVM (`java-and-jvm`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Validates Java and JVM service contracts, calculates G1GC/heap flags, and executes real OpenJDK javac bytecode compilation checks when javac is installed.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill java-application-builder --param heap_mb=2048
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `05.01` | `S0081` | `java-application-builder` | Java Application Builder | `build` | `none` |
| `05.02` | `S0082` | `java-language-version-migration` | Java Language Version Migration | `transform` | `none` |
| `05.03` | `S0083` | `jvm-runtime-tuning` | JVM Runtime Tuning | `optimize` | `none` |
| `05.04` | `S0084` | `spring-boot-service-builder` | Spring Boot Service Builder | `build` | `none` |
| `05.05` | `S0085` | `jakarta-ee-application-builder` | Jakarta EE Application Builder | `build` | `none` |
| `05.06` | `S0086` | `java-persistence-engineering` | Java Persistence Engineering | `analyze` | `none` |
| `05.07` | `S0087` | `java-concurrency-engineering` | Java Concurrency Engineering | `analyze` | `none` |
| `05.08` | `S0088` | `java-reactive-programming` | Java Reactive Programming | `analyze` | `none` |
| `05.09` | `S0089` | `java-module-system` | Java Module System | `analyze` | `none` |
| `05.10` | `S0090` | `gradle-build-engineering` | Gradle Build Engineering | `analyze` | `none` |
| `05.11` | `S0091` | `maven-build-engineering` | Maven Build Engineering | `analyze` | `none` |
| `05.12` | `S0092` | `java-testing` | Java Testing | `verify` | `none` |
| `05.13` | `S0093` | `java-profiling` | Java Profiling | `optimize` | `none` |
| `05.14` | `S0094` | `java-memory-leak-analysis` | Java Memory Leak Analysis | `analyze` | `none` |
| `05.15` | `S0095` | `java-native-interface` | Java Native Interface | `analyze` | `none` |
| `05.16` | `S0096` | `kotlin-jvm-engineering` | Kotlin JVM Engineering | `analyze` | `none` |
| `05.17` | `S0097` | `scala-jvm-engineering` | Scala JVM Engineering | `analyze` | `none` |
| `05.18` | `S0098` | `groovy-engineering` | Groovy Engineering | `analyze` | `none` |
| `05.19` | `S0099` | `jvm-bytecode-analysis` | JVM Bytecode Analysis | `analyze` | `none` |
| `05.20` | `S0100` | `java-security-hardening` | Java Security Hardening | `analyze` | `none` |

### Category 06: C and C++ (`c-and-cpp`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Checks C17 and Modern C++20 source units with gcc/g++ -fsyntax-only -Wall -Wextra when installed and screens for unsafe memory/UB primitives (gets, strcpy, raw malloc).
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill modern-cpp-application-builder
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `06.01` | `S0101` | `c-application-builder` | C Application Builder | `build` | `none` |
| `06.02` | `S0102` | `modern-cpp-application-builder` | Modern C++ Application Builder | `build` | `none` |
| `06.03` | `S0103` | `c-standard-version-targeting` | C Standard Version Targeting | `analyze` | `none` |
| `06.04` | `S0104` | `cpp-standard-version-targeting` | C++ Standard Version Targeting | `analyze` | `none` |
| `06.05` | `S0105` | `pointer-safety-analysis` | Pointer Safety Analysis | `analyze` | `none` |
| `06.06` | `S0106` | `manual-memory-management` | Manual Memory Management | `analyze` | `none` |
| `06.07` | `S0107` | `raii-design` | RAII Design | `build` | `none` |
| `06.08` | `S0108` | `template-metaprogramming` | Template Metaprogramming | `analyze` | `none` |
| `06.09` | `S0109` | `cpp-concepts-engineering` | C++ Concepts Engineering | `analyze` | `none` |
| `06.10` | `S0110` | `cmake-build-engineering` | CMake Build Engineering | `analyze` | `none` |
| `06.11` | `S0111` | `meson-build-engineering` | Meson Build Engineering | `analyze` | `none` |
| `06.12` | `S0112` | `makefile-engineering` | Makefile Engineering | `analyze` | `none` |
| `06.13` | `S0113` | `native-library-builder` | Native Library Builder | `build` | `none` |
| `06.14` | `S0114` | `abi-compatibility-analysis` | ABI Compatibility Analysis | `analyze` | `none` |
| `06.15` | `S0115` | `undefined-behavior-detection` | Undefined Behavior Detection | `verify` | `none` |
| `06.16` | `S0116` | `c-and-cpp-testing` | C and C++ Testing | `verify` | `none` |
| `06.17` | `S0117` | `native-performance-profiling` | Native Performance Profiling | `optimize` | `none` |
| `06.18` | `S0118` | `native-memory-debugging` | Native Memory Debugging | `analyze` | `none` |
| `06.19` | `S0119` | `cross-compilation` | Cross-Compilation | `analyze` | `none` |
| `06.20` | `S0120` | `c-and-cpp-security-hardening` | C and C++ Security Hardening | `analyze` | `none` |

### Category 07: C# and .NET (`csharp-and-dotnet`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Produces SDK-style C# and .NET 8+ architectural contracts, nullable/DI/EF Core checks, and discloses when the dotnet CLI is absent from the host.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill asp-dotnet-core-api-builder
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `07.01` | `S0121` | `csharp-application-builder` | C# Application Builder | `build` | `none` |
| `07.02` | `S0122` | `dotnet-service-builder` | .NET Service Builder | `build` | `none` |
| `07.03` | `S0123` | `aspdotnet-core-api-builder` | ASP.NET Core API Builder | `build` | `none` |
| `07.04` | `S0124` | `entity-framework-core-engineering` | Entity Framework Core Engineering | `analyze` | `none` |
| `07.05` | `S0125` | `csharp-async-engineering` | C# Async Engineering | `analyze` | `none` |
| `07.06` | `S0126` | `csharp-linq-engineering` | C# LINQ Engineering | `analyze` | `none` |
| `07.07` | `S0127` | `csharp-source-generator` | C# Source Generator | `build` | `none` |
| `07.08` | `S0128` | `dotnet-dependency-injection` | .NET Dependency Injection | `analyze` | `none` |
| `07.09` | `S0129` | `dotnet-configuration-engineering` | .NET Configuration Engineering | `analyze` | `none` |
| `07.10` | `S0130` | `dotnet-worker-service` | .NET Worker Service | `analyze` | `none` |
| `07.11` | `S0131` | `blazor-application-builder` | Blazor Application Builder | `build` | `none` |
| `07.12` | `S0132` | `wpf-application-builder` | WPF Application Builder | `build` | `none` |
| `07.13` | `S0133` | `winui-application-builder` | WinUI Application Builder | `build` | `none` |
| `07.14` | `S0134` | `dotnet-testing` | .NET Testing | `verify` | `none` |
| `07.15` | `S0135` | `dotnet-profiling` | .NET Profiling | `optimize` | `none` |
| `07.16` | `S0136` | `dotnet-memory-analysis` | .NET Memory Analysis | `analyze` | `none` |
| `07.17` | `S0137` | `nuget-package-builder` | NuGet Package Builder | `build` | `none` |
| `07.18` | `S0138` | `dotnet-version-migration` | .NET Version Migration | `transform` | `none` |
| `07.19` | `S0139` | `native-interop-for-dotnet` | Native Interop for .NET | `analyze` | `none` |
| `07.20` | `S0140` | `dotnet-security-hardening` | .NET Security Hardening | `analyze` | `none` |

### Category 08: Go and Rust (`go-and-rust`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Audits Go concurrency/module patterns and Rust ownership, lifetimes, and unsafe block // SAFETY: invariants, disclosing host go/rustc binary availability.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill rust-unsafe-code-audit --text 'unsafe { core::ptr::read(p) }'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `08.01` | `S0141` | `go-application-builder` | Go Application Builder | `build` | `none` |
| `08.02` | `S0142` | `go-service-builder` | Go Service Builder | `build` | `none` |
| `08.03` | `S0143` | `go-concurrency-engineering` | Go Concurrency Engineering | `analyze` | `none` |
| `08.04` | `S0144` | `go-module-management` | Go Module Management | `analyze` | `none` |
| `08.05` | `S0145` | `go-cli-builder` | Go CLI Builder | `build` | `none` |
| `08.06` | `S0146` | `go-testing` | Go Testing | `verify` | `none` |
| `08.07` | `S0147` | `go-profiling` | Go Profiling | `optimize` | `none` |
| `08.08` | `S0148` | `go-memory-optimization` | Go Memory Optimization | `optimize` | `none` |
| `08.09` | `S0149` | `go-cross-compilation` | Go Cross-Compilation | `analyze` | `none` |
| `08.10` | `S0150` | `go-security-hardening` | Go Security Hardening | `analyze` | `none` |
| `08.11` | `S0151` | `rust-application-builder` | Rust Application Builder | `build` | `none` |
| `08.12` | `S0152` | `rust-ownership-modeling` | Rust Ownership Modeling | `analyze` | `none` |
| `08.13` | `S0153` | `rust-lifetime-engineering` | Rust Lifetime Engineering | `analyze` | `none` |
| `08.14` | `S0154` | `rust-async-engineering` | Rust Async Engineering | `analyze` | `none` |
| `08.15` | `S0155` | `rust-trait-architecture` | Rust Trait Architecture | `analyze` | `none` |
| `08.16` | `S0156` | `rust-cargo-workspace` | Rust Cargo Workspace | `analyze` | `none` |
| `08.17` | `S0157` | `rust-ffi-engineering` | Rust FFI Engineering | `analyze` | `none` |
| `08.18` | `S0158` | `rust-testing` | Rust Testing | `verify` | `none` |
| `08.19` | `S0159` | `rust-unsafe-code-audit` | Rust Unsafe Code Audit | `verify` | `none` |
| `08.20` | `S0160` | `rust-security-hardening` | Rust Security Hardening | `analyze` | `none` |

### Category 09: Mobile Languages and Platforms (`mobile-languages-and-platforms`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Models Android (Kotlin/Compose), iOS (Swift/SwiftUI), Flutter, and React Native lifecycle budgets (60/120fps frame time), offline outboxes, deep links, and touch accessibility.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill mobile-platform-performance-profiling --param fps=120
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `09.01` | `S0161` | `kotlin-android-builder` | Kotlin Android Builder | `build` | `none` |
| `09.02` | `S0162` | `java-android-builder` | Java Android Builder | `build` | `none` |
| `09.03` | `S0163` | `swift-application-builder` | Swift Application Builder | `build` | `none` |
| `09.04` | `S0164` | `objective-c-maintenance` | Objective-C Maintenance | `analyze` | `none` |
| `09.05` | `S0165` | `dart-application-builder` | Dart Application Builder | `build` | `none` |
| `09.06` | `S0166` | `flutter-application-builder` | Flutter Application Builder | `build` | `none` |
| `09.07` | `S0167` | `react-native-builder` | React Native Builder | `build` | `none` |
| `09.08` | `S0168` | `android-jetpack-compose` | Android Jetpack Compose | `analyze` | `none` |
| `09.09` | `S0169` | `apple-swiftui-builder` | Apple SwiftUI Builder | `build` | `none` |
| `09.10` | `S0170` | `android-architecture-components` | Android Architecture Components | `analyze` | `none` |
| `09.11` | `S0171` | `ios-uikit-builder` | iOS UIKit Builder | `build` | `none` |
| `09.12` | `S0172` | `mobile-state-management` | Mobile State Management | `analyze` | `none` |
| `09.13` | `S0173` | `mobile-offline-first-design` | Mobile Offline-First Design | `build` | `none` |
| `09.14` | `S0174` | `mobile-background-processing` | Mobile Background Processing | `analyze` | `none` |
| `09.15` | `S0175` | `mobile-deep-linking` | Mobile Deep Linking | `analyze` | `none` |
| `09.16` | `S0176` | `mobile-push-notification-integration` | Mobile Push Notification Integration | `analyze` | `none` |
| `09.17` | `S0177` | `mobile-accessibility` | Mobile Accessibility | `analyze` | `none` |
| `09.18` | `S0178` | `mobile-platform-performance-profiling` | Mobile Performance Profiling | `optimize` | `none` |
| `09.19` | `S0179` | `mobile-store-release-preparation` | Mobile Store Release Preparation | `analyze` | `publishing` |
| `09.20` | `S0180` | `mobile-platform-migration` | Mobile Platform Migration | `transform` | `none` |

### Category 10: Additional Languages (`additional-languages`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Supports 20 additional ecosystems (PHP, Ruby/Rails, Perl, Lua, R, MATLAB, Julia, Haskell, Elixir, Erlang, Clojure, F#, OCaml, Fortran, COBOL, VB, Pascal, Assembly, WebAssembly WAT).
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill webassembly-engineering --text '(module (func (param i32) (result i32) local.get 0))'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `10.01` | `S0181` | `php-application-builder` | PHP Application Builder | `build` | `none` |
| `10.02` | `S0182` | `ruby-application-builder` | Ruby Application Builder | `build` | `none` |
| `10.03` | `S0183` | `ruby-on-rails-builder` | Ruby on Rails Builder | `build` | `none` |
| `10.04` | `S0184` | `perl-engineering` | Perl Engineering | `analyze` | `none` |
| `10.05` | `S0185` | `lua-engineering` | Lua Engineering | `analyze` | `none` |
| `10.06` | `S0186` | `r-language-engineering` | R Language Engineering | `analyze` | `none` |
| `10.07` | `S0187` | `matlab-engineering` | MATLAB Engineering | `analyze` | `none` |
| `10.08` | `S0188` | `julia-engineering` | Julia Engineering | `analyze` | `none` |
| `10.09` | `S0189` | `haskell-engineering` | Haskell Engineering | `analyze` | `none` |
| `10.10` | `S0190` | `elixir-engineering` | Elixir Engineering | `analyze` | `none` |
| `10.11` | `S0191` | `erlang-engineering` | Erlang Engineering | `analyze` | `none` |
| `10.12` | `S0192` | `clojure-engineering` | Clojure Engineering | `analyze` | `none` |
| `10.13` | `S0193` | `fsharp-engineering` | F# Engineering | `analyze` | `none` |
| `10.14` | `S0194` | `ocaml-engineering` | OCaml Engineering | `analyze` | `none` |
| `10.15` | `S0195` | `fortran-modernization` | Fortran Modernization | `transform` | `none` |
| `10.16` | `S0196` | `cobol-modernization` | COBOL Modernization | `transform` | `none` |
| `10.17` | `S0197` | `visual-basic-modernization` | Visual Basic Modernization | `transform` | `none` |
| `10.18` | `S0198` | `delphi-and-pascal-engineering` | Delphi and Pascal Engineering | `analyze` | `none` |
| `10.19` | `S0199` | `assembly-language-engineering` | Assembly Language Engineering | `analyze` | `none` |
| `10.20` | `S0200` | `webassembly-engineering` | WebAssembly Engineering | `analyze` | `none` |

### Category 11: Shell and Systems Scripting (`shell-and-systems-scripting`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Validates Bash/POSIX scripts with bash -n, enforces strict set -euo pipefail error handling, checks AWK/Sed pipelines, and screens regular expressions for nested-quantifier ReDoS risks.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill regular-expression-engineering --param 'pattern=^[a-z0-9]+(?:-[a-z0-9]+)*$'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `11.01` | `S0201` | `bash-script-builder` | Bash Script Builder | `build` | `none` |
| `11.02` | `S0202` | `posix-shell-engineering` | POSIX Shell Engineering | `analyze` | `none` |
| `11.03` | `S0203` | `powershell-script-builder` | PowerShell Script Builder | `build` | `none` |
| `11.04` | `S0204` | `windows-batch-engineering` | Windows Batch Engineering | `analyze` | `none` |
| `11.05` | `S0205` | `zsh-automation` | Zsh Automation | `orchestrate` | `none` |
| `11.06` | `S0206` | `fish-shell-automation` | Fish Shell Automation | `orchestrate` | `none` |
| `11.07` | `S0207` | `command-line-pipeline-design` | Command-Line Pipeline Design | `build` | `none` |
| `11.08` | `S0208` | `text-processing-with-awk` | Text Processing with AWK | `analyze` | `none` |
| `11.09` | `S0209` | `text-processing-with-sed` | Text Processing with Sed | `analyze` | `none` |
| `11.10` | `S0210` | `regular-expression-engineering` | Regular Expression Engineering | `analyze` | `none` |
| `11.11` | `S0211` | `process-control-automation` | Process Control Automation | `orchestrate` | `none` |
| `11.12` | `S0212` | `file-system-automation` | File System Automation | `orchestrate` | `none` |
| `11.13` | `S0213` | `environment-variable-management` | Environment Variable Management | `analyze` | `none` |
| `11.14` | `S0214` | `shell-error-handling` | Shell Error Handling | `analyze` | `none` |
| `11.15` | `S0215` | `shell-portability-testing` | Shell Portability Testing | `verify` | `none` |
| `11.16` | `S0216` | `privilege-boundary-handling` | Privilege Boundary Handling | `analyze` | `privileged` |
| `11.17` | `S0217` | `cron-and-task-scheduler-automation` | Cron and Task Scheduler Automation | `orchestrate` | `none` |
| `11.18` | `S0218` | `remote-command-automation` | Remote Command Automation | `orchestrate` | `destructive` |
| `11.19` | `S0219` | `idempotent-system-scripts` | Idempotent System Scripts | `analyze` | `none` |
| `11.20` | `S0220` | `shell-security-hardening` | Shell Security Hardening | `analyze` | `none` |

### Category 12: Algorithms and Data Structures (`algorithms-and-data-structures`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Executes deterministic sorting, binary search, Dijkstra shortest paths, KMP string matching, dynamic programming edit distance, 2D monotone-chain convex hull, and invariant checks.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill graph-algorithm-engineering --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `12.01` | `S0221` | `algorithm-selection` | Algorithm Selection | `analyze` | `none` |
| `12.02` | `S0222` | `complexity-analysis` | Complexity Analysis | `analyze` | `none` |
| `12.03` | `S0223` | `array-and-string-algorithms` | Array and String Algorithms | `analyze` | `none` |
| `12.04` | `S0224` | `linked-structure-algorithms` | Linked Structure Algorithms | `analyze` | `none` |
| `12.05` | `S0225` | `stack-and-queue-design` | Stack and Queue Design | `build` | `none` |
| `12.06` | `S0226` | `hash-table-engineering` | Hash Table Engineering | `analyze` | `none` |
| `12.07` | `S0227` | `tree-algorithm-engineering` | Tree Algorithm Engineering | `analyze` | `none` |
| `12.08` | `S0228` | `graph-algorithm-engineering` | Graph Algorithm Engineering | `analyze` | `none` |
| `12.09` | `S0229` | `heap-and-priority-queue-design` | Heap and Priority Queue Design | `build` | `none` |
| `12.10` | `S0230` | `sorting-algorithm-selection` | Sorting Algorithm Selection | `analyze` | `none` |
| `12.11` | `S0231` | `searching-algorithm-selection` | Searching Algorithm Selection | `analyze` | `none` |
| `12.12` | `S0232` | `dynamic-programming` | Dynamic Programming | `analyze` | `none` |
| `12.13` | `S0233` | `greedy-algorithm-design` | Greedy Algorithm Design | `build` | `none` |
| `12.14` | `S0234` | `backtracking-and-constraint-search` | Backtracking and Constraint Search | `analyze` | `none` |
| `12.15` | `S0235` | `computational-geometry` | Computational Geometry | `analyze` | `none` |
| `12.16` | `S0236` | `string-matching-algorithms` | String Matching Algorithms | `analyze` | `none` |
| `12.17` | `S0237` | `probabilistic-data-structures` | Probabilistic Data Structures | `analyze` | `none` |
| `12.18` | `S0238` | `streaming-algorithms` | Streaming Algorithms | `analyze` | `none` |
| `12.19` | `S0239` | `parallel-algorithms` | Parallel Algorithms | `analyze` | `none` |
| `12.20` | `S0240` | `algorithm-correctness-proof` | Algorithm Correctness Proof | `verify` | `none` |

### Category 13: Software Architecture (`software-architecture`)

- **Engine**: `software-engineering/tools-engine-architecture.py`
- **How It Works**: Models C4 system/container boundaries and automated architectural fitness functions across Modular Monolith, Microservices, Event-Driven, Hexagonal, Clean, DDD, CQRS, and Event Sourcing patterns.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill software-architecture-documentation --project ledger-core
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `13.01` | `S0241` | `modular-monolith-architecture` | Modular Monolith Architecture | `analyze` | `none` |
| `13.02` | `S0242` | `microservices-architecture` | Microservices Architecture | `analyze` | `none` |
| `13.03` | `S0243` | `event-driven-architecture` | Event-Driven Architecture | `analyze` | `none` |
| `13.04` | `S0244` | `hexagonal-architecture` | Hexagonal Architecture | `analyze` | `none` |
| `13.05` | `S0245` | `clean-architecture` | Clean Architecture | `analyze` | `none` |
| `13.06` | `S0246` | `layered-architecture` | Layered Architecture | `analyze` | `none` |
| `13.07` | `S0247` | `domain-driven-design` | Domain-Driven Design | `build` | `none` |
| `13.08` | `S0248` | `cqrs-architecture` | CQRS Architecture | `analyze` | `none` |
| `13.09` | `S0249` | `event-sourcing-architecture` | Event Sourcing Architecture | `analyze` | `none` |
| `13.10` | `S0250` | `serverless-architecture` | Serverless Architecture | `analyze` | `none` |
| `13.11` | `S0251` | `plugin-architecture` | Plugin Architecture | `analyze` | `none` |
| `13.12` | `S0252` | `pipeline-architecture` | Pipeline Architecture | `orchestrate` | `none` |
| `13.13` | `S0253` | `peer-to-peer-architecture` | Peer-to-Peer Architecture | `analyze` | `none` |
| `13.14` | `S0254` | `multi-tenant-architecture` | Multi-Tenant Architecture | `analyze` | `none` |
| `13.15` | `S0255` | `high-availability-architecture` | High-Availability Architecture | `analyze` | `none` |
| `13.16` | `S0256` | `fault-tolerant-architecture` | Fault-Tolerant Architecture | `analyze` | `none` |
| `13.17` | `S0257` | `scalable-system-architecture` | Scalable System Architecture | `analyze` | `none` |
| `13.18` | `S0258` | `architecture-fitness-functions` | Architecture Fitness Functions | `analyze` | `none` |
| `13.19` | `S0259` | `software-architecture-documentation` | Architecture Documentation | `analyze` | `none` |
| `13.20` | `S0260` | `architecture-review` | Architecture Review | `verify` | `none` |

### Category 14: API Engineering (`api-engineering`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Synthesizes and checks OpenAPI 3.1 contracts, cursor pagination, Idempotency-Key headers, RFC 9457 Problem Details error schemas, and token-bucket rate-limit burst simulations.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill openapi-specification --param rate_capacity=100 --param burst_requests=80
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `14.01` | `S0261` | `rest-api-design` | REST API Design | `build` | `none` |
| `14.02` | `S0262` | `graphql-api-design` | GraphQL API Design | `build` | `none` |
| `14.03` | `S0263` | `grpc-service-design` | gRPC Service Design | `build` | `none` |
| `14.04` | `S0264` | `websocket-protocol-design` | WebSocket Protocol Design | `build` | `none` |
| `14.05` | `S0265` | `webhook-design` | Webhook Design | `build` | `none` |
| `14.06` | `S0266` | `asyncapi-specification` | AsyncAPI Specification | `analyze` | `none` |
| `14.07` | `S0267` | `openapi-specification` | OpenAPI Specification | `analyze` | `none` |
| `14.08` | `S0268` | `api-schema-design` | API Schema Design | `build` | `none` |
| `14.09` | `S0269` | `api-versioning` | API Versioning | `analyze` | `none` |
| `14.10` | `S0270` | `api-pagination` | API Pagination | `analyze` | `none` |
| `14.11` | `S0271` | `api-filtering-and-sorting` | API Filtering and Sorting | `analyze` | `none` |
| `14.12` | `S0272` | `api-idempotency` | API Idempotency | `analyze` | `none` |
| `14.13` | `S0273` | `api-authentication` | API Authentication | `analyze` | `none` |
| `14.14` | `S0274` | `api-authorization` | API Authorization | `analyze` | `none` |
| `14.15` | `S0275` | `api-rate-limiting` | API Rate Limiting | `analyze` | `none` |
| `14.16` | `S0276` | `api-error-modeling` | API Error Modeling | `analyze` | `none` |
| `14.17` | `S0277` | `api-contract-testing` | API Contract Testing | `verify` | `none` |
| `14.18` | `S0278` | `api-mock-server` | API Mock Server | `analyze` | `none` |
| `14.19` | `S0279` | `api-gateway-configuration` | API Gateway Configuration | `verify` | `none` |
| `14.20` | `S0280` | `api-developer-documentation` | API Developer Documentation | `analyze` | `none` |

### Category 15: Web Frontend Engineering (`web-frontend-engineering`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Audits HTML5 semantic landmarks, button/image accessibility attributes, DOM XSS sinks, and exact WCAG 2.1 relative-luminance contrast ratios.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill frontend-accessibility --param 'fg=#0f172a' --param 'bg=#ffffff'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `15.01` | `S0281` | `html-semantic-architecture` | HTML Semantic Architecture | `analyze` | `none` |
| `15.02` | `S0282` | `css-layout-engineering` | CSS Layout Engineering | `analyze` | `none` |
| `15.03` | `S0283` | `responsive-web-design` | Responsive Web Design | `build` | `none` |
| `15.04` | `S0284` | `javascript-frontend-architecture` | JavaScript Frontend Architecture | `analyze` | `none` |
| `15.05` | `S0285` | `react-application-builder` | React Application Builder | `build` | `none` |
| `15.06` | `S0286` | `next-js-application-builder` | Next.js Application Builder | `build` | `none` |
| `15.07` | `S0287` | `vue-application-builder` | Vue Application Builder | `build` | `none` |
| `15.08` | `S0288` | `nuxt-application-builder` | Nuxt Application Builder | `build` | `none` |
| `15.09` | `S0289` | `angular-application-builder` | Angular Application Builder | `build` | `none` |
| `15.10` | `S0290` | `svelte-application-builder` | Svelte Application Builder | `build` | `none` |
| `15.11` | `S0291` | `sveltekit-application-builder` | SvelteKit Application Builder | `build` | `none` |
| `15.12` | `S0292` | `web-component-builder` | Web Component Builder | `build` | `none` |
| `15.13` | `S0293` | `frontend-state-management` | Frontend State Management | `analyze` | `none` |
| `15.14` | `S0294` | `form-engineering` | Form Engineering | `analyze` | `none` |
| `15.15` | `S0295` | `client-side-routing` | Client-Side Routing | `analyze` | `none` |
| `15.16` | `S0296` | `frontend-data-fetching` | Frontend Data Fetching | `analyze` | `none` |
| `15.17` | `S0297` | `frontend-error-boundaries` | Frontend Error Boundaries | `analyze` | `none` |
| `15.18` | `S0298` | `frontend-performance-optimization` | Frontend Performance Optimization | `optimize` | `none` |
| `15.19` | `S0299` | `frontend-accessibility` | Frontend Accessibility | `analyze` | `none` |
| `15.20` | `S0300` | `frontend-security-hardening` | Frontend Security Hardening | `analyze` | `none` |

### Category 16: Web Backend Engineering (`web-backend-engineering`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Evaluates default-deny RBAC/ABAC policies, deterministic percentage feature-flag rollouts, retryable worker queues, and tamper-evident SHA-256 hash-chained audit logs.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill audit-log-service --param rollout_pct=25 --param subject=tenant-42
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `16.01` | `S0301` | `backend-service-builder` | Backend Service Builder | `build` | `none` |
| `16.02` | `S0302` | `authentication-service-builder` | Authentication Service Builder | `build` | `none` |
| `16.03` | `S0303` | `authorization-service-builder` | Authorization Service Builder | `build` | `none` |
| `16.04` | `S0304` | `session-management` | Session Management | `analyze` | `none` |
| `16.05` | `S0305` | `job-queue-builder` | Job Queue Builder | `build` | `none` |
| `16.06` | `S0306` | `background-worker-builder` | Background Worker Builder | `build` | `none` |
| `16.07` | `S0307` | `file-upload-service` | File Upload Service | `analyze` | `none` |
| `16.08` | `S0308` | `email-delivery-service` | Email Delivery Service | `analyze` | `none` |
| `16.09` | `S0309` | `notification-service` | Notification Service | `analyze` | `none` |
| `16.10` | `S0310` | `search-service-integration` | Search Service Integration | `analyze` | `none` |
| `16.11` | `S0311` | `caching-layer-builder` | Caching Layer Builder | `build` | `none` |
| `16.12` | `S0312` | `feature-flag-service` | Feature Flag Service | `analyze` | `none` |
| `16.13` | `S0313` | `audit-log-service` | Audit Log Service | `verify` | `none` |
| `16.14` | `S0314` | `configuration-service` | Configuration Service | `analyze` | `none` |
| `16.15` | `S0315` | `multi-tenant-backend` | Multi-Tenant Backend | `analyze` | `none` |
| `16.16` | `S0316` | `backend-observability` | Backend Observability | `analyze` | `none` |
| `16.17` | `S0317` | `backend-performance` | Backend Performance | `optimize` | `none` |
| `16.18` | `S0318` | `backend-security` | Backend Security | `analyze` | `none` |
| `16.19` | `S0319` | `backend-resilience` | Backend Resilience | `analyze` | `none` |
| `16.20` | `S0320` | `backend-migration` | Backend Migration | `transform` | `none` |

### Category 17: Full-Stack Product Building (`full-stack-product-building`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Aligns persistence schemas, API endpoints, UI views, locale catalogs, and PWA/offline launch gates across 20 full-stack product archetypes.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill saas-product-builder --project acme-billing
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `17.01` | `S0321` | `full-stack-application-builder` | Full-Stack Application Builder | `build` | `none` |
| `17.02` | `S0322` | `saas-product-builder` | SaaS Product Builder | `build` | `none` |
| `17.03` | `S0323` | `admin-portal-builder` | Admin Portal Builder | `build` | `none` |
| `17.04` | `S0324` | `customer-portal-builder` | Customer Portal Builder | `build` | `none` |
| `17.05` | `S0325` | `marketplace-builder` | Marketplace Builder | `build` | `none` |
| `17.06` | `S0326` | `e-commerce-builder` | E-Commerce Builder | `build` | `none` |
| `17.07` | `S0327` | `content-management-system` | Content Management System | `analyze` | `none` |
| `17.08` | `S0328` | `learning-management-system` | Learning Management System | `analyze` | `none` |
| `17.09` | `S0329` | `booking-system-builder` | Booking System Builder | `build` | `none` |
| `17.10` | `S0330` | `real-time-collaboration-app` | Real-Time Collaboration App | `analyze` | `none` |
| `17.11` | `S0331` | `chat-application-builder` | Chat Application Builder | `build` | `none` |
| `17.12` | `S0332` | `dashboard-application-builder` | Dashboard Application Builder | `build` | `none` |
| `17.13` | `S0333` | `form-platform-builder` | Form Platform Builder | `build` | `none` |
| `17.14` | `S0334` | `subscription-product-builder` | Subscription Product Builder | `build` | `financial` |
| `17.15` | `S0335` | `multi-role-workflow-app` | Multi-Role Workflow App | `orchestrate` | `none` |
| `17.16` | `S0336` | `progressive-web-app` | Progressive Web App | `analyze` | `none` |
| `17.17` | `S0337` | `offline-web-app` | Offline Web App | `analyze` | `none` |
| `17.18` | `S0338` | `internationalized-web-app` | Internationalized Web App | `analyze` | `none` |
| `17.19` | `S0339` | `accessible-product-builder` | Accessible Product Builder | `build` | `none` |
| `17.20` | `S0340` | `production-launch-readiness` | Production Launch Readiness | `analyze` | `none` |

### Category 18: Databases and SQL (`databases-and-sql`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Executes real DDL schemas, seed DML, SQL queries, foreign-key constraints, and EXPLAIN QUERY PLAN inspections via Python's built-in SQLite3 engine while screening for SQL interpolation.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill sql-query-optimization --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `18.01` | `S0341` | `relational-schema-design` | Relational Schema Design | `build` | `none` |
| `18.02` | `S0342` | `sql-query-engineering` | SQL Query Engineering | `analyze` | `none` |
| `18.03` | `S0343` | `sql-query-optimization` | SQL Query Optimization | `optimize` | `none` |
| `18.04` | `S0344` | `postgresql-engineering` | PostgreSQL Engineering | `analyze` | `none` |
| `18.05` | `S0345` | `mysql-engineering` | MySQL Engineering | `analyze` | `none` |
| `18.06` | `S0346` | `sql-server-engineering` | SQL Server Engineering | `analyze` | `none` |
| `18.07` | `S0347` | `oracle-database-engineering` | Oracle Database Engineering | `analyze` | `none` |
| `18.08` | `S0348` | `sqlite-engineering` | SQLite Engineering | `analyze` | `none` |
| `18.09` | `S0349` | `database-migration-engineering` | Database Migration Engineering | `transform` | `irreversible` |
| `18.10` | `S0350` | `index-design` | Index Design | `build` | `none` |
| `18.11` | `S0351` | `transaction-design` | Transaction Design | `build` | `none` |
| `18.12` | `S0352` | `isolation-level-analysis` | Isolation Level Analysis | `analyze` | `none` |
| `18.13` | `S0353` | `stored-procedure-engineering` | Stored Procedure Engineering | `analyze` | `none` |
| `18.14` | `S0354` | `trigger-engineering` | Trigger Engineering | `analyze` | `none` |
| `18.15` | `S0355` | `view-and-materialized-view-design` | View and Materialized View Design | `build` | `none` |
| `18.16` | `S0356` | `database-partitioning` | Database Partitioning | `analyze` | `none` |
| `18.17` | `S0357` | `database-replication` | Database Replication | `analyze` | `none` |
| `18.18` | `S0358` | `database-backup-and-recovery` | Database Backup and Recovery | `analyze` | `none` |
| `18.19` | `S0359` | `database-security` | Database Security | `analyze` | `none` |
| `18.20` | `S0360` | `database-performance-diagnosis` | Database Performance Diagnosis | `optimize` | `none` |

### Category 19: NoSQL and Data Stores (`nosql-and-data-stores`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Verifies NoSQL replication consistency quorums (R + W > N), ranked inverted-index document retrieval, partition key distribution, and key-value/graph/vector store parameters.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill nosql-consistency-modeling --param replicas=5 --param read_quorum=3 --param write_quorum=3
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `19.01` | `S0361` | `document-database-design` | Document Database Design | `build` | `none` |
| `19.02` | `S0362` | `mongodb-engineering` | MongoDB Engineering | `analyze` | `none` |
| `19.03` | `S0363` | `key-value-store-design` | Key-Value Store Design | `build` | `none` |
| `19.04` | `S0364` | `redis-engineering` | Redis Engineering | `analyze` | `none` |
| `19.05` | `S0365` | `wide-column-database-design` | Wide-Column Database Design | `build` | `none` |
| `19.06` | `S0366` | `cassandra-engineering` | Cassandra Engineering | `analyze` | `none` |
| `19.07` | `S0367` | `graph-database-design` | Graph Database Design | `build` | `none` |
| `19.08` | `S0368` | `neo4j-engineering` | Neo4j Engineering | `analyze` | `none` |
| `19.09` | `S0369` | `time-series-database-design` | Time-Series Database Design | `build` | `none` |
| `19.10` | `S0370` | `influxdb-engineering` | InfluxDB Engineering | `analyze` | `none` |
| `19.11` | `S0371` | `search-index-design` | Search Index Design | `build` | `none` |
| `19.12` | `S0372` | `elasticsearch-engineering` | Elasticsearch Engineering | `analyze` | `none` |
| `19.13` | `S0373` | `vector-database-design` | Vector Database Design | `build` | `none` |
| `19.14` | `S0374` | `object-storage-engineering` | Object Storage Engineering | `analyze` | `none` |
| `19.15` | `S0375` | `distributed-cache-design` | Distributed Cache Design | `build` | `none` |
| `19.16` | `S0376` | `nosql-consistency-modeling` | NoSQL Consistency Modeling | `analyze` | `none` |
| `19.17` | `S0377` | `nosql-query-optimization` | NoSQL Query Optimization | `optimize` | `none` |
| `19.18` | `S0378` | `data-store-migration` | Data Store Migration | `transform` | `irreversible` |
| `19.19` | `S0379` | `data-store-capacity-planning` | Data Store Capacity Planning | `analyze` | `none` |
| `19.20` | `S0380` | `data-store-security` | Data Store Security | `analyze` | `none` |

### Category 20: Data Engineering (`data-engineering`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Runs batch/streaming ETL/ELT aggregations, enforces primary-key uniqueness and domain-bound Data Quality expectations, and maps column-level lineage.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill data-quality-framework --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `20.01` | `S0381` | `batch-data-pipeline-builder` | Batch Data Pipeline Builder | `build` | `none` |
| `20.02` | `S0382` | `streaming-data-pipeline-builder` | Streaming Data Pipeline Builder | `build` | `none` |
| `20.03` | `S0383` | `etl-pipeline-builder` | ETL Pipeline Builder | `build` | `none` |
| `20.04` | `S0384` | `elt-pipeline-builder` | ELT Pipeline Builder | `build` | `none` |
| `20.05` | `S0385` | `data-lake-architecture` | Data Lake Architecture | `analyze` | `none` |
| `20.06` | `S0386` | `data-warehouse-architecture` | Data Warehouse Architecture | `analyze` | `none` |
| `20.07` | `S0387` | `lakehouse-architecture` | Lakehouse Architecture | `analyze` | `none` |
| `20.08` | `S0388` | `apache-spark-engineering` | Apache Spark Engineering | `analyze` | `none` |
| `20.09` | `S0389` | `apache-kafka-engineering` | Apache Kafka Engineering | `analyze` | `none` |
| `20.10` | `S0390` | `apache-flink-engineering` | Apache Flink Engineering | `analyze` | `none` |
| `20.11` | `S0391` | `apache-airflow-engineering` | Apache Airflow Engineering | `analyze` | `none` |
| `20.12` | `S0392` | `dbt-analytics-engineering` | dbt Analytics Engineering | `analyze` | `none` |
| `20.13` | `S0393` | `data-quality-framework` | Data Quality Framework | `analyze` | `none` |
| `20.14` | `S0394` | `data-lineage-engineering` | Data Lineage Engineering | `analyze` | `none` |
| `20.15` | `S0395` | `change-data-capture` | Change Data Capture | `analyze` | `none` |
| `20.16` | `S0396` | `schema-evolution` | Schema Evolution | `analyze` | `none` |
| `20.17` | `S0397` | `data-contract-engineering` | Data Contract Engineering | `analyze` | `none` |
| `20.18` | `S0398` | `data-orchestration` | Data Orchestration | `orchestrate` | `none` |
| `20.19` | `S0399` | `data-pipeline-observability` | Data Pipeline Observability | `orchestrate` | `none` |
| `20.20` | `S0400` | `data-platform-security` | Data Platform Security | `analyze` | `none` |

### Category 21: Machine Learning Engineering (`machine-learning-engineering`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Computes binary classification confusion matrices, Precision, Recall, F1, exact Mann-Whitney ROC-AUC, and Population Stability Index (PSI) model drift checks.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill model-evaluation --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `21.01` | `S0401` | `machine-learning-problem-framing` | Machine Learning Problem Framing | `analyze` | `none` |
| `21.02` | `S0402` | `feature-engineering` | Feature Engineering | `analyze` | `none` |
| `21.03` | `S0403` | `model-training-pipeline` | Model Training Pipeline | `orchestrate` | `none` |
| `21.04` | `S0404` | `model-evaluation` | Model Evaluation | `analyze` | `none` |
| `21.05` | `S0405` | `model-selection` | Model Selection | `analyze` | `none` |
| `21.06` | `S0406` | `hyperparameter-optimization` | Hyperparameter Optimization | `optimize` | `none` |
| `21.07` | `S0407` | `cross-validation-design` | Cross-Validation Design | `build` | `none` |
| `21.08` | `S0408` | `class-imbalance-handling` | Class Imbalance Handling | `analyze` | `none` |
| `21.09` | `S0409` | `model-explainability` | Model Explainability | `analyze` | `none` |
| `21.10` | `S0410` | `model-serving` | Model Serving | `analyze` | `none` |
| `21.11` | `S0411` | `model-monitoring` | Model Monitoring | `analyze` | `none` |
| `21.12` | `S0412` | `model-drift-detection` | Model Drift Detection | `verify` | `none` |
| `21.13` | `S0413` | `mlops-pipeline` | MLOps Pipeline | `orchestrate` | `none` |
| `21.14` | `S0414` | `experiment-tracking` | Experiment Tracking | `analyze` | `none` |
| `21.15` | `S0415` | `model-registry` | Model Registry | `analyze` | `none` |
| `21.16` | `S0416` | `batch-inference` | Batch Inference | `analyze` | `none` |
| `21.17` | `S0417` | `real-time-inference` | Real-Time Inference | `analyze` | `none` |
| `21.18` | `S0418` | `responsible-ai-evaluation` | Responsible AI Evaluation | `analyze` | `none` |
| `21.19` | `S0419` | `machine-learning-security` | Machine Learning Security | `analyze` | `none` |
| `21.20` | `S0420` | `model-reproducibility` | Model Reproducibility | `analyze` | `none` |

### Category 22: Generative AI and Agents (`generative-ai-and-agents`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Runs offline-safe RAG passage retrieval, grounded `[id](url)` citation verification, token budget estimation, and prompt-injection heuristic defense without hardcoded credentials.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill retrieval-augmented-generation --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `22.01` | `S0421` | `llm-application-builder` | LLM Application Builder | `build` | `none` |
| `22.02` | `S0422` | `retrieval-augmented-generation` | Retrieval-Augmented Generation | `analyze` | `none` |
| `22.03` | `S0423` | `embedding-pipeline-builder` | Embedding Pipeline Builder | `build` | `none` |
| `22.04` | `S0424` | `vector-retrieval-engineering` | Vector Retrieval Engineering | `analyze` | `none` |
| `22.05` | `S0425` | `prompt-engineering` | Prompt Engineering | `analyze` | `none` |
| `22.06` | `S0426` | `structured-output-engineering` | Structured Output Engineering | `analyze` | `none` |
| `22.07` | `S0427` | `tool-using-agent-builder` | Tool-Using Agent Builder | `build` | `none` |
| `22.08` | `S0428` | `multi-agent-orchestration` | Multi-Agent Orchestration | `orchestrate` | `none` |
| `22.09` | `S0429` | `agent-memory-design` | Agent Memory Design | `build` | `none` |
| `22.10` | `S0430` | `agent-planning-system` | Agent Planning System | `orchestrate` | `none` |
| `22.11` | `S0431` | `agent-evaluation-harness` | Agent Evaluation Harness | `orchestrate` | `none` |
| `22.12` | `S0432` | `agent-guardrail-design` | Agent Guardrail Design | `build` | `none` |
| `22.13` | `S0433` | `context-window-management` | Context Window Management | `analyze` | `none` |
| `22.14` | `S0434` | `prompt-injection-defense` | Prompt Injection Defense | `analyze` | `security-sensitive` |
| `22.15` | `S0435` | `grounded-citation-system` | Grounded Citation System | `analyze` | `none` |
| `22.16` | `S0436` | `llm-cost-optimization` | LLM Cost Optimization | `optimize` | `financial` |
| `22.17` | `S0437` | `llm-latency-optimization` | LLM Latency Optimization | `optimize` | `none` |
| `22.18` | `S0438` | `model-routing` | Model Routing | `analyze` | `none` |
| `22.19` | `S0439` | `human-approval-workflow` | Human Approval Workflow | `orchestrate` | `security-sensitive` |
| `22.20` | `S0440` | `generative-ai-observability` | Generative AI Observability | `analyze` | `none` |

### Category 23: Computer Vision and Media Code (`computer-vision-and-media-code`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Synthesizes and validates deterministic vector SVG media assets, dimensional metadata, and safe media pipeline configurations.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill computer-graphics-pipeline --param width=400 --param height=140
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `23.01` | `S0441` | `image-processing-pipeline` | Image Processing Pipeline | `orchestrate` | `none` |
| `23.02` | `S0442` | `computer-vision-model-integration` | Computer Vision Model Integration | `analyze` | `none` |
| `23.03` | `S0443` | `object-detection-system` | Object Detection System | `verify` | `none` |
| `23.04` | `S0444` | `image-classification-system` | Image Classification System | `analyze` | `none` |
| `23.05` | `S0445` | `ocr-pipeline` | OCR Pipeline | `orchestrate` | `none` |
| `23.06` | `S0446` | `video-processing-pipeline` | Video Processing Pipeline | `orchestrate` | `none` |
| `23.07` | `S0447` | `audio-processing-pipeline` | Audio Processing Pipeline | `orchestrate` | `none` |
| `23.08` | `S0448` | `speech-recognition-integration` | Speech Recognition Integration | `analyze` | `none` |
| `23.09` | `S0449` | `speech-synthesis-integration` | Speech Synthesis Integration | `analyze` | `none` |
| `23.10` | `S0450` | `media-transcoding` | Media Transcoding | `analyze` | `none` |
| `23.11` | `S0451` | `streaming-media-server` | Streaming Media Server | `analyze` | `none` |
| `23.12` | `S0452` | `image-optimization-service` | Image Optimization Service | `optimize` | `none` |
| `23.13` | `S0453` | `video-thumbnail-generator` | Video Thumbnail Generator | `build` | `none` |
| `23.14` | `S0454` | `computer-graphics-pipeline` | Computer Graphics Pipeline | `orchestrate` | `none` |
| `23.15` | `S0455` | `opencv-engineering` | OpenCV Engineering | `analyze` | `none` |
| `23.16` | `S0456` | `ffmpeg-automation` | FFmpeg Automation | `orchestrate` | `none` |
| `23.17` | `S0457` | `gpu-media-acceleration` | GPU Media Acceleration | `analyze` | `none` |
| `23.18` | `S0458` | `media-metadata-extraction` | Media Metadata Extraction | `analyze` | `none` |
| `23.19` | `S0459` | `media-quality-validation` | Media Quality Validation | `verify` | `none` |
| `23.20` | `S0460` | `media-pipeline-security` | Media Pipeline Security | `orchestrate` | `none` |

### Category 24: Cloud Architecture (`cloud-architecture`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Calculates non-overlapping IPv4 VPC subnets, multi-AZ composite availability SLAs, and cloud landing-zone / FinOps governance checks.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill cloud-network-design --param vpc_cidr=10.40.0.0/16 --param az_count=3
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `24.01` | `S0461` | `aws-architecture` | AWS Architecture | `analyze` | `none` |
| `24.02` | `S0462` | `microsoft-azure-architecture` | Microsoft Azure Architecture | `analyze` | `none` |
| `24.03` | `S0463` | `google-cloud-architecture` | Google Cloud Architecture | `analyze` | `none` |
| `24.04` | `S0464` | `cloud-landing-zone` | Cloud Landing Zone | `analyze` | `none` |
| `24.05` | `S0465` | `cloud-network-design` | Cloud Network Design | `build` | `none` |
| `24.06` | `S0466` | `cloud-identity-design` | Cloud Identity Design | `build` | `none` |
| `24.07` | `S0467` | `cloud-storage-design` | Cloud Storage Design | `build` | `none` |
| `24.08` | `S0468` | `cloud-database-selection` | Cloud Database Selection | `analyze` | `none` |
| `24.09` | `S0469` | `cloud-compute-selection` | Cloud Compute Selection | `analyze` | `none` |
| `24.10` | `S0470` | `cloud-serverless-design` | Cloud Serverless Design | `build` | `none` |
| `24.11` | `S0471` | `cloud-container-platform` | Cloud Container Platform | `analyze` | `none` |
| `24.12` | `S0472` | `cloud-cost-optimization` | Cloud Cost Optimization | `optimize` | `financial` |
| `24.13` | `S0473` | `cloud-reliability-engineering` | Cloud Reliability Engineering | `analyze` | `none` |
| `24.14` | `S0474` | `cloud-security-architecture` | Cloud Security Architecture | `analyze` | `none` |
| `24.15` | `S0475` | `cloud-migration-planning` | Cloud Migration Planning | `transform` | `none` |
| `24.16` | `S0476` | `hybrid-cloud-architecture` | Hybrid Cloud Architecture | `analyze` | `none` |
| `24.17` | `S0477` | `multi-cloud-architecture` | Multi-Cloud Architecture | `analyze` | `none` |
| `24.18` | `S0478` | `cloud-backup-and-recovery` | Cloud Backup and Recovery | `analyze` | `none` |
| `24.19` | `S0479` | `cloud-compliance-mapping` | Cloud Compliance Mapping | `analyze` | `none` |
| `24.20` | `S0480` | `cloud-architecture-review` | Cloud Architecture Review | `verify` | `none` |

### Category 25: DevOps and CI/CD (`devops-and-ci-cd`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Generates commit-pinned CI/CD workflows and audits pipelines for unpinned @main/@latest actions, missing permissions blocks, and accidental secret echoing.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill github-actions-engineering
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `25.01` | `S0481` | `continuous-integration-pipeline` | Continuous Integration Pipeline | `orchestrate` | `none` |
| `25.02` | `S0482` | `continuous-delivery-pipeline` | Continuous Delivery Pipeline | `orchestrate` | `none` |
| `25.03` | `S0483` | `continuous-deployment-pipeline` | Continuous Deployment Pipeline | `orchestrate` | `none` |
| `25.04` | `S0484` | `build-pipeline-engineering` | Build Pipeline Engineering | `orchestrate` | `none` |
| `25.05` | `S0485` | `release-pipeline-engineering` | Release Pipeline Engineering | `orchestrate` | `none` |
| `25.06` | `S0486` | `github-actions-engineering` | GitHub Actions Engineering | `analyze` | `none` |
| `25.07` | `S0487` | `azure-pipelines-engineering` | Azure Pipelines Engineering | `orchestrate` | `none` |
| `25.08` | `S0488` | `gitlab-ci-engineering` | GitLab CI Engineering | `analyze` | `none` |
| `25.09` | `S0489` | `jenkins-pipeline-engineering` | Jenkins Pipeline Engineering | `orchestrate` | `none` |
| `25.10` | `S0490` | `artifact-repository-management` | Artifact Repository Management | `analyze` | `none` |
| `25.11` | `S0491` | `build-cache-optimization` | Build Cache Optimization | `optimize` | `none` |
| `25.12` | `S0492` | `deployment-promotion-workflow` | Deployment Promotion Workflow | `orchestrate` | `none` |
| `25.13` | `S0493` | `environment-configuration` | Environment Configuration | `analyze` | `none` |
| `25.14` | `S0494` | `secret-injection-workflow` | Secret Injection Workflow | `orchestrate` | `credential` |
| `25.15` | `S0495` | `canary-deployment` | Canary Deployment | `orchestrate` | `none` |
| `25.16` | `S0496` | `blue-green-deployment` | Blue-Green Deployment | `orchestrate` | `none` |
| `25.17` | `S0497` | `rolling-deployment` | Rolling Deployment | `orchestrate` | `none` |
| `25.18` | `S0498` | `release-rollback` | Release Rollback | `orchestrate` | `irreversible` |
| `25.19` | `S0499` | `pipeline-security` | Pipeline Security | `orchestrate` | `none` |
| `25.20` | `S0500` | `pipeline-observability` | Pipeline Observability | `orchestrate` | `none` |

### Category 26: Containers and Orchestration (`containers-and-orchestration`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Audits Dockerfiles and container specs for floating :latest tags, root user execution, unsafe ADD usage, and missing HEALTHCHECK directives.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill container-security-scanning --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `26.01` | `S0501` | `dockerfile-engineering` | Dockerfile Engineering | `analyze` | `none` |
| `26.02` | `S0502` | `container-image-optimization` | Container Image Optimization | `optimize` | `none` |
| `26.03` | `S0503` | `container-security-scanning` | Container Security Scanning | `analyze` | `none` |
| `26.04` | `S0504` | `docker-compose-engineering` | Docker Compose Engineering | `analyze` | `none` |
| `26.05` | `S0505` | `kubernetes-application-deployment` | Kubernetes Application Deployment | `orchestrate` | `none` |
| `26.06` | `S0506` | `kubernetes-operator-builder` | Kubernetes Operator Builder | `build` | `none` |
| `26.07` | `S0507` | `helm-chart-engineering` | Helm Chart Engineering | `analyze` | `none` |
| `26.08` | `S0508` | `kustomize-engineering` | Kustomize Engineering | `analyze` | `none` |
| `26.09` | `S0509` | `kubernetes-networking` | Kubernetes Networking | `analyze` | `none` |
| `26.10` | `S0510` | `kubernetes-storage` | Kubernetes Storage | `analyze` | `none` |
| `26.11` | `S0511` | `kubernetes-autoscaling` | Kubernetes Autoscaling | `analyze` | `none` |
| `26.12` | `S0512` | `kubernetes-policy-enforcement` | Kubernetes Policy Enforcement | `verify` | `none` |
| `26.13` | `S0513` | `kubernetes-observability` | Kubernetes Observability | `analyze` | `none` |
| `26.14` | `S0514` | `kubernetes-troubleshooting` | Kubernetes Troubleshooting | `analyze` | `none` |
| `26.15` | `S0515` | `service-mesh-engineering` | Service Mesh Engineering | `analyze` | `none` |
| `26.16` | `S0516` | `istio-engineering` | Istio Engineering | `analyze` | `none` |
| `26.17` | `S0517` | `container-registry-management` | Container Registry Management | `analyze` | `none` |
| `26.18` | `S0518` | `container-supply-chain-security` | Container Supply Chain Security | `analyze` | `none` |
| `26.19` | `S0519` | `cluster-upgrade-planning` | Cluster Upgrade Planning | `transform` | `destructive` |
| `26.20` | `S0520` | `cluster-disaster-recovery` | Cluster Disaster Recovery | `analyze` | `none` |

### Category 27: Infrastructure as Code (`infrastructure-as-code`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Enforces Policy-as-Code rules across Terraform/OpenTofu/CloudFormation/Bicep configs, flagging unrestricted 0.0.0.0/0 SSH/RDP ingress, wildcard IAM actions, and disabled encryption.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill policy-as-code --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `27.01` | `S0521` | `terraform-engineering` | Terraform Engineering | `analyze` | `none` |
| `27.02` | `S0522` | `opentofu-engineering` | OpenTofu Engineering | `analyze` | `none` |
| `27.03` | `S0523` | `pulumi-engineering` | Pulumi Engineering | `analyze` | `none` |
| `27.04` | `S0524` | `aws-cloudformation-engineering` | AWS CloudFormation Engineering | `analyze` | `none` |
| `27.05` | `S0525` | `azure-bicep-engineering` | Azure Bicep Engineering | `analyze` | `none` |
| `27.06` | `S0526` | `arm-template-engineering` | ARM Template Engineering | `analyze` | `none` |
| `27.07` | `S0527` | `google-cloud-deployment-automation` | Google Cloud Deployment Automation | `orchestrate` | `none` |
| `27.08` | `S0528` | `ansible-automation` | Ansible Automation | `orchestrate` | `none` |
| `27.09` | `S0529` | `chef-automation` | Chef Automation | `orchestrate` | `none` |
| `27.10` | `S0530` | `puppet-automation` | Puppet Automation | `orchestrate` | `none` |
| `27.11` | `S0531` | `infrastructure-module-design` | Infrastructure Module Design | `build` | `none` |
| `27.12` | `S0532` | `infrastructure-state-management` | Infrastructure State Management | `analyze` | `none` |
| `27.13` | `S0533` | `infrastructure-drift-detection` | Infrastructure Drift Detection | `verify` | `none` |
| `27.14` | `S0534` | `policy-as-code` | Policy as Code | `analyze` | `none` |
| `27.15` | `S0535` | `secrets-as-code-governance` | Secrets as Code Governance | `analyze` | `credential` |
| `27.16` | `S0536` | `environment-provisioning` | Environment Provisioning | `analyze` | `none` |
| `27.17` | `S0537` | `immutable-infrastructure` | Immutable Infrastructure | `analyze` | `none` |
| `27.18` | `S0538` | `infrastructure-testing` | Infrastructure Testing | `verify` | `none` |
| `27.19` | `S0539` | `infrastructure-documentation` | Infrastructure Documentation | `analyze` | `none` |
| `27.20` | `S0540` | `infrastructure-recovery` | Infrastructure Recovery | `analyze` | `none` |

### Category 28: Operating Systems and Low-Level Systems (`operating-systems-and-low-level-systems`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Performs read-only POSIX host CPU/page-size diagnostics and computes virtual-memory page number and byte-offset translations.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill virtual-memory-analysis --param vaddr=0x004031A8
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `28.01` | `S0541` | `linux-systems-engineering` | Linux Systems Engineering | `analyze` | `none` |
| `28.02` | `S0542` | `windows-systems-engineering` | Windows Systems Engineering | `analyze` | `none` |
| `28.03` | `S0543` | `macos-systems-engineering` | macOS Systems Engineering | `analyze` | `none` |
| `28.04` | `S0544` | `kernel-module-engineering` | Kernel Module Engineering | `analyze` | `privileged` |
| `28.05` | `S0545` | `device-driver-engineering` | Device Driver Engineering | `analyze` | `privileged` |
| `28.06` | `S0546` | `process-and-thread-engineering` | Process and Thread Engineering | `analyze` | `none` |
| `28.07` | `S0547` | `virtual-memory-analysis` | Virtual Memory Analysis | `analyze` | `none` |
| `28.08` | `S0548` | `file-system-engineering` | File System Engineering | `analyze` | `none` |
| `28.09` | `S0549` | `network-stack-analysis` | Network Stack Analysis | `analyze` | `none` |
| `28.10` | `S0550` | `system-call-analysis` | System Call Analysis | `analyze` | `none` |
| `28.11` | `S0551` | `boot-process-engineering` | Boot Process Engineering | `analyze` | `none` |
| `28.12` | `S0552` | `service-manager-engineering` | Service Manager Engineering | `analyze` | `none` |
| `28.13` | `S0553` | `package-manager-engineering` | Package Manager Engineering | `analyze` | `none` |
| `28.14` | `S0554` | `system-performance-tuning` | System Performance Tuning | `optimize` | `none` |
| `28.15` | `S0555` | `system-resource-diagnosis` | System Resource Diagnosis | `analyze` | `none` |
| `28.16` | `S0556` | `virtualization-engineering` | Virtualization Engineering | `analyze` | `none` |
| `28.17` | `S0557` | `hypervisor-integration` | Hypervisor Integration | `analyze` | `privileged` |
| `28.18` | `S0558` | `embedded-linux-engineering` | Embedded Linux Engineering | `analyze` | `none` |
| `28.19` | `S0559` | `real-time-operating-systems` | Real-Time Operating Systems | `analyze` | `none` |
| `28.20` | `S0560` | `systems-security-hardening` | Systems Security Hardening | `analyze` | `none` |

### Category 29: Networking and Distributed Systems (`networking-and-distributed-systems`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Computes Little's Law in-flight concurrency (L = lambda * W), Raft majority quorums, and wire-level W3C traceparent headers.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill network-distributed-tracing --param rps=500 --param latency_sec=0.03
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `29.01` | `S0561` | `network-protocol-engineering` | Network Protocol Engineering | `analyze` | `none` |
| `29.02` | `S0562` | `tcp-and-udp-analysis` | TCP and UDP Analysis | `analyze` | `none` |
| `29.03` | `S0563` | `http-protocol-engineering` | HTTP Protocol Engineering | `analyze` | `none` |
| `29.04` | `S0564` | `dns-engineering` | DNS Engineering | `analyze` | `none` |
| `29.05` | `S0565` | `tls-engineering` | TLS Engineering | `analyze` | `none` |
| `29.06` | `S0566` | `load-balancer-configuration` | Load Balancer Configuration | `analyze` | `none` |
| `29.07` | `S0567` | `reverse-proxy-engineering` | Reverse Proxy Engineering | `analyze` | `none` |
| `29.08` | `S0568` | `content-delivery-network-design` | Content Delivery Network Design | `build` | `none` |
| `29.09` | `S0569` | `service-discovery` | Service Discovery | `analyze` | `none` |
| `29.10` | `S0570` | `distributed-consensus` | Distributed Consensus | `analyze` | `none` |
| `29.11` | `S0571` | `leader-election` | Leader Election | `analyze` | `none` |
| `29.12` | `S0572` | `distributed-locking` | Distributed Locking | `analyze` | `none` |
| `29.13` | `S0573` | `distributed-transactions` | Distributed Transactions | `analyze` | `none` |
| `29.14` | `S0574` | `message-delivery-semantics` | Message Delivery Semantics | `analyze` | `none` |
| `29.15` | `S0575` | `network-resilience-testing` | Network Resilience Testing | `verify` | `none` |
| `29.16` | `S0576` | `latency-budget-engineering` | Latency Budget Engineering | `analyze` | `none` |
| `29.17` | `S0577` | `capacity-planning` | Capacity Planning | `analyze` | `none` |
| `29.18` | `S0578` | `network-distributed-tracing` | Distributed Tracing | `analyze` | `none` |
| `29.19` | `S0579` | `network-troubleshooting` | Network Troubleshooting | `analyze` | `none` |
| `29.20` | `S0580` | `zero-trust-networking` | Zero Trust Networking | `analyze` | `none` |

### Category 30: Cybersecurity Engineering (`cybersecurity-engineering`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Executes regex/AST Static Application Security Testing (SAST), high-risk secret pattern scanning with automatic redaction, and STRIDE threat modeling.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill static-application-security-testing --input software-engineering/tools-workbench.py
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `30.01` | `S0581` | `threat-modeling` | Threat Modeling | `analyze` | `none` |
| `30.02` | `S0582` | `secure-coding-review` | Secure Coding Review | `verify` | `none` |
| `30.03` | `S0583` | `application-security-testing` | Application Security Testing | `verify` | `none` |
| `30.04` | `S0584` | `static-application-security-testing` | Static Application Security Testing | `verify` | `none` |
| `30.05` | `S0585` | `dynamic-application-security-testing` | Dynamic Application Security Testing | `verify` | `none` |
| `30.06` | `S0586` | `software-composition-analysis` | Software Composition Analysis | `analyze` | `none` |
| `30.07` | `S0587` | `dependency-vulnerability-management` | Dependency Vulnerability Management | `analyze` | `none` |
| `30.08` | `S0588` | `secrets-detection` | Secrets Detection | `verify` | `credential` |
| `30.09` | `S0589` | `identity-and-access-management` | Identity and Access Management | `analyze` | `credential` |
| `30.10` | `S0590` | `oauth-and-openid-connect` | OAuth and OpenID Connect | `analyze` | `credential` |
| `30.11` | `S0591` | `cryptographic-implementation-review` | Cryptographic Implementation Review | `verify` | `none` |
| `30.12` | `S0592` | `key-management` | Key Management | `analyze` | `credential` |
| `30.13` | `S0593` | `security-logging` | Security Logging | `analyze` | `none` |
| `30.14` | `S0594` | `security-incident-automation` | Security Incident Automation | `orchestrate` | `security-sensitive` |
| `30.15` | `S0595` | `security-patch-engineering` | Security Patch Engineering | `analyze` | `security-sensitive` |
| `30.16` | `S0596` | `web-security-hardening` | Web Security Hardening | `analyze` | `none` |
| `30.17` | `S0597` | `api-security-testing` | API Security Testing | `verify` | `none` |
| `30.18` | `S0598` | `cloud-security-posture` | Cloud Security Posture | `analyze` | `none` |
| `30.19` | `S0599` | `container-security` | Container Security | `analyze` | `none` |
| `30.20` | `S0600` | `supply-chain-security` | Supply Chain Security | `analyze` | `none` |

### Category 31: Testing Engineering (`testing-engineering`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Builds and runs in-process Python unittest suites covering normal, boundary (empty/zero), and idempotent property assertions.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill unit-test-engineering
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `31.01` | `S0601` | `unit-test-engineering` | Unit Test Engineering | `analyze` | `none` |
| `31.02` | `S0602` | `integration-test-engineering` | Integration Test Engineering | `analyze` | `none` |
| `31.03` | `S0603` | `end-to-end-test-engineering` | End-to-End Test Engineering | `analyze` | `none` |
| `31.04` | `S0604` | `contract-test-engineering` | Contract Test Engineering | `analyze` | `none` |
| `31.05` | `S0605` | `property-based-testing` | Property-Based Testing | `verify` | `none` |
| `31.06` | `S0606` | `fuzz-testing` | Fuzz Testing | `verify` | `none` |
| `31.07` | `S0607` | `mutation-testing` | Mutation Testing | `verify` | `none` |
| `31.08` | `S0608` | `snapshot-testing` | Snapshot Testing | `verify` | `none` |
| `31.09` | `S0609` | `golden-file-testing` | Golden File Testing | `verify` | `none` |
| `31.10` | `S0610` | `performance-testing` | Performance Testing | `verify` | `none` |
| `31.11` | `S0611` | `load-testing` | Load Testing | `verify` | `none` |
| `31.12` | `S0612` | `stress-testing` | Stress Testing | `verify` | `none` |
| `31.13` | `S0613` | `soak-testing` | Soak Testing | `verify` | `none` |
| `31.14` | `S0614` | `chaos-testing` | Chaos Testing | `verify` | `none` |
| `31.15` | `S0615` | `security-testing` | Security Testing | `verify` | `none` |
| `31.16` | `S0616` | `accessibility-testing` | Accessibility Testing | `verify` | `none` |
| `31.17` | `S0617` | `compatibility-testing` | Compatibility Testing | `verify` | `none` |
| `31.18` | `S0618` | `regression-test-design` | Regression Test Design | `build` | `none` |
| `31.19` | `S0619` | `test-data-management` | Test Data Management | `analyze` | `none` |
| `31.20` | `S0620` | `test-automation-architecture` | Test Automation Architecture | `orchestrate` | `none` |

### Category 32: Debugging and Root Cause Analysis (`debugging-and-root-cause-analysis`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Extracts stack-trace frames and root exceptions, detects cycles in lock wait-for graphs, and synthesizes minimal unified-diff corrective patches.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill deadlock-debugging --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `32.01` | `S0621` | `reproduction-case-builder` | Reproduction Case Builder | `build` | `none` |
| `32.02` | `S0622` | `stack-trace-analysis` | Stack Trace Analysis | `analyze` | `none` |
| `32.03` | `S0623` | `log-based-debugging` | Log-Based Debugging | `analyze` | `none` |
| `32.04` | `S0624` | `interactive-debugging` | Interactive Debugging | `analyze` | `none` |
| `32.05` | `S0625` | `remote-debugging` | Remote Debugging | `analyze` | `none` |
| `32.06` | `S0626` | `core-dump-analysis` | Core Dump Analysis | `analyze` | `none` |
| `32.07` | `S0627` | `memory-corruption-debugging` | Memory Corruption Debugging | `analyze` | `none` |
| `32.08` | `S0628` | `race-condition-debugging` | Race Condition Debugging | `analyze` | `none` |
| `32.09` | `S0629` | `deadlock-debugging` | Deadlock Debugging | `analyze` | `none` |
| `32.10` | `S0630` | `performance-regression-diagnosis` | Performance Regression Diagnosis | `optimize` | `none` |
| `32.11` | `S0631` | `network-failure-diagnosis` | Network Failure Diagnosis | `analyze` | `none` |
| `32.12` | `S0632` | `database-failure-diagnosis` | Database Failure Diagnosis | `analyze` | `none` |
| `32.13` | `S0633` | `distributed-system-debugging` | Distributed System Debugging | `analyze` | `none` |
| `32.14` | `S0634` | `frontend-rendering-debugging` | Frontend Rendering Debugging | `analyze` | `none` |
| `32.15` | `S0635` | `mobile-crash-debugging` | Mobile Crash Debugging | `analyze` | `none` |
| `32.16` | `S0636` | `build-failure-debugging` | Build Failure Debugging | `analyze` | `none` |
| `32.17` | `S0637` | `dependency-conflict-debugging` | Dependency Conflict Debugging | `analyze` | `none` |
| `32.18` | `S0638` | `environment-drift-debugging` | Environment Drift Debugging | `analyze` | `none` |
| `32.19` | `S0639` | `root-cause-analysis-report` | Root Cause Analysis Report | `analyze` | `none` |
| `32.20` | `S0640` | `minimal-corrective-patch` | Minimal Corrective Patch | `analyze` | `none` |

### Category 33: Compilers and Language Tooling (`compilers-and-language-tooling`)

- **Engine**: `software-engineering/tools-engine-languages.py`
- **How It Works**: Executes a complete mini-compiler pipeline: lexer, recursive-descent parser, AST builder, constant-folding optimizer, static type checker, bytecode compiler, stack VM, and Base64-VLQ encoder.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill virtual-machine-builder --text 'let a = 6 * 7; let b = a + 8; b'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `33.01` | `S0641` | `lexer-builder` | Lexer Builder | `build` | `none` |
| `33.02` | `S0642` | `parser-builder` | Parser Builder | `build` | `none` |
| `33.03` | `S0643` | `abstract-syntax-tree-design` | Abstract Syntax Tree Design | `build` | `none` |
| `33.04` | `S0644` | `semantic-analyzer-builder` | Semantic Analyzer Builder | `build` | `none` |
| `33.05` | `S0645` | `type-checker-builder` | Type Checker Builder | `build` | `none` |
| `33.06` | `S0646` | `intermediate-representation-design` | Intermediate Representation Design | `build` | `none` |
| `33.07` | `S0647` | `code-generator-builder` | Code Generator Builder | `build` | `none` |
| `33.08` | `S0648` | `interpreter-builder` | Interpreter Builder | `build` | `none` |
| `33.09` | `S0649` | `virtual-machine-builder` | Virtual Machine Builder | `build` | `none` |
| `33.10` | `S0650` | `bytecode-compiler` | Bytecode Compiler | `analyze` | `none` |
| `33.11` | `S0651` | `just-in-time-compiler` | Just-In-Time Compiler | `analyze` | `none` |
| `33.12` | `S0652` | `ahead-of-time-compiler` | Ahead-of-Time Compiler | `analyze` | `none` |
| `33.13` | `S0653` | `compiler-optimization-pass` | Compiler Optimization Pass | `optimize` | `none` |
| `33.14` | `S0654` | `linker-and-loader-analysis` | Linker and Loader Analysis | `analyze` | `none` |
| `33.15` | `S0655` | `source-map-engineering` | Source Map Engineering | `analyze` | `none` |
| `33.16` | `S0656` | `transpiler-builder` | Transpiler Builder | `build` | `none` |
| `33.17` | `S0657` | `formatter-builder` | Formatter Builder | `build` | `none` |
| `33.18` | `S0658` | `linter-builder` | Linter Builder | `build` | `none` |
| `33.19` | `S0659` | `language-server-protocol` | Language Server Protocol | `analyze` | `none` |
| `33.20` | `S0660` | `domain-specific-language-builder` | Domain-Specific Language Builder | `build` | `none` |

### Category 34: Build Systems and Package Management (`build-systems-and-package-management`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Validates Semantic Versioning 2.0.0 identifiers, computes SHA-256 content digests, and enforces human approval before package publishing.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill semantic-versioning --param version=2.1.0-rc.1
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `34.01` | `S0661` | `build-system-selection` | Build System Selection | `analyze` | `none` |
| `34.02` | `S0662` | `dependency-resolution` | Dependency Resolution | `analyze` | `none` |
| `34.03` | `S0663` | `semantic-versioning` | Semantic Versioning | `analyze` | `none` |
| `34.04` | `S0664` | `lockfile-governance` | Lockfile Governance | `analyze` | `none` |
| `34.05` | `S0665` | `package-publishing` | Package Publishing | `analyze` | `publishing` |
| `34.06` | `S0666` | `private-package-registry` | Private Package Registry | `analyze` | `none` |
| `34.07` | `S0667` | `monorepo-build-system` | Monorepo Build System | `analyze` | `none` |
| `34.08` | `S0668` | `bazel-engineering` | Bazel Engineering | `analyze` | `none` |
| `34.09` | `S0669` | `buck-engineering` | Buck Engineering | `analyze` | `none` |
| `34.10` | `S0670` | `ninja-build-engineering` | Ninja Build Engineering | `analyze` | `none` |
| `34.11` | `S0671` | `gradle-engineering` | Gradle Engineering | `analyze` | `none` |
| `34.12` | `S0672` | `maven-engineering` | Maven Engineering | `analyze` | `none` |
| `34.13` | `S0673` | `npm-workspace-engineering` | NPM Workspace Engineering | `analyze` | `none` |
| `34.14` | `S0674` | `yarn-workspace-engineering` | Yarn Workspace Engineering | `analyze` | `none` |
| `34.15` | `S0675` | `pnpm-workspace-engineering` | PNPM Workspace Engineering | `analyze` | `none` |
| `34.16` | `S0676` | `python-packaging` | Python Packaging | `analyze` | `none` |
| `34.17` | `S0677` | `cargo-packaging` | Cargo Packaging | `analyze` | `none` |
| `34.18` | `S0678` | `nuget-packaging` | NuGet Packaging | `analyze` | `none` |
| `34.19` | `S0679` | `reproducible-builds` | Reproducible Builds | `analyze` | `none` |
| `34.20` | `S0680` | `hermetic-build-engineering` | Hermetic Build Engineering | `analyze` | `none` |

### Category 35: Code Quality and Refactoring (`code-quality-and-refactoring`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Computes per-function AST McCabe cyclomatic complexity, line counts, and duplicate-line counts to guide behavior-preserving refactoring.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill complexity-reduction --input software-engineering/tools-input-parser.py
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `35.01` | `S0681` | `code-review` | Code Review | `verify` | `none` |
| `35.02` | `S0682` | `refactoring-planning` | Refactoring Planning | `transform` | `none` |
| `35.03` | `S0683` | `legacy-code-modernization` | Legacy Code Modernization | `transform` | `none` |
| `35.04` | `S0684` | `technical-debt-register` | Technical Debt Register | `analyze` | `none` |
| `35.05` | `S0685` | `dead-code-removal` | Dead Code Removal | `analyze` | `irreversible` |
| `35.06` | `S0686` | `duplicate-code-reduction` | Duplicate Code Reduction | `optimize` | `none` |
| `35.07` | `S0687` | `complexity-reduction` | Complexity Reduction | `optimize` | `none` |
| `35.08` | `S0688` | `naming-improvement` | Naming Improvement | `analyze` | `none` |
| `35.09` | `S0689` | `function-decomposition` | Function Decomposition | `analyze` | `none` |
| `35.10` | `S0690` | `class-decomposition` | Class Decomposition | `analyze` | `none` |
| `35.11` | `S0691` | `module-boundary-repair` | Module Boundary Repair | `analyze` | `none` |
| `35.12` | `S0692` | `dependency-inversion` | Dependency Inversion | `analyze` | `none` |
| `35.13` | `S0693` | `api-simplification` | API Simplification | `analyze` | `none` |
| `35.14` | `S0694` | `type-safety-improvement` | Type Safety Improvement | `analyze` | `none` |
| `35.15` | `S0695` | `null-safety-improvement` | Null Safety Improvement | `analyze` | `none` |
| `35.16` | `S0696` | `error-handling-refactor` | Error Handling Refactor | `transform` | `none` |
| `35.17` | `S0697` | `concurrency-refactor` | Concurrency Refactor | `transform` | `none` |
| `35.18` | `S0698` | `performance-refactor` | Performance Refactor | `transform` | `none` |
| `35.19` | `S0699` | `security-refactor` | Security Refactor | `transform` | `none` |
| `35.20` | `S0700` | `behavior-preserving-refactor` | Behavior-Preserving Refactor | `transform` | `none` |

### Category 36: Performance Engineering (`performance-engineering`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Runs warmup + multi-iteration statistical micro-benchmarks (median and p95 latency) and enforces quantitative latency regression budgets.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill mobile-engineering-performance-profiling --param budget_ms=5.0
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `36.01` | `S0701` | `performance-budget-definition` | Performance Budget Definition | `optimize` | `none` |
| `36.02` | `S0702` | `benchmark-design` | Benchmark Design | `build` | `none` |
| `36.03` | `S0703` | `cpu-profiling` | CPU Profiling | `optimize` | `none` |
| `36.04` | `S0704` | `memory-profiling` | Memory Profiling | `optimize` | `none` |
| `36.05` | `S0705` | `i-o-profiling` | I/O Profiling | `optimize` | `none` |
| `36.06` | `S0706` | `database-profiling` | Database Profiling | `optimize` | `none` |
| `36.07` | `S0707` | `network-profiling` | Network Profiling | `optimize` | `none` |
| `36.08` | `S0708` | `frontend-performance-profiling` | Frontend Performance Profiling | `optimize` | `none` |
| `36.09` | `S0709` | `mobile-engineering-performance-profiling` | Mobile Performance Profiling | `optimize` | `none` |
| `36.10` | `S0710` | `gpu-profiling` | GPU Profiling | `optimize` | `none` |
| `36.11` | `S0711` | `latency-optimization` | Latency Optimization | `optimize` | `none` |
| `36.12` | `S0712` | `throughput-optimization` | Throughput Optimization | `optimize` | `none` |
| `36.13` | `S0713` | `memory-optimization` | Memory Optimization | `optimize` | `none` |
| `36.14` | `S0714` | `allocation-reduction` | Allocation Reduction | `optimize` | `none` |
| `36.15` | `S0715` | `caching-strategy` | Caching Strategy | `optimize` | `none` |
| `36.16` | `S0716` | `concurrency-optimization` | Concurrency Optimization | `optimize` | `none` |
| `36.17` | `S0717` | `vectorization` | Vectorization | `analyze` | `none` |
| `36.18` | `S0718` | `query-optimization` | Query Optimization | `optimize` | `none` |
| `36.19` | `S0719` | `performance-regression-guard` | Performance Regression Guard | `optimize` | `none` |
| `36.20` | `S0720` | `capacity-benchmarking` | Capacity Benchmarking | `analyze` | `none` |

### Category 37: Observability and Reliability (`observability-and-reliability`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Calculates Google SRE multi-window SLO error-budget burn rates, fast-burn paging thresholds (14.4x), and OpenTelemetry head/tail sampling configs.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill observability-distributed-tracing --param slo_pct=99.95 --param error_rate=0.0002
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `37.01` | `S0721` | `structured-logging` | Structured Logging | `analyze` | `none` |
| `37.02` | `S0722` | `metrics-instrumentation` | Metrics Instrumentation | `analyze` | `none` |
| `37.03` | `S0723` | `observability-distributed-tracing` | Distributed Tracing | `analyze` | `none` |
| `37.04` | `S0724` | `opentelemetry-engineering` | OpenTelemetry Engineering | `analyze` | `none` |
| `37.05` | `S0725` | `service-level-indicators` | Service Level Indicators | `analyze` | `none` |
| `37.06` | `S0726` | `service-level-objectives` | Service Level Objectives | `analyze` | `none` |
| `37.07` | `S0727` | `error-budget-management` | Error Budget Management | `analyze` | `none` |
| `37.08` | `S0728` | `alert-design` | Alert Design | `build` | `none` |
| `37.09` | `S0729` | `dashboard-engineering` | Dashboard Engineering | `analyze` | `none` |
| `37.10` | `S0730` | `health-check-design` | Health Check Design | `build` | `none` |
| `37.11` | `S0731` | `readiness-and-liveness-probes` | Readiness and Liveness Probes | `analyze` | `none` |
| `37.12` | `S0732` | `resilience-patterns` | Resilience Patterns | `analyze` | `none` |
| `37.13` | `S0733` | `circuit-breaker-engineering` | Circuit Breaker Engineering | `analyze` | `none` |
| `37.14` | `S0734` | `retry-and-backoff-design` | Retry and Backoff Design | `build` | `none` |
| `37.15` | `S0735` | `bulkhead-isolation` | Bulkhead Isolation | `analyze` | `none` |
| `37.16` | `S0736` | `graceful-degradation` | Graceful Degradation | `analyze` | `none` |
| `37.17` | `S0737` | `disaster-recovery-testing` | Disaster Recovery Testing | `verify` | `none` |
| `37.18` | `S0738` | `incident-runbook` | Incident Runbook | `analyze` | `none` |
| `37.19` | `S0739` | `post-incident-review` | Post-Incident Review | `verify` | `none` |
| `37.20` | `S0740` | `reliability-game-day` | Reliability Game Day | `analyze` | `none` |

### Category 38: Git and Source Control (`git-and-source-control`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Verifies Conventional Commit headers, screens for unresolved merge conflict markers, calculates git-bisect step bounds, and gates destructive history rewrites.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill commit-hygiene --param 'commit_message=feat(parser): support copied html tables'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `38.01` | `S0741` | `git-repository-initialization` | Git Repository Initialization | `build` | `none` |
| `38.02` | `S0742` | `branching-strategy` | Branching Strategy | `analyze` | `none` |
| `38.03` | `S0743` | `commit-hygiene` | Commit Hygiene | `analyze` | `none` |
| `38.04` | `S0744` | `interactive-rebase` | Interactive Rebase | `analyze` | `irreversible` |
| `38.05` | `S0745` | `merge-conflict-resolution` | Merge Conflict Resolution | `analyze` | `none` |
| `38.06` | `S0746` | `git-bisect-debugging` | Git Bisect Debugging | `analyze` | `none` |
| `38.07` | `S0747` | `git-hooks-engineering` | Git Hooks Engineering | `analyze` | `none` |
| `38.08` | `S0748` | `git-submodule-management` | Git Submodule Management | `analyze` | `none` |
| `38.09` | `S0749` | `git-subtree-management` | Git Subtree Management | `analyze` | `none` |
| `38.10` | `S0750` | `large-file-storage` | Large File Storage | `analyze` | `none` |
| `38.11` | `S0751` | `monorepo-version-control` | Monorepo Version Control | `analyze` | `none` |
| `38.12` | `S0752` | `release-tagging` | Release Tagging | `analyze` | `publishing` |
| `38.13` | `S0753` | `signed-commits` | Signed Commits | `analyze` | `credential` |
| `38.14` | `S0754` | `protected-branch-configuration` | Protected Branch Configuration | `analyze` | `none` |
| `38.15` | `S0755` | `pull-request-workflow` | Pull Request Workflow | `orchestrate` | `none` |
| `38.16` | `S0756` | `code-ownership-rules` | Code Ownership Rules | `analyze` | `none` |
| `38.17` | `S0757` | `repository-migration` | Repository Migration | `transform` | `none` |
| `38.18` | `S0758` | `history-rewriting` | History Rewriting | `analyze` | `irreversible` |
| `38.19` | `S0759` | `secret-removal-from-history` | Secret Removal from History | `analyze` | `irreversible` |
| `38.20` | `S0760` | `source-control-recovery` | Source Control Recovery | `analyze` | `none` |

### Category 39: Documentation Engineering (`documentation-engineering`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Validates case-sensitive relative Markdown links on disk, authors docs-as-code artifacts, and audits documentation hygiene.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill documentation-link-validation --input software-engineering/knowledge-readme.md
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `39.01` | `S0761` | `readme-authoring` | README Authoring | `build` | `none` |
| `39.02` | `S0762` | `developer-guide` | Developer Guide | `analyze` | `none` |
| `39.03` | `S0763` | `documentation-engineering-architecture-docs` | Architecture Documentation | `analyze` | `none` |
| `39.04` | `S0764` | `api-reference-documentation` | API Reference Documentation | `analyze` | `none` |
| `39.05` | `S0765` | `code-comment-engineering` | Code Comment Engineering | `analyze` | `none` |
| `39.06` | `S0766` | `tutorial-authoring` | Tutorial Authoring | `build` | `none` |
| `39.07` | `S0767` | `how-to-guide` | How-To Guide | `analyze` | `none` |
| `39.08` | `S0768` | `troubleshooting-guide` | Troubleshooting Guide | `analyze` | `none` |
| `39.09` | `S0769` | `runbook-authoring` | Runbook Authoring | `build` | `none` |
| `39.10` | `S0770` | `release-notes` | Release Notes | `analyze` | `none` |
| `39.11` | `S0771` | `changelog-engineering` | Changelog Engineering | `analyze` | `none` |
| `39.12` | `S0772` | `migration-guide` | Migration Guide | `transform` | `none` |
| `39.13` | `S0773` | `contribution-guide` | Contribution Guide | `analyze` | `none` |
| `39.14` | `S0774` | `security-policy` | Security Policy | `analyze` | `none` |
| `39.15` | `S0775` | `documentation-site-builder` | Documentation Site Builder | `build` | `none` |
| `39.16` | `S0776` | `documentation-search` | Documentation Search | `analyze` | `none` |
| `39.17` | `S0777` | `documentation-versioning` | Documentation Versioning | `analyze` | `none` |
| `39.18` | `S0778` | `executable-documentation` | Executable Documentation | `analyze` | `none` |
| `39.19` | `S0779` | `documentation-link-validation` | Documentation Link Validation | `verify` | `none` |
| `39.20` | `S0780` | `documentation-freshness-audit` | Documentation Freshness Audit | `verify` | `none` |

### Category 40: Developer Experience and Tooling (`developer-experience-and-tooling`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Generates deterministic seeded test fixtures, .editorconfig files, and reproducible developer environment bootstrap definitions.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill seed-data-generator
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `40.01` | `S0781` | `developer-environment-bootstrap` | Developer Environment Bootstrap | `analyze` | `none` |
| `40.02` | `S0782` | `dev-container-builder` | Dev Container Builder | `build` | `none` |
| `40.03` | `S0783` | `ide-configuration` | IDE Configuration | `analyze` | `none` |
| `40.04` | `S0784` | `editor-configuration` | Editor Configuration | `analyze` | `none` |
| `40.05` | `S0785` | `language-server-setup` | Language Server Setup | `build` | `none` |
| `40.06` | `S0786` | `code-formatter-setup` | Code Formatter Setup | `build` | `none` |
| `40.07` | `S0787` | `linter-setup` | Linter Setup | `build` | `none` |
| `40.08` | `S0788` | `pre-commit-hook-setup` | Pre-Commit Hook Setup | `build` | `none` |
| `40.09` | `S0789` | `local-service-orchestration` | Local Service Orchestration | `orchestrate` | `none` |
| `40.10` | `S0790` | `mock-service-builder` | Mock Service Builder | `build` | `none` |
| `40.11` | `S0791` | `seed-data-generator` | Seed Data Generator | `build` | `none` |
| `40.12` | `S0792` | `test-fixture-builder` | Test Fixture Builder | `build` | `none` |
| `40.13` | `S0793` | `code-generator-tool` | Code Generator Tool | `build` | `none` |
| `40.14` | `S0794` | `scaffolding-cli` | Scaffolding CLI | `build` | `none` |
| `40.15` | `S0795` | `developer-portal` | Developer Portal | `analyze` | `none` |
| `40.16` | `S0796` | `internal-package-template` | Internal Package Template | `analyze` | `none` |
| `40.17` | `S0797` | `golden-path-engineering` | Golden Path Engineering | `analyze` | `none` |
| `40.18` | `S0798` | `developer-feedback-loop` | Developer Feedback Loop | `analyze` | `none` |
| `40.19` | `S0799` | `onboarding-automation` | Onboarding Automation | `orchestrate` | `none` |
| `40.20` | `S0800` | `developer-productivity-measurement` | Developer Productivity Measurement | `analyze` | `none` |

### Category 41: Desktop, Embedded, and Edge (`desktop-embedded-and-edge`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Computes embedded CRC-16/CCITT and CRC-32 packet frame checksums, Liu & Layland RTOS rate-monotonic CPU bounds, and battery runtime estimates.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill real-time-firmware --param battery_mah=3000 --param avg_ma=1.5
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `41.01` | `S0801` | `windows-desktop-application` | Windows Desktop Application | `analyze` | `none` |
| `41.02` | `S0802` | `macos-desktop-application` | macOS Desktop Application | `analyze` | `none` |
| `41.03` | `S0803` | `linux-desktop-application` | Linux Desktop Application | `analyze` | `none` |
| `41.04` | `S0804` | `electron-application-builder` | Electron Application Builder | `build` | `none` |
| `41.05` | `S0805` | `tauri-application-builder` | Tauri Application Builder | `build` | `none` |
| `41.06` | `S0806` | `qt-application-builder` | Qt Application Builder | `build` | `none` |
| `41.07` | `S0807` | `embedded-firmware-builder` | Embedded Firmware Builder | `build` | `none` |
| `41.08` | `S0808` | `microcontroller-programming` | Microcontroller Programming | `analyze` | `none` |
| `41.09` | `S0809` | `arduino-engineering` | Arduino Engineering | `analyze` | `none` |
| `41.10` | `S0810` | `raspberry-pi-engineering` | Raspberry Pi Engineering | `analyze` | `none` |
| `41.11` | `S0811` | `iot-device-software` | IoT Device Software | `analyze` | `none` |
| `41.12` | `S0812` | `edge-computing-application` | Edge Computing Application | `analyze` | `none` |
| `41.13` | `S0813` | `sensor-integration` | Sensor Integration | `analyze` | `none` |
| `41.14` | `S0814` | `hardware-communication-protocols` | Hardware Communication Protocols | `analyze` | `none` |
| `41.15` | `S0815` | `real-time-firmware` | Real-Time Firmware | `analyze` | `none` |
| `41.16` | `S0816` | `bootloader-engineering` | Bootloader Engineering | `analyze` | `privileged` |
| `41.17` | `S0817` | `over-the-air-update-system` | Over-the-Air Update System | `analyze` | `destructive` |
| `41.18` | `S0818` | `low-power-software-design` | Low-Power Software Design | `build` | `none` |
| `41.19` | `S0819` | `embedded-debugging` | Embedded Debugging | `analyze` | `none` |
| `41.20` | `S0820` | `device-fleet-management` | Device Fleet Management | `analyze` | `none` |

### Category 42: Game and Simulation Engineering (`game-and-simulation-engineering`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Runs obstacle-avoiding 2D A* pathfinding, fixed-timestep (60Hz) Entity-Component-System position integration, and deterministic replay digest checks.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill deterministic-simulation
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `42.01` | `S0821` | `game-engine-architecture` | Game Engine Architecture | `analyze` | `none` |
| `42.02` | `S0822` | `unity-game-builder` | Unity Game Builder | `build` | `none` |
| `42.03` | `S0823` | `unreal-engine-builder` | Unreal Engine Builder | `build` | `none` |
| `42.04` | `S0824` | `godot-game-builder` | Godot Game Builder | `build` | `none` |
| `42.05` | `S0825` | `2d-game-systems` | 2D Game Systems | `analyze` | `none` |
| `42.06` | `S0826` | `3d-game-systems` | 3D Game Systems | `analyze` | `none` |
| `42.07` | `S0827` | `gameplay-programming` | Gameplay Programming | `analyze` | `none` |
| `42.08` | `S0828` | `physics-simulation` | Physics Simulation | `analyze` | `none` |
| `42.09` | `S0829` | `animation-system` | Animation System | `analyze` | `none` |
| `42.10` | `S0830` | `ai-behavior-system` | AI Behavior System | `analyze` | `none` |
| `42.11` | `S0831` | `multiplayer-networking` | Multiplayer Networking | `analyze` | `none` |
| `42.12` | `S0832` | `game-save-system` | Game Save System | `analyze` | `none` |
| `42.13` | `S0833` | `procedural-generation` | Procedural Generation | `analyze` | `none` |
| `42.14` | `S0834` | `shader-programming` | Shader Programming | `analyze` | `none` |
| `42.15` | `S0835` | `rendering-pipeline` | Rendering Pipeline | `orchestrate` | `none` |
| `42.16` | `S0836` | `game-performance-optimization` | Game Performance Optimization | `optimize` | `none` |
| `42.17` | `S0837` | `game-tooling` | Game Tooling | `analyze` | `none` |
| `42.18` | `S0838` | `game-testing` | Game Testing | `verify` | `none` |
| `42.19` | `S0839` | `simulation-framework` | Simulation Framework | `analyze` | `none` |
| `42.20` | `S0840` | `deterministic-simulation` | Deterministic Simulation | `analyze` | `none` |

### Category 43: Blockchain and Decentralized Systems (`blockchain-and-decentralized-systems`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Constructs and verifies cryptographic SHA-256 Merkle inclusion proofs, screens smart contracts for tx.origin/reentrancy patterns, and computes BFT 3f+1 quorums.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill smart-contract-security-audit --param validators=10
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `43.01` | `S0841` | `blockchain-architecture` | Blockchain Architecture | `analyze` | `none` |
| `43.02` | `S0842` | `smart-contract-engineering` | Smart Contract Engineering | `analyze` | `none` |
| `43.03` | `S0843` | `solidity-engineering` | Solidity Engineering | `analyze` | `none` |
| `43.04` | `S0844` | `rust-smart-contract-engineering` | Rust Smart Contract Engineering | `analyze` | `none` |
| `43.05` | `S0845` | `smart-contract-testing` | Smart Contract Testing | `verify` | `none` |
| `43.06` | `S0846` | `smart-contract-security-audit` | Smart Contract Security Audit | `verify` | `security-sensitive` |
| `43.07` | `S0847` | `wallet-integration` | Wallet Integration | `analyze` | `none` |
| `43.08` | `S0848` | `token-standard-implementation` | Token Standard Implementation | `analyze` | `none` |
| `43.09` | `S0849` | `decentralized-identity` | Decentralized Identity | `analyze` | `none` |
| `43.10` | `S0850` | `consensus-protocol-analysis` | Consensus Protocol Analysis | `analyze` | `none` |
| `43.11` | `S0851` | `blockchain-indexer` | Blockchain Indexer | `analyze` | `none` |
| `43.12` | `S0852` | `oracle-integration` | Oracle Integration | `analyze` | `none` |
| `43.13` | `S0853` | `layer-2-integration` | Layer 2 Integration | `analyze` | `none` |
| `43.14` | `S0854` | `cross-chain-messaging` | Cross-Chain Messaging | `analyze` | `none` |
| `43.15` | `S0855` | `gas-optimization` | Gas Optimization | `optimize` | `financial` |
| `43.16` | `S0856` | `key-custody-design` | Key Custody Design | `build` | `credential` |
| `43.17` | `S0857` | `decentralized-storage` | Decentralized Storage | `analyze` | `none` |
| `43.18` | `S0858` | `blockchain-node-operations` | Blockchain Node Operations | `analyze` | `none` |
| `43.19` | `S0859` | `protocol-upgrade-planning` | Protocol Upgrade Planning | `transform` | `destructive` |
| `43.20` | `S0860` | `blockchain-incident-response` | Blockchain Incident Response | `analyze` | `security-sensitive` |

### Category 44: Enterprise Platforms and Integrations (`enterprise-platforms-and-integrations`)

- **Engine**: `software-engineering/tools-engine-systems-apps.py`
- **How It Works**: Verifies constant-time HMAC-SHA256 webhook signatures, generates idempotency keys, and defines secret-free environment-variable contracts for 20 enterprise platforms.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill stripe-integration --action-mode plan
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `44.01` | `S0861` | `microsoft-graph-integration` | Microsoft Graph Integration | `analyze` | `none` |
| `44.02` | `S0862` | `sharepoint-development` | SharePoint Development | `analyze` | `none` |
| `44.03` | `S0863` | `power-platform-development` | Power Platform Development | `analyze` | `none` |
| `44.04` | `S0864` | `salesforce-development` | Salesforce Development | `analyze` | `none` |
| `44.05` | `S0865` | `sap-integration` | SAP Integration | `analyze` | `none` |
| `44.06` | `S0866` | `servicenow-development` | ServiceNow Development | `analyze` | `none` |
| `44.07` | `S0867` | `oracle-erp-integration` | Oracle ERP Integration | `analyze` | `none` |
| `44.08` | `S0868` | `workday-integration` | Workday Integration | `analyze` | `none` |
| `44.09` | `S0869` | `microsoft-dynamics-365-integration` | Microsoft Dynamics 365 Integration | `analyze` | `none` |
| `44.10` | `S0870` | `google-workspace-integration` | Google Workspace Integration | `analyze` | `none` |
| `44.11` | `S0871` | `slack-app-development` | Slack App Development | `analyze` | `none` |
| `44.12` | `S0872` | `microsoft-teams-app-development` | Microsoft Teams App Development | `analyze` | `none` |
| `44.13` | `S0873` | `atlassian-jira-integration` | Atlassian Jira Integration | `analyze` | `none` |
| `44.14` | `S0874` | `confluence-integration` | Confluence Integration | `analyze` | `none` |
| `44.15` | `S0875` | `stripe-integration` | Stripe Integration | `analyze` | `financial` |
| `44.16` | `S0876` | `paypal-integration` | PayPal Integration | `analyze` | `financial` |
| `44.17` | `S0877` | `twilio-integration` | Twilio Integration | `analyze` | `none` |
| `44.18` | `S0878` | `sendgrid-integration` | SendGrid Integration | `analyze` | `none` |
| `44.19` | `S0879` | `electronic-signature-integration` | Electronic Signature Integration | `analyze` | `credential` |
| `44.20` | `S0880` | `enterprise-service-bus-integration` | Enterprise Service Bus Integration | `analyze` | `none` |

### Category 45: Scientific and Numerical Computing (`scientific-and-numerical-computing`)

- **Engine**: `software-engineering/tools-engine-data-ai.py`
- **How It Works**: Solves dense linear systems via Gaussian elimination with partial pivoting (verifying ||Ax - b||_inf < 1e-9), integrates ODEs with 4th-order Runge-Kutta, and runs seeded Monte Carlo.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill numerical-linear-algebra --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `45.01` | `S0881` | `numerical-linear-algebra` | Numerical Linear Algebra | `analyze` | `none` |
| `45.02` | `S0882` | `numerical-optimization` | Numerical Optimization | `optimize` | `none` |
| `45.03` | `S0883` | `differential-equation-solver` | Differential Equation Solver | `analyze` | `none` |
| `45.04` | `S0884` | `statistical-computing` | Statistical Computing | `analyze` | `none` |
| `45.05` | `S0885` | `signal-processing` | Signal Processing | `analyze` | `none` |
| `45.06` | `S0886` | `image-analysis` | Image Analysis | `analyze` | `none` |
| `45.07` | `S0887` | `computational-physics` | Computational Physics | `analyze` | `none` |
| `45.08` | `S0888` | `computational-chemistry` | Computational Chemistry | `analyze` | `none` |
| `45.09` | `S0889` | `bioinformatics-pipeline` | Bioinformatics Pipeline | `orchestrate` | `none` |
| `45.10` | `S0890` | `geospatial-computing` | Geospatial Computing | `analyze` | `none` |
| `45.11` | `S0891` | `monte-carlo-simulation` | Monte Carlo Simulation | `analyze` | `none` |
| `45.12` | `S0892` | `finite-element-method` | Finite Element Method | `analyze` | `none` |
| `45.13` | `S0893` | `finite-difference-method` | Finite Difference Method | `analyze` | `none` |
| `45.14` | `S0894` | `scientific-visualization` | Scientific Visualization | `analyze` | `none` |
| `45.15` | `S0895` | `high-performance-computing` | High-Performance Computing | `optimize` | `none` |
| `45.16` | `S0896` | `gpu-scientific-computing` | GPU Scientific Computing | `analyze` | `none` |
| `45.17` | `S0897` | `parallel-numerical-computing` | Parallel Numerical Computing | `analyze` | `none` |
| `45.18` | `S0898` | `reproducible-research-code` | Reproducible Research Code | `analyze` | `none` |
| `45.19` | `S0899` | `numerical-stability-analysis` | Numerical Stability Analysis | `analyze` | `none` |
| `45.20` | `S0900` | `scientific-validation` | Scientific Validation | `verify` | `none` |

### Category 46: Legacy Modernization and Migration (`legacy-modernization-and-migration`)

- **Engine**: `software-engineering/tools-engine-architecture.py`
- **How It Works**: Detects and modernizes legacy Python 2 syntax into AST-verified Python 3, converts structured data across JSON/CSV/XML, and plans phased Strangler-Fig cutovers.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill python-2-to-python-3-migration --text 'print xrange(5)'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `46.01` | `S0901` | `legacy-system-discovery` | Legacy System Discovery | `analyze` | `none` |
| `46.02` | `S0902` | `legacy-dependency-mapping` | Legacy Dependency Mapping | `analyze` | `none` |
| `46.03` | `S0903` | `strangler-fig-migration` | Strangler Fig Migration | `transform` | `none` |
| `46.04` | `S0904` | `database-migration` | Database Migration | `transform` | `irreversible` |
| `46.05` | `S0905` | `api-modernization` | API Modernization | `transform` | `none` |
| `46.06` | `S0906` | `ui-modernization` | UI Modernization | `transform` | `none` |
| `46.07` | `S0907` | `monolith-decomposition` | Monolith Decomposition | `analyze` | `none` |
| `46.08` | `S0908` | `mainframe-integration` | Mainframe Integration | `analyze` | `none` |
| `46.09` | `S0909` | `cobol-migration` | COBOL Migration | `transform` | `none` |
| `46.10` | `S0910` | `visual-basic-migration` | Visual Basic Migration | `transform` | `none` |
| `46.11` | `S0911` | `python-2-to-python-3-migration` | Python 2 to Python 3 Migration | `transform` | `none` |
| `46.12` | `S0912` | `java-version-upgrade` | Java Version Upgrade | `transform` | `none` |
| `46.13` | `S0913` | `dotnet-framework-to-modern-dotnet` | .NET Framework to Modern .NET | `analyze` | `none` |
| `46.14` | `S0914` | `angularjs-migration` | AngularJS Migration | `transform` | `none` |
| `46.15` | `S0915` | `react-version-upgrade` | React Version Upgrade | `transform` | `none` |
| `46.16` | `S0916` | `cloud-migration` | Cloud Migration | `transform` | `none` |
| `46.17` | `S0917` | `containerization-migration` | Containerization Migration | `transform` | `none` |
| `46.18` | `S0918` | `data-format-migration` | Data Format Migration | `transform` | `none` |
| `46.19` | `S0919` | `behavior-parity-testing` | Behavior Parity Testing | `verify` | `none` |
| `46.20` | `S0920` | `cutover-and-rollback-planning` | Cutover and Rollback Planning | `orchestrate` | `destructive` |

### Category 47: Release and Production Operations (`release-and-production-operations`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Generates CycloneDX 1.5 Software Bills of Materials (SBOM), computes SHA-256 provenance digests, and gates live environment promotion, deployment, signing, and rollback.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill software-bill-of-materials --project ledger-api
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `47.01` | `S0921` | `release-planning` | Release Planning | `analyze` | `none` |
| `47.02` | `S0922` | `release-candidate-build` | Release Candidate Build | `analyze` | `none` |
| `47.03` | `S0923` | `version-numbering` | Version Numbering | `analyze` | `none` |
| `47.04` | `S0924` | `release-notes-generation` | Release Notes Generation | `analyze` | `none` |
| `47.05` | `S0925` | `artifact-signing` | Artifact Signing | `analyze` | `credential` |
| `47.06` | `S0926` | `software-bill-of-materials` | Software Bill of Materials | `analyze` | `none` |
| `47.07` | `S0927` | `provenance-attestation` | Provenance Attestation | `analyze` | `none` |
| `47.08` | `S0928` | `environment-promotion` | Environment Promotion | `analyze` | `destructive` |
| `47.09` | `S0929` | `production-deployment` | Production Deployment | `orchestrate` | `destructive` |
| `47.10` | `S0930` | `database-release-coordination` | Database Release Coordination | `orchestrate` | `none` |
| `47.11` | `S0931` | `feature-flag-rollout` | Feature Flag Rollout | `analyze` | `none` |
| `47.12` | `S0932` | `canary-analysis` | Canary Analysis | `analyze` | `none` |
| `47.13` | `S0933` | `rollback-execution` | Rollback Execution | `orchestrate` | `irreversible` |
| `47.14` | `S0934` | `hotfix-engineering` | Hotfix Engineering | `analyze` | `none` |
| `47.15` | `S0935` | `production-verification` | Production Verification | `verify` | `none` |
| `47.16` | `S0936` | `operational-readiness-review` | Operational Readiness Review | `verify` | `none` |
| `47.17` | `S0937` | `change-management-record` | Change Management Record | `analyze` | `none` |
| `47.18` | `S0938` | `maintenance-window-planning` | Maintenance Window Planning | `analyze` | `none` |
| `47.19` | `S0939` | `end-of-life-planning` | End-of-Life Planning | `analyze` | `none` |
| `47.20` | `S0940` | `production-support-handoff` | Production Support Handoff | `analyze` | `none` |

### Category 48: Version and Documentation Currency (`version-and-documentation-currency`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Probes live host runtime versions against project targets and scans source code for deprecated Python and JavaScript APIs.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill runtime-version-matrix
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `48.01` | `S0941` | `official-documentation-discovery` | Official Documentation Discovery | `analyze` | `none` |
| `48.02` | `S0942` | `documentation-version-matching` | Documentation Version Matching | `analyze` | `none` |
| `48.03` | `S0943` | `release-note-tracking` | Release Note Tracking | `analyze` | `none` |
| `48.04` | `S0944` | `breaking-change-detection` | Breaking Change Detection | `verify` | `none` |
| `48.05` | `S0945` | `deprecation-tracking` | Deprecation Tracking | `analyze` | `none` |
| `48.06` | `S0946` | `end-of-support-detection` | End-of-Support Detection | `verify` | `none` |
| `48.07` | `S0947` | `compatibility-verification` | Compatibility Verification | `verify` | `none` |
| `48.08` | `S0948` | `runtime-version-matrix` | Runtime Version Matrix | `analyze` | `none` |
| `48.09` | `S0949` | `framework-version-matrix` | Framework Version Matrix | `analyze` | `none` |
| `48.10` | `S0950` | `dependency-freshness-review` | Dependency Freshness Review | `verify` | `none` |
| `48.11` | `S0951` | `security-advisory-tracking` | Security Advisory Tracking | `analyze` | `none` |
| `48.12` | `S0952` | `migration-path-discovery` | Migration Path Discovery | `transform` | `none` |
| `48.13` | `S0953` | `historical-documentation-retrieval` | Historical Documentation Retrieval | `analyze` | `none` |
| `48.14` | `S0954` | `archived-api-interpretation` | Archived API Interpretation | `analyze` | `none` |
| `48.15` | `S0955` | `latest-stable-version-verification` | Latest Stable Version Verification | `verify` | `none` |
| `48.16` | `S0956` | `prerelease-feature-isolation` | Prerelease Feature Isolation | `analyze` | `none` |
| `48.17` | `S0957` | `documentation-source-ranking` | Documentation Source Ranking | `analyze` | `none` |
| `48.18` | `S0958` | `example-code-validation` | Example Code Validation | `verify` | `none` |
| `48.19` | `S0959` | `version-pinned-guidance` | Version-Pinned Guidance | `analyze` | `none` |
| `48.20` | `S0960` | `documentation-evidence-register` | Documentation Evidence Register | `analyze` | `none` |

### Category 49: Large Codebase and Multi-File Delivery (`large-codebase-and-multi-file-delivery`)

- **Engine**: `software-engineering/tools-engine-architecture.py`
- **How It Works**: Scans multi-file codebases safely, partitions module graphs via Tarjan SCCs, scores unified diff blast radius, and validates clean deliverable archives.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill dependency-graph-partitioning --input software-engineering
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `49.01` | `S0961` | `large-repository-planning` | Large Repository Planning | `analyze` | `none` |
| `49.02` | `S0962` | `multi-file-code-generation` | Multi-File Code Generation | `analyze` | `none` |
| `49.03` | `S0963` | `repository-wide-refactoring` | Repository-Wide Refactoring | `transform` | `none` |
| `49.04` | `S0964` | `cross-module-interface-management` | Cross-Module Interface Management | `analyze` | `none` |
| `49.05` | `S0965` | `dependency-graph-partitioning` | Dependency Graph Partitioning | `analyze` | `none` |
| `49.06` | `S0966` | `parallel-change-coordination` | Parallel Change Coordination | `orchestrate` | `none` |
| `49.07` | `S0967` | `generated-file-governance` | Generated File Governance | `analyze` | `none` |
| `49.08` | `S0968` | `incremental-compilation-strategy` | Incremental Compilation Strategy | `analyze` | `none` |
| `49.09` | `S0969` | `large-test-suite-optimization` | Large Test Suite Optimization | `optimize` | `none` |
| `49.10` | `S0970` | `monorepo-ownership-mapping` | Monorepo Ownership Mapping | `analyze` | `none` |
| `49.11` | `S0971` | `multi-package-release` | Multi-Package Release | `analyze` | `publishing` |
| `49.12` | `S0972` | `cross-repository-change` | Cross-Repository Change | `analyze` | `none` |
| `49.13` | `S0973` | `schema-and-client-synchronization` | Schema and Client Synchronization | `analyze` | `none` |
| `49.14` | `S0974` | `database-and-application-coordination` | Database and Application Coordination | `orchestrate` | `none` |
| `49.15` | `S0975` | `configuration-matrix-management` | Configuration Matrix Management | `analyze` | `none` |
| `49.16` | `S0976` | `large-diff-review` | Large Diff Review | `verify` | `none` |
| `49.17` | `S0977` | `change-set-chunking` | Change Set Chunking | `analyze` | `none` |
| `49.18` | `S0978` | `integration-branch-management` | Integration Branch Management | `analyze` | `none` |
| `49.19` | `S0979` | `repository-wide-validation` | Repository-Wide Validation | `verify` | `none` |
| `49.20` | `S0980` | `clean-deliverable-packaging` | Clean Deliverable Packaging | `analyze` | `none` |

### Category 50: Correctness, Safety, and Verification (`correctness-safety-and-verification`)

- **Engine**: `software-engineering/tools-engine-operations-quality.py`
- **How It Works**: Verifies specification-to-test traceability completeness, multi-run SHA-256 execution determinism, clean-environment execution, and final ZIP reopen integrity.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill specification-to-test-traceability --input software-engineering/asset-sample-fixture.json
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `50.01` | `S0981` | `specification-to-test-traceability` | Specification-to-Test Traceability | `analyze` | `none` |
| `50.02` | `S0982` | `static-type-verification` | Static Type Verification | `verify` | `none` |
| `50.03` | `S0983` | `formal-specification` | Formal Specification | `analyze` | `none` |
| `50.04` | `S0984` | `model-checking` | Model Checking | `verify` | `none` |
| `50.05` | `S0985` | `invariant-design` | Invariant Design | `build` | `none` |
| `50.06` | `S0986` | `property-verification` | Property Verification | `verify` | `none` |
| `50.07` | `S0987` | `proof-assisted-programming` | Proof-Assisted Programming | `verify` | `none` |
| `50.08` | `S0988` | `boundary-value-testing` | Boundary Value Testing | `verify` | `none` |
| `50.09` | `S0989` | `error-path-verification` | Error Path Verification | `verify` | `none` |
| `50.10` | `S0990` | `determinism-verification` | Determinism Verification | `verify` | `none` |
| `50.11` | `S0991` | `concurrency-correctness` | Concurrency Correctness | `analyze` | `none` |
| `50.12` | `S0992` | `numerical-correctness` | Numerical Correctness | `analyze` | `none` |
| `50.13` | `S0993` | `security-correctness` | Security Correctness | `analyze` | `none` |
| `50.14` | `S0994` | `data-integrity-verification` | Data Integrity Verification | `verify` | `none` |
| `50.15` | `S0995` | `migration-correctness` | Migration Correctness | `transform` | `none` |
| `50.16` | `S0996` | `backward-compatibility-verification` | Backward Compatibility Verification | `verify` | `none` |
| `50.17` | `S0997` | `reproducible-execution` | Reproducible Execution | `analyze` | `none` |
| `50.18` | `S0998` | `clean-environment-verification` | Clean Environment Verification | `verify` | `none` |
| `50.19` | `S0999` | `final-artifact-reopen-test` | Final Artifact Reopen Test | `analyze` | `none` |
| `50.20` | `S1000` | `known-limitation-disclosure` | Known Limitation Disclosure | `analyze` | `none` |

### Category 51: Coding Agent Orchestration (`coding-agent-orchestration`)

- **Engine**: `software-engineering/tools-engine-architecture.py`
- **How It Works**: Orchestrates multi-agent engineering workflows: Task Router, Tool Capability Discovery, DAG topological scheduling, bounded parallel workers, 3-way conflict reconciliation, and Human Approval Gates.
- **Example (`bash`)**:
```bash
python3 software-engineering/tools-workbench.py run --skill task-router --text 'audit SQL queries and generate an OpenAPI contract'
```

| Coord | ID | Slug | Skill Name | Mode | Approval Scope on Apply |
|---|---|---|---|---|---|
| `51.01` | `S1001` | `task-router` | Task Router | `orchestrate` | `none` |
| `51.02` | `S1002` | `tool-capability-discovery` | Tool Capability Discovery | `analyze` | `none` |
| `51.03` | `S1003` | `repository-inspection-agent` | Repository Inspection Agent | `orchestrate` | `none` |
| `51.04` | `S1004` | `research-agent` | Research Agent | `orchestrate` | `none` |
| `51.05` | `S1005` | `implementation-agent` | Implementation Agent | `orchestrate` | `none` |
| `51.06` | `S1006` | `test-agent` | Test Agent | `orchestrate` | `none` |
| `51.07` | `S1007` | `review-agent` | Review Agent | `verify` | `none` |
| `51.08` | `S1008` | `security-agent` | Security Agent | `orchestrate` | `none` |
| `51.09` | `S1009` | `performance-agent` | Performance Agent | `optimize` | `none` |
| `51.10` | `S1010` | `documentation-agent` | Documentation Agent | `orchestrate` | `none` |
| `51.11` | `S1011` | `release-agent` | Release Agent | `orchestrate` | `none` |
| `51.12` | `S1012` | `dependency-controlled-execution` | Dependency-Controlled Execution | `analyze` | `none` |
| `51.13` | `S1013` | `state-handoff` | State Handoff | `analyze` | `none` |
| `51.14` | `S1014` | `parallel-work-coordination` | Parallel Work Coordination | `orchestrate` | `none` |
| `51.15` | `S1015` | `conflict-reconciliation` | Conflict Reconciliation | `analyze` | `none` |
| `51.16` | `S1016` | `focused-repair-loop` | Focused Repair Loop | `analyze` | `none` |
| `51.17` | `S1017` | `verification-gate` | Verification Gate | `verify` | `none` |
| `51.18` | `S1018` | `human-approval-gate` | Human Approval Gate | `verify` | `security-sensitive` |
| `51.19` | `S1019` | `reusable-workbench` | Reusable Workbench | `analyze` | `none` |
| `51.20` | `S1020` | `final-delivery-orchestrator` | Final Delivery Orchestrator | `analyze` | `none` |

## 7. Validation and Tests Completed

All checks below were executed against the actual packaged files in this environment (`Linux x86_64`, `Python 3.13.16`, `Node.js v20.20.2`, `GCC/G++ 14.2.0`, `OpenJDK javac 11`, `GNU bash 5.2.37`, `SQLite 3.46.1`):

1. **Help, Launcher, and Capability Listing**: Verified `python3 software-engineering/tools-workbench.py --help`, `capabilities --summary` (`51` categories, `1020` skills), `capabilities --requires-approval` (`48` skills), and `bash software-engineering/tools-run.sh environment`.
2. **Full 1,020-Skill Execution Sweep**: Executed every category (`--category 1` through `--category 51`, running all `1,020` individual skills) through the real CLI with `0` unhandled errors and `100%` schema-valid JSON envelopes.
3. **Plain Text, Markdown, and Copied HTML Input Parsing**: Verified `parse-input --execute` on plain text, Markdown tables/headings, and nested HTML containing CSS classes and `data-*` attributes.
4. **Input Mutation Sensitivity**:
   - Compiler/VM (`33.09` `virtual-machine-builder`): `let x = 5 + 3; x * 10` produced `vm_result=80`; changing input to `let x = 20 - 6; x * 3` changed `vm_result` to `42`.
   - SAST (`30.04` `static-application-security-testing`): Unsafe `subprocess.run(..., shell=True)` + `eval(...)` returned `SEC-SAST-001: failed`; clean typed Python returned `SEC-SAST-001: passed` (`0` findings).
   - WCAG Contrast (`15.19` `frontend-accessibility`): `#0f172a` on `#ffffff` passed (`17.85:1`); `#cccccc` on `#ffffff` failed (`1.61:1 < 4.5:1`).
5. **Invalid/Missing Input, Confinement, Approval Gate, and Recovery Path**:
   - Missing input path returned `INPUT_NOT_FOUND` (`inputs_validated=false`, `rc=2`).
   - Malformed JSON input returned `INVALID_INPUT_JSON` (`rc=2`).
   - Protected system output path returned `FORBIDDEN_OUTPUT_PATH` (`rc=3`).
   - Unapproved `production-deployment --action-mode apply` blocked with `status="approval_required"` (`rc=4`); adding `--approve --approval-reason "..."` succeeded with `approval.granted=true`.
   - Workflow interruption (`--workflow greenfield-service-delivery --simulate-interrupt-after adr`, `rc=5`) persisted `checkpoint-state.json` after 2 steps, and `recover --checkpoint ...` resumed the remaining 4 steps (`api`, `db`, `security`, `dod`) returning `status="recovered"`.
6. **Artifact Inspection, Clean Skill-Root Verification, and Final ZIP Reopen**:
   - Opened, parsed, and verified generated `adr-0001-baseline.md`, `openapi-3.1.json`, `sbom-cyclonedx.json`, `media-computer-graphics-pipeline.svg`, and `migrated-python3.py`.
   - Copied `software-engineering/` beside a root `SKILL.md` in a clean temporary directory and verified relative-path execution, case-sensitive link resolution (`documentation-link-validation`), zero internal subfolders, and final ZIP extraction/reopen integrity (`software-engineering-skill-tool.zip`).

## 8. Known Limitations, Unavailable Integrations, and Platform Caveats

- **Absent Local Compilers/SDKs in Build Environment**: `go`, `rustc`, `cargo`, `dotnet`, `clang`, `php`, `ruby`, `lua`, `docker`, `kubectl`, `terraform`, Android SDK, and Xcode are not installed in this Linux container. Skills targeting those ecosystems perform static contract, syntax-balance, security, and architectural verification and explicitly disclose missing host binaries rather than claiming live binary compilation.
- **Offline-By-Default Cloud, Blockchain, and SaaS Integrations**: Live calls to AWS, Azure, Google Cloud, Kubernetes clusters, blockchain RPC nodes, and enterprise SaaS APIs (Microsoft Graph, SharePoint, Salesforce, SAP, ServiceNow, Stripe, PayPal, Twilio, SendGrid, Slack, Jira) are disabled offline (`network_execution_enabled: false`) and require user-provided environment credentials and endpoints when integrated into a live deployment pipeline.
- **Hosted Platform & Catalog Caveat**: Local relative paths (such as `software-engineering/tools-workbench.py` and `software-engineering/asset-default-config.json`) are verified from a clean skill root. If a resource is later inaccessible inside a hosted agent catalog or sandboxed web viewer, treat that behavior as a hosting, catalog, permission, or platform constraint rather than a local package path defect; no local package can override external platform isolation rules.

## 9. Troubleshooting, Recovery, Extension, and Maintenance

- **Ambiguous Skill Name (`AMBIGUOUS_SKILL_NAME`)**: Three titles appear in two categories (`Mobile Performance Profiling`, `Architecture Documentation`, `Distributed Tracing`). Pass the unique slug (for example `--skill network-distributed-tracing`), coordinate (`--skill 29.18`), `--category 29`, or `--all-matches`.
- **Approval Blocked (`APPROVAL_REQUIRED`)**: Use `--action-mode plan` (default) for dry-run verification and artifact synthesis, or pass `--approve --approval-reason "<ticket-or-justification>"` after human review.
- **Interrupted Workflow Recovery**: Locate `checkpoint-state.json` inside your `--output` folder and run `python3 software-engineering/tools-workbench.py recover --checkpoint <dir>/checkpoint-state.json --output <dir>`.
- **Extending Capabilities**: Add new skill entries to `software-engineering/asset-skill-catalog.json` (preserving `^[a-z0-9]+(?:-[a-z0-9]+)*$` slug uniqueness), bind `"engine"` to one of the five domain engine modules (`software-engineering/tools-engine-architecture.py`, `software-engineering/tools-engine-languages.py`, `software-engineering/tools-engine-data-ai.py`, `software-engineering/tools-engine-systems-apps.py`, or `software-engineering/tools-engine-operations-quality.py`), and run `python3 software-engineering/tools-workbench.py selftest` plus `documentation-link-validation` before repackaging.
