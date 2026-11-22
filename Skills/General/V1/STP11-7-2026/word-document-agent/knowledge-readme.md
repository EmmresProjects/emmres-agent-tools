# word-document-agent — knowledge and operations guide

A reusable **Skill Tool** component (no `SKILL.md`) that turns the
*Microsoft Word Agent Skill Catalog* — 53 categories, 636 candidate skills —
into one working, offline, standard-library-only tool for Microsoft Word
OOXML packages (`.docx`, `.docm`, `.dotx`, `.dotm`).

The tool creates documents, edits and restructures them, inspects and
sanitises the real package, runs review and release gates, exports
artefacts, keeps last-known-good backups, and answers catalog-routing
questions. Anything that cannot be done safely to the file itself is
returned as a validated, verifiable **procedure** (`plan`) instead of a
false claim of automation.

---

## 1. What this skill tool is and is not

**Is**

- One unified tool for every Word capability that is representable in the
  OOXML package: creation, structure, text, tables, fields, TOC, headers and
  footers, images, content controls, comments, tracked changes, protection,
  metadata, sanitisation, mail merge, conversion and export.
- A read-only auditor that reports what a Word file really contains:
  hidden text, personal metadata, revisions, external relationships,
  macros, field codes, section geometry, style usage, accessibility defects
  and proofing defects.
- A release gate (`qa`) that combines integrity, structure, accessibility,
  proofing and review state into a pass/fail verdict with next actions.

**Is not**

- A Word UI automation layer. There is no COM, AppleScript, VBA or
  keyboard automation, and no cloud/Microsoft 365 Graph calls.
- A renderer. It never paginates, does not evaluate field results, and does
  not rasterise pages. Field-heavy and pagination-heavy results are marked
  for update in Word (see §11).
- A security tool. It detects and removes hidden data per policy; it does
  not encrypt packages, validate digital signatures or sandbox macros.
- A complete Copilot skill. It is the Skill Tool component that a future
  `SKILL.md` package copies beside its root `SKILL.md` unchanged.

---

## 2. Install, requirements, exact commands

### Requirements

| Item | Requirement | Verified in this environment |
| ---- | ----------- | ---------------------------- |
| Python | 3.9 or newer (3.9+ features: PEP 584 dict merge, `str \| None` annotations under `from __future__`) | Python 3.13.16 |
| Third-party packages | **none** — standard library only (`zipfile`, `xml.etree.ElementTree`, `hashlib`, `csv`, `json`, `re`, `argparse`, `subprocess` for the optional PDF adapter) | no packages installed for the tool |
| Word | not required to run any operation | not present; validated with `python-docx` as an independent reader |
| OS | Windows, macOS or Linux (paths are handled with `os.path`; no shell is used except the optional PDF adapter) | Linux sandbox |

`asset-default-config.json` is the only file the tool needs beyond its
`tools-` files; the other two assets are read by inspection, routing and
scoring.

### Commands

Run from the folder that contains the tool files (or prefix the path):

```shell
# help and capability inventory
python3 tools-word-doc-agent.py --help
python3 tools-word-doc-agent.py capabilities
python3 tools-word-doc-agent.py capabilities --param all=true --json

# create, inspect, gate
python3 tools-word-doc-agent.py create --output brief.docx \
    --param markdown-file=brief.md --param title="Q3 Brief"
python3 tools-word-doc-agent.py inspect --input brief.docx --json
python3 tools-word-doc-agent.py qa --input brief.docx

# self-test (152 assertions, drives the real CLI)
python3 tools-selftest.py
python3 tools-selftest.py --verbose --workdir /tmp/wda-tests
```

Windows `cmd.exe` / PowerShell use the same arguments; only the
interpreter name changes (`py -3` or `python`). Always name the shell when
scripting: `bash`, `zsh`, `cmd.exe`, `powershell`.

### File inventory and load order

Every file sits directly in this folder. Nothing is nested.

| File | Role | Loaded by |
| ---- | ---- | --------- |
| `tools-word-doc-agent.py` | **Primary entry point**: argument parsing, operation dispatch, approval gate, JSON envelope, human summaries, exit codes | run directly |
| `tools-docx-core.py` | Shared foundation: namespaces, OOXML package model (`Docx`), config loading, limits, deadlines, atomic writes, integrity report, field/text helpers | entry point, then every other module |
| `tools-docx-build.py` | Element constructors: paragraphs, runs, fields, tables, lists/numbering, images, content controls, watermarks, headers/footers, blank package + style set | structure, and therefore audit/edit |
| `tools-docx-audit.py` | Read-only inspection: profile, hidden data, risk, accessibility rules, proofing rules, release gate, catalog scoring | entry point via bootstrap; edit (policy, inventories) |
| `tools-docx-structure.py` | Structural operations: create, insert, format, table, image, headerfooter, watermark, toc, form, list | entry point |
| `tools-docx-edit.py` | Content/metadata/state operations: replace, metadata, styles, fields, comments, revisions, sanitize, protect, merge, copy, convert, backup/restore, plan/route | entry point |
| `tools-docx-export.py` | Export renderers: txt, md, html (inline CSS + base64 images), json, pdf adapter | entry point |
| `tools-selftest.py` | Runnable end-to-end suite over the real CLI, plus package-shape checks | run directly |
| `knowledge-readme.md` | This guide | human/agent reading |
| `asset-default-config.json` | Default configuration: paths, limits, external adapters, approval list, defaults, policy filenames | entry point at startup |
| `asset-inspection-policy.json` | Policy data: sanitisation targets, accessibility rules, proofing checks, release-gate thresholds, risk severities | `inspect`, `sanitize`, `a11y`, `proof`, `qa` |
| `asset-capability-map.json` | The catalog compiled into routing data: 53 categories, 636 skills, each mapped to one implementing operation with parameters, engine (`native`/`advisory`), triggers and category surfaces | `capabilities`, `route`, `plan`, `score` |

Loading order is enforced by a small bootstrap inside each module
(`_bootstrap(module_name, filename)`), because the packaged filenames
contain hyphens and are therefore not importable by their own names:
`tools-docx-core.py` → `tools_docx_core`, `tools-docx-build.py` →
`tools_docx_build`, and so on. Modules are imported lazily, so a
capabilities query never loads the editing code.

---

## 3. Operations

33 operations. Each has a purpose, configurable inputs, validated
parameters, structured output, and a documented failure mode. Run
`capabilities` for the live list. `python3 tools-word-doc-agent.py <op> --help`-style
guidance is not used; the per-operation usage is below and in
`capabilities`.

Common flags:

| Flag | Meaning |
| ---- | ------- |
| `--input/-i FILE` | Input package; validated (exists, is a file, `.docx/.docm/.dotx/.dotm`, under `limits.max_input_bytes`) |
| `--output/-o PATH` | Output package path, or a directory for `merge`; created if the parent is missing; existing files are refused unless in-place |
| `--param/-p key=value` | Operation parameter, repeatable; `true/false`, integers and decimals are coerced |
| `--config FILE` | JSON merged over `asset-default-config.json` |
| `--json` | One JSON envelope on stdout |
| `--quiet` | Suppress the human summary |
| `--approve` | Approve a destructive/publishing/in-place action |
| `--in-place` | Write back onto `--input` (requires `--approve`) |
| `--dry-run` | Report what would change; no output written |
| `--report-file FILE` | Also write the JSON envelope to `FILE` |
| `--timeout SECONDS` | Time budget; cooperative checkpoints abort with `LIMIT` |

### 3.1 Document creation and lifecycle

| Operation | What it does | Key parameters | Output |
| --------- | ------------ | -------------- | ------ |
| `create` | New package from Markdown or JSON blocks; writing a valid `[Content_Types].xml`, rels, `document.xml`, style set, `settings.xml`, core/app properties; optional template style copy and self-check | `markdown`, `markdown_file`, `content`, `content_file` (JSON), `format=docx\|docm\|dotx\|dotm`, `title`, `author`, `template`, `page_size`, `orientation`, `margins`, `header`, `footer` | `written`, `bytes`, `format`, `blocks`, `self_check` (integrity of the saved file) |
| `copy` | Save-a-copy branch: bumps revision, refreshes modified date, optional title suffix/anonymise | `title_suffix`, `stamp_title`, `anonymize` | `written`, `sha256`, `title` |
| `convert` | Switch package type (`docx↔docm↔dotx↔dotm`) by rewriting the main content type; warns when macro parts would survive | `format=docx\|docm\|dotx\|dotm` | `from`, `to`, `main_content_type`, `notes` |
| `backup` | Write a verified copy into the state directory, register `sha256`, prune to `limits.max_backups_per_source` | `mode=list`, `label` | `backup` path, `id`, `sha256` |
| `restore` | Restore a registered backup after verifying its hash | `id`, `backup` | `written`, `verified` |

Markdown support: `#`–`######` headings, paragraphs, `-`/`*`/`1.` lists,
`>` quotes, fenced code, `---` page break, pipe tables, `**bold**`,
`*italic*`, `` `code` ``, `[text](url)` links (external links become real
relationships, `#anchor` links become internal anchors).

```shell
python3 tools-word-doc-agent.py create --output offer.docx \
    --param markdown="# Offer for {{name}}\n\nHello {{name}}.\n" --param title=Offer
python3 tools-word-doc-agent.py create --output house.docx \
    --param template=corporate.dotx --param markdown-file=draft.md
```

### 3.2 Inspection, accessibility, proofing, gating

| Operation | What it does | Notes |
| --------- | ------------ | ----- |
| `inspect` | One structured profile: file identity (format, parts, bytes, sha256), core/app/custom properties, protection state, word/paragraph counts, heading outline, section geometry (page size, orientation, margins, columns, header/footer refs, break type), tables (rows, columns, irregularity, repeated header, empty header cells, style), images with alt text, fields by type with switches, footnotes/endnotes, styles (defined/used/unused/undefined), fonts, bookmarks, links, revisions, comments, hidden content, package risk, accessibility findings, proofing findings | `--param deep=true` keeps full style/field/comment lists |
| `a11y` | Accessibility audit driven by `asset-inspection-policy.json`: title, language, heading skips, empty headings, image alt text, ambiguous link text, table header rows/empty headers/ragged rows, hidden content, sub-8 pt text, untagged content controls, empty outline | Returns rule ids, severities, evidence and `score` |
| `proof` | Proofing defects from policy patterns: placeholders (`{{key}}`, `[TODO]`, `LOREM IPSUM`), authoring markers, repeated words, double spaces, straight quotes, trailing spaces, space before punctuation, empty-paragraph runs | `--param fail_on=error` turns blocking findings into exit 8 |
| `qa` | Release gate: integrity, structure, accessibility, proofing, tracked changes (and comments when configured) → `pass` / `pass-with-warnings` / `fail`, with `blocking_checks` and `next_actions` | Exit 8 on fail; `--param report_only=true` never fails the process |
| `score` | Catalog coverage + document evidence: 53 categories, 636 skills, which operations are native vs advisory, which categories the document actually exercises | `detail[]` per category |
| `capabilities` | Operation inventory with catalog skill counts; `--param all=true` lists all 636 skills with their category, operation and engine | The starting point for any agent |

```shell
python3 tools-word-doc-agent.py inspect --input report.docx --json --report-file profile.json
python3 tools-word-doc-agent.py a11y --input report.docx --json
python3 tools-word-doc-agent.py qa --input report.docx --param report_only=true
```

Exit codes: `0` ok, `1` internal error, `2` usage, `3` not found,
`4` approval required, `5` limit, `6` integrity, `7` external unavailable,
`8` policy/gate.

### 3.3 Text, metadata, styles, fields, lists, TOC

| Operation | What it does | Key parameters |
| --------- | ------------ | -------------- |
| `replace` | Find/replace across body, headers and footers; works across run boundaries so split formatting does not defeat the match | `find`, `replace`, `regex`, `case_sensitive`, `whole_word`, `style` (paragraph style filter), `include_headers`, `dry_run`; capped by `limits.max_replacements` |
| `metadata` | Read/set core properties (`title subject creator keywords description category contentStatus language lastModifiedBy`), add/remove custom properties, clear personal data | `set_title`, `set_subject`, `set_author`, `set_keywords`, `set_description`, `set_category`, `set_status`, `set_language`, `set_last_modified_by`, `add_custom="Name=Value"`, `remove_custom="Name"`, `clear_personal=true` |
| `styles` | `mode=list` (defined/used/undefined/unused + heading set), `mode=apply` (named style to matched paragraphs), `mode=normalize` (report or strip direct character formatting) | `style`, `match`, `paragraph_index`, `strip` |
| `fields` | `mode=list` (type, code, switches, arguments, locked, stale candidates, dynamic fields), `mode=insert`, `mode=update` (mark dirty + `w:updateFields`), `mode=lock/unlock` | `code`, `display` |
| `list` | `mode=insert` (bullet/decimal, levels 0–2), `mode=report` (list paragraphs + numbering definitions), `mode=restart/continue` (`lvlOverride/startOverride`), `mode=style` (apply numbering to matching paragraphs) | `items="a;b"`, `ordered`, `level`, `num_id`, `match`, `style` |
| `toc` | `mode=insert` writes a `TOC \o "1-n" \h \z \u` field plus heading and marks fields for update; `mode=outline` returns the heading structure; `mode=remove` | `depth` (1–6), `heading`, `anchor` |

```shell
python3 tools-word-doc-agent.py replace --input a.docx --output b.docx \
    --param find="{{quarter}}" --param replace=Q3
python3 tools-word-doc-agent.py metadata --input b.docx --output c.docx \
    --param set_keywords="q3,ops" --param clear_personal=true
python3 tools-word-doc-agent.py toc --input c.docx --output d.docx --param depth=2
```

`replace` also handles whole-word matching and capture groups
(`--param find="(\w+) report" --param regex=true --param replace="\1 summary"`).

### 3.4 Structure: insert, format, tables, images, headers, watermarks, forms

| Operation | What it does | Key parameters |
| --------- | ------------ | -------------- |
| `insert` | `type=pagebreak\|columnbreak\|linebreak\|cover\|bookmark\|caption\|control\|table\|list\|text`; anchors: `end`, `start`, `index:N`, `after-heading:TEXT`, `before-heading:TEXT` | `type`, `anchor`, `title`, `subtitle`, `name`, `label`, `text`, `items`, `ordered`, `data-file` |
| `format` | `scope=page` (page size, orientation, margins `top=2cm,left=2cm`, gutter), `scope=columns` (1–12, space, separator), `scope=paragraph` (spacing before/after, line spacing, first-line indent, alignment, keep-next, style filter), `scope=character` (default font, size, language, colour), `scope=section` (nextPage/continuous/evenPage/oddPage) | `scope`, `page_size`, `orientation`, `margins`, `gutter`, `columns`, `space`, `line_between`, `space_before`, `space_after`, `line_spacing`, `first_line_indent`, `align`, `keep_next`, `style`, `font`, `font_size`, `language`, `text_color`, `break_type` |
| `table` | `mode=create` (CSV/JSON data, width columns, repeated header row), `mode=structure` (report or set repeated header), `mode=sort` (header preserved), `mode=design` (style, header shading), `mode=formula` (appends a total row containing a `=SUM(ABOVE)` field) | `data-file`, `headers`, `style`, `widths_cm`, `repeat_header`, `mode`, `table_index`, `column`, `descending`, `shade_header`, `label` |
| `image` | Embeds PNG/JPEG/GIF/BMP as `w:drawing` with real dimensions from the file header, centred paragraph, alt text in `wp:docPr/@descr` and `pic:cNvPr/@descr` | `image`, `alt`, `width_cm` (0.5–60) |
| `headerfooter` | Writes header/footer content for the active section, optional `PAGE of NUMPAGES` fields, alignment, clear | `header_text`, `footer_text`, `header_page_numbers`, `footer_page_numbers`, `align`, `clear` |
| `watermark` | Adds a WordArt text watermark shape to the header (diagonal or horizontal, colour, size ≤ 40 chars); `mode=remove` strips tool-generated watermarks | `text`, `color`, `size_pt`, `diagonal`, `mode` |
| `form` | Builds `w:sdt` content controls from a JSON spec: text, rich text, date, checkbox, drop-down, combo, repeating; optional forms protection | `spec-file` or inline `type`/`alias`/`value`/`items`; `protect=true` |

Form spec (see `asset-capability-map.json` category 41 for the skills it
covers):

```json
{"title": "Intake",
 "controls": [
   {"type": "text",     "alias": "Full name", "tag": "name", "value": "Jane Doe"},
   {"type": "date",     "alias": "Start date", "tag": "start"},
   {"type": "dropdown", "alias": "Team", "tag": "team", "items": ["Ops", "Eng"]},
   {"type": "checkbox", "alias": "Approved", "tag": "approved", "checked": true}
 ]}
```

`image` note: SVG, 3-D models, SmartArt, charts and shapes are **not**
authored by this tool (see §11); they are detected by `inspect`.

### 3.5 Review state, protection, sanitisation

| Operation | What it does | Key parameters |
| --------- | ------------ | -------------- |
| `comments` | `mode=list` (authors, dates, text, orphan refs), `mode=add` (anchored to matched text, writes `comments.xml`, range + reference runs), `mode=remove` | `mode`, `comment`, `author`, `anchor_text` |
| `revisions` | `mode=report` (insertions, deletions, moves, property changes, authors, date range), `mode=accept-all`, `mode=reject-all` (unwraps kept text, converts `delText`, drops property-change records, handles row marks) | `mode` |
| `sanitize` | Applies `asset-inspection-policy.json` targets: replace author/last-modified-by, drop company/manager, remove `docProps/custom.xml`, remove comments, accept revisions, remove hidden text, remove `customXml/*`; report-only targets (macros, embedded objects, external links, white text) are surfaced, not silently changed | `targets="core-author,hidden-text"` to narrow, `--dry-run` to preview |
| `protect` | Applies `readOnly`, `comments`, `forms` or `trackedChanges` protection with enforcement; optional SHA-512 salted password hash per ISO/IEC 29500-1 17.15.1.29 | `mode`, `password` |
| `unprotect` | Removes `w:documentProtection` | — |

Review semantics verified by test: `accept-all` keeps inserted text and
drops deleted text; `reject-all` keeps deleted text (restored as normal
text) and drops insertions.

### 3.6 Mail merge, export, planning, routing

| Operation | What it does | Key parameters |
| --------- | ------------ | -------------- |
| `merge` | Renders a placeholder document from a JSON/CSV record set: one file per record or one combined document with page breaks | `data_file`, `placeholder` (default `{{key}}`), `name_field` (default `id`), `combine`, `missing=keep\|error`; capped by `limits.max_merge_records`; `--output` is a directory |
| `export` | `txt`, `md`, `html` (single file, inline CSS, base64 images, `lang` attribute, real table markup), `json` (document model: blocks, runs with emphasis, tables, images, metadata), `pdf` (external adapter only) | `format`, `embed_images`, `converter`, `allow_subprocess` |
| `route` | Maps free text to catalog skills, with category, operation, engine and score | `query`, `limit` |
| `plan` | Turns a catalog skill (or a whole category) into prerequisites, five steps, automation command, verification and rollback | `skill`, `category_id` |

```shell
python3 tools-word-doc-agent.py merge --input offer.docx --output out/records \
    --param data_file=records.json --param combine=true
python3 tools-word-doc-agent.py export --input report.docx --output report.html
python3 tools-word-doc-agent.py route --param query="remove hidden metadata"
python3 tools-word-doc-agent.py plan --param skill="Blank Document Creator"
```

---

## 4. Catalog coverage

`asset-capability-map.json` maps **all 636 skills** onto the 33
operations. `capabilities` reports live counts; the table below shows each
category's implementing operations and how many of its 12 skills use each
(`op(count)`). `plan` is used where the capability is a Word UI or service
action that cannot be performed on the package safely — the tool then
returns a verified procedure instead of pretending to automate it.

| # | Category | Purpose | Implementing operations |
| - | -------- | ------- | ----------------------- |
| 1 | File Lifecycle and Backstage Operations | control documents from creation through closure. | plan(5), copy(2), create(2), qa(2), convert(1) |
| 2 | Document Properties, Inspection, and Hidden Data | understand and sanitize the real document before release. | inspect(10), sanitize(2) |
| 3 | Protection, Encryption, Signatures, and Final Status | apply appropriate document controls without blocking valid work. | protect(11), inspect(1) |
| 4 | Recovery, Versions, and AutoSave | preserve work and recover from interruption. | plan(6), backup(3), restore(3) |
| 5 | Templates, Building Blocks, and Reusable Systems | turn repeatable document structures into governed assets. | create(6), styles(4), plan(2) |
| 6 | Clipboard, Paste, and Content Transfer | move content without importing damage. | plan(11), replace(1) |
| 7 | Fonts and Character Formatting | control character-level presentation precisely. | format(6), inspect(6) |
| 8 | Text Effects, WordArt, and Advanced Typography | use display typography while preserving readability and compatibility. | plan(12) |
| 9 | Paragraph Formatting and Pagination | control flow, spacing, and page behavior at paragraph level. | format(10), plan(2) |
| 10 | Bullets, Numbering, and Multilevel Lists | create stable list systems that survive editing. | list(12) |
| 11 | Styles and Document Formatting Systems | replace uncontrolled direct formatting with maintainable styles. | styles(12) |
| 12 | Find, Replace, Navigation, and Selection | locate and operate on content with accuracy. | replace(10), inspect(2) |
| 13 | Editor, Proofing, Thesaurus, and Word Count | improve language quality with controlled proofing. | proof(11), inspect(1) |
| 14 | Dictation, Transcription, Voice, and Copilot Workflows | use assisted authoring while retaining human-verifiable output. | plan(12) |
| 15 | Pages, Breaks, and Section Architecture | control document structure across pages and sections. | insert(7), format(5) |
| 16 | Margins, Orientation, Paper, and Book Layout | set production geometry for screen and print. | format(8), plan(4) |
| 17 | Columns, Hyphenation, Line Numbers, and Text Flow | format long-form multi-column and numbered documents. | format(12) |
| 18 | Headers, Footers, and Page Numbers | create section-aware recurring content. | headerfooter(12) |
| 19 | Tables: Creation and Conversion | create tables from manual, textual, Excel, and reusable sources. | table(12) |
| 20 | Tables: Structure, Size, and Flow | manage rows, columns, cells, widths, heights, pagination, and fit behavior. | table(12) |
| 21 | Tables: Design, Sorting, Formulas, and Accessibility | format, calculate, and make tables understandable. | table(8), a11y(4) |
| 22 | Pictures and Image Processing | insert, edit, compress, position, and describe images. | image(10), a11y(1), inspect(1) |
| 23 | Shapes, Text Boxes, Fills, and Lines | build editable visual components with full property control. | plan(12) |
| 24 | Shape Effects and 3-D Formatting | apply and govern visual effects without harming clarity. | plan(12) |
| 25 | Object Positioning, Wrapping, Anchors, and Layers | place floating and inline objects predictably. | plan(7), inspect(5) |
| 26 | Arrange, Align, Group, Rotate, and Selection Pane | control object geometry and selection at scale. | plan(12) |
| 27 | SmartArt | create and transform structured diagrams. | plan(12) |
| 28 | Charts and Data Visualization | create and format charts with analytical integrity. | plan(8), inspect(4) |
| 29 | Icons, SVG, 3-D Models, Screenshots, and Embedded Objects | use specialized visual and linked content safely. | inspect(12) |
| 30 | Equations, Symbols, and Scientific Notation | author technical notation accurately. | inspect(12) |
| 31 | Links, Bookmarks, Cross-References, and Fields | build dynamic navigation and references. | fields(10), insert(2) |
| 32 | Table of Contents and Document Navigation | produce reliable long-document navigation. | toc(9), inspect(3) |
| 33 | Footnotes and Endnotes | manage scholarly notes and numbering. | inspect(12) |
| 34 | Citations, Bibliographies, and Research | create traceable academic references. | plan(11), inspect(1) |
| 35 | Captions, Tables of Figures, Indexes, and Authorities | build generated reference apparatus. | insert(7), plan(5) |
| 36 | Mail Merge, Labels, Envelopes, and Directories | produce personalized outputs from structured recipient data. | merge(12) |
| 37 | Comments, Mentions, and Collaborative Review | run a controlled comment lifecycle. | comments(12) |
| 38 | Track Changes, Accept, Reject, Compare, and Combine | manage revisions without losing auditability. | revisions(12) |
| 39 | Accessibility and Inclusive Documents | make Word documents usable by more readers. | a11y(12) |
| 40 | Language, Translation, and Localization | adapt documents across languages and regions. | plan(12) |
| 41 | Forms, Content Controls, and XML Mapping | build structured fillable documents. | form(12) |
| 42 | Macros, VBA, Developer Tools, and Automation | automate desktop Word safely and repeatably. | plan(7), inspect(5) |
| 43 | Add-ins, COM, Web Add-ins, and Connected Services | extend Word while controlling dependencies. | inspect(12) |
| 44 | Open XML, File Formats, and Compatibility | preserve structure across formats and versions. | inspect(12) |
| 45 | PDF, XPS, Export, and Print Production | produce reliable fixed outputs. | export(9), plan(3) |
| 46 | Watermarks, Page Color, Borders, and Backgrounds | apply document-level visual treatments with section awareness. | format(8), watermark(4) |
| 47 | Views, Windows, Focus, and Immersive Reading | control editing and reading environments. | plan(9), inspect(3) |
| 48 | Word Options, Ribbon, Quick Access, and Interface Customization | configure Word for repeatable professional use. | plan(11), inspect(1) |
| 49 | Trust Center, Protected View, and Security | manage execution and file-opening risk. | inspect(8), protect(4) |
| 50 | Performance, File Size, and Fidelity | keep documents responsive without degrading output. | inspect(12) |
| 51 | Quality Assurance, Regression, and Release | prove the document works through the recipient path. | qa(12) |
| 52 | Repair, Recovery, and Workarounds | fix the earliest responsible cause while preserving valid work. | inspect(11), restore(1) |
| 53 | Evaluation, Benchmarking, and Skill Management | measure and maintain the Word capability ecosystem. | score(8), plan(4) |

Totals by operation (from `capabilities`): inspect 134, plan 179, format 49,
table 32, list 12, styles 16, a11y 17, form 12, comments 12, revisions 12,
headerfooter 12, merge 12, protect 15, export 9, fields 10, image 10,
insert 16, copy 2, create 8, convert 1, qa 14, replace 11, proof 11,
sanitize 2, score 8, backup 3, restore 4, toc 9, watermark 4.

---

## 5. Inputs, outputs, schemas, configuration

### 5.1 JSON envelope

Successful run (`--json`):

```json
{"ok": true, "operation": "inspect", "elapsed_ms": 41,
 "result": {"file": {"format": "docx", "parts": 9}, "...": "..."},
 "report_file": "profile.json"}
```

Failure:

```json
{"ok": false, "operation": "sanitize", "elapsed_ms": 12,
 "error": {"code": "APPROVAL_REQUIRED",
           "message": "sanitize requires approval: removes content and metadata irreversibly",
           "details": {"approve_with": "--approve", "reason": "..."}}}
```

Without `--json`, a compact human summary is printed to stdout (errors to
stderr with a `hint:` line when one is available). Read-only operations
never write to `--output`; writing operations always report `written`.

### 5.2 Configuration (`asset-default-config.json`)

| Section | Keys | Effect |
| ------- | ---- | ------ |
| `paths` | `state_dir`, `backup_dir`, `report_dir` | Where backups and the registry live (relative to the process working directory) |
| `limits` | `max_input_bytes` (100 MB), `max_zip_entries` (10 000), `max_part_bytes` (64 MB), `max_replacements` (20 000), `max_merge_records` (500), `max_pdf_pages`, `max_scan_text_chars` (4 M), `max_seconds` (300), `max_backups_per_source` (10) | Enforced before or during work; violations exit 5 |
| `external` | `pdf_converter`, `pdf_converter_args`, `timeout_seconds`, `allow_subprocess` | PDF export adapter; disabled by default with `allow_subprocess=false` |
| `editing` | `require_approval_for`, `allowed_input_extensions`, `refuse_output_overwrite`, `allow_in_place` | Safety controls |
| `defaults` | `sanitized_author`, `title`, `watermark_text`, `watermark_color`, `toc_depth`, `placeholder_pattern` | Values used when a parameter is absent |
| `policy_files` | `inspection_policy`, `capability_map`, `catalog` | Asset filenames resolved next to the tool files |

Pass `--config my-config.json` to override any subset (deep merge).

### 5.3 Inspection policy (`asset-inspection-policy.json`)

- `sanitize.targets[]`: `id`, `enabled`, `severity`, `action`
  (`set-property`, `remove-property`, `remove-part`, `remove-part-pattern`,
  `remove-comments`, `accept-revisions`, `remove-hidden-text`,
  `report-only`), plus `property`/`pattern`/`replacement_setting`/`why`.
- `a11y.rules[]`: `id`, `severity`, `why`; `a11y.ambiguous_link_text`,
  `a11y.min_body_half_points`.
- `proofing.checks[]`: `id`, `enabled`, `severity`, `patterns[]`,
  `min_run`, `why`; plus `ignore_terms`, `max_findings`.
- `qa_gate`: `fail_on_severity`, `max_warnings`, `required_checks`,
  `require_no_tracked_changes`, `require_no_comments`,
  `require_no_placeholders`, `require_alt_text`.
- `risk`: severity per risk class (macros, external relationships, DDE
  fields, protected-view flags, dynamic data exchange, missing styles).

Rule ids are returned verbatim in output so a parent skill can map them to
its own vocabulary. Adding a rule needs no code change: add the `id` and
pattern, then handle the id downstream.

### 5.4 Capability map (`asset-capability-map.json`)

```json
{"categories": [{"id": 38,
                 "name": "Track Changes, Accept, Reject, Compare, and Combine",
                 "purpose": "manage revisions without losing auditability.",
                 "surfaces": ["Track Changes for insertions", "..."],
                 "skills": [{"name": "Track Changes Configurator",
                             "op": "revisions",
                             "params": {},
                             "engine": "native",
                             "triggers": ["track", "changes", "configurator"]}]}],
 "operations": {"revisions": {"catalog_skills": 12, "categories": [38]}}}
```

`engine=native` means the operation reads or writes OOXML for that
capability. `engine=advisory` means `plan` returns a validated procedure
instead.

---

## 6. Safety and approval rules

1. **Approval gate.** `--approve` is mandatory for: `sanitize`,
   `revisions --param mode=accept-all|reject-all`, `protect`,
   `unprotect`, `watermark --param mode=remove`, `restore`, and any
   `--in-place` write. Without it the tool exits 4 and changes nothing.
2. **Dry run is always allowed.** `--dry-run` bypasses the approval gate
   because nothing is written; `sanitize` and `restore` print what they
   would do. `replace` honours `--dry-run` too.
3. **No silent overwrite.** An existing `--output` is refused (exit 2);
   overwriting the input requires `--in-place --approve`.
4. **Atomic writes.** Every write goes to a temporary file in the target
   directory and is moved into place with `os.replace`, so a crash cannot
   leave a half-written `.docx`.
5. **Reversibility first.** `backup` before destructive work; `copy` for
   branches; `revisions` preserves the accepted/rejected outcome as a new
   file rather than mutating history in place.
6. **Report-only by default for irreversible judgement calls.** Macros,
   embedded OLE objects, external relationships and white-on-white text are
   reported, never removed automatically: removing a macro changes the file
   type, and white text may be deliberate. Change the policy asset if your
   situation requires removal.
7. **No arbitrary execution.** The only subprocess is the optional PDF
   converter, which requires an explicit `pdf_converter` path *and*
   `allow_subprocess=true`, runs with a timeout, and its output is checked
   for the `%PDF-` header. Nothing is downloaded or installed.
8. **No secrets.** No credentials, tokens, tenant IDs or private endpoints
   exist in the package or its configuration.
9. **Publishing/financial/credential actions are out of scope.** The tool
   never sends, signs, encrypts for distribution or uploads a document.
10. **Password-protected protection is unverified here.** The SHA-512 hash
    writer follows the published OOXML structure, but no Word instance was
    available to confirm acceptance; the output says so.

---

## 7. Validation and tests actually completed

Everything below was executed in this environment (Linux, Python 3.13.16,
no Word, no LibreOffice).

| Check | Command | Result |
| ----- | ------- | ------ |
| Help and capability list | `python3 tools-word-doc-agent.py --help`, `... capabilities` | 33 operations listed, catalog totals 53 categories / 636 skills |
| Full catalog expansion | `... capabilities --param all=true --json` | 636 skill records, each with an operation and engine |
| End-to-end suite | `python3 tools-selftest.py` | **152 tests, 0 failures** (see breakdown below) |
| Independent reader | `python-docx` opened 10 generated artefacts (created, TOC, form, image, sanitised, table, landscape geometry, merged, protected, recovered) | all opened with expected paragraph/table/section counts |
| Geometry/inline checks | `python-docx` + raw XML | 3 cm image = 1 080 000 EMU with `descr` alt text; landscape section 10 059 670 × 7 773 670 EMU with 2 cm margins |
| Timeout/cancellation | `... inspect --input guide.docx --timeout 0.0001` | exit 5, `time budget exceeded during proofing` |
| Clean-root portability | copy folder beside a test `SKILL.md`, run with relative paths | `create` + `inspect` succeeded |

Self-test groups (152 assertions): cli 7, create 8, inspect 7, proof 2,
a11y 1, qa 4, replace 6, metadata 3, styles 4, fields 2, toc 1,
headerfooter 2, watermark 4, image 3, form 4, list 4, table 5, structure 12,
review 14, protection 6, merge 7, export 11, backup 3, restore 3, route 2,
plan 4, score 3, errors 9, recovery 3, portability 2, package 6.

What the suite proves, by area:

- **Creation**: markdown → docx with headings, lists, tables, links; parts
  present; package validates; overwrite refused.
- **Inspection**: heading/table counts, title property, protection state,
  placeholder detection, readable human summary.
- **Gates**: `qa` blocks a document with placeholders (exit 8, `POLICY`),
  `--param report_only=true` returns a verdict instead of failing.
- **Replace**: literal replacement, placeholder removed from the copy,
  regex dry-run writes nothing, replacement limit enforced, invalid regex
  is a usage error, in-place requires approval.
- **Review**: tracked insertions/deletions detected with authors;
  accept/reject change which text survives; comments add/list;
  sanitise (dry-run, approval, applied) clears revisions, comments and
  hidden text and reports remaining risk.
- **Structure**: cover page, bookmark, page geometry persisted as
  landscape, columns, dotx conversion detected by `inspect`.
- **Tables**: CSV creation, sort, structure report with repeated headers,
  appended total row (`sum` 3680).
- **Export**: html is a complete document with data-URI images and a `lang`
  attribute; markdown keeps headings and tables; json model reloads; pdf
  fails cleanly with `EXTERNAL_UNAVAILABLE` when unconfigured, and reports
  honestly when pointed at a missing/failing converter.
- **Recovery**: backup → deliberately corrupt the file → `INTEGRITY` on
  inspect → restore by id → recovered file validates.
- **Errors**: corrupt zip `INTEGRITY`, missing file `NOT_FOUND`, wrong
  extension `USAGE`, legacy `.doc` rejected, unknown operation, malformed
  `--param`, missing config.
- **Package shape**: no `SKILL.md`, no subfolders, all files prefixed,
  exactly one knowledge file, entry point present.

Not tested, and not claimed: rendering inside Microsoft Word, field
recalculation/pagination, password-protected protection acceptance by
Word, macro behaviour, PDF output quality (no converter available),
OneDrive/SharePoint paths, and Word UI automation of any kind.

---

## 8. Limits and unavailable integrations

| Area | Status | Detail |
| ---- | ------ | ------ |
| Word pagination, layout and rendering | unavailable | No layout engine. Page counts are estimates (`words/500`). TOC/cross-reference/index results are fields marked for update; Word fills them on open or F9. |
| `w:updateFields` support | best effort | Some Word builds ignore it; the TOC entry text tells the user to update the field. |
| PDF/XPS export | configurable adapter | Requires an external headless converter (`soffice` etc.) plus `allow_subprocess=true`. Not bundled, not installed, not tested for output quality. |
| Charts, SmartArt, shapes/text boxes/WordArt, 3-D models, SVG, equations | detect + advise | `inspect` reports them; authoring is out of scope (`plan` returns the Word procedure). |
| Macros/VBA | detect only | Presence is reported (`word/vbaProject.bin`, docm/dotm); nothing is executed or removed automatically. |
| Digital signatures, encryption-to-open, IRM | unavailable | `inspect` reports what it finds; `protect` only writes editing restrictions. |
| Compare/Combine documents | advisory | Accept/reject is native; document comparison is a `plan` procedure (Word's Compare) because a faithful merge engine is out of scope. |
| Translation, Copilot, dictation, add-ins, COM, trust-centre policy | advisory | Reported or planned; no network, no Office process. |
| Printers, trays, duplex, print preview | advisory | No print stack. |
| OneDrive/SharePoint/Recent files | unavailable | No Graph or cloud client; work on local copies. |
| `.doc`, `.dot`, `.rtf`, `.pdf` inputs | rejected | OOXML only; exit 2 with the allowed list. |
| Footnotes/endnotes authoring | report only | Counted and summarised; created in Word. |
| Cross-reference creation | fields supported | `fields --param mode=insert` can write `REF`/`PAGEREF`; reference integrity is reported, not auto-repaired. |
| Very large documents | bounded | Limits above; `max_part_bytes` 64 MB, `max_scan_text_chars` 4 M. |

---

## 9. Troubleshooting and recovery

| Symptom | Cause | Action |
| ------- | ----- | ------ |
| `INTEGRITY — not a valid OOXML/zip package` | file is not a zip/OOXML, or truncated | `backup --param mode=list`, then `restore --param id=...`; if no backup, reopen in Word and save a fresh copy |
| `INTEGRITY — malformed XML in word/...` | part corrupted by another tool | restore a backup; if unavailable, unzip and repair the part manually, then re-run `inspect` |
| `APPROVAL_REQUIRED` | destructive/in-place action without `--approve` | re-run with `--approve`, or preview with `--dry-run` |
| `LIMIT — replacement count exceeded` | pattern too broad | narrow the pattern, use `--param style=Heading1`, or raise `limits.max_replacements` in your config |
| `POLICY — release gate failed` | `qa` found blocking findings | read `next_actions`; typical fixes are `revisions --param mode=accept-all --approve`, `metadata --param set_title=...`, `format --param scope=character --param language=en-US`, or supplying image `alt` text |
| `EXTERNAL_UNAVAILABLE — PDF` | no converter configured | see the `details.enable` list; export html/md/txt meanwhile |
| Output file refused | output exists | delete it, choose another `--output`, or use `--in-place --approve` intentionally |
| Windows console shows `UnicodeEncodeError` | legacy code page | run with `--json` and redirect, or `set PYTHONUTF8=1` |
| `unknown operation` | typo or alias missing | `capabilities` lists the valid names; aliases exist for `audit`, `profile`, `check`, `accept-revisions`, `reject-revisions`, `mailmerge`, `versions`, `pdf` |
| Second run "file in use" (Windows) | Word has the file open | close Word; the tool never holds handles after exit |
| Partial results after kill | interrupted run | the atomic write means the target is either the old or the new file; re-run |

Recovery pattern that the suite exercises:

```shell
python3 tools-word-doc-agent.py backup --input report.docx --param label=pre-edit
# ... destructive work ...
python3 tools-word-doc-agent.py inspect --input report.docx      # detect damage
python3 tools-word-doc-agent.py restore --input report.docx --output report.docx \
    --param id=<id> --approve
```

State lives in `.word-document-agent-state/` (relative to the working
directory): `backups/` holds copies, `backups/registry.json` holds ids,
hashes, sizes and timestamps.

---

## 10. Extension and maintenance

- **New operation**: add a handler `op_x(ctx)` to
  `tools-word-doc-agent.py`, register it in `OPERATIONS` and `HANDLERS`,
  and add it to `_file_for_operation`. Keep input validation through
  `C.validate_input_path` / `C.validate_output_path` and return a plain
  dict.
- **New package logic**: put it in the layer that owns it — element
  constructors in `tools-docx-build.py`, structural mutation in
  `tools-docx-structure.py`, content/state mutation in
  `tools-docx-edit.py`, evidence in `tools-docx-audit.py`, renderers in
  `tools-docx-export.py`. Everything shared goes in `tools-docx-core.py`.
- **New inspection/proofing rule**: add it to
  `asset-inspection-policy.json`; if it needs new evidence, extend the
  relevant inventory function in `tools-docx-audit.py` and add the rule id
  to `a11y_audit`/`proofing`.
- **New catalog mapping**: regenerate or edit
  `asset-capability-map.json` so every skill still points at a real
  operation and a truthful `engine`; `score` and `capabilities` read it
  directly.
- **Long loops**: call `C.checkpoint("<name>")` so `--timeout` can cancel
  them.
- **Folder hygiene**: the entry point sets `sys.dont_write_bytecode = True`,
  so running the tool never creates `__pycache__` inside the delivered
  folder (and it also works from read-only media). Runtime state
  (`.word-document-agent-state/`) is created in the *working directory*,
  not next to the tool files, when you run from elsewhere.
- **Namespace prefixes**: OOXML prefixes that must round-trip are
  registered from each part's own `xmlns:` declarations, so unrecognised
  vendor namespaces survive edits.

Maintenance checklist before shipping a change: `python3 tools-selftest.py`,
`python3 tools-word-doc-agent.py capabilities`, and one `inspect --json` on
a real document.

---

## 11. Compatibility with the future Copilot skill package

- This folder contains **no** `SKILL.md`; the combined package must have
  exactly one `SKILL.md` at the skill root.
- The folder can be copied beside that root `SKILL.md` without
  modification: `my-skill/SKILL.md` + `my-skill/word-document-agent/…`.
- 12 files (8 `tools-`, 1 `knowledge-`, 3 `asset-`), every path at most three levels deep from the future skill
  root (`word-document-agent/tools-docx-core.py`), well inside the 500-file
  limit.
- All references are relative and case-sensitive: assets are resolved next
  to the tool files by exact filename, never by absolute or traversal
  paths.
- Verified by running the tool from a clean temporary skill root that
  contained one test `SKILL.md` and only relative paths.
- External failures (missing converter binary, a hosted resource that is
  not reachable, Word refusing a password hash) are platform or hosting
  issues; this package detects and reports them rather than preventing
  them.
