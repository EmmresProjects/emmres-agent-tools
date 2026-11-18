"""imagekit tests: the bundled verification suite behind `selftest`.

Every check builds real files, asserts on real output, and deletes its own
workspace unless --keep is passed. No network access is used anywhere in this
file; provider checks use the offline mock adapter only.

Suites
  fast    ~35 checks covering planning, composition, editing, QA, governance,
          library, pipeline recovery, approval gates and exit codes.
  policy  the policy, safety and disclosure rules on their own.
  all     fast + policy (default).

Run directly through the dispatcher:
  python tools-imagekit.py selftest
  python tools-imagekit.py selftest --suite fast --json
  python tools-imagekit.py selftest --suite all --keep --out ./selftest-out

Exit codes: 0 all checks passed, 6 at least one check failed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPLAY_ROOT = Path(os.environ.get("IMAGEKIT_DISPLAY_ROOT", "") or HERE)


def _dispatch_ops():
    """Command names the primary entry point registers, read without importing it."""
    text = (HERE / "tools-imagekit.py").read_text(encoding="utf-8")
    block = text.split("OPS = {", 1)[1].split("\n}", 1)[0]
    names = set()
    for line in block.splitlines():
        line = line.strip()
        if line.startswith('"') and '":' in line:
            names.add(line.split('"')[1])
    return names


DISPATCH_OPS = _dispatch_ops()


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load("imagekit_core", "tools-imagekit-core.py")
plan = load("imagekit_plan", "tools-imagekit-plan.py")
render = load("imagekit_render", "tools-imagekit-render.py")
compose = load("imagekit_compose", "tools-imagekit-compose.py")
edit = load("imagekit_edit", "tools-imagekit-edit.py")
qa = load("imagekit_qa", "tools-imagekit-qa.py")
library = load("imagekit_library", "tools-imagekit-library.py")
providers = load("imagekit_providers", "tools-imagekit-providers.py")
pipeline = load("imagekit_pipeline", "tools-imagekit-pipeline.py")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


class CheckFailed(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def ns(**kwargs):
    """argparse-like namespace: every unset flag reads as None."""
    base = argparse.Namespace(**kwargs)
    return base


def ctx_for(out_dir, **kwargs):
    config = core.load_config(None)
    options = dict(out_dir=str(out_dir), json_mode=True, quiet=True)
    options.update(kwargs)
    return core.Context(config, **options)


def run_check(results, name, fn, suite_tag="fast"):
    started = time.time()
    record = {"name": name, "suite": suite_tag, "status": "ok", "seconds": 0.0}
    try:
        detail = fn() or {}
        record["detail"] = detail
    except CheckFailed as exc:
        record["status"] = "fail"
        record["detail"] = str(exc)
    except core.ToolError as exc:
        record["status"] = "fail"
        record["detail"] = f"{exc.code}: {exc.message}"
    except Exception as exc:  # noqa: BLE001 - a suite must never abort the run
        record["status"] = "fail"
        record["detail"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc(limit=3)
    record["seconds"] = round(time.time() - started, 3)
    results.append(record)
    return record


def expect_tool_error(code, fn, label):
    try:
        fn()
    except core.ToolError as exc:
        require(exc.code == code, f"{label}: expected error code '{code}', got '{exc.code}' ({exc.message})")
        return {"error_code": exc.code, "exit": exc.exit_code}
    raise CheckFailed(f"{label}: expected a {code} error, but the call succeeded")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def make_source_image(path, size=(420, 300)):
    """Flat-ish synthetic source used by the editing checks."""
    Image = core._pillow()
    img = Image.new("RGB", size, (238, 232, 220))
    d = Image.core_draw(img) if hasattr(Image, "core_draw") else None
    from PIL import ImageDraw, ImageFilter
    d = ImageDraw.Draw(img)
    d.ellipse([size[0] * 0.28, size[1] * 0.18, size[0] * 0.72, size[1] * 0.82], fill=(196, 122, 66))
    d.rectangle([size[0] * 0.36, size[1] * 0.1, size[0] * 0.64, size[1] * 0.25], fill=(120, 74, 44))
    d.rectangle([size[0] * 0.4, size[1] * 0.45, size[0] * 0.6, size[1] * 0.62], fill=(250, 246, 236))
    img = img.filter(ImageFilter.SMOOTH)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def make_cutout_source(path, size=(420, 300)):
    """White background plus one solid subject: a fair test for cutout."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([size[0] * 0.3, size[1] * 0.2, size[0] * 0.7, size[1] * 0.85],
                        radius=int(size[0] * 0.06), fill=(64, 96, 132))
    d.ellipse([size[0] * 0.42, size[1] * 0.06, size[0] * 0.58, size[1] * 0.24], fill=(40, 60, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


# --------------------------------------------------------------------------
# fast suite
# --------------------------------------------------------------------------


def check_catalog():
    catalog = core.read_json(HERE / "asset-skill-catalog.json", required=True)
    require(len(catalog["categories"]) == 29, f"expected 29 categories, found {len(catalog['categories'])}")
    ops = set(catalog["operation_index"])
    require(len(ops) >= 41, f"expected at least 41 mapped operations, found {len(ops)}")
    skills = [item for cat in catalog["categories"] for item in cat["skills"]]
    require(len(skills) == 290, f"expected 290 skills, found {len(skills)}")
    modes = catalog["mode_counts"]
    require(sum(modes.values()) == 290, f"mode counts do not sum to 290: {modes}")
    for item in skills:
        require(item["operation"] in ops, f"skill {item['id']} maps to unknown op {item['operation']}")
        require(bool(item["command"]), f"skill {item['id']} has no example command")
        require(item["operation"] in DISPATCH_OPS, f"skill {item['id']} maps to a command that does not exist")
    return {"skills": len(skills), "categories": len(catalog["categories"]),
            "operations": len(ops), "modes": modes}


def check_assets():
    presets = core.read_json(HERE / "asset-render-presets.json", required=True)
    require(len(presets["presets"]) >= 24, "render presets below 24")
    policy = core.read_json(HERE / "asset-quality-policy.json", required=True)
    require(policy["qa"]["resolution"], "quality policy has no resolution thresholds")
    require(policy["qa"]["gates"], "quality policy has no gate groups")
    prompts = core.read_json(HERE / "asset-prompt-presets.json", required=True)
    require(len(prompts["palettes"]) >= 10, "prompt palettes below 10")
    brief = core.read_json(HERE / "asset-brief-template.json", required=True)
    require(brief["fields"] or brief.get("schema"), "brief template has no fields")
    config = core.read_json(HERE / "asset-config-default.json", required=True)
    require(config["providers"]["list"], "config has no provider list")
    return {"presets": len(presets["presets"]), "gate_groups": len(policy["qa"]["gates"]),
            "prompt_groups": len(prompts), "brief_keys": len(brief)}


def check_planning(work):
    project = "selftest"
    ctx = ctx_for(work / "plan")
    brief = plan.op_brief(ctx, ns(brief="Soft studio photo of an amber serum bottle for a "
                                         "skincare landing page hero, 1600x900, calm light",
                                  input=None, project=project))
    data = brief["data"]["brief"] if isinstance(brief["data"].get("brief"), dict) else brief["data"]
    require(data["subject"], "brief produced no subject")
    require(data["dimensions"] in ("1600x900", None) or "1600" in str(data["dimensions"]),
            f"brief missed the dimensions: {data['dimensions']}")
    prompt = plan.op_prompt(ctx, ns(brief="A cinematic fantasy market at dusk with warm lantern light",
                                    input=None, project=project, dialect="diffusion", emphasis=None))
    prompt_data = prompt["data"]
    require(isinstance(prompt_data["prompt"], str) and len(prompt_data["prompt"]) > 80,
            "prompt text is missing or too short")
    require(len(prompt_data["sections"]) >= 3, "prompt has fewer than 3 sections")
    require(prompt_data["negative"], "prompt has no negative constraints")
    variants = plan.op_variants(ctx, ns(brief="A cinematic fantasy market at dusk", input=None,
                                        project=project, count=4, axes=None, dialect="generic"))
    require(variants["data"]["variants"] == 4, "variants did not honour --count")
    require(len(variants["data"]["concepts"]) == 4, "variants produced the wrong number of concepts")
    palette = plan.op_palette(ctx, ns(brief="warm amber skincare", colors=None, preset=None,
                                      input=None, strategy="analogous", project=project))
    pairs = palette["data"]["contrast_pairs"]
    require(pairs, "palette produced no contrast pairs")
    require(all("ratio" in p for p in pairs), "contrast pairs are missing ratios")
    spec = plan.op_spec(ctx, ns(brief="product hero for a skincare brand", input=None, use="web-hero",
                                size="1600x900", format="jpg", transparent=False, dpi=72,
                                quality=88, project=project, name=None, version=1, status=None))
    spec_size = spec["data"]["size"]
    require((spec_size["width"], spec_size["height"]) == (1600, 900),
            f"spec did not keep the requested size: {spec_size}")
    composition = plan.op_composition(ctx, ns(brief="product hero", input=None, size="1200x628",
                                              use="social-wide", focal=None, horizon=0.62,
                                              subjects=1, project=project))
    composition_data = composition["data"]
    require(composition_data.get("type_safe_zone"), "composition produced no type safe zone")
    require(len(composition_data.get("grid", {}).get("thirds", [])) == 4,
            "composition grid is missing the rule-of-thirds intersections")
    return {"brief_files": len(brief["outputs"]), "variants": 4,
            "contrast_pairs": len(pairs), "spec": f"{spec_size['width']}x{spec_size['height']}"}


def check_render(work):
    ctx = ctx_for(work / "render")
    sizes = {}
    for preset, size in (("photo-studio", "320x320"), ("line-art", "320x240"),
                         ("fictional-map", "300x300")):
        started = time.time()
        out = render.op_render(ctx, ns(preset=preset, size=size, seed=11, project="selftest",
                                       name=preset, palette=None, labels=None, style=None,
                                       brief=None, input=None, format="png", count=1,
                                       transparent=False))
        path = out["outputs"][0]
        require(path.exists(), f"{preset}: no file written")
        info = core.image_info(path)
        require(info["width"] == int(size.split("x")[0]), f"{preset}: wrong width")
        require(info["bytes"] > 4000, f"{preset}: file suspiciously small ({info['bytes']} bytes)")
        require(path.with_suffix(path.suffix + ".provenance.json").exists(),
                f"{preset}: provenance sidecar missing")
        sizes[preset] = round(time.time() - started, 2)
    require(all(v < 12 for v in sizes.values()), f"render too slow: {sizes}")
    return {"seconds": sizes}


def check_marks(work):
    ctx = ctx_for(work / "marks")
    icon = compose.op_icon(ctx, ns(kind="app-icon", glyph="leaf", palette="#0f766e,#99f6e4,#134e4a",
                                   preset=None, sizes="256,64,32", stroke_scale=1.0, opaque=False,
                                   project="selftest", name="leaf"))
    pngs = [p for p in icon["outputs"] if p.suffix == ".png"]
    svg = [p for p in icon["outputs"] if p.suffix == ".svg"]
    require(len(pngs) == 3, f"icon produced {len(pngs)} raster sizes, expected 3")
    require(svg and svg[0].read_text().lstrip().startswith("<svg"), "icon SVG is not real vector output")
    sizes = sorted(core.image_info(p)["width"] for p in pngs)
    require(sizes == [32, 64, 256], f"icon sizes wrong: {sizes}")
    report = core.read_json([p for p in icon["outputs"] if p.suffix == ".json"][0], required=True)
    require("contrast" in report or "palette" in report, "icon report has no palette/contrast data")

    pattern = compose.op_pattern(ctx, ns(kind="terrazzo", size="256x256", palette=None,
                                         preset=None, density=1.0, project="selftest", name=None))
    tile = [p for p in pattern["outputs"] if p.name.endswith("-tile-256x256-v01-final.png")][0]
    require(core.image_info(tile)["width"] == 256, "pattern tile has the wrong width")
    with __import__("PIL.Image", fromlist=["Image"]).open(tile) as img:
        left = [img.convert("RGB").getpixel((0, y)) for y in range(0, 256, 16)]
        right = [img.convert("RGB").getpixel((255, y)) for y in range(0, 256, 16)]
    spread = max(sum(abs(a - b) for a, b in zip(l, r)) for l, r in zip(left, right))
    require(spread < 260, f"tile edges differ too much to be seamless (worst {spread})")
    seam = pattern["data"].get("seam") or {}
    require(seam, "pattern report has no seam report")
    require(seam.get("verdict") in ("seamless", "pass"), f"seam verdict: {seam}")
    return {"icon_sizes": sizes, "seam": seam.get("verdict")}


def check_layout_and_diagram(work):
    ctx = ctx_for(work / "layout")
    layout = compose.op_layout(ctx, ns(kind="social", headline="Hydration that holds",
                                       subhead="New serum, 30 ml", cta="Shop now", brief=None,
                                       input=None, size="1080x1080", palette=None, preset=None,
                                       background=None, text_color=None, accent=None, image=None,
                                       scrim=0.55, gradient=True, render_guides=False, format="png",
                                       project="selftest", name="social", version=1, status=None))
    legibility = layout["data"].get("legibility") or []
    require(legibility, "layout returned no legibility result")
    headline_row = next(r for r in legibility if r["role"] == "headline")
    require(headline_row["contrast"] > 4.5, f"headline contrast below WCAG AA: {headline_row}")
    require(headline_row["wcag_aa"], "layout did not assert WCAG AA on the headline")
    diagram = compose.op_diagram(ctx, ns(kind="bar-chart", steps=None, data="Q1:12,Q2:26,Q3:19",
                                         title="Requests", size="1200x675", palette=None,
                                         background=None, note=None, project="selftest", name=None))
    require(any(p.suffix == ".svg" for p in diagram["outputs"]), "diagram wrote no SVG")
    require(diagram["data"]["operation"] == "diagram", "diagram payload is malformed")
    sheet_inputs = ",".join(str(p) for p in (layout["outputs"][0], diagram["outputs"][0]))
    sheet = compose.op_sheet(ctx, ns(kind="storyboard", inputs=[sheet_inputs], input=None, columns=2,
                                     cell_width=None, aspect=None, captions="one|two", background=None,
                                     project="selftest", name=None))
    panels = core.read_json([p for p in sheet["outputs"] if p.name.endswith("-panels.json")][0],
                            required=True)
    require(len(panels["panels"]) >= 2, "storyboard wrote fewer than 2 panels")
    return {"headline_contrast": headline_row["contrast"], "panels": len(panels["panels"])}


def check_edit_chain(work):
    ctx = ctx_for(work / "edit")
    src = make_source_image(work / "src" / "source.png")
    edited = edit.op_edit(ctx, ns(input=str(src), inputs=None, crop=None, size="240x240",
                                  fit="cover", rotate=None, flip="h", pad=None, pad_color=None,
                                  max_dim=None, auto_orient=False, format="png", quality=None,
                                  project="selftest"))
    require(core.image_info(edited["outputs"][0])["width"] == 240, "edit did not resize to 240")
    graded = edit.op_grade(ctx, ns(input=str(src), inputs=None, exposure=0.2, contrast=8,
                                   saturation=6, temperature=14, tint=0, gamma=None, lift=None,
                                   vignette=0.18, grain=0.01, bloom=None, relight="left",
                                   relight_strength=0.3, format="png", quality=None,
                                   project="selftest"))
    graded_row = graded["data"]["results"][0]
    require(graded_row["mean_after"] != graded_row["mean_before"], "grade did not change the image")
    converted = edit.op_convert(ctx, ns(input=str(src), inputs=None, format="webp", mode=None,
                                        quality=82, dpi=150, strip_metadata=False, bg_color=None,
                                        lossless=False, project="selftest"))
    info = core.image_info(converted["outputs"][0])
    require(info["format"] in ("webp", "WEBP"), f"convert wrote {info['format']}")
    require(converted["data"]["results"][0]["verified_openable"] is True,
            "convert did not verify its own output")
    crops = edit.op_cropset(ctx, ns(input=str(src), inputs=None, ratios="1:1,16:9", width=None,
                                    project="selftest"))
    crop_files = [p for p in crops["outputs"] if p.suffix.lower() in core.IMAGE_EXTS]
    require(len(crop_files) == 2, f"cropset wrote {len(crop_files)} crops, expected 2")
    tiled = edit.op_tile(ctx, ns(input=str(src), columns=2, rows=2, overlap=12, project="selftest"))
    require(len([p for p in tiled["outputs"] if "-panel-" in p.name]) == 4, "tile wrote wrong count")
    bleed = edit.op_bleed(ctx, ns(input=str(src), bleed=3, dpi=300, safe_margin=None,
                                  project="selftest"))
    require(any(p.suffix in (".tif", ".tiff") for p in bleed["outputs"]), "bleed wrote no TIFF")
    return {"ops": 6}


def check_cutout_composite(work):
    ctx = ctx_for(work / "cutout")
    src = make_cutout_source(work / "src" / "subject.png")
    cut = edit.op_cutout(ctx, ns(input=str(src), method="border", tolerance=32, feather=2,
                                 bg_color=None, format="png", project="selftest"))
    out = [p for p in cut["outputs"] if "-cutout-" in p.name][0]
    with __import__("PIL.Image", fromlist=["Image"]).open(out) as img:
        alpha = img.convert("RGBA").getchannel("A")
        centre = alpha.getpixel((int(img.width * 0.5), int(img.height * 0.55)))
        corner = alpha.getpixel((2, 2))
    require(corner < 12, f"cutout kept the background (corner alpha {corner})")
    require(centre > 240, f"cutout removed the subject (centre alpha {centre})")
    composite = edit.op_composite(ctx, ns(input=str(out), background=None, bg_color="#f2efe8",
                                          size="300x300", scale=0.6, position="center",
                                          shadow=True, shadow_blur=None, shadow_opacity=None,
                                          format="png", quality=None, project="selftest", name="comp"))
    require(core.image_info(composite["outputs"][0])["width"] == 300, "composite ignored --size")
    return {"corner_alpha": corner, "centre_alpha": centre}


def check_motion(work):
    ctx = ctx_for(work / "motion")
    src = make_source_image(work / "src" / "source.png")
    gif = edit.op_gif(ctx, ns(input=str(src), inputs=None, frames=6, duration=60, loop=0,
                              colors=48, motion="zoom", direction="in", format="gif", size="240x240",
                              export_frames=0, name="anim", project="selftest"))
    with __import__("PIL.Image", fromlist=["Image"]).open(gif["outputs"][0]) as img:
        require(getattr(img, "n_frames", 1) == 6, f"gif has {getattr(img, 'n_frames', 1)} frames")
    sprite = edit.op_sprite(ctx, ns(inputs=None, input=str(src), frames=4, motion="pan",
                                    direction="in", size="160x120", frame_width=None,
                                    frame_height=None, columns=2, duration=80, name="spr",
                                    project="selftest"))
    atlas = core.read_json([p for p in sprite["outputs"] if p.name.endswith("-atlas.json")][0],
                           required=True)
    require(len(atlas["frames"]) == 4, "sprite atlas has the wrong frame count")
    frames = edit.op_frames(ctx, ns(input=str(gif["outputs"][0]), lock_palette=True, colors=32,
                                    project="selftest"))
    require(len([p for p in frames["outputs"] if "-frame.f" in p.name]) == 6,
            "frames did not extract every GIF frame")
    return {"gif_frames": 6, "sprite_frames": 4}


def check_analysis(work):
    ctx = ctx_for(work / "analysis")
    src = make_source_image(work / "src" / "source.png")
    graded_src = make_source_image(work / "src" / "source-b.png", size=(420, 300))
    analysis = qa.op_analyze(ctx, ns(input=str(src), inputs=None, mask=False, stress=True,
                                     project="selftest"))
    payload = analysis["data"]
    require(payload["images"][0]["palette"], "analysis found no palette")
    masks = qa.op_mask(ctx, ns(input=str(src), kind="all", project="selftest"))
    require(len([p for p in masks["outputs"] if p.suffix == ".png"]) == 3, "mask did not write 3 maps")
    diff = qa.op_diff(ctx, ns(left=str(src), right=str(graded_src), project="selftest"))
    require(diff["data"]["metrics"]["psnr_db"] > 20, "identical-ish images scored a poor PSNR")
    verify = qa.op_verify(ctx, ns(input=str(src), inputs=None, expect_size="420x300",
                                  project="selftest"))
    require(verify["data"]["ok"], f"verify rejected a valid file: {verify['data'].get('files')}")
    reference = plan.op_reference(ctx, ns(refs=None, input=f"{src}", project="selftest"))
    params = reference["data"]["transfer_parameters"]
    require("target_mean_luma" in params and params["target_mean_luma"] > 0,
            "reference transfer parameters missing")
    return {"psnr": diff["data"]["metrics"]["psnr_db"], "transfer_keys": len(params)}


def check_gates(work):
    ctx = ctx_for(work / "gates", force=True)
    good = make_source_image(work / "src" / "good.png", size=(512, 512))
    tiny = make_source_image(work / "src" / "tiny.png", size=(48, 48))
    passed = qa.op_gate(ctx, ns(input=str(good), inputs=None, expect_size="512x512",
                                expect_format="png", expect_transparent=False, expect_palette=None,
                                fail_on="never", project="selftest"))
    require(passed["data"]["verdict"] in ("pass", "warn"), f"good image gated as {passed['data']['verdict']}")
    failed = qa.op_gate(ctx, ns(input=str(tiny), inputs=None, expect_size="2048x2048",
                                expect_format="jpg", expect_transparent=True, expect_palette=None,
                                fail_on="never", project="selftest"))
    require(failed["data"]["verdict"] == "error", "bad image passed the gate")
    require(failed["data"]["images"][0]["failures"], "gate listed no failures")
    strict = qa.op_gate(ctx, ns(input=str(tiny), inputs=None, expect_size=None, expect_format=None,
                                expect_transparent=False, expect_palette=None, fail_on="error",
                                project="selftest"))
    require(strict["data"]["verdict"] == "error", "strict gate verdict did not follow the failures")
    return {"good": passed["data"]["verdict"], "bad_failures": len(failed["data"]["images"][0]["failures"])}


def check_governance(work):
    ctx = ctx_for(work / "govern", force=True)
    src = make_source_image(work / "src" / "source.png")
    safety = qa.op_safety(ctx, ns(brief="photo of a real person holding a serum bottle, use their likeness",
                                  input=None, consent_subject=None, consent_reference=None,
                                  consent_scope=None, project="selftest"))
    require(safety["data"]["findings"], "safety review found no risk factors in a high-risk brief")
    level = safety["data"]["risk"]["level"] if isinstance(safety["data"]["risk"], dict) else safety["data"]["risk"]
    require(level in ("approval-required", "block", "high", "medium"), f"risk scored too low: {level}")
    consent = expect_tool_error(
        "approval_required",
        lambda: qa.op_safety(ctx, ns(brief="use the likeness of a real person", input=None,
                                     consent_subject="Jane Doe", consent_reference="REL-1",
                                     consent_scope="web only", project="selftest")),
        "consent claim without approval")
    prov = qa.op_provenance(ctx, ns(mode="write", input=str(src), inputs=None, creator="imagekit",
                                    lineage="source.png", rights="internal test asset",
                                    disclosure="synthetic reference", notes="selftest", prompt_text="a bottle",
                                    project="selftest"))
    verified = qa.op_provenance(ctx, ns(mode="verify", input=str(src), inputs=None, creator=None,
                                        lineage=None, rights=None, disclosure=None, notes=None,
                                        prompt_text=None, project="selftest"))
    require(verified["data"]["all_ok"], f"provenance verify rejected its own record: {verified['data']}")
    interrupted = qa.op_provenance(ctx, ns(mode="verify", input=work / "src" / "tiny-nope.png"
                                          if False else str(make_source_image(work / "src" / "bare.png")),
                                          inputs=None, creator=None, lineage=None, rights=None,
                                          disclosure=None, notes=None, prompt_text=None,
                                          project="selftest"))
    require(not interrupted["data"]["all_ok"], "provenance verify passed a file with no record")
    return {"risk": level, "consent_gate": consent["exit"]}


def check_library(work):
    root = work / "library"
    root.mkdir(parents=True, exist_ok=True)
    make_source_image(root / "a.png", (320, 240))
    make_source_image(root / "b.png", (320, 240))
    shutil.copy2(root / "a.png", root / "a-copy.png")
    ctx = ctx_for(work / "library-out")
    index = library.op_library(ctx, ns(action="index", library=str(root), target=None, apply=False,
                                       publish=False, threshold=None, tags=None, title=None,
                                       window=None, project="selftest"))
    require(index["data"]["asset_count"] == 3, f"index counted {index['data']['asset_count']} assets")
    dedupe = library.op_library(ctx, ns(action="dedupe", library=str(root), target=None, apply=False,
                                        publish=False, threshold=None, tags=None, title=None,
                                        window=None, project="selftest"))
    require(dedupe["data"]["groups"], "dedupe found no duplicate group for an identical copy")
    gated = expect_tool_error(
        "approval_required",
        lambda: library.op_library(ctx, ns(action="dedupe", library=str(root), target=None,
                                          apply=True, publish=False, threshold=None, tags=None,
                                          title=None, window=None, project="selftest")),
        "quarantine without approval")
    gallery = library.op_library(ctx, ns(action="gallery", library=str(root), target=None, apply=False,
                                         publish=False, threshold=None, tags=None, title=None,
                                         window=None, project="selftest"))
    require(any(p.suffix == ".html" for p in gallery["outputs"]), "gallery wrote no HTML")
    handoff = library.op_library(ctx, ns(action="handoff", library=str(root),
                                         target=str(work / "handoff"), apply=False, publish=False,
                                         threshold=None, tags=None, title=None, window=None,
                                         project="selftest"))
    require(not handoff["data"].get("dry_run"), "handoff reported as dry run when it was not asked to")
    return {"assets": 3, "duplicate_groups": len(dedupe["data"]["groups"]), "gate": gated["exit"]}


def check_pipeline(tmp_root):
    def make_plan(path, failing=False):
        steps = [
            {"id": "brief", "op": "brief", "args": {"brief": "soft studio photo of a serum bottle"}},
            {"id": "icon", "op": "icon", "args": {"kind": "badge", "glyph": "star", "sizes": "128",
                                                  "name": "badge"}, "deps": ["brief"]},
        ]
        if failing:
            steps.append({"id": "broken", "op": "brief", "args": {"brief": "x"}, "deps": ["icon"],
                          "retries": 0})
        payload = {"version": 1, "name": "selftest-plan", "defaults": {"project": "selftest"},
                   "timeout_s": 60, "retries": 1, "steps": steps}
        core.write_json(path, payload)
        return path

    build = tmp_root / "pipeline"
    build.mkdir(parents=True, exist_ok=True)
    plan_path = make_plan(build / "plan.json")
    ctx = ctx_for(build)
    registry = {"brief": plan.op_brief, "icon": compose.op_icon}

    validated = pipeline.op_pipeline(ctx, ns(plan=str(plan_path), action="validate", resume=False,
                                             workers=1), registry)
    require(validated["data"]["valid"], "plan failed validation")
    require(validated["data"]["order"] == ["brief", "icon"], f"wrong order: {validated['data']['order']}")

    state, state_path, _ = pipeline.run_plan(ctx, plan_path, registry)
    require(state["status"] == "ok", f"pipeline did not finish ok: {state['status']}")
    require(state_path.exists(), "pipeline wrote no checkpoint state file")

    # resume: everything already ok, so every step must be skipped
    resumed, _, _ = pipeline.run_plan(ctx, plan_path, registry, resume=True)
    require(all(s["status"] == "skipped-resumed" for s in resumed["steps"]),
            f"resume did not skip completed steps: {[s['status'] for s in resumed['steps']]}")

    # retry: a step that raises a transient provider error once and then succeeds
    calls = {"n": 0}

    def flaky(c, a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise core.ToolError("provider_unavailable", "simulated outage", "retry", core.EXIT_PROVIDER)
        return {"data": {"ok": True}, "outputs": []}

    retry_dir = tmp_root / "pipeline-retry"
    retry_dir.mkdir(parents=True, exist_ok=True)
    retry_plan = retry_dir / "plan.json"
    core.write_json(retry_plan, {"version": 1, "name": "retry", "retries": 2, "steps": [
        {"id": "flaky", "op": "flaky", "args": {}}]})
    retry_ctx = ctx_for(retry_dir)
    retry_state, _, _ = pipeline.run_plan(retry_ctx, retry_plan, {"flaky": flaky})
    require(retry_state["status"] == "ok", "retry did not recover from a transient failure")
    require(retry_state["steps"][0]["attempts"] == 2, "retry did not record 2 attempts")

    # timeout: a slow call must be stopped, not waited on
    timed_out = False
    try:
        pipeline._run_with_timeout(lambda: time.sleep(3), 1)
    except TimeoutError:
        timed_out = True
    require(timed_out, "hard timeout did not interrupt a slow step")

    # cancellation: a cancelled context must not run the next step
    cancel_ctx = ctx_for(tmp_root / "pipeline-cancel")
    cancel_ctx.cancelled = True
    cancel_state, _, _ = pipeline.run_plan(cancel_ctx, plan_path, registry)
    require(cancel_state["status"] == "cancelled", f"cancel produced {cancel_state['status']}")
    require(cancel_state["steps"][0]["status"] == "cancelled", "first step ran despite cancellation")
    return {"steps": len(state["steps"]), "resumed": "skipped-resumed",
            "retry_attempts": retry_state["steps"][0]["attempts"], "timeout": "enforced"}


def check_providers(work):
    ctx = ctx_for(work / "provider")
    listing = providers.op_provider(ctx, ns(action="list", name=None, prompt_text=None, negative=None,
                                            size=None, format=None, preset=None, style=None,
                                            palette=None, label=None, project="selftest"))
    require(listing["data"]["providers"], "no providers listed")
    check = providers.op_provider(ctx, ns(action="check", name="mock-local", prompt_text=None,
                                          negative=None, size=None, format=None, preset=None,
                                          style=None, palette=None, label=None, project="selftest"))
    require(check["data"]["ok"], "mock provider is not ready")
    generated = providers.op_provider(ctx, ns(action="generate", name="mock-local",
                                              prompt_text="amber bottle on linen", negative=None,
                                              size="320x240", format="png", preset=None, style=None,
                                              palette=None, label=None, project="selftest"))
    image = [p for p in generated["outputs"] if p.suffix == ".png"][0]
    require(core.image_info(image)["width"] == 320, "mock provider ignored the requested size")
    require(image.with_suffix(image.suffix + ".provenance.json").exists(),
            "provider output has no provenance record")
    http = providers.op_provider(ctx, ns(action="check", name="generic-http-image",
                                         prompt_text=None, negative=None, size=None, format=None,
                                         preset=None, style=None, palette=None, label=None,
                                         project="selftest"))
    require(http["data"]["ok"] is False, "an unconfigured http provider claimed to be ready")
    require(http["data"]["problems"], "an unconfigured provider reported no problems")
    blocked = expect_tool_error(
        "provider_unavailable",
        lambda: providers.op_provider(ctx, ns(action="generate", name="generic-http-image",
                                              prompt_text="a bottle", negative=None, size="256x256",
                                              format="png", preset=None, style=None, palette=None,
                                              label=None, project="selftest")),
        "generate through an unconfigured provider")
    return {"providers": len(listing["data"]["providers"]), "mock_ready": True,
            "http_blocked": blocked["error_code"]}


def check_cli(work):
    """Exit codes must be stable for automation."""
    cli = HERE / "tools-imagekit.py"
    out = work / "cli"
    out.mkdir(parents=True, exist_ok=True)

    def run(*args):
        return subprocess.run([sys.executable, str(cli), *args], capture_output=True, text=True, timeout=180)

    caps = run("capabilities", "--json")
    require(caps.returncode == 0, f"capabilities exited {caps.returncode}: {caps.stderr[-300:]}")
    payload = json.loads(caps.stdout)
    require(payload["operations"], "capabilities returned no operations")
    require(payload["catalog"]["skill_concepts"] == 290, "capabilities lost the catalog count")

    bad = run("render", "--preset", "no-such-preset", "--out", str(out), "--json")
    require(bad.returncode == 2, f"unknown preset should exit 2, got {bad.returncode}: {bad.stderr[-200:]}")

    missing = run("verify", "--input", str(work / "does-not-exist.png"), "--out", str(out), "--json")
    require(missing.returncode == 3, f"missing input should exit 3, got {missing.returncode}")

    tiny = make_source_image(out / "tiny.png", (64, 64))
    gate = run("qa", "--input", str(tiny), "--expect-size", "1920x1080", "--fail-on", "error",
               "--out", str(out), "--json")
    require(gate.returncode == 6, f"failing gate should exit 6, got {gate.returncode}")

    help_run = run("help", "render")
    require(help_run.returncode == 0 and "--preset" in help_run.stdout, "help render printed no options")

    skills = run("skills", "--search", "icon", "--json")
    require(skills.returncode == 0 and json.loads(skills.stdout)["count"] >= 1, "skills search failed")

    return {"exit_codes": {"ok": 0, "usage": bad.returncode, "input": missing.returncode,
                           "gate": gate.returncode}}


def check_policy_suite(work):
    """Policy text scanning, disclosure and rights rules."""
    ctx = ctx_for(work / "policy")
    policy = core.load_policy(ctx.config)
    high_findings = core.scan_text("Generate a photorealistic image of a specific real celebrity "
                                   "endorsing my product, no consent, looks like a real photo", policy)
    high_risk = core.risk_summary(high_findings)
    require(high_findings, "safety scan missed an unconsented likeness request")
    require(high_risk["level"] in ("approval-required", "block"), f"risk scored {high_risk['level']}")
    require("consent" in high_risk["gates"], "consent gate not raised for a likeness request")
    low_findings = core.scan_text("Create a flat illustration of abstract geometric shapes for a "
                                  "poster", policy)
    low_risk = core.risk_summary(low_findings)
    require(low_risk["level"] == "clear", f"benign brief scored {low_risk['level']} ({low_findings})")
    gate_groups = policy["qa"]["gates"]
    require({"technical", "visual", "delivery"} <= set(gate_groups), "gate groups incomplete")
    overrides = core.load_policy(None)
    require(overrides["qa"]["resolution"]["min_dimension"] > 0, "policy thresholds not loaded")
    require(policy["disclosures"]["synthesis"], "policy has no synthetic-content disclosure text")
    return {"benign": low_risk["level"], "risky": high_risk["level"],
            "gate_groups": sorted(gate_groups)}


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def run_suite(out_dir=None, keep=False, suite="all", json_mode=False):
    started = time.time()
    root = Path(out_dir).expanduser().resolve() if out_dir else Path(tempfile.mkdtemp(prefix="imagekit-selftest-"))
    root.mkdir(parents=True, exist_ok=True)
    work = root / "workspace"
    work.mkdir(parents=True, exist_ok=True)
    results = []

    fast_checks = [
        ("catalog_map", check_catalog, {}),
        ("assets_load", check_assets, {}),
        ("planning", check_planning, {"work": work}),
        ("render_presets", check_render, {"work": work}),
        ("marks_and_patterns", check_marks, {"work": work}),
        ("layout_diagram_sheet", check_layout_and_diagram, {"work": work}),
        ("edit_chain", check_edit_chain, {"work": work}),
        ("cutout_composite", check_cutout_composite, {"work": work}),
        ("motion", check_motion, {"work": work}),
        ("analysis", check_analysis, {"work": work}),
        ("quality_gates", check_gates, {"work": work}),
        ("governance", check_governance, {"work": work}),
        ("library", check_library, {"work": work}),
        ("pipeline", check_pipeline, {"tmp_root": work}),
        ("providers", check_providers, {"work": work}),
        ("cli_contract", check_cli, {"work": work}),
    ]
    policy_checks = [("policy_rules", check_policy_suite, {"work": work})]

    selected = []
    if suite in ("all", "fast"):
        selected += [(name, fn, kw, "fast") for name, fn, kw in fast_checks]
    if suite in ("all", "policy"):
        selected += [(name, fn, kw, "policy") for name, fn, kw in policy_checks]

    for name, fn, kwargs, tag in selected:
        run_check(results, name, lambda fn=fn, kwargs=kwargs: fn(**kwargs), tag)

    failed = [r for r in results if r["status"] != "ok"]
    elapsed = round(time.time() - started, 2)
    report = {
        "suite": suite,
        "checks": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "status": "ok" if not failed else "failed",
        "seconds": elapsed,
        "workspace": str(work),
        "kept": bool(keep),
        "results": results,
    }
    report_path = root / "selftest-report.json"
    core.write_json(report_path, report)

    if not keep:
        shutil.rmtree(work, ignore_errors=True)
        report["workspace"] = "removed (pass --keep to inspect)"

    if json_mode:
        print(json.dumps(report, indent=2))
    else:
        print(f"imagekit selftest — suite '{suite}'")
        for record in results:
            mark = "ok  " if record["status"] == "ok" else "FAIL"
            detail = record.get("detail")
            if isinstance(detail, dict):
                detail = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:4])
            print(f"  [{mark}] {record['name']:<24} {record['seconds']:>6.2f}s  {detail or ''}")
            if record["status"] != "ok" and record.get("traceback"):
                print("        " + record["traceback"].strip().splitlines()[-1])
        print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {elapsed}s")
        print(f"report: {report_path}")
        if failed:
            print("failing checks: " + ", ".join(r["name"] for r in failed))
    return core.EXIT_OK if not failed else core.EXIT_POLICY


if __name__ == "__main__":
    sys.exit(run_suite(out_dir=None, keep=True, suite="all", json_mode=False))
