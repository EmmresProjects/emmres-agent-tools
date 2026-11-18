# AI Image Production Toolkit

One unified Skill Tool that covers every capability in the *AI Image Skill Catalog*:
**23 categories, 184 capabilities**, from prompt design and Stable Diffusion
operations through editing, compositing, colour, typography, commerce, training,
governance, safety, QA, automation, performance and delivery.

Everything runs from a single entry point with the Python standard library.
The optional extras (Pillow, NumPy) are declared in `tools-requirements.txt` and
unlock the pixel-level analysis capabilities; without them those capabilities
report a *blocked integration* instead of inventing numbers.

---

## 1. Purpose and scope

This Skill Tool exists to turn a creative or production brief into
**engine-ready artefacts and auditable decisions**:

* **Prompts and parameters** that can be pasted straight into an engine
  (A1111 parameter blocks, ComfyUI graphs, sampler/scheduler/step recommendations).
* **Measurements** taken from real image files (exposure, contrast, sharpness,
  noise, banding, colour casts, crop geometry, contrast ratios, print resolution).
* **Plans and checklists** for the work that surrounds generation: setup, GPU
  budgets, cost estimates, batch manifests, queue schedules, training
  configurations, rights reviews, consent checks, safety screens, release
  checklists and QA pipelines.
* **Provenance and disclosure** records so a generated asset can be traced and
  correctly labelled.

### What it deliberately does not do

This Skill Tool **does not render images**. It has no diffusion model, no GPU
requirement and no model weights. Capabilities that would need a diffusion
engine produce the complete, engine-ready package instead - prompt, negative
prompt, sampler, scheduler, steps, CFG, seed, A1111 parameters, ComfyUI workflow
JSON, output manifest and QA checklist - and state plainly that the rendering
step is yours. Nothing here pretends to have produced a pixel it did not
measure.

It also never embeds credentials, never hardcodes a private endpoint and never
executes shell commands or third-party code on your behalf.

---

## 2. Requirements and setup

| Item | Requirement |
| --- | --- |
| Python | 3.10 or newer (developed and tested on CPython 3.13) |
| Required packages | none - the standard library is enough for every capability |
| Optional packages | `pillow>=10.0`, `numpy>=1.24` (see `tools-requirements.txt`) |
| Network | not required; no capability makes a network call |
| Credentials | none; API tokens are read from the environment by your own client |
| GPU | not required; VRAM and cost figures are planning estimates |

```bash
# 1. Work inside the Skill Tool folder
cd ai-image-production-toolkit

# 2. (optional) install the accelerated extras
python3 -m pip install -r tools-requirements.txt

# 3. confirm the environment
python3 tools-image-toolkit.py doctor
```

`doctor` prints the interpreter version, whether Pillow and NumPy are present,
the number of registered capabilities and the packaged files.

---

## 3. Quick start

```bash
# list everything the tool can do
python3 tools-image-toolkit.py capabilities

# the 23 categories and their capability ids
python3 tools-image-toolkit.py categories

# find one capability
python3 tools-image-toolkit.py capabilities --search lora

# run one capability and read the report
python3 tools-image-toolkit.py run prompt-design.prompt-architect \
    --set brief="launch an autumn walking-boot line for a coastal trail brand"

# machine-readable output
python3 tools-image-toolkit.py run prompt-design.prompt-architect \
    --set brief="launch an autumn walking-boot line" --json

# preview without executing (no files are written)
python3 tools-image-toolkit.py run automation-batch.image-generation-api-integrator --dry-run

# analyse an image you already have
python3 tools-image-toolkit.py run quality-evaluation.visual-quality-reviewer \
    --set image=/absolute/path/to/photo.png --out-dir output
```

Relative input paths are resolved against the current working directory first and
against the Skill Tool folder second, so the examples below work from the
Skill Tool folder or from the parent package root.

---

## 4. Command reference

| Command | Purpose |
| --- | --- |
| `python3 tools-image-toolkit.py --help` | top-level help |
| `python3 tools-image-toolkit.py capabilities` | every capability id, grouped by category |
| `python3 tools-image-toolkit.py capabilities --category <name or number>` | filter by category |
| `python3 tools-image-toolkit.py capabilities --search <term>` | filter by id, name, description |
| `python3 tools-image-toolkit.py capabilities --kind <kind>` | filter by kind |
| `python3 tools-image-toolkit.py capabilities --risk <risk>` | filter by risk |
| `python3 tools-image-toolkit.py capabilities --json` | machine-readable capability list |
| `python3 tools-image-toolkit.py capabilities --verbose` | add kinds, risks and parameters |
| `python3 tools-image-toolkit.py categories` | the 23 categories with their capability ids |
| `python3 tools-image-toolkit.py categories --json` | machine-readable category index |
| `python3 tools-image-toolkit.py run <capability-id>` | run one capability |
| `python3 tools-image-toolkit.py run <id> --set key=value` | set a parameter (repeatable) |
| `python3 tools-image-toolkit.py run <id> --param-file <file.json>` | set parameters from JSON |
| `python3 tools-image-toolkit.py run <id> --out-dir <dir>` | choose the output directory |
| `python3 tools-image-toolkit.py run <id> --json` | JSON envelope only |
| `python3 tools-image-toolkit.py run <id> --dry-run` | validate and show the plan, write nothing |
| `python3 tools-image-toolkit.py run <id> --yes` | approve a destructive or external action |
| `python3 tools-image-toolkit.py run <id> --verbose` | progress notes on stderr |
| `python3 tools-image-toolkit.py doctor` | runtime, optional dependencies and packaged files |
| `python3 tools-image-toolkit.py --version` | version string |

### Parameter values

`--set key=value` values are coerced:

| Written as | Becomes |
| --- | --- |
| `42` | integer |
| `0.7` | float |
| `true`, `yes`, `on` | boolean `True` |
| `false`, `no`, `off` | boolean `False` |
| `null`, `none` | `None` |
| `["a","b"]`, `{"k":1}` | parsed JSON |
| anything else | the string itself |

Quote values that contain spaces or shell metacharacters:
`--set brief="a lighthouse in fog"`.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success |
| `2` | usage error (no command given) |
| `3` | invalid input (unknown capability, missing or bad parameter) |
| `4` | approval required (a gated action was attempted without `--yes`) |
| `5` | the capability ran but reported errors, or an unexpected tool error |
| `6` | blocked integration (an optional dependency or service is unavailable) |

---

## 5. Inputs, outputs, schemas and configuration

### 5.1 Inputs

* **Capability id** - the stable identifier from `capabilities` (for example
  `quality-evaluation.visual-quality-reviewer`).
* **Parameters** - supplied with `--set key=value` (repeatable) or
  `--param-file file.json` (a JSON object). `--set` wins over `--param-file`.
* **Input files** - paths to images, datasets, prompt lists, job lists,
  workflows, suites or logs. Path parameters are validated: a missing file is
  an invalid-input error, not a silent fallback.

### 5.2 Outputs

Every successful run writes into the output directory (`--out-dir`, default
`output`):

| File | Contents |
| --- | --- |
| `report.md` | the human-readable report for the run |
| `result.json` | the full JSON envelope for the run |
| capability-specific files | for example `batch-manifest.json`, `provenance.json`, `*-manifest.json`, `curated-dataset.jsonl`, generated PNGs, SVG mockups |
| `placeholder-inputs/` | synthetic placeholder images, present only when an optional image input was not supplied |

### 5.3 JSON envelope

Every run returns the same envelope:

```json
{
  "ok": true,
  "status": "completed",
  "tool": "ai-image-production-toolkit",
  "version": "1.0.0",
  "capability": "quality-evaluation.visual-quality-reviewer",
  "category": "Quality Assurance and Evaluation",
  "skill": "Visual Quality Reviewer",
  "kind": "audit",
  "risk": "safe",
  "summary": "Quality score 87.8/100 (pass).",
  "inputs": { "image": "asset-sample-image.png" },
  "data": { },
  "report": "# Visual quality review\n\n...",
  "artifacts": ["output/report.md", "output/result.json"],
  "warnings": [],
  "metrics": { },
  "approval": { "required": false, "granted": false },
  "duration_ms": 42
}
```

`status` is one of `completed`, `completed_with_errors`, `approval_required`,
`invalid_input`, `blocked` or `error`. `data` and `metrics` are
capability-specific; `artifacts` lists every file the run produced, relative to
the current directory.

### 5.4 Configuration

There is no configuration file to edit and no environment variable is required.
Everything is configurable per run through parameters, and everything that
varies by machine is read from the environment:

| Setting | Where it lives |
| --- | --- |
| output location | `--out-dir` or the `out_dir` parameter |
| models, samplers, schedulers, steps, CFG, seed | parameters, backed by `asset-model-profiles.json` |
| prompt vocabulary and style rewrite map | `asset-style-lexicon.json` |
| negative prompt targets and artefact signatures | `asset-negative-prompts.json` |
| quality rubric, delivery specs, print rules | `asset-quality-rubric.json` |
| safety, rights, consent, disclosure and release rules | `asset-safety-rules.json` |
| A1111 / ComfyUI templates, platform sizes, crop ratios | `asset-workflow-templates.json` |
| sample prompts, regression cases, caption corpus | `asset-sample-prompts.json` |
| hosted API token | your own environment variable, read by your own client (never stored here) |

### 5.5 Timeouts, retries, cancellation, cleanup and recovery

| Concern | Behaviour |
| --- | --- |
| Timeouts | No capability makes a network call, so there is no network timeout to honour. The API capabilities accept a `timeout` parameter and write it into the request specification they emit (default 30 s, ComfyUI 120 s) and into the reference client, which applies it to every call. |
| Retries | The API specification defines 2 retries by default with exponential backoff from 1 s capped at 30 s, retrying on 429/5xx and connection resets, and never retrying 400/401/403/404/422. The reference client implements exactly that. The same seed is reused on a retry so a retry cannot create a duplicate billed image. |
| Cancellation | Pressing Ctrl-C stops the run cleanly: the CLI catches `KeyboardInterrupt`, prints `interrupted` on stderr and exits with code 5. Nothing is left half-written, because every artefact is written as a complete file. |
| Cleanup | The tool never deletes input files. Output directories are written to and never cleared automatically. The only in-place operation - renaming files with `--set copy=false` - is gated behind `--yes` and never overwrites an existing file. |
| Recovery | Every run is idempotent for the same inputs. `batch-prompt-runner` keeps `batch-state.json` and skips completed job ids, so an interrupted batch resumes. A failed run leaves `result.json` in the output directory describing the failure; re-run with the corrected input rather than deleting the directory. |

---

## 6. Safety, approvals and governance

### 6.1 Approval gate

Capabilities classified `destructive`, `external` or `publishing` refuse to run
without an explicit approval:

```bash
# refused, exit code 4, nothing is written
python3 tools-image-toolkit.py run governance-provenance.privacy-redaction-specialist

# approved
python3 tools-image-toolkit.py run governance-provenance.privacy-redaction-specialist --yes
```

The gated capabilities are:

| Capability | Risk | Why it is gated |
| --- | --- | --- |
| `governance-provenance.privacy-redaction-specialist` | destructive | redaction cannot be undone |
| `automation-batch.image-generation-api-integrator` | external | talks to a hosted service |
| `automation-batch.comfyui-api-automator` | external | submits jobs to a remote ComfyUI server |

`asset-naming-and-metadata-automator` has a second gate: renaming files in
place (`--set copy=false`) asks for `--yes` even though the capability as a
whole is not gated, because in-place renames cannot be undone.

### 6.2 Rules the tool enforces in code

* No capability executes shell commands, evaluates third-party code or loads
  plugins.
* No capability makes a network call. The API capabilities emit a request
  specification and a reference client instead; the token stays in your
  environment.
* No credential, private endpoint or paid service is embedded anywhere.
* Rights, consent, copyright, safety and age screening are heuristic triage
  aids. A clean screen is **not** a legal opinion and never certifies content
  as safe; the reports say so explicitly.
* Artist-name requests are rewritten into neutral visual attributes using the
  packaged rewrite map rather than being silently passed through.
* Destructive operations write to a new file unless you explicitly ask for an
  in-place change, and the in-place path requires `--yes`.

---

## 7. Complete capability reference

All 184 capabilities, grouped by catalog category, each with its kind, risk and
a runnable example. Commands are shown from inside the Skill Tool folder.


### 1. Foundations of Generative Imaging

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `foundations.image-training-concepts-explainer` | Image Training Concepts Explainer | explain | safe |
| `foundations.pixel-and-tensor-interpreter` | Pixel and Tensor Interpreter | compute | safe |
| `foundations.latent-space-navigator` | Latent Space Navigator | compute | safe |
| `foundations.diffusion-process-demonstrator` | Diffusion Process Demonstrator | artifact | write |
| `foundations.prompt-encoding-specialist` | Prompt Encoding Specialist | analysis | safe |
| `foundations.cross-attention-visualizer` | Cross-Attention Visualizer | analysis | safe |
| `foundations.generative-model-comparator` | Generative Model Comparator | analysis | safe |
| `foundations.image-synthesis-glossary-builder` | Image Synthesis Glossary Builder | artifact | write |

**`foundations.image-training-concepts-explainer`** - Explain how image-text datasets, pixel tensors, feature learning, and optimization teach a model visual patterns without implying that the model stores a browsable library of its training images.

```bash
python3 tools-image-toolkit.py run foundations.image-training-concepts-explainer --out-dir output
```

All parameters are optional: `topic`, `audience`.

**`foundations.pixel-and-tensor-interpreter`** - Translate width, height, channels, RGB values, bit depth, normalization, and tensor shapes into plain-language explanations and implementation-ready specifications.

```bash
python3 tools-image-toolkit.py run foundations.pixel-and-tensor-interpreter --set width="1024" --set height="1024" --out-dir output
```

Required: `width`, `height`. Optional: `channels`, `bit_depth`, `latent_divisor`, `batch_size`.

**`foundations.latent-space-navigator`** - Explain and apply latent representations, semantic directions, interpolation, vector arithmetic, and neighborhood search for controlled visual variation.

```bash
python3 tools-image-toolkit.py run foundations.latent-space-navigator --set concepts='["dawn","dusk"]' --out-dir output
```

Required: `concepts`. Optional: `operation`, `steps`.

**`foundations.diffusion-process-demonstrator`** - Describe forward noising and reverse denoising, timestep schedules, score prediction, and iterative refinement through accurate analogies and technical diagrams.

```bash
python3 tools-image-toolkit.py run foundations.diffusion-process-demonstrator --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `steps`, `schedule`, `keep_frames`.

**`foundations.prompt-encoding-specialist`** - Explain tokenization, text encoders, embeddings, attention, truncation, weighting, and how prompt wording becomes conditioning signals.

```bash
python3 tools-image-toolkit.py run foundations.prompt-encoding-specialist --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `negative`, `model`, `width`, `height`, `steps`, `cfg`, `sampler`, `scheduler`, `seed`.

**`foundations.cross-attention-visualizer`** - Map prompt tokens to image regions, identify weak token binding, and propose prompt or conditioning changes that improve object-attribute relationships.

```bash
python3 tools-image-toolkit.py run foundations.cross-attention-visualizer --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `regions`.

**`foundations.generative-model-comparator`** - Compare diffusion, GAN, autoregressive, flow-matching, and hybrid image models by quality, speed, controllability, hardware needs, and failure modes.

```bash
python3 tools-image-toolkit.py run foundations.generative-model-comparator --out-dir output
```

All parameters are optional: `priority`, `models`.

**`foundations.image-synthesis-glossary-builder`** - Create precise, audience-aware glossaries for concepts such as latent, seed, sampler, CFG, VAE, checkpoint, LoRA, ControlNet, inpainting, and upscaling. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 6

```bash
python3 tools-image-toolkit.py run foundations.image-synthesis-glossary-builder --out-dir output
```

All parameters are optional: `terms`, `audience`.

### 2. Prompt Design and Creative Direction

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `prompt-design.prompt-architect` | Prompt Architect | artifact | write |
| `prompt-design.negative-prompt-engineer` | Negative Prompt Engineer | artifact | write |
| `prompt-design.prompt-debugger` | Prompt Debugger | analysis | safe |
| `prompt-design.prompt-variant-generator` | Prompt Variant Generator | artifact | write |
| `prompt-design.art-direction-translator` | Art Direction Translator | artifact | write |
| `prompt-design.prompt-weighting-specialist` | Prompt Weighting Specialist | artifact | write |
| `prompt-design.seed-and-reproducibility-manager` | Seed and Reproducibility Manager | artifact | write |
| `prompt-design.prompt-library-curator` | Prompt Library Curator | artifact | write |

**`prompt-design.prompt-architect`** - Convert a visual goal into a structured prompt covering subject, environment, composition, lens, lighting, palette, texture, style, quality, and exclusions.

```bash
python3 tools-image-toolkit.py run prompt-design.prompt-architect --set brief="launch an autumn walking-boot line for a coastal trail brand" --out-dir output
```

Required: `brief`. Optional: `subject`, `style`, `audience`, `model`.

**`prompt-design.negative-prompt-engineer`** - Identify likely artifacts and build concise negative constraints without suppressing desirable detail, diversity, or natural texture.

```bash
python3 tools-image-toolkit.py run prompt-design.negative-prompt-engineer --out-dir output
```

All parameters are optional: `target`, `scene`, `strictness`.

**`prompt-design.prompt-debugger`** - Diagnose ignored objects, merged concepts, bad anatomy, weak composition, style drift, and text failures, then revise one variable at a time.

```bash
python3 tools-image-toolkit.py run prompt-design.prompt-debugger --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `observed`.

**`prompt-design.prompt-variant-generator`** - Produce controlled prompt families that vary only selected dimensions such as camera angle, era, weather, medium, or colorway.

```bash
python3 tools-image-toolkit.py run prompt-design.prompt-variant-generator --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `dimensions`, `variants_per_dimension`.

**`prompt-design.art-direction-translator`** - Turn mood boards, brand language, scripts, or abstract creative briefs into concrete visual attributes and generation instructions.

```bash
python3 tools-image-toolkit.py run prompt-design.art-direction-translator --set mood="serene" --out-dir output
```

Required: `mood`. Optional: `medium`.

**`prompt-design.prompt-weighting-specialist`** - Apply token emphasis, regional prompting, BREAK-style separation, and model-specific syntax while avoiding unstable over-weighting.

```bash
python3 tools-image-toolkit.py run prompt-design.prompt-weighting-specialist --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `syntax`, `max_weight`.

**`prompt-design.seed-and-reproducibility-manager`** - Record seeds, model versions, dimensions, samplers, steps, guidance, and conditioning inputs so results can be repeated and audited.

```bash
python3 tools-image-toolkit.py run prompt-design.seed-and-reproducibility-manager --out-dir output
```

All parameters are optional: `prompt`, `model`, `seed`, `manifest`.

**`prompt-design.prompt-library-curator`** - Organize reusable prompts by intent, subject, style, model compatibility, tested settings, and known failure conditions. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 7

```bash
python3 tools-image-toolkit.py run prompt-design.prompt-library-curator --out-dir output
```

All parameters are optional: `library`, `query`, `entries`.

### 3. Stable Diffusion Core Operations

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `stable-diffusion.stable-diffusion-setup-guide` | Stable Diffusion Setup Guide | plan | safe |
| `stable-diffusion.checkpoint-selection-advisor` | Checkpoint Selection Advisor | analysis | safe |
| `stable-diffusion.sampler-and-scheduler-tuner` | Sampler and Scheduler Tuner | plan | write |
| `stable-diffusion.vae-manager` | VAE Manager | diagnose | safe |
| `stable-diffusion.sdxl-workflow-specialist` | SDXL Workflow Specialist | plan | write |
| `stable-diffusion.stable-diffusion-3-workflow-specialist` | Stable Diffusion 3 Workflow Specialist | plan | write |
| `stable-diffusion.webui-workflow-builder` | WebUI Workflow Builder | artifact | write |
| `stable-diffusion.comfyui-graph-designer` | ComfyUI Graph Designer | artifact | write |

**`stable-diffusion.stable-diffusion-setup-guide`** - Plan local or hosted installations for Stable Diffusion interfaces, Python environments, GPU drivers, model storage, and safe update practices.

```bash
python3 tools-image-toolkit.py run stable-diffusion.stable-diffusion-setup-guide --out-dir output
```

All parameters are optional: `gpu_vram_gb`, `interface`, `os`.

**`stable-diffusion.checkpoint-selection-advisor`** - Match base checkpoints and model families to realism, illustration, anime, product, architecture, typography, or speed requirements.

```bash
python3 tools-image-toolkit.py run stable-diffusion.checkpoint-selection-advisor --out-dir output
```

All parameters are optional: `goal`, `budget`.

**`stable-diffusion.sampler-and-scheduler-tuner`** - Select and test samplers, schedulers, step counts, denoising strength, and guidance values using repeatable comparison grids.

```bash
python3 tools-image-toolkit.py run stable-diffusion.sampler-and-scheduler-tuner --out-dir output
```

All parameters are optional: `model`, `goal`, `sampler`.

**`stable-diffusion.vae-manager`** - Choose, attach, test, and troubleshoot variational autoencoders for color, contrast, detail, decoding, and black-image issues.

```bash
python3 tools-image-toolkit.py run stable-diffusion.vae-manager --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `symptom`.

**`stable-diffusion.sdxl-workflow-specialist`** - Build SDXL base, refiner, and high-resolution workflows with appropriate dimensions, conditioning, memory controls, and prompt styles.

```bash
python3 tools-image-toolkit.py run stable-diffusion.sdxl-workflow-specialist --out-dir output
```

All parameters are optional: `aspect`, `refiner`.

**`stable-diffusion.stable-diffusion-3-workflow-specialist`** - Design text-to-image and image-conditioning workflows for Stable Diffusion 3-class architectures, including encoder and memory considerations.

```bash
python3 tools-image-toolkit.py run stable-diffusion.stable-diffusion-3-workflow-specialist --out-dir output
```

All parameters are optional: `variant`.

**`stable-diffusion.webui-workflow-builder`** - Create reproducible Automatic1111 or Forge workflows using presets, scripts, extensions, parameter captures, and batch generation.

```bash
python3 tools-image-toolkit.py run stable-diffusion.webui-workflow-builder --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `negative`, `model`.

**`stable-diffusion.comfyui-graph-designer`** - Build modular node graphs for generation, refinement, ControlNet, IP-Adapter, inpainting, upscaling, metadata, and reusable subgraphs. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 8

```bash
python3 tools-image-toolkit.py run stable-diffusion.comfyui-graph-designer --out-dir output
```

All parameters are optional: `template`, `prompt`.

### 4. Diffusion Control and Conditioning

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `control-conditioning.controlnet-orchestrator` | ControlNet Orchestrator | plan | write |
| `control-conditioning.pose-conditioning-specialist` | Pose Conditioning Specialist | analyze | safe |
| `control-conditioning.depth-guided-composer` | Depth-Guided Composer | analyze | safe |
| `control-conditioning.edge-and-line-art-controller` | Edge and Line-Art Controller | analyze | safe |
| `control-conditioning.segmentation-guided-generator` | Segmentation-Guided Generator | artifact | write |
| `control-conditioning.ip-adapter-specialist` | IP-Adapter Specialist | plan | write |
| `control-conditioning.regional-prompting-composer` | Regional Prompting Composer | artifact | write |
| `control-conditioning.multi-control-conflict-resolver` | Multi-Control Conflict Resolver | analysis | safe |

**`control-conditioning.controlnet-orchestrator`** - Choose and combine edge, depth, pose, line-art, normal, segmentation, tile, and scribble controls with calibrated strengths and start-end ranges.

```bash
python3 tools-image-toolkit.py run control-conditioning.controlnet-orchestrator --out-dir output
```

All parameters are optional: `goal`, `controls`.

**`control-conditioning.pose-conditioning-specialist`** - Extract, correct, and apply human or creature poses while protecting limb integrity, camera perspective, and intended gesture.

```bash
python3 tools-image-toolkit.py run control-conditioning.pose-conditioning-specialist --out-dir output
```

All parameters are optional: `pose`, `joints`.

**`control-conditioning.depth-guided-composer`** - Use monocular depth maps to preserve spatial layout, foreground separation, scale, and camera geometry during restyling.

```bash
python3 tools-image-toolkit.py run control-conditioning.depth-guided-composer --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `depth`.

**`control-conditioning.edge-and-line-art-controller`** - Use Canny, HED, soft-edge, or line-art maps to retain silhouettes and structure without over- constraining texture.

```bash
python3 tools-image-toolkit.py run control-conditioning.edge-and-line-art-controller --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `edges`, `mode`.

**`control-conditioning.segmentation-guided-generator`** - Assign regions or semantic classes to control placement, materials, colors, and scene composition.

```bash
python3 tools-image-toolkit.py run control-conditioning.segmentation-guided-generator --out-dir output
```

All parameters are optional: `segmentation`, `classes`.

**`control-conditioning.ip-adapter-specialist`** - Use reference-image embeddings for style, composition, face, or object guidance while balancing prompt control and reference strength.

```bash
python3 tools-image-toolkit.py run control-conditioning.ip-adapter-specialist --set references='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

All parameters are optional: `references`, `role`, `prompt_weight`.

**`control-conditioning.regional-prompting-composer`** - Apply masks, attention regions, or area conditioning to give different parts of one image distinct subjects, styles, and constraints.

```bash
python3 tools-image-toolkit.py run control-conditioning.regional-prompting-composer --out-dir output
```

All parameters are optional: `regions`, `layout`.

**`control-conditioning.multi-control-conflict-resolver`** - Diagnose competing conditioning signals and rebalance weights, control order, masks, and denoising windows. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 9

```bash
python3 tools-image-toolkit.py run control-conditioning.multi-control-conflict-resolver --out-dir output
```

All parameters are optional: `controls`.

### 5. Text-to-Image Creation

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `text-to-image.concept-art-generator` | Concept Art Generator | artifact | write |
| `text-to-image.photorealistic-scene-creator` | Photorealistic Scene Creator | artifact | write |
| `text-to-image.illustration-style-creator` | Illustration Style Creator | artifact | write |
| `text-to-image.product-visualization-maker` | Product Visualization Maker | artifact | write |
| `text-to-image.architectural-visualization-maker` | Architectural Visualization Maker | artifact | write |
| `text-to-image.food-and-beverage-visual-maker` | Food and Beverage Visual Maker | artifact | write |
| `text-to-image.fashion-concept-generator` | Fashion Concept Generator | artifact | write |
| `text-to-image.abstract-visual-synthesizer` | Abstract Visual Synthesizer | artifact | write |

**`text-to-image.concept-art-generator`** - Create environment, character, prop, vehicle, and creature concepts with iteration sheets and production-ready visual notes.

```bash
python3 tools-image-toolkit.py run text-to-image.concept-art-generator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.photorealistic-scene-creator`** - Specify credible lenses, exposure, lighting, materials, depth of field, and natural imperfections for realistic images.

```bash
python3 tools-image-toolkit.py run text-to-image.photorealistic-scene-creator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.illustration-style-creator`** - Generate editorial, children's, fantasy, technical, comic, ink, watercolor, and vector-like illustration directions without copying living artists.

```bash
python3 tools-image-toolkit.py run text-to-image.illustration-style-creator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.product-visualization-maker`** - Create clean product concepts, pack shots, exploded views, material variants, and lifestyle scenes with geometry and branding safeguards.

```bash
python3 tools-image-toolkit.py run text-to-image.product-visualization-maker --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.architectural-visualization-maker`** - Generate exterior, interior, landscape, and urban concepts with coherent perspective, circulation, materials, and daylight.

```bash
python3 tools-image-toolkit.py run text-to-image.architectural-visualization-maker --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.food-and-beverage-visual-maker`** - Create appetizing, physically plausible food imagery with accurate textures, styling, table settings, and commercial lighting.

```bash
python3 tools-image-toolkit.py run text-to-image.food-and-beverage-visual-maker --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.fashion-concept-generator`** - Develop garments, accessories, textiles, silhouettes, colorways, and lookbook scenes while preserving construction logic.

```bash
python3 tools-image-toolkit.py run text-to-image.fashion-concept-generator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

**`text-to-image.abstract-visual-synthesizer`** - Create non-representational images using controlled color, rhythm, geometry, texture, flow, and depth. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 10

```bash
python3 tools-image-toolkit.py run text-to-image.abstract-visual-synthesizer --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `deliverable`, `medium`.

### 6. Single-Image Editing

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `single-image-editing.inpainting-specialist` | Inpainting Specialist | plan | write |
| `single-image-editing.outpainting-specialist` | Outpainting Specialist | plan | write |
| `single-image-editing.object-removal-editor` | Object Removal Editor | plan | write |
| `single-image-editing.object-addition-editor` | Object Addition Editor | plan | write |
| `single-image-editing.background-replacement-editor` | Background Replacement Editor | plan | write |
| `single-image-editing.relighting-specialist` | Relighting Specialist | plan | write |
| `single-image-editing.expression-and-gaze-editor` | Expression and Gaze Editor | plan | write |
| `single-image-editing.image-repair-and-restoration-editor` | Image Repair and Restoration Editor | transform | write |

**`single-image-editing.inpainting-specialist`** - Replace, add, or repair selected regions using masks, context-aware prompts, denoising control, and edge blending.

```bash
python3 tools-image-toolkit.py run single-image-editing.inpainting-specialist --set image=asset-sample-image.png --set instruction="replace the sky with a clear evening" --out-dir output
```

Required: `instruction`. Optional: `image`, `mask`.

**`single-image-editing.outpainting-specialist`** - Extend images beyond original borders while maintaining perspective, lighting, subject continuity, and seamless textures.

```bash
python3 tools-image-toolkit.py run single-image-editing.outpainting-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `expand`.

**`single-image-editing.object-removal-editor`** - Remove unwanted objects and reconstruct plausible backgrounds without repeating patterns, smears, or ghost edges.

```bash
python3 tools-image-toolkit.py run single-image-editing.object-removal-editor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `mask`.

**`single-image-editing.object-addition-editor`** - Insert new subjects with consistent scale, contact shadows, reflections, occlusion, color temperature, and focus.

```bash
python3 tools-image-toolkit.py run single-image-editing.object-addition-editor --set image=asset-sample-image.png --set object="a brass compass" --out-dir output
```

Required: `object`. Optional: `image`, `position`.

**`single-image-editing.background-replacement-editor`** - Separate foregrounds, replace scenes, and match edge detail, spill, depth, lighting, and atmospheric perspective.

```bash
python3 tools-image-toolkit.py run single-image-editing.background-replacement-editor --set image=asset-sample-image.png --set new_background="a sunlit studio cyclorama" --out-dir output
```

Required: `new_background`. Optional: `image`, `matte`.

**`single-image-editing.relighting-specialist`** - Change key, fill, rim, direction, softness, color temperature, and time of day while keeping material response believable.

```bash
python3 tools-image-toolkit.py run single-image-editing.relighting-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `direction`, `temperature`, `apply`.

**`single-image-editing.expression-and-gaze-editor`** - Make controlled facial-expression or eye-direction changes while preserving identity, anatomy, and image realism.

```bash
python3 tools-image-toolkit.py run single-image-editing.expression-and-gaze-editor --set image=asset-sample-image.png --set expression="a wry half smile" --out-dir output
```

Required: `expression`. Optional: `image`, `gaze`.

**`single-image-editing.image-repair-and-restoration-editor`** - Repair scratches, tears, compression artifacts, missing regions, color fading, and local damage with conservative reconstruction. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 11

```bash
python3 tools-image-toolkit.py run single-image-editing.image-repair-and-restoration-editor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `strength`.

### 7. Multi-Image Editing and Composition

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `multi-image-composition.multi-reference-composer` | Multi-Reference Composer | plan | write |
| `multi-image-composition.identity-consistency-manager` | Identity Consistency Manager | analyze | safe |
| `multi-image-composition.style-consistency-manager` | Style Consistency Manager | analyze | safe |
| `multi-image-composition.photomontage-integrator` | Photomontage Integrator | plan | write |
| `multi-image-composition.contact-sheet-builder` | Contact Sheet Builder | artifact | write |
| `multi-image-composition.before-and-after-editor` | Before-and-After Editor | artifact | write |
| `multi-image-composition.visual-asset-family-generator` | Visual Asset Family Generator | artifact | write |
| `multi-image-composition.reference-conflict-resolver` | Reference Conflict Resolver | analysis | safe |

**`multi-image-composition.multi-reference-composer`** - Combine subjects, styles, poses, products, and locations from several references with explicit source-role assignment.

```bash
python3 tools-image-toolkit.py run multi-image-composition.multi-reference-composer --set references='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

Required: `references`. Optional: `roles`.

**`multi-image-composition.identity-consistency-manager`** - Preserve distinctive facial and character features across images while controlling pose, wardrobe, lighting, and expression.

```bash
python3 tools-image-toolkit.py run multi-image-composition.identity-consistency-manager --set images='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

Required: `images`.

**`multi-image-composition.style-consistency-manager`** - Maintain line quality, palette, rendering method, texture, and finishing across a set of unrelated scenes.

```bash
python3 tools-image-toolkit.py run multi-image-composition.style-consistency-manager --set images='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

Required: `images`.

**`multi-image-composition.photomontage-integrator`** - Blend multiple photographs using perspective matching, masking, color grading, grain, shadows, and depth cues.

```bash
python3 tools-image-toolkit.py run multi-image-composition.photomontage-integrator --set images='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

Required: `images`.

**`multi-image-composition.contact-sheet-builder`** - Generate and label systematic grids of variations for rapid review of prompts, seeds, poses, lighting, or product options.

```bash
python3 tools-image-toolkit.py run multi-image-composition.contact-sheet-builder --set images='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

All parameters are optional: `images`, `spec`, `columns`, `cell_width`, `label`.

**`multi-image-composition.before-and-after-editor`** - Create aligned comparison pairs showing a controlled transformation while keeping framing and unaffected regions stable.

```bash
python3 tools-image-toolkit.py run multi-image-composition.before-and-after-editor --out-dir output
```

All parameters are optional: `before`, `after`.

**`multi-image-composition.visual-asset-family-generator`** - Produce coordinated hero images, banners, thumbnails, cards, and social crops from one campaign direction.

```bash
python3 tools-image-toolkit.py run multi-image-composition.visual-asset-family-generator --set campaign="spring launch" --out-dir output
```

Required: `campaign`.

**`multi-image-composition.reference-conflict-resolver`** - Detect contradictions among references and prioritize identity, pose, style, composition, palette, or object details explicitly. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 12

```bash
python3 tools-image-toolkit.py run multi-image-composition.reference-conflict-resolver --set references='["asset-sample-image.png","asset-sample-image.png"]' --out-dir output
```

All parameters are optional: `references`.

### 8. Masking, Selections, and Compositing

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `masking-compositing.precision-mask-generator` | Precision Mask Generator | transform | write |
| `masking-compositing.semantic-segmentation-editor` | Semantic Segmentation Editor | analyze | safe |
| `masking-compositing.alpha-matting-specialist` | Alpha Matting Specialist | analyze | safe |
| `masking-compositing.layered-composite-builder` | Layered Composite Builder | artifact | write |
| `masking-compositing.shadow-and-reflection-composer` | Shadow and Reflection Composer | compute | write |
| `masking-compositing.occlusion-planner` | Occlusion Planner | plan | write |
| `masking-compositing.edge-decontamination-specialist` | Edge Decontamination Specialist | transform | write |
| `masking-compositing.blend-mode-advisor` | Blend Mode Advisor | analysis | safe |

**`masking-compositing.precision-mask-generator`** - Create clean masks for hair, fur, glass, smoke, foliage, thin structures, soft edges, and partially transparent materials.

```bash
python3 tools-image-toolkit.py run masking-compositing.precision-mask-generator --out-dir output
```

All parameters are optional: `mask`, `material`, `feather`.

**`masking-compositing.semantic-segmentation-editor`** - Divide images into editable subject classes and convert segmentation outputs into production masks.

```bash
python3 tools-image-toolkit.py run masking-compositing.semantic-segmentation-editor --out-dir output
```

All parameters are optional: `segmentation`, `classes`.

**`masking-compositing.alpha-matting-specialist`** - Recover accurate foreground opacity and edge color for difficult subjects against complex backgrounds.

```bash
python3 tools-image-toolkit.py run masking-compositing.alpha-matting-specialist --set image=asset-sample-cutout.png --out-dir output
```

All parameters are optional: `image`, `background`.

**`masking-compositing.layered-composite-builder`** - Assemble editable foreground, midground, background, effects, and grading layers with clear blend logic.

```bash
python3 tools-image-toolkit.py run masking-compositing.layered-composite-builder --out-dir output
```

All parameters are optional: `layers`.

**`masking-compositing.shadow-and-reflection-composer`** - Generate or repair cast shadows, contact shadows, glossy reflections, and mirror content consistent with scene geometry.

```bash
python3 tools-image-toolkit.py run masking-compositing.shadow-and-reflection-composer --out-dir output
```

All parameters are optional: `subject_width`, `subject_height`, `light_direction`, `elevation`.

**`masking-compositing.occlusion-planner`** - Determine correct front-back relationships and mask overlaps so inserted subjects sit naturally in the scene.

```bash
python3 tools-image-toolkit.py run masking-compositing.occlusion-planner --out-dir output
```

All parameters are optional: `subjects`.

**`masking-compositing.edge-decontamination-specialist`** - Remove color spill, halos, jagged cutouts, and background contamination after subject extraction.

```bash
python3 tools-image-toolkit.py run masking-compositing.edge-decontamination-specialist --set image=asset-sample-cutout.png --out-dir output
```

All parameters are optional: `image`, `spill`, `amount`.

**`masking-compositing.blend-mode-advisor`** - Choose multiply, screen, overlay, soft light, luminosity, color, and masking strategies for non- destructive compositing. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 13

```bash
python3 tools-image-toolkit.py run masking-compositing.blend-mode-advisor --out-dir output
```

All parameters are optional: `foreground`, `background`, `intent`.

### 9. Upscaling, Enhancement, and Restoration

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `upscaling-restoration.ai-upscaling-specialist` | AI Upscaling Specialist | plan | write |
| `upscaling-restoration.high-resolution-fix-tuner` | High-Resolution Fix Tuner | transform | write |
| `upscaling-restoration.detail-recovery-specialist` | Detail Recovery Specialist | plan | write |
| `upscaling-restoration.face-restoration-controller` | Face Restoration Controller | plan | write |
| `upscaling-restoration.noise-and-grain-manager` | Noise and Grain Manager | transform | write |
| `upscaling-restoration.deblur-and-sharpening-specialist` | Deblur and Sharpening Specialist | transform | write |
| `upscaling-restoration.old-photo-restoration-specialist` | Old Photo Restoration Specialist | transform | write |
| `upscaling-restoration.print-resolution-planner` | Print Resolution Planner | compute | safe |

**`upscaling-restoration.ai-upscaling-specialist`** - Select latent, diffusion, ESRGAN-class, or face-aware upscaling workflows based on source quality and final output size.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.ai-upscaling-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `target_width`, `target_height`.

**`upscaling-restoration.high-resolution-fix-tuner`** - Balance first-pass composition with second-pass detail using scale, denoising, tile size, overlap, and seam controls.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.high-resolution-fix-tuner --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `scale`, `tile`, `overlap`, `apply`.

**`upscaling-restoration.detail-recovery-specialist`** - Restore credible fine texture without inventing destructive facial, product, logo, or material details.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.detail-recovery-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `subject`.

**`upscaling-restoration.face-restoration-controller`** - Apply face repair selectively, compare preservation versus beautification, and prevent plastic texture or identity drift.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.face-restoration-controller --out-dir output
```

All parameters are optional: `subject`, `goal`.

**`upscaling-restoration.noise-and-grain-manager`** - Reduce sensor noise or compression noise while retaining texture, then add coherent grain for final integration.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.noise-and-grain-manager --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `denoise`, `grain`, `seed`.

**`upscaling-restoration.deblur-and-sharpening-specialist`** - Treat motion blur, defocus, and softness with artifact-aware sharpening and realistic frequency recovery.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.deblur-and-sharpening-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `amount`, `apply`.

**`upscaling-restoration.old-photo-restoration-specialist`** - Repair aged photos, preserve historical character, and separate factual restoration from speculative reconstruction.

```bash
python3 tools-image-toolkit.py run upscaling-restoration.old-photo-restoration-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `strength`.

**`upscaling-restoration.print-resolution-planner`** - Calculate pixel dimensions, DPI, bleed, crop, viewing distance, and sharpening for posters, books, packaging, and large format. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 14

```bash
python3 tools-image-toolkit.py run upscaling-restoration.print-resolution-planner --set width_px="4000" --set height_px="3000" --out-dir output
```

Required: `width_px`, `height_px`. Optional: `dpi`, `bleed_mm`, `target_inches`.

### 10. Color, Lighting, and Finishing

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `color-lighting.color-grading-specialist` | Color Grading Specialist | transform | write |
| `color-lighting.palette-transfer-editor` | Palette Transfer Editor | transform | write |
| `color-lighting.lighting-continuity-auditor` | Lighting Continuity Auditor | analyze | safe |
| `color-lighting.day-to-night-converter` | Day-to-Night Converter | transform | write |
| `color-lighting.season-and-weather-transformer` | Season and Weather Transformer | plan | write |
| `color-lighting.cinematic-look-designer` | Cinematic Look Designer | transform | write |
| `color-lighting.hdr-and-dynamic-range-specialist` | HDR and Dynamic Range Specialist | transform | write |
| `color-lighting.output-color-manager` | Output Color Manager | analyze | safe |

**`color-lighting.color-grading-specialist`** - Build coherent contrast, white balance, saturation, hue separation, and tonal curves for a target mood or brand palette.

```bash
python3 tools-image-toolkit.py run color-lighting.color-grading-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `mood`, `strength`, `apply`.

**`color-lighting.palette-transfer-editor`** - Transfer color relationships from a reference while protecting skin, product, neutral, and brand-critical colors.

```bash
python3 tools-image-toolkit.py run color-lighting.palette-transfer-editor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `reference`, `protect`, `strength`.

**`color-lighting.lighting-continuity-auditor`** - Check direction, softness, intensity, shadow geometry, bounce, reflections, and emissive sources across composites.

```bash
python3 tools-image-toolkit.py run color-lighting.lighting-continuity-auditor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `expected_direction`.

**`color-lighting.day-to-night-converter`** - Transform daylight scenes into night scenes with plausible exposure, practical lights, sky, reflections, and ambient color.

```bash
python3 tools-image-toolkit.py run color-lighting.day-to-night-converter --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `strength`, `apply`.

**`color-lighting.season-and-weather-transformer`** - Change rain, snow, fog, haze, wind, foliage, or season while preserving scene structure and physical plausibility.

```bash
python3 tools-image-toolkit.py run color-lighting.season-and-weather-transformer --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `weather`, `season`.

**`color-lighting.cinematic-look-designer`** - Create filmic contrast, halation, bloom, grain, lens behavior, and color separation suited to narrative intent.

```bash
python3 tools-image-toolkit.py run color-lighting.cinematic-look-designer --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `look`, `grain`, `apply`.

**`color-lighting.hdr-and-dynamic-range-specialist`** - Merge or synthesize highlight and shadow detail while avoiding halos, flatness, and unnatural local contrast.

```bash
python3 tools-image-toolkit.py run color-lighting.hdr-and-dynamic-range-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `exposures`, `apply`.

**`color-lighting.output-color-manager`** - Prepare sRGB, Display P3, CMYK-oriented, HDR, and print-preview outputs with explicit conversion and soft-proofing guidance. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 15

```bash
python3 tools-image-toolkit.py run color-lighting.output-color-manager --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `target`.

### 11. Typography, Logos, and Graphic Design

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `typography-design.image-typography-planner` | Image Typography Planner | artifact | write |
| `typography-design.ai-text-artifact-corrector` | AI Text Artifact Corrector | analysis | safe |
| `typography-design.logo-concept-generator` | Logo Concept Generator | artifact | write |
| `typography-design.poster-and-key-art-designer` | Poster and Key Art Designer | artifact | write |
| `typography-design.social-graphic-builder` | Social Graphic Builder | artifact | write |
| `typography-design.packaging-artwork-conceptualizer` | Packaging Artwork Conceptualizer | plan | write |
| `typography-design.infographic-visual-asset-maker` | Infographic Visual Asset Maker | artifact | write |
| `typography-design.brand-template-adapter` | Brand Template Adapter | artifact | write |

**`typography-design.image-typography-planner`** - Plan type hierarchy, font pairing, placement, contrast, safe zones, and readability, then add final text in a deterministic design tool.

```bash
python3 tools-image-toolkit.py run typography-design.image-typography-planner --set headline="SHIP FASTER" --out-dir output
```

Required: `headline`. Optional: `subhead`, `width`, `height`, `background`, `text_color`.

**`typography-design.ai-text-artifact-corrector`** - Identify garbled generated lettering and replace it with accurate editable typography while matching perspective and material.

```bash
python3 tools-image-toolkit.py run typography-design.ai-text-artifact-corrector --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `text`.

**`typography-design.logo-concept-generator`** - Explore original symbol, wordmark, monogram, and lockup directions while checking simplicity, scalability, and distinctiveness.

```bash
python3 tools-image-toolkit.py run typography-design.logo-concept-generator --set brand="Northwind" --out-dir output
```

Required: `brand`. Optional: `industry`.

**`typography-design.poster-and-key-art-designer`** - Combine generated imagery with title, billing, date, and call-to-action zones in a clear visual hierarchy.

```bash
python3 tools-image-toolkit.py run typography-design.poster-and-key-art-designer --set title="Tidewater Nights" --out-dir output
```

Required: `title`. Optional: `subtitle`, `width`, `height`.

**`typography-design.social-graphic-builder`** - Create platform-ready square, portrait, landscape, story, and thumbnail designs with crop- safe focal points.

```bash
python3 tools-image-toolkit.py run typography-design.social-graphic-builder --set headline="SHIP FASTER" --out-dir output
```

Required: `headline`. Optional: `platform`.

**`typography-design.packaging-artwork-conceptualizer`** - Generate label and package concepts mapped to real dieline constraints, panels, barcodes, legal copy, and print finishes.

```bash
python3 tools-image-toolkit.py run typography-design.packaging-artwork-conceptualizer --set product="an amber glass perfume bottle" --out-dir output
```

Required: `product`. Optional: `dieline`.

**`typography-design.infographic-visual-asset-maker`** - Create icons, spot illustrations, metaphors, backgrounds, and diagram components while keeping factual text outside the image model.

```bash
python3 tools-image-toolkit.py run typography-design.infographic-visual-asset-maker --set topic="how diffusion models learn" --out-dir output
```

Required: `topic`. Optional: `items`.

**`typography-design.brand-template-adapter`** - Apply approved colors, fonts, spacing, logo clear space, art direction, and accessibility rules to repeatable assets. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 16

```bash
python3 tools-image-toolkit.py run typography-design.brand-template-adapter --set brand="Northwind" --out-dir output
```

Required: `brand`. Optional: `headline`.

### 12. Characters, Faces, and People

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `characters-people.character-design-specialist` | Character Design Specialist | artifact | write |
| `characters-people.character-consistency-engineer` | Character Consistency Engineer | plan | write |
| `characters-people.portrait-generator` | Portrait Generator | artifact | write |
| `characters-people.group-composition-specialist` | Group Composition Specialist | compute | write |
| `characters-people.hands-and-anatomy-repair-specialist` | Hands and Anatomy Repair Specialist | plan | write |
| `characters-people.wardrobe-and-costume-editor` | Wardrobe and Costume Editor | plan | write |
| `characters-people.hair-and-grooming-editor` | Hair and Grooming Editor | plan | write |
| `characters-people.ethical-portrait-workflow-auditor` | Ethical Portrait Workflow Auditor | audit | safe |

**`characters-people.character-design-specialist`** - Define silhouette, proportions, costume, props, materials, palette, and turnaround requirements for a distinct original character.

```bash
python3 tools-image-toolkit.py run characters-people.character-design-specialist --set character="Ilse, a cartographer with ink-stained fingers" --out-dir output
```

Required: `character`.

**`characters-people.character-consistency-engineer`** - Use reference sheets, embeddings, adapters, seeds, and controlled prompts to maintain a character across scenes.

```bash
python3 tools-image-toolkit.py run characters-people.character-consistency-engineer --set character="Ilse, a cartographer with ink-stained fingers" --out-dir output
```

Required: `character`. Optional: `scenes`.

**`characters-people.portrait-generator`** - Create portraits with explicit framing, lens, pose, light, background, styling, and retouching constraints.

```bash
python3 tools-image-toolkit.py run characters-people.portrait-generator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`.

**`characters-people.group-composition-specialist`** - Arrange multiple people with clear identities, poses, spacing, gaze, overlap, and interaction without duplication.

```bash
python3 tools-image-toolkit.py run characters-people.group-composition-specialist --out-dir output
```

All parameters are optional: `people`, `frame`.

**`characters-people.hands-and-anatomy-repair-specialist`** - Detect and repair fingers, joints, limb attachments, perspective, and pose errors with masked regeneration.

```bash
python3 tools-image-toolkit.py run characters-people.hands-and-anatomy-repair-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `region`.

**`characters-people.wardrobe-and-costume-editor`** - Change clothing, materials, accessories, historical era, or uniform details while preserving pose and body continuity.

```bash
python3 tools-image-toolkit.py run characters-people.wardrobe-and-costume-editor --set image=asset-sample-image.png --set garment="a waxed canvas coat with a storm hood" --out-dir output
```

Required: `garment`. Optional: `image`, `era`.

**`characters-people.hair-and-grooming-editor`** - Modify hairstyle, length, texture, or facial hair with accurate edges, volume, lighting, and identity preservation.

```bash
python3 tools-image-toolkit.py run characters-people.hair-and-grooming-editor --set image=asset-sample-image.png --set hair="a short silver undercut" --out-dir output
```

Required: `hair`. Optional: `image`.

**`characters-people.ethical-portrait-workflow-auditor`** - Check consent, impersonation, sensitive edits, minors, stereotyping, and disclosure needs before producing or releasing portraits. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 17

```bash
python3 tools-image-toolkit.py run characters-people.ethical-portrait-workflow-auditor --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `consent_records`.

### 13. Products, Commerce, and Advertising

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `commerce-advertising.e-commerce-image-producer` | E-commerce Image Producer | plan | write |
| `commerce-advertising.virtual-product-photography-director` | Virtual Product Photography Director | artifact | write |
| `commerce-advertising.product-variant-generator` | Product Variant Generator | artifact | write |
| `commerce-advertising.lifestyle-placement-specialist` | Lifestyle Placement Specialist | plan | write |
| `commerce-advertising.advertising-concept-generator` | Advertising Concept Generator | artifact | write |
| `commerce-advertising.marketplace-compliance-reviewer` | Marketplace Compliance Reviewer | audit | safe |
| `commerce-advertising.catalog-consistency-auditor` | Catalog Consistency Auditor | audit | safe |
| `commerce-advertising.conversion-creative-variant-maker` | Conversion Creative Variant Maker | artifact | write |

**`commerce-advertising.e-commerce-image-producer`** - Create consistent white-background, angle, detail, scale, and shadow standards for product listings.

```bash
python3 tools-image-toolkit.py run commerce-advertising.e-commerce-image-producer --set product="an amber glass perfume bottle" --out-dir output
```

Required: `product`. Optional: `marketplace`.

**`commerce-advertising.virtual-product-photography-director`** - Plan studio sets, lenses, surfaces, light rigs, reflections, and hero compositions for synthetic product shoots.

```bash
python3 tools-image-toolkit.py run commerce-advertising.virtual-product-photography-director --set product="an amber glass perfume bottle" --out-dir output
```

Required: `product`.

**`commerce-advertising.product-variant-generator`** - Generate controlled color, finish, material, and accessory variants while keeping geometry and branding unchanged.

```bash
python3 tools-image-toolkit.py run commerce-advertising.product-variant-generator --set product="an amber glass perfume bottle" --out-dir output
```

Required: `product`. Optional: `dimension`, `count`.

**`commerce-advertising.lifestyle-placement-specialist`** - Place products in credible use contexts with correct scale, hand interaction, contact, reflections, and safety considerations.

```bash
python3 tools-image-toolkit.py run commerce-advertising.lifestyle-placement-specialist --set product="an amber glass perfume bottle" --out-dir output
```

Required: `product`. Optional: `context`.

**`commerce-advertising.advertising-concept-generator`** - Develop campaign territories, visual metaphors, hero shots, supporting scenes, and channel adaptations from a brief.

```bash
python3 tools-image-toolkit.py run commerce-advertising.advertising-concept-generator --set brief="launch an autumn walking-boot line for a coastal trail brand" --out-dir output
```

Required: `brief`. Optional: `channels`.

**`commerce-advertising.marketplace-compliance-reviewer`** - Check image dimensions, backgrounds, overlays, claims, badges, and prohibited content against a specified marketplace policy.

```bash
python3 tools-image-toolkit.py run commerce-advertising.marketplace-compliance-reviewer --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `marketplace`.

**`commerce-advertising.catalog-consistency-auditor`** - Detect deviations in crop, angle, lighting, white point, shadow, color, and retouching across large product catalogs.

```bash
python3 tools-image-toolkit.py run commerce-advertising.catalog-consistency-auditor --out-dir output
```

All parameters are optional: `path`.

**`commerce-advertising.conversion-creative-variant-maker`** - Generate testable ad alternatives changing one hypothesis at a time, such as benefit framing, focal product, context, or color. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 18

```bash
python3 tools-image-toolkit.py run commerce-advertising.conversion-creative-variant-maker --set brief="launch an autumn walking-boot line for a coastal trail brand" --out-dir output
```

Required: `brief`. Optional: `hypotheses`.

### 14. Architecture, Interiors, and Spatial Design

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `architecture-interiors.interior-concept-visualizer` | Interior Concept Visualizer | artifact | write |
| `architecture-interiors.exterior-massing-visualizer` | Exterior Massing Visualizer | artifact | write |
| `architecture-interiors.floor-plan-to-perspective-designer` | Floor Plan to Perspective Designer | compute | write |
| `architecture-interiors.material-and-finish-variant-maker` | Material and Finish Variant Maker | artifact | write |
| `architecture-interiors.furniture-staging-specialist` | Furniture Staging Specialist | compute | write |
| `architecture-interiors.landscape-visualization-creator` | Landscape Visualization Creator | plan | write |
| `architecture-interiors.real-estate-enhancement-editor` | Real Estate Enhancement Editor | plan | write |
| `architecture-interiors.spatial-consistency-auditor` | Spatial Consistency Auditor | audit | safe |

**`architecture-interiors.interior-concept-visualizer`** - Translate plans and briefs into room concepts with realistic scale, circulation, furniture, materials, and lighting.

```bash
python3 tools-image-toolkit.py run architecture-interiors.interior-concept-visualizer --out-dir output
```

All parameters are optional: `room`, `dimensions`.

**`architecture-interiors.exterior-massing-visualizer`** - Explore facade rhythm, massing, openings, landscape, context, and daylight while marking concept-level assumptions.

```bash
python3 tools-image-toolkit.py run architecture-interiors.exterior-massing-visualizer --set building="a four-storey timber apartment block" --out-dir output
```

Required: `building`. Optional: `storeys`.

**`architecture-interiors.floor-plan-to-perspective-designer`** - Use plan geometry and references to create consistent eye-level perspectives without inventing impossible connections.

```bash
python3 tools-image-toolkit.py run architecture-interiors.floor-plan-to-perspective-designer --set width_m="8" --set depth_m="5.5" --out-dir output
```

Required: `width_m`, `depth_m`. Optional: `height_m`.

**`architecture-interiors.material-and-finish-variant-maker`** - Swap floors, walls, cabinetry, stone, metal, fabric, and paint while preserving geometry and lighting response.

```bash
python3 tools-image-toolkit.py run architecture-interiors.material-and-finish-variant-maker --set room="a small kitchen with a north window" --out-dir output
```

Required: `room`. Optional: `surface`.

**`architecture-interiors.furniture-staging-specialist`** - Stage rooms for function and visual balance while maintaining clearances, accessibility, and realistic object sizes.

```bash
python3 tools-image-toolkit.py run architecture-interiors.furniture-staging-specialist --out-dir output
```

All parameters are optional: `room`, `dimensions`.

**`architecture-interiors.landscape-visualization-creator`** - Create planting, hardscape, site furniture, water, season, and maintenance-aware landscape concepts.

```bash
python3 tools-image-toolkit.py run architecture-interiors.landscape-visualization-creator --set site="a coastal cliff path at low tide" --out-dir output
```

Required: `site`. Optional: `climate`.

**`architecture-interiors.real-estate-enhancement-editor`** - Improve exposure, declutter, correct verticals, and stage responsibly without concealing material property defects.

```bash
python3 tools-image-toolkit.py run architecture-interiors.real-estate-enhancement-editor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`.

**`architecture-interiors.spatial-consistency-auditor`** - Check perspective, vanishing points, door and stair logic, scale, reflections, and repeated architectural elements. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 19

```bash
python3 tools-image-toolkit.py run architecture-interiors.spatial-consistency-auditor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`.

### 15. Scientific, Medical, and Technical Imaging

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `scientific-technical.scientific-illustration-planner` | Scientific Illustration Planner | artifact | write |
| `scientific-technical.medical-visualization-safety-reviewer` | Medical Visualization Safety Reviewer | audit | safe |
| `scientific-technical.technical-cutaway-creator` | Technical Cutaway Creator | artifact | write |
| `scientific-technical.dataset-visualization-asset-maker` | Dataset Visualization Asset Maker | artifact | write |
| `scientific-technical.microscopy-image-enhancement-guide` | Microscopy Image Enhancement Guide | plan | write |
| `scientific-technical.satellite-and-geospatial-image-stylist` | Satellite and Geospatial Image Stylist | plan | write |
| `scientific-technical.patent-figure-preparation-guide` | Patent Figure Preparation Guide | artifact | write |
| `scientific-technical.technical-accuracy-auditor` | Technical Accuracy Auditor | audit | safe |

**`scientific-technical.scientific-illustration-planner`** - Convert a verified mechanism or structure into a labeled visual plan that separates evidence from artistic interpretation.

```bash
python3 tools-image-toolkit.py run scientific-technical.scientific-illustration-planner --set mechanism="a bicycle freewheel" --out-dir output
```

Required: `mechanism`. Optional: `labels`.

**`scientific-technical.medical-visualization-safety-reviewer`** - Check educational medical imagery for anatomical plausibility, privacy, labeling, uncertainty, and non-diagnostic framing.

```bash
python3 tools-image-toolkit.py run scientific-technical.medical-visualization-safety-reviewer --set description="a chest radiograph annotated with an arrow" --out-dir output
```

Required: `description`.

**`scientific-technical.technical-cutaway-creator`** - Design exploded, cutaway, and sectional concepts with consistent part relationships and callout space.

```bash
python3 tools-image-toolkit.py run scientific-technical.technical-cutaway-creator --set assembly="a bicycle freewheel" --out-dir output
```

Required: `assembly`. Optional: `parts`.

**`scientific-technical.dataset-visualization-asset-maker`** - Create visual metaphors and decorative assets around charts without altering or fabricating the underlying data.

```bash
python3 tools-image-toolkit.py run scientific-technical.dataset-visualization-asset-maker --out-dir output
```

All parameters are optional: `chart_type`, `items`.

**`scientific-technical.microscopy-image-enhancement-guide`** - Plan denoising, contrast, segmentation, and colorization while preserving quantitative integrity and recording transformations.

```bash
python3 tools-image-toolkit.py run scientific-technical.microscopy-image-enhancement-guide --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `modality`.

**`scientific-technical.satellite-and-geospatial-image-stylist`** - Enhance or stylize geospatial imagery while preserving scale, orientation, legend accuracy, and source distinctions.

```bash
python3 tools-image-toolkit.py run scientific-technical.satellite-and-geospatial-image-stylist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `style`.

**`scientific-technical.patent-figure-preparation-guide`** - Convert concepts into clear monochrome line drawings with consistent numbering, contours, and views for professional review.

```bash
python3 tools-image-toolkit.py run scientific-technical.patent-figure-preparation-guide --set invention="a self-levelling cargo trolley" --out-dir output
```

Required: `invention`. Optional: `views`.

**`scientific-technical.technical-accuracy-auditor`** - Compare a generated technical image against specifications, diagrams, dimensions, and source references before release. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 20

```bash
python3 tools-image-toolkit.py run scientific-technical.technical-accuracy-auditor --set image=asset-sample-image.png --set spec="a bicycle freewheel with a 14-tooth sprocket and a 28-tooth chainring" --out-dir output
```

Required: `spec`. Optional: `image`.

### 16. Training, Fine-Tuning, and Personalization

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `training-personalization.dataset-curation-specialist` | Dataset Curation Specialist | analyze | write |
| `training-personalization.captioning-pipeline-designer` | Captioning Pipeline Designer | artifact | write |
| `training-personalization.lora-training-specialist` | LoRA Training Specialist | compute | write |
| `training-personalization.dreambooth-training-specialist` | DreamBooth Training Specialist | plan | write |
| `training-personalization.textual-inversion-trainer` | Textual Inversion Trainer | plan | write |
| `training-personalization.fine-tune-evaluation-designer` | Fine-Tune Evaluation Designer | artifact | write |
| `training-personalization.overfitting-and-memorization-auditor` | Overfitting and Memorization Auditor | audit | safe |
| `training-personalization.model-merge-and-blend-evaluator` | Model Merge and Blend Evaluator | plan | write |

**`training-personalization.dataset-curation-specialist`** - Select, deduplicate, caption, balance, filter, and document image datasets for quality, rights, diversity, and target coverage.

```bash
python3 tools-image-toolkit.py run training-personalization.dataset-curation-specialist --out-dir output
```

All parameters are optional: `dataset`, `caption_field`.

**`training-personalization.captioning-pipeline-designer`** - Create concise, consistent captions with trigger terms, subject attributes, style descriptors, and configurable caption dropout.

```bash
python3 tools-image-toolkit.py run training-personalization.captioning-pipeline-designer --out-dir output
```

All parameters are optional: `dataset`, `caption_field`, `trigger`.

**`training-personalization.lora-training-specialist`** - Plan LoRA rank, alpha, learning rates, resolution buckets, repeats, epochs, regularization, checkpoints, and validation prompts.

```bash
python3 tools-image-toolkit.py run training-personalization.lora-training-specialist --out-dir output
```

All parameters are optional: `dataset`, `dataset_size`, `target`.

**`training-personalization.dreambooth-training-specialist`** - Train subject personalization with class images, prior preservation, identity tests, and overfitting controls.

```bash
python3 tools-image-toolkit.py run training-personalization.dreambooth-training-specialist --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `class`, `dataset_size`.

**`training-personalization.textual-inversion-trainer`** - Create and evaluate learned embeddings for compact concepts, styles, objects, or negative artifact suppression.

```bash
python3 tools-image-toolkit.py run training-personalization.textual-inversion-trainer --set concept="a fictional ceramic glaze called sea-glaze" --out-dir output
```

Required: `concept`. Optional: `examples`.

**`training-personalization.fine-tune-evaluation-designer`** - Build fixed prompt suites and compare identity, flexibility, leakage, memorization, anatomy, style, and prompt adherence.

```bash
python3 tools-image-toolkit.py run training-personalization.fine-tune-evaluation-designer --set target="a character LoRA for a lighthouse keeper" --out-dir output
```

Required: `target`.

**`training-personalization.overfitting-and-memorization-auditor`** - Detect copied poses, backgrounds, textures, or near-duplicates and recommend data, step, or regularization changes.

```bash
python3 tools-image-toolkit.py run training-personalization.overfitting-and-memorization-auditor --out-dir output
```

All parameters are optional: `dataset`, `caption_field`.

**`training-personalization.model-merge-and-blend-evaluator`** - Test checkpoint or adapter combinations systematically for compatibility, interference, style balance, and reproducibility. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 21

```bash
python3 tools-image-toolkit.py run training-personalization.model-merge-and-blend-evaluator --out-dir output
```

All parameters are optional: `models`, `ratios`.

### 17. Data Governance, Rights, and Provenance

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `governance-provenance.training-data-rights-reviewer` | Training Data Rights Reviewer | audit | safe |
| `governance-provenance.copyright-risk-screener` | Copyright Risk Screener | audit | safe |
| `governance-provenance.artist-style-request-rewriter` | Artist Style Request Rewriter | artifact | write |
| `governance-provenance.consent-and-likeness-checker` | Consent and Likeness Checker | audit | safe |
| `governance-provenance.provenance-metadata-manager` | Provenance Metadata Manager | transform | write |
| `governance-provenance.synthetic-media-disclosure-writer` | Synthetic Media Disclosure Writer | artifact | write |
| `governance-provenance.privacy-redaction-specialist` | Privacy Redaction Specialist | transform | destructive |
| `governance-provenance.dataset-bias-auditor` | Dataset Bias Auditor | analyze | safe |

**`governance-provenance.training-data-rights-reviewer`** - Document dataset sources, licenses, consent, restrictions, retention, and removal paths before training or fine-tuning.

```bash
python3 tools-image-toolkit.py run governance-provenance.training-data-rights-reviewer --out-dir output
```

All parameters are optional: `dataset`.

**`governance-provenance.copyright-risk-screener`** - Flag close imitation, protected characters, logos, distinctive trade dress, and likely source reconstruction for human legal review.

```bash
python3 tools-image-toolkit.py run governance-provenance.copyright-risk-screener --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `context`.

**`governance-provenance.artist-style-request-rewriter`** - Translate requests naming living artists into high-level visual attributes that preserve creative intent without direct imitation.

```bash
python3 tools-image-toolkit.py run governance-provenance.artist-style-request-rewriter --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`.

**`governance-provenance.consent-and-likeness-checker`** - Verify authorization and intended use when generating or editing identifiable people, voices, private settings, or sensitive contexts.

```bash
python3 tools-image-toolkit.py run governance-provenance.consent-and-likeness-checker --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `records`.

**`governance-provenance.provenance-metadata-manager`** - Preserve prompts, model identifiers, edits, content credentials, timestamps, and source relationships in sidecars or embedded metadata.

```bash
python3 tools-image-toolkit.py run governance-provenance.provenance-metadata-manager --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `sidecar`, `prompt`, `model`, `seed`, `embed`.

**`governance-provenance.synthetic-media-disclosure-writer`** - Create clear, context-appropriate labels describing AI generation, editing, uncertainty, and material changes.

```bash
python3 tools-image-toolkit.py run governance-provenance.synthetic-media-disclosure-writer --out-dir output
```

All parameters are optional: `context`, `details`.

**`governance-provenance.privacy-redaction-specialist`** - Remove faces, plates, addresses, screens, documents, and hidden metadata before sharing images.

```bash
python3 tools-image-toolkit.py run governance-provenance.privacy-redaction-specialist --set image=asset-sample-image.png --yes --out-dir output
```

All parameters are optional: `image`, `apply`.

**`governance-provenance.dataset-bias-auditor`** - Measure representation, caption quality, stereotype risk, imbalance, and performance gaps across relevant groups and scenarios. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 22

```bash
python3 tools-image-toolkit.py run governance-provenance.dataset-bias-auditor --out-dir output
```

All parameters are optional: `dataset`, `caption_field`.

### 18. Safety, Authenticity, and Misuse Prevention

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `safety-authenticity.deepfake-risk-assessor` | Deepfake Risk Assessor | audit | safe |
| `safety-authenticity.image-authenticity-analyst` | Image Authenticity Analyst | analyze | safe |
| `safety-authenticity.content-safety-classifier` | Content Safety Classifier | audit | safe |
| `safety-authenticity.age-sensitive-image-reviewer` | Age-Sensitive Image Reviewer | audit | safe |
| `safety-authenticity.misleading-edit-detector` | Misleading Edit Detector | analyze | safe |
| `safety-authenticity.watermark-and-disclosure-planner` | Watermark and Disclosure Planner | artifact | write |
| `safety-authenticity.adversarial-prompt-defense-designer` | Adversarial Prompt Defense Designer | analysis | safe |
| `safety-authenticity.safe-release-checklist-builder` | Safe Release Checklist Builder | artifact | write |

**`safety-authenticity.deepfake-risk-assessor`** - Evaluate impersonation, deception, reputational harm, and distribution context, then recommend safeguards or safer alternatives.

```bash
python3 tools-image-toolkit.py run safety-authenticity.deepfake-risk-assessor --set scenario="a political campaign uses a synthetic voice of a real person" --out-dir output
```

Required: `scenario`. Optional: `subject`.

**`safety-authenticity.image-authenticity-analyst`** - Inspect metadata, compression, lighting, geometry, and generation artifacts while reporting uncertainty rather than claiming infallible detection.

```bash
python3 tools-image-toolkit.py run safety-authenticity.image-authenticity-analyst --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`.

**`safety-authenticity.content-safety-classifier`** - Classify visual requests and outputs for violence, sexual content, exploitation, self-harm, privacy, fraud, and other policy risks.

```bash
python3 tools-image-toolkit.py run safety-authenticity.content-safety-classifier --set text="a lighthouse keeper standing on wet rocks at dawn" --set image=asset-sample-image.png --out-dir output
```

Required: `text`. Optional: `image`.

**`safety-authenticity.age-sensitive-image-reviewer`** - Apply strict safeguards to any content involving minors or age ambiguity and prevent sexualized or exploitative transformations.

```bash
python3 tools-image-toolkit.py run safety-authenticity.age-sensitive-image-reviewer --set description="a chest radiograph annotated with an arrow" --out-dir output
```

Required: `description`.

**`safety-authenticity.misleading-edit-detector`** - Identify edits that materially change events, evidence, documents, products, property, or public-interest claims.

```bash
python3 tools-image-toolkit.py run safety-authenticity.misleading-edit-detector --out-dir output
```

All parameters are optional: `before`, `after`, `claim`.

**`safety-authenticity.watermark-and-disclosure-planner`** - Choose durable visible labels, invisible provenance, placement, contrast, and export practices appropriate to risk.

```bash
python3 tools-image-toolkit.py run safety-authenticity.watermark-and-disclosure-planner --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `text`, `mode`.

**`safety-authenticity.adversarial-prompt-defense-designer`** - Detect attempts to bypass image safeguards through obfuscation, multi-step transformations, encoded text, or tool chaining.

```bash
python3 tools-image-toolkit.py run safety-authenticity.adversarial-prompt-defense-designer --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`.

**`safety-authenticity.safe-release-checklist-builder`** - Create pre-publication checks for rights, privacy, factuality, security, disclosure, brand, accessibility, and platform rules. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 23

```bash
python3 tools-image-toolkit.py run safety-authenticity.safe-release-checklist-builder --out-dir output
```

All parameters are optional: `channel`, `media_type`.

### 19. Quality Assurance and Evaluation

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `quality-evaluation.visual-quality-reviewer` | Visual Quality Reviewer | audit | safe |
| `quality-evaluation.prompt-adherence-evaluator` | Prompt Adherence Evaluator | audit | safe |
| `quality-evaluation.aesthetic-benchmark-designer` | Aesthetic Benchmark Designer | artifact | write |
| `quality-evaluation.artifact-detection-specialist` | Artifact Detection Specialist | analyze | safe |
| `quality-evaluation.image-pair-comparator` | Image Pair Comparator | analyze | safe |
| `quality-evaluation.regression-test-builder` | Regression Test Builder | artifact | write |
| `quality-evaluation.human-review-protocol-designer` | Human Review Protocol Designer | artifact | write |
| `quality-evaluation.delivery-readiness-auditor` | Delivery Readiness Auditor | audit | safe |

**`quality-evaluation.visual-quality-reviewer`** - Score composition, coherence, detail, anatomy, lighting, texture, noise, artifacts, and overall finish using a repeatable rubric.

```bash
python3 tools-image-toolkit.py run quality-evaluation.visual-quality-reviewer --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`.

**`quality-evaluation.prompt-adherence-evaluator`** - Verify every requested subject, attribute, relationship, count, style, and exclusion against the final image.

```bash
python3 tools-image-toolkit.py run quality-evaluation.prompt-adherence-evaluator --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `observations`.

**`quality-evaluation.aesthetic-benchmark-designer`** - Create fair blind comparisons with standardized prompts, seeds where possible, outputs, raters, and scoring criteria.

```bash
python3 tools-image-toolkit.py run quality-evaluation.aesthetic-benchmark-designer --set prompt="a weathered brass lighthouse on a basalt headland, cold overcast morning" --out-dir output
```

Required: `prompt`. Optional: `candidates`, `raters`.

**`quality-evaluation.artifact-detection-specialist`** - Find malformed hands, duplicated objects, warped text, broken reflections, seams, halos, tiling, banding, and inconsistent geometry.

```bash
python3 tools-image-toolkit.py run quality-evaluation.artifact-detection-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`.

**`quality-evaluation.image-pair-comparator`** - Compare revisions at pixel, structural, semantic, and perceptual levels, highlighting intended and unintended changes.

```bash
python3 tools-image-toolkit.py run quality-evaluation.image-pair-comparator --out-dir output
```

All parameters are optional: `before`, `after`.

**`quality-evaluation.regression-test-builder`** - Maintain reference prompts and expected characteristics to catch quality changes after model, node, or parameter updates.

```bash
python3 tools-image-toolkit.py run quality-evaluation.regression-test-builder --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `suite`, `image`.

**`quality-evaluation.human-review-protocol-designer`** - Define reviewer instructions, sampling, disagreement handling, escalation, and acceptance thresholds for image pipelines.

```bash
python3 tools-image-toolkit.py run quality-evaluation.human-review-protocol-designer --out-dir output
```

All parameters are optional: `workflow`, `volume`.

**`quality-evaluation.delivery-readiness-auditor`** - Confirm dimensions, crops, transparency, color space, file format, metadata, naming, accessibility, and channel-specific requirements. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 24

```bash
python3 tools-image-toolkit.py run quality-evaluation.delivery-readiness-auditor --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `channel`.

### 20. Automation, APIs, and Batch Pipelines

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `automation-batch.image-generation-api-integrator` | Image Generation API Integrator | external | external |
| `automation-batch.batch-prompt-runner` | Batch Prompt Runner | artifact | write |
| `automation-batch.comfyui-api-automator` | ComfyUI API Automator | external | external |
| `automation-batch.asset-naming-and-metadata-automator` | Asset Naming and Metadata Automator | transform | write |
| `automation-batch.gpu-queue-orchestrator` | GPU Queue Orchestrator | compute | write |
| `automation-batch.cost-and-throughput-optimizer` | Cost and Throughput Optimizer | compute | write |
| `automation-batch.automated-qa-pipeline-builder` | Automated QA Pipeline Builder | artifact | write |
| `automation-batch.human-in-the-loop-image-workflow-designer` | Human-in-the-Loop Image Workflow Designer | artifact | write |

**`automation-batch.image-generation-api-integrator`** - Design request schemas, authentication, retries, idempotency, polling, storage, and error handling for image generation services.

```bash
python3 tools-image-toolkit.py run automation-batch.image-generation-api-integrator --yes --out-dir output
```

All parameters are optional: `endpoint`, `prompt`, `model`, `timeout`, `retries`.

**`automation-batch.batch-prompt-runner`** - Generate large prompt sets with parameter sweeps, deterministic naming, metadata capture, resume support, and failure isolation.

```bash
python3 tools-image-toolkit.py run automation-batch.batch-prompt-runner --out-dir output
```

All parameters are optional: `prompts`, `width`, `height`, `seeds`, `resume`.

**`automation-batch.comfyui-api-automator`** - Parameterize workflows, submit jobs, monitor queues, collect outputs, and preserve workflow JSON for repeatability.

```bash
python3 tools-image-toolkit.py run automation-batch.comfyui-api-automator --yes --out-dir output
```

All parameters are optional: `workflow`, `endpoint`, `prompt`, `timeout`.

**`automation-batch.asset-naming-and-metadata-automator`** - Apply stable filenames, folders, tags, prompts, seeds, licenses, versions, and campaign identifiers.

```bash
python3 tools-image-toolkit.py run automation-batch.asset-naming-and-metadata-automator --out-dir output
```

All parameters are optional: `path`, `campaign`, `template`, `copy`.

**`automation-batch.gpu-queue-orchestrator`** - Schedule jobs by VRAM, resolution, priority, model load cost, timeout, and retry policy across available workers.

```bash
python3 tools-image-toolkit.py run automation-batch.gpu-queue-orchestrator --out-dir output
```

All parameters are optional: `jobs`, `workers`, `vram_gb`.

**`automation-batch.cost-and-throughput-optimizer`** - Estimate images per hour and per dollar, then tune batch size, steps, resolution, caching, and model choice.

```bash
python3 tools-image-toolkit.py run automation-batch.cost-and-throughput-optimizer --out-dir output
```

All parameters are optional: `model`, `steps`, `batch_size`, `resolution`, `hourly_cost`.

**`automation-batch.automated-qa-pipeline-builder`** - Run dimension, corruption, blank-image, similarity, safety, metadata, and artifact checks before assets advance.

```bash
python3 tools-image-toolkit.py run automation-batch.automated-qa-pipeline-builder --out-dir output
```

All parameters are optional: `path`, `policy`.

**`automation-batch.human-in-the-loop-image-workflow-designer`** - Route generated candidates through review, annotation, revision, approval, and publishing with complete lineage. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 25

```bash
python3 tools-image-toolkit.py run automation-batch.human-in-the-loop-image-workflow-designer --out-dir output
```

All parameters are optional: `workflow`, `volume`.

### 21. Performance and Hardware Optimization

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `performance-hardware.vram-budget-planner` | VRAM Budget Planner | compute | safe |
| `performance-hardware.low-memory-diffusion-optimizer` | Low-Memory Diffusion Optimizer | plan | write |
| `performance-hardware.inference-speed-tuner` | Inference Speed Tuner | plan | write |
| `performance-hardware.gpu-compatibility-troubleshooter` | GPU Compatibility Troubleshooter | diagnose | safe |
| `performance-hardware.model-storage-manager` | Model Storage Manager | analyze | write |
| `performance-hardware.environment-reproducibility-builder` | Environment Reproducibility Builder | artifact | write |
| `performance-hardware.thermal-and-reliability-monitor` | Thermal and Reliability Monitor | analyze | safe |
| `performance-hardware.cloud-gpu-deployment-planner` | Cloud GPU Deployment Planner | plan | write |

**`performance-hardware.vram-budget-planner`** - Estimate memory for checkpoints, text encoders, VAE, ControlNet, adapters, resolution, batch size, and precision.

```bash
python3 tools-image-toolkit.py run performance-hardware.vram-budget-planner --out-dir output
```

All parameters are optional: `model`, `width`, `height`, `batch_size`, `precision`, `controlnets`, `loras`.

**`performance-hardware.low-memory-diffusion-optimizer`** - Use attention slicing, tiled VAE, CPU offload, quantization, sequential loading, and smaller batches with documented trade-offs.

```bash
python3 tools-image-toolkit.py run performance-hardware.low-memory-diffusion-optimizer --out-dir output
```

All parameters are optional: `vram_gb`, `model`.

**`performance-hardware.inference-speed-tuner`** - Benchmark compile options, efficient attention, distilled models, latent caching, step reduction, and resolution strategies.

```bash
python3 tools-image-toolkit.py run performance-hardware.inference-speed-tuner --out-dir output
```

All parameters are optional: `model`, `target`.

**`performance-hardware.gpu-compatibility-troubleshooter`** - Diagnose driver, CUDA, ROCm, Metal, DirectML, precision, kernel, and library compatibility failures.

```bash
python3 tools-image-toolkit.py run performance-hardware.gpu-compatibility-troubleshooter --out-dir output
```

All parameters are optional: `symptom`.

**`performance-hardware.model-storage-manager`** - Organize checkpoints, adapters, embeddings, VAEs, hashes, symlinks, archives, and shared caches across interfaces.

```bash
python3 tools-image-toolkit.py run performance-hardware.model-storage-manager --out-dir output
```

All parameters are optional: `path`, `apply`.

**`performance-hardware.environment-reproducibility-builder`** - Pin dependencies, record drivers, export workflows, isolate environments, and create clean rebuild procedures.

```bash
python3 tools-image-toolkit.py run performance-hardware.environment-reproducibility-builder --out-dir output
```

All parameters are optional: `name`.

**`performance-hardware.thermal-and-reliability-monitor`** - Track memory pressure, utilization, temperatures, throttling, crashes, and long-run stability during batch generation.

```bash
python3 tools-image-toolkit.py run performance-hardware.thermal-and-reliability-monitor --out-dir output
```

All parameters are optional: `log`.

**`performance-hardware.cloud-gpu-deployment-planner`** - Choose instance classes, storage, startup images, security controls, shutdown rules, and cost caps for hosted diffusion. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 26

```bash
python3 tools-image-toolkit.py run performance-hardware.cloud-gpu-deployment-planner --out-dir output
```

All parameters are optional: `model`, `concurrency`.

### 22. Specialized Media and Asset Production

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `specialized-media.game-concept-asset-maker` | Game Concept Asset Maker | artifact | write |
| `specialized-media.texture-and-material-generator` | Texture and Material Generator | artifact | write |
| `specialized-media.sprite-and-pixel-art-generator` | Sprite and Pixel Art Generator | artifact | write |
| `specialized-media.comic-and-storyboard-creator` | Comic and Storyboard Creator | artifact | write |
| `specialized-media.book-illustration-planner` | Book Illustration Planner | plan | write |
| `specialized-media.thumbnail-and-keyframe-generator` | Thumbnail and Keyframe Generator | artifact | write |
| `specialized-media.icon-family-designer` | Icon Family Designer | artifact | write |
| `specialized-media.pattern-and-surface-design-maker` | Pattern and Surface Design Maker | artifact | write |

**`specialized-media.game-concept-asset-maker`** - Create characters, environments, props, icons, loading art, and paintovers aligned to a game art bible.

```bash
python3 tools-image-toolkit.py run specialized-media.game-concept-asset-maker --set game="a small farming game set on a northern island" --out-dir output
```

Required: `game`. Optional: `asset_type`.

**`specialized-media.texture-and-material-generator`** - Create seamless albedo, roughness, normal-reference, height-reference, and material concept maps with tiling checks.

```bash
python3 tools-image-toolkit.py run specialized-media.texture-and-material-generator --out-dir output
```

All parameters are optional: `size`, `seed`, `pattern`.

**`specialized-media.sprite-and-pixel-art-generator`** - Design grid-aligned sprites, tiles, palettes, silhouettes, and animation-ready frame plans at specified pixel scales.

```bash
python3 tools-image-toolkit.py run specialized-media.sprite-and-pixel-art-generator --set subject="a lighthouse keeper in a heavy wool coat" --out-dir output
```

Required: `subject`. Optional: `scale`, `frames`.

**`specialized-media.comic-and-storyboard-creator`** - Produce consistent panels, shot continuity, staging, speech-safe areas, and readable action across a sequence.

```bash
python3 tools-image-toolkit.py run specialized-media.comic-and-storyboard-creator --set story="a courier crosses a flooded city" --out-dir output
```

Required: `story`. Optional: `panels`.

**`specialized-media.book-illustration-planner`** - Map manuscript beats to page turns, recurring characters, visual pacing, age level, and print- safe compositions.

```bash
python3 tools-image-toolkit.py run specialized-media.book-illustration-planner --set manuscript="a field guide to coastal birds" --out-dir output
```

Required: `manuscript`. Optional: `pages`.

**`specialized-media.thumbnail-and-keyframe-generator`** - Create high-contrast, readable small-format visuals with controlled focal points and crop-safe alternatives.

```bash
python3 tools-image-toolkit.py run specialized-media.thumbnail-and-keyframe-generator --set topic="how diffusion models learn" --out-dir output
```

Required: `topic`. Optional: `ratio`.

**`specialized-media.icon-family-designer`** - Generate a cohesive icon system with consistent stroke, corner, perspective, fill, grid, and optical weight.

```bash
python3 tools-image-toolkit.py run specialized-media.icon-family-designer --set set="a transit app icon set" --out-dir output
```

Required: `set`. Optional: `count`.

**`specialized-media.pattern-and-surface-design-maker`** - Create seamless repeats, half-drops, border prints, placement prints, and coordinated colorways for textiles or surfaces. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 27

```bash
python3 tools-image-toolkit.py run specialized-media.pattern-and-surface-design-maker --out-dir output
```

All parameters are optional: `pattern`, `size`, `seed`.

### 23. Accessibility, Localization, and Delivery

| Capability id | Skill | Kind | Risk |
| --- | --- | --- | --- |
| `accessibility-delivery.alt-text-writer-for-images` | Alt Text Writer for Images | artifact | write |
| `accessibility-delivery.color-contrast-accessibility-reviewer` | Color Contrast Accessibility Reviewer | audit | safe |
| `accessibility-delivery.localization-safe-visual-designer` | Localization-Safe Visual Designer | plan | write |
| `accessibility-delivery.cultural-context-reviewer` | Cultural Context Reviewer | audit | safe |
| `accessibility-delivery.multi-format-export-specialist` | Multi-Format Export Specialist | transform | write |
| `accessibility-delivery.responsive-crop-planner` | Responsive Crop Planner | compute | write |
| `accessibility-delivery.image-optimization-specialist` | Image Optimization Specialist | transform | write |
| `accessibility-delivery.archive-and-handoff-manager` | Archive and Handoff Manager | artifact | write |

**`accessibility-delivery.alt-text-writer-for-images`** - Write concise or extended alternative text based on the image's purpose, relevant details, and surrounding context.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.alt-text-writer-for-images --set details="a red lighthouse against a grey sea at dawn" --out-dir output
```

Required: `details`. Optional: `purpose`, `length`.

**`accessibility-delivery.color-contrast-accessibility-reviewer`** - Check foreground-background contrast, color-only encoding, low-vision legibility, and grayscale distinguishability.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.color-contrast-accessibility-reviewer --set foreground="#1a1a1a" --set background="#ffffff" --out-dir output
```

Required: `foreground`, `background`. Optional: `size`.

**`accessibility-delivery.localization-safe-visual-designer`** - Reserve flexible text space, avoid culture-specific ambiguity, and support mirrored or expanded layouts for localization.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.localization-safe-visual-designer --set asset="a packaging hero shot for a perfume bottle" --out-dir output
```

Required: `asset`. Optional: `locales`.

**`accessibility-delivery.cultural-context-reviewer`** - Flag symbols, gestures, clothing, maps, colors, and scenarios that may be inaccurate, sensitive, or inappropriate in a target locale.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.cultural-context-reviewer --set description="a chest radiograph annotated with an arrow" --out-dir output
```

Required: `description`. Optional: `locale`.

**`accessibility-delivery.multi-format-export-specialist`** - Prepare PNG, JPEG, WebP, AVIF, TIFF, SVG-adjacent, layered, and print files with suitable compression and transparency.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.multi-format-export-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `formats`, `quality`.

**`accessibility-delivery.responsive-crop-planner`** - Create focal-point-aware crops for desktop, mobile, social, banners, thumbnails, and app cards without losing essential content.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.responsive-crop-planner --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `focal`.

**`accessibility-delivery.image-optimization-specialist`** - Reduce file size through dimensions, quality, chroma, metadata, and modern formats while meeting visual thresholds.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.image-optimization-specialist --set image=asset-sample-image.png --out-dir output
```

All parameters are optional: `image`, `max_width`, `quality`.

**`accessibility-delivery.archive-and-handoff-manager`** - Package editable sources, final exports, prompts, models, licenses, fonts, metadata, and README instructions for continuation. AI IMAGE SKILL CATALOG AI Image Skill Catalog | 28 Recommended Blueprint for Turning Catalog Items into Real Skills Trigger definition State exactly when the Skill should activate, what requests are out of scope, and which competing Skills take priority. Inputs Define accepted prompts, reference images, masks, model files, workflow JSON, dimensions, formats, brand guides, and policy constraints. Outputs Specify image formats, editable sources, metadata, provenance, comparison grids, reports, and naming conventions. Tool routing Name required generation engines, editors, command-line tools, APIs, connectors, GPU environments, and fallback routes. Workflow Encode the shortest reliable sequence: inspect inputs, choose model, generate a small test, evaluate, refine, export, and verify. Evaluation Include normal, boundary, failure, safety, reproducibility, and visual-regression cases with measurable pass criteria. Governance Record rights, consent, privacy, retention, disclosure, provenance, and human approval requirements. Recovery Preserve the last passing output, isolate the failing stage, vary one factor, rerun affected checks, and keep exact settings. Suggested Skill Package Structure skill-name/ SKILL.md agents/ui_metadata.yaml references/model-guide.md references/quality-rubric.md references/safety-and-rights.md scripts/run_workflow.py scripts/validate_output.py assets/templates-or-presets/ AI IMAGE SKILL CATALOG AI Image Skill Catalog | 29 Sources and Notes This catalog is an original planning document based on established generative-imaging concepts. Product names are used descriptively. Verify current licenses, model cards, platform terms, and technical requirements before implementation. Visual in this file was generated with Microsoft Copilot.

```bash
python3 tools-image-toolkit.py run accessibility-delivery.archive-and-handoff-manager --out-dir output
```

All parameters are optional: `path`, `name`.


---

## 8. Tests actually completed

The following checks were executed against this build (not aspirational - each
one ran):

| Check | Method | Result |
| --- | --- | --- |
| Syntax and static analysis | `ast.parse` on every module plus a pyflakes pass over all six `tools-*.py` files | clean; no undefined names, no unused-import errors that affect behaviour |
| Every capability runs | an external harness executed all 184 capability ids through the real CLI in a temporary directory with generated fixtures (PNGs, a caption dataset, a consent file, a prompt list, a job list, a ComfyUI-style workflow, a regression suite, a thermal log) | 184 / 184 completed, each returning a non-empty report and writing `report.md` and `result.json` |
| Deterministic placeholder path | capabilities that take an optional image were run with no image supplied | each generated a placeholder, analysed it, and reported the substitution as a warning |
| Parameter validation | a run with a missing required parameter | rejected with exit code 3 and a message naming the parameter |
| Unknown capability | a run with a malformed id | rejected with exit code 3 and a hint pointing at `capabilities --search` |
| Approval gate | a gated capability without `--yes`, then with `--yes` | exit code 4 and no files written; exit code 0 and files written |
| Dry run | `--dry-run` on a capability | validated the parameters, reported the plan, wrote nothing |
| Output directory handling | an unwritable output directory | reported as invalid input rather than crashing |
| Optional dependency fallback | Pillow/NumPy paths | pixel capabilities raise a blocked integration when the extras are absent; every other capability still runs |
| Every documented example | an external harness extracted all 184 example commands from this file and executed them in a POSIX shell | 184 / 184 ran successfully |

The harness lives outside the Skill Tool folder on purpose - the shipped
package contains no test scaffolding, logs or caches.

---

## 9. Known limits and unavailable integrations

* **No image rendering.** There is no diffusion model, no checkpoint and no GPU
  requirement. Capabilities that would need an engine return engine-ready
  artefacts and say so.
* **No hosted services.** No capability calls an external API. `api_integrator`
  and `comfyui_automator` produce a request specification and a reference
  client that you run yourself.
* **No vision model.** `prompt-adherence-evaluator` compares the written
  request with a written description of the result. It cannot look at a picture.
  `authenticity_analyst` and `misleading-edit-detector` work on measurable
  signals (noise structure, edge halos, compression history, EXIF); they are
  indicators, not proof.
* **No legal or compliance certification.** Rights, consent, copyright and
  cultural screens are pattern and rule based.
* **Screening is English and pattern based.** The safety and rights rule packs
  cover English phrases. A paraphrase in another language may not be caught.
* **Pixel capabilities need the optional extras.** Without Pillow and NumPy
  those capabilities report `blocked` (exit code 6) instead of guessing.
* **Training capabilities plan, they do not train.** `lora_training`,
  `dreambooth_training` and `textual_inversion` compute a configuration and a
  step budget; no trainer is executed.
* **Timings and costs are estimates.** They are derived from the packaged
  reference profile (a 24 GB consumer card) and scale linearly with pixels and
  steps. Measure on your own hardware.
* **The renderer is yours.** Generated A1111 parameter blocks and ComfyUI
  graphs are validated by structure, not by executing them against an engine.

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `invalid_input: missing required parameter: X` | a required parameter was not supplied | run `capabilities --verbose --search <term>` to see the contract, then supply it |
| `invalid_input: parameter validation failed` | a value has the wrong type or is not one of the allowed choices | the message lists every problem; fix them all in one pass |
| `invalid_input: image not found: ...` | a path parameter points at a missing file | check the path; relative paths resolve against the current directory and then the Skill Tool folder |
| exit code 4, `approval_required` | a gated capability was invoked without `--yes` | add `--yes` after reading the plan, or use `--dry-run` first |
| exit code 6, `blocked` | Pillow or NumPy is missing | `python3 -m pip install -r tools-requirements.txt` |
| The report says a placeholder was analysed | no image was supplied for an optional image parameter | pass `--set image=<your file>` for a real measurement |
| `no output directory created` | the run was gated or failed before writing | gated runs deliberately create nothing until approved |
| A run produced nothing in `output/` | `--dry-run` was used | drop `--dry-run` |

### Recovery rules

* Every run is idempotent: the same parameters and the same input files
  produce the same report and the same deterministic seeds.
* A failed run leaves the output directory in place with `result.json`
  describing the failure; re-run after fixing the input rather than deleting
  the directory.
* `batch-prompt-runner` keeps `batch-state.json`; completed job ids are
  skipped on the next run, so an interrupted batch resumes instead of
  restarting.
* `asset-naming-and-metadata-automator` never overwrites an existing file and
  records the applied mapping.
* Nothing in the tool deletes input files. The only in-place operations are
  explicitly gated behind `--yes`.

---

## 11. Extending the Skill Tool

1. **Add a capability.** Append a `register(...)` entry in
   `tools-capabilities.py` with the id, the catalog skill name, the handler
   (`module:function`), the kind, the risk and the parameter contract. Then
   write the handler in the matching module (`tools-creative.py`,
   `tools-media.py` or `tools-production.py`) with the same signature:
   `def handler(ctx) -> Dict[str, Any]`.
2. **A handler receives a context** with `ctx.get(name, default)`,
   `ctx.require(name)`, `ctx.path(name)`, `ctx.paths(name)`, `ctx.out(filename)`,
   `ctx.emit(path)`, `ctx.write_text(...)`, `ctx.write_json(...)`,
   `ctx.warn(message)` and `ctx.artifacts`. Return a dictionary with at least
   `summary`; `data`, `report`, `metrics` and `artifacts` are optional.
3. **Raise `core.InvalidInput`** for bad input, `core.BlockedIntegration` for an
   unavailable service and `core.ToolError` for anything else - the CLI maps
   them to exit codes 3, 6 and 5.
4. **Add reference data** as a new `asset-*.json` file and load it with
   `load_asset("asset-your-file.json")`. Keep the JSON valid and ASCII-safe.
5. **Keep the ids stable.** Ids are the public API of this Skill Tool; change
   them only with a migration note.

---

## 12. Maintenance

* The catalog source of truth is `asset-capability-catalog.json`; the registry
  in `tools-capabilities.py` maps every entry to a handler. A change to either
  must keep the mapping complete.
* Re-run the full 184-capability sweep after any change to
  `tools-capabilities.py`, `tools-core.py` or the CLI.
* Third-party dependency versions live only in `tools-requirements.txt`.
* The tool targets Python 3.10+ and uses no deprecated standard-library APIs.
* `doctor` is the quickest way to confirm the packaged files are intact.

---

## 13. File manifest

| File | Role |
| --- | --- |
| `tools-image-toolkit.py` | primary entry point: CLI, parameter handling, validation, dispatch, approvals, JSON envelope |
| `tools-core.py` | shared primitives: asset loading, module loader, PNG codec, image probing, colour maths, statistics, text and SVG helpers, placeholder images |
| `tools-capabilities.py` | the registry: all 184 capability ids with their handlers, kinds, risks and parameter contracts |
| `tools-creative.py` | 71 creative-side handlers (foundations, prompt design, generation, typography, characters, commerce, architecture, scientific planning, accessibility copy) |
| `tools-media.py` | 44 media-side handlers (pixel analysis and editing, compositing, colour, QA measurement, textures, sprites, export) |
| `tools-production.py` | 69 production-side handlers (SD operations, conditioning, finishing, training, governance, safety, QA planning, automation, performance, handoff) |
| `tools-requirements.txt` | the optional dependency list (Pillow, NumPy) |
| `knowledge-readme.md` | this document |
| `asset-capability-catalog.json` | the parsed source catalog: 23 categories and 184 skill names and descriptions |
| `asset-model-profiles.json` | model, GPU, sampler, ControlNet, LoRA and cost planning data |
| `asset-style-lexicon.json` | creative vocabularies and the living-artist rewrite map |
| `asset-negative-prompts.json` | negative-prompt targets and artefact signatures |
| `asset-quality-rubric.json` | quality rubric, artefact checks, delivery specs, naming and print rules |
| `asset-safety-rules.json` | safety, rights, consent, disclosure, release and cultural rule data |
| `asset-workflow-templates.json` | A1111 and ComfyUI templates, platform sizes, crop ratios, brands, dielines, light rigs |
| `asset-sample-prompts.json` | sample prompts, regression cases, the caption corpus, palettes and variant dimensions |
| `asset-sample-image.png` | a small deterministic PNG used by the documented examples |
| `asset-sample-cutout.png` | a small deterministic RGBA cut-out used by the matting examples |
