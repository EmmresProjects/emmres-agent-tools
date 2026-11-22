# powerpoint-agent — knowledge and operating guide

A unified, production **Skill Tool** for creating, editing, auditing, repairing, optimizing,
and delivering Microsoft PowerPoint work. It turns the *PowerPoint Agent Skill Catalog*
(50 categories × 7 execution roles = 350 proposed skills) into one runnable tool with a
command for every catalog operation, deterministic validation, and hard safety gates.

* Skill Tool name: `powerpoint-agent`
* Primary entry point: `tools-pptx-agent.py`
* Runtime: Python 3.9+ (verified on 3.13.16), `python-pptx>=1.0.0`, `Pillow>=10.0.0`
* Output artifacts: editable `.pptx`, JSON/Markdown/CSV reports, PNG previews, preview PDF
* No `SKILL.md` is included: this folder is the reusable Skill Tool component, meant to sit
  beside a parent package's single root `SKILL.md`.

---

## 1. Purpose

Most PowerPoint automation fails in one of five ways: it flattens editable objects into
images, it silently produces files PowerPoint must repair, it cannot say *why* a deck is
bad, it "fixes" things without a record, or it claims capabilities it never implemented.

`powerpoint-agent` is built the other way around:

1. **Editability first.** Everything is written with native PowerPoint objects — text
   frames, real tables, native charts with an embedded workbook, PNG pictures with alt text,
   real section lists. Nothing is rasterized to look editable.
2. **Validate before and after.** Deck specs are schema-validated before a single slide is
   written. Every produced package is reopened with `python-pptx`, structurally validated
   (zip/XML/relationships/content types), and optionally audited before it counts as done.
3. **Explain, don't just fail.** 45 audit rules produce severity, slide, shape, evidence, and
   a fix hint; a quality score and a configurable gate turn findings into an exit code.
4. **Reversible changes.** Repair and optimize run as dry runs by default, write only with
   `--approve`, record before/after for every change, verify the result on a staged copy, and
   then publish. Batch executes against a staging area and publishes only if every job passes.
5. **Only claim what exists.** Every command, rule, fixer, and export format documented here
   was executed during validation (see §11). Faithful PDF/PNG export needs LibreOffice and is
   reported as unavailable when the binary is missing — never faked.

### Capability map vs. the catalog

The catalog's seven roles map 1:1 onto seven runnable operations; the 50 categories are
carried as metadata (via `catalog`) and as content in the builders and rule pack.

| Catalog role | Operation | Command(s) |
| --- | --- | --- |
| Architect | plan | `plan` |
| Builder | build | `build`, `notes --action scaffold`, `sections --action set` |
| Formatter | format | `format`, `replace`, `themes` |
| Auditor | audit | `audit`, `inspect`, `outline`, `validate`, `search`, `compare`, `preview` |
| Repairer | repair | `repair` |
| Automator | automate | `batch`, `export` |
| Optimizer | optimize | `optimize` |
| (cross-cutting) | orchestrate | `deliver`, `catalog`, `doctor`, `capabilities` |

Use `catalog` to find any of the 350 catalog skills and see which operation runs it:

```bash
python tools-pptx-agent.py catalog --action categories
python tools-pptx-agent.py catalog --action list --category 32 --role Auditor
python tools-pptx-agent.py catalog --action show --skill S221
```

`asset-capability-catalog.json` holds all 50 categories, all 350 skill names, the verbatim
descriptions extracted from the source catalog, and the role → operation mapping.

---

## 2. Install and run

### 2.1 Requirements

| Requirement | Version tested | Needed for |
| --- | --- | --- |
| Python | 3.13.16 (3.9+ supported) | everything |
| `python-pptx` | 1.0.2 | every command that reads or writes a deck |
| `Pillow` | 13.0.0 | font metrics (overflow estimates), previews, optimize, preview PDF |
| LibreOffice (`soffice`) | not present in this environment | `export --format pdf`, `export --format png` |
| poppler `pdftoppm` or `mutool` | not present in this environment | `export --format png` |

`lxml` comes with `python-pptx` and is used for the OOXML parts this tool writes directly
(section list, cell borders, fill alpha).

### 2.2 Install

```bash
# from the folder that contains the skill-tool files
python -m pip install -r tools-requirements.txt
```

`tools-requirements.txt` pins the two runtime dependencies with tested upper bounds. There is
no other install step, no build step, and no service to start.

### 2.3 Run

All commands are run from the skill-tool folder, or with an explicit path:

```bash
# bash / zsh (macOS, Linux, WSL, Git Bash)
cd powerpoint-agent
python tools-pptx-agent.py --help
python tools-pptx-agent.py capabilities
python tools-pptx-agent.py doctor

# PowerShell / cmd.exe (Windows)
cd powerpoint-agent
py tools-pptx-agent.py doctor
```

`--help` prints the command list, exit codes, and examples. `capabilities` prints the same
information plus roles, write/approval flags, layouts, themes, dependencies, and exit codes,
and a count of the shipped audit rules; add `--json` for a machine-readable envelope containing
`data.audit_rules` (`count`, `ids`, `categories`) alongside `data.commands`.

### 2.4 First run in 60 seconds

```bash
python tools-pptx-agent.py build --spec asset-deck-spec-example.json --out deck.pptx --audit
python tools-pptx-agent.py audit --in deck.pptx --out audit.json --markdown audit.md --fail-on error
python tools-pptx-agent.py preview --in deck.pptx --out-dir previews --pdf previews/deck.pdf
python tools-pptx-agent.py deliver --spec asset-deck-spec-example.json --out-dir delivery --format preview-pdf
```

Expected on a clean install: the build reports `12 slides`, `Reopened and verified: yes`,
`Audit: score 100.0 · 0 errors · 0 warnings · gate PASS`; the preview writes 12 PNGs plus a
PDF; `deliver` writes a folder containing the deck, audit reports, previews, and a
`delivery-report.md`.

---

## 3. Files in this Skill Tool

Every file sits directly in this folder; there are no subfolders and every file is prefixed
`tools-`, `knowledge-`, or `asset-`.

| File | Prefix | Role |
| --- | --- | --- |
| `tools-pptx-agent.py` | tools | **Primary entry point.** CLI parser, 22 commands, dispatch, exit codes, JSON envelope, error rendering. Contains the plan/build/format orchestration used by the CLI. |
| `tools-deck-core.py` | tools | Shared core loaded by the entry point: dependency detection, structured errors, JSON/atomic IO, config merge, themes, colour math, text metrics, deck-spec schema + validation, the 12 layout builders, deck inspection/outline, section list read/write, package integrity validation. |
| `tools-audit-engine.py` | tools | Rule-pack loader, 45 rule implementations, scoring, gate, Markdown/CSV writers. |
| `tools-repair-engine.py` | tools | Repair planner/executor: 11 fixers, policy loading, staged write, before/after verification, Markdown report. |
| `tools-media-optimizer.py` | tools | Media inventory, in-place recompression/downscale, optional unused-part drop (zip rewrite), verification, report. |
| `tools-render-adapter.py` | tools | Internal Pillow slide renderer (backgrounds, shapes, wrapped text, bullets, images, tables, charts), preview PDF assembly, LibreOffice PDF/PNG adapters with dependency detection. |
| `tools-edit-engine.py` | tools | Format normaliser, search/replace, speaker-note check/scaffold/export, section listing/rewrite, deck comparison. |
| `tools-batch-runner.py` | tools | Manifest validation, in-process dispatch, staging, retries, timeout, publish-or-rollback, batch reports. |
| `tools-requirements.txt` | tools | Dependency declaration (`python-pptx`, `Pillow`) for `pip install -r`. |
| `knowledge-readme.md` | knowledge | This file: the single documentation source. |
| `asset-default-config.json` | asset | Runtime defaults: limits, timeouts, audit thresholds, font allowlist, render/export/batch/safety policy, default theme. |
| `asset-audit-rules.json` | asset | 45-rule pack: id, title, category, severity, fixability, description, fix hint, thresholds, severity overrides, disabled list. |
| `asset-repair-policy.json` | asset | Which rules the repair engine may fix, which need approval, and which are never automatic. |
| `asset-capability-catalog.json` | asset | The 50 categories and 350 catalog skills with verbatim descriptions and role → operation mapping. |
| `asset-brand-preset-corporate-blue.json` | asset | Navy/steel executive theme preset. |
| `asset-brand-preset-editorial-warm.json` | asset | Ivory/serif editorial theme preset. |
| `asset-brand-preset-midnight-teal.json` | asset | Dark conference theme preset. |
| `asset-deck-spec-example.json` | asset | 12-slide reference deck spec: every layout except two, sections, notes, alt text, captions. Doubles as the schema reference. |
| `asset-deck-spec-illustration.png` | asset | 1600×1000 PNG used by the example spec's `image-right` slide. |
| `asset-brief-example.md` | asset | Reference brief for `plan`. |
| `asset-batch-example.json` | asset | 4-job batch manifest (build → audit → inspect → preview) with relative paths. |

**Load order** (flat folder, resolved by exact filename):

1. `tools-pptx-agent.py` (you run this) loads the seven engine modules by exact filename, in
   order: deck core, audit, repair, media, render, edit, batch.
2. `tools-deck-core.py` must load first; the others bind to it (`AUDIT.bind(CORE)`, `REPAIR.bind(CORE, AUDIT)`, …).
3. Assets are read at runtime relative to the skill-tool folder, never from an absolute path.

Do not rename a file without updating the loader list at the top of `tools-pptx-agent.py` and
the asset filenames in `tools-deck-core.py` / `doctor`.

---

## 4. Commands

`python tools-pptx-agent.py <command> [options]` — 22 commands.

| Command | Writes files | Needs `--approve` | Purpose |
| --- | --- | --- | --- |
| `capabilities` | – | – | Command/role/safety/exit-code map, theme presets, audit-rule inventory |
| `doctor` | – | – | Environment, dependency, asset, and backend checks |
| `catalog` | – | – | Search the 50/350 capability catalog |
| `themes` | – | – | List or validate theme presets |
| `plan` | optional JSON | – | Brief → plan, outline, acceptance checks, command sequence |
| `build` | `.pptx` | – | Deck spec → editable deck (optionally audited) |
| `format` | `.pptx` | – | Typography, theme repaint, footer/slide-number normalisation |
| `audit` | JSON/MD/CSV | – | 45-rule audit, score, gate |
| `repair` | `.pptx` (with `--write`) | yes for `--write` | Safe fixes with before/after record |
| `optimize` | `.pptx` (with `--write`) | yes for `--write` | Media recompression + unused-part removal |
| `validate` | optional JSON | – | OOXML package integrity |
| `inspect` | optional JSON | – | Structure, fonts, colours, media, per-slide stats |
| `outline` | MD/JSON/TXT | – | Deck outline |
| `notes` | `.pptx` / `.md` | – | Note coverage, scaffold, export |
| `sections` | `.pptx` (set) | – | List or rewrite sections |
| `search` | – | – | Find text |
| `replace` | `.pptx` (with `--write`) | yes for `--write` | Replace text across runs |
| `compare` | optional MD | – | Diff two decks |
| `preview` | PNG/PDF | – | Approximate slide renders for QA |
| `export` | PDF/PNG | – | Faithful (LibreOffice) or preview export |
| `batch` | many | yes if a job writes | Manifest execution with staging/rollback |
| `deliver` | a folder | yes for repair/optimize stages | End-to-end pipeline |

Global flags accepted by every command:

| Flag | Meaning |
| --- | --- |
| `--json` | Emit one JSON envelope on stdout (`{"ok":…, "command":…, "data":…, "exit_code":…}`) |
| `--config <path>` | JSON config layered on top of `asset-default-config.json` (config wins over rule-pack defaults) |
| `--log-file <path>` | Append JSONL run events (started, steps, findings, finished) |
| `--timeout <seconds>` | Cooperative deadline checked between slides, rules, media parts, and jobs (default 300) |
| `--overwrite` | Allow replacing existing output files |
| `--quiet` | Suppress the human-readable summary (JSON still prints) |

Common input alias: `--in` or `--input` both name the deck.

---

## 5. Exit codes

| Code | Meaning | Typical cause |
| --- | --- | --- |
| 0 | success | – |
| 2 | usage error | missing/invalid arguments, unknown command, bad slide range |
| 3 | dependency | `python-pptx`/`Pillow` missing, LibreOffice or PDF rasterizer absent |
| 4 | validation | missing file, wrong extension, invalid JSON, invalid spec/manifest/theme, failed package validation |
| 5 | gate failed | audit findings at or above `--fail-on`; `deliver` with a failing gate |
| 6 | safety refusal | `--write` without `--approve`, in-place write attempt, unapproved batch write jobs |
| 7 | runtime | timeout, unexpected engine error, batch job failures |
| 130 | interrupted | Ctrl-C before completion |

Scripts should branch on these codes rather than parsing text. With `--json`, failures return
`{"ok": false, "error": {"code", "message", "hint", "details"}}` and the same exit code.

---

## 6. Deck spec schema (`build`, `deliver`, `batch`)

`build` consumes a JSON deck spec. Load it, validate it, write slides. The full working
reference is `asset-deck-spec-example.json`.

```jsonc
{
  "spec_version": "1.0",
  "meta": {
    "title": "Q4 review",              // written to core properties
    "author": "FP&A",                   // optional
    "subject": "Board review",          // optional
    "company": "Acme Analytics",        // stored as category
    "language": "en-US",                // optional, default en-US
    "keywords": ["board", "revenue"]    // optional list of strings
  },
  "theme": {
    "preset": "corporate-blue",         // corporate-blue | editorial-warm | midnight-teal | path to a theme JSON
    "overrides": {                      // optional deep merge onto the preset
      "colors": { "accent": "#1F7A63" },
      "typography": { "body_pt": 17 }
    }
  },
  "slide_size": { "width_in": 13.333, "height_in": 7.5 },   // default 16:9
  "footer": {
    "text": "Acme — Confidential",
    "show_footer": true, "show_slide_number": true, "show_date": false
  },
  "sections": [
    { "name": "Opening", "slide_ids": ["title-q4-review", "executive-summary"] }
  ],
  "slides": [
    { "id": "title-q4-review", "layout": "title",
      "title": "Q4 revenue and retention review",
      "subtitle": "…", "eyebrow": "Board of Directors · 12 Nov 2026",
      "notes": "Speaker notes go here." }
  ]
}
```

### 6.1 Layouts and their fields

| Layout | Required | Optional |
| --- | --- | --- |
| `title` | `title` | `subtitle`, `eyebrow`, `notes`, `background` |
| `section` | `title` | `subtitle`, `notes`, `background` |
| `closing` | `title` | `subtitle`, `notes`, `background` |
| `bullets` / `title-and-content` | `title`, `bullets` | `subtitle`, `aside`, `notes`, `background` |
| `two-column` | `title`, and content in `left` or `right` | `notes`, `background` |
| `kpi` | `title`, 1–4 `kpis` | `subtitle`, `notes`, `background` |
| `quote` | `quote` | `attribution`, `title` (eyebrow), `notes`, `background` |
| `table` | `title`, `table.headers`, `table.rows` | `caption`, `notes`, `background` |
| `chart` | `title`, `chart.categories`, `chart.series` | `caption`, `notes`, `background` |
| `image-right` | `title`, `image` | `bullets`, `caption`, `notes`, `background` |
| `blank` | – | `title`, `notes`, `background` |

Aliases accepted for convenience: `agenda`, `content`, `title-content` → `bullets`;
`two-col`, `columns` → `two-column`; `kpis`, `metrics` → `kpi`; `data-table` → `table`;
`graph` → `chart`; `image`, `picture` → `image-right`; `cover`, `opening` → `title`;
`end`, `thanks` → `closing`; `divider` → `section`.

### 6.2 Field details

* **bullets** – list of strings *or* `{"text": "...", "level": 0-2, "bold": bool}`. Levels
  render with `•`, `–`, `·` and increase the hanging indent. The builder shrinks the font
  automatically (never below 10pt) and records a warning when it had to.
* **kpis** – 1–4 cards: `{"value": "$42.6M", "label": "Net revenue", "detail": "+18% YoY"}`.
  `value` is required.
* **table** – `headers` (non-empty list), `rows` (list of lists, width must equal headers),
  optional `column_widths_in`, optional `first_row_header` (default true → header fill +
  header-row accessibility flag).
* **chart** – `chart_type` ∈ `column`, `column-clustered`, `column-stacked`, `bar`,
  `bar-clustered`, `line`, `pie`, `doughnut`, `area`, `scatter`; `categories`; `series`
  (`[{"name": "FY26", "values": [31.2, 34.8, 38.1]}]`); optional `value_suffix`
  (for example `"M"` or `"%"`) and `show_legend`.
* **image** – `{"path": "asset-deck-spec-illustration.png", "alt": "…", "fit": "contain"}`
  or just a path string. Relative paths resolve against the spec file's folder first, then
  the skill-tool folder. **PNG/JPEG/GIF/BMP/TIFF/WebP only** — python-pptx cannot embed SVG,
  EMF, or WMF; the validator rejects those with an instruction to convert. `alt` is written
  only when supplied, so a missing description genuinely fails `A11Y002` instead of being
  papered over.
* **sections** – `slide_ids` must reference slide `id`s that exist, and a slide may appear in
  at most one section. Sections are written as a real `p14:sectionLst` with the conventional
  `p14` prefix.
* **background** – per-slide `#RRGGBB` canvas override.

### 6.3 Limits enforced before any slide is written

From `asset-default-config.json` → `limits` (override with `--config`):
`max_slides` 400, `max_bullets_per_slide` 60, `max_table_rows` 200, `max_table_columns` 20,
`max_chart_series` 12, `max_chart_categories` 60, `max_text_chars` 4000,
`max_notes_chars` 20000, `max_batch_jobs` 200, `max_input_mb` 300.

Every problem is collected and reported together (never one per run):

```
ERROR [E_VALIDATION] Deck spec validation failed with 6 problem(s).
  - slides[2].bullets must contain at least one bullet.
  - slides[3]: Unknown slide layout 'hero'
  - slides[4].chart.series[1] contains a non-numeric value.
  - slides[5].table.rows[1] has 2 cells but 4 headers.
  - slides[6].image.path does not resolve: missing.png
  - sections[1] references unknown slide ids: quarter-in-numbers
```

---

## 7. Themes (brand presets)

A theme JSON needs `name`, `fonts`, `colors`, `typography`, and `grid` (`validate_theme`
enforces this). Colour values are `#RRGGBB` **or another palette key** (`"surface": "primary"`
is resolved by following references, with cycle protection). Keys used by the builders:

* `fonts` – `heading`, `body`, `mono`
* `colors` – `background`, `surface`, `primary`, `secondary`, `accent`, `accent_soft`, `text`,
  `text_inverse`, `muted`, `border`, `success`, `warning`, `danger`
* `typography` – `title_pt`, `subtitle_pt`, `section_pt`, `heading_pt`, `body_pt`, `small_pt`,
  `kpi_pt`, `quote_pt`, `line_spacing`, `bullet_indent_in`
* `grid` – `margin_x_in`, `margin_y_in`, `title_top_in`, `content_top_in`, `footer_y_in`, `gap_in`
* `art` – `corner_radius_in`
* `table` / `chart` / `footer` – table fills, chart palette, footer switches

Ink colours are chosen by measured contrast: each slide paints its theme background, and text
uses whichever of `text`/`text_inverse` has the higher WCAG ratio against that specific
background, so dark presets never put light text on a light canvas. Accents fall back from
`accent` to `accent_soft` when the primary accent would fail contrast on the canvas.

```bash
python tools-pptx-agent.py themes --action list
python tools-pptx-agent.py themes --action validate --theme midnight-teal
python tools-pptx-agent.py themes --action validate --theme ./my-brand.json
python tools-pptx-agent.py build --spec spec.json --out deck.pptx --theme editorial-warm
```

`--theme` overrides the spec. `asset-brand-preset-*.json` can be copied and edited to make an
organization preset; it will be picked up by `themes --action list` only if named
`asset-brand-preset-<name>.json` and added to the preset map in `tools-deck-core.py:load_theme`.

---

## 8. Audit rules, scoring, and the gate

`audit` runs `asset-audit-rules.json` (45 rules) against a deck. Each finding carries
`rule_id`, `title`, `category`, `severity`, `slide`, `location`, `message`, `fix_hint`,
`fixable`, and `evidence`.

| Category | Rules | Examples of what is caught |
| --- | --- | --- |
| geometry | `GEO001`–`GEO005` | shapes off-canvas, text in the safe-margin band, overlapping text frames, zero-size shapes, **text hidden behind an opaque shape** |
| typography | `TYP001`–`TYP006` | explicit fonts below the minimum, unapproved fonts, >2 families in one box, estimated overflow, empty placeholders, undersized titles |
| colour | `COL001`, `COL002` | WCAG contrast failures (4.5:1, or 3:1 for large text), text identical to its background. The background is the one actually behind the run: its own fill, an opaque card it sits on, or the canvas |
| accessibility | `A11Y001`–`A11Y008` | missing title, missing alt text, title not first in reading order, duplicate titles, table without a header row, untitled chart, missing notes, raw-URL link text |
| structure | `STR001`–`STR005` | empty slide, slide budget, placeholder text ("lorem ipsum", "TODO", "click to add"), overlong title, too many bullets |
| content | `CNT001` | overlong bullets |
| links | `LNK001`–`LNK003` | blocked protocols (`javascript:`, `data:`, `vbscript:`, `file:`), external links, broken slide jumps |
| media | `MED001`–`MED003` | empty media parts, oversized media, images above the pixel target |
| compatibility | `CMP001`–`CMP005` | OLE objects, 3D models, EMF/WMF/SVG, unapproved fonts, Morph transitions |
| performance | `PERF001`–`PERF003` | file over budget, byte-identical duplicate media, unreferenced media |
| security | `SEC001`–`SEC004` | VBA project, external relationships, embedded files/objects, executable or macro-enabled content |

Design details that matter:

* **Colour resolution is real.** Run colours, theme (`schemeClr`) colours, shape fills, and
  slide → layout → master backgrounds are resolved from the package, so contrast checks use
  the colour and background that will actually render. Runs whose colour is inherited are
  counted in `unverified_color_runs` instead of being guessed.
* **Overflow is measured, not eyeballed.** The renderer-style text metrics use real TrueType
  glyph widths when a font file is available, then fall back to conservative per-character
  widths. Findings built from estimates say `"estimated": true`.
* **Furniture is exempt where it should be.** Footers, slide numbers, captions, and eyebrows
  are excluded from minimum-font-size, margin-band, overlap, and furniture-overlap checks
  (they are deliberately small and at the page edge).
* **Chart data workbooks are not "embedded OLE objects".** `Microsoft_Excel_Sheet*.xlsx`
  parts that back native charts are excluded from `CMP001`, `SEC003`, and `PERF003`.

Scoring: start at 100, subtract 8 per error, 3 per warning, 0.5 per info (floor 0). The gate
is `--fail-on error|warning|info|none` (default from config: `error`); the command exits `5`
when the gate fails, and `deliver` inherits that exit code.

```bash
python tools-pptx-agent.py audit --in deck.pptx \
    --out audit.json --markdown audit.md --csv audit.csv --fail-on warning
python tools-pptx-agent.py audit --in deck.pptx --only A11Y001,A11Y002,A11Y005
python tools-pptx-agent.py audit --in deck.pptx --rule-pack ./my-rules.json
```

Thresholds (`min_font_pt`, `min_title_pt`, contrast ratios, margins, budgets, font allowlist,
placeholder patterns) are rule-pack defaults overridden by the `audit` block of your config.

---

## 9. Repair, optimization, and safety

### 9.1 Repair (`repair`)

Plans fixes from audit findings, applies only what `asset-repair-policy.json` allows, records
every change, verifies the result on a temporary copy, and publishes only with `--write
--approve`.

| Rule | Fixer | Approval | Effect |
| --- | --- | --- | --- |
| `TYP001` | raise-font-size | – | Raises explicit small runs to `audit.min_font_pt` |
| `TYP002` | substitute-font | – | Replaces unapproved fonts with the theme body font |
| `TYP003` | normalize-fonts | – | Unifies mixed families in one text box to the dominant one |
| `TYP005` | remove-empty-placeholder | – | Deletes placeholders with no text |
| `COL001` / `COL002` | fix-contrast | – | Recolours low-contrast or invisible text to the readable theme ink |
| `GEO001` | move-inside-slide | – | Nudges shapes inside the canvas |
| `A11Y003` | reading-order | – | Moves the title to the front of the shape tree |
| `A11Y005` | table-header | – | Flags the first table row as a header |
| `A11Y002` | alt-text | **yes** | Generates alt text from the nearest caption or file name (review the wording) |
| `LNK001` | remove-blocked-link | **yes** | Strips blocked hyperlink protocols |

Never automatic (human work, listed in `manual_findings`): `TYP004` (overflow needs copy
editing), `GEO004`, `A11Y001`, `STR001`, `STR003`, `STR004`, `SEC001`, `SEC004`, `CMP001`,
`MED001`, `PERF001`–`PERF003`.

```bash
python tools-pptx-agent.py repair --in deck.pptx --out deck.fixed.pptx --dry-run
python tools-pptx-agent.py repair --in deck.pptx --out deck.fixed.pptx --write --approve --report repair.md
python tools-pptx-agent.py repair --in deck.pptx --out deck.fixed.pptx --rules TYP002,COL001 --dry-run
```

The dry run prints the projected score/counts, every planned change, what still needs a human,
and refuses to touch the input path (in-place repair is blocked outright).

### 9.2 Optimize (`optimize`)

* inventories every `/ppt/media/*` part (format, pixels, bytes, slide references)
* downscales to `--max-dimension` (default 1920) and recompresses in the original format,
  so no relationship or content-type surgery is needed
* keeps every recompressed image decodable (a recompressed blob that will not reopen is
  rejected and the original kept)
* optional `--drop-unused-media` rewrites the zip without parts that **no relationship in the
  whole package** references (media used only by a layout or master is kept)
* always verifies the staged package (reopen, count slides, decode pictures) before publishing
* reports measured savings only for a real write; a dry run reports a projection and says so

```bash
python tools-pptx-agent.py optimize --in deck.pptx --out deck.small.pptx --dry-run
python tools-pptx-agent.py optimize --in deck.pptx --out deck.small.pptx --write --approve \
    --max-dimension 1600 --quality 78 --drop-unused-media --report optimize.md
```

### 9.3 Approval and destructive-action rules

* `repair --write`, `optimize --write`, `replace --write` require `--approve`; without it the
  command exits `6` **before** touching the filesystem.
* No command writes to its own input path. `repair`, `optimize`, `replace`, `format`, and
  `notes --action scaffold`/`sections --action set` refuse in-place writes.
* Outputs are written atomically (temp file in the same folder, then `os.replace`); a crash
  or `Ctrl-C` cannot leave a half-written deck at the destination.
* A batch that contains `repair`, `optimize`, or `replace` jobs requires `--approve`.
* Blocked hyperlink protocols are defined in config `safety.blocked_protocols` and enforced by
  `LNK001` (audit) and the `LNK001` fixer.
* The tool never enables macros, never executes VBA, never evaluates external content, and
  never calls the network. There are no credentials, endpoints, or paid services anywhere.

---

## 10. Every capability, with examples

### 10.1 Plan (Architect)

```bash
python tools-pptx-agent.py plan --brief asset-brief-example.md --out plan.json --narrative exec-brief
python tools-pptx-agent.py plan --brief-text "Goal: pitch a data platform. Audience: CTOs. 8 slides." --narrative sales
```

Parses goal, audience, constraints, success criteria, deliverable, brand, length, and "avoid"
lines (any heading style, Markdown or plain text), then emits: a narrative arc (`exec-brief`,
`training`, `research`, `sales`, `general`), one outline slot per slide with layout, purpose,
catalog category and the seven catalog skills for that category, acceptance checks derived
from the **live** rule pack, risks, warnings (missing audience, budget/template mismatch), and
the exact command sequence. Output is printed and optionally written as JSON.

### 10.2 Build (Builder)

```bash
python tools-pptx-agent.py build --spec asset-deck-spec-example.json --out deck.pptx --audit
python tools-pptx-agent.py build --spec spec.json --out deck.pptx --theme midnight-teal
python tools-pptx-agent.py build --spec appendix.json --out combined.pptx --into deck.pptx
```

Validates → builds → saves atomically → reopens → verifies slide count, titles, sections, size
→ optional audit. With `--into`, the spec's slides are appended to an existing deck (sections
are renumbered).

### 10.3 Format (Formatter)

```bash
python tools-pptx-agent.py format --in deck.pptx --out deck.formatted.pptx --theme corporate-blue
python tools-pptx-agent.py format --in deck.pptx --out deck.formatted.pptx \
    --keep-fonts --min-font-size 12 --footer-text "Acme — Internal" --no-slide-numbers
python tools-pptx-agent.py format --in deck.pptx --out dark.pptx --theme midnight-teal   # repaint solids
python tools-pptx-agent.py format --in deck.pptx --out dark.pptx --theme midnight-teal --keep-backgrounds
```

Applies theme typography (heading/body fonts unless `--keep-fonts`), raises explicit sizes
below `--min-font-size`, standardises body margins, removes empty placeholders, and adds a
footer and slide numbers when missing (never duplicates them). It then reopens the result and
reports per-slide change counts.

Geometry is repaired, not redesigned: a title box is only moved when it is objectively broken
(off-canvas, overflowing, no usable size, stranded far below the grid, or indented more than
0.75 in from the margin). A valid title-slide placement or a narrow measure beside an accent
panel is left exactly as designed, and a box is never widened past an opaque panel to its right.

Backgrounds are handled by `--repaint-backgrounds` / `--keep-backgrounds`; the default `auto`
repaints only plain solid slide-level fills, so a solid-colour deck follows the theme while a
picture or gradient background is preserved. When a canvas is repainted, each run's ink is
re-measured against whatever is actually behind it — its own shape fill, the opaque card it
sits on, or the canvas — and only runs that fall below 4.5:1 are re-inked. The summary line
reports `Backgrounds repainted: N · kept: N · text colours adjusted: N`.

### 10.4 Audit (Auditor)

See §8. Human summary, `--out` JSON, `--markdown`, `--csv`, `--only`, `--rule-pack`,
`--fail-on`. Exit `5` on gate failure.

### 10.5 Inspect / Outline / Validate / Search / Compare

```bash
python tools-pptx-agent.py inspect --in deck.pptx --out stats.json
python tools-pptx-agent.py outline --in deck.pptx --format md --out outline.md
python tools-pptx-agent.py outline --in deck.pptx --format json --out outline.json
python tools-pptx-agent.py validate --in deck.pptx --out validation.json
python tools-pptx-agent.py search --in deck.pptx --pattern "retention" --case-sensitive
python tools-pptx-agent.py search --in deck.pptx --pattern "retention|expansion" --regex
python tools-pptx-agent.py compare --a old.pptx --b new.pptx --markdown compare.md
```

`validate` is the package-integrity gate: zip integrity, `[Content_Types].xml`,
well-formedness of every XML part, every relationship target present, every part with a
declared content type, orphan parts, and whether `python-pptx` can reopen the file. Exit `4`
when errors are found.

### 10.6 Repair / Optimize

See §9.

### 10.7 Notes (Builder)

```bash
python tools-pptx-agent.py notes --in deck.pptx --action check
python tools-pptx-agent.py notes --in deck.pptx --action scaffold --out deck.notes.pptx
python tools-pptx-agent.py notes --in deck.pptx --action export --out notes.md
```

`check` reports coverage, total words, and estimated speaking time at 130 wpm. `scaffold`
writes a structured `TITLE / TALKING POINTS / TRANSITION / TIMING` skeleton to slides that
have no notes — it is a scaffold, not generated prose. `export` writes `notes.md`.

### 10.8 Sections (Builder)

```bash
python tools-pptx-agent.py sections --in deck.pptx --action list
python tools-pptx-agent.py sections --in deck.pptx --action set \
    --map "Opening:1-3;Evidence:4-8;Plan:9-11;Close:12" --out deck.sections.pptx
python tools-pptx-agent.py sections --in deck.pptx --action set --sections-json sections.json --out deck.sections.pptx
```

Writes a genuine `p14:sectionLst` (conventional `p14` prefix, GUID ids, slide-id references).
Validates ranges, unknown slides, and duplicate assignment before writing.

### 10.9 Replace (Formatter)

```bash
python tools-pptx-agent.py replace --in deck.pptx --pattern "Acme Analytics" --replacement "Acme"
python tools-pptx-agent.py replace --in deck.pptx --pattern "Acme Analytics" --replacement "Acme" \
    --write --approve --out deck.renamed.pptx
python tools-pptx-agent.py replace --in deck.pptx --pattern "(?i)fy27" --replacement "FY27" --regex
```

Run-level replacement preserves formatting. When a phrase is split across runs,
`--paragraph-fallback` (default on) rewrites the paragraph and **reports that formatting
collapsed into the first run** instead of silently losing it.

### 10.10 Preview and export (Auditor / Automator)

```bash
python tools-pptx-agent.py preview --in deck.pptx --out-dir previews --slides 1-4,7 --width 1600 --pdf previews/deck.pdf
python tools-pptx-agent.py export --in deck.pptx --out deck.pdf --format pdf          # LibreOffice
python tools-pptx-agent.py export --in deck.pptx --out deck.pdf --format preview-pdf  # offline
python tools-pptx-agent.py export --in deck.pptx --out pages --format png --dpi 150   # LibreOffice + poppler
python tools-pptx-agent.py export --in deck.pptx --out pages --format preview-png --width 1200
```

The internal renderer draws theme backgrounds, solid and rounded shapes, images, tables,
charts (clustered/stacked columns, bars, lines, areas, pie/doughnut with legend, axis values,
category labels and data labels), text with real font metrics, wrapping, alignment, anchored
frames, bullets and hanging indents — enough to review hierarchy, overflow, and contrast.
It is explicitly **approximate**: no SmartArt, transitions, animations, 3-D, or effects, and
typography will differ from PowerPoint. Faithful output requires LibreOffice; when the binary
is absent the command exits `3` with install guidance rather than producing a fake "PDF".

### 10.11 Batch (Automator)

```bash
python tools-pptx-agent.py batch --manifest asset-batch-example.json --out-dir batch-out --dry-run
python tools-pptx-agent.py batch --manifest asset-batch-example.json --out-dir batch-out
# --out-dir is required in practice: outputs are rebased there and the skill folder is guarded
```

Manifest shape:

```jsonc
{
  "manifest_version": "1.0",
  "options": {
    "stop_on_error": true,      // stop after the first failure
    "retries": 0,                // retries per job
    "timeout_seconds": 240,      // cooperative per-job timeout
    "stage_outputs": true,       // write into a staging folder first
    "publish_at_end": true       // publish only when every job passes
  },
  "jobs": [
    { "id": "build", "command": "build", "args": { "spec": "spec.json", "out": "out/deck.pptx" } }
  ]
}
```

* Input paths inside a manifest are resolved **relative to the manifest file**, not the shell's
  working directory, so a manifest stays portable. A later job that consumes an earlier job's
  output follows it into `--out-dir` automatically.
* Output paths resolve against `--out-dir` when it is given (falling back to the manifest's own
  folder). Always pass `--out-dir`: a batch whose outputs would land inside the skill folder is
  refused with exit `2`, so no run can leave artifacts next to the tool files.
* Output arguments (`out`, `out_dir`, `markdown`, `csv`, `pdf`, `report`, `json_report`,
  `audit_out`) are detected automatically and staged; `outputs` can also map arg → final path
  explicitly.
* Jobs run in-process through the same parser and handlers as the CLI — no shell, no
  interpolation, no injection surface.
* If any job fails with staging on, **nothing is published**; the staged artifacts are
  deleted and the batch exits `7`. Fix the manifest/job and rerun.
* Writes `batch-log.jsonl`, `batch-summary.json`, and `batch-summary.md` in `--out-dir`.

### 10.12 Deliver (Orchestrator)

```bash
python tools-pptx-agent.py deliver --spec asset-deck-spec-example.json --out-dir delivery --format preview-pdf
python tools-pptx-agent.py deliver --input deck.pptx --out-dir delivery --theme editorial-warm \
    --repair --optimize --approve --format pdf
```

Stages: build (or intake [+ format when `--theme` is given]) → audit → optional repair →
optional optimize → final audit → previews → optional export → `delivery-report.md`. Produces
one folder with the deck, `audit.json`, `audit.md`, `repair.md`, `optimize.md`, `previews/`,
the export, and the report. `--repair` / `--optimize` require `--approve`; without it those
stages are recorded as `skipped` and a warning is added. Exits `5` if the final gate fails.

### 10.13 Meta commands

```bash
python tools-pptx-agent.py capabilities [--json]
python tools-pptx-agent.py doctor
python tools-pptx-agent.py catalog --action categories
python tools-pptx-agent.py themes --action list
```

`doctor` checks Python, `python-pptx`, Pillow, font-file availability, LibreOffice, PDF
rasterizer, all nine bundle assets, catalog integrity (50/350), rule-pack implementation
coverage (45/45), theme validity, example-spec validity, and whether the skill folder is
writable. It exits `3` if any check fails.

---

## 11. Validation actually performed

Everything below was executed in this environment (Linux, Python 3.13.16, python-pptx 1.0.2,
Pillow 13.0.0) against the delivered files. LibreOffice and poppler are **not installed**, so
faithful export was tested for its failure path only.

| # | Test | Result |
| --- | --- | --- |
| 1 | `--help`, `capabilities`, `capabilities --json` | Command list, 22 commands with roles/approval, exit codes, layouts, themes |
| 2 | `doctor` | 14 checks, 0 failures, 2 expected warnings (LibreOffice, poppler) |
| 3 | `themes --action list` / `validate` (3 presets + custom path + invalid theme) | Valid presets pass; invalid theme reports 2 problems and exits 4 |
| 4 | `catalog` categories/list/show (`S221`, `S350`, category 32, role filter) | 50 categories, 350 skills, correct role → operation mapping |
| 5 | `plan` from `asset-brief-example.md` (exec-brief) | Goal/audience/constraints parsed, 10-slot outline, 12 acceptance checks, JSON written |
| 6 | `build` of the 12-slide example spec | Built, reopened, verified, sections read back (4 sections), audit 100.0 / gate PASS |
| 7 | `build` of an 11-slide edge-case spec (10×7.5 in canvas, midnight-teal, multi-level bullets, pie/bar charts, 6-column table, two-column, 3 KPIs, quote, blank, `image-right` without alt, closing) | Built, reopened, audit 99.5 with the expected single info-level finding (`A11Y007`) and warnings for 4:3 canvas + missing alt text |
| 8 | `build --into` an existing 5-slide deck | 17 slides, appended spec sections correct; pre-existing untitled slide reported as an issue |
| 9 | Invalid spec (missing bullets, unknown layout, non-numeric chart values, row/header mismatch, missing image, unknown section id) | 6 problems aggregated in one error, exit 4, no output file created |
| 10 | Invalid JSON, missing input, wrong extension, existing output without `--overwrite` | Exit 4 with actionable hints in every case |
| 11 | `--json` envelopes for success and failure | `{"ok": true, …}` and `{"ok": false, "error": {code, message, hint, details}}` |
| 12 | `audit` on a deliberately broken 5-slide deck (off-canvas shapes, 9pt low-contrast Papyrus text, "click to add" placeholder, empty slide, table without header row, duplicate title, 220-char bullet, `javascript:` hyperlink) | 9 errors / 7 warnings / 3 info, score 5.5, gate FAIL exit 5; JSON + Markdown + CSV written and inspected |
| 13 | `audit --only`, `--config` threshold override, `--fail-on warning` | Only requested rules run; config overrides rule-pack defaults; gate exit 5 |
| 14 | `repair --dry-run` on the broken deck | 10 planned changes across 7 rules, projected 5.5 → 64.0, 9 manual items listed, nothing written |
| 15 | `repair --write` without `--approve` | Refused, exit 6, no file |
| 16 | `repair --write --approve` on the broken deck | 10 changes with before/after record; independent re-audit confirms 9 errors → 4 errors; `javascript:` link stripped; `repair.md` inspected |
| 17 | `optimize --dry-run` / `--write` without `--approve` / `--write --approve` on a deck with a 4000×2500 PNG | Dry run reports a projection only; unapproved write refused (exit 6); approved write 252.1 KB → 185.9 KB (26.3%), staged copy reopened, picture decoded |
| 18 | `optimize --drop-unused-media` on the built deck | Chart workbooks preserved (correctly treated as referenced), 8.2% measured saving, verified |
| 19 | `format` (theme swap to editorial-warm; then `--keep-fonts --no-footer --no-slide-numbers --min-font-size 12`) | 12/12 slides touched, theme re-verified by audit at 100.0; variant flags honoured |
| 19a | `format --theme midnight-teal` on the built deck (dark canvas over light cards) | 12/12 canvases repainted, 43 runs re-inked, audit 100.0; KPI card values stayed dark on their white cards, title-slide placement and the centred closing title were left untouched, and no title ran under the accent panel — all confirmed by reading the rendered previews |
| 19b | `format --theme midnight-teal --keep-backgrounds` on the same deck | 0 repainted / 12 kept, text left alone, audit 100.0 |
| 19c | `format` on the broken third-party deck | 2 broken title boxes repaired, 1 empty placeholder removed, 5 slide numbers added, score 5.5 → 32.5 |
| 20 | `replace` dry run → unapproved write (exit 6) → approved write | 12 replacements, `compare` shows the changed word count |
| 21 | `search` (literal, case-sensitive, regex) | Literal and regex both match; counts and locations correct |
| 22 | `notes check` / `scaffold` / `export` | Coverage %, speaking time, 5 scaffolds written and re-checked at 100%, Markdown export |
| 23 | `sections list` / `set --map` / `set --sections-json` | Lists 4 sections; both writers produce correct `p14:sectionLst` (verified in `presentation.xml`) |
| 24 | `inspect`, `outline` (md/json/txt), `compare` | Stats, fonts, colours, media, sections, per-slide table; outline in all three formats; comparison deltas |
| 25 | `validate` on built, repaired, and optimised decks | PASS (229 checks on the 12-slide deck): zip, XML parts, relationships, content types, orphans |
| 26 | `preview` (all slides, slide subset, `--pdf`) | 12 PNGs at 1280 px plus a 12-page PDF; every preview opened and visually inspected during development, which is how the background-over-title z-order bug and the missing bullet glyphs were found and fixed |
| 27 | `export --format pdf` and `--format png` without LibreOffice | Dependency error with install guidance, exit 3, no fake file |
| 28 | `export --format preview-pdf` / `preview-png` | Offline PDF (12 pages) and PNG set produced |
| 29 | `batch --dry-run` and real run of `asset-batch-example.json` | 4/4 jobs succeeded (build, audit, inspect, preview), summaries written under `--out-dir`, chained inputs followed the relocated outputs |
| 29a | `batch` without `--out-dir`, run from inside the skill folder | Refused with exit 2 before anything ran; folder left byte-for-byte unchanged |
| 30 | Batch with a failing job, staging on | 2 succeeded / 1 failed, **nothing published**, exit 7; after removing the bad job, 2/2 published |
| 31 | `deliver --spec … --format preview-pdf --repair --optimize --approve` | 6 stages ok, final audit 100.0 PASS, 8 artifacts + delivery report |
| 32 | `deliver --input …` (existing deck, gate failure path) | Stages recorded, gate FAIL surfaced, exit 5, audit.md lists the error |
| 33 | `--timeout 0.05` on a 12-slide preview | Cooperative timeout, exit 7, partial files cleaned by the caller |
| 34 | `--log-file` | JSONL run log with started/finished events |
| 35 | Corner cases exercised by the above | missing/empty text, non-ASCII (…, ·, “), 4:3 canvas, dark theme, 6 columns, 4 KPIs rejected at 5, chart types pie/bar/line/column, image without alt text, phrase split across runs, duplicate slide ids |
| 35a | Contrast measured on stacked shapes | A white KPI card with dark text inside a dark deck reports no `COL001`; the same rule still fires at 1.09:1 for genuinely dark-on-dark text in the broken deck |
| 35b | `repair --dry-run` on a clean 100.0 deck | Reports "No fixable findings" with the score, and states that nothing was written |
| 36 | Independent XML inspection | Bullet `a:pPr` child order (`lnSpc`, `spcBef`, `spcAft`, `buFont`, `buChar`) and cell `a:tcPr` order (`lnL`, `lnR`, `lnT`, `lnB`, `solidFill`) conform to the DrawingML schema |
| 37 | Packaging | Flat folder, prefixes verified, no `SKILL.md`, no subfolders, ZIP root contains the folder, extract-and-rerun verified |
| 38 | Clean-root retest | Folder copied beside a test `SKILL.md`, then `doctor`, `capabilities`, `build`, `validate`, `audit`, `preview`, `batch`, `deliver` run with relative paths only; 21 files unchanged, no `__pycache__`, no stray outputs |

Not tested (and not claimed): rendering fidelity inside Microsoft PowerPoint, LibreOffice
conversion, poppler rasterization, SmartArt/3-D/animations, password-protected or `.ppt`
(binary) inputs, and Windows/macOS font-fallback behaviour. See §12 and §13.

---

## 12. Limits and unavailable integrations

**Runtime limits**

* `python-pptx` cannot author SmartArt, Morph transitions, animations, motion paths, 3-D
  models, ink, cameo recordings, or embedded video playback. The audit *detects* several of
  these in existing decks (`CMP002`, `CMP005`, `CMP003`) but the builder cannot create them.
  Requests that need them are out of scope for this Skill Tool and must be done in PowerPoint.
* Only `.pptx`, `.pptm`, `.potx`, `.ppsx` inputs are accepted, and only `.pptx` is written.
  Binary `.ppt` and password-protected decks are rejected with a clear error.
* Only raster images can be embedded (PNG/JPEG/GIF/BMP/TIFF/WebP). SVG/EMF/WMF must be
  converted first; the validator says so explicitly.
* Speaker notes are written as text. Notes masters, handouts, and print layouts are not
  authored.
* Text metrics are approximations; overflow findings are heuristic (flagged
  `"estimated": true`). Real overflow can only be confirmed by rendering.
* Cooperative timeouts are checked between units of work (slides, rules, media parts, jobs);
  a single long `python-pptx` save cannot be interrupted mid-call.

**Unavailable integrations (configurable, not implemented as live services)**

* **Faithful PDF/PNG export** needs a local LibreOffice/`soffice` binary. It is not installed
  here, so only the dependency-detection path was tested. Configure `export.libreoffice_binary`
  (or put `soffice` on `PATH`) and the same `export --format pdf|png` commands will use it.
* **PNG export** additionally needs `pdftoppm`, `pdftocairo`, or `mutool`.
* No cloud service, model API, license server, or device is used or required. Nothing in this
  tool calls the network, and no credentials exist anywhere in the package.

**Deliberate non-goals**

* It does not generate prose. `plan` structures a brief; `notes --action scaffold` writes an
  outline skeleton. Copywriting stays with the author.
* It does not promise a pixel-perfect render. The internal renderer is for QA triage.

---

## 13. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ERROR [E_DEPENDENCY] python-pptx is required …` | Dependency not installed for this interpreter | `python -m pip install -r tools-requirements.txt`; confirm with `python tools-pptx-agent.py doctor` |
| `FATAL: missing required skill-tool module: tools-*.py` | Files were moved out of the flat folder or renamed | Restore the exact filenames beside `tools-pptx-agent.py` |
| `ERROR [E_DEPENDENCY] Faithful PDF export needs a LibreOffice/soffice binary` | LibreOffice absent | Install it, set `export.libreoffice_binary`, or use `--format preview-pdf` |
| `ERROR [E_DEPENDENCY] PNG export needs a PDF rasterizer` | poppler/mutool absent | Install `poppler-utils`, or use `--format preview-png` |
| `ERROR [E_VALIDATION] Deck spec validation failed with N problem(s)` | Spec schema/content problems | Read each listed line; compare with `asset-deck-spec-example.json` |
| `ERROR [E_VALIDATION] Output … already exists and --overwrite was not supplied` | Refusing to clobber | Pass `--overwrite` or pick a new path |
| `ERROR [E_SAFETY] … requires --approve` | Write command without approval | Review the dry run, then rerun with `--approve` |
| `ERROR [E_SAFETY] … must write to a new file` | Output path equals input path | Choose a different `--out` |
| `ERROR [E_GATE]`/exit 5 | Audit gate failed | Read the findings, fix, or relax `--fail-on` |
| Exit 7 with a timeout message | `--timeout` too small for the deck | Raise `--timeout`, describe fewer slides, or split the deck |
| `doctor` reports `warn` on font-metrics | No TrueType font found | Install DejaVu/Liberation fonts or set `render.font_search_paths` |
| `doctor` reports `warn` on `pdf-rasterizer` / `libreoffice` | Optional backends missing | Only needed for faithful export |
| Bullets look cramped in PowerPoint after `repair` raised font sizes | Larger text can overflow the frame | Re-run `audit` and fix `TYP004` by shortening copy |
| A preview looks different from PowerPoint | Internal renderer is approximate | Use `export --format pdf` with LibreOffice for fidelity |
| `validate` reports missing relationship targets | Package was hand-edited or truncated | Recover in PowerPoint, or rebuild from the spec |

**Recovery**

* Every write is atomic: on failure the destination is untouched and temp files are removed.
* `repair` and `optimize` never modify the input; re-run against the original at any time.
* A failed `batch` publishes nothing (with staging on) — the staging folder is discarded.
* Repair records `before`/`after` for each change in `repair.md` / `--json-report`, so a
  rejected fix can be identified and reversed by re-running from the original file.
* `compare` quantifies the difference between the original and a produced file.

**Extension**

* New audit rule: add the entry to `asset-audit-rules.json` and a `rule_<id>` function in
  `tools-audit-engine.py`, then register it in `RULE_FUNCTIONS`. `doctor` will flag any
  declared-but-unimplemented rule.
* New fixer: implement `fix_<name>(state, finding) -> (applied, before, after, note)` in
  `tools-repair-engine.py`, add it to `FIXERS`, and enable it in `asset-repair-policy.json`
  (set `requires_approval` for anything approximate).
* New layout: add the layout to `LAYOUTS`, write `_layout_<name>` in `tools-deck-core.py`, and
  register it in `LAYOUT_BUILDERS`.
* New theme: copy a preset, edit colours/fonts/typography, validate with
  `themes --action validate`, then reference it by path with `--theme`.
* New config knob: add it to `DEFAULT_CONFIG` in `tools-deck-core.py` and to
  `asset-default-config.json`; read it with `CORE.cfg(config, "dotted.key", default)`.

**Maintenance**

* Keep the rules pack and the engine in sync — `doctor` enforces it.
* Re-run the §11 smoke sequence after any change:
  `doctor → build+audit → audit → repair dry-run → preview → deliver`.
* The catalog asset is a snapshot of the source catalog; regenerate it if the catalog changes.

---

## 14. Design notes worth knowing

* **Why Python.** `python-pptx` is the only mature, open, dependency-light library that reads
  *and* writes native PowerPoint objects (text frames, tables, native charts with workbook
  data, pictures, notes, section lists) without a Microsoft Office install. Pure OOXML hand
  authoring would be far more code for the same result, and VBA/Office.js would require
  PowerPoint or a tenant — neither is available or appropriate for an offline Skill Tool.
* **Why one tool per role.** Each engine is loaded by path from a flat folder, so a future
  parent skill can copy this folder next to `SKILL.md` unchanged. Splitting by role keeps
  audit read-only, repair reversible, and batch sandboxed, and lets a maintainer test one
  engine without the rest.
* **Why z-order bugs are caught by a rule.** During development the preview showed the title
  slide with no title: a full-bleed background rectangle had been added *above* the title
  placeholder. Fixing the builder was not enough, so `GEO005` now detects any text hidden
  behind an opaque shape. Rules exist to encode lessons like this.
* **Why contrast is computed per slide.** Dark themes initially produced light text on a white
  default background because only `slide.background` was set on some layouts. The fix was to
  paint the theme canvas on every slide and choose ink by measured contrast; the audit then
  verified it. This is why `COL001` resolves backgrounds through slide → layout → master, and
  then through the shapes stacked underneath the text: a run on a white KPI card inside a dark
  deck is measured against the card, not against the slide canvas.
