# AI Image Skill Tool - Knowledge Base

This folder is a single, self-contained tool for the 184 candidate skills in the
*AI Image Skill Catalog* (23 categories x 8 skills). It is one command-line entry point,
`tools-ai-image-toolkit.py`, that routes each catalogue number to an operation.

Read the **Limitations** section before relying on any result.

## 1. What is real and what is not

Every capability is mapped to one of five execution modes. The mode tells you what kind of
output to expect.

| Mode | Count | What happens when you run it |
|---|---|---|
| `local` | 110 | Real computation or file operation on the files you give it (Pillow for images, arithmetic for layouts, budgets and schedules). |
| `heuristic` | 11 | Rule-based advisory output (keyword and pattern checks, token estimates, glossaries). A person must review it. |
| `engine` | 32 | Builds and validates a **job specification** or ComfyUI-style graph (inpainting, object removal, ControlNet, IP-Adapter, face restoration, batch submission). **Nothing is generated** unless a provider is configured, enabled, and you approve the submission. |
| `policy` | 14 | Rule-based screening, release checklists, workflow state and lineage. Not legal advice and not a compliance certification. |
| `blueprint` | 17 | A structured plan built from a category template. It does not create images. |

**Model-dependent work is not executed without a configured provider.** Diffusion inference,
inpainting, ControlNet, IP-Adapter, LoRA or model training, and GPU scheduling need a model
runtime. This toolkit does not contain one. It writes the job specification and stops, unless
you set `provider.enabled`, supply an endpoint, supply the credential, and pass `--execute --approve`.

Approval counts across the 184 capabilities: none 155,
conditional 28, required 1.
See section 6 for what is enforced in code.

## 2. Files in this folder

| File | Role |
|---|---|
| `tools-ai-image-toolkit.py` | **Entry point.** CLI with `help`, `list`, `ops`, `describe`, `run`, `check-config` (alias `doctor`), `version`. |
| `tools-common.py` | Shared core: error model and exit codes, parameter specs and validation, `Context` (output paths, input protection, approvals), config loading, hashing and colour helpers. |
| `tools-prompt-engine.py` | Prompt construction, negatives, debug suggestions, variants, weight checks, glossary, captions, alt text, eval suites, prompt library, and `plan.blueprint`. |
| `tools-compute-engine.py` | Resolution, tiling, VRAM and throughput budgets, schedules, sweep grids, blind A/B ballots, region layouts, occlusion order, GPU queue, training plans, and `system.*` environment probes. |
| `tools-policy-engine.py` | Rule-based policy screens, style rewrites, manifest and disclosure checks, the release gate, workflow state with lineage, and reproducibility records (`repro.*`). |
| `tools-image-engine.py` | 31 Pillow-based catalogue operations (`image.*`) plus the utility `image.inspect`: export, crop, masks, compositing, colour, contact sheets, QA, metadata, redaction scan, authenticity indicators, and more. |
| `tools-provider-adapter.py` | `engine.*` job specs, ComfyUI-style graph, control plans, and `provider.*` (dry-run check, health probe, resumable batch). The only module that can use the network. |
| `tools-self-test.py` | Self-test: 46 tests covering structure, catalogue integrity, CLI behaviour, operation outputs, safety refusals and the provider path. Uses a local mock provider. |
| `tools-requirements.txt` | Python dependency list (Pillow). |
| `knowledge-readme.md` | This file. |
| `asset-capability-catalog.json` | The 184 capabilities: number, name, description, category, operation, mode, approval, risk, default parameters. |
| `asset-config-default.json` | Default limits, retries, timeouts, provider settings (disabled), thresholds, colour profile paths, fonts. Override with `--config`. |
| `asset-prompt-vocabulary.json` | Prompt presets, artifact negatives, debug rules, moods, variant dimensions, glossary, alt-text rules. |
| `asset-plan-templates.json` | Blueprint templates for categories 1-23, plus default governance, recovery and evaluation plans. |
| `asset-model-profiles.json` | Model-family parameter counts, precision and residency factors, and training defaults used by the budget tools. |
| `asset-policy-rules.json` | Policy rulesets (content safety, age, deepfake, copyright, likeness, adversarial, misleading, portrait ethics, medical, cultural, marketplace), artist-style map, disclosure templates, release checklist, workflow transitions. |
| `asset-layout-presets.json` | Canvases, poster and social layouts, delivery specifications and crop targets. |
| `asset-sample-sweep.json` | Example batch request for `provider.batch` and `compute.sweep_grid`. Edit the prompt, axes and model name. |

## 3. Requirements

- Python 3.9 or newer (standard library only, except the image operations).
- Pillow for `image.*` operations: `python3 -m pip install -r tools-requirements.txt`.
- Tested on Python 3.13.16 with Pillow 13.0.0 (Linux, 2 CPUs, no GPU). Other versions and platforms were not tested.

No numpy, OpenCV, PyTorch or GPU driver is needed.

## 4. Quick start

Run every command from inside this folder, or give the full path to the entry point.

```bash
python3 tools-ai-image-toolkit.py help
python3 tools-ai-image-toolkit.py check-config              # validates assets, config, Pillow, paths
python3 tools-ai-image-toolkit.py list                      # all 184 capabilities
python3 tools-ai-image-toolkit.py list --category 8 --mode local
python3 tools-ai-image-toolkit.py describe 8.1              # parameters, defaults, example
python3 tools-ai-image-toolkit.py run 8.1 -p image=photo.png -p threshold=128
python3 tools-ai-image-toolkit.py run image.inspect -p images=photo.png   # utility op by name
python3 tools-ai-image-toolkit.py run 22.7 -p image=photo.png -p formats=png,webp -p sizes=512,256
python3 tools-self-test.py                                  # run the test suite
```

A capability can be named by its number (`8.1`), its id (`precision-mask-generator`), or a
unique part of its name. Utility operations that are not in the catalogue, such as
`image.inspect`, can be run by operation name.

### Passing parameters

- Repeat `-p key=value` for each parameter.
- Lists: `a,b,c` or a JSON array. Objects: inline JSON or `@file.json`.
- Files: relative or absolute paths. They are checked before anything runs.
- Booleans: `true`/`false`, `yes`/`no`, `1`/`0`.
- All parameters at once: `--params-json '{"image": "photo.png"}'` or `--params-json @params.json`.

`run` prints one JSON document on stdout:

```json
{"ok": true, "capability": {...}, "operation": "image.mask_build", "execution_mode": "local",
 "notice": "...", "result": {...}, "warnings": [], "artifacts": ["..."], "toolkit_version": "1.0.0"}
```

Errors go to stderr as one JSON document `{"ok": false, "error": {"code", "message", "field", "hint"}}`.

## 5. Command reference and exit codes

| Command | Purpose |
|---|---|
| `help` | Full usage text. |
| `list [--category N] [--mode MODE] [--json]` | Catalogue table. |
| `ops [--json]` | Operation registry, including utility operations. |
| `describe CAP [--json]` | Parameters, types, defaults, ranges, approval and mode notice. |
| `run CAP [-p k=v ...] [--params-json J] [--output-dir D] [--config F] [--approve] [--overwrite]` | Run one capability. |
| `check-config` / `doctor [--config F] [--output-dir D]` | Checks assets, catalogue, config, Pillow, provider settings, and output-directory writability. |
| `version` | Toolkit version. |

| Exit code | Meaning |
|---|---|
| 0 | Success. |
| 2 | Invalid input: unknown or bad parameter, bad file content, refused overwrite, blocked release, or failed `check-config`. |
| 3 | Approval required. Re-run with `--approve` after review. |
| 4 | Dependency missing (Pillow). |
| 5 | Unavailable: provider disabled, credential missing or rejected, or provider unreachable after retries. |
| 6 | Runtime error. Set `AIIMG_DEBUG=1` for a traceback. |
| 7 | Not found: unknown capability or missing input file. |
| 8 | Cancelled (Ctrl-C). |
| 9 | Timeout. |

## 6. Approval gates and safety rules

These rules are enforced in code, not just documented.

- **Required approval.** Capability 18.8 (Safe Release Checklist Builder) refuses to run without `--approve`.
- **Conditional approval, enforced:**
  - `engine.job` with `execute=true` needs `--approve`.
  - `provider.batch` with `execute=true` needs `--approve`.
  - `policy.release_gate` with `action=publish` needs `--approve`, and blocks if any checklist item is unchecked. It never publishes or uploads anything.
  - `policy.workflow` transitions marked as needing approval need `--approve`. The author of an item cannot approve it, and approval requires a different actor.
  - `prompt.library` removal needs `--approve`.
- **Conditional label, advisory only.** `engine.comfy_graph` (3.8, 20.3) is labelled conditional in the catalogue, but the tool only writes the graph file. It never submits it.
- **Outputs never replace inputs.** Any file passed as an input is protected, including files nested inside JSON parameters.
- **Explicit overwrites.** An existing file given as `output=` is refused unless `--overwrite --approve` is passed.
- **Default outputs** go to `<output-dir>/<capability-id>/`. Replacing a previous default output is allowed and reported as a warning.
- **Bounded inputs.** Limits from the config apply: `max_pixels`, `max_file_bytes`, `max_batch_items`, `max_text_chars`, `max_variants`, `max_layers`, and others. The file size is checked before opening, and the pixel count from the image header before the pixels are decoded.
- **Metadata.** `image.export` strips metadata by default (`strip_metadata=true`). `image.redact` reports metadata findings (EXIF, GPS, text chunks) and does not detect faces, plates or screens.
- **Records.** Policy decisions, release gates, workflow events and batch manifests are written as JSON. Lineage files are appended, never rewritten.

## 7. Configuration

Defaults live in `asset-config-default.json`. To override them, write a partial JSON file and pass it
with `--config`. Only the keys you give are changed; the rest are merged in.

```json
{"output_dir": "/data/ai-out",
 "limits": {"max_pixels": 25000000},
 "retries": {"max_attempts": 4, "backoff_seconds": 2},
 "provider": {"enabled": true,
              "endpoint": "https://gpu.example.internal:8443",
              "allowed_hosts": ["gpu.example.internal"],
              "timeout_seconds": 300,
              "api_key_env": "AIIMG_PROVIDER_KEY"}}
```

- `output_dir` defaults to `ai-image-toolkit-output` in the current directory.
- Provider credentials are **never** stored in files. They are read from the environment variable named in `provider.api_key_env`. The default name is `AIIMG_PROVIDER_KEY`.
- `provider.allowed_hosts` must list the endpoint host. Credentials embedded in the URL are refused.
- Redirects are not followed.
- `AIIMG_DEBUG=1` prints tracebacks for unexpected errors.

## 8. The provider contract

This is the one piece that goes over the network, and it is a **simple contract defined by this toolkit**.
It is not the API of ComfyUI, Automatic1111, or any hosted service.

- `POST {endpoint}{submit_path}` (default `/generate`) with a JSON body containing the job fields. For `engine.job`, `image_base64` and `mask_base64` are included when those inputs are given.
- The response must be the raw image (PNG, JPEG or WEBP, verified by magic bytes), or JSON with `image_base64` (or `images[0]`).
- `GET {endpoint}{health_path}` (default `/health`), used by `run provider.check -p mode=probe`.
- Attempts: up to `retries.max_attempts`, with exponential backoff, for network errors, 408, 429 and 5xx. 401 and 403 are reported as unavailable, with no retry.
- Response size is capped by `limits.max_file_bytes`.

To use a real engine, put a small adapter in front of it that speaks this contract. Then set the endpoint.
Do not send confidential material to an endpoint you do not control.

## 9. Limitations

These are stated plainly so that nothing is over-claimed.

1. **No image generation runs locally.** The 32 `engine` capabilities (for example inpainting, object removal, outpainting, ControlNet, IP-Adapter, face restoration, multi-reference composition, ComfyUI and API automation) produce job specifications, control specifications or graphs. Output needs a configured provider that you run or trust. The training items (16.3, 16.4, 16.5) produce training plans and budgets, not trained weights. The model-merge item (16.8) produces a sweep grid of settings, not a merged model.
2. **The provider contract is generic.** Only the contract in section 8 is implemented. Engine-specific APIs are not. `engine.comfy_graph` produces ComfyUI API-format JSON, but the tool does not submit it.
3. **Provider testing used a local mock.** The submit, retry, timeout, credential and batch-resume paths were tested against an in-process mock server on 127.0.0.1. No real GPU service was tested.
4. **Policy screens are keyword and pattern triage.** They can miss rephrased or unusual requests and can flag harmless text. They are not a safety guarantee, not legal advice, and not a compliance certification. Every decision needs a person to confirm it.
5. **Face, plate and screen detection is not bundled.** `image.redact` checks metadata only and says so in its output. Identifiable visual content needs human review or a separate detector.
6. **Authenticity checks are indicators, not proof.** Error-level analysis, metadata presence and similar signals can be wrong in both directions. Results are labelled "uncertain - indicators only".
7. **Colour management needs profiles you supply.** No ICC profiles are bundled. Without an embedded profile, the source is treated as sRGB and a warning is shown. CMYK conversion needs a configured CMYK profile.
8. **Image algorithms are simple.** Edges use a Sobel-like filter, not Canny. Sharpening, denoising, upscaling and inpainting-style fills are Pillow-level approximations and are not comparable to model-based restoration.
9. **Blueprints and heuristics are guidance.** Plans and rule-based advice are starting points for a human. They are not measurements.
10. **Budgets are estimates.** VRAM, throughput and cost figures come from the bundled model profiles and rules of thumb. They are not benchmarks of your hardware. Run `system.probe` in `setup-check` or `gpu-compat` mode to check the local environment.
11. **The catalogue mapping is ours.** The PDF describes 184 skills. Each one is mapped to one of 80 operations, many with different default parameters. Two capabilities that share an operation are configurations of the same code, not separate algorithms.
12. **Ballots and manifests are only as blind as they are used.** `compute.blind_pairs` randomises left and right and separates the key file, but it cannot stop a rater from opening the key.
13. **Workflow and lineage are local JSON files.** Nothing is synchronised, locked or signed. Two users writing the same lineage file at once can lose events.
14. **Platform coverage.** Tested on Linux only. Python 3.9 compatibility was checked by parsing the source with the 3.9 grammar. Code was executed only on Python 3.13.
15. **Output quality was not reviewed by eye.** Tests check file names, sizes, modes, counts, error codes and decisions. They do not judge visual quality.
16. **Relative output paths** are resolved against the current directory, not the folder containing the tool.

## 10. Tests

`python3 tools-self-test.py` runs 46 tests in a temporary directory. It writes nothing into this folder and removes its
workspace unless `--keep` is given. It starts a mock provider on a random 127.0.0.1 port. It needs no internet access.

- **Structure (S01-S09):** file names and folder layout, assets parse, engine modules compile, 184 unique capabilities in 23x8, every operation registered, catalogue defaults only use declared parameters, safe defaults (provider disabled, no `execute` default of true), approval and mode values, asset cross-references.
- **CLI (C01-C07):** help, version, list and filters, describe, error codes for unknown or bad parameters and missing files, approval gate, check-config (including a malformed config), a utility operation by name.
- **Local operations (L01-L11):** export naming and sizes, mask mode and size, comparison heatmap, contact sheet, sweep expansion to 16, blind-ballot separation, policy decisions for violent, benign and obfuscated text, binary input refused, release gate, workflow with separation of duties and lineage, prompt build and weight parsing.
- **Safety (G01-G05):** an output equal to an input is refused and the input is intact, overwrite rules, unsupported extension, configured pixel limit, non-image input.
- **Engine and provider (P01-P14):** prepared job with no network call, mask rules, no network call without approval, disabled provider, host allow-list and embedded credentials, a PNG round trip, base64 JSON response, wrong key, missing key, 500 retries, timeout, probe, comfy graph and control plan, batch dry run, approval, limit, resume.

Run a subset with `--only P` (prefix match), and use `-v` for command-level output.

Run the self-test from a copy of the folder placed anywhere on disk. It does not depend on the current directory.

## 11. Capability list

Operation names are shown so you can see which capabilities share code. The approval and risk columns are
the catalogue values. Enforcement is described in section 6.

| No. | Capability | Id | Operation | Mode | Approval | Risk |
|---|---|---|---|---|---|---|
| 1.1 | Image Training Concepts Explainer | `image-training-concepts-explainer` | `plan.blueprint` | blueprint | none | low |
| 1.2 | Pixel and Tensor Interpreter | `pixel-and-tensor-interpreter` | `compute.tensor_spec` | local | none | low |
| 1.3 | Latent Space Navigator | `latent-space-navigator` | `compute.latent_interpolation` | local | none | low |
| 1.4 | Diffusion Process Demonstrator | `diffusion-process-demonstrator` | `compute.diffusion_schedule` | local | none | low |
| 1.5 | Prompt Encoding Specialist | `prompt-encoding-specialist` | `prompt.token_estimate` | heuristic | none | low |
| 1.6 | Cross-Attention Visualizer | `cross-attention-visualizer` | `prompt.binding_check` | heuristic | none | low |
| 1.7 | Generative Model Comparator | `generative-model-comparator` | `plan.blueprint` | blueprint | none | low |
| 1.8 | Image Synthesis Glossary Builder | `image-synthesis-glossary-builder` | `prompt.glossary` | local | none | low |
| 2.1 | Prompt Architect | `prompt-architect` | `prompt.build` | local | none | low |
| 2.2 | Negative Prompt Engineer | `negative-prompt-engineer` | `prompt.negative` | heuristic | none | low |
| 2.3 | Prompt Debugger | `prompt-debugger` | `prompt.debug` | heuristic | none | low |
| 2.4 | Prompt Variant Generator | `prompt-variant-generator` | `prompt.variants` | local | none | low |
| 2.5 | Art Direction Translator | `art-direction-translator` | `prompt.brief_translate` | heuristic | none | low |
| 2.6 | Prompt Weighting Specialist | `prompt-weighting-specialist` | `prompt.weight_check` | local | none | low |
| 2.7 | Seed and Reproducibility Manager | `seed-and-reproducibility-manager` | `repro.seed_record` | local | none | low |
| 2.8 | Prompt Library Curator | `prompt-library-curator` | `prompt.library` | local | conditional | low |
| 3.1 | Stable Diffusion Setup Guide | `stable-diffusion-setup-guide` | `system.probe` | local | none | low |
| 3.2 | Checkpoint Selection Advisor | `checkpoint-selection-advisor` | `plan.blueprint` | blueprint | none | low |
| 3.3 | Sampler and Scheduler Tuner | `sampler-and-scheduler-tuner` | `compute.sweep_grid` | local | none | low |
| 3.4 | VAE Manager | `vae-manager` | `plan.blueprint` | blueprint | none | low |
| 3.5 | SDXL Workflow Specialist | `sdxl-workflow-specialist` | `compute.resolution` | local | none | low |
| 3.6 | Stable Diffusion 3 Workflow Specialist | `stable-diffusion-3-workflow-specialist` | `compute.resolution` | local | none | low |
| 3.7 | WebUI Workflow Builder | `webui-workflow-builder` | `engine.job` | engine | conditional | low |
| 3.8 | ComfyUI Graph Designer | `comfyui-graph-designer` | `engine.comfy_graph` | engine | conditional | low |
| 4.1 | ControlNet Orchestrator | `controlnet-orchestrator` | `engine.control_plan` | engine | none | low |
| 4.2 | Pose Conditioning Specialist | `pose-conditioning-specialist` | `engine.control_plan` | engine | none | low |
| 4.3 | Depth-Guided Composer | `depth-guided-composer` | `engine.control_plan` | engine | none | low |
| 4.4 | Edge and Line-Art Controller | `edge-and-line-art-controller` | `image.edge_map` | local | none | low |
| 4.5 | Segmentation-Guided Generator | `segmentation-guided-generator` | `engine.control_plan` | engine | none | low |
| 4.6 | IP-Adapter Specialist | `ip-adapter-specialist` | `engine.control_plan` | engine | none | low |
| 4.7 | Regional Prompting Composer | `regional-prompting-composer` | `compute.region_layout` | local | none | low |
| 4.8 | Multi-Control Conflict Resolver | `multi-control-conflict-resolver` | `compute.control_conflicts` | local | none | low |
| 5.1 | Concept Art Generator | `concept-art-generator` | `prompt.build` | local | none | low |
| 5.2 | Photorealistic Scene Creator | `photorealistic-scene-creator` | `prompt.build` | local | none | low |
| 5.3 | Illustration Style Creator | `illustration-style-creator` | `prompt.build` | local | none | low |
| 5.4 | Product Visualization Maker | `product-visualization-maker` | `prompt.build` | local | none | low |
| 5.5 | Architectural Visualization Maker | `architectural-visualization-maker` | `prompt.build` | local | none | low |
| 5.6 | Food and Beverage Visual Maker | `food-and-beverage-visual-maker` | `prompt.build` | local | none | low |
| 5.7 | Fashion Concept Generator | `fashion-concept-generator` | `prompt.build` | local | none | low |
| 5.8 | Abstract Visual Synthesizer | `abstract-visual-synthesizer` | `prompt.build` | local | none | low |
| 6.1 | Inpainting Specialist | `inpainting-specialist` | `engine.job` | engine | conditional | medium |
| 6.2 | Outpainting Specialist | `outpainting-specialist` | `image.outpaint_prepare` | local | none | low |
| 6.3 | Object Removal Editor | `object-removal-editor` | `engine.job` | engine | conditional | medium |
| 6.4 | Object Addition Editor | `object-addition-editor` | `engine.job` | engine | conditional | medium |
| 6.5 | Background Replacement Editor | `background-replacement-editor` | `image.composite` | local | none | low |
| 6.6 | Relighting Specialist | `relighting-specialist` | `engine.job` | engine | conditional | medium |
| 6.7 | Expression and Gaze Editor | `expression-and-gaze-editor` | `engine.job` | engine | conditional | high |
| 6.8 | Image Repair and Restoration Editor | `image-repair-and-restoration-editor` | `engine.job` | engine | conditional | medium |
| 7.1 | Multi-Reference Composer | `multi-reference-composer` | `engine.job` | engine | conditional | medium |
| 7.2 | Identity Consistency Manager | `identity-consistency-manager` | `engine.job` | engine | conditional | high |
| 7.3 | Style Consistency Manager | `style-consistency-manager` | `image.consistency_report` | local | none | low |
| 7.4 | Photomontage Integrator | `photomontage-integrator` | `image.composite` | local | none | low |
| 7.5 | Contact Sheet Builder | `contact-sheet-builder` | `image.contact_sheet` | local | none | low |
| 7.6 | Before-and-After Editor | `before-and-after-editor` | `image.before_after` | local | none | low |
| 7.7 | Visual Asset Family Generator | `visual-asset-family-generator` | `image.responsive_crop` | local | none | low |
| 7.8 | Reference Conflict Resolver | `reference-conflict-resolver` | `compute.control_conflicts` | local | none | low |
| 8.1 | Precision Mask Generator | `precision-mask-generator` | `image.mask_build` | local | none | low |
| 8.2 | Semantic Segmentation Editor | `semantic-segmentation-editor` | `image.mask_build` | local | none | low |
| 8.3 | Alpha Matting Specialist | `alpha-matting-specialist` | `image.mask_build` | local | none | low |
| 8.4 | Layered Composite Builder | `layered-composite-builder` | `image.composite` | local | none | low |
| 8.5 | Shadow and Reflection Composer | `shadow-and-reflection-composer` | `image.shadow_reflection` | local | none | low |
| 8.6 | Occlusion Planner | `occlusion-planner` | `compute.occlusion_order` | local | none | low |
| 8.7 | Edge Decontamination Specialist | `edge-decontamination-specialist` | `image.edge_decontam` | local | none | low |
| 8.8 | Blend Mode Advisor | `blend-mode-advisor` | `plan.blueprint` | blueprint | none | low |
| 9.1 | AI Upscaling Specialist | `ai-upscaling-specialist` | `image.upscale` | local | none | low |
| 9.2 | High-Resolution Fix Tuner | `high-resolution-fix-tuner` | `compute.tile_plan` | local | none | low |
| 9.3 | Detail Recovery Specialist | `detail-recovery-specialist` | `engine.job` | engine | conditional | medium |
| 9.4 | Face Restoration Controller | `face-restoration-controller` | `engine.job` | engine | conditional | high |
| 9.5 | Noise and Grain Manager | `noise-and-grain-manager` | `image.denoise_grain` | local | none | low |
| 9.6 | Deblur and Sharpening Specialist | `deblur-and-sharpening-specialist` | `image.sharpen` | local | none | low |
| 9.7 | Old Photo Restoration Specialist | `old-photo-restoration-specialist` | `engine.job` | engine | conditional | high |
| 9.8 | Print Resolution Planner | `print-resolution-planner` | `compute.print_resolution` | local | none | low |
| 10.1 | Color Grading Specialist | `color-grading-specialist` | `image.color_adjust` | local | none | low |
| 10.2 | Palette Transfer Editor | `palette-transfer-editor` | `image.palette_transfer` | local | none | low |
| 10.3 | Lighting Continuity Auditor | `lighting-continuity-auditor` | `image.consistency_report` | local | none | low |
| 10.4 | Day-to-Night Converter | `day-to-night-converter` | `image.color_adjust` | heuristic | none | low |
| 10.5 | Season and Weather Transformer | `season-and-weather-transformer` | `engine.job` | engine | conditional | medium |
| 10.6 | Cinematic Look Designer | `cinematic-look-designer` | `image.color_adjust` | local | none | low |
| 10.7 | HDR and Dynamic Range Specialist | `hdr-and-dynamic-range-specialist` | `plan.blueprint` | blueprint | none | low |
| 10.8 | Output Color Manager | `output-color-manager` | `image.color_convert` | local | none | low |
| 11.1 | Image Typography Planner | `image-typography-planner` | `image.text_overlay` | local | none | low |
| 11.2 | AI Text Artifact Corrector | `ai-text-artifact-corrector` | `image.text_overlay` | local | none | low |
| 11.3 | Logo Concept Generator | `logo-concept-generator` | `engine.job` | engine | conditional | low |
| 11.4 | Poster and Key Art Designer | `poster-and-key-art-designer` | `image.layout_compose` | local | none | low |
| 11.5 | Social Graphic Builder | `social-graphic-builder` | `image.layout_compose` | local | none | low |
| 11.6 | Packaging Artwork Conceptualizer | `packaging-artwork-conceptualizer` | `image.layout_compose` | local | none | low |
| 11.7 | Infographic Visual Asset Maker | `infographic-visual-asset-maker` | `engine.job` | engine | conditional | low |
| 11.8 | Brand Template Adapter | `brand-template-adapter` | `image.layout_compose` | local | none | low |
| 12.1 | Character Design Specialist | `character-design-specialist` | `prompt.build` | local | none | low |
| 12.2 | Character Consistency Engineer | `character-consistency-engineer` | `engine.job` | engine | conditional | medium |
| 12.3 | Portrait Generator | `portrait-generator` | `prompt.build` | local | none | low |
| 12.4 | Group Composition Specialist | `group-composition-specialist` | `prompt.build` | local | none | low |
| 12.5 | Hands and Anatomy Repair Specialist | `hands-and-anatomy-repair-specialist` | `engine.job` | engine | conditional | medium |
| 12.6 | Wardrobe and Costume Editor | `wardrobe-and-costume-editor` | `engine.job` | engine | conditional | medium |
| 12.7 | Hair and Grooming Editor | `hair-and-grooming-editor` | `engine.job` | engine | conditional | medium |
| 12.8 | Ethical Portrait Workflow Auditor | `ethical-portrait-workflow-auditor` | `policy.run` | policy | none | medium |
| 13.1 | E-commerce Image Producer | `e-commerce-image-producer` | `image.layout_compose` | local | none | low |
| 13.2 | Virtual Product Photography Director | `virtual-product-photography-director` | `plan.blueprint` | blueprint | none | low |
| 13.3 | Product Variant Generator | `product-variant-generator` | `engine.job` | engine | conditional | medium |
| 13.4 | Lifestyle Placement Specialist | `lifestyle-placement-specialist` | `image.composite` | local | none | low |
| 13.5 | Advertising Concept Generator | `advertising-concept-generator` | `plan.blueprint` | blueprint | none | low |
| 13.6 | Marketplace Compliance Reviewer | `marketplace-compliance-reviewer` | `image.delivery_audit` | local | none | low |
| 13.7 | Catalog Consistency Auditor | `catalog-consistency-auditor` | `image.consistency_report` | local | none | low |
| 13.8 | Conversion Creative Variant Maker | `conversion-creative-variant-maker` | `prompt.variants` | local | none | low |
| 14.1 | Interior Concept Visualizer | `interior-concept-visualizer` | `prompt.build` | local | none | low |
| 14.2 | Exterior Massing Visualizer | `exterior-massing-visualizer` | `prompt.build` | local | none | low |
| 14.3 | Floor Plan to Perspective Designer | `floor-plan-to-perspective-designer` | `plan.blueprint` | blueprint | none | low |
| 14.4 | Material and Finish Variant Maker | `material-and-finish-variant-maker` | `engine.job` | engine | conditional | medium |
| 14.5 | Furniture Staging Specialist | `furniture-staging-specialist` | `plan.blueprint` | blueprint | none | low |
| 14.6 | Landscape Visualization Creator | `landscape-visualization-creator` | `prompt.build` | local | none | low |
| 14.7 | Real Estate Enhancement Editor | `real-estate-enhancement-editor` | `image.color_adjust` | local | none | low |
| 14.8 | Spatial Consistency Auditor | `spatial-consistency-auditor` | `plan.blueprint` | blueprint | none | low |
| 15.1 | Scientific Illustration Planner | `scientific-illustration-planner` | `plan.blueprint` | blueprint | none | low |
| 15.2 | Medical Visualization Safety Reviewer | `medical-visualization-safety-reviewer` | `policy.run` | policy | none | low |
| 15.3 | Technical Cutaway Creator | `technical-cutaway-creator` | `engine.job` | engine | conditional | low |
| 15.4 | Dataset Visualization Asset Maker | `dataset-visualization-asset-maker` | `plan.blueprint` | blueprint | none | low |
| 15.5 | Microscopy Image Enhancement Guide | `microscopy-image-enhancement-guide` | `image.denoise_grain` | local | none | low |
| 15.6 | Satellite and Geospatial Image Stylist | `satellite-and-geospatial-image-stylist` | `image.color_adjust` | local | none | low |
| 15.7 | Patent Figure Preparation Guide | `patent-figure-preparation-guide` | `image.edge_map` | local | none | low |
| 15.8 | Technical Accuracy Auditor | `technical-accuracy-auditor` | `image.delivery_audit` | local | none | low |
| 16.1 | Dataset Curation Specialist | `dataset-curation-specialist` | `image.dataset_curate` | local | none | low |
| 16.2 | Captioning Pipeline Designer | `captioning-pipeline-designer` | `prompt.caption` | local | none | low |
| 16.3 | LoRA Training Specialist | `lora-training-specialist` | `compute.training_plan` | local | none | low |
| 16.4 | DreamBooth Training Specialist | `dreambooth-training-specialist` | `compute.training_plan` | local | none | low |
| 16.5 | Textual Inversion Trainer | `textual-inversion-trainer` | `compute.training_plan` | local | none | low |
| 16.6 | Fine-Tune Evaluation Designer | `fine-tune-evaluation-designer` | `prompt.eval_suite` | local | none | low |
| 16.7 | Overfitting and Memorization Auditor | `overfitting-and-memorization-auditor` | `image.near_duplicate` | local | none | low |
| 16.8 | Model Merge and Blend Evaluator | `model-merge-and-blend-evaluator` | `compute.sweep_grid` | local | none | low |
| 17.1 | Training Data Rights Reviewer | `training-data-rights-reviewer` | `policy.manifest_check` | local | none | low |
| 17.2 | Copyright Risk Screener | `copyright-risk-screener` | `policy.run` | policy | none | low |
| 17.3 | Artist Style Request Rewriter | `artist-style-request-rewriter` | `policy.rewrite_style` | policy | none | low |
| 17.4 | Consent and Likeness Checker | `consent-and-likeness-checker` | `policy.run` | policy | none | medium |
| 17.5 | Provenance Metadata Manager | `provenance-metadata-manager` | `image.metadata_sidecar` | local | none | low |
| 17.6 | Synthetic Media Disclosure Writer | `synthetic-media-disclosure-writer` | `policy.disclosure` | policy | none | low |
| 17.7 | Privacy Redaction Specialist | `privacy-redaction-specialist` | `image.redact` | local | none | low |
| 17.8 | Dataset Bias Auditor | `dataset-bias-auditor` | `policy.manifest_check` | local | none | low |
| 18.1 | Deepfake Risk Assessor | `deepfake-risk-assessor` | `policy.run` | policy | none | high |
| 18.2 | Image Authenticity Analyst | `image-authenticity-analyst` | `image.authenticity` | heuristic | none | low |
| 18.3 | Content Safety Classifier | `content-safety-classifier` | `policy.run` | policy | none | high |
| 18.4 | Age-Sensitive Image Reviewer | `age-sensitive-image-reviewer` | `policy.run` | policy | none | high |
| 18.5 | Misleading Edit Detector | `misleading-edit-detector` | `policy.run` | policy | none | medium |
| 18.6 | Watermark and Disclosure Planner | `watermark-and-disclosure-planner` | `image.text_overlay` | local | none | low |
| 18.7 | Adversarial Prompt Defense Designer | `adversarial-prompt-defense-designer` | `policy.run` | policy | none | high |
| 18.8 | Safe Release Checklist Builder | `safe-release-checklist-builder` | `policy.release_gate` | policy | required | high |
| 19.1 | Visual Quality Reviewer | `visual-quality-reviewer` | `image.quality_metrics` | heuristic | none | low |
| 19.2 | Prompt Adherence Evaluator | `prompt-adherence-evaluator` | `prompt.adherence` | heuristic | none | low |
| 19.3 | Aesthetic Benchmark Designer | `aesthetic-benchmark-designer` | `compute.blind_pairs` | local | none | low |
| 19.4 | Artifact Detection Specialist | `artifact-detection-specialist` | `image.artifact_scan` | heuristic | none | low |
| 19.5 | Image Pair Comparator | `image-pair-comparator` | `image.compare` | local | none | low |
| 19.6 | Regression Test Builder | `regression-test-builder` | `image.compare` | local | none | low |
| 19.7 | Human Review Protocol Designer | `human-review-protocol-designer` | `plan.blueprint` | blueprint | none | low |
| 19.8 | Delivery Readiness Auditor | `delivery-readiness-auditor` | `image.delivery_audit` | local | none | low |
| 20.1 | Image Generation API Integrator | `image-generation-api-integrator` | `provider.check` | engine | none | low |
| 20.2 | Batch Prompt Runner | `batch-prompt-runner` | `provider.batch` | engine | conditional | medium |
| 20.3 | ComfyUI API Automator | `comfyui-api-automator` | `engine.comfy_graph` | engine | conditional | low |
| 20.4 | Asset Naming and Metadata Automator | `asset-naming-and-metadata-automator` | `repro.asset_name` | local | none | low |
| 20.5 | GPU Queue Orchestrator | `gpu-queue-orchestrator` | `compute.gpu_queue` | local | none | low |
| 20.6 | Cost and Throughput Optimizer | `cost-and-throughput-optimizer` | `compute.throughput` | local | none | low |
| 20.7 | Automated QA Pipeline Builder | `automated-qa-pipeline-builder` | `image.qa_pipeline` | local | none | low |
| 20.8 | Human-in-the-Loop Image Workflow Designer | `human-in-the-loop-image-workflow-designer` | `policy.workflow` | policy | conditional | medium |
| 21.1 | VRAM Budget Planner | `vram-budget-planner` | `compute.vram_budget` | local | none | low |
| 21.2 | Low-Memory Diffusion Optimizer | `low-memory-diffusion-optimizer` | `compute.vram_budget` | local | none | low |
| 21.3 | Inference Speed Tuner | `inference-speed-tuner` | `compute.throughput` | local | none | low |
| 21.4 | GPU Compatibility Troubleshooter | `gpu-compatibility-troubleshooter` | `system.probe` | local | none | low |
| 21.5 | Model Storage Manager | `model-storage-manager` | `system.model_storage` | local | none | low |
| 21.6 | Environment Reproducibility Builder | `environment-reproducibility-builder` | `system.env_lock` | local | none | low |
| 21.7 | Thermal and Reliability Monitor | `thermal-and-reliability-monitor` | `system.probe` | local | none | low |
| 21.8 | Cloud GPU Deployment Planner | `cloud-gpu-deployment-planner` | `compute.cloud_budget` | local | none | low |
| 22.1 | Game Concept Asset Maker | `game-concept-asset-maker` | `engine.job` | engine | conditional | low |
| 22.2 | Texture and Material Generator | `texture-and-material-generator` | `image.tiling` | local | none | low |
| 22.3 | Sprite and Pixel Art Generator | `sprite-and-pixel-art-generator` | `image.pixel_art` | local | none | low |
| 22.4 | Comic and Storyboard Creator | `comic-and-storyboard-creator` | `plan.blueprint` | blueprint | none | low |
| 22.5 | Book Illustration Planner | `book-illustration-planner` | `plan.blueprint` | blueprint | none | low |
| 22.6 | Thumbnail and Keyframe Generator | `thumbnail-and-keyframe-generator` | `image.responsive_crop` | local | none | low |
| 22.7 | Icon Family Designer | `icon-family-designer` | `image.export` | local | none | low |
| 22.8 | Pattern and Surface Design Maker | `pattern-and-surface-design-maker` | `image.tiling` | local | none | low |
| 23.1 | Alt Text Writer for Images | `alt-text-writer-for-images` | `prompt.alt_text` | heuristic | none | low |
| 23.2 | Color Contrast Accessibility Reviewer | `color-contrast-accessibility-reviewer` | `compute.contrast` | local | none | low |
| 23.3 | Localization-Safe Visual Designer | `localization-safe-visual-designer` | `plan.blueprint` | blueprint | none | low |
| 23.4 | Cultural Context Reviewer | `cultural-context-reviewer` | `policy.run` | policy | none | low |
| 23.5 | Multi-Format Export Specialist | `multi-format-export-specialist` | `image.export` | local | none | low |
| 23.6 | Responsive Crop Planner | `responsive-crop-planner` | `image.responsive_crop` | local | none | low |
| 23.7 | Image Optimization Specialist | `image-optimization-specialist` | `image.export` | local | none | low |
| 23.8 | Archive and Handoff Manager | `archive-and-handoff-manager` | `repro.handoff` | local | none | low |

## 12. Category list

1. Foundations of Generative Imaging
2. Prompt Design and Creative Direction
3. Stable Diffusion Core Operations
4. Diffusion Control and Conditioning
5. Text-to-Image Creation
6. Single-Image Editing
7. Multi-Image Editing and Composition
8. Masking, Selections, and Compositing
9. Upscaling, Enhancement, and Restoration
10. Color, Lighting, and Finishing
11. Typography, Logos, and Graphic Design
12. Characters, Faces, and People
13. Products, Commerce, and Advertising
14. Architecture, Interiors, and Spatial Design
15. Scientific, Medical, and Technical Imaging
16. Training, Fine-Tuning, and Personalization
17. Data Governance, Rights, and Provenance
18. Safety, Authenticity, and Misuse Prevention
19. Quality Assurance and Evaluation
20. Automation, APIs, and Batch Pipelines
21. Performance and Hardware Optimization
22. Specialized Media and Asset Production
23. Accessibility, Localization, and Delivery
