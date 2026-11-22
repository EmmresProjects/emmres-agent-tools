# AI Video Production

## Purpose and scope

`ai-video-production` is a reusable, vendor-neutral Skill Tool for planning, validating, previsualizing, captioning, evaluating, and optionally rendering AI-video projects. It unifies all **47 categories and 564 named capabilities** in the supplied AI Video Creation Skill Catalog. The complete names and source descriptions are operational data in `ai-video-production/asset-skill-catalog.json`, not decorative documentation.

The tool produces editable plans, task-specific handoffs, quality gates, approval registers, SRT/WebVTT captions, and an interactive self-contained HTML animatic. It does **not** pretend to run neural generation. Provider-dependent generation, transformation, avatar, voice, training, sensor, and compute work is marked `provider-adapter-required` with status `planned`. This makes every catalog capability selectable and actionable without inventing provider results.

## Files, roles, and loading order

1. `ai-video-production/tools-ai-video.py` — **primary entry point**. Loads configuration and catalog, parses CLI commands, selects capabilities, writes artifacts, and returns structured JSON results/errors. It loads the two modules below automatically.
2. `ai-video-production/tools-video-core.py` — shared catalog validation, brief normalization, capability routing, approval policy, shot planning, captions, evaluation, atomic writes, and HTML animatic generation. Loaded first by the entry point; do not execute it directly.
3. `ai-video-production/tools-video-media.py` — optional FFmpeg/FFprobe discovery, bounded process execution, retries, cancellation, timeout cleanup, color-panel MP4 rendering, and output inspection. Loaded second; do not execute it directly.
4. `ai-video-production/tools-test-ai-video.py` — standard-library regression tests. Run after the primary files and assets are colocated; it is not imported at runtime.
5. `ai-video-production/asset-skill-catalog.json` — validated source catalog: 47 categories, 564 unique capabilities, purposes, descriptions, stable ids, and source numbering.
6. `ai-video-production/asset-default-config.json` — active defaults, limits, runtime policy, provider placeholders, retry/timeout controls, and executable names.
7. `ai-video-production/asset-brief.schema.json` — JSON Schema 2020-12 for editors, validators, and adapter authors. The runtime independently enforces the required safety-critical constraints using the standard library; a JSON Schema package is not required.
8. `ai-video-production/asset-example-brief.json` — complete editable fixture and quick-start input.

There is one knowledge file and no runtime dependency file because Python's standard library is sufficient for all local artifacts.

## Setup and dependencies

- Required: **Python 3.10 or newer**. Tested with Python 3.13.16. No third-party Python packages, network access, API keys, models, paid accounts, or services are required for local planning and artifacts.
- Optional: **FFmpeg and FFprobe** on `PATH`, only for the `render` and successful media-runtime portions of `probe`. Successful rendering was not tested in the build environment because these executables were unavailable. All other features work without them.
- Keep the folder and filenames together with exact case-sensitive spelling. Copy `ai-video-production/` directly beside the future package's root `SKILL.md`; no changes are needed.

All commands below assume the **future skill root** is the current directory and use `bash` explicitly:

```bash
bash -lc 'python3 --version'
bash -lc 'python3 ai-video-production/tools-ai-video.py --help'
bash -lc 'python3 ai-video-production/tools-test-ai-video.py'
```

## Commands and capabilities

### 1. Discover all capabilities

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py capabilities'
bash -lc 'python3 ai-video-production/tools-ai-video.py capabilities --category 17 --format json'
bash -lc 'python3 ai-video-production/tools-ai-video.py capabilities --query caption --format json'
bash -lc 'python3 ai-video-production/tools-ai-video.py capabilities --category quality-assurance-and-evaluation --output output/capabilities.json'
```

`--category` accepts a category number, exact case-insensitive display name, or lowercase id. `--query` searches capability ids, names, descriptions, and category purposes. JSON includes complete records. Text output is concise and human-readable.

All source categories are included:

1. Foundations: Diffusion, Transformers, and Latent Video
2. Prompt Engineering and Creative Briefing
3. Text-to-Video Generation
4. Image-to-Video and Keyframe Animation
5. Video-to-Video Transformation
6. Generative Video Editing and Inpainting
7. Temporal Consistency and Identity Preservation
8. Camera, Lens, and Cinematography Control
9. Motion, Physics, and Choreography
10. Story Development and Screenwriting
11. Preproduction, Shot Lists, and Storyboards
12. Multi-Shot Planning and Scene Assembly
13. Avatar and Digital Presenter Systems
14. Lip Sync, Facial Animation, and Performance
15. Voice, Speech, and Dubbing
16. Music, Sound Design, and Audio Post
17. Captioning, Subtitles, and Accessibility
18. Translation and Video Localization
19. Editing, Timeline, and Postproduction
20. Color, Look Development, and Finishing
21. VFX, Compositing, and Rotoscoping
22. 3D, NeRF, Gaussian Splatting, and Virtual Production
23. Animation and Motion Graphics
24. Music Videos and Audio-Reactive Visuals
25. Template, Stock, and Article-to-Video Systems
26. Marketing, Advertising, and Brand Video
27. Social Media and Short-Form Video
28. YouTube, Streaming, and Creator Workflows
29. Education, Training, and Instructional Video
30. Film, Television, and Episodic Production
31. News, Documentary, and Factual Video
32. Commerce, Product, and Retail Video
33. Architecture, Real Estate, and Spatial Video
34. Scientific, Medical, and Technical Visualization
35. Sports, Fitness, and Motion Analysis
36. Gaming, Machinima, and Virtual Worlds
37. Restoration, Enhancement, and Remastering
38. Compression, Encoding, and Delivery
39. Quality Assurance and Evaluation
40. Safety, Rights, Consent, and Provenance
41. Automation, APIs, and Production Pipelines
42. Model Training, Adaptation, and Research
43. Compute, Performance, and MLOps
44. Asset Management, Metadata, and Collaboration
45. Interactive, Personalized, and Real-Time Video
46. Specialized Capture and Sensor Workflows
47. Catalog Governance and Skill Orchestration

### 2. Validate a brief

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py validate --input ai-video-production/asset-example-brief.json'
bash -lc 'python3 ai-video-production/tools-ai-video.py validate --input input/brief.json --output output/normalized-brief.json'
```

Validation occurs before artifacts are written. It checks required and unknown fields, duration/FPS/ratio/resolution, shot structure and durations, asset records, and category/capability ids. Output is normalized with defaults. Invalid JSON reports line and column; missing input, oversized input, invalid fields, and unknown capabilities have distinct error codes.

### 3. Create a production plan

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py plan --input ai-video-production/asset-example-brief.json --output output/plan.json'
```

Select categories and/or individual capabilities in the brief. With neither, the tool selects eight core planning, assembly, accessibility, provenance, and release capabilities. Explicit selections are deduplicated. Selecting all 47 categories produces all 564 task records.

The `--request` option additionally accepts plain text, Markdown, or copied HTML. Tags, classes, data attributes, list numbering, and repeated whitespace are ignored. Exact category or capability display names/ids found in the cleaned text are added to the brief selections:

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py plan --input input/brief.json --request "<ol><li class=item>Translation and Video Localization</li><li>Delivery Specification Validator</li></ol>" --output output/plan.json'
```

Each selected capability receives a clear responsibility, inputs, expected artifacts, quality gates, required approvals, execution mode, status, and recovery route. Plans preserve the normalized brief, ordered shots, timing, provenance, warnings, pending approvals, and unresolved provider work. The plan itself is the editable work package; expected provider artifacts are contracts, not claims that those files were generated.

### 4. Run the complete local workflow

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py run --input ai-video-production/asset-example-brief.json --output-dir output/video'
```

Writes:

- `output/video/plan.json` — editable production plan and selected capability tasks.
- `output/video/preview.html` — interactive animated storyboard with play/pause, restart, seek, keyboard support, live captions, reduced-motion support, and no external resources.
- `output/video/captions.srt` — SubRip captions from shot timings.
- `output/video/captions.vtt` — WebVTT equivalent.
- `output/video/evaluation.json` — deterministic quality/release checks.

The HTML is an actual local previsualization using text and color panels, **not AI-generated footage**. Edit brief shots, captions, durations, camera, and visual descriptions, then rerun with a new directory or reviewed `--overwrite`.

### 5. Generate or regenerate individual artifacts

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py preview --input output/video/plan.json --output output/video/preview.html --overwrite'
bash -lc 'python3 ai-video-production/tools-ai-video.py captions --input output/video/plan.json --output output/video/captions.srt --format srt --overwrite'
bash -lc 'python3 ai-video-production/tools-ai-video.py captions --input output/video/plan.json --output output/video/captions.vtt --format vtt --overwrite'
bash -lc 'python3 ai-video-production/tools-ai-video.py evaluate --input output/video/plan.json --output output/video/evaluation.json --overwrite'
```

Caption timing rejects non-positive ranges. Evaluation checks shot presence, valid timing, continuity, caption coverage, resolved approvals, and disclosure of external work. `review-required` returns exit code 2 because human action remains; it is not a crash.

### 6. Probe and optionally render an MP4 animatic

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py probe'
bash -lc 'python3 ai-video-production/tools-ai-video.py render --input output/video/plan.json --output output/video/animatic.mp4 --approve'
```

`render` requires explicit `--approve`, uses no shell, starts no network service, and supports only `.mp4`. It constructs color-panel segments from the plan, concatenates them with FFmpeg, uses H.264 by default or the configured codec, and inspects the completed media with FFprobe before atomically moving it into place. The MP4 intentionally contains color panels only; use the HTML preview for text/caption visuals. It does not create neural footage.

Controls come from `ai-video-production/asset-default-config.json` or a copied file passed with `--config`: executable names, timeout (default 120 seconds), retries (default 1), exponential backoff, dimensions/FPS, and colors. `--cancel-file path` requests cooperative process termination when that file appears. Timeout/cancellation terminates the external process group. Temporary render directories and `.partial.mp4` files are cleaned on success or failure. Invalid arguments and FFmpeg errors return bounded diagnostics rather than infinite retries.

```bash
bash -lc 'python3 ai-video-production/tools-ai-video.py render --input output/video/plan.json --output output/video/animatic.mp4 --approve --cancel-file output/cancel.render'
```

## Input schema and configuration

Required brief fields are `project_title` and `prompt`. Important optional fields:

| Field | Meaning/default |
|---|---|
| `objective`, `audience` | Creative and review context; empty when omitted |
| `duration_seconds` | Requested duration, default 12; maximum 3600 |
| `aspect_ratio` | Positive integer ratio, default `16:9` |
| `resolution` | Width/height, default `[1280,720]`, each 16–16384 |
| `fps` | Default 24; greater than 0 and at most 120 |
| `language`, `seed` | Locale and reproducibility metadata |
| `categories`, `capabilities` | Exact ids/names to select; deduplicated |
| `shots` | Ordered `title`, `visual`, optional caption/duration/source/camera/motion |
| `assets` | Local path, kind, license, consent, source URI metadata |
| `safety` | Real-person, voice-cloning, sensitive, factual, and political flags |
| `delivery` | Container/codec/audio/caption preferences and disclosure |
| `provider` | Configurable provider/model/credential variable name; never a secret value |

Explicit shot durations take precedence. Unspecified shots share remaining requested duration. If explicit shots exceed the requested duration, the plan uses the actual shot total and emits a warning. Input JSON defaults to a 1 MiB limit. Plans support up to 500 shots and all 564 capabilities.

Asset media paths are metadata for planning and future adapters; the built-in color renderer does not composite them. This limitation is explicit rather than silently ignoring them in a claimed edited video. Provider `endpoint` is null and network access is false by default. A future adapter may supply an allowlisted HTTPS endpoint and read its credential from the named environment variable; no credential is bundled.

All tool-internal default resource paths are resolved relative to `tools-video-core.py`. Documentation references use future-skill-root-relative paths such as `ai-video-production/asset-default-config.json`.

## Outputs and errors

Successful non-text commands print JSON with `status` and operation-specific fields. Errors print structured JSON to standard error:

```json
{
  "status": "error",
  "error": {
    "code": "INPUT_NOT_FOUND",
    "message": "Input file does not exist: input/missing.json",
    "details": null
  }
}
```

Common codes include `INVALID_JSON`, `INVALID_BRIEF`, `UNKNOWN_CAPABILITY`, `NO_CAPABILITY_MATCH`, `OUTPUT_EXISTS`, `APPROVAL_REQUIRED`, `RUNTIME_UNAVAILABLE`, `TIMEOUT`, `CANCELLED`, and `RENDER_FAILED`. Exit 0 indicates successful local work; exit 2 indicates an expected validation/review/operational error; 130 indicates keyboard interruption. Unexpected programming errors are not relabeled as successful output.

## Safety and approval rules

- No network, shell interpretation, model invocation, publishing, training, credential use, or privileged action occurs in local commands.
- `render` requires `--approve`; overwriting any artifact also requires `--overwrite`. Existing outputs are preserved by default.
- Likeness/avatar/identity, voice cloning, sensitive/political content, factual reconstruction, rights-sensitive assets, training, security/webhook, and publishing-related capabilities add approval entries. Assets without license metadata add a rights approval.
- Pending approvals block release readiness but do not prevent editing or evaluating a plan. Record human decisions in a reviewed copy of the plan; do not treat self-attestation as legal consent.
- Medical, political, factual, public-figure, identity, and high-impact uses need qualified human review. Provenance and synthetic-media disclosures must travel with downstream media.
- Plans contain user-provided brief content. Review before sharing. No telemetry, hidden endpoints, or secret logging is implemented.

## Validation and tests actually completed

Executed with Python 3.13.16:

- Main `--help`, category-filtered `capabilities`, and complete catalog loading.
- Catalog integrity: 47 categories, exactly 564 capabilities, 12 per category, unique ids.
- Reachability test generated a real plan for every category and matched all 564 selected capability ids.
- Example plan: three shots, 12 seconds, 60 capabilities from five selected categories.
- Valid brief normalization; invalid duration and missing prompt rejection; missing-file structured error.
- Changed project/shots input produced changed plan content.
- SRT and WebVTT text/timestamps generated and inspected.
- Self-contained HTML generated and inspected; embedded title HTML was escaped; no external HTTPS dependency.
- Full `run` interface generated all five outputs, parsed both JSON files, and inspected HTML/captions.
- Evaluation verified continuous timing and reported pending approval/release review correctly.
- Existing-output test verified atomic no-overwrite recovery preserves the original file.
- HTML request selection ignored markup and added the requested category.
- Render approval gate rejected an unapproved request.
- The automated suite contains 14 tests. See the final delivery summary for the final rerun result.

**Not performed:** successful MP4 encoding/FFprobe inspection, neural inference, provider API calls, source-media compositing, publishing, voice/likeness generation, model training, or sensor capture. FFmpeg/FFprobe were not installed in the build environment. The adapter is implemented and its independent approval/error paths are tested, but successful FFmpeg execution remains platform-dependent and must be verified where FFmpeg is installed.

## Limits and unavailable integrations

- This is a production orchestrator and previsualization tool, not a bundled neural video model. It cannot convert a prompt into photoreal footage without a configured provider.
- Translation/localization tasks create routing and handoff requirements; no machine translation is claimed. SRT/VTT timing and formatting are local and functional.
- HTML playback uses text/color panels and browser timing, not decoded source video. Browser behavior depends on a modern HTML/JavaScript viewer.
- MP4 rendering is color-only, does not composite source assets, and was not successfully run locally. Other containers/codecs require adapter extension.
- Quality evaluation checks plan structure, timing, captions, approvals, and disclosure. It does not claim perceptual, identity, flicker, audio-sync, or neural prompt-adherence analysis of rendered pixels.
- No external platform can be made reliable by this folder. Hosted catalog, permissions, endpoint, or provider outages remain integration/platform issues.

## Troubleshooting and recovery

- **Input not found/invalid JSON:** use exact case-sensitive paths and validate JSON syntax. The parser reports line/column.
- **Unknown category/capability:** run `capabilities --query term`; use returned ids in the brief.
- **Output exists:** inspect it, select another path, or use `--overwrite` only when replacement is intended. Atomic `.tmp` and `.partial.mp4` files are removed on handled failures.
- **Review required:** inspect `evaluation.json`; resolve the earliest failed check. Pending approvals are expected before release.
- **FFmpeg unavailable:** install a supported FFmpeg/FFprobe build or set executable paths in a copied config. Local JSON/HTML/caption operations do not need it.
- **Timeout/cancellation:** preserve the plan and successful artifacts, increase a justified timeout, reduce dimensions/FPS/shot count, or fix the external process. Rerun only the affected render.
- **Provider-dependent task remains planned:** configure and test a separate allowlisted adapter. Never convert `planned` to `completed` without verified output and provenance.
- **HTML does not animate:** enable JavaScript in a modern browser. The file is still inspectable as HTML. No network is required.

## Extension and maintenance

Add provider behavior in a new prefixed `tools-` adapter or extend `tools-video-media.py` with explicit allowlists, schema validation, timeouts, cleanup, approval, and tests. Reuse `tools-video-core.py` for catalog ids, plan contracts, and recovery. Never embed credentials. Keep `asset-skill-catalog.json` ids stable; if renaming resources, update loader paths, commands, tests, and this knowledge file together. Run the full tests after changes and add successful media tests on a host with FFmpeg. Preserve completed shots, isolate the earliest failing stage, and record model/provider versions and provenance in downstream artifacts.
