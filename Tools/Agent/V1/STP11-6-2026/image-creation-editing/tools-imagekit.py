#!/usr/bin/env python3
"""imagekit: single entry point for the image-creation-editing Skill Tool.

Run `python tools-imagekit.py capabilities` for the capability list,
`python tools-imagekit.py help` for usage, or
`python tools-imagekit.py <command> --help` for one command.

Every command writes real files under --out and prints a JSON envelope with
--json. Exit codes: 0 ok, 2 usage, 3 input, 4 approval required, 5 provider,
6 policy/quality gate, 130 cancelled.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL_VERSION = "1.0.0"

MODULE_FILES = [
    ("imagekit_core", "tools-imagekit-core.py"),
    ("imagekit_plan", "tools-imagekit-plan.py"),
    ("imagekit_render", "tools-imagekit-render.py"),
    ("imagekit_compose", "tools-imagekit-compose.py"),
    ("imagekit_edit", "tools-imagekit-edit.py"),
    ("imagekit_qa", "tools-imagekit-qa.py"),
    ("imagekit_library", "tools-imagekit-library.py"),
    ("imagekit_providers", "tools-imagekit-providers.py"),
    ("imagekit_pipeline", "tools-imagekit-pipeline.py"),
]


def load_modules():
    """Load sibling tool modules by path (filenames contain hyphens)."""
    loaded = {}
    for name, filename in MODULE_FILES:
        path = HERE / filename
        if not path.exists():
            print(f"FATAL: required tool module missing: {filename}", file=sys.stderr)
            raise SystemExit(1)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


MODULES = load_modules()
core = MODULES["imagekit_core"]
plan = MODULES["imagekit_plan"]
render = MODULES["imagekit_render"]
compose = MODULES["imagekit_compose"]
edit = MODULES["imagekit_edit"]
qa = MODULES["imagekit_qa"]
library = MODULES["imagekit_library"]
providers = MODULES["imagekit_providers"]
pipeline = MODULES["imagekit_pipeline"]


# --------------------------------------------------------------------------
# operation registry
# --------------------------------------------------------------------------

OPS = {
    # group, function, summary
    "brief": ("plan", plan.op_brief, "Interpret a request into a structured production brief."),
    "prompt": ("plan", plan.op_prompt, "Build a model-ready prompt with sections and negative constraints."),
    "variants": ("plan", plan.op_variants, "Produce distinct prompt variants and concept directions."),
    "spec": ("plan", plan.op_spec, "Build a delivery specification: size, colour, format, naming."),
    "composition": ("plan", plan.op_composition, "Plan focal hierarchy, safe zones and crop safety."),
    "direction": ("plan", plan.op_direction, "Create or audit an art direction document."),
    "styleguide": ("plan", plan.op_styleguide, "Build a brand, world, illustration or photography style guide."),
    "reference": ("plan", plan.op_reference, "Analyse references for palette, tone, texture and transfer parameters."),
    "palette": ("plan", plan.op_palette, "Develop accessible palettes with print equivalents and contrast pairs."),

    "render": ("make", render.op_render, "Render a deterministic reference composition from a preset grammar."),
    "icon": ("make", compose.op_icon, "Draw icon, logo and sticker marks as raster plus true vector SVG."),
    "pattern": ("make", compose.op_pattern, "Build seamless pattern and material tiles with a seam test."),
    "layout": ("make", compose.op_layout, "Compose typographic layouts with legibility and safe-zone checks."),
    "diagram": ("make", compose.op_diagram, "Draw flow, cycle, hierarchy, annotated and bar-chart figures."),
    "sheet": ("make", compose.op_sheet, "Assemble storyboards, comic panels and contact sheets."),
    "mockup": ("make", render.op_mockup, "Place artwork onto a product form with perspective bounds."),

    "edit": ("edit", edit.op_edit, "Crop, resize, rotate, flip, pad and extend images."),
    "retouch": ("edit", edit.op_retouch, "Heal, blur, sharpen, denoise or despeckle chosen regions."),
    "cutout": ("edit", edit.op_cutout, "Remove a background and emit a cut-out plus a matte."),
    "composite": ("edit", edit.op_composite, "Composite a cut-out subject over a background with shadow."),
    "grade": ("edit", edit.op_grade, "Grade colour, lighting, atmosphere and relight direction."),
    "restore": ("edit", edit.op_restore, "Restore and enhance with before/after metrics."),
    "convert": ("edit", edit.op_convert, "Convert format, mode, dpi and metadata, then verify the output."),
    "tile": ("edit", edit.op_tile, "Split oversized artwork into aligned panels with overlap."),
    "bleed": ("edit", edit.op_bleed, "Extend artwork to a bleed with trim and safe-area guides."),
    "cropset": ("edit", edit.op_cropset, "Produce subject-aware crops across many aspect ratios."),
    "gif": ("motion", edit.op_gif, "Build GIF, WebP or APNG animation from frames or a motion preset."),
    "sprite": ("motion", edit.op_sprite, "Build a sprite sheet plus an atlas JSON."),
    "frames": ("motion", edit.op_frames, "Extract, renumber and palette-lock animation frames."),

    "analyze": ("check", qa.op_analyze, "Analyse content, style and transformation boundaries."),
    "mask": ("check", qa.op_mask, "Emit saliency, depth-proxy and region maps."),
    "diff": ("check", qa.op_diff, "Compare two images with SSIM, PSNR, RMSE and a heatmap."),
    "qa": ("check", qa.op_gate, "Run quality gates against the policy file."),
    "trace": ("check", qa.op_trace, "Trace brief requirements against delivered files."),
    "verify": ("check", qa.op_verify, "Validate that every delivered file opens and matches expectations."),
    "safety": ("govern", qa.op_safety, "Review safety, consent, trademark and deception risk."),
    "provenance": ("govern", qa.op_provenance, "Write or verify provenance records."),
    "dashboard": ("govern", qa.op_dashboard, "Build a production dashboard from state and assets."),
    "library": ("manage", library.op_library, "Index, dedupe, name, version, hand off and preserve assets."),
    "pipeline": ("manage", None, "Run a JSON pipeline with checkpoints, retries and resume."),
    "provider": ("manage", providers.op_provider, "Inspect or call a configured external image provider."),
}

GROUPS = {
    "plan": "Plan and direct: briefs, prompts, specs, composition, art direction, palettes.",
    "make": "Make images: scene composition, marks, patterns, layouts, diagrams, sheets, mockups.",
    "edit": "Edit images: geometry, retouching, cutouts, compositing, grading, restoration, conversion.",
    "motion": "Motion preparation: animation, sprite sheets, frame handling.",
    "check": "Check work: analysis, masks, difference, quality gates, traceability, integrity.",
    "govern": "Govern: safety and rights review, provenance, production reporting.",
    "manage": "Manage: asset library, pipelines, provider adapters.",
}

REGISTRY = {name: entry[1] for name, entry in OPS.items() if entry[1] is not None}
REGISTRY["pipeline"] = lambda ctx, a: pipeline.op_pipeline(ctx, a, REGISTRY)


# --------------------------------------------------------------------------
# argument definitions
# --------------------------------------------------------------------------

C = argparse.SUPPRESS


def A(flag, **kw):
    return (flag, kw)


COMMON = [
    A("--out", dest="out", metavar="DIR", help="output directory (default: config paths.output_dir)"),
    A("--config", dest="config", metavar="FILE", help="JSON config overriding asset-config-default.json"),
    A("--json", dest="json_mode", action="store_true", help="print a structured JSON envelope"),
    A("--quiet", dest="quiet", action="store_true", help="only print output paths"),
    A("--verbose", dest="verbose", action="store_true", help="extra diagnostics on stderr"),
    A("--seed", dest="seed", type=int, help="deterministic seed for generated output"),
    A("--force", dest="force", action="store_true", help="allow overwriting existing outputs"),
    A("--approve", dest="approve", action="store_true", help="approve an action that changes or transmits files"),
    A("--approval-note", dest="approval_note", default="", metavar="TEXT", help="why the approved action is safe"),
    A("--dry-run", dest="dry_run", action="store_true", help="plan and validate without writing files"),
]

COMMAND_ARGS = {
    "brief": [A("--brief", metavar="TEXT", help="free-text request"), A("--input", metavar="FILE", help="brief file"),
              A("--project", metavar="NAME", help="project slug used in filenames")],
    "prompt": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--project", metavar="NAME"),
               A("--dialect", default="generic", choices=["generic", "diffusion", "chat"]),
               A("--emphasis", metavar="TEXT", help="extra emphasis appended to the prompt")],
    "variants": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--project", metavar="NAME"),
                 A("--count", type=int, default=6), A("--axes", metavar="LIST",
                 help="composition,lens,lighting,palette,medium,era,emphasis"),
                 A("--dialect", default="generic", choices=["generic", "diffusion", "chat"])],
    "spec": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"),
             A("--use", metavar="USE", help="key from asset-prompt-presets.json output_uses"),
             A("--size", metavar="WxH"), A("--format", metavar="EXT"), A("--transparent", action="store_true"),
             A("--dpi", type=int), A("--quality", type=int), A("--project", metavar="NAME"),
             A("--name", metavar="TEXT"), A("--version", type=int, default=1),
             A("--status", metavar="STATUS"), A("--max-kb", dest="max_kb", type=int)],
    "composition": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"),
                    A("--size", metavar="WxH"), A("--use", metavar="USE"), A("--focal", metavar="POINT"),
                    A("--horizon", type=float, default=0.62), A("--subjects", type=int, default=1),
                    A("--project", metavar="NAME")],
    "direction": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--project", metavar="NAME"),
                  A("--mode", default="create", choices=["create", "check"]),
                  A("--owner", metavar="NAME"), A("--direction", metavar="FILE", help="direction JSON for --mode check"),
                  A("--candidate", metavar="TEXT", help="candidate description to score")],
    "styleguide": [A("--kind", default="brand", choices=["brand", "world", "illustration", "photography"]),
                   A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--project", metavar="NAME"),
                   A("--colors", metavar="HEX,HEX")],
    "reference": [A("--refs", action="append", metavar="FILE", help="reference image (repeatable or comma separated)"),
                  A("--input", metavar="FILE"), A("--project", metavar="NAME")],
    "palette": [A("--brief", metavar="TEXT"), A("--colors", metavar="HEX,HEX"),
                A("--preset", metavar="NAME"), A("--input", metavar="FILE"),
                A("--strategy", default="analogous",
                  choices=["analogous", "complementary", "triadic", "split", "monochrome"]),
                A("--project", metavar="NAME")],

    "render": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--preset", metavar="PRESET"),
               A("--style", metavar="STYLE", help="alias such as photographic, poster, watercolour"),
               A("--size", metavar="WxH"), A("--palette", metavar="HEX,HEX"), A("--count", type=int, default=1),
               A("--format", metavar="EXT"), A("--quality", type=int), A("--project", metavar="NAME"),
               A("--name", metavar="TEXT"), A("--version", type=int, default=1), A("--status", metavar="STATUS"),
               A("--labels", metavar="LIST", help="labels for diagram-style presets"),
               A("--prompt", dest="prompt_text", metavar="TEXT", help="prompt text to record in provenance"),
               A("--no-disclosure", dest="no_disclosure", action="store_true",
                 help="omit the synthetic disclosure (only for internal tests)")],
    "icon": [A("--kind", default="app-icon", choices=["app-icon", "badge", "monogram", "logo-shield", "sticker", "glyph"]),
             A("--glyph", metavar="GLYPH", help="arrow, chart, person, leaf, cloud, shield, search, check, plus, star, play, document, clock, pin, gear, grid, abstract"),
             A("--palette", metavar="HEX,HEX"), A("--preset", metavar="NAME"), A("--sizes", metavar="LIST"),
             A("--stroke-scale", dest="stroke_scale", type=float, default=1.0), A("--opaque", action="store_true"),
             A("--project", metavar="NAME"), A("--name", metavar="TEXT")],
    "pattern": [A("--kind", default="dots", metavar="KIND"), A("--size", metavar="WxH"), A("--palette", metavar="HEX,HEX"),
                A("--preset", metavar="NAME"), A("--density", type=float, default=1.0),
                A("--project", metavar="NAME"), A("--name", metavar="TEXT")],
    "layout": [A("--kind", default="social", choices=list(compose.LAYOUT_KINDS)),
               A("--headline", metavar="TEXT"), A("--subhead", metavar="TEXT"), A("--cta", metavar="TEXT"),
               A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--size", metavar="WxH"),
               A("--palette", metavar="HEX,HEX"), A("--preset", metavar="NAME"), A("--background", metavar="HEX"),
               A("--text-color", dest="text_color", metavar="HEX"), A("--accent", metavar="HEX"),
               A("--image", metavar="FILE"), A("--scrim", type=float, default=0.55), A("--gradient", action="store_true"),
               A("--render-guides", dest="render_guides", action="store_true"), A("--format", metavar="EXT"),
               A("--project", metavar="NAME"), A("--name", metavar="TEXT"), A("--version", type=int, default=1),
               A("--status", metavar="STATUS")],
    "diagram": [A("--kind", default="flow", choices=["flow", "cycle", "hierarchy", "bar-chart", "annotated"]),
                A("--steps", metavar="LIST"), A("--data", metavar="LABEL:VALUE,..."), A("--title", metavar="TEXT"),
                A("--size", metavar="WxH"), A("--palette", metavar="HEX,HEX"), A("--background", metavar="HEX"),
                A("--note", metavar="TEXT"), A("--project", metavar="NAME"), A("--name", metavar="TEXT")],
    "sheet": [A("--kind", default="contact-sheet", choices=["contact-sheet", "storyboard", "comic"]),
              A("--inputs", action="append", metavar="LIST"), A("--input", metavar="FILE"),
              A("--columns", type=int), A("--cell-width", dest="cell_width", type=int),
              A("--aspect", type=float), A("--captions", metavar="A|B|C"), A("--background", metavar="HEX"),
              A("--project", metavar="NAME"), A("--name", metavar="TEXT")],
    "mockup": [A("--artwork", metavar="FILE"), A("--input", metavar="FILE"),
               A("--form", default="bottle", choices=["bottle", "box", "pouch", "cup", "poster", "phone", "tote"]),
               A("--preset", metavar="NAME", help="alias of --form"), A("--size", metavar="WxH"),
               A("--format", metavar="EXT"), A("--project", metavar="NAME"), A("--name", metavar="TEXT"),
               A("--version", type=int, default=1), A("--status", metavar="STATUS")],

    "edit": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
             A("--crop", metavar="x,y,w,h"), A("--size", metavar="WxH"),
             A("--fit", default="cover", choices=["cover", "contain", "stretch"]), A("--rotate", type=float),
             A("--flip", metavar="h|v"), A("--pad", type=int), A("--pad-color", dest="pad_color", metavar="HEX"),
             A("--max-dim", dest="max_dim", type=int), A("--auto-orient", dest="auto_orient", action="store_true"),
             A("--format", metavar="EXT"), A("--quality", type=int), A("--project", metavar="NAME")],
    "retouch": [A("--input", metavar="FILE"), A("--region", action="append", metavar="x,y,w,h"),
                A("--mode", default="heal", choices=["heal", "blur", "sharpen", "denoise", "clone", "despeckle"]),
                A("--strength", type=float, default=1.0), A("--feather", type=int), A("--threshold", type=int),
                A("--format", metavar="EXT"), A("--quality", type=int), A("--project", metavar="NAME")],
    "cutout": [A("--input", metavar="FILE"), A("--method", default="auto", choices=["auto", "chroma", "border", "color"]),
               A("--tolerance", type=int), A("--feather", type=int), A("--bg-color", dest="bg_color", metavar="HEX"),
               A("--format", metavar="EXT"), A("--project", metavar="NAME")],
    "composite": [A("--input", metavar="FILE"), A("--subject", dest="input", metavar="FILE",
                   help="cut-out PNG with alpha (alias of --input)"),
                  A("--background", metavar="FILE"), A("--bg-color", dest="bg_color", metavar="HEX"),
                  A("--size", metavar="WxH"), A("--scale", type=float, default=0.6), A("--position", metavar="POS"),
                  A("--no-shadow", dest="shadow", action="store_false"), A("--shadow-blur", dest="shadow_blur", type=int),
                  A("--shadow-opacity", dest="shadow_opacity", type=float), A("--format", metavar="EXT"),
                  A("--quality", type=int), A("--project", metavar="NAME"), A("--name", metavar="TEXT")],
    "grade": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
              A("--exposure", type=float), A("--contrast", type=float), A("--saturation", type=float),
              A("--temperature", type=float), A("--tint", type=float), A("--gamma", type=float),
              A("--lift", type=float), A("--vignette", type=float), A("--grain", type=float), A("--bloom", type=float),
              A("--relight", metavar="DIRECTION"), A("--relight-strength", dest="relight_strength", type=float, default=0.35),
              A("--format", metavar="EXT"), A("--quality", type=int), A("--project", metavar="NAME")],
    "restore": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
                A("--denoise", action="store_true"), A("--despeckle", action="store_true"),
                A("--autolevels", action="store_true"), A("--sharpen", type=float), A("--upscale", type=float),
                A("--format", metavar="EXT"), A("--quality", type=int), A("--project", metavar="NAME")],
    "convert": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
                A("--format", metavar="EXT"), A("--mode", metavar="MODE"), A("--quality", type=int),
                A("--dpi", type=int), A("--strip-metadata", dest="strip_metadata", action="store_true"),
                A("--bg-color", dest="bg_color", metavar="HEX"), A("--lossless", action="store_true"),
                A("--project", metavar="NAME")],
    "tile": [A("--input", metavar="FILE"), A("--columns", type=int), A("--rows", type=int),
             A("--overlap", type=int), A("--project", metavar="NAME")],
    "bleed": [A("--input", metavar="FILE"), A("--bleed", type=int), A("--dpi", type=int),
              A("--safe-margin", dest="safe_margin", type=float), A("--project", metavar="NAME")],
    "cropset": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
                A("--ratios", metavar="LIST"), A("--width", type=int), A("--project", metavar="NAME")],
    "gif": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
            A("--frames", type=int), A("--duration", type=int), A("--loop", type=int), A("--colors", type=int),
            A("--motion", default="zoom", choices=["pan", "zoom", "pulse", "fade", "rotate", "parallax"]),
            A("--direction", metavar="in|out"), A("--format", metavar="gif|webp|png"), A("--size", metavar="WxH"),
            A("--export-frames", dest="export_frames", type=int), A("--name", metavar="TEXT"),
            A("--project", metavar="NAME")],
    "sprite": [A("--inputs", action="append", metavar="LIST"), A("--input", metavar="FILE"),
               A("--frames", type=int, help="synthesise this many motion frames from one --input"),
               A("--motion", default="zoom", choices=["pan", "zoom", "pulse", "fade", "rotate", "parallax"]),
               A("--direction", metavar="in|out"), A("--size", metavar="WxH"),
               A("--frame-width", dest="frame_width", type=int), A("--frame-height", dest="frame_height", type=int),
               A("--columns", type=int), A("--duration", type=int), A("--name", metavar="TEXT"),
               A("--project", metavar="NAME")],
    "frames": [A("--input", metavar="FILE"), A("--lock-palette", dest="lock_palette", action="store_true"),
               A("--colors", type=int), A("--project", metavar="NAME")],

    "analyze": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
                A("--mask", action="store_true"), A("--stress", action="store_true"), A("--project", metavar="NAME")],
    "mask": [A("--input", metavar="FILE"), A("--kind", default="all", choices=["all", "saliency", "depth", "regions"]),
             A("--project", metavar="NAME")],
    "diff": [A("--left", metavar="FILE"), A("--right", metavar="FILE"), A("--project", metavar="NAME")],
    "qa": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
           A("--expect-size", dest="expect_size", metavar="WxH"), A("--expect-format", dest="expect_format", metavar="EXT"),
           A("--expect-transparent", dest="expect_transparent", action="store_true"),
           A("--expect-palette", dest="expect_palette", metavar="HEX,HEX"),
           A("--fail-on", dest="fail_on", default="never", choices=["never", "warn", "error"]),
           A("--project", metavar="NAME")],
    "trace": [A("--brief-file", dest="brief_file", metavar="FILE"), A("--input", metavar="FILE"),
              A("--inputs", action="append", metavar="LIST"), A("--project", metavar="NAME")],
    "verify": [A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
               A("--expect-size", dest="expect_size", metavar="WxH"), A("--project", metavar="NAME")],
    "safety": [A("--brief", metavar="TEXT"), A("--input", metavar="FILE"),
               A("--consent-subject", dest="consent_subject", metavar="NAME"),
               A("--consent-reference", dest="consent_reference", metavar="ID"),
               A("--consent-scope", dest="consent_scope", metavar="TEXT"),
               A("--project", metavar="NAME")],
    "provenance": [A("--mode", default="write", choices=["write", "read", "verify"]), A("--input", metavar="FILE"),
                   A("--inputs", action="append", metavar="LIST"), A("--creator", metavar="NAME"),
                   A("--lineage", metavar="LIST"), A("--rights", metavar="TEXT"), A("--disclosure", metavar="TEXT"),
                   A("--notes", metavar="TEXT"), A("--prompt", dest="prompt_text", metavar="TEXT"),
                   A("--project", metavar="NAME")],
    "dashboard": [A("--state", metavar="FILE"), A("--scan", metavar="DIR"), A("--gate-report", dest="gate_report", metavar="FILE"),
                  A("--library", metavar="FILE", help="path to a library index JSON"), A("--project", metavar="NAME")],
    "library": [A("--action", default="index",
                  choices=["index", "dedupe", "name", "versions", "rights", "gallery", "handoff", "usage",
                           "preserve", "master", "verify-handoff"]),
                A("--library", metavar="DIR"), A("--target", metavar="DIR"), A("--apply", action="store_true"),
                A("--publish", action="store_true"), A("--threshold", type=int), A("--tags", metavar="LIST"),
                A("--title", metavar="TEXT"), A("--window", type=int), A("--project", metavar="NAME")],
    "pipeline": [A("--plan", metavar="FILE"), A("--action", default="run", choices=["run", "validate"]),
                 A("--resume", action="store_true"), A("--workers", type=int, default=1)],
    "provider": [A("--action", default="list", choices=["list", "check", "generate"]), A("--name", metavar="PROVIDER"),
                 A("--prompt", dest="prompt_text", metavar="TEXT"), A("--negative", metavar="TEXT"),
                 A("--size", metavar="WxH"), A("--format", metavar="EXT"),
                 A("--preset", metavar="PRESET"), A("--style", metavar="STYLE"), A("--palette", metavar="HEX,HEX"),
                 A("--label", metavar="TEXT"), A("--project", metavar="NAME")],
}

GENERIC_SKILL_ARGS = [
    A("--brief", metavar="TEXT"), A("--input", metavar="FILE"), A("--inputs", action="append", metavar="LIST"),
    A("--project", metavar="NAME"), A("--name", metavar="TEXT"), A("--size", metavar="WxH"),
    A("--format", metavar="EXT"), A("--palette", metavar="HEX,HEX"), A("--preset", metavar="PRESET"),
    A("--style", metavar="STYLE"), A("--kind", metavar="KIND"), A("--action", metavar="ACTION"),
    A("--mode", metavar="MODE"), A("--use", metavar="USE"), A("--headline", metavar="TEXT"),
    A("--subhead", metavar="TEXT"), A("--cta", metavar="TEXT"), A("--title", metavar="TEXT"),
    A("--steps", metavar="LIST"), A("--data", metavar="LABEL:VALUE"), A("--colors", metavar="HEX,HEX"),
    A("--strategy", metavar="STRATEGY"), A("--ratios", metavar="LIST"), A("--region", action="append", metavar="x,y,w,h"),
    A("--count", type=int), A("--columns", type=int), A("--library", metavar="DIR"), A("--target", metavar="DIR"),
    A("--plan", metavar="FILE"), A("--brief-file", dest="brief_file", metavar="FILE"),
    A("--left", metavar="FILE"), A("--right", metavar="FILE"), A("--artwork", metavar="FILE"),
    A("--form", metavar="FORM"), A("--state", metavar="FILE"), A("--gate-report", dest="gate_report", metavar="FILE"),
    A("--background", metavar="FILE"), A("--image", metavar="FILE"), A("--glyph", metavar="GLYPH"),
    A("--sizes", metavar="LIST"), A("--captions", metavar="TEXT"), A("--subject", metavar="TEXT"),
    A("--dpi", type=int), A("--quality", type=int), A("--width", type=int), A("--bleed", type=int),
    A("--denoise", action="store_true"), A("--autolevels", action="store_true"), A("--despeckle", action="store_true"),
    A("--sharpen", type=float), A("--upscale", type=float), A("--mask", action="store_true"),
    A("--stress", action="store_true"), A("--apply", action="store_true"), A("--lock-palette",
      dest="lock_palette", action="store_true"),
]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-imagekit.py",
        description="image-creation-editing Skill Tool: create, edit, validate and manage image assets.",
        epilog="Start with: capabilities, skills, doctor, selftest, or help COMMAND.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"imagekit {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add_common(p):
        for flag, kw in COMMON:
            p.add_argument(flag, **kw)

    p = sub.add_parser("help", help="show overall help or one command's help")
    p.add_argument("topic", nargs="?", help="command name")

    p = sub.add_parser("capabilities", help="list every capability, operation and catalog mode")
    p.add_argument("--json", dest="json_mode", action="store_true")

    p = sub.add_parser("skills", help="list the 290 catalogued skill concepts and their mapped operation")
    p.add_argument("--category", type=int, metavar="N", help="filter to one catalog category (1-29)")
    p.add_argument("--search", metavar="TEXT", help="substring search over names and descriptions")
    p.add_argument("--mode", metavar="MODE", choices=["local", "assisted", "review", "provider"])
    p.add_argument("--json", dest="json_mode", action="store_true")

    p = sub.add_parser("skill", help="run the mapped operation for one catalog skill id (e.g. 7.4)")
    p.add_argument("skill_id", metavar="ID")
    for flag, kw in GENERIC_SKILL_ARGS:
        p.add_argument(flag, **kw)
    add_common(p)

    p = sub.add_parser("doctor", help="check runtime, formats, fonts and provider readiness")
    p.add_argument("--json", dest="json_mode", action="store_true")
    for flag, kw in [x for x in COMMON if x[0] in ("--out", "--config", "--verbose")]:
        p.add_argument(flag, **kw)

    p = sub.add_parser("selftest", help="run the bundled verification suite")
    p.add_argument("--suite", default="all", choices=["all", "fast", "policy"],
                   help="which group of checks to run")
    p.add_argument("--keep", action="store_true", help="keep the temporary test workspace")
    p.add_argument("--json", dest="json_mode", action="store_true")
    p.add_argument("--out", dest="out", metavar="DIR")

    for name, (group, _fn, summary) in OPS.items():
        if name in ("pipeline", "provider"):
            pass
        p = sub.add_parser(name, help=summary, description=summary)
        for flag, kw in COMMAND_ARGS.get(name, []):
            p.add_argument(flag, **kw)
        add_common(p)
    return parser


# --------------------------------------------------------------------------
# context + dispatch
# --------------------------------------------------------------------------


def make_context(args):
    config = core.load_config(getattr(args, "config", None))
    ctx = core.Context(
        config,
        out_dir=getattr(args, "out", None),
        json_mode=bool(getattr(args, "json_mode", False)),
        quiet=bool(getattr(args, "quiet", False)),
        seed=getattr(args, "seed", None),
        force=bool(getattr(args, "force", False)),
        approve=bool(getattr(args, "approve", False)),
        approval_note=getattr(args, "approval_note", "") or "",
        verbose=bool(getattr(args, "verbose", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    return ctx


def install_signal_handler(ctx):
    def handler(signum, frame):
        ctx.cancelled = True
        print("\n[imagekit] cancellation requested; finishing the current step safely.", file=sys.stderr)

    try:
        signal.signal(signal.SIGINT, handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):
        pass


def envelope(ok, command, data=None, outputs=None, warnings=None, error=None, ctx=None, started=None):
    payload = {
        "ok": ok,
        "tool": "image-creation-editing",
        "version": TOOL_VERSION,
        "command": command,
        "outputs": outputs or [],
        "data": data if data is not None else {},
        "warnings": warnings or [],
    }
    if error:
        payload["error"] = error
    if ctx is not None:
        payload["out_root"] = str(ctx.out_root)
        payload["approvals"] = [a["action"] for a in ctx._approvals]
        payload["audit_events"] = len(ctx.audit_events)
    if started is not None:
        payload["seconds"] = round(time.time() - started, 3)
    return payload


def describe_outputs(ctx, outputs):
    rows = []
    for p in outputs or []:
        try:
            size = Path(p).stat().st_size
        except OSError:
            size = 0
        rows.append({"path": core.rel_posix(p, ctx.out_root), "bytes": size,
                     "written": Path(p).exists()})
    return rows


def print_human(ctx, command, result, elapsed):
    data = result.get("data") or {}
    if ctx.quiet:
        for row in describe_outputs(ctx, result.get("outputs")):
            print(row["path"])
        return
    marker = " (dry run)" if ctx.dry_run else ""
    print(f"[imagekit] {command} ok in {elapsed:.2f}s{marker} -> {core.rel_posix(ctx.out_root)}/")
    summary = summarise(data)
    if summary:
        print(summary)
    for warn in result.get("warnings") or []:
        print(f"  ! {warn}")
    for row in describe_outputs(ctx, result.get("outputs")):
        if row["written"]:
            print(f"  + {row['path']} ({core.human_bytes(row['bytes'])})")
        else:
            print(f"  + {row['path']} (dry run, not written)")


def summarise(data):
    if not isinstance(data, dict):
        return ""
    lines = []
    for key in ("verdict", "risk", "status", "preset", "preset_label", "size", "format", "seed",
                "count", "operation", "assets", "mode", "level", "project", "palette", "frames"):
        if key in data and isinstance(data[key], (str, int, float, list)) and str(data[key]) not in ("", "[]"):
            value = data[key]
            if isinstance(value, list) and len(value) > 6:
                value = value[:6] + ["..."]
            lines.append(f"  {key}: {value}")
    if "brief" in data and isinstance(data["brief"], dict):
        brief = data["brief"]
        lines.append(f"  subject: {brief.get('subject')}")
        if brief.get("open_questions"):
            lines.append(f"  open questions: {len(brief['open_questions'])}")
    if "failures" in data and data["failures"]:
        lines.append(f"  failures: {data['failures'][:4]}")
    return "\n".join(lines[:10])


def dispatch(command, args, ctx):
    if command not in REGISTRY:
        raise core.usage_error(f"Unknown command '{command}'",
                               "Run 'tools-imagekit.py capabilities' for the command list.")
    return REGISTRY[command](ctx, args)


# --------------------------------------------------------------------------
# meta commands
# --------------------------------------------------------------------------


def cmd_capabilities(args):
    catalog = core.read_json(POLICY_CATALOG(), required=True)
    if getattr(args, "json_mode", False):
        payload = {
            "tool": "image-creation-editing",
            "version": TOOL_VERSION,
            "catalog": {"categories": len(catalog["categories"]),
                        "skill_concepts": sum(len(c["skills"]) for c in catalog["categories"]),
                        "mode_counts": catalog.get("mode_counts")},
            "groups": GROUPS,
            "operations": [{"name": name, "group": entry[0], "summary": entry[2],
                            "modes_available": True} for name, entry in OPS.items()],
        }
        print(json.dumps(payload, indent=2))
        return core.EXIT_OK
    total = sum(len(c["skills"]) for c in catalog["categories"])
    print(f"image-creation-editing Skill Tool v{TOOL_VERSION}")
    print(f"{len(catalog['categories'])} catalog categories, {total} skill concepts, "
          f"{len(OPS)} operations\n")
    for group, label in GROUPS.items():
        print(f"{group.upper()} — {label}")
        for name, entry in OPS.items():
            if entry[0] == group:
                print(f"  {name:<12} {entry[2] if entry[1] else 'Run a JSON pipeline with checkpoints, retries and resume.'}")
        print()
    print("Engine: deterministic reference compositor + local Pillow imaging. "
          "Photoreal synthesis requires a configured provider (see 'provider').")
    print(f"Catalog mode mix: {catalog.get('mode_counts')}")
    print("Run 'skills --search TEXT' to find a catalog concept and its mapped command.")
    return core.EXIT_OK


def POLICY_CATALOG():
    return core.load_config().catalog_path()


def cmd_skills(args):
    catalog = core.read_json(core.load_config().catalog_path(), required=True)
    cats = catalog["categories"]
    if getattr(args, "category", None):
        cats = [c for c in cats if c["number"] == args.category]
        if not cats:
            raise core.usage_error(f"No catalog category {args.category} (valid: 1-29)")
    search = (getattr(args, "search", None) or "").lower()
    mode = getattr(args, "mode", None)
    rows = []
    for c in cats:
        for s in c["skills"]:
            if search and search not in (s["name"] + " " + s["description"] + " " + s["operation"]).lower():
                continue
            if mode and s["mode"] != mode:
                continue
            rows.append({"category": f"{c['number']}. {c['name']}", **s})
    if getattr(args, "json_mode", False):
        print(json.dumps({"count": len(rows), "skills": rows}, indent=2))
        return core.EXIT_OK
    if not rows:
        print("No matching skill concepts.")
        return core.EXIT_OK
    current = None
    for row in rows:
        if row["category"] != current:
            current = row["category"]
            print(f"\n{current}")
        print(f"  {row['id']:<5} {row['name'][:44]:<44} -> {row['operation']:<11} [{row['mode']}]")
    print(f"\n{len(rows)} concepts listed. Run 'skill ID' (e.g. skill 7.4) to execute the mapped operation.")
    return core.EXIT_OK


def cmd_skill(args, ctx):
    catalog = core.read_json(ctx.config.catalog_path(), required=True)
    entry = None
    for c in catalog["categories"]:
        for s in c["skills"]:
            if s["id"] == args.skill_id:
                entry = {"category": c["name"], **s}
                break
    if entry is None:
        raise core.usage_error(f"Unknown skill id '{args.skill_id}'",
                               "Use the ids shown by 'skills', for example 7.4.")
    op = entry["operation"]
    if op not in REGISTRY:
        raise core.usage_error(f"Skill {entry['id']} maps to operation '{op}', which is not registered.")
    print(f"[imagekit] skill {entry['id']} {entry['name']} -> {op} ({entry['mode']})", file=sys.stderr)
    if entry["mode"] in ("review",):
        print("[imagekit] note: this capability produces review material; a human makes the call.",
              file=sys.stderr)
    if entry["mode"] == "provider":
        print("[imagekit] note: this capability needs a configured provider; see 'provider --action list'.",
              file=sys.stderr)
    try:
        return REGISTRY[op](ctx, args)
    except core.ToolError as exc:
        if exc.exit_code == core.EXIT_USAGE:
            exc.hint = (exc.hint + " " if exc.hint else "") + f"Suggested form: {entry['command']}"
        raise


def cmd_doctor(args):
    checks = []
    py_ok = sys.version_info >= (3, 9)
    checks.append({"check": "python_version", "value": sys.version.split()[0], "pass": py_ok,
                   "note": "3.9 or newer required"})
    try:
        import PIL
        checks.append({"check": "pillow", "value": PIL.__version__, "pass": True})
    except ImportError:
        checks.append({"check": "pillow", "value": None, "pass": False,
                       "note": "install with: python -m pip install -r tools-requirements.txt"})
    writable = False
    note = ""
    try:
        probe = core.ensure_dir(Path(getattr(args, "out", None) or "output") / ".probe")
        probe.rmdir()
        probe.parent.rmdir()
        writable = True
    except OSError as exc:
        note = str(exc)
    checks.append({"check": "output_writable", "value": bool(writable), "pass": writable, "note": note})
    checks.append({"check": "scalable_font", "value": core.has_scalable_font(),
                   "pass": bool(core.has_scalable_font()),
                   "note": "set IMAGEKIT_FONT to a .ttf/.otf path if no system font is found"})
    formats = {}
    if py_ok:
        from PIL import Image
        for ext, fmt in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP"), ("tiff", "TIFF"),
                         ("gif", "GIF"), ("avif", "AVIF"), ("bmp", "BMP")):
            try:
                buf = __import__("io").BytesIO()
                Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format=fmt)
                formats[ext] = len(buf.getvalue()) > 0
            except Exception:
                formats[ext] = False
    checks.append({"check": "write_formats", "value": formats, "pass": any(formats.values())})
    try:
        catalog = core.read_json(core.load_config().catalog_path(), required=True)
        count = sum(len(c["skills"]) for c in catalog["categories"])
        checks.append({"check": "catalog", "value": count, "pass": count == 290})
    except Exception as exc:
        checks.append({"check": "catalog", "value": str(exc), "pass": False})
    try:
        core.load_policy(core.load_config())
        checks.append({"check": "policy_file", "value": "loaded", "pass": True})
    except Exception as exc:
        checks.append({"check": "policy_file", "value": str(exc), "pass": False})
    cfg = core.load_config(getattr(args, "config", None))
    provider_rows = []
    for name, pcfg in (cfg.providers.get("list") or {}).items():
        adapter = providers.get_adapter(name, core.Context(cfg, out_dir=getattr(args, "out", None)))
        provider_rows.append(adapter.check() | {"name": name})
    ok = all(c["pass"] for c in checks if c["check"] != "scalable_font")
    if getattr(args, "json_mode", False):
        print(json.dumps({"ok": ok, "checks": checks, "providers": provider_rows,
                          "allow_network": bool(cfg.providers.get("allow_network"))}, indent=2))
    else:
        print("imagekit doctor\n")
        for c in checks:
            print(f"  [{'ok ' if c['pass'] else 'FAIL'}] {c['check']}: {c['value']}"
                  + (f"  ({c['note']})" if c.get("note") else ""))
        print("\nproviders (all optional; the tool works offline):")
        for row in provider_rows:
            state = "ready" if row.get("ok") else "not configured"
            print(f"  [{'ok ' if row.get('ok') else '---'}] {row['name']}: {state}")
            for problem in row.get("problems") or []:
                print(f"        - {problem}")
        print("\nnetwork is disabled for providers unless providers.allow_network is true in config")
    return core.EXIT_OK if ok else core.EXIT_INTERNAL


def cmd_selftest(args):
    path = HERE / "tools-imagekit-tests.py"
    if not path.exists():
        print("selftest module missing: tools-imagekit-tests.py", file=sys.stderr)
        return core.EXIT_INTERNAL
    spec = importlib.util.spec_from_file_location("imagekit_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_suite(out_dir=getattr(args, "out", None), keep=bool(getattr(args, "keep", False)),
                            suite=getattr(args, "suite", "all"), json_mode=bool(getattr(args, "json_mode", False)))


def cmd_help(args):
    parser = build_parser()
    topic = getattr(args, "topic", None)
    if not topic:
        parser.print_help()
        print("\nQuick start")
        print("  python tools-imagekit.py capabilities")
        print("  python tools-imagekit.py skills --search poster")
        print("  python tools-imagekit.py brief --brief \"soft studio photo of a serum bottle\" --json")
        print("  python tools-imagekit.py render --preset photo-studio --size 1024x1024 --out out")
        print("  python tools-imagekit.py selftest")
        return core.EXIT_OK
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and topic in action.choices:
            action.choices[topic].print_help()
            return core.EXIT_OK
    print(f"No such command: {topic}", file=sys.stderr)
    return core.EXIT_USAGE


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command is None:
        return cmd_help(args)
    if command == "help":
        return cmd_help(args)
    if command == "capabilities":
        return cmd_capabilities(args)
    if command == "skills":
        return cmd_skills(args)
    if command == "doctor":
        return cmd_doctor(args)
    if command == "selftest":
        return cmd_selftest(args)

    started = time.time()
    json_mode = bool(getattr(args, "json_mode", False))
    ctx = None
    try:
        ctx = make_context(args)
        install_signal_handler(ctx)
        if command == "skill":
            result = cmd_skill(args, ctx)
        else:
            result = dispatch(command, args, ctx)
        outputs = describe_outputs(ctx, result.get("outputs"))
        if command == "qa" and getattr(args, "fail_on", "never") != "never":
            verdict = (result.get("data") or {}).get("verdict")
            if verdict == "error" or (verdict == "warn" and args.fail_on == "warn"):
                if json_mode:
                    print(json.dumps(envelope(False, command, result.get("data"), outputs,
                                              result.get("warnings"), ctx=ctx, started=started,
                                              error={"code": "quality_gate", "message": f"verdict {verdict}",
                                                     "hint": "Fix the failing checks or lower --fail-on."}), indent=2))
                else:
                    print_human(ctx, command, result, time.time() - started)
                    print(f"[imagekit] quality gate failed (verdict {verdict})", file=sys.stderr)
                return core.EXIT_POLICY
        cancelled = bool(getattr(ctx, "cancelled", False)) or (
            isinstance(result.get("data"), dict) and result["data"].get("status") == "cancelled")
        if json_mode:
            print(json.dumps(envelope(True, command, result.get("data"), outputs,
                                      result.get("warnings"), ctx=ctx, started=started), indent=2))
        else:
            print_human(ctx, command, result, time.time() - started)
            if cancelled:
                print("[imagekit] cancelled: state saved, re-run with --resume to continue",
                      file=sys.stderr)
        return core.EXIT_CANCELLED if cancelled else core.EXIT_OK
    except core.ToolError as exc:
        if json_mode:
            print(json.dumps({"ok": False, "tool": "image-creation-editing", "version": TOOL_VERSION,
                              "command": command, "error": exc.as_dict(),
                              "seconds": round(time.time() - started, 3)}, indent=2))
        else:
            print(f"[imagekit] {exc.code}: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"  hint: {exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("[imagekit] cancelled by user", file=sys.stderr)
        return core.EXIT_CANCELLED
    except Exception as exc:  # unexpected: never leak a traceback in json mode
        if json_mode:
            print(json.dumps({"ok": False, "command": command,
                              "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}",
                                        "hint": "Re-run with --verbose for a traceback."}}, indent=2))
        else:
            print(f"[imagekit] internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(traceback.format_exc(limit=6), file=sys.stderr)
        return core.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
