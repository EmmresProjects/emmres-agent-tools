# universal-presentation-design — knowledge-readme.md
## Purpose
This folder is a reusable **Skill Tool component** for a future Copilot skill. It is not a complete skill and intentionally contains no `SKILL.md`. It implements the unified capability umbrella described by `Universal_Presentation_Design_Skill_Catalog.pdf`: **35 categories and 525 named candidate capabilities** across presentation strategy, editable deck production, visual design, data visualization, diagrams, timelines, motion, multimedia planning, interactivity, accessibility, localization, governance, collaboration, QA, AI-assisted workflows, specialized formats, and technical interoperability.
The implementation is a local, deterministic Python engine. It produces editable PowerPoint (`.pptx`), vector SVG slides, self-contained interactive HTML, deck JSON, PNG previews, raster PDF, GIF motion previews, reports, handoffs, captions, scripts, policies, and reusable visual assets. External design platforms are handled through explicit portable handoffs rather than fake live API calls.
## Primary entry point and exact run commands
Run commands from the future skill root, with this folder copied beside the root `SKILL.md`. Use `bash` or another POSIX shell; on Windows use PowerShell with the same relative paths.
```bash
python3 universal-presentation-design/tools-presentation-design.py --help
python3 universal-presentation-design/tools-presentation-design.py capabilities
python3 universal-presentation-design/tools-presentation-design.py capabilities --category 12 --format text
python3 universal-presentation-design/tools-presentation-design.py build --input universal-presentation-design/asset-example-deck.json --output out/example --formats json,svg,html,pptx,png,pdf,gif
python3 universal-presentation-design/tools-presentation-design.py run --capability contrast-ratio-auditor --input universal-presentation-design/asset-example-deck.json --output out/contrast --formats json,svg,html,pptx
python3 universal-presentation-design/tools-presentation-design.py audit --input universal-presentation-design/asset-example-deck.json --fail-on-major
python3 universal-presentation-design/tools-presentation-design.py repair --input universal-presentation-design/asset-example-deck.json --output out/repaired --formats json,pptx
python3 universal-presentation-design/tools-presentation-design.py self-test
```
## Setup and dependencies
Supported runtime: Python 3.10 or newer. Tested in this environment with Python 3.13.16. Install the exact tested dependency versions:
```bash
python3 -m pip install python-pptx==1.0.2 Pillow==11.3.0 jsonschema==4.25.1 segno==1.6.6
```
Dependency roles: `python-pptx` for editable OOXML production/inspection; `Pillow` for image processing, PNG/PDF/GIF previews; `jsonschema` for deck validation; `segno` for local QR SVG generation. `lxml` is installed transitively by `python-pptx` and used for bounded OOXML patches. No network service, credential, paid API, browser, PowerPoint application, LibreOffice, FFmpeg, or generative model is required for the implemented local paths.
## File roles and loading order
| File | Role |
|---|---|
| `asset-capabilities.json` | Operational dispatch registry extracted from the supplied PDF: 525 capability ids, names, categories, descriptions, families, presets, modes, approval flags, and limitations. |
| `asset-config.json` | Default limits, paths, formats, providers, safety policy, accessibility thresholds, retries, and locale formatting presets. |
| `asset-deck.schema.json` | JSON Schema for the portable deck model. |
| `asset-example-brief.md` | Working Markdown brief input example. |
| `asset-example-data.csv` | Working CSV data input example. |
| `asset-example-deck.json` | Working example deck used by commands and tests. |
| `asset-example-image.png` | Reusable procedural image fixture for image workflow tests and examples. |
| `asset-example-request.html` | Copied-HTML request parsing fixture with tags, classes, data attributes, and numbering marks. |
| `asset-example-translations.json` | Supplied translation map fixture for deterministic localization tests. |
| `asset-themes.json` | Reusable theme presets: neutral, dark, high-contrast, warm, colorblind-safe. |
| `knowledge-readme.md` | This single consolidated knowledge file. |
| `tools-core.py` | Shared safe I/O, atomic writes, retries, deadlines/cancellation, input parsing, schema validation, themes, contrast, audit, repair, request parsing, diffing, and localization helpers. |
| `tools-operations.py` | Capability registry dispatch and family engines. Every registered capability maps to a real handler and preset. |
| `tools-pptx.py` | Editable PowerPoint writer/reader/inspector/recovery. Writes shapes, text, native basic charts/tables, notes, links, alt text, theme XML, sections, transitions, and kiosk timings. |
| `tools-presentation-design.py` | Primary CLI entry point. Parses commands, loads modules, validates paths/approvals, emits structured JSON, and returns stable exit codes. |
| `tools-raster.py` | Pillow raster previews, contact sheets, raster PDF, and GIF motion previews from the same layout primitives. |
| `tools-render.py` | Deterministic layout engine and vector SVG/HTML renderer. Includes charts, tables, diagrams, timelines, KPI cards, quizzes, links, and motion-aware HTML. |
| `tools-tests.py` | Bundled validation suite exercised through the real CLI, including a clean temporary skill root with one test SKILL.md. |

Loading order is `tools-core.py` → `tools-render.py` → `tools-raster.py` → `tools-pptx.py` → `tools-operations.py`, orchestrated by `tools-presentation-design.py`. Tests are loaded only by `self-test`. All packaged references are relative to the future skill root and use forward slashes.
## Inputs, outputs, parameters, and configuration
### Input handling
- `--input` accepts relative paths to JSON decks, Markdown, plain text, copied HTML, CSV/TSV, or PPTX. `--kind` can override auto-detection.
- `--text` accepts inline Markdown/text.
- HTML tags, CSS classes, data attributes, numbering marks, and extra spacing are ignored by `parse-request` and HTML input conversion.
- `parse-request` extracts category names, Skill Tool Names, and Descriptions from plain text, Markdown, or copied HTML and returns a unified umbrella name.
- CSV input detects numeric columns and creates chart/table slides. Markdown headings become sections/slides; fenced JSON can supply chart/table/diagram/timeline/quiz data.
- PPTX input is read through `python-pptx`; damaged OOXML can be salvaged by the recovery capability.
### Deck model
The canonical model is `asset-deck.schema.json`. Required top-level fields are `schema_version`, `title`, and `slides`. Each slide requires `id`, `layout`, and `title`. Supported layouts include `title`, `section`, `bullets`, `two-column`, `chart`, `table`, `diagram`, `timeline`, `image`, `quote`, `kpi`, `comparison`, `quiz`, `dashboard`, `closing`, and `blank`. Optional slide payloads include `chart`, `table`, `diagram`, `timeline`, `image`, `kpis`, `quiz`, `link`, `objects`, `notes`, `alt_text`, `transition`, `translations`, `status`, `owner`, and `tags`.
### Output formats
`--formats` accepts any comma-separated subset of `json,svg,html,pptx,png,pdf,gif`. Defaults are `json,svg,html,pptx`.
- `json`: normalized editable deck model.
- `svg`: one vector slide per file under `slides/` in the chosen output directory.
- `html`: self-contained interactive deck with inline CSS/JS, keyboard navigation, internal links, and quiz feedback.
- `pptx`: editable PowerPoint with native basic charts/tables where supported, notes, links, alt text, theme colors, sections, and transitions.
- `png`: slide previews and a contact sheet.
- `pdf`: raster PDF generated from PNG previews; not a vector PDF and not a PowerPoint application export.
- `gif`: simple slide-sequence motion preview; not object-level animation or video.
Every command returns structured JSON on stdout. Successful results include `ok`, capability/family/preset/mode when applicable, `data`, `artifacts`, `warnings`, and `metrics`. Errors include `ok:false` and `error.code/message/details`. Exit codes: `0` success, `1` internal error, `2` validation/unsafe input, `3` approval required, `4` not found/unknown capability, `5` missing dependency, `6` timeout, `7` cancelled.
### Configuration and parameters
Use `--config <relative-config.json>` to override `asset-config.json`. Important keys: `limits`, `defaults`, `paths`, `providers`, `safety`, `accessibility`, `retry`, and `locales`. Use `--params '<json>'` or `--params @params.json` for capability-specific options. Examples:
```bash
python3 universal-presentation-design/tools-presentation-design.py run --capability color-palette-architect --input universal-presentation-design/asset-example-deck.json --output out/palette --params '{"accent":"#0072B2","harmony":"triadic"}' --formats json,svg,html,pptx
python3 universal-presentation-design/tools-presentation-design.py run --capability image-crop-and-focal-point-specialist --input universal-presentation-design/asset-example-deck.json --output out/crop --params '{"image":"universal-presentation-design/asset-example-image.png","focal_x":0.6,"focal_y":0.4,"width":700,"height":400}' --formats json,png,pptx
python3 universal-presentation-design/tools-presentation-design.py run --capability interactive-quiz-builder --input universal-presentation-design/asset-example-deck.json --output out/quiz --formats json,html,pptx
python3 universal-presentation-design/tools-presentation-design.py localize --input universal-presentation-design/asset-example-deck.json --locale fr --translations universal-presentation-design/asset-example-translations.json --output out/fr --formats json,pptx
```
## Safety, approval, limits, timeouts, retries, cancellation, cleanup, and recovery
- No arbitrary shell, network, or tool execution is performed. Providers are local heuristics, procedural graphics, supplied translation maps, and local QR generation.
- No secrets, private endpoints, paid credentials, or unavailable services are embedded.
- Destructive overwrite requires both `--overwrite` and `--approve`. Non-empty output directories are moved to a `.bak` sibling before replacement; files are written atomically with temporary files and retries.
- Publishing, permission changes, final sign-off, privacy redaction, macro/script handoffs, and REST/API handoffs are approval-gated. Handoff generation does not execute the external action.
- Input/output paths must be relative, stay inside the current working root, and avoid parent traversal. Packaged resource references resolve from the future skill root.
- Limits are configurable in `asset-config.json`: input bytes, text characters, slides, objects per slide, CSV rows, image pixels, batch items, and SVG nodes.
- `--timeout` sets a cooperative deadline checked between slides/stages. SIGINT/SIGTERM set cancellation and return structured exit code 7 where signal handling is available.
- Atomic writes retry transient OS errors using `retry.attempts` and `retry.backoff_ms`. Temporary files are cleaned on failure. Overwrite backups support recovery.
- Damaged PPTX recovery first tries normal opening, then salvages slide text from OOXML parts and writes a reviewable recovered deck plus warnings.
## Capability families and examples
Every capability is reachable with:
```bash
python3 universal-presentation-design/tools-presentation-design.py run --capability <id> --input <relative-input> --output <relative-output-dir> --formats json,svg,html,pptx [--params @params.json] [--approve]
```
The table below lists every registered capability. Use the listed id in the command pattern above. Approval-gated rows require `--approve` only when the handler performs or prepares the gated action; dry-run remains non-writing.
| Family | Responsibility | Example capability | Example parameters / notes |
|---|---|---|---|
| `strategy` | Audience/outcome framing, narrative frameworks, briefs, evidence gaps, agenda/closing. | `audience-and-outcome-framing` | `{"audience":"Board","outcome":"Approve pilot","call_to_action":"Release phase-one funding"}` |
| `planning` | Inventory, decomposition, mapping, notes, density, reuse, versioning, timing. | `deck-length-and-timing-estimator` | `{"target_minutes":15}` |
| `pptx` | Editable PPTX production, themes, sections, notes, templates, compatibility, kiosk timings. | `powerpoint-deck-builder` | `{"theme":"dark","kiosk":false}` |
| `handoff` | Portable PPTX/SVG/HTML/JSON plus explicit platform handoff; no live API call. | `google-slides-deck-builder` | Publishing/permission/script rows require approval. |
| `geometry` | Canvas grid, safe areas, guides, distribution, z-order, responsive variants, object transforms. | `responsive-canvas-layout-planner` | `{"grid":12,"margin":64,"safe":48,"dx":10,"dy":0,"scale":1.0}` |
| `layout` | Composition variants and hierarchy/whitespace/reading-path adjustments. | `golden-ratio-layout-designer` | Variants include thirds, golden, modular, asymmetric, symmetric, minimal, dense, focal. |
| `typography` | Font pairing, type scale, spacing, fit audit, RTL/CJK recommendations, substitution notes. | `typeface-pairing-curator` | `{"heading":"Georgia","body":"Arial","scale":1.1}` |
| `theme` | Palette harmony, contrast audit, colorblind-safe themes, semantic tokens, variants, gradients. | `color-palette-architect` | `{"accent":"#0072B2","harmony":"triadic"}` |
| `vector` | SVG icons, shapes, paths, boolean-style compositions, export optimization, icon alt text. | `custom-icon-set-designer` | `{"icons":["arrow","check","chart","lock"]}` |
| `image` | Crop/focal point, chroma-key transparency, masks, retouching, compression, DPI, rights/credits. | `image-crop-and-focal-point-specialist` | `{"image":"universal-presentation-design/asset-example-image.png","focal_x":0.5,"focal_y":0.5}` |
| `illustration` | Procedural SVG art direction, moodboards, geometric/abstract styles, prompt plans. | `abstract-background-designer` | `{"seed":7,"elements":18}`; no generative model call. |
| `chart` | Chart selection, native basic PPTX charts, SVG/PNG charts, annotations, misleading-chart audit. | `chart-selection-advisor` | `{"chart":{"type":"line","categories":["Q1","Q2"],"series":[{"name":"Incidents","values":[18,24]}]}}` |
| `table` | Tables/matrices, pagination, conditional formatting, accessibility, table-to-chart conversion. | `long-table-pagination-specialist` | `{"page_size":12}` |
| `diagram` | Flow, swimlane, architecture, network, mind/concept maps, causal loops, cycles, pyramids, Venn, hierarchy, journey, blueprint. | `flowchart-designer` | `{"diagram":{"type":"flow","nodes":[{"id":"a","label":"Start"}],"edges":[]}}` |
| `timeline` | Timelines, roadmaps, Gantt, milestones, horizons, now-next-later, dependencies, critical path. | `critical-path-visualizer` | `{"timeline":{"mode":"gantt","events":[{"label":"A","start":"2026-01-01","end":"2026-02-01","dependencies":[]}]}}` |
| `motion` | Transition systems, timing, build storyboards, HTML/GIF previews, motion accessibility. | `transition-system-designer` | `{"transition":"fade","duration_ms":450,"stagger_ms":150}` |
| `media` | Voiceover scripts, SRT/VTT captions, chapters, credits, media plans, compatibility notes. | `voiceover-script-designer` | No recording/encoding/mixing runtime is bundled. |
| `interactive` | Hyperlinks, branching, quizzes, kiosk navigation, QR SVG, navigation usability report. | `interactive-quiz-builder` | `{"url":"https://example.com/deck"}` for QR capability. |
| `genre` | Business, sales, startup, education, and event deck structures/checklists/timing. | `board-meeting-deck-designer` | PechaKucha/Ignite enforce slide timing presets. |
| `accessibility` | Contrast, alt text, reading order, tables, plain language, cognitive load, remediation. | `accessibility-remediation-specialist` | `{"remediate":true}` |
| `localization` | String extraction, supplied translations, expansion audit, RTL, date/number formatting. | `presentation-localization-manager` | `{"locale":"fr","apply":true,"translations":{"fr":{"Title":"Titre"}}}` |
| `governance` | Template governance, naming, asset licensing, review states, metrics, adoption rules. | `enterprise-template-architect` | `{"owner":"Design Ops","version":"1.0"}` |
| `collaboration` | Comment triage, review workflow, roles, deadlines, change logs, sign-off gates. | `final-sign-off-coordinator` | Requires `--approve`; `{"approver":"human-reviewer","role":"owner"}` |
| `qa` | Visual/structure audit, fidelity notes, privacy review, recovery, delivery checklist. | `presentation-recovery-and-corruption-specialist` | Use `--kind pptx --input damaged.pptx`. |
| `ai` | Local heuristic brief/rewrite/notes/critique/tagging plus prompt sets and human-review gates. | `ai-slide-content-rewriter` | No external model is called. |
| `format` | Infographic, poster, comic, storyboard, lookbook, moodboard, handout, magazine, carousel variants. | `printable-presentation-handout-designer` | Use `--formats json,html,pdf,png,pptx`. |
| `pipeline` | JSON/CSV/OOXML pipelines, template mapping, batch thumbnails, CI quality commands. | `presentation-ci-quality-pipeline-designer` | CI command uses `audit --fail-on-major`. |

### Complete capability registry
| # | Category | Capability name | Command id | Family | Mode | Approval |
|---:|---|---|---|---|---|---|
| 1 | 1 | Audience and Outcome Framing | `audience-and-outcome-framing` | `strategy` | `local` | no |
| 2 | 1 | Presentation Brief Builder | `presentation-brief-builder` | `strategy` | `local` | no |
| 3 | 1 | Message Hierarchy Design | `message-hierarchy-design` | `strategy` | `local` | no |
| 4 | 1 | Executive Narrative Architecture | `executive-narrative-architecture` | `strategy` | `local` | no |
| 5 | 1 | Problem-Solution Storytelling | `problem-solution-storytelling` | `strategy` | `local` | no |
| 6 | 1 | Situation-Complication-Resolution Structuring | `situation-complication-resolution-structuring` | `strategy` | `local` | no |
| 7 | 1 | Persuasive Argument Sequencing | `persuasive-argument-sequencing` | `strategy` | `local` | no |
| 8 | 1 | Educational Lesson Sequencing | `educational-lesson-sequencing` | `strategy` | `local` | no |
| 9 | 1 | Product Demo Storyboarding | `product-demo-storyboarding` | `strategy` | `local` | no |
| 10 | 1 | Investor Pitch Architecture | `investor-pitch-architecture` | `strategy` | `local` | no |
| 11 | 1 | Sales Deck Narrative Design | `sales-deck-narrative-design` | `strategy` | `local` | no |
| 12 | 1 | Conference Talk Structuring | `conference-talk-structuring` | `strategy` | `local` | no |
| 13 | 1 | Workshop Flow Design | `workshop-flow-design` | `strategy` | `local` | no |
| 14 | 1 | Portfolio Story Curation | `portfolio-story-curation` | `strategy` | `local` | no |
| 15 | 1 | Case Study Narrative Builder | `case-study-narrative-builder` | `strategy` | `local` | no |
| 16 | 2 | Slide Inventory Planner | `slide-inventory-planner` | `planning` | `local` | no |
| 17 | 2 | One-Idea-per-Slide Decomposer | `one-idea-per-slide-decomposer` | `planning` | `local` | no |
| 18 | 2 | Content-to-Slide Mapper | `content-to-slide-mapper` | `planning` | `local` | no |
| 19 | 2 | Outline-to-Deck Converter | `outline-to-deck-converter` | `planning` | `local` | no |
| 20 | 2 | Slide Purpose Classifier | `slide-purpose-classifier` | `planning` | `local` | no |
| 21 | 2 | Evidence Placement Planner | `evidence-placement-planner` | `planning` | `local` | no |
| 22 | 2 | Section Divider System Designer | `section-divider-system-designer` | `planning` | `local` | no |
| 23 | 2 | Agenda and Navigation Builder | `agenda-and-navigation-builder` | `planning` | `local` | no |
| 24 | 2 | Appendix Architecture | `appendix-architecture` | `planning` | `local` | no |
| 25 | 2 | Speaker Notes Planner | `speaker-notes-planner` | `planning` | `local` | no |
| 26 | 2 | Content Density Balancer | `content-density-balancer` | `planning` | `local` | no |
| 27 | 2 | Slide Reuse and Modularization | `slide-reuse-and-modularization` | `planning` | `local` | no |
| 28 | 2 | Versioned Deck Planning | `versioned-deck-planning` | `planning` | `local` | no |
| 29 | 2 | Localization-Ready Content Planning | `localization-ready-content-planning` | `planning` | `local` | no |
| 30 | 2 | Deck Length and Timing Estimator | `deck-length-and-timing-estimator` | `planning` | `local` | no |
| 31 | 3 | PowerPoint Deck Builder | `powerpoint-deck-builder` | `pptx` | `local` | no |
| 32 | 3 | PowerPoint Slide Master Engineer | `powerpoint-slide-master-engineer` | `pptx` | `local` | no |
| 33 | 3 | PowerPoint Layout Designer | `powerpoint-layout-designer` | `pptx` | `local` | no |
| 34 | 3 | PowerPoint Theme Builder | `powerpoint-theme-builder` | `pptx` | `local` | no |
| 35 | 3 | PowerPoint Placeholder Architect | `powerpoint-placeholder-architect` | `pptx` | `local` | no |
| 36 | 3 | PowerPoint Selection Pane Organizer | `powerpoint-selection-pane-organizer` | `pptx` | `local` | no |
| 37 | 3 | PowerPoint Guides and Grid Specialist | `powerpoint-guides-and-grid-specialist` | `pptx` | `local` | no |
| 38 | 3 | PowerPoint Format Painter Workflow | `powerpoint-format-painter-workflow` | `pptx` | `local` | no |
| 39 | 3 | PowerPoint Reuse Slides Curator | `powerpoint-reuse-slides-curator` | `pptx` | `local` | no |
| 40 | 3 | PowerPoint Section Manager | `powerpoint-section-manager` | `pptx` | `local` | no |
| 41 | 3 | PowerPoint Notes and Handout Designer | `powerpoint-notes-and-handout-designer` | `pptx` | `local` | no |
| 42 | 3 | PowerPoint Export Specialist | `powerpoint-export-specialist` | `pptx` | `local` | no |
| 43 | 3 | PowerPoint Compatibility Checker | `powerpoint-compatibility-checker` | `pptx` | `local` | no |
| 44 | 3 | PowerPoint Template Packager | `powerpoint-template-packager` | `pptx` | `local` | no |
| 45 | 3 | PowerPoint Kiosk Presentation Builder | `powerpoint-kiosk-presentation-builder` | `pptx` | `local` | no |
| 46 | 4 | PowerPoint Morph Designer | `powerpoint-morph-designer` | `motion` | `local` | no |
| 47 | 4 | PowerPoint Zoom Navigation Builder | `powerpoint-zoom-navigation-builder` | `interactive` | `local` | no |
| 48 | 4 | PowerPoint Animation Sequencer | `powerpoint-animation-sequencer` | `motion` | `local` | no |
| 49 | 4 | PowerPoint Trigger Animation Engineer | `powerpoint-trigger-animation-engineer` | `motion` | `local` | no |
| 50 | 4 | PowerPoint Motion Path Designer | `powerpoint-motion-path-designer` | `motion` | `local` | no |
| 51 | 4 | PowerPoint Chart Animation Specialist | `powerpoint-chart-animation-specialist` | `motion` | `local` | no |
| 52 | 4 | PowerPoint VBA Automation Designer | `powerpoint-vba-automation-designer` | `handoff` | `handoff` | yes |
| 53 | 4 | PowerPoint Office Scripts Integrator | `powerpoint-office-scripts-integrator` | `handoff` | `handoff` | yes |
| 54 | 4 | PowerPoint Add-in Workflow Designer | `powerpoint-add-in-workflow-designer` | `handoff` | `handoff` | no |
| 55 | 4 | PowerPoint Data-Linked Deck Builder | `powerpoint-data-linked-deck-builder` | `pipeline` | `local` | no |
| 56 | 4 | PowerPoint Excel Chart Link Manager | `powerpoint-excel-chart-link-manager` | `handoff` | `handoff` | no |
| 57 | 4 | PowerPoint Custom XML and OOXML Specialist | `powerpoint-custom-xml-and-ooxml-specialist` | `pipeline` | `local` | no |
| 58 | 4 | PowerPoint Batch Formatting Automator | `powerpoint-batch-formatting-automator` | `pptx` | `local` | no |
| 59 | 4 | PowerPoint Accessibility Checker | `powerpoint-accessibility-checker` | `accessibility` | `local` | no |
| 60 | 4 | PowerPoint Repair and Recovery Specialist | `powerpoint-repair-and-recovery-specialist` | `qa` | `local` | no |
| 61 | 5 | Google Slides Deck Builder | `google-slides-deck-builder` | `handoff` | `handoff` | no |
| 62 | 5 | Google Slides Theme Builder | `google-slides-theme-builder` | `theme` | `local` | no |
| 63 | 5 | Google Slides Master Layout Designer | `google-slides-master-layout-designer` | `layout` | `local` | no |
| 64 | 5 | Google Slides Grid and Alignment Specialist | `google-slides-grid-and-alignment-specialist` | `geometry` | `local` | no |
| 65 | 5 | Google Slides Collaboration Manager | `google-slides-collaboration-manager` | `handoff` | `handoff` | no |
| 66 | 5 | Google Slides Comment Resolution Workflow | `google-slides-comment-resolution-workflow` | `handoff` | `handoff` | no |
| 67 | 5 | Google Slides Version History Curator | `google-slides-version-history-curator` | `handoff` | `handoff` | no |
| 68 | 5 | Google Slides Apps Script Automator | `google-slides-apps-script-automator` | `handoff` | `handoff` | yes |
| 69 | 5 | Google Slides Data Linking Specialist | `google-slides-data-linking-specialist` | `handoff` | `handoff` | no |
| 70 | 5 | Google Slides Publishing Specialist | `google-slides-publishing-specialist` | `handoff` | `handoff` | yes |
| 71 | 5 | Google Slides Offline Workflow Designer | `google-slides-offline-workflow-designer` | `handoff` | `handoff` | no |
| 72 | 5 | Google Slides Template Gallery Curator | `google-slides-template-gallery-curator` | `handoff` | `handoff` | no |
| 73 | 5 | Google Slides Add-on Integrator | `google-slides-add-on-integrator` | `handoff` | `handoff` | no |
| 74 | 5 | Google Slides Sharing and Permission Designer | `google-slides-sharing-and-permission-designer` | `handoff` | `handoff` | yes |
| 75 | 5 | Google Slides to PowerPoint Fidelity Specialist | `google-slides-to-powerpoint-fidelity-specialist` | `handoff` | `handoff` | no |
| 76 | 6 | Canva Presentation Builder | `canva-presentation-builder` | `handoff` | `handoff` | no |
| 77 | 6 | Canva Brand Kit Architect | `canva-brand-kit-architect` | `handoff` | `handoff` | no |
| 78 | 6 | Canva Template System Designer | `canva-template-system-designer` | `handoff` | `handoff` | no |
| 79 | 6 | Canva Magic Design Director | `canva-magic-design-director` | `handoff` | `handoff` | no |
| 80 | 6 | Canva Magic Resize Specialist | `canva-magic-resize-specialist` | `geometry` | `local` | no |
| 81 | 6 | Canva Bulk Create Workflow | `canva-bulk-create-workflow` | `pipeline` | `local` | no |
| 82 | 6 | Canva Data Autofill Designer | `canva-data-autofill-designer` | `pipeline` | `local` | no |
| 83 | 6 | Canva Elements and Asset Curator | `canva-elements-and-asset-curator` | `handoff` | `handoff` | no |
| 84 | 6 | Canva Animation Designer | `canva-animation-designer` | `motion` | `local` | no |
| 85 | 6 | Canva Present and Record Producer | `canva-present-and-record-producer` | `handoff` | `handoff` | no |
| 86 | 6 | Canva Whiteboard-to-Deck Converter | `canva-whiteboard-to-deck-converter` | `handoff` | `handoff` | no |
| 87 | 6 | Canva Team Workspace Organizer | `canva-team-workspace-organizer` | `handoff` | `handoff` | no |
| 88 | 6 | Canva Content Planner Integrator | `canva-content-planner-integrator` | `handoff` | `handoff` | no |
| 89 | 6 | Canva Export and Print Specialist | `canva-export-and-print-specialist` | `handoff` | `handoff` | no |
| 90 | 6 | Canva Cross-Platform Handoff Specialist | `canva-cross-platform-handoff-specialist` | `handoff` | `handoff` | no |
| 91 | 7 | Adobe Illustrator Vector Asset Designer | `adobe-illustrator-vector-asset-designer` | `vector` | `local` | no |
| 92 | 7 | Adobe Photoshop Image Compositor | `adobe-photoshop-image-compositor` | `image` | `local` | no |
| 93 | 7 | Adobe InDesign Presentation Document Designer | `adobe-indesign-presentation-document-designer` | `handoff` | `handoff` | no |
| 94 | 7 | Adobe Express Presentation Builder | `adobe-express-presentation-builder` | `handoff` | `handoff` | no |
| 95 | 7 | Adobe Firefly Visual Ideation Director | `adobe-firefly-visual-ideation-director` | `handoff` | `handoff` | no |
| 96 | 7 | Adobe Acrobat PDF Presentation Specialist | `adobe-acrobat-pdf-presentation-specialist` | `handoff` | `handoff` | no |
| 97 | 7 | Adobe After Effects Motion Graphics Designer | `adobe-after-effects-motion-graphics-designer` | `handoff` | `handoff` | no |
| 98 | 7 | Adobe Premiere Pro Presentation Video Editor | `adobe-premiere-pro-presentation-video-editor` | `handoff` | `handoff` | no |
| 99 | 7 | Adobe Audition Presentation Audio Editor | `adobe-audition-presentation-audio-editor` | `handoff` | `handoff` | no |
| 100 | 7 | Adobe Bridge Asset Librarian | `adobe-bridge-asset-librarian` | `handoff` | `handoff` | no |
| 101 | 7 | Adobe Fonts Typography Curator | `adobe-fonts-typography-curator` | `typography` | `local` | no |
| 102 | 7 | Adobe Color Palette Designer | `adobe-color-palette-designer` | `theme` | `local` | no |
| 103 | 7 | Adobe Stock Asset Researcher | `adobe-stock-asset-researcher` | `handoff` | `handoff` | no |
| 104 | 7 | Creative Cloud Libraries Manager | `creative-cloud-libraries-manager` | `handoff` | `handoff` | no |
| 105 | 7 | Adobe-to-Slide Handoff Specialist | `adobe-to-slide-handoff-specialist` | `handoff` | `handoff` | no |
| 106 | 8 | Apple Keynote Deck Designer | `apple-keynote-deck-designer` | `handoff` | `handoff` | no |
| 107 | 8 | Prezi Spatial Story Designer | `prezi-spatial-story-designer` | `handoff` | `handoff` | no |
| 108 | 8 | Pitch Collaborative Deck Builder | `pitch-collaborative-deck-builder` | `handoff` | `handoff` | no |
| 109 | 8 | Figma Slides Designer | `figma-slides-designer` | `handoff` | `handoff` | no |
| 110 | 8 | Beautiful.ai Smart Deck Designer | `beautiful-ai-smart-deck-designer` | `handoff` | `handoff` | no |
| 111 | 8 | Gamma Presentation Creator | `gamma-presentation-creator` | `handoff` | `handoff` | no |
| 112 | 8 | Visme Presentation Designer | `visme-presentation-designer` | `handoff` | `handoff` | no |
| 113 | 8 | Genially Interactive Presentation Builder | `genially-interactive-presentation-builder` | `handoff` | `handoff` | no |
| 114 | 8 | Tome Narrative Presentation Designer | `tome-narrative-presentation-designer` | `handoff` | `handoff` | no |
| 115 | 8 | Zoho Show Deck Builder | `zoho-show-deck-builder` | `handoff` | `handoff` | no |
| 116 | 8 | LibreOffice Impress Specialist | `libreoffice-impress-specialist` | `handoff` | `handoff` | no |
| 117 | 8 | WPS Presentation Designer | `wps-presentation-designer` | `handoff` | `handoff` | no |
| 118 | 8 | Haiku Deck Visual Storyteller | `haiku-deck-visual-storyteller` | `handoff` | `handoff` | no |
| 119 | 8 | Mentimeter Interactive Presenter | `mentimeter-interactive-presenter` | `handoff` | `handoff` | no |
| 120 | 8 | Miro Presentation Mode Designer | `miro-presentation-mode-designer` | `handoff` | `handoff` | no |
| 121 | 9 | Coordinate-Based Object Positioner | `coordinate-based-object-positioner` | `geometry` | `local` | no |
| 122 | 9 | Grid System Architect | `grid-system-architect` | `geometry` | `local` | no |
| 123 | 9 | Guide and Ruler Manager | `guide-and-ruler-manager` | `geometry` | `local` | no |
| 124 | 9 | Smart Guide Alignment Specialist | `smart-guide-alignment-specialist` | `geometry` | `local` | no |
| 125 | 9 | Baseline Grid Designer | `baseline-grid-designer` | `geometry` | `local` | no |
| 126 | 9 | Optical Alignment Reviewer | `optical-alignment-reviewer` | `geometry` | `local` | no |
| 127 | 9 | Spacing Rhythm Calibrator | `spacing-rhythm-calibrator` | `geometry` | `local` | no |
| 128 | 9 | Margin and Safe-Area Designer | `margin-and-safe-area-designer` | `geometry` | `local` | no |
| 129 | 9 | Object Distribution Specialist | `object-distribution-specialist` | `geometry` | `local` | no |
| 130 | 9 | Layer and Z-Order Manager | `layer-and-z-order-manager` | `geometry` | `local` | no |
| 131 | 9 | Anchor and Constraint Designer | `anchor-and-constraint-designer` | `geometry` | `local` | no |
| 132 | 9 | Responsive Canvas Layout Planner | `responsive-canvas-layout-planner` | `geometry` | `local` | no |
| 133 | 9 | Pixel-Perfect Placement Reviewer | `pixel-perfect-placement-reviewer` | `geometry` | `local` | no |
| 134 | 9 | Snap Behavior Specialist | `snap-behavior-specialist` | `geometry` | `local` | no |
| 135 | 9 | Multi-Object Transformation Controller | `multi-object-transformation-controller` | `geometry` | `local` | no |
| 136 | 10 | Visual Hierarchy Designer | `visual-hierarchy-designer` | `layout` | `local` | no |
| 137 | 10 | Rule-of-Thirds Composer | `rule-of-thirds-composer` | `layout` | `local` | no |
| 138 | 10 | Golden Ratio Layout Designer | `golden-ratio-layout-designer` | `layout` | `local` | no |
| 139 | 10 | Modular Grid Layout Designer | `modular-grid-layout-designer` | `layout` | `local` | no |
| 140 | 10 | Asymmetrical Balance Designer | `asymmetrical-balance-designer` | `layout` | `local` | no |
| 141 | 10 | Symmetrical Balance Designer | `symmetrical-balance-designer` | `layout` | `local` | no |
| 142 | 10 | Whitespace Strategist | `whitespace-strategist` | `layout` | `local` | no |
| 143 | 10 | Focal Point Designer | `focal-point-designer` | `layout` | `local` | no |
| 144 | 10 | Reading Path Engineer | `reading-path-engineer` | `layout` | `local` | no |
| 145 | 10 | Proximity and Grouping Specialist | `proximity-and-grouping-specialist` | `layout` | `local` | no |
| 146 | 10 | Repetition and Rhythm Designer | `repetition-and-rhythm-designer` | `layout` | `local` | no |
| 147 | 10 | Contrast-Led Composition Designer | `contrast-led-composition-designer` | `layout` | `local` | no |
| 148 | 10 | Edge Tension Reviewer | `edge-tension-reviewer` | `layout` | `local` | no |
| 149 | 10 | Dense Information Layout Designer | `dense-information-layout-designer` | `layout` | `local` | no |
| 150 | 10 | Minimalist Layout Designer | `minimalist-layout-designer` | `layout` | `local` | no |
| 151 | 11 | Typeface Pairing Curator | `typeface-pairing-curator` | `typography` | `local` | no |
| 152 | 11 | Presentation Typography System Designer | `presentation-typography-system-designer` | `typography` | `local` | no |
| 153 | 11 | Font Size Scale Architect | `font-size-scale-architect` | `typography` | `local` | no |
| 154 | 11 | Heading Hierarchy Designer | `heading-hierarchy-designer` | `typography` | `local` | no |
| 155 | 11 | Paragraph and Line Spacing Calibrator | `paragraph-and-line-spacing-calibrator` | `typography` | `local` | no |
| 156 | 11 | Kerning and Tracking Reviewer | `kerning-and-tracking-reviewer` | `typography` | `local` | no |
| 157 | 11 | Text Box Fit Specialist | `text-box-fit-specialist` | `typography` | `local` | no |
| 158 | 11 | Widow and Orphan Controller | `widow-and-orphan-controller` | `typography` | `local` | no |
| 159 | 11 | Text Emphasis System Designer | `text-emphasis-system-designer` | `typography` | `local` | no |
| 160 | 11 | Numeral and Data Typography Specialist | `numeral-and-data-typography-specialist` | `typography` | `local` | no |
| 161 | 11 | Multilingual Typography Designer | `multilingual-typography-designer` | `typography` | `local` | no |
| 162 | 11 | Right-to-Left Layout Specialist | `right-to-left-layout-specialist` | `typography` | `local` | no |
| 163 | 11 | CJK Presentation Typography Specialist | `cjk-presentation-typography-specialist` | `typography` | `local` | no |
| 164 | 11 | Accessible Typography Reviewer | `accessible-typography-reviewer` | `typography` | `local` | no |
| 165 | 11 | Font Embedding and Substitution Specialist | `font-embedding-and-substitution-specialist` | `typography` | `local` | no |
| 166 | 12 | Color Palette Architect | `color-palette-architect` | `theme` | `local` | no |
| 167 | 12 | Brand Theme Translator | `brand-theme-translator` | `theme` | `local` | no |
| 168 | 12 | Theme Token Designer | `theme-token-designer` | `theme` | `local` | no |
| 169 | 12 | Light and Dark Theme Designer | `light-and-dark-theme-designer` | `theme` | `local` | no |
| 170 | 12 | Color Harmony Specialist | `color-harmony-specialist` | `theme` | `local` | no |
| 171 | 12 | Contrast Ratio Auditor | `contrast-ratio-auditor` | `theme` | `local` | no |
| 172 | 12 | Color-Blind Safe Palette Designer | `color-blind-safe-palette-designer` | `theme` | `local` | no |
| 173 | 12 | Corporate Brand Compliance Reviewer | `corporate-brand-compliance-reviewer` | `theme` | `local` | no |
| 174 | 12 | Campaign Visual System Designer | `campaign-visual-system-designer` | `theme` | `local` | no |
| 175 | 12 | Gradient and Duotone Designer | `gradient-and-duotone-designer` | `theme` | `local` | no |
| 176 | 12 | Accent Color Allocation Specialist | `accent-color-allocation-specialist` | `theme` | `local` | no |
| 177 | 12 | Semantic Color System Designer | `semantic-color-system-designer` | `theme` | `local` | no |
| 178 | 12 | Cross-Platform Theme Mapper | `cross-platform-theme-mapper` | `theme` | `local` | no |
| 179 | 12 | Seasonal Theme Designer | `seasonal-theme-designer` | `theme` | `local` | no |
| 180 | 12 | Theme Variation Generator | `theme-variation-generator` | `theme` | `local` | no |
| 181 | 13 | Shape Language Designer | `shape-language-designer` | `vector` | `local` | no |
| 182 | 13 | Custom Icon Set Designer | `custom-icon-set-designer` | `vector` | `local` | no |
| 183 | 13 | SVG Asset Specialist | `svg-asset-specialist` | `vector` | `local` | no |
| 184 | 13 | Vector Path Editor | `vector-path-editor` | `vector` | `local` | no |
| 185 | 13 | Boolean Shape Operations Specialist | `boolean-shape-operations-specialist` | `vector` | `local` | no |
| 186 | 13 | Pictogram System Designer | `pictogram-system-designer` | `vector` | `local` | no |
| 187 | 13 | Diagram Shape Library Curator | `diagram-shape-library-curator` | `vector` | `local` | no |
| 188 | 13 | Line and Stroke Style Designer | `line-and-stroke-style-designer` | `vector` | `local` | no |
| 189 | 13 | Corner Radius System Designer | `corner-radius-system-designer` | `vector` | `local` | no |
| 190 | 13 | Badge and Label Designer | `badge-and-label-designer` | `vector` | `local` | no |
| 191 | 13 | Callout and Annotation Designer | `callout-and-annotation-designer` | `vector` | `local` | no |
| 192 | 13 | Isometric Vector Designer | `isometric-vector-designer` | `vector` | `local` | no |
| 193 | 13 | Flat Illustration Designer | `flat-illustration-designer` | `vector` | `local` | no |
| 194 | 13 | Vector Export Optimizer | `vector-export-optimizer` | `vector` | `local` | no |
| 195 | 13 | Icon Accessibility Reviewer | `icon-accessibility-reviewer` | `vector` | `local` | no |
| 196 | 14 | Presentation Image Art Director | `presentation-image-art-director` | `image` | `local` | no |
| 197 | 14 | Photo Selection Curator | `photo-selection-curator` | `image` | `local` | no |
| 198 | 14 | Image Crop and Focal Point Specialist | `image-crop-and-focal-point-specialist` | `image` | `local` | no |
| 199 | 14 | Background Removal Specialist | `background-removal-specialist` | `image` | `local` | no |
| 200 | 14 | Image Masking Designer | `image-masking-designer` | `image` | `local` | no |
| 201 | 14 | Photo Retouching Specialist | `photo-retouching-specialist` | `image` | `local` | no |
| 202 | 14 | Image Color Grading Designer | `image-color-grading-designer` | `image` | `local` | no |
| 203 | 14 | Composite Image Builder | `composite-image-builder` | `image` | `local` | no |
| 204 | 14 | Stock Image Licensing Coordinator | `stock-image-licensing-coordinator` | `image` | `local` | no |
| 205 | 14 | Image Resolution and DPI Reviewer | `image-resolution-and-dpi-reviewer` | `image` | `local` | no |
| 206 | 14 | Transparent Asset Optimizer | `transparent-asset-optimizer` | `image` | `local` | no |
| 207 | 14 | Image Compression Specialist | `image-compression-specialist` | `image` | `local` | no |
| 208 | 14 | Before-and-After Visual Designer | `before-and-after-visual-designer` | `image` | `local` | no |
| 209 | 14 | Product Mockup Compositor | `product-mockup-compositor` | `image` | `local` | no |
| 210 | 14 | Photo Caption and Credit Manager | `photo-caption-and-credit-manager` | `image` | `local` | no |
| 211 | 15 | Editorial Illustration Director | `editorial-illustration-director` | `illustration` | `local` | no |
| 212 | 15 | Concept Art Planner | `concept-art-planner` | `illustration` | `local` | no |
| 213 | 15 | Character Illustration Designer | `character-illustration-designer` | `illustration` | `local` | no |
| 214 | 15 | Scene Composition Designer | `scene-composition-designer` | `illustration` | `local` | no |
| 215 | 15 | Collage Art Designer | `collage-art-designer` | `illustration` | `local` | no |
| 216 | 15 | Paper-Cut Style Designer | `paper-cut-style-designer` | `illustration` | `local` | no |
| 217 | 15 | 3D Illustration Art Director | `3d-illustration-art-director` | `illustration` | `local` | no |
| 218 | 15 | Hand-Drawn Visual Style Designer | `hand-drawn-visual-style-designer` | `illustration` | `local` | no |
| 219 | 15 | Abstract Background Designer | `abstract-background-designer` | `illustration` | `local` | no |
| 220 | 15 | Geometric Art Designer | `geometric-art-designer` | `illustration` | `local` | no |
| 221 | 15 | Infographic Illustration Designer | `infographic-illustration-designer` | `illustration` | `local` | no |
| 222 | 15 | Technical Illustration Designer | `technical-illustration-designer` | `illustration` | `local` | no |
| 223 | 15 | Moodboard Curator | `moodboard-curator` | `illustration` | `local` | no |
| 224 | 15 | Style Consistency Director | `style-consistency-director` | `illustration` | `local` | no |
| 225 | 15 | Creative Variation Generator | `creative-variation-generator` | `illustration` | `local` | no |
| 226 | 16 | Chart Selection Advisor | `chart-selection-advisor` | `chart` | `local` | no |
| 227 | 16 | Data-to-Story Visualizer | `data-to-story-visualizer` | `chart` | `local` | no |
| 228 | 16 | Executive Dashboard Slide Designer | `executive-dashboard-slide-designer` | `chart` | `local` | no |
| 229 | 16 | Bar and Column Chart Designer | `bar-and-column-chart-designer` | `chart` | `local` | no |
| 230 | 16 | Line and Area Chart Designer | `line-and-area-chart-designer` | `chart` | `local` | no |
| 231 | 16 | Scatter and Bubble Plot Designer | `scatter-and-bubble-plot-designer` | `chart` | `local` | no |
| 232 | 16 | Waterfall Chart Designer | `waterfall-chart-designer` | `chart` | `local` | no |
| 233 | 16 | Funnel Chart Designer | `funnel-chart-designer` | `chart` | `local` | no |
| 234 | 16 | Heatmap Designer | `heatmap-designer` | `chart` | `local` | no |
| 235 | 16 | Map Visualization Designer | `map-visualization-designer` | `chart` | `local` | no |
| 236 | 16 | Small Multiples Designer | `small-multiples-designer` | `chart` | `local` | no |
| 237 | 16 | KPI Scorecard Designer | `kpi-scorecard-designer` | `chart` | `local` | no |
| 238 | 16 | Uncertainty Visualization Specialist | `uncertainty-visualization-specialist` | `chart` | `local` | no |
| 239 | 16 | Chart Annotation Specialist | `chart-annotation-specialist` | `chart` | `local` | no |
| 240 | 16 | Misleading Chart Auditor | `misleading-chart-auditor` | `chart` | `local` | no |
| 241 | 17 | Presentation Table Designer | `presentation-table-designer` | `table` | `local` | no |
| 242 | 17 | Comparison Matrix Builder | `comparison-matrix-builder` | `table` | `local` | no |
| 243 | 17 | Feature Comparison Designer | `feature-comparison-designer` | `table` | `local` | no |
| 244 | 17 | Pricing Table Designer | `pricing-table-designer` | `table` | `local` | no |
| 245 | 17 | RACI Matrix Designer | `raci-matrix-designer` | `table` | `local` | no |
| 246 | 17 | Risk Matrix Designer | `risk-matrix-designer` | `table` | `local` | no |
| 247 | 17 | Prioritization Matrix Designer | `prioritization-matrix-designer` | `table` | `local` | no |
| 248 | 17 | Decision Matrix Builder | `decision-matrix-builder` | `table` | `local` | no |
| 249 | 17 | SWOT Layout Designer | `swot-layout-designer` | `table` | `local` | no |
| 250 | 17 | Timeline Table Designer | `timeline-table-designer` | `table` | `local` | no |
| 251 | 17 | Financial Statement Slide Formatter | `financial-statement-slide-formatter` | `table` | `local` | no |
| 252 | 17 | Long Table Pagination Specialist | `long-table-pagination-specialist` | `table` | `local` | no |
| 253 | 17 | Conditional Formatting Designer | `conditional-formatting-designer` | `table` | `local` | no |
| 254 | 17 | Table Accessibility Reviewer | `table-accessibility-reviewer` | `table` | `local` | no |
| 255 | 17 | Table-to-Visual Converter | `table-to-visual-converter` | `table` | `local` | no |
| 256 | 18 | Flowchart Designer | `flowchart-designer` | `diagram` | `local` | no |
| 257 | 18 | Process Map Architect | `process-map-architect` | `diagram` | `local` | no |
| 258 | 18 | Swimlane Diagram Designer | `swimlane-diagram-designer` | `diagram` | `local` | no |
| 259 | 18 | System Architecture Diagram Designer | `system-architecture-diagram-designer` | `diagram` | `local` | no |
| 260 | 18 | Network Diagram Designer | `network-diagram-designer` | `diagram` | `local` | no |
| 261 | 18 | Mind Map Designer | `mind-map-designer` | `diagram` | `local` | no |
| 262 | 18 | Concept Map Designer | `concept-map-designer` | `diagram` | `local` | no |
| 263 | 18 | Causal Loop Diagram Designer | `causal-loop-diagram-designer` | `diagram` | `local` | no |
| 264 | 18 | Funnel Process Designer | `funnel-process-designer` | `diagram` | `local` | no |
| 265 | 18 | Cycle Diagram Designer | `cycle-diagram-designer` | `diagram` | `local` | no |
| 266 | 18 | Pyramid Diagram Designer | `pyramid-diagram-designer` | `diagram` | `local` | no |
| 267 | 18 | Venn Diagram Designer | `venn-diagram-designer` | `diagram` | `local` | no |
| 268 | 18 | Hierarchy and Org Chart Designer | `hierarchy-and-org-chart-designer` | `diagram` | `local` | no |
| 269 | 18 | Journey Map Designer | `journey-map-designer` | `diagram` | `local` | no |
| 270 | 18 | Service Blueprint Designer | `service-blueprint-designer` | `diagram` | `local` | no |
| 271 | 19 | Project Timeline Designer | `project-timeline-designer` | `timeline` | `local` | no |
| 272 | 19 | Product Roadmap Designer | `product-roadmap-designer` | `timeline` | `local` | no |
| 273 | 19 | Milestone Map Designer | `milestone-map-designer` | `timeline` | `local` | no |
| 274 | 19 | Gantt Slide Designer | `gantt-slide-designer` | `timeline` | `local` | no |
| 275 | 19 | Release Plan Visualizer | `release-plan-visualizer` | `timeline` | `local` | no |
| 276 | 19 | Transformation Roadmap Designer | `transformation-roadmap-designer` | `timeline` | `local` | no |
| 277 | 19 | Strategic Horizon Map Designer | `strategic-horizon-map-designer` | `timeline` | `local` | no |
| 278 | 19 | Quarterly Plan Designer | `quarterly-plan-designer` | `timeline` | `local` | no |
| 279 | 19 | Event Program Timeline Designer | `event-program-timeline-designer` | `timeline` | `local` | no |
| 280 | 19 | Historical Timeline Designer | `historical-timeline-designer` | `timeline` | `local` | no |
| 281 | 19 | Dependency Map Designer | `dependency-map-designer` | `timeline` | `local` | no |
| 282 | 19 | Critical Path Visualizer | `critical-path-visualizer` | `timeline` | `local` | no |
| 283 | 19 | Now-Next-Later Designer | `now-next-later-designer` | `timeline` | `local` | no |
| 284 | 19 | Phased Rollout Designer | `phased-rollout-designer` | `timeline` | `local` | no |
| 285 | 19 | Roadmap Status Update Designer | `roadmap-status-update-designer` | `timeline` | `local` | no |
| 286 | 20 | Transition System Designer | `transition-system-designer` | `motion` | `local` | no |
| 287 | 20 | Motion Design Storyboarder | `motion-design-storyboarder` | `motion` | `local` | no |
| 288 | 20 | Animation Timing Calibrator | `animation-timing-calibrator` | `motion` | `local` | no |
| 289 | 20 | Build Sequence Designer | `build-sequence-designer` | `motion` | `local` | no |
| 290 | 20 | Emphasis Animation Specialist | `emphasis-animation-specialist` | `motion` | `local` | no |
| 291 | 20 | Entrance and Exit Animation Designer | `entrance-and-exit-animation-designer` | `motion` | `local` | no |
| 292 | 20 | Looping Animation Designer | `looping-animation-designer` | `motion` | `local` | no |
| 293 | 20 | Cinematic Slide Transition Designer | `cinematic-slide-transition-designer` | `motion` | `local` | no |
| 294 | 20 | Microinteraction Designer | `microinteraction-designer` | `motion` | `local` | no |
| 295 | 20 | Animated Diagram Builder | `animated-diagram-builder` | `motion` | `local` | no |
| 296 | 20 | Animated Chart Builder | `animated-chart-builder` | `motion` | `local` | no |
| 297 | 20 | GIF Integration Specialist | `gif-integration-specialist` | `motion` | `local` | no |
| 298 | 20 | Lottie Asset Integrator | `lottie-asset-integrator` | `motion` | `local` | no |
| 299 | 20 | Motion Accessibility Reviewer | `motion-accessibility-reviewer` | `motion` | `local` | no |
| 300 | 20 | Performance-Safe Animation Optimizer | `performance-safe-animation-optimizer` | `motion` | `local` | no |
| 301 | 21 | Presentation Video Producer | `presentation-video-producer` | `media` | `plan` | no |
| 302 | 21 | Screen Recording Director | `screen-recording-director` | `media` | `plan` | no |
| 303 | 21 | Voiceover Script Designer | `voiceover-script-designer` | `media` | `plan` | no |
| 304 | 21 | Voiceover Recording Specialist | `voiceover-recording-specialist` | `media` | `plan` | no |
| 305 | 21 | Slide-to-Video Exporter | `slide-to-video-exporter` | `media` | `plan` | no |
| 306 | 21 | Picture-in-Picture Layout Designer | `picture-in-picture-layout-designer` | `media` | `plan` | no |
| 307 | 21 | Subtitle and Caption Producer | `subtitle-and-caption-producer` | `media` | `plan` | no |
| 308 | 21 | Audio Ducking and Mixing Specialist | `audio-ducking-and-mixing-specialist` | `media` | `plan` | no |
| 309 | 21 | Background Music Curator | `background-music-curator` | `media` | `plan` | no |
| 310 | 21 | Video Compression Specialist | `video-compression-specialist` | `media` | `plan` | no |
| 311 | 21 | Embedded Media Compatibility Reviewer | `embedded-media-compatibility-reviewer` | `media` | `plan` | no |
| 312 | 21 | Interactive Video Chapter Designer | `interactive-video-chapter-designer` | `media` | `plan` | no |
| 313 | 21 | Webcam Presentation Coach | `webcam-presentation-coach` | `media` | `plan` | no |
| 314 | 21 | Livestream Visual Package Designer | `livestream-visual-package-designer` | `media` | `plan` | no |
| 315 | 21 | Multimedia Rights and Credits Manager | `multimedia-rights-and-credits-manager` | `media` | `plan` | no |
| 316 | 22 | Hyperlinked Deck Architect | `hyperlinked-deck-architect` | `interactive` | `local` | no |
| 317 | 22 | Branching Scenario Designer | `branching-scenario-designer` | `interactive` | `local` | no |
| 318 | 22 | Interactive Quiz Builder | `interactive-quiz-builder` | `interactive` | `local` | no |
| 319 | 22 | Clickable Prototype Designer | `clickable-prototype-designer` | `interactive` | `local` | no |
| 320 | 22 | Escape-Room Presentation Designer | `escape-room-presentation-designer` | `interactive` | `local` | no |
| 321 | 22 | Choose-Your-Path Story Designer | `choose-your-path-story-designer` | `interactive` | `local` | no |
| 322 | 22 | Interactive Dashboard Navigator | `interactive-dashboard-navigator` | `interactive` | `local` | no |
| 323 | 22 | Presentation Game Designer | `presentation-game-designer` | `interactive` | `local` | no |
| 324 | 22 | Touchscreen Kiosk Designer | `touchscreen-kiosk-designer` | `interactive` | `local` | no |
| 325 | 22 | QR-Connected Presentation Designer | `qr-connected-presentation-designer` | `interactive` | `local` | no |
| 326 | 22 | Augmented Reality Presentation Planner | `augmented-reality-presentation-planner` | `interactive` | `local` | no |
| 327 | 22 | Virtual Reality Storyboard Designer | `virtual-reality-storyboard-designer` | `interactive` | `local` | no |
| 328 | 22 | 360-Degree Presentation Designer | `360-degree-presentation-designer` | `interactive` | `local` | no |
| 329 | 22 | Embedded Web Content Integrator | `embedded-web-content-integrator` | `interactive` | `local` | no |
| 330 | 22 | Nonlinear Navigation Usability Tester | `nonlinear-navigation-usability-tester` | `interactive` | `local` | no |
| 331 | 23 | Executive Briefing Designer | `executive-briefing-designer` | `genre` | `local` | no |
| 332 | 23 | Board Meeting Deck Designer | `board-meeting-deck-designer` | `genre` | `local` | no |
| 333 | 23 | Quarterly Business Review Designer | `quarterly-business-review-designer` | `genre` | `local` | no |
| 334 | 23 | Annual Report Presentation Designer | `annual-report-presentation-designer` | `genre` | `local` | no |
| 335 | 23 | Strategy Deck Designer | `strategy-deck-designer` | `genre` | `local` | no |
| 336 | 23 | Consulting-Style Deck Designer | `consulting-style-deck-designer` | `genre` | `local` | no |
| 337 | 23 | Operations Review Designer | `operations-review-designer` | `genre` | `local` | no |
| 338 | 23 | Change Management Deck Designer | `change-management-deck-designer` | `genre` | `local` | no |
| 339 | 23 | Policy Presentation Designer | `policy-presentation-designer` | `genre` | `local` | no |
| 340 | 23 | Proposal Deck Designer | `proposal-deck-designer` | `genre` | `local` | no |
| 341 | 23 | Internal Communications Deck Designer | `internal-communications-deck-designer` | `genre` | `local` | no |
| 342 | 23 | Town Hall Presentation Designer | `town-hall-presentation-designer` | `genre` | `local` | no |
| 343 | 23 | Crisis Communication Deck Designer | `crisis-communication-deck-designer` | `genre` | `local` | no |
| 344 | 23 | Partnership Deck Designer | `partnership-deck-designer` | `genre` | `local` | no |
| 345 | 23 | Business Case Presentation Designer | `business-case-presentation-designer` | `genre` | `local` | no |
| 346 | 24 | Sales Pitch Deck Designer | `sales-pitch-deck-designer` | `genre` | `local` | no |
| 347 | 24 | Marketing Campaign Deck Designer | `marketing-campaign-deck-designer` | `genre` | `local` | no |
| 348 | 24 | Brand Guidelines Presentation Designer | `brand-guidelines-presentation-designer` | `genre` | `local` | no |
| 349 | 24 | Go-to-Market Deck Designer | `go-to-market-deck-designer` | `genre` | `local` | no |
| 350 | 24 | Product Launch Presentation Designer | `product-launch-presentation-designer` | `genre` | `local` | no |
| 351 | 24 | Customer Segmentation Deck Designer | `customer-segmentation-deck-designer` | `genre` | `local` | no |
| 352 | 24 | Buyer Persona Presentation Designer | `buyer-persona-presentation-designer` | `genre` | `local` | no |
| 353 | 24 | Competitive Battlecard Designer | `competitive-battlecard-designer` | `genre` | `local` | no |
| 354 | 24 | Case Study Sales Deck Designer | `case-study-sales-deck-designer` | `genre` | `local` | no |
| 355 | 24 | Sponsorship Deck Designer | `sponsorship-deck-designer` | `genre` | `local` | no |
| 356 | 24 | Media Kit Presentation Designer | `media-kit-presentation-designer` | `genre` | `local` | no |
| 357 | 24 | Event Promotion Deck Designer | `event-promotion-deck-designer` | `genre` | `local` | no |
| 358 | 24 | Social Media Carousel Designer | `social-media-carousel-designer` | `format` | `local` | no |
| 359 | 24 | Webinar Deck Designer | `webinar-deck-designer` | `genre` | `local` | no |
| 360 | 24 | Lead Magnet Presentation Designer | `lead-magnet-presentation-designer` | `genre` | `local` | no |
| 361 | 25 | Investor Pitch Deck Designer | `investor-pitch-deck-designer` | `genre` | `local` | no |
| 362 | 25 | Startup One-Pager Visual Designer | `startup-one-pager-visual-designer` | `format` | `local` | no |
| 363 | 25 | Product Vision Deck Designer | `product-vision-deck-designer` | `genre` | `local` | no |
| 364 | 25 | Product Requirements Presentation Designer | `product-requirements-presentation-designer` | `genre` | `local` | no |
| 365 | 25 | Design Sprint Deck Designer | `design-sprint-deck-designer` | `genre` | `local` | no |
| 366 | 25 | Hackathon Pitch Designer | `hackathon-pitch-designer` | `genre` | `local` | no |
| 367 | 25 | Innovation Portfolio Deck Designer | `innovation-portfolio-deck-designer` | `genre` | `local` | no |
| 368 | 25 | Business Model Canvas Presenter | `business-model-canvas-presenter` | `genre` | `local` | no |
| 369 | 25 | Lean Canvas Presentation Designer | `lean-canvas-presentation-designer` | `genre` | `local` | no |
| 370 | 25 | Product Demo Deck Designer | `product-demo-deck-designer` | `genre` | `local` | no |
| 371 | 25 | Roadshow Deck Designer | `roadshow-deck-designer` | `genre` | `local` | no |
| 372 | 25 | Fundraising Data Room Deck Curator | `fundraising-data-room-deck-curator` | `genre` | `local` | no |
| 373 | 25 | Market Sizing Slide Designer | `market-sizing-slide-designer` | `genre` | `local` | no |
| 374 | 25 | Traction Metrics Slide Designer | `traction-metrics-slide-designer` | `genre` | `local` | no |
| 375 | 25 | Pitch Competition Deck Optimizer | `pitch-competition-deck-optimizer` | `genre` | `local` | no |
| 376 | 26 | Lesson Slide Designer | `lesson-slide-designer` | `genre` | `local` | no |
| 377 | 26 | Lecture Deck Designer | `lecture-deck-designer` | `genre` | `local` | no |
| 378 | 26 | Student Presentation Coach | `student-presentation-coach` | `genre` | `local` | no |
| 379 | 26 | Thesis Defense Deck Designer | `thesis-defense-deck-designer` | `genre` | `local` | no |
| 380 | 26 | Research Conference Poster-to-Deck Converter | `research-conference-poster-to-deck-converter` | `format` | `local` | no |
| 381 | 26 | Scientific Presentation Designer | `scientific-presentation-designer` | `genre` | `local` | no |
| 382 | 26 | Medical Education Deck Designer | `medical-education-deck-designer` | `genre` | `local` | no |
| 383 | 26 | eLearning Slide Designer | `elearning-slide-designer` | `genre` | `local` | no |
| 384 | 26 | Microlearning Deck Designer | `microlearning-deck-designer` | `genre` | `local` | no |
| 385 | 26 | Training Facilitator Deck Designer | `training-facilitator-deck-designer` | `genre` | `local` | no |
| 386 | 26 | Assessment and Quiz Slide Designer | `assessment-and-quiz-slide-designer` | `genre` | `local` | no |
| 387 | 26 | Classroom Game Slide Designer | `classroom-game-slide-designer` | `genre` | `local` | no |
| 388 | 26 | Rubric-Aligned Presentation Designer | `rubric-aligned-presentation-designer` | `genre` | `local` | no |
| 389 | 26 | Citation and Reference Slide Manager | `citation-and-reference-slide-manager` | `genre` | `local` | no |
| 390 | 26 | Accessible Learning Materials Designer | `accessible-learning-materials-designer` | `genre` | `local` | no |
| 391 | 27 | Keynote Presentation Designer | `keynote-presentation-designer` | `genre` | `local` | no |
| 392 | 27 | Conference Session Deck Designer | `conference-session-deck-designer` | `genre` | `local` | no |
| 393 | 27 | Award Ceremony Visual Designer | `award-ceremony-visual-designer` | `genre` | `local` | no |
| 394 | 27 | Wedding Slideshow Designer | `wedding-slideshow-designer` | `genre` | `local` | no |
| 395 | 27 | Memorial Tribute Presentation Designer | `memorial-tribute-presentation-designer` | `genre` | `local` | no |
| 396 | 27 | Church and Worship Presentation Designer | `church-and-worship-presentation-designer` | `genre` | `local` | no |
| 397 | 27 | Community Event Presentation Designer | `community-event-presentation-designer` | `genre` | `local` | no |
| 398 | 27 | PechaKucha Deck Designer | `pechakucha-deck-designer` | `genre` | `local` | no |
| 399 | 27 | Ignite Talk Designer | `ignite-talk-designer` | `genre` | `local` | no |
| 400 | 27 | TED-Style Talk Visual Designer | `ted-style-talk-visual-designer` | `genre` | `local` | no |
| 401 | 27 | Panel Discussion Visual Producer | `panel-discussion-visual-producer` | `genre` | `local` | no |
| 402 | 27 | Master of Ceremonies Visual Cue Designer | `master-of-ceremonies-visual-cue-designer` | `genre` | `local` | no |
| 403 | 27 | Digital Signage Presentation Designer | `digital-signage-presentation-designer` | `genre` | `local` | no |
| 404 | 27 | Stage Screen Content Designer | `stage-screen-content-designer` | `genre` | `local` | no |
| 405 | 27 | Speaker Confidence and Rehearsal Planner | `speaker-confidence-and-rehearsal-planner` | `genre` | `local` | no |
| 406 | 28 | WCAG Presentation Accessibility Auditor | `wcag-presentation-accessibility-auditor` | `accessibility` | `local` | no |
| 407 | 28 | Reading Order Specialist | `reading-order-specialist` | `accessibility` | `local` | no |
| 408 | 28 | Alt Text Writer | `alt-text-writer` | `accessibility` | `local` | no |
| 409 | 28 | Accessible Color and Contrast Reviewer | `accessible-color-and-contrast-reviewer` | `accessibility` | `local` | no |
| 410 | 28 | Keyboard Navigation Designer | `keyboard-navigation-designer` | `accessibility` | `local` | no |
| 411 | 28 | Screen Reader Compatibility Specialist | `screen-reader-compatibility-specialist` | `accessibility` | `local` | no |
| 412 | 28 | Accessible Table and Chart Designer | `accessible-table-and-chart-designer` | `accessibility` | `local` | no |
| 413 | 28 | Caption and Transcript Coordinator | `caption-and-transcript-coordinator` | `accessibility` | `local` | no |
| 414 | 28 | Plain Language Editor | `plain-language-editor` | `accessibility` | `local` | no |
| 415 | 28 | Dyslexia-Friendly Slide Designer | `dyslexia-friendly-slide-designer` | `accessibility` | `local` | no |
| 416 | 28 | Neuroinclusive Presentation Designer | `neuroinclusive-presentation-designer` | `accessibility` | `local` | no |
| 417 | 28 | Low-Vision Presentation Designer | `low-vision-presentation-designer` | `accessibility` | `local` | no |
| 418 | 28 | Cognitive Load Reviewer | `cognitive-load-reviewer` | `accessibility` | `local` | no |
| 419 | 28 | Flashing and Motion Safety Reviewer | `flashing-and-motion-safety-reviewer` | `accessibility` | `local` | no |
| 420 | 28 | Accessibility Remediation Specialist | `accessibility-remediation-specialist` | `accessibility` | `local` | no |
| 421 | 29 | Presentation Localization Manager | `presentation-localization-manager` | `localization` | `local` | no |
| 422 | 29 | Translation-Ready Layout Designer | `translation-ready-layout-designer` | `localization` | `local` | no |
| 423 | 29 | Text Expansion Accommodation Specialist | `text-expansion-accommodation-specialist` | `localization` | `local` | no |
| 424 | 29 | Bidirectional Slide Designer | `bidirectional-slide-designer` | `localization` | `local` | no |
| 425 | 29 | Cultural Visual Review Specialist | `cultural-visual-review-specialist` | `localization` | `local` | no |
| 426 | 29 | Local Color and Symbol Reviewer | `local-color-and-symbol-reviewer` | `localization` | `local` | no |
| 427 | 29 | Global Date and Number Formatter | `global-date-and-number-formatter` | `localization` | `local` | no |
| 428 | 29 | Multilingual Font Compatibility Specialist | `multilingual-font-compatibility-specialist` | `localization` | `local` | no |
| 429 | 29 | Subtitle Localization Specialist | `subtitle-localization-specialist` | `localization` | `local` | no |
| 430 | 29 | Localized Chart Label Designer | `localized-chart-label-designer` | `localization` | `local` | no |
| 431 | 29 | Regional Brand Compliance Reviewer | `regional-brand-compliance-reviewer` | `localization` | `local` | no |
| 432 | 29 | Transcreation Presentation Writer | `transcreation-presentation-writer` | `localization` | `local` | no |
| 433 | 29 | Global Template Variant Manager | `global-template-variant-manager` | `localization` | `local` | no |
| 434 | 29 | Cross-Cultural Presentation Reviewer | `cross-cultural-presentation-reviewer` | `localization` | `local` | no |
| 435 | 29 | International Export Quality Specialist | `international-export-quality-specialist` | `localization` | `local` | no |
| 436 | 30 | Enterprise Template Architect | `enterprise-template-architect` | `governance` | `plan` | no |
| 437 | 30 | Template Governance Manager | `template-governance-manager` | `governance` | `plan` | no |
| 438 | 30 | Design System Documentation Writer | `design-system-documentation-writer` | `governance` | `plan` | no |
| 439 | 30 | Slide Library Curator | `slide-library-curator` | `governance` | `plan` | no |
| 440 | 30 | Approved Asset Library Manager | `approved-asset-library-manager` | `governance` | `plan` | no |
| 441 | 30 | Brand Compliance Automation Designer | `brand-compliance-automation-designer` | `governance` | `plan` | no |
| 442 | 30 | Template Adoption Planner | `template-adoption-planner` | `governance` | `plan` | no |
| 443 | 30 | Design Request Intake Designer | `design-request-intake-designer` | `governance` | `plan` | no |
| 444 | 30 | Presentation Center of Excellence Planner | `presentation-center-of-excellence-planner` | `governance` | `plan` | no |
| 445 | 30 | Deck Review Workflow Designer | `deck-review-workflow-designer` | `governance` | `plan` | no |
| 446 | 30 | Naming and File Taxonomy Architect | `naming-and-file-taxonomy-architect` | `governance` | `plan` | no |
| 447 | 30 | Asset Version Control Manager | `asset-version-control-manager` | `governance` | `plan` | no |
| 448 | 30 | Reusable Component Designer | `reusable-component-designer` | `governance` | `plan` | no |
| 449 | 30 | Template Training Designer | `template-training-designer` | `governance` | `plan` | no |
| 450 | 30 | Presentation Operations Metrics Designer | `presentation-operations-metrics-designer` | `governance` | `plan` | no |
| 451 | 31 | Presentation Collaboration Facilitator | `presentation-collaboration-facilitator` | `collaboration` | `plan` | no |
| 452 | 31 | Comment and Feedback Triage Manager | `comment-and-feedback-triage-manager` | `collaboration` | `plan` | no |
| 453 | 31 | Stakeholder Review Planner | `stakeholder-review-planner` | `collaboration` | `plan` | no |
| 454 | 31 | Approval Workflow Designer | `approval-workflow-designer` | `collaboration` | `plan` | no |
| 455 | 31 | Design Critique Facilitator | `design-critique-facilitator` | `collaboration` | `plan` | no |
| 456 | 31 | Coauthoring Conflict Resolver | `coauthoring-conflict-resolver` | `collaboration` | `plan` | no |
| 457 | 31 | Change Log Maintainer | `change-log-maintainer` | `collaboration` | `plan` | no |
| 458 | 31 | Presentation Project Manager | `presentation-project-manager` | `collaboration` | `plan` | no |
| 459 | 31 | Asset Handoff Coordinator | `asset-handoff-coordinator` | `collaboration` | `plan` | no |
| 460 | 31 | Reviewer Role and Responsibility Mapper | `reviewer-role-and-responsibility-mapper` | `collaboration` | `plan` | no |
| 461 | 31 | Deadline and Milestone Planner | `deadline-and-milestone-planner` | `collaboration` | `plan` | no |
| 462 | 31 | Remote Presentation Collaboration Designer | `remote-presentation-collaboration-designer` | `collaboration` | `plan` | no |
| 463 | 31 | Client Revision Manager | `client-revision-manager` | `collaboration` | `plan` | no |
| 464 | 31 | Content Owner Matrix Designer | `content-owner-matrix-designer` | `collaboration` | `plan` | no |
| 465 | 31 | Final Sign-Off Coordinator | `final-sign-off-coordinator` | `collaboration` | `plan` | yes |
| 466 | 32 | Slide Visual QA Reviewer | `slide-visual-qa-reviewer` | `qa` | `local` | no |
| 467 | 32 | Cross-Platform Fidelity Tester | `cross-platform-fidelity-tester` | `qa` | `local` | no |
| 468 | 32 | Projector and Display Tester | `projector-and-display-tester` | `qa` | `local` | no |
| 469 | 32 | Font and Glyph QA Specialist | `font-and-glyph-qa-specialist` | `qa` | `local` | no |
| 470 | 32 | Media Playback Tester | `media-playback-tester` | `qa` | `local` | no |
| 471 | 32 | Link and Navigation Tester | `link-and-navigation-tester` | `qa` | `local` | no |
| 472 | 32 | Animation Regression Tester | `animation-regression-tester` | `qa` | `local` | no |
| 473 | 32 | Print and Handout QA Specialist | `print-and-handout-qa-specialist` | `qa` | `local` | no |
| 474 | 32 | PDF Export Preflight Specialist | `pdf-export-preflight-specialist` | `qa` | `local` | no |
| 475 | 32 | File Size Optimization Specialist | `file-size-optimization-specialist` | `qa` | `local` | no |
| 476 | 32 | Broken Asset Detector | `broken-asset-detector` | `qa` | `local` | no |
| 477 | 32 | Presentation Performance Optimizer | `presentation-performance-optimizer` | `qa` | `local` | no |
| 478 | 32 | Privacy and Metadata Reviewer | `privacy-and-metadata-reviewer` | `qa` | `local` | yes |
| 479 | 32 | Final Delivery Packager | `final-delivery-packager` | `qa` | `local` | yes |
| 480 | 32 | Presentation Recovery and Corruption Specialist | `presentation-recovery-and-corruption-specialist` | `qa` | `local` | no |
| 481 | 33 | AI Presentation Brief Interpreter | `ai-presentation-brief-interpreter` | `ai` | `local` | no |
| 482 | 33 | AI Outline-to-Deck Orchestrator | `ai-outline-to-deck-orchestrator` | `ai` | `local` | no |
| 483 | 33 | AI Slide Content Rewriter | `ai-slide-content-rewriter` | `ai` | `local` | no |
| 484 | 33 | AI Layout Variation Generator | `ai-layout-variation-generator` | `ai` | `local` | no |
| 485 | 33 | AI Theme Ideation Director | `ai-theme-ideation-director` | `ai` | `local` | no |
| 486 | 33 | AI Image Prompt Designer for Slides | `ai-image-prompt-designer-for-slides` | `ai` | `local` | no |
| 487 | 33 | AI Brand-Safe Visual Generator | `ai-brand-safe-visual-generator` | `illustration` | `local` | no |
| 488 | 33 | AI Chart Recommendation Specialist | `ai-chart-recommendation-specialist` | `ai` | `local` | no |
| 489 | 33 | AI Speaker Notes Generator | `ai-speaker-notes-generator` | `ai` | `local` | no |
| 490 | 33 | AI Accessibility Remediation Assistant | `ai-accessibility-remediation-assistant` | `ai` | `local` | no |
| 491 | 33 | AI Localization Workflow Designer | `ai-localization-workflow-designer` | `ai` | `local` | no |
| 492 | 33 | AI Design Critique Agent | `ai-design-critique-agent` | `ai` | `local` | no |
| 493 | 33 | AI Asset Tagging and Retrieval Agent | `ai-asset-tagging-and-retrieval-agent` | `ai` | `local` | no |
| 494 | 33 | AI Deck Quality Evaluator | `ai-deck-quality-evaluator` | `ai` | `local` | no |
| 495 | 33 | Human-in-the-Loop Presentation Orchestrator | `human-in-the-loop-presentation-orchestrator` | `ai` | `local` | no |
| 496 | 34 | Infographic Presentation Designer | `infographic-presentation-designer` | `format` | `local` | no |
| 497 | 34 | Pitch Poster Designer | `pitch-poster-designer` | `format` | `local` | no |
| 498 | 34 | Presentation Comic Designer | `presentation-comic-designer` | `format` | `local` | no |
| 499 | 34 | Storyboard Deck Designer | `storyboard-deck-designer` | `format` | `local` | no |
| 500 | 34 | Lookbook Presentation Designer | `lookbook-presentation-designer` | `format` | `local` | no |
| 501 | 34 | Moodboard Presentation Designer | `moodboard-presentation-designer` | `format` | `local` | no |
| 502 | 34 | Photo Essay Presentation Designer | `photo-essay-presentation-designer` | `format` | `local` | no |
| 503 | 34 | Interactive Workbook Designer | `interactive-workbook-designer` | `format` | `local` | no |
| 504 | 34 | Printable Presentation Handout Designer | `printable-presentation-handout-designer` | `format` | `local` | no |
| 505 | 34 | Digital Magazine Deck Designer | `digital-magazine-deck-designer` | `format` | `local` | no |
| 506 | 34 | Portfolio Presentation Designer | `portfolio-presentation-designer` | `format` | `local` | no |
| 507 | 34 | Resume and Career Deck Designer | `resume-and-career-deck-designer` | `format` | `local` | no |
| 508 | 34 | Real Estate Listing Presentation Designer | `real-estate-listing-presentation-designer` | `format` | `local` | no |
| 509 | 34 | Menu and Hospitality Presentation Designer | `menu-and-hospitality-presentation-designer` | `format` | `local` | no |
| 510 | 34 | Museum and Exhibition Presentation Designer | `museum-and-exhibition-presentation-designer` | `format` | `local` | no |
| 511 | 35 | PptxGenJS Deck Engineer | `pptxgenjs-deck-engineer` | `handoff` | `handoff` | no |
| 512 | 35 | python-pptx Automation Engineer | `python-pptx-automation-engineer` | `pipeline` | `local` | no |
| 513 | 35 | VBA Presentation Engineer | `vba-presentation-engineer` | `handoff` | `handoff` | yes |
| 514 | 35 | Google Apps Script Slides Engineer | `google-apps-script-slides-engineer` | `handoff` | `handoff` | yes |
| 515 | 35 | Figma API Presentation Integrator | `figma-api-presentation-integrator` | `handoff` | `handoff` | no |
| 516 | 35 | Canva Apps Workflow Integrator | `canva-apps-workflow-integrator` | `handoff` | `handoff` | no |
| 517 | 35 | Adobe ExtendScript Automation Specialist | `adobe-extendscript-automation-specialist` | `handoff` | `handoff` | yes |
| 518 | 35 | OOXML Presentation Package Engineer | `ooxml-presentation-package-engineer` | `pipeline` | `local` | no |
| 519 | 35 | JSON-to-Deck Pipeline Designer | `json-to-deck-pipeline-designer` | `pipeline` | `local` | no |
| 520 | 35 | CSV-to-Slide Automation Designer | `csv-to-slide-automation-designer` | `pipeline` | `local` | no |
| 521 | 35 | Database-Driven Presentation Builder | `database-driven-presentation-builder` | `pipeline` | `local` | no |
| 522 | 35 | REST API Presentation Integrator | `rest-api-presentation-integrator` | `handoff` | `handoff` | yes |
| 523 | 35 | Template Placeholder Mapping Engineer | `template-placeholder-mapping-engineer` | `pipeline` | `local` | no |
| 524 | 35 | Batch Rendering and Thumbnail Generator | `batch-rendering-and-thumbnail-generator` | `pipeline` | `local` | no |
| 525 | 35 | Presentation CI Quality Pipeline Designer | `presentation-ci-quality-pipeline-designer` | `pipeline` | `local` | no |

## Validation and tests actually completed
Completed on 2026-11-05 in the build environment:
- `--help` and `capabilities` ran successfully; registry reports 35 categories and 525 capabilities.
- Full bundled `self-test` passed: 15/15 test groups, including 525/525 capability dispatches through the real CLI interface with JSON output and 27 family representatives through JSON/SVG/HTML/PPTX output.
- Built the example deck to `json,svg,html,pptx,png,pdf,gif`; opened/inspected PPTX with python-pptx and ZIP CRC, parsed SVG with lxml, inspected HTML, checked PDF header/pages, opened GIF/PNG with Pillow, and visually inspected a contact sheet.
- Ran a 27-family full-format sweep (`json,svg,html,pptx,png,pdf,gif`) with 0 failures and 568 generated artifacts; targeted checks confirmed kiosk show properties, SVG icon set output, QR SVG output, and SRT/VTT caption output.
- Tested Markdown, copied HTML, CSV, JSON deck, and PPTX inputs; changing input changed deck fingerprints.
- Tested invalid JSON, missing input, unsafe traversal, unknown capability, approval gates, overwrite backup recovery, audit failure, repair, and post-repair audit pass.
- Tested localization with supplied translations, batch manifest, diff, merge, PPTX inspection, and corrupted-slide OOXML recovery.
- Copied the folder into a clean temporary skill root beside one test `SKILL.md` and reran build/capabilities using only relative paths.
- Verified flat folder structure, prefixed filenames, exactly one knowledge file, no internal subfolders, and no `SKILL.md` inside the component.
- Inspected the final delivery ZIP (18 entries, no wrapper folder, no nested archives), extracted it into a clean temporary skill root beside one test `SKILL.md`, rebuilt all example formats, and rerun the bundled quick self-test successfully from the extracted files.
## Limits and unavailable integrations
- This is a local production component, not a live connector for Google Slides, Canva, Adobe Creative Cloud, Keynote, Prezi, Pitch, Figma, Beautiful.ai, Gamma, Visme, Genially, Tome, Zoho, LibreOffice, WPS, Haiku, Mentimeter, or Miro. Those capabilities produce portable artifacts and explicit handoffs; platform import/rendering/permissions/publishing must be verified in the target platform.
- PowerPoint application rendering, native morph, object-level animation, trigger animation, motion paths, VBA execution, Office Scripts, add-ins, and Excel live links are not executed. The tool writes editable PPTX, basic transitions, sections, notes, links, theme XML, and portable motion storyboards/HTML/GIF previews.
- PDF is rasterized from previews. It is not a vector PDF export from PowerPoint and does not preserve editable text.
- No video recording, screen capture, MP4 encoding, audio recording, or audio mixing is bundled. Media capabilities produce scripts, captions, chapters, credits, and plans.
- No external AI model or machine translation service is called. AI-assisted rows use deterministic heuristics and prompt/handoff artifacts; localization applies supplied translation maps.
- Map visualization is a schematic coordinate map, not a geographic basemap. Background removal is deterministic chroma-key when a key color is supplied, not AI segmentation.
- Font embedding is application-controlled. Raster previews use Pillow default fonts and may substitute glyphs; SVG/PPTX use configured font names and should be checked on the presenting device.
- Automated accessibility checks do not replace manual screen-reader, keyboard, cognitive, cultural, or legal review.
## Troubleshooting and recovery
- `DEPENDENCY_MISSING`: run the exact pip install command above.
- `SCHEMA_VALIDATION_FAILED`: inspect `error.details.errors`, fix the deck JSON, and rerun. Use `schema` to print the schema.
- `OUTPUT_EXISTS` / `APPROVAL_REQUIRED`: choose a new output path, or intentionally use `--overwrite --approve`. A `.bak` sibling is created for approved overwrites.
- `UNSAFE_PATH`: use relative paths without `..`, absolute paths, drive letters, or home-directory shortcuts.
- `TIMEOUT` / `CANCELLED`: increase `--timeout` for large decks, reduce batch size, or rerun after cancellation. Atomic writes prevent partial final files.
- `PPTX_READ_FAILED`: run `run --capability presentation-recovery-and-corruption-specialist --kind pptx --input <file.pptx> --output <dir>`; review salvaged text and rebuild visuals.
- Missing images in previews: fix the relative asset path or include the asset beside the future skill root. The renderer writes an explicit unavailable-image placeholder rather than silently succeeding.
- External platform failures after import are hosting/catalog/permission/platform issues; this component cannot prevent them.
## Extension and maintenance
1. Add or adjust operational entries in `asset-capabilities.json`; keep ids lowercase hyphenated and unique.
2. Implement or extend a family handler in `tools-operations.py`; reuse `tools-core.py`, `tools-render.py`, `tools-raster.py`, and `tools-pptx.py` rather than duplicating logic.
3. Update `asset-deck.schema.json` and `asset-config.json` when new inputs/limits are introduced.
4. Add tests to `tools-tests.py` and run `self-test` plus a clean temporary skill-root test.
5. Keep all files flat, prefixed, and referenced by exact case-sensitive relative paths. Do not add `SKILL.md`, internal subfolders, nested archives, or machine-specific paths.
## Skill package compatibility
- Component folder: `universal-presentation-design/`.
- Actual component file count after this knowledge file: **18**.
- Maximum path depth from the future skill root: 2 (`universal-presentation-design/<file>`).
- The folder can be copied directly beside a parent package root `SKILL.md`; the combined package must contain exactly one `SKILL.md` at the skill root.
- The delivery ZIP contains this folder directly at the ZIP root, with no wrapper folder and no nested archives.
