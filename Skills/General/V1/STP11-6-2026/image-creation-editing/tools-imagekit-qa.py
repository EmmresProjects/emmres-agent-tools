"""imagekit qa: analysis, quality gates, traceability, safety review, provenance,
difference explanation and production reporting.

Quality findings are deterministic measurements or clearly labelled heuristics.
No trained detector is bundled, so artifact findings are advisory and require a
human reviewer before they are treated as defects.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageStat

import imagekit_core as core
import imagekit_edit as edit


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def analysis_payload(path, img, ctx):
    info = core.image_info(path)
    palette = core.dominant_colors(img, 6)
    tone = core.tone_stats(img)
    saliency = core.saliency_map(img)
    centroid = edit.saliency_centroid(saliency)
    grid = grid_energy(saliency)
    return {
        "file": core.rel_posix(path),
        "technical": info,
        "palette": palette,
        "tone": tone,
        "colour": {
            "channel_means": core.channel_means(img),
            "cast_delta": cast_delta(img),
            "saturation_mean": saturation_mean(img),
        },
        "structure": {
            "edge_density": core.edge_density(img),
            "sharpness_laplacian_variance": core.laplacian_variance(img),
            "contrast_rms": core.rms_contrast(img),
            "saliency_centroid": list(centroid),
            "grid_energy": grid,
            "horizon_estimate": horizon_estimate(img),
            "skin_tone_ratio": core.skin_ratio(img),
        },
        "content_signals": {
            "face_presence_heuristic": core.skin_ratio(img) > 0.06,
            "text_presence_heuristic": text_presence(img),
            "transparency": info["has_alpha"],
            "note": "skin and text signals are colour/edge heuristics, not detectors",
        },
    }


def grid_energy(saliency):
    small = saliency.convert("L").resize((3, 3), Image.Resampling.BOX)
    px = list(small.getdata())
    total = sum(px) or 1
    return [round(v / total, 3) for v in px]


def cast_delta(img):
    means = core.channel_means(img.convert("RGB"))
    avg = sum(means) / 3
    return round(max(abs(m - avg) for m in means), 2)


def saturation_mean(img):
    from PIL import ImageStat
    hsv = img.convert("HSV")
    return round(ImageStat.Stat(hsv.getchannel("S")).mean[0] / 255, 4)


def horizon_estimate(img):
    small = img.convert("L").resize((64, 64), Image.Resampling.BOX)
    rows = []
    for y in range(64):
        row = [small.getpixel((x, y)) for x in range(64)]
        rows.append(sum(row) / 64)
    gradient = [abs(rows[i + 1] - rows[i]) for i in range(63)]
    idx = gradient.index(max(gradient)) if gradient else 0
    return round(idx / 63, 3)


def text_presence(img):
    """Heuristic: many small high-contrast horizontal runs suggest rendered text."""
    small = img.convert("L")
    small.thumbnail((400, 400))
    edges = small.filter(ImageFilter.FIND_EDGES)
    px = edges.load()
    runs = 0
    for y in range(0, small.height, 2):
        run = 0
        for x in range(small.width):
            if px[x, y] > 40:
                run += 1
            else:
                if 3 <= run <= 40:
                    runs += 1
                run = 0
    density = runs / (small.width * small.height / 220.0)
    return round(min(1.0, density), 3)


def op_analyze(ctx, a):
    inputs = edit.collect_inputs(a, ctx)
    results = []
    outputs = []
    for src in inputs:
        img = core.open_image(src, ctx)
        payload = analysis_payload(src, img, ctx)
        if getattr(a, "mask", False):
            mask_dir = core.ensure_dir(core.safe_join(ctx.out_root, "masks"))
            sal = core.saliency_map(img)
            sal_path = core.safe_join(mask_dir, core.slugify(src.stem) + "-saliency.png")
            core.save_image(ctx, sal.convert("L"), sal_path, "PNG")
            depth = depth_proxy(img)
            depth_path = core.safe_join(mask_dir, core.slugify(src.stem) + "-depth.png")
            core.save_image(ctx, depth, depth_path, "PNG")
            outputs += [sal_path, depth_path]
            payload["masks"] = {"saliency": core.rel_posix(sal_path, ctx.out_root),
                                "depth_proxy": core.rel_posix(depth_path, ctx.out_root)}
        if getattr(a, "stress", False):
            payload["boundary_test"] = boundary_test(img)
        results.append(payload)
        ctx.audit("analyze.completed", file=core.rel_posix(src))
    payload = {"operation": "analyze", "count": len(results), "images": results}
    name = core.slugify(f"{getattr(a, 'project', None) or 'analysis'}-content-analysis")
    out_json = core.emit_json(ctx, name, payload)
    md = render_analysis_md(payload)
    out_md = core.emit_text(ctx, name, "md", md)
    return {"data": payload, "outputs": outputs + [out_json, out_md]}


def depth_proxy(img):
    """Monocular depth proxy from sharpness + brightness + vertical position. Not real depth."""
    grey = img.convert("L")
    base = grey.filter(ImageFilter.GaussianBlur(0.8))
    detail = ImageChops.difference(grey, base)
    detail = ImageOps_autocontrast(detail)
    shaded = ImageChops.add(detail, core.vignette_mask(img.size, strength=1.0).point(lambda v: 255 - v))
    return ImageOps_autocontrast(shaded.filter(ImageFilter.GaussianBlur(max(1, min(img.size) * 0.004))))


def ImageOps_autocontrast(img):
    from PIL import ImageOps
    return ImageOps.autocontrast(img, cutoff=1)


def boundary_test(img):
    """Push the image through hostile transforms and record where quality breaks."""
    tests = {}
    base_scores = {"sharpness": core.laplacian_variance(img), "contrast": core.rms_contrast(img)}
    variants = {
        "extreme_crop_20pct": lambda im: im.crop((int(im.width * 0.4), int(im.height * 0.4),
                                                  int(im.width * 0.6), int(im.height * 0.6))),
        "downscale_25pct": lambda im: im.resize((max(8, im.width // 4), max(8, im.height // 4)),
                                                Image.Resampling.LANCZOS),
        "upscale_200pct": lambda im: im.resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS),
        "grayscale": lambda im: im.convert("L").convert(im.mode),
        "mirrored": lambda im: im.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
    }
    for name, fn in variants.items():
        try:
            out = fn(img)
            tests[name] = {
                "result_size": list(out.size),
                "sharpness": core.laplacian_variance(out),
                "contrast": core.rms_contrast(out),
                "sharpness_retention": round(core.laplacian_variance(out) / max(1e-6, base_scores["sharpness"]), 3),
            }
        except Exception as exc:
            tests[name] = {"error": str(exc)}
    worst = min((v.get("sharpness_retention", 1.0), k) for k, v in tests.items())
    return {"baseline": base_scores, "transforms": tests,
            "weakest_transform": worst[1], "weakest_retention": worst[0],
            "guidance": "if retention drops below 0.4 the artwork relies on detail that will not survive the transform"}


def render_analysis_md(payload):
    lines = ["# Image content analysis", ""]
    for item in payload["images"]:
        t = item["technical"]
        lines += [f"## {item['file']}", "",
                  f"- Size: {t['width']}x{t['height']} ({core.aspect_label(t['width'], t['height'])}), mode {t['mode']}, format {t['format']}",
                  f"- Frames: {t['frames']}, alpha: {t['has_alpha']}, dpi: {t['dpi']}",
                  f"- Palette: {', '.join(c['hex'] for c in item['palette'])}",
                  f"- Tone: mean {item['tone']['mean']}, stddev {item['tone']['stddev']}, "
                  f"dynamic range {item['tone']['dynamic_range']}",
                  f"- Sharpness: {item['structure']['sharpness_laplacian_variance']}, "
                  f"contrast rms {item['structure']['contrast_rms']}, edge density {item['structure']['edge_density']}",
                  f"- Saliency centroid: {item['structure']['saliency_centroid']}, "
                  f"horizon estimate {item['structure']['horizon_estimate']}",
                  f"- Grid energy (top-left to bottom-right): {item['structure']['grid_energy']}",
                  f"- Signals: face-likely {item['content_signals']['face_presence_heuristic']}, "
                  f"text-likely {item['content_signals']['text_presence_heuristic']}",
                  ""]
        if "boundary_test" in item:
            bt = item["boundary_test"]
            lines += ["### Transformation boundary test", ""]
            for k, v in bt["transforms"].items():
                if "error" in v:
                    lines.append(f"- {k}: error — {v['error']}")
                else:
                    lines.append(f"- {k}: sharpness retained {v['sharpness_retention']}")
            lines += ["", f"Weakest transform: **{bt['weakest_transform']}** "
                           f"(retention {bt['weakest_retention']}).", "", bt["guidance"], ""]
    return "\n".join(lines) + "\n"


def op_mask(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'mask' command needs --input IMAGE.")
    img = core.open_image(src, ctx)
    outputs = []
    sal = core.saliency_map(img)
    depth = depth_proxy(img)
    kind = (getattr(a, "kind", None) or "all").lower()
    if kind not in ("all", "saliency", "depth", "regions"):
        raise core.usage_error("--kind must be all, saliency, depth or regions")
    report = {"operation": "mask", "source": core.rel_posix(src), "kind": kind, "files": {}}
    if kind in ("all", "saliency"):
        p = ctx.out_path(core.slugify(f"{getattr(a, 'project', None) or 'mask'}-{src.stem}-saliency"), ".png")
        core.save_image(ctx, sal.convert("L"), p, "PNG")
        outputs.append(p)
        report["files"]["saliency"] = core.rel_posix(p, ctx.out_root)
        report["saliency_centroid"] = list(edit.saliency_centroid(sal))
    if kind in ("all", "depth"):
        p = ctx.out_path(core.slugify(f"{getattr(a, 'project', None) or 'mask'}-{src.stem}-depth"), ".png")
        core.save_image(ctx, depth, p, "PNG")
        outputs.append(p)
        report["files"]["depth_proxy"] = core.rel_posix(p, ctx.out_root)
    if kind in ("all", "regions"):
        regions = region_map(img)
        p = ctx.out_path(core.slugify(f"{getattr(a, 'project', None) or 'mask'}-{src.stem}-regions"), ".png")
        core.save_image(ctx, regions["visual"], p, "PNG")
        outputs.append(p)
        report["files"]["regions"] = core.rel_posix(p, ctx.out_root)
        report["regions"] = regions["data"]
    report["limits"] = ["depth is a monocular proxy from local sharpness and vignetting; it is not metric depth",
                        "saliency is an edge/centre heuristic, not a segmentation model",
                        "use these maps as compositing guides and review before use"]
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or 'mask'}-{src.stem}-masks"), report)
    ctx.audit("mask.created", kind=kind)
    return {"data": report, "outputs": outputs + [out_json]}


def region_map(img):
    small = img.convert("RGB").resize((48, 48), Image.Resampling.BOX)
    px = small.load()
    labels = []
    out = img.copy()
    d = ImageDraw.Draw(out)
    cell_w, cell_h = img.width // 6, img.height // 6
    data = []
    for gy in range(6):
        for gx in range(6):
            x0, y0 = gx * 8, gy * 8
            px_vals = [px[x, y] for y in range(y0, y0 + 8) for x in range(x0, x0 + 8)]
            mean = tuple(int(sum(v[i] for v in px_vals) / len(px_vals)) for i in range(3))
            spread = max(max(v[i] for v in px_vals) - min(v[i] for v in px_vals) for i in range(3))
            label = "flat" if spread < 18 else "detailed"
            data.append({"row": gy + 1, "column": gx + 1, "mean_hex": core.rgb_to_hex(mean),
                         "spread": spread, "class": label})
            d.rectangle([gx * cell_w, gy * cell_h, (gx + 1) * cell_w, (gy + 1) * cell_h],
                        outline=(255, 60, 90), width=2)
    return {"visual": out, "data": {"grid": [6, 6], "cells": data,
                                    "flat_cells": sum(1 for c in data if c["class"] == "flat")}}


# --------------------------------------------------------------------------
# difference comparison
# --------------------------------------------------------------------------


def op_diff(ctx, a):
    left_path = core.resolve_input(getattr(a, "left", None) or "")
    right_path = core.resolve_input(getattr(a, "right", None) or "")
    if not left_path or not right_path:
        raise core.usage_error("The 'diff' command needs --left A and --right B.")
    left = core.open_image(left_path, ctx).convert("RGB")
    right = core.open_image(right_path, ctx).convert("RGB")
    size_mismatch = left.size != right.size
    if size_mismatch:
        right_resized = right.resize(left.size, Image.Resampling.LANCZOS)
    else:
        right_resized = right
    diff = ImageChops.difference(left, right_resized)
    stat = ImageStat.Stat(diff)
    rmse = round(math.sqrt(sum(s ** 2 for s in stat.rms) / len(stat.rms)), 3)
    heat = diff.convert("L").point(lambda v: min(255, int(v * 2.4)))
    heat_rgb = Image.merge("RGB", (heat, heat.point(lambda v: int(v * 0.25)), heat.point(lambda v: int(v * 0.1))))
    overlay = Image.blend(left, heat_rgb, 0.55)
    cells = []
    grid = 4
    cw, ch = left.width // grid, left.height // grid
    cell_stat = ImageStat.Stat(heat)
    for gy in range(grid):
        for gx in range(grid):
            box = (gx * cw, gy * ch, (gx + 1) * cw, (gy + 1) * ch)
            region = ImageStat.Stat(heat.crop(box))
            cells.append({"row": gy + 1, "column": gx + 1, "mean_delta": round(region.mean[0], 2),
                          "max_delta": int(region.extrema[0][1])})
    cells.sort(key=lambda c: -c["mean_delta"])
    mean_left = core.channel_means(left)
    mean_right = core.channel_means(right_resized)
    palette_shift = [round(mean_right[i] - mean_left[i], 2) for i in range(3)]
    payload = {
        "operation": "diff",
        "left": core.rel_posix(left_path), "right": core.rel_posix(right_path),
        "size_mismatch": size_mismatch,
        "left_size": list(left.size), "right_size": list(right.size),
        "metrics": {
            "ssim": core.ssim(left, right_resized),
            "psnr_db": core.psnr(left, right_resized),
            "rmse": rmse,
            "changed_permille": round(sum(heat.crop((0, 0, left.width, left.height)).histogram()[24:]) /
                                     (left.width * left.height) * 1000, 2),
        },
        "channel_shift_rgb": palette_shift,
        "top_changed_regions": cells[:4],
        "explanation": explain_diff(rmse, cells[:4], palette_shift, size_mismatch),
        "verdict": "identical" if rmse < 0.5 else "minor" if rmse < 6 else "material" if rmse < 20 else "different",
    }
    project = getattr(a, "project", None) or "diff"
    base = core.slugify(f"{project}-diff")
    overlay_path = ctx.out_path(f"{base}-heatmap", ".png")
    core.save_image(ctx, overlay, overlay_path, "PNG")
    heat_path = ctx.out_path(f"{base}-delta", ".png")
    core.save_image(ctx, heat_rgb, heat_path, "PNG")
    out_json = core.emit_json(ctx, f"{base}-report", payload)
    ctx.audit("diff.completed", verdict=payload["verdict"])
    return {"data": payload, "outputs": [overlay_path, heat_path, out_json]}


def explain_diff(rmse, cells, shift, mismatch):
    parts = []
    if mismatch:
        parts.append("The two files have different dimensions; the right image was resampled for comparison.")
    if rmse < 0.5:
        parts.append("The images are effectively identical at pixel level.")
    else:
        worst = cells[0] if cells else None
        if worst:
            parts.append(f"The strongest change is in grid region row {worst['row']}, column {worst['column']} "
                         f"(mean delta {worst['mean_delta']}).")
        top_shift = max(range(3), key=lambda i: abs(shift[i]))
        name = ("red", "green", "blue")[top_shift]
        parts.append(f"Overall colour shifted by {shift[top_shift]:+.2f} in the {name} channel.")
        if rmse > 20:
            parts.append("Differences are large enough to change the read of the image, not just its finish.")
        else:
            parts.append("Differences are consistent with a grade, crop or resample rather than a content change.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# quality gates
# --------------------------------------------------------------------------


def banding_gap_ratio(img):
    """Empty luma bins inside gradient regions: flat fills are excluded, real banding is caught."""
    grey = img.convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    mask = edges.point(lambda v: 255 if v > 3 else 0)
    hist = grey.histogram(mask=mask)
    covered = sum(hist)
    if covered < max(64, (grey.width * grey.height) // 400):
        return 0.0
    nonzero = [i for i, v in enumerate(hist) if v]
    if not nonzero:
        return 0.0
    lo, hi = min(nonzero), max(nonzero)
    span = hi - lo + 1
    gaps = sum(1 for i in range(lo, hi + 1) if hist[i] == 0)
    return round(gaps / span, 4) if span else 0.0


def highfreq_ratio(img):
    grey = img.convert("L")
    blurred = grey.filter(ImageFilter.GaussianBlur(1.2))
    diff = ImageChops.difference(grey, blurred)
    total = ImageStat.Stat(grey).stddev[0] or 1e-6
    return round(min(3.0, ImageStat.Stat(diff).stddev[0] / total), 4)


def edge_halo_ratio(img):
    grey = img.convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    strong = edges.point(lambda v: 255 if v > 90 else 0)
    if strong.getbbox() is None:
        return 0.0
    ring = strong.filter(ImageFilter.MaxFilter(5))
    inner = strong.filter(ImageFilter.MinFilter(3))
    ring = ImageChops.subtract(ring, inner)
    ring_px = ImageStat.Stat(ring).mean[0]
    strength = ImageStat.Stat(edges).mean[0]
    return round(min(1.0, (ring_px / 255.0) * (strength / 255.0) * 3.2), 4)


def repeated_block_ratio(img):
    small = img.convert("L").resize((64, 64), Image.Resampling.BOX)
    blocks = {}
    for gy in range(0, 64, 8):
        for gx in range(0, 64, 8):
            key = tuple(small.getpixel((x, y)) // 16 for y in range(gy, gy + 8, 2) for x in range(gx, gx + 8, 2))
            blocks[key] = blocks.get(key, 0) + 1
    total = 64
    duplicates = sum(v - 1 for v in blocks.values() if v > 1)
    return round(duplicates / total, 4)


def gate_one(path, ctx, expectations=None):
    img = core.open_image(path, ctx)
    policy = core.load_policy(ctx.config)
    qa = policy.get("qa", {})
    info = core.image_info(path)
    tone = core.tone_stats(img)
    sharp = core.laplacian_variance(img)
    contrast = core.rms_contrast(img)
    checks = []

    def add(group, name, value, ok, limit, note="", advisory=False):
        checks.append({"group": group, "check": name, "value": value, "limit": limit,
                       "pass": bool(ok), "note": note, "advisory": bool(advisory)})

    res = qa.get("resolution", {})
    min_dim = res.get("min_dimension", 256)
    add("technical", "min_dimension", min(info["width"], info["height"]),
        min(info["width"], info["height"]) >= min_dim, f">= {min_dim}")
    if info.get("dpi"):
        min_dpi = res.get("min_effective_dpi", 72)
        add("technical", "dpi_metadata", info["dpi"][0], info["dpi"][0] >= min_dpi, f">= {min_dpi}",
            "dpi is metadata; pixel count decides real detail")
    sharp_cfg = qa.get("sharpness", {})
    add("visual", "laplacian_variance", sharp, sharp >= sharp_cfg.get("min_laplacian_variance", 18.0),
        f">= {sharp_cfg.get('min_laplacian_variance', 18.0)}")
    contrast_cfg = qa.get("contrast", {})
    add("visual", "contrast_rms", contrast, contrast_cfg.get("min_rms", 18.0) <= contrast <= contrast_cfg.get("max_rms", 92.0),
        f"{contrast_cfg.get('min_rms', 18.0)} .. {contrast_cfg.get('max_rms', 92.0)}")
    clip = qa.get("clipping", {})
    add("technical", "white_clip_pct", tone["white_clip_pct"], tone["white_clip_pct"] <= clip.get("max_white_clip_pct", 6.0),
        f"<= {clip.get('max_white_clip_pct', 6.0)}%")
    add("technical", "black_clip_pct", tone["black_clip_pct"], tone["black_clip_pct"] <= clip.get("max_black_clip_pct", 8.0),
        f"<= {clip.get('max_black_clip_pct', 8.0)}%")
    band_ratio = banding_gap_ratio(img)
    distinct_shades = sum(1 for v in img.convert("L").histogram() if v)
    flat_art = tone.get("stddev", 99) < 12 or distinct_shades < 32
    add("technical", "banding_gap_ratio", band_ratio,
        band_ratio <= qa.get("banding", {}).get("max_gap_ratio", 0.35),
        f"<= {qa.get('banding', {}).get('max_gap_ratio', 0.35)}",
        "advisory: flat artwork has few luma steps by design" if flat_art else "", advisory=flat_art)
    cast = cast_delta(img)
    add("visual", "colour_cast_delta", cast, cast <= qa.get("color", {}).get("max_cast_delta", 26.0),
        f"<= {qa.get('color', {}).get('max_cast_delta', 26.0)}")
    sat = saturation_mean(img)
    add("visual", "saturation_mean", sat, sat <= qa.get("color", {}).get("max_saturation_mean", 0.86),
        f"<= {qa.get('color', {}).get('max_saturation_mean', 0.86)}")
    hf = highfreq_ratio(img)
    add("technical", "highfreq_ratio", hf, hf <= qa.get("noise", {}).get("max_highfreq_ratio", 0.62),
        f"<= {qa.get('noise', {}).get('max_highfreq_ratio', 0.62)}", "proxy for noise and over-sharpening")
    naming = qa.get("naming", {})
    pattern = naming.get("pattern", r"^[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+$")
    add("delivery", "filename_pattern", Path(path).name, bool(re.match(pattern, Path(path).name)), pattern)
    allowed = [e.lower() for e in qa.get("delivery", {}).get("allowed_formats", [])]
    ext = Path(path).suffix.lstrip(".").lower()
    if allowed:
        add("delivery", "format_allowed", ext, ext in allowed, ", ".join(allowed))
    art = policy.get("artifact_heuristics", {})
    halo = edge_halo_ratio(img)
    add("artifacts", "edge_halo_ratio", halo, halo <= art.get("edge_halo_threshold", 0.34),
        f"<= {art.get('edge_halo_threshold', 0.34)}", "advisory heuristic", advisory=True)
    repeated = repeated_block_ratio(img)
    add("artifacts", "repeated_block_ratio", repeated, repeated <= art.get("repeated_block_threshold", 0.55),
        f"<= {art.get('repeated_block_threshold', 0.55)}", "advisory heuristic", advisory=True)
    if expectations:
        for key in ("width", "height", "format", "mode"):
            if key in expectations and expectations[key] is not None:
                actual = info.get(key)
                if key == "format":
                    actual = (info.get("format") or "").lower()
                    want = str(expectations[key]).lower()
                    ok = actual.startswith(want[:4]) or want.startswith((actual or "")[:4])
                else:
                    ok = actual == expectations[key]
                add("requirements", f"expected_{key}", actual, ok, str(expectations[key]))
        if expectations.get("transparent") is not None:
            add("requirements", "expected_transparency", info["has_alpha"],
                bool(info["has_alpha"]) == bool(expectations["transparent"]), str(expectations["transparent"]))
    palette_targets = (expectations or {}).get("palette")
    if palette_targets:
        palette = core.dominant_colors(img, 6)
        matched = []
        for target in palette_targets:
            tr = core.hex_to_rgb(target)
            best = min(math.dist(tr, core.hex_to_rgb(p["hex"])) for p in palette) if palette else 999
            matched.append({"target": target, "nearest": min(palette, key=lambda p: math.dist(tr, core.hex_to_rgb(p["hex"])))["hex"] if palette else None,
                            "distance": round(best, 1), "pass": best <= 60})
        add("brand", "palette_match", round(sum(1 for m in matched if m["pass"]) / len(matched), 2),
            all(m["pass"] for m in matched), "each target within RGB distance 60", "opaque colour tolerance")
        checks[-1]["detail"] = matched
    failed = [c for c in checks if not c["pass"]]
    errors = [c for c in failed if not c.get("advisory")]
    verdict = "pass" if not failed else ("error" if errors else "warn")
    return {
        "file": core.rel_posix(path, ctx.out_root if ctx.out_root else None),
        "verdict": verdict, "checks": checks,
        "failures": [f"{c['check']} = {c['value']} (limit {c['limit']})" for c in errors],
        "advisories": [f"{c['check']} = {c['value']} (limit {c['limit']})" for c in failed if c.get("advisory")],
        "warnings": [c["note"] for c in checks if c.get("note")],
        "metrics": {"size": [info["width"], info["height"]], "mode": info["mode"], "format": info["format"],
                    "sharpness": sharp, "contrast": contrast, "tone": tone, "dpi": info.get("dpi"),
                    "alpha": info["has_alpha"], "bytes": info["bytes"]},
    }


def op_gate(ctx, a):
    inputs = edit.collect_inputs(a, ctx)
    expectations = {}
    if getattr(a, "expect_size", None):
        w, h = core.parse_size(a.expect_size)
        expectations.update({"width": w, "height": h})
    if getattr(a, "expect_format", None):
        expectations["format"] = a.expect_format
    if getattr(a, "expect_transparent", None):
        expectations["transparent"] = True
    if getattr(a, "expect_palette", None):
        expectations["palette"] = [c.strip() for c in str(a.expect_palette).split(",") if c.strip()]
    results = [gate_one(p, ctx, expectations or None) for p in inputs]
    overall = "pass"
    if any(r["verdict"] == "error" for r in results):
        overall = "error"
    elif any(r["verdict"] == "warn" for r in results):
        overall = "warn"
    payload = {"operation": "gate", "verdict": overall, "count": len(results),
               "images": results,
               "policy": core.rel_posix(ctx.config.policy_path(), core.SKILL_ROOT),
               "note": "artifact checks are heuristics; treat warn-level findings as review prompts"}
    name_proj = getattr(a, "project", None) or "qa"
    out_json = core.emit_json(ctx, core.slugify(f"{name_proj}-gate-report"), payload)
    md = ["# Quality gate report", "", f"Overall verdict: **{overall}**", ""]
    for r in results:
        md += [f"## {r['file']} — {r['verdict']}", ""]
        md += [f"- {c['group']}/{c['check']}: {c['value']} (limit {c['limit']}) — "
               f"{'PASS' if c['pass'] else 'FAIL'}" + (f" [{c['note']}]" if c.get("note") else "")
               for c in r["checks"]]
        if r["failures"]:
            md += ["", "**Failures**: " + "; ".join(r["failures"])]
        md.append("")
    out_md = core.emit_text(ctx, core.slugify(f"{name_proj}-gate-report"), "md", "\n".join(md) + "\n")
    ctx.audit("gate.completed", verdict=overall, count=len(results))
    return {"data": payload, "outputs": [out_json, out_md]}


# --------------------------------------------------------------------------
# traceability
# --------------------------------------------------------------------------


def op_trace(ctx, a):
    brief_path = core.resolve_input(a.brief_file or "") if getattr(a, "brief_file", None) else None
    if not brief_path:
        raise core.usage_error("The 'trace' command needs --brief-file BRIEF.json (output of 'brief').")
    brief = core.read_json(brief_path, required=True)
    inputs = edit.collect_inputs(a, ctx)
    analyses = []
    for p in inputs:
        img = core.open_image(p, ctx)
        analyses.append({"path": p, "info": core.image_info(p), "analysis": analysis_payload(p, img, ctx)})
    rows = []
    dims = str(brief.get("dimensions") or "")
    if dims:
        w, h = core.parse_size(dims)
        for item in analyses:
            info = item["info"]
            rows.append({"requirement": f"exact dimensions {dims}", "kind": "technical",
                         "delivered": f"{info['width']}x{info['height']}",
                         "status": "met" if (info["width"], info["height"]) == (w, h) else "unmet",
                         "evidence": "image_info", "file": core.rel_posix(item["path"])})
    if brief.get("format"):
        for item in analyses:
            fmt = (item["info"].get("format") or "").lower()
            want = str(brief["format"]).lower()
            rows.append({"requirement": f"format {want}", "kind": "technical", "delivered": fmt,
                         "status": "met" if fmt == want or (want == "jpg" and fmt == "jpeg") else "unmet",
                         "evidence": "image_info", "file": core.rel_posix(item["path"])})
    if brief.get("transparency"):
        for item in analyses:
            rows.append({"requirement": "alpha channel", "kind": "technical",
                         "delivered": item["info"]["has_alpha"],
                         "status": "met" if item["info"]["has_alpha"] else "unmet",
                         "evidence": "image_info", "file": core.rel_posix(item["path"])})
    palette = brief.get("palette") or []
    if palette:
        for item in analyses:
            doms = [c["hex"] for c in item["analysis"]["palette"]]
            hits = []
            for target in palette:
                tr = core.hex_to_rgb(target)
                nearest = min(doms, key=lambda hx: math.dist(tr, core.hex_to_rgb(hx))) if doms else None
                dist = math.dist(tr, core.hex_to_rgb(nearest)) if nearest else 999
                hits.append({"target": target, "nearest": nearest, "distance": round(dist, 1),
                             "ok": dist <= 60})
            met = all(h["ok"] for h in hits)
            rows.append({"requirement": "palette match", "kind": "brand",
                         "delivered": f"{sum(1 for h in hits if h['ok'])}/{len(hits)} within tolerance",
                         "status": "met" if met else "review", "evidence": "dominant colour distance <= 60",
                         "detail": hits, "file": core.rel_posix(item["path"])})
    keywords = [k for k in (brief.get("keywords") or []) if len(k) > 3]
    subject_words = [w for w in re.findall(r"[a-z]{4,}", (brief.get("subject") or "").lower())
                     if w not in core.STOPWORDS]
    if subject_words:
        rows.append({"requirement": "subject presence (manual check)", "kind": "visual",
                     "delivered": "requires human verification",
                     "status": "manual", "evidence": "subject terms: " + ", ".join(subject_words[:6]),
                     "file": None})
    for criterion in brief.get("acceptance_criteria") or []:
        rows.append({"requirement": criterion, "kind": "acceptance", "delivered": "manual review",
                     "status": "manual", "evidence": "stated in brief"})
    for exclusion in brief.get("must_exclude") or []:
        rows.append({"requirement": f"excluded: {exclusion}", "kind": "exclusion",
                     "delivered": "requires human verification", "status": "manual",
                     "evidence": "cannot be proven automatically"})
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    payload = {"operation": "trace", "brief": core.rel_posix(brief_path), "files": [core.rel_posix(p) for p in inputs],
               "rows": rows, "counts": counts,
               "coverage": round((counts.get("met", 0) + counts.get("review", 0)) / max(1, len(rows)), 3),
               "note": "visual and exclusion requirements stay manual: automation cannot prove absence of content"}
    md = ["# Requirement traceability", "", f"Brief: {core.rel_posix(brief_path)}", "",
          f"Counts: {counts}", "", "| Requirement | Kind | Delivered | Status |", "| --- | --- | --- | --- |"]
    for row in rows:
        md.append(f"| {row['requirement']} | {row['kind']} | {row['delivered']} | {row['status']} |")
    out_json = core.emit_json(ctx, core.slugify(f"{brief.get('project', 'project')}-traceability"), payload)
    out_md = core.emit_text(ctx, core.slugify(f"{brief.get('project', 'project')}-traceability"), "md",
                            "\n".join(md) + "\n")
    ctx.audit("trace.completed", rows=len(rows), coverage=payload["coverage"])
    return {"data": payload, "outputs": [out_json, out_md]}


# --------------------------------------------------------------------------
# integrity / acceptance
# --------------------------------------------------------------------------


def op_verify(ctx, a):
    inputs = edit.collect_inputs(a, ctx)
    results = []
    ok_all = True
    for p in inputs:
        record = {"file": core.rel_posix(p), "checks": [], "ok": True}
        try:
            with Image.open(p) as im:
                im.verify()
            record["checks"].append({"check": "decodes", "pass": True})
        except Exception as exc:
            record["checks"].append({"check": "decodes", "pass": False, "error": str(exc)})
            record["ok"] = False
        try:
            info = core.image_info(p)
            record["info"] = info
            record["checks"].append({"check": "dimensions", "pass": info["width"] > 0 and info["height"] > 0,
                                     "value": [info["width"], info["height"]]})
            if info["bytes"] < 512:
                record["checks"].append({"check": "non_trivial_size", "pass": False,
                                         "value": info["bytes"], "note": "file is suspiciously small"})
                record["ok"] = False
        except Exception as exc:
            record["checks"].append({"check": "metadata", "pass": False, "error": str(exc)})
            record["ok"] = False
        expectations = {}
        if getattr(a, "expect_size", None):
            w, h = core.parse_size(a.expect_size)
            expectations = {"width": w, "height": h}
            record["checks"].append({"check": "expected_size", "pass": (info.get("width"), info.get("height")) == (w, h),
                                     "value": [info.get("width"), info.get("height")]})
            record["ok"] = record["ok"] and record["checks"][-1]["pass"]
        record["sha256"] = core.sha256_file(p)
        ok_all = ok_all and record["ok"]
        results.append(record)
    payload = {"operation": "verify", "ok": ok_all, "count": len(results), "files": results,
               "note": "opening and re-reading each file catches truncation and unsupported features; "
                       "it does not prove the recipient's application will render it identically"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or 'verify'}-integrity"), payload)
    ctx.audit("verify.completed", ok=ok_all, count=len(results))
    return {"data": payload, "outputs": [out_json]}


# --------------------------------------------------------------------------
# safety and rights
# --------------------------------------------------------------------------


def op_safety(ctx, a):
    texts = []
    if getattr(a, "brief", None):
        texts.append(a.brief)
    if getattr(a, "input", None):
        p = core.resolve_input(a.input)
        if p.suffix.lower() == ".json":
            payload = core.read_json(p, required=True)
            texts.append(json.dumps(payload, ensure_ascii=False))
        else:
            texts.append(p.read_text(encoding="utf-8")[: ctx.config.limit("max_text_chars", 20000)])
    if not texts:
        raise core.usage_error("The 'safety' command needs --brief TEXT or --input FILE.")
    combined = "\n".join(texts)
    policy = core.load_policy(ctx.config)
    findings = core.scan_text(combined, policy)
    summary = core.risk_summary(findings)
    disclosures = []
    for finding in findings:
        if finding["severity"] == "disclose":
            template = policy.get("disclosures", {}).get(finding["category"])
            if template:
                disclosures.append({"category": finding["category"], "text": template})
    if getattr(a, "disclosure_default", True) and not disclosures:
        disclosures.append({"category": "synthesis",
                            "text": policy["disclosures"]["synthesis"]})
    deception = deception_risk(combined, findings)
    consent = None
    if getattr(a, "consent_subject", None):
        if not getattr(a, "consent_reference", None):
            raise core.approval_error(
                "Consent reference missing for an identity-sensitive edit",
                "Pass --consent-reference ID (ticket, contract or release form) and --approve.")
        ctx.require_approval("safety.consent", "Recording a consent claim for a depicted person.",
                             {"subject": a.consent_subject, "reference": a.consent_reference})
        consent = {"subject": a.consent_subject, "reference": a.consent_reference,
                   "recorded_at": core.now_iso(), "scope": getattr(a, "consent_scope", None) or "unspecified"}
    payload = {
        "operation": "safety",
        "risk": summary,
        "findings": findings,
        "disclosures": disclosures,
        "deception_risk": deception,
        "approval_required": summary["level"] in ("blocked", "approval-required"),
        "gates": gates_for(summary),
        "consent_record": consent,
        "policy": core.rel_posix(ctx.config.policy_path(), core.SKILL_ROOT),
        "limits": ["lexical screening only: it cannot judge intent or context",
                   "no identity, face or provenance model is used",
                   "a human reviewer must confirm the release decision"],
    }
    md = ["# Safety and rights review", "", f"Risk level: **{summary['level']}**", "",
          f"Findings: {summary['counts']}", ""]
    for f in findings:
        md.append(f"- [{f['severity']}] {f['category']}: {f['message']} (matched: {', '.join(f['matched_terms'])})")
    if disclosures:
        md += ["", "## Required disclosures"]
        md += [f"- **{d['category']}**: {d['text']}" for d in disclosures]
    md += ["", "## Deception risk", f"Score: {deception['score']} ({deception['level']})", ""]
    md += [f"- {reason}" for reason in deception["reasons"]]
    if consent:
        md += ["", f"Consent recorded for {consent['subject']} (reference {consent['reference']})."]
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or 'safety'}-review"), payload)
    out_md = core.emit_text(ctx, core.slugify(f"{getattr(a, 'project', None) or 'safety'}-review"), "md",
                            "\n".join(md) + "\n")
    ctx.audit("safety.reviewed", level=summary["level"], score=deception["score"])
    return {"data": payload, "outputs": [out_json, out_md]}


def deception_risk(text, findings):
    low = text.lower()
    score = 0
    reasons = []
    if re.search(r"\b(photorealistic|photo-real|hyperrealistic|lifelike|as a real photo|documentary|news photo)\b", low):
        score += 3
        reasons.append("request asks for photographic realism, which raises the cost of a wrong read")
    if any(f["category"] in ("deception", "likeness") for f in findings):
        score += 3
        reasons.append("request touches evidence, endorsement or identity")
    if re.search(r"\b(before and after|testimonial|proof|results)\b", low):
        score += 2
        reasons.append("claims are implied through before/after or proof framing")
    if re.search(r"\b(public figure|celebrity|politician|official)\b", low):
        score += 2
        reasons.append("a public figure is involved, so endorsement risk applies")
    if re.search(r"\b(disclosure|label|watermark|synthetic)\b", low):
        score -= 1
        reasons.append("the request already mentions disclosure")
    score = max(0, min(10, score))
    level = "low" if score <= 2 else "moderate" if score <= 5 else "high"
    if not reasons:
        reasons.append("no deception indicators matched in the supplied text")
    return {"score": score, "level": level, "reasons": reasons,
            "recommendation": "attach a visible disclosure and record the approval decision"
            if level != "low" else "standard disclosure is sufficient"}


def gates_for(summary):
    gates = list(summary["gates"])
    for gate in ("rights", "disclosure"):
        if gate not in gates:
            gates.append(gate)
    return sorted(set(gates))


def op_provenance(ctx, a):
    mode = (getattr(a, "mode", None) or "write").lower()
    if mode not in ("write", "read", "verify"):
        raise core.usage_error("--mode must be write, read or verify")
    if mode == "write":
        src = core.resolve_input(a.input or "")
        if not src:
            raise core.usage_error("--mode write needs --input IMAGE.")
        record = {
            "record_version": 1,
            "asset": Path(src).name,
            "created_at": core.now_iso(),
            "creator": getattr(a, "creator", None) or "unspecified",
            "tool": f"imagekit {core.VERSION}",
            "lineage": [x for x in str(getattr(a, "lineage", "") or "").split(",") if x],
            "prompt": getattr(a, "prompt_text", None) or "",
            "source_notes": getattr(a, "notes", None) or "",
            "disclosure": getattr(a, "disclosure", None) or
            core.load_policy(ctx.config)["disclosures"]["synthesis"],
            "rights": getattr(a, "rights", None) or "unverified",
        }
        sidecar = src.with_suffix(src.suffix + ".provenance.json")
        if sidecar.exists() and not ctx.force:
            raise core.input_error(f"Provenance sidecar already exists: {sidecar.name}",
                                   "Pass --force to replace it.")
        embedded = False
        try:
            with Image.open(src) as im:
                fmt = (im.format or "").upper()
            if fmt == "PNG":
                img = core.open_image(src, ctx)
                core.save_image(ctx, img, src, "PNG",
                                pnginfo=core.build_pnginfo({
                                    "Title": record["asset"], "Software": record["tool"],
                                    "Description": record["disclosure"],
                                    "imagekit:lineage": json.dumps(record["lineage"]),
                                    "imagekit:provenance": sidecar.name,
                                }))
                embedded = True
        except Exception:
            embedded = False
        # hash the delivered bytes, after any metadata embedding, so verify can match them
        record["sha256"] = core.sha256_file(src)
        record["embedded_in_file"] = embedded
        if not ctx.dry_run:
            core.write_json(sidecar, record)
        payload = {"operation": "provenance", "mode": "write", "record": record,
                   "sidecar": core.rel_posix(sidecar), "embedded_in_file": embedded}
        out_json = core.emit_json(ctx, core.slugify(f"{Path(src).stem}-provenance-write"), payload)
        ctx.audit("provenance.written", asset=Path(src).name)
        return {"data": payload, "outputs": [sidecar, out_json]}
    inputs = edit.collect_inputs(a, ctx)
    records = []
    for p in inputs:
        sidecar = p.with_suffix(p.suffix + ".provenance.json")
        record = {"file": core.rel_posix(p)}
        record["metadata"] = core.read_metadata(p)
        if sidecar.exists():
            record["sidecar"] = core.read_json(sidecar)
            record["sha256_matches"] = (
                record["sidecar"].get("sha256") == core.sha256_file(p))
        else:
            record["sidecar"] = None
        record["ok"] = bool(record["sidecar"]) and record.get("sha256_matches", False)
        if mode == "verify" and not record["ok"]:
            ctx.note(f"{p.name}: provenance missing or checksum mismatch")
        records.append(record)
    payload = {"operation": "provenance", "mode": mode, "records": records,
               "all_ok": all(r["ok"] for r in records),
               "note": "checksum verification proves the file was not modified after its record was written"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or 'provenance'}-{mode}"), payload)
    return {"data": payload, "outputs": [out_json]}


# --------------------------------------------------------------------------
# production dashboard
# --------------------------------------------------------------------------


def op_dashboard(ctx, a):
    state_path = getattr(a, "state", None)
    state = core.read_json(core.resolve_input(state_path), required=False) if state_path else None
    library_index = None
    lib_path = getattr(a, "library", None)
    if lib_path:
        library_index = core.read_json(core.resolve_input(lib_path), required=False)
    gate_report = None
    if getattr(a, "gate_report", None):
        gate_report = core.read_json(core.resolve_input(a.gate_report), required=False)
    files = []
    scan_dir = Path(getattr(a, "scan", None) or ctx.out_root)
    if scan_dir.exists():
        for p in sorted(scan_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in core.IMAGE_EXTS:
                files.append(p)
    by_format, by_status = {}, {}
    total_bytes = 0
    for p in files:
        try:
            info = core.image_info(p)
        except Exception:
            continue
        by_format[info["format"]] = by_format.get(info["format"], 0) + 1
        total_bytes += info["bytes"]
        m = re.search(r"-(draft|review|approved|final|superseded)\b", p.stem)
        status = m.group(1) if m else "unlabelled"
        by_status[status] = by_status.get(status, 0) + 1
    steps = (state or {}).get("steps", [])
    failed = [s for s in steps if s.get("status") == "failed"]
    payload = {
        "operation": "dashboard",
        "generated_at": core.now_iso(),
        "assets": {"count": len(files), "total_bytes": total_bytes,
                   "by_format": by_format, "by_status": by_status,
                   "largest": sorted(([core.rel_posix(p), core.file_size(p)] for p in files),
                                     key=lambda x: -x[1])[:5]},
        "pipeline": ({"name": (state or {}).get("name"), "status": (state or {}).get("status"),
                      "steps": len(steps), "failed": len(failed),
                      "failures": [s.get("id") for s in failed]} if state else None),
        "quality": ({"verdict": gate_report.get("verdict"), "images": gate_report.get("count")}
                    if gate_report else None),
        "library": ({"assets": library_index.get("asset_count"),
                     "duplicates": library_index.get("duplicate_groups")} if library_index else None),
        "spend": {"note": "no provider billing data is collected; cost is tracked outside this tool"},
    }
    html = render_dashboard_html(payload)
    project = getattr(a, "project", None) or "production"
    out_html = core.emit_text(ctx, core.slugify(f"{project}-dashboard"), "html", html)
    out_json = core.emit_json(ctx, core.slugify(f"{project}-dashboard"), payload)
    ctx.audit("dashboard.generated", assets=len(files))
    return {"data": payload, "outputs": [out_html, out_json]}


def render_dashboard_html(payload):
    a = payload["assets"]
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(a["by_format"].items()))
    status_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(a["by_status"].items()))
    pipeline = payload.get("pipeline")
    pipeline_html = ("<p class='muted'>No pipeline state supplied.</p>" if not pipeline else
                     f"<p>Pipeline <strong>{pipeline['name']}</strong> — status "
                     f"<strong>{pipeline['status']}</strong>, {pipeline['steps']} steps, "
                     f"{pipeline['failed']} failed.</p>")
    quality = payload.get("quality")
    quality_html = ("<p class='muted'>No gate report supplied.</p>" if not quality else
                    f"<p>QA verdict <strong>{quality['verdict']}</strong> across {quality['images']} images.</p>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Production dashboard</title>
<style>
 body{{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#0e1116;color:#e6edf3}}
 .wrap{{max-width:980px;margin:0 auto;padding:32px 20px 60px}}
 h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px;color:#9fb0c3;text-transform:uppercase;letter-spacing:.08em}}
 .cards{{display:flex;gap:14px;flex-wrap:wrap;margin-top:18px}}
 .card{{flex:1 1 180px;background:#161b22;border:1px solid #232b36;border-radius:10px;padding:14px}}
 .card b{{display:block;font-size:24px;color:#7ee0c1}} .muted{{color:#8b98a8}}
 table{{width:100%;border-collapse:collapse;margin-top:8px}} td{{padding:6px 8px;border-bottom:1px solid #1e2630}}
 td:last-child{{text-align:right;color:#9fb0c3}}
</style></head><body><div class="wrap">
<h1>Production dashboard</h1>
<p class="muted">Generated {payload['generated_at']} by imagekit.</p>
<div class="cards">
 <div class="card"><b>{a['count']}</b>image assets</div>
 <div class="card"><b>{round(a['total_bytes']/1048576, 1)} MB</b>total size</div>
 <div class="card"><b>{len(a['by_format'])}</b>formats</div>
 <div class="card"><b>{a['by_status'].get('review', 0)}</b>awaiting review</div>
</div>
<h2>Pipeline</h2>{pipeline_html}
<h2>Quality</h2>{quality_html}
<h2>Assets by format</h2><table>{rows or '<tr><td>none</td><td>0</td></tr>'}</table>
<h2>Assets by status</h2><table>{status_rows or '<tr><td>none</td><td>0</td></tr>'}</table>
<h2>Cost</h2><p class="muted">{payload['spend']['note']}</p>
</div></body></html>
"""
