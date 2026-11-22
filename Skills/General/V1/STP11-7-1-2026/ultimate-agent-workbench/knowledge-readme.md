# Ultimate Agent Workbench

## Scope and honest implementation boundary

This is one reusable Skill Tool component, not a complete Copilot skill. Copy `ultimate-agent-workbench/` directly beside the future parent `SKILL.md`. No SKILL.md is included. Python 3.13 (validated on 3.13.16) is required; the implementation uses only the standard library. Validation was performed on the available Python runtime; no Windows or macOS execution is claimed.

The source PDF specifies 242 skills, including autonomous application development, media generation, legal analysis, research and deployment. **This delivery does not implement all 242 as autonomous domain engines.** It implements an offline workbench with 11 concrete processors and a durable evidence-gated execution interface for every catalog entry. An external capability blocks explicitly until a human supplies reviewed results and local evidence. Catalog registration, synthetic routing tests and human attestation must never be represented as domain execution or independent correctness certification. This is a functional workbench with a material scope limitation relative to the full PDF, not a replacement for every specialist capability.

## Files and loading

- `ultimate-agent-workbench/tools-workbench.py`: primary CLI; loads the local processor module and capability definitions, validates plans, manages SQLite transactions and stage approval, produces structured receipts.
- `ultimate-agent-workbench/tools-local.py`: reusable bounded local processors, file containment and fingerprint helpers; loaded by primary CLI using its adjacent filename, never shell-executed.
- `ultimate-agent-workbench/tools-selftest.py`: isolated real-CLI integration tests, including all 242 evidence workflow routes; copies the component beside a test SKILL.md in a temporary root.
- `ultimate-agent-workbench/asset-capabilities.json`: consumed source-derived names, categories, descriptions, stable IDs and explicit execution limitations for all 242 entries. Editable catalog definitions, not an inventory-only manifest.
- `ultimate-agent-workbench/asset-demo-request.json`: runnable three-task dependency and requirement example.
- `ultimate-agent-workbench/asset-demo-text.txt`: editable source consumed by the demo.
- `ultimate-agent-workbench/knowledge-readme.md`: this sole knowledge file.

There are **7 component files**, no internal subfolders and no installed dependencies. No build step or dependency file is necessary. Internal program resources load relative to the component itself; user paths resolve under the configured workspace. No cloud provider, model, prompt service, credential or endpoint is embedded. Domain engines are intentionally file-exchange integrations rather than unrestricted executable plugins.

## Setup and exact commands

Use a writable future skill root as the working directory. Commands below explicitly use Bash and the case-sensitive prefixed entry point. Python must be on PATH as `python3`.

```bash
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py --help'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py capabilities'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py capabilities --category "Orchestration"'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py plan --input ultimate-agent-workbench/asset-demo-request.json --stages 2 --approve'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py run'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py run --signal Continue'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py status'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py export --output review-receipt.json'
bash -c 'python3 ultimate-agent-workbench/tools-selftest.py'
```

The demo writes `team-handoff.html` and `workbench-state.sqlite` in the working root, outside the component. Do not distribute generated state or outputs inside the component. Re-running plan against an existing project is rejected. Choose another relative `--state` path to start a new project without deletion. Global options precede the subcommand. `--root` configures the workspace; default is the current directory. All input/output/state paths are forward-slash relative paths under that workspace. They cannot traverse out, use drive letters, or resolve through escaping symlinks. Output parent directories must already exist. Avoid untrusted shared writable directories: this is not an OS sandbox and cannot defeat concurrent hostile filesystem mutation.

Without `--approve`, plan persists an unapproved proposal. Approve the chosen count with `replan --stages N --approve`. Replan supports expansion/compression, preserves completed tasks and assigns every unfinished task to a future stage in dependency order. Completed stage count is immutable. One stage is recommended for small requests; the estimator uses task and requirement counts, not an LLM feasibility judgment. The critical path uses supplied duration weights. Parallel layers are scheduling advice; execution is sequential, not parallel worker dispatch. The final stage is only as strong as the integration/inspection tasks explicitly included in the request. No generic engine can infer all acceptance conditions.

## Request and state contracts

UTF-8 JSON request:

```json
{
  "goal": "Validate and release an artifact",
  "requirements": ["verified"],
  "tasks": [{
    "id": "verify",
    "capability": "final-completeness-auditor",
    "operation": "external",
    "params": {},
    "depends": [],
    "duration": 1,
    "files": [],
    "requirements": ["verified"],
    "acceptance": "Reviewer confirms all requested artifacts open and match requirements"
  }]
}
```

`goal`, nonempty `tasks`, each task `id`, known catalog `capability`, and nonempty `acceptance` are required. Task IDs are unique ASCII letters/digits/hyphens. Operations default to `external`. Optional arrays default empty, `params` defaults to an object, duration defaults to 1 and must be finite/positive. Requirements are unique nonempty strings; every requirement must be owned by a task and no task can reference unknown requirements. Dependencies must name existing tasks and must form a DAG. Request task order determines order among peers. Maximum 400 tasks and 100 stages; stages cannot exceed unfinished coherent work packages. `output` is required for operations producing artifact bytes, and is always relative. `files` defines integrity baselines: every file-dependent task should list all of its source files here, even when also named inside params. No automatic discovery of hidden linked resources is claimed.

Plan stores requirement ownership, file SHA-256 baselines, dependency layers, estimated critical path, recommended/selected counts, task results and events in SQLite. Each command updates state atomically under a write transaction, with a 5-second SQLite busy timeout. SQLite is transactional, but artifacts and database commits are not a distributed transaction: a crash after artifact creation can leave an unrecorded output. Run refuses to overwrite it; inspect and move it aside before retrying. Keep project databases private; evidence may contain sensitive text. SQLite provides no encryption or user authentication.

Responses are JSON `{ "ok": true, "result": ... }`, or `{ "ok": false, "error": { "code": ..., "message": ..., "recovery": ... } }` with exit 2. Keyboard interruption exits 130. Argument parsing errors use standard argparse text on stderr and exit 2. A successful `run` command can report `blocked` or `repair`; this is a persisted workflow condition, not completion. Inspect `result.status`. `json-validate` syntax errors fail the command, while unmet required keys return `valid:false`; stages treat that as a failed task. Export includes state, coverage and a completion boolean. Human-reviewed results remain explicitly human-attested.

## Concrete offline operations and examples

Run any operation using a JSON parameter file:

```bash
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py process text-review --input params.json --output text-report.json'
```

Replace `text-review` and write params from the following table. Reports are JSON; `document-html` and `zip-build` require `--output` for the artifact and still print a JSON receipt. Plan tasks use the same operation and params, with artifact paths in task `output`.

| Operation | Example params | Output and scope |
|---|---|---|
| `file-inspect` | `{"files":["input.json"]}` | SHA-256/size and readability; JSON syntax and bounded ZIP/OOXML container integrity where appropriate. No PDF rendering or full OOXML semantics. |
| `text-review` | `{"input":"draft.txt"}` | Word/sentence counts, average sentence length, long sentences and duplicate lines; not a semantic rewrite. |
| `document-html` | `{"input":"draft.txt","title":"Report"}` | Escaped, self-contained editable printable HTML. Plain paragraphs, not Markdown rendering, DOCX or PDF conversion. |
| `csv-analyze` | `{"input":"table.csv"}` | Missing/distinct values, duplicates, finite numeric min/max/sum/mean/median; rejects ragged rows and duplicate headers. No XLSX editing or formula execution. |
| `json-validate` | `{"input":"request.json","required":["goal"]}` | JSON syntax and top-level required keys only; not a full JSON Schema implementation. |
| `python-audit` | `{"input":"program.py"}` | AST syntax and selected dangerous-call warnings. Never executes imported or submitted code. Not a full security audit. |
| `html-audit` | `{"input":"page.html"}` | Static missing alt/lang/label-candidate hints. An input id is only a candidate association; no browser or assistive technology testing. |
| `contrast` | `{"foreground":"#000000","background":"#ffffff"}` | WCAG relative luminance ratio and AA/AAA thresholds for opaque sRGB colors. No alpha compositing. |
| `evidence-search` | `{"input":"notes.txt","query":"release risk"}` | Up to 100 lexical matches with source fingerprint and line numbers, not web search or fact checking. |
| `zip-build` | `{"files":["report.html","data.csv"]}` | Verified ZIP of explicit workspace files with relative names; no nested archives. |
| `catalog-ingest` | `{"input":"capabilities.html"}` | Extracted category/name/description records from Markdown, text or copied HTML; script/style content ignored. |

For unambiguous catalog ingestion use `## Category`, `### Tool Name`, then description paragraphs, or `Tool Name: description`. Numbered name lines are recognized. HTML h1/h2 map to category, h3+ to name. Unmarked prose is inherently ambiguous: the output carries a review warning rather than inventing a certain interpretation. The supplied PDF is already fully extracted into the runtime catalog; runtime PDF/DOCX extraction is not implemented and has no dependency on the build-time PDF reader. Copy text or use an external approved converter before ingesting another PDF/DOCX.

## External work, review and gates

Every source capability is reachable by its exact ID in the following reference. Set operation to `external` to request domain work. `run` stops with the original specification and acceptance criteria. An operator or separately managed agent performs the actual work, places outputs in the workspace, checks acceptance and submits a JSON receipt:

```json
{"passed":true,"summary":"Describe actual checks and findings","reviewer":"Responsible reviewer","evidence":["verified-report.html"]}
```

```bash
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py submit --task verify --input evidence-receipt.json --approve'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py run --signal Continue'
```

Submission requires plan approval, current stage, complete dependencies, unchanged baseline files, nonempty evidence files, reviewer, summary and `passed:true`. Evidence is fingerprinted, not semantically certified. Acceptance criteria are reviewed by the operator, not understood by the engine. Never fabricate evidence to advance a gate. `--approve` is a local operator assertion, not identity verification. Financial transactions, credential operations, publishing, deployments, destructive operations and privileged actions are not implemented. Human approval in this tool does not execute those actions elsewhere.

No automatic network calls, provider execution, shell commands, code compilation, database service modifications, media synthesis, licensed rendering or professional legal/financial conclusions occur. Configure providers/models/prompts/policies in the separately governed executor; this package exchanges local evidence only. The catalog descriptions and task criteria are editable guidance, not executable instructions capable of bypassing these limits.

## Recovery, safety, limits and maintenance

```bash
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py replan --stages 3 --approve'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py cancel'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py recover --task verify --approve'
bash -c 'python3 ultimate-agent-workbench/tools-workbench.py recover --refresh-files --approve'
```

`recover --task` targets failed local tasks, not external tasks awaiting evidence. Without `--task`, all failed tasks reset. Completed tasks/results remain intact; cancellation is cleared. A changed required file blocks stage execution until a replacement is reviewed and `--refresh-files --approve` updates baselines. This does not invalidate prior results automatically: the operator must assess whether an earlier artifact needs rebuilding, and create a new state/project if completed work is no longer valid. Do not refresh merely to hide a provenance mismatch.

Continue accepts only Continue, proceed, go on, next (case-insensitive). Scope-changing replies are rejected. Ctrl-C stops at a Python interruption point, rolls back state and cleans an in-progress temporary output; already-created artifacts remain for inspection. There is no automatic retry loop: failed tasks stop and require explicit approved recovery. Tasks are bounded by input/file/task limits, not a hard wall-clock watchdog; there are no remote calls requiring network timeouts. The self-test enforces a 20-second timeout per child CLI call. For untrusted deployments impose host CPU/memory limits and run in a dedicated process/container.

Limits: 8 MiB per input file, 400 files per inspect/archive, 32 MiB aggregate ZIP input/expanded inspected ZIP, 500 inspected ZIP entries, 100,000 CSV rows. ZIP input names are unique and nested compressed files are refused. Static parsing can still consume significant resources on adversarial inputs: these limits are not a security sandbox. Keep component code and catalog trusted. No built-in telemetry, external addresses, secrets or paid dependencies exist. Overwrites in `process`/`export` require `--approve`; stage artifact outputs never overwrite. Temporary atomic-output files are removed on handled failures. No automatic source deletion or cleanup command is supplied.

Troubleshoot missing input by correcting relative spelling/case or restoring the file. UTF-8 decoding failures require conversion before processing. Resolve SQLite locks by waiting for the active command or ending it safely; do not delete a live database. Backup the database and evidence together while no command is active. For a new release preserve stable capability IDs, update source descriptions deliberately, add bounded local operations in `tools-local.py` and extend the real CLI tests before advertising them. Keep old states readable or explicitly migrate them; state format version is 1 and there is no migration engine. Remove generated user files manually only after retaining required records. Component removal does not remove project state. No hosting/catalog/permission failure can be prevented by local packaging.

## Validation actually performed

The bundled self-test executes main help and capability list, all 11 local operations, changed-input behavior, invalid/missing input, output overwrite refusal, traversal rejection, nested archive refusal, HTML escaping, CSV numeric analysis, source fingerprinting, DAG cycle rejection, uncovered requirement rejection, approval gating, stage expansion/compression, two-stage Continue behavior, export parse, cancellation, failed validation/retry and changed-file refresh recovery. All 242 catalog IDs are exercised through block/submit/complete routing with explicitly synthetic evidence; these are routing tests, not tests of 242 professional/domain outputs. HTML was opened as text and structurally inspected, not visually rendered in a browser. ZIP outputs were reopened and CRC-checked. No specialist service or model was available or tested.

Final packaging checks verify seven direct files, exactly one knowledge file, prefixes, no component SKILL.md, no subfolders, no nested archive or wrapper, extraction and repeated help/demo/self-tests from a clean temporary root with one parent test SKILL.md. The parent test file is not delivered. References below and demo resources are checked case-sensitively. The final delivery message states actual test totals and runtime. No unsupported OS/runtime is claimed.

## Complete catalog workflow reference

For **each** entry below, the concrete interface is a task with `capability` equal to the displayed ID, `operation: "external"`, an acceptance criterion and optional files/dependencies/requirements. Follow the submit example above for its evidence result. Original responsibility and checks are retained verbatim apart from PDF whitespace normalization. These are distinct evidence contracts sharing one execution controller, not 242 autonomous engines. Local operations may be assigned to any entry only when their explicitly limited output actually satisfies the chosen task criterion.

### Orchestration, Planning, and Work Splitting

**Powerful Work Splitter** — `powerful-work-splitter`

Evaluates every request before execution and decides whether one response is sufficient or staged work is safer. It scores scope, file volume, dependencies, tool calls, uncertainty, and verification burden; recommends a prompt count; asks the user to accept or override that count; labels each stage X of Y; pauses at checkpoints; accepts Continue or equivalent; and permits the user to expand or reduce Y midstream. It checks stage completion, remaining dependencies, uploaded-file availability, and whether the final stage includes assembly and polish. It returns an approved stage plan, checkpoint state, continuation instruction, and a verified final package.

**Single-Prompt Feasibility Assessor** — `single-prompt-feasibility-assessor`

Determines whether a task can be completed well in one response. It compares required artifacts, context size, tool limits, risk, and validation steps against a single-turn budget. It checks all requested outputs can be created and checked without deferring essential work. It returns a one-turn decision with reasons and a fallback staged plan.

**Adaptive Prompt Count Estimator** — `adaptive-prompt-count-estimator`

Recommends the number of execution stages. It converts work packages, review gates, file groups, and integration steps into a practical count and recalculates it when scope changes. It checks the count is neither artificially inflated nor too small for safe verification. It returns recommended, minimum, and user-selected stage counts.

**User-Controlled Stage Negotiator** — `user-controlled-stage-negotiator`

Lets the user choose a different stage count from the recommendation. It rebalances workload and quality gates across the chosen count without dropping requirements. It checks every requirement still has an owner stage and final verification. It returns a revised stage map and explicit trade-offs.

**Checkpoint and Continue Controller** — `checkpoint-and-continue-controller`

Stops at safe boundaries and resumes only after the user signals continuation. It recognizes Continue, proceed, go on, next, and equivalent intent while rejecting ambiguous scope-changing replies. It checks saved state identifies completed work, current files, next actions, and blockers. It returns a concise checkpoint receipt and the next completed stage.

**Dynamic Stage Expansion Manager** — `dynamic-stage-expansion-manager`

Extends an active plan when the user requests more stages or new complexity appears. It preserves completed stages, renumbers future stages, redistributes remaining work, and adds new validation gates. It checks prior output remains compatible with the expanded plan. It returns an updated X of Y plan with no lost work.

**Dynamic Stage Compression Manager** — `dynamic-stage-compression-manager`

Compresses an active plan when the user wants fewer stages. It merges compatible work packages while protecting critical checks and final assembly. It checks no safety, accuracy, or deliverable requirement is silently removed. It returns a compressed plan and disclosed quality trade-offs.

**Dependency-Aware Task Decomposer** — `dependency-aware-task-decomposer`

Breaks a goal into atomic, ordered work packages. It maps prerequisites, parallel branches, shared inputs, and integration points. It checks each package has clear inputs, outputs, pass conditions, and successor links. It returns a dependency graph and executable sequence.

**Parallel Workstream Coordinator** — `parallel-workstream-coordinator`

Identifies independent branches that can run in parallel. It separates research, drafting, coding, media, and validation streams and defines merge contracts. It checks parallel outputs use compatible names, schemas, and versions. It returns a parallel execution map and merge plan.

**Critical Path Scheduler** — `critical-path-scheduler`

Prioritizes tasks that control total completion time. It estimates duration, uncertainty, blockers, and downstream impact. It checks critical dependencies are completed before optional polish. It returns a critical path, buffer plan, and escalation triggers.

**Milestone Gatekeeper** — `milestone-gatekeeper`

Prevents later work from starting before required evidence exists. It applies entry and exit criteria to discovery, build, test, and release stages. It checks gate evidence is concrete and reproducible. It returns pass, repair, or blocked status with next action.

**Final Assembly Orchestrator** — `final-assembly-orchestrator`

Combines outputs from all stages into one coherent result. It resolves duplication, naming conflicts, formatting drift, broken references, and missing sections. It checks the assembled artifact opens correctly and satisfies the original request. It returns a polished final package and completion summary.

### Intake, Scope, and Requirement Control

**Intent Resolver** — `intent-resolver`

Finds the user's real desired outcome from incomplete or informal instructions. It translates goals, constraints, examples, and exclusions into testable requirements. It checks assumptions are minimal, explicit, and reversible. It returns an outcome statement and requirement set.

**Requirement Ledger** — `requirement-ledger`

Maintains a living list of every explicit and implied requirement. It assigns identifiers, owners, status, evidence, and acceptance tests. It checks nothing is lost during revisions or multi-stage work. It returns a traceable requirement-to-output matrix.

**Constraint Extractor** — `constraint-extractor`

Detects limits on format, length, tools, tone, jurisdiction, time, and audience. It separates hard constraints from preferences and examples. It checks conflicts and impossible combinations are surfaced early. It returns a prioritized constraint register.

**Ambiguity Triage** — `ambiguity-triage`

Decides which uncertainties can be safely assumed and which require a question. It measures impact, reversibility, and risk before interrupting the user. It checks questions are asked only when answers materially change the result. It returns a resolved assumption list or one focused clarification.

**Success Criteria Designer** — `success-criteria-designer`

Converts broad quality goals into observable pass conditions. It defines correctness, completeness, usability, presentation, and handoff checks. It checks criteria can actually fail and are not vague praise. It returns an acceptance checklist.

**Scope Boundary Guard** — `scope-boundary-guard`

Prevents accidental expansion or omission. It tracks in-scope, out-of-scope, deferred, and newly requested work. It checks changes are reflected in schedule and stage count. It returns an updated scope baseline.

**Change Request Integrator** — `change-request-integrator`

Processes mid-project additions, removals, and substitutions. It assesses impact on files, dependencies, tests, and completed work. It checks the change does not silently invalidate prior stages. It returns a revised plan and affected-item list.

**Assumption Register** — `assumption-register`

Records provisional decisions made under missing information. It links each assumption to impact, evidence, and a trigger for rechecking. It checks temporary guesses are not treated as facts. It returns a reviewable assumption log.

**Acceptance Test Author** — `acceptance-test-author`

Writes practical tests for each deliverable. It covers normal, boundary, failure, recovery, accessibility, and clean-state use. It checks tests map directly to requirements. It returns an executable or inspectable test plan.

**Definition-of-Done Enforcer** — `definition-of-done-enforcer`

Blocks completion claims until all required artifacts and checks pass. It verifies content, structure, packaging, naming, and reopenability. It checks partial work is labeled accurately. It returns a completion decision with remaining defects if any.

### File Intake, Recovery, and Asset Management

**Universal File Intake** — `universal-file-intake`

Inventories all attached and generated files before work begins. It records name, type, size, readability, duplication, and intended use. It checks every referenced file is present and accessible. It returns a file inventory and processing plan.

**Missing File Detector** — `missing-file-detector`

Finds files mentioned by the user but unavailable to the agent. It compares instructions, links, manifests, and attachment inventory. It checks work does not proceed on fabricated content. It returns a precise re-upload request naming each missing file.

**Corrupt File Triage** — `corrupt-file-triage`

Diagnoses unreadable or damaged files without overwriting originals. It checks signatures, containers, embedded relationships, encoding, and recoverable content. It checks recovery preserves the source and documents limitations. It returns a repaired copy or actionable recovery report.

**Re-Upload Recovery Coordinator** — `re-upload-recovery-coordinator`

Restores interrupted work after a file is uploaded again. It matches the replacement to prior state, revalidates dependencies, and reruns affected checks. It checks stale results from the missing version are not reused. It returns a resumed plan from the exact safe checkpoint.

**Archive and Package Extractor** — `archive-and-package-extractor`

Safely inspects ZIP, TAR, and nested packages. It prevents path traversal, inventories contents, and preserves directory structure. It checks expected files exist and suspicious entries are isolated. It returns an extraction directory and manifest.

**File Format Identifier** — `file-format-identifier`

Identifies actual format rather than trusting the extension. It uses magic bytes, container structure, and parse tests. It checks mislabeled or unsupported files are flagged. It returns a verified format map.

**Duplicate and Version Resolver** — `duplicate-and-version-resolver`

Detects duplicate, near-duplicate, and conflicting versions. It compares hashes, metadata, content, and modification history. It checks the selected source of truth is explicit. It returns a deduplicated set and version decision.

**Asset Naming Normalizer** — `asset-naming-normalizer`

Applies consistent, portable file and folder names. It removes unsafe characters, resolves collisions, and preserves extensions. It checks links and scripts still resolve after renaming. It returns a normalized asset tree and rename map.

**Cross-File Reference Validator** — `cross-file-reference-validator`

Checks links among documents, sheets, slides, code, and media. It finds broken paths, missing relationships, and stale references. It checks all internal dependencies resolve in the final package. It returns a reference repair report.

**Clean-State Package Tester** — `clean-state-package-tester`

Copies the deliverable into a clean location and opens or runs it there. It detects hidden dependencies on temporary paths, caches, or local settings. It checks the recipient can use the package independently. It returns a clean-state pass result.

### Research, Search, and Evidence

**Research Question Builder** — `research-question-builder`

Turns a broad topic into answerable subquestions. It defines evidence needs, date range, geography, source hierarchy, and exclusions. It checks subquestions collectively cover the decision. It returns a scoped research plan.

**Authoritative Source Finder** — `authoritative-source-finder`

Prioritizes primary and official sources. It locates standards, laws, product documentation, datasets, and original studies. It checks sources are current enough and directly support claims. It returns a source set with relevance notes.

**Multi-Source Evidence Synthesizer** — `multi-source-evidence-synthesizer`

Combines findings without flattening disagreements. It compares definitions, methods, dates, populations, and confidence. It checks conflicts and gaps remain visible. It returns a balanced synthesis with claim-level support.

**Claim Verification Auditor** — `claim-verification-auditor`

Tests each important factual claim against evidence. It checks names, dates, numbers, quotations, and causal wording. It checks unsupported claims are removed or clearly qualified. It returns a verified claim ledger.

**Source Freshness Monitor** — `source-freshness-monitor`

Detects when time-sensitive evidence may be stale. It checks publication date, update date, version, and superseding notices. It checks current claims use current sources. It returns a freshness status and refresh list.

**Citation Integrity Checker** — `citation-integrity-checker`

Ensures citations point to the correct supporting material. It checks source identity, locator, quotation fidelity, and claim scope. It checks citations are neither decorative nor misplaced. It returns a corrected citation set.

**Conflicting Evidence Resolver** — `conflicting-evidence-resolver`

Analyzes credible disagreement rather than choosing silently. It identifies methodological, jurisdictional, temporal, and definitional causes. It checks the final conclusion reflects uncertainty honestly. It returns a conflict matrix and bounded conclusion.

**Dataset Provenance Tracker** — `dataset-provenance-tracker`

Records where data came from and how it changed. It captures source, license, retrieval date, filters, transformations, and joins. It checks results can be reproduced. It returns a provenance record and transformation chain.

**Literature Gap Mapper** — `literature-gap-mapper`

Finds unanswered areas and weak evidence. It classifies missing populations, methods, variables, replications, and time periods. It checks gaps are distinguished from search failure. It returns a prioritized gap map.

**Research Brief Producer** — `research-brief-producer`

Converts evidence into a decision-ready brief. It summarizes findings, confidence, trade-offs, implications, and next steps. It checks every major recommendation traces to evidence. It returns a concise research brief.

### Knowledge, Context, and Memory

**Context Window Curator** — `context-window-curator`

Selects the most relevant material for the current stage. It compresses prior work while preserving requirements, decisions, interfaces, and blockers. It checks critical details survive summarization. It returns a stage-ready context packet.

**Long-Project State Keeper** — `long-project-state-keeper`

Maintains continuation state across pauses. It stores completed stages, open issues, file versions, decisions, and next commands. It checks resumption does not repeat or contradict prior work. It returns a durable project snapshot.

**Decision Memory Manager** — `decision-memory-manager`

Stores decisions with the conditions that made them valid. It records alternatives, rationale, evidence, owner, date, and reversal triggers. It checks outdated decisions are rechecked when conditions change. It returns a decision register.

**Preference Learner** — `preference-learner`

Applies stable user preferences without overgeneralizing. It separates durable preferences from one- time requests. It checks current instructions always override remembered defaults. It returns a personalized but controllable output profile.

**Knowledge Deduplicator** — `knowledge-deduplicator`

Merges repeated notes and overlapping facts. It preserves exceptions, provenance, and the strongest wording. It checks important nuance is not erased. It returns a compact canonical knowledge base.

**Contradiction Detector** — `contradiction-detector`

Finds incompatible statements across instructions and files. It classifies direct conflict, scope difference, version difference, and apparent conflict. It checks material conflicts are resolved before execution. It returns a contradiction report and chosen rule.

**Terminology Controller** — `terminology-controller`

Maintains consistent names, acronyms, and definitions. It creates a glossary and checks every artifact for drift. It checks domain terms remain accurate and audience-appropriate. It returns a controlled vocabulary.

**Context Drift Monitor** — `context-drift-monitor`

Detects when later work diverges from the original goal. It compares stage outputs to the requirement ledger and success criteria. It checks drift is repaired before final assembly. It returns a drift alert and correction plan.

**Handoff Context Packager** — `handoff-context-packager`

Prepares all information another agent or person needs to continue. It includes state, files, commands, decisions, risks, and next tests. It checks the recipient can continue without rediscovery. It returns a concise continuation packet.

**Memory Privacy Filter** — `memory-privacy-filter`

Prevents sensitive or temporary information from becoming durable memory. It classifies data by sensitivity, purpose, retention, and user intent. It checks only appropriate information persists. It returns a sanitized memory update.

### Writing, Editing, and Communication

**Audience-Aware Writer** — `audience-aware-writer`

Adapts content to the reader's knowledge, role, and decision needs. It controls terminology, depth, examples, tone, and call to action. It checks the message is understandable without losing accuracy. It returns a reader-fit draft.

**Structural Editor** — `structural-editor`

Improves organization before sentence-level polishing. It repairs hierarchy, sequence, transitions, duplication, and missing sections. It checks each section has a clear purpose. It returns a coherent revised document.

**Line Editor** — `line-editor`

Improves clarity and precision sentence by sentence. It removes ambiguity, clutter, repetition, weak verbs, and inconsistent terms. It checks meaning and voice are preserved. It returns a polished draft.

**Plain-Language Rewriter** — `plain-language-rewriter`

Makes complex material easier to understand. It shortens sentences, defines terms, and replaces unnecessary jargon. It checks legal or technical precision is retained. It returns an accessible plain- language version.

**Tone and Neutrality Controller** — `tone-and-neutrality-controller`

Aligns voice with context while avoiding manipulation or disrespect. It checks confidence, empathy, bias, formality, and emotional temperature. It checks sensitive topics remain balanced and humane. It returns a tone-calibrated draft.

**Executive Summary Builder** — `executive-summary-builder`

Condenses long material for decision-makers. It extracts the decision, evidence, risks, options, and recommendation. It checks the summary stands alone and matches the source. It returns a one-page or brief executive summary.

**Email and Message Composer** — `email-and-message-composer`

Creates concise workplace communications. It optimizes subject, opening, request, context, deadline, and close. It checks the recipient knows exactly what to do. It returns multiple tone-distinct drafts.

**Argument Mapper** — `argument-mapper`

Makes reasoning explicit and testable. It separates claims, evidence, warrants, objections, and rebuttals. It checks logical gaps and overclaims are visible. It returns an argument map and strengthened narrative.

**Consistency Proofreader** — `consistency-proofreader`

Finds grammar, spelling, punctuation, capitalization, numbering, and style drift. It uses a defined style sheet and checks headings, lists, captions, and references. It checks corrections do not change intended meaning. It returns a publication-ready copy.

**Localization Writer** — `localization-writer`

Adapts meaning for a target language and culture. It handles locale, idioms, dates, units, politeness, and domain terminology. It checks the result reads naturally and preserves intent. It returns a localized version and terminology notes.

### Document and PDF Production

**Word Document Architect** — `word-document-architect`

Creates editable DOCX documents with professional structure. It uses styles, headings, tables, headers, footers, sections, navigation, and accessibility settings. It checks the document renders without clipping or broken layout. It returns a polished editable DOCX.

**PDF Publication Builder** — `pdf-publication-builder`

Produces a stable PDF from an authoritative source document. It controls page size, fonts, bookmarks, links, metadata, and export settings. It checks the PDF renders consistently and text remains selectable where appropriate. It returns a distribution-ready PDF.

**Document Template Engineer** — `document-template-engineer`

Builds reusable templates. It defines styles, placeholders, content controls, instructions, and protected regions. It checks new documents remain consistent without manual formatting. It returns an editable template and usage guide.

**Multi-Document Merger** — `multi-document-merger`

Combines multiple documents while preserving structure. It normalizes styles, sections, numbering, references, and page setup. It checks the merged file has no duplicate controls or broken fields. It returns one coherent master document.

**Document Accessibility Auditor** — `document-accessibility-auditor`

Checks headings, reading order, alt text, tables, links, contrast, and language. It applies safe fixes and flags judgment-dependent issues. It checks the artifact supports assistive technology. It returns an accessible document and audit report.

**Tracked-Change Finalizer** — `tracked-change-finalizer`

Processes revisions safely. It accepts or rejects changes by policy, verifies remaining markup, and preserves a review copy. It checks the final version contains no unintended redlines or comments. It returns a clean final and optional marked copy.

**Document Metadata Scrubber** — `document-metadata-scrubber`

Removes hidden personal and revision data. It cleans authors, comments, custom properties, paths, and editing history where supported. It checks visible content and functionality remain intact. It returns a privacy-scrubbed copy.

**Form and Content-Control Builder** — `form-and-content-control-builder`

Creates fillable document fields. It defines labels, types, validation, defaults, and protection. It checks fields are keyboard-accessible and survive saving. It returns a usable fillable document.

**Table and Figure Manager** — `table-and-figure-manager`

Standardizes tables, figures, captions, and cross-references. It controls widths, repeats headers, adds alt text, and updates references. It checks objects do not overflow pages or lose numbering. It returns a visually stable illustrated document.

**Render and Layout Inspector** — `render-and-layout-inspector`

Renders every page and checks the actual pixels. It detects clipping, overlap, blank pages, broken glyphs, and header or footer drift. It checks all pages pass at readable zoom. It returns a verified publication artifact.

### Spreadsheet, Data, and Analytics

**Data Intake Profiler** — `data-intake-profiler`

Profiles tabular data before analysis. It measures types, missingness, uniqueness, ranges, duplicates, and suspicious values. It checks issues are quantified rather than guessed. It returns a data-quality profile.

**Schema and Type Validator** — `schema-and-type-validator`

Checks data against a declared schema. It validates required fields, types, formats, enums, keys, and relationships. It checks invalid rows are isolated with reasons. It returns a validated dataset and error table.

**Data Cleaning Planner** — `data-cleaning-planner`

Designs safe, reproducible cleaning operations. It separates correction, imputation, exclusion, normalization, and enrichment. It checks raw data remains preserved. It returns a transformation plan and cleaned copy.

**Spreadsheet Formula Engineer** — `spreadsheet-formula-engineer`

Builds maintainable formulas and models. It uses clear references, named ranges, error handling, and documented assumptions. It checks formulas recalculate without circular errors. It returns an auditable workbook.

**Pivot and Summary Builder** — `pivot-and-summary-builder`

Creates decision-ready summaries from detailed records. It selects dimensions, measures, filters, totals, and drill paths. It checks aggregations reconcile to source data. It returns a compact summary workbook.

**Statistical Analysis Runner** — `statistical-analysis-runner`

Applies appropriate descriptive or inferential methods. It checks assumptions, sample size, uncertainty, effect size, and multiple comparisons. It checks results are interpreted within design limits. It returns an analysis with reproducible calculations.

**Data Visualization Designer** — `data-visualization-designer`

Chooses charts that match the question and data. It controls scale, labels, units, ordering, annotations, and accessibility. It checks visuals do not distort comparisons. It returns clear charts with plain- language insights.

**Forecasting and Scenario Modeler** — `forecasting-and-scenario-modeler`

Builds bounded projections. It separates historical fit, assumptions, scenarios, uncertainty, and sensitivity. It checks forecasts are not presented as certainty. It returns a scenario model with ranges.

**Reconciliation Auditor** — `reconciliation-auditor`

Compares totals across sources and transformations. It traces discrepancies through filters, joins, currency, dates, and rounding. It checks differences are explained or corrected. It returns a reconciliation statement.

**Workbook QA Inspector** — `workbook-qa-inspector`

Checks formulas, formats, filters, hidden content, print areas, and error cells. It tests representative inputs and boundary cases. It checks the workbook opens and behaves correctly. It returns a verified spreadsheet package.

### Coding and Software Engineering

**Repository Mapper** — `repository-mapper`

Builds a structural map of an unfamiliar codebase. It identifies entry points, modules, dependencies, tests, configs, and generated files. It checks important runtime paths are traced. It returns an architecture map.

**Implementation Planner** — `implementation-planner`

Turns a feature request into code changes. It defines interfaces, files, migrations, tests, rollout, and rollback. It checks the plan fits existing conventions. It returns a change plan with acceptance tests.

**Code Generator** — `code-generator`

Produces maintainable code from an explicit contract. It follows language idioms, project style, typing, errors, logging, and security requirements. It checks generated code compiles or parses and integrates with existing interfaces. It returns working source files.

**Bug Reproducer** — `bug-reproducer`

Reduces a failure to the smallest repeatable case. It captures environment, inputs, expected behavior, observed behavior, and earliest bad state. It checks the reproduction fails for the right reason. It returns a minimal failing test.

**Root Cause Debugger** — `root-cause-debugger`

Finds the smallest responsible cause. It separates input, state, logic, dependency, environment, build, and interface layers. It checks probes distinguish competing hypotheses. It returns a targeted repair.

**Regression Test Author** — `regression-test-author`

Locks repaired behavior into automated tests. It covers the original failure, normal use, boundaries, and nearby joins. It checks the test fails before the fix and passes after it. It returns a focused regression suite.

**Code Review Auditor** — `code-review-auditor`

Reviews changes for correctness and maintainability. It checks logic, APIs, tests, security, performance, readability, and migration risk. It checks findings are prioritized and actionable. It returns a severity- ranked review.

**Dependency Upgrade Manager** — `dependency-upgrade-manager`

Updates libraries without uncontrolled breakage. It checks release notes, API changes, lockfiles, transitive risk, and compatibility tests. It checks the build and critical workflows pass after upgrade. It returns an upgrade patch and rollback note.

**Refactoring Safeguard** — `refactoring-safeguard`

Improves structure while preserving behavior. It establishes tests, performs small transformations, and rechecks affected paths. It checks public contracts remain stable unless intentionally changed. It returns cleaner code with regression evidence.

**Release Build Verifier** — `release-build-verifier`

Tests production configuration rather than development only. It builds from clean state and checks artifacts, environment variables, packaging, and startup. It checks the release artifact runs through the real user path. It returns a releasable build.

### Web Application Engineering

**Web Architecture Designer** — `web-architecture-designer`

Defines a practical frontend and backend architecture. It selects rendering, routing, state, API, auth, persistence, and deployment patterns. It checks choices fit scale, team, accessibility, and maintenance needs. It returns an architecture specification.

**Responsive Interface Builder** — `responsive-interface-builder`

Creates interfaces that adapt across viewport sizes. It uses semantic HTML, mobile-first layout, resilient components, and touch-safe controls. It checks key flows work on phone, tablet, and desktop. It returns a responsive UI.

**Design System Implementer** — `design-system-implementer`

Builds reusable tokens and components. It defines color, typography, spacing, states, variants, and documentation. It checks components remain consistent and accessible. It returns a reusable component library.

**API Integration Engineer** — `api-integration-engineer`

Connects the interface to services reliably. It handles schemas, authentication, retries, loading, errors, caching, and pagination. It checks failure states are visible and recoverable. It returns a tested integration layer.

**Authentication Flow Builder** — `authentication-flow-builder`

Implements secure sign-in and session behavior. It covers registration, recovery, verification, roles, expiry, logout, and protected routes. It checks secrets and tokens are handled safely. It returns a complete authentication flow.

**Web Accessibility Tester** — `web-accessibility-tester`

Evaluates keyboard, focus, semantics, contrast, labels, and screen-reader behavior. It tests critical journeys against WCAG-aligned checks. It checks blocking barriers are repaired. It returns an accessible web experience.

**Performance Optimizer** — `performance-optimizer`

Improves loading and interaction speed. It measures bundle size, render cost, images, fonts, caching, network waterfalls, and Core Web Vitals. It checks optimizations do not break functionality. It returns a faster verified build.

**Cross-Browser Validator** — `cross-browser-validator`

Tests supported browsers and device classes. It checks layout, APIs, input methods, storage, and media behavior. It checks known differences have fallbacks. It returns a compatibility report and fixes.

**SEO and Metadata Engineer** — `seo-and-metadata-engineer`

Improves discoverability and sharing. It configures titles, descriptions, canonical URLs, structured data, robots, sitemap, and social cards. It checks metadata matches visible content. It returns an indexable site build.

**Production Deployment Verifier** — `production-deployment-verifier`

Deploys or validates the production route. It checks domains, HTTPS, headers, environment, migrations, logs, rollback, and smoke tests. It checks the public path behaves like the tested build. It returns a verified release.

### APIs, Databases, and Backend Systems

**API Contract Designer** — `api-contract-designer`

Defines stable service interfaces. It specifies resources, methods, schemas, errors, pagination, idempotency, and versioning. It checks clients can implement without hidden assumptions. It returns an API specification.

**Database Schema Designer** — `database-schema-designer`

Models data with integrity and evolution in mind. It defines keys, constraints, indexes, relationships, retention, and migration paths. It checks common queries are supported efficiently. It returns a schema and migration plan.

**Query Optimizer** — `query-optimizer`

Improves slow database access. It uses execution plans, indexes, cardinality, batching, and query rewrites. It checks performance gains are measured and correctness preserved. It returns an optimized query set.

**Migration Safety Manager** — `migration-safety-manager`

Plans and validates schema or data migrations. It supports backfill, dual-read or dual-write, verification, rollback, and downtime limits. It checks production data remains consistent. It returns a reversible migration runbook.

**Background Job Orchestrator** — `background-job-orchestrator`

Designs reliable asynchronous work. It handles queues, retries, idempotency, ordering, timeouts, dead letters, and observability. It checks duplicate execution is safe. It returns a resilient job workflow.

**Cache Strategy Engineer** — `cache-strategy-engineer`

Adds caching without stale-data surprises. It defines keys, TTLs, invalidation, consistency, and fallback behavior. It checks cache failures do not corrupt results. It returns a measured caching plan.

**Webhook Reliability Handler** — `webhook-reliability-handler`

Processes external events safely. It verifies signatures, deduplicates events, preserves order where needed, and retries failures. It checks replays are safe and observable. It returns a robust webhook consumer.

**Data Backup and Restore Tester** — `data-backup-and-restore-tester`

Validates recovery rather than only backup creation. It checks schedules, encryption, retention, restore time, and restored integrity. It checks a restore succeeds in a clean environment. It returns a recovery- tested backup system.

**Service Observability Builder** — `service-observability-builder`

Adds actionable metrics, logs, traces, and alerts. It links requests across components and defines service-level indicators. It checks alerts correspond to user impact. It returns an observable backend.

**Rate Limit and Abuse Guard** — `rate-limit-and-abuse-guard`

Protects services from overload and misuse. It applies quotas, burst control, identity, backoff, and clear errors. It checks legitimate clients remain usable. It returns a tested traffic policy.

### Security, Privacy, and Safety

**Threat Modeler** — `threat-modeler`

Identifies realistic threats before implementation. It maps assets, trust boundaries, actors, attack paths, controls, and residual risk. It checks high-impact paths have mitigations and tests. It returns a prioritized threat model.

**Secret Exposure Scanner** — `secret-exposure-scanner`

Finds credentials and sensitive tokens in files and repositories. It checks source, history, configs, logs, archives, and generated artifacts. It checks exposed secrets are revoked rather than merely deleted. It returns a remediation list.

**Input Validation Engineer** — `input-validation-engineer`

Designs validation at trust boundaries. It defines allowlists, schemas, size limits, encoding, normalization, and safe errors. It checks malformed input cannot reach dangerous operations. It returns a validated input layer.

**Authorization Auditor** — `authorization-auditor`

Checks who can do what to which resource. It tests roles, ownership, tenant isolation, indirect references, and privilege changes. It checks denials occur server-side and by default. It returns an access-control report.

**Privacy Data Mapper** — `privacy-data-mapper`

Maps personal and sensitive data through the system. It records purpose, source, fields, storage, sharing, retention, and deletion. It checks data collection is minimized. It returns a privacy inventory.

**Redaction Verifier** — `redaction-verifier`

Confirms sensitive content is actually removed. It checks visible layers, underlying text, metadata, attachments, OCR, and revision history. It checks redacted information cannot be recovered through ordinary extraction. It returns a verified sanitized artifact.

**Secure Configuration Auditor** — `secure-configuration-auditor`

Reviews production settings for unsafe defaults. It checks TLS, headers, permissions, debug modes, storage, network exposure, and logging. It checks critical settings are explicit and tested. It returns a hardened configuration.

**Dependency Vulnerability Triage** — `dependency-vulnerability-triage`

Evaluates reported package weaknesses. It checks exploitability, reachable code, fixed versions, breaking changes, and compensating controls. It checks responses are prioritized by real risk. It returns a remediation plan.

**Safety Boundary Enforcer** — `safety-boundary-enforcer`

Detects requests or actions that could cause meaningful harm. It separates benign, dual-use, restricted, and disallowed components. It checks safe portions remain useful. It returns a bounded safe response.

**Incident Response Coordinator** — `incident-response-coordinator`

Structures containment and recovery after a security event. It preserves evidence, limits spread, rotates access, restores service, and records lessons. It checks actions are timed and owned. It returns an incident runbook and recovery state.

### Testing, Verification, and Quality Assurance

**Requirement Coverage Auditor** — `requirement-coverage-auditor`

Checks every requirement against an artifact and test. It finds missing, partial, conflicting, and unverified items. It checks coverage claims have evidence. It returns a coverage matrix.

**Boundary Case Generator** — `boundary-case-generator`

Creates cases at limits and transitions. It covers empty, missing, repeated, extreme, malformed, and unusual inputs. It checks cases target real failure modes. It returns a boundary test set.

**Integration Join Tester** — `integration-join-tester`

Tests where modules, files, tools, or stages meet. It validates schemas, names, units, ordering, versions, and error contracts. It checks interfaces work end to end. It returns an integration test report.

**Visual Regression Inspector** — `visual-regression-inspector`

Compares rendered outputs at the pixel or layout level. It detects overlap, clipping, unexpected movement, color changes, and missing elements. It checks intentional changes are distinguished from defects. It returns a visual diff review.

**Accessibility QA Runner** — `accessibility-qa-runner`

Tests usable access beyond visual correctness. It checks keyboard operation, screen readers, captions, language, focus, and alternatives. It checks critical user journeys remain available. It returns an accessibility pass report.

**Performance Benchmark Runner** — `performance-benchmark-runner`

Measures latency, throughput, memory, size, and stability. It uses repeatable workloads, warmup, percentiles, and baselines. It checks comparisons use the same environment and limits. It returns a benchmark report.

**Fuzz and Robustness Tester** — `fuzz-and-robustness-tester`

Explores unexpected inputs systematically. It generates malformed structures, random values, and adversarial sequences within safe bounds. It checks crashes and unsafe states are reproducible. It returns a minimized failure corpus.

**Clean-Room Reproduction Tester** — `clean-room-reproduction-tester`

Verifies setup from an empty environment. It follows only documented dependencies and commands. It checks hidden local state is eliminated. It returns a clean-install result.

**Release Gate Evaluator** — `release-gate-evaluator`

Combines required checks into a go or no-go decision. It enforces severity thresholds, waivers, owners, and expiration dates. It checks critical failures cannot be averaged away. It returns a release decision.

**Post-Delivery Validator** — `post-delivery-validator`

Reopens and uses the exact final artifacts. It checks links, archives, permissions, installation, and recipient workflow. It checks the delivered copy matches the tested copy. It returns a final handoff pass.

### Automation, Tools, and Operations

**Tool Capability Profiler** — `tool-capability-profiler`

Learns what a tool can and cannot do before using it. It records inputs, outputs, side effects, dependencies, limits, costs, and failure signals. It checks the selected tool matches the requirement. It returns a capability profile.

**Minimal Toolchain Selector** — `minimal-toolchain-selector`

Chooses the smallest set of tools that can complete the job. It compares fidelity, compatibility, editability, repeatability, and repair cost. It checks unnecessary dependencies are avoided. It returns a justified toolchain.

**Workflow Automation Builder** — `workflow-automation-builder`

Turns repeatable manual steps into automation. It defines triggers, inputs, state, idempotency, errors, retries, approvals, and logs. It checks the workflow can recover from interruption. It returns an automated pipeline.

**Command Safety Wrapper** — `command-safety-wrapper`

Makes shell or operational commands safer. It adds dry-run, quoting, path checks, backups, timeouts, and clear exit handling. It checks destructive actions require explicit boundaries. It returns a guarded command flow.

**Environment Bootstrapper** — `environment-bootstrapper`

Creates a reproducible working environment. It pins dependencies, checks versions, initializes config, and verifies prerequisites. It checks setup works from clean state. It returns a bootstrap script and setup guide.

**Batch Job Manager** — `batch-job-manager`

Processes many files or records predictably. It supports manifests, concurrency, checkpoints, retries, skip rules, and summaries. It checks partial failures do not erase successful work. It returns a resumable batch run.

**Scheduled Task Designer** — `scheduled-task-designer`

Creates reliable recurring work. It handles timezone, overlap, missed runs, holidays, credentials, and alerting. It checks duplicate or delayed runs remain safe. It returns a schedule and operational policy.

**Log Analyzer** — `log-analyzer`

Extracts useful signals from operational logs. It parses structure, correlates events, detects patterns, and links symptoms to components. It checks sensitive fields are protected. It returns an incident- focused analysis.

**Rollback Planner** — `rollback-planner`

Defines how to reverse a change safely. It captures backups, compatibility, data migration limits, triggers, and validation. It checks rollback is tested where feasible. It returns a timed rollback runbook.

**Operational Runbook Writer** — `operational-runbook-writer`

Documents real procedures for operators. It includes prerequisites, commands, expected output, failure branches, escalation, and recovery. It checks a new operator can execute it safely. It returns a production runbook.

### Image and Visual Design

**Visual Brief Interpreter** — `visual-brief-interpreter`

Converts a goal into an actionable image brief. It defines subject, style, composition, lighting, color, aspect ratio, usage, and exclusions. It checks the brief avoids contradictory directions. It returns a production-ready visual specification.

**Text-to-Image Art Director** — `text-to-image-art-director`

Guides image generation toward a coherent result. It controls scene, perspective, materials, atmosphere, realism, and negative constraints. It checks generated visuals match purpose and do not invent factual details. It returns a curated image set.

**Single-Image Editor** — `single-image-editor`

Applies targeted changes to one image. It handles object removal, replacement, recoloring, relighting, cleanup, crop, and background work. It checks unchanged regions remain stable. It returns an edited high-quality image.

**Multi-Image Consistency Editor** — `multi-image-consistency-editor`

Coordinates edits across an image set. It matches identity, palette, lighting, scale, camera, and export settings. It checks the set feels intentionally unified. It returns a consistent asset family.

**Image Restoration Specialist** — `image-restoration-specialist`

Repairs damaged or low-quality images. It reduces noise, scratches, compression, blur, and color fading while preserving identity. It checks restoration does not fabricate critical detail. It returns a restored image and limitation note.

**Background Removal and Compositor** — `background-removal-and-compositor`

Separates subjects and builds clean composites. It refines hair, edges, shadows, color spill, and perspective. It checks the result holds up on light and dark backgrounds. It returns a transparent asset or finished composite.

**Diagram and Infographic Designer** — `diagram-and-infographic-designer`

Turns concepts or processes into readable visuals. It chooses hierarchy, flow, icons, labels, grouping, and scale. It checks the visual can be understood without decorative clutter. It returns a clear explanatory graphic.

**Brand Asset System Builder** — `brand-asset-system-builder`

Creates a coherent family of visual assets. It defines palette, typography, image treatment, shapes, and usage rules. It checks assets remain consistent across formats. It returns a reusable brand kit.

**Image Accessibility Reviewer** — `image-accessibility-reviewer`

Checks whether visuals communicate inclusively. It reviews contrast, text size, color dependence, alt text needs, and cultural interpretation. It checks essential meaning has a nonvisual alternative. It returns an accessible image package.

**Image Export Optimizer** — `image-export-optimizer`

Prepares assets for their final channel. It selects dimensions, format, compression, transparency, color profile, and naming. It checks quality and file size meet platform needs. It returns optimized exports.

### Video, Motion, and Multimedia

**Video Concept Developer** — `video-concept-developer`

Transforms a message into a producible video concept. It defines audience, runtime, narrative arc, format, visual language, and call to action. It checks the concept fits resources and channel. It returns a treatment and success criteria.

**Storyboard Builder** — `storyboard-builder`

Plans shots before production. It maps framing, action, transitions, narration, text, timing, and assets. It checks every key message has a visual beat. It returns a scene-by-scene storyboard.

**Text-to-Video Director** — `text-to-video-director`

Creates precise generation instructions for video models. It controls subject continuity, camera movement, motion, environment, style, and duration. It checks prompts avoid impossible motion and unwanted text. It returns a generation plan and shot set.

**Image-to-Video Animator** — `image-to-video-animator`

Turns still images into controlled motion. It defines depth, parallax, camera path, subject movement, and loop behavior. It checks identity and composition remain stable. It returns a polished animated clip.

**Video Editor** — `video-editor`

Assembles footage into a coherent program. It selects takes, trims pacing, builds continuity, mixes graphics, and manages versions. It checks the edit serves the intended narrative. It returns a finished timeline.

**Motion Graphics Designer** — `motion-graphics-designer`

Creates titles, lower thirds, transitions, and explanatory animation. It uses consistent typography, easing, timing, and safe areas. It checks graphics remain legible on target screens. It returns a motion graphics package.

**Caption and Subtitle Producer** — `caption-and-subtitle-producer`

Creates accurate timed text. It handles segmentation, speaker labels, sound cues, reading speed, and localization. It checks captions synchronize and remain readable. It returns caption files and burned-in option.

**Audio-Video Sync Inspector** — `audio-video-sync-inspector`

Detects and repairs synchronization problems. It checks lip sync, drift, frame rate, sample rate, and edit boundaries. It checks sync holds across the full export. It returns a corrected master.

**Video Quality Controller** — `video-quality-controller`

Reviews picture, sound, captions, encoding, and playback. It checks black frames, clipping, artifacts, loudness, aspect ratio, and metadata. It checks the master plays correctly in target channels. It returns a verified delivery file.

**Multimedia Package Assembler** — `multimedia-package-assembler`

Combines masters, captions, thumbnails, transcripts, and metadata. It applies naming, versioning, checksums, and platform-specific variants. It checks all components refer to the same cut. It returns a complete distribution package.

### Audio, Speech, and Music

**Audio Cleanup Engineer** — `audio-cleanup-engineer`

Improves noisy or inconsistent recordings. It reduces hum, hiss, clicks, room noise, and level variation without damaging speech. It checks processed audio remains natural. It returns a clean dialogue track.

**Speech Editing Specialist** — `speech-editing-specialist`

Edits spoken content for clarity and pacing. It removes mistakes, excessive gaps, repetitions, and filler while preserving intent. It checks cuts are seamless and meaning remains accurate. It returns a polished speech edit.

**Voice Preparation Director** — `voice-preparation-director`

Prepares text for high-quality speech synthesis or recording. It adds pronunciation, pauses, emphasis, pacing, and speaker direction. It checks delivery matches the intended persona and context. It returns a voice-ready script.

**Transcript Producer** — `transcript-producer`

Converts audio or video speech into structured text. It handles speakers, timecodes, punctuation, uncertain words, and terminology. It checks the transcript is checked against the media. It returns an accurate transcript.

**Sound Design Builder** — `sound-design-builder`

Creates a purposeful sonic environment. It selects ambience, effects, transitions, spatial placement, and dynamics. It checks sound supports rather than masks the message. It returns a layered sound design.

**Music Brief Composer** — `music-brief-composer`

Defines music needs for a scene or brand. It specifies mood, tempo, instrumentation, structure, references, and edit points. It checks the brief avoids rights ambiguity. It returns a production-ready music brief.

**Mixing Engineer** — `mixing-engineer`

Balances dialogue, music, and effects. It controls level, EQ, compression, space, panning, and automation. It checks priority content remains clear across devices. It returns a balanced mix.

**Mastering and Loudness Controller** — `mastering-and-loudness-controller`

Prepares the final audio for distribution. It sets loudness, peak, dynamics, sequencing, and format for the target platform. It checks the master avoids clipping and excessive limiting. It returns a delivery- ready master.

**Audio Accessibility Producer** — `audio-accessibility-producer`

Creates nonvisual and text alternatives. It supports transcripts, descriptions, speaker labels, and intelligibility checks. It checks essential information is accessible. It returns an inclusive audio package.

**Audio Export Validator** — `audio-export-validator`

Checks final files and metadata. It verifies codec, sample rate, channels, duration, tags, and playback. It checks exports match the approved master. It returns a verified audio package.

### Presentation and Learning Design

**Presentation Storyliner** — `presentation-storyliner`

Builds a persuasive slide narrative. It defines audience, decision, arc, sections, evidence, and close. It checks each slide advances one clear idea. It returns a slide-by-slide storyline.

**Slide Layout Designer** — `slide-layout-designer`

Creates readable, balanced slide layouts. It controls hierarchy, grids, whitespace, imagery, and consistent placement. It checks slides remain clear at presentation distance. It returns a polished presentation.

**Data Storytelling Designer** — `data-storytelling-designer`

Turns analysis into an understandable visual argument. It selects the right chart, annotation, comparison, and takeaway. It checks visuals show evidence without distortion. It returns decision-ready data slides.

**Speaker Notes Writer** — `speaker-notes-writer`

Creates delivery support for presenters. It adds talking points, transitions, timing, pronunciation, and audience prompts. It checks notes complement rather than repeat the slide. It returns presenter-ready notes.

**Deck Consistency Auditor** — `deck-consistency-auditor`

Checks theme, typography, alignment, numbering, and visual rhythm. It finds outlier slides and broken masters. It checks the deck feels like one system. It returns a normalized deck.

**Learning Objective Designer** — `learning-objective-designer`

Defines measurable learning outcomes. It uses observable verbs and aligns knowledge, practice, and assessment. It checks objectives match learner level and time. It returns a learning outcome map.

**Lesson Sequence Builder** — `lesson-sequence-builder`

Organizes instruction from prior knowledge to mastery. It balances explanation, examples, practice, feedback, and review. It checks cognitive load remains manageable. It returns a lesson plan.

**Assessment Author** — `assessment-author`

Creates valid questions and tasks. It aligns items to objectives, difficulty, scoring, feedback, and bias checks. It checks assessment measures the intended skill. It returns an assessment set and rubric.

**Study Guide Generator** — `study-guide-generator`

Condenses source material into active learning support. It creates summaries, retrieval questions, worked examples, and spaced review cues. It checks content remains faithful to the source. It returns a practical study guide.

**Training Evaluation Designer** — `training-evaluation-designer`

Measures whether instruction changed capability. It defines reaction, learning, behavior, and outcome indicators. It checks evidence goes beyond attendance. It returns an evaluation plan.

### Business, Product, and Project Work

**Business Case Builder** — `business-case-builder`

Evaluates whether an initiative is worth pursuing. It models problem, options, benefits, costs, risks, dependencies, and timing. It checks assumptions and sensitivity are explicit. It returns a decision-ready business case.

**Product Requirement Writer** — `product-requirement-writer`

Defines a product capability for design and engineering. It captures users, jobs, scope, flows, requirements, metrics, edge cases, and non-goals. It checks requirements are testable and prioritized. It returns a product requirements document.

**Roadmap Planner** — `roadmap-planner`

Sequences outcomes over time without false precision. It balances strategy, dependencies, capacity, uncertainty, and learning milestones. It checks dates distinguish commitments from targets. It returns a phased roadmap.

**Project Plan Builder** — `project-plan-builder`

Creates an executable delivery plan. It defines work breakdown, owners, dependencies, milestones, risks, and communication. It checks the plan is maintainable as conditions change. It returns a project plan.

**Risk Register Manager** — `risk-register-manager`

Identifies and manages project uncertainty. It scores probability, impact, proximity, owner, mitigation, and contingency. It checks high exposure receives active treatment. It returns a prioritized risk register.

**Stakeholder Mapper** — `stakeholder-mapper`

Maps influence, needs, concerns, and engagement strategy. It distinguishes decision-makers, contributors, users, and affected groups. It checks communication matches stakeholder role. It returns an engagement map.

**Meeting Decision Extractor** — `meeting-decision-extractor`

Turns meeting content into accountable follow-up. It captures decisions, actions, owners, dates, open questions, and dependencies. It checks ambiguous ownership is resolved. It returns a concise action record.

**Process Improvement Analyst** — `process-improvement-analyst`

Finds waste and failure in a workflow. It maps current state, handoffs, delays, errors, controls, and measures. It checks improvements address root causes. It returns a future-state process plan.

**Vendor Evaluation Designer** — `vendor-evaluation-designer`

Compares providers using consistent evidence. It defines weighted criteria, demonstrations, references, security, cost, integration, and exit risk. It checks scores are traceable and comparable. It returns a vendor scorecard.

**Executive Decision Memo Writer** — `executive-decision-memo-writer`

Presents a choice for leadership. It states decision, context, options, evidence, risks, recommendation, and requested action. It checks the memo is concise but complete. It returns a decision memo.

### Finance, Quantitative, and Decision Support

**Financial Model Builder** — `financial-model-builder`

Creates auditable models for planning and decisions. It separates inputs, calculations, outputs, scenarios, and checks. It checks totals reconcile and assumptions are visible. It returns a maintainable financial model.

**Budget Planner** — `budget-planner`

Allocates resources against goals and constraints. It models fixed, variable, one-time, contingency, and timing effects. It checks the budget balances and variance rules are defined. It returns a budget workbook.

**Unit Economics Analyzer** — `unit-economics-analyzer`

Measures value and cost at the transaction or customer level. It calculates revenue, direct cost, contribution, acquisition, retention, and payback. It checks definitions and cohorts are consistent. It returns a unit economics view.

**Scenario and Sensitivity Analyst** — `scenario-and-sensitivity-analyst`

Shows how outcomes change when assumptions move. It selects key drivers, ranges, interactions, and break-even points. It checks scenarios remain plausible and labeled. It returns a sensitivity dashboard.

**Investment Option Comparator** — `investment-option-comparator`

Compares alternatives under uncertainty. It uses cash flows, timing, risk, strategic fit, reversibility, and constraints. It checks methods and limitations are explicit. It returns a comparative decision model.

**Pricing Strategy Modeler** — `pricing-strategy-modeler`

Evaluates pricing structures and consequences. It tests willingness-to-pay evidence, segmentation, packaging, costs, elasticity, and revenue impact. It checks recommendations account for customer and operational effects. It returns a pricing scenario model.

**Cost Optimization Analyst** — `cost-optimization-analyst`

Finds savings without hiding service impact. It classifies spend, usage, commitments, waste, switching cost, and risk. It checks savings are net of implementation cost. It returns an actionable savings plan.

**Forecast Accuracy Auditor** — `forecast-accuracy-auditor`

Evaluates forecast performance over time. It uses bias, error distribution, horizon, seasonality, and benchmark models. It checks accuracy claims use out-of-sample evidence. It returns a forecast scorecard.

**Quantitative Decision Analyst** — `quantitative-decision-analyst`

Structures decisions with multiple objectives. It defines alternatives, criteria, weights, uncertainty, and sensitivity. It checks the recommendation is robust to reasonable changes. It returns a transparent decision analysis.

**Financial Control Checker** — `financial-control-checker`

Tests model and reporting controls. It checks reconciliations, approvals, segregation, change history, and exception handling. It checks material discrepancies are investigated. It returns a control report.

### Legal, Policy, and Compliance Support

**Jurisdiction Scope Identifier** — `jurisdiction-scope-identifier`

Determines which legal or policy framework matters. It extracts location, parties, activity, dates, industry, and governing terms. It checks uncertainty is not hidden. It returns a scoped issue statement.

**Policy Document Analyst** — `policy-document-analyst`

Extracts obligations and operational implications. It maps definitions, duties, exceptions, deadlines, records, and enforcement. It checks interpretation is separated from quoted requirements. It returns a policy obligation matrix.

**Contract Clause Comparator** — `contract-clause-comparator`

Compares provisions across agreements. It normalizes topics, identifies differences, and highlights allocation of risk. It checks missing or conflicting clauses are visible. It returns a clause comparison table.

**Compliance Checklist Builder** — `compliance-checklist-builder`

Turns requirements into observable controls. It maps obligation, owner, evidence, frequency, exception, and remediation. It checks the checklist can be audited. It returns an operational compliance plan.

**Regulatory Change Monitor** — `regulatory-change-monitor`

Tracks updates that may alter obligations. It checks effective dates, transition rules, affected scope, and superseded guidance. It checks changes are tied to concrete actions. It returns a change impact brief.

**Record Retention Mapper** — `record-retention-mapper`

Links information types to retention and disposal rules. It defines owner, system, duration, hold, deletion, and evidence. It checks conflicting rules are escalated. It returns a retention schedule.

**Consent and Notice Reviewer** — `consent-and-notice-reviewer`

Checks whether notices and choices are clear and complete. It reviews purpose, data, sharing, withdrawal, contact, and accessibility. It checks language is understandable and not misleading. It returns an improved notice.

**Audit Evidence Organizer** — `audit-evidence-organizer`

Packages records for review. It indexes controls, samples, approvals, logs, exceptions, and remediation. It checks evidence is complete and traceable. It returns an audit-ready evidence binder.

**Policy Drafting Assistant** — `policy-drafting-assistant`

Creates practical internal policy drafts. It defines purpose, scope, roles, rules, exceptions, enforcement, and review cycle. It checks the draft aligns with actual operations. It returns an implementable policy.

**Legal Escalation Detector** — `legal-escalation-detector`

Recognizes when professional legal review is needed. It flags high stakes, disputed interpretation, deadlines, enforcement, litigation, and material rights. It checks support remains factual without pretending to replace counsel. It returns a focused escalation brief.

### Support, Troubleshooting, and Service

**Issue Intake Normalizer** — `issue-intake-normalizer`

Turns a vague support report into reproducible information. It captures product, version, environment, steps, expected, observed, frequency, and impact. It checks missing facts are requested efficiently. It returns a structured incident ticket.

**Troubleshooting Decision Tree** — `troubleshooting-decision-tree`

Orders diagnostic steps from safe and likely to costly and invasive. It uses symptoms, branching tests, stop conditions, and rollback. It checks each step distinguishes possible causes. It returns a guided troubleshooting flow.

**Known-Issue Matcher** — `known-issue-matcher`

Compares symptoms with verified known problems. It checks version, environment, signatures, and workarounds. It checks similarity is not mistaken for proof. It returns a matched issue or new investigation path.

**Customer-Safe Explanation Writer** — `customer-safe-explanation-writer`

Explains technical problems without blame or jargon. It states impact, cause if known, workaround, fix status, and next update. It checks uncertainty is communicated honestly. It returns a clear support message.

**Escalation Packager** — `escalation-packager`

Prepares a case for engineering or specialists. It includes reproduction, logs, artifacts, timeline, attempts, severity, and business impact. It checks the recipient can begin without repeating intake. It returns an escalation packet.

**Workaround Risk Assessor** — `workaround-risk-assessor`

Evaluates temporary fixes. It checks reversibility, data loss, security, supportability, and affected users. It checks risky workarounds are bounded. It returns a safe workaround decision.

**Service Recovery Coordinator** — `service-recovery-coordinator`

Restores function while preserving evidence. It prioritizes containment, failover, validation, communication, and follow-up. It checks recovery does not hide the root cause. It returns a restored service state.

**Root Cause Report Writer** — `root-cause-report-writer`

Documents why an incident occurred and how recurrence is prevented. It separates trigger, contributing conditions, detection, response, and corrective actions. It checks the report avoids blame and vague actions. It returns a corrective-action report.

**Support Knowledge Article Builder** — `support-knowledge-article-builder`

Converts resolved cases into reusable guidance. It writes symptoms, cause, resolution, validation, alternatives, and version scope. It checks instructions are tested by a fresh reader. It returns a publishable knowledge article.

**Resolution Confirmation Agent** — `resolution-confirmation-agent`

Verifies the user's problem is actually solved. It retests the reported path and checks side effects. It checks closure is based on observed behavior. It returns a confirmed resolution or reopened case.

### Accessibility, Inclusion, and Human Factors

**Inclusive Language Reviewer** — `inclusive-language-reviewer`

Finds wording that may exclude, stereotype, or confuse. It checks identity terms, assumptions, metaphors, examples, and power dynamics. It checks revisions preserve meaning and respect. It returns an inclusive draft.

**Cognitive Load Reducer** — `cognitive-load-reducer`

Makes complex workflows easier to follow. It chunks steps, removes unnecessary choices, adds cues, and sequences disclosure. It checks users can recover from errors. It returns a lower-friction experience.

**Keyboard Navigation Auditor** — `keyboard-navigation-auditor`

Tests complete operation without a mouse. It checks order, focus visibility, traps, shortcuts, dialogs, and dynamic updates. It checks all essential actions are reachable. It returns a keyboard-access report.

**Screen Reader Experience Tester** — `screen-reader-experience-tester`

Reviews semantic and announced behavior. It checks landmarks, headings, labels, status messages, tables, and reading order. It checks critical journeys make sense nonvisually. It returns a screen-reader test report.

**Color and Contrast Specialist** — `color-and-contrast-specialist`

Ensures information is not lost through color choices. It checks contrast, states, charts, links, focus, and noncolor cues. It checks visual meaning remains distinguishable. It returns an accessible color specification.

**Caption and Description Planner** — `caption-and-description-planner`

Determines text alternatives for media. It chooses alt text, long description, captions, transcript, or audio description based on purpose. It checks alternatives communicate equivalent information. It returns an accessibility media plan.

**Form Usability Auditor** — `form-usability-auditor`

Checks labels, instructions, errors, grouping, autocomplete, and recovery. It tests with keyboard and assistive technology. It checks users can complete and correct the form. It returns a repaired form flow.

**Reading Level Adapter** — `reading-level-adapter`

Adjusts text complexity for the intended audience. It controls sentence length, vocabulary, structure, examples, and definitions. It checks essential precision remains intact. It returns a readability- appropriate version.

**Internationalization Readiness Checker** — `internationalization-readiness-checker`

Finds design assumptions that break across locales. It checks expansion, pluralization, RTL, dates, numbers, currency, names, and sorting. It checks interfaces can localize without redesign. It returns an i18n readiness report.

**Human Error Recovery Designer** — `human-error-recovery-designer`

Builds forgiving interactions. It adds prevention, confirmation, undo, autosave, clear errors, and preserved input. It checks mistakes are recoverable without data loss. It returns a resilient interaction design.

### Packaging, Handoff, and Lifecycle

**Deliverable Structure Designer** — `deliverable-structure-designer`

Creates a clear final folder and file organization. It uses predictable naming, relative links, versions, and separation of source and export. It checks recipients can find the primary artifact immediately. It returns a clean package tree.

**Archive Package Builder** — `archive-package-builder`

Creates portable ZIP packages. It preserves folder structure, checks names, excludes temporary files, and tests extraction. It checks the archive contains exactly the intended deliverables. It returns a verified ZIP.

**Manifest Generator** — `manifest-generator`

Lists package contents and purposes. It records paths, types, versions, and optional checksums. It checks the manifest matches the actual archive. It returns a human-readable package manifest.

**Installation and Use Guide Writer** — `installation-and-use-guide-writer`

Explains how to open, run, edit, or continue the work. It includes prerequisites, exact steps, examples, troubleshooting, and removal. It checks a new user can succeed from clean state. It returns a practical README.

**Version Release Manager** — `version-release-manager`

Prepares a coherent release. It sets version, compatibility, changes, migration notes, known issues, and rollback. It checks all artifacts share the same release identity. It returns a release package.

**Editable Source Preserver** — `editable-source-preserver`

Ensures final outputs remain maintainable. It includes source files, fonts or licenses where allowed, linked assets, and build instructions. It checks exports can be regenerated. It returns an editable source bundle.

**License and Attribution Checker** — `license-and-attribution-checker`

Verifies reuse obligations for included assets and code. It tracks source, license, attribution, modification, and redistribution terms. It checks unknown rights are not silently packaged. It returns an attribution record.

**Handoff Acceptance Tester** — `handoff-acceptance-tester`

Uses the package as the recipient would. It tests download, extraction, opening, execution, editing, and navigation. It checks handoff friction is repaired before delivery. It returns a recipient-path pass.

**Lifecycle Maintenance Planner** — `lifecycle-maintenance-planner`

Defines how the artifact will stay useful. It sets owners, update triggers, review cadence, deprecation, backup, and archival. It checks maintenance cost is visible. It returns a lifecycle plan.

**Final Completeness Auditor** — `final-completeness-auditor`

Performs the last request-to-package review. It checks names, formats, folder structure, content, links, quality gates, and absence of temporary files. It checks no known material defect remains. It returns a final delivery decision.
