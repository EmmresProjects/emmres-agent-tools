# Offline Research, Writing & Translation Skill Tool (RWT)

**One unified, fully offline Skill Tool for the complete research-to-publication workflow:**
local corpus management, retrieval, citation integrity, claims verification, systematic-review
methodology, quantitative accuracy checking, plain-language and developmental editing,
terminology and translation-memory management, locale engineering, accessibility checking,
evidence-bound writing scaffolds, security/privacy scanning and rights-aware export.

It is the operationalization of the 288-skill capability catalog in the accompanying source
PDF (*Emmres Full Offline Research Writing Translation Skill Catalog*). Every catalog skill is
implemented as a real, reachable command — none are stubs. No network access is required or
used at runtime. No credentials are bundled. Nothing phones home.

- **Skill Tool name:** `offline-research-writing-translation`
- **Folder:** `offline-research-writing-translation/` (flat, 18 `tools-*.py` + 1 `knowledge-readme.md` + 9 `asset-*` files)
- **Entry point:** `python3 tools-rwt.py <command>` (call it `rwt` below)
- **Runtime:** Python 3.9+ standard library only (developed and tested on Python 3.13.16)
- **Exit codes:** `0` ok · `1` unexpected error · `2` validation/usage · `3` unavailable (optional local model not configured) · `4` blocked (human gate / approval required)

> This tool organizes and checks your work. It does not replace domain experts,
> bilingual reviewers, editors, lawyers or ethics boards — and it never fabricates
> sources, quotations, page numbers, statistics, translations or approvals.

---

## 1. Purpose

Disconnected knowledge workers — field researchers, fact-checkers, translators, legal and
policy analysts, systematic reviewers, content operations teams — need the full pipeline from
"a pile of documents" to "a defensible, accessible, rights-cleared deliverable" **without
network access**. RWT provides that pipeline as a single local tool with an audit trail.

Covered: corpus ingestion and cleaning · deduplication and referential integrity · snapshots ·
TF-IDF retrieval with boolean operators · extractive answers with abstention · citation
extraction/normalization/checking (9 styles) · claims extraction, linking, coverage,
contradiction scanning (abstains when evidence is weak) · number/unit/date rechecking ·
meta-analysis pooling (fixed effect: MD/SMD/OR/RR) · inter-rater reliability (Fleiss κ) ·
readability and calibration scanning · terminology databases and translation memory (TMX
import/export) · MQM-style QA scoring gate · language ID · script/RTL/CJK handling ·
font cmap inspection · register/inclusivity review · entity/relation/event extraction ·
SRT/VTT handling · alt-text/figure/screenshot evidence practices · provenance and freshness
registries · systematic-review workflow (queue, gates, decisions with hash-chained audit,
adjudication with Cohen-style κ, PRISMA flow, risk-of-bias, descriptive synthesis) ·
redaction/classification/injection/media scanning · rights, embargo, retention ·
controlled export gate · accessible self-contained HTML reports · archives with manifests ·
reopen verification · offline benchmarking · local-model adapters (OCR/STT/MT/LLM/embed/rerank/VLM)
that are **configurable and never bundled**.

## 2. What it is NOT

- Not a hosted service; not a Copilot skill; not a chatbot.
- Not legal, medical, financial or ethics advice. Outputs labelled "not legal advice" must be
  reviewed by the qualified reviewer named in your process.
- Not a substitute for translation by a qualified human in high-risk contexts (legal, medical,
  safety). It is a bilingual-review *aid* with hard gates.
- It does not perform live web/DOI lookups. `citation.resolve` resolves only against the local
  workspace/registry; unresolved identifiers are reported as such, never guessed.
- Full PDF text extraction, OCR, speech-to-text, neural MT and embeddings require your own
  local engines via the adapter system (§12). Without them those specific capabilities report
  "adapter missing" and exit 3. Everything else works regardless.

## 3. Setup

Requirements: Python 3.9+ (`python3 --version`). No pip installs. No native dependencies.

```bash
# Optional: make it callable as `rwt`
python3 tools-rwt.py version
alias rwt="python3 /path/to/offline-research-writing-translation/tools-rwt.py"

# Create a workspace for a project
python3 tools-rwt.py init my-project --name "Hydrogen review" --preset review
```

Presets (`asset-config-presets.json`): `minimal` (default), `review`, `translation`, `legal`.
Presets only seed defaults (roles, quotation limits, retention, redaction profile); edit per
project. Hello-world smoke test:

```bash
python3 tools-rwt.py selftest clean          # builds a temp workspace, runs the pipeline, asserts, cleans up
python3 tools-rwt.py selftest failure-recovery
```

## 4. Quick start (exact commands, all tested)

```bash
cd /path/to/skills
python3 tools-rwt.py version                      # tool + interpreter
python3 tools-rwt.py capabilities --format text   # the full capability list
python3 tools-rwt.py capabilities --search term   # filter
python3 tools-rwt.py doctor --workspace my-project

# Ingest and retrieve
python3 tools-rwt.py corpus ingest --workspace my-project --file paper.txt --title "Santos 2020" --language en
python3 tools-rwt.py search query --workspace my-project --query "electrolysis emissions" --limit 5
python3 tools-rwt.py search answer --workspace my-project --question "Do curtailment pilots report 68%?" 

# Verify a number and a claim
python3 tools-rwt.py numbers recheck --op percent-change --old 200 --new 250 --reported 25%
python3 tools-rwt.py claims add --workspace my-project --text "Electrolysis hydrogen cuts lifecycle emissions ~68% in temperate climates."
python3 tools-rwt.py claims verify --workspace my-project --claim-id cla_xxxxx --max-hits 5

# Run a catalog skill by id (see capabilities for ids)
python3 tools-rwt.py skill 9.5 --op percent-change --old 200 --new 250 --reported 25%
python3 tools-rwt.py skill 6.1 --workspace my-project --query "wind coupling" --limit 5

# Gates
python3 tools-rwt.py workflow gate --workspace my-project --subject publication --required-roles approver
#   -> gate: "blocked" and exit code 4 until an approver decision is recorded
python3 tools-rwt.py workflow decisions-add --workspace my-project --subject publication --role approver --decision approve --actor "Dr. Reyes"

# Export (blocked without approval; audited when approved)
python3 tools-rwt.py security export-gate --workspace my-project --file final.pdf --approved-by "Dr. Reyes"
```

## 5. Workspace layout (created by `rwt init`)

```
my-project/
└── .rwt/
    ├── workspace.json      # version, name, preset, roles, export gate, models registry
    ├── stores/*.jsonl      # documents, references, claims, evidence, reviews, decisions,
    │                       # terms, segments, registry, disclosures, feedback, corrections,
    │                       # gaps, benchmarks, lineage, queue, changes, snapshots
    ├── files/              # content-addressed copies of ingested files (sha256)
    ├── snapshots/          # point-in-time store snapshots (restore requires --yes)
    ├── exports/            # controlled exports (via security export-gate only)
    └── audit.jsonl         # hash-chained, tamper-evident audit log (rwt audit verify)
```

All paths are relative to the workspace you pass with `--workspace` (default `.`). The tool
never writes outside `--workspace` except `--output` paths you pass explicitly, and never
in-place (redaction always writes a copy).

## 6. Configuration (`.rwt/workspace.json`)

Key fields (edit directly, or via engine commands):

```json
{
  "version": "1.0.0", "name": "Hydrogen review", "preset": "review",
  "languages": ["en", "es"],
  "quotation_limits": {"default_words": 90, "licensed_words": 250},
  "export_gate": {"require_approval": true, "embargo_until": null},
  "retention_days": 730, "redaction_profile": "standard",
  "roles": ["translator", "bilingual-reviewer", "domain-expert", "proofreader", "qa", "approver"],
  "models": {}
}
```

- `export_gate.require_approval`: when true, `security export-gate` refuses to copy a file
  for external release without `--approved-by NAME`, and logs the blocked attempt.
- `embargo_until`: ISO date; `workflow gate` and `security embargo` enforce it.
- `retention_days`: consumed by `security retention` (dry-run by default; deletion needs `--apply --yes`).
- `models`: local model adapters (§12). Empty by default — **nothing is silently used**.

## 7. Output format & exit codes

Every engine supports `--format json|text` (global, placed before the engine: `rwt --format text search query …`). JSON envelope for all commands:

```json
{"ok": true, "command": "rwt search", "result": {…}, "warnings": [], "duration_ms": 12}
```

Errors: `{"ok": false, "command": …, "error": {"type": "ValidationError|UnavailableError|BlockedError|…", "message": …, "details": {…}}}`.

| Code | Meaning | Examples |
|---|---|---|
| 0 | success | normal completion; `gate: "open"`; `verdict: "pass"` |
| 1 | unexpected error | bug; the envelope carries a traceback tail |
| 2 | validation/usage | missing file, unknown skill id, bad enum, argparse usage errors |
| 3 | unavailable | `models.*` with no configured local command; benchmark suites that need a local LLM/VLM |
| 4 | blocked | human approval required (`workflow gate`, `security export-gate`); approval records via `workflow decisions-add` |

Change detection: engines never mutate inputs in place unless the command is explicitly
destructive **and** confirmed with `--yes` (restore snapshot, retention delete, corpus update
delete). Redaction always emits a new copy with `--output`.

## 8. Complete capability catalog (all 288)

Ids are stable; `rwt skill <id> [args]` dispatches to the mapped engine and action.
`Automation`: `deterministic` = fully implemented offline, no human needed for the operation;
`assistive` = deterministic checks + explicit human-review routing; `model-adapter` = needs a
configured local model via §12 (otherwise exit 3, never a fake answer).

<!-- CAPABILITY-LIST-START -->
### 1. Offline Scholarly and Academic Collections

| id | Skill | Command | Automation |
|---|---|---|---|
| `1.1` | Offline Scholarly Database | `collections.records` | deterministic |
| `1.2` | Offline Reference Corpus | `collections.records` | deterministic |
| `1.3` | Institutional Repository Harvester | `collections.import` | deterministic |
| `1.4` | Preprint and Version Tracker | `collections.links` | deterministic |
| `1.5` | Thesis and Dissertation Indexer | `collections.records` | deterministic |
| `1.6` | Conference Proceedings Curator | `collections.records` | deterministic |
| `1.7` | Open-Access Corpus Builder | `collections.import` | deterministic |
| `1.8` | Retraction and Correction Registry | `collections.retraction` | deterministic |
| `1.9` | Peer-Review Status Classifier | `collections.classify-review` | assistive |
| `1.10` | Research Dataset Catalog | `collections.records` | deterministic |
| `1.11` | Systematic Review Corpus Manager | `collections.records` | deterministic |
| `1.12` | Scholarly Corpus Coverage Auditor | `collections.coverage` | deterministic |

### 2. Offline News, Legal, Policy, and Standards Libraries

| id | Skill | Command | Automation |
|---|---|---|---|
| `2.1` | Offline News Archive | `collections.records` | deterministic |
| `2.2` | News Event Timeline Builder | `corpus.timeline` | deterministic |
| `2.3` | News Source Diversity Auditor | `corpus.diversity` | deterministic |
| `2.4` | Offline Legal and Policy Corpus | `collections.records` | deterministic |
| `2.5` | Jurisdiction and Authority Classifier | `collections.classify-authority` | assistive |
| `2.6` | Legal Version and Amendment Tracker | `collections.links` | deterministic |
| `2.7` | Case Law Citation Network | `citation.graph` | deterministic |
| `2.8` | Offline Standards Library | `collections.records` | deterministic |
| `2.9` | Standards Requirement Extractor | `text.requirements` | deterministic |
| `2.10` | Policy Comparison Matrix Builder | `text.matrix` | assistive |
| `2.11` | Official Gazette Importer | `collections.import` | deterministic |
| `2.12` | Legal and Standards Rights Guard | `security.rights` | deterministic |

### 3. Citation Indexes, Bibliographies, and Identifier Resolution

| id | Skill | Command | Automation |
|---|---|---|---|
| `3.1` | Offline Citation Index | `citation.graph` | deterministic |
| `3.2` | Citation Extraction Tool | `citation.extract` | deterministic |
| `3.3` | DOI and Bibliography Resolver Cache | `citation.resolve` | deterministic |
| `3.4` | Bibliographic Metadata Normalizer | `citation.normalize` | deterministic |
| `3.5` | Reference Style Formatter | `citation.format` | deterministic |
| `3.6` | Citation Context Extractor | `citation.context` | assistive |
| `3.7` | Broken Reference Detector | `citation.check` | deterministic |
| `3.8` | Author Identity Disambiguator | `citation.disambiguate` | assistive |
| `3.9` | Venue Identity Resolver | `citation.venue` | deterministic |
| `3.10` | Citation Graph Analyzer | `citation.analyze` | deterministic |
| `3.11` | Self-Citation and Citation-Ring Detector | `citation.selfcite` | deterministic |
| `3.12` | Bibliography Completeness Checker | `citation.completeness` | deterministic |

### 4. Corpus Ingestion, Cleaning, and Installation

| id | Skill | Command | Automation |
|---|---|---|---|
| `4.1` | Clean Offline Corpus Installer | `corpus.install` | deterministic |
| `4.2` | Corpus Package Builder | `deliver.archive` | deterministic |
| `4.3` | Corpus License and Redistribution Manager | `collections.records` | deterministic |
| `4.4` | Air-Gapped Corpus Import Gate | `security.import-gate` | assistive |
| `4.5` | Format Extraction Router | `corpus.route` | deterministic |
| `4.6` | Document Structure Preserver | `corpus.ingest` | deterministic |
| `4.7` | Source Deduplication Tool | `corpus.dedup` | deterministic |
| `4.8` | Boilerplate and Navigation Remover | `corpus.deboilerplate` | deterministic |
| `4.9` | Encoding and Unicode Repairer | `corpus.encoding` | deterministic |
| `4.10` | Corpus Integrity Auditor | `corpus.integrity` | deterministic |
| `4.11` | Incremental Corpus Updater | `corpus.update` | deterministic |
| `4.12` | Corpus Rollback Manager | `corpus.snapshot` | deterministic |

### 5. OCR, Speech, and Document Recovery

| id | Skill | Command | Automation |
|---|---|---|---|
| `5.1` | Offline OCR Model | `models.ocr` | model-adapter |
| `5.2` | Scholarly PDF OCR Workflow | `models.ocr` | model-adapter |
| `5.3` | Historical Document OCR | `models.ocr` | model-adapter |
| `5.4` | Handwriting Recognition Workflow | `models.ocr` | model-adapter |
| `5.5` | Table Recognition Engine | `models.ocr` | model-adapter |
| `5.6` | Formula and Math Recognition | `models.ocr` | model-adapter |
| `5.7` | Offline Speech-to-Text Model | `models.stt` | model-adapter |
| `5.8` | Speaker Diarization Engine | `models.stt` | model-adapter |
| `5.9` | Interview Transcript Cleaner | `media.transcript-clean` | deterministic |
| `5.10` | Audio Citation Timecode Builder | `media.timecodes` | deterministic |
| `5.11` | OCR Quality Evaluator | `benchmark.cer` | deterministic |
| `5.12` | Human Transcription Review Queue | `workflow.queue` | deterministic |

### 6. Local Search, Embeddings, Reranking, and Retrieval

| id | Skill | Command | Automation |
|---|---|---|---|
| `6.1` | Local Full-Text Search Engine | `search.query` | deterministic |
| `6.2` | Local Document Embedding Model | `models.embed` | model-adapter |
| `6.3` | Local Reranking Model | `models.rerank` | model-adapter |
| `6.4` | Local Question-Answering Model | `search.answer` | assistive |
| `6.5` | Hybrid Retrieval Orchestrator | `search.hybrid` | deterministic |
| `6.6` | Research Query Decomposer | `search.decompose` | assistive |
| `6.7` | Boolean Search Builder | `search.build` | deterministic |
| `6.8` | Multilingual Search Expander | `search.expand` | assistive |
| `6.9` | Evidence Passage Retriever | `search.passages` | deterministic |
| `6.10` | Search Result Diversity Controller | `search.diversity` | deterministic |
| `6.11` | Retrieval Recall Benchmark | `benchmark.recall` | deterministic |
| `6.12` | Search Explainability Reporter | `search.explain` | deterministic |

### 7. Entity, Claim, Event, and Relationship Extraction

| id | Skill | Command | Automation |
|---|---|---|---|
| `7.1` | Named-Entity Recognition Model | `extract.entities` | assistive |
| `7.2` | Entity Linking Resolver | `extract.link-entities` | assistive |
| `7.3` | Relation Extraction Model | `extract.relations` | assistive |
| `7.4` | Claim Extraction Tool | `claims.extract` | assistive |
| `7.5` | Evidence-Claim Linker | `claims.link` | assistive |
| `7.6` | Event Extraction Model | `extract.events` | assistive |
| `7.7` | Temporal Expression Normalizer | `time.normalize` | assistive |
| `7.8` | Quantity and Unit Extractor | `numbers.extract` | deterministic |
| `7.9` | Methodology Extractor | `extract.methodology` | assistive |
| `7.10` | Funding and Conflict Extractor | `extract.funding` | assistive |
| `7.11` | Definition and Term Extractor | `extract.definitions` | assistive |
| `7.12` | Entity and Claim Review Console | `workflow.queue` | deterministic |

### 8. Source Evaluation, Provenance, and Freshness

| id | Skill | Command | Automation |
|---|---|---|---|
| `8.1` | Source Provenance Manifest | `provenance.manifest` | deterministic |
| `8.2` | Date and Version Freshness Controls | `freshness.check` | deterministic |
| `8.3` | Source Authority Assessor | `collections.classify-authority` | assistive |
| `8.4` | Primary Source Prioritizer | `collections.prioritize-primary` | assistive |
| `8.5` | Source Independence Analyzer | `corpus.independence` | assistive |
| `8.6` | Publication Version Comparator | `text.compare-versions` | assistive |
| `8.7` | Staleness Risk Scorer | `freshness.score` | deterministic |
| `8.8` | Unavailable Source Disclosure | `collections.disclose` | deterministic |
| `8.9` | Evidence Chain Validator | `provenance.verify-chain` | deterministic |
| `8.10` | Provenance Gap Detector | `provenance.gaps` | deterministic |
| `8.11` | Corpus Snapshot Identifier | `provenance.snapshot-id` | deterministic |
| `8.12` | Freshness Review Scheduler | `freshness.schedule` | deterministic |

### 9. Fact Checking, Verification, and Quantitative Accuracy

| id | Skill | Command | Automation |
|---|---|---|---|
| `9.1` | Fact-Checking Dataset Manager | `collections.records` | deterministic |
| `9.2` | Claim Verification Agent | `claims.verify` | assistive |
| `9.3` | Cross-Source Corroboration Checker | `claims.corroborate` | assistive |
| `9.4` | Quotation Verifier | `claims.quote-verify` | deterministic |
| `9.5` | Number and Unit Rechecker | `numbers.recheck` | deterministic |
| `9.6` | Statistical Claim Auditor | `numbers.audit-stat` | assistive |
| `9.7` | Chart-to-Text Consistency Checker | `numbers.chart-text` | assistive |
| `9.8` | Chronology Consistency Checker | `numbers.chronology` | assistive |
| `9.9` | Identity and Attribution Checker | `claims.identity` | assistive |
| `9.10` | Contradiction Mapper | `claims.contradictions` | assistive |
| `9.11` | Uncertainty and Abstention Controller | `text.calibration` | assistive |
| `9.12` | Fact-Check Regression Suite | `benchmark.factcheck` | deterministic |

### 10. Literature Reviews and Evidence Synthesis

| id | Skill | Command | Automation |
|---|---|---|---|
| `10.1` | Literature Review Planner | `review.plan` | assistive |
| `10.2` | Systematic Search Strategy Builder | `search.build` | deterministic |
| `10.3` | Screening and Eligibility Manager | `workflow.queue` | deterministic |
| `10.4` | PRISMA Flow Data Builder | `review.prisma` | deterministic |
| `10.5` | Evidence Extraction Form | `collections.records` | deterministic |
| `10.6` | Risk-of-Bias Workflow | `review.riskofbias` | assistive |
| `10.7` | Narrative Synthesis Agent | `review.synthesis` | assistive |
| `10.8` | Meta-Analysis Data Preparer | `numbers.meta-analysis` | deterministic |
| `10.9` | Evidence Table Builder | `review.evidence-table` | deterministic |
| `10.10` | Research Gap Mapper | `collections.coverage` | deterministic |
| `10.11` | Living Review Update Manager | `review.living-update` | assistive |
| `10.12` | Synthesis Robustness Reviewer | `review.robustness` | assistive |

### 11. Research Design, Study Analysis, and Data Interpretation

| id | Skill | Command | Automation |
|---|---|---|---|
| `11.1` | Research Question Refiner | `design.question` | assistive |
| `11.2` | Study Design Advisor | `design.study-advisor` | assistive |
| `11.3` | Sampling Plan Reviewer | `design.checklist` | assistive |
| `11.4` | Measurement Validity Reviewer | `design.checklist` | assistive |
| `11.5` | Qualitative Coding Assistant | `design.coding` | assistive |
| `11.6` | Survey Instrument Reviewer | `design.survey` | assistive |
| `11.7` | Experimental Protocol Auditor | `design.checklist` | assistive |
| `11.8` | Causal Inference Assumption Reviewer | `design.checklist` | assistive |
| `11.9` | Data Visualization Reviewer | `design.viz` | assistive |
| `11.10` | Reproducible Analysis Packager | `deliver.archive` | deterministic |
| `11.11` | Research Ethics Checklist | `design.checklist` | assistive |
| `11.12` | Limitations and Transferability Writer | `design.limitations` | assistive |

### 12. Academic, Technical, Legal, and Policy Writing

| id | Skill | Command | Automation |
|---|---|---|---|
| `12.1` | Research Writing Architect | `writing.outline` | assistive |
| `12.2` | Academic Manuscript Drafter | `writing.draft` | assistive |
| `12.3` | Technical Report Writer | `writing.draft` | assistive |
| `12.4` | Policy Brief Writer | `writing.draft` | assistive |
| `12.5` | Legal Research Memorandum Writer | `writing.draft` | assistive |
| `12.6` | Standards Compliance Writer | `writing.draft` | assistive |
| `12.7` | Executive Summary Writer | `writing.draft` | assistive |
| `12.8` | Abstract and Synopsis Writer | `writing.draft` | assistive |
| `12.9` | Methods Section Writer | `writing.methods` | deterministic |
| `12.10` | Findings and Results Writer | `writing.draft` | assistive |
| `12.11` | Discussion and Implications Writer | `writing.draft` | assistive |
| `12.12` | Conclusion Boundary Checker | `text.conclusion-check` | assistive |

### 13. Editing, Revision, Style, and Readability

| id | Skill | Command | Automation |
|---|---|---|---|
| `13.1` | Developmental Editor | `text.dev-edit` | assistive |
| `13.2` | Substantive Evidence Editor | `claims.coverage` | assistive |
| `13.3` | Line Editor | `text.line-edit` | assistive |
| `13.4` | Copy Editor | `text.copy-edit` | assistive |
| `13.5` | Technical Consistency Checker | `text.tech-consistency` | deterministic |
| `13.6` | Plain-Language Rewriter | `text.plain-language` | assistive |
| `13.7` | Audience Adaptation Editor | `text.audience` | assistive |
| `13.8` | Tone and Register Controller | `text.tone` | assistive |
| `13.9` | Redundancy and Repetition Detector | `text.redundancy` | deterministic |
| `13.10` | Logical Flow Auditor | `text.logic-flow` | assistive |
| `13.11` | Tracked Revision Explainer | `text.tracked-revision` | assistive |
| `13.12` | Final Proofreading Pass | `text.final-proof` | deterministic |

### 14. Copyright, Quotation, Plagiarism, and Ethical Use

| id | Skill | Command | Automation |
|---|---|---|---|
| `14.1` | Copyright and Quotation Controls | `security.rights` | deterministic |
| `14.2` | Plagiarism-Checking Corpus | `collections.records` | deterministic |
| `14.3` | Similarity Detection Engine | `similarity.scan` | deterministic |
| `14.4` | Patchwriting Detector | `similarity.patchwrite` | assistive |
| `14.5` | Attribution Completeness Checker | `attribution.check` | assistive |
| `14.6` | Fair-Use Review Packet | `security.fair-use` | assistive |
| `14.7` | Licensed Content Usage Meter | `security.usage-meter` | deterministic |
| `14.8` | Public-Domain Status Recorder | `security.public-domain` | assistive |
| `14.9` | Open-License Compliance Checker | `security.open-license` | deterministic |
| `14.10` | Quotation Length and Context Guard | `security.quote-guard` | deterministic |
| `14.11` | Authorship and Contribution Recorder | `collections.records` | deterministic |
| `14.12` | Research Integrity Escalation | `workflow.queue` | deterministic |

### 15. Terminology, Translation Memory, and Linguistic Assets

| id | Skill | Command | Automation |
|---|---|---|---|
| `15.1` | Terminology Database | `terms.store` | deterministic |
| `15.2` | Translation Memory | `tm.store` | deterministic |
| `15.3` | Term Extraction Engine | `terms.extract` | assistive |
| `15.4` | Bilingual Corpus Aligner | `tm.align` | assistive |
| `15.5` | Terminology Consistency Checker | `terms.consistency` | deterministic |
| `15.6` | Translation Memory Cleaner | `tm.clean` | deterministic |
| `15.7` | Term Inflection Generator | `terms.inflect` | assistive |
| `15.8` | Acronym and Abbreviation Manager | `terms.acronyms` | deterministic |
| `15.9` | Named-Entity Translation Policy | `terms.entity-policy` | deterministic |
| `15.10` | Definition Consistency Auditor | `terms.definition-audit` | assistive |
| `15.11` | Subject-Matter Glossary Builder | `terms.glossary-build` | assistive |
| `15.12` | Language Asset Version Registry | `collections.records` | deterministic |

### 16. Offline Machine Translation and Multilingual Models

| id | Skill | Command | Automation |
|---|---|---|---|
| `16.1` | Offline Translation Models | `models.mt` | model-adapter |
| `16.2` | Offline Multilingual Language Models | `models.llm` | model-adapter |
| `16.3` | Language Identification Model | `langid.detect` | deterministic |
| `16.4` | Translation Model Router | `models.route` | deterministic |
| `16.5` | Document-Level Translation Engine | `translate.document` | assistive |
| `16.6` | Low-Resource Language Workflow | `translate.low-resource` | assistive |
| `16.7` | Domain Adaptation Packager | `translate.domain-pack` | deterministic |
| `16.8` | Pivot Translation Guard | `translate.pivot-guard` | assistive |
| `16.9` | Transliteration Engine | `translate.transliterate` | assistive |
| `16.10` | Multilingual Summarization Model | `translate.summarize` | model-adapter |
| `16.11` | Offline Dictionary and Morphology Service | `translate.dictionary` | deterministic |
| `16.12` | Translation Model Drift Monitor | `drift.monitor` | deterministic |

### 17. Translation Production and Human Review

| id | Skill | Command | Automation |
|---|---|---|---|
| `17.1` | Translation Project Planner | `translate.plan` | assistive |
| `17.2` | Pre-Translation Source Editor | `translate.source-edit` | deterministic |
| `17.3` | Human-Review Workflow | `workflow.queue` | deterministic |
| `17.4` | Bilingual Accuracy Reviewer | `translate.bilingual-review` | assistive |
| `17.5` | Target-Language Proofreader | `translate.proofread` | assistive |
| `17.6` | Subject-Matter Translation Reviewer | `translate.domain-review` | assistive |
| `17.7` | Translation Query Manager | `collections.records` | deterministic |
| `17.8` | Translation Error Taxonomy | `qa.taxonomy` | deterministic |
| `17.9` | Severity-Weighted Language Score | `qa.score` | deterministic |
| `17.10` | Back-Translation Risk Check | `translate.backtranslation` | assistive |
| `17.11` | Independent Review Adjudicator | `workflow.adjudicate` | assistive |
| `17.12` | Post-Delivery Language Feedback Loop | `translate.feedback-loop` | deterministic |

### 18. Language-Specific Accuracy and Localization QA

| id | Skill | Command | Automation |
|---|---|---|---|
| `18.1` | Accuracy Benchmarks by Language | `benchmark.sets` | deterministic |
| `18.2` | Locale and Language Variant Controller | `locale.variant` | assistive |
| `18.3` | Filipino and English Research Reviewer | `locale.filipino` | assistive |
| `18.4` | Code-Switching Quality Reviewer | `locale.codeswitch` | assistive |
| `18.5` | Right-to-Left Document Tester | `locale.rtl` | deterministic |
| `18.6` | CJK Research Typography Reviewer | `locale.cjk` | deterministic |
| `18.7` | Complex-Script Font Coverage Tester | `locale.font-coverage` | deterministic |
| `18.8` | Multilingual Citation Style Tester | `citation.format` | deterministic |
| `18.9` | Locale-Specific Number and Date Reviewer | `locale.number-date` | deterministic |
| `18.10` | Language Register and Politeness Reviewer | `locale.register` | assistive |
| `18.11` | Dialect and Inclusive-Language Reviewer | `locale.inclusive` | assistive |
| `18.12` | Language Acceptance Test Runner | `benchmark.acceptance` | deterministic |

### 19. Tables, Figures, Images, and Multimodal Research

| id | Skill | Command | Automation |
|---|---|---|---|
| `19.1` | Figure and Table Extractor | `media.extract-figures` | deterministic |
| `19.2` | Chart Data Recovery Tool | `media.chart-recovery` | assistive |
| `19.3` | Image Provenance Tracker | `collections.records` | deterministic |
| `19.4` | Diagram Interpretation Agent | `models.vlm` | model-adapter |
| `19.5` | Map Evidence Extractor | `media.map-evidence` | assistive |
| `19.6` | Visual Claim Verifier | `media.visual-claim` | assistive |
| `19.7` | Screenshot Evidence Packager | `media.screenshot-pack` | deterministic |
| `19.8` | Accessible Figure Description Writer | `media.alt-text` | assistive |
| `19.9` | Multimodal Citation Builder | `media.multimodal-citation` | assistive |
| `19.10` | Video Research Segmenter | `media.video-segment` | assistive |
| `19.11` | Audio Evidence Reviewer | `media.audio-review` | assistive |
| `19.12` | Multimodal Hallucination Test | `benchmark.hallucination` | model-adapter |

### 20. Research Project, Review, and Approval Workflows

| id | Skill | Command | Automation |
|---|---|---|---|
| `20.1` | Research Project Workspace | `init` | deterministic |
| `20.2` | Human Research Approval Gate | `workflow.gate` | deterministic |
| `20.3` | Source Review Queue | `workflow.queue` | deterministic |
| `20.4` | Claim Review Queue | `workflow.queue` | deterministic |
| `20.5` | Draft Review Workflow | `workflow.queue` | deterministic |
| `20.6` | Conflict-of-Interest Disclosure Workflow | `collections.records` | deterministic |
| `20.7` | Decision and Rationale Log | `workflow.decisions` | deterministic |
| `20.8` | Research Change Control | `workflow.change-control` | deterministic |
| `20.9` | Parallel Reviewer Reconciliation | `workflow.adjudicate` | deterministic |
| `20.10` | Publication Readiness Gate | `workflow.gate` | deterministic |
| `20.11` | Embargo and Confidentiality Controller | `security.embargo` | deterministic |
| `20.12` | Research Handoff Packager | `deliver.handoff` | deterministic |

### 21. Privacy, Security, and Sensitive Research

| id | Skill | Command | Automation |
|---|---|---|---|
| `21.1` | Sensitive Corpus Classifier | `security.classify` | deterministic |
| `21.2` | Research Data Access Controller | `security.access` | deterministic |
| `21.3` | Secret and Identifier Redactor | `security.redact` | deterministic |
| `21.4` | De-Identification Risk Reviewer | `security.deid` | assistive |
| `21.5` | Secure Research Enclave | `security.enclave` | assistive |
| `21.6` | Prompt Injection Defense for Research | `security.injection-scan` | deterministic |
| `21.7` | Malicious Document Scanner | `security.scan-media` | assistive |
| `21.8` | Sensitive Quotation Reviewer | `security.sensitive-quote` | assistive |
| `21.9` | Data Retention and Deletion Manager | `security.retention` | deterministic |
| `21.10` | Controlled Research Export Gate | `security.export-gate` | deterministic |
| `21.11` | Tamper-Evident Research Audit | `audit.verify` | deterministic |
| `21.12` | Incident Response for Research Data | `security.incident` | assistive |

### 22. Evaluation, Benchmarks, and Clean-State Validation

| id | Skill | Command | Automation |
|---|---|---|---|
| `22.1` | Offline Research Agent Benchmark | `benchmark.agent` | model-adapter |
| `22.2` | Writing Quality Benchmark | `benchmark.writing` | deterministic |
| `22.3` | Translation Quality Benchmark | `benchmark.translation` | assistive |
| `22.4` | Citation Accuracy Benchmark | `benchmark.citation` | deterministic |
| `22.5` | Fact-Checking Benchmark | `benchmark.factcheck` | deterministic |
| `22.6` | OCR and Speech Accuracy Benchmark | `benchmark.cer` | deterministic |
| `22.7` | Retrieval Benchmark Suite | `benchmark.recall` | deterministic |
| `22.8` | Clean Offline Research Tests | `selftest.clean` | deterministic |
| `22.9` | Failure-Recovery Research Tests | `selftest.failure-recovery` | deterministic |
| `22.10` | Corpus Leakage Test | `security.leakage-test` | deterministic |
| `22.11` | Human Acceptance Evaluation | `collections.records` | deterministic |
| `22.12` | Regression Release Gate | `workflow.regression-gate` | deterministic |

### 23. Offline Infrastructure, Registries, and Reproducibility

| id | Skill | Command | Automation |
|---|---|---|---|
| `23.1` | Research Model Registry | `collections.records` | deterministic |
| `23.2` | Corpus Registry | `collections.records` | deterministic |
| `23.3` | Prompt and Method Registry | `collections.records` | deterministic |
| `23.4` | Tool Schema Registry | `collections.records` | deterministic |
| `23.5` | Research Workflow Engine | `workflow.runner` | deterministic |
| `23.6` | Research Job Scheduler | `workflow.schedule` | deterministic |
| `23.7` | Durable Research State | `workflow.state` | deterministic |
| `23.8` | Research Provenance Database | `provenance.lineage` | deterministic |
| `23.9` | Reproducible Run Manifest | `provenance.run-manifest` | deterministic |
| `23.10` | Offline Dependency Mirror | `doctor.mirror` | assistive |
| `23.11` | Backup and Restore Verifier | `workflow.backup-verify` | deterministic |
| `23.12` | Research Environment Health Check | `doctor` | deterministic |

### 24. Deliverables, Publishing, Accessibility, and Archiving

| id | Skill | Command | Automation |
|---|---|---|---|
| `24.1` | Research Report Packager | `deliver.report` | deterministic |
| `24.2` | Multilingual Publication Packager | `deliver.multilingual` | deterministic |
| `24.3` | Accessible Research Document Builder | `deliver.accessible-check` | deterministic |
| `24.4` | Citation and Link Integrity Checker | `deliver.citation-integrity` | deterministic |
| `24.5` | PDF Publication Preflight | `deliver.pdf-preflight` | assistive |
| `24.6` | Dataset and Code Release Packager | `deliver.archive` | deterministic |
| `24.7` | Research Archive Builder | `deliver.archive` | deterministic |
| `24.8` | Long-Term Format Migration Planner | `deliver.migration-plan` | assistive |
| `24.9` | Versioned Publication Manager | `collections.records` | deterministic |
| `24.10` | Translation Publication Consistency Checker | `deliver.multilingual-consistency` | deterministic |
| `24.11` | Reader Feedback and Correction Intake | `collections.records` | deterministic |
| `24.12` | Final Artifact Reopen Test | `deliver.reopen` | deterministic |
<!-- CAPABILITY-LIST-END -->

### Command mapping (catalog prefix → engine)

`search.*`→`search` · `citation.*`→`citation` · `text.*`→`text` · `numbers.*`→`numbers` ·
`claims.*`→`claims` · `extract.*`→`extract` · `media.*`→`media` · `time.normalize`→`extract time-normalize` ·
`similarity.*`→`text similarity-*` · `attribution.check`→`text attribution-check` ·
`tm.*`/`terms.*`/`qa.*`/`langid.detect`/`drift.monitor`/`translate.document|plan|domain-pack|low-resource|transliterate|bilingual-review|proofread|domain-review|source-edit|feedback-loop|backtranslation|pivot-guard|dictionary`→`translate` ·
`translate.summarize`→`models summarize` · `locale.*`→`locale` ·
`collections.*`→`collections` (`records`→`records-list`) · `provenance.*`/`freshness.*`→`collections` ·
`workflow.*`→`workflow` (`queue`→`queue-list`, `decisions`→`decisions-list`, `schedule`→`schedule-due`) ·
`review.*`→`workflow` (`plan`→`review-plan`) · `security.*`→`security` (`redact`→`redact-scan`, `import-gate`→`scan-media`) ·
`deliver.*`→`deliver` (`multilingual-consistency`→`multilingual`) ·
`benchmark.*`→`benchmark` · `models.*`→`models` (`translate.summarize`→`models summarize`) ·
`design.*`/`writing.*`→`design` · `selftest.*`→`selftest` · `init`→`rwt init` · `doctor.*`→`rwt doctor` ·
`audit.verify`→`workflow audit-verify` (also `rwt audit verify`).

Missing in catalog but shipped: every engine also exposes direct subcommands (`rwt <engine> <action> --help`).

## 9. Engine quick reference

Every engine implements `--help` and per-action `--help`; examples below are smoke-tested.
Common pattern: stateless checkers take files; stateful operations take `--workspace DIR`.

| Engine | Role | Headline actions |
|---|---|---|
| `corpus` | ingestion & lifecycle | `ingest`, `dedup`, `integrity`, `snapshot`, `encoding`, `deboilerplate`, `install`, `update`, `route`, `timeline`, `diversity`, `independence` |
| `search` | retrieval & answers | `query`, `passages`, `answer` (abstains), `build`, `decompose`, `expand`, `hybrid`, `diversity`, `explain` |
| `citation` | citation lifecycle | `extract`, `resolve` (local only), `normalize`, `format` (apa/mla/chicago/ieee/vancouver/harvard/oscola/bluebook/custom), `check`, `context`, `graph`, `analyze`, `selfcite`, `completeness`, `venue`, `disambiguate` |
| `text` | editing & style | `readability`, `stats`, `redundancy`, `structure`, `plain-language`, `tech-consistency`, `copy-edit`, `line-edit`, `tone`, `logic-flow`, `conclusion-check`, `requirements`, `matrix`, `summarize`, `compare-versions`, `final-proof`, `calibration`, `dev-edit`, `audience`, `tracked-revision`, `similarity-scan`, `similarity-patchwrite`, `attribution-check` |
| `numbers` | quantitative accuracy | `extract`, `recheck` (`--op percent-change|percentage-points|ratio|percent-of|unit-convert|proportion-z`), `audit-stat`, `chart-text`, `chronology`, `meta-analysis`, `kappa` |
| `claims` | claim verification | `extract`, `add`, `list`, `link`, `verify` (abstains), `corroborate`, `quote-verify`, `coverage`, `contradictions`, `identity` |
| `translation` | translation workflow | `langid`, `terms-*` (add/lookup/import/consistency/extract/inflect/acronyms/entity-policy/definition-audit/glossary-build), `tm-*` (add/match/import-tmx/export-tmx/clean/align), `qa-score`, `qa-taxonomy`, `acceptance`, `drift`, `translate-document`, `pivot-guard`, `backtranslation`, `source-edit`, `feedback-loop`, `dictionary`, `translate-plan`, `low-resource`, `domain-pack`, `transliterate`, `bilingual-review`, `proofread`, `domain-review` |
| `locale` | locale engineering | `variant`, `number-date`, `rtl`, `cjk`, `font-coverage`, `register`, `inclusive`, `codeswitch`, `filipino` |
| `extract` | structured extraction | `entities`, `link-entities`, `relations`, `events`, `methodology`, `funding`, `definitions`, `time-normalize` |
| `media` | media evidence | `timecodes`, `transcript-clean`, `extract-figures`, `chart-recovery`, `screenshot-pack`, `video-segment`, `audio-review`, `multimodal-citation`, `alt-text`, `visual-claim`, `map-evidence` |
| `collections` | registries & freshness | `records-add|list|update`, `import`, `links`, `link-list`, `retraction`, `coverage`, `classify-review`, `classify-authority`, `prioritize-primary`, `disclose`, `readiness`, `freshness-check|score|schedule`, `provenance-manifest|verify-chain|gaps|snapshot-id|lineage|run-manifest` |
| `workflow` | review methodology | `queue-add|list|decide`, `gate`, `decisions-add|list`, `change-control|decide`, `adjudicate`, `prisma`, `review-plan`, `riskofbias`, `synthesis`, `evidence-table`, `living-update`, `robustness`, `runner`, `schedule-due`, `state`, `backup-verify`, `regression-gate`, `audit-verify` |
| `security` | safety & rights | `redact-scan|apply`, `classify`, `deid`, `injection-scan`, `scan-media`, `quote-guard`, `sensitive-quote`, `usage-meter`, `public-domain`, `open-license`, `fair-use`, `rights`, `embargo`, `export-check`, `export-gate`, `leakage-test`, `enclave`, `access`, `retention`, `incident` |
| `deliver` | packaging | `report` (accessible HTML), `accessible-check`, `archive` (.zip/.tar.gz + manifest), `reopen`, `citation-integrity`, `multilingual`, `migration-plan`, `handoff`, `pdf-preflight` |
| `benchmark` | evaluation | `cer`, `recall` (recall@k/MRR/nDCG vs gold JSON), `factcheck`, `citation`, `writing`, `translation`, `acceptance`, `sets`, `agent` (adapter), `hallucination` (adapter) |
| `models` | local AI adapters | `ocr`, `stt` (`--diarize`), `mt`, `llm`, `embed`, `rerank`, `vlm`, `route`, `summarize` (extractive fallback) |
| `design` | research design & writing | `question`, `study-advisor`, `checklist` (sampling/measurement/protocol/ethics/causal/survey), `coding`, `survey`, `viz`, `limitations`, `outline`, `draft`, `methods` |
| `selftest` | self-tests | `clean`, `failure-recovery` |

## 10. Examples by capability area

### 10.1 Corpus → search → answer (auditable retrieval)
```bash
rwt corpus ingest --workspace ws --file paper.txt --title "Santos 2020"
rwt search build --workspace ws               # rebuild TF-IDF index after bulk changes
rwt search query  --workspace ws --query "wind electrolysis" --limit 5
rwt search passages --workspace ws --query "intermittency" --limit 3
rwt search answer  --workspace ws --question "What limits off-grid pilots?"  # abstains if nothing fits
rwt search explain --workspace ws --query "intermittency"                  # feature/weight explanation
```

### 10.2 Citations (extract → normalize → verify → format)
```bash
rwt citation extract --file paper.txt --output refs.json
rwt citation normalize --file refs.json --write
rwt citation check --file refs.json          # duplicates, missing DOI/pages/year
rwt citation format --file refs.json --style apa
rwt citation format --file refs.json --style vancouver   # also mla chicago ieee harvard oscola bluebook custom
rwt citation graph --graph graph.json            # graph.json: {edges:[{from,to}], records?}
rwt citation completeness --workspace ws --file refs.json
rwt citation resolve --file refs.json        # LOCAL resolution only; unresolved stay unresolved
```

### 10.3 Claims with evidence (the anti-hallucination spine)
```bash
rwt claims add --workspace ws --text "Intermittency is the binding constraint in published field studies." --confidence 0.8
rwt claims link --workspace ws --claim-id cla_XXX --document-id doc_YYY --relation supporting --locator "line 1" --excerpt "intermittency remains the binding constraint"
rwt claims verify --workspace ws --claim-id cla_XXX --max-hits 5     # retrieval support; reviewer decides
rwt claims corroborate --input sources.json      # {sources:[{source,independent,primary}]} → verdict
rwt claims contradictions --workspace ws                              # cross-document conflicts on linked claims
rwt claims coverage --workspace ws --file draft.md                   # which claims in text lack evidence links
rwt claims quote-verify --workspace ws --file draft.md               # quotes match stored sources
```

### 10.4 Numbers & systematic review
```bash
rwt numbers recheck --op percent-change --old 80 --new 60 --reported -25%
rwt numbers recheck --op unit-convert --old 5 --from-unit km --to-unit m
rwt numbers audit-stat --input stats.json       # {design, n, effect?, ci_low?, ci_high?, p_value?, sd?}
rwt numbers meta-analysis --input studies.json    # {studies:[{measure,n1,n2,mean1,mean2,sd1,sd2,...}]}; fixed-effect pooling
rwt numbers kappa --input ratings.json            # {matrix:[[r1,r2],...], labels?} or {rater1:[...], rater2:[...]}
rwt workflow prisma --input flow.json --svg-output prisma.svg  # {identified, duplicates_removed, screened, excluded_at_screening, full_text_assessed, exclusion_reasons:{...}, included}
rwt workflow riskofbias --input rob.json           # domain judgments → overall
rwt workflow synthesis --input studies.json        # [{id, design, direction}] grouped summary + vote-count warning
rwt workflow adjudicate --input reviews.json       # {reviews:[{reviewer, verdicts:{item: verdict}}]} → agreement, disagreements, Cohen-style κ
rwt workflow evidence-table --workspace ws          # Markdown evidence table from the evidence store
```

### 10.5 Writing support (evidence-bound)
```bash
rwt text structure --file draft.md
rwt text dev-edit --file draft.md                 # developmental: sections, paragraphs, counterarguments
rwt text calibration --file draft.md               # "proven" → flag for reviewer
rwt text plain-language --file draft.md --target grade-8
rwt text final-proof --file draft.md               # placeholders, numbering
rwt text similarity-scan --file draft.md --workspace ws   # overlap report (review, not verdict)
rwt text attribution-check --file draft.md               # quotes carry nearby citations
rwt design outline --kind report
rwt design methods --workspace ws --output methods.md
rwt design draft --title "Hydrogen review" --evidence evidence.json --output draft.md
```

### 10.6 Translation workflow
```bash
rwt translate langid --file es.txt
rwt translate terms-add --workspace ws --term "hidrógeno renovable" --source "renewable hydrogen" --domain energy
rwt translate terms-consistency --workspace ws --file translated.txt --domain energy
rwt translate tm-add --workspace ws --source "Renewable hydrogen reduces lifecycle emissions." --target "El hidrógeno renovable reduce las emisiones del ciclo de vida."
rwt translate tm-match --workspace ws --source "Renewable hydrogen reduces lifecycle emissions."
rwt translate tm-export-tmx --workspace ws --output tm.tmx
rwt translate acceptance --output-file translated.txt    # fixed format/terminology gate (asset-benchmark-cases.json)
rwt translate qa-score --errors errors.json --words 1200   # score + gate (≥95, zero critical)
rwt translate qa-taxonomy                              # the error taxonomy and weights
rwt translate qa-score --errors errors.json           # score + gate (≥95, zero critical)
rwt translate bilingual-review --source src.txt --target tgt.txt   # numbers, negation, modality, length
rwt translate low-resource --pair en-fil                 # human-first workflow envelope
rwt translate domain-pack --workspace ws --domain energy --output pack.json
```

### 10.7 Safety, rights, export
```bash
rwt security injection-scan --file suspicious-source.txt   # sources are data, never instructions
rwt security redact-scan --file notes.txt
rwt security redact-apply --file notes.txt --output notes.redacted.txt
rwt security deid --file extract.csv                       # direct + quasi-identifier risk points
rwt security rights --workspace ws --file figure.png --use export
rwt security embargo --workspace ws --set 2027-01-01
rwt security export-gate --workspace ws --file report.pdf                    # blocked (exit 4) if approval required
rwt security export-gate --workspace ws --file report.pdf --approved-by "Dr. Reyes"
rwt security retention --workspace ws                       # dry-run listing; deletion needs --apply --yes (snapshot first)
```

### 10.8 Packaging & delivery
```bash
rwt deliver accessible-check --file report.html
rwt deliver report --input report.json --output report.html   # accessible, self-contained
rwt deliver archive --include report.html --include exports --output release.zip --kind release --license "CC-BY-4.0"
rwt deliver reopen --paths release.zip --manifest release.zip.manifest.json  # parse-by-type + checksums
rwt deliver citation-integrity --file report.html
rwt deliver handoff --workspace ws --include report.pdf --output handoff.zip
rwt deliver multilingual --manifest editions.json
rwt deliver pdf-preflight --file report.pdf
```

### 10.9 Benchmarks (honest, offline)
```bash
rwt benchmark factcheck                 # 8/8 fixed regression cases (chronology/percent/unit)
rwt benchmark citation                  # 3/3 formatting cases (apa/vancouver/ieee)
rwt benchmark writing --file draft.md   # heuristic smoke score (NOT a grade)
rwt benchmark cer --reference ref.txt --hypothesis mt.txt
rwt benchmark recall --workspace ws --gold gold.json --k 10
rwt benchmark acceptance --output-file translated.txt
rwt benchmark agent --workspace ws --cases agent-cases.json       # exit 3 without local LLM
rwt benchmark hallucination --workspace ws --cases vlm-cases.json # exit 3 without local VLM
```

## 11. Safety & approval rules (enforced in code, not just documented)

1. **No network.** No engine performs HTTP/DNS. External identifiers (DOIs, URLs, ISBN) are stored and checked syntactically only.
2. **Approval gates exit 4.** `workflow gate`, `security export-gate` block until an authorized role records a decision (`workflow decisions-add --role approver --decision approve`). The blocked attempt is audited.
3. **Destructive operations need `--yes`** and print what they will do first: `corpus snapshot --restore NAME --yes`, `corpus update --delete --yes`, `security retention --apply --yes`, `security embargo --clear --yes`.
4. **Redaction never edits the original** — always a new `--output` copy, labelled with the irreversibility caveat.
5. **Injection scan treats sources as data.** Findings are reported, never executed; the protocol instructs citation with locators, not obedience.
6. **Rights gates.** `security rights` checks license/embargo/expiry for quote/export/display; `public-domain`, `open-license`, `fair-use` record reasoning packets (not legal advice) and route to the named legal reviewer.
7. **Embargoes** block `workflow gate` and can block export while set.
8. **Quotation limits** (workspace `quotation_limits`) are enforced by `security quote-guard`; licensed thresholds are configurable.
9. **Audit log** is hash-chained; `rwt audit verify` reports the first broken link. `workflow backup-verify` additionally test-restores stores and re-hashes.
10. **No fabricated verification.** `citation resolve` never invents a resolved status; `claims verify` reports retrieval support and leaves verdicts to reviewers; `translate qa-score` scores error *records* — it cannot see fluency it has no signal for.
11. **PII/leakage.** `security leakage-test` does 8-gram shingles against a restricted manifest to flag near-copies in candidates.
12. **Models are opt-in.** Unconfigured adapters return exit 3 with exact configuration steps; nothing is downloaded implicitly.

## 12. Optional local model adapters (`models.*`)

Nothing is bundled. To enable one, add to `.rwt/workspace.json`:
```json
"models": {
  "mt":        {"command": ["my-local-nmt", "--model", "es-en", "--beam", "6"], "timeout_seconds": 300, "input": "file"},
  "ocr":       {"command": ["tesseract", "stdin", "stdout", "eng"], "input": "stdin"},
  "stt":       {"command": ["whisper-local", "--model", "large-v3"], "input": "file"},
  "diarize":   {"command": ["pyannote-local"], "input": "file"},
  "llm":       {"command": ["llama-local", "--ctx", "8192"], "input": "arg"},
  "embed":     {"command": ["embed-local"], "input": "arg"},
  "rerank":    {"command": ["rerank-local"], "input": "file"},
  "vlm":       {"command": ["vlm-local"], "input": "file"},
  "summarize": {"command": ["summarize-local"], "input": "file"}
}
```
Contract: the command is executed with **argv, no shell**, with a hard
`timeout_seconds`; input file path appended as final argument (`input: "file"`),
or the text as final argument (`input: "arg"`), or stdin (`input: "stdin"`);
results = stdout (UTF-8, truncated at 200 KB). Nonzero exit → exit 3 with stderr tail.
`translate.summarize` and `benchmark agent|hallucination` follow the same
contract; without a model, `summarize` falls back to a deterministic extractive
summary (explicitly labelled), and the benchmarks exit 3.

Hybrid retrieval (`search hybrid`) reports the embedding leg as skipped without an
adapter; results are TF-IDF only, clearly labelled.

## 13. Input/output contracts (by family)

| Family | Input | Output |
|---|---|---|
| `corpus.ingest` | `--file` text/HTML/md/CSV | `{document_id, words, encoding, sha256, locator_scheme}`; stored under `.rwt/files/` |
| `search.query` | `--workspace --query [--limit]` | TF-IDF/boolean matches with scores and locators |
| `citation.format` | refs JSON list `{authors:[{family,given}], issued, title, container, …}` | formatted strings per style |
| `claims.add` | `--text` | claim record with id; `review_status: pending` |
| `numbers.meta-analysis` | studies JSON `[{n, mean, sd}…]` or event pairs | fixed-effect pooling, heterogeneity flags |
| `translate.qa-score` | errors JSON `[{category, severity}]` | score 0–100, per-1000-word rates, gate |
| `security.redact-apply` | file + `--output` | copy with `[TYPE-REDACTED]` tokens |
| `deliver.archive` | `--include` repeatable | `.zip`/`.tar.gz` with embedded `manifest.json` |
| `benchmark.recall` | gold JSON `[{query, relevant:[…]}]` | recall@k, MRR, nDCG@k |
| `workflow.prisma` | flow counts | JSON + SVG diagram |

Schemas that are missing here are shown by each command's `--help`. The catalog
(`asset-skill-catalog.json`) maps id → command → automation class; treat it as the
contract between `rwt skill <id>` and the engines.

## 14. Tests actually completed in this build

Honest status (2026-11-07, this machine). Every item below was executed, not asserted:

- **288/288 catalog skills dispatch** via `rwt skill <id> --help` → exit 0.
- `rwt version`, `rwt doctor --workspace`, `rwt capabilities --format json|text`, `--search` filtering.
- `selftest clean` → **pass** (temp workspace: init, ingest, query, claims add/verify, audit chain, integrity).
- `selftest failure-recovery` → **pass** (audit tamper detected; stored-file corruption detected by integrity check; snapshot saved; missing-file path errors cleanly).
- Manual restore drill: `corpus snapshot` → corrupt a stored file → `corpus integrity` reports NOT ok → `corpus snapshot --restore … --yes` → integrity ok (incl. automatic pre-restore snapshot).
- End-to-end: init → ingest → search → claims add/link → evidence linked with locators.
- `numbers recheck` (incl. `reported` with trailing `%`); `unit-convert` km→m, kg→g, F→C.
- `benchmark factcheck` → **8/8 pass**; `benchmark citation` → **3/3 pass**; `translate acceptance` → **5/5 pass** on conforming output, **0/5** on non-conforming (both paths verified).
- `translate qa-score`: 99.0 → gate `pass`; critical-error case → 70.0 → gate `fail`.
- `security redact-scan` finds email/phone/API-key; `redact-apply` emits copy with original untouched; `injection-scan` on hostile sample; `export-gate` → **exit 4** without approval, **exit 0 + manifest + sha256** with `--approved-by`.
- `workflow gate` (no approver) → **exit 4**; `readiness` on full evidence → `gate: "ready"`.
- `deliver report` → HTML with `lang`, single `h1`, captioned tables, embedded TOC, no external resources; `accessible-check` → **0 issues**; `deliver archive` → valid zip with `manifest.json`; `deliver reopen` → parses both files, checksums match; `audit verify` → chain ok.
- Invalid inputs verified → exit 2 (unknown skill id, missing ingest file, unknown command, bad `--format`).
- Changed input changes output: recheck `25%`/`50%` against the same data flips `match`; acceptance suite flips 5/5→0/5 with a non-conforming candidate.
- `deliver pdf-preflight` on the source catalog PDF → header present, PDF version 1.7, no structural issues; naive page-object count is **not** a reliable page count (stated).

Not tested / unavailable in this environment (see §15):
- No local OCR/STT/MT/LLM/embedding engines configured → `models.*`, `benchmark agent|hallucination` paths were verified only up to the exit-3 boundary, not against real model outputs.
- PDF page/locator extraction: not implemented (no PDF parsing dependency ships); preflight is structural only.
- Font coverage inspection parses sfnt `cmap` (formats 4/12) — tested on system fonts in this environment; exotic CFF/variable fonts may report limited coverage.
- Filipino/Tagalog MT: none exists offline in general use; `locale filipino` provides orthography/register review only, and Filipino *translation* is deliberately not auto-generated (correct equivalents cannot be guaranteed). Use `translate low-resource --pair en-fil` (human-first envelope).

## 15. Limitations & unavailable integrations (exact)

1. **No PDF text extraction.** `pdf-preflight` = header/trailer/encryption/size/page-object sanity only; no font-embedding, tag, or archival (PDF/A) verification — use a local veraPDF/callas if you need that. Page locators require an adapter; line locators are emitted instead.
2. **No OCR.** Scanned documents ingest as their text layer only; otherwise exit 3.
3. **No network retrieval.** Web search, DOI registration APIs, Crossref, licensing APIs are out of scope by design. Citation resolution is local.
4. **No bundled models.** MT/OCR/STT/LLM/embeddings/reranker/VLM are adapters you configure (§12). Quality and license of those models are your responsibility.
5. **Sentence splitting** breaks on abbreviations ("Dr. Smith.") — heuristic; treat as limitation when feeding exact sentence counts to other tools.
6. **TF-IDF hybrid leg skipped** without embeddings adapter; lexical only.
7. **Meta-analysis** is fixed-effect MD/SMD/OR/RR pooling only; no random-effects, no network meta-analysis, no IPD. Heterogeneity is flagged, never silently pooled over.
8. **κ**: Fleiss only, two-reviewer Cohen-style κ inside `workflow adjudicate`; not a full agreement suite.
9. **Chinese/Japanese/Korean** detection is script-based; no morphological analysis; segmentation is dictionary-free heuristic.
10. **Neural anything** (abstractive summaries, paraphrase) is not available offline; `models summarize` falls back to extractive and says so.
11. **Retention deletion** is real deletion (post-snapshot) — run it in a test workspace first.
12. **`enclave`** produces a policy checklist; it does not create OS-level isolation.
13. **Redaction regexes** cannot catch context-only identifiers ("I treated her for X on March 3") — human review mandatory.
14. Timezone/locale data derives from the OS; no IANA tz database is bundled.
15. Numbers from `chart-recovery` are exact only from structured CSV input; image charts return manual-read protocols.

## 16. Troubleshooting & recovery

| Symptom | Cause | Recovery |
|---|---|---|
| `No RWT workspace at DIR` | `.rwt/` missing | `rwt init DIR` |
| `Security gate blocked (exit 4)` | approval policy or embargo | record approver decision (`workflow decisions-add`) or lift embargo with `--yes` |
| `UnavailableError (exit 3)` | local model not configured/found | configure `models.*` (§12); check `rwt doctor --workspace` |
| `audit chain: BROKEN at entry N` | file tampering or interrupted write | restore a snapshot: `rwt corpus snapshot --restore NAME --yes`; re-verify with `rwt corpus integrity` |
| Ingest mojibake | wrong encoding detection | `--language`, then `corpus encoding` to inspect/convert |
| `claims-corpus` shows stale results | index built before ingest | `rwt search build --workspace ws` |
| `manifest mismatch` after archive edit | file changed after packaging | rebuild archive; never patch inside an archive |
| `qa-score` exits 0 but gate=fail | scores are data, not exit codes | read `result.gate`; CI should fail on `fail` |
| Export gate "identifiers present" | scan found candidates | redact-apply, then re-gate |
| Workspace corruption | interrupted write | `rwt corpus integrity --workspace ws` → then `restore` |
| Self-test fails | environment drift | run `rwt doctor`; compare `.rwt/workspace.json` version to TOOL_VERSION |

Recovery tenets: snapshots before destructive ops; decisions ledger is append-only; exports are
copies, never in-place; the audit chain proves — it does not repair — so restore from snapshots.

## 17. Extension & maintenance

- Add a capability: implement `cmd_x` in the engine, register in `COMMANDS`, add a `sub.add_parser` block, map (optional) in `BINDINGS` in `tools-rwt.py`, add/extend `asset-skill-catalog.json` (id must not collide), add `--help` text, run `selftest clean`.
- Change behavior of a capability that backs a catalog skill: the id in `asset-skill-catalog.json` stays stable; note the change in this file (versioning §18).
- Adjust weights/patterns/checklists without code: edit `asset-error-taxonomy.json`, `asset-security-patterns.json`, `asset-readiness-checklist.json`, `asset-config-presets.json`, `asset-benchmark-cases.json`, `asset-citation-styles.json`, `asset-seed-glossary.json` (all plain JSON, all loaded at call time).
- Keep runtime stdlib-only. Do **not** add pip requirements to the runtime path.
- Re-verify after any change: `rwt selftest clean && rwt selftest failure-recovery && rwt benchmark factcheck && rwt benchmark citation`.

## 18. Versioning & compatibility

- Tool version: **1.0.0** (`rwt version`). Catalog format version: embedded in `asset-skill-catalog.json` consumers only (id/name/category/command/automation).
- Workspace schema `version` must equal `TOOL_VERSION`; mismatches are reported by `doctor`.
- Semver intent: breaking workspace/CLI changes → major; new skills/engines → minor; fixes → patch. `TOOL_VERSION` lives in `tools-rwt-core.py` only.
- No telemetry. Version reporting is offline (this file and `rwt version`).

## 19. Appendix A alignment (per source catalog contract)

| Contract field | Where satisfied |
|---|---|
| Purpose | §1–2; per-engine docstrings |
| Triggers | use cases in §1; `capabilities --search` for discovery |
| Inputs | §13 + per-command `--help` |
| Outputs | §7 envelope + §13 per family |
| Local Dependencies | Python 3.9+ stdlib only (§3); optional adapters (§12) |
| Permissions & Rights | §11 rules; `security rights/embargo/access`; approvals exit 4 |
| Procedure | §4 quick start; §10 per area |
| Validation | §14 (executed) + `selftest clean/failure-recovery`, `benchmark *` |
| Human Review | gates (§11), reviewer routing in assistive skills, readiness checklist |
| Evidence & Provenance | locators everywhere, `provenance.*` registry, audit chain, manifests |
| Failure & Recovery | §16 + `selftest failure-recovery` (tested) |
| Benchmarks | §10.9; `benchmark recall/factcheck/citation/writing/translation/acceptance` |
| Versioning | §18; `TOOL_VERSION`, workspace schema, catalog contract |

---

*End of knowledge-readme.md. The only documentation file in this Skill Tool.*
