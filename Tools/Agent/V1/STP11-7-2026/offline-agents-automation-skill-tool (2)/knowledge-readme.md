# Offline Agents and Automation (`offline-agents-automation`)

## 1. Purpose and Overview

`offline-agents-automation` is a self-contained, reusable production Skill Tool implementing the complete **Full Offline Agents and Automation** capability blueprint across **32 categories** and **450 capabilities**. It provides air-gapped local agent execution, model serving configuration and routing, deterministic embeddings and hybrid BM25 + vector retrieval, grounded RAG with indirect prompt-injection defense and explicit abstention, durable SQLite WAL memory and DAG workflows, RBAC/ABAC policy enforcement, single and dual-control human approval gates, PBKDF2-HMAC-SHA256 + MAC secret vaulting, AST static code screening, archive-bomb detection, SPDX/CycloneDX SBOM generation, tamper-evident hash-chained audit logging, disaster recovery drills, and catalog governance.

This folder is a reusable Skill Tool component intended to sit beside a parent skill package's root `SKILL.md` (`<skill-root>/offline-agents-automation/`). It intentionally contains no `SKILL.md` and no internal subdirectories.

## 2. Package Architecture, File Roles, and Loading Order

All **14 files** reside directly inside the  folder`offline-agents-automation/` using strict `tools-`, `knowledge-`, and `asset-` prefixes:

### Primary Entry Point and Supporting `tools-*` Modules
1. `offline-agents-automation/tools-offline-agent-cli.py` — **Primary runnable CLI entry point**. Parses arguments, enforces input byte caps, routes `help`, `capabilities`, `inspect`, `parse-catalog`, `run`, `approve`, `workflow`, and `selftest` commands, validates output against `ExecutionEnvelope`, and writes atomic output files.
2. `offline-agents-automation/tools-core-foundation.py` — **Loaded first by the CLI**. Disables Python bytecode cache generation (`sys.dont_write_bytecode = True`), resolves relative `offline-agents-automation/asset-default-config.json` resources without parent traversal, validates JSON Schemas, parses plain-text/Markdown/HTML capability catalogs, computes deterministic 64-D embeddings + BM25 + RRF fusion, provides PBKDF2-HMAC-SHA256-CTR + HMAC-SHA256 authenticated encryption and secret redaction, manages the SQLite WAL durable state store, enforces RBAC/ABAC and single/dual-control approvals, and appends SHA-256 hash-chained audit events.
3. `offline-agents-automation/tools-catalog-registry.py` — **Loaded second by the CLI**. Holds the authoritative metadata, risk classifications (`read-only`, `reversible`, `approval-required`, `dual-control`), default timeouts, permissions, and lookup/filter indexes for all 32 categories and 450 capabilities.
4. `offline-agents-automation/tools-engine-platform-models.py` — **Loaded on demand for Categories 1–5 (70 capabilities)**. Implements offline stack packaging, baseline/drift detection, quantization/VRAM math, context budgeting, chunking, corpus indexing, hybrid retrieval, SVG/WAV/bbox multimodal utilities, and serving capacity planning.
5. `offline-agents-automation/tools-engine-runtime-security.py` — **Loaded on demand for Categories 6–10 & 22–23 (96 capabilities)**. Implements the 7-state agent FSM, checkpoints, idempotency ledger, tool contracts, bounded isolated subprocess execution, Python AST static security scanning, RBAC/ABAC/HMAC capability tokens, encrypted secret vaulting, archive-bomb inspection, CVE advisory matching, SBOM generation, and air-gap compliance checks.
6. `offline-agents-automation/tools-engine-knowledge-workflows.py` — **Loaded on demand for Categories 11–18 (112 capabilities)**. Implements scoped durable memory with tombstones, knowledge graph traversal, grounded RAG with citation anchors and abstention, Kahn topological DAG workflows, anti-starvation priority queues, circuit breakers, saga compensations, human approval workflows, and multi-agent coordination.
7. `offline-agents-automation/tools-engine-quality-governance.py` — **Loaded on demand for Categories 19–21 & 24–32 (172 capabilities)**. Implements typed prompt compilation, benchmark harness and Wilson 95% confidence intervals, safe AST numerical verification, factual repair, audit timeline verification, budgets, CSV/EML/document ingest, atomic temp-file + fsync + backup file mutation, PII minimization, anomaly detection, SLO error budgets, DR drills, sealed transfer bundles, and 450-skill catalog governance.

### Production `asset-*` Resources
- `offline-agents-automation/asset-default-config.json` — Default offline runtime settings, state paths, offline env flags, loopback service allowlist, model profiles, context budgets, retrieval thresholds, retries, timeouts, and quotas.
- `offline-agents-automation/asset-security-policy.json` — RBAC roles (`viewer`, `operator`, `approver`, `security-reviewer`, `admin`), data classification obligations, Separation-of-Duties approval rules, executable allowlist, blocked AST symbols/imports, archive-bomb limits, secret/injection regexes, and offline vulnerability advisories.
- `offline-agents-automation/asset-schemas.json` — Strict JSON Schemas (`ExecutionEnvelope`, `ToolContract`, `WorkflowDefinition`, `GroundedAnswerContract`, `AgentMessage`, `SkillInterfaceStandard`).
- `offline-agents-automation/asset-prompt-templates.json` — Versioned system/RAG/planner/repair prompt templates with typed variables, locales, and few-shot examples.
- `offline-agents-automation/asset-workflow-presets.json` — Ready-to-run DAG workflows (`airgap-grounded-rag-pipeline`, `approved-transactional-mutation`, `offline-release-readiness-gate`).
- `offline-agents-automation/asset-reference-corpus-and-benchmarks.json` — Approved reference documents, quarantined adversarial fixture, and golden evaluation benchmark cases.

## 3. Setup, Dependencies, and Exact Run Commands

### Runtime & Dependencies
- **Required Runtime**: Python `>= 3.10` (validated on Python `3.13.16`) with standard library `sqlite3` enabled.
- **Required Third-Party Packages**: **None** (100% Python standard library for air-gapped portability; no `pip install` required).
- **Optional Local Binaries** (configurable, never required for core operation): `llama.cpp` (`llama-server`), `ollama`, `vllm`, `tesseract`, `whisper-cli`, `nvidia-smi` / `rocm-smi`.

### Exact Shell Commands (Run from Future Skill Root `<skill-root>/`)
```bash
# 1. Built-in help
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py --help'
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py help'

# 2. List all 32 categories or filter the 450 capabilities
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py capabilities --categories-only'
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py capabilities --category 13'
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py capabilities --risk dual-control'

# 3. Inspect a capability contract
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py inspect rag-pipeline-architect'

# 4. Run end-to-end offline self-test
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py selftest --state-dir .offline-agent-state'

# 5. Run a capability with configurable --input, --output, and --state-dir
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run rag-pipeline-architect --params "{\"query\":\"What are the RPO and RTO targets?\"}" --state-dir .offline-agent-state --output rag-result.json'

# 6. Parse plain-text, Markdown, or copied HTML capability specifications
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py parse-catalog --text "<div class=\"cat\">1. Security<br>1. Skill Name: Log Scanner<br>Description: Scans local logs.</div>"'

# 7. Execute a durable DAG workflow preset
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py workflow --preset airgap-grounded-rag-pipeline --state-dir .offline-agent-state'
```

## 4. Inputs, Outputs, Parameters, Schemas, and Configuration

### CLI Global Parameters
| Parameter | Default | Description |
| --- | --- | --- |
| `--config` | `offline-agents-automation/asset-default-config.json` | JSON configuration file path |
| `--state-dir` | `.offline-agent-state` | Directory for SQLite WAL DB (`offline-agent-store.sqlite3`), `backups/`, `artifacts/`, `quarantine/`, and `ephemeral-workspaces/` |
| `--input` | `None` | Configurable input file (`.json` merged into params; `.md`/`.txt`/`.html`/`.csv`/`.eml` passed as `content` & `text`) |
| `--output` | `None` | Configurable output path written atomically via temp-file + `os.replace` |
| `--principal` | `operator-local` | Caller identity recorded in audit logs and approval requests |
| `--role` | `operator` | RBAC role (`viewer`, `operator`, `approver`, `security-reviewer`, `admin`) |
| `--format` | `pretty` | Output format (`pretty`, `json`, `summary`) |

### Standard Output Envelope (`ExecutionEnvelope`) and Exit Codes
Every `run` execution validates its output against `ExecutionEnvelope` in `offline-agents-automation/asset-schemas.json`, returning `ok`, `capability`, `category`, `trace_id`, `timestamp_utc`, `result`, `execution_metrics`, and `provenance` (`input_sha256`, `policy_version`, `audit_event_hash`).
- Exit code `0`: Success.
- Exit code `2`: Input, JSON syntax, missing file, path-traversal, or unknown-capability validation error.
- Exit code `3`: RBAC permission denial or paused at `approval-required` / `dual-control` gate.
- Exit code `1`: Runtime, timeout, or circuit-breaker error.

## 5. Safety, RBAC, and Human Approval Rules

1. **Offline Network Denial**: `network_governance.mode` defaults to `offline-deny` (`allow_external_network: false`). Subprocess sandboxes run with `python3 -I -S`, `NO_PROXY=*`, `HF_HUB_OFFLINE=1`, strict timeouts, output caps, and AST static screening that blocks `eval`, `exec`, `pickle`, `marshal`, `ctypes`, `os.system`, and `shell=True`.
2. **Path Safety**: Any path containing `..` parent traversal or null bytes is rejected before work begins.
3. **Mandatory Human Approval & Dual Control**:
   - **30 capabilities** are classified `approval-required` (mutations, migrations, rollbacks, secret vault writes/rotations, exports, network exceptions) and **6 capabilities** are classified `dual-control` (`dual-control-approval-manager`, `emergency-access-manager`, `break-glass-secret-recovery`, `secret-material-destruction-operator`, `decommissioning-operator`, `encryption-key-custodian`).
   - Invoking a gated capability without `--approval-id` does **not** execute the mutation; it creates a pending approval bound to `payload_sha256` and exits with code `3`.
   - **Separation of Duties**: The requester cannot approve their own request (`PermissionError`). `dual-control` requires **two distinct** authorized reviewers (`approver`, `security-reviewer`, or `admin`). Modifying any parameter after approval invalidates `payload_sha256` and blocks execution.

### Approval Workflow Example
```bash
# Step 1: Request or trigger approval gate (exits 3 and returns pending approval_id)
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run file-mutation-safety-manager --principal alice --role operator --params "{\"target_path\":\".offline-agent-state/artifacts/demo.json\",\"content\":\"{\\\"safe\\\":true}\"}"'

# Step 2: Independent approver grants approval
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py approve decide --approval-id apr-XXXXXXXXXXXX --reviewer bob --reviewer-role approver --decision approved'

# Step 3: Execute with verified --approval-id
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run file-mutation-safety-manager --principal alice --role operator --approval-id apr-XXXXXXXXXXXX --params "{\"target_path\":\".offline-agent-state/artifacts/demo.json\",\"content\":\"{\\\"safe\\\":true}\"}"'
```

## 6. Complete Capability List, Explanations, and Examples (32 Categories, 450 Capabilities)

Every capability below can be executed directly with:
`bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run <slug> [options]'`

### Category 1: Offline Installation and Environment Reproduction (`offline-installation-and-environment-reproduction` — 14 capabilities)

Example command for Category 1:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run air-gapped-agent-stack-installer --role admin --dry-run --params '{"baseline_id":"prod-baseline-01"}''
```

- **`air-gapped-agent-stack-installer`** (*Air-Gapped Agent Stack Installer* | `reversible` | `1.1`): Packages model runtimes, Python wheels, system libraries, databases, workflow services, sandbox tools, documentation, checksums, and valid offline licenses.
- **`offline-model-and-package-repository-builder`** (*Offline Model and Package Repository Builder* | `reversible` | `1.2`): Creates a local immutable repository for model weights, tokenizers, adapters, containers, wheels, binaries, and signed manifests.
- **`clean-state-agent-installation-tester`** (*Clean-State Agent Installation Tester* | `read-only` | `1.3`): Installs the complete stack in a fresh operating-system profile or virtual machine and executes a reference agent workflow.
- **`offline-only-acceptance-tester`** (*Offline-Only Acceptance Tester* | `read-only` | `1.4`): Disables all network interfaces and verifies inference, retrieval, tool execution, memory, scheduling, observability, and recovery.
- **`dependency-lockfile-manager`** (*Dependency Lockfile Manager* | `reversible` | `1.5`): Pins operating-system packages, Python dependencies, runtime libraries, database versions, model revisions, and tool binaries.
- **`portable-runtime-packager`** (*Portable Runtime Packager* | `reversible` | `1.6`): Builds relocatable launch scripts, environment variables, configuration files, certificates, and approved dependencies.
- **`multi-os-deployment-planner`** (*Multi-OS Deployment Planner* | `read-only` | `1.7`): Maps Windows, Linux, and macOS runtime differences, paths, permissions, GPU backends, services, and limitations.
- **`update-staging-and-rollback-manager`** (*Update Staging and Rollback Manager* | `approval-required` | `1.8`): Tests model, runtime, driver, database, workflow, and security updates against reference cases before promotion.
- **`environment-baseline-recorder`** (*Environment Baseline Recorder* | `reversible` | `1.9`): Captures hardware, drivers, runtimes, models, prompts, tools, schemas, policies, and service configurations.
- **`offline-license-activation-planner`** (*Offline License Activation Planner* | `read-only` | `1.10`): Documents permitted offline activation, local license servers, borrowed seats, expirations, and emergency fallback.
- **`installation-integrity-verifier`** (*Installation Integrity Verifier* | `reversible` | `1.11`): Validates hashes, signatures, package inventory, executable permissions, model shards, and configuration completeness.
- **`unattended-provisioning-builder`** (*Unattended Provisioning Builder* | `approval-required` | `1.12`): Creates deterministic scripts for repeatable installation, initialization, migration, smoke testing, and rollback.
- **`golden-image-builder`** (*Golden Image Builder* | `approval-required` | `1.13`): Creates a hardened reusable workstation or server image with the passing stack and no transient secrets.
- **`environment-drift-detector`** (*Environment Drift Detector* | `read-only` | `1.14`): Compares a running host with the approved baseline and reports changed packages, policies, models, drivers, and services.

### Category 2: Local Language Models and Inference (`local-language-models-and-inference` — 14 capabilities)

Example command for Category 2:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run local-llm-runtime-manager --role admin --dry-run --params '{"params_billion":8.0,"vram_budget_mb":8192,"context_tokens":8192}''
```

- **`local-llm-runtime-manager`** (*Local LLM Runtime Manager* | `reversible` | `2.1`): Configures llama.cpp, vLLM, Ollama, TensorRT-LLM, MLX, or equivalent local serving with explicit model and hardware settings.
- **`model-weight-registry`** (*Model Weight Registry* | `reversible` | `2.2`): Indexes local base models, instruct models, reasoning models, adapters, quantizations, licenses, hashes, and compatibility.
- **`model-format-converter`** (*Model Format Converter* | `reversible` | `2.3`): Converts supported checkpoints among safetensors, GGUF, ONNX, TensorRT, MLX, or runtime-native formats with parity tests.
- **`model-quantization-planner`** (*Model Quantization Planner* | `read-only` | `2.4`): Selects FP16, BF16, FP8, INT8, INT4, GPTQ, AWQ, GGUF, or supported quantization from quality and memory constraints.
- **`context-window-budgeter`** (*Context Window Budgeter* | `reversible` | `2.5`): Allocates tokens among instructions, retrieved evidence, tool results, history, memory, and output with overflow handling.
- **`prompt-cache-manager`** (*Prompt Cache Manager* | `reversible` | `2.6`): Creates and invalidates reusable prefix or KV caches without mixing users, permissions, model versions, or policies.
- **`batch-inference-scheduler`** (*Batch Inference Scheduler* | `reversible` | `2.7`): Groups compatible requests by model, sequence length, priority, deadline, and memory while preserving isolation.
- **`speculative-decoding-configurator`** (*Speculative Decoding Configurator* | `reversible` | `2.8`): Pairs draft and target models, measures acceptance, and disables speculation when quality or latency regresses.
- **`model-routing-engine`** (*Model Routing Engine* | `reversible` | `2.9`): Selects a local model by task, language, modality, context, latency, memory, quality evidence, and tool requirements.
- **`inference-parameter-registry`** (*Inference Parameter Registry* | `reversible` | `2.10`): Versions temperature, top-p, top-k, penalties, seeds, stop rules, grammar constraints, and maximum tokens.
- **`structured-generation-enforcer`** (*Structured Generation Enforcer* | `reversible` | `2.11`): Uses JSON Schema, grammars, constrained decoding, validation, and repair limits to produce machine-consumable output.
- **`long-context-reliability-tester`** (*Long-Context Reliability Tester* | `read-only` | `2.12`): Measures retrieval position, instruction retention, distractor resistance, citation accuracy, and degradation by context length.
- **`model-warmup-and-residency-manager`** (*Model Warmup and Residency Manager* | `reversible` | `2.13`): Preloads models, manages idle eviction, monitors fragmentation, and prevents latency spikes from uncontrolled reloads.
- **`inference-regression-tester`** (*Inference Regression Tester* | `read-only` | `2.14`): Runs fixed prompts and tool traces after changes and compares correctness, latency, memory, format, and refusal behavior.

### Category 3: Embeddings, Reranking, and Semantic Indexing (`embeddings-reranking-and-semantic-indexing` — 14 capabilities)

Example command for Category 3:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run local-embedding-runtime-manager --role admin --dry-run --params '{"query":"What are the RPO and RTO targets?","top_k":3}''
```

- **`local-embedding-runtime-manager`** (*Local Embedding Runtime Manager* | `reversible` | `3.1`): Serves local text, code, image, or multimodal embedding models with pinned preprocessing and dimensions.
- **`embedding-model-registry`** (*Embedding Model Registry* | `reversible` | `3.2`): Records model, revision, tokenizer, pooling, normalization, dimensions, language coverage, license, and evaluation results.
- **`document-chunking-strategist`** (*Document Chunking Strategist* | `reversible` | `3.3`): Selects semantic, structural, sentence, token, code-aware, table-aware, or sliding-window chunking by source type.
- **`chunk-boundary-validator`** (*Chunk Boundary Validator* | `read-only` | `3.4`): Checks broken headings, split tables, orphaned captions, truncated code, duplicated overlap, and missing source identifiers.
- **`embedding-corpus-builder`** (*Embedding Corpus Builder* | `reversible` | `3.5`): Extracts, normalizes, chunks, embeds, and indexes approved local documents with deterministic IDs and provenance.
- **`local-reranker-runtime-manager`** (*Local Reranker Runtime Manager* | `reversible` | `3.6`): Serves cross-encoder or late-interaction rerankers locally and manages candidate count, batching, and latency.
- **`hybrid-retrieval-builder`** (*Hybrid Retrieval Builder* | `reversible` | `3.7`): Combines BM25, dense vectors, metadata filters, graph links, and reranking with calibrated score fusion.
- **`retrieval-threshold-calibrator`** (*Retrieval Threshold Calibrator* | `read-only` | `3.8`): Sets similarity, rank, abstention, and fallback thresholds using labeled relevant and irrelevant examples.
- **`embedding-drift-detector`** (*Embedding Drift Detector* | `read-only` | `3.9`): Compares neighborhood, recall, score distributions, and downstream answers after model or preprocessing changes.
- **`multilingual-retrieval-optimizer`** (*Multilingual Retrieval Optimizer* | `reversible` | `3.10`): Tests cross-language queries, transliteration, language-specific chunking, and reranking across approved languages.
- **`code-retrieval-indexer`** (*Code Retrieval Indexer* | `reversible` | `3.11`): Indexes symbols, files, call graphs, comments, tests, errors, and repository structure using code-aware embeddings.
- **`image-and-diagram-retrieval-indexer`** (*Image and Diagram Retrieval Indexer* | `reversible` | `3.12`): Indexes local images, screenshots, figures, OCR text, captions, and multimodal embeddings with rights metadata.
- **`semantic-deduplication-operator`** (*Semantic Deduplication Operator* | `reversible` | `3.13`): Clusters near-duplicate chunks or files while preserving canonical sources, versions, and materially different clauses.
- **`retrieval-evaluation-runner`** (*Retrieval Evaluation Runner* | `reversible` | `3.14`): Measures recall@k, precision@k, MRR, nDCG, answer support, latency, and abstention on labeled queries.

### Category 4: Vision, Speech, and Multimodal Models (`vision-speech-and-multimodal-models` — 14 capabilities)

Example command for Category 4:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run local-vision-language-runtime-manager --role admin --dry-run --params '{"svg_or_text":"<svg><text>AIR-GAP READY</text></svg>","audio_duration_s":2.4}''
```

- **`local-vision-language-runtime-manager`** (*Local Vision-Language Runtime Manager* | `reversible` | `4.1`): Serves local VLMs with pinned image preprocessing, resolution, tiling, context, quantization, and device settings.
- **`image-understanding-router`** (*Image Understanding Router* | `reversible` | `4.2`): Selects OCR, object detection, segmentation, captioning, document vision, chart reading, or general VLM analysis.
- **`offline-ocr-pipeline-builder`** (*Offline OCR Pipeline Builder* | `reversible` | `4.3`): Runs local OCR with orientation, layout, language, confidence, table, handwriting, and reading-order handling.
- **`visual-grounding-validator`** (*Visual Grounding Validator* | `read-only` | `4.4`): Checks that object, region, text, count, and spatial claims are tied to visible evidence or bounding regions.
- **`local-speech-recognition-manager`** (*Local Speech Recognition Manager* | `reversible` | `4.5`): Serves Whisper or equivalent offline ASR with language, diarization, timestamps, vocabulary, and hardware settings.
- **`speech-diarization-operator`** (*Speech Diarization Operator* | `reversible` | `4.6`): Separates local speakers, handles overlap, merges segments, and exposes confidence without inferring identity.
- **`speech-timestamp-aligner`** (*Speech Timestamp Aligner* | `reversible` | `4.7`): Creates word or phoneme timing and validates drift against the original waveform.
- **`local-text-to-speech-manager`** (*Local Text-to-Speech Manager* | `reversible` | `4.8`): Serves authorized offline neural voices with pronunciation, pace, style, consent, and provenance controls.
- **`audio-event-classifier`** (*Audio Event Classifier* | `read-only` | `4.9`): Detects approved sound classes, silence, clipping, alarms, music, speech, and environmental events locally.
- **`multimodal-input-normalizer`** (*Multimodal Input Normalizer* | `reversible` | `4.10`): Converts images, audio, video, PDFs, and office files into stable local representations with metadata and checksums.
- **`cross-modal-evidence-joiner`** (*Cross-Modal Evidence Joiner* | `reversible` | `4.11`): Links transcript segments, video frames, document pages, image regions, and timestamps into one evidence graph.
- **`multimodal-safety-filter`** (*Multimodal Safety Filter* | `reversible` | `4.12`): Screens images, audio, video, and extracted text before models or tools consume them.
- **`modality-fallback-manager`** (*Modality Fallback Manager* | `reversible` | `4.13`): Routes to OCR, ASR, deterministic parsers, or human review when a general multimodal model is unreliable.
- **`multimodal-regression-suite`** (*Multimodal Regression Suite* | `reversible` | `4.14`): Tests image, audio, document, and mixed-input tasks after model, codec, or preprocessing changes.

### Category 5: Model Serving, Capacity, and Hardware Control (`model-serving-capacity-and-hardware-control` — 14 capabilities)

Example command for Category 5:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run model-serving-gateway --role admin --dry-run --params '{"model_id":"local-instruct-8b-q4","batch_size":2,"available_vram_mb":8192}''
```

- **`model-serving-gateway`** (*Model Serving Gateway* | `reversible` | `5.1`): Exposes stable local APIs for chat, completion, embeddings, reranking, vision, speech, and structured generation.
- **`gpu-backend-router`** (*GPU Backend Router* | `reversible` | `5.2`): Selects CUDA, ROCm, DirectML, Metal, oneAPI, CPU, or supported acceleration and verifies actual device use.
- **`vram-admission-controller`** (*VRAM Admission Controller* | `reversible` | `5.3`): Estimates model, KV cache, batch, adapter, and multimodal memory before accepting or queueing a request.
- **`ram-and-swap-guard`** (*RAM and Swap Guard* | `reversible` | `5.4`): Limits host memory use, detects pressure, prevents destructive swapping, and applies controlled model eviction.
- **`concurrency-limit-manager`** (*Concurrency Limit Manager* | `reversible` | `5.5`): Sets per-model, per-user, per-workflow, and global request limits with queues and backpressure.
- **`latency-service-level-monitor`** (*Latency Service-Level Monitor* | `read-only` | `5.6`): Tracks queue, preprocessing, first-token, generation, tool, retrieval, and end-to-end latency against local objectives.
- **`throughput-benchmark-runner`** (*Throughput Benchmark Runner* | `read-only` | `5.7`): Measures tokens per second, requests per second, batch efficiency, GPU utilization, and tail latency.
- **`model-replica-coordinator`** (*Model Replica Coordinator* | `reversible` | `5.8`): Starts, health-checks, drains, replaces, and balances local model replicas without dropping durable work.
- **`graceful-degradation-manager`** (*Graceful Degradation Manager* | `reversible` | `5.9`): Falls back to smaller models, reduced context, lower resolution, delayed jobs, or human review under resource pressure.
- **`hardware-failure-detector`** (*Hardware Failure Detector* | `read-only` | `5.10`): Identifies GPU resets, ECC errors, thermal throttling, disk faults, out-of-memory events, and driver failures.
- **`energy-and-thermal-budget-manager`** (*Energy and Thermal Budget Manager* | `reversible` | `5.11`): Caps device power, concurrency, and job duration to protect workstation stability and thermal limits.
- **`serving-health-check-builder`** (*Serving Health Check Builder* | `reversible` | `5.12`): Tests model load, tokenizer parity, deterministic probes, structured output, tool schema, and representative inference.
- **`capacity-forecast-planner`** (*Capacity Forecast Planner* | `read-only` | `5.13`): Projects storage, RAM, VRAM, compute, queue time, and growth from users, workflows, models, and context sizes.
- **`inference-resource-accounting`** (*Inference Resource Accounting* | `read-only` | `5.14`): Records actual CPU, GPU, memory, storage, and wall time by model, agent, workflow, user, and job.

### Category 6: Agent Runtime and State Machines (`agent-runtime-and-state-machines` — 14 capabilities)

Example command for Category 6:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run agent-runtime-architect --role admin --dry-run --params '{"current_state":"perceive","next_state":"plan"}''
```

- **`agent-runtime-architect`** (*Agent Runtime Architect* | `reversible` | `6.1`): Defines the perceive, plan, act, observe, verify, persist, and terminate states with explicit transition rules.
- **`agent-state-persistence-manager`** (*Agent State Persistence Manager* | `reversible` | `6.2`): Stores durable task, step, tool, memory, approval, artifact, and error state for restartable execution.
- **`finite-state-workflow-builder`** (*Finite-State Workflow Builder* | `reversible` | `6.3`): Implements deterministic states and guards where unrestricted model planning would be unsafe or hard to recover.
- **`plan-compiler`** (*Plan Compiler* | `reversible` | `6.4`): Converts accepted model plans into typed executable steps with dependencies, inputs, outputs, permissions, and checks.
- **`step-result-validator`** (*Step Result Validator* | `read-only` | `6.5`): Validates schema, meaning, artifact existence, side effects, and expected state before advancing.
- **`agent-termination-controller`** (*Agent Termination Controller* | `reversible` | `6.6`): Stops on success, refusal, cancellation, deadline, budget, repeated failure, unsafe state, or unresolved human decision.
- **`checkpoint-and-resume-manager`** (*Checkpoint and Resume Manager* | `reversible` | `6.7`): Creates consistent checkpoints and resumes from the last verified step rather than repeating completed side effects.
- **`idempotency-key-manager`** (*Idempotency Key Manager* | `reversible` | `6.8`): Prevents duplicate sends, writes, purchases, deployments, or mutations during retries and restarts.
- **`side-effect-ledger`** (*Side-Effect Ledger* | `reversible` | `6.9`): Records intended, attempted, confirmed, reversed, and uncertain changes made by tools or external processes.
- **`agent-loop-detector`** (*Agent Loop Detector* | `read-only` | `6.10`): Detects repeated plans, tool calls, observations, or unchanged failures and forces alternate handling or escalation.
- **`state-migration-manager`** (*State Migration Manager* | `approval-required` | `6.11`): Upgrades persisted agent state across schema, prompt, tool, and workflow versions with rollback.
- **`task-cancellation-coordinator`** (*Task Cancellation Coordinator* | `reversible` | `6.12`): Propagates cancellation to queued, running, child, and external jobs while preserving audit and recoverable state.
- **`agent-session-isolation-manager`** (*Agent Session Isolation Manager* | `reversible` | `6.13`): Separates users, projects, permissions, memory, files, tools, and model contexts.
- **`deterministic-replay-runner`** (*Deterministic Replay Runner* | `reversible` | `6.14`): Replays recorded model outputs or fixed stubs to test workflow logic and side effects without nondeterministic inference.

### Category 7: Tool Registry, Schemas, and Execution Adapters (`tool-registry-schemas-and-execution-adapters` — 14 capabilities)

Example command for Category 7:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run tool-schema-registry --role admin --dry-run --params '{"operation_description":"delete expired archive records"}''
```

- **`tool-schema-registry`** (*Tool Schema Registry* | `reversible` | `7.1`): Versions tool names, descriptions, JSON Schemas, permissions, side effects, timeouts, costs, and deprecation state.
- **`tool-adapter-builder`** (*Tool Adapter Builder* | `reversible` | `7.2`): Wraps local CLI, Python, database, filesystem, application, or service operations behind typed inputs and outputs.
- **`tool-capability-router`** (*Tool Capability Router* | `reversible` | `7.3`): Selects the smallest capable tool by task, permissions, data locality, format fidelity, reliability, and cost.
- **`tool-input-validator`** (*Tool Input Validator* | `read-only` | `7.4`): Rejects malformed, ambiguous, out-of-range, unauthorized, or dangerous arguments before execution.
- **`tool-output-normalizer`** (*Tool Output Normalizer* | `reversible` | `7.5`): Converts stdout, files, tables, images, errors, and metadata into stable result envelopes.
- **`tool-side-effect-classifier`** (*Tool Side-Effect Classifier* | `read-only` | `7.6`): Labels read-only, reversible, destructive, external, privileged, costly, and approval-required operations.
- **`tool-timeout-and-cancellation-manager`** (*Tool Timeout and Cancellation Manager* | `reversible` | `7.7`): Sets soft and hard deadlines, kills child processes safely, and preserves partial diagnostics.
- **`tool-version-compatibility-checker`** (*Tool Version Compatibility Checker* | `read-only` | `7.8`): Tests adapters against exact tool versions, flags changed flags or schemas, and maintains migration notes.
- **`tool-mock-and-stub-generator`** (*Tool Mock and Stub Generator* | `reversible` | `7.9`): Creates deterministic substitutes for testing workflow logic without real side effects or unavailable systems.
- **`tool-contract-test-runner`** (*Tool Contract Test Runner* | `reversible` | `7.10`): Verifies schemas, exit codes, artifacts, side effects, errors, idempotency, timeouts, and cleanup.
- **`tool-deprecation-manager`** (*Tool Deprecation Manager* | `reversible` | `7.11`): Marks replacements, warns callers, blocks unsafe old versions, and tracks removal deadlines.
- **`tool-documentation-generator`** (*Tool Documentation Generator* | `reversible` | `7.12`): Creates concise usage, examples, permissions, failure modes, rollback, and validation guidance from the registry.
- **`tool-result-provenance-recorder`** (*Tool Result Provenance Recorder* | `reversible` | `7.13`): Links every result to tool version, arguments, input hashes, runtime, host, permissions, and produced artifacts.
- **`tool-availability-health-monitor`** (*Tool Availability Health Monitor* | `read-only` | `7.14`): Periodically tests launch, permissions, dependencies, disk access, database access, and representative operations.

### Category 8: Sandboxing and Code Execution (`sandboxing-and-code-execution` — 14 capabilities)

Example command for Category 8:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run tool-execution-sandbox --role admin --dry-run --params '{"code":"def compute(x):\n    return x * 2\n"}''
```

- **`tool-execution-sandbox`** (*Tool Execution Sandbox* | `reversible` | `8.1`): Runs untrusted or generated code in isolated containers, virtual machines, or restricted processes with explicit mounts and limits.
- **`filesystem-sandbox-policy-manager`** (*Filesystem Sandbox Policy Manager* | `reversible` | `8.2`): Exposes only approved read and write paths and blocks traversal, symlink escape, device access, and hidden mounts.
- **`process-isolation-controller`** (*Process Isolation Controller* | `reversible` | `8.3`): Limits users, namespaces, capabilities, system calls, child processes, signals, and process lifetime.
- **`network-isolation-controller`** (*Network Isolation Controller* | `reversible` | `8.4`): Blocks network access by default and allows only explicit destinations, protocols, ports, and time windows when authorized.
- **`resource-quota-enforcer`** (*Resource Quota Enforcer* | `reversible` | `8.5`): Limits CPU, GPU, RAM, process count, disk, file size, runtime, and output volume per execution.
- **`generated-code-static-analyzer`** (*Generated Code Static Analyzer* | `read-only` | `8.6`): Scans syntax, imports, dangerous APIs, obfuscation, secrets, injection, and suspicious persistence before execution.
- **`generated-code-dynamic-monitor`** (*Generated Code Dynamic Monitor* | `read-only` | `8.7`): Observes file, process, network, registry, environment, and system-call behavior during execution.
- **`package-installation-gate`** (*Package Installation Gate* | `reversible` | `8.8`): Allows only approved local packages with pinned hashes, licenses, vulnerability status, and sandbox-compatible dependencies.
- **`ephemeral-workspace-manager`** (*Ephemeral Workspace Manager* | `reversible` | `8.9`): Creates isolated working directories, copies approved inputs, collects declared outputs, and destroys residual state.
- **`sandbox-escape-probe-suite`** (*Sandbox Escape Probe Suite* | `reversible` | `8.10`): Tests path traversal, privilege escalation, container escape, socket access, device access, and unsafe mounts.
- **`code-execution-result-validator`** (*Code Execution Result Validator* | `read-only` | `8.11`): Checks exit status, expected files, schemas, tests, side effects, logs, and cleanup before accepting output.
- **`executable-allowlist-manager`** (*Executable Allowlist Manager* | `reversible` | `8.12`): Permits only approved binaries, interpreters, libraries, and arguments for restricted workflows.
- **`artifact-quarantine-manager`** (*Artifact Quarantine Manager* | `reversible` | `8.13`): Isolates executable, macro-enabled, archive, model, and unknown outputs until security and format checks pass.
- **`reproducible-execution-recorder`** (*Reproducible Execution Recorder* | `reversible` | `8.14`): Stores code, dependencies, environment, command, limits, inputs, outputs, hashes, and test results.

### Category 9: Permission, Identity, and Policy Enforcement (`permission-identity-and-policy-enforcement` — 14 capabilities)

Example command for Category 9:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run role-based-access-controller --role admin --dry-run --params '{"role":"operator","data_classification":"internal"}''
```

- **`role-based-access-controller`** (*Role-Based Access Controller* | `reversible` | `9.1`): Maps users, agents, services, projects, and tools to roles and least-privilege permissions.
- **`attribute-based-policy-engine`** (*Attribute-Based Policy Engine* | `reversible` | `9.2`): Evaluates user, data, project, location, time, risk, tool, and action attributes before authorization.
- **`capability-token-manager`** (*Capability Token Manager* | `reversible` | `9.3`): Issues scoped, short-lived, nontransferable permissions for specific tools, resources, actions, and jobs.
- **`privilege-elevation-workflow`** (*Privilege Elevation Workflow* | `approval-required` | `9.4`): Requires explicit reason, approval, duration, scope, and post-use review for elevated operations.
- **`permission-inheritance-auditor`** (*Permission Inheritance Auditor* | `read-only` | `9.5`): Detects accidental access caused by groups, nested roles, shared folders, service accounts, or default policies.
- **`agent-impersonation-guard`** (*Agent Impersonation Guard* | `reversible` | `9.6`): Prevents an agent from claiming another user, service, authority, approval, or identity.
- **`data-classification-policy-enforcer`** (*Data Classification Policy Enforcer* | `reversible` | `9.7`): Applies public, internal, confidential, restricted, licensed, and personal-data handling rules.
- **`purpose-limitation-gate`** (*Purpose Limitation Gate* | `reversible` | `9.8`): Allows sensitive data and tools only for the approved task purpose and blocks unrelated reuse.
- **`policy-decision-logger`** (*Policy Decision Logger* | `reversible` | `9.9`): Records policy inputs, rule version, decision, reason, obligations, and enforcement outcome.
- **`confused-deputy-defense`** (*Confused Deputy Defense* | `reversible` | `9.10`): Checks that tool authority matches the requesting principal and that retrieved content cannot grant permissions.
- **`cross-tenant-isolation-tester`** (*Cross-Tenant Isolation Tester* | `read-only` | `9.11`): Tests that users, projects, memory, vectors, files, logs, and caches cannot leak across isolation boundaries.
- **`permission-regression-suite`** (*Permission Regression Suite* | `reversible` | `9.12`): Replays allow and deny cases after policy, role, workflow, or tool changes.
- **`emergency-access-manager`** (*Emergency Access Manager* | `dual-control` | `9.13`): Provides controlled break-glass access with strong authentication, narrow scope, expiry, audit, and review.
- **`policy-version-registry`** (*Policy Version Registry* | `reversible` | `9.14`): Versions authorization and safety policies and binds every decision to the exact active revision.

### Category 10: Secrets, Keys, and Cryptographic Material (`secrets-keys-and-cryptographic-material` — 12 capabilities)

Example command for Category 10:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run offline-secret-vault-manager --role admin --dry-run --params '{"text":"Bearer abcdef0123456789abcdef0123"}''
```

- **`offline-secret-vault-manager`** (*Offline Secret Vault Manager* | `approval-required` | `10.1`): Stores passwords, tokens, certificates, license keys, and encryption keys using local encryption and access controls.
- **`secret-injection-controller`** (*Secret Injection Controller* | `approval-required` | `10.2`): Provides secrets to approved processes through short-lived files, handles, or environment channels without exposing them to prompts.
- **`secret-rotation-manager`** (*Secret Rotation Manager* | `approval-required` | `10.3`): Rotates credentials, updates dependents, validates access, and revokes old material without losing durable jobs.
- **`secret-scope-minimizer`** (*Secret Scope Minimizer* | `reversible` | `10.4`): Creates separate credentials by tool, environment, project, user, and action with only needed permissions.
- **`prompt-and-log-secret-redactor`** (*Prompt and Log Secret Redactor* | `reversible` | `10.5`): Detects and masks credentials, private keys, tokens, connection strings, and sensitive configuration before persistence.
- **`secret-leak-scanner`** (*Secret Leak Scanner* | `read-only` | `10.6`): Searches files, repositories, model context, tool outputs, archives, and generated artifacts for exposed secrets.
- **`certificate-lifecycle-manager`** (*Certificate Lifecycle Manager* | `reversible` | `10.7`): Tracks issuance, trust roots, expiry, revocation, offline renewal, and service restart requirements.
- **`encryption-key-custodian`** (*Encryption Key Custodian* | `dual-control` | `10.8`): Manages key generation, wrapping, backup, recovery, rotation, separation of duties, and destruction.
- **`local-tls-configurator`** (*Local TLS Configurator* | `reversible` | `10.9`): Enables authenticated encryption among local services and validates certificates, ciphers, hostnames, and expiry.
- **`secret-access-audit`** (*Secret Access Audit* | `reversible` | `10.10`): Records principal, secret identifier, purpose, tool, time, outcome, and policy without recording the secret value.
- **`break-glass-secret-recovery`** (*Break-Glass Secret Recovery* | `dual-control` | `10.11`): Restores access through approved recovery shares, dual control, audit, and immediate rotation.
- **`secret-material-destruction-operator`** (*Secret Material Destruction Operator* | `dual-control` | `10.12`): Securely removes retired secrets from active stores, backups where policy permits, caches, logs, and temporary files.

### Category 11: Durable Memory and Knowledge Stores (`durable-memory-and-knowledge-stores` — 14 capabilities)

Example command for Category 11:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run durable-memory-database-architect --role admin --dry-run --params '{"content":"Core workflows persist checkpoints in SQLite WAL.","confidence":0.95}''
```

- **`durable-memory-database-architect`** (*Durable Memory Database Architect* | `reversible` | `11.1`): Defines schemas for facts, preferences, decisions, tasks, episodes, artifacts, provenance, confidence, scope, and expiry.
- **`memory-write-gate`** (*Memory Write Gate* | `reversible` | `11.2`): Accepts only validated, useful, scoped, nontransient information and rejects guesses, duplicates, secrets, and unsupported claims.
- **`memory-retrieval-router`** (*Memory Retrieval Router* | `reversible` | `11.3`): Selects semantic, keyword, structured, temporal, graph, or exact lookup based on the query and memory type.
- **`memory-scope-manager`** (*Memory Scope Manager* | `reversible` | `11.4`): Separates user, team, project, agent, workflow, session, and global memory with explicit visibility.
- **`memory-conflict-resolver`** (*Memory Conflict Resolver* | `reversible` | `11.5`): Resolves contradictions using source authority, scope, specificity, recency, evidence, and human decisions.
- **`memory-consolidation-operator`** (*Memory Consolidation Operator* | `reversible` | `11.6`): Merges repeated episodes into durable rules while preserving exceptions, provenance, and supporting examples.
- **`memory-expiry-and-retention-manager`** (*Memory Expiry and Retention Manager* | `reversible` | `11.7`): Applies time-to-live, legal hold, retention, archival, and deletion policies by memory class.
- **`memory-correction-workflow`** (*Memory Correction Workflow* | `reversible` | `11.8`): Updates or retracts bad memory, records the reason, invalidates dependent indexes, and prevents resurrection.
- **`memory-privacy-filter`** (*Memory Privacy Filter* | `reversible` | `11.9`): Detects personal, confidential, licensed, and secret material before storage and applies minimization or approval.
- **`memory-provenance-recorder`** (*Memory Provenance Recorder* | `reversible` | `11.10`): Links every memory item to source, author, timestamp, extraction method, evidence, and confidence.
- **`memory-quality-evaluator`** (*Memory Quality Evaluator* | `read-only` | `11.11`): Tests precision, recall, conflict handling, stale retrieval, privacy, leakage, and usefulness on labeled cases.
- **`memory-backup-and-restore-manager`** (*Memory Backup and Restore Manager* | `reversible` | `11.12`): Creates consistent snapshots, validates restoration, and rebuilds vector or search indexes.
- **`memory-migration-manager`** (*Memory Migration Manager* | `approval-required` | `11.13`): Upgrades schemas and embeddings while preserving identifiers, provenance, permissions, and rollback.
- **`memory-deletion-verifier`** (*Memory Deletion Verifier* | `reversible` | `11.14`): Confirms removal from primary tables, vectors, caches, replicas, exports, and derived summaries as policy requires.

### Category 12: Vector, Search, Graph, and Metadata Databases (`vector-search-graph-and-metadata-databases` — 14 capabilities)

Example command for Category 12:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run vector-database-architect --role admin --dry-run --params '{"start_node":"agent-runtime","max_hops":3}''
```

- **`vector-database-architect`** (*Vector Database Architect* | `reversible` | `12.1`): Configures local Qdrant, Milvus, Weaviate, pgvector, FAISS, or equivalent storage with collections and isolation.
- **`vector-index-tuner`** (*Vector Index Tuner* | `reversible` | `12.2`): Selects HNSW, IVF, flat, product quantization, distance metric, parameters, and memory layout from recall and latency tests.
- **`metadata-filter-designer`** (*Metadata Filter Designer* | `reversible` | `12.3`): Defines typed filters for project, source, date, version, permissions, language, document type, status, and retention.
- **`keyword-search-indexer`** (*Keyword Search Indexer* | `reversible` | `12.4`): Builds local BM25 or full-text indexes with analyzers, stemming, stop words, fields, and access filters.
- **`knowledge-graph-builder`** (*Knowledge Graph Builder* | `reversible` | `12.5`): Extracts approved entities and relations, preserves source links, and avoids treating inferred edges as verified facts.
- **`graph-query-planner`** (*Graph Query Planner* | `read-only` | `12.6`): Uses paths, neighborhoods, temporal edges, and constraints for dependency, provenance, ownership, and impact questions.
- **`database-schema-registry`** (*Database Schema Registry* | `reversible` | `12.7`): Versions tables, indexes, constraints, migrations, views, retention, and compatibility requirements.
- **`database-migration-runner`** (*Database Migration Runner* | `approval-required` | `12.8`): Applies transactional migrations with backups, prechecks, postchecks, rollback, and version locks.
- **`database-integrity-checker`** (*Database Integrity Checker* | `read-only` | `12.9`): Checks constraints, orphan records, duplicate IDs, invalid vectors, missing provenance, permissions, and corruption.
- **`index-rebuild-coordinator`** (*Index Rebuild Coordinator* | `reversible` | `12.10`): Rebuilds search, vector, graph, and metadata indexes from authoritative stores and verifies counts and sampled results.
- **`database-backup-verifier`** (*Database Backup Verifier* | `reversible` | `12.11`): Creates consistent backups, restores them into a clean instance, and runs functional queries.
- **`query-performance-profiler`** (*Query Performance Profiler* | `read-only` | `12.12`): Captures slow queries, plans, lock waits, cache behavior, index use, and resource consumption.
- **`database-access-policy-enforcer`** (*Database Access Policy Enforcer* | `reversible` | `12.13`): Applies row, collection, tenant, project, and field-level controls before retrieval.
- **`storage-compaction-and-retention-manager`** (*Storage Compaction and Retention Manager* | `reversible` | `12.14`): Reclaims space and expires data without breaking identifiers, provenance, backups, or active jobs.

### Category 13: Retrieval-Augmented Generation and Grounding (`retrieval-augmented-generation-and-grounding` — 14 capabilities)

Example command for Category 13:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run rag-pipeline-architect --role admin --dry-run --params '{"query":"How many copies and media types are required by the 3-2-1 backup standard?"}''
```

- **`rag-pipeline-architect`** (*RAG Pipeline Architect* | `reversible` | `13.1`): Defines ingestion, chunking, indexing, retrieval, reranking, context assembly, citation, answer, and abstention stages.
- **`source-eligibility-filter`** (*Source Eligibility Filter* | `reversible` | `13.2`): Allows only approved, current, permission-compatible, nonquarantined sources into retrieval.
- **`query-rewriter`** (*Query Rewriter* | `read-only` | `13.3`): Creates constrained alternate queries, entity expansions, time filters, and vocabulary mappings without changing user intent.
- **`retrieval-fusion-operator`** (*Retrieval Fusion Operator* | `reversible` | `13.4`): Combines keyword, vector, graph, metadata, and curated sources with calibrated reciprocal-rank or score fusion.
- **`context-assembly-manager`** (*Context Assembly Manager* | `reversible` | `13.5`): Orders evidence by relevance, authority, diversity, recency, and token budget while preserving source boundaries.
- **`citation-anchor-builder`** (*Citation Anchor Builder* | `reversible` | `13.6`): Attaches answer claims to exact source, page, section, chunk, line, timestamp, or region identifiers.
- **`claim-to-evidence-validator`** (*Claim-to-Evidence Validator* | `read-only` | `13.7`): Checks whether each factual claim is entailed, contradicted, unsupported, or only partially supported by retrieved evidence.
- **`grounded-abstention-controller`** (*Grounded Abstention Controller* | `reversible` | `13.8`): Returns a clear unknown, asks for missing input, or routes to human review when evidence does not support an answer.
- **`conflicting-source-resolver`** (*Conflicting Source Resolver* | `reversible` | `13.9`): Surfaces disagreement, authority, date, scope, and uncertainty instead of silently selecting a convenient source.
- **`rag-prompt-injection-defender`** (*RAG Prompt Injection Defender* | `reversible` | `13.10`): Treats retrieved content as data, strips active instructions, applies trust labels, and blocks permission escalation.
- **`retrieval-leakage-tester`** (*Retrieval Leakage Tester* | `read-only` | `13.11`): Tests whether access filters, metadata, caches, and rerankers expose unauthorized chunks or document existence.
- **`rag-evaluation-runner`** (*RAG Evaluation Runner* | `reversible` | `13.12`): Measures retrieval recall, context precision, citation accuracy, answer faithfulness, completeness, latency, and abstention.
- **`rag-regression-tester`** (*RAG Regression Tester* | `read-only` | `13.13`): Replays labeled questions after index, embedding, reranker, prompt, or model changes.
- **`evidence-package-exporter`** (*Evidence Package Exporter* | `reversible` | `13.14`): Produces an offline bundle of answer, claims, citations, retrieved excerpts, source identifiers, versions, and validation results.

### Category 14: Workflow Engines and Durable Orchestration (`workflow-engines-and-durable-orchestration` — 14 capabilities)

Example command for Category 14:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run workflow-engine-architect --role admin --dry-run --params '{}''
```

- **`workflow-engine-architect`** (*Workflow Engine Architect* | `reversible` | `14.1`): Defines durable steps, activities, events, timers, compensation, child workflows, signals, and deterministic replay.
- **`workflow-definition-registry`** (*Workflow Definition Registry* | `reversible` | `14.2`): Versions workflow schemas, inputs, outputs, permissions, retries, timeouts, owners, and migration rules.
- **`dag-workflow-builder`** (*DAG Workflow Builder* | `reversible` | `14.3`): Creates dependency graphs with fan-out, fan-in, conditional branches, artifacts, gates, and failure propagation.
- **`long-running-workflow-manager`** (*Long-Running Workflow Manager* | `reversible` | `14.4`): Supports hours-to-months execution with durable timers, resumable state, human waits, and service restarts.
- **`compensation-workflow-builder`** (*Compensation Workflow Builder* | `reversible` | `14.5`): Defines reverse or mitigating actions for completed side effects when later steps fail.
- **`workflow-idempotency-auditor`** (*Workflow Idempotency Auditor* | `read-only` | `14.6`): Tests duplicate events, retries, restarts, and race conditions against state and external side effects.
- **`workflow-migration-manager`** (*Workflow Migration Manager* | `approval-required` | `14.7`): Moves in-flight instances to compatible definitions or preserves old workers until completion.
- **`workflow-cancellation-handler`** (*Workflow Cancellation Handler* | `reversible` | `14.8`): Stops safe future work, compensates where approved, and records unfinished or uncertain side effects.
- **`workflow-input-contract-validator`** (*Workflow Input Contract Validator* | `read-only` | `14.9`): Checks schema, permissions, references, required artifacts, and version compatibility before start.
- **`workflow-output-publisher`** (*Workflow Output Publisher* | `approval-required` | `14.10`): Validates and records final artifacts, summaries, provenance, approvals, and downstream events.
- **`workflow-replay-tester`** (*Workflow Replay Tester* | `read-only` | `14.11`): Replays histories under code changes and detects nondeterminism or incompatible behavior.
- **`workflow-visualization-builder`** (*Workflow Visualization Builder* | `read-only` | `14.12`): Creates offline state, dependency, timeline, error, and approval views for operators.
- **`workflow-stuck-state-detector`** (*Workflow Stuck-State Detector* | `read-only` | `14.13`): Finds missing events, dead workers, expired leases, unresolved approvals, and impossible transitions.
- **`workflow-recovery-operator`** (*Workflow Recovery Operator* | `reversible` | `14.14`): Resumes, retries, compensates, forks, or terminates instances from the last verified state.

### Category 15: Scheduling, Queues, and Event Processing (`scheduling-queues-and-event-processing` — 14 capabilities)

Example command for Category 15:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run job-scheduler --role admin --dry-run --params '{"topic":"indexing","priority":10}''
```

- **`job-scheduler`** (*Job Scheduler* | `reversible` | `15.1`): Runs immediate, delayed, periodic, calendar, dependency, and resource-aware jobs with durable state.
- **`priority-queue-manager`** (*Priority Queue Manager* | `reversible` | `15.2`): Orders work by criticality, deadline, user tier, dependency, age, and fairness without starving low-priority jobs.
- **`event-queue-architect`** (*Event Queue Architect* | `reversible` | `15.3`): Defines durable topics, partitions, ordering, retention, acknowledgement, redelivery, and dead-letter behavior.
- **`event-schema-registry`** (*Event Schema Registry* | `reversible` | `15.4`): Versions event names, payloads, compatibility, ownership, privacy class, and consumer contracts.
- **`event-deduplication-operator`** (*Event Deduplication Operator* | `reversible` | `15.5`): Uses stable event IDs and processing records to prevent repeated side effects.
- **`dead-letter-queue-manager`** (*Dead-Letter Queue Manager* | `reversible` | `15.6`): Captures exhausted or malformed messages with diagnostics, replay controls, and quarantine.
- **`backpressure-controller`** (*Backpressure Controller* | `reversible` | `15.7`): Slows producers, limits consumers, sheds optional work, and protects databases and model servers under load.
- **`worker-lease-manager`** (*Worker Lease Manager* | `reversible` | `15.8`): Assigns jobs with heartbeats, expiry, renewal, ownership transfer, and duplicate-execution protection.
- **`cron-and-calendar-validator`** (*Cron and Calendar Validator* | `read-only` | `15.9`): Tests time zones, daylight-saving changes, month boundaries, holidays, missed runs, and clock drift.
- **`job-dependency-resolver`** (*Job Dependency Resolver* | `reversible` | `15.10`): Starts jobs only when required artifacts, approvals, services, and upstream states are satisfied.
- **`queue-fairness-auditor`** (*Queue Fairness Auditor* | `read-only` | `15.11`): Measures starvation, head-of-line blocking, tenant imbalance, and priority inversion.
- **`event-replay-operator`** (*Event Replay Operator* | `reversible` | `15.12`): Reprocesses bounded historical events safely with idempotency, destination isolation, and progress checkpoints.
- **`clock-and-time-synchronization-monitor`** (*Clock and Time Synchronization Monitor* | `read-only` | `15.13`): Detects host drift that could corrupt leases, schedules, ordering, certificates, or audit timestamps.
- **`scheduler-disaster-recovery-tester`** (*Scheduler Disaster Recovery Tester* | `read-only` | `15.14`): Restores jobs, timers, queues, leases, and dead letters after service or database loss.

### Category 16: Retry, Recovery, and Fault Tolerance (`retry-recovery-and-fault-tolerance` — 14 capabilities)

Example command for Category 16:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run retry-policy-designer --role admin --dry-run --params '{"observed_rpo_steps":0,"observed_rto_seconds":3.5}''
```

- **`retry-policy-designer`** (*Retry Policy Designer* | `reversible` | `16.1`): Sets retryable errors, maximum attempts, exponential backoff, jitter, deadlines, and nonretryable conditions by operation.
- **`failure-classifier`** (*Failure Classifier* | `read-only` | `16.2`): Separates input, permission, policy, dependency, timeout, resource, tool, model, data, schema, and external-state failures.
- **`circuit-breaker-manager`** (*Circuit Breaker Manager* | `reversible` | `16.3`): Opens, tests, and closes circuits around failing models, tools, databases, and services to prevent cascading failure.
- **`bulkhead-isolation-designer`** (*Bulkhead Isolation Designer* | `reversible` | `16.4`): Separates pools, queues, memory, workers, and quotas so one workload cannot exhaust the whole agent system.
- **`checkpoint-recovery-manager`** (*Checkpoint Recovery Manager* | `reversible` | `16.5`): Restarts from the latest verified checkpoint and avoids repeating completed or uncertain side effects.
- **`saga-compensation-coordinator`** (*Saga Compensation Coordinator* | `reversible` | `16.6`): Runs approved compensations in correct order and records actions that cannot be reversed.
- **`partial-result-preserver`** (*Partial Result Preserver* | `reversible` | `16.7`): Retains independent successful branches and reruns only failed dependent work.
- **`crash-recovery-tester`** (*Crash Recovery Tester* | `read-only` | `16.8`): Kills model servers, workers, databases, and orchestrators during execution and verifies durable continuation.
- **`power-loss-recovery-tester`** (*Power-Loss Recovery Tester* | `read-only` | `16.9`): Simulates abrupt host shutdown and validates journals, databases, queues, files, locks, and job state.
- **`network-partition-simulator`** (*Network Partition Simulator* | `reversible` | `16.10`): Tests local service isolation, delayed events, split-brain protection, timeouts, and reconciliation.
- **`data-corruption-recovery-drill`** (*Data Corruption Recovery Drill* | `reversible` | `16.11`): Injects damaged indexes, model shards, state records, caches, and artifacts and verifies detection and restoration.
- **`recovery-point-and-time-objective-manager`** (*Recovery Point and Time Objective Manager* | `reversible` | `16.12`): Defines and measures acceptable data loss and restoration time for each subsystem.
- **`uncertain-side-effect-resolver`** (*Uncertain Side-Effect Resolver* | `reversible` | `16.13`): Detects operations whose result is unknown after timeout and verifies external state before retrying.
- **`failure-recovery-evidence-builder`** (*Failure Recovery Evidence Builder* | `reversible` | `16.14`): Records trigger, affected state, repair, restored artifacts, replay results, and confirming tests.

### Category 17: Human Approval and Oversight (`human-approval-and-oversight` — 14 capabilities)

Example command for Category 17:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run human-approval-gate-builder --role admin --dry-run --params '{"intervention":"inspect"}''
```

- **`human-approval-gate-builder`** (*Human Approval Gate Builder* | `reversible` | `17.1`): Pauses consequential actions and presents request, evidence, risk, alternatives, scope, and exact proposed side effects.
- **`approval-policy-engine`** (*Approval Policy Engine* | `reversible` | `17.2`): Routes approval by action type, risk, data class, destination, amount, environment, and separation-of-duties rules.
- **`dual-control-approval-manager`** (*Dual-Control Approval Manager* | `dual-control` | `17.3`): Requires two independent authorized reviewers for high-risk changes, secrets, releases, or destructive actions.
- **`approval-timeout-handler`** (*Approval Timeout Handler* | `reversible` | `17.4`): Expires stale approvals, prevents execution under changed state, and requires refreshed evidence.
- **`approval-context-packager`** (*Approval Context Packager* | `reversible` | `17.5`): Shows inputs, plan, diffs, model and tool versions, permissions, validation, and rollback before decision.
- **`approval-decision-recorder`** (*Approval Decision Recorder* | `reversible` | `17.6`): Stores reviewer, authority, decision, scope, conditions, timestamp, version, and rationale.
- **`conditional-approval-enforcer`** (*Conditional Approval Enforcer* | `reversible` | `17.7`): Checks required modifications, limits, time windows, destinations, and follow-up verification before execution.
- **`revocation-and-recall-manager`** (*Revocation and Recall Manager* | `approval-required` | `17.8`): Stops queued work, revokes granted capability, and identifies completed side effects when approval is withdrawn.
- **`human-escalation-router`** (*Human Escalation Router* | `reversible` | `17.9`): Selects the correct reviewer by domain, project, risk, availability, and conflict-of-interest rules.
- **`operator-intervention-console`** (*Operator Intervention Console* | `reversible` | `17.10`): Supports pause, inspect, edit safe parameters, retry, skip, compensate, terminate, and export evidence.
- **`approval-bypass-detector`** (*Approval Bypass Detector* | `read-only` | `17.11`): Finds direct tool calls, alternate workflows, replay paths, or stale tokens that avoid required human review.
- **`approval-regression-suite`** (*Approval Regression Suite* | `reversible` | `17.12`): Tests allow, deny, condition, expiry, changed-state, duplicate, and unauthorized-reviewer cases.
- **`post-action-human-review-gate`** (*Post-Action Human Review Gate* | `read-only` | `17.13`): Requires review of outcomes and side effects for actions that cannot be fully predicted before execution.
- **`human-feedback-capture-manager`** (*Human Feedback Capture Manager* | `reversible` | `17.14`): Records corrections and outcomes for evaluation without silently turning unreviewed feedback into policy.

### Category 18: Multi-Agent Coordination and Delegation (`multi-agent-coordination-and-delegation` — 14 capabilities)

Example command for Category 18:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run multi-agent-coordinator --role admin --dry-run --params '{"goal":"Verify air-gap security and export grounded evidence"}''
```

- **`multi-agent-coordinator`** (*Multi-Agent Coordinator* | `reversible` | `18.1`): Decomposes work into bounded tasks with owners, inputs, outputs, dependencies, permissions, deadlines, and acceptance checks.
- **`agent-capability-registry`** (*Agent Capability Registry* | `reversible` | `18.2`): Records each agent model, tools, modalities, permissions, limits, quality evidence, cost, and supported task types.
- **`delegation-policy-engine`** (*Delegation Policy Engine* | `reversible` | `18.3`): Selects agents by competence, data access, risk, workload, latency, and independence requirements.
- **`agent-message-schema-registry`** (*Agent Message Schema Registry* | `reversible` | `18.4`): Versions task, status, result, evidence, error, cancellation, and escalation messages.
- **`shared-artifact-manager`** (*Shared Artifact Manager* | `reversible` | `18.5`): Provides versioned files, tables, plans, and evidence without relying on ambiguous conversational state.
- **`agent-dependency-planner`** (*Agent Dependency Planner* | `read-only` | `18.6`): Builds fan-out and fan-in graphs and prevents downstream work before prerequisites pass.
- **`agent-result-merger`** (*Agent Result Merger* | `reversible` | `18.7`): Combines independent outputs, resolves conflicts, deduplicates evidence, and preserves dissent or uncertainty.
- **`consensus-and-adjudication-manager`** (*Consensus and Adjudication Manager* | `reversible` | `18.8`): Uses independent attempts, critique, judge rules, or human review without mistaking agreement for correctness.
- **`agent-loop-and-ping-pong-detector`** (*Agent Loop and Ping-Pong Detector* | `read-only` | `18.9`): Stops repeated delegation, circular critique, unresolved handoffs, and duplicate work.
- **`agent-budget-allocator`** (*Agent Budget Allocator* | `reversible` | `18.10`): Assigns token, time, tool, memory, and compute limits across agents and reclaims unused capacity.
- **`agent-isolation-controller`** (*Agent Isolation Controller* | `reversible` | `18.11`): Separates prompts, files, memory, permissions, tools, and secrets among specialist agents.
- **`agent-failure-substitution-manager`** (*Agent Failure Substitution Manager* | `reversible` | `18.12`): Reassigns failed work to a compatible agent while preserving state, evidence, and idempotency.
- **`multi-agent-trace-builder`** (*Multi-Agent Trace Builder* | `reversible` | `18.13`): Records delegation, messages, artifacts, decisions, approvals, and final synthesis as one causal trace.
- **`multi-agent-benchmark-runner`** (*Multi-Agent Benchmark Runner* | `read-only` | `18.14`): Compares single-agent, parallel, debate, planner-executor, and hierarchical patterns under identical tasks and limits.

### Category 19: Prompt, Template, and Version Registries (`prompt-template-and-version-registries` — 14 capabilities)

Example command for Category 19:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run prompt-registry --role admin --dry-run --params '{"variables":{"question":"What is RPO?","evidence_blocks":"[doc-airgap-arch-01:c2] RPO is 0 steps."}}''
```

- **`prompt-registry`** (*Prompt Registry* | `reversible` | `19.1`): Versions system, agent, retrieval, tool, evaluation, and repair prompts with owners, status, hashes, and compatibility.
- **`prompt-template-compiler`** (*Prompt Template Compiler* | `reversible` | `19.2`): Validates variables, types, defaults, escaping, required evidence, tool schemas, and output contracts.
- **`prompt-injection-hardening-reviewer`** (*Prompt Injection Hardening Reviewer* | `read-only` | `19.3`): Checks instruction hierarchy, untrusted content boundaries, tool permissions, data exfiltration, and indirect injection.
- **`prompt-a-b-evaluation-runner`** (*Prompt A-B Evaluation Runner* | `reversible` | `19.4`): Compares prompt versions under identical models, tools, datasets, seeds, limits, and scoring guides.
- **`prompt-regression-gate`** (*Prompt Regression Gate* | `reversible` | `19.5`): Blocks promotion when correctness, grounding, safety, format, latency, or refusal behavior materially worsens.
- **`prompt-rollback-manager`** (*Prompt Rollback Manager* | `approval-required` | `19.6`): Restores the last approved prompt set and rebinds workflows, agents, and evaluations safely.
- **`template-localization-manager`** (*Template Localization Manager* | `reversible` | `19.7`): Versions language, terminology, cultural, formatting, and right-to-left variants without drifting core policy.
- **`prompt-dependency-graph`** (*Prompt Dependency Graph* | `reversible` | `19.8`): Maps which agents, workflows, benchmarks, tools, schemas, and policies depend on each prompt version.
- **`prompt-variable-security-filter`** (*Prompt Variable Security Filter* | `reversible` | `19.9`): Prevents secrets, unsafe markup, instructions, or oversized untrusted values from entering privileged prompt sections.
- **`prompt-size-and-token-auditor`** (*Prompt Size and Token Auditor* | `read-only` | `19.10`): Measures static and dynamic prompt cost, redundancy, truncation risk, and context competition.
- **`few-shot-example-curator`** (*Few-Shot Example Curator* | `reversible` | `19.11`): Maintains representative positive, boundary, failure, refusal, and repair examples with provenance.
- **`prompt-change-approval-workflow`** (*Prompt Change Approval Workflow* | `approval-required` | `19.12`): Requires diff, motivation, evaluation evidence, risk assessment, owner, and rollback before production promotion.
- **`prompt-provenance-recorder`** (*Prompt Provenance Recorder* | `reversible` | `19.13`): Links each execution to exact prompt, variables, model, retrieval context, tool schemas, and policies.
- **`prompt-dead-code-cleaner`** (*Prompt Dead-Code Cleaner* | `reversible` | `19.14`): Finds unused templates, stale variables, contradictory instructions, and obsolete examples.

### Category 20: Evaluation Datasets and Benchmarking (`evaluation-datasets-and-benchmarking` — 16 capabilities)

Example command for Category 20:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run evaluation-dataset-registry --role admin --dry-run --params '{"passed":5,"total":5}''
```

- **`evaluation-dataset-registry`** (*Evaluation Dataset Registry* | `reversible` | `20.1`): Versions cases, inputs, expected properties, labels, provenance, licenses, splits, and sensitive-data restrictions.
- **`benchmark-suite-architect`** (*Benchmark Suite Architect* | `read-only` | `20.2`): Combines normal, boundary, adversarial, failure, recovery, long-context, multimodal, and tool-use tests.
- **`golden-answer-curator`** (*Golden Answer Curator* | `reversible` | `20.3`): Maintains accepted answers, scoring rubrics, evidence, acceptable variants, and abstention conditions.
- **`tool-use-benchmark-builder`** (*Tool-Use Benchmark Builder* | `read-only` | `20.4`): Tests tool selection, argument accuracy, schema compliance, side effects, validation, retries, and cleanup.
- **`agentic-task-benchmark-builder`** (*Agentic Task Benchmark Builder* | `read-only` | `20.5`): Tests planning, state persistence, long-running execution, approvals, recovery, and artifact delivery.
- **`grounding-benchmark-builder`** (*Grounding Benchmark Builder* | `read-only` | `20.6`): Tests citation, entailment, conflict handling, unknown answers, and unsupported claim detection.
- **`safety-and-permission-benchmark-builder`** (*Safety and Permission Benchmark Builder* | `read-only` | `20.7`): Tests authorization, secret handling, injection, malware, destructive actions, and escalation.
- **`memory-benchmark-builder`** (*Memory Benchmark Builder* | `read-only` | `20.8`): Tests write quality, retrieval, conflict resolution, correction, deletion, privacy, and stale information.
- **`multimodal-benchmark-builder`** (*Multimodal Benchmark Builder* | `read-only` | `20.9`): Tests images, documents, audio, video, OCR, region grounding, and cross-modal evidence.
- **`benchmark-harness-runner`** (*Benchmark Harness Runner* | `read-only` | `20.10`): Executes cases with fixed models, tools, limits, seeds, environments, and trace capture.
- **`automatic-scoring-pipeline`** (*Automatic Scoring Pipeline* | `reversible` | `20.11`): Combines exact match, schema checks, tests, retrieval metrics, model judges, deterministic validators, and human review.
- **`judge-calibration-manager`** (*Judge Calibration Manager* | `reversible` | `20.12`): Measures model-judge bias, order effects, verbosity preference, self-preference, and agreement with human labels.
- **`evaluation-contamination-checker`** (*Evaluation Contamination Checker* | `read-only` | `20.13`): Detects overlap between test cases, prompts, fine-tuning data, retrieved corpora, and public examples.
- **`benchmark-report-builder`** (*Benchmark Report Builder* | `read-only` | `20.14`): Summarizes pass rates, severity, confidence intervals, latency, resources, regressions, and representative failures.
- **`promotion-quality-gate`** (*Promotion Quality Gate* | `reversible` | `20.15`): Requires predefined thresholds by critical capability and prevents averages from hiding severe failures.
- **`evaluation-reproducibility-packager`** (*Evaluation Reproducibility Packager* | `reversible` | `20.16`): Stores datasets, harness, prompts, models, tools, environment, seeds, outputs, and reports for offline rerun.

### Category 21: Hallucination, Grounding, and Factuality Controls (`hallucination-grounding-and-factuality-controls` — 14 capabilities)

Example command for Category 21:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run claim-extractor --role admin --dry-run --params '{"expression":"(5600 + 6200 + 512) / 16384 * 100","claimed_value":75.1465,"tolerance":0.01}''
```

- **`claim-extractor`** (*Claim Extractor* | `read-only` | `21.1`): Breaks model output into factual, procedural, predictive, subjective, and unverifiable claims for targeted checks.
- **`evidence-entailment-checker`** (*Evidence Entailment Checker* | `read-only` | `21.2`): Classifies each factual claim as supported, contradicted, insufficient, or not applicable against approved evidence.
- **`citation-completeness-checker`** (*Citation Completeness Checker* | `read-only` | `21.3`): Detects factual claims that require citations but lack valid source anchors.
- **`citation-accuracy-validator`** (*Citation Accuracy Validator* | `read-only` | `21.4`): Verifies that cited passages actually support the nearby claim and match source version and scope.
- **`unsupported-specificity-detector`** (*Unsupported Specificity Detector* | `read-only` | `21.5`): Flags invented names, dates, numbers, credentials, file paths, identifiers, quotes, and technical settings.
- **`entity-consistency-checker`** (*Entity Consistency Checker* | `read-only` | `21.6`): Checks names, relationships, dates, versions, units, and identifiers across answer, sources, memory, and tools.
- **`numerical-verification-operator`** (*Numerical Verification Operator* | `reversible` | `21.7`): Recomputes arithmetic, units, totals, ranges, percentages, and tolerances using deterministic code.
- **`contradiction-detector`** (*Contradiction Detector* | `read-only` | `21.8`): Finds conflicts within the answer and against evidence, policy, memory, tool output, or prior verified decisions.
- **`uncertainty-calibrator`** (*Uncertainty Calibrator* | `read-only` | `21.9`): Maps evidence quality and model confidence to clear language, abstention, verification, or escalation behavior.
- **`source-freshness-validator`** (*Source Freshness Validator* | `read-only` | `21.10`): Checks whether a source version or date is suitable for the claim and target decision.
- **`answer-scope-guard`** (*Answer Scope Guard* | `reversible` | `21.11`): Prevents conclusions beyond the user request, available evidence, permissions, or tested environment.
- **`factual-repair-workflow`** (*Factual Repair Workflow* | `reversible` | `21.12`): Removes unsupported claims, retrieves better evidence, recalculates, or abstains while preserving supported content.
- **`grounding-regression-suite`** (*Grounding Regression Suite* | `reversible` | `21.13`): Replays claim-evidence cases after model, prompt, retrieval, reranker, or citation changes.
- **`high-stakes-verification-gate`** (*High-Stakes Verification Gate* | `approval-required` | `21.14`): Requires authoritative sources, deterministic checks, or qualified human approval before consequential advice or action.

### Category 22: Security, Malware, and Unsafe-Code Screening (`security-malware-and-unsafe-code-screening` — 14 capabilities)

Example command for Category 22:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run malware-file-scanner --role admin --dry-run --params '{"packages":[{"package":"pyyaml","version":"5.3"}]}''
```

- **`malware-file-scanner`** (*Malware File Scanner* | `read-only` | `22.1`): Scans executables, scripts, archives, documents, models, and generated artifacts with local engines and signatures.
- **`static-code-security-analyzer`** (*Static Code Security Analyzer* | `read-only` | `22.2`): Detects injection, command execution, unsafe deserialization, path traversal, weak cryptography, credential exposure, and persistence.
- **`dependency-vulnerability-auditor`** (*Dependency Vulnerability Auditor* | `read-only` | `22.3`): Matches local package and binary inventories against an offline vulnerability database and remediation policy.
- **`software-bill-of-materials-builder`** (*Software Bill of Materials Builder* | `reversible` | `22.4`): Creates SPDX or CycloneDX inventories for applications, libraries, containers, models, and packaged tools.
- **`archive-bomb-detector`** (*Archive Bomb Detector* | `read-only` | `22.5`): Checks nested archives, decompression ratios, file counts, path traversal, symlinks, and unsupported formats before extraction.
- **`model-file-security-scanner`** (*Model File Security Scanner* | `read-only` | `22.6`): Checks pickle or executable model formats, custom code, unexpected tensors, metadata, licenses, and hashes before load.
- **`prompt-injection-detector`** (*Prompt Injection Detector* | `read-only` | `22.7`): Identifies direct and indirect attempts to override instructions, exfiltrate data, use tools, or grant permissions.
- **`command-injection-guard`** (*Command Injection Guard* | `reversible` | `22.8`): Uses typed arguments, allowlists, escaping, no-shell execution, and policy checks before local process launch.
- **`unsafe-deserialization-blocker`** (*Unsafe Deserialization Blocker* | `reversible` | `22.9`): Rejects untrusted pickle, marshal, object streams, macros, and custom loaders unless isolated and explicitly approved.
- **`supply-chain-integrity-verifier`** (*Supply Chain Integrity Verifier* | `reversible` | `22.10`): Validates signatures, hashes, provenance, maintainers, expected contents, and offline source of packages and models.
- **`security-quarantine-workflow`** (*Security Quarantine Workflow* | `reversible` | `22.11`): Isolates suspicious inputs, prevents model and tool access, records evidence, and routes authorized review.
- **`sandbox-behavior-analyzer`** (*Sandbox Behavior Analyzer* | `read-only` | `22.12`): Detects file encryption, mass deletion, persistence, process injection, device access, credential search, and network attempts.
- **`security-regression-suite`** (*Security Regression Suite* | `reversible` | `22.13`): Tests malicious files, prompts, code, archives, dependencies, and tool arguments after stack changes.
- **`incident-evidence-packager`** (*Incident Evidence Packager* | `reversible` | `22.14`): Preserves hashes, logs, state, artifacts, timelines, affected permissions, and containment actions without executing suspicious content.

### Category 23: Network Isolation and Offline Governance (`network-isolation-and-offline-governance` — 14 capabilities)

Example command for Category 23:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run default-deny-network-policy-manager --role admin --dry-run --params '{}''
```

- **`default-deny-network-policy-manager`** (*Default-Deny Network Policy Manager* | `reversible` | `23.1`): Blocks outbound and inbound network access except explicitly approved local services and temporary rules.
- **`local-service-allowlist-builder`** (*Local Service Allowlist Builder* | `reversible` | `23.2`): Defines exact hosts, ports, protocols, certificates, identities, and purposes for model, database, queue, and workflow services.
- **`dns-and-proxy-blocker`** (*DNS and Proxy Blocker* | `reversible` | `23.3`): Prevents hidden name resolution, proxy fallback, telemetry, update checks, and covert network paths.
- **`offline-telemetry-auditor`** (*Offline Telemetry Auditor* | `read-only` | `23.4`): Detects attempted analytics, crash reporting, model downloads, license checks, and remote feature calls.
- **`egress-attempt-monitor`** (*Egress Attempt Monitor* | `read-only` | `23.5`): Records blocked connection attempts with process, destination, time, user, job, and relevant stack trace.
- **`network-namespace-manager`** (*Network Namespace Manager* | `reversible` | `23.6`): Places agents, sandboxes, databases, and model servers in separate local network zones with explicit routes.
- **`one-way-transfer-workflow`** (*One-Way Transfer Workflow* | `approval-required` | `23.7`): Supports controlled import or export through scanned removable media, data diodes, or staged transfer stations.
- **`offline-certificate-authority-manager`** (*Offline Certificate Authority Manager* | `read-only` | `23.8`): Issues and rotates local service certificates without external dependency.
- **`time-synchronization-planner`** (*Time Synchronization Planner* | `read-only` | `23.9`): Maintains trusted local time for certificates, leases, schedules, and audit logs without public NTP.
- **`air-gap-compliance-tester`** (*Air-Gap Compliance Tester* | `read-only` | `23.10`): Probes interfaces, routes, DNS, proxies, sockets, package managers, model runtimes, and applications for external reachability.
- **`temporary-network-exception-gate`** (*Temporary Network Exception Gate* | `approval-required` | `23.11`): Requires reason, destination, duration, owner, approval, monitoring, and post-use cleanup.
- **`disconnected-license-resilience-tester`** (*Disconnected License Resilience Tester* | `read-only` | `23.12`): Verifies that approved offline licenses remain valid and that expiry or clock behavior fails safely.
- **`offline-update-import-validator`** (*Offline Update Import Validator* | `approval-required` | `23.13`): Scans, verifies, inventories, stages, tests, and approves updates brought across the boundary.
- **`network-policy-regression-suite`** (*Network Policy Regression Suite* | `reversible` | `23.14`): Tests blocked and allowed traffic after operating-system, firewall, container, service, or application changes.

### Category 24: Audit Logs, Traces, and Provenance (`audit-logs-traces-and-provenance` — 14 capabilities)

Example command for Category 24:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run audit-log-architect --role admin --dry-run --params '{"operation":"verify"}''
```

- **`audit-log-architect`** (*Audit Log Architect* | `reversible` | `24.1`): Defines immutable events for authentication, authorization, tool use, model calls, memory, approvals, state changes, and artifacts.
- **`distributed-trace-builder`** (*Distributed Trace Builder* | `reversible` | `24.2`): Links user request, agent steps, model inference, retrieval, tools, workflows, queues, and outputs with one trace identifier.
- **`model-invocation-recorder`** (*Model Invocation Recorder* | `reversible` | `24.3`): Stores model, revision, quantization, parameters, prompt hash, context sources, timing, resources, and output hash.
- **`artifact-provenance-recorder`** (*Artifact Provenance Recorder* | `reversible` | `24.4`): Links each file, dataset, report, or action to inputs, tools, versions, code, approvals, and validation results.
- **`causal-event-sequencer`** (*Causal Event Sequencer* | `reversible` | `24.5`): Orders events across hosts and services using stable IDs, local clocks, monotonic time, and parent-child relationships.
- **`tamper-evident-log-manager`** (*Tamper-Evident Log Manager* | `reversible` | `24.6`): Uses append-only storage, hash chains, signatures, access control, backups, and verification.
- **`sensitive-log-redactor`** (*Sensitive Log Redactor* | `reversible` | `24.7`): Removes secrets and minimizes personal or licensed data while preserving diagnostic value and audit linkage.
- **`audit-retention-manager`** (*Audit Retention Manager* | `reversible` | `24.8`): Applies retention, legal hold, archive, compression, deletion, and access rules by event class.
- **`trace-sampling-policy-manager`** (*Trace Sampling Policy Manager* | `reversible` | `24.9`): Keeps complete traces for high-risk or failed work and controlled samples for routine high-volume work.
- **`audit-query-and-timeline-builder`** (*Audit Query and Timeline Builder* | `read-only` | `24.10`): Reconstructs who requested, approved, executed, changed, observed, and validated an operation.
- **`provenance-completeness-checker`** (*Provenance Completeness Checker* | `read-only` | `24.11`): Finds outputs lacking source, model, tool, prompt, permission, approval, or validation lineage.
- **`audit-export-packager`** (*Audit Export Packager* | `reversible` | `24.12`): Creates a permission-filtered offline evidence package with logs, traces, artifacts, schemas, and verification.
- **`clock-skew-auditor`** (*Clock Skew Auditor* | `read-only` | `24.13`): Detects timestamp inconsistencies that could misorder approvals, leases, retries, or evidence.
- **`audit-recovery-tester`** (*Audit Recovery Tester* | `read-only` | `24.14`): Restores logs and trace indexes from backup and verifies hash chains, queryability, and retention rules.

### Category 25: Resource, Cost, and Budget Controls (`resource-cost-and-budget-controls` — 14 capabilities)

Example command for Category 25:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run agent-budget-manager --role admin --dry-run --params '{"tokens_used":2048,"tool_calls_used":4}''
```

- **`agent-budget-manager`** (*Agent Budget Manager* | `reversible` | `25.1`): Sets per-user, per-project, per-workflow, and per-agent limits for tokens, time, GPU, CPU, memory, storage, and tool calls.
- **`token-usage-accountant`** (*Token Usage Accountant* | `read-only` | `25.2`): Records prompt, retrieved context, tool context, output, retries, and child-agent token use by model and task.
- **`compute-quota-enforcer`** (*Compute Quota Enforcer* | `reversible` | `25.3`): Limits GPU-seconds, CPU-seconds, concurrency, batch size, and high-memory model access.
- **`storage-quota-manager`** (*Storage Quota Manager* | `reversible` | `25.4`): Controls model, vector, memory, trace, artifact, cache, temporary, and backup growth with reserved safety margins.
- **`time-deadline-controller`** (*Time Deadline Controller* | `reversible` | `25.5`): Sets step, tool, model, workflow, approval, and end-to-end deadlines with graceful cancellation.
- **`tool-call-budget-guard`** (*Tool Call Budget Guard* | `reversible` | `25.6`): Limits repeated, expensive, destructive, or external tool calls and requires escalation before overrun.
- **`dynamic-budget-reallocator`** (*Dynamic Budget Reallocator* | `reversible` | `25.7`): Moves unused capacity to blocked critical work without violating hard limits or fairness.
- **`budget-aware-model-router`** (*Budget-Aware Model Router* | `reversible` | `25.8`): Selects smaller or faster local models when quality evidence shows they satisfy the task.
- **`runaway-agent-detector`** (*Runaway Agent Detector* | `read-only` | `25.9`): Stops loops, exploding delegation, context growth, queue amplification, and repeated retries.
- **`resource-forecast-planner`** (*Resource Forecast Planner* | `read-only` | `25.10`): Projects capacity from users, arrival rates, context size, model mix, retraining, indexing, and retention.
- **`budget-exception-workflow`** (*Budget Exception Workflow* | `approval-required` | `25.11`): Requires reason, scope, expiry, approver, expected benefit, and post-run review for temporary increases.
- **`resource-utilization-dashboard-builder`** (*Resource Utilization Dashboard Builder* | `read-only` | `25.12`): Summarizes queue, model, agent, tool, GPU, CPU, memory, disk, and failure utilization offline.
- **`cost-attribution-reporter`** (*Cost Attribution Reporter* | `read-only` | `25.13`): Maps local hardware and operational cost estimates to agents, workflows, projects, models, and artifacts.
- **`budget-regression-tester`** (*Budget Regression Tester* | `read-only` | `25.14`): Compares resource use after model, prompt, retrieval, tool, workflow, and batching changes.

### Category 26: Data Ingest, Documents, and Local Connectors (`data-ingest-documents-and-local-connectors` — 14 capabilities)

Example command for Category 26:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run offline-connector-registry --role admin --dry-run --params '{"csv_content":"metric,target,formula\nrpo,0,=SUM(0,0)\n"}''
```

- **`offline-connector-registry`** (*Offline Connector Registry* | `reversible` | `26.1`): Versions local filesystem, database, mail archive, issue tracker export, source repository, and document connector definitions.
- **`connector-permission-enforcer`** (*Connector Permission Enforcer* | `reversible` | `26.2`): Applies source-specific identities, paths, filters, row-level rules, and purpose limitations before access.
- **`file-ingest-pipeline`** (*File Ingest Pipeline* | `reversible` | `26.3`): Identifies formats, scans security, extracts metadata and content, deduplicates, classifies, and records provenance.
- **`pdf-and-document-extractor`** (*PDF and Document Extractor* | `read-only` | `26.4`): Extracts text, layout, tables, images, forms, headings, and page anchors from local PDF and office files.
- **`spreadsheet-ingest-operator`** (*Spreadsheet Ingest Operator* | `reversible` | `26.5`): Extracts sheets, tables, formulas, values, types, named ranges, comments, and provenance without silently flattening meaning.
- **`email-archive-ingestor`** (*Email Archive Ingestor* | `reversible` | `26.6`): Indexes approved local mail exports with threading, participants, attachments, dates, permissions, and redaction.
- **`database-snapshot-connector`** (*Database Snapshot Connector* | `reversible` | `26.7`): Reads consistent local snapshots using typed schemas, query limits, audit, and no mutation by default.
- **`source-repository-ingestor`** (*Source Repository Ingestor* | `reversible` | `26.8`): Indexes code, history, branches, symbols, dependencies, tests, issues, and licenses from local repositories.
- **`incremental-change-detector`** (*Incremental Change Detector* | `read-only` | `26.9`): Processes only new, changed, moved, deleted, or permission-modified records using stable identifiers and hashes.
- **`content-classification-operator`** (*Content Classification Operator* | `reversible` | `26.10`): Labels source type, sensitivity, rights, retention, authority, freshness, and indexing eligibility.
- **`ingest-error-quarantine`** (*Ingest Error Quarantine* | `reversible` | `26.11`): Isolates corrupt, malicious, unsupported, encrypted, or policy-blocked items with diagnostics.
- **`connector-drift-detector`** (*Connector Drift Detector* | `read-only` | `26.12`): Detects schema, path, export format, permissions, or source-version changes that could corrupt ingestion.
- **`ingest-completeness-validator`** (*Ingest Completeness Validator* | `read-only` | `26.13`): Compares source counts, hashes, pages, rows, attachments, and error reports against the indexed corpus.
- **`connector-regression-suite`** (*Connector Regression Suite* | `reversible` | `26.14`): Tests normal, empty, large, malformed, moved, deleted, unauthorized, and changed-schema inputs.

### Category 27: Automation Actions and Transaction Safety (`automation-actions-and-transaction-safety` — 14 capabilities)

Example command for Category 27:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run action-plan-diff-builder --role admin --dry-run --params '{"before_text":"mode=open\n","after_text":"mode=offline-deny\n"}''
```

- **`action-plan-diff-builder`** (*Action Plan Diff Builder* | `reversible` | `27.1`): Shows exact proposed file, database, configuration, message, or workflow changes before execution.
- **`read-only-dry-run-operator`** (*Read-Only Dry Run Operator* | `reversible` | `27.2`): Simulates an action, validates prerequisites, estimates side effects, and produces a reviewable plan.
- **`transactional-action-coordinator`** (*Transactional Action Coordinator* | `approval-required` | `27.3`): Groups supported changes into atomic commits and rolls back when validation fails.
- **`file-mutation-safety-manager`** (*File Mutation Safety Manager* | `approval-required` | `27.4`): Uses temp files, atomic rename, backups, permissions, collision checks, and post-write reopen validation.
- **`database-mutation-guard`** (*Database Mutation Guard* | `approval-required` | `27.5`): Requires parameterized statements, row limits, transaction boundaries, expected row counts, and rollback.
- **`bulk-operation-throttler`** (*Bulk Operation Throttler* | `reversible` | `27.6`): Limits batch size, rate, concurrency, and blast radius with checkpoints and stop conditions.
- **`external-action-approval-gate`** (*External Action Approval Gate* | `approval-required` | `27.7`): Requires human authorization for messages, deployments, account changes, purchases, deletions, and other consequential operations.
- **`two-phase-action-committer`** (*Two-Phase Action Committer* | `approval-required` | `27.8`): Separates prepare and commit so state can be reviewed and revalidated before irreversible change.
- **`compensating-action-builder`** (*Compensating Action Builder* | `reversible` | `27.9`): Defines undo or mitigation for actions that cannot participate in atomic transactions.
- **`post-action-verification-operator`** (*Post-Action Verification Operator* | `reversible` | `27.10`): Reads the recipient system or artifact to confirm the intended effect rather than trusting a success response.
- **`mutation-reconciliation-manager`** (*Mutation Reconciliation Manager* | `reversible` | `27.11`): Compares intended, attempted, observed, and audited state and resolves discrepancies.
- **`blast-radius-limiter`** (*Blast Radius Limiter* | `reversible` | `27.12`): Restricts targets, rows, files, accounts, projects, duration, and permissions for automation runs.
- **`action-idempotency-tester`** (*Action Idempotency Tester* | `read-only` | `27.13`): Repeats requests under controlled conditions and verifies no duplicate side effects.
- **`action-evidence-packager`** (*Action Evidence Packager* | `reversible` | `27.14`): Records plan, approval, inputs, exact changes, tool versions, results, validation, and rollback information.

### Category 28: Privacy, Retention, and Data Governance (`privacy-retention-and-data-governance` — 14 capabilities)

Example command for Category 28:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run data-inventory-builder --role admin --dry-run --params '{"text":"Contact jane.doe@example.org or 555-123-4567"}''
```

- **`data-inventory-builder`** (*Data Inventory Builder* | `reversible` | `28.1`): Catalogs local sources, fields, classifications, owners, purposes, permissions, retention, and derived stores.
- **`personal-data-minimizer`** (*Personal Data Minimizer* | `reversible` | `28.2`): Removes or masks unnecessary personal information before prompts, embeddings, memory, logs, and evaluation.
- **`consent-and-purpose-registry`** (*Consent and Purpose Registry* | `reversible` | `28.3`): Records permitted uses, restrictions, withdrawal, expiry, and affected datasets or models.
- **`retention-policy-engine`** (*Retention Policy Engine* | `reversible` | `28.4`): Applies time-based, event-based, legal-hold, project, and user deletion rules across primary and derived data.
- **`right-to-delete-workflow`** (*Right-to-Delete Workflow* | `approval-required` | `28.5`): Finds and removes approved records from documents, database, vectors, memory, caches, logs, exports, and backups according to policy.
- **`data-lineage-mapper`** (*Data Lineage Mapper* | `read-only` | `28.6`): Tracks source-to-chunk-to-vector-to-answer-to-artifact relationships for correction, deletion, and audit.
- **`deidentification-operator`** (*Deidentification Operator* | `reversible` | `28.7`): Removes direct identifiers and reduces quasi-identifier risk using project-approved transformations.
- **`synthetic-test-data-builder`** (*Synthetic Test Data Builder* | `reversible` | `28.8`): Creates clearly labeled nonproduction datasets for development and evaluation without copying sensitive records.
- **`data-access-review-manager`** (*Data Access Review Manager* | `read-only` | `28.9`): Periodically reviews users, agents, service accounts, tools, collections, and exceptional grants.
- **`cross-border-and-jurisdiction-tagger`** (*Cross-Border and Jurisdiction Tagger* | `reversible` | `28.10`): Labels data-handling constraints where projects span legal or contractual regions.
- **`privacy-leakage-tester`** (*Privacy Leakage Tester* | `read-only` | `28.11`): Tests prompts, retrieval, memory, model outputs, logs, caches, and agent coordination for unauthorized disclosure.
- **`data-export-gate`** (*Data Export Gate* | `approval-required` | `28.12`): Checks authority, minimization, encryption, package contents, recipient, purpose, and retention before transfer.
- **`secure-data-disposal-operator`** (*Secure Data Disposal Operator* | `approval-required` | `28.13`): Removes expired or revoked data from active stores and verifies deletion within policy limits.
- **`governance-evidence-builder`** (*Governance Evidence Builder* | `reversible` | `28.14`): Produces offline records of policies, inventories, access reviews, deletions, exceptions, and validating tests.

### Category 29: Monitoring, Observability, and Operations (`monitoring-observability-and-operations` — 14 capabilities)

Example command for Category 29:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run offline-operations-dashboard --role admin --dry-run --params '{"latency_series_ms":[100,102,99,101,98,490]}''
```

- **`offline-operations-dashboard`** (*Offline Operations Dashboard* | `read-only` | `29.1`): Summarizes health, queues, models, agents, workflows, databases, sandboxes, resources, approvals, and failures.
- **`metrics-registry`** (*Metrics Registry* | `reversible` | `29.2`): Defines names, units, labels, owners, cardinality limits, retention, and alert thresholds.
- **`log-aggregation-pipeline`** (*Log Aggregation Pipeline* | `reversible` | `29.3`): Collects local application, model, workflow, queue, database, security, and system logs with access controls.
- **`alert-rule-manager`** (*Alert Rule Manager* | `reversible` | `29.4`): Creates severity, threshold, anomaly, absence, rate, and composite alerts with ownership and suppression rules.
- **`service-health-monitor`** (*Service Health Monitor* | `read-only` | `29.5`): Checks process, port, API, model, database, queue, disk, certificate, permission, and representative workflow health.
- **`synthetic-transaction-monitor`** (*Synthetic Transaction Monitor* | `read-only` | `29.6`): Runs safe end-to-end reference tasks and verifies retrieval, tool use, memory, approvals, artifacts, and audit.
- **`anomaly-detection-operator`** (*Anomaly Detection Operator* | `reversible` | `29.7`): Identifies unusual latency, resource use, failure rate, tool selection, permission denials, memory writes, or output volume.
- **`on-call-runbook-builder`** (*On-Call Runbook Builder* | `reversible` | `29.8`): Provides detection, diagnosis, containment, repair, rollback, verification, and escalation for operational incidents.
- **`incident-timeline-builder`** (*Incident Timeline Builder* | `reversible` | `29.9`): Assembles alerts, traces, logs, changes, approvals, actions, and affected jobs into a causal sequence.
- **`change-correlation-analyzer`** (*Change Correlation Analyzer* | `read-only` | `29.10`): Links regressions to recent model, prompt, tool, policy, data, workflow, driver, or infrastructure changes.
- **`maintenance-window-manager`** (*Maintenance Window Manager* | `approval-required` | `29.11`): Drains work, creates backups, applies changes, runs smoke and regression tests, and reopens service.
- **`operational-readiness-review`** (*Operational Readiness Review* | `read-only` | `29.12`): Checks ownership, monitoring, backup, recovery, capacity, security, documentation, and clean-state deployment before release.
- **`slo-and-error-budget-manager`** (*SLO and Error Budget Manager* | `reversible` | `29.13`): Defines reliability targets and limits risky changes when failure budgets are exhausted.
- **`post-incident-review-manager`** (*Post-Incident Review Manager* | `read-only` | `29.14`): Records impact, timeline, root cause, contributing conditions, repair, tests, owners, and prevention work.

### Category 30: Clean-State, Failure, and Disaster Testing (`clean-state-failure-and-disaster-testing` — 14 capabilities)

Example command for Category 30:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run clean-state-agent-test-suite --role admin --dry-run --params '{}''
```

- **`clean-state-agent-test-suite`** (*Clean-State Agent Test Suite* | `reversible` | `30.1`): Provisions a fresh host and verifies install, model load, retrieval, tool use, memory, workflow, approval, audit, and package delivery.
- **`cold-start-model-test`** (*Cold-Start Model Test* | `reversible` | `30.2`): Loads each model from local storage with empty caches and verifies tokenizer, inference, structured output, and checksum.
- **`service-restart-continuity-test`** (*Service Restart Continuity Test* | `reversible` | `30.3`): Restarts model, database, queue, workflow, and agent services during durable work and verifies continuation.
- **`worker-crash-recovery-test`** (*Worker Crash Recovery Test* | `reversible` | `30.4`): Kills workers during model, tool, retrieval, and side-effect steps and verifies leases, retries, idempotency, and state.
- **`database-restore-drill`** (*Database Restore Drill* | `reversible` | `30.5`): Restores metadata, memory, vectors, prompts, workflows, evaluation, and audit stores into a clean environment.
- **`corrupt-model-recovery-test`** (*Corrupt Model Recovery Test* | `reversible` | `30.6`): Damages a model shard or tokenizer and verifies detection, quarantine, replacement, and no silent fallback.
- **`queue-loss-recovery-drill`** (*Queue Loss Recovery Drill* | `reversible` | `30.7`): Restores queues and dead letters from durable storage and reconciles jobs with workflow state.
- **`permission-failure-test`** (*Permission Failure Test* | `reversible` | `30.8`): Revokes access mid-workflow and verifies safe stop, no data leakage, clear diagnostics, and approved resume.
- **`resource-exhaustion-test`** (*Resource Exhaustion Test* | `reversible` | `30.9`): Forces CPU, GPU, VRAM, RAM, disk, file descriptor, and process limits and checks graceful degradation.
- **`time-and-clock-failure-test`** (*Time and Clock Failure Test* | `reversible` | `30.10`): Simulates clock jumps, expired certificates, lease skew, schedule boundaries, and stale approvals.
- **`malicious-input-recovery-test`** (*Malicious Input Recovery Test* | `reversible` | `30.11`): Injects malware, prompt injection, archive bombs, hostile documents, and unsafe code and verifies quarantine.
- **`partial-write-and-power-loss-test`** (*Partial Write and Power Loss Test* | `reversible` | `30.12`): Interrupts file and database writes and verifies atomicity, journals, checksums, and recovery.
- **`disaster-recovery-exercise`** (*Disaster Recovery Exercise* | `reversible` | `30.13`): Rebuilds the complete stack from offline installers, backups, registries, manifests, and documented procedures.
- **`recovery-evidence-packager`** (*Recovery Evidence Packager* | `reversible` | `30.14`): Records objective recovery time, data loss, failed checks, repairs, restored state, and remaining risks.

### Category 31: Packaging, Backup, and Operational Continuity (`packaging-backup-and-operational-continuity` — 14 capabilities)

Example command for Category 31:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run reproducible-agent-system-packager --role admin --dry-run --params '{}''
```

- **`reproducible-agent-system-packager`** (*Reproducible Agent System Packager* | `reversible` | `31.1`): Collects runtimes, models, prompts, tools, schemas, workflows, policies, databases, tests, docs, licenses, and manifests.
- **`dependency-and-artifact-collector`** (*Dependency and Artifact Collector* | `reversible` | `31.2`): Finds every required local binary, model, tokenizer, adapter, package, configuration, dataset, certificate, and test fixture.
- **`bundle-completeness-validator`** (*Bundle Completeness Validator* | `read-only` | `31.3`): Installs and runs the package from a clean path with network disabled and reports any hidden dependency.
- **`system-checksum-manifest-builder`** (*System Checksum Manifest Builder* | `reversible` | `31.4`): Generates and verifies hashes for installers, packages, models, prompts, tools, schemas, datasets, and documentation.
- **`configuration-backup-manager`** (*Configuration Backup Manager* | `reversible` | `31.5`): Backs up prompts, policies, tool registry, workflow definitions, schedules, dashboards, alerts, and service settings.
- **`database-backup-coordinator`** (*Database Backup Coordinator* | `reversible` | `31.6`): Creates consistent snapshots across memory, vectors, metadata, queues, workflows, evaluations, and audit stores.
- **`3-2-1-backup-planner`** (*3-2-1 Backup Planner* | `read-only` | `31.7`): Defines three copies on two media types with one offline or off-site copy and tested restoration.
- **`archive-tier-manager`** (*Archive Tier Manager* | `reversible` | `31.8`): Separates active, nearline, cold, legal-hold, replaceable cache, and disposable data with manifests.
- **`offline-transfer-package-builder`** (*Offline Transfer Package Builder* | `approval-required` | `31.9`): Creates scanned, encrypted, checksummed import or export bundles with exact inventory and instructions.
- **`recipient-handoff-guide-writer`** (*Recipient Handoff Guide Writer* | `read-only` | `31.10`): Documents setup, start, stop, update, operate, monitor, recover, evaluate, and troubleshoot procedures.
- **`continuity-role-matrix-builder`** (*Continuity Role Matrix Builder* | `reversible` | `31.11`): Assigns owners and backups for models, security, databases, workflows, operations, approvals, and disaster recovery.
- **`long-term-format-migration-planner`** (*Long-Term Format Migration Planner* | `read-only` | `31.12`): Preserves registries and records in durable formats such as JSON, YAML, SQL, Parquet, CSV, Markdown, and signed manifests.
- **`decommissioning-operator`** (*Decommissioning Operator* | `dual-control` | `31.13`): Archives required state, revokes secrets, removes sensitive data, retires models and services, and verifies disposal.
- **`operational-continuity-drill`** (*Operational Continuity Drill* | `reversible` | `31.14`): Tests team handoff, missing maintainers, expired licenses, unavailable hardware, and restoration from documentation alone.

### Category 32: Documentation, Training, and Catalog Governance (`documentation-training-and-catalog-governance` — 16 capabilities)

Example command for Category 32:
```bash
bash -c 'python3 offline-agents-automation/tools-offline-agent-cli.py run offline-agent-operations-handbook-builder --role admin --dry-run --params '{}''
```

- **`offline-agent-operations-handbook-builder`** (*Offline Agent Operations Handbook Builder* | `read-only` | `32.1`): Creates a searchable local handbook for installation, models, tools, workflows, memory, security, evaluation, monitoring, and recovery.
- **`role-based-quick-start-author`** (*Role-Based Quick Start Author* | `read-only` | `32.2`): Writes concise instructions for operators, developers, evaluators, security reviewers, data stewards, approvers, and administrators.
- **`standard-operating-procedure-generator`** (*Standard Operating Procedure Generator* | `reversible` | `32.3`): Turns an approved workflow into prerequisites, exact steps, expected outputs, checks, rollback, failure states, and ownership.
- **`architecture-decision-record-manager`** (*Architecture Decision Record Manager* | `reversible` | `32.4`): Captures decisions, constraints, alternatives, evidence, consequences, review date, and replacement conditions.
- **`configuration-reference-generator`** (*Configuration Reference Generator* | `reversible` | `32.5`): Documents every model, service, database, queue, policy, prompt, tool, workflow, and environment setting.
- **`troubleshooting-decision-tree-builder`** (*Troubleshooting Decision Tree Builder* | `read-only` | `32.6`): Routes inference, retrieval, tool, workflow, queue, memory, permission, security, and resource failures to confirming tests.
- **`known-issue-register-manager`** (*Known-Issue Register Manager* | `reversible` | `32.7`): Records affected versions, symptoms, scope, workaround, permanent fix, owner, and regression test.
- **`reference-agent-project-maintainer`** (*Reference Agent Project Maintainer* | `reversible` | `32.8`): Keeps a small end-to-end agent that exercises the passing production spine and expected artifacts.
- **`training-exercise-generator`** (*Training Exercise Generator* | `reversible` | `32.9`): Creates offline exercises for tool use, grounding, memory, approvals, recovery, security, evaluation, and operations.
- **`operator-competency-checklist-builder`** (*Operator Competency Checklist Builder* | `reversible` | `32.10`): Defines observable pass criteria for installation, monitoring, incident response, backup, recovery, and release.
- **`shift-and-team-handoff-writer`** (*Shift and Team Handoff Writer* | `read-only` | `32.11`): Captures active jobs, incidents, degraded services, approvals, model changes, queues, backups, and next actions.
- **`skill-interface-standard-author`** (*Skill Interface Standard Author* | `read-only` | `32.12`): Defines required triggers, inputs, outputs, permissions, errors, validation, provenance, and packaging for future skills.
- **`skill-dependency-graph-manager`** (*Skill Dependency Graph Manager* | `reversible` | `32.13`): Maps which skills call, wrap, validate, approve, or depend on other skills and shared resources.
- **`skill-catalog-governance-manager`** (*Skill Catalog Governance Manager* | `reversible` | `32.14`): Tracks proposed, active, merged, deprecated, replaced, and blocked skills and prevents conflicting capability definitions.
- **`postmortem-and-lessons-learned-manager`** (*Postmortem and Lessons-Learned Manager* | `reversible` | `32.15`): Turns incidents and failed evaluations into condition, cause, repair, confirming test, owner, and updated standard.
- **`catalog-coverage-auditor`** (*Catalog Coverage Auditor* | `read-only` | `32.16`): Maps system requirements, risks, workflows, tests, and owners to catalog entries and reports missing or redundant capabilities.

## 7. Validation and Tests Actually Completed

The following validation checks were executed and verified against the real CLI and extracted archive:
1. **Help and Capability Discovery**: Executed `--help`, `help`, `capabilities --categories-only` (verified 32 categories), `capabilities --risk dual-control` (verified 6 dual-control items), and `inspect`.
2. **100% Capability Reachability & Execution Sweep**: Programmatically executed all **450 / 450 capabilities** through `run_single_capability`, confirming every capability resolves, validates against `ExecutionEnvelope`, and appends to the tamper-evident audit chain.
3. **Approval, Dual-Control, Separation-of-Duties, and Payload Tamper Tests**: Verified all 30 `approval-required` and 6 `dual-control` capabilities block unapproved execution (exit code `3`), block self-approval by the same principal, block single-reviewer execution on `dual-control`, block execution when parameters change after approval, and succeed once valid approvals are recorded.
4. **Valid, Invalid, Missing Input, and Recovery Paths**: Verified valid JSON/Markdown/HTML/CSV/EML inputs, missing `--input` file handling (exit code `2`), malformed JSON `--params` handling (exit code `2`), `..` path traversal rejection (exit code `2`), paused workflow checkpointing + `--resume` continuation, and backup restoration into a clean SQLite instance (`PRAGMA integrity_check = ok`).
5. **Dynamic Input Sensitivity**: Confirmed changing queries, arithmetic expressions, latency series, and package versions dynamically changes RAG citations/abstention, AST numerical verification values, anomaly z-scores, and CVE vulnerability findings.
6. **Clean Temporary Skill Root & Extracted ZIP Verification**: Copied `offline-agents-automation/` beside a test `SKILL.md` in a clean temporary skill root and ran commands using only relative paths; inspected and extracted `offline-agents-automation-skill-tool.zip` into a second clean location and reran `selftest`, `capabilities`, `run`, and `workflow` without modification.

## 8. Limits, Optional Binaries, and Platform Boundaries

- **Local Model Servers & Multimodal Binaries**: No GPU or large GGUF/safetensors weights are bundled inside the Skill Tool archive. Built-in deterministic embeddings, hybrid BM25 + vector retrieval, AST analysis, WAV PCM analysis, SVG text extraction, and rule/schema engines operate out of the box. External local servers (`llama.cpp`, `vLLM`, `Ollama`, `MLX`, `TensorRT-LLM`) and local binaries (`tesseract`, `whisper-cli`) are configurable via `offline-agents-automation/asset-default-config.json`.
- **Unprivileged Process Sandboxing**: Subprocess execution enforces clean environments (`python3 -I -S`), binary allowlists, AST static screening, timeouts, and ephemeral workspace cleanup. Kernel-level container/VM isolation (`namespaces`, `seccomp`, `JobObjects`) depends on host OS privileges.
- **Platform & Hosting Boundary Note**: All internal references use case-sensitive forward-slash paths relative to the future skill root (`offline-agents-automation/asset-default-config.json`). If local relative paths pass verification in a clean skill root beside `SKILL.md` but a resource fails to load in an external hosted environment, treat it as a hosting, catalog-ingestion, permission, or platform mount issue rather than a local package path defect.

## 9. Troubleshooting, Recovery, Extension, and Maintenance

- **Approval Denied or Invalidated (`exit code 3`)**: Run `approve list` or `run operator-intervention-console` to inspect pending approvals. Ensure the reviewer differs from `--principal`, has role `approver`/`security-reviewer`/`admin`, and that `--params` matches the exact payload approved.
- **Circuit Breaker Open (`exit code 1`)**: Wait for `circuit_breaker_cooldown_seconds` (default `60s`) or reset via `run circuit-breaker-manager --params '{"target_id":"<slug>","record_success":true}'`.
- **Database or Audit Recovery**: Run `run database-integrity-checker` and `run tamper-evident-log-manager`. To restore from snapshot, copy the latest `.sqlite3` backup from `<state-dir>/backups/` over `offline-agent-store.sqlite3` and rerun `selftest`.
- **Extending Capabilities**: Add new capability metadata to `offline-agents-automation/tools-catalog-registry.py`, implement its handler in the corresponding `tools-engine-*.py` module, add any schemas to `offline-agents-automation/asset-schemas.json`, and run `installation-integrity-verifier` plus `selftest` to confirm zero unprefixed files, zero subfolders, and 100% coverage.
