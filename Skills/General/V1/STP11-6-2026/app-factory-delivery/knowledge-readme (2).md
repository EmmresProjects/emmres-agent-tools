# agent-skill-catalog — Skill Tool knowledge file

`agent-skill-catalog` is a self-contained, standard-library Python Skill Tool that
turns the "Agent Skill Catalog" document (30 categories, 244 skills, Appendix A
composition guidance, Appendix B non-negotiable execution rules) into a working,
queryable, reusable resource for any future Copilot skill.

It does four jobs that the parent skill can rely on:

1. **Ingest** a catalog written as plain text, Markdown, or copied HTML into a
   normalized dataset (tags, CSS classes, data attributes, numbering noise and
   stray spacing removed).
2. **Curate** that dataset — validate it, browse it, search it, export it.
3. **Route** a free-text user request to the smallest covering set of catalog
   skills, with the evidence for every pick.
4. **Draft** a full skill specification (the catalog's own nine recommended
   fields) for any one skill, as Markdown or JSON.

Everything runs offline with Python 3.8+ and the standard library. No network,
no secrets, no subprocess execution, no shell-out, no telemetry.

---

## 1. Folder contents

| File | Role |
| --- | --- |
| `tools-skill-catalog.py` | **Primary entry point.** CLI with 9 commands, built-in `--help`, `capabilities` listing, structured JSON envelopes, exit-code contract. |
| `tools-catalog-core.py` | Core library: parsing, normalization, schema + semantic validation, search, routing, spec drafting, export, atomic writes, deadlines. Loaded by the CLI from the same folder (never imported from anywhere else). |
| `tools-selftest.py` | Executable test harness (70 checks). Runs the CLI as a subprocess from a throw-away working directory, so it tests the real interface, not the internals. |
| `asset-catalog-dataset.json` | Normalized dataset built from `Catalog.pdf`: 30 categories, 244 skills, 2 appendices (6 items each). This is the working knowledge base. |
| `asset-catalog-schema.json` | JSON Schema (the validator subset the core implements) that defines the dataset contract. Pass a different schema with `--schema` for a different catalog. |
| `asset-routing-signals.json` | Routing and spec policy: limits, weights, signal groups (platform/language/framework/artifact/delivery/domain/lifecycle), category affinities, fallback affinities, composition map from Appendix A, and the spec defaults. This is the file to tune. |
| `asset-spec-template.md` | Markdown template for `spec --format md` with 14 `{{placeholder}}` slots. Replace it to match your own house spec format. |
| `asset-sample-catalog.html` | Real fixture: a copied-HTML mini catalog (2 categories, 5 skills, 1 appendix, inline `<script>`/`<style>`, data attributes, entities, a wrapped category title). Used by the self-test and handy as a demo of what `ingest` cleans. |
| `knowledge-readme.md` | This file. |

Nine files, flat, at the tool folder root. There is no `SKILL.md`, no subfolder,
and no unprefixed file — the folder can be dropped beside a parent skill's
`SKILL.md` unchanged.

---

## 2. Requirements, setup, exact commands

* **Python 3.8 or newer** (verified on CPython 3.13.16, Linux).
* **No third-party packages required.** `tools-selftest.py` and all commands use
  only `argparse`, `csv`, `html`, `importlib`, `json`, `os`, `re`, `signal`,
  `sys`, `time`, `unicodedata`, `tempfile`, `subprocess` (the last two only in
  the self-test).
* Optional, only if you want to regenerate the dataset from the PDF yourself:
  `pip install "pypdf>=3.0"` (used once, outside the tool, to extract text).

No install step. Copy the folder anywhere and run it with an explicit shell:

```sh
# shell: sh/bash/zsh (POSIX). Bash is used in the self-test.
python3 agent-skill-catalog/tools-skill-catalog.py --help
python3 agent-skill-catalog/tools-skill-catalog.py capabilities

# from inside the future skill root, relative paths only:
python3 agent-skill-catalog/tools-skill-catalog.py stats
python3 agent-skill-catalog/tools-skill-catalog.py route --request "Build a Flutter app for Android and iOS with offline sync, then publish to the Play Store"

# the executable self-test (exit 0 = all pass, 1 = failures, 3 = harness error)
python3 agent-skill-catalog/tools-selftest.py --verbose
```

Windows note: the same commands work with `py` or `python` in place of
`python3`; the script uses `os.path.join` and never hard-codes a separator.

---

## 3. Commands and capabilities (9)

Every command accepts `--format json` and returns a single envelope:

```json
{"ok": true, "tool": "agent-skill-catalog", "command": "route", "data": { ... }}
```

Errors go to **stderr** as `{"ok": false, "tool": ..., "command": ...,
"error": {"code": "...", "message": "...", "hint": "..."}}`.

| Command | What it does | Key options |
| --- | --- | --- |
| `capabilities` | List every capability (text or JSON). | `--format` |
| `ingest` | Parse text / Markdown / copied HTML into a normalized dataset. | `--input`, `--input-format auto\|text\|markdown\|html`, `--output`, `--title`, `--source-name`, `--force`, `--dry-run`, `--timeout`, `--max-input-bytes` |
| `validate` | Schema + semantic validation (unique ids/slugs, dangling category refs, stats consistency, slug pattern, description lengths). | `--dataset`, `--schema`, `--format` |
| `list` | List categories, or the skills of one category, with counts and pagination. | `--category`, `--limit`, `--offset`, `--descriptions`, `--format` |
| `search` | Weighted keyword search over names, descriptions, categories. | `--query`, `--category`, `--limit`, `--format` |
| `route` | Map a request to the smallest covering set of skills + matched signals + reasons + uncovered signals + orchestration hint. | `--request` / `--request-file` / `--request -`, `--max-skills` (1–25, default 6), `--format` |
| `spec` | Draft the nine recommended fields for one skill. | `--skill` (slug, name, `skill-014`, or unambiguous fragment), `--template`, `--output`, `--force`, `--dry-run`, `--format json\|md` |
| `export` | Export the whole dataset as `json`, `csv`, or `md`. | `--format`, `--output`, `--force`, `--dry-run`, `--json-summary` |
| `stats` | Totals, per-category counts, description-length stats, provenance. | `--format` |

All commands also accept `--dataset`, `--schema`, `--signals`, `--timeout`.
Defaults resolve beside the script; `ASC_DATASET`, `ASC_SCHEMA`, `ASC_SIGNALS`
and `ASC_TEMPLATE` override them, so a team can point the same folder at a
different catalog without editing any file.

### 3.1 `ingest`

Accepted structure (the document's own shape, tolerant of decoration):

```
1. Category Title
1. Skill Name: Some Skill
Description: One or two sentences.
...
Appendix A. ...
- bullet item
```

Real run against the PDF text extracted from `Catalog.pdf`:

```sh
python3 agent-skill-catalog/tools-skill-catalog.py ingest \
  --input catalog.txt \
  --output agent-skill-catalog/asset-catalog-dataset.json \
  --source-name "Catalog.pdf (Agent Skill Catalog: 30 categories, 244 skills)" \
  --title "Agent Skill Catalog - Universal Application Creation, Software Engineering, and Delivery"
```

```
Ingested Catalog.pdf (Agent Skill Catalog: 30 categories, 244 skills) (detected format: text)
  categories: 30 | skills: 244 | appendices: 2
  noise lines removed: 69
  validation: VALID (0 issue(s))
  dataset written to: agent-skill-catalog/asset-catalog-dataset.json
```

What it tolerates, all covered by the tests:

* **HTML**: `<script>`/`<style>` blocks, comments, any tags, CSS class and
  `data-*` attributes, HTML entities (`&mdash;`, `&lt;`), block-level line breaks.
* **Markdown**: headings, list markers, emphasis, inline code, links, images,
  block quotes, fences, HTML comments. Ordered-list numbering is deliberately
  **kept**, because the parser keys on `1. Skill Name:`.
* **PDF text quirks**: Symbol/Wingdings bullets such as `U+F0B7` (a private-use
  code point that `str.strip()` does not remove) are converted to `-` before
  line classification, smart quotes and dashes are folded to ASCII, and other
  private-use characters are dropped. Without this, Appendix B collapsed from
  6 rules into 1.
* **Wrapped lines**: a category title that wraps onto the next line is joined
  (`20. API Documentation, Technical Writing, and Developer` + `Experience`), and
  a description that continues after a page break is joined too.
* **Page noise**: repeated header/footer lines are counted and dropped
  (`ignored_noise_lines`, 69 for the real document).
* **stdin**: `--input -` reads stdin; it then requires `--source-name` so the
  stored provenance never becomes a machine path (`E_MISSING_ARG`).

`--input-format` is `auto` by default (HTML if tags are present, Markdown if
Markdown markers dominate, otherwise text). Override it when a document is
ambiguous. Nothing is written until parsing and validation succeed, and the
write is atomic (temp file + `os.replace`), so a failure never leaves a partial
dataset.

### 3.2 `validate`

```sh
python3 agent-skill-catalog/tools-skill-catalog.py validate --format json
```

```json
{"ok": true, "tool": "agent-skill-catalog", "command": "validate",
 "data": {"valid": true, "issue_count": 0, "issues": [], "dataset": "...", "schema": "..."}}
```

`python3 ... validate` on a healthy dataset prints `Result: VALID (0 issue(s))`
and exits 0. On a damaged dataset it prints each issue and exits 1
(`E_DATASET_INVALID`). Verified with a deliberately broken copy of the real
dataset:

```
Result: INVALID (3 issue(s))
  - [error] skills[0].slug: does not match pattern ^[a-z0-9]+(?:-[a-z0-9]+)*$
  - [error] skills[1]: category_id 'cat-99' does not exist
  - [error] catalog.stats.skills: stats say 999 but 244 skills present
```

### 3.3 `list`, `search`, `stats`

```sh
python3 agent-skill-catalog/tools-skill-catalog.py list                     # 30 categories + counts
python3 agent-skill-catalog/tools-skill-catalog.py list --category mobile-application-engineering --format md
python3 agent-skill-catalog/tools-skill-catalog.py search --query "react native offline" --limit 5
python3 agent-skill-catalog/tools-skill-catalog.py search --query "kubernetes" --category cloud-containers-and-platform-engineering
python3 agent-skill-catalog/tools-skill-catalog.py stats
```

Search scoring is weighted and boundary-aware: phrase in name 12, name token 4,
name substring 2.5, phrase in description 5, description token 1.5, description
substring 0.75, category token 0.5. Substring hits require word boundaries, so
`zzzqqq-no-match` returns **0 results** instead of matching the tail `match`
inside unrelated descriptions (this bug existed and is fixed; a check covers it).

### 3.4 `route` — the core capability

```sh
python3 agent-skill-catalog/tools-skill-catalog.py route \
  --request "Build a Flutter app for Android and iOS with offline sync, then publish to the Play Store" \
  --format md
```

```
# Routing plan

Request: Build a Flutter app for Android and iOS with offline sync, then publish to the Play Store

## Matched signals

- **delivery**: play-store
- **domain**: offline
- **framework**: flutter
- **lifecycle**: build, deploy
- **platform**: android, ios

## Selected skills (6)

1. **Flutter Cross-Platform Engineer** (`flutter-cross-platform-engineer`) - score 12.00 - Mobile Application Engineering
   - name matches request terms: flutter
   - covers framework:flutter signal (named in skill name)
   - subject area 'Mobile Application Engineering' fits android / ios / offline
2. **Android Package and Play Release Engineer** (`android-package-and-play-release-engineer`) - score 10.75 - ...
```

How it decides:

1. **Signal extraction** from `asset-routing-signals.json`: every signal value
   and alias is matched on word boundaries against the request, grouped into
   `platform`, `language`, `framework`, `artifact`, `delivery`, `domain`,
   `lifecycle`.
2. **Scoring** per skill: rare request words found in the skill name
   (`name_token` 3.0, down-weighted when a word appears in many skill names, so
   "app"/"engineer" cannot dominate), description tokens (1.0, capped at 4),
   signal coverage (base 2.5 × group priority: framework/language 2.0, domain
   1.6, artifact 1.2, platform 0.7, delivery 1.0, lifecycle 0.5; a signal merely
   mentioned in a description earns 15 % of that, a signal named in the skill
   name earns all of it), category affinity 1.0, fallback group affinity 1.2
   (e.g. `django` → backend category when no skill names Django), lifecycle
   category 1.5, Appendix-A composition membership 2.0.
3. **Selection**: candidates are walked in score order and taken when they cover
   a request signal that is still uncovered, then the remaining budget is filled
   by score and the list is re-sorted by score. So a strong skill always outranks
   a weaker one, and the set still spans the request's areas.
4. **Reporting**: `matched_signals`, `selected` (score, reasons, `covers`),
   `uncovered_signals` (honest gaps — e.g. "operate" for a build-and-deploy
   request), `orchestration_hint` (the App Factory Orchestrator when a request
   spans several stages), `runners_up` (5).

Real routing results (default `--max-skills 6`):

| Request | Top picks |
| --- | --- |
| Flutter app, Android+iOS, offline sync, push, Play Store | `flutter-cross-platform-engineer` 12.00, `android-package-and-play-release-engineer` 10.75, `ios-swift-application-engineer` 10.35, `notification-systems-engineer` 6.60, `progressive-web-app-engineer` 6.50, `ci-pipeline-engineer` 1.50 |
| Migrate a legacy Angular app to React with regression tests | `angular-enterprise-app-engineer` 11.80, `react-application-engineer` 10.00, `react-native-application-engineer` 9.00, `legacy-code-modernization-engineer` 7.00, `multi-language-code-repair-engineer` 5.00, `legacy-version-support-engineer` 4.50 |
| REST API in Python + Postgres, AWS, CI/CD, monitoring | `rest-api-engineer` 10.00, `python-version-aware-engineer` 9.00, `backup-restore-and-retention-planner` 7.00, `aws-application-platform-engineer` 6.50, `ready-to-build-project-generator` 5.50, `ci-pipeline-engineer` 4.50 (uncovered: `lifecycle:operate`) |
| OAuth login + role permissions on a Django app | `authentication-and-authorization-engineer` 5.00, then test/web fallbacks (uncovered: none) |
| Unity game with multiplayer and in-app purchases | `unity-game-engineer` 13.80, `multiplayer-game-systems-engineer` 8.80, game specialists |
| WCAG accessibility + keyboard navigation on a web portal | `accessibility-engineering-specialist` 6.00 first, then web specialists |
| Localize an iOS app into Japanese/Arabic with RTL | `legacy-version-support-engineer` 6.50, `ios-swift-application-engineer` 6.35, `internationalization-engineer` 5.50 |
| Desktop tool in Rust with a Qt UI and installer | `qt-c-application-engineer` 11.00, `linux-desktop-application-engineer` 8.50, `tauri-desktop-engineer` 8.50 |

`--max-skills` changes the budget only (verified 2 vs 6: exactly 2 selected at
`--max-skills 2`, `max_skills: 2` echoed back), and `--request-file` / `--request -`
let a caller feed the request from a file or stdin.

### 3.5 `spec` — skill specification draft

```sh
python3 agent-skill-catalog/tools-skill-catalog.py spec --skill flutter-cross-platform-engineer --format md
python3 agent-skill-catalog/tools-skill-catalog.py spec --skill skill-024 --format json --output draft-spec.md --force
```

Produces the catalogue's nine recommended fields — skill name, trigger
description, inputs, outputs, toolchain, version policy, workflow, verification,
boundaries — where the workflow, verification, boundaries, inputs and outputs
are assembled from the stage the skill belongs to (`intake`, `design`, `build`,
`verify`, `deliver`, `operate`, `secure`, `document`, `package`, `modernize`,
`orchestrate`) and the toolchain is derived from the skill's own text matched
against `spec_defaults.toolchain_groups`. Every draft is stamped `DRAFT` and
heuristically labelled: it is a starting point to review, not a claim that any
check ran. `--template` swaps the Markdown layout; the JSON form is
template-free.

### 3.6 `export`

```sh
python3 agent-skill-catalog/tools-skill-catalog.py export --format csv --output catalog.csv --force
python3 agent-skill-catalog/tools-skill-catalog.py export --format md  --output catalog.md  --force
python3 agent-skill-catalog/tools-skill-catalog.py export --format json --json-summary
```

CSV columns: `id,category_number,category_id,category,category_slug,number,name,slug,description`
(245 lines including the header; RFC-4180 quoting, `\n` line endings). Markdown
is a readable two-level document with category counts.

---

## 4. Inputs, outputs, parameters, configuration

### Dataset schema (`asset-catalog-schema.json`, schema_version `1.0`)

```
catalog   : title, source, source_format, schema, stats{categories,skills,appendices}, preamble[]
categories: id (cat-01..), number, name, slug
skills    : id (skill-001..), category_id, number, name, slug, description
appendices: id, letter, title, items[]
```

Slug rule everywhere: `^[a-z0-9]+(?:-[a-z0-9]+)*$`. IDs are stable, zero-padded
to `max(3, len(str(total)))`, and duplicate slugs get `-2`, `-3`.

### Routing policy (`asset-routing-signals.json`) — the tuning surface

* `limits`: `default_max_skills` (6), `max_request_chars` (4000),
  `orchestrator_skill` ("App Factory Orchestrator").
* `weights`: `name_token`, `desc_token`, `signal_match`, `category_affinity`,
  `lifecycle_category`, `composition`, `subject_term_factor` (0.15),
  `group_affinity` (1.2).
* `signal_group_priority`: per-group multiplier for matched signals.
* `signal_groups`: the vocabulary. Add a product, platform or jargon term here
  and routing starts recognising it.
* `category_affinities` / `group_affinities`: which category is the natural home
  for a signal, and the fallback when no single skill names it.
* `lifecycle_categories`: which categories serve which lifecycle stage.
* `composition`: Appendix A's recommended app-factory composition per stage.
* `spec_defaults`: `toolchain_groups`, `common_inputs`, `common_outputs`,
  `stage_io` (11 stages), `workflow_common`, `stage_workflow`,
  `verification_common`, `boundaries_common`, `version_policy`,
  `extra_tech_terms`.

### Operating limits

| Limit | Value | Behaviour when hit |
| --- | --- | --- |
| Ingest input size | 5 MiB (`--max-input-bytes`) | `E_INPUT_TOO_LARGE`, nothing written |
| Routing request length | 4000 chars (`max_request_chars`) | `E_INPUT_TOO_LARGE` with a hint to shorten or raise the limit |
| `route --max-skills` | 1–25 | `E_BAD_LIMIT` |
| `list`/`search --limit` | 1–500 | `E_BAD_LIMIT` |
| `--timeout` | default 60 s per command | `E_TIMEOUT` (deadline checked between skills/rows, not preemptively) |
| Reads | up to 3 attempts with short backoff | `E_READ_FAILED` |
| Writes | atomic temp file + `os.replace`, temp `*.asc-*` files removed on failure | overwrite refused unless `--force` / `--dry-run` |

**Cancellation**: `SIGINT` is caught and converted to `E_CANCELLED` with exit
130, and `BrokenPipeError` (piping into `head`) exits 0 quietly, so a caller can
stop the tool safely.

**Recovery**: re-running any command is safe. `ingest --dry-run` shows counts
and target without writing; `--force` is the deliberate, explicit approval gate
for overwriting anything; failures leave the previous output intact.

---

## 5. Safety and approval rules

* **No network, no secrets, no shell, no arbitrary code execution.** The tool
  only reads the files you name, writes the files you name, and prints results.
  It never evaluates input, never imports user data, and never spawns a shell.
  (`tools-selftest.py` spawns the CLI as a subprocess — that is the test harness,
  not the shipped behaviour.)
* **Overwrite protection is the destructive-action gate.** Any write to an
  existing path fails with `E_OUTPUT_EXISTS` and a hint; overwriting requires the
  explicit `--force` flag, which is the caller's approval. `--dry-run` previews a
  write without touching the filesystem.
* **Bounded and validated inputs**: size cap, request-length cap, limit ranges,
  dataset validation before use, atomic writes, temp-file cleanup, deadline
  checks, and a documented exit-code contract.
* **Honest output**: routing reports `uncovered_signals`; spec drafts are
  labelled `DRAFT` with heuristic fields; the self-test only reports checks it
  actually ran.

---

## 6. Validation and tests actually completed

Everything below was executed in this workspace on 2026-11-05
(CPython 3.13.16, Linux, `bash`) against these exact files.

1. **Help and capability list** — `--help` prints usage, all 9 commands, and the
   exit-code contract; `capabilities` lists all 9 with summaries (text verified,
   JSON verified).
2. **Self-test suite** — `python3 agent-skill-catalog/tools-selftest.py`:
   **70/70 checks passed, "All self-tests passed."** The suite drives the real
   CLI as a subprocess from a foreign temporary cwd and covers every command,
   error codes (`E_UNKNOWN_CATEGORY`, `E_UNKNOWN_SKILL`, `E_AMBIGUOUS_SKILL`,
   `E_EMPTY_QUERY`, `E_BAD_LIMIT`, `E_MISSING_INPUT`, `E_MISSING_ARG`,
   `E_PARSE_EMPTY`, `E_INPUT_TOO_LARGE`, `E_TIMEOUT`, `E_OUTPUT_EXISTS`), the
   recovery path, `--dry-run`, `--force`, stdin ingestion, HTML and Markdown
   ingestion, JSON/CSV/MD exports, routing caps, `list` pagination limits
   (`E_BAD_LIMIT` for `--limit 0` and for a negative `--offset`), and unit checks
   for slugify, format detection, tokenize, `Deadline`, and atomic writes.
3. **Full re-ingest of the source document** — 30 categories, 244 skills,
   2 appendices, 69 noise lines removed, `VALID (0 issue(s))`. The result is
   identical to the previous build: same 244 `(id, slug, name)` triples, zero
   description differences, identical categories, appendices and preamble.
4. **Dataset validation** — `validate` on the shipped dataset: `VALID (0 issues)`,
   exit 0. `validate` on a deliberately corrupted copy: 3 issues reported, exit 1.
5. **Input-change check** — `--max-skills 2` returns exactly 2 skills and echoes
   `max_skills: 2` (returns 6 by default); different requests produce different
   selections and different `uncovered_signals` (8 requests exercised, table in
   §3.4).
6. **Every major output opened and inspected** — JSON envelope, text/Markdown/CSV
   listings, routing plan (text + md), spec draft (md and JSON), `export` csv
   (245 lines) / md / json, `stats` (per-category table).
7. **Error and usage paths** — 11 malformed invocations checked by hand in
   addition to the suite: unknown category, unknown skill, empty request, bad
   limit, missing dataset, overwrite without `--force`, `--force --dry-run`,
   unknown command (exit 2), missing required option (exit 2).
8. **Configuration overrides** — `ASC_DATASET=/tmp/rebuild.json` changed the
   dataset used by `stats` (provenance switched to `catalog.txt`), proving the
   environment override path works.
9. **Clean temporary skill root** — the folder was copied next to a test
   `SKILL.md` in `/tmp/skillroot/`, then run with relative paths only
   (`python3 agent-skill-catalog/tools-skill-catalog.py stats --format json`,
   `capabilities`, `list`, `route --format md`, `export`, `validate`). All
   resolved their assets beside the script; no absolute path is needed, no file
   was missing.
10. **Extract-and-rerun** — the finished ZIP was extracted into a fresh temporary
    directory, its structure inspected, and the primary tool rerun from the
    extracted copy (see §8 for what the archive contains).
11. **Package hygiene** — exactly one `knowledge-readme.md`, one primary
    `tools-` entry point, two supporting `tools-` files, four `asset-` files
    (9 files total, reported in §1), no `SKILL.md`, no subfolder, no archive
    inside the archive, no `__pycache__`, no temporary or failed file.

### Limitations (real, not papered over)

* The dataset is only as good as the source document; the parser handles the
  shapes in `Catalog.pdf` (numbered categories, `Skill Name:`/`Description:`
  lines, lettered appendices, wrapped lines, Symbol bullets), but a catalog with
  a genuinely different structure will need either a pre-normalized text file or
  a code change in `parse_catalog`.
* Routing is deterministic lexical scoring plus a curated signal vocabulary. It
  has no semantic model, so a request phrased entirely in unseen jargon will
  match weakly — the fix is adding terms to `signal_groups`, not hoping. The
  `uncovered_signals` field is there so a caller can see the gap.
* `spec` output is a heuristic scaffold (explicitly labelled `DRAFT`). It cannot
  know your team's real toolchain versions, so version policy is a policy
  sentence, not a pinned matrix.
* `--timeout` is cooperative: it is checked between units of work, so a single
  enormous regex-bound line could overrun it. The size cap (5 MiB) keeps that
  bounded.
* No scheduler, no daemon, no database, no network sync. State is the JSON files
  on disk, deliberately.
* Regenerating the dataset from the PDF needs `pypdf` (external, optional). The
  shipped dataset means you never have to do that unless the source changes.

---

## 7. Troubleshooting, recovery, extension, maintenance

| Symptom | Cause | Fix |
| --- | --- | --- |
| `E_MISSING_ASSET` / `Core module missing` | `tools-*.py` split up, or files renamed | keep all 9 files in one flat folder; the CLI resolves assets beside itself |
| `E_OUTPUT_EXISTS` | target file already exists | re-run with `--force` (this is the approval step) or pick a new `--output` |
| `E_PARSE_EMPTY` | source has no catalog structure, or wrong `--input-format` | check the `1. Category` / `1. Skill Name:` / `Description:` shape, or force `--input-format text\|markdown\|html` |
| `E_DATASET_INVALID` | hand-edited dataset | run `validate --format json`, fix the listed issues (usually a slug pattern, a dangling `category_id`, or a stale `catalog.stats`) |
| `E_INPUT_TOO_LARGE` | >5 MiB source, or >4000-char request | raise `--max-input-bytes` or `limits.max_request_chars`, or shorten |
| `E_TIMEOUT` | very large input with a small `--timeout` | raise `--timeout` |
| Routing picks feel off | vocabulary gap or weight preference | tune `signal_groups`, `weights`, `signal_group_priority`, `category_affinities` in `asset-routing-signals.json`; no code change needed |
| Spec draft looks generic | skill description is short, or stage mapping is wrong | extend the description in the source, or adjust `SPEC_DEFAULTS.stage_io` / `_STAGE_BY_CATEGORY_KEYWORDS` |

**Extension recipes**

* *Different catalog*: `ingest` your own document into a new dataset, then run
  every command with `--dataset path/to/your-dataset.json` (or set `ASC_DATASET`).
* *Different spec layout*: copy `asset-spec-template.md`, edit it, use
  `--template` (or `ASC_TEMPLATE`). Keep the `{{placeholders}}` you still want.
* *New domain vocabulary*: add values to `signal_groups` and (optionally)
  affinities. The self-test asserts the vocabulary-driven behaviours, so re-run
  it after tuning.
* *New output format*: add a branch in `core.export_dataset` and a choice in the
  `export` subparser; then add a self-test check.

**Maintenance**: keep `catalog.stats` in sync via `ingest` rather than by hand;
re-run `python3 agent-skill-catalog/tools-selftest.py` after any edit (it is the
regression gate); bump nothing else — there is no dependency lockfile and no
build step.

---

## 8. Delivery

The folder ships as the root of one ZIP whose name describes the capability
(`agent-skill-catalog-toolkit.zip`). Extracting it yields `agent-skill-catalog/`
immediately: no wrapper folder, no nested archive, no `SKILL.md`. Total Skill
Tool files: **9**. All paths stay ≤ 3 levels from the future skill root
(`agent-skill-catalog/<file>`), which leaves room for the parent skill's own
`SKILL.md` and folders.
