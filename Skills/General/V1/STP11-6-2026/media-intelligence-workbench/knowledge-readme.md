# knowledge-readme.md — Media Intelligence Workbench

One Skill Tool folder that turns the 25-category / 250-capability *Media Intelligence
Skill Catalog* into a working, reusable component: it parses capability requests,
resolves them against the catalog, emits an evidence-first work plan, and executes the
measurement, validation and governance steps that can be performed honestly with a
plain Python runtime.

This folder is a **Skill Tool component, not a complete Copilot skill**. It contains no
`SKILL.md` and must be copied beside a parent package's root `SKILL.md`.

---

## 1. Purpose

Agents that "understand media" fail in predictable ways: they summarise transcripts and
ignore the picture, they assert things the pixels do not support, they lose the link
between a claim and a timestamp, they transform originals in place, and they quietly
depend on models that may not exist at run time.

This Skill Tool encodes the opposite behaviour:

* **Grounded.** Every observation must carry a source file plus a timecode, region or
  byte offset. The built-in validator rejects unanchored observations.
* **Measured, not guessed.** Where a signal can be measured deterministically (sharpness,
  clipping, silence, hum, scene cuts, flash risk, caption reading speed, contrast),
  it *is* measured, in the standard library, with published thresholds.
* **Honest about scope.** Semantic labelling, verbatim transcription and translation
  need models that are not shipped. The tool plans those steps, marks them as external
  dependencies, and never fabricates their output.
* **Non-destructive and gated.** Originals are hashed and left untouched; file-writing
  actions need explicit approval; privacy, forensic, clinical and safety work carries
  mandatory human-review gates.

## 2. Capability list

Eighteen operations are exposed by one primary entry point:

| Operation | What it does |
| --- | --- |
| `help` | Usage, operation list, exit codes. |
| `capabilities` | Operation list, catalog summary, gates, presets, optional dependencies, documented limitations. |
| `parse` | Parse plain text, Markdown or copied HTML into categories + capability names + descriptions. |
| `plan` | Build a unified, evidence-first work plan (units, steps, evidence, gates, pipeline, contract). |
| `probe` | Technical probe: container, codec, dimensions, duration, channels, bit depth, limits. |
| `inspect` | Ingestion safety scan (executable/trailing payload, pixel bomb, zip bomb, bad headers) + sha256 + optional probe. |
| `ingest` | Batch ingest: safety scan, hashes, asset graph (thumbnails/proxies/sidecars/duplicates), checkpointed manifest, resume. |
| `analyze-image` | Sharpness, noise, exposure, contrast, colour cast, banding, 8x8 blocking, palette, perceptual hash, quality score. |
| `analyze-audio` | Peak/RMS/dBFS, clipping, DC offset, crest factor, silence map, mains hum, spectral balance, channel balance/phase, loudness estimate, tempo. |
| `analyze-video` | Container probe plus frame-sequence analysis: scene boundaries, flash risk, motion energy, per-frame hashes/palettes. |
| `subtitle-audit` | Caption timing: reading speed (CPS), min/max duration, overlap, line length, line count, large gaps. |
| `accessibility` | Flash/strobe risk over a frame series and WCAG 2.x contrast ratio checks. |
| `conformance` | Validate a file against a delivery-specification JSON (format, kind, duration, dimensions, channels, rate, size, loudness). |
| `index` | Build a local media index (metadata, hashes, palette, perceptual hash) and query it by text or perceptual similarity. |
| `report` | Assemble a validated evidence-first report (JSON + optional Markdown) from a plan plus measurement outputs. |
| `evaluate` | Validate a report and score evidence coverage, grounding, low-confidence review load. |
| `validate` | Validate a config, plan or report document. |
| `self-test` | Built-in end-to-end self-test in a temporary workspace (19 checks). |

The catalog itself (25 categories, 250 capabilities) is bundled as data and is the source
of every plan unit. See §7.

## 3. Files in this folder

All files sit directly in the folder. No subfolders, no `SKILL.md`.

| File | Prefix role | Purpose |
| --- | --- | --- |
| `tools-miw.py` | tools — **primary entry point** | CLI: argument parsing, operations, output envelope, approval gate, timeouts, checkpoints, report assembly, self-test. |
| `tools-media-io.py` | tools — support module | Format detection from magic bytes, container/stream probing, ingestion safety scan, sha256, asset graph, native PNG/BMP/PNM decoders, WAV decoder, optional Pillow bridge. |
| `tools-metrics.py` | tools — support module | Metric engines: image quality, perceptual hash, frame difference/scene cuts, flash risk, audio levels/silence/hum/spectrum/tempo, grounding math. Runnable on its own for its selftest. |
| `tools-plan.py` | tools — support module | Catalog loading/search, category profiles, plan construction, structural validators for plan and report. Runnable on its own to browse/search the catalog. |
| `tools-intake.py` | tools — support module | Request intake: HTML/Markdown/plain-text stripping, list and heading parsing, wrapping-description repair, umbrella naming. Runnable on its own. |
| `knowledge-readme.md` | knowledge | This document — the only documentation file. |
| `asset-capability-catalog.json` | asset | The 250-capability catalog with ids, descriptions, policy classes and gates. |
| `asset-policy-presets.json` | asset | Gate definitions, three policy presets (draft/standard/conservative), abstention rules. |
| `asset-default-config.json` | asset | Default configuration: paths, limits, runtime, analysis thresholds, provider slots. |
| `asset-evidence-schema.json` | asset | JSON Schema (2020-12) for the plan, report and evidence-row contracts. |
| `asset-report-template.json` | asset | Hand-authoring template for a report that satisfies the schema. |
| `asset-conformance-spec-example.json` | asset | Example delivery specification for `conformance --spec`. |

**Load order:** `tools-miw.py` loads the four support modules by path at import time, in
this order — `tools-media-io.py`, `tools-metrics.py` (which itself loads
`tools-media-io.py`), `tools-plan.py`, `tools-intake.py`. Assets are resolved lazily per
operation from `paths.*` in the config, then from the package folder. Nothing is imported
from outside this folder.

## 4. Requirements and setup

* **Python 3.9+** (developed and tested on CPython 3.13). No third-party package is
  required for the primary path.
* **Optional, materially useful:** `Pillow>=10` — enables JPEG/GIF/TIFF/WEBP decoding,
  interlaced/16-bit PNG fallback, and TIFF probing. Without it those files are reported as
  `unsupported-format` with a hint; PNG, BMP, PNM and WAV are handled natively.
* **Optional:** `numpy` — only detected and reported; no operation requires it.
* **Optional:** `ffmpeg` — enables live frame extraction for `analyze-video`
  (`--frames-dir` is the dependency-free alternative).
* **Optional:** `jsonschema>=4` — only for validating outputs against
  `asset-evidence-schema.json` in your own pipeline. The CLI's own validator is
  standard-library.

```sh
# from the future skill root (the folder that contains SKILL.md and this Skill Tool folder)
python3 --version                      # 3.9+
pip install "Pillow>=10"                # optional image-format support
pip install "jsonschema>=4"             # optional external schema validation
```

No network access, credentials, paid services or model downloads are used by any
operation.

## 5. Exact commands

Run everything from the future **skill root**. Python is named explicitly.

```sh
# help and inventory
python3 media-intelligence-workbench/tools-miw.py help
python3 media-intelligence-workbench/tools-miw.py capabilities
python3 media-intelligence-workbench/tools-miw.py capabilities --categories

# parse a request (text, Markdown or copied HTML)
python3 media-intelligence-workbench/tools-miw.py parse --in request.html --out media-plans/request.json --approve
python3 media-intelligence-workbench/tools-miw.py parse --text "1. Skill Name: X  Description: Y"

# plan
python3 media-intelligence-workbench/tools-miw.py plan --category 15 --out media-plans/privacy.json --format md --approve
python3 media-intelligence-workbench/tools-miw.py plan --text "audio splice detection" --preset conservative
python3 media-intelligence-workbench/tools-miw.py plan --query "caption readability" --max-units 5

# ingest and inspect
python3 media-intelligence-workbench/tools-miw.py probe media-input/clip.mp4
python3 media-intelligence-workbench/tools-miw.py inspect media-input --out media-plans/safety.json --approve
python3 media-intelligence-workbench/tools-miw.py ingest media-input --out media-plans/ingest.json --out-dir media-plans --approve

# analysis
python3 media-intelligence-workbench/tools-miw.py analyze-image media-input/photo.png media-input/scan.jpg
python3 media-intelligence-workbench/tools-miw.py analyze-audio media-input/take.wav --out media-reports/take.json --approve
python3 media-intelligence-workbench/tools-miw.py analyze-video --frames-dir media-input/frames --fps 25
python3 media-intelligence-workbench/tools-miw.py analyze-video media-input/clip.mp4 --fps 1     # needs ffmpeg

# captions, accessibility, conformance
python3 media-intelligence-workbench/tools-miw.py subtitle-audit media-input/ep1.srt --max-cps 17
python3 media-intelligence-workbench/tools-miw.py accessibility --contrast "#777777,#ffffff"
python3 media-intelligence-workbench/tools-miw.py accessibility --frames-dir media-input/frames --fps 25
python3 media-intelligence-workbench/tools-miw.py conformance media-input/clip.mp4 --spec asset-conformance-spec-example.json

# index, report, evaluation, validation
python3 media-intelligence-workbench/tools-miw.py index media-input --out media-work/index.json --query "interview" --approve
python3 media-intelligence-workbench/tools-miw.py report --plan media-plans/privacy.json \
    --measurements media-reports/img.json media-reports/aud.json \
    --title "Episode 4 QC" --out media-reports/report.json --md media-reports/report.md --approve
python3 media-intelligence-workbench/tools-miw.py evaluate --report media-reports/report.json --plan media-plans/privacy.json
python3 media-intelligence-workbench/tools-miw.py validate --report media-reports/report.json --plan media-plans/privacy.json

# self-test
python3 media-intelligence-workbench/tools-miw.py self-test
python3 media-intelligence-workbench/tools-metrics.py --selftest
```

Support modules are independently runnable:

```sh
python3 media-intelligence-workbench/tools-plan.py --catalog media-intelligence-workbench/asset-capability-catalog.json --list-categories
python3 media-intelligence-workbench/tools-plan.py --catalog media-intelligence-workbench/asset-capability-catalog.json --search "splice"
python3 media-intelligence-workbench/tools-intake.py --in request.html
```

## 6. Operations reference

Every operation prints one JSON envelope to stdout and an optional human summary to
stderr:

```json
{"ok": true, "tool": "media-intelligence-workbench", "version": "1.0.0",
 "operation": "...", "generated_at": "...", "data": {...},
 "warnings": [], "errors": [], "elapsed_s": 0.12}
```

When writing a primary artifact with `--out`, stdout becomes a short confirmation
(`ok`, `operation`, `out`, `errors`, `warnings`) so a pipeline can consume it safely.

**Common options:** `--config PATH`, `--out PATH`, `--approve`, `--quiet`. They may be
placed before or after the operation name (`--config` and `--approve` before the operation
are inherited by the subcommand).

`--text STRING` and `--in FILE` accept plain text, Markdown or copied HTML. HTML tags,
`class`/`data-*` attributes, CSS/JS blocks, numbering, bullets, entities and extra spacing
are removed before parsing.

### 6.1 `parse`

```sh
python3 media-intelligence-workbench/tools-miw.py parse --in request.html --out media-plans/request.json --approve
```

Recognised shapes: `N. Skill Name: …` + `Description: …`; `Name — description`;
`**Name** — description`; `Name | description`; Markdown/HTML table rows; headings as
categories; `N proposed skills` count lines are ignored; wrapped description lines are
re-joined. Output: `{umbrella, slug, category_count, skill_count, categories[], skills[]}`.

### 6.2 `plan`

```sh
python3 media-intelligence-workbench/tools-miw.py plan --category 9 --out media-plans/qc.json --format md --approve
python3 media-intelligence-workbench/tools-miw.py plan --in request.html --preset conservative
```

Selectors: `--in/--text` (capability request), `--category` (`9`, `cat-09`, name or name
fragment), `--query` (keyword search). Free-form text with no list markers falls back to
catalog keyword search and says so in `warnings`.

Each plan carries: request echo, umbrella name, policy preset and thresholds, matched
catalog entries, **units** (`unit_id`, `skill_id`, `responsibility`, `accepted_inputs`,
`analysis_steps`, `required_evidence`, `produces`, `local_commands`,
`external_dependency`, `confidence_policy`, `gates`, `runtime`), the seven-stage
`pipeline` (ingest → probe → measure → ground → interpret → review → report), the gate
list, abstention rules, the output contract and the next commands to run.
`--format md` also writes a Markdown sibling of `--out`.

### 6.3 `probe`

```sh
python3 media-intelligence-workbench/tools-miw.py probe media-input/clip.mp4 media-input/take.wav
```

Reports `detected` (format, kind, container, confidence) and `technical`. Native probing
covers PNG/GIF/BMP/PNM/JPEG/WEBP/TIFF headers, WAV (codec, channels, rate, depth,
duration, truncation), ISO-BMFF (mvhd duration, track handlers, codecs, tkhd dimensions,
audio rate/channels) and MKV/WebM duration. Ogg/MP3/FLAC are partial by design and say so.

### 6.4 `inspect`

```sh
python3 media-intelligence-workbench/tools-miw.py inspect media-input --deep
```

Per file: `sha256`, `size_bytes`, and a `safety` verdict of `pass` / `review` / `block`.
Checks: executable header at byte 0, appended ZIP/RAR/PE/ELF payloads, PDF
JavaScript/Launch/OpenAction, Office macro markers in archives, ZIP expansion ratio and
entry count, declared pixel count against `limits.max_pixels`, zero-dimension images,
missing `moov`, `moov` after `mdat`, empty files. Short signatures are only trusted at
credible offsets, so compressed images do not trigger false payload findings. A `block`
verdict exits with code 3.

### 6.5 `ingest`

```sh
python3 media-intelligence-workbench/tools-miw.py ingest media-input --out media-plans/ingest.json --approve
python3 media-intelligence-workbench/tools-miw.py ingest media-input --out media-plans/ingest.json --approve   # resumes
```

Writes `ingestion-manifest.json` (per-file hash, safety verdict, format, technical probe,
attempts, failures) and `checkpoints.json`. Re-running with `--resume` (default) skips
files whose sha256 and status are unchanged. Retries follow `runtime.retries` with
`runtime.retry_backoff_s` backoff. The manifest also contains an **asset graph**: node
roles (original / thumbnail / proxy / sidecar), stem links and sha256 duplicate groups.
`--no-resume` forces a full pass.

### 6.6 `analyze-image`

```sh
python3 media-intelligence-workbench/tools-miw.py analyze-image media-input/photo.png
```

Per file: technical info, `quality` (sharpness — Laplacian variance + Tenengrad; noise —
Immerkaer residual with an edge-vs-noise guard; exposure — mean/median/percentiles and
clip percentages; contrast — stdev and percentiles; colour cast — channel deviation;
banding — histogram gaps and isolated peaks; blocking — 8×8 boundary energy ratio),
`palette` (4-bit RGB buckets with coverage), `perceptual_hash` (`ahash` + `dhash`), and a
0–100 `quality_score` with an explicit `issues` list. Large images are box-downsampled to
`runtime.analysis_max_side_px` and the scale factor is reported.

### 6.7 `analyze-audio`

```sh
python3 media-intelligence-workbench/tools-miw.py analyze-audio media-input/take.wav --out media-reports/take.json --approve
```

Per file: `levels` (peak/RMS dBFS, crest factor, clipped samples and percent, DC offset,
verdict), `per_channel_levels`, `silence` (regions and ratio at
`analysis.silence_threshold_dbfs`), `hum` (share of total power in 50/60 Hz harmonics,
verdict), `spectrum` (five band shares, centroid, tilt), `channel_balance` (per-channel
RMS, L−R dB, correlation, phase verdict), `loudness` (approximate K-weighted estimate,
explicitly labelled not BS.1770 certified) and `tempo` (onset-envelope autocorrelation
with confidence). WAV/PCM only — see §10.

### 6.8 `analyze-video`

```sh
python3 media-intelligence-workbench/tools-miw.py analyze-video --frames-dir media-input/frames --fps 25
python3 media-intelligence-workbench/tools-miw.py analyze-video media-input/clip.mp4 --fps 1   # requires ffmpeg
```

Container probe always runs. Frame analysis needs frames: a directory of images (with
`--fps` describing the true cadence) or ffmpeg (bounded by `limits.max_video_frames` and
`runtime.stage_timeout_s`, temp frames always cleaned up). Output: scene boundaries with
timecodes and scores, scene count, flash risk, motion energy, luma series, per-frame
dhashes and palettes. Frames from a directory are labelled "cadence assumed".

### 6.9 `subtitle-audit`

```sh
python3 media-intelligence-workbench/tools-miw.py subtitle-audit media-input/ep1.srt --max-cps 17 --max-line-chars 42
```

Parses SRT and WebVTT. Flags `non-positive-duration`, `too-short`, `too-long`,
`reading-speed`, `line-length`, `too-many-lines`, `overlap`, and lists gaps beyond
`--gap-tolerance`. Verdict `fail` on reading-speed/overlap/non-positive durations;
`review` for other issues.

### 6.10 `accessibility`

```sh
python3 media-intelligence-workbench/tools-miw.py accessibility --contrast "#777777,#ffffff" --contrast "#000000,#ffffff"
python3 media-intelligence-workbench/tools-miw.py accessibility --frames-dir media-input/frames --fps 25
```

Contrast: WCAG relative luminance and ratio, with AA/AAA normal/large thresholds.
Flash: luminance transitions in a sliding window at the real frame cadence with a
conservative three-flashes-per-second criterion — an aid, never a certification.

### 6.11 `conformance`

```sh
python3 media-intelligence-workbench/tools-miw.py conformance media-input/clip.mp4 --spec media-intelligence-workbench/asset-conformance-spec-example.json
python3 media-intelligence-workbench/tools-miw.py conformance media-input/take.wav --spec spec.json --audio-analysis
```

Field checks: `format`, `kind`, `duration_s{min,max}`, `width`, `height`, `channels`,
`sample_rate`, `bit_depth`, `max_size_bytes`, `loudness_lufs{min,max}`. Absent fields are
skipped; unobtainable values are reported as violations with a `note` rather than assumed
compliant.

### 6.12 `index`

```sh
python3 media-intelligence-workbench/tools-miw.py index media-input --out media-work/index.json --query "interview" --similar-to media-input/photo.png --threshold 0.9 --approve
```

Builds an index of metadata, sha256, dimensions, palette, dhash and duration. Text query
scores path/format/kind tokens (metadata index only — semantic embeddings are an external
provider, see §9). `--similar-to` ranks indexed images by Hamming distance with a
similarity threshold. Unreadable files are recorded in `failures`, not fatal.

### 6.13 `report`

```sh
python3 media-intelligence-workbench/tools-miw.py report --plan media-plans/privacy.json \
  --measurements media-reports/img.json media-reports/aud.json media-reports/vid.json \
  --title "Episode 4 QC" --out media-reports/report.json --md media-reports/report.md --approve \
  --interpretation "Clipped tail matches the gain change at 00:01:12." --next-step "Re-record." 
```

Turns measurement JSON into evidence rows with anchors (timecode, region or sha256
offset), separates observations from interpretations, and refuses to write a report that
fails validation (e.g. an observation without an anchor). `--extra-evidence` merges
hand-authored evidence tables; `--limit` adds scope limits; `--md` renders a Markdown
report with the evidence table.

### 6.14 `evaluate`

```sh
python3 media-intelligence-workbench/tools-miw.py evaluate --report media-reports/report.json --plan media-plans/privacy.json
```

Returns `evidence_rows`, `observations`, `interpretations`, `anchored_observations`,
`evidence_coverage` (anchored / observations), `grounding_score` (rows with a source / all
rows), `review_required` (rows below `analysis.confidence_review_threshold`) and a
`pass`/`review`/`fail` verdict. The scoring note is emitted with the numbers so downstream
readers know exactly what was measured. `fail` exits 1.

### 6.15 `validate`

```sh
python3 media-intelligence-workbench/tools-miw.py validate --config-file my-config.json --plan media-plans/p.json --report media-reports/r.json
```

Uses the standard-library validators in `tools-plan.py` (the JSON Schema in
`asset-evidence-schema.json` is the same contract for external validators). `--config-file`
validates a configuration; the global `--config` selects the runtime configuration.

### 6.16 `self-test`

```sh
python3 media-intelligence-workbench/tools-miw.py self-test
```

Creates a temporary workspace, writes a PNG and a WAV natively, and exercises the metric
engine, probe, decode, safety scan, inspect, image/audio analysis, subtitle audit,
contrast check, plan, parse, corrupt-input recovery, approval-gate refusal, missing-input
rejection, wrong-content-type reporting, report assembly and evaluation. Exits 0 only if
every check passes.

## 7. The capability catalog and how requests map to it

`asset-capability-catalog.json` holds all 250 capabilities from the supplied catalog with
stable ids (`mi-<category>-<number>`), the original description, a policy class and the
gates that class implies.

| Category | Name | Skills | Policy class |
| --- | --- | --- | --- |
| cat-01 | Multimodal Intake and Media Routing | 10 | media-safety |
| cat-02 | Image Perception and Scene Understanding | 10 | low-risk |
| cat-03 | Video Watching and Temporal Understanding | 10 | low-risk |
| cat-04 | Audio, Speech, and Acoustic Understanding | 10 | low-risk |
| cat-05 | Music Intelligence | 10 | low-risk |
| cat-06 | Text, Captions, and On-Screen Information | 10 | low-risk |
| cat-07 | Emotion, Behavior, and Interaction Signals | 10 | elevated |
| cat-08 | Spatial, Motion, and Physical Analysis | 10 | elevated |
| cat-09 | Quality Inspection and Technical Diagnostics | 10 | low-risk |
| cat-10 | Accessibility and Inclusive Media | 10 | accessibility |
| cat-11 | Editing, Transformation, and Restoration | 10 | destructive-transform |
| cat-12 | Search, Indexing, and Retrieval | 10 | privacy |
| cat-13 | Summarization, Reporting, and Knowledge Extraction | 10 | low-risk |
| cat-14 | Multi-Source Fusion and Synchronization | 10 | elevated |
| cat-15 | Privacy, Consent, and Sensitive Content | 10 | privacy-high |
| cat-16 | Authenticity, Provenance, and Forensics | 10 | forensic |
| cat-17 | Safety, Security, and Operational Monitoring | 10 | safety-critical |
| cat-18 | Scientific, Medical, and Technical Media | 10 | clinical |
| cat-19 | Education, Training, and Coaching | 10 | education |
| cat-20 | Creative, Editorial, and Production Intelligence | 10 | low-risk |
| cat-21 | Commerce, Marketing, and Customer Media | 10 | commerce |
| cat-22 | Localization, Translation, and Cultural Adaptation | 10 | localization |
| cat-23 | Workflow Automation and Agent Orchestration | 10 | orchestration |
| cat-24 | Data, Annotation, and Model Evaluation | 10 | evaluation |
| cat-25 | Governance, Rights, and Responsible Deployment | 10 | governance |

`tools-plan.py` also holds one **category profile** per category: the inputs it accepts,
the four to five concrete steps a competent implementation takes, the evidence it must
attach and the artifacts it produces. These profiles are why a plan unit is executable
guidance rather than a renamed catalog line — for example cat-04 instructs decoding PCM,
measuring level metrics, mapping silence and hum, and emitting transcript-ready segments,
which is exactly what `analyze-audio` implements locally.

Related capabilities are always combined into **one unified plan**, never split into
separate tools: every unit lands in one plan with one umbrella name, one pipeline, one
gate set and one report contract.

## 8. Policy presets, gates and approvals

`asset-policy-presets.json` defines three presets and sixteen gates.

| Preset | Confidence review / abstain | Human-review categories | Notes |
| --- | --- | --- | --- |
| `draft` | 0.55 / 0.35 | none | Internal exploration; nothing may be called publication-ready. |
| `standard` | 0.70 / 0.45 | cat-16, cat-17, cat-18, cat-25 | Default for research, reporting and review. |
| `conservative` | 0.85 / 0.60 | cat-07, 10, 14, 15, 16, 17, 18, 19, 24, 25 | Regulated or high-impact contexts. |

Gates: `gate-ingestion-safety`, `gate-evidence-check`,
`gate-human-review-on-low-confidence`, `gate-accessibility-review`,
`gate-non-destructive`, `gate-approval-before-write`, `gate-privacy-review`,
`gate-approval-before-publish`, `gate-evidence-preservation`, `gate-forensic-review`,
`gate-no-autonomous-action`, `gate-escalation`, `gate-clinical-review`, `gate-checkpoint`,
`gate-reproducibility`, `gate-audit-trail`.

Approval rules enforced in code:

* Every command that creates or overwrites a file requires `--approve`, or an interactive
  y/N answer, or `policy.approval_mode: "auto"`. Non-interactive runs without approval are
  refused with exit code 3 (`approval_mode: "deny"` always refuses).
* Existing outputs are never overwritten unless `policy.allow_overwrite_outputs` is true;
  writes go through a `.part` file and an atomic rename.
* Blocking safety findings stop analysis of that file (exit code 3 for `inspect`).
* `report` will not write an evidence table containing an observation with no anchor, or
  an interpretation with no stated basis.
* Presets naming a category for human review add `gate-human-review-on-low-confidence`
  to its units automatically; any gate flagged `approval_required` sets
  `requires_human_approval` on the unit and the plan.

## 9. Data contracts

* **Plan** — `asset-evidence-schema.json#/$defs/plan`.
* **Report** — `#/$defs/report`, with `evidence_table` rows matching `#/$defs/evidenceRow`.
* **Evidence row** — `claim`, `source`, `kind` (`observation` | `interpretation`),
  `confidence` (0–1), and an anchor: `timecode` (`hh:mm:ss.mmm`), `start_s`, `region`
  (`[x0,y0,x1,y1]` or an object with absolute and relative boxes) or `offset`
  (byte offset / content-hash prefix). Interpretations must add `basis`.
* **Measurement documents** — the `data` object of `analyze-image`, `analyze-audio` and
  `analyze-video`, consumed directly by `report`.
* **Ingestion manifest** — per-file hashes, safety verdicts, technical probes and the
  asset graph.

Both validators are tested against the same documents: the CLI's standard-library
validator and `jsonschema` with `asset-evidence-schema.json`.

## 10. Limits and unavailable integrations

Stated plainly, because a plan is only useful if its gaps are visible:

1. **No models, no network, no paid APIs.** Semantic scene descriptions, object
   detection, OCR, verbatim transcription, speaker diarization, translation, dubbing and
   embeddings are *planned* (every unit lists them as `external_dependency`) but not
   produced. `providers` in the config are disabled slots; nothing contacts a service.
2. **Video frames need ffmpeg or an exported frame sequence.** The container probe works
   everywhere; frame metrics require `--frames-dir` (cadence assumed from `--fps`) or a
   local `ffmpeg`. Without either, `analyze-video` exits 4 with instructions.
3. **Compressed audio is not decoded.** `analyze-audio` measures WAV/PCM (8/16/24/32-bit).
   MP3/AAC/Opus/FLAC must be converted to WAV first — `probe` reports what you have.
4. **Non-PNG/BMP/PNM images need Pillow.** Without it, `analyze-image` and `index` skip
   those files with `unsupported-format` and a hint.
5. **Partial probes.** MP3/FLAC/Ogg duration and frame rates need a full table walk; the
   stdlib probe reports what it can and flags the gap instead of guessing.
6. **Approximate metrics are labelled.** Loudness is an approximate K-weighting estimate,
   not BS.1770; flash risk is a luminance-transition aid, not an accessibility
   certification; noise/banding/blocking verdicts are calibrated for 640 px-normalised
   luma and must be compared like-for-like.
7. **No live/streaming capture, no archive extraction, no media transcoding.** `ingest`
   inventories archives but never extracts them; transformations are planned as recipes
   with an approval gate, never executed.
8. **Single-machine, single-process.** Parallelism, distributed queues, databases and
   hosted indexes are out of scope; checkpoints make long runs resumable instead.

## 11. Validation actually performed

Everything below was executed against this exact package (CPython 3.13, Linux x86_64),
in a clean skill root containing a stand-in `SKILL.md` beside the folder, using relative
paths only. `jsonschema` and `Pillow` were installed for the optional checks noted.

| Check | Result |
| --- | --- |
| `tools-metrics.py --selftest` (synthetic signals) | **35 checks, 0 failures** |
| `tools-miw.py self-test` (end-to-end) | **19 checks, 19 passed** |
| `help`, `capabilities`, `capabilities --categories` | render; 18 operations, 250 skills, 25 categories |
| `parse` on copied HTML (tags, classes, entities, `<ol>`, nested spans) | 2 categories, 3 capabilities correctly extracted |
| `parse` on PDF-extracted catalog text (wrapped descriptions) | 1 category, 10 capabilities, descriptions re-joined |
| `parse` on Markdown headings + bullets | umbrella and both capabilities extracted |
| `plan --category` (all categories reachable), `--text`, `--in`, `--query`, `--preset`, `--format md`, `--max-units` | plans build, validate and render |
| `plan` free-text fallback | 23 keyword matches with an explicit warning |
| `probe` on MP4 (synthetic ISO-BMFF), WAV, SRT | container/stream details reported |
| `inspect` on zip bomb, trailing-payload polyglot, empty file, renamed ELF, noisy JPEG | `block`, `review`, `block`, `block`, `pass` respectively |
| `analyze-image` on PNG, JPEG (Pillow path), flat image, corrupt file | scores 88/83, decoding recorded, corrupt file reported as a per-file failure |
| `analyze-audio` on WAV with tone + silence + clipping | clipping verdict, 33% clipped, silence ratio 0.333, hum `clean` |
| `analyze-audio` on stereo-imbalance WAV | `imbalance`, 18.1 dB L−R, correlation 1.0 |
| `analyze-audio` on MP4 | clean `unsupported` failure with hint (exit path) |
| `analyze-video --frames-dir` | 10 frames, 7 scenes, flash risk `elevated`, motion `high-motion` |
| `analyze-video` without ffmpeg | exit 4 with install/`--frames-dir` guidance |
| `subtitle-audit` on good SRT, bad SRT, VTT | `pass`, `fail` (`too-short`, `reading-speed`, `line-length`, `overlap`), `pass` |
| `accessibility --contrast` | `#777777/#ffffff` = 4.48 → fail; `#000000/#ffffff` = 21.0 → pass |
| `accessibility --frames-dir` | flash check returns `review` with measured transitions |
| `conformance` with a matching spec and with a deliberately mismatched HD spec; loudness check with `--audio-analysis` | compliant run reports no violations; mismatched run reports `width`, `height`, `duration_s`, `channels` and `loudness_lufs` as expected |
| `index` build, `--query`, `--similar-to` | 23 entries indexed, one unreadable file recorded in `failures`, ranked text matches, similarity 1.0 for the reference image |
| `ingest` twice with resume | 23 completed, 1 unreadable file failed after bounded retries, duplicate groups found, second run reuses checkpoints |
| `report` from three measurement files, with `--md` | 3 evidence rows, all anchored; Markdown renders |
| `evaluate` on that report | coverage 1.0, grounding 1.0, verdict `pass` |
| `validate` on config, plan and report | 0 errors; an unanchored observation is rejected with a precise message |
| `jsonschema` against `asset-evidence-schema.json` | plan VALID, report VALID, template VALID; missing-section and unanchored-observation documents rejected |
| Exit codes | 2 (missing path, unknown category), 3 (approval refused, safety block), 4 (ffmpeg missing), 1 (invalid report, partial failures) |
| Configuration changes | `silence_threshold_dbfs` −45 → −6 changed silence ratio 0.333 → 0.667; `conservative` preset added `gate-human-review-on-low-confidence`; `limits.max_files` 5 rejected a 20-file folder |
| Determinism | repeated runs produce identical quality scores (88/88) and identical sha256 values |
| Packaging | no `SKILL.md`, no subfolders, no unprefixed files, no archives inside; ZIP extracted to a clean directory and the primary tool re-ran successfully |

Two claims deliberately **not** made: the ffmpeg frame-extraction path was implemented and
reviewed but could not be executed here (ffmpeg is absent from the build machine); and no
networked/provider-backed capability was tested because none is implemented.

## 12. Troubleshooting and recovery

| Symptom | Cause | Recovery |
| --- | --- | --- |
| exit 3, "approval required … non-interactive run" | approval gate | add `--approve`, or set `policy.approval_mode` to `auto` for that config |
| exit 3, "ingestion blocked" | safety verdict `block` | quarantine the file; re-export from the source; re-run `inspect` |
| exit 4 on `analyze-video` | no ffmpeg | install ffmpeg, or export frames and use `--frames-dir DIR --fps N` |
| `unsupported-format` for JPEG/WEBP/TIFF | Pillow missing | `pip install "Pillow>=10"` or convert to PNG |
| `decode-failed: PNG pixel stream truncated` | partial upload / corrupt file | re-copy the original; `ingest --no-resume` to re-hash |
| `limit-exceeded` on pixels/duration/files | configured caps | raise `limits.*` in a copied config and pass `--config` |
| Plan says "no catalog entries matched" | request text too generic | pass `--category` or a sharper `--query`; check `warnings` for per-capability misses |
| Report rejected with "observation has no … anchor" | evidence row lacks an anchor | add `timecode`, `start_time`, `region` or `offset`, or reclassify as interpretation with a `basis` |
| Duplicate work after a crash | checkpoints not reused | re-run `ingest` with the same `--out-dir` (resume is on by default) |
| Outputs refuse to overwrite | `policy.allow_overwrite_outputs` false | write to a new path or enable the flag deliberately |
| `config file not found` | wrong working directory | run from the skill root, or pass `--config` with a relative path |

Errors are structured (`code`, `message`, `hint`) in the JSON envelope; the human summary
on stderr repeats the hint. Nothing is silently retried except ingest, which bounds retries
by `runtime.retries` and records attempts.

## 13. Configuration reference

`asset-default-config.json` (copy, edit, pass with `--config`):

| Section | Keys |
| --- | --- |
| `paths` | `input_dir`, `output_dir`, `cache_dir`, `plan_dir`, `report_dir`, `catalog`, `policies` |
| `policy` | `preset` (draft/standard/conservative), `approval_mode` (prompt/auto/deny), `allow_overwrite_outputs`, `publication_claims` |
| `limits` | `max_files`, `max_file_bytes`, `max_pixels`, `max_audio_seconds`, `max_video_frames`, `max_plan_units`, `max_text_chars`, `max_zip_entries`, `max_uncompressed_ratio` |
| `runtime` | `stage_timeout_s`, `retries`, `retry_backoff_s`, `sample_fps`, `analysis_max_side_px`, `resume`, `checkpoint_file` |
| `analysis` | `scene_cut_threshold`, `flash_relative_change`, `silence_threshold_dbfs`, `confidence_review_threshold`, `confidence_escalate_threshold`, `language`, `transcript_mode`, `timecode_format` |
| `providers` | `vision`, `speech`, `language` — each `{enabled, adapter, model, endpoint}`; disabled by default and never contacted by this package |
| `privacy` | `redact_paths_in_reports`, `keep_originals_read_only`, `store_raw_samples`, `retention_days` |

Relative `paths.*` values are resolved against the working directory, then the config
directory, then this folder. No absolute path, drive letter or tenant path is required or
baked in.

## 14. Extension and maintenance

**Add a capability or refresh the catalog.** Edit
`asset-capability-catalog.json` (or regenerate it from an updated catalog document):
keep ids stable (`mi-<cat>-<n>`), keep every description non-empty, add the policy class
and gate ids. `plan` validates ids, uniqueness and gate references on every run, so a
malformed catalog fails fast instead of producing a bad plan.

**Add a metric.** Implement it in `tools-metrics.py` as a pure function over a `Raster` or
a sample list, return a dict with a `verdict`, and add two assertions to its `selftest()`
(one positive, one negative). Then surface it in the relevant operation in
`tools-miw.py` and add a row to the report assembler so it becomes evidence.

**Add an operation.** Add a handler `op_<name>(args, config)` returning
`(envelope, human_summary)`, register it in `HANDLERS`, add a subparser with `common(...)`,
and add the entry to `OPERATIONS` so `help` and `capabilities` stay truthful.

**Add a gate or preset.** Edit `asset-policy-presets.json`; reference the gate id from the
catalog's category or skill. Unknown gate ids are rejected by the plan validator.

**Enable a provider later.** Fill `providers.*` in a config copy, implement the adapter
behind an explicit approval gate, and keep the local measurement path as the default so
the package still works offline.

**Housekeeping.** Keep every filename prefixed (`tools-`, `knowledge-`, `asset-`), keep
all files directly in this folder, keep the single `knowledge-readme.md` as the only
documentation, and re-run both selftests plus one real `plan`/`report` round trip after any
change.
