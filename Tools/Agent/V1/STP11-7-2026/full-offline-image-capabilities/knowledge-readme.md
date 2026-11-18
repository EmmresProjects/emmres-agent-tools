# full-offline-image-capabilities — knowledge and operations guide

A single, reusable Skill Tool component for air-gapped (fully offline) image
work. It covers environment readiness, model and asset integrity, security
screening, rights auditing, image inspection, automated quality validation,
deterministic image operations (convert, mask, canvas), reproducible
generation planning, prompt systems, batch control, packaging, recovery and
governance.

It is built from the *Full Offline Image Capabilities* catalog (32 categories,
480 proposed skills). The whole catalog is retained as data: every one of the
480 skills is mapped to one of the 19 implemented commands, so a caller can
ask "which capability covers skill X?" and get a concrete answer.

This folder is a Skill Tool component. It contains no `SKILL.md` and is meant
to be copied beside a parent package's root `SKILL.md` without modification.

---

## 1. Purpose and capability list

| Command | What it does | Source catalog categories served |
| --- | --- | --- |
| `capabilities` | Lists the 19 commands and maps the 32-category / 480-skill source catalog, including a per-category breakdown. | 32 (governance) |
| `doctor` | Environment readiness: python/package versions, GPU probe, disk, model dir, wheelhouse, no-download policy, diffusion-runtime availability. | 1, 2, 22 |
| `inspect` | Decodes an image and reports format, dimensions, mode/channels, alpha, aspect, bit depth, ICC/EXIF, DPI, size, SHA-256, dHash/aHash, pixel statistics. | 24, 25 |
| `validate` | Runs 12 QC checks per image (decode, dimensions, aspect, file size, empty, alpha, artifacts, colour, text region, bit depth, metadata, duplicate) with configurable severity and writes JSON + Markdown reports. | 25, 29 |
| `compare` | Visual regression comparator: dHash/aHash Hamming distance, mean absolute difference, changed-pixel ratio, verdict. | 29 |
| `convert` | Exports PNG/JPEG/WebP/TIFF with quality, resize/scale, metadata stripping, alpha flattening over a chosen background. | 24, 17 |
| `mask` | Builds masks from alpha/luma/threshold with invert, box-blur feather, island removal and hole filling; reports coverage and component count. | 9 |
| `canvas` | Expands a canvas for outpainting (edge or colour fill) and emits the matching inpaint mask. | 7 |
| `manifest` | Builds and verifies SHA-256 integrity manifests for model/asset trees; reports missing, mismatched and extra files. | 3, 4, 31 |
| `screen` | Screens files for pickle and executable payload risk, SafeTensors enforcement, archive bombs, path traversal, symlinks in archives, nested archives and embedded secrets; optional quarantine. | 27 |
| `license-audit` | Detects licences from sidecars, JSON fields and SafeTensors metadata; classifies allow/review/deny, computes redistribution rights and the release-rights gate; builds attribution notices. | 26 |
| `quarantine` | Lists quarantined files and restores them behind an approval gate. | 27 |
| `plan` | Builds a schema-validated, reproducible job spec for `txt2img`, `img2img`, `inpaint`, `outpaint`, `upscale`, `controlnet`, `lora`, `remove-bg`, `restore-face`, including a VRAM-aware memory plan, local-weight blockers and an optional standalone runner script. | 5–23, 30 |
| `prompt` | Compiles prompt templates with variables and defaults, merges negative-prompt libraries, converts weighting styles, estimates token budget and flags prompt-injection patterns. | 28 |
| `batch` | Builds job queues from a seed/resolution/model/LoRA matrix, runs them with retries, timeouts, resume state and per-job outcomes; reports weight-dependent jobs as blocked instead of faking them. | 21 |
| `package` | Builds a handoff ZIP with checksum manifest, version manifest, licence manifest, NOTICES, INSTALL.md, SELFTEST.md and optional config/preset copies. No nested archives. | 31 |
| `recover` | Salvages truncated PNGs scanline-by-scanline, rebuilds manifests and restores quarantined files. | 32, 23 |
| `cleanup` | Removes bounded tool temp artifacts (`*.foic-tmp`, `foic-tmp-*`, `.foic-cache`); dry run unless `--approve`. | 23 |
| `selftest` | Generates synthetic fixtures and runs the full offline test suite through the real CLI. | 31, 32 |

Every command supports `--help`. Every command is network-free by design.

---

## 2. Setup and dependencies

* **Required:** Python 3.9 or newer. No third-party package is required.
* **Optional:** `Pillow` (JPEG/WebP/TIFF pixel access and export), `NumPy`
  (faster analytics; the current release does not import it). See
  `tools-requirements.txt` for pinned ranges.
* **Optional execution stack** (only to *run* emitted plans): `torch`,
  `diffusers`, `transformers`, `safetensors`, `accelerate`, `onnxruntime`,
  `realesrgan`, `gfpgan`. None of these are needed for any command here.
* **Install (offline, from a staged wheelhouse):**

```sh
python3 -m pip install --no-index --find-links ./wheelhouse -r full-offline-image-capabilities/tools-requirements.txt
```

* **Image backend:** `runtime.image_backend=auto` uses Pillow when it is
  importable and otherwise falls back to bundled pure-standard-library codecs
  (full PNG decode/encode, plus JPEG/WebP/BMP/GIF/TIFF header parsing for
  dimensions and metadata). With `auto` the two backends are interchangeable
  for PNG; pass `--set runtime.image_backend=stdlib` to force the dependency
  free path and `pillow` to require Pillow.

### Exact run commands

```sh
# from the future skill root (recommended, all paths relative)
python3 full-offline-image-capabilities/tools-image-capabilities.py --help
python3 full-offline-image-capabilities/tools-image-capabilities.py capabilities --catalog
python3 full-offline-image-capabilities/tools-image-capabilities.py doctor
python3 full-offline-image-capabilities/tools-image-capabilities.py selftest --out ./selftest-out

# or from inside the Skill Tool folder
cd full-offline-image-capabilities
python3 tools-image-capabilities.py validate ../images --preset photo --report qc.md
```

Exit codes: `0` success, `1` operational error, `2` usage error, `3` approval
required, `4` timeout, `5` validation/QC gate or self-test failure.

---

## 3. File map and loading order

All files sit directly inside `full-offline-image-capabilities/`.

| File | Role |
| --- | --- |
| `tools-image-capabilities.py` | **Primary entry point.** CLI, subcommand dispatch, global flags, output rendering, exit codes. |
| `tools-image-core.py` | Shared runtime: config loading and merging, logging, path-safety guard, SHA-256, JSON I/O, JSON-Schema subset validator, deadline/retry/approval primitives, image decode/encode (Pillow + stdlib codecs), pixel analytics (dHash/aHash, statistics). Every other module loads this first. |
| `tools-image-qc.py` | QC policy loading, `inspect`, the 12 check functions, `validate`, perceptual comparison, Markdown/JSON report builder. |
| `tools-image-security.py` | Component classification, manifest build/verify, file and tree screening, SafeTensors header parsing, quarantine/restore, licence and rights audit. |
| `tools-image-ops.py` | Pixel operations: convert, alpha compositing/flatten, resize, mask build and cleanup, canvas expansion. |
| `tools-image-pipeline.py` | Presets and recipes, runtime/hardware probing (`doctor`), prompt compiler, job-spec builder, memory planner, local-weight resolution, provenance, runner-script emitter, batch build/run. |
| `tools-image-recover.py` | PNG salvage, manifest rebuild, quarantine restore, bounded cleanup. |
| `tools-image-selftest.py` | Fixture generation and the offline test suite invoked by `selftest`. |
| `tools-requirements.txt` | Dependency declaration and offline install command. |
| `asset-default-config.json` | Default runtime configuration (paths, limits, timeouts, retries, security, logging). |
| `asset-qc-policy.json` | QC defaults, thresholds, per-check severity and presets (`photo`, `product-cutout`, `upscale-output`, `typography`, `thumbnail`). |
| `asset-qc-schema.json` | Contract for the QC report produced by `validate`. |
| `asset-plan-schema.json` | Contract for the job spec produced by `plan`. |
| `asset-pipeline-presets.json` | Model presets (SDXL/SD1.5/FLUX/SD3), samplers, the nine operation presets, memory-fallback ladder, control types, output defaults. |
| `asset-prompt-recipes.json` | Prompt templates, negative-prompt libraries, weighting styles, token estimate, injection patterns. |
| `asset-model-catalog.json` | Model component registry, component kinds, compatibility matrix, format policy. Checksums are `null` by design and are filled from a verified local manifest. |
| `asset-capability-catalog.json` | The extracted source catalog: 32 categories, 480 skills, each mapped to a command with an `offline-executable` or `requires-local-weights` mode. |
| `asset-license-policy.json` | Licence classification, redistribution rules, attribution templates, detection hints. |
| `asset-report-template.md` | Markdown template used by the QC report builder. |
| `knowledge-readme.md` | This file (the only knowledge file). |

Because packaged filenames use hyphens, modules load each other through
`core.load_sibling()` / the equivalent inline loader in the entry point.
Execution order is always: entry point → `core` → the module that implements
the command → assets read on demand by relative name from `core.TOOL_DIR`.

---

## 4. Inputs, outputs and configuration

**Input handling.** Commands accept individual file paths, repeated paths,
directories (for `validate`, `screen`, `manifest`, `package`,
`license-audit`), a `--root` directory, or a JSON spec/queue file. HTML tags,
numbering marks and stray spacing are irrelevant here because inputs are
filesystem paths, but the catalog asset retains the original skill names
exactly as published.

**Structured output.** Every command prints JSON when `--json` is passed and
writes it to `--json-out PATH`. Without `--json`, commands print a
human-readable summary; `validate` can additionally write a Markdown report
with `--report`. All result envelopes use `schema: foic.result/1` style
identifiers and are validated against `asset-qc-schema.json` (QC reports) or
`asset-plan-schema.json` (job specs) at runtime — the validation outcome is
included in the output (`schema_validation`).

**Configuration.** `asset-default-config.json` is merged with an optional
`--config PATH`, then with `--set key=value` overrides using dotted paths:

```sh
python3 tools-image-capabilities.py --set runtime.vram_gb=12 \
  --set paths.model_dir=./models --set runtime.image_backend=stdlib doctor
```

Key groups: `paths.*` (output/cache/model/wheelhouse/quarantine),
`runtime.*` (backend, network `deny`, device, vram/ram budget, workers,
`timeout_seconds`, `max_file_mb`, `max_pixels`, `max_batch_jobs`, retry),
`output.*` (format, indent, provenance, overwrite), `security.*` (pickle and
blocked extension lists, archive limits, secret scan, approval list),
`logging.*`. The config fingerprint appears in every report so a result can be
tied to the exact settings that produced it.

**QC policy.** `asset-qc-policy.json` holds defaults, thresholds, severities
and presets. Override any value per run:

```sh
python3 tools-image-capabilities.py validate ./out --preset photo \
  --policy-set min_width=2048 --policy-set thresholds.clipped_pixel_ratio_max=0.005
```

**Job spec parameters.** `plan` merges, in order: operation defaults → preset
file → `--set` overrides. Sampler selection also fixes the scheduler and
clamps steps to the sampler minimum. `parameters` carries seed, steps,
guidance, sampler, scheduler, width, height, batch size, plus kind-specific
keys (denoise strength, mask blur, scale, tile size, control weight, …).

---

## 5. Capability examples

### capabilities

```sh
python3 tools-image-capabilities.py capabilities
python3 tools-image-capabilities.py capabilities --catalog
python3 tools-image-capabilities.py capabilities --catalog --category 25
```

### doctor

```sh
python3 tools-image-capabilities.py doctor --json
```

Reports python/package versions, `nvidia-smi` presence, torch CUDA, disk,
model directory contents, wheelhouse presence, the no-download policy and
whether a diffusion runtime exists. When torch/diffusers are missing it says
so explicitly and marks generation execution blocked.

### inspect

```sh
python3 tools-image-capabilities.py inspect ./images/a.png ./images/b.jpg --json
```

### validate

```sh
python3 tools-image-capabilities.py validate ./images --preset product-cutout \
  --report qc.md --fail-on warn --json-out qc.json
```

`--fail-on` controls the gate: `warn` (default) exits 5 on warn or fail,
`fail` exits 5 only on fail, `pass` exits 5 on anything. `--check` runs a
subset, e.g. `--check decode,dimensions,alpha`.

### compare

```sh
python3 tools-image-capabilities.py compare before.png after.png --tolerance 8
```

Verdicts: `identical-structure`, `within-tolerance`, `similar` (≤ 12),
`different`.

### convert

```sh
python3 tools-image-capabilities.py convert in.png out.jpg --format jpeg --quality 88 \
  --strip-metadata --max-width 2048
python3 tools-image-capabilities.py convert cutout.png flat.png --flatten-alpha --background "#ffffff"
```

### mask

```sh
python3 tools-image-capabilities.py mask cutout.png mask.png --mode alpha \
  --feather 6 --min-island-px 400 --fill-holes 2500
```

### canvas

```sh
python3 tools-image-capabilities.py canvas photo.png expanded.png \
  --left 256 --right 256 --fill edge --mask-out expanded-mask.png
```

### manifest

```sh
python3 tools-image-capabilities.py manifest build --root ./models --out manifest.json
python3 tools-image-capabilities.py manifest verify --root ./models --manifest manifest.json
```

### screen and quarantine

```sh
python3 tools-image-capabilities.py screen --root ./incoming --json
python3 tools-image-capabilities.py screen --root ./incoming --quarantine --approve
python3 tools-image-capabilities.py quarantine list
python3 tools-image-capabilities.py quarantine restore --record quarantine/records/bad.ckpt.<stamp>.json --approve
```

Detections: raw-pickle and zip-wrapped torch checkpoints, ELF/PE payloads,
blocked extensions, SafeTensors header validation, format/extension mismatch,
archive bombs (uncompressed size and ratio), entry-count limits, path
traversal and symlink entries, nested archives, and secrets (AWS keys,
OpenAI-style keys, generic API keys/tokens, private-key blocks, bearer
tokens, credentialed URLs).

### license-audit

```sh
python3 tools-image-capabilities.py license-audit --root ./models --json
```

Evidence is read from sidecar licence files, JSON licence fields and
SafeTensors `__metadata__`. Classification is allow/review/deny;
redistribution is permit / permit-with-terms / restricted / block; any
restricted or blocked component sets the release-rights gate to `blocked`.

### plan

```sh
python3 tools-image-capabilities.py plan --kind txt2img --model sdxl-base \
  --prompt "a mountain lake at dawn" --negative "blurry, watermark" \
  --seed 1234 --width 1024 --height 1024 --set steps=34 --set guidance=7.5 \
  --out job.json --prov-out job.prov.json --emit-script runner.py
```

The spec is validated against `asset-plan-schema.json`, includes a VRAM-aware
memory plan with fallback list and notes, records provenance (tool version,
config fingerprint, preset provenance), and lists local-weight/runtime
blockers. `status` is `ready-to-run-locally` only when every required
component is found under `paths.model_dir` and a runtime exists; otherwise it
is `blocked` with the exact reasons. `--emit-script` writes a standalone
runner — **it is never executed by this tool**, and its first action is to
refuse to continue if the checkpoint is not already on disk.

### prompt

```sh
python3 tools-image-capabilities.py prompt --template product-shot \
  --set subject="a red ceramic mug" --set background="seamless light gray" \
  --weight-style a1111 --model sdxl-base
```

Returns the rendered prompt, the merged negative library, the heuristic token
estimate against the model's tokenizer limit, and injection flags.

### batch

```sh
python3 tools-image-capabilities.py batch build --plan job.json \
  --matrix '{"seeds":[1,2,3],"resolutions":[[1024,1024],[896,1152]],"models":["sdxl-base"]}' \
  --out queue.json --out-dir ./out
python3 tools-image-capabilities.py batch run --queue queue.json --out-dir ./out --resume
python3 tools-image-capabilities.py batch status --state ./out/batch-state.json
```

A queue job is `{"job_id": ..., "op": ..., "params": {...}}`. Offline ops
(`inspect`, `validate`, `convert`, `mask`, `canvas`, `screen`, `manifest`)
execute with retries and a shared deadline. Weight-dependent ops
(`generate`, `upscale-model`, `segment`, `restore-face-model`) are recorded
as `blocked` with reasons and the plan is preserved so the queue can be
replayed where weights exist. `--resume` skips jobs already recorded in the
state file.

### package

```sh
python3 tools-image-capabilities.py package --root ./models --out handoff.zip \
  --include config,presets,licenses --max-total-mb 40960
```

Produces `<name>-offline-package/` containing `content/`, `manifest/`
(checksums, version, licences), `NOTICES.md`, `INSTALL.md`, `SELFTEST.md`
and optional `config/`. No nested archives are written.

### recover

```sh
python3 tools-image-capabilities.py recover png --input truncated.png --output salvaged.png
python3 tools-image-capabilities.py recover manifest --root ./models --out manifest.json --overwrite
python3 tools-image-capabilities.py recover restore --record quarantine/records/x.json --approve
```

Salvage decompresses as many scanlines as zlib can produce, re-encodes a
valid PNG and pads the remainder with black rows so missing data is visible;
the report states rows recovered, rows padded and the recovery ratio. The
damaged file is never modified.

### cleanup

```sh
python3 tools-image-capabilities.py cleanup --dir ./work --older-than 24
python3 tools-image-capabilities.py cleanup --dir ./work --approve
```

### selftest

```sh
python3 tools-image-capabilities.py selftest --out ./selftest-out
```

---

## 6. Safety, approval and limits

* **No network.** `runtime.network` is `deny`. Nothing in the package opens a
  socket. A missing local component fails loudly instead of downloading.
* **Approval gate.** `security.require_approval_for` lists `quarantine`,
  `delete`, `restore` and `overwrite`. Attempting one without `--approve`
  exits `3` with a descriptive message. `cleanup` degrades to a dry run
  without `--approve`.
* **Path safety.** Archive entry names are checked for traversal and
  symlinks; `core.safe_join` rejects absolute paths, `..`, drive letters and
  escapes from the intended root.
* **No arbitrary execution.** The runner-script emitter writes text; it never
  runs it. No command shells out except `doctor`, which runs the read-only
  `nvidia-smi --query-gpu` probe with a 10-second timeout when `nvidia-smi`
  exists, and the self-test, which runs its own CLI as a subprocess.
* **Bounded work.** `runtime.timeout_seconds` (default 300) drives a shared
  `Deadline` checked inside every long loop; `runtime.max_file_mb` (2048),
  `runtime.max_pixels` (64 MP), `runtime.max_batch_jobs` (5000) and archive
  limits bound resource use. Retries default to 2 attempts with backoff.
* **Non-destructive recovery.** Salvage and rebuild write new files; only
  `cleanup --approve` and quarantine moves change the working tree, and both
  keep a record.
* **Secrets.** No credentials are embedded. The scanner looks for leaked
  secrets in *inputs* and reports them.

---

## 7. Validation actually completed

Executed against this build on CPython 3.13.16 (Linux), scratch directories
outside the package, no network access:

1. `capabilities` (list, catalog, single category) — 5 checks.
2. `selftest` **with Pillow 11 available**: **103/103 checks passed, exit 0**.
3. `selftest` **with Pillow blocked** (`PYTHONPATH` shim raising on
   `import PIL`, forcing the pure-standard-library codec path):
   **102/102 checks passed, exit 0** (the JPEG-export check is skipped and
   reported as skipped when Pillow is absent).
4. Test coverage inside the suite, all passing in both backends:
   unit checks (path traversal rejected, schema validator positive and
   negative, config override, deadline expiry, approval gate open and
   closed); `inspect` valid/missing/multi-input and forced stdlib backend;
   `validate` pass, fail-with-exit-5, `--fail-on` semantics, Markdown report,
   preset application, check subset, duplicate detection, policy override;
   `compare` identical and different; `convert` round trip, resize, alpha
   flatten, JPEG export; `mask` alpha coverage ≈ 0.25, feather, island
   removal, invert ≈ 0.75; `canvas` 1024→1280 expansion with mask;
   `manifest` build, clean verify, drift after modification, missing file,
   overwrite refusal; `screen` pickle flagged, SafeTensors accepted, secret
   detected, clean file passes, zip traversal, 60 MB archive-bomb ratio,
   entry-count limit, quarantine; `license-audit` detection and blocked
   non-commercial gate; `plan` schema validity, seed in job id, blocked
   status with blockers, memory plan, emitted runner compiles with
   `py_compile`, 8 GB VRAM fallbacks, upscale plan, unknown model rejected;
   `prompt` rendering, negative merge, missing variable rejection, injection
   flag; `batch` queue build (6 jobs), blocked reporting, offline ops
   completing, failed job recorded, resume, status; `recover` truncated-PNG
   salvage (partial, rows recovered, decodes at 256×256), manifest rebuild,
   quarantine restore; `cleanup` dry run, non-temp files kept, deletion with
   `--approve`; `package` build, no nested archives, manifest/install/
   selftest/notices present, overwrite refusal; error handling (QC gate exit
   5, recovery re-run, missing config exit 1, invalid config exit 1, unknown
   command exit 2).
5. Artefacts inspected after the run: the Markdown QC report renders the
   summary table and per-image check tables; the job spec validates against
   `asset-plan-schema.json`; the emitted runner script passes `py_compile`;
   the handoff ZIP lists `content/`, `manifest/`, `config/`,
   `NOTICES.md`, `INSTALL.md`, `SELFTEST.md`.
6. Reference and portability checks: the folder was copied into a clean
   temporary skill root beside a test `SKILL.md` and re-run using relative
   paths only; the final ZIP was extracted into a clean directory and the
   primary entry point was executed again from the extracted copy.

---

## 8. Limits and unavailable integrations

* **No diffusion execution.** There are no model weights and no
  `torch`/`diffusers` in this environment, and the design forbids fetching
  either. Generation, model upscaling, segmentation and face restoration are
  delivered as validated, reproducible job specs (plus optional runner
  scripts) and are reported `blocked` with the exact missing components. The
  claim "generates images" is **not** made for this package.
* **Heuristic checks, not detectors.** Face, hand, identity-consistency and
  text-spelling quality checks from the catalog are not implemented as
  detectors: no face/hand/OCR models are bundled. What is implemented is a
  text-region heuristic based on edge density (usable as a typography/legibility
  signal, not as spelling verification), duplicate/near-duplicate detection
  via dHash, and structural artifact metrics (flatness, clipping, banding,
  noise). Treat them as triage that routes images to human review.
* **Colour management.** ICC profiles are detected, not converted; there is
  no colour-management engine. Display P3, CMYK and OpenEXR conversion are
  planning-level only.
* **Metadata.** EXIF/ICC/DPI presence is detected and EXIF can be stripped on
  export; GPS coordinates are not parsed or redacted individually.
* **GPU.** `doctor` reports hardware via `nvidia-smi`/torch when present;
  there is no GPU scheduler, benchmark runner or thermal test. The memory
  planner is budget-driven from `runtime.vram_gb` and is honest when the
  budget is unknown.
* **Catalog checksums.** `asset-model-catalog.json` deliberately ships
  `sha256: null`. Checksums come from a manifest you build and verify locally;
  none are invented.
* **Streaming/large files.** PNG decode is whole-file; files above
  `runtime.max_file_mb` are rejected rather than streamed.

---

## 9. Troubleshooting and recovery

| Symptom | Cause and fix |
| --- | --- |
| `ERROR [not-found]` | Path is wrong or has no read permission; remember paths resolve against the current working directory. |
| `ERROR [approval-required]` | Gated action; re-run with `--approve` after reviewing the dry-run output. |
| `ERROR [would-overwrite]` | Output exists; add `--overwrite`, or write elsewhere. |
| `ERROR [unsupported-image]` | Format not recognised, or non-PNG pixels requested without Pillow; install Pillow or convert to PNG. |
| `ERROR [backend-unavailable]` | `--set runtime.image_backend=pillow` used without Pillow, or exporting JPEG/WebP/TIFF while Pillow is absent. Use `auto` for PNG-only stdlib output. |
| `ERROR [qc-failed]` (exit 5) | The gate fired. Inspect the report, then relax the specific threshold with `--policy-set`, or fix the image. |
| `ERROR [invalid-spec]` | A `--set` override produced an out-of-range job spec; the message lists the offending paths. |
| Decode reports `truncated` | Run `recover png --input ... --output ...`. |
| Manifest `drift` | Rebuild after confirming the change was intentional: `recover manifest --root ... --out ... --overwrite`. |
| Quarantined a file by mistake | `quarantine list`, then `quarantine restore --record <path> --approve`. Restore refuses to clobber an existing path. |
| Everything slow | Lower `thresholds.sample_side`, run `--check` subsets, or install Pillow/NumPy. |

**Recovery model.** Every long operation writes through `atomic_write_*`
(temp file plus `os.replace`), so a crash never leaves a half-written output;
stray temp files are `*.foic-tmp` and are what `cleanup` removes. Batch state
is stored after every job so `--resume` continues without redoing work.
Manifests make silent corruption visible rather than tolerable.

---

## 10. Extension and maintenance

* **Add a QC check:** write `def check_x(ctx)` in `tools-image-qc.py`,
  append it to `CHECKS`, add its severity to `severity` in
  `asset-qc-policy.json` and any threshold to `thresholds`. It is then
  reachable via `--check x` and appears in reports automatically.
* **Add a model or operation:** extend `models`, `samplers` or `operations`
  in `asset-pipeline-presets.json` and register components plus a
  compatibility row in `asset-model-catalog.json`. No code change is needed
  for a new checkpoint, sampler or operation default.
* **Add a prompt recipe:** extend `templates` or `negative_libraries` in
  `asset-prompt-recipes.json`.
* **Wire real execution:** keep the emitted runner script as the boundary.
  Extend `RUNNER_TEMPLATE` in `tools-image-pipeline.py` with site-specific
  branches (local Real-ESRGAN, SAM, GFPGAN, ONNX sessions); the tool still
  never executes it.
* **Tune policy:** copy `asset-default-config.json` or
  `asset-qc-policy.json`, edit, and pass `--config`/`--policy`. Packaged
  assets stay pristine and the config fingerprint in each report records
  which settings were used.
* **Re-validate after any change:** run `selftest` in both backends
  (`auto`/Pillow and `--set runtime.image_backend=stdlib`) before shipping.
