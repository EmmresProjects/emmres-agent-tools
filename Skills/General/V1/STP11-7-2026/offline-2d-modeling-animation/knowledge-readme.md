# offline-2d-modeling-animation — Skill Tool

A complete, offline, dependency-free production toolkit covering the entire
"Offline 2D Modeling and Animation" skill catalog: **26 categories, 276
skills**, every one implemented as a callable, tested Python function.
Pure standard library (Python 3.10+; developed and validated on CPython
3.13.16). No network, no GPU, no secrets, no installs.

The folder is flat on purpose: it is a Skill Tool component meant to sit
directly beside a parent skill's `SKILL.md` (i.e. at
`<skill-root>/offline-2d-modeling-animation/`). All internal references are
relative to that future skill root, forward slashes, case-sensitive.

---

## Quick start

```bash
# from the folder that contains this Skill Tool folder (the future skill root)
python3 offline-2d-modeling-animation/tools-studio.py help
python3 offline-2d-modeling-animation/tools-studio.py capability-list
python3 offline-2d-modeling-animation/tools-studio.py capability-list --category 12
python3 offline-2d-modeling-animation/tools-studio.py info --apps
python3 offline-2d-modeling-animation/tools-studio.py init-project --name my-short --root ./my-short
python3 offline-2d-modeling-animation/tools-studio.py run anim.timecode frame=123 fps=24
python3 offline-2d-modeling-animation/tools-studio.py run qa.missing_frames directory=./render/sh010 pattern_range=1-48
```

Every command prints one JSON envelope:

```json
{"ok": true,  "tool": "...", "version": "1.0.0", "command": "...", "result": {...}}
{"ok": false, "error": {"code": "invalid_input", "message": "...", "details": {...}}}
```

Exit codes: `0` ok, `2` invalid input/usage, `3` runtime or file error,
`4` gate blocked, `5` approval required.

For image-object pipelines (frame renders, atlases, composites), import the
modules as a library instead — same folder, standard importlib or `sys.path`:

```python
import sys; sys.path.insert(0, "offline-2d-modeling-animation")
import tools-imaging as im, tools-anim as anim, tools-qa as qa   # etc.
img = im.new_image(1920, 1080, (255, 255, 255, 255))
im.draw_rect(img, 100, 100, 400, 300, (200, 60, 60, 255))
im.save_image("frame.png", img)
```

(Module files use `tools-` prefixes in their names; load them with
importlib or rename-free `sys.path` import as shown. The sibling-loader
inside each module handles cross-imports automatically.)

---

## Files and roles

**Runnable tools (`tools-`)**

| file | role |
| --- | --- |
| `tools-studio.py` | **Primary entry point.** CLI: help, capability-list, info, init-project, run (generic dispatcher), version. Owns the 276-skill catalog map. |
| `tools-core.py` | Shared foundation: structured errors (`StudioError`, `ApprovalRequired`, `GateBlocked`), atomic IO, validation helpers, config, splitmix64 RNG, naming, limits, shell-free bounded subprocess, executable discovery. |
| `tools-imaging.py` | Raster engine: PNG (8-bit truecolor/indexed/tRNS/gAMA/sRGB/text, APNG), BMP, TGA (RLE), PPM/PGM, GIF (LZW/animation/transparency) codecs; blending, alpha ops, adjustments, color science, quantize/dither, resample, blur, noise, analysis, drawing, 5x7 bitmap font, SVG-subset rasterizer, contact sheets, mini-PDF writer; raster craft (brush presets, flat separation, cleanup, mip chains, batch). |
| `tools-vector.py` | Vector engine: SVG document scaffold, optimize (regex minifier), audit, symbols, path math (bezier resample, simplify RDP, Chaikin, boolean ops, offsets), gradients/hatches, shape sheets, typography system, text-to-outlines (local TTF), speech balloons, credits cards, font coverage, vector-to-raster. |
| `tools-anim.py` | Animation core: xsheets, timing charts, holds, markers, retimes, timecode/slates; key poses, onion skins, rough animation, breakdowns, inbetweens, cleanup, smears, secondary motion, polish; bones/IK/FK, lattice deform, weight maps, stress tests, seam repair, rig bake; visemes/lipsync (+alt-language, drift, smooth, silent acting), facial plans; multiplane camera, parallax, shake, perspective grids, camera export; particles, trails, impact frames, atmospheric passes, procedural loops, seed policy; storyboard setup/revisions/animatic/panels/thumbnails, editorial conform. |
| `tools-audio.py` | Audio: WAV/AIFF decode (PCM 8/16/24/32, float32; AIFF 80-bit rate), WAV encode, convert, mix, normalize, gain/fade, trim, silence/onset detection, beat estimation, cue markers, dialogue cleanup (gate/de-click/normalize), scratch assembly, proxies, SFX librarian, peak/clipping QA, rate/frame conform. |
| `tools-neural.py` | Offline neural: runtime report/run (structured `runtime_not_configured` when absent), quantization plans, model provenance, prompt/seed recipes, and **deterministic procedural stand-ins** for txt2img/img2img/inpaint/background-removal/lineart-cleanup/colorize/interpolation/pose-overlay/character-reference/identity-check/motion/lipsync (all outputs honestly flagged `engine: "procedural-standin"`), generated-asset triage with approval gate. |
| `tools-assets.py` | Sprites & pixel art: sheet grids, atlas packing (deterministic skyline, extrusion, pow2), trim, slice, pivots + alignment validation, atlas metadata, nine-slice, tileset packaging, seamless check, memory budget, engine import check; pixel canvas setup, dither patterns, cluster cleanup, palette limits/swaps, scaling validation, retro constraints, pixel character animation; long-term format migration plans. |
| `tools-encode.py` | Export & automation: PNG-sequence/GIF/APNG/PDF exporters, WebP + video via optional external encoders, format router, export naming/versioning, encoder audit, batch runner (resume/retry), job queue with dependencies, reproducible script generation, sandboxed script runner, headless render plans, deterministic cache, workspace relink, checksums/integrity, smoke tests, build provenance, delivery packages, cross-app pipeline adapter. |
| `tools-qa.py` | QA & review: missing/duplicate/corrupt frames, alpha/loop/resolution/fps/color-profile validation, visual regression, flash safety, contact sheets, review packages (slates, burn-ins), acceptance + final delivery gates, temporal consistency suite (flicker, boil, texture lock, arcs, volume, repair plans), subtitles/captions, safe area/contrast, high-contrast & reduced-motion variants, localization packaging, cleanup scanner, optimization pass, reference sheets, style signatures. |
| `tools-project.py` | Production management: suite selection, installer/lockfile/plugin bundles, readiness/disk/GPU routing, portable workspaces, health checks, update staging, multi-OS plans, license inventory; project scaffold, naming enforcement, manifests/registries, snapshots, change impact, handoff, archives, dependency graphs, render queues; character/prop/environment design sheets, briefs, mouth charts, continuity maps; rigs (cutout split, migrate, controls, corrective shapes, facial table), boards analysis, camera setup/depth cues, FX comps/hand-drawn FX/caching, lettering guides, font library/licenses, rights (models, provenance, sanitization, consent, OSS obligations, quarantine, release gate), packaging (verify, cold-start, backups, knowledge), accessibility (text zones, RTL, description cues), layout integration, playback plans; interchange (Krita/Inkscape/Aseprite/OpenToonz/Synfig/Grease Pencil), paper-scan deskew/registration/tone removal, rotoscope prep, legacy rescue, 2D-to-3D reference packs, print planning, educational exercises, decommissioning. |

**Data assets (`asset-`)**

| file | purpose |
| --- | --- |
| `asset-viseme-chart.json` | Phoneme→viseme map, coarticulation rules, approximate-open fallback for energy-only lipsync. |
| `asset-color-palettes.json` | Six named palettes with roles and protected colors (incl. pixel-limited-16, gameboy-4). |
| `asset-easing-curves.json` | Easing presets, timing rules, camera-move defaults. |
| `asset-project-template.json` | Project scaffold: folder tree, naming rules, shot-manifest/asset-registry schemas, defaults (24 fps, 1920x1080, sRGB). |
| `asset-render-presets.json` | Image/animation/video/audio/review export presets (png/apng/gif/h264/ffv1/prores/wav). |
| `asset-retro-constraints.json` | Hardware profiles (DMG/NES/SNES/Genesis/GBA/Pico-8/generic) for pixel validation. |
| `asset-format-matrix.json` | Consumer→format routing + alpha notes for the format router. |
| `asset-qa-checklist.json` | Fifteen acceptance gates with critical flags for the QA gate commands. |
| `asset-rights-schema.json` | License classes, font/model/consent field schemas, attribution template, sanitizer targets. |

**Knowledge (`knowledge-`)** — this file is the only one: `knowledge-readme.md`.

---

## Capability map (26 categories / 276 skills)

Produced from the same table the CLI serves (`capability-list`); every entry
names the implementing function(s).

**1. Offline Platform, Installation, and Environment** (12 skills)
  - 1.1 Offline Suite Selector — `project.suite_selector`
  - 1.2 Air-Gapped Installer Packager — `project.installer_package`
  - 1.3 Dependency Lockfile Builder — `project.dependency_lockfile`
  - 1.4 Offline Plugin Bundle Curator — `project.plugin_bundle_curator`
  - 1.5 Workstation Readiness Auditor — `project.readiness_audit`
  - 1.6 Temporary Disk Budget Planner — `project.disk_budget`
  - 1.7 GPU Capability Router — `project.gpu_router`
  - 1.8 Portable Workspace Builder — `project.portable_workspace`
  - 1.9 Environment Health Checker — `project.environment_health_check`
  - 1.10 Offline Update Stager — `project.update_stager`
  - 1.11 Multi-OS Deployment Planner — `project.multi_os_plan`
  - 1.12 License Inventory Registrar — `project.license_inventory`

**2. Project Architecture and Production Management** (10 skills)
  - 2.1 2D Project Scaffolder — `project.project_init`
  - 2.2 Naming Convention Enforcer — `project.naming_enforcer`
  - 2.3 Shot Manifest Manager — `project.shot_manifest_update`
  - 2.4 Asset Registry Builder — `project.asset_registry_add`
  - 2.5 Version Snapshot Manager — `project.version_snapshot`
  - 2.6 Change Impact Analyzer — `project.change_impact`
  - 2.7 Production Handoff Packager — `project.handoff_package`
  - 2.8 Archive and Restore Operator — `project.archive_and_restore`
  - 2.9 Scene Dependency Grapher — `project.dependency_graph`
  - 2.10 Render Queue Planner — `project.render_queue_plan`

**3. Raster Drawing and Painting** (10 skills)
  - 3.1 Raster Canvas Creator — `imaging.new_image + imaging.save_image`
  - 3.2 Brush Engine Configurator — `imaging.brush_config`
  - 3.3 Brush Library Curator — `imaging.brush_library`
  - 3.4 Digital Inking Assistant — `imaging.ink_stroke`
  - 3.5 Flat Color Separator — `imaging.flat_color_separate`
  - 3.6 Raster Shading Builder — `imaging.auto_shadow`
  - 3.7 Texture Painting Operator — `imaging.tile_texture`
  - 3.8 Raster Cleanup Technician — `imaging.raster_cleanup`
  - 3.9 High-Resolution Downsample Specialist — `imaging.downsample_mip`
  - 3.10 Raster Batch Processor — `imaging.raster_batch`

**4. Vector Illustration and Shape Design** (10 skills)
  - 4.1 Vector Document Architect — `vector.svg_document`
  - 4.2 Bezier Path Specialist — `vector.resample_poly, vector.path_lengths, vector.point_at`
  - 4.3 Vector Line-Art Cleaner — `vector.simplify_polys, vector.smooth_chaikin`
  - 4.4 Shape Language Designer — `vector.shape_sheet`
  - 4.5 Boolean Geometry Operator — `vector.boolean_ops`
  - 4.6 Vector Gradient and Pattern Builder — `vector.gradient_svg, vector.hatch_pattern_svg`
  - 4.7 Symbol and Clone Manager — `vector.svg_symbolize`
  - 4.8 SVG Compatibility Auditor — `vector.svg_audit`
  - 4.9 Vector-to-Raster Exporter — `vector.vector_to_raster`
  - 4.10 Vector Asset Optimizer — `vector.svg_optimize`

**5. Pixel Art and Limited-Resolution Production** (10 skills)
  - 5.1 Pixel Canvas Setup — `assets.pixel_canvas_setup`
  - 5.2 Pixel Brush and Dither Designer — `assets.pixel_dither_pattern`
  - 5.3 Pixel Cluster Cleaner — `assets.pixel_cluster_cleanup`
  - 5.4 Limited Palette Optimizer — `assets.palette_limit`
  - 5.5 Subpixel Motion Planner — `anim.subpixel_motion`
  - 5.6 Pixel Tile Builder — `assets.tile_with_variants`
  - 5.7 Pixel Character Animator — `assets.pixel_character_animate`
  - 5.8 Pixel Scaling Validator — `assets.pixel_scaling_check`
  - 5.9 Palette Swap Generator — `assets.palette_swap, assets.palette_swap_by_index`
  - 5.10 Retro Platform Constraint Emulator — `assets.pixel_constraints_check`

**6. Layers, Masks, Compositing, and Color** (10 skills)
  - 6.1 Layer Stack Architect — `imaging.layer_stack`
  - 6.2 Mask Workflow Designer — `imaging.mask_workflow, imaging.mask_from_polygons`
  - 6.3 Blend Mode Auditor — `imaging.blend_mode_audit`
  - 6.4 Alpha Channel Specialist — `imaging.premultiply, imaging.defringe, imaging.flatten`
  - 6.5 Palette Library Manager — `assets.load_palettes, imaging.dominant_colors`
  - 6.6 Color Script Builder — `imaging.color_script`
  - 6.7 Color Profile Manager — `imaging.color_profile`
  - 6.8 Color-Blind Readability Checker — `imaging.apply_cvd, imaging.contrast_ratio`
  - 6.9 Compositing Preflight Operator — `imaging.compositing_preflight`
  - 6.10 Non-Destructive Correction Builder — `imaging.op_levels, imaging.op_curves, imaging.op_exposure`

**7. Fonts, Text, Lettering, and Graphic Elements** (8 skills)
  - 7.1 Offline Font Library Manager — `project.font_library`
  - 7.2 Font License Auditor — `project.font_license`
  - 7.3 Typography Style System Builder — `vector.typography_system`
  - 7.4 Hand-Lettering Assistant — `project.lettering_guides`
  - 7.5 Text-to-Path Converter — `vector.text_to_outlines`
  - 7.6 Multilingual Glyph Coverage Checker — `vector.read_font_coverage, vector.check_glyph_coverage`
  - 7.7 Speech Balloon Designer — `vector.speech_balloon`
  - 7.8 Credits and Legal Card Builder — `vector.credits_card`

**8. Character, Prop, and Environment Design** (10 skills)
  - 8.1 Character Brief Interpreter — `project.character_brief`
  - 8.2 Character Turnaround Generator — `project.design_sheet_template (kind=turnaround)`
  - 8.3 Expression Sheet Builder — `project.design_sheet_template (kind=expressions)`
  - 8.4 Mouth Chart Designer — `project.mouth_chart_template`
  - 8.5 Hand and Pose Library Builder — `project.design_sheet_template (kind=hands-poses)`
  - 8.6 Costume Variant Manager — `project.costume_variant_registry`
  - 8.7 Prop Turnaround Builder — `project.design_sheet_template (kind=prop)`
  - 8.8 Environment Template Builder — `project.design_sheet_template (kind=environment)`
  - 8.9 Location Continuity Mapper — `project.location_continuity_map`
  - 8.10 Model Sheet Consistency Auditor — `project.model_sheet_consistency`

**9. Storyboarding, Animatics, and Editorial Planning** (10 skills)
  - 9.1 Storyboard Project Setup — `anim.storyboard_setup`
  - 9.2 Storyboard Composition Assistant — `project.boards_composition`
  - 9.3 Shot Language Planner — `project.boards_shotlist`
  - 9.4 Continuity and Screen-Direction Checker — `project.boards_continuity`
  - 9.5 Animatic Assembler — `anim.animatic_assemble`
  - 9.6 Animatic Timing Refiner — `anim.animatic_timing_refine`
  - 9.7 Storyboard Revision Tracker — `anim.board_revision_compare`
  - 9.8 Panel Export Packager — `anim.panel_export`
  - 9.9 Thumbnail Sequence Generator — `anim.thumbnail_sheet`
  - 9.10 Editorial Conform Assistant — `anim.editorial_conform`

**10. Timing, Exposure Sheets, and Timeline Operations** (10 skills)
  - 10.1 Exposure Sheet Builder — `anim.build_xsheet`
  - 10.2 Dope Sheet Editor — `anim.edit_xsheet`
  - 10.3 Frame Rate Planner — `anim.fps_conversion_plan`
  - 10.4 Timing Chart Designer — `anim.timing_chart`
  - 10.5 Hold and Exposure Optimizer — `anim.hold_pattern`
  - 10.6 Timeline Marker Manager — `anim.markers`
  - 10.7 Retime and Time-Warp Operator — `anim.retime_plan, anim.lipsync_retime`
  - 10.8 Frame Range Reconciler — `anim.reconcile_ranges`
  - 10.9 Timecode and Slate Builder — `anim.timecode, anim.slate_image`
  - 10.10 Playback Performance Tuner — `project.playback_plan`

**11. Frame-by-Frame Animation** (10 skills)
  - 11.1 Key Pose Planner — `anim.key_pose_plan`
  - 11.2 Onion Skin Configurator — `anim.onion_skins`
  - 11.3 Rough Animation Builder — `anim.anim_rough`
  - 11.4 Breakdown Pose Designer — `anim.breakdown_pose`
  - 11.5 Inbetween Generator — `anim.inbetween`
  - 11.6 Cleanup Animation Specialist — `anim.cleanup_animation`
  - 11.7 Smear and Multiples Designer — `anim.smear_frame`
  - 11.8 Secondary Motion Animator — `anim.secondary_motion`
  - 11.9 Effects Draft Animator — `anim.simulate_particles`
  - 11.10 Animation Polish Pass — `anim.polish_report`

**12. Rigging, Cutout, and Deformation** (12 skills)
  - 12.1 Cutout Rig Architect — `project.rig_split`
  - 12.2 Bone Rig Builder — `anim.bone_rig_build`
  - 12.3 Inverse Kinematics Configurator — `anim.solve_ik_2bone, anim.solve_fk`
  - 12.4 Mesh Deformation Builder — `anim.lattice_deform`
  - 12.5 Weight Painting Assistant — `anim.weight_map`
  - 12.6 Smart Bone and Corrective Shape Designer — `project.rig_corrective_record`
  - 12.7 Facial Rig Builder — `project.facial_rig_table`
  - 12.8 Rig Control Interface Designer — `project.rig_controls`
  - 12.9 Rig Stress Tester — `anim.rig_stress_test`
  - 12.10 Rig Version Migrator — `project.rig_migrate`
  - 12.11 Puppet Seam Repairer — `anim.seam_repair`
  - 12.12 Rig Bake and Export Operator — `anim.bake_rig`

**13. Facial Performance and Lip Synchronization** (10 skills)
  - 13.1 Dialogue Analyzer — `anim.phonemes_to_visemes`
  - 13.2 Offline Lip-Sync Solver — `anim.lipsync_from_audio`
  - 13.3 Mouth Shape Mapper — `anim.load_viseme_chart`
  - 13.4 Coarticulation Refiner — `anim.lipsync_smooth`
  - 13.5 Facial Acting Planner — `anim.facial_plan`
  - 13.6 Blink and Gaze Animator — `anim.blink_gaze_plan`
  - 13.7 Lip-Sync Drift Checker — `anim.lipsync_drift_check`
  - 13.8 Alternate Language Lip-Sync Adapter — `anim.lipsync_alt_language`
  - 13.9 Facial Rig Range Validator — `anim.facial_range_check`
  - 13.10 Silent Performance Animator — `anim.facial_silent_plan`

**14. Audio Preparation and Waveform Synchronization** (10 skills)
  - 14.1 Offline Audio Ingestor — `audio.ingest`
  - 14.2 Dialogue Cleanup Operator — `audio.dialogue_cleanup`
  - 14.3 Waveform Cue Marker — `audio.cue_markers`
  - 14.4 Sample Rate and Frame Rate Reconciler — `audio.convert, audio.drift_check`
  - 14.5 Audio Proxy Builder — `audio.proxy`
  - 14.6 Scratch Track Assembler — `audio.scratch_assemble`
  - 14.7 Beat Sheet Extractor — `audio.beat_sheet`
  - 14.8 Offline Sound Effect Librarian — `audio.sfx_library`
  - 14.9 Audio Peak and Clipping Checker — `audio.audio_qa, audio.levels`
  - 14.10 Final Audio Conform Assistant — `audio.audio_conform`

**15. Camera, Multiplane, Layout, and Perspective** (10 skills)
  - 15.1 2D Camera Setup — `project.camera_setup`
  - 15.2 Multiplane Scene Builder — `anim.render_multiplane_frame`
  - 15.3 Parallax Motion Planner — `anim.parallax_plan`
  - 15.4 Camera Move Designer — `anim.camera_sample`
  - 15.5 Perspective Grid Builder — `anim.perspective_grid`
  - 15.6 Layout Pose Integrator — `project.layout_integrate`
  - 15.7 Camera Boundary Auditor — `anim.camera_boundary_audit`
  - 15.8 Focus and Depth Cue Designer — `project.depth_cue`
  - 15.9 Camera Data Exporter — `anim.camera_export`
  - 15.10 Shake and Impact Motion Builder — `anim.camera_shake`

**16. Particles, Effects, and Procedural Motion** (10 skills)
  - 16.1 2D Particle System Builder — `anim.simulate_particles`
  - 16.2 Hand-Drawn Effects Animator — `project.fx_handdrawn`
  - 16.3 Procedural Loop Generator — `anim.procedural_loop`
  - 16.4 Trail and Motion Stroke Designer — `anim.trail_frames`
  - 16.5 Effect Layer Compositor — `project.fx_comp`
  - 16.6 Particle Cache Manager — `project.fx_cache`
  - 16.7 Random Seed Controller — `anim.seed_policy`
  - 16.8 Effect Boundary Validator — `anim.effect_boundary_check`
  - 16.9 Impact Frame Designer — `anim.impact_frame`
  - 16.10 Atmospheric Pass Builder — `anim.atmospheric_pass`

**17. Offline Neural Image and Motion Generation** (16 skills)
  - 17.1 Offline Model Runtime Manager — `neural.runtime_report, neural.run_runtime`
  - 17.2 Offline Text-to-Image Operator — `neural.txt2img`
  - 17.3 Offline Image-to-Image Operator — `neural.img2img`
  - 17.4 Offline Inpainting Operator — `neural.inpaint`
  - 17.5 Offline Background Removal Operator — `neural.remove_background`
  - 17.6 Offline Line-Art Cleanup Operator — `neural.cleanup_lineart`
  - 17.7 Offline Colorization Operator — `neural.colorize`
  - 17.8 Offline Frame Interpolation Operator — `neural.interpolate_frames`
  - 17.9 Offline Pose Guidance Operator — `neural.pose_guidance_overlay`
  - 17.10 Offline Character Consistency Operator — `neural.character_reference_sheet`
  - 17.11 Offline Motion Generation Operator — `neural.motion_candidates`
  - 17.12 Offline Neural Lip-Sync Operator — `neural.neural_lipsync`
  - 17.13 Local Model Quantization Planner — `neural.quantization_plan`
  - 17.14 Model Provenance and License Auditor — `neural.model_provenance`
  - 17.15 Prompt and Seed Reproducer — `neural.save_recipe, neural.load_recipe`
  - 17.16 Generated Asset Triage — `neural.triage`

**18. Temporal and Character Consistency** (10 skills)
  - 18.1 Frame Consistency Checker — `qa.frame_consistency`
  - 18.2 Character Proportion Tracker — `qa.proportion_tracker`
  - 18.3 Character Identity Validator — `qa.identity_validator`
  - 18.4 Line Boil Controller — `qa.line_boil_analysis`
  - 18.5 Temporal Color Stabilizer — `qa.temporal_color_stabilizer`
  - 18.6 Texture Lock Validator — `qa.consistency_texture`
  - 18.7 Background Stability Checker — `qa.background_stability`
  - 18.8 Motion Arc Auditor — `qa.motion_arc_analysis`
  - 18.9 Volume Preservation Checker — `qa.volume_preservation`
  - 18.10 Generated Sequence Consistency Repair Planner — `qa.consistency_repair_plan`

**19. Sprite Sheets, Atlases, Tiles, and Game Assets** (10 skills)
  - 19.1 Sprite Sheet Layout Planner — `assets.sheet_grid`
  - 19.2 Texture Atlas Packer — `assets.atlas_pack`
  - 19.3 Sprite Trim and Extrude Operator — `assets.trim_sprite`
  - 19.4 Pivot and Anchor Manager — `assets.set_pivots`
  - 19.5 Sprite Alignment Validator — `assets.pivot_alignment_check`
  - 19.6 Atlas Metadata Exporter — `assets.atlas_metadata`
  - 19.7 Nine-Slice Asset Builder — `assets.nine_slice`
  - 19.8 Tilemap Asset Packager — `assets.tileset_package`
  - 19.9 Sprite Memory Budgeter — `assets.sprite_memory_budget`
  - 19.10 Engine Import Validator — `assets.engine_import_check`

**20. Export, Encoding, and Delivery Formats** (12 skills)
  - 20.1 Image Sequence Exporter — `encode.export_sequence`
  - 20.2 Animated GIF Exporter — `encode.export_gif_from_files`
  - 20.3 APNG Exporter — `encode.export_animation`
  - 20.4 Animated WebP Exporter — `encode.export_webp`
  - 20.5 MP4 Review Exporter — `encode.export_video (codec=h264)`
  - 20.6 Master Video Exporter — `encode.export_video (codec=ffv1/prores)`
  - 20.7 Transparent Video Export Planner — `encode.transparent_video_plan`
  - 20.8 Format Compatibility Router — `encode.format_router`
  - 20.9 Batch Export Orchestrator — `encode.run_batch`
  - 20.10 Export Naming and Versioning Enforcer — `encode.export_name`
  - 20.11 Encoder Availability Auditor — `encode.encoder_audit`
  - 20.12 Delivery Package Builder — `encode.delivery_package`

**21. Validation, Quality Assurance, and Review** (14 skills)
  - 21.1 Missing Frame Detector — `qa.missing_frames`
  - 21.2 Duplicate Frame Detector — `qa.duplicate_frames`
  - 21.3 Corrupt Frame Detector — `qa.corrupt_frames`
  - 21.4 Alpha Channel Validator — `qa.alpha_validation`
  - 21.5 Animation Loop Validator — `qa.loop_check`
  - 21.6 Color Profile Validator — `qa.color_profile_check`
  - 21.7 Resolution and Aspect Validator — `qa.resolution_check`
  - 21.8 Frame Rate Metadata Validator — `qa.fps_metadata_check`
  - 21.9 Sprite Pivot Validator — `assets.pivot_alignment_check`
  - 21.10 Visual Regression Comparator — `qa.visual_regression`
  - 21.11 Flicker and Flash Safety Checker — `qa.flash_safety`
  - 21.12 Review Contact Sheet Builder — `qa.build_contact_sheet`
  - 21.13 Playback Review Packager — `qa.review_package`
  - 21.14 Acceptance Gate Runner — `qa.acceptance_gate, qa.final_delivery_gate`

**22. Automation, Scripting, and Reproducibility** (12 skills)
  - 22.1 Reproducible Export Script Builder — `encode.generate_script`
  - 22.2 Manifest-Driven Batch Processor — `encode.run_batch`
  - 22.3 Headless Render Operator — `encode.headless_render`
  - 22.4 Cross-Application Pipeline Adapter — `encode.pipeline_adapter`
  - 22.5 Deterministic Cache Builder — `encode.cache_build`
  - 22.6 Offline Job Queue — `encode.job_queue`
  - 22.7 Workspace Relinker — `encode.relink_workspace`
  - 22.8 Checksum and Integrity Reporter — `encode.checksum_report, encode.verify_checksums`
  - 22.9 Script Safety Sandbox — `encode.sandbox_run`
  - 22.10 Pipeline Smoke Test Builder — `encode.pipeline_smoke_test`
  - 22.11 Failure Resume Manager — `encode.run_batch (resume), encode.job_queue (retry)`
  - 22.12 Build Provenance Recorder — `encode.build_provenance`

**23. Licensing, Rights, Privacy, and Governance** (10 skills)
  - 23.1 Asset License Classifier — `project.classify_asset`
  - 23.2 Model Usage Rights Auditor — `project.rights_model`
  - 23.3 Font Embedding and Redistribution Checker — `project.font_license`
  - 23.4 Attribution File Generator — `project.attribution_file`
  - 23.5 Provenance Metadata Manager — `project.rights_provenance`
  - 23.6 Sensitive Reference Sanitizer — `project.sanitize_for_sharing`
  - 23.7 Talent Consent Tracker — `project.consent_record`
  - 23.8 Open-Source Obligation Packager — `project.oss_obligations`
  - 23.9 Third-Party Asset Quarantine — `project.quarantine_assets`
  - 23.10 Release Rights Gate — `project.release_rights_gate`

**24. Packaging, Archival, Recovery, and Handoff** (10 skills)
  - 24.1 Clean Offline Project Bundle Builder — `project.handoff_package`
  - 24.2 Bundle Completeness Validator — `project.pack_verify`
  - 24.3 Cold-Start Rebuild Tester — `project.pack_coldstart`
  - 24.4 Archive Tier Planner — `project.archive_tier_plan`
  - 24.5 Long-Term Format Migrator — `assets.format_migration_plan`
  - 24.6 Disaster Recovery Drill — `project.disaster_recovery_drill`
  - 24.7 Incremental Backup Planner — `project.pack_backup`
  - 24.8 Project Decommissioner — `project.decommission`
  - 24.9 Recipient Handoff Guide Writer — `project.recipient_guide`
  - 24.10 Offline Knowledge Packager — `project.pack_knowledge`

**25. Accessibility, Localization, and Alternate Deliverables** (8 skills)
  - 25.1 Subtitle and Caption Timing Builder — `qa.build_subtitles`
  - 25.2 Localized Text Replacement Planner — `project.access_text_zones`
  - 25.3 Right-to-Left Layout Checker — `project.access_rtl_check`
  - 25.4 Safe-Area and Legibility Checker — `qa.safe_area_check, qa.contrast_check`
  - 25.5 Audio Description Cue Planner — `project.access_description_cues`
  - 25.6 Reduced-Motion Variant Builder — `qa.reduced_motion_variant`
  - 25.7 High-Contrast Variant Builder — `qa.high_contrast_variant`
  - 25.8 Localization Package Exporter — `qa.localization_package`

**26. Specialized Workflows and Interchange** (12 skills)
  - 26.1 Paper Scan and Registration Operator — `project.deskew, project.register_pages, project.remove_paper_tone`
  - 26.2 Rotoscope Preparation Assistant — `project.rotoscope_prep`
  - 26.3 Grease Pencil 2D-3D Hybrid Builder — `project.interchange_package (app=grease-pencil)`
  - 26.4 OpenToonz Scene Exchange Manager — `project.interchange_package (app=opentoonz)`
  - 26.5 Synfig Cutout Exchange Manager — `project.interchange_package (app=synfig)`
  - 26.6 Krita Animation Exchange Manager — `project.interchange_package (app=krita)`
  - 26.7 Aseprite Sprite Exchange Manager — `project.interchange_package (app=aseprite)`
  - 26.8 Inkscape Vector Exchange Manager — `project.interchange_package (app=inkscape)`
  - 26.9 Legacy Format Rescue Operator — `project.legacy_rescue`
  - 26.10 2D-to-3D Reference Packager — `project.reference_pack_3d`
  - 26.11 Print and Merchandise Output Planner — `project.print_plan`
  - 26.12 Educational Exercise Generator — `project.educational_exercises`


---

## Command reference (CLI)

- `help` — commands and conventions.
- `capability-list [--category N] [--json]` — the full map above.
- `info [--apps]` — machine report; `--apps` adds optional external tool detection (never executes anything).
- `init-project --name NAME --root DIR [--fps N] [--width N] [--height N]` — scaffold a production tree (28 directories, manifest, registry, defaults).
- `run MODULE.FUNCTION key=value ...` — call any public function. Values are
  parsed as JSON when they look like JSON (`true`, `null`, numbers,
  `[...]`, `{...}`); otherwise they stay strings. Unknown parameters are
  rejected with the accepted parameter list. Flags: `--out FILE` (write the
  envelope to a file), `--compact` (single-line JSON).
- `version` — name + version.

Library-level functions that pass image/audio objects between calls (frame
pipelines, atlas packing, compositing) are intended for Python use; the
`run` command suits data-in/data-out functions (paths, dicts, numbers).

## Configuration

Optional `studio-config.json` beside the tool folder (or pass a config dict
in library use). Missing keys fall back to built-in defaults:

```json
{
  "limits": {"max_pixels": 40000000, "max_frames": 20000,
             "max_rows": 200000, "subprocess_timeout": 600,
             "subprocess_retries": 0, "sheet_max_tiles": 20000},
  "apps": {"krita": null, "inkscape": null, "aseprite": null,
           "blender": null, "opentoonz": null, "synfig": null,
           "ffmpeg": null, "cwebp": null, "magick": null},
  "export": {"h264_crf": "18", "h264_preset": "medium", "master_codec": "ffv1"},
  "neural": {"runtimes": {}}
}
```

`apps` values are `null` (discover on PATH) or an absolute path to a
specific binary. No secrets, keys, or paid credentials are stored or needed.

## Safety model

- **Approval gates**: destructive/irreversible/publishing actions
  (decommission, release rights clearance, quarantine release, generated
  triage) raise `ApprovalRequired` (exit 5) until explicitly approved.
- **Gates**: QA/release gates raise `GateBlocked` (exit 4) while critical
  checks fail or (final gate) are not run.
- **Bounded execution**: pixel/frame/row limits, subprocess timeouts and
  retries, no shell interpolation (`run_command` takes argv lists only).
- **Non-destructive defaults**: cleanup stages files to a trash folder;
  snapshots zip before archiving; originals are never edited in place.
- **No network, no telemetry, no embedded credentials.**

## Dependencies

- **Runtime: none.** Python 3.10+ standard library only (validated on
  CPython 3.13.16). Nothing to install.
- **Optional external applications** (all degrade gracefully with
  structured `encoder_unavailable` / plan-only results when absent):
  `ffmpeg` (video encode, WebP route, non-WAV/AIFF audio decode),
  `cwebp` (WebP), ImageMagick `magick` (alternative image/PDF route),
  plus the DCC apps (Krita, Inkscape, Aseprite, Blender, OpenToonz,
  Synfig) for interchange/plans only. Configure paths in
  `studio-config.json` under `apps`.
- Development-only tools used while building this Skill Tool (NOT needed at
  runtime): Pillow 13.0.0 (cross-validation of codec round-trips) and pypdf
  (source catalog extraction).

## Limitations (exact)

- **Raster encode**: PNG (8-bit truecolor/indexed + tRNS, gAMA/sRGB, text),
  BMP (24-bit default; 32-bit alpha opt-in — many readers ignore BMP alpha),
  TGA (RLE), PPM/PGM, GIF, APNG. **No JPEG/TIFF/WebP encoding built in** —
  WebP export returns `encoder_unavailable` unless `cwebp`/`ffmpeg` is
  configured. Interlaced PNG is rejected with a structured error. 16-bit
  PNG loads are reduced to 8-bit.
- **Vector**: the built-in SVG parser/rasterizer covers a practical subset
  (paths, basic shapes, transforms, simple gradients); `vector.svg_audit`
  flags unsupported features. Text outlining uses local TTF/OTF fonts
  (DejaVu ships on most systems; point `font_library` at your font folder).
- **Audio**: WAV/AIFF decode and WAV encode only, natively. Compressed
  formats (MP3/OGG/FLAC) require a configured `ffmpeg`. Analysis-grade
  tools (onsets/beats) are energy-based heuristics.
- **Neural**: no model weights or runtimes are bundled. Without a
  configured local runtime, `runtime_report`/`run_runtime` return
  `runtime_not_configured`, and every generation function returns an
  honestly-labelled deterministic **procedural stand-in**
  (`engine: "procedural-standin"`) — useful for pipeline tests and previz,
  not artist-quality generation. Configure runtimes in
  `studio-config.json` (`neural.runtimes`) to bind real engines later.
- **Video export**: h264/FFV1/ProRes masters require external `ffmpeg`
  (not installed in the build environment — the structured-unavailable
  path is what was tested; ImageMagick `magick` was present and its
  image/PDF route was exercised).
- **OpenToonz**: no stable headless CLI exists — interchange packages only.
- **QA heuristics**: perceptual hashes, luma deltas, and silhouette metrics
  are heuristics; they flag candidates for human review and do not replace
  eyes. Flash-safety checks use luminance-delta thresholds, not a certified
  photosensitivity test.
- **Performance**: pure-Python pixel loops — multi-megapixel blurs and
  full-HD frame batches take seconds to minutes. Limits protect against
  runaway sizes; use proxies (built in) for scrubbing.

## Validation performed (actually run)

- Codec round-trips: PNG/GIF/BMP/TGA/PPM exact byte-level re-decode
  equality; cross-validated against Pillow in both directions
  (development-only dependency).
- Animation/audio/QA suites: xsheets→storyboard, lipsync (incl. alternate
  language, drift, smooth, retime), camera/multiplane, particles/FX, WAV
  8/16/24/int32/float32 + AIFF decode, beat estimate 119 BPM on a 120 BPM
  reference signal, dialogue cleanup (gate/de-click/normalize verified),
  missing/duplicate/corrupt frame detection, flash-safety events detected,
  acceptance gate 15/15 pass **and** block paths, final delivery gate
  blocks not-run checks, visual regression, subtitles with reading-speed
  flags, localization package.
- Pipelines: atlas pack/slice/pivot validation, pixel-constraint checks
  (DMG/GBA profiles), export naming/versioning, batch resume + retry,
  job-queue dependencies, checksum verify, delivery packaging, smoke tests,
  relink, provenance, encoder audit, pipeline adapter plans.
- Project ops: 28-directory scaffold, snapshot/restore, dependency graph,
  change impact, render queue, disk budget, multi-OS plan, rights
  registry→attribution→release gate (blocked→cleared), quarantine,
  archive tiers + DR drill + decommission approval, paper-scan deskew
  (3.0 deg recovered vs 3.1 deg true), page registration, rotoscope prep,
  Krita/Inkscape interchange normalization, legacy rescue, 2D-to-3D
  reference pack, print plan (20.3 x 25.4 cm + cut SVG), 8 educational
  exercises, rig split/migrate/controls, boards analysis, camera/depth/FX,
  fonts, accessibility suite, layout integration.
- CLI: help / capability-list (276 skills, 302 function references all
  resolve) / info / init-project / run (success, JSON coercion,
  unknown-parameter rejection, missing-parameter rejection, unknown command)
  / approval-gate exit path / `--out` envelope writing.
- Error handling: invalid, missing, and malformed inputs produce structured
  errors; one recovery path exercised per module family (e.g. batch resume,
  gate re-run after fix, trash-folder restore).

## Compatibility

- Copy this folder directly beside a parent package's root `SKILL.md`; no
  modification needed. No subfolders; 21 files total (11 `tools-`, 9
  `asset-`, 1 `knowledge-`); all paths are 1 level below the future skill
  root (limit: 3). Internal references are relative, forward-slash,
  case-sensitive. No SKILL.md inside, by design.
