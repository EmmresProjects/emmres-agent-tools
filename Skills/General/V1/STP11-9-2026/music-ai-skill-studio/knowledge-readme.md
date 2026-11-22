# knowledge-readme.md — music-ai-skill-studio

One Skill Tool that turns a **music + AI capability catalog** (or any capability list) into
**executable, editable, reviewable production cases**, with quality control for WAV files,
approval gating for sensitive actions, and deterministic delivery packaging.

Folder: `music-ai-skill-studio/` · Studio version 1.0.0 · Python 3.9+ · **standard library only**

> This folder is the reusable Skill Tool *component*. It contains no `SKILL.md`. Copy the folder
> beside a parent skill's root `SKILL.md` and it works unchanged.

---

## 1. Purpose

Large music/AI capability catalogs (the supplied one lists **400 skills in 40 categories**) are
documents. A document cannot be run, checked or handed off. This Skill Tool closes that gap:

| The catalog says | This Skill Tool produces |
| --- | --- |
| "Mixing (10 skills)" | one unified case covering all 10 mixing capabilities, with per-capability deliverables and checks |
| "produces editable and traceable outputs" | a Markdown brief, a JSON plan, a checklist CSV and a handoff manifest you can edit |
| "validates musical intent, technical quality, rights, accessibility" | a validation checklist, blocking rights/consent flags, and an approval gate before publishing |
| "defines required inputs and constraints" | intake validation that rejects missing, malformed, out-of-range or unsafe input with a stable error code |
| "ends with a clean handoff" | a deterministic ZIP delivery, a file manifest, and a safe extractor |

Everything is deterministic and offline: **no network calls, no model inference, no audio DSP
beyond uncompressed WAV reading/writing**.

---

## 2. What is implemented (complete capability list)

| # | Capability | Command | Status |
| --- | --- | --- | --- |
| 1 | Built-in help and capability listing | `--help`, `capabilities` | implemented, tested |
| 2 | Catalog intake from plain text, Markdown, copied HTML, PDF-derived text | `list` | implemented, tested |
| 3 | Category lookup by number, slug or name fragment; capability lookup by name/slug | `list`, `plan` | implemented, tested |
| 4 | Unified multi-capability case planning (category umbrella, several capabilities, or ad-hoc) | `plan` | implemented, tested |
| 5 | Operation templating: 11 families (analysis, planning, generation, audio processing, verification, rights & release, education, performance, documentation, marketing, sound design) + 6 category profiles + universal checks | `plan` | implemented, tested |
| 6 | Intake validation (required fields, enums, tempo range, meter format, duration, string/list limits) | `plan`, `verify-intake` | implemented, tested |
| 7 | Blocking flags for unknown/pending/restricted rights and missing/pending voice consent | `plan`, `verify-intake` | implemented, tested |
| 8 | Approval gating for `release_publish`, `rights_signing`, `payment`, `credential_use`, `irreversible_delete` | `plan`, `verify-intake`, `batch` | implemented, tested |
| 9 | Editable deliverables: Markdown brief, plan JSON, checklist CSV, handoff CSV | `plan` | implemented, tested |
| 10 | Deterministic ZIP delivery (fixed timestamps, sorted entries, integrity check) | `plan`, `pack` | implemented, tested |
| 11 | Batch rendering with per-job isolation, retries, per-job timeouts, JSON report, optional parallelism | `batch` | implemented, tested |
| 12 | WAV integrity QC + level QC: peak dBFS, RMS dBFS, crest factor, clipped-sample ratio, DC offset, duration, channels, sample rate, codec | `qc-audio` | implemented, tested |
| 13 | Threshold profiles (strict master, sample pack, spoken word, archive integrity, default) | `qc-audio --qc-profile` | implemented, tested |
| 14 | Silence trimming that never overwrites its input | `trim-silence` | implemented, tested |
| 15 | Traversal-guarded extraction of delivery archives | `unpack` | implemented, tested |
| 16 | Configurable limits, retries, timeouts and output switches | `--workflow` | implemented, tested |
| 17 | Structured JSON output and stable exit codes for every command | `--json` | implemented, tested |
| 18 | Runnable end-to-end self test, including a clean "skill root" packaging check | `tools-music_ai_studio_selftest.py` | implemented, tested |

**Deliberately not implemented** (not claimed anywhere in the output): audio DSP beyond WAV
header/level inspection, loudness in LUFS, true-peak / inter-sample peak detection, stem
separation, transcription, notation rendering, model inference, text-to-music generation,
network access, PDF parsing. See §11.

---

## 3. Files in this Skill Tool

| File | Prefix | Role |
| --- | --- | --- |
| `tools-music_ai_studio_cli.py` | tools- | **Primary entry point.** Command-line interface: argument parsing, exit codes, JSON envelopes, help. Imports the engine. |
| `tools-music_ai_studio.py` | tools- | **Engine module.** Catalog parsing, capability profiling, intake validation, approval gating, rendering, WAV QC, deterministic archive I/O. Imported by both the CLI and the self test. Never reads `argv`. |
| `tools-music_ai_studio_selftest.py` | tools- | **Runnable test suite.** Drives the real CLI end to end: valid, invalid, missing, extreme and recovery paths, plus the packaging checks. |
| `knowledge-readme.md` | knowledge- | This file: the only documentation. |
| `asset-catalog-music-ai-400.md` | asset- | The supplied 400-skill / 40-category catalog as a default `--source`, and a worked format example. |
| `asset-intake-schema.json` | asset- | Draft-07 JSON Schema for intake documents (`--intake` / `--intake-file`). |
| `asset-intake-example.json` | asset- | Realistic intake for a mix + stem delivery case, including a gated publish request. |
| `asset-workflow-default.json` | asset- | Every workflow key with its built-in default value; pass with `--workflow`. |
| `asset-qc-profiles.json` | asset- | Five QC threshold profiles for `qc-audio --qc-profile … --qc-profile-name …`. |
| `asset-delivery-profiles.json` | asset- | Delivery targets (loudness, true peak, sample rate, bit depth, channels, format) for streaming, video, podcast, game, film, physical and archive delivery. Reference data for briefs and handoffs. |
| `asset-handoff-manifest.csv` | asset- | Handoff manifest example with the exact columns the tool writes, extended with owner/notes fields for human use. |
| `asset-handoff-checklist-template.md` | asset- | Human sign-off sheet (package integrity, technical, listening, rights, recovery) to copy next to a delivery. |
| `asset-batch-jobs-example.json` | asset- | Five-job batch file: three valid jobs, one approval-gated job, one missing-goal job, one malformed-intake job. Used by the self test. |

Execution order: CLI (`tools-music_ai_studio_cli.py`) → engine (`tools-music_ai_studio.py`) →
assets and user files. The self test is independent of the CLI's own code paths: it shells out
to the CLI exactly as a user would.

Total: **3 tools files, 1 knowledge file, 9 assets files = 13 files, maximum path depth 2.**

---

## 4. Setup and exact commands

Requirements: **Python 3.9+**. No third-party packages, no install step, no virtualenv required.

```sh
python3 --version          # expect 3.9 or newer
```

All commands below are written for **bash/sh** from the directory that contains the Skill Tool
folder (referred to as the future skill root).

### 4.1 Help and capability list

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py --help
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py capabilities
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py capabilities --json
```

### 4.2 Parse a catalog (`list`)

```sh
# the shipped catalog: expect 40 categories / 400 capabilities
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list \
  --source music-ai-skill-studio/asset-catalog-music-ai-400.md

# one category, by number, as Markdown
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list \
  --source music-ai-skill-studio/asset-catalog-music-ai-400.md \
  --category 19 --format markdown

# inline text (numbered list, bullets, or copied HTML all work)
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list \
  --text "Mixing
- Bus Gain Reviewer
- Reference Match Checker"
```

### 4.3 Build a case (`plan`)

```sh
# one category (all 10 capabilities) into one unified case
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py plan \
  --source music-ai-skill-studio/asset-catalog-music-ai-400.md \
  --category 19 \
  --title "Salt Harbor Mix" \
  --intake-file music-ai-skill-studio/asset-intake-example.json \
  --approve APPROVE-RELEASE \
  --out out/salt-harbor

# several capabilities from different categories, unified
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py plan \
  --source music-ai-skill-studio/asset-catalog-music-ai-400.md \
  --capability "Mix Bus Balance Reviewer" \
  --capability "Loudness Measurement Analyst" \
  --intake-file music-ai-skill-studio/asset-intake-example.json \
  --approve APPROVE-RELEASE --out out/mix-and-loudness

# ad-hoc case: no catalog at all
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py plan \
  --title "Album Sequence Review" \
  --intake '{"goal":"Sequence the 10 approved masters","inputs":["10 masters","sequence notes"]}' \
  --out out/sequence

# validate only, write nothing
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py plan \
  --source music-ai-skill-studio/asset-catalog-music-ai-400.md --category 19 \
  --intake-file music-ai-skill-studio/asset-intake-example.json \
  --approve APPROVE-RELEASE --dry-run --json
```

A case writes `<case-id>-plan.json`, `<case-id>-brief.md`, `<case-id>-checklist.csv`,
`<case-id>-handoff.csv` and `<case-id>-delivery.zip` into `--out`.

### 4.4 Intake validation and approvals (`verify-intake`)

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py verify-intake \
  --intake-file music-ai-skill-studio/asset-intake-example.json \
  --approve APPROVE-RELEASE

python3 music-ai-skill-studio/tools-music_ai_studio_cli.py verify-intake --json \
  --intake '{"goal":"Sign the split sheet","inputs":["split sheet v2"],"actions":["rights_signing"]}'
# exit code 5, error.approval_required, tokens_expected ["APPROVE-RIGHTS"]
```

### 4.5 Batch (`batch`)

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py batch \
  --jobs music-ai-skill-studio/asset-batch-jobs-example.json \
  --source-root music-ai-skill-studio \
  --workers 2 --out out/batch
# writes out/batch/00N-<slug>/… and out/batch/batch-report.json
# invalid jobs are reported, never crash the batch; exit 3 if any job failed
```

### 4.6 WAV QC (`qc-audio`)

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py qc-audio \
  --audio master.wav --json --out-file out/qc-master.json

python3 music-ai-skill-studio/tools-music_ai_studio_cli.py qc-audio \
  --audio-dir stems/ \
  --qc-profile music-ai-skill-studio/asset-qc-profiles.json \
  --qc-profile-name strict-master \
  --out-file out/qc-stems.json
# exit 0 all pass, 3 one or more files failed, 6 not a WAV, 4 limit exceeded
```

Generate a test signal without external tools:

```sh
python3 -c "import sys;sys.path.insert(0,'music-ai-skill-studio');import importlib;\
c=importlib.import_module('tools-music_ai_studio');c.make_test_wav('tone.wav',seconds=2.0)"
```

### 4.7 Silence trim (`trim-silence`)

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py trim-silence \
  --audio raw-take.wav --out-file raw-take-trimmed.wav --threshold-dbfs -55 --json
```

### 4.8 Package and extract (`pack`, `unpack`)

```sh
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py pack \
  --input out/salt-harbor --name salt-harbor \
  --archive out/salt-harbor-delivery.zip --out-manifest out/salt-harbor-manifest.csv

python3 music-ai-skill-studio/tools-music_ai_studio_cli.py unpack \
  --archive out/salt-harbor-delivery.zip --dest out/verify
```

`pack` refuses nested folders inside the delivery (flatten first) and never rewrites sources.
`unpack` refuses absolute paths, `..` traversal and zip bombs expanding past its byte budget.

### 4.9 Self test

```sh
# full suite (63 checks)
python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py

# also compare a PDF-extracted text export against the shipped markdown catalog
python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --pdf-text catalog.txt

# keep the scratch workspace, or emit a JSON report
python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --keep
python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --json
```

Exit code 0 means every check passed; 1 means at least one failed (details are printed).

---

## 5. Inputs, outputs, parameters, configuration

### 5.1 Catalog source formats accepted by `list` / `plan --source`

The parser is tolerant by design. All of these parse to the same capabilities:

```text
1. Skill Name: Mix Bus Balance Reviewer          # keyword form, numbered
- Mix Bus Balance Reviewer                        # bullet form
**Mix Bus Balance Reviewer**                      # Markdown bold
## 19. Mixing                                     # Markdown category heading
<li data-id="3"><span>3.</span> Mix Bus Balance Reviewer</li>   # copied HTML
This category covers gain staging, bus structure, …            # category scope line
```

It also skips page furniture that PDF extraction leaves behind: `Music AI Skill Catalog | 7`,
`Category Index` listing entries with `(10 skills)`, hard-wrapped description paragraphs,
repeated category headers across page breaks, and document preamble text before the first
category. HTML tags, CSS classes, `data-*` attributes, bullets (`-`, `•`, `▪`, `·`), numbering
marks and extra spacing are removed before matching. Capability names are de-duplicated per
category with a stable `-2`, `-3` suffix.

**PDF files are not parsed.** Convert first (the CLI prints this exact recipe when handed a
`%PDF` file):

```sh
python3 -c "import pypdf,sys;print('\n'.join((p.extract_text() or '') for p in pypdf.PdfReader(sys.argv[1]).pages))" in.pdf > catalog.txt
python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list --source catalog.txt
```

### 5.2 Intake document (`--intake '<json>'` or `--intake-file PATH`)

Schema: `music-ai-skill-studio/asset-intake-schema.json` · Example:
`music-ai-skill-studio/asset-intake-example.json`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `goal` | string | **yes** | 3–4000 chars; empty or whitespace is rejected |
| `inputs` | string or string[] | **yes** | at least one item; `;` or newlines allowed in the string form |
| `constraints`, `references` | string or string[] | no | references are for stylistic analysis, never imitation |
| `audience`, `deadline`, `platform`, `key`, `duration`, `stem_format`, `model`, `seed`, `prompt`, `notes` | string | no | rendered into the brief and the plan |
| `tempo_bpm` | number or numeric string | no | 20–400, normalized to `104` |
| `meter` | string | no | `4/4`, `7/8`, `6/8+3/4` |
| `rights_status` | enum | no (default `unknown`) | `original, cleared, public domain, cc0, cc by, cc by-sa, licensed, unknown, pending, restricted` |
| `voice_consent` | enum | no (default `not_applicable`) | `not_applicable, consented, public domain, synthetic none required, missing, pending` |
| `actions` | string[] | no | `release_publish, rights_signing, payment, credential_use, irreversible_delete` — each needs a token |

Enums accept `not_applicable`, `not-applicable` and `not applicable` (case-insensitive).
`unknown` / `pending` / `restricted` rights and `missing` / `pending` consent produce
**blocking flags** in every generated file. They do not stop the case from being planned —
the work can proceed, publishing cannot, and every deliverable states why.

### 5.3 Workflow configuration (`--workflow PATH`)

Defaults live in `asset-workflow-default.json` and in the engine's `DEFAULT_WORKFLOW`.
Unknown keys are rejected only where they would change behaviour silently; limits, retries,
timeout, `output.archive`, `qc.*` and `naming.max_slug_words` are honoured.

| Key | Default | Meaning |
| --- | --- | --- |
| `limits.max_input_bytes` | 4 000 000 | catalog / intake text size |
| `limits.max_catalog_entries` | 2 000 | capabilities parsed from one source |
| `limits.max_categories` | 200 | categories per source |
| `limits.max_batch_jobs` | 200 | jobs accepted by `batch` |
| `limits.max_audio_bytes` | 268 435 456 | WAV size cap (256 MiB) |
| `limits.max_audio_scan_seconds` | 30.0 | wall-clock budget per WAV for measurement |
| `limits.max_string_length` / `max_name_length` | 4 000 / 120 | field caps |
| `retries` | 0 | retries for recoverable I/O errors (0–5); validation errors are never retried |
| `timeout_seconds` | 60 | wall-clock guard per operation (POSIX `SIGALRM`) |
| `output.archive` | true | write the delivery ZIP with a case |
| `qc.*` | see defaults | QC thresholds when no profile is supplied |
| `naming.max_slug_words` | 6 | words kept in generated slugs |

### 5.4 Outputs

`plan.json` — stable machine contract:

```json
{
  "schema_version": "1.0", "studio": "music-ai-skill-studio",
  "case_id": "salt-harbor-mix-20261109T000000Z", "timestamp_utc": "2026-11-09T00:00:00Z",
  "kind": "category", "title": "Salt Harbor Mix", "slug": "salt-harbor-mix",
  "category": "Mixing", "capability": null,
  "intake": { "goal": "...", "inputs": ["..."], "rights_status": "original",
              "voice_consent": "consented", "blocking_flags": [] },
  "profiles": [ { "capability": "Mix Bus Balance Reviewer", "family": "audio_processing",
                  "risk": "medium", "deliverables": ["..."], "checks": ["..."] } ],
  "coverage": { "capabilities_in_case": 10, "families": ["..."],
                "risk_level": "high", "blocking_flags": [] },
  "deliverables": ["..."], "checks": ["..."],
  "workflow": { "limits": {}, "retries": 0, "timeout_seconds": 60, "output": {} },
  "approvals": { "actions": [{"action": "release_publish", "token": "APPROVE-RELEASE",
                              "approved": true}], "approved_count": 1 },
  "limitations": ["..."]
}
```

`brief.md` — ten sections: brief, musical/technical parameters, per-capability work plan,
consolidated deliverable checklist, validation checklist, rights/consent/disclosure,
decision log table, approval gates, handoff, limits of the document.

`checklist.csv` — columns `block,item,owner,status,evidence,notes`; one row per deliverable,
per validation check, per blocking flag, plus a human-review sign-off row. Filter `block` to
split work between producer, engineer and rights reviewer.

`handoff.csv` — columns `case_id,kind,file,role,status`; the recipient's inventory of what was
delivered and why.

`delivery.zip` — the four files above under a single slug folder, fixed timestamps and sorted
entries, so identical inputs produce a byte-identical archive (verified in the self test).

### 5.5 Exit codes and error envelope

| Code | Meaning |
| --- | --- |
| 0 | success (for `qc-audio`: every file passed; for `batch`: every job succeeded) |
| 2 | usage error (argparse) |
| 3 | input / validation error (`input_error`, `validation_error`, `invalid_config`, `invalid_json`, `category_not_found`, `file_not_found`, `no_capabilities_found`, `unsafe_overwrite`, `bad_structure`, …) |
| 4 | limit exceeded (`limit_exceeded`, including `timeout`) |
| 5 | approval required (`approval_required`) |
| 6 | unsupported input (`unsupported_audio_format`, `pdf_requires_conversion`) |
| 130 | interrupted (SIGINT); partial `*.tmp-*` files are removed |

With `--json`, every failure is a structured object on stdout:

```json
{ "ok": false,
  "error": { "code": "approval_required", "message": "approval required for: release_publish",
             "details": { "missing": ["release_publish"],
                          "tokens_expected": ["APPROVE-RELEASE"],
                          "note": "Re-run with --approve APPROVE-RELEASE" } } }
```

---

## 6. How each capability works (with examples)

### 6.1 Catalog intake
Each non-empty line is stripped of bullets, numbering, Markdown emphasis and HTML, then matched
in this order: `Skill Name:` keyword → `Description:` label → category scope sentence
(`This category covers …`) → description continuation → category header → bare capability name.
A bare line becomes a category header only with positive evidence (Markdown `#`,
`(n skills)` marker, a following scope sentence, or a following `1. Skill Name:` item);
otherwise it is a capability name. Lines before the first category header are treated as
document preamble and ignored when categories exist (counted in `stats.warnings`), or used as a
flat list under `--default-category` when the source has no categories at all.

Verified: the 48-page PDF's text layer parses to exactly **40 categories × 400 capabilities** —
byte-for-byte the same 400 names as the shipped markdown asset (`--pdf-text` test).

### 6.2 Unified case planning
`--category` folds every capability in that category into one case (default cap 40 via
`--max-capabilities`). `--capability` accepts repeated names, inside or outside the catalog.
Several categories may be combined. Each capability gets an operation template:

* **family** from 11 ordered rules matched against the capability name
  (analyst/measurement → `analysis`; curator/planner/designer → `planning`;
  writer/prompt/generator → `generation`; mix/master/stem/restore → `audio_processing`;
  review/QA/test/recovery → `verification`; rights/licence/publish/consent → `rights_and_release`;
  teach/curriculum/practice → `education`; live/coding/performance → `performance`;
  tag/catalog/archive/metadata/notation → `documentation`; marketing/audience/release →
  `marketing`; voice/vocal/timbre/synthesis → `sound_design`; otherwise `general`);
* **category profile** adds rights/provenance, voice-consent, delivery, session, accessibility or
  citation deliverables and raises risk (`high` for rights and voice categories);
* **universal checks** are appended to every capability: inputs validated, outputs editable and
  traceable, rights/consent recorded, accessibility and safety noted, human review recorded.

Example: planning category 19 (Mixing) yields 10 profiled capabilities, a merged deliverable list
and merged validation list, and `coverage.families` shows every family involved.

### 6.3 Intake validation
Rejects: empty/absent goal, absent inputs, non-object intake, tempo outside 20–400, non-numeric
tempo, badly formed meter, duration without a number, unknown rights/consent values, over-long
strings, and lists above 50 items. Every rejection names the field.

### 6.4 Approval gating
Five actions are gated. The token must be supplied explicitly:

| Action | Token |
| --- | --- |
| `release_publish` | `APPROVE-RELEASE` |
| `rights_signing` | `APPROVE-RIGHTS` |
| `payment` | `APPROVE-PAYMENT` |
| `credential_use` | `APPROVE-CREDENTIALS` |
| `irreversible_delete` | `APPROVE-DELETE` |

Without the token the command exits 5 and writes nothing (`--dry-run`) or fails the job
(`batch`). Nothing in this tool publishes, signs, pays, uses credentials or deletes anything —
the gate exists so a future parent skill cannot skip the human decision silently.

### 6.5 Batch
`--jobs FILE` with `{"defaults": {...}, "jobs": [...]}`. Each job may set `title`, `category`,
`capability`, `source`, `intake`, `approve`, `timestamp`, `case_id`. Each job gets its own folder
and its own error envelope; one bad job never stops the run. `--workers 1..8` enables
`ThreadPoolExecutor` parallelism. The report records `jobs_total`, `jobs_ok`, `jobs_failed`,
per-job errors and `elapsed_seconds`. Relative `source` paths resolve against the jobs file's
folder, then `--source-root`, then the working directory.

The shipped example deliberately contains two valid jobs, one approval-gated job, one
missing-goal job and one malformed-intake job. Measured result: **5 jobs → 2 ok, 3 isolated
failures** (`approval_required`, `validation_error`, `intake_not_object`), exit code 3, with the
two valid cases rendered in full.

### 6.6 WAV QC
Reads RIFF/WAVE headers and chunks, validates declared sizes against the real file size, and
measures uniformly sampled frames (up to ~200 000): peak dBFS, RMS dBFS, crest factor, clipped
sample ratio (|x| ≥ 0.999), approximate DC offset, duration, channels, sample rate and codec.
Supported sample formats: 8/16/24/32-bit integer PCM and 32/64-bit float. Verdicts compare
against the active threshold profile; every report also lists its own limitations (RMS ≠ LUFS,
sample peak ≠ true peak, uniform sampling).

### 6.7 Silence trimming
16-bit PCM only. Writes a new file with a fresh 44-byte header; refuses to overwrite the input
(`unsafe_overwrite`); reports trimmed head/tail frames and kept duration.

### 6.8 Packaging
`pack`/`plan` write ZIPs with fixed 1980-01-01 timestamps, sorted entries, deflate compression and
a post-write `testzip()` integrity check. `unpack` re-validates every entry path before writing.
In packaging mode (`validate_archive_entry(package_layout=True)`, used by the self test's guard
checks) it also enforces Skill Tool rules: one top-level folder, no subfolders, `tools-`/
`knowledge-`/`asset-` prefixes, no `SKILL.md`, no nested archives.

---

## 7. Safety and approval rules

1. **No autonomous irreversible action.** Publishing, rights signing, payments, credential use and
   irreversible deletes require an explicit approval token and exit 5 without it.
2. **Rights and consent are blocking, not advisory.** `unknown` / `pending` / `restricted` rights
   and `missing` / `pending` voice consent appear as blocking flags in the plan, brief and
   checklist.
3. **No imitation of named artists.** The intake template and brief label references as
   stylistic traits to analyse, and every generation profile carries the check
   "no direct imitation of a named living artist".
4. **Disclosure is generated, not optional.** The brief always includes a disclosure line for
   AI-assisted elements.
5. **Non-destructive by design.** `trim-silence` refuses to overwrite its input; `plan` never
   edits sources; `unpack` refuses traversal; `pack` refuses nested folders.
6. **No shell, network or arbitrary code execution.** The tool runs no subprocesses, opens no
   sockets, evaluates no expressions and loads no plugins. User strings are data, never code.
7. **Bounded work.** Every input has a byte cap, every audio scan has a time budget, every
   operation has a timeout, batches have a job cap and archives have an entry/expansion cap.
8. **Human review is mandatory.** The generated checklist always ends with a human-review
   sign-off row, and the handoff template records the reviewer's name, role and date.

---

## 8. Validation and tests actually completed

Run in this workspace with Python 3.13.16 and re-runnable with
`python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --pdf-text uploads/catalog.txt`.
The suite drives the real CLI through subprocesses, uses temporary directories only, and leaves
no files behind unless `--keep` is passed.

**Measured result of the final run: 64 checks without the PDF comparison (65 with it),
all passed, 0 failed, exit code 0.**

| Area | Checks | What was verified |
| --- | --- | --- |
| Help / capability list | 3 | `--help`; `capabilities` text and JSON (9 commands declared) |
| Catalog parsing | 5 | 40/400 parse; category names; markdown = HTML = inline text; PDF-derived text = shipped asset (400 names identical); `--category` filter changes the result |
| Errors and limits | 8 | unparseable source (3), missing file (3), no source (3), unknown category with `available` list, empty goal, missing inputs, tempo 900 rejected, malformed inline JSON, byte limit (4), invalid workflow config |
| Planning and deliverables | 10 | 5 files written, 10 capabilities covered, brief renders intake (104 BPM, D major, decision log), approval status rendered, checklist CSV columns and rows, delivery ZIP holds 4 files under one slug, byte-identical re-run, single-capability case, two-capability case with mixed families, ad-hoc case |
| Rights / approvals | 5 | two blocking flags for unknown rights + missing consent, publish without token → exit 5 with expected token, token accepted by `verify-intake`, payment gated, `--dry-run` writes nothing |
| Batch | 6 | 5 jobs: 2 ok / 3 isolated failures, missing-goal job isolated, malformed-intake job isolated, unapproved publish job gated (`approval_required`), exit 3, report file written and parsed |
| WAV QC | 6 | clean 2 s/48 kHz/stereo file passes with measured duration 2.0 s, silent file fails (3), strict-master profile rejects a 0.5 s hot file, unknown profile name is a config error, non-WAV → exit 6, missing file → 3, truncated file reported as an integrity failure |
| Trim silence | 2 | padded tone shortened to a still-valid WAV; input overwrite refused |
| Pack / unpack | 5 | 2-entry ZIP plus manifest, extraction restores both files, nested folder refused, five archive-guard cases (traversal, `SKILL.md`, unprefixed name, subfolder, nested ZIP), traversal archive rejected and nothing escapes |
| Packaging / portability | 7 | runs beside a parent `SKILL.md` with relative paths only; category-27 case renders from relative paths; outputs land inside the clean root; no subfolders; no unprefixed files; exactly one knowledge file; no `SKILL.md` inside the Skill Tool folder; primary entry point present |

Additional manual checks performed during the work:

* `tools-music_ai_studio_cli.py` imports the engine by hyphenated filename via `importlib`;
  both files stay side by side with no `__init__.py` and no package structure.
* A deterministic re-run with a fixed `--timestamp` and `--case-id` produced a byte-identical
  delivery ZIP.
* Malformed and hostile archives (`../../escape.txt`) were written deliberately and rejected.
* The final delivery ZIP was opened and inspected (entry list, integrity, no nested archives,
  no wrapper folder), extracted into a clean directory, and the primary tool was re-run from
  the extracted copy — see §13.

---

## 9. Limits and unavailable integrations

* **PDF input is not parsed.** No PDF library is in the standard library; the CLI returns
  `pdf_requires_conversion` with a one-line `pypdf` recipe, and the shipped catalog asset exists so
  the tool is usable without any conversion.
* **No loudness (LUFS), true peak, or inter-sample peak measurement.** Reports carry RMS and
  sample peak only and say so in every result. `asset-delivery-profiles.json` lists target values
  as reference data, not as measurements.
* **No audio DSP beyond WAV I/O and silence trimming.** No EQ, compression, time-stretch,
  pitch-shift, stem separation, restoration, transcription, or notation rendering.
* **WAV only.** Only uncompressed RIFF/WAVE (PCM 8/16/24/32-bit, float 32/64-bit). MP3, FLAC, AIFF,
  OGG, and RF64/W64 return `unsupported_audio_format` or are not inspected.
* **`trim-silence` requires 16-bit PCM** and rejects anything else rather than guessing.
* **No model inference, no music generation, no text-to-audio.** Model names, seeds and prompts
  are *recorded* for provenance; nothing is called.
* **No network access** — no hosting, catalog, API or credential integration. If a future parent
  skill needs one, it is that skill's responsibility; this tool cannot and does not prevent
  external platform failures (hosting, permissions, catalog availability).
* **Quality verdicts are checklists, not judgements.** Musical coherence, artifact review,
  intelligibility, cultural context and rights decisions require a human. The tool records the
  decision; it cannot make it.
* **Per-operation timeouts use POSIX `SIGALRM`.** On platforms without it the guard is skipped and
  only the scan budgets and size limits bound the work (the code documents this).
* **Batch parallelism uses threads** (I/O-bound work). Heavy CPU work is not parallelised by
  processes.
* **Language:** generated text is English; slugs are ASCII-only, so non-Latin titles collapse to
  `untitled` and should be given an explicit `--title` or `--case-id`.

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `error [no_capabilities_found]` | source has no recognizable capability lines | use `1. Skill Name: X` or `- X` lines; check with `list --format markdown` |
| `error [pdf_requires_conversion]` | a `.pdf` was passed to `--source` | run the printed `pypdf` one-liner first, or use `asset-catalog-music-ai-400.md` |
| `error [missing_input]: a catalog source is required` | `--category` used without `--source`/`--text` | add the source, or drop `--category` for an ad-hoc case |
| `error [category_not_found]` | number/slug/name mismatch | the error's `details.available` lists real names; run `list` |
| `error [approval_required]` | intake `actions` without tokens | re-run with `--approve APPROVE-RELEASE` (or the token named in the error) |
| exit 4 `limit_exceeded` | a limit was hit (often `max_input_bytes` in a test) | raise the limit in `--workflow`, or split the input |
| exit 4 with `timeout` | operation exceeded `timeout_seconds` | raise it, or reduce the case size (`--max-capabilities`) |
| `qc-audio` can't measure a file | codec/format not supported | convert to 16/24-bit PCM WAV with an external tool, then re-run |
| `peak`/`clip` issues reported | genuinely hot or clipped master | fix in the DAW; the tool never modifies audio except `trim-silence` |
| `bad_structure` from `pack` | nested folder in the delivery | flatten it; `plan` outputs are already flat |
| `unsafe_overwrite` from `trim-silence` | `--out-file` equals `--audio` | choose a new output name |
| Batch job `file_not_found` | relative `source` not resolvable | pass `--source-root`, or make job `source` absolute |
| Interrupted run left `.tmp-*` files | SIGINT during a write | the CLI removes them on Ctrl-C; otherwise delete `*.tmp-*` and re-run |

Recovery principles: every write is atomic (`*.tmp-<pid>` then `os.replace`), so an interrupted
run never leaves a half-written deliverable; a failing batch job never blocks the others; and a
previous approved case is always re-derivable from intake + catalog + `--timestamp`.

---

## 11. Extension and maintenance

**Add a capability family.** Append a `(regex, {family, deliverables, checks})` tuple to
`_OP_RULES` in `tools-music_ai_studio.py`; first match wins. Add category-level behaviour to
`_CATEGORY_PROFILES` (it can raise `risk` and add deliverables/checks).

**Add a gated action.** Add the action to `APPROVAL_TOKENS`, to `asset-intake-schema.json`
(`actions.items.enum`) and to `asset-workflow-default.json` (`approvals.required_for`), then extend
the self test's gating checks.

**Change output shape.** `build_plan()` holds the JSON contract, `render_markdown()` the brief,
`render_checklist_csv()` / `render_file_manifest()` the CSVs. Keep `schema_version` bumped when the
contract changes.

**Add a QC threshold profile.** Add an entry to `asset-qc-profiles.json`; only the four
implemented keys are honoured (`clip_ceiling_dbfs`, `minimum_duration_seconds`,
`maximum_duration_seconds`, `require_non_silent`). Unknown keys are ignored by design so a richer
future profile file still loads.

**Keep it dependency-free.** If a future capability genuinely needs a third-party package
(e.g. `pypdf` for direct PDF intake, `numpy` for LUFS), add it deliberately, add a
`tools-requirements.txt` file *only then*, document supported versions here, and keep the current
behaviour as the fallback so the package still runs on a bare interpreter.

**Maintenance checklist before any release of a change:** run the self test with `--pdf-text`;
re-run the clean-root test; verify the file count and that no `SKILL.md` exists inside
`music-ai-skill-studio/`; confirm `knowledge-readme.md` still matches the real commands; rebuild
the delivery ZIP.

---

## 12. Reference: category → default deliverables (first matching rule)

| Family | Trigger examples | Core deliverables | Core checks |
| --- | --- | --- | --- |
| `analysis` | analyst, measurement, examiner, profiler | measurement sheet, observation log, corrective actions | method cited, tolerances stated, subjectivity flagged |
| `planning` | curator, planner, architect, designer, auditor | assumptions, step plan, risk register, acceptance criteria | assumptions listed, rights recorded, criteria testable |
| `generation` | writer, prompt, generator, hook, adapter | creative brief, three controlled variations, selection rationale, settings record | no artist imitation, lyrics reviewed, settings recorded |
| `audio_processing` | mix, master, stem, restore, bus | source inventory, processing chain, before/after table, clean reference | no clipped samples, phase ≥ 0, reference kept |
| `verification` | review, QA, test, recovery, validator | test matrix, defects, recovery procedure, rerun evidence | empty and malformed input tested, recovery exercised |
| `rights_and_release` | rights, licence, publish, consent, royalties | rights matrix, consent records, disclosure text, approval gate | every asset has a row, consent on file, approval recorded |
| `education` | teach, curriculum, lesson, practice | objectives, sequence, rubric, accessibility | objectives observable, 3+ rubric levels |
| `performance` | live, concert, stage, coding | technical rider, cue sheet, fallback plan, show-day checklist | fallback present, timings confirmed, level limits set |
| `documentation` | catalog, archive, metadata, notation | field schema, vocabulary, sample record, validation rules | required fields populated, naming stated |
| `marketing` | marketing, audience, release, distribution | segments, channel plan, asset list, measurement plan | claims evidenced, metadata complete, promo rights cleared |
| `sound_design` | voice, vocal, timbre, synthesis, sample | timbre spec, synthesis description, variation matrix, consent note | no unconsented cloning, articulation mapped |
| `general` | anything else | scope and inputs, draft deliverable, validation result | inputs validated, human review recorded |

Category profiles then add: provenance ledger and territory/term checks (rights, governance);
voice-consent checklist and deception review (voice, vocal); delivery profile and loudness/true-peak
checks (mastering, release, distribution, marketing); session file list and missing-media checks
(recording, DAW, mixing, editing, stems); accessibility/safety note and hearing limits
(accessibility, wellness, education); citation list and attribution (research, catalog, archives,
collaboration, project management).

---

## 13. Package verification performed

* Delivery archive: **`music-ai-catalog-to-production-cases.zip`**, containing the folder
  `music-ai-skill-studio/` **directly at the ZIP root** — no wrapper folder, no nested archives,
  no caches.
* 13 files, maximum path depth **2** (`music-ai-skill-studio/<file>`), well inside the 500-file
  and three-level limits. File count of the Skill Tool folder: **13**.
* `music-ai-skill-studio/` contains **no** `SKILL.md`, no subfolders, and only `tools-`,
  `knowledge-` and `asset-` prefixed files; the future combined package therefore keeps exactly
  one `SKILL.md` at its root.
* The folder was copied beside a temporary parent `SKILL.md`, and the primary tool was run with
  relative paths only (`python3 music-ai-skill-studio/tools-music_ai_studio_cli.py list --source
  music-ai-skill-studio/asset-catalog-music-ai-400.md` etc.) — all checks passed.
* The final ZIP was opened, listed, integrity-tested, extracted into a clean directory, and the
  primary tool was re-run from the extracted copy (§8, §13 table in the final run output).
