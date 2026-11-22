# image-creation-editing — Skill Tool knowledge base

One folder, one entry point, every image-creation, editing, checking and delivery capability
covered by the 29 catalog categories: **29 categories → 290 named skill concepts → 41 operations
→ 43 CLI commands**.

Everything here runs locally with Python and Pillow. Nothing calls the network unless you configure
an image provider yourself and switch it on.

- Entry point: `python tools-imagekit.py`
- Self-test: `python tools-imagekit.py selftest`
- Capability list: `python tools-imagekit.py capabilities`
- Catalog browser: `python tools-imagekit.py skills --search poster`

---

## 1. Install and first run

```bash
python -m pip install -r tools-requirements.txt
python tools-imagekit.py doctor                 # runtime, formats, fonts, provider readiness
python tools-imagekit.py selftest               # 17 checks, real files, ~7 s
python tools-imagekit.py capabilities           # what this tool can do
```

Requirements: Python 3.9+ and Pillow 10+. No other dependency is required. `doctor` reports which
write formats your Pillow build supports (`png jpg webp tiff gif avif bmp` on a standard build) and
whether a scalable font was found for text layouts.

If no system font is found, point the tool at one:

```bash
export IMAGEKIT_FONT=/path/to/Inter-Regular.ttf
```

Every command writes real files into `--out` (default `./out`) and prints a JSON envelope with
`--json`. Exit codes are stable, see §9.

---

## 2. The mental model

| Layer | What it is | Where it lives |
|---|---|---|
| Catalog | 29 categories × 10 skill concepts from the source document, each mapped to an operation and an execution mode | `asset-skill-catalog.json` |
| Operation | One implemented handler, e.g. `icon`, `cutout`, `qa`, `pipeline` | `tools-imagekit-*.py` |
| Command | The CLI name you type; 43 of them, grouped 9 plan / 7 make / 11 edit / 3 motion / 6 check / 3 govern / 3 manage (+ meta) | `tools-imagekit.py` |
| Asset | A JSON data file that drives behaviour, palettes, presets, policy | `asset-*.json` |
| Context | One run: output root, config, policy, seed, approval state, audit trail | `tools-imagekit-core.py` |

Execution modes recorded per catalog skill (this is the honest capability split):

| Mode | Count | Meaning |
|---|---|---|
| `local` | 209 | Produces the deliverable file itself, offline. |
| `assisted` | 37 | Produces a deterministic reference composition **plus** the prompt, spec and provenance needed to finish it in an image model. Photoreal synthesis itself needs a configured provider. |
| `review` | 43 | Produces measurement, analysis or a plan; a human makes the final judgement. |
| `provider` | 1 | Needs an external model (14.6 colourisation). Wire it through `provider`. |

Check any concept before you rely on it:

```bash
python tools-imagekit.py skills --mode assisted --category 2
python tools-imagekit.py skill 7.4            # runs the mapped operation directly
```

---

## 3. Command reference

All commands accept: `--out DIR --config FILE --json --quiet --verbose --seed N --force
--approve --approval-note TEXT --dry-run`. `--dry-run` runs the full pipeline in memory, writes
nothing, and reports the paths it would have written.

### 3.1 plan — decide before you draw

| Command | Purpose | Key flags |
|---|---|---|
| `brief` | Free text → structured production brief, open questions, acceptance criteria | `--brief`, `--input` |
| `prompt` | Model-ready prompt with sections, negative constraints, dialect | `--dialect generic\|diffusion\|chat`, `--emphasis` |
| `variants` | Distinct concept directions with test hypotheses | `--count`, `--axes` |
| `spec` | Delivery spec: size, colour, format, dpi, naming, metadata plan | `--use`, `--size`, `--format`, `--dpi` |
| `composition` | Focal hierarchy, thirds, type safe zone, crop safety | `--size`, `--use`, `--focal`, `--horizon`, `--subjects` |
| `direction` | Art-direction document; or score a candidate against it | `--mode create\|check`, `--direction`, `--candidate` |
| `styleguide` | Brand, world, illustration or photography guide (+SVG swatches) | `--kind`, `--colors` |
| `reference` | Analyse reference images → palette, tone, texture, transfer parameters | `--input a.png,b.png` |
| `palette` | Accessible palette with WCAG pairs and naive CMYK print equivalents | `--colors`, `--preset`, `--strategy` |

### 3.2 make — produce files

| Command | Output |
|---|---|
| `render` | Deterministic scene from one of 24 preset grammars + provenance sidecar + manifest |
| `icon` | `app-icon`, `badge`, `monogram`, `logo-shield`, `sticker`, `glyph` → PNG set (≤12 sizes), true vector SVG, report |
| `pattern` | 12 seamless tile kinds → tile, tiled preview, SVG wrapper, seam report |
| `layout` | poster/social/story/banner/cover/brochure/presentation/thumbnail → PNG or PDF + legibility and overflow spec |
| `diagram` | flow, cycle, hierarchy, bar-chart, annotated → PNG + SVG + spec |
| `sheet` | storyboard, comic, contact sheet → PNG + panel map JSON |
| `mockup` | Artwork onto bottle, box, pouch, cup, tote, phone or poster with face bounds in the manifest |

### 3.3 edit — change and repair

`edit` (crop/resize/fit/rotate/flip/pad/max-dim/auto-orient) · `retouch`
(heal/blur/sharpen/denoise/clone/despeckle on `--region x,y,w,h`, repeatable) · `cutout`
(auto/chroma/border/color + tolerance + feather, emits cut-out **and** matte) · `composite`
(subject over background with contact shadow) · `grade` (exposure, contrast, saturation,
temperature, tint, gamma, lift, vignette, grain, bloom, relight direction) · `restore`
(denoise, despeckle, autolevels, upscale ≤4×, sharpen, before/after metrics) · `convert`
(png/jpg/webp/tiff/avif/gif/bmp, mode, dpi, metadata strip, reopened and verified) · `tile`
(aligned panels with overlap) · `bleed` (mm bleed, trim and safe guides, TIFF) · `cropset`
(subject-aware crops across ratios).

### 3.4 motion — animation preparation

`gif` (`--motion pan|zoom|pulse|fade|rotate|parallax`, or frame files, or an animated input)
· `sprite` (sheet + atlas JSON, can synthesise frames from one image) · `frames` (extract,
renumber, palette-lock).

### 3.5 check — measure

`analyze` (content, style, palette, sharpness, stress test) · `mask` (saliency, depth proxy,
region map) · `diff` (SSIM, PSNR, RMSE, heatmap, worst regions, verdict) · `qa` (policy gates;
`--fail-on warn|error` returns exit 6) · `trace` (brief requirements vs delivered files) ·
`verify` (every file opens, dimensions and mode read back).

### 3.6 govern — responsibility

`safety` (lexical risk scan of a brief: likeness, trademark, deception, sensitive content;
raises approval gates) · `provenance` (`write`, `read`, `verify`; sidecar + PNG metadata +
SHA-256 of the delivered bytes) · `dashboard` (HTML + JSON production report from a folder
or a pipeline state file).

### 3.7 manage — scale

`library` (`index`, `dedupe`, `name`, `versions`, `rights`, `gallery`, `handoff`, `usage`,
`preserve`, `master`, `verify-handoff`) · `pipeline` (JSON plan, dependencies, retries,
timeouts, checkpoints, resume, cancellation) · `provider` (`list`, `check`, `generate`).

### 3.8 meta

`help [COMMAND]` · `capabilities [--json]` · `skills [--category N] [--search T] [--mode M]` ·
`skill ID` · `doctor [--json]` · `selftest [--suite all|fast|policy] [--keep] [--json]` ·
`--version`.

---

## 4. Configuration

`--config FILE` replaces `asset-config-default.json`. Copy it, change it, keep it versioned.
Every key is optional; missing keys fall back to the built-in defaults.

```jsonc
{
  "paths":   { "output_dir": "out", "library_dir": "library" },
  "limits":  { "max_pixels": 40000000, "max_batch_items": 200, "max_frames": 240,
               "max_steps": 60, "default_timeout_s": 300, "max_retries": 3 },
  "naming":  { "pattern": "{project}-{content}-{dimensions}-v{version}-{status}",
               "separator": "-", "max_length": 96 },
  "formats": { "default": "png", "quality": 92, "dpi": 300 },
  "render":  { "default_preset": "photo-studio", "seed": 20260101 },
  "quality": { "default_gate": "standard" },
  "pipeline":{ "checkpoint": true, "continue_on_error": false },
  "providers": { "allow_network": false, "list": { } },
  "catalog_file": "asset-skill-catalog.json",
  "quality_policy_file": "asset-quality-policy.json",
  "render_presets_file": "asset-render-presets.json",
  "brief_template_file": "asset-brief-template.json"
}
```

Verified behaviours: `limits.max_pixels` rejects an oversized render; `limits.max_batch_items`
rejects a batch of 3 when set to 2; `naming.separator` and `--out` change delivered names and
locations; `quality_policy_file` can point at your own gate thresholds.

---

## 5. Quality policy

`asset-quality-policy.json` holds the rules `qa` and `safety` apply:

- `qa.resolution / sharpness / contrast / clipping / banding / color / noise / text /
  transparency / naming / delivery` — thresholds per check.
- `qa.gates` — which checks belong to `technical`, `visual`, `brand`, `delivery`.
- `text_rules` — lexical risk rules with severity `block | approval | disclose | review | info`.
- `disclosures` — the synthetic-content wording written into provenance records.
- `approval_rules` — actions that require `--approve`.
- `artifact_heuristics` — halo and repeated-block thresholds, explicitly advisory.

Advisory checks raise `warn`, never `error`. A gate returns `pass | warn | error` with per-check
values, limits and notes, so a reviewer can see the measurement, not just the verdict.

---

## 6. Approval gate

These actions refuse to run without `--approve --approval-note "why this is safe"` and exit `4`:

| Action | Trigger |
|---|---|
| `handoff.apply` | `library --action handoff --apply --target DIR` (writes/overwrites a delivery bundle) |
| `handoff.publish` | `library --action handoff --publish` (transmits assets outside the workspace) |
| `library.dedupe.apply` | `library --action dedupe --apply` (moves files into `_quarantine/`) |
| `library.rename.apply` | `library --action name --apply` (renames files in place) |
| `safety.consent` | Recording a consent claim for a depicted real person |
| `provider.generate` | Any non-mock provider call (may bill, may log) |

Approvals are recorded in the run envelope (`approvals`) and in `audit-log.jsonl`.

---

## 7. Providers

The tool works offline. To call a real image model, add it to the config:

```jsonc
"providers": {
  "allow_network": true,
  "list": {
    "my-model": {
      "kind": "http",
      "endpoint": "https://api.example.com/v1/images",
      "method": "POST",
      "auth_env": "MY_MODEL_KEY",
      "auth_scheme": "Bearer",
      "request_template": { "prompt": "{{prompt}}", "size": "{{size}}" },
      "response_image_base64_pointer": "/data/0/b64_json",
      "response_image_url_pointer": "/data/0/url",
      "timeout_s": 120,
      "retries": 2
    }
  }
}
```

Rules enforced by code: `allow_network` must be true; `auth_env` names an environment variable
(no credentials are ever stored in the tool); readiness is checked before any call (`provider
--action check`); a call needs approval; the response is size-capped and validated before it is
accepted; the returned image gets a provenance sidecar naming the provider and the prompt.

`mock-local` is a deterministic offline adapter used by the self-test and for wiring checks.

---

## 8. Pipelines

```jsonc
{
  "version": 1,
  "name": "campaign",
  "defaults": { "project": "acme", "out": "build" },
  "timeout_s": 180, "retries": 1, "continue_on_error": false,
  "steps": [
    { "id": "brief",  "op": "brief", "args": { "brief": "soft studio serum bottle" } },
    { "id": "render", "op": "render", "args": { "preset": "photo-studio", "size": "1200x1200" },
      "deps": ["brief"] },
    { "id": "gate",   "op": "qa",
      "args": { "input": "{{steps.render.outputs:png}}", "fail_on": "error" },
      "deps": ["render"], "requires": ["acme-brief.json"] }
  ]
}
```

- Steps run in dependency order; cycles and unknown ops are rejected by `validate`.
- `defaults` apply to every step; a step's own `args` win; a step-level `out` runs that step in
  its own output root.
- Placeholders: `{{steps.ID.outputs.N}}`, `{{steps.ID.outputs:substring}}`,
  `{{steps.ID.data.path}}`, `{{out}}`.
- Checkpoints: `<out>/<name>-state.json` after every step. `--resume` skips steps already `ok`
  (reported as `skipped-resumed`).
- Retries use exponential backoff and only for transient codes (`provider_unavailable`,
  `internal`, `encode_failed`).
- Timeouts are hard on POSIX (SIGALRM); on platforms without SIGALRM the run reports
  `hard_timeout_enforced: false` and the timeout is advisory.
- Ctrl-C sets the cancellation flag: the current step finishes its write, later steps are marked
  `cancelled`, and the state file remains resumable.
- Report: `<name>-pipeline-run.json` + `.md`.

---

## 9. Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Usage error (bad flag, unknown preset, out-of-range value) |
| 3 | Input error (missing file, unreadable image, refusal to overwrite) |
| 4 | Approval required |
| 5 | Provider unavailable or misconfigured |
| 6 | Policy or quality gate failed |
| 130 | Cancelled |

Errors are structured, not prose:

```json
{ "ok": false, "error": { "code": "input", "message": "Input not found: hero.png",
  "hint": "Check that the path exists and is spelled correctly.", "details": {} } }
```

with `--json`, and the same fields on one line plus a `hint:` line without it.

---

## 10. Assets in this folder

| File | Role |
|---|---|
| `asset-skill-catalog.json` | 29 categories, 290 skills, operation index, mode counts, per-skill example command |
| `asset-config-default.json` | Default config: paths, limits, naming, formats, quality, providers |
| `asset-quality-policy.json` | QA thresholds, gate groups, text-risk rules, disclosures, approval rules |
| `asset-render-presets.json` | 24 render presets, subject forms, 29 style aliases |
| `asset-prompt-presets.json` | Lenses, camera heights, lighting, palettes, media, eras, negatives, output uses |
| `asset-brief-template.json` | Brief field schema, open-question rules, acceptance library |

Nothing in this folder is decorative: removing any asset breaks a documented command.

---

## 11. Tool files and roles

| File | Role |
|---|---|
| `tools-imagekit.py` | Single entry point: parser, dispatch, JSON envelope, signal handling, exit codes, meta commands |
| `tools-imagekit-core.py` | Foundation: config/policy loading, context, image IO, metadata, metrics, colour, text policy scan, raster primitives, naming |
| `tools-imagekit-plan.py` | The 9 planning operations |
| `tools-imagekit-render.py` | Scene compositor (41 primitives), treatments, provenance, 24 presets, mockups |
| `tools-imagekit-compose.py` | Icons/vector marks, patterns, layouts, diagrams, sheets |
| `tools-imagekit-edit.py` | Geometry, retouch, cutout, composite, grade, restore, convert, tile, bleed, cropset, motion |
| `tools-imagekit-qa.py` | Analysis, masks, diff, gates, trace, verify, safety, provenance, dashboard |
| `tools-imagekit-library.py` | Index, dedupe, naming, versions, rights, gallery, handoff, usage, preservation, masters |
| `tools-imagekit-providers.py` | Provider interface, offline mock, generic HTTP adapter |
| `tools-imagekit-pipeline.py` | Plan validation, dependency order, retries, timeouts, checkpoints, resume |
| `tools-imagekit-tests.py` | The self-test suite (17 checks) |

---

## 12. Worked example

```bash
P="acme"
python tools-imagekit.py brief --brief "Soft studio photo of an amber serum bottle, calm light" \
  --project $P --out out
python tools-imagekit.py palette --brief "warm amber skincare" --project $P --out out
python tools-imagekit.py render --preset photo-studio --size 1600x1600 --seed 7 \
  --project $P --name hero --out out
python tools-imagekit.py icon --kind app-icon --glyph leaf --palette "#0f766e,#99f6e4,#134e4a" \
  --sizes 512,128,32 --project $P --name mark --out out
python tools-imagekit.py layout --kind social --headline "Hydration that holds" \
  --subhead "New serum, 30 ml" --cta "Shop now" --image out/acme-hero-1600x1600-v01-draft.png \
  --project $P --name social --out out
python tools-imagekit.py cutout --input out/acme-hero-1600x1600-v01-draft.png --project $P --out out
python tools-imagekit.py qa --inputs out/acme-social-1080x1080-v01-review.png --fail-on error \
  --project $P --out out
python tools-imagekit.py trace --brief-file out/acme-brief.json \
  --input out/acme-social-1080x1080-v01-review.png --project $P --out out
python tools-imagekit.py library --action handoff --library out --target delivery --apply \
  --approve --approval-note "client delivery approved" --project $P --out out
```

Every step prints the files it wrote; `out/audit-log.jsonl` records what ran.

---

## 13. Limitations — read this before promising an outcome

1. **No photoreal synthesis.** `render` is a deterministic geometric compositor, not a diffusion
   model. Its output is a composition reference: layout, palette, light direction, framing. Real
   photographic detail requires a configured provider (`provider`) or an external model using the
   prompt and spec this tool emits. 37 catalog skills are therefore `assisted`, not `local`.
2. **Colourisation (14.6) is the one `provider` skill.** It cannot be done in-process.
3. **43 review skills produce evidence, not verdicts.** Aesthetic, semantic and
   rights judgements stay with a human; the tool supplies measurements, comparisons and checklists.
4. **Mockups approximate perspective.** Artwork is fit to the printable face and warped by slice
   scaling; validate against a real dieline for production.
5. **CMYK is reported, not converted.** Without an ICC profile, print colour is a naive
   approximation and is labelled as such in the spec.
6. **Auto-cutout is colour-based** (border flood + chroma/colour key + feather + island removal).
   It fails on busy backgrounds, glass and fine hair — use a provider mask or hand-drawn matte.
7. **Text is drawn with a single font family** found on the system; layout legibility is asserted
   against the policy thresholds, not against your brand typeface metrics.
8. **Banding, halo and repeated-block checks are heuristics** and are advisory by design.
9. **No network, no credentials, no telemetry** unless you configure a provider yourself.
10. **Timeouts are POSIX-only for hard enforcement**; elsewhere they are advisory and reported as
    such.
11. **No SBOM/virus scan and no cloud storage integration**; the pipeline writes to local paths only.
12. **Fonts, avatars, LUTs and sample photos are not bundled** — nothing unused, nothing unverified.

---

## 14. Troubleshooting

| Symptom | Fix |
|---|---|
| `Refusing to overwrite existing output` (exit 3) | Choose another `--name`, pass `--force`, or change `--out`. |
| `usage: --preset ... must be one of` (exit 2) | `python tools-imagekit.py skills --search <word>` or read `asset-render-presets.json`. |
| `Provider is not usable yet` (exit 5) | `provider --action check --name X` lists every missing piece. |
| Gate keeps returning `error` | Read `failures` (hard) vs `advisories` (soft) in the gate report; adjust thresholds in your policy copy. |
| Text renders in a fallback font | Set `IMAGEKIT_FONT` to a `.ttf`/`.otf` path; `doctor` confirms. |
| `audit-log.jsonl` grows | It is append-only by design; archive or delete per project. |
| Selftest fails after you edit a module | `python tools-imagekit.py selftest --suite fast --keep`, then inspect the kept workspace. |

---

## 15. What the self-test proves

`python tools-imagekit.py selftest` runs 17 checks against real files (last verified run:
17/17 passing, ~6.5 s):

catalog mapping integrity · asset loading · planning outputs · three render presets · icon sizes and
real SVG · pattern seam · layout legibility · diagram and sheet · six-step edit chain · cutout and
composite · GIF/sprite/frame extraction · analysis, masks, diff, verify, reference transfer · gate
pass and gate failure · safety scan, consent gate, provenance write and verify · library index,
dedupe, gallery, handoff · pipeline validate, run, resume, retry, timeout, cancellation ·
provider list/check/generate and blocks on an unconfigured HTTP provider · CLI exit codes
0/2/3/6 and `help`/`skills` · policy rules and gate groups.
