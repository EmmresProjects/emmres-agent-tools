# sprite-forge-toolkit

A production toolkit for 2D sprite work: authoring, packing, animation, validation, engine
hand-off, conversion, governance and orchestration. Everything is local, deterministic and
offline. No network calls, no API keys, no build step.

The toolkit implements the 154 capabilities in the SpriteForge skill catalog as 117 runnable
operations across 14 tool files (one entry point, one shared core, eleven modules, one
self-test runner).

---

## 1. Requirements

| Item | Requirement |
| --- | --- |
| Python | 3.10 or newer (tested on 3.13) |
| Pillow | 10 or newer, below 14 (`pip install "Pillow>=10,<14"`) |
| Everything else | Python standard library only |
| Optional | `ffmpeg` on `PATH` for MP4 output. Without it, video export is unavailable and GIF/APNG is used instead. |

Check the runtime at any time:

```bash
python3 tools-spriteforge.py doctor
```

`doctor` prints one PASS/WARN/FAIL line per requirement plus a JSON verdict, and never raises.

---

## 2. Quick start

```bash
# 1. what can this do?
python3 tools-spriteforge.py capabilities

# 2. which tool file handles what?
python3 tools-spriteforge.py modules -v

# 3. I have a task, not a command name
python3 tools-spriteforge.py route --query "eight direction walk cycle packed to an atlas for unity"

# 4. run the shipped end-to-end example (author -> palette -> sheet -> validate -> unity)
python3 tools-spriteforge.py pipeline run --spec asset-sf-pipeline-spec.json --journal build/pipeline-state.json
```

Every operation has its own help:

```bash
python3 tools-spriteforge.py sheet compose --help
python3 tools-spriteforge-sheet.py compose --help     # identical: modules run standalone
```

---

## 3. Two ways to call the same code

**Hub form** — one entry point, module names as words:

```bash
python3 tools-spriteforge.py --root /path/to/project <module> <op> [options]
python3 tools-spriteforge.py <module> <op> [options]     # --root defaults to .
```

**Direct form** — call a module file yourself:

```bash
python3 tools-spriteforge-sheet.py --root /path/to/project compose --input art/frames --output art/hero.png
```

Both route to the same handler and produce the same report. The hub adds only dispatch and
discovery. Use the direct form in scripts and CI where startup cost matters.

### Where files come from

`--root` is the base every relative path resolves against. It defaults to the current
directory. Reads resolve against `--root` first, then fall back to files shipped next to the
tools, so the example JSON specs work from any project folder. Writes must stay inside
`--root`; anything else raises `OUTSIDE_ROOT` unless you pass `--allow-outside-root`.

---

## 4. The tool files

| File | Role | Ops |
| --- | --- | --- |
| `tools-spriteforge.py` | **Primary entry.** Capability listing, module index, `doctor`, `route`, `run` dispatch, short-form `<module> <op>`. | 5 |
| `tools-spriteforge-core.py` | Shared library: option parsing, reporting, error taxonomy, exit codes, image I/O, guards, frame sets, sheet geometry. Not meant to be run directly except `--check`. | 1 (`--check`) |
| `tools-spriteforge-author.py` | Create sprites from data: briefs, shape-layer sprites, palettes, voxel renders, turntables, tokens, variants, rig plans, action sets from pose folders. | 10 |
| `tools-spriteforge-plan.py` | Plan before art: catalog parsing, sheet planning, action matrices, estimates, versions, rights, style bibles, state machines, dependency graphs, routers, change requests. | 12 |
| `tools-spriteforge-sheet.py` | Sheets: compose from frames, extract from sheets, MaxRects atlas packing, slicing, metadata in 10 formats, contact previews, repacking, layout detection, tile checks. | 9 |
| `tools-spriteforge-anim.py` | Motion: retiming, in-betweens, loop probing and repair, secondary motion, path baking, playback review. | 7 |
| `tools-spriteforge-edit.py` | Per-pixel editing and cleanup: crop, pad, offset, resize, rotate, transform, outline, alpha threshold, decontamination, background removal, palette mapping, quantisation, colour swap, hue shift, pixelate, despill, despeckle, alpha bleed, edge clean, normalise, symmetry, up/downscale, series recolour, compositing. | 25 |
| `tools-spriteforge-validate.py` | QA: sheet validation, flicker, silhouette, palette compliance, seams, anchors, regression, accessibility, consistency, similarity, content policy, cultural review, engine smoke tests. | 13 |
| `tools-spriteforge-analyze.py` | Inspection: asset catalogues, duplicate detection, style extraction, gap analysis, layer splits, autotile rules, collision masks, depth maps, motion notes, incremental rebuilds, review packages, layout detection. | 12 |
| `tools-spriteforge-engine.py` | Engine hand-off: Unity, Godot, Unreal Paper2D, GameMaker, RPG Maker, web (canvas/Phaser/PixiJS/Three), mobile budgets, retro hardware limits. | 8 |
| `tools-spriteforge-convert.py` | Export: still formats, animated formats, SVG subset, colour modes, alpha audit, renaming, batch scaling, source splitting, preview video. | 9 |
| `tools-spriteforge-govern.py` | Governance: style gates, licence rollups with credits, approval records for destructive batch actions. | 3 |
| `tools-spriteforge-selftest.py` | Self-check: builds a throwaway project and runs all 117 operations, asserting exit codes and 11 deliberate failure cases. Not an operation module. | 129 cases |

Shipped data assets:

| File | Role |
| --- | --- |
| `asset-sf-capability-catalog.json` | All 154 catalog skills, each mapped to the operation that delivers it, with description and approval flags. Read by `capabilities`, `doctor`, `route`. |
| `asset-sf-sample-spec.json` | A complete 64×64 knight: six layers, three actions (idle, walk, attack) with motion. The worked example for `author frames`. |
| `asset-sf-voxel-spec.json` | Five-box voxel model for `author voxel` and `author turntable`. |
| `asset-sf-rig-spec.json` | Nine-part rig with sockets and anchors for `author rigplan`. |
| `asset-sf-action-matrix.json` | An action matrix (hero, slime, boss) with required/optional/blocked/approved states. |
| `asset-sf-pipeline-spec.json` | A five-step production pipeline used by `pipeline run` and `pipeline farm`. |

---

## 5. Global options

Accepted before the operation and, for the common ones, after it too:

| Option | Meaning |
| --- | --- |
| `--root DIR` | Base directory for relative paths. Default `.` |
| `--output/-o`, `--input/-i` | Per-operation paths, resolved against `--root` |
| `--report PATH` | Write the JSON report to a file. `-` means stdout |
| `--json` | Shorthand for `--report -`: one JSON document on stdout |
| `--quiet` | Suppress progress lines and warnings |
| `--dry-run` | Validate and plan; write no outputs |
| `--timeout SECONDS` | Wall-clock budget. `0` means no limit |
| `--approve TOKEN` | Unlock a guarded operation (repeatable) |
| `--allow-outside-root` | Permit writes outside `--root` |
| `--max-frames`, `--max-pixels` | Resource caps for large inputs |
| `--version` | Print version and exit |

`--json` is safe to pipe: progress and warnings go to stderr, so stdout is exactly one
JSON document.

---

## 6. Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (warnings may still be present in the report) |
| 1 | Internal error — a bug, with `SPRITEFORGE_DEBUG=1` for a traceback |
| 2 | User or input error: bad path, bad argument, unreadable file, failed validation |
| 4 | Approval required — a guarded operation was attempted without `--approve` |
| 5 | Timeout exceeded |
| 6 | Pipeline or recovery failure — at least one step failed |

Errors are one line plus a hint:

```
error [INPUT_NOT_FOUND]: input image not found: /project/nope.png
hint: check the path and --root
```

With `--json`, the same failure is a document with `error.code`, `error.message`,
`error.hint`, `error.details` and `exit_code`.

---

## 7. Guarded operations

Some operations change what ships, so they require an explicit token. The error names the
token you need.

| Token | Operation |
| --- | --- |
| `version-final` | Locking a final version |
| `validate-similarity` | Originality screen against protected designs |
| `validate-content-policy` | Age-rating and platform text screen |
| `validate-cultural` | Cultural motif review routing |
| `validate-engine-smoke` | Engine import readiness |
| `engine-unity`, `engine-godot`, `engine-unreal`, `engine-gamemaker`, `engine-rpgmaker`, `engine-web`, `engine-mobile`, `engine-retro` | Each engine pack |
| `govern-stylegate`, `govern-license`, `govern-batch-gate` | Style gate, licence records, batch write records |
| `pipeline-watch` | Folder watch plans |

---

## 8. Common recipes

### Author a sprite from a spec

```bash
python3 tools-spriteforge.py author frames --spec asset-sf-sample-spec.json --output build/frames
```

Spec shape: `layers[]` with `shape` (`rect`, `ellipse`, `capsule`, `triangle`, `diamond`,
`polygon`, `line`, `star`, `arc`), `box`, `color`, optional `outline`; and `actions[]` with
`frames`, `duration` and `motion[]` (`bob`, `sway`, `rotate`, `slide`, `blink`). Colours may
reference palette roles such as `@base`, `@light_1`, `@shade_1`, `@outline`, `@team_1`.

### Pack a sheet and export metadata

```bash
python3 tools-spriteforge.py sheet compose -i build/frames -o art/hero.png --metadata art/hero.json --cell 64x64 --cols 5 --padding 2
python3 tools-spriteforge.py sheet metadata -i art/hero.json -o art/hero.unity --format unity
```

Outputs are `spriteforge-sheet` v1 JSON: `sheet`, `cell`, `grid`, `pivot`, `frames[]`
(with `frame`, `source_size`, `offset`, `duration`), `actions[]` and `generator`.

### Validate a whole set

```bash
python3 tools-spriteforge.py validate sheet     -i art/hero.png --metadata art/hero.json
python3 tools-spriteforge.py validate flicker   -i build/frames
python3 tools-spriteforge.py validate anchors   -i build/frames
python3 tools-spriteforge.py validate palette   -i build/frames --palette build/palette.json
python3 tools-spriteforge.py validate seam      -i art/tiles --tile
python3 tools-spriteforge.py validate regression -i build/frames --approved art/approved
```

Every check returns `{check, ok, errors, warnings, findings[]}` where each finding has
`severity`, `code`, `message` and `context`. Checks report, they do not repair.

### Repack and export to an engine

```bash
python3 tools-spriteforge.py sheet atlas -i build/frames -o art/atlas.png --metadata art/atlas.json --pow2
python3 tools-spriteforge.py engine unity --metadata art/atlas.json -o art/unity.json --approve engine-unity
python3 tools-spriteforge.py engine godot --metadata art/atlas.json -o art/hero.tres --approve engine-godot
```

### Orchestrate a repeatable pipeline

```bash
python3 tools-spriteforge.py pipeline run --spec asset-sf-pipeline-spec.json --journal state.json
python3 tools-spriteforge.py pipeline recover --journal state.json --only-failed
python3 tools-spriteforge.py pipeline farm --spec farm.json --workers 4
```

A pipeline step is `{id, module, op, args, depends_on, allow_failure, approve}`. Steps run in
dependency order, each in its own subprocess with the journal updated after every step, so a
failure leaves earlier work intact.

---

## 9. Interpreting reports

Every run returns one JSON document:

```json
{
  "tool": "spriteforge", "tool_version": "1.0.0",
  "module": "sheet", "operation": "compose",
  "ok": true, "root": "/project", "dry_run": false,
  "warnings": [{"code": "TOO_SIMILAR", "message": "...", "context": {}}],
  "outputs": [{"kind": "image", "path": "/project/art/hero.png"}],
  "stats": {"frames": 15},
  "summary": {"frames": 15, "output": "/project/art/hero.png"},
  "exit_code": 0, "elapsed_s": 0.12
}
```

* `outputs` is the complete list of files and directories produced.
* `warnings` never fail a run; `ok` and the exit code do.
* `stats` holds machine-readable counts you can assert on in CI.

---

## 10. Testing done on this build

Run from a clean root containing only `SKILL.md` and this folder, using relative paths:

* `doctor` — 11 checks, 0 failures.
* All 112 module operations respond to `--help`.
* All 117 operations executed at least once with real inputs (`tools-spriteforge-selftest.py`).
* Capability asset round-trip: 17 categories, 154 skills; all 94 distinct operation
  references resolve; names identical to the source catalog.
* Invalid input on every module: missing file, corrupt image, malformed JSON, wrong
  extension, empty folder, bad argument, directory where a file was expected — all exit 2
  with a named code, never a traceback.
* Recovery: pipeline fails at a chosen step, `recover --only-failed` resumes from it.
* Changed input: recolouring one frame changes the composed sheet checksum.
* `--dry-run` writes nothing. `--json` emits exactly one parseable document.
* Godot output checked structurally: `load_steps` equals sub_resources + ext_resources + 1,
  per-frame durations preserve total clip time.
* ZIP extracted to a fresh folder and rerun without errors.
* `tools-spriteforge-selftest.py` passes 129/129 from a clean tree in about 30 seconds
  (112 module operations, 5 hub operations, 11 expected-failure cases).

Not tested: real Unity/Godot/Unreal/GameMaker/RPG Maker imports (packs are structurally
checked only), MP4 export (no ffmpeg present), and visual quality of generated art beyond
inspection.

---

## 11. Limitations

Read these before relying on the toolkit in production.

**Scope and honesty**

* Art is generated from geometric shape layers, not from a model. It is deterministic and
  useful for prototypes, placeholders, palette tests and pipeline validation. It is not a
  replacement for a pixel artist.
* The `validate` module measures and reports. It never claims an asset is "good" — only that
  it passes named, checkable rules.
* `content-policy` and `cultural` screen text metadata only. They cannot see visual content.
  Both route to human review by design.
* `similarity` is a perceptual screen, not legal advice.
* `engine-smoke` checks structure (alpha, sizes, names, pivots, metadata). It does not run an
  engine import.

**Platform**

* No ffmpeg means no MP4. `convert preview-video` rejects `.mp4` with `BAD_EXTENSION` and
  suggests GIF/APNG.
* Animated WebP support depends on the linked Pillow build; GIF and APNG always work.
* The SVG writer covers a deliberate subset: `rect`, `circle`, `ellipse`, `polygon`,
  `polyline`, `line`, `path` with M/L/H/V/C/Q/Z. Arcs, gradients, masks, filters, text and
  embedded rasters raise `UNSUPPORTED_SVG` rather than rendering something wrong.
* Raster to SVG emits one `<rect>` per horizontal run — exact, but large for detailed art.
* Retro presets report hardware limits; exceeding them is a `LIMIT_EXCEEDED` finding, not an
  automatic rescale.
* Very large sheets are bounded by `--max-pixels` and `--max-frames`. Raise them
  deliberately.

**Determinism**

* Given the same spec, seed and Pillow version, output is byte-reproducible.
* Across Pillow versions, resampling and palette quantisation may differ slightly.
* `farm` sharding is deterministic for a given spec and worker count, not across different
  worker counts.

---

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `OUTSIDE_ROOT` | Output path leaves `--root`. Write inside the root or add `--allow-outside-root`. |
| `APPROVAL_REQUIRED` | Guarded operation. Re-run with the `--approve` token named in the hint. |
| `SPEC_NOT_FOUND` | The spec path is wrong. Pass a path relative to `--root`, or an absolute path. |
| `UNSUPPORTED_IMAGE` | The file is not a decodable image. Check it is a real PNG/GIF/WebP/JPEG/BMP/TGA/TIFF. |
| `NO_FRAMES` | The input folder has no images, or `--pattern` filtered them all out. |
| `OUTPUT_IS_DIR` | A file output points at a folder. Choose a file path or use an operation that takes a folder. |
| `INPUT_IS_DIR` | A single-file input got a folder. Use the folder form or point at one file. |
| `BAD_SIZE` | Use `WIDTHxHEIGHT`, for example `64x64`. |
| `TIMEOUT` | Raise `--timeout`, shrink the batch, or split the job. |
| Exit 1 with no message | Re-run with `SPRITEFORGE_DEBUG=1` for a traceback, and treat it as a bug. |

---

## 13. Design rules this toolkit follows

1. **One entry point.** `tools-spriteforge.py` is the front door; other files exist because
   they hold genuinely different functionality.
2. **Report, do not guess.** Every operation returns structured data with counts, findings
   and output paths.
3. **Fail loudly and early.** Bad input is rejected before any work, with a named code and a
   hint. Nothing is silently skipped.
4. **Never write outside the root** unless explicitly allowed.
5. **Approval for consequential actions.** Anything that decides what ships asks first.
6. **Deterministic by default.** Seeds are explicit, ordering is stable, hashes are checked.
7. **Standard library plus Pillow.** No hidden dependencies, no network, no build step.
