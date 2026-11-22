# presentation-makers — Skill Tool knowledge

One reusable Skill Tool component that implements the whole **Presentation Makers** capability
catalog: requirements interpretation, platform selection, narrative planning, deterministic
drafting, native rendering to five output formats, accessibility and quality-gate auditing,
regression diffing, benchmarking, approved external conversion, and delivery packaging with
clean-state verification.

- **Skill Tool folder:** `presentation-makers/`
- **Primary entry point:** `presentation-makers/tools-presentation-makers.py`
- **Runtime:** Python 3.9+ and the standard library only. No third-party package, no network call,
  no API key, no font file, no external binary is required for any bundled capability.
- **Catalog coverage:** 32 categories / 320 capabilities, extracted from
  `Presentation_Makers_Agent_Skill_Catalog.pdf` and shipped as
  `presentation-makers/asset-skill-catalog.json`.

This folder is a Skill Tool component, not a complete Copilot skill: it contains **no `SKILL.md`**
and is designed to be copied directly beside a parent package's root `SKILL.md`. When combined,
that root keeps exactly one `SKILL.md`.

---

## 1. What the tool does

| Stage | Command | Result |
|---|---|---|
| Understand the request | `brief` | Normalised brief, platform ranking with reasons and cautions, narrative framework, risk register, production sequence, assumptions, review checkpoints |
| Plan the argument | `plan` | Schema-valid deck spec with sections, slide roles, pacing, call to action, appendix |
| Fill it with content | `draft` | Deck spec populated from Markdown/JSON sources, content map, pacing plan, speaker-note drafts |
| Render deliverables | `build` | `.pptx` (with notes), self-contained `.html`, `.pdf` handout, `handout.md`, `speaker-notes.md`, `outline.md`, optional `captions.srt` |
| Prove it is safe to use | `audit` | 46 checks across 7 weighted gates, 0–100 score, blocking/non-blocking decision, Markdown + JSON report |
| Compare versions | `diff` | Slide-level regression diff between two specs |
| Choose between candidates | `benchmark` | Ranked comparison of several specs against one brief |
| Hand it over | `package` | Flat delivery folder + zip with editable spec, outputs, audit evidence, sources, README, manifest, re-opened from a fresh extraction |
| Parse new catalogs | `intake` | Plain text / Markdown / copied HTML → structured catalog JSON |
| Delegate real conversion | `convert` | Bounded, approval-gated call to an installed local converter |
| Run everything | `pipeline` | brief → plan → build → audit → package in one command with per-step status |
| Verify the tool itself | `selftest` | 23 test groups over every public interface |

### Catalog coverage by category

`capabilities` prints this table at runtime (coverage is stored in the catalog asset, so it stays
in sync with the catalog itself). Mode meanings: **automated** = the command does the work and
writes a validated artifact; **assisted** = the command computes checks or partial artifacts and a
human finishes the last mile; **advisory** = the command produces a plan, constraint report, or
checklist for work that must happen inside a vendor application or on a specific device.

| # | Category | Handler commands | Mode |
|---|---|---|---|
| 1 | Presentation Strategy and Tool Selection | `brief`, `plan`, `validate` | automated |
| 2 | Narrative, Story, and Information Design | `plan`, `draft` | automated |
| 3 | Slide and Page Structure | `plan`, `build`, `audit` | automated |
| 4 | Typography and Text Formatting | `build`, `audit` | assisted |
| 5 | Shapes, Lines, Geometry, and Object Arrangement | `build`, `audit` | assisted |
| 6 | Images, Icons, Illustration, and Visual Assets | `plan`, `audit` | assisted |
| 7 | Tables, Charts, Data, and Infographics | `plan`, `build`, `audit` | assisted |
| 8 | Audio, Video, Recording, and Media | `plan`, `audit`, `package` | advisory |
| 9 | Animation, Transitions, and Motion Design | `plan`, `build`, `audit` | assisted |
| 10 | Interactivity, Links, Branching, and Audience Engagement | `build`, `audit` | assisted |
| 11 | Themes, Templates, Branding, and Design Systems | `build`, `audit` | automated |
| 12 | Collaboration, Review, Approval, and Analytics | `audit`, `package` | advisory |
| 13 | Accessibility and Inclusive Presentation Design | `build`, `audit` | automated |
| 14 | Export, Conversion, Packaging, and Publishing | `build`, `package`, `convert` | automated |
| 15 | Testing, Troubleshooting, and Presentation Reliability | `audit`, `diff`, `package`, `selftest` | automated |
| 16 | Microsoft PowerPoint Mastery | `brief`, `build`, `audit`, `convert` | advisory |
| 17 | Canva Presentations Mastery | `brief`, `build`, `audit` | advisory |
| 18 | Apple Keynote Mastery | `brief`, `build`, `audit` | advisory |
| 19 | Prezi Mastery | `brief`, `plan`, `audit` | advisory |
| 20 | Gamma Mastery | `brief`, `plan`, `build` | advisory |
| 21 | Pitch Mastery | `brief`, `plan`, `build` | advisory |
| 22 | Beautiful.ai Mastery | `brief`, `plan`, `audit` | advisory |
| 23 | LibreOffice Impress Mastery | `brief`, `convert`, `audit` | assisted |
| 24 | Zoho Show Mastery | `brief`, `audit` | advisory |
| 25 | Visme Mastery | `brief`, `plan`, `audit` | advisory |
| 26 | Adobe Express Presentations Mastery | `brief`, `plan`, `audit` | advisory |
| 27 | Figma Slides Mastery | `brief`, `plan`, `audit` | advisory |
| 28 | AI-Assisted Presentation Production | `draft`, `audit` | assisted |
| 29 | Automation, APIs, Macros, and Batch Production | `pipeline`, `diff`, `convert` | automated |
| 30 | Presentation Security, Privacy, and Governance | `audit`, `package` | assisted |
| 31 | Domain Presentation Solutions | `brief`, `plan`, `draft`, `build` | automated |
| 32 | Advanced Agent Orchestration for Presentation Work | `pipeline`, `benchmark`, `capabilities`, `package` | automated |

Read the coverage honestly: the tool never operates a vendor UI. For platform-mastery categories it
supplies the platform profile, export-interchange path, constraint report, and known limitations;
the clicking happens in PowerPoint, Canva, Keynote, Prezi, Gamma, Pitch, Beautiful.ai, Impress,
Zoho Show, Visme, Adobe Express, or Figma Slides. Section 12 lists the exact boundaries.

---

## 2. Setup

No installation step is required.

```sh
# POSIX shell (bash/zsh)
python3 --version                      # 3.9 or newer
cd <skill-root>/presentation-makers
python3 tools-presentation-makers.py --help
```

```powershell
# Windows PowerShell
python --version
cd <skill-root>\presentation-makers
python tools-presentation-makers.py --help
```

Optional, for maintainers only — not needed to run the tool:

```sh
python3 -m pip install --upgrade pypdf python-pptx   # used only to inspect rendered output in review
```

The tool itself imports nothing outside the standard library (`argparse`, `json`, `zipfile`, `zlib`,
`re`, `subprocess`, `tempfile`, `shutil`, `datetime`, `html`, `importlib.util`). `python-pptx` and
`pypdf` were used during development to re-open the generated PPTX/PDF files; they are **not**
imported by any shipped module.

Every command that writes output accepts `--outdir`; the default is `presentation-output/` relative
to the current working directory. Shipped assets are always resolved relative to the module
directory, so the tool works from any working directory.

---

## 3. Files, roles, and load order

| File | Role |
|---|---|
| `tools-presentation-makers.py` | **Primary entry point.** Argument parser, 15 subcommands, exit-code contract, approval gates, human and JSON output. Loads the libraries in the order below. |
| `tools-pm-core.py` | Shared library: configuration merge/validation, structured `PMError` + exit codes, atomic file IO, JSON-schema subset validator, WCAG colour math, text/line metrics, slug and text helpers. |
| `tools-pm-intake.py` | Catalog intake: text/Markdown/HTML normalisation, category and skill extraction, duplicate merging, catalog assembly and validation. |
| `tools-pm-plan.py` | Requirements and drafting engine: brief normalisation and defaults, narrative-framework selection, platform scoring, risk register, production sequence, content parsing, deck-spec generation, speaker-note drafting, pacing plan. |
| `tools-pm-render.py` | Layout engine (one neutral frame model) plus the PPTX, HTML, PDF, Markdown, notes, outline, and SRT renderers. |
| `tools-pm-audit.py` | Audit checks, weighted gate scoring, Markdown report, and spec diff. |
| `tools-pm-package.py` | Delivery package assembly, manifest, zip creation, clean-state extraction and verification of every file. |
| `tools-selftest.py` | Executable test suite (23 groups). Run directly or through `selftest`. |

**Load order** (each module registers itself in `sys.modules` under `pm_*` before importing the next):

```
tools-pm-core.py
  → tools-pm-intake.py
    → tools-pm-plan.py
      → tools-pm-render.py
        → tools-pm-audit.py
          → tools-pm-package.py
            → tools-presentation-makers.py   (primary entry point)
              → tools-selftest.py            (test runner, never imported by the tool)
```

Shipped assets, each loaded through `paths.*` in the configuration:

| Asset | Purpose | Consumed by |
|---|---|---|
| `asset-skill-catalog.json` | 32 categories × 320 skills with slugs, descriptions, and command coverage | `capabilities`, `intake` (regeneration target) |
| `asset-coverage-map.json` | Category → handler commands + coverage mode + note | `intake --coverage-map`, tests |
| `asset-brief-schema.json` | JSON Schema for the requirements brief | `validate --kind brief`, `brief`, `plan`, `draft` |
| `asset-deck-spec-schema.json` | JSON Schema for the deck specification | `validate --kind deck-spec`, `build`, `audit`, `diff`, `benchmark`, `package` |
| `asset-narrative-frameworks.json` | 14 narrative frameworks (executive SCIA, teaching, sales, investor, technical, research, status, campaign, training, event, portfolio, async card, spatial, classic) | `plan`, `draft`, `pipeline` |
| `asset-slide-roles.json` | 15 slide roles with layout, bullet limits, pacing, and guidance; 10 layout patterns | `plan`, `audit` |
| `asset-platform-profiles.json` | 12 platforms scored on 16 attributes with requirements, strengths, limits, export formats | `brief` |
| `asset-theme-presets.json` | 4 accessible theme presets (colours, fonts, type scale, geometry, chart series) | `build`, `audit` |
| `asset-quality-gates.json` | 7 weighted gates covering 46 check codes, with remediation text and scoring method | `audit`, `package` |
| `asset-default-config.json` | Editable copy of every built-in default, used with `--config` | `--config`, `config` |
| `asset-example-brief.json` | Complete worked brief (executive decision deck) | examples, `selftest` |
| `asset-example-content.md` | Matching content source: headings, bullets, `Metric:` lines, `Source:` lines | examples, `selftest` |

All cross-references inside this document and inside the assets are relative to the future skill
root, for example `presentation-makers/asset-theme-presets.json`, and use forward slashes.

---

## 4. Command reference

Global options accepted before **or** after the subcommand: `--config <file>`, `--json`,
`--force`, `--quiet`. `--version` prints the tool version.

Exit codes (identical for every command):

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Invalid, unparsable, or missing input; schema violation; unknown argument value |
| 3 | Quality gate / audit failure, failed package verification, strict-mode warning |
| 4 | Required external tool or integration unavailable |
| 5 | Approval required (overwrite, external execution) |
| 6 | Unexpected internal failure (`PM_DEBUG=1` prints the traceback) |
| 130 | Interrupted by the user |

### 4.1 `capabilities` — capability index (built-in help for the catalog)

```sh
python3 tools-presentation-makers.py capabilities
python3 tools-presentation-makers.py capabilities --category 13 --format json
python3 tools-presentation-makers.py capabilities --search "contrast"
python3 tools-presentation-makers.py capabilities --skill "Alt Text Specialist"
python3 tools-presentation-makers.py capabilities --format md --out capabilities.md
```

`--category` accepts an id (`13`), a slug fragment, or a name fragment. `--format` is
`text|json|md`. Output lists every category with skill count and the commands that implement it;
with filters it lists matching skills with their descriptions.

### 4.2 `intake` — parse plain text, Markdown, or copied HTML into a catalog

```sh
python3 tools-presentation-makers.py intake --input catalog.txt \
  --format text --coverage-map asset-coverage-map.json \
  --source-label "supplied catalog" --out asset-skill-catalog.json --force
printf '<div class="c"><h2>9. Media</h2><ul><li><strong>Skill Name:</strong> Caption Writer</li><li>Description: Writes timed captions.</li></ul></div>' \
  | python3 tools-presentation-makers.py intake --input - --out catalog.json --force
```

Recognised shapes: `N. Skill Name: X` + `Description: Y`; Markdown `## N. Category` with
`- **Name**: description`; copied HTML (tags, classes, `data-*` attributes, inline styles, list
markup, and entities are removed before parsing); and a single capability paragraph, which becomes
one category named from `--single-name` or derived from the text. Duplicate category names (for
example an index page repeated before the body) are merged instead of duplicated. `--format` is
`auto|text|markdown|html`. Exit code 2 with `intake-empty` when nothing parseable is supplied.

### 4.3 `validate` — schema check without side effects

```sh
python3 tools-presentation-makers.py validate --kind brief --input asset-example-brief.json
python3 tools-presentation-makers.py validate --kind deck-spec --input out/deck-spec.json --json
```

Prints every schema diagnostic with a JSON-path location. Exit 0 when valid, 2 when invalid.

### 4.4 `brief` — interpret the requirements and choose a platform

```sh
python3 tools-presentation-makers.py brief --brief asset-example-brief.json --outdir out
```

Writes `out/brief/brief.json` (normalised brief), `out/brief/brief-analysis.json` (full structured
analysis) and `out/brief/brief-analysis.md` (readable report) containing: platform ranking with
scores, reasons, cautions, requirements and export formats; the selected narrative framework with
its section sequence plus the runner-up; a risk register with level, trigger, mitigation, and the
test that proves the mitigation; a 7-step production sequence with exit criteria; the assumptions
the tool applied; success criteria; and the review checkpoints.

### 4.5 `plan` and `draft` — narrative skeleton and content-filled spec

```sh
python3 tools-presentation-makers.py plan  --brief asset-example-brief.json --outdir out
python3 tools-presentation-makers.py draft --brief asset-example-brief.json \
  --content asset-example-content.md --outdir out
```

`plan` produces the structure only (roles, titles, pacing, call to action, appendix). `draft`
requires at least one `--content` file and fills bullets, metric cards, tables, phases, columns,
sources, and speaker notes from it. Both write `deck-spec.json`; `draft` also writes
`content-map.json` (which content section fed which slide) and both write `pacing-plan.json` and
`deck-plan.md`. Repeat `--content` to merge several files. Refuses to overwrite an existing
`deck-spec.json` without `--force` (exit 5).

Content source format — Markdown headings become sections, and each line is classified:

```markdown
# Document title (optional, ignored as a section)

## Section title that matches a framework section (or is matched by keyword overlap)
- A bullet becomes a slide point.
Metric: Pre-renewal churn = 7.3% (up 3.2 pts year over year)   # becomes a metric card
Source: Support operations warehouse, extract dated 2026-10-28  # becomes a slide source + appendix entry
Note: say this out loud during the slide                      # becomes speaker note text
Plain paragraphs are split into sentences and treated as candidate points.
```

JSON content is also accepted: `{"title": "...", "sections": [{"title": "...", "points": [...],
"metrics": [{"label","value","note","source"}], "sources": [...], "notes": "..."}]}`.

### 4.6 `build` — render every deliverable natively

```sh
python3 tools-presentation-makers.py build --spec out/deck-spec.json --outdir out/deck \
  --formats pptx,html,pdf,md,notes,outline,srt
```

| Format | File | What is produced |
|---|---|---|
| `pptx` | `deck.pptx` | 16:9 Office Open XML deck: slide master, blank layout, theme part carrying the palette and fonts, one shape per layout frame, a real title placeholder on every slide, and a notes page per slide. Opens in PowerPoint, Keynote, and LibreOffice Impress. |
| `html` | `deck.html` | One self-contained file: inline CSS and JS, no external fonts/scripts/images, keyboard navigation (arrows, Home, End, `N` for notes), visible notes panel, `aria-roledescription="slide"`, per-slide `aria-label`, semantic `h1`/`h2`, `prefers-reduced-motion` handling, and print styles. |
| `pdf` | `handout.pdf` | Hand-written PDF 1.4, one page (960×540 pt) per visible slide, Helvetica family, real text (selectable and extractable), filled shape rules. Hidden slides are excluded. |
| `md` | `handout.md` | Full Markdown handout: metadata, call to action, sections, bullets, metric lines, tables, quotes, per-slide sources, source list. |
| `notes` | `speaker-notes.md` | Per-slide narration, role, pacing, cumulative time, hidden-slide flag, sources. |
| `outline` | `outline.md` | One-page structural outline with role tags. |
| `srt` | `captions.srt` | Draft caption file derived from speaker notes and slide timing. |

Each file is re-opened and structurally verified immediately after writing; failures are reported as
`build-verify-failed`. Refuses to overwrite existing rendered files without `--force` (exit 5).

### 4.7 `audit` — accessibility, integrity, privacy, and delivery gates

```sh
python3 tools-presentation-makers.py audit --spec out/deck-spec.json --build-dir out/deck \
  --brief asset-example-brief.json --outdir out/audit
python3 tools-presentation-makers.py audit --spec out/deck-spec.json --gate accessible-by-default --strict
```

46 check codes are grouped into 7 weighted gates from `asset-quality-gates.json`:

| Gate | Weight | Blocking | Checks |
|---|---|---|---|
| `content-integrity` | 22 | yes | call to action, sections, agenda, summary, unsourced evidence, data source, percent totals, truncated axis, missing baseline, stale data, table density |
| `accessible-by-default` | 22 | yes | titles, alt text, decorative conflict, text contrast, graphic contrast, minimum type size, reading order, table headers, media captions, flash rate, autoplay |
| `layout-safety` | 18 | yes | bullet count, bullet length, computed overflow, words per minute, off-slide frames, frame count |
| `privacy-governance` | 16 | yes | email PII, phone-number PII, confidentiality marker, external links, hidden slides, retention/metadata |
| `delivery-readiness` | 14 | yes | missing declared outputs, PPTX structure and slide count, PDF completeness, HTML external references, missing notes, oversized files |
| `brand-consistency` | 5 | no | approved fonts, approved palette, logo on title slide, organisation/footer |
| `motion-comfort` | 3 | no | animated object count, meaning carried only by motion |

Gate score = `100 × (1 − (errors + 0.5 × warnings) / applicable checks)`; the deck score is the
weighted mean. Deck status is `pass` (no findings above info), `review` (warnings), or `blocked`
(any error). Exit code is 3 when blocked, or when `--strict` turns warnings into a failure.

The overflow check is real geometry, not a heuristic guess: the layout engine lays the deck out,
estimates wrapped text height per frame from the type size and box width, and compares it with the
frame height after the renderer's own shrink-to-fit pass.

### 4.8 `diff` — regression review

```sh
python3 tools-presentation-makers.py diff --before v1/deck-spec.json --after v2/deck-spec.json --outdir out/diff
```

Reports added, removed, changed (with the fields that changed), and reorder-only differences, as
JSON plus Markdown.

### 4.9 `benchmark` — choose between candidates under one brief

```sh
python3 tools-presentation-makers.py benchmark --spec a/deck-spec.json b/deck-spec.json c/deck-spec.json \
  --brief asset-example-brief.json
```

Ranks candidates by audit score, blocking errors, warnings, source coverage per slide, and slide
count, reusing the full audit for each candidate.

### 4.10 `package` — delivery folder, zip, and clean-state verification

```sh
python3 tools-presentation-makers.py package --spec out/deck-spec.json --build-dir out/deck \
  --outdir out --verify [--extra notes.pdf] [--force]
```

Produces `out/<deck-slug>-delivery/` and `out/<deck-slug>-delivery.zip` containing, flat at the
archive root: every rendered output that exists, `deck-spec.json` (the editable twin), `sources.md`,
`audit-report.md`, `audit-report.json`, `README.md` (open instructions, edit path, audit status,
approval checklist, stated limits), and `manifest.json`. Verification extracts the zip into a fresh
temporary directory and re-opens every file (PPTX parts and slide count, PDF header/EOF/pages, HTML
self-containment, JSON parse, text non-empty), and rejects unsafe paths or nested archives.
Requires `--force` to replace an existing package (exit 5).

### 4.11 `convert` — approved external conversion

```sh
python3 tools-presentation-makers.py convert --input out/deck/deck.pptx --to pdf \
  --tool soffice --approve-external-tool --timeout 180 --outdir out/converted
```

The only command that executes an external process. It requires `--approve-external-tool`
(exit 5 without it), resolves the binary with `shutil.which` (exit 4 when absent), checks the input
exists and is under the size limit, runs without a shell, applies the configured timeout, retries up
to `limits.max_retries`, and reports the exact argv it used. Supported launchers: `soffice`,
`libreoffice`, `unoconv`; anything else is invoked with the file path only.

### 4.12 `pipeline` — the whole chain, with checkpoints

```sh
python3 tools-presentation-makers.py pipeline --brief asset-example-brief.json \
  --content asset-example-content.md --outdir out --formats pptx,html,pdf,md,notes
```

Runs brief analysis → plan/draft → build → audit → package → verify, writing one JSON envelope with
per-step status. If the audit blocks delivery, packaging is skipped and the command exits 3, unless
`--allow-blocked` is passed to package a draft deliberately.

### 4.13 `config` and `selftest`

```sh
python3 tools-presentation-makers.py config            # effective limits, policy, audit, render, ai
python3 tools-presentation-makers.py config --show     # full merged configuration
python3 tools-presentation-makers.py selftest --verbose
```

`selftest` runs all 23 test groups and exits 3 if any fail.

---

## 5. Inputs

### 5.1 Brief (`asset-brief-schema.json`)

Required: `title`, `purpose`, `audience.primary`. Everything else is optional and defaulted, and the
applied defaults are printed by `brief` and stored in `deck.assumptions`, so nothing is silent.

```json
{
  "title": "Protecting Q4 retention without adding headcount",
  "purpose": "decide",
  "purpose_detail": "Get steering-committee approval ...",
  "audience": {"primary": "steering committee", "size": "9 people", "expertise": "executive",
                "language": "en", "objections": ["We already tried this in Q2"]},
  "delivery": {"mode": "live-stage", "duration_minutes": 25, "network": "unreliable",
                "devices": ["Windows laptop"]},
  "constraints": {"slides_target": 18, "must_include": ["churn trend"],
                   "approved_fonts": ["Segoe UI"], "logo_required": false,
                   "editability": "editable-source-required"},
  "priorities": ["precision", "data_visuals", "compatibility", "offline", "accessibility"],
  "platform": {"preferred": "powerpoint", "must_support": ["pptx", "pdf"]},
  "narrative": {"framework": "executive-scia", "tone": "plain"},
  "accessibility": {"standard": "wcag-aa", "reduced_motion": true, "min_body_pt": 16},
  "security": {"confidential": true, "external_audience": false, "retention_days": 180},
  "call_to_action": {"decision": "Approve the pilot", "owner": "VP Customer Success", "deadline": "2026-11-21"}
}
```

Valid `purpose` values: `decide, inform, persuade, teach, train, sell, fundraise, status, research,
launch, align, recap`. Valid `delivery.mode` values: `live-stage, classroom, sales-call,
remote-meeting, recorded-narration, self-running-kiosk, embedded-web, printable-handout, workshop`.
`priorities` accepts any of the 16 platform attributes. If `priorities` is omitted the tool infers
them from purpose, delivery mode, and network conditions.

### 5.2 Deck specification (`asset-deck-spec-schema.json`)

```
spec_version, deck{title, subtitle, organization, audience, purpose, delivery_mode, platform,
                   language, duration_minutes, framework, theme_id, classification, reduced_motion},
theme{id, background, surface, text_primary, text_secondary, accent, accent_alt, text_on_accent,
      fonts{heading, body, mono}, typography_pt{...}, geometry{...}, chart_series[]},
call_to_action{decision, owner, deadline},
sections[{id, title, purpose, slide_ids[]}],
slides[{id, role, title, subtitle, section_id, bullets[], steps[], phases[], metrics[], table{},
        quote{}, left[]/right[]/left_title/right_title, notes, evidence[], image{}, media[],
        animation[], links[], data{}, alt_text, decorative, reading_order[], pacing_seconds,
        hidden, sources[]}],
sources[], assumptions[], review_checkpoints[], provenance{}, stats{}
```

`role` must be one of the 15 roles in `asset-slide-roles.json`. `id` must match
`^[a-z0-9][a-z0-9-]{0,59}$`. The spec is hand-editable: edit `deck-spec.json`, then re-run `build`
and `audit`. Everything downstream reads it.

### 5.3 Catalog text (for `intake`)

Plain text, Markdown, or copied HTML as described in 4.2. The shipped catalog was generated with
this command from the source PDF text, so regeneration is reproducible.

---

## 6. Outputs

| Output | Where | Notes |
|---|---|---|
| `brief.json`, `brief-analysis.json`, `brief-analysis.md` | `<outdir>/brief/` | Normalised brief + full analysis |
| `deck-spec.json` | `<outdir>/` | Single source of truth, schema-valid |
| `content-map.json`, `pacing-plan.json`, `deck-plan.md` | `<outdir>/` | Only with `draft` / content input (pacing always) |
| `deck.pptx`, `deck.html`, `handout.pdf`, `handout.md`, `speaker-notes.md`, `outline.md`, `captions.srt` | `--outdir` of `build` | Each verified after writing |
| `audit-report.json`, `audit-report.md` | `--outdir` of `audit` | Findings, gates, score, evaluated checks |
| `spec-diff.json`, `spec-diff.md` | `--outdir` of `diff` | Regression review |
| `<slug>-delivery/`, `<slug>-delivery.zip` | `--outdir` of `package` | Flat archive, manifest, README, re-verified |

Every command can also return a machine-readable envelope with `--json`:
`{tool, version, command, status, generated_at, diagnostics[], outputs[], counts, ...command fields}`.
Diagnostics always carry `level` (`error|warning|info`), `code`, `message`, and optional `where`
and `hint`.

---

## 7. Configuration

`--config <file>` deep-merges a JSON file over the built-in defaults
(`asset-default-config.json` is a ready copy). Partial files are valid.

| Key | Default | Effect |
|---|---|---|
| `paths.output_dir` | `presentation-output` | default `--outdir` |
| `paths.*` | asset filenames | which asset each stage loads |
| `limits.max_slides` | 120 | hard ceiling, enforced by the spec schema |
| `limits.max_sections` | 24 | same |
| `limits.max_bullets_per_slide` | 7 | ceiling used by planning and the density check |
| `limits.max_chars_per_bullet` | 180 | bullets are truncated at planning time, flagged at audit time |
| `limits.max_words_per_minute` | 180 | pacing realism check |
| `limits.max_file_bytes` | 26214400 | oversized output warning; also the `convert` input ceiling |
| `limits.max_input_bytes` | 2097152 | `intake` input ceiling |
| `limits.external_tool_timeout_seconds` | 180 | per-attempt `convert` timeout (1–3600) |
| `limits.max_retries` | 2 | `convert` retry budget |
| `policy.require_overwrite_approval` | true | refuse to replace specs/renders/packages without `--force` |
| `policy.require_external_tool_approval` | true | `convert` requires `--approve-external-tool` |
| `policy.allow_network` | false | nothing in the tool opens a socket; kept explicit |
| `policy.allow_arbitrary_code` | false | no eval, no shell; the only process is the approved converter |
| `policy.fail_on_warning_in_strict_mode` | true | `--strict` semantics |
| `audit.contrast_minimum_normal` | 4.5 | WCAG AA body text |
| `audit.contrast_minimum_large` | 3.0 | WCAG AA large text / graphics |
| `audit.min_body_font_pt` | 14 | minimum body size on a projector |
| `audit.max_flashes_per_second` | 3 | flash/photosensitivity ceiling |
| `audit.max_animation_objects_per_slide` | 8 | motion-comfort ceiling |
| `render.default_theme` | `aurora` | theme when neither spec nor brief names one |
| `render.include_notes` | true | write PPTX notes pages |
| `render.pptx_creator` | `presentation-makers Skill Tool` | document property |
| `ai.provider` | `local-deterministic` | only implemented provider; see section 12 |

Invalid configuration raises `config-invalid` (exit 2) before any work starts: limits must be
positive integers, the timeout must be 1–3600, contrast thresholds must be numeric.

---

## 8. Knowledge assets in practice

**Narrative frameworks** (`asset-narrative-frameworks.json`). Selection scores each framework on
purpose match (+40), audience match (+25), delivery-mode match (+20), corpus keyword hits (+8), the
purpose hint map (+12), plus a base priority. Ties break alphabetically, so selection is
deterministic. `narrative.framework` in the brief overrides scoring. Each framework carries its
section list with a slide role per section, a call-to-action template, and a closing tip.

**Slide roles** (`asset-slide-roles.json`). Each role sets the layout pattern, bullet limit, word
limit, default pacing, whether a source is mandatory, and one line of guidance. `audit` uses these
limits, so editing the asset changes both planning and checking.

**Platforms** (`asset-platform-profiles.json`). 12 platforms × 16 attributes (0–5). `brief` scores
each platform as the mean of the brief's priorities, subtracts 25 when a required export format is
missing, adds 15 for an explicit preference, and reports the reasons and cautions behind the number.
Scores are general and must be re-checked per plan and region — the asset says so.

**Themes** (`asset-theme-presets.json`). Four presets; each is verified in the test suite against the
AA thresholds for primary text, secondary text, and accent graphics.

**Gates** (`asset-quality-gates.json`). Weights, blocking flags, check-code membership, scoring
method, and remediation text. Editing the gate list changes the audit verdict without touching code.

---

## 9. Safety and approval rules

1. **Overwrite approval.** With `policy.require_overwrite_approval` on (default), replacing an
   existing deck spec, rendered file, or delivery package requires `--force`; otherwise exit 5 with
   `output-exists` / `package-exists`. Writes are atomic (temp file in the same directory, then
   `os.replace`), so a failed write never truncates a good file.
2. **External execution approval.** `convert` requires `--approve-external-tool`, runs without a
   shell, and is bounded by a timeout and retry budget. No other command spawns a process.
3. **No network, no credentials.** Nothing reads an API key, contacts a service, or embeds an
   endpoint. `policy.allow_network` is documented as false and is not used to enable anything.
4. **No arbitrary code.** No `eval`, no `exec`, no dynamic import from user input, no shell
   interpolation. User strings reach output as text only and are XML/HTML/PDF-escaped at the
   boundary (`html.escape`, `_pdf_escape`, `_esc`).
5. **Privacy review is explicit.** The audit raises `privacy.pii-email` (error) and
   `privacy.pii-phone` (warning) for personal data found in slide text or notes, flags external
   links for a live check, and flags hidden slides that still ship inside the file. It does not
   silently strip anything — a human decides.
6. **Approval checkpoints travel with the package.** The brief's review checkpoints (claims and
   sources, sensitive content, brand and legal, accessibility, external publishing) are copied into
   the spec and rendered as an unchecked list in the package README.
7. **Publishing, financial, and credential actions are out of scope.** The tool never uploads,
   shares, or authenticates. Publishing a deck stays a human action in the target platform.
8. **Stated limits in the deliverable.** Every package README repeats what the package does not
   contain (fonts, media, video), so the recipient is not misled.

---

## 10. Validation performed

Everything below was executed on this build with `python3` (CPython 3.13.16) in a Linux sandbox,
from a clean working directory, using relative and absolute paths as shown.

### 10.1 Bundled suite

```sh
python3 presentation-makers/tools-selftest.py            # 23 passed, 0 failed, 23 total
python3 presentation-makers/tools-presentation-makers.py selftest
```

| # | Test group | What it proves |
|---|---|---|
| 1 | core: contrast and colour maths | 21:1 and 1:1 anchors, AA/AAA banding, hex parsing, invalid colour error |
| 2 | core: JSON schema validator | valid instance passes; `minimum`, `additionalProperties`, `required` violations reported |
| 3 | core: text metrics and helpers | line estimation, word count, slugify, speaking seconds, truncation |
| 4 | core: configuration | defaults, deep merge preserves unspecified keys, invalid limit rejected |
| 5 | intake: labeled text | 2 categories / 3 skills parsed, catalog validates |
| 6 | intake: HTML and Markdown | tags and `data-*` attributes never leak into names; Markdown bold entries parse; H1 is not a category |
| 7 | intake: single capability and empty input | one-paragraph capability becomes one category; empty input raises `intake-empty` |
| 8 | assets: catalog integrity | exactly 32 categories / 320 skills, no duplicate slugs, every skill described, every category covered by commands, coverage map matches catalog names both ways |
| 9 | assets: theme contrast | all four themes pass AA for primary, secondary, and graphic use |
| 10 | brief: validation and defaults | example brief loads, minimal brief is defaulted, missing required field raises `brief-schema`, broken JSON raises `json-invalid` |
| 11 | plan: framework and platform | `decide`→`executive-scia`, `teach`→`educational-sequence`; changing priorities changes the platform ranking |
| 12 | plan: spec generation | schema-valid spec, title first / appendix last, notes on every slide, sources carried, metric cards extracted, pacing within budget |
| 13 | render: all formats | 7 formats produced; PPTX parts, notes pages, theme colour, and title placeholder verified; HTML has inline style/script, zero external references, `h1`/`h2`, aria slide roles, reduced-motion CSS; PDF header/EOF/page objects/fonts; notes, outline, and SRT contents |
| 14 | render: output changes with input | a changed title and theme produce different HTML and different bytes |
| 15 | audit: clean deck | example deck reaches `pass`, score 100.0, 45 checks evaluated, zero blocking gates, report renders |
| 16 | audit: injected faults | 7 deliberately injected defects (no CTA, white-on-white text, missing alt text, 8×400-character bullets, an email address, 9 flashes/second, unsourced evidence) are each detected and the deck is `blocked` |
| 17 | audit: delivery and export | missing declared outputs flagged; an injected external stylesheet is flagged |
| 18 | diff and benchmark | added/changed slides detected, identical specs compare equal, two candidates score |
| 19 | package | zip contains the 7 required files, verification re-opens every file, overwrite needs approval, a nested archive is rejected |
| 20 | cli: surface and exit codes | `--help` lists all 15 commands; `capabilities` text/JSON/filter; unknown category → 2; valid/invalid/missing input → 0/2/2; `convert` without approval → 5; missing converter → 4 |
| 21 | cli: pipeline end to end | one command produces spec, 5 outputs, passing audit, and a verified zip; `build` and `audit` re-run cleanly afterwards; `--strict` exits 0 or 3 |
| 22 | cli: intake round trip | CLI intake writes a catalog with the expected 2 categories / 3 skills and names |
| 23 | recovery paths | truncated JSON raises `json-invalid`, schema violation is structured, overwrite protection keeps the original bytes, empty content raises `content-empty` |

### 10.2 Manual command verification (this build)

| Command | Observed result |
|---|---|
| `--help` | Usage with all 15 subcommands, expanded epilog of examples, exit 0 |
| `capabilities` | `32 categories, 320 skills (catalog v1.0)`, per-category rows with handler commands and mode |
| `capabilities --format md --out caps.md` | 452-line Markdown listing written |
| `capabilities --skill "Color Contrast Specialist"` | Skill detail with description and category |
| `capabilities --category does-not-exist` | `category-not-found`, exit 2 |
| `intake` on the source PDF text | 32 categories / 320 skills, coverage attached, catalog written |
| `intake` on piped HTML (`--input -`) | `Parsed stdin (html): 1 categories, 1 skills` |
| `validate --kind brief` | valid → exit 0; malformed JSON → `json-invalid`, exit 2; missing file → `input-missing`, exit 2 |
| `brief` | Framework `executive-scia`, platform Microsoft PowerPoint 100/100, 3 risks, 3 files written |
| `plan` (no content) | Skeleton spec written, warnings about unsourced evidence slides, exit 0 |
| `draft` (with content) | 18 slides, content-map, pacing plan, deck plan; re-run without `--force` → exit 5 |
| `draft` without `--content` | argparse requirement error, exit 2 |
| `build --formats pptx,html,pdf,md,notes,outline` | 6 files written and each verified: `18 slides, 18 notes pages`, `self-contained HTML deck`, `PDF with 17 page object(s)`, text files with byte counts. `srt` is covered by the suite (test 13) and by the `--formats ...,srt` path. |
| `build` re-run without `--force` | `output-exists`, exit 5 |
| `audit --build-dir` | All 7 gates `pass`, score 100.0, 4 info notes, 45 checks evaluated |
| `audit --strict` | exit 0 (no warnings in the example deck) |
| `package --verify` | 11 files, 60.3 KB, `verification: pass (12 files reopened from a fresh extraction)` |
| `package` re-run without `--force` | exit 5 |
| `diff` identical specs | `0 added, 0 removed, 0 changed` |
| `benchmark` two candidates | Ranked 100.0 (18 slides, 38.9% sourced) above 96.0 (16 slides, 2 errors) |
| `config --show` | Full merged configuration printed |
| `convert` without approval | exit 5 |
| `convert --approve-external-tool --tool no-such-binary-xyz` | exit 4 |
| Generated `handout.pdf` | Re-parsed with a PDF library: 17 pages, extracted text matches slide titles and bullets |
| Generated `deck.pptx` | Re-opened with a PPTX library: 18 slides, 12191695×6858000 EMU (16:9), no XML parse errors in any of the 88 parts, notes text present, real title text on each slide |

### 10.3 Package compatibility checks

- `presentation-makers/` contains **no `SKILL.md`**, no subfolder, and no unprefixed file.
- Every filename starts with `tools-`, `knowledge-`, or `asset-`.
- Exactly one knowledge file: `knowledge-readme.md`. Tool and asset counts follow function
  (8 tools, 12 assets), not a quota.
- Every path is at most two levels below the future skill root
  (`presentation-makers/<file>`), well inside the three-level limit.
- 21 files in total, far below the 500-file ceiling.
- The folder was copied beside a test `SKILL.md` into a clean temporary skill root and re-run using
  only relative paths (see section 14 for the exact commands that were executed).
- The delivery zip contains the `presentation-makers/` folder directly at its root, with no wrapper
  folder and no nested archive; extracting it reproduces the folder immediately.

---

## 11. What was *not* verified

Stated plainly so nothing is overclaimed:

- **Real vendor applications.** PowerPoint, Canva, Keynote, Prezi, Gamma, Pitch, Beautiful.ai,
  Impress, Zoho Show, Visme, Adobe Express, and Figma Slides were not installed or driven. The PPTX
  output was validated structurally and re-opened with a PPTX parsing library; it was not opened in
  Microsoft PowerPoint in this environment.
- **`convert`.** No LibreOffice/`soffice`/`unoconv` binary exists in this environment, so only the
  approval gate (exit 5) and the missing-binary path (exit 4) were exercised. Actual conversion
  output is unverified here and must be checked on a machine that has the converter.
- **Video, GIF, audio, and image rendering.** No encoder or media pipeline is bundled (see 12).
- **Screen-reader, projector, and device testing.** Automated accessibility checks ran; no real
  assistive technology or display hardware was used.
- **External AI providers.** No model API was contacted; only the offline drafting engine ran.

---

## 12. Limits and unavailable integrations

| Area | Status and boundary |
|---|---|
| Vendor UI automation | Not supported by design. Platform categories get profiles, interchange paths, constraint reports, and checklists. |
| PowerPoint VBA / Office Add-ins / UNO macros | Not executed. `convert` is the only process call. |
| Animation and transitions in PPTX | Declared in the spec and enforced by the audit; the PPTX carries sequential content, not authored timelines. |
| Charts and images | Metric cards, tables, phases, and columns render natively. Chart images, photographs, icons, and logos must be supplied; the tool places and checks them, it does not generate them. |
| Video, GIF, MP3, PNG export | Not bundled. Use `convert` with an installed converter, or export from the target application. |
| Fonts | Referenced by family name, never embedded or redistributed. Substitution must be reviewed; the audit checks approved-font compliance when the brief supplies a list. |
| Viewer analytics | Not available offline. Analytics considerations appear in the collaboration coverage notes. |
| Nested archives | Deliberately rejected in packages. |
| AI drafting | Deterministic and offline: extracts headings, points, metrics, and sources from supplied material and drafts notes. It does not invent facts, and `draft` marks generated text for human fact-check in the spec assumptions. |
| Contrast beyond the theme | Checked for the theme pairs actually used (text, secondary text, text-on-accent, accents). Contrast of arbitrary colours inside supplied images cannot be measured. |
| Locale | Text is UTF-8 and RTL/CJK content round-trips through HTML and PPTX, but the PDF handout uses the standard Helvetica family with latin-1 coverage, so non-Latin glyphs are transliterated or replaced there. |
| Scale | Tuned for decks up to the configured 120 slides; very large decks will spend most time in layout and audit. |

---

## 13. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `asset-missing` | An `asset-*` file was moved or renamed | Restore it beside the `tools-*` modules, or point `paths.*` at a copy with `--config` |
| `json-invalid` | Broken JSON in an input file | Fix the syntax; `validate` reports schema problems separately |
| `brief-schema` / `spec-schema` | Field missing, wrong type, or unknown enum value | Run `validate --kind brief|deck-spec --input <file>` for JSON-path diagnostics |
| `content-unparsed` | Content file has no headings and no sentences | Add `## Section` headings and `- ` bullets, or supply the JSON content shape |
| `output-exists` / `package-exists` | Overwrite protection | Re-run with `--force`, or choose another `--outdir` |
| `approval-required` | `convert` without approval | Add `--approve-external-tool` after checking the binary and the input |
| `external-tool-missing` | Converter not on `PATH` | Install it, or use the bundled formats which need no converter |
| `category-not-found` / `skill-not-found` | Filter matched nothing | Run `capabilities` without filters, or shorten the fragment |
| Audit `blocked` | One or more blocking gates have errors | Read `audit-report.md`: each finding carries a `Fix:` hint; re-run `audit` after editing |
| `density.overflow-risk` | Too much text for its box at the minimum type size | Shorten the text, split the slide, or move detail to notes/appendix |
| `a11y.contrast-text` | Theme pair below AA | Adjust the theme colours in the spec (or preset) and re-audit; contrast is recomputed |
| `delivery.build-missing` | Audit pointed at a directory without the declared outputs | Run `build` into that directory, or audit the directory you actually built |
| `delivery.html-external-resource` | Something reintroduced an external reference | Inline the asset; the renderer never emits external references |
| `package-file-invalid` during verify | A packaged file failed re-opening | Rebuild that format, then re-run `package --force` |
| Rendering looks empty in a browser preview | The in-app preview shows a sandboxed iframe; `deck.html` is fully self-contained but needs script permission for navigation | Download and open the file in a normal browser |
| A command stops halfway | Interrupted (`130`) or an OS error | Outputs are written atomically; re-run the command. The package step rebuilds its staging directory on every run |

Recovery principle used throughout: the deck spec is the checkpoint. It is human-readable, validated
against the schema before use, and never destroyed on write failure, so any stage can be re-run from
it. `diff` between saved specs shows exactly what changed between attempts.

---

## 14. Extension and maintenance

**Add a platform.** Append an entry to `asset-platform-profiles.json` with all 16 attribute scores
plus `export_formats`, `requires`, `strengths`, `limits`, and `best_for`. No code change: `brief`
scores whatever is in the asset, and the test suite checks the catalog/coverage pairing, not the
platform list length.

**Add a narrative framework.** Append to `asset-narrative-frameworks.json` with `match`, `priority`,
`default_pacing_seconds`, a `sections` list using existing role ids, and a `call_to_action`
template. `plan` picks it up immediately.

**Add a slide role.** Append to `asset-slide-roles.json`, then map its `layout` to one of the ten
layout patterns in `tools-pm-render.py` (`title`, `section`, `bullets`, `two-column`, `metrics`,
`quote`, `process`, `timeline`, `table`, `closing`) — or add a new branch in `layout_deck()` and
name it in `layouts` in the same asset.

**Add a theme.** Append to `asset-theme-presets.json`; keep all required colour keys, at least four
chart colours, and both font stacks. Run `selftest` — the theme contrast test fails the build if a
pair drops below AA.

**Add or retune a check.** Add the check code to a gate's `checks` array in
`asset-quality-gates.json`, emit it from the matching `check_*` function in `tools-pm-audit.py`, and
record an `audit.ok(code)` in every not-applicable branch so the gate's `checks_evaluated` stays
honest. Unknown codes stay `ungated` and never affect the score.

**Change limits or policy.** Edit `asset-default-config.json`, or ship a new preset and pass
`--config`.

**Regenerate the catalog.** After editing source catalog text:
`python3 tools-presentation-makers.py intake --input <text> --coverage-map asset-coverage-map.json --out asset-skill-catalog.json --force`,
then run `selftest` (it asserts 32/320 and both-way coverage-map agreement).

**Maintenance checklist for any change**

```sh
python3 tools-presentation-makers.py selftest --verbose        # must be all-pass
python3 tools-presentation-makers.py capabilities | head -40    # coverage still correct
python3 tools-presentation-makers.py pipeline --brief asset-example-brief.json \
  --content asset-example-content.md --outdir /tmp/pm-check    # must exit 0
```

Keep module boundaries as they are: shared math and IO belong in `tools-pm-core.py`, layout belongs
in `tools-pm-render.py`, and no renderer may re-implement a check that belongs in
`tools-pm-audit.py`.

**Copying beside a parent `SKILL.md`.** The folder is self-contained; after copying, run this from
the future skill root (POSIX shell) to confirm every relative reference resolves:

```sh
cd <skill-root>
python3 presentation-makers/tools-presentation-makers.py capabilities
python3 presentation-makers/tools-presentation-makers.py pipeline \
  --brief presentation-makers/asset-example-brief.json \
  --content presentation-makers/asset-example-content.md --outdir /tmp/pm-skill-root-check
```

Windows PowerShell equivalent:

```powershell
cd <skill-root>
python presentation-makers\tools-presentation-makers.py capabilities
python presentation-makers\tools-presentation-makers.py pipeline `
  --brief presentation-makers\asset-example-brief.json `
  --content presentation-makers\asset-example-content.md --outdir $env:TEMP\pm-skill-root-check
```

---

## 15. Quick recipes

```sh
# 1. Skeleton in 15 seconds, then look at the plan
python3 tools-presentation-makers.py plan --brief asset-example-brief.json --outdir out
sed -n '1,40p' out/deck-plan.md

# 2. Full production run
python3 tools-presentation-makers.py pipeline --brief asset-example-brief.json \
  --content asset-example-content.md --outdir out

# 3. Only the accessibility gate, strict, machine-readable
python3 tools-presentation-makers.py audit --spec out/deck-spec.json --build-dir out \
  --gate accessible-by-default --strict --json | python3 -m json.tool | head -40

# 4. Compare two revisions for regressions
python3 tools-presentation-makers.py diff --before out-v1/deck-spec.json --after out/deck-spec.json --outdir out/diff

# 5. Strict corporate profile
python3 tools-presentation-makers.py build --spec out/deck-spec.json --outdir out/strict \
  --config asset-default-config.json

# 6. Turn a pasted feature list into a catalog
python3 tools-presentation-makers.py intake --input features.md --out features-catalog.json --force
```
