# story-narrative-studio - knowledge and operating guide

A reusable **Skill Tool component** for narrative planning, production and revision. It is
designed to be dropped beside a future skill package's root `SKILL.md`, not to be one:
this folder contains no `SKILL.md`, no sub-folders and no unprefixed files.

| Item | Value |
| --- | --- |
| Skill Tool name | `story-narrative-studio` |
| Version | 1.0.0 |
| Primary entry point | `story-narrative-studio/tools-story-narrative.py` |
| Language / runtime | Python 3.8+ (tested on Python 3.13.16), standard library only |
| Third-party dependencies | none |
| Files in this folder | 5 `tools-` files, 10 `asset-` files, this `knowledge-readme.md` |
| Source of the capability list | the supplied catalog *Story, Narrative, Literature, Poetry, Lyrics, Comics, and Game Writing Skill Catalog* - 37 categories, 444 named capabilities, transcribed into `asset-capability-catalog.json` |

---

## 1. Purpose

The catalog defines 444 named capabilities across 37 categories. Rather than 444 disconnected
prompts, this Skill Tool unifies them into **one deterministic production engine** with 15 runnable
operations. Every operation is backed by the named catalog capabilities listed by
`capabilities` (for example, `outline` is backed by 84 named capabilities from categories 3, 4, 12,
13, 18, 29 and 36), and every operation produces files a writer can edit and a production team can
schedule.

What it actually does:

* turns a theme, genre, audience and format into a **brief** with a promise, envelope, boundaries and budgets;
* generates **premise directions** with loglines, pressure, the lie believed and the climax cost;
* builds **beat sheets** from eleven structure presets with act budgets, tension targets and scene counts;
* builds **cast sheets** with wants, needs, wounds, voice, relationships and each character's answer to the thematic question;
* converts outlines into **scene cards** with goal, obstacle, tactic ladder, value shift and word budget;
* breaks a chapter into **comic pages and panels** with lettering budgets and splashes;
* builds and validates a **branch node graph** for visual novels, interactive fiction and games, including flag satisfiability and ending reachability;
* runs a **measurable manuscript audit** against a 14-dimension rubric and returns a verdict;
* validates a **continuity bible** against a packed JSON Schema subset, checks referential integrity, and cross-checks it against the manuscript;
* analyses **verse prosody**: syllables, rhyme scheme, stanza shape, refrains, meter estimate;
* converts audit findings into an **ordered, approval-gated revision plan**;
* **exports** a synopsis, treatment, style sheet, pitch or bible summary from a stored project bundle;
* lets any writer or agent **search the 444-item catalog** without leaving the CLI.

What it deliberately does **not** do: it does not write finished prose, call a model, call a network
service, publish anything, or judge taste. It produces structure, budgets, constraints, evidence and
prompt blocks - the production scaffolding an author or a downstream model completes.

---

## 2. Setup

No installation, no virtual environment, no package manager.

```bash
# from the future skill root (the folder that also holds SKILL.md)
bash -c 'python3 story-narrative-studio/tools-story-narrative.py help'
```

Requirements:

* Python 3.8 or newer on `PATH` as `python3` (Windows: `python tools-story-narrative.py ...`).
* UTF-8 locale for non-ASCII manuscripts (the tool reads and writes UTF-8 explicitly).
* Write permission on the output directory (`--out`, default `story-narrative-out/`).

Verify the installation at any time:

```bash
bash -c 'python3 story-narrative-studio/tools-story-narrative.py self-test'
```

`self-test` runs 15 checks in a temporary directory and prints one line per check. It exits 0 only
when every check passes (see section 9 for the checks and their results).

---

## 3. File roles and load order

Every file sits directly in `story-narrative-studio/`. Nothing is installed, imported from
`site-packages`, or loaded from a path outside the folder.

| File | Prefix role | What it does | Load order |
| --- | --- | --- | --- |
| `tools-story-narrative.py` | primary entry point | argument parsing, validation, dispatch, artifact writing, project bundles, `self-test` | 1 (you run this) |
| `tools-narrative-core.py` | shared runtime | limits, error envelope, guarded writes, project model, asset loading, JSON-Schema-subset validator, deadlines | 2 (loaded by the entry point) |
| `tools-narrative-generators.py` | generation engine | brief, premise, outline, characters, scenes, comic, game, export builders | 3 (loaded on demand by generating operations) |
| `tools-manuscript-audit.py` | analysis engine | manuscript metrics and rubric scoring, continuity validation, verse prosody | 3 (loaded on demand by `audit`, `continuity`, `verse`) |
| `tools-revision-planner.py` | planning engine | reuses the audit module, produces the ordered revision plan | 3 (loaded on demand by `revise`) |
| `asset-capability-catalog.json` | catalog data | 37 categories, 444 named capabilities with descriptions and keywords; maps each operation to the categories it serves | asset |
| `asset-structure-presets.json` | presets | 11 beat templates (positions, tension targets, functions), 17 format budgets, 5 audience envelopes | asset |
| `asset-genre-presets.json` | presets | 13 genre envelopes: conventions, promise, pitfalls, motifs, sensory palette, conflicts, roles, settings, pressures, antagonist answers, name pools | asset |
| `asset-rubric.json` | policy | 14 rubric dimensions with weights, anchors and auto-check names; per-format thresholds; severity weights | asset |
| `asset-prompts.json` | prompt set | model-agnostic prompt templates, 7 roles, 7 style voices, 10 revision passes | asset |
| `asset-continuity-schema.json` | schema | JSON Schema (subset) for the continuity bible | asset |
| `asset-scene-card-template.md` | template | the editable scene-card document rendered by `scenes` | asset |
| `asset-brief-template.md` | template | the brief document rendered by `brief` | asset |
| `asset-sample-manuscript.md` | fixture | ~690-word original short story with deliberate craft faults, used by `self-test` and documented examples | asset |
| `asset-sample-bible.json` | fixture | a valid continuity bible for the sample manuscript, including one unused location and two open threads so findings are real | asset |

The three engine modules are **not** importable by name: packaged filenames keep the required
`tools-` prefix, so they are loaded by exact filename through `importlib`, and a single `sns_core`
instance is registered in `sys.modules` so exception types and limits are shared across modules.
You never load them yourself; run `tools-story-narrative.py`.

---

## 4. Commands

All commands assume the future skill root as the working directory, so paths stay relative and
case-sensitive. `--out DIR` defaults to `story-narrative-out/` and is created on first write.

```bash
CLI='python3 story-narrative-studio/tools-story-narrative.py'
```

### 4.1 Discovery

```bash
bash -c "$CLI help"                      # workflow, exit codes, pipeline
bash -c "$CLI help outline"              # one operation, with an example
bash -c "$CLI capabilities"              # 15 operations, options, catalog coverage
bash -c "$CLI capabilities --op scenes"  # which catalog categories back one operation
bash -c "$CLI capabilities --categories" # plus the full 37-category listing
bash -c "$CLI capabilities --op-json"    # machine-readable capability table
bash -c "$CLI catalog"                   # the 37 categories
bash -c "$CLI catalog --category 3"      # all 12 named capabilities of a category (number or slug)
bash -c "$CLI catalog --search reveal"   # search 444 names, descriptions and keywords
bash -c "$CLI catalog --op audit"        # categories that back an operation
```

### 4.2 Generating operations

Shared story options (accepted by every generating operation and by `audit`, `revise`,
`continuity` and `verse`):

| Option | Values | Meaning |
| --- | --- | --- |
| `--title` | text (<=160 chars) | working title; inherited from the project bundle when omitted |
| `--format` | `novel`, `novella`, `short-story`, `flash`, `screenplay`, `tv`, `comic`, `manga`, `visual-novel`, `interactive-fiction`, `ttrpg`, `audio-drama`, `stage`, `poetry`, `lyrics`, `memoir`, `game` | production budget, deliverable list, default structure |
| `--genre` | `fantasy`, `science-fiction`, `mystery`, `romance`, `horror`, `thriller`, `literary`, `historical`, `comedy`, `slice-of-life`, `young-adult`, `cyberpunk`, `fabulation` | genre envelope used for motifs, conflicts, roles, settings, sensory palette |
| `--audience` | `children`, `middle-grade`, `ya`, `adult`, `general` (default `general`) | content envelope, POV ceiling, length envelope, rubric thresholds |
| `--words` | 100 - 400000 | target length; defaults to the format's preset budget |
| `--structure` | `three-act`, `hero-journey`, `save-the-cat`, `kishotenketsu`, `seven-point`, `short-story`, `flash`, `tv-pilot`, `comic-22page`, `vn-route`, `ttrpg-session` | beat template; defaults from the format |
| `--themes` | comma separated | drives the thematic question and audit theme checks |
| `--non-negotiables`, `--boundaries` | comma separated | added to the brief's contract |
| `--style` | `plain`, `lyrical`, `noir`, `warm`, `clinical`, `epic`, `vernacular` | voice contract in the brief and style sheet |
| `--platform`, `--deadline` | free text | recorded in the brief |
| `--seed` | integer (default `20260101`) | deterministic generation; same seed = identical output |
| `--allow-custom` | flag | permit a format/genre/audience/structure outside the presets, using generic envelopes plus a warning |
| `--beats`, `--scenes`, `--cast`, `--pages`, `--nodes`, `--routes` | integers | override the structural counts |

Shared plumbing options (all operations): `--out DIR`, `--name FILE`, `--project FILE`, `--json`,
`--stdout`, `--approve-overwrite`, `--timeout SECONDS` (default 60, max 600), `--quiet`.

#### `brief` - production contract

```bash
bash -c "$CLI brief --title 'Low Tide' --format novella --genre mystery --audience adult \
  --words 30000 --themes 'truth, loyalty' --non-negotiables 'single POV' --project project.json"
```

Writes `<slug>-brief.md` and `.json`: production facts, emotional promise, thematic question with
three competing answers, audience envelope, non-negotiables, boundaries, deliverables, originality
note, three open decisions, production budget (scenes, chapters, dialogue ratio, POV ceiling) and
the brief interview prompts.

#### `premise` - directions, not a single idea

```bash
bash -c "$CLI premise --title 'The Salt Road' --format novel --genre fantasy --audience adult \
  --directions 4 --seed 7 --project project.json"
```

`--directions` accepts 2-7. Each direction uses a different lens (inversion, constraint, witness,
aftermath, bargain, double life, custody) and reports logline, opening image, central pressure, the
opposition's competing answer, the lie believed with its originating wound, the climax cost,
structural risk and a differentiation check.

#### `outline` - beat sheet with budgets

```bash
bash -c "$CLI outline --title 'The Salt Road' --format novel --genre fantasy --audience adult \
  --structure save-the-cat --project project.json"
```

Each beat carries act, position, tension target, scene and word budget, POV, live conflict, tactics,
value shift, cast on stage, sensory anchor, function, causal obligation, cost paid, setup paid, and
an expansion prompt. Act totals and a tension-curve diagram are appended. `--structure kishotenketsu`,
`hero-journey`, `seven-point`, `short-story`, `flash`, `tv-pilot`, `comic-22page`, `vn-route` and
`ttrpg-session` are equally available.

#### `characters` - cast sheet

```bash
bash -c "$CLI characters --format novel --genre fantasy --audience adult --cast 8 --project project.json"
```

Names are unique, ages follow the audience envelope, and wants/needs/lies are derived from the
genre's conflicts. When the project already holds an outline, the protagonist and opposition names
are inherited so the pipeline stays consistent. `--protagonist 'Nadia Osei'` forces the lead name.

#### `scenes` - scene cards

```bash
bash -c "$CLI scenes --format novel --genre fantasy --project project.json"
bash -c "$CLI scenes --format short-story --genre literary --outline outline.json"
```

Reads the outline from `--outline` or the project bundle; without either it stops with exit code 4
and a hint (see 10.2). Scene budgets are distributed by tension, a summary table is produced, and
each scene is rendered from `asset-scene-card-template.md` with a drafting prompt.

#### `comic` - pages and panels

```bash
bash -c "$CLI comic --title 'Low Tide' --format manga --genre mystery --audience ya --pages 20"
```

Per page: beat, act, panel count, splash flag, dialogue-word budget, page-turn hook and beat
function, plus a thumbnail grid. Warnings fire when lettering or panel averages exceed a sustainable
schedule.

#### `game` - branch graph

```bash
bash -c "$CLI game --title 'Signal Debt' --format visual-novel --genre cyberpunk \
  --nodes 14 --routes 3"
```

Builds start node, choice nodes, route-lock node and one ending per route; validates reachability,
dead ends, flag satisfiability and unreachable endings; emits a text graph, per-node choice records
(flags set, flags required, consequence) and node prompts. `--nodes` must be at least `--routes + 4`.

#### `export` - assembled deliverables

```bash
bash -c "$CLI export --kind synopsis --word-budget 600 --project project.json"
bash -c "$CLI export --kind treatment --word-budget 900 --project project.json"
bash -c "$CLI export --kind style-sheet --project project.json"
bash -c "$CLI export --kind pitch --project project.json"
bash -c "$CLI export --kind bible-summary --project project.json"
```

Exports reuse stored sections. A missing section is never invented: the export says what is missing
and how to attach it (for example, `bible-summary` lists the nine required bible sections and the
exact `continuity --project ... --bible ...` command).

### 4.3 Analysis operations

#### `audit` - rubric-scored verdict

```bash
bash -c "$CLI audit --input story-narrative-studio/asset-sample-manuscript.md \
  --format short-story --genre literary --audience adult --words 900"
bash -c "$CLI audit --input manuscript.md --format novel --genre fantasy --outline outline.json"
bash -c "$CLI audit --text 'Inline passage...' --format flash --genre comedy"
```

Computes: word/sentence/section counts; Flesch-Kincaid grade estimate; sentence mean, spread, long
and short shares, opener monotony; adverb, abstract-noun, hedge, filter-word, cliché and
exposition-marker densities; passive constructions; dialogue share, line count and tag density;
POV pronoun mix; section word counts and a pressure proxy; beat coverage when an outline is
supplied; content-boundary and rights triage. It then scores up to 9 of the 14 rubric dimensions
automatically (11 when both an outline and `--themes` are supplied), leaves the reading-dependent
dimensions marked `manual`, applies issue penalties
and returns a verdict of `ready`, `revise` or `blocked`, plus ranked next actions.

`--fail-on-block` exits 5 when the verdict is `blocked` (CI use). The report and data are still
written first, so the evidence is never lost.

#### `continuity` - bible validation and cross-check

```bash
bash -c "$CLI continuity --bible story-narrative-studio/asset-sample-bible.json \
  --input story-narrative-studio/asset-sample-manuscript.md"
bash -c "$CLI continuity --bible bible.json --project project.json"   # stores the bible in the bundle
```

Validates against `asset-continuity-schema.json`, then checks referential integrity (relationship
targets, item owners, event locations and participants, faction oppositions, duplicate order
values), thread discipline (resolved without payoff, open with a resolution point, unlocated
threads), rules without cost, sources marked verified without a citation, invented claims that must
stay framed as fiction, duplicate glossary terms, and - when a manuscript is supplied - characters
and locations that never appear, plus every thread still open. `--fail-on-critical` exits 5 when
schema-level findings exist.

#### `verse` - prosody

```bash
bash -c "$CLI verse --input poem.txt"
bash -c "$CLI verse --text 'The tide came in at four/and took the salt road down'"
```

Reports lines, stanzas and stanza shape, syllables per line (mean, spread, range), rhyme scheme from
line endings, rhymed-line share, refrains, meter estimate, a per-line table with a stress skeleton,
and findings (metrical inconsistency, partial rhyme, missing refrain anchor, single-line input).
Syllable and stress values are spelling-based estimates and are labelled as such.

#### `revise` - ordered, gated revision plan

```bash
bash -c "$CLI revise --input story-narrative-studio/asset-sample-manuscript.md \
  --format short-story --genre literary --audience adult --words 900"
bash -c "$CLI revise --input manuscript.md --format novel --genre fantasy \
  --pass structural,character,prose --max-tasks 25"
```

Reuses the audit exactly once, then emits tasks with location, evidence, causal problem, smallest
sufficient fix, risk, owning revision pass and an approval flag; a pass sequence (structural,
character, scene, dialogue, prose, pacing, continuity, theme, ethics, readiness) with effort
estimate; section-level evidence; and an explicit order of work. Valid passes are listed on
`--pass` failure.

---

## 5. Inputs, outputs and configuration

### 5.1 Inputs

| Input | Accepted form | Validation |
| --- | --- | --- |
| Manuscript / poem | UTF-8 `.md` or `.txt` via `--input`, or inline `--text` | must exist, be a file, be UTF-8, and be <= 8 MB |
| Outline | JSON from `outline` via `--outline` | must parse as JSON; beat fields are read defensively |
| Cast | JSON from `characters` via `--cast-file` | must parse as JSON |
| Premise | JSON from `premise` via `--premise` | must parse; missing directions degrade with a warning |
| Bible | JSON via `--bible` or `project.bible` | must parse, then validate against the packed schema |
| Project | JSON via `--project` | must carry `schema: story-narrative-studio/project` |
| Enumerations | `--format`, `--genre`, `--audience`, `--structure`, `--style` | case-insensitive match against presets; unknown values are rejected unless `--allow-custom` |
| Numbers | `--words`, `--beats`, `--scenes`, `--cast`, `--pages`, `--nodes`, `--routes`, `--directions`, `--max-tasks`, `--word-budget`, `--seed`, `--timeout` | ranges enforced before any work starts |

### 5.2 Outputs

Every generating operation writes two files with the same stem: `-<op>.md` (the deliverable) and
`-<op>.json` (the structured data used to build it). `audit`, `continuity`, `verse` and `revise`
follow the same pattern. With `--stdout` the markdown goes to stdout and no file is written.

Artifacts are recorded in the result envelope:

```json
{
  "status": "ok",
  "tool": "story-narrative-studio",
  "version": "1.0.0",
  "op": "outline",
  "generated_at": "2026-11-05T11:26:23Z",
  "result": { "beats": 15, "scenes": 51, "words": 90000, "structure": "Save the Cat (15 Beats)" },
  "warnings": ["Scene allocation was normalised ..."],
  "artifacts": [{ "path": "story-narrative-out/the-salt-road-outline.md", "bytes": 19710, "kind": "outline deliverable (markdown)" }]
}
```

`--json` prints the full envelope (with complete data, not the summary); without it a single human
line is printed. Errors are always JSON on stderr with `status: "error"`, a `code`, a `field`, a
`hint` and `details`.

### 5.3 Project bundle

`--project FILE` makes the operations a pipeline. The bundle is written by the tool only:

```
schema, schema_version, tool_version, created_at, updated_at, title, meta,
brief, premise, outline, characters, scenes, comic, game, bible, audits[]
```

Each generating operation stores its section; `continuity` stores the validated bible and appends an
audit record; `audit` appends a verdict record (the last 20 are kept); `scenes` and `export` read
earlier sections; the title is inherited so later files keep the same slug.

```bash
bash -c "$CLI brief   --title 'The Salt Road' --format novel --genre fantasy --audience adult --project project.json"
bash -c "$CLI premise --format novel --genre fantasy --directions 3 --project project.json"
bash -c "$CLI outline --format novel --genre fantasy --project project.json"
bash -c "$CLI characters --format novel --genre fantasy --project project.json"
bash -c "$CLI scenes --format novel --genre fantasy --project project.json"
bash -c "$CLI export --kind synopsis --project project.json"
```

### 5.4 Configuration assets

Presets are data, not code. Edit the asset file and the next run uses it - no code change:

* add a format or change a word budget -> `asset-structure-presets.json` (`formats`);
* add a beat template or reorder tension targets -> `asset-structure-presets.json` (`structures`);
* change an audience envelope or rubric threshold -> `asset-structure-presets.json` (`audiences`) and `asset-rubric.json` (`format_thresholds`);
* add a genre envelope (conventions, conflicts, roles, settings, name pools) -> `asset-genre-presets.json`;
* change rubric weights, anchors or auto-checks -> `asset-rubric.json`;
* change prompt blocks, style voices or revision passes -> `asset-prompts.json`;
* extend the bible schema -> `asset-continuity-schema.json` (the validator supports the keywords listed in 6.2).

Keep filenames exact: the loader resolves only `asset-*.json` / `asset-*.md` files that sit beside
the tools in this folder, and it refuses names containing a path separator.

---

## 6. Data contracts

### 6.1 Continuity bible (abridged)

```json
{
  "schema": "story-narrative-studio/continuity-bible",
  "title": "The Salt Road", "format": "short-story", "genre": "literary", "audience": "adult",
  "characters": [{"id": "maren", "name": "Maren", "role": "protagonist",
                  "want": "...", "need": "...", "wound": "...",
                  "relationships": [{"to": "dov", "type": "old alliance"}],
                  "knowledge": [{"fact": "the tally was short", "learned_at": "ev-01"}]}],
  "locations": [{"id": "barn", "name": "The tithe barn"}],
  "factions": [{"id": "upper-town", "name": "The upper town", "goal": "...", "opposes": ["salt-counting-houses"]}],
  "rules": [{"id": "rule-tithe", "domain": "civic", "statement": "...", "cost": "..."}],
  "items": [{"id": "ledger", "name": "The clerk's ledger", "owner": "clerk"}],
  "timeline": [{"id": "ev-01", "when": "day 1, 04:00", "order": 1, "event": "...",
                "location": "barn", "participants": ["maren", "dov"]}],
  "threads": [{"id": "th-tally", "question": "...", "status": "open", "opened_at": "ev-00"}],
  "sources": [{"id": "src-bell", "claim": "...", "source": "...", "confidence": "invented"}],
  "glossary": [{"term": "tithe", "meaning": "..."}]
}
```

Ids match `^[a-z0-9][a-z0-9-]*$`. Roles are one of `protagonist`, `antagonist`, `ally`, `foil`,
`mentor`, `love-interest`, `supporting`, `minor`. Thread status is `open`, `resolved` or
`abandoned`. Source confidence is `verified`, `probable`, `uncertain` or `invented`. The root rejects
unknown keys, so typos surface as critical findings instead of silent data loss.

### 6.2 Schema support

`validate_schema` implements this JSON Schema subset - enough to make the bible contract strict
without a dependency: `type` (object, array, string, integer, number, boolean, null), `enum`,
`pattern`, `minLength`, `maxLength`, `minimum`, `maximum`, `minItems`, `maxItems`, `items`,
`required`, `properties`, `additionalProperties: false`. `$ref`, `allOf`, `oneOf`, `anyOf`,
`format`, `uniqueItems` and remote references are **not** supported; rewrite such constraints as
explicit keys or check them in code.

### 6.3 Outline, cast and node-graph data

`outline.json` -> `beats[]` with `position`, `tension`, `target_words`, `scenes`, `purpose`,
`turning_point`, `act`; plus `acts`, `protagonist`, `antagonist`, `thematic_question`.
`characters.json` -> `cast[]` with `id`, `name`, `role`, `want`, `need`, `wound`, `lie`, `voice`,
and `relationships[]`. `game.json` -> `nodes[]` with `type`, `choices[]` (`target`, `sets.flags`,
`requires`, `consequence`), `routes[]`, `endings[]`, `reachable[]`.

---

## 7. Rubric, verdicts and thresholds

14 dimensions, weighted in `asset-rubric.json`: coherence, causality, character agency, character
depth, theme integration, structure, pacing, prose clarity, originality, emotional impact, dialogue
economy, format readiness, audience fit, rights and representation.

Verdict logic: mean of the automatically scored dimensions, minus `severity_weights`/10 for the
issues found (capped at 1.2), compared with the format's `min_total`; any dimension listed in
`block_below` below its floor forces `blocked`. Formats with their own thresholds include `novel`,
`short-story`, `screenplay`, `comic`, `visual-novel`, `children` and `ya`. Five dimensions stay
`manual` by design - coherence, character depth and emotional impact always, theme integration until
`--themes` is supplied, structure until an outline is supplied, and continuity until a bible is
supplied - because counting cannot measure them.

Scores are heuristics with cited evidence, not editorial verdicts. Every issue prints a location and
the measurement that produced it, so a reader can disagree with the tool and see why.

---

## 8. Safety, approval and limits

**Approval gates**

* Overwriting any existing output file - markdown, JSON, or project bundle - requires
  `--approve-overwrite`. Without it the run stops with exit code 3 and writes nothing.
* `revise` marks structural, ethics and format tasks as requiring approval, and lists the task ids
  in an approval gate line before any agent edits a manuscript.
* Writes are atomic (temp file + `os.replace`), so an interrupted run cannot truncate an existing deliverable.

**Bounded behaviour**

* No network access, no shell execution, no code evaluation, no model calls, no telemetry.
* No secrets, credentials, endpoints or paid services are referenced anywhere.
* Manuscripts are capped at 8 MB, outputs at 400,000 words of target material, beats/pages/nodes at
  the documented ceilings, and each operation carries a soft deadline (`--timeout`, default 60s, max
  600s) checked between stages with exit code 5 on expiry.
* Output paths must be relative filenames under `--out`; absolute paths and `..` traversal are rejected.

**Ethics content the tool enforces**

The audit's rights triage flags method detail (instructions for harm), stereotype phrasing,
possible real-person references, and unlabelled invented claims, and links each flag to the
`representation_ethics` dimension. The genre assets carry content notes (for example: no real
unsolved crimes, no operational detail for weapons or intrusion, no humour built on protected
characteristics). These are triage aids, not a rights review or a compliance certification.

---

## 9. Validation and tests actually completed

Everything below was run against this exact folder on Python 3.13.16 with the standard library only.

**`self-test` - 15 checks, 15 passed** (also the regression suite for future edits):

```
PASS generate-brief / generate-premise / generate-outline / generate-characters
PASS generate-comic / generate-game / generate-scenes
PASS audit-manuscript          verdict blocked, weighted 2.67 on the fixture
PASS continuity-bible          4 findings, status "issues"
PASS verse-prosody             scheme ABCD on a 4-line fixture
PASS revision-plan             13 tasks, 4 gated
PASS export-synopsis           2,128 bytes from a project bundle
PASS determinism               same seed identical, different seed different
PASS validation-and-approval   invalid input, traversal, unapproved overwrite rejected
PASS project-roundtrip         bundle written and reloaded
```

**Full pipeline executed** (brief -> premise -> outline -> characters -> scenes -> continuity ->
export x4) for two different projects (`novel/fantasy/adult`, `novella/mystery/adult`) and a
short-story/horror path; every artifact opened and read, not merely created.

**Interface coverage executed**

| Test | Result |
| --- | --- |
| `help`, `help <op>`, `capabilities`, `capabilities --op <op>`, `capabilities --categories`, `capabilities --op-json` | correct text/JSON |
| `catalog`, `catalog --category 3`, `catalog --search reveal`, `catalog --op scenes` | 37 categories / 444 capabilities; searches return real rows |
| valid inputs for all 15 operations | exit 0, artifacts written |
| invalid enum (`--format nonsuchformat`, `--genre nonsuch`, `--structure bogus`) | exit 2 with the accepted values listed |
| `--allow-custom` with an unknown format | exit 0, generic envelopes, warning recorded |
| missing input file | exit 4 with hint |
| `scenes` without an outline | exit 4 with the recovery hint |
| `revise --pass bogus` | exit 2 listing the 10 valid passes |
| `catalog --search zzz` | exit 4 (no match) |
| unapproved overwrite | exit 3, file untouched |
| `--timeout 0.001` | exit 5 (budget), `--timeout 601` | exit 2 (ceiling) |
| `--words 9000000` | exit 2 (range) |
| bible with an unknown key and an invalid role | 2 critical findings, `--fail-on-critical` exits 5 |
| bible with a dangling relationship, resolved thread without payoff | high-severity findings, `--fail-on-critical` exits 0 (correct: not schema-critical) |
| `audit --fail-on-block` on the fixture | report written, exit 5 |
| `--stdout` | markdown to stdout, no files written |
| `--json` | full envelope with result, warnings, artifacts |
| parse every generated `.json` (14 files) | all valid JSON |
| mutation check | changing `--seed`, `--genre`, `--format`, `--words`, `--pages`, `--nodes` changes the output as expected; same seed reproduces identical bytes |

**Reference integrity** - every `asset-*` filename in `asset-capability-catalog.json`, the schema,
the templates and this document was checked to exist in the folder with exact case; `grep` for
`asset-` references returns only the ten packed files. `asset-scene-card-template.md` is used by
`scenes`, `asset-brief-template.md` by `brief`, `asset-prompts.json` by six operations,
`asset-rubric.json` by `audit` and `verdict`, `asset-continuity-schema.json` by `continuity`,
`asset-capability-catalog.json` by `capabilities`/`catalog`, `asset-structure-presets.json` and
`asset-genre-presets.json` by every generator, and the two fixtures by `self-test` and the examples.

**Packaging checks actually run**

1. Copied the folder into a clean temporary skill root containing one test `SKILL.md`; ran
   `capabilities`, `self-test`, `brief`, `outline`, `audit` from that root using only relative paths
   (`story-narrative-studio/...`) - all exit 0, 15/15 self-test checks passed.
2. Built `story-narrative-studio-skill-tool.zip`: 16 entries, one root folder
   `story-narrative-studio/`, maximum depth 1 under the ZIP root, no wrapper folder, no nested
   archive, no `__pycache__`.
3. Extracted the ZIP into a fresh directory beside a test `SKILL.md` and reran `help`, `self-test`
   (15/15), `catalog --category 19`, `game --out out` and `continuity` - all exit 0 and all
   referenced resources resolved with exact case.
4. Confirmed `__pycache__` is never created next to the packaged files (`sys.dont_write_bytecode`).

**Not tested / not claimed**: no model was called, so the prompt blocks are not measured for model
output quality; prose quality scores are heuristics; pronunciation-based prosody was not validated
against a phonology corpus; no Windows, macOS or Python 3.8-3.12 interpreter was available in this
environment (the code avoids 3.9+ syntax, but only 3.13.16 was executed).

---

## 10. Troubleshooting and recovery

### 10.1 Exit codes

| Code | Meaning | Recovery |
| --- | --- | --- |
| 0 | success | - |
| 2 | invalid input (enum, range, timeout, style, pass) | read the `hint`, which lists accepted values |
| 3 | approval required | inspect the existing file, then rerun with `--approve-overwrite` |
| 4 | missing file or asset | check the path; restore missing `asset-*` files beside the tools |
| 5 | limit, deadline or blocked verdict | reduce size, raise `--timeout`, or fix the critical findings |
| 6 | internal error | rerun with `--json`, then `self-test` |

### 10.2 Named recovery paths

* **`scenes` without an outline** - exit 4: run `outline ... --project project.json` first, or pass
  `--outline outline.json`. Tested.
* **Accidental overwrite attempt** - exit 3: the existing file is untouched and the message names it.
  Rerun with `--approve-overwrite`, or use `--name` to write beside it.
* **Unknown enum** - exit 2 lists every valid value; `--allow-custom` continues with generic
  envelopes when the medium genuinely is not in the presets.
* **Blocked audit** - the report is still written. Work the ranked `Next actions` in order, then
  rerun `audit` and compare `verdict.weighted_score`, not impressions.
* **Bible fails to validate** - the findings name the JSON path (`$.characters[0].role`). Fix the
  value, rerun; do not delete the schema to silence it.
* **Timeout expiry** - exit 5 with the stage name. Lower `--words`/`--beats`/`--pages`/`--nodes` or
  raise `--timeout` (max 600) rather than retrying the same run unchanged.
* **Corrupted project bundle** - the loader rejects unknown schemas rather than guessing. Regenerate
  the sections; the individual `-<op>.json` files remain usable as `--outline`/`--cast-file`/`--bible` inputs.
* **Restored a deleted asset** - rerun `self-test`; it fails loudly and names the missing file.

### 10.3 Diagnostics

```bash
bash -c "$CLI self-test --keep"                        # keeps the temp directory, prints its path
bash -c "$CLI capabilities --op-json"                  # operations, options and catalog coverage
bash -c "$CLI outline --title T --format novel --genre fantasy --json --stdout"   # full envelope, no files
```

---

## 11. Extension and maintenance

* **New structure preset** - add an entry under `structures` with `beats[]` (`key`, `name`, `act`,
  `position` 0-1, `tension` 0-1, `purpose`, `turning_point`) and reference it from a format's
  `structure`. No code change; `outline` picks it up, and `--beats` still overrides the count.
* **New format** - add to `formats` with `default_words`, `avg_scene_words`, `dialogue_ratio`,
  `max_pov`, `structure`, `chapter_words`, `deliverables`. `comic` and `game` read budgets from here too.
* **New genre** - add to `genres` with `conventions`, `reader_promise`, `pitfalls`, `motifs`,
  `sensory_palette`, `conflicts` (each phrased `"X against Y"`, which the generators parse),
  `tone_words`, `name_pool`, `required_systems`, `roles`, `settings`, `pressures`,
  `antagonist_answers`, `content_notes`.
* **New rubric dimension** - add it to `dimensions`; to score it automatically, add a check in
  `score_manuscript` (`tools-manuscript-audit.py`) and return `{score, confidence, evidence}`.
  Dimensions without a check stay `manual` and are excluded from the mean.
* **New operation** - add a function `cmd_<name>` in `tools-story-narrative.py`, register it in
  `OPERATIONS` and `dispatch`, add a subparser in `build_parser`, map its catalog categories in
  `asset-capability-catalog.json` -> `operations`, and document it here and in `HELP_TEXT`.
* **New revision pass** - add it to `asset-prompts.json` -> `revision_passes`; `revise --pass`
  validates against that list and `_pass_for` decides which dimensions route to it.
* **New schema keyword** - extend `validate_schema` in `tools-narrative-core.py` and note the change
  in 6.2; keep unsupported keywords documented rather than silently ignored.

Maintenance rules: keep the single `knowledge-readme.md`; keep every file at the top level of this
folder; keep the `tools-`/`asset-` prefixes; keep exceptions raised as `core.StudioError` so exit
codes stay meaningful; run `self-test` plus one full pipeline before packaging; and bump
`TOOL_VERSION` in `tools-narrative-core.py` with the matching `version` reported by `capabilities`.

---

## 12. Packaging and compatibility with a future skill

* This folder contains **no `SKILL.md`** and is not a complete skill package. It is a component to
  be copied beside a parent package's root `SKILL.md`, which supplies the single skill entry file.
* Every file is directly inside `story-narrative-studio/` - no sub-folders, no numbering folders, no
  wrappers, no archives, no caches, no logs, no installed dependencies.
* Depth from the future skill root: `story-narrative-studio/<file>` - two levels, inside the
  three-level limit. 16 files, far under the 500-file package ceiling.
* All internal references are relative to the future skill root and forward-slashed, for example
  `story-narrative-studio/tools-story-narrative.py` and
  `story-narrative-studio/asset-sample-manuscript.md`. There are no absolute, drive-letter,
  parent-traversal, tenant or installation paths.
* Filenames are lowercase ASCII with single hyphens and exact case; commands in this document use
  the same case as the files on disk.
* Delivery: one descriptively named ZIP, `story-narrative-studio-skill-tool.zip`, whose root
  contains the `story-narrative-studio/` folder directly - no outer wrapper folder, no nested archive.
