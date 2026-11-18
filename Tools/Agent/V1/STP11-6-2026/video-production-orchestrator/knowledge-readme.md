# video-production-orchestrator — Skill Tool knowledge

A reusable Skill Tool component (no `SKILL.md`) that implements the **Ultimate
Video Maker Skill Catalog**: 25 categories, 250 specialised capabilities covering
planning → writing → boarding → ingest → editing → motion → graphics → text →
compositing → VFX → generative → animation → colour → audio → image → restoration
→ genre formats → work video → localisation → versioning → mastering → QA/rights
→ pipeline → autonomous orchestration.

It runs on **Python 3.8+ standard library only**. FFmpeg/FFprobe are optional and
detected at runtime; without them the non-media capabilities still work and media
commands fail with a clear, actionable error (never a silent fake success).

---

## 1. Purpose and capability list

Two layers ship together and are used through one CLI:

1. **A 250-capability catalog runtime** — every catalog skill is a first-class,
   addressable capability (`C01-E01` … `C25-E10`) with category, family, delivery
   phase, approval flag, default artifact names and an executable command.
2. **A real media/planning engine** — plan generation and validation, requirement
   coverage, task briefs, delivery-matrix expansion, creative variants, probing,
   QA scanning, aspect reframing, transcoding, caption retiming/validation,
   checksummed delivery manifests, per-file stack policies and approval gating.

### Capability families (map to the 25 catalog categories)

| family | catalog categories | what it covers |
| --- | --- | --- |
| planning | 1 Creative direction, strategy, production planning | briefs, concepts, treatments, scope, feasibility, risk |
| writing | 2 Research, scripts, story, messaging | research, scripts, hooks, narrations, claims |
| board | 3 Storyboarding, shot design, previz | boards, shot lists, coverage, animatics |
| ingest | 4 Media ingest, organisation, preparation | ingest, checksums, proxies, logging, dedupe |
| edit | 5 Editing, assembly, narrative shaping | rough/fine cut, multicam, continuity, picture lock |
| motion | 6-7 Transitions, motion, speed, stabilisation | cuts, dissolves, whips, retiming, stabilisation |
| text | 8 Text, titles, captions, typography | titles, lower thirds, captions, typography QA |
| graphics | 9 Motion graphics, infographics | graphics, charts, maps, logo, templates |
| composite | 10 Compositing, layering | keys, roto, mattes, tracking, matchmove |
| vfx | 11 Visual effects | removal, weather, pyro, destruction |
| generative | 12 Generative video | text/image-to-video, prompting, provenance |
| animation | 13 Animation inside video | characters, products, lip sync, crowd |
| color | 14 Colour correction and finishing | balance, matching, looks, HDR/SDR, LUTs |
| audio | 15 Audio, music, SFX, foley, mixing | dialogue, repair, cues, stems, loudness |
| image | 16 Image/photo/PNG/graphic assets | prep, cutouts, restoration, layer separation |
| repair | 17 Restoration and technical repair | upscaling, interpolation, deinterlace, drift |
| genre | 18 Specialised formats and genres | social, YouTube, commercial, doc, trailer |
| workvideo | 19 Screen recording, tutorials, work video | screen capture, tutorials, demos, chaptering |
| localize | 20 Localisation, accessibility | subtitles, dubbing, audio description, safety |
| version | 21 Aspect ratios and platform versioning | reframing, safe areas, cutdowns, uploads |
| master | 22 Encoding, mastering, delivery | codecs, masters, streaming, stems, manifests |
| qa | 23 QA, review, rights, safety | frame/audio QA, rights, privacy, sign-off |
| pipeline | 24 Pipeline, automation, recovery | structure, render queues, archives |
| autonomous | 25 Advanced autonomous systems | planning, routing, repair agents, orchestration |

### CLI commands (the complete, reachable capability surface)

| command | capability |
| --- | --- |
| `capabilities` | list all 250 capabilities with category, family, phase, approval flag and the exact follow-up command; filter by `--category`, `--family`, `--phase` |
| `parse` | ingest a single capability, numbered list, one category or several categories from plain text, Markdown or copied HTML; strip tags/classes/attributes/numbering; extract real names + descriptions; derive an umbrella name |
| `search` | keyword search across name/description/keywords, optionally restricted to families |
| `plan` | resolve a free-text request (or a parsed list) into a schema-valid production plan with deliverables, per-capability tasks, quality gates and risks |
| `coverage` | requirement coverage of a plan, uncovered requirements, phase spread, approval list, blocking risks (`--fail-on-gap` → exit 4) |
| `brief` | execution-ready brief for one capability: inputs, procedure, outputs, checks, done-when, limits |
| `digest` | expand a master delivery configuration into a version matrix (platform × language) with duration-limit verification |
| `variant` | distinct creative variants (hook, pacing, visual direction, structural deltas), deterministic with `--seed` |
| `probe` | real media metadata: container, codec, resolution, fps, pix_fmt, colour tags, field order, alpha, audio layout, duration, aspect |
| `thumbs` | evenly spaced review stills with timecodes |
| `qa` | black segments, frozen picture, silence, integrated LUFS, true peak, interlace/no-audio warnings (`--fail-on-error` → exit 4) |
| `reframe` | render one aspect version: centre crop or blurred-pad letterbox, re-probed after render |
| `transcode` | deterministic H.264/AAC review copy with optional downscale |
| `captions` | parse/validate SRT (and VTT cues), retime (`--offset`, `--scale`, `--stretch-to`), enforce min duration and reading speed, rewrite a clean SRT |
| `manifest` | build a delivery manifest with sha256 checksums and verify/tamper-detect it |
| `stack` | run a per-file media stack policy (aspect, duration, size, audio, loudness, captions, interlace, alpha) across many files |
| `approve` | two-step approval gate bound to an action + exact target set |
| `selftest` | 27-test validation suite that exercises every command against real generated media |
| `doctor` | runtime, dependency, catalog and asset availability report |
| `help` | full usage and command list (19 working commands in total) |

---

## 2. Setup and dependencies

**Required:** Python 3.8+ (tested on 3.13.16). No third-party packages are needed.

**Optional, for media commands** (`probe`, `thumbs`, `qa`, `reframe`, `transcode`,
`stack`, and `digest --check-duration`):

- FFmpeg + FFprobe on `PATH`, **or**
- `pip install imageio-ffmpeg` (provides a bundled ffmpeg binary),
- **or** set `VIDEO_FFMPEG=/path/to/ffmpeg` and `VIDEO_FFPROBE=/path/to/ffprobe`.

Detection order: `VIDEO_FFMPEG`/`VIDEO_FFPROBE` → `PATH` → `imageio-ffmpeg` wheel.
If only ffmpeg is present, probing falls back to parsing `ffmpeg -i` (reported as
`"probe_source": "ffmpeg-stderr"`); loudness/black/silence scans still work.

**Shells.** Examples below use POSIX `sh`/bash. Windows equivalents:

```bat
py -3 tools-video-orchestrator.py capabilities
py -3 tools-video-orchestrator.py plan --request "45s vertical explainer with captions" --output plan.json
```

```powershell
python tools-video-orchestrator.py doctor
```

Paths passed to the tool are resolved against the **current working directory**
(absolute paths are used as given), so `--output out/plan.json` lands beside your
project, not inside the Skill Tool folder. No installation step, no environment
variables required, no network access at any point.

---

## 3. Files in this Skill Tool

| file | role |
| --- | --- |
| `tools-video-orchestrator.py` | **Primary entry point.** Argument parsing, all 20 commands, structured errors, exit codes. Run this. |
| `tools-video-core.py` | Shared runtime: path resolution, atomic writes, structured errors, JSON-Schema-subset validator, timecode maths (NDF + drop-frame), bounded subprocess execution, approval tokens, finding summaries, log. Imported first. |
| `tools-video-input.py` | Input ingestion: format detection (text/markdown/html), markup cleaning, list/number/label parsing, category detection, umbrella-name derivation. Imported by the planner and the CLI. |
| `tools-video-plan.py` | Planning intelligence: catalog load/search, request→capability resolution, plan building, coverage, task briefs, delivery digests, variants, manifest build/verify, status updates. Imported by the CLI and self-test. |
| `tools-video-media.py` | Media engine: FFmpeg discovery, probe (ffprobe + ffmpeg-stderr fallback), thumbnails, QA scan, reframe, transcode, caption parse/retime/write, delivery-matrix expansion. Imported by the CLI and self-test. |
| `tools-video-selftest.py` | Validation suite (27 tests) driving the CLI as a subprocess. Used by the `selftest` command. |
| `knowledge-readme.md` | This document (the only knowledge file). |
| `asset-catalog-data.json` | The 250 capabilities in machine-readable form: id, name, slug, category, family, phase, description, keywords, approval flag, per-category default artifacts, phase definitions. |
| `asset-production-plan.schema.json` | JSON Schema for plans. Enforced on generation **and** on load, so an invalid plan can never be executed. |
| `asset-master-config.schema.json` | JSON Schema for delivery/master configurations consumed by `digest`. |
| `asset-media-stack.schema.json` | JSON Schema for per-file stack policies consumed by `stack`. |
| `asset-approval-policy.json` | Human-readable policy listing actions that always require approval, and the token algorithm. |
| `asset-type-scale.json` | Type scale, safe areas/durations and contrast rules used by titles, lower thirds, captions and graphics work. |
| `asset-sample-master-config.json` | Ready-to-run master configuration (4 platforms × 3 languages). |
| `asset-sample-media-stack.json` | Ready-to-run vertical social delivery policy. |
| `asset-fixture-qa-sample.mp4` | 6-second 640×360/30 fps fixture that intentionally contains a 1 s black segment, a silent passage and low loudness, used by `selftest` and as a probe/QA example. |

**Load/execution order:** `tools-video-orchestrator.py` → `tools-video-core.py` →
`tools-video-input.py` → `tools-video-plan.py` / `tools-video-media.py` →
`tools-video-selftest.py` (only for `selftest`). Assets are read relative to the
tool files, so the whole folder can be copied anywhere.

---

## 4. Inputs, outputs, parameters, configuration

### Inputs accepted

- A single capability sentence: `"Remove the boom mic from the interview."`
- A numbered or bulleted list, with or without `Skill Name:` / `Description:` labels.
- One category, or several categories, in plain text, Markdown or pasted HTML.
- A caption file (SRT), a master configuration (JSON), a stack policy (JSON), a
  plan (JSON), or real media files.

Input can be inline (`--request`, `--capability`, `--input`), from a file
(`--request-file`, `--plan`, `--config`, `--policy`, `--verify`), or piped on
stdin (`cat brief.md | python3 tools-video-orchestrator.py parse --format md`).

**Messy input is normalised.** HTML tags, CSS classes, `data-*` attributes,
Markdown emphasis/headings/links, list markers, numbering marks and repeated
spacing never reach the extracted names. `<li>` becomes a list item,
`<h*>`/`<div>`/`<p>` become line breaks, inline tags (`span`, `em`, `strong`, …)
separate name from description; `td`/`th` cells are joined as
`Name – Description`.

### Outputs

- **stdout:** JSON by default; `--format md` or `--format text` for humans.
- **`--output PATH`:** writes the artifact (plan, matrix, QA report, caption file,
  manifest, rendered video). When the artifact is written, the report still goes
  to stdout for `plan`, `digest`, `captions`, `manifest`, `reframe`, `transcode`.
- Exit codes: `0` ok, `2` usage/validation, `3` not found, `4` gate/approval,
  `5` external tool failure, `6` timeout, `7` internal error. Errors print
  `{"error": {"code", "message", "hint", "details"}}` on stderr.

### Key parameters

| command | parameters |
| --- | --- |
| `plan` | `--request`/`--request-file`/`--plan-from`, `--capability` (repeatable, id/slug/name), `--max-capabilities` (default 60), `--goal`, `--audience`, `--platform`, `--duration` (`90`, `1m30s`, `00:01:30`), `--deliverable kind:path` (repeatable), `--plan-id` |
| `digest` | `--config`, `--platform` (repeatable override), `--check-duration`, `--timeout` |
| `qa` | `--black-threshold` 0.10, `--black-min-duration` 0.5, `--silence-db` −50, `--silence-min` 2.0, `--freeze-min` 2.0, `--fail-on-error`, `--timeout` 300 |
| `captions` | `--offset` (`-1.5s`, `+00:00:02`), `--scale` 1.0, `--stretch-to`, `--min-duration` 0.8, `--max-cps` 21, `--fail-on-error` |
| `stack` | `--policy`, `--input` (repeatable/comma-separated), `--fail-on-error`, `--timeout` |
| `reframe` | `--aspect` (16:9, 9:16, 1:1, 4:5, 4:3, 2:1, 21:9, 3:4, 5:4), `--mode crop\|pad`, `--no-blur-pad`, `--crf` 20 |
| `approve` | `--action`, `--target` (repeatable), `--approve-plan`, `--approve TOKEN`, `--record approvals.json` |
| `manifest` | `--entry "path,role=master,version=v1"` (repeatable), `--verify`, `--root`, `--no-checksums`, `--fail-on-error` |

### Configuration assets

- **Master config** (`asset-master-config.schema.json`): `master` (title, source,
  fps, resolution, color_space, loudness target), `platforms`, `languages`,
  `captions`, `duration_limits`, `compliance`. Platform presets available to
  `digest`: `youtube-1080p`, `youtube-4k`, `reels-vertical`, `tiktok-vertical`,
  `shorts-vertical`, `instagram-square`, `instagram-portrait`, `linkedin-1080p`,
  `broadcast-1080i`, `cinema-2k`.
- **Stack policy** (`asset-media-stack.schema.json`): `delivery` (platform,
  aspect, max_duration_seconds, max_bytes, require_audio, captions, loudness
  target, max true peak) and `checks` (subset of `probe`, `duration`, `aspect`,
  `loudness`, `captions`, `size`, `audio_present`, `interlace`, `alpha`).
- **Plan** (`asset-production-plan.schema.json`).
- **Catalog data:** point `--catalog` at a different `asset-catalog-data.json`
  to run the same tool against your own catalog (same shape).

---

## 5. Explanation and examples for every capability

All examples assume the current directory contains this Skill Tool folder.

### capabilities

```sh
python3 tools-video-orchestrator.py capabilities
python3 tools-video-orchestrator.py capabilities --category C15 --format md
python3 tools-video-orchestrator.py capabilities --family audio --phase 2 --count-only
```

`--count-only` returns the catalog summary (25 categories / 250 capabilities)
without the 250 rows; useful for cheap health checks.

### parse (messy input normalisation)

```sh
python3 tools-video-orchestrator.py parse --request $'Motion Retiming\n- Speed Ramping Specialist - Smooths acceleration around action beats.\n- Reverse and Loop Designer: Builds seamless reversals.'
python3 tools-video-orchestrator.py parse --request '<div class="x"><h3>Audio</h3><ul><li><span>Dialogue Editor</span> <em>Cleans and levels spoken audio.</em></li></ul></div>' --format md
```

The result reports `source_format`, `umbrella_name` (the category when one exists,
otherwise a short lowercase-hyphenated name derived from the shared purpose),
`categories`, `skills` and `warnings` (duplicate names dropped, empty categories,
capabilities collected before a heading, …).

### search

```sh
python3 tools-video-orchestrator.py search "loudness"
python3 tools-video-orchestrator.py search "remove object" --family vfx --limit 5
python3 tools-video-orchestrator.py search "thumbnail" --family version --format md
```

### plan

```sh
python3 tools-video-orchestrator.py plan \
  --request "Deliver a 45 second vertical explainer with captions, colour grade and a final export" \
  --audience "mobile viewers" --platform reels-vertical --duration 45 \
  --deliverable final-master:masters/final.mp4 \
  --deliverable review-preview:review/preview.mp4 \
  --output plan.json
```

Resolution is auditable: explicit ids → names found in the request → requirement
driven round-robin across families (so coverage wins over depth when the budget is
tight). Every selected capability carries a `notes` rationale. Plans are validated
against `asset-production-plan.schema.json` before they are written.

Plans are deliberately shown, not silently preferred: `plan` prints the plan id,
capability count, phases, gates, risks, coverage and the artifact path.
With `--format md` the plan artifact itself is written as a readable Markdown
document (`<plan>.md`: goal, deliverables table, capabilities by phase, gates,
risks, coverage) instead of JSON; `--format text` leaves the report plain.

### coverage / brief (execution loop)

```sh
python3 tools-video-orchestrator.py coverage --plan plan.json --format md
python3 tools-video-orchestrator.py brief --plan plan.json --capability C05-E01 --format md
python3 tools-video-orchestrator.py coverage --plan plan.json --fail-on-gap
```

A brief contains: source description, why it is in the plan, inputs (goal,
deliverables, audience, platform, duration), a family-specific procedure, expected
output artifact names, checks that must pass, done-when criteria, approval flag
and limits (timeout, retries, failure policy).

### digest (versioning / mastering)

```sh
python3 tools-video-orchestrator.py digest --config asset-sample-master-config.json --output matrix.json
python3 tools-video-orchestrator.py digest --config asset-sample-master-config.json --platform reels-vertical --check-duration
```

`--check-duration` probes `master.source` and marks each variant `duration_ok`.
If the source cannot be found it says so explicitly instead of pretending the
limits were checked.

### variant (creative exploration)

```sh
python3 tools-video-orchestrator.py variant --count 3 --base "product launch"
python3 tools-video-orchestrator.py variant --count 3 --base "product launch" --seed client-b
```

Six creative lenses (retention-hook, proof-led, story-led, contrarian,
demonstration-first, quiet-authority); no duplicates, deterministic without a
seed, seed-rotated with one.

### probe / thumbs / qa (review assets and gates)

```sh
python3 tools-video-orchestrator.py probe --input asset-fixture-qa-sample.mp4 --format md
python3 tools-video-orchestrator.py thumbs --input asset-fixture-qa-sample.mp4 --output-dir thumbs --count 9
python3 tools-video-orchestrator.py qa --input asset-fixture-qa-sample.mp4 --silence-min 0.5 --output qa.json
python3 tools-video-orchestrator.py qa --input asset-fixture-qa-sample.mp4 --silence-min 0.5 --fail-on-error
```

`qa` reports black segments, freeze segments, silence segments, integrated LUFS
(EBU R128 via `ebur128`), true peak, and warns on interlace or missing audio. Fails
(exit 4) only when `--fail-on-error` is passed, so it can be used as a soft or
hard gate. Against the shipped fixture it correctly reports
`black_frames` (2.0–3.03 s) and `silence_detected` (≈4.0–5.0 s) at
−22 LUFS / −16.3 dBFS peak.

### reframe / transcode (platform versioning)

```sh
python3 tools-video-orchestrator.py reframe --input master.mp4 --aspect 9:16 --output v9x16.mp4
python3 tools-video-orchestrator.py reframe --input master.mp4 --aspect 1:1 --mode pad --output v1x1.mp4
python3 tools-video-orchestrator.py transcode --input master.mp4 --max-width 1280 --crf 26 --output review.mp4
```

Crop centres on the subject area without distortion; `--mode pad` letterboxes and
`--no-blur-pad` switches the blurred fill to solid black. Every render is
re-probed and its output size/duration reported.

### captions

```sh
python3 tools-video-orchestrator.py captions --input captions.srt --offset 1s --output captions-fixed.srt
python3 tools-video-orchestrator.py captions --input captions.srt --stretch-to 00:00:58 --min-duration 1.0
python3 tools-video-orchestrator.py captions --input captions.srt --max-cps 17 --fail-on-error
```

Detects unparseable timing (exit 2), overlapping cues (fail), reading speed above
the limit, over-long lines and applied minimum durations.

### manifest (delivery packaging / QA gate)

```sh
python3 tools-video-orchestrator.py manifest --root delivery \
  --entry "master.mp4,role=master,version=v1" \
  --entry "captions-en.srt,role=captions,version=v1" \
  --output delivery-manifest.json
python3 tools-video-orchestrator.py manifest --verify delivery-manifest.json --root delivery --fail-on-error
```

Builds sha256 hashes; verification detects missing files, size changes and
checksum mismatches and exits 4 with `--fail-on-error`.

### stack (per-file delivery policy)

```sh
python3 tools-video-orchestrator.py stack --policy asset-sample-media-stack.json --input shorts/*.mp4 --format md
python3 tools-video-orchestrator.py stack --policy asset-sample-media-stack.json --input final.mp4 --fail-on-error
```

### approve (irreversible / sensitive actions)

```sh
python3 tools-video-orchestrator.py approve --action publish-master --target masters/final.mp4   # prints the token
python3 tools-video-orchestrator.py approve --action publish-master --target masters/final.mp4 --approve <token>
```

Tokens are `sha256("video-production-orchestrator/approval/v1|<action>|<sorted targets>")[:16]`,
so a token for one target set can never unlock another. `--record approvals.json`
appends an audit trail.

### selftest / doctor

```sh
python3 tools-video-orchestrator.py selftest
python3 tools-video-orchestrator.py selftest --workdir ./selftest-scratch --keep --format md
python3 tools-video-orchestrator.py doctor
```

---

## 6. Safety and approval rules

- **No network access, no credentials, no paid services, no shell execution from
  untrusted input.** The only subprocess calls are FFmpeg/FFprobe with argument
  lists (never a shell string), a timeout on every call, and validated inputs.
- **Everything is bounded.** Media commands default to a 120–600 s timeout and
  raise a structured `timeout` error (exit 6) instead of hanging. No retries are
  performed silently; the self-test documents one retry as the manual policy.
- **Originals are never modified.** `probe`, `qa`, `thumbs`, `reframe`,
  `transcode` only read the input; renders go to the path you name. `manifest`
  only reads. Caption rewriting writes to `--output` (never in place unless you
  pass the same path deliberately).
- **Approval is required before** anything destructive, irreversible, publishing,
  financial, credential or privacy-sensitive: deleting/overwriting sources,
  uploading or publishing a master, rights/licensing decisions, likeness changes,
  clearing storage after checksum verification, exporting a package that leaves
  the workspace. `asset-approval-policy.json` states the policy; the `approve`
  command enforces it; capabilities flagged `requires_approval: true` in
  `asset-catalog-data.json` (28 of 250) cannot be marked `done` in a plan without
  a recorded approval.
- **Human review stays in the loop.** Plans always include blocking `approval`,
  `rights` and `qa` gates; `coverage --fail-on-gap` and the `--fail-on-error`
  flags make gates enforceable in automation.
- **Do not use the tool to fabricate evidence.** Restoration, generative and
  disclosure-sensitive work must be labelled; the approval policy and the QA gates
  exist to keep that visible.

---

## 7. Validation and tests actually completed

Run on **Python 3.13.16 / Linux, FFmpeg n9.0.2** (via `imageio-ffmpeg`), no
`ffprobe` present — i.e. the fallback probe path was exercised too.

`python3 tools-video-orchestrator.py selftest` → **27 passed, 0 failed, 0 skipped**
(~7 s):

| # | test | verified |
| --- | --- | --- |
| 1 | timecode maths | NDF 25 fps, 29.97 drop-frame (`frame 1800 = 00:01:00;02`, `01:00:00;00 = 107892`), invalid timecodes rejected, duration parsing |
| 2 | help | all commands listed, exit 0 |
| 3 | capabilities | 25 categories / 250 capabilities, every row has a command |
| 4 | capabilities filters | `--category C15` narrows the list; `--phase` filter changes the count |
| 5 | parse plain text | 2 capabilities, exact names, umbrella `motion-retiming` |
| 6 | parse copied HTML | tags/classes/attributes stripped, name and description separated |
| 7 | parse numbered markdown | `**1.**`/`Skill Name:` labels removed from names |
| 8 | search | colour-family hits first, family filter works |
| 9 | plan | 60 capabilities, schema-valid, phases reported |
| 10 | plan rejects invalid input | bad duration → 2, unknown capability → 3, bad deliverable kind → 2 |
| 11 | plan rejects missing input | empty request → 2 with hint |
| 12 | plan schema gate | malformed plan rejected before use |
| 13 | coverage + brief | coverage status, brief fields, unknown capability → 3 |
| 14 | digest | 2 platforms × 2 languages = 4 variants; unknown platform → 2 |
| 15 | variant | 3 distinct variants, deterministic, seed changes selection |
| 16 | approval gate | wrong token → 4, wrong target set → 4, correct token → granted |
| 17 | probe | 640×360 @ 30 fps, 6.0 s, video+audio |
| 18 | probe missing file | → 3 |
| 19 | qa scan | detects black segment + silence, `--fail-on-error` → 4 |
| 20 | captions | offset applied exactly (`00:00:01,000 --> 00:00:03,400`), malformed cue → 2 |
| 21 | manifest build + verify | sha256 recorded, tampering detected (exit 4), restored, re-verified |
| 22 | thumbnails | 4 real JPEGs (magic bytes checked) with timecodes |
| 23 | reframe 9:16 | crop box reported, output aspect 9:16, re-probed duration preserved, bad aspect → 2 |
| 24 | transcode | downscaled to 320 px, smaller than source |
| 25 | media stack policy | loudness warning surfaced; strict policy → 4 |
| 26 | recovery path | gate fails → cause fixed → same command passes |
| 27 | doctor | all 9 assets present, 250 capabilities, ≥19 commands, ffmpeg detected |

Additional manual checks performed during development: `plan --output` +
`digest --output` + `captions --output` + `manifest` all return their report on
stdout while writing the artifact; `digest --check-duration` against a missing
source reports `duration_check_note` rather than silently passing; `stack` with
`asset-sample-media-stack.json` correctly flagged a 16:9 fixture as
`aspect_mismatch` and a −22 LUFS track as off target; every `asset-*.json` file
was parsed and both sample configs validated against their schemas.

**Not performed / not claimed:** no automated test suite was run against real
broadcast masters, MXF/ProRes/DNxHR material, HDR grading data, or a real
NLE/AE project file, because none is available in this environment. No test was run
on Windows or macOS. No claim is made about GPU acceleration, `ffprobe`-specific
fields beyond what the fallback covers, or any hosted service.

---

## 8. Limits and unavailable integrations

- **No NLE integration.** It cannot open, render or write Premiere/Resolve/FCP/AE
  project files. It produces plans, briefs, EDL-style task data, manifests and
  review media that belong beside such projects.
- **No generative model, TTS, music library or stock service.** The generative,
  voice and music capabilities are planned, briefed and gated; actual synthesis is
  an external provider you supply. The tool records what must be recorded
  (prompts, provenance, disclosures).
- **Creative judgement is not automated.** Colour looks, edit rhythm, VFX quality
  and script quality are scoped into briefs with checks; humans execute.
- **Media operations are limited to what FFmpeg can do deterministically**
  (probe, stills, black/freeze/silence/loudness analysis, crop/pad reframe,
  H.264/AAC transcode). No neural upscaling, rotoscoping or stabilisation.
- **`stack` loudness checks rescan the file** (slower on long files); use
  `--timeout` to raise the budget.
- **Drop-frame maths supports 29.97/59.94.** 23.976 uses non-drop, which is the
  common convention; other rates use non-drop.
- **Single-process.** There is no distributed render queue; the pipeline
  capabilities produce the configuration and records a queue runner would consume.

---

## 9. Troubleshooting, recovery, extension, maintenance

| symptom | cause | fix |
| --- | --- | --- |
| `missing_dependency: FFmpeg is not available` | no ffmpeg found | install FFmpeg on PATH, `pip install imageio-ffmpeg`, or set `VIDEO_FFMPEG` |
| `external_failure: FFmpeg could not read …` | corrupt/unsupported media | run `probe` first; the hint carries FFmpeg's last error line |
| `timeout` (exit 6) | long file or slow disk | raise `--timeout`, or work on a shorter proxy |
| `validation_error` with `details.errors` | plan/config/policy failed schema | the message lists exact JSON paths and problems |
| `gate_failed` (exit 4) | approval or QA/coverage/manifest gate | read `details`; fix the cause, or record approval with `approve` |
| parse produced too few capabilities | input had no list markers or labels | add `-` bullets or `Name — Description` pairs; check `warnings` in the output |
| `no catalog capability matched` | request has no recognised production words | use concrete words (edit, captions, grade, export, QA) or pass `--capability C05-E01` |

**Recovery pattern used by the tool's own gates:** find the earliest failing layer,
keep what passed, repair only that layer, re-run the linked checks. The
`recovery path` self-test demonstrates exactly this loop.

**Extension points**

- New catalog entries: edit `asset-catalog-data.json` (`skills[]` + the ids in
  `categories[]`), keeping ids `C##-E##` and adding `family`, `phase`,
  `requires_approval`, `keywords`. No code change needed.
- New families: add a `FAMILY_*` entry to `FAMILY_PLAYBOOK` and `_family_checks`
  in `tools-video-plan.py` so briefs stay specific.
- New platform presets: add to `PLATFORM_PRESETS` in `tools-video-media.py`.
- New checks: add to `cmd_stack` (CLI) and `checks` enum in
  `asset-media-stack.schema.json`; keep both in sync.
- New schema fields: update the schema file **and** the code that reads it; the
  validator enforces `additionalProperties: false` on purpose.

**Maintenance checklist**

1. `python3 tools-video-orchestrator.py doctor` — assets, catalog, runtime.
2. `python3 tools-video-orchestrator.py selftest` — must stay at 0 failures.
3. Keep `asset-catalog-data.json` counts equal to the category index
   (currently 25 / 250; `capabilities` asserts this).
4. Never place `SKILL.md` or subfolders inside this folder; the combined package
   keeps exactly one `SKILL.md` at its root, beside this folder.
