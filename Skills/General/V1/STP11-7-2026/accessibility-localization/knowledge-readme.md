# Accessibility and Localization Skill Tool

**Skill Tool name:** `accessibility-localization`  
**Version:** 1.0.0  
**Type:** Unified Skill Tool component for a future Copilot skill. This is **not** a complete skill package and contains **no SKILL.md**.  
**Runtime:** Python 3.10+ (tested on Python 3.13.16), standard library only.  
**Network access:** none. Input code is never executed.  
**Coverage:** 220 capabilities across 18 categories, implemented by 31 local engines.  
**Files:** 18 (5 `tools-*` files, 12 `asset-*` files, 1 `knowledge-*` file).  
**Entry point:** `tools-accessibility-localization.py`.

---

## 1. Purpose

This Skill Tool implements the capability catalog in *Emmre's Accessibility and Localization Skill Catalog* as one unified, reusable component. It is designed to be copied beside a parent skill's root `SKILL.md` and used by an agent or a human operator to:

- inventory and explain all 220 catalog capabilities;
- run local, deterministic accessibility and localization analyses on HTML, CSS, source, locale resources, documents, captions, audio, and review data;
- generate governance artifacts, test protocols, research protocols, risk registers, release-gate decisions, maturity assessments, crosswalks, and traceability matrices;
- produce inspectable JSON and Markdown evidence for every run;
- make the boundary between automation and human review explicit.

The catalog is treated as a capability map, not as a claim that every listed skill is already implemented. Capabilities whose work requires a browser, assistive technology, a human linguist, a disabled participant, a PDF/UA validator, OCR, or legal judgment are marked **assisted** and produce a rigorous, capability-specific protocol plus any automated pre-checks that can be run locally. They do not pretend to be completed human tests.

## 2. What this Skill Tool is and is not

**It is:**

- a single unified Skill Tool folder with 220 registered capabilities;
- a local analysis and evidence-generation engine;
- a structured, machine-readable and human-readable reporting tool;
- a configurable, bounded, and recoverable command-line interface.

**It is not:**

- a complete Copilot skill (there is no `SKILL.md`);
- a browser automation tool, a screen-reader emulator, or a substitute for testing with disabled users;
- a PDF/UA validator, an OCR engine, a machine-translation service, or a font-binary inspector;
- a legal opinion or a procurement decision;
- a network service or a hosted API.

## 3. Quick start

```bash
# 1. Show help and the capability list
python3 tools-accessibility-localization.py --help
python3 tools-accessibility-localization.py capabilities
python3 tools-accessibility-localization.py categories

# 2. Describe a capability and its example command
python3 tools-accessibility-localization.py describe automated-accessibility-scanner

# 3. Run a capability (HTML audit)
python3 tools-accessibility-localization.py run automated-accessibility-scanner --input page.html --output output/scanner

# 4. Run a governance artifact with no input
python3 tools-accessibility-localization.py run accessibility-statement-generator --output output/statement

# 5. Run every capability in a category
python3 tools-accessibility-localization.py run-category automated-wcag --input page.html --output output/wcag

# 6. Run every capability that accepts the input
python3 tools-accessibility-localization.py run-all --input evidence --output output/all

# 7. Validate configuration and input
python3 tools-accessibility-localization.py validate --input page.html --capability-id automated-accessibility-scanner

# 8. Write the default configuration
python3 tools-accessibility-localization.py config --init --output output/default-config.json

# 9. Run the built-in engine self-test (32 capabilities, all 31 engines)
python3 tools-accessibility-localization.py self-test
```

All package references are relative to the Skill Tool folder; the CLI resolves its own directory at runtime. No command requires a network connection, a paid service, or a credential.

## 4. Requirements and dependencies

- **Python:** 3.10 or newer. Tested on Python 3.13.16.
- **Packages:** none. The implementation uses only the Python standard library: `argparse`, `csv`, `html.parser`, `json`, `re`, `zipfile`, `wave`, `struct`, `unicodedata`, `datetime`, `pathlib`, `importlib`, and related modules.
- **Operating system:** any platform with Python 3.10+ and a POSIX or Windows shell. The timeout guard uses `SIGALRM` when available and degrades to a no-op on platforms without it.
- **External tools:** none required. The loudness engine reads uncompressed WAV directly. For perceptual loudness, MP3/AAC analysis, OCR, PDF/UA validation, browser rendering, or real assistive-technology testing, use an external tool and record the result as human evidence. These integrations are intentionally not bundled.

## 5. File inventory and loading order

| File | Role | Loaded by |
| --- | --- | --- |
| `knowledge-readme.md` | This knowledge file: purpose, complete capability list, commands, schemas, safety rules, validation evidence, limits, troubleshooting, extension, and maintenance. | Read by the agent or operator. |
| `tools-accessibility-localization.py` | Primary CLI entry point: argument parsing, validation, run orchestration, category/all runs, self-test, output writing. | Run directly. |
| `tools-core.py` | Shared core: configuration, schema validation, findings model, timeouts, Markdown report rendering, output envelope, file helpers. | Loaded first by the CLI. |
| `tools-templates.py` | Data-only protocol, governance-document, and review-workbench templates (58 protocols, 25 documents, 13 review templates). | Loaded second by the CLI. |
| `tools-registry.py` | Loads and validates the 220-entry registry, derives input/output contracts, standards, procedures, pass/fail criteria, and examples. | Loaded third by the CLI. |
| `tools-engines.py` | All 31 analysis/generation engines and the 75 static checks. | Loaded fourth by the CLI. |
| `asset-capability-registry.json` | The 220 catalog entries with category, description, operation, status, parameters, and checks. | `tools-registry.py`. |
| `asset-default-config.json` | Default configuration: locale, standards, limits, paths, privacy, approval, organization, release gate, report. | `config --init` and documentation; the runtime default is in `tools-core.py`. |
| `asset-wcag-22.json` | WCAG 2.2 A/AA criteria used by the requirements mapper, crosswalk, and reports. | `wcag_map`, `crosswalk`. |
| `asset-standards-crosswalk.json` | WCAG 2.2 to EN 301 549, Section 508, and ISO 30071-1 planning crosswalk. | `crosswalk`. |
| `asset-input.schema.json` | JSON Schema describing the flexible input envelope. | Validation and documentation. |
| `asset-output.schema.json` | JSON Schema describing the output envelope. | Validation and documentation. |
| `asset-fixture-sample.html` | Deliberately broken HTML fixture used by the self-test and as a safe example. | `self-test`. |
| `asset-fixture-locale.json` | Source/target locale fixture with missing keys, an extra key, and a placeholder mismatch. | `self-test`. |
| `asset-fixture-captions.vtt` | WebVTT fixture with overlap, reading-speed, line-length, and empty-cue issues. | `self-test`. |
| `asset-fixture-review.csv` | Bilingual review fixture with critical, serious, moderate, and minor rows. | `self-test`. |
| `asset-template-termbase.csv` | Reusable termbase template with a duplicate row for validation examples. | `self-test` and as a copyable template. |
| `asset-template-review-scorecard.csv` | Reusable language-quality scorecard template. | `self-test` and as a copyable template. |

The CLI loads the modules in this order: `tools-core.py`, `tools-templates.py`, `tools-registry.py`, `tools-engines.py`. Hyphenated filenames are loaded with `importlib.util.spec_from_file_location`; no package installation or `sys.path` change is required.

## 6. Commands

### `capabilities`

Lists the 220 capabilities. Filters: `--category`, `--search`, `--status {auto,assisted,human}`, `--operation`, `--long`, `--json`.

```bash
python3 tools-accessibility-localization.py capabilities
python3 tools-accessibility-localization.py capabilities --category localization
python3 tools-accessibility-localization.py capabilities --search caption
python3 tools-accessibility-localization.py capabilities --status assisted --json
```

### `categories`

Lists the 18 categories with counts and statuses. `--json` for machine-readable output.

```bash
python3 tools-accessibility-localization.py categories
python3 tools-accessibility-localization.py categories --json
```

### `describe <capability-id>`

Prints the full contract: purpose, operation, parameters, input/output kinds, standards, procedure, pass/fail criteria, human-review requirement, evidence, limitations, and an example command. `--json` for the structured specification.

```bash
python3 tools-accessibility-localization.py describe caption-quality-auditor
python3 tools-accessibility-localization.py describe caption-quality-auditor --json
```

### `run <capability-id>`

Runs one capability.

| Option | Default | Meaning |
| --- | --- | --- |
| `--input PATH` | none | Input file or directory. Required for analysis capabilities; optional for governance/plan capabilities. |
| `--brief TEXT` | none | Capability brief or context. Used by governance/plan capabilities and term extraction. |
| `--output PATH` | `output` | Output directory, or a `.json` file path. The Markdown report is written beside it. |
| `--config PATH` | built-in defaults | JSON configuration file. |
| `--timeout SECONDS` | config value (60) | Per-run timeout. |
| `--limit N` | config value (500) | Maximum findings. |
| `--json` | off | Print the full JSON envelope to stdout. |
| `--no-markdown` | off | Do not write the Markdown report. |

```bash
python3 tools-accessibility-localization.py run automated-accessibility-scanner --input page.html --output output/scanner
python3 tools-accessibility-localization.py run color-contrast-checker --input page.html --output output/contrast --json
python3 tools-accessibility-localization.py run accessibility-statement-generator --brief "Public portal, WCAG 2.2 AA target" --output output/statement
python3 tools-accessibility-localization.py run accessible-office-document-checker --input report.docx --output output/report
python3 tools-accessibility-localization.py run caption-quality-auditor --input captions.vtt --output output/captions
python3 tools-accessibility-localization.py run pseudolocalization-test-suite --input locales/en.json --output output/pseudo
python3 tools-accessibility-localization.py run language-quality-scorecard --input review.csv --output output/scorecard
python3 tools-accessibility-localization.py run accessibility-release-gatekeeper --input output/scanner/scanner.json --output output/gate
```

### `run-category <category>`

Runs every capability in one category. Capabilities that do not accept the supplied input are skipped with a reason. The combined JSON and Markdown reports list every result and skip.

```bash
python3 tools-accessibility-localization.py run-category automated-wcag --input page.html --output output/wcag
python3 tools-accessibility-localization.py run-category governance --output output/governance
python3 tools-accessibility-localization.py run-category documents --input report.docx --output output/documents
```

### `run-all`

Runs every capability that accepts the input. With a directory, directory-capable capabilities (HTML, locale, source, evidence, brief) run against the directory; optional file-based capabilities run with defaults; required file-based capabilities are skipped with a reason. This is a broad sweep, not a substitute for running each capability with its intended input.

```bash
python3 tools-accessibility-localization.py run-all --input evidence --output output/all
python3 tools-accessibility-localization.py run-all --input page.html --output output/html-all
python3 tools-accessibility-localization.py run-all --output output/no-input
```

### `validate`

Validates the configuration and, when `--capability-id` is supplied, the input against that capability's accepted kinds. Exits 2 when invalid.

```bash
python3 tools-accessibility-localization.py validate --config asset-default-config.json
python3 tools-accessibility-localization.py validate --input page.html --capability-id automated-accessibility-scanner
```

### `config`

Prints the effective configuration or writes the default configuration.

```bash
python3 tools-accessibility-localization.py config
python3 tools-accessibility-localization.py config --init --output output/default-config.json
```

### `self-test`

Runs 32 capability tests covering all 31 engines, using the bundled fixtures and temporary files. It writes outputs to a temporary directory, checks expected statuses/findings/artifacts, and removes the directory unless `--keep` is passed.

```bash
python3 tools-accessibility-localization.py self-test
python3 tools-accessibility-localization.py self-test --keep
```

## 7. Input handling and validation

The tool accepts a single capability, a category, or the whole catalog. It does not ask which tool to build; the registry already maps every capability to an operation.

**Accepted input kinds**

| Kind | Accepted files | Used by |
| --- | --- | --- |
| `html` | `.html`, `.htm`, `.xhtml`, `.css`, `.js`, `.jsx`, `.ts`, `.tsx`, `.vue`, `.svelte` | static, contrast, media, font coverage |
| `document` | `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.epub` | document audit |
| `source` | `.html`, `.htm`, `.xhtml`, `.css`, `.js`, `.jsx`, `.ts`, `.tsx`, `.vue`, `.svelte`, `.json`, `.properties`, `.csv`, `.txt` | i18n audit |
| `locale` | `.json`, `.properties`, `.csv`, `.txt` | pseudo-localization |
| `json` | `.json` | locale formatting, maturity, exception, retest, traceability |
| `csv` | `.csv` | termbase, translation memory, audio description, defects |
| `review` | `.csv`, `.json` | review workbench |
| `text` | `.txt`, `.md`, `.html` | readability |
| `captions` | `.vtt`, `.srt` | caption QA |
| `audio` | `.wav` | loudness |
| `data` | `.csv`, `.json` | chart/map/diagram alternatives |
| `findings` | `.json` | risk register, release gate |
| `evidence` | evidence files or a directory | evidence repository |
| `defects` | `.csv`, `.json` | defect triage |
| `retest` | `.json` | remediation verification |
| `vendor` | `.json`, `.txt`, `.md`, `.html` | procurement review |
| `request` | `.json` | exception review |
| `brief` | `.json`, `.txt`, `.md` | governance/plan artifacts |
| `config` | `.json` | CI generation |

**Validation before work begins**

- The input path must exist and must not exceed `limits.max_bytes`.
- A file's extension must be accepted by the capability's input kind.
- JSON inputs are parsed; CSV inputs are parsed and must contain data rows; text inputs must be non-empty; caption inputs must contain WebVTT or SRT cues; documents are checked for a valid signature; audio must be WAV.
- Directories are accepted by directory-capable engines. File-based engines resolve the first matching file in a directory, or the run is skipped with a reason in `run-category`/`run-all`.
- Configuration is checked for required sections and positive integer limits.

**Limits and recovery**

- `limits.max_findings` (default 500) caps findings.
- `limits.max_files` (default 200) caps directory scans.
- `limits.max_bytes` (default 8 MiB) caps file size.
- `limits.timeout_seconds` (default 60) bounds each run via `SIGALRM` where available.
- `limits.max_rows` (default 5000) is available for row-based engines.
- Invalid input returns a structured error envelope with an error code, message, field, and recovery suggestion; the exit code is 2.
- Engine failures are caught and reported as an error envelope; `run-category`/`run-all` continue with other capabilities.
- Partial outputs are written only after validation. The self-test uses a temporary directory and cleans it up.

## 8. Output contract

Every run writes:

- `<capability-id>.json` — the structured envelope.
- `<capability-id>.md` — the human-readable report.
- `artifacts/` — generated artifacts such as pseudo-localized resources, review workbenches, risk registers, gate decisions, protocols, and CI YAML.

The JSON envelope contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Envelope schema version (`1.0`). |
| `tool` | Tool name and version. |
| `generated_at` | UTC timestamp. |
| `capability` | ID, name, category, operation, and catalog status (`auto`, `assisted`, `human`). |
| `status` | `passed`, `attention`, `failed`, `error`, or `skipped`. |
| `summary` | Human-readable result. |
| `input` | Resolved input path, kind, size, and parse metadata. |
| `findings` | Normalized findings with check, severity, title, description, location, evidence, WCAG reference, and remediation. |
| `metrics` | Severity counts and operation-specific metrics. |
| `protocol` | Objective, roles, steps, pass/fail criteria, evidence, privacy, and human-review requirement for assisted capabilities. |
| `artifacts` | Generated artifact paths, kinds, and descriptions. |
| `limitations` | What the engine cannot verify. |
| `next_steps` | Recommended next actions. |
| `config` | The effective configuration used. |

**Exit codes:** `0` for passed/attention, `1` for failed, `2` for error or invalid input.

**Status semantics:** `failed` means at least one critical or serious finding (or a blocked gate); `attention` means moderate/minor findings or a draft requiring human review; `passed` means no findings above info; `error` means the engine could not complete; `skipped` means the capability was not run because the input was incompatible.

## 9. Configuration

The default configuration is in `asset-default-config.json` and in `tools-core.py`. It includes:

- `locale`: default `en-PH`, source `en`, targets `fil-PH` and `en-PH`, fallback chain, time zone `Asia/Manila`, currency `PHP`.
- `standards`: WCAG 2.2, levels A and AA, document standards, localization standards.
- `limits`: findings, files, bytes, timeout, rows.
- `paths`: output, artifacts, cache.
- `privacy`: redact PII, do not retain recordings, no external services.
- `approval`: deny by default for publish, delete, credential, financial, and security actions.
- `organization`: name, scope, owner, feedback channel, response days, escalation contact.
- `release_gate`: block on critical/serious, allow exceptions, maximum exception days, require owner.
- `report`: include evidence, remediation, protocol, and Markdown.

Example configuration override:

```json
{
  "locale": {"default": "fil-PH", "targets": ["fil-PH", "en-PH"]},
  "limits": {"max_findings": 200, "timeout_seconds": 30},
  "organization": {"name": "Example Agency", "owner": "Accessibility Lead"},
  "release_gate": {"block_on": ["critical"], "max_exception_days": 30}
}
```

Use `--config` with any run. The effective configuration is echoed in the output envelope and the Markdown report.

## 10. Capability catalog (220)

### 10.1 Category summary

| Slug | Category | Capabilities | Automated | Assisted |
| --- | --- | ---: | ---: | ---: |
| `governance` | Accessibility Governance, Strategy, and Conformance | 12 | 7 | 5 |
| `automated-wcag` | Automated WCAG and Static Analysis | 12 | 11 | 1 |
| `screen-reader-at` | Screen Reader and Assistive Technology Testing | 12 | 0 | 12 |
| `keyboard-input` | Keyboard, Focus, Voice, and Alternative Input | 12 | 4 | 8 |
| `visual` | Visual Accessibility, Contrast, Reflow, and Motion | 12 | 2 | 10 |
| `forms-critical-journeys` | Forms, Authentication, Commerce, and Critical Journeys | 12 | 0 | 12 |
| `documents` | Accessible Documents, PDFs, and Office Content | 14 | 4 | 10 |
| `images-charts` | Images, Alt Text, Charts, Math, and Data Visualization | 12 | 5 | 7 |
| `media` | Audio, Video, Captions, and Descriptions | 12 | 2 | 10 |
| `platforms` | Mobile, Desktop, Kiosk, XR, and Embedded Accessibility | 12 | 0 | 12 |
| `cognitive` | Cognitive, Learning, Neurodiversity, and Plain Language | 12 | 1 | 11 |
| `i18n-architecture` | Localization Architecture and Internationalization | 12 | 6 | 6 |
| `terminology-language-quality` | Translation Memory, Terminology, and Language Quality | 12 | 5 | 7 |
| `filipino-english` | Filipino, English, and Philippine Locale Quality | 12 | 2 | 10 |
| `rtl-scripts-fonts` | Right-to-Left, Scripts, Fonts, and Typography | 12 | 2 | 10 |
| `pseudo-functional-qa` | Pseudolocalization and Functional Localization QA | 12 | 4 | 8 |
| `research-human-validation` | Inclusive Research and Human Validation | 12 | 1 | 11 |
| `procurement-operations` | Procurement, Third Parties, Support, and Continuous Operations | 14 | 4 | 10 |

**Total:** 220 capabilities — 60 automated, 160 assisted, 0 human-only. Every assisted capability produces a specific protocol and requires a qualified human to execute or approve it.

### 10.2 Capability list and examples

Every row is a real registered capability. The example command uses the prefixed primary entry point and relative paths. Replace the input path with your own file.

| ID | Name | Status | Operation | Input | Example |
| --- | --- | --- | --- | --- | --- |
| `accessibility-conformance-target-planner` | Accessibility Conformance Target Planner | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-conformance-target-planner --brief "Accessibility Conformance Target Planner" --output output/accessibility-conformance-target-planner` |
| `accessibility-evidence-repository-manager` | Accessibility Evidence Repository Manager | auto | `evidence_repo` | evidence | `python3 tools-accessibility-localization.py run accessibility-evidence-repository-manager --input input.json --output output/accessibility-evidence-repository-manager` |
| `accessibility-exception-reviewer` | Accessibility Exception Reviewer | assisted | `exception_review` | request | `python3 tools-accessibility-localization.py run accessibility-exception-reviewer --input input.json --output output/accessibility-exception-reviewer` |
| `accessibility-kpi-and-okr-designer` | Accessibility KPI and OKR Designer | auto | `kpi_design` | brief | `python3 tools-accessibility-localization.py run accessibility-kpi-and-okr-designer --brief "Accessibility KPI and OKR Designer" --output output/accessibility-kpi-and-okr-designer` |
| `accessibility-maturity-assessor` | Accessibility Maturity Assessor | auto | `maturity_assess` | json | `python3 tools-accessibility-localization.py run accessibility-maturity-assessor --input input.json --output output/accessibility-maturity-assessor` |
| `accessibility-policy-author` | Accessibility Policy Author | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-policy-author --brief "Accessibility Policy Author" --output output/accessibility-policy-author` |
| `accessibility-program-architect` | Accessibility Program Architect | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-program-architect --brief "Accessibility Program Architect" --output output/accessibility-program-architect` |
| `accessibility-release-gatekeeper` | Accessibility Release Gatekeeper | auto | `release_gate` | findings | `python3 tools-accessibility-localization.py run accessibility-release-gatekeeper --input input.json --output output/accessibility-release-gatekeeper` |
| `accessibility-risk-register-manager` | Accessibility Risk Register Manager | auto | `risk_register` | findings | `python3 tools-accessibility-localization.py run accessibility-risk-register-manager --input input.json --output output/accessibility-risk-register-manager` |
| `accessibility-standards-crosswalk-builder` | Accessibility Standards Crosswalk Builder | auto | `crosswalk` | brief | `python3 tools-accessibility-localization.py run accessibility-standards-crosswalk-builder --brief "Accessibility Standards Crosswalk Builder" --output output/accessibility-standards-crosswalk-builder` |
| `accessibility-statement-generator` | Accessibility Statement Generator | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-statement-generator --brief "Accessibility Statement Generator" --output output/accessibility-statement-generator` |
| `wcag-2-2-aa-requirements-mapper` | WCAG 2.2 AA Requirements Mapper | auto | `wcag_map` | brief | `python3 tools-accessibility-localization.py run wcag-2-2-aa-requirements-mapper --brief "WCAG 2.2 AA Requirements Mapper" --output output/wcag-2-2-aa-requirements-mapper` |
| `aria-misuse-detector` | ARIA Misuse Detector | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run aria-misuse-detector --input page.html --output output/aria-misuse-detector` |
| `accessibility-regression-baseline-manager` | Accessibility Regression Baseline Manager | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run accessibility-regression-baseline-manager --input page.html --output output/accessibility-regression-baseline-manager` |
| `accessible-name-computation-inspector` | Accessible Name Computation Inspector | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run accessible-name-computation-inspector --input page.html --output output/accessible-name-computation-inspector` |
| `automated-accessibility-scanner` | Automated Accessibility Scanner | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run automated-accessibility-scanner --input page.html --output output/automated-accessibility-scanner` |
| `component-accessibility-linter` | Component Accessibility Linter | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run component-accessibility-linter --input page.html --output output/component-accessibility-linter` |
| `dom-semantics-analyzer` | DOM Semantics Analyzer | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run dom-semantics-analyzer --input page.html --output output/dom-semantics-analyzer` |
| `dynamic-state-accessibility-monitor` | Dynamic State Accessibility Monitor | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run dynamic-state-accessibility-monitor --input page.html --output output/dynamic-state-accessibility-monitor` |
| `form-label-and-error-scanner` | Form Label and Error Scanner | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run form-label-and-error-scanner --input page.html --output output/form-label-and-error-scanner` |
| `heading-hierarchy-auditor` | Heading Hierarchy Auditor | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run heading-hierarchy-auditor --input page.html --output output/heading-hierarchy-auditor` |
| `landmark-coverage-auditor` | Landmark Coverage Auditor | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run landmark-coverage-auditor --input page.html --output output/landmark-coverage-auditor` |
| `link-purpose-analyzer` | Link Purpose Analyzer | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run link-purpose-analyzer --input page.html --output output/link-purpose-analyzer` |
| `wcag-2-2-aa-test-suite-orchestrator` | WCAG 2.2 AA Test Suite Orchestrator | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run wcag-2-2-aa-test-suite-orchestrator --input page.html --output output/wcag-2-2-aa-test-suite-orchestrator` |
| `assistive-technology-compatibility-matrix` | Assistive Technology Compatibility Matrix | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run assistive-technology-compatibility-matrix --brief "Assistive Technology Compatibility Matrix" --output output/assistive-technology-compatibility-matrix` |
| `jaws-enterprise-workflow-tester` | JAWS Enterprise Workflow Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run jaws-enterprise-workflow-tester --brief "JAWS Enterprise Workflow Tester" --output output/jaws-enterprise-workflow-tester` |
| `nvda-journey-tester` | NVDA Journey Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run nvda-journey-tester --brief "NVDA Journey Tester" --output output/nvda-journey-tester` |
| `narrator-windows-tester` | Narrator Windows Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run narrator-windows-tester --brief "Narrator Windows Tester" --output output/narrator-windows-tester` |
| `refreshable-braille-tester` | Refreshable Braille Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run refreshable-braille-tester --brief "Refreshable Braille Tester" --output output/refreshable-braille-tester` |
| `screen-magnifier-workflow-tester` | Screen Magnifier Workflow Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run screen-magnifier-workflow-tester --brief "Screen Magnifier Workflow Tester" --output output/screen-magnifier-workflow-tester` |
| `screen-reader-test-environment` | Screen-Reader Test Environment | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run screen-reader-test-environment --brief "Screen-Reader Test Environment" --output output/screen-reader-test-environment` |
| `speech-output-transcript-analyzer` | Speech Output Transcript Analyzer | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run speech-output-transcript-analyzer --input review.csv --output output/speech-output-transcript-analyzer` |
| `switch-access-tester` | Switch Access Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run switch-access-tester --brief "Switch Access Tester" --output output/switch-access-tester` |
| `talkback-android-tester` | TalkBack Android Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run talkback-android-tester --brief "TalkBack Android Tester" --output output/talkback-android-tester` |
| `voiceover-ios-tester` | VoiceOver iOS Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run voiceover-ios-tester --brief "VoiceOver iOS Tester" --output output/voiceover-ios-tester` |
| `voiceover-macos-tester` | VoiceOver macOS Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run voiceover-macos-tester --brief "VoiceOver macOS Tester" --output output/voiceover-macos-tester` |
| `eye-gaze-interaction-reviewer` | Eye-Gaze Interaction Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run eye-gaze-interaction-reviewer --brief "Eye-Gaze Interaction Reviewer" --output output/eye-gaze-interaction-reviewer` |
| `focus-management-state-tester` | Focus Management State Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run focus-management-state-tester --brief "Focus Management State Tester" --output output/focus-management-state-tester` |
| `focus-visibility-inspector` | Focus Visibility Inspector | auto | `contrast_audit` | html | `python3 tools-accessibility-localization.py run focus-visibility-inspector --input page.html --output output/focus-visibility-inspector` |
| `focus-order-validator` | Focus-Order Validator | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run focus-order-validator --input page.html --output output/focus-order-validator` |
| `gesture-alternative-validator` | Gesture Alternative Validator | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run gesture-alternative-validator --input page.html --output output/gesture-alternative-validator` |
| `keyboard-trap-detector` | Keyboard Trap Detector | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run keyboard-trap-detector --input page.html --output output/keyboard-trap-detector` |
| `keyboard-only-navigation-test` | Keyboard-Only Navigation Test | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run keyboard-only-navigation-test --brief "Keyboard-Only Navigation Test" --output output/keyboard-only-navigation-test` |
| `pointer-target-size-auditor` | Pointer Target Size Auditor | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run pointer-target-size-auditor --input page.html --output output/pointer-target-size-auditor` |
| `shortcut-collision-analyzer` | Shortcut Collision Analyzer | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run shortcut-collision-analyzer --input page.html --output output/shortcut-collision-analyzer` |
| `sticky-key-and-slow-key-compatibility-tester` | Sticky-Key and Slow-Key Compatibility Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run sticky-key-and-slow-key-compatibility-tester --brief "Sticky-Key and Slow-Key Compatibility Tester" --output output/sticky-key-and-slow-key-compatibility-tester` |
| `switch-navigation-test` | Switch Navigation Test | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run switch-navigation-test --brief "Switch Navigation Test" --output output/switch-navigation-test` |
| `voice-control-test-suite` | Voice-Control Test Suite | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run voice-control-test-suite --brief "Voice-Control Test Suite" --output output/voice-control-test-suite` |
| `animation-pause-and-control-tester` | Animation Pause and Control Tester | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run animation-pause-and-control-tester --input page.html --output output/animation-pause-and-control-tester` |
| `color-contrast-checker` | Color-Contrast Checker | auto | `contrast_audit` | html | `python3 tools-accessibility-localization.py run color-contrast-checker --input page.html --output output/color-contrast-checker` |
| `content-zoom-and-magnification-tester` | Content Zoom and Magnification Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run content-zoom-and-magnification-tester --brief "Content Zoom and Magnification Tester" --output output/content-zoom-and-magnification-tester` |
| `dyslexia-friendly-typography-options` | Dyslexia-Friendly Typography Options | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run dyslexia-friendly-typography-options --input page.html --output output/dyslexia-friendly-typography-options` |
| `high-contrast-mode-test` | High-Contrast Mode Test | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run high-contrast-mode-test --brief "High-Contrast Mode Test" --output output/high-contrast-mode-test` |
| `low-vision-theme-designer` | Low-Vision Theme Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run low-vision-theme-designer --brief "Low-Vision Theme Designer" --output output/low-vision-theme-designer` |
| `non-color-information-validator` | Non-Color Information Validator | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run non-color-information-validator --input page.html --output output/non-color-information-validator` |
| `photosensitivity-safety-checker` | Photosensitivity Safety Checker | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run photosensitivity-safety-checker --input page.html --output output/photosensitivity-safety-checker` |
| `reduced-motion-support-validator` | Reduced-Motion Support Validator | auto | `static_audit` | html | `python3 tools-accessibility-localization.py run reduced-motion-support-validator --input page.html --output output/reduced-motion-support-validator` |
| `responsive-reflow-tester` | Responsive Reflow Tester | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run responsive-reflow-tester --input page.html --output output/responsive-reflow-tester` |
| `text-resizing-test` | Text-Resizing Test | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run text-resizing-test --input page.html --output output/text-resizing-test` |
| `visual-reading-order-inspector` | Visual Reading Order Inspector | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run visual-reading-order-inspector --input page.html --output output/visual-reading-order-inspector` |
| `accessible-authentication-reviewer` | Accessible Authentication Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run accessible-authentication-reviewer --input page.html --output output/accessible-authentication-reviewer` |
| `accessible-captcha-alternative-planner` | Accessible CAPTCHA Alternative Planner | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-captcha-alternative-planner --brief "Accessible CAPTCHA Alternative Planner" --output output/accessible-captcha-alternative-planner` |
| `accessible-data-entry-assistant` | Accessible Data Entry Assistant | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run accessible-data-entry-assistant --input page.html --output output/accessible-data-entry-assistant` |
| `accessible-form-architecture` | Accessible Form Architecture | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-form-architecture --brief "Accessible Form Architecture" --output output/accessible-form-architecture` |
| `accessible-signature-workflow` | Accessible Signature Workflow | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-signature-workflow --brief "Accessible Signature Workflow" --output output/accessible-signature-workflow` |
| `checkout-accessibility-tester` | Checkout Accessibility Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run checkout-accessibility-tester --brief "Checkout Accessibility Tester" --output output/checkout-accessibility-tester` |
| `education-assessment-accessibility-tester` | Education Assessment Accessibility Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run education-assessment-accessibility-tester --brief "Education Assessment Accessibility Tester" --output output/education-assessment-accessibility-tester` |
| `error-identification-and-recovery-tester` | Error Identification and Recovery Tester | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run error-identification-and-recovery-tester --input page.html --output output/error-identification-and-recovery-tester` |
| `financial-data-accessibility-reviewer` | Financial Data Accessibility Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run financial-data-accessibility-reviewer --input page.html --output output/financial-data-accessibility-reviewer` |
| `government-service-journey-tester` | Government Service Journey Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run government-service-journey-tester --brief "Government Service Journey Tester" --output output/government-service-journey-tester` |
| `healthcare-journey-accessibility-reviewer` | Healthcare Journey Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run healthcare-journey-accessibility-reviewer --brief "Healthcare Journey Accessibility Reviewer" --output output/healthcare-journey-accessibility-reviewer` |
| `session-timeout-accessibility-tester` | Session Timeout Accessibility Tester | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run session-timeout-accessibility-tester --input page.html --output output/session-timeout-accessibility-tester` |
| `accessible-epub-validator` | Accessible EPUB Validator | auto | `document_audit` | document | `python3 tools-accessibility-localization.py run accessible-epub-validator --input document.pdf --output output/accessible-epub-validator` |
| `accessible-excel-workbook-builder` | Accessible Excel Workbook Builder | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-excel-workbook-builder --brief "Accessible Excel Workbook Builder" --output output/accessible-excel-workbook-builder` |
| `accessible-office-document-checker` | Accessible Office-Document Checker | auto | `document_audit` | document | `python3 tools-accessibility-localization.py run accessible-office-document-checker --input document.pdf --output output/accessible-office-document-checker` |
| `accessible-pdf-checker` | Accessible PDF Checker | auto | `document_audit` | document | `python3 tools-accessibility-localization.py run accessible-pdf-checker --input document.pdf --output output/accessible-pdf-checker` |
| `accessible-pdf-form-tester` | Accessible PDF Form Tester | assisted | `document_audit` | document | `python3 tools-accessibility-localization.py run accessible-pdf-form-tester --input document.pdf --output output/accessible-pdf-form-tester` |
| `accessible-powerpoint-deck-builder` | Accessible PowerPoint Deck Builder | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-powerpoint-deck-builder --brief "Accessible PowerPoint Deck Builder" --output output/accessible-powerpoint-deck-builder` |
| `accessible-template-library-manager` | Accessible Template Library Manager | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-template-library-manager --brief "Accessible Template Library Manager" --output output/accessible-template-library-manager` |
| `accessible-word-document-builder` | Accessible Word Document Builder | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-word-document-builder --brief "Accessible Word Document Builder" --output output/accessible-word-document-builder` |
| `document-conversion-accessibility-guard` | Document Conversion Accessibility Guard | assisted | `document_audit` | document | `python3 tools-accessibility-localization.py run document-conversion-accessibility-guard --input document.pdf --output output/document-conversion-accessibility-guard` |
| `document-plain-language-reviewer` | Document Plain-Language Reviewer | auto | `readability` | text | `python3 tools-accessibility-localization.py run document-plain-language-reviewer --input content.txt --output output/document-plain-language-reviewer` |
| `office-accessibility-checker-workflow` | Office Accessibility Checker Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run office-accessibility-checker-workflow --brief "Office Accessibility Checker Workflow" --output output/office-accessibility-checker-workflow` |
| `pdf-reading-order-validator` | PDF Reading Order Validator | assisted | `document_audit` | document | `python3 tools-accessibility-localization.py run pdf-reading-order-validator --input document.pdf --output output/pdf-reading-order-validator` |
| `pdf-tagging-remediation-specialist` | PDF Tagging Remediation Specialist | assisted | `document_audit` | document | `python3 tools-accessibility-localization.py run pdf-tagging-remediation-specialist --input document.pdf --output output/pdf-tagging-remediation-specialist` |
| `scanned-pdf-ocr-accessibility-workflow` | Scanned PDF OCR Accessibility Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run scanned-pdf-ocr-accessibility-workflow --brief "Scanned PDF OCR Accessibility Workflow" --output output/scanned-pdf-ocr-accessibility-workflow` |
| `accessible-chart-designer` | Accessible Chart Designer | auto | `data_alternative` | data | `python3 tools-accessibility-localization.py run accessible-chart-designer --input input.json --output output/accessible-chart-designer` |
| `accessible-dashboard-reviewer` | Accessible Dashboard Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run accessible-dashboard-reviewer --input page.html --output output/accessible-dashboard-reviewer` |
| `accessible-diagram-navigator` | Accessible Diagram Navigator | auto | `data_alternative` | data | `python3 tools-accessibility-localization.py run accessible-diagram-navigator --input input.json --output output/accessible-diagram-navigator` |
| `accessible-icon-semantics-reviewer` | Accessible Icon Semantics Reviewer | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run accessible-icon-semantics-reviewer --input page.html --output output/accessible-icon-semantics-reviewer` |
| `accessible-map-alternative-builder` | Accessible Map Alternative Builder | auto | `data_alternative` | data | `python3 tools-accessibility-localization.py run accessible-map-alternative-builder --input input.json --output output/accessible-map-alternative-builder` |
| `alt-text-quality-checker` | Alt-Text Quality Checker | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run alt-text-quality-checker --input page.html --output output/alt-text-quality-checker` |
| `chart-description-generator` | Chart Description Generator | auto | `data_alternative` | data | `python3 tools-accessibility-localization.py run chart-description-generator --input input.json --output output/chart-description-generator` |
| `chemical-and-scientific-notation-accessibility` | Chemical and Scientific Notation Accessibility | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run chemical-and-scientific-notation-accessibility --input page.html --output output/chemical-and-scientific-notation-accessibility` |
| `complex-image-description-author` | Complex Image Description Author | assisted | `data_alternative` | data | `python3 tools-accessibility-localization.py run complex-image-description-author --input input.json --output output/complex-image-description-author` |
| `decorative-image-classifier` | Decorative Image Classifier | auto | `media_audit` | html | `python3 tools-accessibility-localization.py run decorative-image-classifier --input page.html --output output/decorative-image-classifier` |
| `image-text-detector` | Image Text Detector | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run image-text-detector --input page.html --output output/image-text-detector` |
| `math-accessibility-formatter` | Math Accessibility Formatter | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run math-accessibility-formatter --input page.html --output output/math-accessibility-formatter` |
| `accessible-media-player-tester` | Accessible Media Player Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-media-player-tester --brief "Accessible Media Player Tester" --output output/accessible-media-player-tester` |
| `audio-description-workflow` | Audio-Description Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run audio-description-workflow --brief "Audio-Description Workflow" --output output/audio-description-workflow` |
| `caption-quality-auditor` | Caption Quality Auditor | auto | `caption_qa` | captions | `python3 tools-accessibility-localization.py run caption-quality-auditor --input captions.vtt --output output/caption-quality-auditor` |
| `caption-translation-and-localization` | Caption Translation and Localization | assisted | `caption_qa` | captions | `python3 tools-accessibility-localization.py run caption-translation-and-localization --input captions.vtt --output output/caption-translation-and-localization` |
| `captions-and-subtitle-engine` | Captions and Subtitle Engine | assisted | `caption_qa` | captions | `python3 tools-accessibility-localization.py run captions-and-subtitle-engine --input captions.vtt --output output/captions-and-subtitle-engine` |
| `descriptive-transcript-generator` | Descriptive Transcript Generator | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run descriptive-transcript-generator --input page.html --output output/descriptive-transcript-generator` |
| `extended-audio-description-planner` | Extended Audio Description Planner | assisted | `audio_description` | csv | `python3 tools-accessibility-localization.py run extended-audio-description-planner --input input.json --output output/extended-audio-description-planner` |
| `live-captioning-workflow` | Live Captioning Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run live-captioning-workflow --brief "Live Captioning Workflow" --output output/live-captioning-workflow` |
| `media-loudness-and-clarity-reviewer` | Media Loudness and Clarity Reviewer | auto | `loudness` | audio | `python3 tools-accessibility-localization.py run media-loudness-and-clarity-reviewer --input input.json --output output/media-loudness-and-clarity-reviewer` |
| `podcast-accessibility-packager` | Podcast Accessibility Packager | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run podcast-accessibility-packager --brief "Podcast Accessibility Packager" --output output/podcast-accessibility-packager` |
| `seizure-safe-video-reviewer` | Seizure-Safe Video Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run seizure-safe-video-reviewer --input page.html --output output/seizure-safe-video-reviewer` |
| `sign-language-video-workflow` | Sign-Language Video Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run sign-language-video-workflow --brief "Sign-Language Video Workflow" --output output/sign-language-video-workflow` |
| `desktop-application-accessibility-tester` | Desktop Application Accessibility Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run desktop-application-accessibility-tester --brief "Desktop Application Accessibility Tester" --output output/desktop-application-accessibility-tester` |
| `electron-application-accessibility-reviewer` | Electron Application Accessibility Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run electron-application-accessibility-reviewer --input page.html --output output/electron-application-accessibility-reviewer` |
| `embedded-hardware-accessibility-reviewer` | Embedded Hardware Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run embedded-hardware-accessibility-reviewer --brief "Embedded Hardware Accessibility Reviewer" --output output/embedded-hardware-accessibility-reviewer` |
| `kiosk-accessibility-designer` | Kiosk Accessibility Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run kiosk-accessibility-designer --brief "Kiosk Accessibility Designer" --output output/kiosk-accessibility-designer` |
| `mobile-accessibility-test-suite` | Mobile Accessibility Test Suite | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run mobile-accessibility-test-suite --brief "Mobile Accessibility Test Suite" --output output/mobile-accessibility-test-suite` |
| `native-android-accessibility-reviewer` | Native Android Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run native-android-accessibility-reviewer --brief "Native Android Accessibility Reviewer" --output output/native-android-accessibility-reviewer` |
| `native-ios-accessibility-reviewer` | Native iOS Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run native-ios-accessibility-reviewer --brief "Native iOS Accessibility Reviewer" --output output/native-ios-accessibility-reviewer` |
| `self-service-terminal-tester` | Self-Service Terminal Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run self-service-terminal-tester --brief "Self-Service Terminal Tester" --output output/self-service-terminal-tester` |
| `smart-tv-accessibility-reviewer` | Smart TV Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run smart-tv-accessibility-reviewer --brief "Smart TV Accessibility Reviewer" --output output/smart-tv-accessibility-reviewer` |
| `voice-assistant-accessibility-designer` | Voice Assistant Accessibility Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run voice-assistant-accessibility-designer --brief "Voice Assistant Accessibility Designer" --output output/voice-assistant-accessibility-designer` |
| `wearable-accessibility-reviewer` | Wearable Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run wearable-accessibility-reviewer --brief "Wearable Accessibility Reviewer" --output output/wearable-accessibility-reviewer` |
| `xr-accessibility-reviewer` | XR Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run xr-accessibility-reviewer --brief "XR Accessibility Reviewer" --output output/xr-accessibility-reviewer` |
| `adhd-friendly-workflow-reviewer` | ADHD-Friendly Workflow Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run adhd-friendly-workflow-reviewer --brief "ADHD-Friendly Workflow Reviewer" --output output/adhd-friendly-workflow-reviewer` |
| `accessible-help-and-guidance-designer` | Accessible Help and Guidance Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-help-and-guidance-designer --brief "Accessible Help and Guidance Designer" --output output/accessible-help-and-guidance-designer` |
| `attention-safe-notification-designer` | Attention-Safe Notification Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run attention-safe-notification-designer --brief "Attention-Safe Notification Designer" --output output/attention-safe-notification-designer` |
| `autism-inclusive-interface-reviewer` | Autism-Inclusive Interface Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run autism-inclusive-interface-reviewer --brief "Autism-Inclusive Interface Reviewer" --output output/autism-inclusive-interface-reviewer` |
| `cognitive-accessibility-reviewer` | Cognitive Accessibility Reviewer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run cognitive-accessibility-reviewer --brief "Cognitive Accessibility Reviewer" --output output/cognitive-accessibility-reviewer` |
| `cognitive-load-journey-tester` | Cognitive Load Journey Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run cognitive-load-journey-tester --brief "Cognitive Load Journey Tester" --output output/cognitive-load-journey-tester` |
| `consistent-navigation-auditor` | Consistent Navigation Auditor | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run consistent-navigation-auditor --input page.html --output output/consistent-navigation-auditor` |
| `dyscalculia-aware-data-reviewer` | Dyscalculia-Aware Data Reviewer | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run dyscalculia-aware-data-reviewer --input page.html --output output/dyscalculia-aware-data-reviewer` |
| `easy-read-content-designer` | Easy Read Content Designer | assisted | `readability` | text | `python3 tools-accessibility-localization.py run easy-read-content-designer --input content.txt --output output/easy-read-content-designer` |
| `memory-support-pattern-designer` | Memory Support Pattern Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run memory-support-pattern-designer --brief "Memory Support Pattern Designer" --output output/memory-support-pattern-designer` |
| `plain-language-rewriter` | Plain-Language Rewriter | assisted | `readability` | text | `python3 tools-accessibility-localization.py run plain-language-rewriter --input content.txt --output output/plain-language-rewriter` |
| `reading-level-analyzer` | Reading-Level Analyzer | auto | `readability` | text | `python3 tools-accessibility-localization.py run reading-level-analyzer --input content.txt --output output/reading-level-analyzer` |
| `bidirectional-text-safety-auditor` | Bidirectional Text Safety Auditor | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run bidirectional-text-safety-auditor --input locale.json --output output/bidirectional-text-safety-auditor` |
| `international-address-and-name-designer` | International Address and Name Designer | assisted | `locale_format` | json | `python3 tools-accessibility-localization.py run international-address-and-name-designer --input input.json --output output/international-address-and-name-designer` |
| `internationalization-readiness-auditor` | Internationalization Readiness Auditor | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run internationalization-readiness-auditor --input locale.json --output output/internationalization-readiness-auditor` |
| `language-pack-builder` | Language Pack Builder | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run language-pack-builder --brief "Language Pack Builder" --output output/language-pack-builder` |
| `locale-feature-flag-manager` | Locale Feature-Flag Manager | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run locale-feature-flag-manager --brief "Locale Feature-Flag Manager" --output output/locale-feature-flag-manager` |
| `locale-negotiation-and-fallback-manager` | Locale Negotiation and Fallback Manager | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run locale-negotiation-and-fallback-manager --brief "Locale Negotiation and Fallback Manager" --output output/locale-negotiation-and-fallback-manager` |
| `locale-specific-date-and-number-formatter` | Locale-Specific Date and Number Formatter | auto | `locale_format` | json | `python3 tools-accessibility-localization.py run locale-specific-date-and-number-formatter --input input.json --output output/locale-specific-date-and-number-formatter` |
| `localization-framework-architect` | Localization Framework Architect | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run localization-framework-architect --brief "Localization Framework Architect" --output output/localization-framework-architect` |
| `localization-key-governance` | Localization Key Governance | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run localization-key-governance --input locale.json --output output/localization-key-governance` |
| `plural-and-grammatical-variation-engine` | Plural and Grammatical Variation Engine | assisted | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run plural-and-grammatical-variation-engine --input locale.json --output output/plural-and-grammatical-variation-engine` |
| `string-externalization-scanner` | String Externalization Scanner | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run string-externalization-scanner --input locale.json --output output/string-externalization-scanner` |
| `unicode-and-encoding-validator` | Unicode and Encoding Validator | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run unicode-and-encoding-validator --input locale.json --output output/unicode-and-encoding-validator` |
| `back-translation-risk-check` | Back-Translation Risk Check | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run back-translation-risk-check --brief "Back-Translation Risk Check" --output output/back-translation-risk-check` |
| `bilingual-edit-and-proofread` | Bilingual Edit and Proofread | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run bilingual-edit-and-proofread --input review.csv --output output/bilingual-edit-and-proofread` |
| `human-translation-review` | Human Translation Review | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run human-translation-review --input review.csv --output output/human-translation-review` |
| `language-quality-scorecard` | Language Quality Scorecard | auto | `review_workbench` | review | `python3 tools-accessibility-localization.py run language-quality-scorecard --input review.csv --output output/language-quality-scorecard` |
| `machine-translation-quality-estimator` | Machine Translation Quality Estimator | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run machine-translation-quality-estimator --input review.csv --output output/machine-translation-quality-estimator` |
| `multilingual-style-guide-author` | Multilingual Style Guide Author | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run multilingual-style-guide-author --brief "Multilingual Style Guide Author" --output output/multilingual-style-guide-author` |
| `term-extraction-and-candidate-miner` | Term Extraction and Candidate Miner | assisted | `termbase_audit` | csv | `python3 tools-accessibility-localization.py run term-extraction-and-candidate-miner --input termbase.csv --output output/term-extraction-and-candidate-miner` |
| `terminology-consistency-checker` | Terminology Consistency Checker | auto | `termbase_audit` | csv | `python3 tools-accessibility-localization.py run terminology-consistency-checker --input termbase.csv --output output/terminology-consistency-checker` |
| `terminology-database-manager` | Terminology Database Manager | auto | `termbase_audit` | csv | `python3 tools-accessibility-localization.py run terminology-database-manager --input termbase.csv --output output/terminology-database-manager` |
| `transcreation-workflow` | Transcreation Workflow | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run transcreation-workflow --brief "Transcreation Workflow" --output output/transcreation-workflow` |
| `translation-error-taxonomy-manager` | Translation Error Taxonomy Manager | auto | `review_workbench` | review | `python3 tools-accessibility-localization.py run translation-error-taxonomy-manager --input review.csv --output output/translation-error-taxonomy-manager` |
| `translation-memory-manager` | Translation Memory Manager | auto | `tm_match` | csv | `python3 tools-accessibility-localization.py run translation-memory-manager --input termbase.csv --output output/translation-memory-manager` |
| `filipino-language-review` | Filipino Language Review | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run filipino-language-review --input review.csv --output output/filipino-language-review` |
| `filipino-plain-language-specialist` | Filipino Plain-Language Specialist | assisted | `readability` | text | `python3 tools-accessibility-localization.py run filipino-plain-language-specialist --input content.txt --output output/filipino-plain-language-specialist` |
| `filipino-speech-and-caption-reviewer` | Filipino Speech and Caption Reviewer | assisted | `caption_qa` | captions | `python3 tools-accessibility-localization.py run filipino-speech-and-caption-reviewer --input captions.vtt --output output/filipino-speech-and-caption-reviewer` |
| `filipino-terminology-curator` | Filipino Terminology Curator | assisted | `termbase_audit` | csv | `python3 tools-accessibility-localization.py run filipino-terminology-curator --input termbase.csv --output output/filipino-terminology-curator` |
| `filipino-english-bilingual-consistency-checker` | Filipino-English Bilingual Consistency Checker | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run filipino-english-bilingual-consistency-checker --input locale.json --output output/filipino-english-bilingual-consistency-checker` |
| `philippine-address-and-identity-form-tester` | Philippine Address and Identity Form Tester | assisted | `locale_format` | json | `python3 tools-accessibility-localization.py run philippine-address-and-identity-form-tester --input input.json --output output/philippine-address-and-identity-form-tester` |
| `philippine-cultural-sensitivity-reviewer` | Philippine Cultural Sensitivity Reviewer | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run philippine-cultural-sensitivity-reviewer --input review.csv --output output/philippine-cultural-sensitivity-reviewer` |
| `philippine-english-review` | Philippine English Review | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run philippine-english-review --input review.csv --output output/philippine-english-review` |
| `philippine-government-content-reviewer` | Philippine Government Content Reviewer | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run philippine-government-content-reviewer --input review.csv --output output/philippine-government-content-reviewer` |
| `philippine-locale-formatter` | Philippine Locale Formatter | auto | `locale_format` | json | `python3 tools-accessibility-localization.py run philippine-locale-formatter --input input.json --output output/philippine-locale-formatter` |
| `regional-language-expansion-planner` | Regional Language Expansion Planner | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run regional-language-expansion-planner --brief "Regional Language Expansion Planner" --output output/regional-language-expansion-planner` |
| `taglish-content-policy-reviewer` | Taglish Content Policy Reviewer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run taglish-content-policy-reviewer --brief "Taglish Content Policy Reviewer" --output output/taglish-content-policy-reviewer` |
| `bidirectional-content-debugger` | Bidirectional Content Debugger | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run bidirectional-content-debugger --input locale.json --output output/bidirectional-content-debugger` |
| `cjk-typography-reviewer` | CJK Typography Reviewer | assisted | `font_coverage` | html | `python3 tools-accessibility-localization.py run cjk-typography-reviewer --input page.html --output output/cjk-typography-reviewer` |
| `complex-script-shaping-tester` | Complex Script Shaping Tester | assisted | `font_coverage` | html | `python3 tools-accessibility-localization.py run complex-script-shaping-tester --input page.html --output output/complex-script-shaping-tester` |
| `emoji-and-symbol-accessibility-reviewer` | Emoji and Symbol Accessibility Reviewer | assisted | `media_audit` | html | `python3 tools-accessibility-localization.py run emoji-and-symbol-accessibility-reviewer --input page.html --output output/emoji-and-symbol-accessibility-reviewer` |
| `font-coverage-for-target-languages` | Font Coverage for Target Languages | auto | `font_coverage` | html | `python3 tools-accessibility-localization.py run font-coverage-for-target-languages --input page.html --output output/font-coverage-for-target-languages` |
| `indic-script-layout-reviewer` | Indic Script Layout Reviewer | assisted | `font_coverage` | html | `python3 tools-accessibility-localization.py run indic-script-layout-reviewer --input page.html --output output/indic-script-layout-reviewer` |
| `rtl-interaction-tester` | RTL Interaction Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run rtl-interaction-tester --brief "RTL Interaction Tester" --output output/rtl-interaction-tester` |
| `right-to-left-layout-support` | Right-to-Left Layout Support | assisted | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run right-to-left-layout-support --input locale.json --output output/right-to-left-layout-support` |
| `southeast-asian-script-reviewer` | Southeast Asian Script Reviewer | assisted | `font_coverage` | html | `python3 tools-accessibility-localization.py run southeast-asian-script-reviewer --input page.html --output output/southeast-asian-script-reviewer` |
| `transliteration-and-romanization-manager` | Transliteration and Romanization Manager | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run transliteration-and-romanization-manager --brief "Transliteration and Romanization Manager" --output output/transliteration-and-romanization-manager` |
| `typography-localization-tester` | Typography Localization Tester | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run typography-localization-tester --input page.html --output output/typography-localization-tester` |
| `webfont-performance-and-fallback-planner` | Webfont Performance and Fallback Planner | assisted | `font_coverage` | html | `python3 tools-accessibility-localization.py run webfont-performance-and-fallback-planner --input page.html --output output/webfont-performance-and-fallback-planner` |
| `locale-specific-search-tester` | Locale-Specific Search Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run locale-specific-search-tester --brief "Locale-Specific Search Tester" --output output/locale-specific-search-tester` |
| `localization-automation-harness` | Localization Automation Harness | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run localization-automation-harness --brief "Localization Automation Harness" --output output/localization-automation-harness` |
| `localized-build-smoke-tester` | Localized Build Smoke Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run localized-build-smoke-tester --brief "Localized Build Smoke Tester" --output output/localized-build-smoke-tester` |
| `localized-email-and-notification-tester` | Localized Email and Notification Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run localized-email-and-notification-tester --brief "Localized Email and Notification Tester" --output output/localized-email-and-notification-tester` |
| `localized-input-and-validation-tester` | Localized Input and Validation Tester | assisted | `locale_format` | json | `python3 tools-accessibility-localization.py run localized-input-and-validation-tester --input input.json --output output/localized-input-and-validation-tester` |
| `localized-print-and-export-tester` | Localized Print and Export Tester | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run localized-print-and-export-tester --brief "Localized Print and Export Tester" --output output/localized-print-and-export-tester` |
| `placeholder-and-variable-validator` | Placeholder and Variable Validator | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run placeholder-and-variable-validator --input locale.json --output output/placeholder-and-variable-validator` |
| `pseudolocalization-test-suite` | Pseudolocalization Test Suite | auto | `pseudo_generate` | locale | `python3 tools-accessibility-localization.py run pseudolocalization-test-suite --input locale.json --output output/pseudolocalization-test-suite` |
| `screenshot-localization-reviewer` | Screenshot Localization Reviewer | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run screenshot-localization-reviewer --input review.csv --output output/screenshot-localization-reviewer` |
| `string-expansion-stress-tester` | String Expansion Stress Tester | auto | `pseudo_generate` | locale | `python3 tools-accessibility-localization.py run string-expansion-stress-tester --input locale.json --output output/string-expansion-stress-tester` |
| `truncation-and-overflow-detector` | Truncation and Overflow Detector | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run truncation-and-overflow-detector --input page.html --output output/truncation-and-overflow-detector` |
| `untranslated-string-detector` | Untranslated String Detector | auto | `i18n_audit` | locale | `python3 tools-accessibility-localization.py run untranslated-string-detector --input locale.json --output output/untranslated-string-detector` |
| `accessibility-diary-study` | Accessibility Diary Study | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-diary-study --brief "Accessibility Diary Study" --output output/accessibility-diary-study` |
| `accessibility-support-feedback-miner` | Accessibility Support Feedback Miner | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run accessibility-support-feedback-miner --input review.csv --output output/accessibility-support-feedback-miner` |
| `accessible-research-protocol-designer` | Accessible Research Protocol Designer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-research-protocol-designer --brief "Accessible Research Protocol Designer" --output output/accessible-research-protocol-designer` |
| `assistive-technology-user-study-facilitator` | Assistive Technology User Study Facilitator | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run assistive-technology-user-study-facilitator --brief "Assistive Technology User Study Facilitator" --output output/assistive-technology-user-study-facilitator` |
| `bias-aware-persona-reviewer` | Bias-Aware Persona Reviewer | assisted | `review_workbench` | review | `python3 tools-accessibility-localization.py run bias-aware-persona-reviewer --input review.csv --output output/bias-aware-persona-reviewer` |
| `community-language-validation` | Community Language Validation | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run community-language-validation --brief "Community Language Validation" --output output/community-language-validation` |
| `disabled-participant-recruitment-planner` | Disabled Participant Recruitment Planner | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run disabled-participant-recruitment-planner --brief "Disabled Participant Recruitment Planner" --output output/disabled-participant-recruitment-planner` |
| `human-accessibility-testing` | Human Accessibility Testing | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run human-accessibility-testing --brief "Human Accessibility Testing" --output output/human-accessibility-testing` |
| `human-translation-review-panel` | Human Translation Review Panel | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run human-translation-review-panel --brief "Human Translation Review Panel" --output output/human-translation-review-panel` |
| `inclusive-co-design-facilitator` | Inclusive Co-Design Facilitator | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run inclusive-co-design-facilitator --brief "Inclusive Co-Design Facilitator" --output output/inclusive-co-design-facilitator` |
| `inclusive-survey-designer` | Inclusive Survey Designer | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run inclusive-survey-designer --brief "Inclusive Survey Designer" --output output/inclusive-survey-designer` |
| `research-finding-traceability-manager` | Research Finding Traceability Manager | auto | `traceability` | findings | `python3 tools-accessibility-localization.py run research-finding-traceability-manager --input input.json --output output/research-finding-traceability-manager` |
| `accessibility-ci-cd-integrator` | Accessibility CI/CD Integrator | auto | `ci_generate` | config | `python3 tools-accessibility-localization.py run accessibility-ci-cd-integrator --brief "Accessibility CI/CD Integrator" --output output/accessibility-ci-cd-integrator` |
| `accessibility-defect-triage` | Accessibility Defect Triage | auto | `defect_triage` | defects | `python3 tools-accessibility-localization.py run accessibility-defect-triage --input input.json --output output/accessibility-defect-triage` |
| `accessibility-incident-response` | Accessibility Incident Response | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-incident-response --brief "Accessibility Incident Response" --output output/accessibility-incident-response` |
| `accessibility-remediation-verifier` | Accessibility Remediation Verifier | auto | `remediation_verify` | retest | `python3 tools-accessibility-localization.py run accessibility-remediation-verifier --input input.json --output output/accessibility-remediation-verifier` |
| `accessibility-training-curriculum-builder` | Accessibility Training Curriculum Builder | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessibility-training-curriculum-builder --brief "Accessibility Training Curriculum Builder" --output output/accessibility-training-curriculum-builder` |
| `accessible-customer-support-designer` | Accessible Customer Support Designer | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-customer-support-designer --brief "Accessible Customer Support Designer" --output output/accessible-customer-support-designer` |
| `accessible-design-system-steward` | Accessible Design System Steward | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-design-system-steward --brief "Accessible Design System Steward" --output output/accessible-design-system-steward` |
| `accessible-help-center-auditor` | Accessible Help Center Auditor | assisted | `static_audit` | html | `python3 tools-accessibility-localization.py run accessible-help-center-auditor --input page.html --output output/accessible-help-center-auditor` |
| `accessible-procurement-requirements-author` | Accessible Procurement Requirements Author | assisted | `governance_artifact` | brief | `python3 tools-accessibility-localization.py run accessible-procurement-requirements-author --brief "Accessible Procurement Requirements Author" --output output/accessible-procurement-requirements-author` |
| `continuous-accessibility-monitoring` | Continuous Accessibility Monitoring | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run continuous-accessibility-monitoring --brief "Continuous Accessibility Monitoring" --output output/continuous-accessibility-monitoring` |
| `localization-release-manager` | Localization Release Manager | assisted | `plan_artifact` | brief | `python3 tools-accessibility-localization.py run localization-release-manager --brief "Localization Release Manager" --output output/localization-release-manager` |
| `third-party-component-accessibility-assessor` | Third-Party Component Accessibility Assessor | assisted | `procurement_review` | vendor | `python3 tools-accessibility-localization.py run third-party-component-accessibility-assessor --input input.json --output output/third-party-component-accessibility-assessor` |
| `vpat-and-acr-reviewer` | VPAT and ACR Reviewer | auto | `procurement_review` | vendor | `python3 tools-accessibility-localization.py run vpat-and-acr-reviewer --input input.json --output output/vpat-and-acr-reviewer` |
| `vendor-accessibility-due-diligence` | Vendor Accessibility Due Diligence | assisted | `procurement_review` | vendor | `python3 tools-accessibility-localization.py run vendor-accessibility-due-diligence --input input.json --output output/vendor-accessibility-due-diligence` |


## 11. Engines and operation explanations

### `static_audit`

**Purpose.** Parse HTML/CSS/source files and run deterministic WCAG, semantics, accessibility, localization, and content checks without executing code.  
**Capabilities.** 38 — ARIA Misuse Detector, Accessibility Regression Baseline Manager, Accessible Name Computation Inspector, Automated Accessibility Scanner, Component Accessibility Linter, DOM Semantics Analyzer and 32 more  
**Input.** HTML or a directory of HTML/CSS/source files.  
**Output.** JSON envelope, Markdown report, findings with locations and WCAG references.  
**Example.** `python3 tools-accessibility-localization.py run aria-misuse-detector --input page.html --output output/aria-misuse-detector`  
**Limitations.** No runtime, focus, or screen-reader behavior; CSS cascade is approximated.  
### `contrast_audit`

**Purpose.** Extract declared foreground/background color pairs and compute WCAG contrast ratios, including focus-indicator styling.  
**Capabilities.** 2 — Focus Visibility Inspector, Color-Contrast Checker  
**Input.** HTML or CSS with declared colors.  
**Output.** JSON envelope, Markdown report, computed ratios and failing selectors.  
**Example.** `python3 tools-accessibility-localization.py run focus-visibility-inspector --input page.html --output output/focus-visibility-inspector`  
**Limitations.** Only declared colors; images, gradients, and overlays need manual review.  
### `document_audit`

**Purpose.** Inspect PDF, OOXML, and EPUB structure, language, metadata, headings, tables, images, links, and reading-order signals.  
**Capabilities.** 7 — Accessible EPUB Validator, Accessible Office-Document Checker, Accessible PDF Checker, Accessible PDF Form Tester, Document Conversion Accessibility Guard, PDF Reading Order Validator and 1 more  
**Input.** PDF, DOCX, XLSX, PPTX, or EPUB.  
**Output.** JSON envelope, Markdown report, structural findings and metadata.  
**Example.** `python3 tools-accessibility-localization.py run accessible-epub-validator --input document.pdf --output output/accessible-epub-validator`  
**Limitations.** Structural checks only; no rendering, PDF/UA validation, or OCR.  
### `media_audit`

**Purpose.** Inventory images, SVG, video, audio, tracks, MathML, and charts; check alternatives, captions, transcripts, and descriptions.  
**Capabilities.** 8 — Accessible Icon Semantics Reviewer, Alt-Text Quality Checker, Chemical and Scientific Notation Accessibility, Decorative Image Classifier, Image Text Detector, Math Accessibility Formatter and 2 more  
**Input.** HTML with media elements.  
**Output.** JSON envelope, Markdown report, media inventory and alternatives findings.  
**Example.** `python3 tools-accessibility-localization.py run accessible-icon-semantics-reviewer --input page.html --output output/accessible-icon-semantics-reviewer`  
**Limitations.** No OCR, speech recognition, or audio decoding; alt-text quality needs human review.  
### `i18n_audit`

**Purpose.** Extract and validate locale resources, placeholders, key governance, Unicode safety, bidirectional text, RTL layout, and source/target parity.  
**Capabilities.** 11 — Bidirectional Text Safety Auditor, Internationalization Readiness Auditor, Localization Key Governance, Plural and Grammatical Variation Engine, String Externalization Scanner, Unicode and Encoding Validator and 5 more  
**Input.** Source files (HTML, CSS, JS/TS, JSON, properties, CSV, TXT) or a source directory.  
**Output.** JSON envelope, Markdown report, parity/placeholder/Unicode findings.  
**Example.** `python3 tools-accessibility-localization.py run bidirectional-text-safety-auditor --input locale.json --output output/bidirectional-text-safety-auditor`  
**Limitations.** Heuristic extraction; linguistic quality and cultural fit need human review.  
### `pseudo_generate`

**Purpose.** Generate a deterministic pseudo-localized resource that preserves placeholders and exposes layout and truncation risks.  
**Capabilities.** 2 — Pseudolocalization Test Suite, String Expansion Stress Tester  
**Input.** JSON or .properties locale resource.  
**Output.** JSON envelope, Markdown report, pseudo-localized resource artifact.  
**Example.** `python3 tools-accessibility-localization.py run pseudolocalization-test-suite --input locale.json --output output/pseudolocalization-test-suite`  
**Limitations.** Tests layout resilience, not translation quality.  
### `locale_format`

**Purpose.** Format or validate dates, times, numbers, currency, phone numbers, addresses, and names for the configured locale.  
**Capabilities.** 5 — International Address and Name Designer, Locale-Specific Date and Number Formatter, Philippine Address and Identity Form Tester, Philippine Locale Formatter, Localized Input and Validation Tester  
**Input.** JSON values object.  
**Output.** JSON envelope, Markdown report, formatted values artifact.  
**Example.** `python3 tools-accessibility-localization.py run international-address-and-name-designer --input input.json --output output/international-address-and-name-designer`  
**Limitations.** Defaults must be verified against official locale conventions.  
### `termbase_audit`

**Purpose.** Validate termbase structure, duplicates, missing translations, forbidden forms, and terminology consistency; extract candidate terms for review.  
**Capabilities.** 4 — Term Extraction and Candidate Miner, Terminology Consistency Checker, Terminology Database Manager, Filipino Terminology Curator  
**Input.** Termbase CSV (or a text/source CSV in extract mode).  
**Output.** JSON envelope, Markdown report, validation findings or candidate CSV.  
**Example.** `python3 tools-accessibility-localization.py run term-extraction-and-candidate-miner --input termbase.csv --output output/term-extraction-and-candidate-miner`  
**Limitations.** Structural validation; subject-matter approval is still required.  
### `tm_match`

**Purpose.** Validate translation-memory structure or score fuzzy matches between source segments and translation memory.  
**Capabilities.** 1 — Translation Memory Manager  
**Input.** Translation-memory CSV.  
**Output.** JSON envelope, Markdown report, match scores artifact.  
**Example.** `python3 tools-accessibility-localization.py run translation-memory-manager --input termbase.csv --output output/translation-memory-manager`  
**Limitations.** Fuzzy scores are heuristics, not translator judgment.  
### `review_workbench`

**Purpose.** Generate a reviewer-ready CSV/Markdown workbench and a severity-weighted scorecard for bilingual, MTQE, cultural, or speech review.  
**Capabilities.** 13 — Speech Output Transcript Analyzer, Bilingual Edit and Proofread, Human Translation Review, Language Quality Scorecard, Machine Translation Quality Estimator, Translation Error Taxonomy Manager and 7 more  
**Input.** Review CSV or JSON.  
**Output.** JSON envelope, Markdown report, review workbench CSV and scorecard JSON.  
**Example.** `python3 tools-accessibility-localization.py run speech-output-transcript-analyzer --input review.csv --output output/speech-output-transcript-analyzer`  
**Limitations.** Organizes review; does not replace qualified human review.  
### `readability`

**Purpose.** Compute readability metrics, flag difficult constructions, and produce a rule-based plain-language or Easy Read draft for human review.  
**Capabilities.** 5 — Document Plain-Language Reviewer, Easy Read Content Designer, Plain-Language Rewriter, Reading-Level Analyzer, Filipino Plain-Language Specialist  
**Input.** Text, Markdown, or HTML.  
**Output.** JSON envelope, Markdown report, metrics and optional plain-language draft.  
**Example.** `python3 tools-accessibility-localization.py run document-plain-language-reviewer --input content.txt --output output/document-plain-language-reviewer`  
**Limitations.** Formulas are proxies; Filipino readability is heuristic.  
### `font_coverage`

**Purpose.** Inspect declared font stacks and Unicode script ranges and report coverage, fallback, shaping, and webfont performance risks.  
**Capabilities.** 6 — CJK Typography Reviewer, Complex Script Shaping Tester, Font Coverage for Target Languages, Indic Script Layout Reviewer, Southeast Asian Script Reviewer, Webfont Performance and Fallback Planner  
**Input.** HTML, CSS, or text.  
**Output.** JSON envelope, Markdown report, script and font-stack findings.  
**Example.** `python3 tools-accessibility-localization.py run cjk-typography-reviewer --input page.html --output output/cjk-typography-reviewer`  
**Limitations.** No binary glyph coverage or shaping verification.  
### `caption_qa`

**Purpose.** Parse WebVTT/SRT cues and validate timing, overlap, reading speed, line length, speaker labels, and completeness.  
**Capabilities.** 4 — Caption Quality Auditor, Caption Translation and Localization, Captions and Subtitle Engine, Filipino Speech and Caption Reviewer  
**Input.** WebVTT or SRT.  
**Output.** JSON envelope, Markdown report, cue-level findings and optional generated VTT.  
**Example.** `python3 tools-accessibility-localization.py run caption-quality-auditor --input captions.vtt --output output/caption-quality-auditor`  
**Limitations.** Does not verify caption accuracy against audio.  
### `audio_description`

**Purpose.** Validate description timing, reading speed, and overlap with dialogue, or generate a description plan.  
**Capabilities.** 1 — Extended Audio Description Planner  
**Input.** CSV or JSON description script.  
**Output.** JSON envelope, Markdown report, description plan or timing findings.  
**Example.** `python3 tools-accessibility-localization.py run extended-audio-description-planner --input input.json --output output/extended-audio-description-planner`  
**Limitations.** Description quality and cultural fit need human review.  
### `loudness`

**Purpose.** Measure peak and RMS levels in uncompressed WAV audio and report clipping and dynamic-range risks.  
**Capabilities.** 1 — Media Loudness and Clarity Reviewer  
**Input.** Uncompressed WAV.  
**Output.** JSON envelope, Markdown report, peak/RMS metrics.  
**Example.** `python3 tools-accessibility-localization.py run media-loudness-and-clarity-reviewer --input input.json --output output/media-loudness-and-clarity-reviewer`  
**Limitations.** No perceptual LUFS or compressed-format analysis.  
### `data_alternative`

**Purpose.** Generate chart descriptions, data tables, searchable map alternatives, diagram navigators, or long-description templates.  
**Capabilities.** 5 — Accessible Chart Designer, Accessible Diagram Navigator, Accessible Map Alternative Builder, Chart Description Generator, Complex Image Description Author  
**Input.** Chart/map/diagram CSV or JSON.  
**Output.** JSON envelope, Markdown report, description/table artifact.  
**Example.** `python3 tools-accessibility-localization.py run accessible-chart-designer --input input.json --output output/accessible-chart-designer`  
**Limitations.** Generated descriptions are drafts; a domain expert must verify them.  
### `governance_artifact`

**Purpose.** Generate a policy, statement, charter, procurement requirement, design-system guidance, or other governance artifact for human approval.  
**Capabilities.** 25 — Accessibility Conformance Target Planner, Accessibility Policy Author, Accessibility Program Architect, Accessibility Statement Generator, Low-Vision Theme Designer, Accessible CAPTCHA Alternative Planner and 19 more  
**Input.** Optional brief; defaults are used if omitted.  
**Output.** JSON envelope, Markdown report, governance artifact Markdown.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-conformance-target-planner --brief "Accessibility Conformance Target Planner" --output output/accessibility-conformance-target-planner`  
**Limitations.** Drafts require owner, legal, and procurement approval.  
### `plan_artifact`

**Purpose.** Generate a test, research, or operations protocol with objective, roles, steps, evidence, and pass/fail criteria.  
**Capabilities.** 67 — Assistive Technology Compatibility Matrix, JAWS Enterprise Workflow Tester, NVDA Journey Tester, Narrator Windows Tester, Refreshable Braille Tester, Screen Magnifier Workflow Tester and 61 more  
**Input.** Optional brief; defaults are used if omitted.  
**Output.** JSON envelope, Markdown report, protocol Markdown.  
**Example.** `python3 tools-accessibility-localization.py run assistive-technology-compatibility-matrix --brief "Assistive Technology Compatibility Matrix" --output output/assistive-technology-compatibility-matrix`  
**Limitations.** Protocols require human execution; they are not test results.  
### `risk_register`

**Purpose.** Convert findings into a scored risk register with user impact, legal exposure, reach, recurrence, owner, mitigation, and due date.  
**Capabilities.** 1 — Accessibility Risk Register Manager  
**Input.** Findings JSON from an analysis run.  
**Output.** JSON envelope, Markdown report, risk-register CSV.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-risk-register-manager --input input.json --output output/accessibility-risk-register-manager`  
**Limitations.** Scoring is a decision aid; legal exposure requires legal review.  
### `release_gate`

**Purpose.** Apply configured blocking thresholds to findings and return an allow/block/conditional decision with reasons.  
**Capabilities.** 1 — Accessibility Release Gatekeeper  
**Input.** Findings JSON from an analysis run.  
**Output.** JSON envelope, Markdown report, gate-decision JSON.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-release-gatekeeper --input input.json --output output/accessibility-release-gatekeeper`  
**Limitations.** Only evaluates supplied evidence; cannot discover unknown barriers.  
### `maturity_assess`

**Purpose.** Score eight accessibility dimensions against a 1-5 rubric and produce a staged improvement roadmap.  
**Capabilities.** 1 — Accessibility Maturity Assessor  
**Input.** Optional JSON scores object; defaults are used if omitted.  
**Output.** JSON envelope, Markdown report, maturity-assessment JSON.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-maturity-assessor --input input.json --output output/accessibility-maturity-assessor`  
**Limitations.** Scores depend on the quality of supplied evidence.  
### `crosswalk`

**Purpose.** Map WCAG 2.2 criteria to EN 301 549, Section 508, and ISO 30071-1 planning references with a verification warning.  
**Capabilities.** 1 — Accessibility Standards Crosswalk Builder  
**Input.** No input required.  
**Output.** JSON envelope, Markdown report, crosswalk Markdown.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-standards-crosswalk-builder --brief "Accessibility Standards Crosswalk Builder" --output output/accessibility-standards-crosswalk-builder`  
**Limitations.** Clause families are a planning aid; verify current editions.  
### `wcag_map`

**Purpose.** Map journeys or features to applicable WCAG 2.2 A/AA criteria and report evidence gaps.  
**Capabilities.** 1 — WCAG 2.2 AA Requirements Mapper  
**Input.** Optional journey JSON or text list.  
**Output.** JSON envelope, Markdown report, requirements-map Markdown.  
**Example.** `python3 tools-accessibility-localization.py run wcag-2-2-aa-requirements-mapper --brief "WCAG 2.2 AA Requirements Mapper" --output output/wcag-2-2-aa-requirements-mapper`  
**Limitations.** Applicability depends on the actual product and jurisdiction.  
### `kpi_design`

**Purpose.** Generate outcome-based accessibility KPIs and OKRs with baselines, targets, methods, and anti-patterns.  
**Capabilities.** 1 — Accessibility KPI and OKR Designer  
**Input.** No input required.  
**Output.** JSON envelope, Markdown report, KPI/OKR JSON.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-kpi-and-okr-designer --brief "Accessibility KPI and OKR Designer" --output output/accessibility-kpi-and-okr-designer`  
**Limitations.** Targets must be validated against organizational baselines.  
### `exception_review`

**Purpose.** Evaluate an exception request for scope, duration, equivalent access, owner, and constraint; return a recommendation for human decision.  
**Capabilities.** 1 — Accessibility Exception Reviewer  
**Input.** Exception request JSON.  
**Output.** JSON envelope, Markdown report, exception-review JSON.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-exception-reviewer --input input.json --output output/accessibility-exception-reviewer`  
**Limitations.** Recommends only; an accountable human decides.  
### `evidence_repo`

**Purpose.** Inventory evidence files by type and date and report gaps.  
**Capabilities.** 1 — Accessibility Evidence Repository Manager  
**Input.** Evidence directory or file.  
**Output.** JSON envelope, Markdown report, evidence-manifest CSV.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-evidence-repository-manager --input input.json --output output/accessibility-evidence-repository-manager`  
**Limitations.** Does not verify authenticity or completeness of evidence.  
### `defect_triage`

**Purpose.** Score defects by severity, journey importance, and workaround quality and produce a prioritized queue.  
**Capabilities.** 1 — Accessibility Defect Triage  
**Input.** Defect CSV or JSON.  
**Output.** JSON envelope, Markdown report, prioritized-defect CSV.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-defect-triage --input input.json --output output/accessibility-defect-triage`  
**Limitations.** Prioritization is a decision aid; owners remain accountable.  
### `remediation_verify`

**Purpose.** Match retest evidence to original findings and return verified/unverified/reopened decisions.  
**Capabilities.** 1 — Accessibility Remediation Verifier  
**Input.** Retest JSON with findings and retest arrays.  
**Output.** JSON envelope, Markdown report, verification JSON.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-remediation-verifier --input input.json --output output/accessibility-remediation-verifier`  
**Limitations.** Depends on the quality of supplied retest evidence.  
### `procurement_review`

**Purpose.** Review a vendor or VPAT/ACR document for conformance claims, testing evidence, limitations, and remediation terms.  
**Capabilities.** 3 — Third-Party Component Accessibility Assessor, VPAT and ACR Reviewer, Vendor Accessibility Due Diligence  
**Input.** Vendor/VPAT/ACR JSON, text, Markdown, or HTML.  
**Output.** JSON envelope, Markdown report, procurement findings.  
**Example.** `python3 tools-accessibility-localization.py run third-party-component-accessibility-assessor --input input.json --output output/third-party-component-accessibility-assessor`  
**Limitations.** Not a legal opinion or a substitute for vendor testing.  
### `ci_generate`

**Purpose.** Generate a GitHub Actions pipeline that runs the Skill Tool self-test and locale checks and uploads evidence.  
**Capabilities.** 1 — Accessibility CI/CD Integrator  
**Input.** No input required.  
**Output.** JSON envelope, Markdown report, CI YAML.  
**Example.** `python3 tools-accessibility-localization.py run accessibility-ci-cd-integrator --brief "Accessibility CI/CD Integrator" --output output/accessibility-ci-cd-integrator`  
**Limitations.** Review the pipeline before deployment; no secrets are included.  
### `traceability`

**Purpose.** Link findings to requirements and report orphan findings or requirements.  
**Capabilities.** 1 — Research Finding Traceability Manager  
**Input.** Traceability JSON with findings and requirements.  
**Output.** JSON envelope, Markdown report, traceability-matrix CSV.  
**Example.** `python3 tools-accessibility-localization.py run research-finding-traceability-manager --input input.json --output output/research-finding-traceability-manager`  
**Limitations.** Depends on complete requirement and finding data.  

## 12. Safety, privacy, and approval rules

- **No destructive actions.** The tool writes only to the output directory and the artifacts directory. It does not delete, publish, deploy, send, or modify source files.
- **No arbitrary code execution.** Input HTML, CSS, JavaScript, and source files are parsed as text. Scripts are never executed. No `eval`, `exec`, `subprocess`, or shell invocation is used.
- **No network access.** There are no HTTP clients, API calls, telemetry, or external services. The tool works offline.
- **No embedded secrets.** The package contains no credentials, private endpoints, or paid service keys.
- **Privacy.** The default configuration sets a privacy policy: redact PII, do not retain recordings, and do not use external services. The `Config.redact` helper is available for callers that need to redact free text; the tool does not silently alter evidence. Research protocols require informed consent and a withdrawal process.
- **Human review.** Assisted capabilities are not evidence of completion. They produce a protocol, a workbench, or a draft that a qualified human must execute or approve. The `human_review` field in every specification states this.
- **Approval gates.** The release gate blocks critical and serious findings unless an approved exception exists. The exception reviewer recommends approve/reject/revise but does not approve; an accountable human owner must decide. Procurement review is not a legal opinion.
- **Destructive/irreversible actions.** None are implemented. If a future extension adds publishing, deletion, credential handling, financial transactions, or security changes, it must require explicit approval and must not be wired to this tool's current commands.

## 13. Validation and tests actually completed

All tests were run in the workspace with Python 3.13.16.

1. **Syntax and import check.** All five `tools-*.py` files parse and load through the CLI bootstrap. The 220-entry registry loads and validates; no duplicate IDs.
2. **Help and capability-list commands.** `--help`, `capabilities`, `categories`, `describe`, and `validate` were run successfully. Filters by category, search, status, and operation were exercised.
3. **Self-test: 32 capabilities, all 31 engines.** `python3 tools-accessibility-localization.py self-test` reports `32 passed, 0 failed`. The tested capabilities cover:
   - static analysis, contrast, office documents, alt text, placeholders, bilingual parity, captions, language scorecard, termbase, translation memory, readability, font coverage, audio description, loudness, chart description, statement generation, research protocol, risk register, release gate, maturity, crosswalk, WCAG map, KPI design, exception review, evidence repository, defect triage, remediation verification, VPAT/ACR review, CI generation, traceability, pseudo-localization, and Philippine locale formatting.
4. **Real interface runs.** The following were run through the CLI with real files and inspected:
   - `automated-accessibility-scanner` on the HTML fixture: 75 checks, 8 serious, 13 moderate, 8 minor, 14 info; status `failed`.
   - `color-contrast-checker` on the HTML fixture: 3 serious contrast findings; status `failed`.
   - `accessible-office-document-checker` on a generated DOCX: language and heading findings; status `failed`.
   - `accessible-pdf-checker` on a minimal PDF: 6 structural findings; status `failed`.
   - `accessible-epub-validator` on a minimal EPUB: missing language and alt-text findings; status `failed`.
   - `caption-quality-auditor` on the VTT fixture: overlap, reading-speed, line-length, and empty-cue findings; status `attention`.
   - `language-quality-scorecard` on the review fixture: 4 rows, 11 weighted errors, critical and serious issues; status `failed`.
   - `pseudolocalization-test-suite` on the locale fixture: 12 strings, placeholders preserved; status `passed`.
   - `philippine-locale-formatter` on a values JSON: formatted date, amount, and phone; status `attention` due to validation findings.
   - `media-loudness-and-clarity-reviewer` on a generated WAV: peak -20.8 dBFS, RMS -23.8 dBFS, 1.0 s; status `passed`.
   - `accessibility-maturity-assessor` on a scores JSON: overall 2.75/5; status `attention`.
   - `accessibility-release-gatekeeper` on a findings JSON: blocked by a critical finding; status `failed`.
   - `vpat-and-acr-reviewer` on a vendor statement: missing remediation commitment; status `attention`.
5. **Invalid, missing, and recovery paths.** Missing input, nonexistent path, wrong input kind, invalid JSON for a JSON capability, unknown capability, unknown category, and invalid configuration were tested. Each returned a structured error or a clear message with exit code 2.
6. **Input change.** Changing the HTML fixture, locale fixture, caption fixture, review fixture, termbase fixture, and document inputs changed the findings and statuses as intended.
7. **Output inspection.** JSON envelopes were parsed and validated against `asset-output.schema.json` with zero errors. Markdown reports were opened and inspected. Artifacts were opened and inspected (pseudo-localized JSON, review workbench CSV, risk register CSV, gate decision JSON, protocol Markdown, CI YAML, chart description, map alternative, term candidates).
8. **Category and all runs.** All 18 categories were run with a mixed evidence directory: 170 capability results, 0 errors, 50 skipped with reasons. `run-all` with the same directory produced 170 results, 0 errors. `run-all` with no input produced 103 results, 0 errors, 117 skipped.
9. **Schema validation.** `asset-output.schema.json` validated a real scanner envelope with zero errors. `asset-input.schema.json` validated the locale fixture with zero errors.
10. **Configuration initialization.** `config --init` wrote a valid default config to a directory and to a file path.
11. **Self-test cleanup.** The self-test created a temporary directory, wrote outputs, and removed it. `--keep` retains it for inspection.

**Not tested (and not claimed):** real NVDA, JAWS, VoiceOver, TalkBack, Narrator, braille, switch, eye-gaze, or magnifier sessions; real browser rendering; real PDF/UA validation; OCR; perceptual loudness; binary font coverage; machine translation; human translation review; disabled-participant research; legal or procurement approval. The assisted capabilities produce the protocols and evidence structures for these activities but do not perform them.

## 14. Limits and unavailable integrations

- **Static analysis is not runtime analysis.** It cannot observe focus after interaction, live-region announcements, screen-reader speech, or browser-specific rendering.
- **Contrast analysis is limited to declared colors.** It does not resolve images, gradients, blend modes, opacity, or overlays.
- **PDF inspection is structural.** It checks raw PDF objects for language, structure tree, MarkInfo, tabs, title, and AcroForm. It is not a PDF/UA validator and does not render pages.
- **OOXML inspection is XML-level.** It checks core properties, heading styles, images, tables, and slide titles. It does not render the document.
- **EPUB inspection is structural.** It checks the container, OPF, language, title, nav, content-document language, titles, and image alt attributes. It does not test reading systems.
- **No OCR.** Scanned PDFs require an external OCR tool and human verification.
- **No perceptual loudness.** Only uncompressed WAV peak/RMS is measured. Use ffmpeg `loudnorm` or an equivalent tool for LUFS compliance.
- **No binary font inspection.** Font coverage checks declared font stacks and Unicode script ranges, not glyph coverage or shaping.
- **No machine translation.** The MTQE workbench organizes review; it does not translate.
- **No browser or assistive technology.** Screen-reader, keyboard, voice-control, switch, eye-gaze, magnifier, high-contrast, zoom, mobile, TV, XR, and kiosk capabilities produce protocols, not test results.
- **No human review.** Translation review, cultural review, research, co-design, and legal/procurement decisions require qualified humans.
- **No network.** The tool cannot scan a live URL, call an axe-core/Lighthouse/Pa11y service, or fetch a vendor ACR. Use local files or an external pipeline.
- **No hosted-service guarantees.** If a future parent package tries to reach an external service, that is a hosting/permission issue outside this tool's control.

## 15. Troubleshooting and recovery

| Symptom | Cause | Recovery |
| --- | --- | --- |
| `Input is required for ...` | A required input was omitted. | Pass `--input` with a supported file, or use a governance/plan capability. |
| `Input path does not exist` | Wrong path. | Check the path and retry. |
| `Input kind 'json' is not accepted` | The file extension does not match the capability. | Pass a file with an accepted extension; see `describe`. |
| `Invalid JSON in ...` | Malformed JSON. | Fix the syntax; the error includes line/column. |
| `CSV input has no data rows` | Empty CSV. | Add rows and a header. |
| `Caption input is not valid WebVTT or SRT` | Wrong caption format. | Use a `.vtt` or `.srt` file with valid cues. |
| `Unknown check(s)` | A registry check is not implemented. | Update the registry or the check map; this should not happen with the bundled registry. |
| `Unknown template` | A capability references a missing template. | Use a template listed by `describe` or add it to `tools-templates.py`. |
| `Timeout` | The run exceeded the timeout. | Increase `--timeout` or reduce the input. |
| `No HTML files found` | The directory has no HTML. | Pass an HTML file or a directory with HTML. |
| `No JSON file found` | A file-based capability received a directory without a matching file. | Run the capability with a specific file, or use `run-all` with a directory for directory-capable capabilities. |
| `Config is missing the '...' section` | The config is incomplete. | Start from `config --init` and merge overrides. |
| `Registry entry ... is missing fields` | The registry asset is corrupted. | Restore `asset-capability-registry.json` from the package. |
| `BrokenPipeError` in a terminal | A pipe (for example, `| head`) closed. | Ignore; the tool itself completed. Avoid piping JSON to `head` if you need the full output. |

**Recovery path example:** if a scanner run fails because the input is a directory, pass the HTML file directly, or use `run-all` with the directory so the scanner runs against the HTML files it contains. If a gate is blocked, open the gate decision JSON, fix the blocking findings, re-run the analysis, and re-run the gate. If an exception is needed, complete the exception request JSON with scope, constraint, equivalent access, duration, and owner; the reviewer will return a recommendation for a human decision.

## 16. Extension and maintenance

**Add a capability**

1. Add the catalog entry to `asset-capability-registry.json` with `id`, `name`, `description`, `category`, `category_title`, `operation`, `status`, `params`, and `checks`.
2. Reuse an existing operation if possible. If the capability needs a new engine, add a function to `tools-engines.py` and register it in `ENGINES`.
3. Add the input kind to `INPUT_KIND` in `tools-registry.py` if it is new.
4. Add a template to `PROTOCOLS`, `DOCUMENTS`, or `REVIEW_TEMPLATES` if the capability generates a protocol or document.
5. Add a self-test case to `cmd_self_test` in the CLI.
6. Run `self-test` and a real `run` with the new capability.

**Add a static check**

1. Write a function `check_<name>(doc, ctx) -> list[Finding]` in `tools-engines.py`.
2. Add it to `CHECKS`.
3. Reference it from a capability's `checks` array.
4. Add a fixture assertion to the self-test.

**Change a standard or threshold**

- WCAG criteria are in `asset-wcag-22.json`; the crosswalk is in `asset-standards-crosswalk.json`.
- Contrast thresholds are in `check_contrast` (4.5:1 normal, 3:1 large).
- Release-gate thresholds are in the `release_gate` configuration section.
- Locale defaults are in the `locale` configuration section.

**Versioning**

- Tool version: `1.0.0` in `tools-core.py` and the README.
- Envelope schema version: `1.0`.
- Registry entry version: `1.0.0`.
- When changing the output envelope, update `asset-output.schema.json` and the self-test.
- When changing a capability's operation or parameters, update the registry and the examples.

**Maintenance checks**

- Run `python3 tools-accessibility-localization.py self-test` after any change.
- Run `python3 tools-accessibility-localization.py capabilities --json` and confirm 220 entries.
- Run `python3 tools-accessibility-localization.py run-all --input evidence --output /tmp/check` and confirm no errors.
- Validate a sample envelope against `asset-output.schema.json`.
- Re-run the category sweep and confirm no errors.

## 17. Skill package compatibility

- The Skill Tool folder contains **no `SKILL.md`**.
- It contains **no internal subfolders**. Every file is directly inside `accessibility-localization/`.
- Every filename begins with `tools-` or `asset-` and uses lowercase ASCII letters, digits, dots, and single hyphens.
- Every path is relative to the future skill root. There are no absolute paths, drive letters, parent traversal, or machine-specific paths.
- The folder can be copied directly beside a parent package's root `SKILL.md` without modification.
- The future combined package must contain exactly one `SKILL.md` at the skill root. This folder does not add one.
- The Skill Tool has 18 files (5 tools, 12 assets, 1 knowledge file), well below the 500-file combined-package limit.
- Every path is at most two levels deep from the future skill root (`accessibility-localization/<file>`).
- The final ZIP contains the `accessibility-localization/` folder directly at the ZIP root. It contains no nested archives, no wrapper folder, and no generated caches or outputs.
- The ZIP filename is descriptive: `accessibility-localization-skill-tool.zip`.

## 18. Asset inventory

| Asset | Purpose | Used by |
| --- | --- | --- |
| `asset-capability-registry.json` | 220 catalog entries and their operation mappings. | `tools-registry.py`, all commands. |
| `asset-default-config.json` | Portable default configuration. | `config --init`, documentation. |
| `asset-wcag-22.json` | 60 WCAG 2.2 criteria (A/AA plus two AAA reference criteria; 4.1.1 Parsing removed in 2.2). | `wcag_map`, `crosswalk`. |
| `asset-standards-crosswalk.json` | WCAG to EN 301 549, Section 508, and ISO 30071-1 planning crosswalk. | `crosswalk`. |
| `asset-input.schema.json` | Flexible input envelope schema. | Validation and documentation. |
| `asset-output.schema.json` | Output envelope schema. | Validation and documentation. |
| `asset-fixture-sample.html` | Broken HTML fixture. | `self-test`. |
| `asset-fixture-locale.json` | Source/target locale fixture. | `self-test`. |
| `asset-fixture-captions.vtt` | Caption fixture. | `self-test`. |
| `asset-fixture-review.csv` | Review fixture. | `self-test`. |
| `asset-template-termbase.csv` | Termbase template. | `self-test`, copyable template. |
| `asset-template-review-scorecard.csv` | Scorecard template. | `self-test`, copyable template. |

All 12 assets are used by the tool or the self-test. There are no decorative, placeholder, or unused assets.

---

## Appendix: status counts by category

| Category | Capabilities | Automated | Assisted |
| --- | ---: | ---: | ---: |
| `governance` | Accessibility Governance, Strategy, and Conformance | 12 | 7 | 5 |
| `automated-wcag` | Automated WCAG and Static Analysis | 12 | 11 | 1 |
| `screen-reader-at` | Screen Reader and Assistive Technology Testing | 12 | 0 | 12 |
| `keyboard-input` | Keyboard, Focus, Voice, and Alternative Input | 12 | 4 | 8 |
| `visual` | Visual Accessibility, Contrast, Reflow, and Motion | 12 | 2 | 10 |
| `forms-critical-journeys` | Forms, Authentication, Commerce, and Critical Journeys | 12 | 0 | 12 |
| `documents` | Accessible Documents, PDFs, and Office Content | 14 | 4 | 10 |
| `images-charts` | Images, Alt Text, Charts, Math, and Data Visualization | 12 | 5 | 7 |
| `media` | Audio, Video, Captions, and Descriptions | 12 | 2 | 10 |
| `platforms` | Mobile, Desktop, Kiosk, XR, and Embedded Accessibility | 12 | 0 | 12 |
| `cognitive` | Cognitive, Learning, Neurodiversity, and Plain Language | 12 | 1 | 11 |
| `i18n-architecture` | Localization Architecture and Internationalization | 12 | 6 | 6 |
| `terminology-language-quality` | Translation Memory, Terminology, and Language Quality | 12 | 5 | 7 |
| `filipino-english` | Filipino, English, and Philippine Locale Quality | 12 | 2 | 10 |
| `rtl-scripts-fonts` | Right-to-Left, Scripts, Fonts, and Typography | 12 | 2 | 10 |
| `pseudo-functional-qa` | Pseudolocalization and Functional Localization QA | 12 | 4 | 8 |
| `research-human-validation` | Inclusive Research and Human Validation | 12 | 1 | 11 |
| `procurement-operations` | Procurement, Third Parties, Support, and Continuous Operations | 14 | 4 | 10 |


## Appendix: operation counts

| Operation | Capabilities |
| --- | ---: |
| `static_audit` | 38 |
| `contrast_audit` | 2 |
| `document_audit` | 7 |
| `media_audit` | 8 |
| `i18n_audit` | 11 |
| `pseudo_generate` | 2 |
| `locale_format` | 5 |
| `termbase_audit` | 4 |
| `tm_match` | 1 |
| `review_workbench` | 13 |
| `readability` | 5 |
| `font_coverage` | 6 |
| `caption_qa` | 4 |
| `audio_description` | 1 |
| `loudness` | 1 |
| `data_alternative` | 5 |
| `governance_artifact` | 25 |
| `plan_artifact` | 67 |
| `risk_register` | 1 |
| `release_gate` | 1 |
| `maturity_assess` | 1 |
| `crosswalk` | 1 |
| `wcag_map` | 1 |
| `kpi_design` | 1 |
| `exception_review` | 1 |
| `evidence_repo` | 1 |
| `defect_triage` | 1 |
| `remediation_verify` | 1 |
| `procurement_review` | 3 |
| `ci_generate` | 1 |
| `traceability` | 1 |

---

*This Skill Tool is a component, not a complete skill. It does not include a SKILL.md and must be combined with a parent skill package that provides one.*
