# pixel-art-media — Skill Tool knowledge base

Version 1.0.0. One unified, offline, deterministic Skill Tool covering the full
"Pixel Art, Sprite, Voxel, Animation, and Pixel Media Skill Catalog"
(29 categories, 318 catalog skills). Every capability is reachable through a
single prefixed entry point; advisory skills are served by parameterized
guide/document generators, and pipeline-author skills generate real,
syntax-checked scripts (clearly labeled as generated, never executed here).

---

## 1. Purpose and capabilities

Create, edit, animate, convert, package, validate, preserve, and publish
pixel-based media:

- **Imaging (19 commands):** format conversion, integer scaling, transforms,
  crop/trim/pad, outlines, orphan cleanup, dithering, shadows, silhouettes,
  normal maps, glitch/recolor/background removal, layer
  splitting/compositing, SVG export, retro display simulation.
- **Palettes (7):** extraction, mood/scheme generation, application with
  dithering, cycling animations, 8-format conversion, palette analysis,
  palette swap.
- **Sheets & animation (8):** GIF/APNG/WebP encode/decode, frame-sequence
  export, atlas packing (7 metadata formats) + metadata conversion, grid
  slicing, Aseprite sheet export, tag/pivot/event sidecars.
- **Generators (14):** procedural sprites, 8 effect animations, 9 motion
  presets, 7 UI kinds, 12 icon kinds (+all), 12 seamless pattern kinds, 16/47-mask
  autotile tilesets, isometric primitives, bitmap text + 5 text effects, font
  sheets, voxel build/render/turntable/stack.
- **Delivery (7):** engine exports (Godot, Unity, web, Phaser, Pixi, RPG Maker,
  GameMaker, Unreal, retro handheld, VTT), 11 platform presets,
  Tiled tilemap export (TMX/TSX/JSON/CSV), captions (burn-in + SRT),
  storyboards/animatics, contact sheets, pipeline script authoring.
- **Quality (8):** constraint validation, pixel-art critique, seamless-tile
  checking/fixing, grid-damage restoration, folder audits (incl. secret
  scanning), license records, reopen tests, diff comparison.
- **Planning (8):** production briefs with IP-keyword flags, art bibles,
  platform constraints, scope estimates, review rubrics, practice sessions,
  per-category/per-skill guides, capability/catalog listing.
- **Entry (2):** `batch` (JSON job pipelines) and `selftest` (71-test suite).

Catalog coverage: all 29 categories and 318 skills are mapped in
`pixel-art-media/asset-catalog.json` with an execution mode per skill:
**command** (179, direct automation), **assist** (87, the tool produces
measurements/artifacts; artistic judgment stays with you), **guide** (51,
structured production guidance), **built-in** (1, the selftest). Run
`capabilities --catalog` for the live map.

## 2. Folder layout and file roles

All files sit directly in `pixel-art-media/` (no subfolders):

| File | Role |
|---|---|
| `tools-pixel-media.py` | Entry point: CLI, config merge, JSON I/O, exit codes, timeout, batch, selftest dispatch |
| `tools-imaging.py` | Imaging commands + shared library (safe I/O, limits, color math, grid detection) |
| `tools-palettes.py` | Palette commands + 8-format palette reader/writer |
| `tools-sheets.py` | Animation codec, frame resolution, atlas packing/slicing, tags |
| `tools-generators.py` | Procedural sprite/effect/motion/UI/icon/pattern/tileset/text/voxel generators |
| `tools-delivery.py` | Engine/platform exports, tilemaps, captions, storyboards, contact sheets |
| `tools-quality.py` | Validation, critique, seam-check, audit, license records, reopen-test, compare |
| `tools-planning.py` | Brief/bible/constraints/scope/rubric/practice/guide/capabilities |
| `tools-selftest.py` | 71 unittest tests exercised through the real CLI |
| `tools-requirements.txt` | `Pillow>=10.0,<14` (the only third-party dependency) |
| `asset-config.json` | Default limits, I/O retry policy, defaults (fps, seed, alpha threshold) |
| `asset-presets.json` | 11 platform export presets + 6 retro system palettes/specs |
| `asset-guides.json` | 29 category guides: workflow, checklist, pitfalls, example commands |
| `asset-catalog.json` | All 318 catalog skills with descriptions, modes, and mapped operations |
| `asset-font.json` | CC0 bitmap fonts: 79 glyphs at 5x7 and 57 glyphs at 3x5 |

Internal references are relative to the future skill root (e.g.
`pixel-art-media/asset-config.json`), forward slashes, exact case.

## 3. Requirements and setup

- Python **3.9+** (developed and tested on 3.13).
- **Pillow >= 10, < 14** (tested on 13.0.0). Pillow 14 removes a deprecated
  API this tool uses; the pin prevents breakage.
- No network access, no accounts, no AI providers, no secrets. Everything is
  local and deterministic (seeded RNG).
- Optional, only if you want to *run* generated pipeline scripts yourself:
  ffmpeg, ImageMagick, Aseprite, Node.js. The tool never executes them.

Setup (bash):

```bash
python3 -m pip install -r pixel-art-media/tools-requirements.txt
python3 pixel-art-media/tools-pixel-media.py --help
python3 pixel-art-media/tools-pixel-media.py capabilities
python3 pixel-art-media/tools-pixel-media.py selftest
```

On Windows PowerShell use `py -m pip install -r pixel-art-media\tools-requirements.txt`
and `py pixel-art-media\tools-pixel-media.py <command>`; all tool paths inside
the package remain forward-slash relative.

## 4. Entry point, common flags, output, exit codes

```bash
python3 pixel-art-media/tools-pixel-media.py <command> [args] [--json] [--config FILE] [--force] [--dry-run] [--quiet]
```

- `--json` — explicit JSON output (JSON is already the default; accepted for clarity).
- `--config FILE` — user JSON merged over `pixel-art-media/asset-config.json`.
- `--force` — **approval gate**: required to overwrite existing outputs or edit
  sidecars in place. Without it, existing outputs raise `output-exists` (exit 5).
- `--dry-run` — validate and report without writing anything.
- `--quiet` — suppress stdout on success (errors still print to stderr).

Success prints one JSON object to stdout:
`{"ok": true, "command": ..., "written_files": [...], "warnings": [...], "elapsed_ms": ..., ...command fields}`.
Errors print to stderr:
`{"ok": false, "command": ..., "error": {"code": ..., "message": ..., "hint": ...}}`.

Exit codes: **0** ok · **1** internal error · **2** bad arguments/input format ·
**3** input/config/asset not found · **4** limit exceeded or timeout ·
**5** output exists (needs `--force`) · **6** dependency missing ·
**7** validation failed · **8** write failed · **130** cancelled (Ctrl-C).

## 5. Configuration

`pixel-art-media/asset-config.json` (override any key via `--config`):

```json
{
  "limits": {"max_side": 4096, "max_pixels": 8294400, "op_pixels": 1048576,
             "max_frames": 256, "max_colors": 256, "max_sheet_side": 4096,
             "max_inputs": 512, "timeout_seconds": 180},
  "io": {"write_retries": 2, "retry_delay_seconds": 0.2},
  "defaults": {"fps": 12, "seed": 7, "gif_min_delay_ms": 20,
               "alpha_threshold": 128, "background": "#00000000"}
}
```

- `max_side`/`max_pixels` bound any single image (8,294,400 = 3840x2160, so the
  4K wallpaper preset works out of the box).
- `op_pixels` bounds slow per-pixel Python operations (cleanup, critique
  metrics, seam-check, etc.); larger images get a clear `limit-*` error.
- `timeout_seconds` is enforced with SIGALRM on POSIX; on Windows it is not
  enforced (documented limitation). Timeouts exit 4 and leave no partial files.
- Writes are atomic (temp file + rename) with bounded retries; validation
  errors and cancellation are never retried; temp files are cleaned on failure.

## 6. Command reference (all 73)

Every command also supports the common flags from section 4. `--help` on any
command prints its exact signature. Paths below are examples; use your own.

### 6.1 Imaging (`tools-imaging.py`)

| Command | What it does | Example |
|---|---|---|
| `convert` | Resize/reformat with pixel-safe policies, optional quantize+dither, outline/cleanup, integer preview, palette sidecar | `convert photo.jpg out/sprite.png --width 64 --colors 16 --dither bayer4 --preview-scale 4 --palette-out out/pal.json` |
| `scale` | Integer or targeted scaling | `scale in.png out.png --factor 4` |
| `transform` | flip-h, flip-v, rot90/180/270, transpose, transverse | `transform in.png out.png rot90` |
| `crop` | Rectangle crop | `crop in.png out.png --x 2 --y 2 --width 8 --height 8` |
| `trim` | Trim transparent border (+offset meta) | `trim in.png out.png --padding 1 --meta out/trim.json` |
| `pad` | Pad to canvas with anchor | `pad in.png out.png --width 24 --height 24 --anchor center` |
| `outline` | outer/inner/inline 1px outline | `outline in.png out.png --mode outer --color "#101018"` |
| `cleanup` | Remove orphan pixels | `cleanup in.png out.png --passes 2` |
| `dither` | Ordered (Bayer 2/4/8) dithering to N colors | `dither in.png out.png --colors 6 --matrix 8 --strength 40` |
| `shadow` | Drop or blob shadow | `shadow in.png out.png --blob` |
| `silhouette` | Solid silhouette | `silhouette in.png out.png --color "#222244"` |
| `normal-map` | Height-to-normal map from luminance | `normal-map in.png out.png --strength 3` |
| `glitch` | Seeded glitch (bands, channel shift) | `glitch in.png out.png --seed 9 --intensity 0.7` |
| `recolor` | 8 mood presets or HSV shift | `recolor in.png out.png --preset night` |
| `remove-bg` | Flood/key background removal | `remove-bg in.png out.png --tolerance 12` |
| `palette-swap` | Remap one palette onto another | `palette-swap in.png out.png --src a.json --dst b.json` |
| `split-layers` | Split by connected component or color | `split-layers in.png out/layers --mode color --max-layers 6` |
| `composite` | Alpha-composite layers (files or JSON spec) | `composite out/comp.png layer1.png layer2.png` |
| `svg-export` | Rect-run vectorization of pixel art | `svg-export in.png out/vec.svg` |
| `display-sim` | crt/lcd/led/gameboy/mono/1bit simulation | `display-sim in.png out/crt.png --mode crt --scale 4` |

### 6.2 Palettes (`tools-palettes.py`)

| Command | What it does | Example |
|---|---|---|
| `palette-extract` | Extract N colors, sorted by frequency/luminance/hue | `palette-extract in.png out/pal.json --colors 12 --sort hue` |
| `palette-generate` | 8 moods x 5 schemes, ramped | `palette-generate out/pal.json --mood cozy --ramps 3 --steps 5` |
| `palette-apply` | Quantize to palette (+dither, optional indexed PNG) | `palette-apply in.png out.png --palette pal.json --dither floyd --indexed out/indexed.png` |
| `palette-cycle` | Color-cycle animation (GIF/APNG/WebP + frames) | `palette-cycle in.png out/cycle.gif --palette pal.json --frames 5 --frames-dir out/cycle-frames` |
| `palette-convert` | Convert between json/gpl/pal/hex/act/ase/png (swatch) | `palette-convert pal.json out/pal.gpl --name "My Palette"` |
| `palette-analyze` | Ramp/contrast/harmony report + swatches | `palette-analyze pal.json --image in.png --report out/report.json --swatches out/sw.png` |
| `palette-swap` | Remap one palette onto another | `palette-swap in.png out.png --src a.json --dst b.json` |

Palette JSON schema: `{"name": "...", "colors": ["#rrggbb", ...]}` (a bare hex
array is also accepted). PNG palettes are written as indexed swatches so order
roundtrips exactly. JASC `.pal`, Microsoft `.act`, Aseprite `.ase`, `.gpl`,
`.hex` are read and written; `.act`/`.ase` are RGB-only by format.

### 6.3 Sheets & animation (`tools-sheets.py`)

| Command | What it does | Example |
|---|---|---|
| `anim-encode` | Frames/dir/sheet → GIF/APNG/WebP (loop, reverse, pingpong, hold, scale) | `anim-encode frames/ --output out/loop.gif --fps 12 --scale 4 --pingpong` |
| `anim-decode` | Animation → PNG frames + durations.json | `anim-decode in.gif out/frames` |
| `sequence-export` | Frames → PNG/BMP/TGA/TIFF sequence | `sequence-export frames/ --outdir out/seq --format tga --scale 2` |
| `sheet-pack` | Pack frames → atlas + metadata (7 formats), trim/extrude/POT/pivots | `sheet-pack frames/ --output out/atlas.png --meta out/atlas.json --meta-format json-hash --extrude 1 --power-of-two` |
| `sheet-slice` | Sheet → frames by cell size or auto-detection | `sheet-slice sheet.png out/frames --grid 16x16` |
| `atlas-meta` | Convert json-simple manifest → json-hash/json-array/aseprite/csv/xml/plist | `atlas-meta atlas-simple.json out/meta.xml --format xml --image-name atlas.png` |
| `tags` | Create/edit tag/pivot/event sidecars | `tags out/anim.json --init --frames 8 --size 16x16 --fps 12` then `tags out/anim.json --add-tag idle:0:3 --add-event 2:footstep --set-pivot idle:0.5,1.0 --force` |
| `aseprite-export` | Horizontal/packed sheet + Aseprite-style JSON with tags | `aseprite-export frames/ --output out/hero.png --layout horizontal --tag idle:0:3` |

Notes:
- `--grid WxH` is the **cell size in pixels** (e.g. `16x16`), not columns x rows.
- `anim-encode`/`sheet-pack` accept a directory, a glob/list of files, an
  animated file, or one sheet image with `--grid`.
- Re-encoding without `--fps` preserves source frame durations exactly.
- GIF is a 256-color, binary-alpha format; Pillow merges identical consecutive
  frames on save (durations are summed). This is expected GIF behavior, not
  frame loss — use APNG/WebP when you need exact frame counts or soft alpha.
- Tag sidecar schema: `{"frames": N, "size": [w,h], "fps": F, "pivots": {name:[x,y]}, "tags": [{name,from,to,direction}], "events": [{frame,name}]}`.
  Editing an existing sidecar requires `--force` (in-place approval gate).

### 6.4 Generators (`tools-generators.py`)

| Command | What it does | Example |
|---|---|---|
| `sprite-generate` | Seeded creature/robot/bug/ghost/slime sprites (+sheet) | `sprite-generate out/sprites --kind creature --size 16 --count 8 --seed 21 --sheet out/sprites.png` |
| `effect-generate` | fire/rain/snow/sparkle/explosion/smoke/water/energy loops | `effect-generate out/fx --kind fire --size 48 --frames 16 --animation out/fire.gif --sheet out/fire.png` |
| `motion-generate` | 9 motion presets from a still (idle-bob, hover, shake, jump, walk-bob, coin-spin, hit-flash, dissolve, pulse) | `motion-generate hero.png --kind idle-bob --frames 8 --animation out/idle.gif --outdir out/idle-frames` |
| `ui-generate` | panel/button/bar/dialog/cursor/slot/heart (flat/bevel/rounded, labels, segmented bars, grids) | `ui-generate button out/play.png --label PLAY --width 64 --style bevel` |
| `icon-generate` | 12 RPG icon kinds (+all), variants, palette-locked | `icon-generate out/icons --kind all --size 16 --variants 2 --sheet out/icons.png` |
| `pattern-generate` | 12 seamless pattern kinds (+tiled preview) | `pattern-generate out/brick.png --kind brick --size 32 --scale 4 --preview out/brick-tiled.png` |
| `tileset-generate` | 16-mask or 47-mask blob autotiles, 7 terrains, variants, ASCII-map preview | `tileset-generate out/ts.png --terrain grass --mode 47 --tile-size 16 --padding 0 --preview-map map.txt` |
| `iso-primitive` | tile/cube/slab isometric primitives | `iso-primitive cube out/cube.png --size 16` |
| `text-render` | Bitmap text (5x7/3x5), outline/shadow/align, 5 animated effects | `text-render "HELLO" out/hello.png --font 5x7 --color "#ffd24a" --outline "#000000"` · `text-render "GO" out/go.gif --effect wave --frames 8` |
| `font-sheet` | Full font atlas preview | `font-sheet out/font.png --font 5x7` |
| `voxel-build` | Box / heightmap / model-copy voxel models (JSON or .vox) | `voxel-build out/model.json --box 8x8x8 --color "#7a9b4e"` · `voxel-build out/terrain.json --heightmap map.png --max-height 6` |
| `voxel-render` | iso/front/side/top orthographic renders | `voxel-render out/model.json out/render.png --view iso --scale 6` |
| `voxel-turntable` | Rotating render frames + optional GIF | `voxel-turntable out/model.json out/turn --scale 4 --animation out/turn.gif` |
| `voxel-stack` | Z-layers as sprite-stack sheet + JSON | `voxel-stack out/model.json out/stack.png` |

Notes:
- All generators are seeded (`--seed`); identical inputs → identical bytes.
- Voxel model JSON: `{"size":[x,y,z], "palette":[[r,g,b],...], "voxels":[[x,y,z,colorIndex],...]}`;
  MagicaVoxel `.vox` is read/written by `voxel-build`/`voxel-render` via extension.
- `tileset-generate --preview-map` takes an **input** ASCII file (`#` = filled,
  override with `--fill-char`); the rendered preview path is reported in JSON.
- Tileset JSON records mask bits `N=1,E=4,S=16,W=64` (blob adds NE=2, SE=8,
  SW=32, NW=128). Use `--padding 0` for tilesets consumed by `tilemap-export`.
- Generated art is procedural placeholder-quality reference material, not
  hand-crafted final art; use it for blocking, tests, and iteration.

### 6.5 Delivery (`tools-delivery.py`)

| Command | What it does | Example |
|---|---|---|
| `engine-export` | godot/unity/web/phaser/pixi/rpgmaker/gamemaker/unreal/retro/vtt packages | `engine-export godot out/godot --input atlas.png --atlas-json atlas.json --name hero --fps 12` |
| `platform-export` | 11 presets: app-icon, favicon, emote, badge, sticker, wallpaper, social, store, thumbnail, led-matrix, watch | `platform-export icon.png out/app --preset app-icon` |
| `script-author` | Generate (never run) ffmpeg/ImageMagick/Aseprite-Lua/batch-job pipeline scripts | `script-author ffmpeg out/scripts --fps 12` |
| `tilemap-export` | Tiled TMX/TSX/JSON/CSV from a map file | `tilemap-export --map level.csv --tileset-image ts.png --tile-size 16 --outdir out/map --format all` |
| `caption-render` | Burn captions into animation + optional SRT | `caption-render in.gif out/capped.gif --cues cues.json --srt-out out/caps.srt` |
| `storyboard-generate` | Shot board PNG (+animatic GIF) from JSON or demo | `storyboard-generate --output out/board.png --input shots.json --animatic out/board.gif` |
| `contact-sheet` | Labeled review sheet + index JSON | `contact-sheet frames/ --output out/contact.png --labels --checker --title "Idle v2" --index out/index.json` |

Notes:
- Engine exports are real, inspectable files: Godot `.tres` AtlasTextures +
  SpriteFrames, Unity `.meta` (filterMode 0, point), self-contained pixelated
  HTML viewer, Phaser/Pixi loader snippets (`.js`/`.mjs`), RPG Maker/GM/Unreal
  import notes + data, retro palette-locked PNG + spec, VTT circular tokens.
- `--map` accepts CSV gids (`0,1,2` rows) or ASCII + `--legend "A=0,B=1"`;
  `-1`/unmapped = empty (TMX gid 0). Out-of-range gids exit 2.
- Cues JSON: `[{"start": 0.0, "end": 0.5, "text": "GO"}]` (seconds); `.srt` input
  also accepted. Storyboard JSON: `{"shots":[{"title","image","notes"}]}`.
- Platform presets live in `pixel-art-media/asset-presets.json`; retro systems
  (gameboy/nes/snes/genesis/c64/pico8) are available via `engine-export retro
  --system NAME` and `constraints --system NAME`.

### 6.6 Quality (`tools-quality.py`)

| Command | What it does | Example |
|---|---|---|
| `validate` | Constraint checks: size, POT, colors, palette compliance, alpha, naming regex, grid damage, frame counts, flash risk, atlas integrity | `validate sprite.png --width 16 --height 16 --max-colors 32 --binary-alpha --detect-resample --report out/v.json` |
| `critique` | Prioritized pixel-art findings + manual checklist (md + JSON) | `critique sprite.png --output out/crit.md --json-out out/crit.json` |
| `seam-check` | Seamless-tile check; optional crossfade fix + tiled preview | `seam-check tile.png --fix out/fixed.png --preview out/tiled.png --repeat 3` |
| `audit` | Folder inventory: naming, licenses, reopenability, secret scan | `audit assets/ out/audit.json --creator "Me" --license CC0 --markdown out/audit.md` |
| `license-record` | Per-asset provenance record with approval status | `license-record sprite.png out/lic.json --creator "Me" --license CC0 --status approved` |
| `reopen-test` | Reopen/decode files, report failures | `reopen-test a.png b.gif --report out/reopen.json` |
| `compare` | Pixel/animation diff with report + heat image; exit 7 on mismatch | `compare a.png b.png --diff-image out/diff.png --report out/diff.json` |
| `restore` | Detect + repair upscaled/resampled pixel grids | `restore damaged.png out/fixed.png --colors 16 --upscale 4` |

Notes:
- `validate` exits 7 when any check fails. Loop-closure similarity is
  **informational by default**; pass `--require-loop` to enforce it.
- `restore` grid detection: exact nearest-upscales are found by run-length GCD
  (score ≥ 0.95); bilinear/bicubic damage is found by second-derivative
  autocorrelation (`method: gradient-period`, confidence ≥ 0.45 on both axes).
  Heavy Lanczos ringing can defeat detection — pass `--block N` explicitly.
- `seam-check` metric: a tile is seamless when its wrap transition is no
  harsher than the harshest transition already inside the pattern
  (`wrap_diff <= max_internal_diff + tolerance`). Periodic patterns
  (checker/brick) pass legitimately; gradients fail. Exit 7 on failure.
- `audit` scans text files for secret-looking tokens (AWS keys, private keys,
  bearer tokens, etc.) and reports `clean: false` with locations if found.

### 6.7 Planning (`tools-planning.py`)

| Command | What it does | Example |
|---|---|---|
| `brief` | Production brief + IP-keyword risk flags (JSON + md twins) | `brief out/brief.md --idea "a tiny fox mage, 32x32" --platform web` |
| `art-bible` | Style/palette/animation rules document | `art-bible out/bible.md --title "My Game" --palette pal.json` |
| `constraints` | Platform/handheld constraint sheet | `constraints out/constraints.md --platform gameboy --system gameboy` |
| `scope` | Heuristic asset scope + hours estimate | `scope out/scope.md --items "characters:3,props:10,tiles:20"` |
| `rubric` | Weighted 0-5 review rubric | `rubric out/rubric.md --title "Sprint 4 review"` |
| `practice` | Seeded practice session with measurable goals | `practice out/practice.md --count 5 --seed 4` |
| `guide` | Per-category or per-skill workflow/checklist/pitfalls + mapped commands | `guide out/guide.md --category 12` · `guide out/g.md --skill "Walk Cycle Animator"` |
| `capabilities` | Live command map; `--catalog` full 318-skill map; `--category N` | `capabilities --catalog` |

Planning documents that emit twins accept either extension as the anchor:
`brief out/brief.md` writes `out/brief.md` + `out/brief.json` (and vice versa).
Estimates are heuristics for planning, not commitments. IP flags are keyword
heuristics — always review references manually.

### 6.8 Batch & selftest (entry)

```bash
python3 pixel-art-media/tools-pixel-media.py batch jobs.json --report out/report.json
python3 pixel-art-media/tools-pixel-media.py selftest --verbose
```

Jobs file schema (max 200 jobs):

```json
{"defaults": {"args": {"seed": 7}},
 "jobs": [
   {"id": "sprites", "op": "sprite-generate", "positional": ["out/sprites"],
    "args": {"kind": "creature", "count": 4, "size": 16}},
   {"argv": ["convert", "out/sprites/creature-0007.png", "out/big.png", "--factor", "4"]}
 ]}
```

Stop-on-error by default; `--continue-on-error` runs all jobs and reports each.
`--dry-run` validates the whole pipeline without writing. The report contains
per-job exit codes, stdout JSON, and written files.

## 7. Safety and approval rules

1. **Overwrites are gated.** Existing outputs (and in-place sidecar edits)
   require explicit `--force`; otherwise exit 5. `--dry-run` never writes.
2. **Bounded execution.** Dimensions, pixels, frames, colors, input counts,
   and wall-clock time are limited (section 5); violations exit 4 with hints.
3. **Atomic writes + cleanup.** Temp-file writes with rename; failures and
   Ctrl-C remove partial temp files; retries are bounded and never retry
   validation errors.
4. **No network, no secrets, no execution of generated content.** Generated
   ffmpeg/ImageMagick/Aseprite/Node scripts are written to disk for you to
   review and run; the tool only syntax-checks them (`bash -n`, `node --check`).
5. **Provenance.** `audit` + `license-record` track creator/license/source and
   approval status; `brief` flags protected-IP keywords for redesign.
6. **Accessibility.** Animation validation includes a WCAG-style flash-risk
   check; `display-sim` previews are integer-scaled.

## 8. Tests actually completed (this build)

All tests ran against the real CLI on Python 3.13 + Pillow 13.0.0:

- **`selftest`: 71/71 passed, 0 failures, 0 errors.** Covers: capability counts
  (73 commands / 29 categories / 318 skills); palette roundtrips in all 7
  writable formats (byte-exact order); deterministic generation (sprites,
  glitch, palettes); all 8 effects, 9 motions, 7 UI kinds, 12+ icons; tilesets
  16/47 + map export; voxel build/render/turntable/stack + .vox roundtrip;
  GIF/APNG/WebP encode/decode roundtrips (WebP lossless byte-equality) and
  duration preservation without `--fps`; tag lifecycle incl. exit-5 overwrite
  guard and exit-7 invalid ranges; TMX/JSON/CSV gid mapping + out-of-range
  exit 2; Godot/Unity/web exports; SRT captions; storyboard; generated-script
  syntax checks (`bash -n`, `node --check` for .js/.mjs); audit secret detection
  (planted AWS-style key found); restore on nearest and bilinear damage;
  overwrite/dry-run/limit/config guards; and a clean-root subprocess test that
  copies the folder beside a test SKILL.md and runs with relative paths only.
- **End-to-end pipeline: 21/21 steps passed** (sprite → palette → slice →
  motion → encode/decode → pack x2 → validate x2 → tags → Godot/web export →
  app-icon export → contact sheet → critique → guide → brief → audit of 67
  artifacts (clean, 0 secrets) → reopen-test 5/5).
- **Full-variant sweep: 267/267 invocations passed**, exercising every command
  and every enum variant (all resample/dither modes, all moods/schemes, all
  effects/motions/UI/icons/patterns/terrains, all 7 meta formats, all 10 engine
  targets + 6 retro systems, all 11 platform presets, all 29 category guides,
  batch, dry-run, overwrite guard, timeout).
- **Timeout enforcement verified**: a 1s budget on a heavy op exits 4 with no
  partial output file.
- **Reopen verification**: exported `.tres`/`.html` read back as UTF-8 text,
  GIFs decode, JSON manifests parse, ICO loads.

Re-run everything yourself: `python3 pixel-art-media/tools-pixel-media.py selftest`.

## 9. Known limitations and unavailable integrations

- **No AI generation.** All art is procedural/algorithmic. Skills marked
  *assist* produce measurements and artifacts; artistic judgment is yours.
- **Generated scripts are not executed.** ffmpeg/ImageMagick/Aseprite are not
  bundled or required; scripts are syntax-checked only (bash/node where
  available). Aseprite Lua is written but not executed or validated by a Lua
  interpreter here.
- **GIF**: 256 colors, binary alpha; identical consecutive frames are merged
  on save (durations summed) — expected Pillow/GIF behavior.
- **Grid restore**: nearest and bilinear/bicubic damage detected reliably;
  heavy Lanczos ringing may need manual `--block N`.
- **Windows**: SIGALRM timeout not enforced (limits still apply); everything
  else is cross-platform.
- **Pillow < 14 pin**: Pillow 14 removes `Image.getdata`; upgrade the code
  before lifting the pin.
- **No editor plugins / cloud / store publishing**: exports are files you
  import/publish yourself; no network integrations exist by design.
- Palette `.act`/`.ase` are RGB-only; `.hex` is lossless text; PNG swatches are
  indexed to preserve order.
- Voxel rendering is orthographic software rendering (no lighting/shadows).

## 10. Troubleshooting and recovery

| Symptom | Cause / fix |
|---|---|
| exit 5 `output-exists` | Approval gate. Re-run with `--force` or choose a new path. |
| exit 4 `limit-*` | Reduce size/frames/colors, or raise the specific limit via `--config`. |
| exit 4 `timeout` | Operation exceeded `timeout_seconds`; reduce work or raise the budget (POSIX). |
| exit 3 `*-not-found` | Check the path; inputs are relative to your working directory. |
| exit 7 from `validate`/`compare`/`seam-check` | A check failed — read `checks`/`failed` in the JSON; these are results, not crashes. |
| `bad-meta` from `atlas-meta` | Input must be a `json-simple` manifest from `sheet-pack --meta-format json-simple`. |
| `tileset-mismatch` in `tilemap-export` | Regenerate the tileset with `--padding 0`. |
| GIF frame count lower than input | Identical frames merged (see section 9); use APNG/WebP for exact counts. |
| `restore` returns block 1 | Damage below detection confidence; pass `--block N` if you know the factor. |
| Partial output after a crash | Should not happen (atomic writes); delete any `.pam-*` temp files if a kill -9 occurred. |
| Pillow deprecation warnings | Cosmetic on Pillow 13; the `<14` pin keeps behavior stable. |

Recovery: every command is deterministic and side-effect-free except its
declared outputs; re-running with the same inputs reproduces results. Batch
reports identify exactly which job failed; fix and re-run from that job.

## 11. Extending and maintaining

- **Add a command:** implement `cmd_x(a, ctx)` in the right module, register it
  in that module's `register(sub, common)` with `parents=[common]`, use
  `I()`/`G()`/`S()`/`P()` sibling accessors and `L.ToolError(code, message, hint)`
  for failures, write outputs only via `L.save_image/write_text/write_json`
  (atomic + gated). The entry point auto-discovers registered commands.
- **Add catalog mappings:** edit `pixel-art-media/asset-catalog.json` (skill →
  ops/mode) and `pixel-art-media/asset-guides.json` (workflow/checklist/
  pitfalls/commands). `capabilities` and `guide` read them live.
- **Add presets:** extend `pixel-art-media/asset-presets.json`; `platform-export`
  and `constraints` pick them up.
- **Add glyphs:** extend `pixel-art-media/asset-font.json` (5x7/3x5 bit rows).
- **Tests:** add `test_NN_*` methods to `pixel-art-media/tools-selftest.py`;
  they run through the real CLI via `run_cli`. Keep the suite green before
  delivery: `selftest` exits 7 on any failure.
- **Conventions:** files stay flat in the folder with `tools-`/`knowledge-`/
  `asset-` prefixes; internal references stay relative to the skill root;
  no absolute paths, no secrets, no network.

## 12. Provenance

Built from the attached design catalog PDF ("Pixel Art, Sprite, Voxel,
Animation, and Pixel Media Skill Catalog", 29 categories / 318 skills).
The bitmap fonts in `asset-font.json` are original CC0 glyph designs created
for this tool. All code is original; the only third-party runtime dependency
is Pillow (BSD-style license).
