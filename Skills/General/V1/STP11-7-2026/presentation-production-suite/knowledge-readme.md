# presentation-production-suite — knowledge and operating manual

A production Skill Tool for inspecting, auditing, repairing, authoring, governing and
validating PowerPoint presentations. It is a reusable **component** of a larger Copilot
skill: this folder contains no `SKILL.md` and does not need one to run. Everything here is
executable with Python 3.9+ and two ordinary libraries.

- **Skill Tool name:** `presentation-production-suite`
- **Primary entry point:** `tools-presentation-suite.py`
- **Capability source:** `asset-capability-registry.json` — all 324 capability concepts from
  the 27 categories of the Presentation Agent Skill Catalog, each mapped to its real
  implementation status, engines and commands.
- **Version:** 1.0.0

---

## 1. Purpose

The suite turns a `.pptx` package (or a neutral JSON deck model) into a normalized, fully
measured model, then lets you act on it with the same vocabulary:

| You want to | Command | Result |
|---|---|---|
| know what is actually in the deck | `inspect` | neutral model + 14 structural engines |
| find every defect, scored and ranked | `audit` | `audit.json`, `report.md`, `report.html`, scorecards, defect queue |
| see the slides without PowerPoint | `render`, `contact-sheet` | PNG/JPEG per slide, one review grid |
| fix defects safely, provably | `repair`, `normalize`, `governance` | new deck + change log + before/after verification |
| build slides from structured content | `author` | validated `.pptx` from a declarative spec |
| prove nothing regressed | `snapshot`, `compare` | last-known-good model, structural + visual diff |
| decide whether it may ship | `gate`, `export-check`, `score` | pass/block with blocking codes |
| run it over a whole library | `batch` | per-deck results with failure isolation |
| work in another language/tool | `extract-text`, `convert-units` | translation-ready text, unit conversion |

Everything is deterministic: the same input and config produce the same numbers, the same
findings and the same file bytes for the same operations.

---

## 2. Setup

### Requirements

| Requirement | Version | Needed for |
|---|---|---|
| Python | 3.9 – 3.13 | everything |
| `python-pptx` | >= 0.6.21 (tested on 1.0.2) | reading and writing `.pptx` / `.pptm` |
| `Pillow` | >= 9 (tested on 13.0.0) | `render`, `contact-sheet`, `compare --visual` |

Nothing else is required. `jsonschema` is optional (the built-in validator is used either
way). No network access, no office suite, no credentials, no paid service, no environment
variables are needed or read (except `PPTSUITE_DEBUG=1`, which adds a traceback on
unexpected errors).

### Install

```sh
# any shell; pip for the interpreter you intend to use
python -m pip install "python-pptx>=0.6.21" "Pillow>=9"
```

### First run (always do this after copying the folder)

```sh
python tools-presentation-suite.py selfcheck
```

`selfcheck` verifies the Python version, both libraries, every shipped asset, the default
config against its schema, the registry entry count, and that the folder is flat (no
subfolders, no `SKILL.md`). Exit code 3 means something required is missing.

The tool degrades cleanly when a dependency is absent rather than crashing: `python-pptx`
is only imported when a deck actually has to be read or written, so `help`, `capabilities`,
`selfcheck`, `validate-config`, `catalog`, `convert-units` and JSON-model analysis keep
working, while deck commands exit 3 with the exact `pip install` line to run. This is
covered by the `deps` self-test case, which makes both libraries unimportable and asserts
the behaviour.

### Supported inputs and outputs

| Input | Accepted | Notes |
|---|---|---|
| Deck | `.pptx`, `.pptm`, `.potx` | read-only commands work on all three; edit commands require `.pptx`/`.pptm` |
| Deck (neutral model) | `.json` | output of `inspect` (`model` key) or a hand-written neutral model; read-only commands only |
| Config | `.json` | merged over `asset-default-rules.json`, validated against `asset-config.schema.json` |
| Deck spec | `.json` | validated against `asset-deck-spec.schema.json` before anything is written |

Outputs: `.json` (models, reports, change logs), `.md` and `.html` (reports),
`.png`/`.jpg` (renders), `.pptx` (repaired/normalized/authored decks). All JSON is written
atomically (temp file + rename), so an interrupted run never leaves a half-written report.

---

## 3. Command reference

Run `python tools-presentation-suite.py help` for the summary, or
`python tools-presentation-suite.py help <command>` for one command. Global flags
(`--config`, `--quiet`) go **before** the command name:

```sh
python tools-presentation-suite.py --config my-rules.json audit --deck deck.pptx --out out/
```

Exit codes are uniform:

| Code | Meaning |
|---|---|
| 0 | success (and, for `gate`/`audit`, no findings at the `--fail-on` level) |
| 1 | the run completed but reported blocking findings (audit `--fail-on`, `gate`, `batch` partial failure) |
| 2 | usage error: bad arguments, unknown engine/scope/profile, invalid config or spec, missing file |
| 3 | runtime error: unreadable or corrupt deck, missing renderer, refused unsafe operation |

### 3.1 `help`, `capabilities`, `selfcheck`, `validate-config`

```sh
python tools-presentation-suite.py capabilities            # human summary
python tools-presentation-suite.py capabilities --json     # commands, 69 engines, catalog coverage
python tools-presentation-suite.py validate-config --config my-rules.json
```

`capabilities --json` is the machine-readable contract: every command with its usage and
approval flag, every engine with its group and description, and the catalog coverage
(324 capabilities → implemented / documented / delegated).

### 3.2 `inspect` — understand before changing

```sh
python tools-presentation-suite.py inspect --deck deck.pptx --out out/
```

Writes `out/inspect.json` with `meta` (hashes, counts, aspect), `model` (the full neutral
model: every slide, every object, geometry in points, runs, fills, alt text, media, notes,
theme), `engines` and `issues`. Save this file: it is the baseline for `audit --baseline`
and `score --baseline`.

### 3.3 `audit` — the full pass

```sh
python tools-presentation-suite.py audit --deck deck.pptx --out out/ \
  [--profile classroom] [--baseline out/inspect.json] [--fail-on error|warning|none] [--json]
```

Runs the 60-engine audit bundle, then computes:

- **`scores`** — readability, consistency, accessibility, structure, evidence, security and
  a weighted `overall` (weights in `score_weights`).
- **`defect_queue`** — deduplicated classes ranked by `severity weight × spread`.
- **`plan`** — the ordered remediation sequence for *this* deck.
- **`gate`** — pass/block from error-severity findings.

Outputs `audit.json`, `report.md`, `report.html`. The HTML report is self-contained (CSS
inline, contact sheet embedded as a data URI) and prints cleanly.

### 3.4 `analyze` — one engine at a time

```sh
python tools-presentation-suite.py analyze --deck deck.pptx --engine contrast,overflow --json
```

Engine names come from `capabilities`. Combine them freely; order is preserved and each
engine reports independently.

### 3.5 `render` and `contact-sheet` — see it

```sh
python tools-presentation-suite.py render --deck deck.pptx --out render/ \
  [--width 1280] [--labels] [--safe-area] [--no-text] [--format png|jpg] [--slides 1,3] [--contact-sheet]
python tools-presentation-suite.py contact-sheet --deck deck.pptx --out sheet.png --width 320 --cols 4
```

`render` draws the **geometry model**: real object boxes, fills, text, connectors, plus the
safe-area guide. It is for review, thumbnails and regression — not pixel parity. See
§9 for the exact limits.

### 3.6 `snapshot`, `repair`, `normalize`, `governance` — change safely

All three edit commands share one contract:

1. **Dry run by default.** Without `--apply`, nothing is written; you get a preview file
   (`repair-preview.json`) listing every planned change with before/after values.
2. **Approval gate.** Scopes that change appearance or destroy content
   (`contrast`, `effects`, `colors`, `strip-notes`, `redact`) refuse to run without
   `--approve` and exit 2 with the exact list of scopes needing approval.
3. **Backup first.** The original deck is copied to `<out>/last-known-good/` before writing.
4. **Verify after.** The output is re-opened, package health is re-checked, and the same
   engines are re-run so you see `before → after` counts and any newly introduced error
   classes (exit 3 if a repair made things worse).
5. **Idempotent.** A second run on the output reports zero changes.

```sh
# repair scopes
python tools-presentation-suite.py repair --deck deck.pptx --out out/ \
  --scope bounds,safe-margin,connectors,type-min,overflow,alt-text,reading-order [--apply] [--approve]
python tools-presentation-suite.py repair --deck deck.pptx --out out/ \
  --scope contrast,effects,scrub-metadata,strip-notes,redact --apply --approve

# normalizers
python tools-presentation-suite.py normalize --deck deck.pptx --out out/ \
  --scope align,spacing[,colors,names] [--apply] [--approve]

# governance
python tools-presentation-suite.py governance --deck deck.pptx --out out/ \
  --action scrub-metadata,strip-notes,redact [--apply] [--approve]
python tools-presentation-suite.py governance --deck deck.pptx --out out/ --hide 4,5 [--show 1] --apply
```

| Repair scope | What it does | Approval |
|---|---|---|
| `bounds` | pull partially/fully off-canvas objects back inside; shrink objects wider than the canvas | no |
| `safe-margin` | move objects that cross the safe-area line back inside it | no |
| `connectors` | attach loose connector ends to the nearest shape | no |
| `type-min` | raise runs below the profile minimum (footer band uses `min_footer_pt`) | no |
| `overflow` | grow text frames downward inside the safe area, capped at 240pt growth | no |
| `alt-text` | write content-derived alt text; mark obvious decoration as decorative | no |
| `reading-order` | re-sequence shapes to top-to-bottom, left-to-right | no |
| `contrast` | shift text lightness until the required WCAG ratio passes | **yes** |
| `effects` | remove shadow/glow/bevel/reflection | **yes** |
| `scrub-metadata` | clear author, last-modified-by, comments, keywords, category, subject, title, company | no |
| `strip-notes` | delete all speaker-notes text | **yes** |
| `redact` | replace sensitive matches with `[REDACTED]` in text and notes | **yes** |

| Normalizer | What it does | Approval |
|---|---|---|
| `align` | snap near-aligned left/top edges (within `align_tolerance_pt`) to a shared median | no |
| `spacing` | equalize gaps in a row/column of ≥3 objects | no |
| `colors` | collapse colours within `color_drift_distance` onto the most-used variant | **yes** |
| `names` | rename objects to `role-slide-index` semantic identifiers | no |

### 3.7 `author` — build slides from a spec

```sh
python tools-presentation-suite.py author --spec deck-spec.json --out new.pptx \
  [--base template.pptx] [--force]
python tools-presentation-suite.py author --type content --title "Claim here" \
  --bullets "first|second|third" --out new.pptx
```

Slide types: `title`, `content`, `kpi` (metric cards), `process` (chevron row, max 6 steps).
The spec is validated against `asset-deck-spec.schema.json` **before** any file is written,
so an invalid spec produces a precise error list and no output. Authored decks are
automatically checked for package health, bounds, minimum type size, overflow,
accessibility and reading order, and authoring clamps its own type sizes and fill colours so
it cannot emit the defects it detects. `asset-deck-spec-example.json` is a working example.

### 3.8 `compare`, `score`, `gate`, `plan`

```sh
python tools-presentation-suite.py compare --before a.pptx --after b.pptx [--visual] [--out out/]
python tools-presentation-suite.py score --deck deck.pptx [--baseline inspect.json] [--compare-to-baseline]
python tools-presentation-suite.py gate --deck deck.pptx [--artifacts deck.pptx,deck.pdf] [--baseline snap.json]
python tools-presentation-suite.py plan --deck deck.pptx
```

`compare` matches objects by name plus a 60-character text prefix, so small edits do not
break the pairing; `--visual` renders both sides per slide and reports changed pixel ratio
and bounding box, writing `diff-NNN.png` overlays.

### 3.9 `export-check`, `rescale`, `extract-text`, `batch`, `convert-units`, `catalog`

```sh
python tools-presentation-suite.py export-check --deck deck.pptx --artifacts deck.pptx,deck.pdf
python tools-presentation-suite.py rescale --deck deck.pptx --out out.pptx --preset 16:9 --apply --approve
python tools-presentation-suite.py extract-text --deck deck.pptx --out out/ [--slides 1,2] [--with-notes]
python tools-presentation-suite.py batch --deck "decks/*.pptx" --out batch-out/ --audit
python tools-presentation-suite.py convert-units --values 72,540 --from pt --to in
python tools-presentation-suite.py catalog [--missing]
```

`rescale` presets: `16:9`, `4:3`, `16:10`, `1:1`, `portrait`, `a4-landscape`,
`a4-portrait`, or explicit `--width-pt`/`--height-pt` (200–4000pt). It scales object
geometry proportionally, so a change of aspect ratio also needs a re-audit (the command
re-runs bounds/min-type/overflow and records the findings).

`batch` isolates failures: a corrupt deck is recorded with its reason and the batch
continues (exit 1). With `--repair-scopes ... --apply` it applies repairs per deck into
`--out`.

---

## 4. Configuration

The defaults live in `asset-default-rules.json`; your file is merged over them (deep for the
dictionaries `profiles`, `sensitive_patterns`, `font_fallback_map`, `limits`, `risk_weights`,
`score_weights`, `dependency_checks`). Unknown keys are ignored **and reported** so typos do
not pass silently. Validate with `validate-config`.

### Viewing profiles

| Profile | Min text | Safe margin | Body contrast | Max words/slide | Target density |
|---|---:|---:|---:|---:|---:|
| `conference-room` (default) | 18pt | 28pt | 4.5 | 90 | 35% |
| `classroom` | 20pt | 32pt | 4.5 | 70 | 30% |
| `large-stage` | 24pt | 40pt | 6.0 | 45 | 25% |
| `desktop-review` | 11pt | 18pt | 4.5 | 140 | 45% |
| `print-handout` | 10pt | 22pt | 7.0 | 160 | 40% |

Select with `--profile` on `audit`, or `"profile": "..."` in your config, or per-run flat
overrides via `"overrides": {"min_text_pt": 22}`.

### Keys you will actually change

| Key | Meaning |
|---|---|
| `align_tolerance_pt`, `spacing_tolerance_pt` | how close counts as "nearly aligned"/"equal gaps" |
| `color_drift_distance` | RGB distance under which two colours are treated as one |
| `overlap_min_ratio`, `overlap_ignore_area_pt2` | overlap sensitivity |
| `font_fallback_map` | font → ordered metric-similar replacements |
| `sensitive_patterns` | named regexes scanned in text **and** notes |
| `allowed_link_domains` | link policy allow-list (empty list = allow all) |
| `risk_weights`, `score_weights` | how findings roll up into priority and scores |
| `brand` | `allowed_colors`, `allowed_fonts`, `logo_name_patterns`, `min_logo_clear_space_pt` |
| `limits` | `max_slides`, `max_objects_per_slide`, `max_file_bytes`, `timeout_seconds` |

Example:

```json
{
  "profile": "large-stage",
  "overrides": {"min_text_pt": 22},
  "brand": {"allowed_colors": ["1F5C9E", "FFFFFF", "F2F5F9"], "allowed_fonts": ["Calibri", "Aptos"]},
  "limits": {"timeout_seconds": 90}
}
```

---

## 5. Engines (69), by group

Each engine returns `engine`, `title`, `summary`, `data`, `issues[]` and `limits[]`.
Issue shape: `{slide, object, severity, code, message, repair, evidence}`.

| Group | Engines |
|---|---|
| structure | `package`, `census`, `layouts`, `editable`, `hidden`, `metadata`, `assets`, `dependencies`, `intent`, `families` (10) |
| geometry | `canvas`, `coordinates`, `zones`, `safe-margin`, `bounds`, `rotation`, `whitespace`, `density-heatmap`, `overlap` (9) |
| grid | `alignment`, `distribution`, `spacing-tokens`, `title-zone`, `footer-zone` (5) |
| typography | `fonts`, `type-inheritance`, `min-type`, `type-scale`, `line-length`, `overflow`, `emphasis`, `rhythm` (8) |
| colour | `palette`, `contrast`, `color-drift`, `transparency`, `monochrome`, `color-semantics` (6) |
| shapes | `shapes`, `connectors` (2) |
| data | `tables`, `charts` (2) |
| media | `images`, `av`, `animation` (3) |
| accessibility | `a11y`, `reading-order`, `alt-text`, `color-independence`, `links`, `language` (6) |
| content | `bullets`, `titles`, `terminology`, `redundancy`, `abbreviations`, `sequence` (6) |
| evidence | `citations`, `freshness` (2) |
| brand | `brand` (1) |
| security | `sensitive`, `metadata-privacy`, `link-policy`, `macros` (4) |
| qa | `regression`, `defects`, `scores`, `gate`, `plan` (5) |
| capability | `capability-audit` (1) |

Run `capabilities` for the one-line description of each.

### Representative issue codes

`PKG_BAD_XML`, `PKG_DANGLING_REL`, `GEO_OUT_OF_BOUNDS`, `GEO_SAFE_MARGIN`, `GRID_NEAR_ALIGN`,
`GRID_DISTRIBUTION`, `GRID_TITLE_DRIFT`, `TYPE_TOO_SMALL`, `TYPE_OVERFLOW`,
`TYPE_LINE_TOO_LONG`, `A11Y_CONTRAST`, `A11Y_ALT_MISSING`, `A11Y_READING_ORDER`,
`A11Y_RTL_CONTENT`, `A11Y_CJK_CONTENT`, `COLOR_DRIFT`, `COLOR_GRAYSCALE_COLLISION`,
`SHAPE_CONNECTOR_DETACHED`, `DATA_TABLE_NO_HEADER`, `DATA_CHART_NO_TITLE`,
`MEDIA_LOW_DPI`, `HIDDEN_SLIDE`, `HIDDEN_OFF_CANVAS`, `SEC_EMAIL`, `SEC_AWS_ACCESS_KEY`,
`SEC_INTERNAL_URL`, `SEC_METADATA`, `INTENT_PLACEHOLDER`, `EVIDENCE_NO_CITATION`,
`EVIDENCE_STALE_YEAR`, `CONTENT_TITLE_TOPIC`, `BRAND_COLOR`, `BRAND_FONT`.

---

## 6. How the analysis is computed

Understanding three measurements prevents misreading the reports.

**Contrast.** Resolved per text run against its *effective* background: the object's own
solid fill first, otherwise the topmost filled shape that covers ≥60% of the text box,
otherwise the slide background (or the theme `lt1`/`dk1` if the slide has none). Requirement
is `contrast_body` (default 4.5:1) or `contrast_large` (3:1) when the run is ≥`large_text_pt`
or bold and ≥0.58× that size. WCAG 2.x relative luminance is used.

**Text measurement.** Line counts and overflow come from per-script average glyph advances
(`latin 0.50`, `cjk 1.00`, `wide 0.62` of the font size, with a bold factor and a narrow-
character correction). This is accurate to roughly ±8% for office typefaces — enough to rank
risk, not to certify a fit. Every engine that uses it says so in its `limits`.

**Scores.** `100 − Σ(risk weight of its findings)`, floored at 0, mapped into readability
(`TYPE_`, `SPACE_`, `CONTENT_`), consistency (`GRID_`, `COLOR_`, `TYPE_SCALE`,
`TYPE_EMPHASIS`, `SHAPE_`, `LAYOUT_`), accessibility (`A11Y_`), structure (`PKG_`,
`CENSUS_`, `HIDDEN_`, `EDIT_`, `GEO_`, `DEP_`), evidence (`EVIDENCE_`) and security
(`SEC_`). The overall score is the weighted mean of the first five.

---

## 7. Safety and approval rules

- **Nothing destructive happens by default.** `repair`, `normalize` and `governance` are dry
  runs unless `--apply` is given.
- **Appearance-changing or content-destroying scopes require `--approve`**: `contrast`,
  `effects`, `colors`, `strip-notes`, `redact`, and `rescale --apply`. Without it the command
  exits 2 and names the scopes.
- **Every write is backed up** to `<out>/last-known-good/<deck name>` before the new file is
  saved, and every change is logged with before/after values.
- **Every write is verified**: the output is re-opened, package health re-checked, and the
  relevant engines re-run. If a repair introduces a new error class, the command exits 3 and
  says so.
- **No network, no shell, no credential use.** The only external processes this suite ever
  runs are the ones you start yourself (e.g. `tools-selftest.py` invoking the CLI).
  Sensitive-content scanning is local pattern matching; matches are masked in output
  (`jane******om`-style previews), never printed in full.
- **Bounded work.** `limits.max_slides`, `max_objects_per_slide`, `max_file_bytes` and
  `timeout_seconds` stop runaway runs; renders are capped at 42 megapixels.
- **Governance before external release**: run `governance --action scrub-metadata,redact`
  plus `gate`, because hidden slides, notes and document properties all ship inside the file.

---

## 8. The capability registry — all 324 catalogued capabilities

`asset-capability-registry.json` maps every capability from the source catalog
(27 categories × 12) to an `id` (`c01-01` … `c27-12`), its category, its description, a
status, the engines that implement it, the commands that reach it, and — where relevant —
the requirement that blocks full automation.

| Status | Count | Meaning |
|---|---:|---|
| `implemented` | 187 | fully implemented and runnable through the named command |
| `documented` | 129 | detection, measurement or partial automation is implemented; the remaining synthesis or judgement step is guidance in this file |
| `delegated` | 8 | needs an external runtime, service or licence that this Skill Tool deliberately does not embed |

```sh
python tools-presentation-suite.py catalog --missing      # list the delegated capabilities and why
```

The eight delegated capabilities, with the exact reason:

| Capability | Requirement |
|---|---|
| c19-09 — Export Parity Checker | PowerPoint or LibreOffice to produce PDF/image exports for comparison |
| c19-11 — Presentation Mode Walkthrough | PowerPoint presentation-mode playback for transitions, builds and presenter view |
| c22-01 — PowerPoint-to-PDF Exporter | PowerPoint or LibreOffice to perform the actual PDF export |
| c23-02 — Terminology-Controlled Translator | a translation service or language model plus an approved terminology list |
| c23-09 — Localized Image Text Reviewer | OCR to read text inside images |
| c23-11 — Cultural Visual Suitability Reviewer | a vision model or human cultural review |
| c24-10 — Sharing Permission Reviewer | network/Microsoft Graph access to test recipient permissions |
| c25-11 — Cross-Renderer Fidelity Benchmark | PowerPoint, LibreOffice and a second renderer for cross-renderer comparison |

The registry is the source of truth: `python tools-presentation-suite.py catalog --missing`
prints this list from the shipped asset, so it can never drift from the documentation.

---

## 9. Limits — what this tool does not do

Stated plainly, because the reports would otherwise over-claim:

1. **No PowerPoint rendering engine.** `render` draws the geometry model. Font metrics,
   gradients, transparency, group transforms, chart/table internals and media poster frames
   are approximated. Use `export-check` for artifact/package verification and export from
   PowerPoint/LibreOffice when you need pixel parity.
2. **No text rewriting.** The suite measures and flags content (long bullets, topic-style
   titles, unexplained acronyms, terminology drift, redundancy). It does not rewrite prose —
   that is a language-model task outside this component.
3. **Inheritance is not fully resolved.** Theme, master and layout chains are read for
   colours and canvas, but a run that inherits its size cannot be measured without rendering;
   those runs are counted as `unmeasured_runs`, not as failures.
4. **Chart truth needs the workbook.** Axis scaling, aggregation and label values are checked
   structurally; the underlying data is not evaluated.
5. **Media requires external tools.** Codecs, loudness, captions and transcripts need
   `ffprobe`/`ffmpeg` or a caption file; only package presence and placement are checked.
6. **No OCR, no vision.** Text inside images, and diagram semantics, are invisible here.
7. **Live link and permission checks need network access.** Link text and domain policy are
   checked offline; reachability and recipient permissions are not.
8. **Font installation state is unknowable.** The suite proposes metric-compatible fallbacks
   from `font_fallback_map`; it cannot confirm what is installed on the delivery machine.
9. **Deleting masters/layouts is intentionally not automated.** Unused and duplicate layouts
   are reported; removing them changes the template contract, so it stays a human decision.
10. **Approximate metrics are labelled per engine.** Any engine relying on advance widths
    says so in its `limits` array, and those lines are printed in every report.

---

## 10. Files in this Skill Tool

| File | Prefix role | Purpose and load order |
|---|---|---|
| `tools-presentation-suite.py` | **primary entry point** | CLI, config loading, reporting, command implementations, approval and verification flow. Loads the modules below in order. |
| `tools-deckmodel.py` | supporting module (load 1st) | PPTX → normalized deck model, neutral JSON model, structural diff, package health, colour and text helpers. All `python-pptx` access lives here. |
| `tools-analysis.py` | supporting module (load 2nd) | 69 read-only engines. Depends on `deckmodel`. Never writes files or mutates decks. |
| `tools-authoring.py` | supporting module (load 3rd) | Repair/normalize/authoring operations that mutate a presentation. Depends on `deckmodel`. |
| `tools-render.py` | supporting module (load 4th) | Pillow renderer, contact sheets, visual diff. Depends on `deckmodel`. |
| `tools-selftest.py` | supporting tool (independent) | Builds fixtures and runs 22 acceptance cases (146 assertions) against the CLI. Not needed at runtime. |
| `knowledge-readme.md` | knowledge | This document. |
| `asset-default-rules.json` | asset | Thresholds, profiles, sensitive patterns, fallback map, risk/score weights, limits. |
| `asset-capability-registry.json` | asset | The 324-capability registry with status, engines and commands. |
| `asset-config.schema.json` | asset | JSON Schema for user config; used by `validate-config` and `selfcheck`. |
| `asset-deck-spec.schema.json` | asset | JSON Schema for `author --spec`. |
| `asset-deck-spec-example.json` | asset | A working four-slide spec (title, content, kpi, process). |
| `asset-test-cases.json` | asset | The 22 acceptance cases, their fixtures and their assertions. |
| `asset-report-template.html` | asset | Self-contained HTML report shell (no external resources). |

The folder is flat by design: every file sits directly inside
`presentation-production-suite/`, every name is prefixed `tools-`, `knowledge-` or `asset-`,
and there is no `SKILL.md`, no subfolder and no archive. It can be copied beside a parent
package's root `SKILL.md` unchanged.

---

## 11. Validation actually performed

Run on Python 3.13.16, python-pptx 1.0.2, Pillow 13.0.0, Linux. Reproduce with
`python tools-selftest.py` (add `--verbose` for all assertions, `--keep` to keep fixtures).

| Case | What it proves |
|---|---|
| `deps` | with `python-pptx` and Pillow made unimportable: `selfcheck` exits 3 and names both libraries, `capabilities`/`validate-config`/`catalog`/`help`/`convert-units` still work, `inspect`/`render` exit 3 with a clean runtime message and no traceback |
| `env` | `selfcheck` ready; `capabilities` lists 25 commands, 69 engines, 324 catalog capabilities; `help` and `help <cmd>` work; unknown topic exits 2 |
| `shape` | no `SKILL.md`, no subfolders, all prefixes valid, exactly one knowledge file, file count within limits |
| `config` | defaults validate; user config merges and switches profile; invalid config exits 2 and names the type error |
| `inspect` | 3-slide model, 960pt canvas, verbatim text, sha256 recorded; missing deck exits 2 |
| `audit-clean` | clean deck: 0 errors, overall ≥80, `audit.json`/`report.md`/`report.html`, scorecards and limits present |
| `audit-defects` | 11 seeded defect classes all detected; score <80; queue ranked with an error first; `gate` blocks with codes |
| `repair` | approval gate (exit 2), dry run writes nothing, apply writes a healthy package, errors do not increase, off-canvas/small-type/alt-text/overflow all repaired on re-audit, backup taken, second run idempotent |
| `normalize` | changes applied, colour drift collapsed, second run idempotent |
| `governance` | email/API key redacted, metadata cleared, notes stripped, changes recorded, visibility works, out-of-range slide exits 2 |
| `author` | 4 slides built from spec with 0 validation errors, reopens correctly, invalid spec exits 2 and writes nothing, existing output needs `--force` |
| `render` | 3 PNGs at 960×540, contact sheet spans thumbnails, manifest counts match |
| `regression` | identical decks → 0 structural changes and identical renders; different decks → structural + visual changes |
| `snapshot` | label, deck copy, model hash recorded; snapshot usable as an audit baseline |
| `errors` | unknown engine/scope/command/profile/audit-file exit 2; corrupt deck exits 3 with a clear message |
| `edge` | empty slide, zero-size object, rotation, RTL and CJK detected; 4:3 recognised; no crash |
| `units` | 72pt = 1in = 914400 EMU; 540pt = 7.5in; non-numeric exits 3 |
| `batch` | 3 decks processed with 1 isolated failure carrying its reason; successes carry scores |
| `extract` | text with slide/object context, notes included on request, markdown carries context |
| `rescale` | requires `--approve`; 4:3 at 720pt after applying; out-of-range canvas exits 2 |
| `plan` | ordered steps, suite-relative commands, snapshot first |
| `a11y-profile` | a stricter profile reports at least as many violations (profile plumbing is live) |

All 22 cases and 146 assertions pass. Additional checks done during development and
reproducible by hand: HTML reports parse with balanced `table/thead/tbody/tr/td/th`, `h2`,
`h3`, `ul`, `li`, `p`, `code`, `strong` and contain no unreplaced `{{PLACEHOLDER}}` tokens;
rendered PNGs were opened and inspected; an authored example deck passes
`contrast,min-type,overflow,bounds,a11y` with zero findings.

Not performed (and not claimed): any test against PowerPoint or LibreOffice rendering,
any font-metric comparison against a real font engine, any media codec check, any OCR, and
any live network link check.

---

## 12. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR (usage): deck not found` (exit 2) | wrong path | paths are resolved from your shell's cwd, not the suite folder |
| `ERROR (runtime): cannot open presentation` (exit 3) | corrupt or password-protected package | try `snapshot`/`inspect` on a re-saved copy; the suite will not invent recovery for encrypted files |
| `python-pptx is required` | dependency missing | `python -m pip install "python-pptx>=0.6.21"` |
| `Pillow is not installed` | rendering without Pillow | `python -m pip install "Pillow>=9"`; everything except `render`/`contact-sheet`/`--visual` still works |
| `approval required for: ...` (exit 2) | appearance-changing scope | review the dry-run preview, then re-run with `--approve` |
| `unknown engine` / `unknown scope` | typo | `capabilities` lists engines; `help repair` lists scopes |
| Audit reports a code you disagree with | threshold too strict for your context | adjust the profile or `overrides`, then `validate-config` |
| `NOTE: unknown config key ignored` | typo in your config | the message names the key |
| Repair reports "no room to grow within the safe area" | text genuinely too long | shorten the text or split the slide; the tool refuses to push content off-slide |
| A repair introduced a new error class (exit 3) | an aggressive scope interacted with the layout | restore `<out>/last-known-good/<deck>` and re-run narrower scopes |
| `set PPTSUITE_DEBUG=1` | unexpected error | prints a traceback for a bug report |
| Timing out on a huge deck | `limits.timeout_seconds` | raise it in your config, or run per-slide engines with `analyze` |

**Recovery model.** Every editing command writes a backup before saving; the neutral model
in `snapshot.json`/`inspect.json` lets you detect drift afterwards with `compare`; and the
change log (`repair-changes.json`, `normalize-changes.json`, `governance-changes.json`)
contains before/after values for every modified object, which is enough to reverse a change
by hand if needed.

---

## 13. Extending and maintaining

**Add an engine.** Write a function in `tools-analysis.py` with the signature
`(deck, cfg) -> result(...)`, register it in `ENGINES`, add it to an `ENGINE_GROUPS` entry and
an `ENGINE_DESCRIPTIONS` entry, then add the engine name to `AUDIT_BUNDLE` in
`tools-presentation-suite.py` if it should run in the full audit. Update the registry entry
that it implements and re-run `tools-selftest.py`.

**Add a repair.** Write the operation in `tools-authoring.py`, append every mutation to the
`changes` list with before/after values, clamp geometry so it cannot create a new
off-canvas/overflow defect, register it in `OPERATIONS` (with the approval flag), and prove
idempotency by running it twice in a self-test case.

**Add a profile.** Add an entry under `profiles` in `asset-default-rules.json`; it is
validated automatically. Keep every key that `definitions.profile` requires.

**Change a threshold for one client.** Do not edit the shipped asset — pass `--config` with
`overrides`, so the default stays portable.

**Maintenance checklist before shipping a change**

```sh
python tools-presentation-suite.py selfcheck
python tools-presentation-suite.py catalog          # registry references must all resolve
python tools-selftest.py                            # 22 cases / 146 assertions
```

Also confirm: exactly one `knowledge-readme.md`, at least one `tools-` entry point, no
`SKILL.md`, no subfolders, no unprefixed files, every reference relative and case-exact.
