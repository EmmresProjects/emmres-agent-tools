# ai-video-creation — Skill Tool component

**Reusable, vendor-neutral Skill Tool folder for a future Copilot skill.** This folder is a
component, not a complete skill: it contains no `SKILL.md` and must be copied **directly beside**
the parent package's root `SKILL.md`. The ZIP that delivers it is only a container.

Source blueprint: *AI Video Creation Skill Catalog* (47 categories, 564 skills). Per the
instruction to combine every related listed capability into one unified Skill Tool, all 47
categories are folded into the umbrella `ai-video-creation`. Every one of the 564 named
capabilities is present in the bundled registry, reachable by id/slug/name, and has a live
contract and a real local operation. The catalog itself says it is a capability blueprint that
does not implement skills or bind to a vendor; this tool therefore implements the **production
contract layer** — parsing, routing, planning, editable artifacts, validation, quality gates,
bounded execution, approvals, and delivery manifests — and keeps generative providers, models,
and encoders configurable. It does **not** claim to render neural video (see "Limits").

---

## 1. Purpose and complete capability list

| # | Command | Capability |
|---|---|---|
| 1 | `help` | Built-in help, command list, quickstart. |
| 2 | `capabilities` | Capability-list command: 17 commands, 47 categories, 564 capabilities, 9 operations, filters, text/JSON output. |
| 3 | `ingest` | Accept a single capability, a numbered Skill Tool list, one category, or several categories in plain text, Markdown, or copied HTML. Strips tags, CSS classes, data attributes, numbering marks, and extra spacing; extracts real category names, Skill Tool Names, and Descriptions; writes one **unified** registry with a lowercase-hyphenated umbrella name. |
| 4 | `describe` | Full recommended skill contract for any of the 564 capabilities: trigger, inputs, outputs, tools, gates, failure behavior, approval, observability, limits. |
| 5 | `route` | Transparent keyword/family scoring of a brief against all catalog capabilities. |
| 6 | `plan` | One dependency-ordered production plan: 9 stages, routed capabilities, gates, approvals, timeouts, retries, fallbacks, cost hint, warnings, unresolved issues. |
| 7 | `prompt` | Deterministic editable positive/negative prompt variants, constraints, seeds, ambiguity audit, JSON + Markdown artifacts. |
| 8 | `shotlist` | Bounded shot list from a script/brief: scene grouping, timecodes, camera/motion from style presets, Markdown EDL. |
| 9 | `captions` | SRT / WebVTT / JSON cue parsing, timing/overlap/reading-speed/line-length validation, normalized SRT + VTT output. |
| 10 | `validate` | Brief/job validation against `asset-brief-schema.json` plus semantic safety, path, locale, provider, and secret checks; structured errors with paths. |
| 11 | `gates` | Seven catalog quality gates with real evidence, pass/fail/warning/deferred status and remediation. |
| 12 | `run` | Bounded execution of a full plan **or any single one of the 564 capabilities** via `--capability`: dry-run by default, local provider, approval gating, per-stage/job timeouts, bounded retries, cancellation, `.partial` cleanup, checkpoint, `--resume` recovery, and a run report. |
| 13 | `approve` | Record, list, and revoke scoped or job-wide human approvals with tokens and expiry. |
| 14 | `deliver` | Checksummed delivery manifest from real local artifacts plus encoding/accessibility/provenance metadata. |
| 15 | `report` | Summarize a run directory from `checkpoint.json`, including earliest failure and next steps. |
| 16 | `selftest` | Built-in validation suite over every major capability, all 564 contracts, and all recovery paths. |
| 17 | `doctor` | Runtime, config, schema, registry, resource-reference, and folder-layout checks. |

Catalog coverage by category (12 capabilities each, 564 total): 1 Foundations: Diffusion,
Transformers, and Latent Video; 2 Prompt Engineering and Creative Briefing; 3 Text-to-Video
Generation; 4 Image-to-Video and Keyframe Animation; 5 Video-to-Video Transformation;
6 Generative Video Editing and Inpainting; 7 Temporal Consistency and Identity Preservation;
8 Camera, Lens, and Cinematography Control; 9 Motion, Physics, and Choreography; 10 Story
Development and Screenwriting; 11 Preproduction, Shot Lists, and Storyboards; 12 Multi-Shot
Planning and Scene Assembly; 13 Avatar and Digital Presenter Systems; 14 Lip Sync, Facial
Animation, and Performance; 15 Voice, Speech, and Dubbing; 16 Music, Sound Design, and Audio
Post; 17 Captioning, Subtitles, and Accessibility; 18 Translation and Video Localization;
19 Editing, Timeline, and Postproduction; 20 Color, Look Development, and Finishing;
21 VFX, Compositing, and Rotoscoping; 22 3D, NeRF, Gaussian Splatting, and Virtual Production;
23 Animation and Motion Graphics; 24 Music Videos and Audio-Reactive Visuals; 25 Template,
Stock, and Article-to-Video Systems; 26 Marketing, Advertising, and Brand Video; 27 Social
Media and Short-Form Video; 28 YouTube, Streaming, and Creator Workflows; 29 Education,
Training, and Instructional Video; 30 Film, Television, and Episodic Production; 31 News,
Documentary, and Factual Video; 32 Commerce, Product, and Retail Video; 33 Architecture, Real
Estate, and Spatial Video; 34 Scientific, Medical, and Technical Visualization; 35 Sports,
Fitness, and Motion Analysis; 36 Gaming, Machinima, and Virtual Worlds; 37 Restoration,
Enhancement, and Remastering; 38 Compression, Encoding, and Delivery; 39 Quality Assurance and
Evaluation; 40 Safety, Rights, Consent, and Provenance; 41 Automation, APIs, and Production
Pipelines; 42 Model Training, Adaptation, and Research; 43 Compute, Performance, and MLOps;
44 Asset Management, Metadata, and Collaboration; 45 Interactive, Personalized, and Real-Time
Video; 46 Specialized Capture and Sensor Workflows; 47 Catalog Governance and Skill
Orchestration.

Capabilities map to nine real local operation families: `contract` (editable job envelope for
generative capabilities), `plan`, `compile-prompt`, `shotlist`, `captions`, `validate`, `gates`,
`approval`, `delivery`. Every capability belongs to exactly one family, so no listed capability
is dropped and overlapping capabilities share the same implementation instead of being
duplicated.

---

## 2. Files, roles, and loading order

All files are flat inside `ai-video-creation/`; there are no internal subfolders, no
`SKILL.md`, and no unprefixed files. During packaging the folder contained **19 files**:
8 `tools-*` files (the primary entry point, a POSIX launcher, 5 runtime modules, and the
built-in test module), exactly 1 `knowledge-*` file, and 10 `asset-*` files. `doctor`
prints the authoritative count at any time.

| File | Role |
|---|---|
| `tools-ai-video.py` | **Primary entry point.** CLI parser, command dispatch, structured envelopes, exit codes, artifact writing. Loads all siblings below with `importlib` (hyphenated filenames cannot be plain-imported). |
| `tools-launch.sh` | POSIX `/bin/sh` launcher; resolves the primary script relative to itself. Optional convenience, not required. |
| `tools-contracts.py` | Loaded 1st. Root-relative resource resolution, config merge/validation, JSON-Schema subset validator, semantic validation, secret redaction, approval-token verification, skill contract builder, structured envelope, atomic writes. |
| `tools-catalog.py` | Loaded 2nd. HTML/Markdown/plain-text stripping, catalog parsing into one unified registry, registry validation, lookup/search, capability classification. |
| `tools-planner.py` | Loaded 3rd. Routing, dependency-ordered plan construction, prompt compilation, shot list / EDL generation. |
| `tools-media.py` | Loaded 4th. Caption parsing/validation, quality-gate evaluation, delivery manifest, cost estimate. |
| `tools-runtime.py` | Loaded 5th. Bounded executor, approval store, retries, timeouts, cancellation, cleanup, checkpoint/resume, run report. Receives the other modules by dependency injection. |
| `tools-selftest.py` | Loaded 6th. Built-in validation suite (`selftest` command); direct module calls plus real CLI subprocesses. |
| `knowledge-readme.md` | This file: the single consolidated knowledge file. |
| `asset-capability-catalog.json` | Real dataset extracted from the source PDF: 47 categories with purposes and 564 capabilities with names, descriptions, ids, slugs, families, operations, safety classes, and quality gates. |
| `asset-default-config.json` | Default operational config: resource references, limits, caption limits, timeouts, retries, providers, 9-stage workflow, gate ids, output names, cost hints, testing flag. |
| `asset-brief-schema.json` | JSON Schema (draft-07 subset) for briefs/jobs: fields, enum values, patterns, bounds. |
| `asset-quality-gates.json` | Seven gate definitions, checks, severities, remediation, allowed fps, handoff artifacts. |
| `asset-style-presets.json` | Six editable style vocabularies (cinematic, documentary, social-short, music-video, education, product) with camera/motion/lighting/palette/negative terms. |
| `asset-delivery-presets.json` | Five delivery presets (web-1080p, shorts-vertical, broadcast-1080i, archive-master, social-square). |
| `asset-policy-approval.json` | Approval/safety policy: sensitive keywords, brief flags, actions, prohibited behavior, redaction patterns. |
| `asset-example-brief.json` | Runnable example brief used by docs and `selftest`. |
| `asset-example-captions.srt` | Runnable SRT fixture used by docs, `captions`, `gates`, and `selftest`. |
| `asset-fixture-catalog.html` | Copied-HTML fixture with classes, data attributes, `<style>`, `<script>`, and numbering, used to test ingestion cleanup. |

**Loading order:** `tools-ai-video.py` → `tools-contracts.py` → `tools-catalog.py` →
`tools-planner.py` → `tools-media.py` → `tools-runtime.py` → `tools-selftest.py`. All sibling
modules are independent of each other's import names; the primary entry point is the
composition root.

---

## 3. Setup, dependencies, and exact commands

**Runtime:** Python 3.9 or newer, standard library only. No `pip install`, no compiler, no
third-party packages, no credentials, no network. Verified on the Python shipped in this
workspace (see `doctor`). Optional: a POSIX `/bin/sh` for `tools-launch.sh`.

Run from the future skill root (the directory containing the parent `SKILL.md` and this
`ai-video-creation/` folder). The shell is specified explicitly:

```sh
bash -lc 'python3 ai-video-creation/tools-ai-video.py help'
bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities --categories-only --format text'
bash -lc 'python3 ai-video-creation/tools-ai-video.py doctor'
bash -lc 'python3 ai-video-creation/tools-ai-video.py selftest'
bash -lc 'sh ai-video-creation/tools-launch.sh help'
```

Global flags (place them before or after the subcommand): `--root DIR` (explicit future skill
root), `--config FILE` (JSON override deep-merged over the default), `--catalog FILE`
(ingested registry to use instead of the bundled catalog), `--format json|text`, `--output FILE`
(primary artifact), `--help` on any command.

Exit codes: `0` success, `2` validation/input error, `3` approval required, `4` runtime or
provider error, `5` not found, `6` unexpected, `130` interrupt.

---

## 4. Inputs, outputs, parameters, schemas, configuration

**Structured envelope.** Every command prints one JSON object (or human text with
`--format text`): `schema_version`, `tool`, `command`, `status`, `generated_at`, `data`,
`errors`, `warnings`, `recovery`, `limits`, `meta`. Errors carry `code`, `message`, `path`,
`recovery`, and `details`. Secret-like keys are redacted before output.

**User I/O.** `--input PATH` / `--text VALUE` are mutually exclusive and required for commands
that need content. User-supplied absolute paths are accepted for CLI I/O (needed for temporary
directories); **packaged references are different** (below). `--output FILE` writes the
command's primary artifact (JSON, or Markdown for `--markdown`, SRT for `--srt`, VTT for
`--vtt`). `run` uses `--output-dir DIR`; `deliver` uses `--artifacts-dir DIR`; `report` uses
`--run-dir DIR`.

**Skill-root-relative references.** Config and this document refer to resources as
`/asset-default-config.json` etc. A leading `/` means *relative to the future skill root*, never
a filesystem absolute path; drive letters, `..`, backslashes, and URLs are rejected. The
resolver checks, in order: explicit `--root` → parent of the Skill Tool folder (the future
skill root containing `SKILL.md`) → the Skill Tool folder itself (where the files are packaged)
→ current working directory. This makes the same references work both inside a parent skill
package and in a freshly extracted ZIP. All references use exact case-sensitive names and
forward slashes. The exact references used by `asset-default-config.json` are:

```
/asset-capability-catalog.json
/asset-default-config.json
/asset-brief-schema.json
/asset-quality-gates.json
/asset-style-presets.json
/asset-delivery-presets.json
/asset-policy-approval.json
/asset-example-brief.json
/asset-example-captions.srt
/asset-fixture-catalog.html
```

**Brief schema** (`/asset-brief-schema.json`): required `title`, `objective`; optional
`duration_seconds`, `fps` (23.976/24/25/29.97/30/50/59.94/60), `aspect_ratio`, `resolution`
(`WIDTHxHEIGHT`), `style`, `style_preset`, `locale`, `safety_class`, `capabilities`, `script`,
`captions_text`, `captions_source`, `captions_required`, `audio_description`,
`reference_assets`, `delivery_preset`, `provider`, `seed`, `prompt_variants`, `constraints`,
`negative_prompt`, `camera`, `motion`, `keywords`, `approval`, `publish`, `voice_clone`,
`likeness`, `medical`, `political`, `factual_reconstruction`, `action`, `notes`. Semantic checks
add: no embedded secrets, no unsafe resource paths, rights metadata on reference assets,
enabled provider, BCP-47-like locale, and approval warnings for publishing.

**Config** (`/asset-default-config.json`, override with `--config`): `resources` (all nine
asset references), `limits` (2 MiB input, 8 MiB output, 1000 capabilities, 64 stages, 240
shots, 5000 caption cues, 12 prompt variants, 20 route results, 3600 s duration),
`caption_limits` (20 chars/s, 0.7 s minimum cue, 2 lines, 42 chars/line), `timeouts`
(30 s/stage, 180 s/job), `retries` (3 attempts, 0.05 s bounded backoff),
`providers` (`default: local`, `enabled: ["local"]`, `allow_external: false`, `shell: false`,
`network: false`, model version pins), `workflow` (9 stages with dependencies, operations,
families, gates), `output` (checkpoint/report/cancel/approvals filenames), `cost`, `testing`.

**Plan schema** (`plan` output): `job_id`, `umbrella`, `brief`, `provider`, `stage_order`,
`stages[]` (`id`, `depends_on`, `operation`, `capabilities[]`, `quality_gates`,
`approval_required`, `approval_reasons`, `limits`, `fallback`, `status`), `routing`, `policy`,
`limits`, `warnings`, `unresolved_issues`, `validation`.

**Run outputs** inside `--output-dir`: `stage-plan.json`, `stage-prompt.json`,
`stage-prompt.md`, `stage-shotlist.json`, `stage-shotlist.md`, `stage-contract.json`,
`stage-captions.json`, `stage-captions.srt`, `stage-captions.vtt`, `stage-validation.json`,
`stage-gates.json`, `stage-approval.json`, `stage-delivery.json`, `checkpoint.json`,
`run-report.json`. Dry-run writes only `checkpoint.json` and `run-report.json`.

---

## 5. Examples for every capability

```sh
# 1 help / 2 capabilities
bash -lc 'python3 ai-video-creation/tools-ai-video.py help'
bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities --category captions --limit 5'
bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities --categories-only --format text'

# 3 ingest: plain text, Markdown, or copied HTML -> ONE unified registry
bash -lc 'python3 ai-video-creation/tools-ai-video.py ingest --input source.html --name coastal-video --output registry.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities --catalog registry.json --limit 5'

# 4 describe any of the 564 capabilities
bash -lc 'python3 ai-video-creation/tools-ai-video.py describe --capability c17-s01 --output contract.json'

# 5 route / 6 plan
bash -lc 'python3 ai-video-creation/tools-ai-video.py route --input ai-video-creation/asset-example-brief.json --output route.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py plan --input ai-video-creation/asset-example-brief.json --output plan.json'

# 7 prompt / 8 shotlist
bash -lc 'python3 ai-video-creation/tools-ai-video.py prompt --input ai-video-creation/asset-example-brief.json --variants 2 --output prompt.json --markdown prompt.md'
bash -lc 'python3 ai-video-creation/tools-ai-video.py shotlist --input ai-video-creation/asset-example-brief.json --output shotlist.json --markdown shotlist.md'

# 9 captions / 10 validate / 11 gates
bash -lc 'python3 ai-video-creation/tools-ai-video.py captions --input ai-video-creation/asset-example-captions.srt --output cap.json --srt captions.srt --vtt captions.vtt'
bash -lc 'python3 ai-video-creation/tools-ai-video.py validate --input ai-video-creation/asset-example-brief.json --output validation.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py gates --input ai-video-creation/asset-example-brief.json --artifact-prompt prompt.json --artifact-shotlist shotlist.json --artifact-captions cap.json --artifact-validation validation.json --output gates.json'

# 12 run: dry-run first, then bounded local execution; or run one capability directly
bash -lc 'python3 ai-video-creation/tools-ai-video.py run --input plan.json --output-dir artifacts --mode dry-run'
bash -lc 'python3 ai-video-creation/tools-ai-video.py run --input plan.json --output-dir artifacts --mode execute --provider local'
bash -lc 'python3 ai-video-creation/tools-ai-video.py run --capability c17-s01 --output-dir one-cap --mode execute --provider local'
bash -lc "python3 ai-video-creation/tools-ai-video.py run --capability c03-s01 --text '{\"title\":\"Variant\",\"objective\":\"A different clip for comparison.\",\"duration_seconds\":8,\"fps\":24,\"resolution\":\"1920x1080\"}' --output-dir one-cap-b --mode execute"

# 13 approve sensitive stages (example: publishing), then resume
bash -lc 'python3 ai-video-creation/tools-ai-video.py approve --confirm --capability all --reason "reviewed publishing scope" --store approvals.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py run --input plan.json --output-dir artifacts --mode execute --approval-token TOKEN --store approvals.json --resume'
bash -lc 'python3 ai-video-creation/tools-ai-video.py approve --list --store approvals.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py approve --revoke TOKEN --confirm --store approvals.json'

# 14 deliver / 15 report / 16 selftest / 17 doctor
bash -lc 'python3 ai-video-creation/tools-ai-video.py deliver --input ai-video-creation/asset-example-brief.json --artifacts-dir artifacts --output delivery.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py report --run-dir artifacts --output report.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py selftest --output selftest.json'
bash -lc 'python3 ai-video-creation/tools-ai-video.py doctor'
```

Inline JSON works everywhere via `--text '{"title":"...","objective":"..."}'`. Use `--strict`
with brief commands to fail on validation errors instead of warning.

---

## 6. Safety and approval rules

* **No secrets, endpoints, or credentials** are embedded. Secret-like keys in briefs are
  rejected and secret-like values are redacted from envelopes.
* **No arbitrary shell, subprocess, network, or code execution.** The only process this tool
  starts is its own CLI during `selftest` (bounded with a timeout and an argument list, never a
  shell). Provider `allow_external`, `network`, and `shell` default to `false`.
* **Approval is required** before sensitive work: capabilities matching identity, likeness,
  voice cloning, face/deepfake, rights, consent, releases, medical, political, biometric,
  minors, privacy, security, credentials, financial, training/fine-tuning, or sensitive
  keywords; brief flags `publish`, `voice_clone`, `likeness`, `medical`, `political`,
  `factual_reconstruction`, `restricted`; actions `publish`, `delete`, `overwrite`, `train`,
  `external_call`, `upload`; and safety classes `high-impact` / `restricted`.
* Recording or revoking an approval itself requires `--confirm`. Tokens are random, scoped to
  one capability (or `all` for job-wide), expire (default 24 h), and can be revoked. An
  out-of-scope token is rejected. A blocked run preserves completed stages, reports
  `needs_approval` (exit 3), and prints the exact `approve` + `--resume` recovery command.
* **Dry-run is the default.** `--mode execute` is needed to write stage artifacts. Publishing,
  deletion, training, and external calls are never performed by this tool.
* Paths inside briefs/config must be relative forward-slash references; absolute/drive-letter,
  parent traversal, and URL references are rejected.

---

## 7. Quality gates, limits, retries, cancellation, cleanup, recovery

Seven gates from the catalog (see `/asset-quality-gates.json`): temporal coherence, prompt or
source fidelity, technical validity, accessibility, rights and consent, reproducibility,
handoff readiness. Each returns `pass` / `fail` / `warning` / `deferred` with real evidence.
Checks needing rendered pixels (flicker, geometry, audio sync) are reported as **deferred**,
never falsely passed. `gates` exits `2` when any gate fails.

Limits: input 2 MiB, output 8 MiB, 64 stages, 240 shots, 5000 caption cues, 12 prompt
variants, 20 route results, 3600 s duration. Timeouts: 30 s per stage, 180 s per job, both
checked with `time.monotonic()`. Retries: 3 attempts per stage, 0.05 s bounded backoff, only
for `IO_ERROR`, `STAGE_ERROR`, `STAGE_TIMEOUT`-style transient failures; non-retryable errors
(approval, provider) stop immediately. Cancellation: create a file named `CANCEL` in the run
directory (or the working directory); the run stops with `cancelled`, writes a checkpoint, and
prints the resume route. Cleanup: `.partial` files are removed after every run; successful
stages and `checkpoint.json` are preserved. Recovery: fix the cause, then rerun with
`--resume` (same `job_id` required; `RESUME_JOB_MISMATCH` otherwise). `--fail-stage` with
`--test-mode` requires `testing.allow_fault_injection: true` in a `--config` override; it is a
documented recovery drill, not a hidden behavior.

---

## 8. Validation and tests actually completed

Reproduce with:

```sh
bash -lc 'python3 ai-video-creation/tools-ai-video.py selftest'
bash -lc 'python3 ai-video-creation/tools-ai-video.py doctor'
bash -lc 'python3 ai-video-creation/tools-ai-video.py help --format text'
bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities --categories-only --format text'
```

During packaging, on Python 3.13.16, the built-in suite ran **28 named checks, 28 passed,
0 failed** (`selftest` exit code 0), and `doctor` reported **16/16 environment checks passed**.
Checks actually executed through the real CLI and direct module calls:

1. `help` (17 commands) and `capabilities` (564 capabilities, 47 categories, 9 local
   operations) in both JSON and text modes.
2. Category, query, and family filters; `describe` by id plus a structured not-found error.
3. Contract construction for **all 564** capabilities (7 gates, limits, approval mechanism)
   and reachability of **all 47 categories** (12 capabilities each).
4. Ingestion of plain text with page chrome, a wrapped category heading, wrapped
   descriptions, and an appendix section (2 categories, 3 capabilities, purpose preserved,
   appendix excluded, registry saved and reused via `--catalog`), Markdown
   with headings/bold names/em dashes, copied HTML with classes, data attributes, `<style>`,
   `<script>`, and numbering (4 capabilities), and a single unlabeled capability.
5. Validation: valid brief passes; invalid brief returns 5 structured error codes including
   `EMBEDDED_SECRET`, `UNSAFE_PATH`, `TYPE_MISMATCH`, `ENUM_MISMATCH`; missing file returns
   `INPUT_NOT_FOUND`; a non-object brief returns `TYPE_MISMATCH`.
6. Changed input changes output: two different briefs produce different prompts and seeds;
   ambiguity audit flags vague terms; JSON + Markdown prompt artifacts are written and parsed.
7. Shot list: 5 shots, ordered timecodes, 24 s total, EDL, JSON + Markdown artifacts.
8. Captions: SRT/VTT/JSON roundtrip, 4 cues, overlap detection, normalized SRT/VTT files.
9. Quality gates: 7 gates evaluated on real artifacts (pass), plus a forced failure
   (`accessibility`, invalid fps) and CLI artifact wiring.
10. Deterministic 9-stage plan and ranked routing, with saved JSON artifacts.
11. `run` dry-run: 9 simulated stages, no stage artifacts, checkpoint written.
12. `run` execute with `local`: 9 completed stages, 15 artifacts written and each JSON
    parsed, delivery checksums verified against file bytes, `report` rendered.
13. Direct single-capability execution (`--capability`): a generation capability writes a
    `spec_ready` job envelope, a caption capability writes normalized SRT, a sensitive
    capability is blocked until approval, and a changed brief produces a different `job_id`.
14. `deliver`: manifest with >=10 files and a changed delivery preset.
15. Approval: a publishing brief blocks 3 sensitive stages, preserves earlier stages, rejects
    an out-of-scope token, and completes after a job-wide approval plus `--resume`.
16. Recovery: unconfigured provider fails with `PROVIDER_UNAVAILABLE` and recovers with a
    changed provider + `--resume`; fault injection triggers 3 bounded attempts, removes
    `.partial` files, preserves 6 completed stages, and completes on resume.
17. Cancellation: a `CANCEL` file stops the run with a recovery message; removing it and
    resuming completes all stages.
18. `doctor`: config, schema, registry, all 9 resource references, folder layout, and
    provider policy.
19. Folder structure: 19 flat prefixed files, exactly one knowledge file, no `SKILL.md`, no
    subfolders, no nested archives; every documented resource reference resolves.
20. Clean skill root: the folder was copied beside one test `SKILL.md`; relative CLI
    invocation, `doctor`, `describe`, and explicit `--root` all passed.

Every command's output was opened/parsed/inspected. PDF source extraction was performed with
`pypdf` only in the build workspace (not a runtime dependency): 47 category blocks, 564
`Skill Name:` entries, and 564 `Description:` entries were read and normalized into
`/asset-capability-catalog.json`. The complete 68-page plain-text extraction was also fed back
through the shipped `ingest` command to confirm it recovers all 47 categories (including
wrapped headings such as "Foundations: Diffusion, Transformers, and Latent Video") and all
564 capabilities while ignoring page chrome and the appendix sections.

**Packaging validation** (performed on the final `ai-video-creation-skill-tool.zip`): the
archive was opened with Python `zipfile`, CRC-tested (OK), and inspected — 20 entries: one
directory `ai-video-creation/` directly at the ZIP root plus 19 flat files; no wrapper folder,
no nested archive, no `SKILL.md`, no subfolders, no unprefixed files. The ZIP was extracted
into a clean temporary location and every command was rerun from the extracted files using
only relative paths (`help`, `capabilities`, `doctor`, `describe`, `ingest`, `plan`, `prompt`,
`shotlist`, `captions`, `validate`, `gates`, `run` dry-run and execute, `report`, `deliver`,
`run --capability`, `approve`, `selftest`), all with exit code 0 and parseable JSON/text
outputs. The folder was also copied beside one test `SKILL.md` in a clean future skill root:
exactly 1 `SKILL.md`, 20 total files, maximum path depth 2 (within the 3-level limit and far
below 500 files); all commands and the full 28-check `selftest` passed there too. Both the
extracted root and the future skill root resolved every `/asset-*` reference with exact
case-sensitive spelling.

---

## 9. Limits and unavailable integrations

* **No neural video generation, image generation, TTS, lip sync, avatar rendering, audio
  analysis, VFX, color grading, or pixel-level encoding** is bundled. No diffusion model,
  transformer, GPU, or vendor SDK is present. Generative catalog capabilities are implemented
  as reachable, editable, validated **job envelopes** (`status: spec_ready`) that name the
  provider, model version, inputs, gates, approval, and failure behavior.
* **No ffmpeg / media probing / codec implementation.** Delivery presets are validated
  metadata; `deliver` checksums real local artifacts but never encodes a video.
* **No external services, storage, webhooks, GPU scheduler, telemetry backend, or CDN.**
  `providers.enabled` contains only `local`; requesting another provider yields
  `PROVIDER_UNAVAILABLE` with a recovery route. Add an adapter through `--config` and keep
  credentials outside the package.
* **No pixel-level QA.** The temporal-coherence and accessibility gates evaluate plans,
  prompts, shot timings, and caption cues; render-level checks are `deferred`.
* **Approvals are local tokens**, not an identity provider or enterprise approval system.
* Catalog descriptions are templated in the source PDF; the extracted registry preserves them
  verbatim (whitespace-normalized) and adds normalized ids/families/operations.
* A hosting, catalog, permission, or platform failure of an external resource is outside this
  package's control; the tool reports it as a provider/hosting issue rather than claiming the
  package can prevent it.

---

## 10. Troubleshooting, recovery, extension, and maintenance

| Symptom | Cause / fix |
|---|---|
| `RESOURCE_NOT_FOUND` | The `/asset-*.json` reference did not resolve. Run `doctor`; pass `--root` pointing at the skill root, or keep the folder intact so its own files are found. |
| `INPUT_NOT_FOUND` / `INPUT_SOURCE_ERROR` | Supply exactly one of `--input PATH` or `--text VALUE`; check the path. |
| `INPUT_INVALID_JSON` / schema errors | Brief must be a JSON object with `title` and `objective`; see `validate` errors and `/asset-brief-schema.json`. |
| `PROVIDER_UNAVAILABLE` | Only `local` is enabled. Add a provider adapter via `--config`, or rerun with `--provider local --resume`. |
| `APPROVAL_REQUIRED` (exit 3) | Run the printed `approve --confirm ...` command, then rerun with `--approval-token TOKEN --resume`. Use `--capability all` for job-wide scope. |
| `CONFIRMATION_REQUIRED` | `approve` recording/revocation needs `--confirm`. |
| `STAGE_TIMEOUT` / `JOB_TIMEOUT` | Raise `timeouts` in a `--config` override or split the job; completed stages are preserved for `--resume`. |
| `cancelled` | Remove the `CANCEL` file and rerun with `--resume`. |
| `RESUME_JOB_MISMATCH` | The checkpoint belongs to another brief; use a different `--output-dir` or regenerate the plan. |
| Gates `deferred` | Supply the missing artifact (`--artifact-prompt`, `--artifact-shotlist`, `--artifact-captions`, `--artifact-validation`) or review rendered media manually. |
| Ingestion missed an entry | Use labeled lines (`Category N: ...`, `N. Skill Name: ...`, `Description: ...`) or Markdown `**Name** — Description`; wrapped description lines are joined automatically. |

**Extension.** Add a capability by editing `/asset-capability-catalog.json` (or ingest a new
catalog and pass `--catalog`), add a style in `/asset-style-presets.json`, a delivery target in
`/asset-delivery-presets.json`, a gate in `/asset-quality-gates.json`, a workflow stage in
`/asset-default-config.json`, or a provider adapter in a `--config` override plus a bounded
handler in `tools-runtime.py`. Keep every new file flat and prefixed, update the resource
references here, and rerun `selftest` + `doctor`. **Maintenance:** keep ids/slugs stable
(`cNN-sNN`), keep `quality_gate_ids` in sync with the gates asset, and never add credentials,
`SKILL.md`, subfolders, or nested archives to this folder.

**Copilot skill compatibility.** Copy `ai-video-creation/` directly beside the parent
`SKILL.md`. The future combined package then has exactly one `SKILL.md` at its root, every path
is at most three levels deep, and the file count is far below 500. The delivery ZIP contains
the folder at its root with no wrapper folder and no nested archive.
