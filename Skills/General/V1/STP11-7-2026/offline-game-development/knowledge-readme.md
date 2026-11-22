# Offline Game Development Skill Tool — the manual

One offline command line over the *Full Offline Game Development Skill Catalog*: 1419 capabilities
in 34 umbrella categories, plus the artifacts a team needs from them (specs, sprint plans,
checklists, prompt packs) and the gates that decide whether the work is done (rights, budgets,
evidence, coverage, structure). It never touches the network, never edits a game project, and never
fills in a team's decisions for them: everything the catalog does not state is marked `fill` and
left for a human.

Everything below was written against this folder as it ships. Where a number, a key, or a check
name appears, it came out of the tool itself (`info`, `config show`, `doctor`, `capabilities`)
rather than from a memory of it.

---

## 1. Install, run, and requirements

The Skill Tool is a flat folder of `tools-*.py` modules and `asset-*` data files. Put it beside
the parent `SKILL.md`:

```
your-skill/
├── SKILL.md
└── offline-game-development/
    ├── tools-offline-game-development.py   <- the entry point and the only thing you invoke
    ├── tools-runtime-kit.py                <- errors, envelope, config, writes, renderers
    ├── tools-catalog-engine.py             <- dataset, archetypes, selection, search, ingest
    ├── tools-artifact-generators.py        <- spec, plan, checklist, prompt packs
    ├── tools-gates.py                      <- validator, rights, budget, evidence, audit, doctor
    ├── tools-selftest.py                   <- the unit suite
    ├── knowledge-readme.md                 <- this manual
    └── asset-*.json / asset-*.md           <- the data the tool reads
```

* Python 3.9 or newer. Nothing else: no third-party packages, no build step, no daemon.
* Run it from the folder that *contains* `offline-game-development/`, with the full relative path:

```
$ python3 offline-game-development/tools-offline-game-development.py --help
$ python3 offline-game-development/tools-offline-game-development.py info --format md
$ python3 offline-game-development/tools-offline-game-development.py capabilities --format md
```

`info` answers "what am I holding" (edition, fingerprint, counts, engines, layers, the source's own
acceptance reference). `capabilities` answers "what can this tool do" and names the module behind
each command. `help <command>` prints that command's real usage text and every flag it accepts;
there are 23 commands, and each one is documented in section 5 below.

The tool never writes inside its own folder. Reports go where you point them (`--out`, `--out-dir`,
or `output.dir` in config), and an interrupted write leaves nothing behind except a `.tmp-*` file
that `cleanup` can find.

---

## 2. The catalog the tool reads

`asset-catalog.json` is the packaged dataset, mirrored from the source document:

| fact | value |
|---|---|
| title | Full Offline Game Development Skill Catalog |
| edition | Catalog Edition 1.0 (November 2026) |
| tagline | Offline-first, engine-aware, rights-cleared, target-tested |
| categories | 34, each with the count the source declared |
| capabilities | 1419 |
| engines | godot, unity, unreal, roblox |
| fingerprint | `dcc809e4ea28` (computed by `validate`, not stored) |

Five architecture layers divide the categories, and a capability's layer comes from its category
number:

| layer | categories | what it owns |
|---|---|---|
| production-control-plane | 1 | engine, platforms, architecture, budgets, offline tools, licenses, build route, acceptance matrix |
| engine-packs | 2–4 | exact Godot / Unity / Unreal / Roblox Studio conventions, exporters, tests, platform constraints |
| runtime-skills | 5–12 | gameplay, physics, AI, save data, networking, anti-cheat, performance, crash recovery |
| content-skills | 13–24 | code, scenes, levels, meshes, materials, animation, audio, UI, localization, accessibility |
| release-skills | 25–34 | packaging, store submission, certification, telemetry, patching, post-release support |

Every capability is one of six **description archetypes**. The archetype is not decoration: it fixes
the description template, the owner, the plan phase, the section contract a spec must satisfy, the
gates that close the work, and the kinds of evidence that count.

| archetype | label | default owner | plan phase | spec sections | gates | evidence kinds |
|---|---|---|---|---|---|---|
| define | Define | technical-director | decide | 9 | 4 | decision-record, review-signoff, updated-spec, milestone-note |
| create | Create | build-engineer | produce | 6 | 4 | build-log, checksum, rights-record, post-build-report |
| implement | Implement | gameplay-programmer | build | 8 | 4 | build-artifact, test-report, measurement, rights-record |
| operate | Operate | live-ops-engineer | operate | 5 | 4 | run-log, monitoring-snapshot, incident-note, drill-report |
| provide | Provide | tools-programmer | provide | 5 | 4 | package-manifest, version-notes, adoption-record, support-handoff |
| evaluate | Evaluate | qa-lead | verify | 7 | 4 | test-report, measurement, failure-evidence, regression-retest |

`plan` orders phases in the order decide → produce → build → operate → provide → verify, which is
why the six archetypes tile a sprint without anybody writing a workflow document.

Identifiers: every capability has a stable `id` (`c13e05`), a human `ref` (`13.5`, category then
entry, never zero-padded), a `slug`, and a `name`. Descriptions are *generated* from the archetype
template plus the subject, which is why `validate` can prove the dataset round-trips and why
`coverage` can tell you when a stored description has drifted from its template.

---

## 3. Referring to capabilities

Any command that acts on capabilities takes references as plain words:

```
$ python3 offline-game-development/tools-offline-game-development.py show c13e05 --format md
$ python3 offline-game-development/tools-offline-game-development.py show 13.5 --format md
$ python3 offline-game-development/tools-offline-game-development.py show sprite-generator --format md
$ python3 offline-game-development/tools-offline-game-development.py show "Sprite Generator" --format md
```

* An id, a short id, a slug, or an exact name resolves to one record. A slug or name fragment that
  matches several records is an `E-AMBIGUOUS` failure that lists the candidate ids — that is a
  feature: silently picking one capability out of four is how a spec ends up describing the wrong
  work.
* Something that cannot be found is `E-NOT-FOUND` (exit 4) with a *did you mean* hint built from the
  nearest names and slugs:

```
$ python3 offline-game-development/tools-offline-game-development.py show "spite generator" --format md  # exit 4
```

* A file path is refused with an explanation, because `entries/…` and `asset-catalog.json#c13e05`
  are where data *lives*, not what a capability *is*.
* `--from-file PATH` reads more references from a text file (one per line) or a JSON list;
  `--all` selects every capability; `--category`, `--layer`, `--tag`, `--difficulty`, `--role`,
  `--risk`, and `--engine-scoped` filter a selection without naming records.
* A selection larger than `limits.max_targets` (400 by default) stops with `E-LIMIT` rather than
  printing something you cannot read. Narrow it or raise `--max-targets`.
* `--limit` means two things and only two: rows for a browse (`list`, `search`, `export`,
  `coverage`), capabilities for a selection command (`spec`, `plan`, `checklist`, `prompts`,
  `evidence new`). `--limit 0` means "I asked for everything" and never warns.

Browsing and selection differ on purpose: `list`, `categories`, `export`, and `coverage` read the
whole catalog and page it (an unrequested `list` is capped at `limits.max_list_rows` = 500 rows and
says so with `W-TRUNCATED`/`W-CAPPED`), while the artifact commands refuse to work on anything they
have not been pointed at.

---

## 4. The output contract

Every command obeys the same rules, so you can script any of them:

* `--format json` (the default) prints one envelope and nothing else:
  `ok`, `tool`, `version`, `command`, `data`, `warnings`, `errors`, `meta`. `meta.exit_code` always
  matches the process exit code, so a parsed result and a shell test agree.
* `--format md`, `text`, or `csv` prints the artifact itself on stdout. If a gate then blocks, the
  error envelope goes to **stderr** so the artifact stays pipeable.
* `--out PATH` writes the artifact and leaves the envelope on stdout with `data.written`
  (`path`, `bytes`, `created`, `attempts`, `backup`). A `.json` target receives the JSON payload; a
  `.md`, `.csv`, or `.txt` target receives the rendered body. `PATH` may contain `{id}`, `{slug}`,
  `{engine}`, or `{sprint}`, and then one file is written per capability.
* `--quiet` prints nothing on stdout and puts any error envelope on stderr. `--stamp` adds
  `generated_at` and `meta.duration_ms`; without it two runs of the same command produce identical
  bytes, which is what makes these artifacts committable.
* `--report-only` writes and prints the report even when a gate blocks, and exits 0. It never waives
  a finding; it only lets a pipeline collect the report before failing on its own terms.
* Overwriting an existing file needs `--allow-overwrite --approve OVERWRITE`; `--backup` keeps a
  `.bak`. Replacing the packaged dataset needs `--approve REPLACE-DATASET`. A wrong token is
  refused, and the message names both tokens.

Exit codes, one per failure kind:

| code | meaning | typical cause |
|---|---|---|
| 0 | ok | — |
| 2 | E-USAGE | a flag combination that cannot work, a selection with nothing in it |
| 3 | E-INPUT | an unreadable or malformed input file, a config that is not JSON |
| 4 | E-NOT-FOUND / E-AMBIGUOUS | a reference that does not resolve, or resolves to several |
| 5 | E-GATE | a gate found something blocking (rights, budget, evidence, validation, selftest) |
| 6 | E-APPROVAL | a destructive action without its token, or with the wrong one |
| 7 | E-CANCELLED | SIGINT was delivered to the run |
| 8 | E-INTERNAL | a defect in the tool; re-run with `--verbose` |
| 9 | E-LIMIT / E-TIMEOUT | too many targets, too large an input, or `--timeout` reached |
| 10 | E-ENVIRONMENT | `doctor` says the install itself is wrong |

Warnings are data, not decoration: `W-TRUNCATED`, `W-CAPPED`, `W-SKIPPED`, `W-DESC-TEMPLATE`,
`W-RIGHTS-WAIVED`, and friends arrive as `{code, message, where}` inside the envelope, so a CI step
can fail on them if it wants.

---

## 5. The 23 commands

Grouped the way `capabilities` groups them. Every example is runnable from the folder containing
`offline-game-development/`.

### describe

`info` — the Skill Tool and the catalog: version, entry point, run command, engines, layers, edition,
fingerprint, and the source document's acceptance reference. `--engine` narrows the engine view and
only accepts the enabled packs:

```
$ python3 offline-game-development/tools-offline-game-development.py info --format md
$ python3 offline-game-development/tools-offline-game-development.py info --engine cryengine  # exit 2
```

`capabilities` — what this tool can do, which module implements each command, its honest
limitations, and which extra modules exist and why.

`help` — usage for one command, generated from the live argument parser, so it cannot drift from the
code: `help spec`, `help rights`, `help import`.

### list

`categories` — the 34 categories with declared versus actual counts and layer: a good starting point
when you know the area but not the id.

`list` — browse and filter records, with `--limit`/`--offset` paging and `--fields` to choose columns.

```
$ python3 offline-game-development/tools-offline-game-development.py list --category 13 --limit 5 --format md
$ python3 offline-game-development/tools-offline-game-development.py list --tag testing --limit 8 --format md
```

`show` — the full record for one or more capabilities: description, tags, owner, estimate,
difficulty, engines in scope, risk class with its reasons, and the catalog reference.

`search` — ranked search over names, descriptions, and tags, reporting the terms it used so a low
count is information rather than mystery.

```
$ python3 offline-game-development/tools-offline-game-development.py search "deterministic replay" --format md
```

### produce

`spec` — the capability spec: the archetype's section contract rendered as tables, every team-owned
row marked `fill`, the gates that block completion listed with their evidence kinds, and the engine
pack's concrete commands. This is the main artifact, and it is the reason the catalog is usable.

```
$ python3 offline-game-development/tools-offline-game-development.py spec c13e05 --engine godot --format md
$ python3 offline-game-development/tools-offline-game-development.py spec c13e05 --format json --out specs/c13e05.json
$ python3 offline-game-development/tools-offline-game-development.py spec --category 31 --limit 3 --out "specs/{id}.md" --format md
```

The same capability under a different engine, platform, or device profile is a different document:
`--engine`, `--platform`, `--device-profile`, and `--region` all reach into the spec, so an Unreal
spec talks about `UnrealEditor` commandlets and a Godot one talks about export templates.

`plan` — an ordered sprint plan for a selection: phase, owner, effort, estimate in days,
dependencies, gates, and which capabilities need an approval token before they can be closed.

```
$ python3 offline-game-development/tools-offline-game-development.py plan --from-file sprint.txt --format md
$ python3 offline-game-development/tools-offline-game-development.py plan c13e05 c34e23 --sprint 7 --format md
```

`checklist` — one tick-box per blocking section, per gate, and per evidence kind, from the same
contract as the spec, so the two never disagree. `--evidence FILE` ticks items from evidence you have
already recorded.

```
$ python3 offline-game-development/tools-offline-game-development.py checklist c13e05 --format md
$ python3 offline-game-development/tools-offline-game-development.py checklist c34e23 --evidence evidence/gate-results.json --format csv
```

`prompts` — prompt packs for a **local** model only: the context files to attach, the wording to use,
and the answers that are forbidden (inventing a license, inventing a measurement, claiming a build
passed). Configuring a remote provider or a non-loopback endpoint is refused with `E-INPUT`, and the
refusal is the point:

```
$ python3 offline-game-development/tools-offline-game-development.py prompts c13e05 --format md
$ python3 offline-game-development/tools-offline-game-development.py prompts c13e05 --provider-kind remote  # exit 3
```

### verify

`evidence new REF… --out PATH` writes a skeleton with one check per blocking section and per gate,
verdicts seeded to `not-run`; `evidence check --input FILE` validates filled records against the
schema and the gates, reports contradictions (a `pass` verdict while a check is `blocked`, a `fail`
without failure evidence, a gate the catalog requires that nobody recorded), and exits 5 when a block
stands. `--require-gates` makes a missing gate record blocking instead of a warning;
`--use-asset-fixture` runs the shipped pass/fail pair.

```
$ python3 offline-game-development/tools-offline-game-development.py evidence new c13e05 --out evidence/c13e05.json
$ python3 offline-game-development/tools-offline-game-development.py evidence check --input evidence/gate-results.json --format md
$ python3 offline-game-development/tools-offline-game-development.py evidence check --use-asset-fixture --require-gates --format md  # exit 5
$ python3 offline-game-development/tools-offline-game-development.py evidence new c13e05 --out evidence/c13e05.json --allow-overwrite  # exit 6
$ python3 offline-game-development/tools-offline-game-development.py evidence new c13e05 --out evidence/c13e05.json --allow-overwrite --approve OVERWRITE --backup
```

### gate

`rights --input MANIFEST` — the R1–R10 rights gate over an asset manifest (JSON records, a JSON list,
or a delimited file with a header row):

| gate | rule | severity |
|---|---|---|
| R1 | the license is one the policy knows | block |
| R2 | provenance fields are filled (`license`, `source`, `acquired_from`, `acquired_on`, `generated_by`) | block |
| R3 | commercial use matches what the license permits | block |
| R4 | redistribution or bundling matches the license | block |
| R5 | an attribution requirement has a credit line recorded | warn |
| R6 | a generated asset says which local model or tool produced it | block |
| R7 | engine and SDK licenses are respected in a shipped bundle | block |
| R8 | the per-asset record file exists (a blocker with `--check-record-files`) | warn |
| R9 | the record is inside the review window (180 days by policy) | warn |
| R10 | approval-required uses are named, not assumed | block |

A blank cell is unfilled; `n/a` is an answer. `--waive` records that a named human decided a finding
does not apply and needs `--approve WAIVE-RIGHTS`; a waiver adds `W-RIGHTS-WAIVED` and never deletes
a finding. `--commercial no` and `--bundle yes/no` set the declarations the policy judges.

```
$ python3 offline-game-development/tools-offline-game-development.py rights --input rights/manifest.json --format md
$ python3 offline-game-development/tools-offline-game-development.py rights --input rights/manifest.json --check-record-files --format md  # exit 5
$ python3 offline-game-development/tools-offline-game-development.py rights --input rights/manifest.json --waive --approve WAIVE-RIGHTS
```

`budget presets` lists the device profiles and metrics; `budget check --input FILE` compares measured
values with the ceilings for each record's profile. `asset-budget-presets.json` holds 10 profiles and
21 metrics; a profile defines a subset of them (mobile-low 19, server-authoritative 8), and a metric
the profile does not define is `review`, never a pass. Headroom is signed, and the direction of the
metric decides whether positive is good:

```
$ python3 offline-game-development/tools-offline-game-development.py budget presets --format md
$ python3 offline-game-development/tools-offline-game-development.py budget check --input measurements.json --format md
```

`offline-audit` audits this folder (and any `--directory` you add) for the properties that make the
offline promise true: flat layout and naming, no import of a network library, no shell-out, no
credential-shaped strings, and the presence of this readme. It reports findings instead of silently
"fixing" them, and a line may be exempted only by an explicit `# audit: allow` marker.

`coverage` reports the catalog against the source's own claims: per-category counts versus declared
counts, archetype, layer, owner, difficulty, engine, and risk distributions, and the drift findings
`validate` would refuse to ignore. `--expect-fingerprint dcc809e4ea28` fails (exit 5) if the dataset
you are pointing at is not the one this manual describes.

`validate` checks the dataset structurally (counts, ids, refs, archetype consistency, description
round-trip) and, with `--records FILE --kind NAME`, checks generated artifacts against one of the 11
record definitions in `asset-output-schema.json`. It also prints the fingerprint it computed.

```
$ python3 offline-game-development/tools-offline-game-development.py validate --format md
$ python3 offline-game-development/tools-offline-game-development.py validate --records specs/c13e05.json --kind spec_record --format md
```

### export / ingest

`export` writes records in the documented column order
(`id, ref, name, slug, category, category_title, layer, archetype, subject, description, tags, role,
difficulty, est_days, engines, risk_class, risk_reasons`) as CSV or JSON; `--columns` picks a subset
and an unknown name is a usage error that lists what is available.

`import` rebuilds a dataset from text you already have: Markdown, HTML, plain text, CSV, TSV, or
JSON. Headings, numbering, tags, CSS, and prose between entries are ignored or counted; category
names, skill names, and descriptions are kept, and a description that does not match an archetype
template gets its archetype inferred from the name. A PDF path is accepted only when the extracted
text sits beside it (`catalog.pdf` needs `catalog.txt` or `catalog.pdf.txt`), because no PDF reader is
shipped and pretending otherwise would make the ingest silently depend on a library. `--summary-only`
reports the parse without writing; writing the dataset over the packaged one needs
`--approve REPLACE-DATASET`.

```
$ python3 offline-game-development/tools-offline-game-development.py import --source offline-game-development/asset-sample-source.md --summary-only --format md
```

The two categories and ten entries in `asset-sample-source.md` are the fixture the self-test parses;
its output is a valid dataset you can hand to `--dataset`.

### configure / maintain

`config show` prints the merged configuration, the layers that produced it, the resolved asset paths,
and the editable keys. `config init --out FILE` writes an editable copy of the packaged defaults.

```
$ python3 offline-game-development/tools-offline-game-development.py config show --format md
$ python3 offline-game-development/tools-offline-game-development.py config init --out ogd.json
$ python3 offline-game-development/tools-offline-game-development.py config show --config ogd.json --format md
```

`doctor` runs the twelve read-only checks that decide whether the install is trustworthy, and exits 10
with `E-ENVIRONMENT` when one fails: python-version, sibling-modules, file-discipline,
assets-present, dataset-parses, catalog-round-trip, schema-consistency, config-layers,
approval-policy, write-target, offline-posture, readme-coverage.

```
$ python3 offline-game-development/tools-offline-game-development.py doctor --format md
```

`cleanup` finds the `.tmp-*` leftovers of interrupted writes (and `.bak` backups with
`--include-backups`) under `--directory`; `--delete` needs `--approve DELETE-FILES`.

`selftest` runs the unit suite in this folder: 109 cases that call every command, assert the
envelope, drive the gates, check the config layering, and verify that this manual's examples actually
run. It ends with a coverage report of its own — a command that no test reaches fails the suite.

```
$ python3 offline-game-development/tools-offline-game-development.py selftest --format md
$ python3 offline-game-development/tools-offline-game-development.py selftest --only TestSelection --format md
```

---

## 6. Configuration

Layers merge in this order, later over earlier, and `config show` names every layer that fired:

1. built-in defaults in `tools-runtime-kit.py`
2. `asset-config-default.json` in this folder
3. `--config PATH` (a JSON file; anything else is `E-INPUT`)
4. `OGD_CONFIG` (inline JSON, or a path)
5. command-line flags

The keys, with their packaged defaults — every one of them is read by the code, and `config show`
lists exactly the keys it reads as `editable_keys`:

| key | default | what it controls |
|---|---|---|
| `assets_dir` | `auto` | where `asset-*` files are read from; `auto` means beside this script |
| `dataset` | `asset-catalog.json` | the catalog dataset; `--dataset` points at an imported one |
| `archetypes` | `asset-archetypes.json` | the six archetypes, templates, section contracts, gates |
| `license_policy` | `asset-license-policy.json` | 20 licenses, 19 kinds, R1–R10, review window, waiver rule |
| `budget_presets` | `asset-budget-presets.json` | 10 profiles, 21 metrics, measurement rules, fallback guidance |
| `output_schema` | `asset-output-schema.json` | the envelope schema, 11 record definitions, `validator_support` |
| `spec_template` | `asset-spec-template.md` | the shell a spec is rendered into |
| `evidence_fixture` | `asset-evidence-fixture.json` | the pass/fail pair used by `--use-asset-fixture` |
| `engines.enabled` | `godot, unity, unreal, roblox` | the packs `--engine` may name; removing one makes it a usage error |
| `engines.default` | `godot` | the pack used when `--engine` is absent |
| `target.platform` | `desktop` | written into specs and prompt packs |
| `target.device_profile` | `desktop-mid` | the budget profile a spec quotes |
| `target.region` | `worldwide` | store and localization rows |
| `provider.kind` | `local` | `remote` is refused for prompt packs |
| `provider.model_id` / `provider.endpoint` | empty | a local model's id; an endpoint must be loopback |
| `provider.allow_network` | `false` | must stay false; `true` with a remote kind is `E-INPUT` |
| `limits.max_input_mb` | 64 | the largest file the tool will read |
| `limits.max_targets` | 400 | capabilities per selection |
| `limits.timeout_seconds` | 60 | wall clock per run; `--timeout 0.001` proves it bites |
| `limits.retries` | 3 | write attempts before giving up |
| `limits.max_list_rows` | 500 | rows in an unrequested browse |
| `limits.walk_depth` | 6 | how deep `cleanup` and the audit walk |
| `output.format` | `json` | the default when `--format` is absent |
| `output.dir` | empty | base directory for relative `--out` paths |
| `output.timestamp` | `false` | same as `--stamp` |
| `output.redact_secrets` | `true` | redact credential-shaped strings from artifacts |
| `output.deterministic` | `true` | sorted keys and stable ordering, so output diffs mean something |
| `approvals.require_for` | 9 actions | overwrite-file, delete-file, replace-dataset, rights-waiver, publish, signing, monetization-change, credential-access, device-flash |
| `offline.allow_network` | `false` | the whole tool refuses to import a network library; `offline-audit` checks it |
| `offline.forbid_remote_urls_in_specs` | `true` | a spec reminds that a configured provider endpoint must not be embedded in it |

`offline.forbid_remote_urls_in_specs` is enforced by the spec writer: a remote URL in a generated
artifact is a defect, and a loopback address is exempt because a local model server is a legitimate
offline dependency.

A zero in `limits` is not "off" for everything: `max_list_rows` and `max_targets` treat `0` as
unlimited, while `timeout_seconds` `0` means no deadline. `--limit 0` on the command line means the
same thing to a browse: show me everything, and don't warn about it.

---

## 7. The data files

Nine `asset-*` files sit in this folder. The tool reads all of them, `doctor` checks that the ones
config names are present, and each file explains itself in a `description` or `_comment` key so you
never have to open the code to know what a field is for.

| file | what it holds | who reads it |
|---|---|---|
| `asset-catalog.json` | the dataset: source document, 34 categories, 1419 entries with id, index, name, slug, archetype, subject, description | `info`, `list`, `show`, `search`, `spec`, `plan`, `checklist`, `prompts`, `export`, `coverage`, `validate` |
| `asset-archetypes.json` | the six archetypes: `catalog_template`, `sections` (field lists and blocking flags), `gates`, `evidence_kinds`, `default_owner`, `plan_phase`, plus `inference_rules` and `default_archetype` | description round-trip, archetype inference, every artifact's shape |
| `asset-license-policy.json` | 20 licenses with their permissions, 19 kinds, the ten R-gates, provenance required versus recommended fields, the 180-day review window, the waiver rule | `rights` |
| `asset-budget-presets.json` | 10 device profiles over 21 metrics with limits, units, directions and severities, measurement rules, and per-metric fallback guidance | `budget`, the budget rows of a spec |
| `asset-output-schema.json` | the envelope schema, 11 record definitions, the 18 validator keywords in `validator_support` | `validate`, and every artifact command |
| `asset-config-default.json` | the configuration layer described in section 6, with `_comment` keys that explain the folder-relative asset names | `load_config` |
| `asset-spec-template.md` | the shell a capability spec is rendered into, containing only `{{TOKEN}}` placeholders | `spec` |
| `asset-evidence-fixture.json` | one passing and one failing evidence record, verbatim in shape from the contract | `evidence check --use-asset-fixture`, the self-test |
| `asset-sample-source.md` | a ten-entry, two-category import fixture generated from `asset-archetypes.json` | `import`, the self-test |

Editing policy data is supported and expected: copy the file, change it, point config at it. The
gates will then judge your work by your policy, which is the only honest arrangement for offline
tooling.

---

## 8. A sprint, start to finish

From the folder containing `offline-game-development/`, with your own capability list in
`sprint.txt` (one reference per line):

1. `config show --format md` — confirm engine, profile, and limits before anything else.
2. `list --category 26 --limit 20 --format md` — see what the area actually contains.
3. `spec --from-file sprint.txt --out "specs/{id}.md" --format md` — one spec per capability, each
   with its `fill` rows and blocking gates.
4. `plan --from-file sprint.txt --sprint 7 --out plans/sprint-7.md --format md` — phases, owners,
   effort, dependencies, and what needs an approval.
5. `checklist --from-file sprint.txt --out checklists/sprint-7.md --format md` — the tick-boxes your
   review will actually read.
6. Hand the specs to the team. `prompts` output goes to your local model, never to a hosted one.
7. `rights --input rights/manifest.json --out reports/rights.md --format md` — asset paperwork, as
   part of the same sprint, not after it.
8. `budget check --input measurements.json --out reports/budget.md --format md` — numbers, not
   adjectives.
9. `evidence new --from-file sprint.txt --out "evidence/{id}.json"` for what still needs a record,
   then `evidence check --input ...` when the checks exist.
10. `coverage --format md` and `validate --format md` — did the catalog you used say what you think
    it said.
11. `selftest --format md` and `doctor --format md` — before you commit anything, including this
    readme: the self-test fails if a command lost its documentation.

Steps 3–5 write files, so they need `--out`; each artifact ends with the data it was built from
(`dataset`, `fingerprint`, engine, profile), so a report never detaches from the inputs that made it.

---

## 9. Troubleshooting

| symptom | what it means | what to do |
|---|---|---|
| `E-USAGE … needs something to act on` | the command got no reference and no filter | pass references, or `--category`/`--tag`, or `--all` |
| `E-AMBIGUOUS` with a list of ids | your wording matches several capabilities | pass the id |
| `E-LIMIT … above limits.max_targets` | a selection too large to be useful | add filters or `--limit`, or raise `--max-targets` deliberately |
| `E-TIMEOUT` | the run hit `--timeout` | narrow the selection, raise the timeout, or drop `--all` |
| `E-APPROVAL … not the one this action requires` | the token was missing or wrong | re-run with the named token; a near miss never writes a file |
| `E-INPUT … not a readable path` from `import` | a PDF was named with no extracted text beside it | run your PDF extractor once, keep the `.txt` next to it, re-run |
| `E-GATE` from `rights` | a license, provenance, or review-window finding stands | fix the manifest or record a waiver with `--approve WAIVE-RIGHTS` |
| `E-GATE` from `evidence check` | a check failed, or a gate nobody recorded | record the check, or ship without the claim |
| `E-ENVIRONMENT` from `doctor` | the install is inconsistent | the failing check names its own remediation; fix it and re-run |
| a spec says `fill` everywhere it matters | the catalog does not decide for you | that is the design: fill the row, re-run `checklist --evidence` |
| output bytes differ between two runs | `--stamp` was used, or a different dataset | drop `--stamp`; check `validate`'s fingerprint |
| `W-DESC-TEMPLATE` on a capability | a stored description drifted from its template | `coverage` names each one; regenerate or correct the row |

Two facts worth holding on to: the tool's own `offline-audit` and `doctor` are the fastest way to
tell "my input is wrong" from "the install is wrong", and every envelope carries `meta.exit_code`, so
a wrapper script can trust the JSON and the shell at the same time.

---

## 10. What this tool will not do

* It does not execute anything. It names the engine, compiler, device, and store commands in
  reports for a human or CI runner; it never runs them, and it does not pretend a build passed.
* It does not reach the network. A provider endpoint is accepted only when it is a loopback address
  with network flags off, and `offline-audit` fails the folder if anyone adds a network import.
* It does not read a PDF on its own, and it does not ship a Markdown or HTML library: ingest is
  regular-expression based over text you extracted, with structure ignored rather than guessed.
* It cannot verify a license or a measurement that was never recorded; the gates judge the records
  you supply and say plainly which fields are missing.
* It does not write into its own folder, does not delete anything without `--approve DELETE-FILES`,
  and does not replace its own dataset without `--approve REPLACE-DATASET`.
* It does not invent team decisions. Where the catalog is silent, an artifact says `fill`.

Run `capabilities --format json` for the machine-readable version of that list, and
`python3 offline-game-development/tools-selftest.py -v` when you want the tracebacks behind a
failing test.
