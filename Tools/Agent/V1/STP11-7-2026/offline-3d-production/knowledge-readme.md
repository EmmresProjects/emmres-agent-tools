# offline-3d-production — Knowledge File

Reusable **Skill Tool component** for an offline / air-gapped 3D modeling and animation
production pipeline. It is designed to be copied beside a parent skill's `SKILL.md`
(this folder has none) and driven either by a person at a workstation or by an agent.

- Tool name: `offline-3d-production`
- Version: `1.0.0` (report schema `1.0.0`)
- Primary entry point: `tools-o3d.py`
- Language: Python 3 (standard library first), optional `numpy` for raster/texture work
- Network: **never used** — no download, no telemetry, no remote API, no license phone-home
- Source catalog: *Full Offline 3D Modeling and Animation — Skill Catalog* (32 categories, 446 proposed skills)

---

## 1. Purpose and scope

The source catalog is a planning document: 32 categories of capabilities that an offline 3D
agent would need, from workstation readiness through modeling, sculpting, retopology, UV,
texturing, baking, look-dev, rigging, animation, mocap, simulation, proceduralism, neural
generation, rendering, interchange, real-time optimisation, fabrication, QC, licensing,
archiving and pipeline operations.

This Skill Tool turns that catalog into one runnable component. Because the catalog is a single
coherent domain, it is delivered as **one unified Skill Tool folder** rather than 32 folders,
as required by the build rules. Every command below maps back to catalog categories, and the
mapping is in §9.

What the tool actually does, end to end, with no DCC and no network:

| Layer | Implemented capability |
| --- | --- |
| Standards | Project scaffold, naming standard + validator, unit/scale standard, axis/handedness standard, project configuration, department handoff template |
| Geometry | OBJ/PLY/STL/GLB/JSON import & export, topology measurement, manifold/watertight validation, repair (weld, winding, degenerate, holes, loose verts), decimation, voxel remesh, Catmull-Clark subdivision, smoothing, symmetry analysis & mirror repair, area-weighted surface sampling, UV distortion analysis |
| Textures | Full PNG encoder/decoder (zlib + struct), procedural map synthesis (checker, UV layout, noise, height, normal, AO, roughness, metallic, base colour, masks), coherent PBR sets, height→normal derivation, channel packing (ORM/RMA), texture audit, variant generation, mip-chain inspection |
| Render / review | Deterministic software rasteriser with auto-smooth corner normals, key/fill/rim lighting, fog, supersampling, wireframe; PNG/EXR(half-float)/PPM stills; turntable sequences; MP4 encode through a **local** ffmpeg; compositing (exposure/bloom/vignette/contrast/saturation/lift/gain); contact sheets with a built-in 5×7 bitmap font |
| Animation | Easing-preset curve evaluation (Hermite + cubic-bezier), resampling, statistics, RDP key reduction, loop construction with seam-drift measurement, retiming, animation gate (pops, late starts, early ends, loop seams) |
| Mocap | BVH parser with channel-order-aware forward kinematics, foot-contact/plant and slide detection, root-motion extraction, hip-normalised retarget planning, deterministic synthetic capture generator |
| Lip-sync | Text→viseme mapping and WAV-amplitude→viseme driving, with JSON/VTT/SRT cue output, fully offline |
| Planning | VRAM, RAM, scratch-disk, render-schedule and hardware audits with explicit assumptions |
| Governance | Offline install manifests with checksums, integrity verification, license audit against a local policy, performer consent audit, provenance records, SHA-256 hashing |
| Reporting | Structured JSON for every command, Markdown/HTML report rendering, deterministic labelled contact sheets, JSONL audit log |

### Honest limits (read this before promising anything)

- This is **not Blender**. It does not open `.blend`, rig, skin, sculpt with brushes, simulate
  cloth/fluids, or bake from high-poly to low-poly. Those remain DCC operations; this tool
  provides the surrounding contract, measurement, validation, packaging and review layer that
  a DCC-based pipeline needs, plus a dependency-free mesh/texture path for CI-style checks.
- The rasteriser is a **review/preview renderer** (Lambert + Phong-style interpolation,
  z-buffer, no ray-traced shadows, no GI, no PBR BRDF integration). It is deterministic — same
  input, same bytes — which is exactly what regression gates need, but it is not a final
  frame renderer. §5.6 states this in the command output too (`note` field).
- "Baking", "retopology", "sculpting", "rigging", "cloth", "fluids", "NeRF/Gaussian splat",
  "neural 3D generation" and "motion matching" exist in the catalog as *future skills*. In this
  Skill Tool their measurable, provable parts are implemented (validation, budgets, planning,
  provenance, audit, packaging); the parts that require a licensed DCC or a GPU model runtime
  are explicitly blocked and reported, never faked. See §11.
- Decimation is vertex clustering, not quadric edge collapse. Remeshing is a voxel/blocky
  surface + Laplacian, not adaptive surface nets. Both are documented as such in output.
- Only 8-bit non-interlaced PNGs are read; 16-bit and Adam7 inputs are rejected with a hint.
- BVH is the only capture format parsed. FBX/C3D/CSV capture needs conversion by the DCC first.

---

## 2. Setup and dependencies

### 2.1 Requirements

| Dependency | Required? | Version tested | Why |
| --- | --- | --- | --- |
| Python | yes | 3.13.16 (code targets 3.8+ syntax) | runtime |
| `numpy` | yes for `texture *`, `mesh render`, `review *`, `sheet`, `fixture` textures | 2.3.5 | raster and image maths |
| `ffmpeg` binary | no (optional) | 9.0.2 static build | only `review encode` (H.264 MP4) |
| Blender | no | — | not used; the geometry module deliberately avoids numpy so it can also run under Blender's bundled Python |

Standard library only: `argparse`, `json`, `struct`, `zlib`, `math`, `hashlib`, `subprocess`,
`wave`, `random`, `tempfile`, `unittest`, `html`, `re`, `fnmatch`, `importlib.util`.
There is **no** `requirements.txt`, `package.json` or lockfile to install: the runtime needs no
third-party packages and no package manager, which is what makes the folder safe to drop on an
air-gapped machine.

### 2.2 Install (connected staging machine, then air gap)

```sh
# 1. static ffmpeg, only if you want MP4 reviews
#    download once on a connected machine, copy the binary over, then either:
export O3D_FFMPEG=/opt/ffmpeg/ffmpeg        # sh/bash
#    or put it on PATH. Nothing is downloaded at run time.

# 2. numpy, only if the workstation Python lacks it
python3 -m pip install "numpy>=1.24"
#    with no network, stage wheels first:
#    python3 -m pip download numpy -d wheels/    (staging machine)
#    python3 -m pip install --no-index --find-links wheels numpy   (air-gapped machine)

# 3. copy this folder whole; do not rename files
ls offline-3d-production
```

Blender's bundled Python already contains numpy, so on a Blender-only workstation you can run:

```sh
blender --background --python tools-o3d.py -- capabilities
```

(Only the dependency-free commands — mesh, anim, mocap, plan, license, install, hash — are
guaranteed there; texture/render commands need numpy, which Blender ships.)

### 2.3 Exact commands

All commands below assume the shell is `sh`/`bash` and the current directory is this folder.

```sh
python3 tools-o3d.py help
python3 tools-o3d.py capabilities
python3 tools-o3d.py doctor
```

Use `python` instead of `python3` on Windows PowerShell; the script itself is portable.

### 2.4 Configuration

Default configuration file: `asset-default-config.json` (sits beside `tools-o3d.py` and is
auto-discovered). Override with `--config path/to/config.json`; merge one-off values with
`--overrides '{"limits":{"timeout_seconds":60}}'`.

Sections and the values that matter:

| Key | Meaning | Default |
| --- | --- | --- |
| `project.units` | scene unit | `m` |
| `project.up_axis` / `project.forward_axis` | coordinate contract | `Z` / `Y` |
| `project.fps` | frame rate | `24` |
| `project.color_management` | working space | `ACEScg` |
| `limits.timeout_seconds` | default per-command wall clock | `900` |
| `limits.max_input_mb` | input read ceiling | `128` |
| `limits.max_bvh_frames` | BVH frame ceiling | `200000` |
| `limits.max_output_triangles` | subdivision ceiling | `400000` |
| `limits.output_dir` | where default outputs land | `outputs` |
| `limits.audit_dir` | JSONL audit log directory | `outputs/audit` |
| `mesh.profile` | default geometry gate profile | see file |
| `texture.channel_order` | packing convention | `ORM` |
| `texture.normal_convention` | normal map convention | `OpenGL` |
| `render.*` | engine/device/samples/still format/bitrate | software raster, CPU |
| `animation.profile` | animation gate thresholds | see file |
| `qa.package_profile` | delivery gate profile | see file |
| `licensing.policy_file` | license policy path | `asset-license-policy.json` |

Validation happens **before** any work: missing sections, non-positive timeouts, unknown
up-axis values, or an up-axis equal to the forward axis all fail with exit code 2 and a hint.

---

## 3. Command surface (62 commands)

Global options accepted **before or after** the subcommand (the entry point normalises
argument order):

| Option | Effect |
| --- | --- |
| `--config PATH` | configuration file |
| `--workspace DIR` | base directory; all relative paths resolve inside it (default: current dir) |
| `--out PATH` | output file or directory |
| `--format json\|text` | structured JSON (default) or a compact text summary |
| `--timeout SECONDS` | wall-clock budget; exceeding it exits 4 |
| `--approve` | authorise a guarded action |
| `--dry-run` | validate, plan and report without writing outputs |
| `--allow-outside` | permit absolute paths outside the workspace |
| `--verbose` | include tracebacks on error |
| `--overrides JSON` | merge values over the configuration |

Exit codes: `0` success · `1` runtime error · `2` invalid input or failed gate · `3` approval
required · `4` limit exceeded / timeout / cancelled.

Print the truth from the tool itself: `python3 tools-o3d.py capabilities`.

```
capabilities
help
doctor
fixture
scaffold
standards naming | units | axis | config | handoff
validate names | geometry | animation | package
mesh inspect | validate | repair | convert | decimate | remesh | subdivide | smooth
     symmetry | sample | uv-analysis | render
texture generate | pbr-set | normal | pack | audit | variant | mips
anim sample | stats | reduce | loop | retime | gate
mocap inspect | contacts | rootmotion | retarget | synth
review turntable | encode
lipsync text | audio
plan vram | ram | disk | render | hardware
install manifest | verify
license audit | consent | provenance
hash
gate
report
sheet
```

---

## 4. Inputs, outputs and schemas

### 4.1 Path rules

- Every relative path resolves against `--workspace` (default: current directory).
- `..` traversal is refused. Absolute paths are accepted **only** when they resolve inside the
  workspace, unless `--allow-outside` is given. This is deliberate: a typo on an air-gapped
  workstation cannot reach `/etc` or `C:\Windows`.
- Missing inputs fail with `invalid_input` and the resolved absolute path in `details`.
- Outputs are written atomically (temp file + `os.replace`), so an interrupted run cannot leave
  a half-written JSON that later looks valid.

### 4.2 Accepted mesh inputs

`.obj` (with `vt` UVs), `.ply` (ascii and binary little-endian, x/y/z float), `.stl` (binary and
ascii), `.glb`/`.gltf` (glTF 2.0 triangles; embedded or same-folder `.bin`), and the tool's own
`.json` mesh (`{"verts": [[x,y,z]...], "faces": [[i,j,k]...], "uvs": [...], "face_uvs": [...]}`).
Ceilings: 800 000 vertices / 800 000 triangles per mesh (constants `MAX_VERTS`, `MAX_TRIS` in
`tools-o3d-geom.py`).

### 4.3 Mesh outputs

`.obj`, `.ply` (ascii), `.stl` (binary), `.glb` (single primitive, POSITION/NORMAL/indices,
glTF 2.0), `.json`.

### 4.4 Structured result shape

Every command writes one JSON object (see `asset-schema-report.schema.json`):

```json
{
  "tool": {"name": "offline-3d-production", "version": "1.0.0", "utc": "...", "network_used": false},
  "command": "mesh",
  "operation": "validate",
  "status": "ok",
  "gate": "pass",
  "fail_on": "error",
  "blocking": 0,
  "blocking_codes": [],
  "counts": {"critical": 0, "error": 0, "warning": 1, "info": 2},
  "worst": "warning",
  "findings": [{"severity": "warning", "code": "uv_stretch", "message": "...", "hint": "..."}],
  "output": "outputs/fixture-cube-geometry-gate.json"
}
```

Errors use the same envelope with `"status": "error"` and:

```json
{"error": {"code": "invalid_input", "message": "...", "hint": "...", "details": {}}}
```

Error codes: `invalid_input`, `approval_required`, `limit_exceeded`, `runtime_error`,
`missing_dependency`, `encode_failed`, `cancelled`, `rasterisation_empty`, `remesh_empty_surface`.

Findings use four severities (`info`, `warning`, `error`, `critical`) and stable snake_case
codes, so a gate can be automated on codes rather than message text.

### 4.5 Other output formats

- stills: `.png` (8-bit RGB), `.ppm` (P6/P3), `.exr` (OpenEXR scanline, HALF, uncompressed,
  channels B/G/R — readable by Nuke, Blender, OpenEXR tools)
- sequences: `frame-0001.png`, `frame-0002.png`, ... inside the `--out` directory
- video: `.mp4` (H.264, yuv420p) via local ffmpeg
- captions: `.vtt`, `.srt`
- reports: `.md`, `.html`
- audit trail: `outputs/audit/audit-log.jsonl` (timestamp, command, subcommand, status, out, approve)

---

## 5. Capability reference

### 5.1 Meta, environment, fixtures

```sh
python3 tools-o3d.py capabilities                       # 62 commands + catalog coverage
python3 tools-o3d.py help                               # usage, examples, exit codes
python3 tools-o3d.py doctor                             # Python/numpy/ffmpeg/blender probes, advisories
python3 tools-o3d.py fixture --dir fixtures --force     # deterministic OBJ/PLY/STL/GLB/BVH/PNG fixtures
```

`doctor` returns `"status": "degraded"` with an advisory when numpy or ffmpeg is missing, and
never claims a dependency exists when it does not.

`fixture` writes: `fixture-cube.obj`, `fixture-cube.json`, `fixture-cube.glb`,
`fixture-sphere.obj`, `fixture-sphere.stl`, `fixture-open.obj` (deliberately holed),
`fixture-capture.bvh`, six `fixture-pbr-*.png` maps and `fixture-uv-layout.png`. These are the
inputs used by the self-tests and by the smoke procedure in §8.

### 5.2 Project, standards and naming

```sh
python3 tools-o3d.py scaffold --project hero-film --dir projects/hero --fps 25 --units m --up-axis Z
python3 tools-o3d.py standards naming --out outputs/naming-standard.json
python3 tools-o3d.py standards units --unit cm --character-height 1.8 --for-print
python3 tools-o3d.py standards axis --up Z --forward Y --handedness right --rotation-order XYZ
python3 tools-o3d.py standards config --project hero --fps 24 --resolution 1920x1080 --handles 8
python3 tools-o3d.py standards handoff --project hero --department rigging --out outputs/handoff.json
python3 tools-o3d.py validate names --name hero_model_body_v001 --name "hero model body" --out outputs/names.json
python3 tools-o3d.py validate names --names-file names.txt
```

`scaffold` creates the 17 standard folders (`00-admin` … `15-delivery`, `99-archive`) plus
`00-admin/project-config.json` with units, up axis, fps, resolution, colour management, cache
policy, naming block and delivery targets.

The naming standard is a single parseable pattern:

```
<asset>_<kind>_<desc>_v<NNN>          e.g.  hero_model_body_v001
```

`validate names` flags: `pattern_mismatch`, `illegal_character`, `reserved_default_name`
(`Default`, `Cube`, `Material`, …), `duplicate_name` (case-insensitive), `name_too_long`,
`double_separator`, `trailing_separator`, `unknown_kind`, `asset_code_mismatch`. It also returns
a `rename_map` with a safe replacement for anything that misses the pattern. Unit and axis
standards include the gravity conversion, clip recommendation and the conversions between Z-up
and Y-up.

### 5.3 Mesh modeling, topology and analysis

```sh
python3 tools-o3d.py mesh inspect --mesh fixtures/fixture-cube.obj --uv
python3 tools-o3d.py mesh validate --mesh fixtures/fixture-sphere.obj --profile profile.json
python3 tools-o3d.py mesh repair --mesh fixtures/fixture-open.obj --fill --out outputs/fixed.obj
python3 tools-o3d.py mesh convert --mesh fixtures/fixture-cube.obj --to glb --from-unit m --to-unit cm --up-from Z --up-to Y
python3 tools-o3d.py mesh decimate --mesh fixtures/fixture-sphere.obj --target-tris 300 --out outputs/lod.obj
python3 tools-o3d.py mesh remesh --mesh fixtures/fixture-sphere.obj --voxel-size 0.05 --out outputs/remesh.obj
python3 tools-o3d.py mesh subdivide --mesh fixtures/fixture-cube.obj --levels 2 --out outputs/subd.obj
python3 tools-o3d.py mesh smooth --mesh fixtures/fixture-sphere.obj --iterations 2 --factor 0.5
python3 tools-o3d.py mesh symmetry --mesh fixtures/fixture-cube.obj --axis X
python3 tools-o3d.py mesh symmetry --mesh m.obj --repair --approve --out outputs/symmetric.obj
python3 tools-o3d.py mesh sample --mesh fixtures/fixture-sphere.obj --count 1000 --seed 7 --out outputs/samples.json
python3 tools-o3d.py mesh uv-analysis --mesh textured.obj --resolution 2048
```

`mesh inspect` reports vertices/faces/triangles/quads/ngons, bounds, dimensions, centre, surface
area, signed volume, edge count, boundary edges, non-manifold edges, boundary loops, connected
components and their sizes, degenerate faces, valence histogram, triangle quality (worst aspect
ratio, minimum angle, long-thin count), symmetry match ratio, and `watertight`. With `--uv` it
adds the UV report.

`mesh validate` is the geometry gate. Findings:

| Code | Severity | Meaning / fix |
| --- | --- | --- |
| `empty_mesh` | critical | no faces — re-export |
| `nonmanifold_edges` | error | edge shared by >2 faces — `mesh repair` |
| `open_boundary` | error | boundary edges with `require_closed` — `mesh repair --fill` |
| `scale_defect` | error | height outside ±50 % of `expected_height_m` — fix scale, re-apply transforms |
| `symmetry_defect` | error | <98 % mirrored with `require_symmetry` — `mesh symmetry --repair` |
| `triangle_budget_exceeded` | error | over `max_triangles` — `mesh decimate` |
| `flipped_uv` | error | mirrored UV triangles — re-unwrap/flip island |
| `uv_missing` | error | `check_uv` set but no UVs |
| `sliver_triangles` | warning | min angle below `min_angle_degrees` |
| `degenerate_faces` / `loose_vertices` | warning | cleanup via `mesh repair` |
| `self_intersections` | warning | intersecting triangle pairs (bounded screen) |
| `inverted_winding` | warning | negative signed volume on a closed mesh |
| `uv_stretch` / `low_texel_density` | warning | distortion thresholds from the profile |

`mesh repair` performs, in order: weld (grid hash within `--weld-distance`), delete degenerate
faces, unify winding per component, optional hole fill (fan-fill loops up to
`--max-hole-edges`, default 64), remove loose vertices. It reports the counts of each stage and
before/after topology.

`mesh convert` applies unit scaling and up-axis rotation (`--from-unit`+`--to-unit`,
`--up-from`+`--up-to`, `--scale`) and records each transform in the result.

`mesh symmetry --repair` deletes the `-`axis half, mirrors the `+` half, welds the seam, unifies
winding, and reports before/after symmetry. It requires `--approve` because it replaces geometry.

`mesh sample` does area-weighted barycentric sampling with a seeded RNG (deterministic across
runs and machines) — suitable for scattering plans, bake cage validation and point-cloud export.

### 5.4 Textures

```sh
python3 tools-o3d.py texture generate --kind uv-layout --tiles 2 --width 512 --height 512 --out outputs/uv.png
python3 tools-o3d.py texture pbr-set --family stone --width 1024 --height 1024 --out outputs/stone
python3 tools-o3d.py texture normal --height-map outputs/stone/pbr-stone-height.png --strength 2.5
python3 tools-o3d.py texture pack --source ao=a.png --source rough=r.png --source metal=m.png \
        --layout R=ao:r,G=rough:r,B=metal:r --out outputs/orm.png
python3 tools-o3d.py texture audit --image outputs/stone/pbr-stone-basecolor.png --role basecolor --expected-size 1024x1024
python3 tools-o3d.py texture variant --image base.png --mode worn --amount 0.6 --seed 3
python3 tools-o3d.py texture mips --image base.png --levels 4 --out outputs/mips
```

Generate kinds: `checker`, `uv-layout` (UDIM tile reference with U/D/V/R orientation markers),
`gradient`, `noise` (seeded fBm value noise), `height`, `normal`, `ao`, `roughness`, `metallic`,
`basecolor`, `mask`.

`pbr-set` families: `stone`, `metal`, `fabric`, `skin`, `wood`; it emits basecolour, roughness,
metallic, normal (OpenGL +Y), AO approximation and height, all coherent from one seed.

`texture audit` findings: `non_power_of_two`, `size_mismatch`, `flat_channel`,
`low_bit_depth_content`, `banding`, `normal_invalid_z`, `normal_green_convention`,
`baked_specular`, `missing_alpha`, `seam_padding`, `embedded_color_profile`. Channel statistics
(min/max/mean/std/unique values) are always included so a reviewer can judge without opening the
file.

`texture variant` modes: `worn`, `wet`, `dirty`, `colorway`, `damaged`, `clean` — each derived
from one source with a fixed seed so variants are reproducible.

`texture mips` writes downsampled levels and reports per-level mean/std (bleeding and energy-loss
evidence for real-time handoffs).

### 5.5 Animation and motion

```sh
python3 tools-o3d.py anim sample --keys curves.json --curve root_z --start 1 --end 48 --step 6 --easing ease-in-out
python3 tools-o3d.py anim stats --keys curves.json --curve root_z
python3 tools-o3d.py anim reduce --keys curves.json --curve root_z --tolerance 0.01
python3 tools-o3d.py anim loop --keys curves.json --curve root_z --loop-frames 24 --loops 3
python3 tools-o3d.py anim retime --keys curves.json --curve root_z --factor 2
python3 tools-o3d.py anim gate --curves curves.json                 # fails if a curve starts after frame 1
python3 tools-o3d.py anim gate --curves curves.json --overrides '{"animation":{"profile":{"frame_start":1,"frame_end":24}}}'
python3 tools-o3d.py anim gate --curves curves.json --loop

python3 tools-o3d.py mocap inspect --bvh fixtures/fixture-capture.bvh
python3 tools-o3d.py mocap contacts --bvh fixtures/fixture-capture.bvh --threshold 0.02
python3 tools-o3d.py mocap rootmotion --bvh fixtures/fixture-capture.bvh
python3 tools-o3d.py mocap retarget --source Hips:95,Spine:12 --target Hips:104,Spine:14
python3 tools-o3d.py mocap synth --frames 48 --fps 24 --seed 7 --out outputs/capture.bvh
```

`keys` input is either `[[frame, value], ...]` or an object of named curves (then pass `--curve`).
Easing presets: `linear`, `smooth`, `ease-in`, `ease-out`, `ease-in-out`, `anticipate`,
`overshoot`, `step`. `anim reduce` is Ramer–Douglas–Peucker with a time-weighted error budget and
reports before/after key counts and reduction percent.

`anim gate` compares each curve against the shot range in `animation.profile`
(`frame_start` default 1, `frame_end` default 120) and fails on `warning` by default, so a pop
cannot slip through silently. Findings: `empty_curve`, `jitter_or_pop` (per-frame delta above
`max_jitter_per_frame`, default 0.25), `late_start` (curve starts after `frame_start`),
`early_end` (curve ends before `frame_end`), `loop_seam` (endpoints differ by more than
`loop_tolerance`). Pass your own shot range with `--overrides` or a custom `--config`; a
two-key 24-frame curve will legitimately report `early_end` against the default 120-frame shot.

`mocap inspect` parses `HIERARCHY`/`MOTION`, honours per-joint channel order, and reports joint
count/names, roots, frames, fps, duration, channel count and root translation range.
`mocap contacts` runs forward kinematics per frame and reports plant runs, plant ratio and
sliding runs (a plant whose height drifts). `mocap rootmotion` returns total translation, planar
distance and average speed. `mocap retarget` returns the hip-normalised scale and per-joint length
ratios; it refuses mismatched chain lengths.

### 5.6 Rendering, review and finishing

```sh
python3 tools-o3d.py mesh render --mesh hero.obj --pose hero --width 960 --height 640 --samples 2 \
        --still-format png --label hero --out outputs/hero.png
python3 tools-o3d.py mesh render --mesh hero.obj --still-format exr --out outputs/hero.exr
python3 tools-o3d.py mesh render --mesh hero.obj --shading flat --wireframe --out outputs/topo.png
python3 tools-o3d.py review turntable --mesh hero.obj --frames 24 --width 480 --height 480 --label hero
python3 tools-o3d.py review encode --frames-dir outputs/turntable --fps 24 --bitrate 4M
python3 tools-o3d.py sheet --image "hero=outputs/a.png" --image "top=outputs/b.png" --columns 2 --cell 160
```

Poses: `hero`, `front`, `side`, `three-quarter`, `top`, `closeup` (see `asset-render-presets.json`
for the review presets and what each is for). Options: `--samples` supersampling, `--bg r,g,b`,
`--exposure`, `--fog`, `--wireframe`, `--shading smooth|flat`, `--label`.

Normals use an auto-smooth rule (40° threshold, per-face-corner normals), so curved surfaces are
smooth and hard-surface edges stay crisp. Shading is two-sided: inverted winding shows up as
wrong scale/volume in the gate, not as a black render.

`review encode` shells out to **ffmpeg only**, with an argument list (never a shell string), a
hard timeout, and a two-attempt strategy that copes with both old and new ffmpeg releases
(`-fps_mode vfr`, then no flag). If ffmpeg is absent it returns
`"status": "dependency_unavailable"` with `offline_install_hint` instead of failing the run.

`sheet` composes a labelled contact sheet (built-in 5×7 bitmap font, so no font file is needed).

### 5.7 Planning

```sh
python3 tools-o3d.py plan vram --triangles 500000 --texture 4096x4096x3=albedo --samples 128 --device-gb 8
python3 tools-o3d.py plan ram --triangles 200000 --sculpt-millions 12 --cache-gb 8 --system-gb 64
python3 tools-o3d.py plan disk --frames 480 --frame-mb 24 --cache-versions 3 --available-gb 500
python3 tools-o3d.py plan render --frames 4800 --seconds-per-frame 45 --machines 8 --target-hours 12
python3 tools-o3d.py plan hardware --requirements req.json --reported reported.json
```

Every planning command returns a `components_*` breakdown, `peak_gib` (or MB/GB), `headroom`,
`fits`, a `recommendation`, and — importantly — an `assumptions` block saying the coefficients are
planning heuristics, not vendor measurements. Validate against a measured workload before locking
a budget. `plan hardware` compares numeric requirements (fails below) and string requirements
(warns on mismatch) and reports `unknown` for anything not supplied.

### 5.8 Licensing, consent, provenance, integrity

```sh
python3 tools-o3d.py license audit --assets assets.json --policy asset-license-policy.json
python3 tools-o3d.py license consent --records consent.json --policy asset-consent-policy.json
python3 tools-o3d.py license provenance --file outputs/hero.png --model local-model --model-version 2.1 \
        --seed 42 --license-id cc-by-4.0 --operator artist
python3 tools-o3d.py install manifest --entries entries.json --bundle-dir bundle
python3 tools-o3d.py install verify --manifest manifest.json --base-dir .
python3 tools-o3d.py hash --file a.obj --file b.png --out outputs/checksums.json
```

`license audit` findings: `unknown_license` (critical), `no_commercial_use` (critical),
`redistribution_forbidden`, `ai_training_forbidden` (critical), `missing_attribution`,
`missing_source`, `missing_checksum`, `missing_model_receipt`. The policy file is data — replace
its rule table with what legal actually signed off; the bundled table is a documented default.

`license consent` findings: `missing_consent_field`, `training_not_permitted`, `no_territory`,
`consent_expired`, `missing_retention`.

`license provenance` records model, version, seed, inputs, license id, operator, output SHA-256
and environment, plus a `completeness` block listing what is still missing — it never pretends an
incomplete record is complete.

`install manifest` checksums every file it can find in the bundle and flags
`checksum_mismatch` (critical), `installer_missing`, `unverified_installer`,
`installer_no_license`. `install verify` re-hashes files and reports `missing_file` /
`checksum_mismatch`. Nothing is ever installed or executed by this tool.

### 5.9 Reporting

```sh
python3 tools-o3d.py report --input outputs/fixture-cube-geometry-gate.json --title "Geometry Gate"
python3 tools-o3d.py report --input outputs/vram-plan.json --html
python3 tools-o3d.py gate        # every gate, blocking codes and how to clear them
```

`report` turns any JSON result into Markdown or HTML with summary, findings table (severity
coloured in HTML), metrics, recommendation, breakdown and a reproduction note.

---

## 6. Safety and approval rules

1. **No network, ever.** No HTTP client is imported anywhere in the package. `doctor` reports
   `"network_required": false`; every result carries `"network_used": false`.
2. **No arbitrary execution.** The only subprocess is ffmpeg, invoked with an argument list
   (`shell=False`), a fixed flag set, a timeout, and a path the operator supplied or that is on
   PATH. No `eval`, no `exec`, no dynamic imports from user input.
3. **Approval gate.** `--approve` is required to overwrite geometry in place
   (`mesh symmetry --repair`) and is the pattern for any future destructive command; the refusal
   returns exit code 3, `approval_required`, with the exact re-run command in `hint`.
4. **Dry run first.** Every command supports `--dry-run`, which validates inputs, computes the
   result and writes nothing.
5. **Bounded work.** Hard ceilings exist for mesh vertices/triangles, BVH frames, remesh grid
   voxels, subdivision triangles, render pixels, texture pixels, input file size and wall clock.
   Exceeding them exits 4 with the number and the knob to change.
6. **Path containment.** Relative-only resolution, `..` refused, absolute paths limited to the
   workspace unless explicitly opted in.
7. **Non-destructive outputs.** Originals are never modified; new files are written beside them
   or under `--out`. Nothing is deleted by any command.
8. **Provenance and licenses are recorded, not inferred.** Missing data is reported as missing.
9. **Audit trail.** When `limits.audit_dir` resolves, each command appends one JSONL record
   (timestamp, command, subcommand, status, out, approve flag).
10. **Human review before release.** The gates produce evidence; a person still signs the
    approval record. The tool never publishes or uploads anything.

---

## 7. Limits, timeouts, retries, cancellation, recovery

| Mechanism | Behaviour |
| --- | --- |
| Timeout | `--timeout` or `limits.timeout_seconds`; checked at stage boundaries (load, per turntable frame, verify); exceeding exits 4 with the stage name |
| Cancellation | Ctrl+C exits 4 (`cancelled`) instead of a traceback; already-written files stay valid because writes are atomic |
| Retries | I/O helper supports bounded retries (`core.retry_call`, default 2 attempts, 0.05 s backoff) used only for transient filesystem errors; logic errors are never retried |
| Recovery | Every failure names the command to re-run; §10 and `asset-recovery-runbook.md` cover the production failure modes |
| Ceilings | `MAX_VERTS`/`MAX_TRIS` 800 k, BVH 200 k frames, remesh grid 12 M voxels, subdivision 400 k triangles, render 64 M subsamples, texture 16.7 M pixels, input 128 MB |
| Determinism | seeded RNG everywhere (`--seed`), sorted iteration for hashes and reports, no timestamps inside pixel data |

---

## 8. Validation and tests actually completed

`tools-o3d-tests.py` drives the real CLI through subprocess (not internal helpers) and writes
only into a temporary directory that it removes afterwards.

```sh
python3 tools-o3d-tests.py            # full suite (102 tests)
python3 tools-o3d-tests.py -v         # verbose
python3 tools-o3d-tests.py --only mesh
```

**Result on the 2026-11-07 build: 102 tests, 102 passed, 0 failures, 0 errors, 0 skipped**
(`python3 tools-o3d-tests.py` on CPython 3.13.16 / Linux x86_64, numpy 2.3.5, ffmpeg 9.0.2).
The suite was re-run after the final code change and after the extracted-ZIP re-test.

What the suite proves, by group:

| Group | Tests | Covers |
| --- | --- | --- |
| Meta | 7 | capabilities (62 commands, 32 categories, 446 skills), help, doctor, gate catalogue, text format, no-args and unknown-command exit codes |
| Standards | 4 | naming/units/axis/config/handoff documents, bad-axis rejection, print units |
| Names | 4 | valid names pass, invalid/reserved/duplicate/illegal flagged, `--names-file`, missing input |
| Mesh | 31 | inspect cube/open/sphere+UV, gate pass and fail on open mesh, scale defect, repair fills holes (verified by re-inspection), OBJ→GLB→inspect round trip, unit+axis conversion (100 cm), STL export, decimation to budget, subdivision, voxel remesh, symmetry report, approval gate (exit 3) and approved mirror repair, deterministic sampling and seed sensitivity, UV analysis (flip/texel/orientation), UV-missing rejection, missing/corrupt mesh, `..` refusal, absolute path inside allowed / outside refused, `--allow-outside`, PNG+EXR stills, byte-identical renders, settings change output, tiny render rejected |
| Texture | 10 | seven generate kinds, size rejection, PBR set + audit, height→normal + normal audit, ORM packing, mismatched-size rejection, six variant modes producing distinct bytes, mip chain, non-power-of-two reporting, missing file |
| Anim | 8 | resample/stats, easing changes output, RDP reduction, loop seam drift, retime, gate pass, gate detects pop, malformed input |
| Mocap | 6 | inspect (48 frames @ 24 fps), contacts, root motion, retarget scale, chain-length mismatch, synth→parse round trip, corrupt BVH |
| Lip-sync | 4 | text→VTT, VTT+SRT together, empty text rejected, generated WAV→visemes |
| Plan | 6 | VRAM (and monotonic growth with triangles), RAM/disk/render, hardware fail, hardware pass |
| License/integrity | 9 | NC license blocks, unknown license critical, clean audit passes, consent training block, provenance complete + missing-field reporting, manifest missing installer, hash + verify pass, tampered digest detected |
| Project/reports | 13 | scaffold folder + config, package gate pass and fail, Markdown and HTML reports, contact sheet, turntable sequence, MP4 encode (encoded or graceful `dependency_unavailable`), missing frames, render brightness floor, sheet dimensions, dry-run writes nothing, timeout exits 4, bad overrides JSON, broken config rejected |

Beyond the suite, these were done by hand on the same build:

- `python3 tools-o3d.py capabilities` / `help` / `doctor` — run and inspected.
- `texture pbr-set` maps opened as images (base colour and normal visually inspected).
- `mesh render` sphere and cube stills opened and inspected (correct 3D shading, smooth sphere,
  crisp cube edges, visible label burn-in).
- `sheet` output opened and inspected (labels `HERO`, `NORMAL`, `AO`, `UV` legible).
- `review turntable` → 12 frames at 240×240 → `review encode` with a local ffmpeg 9.0.2 static
  build → produced a 29 005-byte `review.mp4` with an `ftypisom` header box.
- EXR output parsed back with the tool's own header reader (channels B, G, R; correct dimensions).
- GLB output verified by magic header and by re-importing and inspecting triangle counts.
- All 62 commands reachable; `capabilities` and the documentation were generated from the same
  registry and cross-checked.

Not performed (and not claimed): rendering in Blender/Cycles, GPU denoising, cloth/fluid
simulation, rig/skin deform tests, neural inference, and any network-dependent check. Those
require a DCC or a model runtime that is not part of this component — see §11.

### 8.1 Clean-root compatibility test

The folder is verified to sit beside a parent `SKILL.md`:

```sh
mkdir -p /tmp/clean-skill-root
cd /tmp/clean-skill-root
printf '# test skill\n' > SKILL.md          # parent package root marker only
cp -r offline-3d-production .
cd offline-3d-production
python3 tools-o3d.py capabilities >/dev/null && echo "relative-path run OK"
python3 tools-o3d.py fixture --dir fixtures --force >/dev/null
python3 tools-o3d.py mesh inspect --mesh fixtures/fixture-cube.obj --format text | head -5
```

All references inside the package are relative to the future skill root, e.g.
`offline-3d-production/asset-default-config.json`. There are no absolute paths, no
drive-letter paths, no `..` traversal and no generated URLs in any packaged file.

---

## 9. Catalog coverage map

Every one of the 32 catalog categories is served. `C` = implemented command(s);
`V` = validation/measurement of the capability; `P` = planning/budget/governance for a
capability that needs a DCC, GPU model or licensed tool.

| # | Catalog category | Covered by |
| --- | --- | --- |
| 1 | Offline installation, runtime, workstation readiness | C `doctor`, `install manifest`, `install verify`, `hash`; P `plan hardware` |
| 2 | Project architecture, standards, scene templates | C `scaffold`, `standards naming/units/axis/config/handoff`, `validate names` |
| 3 | Mesh modeling and topology construction | C `mesh inspect/validate/repair/convert/decimate/subdivide/smooth/symmetry/remesh` |
| 4 | Sculpting and high-resolution detail | V `mesh inspect` (density, valence, non-manifold, self-intersection), `mesh remesh`, `mesh smooth`; P `plan ram` |
| 5 | Retopology and production mesh conversion | C `mesh decimate/remesh/convert/validate` (budget, poles, slivers, boundary, correspondence via re-export); P `plan vram` |
| 6 | UV mapping, UDIMs, surface coordinates | C `mesh uv-analysis`, `texture generate --kind uv-layout` (UDIM/tile orientation reference) |
| 7 | PBR texturing and texture painting | C `texture pbr-set/generate/pack/variant/audit/mips` |
| 8 | Baking, projection, map generation | C `texture normal`, `texture pack`, `mesh sample` (cage/scatter points); V `texture audit` (dilation, channels); P bake-budget in `plan ram` |
| 9 | Materials, shaders, procedural surface libraries | C `texture pbr-set` families, `texture audit` role checks |
| 10 | Lighting, HDRI, exposure, look development | C `mesh render` (key/fill/rim, fog, exposure), `asset-render-presets.json`, compositing helpers |
| 11 | Rigging, controls, deformation systems | V `mesh inspect`/`validate` (deformation-ready topology, symmetry, scale), `anim gate`; P rig budgets |
| 12 | Skin weights, deformation, shape keys | V geometry/animation gates plus weight-transfer planning via `mesh sample`; P — weight solving needs a DCC |
| 13 | Facial rigging, eyes, lip sync, performance | C `lipsync text`, `lipsync audio` (VTT/SRT cues, viseme library in `asset-anim-presets.json`) |
| 14 | Motion capture, retargeting, motion editing | C `mocap inspect/contacts/rootmotion/retarget/synth`, `anim reduce/retime/loop/gate` |
| 15 | Keyframe animation, graph editing, NLA | C `anim sample/stats/reduce/loop/retime/gate` |
| 16 | Camera tracking, object tracking, matchmove | P `plan hardware`, `plan vram`; V `mesh render` review stills for camera/scale checks — solving needs a tracker |
| 17 | Physics, rigid bodies, constraints | P `plan ram`/`plan disk` (cache budgets), determinism guidance; V contact detection in `mocap contacts` |
| 18 | Cloth, garments, soft goods | C `mesh subdivide`/`smooth` prep, `plan ram`/`plan disk` for caches; simulation needs a DCC |
| 19 | Hair, fur, grooming, feathers | V `mesh inspect` (card topology, density), `texture audit` (groom masks); P budgets |
| 20 | Fluids, smoke, fire, volumes, particles | P `plan ram`, `plan disk` (volume caches); volumes themselves need a DCC |
| 21 | Geometry nodes, procedural modeling, scattering | C `mesh sample` (seeded scatter points), `mesh remesh`/`decimate`; P node-tree budgets |
| 22 | Offline neural 3D generation and reconstruction | C `license provenance` (model, version, seed, inputs, hash), `texture normal`, `license audit`; inference itself needs a local model runtime (blocked, see §11) |
| 23 | Rendering engines, GPU support, denoising | C `mesh render`, `review turntable`, `review encode`; P `plan vram`, `plan hardware` |
| 24 | Render farm, batch rendering, job orchestration | C `review turntable` sequences, `review encode`; P `plan render` (chunking, retries factor, target hours) |
| 25 | Color management, compositing, finishing | C compositing pipeline (exposure/bloom/vignette/contrast/saturation/lift/gain), EXR output, `sheet`, `report` |
| 26 | glTF, GLB, FBX, USD, interchange | C `mesh convert` (OBJ/PLY/STL/GLB/JSON, units, axis), `standards axis` conversions; FBX/USD need a DCC |
| 27 | Real-time, game, XR, runtime optimization | C `mesh decimate`, `texture audit` (POT, mips), `texture mips`, `validate package` extensions; P `plan vram` |
| 28 | Fabrication, 3D printing, CAD, physical output | C `mesh convert --to stl` (binary STL), `mesh validate` (watertight, non-manifold), `standards units --for-print` |
| 29 | Model, rig, texture, scene quality control | C `mesh validate`, `anim gate`, `texture audit`, `validate names/package`, `gate` |
| 30 | Licensing, provenance, security, responsible use | C `license audit/consent/provenance`, `asset-license-policy.json`, `asset-consent-policy.json`, `asset-licence-register.template.csv` |
| 31 | Reproducibility, packaging, backup, archival | C `hash`, `install verify`, `validate package`, `report`, audit log; procedure in `asset-recovery-runbook.md` |
| 32 | Documentation, training, automation, pipeline operations | C `report` (Markdown/HTML), `standards handoff`, `sheet`, `asset-handoff-handbook.md`, self-tests, JSONL audit |

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Action |
| --- | --- | --- |
| `missing_dependency` on texture/render | numpy absent | `python3 -m pip install "numpy>=1.24"`, or run those commands only where Blender's Python (which has numpy) is available |
| `dependency_unavailable` on `review encode` | ffmpeg not found | copy a static ffmpeg, put it on PATH or set `O3D_FFMPEG`; deliver PNG/EXR sequences meanwhile |
| `invalid_input: Absolute mesh is not allowed` | absolute path outside the workspace | use a relative path, or `--allow-outside` when you really mean it |
| `invalid_input: may not contain '..'` | traversal | resolve the path inside `--workspace` |
| `approval_required` (exit 3) | guarded action | run `--dry-run` first, then re-run with `--approve` |
| `limit_exceeded` (exit 4) | ceiling or timeout | reduce workload (frames/samples/triangles/voxel grid), raise `--timeout`, or raise the constant deliberately |
| Gate fails on `open_boundary` | holes in the mesh | `mesh repair --fill`; if loops exceed `--max-hole-edges`, fill in the DCC |
| Gate fails on `scale_defect` | wrong scene scale | scale the asset, apply transforms, re-export, re-run |
| `encode_failed` | ffmpeg flag/version or unreadable frame | check frames, set `O3D_FFMPEG` to a known-good build; the tool already tries both `-fps_mode` and legacy flag sets |
| `rasterisation_empty` / `remesh_empty_surface` | voxel size too large or open surface | lower `--voxel-size` (≈1/60 of model height), or `mesh repair --fill` first |
| Texture audit reports `non_power_of_two` | odd dimensions | export POT sizes for mipmapped real-time targets |
| Audit reports `normal_green_convention` | DirectX/OpenGL mix-up | invert green for DirectX targets, or re-bake with the correct tangent basis |
| Config rejected | missing section, bad fps/axis | start from `asset-default-config.json` and edit values only |
| `import` failure at startup | a `tools-o3d-*.py` file renamed/missing | copy the folder whole; do not rename anything |

Deeper procedures — DCC won't launch, render dies from memory, disk fills, vendor mesh arrives
broken, animation pops after a rig change, mocap scaled wrong, delivery rejected, provenance
challenge, truncated files, rollback — are in `asset-recovery-runbook.md`.

### Extending the tool

1. **New command:** add a row to `CAPABILITIES`, a subparser in `build_parser()`, a handler
   `cmd_*`, and register it in `HANDLERS`. Keep the return value a JSON-safe dict with
   `"status"`.
2. **New geometry:** add the function to `tools-o3d-geom.py` (pure stdlib) and call it from
   `cmd_mesh`. Keep iteration sorted and RNG seeded.
3. **New map type:** add a branch to `generate_texture()` in `tools-o3d-texture.py` and list the
   kind in the `--kind` choices.
4. **New gate:** add findings with stable codes, return `pipeline.gate(findings, fail_on)`, and
   document the code in §5 and in `cmd_gate`.
5. **New third-party dependency:** update §2 and `doctor`, and keep the command failing with
   `missing_dependency` and an install hint when it is absent.

### Maintenance checklist per release

```sh
python3 tools-o3d.py capabilities > /dev/null     # registry intact
python3 tools-o3d.py doctor                       # dependencies honest
python3 tools-o3d-tests.py                        # 102/102 expected
python3 tools-o3d.py fixture --dir /tmp/o3d-smoke --force
python3 tools-o3d.py mesh validate --mesh /tmp/o3d-smoke/fixture-cube.obj
```
Bump `VERSION` in `tools-o3d-core.py` (and `TOOL_NAME`/`VERSION` in `tools-o3d.py`) together and
record the change in this file.

---

## 11. Explicitly unavailable integrations

These are catalog capabilities that cannot be implemented inside a portable, dependency-free
component. They are **blocked, configurable and reported**, not simulated:

| Capability | Why unavailable here | What the tool does instead |
| --- | --- | --- |
| Blender / DCC scripting, `.blend` I/O | no DCC installed; no licensed runtime | documented contract, validation, measurement, packaging; geometry modules run under Blender's Python if you wire them in |
| Sculpt brush simulation, multires displacement | needs a DCC sculpt engine | density/valence/manifold/self-intersection validation, voxel remesh, smoothing |
| Auto-retopology, quad remeshing | needs a remesher | budget planning, decimation, topology validation, correspondence via re-export |
| High→low baking, cage projection | needs a baker | normal derivation from height, channel packing, dilation audit, cage/scatter sampling |
| Rigging, skin weights, shape keys, facial rigs | needs a DCC rig engine | geometry/animation gates, symmetry and deformation-readiness validation |
| Cloth/rigid/fluid/smoke simulation | needs a solver | cache and RAM/disk budgets, determinism and seed policy, contact validation on capture data |
| Groom/hair systems | needs a grooming engine | card topology and mask audits, budget planning |
| Camera/Object tracking, photogrammetry, NeRF/Gaussian splats | needs solver/model runtimes and source footage | planning, provenance recording, license/consent audit of the sources |
| Neural text/image→3D, motion generation, ML lip-sync | needs GPU model weights and a runtime (offline weights are not shipped) | deterministic amplitude/text-driven lip-sync, provenance receipts (model/version/seed/inputs), license and training-permission audit |
| FBX / USD import-export | needs vendor/SDK libraries | OBJ/PLY/STL/GLB/JSON interchange; unit and axis conversion with recorded matrices |
| GPU rendering, denoising, CPU/GPU parity | needs a GPU renderer | VRAM/hardware planning, deterministic CPU review renders |
| Publishing, uploading, farm submission | prohibited by the approval rules | produces review media and manifests for a human to send |

If you need any of the above, they belong in the parent skill's DCC adapter layer; this Skill
Tool supplies the standards, gates, budgets, provenance and reporting that adapter must satisfy.

---

## 12. Packaged files

Everything sits directly in `offline-3d-production/` (no subfolders, no `SKILL.md`).

### Runnable tools (`tools-`)

| File | Role | Load order |
| --- | --- | --- |
| `tools-o3d.py` | **primary entry point**: CLI, argument parsing, all command handlers, capability registry, JSON/text output, exit codes | 1 (loads the rest) |
| `tools-o3d-core.py` | shared runtime: errors/exit codes, deadlines, path guard, atomic I/O, config loading/validation, hashing, audit log, environment info | 2 |
| `tools-o3d-geom.py` | geometry engine (stdlib only): importers/exporters, topology analysis, repair, decimate, remesh, subdivide, smooth, symmetry, sampling, UV analysis, unit/axis conversion, fixtures | 3 |
| `tools-o3d-texture.py` | texture engine: stdlib PNG codec, numpy image ops, procedural maps, PBR sets, packing, audit, variants, mips | 4 |
| `tools-o3d-anim.py` | animation engine: curve maths, RDP reduction, loops, retiming, BVH parse + FK, contacts, root motion, retarget, visemes, VTT/SRT | 5 |
| `tools-o3d-render.py` | render engine: camera, deterministic rasteriser, PNG/PPM/EXR writers, EXR header reader, compositing, ffmpeg encode | 6 |
| `tools-o3d-pipeline.py` | project/standards/QA engine: scaffold, naming, units, axis, config, gates, licenses, consent, provenance, manifests, planning, reports, contact sheet, fixtures | 7 |
| `tools-o3d-tests.py` | self-test suite (101 subprocess tests) | on demand |

Loading contract: `tools-o3d.py` loads each sibling by exact filename, registers it in
`sys.modules` as `o3d_core`, `o3d_geom`, `o3d_tex`, `o3d_anim`, `o3d_render`, `o3d_pipeline`, and
each module fetches its dependency with `sys.modules["o3d_core"]`. Sibling modules import each
other only through that registry, so the folder never needs to be a Python package (no
`__init__.py`).

### Knowledge (`knowledge-`)

| File | Role |
| --- | --- |
| `knowledge-readme.md` | this document — purpose, setup, commands, schemas, safety, tests, limits, troubleshooting, coverage map |

### Assets (`asset-`)

| File | Purpose |
| --- | --- |
| `asset-default-config.json` | default configuration; auto-discovered beside the entry point |
| `asset-license-policy.json` | license rule table consumed by `license audit` |
| `asset-consent-policy.json` | consent field/rule table consumed by `license consent` |
| `asset-render-presets.json` | named review/lighting/camera presets and their intended use |
| `asset-schema-report.schema.json` | JSON Schema (draft-07) for every structured result |
| `asset-handoff-template.json` | fill-in department handoff (source version, dependencies, contracts, approvals, license block) |
| `asset-handoff-handbook.md` | per-department minimums, the shared contract table, and what "approved" means |
| `asset-licence-register.template.csv` | license/consent register template with one worked row per license type |
| `asset-offline-install-manifest.template.json` | air-gapped bundle manifest template with install commands |
| `asset-recovery-runbook.md` | ten production failure procedures with executable commands |

File count: **19 files** in one folder (8 `tools-`, 1 `knowledge-`, 10 `asset-`). Path depth from the future skill root: 2 levels. No archives, no
subfolders, no generated logs, no caches, no machine-specific paths.
