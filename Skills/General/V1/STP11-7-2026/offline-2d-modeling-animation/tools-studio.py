#!/usr/bin/env python3
"""tools-studio.py — primary entry point for the offline-2d-modeling-animation
Skill Tool.

A single, dependency-free command line surface over the tool modules:

  tools-core.py     shared foundation (errors, IO, limits, subprocess, config)
  tools-imaging.py  raster engine (PNG/BMP/TGA/PPM/GIF/APNG + pixel ops)
  tools-vector.py   SVG/vector/typography engine
  tools-anim.py     timing, xsheets, rigs, lipsync, camera, effects, boards
  tools-audio.py    WAV/AIFF decode, encode, analysis, conform
  tools-neural.py   offline neural runtime reporting + deterministic stand-ins
  tools-assets.py   sprite sheets, atlases, pixel-art constraints, tiles
  tools-encode.py   export/encode, batches, job queue, provenance, checksums
  tools-qa.py       validation, QA gates, review packaging, accessibility
  tools-project.py  project management, rights, packaging, interchange, misc

Commands:
  help              overview of commands and conventions
  capability-list   all 26 categories / 276 catalog skills -> function mapping
  info              machine report + optional external app detection
  init-project      scaffold a production folder tree
  run               call any module function with JSON-friendly arguments

Every command prints one JSON envelope: {"ok": true, "result": ...} or
{"ok": false, "error": {"code": ..., "message": ..., ...}}.
Exit codes: 0 ok, 2 invalid input, 3 runtime/file error, 4 gate blocked,
5 approval required.
"""
import json
import os
import sys

TOOL_NAME = "offline-2d-modeling-animation"
TOOL_VERSION = "1.0.0"
MODULES = ("core", "imaging", "vector", "anim", "audio", "neural", "assets",
           "encode", "qa", "project")

# ------------------------------------------------------------------ catalog --

CATEGORIES = [
    (1, "Offline Platform, Installation, and Environment", [
        (1, "Offline Suite Selector", "project.suite_selector"),
        (2, "Air-Gapped Installer Packager", "project.installer_package"),
        (3, "Dependency Lockfile Builder", "project.dependency_lockfile"),
        (4, "Offline Plugin Bundle Curator", "project.plugin_bundle_curator"),
        (5, "Workstation Readiness Auditor", "project.readiness_audit"),
        (6, "Temporary Disk Budget Planner", "project.disk_budget"),
        (7, "GPU Capability Router", "project.gpu_router"),
        (8, "Portable Workspace Builder", "project.portable_workspace"),
        (9, "Environment Health Checker", "project.environment_health_check"),
        (10, "Offline Update Stager", "project.update_stager"),
        (11, "Multi-OS Deployment Planner", "project.multi_os_plan"),
        (12, "License Inventory Registrar", "project.license_inventory"),
    ]),
    (2, "Project Architecture and Production Management", [
        (1, "2D Project Scaffolder", "project.project_init"),
        (2, "Naming Convention Enforcer", "project.naming_enforcer"),
        (3, "Shot Manifest Manager", "project.shot_manifest_update"),
        (4, "Asset Registry Builder", "project.asset_registry_add"),
        (5, "Version Snapshot Manager", "project.version_snapshot"),
        (6, "Change Impact Analyzer", "project.change_impact"),
        (7, "Production Handoff Packager", "project.handoff_package"),
        (8, "Archive and Restore Operator", "project.archive_and_restore"),
        (9, "Scene Dependency Grapher", "project.dependency_graph"),
        (10, "Render Queue Planner", "project.render_queue_plan"),
    ]),
    (3, "Raster Drawing and Painting", [
        (1, "Raster Canvas Creator", "imaging.new_image + imaging.save_image"),
        (2, "Brush Engine Configurator", "imaging.brush_config"),
        (3, "Brush Library Curator", "imaging.brush_library"),
        (4, "Digital Inking Assistant", "imaging.ink_stroke"),
        (5, "Flat Color Separator", "imaging.flat_color_separate"),
        (6, "Raster Shading Builder", "imaging.auto_shadow"),
        (7, "Texture Painting Operator", "imaging.tile_texture"),
        (8, "Raster Cleanup Technician", "imaging.raster_cleanup"),
        (9, "High-Resolution Downsample Specialist", "imaging.downsample_mip"),
        (10, "Raster Batch Processor", "imaging.raster_batch"),
    ]),
    (4, "Vector Illustration and Shape Design", [
        (1, "Vector Document Architect", "vector.svg_document"),
        (2, "Bezier Path Specialist", "vector.resample_poly, vector.path_lengths, vector.point_at"),
        (3, "Vector Line-Art Cleaner", "vector.simplify_polys, vector.smooth_chaikin"),
        (4, "Shape Language Designer", "vector.shape_sheet"),
        (5, "Boolean Geometry Operator", "vector.boolean_ops"),
        (6, "Vector Gradient and Pattern Builder", "vector.gradient_svg, vector.hatch_pattern_svg"),
        (7, "Symbol and Clone Manager", "vector.svg_symbolize"),
        (8, "SVG Compatibility Auditor", "vector.svg_audit"),
        (9, "Vector-to-Raster Exporter", "vector.vector_to_raster"),
        (10, "Vector Asset Optimizer", "vector.svg_optimize"),
    ]),
    (5, "Pixel Art and Limited-Resolution Production", [
        (1, "Pixel Canvas Setup", "assets.pixel_canvas_setup"),
        (2, "Pixel Brush and Dither Designer", "assets.pixel_dither_pattern"),
        (3, "Pixel Cluster Cleaner", "assets.pixel_cluster_cleanup"),
        (4, "Limited Palette Optimizer", "assets.palette_limit"),
        (5, "Subpixel Motion Planner", "anim.subpixel_motion"),
        (6, "Pixel Tile Builder", "assets.tile_with_variants"),
        (7, "Pixel Character Animator", "assets.pixel_character_animate"),
        (8, "Pixel Scaling Validator", "assets.pixel_scaling_check"),
        (9, "Palette Swap Generator", "assets.palette_swap, assets.palette_swap_by_index"),
        (10, "Retro Platform Constraint Emulator", "assets.pixel_constraints_check"),
    ]),
    (6, "Layers, Masks, Compositing, and Color", [
        (1, "Layer Stack Architect", "imaging.layer_stack"),
        (2, "Mask Workflow Designer", "imaging.mask_workflow, imaging.mask_from_polygons"),
        (3, "Blend Mode Auditor", "imaging.blend_mode_audit"),
        (4, "Alpha Channel Specialist", "imaging.premultiply, imaging.defringe, imaging.flatten"),
        (5, "Palette Library Manager", "assets.load_palettes, imaging.dominant_colors"),
        (6, "Color Script Builder", "imaging.color_script"),
        (7, "Color Profile Manager", "imaging.color_profile"),
        (8, "Color-Blind Readability Checker", "imaging.apply_cvd, imaging.contrast_ratio"),
        (9, "Compositing Preflight Operator", "imaging.compositing_preflight"),
        (10, "Non-Destructive Correction Builder", "imaging.op_levels, imaging.op_curves, imaging.op_exposure"),
    ]),
    (7, "Fonts, Text, Lettering, and Graphic Elements", [
        (1, "Offline Font Library Manager", "project.font_library"),
        (2, "Font License Auditor", "project.font_license"),
        (3, "Typography Style System Builder", "vector.typography_system"),
        (4, "Hand-Lettering Assistant", "project.lettering_guides"),
        (5, "Text-to-Path Converter", "vector.text_to_outlines"),
        (6, "Multilingual Glyph Coverage Checker", "vector.read_font_coverage, vector.check_glyph_coverage"),
        (7, "Speech Balloon Designer", "vector.speech_balloon"),
        (8, "Credits and Legal Card Builder", "vector.credits_card"),
    ]),
    (8, "Character, Prop, and Environment Design", [
        (1, "Character Brief Interpreter", "project.character_brief"),
        (2, "Character Turnaround Generator", "project.design_sheet_template (kind=turnaround)"),
        (3, "Expression Sheet Builder", "project.design_sheet_template (kind=expressions)"),
        (4, "Mouth Chart Designer", "project.mouth_chart_template"),
        (5, "Hand and Pose Library Builder", "project.design_sheet_template (kind=hands-poses)"),
        (6, "Costume Variant Manager", "project.costume_variant_registry"),
        (7, "Prop Turnaround Builder", "project.design_sheet_template (kind=prop)"),
        (8, "Environment Template Builder", "project.design_sheet_template (kind=environment)"),
        (9, "Location Continuity Mapper", "project.location_continuity_map"),
        (10, "Model Sheet Consistency Auditor", "project.model_sheet_consistency"),
    ]),
    (9, "Storyboarding, Animatics, and Editorial Planning", [
        (1, "Storyboard Project Setup", "anim.storyboard_setup"),
        (2, "Storyboard Composition Assistant", "project.boards_composition"),
        (3, "Shot Language Planner", "project.boards_shotlist"),
        (4, "Continuity and Screen-Direction Checker", "project.boards_continuity"),
        (5, "Animatic Assembler", "anim.animatic_assemble"),
        (6, "Animatic Timing Refiner", "anim.animatic_timing_refine"),
        (7, "Storyboard Revision Tracker", "anim.board_revision_compare"),
        (8, "Panel Export Packager", "anim.panel_export"),
        (9, "Thumbnail Sequence Generator", "anim.thumbnail_sheet"),
        (10, "Editorial Conform Assistant", "anim.editorial_conform"),
    ]),
    (10, "Timing, Exposure Sheets, and Timeline Operations", [
        (1, "Exposure Sheet Builder", "anim.build_xsheet"),
        (2, "Dope Sheet Editor", "anim.edit_xsheet"),
        (3, "Frame Rate Planner", "anim.fps_conversion_plan"),
        (4, "Timing Chart Designer", "anim.timing_chart"),
        (5, "Hold and Exposure Optimizer", "anim.hold_pattern"),
        (6, "Timeline Marker Manager", "anim.markers"),
        (7, "Retime and Time-Warp Operator", "anim.retime_plan, anim.lipsync_retime"),
        (8, "Frame Range Reconciler", "anim.reconcile_ranges"),
        (9, "Timecode and Slate Builder", "anim.timecode, anim.slate_image"),
        (10, "Playback Performance Tuner", "project.playback_plan"),
    ]),
    (11, "Frame-by-Frame Animation", [
        (1, "Key Pose Planner", "anim.key_pose_plan"),
        (2, "Onion Skin Configurator", "anim.onion_skins"),
        (3, "Rough Animation Builder", "anim.anim_rough"),
        (4, "Breakdown Pose Designer", "anim.breakdown_pose"),
        (5, "Inbetween Generator", "anim.inbetween"),
        (6, "Cleanup Animation Specialist", "anim.cleanup_animation"),
        (7, "Smear and Multiples Designer", "anim.smear_frame"),
        (8, "Secondary Motion Animator", "anim.secondary_motion"),
        (9, "Effects Draft Animator", "anim.simulate_particles"),
        (10, "Animation Polish Pass", "anim.polish_report"),
    ]),
    (12, "Rigging, Cutout, and Deformation", [
        (1, "Cutout Rig Architect", "project.rig_split"),
        (2, "Bone Rig Builder", "anim.bone_rig_build"),
        (3, "Inverse Kinematics Configurator", "anim.solve_ik_2bone, anim.solve_fk"),
        (4, "Mesh Deformation Builder", "anim.lattice_deform"),
        (5, "Weight Painting Assistant", "anim.weight_map"),
        (6, "Smart Bone and Corrective Shape Designer", "project.rig_corrective_record"),
        (7, "Facial Rig Builder", "project.facial_rig_table"),
        (8, "Rig Control Interface Designer", "project.rig_controls"),
        (9, "Rig Stress Tester", "anim.rig_stress_test"),
        (10, "Rig Version Migrator", "project.rig_migrate"),
        (11, "Puppet Seam Repairer", "anim.seam_repair"),
        (12, "Rig Bake and Export Operator", "anim.bake_rig"),
    ]),
    (13, "Facial Performance and Lip Synchronization", [
        (1, "Dialogue Analyzer", "anim.phonemes_to_visemes"),
        (2, "Offline Lip-Sync Solver", "anim.lipsync_from_audio"),
        (3, "Mouth Shape Mapper", "anim.load_viseme_chart"),
        (4, "Coarticulation Refiner", "anim.lipsync_smooth"),
        (5, "Facial Acting Planner", "anim.facial_plan"),
        (6, "Blink and Gaze Animator", "anim.blink_gaze_plan"),
        (7, "Lip-Sync Drift Checker", "anim.lipsync_drift_check"),
        (8, "Alternate Language Lip-Sync Adapter", "anim.lipsync_alt_language"),
        (9, "Facial Rig Range Validator", "anim.facial_range_check"),
        (10, "Silent Performance Animator", "anim.facial_silent_plan"),
    ]),
    (14, "Audio Preparation and Waveform Synchronization", [
        (1, "Offline Audio Ingestor", "audio.ingest"),
        (2, "Dialogue Cleanup Operator", "audio.dialogue_cleanup"),
        (3, "Waveform Cue Marker", "audio.cue_markers"),
        (4, "Sample Rate and Frame Rate Reconciler", "audio.convert, audio.drift_check"),
        (5, "Audio Proxy Builder", "audio.proxy"),
        (6, "Scratch Track Assembler", "audio.scratch_assemble"),
        (7, "Beat Sheet Extractor", "audio.beat_sheet"),
        (8, "Offline Sound Effect Librarian", "audio.sfx_library"),
        (9, "Audio Peak and Clipping Checker", "audio.audio_qa, audio.levels"),
        (10, "Final Audio Conform Assistant", "audio.audio_conform"),
    ]),
    (15, "Camera, Multiplane, Layout, and Perspective", [
        (1, "2D Camera Setup", "project.camera_setup"),
        (2, "Multiplane Scene Builder", "anim.render_multiplane_frame"),
        (3, "Parallax Motion Planner", "anim.parallax_plan"),
        (4, "Camera Move Designer", "anim.camera_sample"),
        (5, "Perspective Grid Builder", "anim.perspective_grid"),
        (6, "Layout Pose Integrator", "project.layout_integrate"),
        (7, "Camera Boundary Auditor", "anim.camera_boundary_audit"),
        (8, "Focus and Depth Cue Designer", "project.depth_cue"),
        (9, "Camera Data Exporter", "anim.camera_export"),
        (10, "Shake and Impact Motion Builder", "anim.camera_shake"),
    ]),
    (16, "Particles, Effects, and Procedural Motion", [
        (1, "2D Particle System Builder", "anim.simulate_particles"),
        (2, "Hand-Drawn Effects Animator", "project.fx_handdrawn"),
        (3, "Procedural Loop Generator", "anim.procedural_loop"),
        (4, "Trail and Motion Stroke Designer", "anim.trail_frames"),
        (5, "Effect Layer Compositor", "project.fx_comp"),
        (6, "Particle Cache Manager", "project.fx_cache"),
        (7, "Random Seed Controller", "anim.seed_policy"),
        (8, "Effect Boundary Validator", "anim.effect_boundary_check"),
        (9, "Impact Frame Designer", "anim.impact_frame"),
        (10, "Atmospheric Pass Builder", "anim.atmospheric_pass"),
    ]),
    (17, "Offline Neural Image and Motion Generation", [
        (1, "Offline Model Runtime Manager", "neural.runtime_report, neural.run_runtime"),
        (2, "Offline Text-to-Image Operator", "neural.txt2img"),
        (3, "Offline Image-to-Image Operator", "neural.img2img"),
        (4, "Offline Inpainting Operator", "neural.inpaint"),
        (5, "Offline Background Removal Operator", "neural.remove_background"),
        (6, "Offline Line-Art Cleanup Operator", "neural.cleanup_lineart"),
        (7, "Offline Colorization Operator", "neural.colorize"),
        (8, "Offline Frame Interpolation Operator", "neural.interpolate_frames"),
        (9, "Offline Pose Guidance Operator", "neural.pose_guidance_overlay"),
        (10, "Offline Character Consistency Operator", "neural.character_reference_sheet"),
        (11, "Offline Motion Generation Operator", "neural.motion_candidates"),
        (12, "Offline Neural Lip-Sync Operator", "neural.neural_lipsync"),
        (13, "Local Model Quantization Planner", "neural.quantization_plan"),
        (14, "Model Provenance and License Auditor", "neural.model_provenance"),
        (15, "Prompt and Seed Reproducer", "neural.save_recipe, neural.load_recipe"),
        (16, "Generated Asset Triage", "neural.triage"),
    ]),
    (18, "Temporal and Character Consistency", [
        (1, "Frame Consistency Checker", "qa.frame_consistency"),
        (2, "Character Proportion Tracker", "qa.proportion_tracker"),
        (3, "Character Identity Validator", "qa.identity_validator"),
        (4, "Line Boil Controller", "qa.line_boil_analysis"),
        (5, "Temporal Color Stabilizer", "qa.temporal_color_stabilizer"),
        (6, "Texture Lock Validator", "qa.consistency_texture"),
        (7, "Background Stability Checker", "qa.background_stability"),
        (8, "Motion Arc Auditor", "qa.motion_arc_analysis"),
        (9, "Volume Preservation Checker", "qa.volume_preservation"),
        (10, "Generated Sequence Consistency Repair Planner", "qa.consistency_repair_plan"),
    ]),
    (19, "Sprite Sheets, Atlases, Tiles, and Game Assets", [
        (1, "Sprite Sheet Layout Planner", "assets.sheet_grid"),
        (2, "Texture Atlas Packer", "assets.atlas_pack"),
        (3, "Sprite Trim and Extrude Operator", "assets.trim_sprite"),
        (4, "Pivot and Anchor Manager", "assets.set_pivots"),
        (5, "Sprite Alignment Validator", "assets.pivot_alignment_check"),
        (6, "Atlas Metadata Exporter", "assets.atlas_metadata"),
        (7, "Nine-Slice Asset Builder", "assets.nine_slice"),
        (8, "Tilemap Asset Packager", "assets.tileset_package"),
        (9, "Sprite Memory Budgeter", "assets.sprite_memory_budget"),
        (10, "Engine Import Validator", "assets.engine_import_check"),
    ]),
    (20, "Export, Encoding, and Delivery Formats", [
        (1, "Image Sequence Exporter", "encode.export_sequence"),
        (2, "Animated GIF Exporter", "encode.export_gif_from_files"),
        (3, "APNG Exporter", "encode.export_animation"),
        (4, "Animated WebP Exporter", "encode.export_webp"),
        (5, "MP4 Review Exporter", "encode.export_video (codec=h264)"),
        (6, "Master Video Exporter", "encode.export_video (codec=ffv1/prores)"),
        (7, "Transparent Video Export Planner", "encode.transparent_video_plan"),
        (8, "Format Compatibility Router", "encode.format_router"),
        (9, "Batch Export Orchestrator", "encode.run_batch"),
        (10, "Export Naming and Versioning Enforcer", "encode.export_name"),
        (11, "Encoder Availability Auditor", "encode.encoder_audit"),
        (12, "Delivery Package Builder", "encode.delivery_package"),
    ]),
    (21, "Validation, Quality Assurance, and Review", [
        (1, "Missing Frame Detector", "qa.missing_frames"),
        (2, "Duplicate Frame Detector", "qa.duplicate_frames"),
        (3, "Corrupt Frame Detector", "qa.corrupt_frames"),
        (4, "Alpha Channel Validator", "qa.alpha_validation"),
        (5, "Animation Loop Validator", "qa.loop_check"),
        (6, "Color Profile Validator", "qa.color_profile_check"),
        (7, "Resolution and Aspect Validator", "qa.resolution_check"),
        (8, "Frame Rate Metadata Validator", "qa.fps_metadata_check"),
        (9, "Sprite Pivot Validator", "assets.pivot_alignment_check"),
        (10, "Visual Regression Comparator", "qa.visual_regression"),
        (11, "Flicker and Flash Safety Checker", "qa.flash_safety"),
        (12, "Review Contact Sheet Builder", "qa.build_contact_sheet"),
        (13, "Playback Review Packager", "qa.review_package"),
        (14, "Acceptance Gate Runner", "qa.acceptance_gate, qa.final_delivery_gate"),
    ]),
    (22, "Automation, Scripting, and Reproducibility", [
        (1, "Reproducible Export Script Builder", "encode.generate_script"),
        (2, "Manifest-Driven Batch Processor", "encode.run_batch"),
        (3, "Headless Render Operator", "encode.headless_render"),
        (4, "Cross-Application Pipeline Adapter", "encode.pipeline_adapter"),
        (5, "Deterministic Cache Builder", "encode.cache_build"),
        (6, "Offline Job Queue", "encode.job_queue"),
        (7, "Workspace Relinker", "encode.relink_workspace"),
        (8, "Checksum and Integrity Reporter", "encode.checksum_report, encode.verify_checksums"),
        (9, "Script Safety Sandbox", "encode.sandbox_run"),
        (10, "Pipeline Smoke Test Builder", "encode.pipeline_smoke_test"),
        (11, "Failure Resume Manager", "encode.run_batch (resume), encode.job_queue (retry)"),
        (12, "Build Provenance Recorder", "encode.build_provenance"),
    ]),
    (23, "Licensing, Rights, Privacy, and Governance", [
        (1, "Asset License Classifier", "project.classify_asset"),
        (2, "Model Usage Rights Auditor", "project.rights_model"),
        (3, "Font Embedding and Redistribution Checker", "project.font_license"),
        (4, "Attribution File Generator", "project.attribution_file"),
        (5, "Provenance Metadata Manager", "project.rights_provenance"),
        (6, "Sensitive Reference Sanitizer", "project.sanitize_for_sharing"),
        (7, "Talent Consent Tracker", "project.consent_record"),
        (8, "Open-Source Obligation Packager", "project.oss_obligations"),
        (9, "Third-Party Asset Quarantine", "project.quarantine_assets"),
        (10, "Release Rights Gate", "project.release_rights_gate"),
    ]),
    (24, "Packaging, Archival, Recovery, and Handoff", [
        (1, "Clean Offline Project Bundle Builder", "project.handoff_package"),
        (2, "Bundle Completeness Validator", "project.pack_verify"),
        (3, "Cold-Start Rebuild Tester", "project.pack_coldstart"),
        (4, "Archive Tier Planner", "project.archive_tier_plan"),
        (5, "Long-Term Format Migrator", "assets.format_migration_plan"),
        (6, "Disaster Recovery Drill", "project.disaster_recovery_drill"),
        (7, "Incremental Backup Planner", "project.pack_backup"),
        (8, "Project Decommissioner", "project.decommission"),
        (9, "Recipient Handoff Guide Writer", "project.recipient_guide"),
        (10, "Offline Knowledge Packager", "project.pack_knowledge"),
    ]),
    (25, "Accessibility, Localization, and Alternate Deliverables", [
        (1, "Subtitle and Caption Timing Builder", "qa.build_subtitles"),
        (2, "Localized Text Replacement Planner", "project.access_text_zones"),
        (3, "Right-to-Left Layout Checker", "project.access_rtl_check"),
        (4, "Safe-Area and Legibility Checker", "qa.safe_area_check, qa.contrast_check"),
        (5, "Audio Description Cue Planner", "project.access_description_cues"),
        (6, "Reduced-Motion Variant Builder", "qa.reduced_motion_variant"),
        (7, "High-Contrast Variant Builder", "qa.high_contrast_variant"),
        (8, "Localization Package Exporter", "qa.localization_package"),
    ]),
    (26, "Specialized Workflows and Interchange", [
        (1, "Paper Scan and Registration Operator", "project.deskew, project.register_pages, project.remove_paper_tone"),
        (2, "Rotoscope Preparation Assistant", "project.rotoscope_prep"),
        (3, "Grease Pencil 2D-3D Hybrid Builder", "project.interchange_package (app=grease-pencil)"),
        (4, "OpenToonz Scene Exchange Manager", "project.interchange_package (app=opentoonz)"),
        (5, "Synfig Cutout Exchange Manager", "project.interchange_package (app=synfig)"),
        (6, "Krita Animation Exchange Manager", "project.interchange_package (app=krita)"),
        (7, "Aseprite Sprite Exchange Manager", "project.interchange_package (app=aseprite)"),
        (8, "Inkscape Vector Exchange Manager", "project.interchange_package (app=inkscape)"),
        (9, "Legacy Format Rescue Operator", "project.legacy_rescue"),
        (10, "2D-to-3D Reference Packager", "project.reference_pack_3d"),
        (11, "Print and Merchandise Output Planner", "project.print_plan"),
        (12, "Educational Exercise Generator", "project.educational_exercises"),
    ]),
]


# ----------------------------------------------------------------- plumbing --

def _tool_root():
    return os.path.dirname(os.path.abspath(__file__))


def _load_module(name):
    if name not in MODULES:
        raise _core().err("invalid_input",
                          "module must be one of: %s" % ", ".join(MODULES))
    import importlib.util
    key = "studio." + name
    mod = sys.modules.get(key)
    if mod is not None:
        return mod
    path = os.path.join(_tool_root(), "tools-%s.py" % name)
    if not os.path.isfile(path):
        raise _core().err("file_not_found", "missing module file: %s" % path)
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


_CORE = None


def _core():
    global _CORE
    if _CORE is None:
        _CORE = _load_module("core")
    return _CORE


def _emit(payload, out_path=None, compact=False):
    text = json.dumps(payload, indent=None if compact else 2,
                      default=_json_default, ensure_ascii=False)
    if out_path:
        _core().write_bytes(out_path, (text + "\n").encode("utf-8"))
    else:
        sys.stdout.write(text + "\n")


def _json_default(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _coerce(value):
    """CLI strings -> JSON-typed values when they look like JSON."""
    v = value.strip()
    if v.startswith("{") or v.startswith("["):
        try:
            return json.loads(v)
        except ValueError:
            return value
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return value


def _exit_code(err_obj):
    code = getattr(err_obj, "code", None) or "error"
    if code == "invalid_input" or code == "usage":
        return 2
    if code == "gate_blocked":
        return 4
    if code == "approval_required":
        return 5
    return 3


# ---------------------------------------------------------------- commands --

def cmd_help(argv):
    us = {
        "help": "this overview",
        "capability-list": "all categories/skills and the function that "
                           "implements each (--category N to filter, --json)",
        "info": "machine report; optional external app detection",
        "init-project": "--name NAME --root DIR [--fps 24] [--width 1920] "
                        "[--height 1080]: scaffold a production tree",
        "run": "run MODULE.FUNCTION key=value ... — call any tool function; "
               "values are parsed as JSON when they look like it "
               "(--out FILE writes the envelope, --compact one-line JSON)",
        "version": "tool name and version",
    }
    return {"tool": TOOL_NAME, "version": TOOL_VERSION,
            "python": sys.version.split()[0],
            "commands": us,
            "modules": list(MODULES),
            "conventions": {
                "output": "every command prints {'ok': true, 'result': ...} "
                          "or {'ok': false, 'error': {code, message}}",
                "errors": "structured codes: invalid_input, file_not_found, "
                          "limit_exceeded, encoder_unavailable, "
                          "runtime_not_configured, gate_blocked, "
                          "approval_required, ...",
                "safety": "destructive/irreversible/publishing actions need "
                          "explicit approval flags; subprocess runs are "
                          "bounded and shell-free; no secrets are embedded",
                "library use": "import tools-imaging etc. directly for "
                               "image-object pipelines; the CLI 'run' command "
                               "suits data-in/data-out functions",
            }}


def cmd_capability_list(argv):
    cat_filter = None
    as_json = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--category":
            i += 1
            if i >= len(argv):
                raise _core().err("invalid_input", "--category needs a number")
            cat_filter = int(_coerce(argv[i]))
        elif a == "--json":
            as_json = True
        i += 1
    cats = []
    total = 0
    for num, name, skills in CATEGORIES:
        if cat_filter is not None and num != cat_filter:
            continue
        cats.append({"category": num, "name": name,
                     "skills": [{"skill": n, "name": sname, "call": call}
                                for n, sname, call in skills]})
        total += len(skills)
    if not cats:
        raise _core().err("invalid_input",
                          "no category %r (range 1-26)" % cat_filter)
    if as_json:
        return {"categories": cats, "skills_listed": total,
                "skills_total": sum(len(s) for _, _, s in CATEGORIES),
                "categories_total": len(CATEGORIES)}
    lines = []
    for c in cats:
        lines.append("%2d. %s" % (c["category"], c["name"]))
        for s in c["skills"]:
            lines.append("     %2d.%-2d %-46s -> %s" %
                         (c["category"], s["skill"], s["name"][:46], s["call"]))
    return {"text": "\n".join(lines), "skills_listed": total,
            "skills_total": sum(len(s) for _, _, s in CATEGORIES),
            "categories_total": len(CATEGORIES)}


def cmd_info(argv):
    core = _core()
    rep = core.machine_report()
    detect = "--apps" in argv
    if detect:
        cfg = core.load_config()
        rep["apps"] = core.detect_apps(cfg)
    rep["tool"] = TOOL_NAME
    rep["version"] = TOOL_VERSION
    return rep


def cmd_init_project(argv):
    project = _load_module("project")
    core = _core()
    opts = _parse_opts(argv, {"--name": "name", "--root": "root",
                              "--fps": "fps", "--width": "width",
                              "--height": "height"})
    if "name" not in opts or "root" not in opts:
        raise core.err("invalid_input",
                       "init-project needs --name NAME --root DIR")
    res = [int(opts.get("width", 1920)), int(opts.get("height", 1080))]
    return project.project_init(opts["name"], opts["root"],
                                fps=int(opts.get("fps", 24)),
                                resolution=res)


def cmd_run(argv):
    core = _core()
    import inspect
    if not argv:
        raise core.err("invalid_input",
                       "run needs MODULE.FUNCTION [key=value ...]")
    target = argv[0]
    if "." not in target:
        raise core.err("invalid_input",
                       "target must look like module.function, got %r" % target)
    mod_name, fn_name = target.split(".", 1)
    mod = _load_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if fn is None or fn_name.startswith("_"):
        raise core.err("invalid_input", "no public function %s" % target)
    if not callable(fn):
        raise core.err("invalid_input", "%s is not callable" % target)
    kwargs = {}
    for tok in argv[1:]:
        if tok in ("--compact", "--out"):
            continue
        if "=" not in tok:
            raise core.err("invalid_input",
                           "arguments must be key=value pairs, got %r" % tok)
        k, v = tok.split("=", 1)
        kwargs[k] = _coerce(v)
    try:
        params = inspect.signature(fn).parameters
        bad = [k for k in kwargs if k not in params and
               not (params.get(k) is not None and
                    params[k].kind == inspect.Parameter.VAR_KEYWORD)]
        if bad:
            raise core.err(
                "invalid_input",
                "%s does not accept: %s (parameters: %s)" %
                (target, ", ".join(bad),
                 ", ".join(p for p in params)))
        result = fn(**kwargs)
    except core.StudioError:
        raise
    except TypeError as e:
        raise core.err("invalid_input", "%s call failed: %s" % (target, e))
    if isinstance(result, tuple) and len(result) == 2 and \
            isinstance(result[1], dict) and "svg" not in result[1]:
        result = result[0]
    return result


def _parse_opts(argv, spec):
    core = _core()
    opts = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in spec:
            i += 1
            if i >= len(argv):
                raise core.err("invalid_input", "%s needs a value" % a)
            opts[spec[a]] = argv[i]
        i += 1
    return opts


COMMANDS = {
    "help": cmd_help,
    "capability-list": cmd_capability_list,
    "capabilities": cmd_capability_list,
    "info": cmd_info,
    "init-project": cmd_init_project,
    "run": cmd_run,
    "version": lambda argv: {"tool": TOOL_NAME, "version": TOOL_VERSION},
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = None
    compact = False
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            sys.stdout.write(json.dumps(
                {"ok": False, "error": {"code": "invalid_input",
                                        "message": "--out needs a path"}}) + "\n")
            return 2
        out_path = argv[i + 1]
        del argv[i:i + 2]
    if "--compact" in argv:
        compact = True
        argv = [a for a in argv if a != "--compact"]
    if not argv:
        result = cmd_help([])
    else:
        cmd, rest = argv[0], argv[1:]
        handler = COMMANDS.get(cmd)
        if handler is None:
            _emit({"ok": False, "error": {
                "code": "invalid_input",
                "message": "unknown command %r (try 'help')" % cmd,
                "commands": sorted(COMMANDS)}}, out_path, compact)
            return 2
        try:
            result = handler(rest)
        except _core().StudioError as e:
            _emit({"ok": False, "error": {
                "code": getattr(e, "code", "error"),
                "message": str(e),
                "details": getattr(e, "details", None) or {}}}, out_path,
                compact)
            return _exit_code(e)
        except Exception as e:  # noqa: BLE001 - CLI boundary
            _emit({"ok": False, "error": {
                "code": "error", "message": "%s: %s" % (type(e).__name__, e)}},
                  out_path, compact)
            return 3
    _emit({"ok": True, "tool": TOOL_NAME, "version": TOOL_VERSION,
           "command": argv[0] if argv else "help", "result": result},
          out_path, compact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
