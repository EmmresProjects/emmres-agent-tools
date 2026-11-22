# offline-neural-video-pipeline — knowledge and operations guide

A local, **offline-first control plane, executor and validator** for the capabilities described in
*Full Offline Neural Video Skill Catalog* (Catalog Edition 1.0, November 2026 — 28 categories,
1235 capability concepts).

This folder is a reusable **Skill Tool component**. It contains no `SKILL.md`: copy the whole folder
beside a parent package's root `SKILL.md` and it becomes part of that skill. Every internal reference
below is relative to the future skill root (`<skill-root>/offline-neural-video-pipeline/<file>`).

---

## 1. What the tool actually does

The catalog is a capability map, not a runtime. This Skill Tool turns that map into something an
offline workstation can execute and audit:

| Layer | What is implemented | Truthfulness note |
| --- | --- | --- |
| Capability index | All 28 categories and all 1235 concepts, normalised with stable slugs, ids, descriptions and a routing operation per concept | Extracted from the supplied catalog PDF; text is preserved, not invented |
| Resolution & routing | `capability → operation → model registry entry → license → budget → ordered stage graph` | Registry numbers are **planning defaults** to be re-verified against the pinned model card |
| Preflight | Python, ffmpeg/ffprobe, encoders, filters, GPU (nvidia-smi), RAM, disk, model-file presence + SHA-256, input decodability, backend command, consent gate | All checks run locally and produce structured evidence |
| Execution | Real ffmpeg execution of input preparation, deterministic **proxy** implementations of each neural operation family, audio/loudness, programme assembly, finishing, caption burn-in/sidecar, delivery encode, validation, packaging | Neural inference itself is **not** bundled: it is dispatched to an operator-configured local runner, or replaced by a clearly labelled non-neural proxy |
| Validation | Full decode, duration, fps, resolution, pixel format, black segments, freezes, EBU R128 loudness, true peak, sample peak, audio presence, caption presence, bitrate, file size | Every figure is measured with ffprobe/ffmpeg on the real file |
| Governance | Append-only rights/consent ledger, consent gate, operator approvals, provenance with SHA-256 hashes, disclosure rendering, ZIP packaging with archive verification | Gated actions are refused without `--approve` |
| Delivery | `<job>-delivery.zip` containing media, `provenance.json`, `disclosure.md`, `validation-report.json`, `README.md` | No nested archives; verified after writing |

The tool never downloads weights, never contacts a network service, and never pretends that a
non-neural proxy is a neural result. A proxy deliverable carries an explicit notice in
`disclosure.md` and in `provenance.json` (`proxy_path_used: true`).

---

## 2. Files in this Skill Tool folder and their roles

Execution/load order is: `tools-onv.py` → loads `tools-onv-common.py` immediately →
other modules are loaded lazily on first use by the command that needs them.

| File | Role | Loaded by |
| --- | --- | --- |
| `tools-onv.py` | **Primary entry point.** Argument parsing, command dispatch, runtime context, job paths, signal handling, exit codes | Executed directly |
| `tools-onv-common.py` | Shared core: version, `OnvError` + exit codes, JSON/IO/hashing helpers, bounded subprocess `Runner` (timeout, retries, cancellation, logs), job-spec defaults + schema validation, path resolution | Imported by the entry point at startup |
| `tools-onv-planning.py` | Catalog index (`Catalog`), model registry (`Registry`), capability resolution, model routing, preset resolution, budget estimation, stage-graph construction, plan document | `plan`, `describe`, `capabilities`, `run`, `preflight`, `spec-init` |
| `tools-onv-preflight.py` | Environment/hardware/disk/model-file/input/backend/rights checks and the ffmpeg capability probe | `doctor`, `preflight`, `run` (first stage) |
| `tools-onv-exec.py` | Stage executors, job state for resume, backend dispatch + command templating, proxy fallbacks, cancel-safe process handling | `run` |
| `tools-onv-validate.py` | Media validation suite and report formatting | `validate`, `run` (validate stage) |
| `tools-onv-governance.py` | Rights ledger, consent evaluation, governance gate, provenance builder, disclosure rendering, delivery packaging, archive verification | `ledger`, `run`, `archive-verify` |
| `tools-onv-selftest.py` | End-to-end regression suite driving the real CLI in a temporary workspace | `selftest` |
| `asset-capability-catalog.json` | Normalised 28-category / 1235-concept index with per-concept operation routing | All routing commands |
| `asset-model-registry.json` | Offline model routing registry (SVD, AnimateDiff, CogVideoX, HunyuanVideo, Wan, LTX-Video, Mochi, LivePortrait, Wav2Lip, RIFE, Real-ESRGAN, BasicVSR++, latent editor) + proxy filter descriptions | Planning |
| `asset-preset-recipes.json` | Render presets (`draft`, `preview`, `delivery`, `archive`) and per-operation overrides | Planning |
| `asset-validation-policy.json` | Validation thresholds, severities, approval requirements, limits | Validation, packaging |
| `asset-governance-policy.json` | Consent rules, allowed-use scopes, disclosure rules, provenance requirements, license blocking, retention | Governance |
| `asset-disclosure-templates.json` | Rendered into package `disclosure.md` (short/full statement, consent attestation, proxy notice, package README) | Packaging |
| `asset-job-spec.template.json` | Starting point for `spec-init` | `spec-init` |

16 files, all directly inside this folder, no subfolders, no `SKILL.md`.

---

## 3. Requirements and setup

* **Python 3.8+** (tested on CPython 3.13.16). Standard library only — no `pip install` is required or used.
* **ffmpeg + ffprobe** on `PATH`, or absolute paths in a config file. Practically required for
  `preflight`, `run`, `validate` and `doctor`. An ffmpeg built with `libx264`, `aac`,
  `minterpolate`, `zoompan`, `hqdn3d`, `deflicker`, `blackdetect`, `freezedetect`, `loudnorm`
  and (for caption burn-in) **libass** is recommended.
* No GPU requirement for the proxy/validation path. A CUDA/NVIDIA host is only probed via
  `nvidia-smi` when it exists.
* No network access is used at any point.

Install ffmpeg if missing:

```sh
# Debian/Ubuntu
sudo apt-get install -y ffmpeg
# macOS (Homebrew)
brew install ffmpeg
```

Make the entry point executable (optional):

```sh
chmod +x <skill-root>/offline-neural-video-pipeline/tools-onv.py
```

All commands below are shown from the **skill root** with an explicit shell and explicit relative
paths; replace `<job-spec>` etc. with your own filenames.

```sh
sh -c 'cd <skill-root> && python3 offline-neural-video-pipeline/tools-onv.py help'
```

---

## 4. Quick start (verified commands)

```sh
# 1. environment + assets
python3 offline-neural-video-pipeline/tools-onv.py doctor

# 2. capability surface
python3 offline-neural-video-pipeline/tools-onv.py capabilities
python3 offline-neural-video-pipeline/tools-onv.py capabilities --category 1
python3 offline-neural-video-pipeline/tools-onv.py capabilities --search "lip sync"
python3 offline-neural-video-pipeline/tools-onv.py describe offline-image-to-video-engine

# 3. create and edit a job spec
python3 offline-neural-video-pipeline/tools-onv.py spec-init \
    --capability offline-image-to-video-engine --job-id demo-001 \
    --input inputs/reference.png --preset preview --out job.json

# 4. plan (capability -> model -> budget -> stages)
python3 offline-neural-video-pipeline/tools-onv.py plan --spec job.json --show-command

# 5. preflight (hardware, models, rights, disk, inputs)
python3 offline-neural-video-pipeline/tools-onv.py preflight --spec job.json

# 6. execute
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --approve

# 7. validate any delivered file
python3 offline-neural-video-pipeline/tools-onv.py validate --file outputs/demo-001.mp4

# 8. inspect the delivery archive
python3 offline-neural-video-pipeline/tools-onv.py archive-verify \
    --archive packages/demo-001-delivery.zip

# 9. regression suite
python3 offline-neural-video-pipeline/tools-onv.py selftest
```

---

## 5. How a job flows

```
spec.json
  │ validate_spec()  (types, ranges, defaults, path resolution)
  ▼
Catalog.resolve(capability) ──► operation (generate | transform | animate | interpolate |
  │                                        restore | edit | audio | caption | color |
  │                                        encode | validate | govern | operate)
  ▼
Registry.route(operation, spec, hardware) ──► model id + license + proxy equivalent
  ▼
Preset + params ──► estimate_budget() ──► VRAM / RAM / disk / time / frame clamp
  ▼
build_stages() ──► ordered stage graph (with approval flags and timeouts)
  ▼
run_job()
  governance gate → preflight → prepare-inputs → [neural-*] → [audio] → [compose]
                  → [color] → [caption] → encode → validate → package
  ▼
outputs/<job>.mp4 + .onv-work/<job>/state.json + reports/ + packages/<job>-delivery.zip
```

Stage executors:

| Executor | Stage ids | Behaviour |
| --- | --- | --- |
| `internal` | `preflight`, `validate`, `package` | Delegated to the preflight/validate/governance modules |
| `ffmpeg-prepare` | `prepare-inputs` | ffprobe each input, optional SHA-256 verification, records `current_media` |
| `ffmpeg-proxy` | `neural-generate`, `neural-animate`, `neural-interpolate`, `neural-restore`, `neural-edit`, `neural-transform` | Deterministic ffmpeg implementation (see §9) |
| `backend` | the same neural stage when `spec.mode` is `backend`/`auto` with a command | Runs the operator's local neural runner with a hard timeout, retries, approval gate |
| `ffmpeg-audio` | `audio` | Loudness normalisation (`loudnorm`) and dialogue/music mix |
| `ffmpeg-compose` | `compose` | Multi-clip programme assembly (scale/pad/concat filter graph) |
| `ffmpeg-colour` | `color` | Finishing pass (`eq` + `colorlevels`) |
| `ffmpeg-caption` | `caption` | Subtitle burn-in via libass, or sidecar SRT when `mode: sidecar` |
| `ffmpeg-encode` | `encode` | Delivery encode (H.264/H.265/AV1/VP9, AAC/Opus, `+faststart`) and size limit |

`run` stops on the first blocking stage unless `spec.fallback.on_backend_unavailable` allows a
labelled recovery (§11).

---

## 6. Command reference

Global options accepted by every command: `--json`, `--quiet`, `--verbose`, `--workdir PATH`,
`--approve`, `--overwrite`, `--config PATH`, and asset overrides `--catalog`, `--registry`,
`--presets`, `--policy`, `--governance`, `--disclosures`.

Exit codes: `0` ok · `2` invalid input/spec · `3` media validation failed · `4` blocked
(approval, consent, backend or preflight) · `5` internal/runtime failure · `6` cancelled.

### 6.1 `help`
Prints usage, all commands, global options, examples, exit codes and the 28-category summary.

```sh
python3 offline-neural-video-pipeline/tools-onv.py help
python3 offline-neural-video-pipeline/tools-onv.py help --json
```

### 6.2 `capabilities` — the capability list command
Prints the catalog summary; supports drill-down and search.

```sh
python3 offline-neural-video-pipeline/tools-onv.py capabilities              # 28 categories, 1235 concepts
python3 offline-neural-video-pipeline/tools-onv.py capabilities --category 11
python3 offline-neural-video-pipeline/tools-onv.py capabilities --category frame-interpolation-slow-motion-restoration-and-upscaling
python3 offline-neural-video-pipeline/tools-onv.py capabilities --search "lip sync" --limit 40
python3 offline-neural-video-pipeline/tools-onv.py capabilities --op interpolate
python3 offline-neural-video-pipeline/tools-onv.py capabilities --concept offline-lip-sync-model
```

`--op` values: `generate`, `transform`, `animate`, `interpolate`, `restore`, `edit`, `audio`,
`caption`, `color`, `encode`, `validate`, `govern`, `operate`.

### 6.3 `describe <capability>`
Explains one capability: operation family, catalog description, default model route, license
and redistribution status, proxy equivalent, and preset geometry. Accepts a concept slug, concept
id (`c01-002`), category slug, or operation name.

```sh
python3 offline-neural-video-pipeline/tools-onv.py describe offline-image-to-video-engine
python3 offline-neural-video-pipeline/tools-onv.py describe c10-021 --vram-gb 12 --model-id liveportrait
```

### 6.4 `doctor`
Environment report: Python version, ffmpeg version + required encoders/filters with yes/no,
GPU/RAM, workdir writability, free disk, and every asset file with its version and size. Use this
first when a job refuses to start.

### 6.5 `spec-init`
Writes `asset-job-spec.template.json` with optional overrides.

```sh
python3 offline-neural-video-pipeline/tools-onv.py spec-init \
    --capability offline-video-denoising-model --job-id restore-001 \
    --input inputs/shot.mp4 --input-role video --preset delivery --out job.json
```
`--force` overwrites an existing file; without it the command refuses (exit 2).

### 6.6 `plan`
Resolves capability → operation → model → budget → stages and writes
`<workdir>/.onv-work/<job_id>/plan.json`. `--show-command` prints the resolved backend command
and expected output path.

```sh
python3 offline-neural-video-pipeline/tools-onv.py plan --spec job.json
python3 offline-neural-video-pipeline/tools-onv.py plan --spec job.json --show-command --json
python3 offline-neural-video-pipeline/tools-onv.py plan --spec job.json --out custom-plan.json
```

### 6.7 `preflight`
Runs the checks and writes `.onv-work/<job_id>/reports/preflight-<job_id>.json`.
Exit `0` when ready, `4` when a blocking check failed. `--plan PATH` reuses an existing plan.

### 6.8 `run`
Executes the pipeline. Options: `--resume`, `--dry-run`, `--validate-only`, `--overwrite`
(requires `--approve`), `--approve`.

```sh
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --dry-run
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --approve
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --approve --resume
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --approve --overwrite
python3 offline-neural-video-pipeline/tools-onv.py run --spec job.json --validate-only
```

### 6.9 `validate`
Validates any media file (its own or externally produced). With `--spec` the plan's expected
duration/fps/resolution and the job's limits are used as expectations.

```sh
python3 offline-neural-video-pipeline/tools-onv.py validate --file outputs/demo-001.mp4
python3 offline-neural-video-pipeline/tools-onv.py validate --file master.mp4 --spec job.json --out report.json
```
Exit `0` when the policy passes, `3` when a check with `severity: error` fails, `2` when the file
is missing or undecodable.

### 6.10 `ledger` — consent and rights records
```sh
# record consent (requires --approve)
python3 offline-neural-video-pipeline/tools-onv.py ledger add \
    --file ledger/rights-ledger.json --subject actor-001 --status granted \
    --scope internal-preview --evidence consent-2026-05-01.pdf --granted-by producer --approve

# several subjects, expiry and license metadata
python3 offline-neural-video-pipeline/tools-onv.py ledger add --file ledger/rights-ledger.json \
    --subject actor-001 --subject actor-002 --status granted --scope commercial-delivery \
    --evidence msa-2026.pdf --license operator-declared --expires 2027-01-01T00:00:00Z --approve

# inspect / check
python3 offline-neural-video-pipeline/tools-onv.py ledger list --file ledger/rights-ledger.json
python3 offline-neural-video-pipeline/tools-onv.py ledger check --file ledger/rights-ledger.json \
    --subject actor-001 --scope internal-preview
```
`ledger check` exits `0` when every subject passes and `4` when blocked. `--scope` must be one of
the `scopes.allowed_use_values` in `asset-governance-policy.json`.

### 6.11 `archive-verify`
Opens a delivery ZIP, tests CRCs, rejects nested archives and unsafe member paths, and lists members.

```sh
python3 offline-neural-video-pipeline/tools-onv.py archive-verify --archive packages/demo-001-delivery.zip
```

### 6.12 `selftest`
Runs the built-in regression suite (42 cases, ~23 s) in a throw-away workspace and prints
per-case results. `--keep` keeps the workspace, `--timeout SECONDS` bounds the whole run.

```sh
python3 offline-neural-video-pipeline/tools-onv.py selftest
python3 offline-neural-video-pipeline/tools-onv.py selftest --json | python3 -m json.tool
```

### 6.13 `version`
```sh
python3 offline-neural-video-pipeline/tools-onv.py version
```

---

## 7. Job spec schema

`spec-init` gives you a valid file; the schema below is the contract enforced by
`validate_spec()` in `tools-onv-common.py`. Unknown top-level keys are ignored and reported as
warnings. Relative paths resolve against the **spec file's directory**.

```jsonc
{
  "spec_version": "1.0",              // informational
  "job_id": "demo-001",               // REQUIRED, 1-96 chars, no path separators
  "title": "optional human label",
  "capability": "offline-image-to-video-engine", // REQUIRED: concept slug/id, category slug, or op name
  "op": null,                          // optional override of the routed operation family
  "mode": "auto",                      // auto | backend | proxy | validate-only
  "preset": "preview",                 // key in asset-preset-recipes.json
  "prompt": "text prompt (string)",
  "inputs": [                          // list of strings or objects
    {"role": "image", "path": "inputs/reference.png", "sha256": null}
  ],
  "params": {                          // overrides preset geometry/quality
    "width": 640, "height": 360, "fps": 24, "duration_s": 3.0,
    "frames": null, "steps": 20, "seed": 1234, "scale": 2, "interp_exp": 2,
    "negative_prompt": null
  },
  "hardware": {"profile": "auto", "vram_gb": null, "ram_gb": null, "allow_cpu": true},
  "models": {
    "root": "models",                  // directory searched for registry weight globs
    "model_id": null,                  // force a registry entry
    "files": [{"path": "svd_xt.safetensors", "sha256": "…", "size_gb": 9.6}],
    "check_hashes": true
  },
  "backend": {
    "command": null,                   // string or list; placeholders below
    "timeout_s": 900,                  // 1..∞, per attempt
    "retries": 0                       // 0..5
  },
  "output": {"dir": "outputs", "filename": null},   // filename must have no separators
  "delivery": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac", "package": false},
  "rights": {
    "ledger": "ledger/rights-ledger.json",
    "require_consent": false,
    "subject_ids": [],
    "allowed_use": "internal-preview",
    "license": null,
    "disclosure_required": true
  },
  "limits": {"timeout_s": 3600, "max_frames": 240, "max_output_mb": 2048},
  "proxy": {"allow_placeholder": false},
  "fallback": {"on_backend_unavailable": "fail"},   // fail | proxy | skip
  "audio": null,      // {"mode": "normalize", "target_lufs": -16.0, "sources": [...]}
  "captions": null,   // {"srt": "subs.srt", "mode": "burn"} or {"cues": [{"start":0,"end":1.2,"text":"hi"}], "mode":"burn"}
  "compose": null     // {"clips": ["a.mp4","b.mp4"], "mode": "concat"}
}
```

Input roles recognised by the executor: `image`/`still`/`portrait`/`reference` (images),
`video`/`source`/`clip`/`driving`/`reference-video` (videos), `audio`/`voice`/`dialogue`/`music`/
`speech` (audio), `mask`.

### Backend command placeholders

`spec.backend.command` (string, shell-split safely, or list of argv items) may use:

`{job_id}` `{model_path}` `{model_dir}` `{input_image}` `{input_video}` `{input_audio}` `{mask}`
`{out_dir}` `{output}` `{width}` `{height}` `{fps}` `{frames}` `{duration_s}` `{duration}` `{seed}`
`{steps}` `{scale}` `{interp_exp}` `{prompt}` `{negative_prompt}` `{preset}` `{capability}`

If `{output}` is absent the resolved output path is appended as the final argument. Unknown
placeholders are rejected with `E_SPEC` before anything runs.

```json
"backend": {"command": "python3 -m svd_runner --model {model_path} --image {input_image} --out {output} --frames {frames} --fps {fps} --seed {seed}", "timeout_s": 900, "retries": 1}
```

### Config file (`--config`)

```json
{
  "ffmpeg": "/usr/local/bin/ffmpeg",
  "ffprobe": "/usr/local/bin/ffprobe",
  "assets": {
    "catalog": "asset-capability-catalog.json",
    "registry": "asset-model-registry.json",
    "presets": "asset-preset-recipes.json",
    "policy": "asset-validation-policy.json",
    "governance": "asset-governance-policy.json",
    "disclosures": "asset-disclosure-templates.json"
  },
  "paths": {"work_root": ".onv-work", "package_root": "packages"}
}
```
Asset paths in the config resolve relative to this Skill Tool folder; absolute paths are also
accepted. `--workdir` sets the project root under which `work_root` and `package_root` are created.

### Runtime layout created in the project workdir

```
<workdir>/outputs/<job_id>.mp4                         delivery media
<workdir>/.onv-work/<job_id>/plan.json                 plan document
<workdir>/.onv-work/<job_id>/state.json                resumable job state
<workdir>/.onv-work/<job_id>/scratch/                  intermediate media + inputs.json
<workdir>/.onv-work/<job_id>/logs/                     per-attempt command logs
<workdir>/.onv-work/<job_id>/reports/                  preflight/validation JSON
<workdir>/packages/<job_id>-delivery.zip               delivery archive
<workdir>/packages/<job_id>/                           unpacked package contents
```

---

## 8. Assets and how to edit them

| Asset | Editable knobs | Effect |
| --- | --- | --- |
| `asset-capability-catalog.json` | `operations`, per-concept `op`, category titles/slugs, descriptions | Changes what `capabilities`/`describe` show and which operation each concept routes to |
| `asset-model-registry.json` | `models[]` (`ops`, `weights.filename_globs`, `approx_size_gb`, `residency.vram_min_gb`, `vram_reference`, `limits.frames_per_second`, `license`, `backend.command_template`, `default_for_ops`) and `proxy_filters` | Changes routing choice, VRAM/time estimates, weight detection, license status, default command, proxy behaviour |
| `asset-preset-recipes.json` | `presets.*` geometry/fps/steps/crf/x264 preset/audio bitrate/LUFS/throughput, `op_overrides` | Changes default geometry, encode quality and audio target per preset |
| `asset-validation-policy.json` | `checks.*` thresholds, `severity.*`, `fail_mode`, `approvals.require_for`, `limits.*` | Changes what passes and what must be approved |
| `asset-governance-policy.json` | `consent.required_when`, `scopes.allowed_use_values`, `disclosure.*`, `provenance.required_records`, `licenses.blocking_status` | Changes consent/disclosure/licence gate behaviour |
| `asset-disclosure-templates.json` | template bodies | Changes generated `disclosure.md` / `README.md` wording |
| `asset-job-spec.template.json` | any spec field | Changes what `spec-init` writes |

**Registry numbers are planning defaults.** Before production use on an air-gapped host, re-verify
VRAM, throughput, weight filenames and license terms from the pinned model card, then update the
registry. Adding a model is a data-only change (new object in `models[]`, plus a
`backend.command_template`); no code edit is required.

---

## 9. Capability coverage: operations and proxy behaviour

`capabilities --op <op>` lists every catalog concept routing to an operation. The proxy column is
what actually executes when no neural backend is available.

| Operation family | Catalog examples | Proxy implementation (real ffmpeg) | Fidelity |
| --- | --- | --- | --- |
| `generate` | Text-to-video, image-to-video, keyframe-to-video, storyboard-to-video, looping/cinemagraph | `zoompan` Ken Burns animatic from the first image input, scaled/padded to the plan geometry; `testsrc2` placeholder only when `spec.proxy.allow_placeholder=true` | Pipeline scaffolding only — not neural generation |
| `animate` | Lip-sync, face animation, talking avatar, camera/product animation | Same `zoompan` still-motion path | Pipeline scaffolding only |
| `transform` | Video-to-video, motion/style transfer, identity adapters | `curves=medium_contrast` + `hue=s=1.08` LUT-style restyle | Global look change only |
| `interpolate` | RIFE/FILM/DAIN interpolation, slow motion, time-lapse, 24/25/30 fps conversion | `minterpolate=fps=N:mi_mode=mci:mc_mode=aobmc:vsbmc=1` | Real frame synthesis; may warp on fast motion |
| `restore` | Upscaling, denoise, deflicker, deblur, frame restoration, artifact removal | `hqdn3d` + `deflicker` + `scale` (lanczos/bicubic) + `unsharp` | Classical, honest preview stand-in |
| `edit` | Inpainting, outpainting, object removal, relighting, colorisation, scene extension | `eq` brightness/contrast/saturation relight approximation | Cannot remove or replace objects |
| `audio` | Speech, dialogue, music, SFX, loudness, mix | `loudnorm` EBU R128 at the preset/job target plus optional audio-input mix | Real, measured loudness |
| `caption` | Subtitles, SRT/VTT, accessibility media | `subtitles` burn-in (libass) or sidecar SRT writer, inline cues supported | Real |
| `color` | Colour, lighting, HDR/finishing | `eq` + `colorlevels` finishing pass | Deterministic grade |
| `encode` | Encoding, containers, codecs, delivery formats | `libx264`/`libx265`/`libsvtav1`/`libvpx-vp9` + `aac`/`libopus` with `+faststart` | Real |
| `validate` | Decode/quality/QC/consent gates | Full validation suite (§10) | Real, measured |
| `govern` | Safety, consent, rights, provenance, disclosure | Ledger + gate + provenance + disclosure (§11) | Real |
| `operate` | Runtime, hardware, orchestration, packaging, diagnostics, regression | Plan/budget/preflight/package/selftest machinery | Real |

Budgets are deterministic heuristics, documented in `plan.json` (`budget.method`): VRAM scales
from `residency.vram_min_gb` by `(pixels × frames) / vram_reference`^0.85, time from
`limits.frames_per_second`, disk from the preset's `target_bitrate_kbps`, and frames are clamped to
`spec.limits.max_frames`.

---

## 10. Validation checks actually implemented

`validate_media()` in `tools-onv-validate.py`, thresholds from `asset-validation-policy.json`:

| Check | Method | Default threshold | Severity |
| --- | --- | --- | --- |
| `probe` | `ffprobe -show_format -show_streams -count_frames` | video stream must exist | error |
| `decode` | full decode to `-f null -` with `-v error`, error lines counted | `max_error_lines: 0` | error |
| `duration` | measured vs plan expectation | `max(0.35 s, 8 %)` | warning |
| `fps` | measured vs plan expectation | 2 % | warning |
| `resolution` | measured vs plan, even dimensions required | exact / even | error |
| `pix_fmt` | ffprobe `pix_fmt` against allow-list | yuv420p family, nv12, rgb24 | warning |
| `black` | `blackdetect=d=0.5:pix_th=0.10` | ≤ 2.0 s total | warning |
| `freeze` | `freezedetect=n=-60dB:d=2.0` | ≤ 3.0 s total | warning |
| `loudness` | `loudnorm=print_format=json` | target ±3 LU, true peak ≤ −1 dBTP, LRA ≤ 20 LU | warning |
| `clipping` | `volumedetect` | sample peak ≤ −0.05 dBFS | warning |
| `audio_presence` | stream presence vs declared audio | must exist when declared | info |
| `caption` | subtitle stream or SRT/VTT sidecar cue count | ≥ 1 cue when declared | warning |
| `bitrate` | size ÷ duration | ≥ 200 kbps | warning |
| `filesize` | `os.stat` vs `spec.limits.max_output_mb` | limit | error |

Any `fail` with `severity: error` makes the report invalid (exit 3). `warn`/non-error `fail`
values are reported and do not block unless `fail_mode` is changed.

---

## 11. Safety, approval and governance rules

1. **Approval is required before** dispatching a neural backend command, writing delivery
   packaging, overwriting an existing output, and writing consent/rights ledger records. Without
   `--approve` these actions return `E_APPROVAL` (exit 4). `--overwrite` is additionally refused
   unless `--approve` is also present.
2. **Consent gate.** If `spec.rights.require_consent` is true, or the operation is in
   `consent.required_when` and subjects are declared, the run is blocked before any media work
   unless every subject has a `granted` ledger record with evidence, a matching scope, and no
   expiry in the past. There is no operator override flag for consent.
3. **Disclosure.** Operations in `disclosure.required_for_ops` (generate, animate, transform, edit)
   refuse `disclosure_required: false`. Packages always include `disclosure.md`, and a proxy run
   additionally gets the explicit non-neural notice.
4. **Licenses.** Provenance records the model license name and redistribution status. Anything the
   registry marks `unknown` blocks delivery packaging until an operator records a decision
   (`licenses.blocking_status` in `asset-governance-policy.json`).
5. **Provenance.** `provenance.json` records job id, spec SHA-256, tool/catalog/registry versions,
   model ids and licenses, input hashes, output hash, validation summary, proxy usage and operator
   approval state.
6. **Bounded execution.** Every external process runs with a hard timeout, optional retries with
   backoff, `start_new_session=True` process groups, and log capture to
   `.onv-work/<job>/logs/<stage>.attemptN.log`. stdout/stderr are capped, and cancellation
   (SIGINT/SIGTERM) kills the whole process group.
7. **No arbitrary execution.** Only two things are executed: the operator-declared
   `spec.backend.command` (placeholder-validated, approval-gated) and ffmpeg/ffprobe/nvidia-smi.
   There is no shell interpretation, no `eval`, no network access, no credential use.
8. **Ledger is append-only in practice.** Records are appended; editing history is out of scope by
   design — correct a mistake by appending a new record (for example `status: withdrawn`).

---

## 12. Validation and tests actually completed

Environment used for the recorded runs: Linux, CPython 3.13.16, ffmpeg 7.1.5 (Debian build),
no GPU, 1.9 GB RAM detected. Commands below were executed successfully from a project workdir;
the tool folder itself was not modified by the runs.

### 12.1 Built-in regression suite — 42/42 cases passed (~23 s)

```sh
python3 offline-neural-video-pipeline/tools-onv.py selftest
# Selftest: 42/42 cases passed in 22.7s
# Result: ALL CASES PASSED
```

| Case | Asserts |
| --- | --- |
| `help` | command set printed |
| `capabilities` | **28 categories, 1235 concepts** |
| `capabilities-category` | category 1 holds 40 concepts |
| `capabilities-search` | `"lip sync"` returns matches |
| `capabilities-op` | `interpolate` has concepts |
| `describe` | model route and proxy reported |
| `describe-unknown` | unknown capability → exit 2 `E_SPEC` |
| `doctor` | ffmpeg detected, `minterpolate` present |
| `spec-init` | spec file written |
| `plan` | operation `generate`, one neural stage, proxy executor, plan file written |
| `plan-invalid-capability` | exit 2 `E_SPEC` |
| `plan-missing-field` | missing `job_id` → exit 2 |
| `plan-missing-spec` | exit 2 `E_NOT_FOUND` |
| `preflight-ok` | `ready: true` |
| `preflight-missing-input` | exit 4, `ready: false` |
| `ledger-add-needs-approval` | exit 4 `E_APPROVAL` |
| `ledger-add` / `ledger-check` / `ledger-list` | granted record passes the gate |
| `ledger-add-denied` / `ledger-check-denied` | denied record blocks (exit 4) |
| `run-consent-blocked` | governance stage fails, run exit 4 |
| `run-proxy` | end-to-end success, delivery file + package, `validation_passed: true`, `proxy_path_used: true` |
| `validate-output` | delivered file passes `decode` and `resolution` |
| `archive-verify` | package valid, contains `provenance.json` and `disclosure.md` |
| `run-resume` | ≥ 3 stages skipped on resume |
| `run-backend-approval` | backend dispatch without `--approve` → exit 4 `E_APPROVAL` |
| `run-backend-fallback` | failed backend recovers through the labelled proxy path |
| `run-variant` | changing `duration_s` to 4.0 changes measured output duration (> 3 s) |
| `validate-broken` | corrupt file rejected (exit 2) |
| `run-dry-run` | stage list only, no execution |
| `run-validate-only` | validate-only mode reachable (exit 3 on non-conforming media) |
| `run-audio` | audio stage succeeds; loudness check passes |
| `run-caption` | caption burn-in succeeds |
| `run-compose` | two-clip assembly succeeds |
| `run-interpolate` | 48 fps retime validates |
| `run-restore` | restore proxy succeeds |
| `validate-policy-failure` | resolution mismatch → exit 3 |
| `run-overwrite-guard` | existing output protected without `--overwrite` (exit 4) |
| `run-overwrite-approved` | `--approve --overwrite` succeeds |
| `plan-show-command` | resolved backend command rendered |
| `config-file` | `paths.work_root` honoured |

### 12.2 Additional manual verification performed

| Test | Command (abridged) | Result |
| --- | --- | --- |
| Proxy pipeline on a still | `run --spec spec-a.json --approve` | succeeded; 320×180 @ 12 fps, 2.000 s, 12 validation checks, package with 5 members |
| Audio normalisation | `run --spec job-audio.json --approve` | `loudness pass integrated -16.1 LUFS, true peak -8.82 dBTP`, `clipping pass -8.80 dBFS` |
| Caption burn-in | `run --spec job-caption.json --approve` | captions burned in from `subs.srt`; 14 checks pass |
| Two-clip assembly | `run --spec job-compose.json --approve` | `concatenated 2 clips` |
| Interpolation | `run --spec job-interp.json --approve` | `minterpolate` 24→48 fps, fps check pass |
| Restoration | `run --spec job-restore.json --approve` | `hqdn3d + deflicker + lanczos` proxy, 320×180→640×360 |
| Colour finishing | `run --spec job-color.json --approve` | finishing pass + encode, 14 checks pass |
| Policy failure | `validate --file outputs/job-interp.mp4 --spec job-restore.json` | `PACKAGE INVALID … 1 blocking check(s): resolution`, exit 3 |
| Standalone validation | `validate --file outputs/job-interp.mp4` | `PACKAGE VALID`, exit 0, bitrate warning only |
| Missing media | `validate --file missing.mp4` | `E_INPUT`, exit 2 |
| Backend timeout | `backend.timeout_s: 2`, `retries: 1`, runner sleeps 30 s | failed after 2 attempts in ~6.5 s, exit 5, timeout message recorded |
| Cancellation | SIGINT during a 60 s backend runner | child process group killed, status `CANCELLED`, exit 6, no orphan processes |
| Overwrite protection | re-run without `--overwrite` | `E_APPROVAL`, exit 4 |
| `--op` override | capability `color-script-builder` with `op: restore` | plan routed to `neural-restore` |
| Config file | `--config onv-config.json plan --spec …` | plan written under `.custom-work/` |
| Plan reuse | `preflight --plan <plan.json>` | reused plan, `ready: true` |
| Archive inspection | `archive-verify --archive packages/job-a-delivery.zip` | valid, 5 members, no nested archives |
| JSON error envelope | `plan --spec nope.json --json` | `{"ok": false, "error": {"code": "E_NOT_FOUND", …}}`, exit 2 |

No test in this document is projected or invented; every row reflects an executed command.

---

## 13. Limits and integrations that are not available

* **No model weights are bundled and no neural inference is performed by this tool.** Generation,
  face animation, lip sync, video-to-video and latent editing require an operator-supplied local
  runner (`spec.backend.command`) plus weights placed under `spec.models.root`. Without them the
  run is either blocked (`fallback: fail`), recovered through the labelled proxy (`fallback: proxy`)
  or skipped (`fallback: skip`).
* **Proxy output is not a neural deliverable.** It is deterministic ffmpeg processing; the package
  says so in `disclosure.md` and `provenance.json`.
* **Automatic transcription is not implemented.** Captions come from an operator-supplied SRT/VTT
  or inline cues; ASR must be run by a separate offline tool and fed in as a sidecar.
* **No model download, no network, no cloud API, no credential handling.** Deliberate.
* **GPU support is detection-only** (`nvidia-smi` name/memory/driver). The tool does not initialise
  CUDA, PyTorch or any inference runtime, and does not measure real VRAM residency — figures are
  planning estimates.
* **License verification is manual.** The registry records license name and redistribution status
  but cannot read the pinned license file; anything marked `unknown` blocks packaging until an
  operator records a decision.
* **Caption burn-in needs an ffmpeg built with libass.** Without it the stage blocks with
  `E_BLOCKED` and suggests `mode: sidecar` (verified behaviour; the test host's ffmpeg has libass).
* **Registry numbers (VRAM, throughput, size) are planning defaults**, not measurements of your
  pinned weights.
* **Concurrency is serial**: one job per process, one stage at a time
  (`limits.max_parallel_stages: 1`). Parallel batch scheduling is out of scope for this component.
* **Perceptual quality scoring (LPIPS/SSIM/PSNR) is not implemented**; perceptual/human-review
  catalog concepts route to `validate` but the automated suite is structural + audio + caption
  based. Add a scorer in `tools-onv-validate.py` if you need it.

---

## 14. Troubleshooting and recovery

| Symptom | Code / exit | Cause | Fix |
| --- | --- | --- | --- |
| `ffmpeg not found or not executable` | check `media.ffmpeg`, exit 4 at preflight | ffmpeg missing from PATH | install ffmpeg or set `ffmpeg` in the config file |
| `missing encoders: libx264` | check `media.encoders` | minimal ffmpeg build | install a fuller build or change `spec.delivery.video_codec` |
| `no weights matched registry globs` | `models.weights` warn | weights absent from `spec.models.root` | place pinned weights there, declare `spec.models.files`, or use `mode: proxy` |
| `no GPU detected` | `hardware.gpu` warn | no NVIDIA GPU/smi | pass `spec.hardware.vram_gb`, choose a smaller preset, or use the proxy path |
| `input missing: …` | `E_INPUT` / preflight exit 4 | wrong relative path | inputs resolve against the spec file's directory |
| `backend dispatch … requires explicit approval` | `E_APPROVAL` exit 4 | gated action | review `plan --show-command`, then re-run with `--approve` |
| `backend executable not found` | `E_BACKEND` exit 4 | bad command | pin the absolute runner path or add it to PATH |
| `backend finished without producing the declared output` | `E_BACKEND` exit 4 | runner ignored `{output}` | make the runner write to the `{output}` argument |
| `command timed out after Ns` | `E_RUNTIME` exit 5 | slow runner | raise `spec.backend.timeout_s` / `spec.limits.timeout_s`, or lower frames |
| `consent gate blocked the job` | governance stage failed, exit 4 | missing/denied/expired consent | `ledger add … --status granted --evidence …` then re-run |
| `refusing to overwrite existing output` | `E_APPROVAL` exit 4 | output exists | add `--overwrite` (and `--approve`) or change `job_id`/filename |
| `this ffmpeg build has no 'subtitles' filter` | `E_BLOCKED` exit 4 | no libass | install libass-enabled ffmpeg or `captions.mode: sidecar` |
| `validation failed: N blocking check(s)` | exit 3 | media does not meet policy | read `.onv-work/<job>/reports/validation-*.json`, adjust source or thresholds |
| `exceeds spec.limits.max_output_mb` | `E_VALIDATION` exit 3 | file too large | raise the limit, lower CRF quality (`crf` up) or use a smaller preset |
| `proxy path … needs a reference image or video input` | `E_BLOCKED` exit 4 | no input for the proxy | add an input, set `spec.proxy.allow_placeholder: true`, or configure a backend |
| `preset 'X' is not defined` | `E_SPEC` exit 2 | typo or custom presets file | use `draft|preview|delivery|archive` or add the preset to the asset |
| `capability 'X' not found` | `E_SPEC` exit 2 | wrong slug | `capabilities --search <text>`; details list the closest matches |

Recovery procedures:

* **Resume a failed or interrupted job**: `run --spec job.json --approve --resume`. Stages whose
  state is `succeeded` and whose outputs still exist are skipped; everything else re-runs.
* **Re-run from scratch**: delete `.onv-work/<job_id>/` (and `outputs/<job>.mp4`) or use a new
  `job_id`.
* **Inspect what happened**: `.onv-work/<job_id>/state.json` (per-stage status, attempts,
  durations, artifacts), `reports/preflight-*.json`, `reports/validation-*.json`, and
  `logs/<stage>.attemptN.log`.
* **Repackage without re-rendering**: the package directory `packages/<job_id>/` is complete; add
  files there and re-zip, then check with `archive-verify`.
* **Change only the deliverable quality**: edit `spec.preset` or `params.crf` and re-run with
  `--overwrite`; upstream proxy/backend stages re-run unless resumed.
* **Safe cleanup**: the tool only deletes inside `.onv-work/<job_id>/`, `packages/<job_id>/` and
  `outputs/` paths it created for that job. Nothing outside the workdir is touched, except that
  `package_delivery` replaces an existing `packages/<job_id>/` directory for the same job.

---

## 15. Extension points

| Goal | Where | How |
| --- | --- | --- |
| Add a model | `asset-model-registry.json` | Append an object with `id`, `ops`, `weights.filename_globs`, `residency`, `limits`, `license`, `backend.command_template` |
| Change default routing | `default_for_ops` in the registry, or `spec.models.model_id` | Explicit id wins over automatic routing |
| Add a proxy behaviour | `proxy_filters` in the registry + `stage_proxy()` in `tools-onv-exec.py` | Add the ffmpeg filter and an `elif op == …` branch |
| Add an operation family | `operations` in `asset-capability-catalog.json`, `NEURAL_OPS`/`LOCAL_OPS` in `tools-onv-planning.py`, an executor branch, and the op keyword rules used to build the catalog | Keep `ALL_OPS` in sync |
| Add a preset | `asset-preset-recipes.json` → `presets` | Any key is accepted by `--preset` / `spec.preset` |
| Tighten/relax QA | `asset-validation-policy.json` → `checks`, `severity`, `fail_mode` | No code change required |
| Change what needs approval | `asset-validation-policy.json` → `approvals.require_for` | `backend_dispatch`, `package`, `overwrite`, `ledger_write` |
| Add a disclosure variant | `asset-disclosure-templates.json` and `disclosure.default_template` in the governance policy | `{placeholder}` fields are documented in §7/§14 |
| Add a new command | `tools-onv.py` (`build_parser`, `COMMAND_LIST`, `HELP_TEXT`, handler registry) | Keep the JSON envelope and exit codes |
| Add a regression case | `tools-onv-selftest.py` → `run_selftest()` | Use `harness.run_cli(...)` and `harness.expect(...)` |

Maintenance notes:

* Re-extract the catalog asset if the source catalog edition changes; keep `catalog_version` in
  sync and confirm `concept_count` is 1235 for edition 1.0.
* Bump `TOOL_VERSION` in `tools-onv-common.py` (single source of truth) on behaviour changes; it
  flows into plans, provenance and packages.
* `asset-capability-catalog.json` is generated data: 28 category objects with `number`, `slug`,
  `title`, `concept_count`, `default_op` and `concepts[]` (`id`, `name`, `slug`, `op`,
  `description`). Duplicate concept names across categories get a `-cNN` suffix so slugs stay
  unique.
* Retention knobs live in `asset-governance-policy.json` → `retention`; the tool does not delete
  old logs automatically — schedule that yourself if required.

---

## 16. Package compatibility notes

* This folder contains **no `SKILL.md`**, no subfolders, no archives, and no unprefixed files.
* All 16 files sit directly inside `offline-neural-video-pipeline/`; maximum path depth from a
  future skill root is two levels (`<skill-root>/offline-neural-video-pipeline/<file>`).
* Every internal reference uses a prefixed sibling filename resolved relative to the tool folder —
  no absolute paths, no parent traversal, no generated URLs.
* The tool works when copied beside a parent `SKILL.md`; run it from the skill root with
  `python3 offline-neural-video-pipeline/tools-onv.py …` (verified from a clean temporary skill
  root containing one test `SKILL.md`).
* Any external failure (missing ffmpeg, unavailable weights, unreadable network share) is a host or
  platform condition, not something the package can prevent; the tool reports it in structured
  form instead of failing silently.
