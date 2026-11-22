# Data, Web, App, Game, File, and Interactive Systems Skill Tool (`data-web-app-interaction`)

## 1. Purpose and Architecture Overview

`data-web-app-interaction` is a unified, self-contained, production Skill Tool component implementing all **33 categories** and **330 capabilities** from the *Data, Web, App, Game, File, and Interactive Systems Skill Catalog*. It provides authorized read-only-by-default inspection, structured extraction, data cleaning, statistical analysis, discrete/Monte Carlo/agent-based simulation, web/API/network/media/document/geospatial processing, accessibility and defensive security auditing, and checkpointed multi-step workflow orchestration.

This folder is a reusable Skill Tool component designed to sit directly beside a parent package's root `SKILL.md` without modification. Every internal reference uses exact case-sensitive forward-slash paths relative to the future skill root (for example, `data-web-app-interaction/asset-default-config.json`).

### Operating Principles Enforced by Default
- Observe before acting. Default to read-only inspection and use safe copies or test environments.
- Require clear authorization for accounts, private data, uploads, messages, transactions, modifications, automation, and security testing.
- Treat CAPTCHAs, identity verification, legal acceptance, payment approval, and ambiguous permissions as human handoff points.
- Use APIs and supported exports when they are more reliable, lawful, and respectful than interface automation or scraping.
- Bound searches, crawls, loops, downloads, uploads, retries, game exploration, and tests with stop conditions and rate limits.
- Validate through the real use path: reopen files, replay workflows, compare expected and actual state, and preserve evidence without secrets.

### Tool Architecture, Roles, and Module Loading Order

All code files use `sys.dont_write_bytecode = True` prior to loading sibling modules so running the Skill Tool never pollutes `data-web-app-interaction/` with `__pycache__` subdirectories.

1. **`data-web-app-interaction/tools-cli.py` (Primary Runnable Entry Point — Loaded 1st)**: Parses CLI subcommands (`capabilities`, `inspect-skill`, `parse-catalog`, `run`, `workflow`, `verify-all`), validates CLI arguments, invokes pre-execution path/policy/approval/human-handoff checks, dispatches handlers within timeout/retry bounds, scrubs output secrets, and writes structured JSON or Markdown envelopes.
2. **`data-web-app-interaction/tools-core.py` (Shared Runtime Kernel — Loaded 2nd)**: Implements relative resource resolution, dynamic hyphenated module loading, configuration merging, `SecretRedactor`, `enforce_url_boundary`, `enforce_approval_gate`, `check_human_handoff_triggers`, `RateLimiter`, `run_bounded`, hash-chained `ActionLedger`, atomic `CheckpointStore`, pure-stdlib `SimpleDOMParser` (with `colspan`/`rowspan` table normalization), and multi-format `load_structured_records` (CSV, TSV, JSON, JSONL, GeoJSON, XML, SQLite).
3. **`data-web-app-interaction/tools-catalog-parser.py` (Catalog Parser & Registry — Loaded 3rd)**: Strips HTML tags, CSS classes, `data-*` attributes, numbering marks, and Markdown formatting from plain text, Markdown, or copied HTML specifications to extract categories, skill names, descriptions, and umbrella slugs; indexes `data-web-app-interaction/asset-skill-catalog.json` via `SkillRegistry`.
4. **`data-web-app-interaction/tools-governance-search.py` (Domain Module — Loaded on Demand / 4th)**: Implements Categories 1–2 (20 capabilities: authorization gatekeeping, read-only controller, confirmation digests, secret/entropy redaction, SSRF/domain boundaries, `robots.txt` reviewer, PII minimization, sandbox planning, human-handoff escalation, epistemic reporting, BM25-like search, research planning, domain restriction, freshness checks, multilingual normalization, entity disambiguation, authority scoring, URL/shingle deduplication, archive diffs, and gap detection).
5. **`data-web-app-interaction/tools-web-api-infra.py` (Domain Module — Loaded on Demand / 5th)**: Implements Categories 3–10 (80 capabilities: DOM reading, scroll checkpointing, reader-mode noise filtering, WCAG contrast/heading/label checks, form mapping, wizard navigation, session cookie auditing, upload/download verification, table normalization, REST/GraphQL/SOAP/Webhook/RSS/Atom/OpenAPI operations, GitHub repository/diff/Actions analysis, Cloudflare/DNS/TLS/CDN/redirect diagnostics, cloud storage sync/ACL/integrity auditing, and social thread/moderation/draft workflows).
6. **`data-web-app-interaction/tools-media-docs-files.py` (Domain Module — Loaded on Demand / 6th)**: Implements Categories 11–13 (30 capabilities: WAV PCM signal/RMS/clipping analysis, WebVTT/SRT transcript cleanup and subtitle QA, HLS `.m3u8` inspection, A/V drift checks, OOXML `.docx`/`.xlsx`/`.pptx` + HTML/PDF/SVG/PNG/GIF inspection, Zip-Slip and Zip-Bomb protected archive inventory, magic-byte format identification, cross-format conversion, SQLite FTS5 full-text indexing, POSIX permission auditing, and reversible batch file operations).
7. **`data-web-app-interaction/tools-data-science-sim.py` (Domain Module — Loaded on Demand / 7th)**: Implements Categories 14–17 (40 capabilities: schema/dictionary inference, timezone/currency normalization, cardinality-checked relational joins, deduplication/imputation, k-anonymity & PII redaction, descriptive statistics, robust MAD anomaly detection, funnels, cohorts, seeded Monte Carlo simulation, `heapq` Discrete-Event queueing simulation, Agent-Based simulation, watermarked synthetic data generation, What-If sweeps, and RMSE/MAE calibration).
8. **`data-web-app-interaction/tools-apps-games-qa.py` (Domain Module — Loaded on Demand / 8th)**: Implements Categories 18–24 (70 capabilities: desktop/mobile UI focus, touch-target >=44dp & viewport overflow checks, ANR/crash parsing, quest graph BFS soft-lock/reachability testing, game economy faucet-sink analysis, frame-time FPS/1%-low profiling, projectile tunneling math, localization placeholder QA, Core Web Vitals checks, delta-debugging step reduction, resilient Playwright/semantic selector engineering, flakiness diagnostics, and crawl safety auditing).
9. **`data-web-app-interaction/tools-net-sec-ops.py` (Domain Module — Loaded on Demand / 9th)**: Implements Categories 25–33 (90 capabilities: HTTP/HAR/WebSocket/binary `.pcap` flow parsing, traffic privacy auditing, WCAG 2.2 AA/AAA contrast & voice-control `2.5.3` checks, saturation/soak/latency breakdown & live process resource monitoring, defensive RBAC/header/supply-chain security review, noise-filtered change watchers & alert deduplication, RFC-5322 email/chat/attachment safety & confirmation gating, sandbox cart/coupon/receipt/financial gatekeeping, WGS84 Haversine routing/GeoJSON/KML/GPX track playback & geoprivacy coarsening, and end-to-end DAG decomposition, hash-chained ledgers, checkpoints, provenance packaging, and deliverable curation).

### Asset Files and Operational Purposes

- `data-web-app-interaction/asset-skill-catalog.json`: Canonical machine-readable registry of all 33 categories and 330 capabilities with IDs (`cat01-01`..`cat33-10`), slugs, descriptions, risk tiers, approval flags, and handler mappings.
- `data-web-app-interaction/asset-default-config.json`: Default portable configuration specifying relative asset paths, timeouts, retry/backoff settings, rate limits, archive safety caps, and configurable external provider slots.
- `data-web-app-interaction/asset-safety-policy.json`: Enforceable safety policy defining read-only vs consequential verbs, human-handoff triggers, blocked private IP CIDRs, regex secret/PII redaction rules, and sandbox isolation baselines.
- `data-web-app-interaction/asset-io-schemas.json`: JSON Schema (Draft 2020-12) definitions for `SkillInvocationRequest`, `SkillResultEnvelope`, `WorkflowDefinition`, and hash-chained `LedgerEntry` records.
- `data-web-app-interaction/asset-prompt-templates.json`: Structured prompt templates for optional LLM/agent synthesis across research decomposition, consequential-action confirmation, accessibility defects, defensive security findings, and communication drafts.
- `data-web-app-interaction/asset-sample-page.html`: Multi-section HTML5 document fixture (landmarks, navigation, pricing table, form controls, metadata, CSS styles) for web, accessibility, extraction, and QA capabilities.
- `data-web-app-interaction/asset-sample-records.csv`: Multi-column tabular telemetry and commerce fixture (cohorts, timestamps, regions, funnel stages, latencies, duplicates, missing values) for data cleaning, statistical analysis, and monitoring capabilities.
- `data-web-app-interaction/asset-sample-events.jsonl`: Multi-service structured event, HTTP status, rate-limit, incident, and collaboration channel log stream for API, debugging, observability, and messaging capabilities.
- `data-web-app-interaction/asset-sample-spatial.geojson`: CRS84 GeoJSON `FeatureCollection` containing transit hubs, a multi-waypoint GPS `LineString` with timestamps/elevations, and a closed corridor `Polygon` for geospatial capabilities.

## 2. Setup, Runtime Requirements, and Dependencies

### Required Runtime
- **Python**: `>= 3.10` (implemented and verified on `Python 3.13.16`).
- **Core Dependencies**: **None required** beyond the Python 3 Standard Library (`argparse`, `csv`, `datetime`, `difflib`, `email`, `fnmatch`, `hashlib`, `heapq`, `hmac`, `html.parser`, `importlib`, `ipaddress`, `json`, `math`, `os`, `pathlib`, `random`, `re`, `resource`, `shutil`, `socket`, `sqlite3`, `ssl`, `stat`, `statistics`, `struct`, `subprocess`, `tarfile`, `tempfile`, `time`, `unicodedata`, `urllib`, `wave`, `xml.etree.ElementTree`, `zipfile`, `zoneinfo`).

### Optional Third-Party Accelerators
All 330 capabilities run without third-party packages. When inspecting rich PDF text streams externally, optional `pypdf` is auto-detected by `universal-document-reader` if installed:
```bash
bash -c 'python3 -m pip install "pypdf>=4.0,<6.0"'
```
System OCR (`tesseract >= 5.0`) is optional for `scanned-document-ocr-reader` when reading raster scan images directly.

### Core CLI Commands (Run from Future Skill Root)
```bash
# 1. Show primary entry-point help and subcommand help
bash -c 'python3 data-web-app-interaction/tools-cli.py --help'
bash -c 'python3 data-web-app-interaction/tools-cli.py run --help'

# 2. List all 33 categories and 330 capabilities (JSON, table, or Markdown)
bash -c 'python3 data-web-app-interaction/tools-cli.py capabilities --format table'
bash -c 'python3 data-web-app-interaction/tools-cli.py capabilities --category 16 --format json'
bash -c 'python3 data-web-app-interaction/tools-cli.py capabilities --search accessibility --format markdown'

# 3. Inspect metadata and invocation details for any capability
bash -c 'python3 data-web-app-interaction/tools-cli.py inspect-skill --skill cat01-01'

# 4. Parse plain text, Markdown, or copied HTML skill catalogs
bash -c 'python3 data-web-app-interaction/tools-cli.py parse-catalog --text "Category 1: Demo\n1. Skill Name: Demo Reader\nDescription: Reads authorized files."'

# 5. Run a capability with configurable input, parameters, config, and output path
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-cleaning-specialist --input data-web-app-interaction/asset-sample-records.csv --config data-web-app-interaction/asset-default-config.json'

# 6. Run a multi-step resumable workflow with checkpoint and hash-chained ledger
bash -c 'python3 data-web-app-interaction/tools-cli.py workflow --steps-json "{\"workflow_name\":\"audit-pipeline\",\"steps\":[{\"step_id\":\"s1\",\"skill\":\"webpage-structure-reader\"},{\"step_id\":\"s2\",\"skill\":\"data-cleaning-specialist\"}]}"'

# 7. Run deterministic smoke verification across all 330 capabilities
bash -c 'python3 data-web-app-interaction/tools-cli.py verify-all'
```

## 3. Inputs, Outputs, Parameters, Schemas, and Configuration

### CLI Parameters for `run`
- `--skill` *(required)*: Capability identifier (`cat01-01`..`cat33-10`), hyphenated slug (`webpage-structure-reader`), or exact case-insensitive Skill Name.
- `--input` *(optional)*: Relative or authorized file/directory path. Validated for existence, size (`<= execution.max_file_bytes`), NUL bytes, and parent-directory traversal (`..` forbidden).
- `--output` *(optional)*: Destination path where structured JSON or Markdown output is written.
- `--config` *(optional)*: Custom JSON configuration file merged cleanly over `data-web-app-interaction/asset-default-config.json`.
- `--params` / `--params-file` *(optional)*: Capability-specific JSON object parameters.
- `--approve` & `--approval-note` *(optional)*: Explicit human confirmation flag and scope note required for mutating, publishing, account, or financial actions.
- `--dry-run` *(optional)*: Performs input validation, human-handoff checks, and approval checks without invoking the capability handler.
- `--timeout` & `--max-retries` *(optional)*: Bounded execution timeout (capped at `max_timeout_seconds=300`) and exponential-backoff retry count.
- `--checkpoint-path` & `--idempotency-key` *(optional)*: Atomic JSON checkpoint state and idempotency key preventing duplicate execution of completed or consequential steps.
- `--ledger-path` *(optional)*: Append-only SHA-256 hash-chained JSONL audit log path.

### Structured Output Envelope (`SkillResultEnvelope`)
Every execution returns a consistent JSON object matching `data-web-app-interaction/asset-io-schemas.json`:
- `ok` (`boolean`): `true` when `status == "completed"`, `false` on validation, policy, handoff, approval, or runtime errors.
- `status` (`string`): `completed`, `validation_error` (exit code `2`), `approval_required` (exit code `3`), `human_handoff_required` (exit code `3`), `blocked_by_policy` (exit code `3`), or `failed` (exit code `1`).
- `skill_id`, `slug`, `skill_name`, `category_number`, `category_name`: Canonical capability identifiers.
- `observation_mode`: Honest epistemic tag (`directly_observed`, `tested`, `simulated`, `inferred`, `blocked`, or `unverified`).
- `data` & `metrics`: Capability-specific structured output (automatically scrubbed by `SecretRedactor`).
- `warnings`, `limitations`, `redactions_applied`, `provenance`: Operational caveats, redaction count, SHA-256 input digest, config path, module name, and Python version.

## 4. Safety, Privacy, Secret Redaction, and Approval Rules

1. **Read-Only Default**: All 330 capabilities operate in non-destructive inspection, preview, testing, or simulation mode unless an explicit mutation flag (`apply`, `publish`, `send`, `commit_transaction`, `confirm_booking`, `execute_mutation`) is passed.
2. **Mandatory Approval Gate**: Consequential, destructive, publishing, account-modifying, and financial operations raise `approval_required` (exit code `3`) unless `--approve` is explicitly supplied by an authorized operator.
3. **Human Handoff Escalation**: Active CAPTCHAs (`g-recaptcha`, `hcaptcha`, `cf-turnstile`), MFA challenges, identity checks, legal agreement acceptances, and payment confirmations immediately pause automation with `human_handoff_required` (exit code `3`).
4. **Secret and Credential Protection**: Bearer tokens, JWTs, PEM private keys, cloud access keys (`AKIA*`), VCS tokens (`ghp_*`), password/cookie/secret assignments, and Luhn-valid payment card numbers are automatically redacted before output serialization. Zero credentials or private endpoints are embedded in this Skill Tool.
5. **Target & Archive Boundaries**: Parent traversal (`..`), RFC1918/loopback SSRF URLs, Zip-Slip archive paths, and Zip-Bomb ratios (`> 100:1` or `> 100 MB` uncompressed) are blocked by policy.

## 5. Complete Capability Reference and Examples (All 33 Categories, 330 Capabilities)

### Category 1: Authorization, Scope, and Safe Operation
- **Engine Module**: `data-web-app-interaction/tools-governance-search.py` | **Category ID**: `cat01`

#### cat01-01 — Action Authorization Gatekeeper (`action-authorization-gatekeeper`)
- **Capability**: Confirms ownership, permission, target, account, environment, and allowed actions before interacting with websites, apps, games, devices, files, or services.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill action-authorization-gatekeeper --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-02 — Read-Only Exploration Controller (`read-only-exploration-controller`)
- **Capability**: Defaults to observation, search, scrolling, inspection, and reversible navigation until a modifying action is explicitly requested and permitted.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill read-only-exploration-controller --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-03 — Consequential Action Confirmation Manager (`consequential-action-confirmation-manager`)
- **Capability**: Requires clear confirmation before purchases, publishing, deletion, account changes, messages, uploads, submissions, or irreversible operations.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill consequential-action-confirmation-manager --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-04 — Credential and Secret Handling Guardian (`credential-and-secret-handling-guardian`)
- **Capability**: Uses authorized authentication without exposing passwords, tokens, cookies, private keys, recovery codes, or session data in outputs or logs.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill credential-and-secret-handling-guardian --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-05 — Target Boundary Enforcer (`target-boundary-enforcer`)
- **Capability**: Restricts automation and testing to declared domains, applications, files, accounts, environments, and rate limits.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill target-boundary-enforcer --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-06 — Terms, Robots, and Access Policy Reviewer (`terms-robots-and-access-policy-reviewer`)
- **Capability**: Checks applicable terms, robots directives, API policies, licensing, and technical access constraints before large-scale collection or automation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill terms-robots-and-access-policy-reviewer --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-07 — Privacy and Personal Data Minimizer (`privacy-and-personal-data-minimizer`)
- **Capability**: Collects only necessary personal data, applies consent and purpose limits, and supports redaction, retention, and deletion requirements.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill privacy-and-personal-data-minimizer --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-08 — Safe Sandbox Execution Planner (`safe-sandbox-execution-planner`)
- **Capability**: Isolates untrusted files, applications, games, scripts, and downloads with snapshots, restricted permissions, and controlled network access.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill safe-sandbox-execution-planner --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-09 — Human Handoff and Escalation Controller (`human-handoff-and-escalation-controller`)
- **Capability**: Stops and requests human action when identity checks, CAPTCHAs, legal acceptance, financial approval, physical control, or ambiguous authorization is required.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill human-handoff-and-escalation-controller --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

#### cat01-10 — Capability and Limitation Reporter (`capability-and-limitation-reporter`)
- **Capability**: States what was directly observed, tested, simulated, inferred, blocked, or left unverified without claiming unsupported access or completion.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill capability-and-limitation-reporter --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","owner_confirmed":true}'\'''
```

### Category 2: Web Search and Discovery
- **Engine Module**: `data-web-app-interaction/tools-governance-search.py` | **Category ID**: `cat02`

#### cat02-01 — General Web Search Specialist (`general-web-search-specialist`)
- **Capability**: Builds precise queries, compares results, opens relevant sources, and answers with traceable distinctions between evidence and interpretation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill general-web-search-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-02 — Deep Web Research Planner (`deep-web-research-planner`)
- **Capability**: Decomposes broad questions into nonredundant searches, authoritative source types, freshness needs, and explicit verification criteria.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill deep-web-research-planner --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-03 — Domain-Restricted Searcher (`domain-restricted-searcher`)
- **Capability**: Searches within specified sites, organizations, repositories, documentation portals, journals, or government domains.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill domain-restricted-searcher --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-04 — Current Information Verifier (`current-information-verifier`)
- **Capability**: Checks dates, versions, status, availability, ownership, and primary sources for time-sensitive claims.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill current-information-verifier --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-05 — Multilingual Web Searcher (`multilingual-web-searcher`)
- **Capability**: Searches across languages using translated terms, local terminology, transliteration, and regional sources while preserving meaning.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill multilingual-web-searcher --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-06 — Entity Disambiguation Researcher (`entity-disambiguation-researcher`)
- **Capability**: Separates people, companies, products, places, projects, and similarly named artifacts before collecting data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill entity-disambiguation-researcher --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-07 — Source Authority Evaluator (`source-authority-evaluator`)
- **Capability**: Ranks primary, official, peer-reviewed, reputable secondary, community, and unverified sources according to the question.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill source-authority-evaluator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-08 — Search Result Deduplicator (`search-result-deduplicator`)
- **Capability**: Clusters mirrors, syndications, reposts, parameter variants, and near-duplicate pages to avoid counting the same evidence repeatedly.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill search-result-deduplicator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-09 — Archived Web Discovery Specialist (`archived-web-discovery-specialist`)
- **Capability**: Uses lawful archives, cached copies, release histories, and preserved snapshots to investigate changed or removed public pages.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill archived-web-discovery-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

#### cat02-10 — Research Gap Detector (`research-gap-detector`)
- **Capability**: Identifies unanswered questions, conflicting claims, missing primary evidence, and the next minimum search needed to resolve them.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill research-gap-detector --input data-web-app-interaction/asset-sample-records.csv --params '\''{"query":"onboarding sandbox pagination"}'\'''
```

### Category 3: Webpage Reading, Scrolling, and Navigation
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat03`

#### cat03-01 — Webpage Structure Reader (`webpage-structure-reader`)
- **Capability**: Identifies headings, navigation, main content, sidebars, tables, images, forms, footers, dialogs, and interactive controls.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill webpage-structure-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-02 — Long-Page Scroll Navigator (`long-page-scroll-navigator`)
- **Capability**: Traverses long or infinite-scroll pages with checkpoints, duplicate detection, loading-state handling, and stop conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill long-page-scroll-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-03 — Article and Blog Reader (`article-and-blog-reader`)
- **Capability**: Extracts thesis, evidence, examples, dates, authorship, links, caveats, and conclusions from public articles.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill article-and-blog-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-04 — Documentation Site Navigator (`documentation-site-navigator`)
- **Capability**: Traverses versioned manuals, sidebars, anchors, examples, references, and related pages to find exact instructions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill documentation-site-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-05 — Table and List Extractor (`table-and-list-extractor`)
- **Capability**: Captures visible tabular or repeated page data with headers, units, pagination, filters, and provenance.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill table-and-list-extractor --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-06 — Dynamic Content Observer (`dynamic-content-observer`)
- **Capability**: Waits for client-rendered content, expands sections, follows tabs, and distinguishes loaded state from missing data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dynamic-content-observer --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-07 — Page Link Graph Explorer (`page-link-graph-explorer`)
- **Capability**: Maps internal and external links, navigation depth, orphaned pages, redirects, and broken destinations within scope.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill page-link-graph-explorer --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-08 — Reader Mode and Noise Filter (`reader-mode-and-noise-filter`)
- **Capability**: Separates primary text from advertisements, cookie layers, repeated menus, recommendations, and unrelated page chrome.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill reader-mode-and-noise-filter --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-09 — Page Change Comparator (`page-change-comparator`)
- **Capability**: Compares approved snapshots or versions for text, layout, links, images, metadata, and interactive behavior changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill page-change-comparator --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

#### cat03-10 — Web Accessibility Reading Tester (`web-accessibility-reading-tester`)
- **Capability**: Checks keyboard order, headings, landmarks, labels, alt text, contrast, zoom, reflow, and screen-reader-oriented structure.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-accessibility-reading-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"viewport_chars":500}'\'''
```

### Category 4: Browser Interaction and Form Use
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat04`

#### cat04-01 — Browser Task Operator (`browser-task-operator`)
- **Capability**: Performs authorized clicking, typing, tab switching, scrolling, selection, downloads, uploads, and navigation with step-level checks.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-task-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-02 — Form Completion Specialist (`form-completion-specialist`)
- **Capability**: Maps user-provided data to fields, respects required formats, validates before submission, and avoids inventing missing values.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill form-completion-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-03 — Multi-Step Workflow Navigator (`multi-step-workflow-navigator`)
- **Capability**: Completes wizard-style workflows while tracking state, dependencies, back navigation, validation, and final review.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill multi-step-workflow-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-04 — Search Filter and Sort Operator (`search-filter-and-sort-operator`)
- **Capability**: Uses filters, facets, date ranges, search boxes, sorting, and pagination to reach a reproducible result set.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill search-filter-and-sort-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-05 — Browser Session State Manager (`browser-session-state-manager`)
- **Capability**: Tracks cookies, login state, tabs, navigation history, local storage, and clean-session requirements without exposing secrets.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-session-state-manager --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-06 — File Upload Workflow Tester (`file-upload-workflow-tester`)
- **Capability**: Validates extension, size, MIME type, progress, cancellation, errors, preview, replacement, and final attachment state.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill file-upload-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-07 — Download Workflow Validator (`download-workflow-validator`)
- **Capability**: Confirms requested file identity, source, size, type, checksum when available, completion, safe storage, and openability.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill download-workflow-validator --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-08 — Clipboard and Copy-Paste Tester (`clipboard-and-copy-paste-tester`)
- **Capability**: Checks copied text, formatting, rich content, special characters, images, tables, and cross- application paste behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill clipboard-and-copy-paste-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-09 — Browser Permission Flow Tester (`browser-permission-flow-tester`)
- **Capability**: Tests camera, microphone, location, notifications, downloads, popups, and storage permissions using explicit authorization.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-permission-flow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

#### cat04-10 — Browser Recovery Operator (`browser-recovery-operator`)
- **Capability**: Recovers from stale elements, timeouts, redirects, lost sessions, modal blocks, and navigation errors without repeating consequential actions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-recovery-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"values":{"requester_name":"Alex Rivera","work_email":"alex@example.org","environment_tier":"sandbox"}}'\'''
```

### Category 5: Website Data Collection and Extraction
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat05`

#### cat05-01 — Structured Web Data Extractor (`structured-web-data-extractor`)
- **Capability**: Extracts authorized public records into defined schemas with source URLs, timestamps, types, units, and validation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill structured-web-data-extractor --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-02 — Pagination and Infinite-Scroll Collector (`pagination-and-infinite-scroll-collector`)
- **Capability**: Collects bounded result sets across pages or lazy loading with deduplication, rate limits, and resumable checkpoints.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill pagination-and-infinite-scroll-collector --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-03 — Metadata Harvesting Specialist (`metadata-harvesting-specialist`)
- **Capability**: Collects titles, authors, dates, descriptions, canonical URLs, language, media metadata, and declared licenses.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill metadata-harvesting-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-04 — Web Table Normalizer (`web-table-normalizer`)
- **Capability**: Converts HTML tables into clean CSV, JSON, or spreadsheet structures while preserving merged headers, notes, and units.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-table-normalizer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-05 — Directory and Catalog Collector (`directory-and-catalog-collector`)
- **Capability**: Collects public listings with names, categories, descriptions, links, locations, and freshness indicators.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill directory-and-catalog-collector --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-06 — Public Dataset Downloader (`public-dataset-downloader`)
- **Capability**: Locates official dataset releases, selects correct versions and formats, records licenses, and validates downloaded files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill public-dataset-downloader --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-07 — Web Content Snapshotter (`web-content-snapshotter`)
- **Capability**: Creates authorized snapshots of visible pages, selected regions, text, or metadata for comparison and evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-content-snapshotter --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-08 — Extraction Schema Designer (`extraction-schema-designer`)
- **Capability**: Defines fields, types, null behavior, identifiers, provenance, normalization, and quality rules before collection.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill extraction-schema-designer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-09 — Collection Rate and Load Controller (`collection-rate-and-load-controller`)
- **Capability**: Uses APIs or respectful bounded requests, caching, backoff, scheduling, and stop conditions to protect services.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill collection-rate-and-load-controller --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

#### cat05-10 — Web Extraction Quality Auditor (`web-extraction-quality-auditor`)
- **Capability**: Checks completeness, duplicates, truncation, encoding, pagination, hidden filters, field shifts, and source traceability.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-extraction-quality-auditor --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_pages":5}'\'''
```

### Category 6: APIs, Feeds, and Machine Interfaces
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat06`

#### cat06-01 — REST API Client Operator (`rest-api-client-operator`)
- **Capability**: Uses authorized endpoints, methods, headers, parameters, pagination, authentication, and error handling with reproducible requests.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill rest-api-client-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-02 — GraphQL Query Operator (`graphql-query-operator`)
- **Capability**: Inspects authorized schemas and runs bounded queries with variables, pagination, fragments, and response validation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill graphql-query-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-03 — SOAP Service Client Operator (`soap-service-client-operator`)
- **Capability**: Uses WSDL-defined operations, envelopes, namespaces, authentication, fault handling, and response validation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill soap-service-client-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-04 — Webhook Workflow Tester (`webhook-workflow-tester`)
- **Capability**: Tests authorized event delivery, signatures, retries, ordering, idempotency, timeouts, and failure recovery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill webhook-workflow-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-05 — RSS and Atom Feed Reader (`rss-and-atom-feed-reader`)
- **Capability**: Collects entries, dates, authors, links, categories, enclosures, and updates while deduplicating feed changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill rss-and-atom-feed-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-06 — OpenAPI and API Documentation Reader (`openapi-and-api-documentation-reader`)
- **Capability**: Interprets schemas, examples, authentication, constraints, status codes, and version differences before calling services.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill openapi-and-api-documentation-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-07 — API Pagination and Throttling Manager (`api-pagination-and-throttling-manager`)
- **Capability**: Handles cursors, offsets, rate headers, retry-after values, backoff, caching, and resumable checkpoints.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill api-pagination-and-throttling-manager --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-08 — API Response Schema Validator (`api-response-schema-validator`)
- **Capability**: Checks types, required fields, enums, units, nulls, version changes, and documented error bodies.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill api-response-schema-validator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-09 — API Contract Regression Tester (`api-contract-regression-tester`)
- **Capability**: Compares current behavior with documented or prior contracts across normal, boundary, and failure cases.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill api-contract-regression-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

#### cat06-10 — API Data Exporter (`api-data-exporter`)
- **Capability**: Transforms authorized responses into CSV, JSON Lines, databases, reports, or downstream schemas with provenance.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill api-data-exporter --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"method":"GET","url":"https://docs.example.org/api/v2/items?page=1"}'\'''
```

### Category 7: GitHub and Source Hosting
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat07`

#### cat07-01 — GitHub Repository Reader (`github-repository-reader`)
- **Capability**: Navigates README files, source trees, branches, tags, releases, issues, pull requests, actions, and project documentation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill github-repository-reader --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-02 — GitHub Code Searcher (`github-code-searcher`)
- **Capability**: Finds symbols, strings, configurations, examples, and usage patterns across authorized repositories and versions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill github-code-searcher --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-03 — GitHub Issue Investigator (`github-issue-investigator`)
- **Capability**: Groups reports, reproductions, labels, milestones, linked changes, workarounds, and unresolved questions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill github-issue-investigator --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-04 — Pull Request Reviewer (`pull-request-reviewer`)
- **Capability**: Analyzes diffs, tests, comments, dependencies, compatibility, security, and documentation without merging unless authorized.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill pull-request-reviewer --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-05 — Release and Changelog Analyst (`release-and-changelog-analyst`)
- **Capability**: Compares releases, assets, tags, notes, compatibility, checksums, migration guidance, and breaking changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill release-and-changelog-analyst --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-06 — GitHub Actions Observer (`github-actions-observer`)
- **Capability**: Reads workflow definitions, runs, logs, artifacts, permissions, caches, and failure points within authorized access.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill github-actions-observer --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-07 — GitHub Pages Tester (`github-pages-tester`)
- **Capability**: Tests public static sites for navigation, assets, routes, responsiveness, accessibility, and deployment-specific defects.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill github-pages-tester --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-08 — Repository Download and Verification Specialist (`repository-download-and-verification-specialist`)
- **Capability**: Clones or downloads authorized repositories, checks target revision, submodules, large files, and integrity.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill repository-download-and-verification-specialist --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-09 — Issue Reproduction Packager (`issue-reproduction-packager`)
- **Capability**: Creates minimal steps, environment details, input files, expected behavior, actual behavior, and diagnostic evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill issue-reproduction-packager --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

#### cat07-10 — Source Hosting Migration Planner (`source-hosting-migration-planner`)
- **Capability**: Plans repository, issues, releases, actions, pages, permissions, webhooks, and history migration between approved platforms.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill source-hosting-migration-planner --input data-web-app-interaction --params '\''{"pattern":"def handle_"}'\'''
```

### Category 8: Cloudflare, Hosting, and Web Infrastructure
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat08`

#### cat08-01 — Cloudflare Site Behavior Inspector (`cloudflare-site-behavior-inspector`)
- **Capability**: Reviews authorized DNS, proxy behavior, redirects, caching, certificates, headers, pages, and edge responses.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cloudflare-site-behavior-inspector --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-02 — Static Hosting Deployment Tester (`static-hosting-deployment-tester`)
- **Capability**: Tests GitHub Pages, Cloudflare Pages, Netlify, Vercel, Azure Static Web Apps, or similar authorized deployments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill static-hosting-deployment-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-03 — DNS and Domain Resolution Diagnostician (`dns-and-domain-resolution-diagnostician`)
- **Capability**: Checks records, propagation, authority, CNAME chains, TTLs, split behavior, and common misconfiguration.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dns-and-domain-resolution-diagnostician --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-04 — TLS and Certificate Tester (`tls-and-certificate-tester`)
- **Capability**: Checks hostname coverage, chain, validity, protocol support, redirects, mixed content, and renewal indicators.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill tls-and-certificate-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-05 — CDN Cache Behavior Tester (`cdn-cache-behavior-tester`)
- **Capability**: Tests cache keys, status, age, purging, revalidation, variants, compression, and stale-content behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cdn-cache-behavior-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-06 — Redirect and Routing Auditor (`redirect-and-routing-auditor`)
- **Capability**: Maps HTTP redirects, route rewrites, canonical hosts, trailing slashes, locale paths, and redirect loops.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill redirect-and-routing-auditor --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-07 — Hosting Error Log Interpreter (`hosting-error-log-interpreter`)
- **Capability**: Correlates authorized build, runtime, edge, function, and access logs with user-visible failures.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill hosting-error-log-interpreter --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-08 — Deployment Smoke Tester (`deployment-smoke-tester`)
- **Capability**: Checks homepage, critical routes, assets, forms, APIs, authentication, metadata, and monitoring after deployment.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill deployment-smoke-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-09 — Rollback Readiness Tester (`rollback-readiness-tester`)
- **Capability**: Confirms prior build availability, database compatibility, cache behavior, DNS impact, and documented rollback steps.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill rollback-readiness-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

#### cat08-10 — Web Infrastructure Change Validator (`web-infrastructure-change-validator`)
- **Capability**: Compares intended configuration with observed behavior before and after approved infrastructure changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-infrastructure-change-validator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"domain":"docs.example.org"}'\'''
```

### Category 9: Cloud Storage and File Transfer
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat09`

#### cat09-01 — Cloud Storage Browser (`cloud-storage-browser`)
- **Capability**: Navigates authorized buckets, drives, folders, objects, versions, metadata, previews, permissions, and search.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cloud-storage-browser --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-02 — Authorized File Downloader (`authorized-file-downloader`)
- **Capability**: Downloads requested files from approved sources and verifies name, type, completeness, and final openability.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill authorized-file-downloader --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-03 — Authorized File Uploader (`authorized-file-uploader`)
- **Capability**: Uploads user-approved files to the specified destination, checks permissions and metadata, and verifies final presence.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill authorized-file-uploader --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-04 — Large File Transfer Manager (`large-file-transfer-manager`)
- **Capability**: Uses multipart transfer, resumable uploads, checksums, retries, quotas, and progress checks for large authorized files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill large-file-transfer-manager --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-05 — Folder Synchronization Planner (`folder-synchronization-planner`)
- **Capability**: Compares local and cloud trees, identifies conflicts, and performs approved one-way or two- way synchronization.
- **Posture & Risk**: `destructive-or-mutating` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill folder-synchronization-planner --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-06 — Cloud Version Recovery Specialist (`cloud-version-recovery-specialist`)
- **Capability**: Finds prior versions, deleted items, snapshots, retention windows, and restoration options without overwriting current data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cloud-version-recovery-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-07 — File Sharing Permission Auditor (`file-sharing-permission-auditor`)
- **Capability**: Checks public, organization, link, user, group, download, edit, expiry, and inheritance settings.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill file-sharing-permission-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-08 — Cross-Storage Migration Operator (`cross-storage-migration-operator`)
- **Capability**: Moves approved data among OneDrive, SharePoint, Google Drive, Dropbox, S3-compatible storage, or local systems with validation.
- **Posture & Risk**: `destructive-or-mutating` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cross-storage-migration-operator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-09 — Transfer Integrity Auditor (`transfer-integrity-auditor`)
- **Capability**: Checks counts, sizes, hashes, metadata, permissions, timestamps, and failed objects after transfer.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill transfer-integrity-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

#### cat09-10 — Storage Quota and Lifecycle Analyst (`storage-quota-and-lifecycle-analyst`)
- **Capability**: Reviews capacity, versions, duplicates, retention, archive tiers, lifecycle rules, and safe cleanup opportunities.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill storage-quota-and-lifecycle-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"file_size_bytes":33554432}'\'''
```

### Category 10: Social Media and Community Platforms
- **Engine Module**: `data-web-app-interaction/tools-web-api-infra.py` | **Category ID**: `cat10`

#### cat10-01 — Public Social Content Reader (`public-social-content-reader`)
- **Capability**: Reads authorized public posts, threads, captions, comments, dates, links, and media context within platform rules.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill public-social-content-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-02 — Social Search and Hashtag Researcher (`social-search-and-hashtag-researcher`)
- **Capability**: Searches public platform content by keywords, hashtags, accounts, dates, and regions while noting sampling limits.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-search-and-hashtag-researcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-03 — Thread and Conversation Reconstructor (`thread-and-conversation-reconstructor`)
- **Capability**: Orders posts, replies, quotes, edits, deletions visible publicly, and branching discussion context.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill thread-and-conversation-reconstructor --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-04 — Social Media Content Collector (`social-media-content-collector`)
- **Capability**: Collects bounded public content with URLs, timestamps, authorship labels, media references, and deduplication.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-media-content-collector --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-05 — Community Sentiment Evidence Analyst (`community-sentiment-evidence-analyst`)
- **Capability**: Summarizes expressed opinions, recurring themes, and representative examples without inferring private traits or treating samples as populations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill community-sentiment-evidence-analyst --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-06 — Platform Content Export Reader (`platform-content-export-reader`)
- **Capability**: Interprets user-provided account exports, messages, posts, media indexes, and metadata with privacy safeguards.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill platform-content-export-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-07 — Social Publishing Draft Operator (`social-publishing-draft-operator`)
- **Capability**: Prepares posts, previews, alt text, links, and schedules but requires confirmation before publication.
- **Posture & Risk**: `publishing` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-publishing-draft-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-08 — Comment Moderation Assistant (`comment-moderation-assistant`)
- **Capability**: Classifies reported content according to supplied policies and recommends actions while preserving human review for consequential decisions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill comment-moderation-assistant --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-09 — Social Media Link and Media Validator (`social-media-link-and-media-validator`)
- **Capability**: Checks linked pages, thumbnails, captions, alt text, playable media, and broken or misleading destinations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-media-link-and-media-validator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

#### cat10-10 — Social Platform Workflow Tester (`social-platform-workflow-tester`)
- **Capability**: Tests authorized login, posting drafts, uploads, editing, privacy controls, accessibility, and error recovery without spamming.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-platform-workflow-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"max_chars":280}'\'''
```

### Category 11: Video, Audio, and Streaming Media
- **Engine Module**: `data-web-app-interaction/tools-media-docs-files.py` | **Category ID**: `cat11`

#### cat11-01 — Online Video Viewer and Interpreter (`online-video-viewer-and-interpreter`)
- **Capability**: Reviews accessible video frames, captions, transcript, chapters, metadata, and supplied context to summarize content and evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill online-video-viewer-and-interpreter --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-02 — Audio and Music Listener Analyst (`audio-and-music-listener-analyst`)
- **Capability**: Interprets audible structure, speech, instruments, rhythm, arrangement, themes, and user- supplied context without claiming unavailable perception.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill audio-and-music-listener-analyst --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-03 — Transcript Extraction and Cleanup Specialist (`transcript-extraction-and-cleanup-specialist`)
- **Capability**: Creates or cleans authorized transcripts with speakers, timestamps, punctuation, uncertain passages, and non-speech cues.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill transcript-extraction-and-cleanup-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-04 — Video Chapter and Segment Indexer (`video-chapter-and-segment-indexer`)
- **Capability**: Creates timestamps for topics, scenes, demonstrations, highlights, questions, ads, and key transitions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill video-chapter-and-segment-indexer --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-05 — Streaming Playback Tester (`streaming-playback-tester`)
- **Capability**: Tests play, pause, seek, buffering, quality switching, captions, audio tracks, fullscreen, and recovery across authorized environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill streaming-playback-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-06 — Media Metadata Inspector (`media-metadata-inspector`)
- **Capability**: Reads codec, resolution, frame rate, duration, channels, language, captions, chapters, and container metadata from available files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill media-metadata-inspector --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-07 — Caption and Subtitle QA Specialist (`caption-and-subtitle-qa-specialist`)
- **Capability**: Checks timing, completeness, line breaks, reading speed, speaker identification, language, and synchronization.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill caption-and-subtitle-qa-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-08 — Audio-Video Synchronization Tester (`audio-video-synchronization-tester`)
- **Capability**: Detects drift, offsets, dropped sections, frame cadence issues, and mismatched durations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill audio-video-synchronization-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-09 — Media Download Permission Reviewer (`media-download-permission-reviewer`)
- **Capability**: Uses only platform-supported or explicitly authorized downloads and records source, license, and permitted use.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill media-download-permission-reviewer --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

#### cat11-10 — Media Content Comparison Analyst (`media-content-comparison-analyst`)
- **Capability**: Compares versions, cuts, mixes, captions, framing, timing, quality, and missing segments using provided or accessible media.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill media-content-comparison-analyst --input data-web-app-interaction/asset-sample-page.html --params '\''{"audio_duration_ms":60000,"video_duration_ms":60020}'\'''
```

### Category 12: Document, Image, and Office File Interaction
- **Engine Module**: `data-web-app-interaction/tools-media-docs-files.py` | **Category ID**: `cat12`

#### cat12-01 — Universal Document Reader (`universal-document-reader`)
- **Capability**: Reads supported PDF, DOCX, PPTX, XLSX, text, HTML, Markdown, EPUB, and image-based documents with format-aware extraction.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill universal-document-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-02 — Scanned Document OCR Reader (`scanned-document-ocr-reader`)
- **Capability**: Converts authorized scans into searchable text while preserving page references, uncertainty, tables, and reading order.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill scanned-document-ocr-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-03 — Spreadsheet Data Reader (`spreadsheet-data-reader`)
- **Capability**: Inspects sheets, tables, formulas, types, filters, hidden structures, charts, and data quality using safe copies.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill spreadsheet-data-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-04 — Presentation Reader and Navigator (`presentation-reader-and-navigator`)
- **Capability**: Reviews slide order, visible content, notes, charts, images, links, and layout consistency.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill presentation-reader-and-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-05 — Image Text and Object Inspector (`image-text-and-object-inspector`)
- **Capability**: Reads visible text and identifies relevant objects, layout, relationships, colors, and image quality within privacy restrictions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill image-text-and-object-inspector --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-06 — Archive and Package Reader (`archive-and-package-reader`)
- **Capability**: Inventories authorized ZIP, TAR, 7Z, application bundles, and nested containers with safe extraction limits.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill archive-and-package-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-07 — File Format Identifier (`file-format-identifier`)
- **Capability**: Uses signatures, structure, metadata, and parsers to identify mislabeled or unknown files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill file-format-identifier --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-08 — Cross-Format Conversion Operator (`cross-format-conversion-operator`)
- **Capability**: Converts among approved document, image, data, and media formats while validating fidelity and reporting loss.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cross-format-conversion-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-09 — Document Search and Cross-Reference Builder (`document-search-and-cross-reference-builder`)
- **Capability**: Finds terms, entities, references, links, figures, tables, and related passages across multiple supplied files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill document-search-and-cross-reference-builder --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

#### cat12-10 — Office File Round-Trip Tester (`office-file-round-trip-tester`)
- **Capability**: Opens, edits on copies, renders, reopens, and compares office files for layout, formulas, links, accessibility, and editability.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill office-file-round-trip-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"target_format":"markdown"}'\'''
```

### Category 13: Local Filesystem and Offline Data
- **Engine Module**: `data-web-app-interaction/tools-media-docs-files.py` | **Category ID**: `cat13`

#### cat13-01 — Local Workspace Explorer (`local-workspace-explorer`)
- **Capability**: Inventories authorized folders, files, sizes, types, dates, permissions, and relationships without modifying originals.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill local-workspace-explorer --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-02 — Offline File Searcher (`offline-file-searcher`)
- **Capability**: Finds authorized content by name, metadata, text, structure, hash, or pattern across local collections.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill offline-file-searcher --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-03 — Duplicate File Analyst (`duplicate-file-analyst`)
- **Capability**: Identifies exact and near duplicates using hashes, normalized content, metadata, and safe review groups.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill duplicate-file-analyst --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-04 — File Organization Planner (`file-organization-planner`)
- **Capability**: Designs reversible naming, folder, metadata, tagging, and archive structures suited to the user workflow.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill file-organization-planner --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-05 — Batch File Renaming Operator (`batch-file-renaming-operator`)
- **Capability**: Renames approved files using previews, collision checks, reversible mappings, and post- operation validation.
- **Posture & Risk**: `destructive-or-mutating` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill batch-file-renaming-operator --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-06 — Local Data Importer (`local-data-importer`)
- **Capability**: Loads supported local files into clean analysis structures while preserving raw inputs and documenting transformations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill local-data-importer --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-07 — Offline Search Index Builder (`offline-search-index-builder`)
- **Capability**: Creates authorized indexes for fast full-text, metadata, and fielded search with update and deletion behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill offline-search-index-builder --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-08 — Filesystem Permission Auditor (`filesystem-permission-auditor`)
- **Capability**: Reviews access, ownership, inheritance, executable bits, sharing, and unintended exposure within authorized scope.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill filesystem-permission-auditor --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-09 — Safe Local File Manipulator (`safe-local-file-manipulator`)
- **Capability**: Copies, moves, transforms, or deletes only explicitly approved files with backups and conflict handling.
- **Posture & Risk**: `destructive-or-mutating` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill safe-local-file-manipulator --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

#### cat13-10 — Local Workspace Integrity Auditor (`local-workspace-integrity-auditor`)
- **Capability**: Checks missing files, broken references, unexpected changes, duplicates, stale outputs, and clean handoff structure.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill local-workspace-integrity-auditor --input data-web-app-interaction --params '\''{"name_glob":"asset-*"}'\'''
```

### Category 14: Data Reading and Interpretation
- **Engine Module**: `data-web-app-interaction/tools-data-science-sim.py` | **Category ID**: `cat14`

#### cat14-01 — Structured Data Interpreter (`structured-data-interpreter`)
- **Capability**: Reads CSV, JSON, XML, YAML, Parquet, databases, and tabular formats with schema, unit, and encoding awareness.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill structured-data-interpreter --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-02 — Unstructured Text Corpus Reader (`unstructured-text-corpus-reader`)
- **Capability**: Processes documents, messages, logs, notes, and web text into themes, entities, evidence, and structured summaries.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill unstructured-text-corpus-reader --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-03 — Data Dictionary Builder (`data-dictionary-builder`)
- **Capability**: Documents fields, types, units, allowed values, source, null meaning, identifiers, and relationships.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-dictionary-builder --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-04 — Schema and Type Inference Analyst (`schema-and-type-inference-analyst`)
- **Capability**: Infers candidate schemas from real data while marking ambiguity, mixed types, and unsupported assumptions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill schema-and-type-inference-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-05 — Entity and Relationship Extractor (`entity-and-relationship-extractor`)
- **Capability**: Identifies people, organizations, products, places, events, identifiers, and relationships from supplied or authorized material.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill entity-and-relationship-extractor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-06 — Time-Series Interpreter (`time-series-interpreter`)
- **Capability**: Reads timestamps, intervals, frequencies, gaps, seasonality, events, time zones, and measurement changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill time-series-interpreter --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-07 — Geospatial Data Interpreter (`geospatial-data-interpreter`)
- **Capability**: Reads coordinates, projections, boundaries, routes, places, spatial joins, and geographic uncertainty.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill geospatial-data-interpreter --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-08 — Log and Event Stream Reader (`log-and-event-stream-reader`)
- **Capability**: Interprets timestamps, severity, components, sessions, requests, traces, errors, and event sequences.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill log-and-event-stream-reader --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-09 — Survey and Feedback Reader (`survey-and-feedback-reader`)
- **Capability**: Analyzes responses, scales, free text, missingness, sampling limits, and recurring themes without overgeneralizing.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill survey-and-feedback-reader --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

#### cat14-10 — Data Meaning and Context Auditor (`data-meaning-and-context-auditor`)
- **Capability**: Checks whether fields, categories, metrics, and labels mean what the analysis assumes in the actual domain.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-meaning-and-context-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"time_column":"timestamp_utc"}'\'''
```

### Category 15: Data Cleaning, Transformation, and Manipulation
- **Engine Module**: `data-web-app-interaction/tools-data-science-sim.py` | **Category ID**: `cat15`

#### cat15-01 — Data Cleaning Specialist (`data-cleaning-specialist`)
- **Capability**: Handles missing values, duplicates, invalid records, whitespace, encodings, types, and documented correction rules.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-cleaning-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-02 — Data Normalization Engineer (`data-normalization-engineer`)
- **Capability**: Standardizes names, dates, units, categories, identifiers, addresses, and representations while preserving originals.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-normalization-engineer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-03 — Data Join and Merge Specialist (`data-join-and-merge-specialist`)
- **Capability**: Joins datasets using validated keys, cardinality checks, unmatched analysis, and duplicate prevention.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-join-and-merge-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-04 — Data Reshaping Operator (`data-reshaping-operator`)
- **Capability**: Pivots, unpivots, groups, explodes, aggregates, and restructures data for analysis or delivery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-reshaping-operator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-05 — Data Filtering and Sampling Specialist (`data-filtering-and-sampling-specialist`)
- **Capability**: Creates reproducible subsets using documented rules, stratification, random seeds, and coverage checks.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-filtering-and-sampling-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-06 — Text Parsing and Extraction Engineer (`text-parsing-and-extraction-engineer`)
- **Capability**: Extracts fields from filenames, logs, messages, documents, URLs, and semi-structured text with validation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill text-parsing-and-extraction-engineer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-07 — Date, Time, and Time-Zone Normalizer (`date-time-and-time-zone-normalizer`)
- **Capability**: Resolves formats, locales, daylight saving, offsets, missing zones, intervals, and ambiguous timestamps.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill date-time-and-time-zone-normalizer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-08 — Unit and Currency Conversion Specialist (`unit-and-currency-conversion-specialist`)
- **Capability**: Applies explicit conversion sources or supplied rates, unit dimensionality, rounding, and effective dates.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill unit-and-currency-conversion-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-09 — Data Anonymization and Redaction Operator (`data-anonymization-and-redaction-operator`)
- **Capability**: Removes or transforms personal and sensitive fields according to stated utility, re- identification risk, and policy.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-anonymization-and-redaction-operator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

#### cat15-10 — Transformation Reproducibility Auditor (`transformation-reproducibility-auditor`)
- **Capability**: Ensures raw inputs, code, parameters, software versions, outputs, and validation checks can be rerun.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill transformation-reproducibility-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"group_by":"region","value_column":"latency_ms"}'\'''
```

### Category 16: Data Analysis, Comparison, and Insight
- **Engine Module**: `data-web-app-interaction/tools-data-science-sim.py` | **Category ID**: `cat16`

#### cat16-01 — Exploratory Data Analyst (`exploratory-data-analyst`)
- **Capability**: Profiles distributions, relationships, segments, anomalies, trends, and data limitations before modeling.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill exploratory-data-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-02 — Descriptive Statistics Analyst (`descriptive-statistics-analyst`)
- **Capability**: Computes counts, rates, central tendency, spread, quantiles, uncertainty, and group comparisons with correct denominators.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill descriptive-statistics-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-03 — Comparative Evidence Analyst (`comparative-evidence-analyst`)
- **Capability**: Compares versions, sources, cohorts, products, periods, or systems under aligned definitions and conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill comparative-evidence-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-04 — Trend and Change Analyst (`trend-and-change-analyst`)
- **Capability**: Separates level, direction, seasonality, structural breaks, measurement changes, and normal variability.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill trend-and-change-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-05 — Anomaly Investigation Analyst (`anomaly-investigation-analyst`)
- **Capability**: Finds unusual records or behavior, tests data quality causes, and avoids labeling anomalies as wrongdoing without evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill anomaly-investigation-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-06 — Funnel and Journey Analyst (`funnel-and-journey-analyst`)
- **Capability**: Measures stage entry, completion, drop-off, time, segmentation, and definition sensitivity across workflows.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill funnel-and-journey-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-07 — Cohort and Retention Analyst (`cohort-and-retention-analyst`)
- **Capability**: Tracks behavior by starting cohort, time since entry, return definitions, censoring, and comparable observation windows.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cohort-and-retention-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-08 — Text Theme and Topic Analyst (`text-theme-and-topic-analyst`)
- **Capability**: Identifies recurring themes, contrasts, representative passages, and uncertainty without fabricating quotations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill text-theme-and-topic-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-09 — Root Cause Evidence Analyst (`root-cause-evidence-analyst`)
- **Capability**: Builds and tests causal hypotheses using timelines, comparisons, traces, experiments, and explicit alternative explanations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill root-cause-evidence-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

#### cat16-10 — Insight Validation Reviewer (`insight-validation-reviewer`)
- **Capability**: Checks whether conclusions are supported, practically meaningful, reproducible, and clear about uncertainty and limitations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill insight-validation-reviewer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"column":"latency_ms","group_by":"region"}'\'''
```

### Category 17: Data Simulation and Synthetic Testing
- **Engine Module**: `data-web-app-interaction/tools-data-science-sim.py` | **Category ID**: `cat17`

#### cat17-01 — Scenario Simulation Designer (`scenario-simulation-designer`)
- **Capability**: Creates explicit assumptions, variables, ranges, relationships, events, outputs, and validation for hypothetical scenarios.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill scenario-simulation-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-02 — Monte Carlo Simulation Analyst (`monte-carlo-simulation-analyst`)
- **Capability**: Runs repeated sampled scenarios with documented distributions, seeds, convergence checks, and uncertainty summaries.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill monte-carlo-simulation-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-03 — Discrete Event Simulation Designer (`discrete-event-simulation-designer`)
- **Capability**: Models queues, resources, arrivals, service, routing, failure, and time-dependent operations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill discrete-event-simulation-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-04 — Agent-Based Simulation Designer (`agent-based-simulation-designer`)
- **Capability**: Models autonomous entities, rules, interactions, environment, adaptation, and emergent outcomes with sensitivity testing.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill agent-based-simulation-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-05 — Synthetic Dataset Generator (`synthetic-dataset-generator`)
- **Capability**: Creates clearly labeled artificial data matching declared schemas and constraints without presenting it as observed reality.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill synthetic-dataset-generator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-06 — Load and Traffic Simulation Planner (`load-and-traffic-simulation-planner`)
- **Capability**: Models authorized user volumes, request mixes, think time, concurrency, ramp-up, and safe stopping thresholds.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill load-and-traffic-simulation-planner --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-07 — Failure Injection Scenario Designer (`failure-injection-scenario-designer`)
- **Capability**: Simulates timeouts, missing data, unavailable services, corrupt files, partial responses, and resource exhaustion in test environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill failure-injection-scenario-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-08 — What-If Analysis Specialist (`what-if-analysis-specialist`)
- **Capability**: Tests parameter changes, policy options, thresholds, capacity, pricing, schedules, and assumptions without overstating predictions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill what-if-analysis-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-09 — Simulation Calibration Analyst (`simulation-calibration-analyst`)
- **Capability**: Compares model behavior with observed or supplied reference data and documents mismatch and parameter uncertainty.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill simulation-calibration-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

#### cat17-10 — Simulation Validity Auditor (`simulation-validity-auditor`)
- **Capability**: Checks assumptions, implementation, sensitivity, edge cases, reproducibility, and whether outputs answer the intended question.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill simulation-validity-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":42,"trials":1000}'\'''
```

### Category 18: Application Interaction and Desktop Automation
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat18`

#### cat18-01 — Desktop Application Operator (`desktop-application-operator`)
- **Capability**: Uses authorized menus, toolbars, dialogs, keyboard shortcuts, drag and drop, and file operations with state checks.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill desktop-application-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-02 — Cross-Application Workflow Operator (`cross-application-workflow-operator`)
- **Capability**: Moves approved data among browsers, office apps, editors, terminals, media tools, and specialized applications.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cross-application-workflow-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-03 — GUI Element Inspector (`gui-element-inspector`)
- **Capability**: Identifies controls, labels, states, focus, hierarchy, accessibility properties, and actionable elements.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill gui-element-inspector --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-04 — Keyboard-Only Workflow Tester (`keyboard-only-workflow-tester`)
- **Capability**: Completes tasks without a pointer to test focus order, shortcuts, menus, dialogs, and accessibility.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill keyboard-only-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-05 — Touch Interaction Tester (`touch-interaction-tester`)
- **Capability**: Tests taps, long presses, swipes, pinches, rotations, drag, multi-touch, target size, and gesture conflicts on supported devices.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill touch-interaction-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-06 — Application Settings Navigator (`application-settings-navigator`)
- **Capability**: Finds and changes explicitly approved settings, records previous values, and verifies the resulting behavior.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill application-settings-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-07 — Import and Export Workflow Tester (`import-and-export-workflow-tester`)
- **Capability**: Tests file selection, options, progress, cancellation, warnings, output fidelity, and reopen behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill import-and-export-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-08 — Application Update Tester (`application-update-tester`)
- **Capability**: Tests authorized install, migration, compatibility, preferences, files, plugins, rollback, and first launch.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill application-update-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-09 — Desktop Automation Script Designer (`desktop-automation-script-designer`)
- **Capability**: Creates bounded, idempotent automation with window checks, delays, retries, logs, and abort conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill desktop-automation-script-designer --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

#### cat18-10 — Application Recovery Operator (`application-recovery-operator`)
- **Capability**: Recovers from hangs, crashed dialogs, lost focus, unsaved work, stale state, and partial operations while preserving data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill application-recovery-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"window_title":"Service Catalog"}'\'''
```

### Category 19: Mobile App Interaction and Device Testing
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat19`

#### cat19-01 — Mobile App Task Operator (`mobile-app-task-operator`)
- **Capability**: Performs authorized taps, typing, gestures, navigation, uploads, downloads, and app workflows with state verification.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-app-task-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-02 — Mobile Installation and Launch Tester (`mobile-installation-and-launch-tester`)
- **Capability**: Tests install, update, first launch, permissions, onboarding, deep links, backgrounding, and relaunch.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-installation-and-launch-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-03 — Mobile Permission Experience Tester (`mobile-permission-experience-tester`)
- **Capability**: Tests camera, photos, microphone, location, notifications, contacts, storage, and denial recovery with consent.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-permission-experience-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-04 — Responsive Mobile Layout Tester (`responsive-mobile-layout-tester`)
- **Capability**: Checks orientations, screen sizes, density, text scaling, safe areas, keyboards, notches, and split-screen behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill responsive-mobile-layout-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-05 — Mobile Network Condition Tester (`mobile-network-condition-tester`)
- **Capability**: Tests offline, slow, intermittent, roaming-like, captive, and recovery behavior in authorized test environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-network-condition-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-06 — Mobile Storage and Data Tester (`mobile-storage-and-data-tester`)
- **Capability**: Checks cache, local data, downloads, low storage, backup, restore, logout, and data-clearing behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-storage-and-data-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-07 — Mobile Notification Tester (`mobile-notification-tester`)
- **Capability**: Tests delivery, grouping, actions, badges, sound, privacy, permissions, deep links, and stale notifications.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-notification-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-08 — Mobile Accessibility Tester (`mobile-accessibility-tester`)
- **Capability**: Checks screen reader labels, focus, target size, text scale, contrast, motion, switch access, and voice control.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-accessibility-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-09 — Mobile Battery and Background Analyst (`mobile-battery-and-background-analyst`)
- **Capability**: Observes approved app activity, background work, wake behavior, location use, and obvious battery-impact patterns.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-battery-and-background-analyst --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

#### cat19-10 — Mobile Crash and ANR Investigator (`mobile-crash-and-anr-investigator`)
- **Capability**: Collects authorized crash, freeze, lifecycle, device, OS, reproduction, and diagnostic evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill mobile-crash-and-anr-investigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"cold_start_ms":840}'\'''
```

### Category 20: Game Playing, Exploration, and Interaction
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat20`

#### cat20-01 — Game Tutorial and Onboarding Player (`game-tutorial-and-onboarding-player`)
- **Capability**: Completes authorized tutorials, records clarity, control teaching, pacing, blockers, and first- session experience.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-tutorial-and-onboarding-player --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-02 — Gameplay Systems Explorer (`gameplay-systems-explorer`)
- **Capability**: Tests movement, combat, crafting, inventory, economy, progression, dialogue, quests, and save behavior within the game rules.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill gameplay-systems-explorer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-03 — Quest and Objective Tester (`quest-and-objective-tester`)
- **Capability**: Follows normal and alternate paths, tracks objective state, markers, dialogue, rewards, and failure recovery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill quest-and-objective-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-04 — Game UI and Menu Tester (`game-ui-and-menu-tester`)
- **Capability**: Tests menus, HUD, inventory, maps, settings, prompts, controllers, keyboard, touch, and accessibility options.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-ui-and-menu-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-05 — Save Load and Checkpoint Tester (`save-load-and-checkpoint-tester`)
- **Capability**: Tests manual saves, autosaves, checkpoints, slots, cloud sync, corruption recovery, and version compatibility.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill save-load-and-checkpoint-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-06 — Multiplayer Session Tester (`multiplayer-session-tester`)
- **Capability**: Tests authorized lobbies, invites, matchmaking, reconnect, host migration, latency handling, moderation, and result synchronization.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill multiplayer-session-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-07 — Game Economy and Progression Analyst (`game-economy-and-progression-analyst`)
- **Capability**: Reviews resource flows, rewards, sinks, pacing, unlocks, difficulty, and potential dead ends using observed play data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-economy-and-progression-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-08 — Narrative Choice and Ending Tester (`narrative-choice-and-ending-tester`)
- **Capability**: Exercises branches, flags, relationship states, consequences, route locks, and ending conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill narrative-choice-and-ending-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-09 — Controller and Input Device Tester (`controller-and-input-device-tester`)
- **Capability**: Tests keyboard, mouse, gamepad, touch, remapping, dead zones, vibration, disconnection, and multiple devices.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill controller-and-input-device-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

#### cat20-10 — Game Session Evidence Recorder (`game-session-evidence-recorder`)
- **Capability**: Captures permitted steps, timestamps, screenshots or clips, logs, builds, seeds, saves, and observed outcomes for reproduction.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-session-evidence-recorder --input data-web-app-interaction/asset-sample-records.csv --params '\''{"seed":1337}'\'''
```

### Category 21: Game Beta Testing and Quality Assurance
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat21`

#### cat21-01 — Game Functional QA Tester (`game-functional-qa-tester`)
- **Capability**: Tests game features against requirements using normal, boundary, interrupted, repeated, and recovery scenarios.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-functional-qa-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-02 — Exploratory Game Tester (`exploratory-game-tester`)
- **Capability**: Uses charters and time-boxed exploration to discover unexpected state combinations without destructive cheating or service abuse.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill exploratory-game-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-03 — Game Regression Tester (`game-regression-tester`)
- **Capability**: Rechecks fixed defects and nearby systems across supported builds, saves, devices, and control schemes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-regression-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-04 — Game Compatibility Tester (`game-compatibility-tester`)
- **Capability**: Tests supported operating systems, hardware tiers, resolutions, aspect ratios, drivers, languages, and input devices.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-compatibility-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-05 — Game Performance Tester (`game-performance-tester`)
- **Capability**: Measures frame rate, frame time, loading, memory, stutter, thermal behavior, network delay, and long-session degradation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-performance-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-06 — Game Collision and Physics Tester (`game-collision-and-physics-tester`)
- **Capability**: Tests boundaries, slopes, moving platforms, vehicles, ragdolls, projectiles, stacking, tunneling, and reset behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-collision-and-physics-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-07 — Game Balance Test Designer (`game-balance-test-designer`)
- **Capability**: Runs controlled comparisons of characters, items, levels, strategies, probabilities, and progression using reproducible conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-balance-test-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-08 — Localization Game QA Tester (`localization-game-qa-tester`)
- **Capability**: Checks text fit, fonts, directionality, voice, subtitles, cultural references, input glyphs, and localized logic.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill localization-game-qa-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-09 — Game Accessibility QA Tester (`game-accessibility-qa-tester`)
- **Capability**: Tests remapping, subtitles, contrast, difficulty options, motion, audio cues, timing, assist modes, and menu accessibility.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-accessibility-qa-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

#### cat21-10 — Game Bug Triage Specialist (`game-bug-triage-specialist`)
- **Capability**: Ranks defects by reproducibility, severity, frequency, player impact, data loss, progression block, and release risk.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill game-bug-triage-specialist --input data-web-app-interaction/asset-sample-records.csv --params '\''{"projectile_speed_mps":120,"physics_tick_hz":60}'\'''
```

### Category 22: Website and Web App Testing
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat22`

#### cat22-01 — Website Functional Tester (`website-functional-tester`)
- **Capability**: Tests links, navigation, forms, search, accounts, uploads, downloads, media, errors, and critical workflows.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill website-functional-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-02 — Responsive Web Tester (`responsive-web-tester`)
- **Capability**: Checks layouts, breakpoints, orientation, zoom, text scaling, input methods, and content reflow across viewports.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill responsive-web-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-03 — Cross-Browser Compatibility Tester (`cross-browser-compatibility-tester`)
- **Capability**: Compares supported versions of Chromium, Firefox, Safari, and WebView-like environments for visible and functional differences.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cross-browser-compatibility-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-04 — Web Accessibility QA Specialist (`web-accessibility-qa-specialist`)
- **Capability**: Tests keyboard, screen-reader structure, focus, contrast, forms, errors, motion, zoom, reflow, and alternatives.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-accessibility-qa-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-05 — Web Performance Tester (`web-performance-tester`)
- **Capability**: Measures navigation, resource loading, Core Web Vitals, caching, compression, image delivery, JavaScript cost, and slow-network behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-performance-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-06 — Web Session and Authentication Tester (`web-session-and-authentication-tester`)
- **Capability**: Tests authorized sign-in, sign-out, timeout, refresh, role access, recovery, multi-tab behavior, and session revocation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-session-and-authentication-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-07 — Web Form Boundary Tester (`web-form-boundary-tester`)
- **Capability**: Tests missing, long, malformed, Unicode, file, date, locale, duplicate, and interrupted input with safe values.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-form-boundary-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-08 — Web Error Recovery Tester (`web-error-recovery-tester`)
- **Capability**: Tests 404, 500, offline, timeout, expired session, failed payment simulation, partial upload, and retry behavior in test environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-error-recovery-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-09 — Web Analytics Validation Tester (`web-analytics-validation-tester`)
- **Capability**: Checks consent-aware event names, properties, triggers, duplication, navigation, campaign parameters, and exclusion of sensitive data.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill web-analytics-validation-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

#### cat22-10 — Website Release Acceptance Tester (`website-release-acceptance-tester`)
- **Capability**: Runs a prioritized smoke and regression suite against staging and production with clear pass, fail, and rollback criteria.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill website-release-acceptance-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"vitals":{"lcp_ms":1850,"inp_ms":95,"cls":0.04,"ttfb_ms":210}}'\'''
```

### Category 23: App Testing, Debugging, and Defect Reproduction
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat23`

#### cat23-01 — Reproduction Step Engineer (`reproduction-step-engineer`)
- **Capability**: Reduces a reported problem to the smallest reliable sequence with environment, input, state, expected, and actual behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill reproduction-step-engineer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-02 — Crash and Freeze Investigator (`crash-and-freeze-investigator`)
- **Capability**: Collects stacks, logs, dumps, device state, timing, recent actions, and minimal reproduction evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill crash-and-freeze-investigator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-03 — Log Correlation Analyst (`log-correlation-analyst`)
- **Capability**: Aligns client, server, browser, app, network, and infrastructure timestamps to locate the earliest observed failure.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill log-correlation-analyst --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-04 — State and Cache Debugger (`state-and-cache-debugger`)
- **Capability**: Tests clean profiles, caches, cookies, local data, preferences, sessions, and migrations to isolate state-dependent defects.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill state-and-cache-debugger --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-05 — Dependency and Environment Debugger (`dependency-and-environment-debugger`)
- **Capability**: Compares versions, libraries, services, permissions, locales, time zones, hardware, and configuration.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dependency-and-environment-debugger --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-06 — Network Workflow Debugger (`network-workflow-debugger`)
- **Capability**: Uses authorized traces and logs to inspect DNS, connection, TLS, request, response, timeout, retry, and payload behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill network-workflow-debugger --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-07 — UI State Debugger (`ui-state-debugger`)
- **Capability**: Tracks focus, visibility, enabled state, loading, overlays, stale data, race conditions, and event ordering.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill ui-state-debugger --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-08 — Data Corruption Diagnostician (`data-corruption-diagnostician`)
- **Capability**: Identifies malformed records, partial writes, incompatible versions, invalid references, and safe recovery options.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill data-corruption-diagnostician --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-09 — Minimal Fix Verification Tester (`minimal-fix-verification-tester`)
- **Capability**: Confirms the repaired cause, original reproduction, normal behavior, boundaries, and nearby regression risks.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill minimal-fix-verification-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

#### cat23-10 — Defect Report Author (`defect-report-author`)
- **Capability**: Produces concise title, environment, severity, reproducibility, steps, evidence, expected result, actual result, and impact.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill defect-report-author --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"severity":"medium"}'\'''
```

### Category 24: Automation, Crawling, and Browser Scripting
- **Engine Module**: `data-web-app-interaction/tools-apps-games-qa.py` | **Category ID**: `cat24`

#### cat24-01 — Browser Automation Designer (`browser-automation-designer`)
- **Capability**: Creates Playwright, Selenium, or equivalent workflows with stable selectors, waits, assertions, retries, and clean teardown.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-automation-designer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-02 — Crawl Strategy Designer (`crawl-strategy-designer`)
- **Capability**: Defines seeds, scope, depth, include and exclude rules, rate limits, canonicalization, deduplication, and stop conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill crawl-strategy-designer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-03 — Robust Selector Engineer (`robust-selector-engineer`)
- **Capability**: Uses semantic roles, labels, test IDs, text, and structural fallbacks instead of fragile coordinate-only selectors.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill robust-selector-engineer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-04 — Automation Idempotency Specialist (`automation-idempotency-specialist`)
- **Capability**: Ensures reruns do not duplicate messages, submissions, uploads, purchases, records, or other consequential effects.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill automation-idempotency-specialist --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-05 — Automation Checkpoint and Resume Engineer (`automation-checkpoint-and-resume-engineer`)
- **Capability**: Stores safe progress markers so bounded collection or testing can resume after failure without replaying completed actions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill automation-checkpoint-and-resume-engineer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-06 — Headless and Headed Parity Tester (`headless-and-headed-parity-tester`)
- **Capability**: Compares behavior, rendering, permissions, timing, and anti-automation limitations between execution modes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill headless-and-headed-parity-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-07 — Automation Evidence Collector (`automation-evidence-collector`)
- **Capability**: Captures permitted screenshots, traces, console output, network logs, videos, and structured results for failed steps.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill automation-evidence-collector --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-08 — Automation Flakiness Diagnostician (`automation-flakiness-diagnostician`)
- **Capability**: Finds timing, selector, shared-state, test-data, network, environment, and nondeterminism causes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill automation-flakiness-diagnostician --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-09 — Scheduled Web Monitor Designer (`scheduled-web-monitor-designer`)
- **Capability**: Creates bounded checks for availability, content changes, prices, releases, or status with alerts and policy compliance.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill scheduled-web-monitor-designer --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

#### cat24-10 — Automation Safety Auditor (`automation-safety-auditor`)
- **Capability**: Checks target scope, credentials, rate limits, destructive actions, privacy, retries, logging, and emergency stop behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill automation-safety-auditor --input data-web-app-interaction/asset-sample-page.html --params '\''{"max_depth":2,"max_pages":10}'\'''
```

### Category 25: Network, Protocol, and Traffic Observation
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat25`

#### cat25-01 — HTTP Request Response Analyst (`http-request-response-analyst`)
- **Capability**: Examines authorized methods, URLs, headers, cookies, status, timing, body, redirects, cache, and content negotiation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill http-request-response-analyst --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-02 — Browser Network Trace Reader (`browser-network-trace-reader`)
- **Capability**: Interprets HAR or developer-tool captures for waterfalls, failures, dependencies, blocking, and repeated requests.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill browser-network-trace-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-03 — DNS Resolution Analyst (`dns-resolution-analyst`)
- **Capability**: Checks lookup path, records, caching, failures, split behavior, and endpoint selection for authorized targets.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dns-resolution-analyst --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-04 — TLS Connection Observer (`tls-connection-observer`)
- **Capability**: Reviews certificate, protocol, handshake, hostname, chain, and timing information without attempting unauthorized interception.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill tls-connection-observer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-05 — WebSocket Session Tester (`websocket-session-tester`)
- **Capability**: Tests authorized connection, messages, ordering, heartbeats, reconnection, limits, and state synchronization.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill websocket-session-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-06 — Streaming Protocol Observer (`streaming-protocol-observer`)
- **Capability**: Analyzes authorized manifests, segments, buffering, adaptive quality, captions, and playback errors.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill streaming-protocol-observer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-07 — Packet Capture Interpreter (`packet-capture-interpreter`)
- **Capability**: Reads authorized captures to summarize flows, endpoints, timing, retransmission, resets, and protocol behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill packet-capture-interpreter --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-08 — API Traffic Comparator (`api-traffic-comparator`)
- **Capability**: Compares normal and failing requests for parameters, headers, payload, identity, ordering, timing, and response differences.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill api-traffic-comparator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-09 — Network Condition Simulator (`network-condition-simulator`)
- **Capability**: Applies bounded latency, bandwidth, packet loss, offline, and reconnect conditions in controlled test environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill network-condition-simulator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

#### cat25-10 — Traffic Privacy Auditor (`traffic-privacy-auditor`)
- **Capability**: Checks URLs, headers, payloads, logs, analytics, and third-party requests for unintended sensitive data exposure.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill traffic-privacy-auditor --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"baseline_rtt_ms":25,"added_latency_ms":100}'\'''
```

### Category 26: Accessibility and Inclusive Interaction
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat26`

#### cat26-01 — Screen Reader Workflow Tester (`screen-reader-workflow-tester`)
- **Capability**: Tests supported screen readers for names, roles, states, order, landmarks, forms, alerts, and dynamic updates.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill screen-reader-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-02 — Keyboard Navigation Tester (`keyboard-navigation-tester`)
- **Capability**: Tests tab order, focus visibility, skips, traps, shortcuts, menus, dialogs, grids, and activation without a pointer.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill keyboard-navigation-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-03 — Voice Control Interaction Tester (`voice-control-interaction-tester`)
- **Capability**: Checks visible names, labels, commands, dictation, correction, and alternatives for supported voice-control systems.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill voice-control-interaction-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-04 — Switch and Alternative Input Tester (`switch-and-alternative-input-tester`)
- **Capability**: Tests sequential navigation, scanning, target activation, timing, and escape using supported alternative inputs.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill switch-and-alternative-input-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-05 — Low Vision Interaction Tester (`low-vision-interaction-tester`)
- **Capability**: Checks zoom, magnification, reflow, contrast, text spacing, focus, image clarity, and loss of content.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill low-vision-interaction-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-06 — Cognitive Accessibility Tester (`cognitive-accessibility-tester`)
- **Capability**: Evaluates clarity, consistency, instructions, memory load, error prevention, time limits, distractions, and recovery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cognitive-accessibility-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-07 — Caption and Audio Alternative Tester (`caption-and-audio-alternative-tester`)
- **Capability**: Checks captions, transcripts, audio description, visual alerts, non-speech cues, and synchronization.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill caption-and-audio-alternative-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-08 — Motion and Flash Safety Tester (`motion-and-flash-safety-tester`)
- **Capability**: Checks reduced-motion support, pausing, parallax, vestibular triggers, flash frequency, and animation control.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill motion-and-flash-safety-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-09 — Accessible Game Interaction Tester (`accessible-game-interaction-tester`)
- **Capability**: Tests remapping, assist modes, subtitles, text size, color cues, audio cues, timing, difficulty, and menu access.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill accessible-game-interaction-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

#### cat26-10 — Accessibility Defect Reporter (`accessibility-defect-reporter`)
- **Capability**: Maps observed barriers to task impact, reproduction, relevant standards, affected users, and testable acceptance criteria.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill accessibility-defect-reporter --input data-web-app-interaction/asset-sample-page.html --params '\''{"foreground_hex":"#1f2937","background_hex":"#ffffff"}'\'''
```

### Category 27: Performance, Reliability, and Load Testing
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat27`

#### cat27-01 — Performance Baseline Designer (`performance-baseline-designer`)
- **Capability**: Defines representative environments, tasks, datasets, network conditions, warmup, metrics, thresholds, and repeat counts.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill performance-baseline-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-02 — Load Test Planner (`load-test-planner`)
- **Capability**: Creates authorized concurrency, arrival, duration, ramp, workload mix, data, monitoring, and safety stop conditions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill load-test-planner --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-03 — Stress and Capacity Tester (`stress-and-capacity-tester`)
- **Capability**: Finds controlled saturation, degradation, queue growth, error behavior, recovery, and safe capacity boundaries.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill stress-and-capacity-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-04 — Soak and Endurance Tester (`soak-and-endurance-tester`)
- **Capability**: Runs long sessions to identify leaks, cache growth, drift, degradation, stale state, and scheduled-job interactions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill soak-and-endurance-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-05 — Frontend Performance Analyst (`frontend-performance-analyst`)
- **Capability**: Measures rendering, scripting, layout, memory, assets, input responsiveness, and user-visible delays.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill frontend-performance-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-06 — Backend Latency Analyst (`backend-latency-analyst`)
- **Capability**: Separates network, queue, application, database, dependency, and payload contributions to response time.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill backend-latency-analyst --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-07 — Reliability and Retry Tester (`reliability-and-retry-tester`)
- **Capability**: Tests timeout, retry, backoff, duplication, idempotency, failover, partial success, and recovery behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill reliability-and-retry-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-08 — Resource Usage Monitor (`resource-usage-monitor`)
- **Capability**: Observes CPU, memory, disk, network, handles, threads, GPU, battery, and thermal behavior in approved environments.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill resource-usage-monitor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-09 — Performance Regression Detector (`performance-regression-detector`)
- **Capability**: Compares aligned builds and workloads using distributions, confidence, environment controls, and practical thresholds.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill performance-regression-detector --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

#### cat27-10 — Performance Findings Reporter (`performance-findings-reporter`)
- **Capability**: Links user impact to measured evidence, bottleneck hypotheses, reproduction, and prioritized remediation checks.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill performance-findings-reporter --input data-web-app-interaction/asset-sample-records.csv --params '\''{"peak_rps":50,"think_time_s":2.0}'\'''
```

### Category 28: Security-Focused Defensive Interaction
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat28`

#### cat28-01 — Secure Configuration Reviewer (`secure-configuration-reviewer`)
- **Capability**: Reviews authorized application, browser, cloud, storage, repository, and hosting settings against defined baselines.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill secure-configuration-reviewer --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-02 — Authentication Flow Defensive Tester (`authentication-flow-defensive-tester`)
- **Capability**: Tests authorized registration, sign-in, recovery, MFA, logout, timeout, and lockout for correctness without bypass attempts.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill authentication-flow-defensive-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-03 — Authorization Boundary Tester (`authorization-boundary-tester`)
- **Capability**: Verifies role-based access using approved test accounts and expected permissions without accessing unrelated real user data.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill authorization-boundary-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-04 — Input Validation Defensive Tester (`input-validation-defensive-tester`)
- **Capability**: Uses safe non-destructive boundary inputs to find crashes, errors, unsafe reflection, or inconsistent validation.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill input-validation-defensive-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-05 — Sensitive Data Exposure Reviewer (`sensitive-data-exposure-reviewer`)
- **Capability**: Checks pages, files, logs, errors, metadata, storage, analytics, and exports for unintended secrets or personal data.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill sensitive-data-exposure-reviewer --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-06 — Dependency and Supply Chain Observer (`dependency-and-supply-chain-observer`)
- **Capability**: Reviews lockfiles, packages, releases, signatures, provenance, and authorized vulnerability reports for risk triage.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dependency-and-supply-chain-observer --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-07 — File Upload Safety Tester (`file-upload-safety-tester`)
- **Capability**: Checks allowed types, size, naming, metadata, preview, storage, access, and safe rejection behavior using benign test files.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill file-upload-safety-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-08 — Security Header and Cookie Reviewer (`security-header-and-cookie-reviewer`)
- **Capability**: Checks authorized sites for transport, framing, content, referrer, permission, cookie, and caching protections.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill security-header-and-cookie-reviewer --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-09 — Defensive Incident Artifact Collector (`defensive-incident-artifact-collector`)
- **Capability**: Preserves authorized logs, timestamps, files, hashes, screenshots, traces, and environment notes for investigation.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill defensive-incident-artifact-collector --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

#### cat28-10 — Responsible Security Finding Reporter (`responsible-security-finding-reporter`)
- **Capability**: Documents impact, reproduction, evidence, scope, mitigation, disclosure path, and avoids weaponized instructions.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill responsible-security-finding-reporter --input data-web-app-interaction/asset-sample-page.html --params '\''{"severity":"low"}'\'''
```

### Category 29: Monitoring, Change Detection, and Watchers
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat29`

#### cat29-01 — Website Availability Monitor (`website-availability-monitor`)
- **Capability**: Checks authorized URLs at bounded intervals for status, content, TLS, redirects, latency, and error evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill website-availability-monitor --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-02 — Content Change Monitor (`content-change-monitor`)
- **Capability**: Tracks approved pages or files for meaningful text, link, metadata, price, policy, or release changes with noise controls.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill content-change-monitor --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-03 — Repository Release Watcher (`repository-release-watcher`)
- **Capability**: Monitors public or authorized tags, releases, assets, notes, advisories, and compatibility changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill repository-release-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-04 — Dataset Update Watcher (`dataset-update-watcher`)
- **Capability**: Detects new versions, schema changes, rows, fields, dates, licenses, and download integrity for selected datasets.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill dataset-update-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-05 — Cloud Storage Change Watcher (`cloud-storage-change-watcher`)
- **Capability**: Observes authorized folder or object additions, changes, deletions, versions, and permission changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cloud-storage-change-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-06 — Application Health Watcher (`application-health-watcher`)
- **Capability**: Monitors approved logs, status endpoints, queues, errors, resources, and service-level indicators.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill application-health-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-07 — Social Content Watcher (`social-content-watcher`)
- **Capability**: Tracks bounded public accounts, keywords, or hashtags within platform rules and reports only relevant changes.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill social-content-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-08 — Price and Availability Watcher (`price-and-availability-watcher`)
- **Capability**: Monitors public product prices or stock with timestamps, region, seller, variants, and policy- compliant frequency.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill price-and-availability-watcher --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-09 — Alert Deduplication and Routing Designer (`alert-deduplication-and-routing-designer`)
- **Capability**: Groups repeated signals, applies severity and ownership, suppresses noise, and creates clear escalation paths.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill alert-deduplication-and-routing-designer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

#### cat29-10 — Monitor Validation Tester (`monitor-validation-tester`)
- **Capability**: Tests false positives, missed changes, schedule drift, credentials, rate limits, alert delivery, and recovery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill monitor-validation-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"prior_row_count":10}'\'''
```

### Category 30: Communication, Messaging, and Collaboration Tools
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat30`

#### cat30-01 — Email Reader and Thread Summarizer (`email-reader-and-thread-summarizer`)
- **Capability**: Reads authorized mail, reconstructs threads, identifies decisions, requests, attachments, deadlines, and unresolved items.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill email-reader-and-thread-summarizer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-02 — Email Draft and Reply Operator (`email-draft-and-reply-operator`)
- **Capability**: Creates replies using supplied context, but requires confirmation before sending unless explicit standing authorization exists.
- **Posture & Risk**: `publishing` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill email-draft-and-reply-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-03 — Chat and Channel Reader (`chat-and-channel-reader`)
- **Capability**: Summarizes authorized workspace conversations, decisions, links, files, owners, and open questions with privacy controls.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill chat-and-channel-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-04 — Collaboration File Comment Reader (`collaboration-file-comment-reader`)
- **Capability**: Extracts comments, mentions, resolutions, suggestions, and document anchors from supported collaborative files.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill collaboration-file-comment-reader --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-05 — Meeting Artifact Interpreter (`meeting-artifact-interpreter`)
- **Capability**: Reads agendas, notes, transcripts, recordings when available, decisions, actions, owners, dates, and supporting links.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill meeting-artifact-interpreter --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-06 — Message Search Specialist (`message-search-specialist`)
- **Capability**: Finds authorized messages by participant, channel, date, keyword, link, attachment, or topic while respecting scope.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill message-search-specialist --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-07 — Notification Triage Operator (`notification-triage-operator`)
- **Capability**: Groups alerts, removes duplicates, ranks urgency, identifies required actions, and preserves links to original items.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill notification-triage-operator --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-08 — Collaboration Workflow Tester (`collaboration-workflow-tester`)
- **Capability**: Tests mentions, sharing, comments, approvals, notifications, permissions, and version behavior in approved workspaces.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill collaboration-workflow-tester --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-09 — Attachment and Link Safety Reviewer (`attachment-and-link-safety-reviewer`)
- **Capability**: Checks attachment identity, type, source, destination, access, and obvious risks before opening or forwarding.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill attachment-and-link-safety-reviewer --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

#### cat30-10 — Communication Action Confirmation Manager (`communication-action-confirmation-manager`)
- **Capability**: Reviews recipients, content, attachments, visibility, timing, and consequences before messages or posts are sent.
- **Posture & Risk**: `publishing` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill communication-action-confirmation-manager --input data-web-app-interaction/asset-sample-events.jsonl --params '\''{"keyword":"Retry-After"}'\'''
```

### Category 31: Transactions, Commerce, and Account Workflows
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat31`

#### cat31-01 — Product Comparison Researcher (`product-comparison-researcher`)
- **Capability**: Compares current public specifications, prices, availability, warranties, reviews, and compatibility using source-grounded evidence.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill product-comparison-researcher --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-02 — Shopping Cart Workflow Tester (`shopping-cart-workflow-tester`)
- **Capability**: Tests add, remove, quantity, variant, price, tax, shipping estimate, discounts, persistence, and error handling without purchasing.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill shopping-cart-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-03 — Checkout Test Environment Operator (`checkout-test-environment-operator`)
- **Capability**: Tests approved sandbox transactions, payment states, validation, confirmation, receipts, and failure recovery.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill checkout-test-environment-operator --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-04 — Subscription Workflow Tester (`subscription-workflow-tester`)
- **Capability**: Tests authorized trial, upgrade, downgrade, renewal, pause, cancellation, billing history, and confirmation behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill subscription-workflow-tester --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-05 — Account Settings Navigator (`account-settings-navigator`)
- **Capability**: Finds and changes only approved profile, privacy, notification, security, or preference settings with before-and-after verification.
- **Posture & Risk**: `consequential` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill account-settings-navigator --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-06 — Booking and Reservation Researcher (`booking-and-reservation-researcher`)
- **Capability**: Finds current options, constraints, cancellation terms, total prices, schedules, and accessibility but requires confirmation before booking.
- **Posture & Risk**: `financial` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill booking-and-reservation-researcher --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-07 — Marketplace Listing Reader (`marketplace-listing-reader`)
- **Capability**: Extracts seller, condition, location, price, shipping, returns, authenticity claims, and listing history from public pages.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill marketplace-listing-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-08 — Coupon and Promotion Validator (`coupon-and-promotion-validator`)
- **Capability**: Checks eligibility, dates, exclusions, stacking, region, minimums, and final price in non- purchase or sandbox flows.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill coupon-and-promotion-validator --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-09 — Transaction Receipt and Record Reader (`transaction-receipt-and-record-reader`)
- **Capability**: Interprets user-provided receipts, invoices, confirmations, line items, taxes, fees, dates, and identifiers.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill transaction-receipt-and-record-reader --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

#### cat31-10 — Financial Consequence Gatekeeper (`financial-consequence-gatekeeper`)
- **Capability**: Requires explicit final approval before charges, transfers, purchases, renewals, refunds, or binding financial commitments.
- **Posture & Risk**: `financial` — Requires `--approve` when mutating/committing.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill financial-consequence-gatekeeper --input data-web-app-interaction/asset-sample-page.html --params '\''{"environment":"sandbox","subtotal_usd":98.0}'\'''
```

### Category 32: Geospatial, Maps, and Location-Based Interaction
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat32`

#### cat32-01 — Interactive Map Reader (`interactive-map-reader`)
- **Capability**: Uses authorized map layers, markers, routes, labels, legends, scale, and place details to answer spatial questions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill interactive-map-reader --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-02 — Route Comparison Analyst (`route-comparison-analyst`)
- **Capability**: Compares travel modes, distance, duration, transfers, tolls, accessibility, and time context using current sources.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill route-comparison-analyst --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-03 — Location Search Specialist (`location-search-specialist`)
- **Capability**: Finds places by category, proximity, hours, services, ratings, accessibility, and user constraints.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill location-search-specialist --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-04 — Geocoding and Address Validator (`geocoding-and-address-validator`)
- **Capability**: Normalizes supplied addresses, checks candidate matches, coordinates, administrative areas, and ambiguity.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill geocoding-and-address-validator --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-05 — Map Layer and Legend Interpreter (`map-layer-and-legend-interpreter`)
- **Capability**: Explains symbols, colors, thresholds, boundaries, dates, projections, and source limitations.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill map-layer-and-legend-interpreter --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-06 — Geospatial File Interaction Specialist (`geospatial-file-interaction-specialist`)
- **Capability**: Reads KML, GeoJSON, GPX, shapefiles, tiles, and coordinate tables with projection awareness.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill geospatial-file-interaction-specialist --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-07 — Location-Based App Tester (`location-based-app-tester`)
- **Capability**: Tests maps, location permissions, geofences, routing, offline maps, accuracy, backgrounding, and privacy behavior.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill location-based-app-tester --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-08 — Route Playback and Track Analyst (`route-playback-and-track-analyst`)
- **Capability**: Interprets authorized GPS tracks for timing, stops, speed, distance, elevation, gaps, and anomalies.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill route-playback-and-track-analyst --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-09 — Spatial Data Quality Auditor (`spatial-data-quality-auditor`)
- **Capability**: Checks coordinate range, projection, duplicates, invalid geometry, topology, precision, and missing location context.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill spatial-data-quality-auditor --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

#### cat32-10 — Location Privacy Reviewer (`location-privacy-reviewer`)
- **Capability**: Minimizes precise-location exposure, checks sharing and retention, and avoids publishing sensitive routes or home locations.
- **Posture & Risk**: `security-defensive` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill location-privacy-reviewer --input data-web-app-interaction/asset-sample-spatial.geojson --params '\''{"lat":14.4499,"lon":120.9820}'\'''
```

### Category 33: End-to-End Agent Orchestration and Evidence
- **Engine Module**: `data-web-app-interaction/tools-net-sec-ops.py` | **Category ID**: `cat33`

#### cat33-01 — Interactive Task Decomposer (`interactive-task-decomposer`)
- **Capability**: Breaks a broad request into authorized discovery, interaction, data, testing, validation, and delivery stages with dependencies.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill interactive-task-decomposer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-02 — Tool and Interface Router (`tool-and-interface-router`)
- **Capability**: Selects browser, API, filesystem, document, data, media, automation, testing, or simulation tools according to the real target.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill tool-and-interface-router --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-03 — Observation and Action Ledger (`observation-and-action-ledger`)
- **Capability**: Records what was observed, what action was taken, resulting state, evidence, uncertainty, and next safe step.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill observation-and-action-ledger --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-04 — Checkpoint and Recovery Coordinator (`checkpoint-and-recovery-coordinator`)
- **Capability**: Preserves stable state, records progress, and resumes after interruption without repeating consequential actions.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill checkpoint-and-recovery-coordinator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-05 — Cross-System Workflow Integrator (`cross-system-workflow-integrator`)
- **Capability**: Coordinates approved data movement and task state across websites, APIs, cloud storage, repositories, files, apps, and reports.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill cross-system-workflow-integrator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-06 — Requirement Coverage Auditor (`requirement-coverage-auditor`)
- **Capability**: Maps every requested search, file, action, test, scenario, format, and deliverable to a completed item or stated limitation.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill requirement-coverage-auditor --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-07 — Evidence and Provenance Packager (`evidence-and-provenance-packager`)
- **Capability**: Packages source links, file references, timestamps, screenshots when permitted, logs, test cases, and transformed data for review.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill evidence-and-provenance-packager --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-08 — Normal Boundary and Failure Scenario Designer (`normal-boundary-and-failure-scenario-designer`)
- **Capability**: Creates representative, extreme, missing, interrupted, repeated, permission, and recovery cases for interactive systems.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill normal-boundary-and-failure-scenario-designer --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-09 — Joined Workflow Acceptance Tester (`joined-workflow-acceptance-tester`)
- **Capability**: Runs the entire use path from input through interaction, transformation, output, reopen, and recipient handoff.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill joined-workflow-acceptance-tester --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

#### cat33-10 — Clean Deliverable Curator (`clean-deliverable-curator`)
- **Capability**: Removes scratch data and secrets, verifies names and structure, preserves editable outputs, and delivers only the requested artifacts.
- **Posture & Risk**: `read-only` — Read-only / safe-copy operation by default.
- **Example Command**:
```bash
bash -c 'python3 data-web-app-interaction/tools-cli.py run --skill clean-deliverable-curator --input data-web-app-interaction/asset-sample-records.csv --params '\''{"step_id":"stage-01"}'\'''
```

## 6. Validation and Tests Actually Completed

The following validations were executed and verified in `Python 3.13.16` on Linux:
1. **Help & Capability Listing**: Verified `tools-cli.py --help`, `tools-cli.py run --help`, and `tools-cli.py capabilities` across `json`, `table`, and `markdown` formats.
2. **Full 330-Capability Execution (`verify-all`)**: Executed all 330 registered capabilities across all 33 categories via `tools-cli.py verify-all`, confirming `330/330` passed with `0` missing handlers and `0` failures.
3. **Input Catalog Parser (`parse-catalog`)**: Verified extraction of categories, skill names, descriptions, and umbrella slugs from copied HTML containing `<style>`, CSS classes, `data-*` attributes, and numbering prefixes.
4. **Pre-Execution Validation & Boundary Blocking**: Verified structured `validation_error` (exit code `2`) on missing `--input` files, malformed `--params` JSON, and unknown skill identifiers; verified `blocked_by_policy` (exit code `3`) on parent-directory dot-dot traversal inputs and Zip-Slip traversal archive entries.
5. **Approval Gate & Human-Handoff Enforcement**: Verified `authorized-file-uploader` and `financial-consequence-gatekeeper` block unapproved consequential execution with `approval_required` (exit code `3`) and succeed when `--approve` is passed; verified `g-recaptcha` HTML triggers `human_handoff_required` (exit code `3`).
6. **Secret Redaction**: Verified Bearer tokens and `ghp_*` tokens are scrubbed from structured output with `redactions_applied >= 2`.
7. **Input-Change Sensitivity**: Verified fixing `.muted-note` contrast in HTML changes `web-accessibility-reading-tester` issue count from `1` to `0`; verified changing Monte Carlo `mean_ms` from `100.0` to `300.0` shifts simulated median from `~100ms` to `~300ms`; verified generated binary `.wav` PCM audio, `.docx` OOXML, and `.pcap` packet captures parse accurately.
8. **Checkpoint Crash Recovery & Ledger Chaining**: Executed a 2-step `workflow` writing a checkpoint and hash-chained JSONL ledger, appended a 3rd step, re-ran against the same checkpoint, and confirmed steps 1–2 skipped replay (`steps_resumed_from_checkpoint: 2`) while step 3 executed cleanly.
9. **Clean Skill Root & ZIP Round-Trip**: Verified relative-path execution beside a temporary root `SKILL.md`, confirmed zero subfolders (`__pycache__` suppressed), zero unprefixed files, and verified extracted ZIP structure and rerun parity.

## 7. Limits and Unavailable External Integrations

- **Live External Network Disabled by Default**: `network.allow_live_network` defaults to `false` in `data-web-app-interaction/asset-default-config.json` so runs are deterministic, offline-safe, and never contact external servers unexpectedly. Set `allow_live_network: true` and pass `live_request: true` in `--params` for authorized HTTP inspection.
- **External Search, SaaS, Cloud, and Model Credentials**: No third-party API keys, OAuth tokens, cloud storage credentials, or LLM endpoints are bundled. Provider slots in `data-web-app-interaction/asset-default-config.json` (`DWAI_SEARCH_API_KEY`, `DWAI_MODEL_API_KEY`) remain configurable via environment variables.
- **Headed Desktop GUI, Physical Mobile Handsets, and Live Game Binaries**: Headless server environments lack attached X11/Wayland desktop sessions, ADB/iOS physical handsets, GPU render windows, and browser engines unless separately installed. Capabilities in Categories 18–21 evaluate supplied UI trees, viewport geometries, crash/ANR traces, quest graphs, frame-time telemetry, economy flows, and localization tables, and explicitly report environment limitations in `limitations`.
- **System OCR Binary**: Direct raster image OCR in `scanned-document-ocr-reader` uses system `tesseract` when present on `PATH`, falling back cleanly to structured OCR page blocks otherwise.

## 8. Troubleshooting, Recovery, Extension, and Maintenance

- **`validation_error` on `--input` or `--config`**: Ensure you are running from the parent skill root directory (the directory containing `data-web-app-interaction/`) using forward-slash relative paths without `..` segments.
- **`approval_required` (Exit Code 3)**: Review the returned `data.details` preview carefully; if owner permission and target scope are confirmed, re-run with `--approve --approval-note "<scope>"`.
- **Resuming Interrupted Workflows**: Always pass `--checkpoint-path <file.json>` and `--ledger-path <file.jsonl>` to `workflow` or `run --idempotency-key <key>`. Completed steps and idempotency digests are persisted atomically via `os.replace`, preventing duplicate side effects upon restart.
- **Adding or Extending Capabilities**: Register new capability metadata in `data-web-app-interaction/asset-skill-catalog.json`, add its handler function to the matching domain module (for example, `data-web-app-interaction/tools-net-sec-ops.py`) `SKILL_HANDLERS` dictionary, and run `bash -c 'python3 data-web-app-interaction/tools-cli.py verify-all'` plus `clean-deliverable-curator` to confirm 100% coverage and naming compliance.
