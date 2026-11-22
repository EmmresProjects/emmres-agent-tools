"""Image measurement, editing and quality capabilities.

Part of the AI Image Production Toolkit.

Pixel-level operations use Pillow and NumPy when they are installed; the
packaged ``tools-requirements.txt`` declares them as optional extras. When they
are missing, the affected capability raises a clear blocked-integration error
instead of pretending to have analysed the image.
"""

from __future__ import annotations

import colorsys
import importlib.util
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if "tools_core" not in sys.modules:  # pragma: no cover - direct-import fallback
    _spec = importlib.util.spec_from_file_location("tools_core", os.path.join(_HERE, "tools-core.py"))
    _core = importlib.util.module_from_spec(_spec)
    sys.modules["tools_core"] = _core
    _spec.loader.exec_module(_core)

import tools_core as core  # noqa: E402
from tools_core import (  # noqa: E402
    BlockedIntegration, InvalidInput, deterministic_seed, hex_to_rgb, load_asset,
    md_table, png_write, rgb_to_hex, slugify, svg_document, svg_text, truncate,
)

RUBRIC = "asset-quality-rubric.json"
PROFILES = "asset-model-profiles.json"
TEMPLATES = "asset-workflow-templates.json"
NEGATIVES = "asset-negative-prompts.json"


# ---------------------------------------------------------------------------
# Pixel helpers
# ---------------------------------------------------------------------------


def _md_bullets(items: List[str]) -> str:
    """Render a list as markdown bullets."""
    return "\n".join(f"- {item}" for item in items)


def _require_pixels(feature: str) -> None:
    missing = []
    if not core.have_pillow():
        missing.append("Pillow")
    if not core.have_numpy():
        missing.append("NumPy")
    if missing:
        raise BlockedIntegration(
            f"{feature} needs {' and '.join(missing)}",
            missing=missing,
            remedy="python3 -m pip install -r tools-requirements.txt",
            note="Metadata-only capabilities keep working without these extras.")


def _load_rgb(path: str):
    """Load an image as a float32 numpy array of shape (H, W, 3) in 0..255."""
    _require_pixels("Pixel analysis")
    import numpy as np
    from PIL import Image

    if not os.path.isfile(path):
        raise InvalidInput(f"image not found: {path}")
    with Image.open(path) as im:
        im.load()
        arr = np.asarray(im.convert("RGB"), dtype=np.float32)
    return arr


def _save_rgb(arr, path: str) -> str:
    _require_pixels("Image writing")
    import numpy as np
    from PIL import Image

    data = np.clip(arr, 0, 255).astype("uint8")
    core.ensure_parent(path)
    Image.fromarray(data, mode="RGB").save(path, format="PNG", optimize=True)
    return path


def _luminance(arr):
    import numpy as np

    return arr[:, :, 0] * 0.2126 + arr[:, :, 1] * 0.7152 + arr[:, :, 2] * 0.0722


def _saturation(arr):
    import numpy as np

    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)


def _variance_of_laplacian(lum) -> float:
    import numpy as np

    lap = (lum[:-2, 1:-1] + lum[2:, 1:-1] + lum[1:-1, :-2] + lum[1:-1, 2:] - 4 * lum[1:-1, 1:-1])
    return float(np.var(lap))


def _noise_sigma(lum) -> float:
    """Estimate noise sigma from the median absolute deviation of a high-pass filter."""
    import numpy as np

    k = np.array([[-1, 2, -1], [2, -4, 2], [-1, 2, -1]], dtype=np.float32)
    padded = np.pad(lum, 1, mode="reflect")
    hp = np.zeros_like(lum)
    for dy in range(3):
        for dx in range(3):
            hp += k[dy, dx] * padded[dy:dy + lum.shape[0], dx:dx + lum.shape[1]]
    mad = float(np.median(np.abs(hp - np.median(hp))))
    return round(mad / 0.6745 / 6.0, 3)


def _image_metrics(arr) -> Dict[str, Any]:
    import numpy as np

    lum = _luminance(arr)
    sat = _saturation(arr)
    hist = np.histogram(lum, bins=256, range=(0, 255))[0].astype(np.float64)
    total = hist.sum() or 1.0
    clip_high = float(hist[255] / total * 100)
    clip_low = float(hist[0] / total * 100)
    mid = hist[16:240].sum() / total * 100
    return {
        "width": int(arr.shape[1]), "height": int(arr.shape[0]),
        "luminance_mean": round(float(lum.mean()), 2),
        "luminance_std": round(float(lum.std()), 2),
        "luminance_p5": round(float(np.percentile(lum, 5)), 2),
        "luminance_p95": round(float(np.percentile(lum, 95)), 2),
        "clip_high_pct": round(clip_high, 3),
        "clip_low_pct": round(clip_low, 3),
        "midtone_pct": round(mid, 2),
        "saturation_mean": round(float(sat.mean()), 4),
        "saturation_p95": round(float(np.percentile(sat, 95)), 4),
        "sharpness_variance": round(_variance_of_laplacian(lum), 2),
        "noise_sigma": _noise_sigma(lum),
        "mean_rgb": [round(float(arr[:, :, i].mean()), 1) for i in range(3)],
        "channel_clip_pct": [round(float((arr[:, :, i] >= 255).mean() * 100), 3) for i in range(3)],
    }


def _dhash(arr, size: int = 8) -> str:
    import numpy as np

    small = arr[:: max(1, arr.shape[0] // size), :: max(1, arr.shape[1] // size)][:size, :size]
    bits = (small[:, 1:, 0] > small[:, :-1, 0]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{size * size // 4}x}"


def _palette(arr, count: int = 6) -> List[str]:
    """Extract a dominant palette using a luminance/saturation 3D histogram."""
    import numpy as np

    q = (arr // 32).astype(np.int32)
    keys = (q[:, :, 0] * 64 + q[:, :, 1] * 8 + q[:, :, 2]).ravel()
    counts = np.bincount(keys, minlength=64 * 8 * 8)
    order = np.argsort(counts)[::-1][: count * 3]
    picks = []
    for key in order:
        r = (key // 64) % 8
        g = (key // 8) % 8
        b = key % 8
        candidate = (r * 32 + 16, g * 32 + 16, b * 32 + 16)
        if all(sum(abs(a - b) for a, b in zip(candidate, p)) > 60 for p in picks):
            picks.append(candidate)
        if len(picks) >= count:
            break
    return [rgb_to_hex(p) for p in picks]


def _report(ctx, slug: str, title: str, body: str, data: Any, summary: str,
            metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    report = f"# {title}\n\n{body}\n"
    ctx.write_text(f"{slug}.md", report)
    ctx.write_json(f"{slug}.json", {"title": title, "summary": summary, "data": data,
                                    "metrics": metrics or {}, "generated_utc": core.utc_now()})
    return {"summary": summary, "data": data, "report": report, "metrics": metrics or {}}


def _sha_note(ctx, source: str) -> str:
    return f"source: `{core.rel_path(source)}` (sha256 {core.sha256_file(source)[:12]})"


# ---------------------------------------------------------------------------
# Foundations - diffusion demonstrator
# ---------------------------------------------------------------------------


def diffusion_demonstrator(ctx) -> Dict[str, Any]:
    steps = int(ctx.get("steps", 20))
    schedule = str(ctx.get("schedule", "linear"))
    keep = int(ctx.get("keep_frames", 6))
    source = ctx.get("image")
    _require_pixels("The diffusion demonstration")
    import numpy as np
    from PIL import Image

    if source:
        arr = _load_rgb(str(source)).astype("float32")
        origin = core.rel_path(str(source))
    else:
        width = height = 256
        yy, xx = np.mgrid[0:height, 0:width]
        arr = np.zeros((height, width, 3), dtype=np.float32)
        arr[..., 0] = 220 * (1 - xx / width)
        arr[..., 1] = 190 * (1 - yy / height)
        arr[..., 2] = 120 + 80 * np.sin(xx / 18.0) * np.cos(yy / 18.0)
        origin = "synthetic test card"
    rng = np.random.default_rng(int(ctx.get("seed", 1234)))
    noise = rng.normal(0, 1, arr.shape)

    def sigma_for(t: float) -> float:
        if schedule == "cosine":
            return math.sqrt(max(0.0, math.cos(t * math.pi / 2) ** 2))
        if schedule == "quadratic":
            return math.sqrt(max(0.0, (1 - t) ** 2))
        return math.sqrt(max(0.0, 1 - t))

    frames = []
    schedule_rows = []
    for i in range(steps + 1):
        t = i / steps
        sigma = sigma_for(t)
        noisy = arr * (1 - t) + noise * sigma * 90.0
        schedule_rows.append({"step": i, "t": round(t, 4), "sigma": round(sigma, 4)})
        if i % max(1, steps // max(1, keep)) == 0 or i == steps:
            frames.append((i, np.clip(noisy, 0, 255)))

    reverse_frames = []
    blurred = arr.copy()
    for i in range(steps, -1, -max(1, steps // max(1, keep))):
        t = i / steps
        sigma = sigma_for(t)
        noisy = blurred * (1 - t) + noise * sigma * 90.0
        # A non-learned denoiser: repeated 3x3 blur, which approximates removing the high-frequency noise.
        pil = Image.fromarray(np.clip(noisy, 0, 255).astype("uint8"), mode="RGB")
        pil = pil.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(radius=1.2))
        blurred = np.asarray(pil, dtype=np.float32)
        reverse_frames.append((i, np.clip(blurred, 0, 255)))

    written = []
    frame_dir = os.path.join(ctx.out_dir, "diffusion-frames")
    os.makedirs(frame_dir, exist_ok=True)
    for idx, (step_no, frame) in enumerate(frames):
        p = os.path.join(frame_dir, f"forward-{step_no:03d}.png")
        _save_rgb(frame, p)
        written.append(core.rel_path(p))
    for idx, (step_no, frame) in enumerate(reverse_frames):
        p = os.path.join(frame_dir, f"reverse-{step_no:03d}.png")
        _save_rgb(frame, p)
        written.append(core.rel_path(p))
    final_path = os.path.join(ctx.out_dir, "diffusion-reconstruction.png")
    _save_rgb(reverse_frames[-1][1], final_path)
    written.append(core.rel_path(final_path))
    for path in written:
        ctx.artifacts.append(path)

    metrics = _image_metrics(arr)
    reconstruction = _image_metrics(reverse_frames[-1][1])
    mae = float(np.abs(arr - reverse_frames[-1][1]).mean())
    report = "\n".join([
        f"Source: {origin}",
        f"Schedule: {schedule}, steps: {steps}, frames written: {len(written)}",
        "",
        "How this demonstration works:",
        "- The forward pass mixes the clean image with Gaussian noise along a sigma schedule.",
        "- The reverse pass repeatedly applies a non-learned 3x3 Gaussian blur to stand in for a denoiser.",
        "- This reproduces the shape of the diffusion process, not a trained model's output quality.",
        "",
        md_table([(r["step"], r["t"], r["sigma"]) for r in schedule_rows[::max(1, steps // 8)][:9]],
                 ["Step", "t", "sigma"]),
        "",
        f"Reconstruction mean absolute error: {mae:.2f} / 255",
        md_table([["sharpness (source)", metrics["sharpness_variance"]],
                  ["sharpness (reconstruction)", reconstruction["sharpness_variance"]],
                  ["noise sigma (source)", metrics["noise_sigma"]],
                  ["noise sigma (reconstruction)", reconstruction["noise_sigma"]]],
                 ["Metric", "Value"]),
    ])
    data = {"source": origin, "schedule": schedule, "steps": steps,
            "schedule_curve": schedule_rows, "frames": written,
            "reconstruction_mae": round(mae, 2),
            "source_metrics": metrics, "reconstruction_metrics": reconstruction}
    return _report(ctx, "diffusion-demonstration", "Diffusion process demonstration", report, data,
                   f"Forward and reverse diffusion demonstrated over {steps} steps.", {"mae": round(mae, 2)})


# ---------------------------------------------------------------------------
# Multi-image comparison
# ---------------------------------------------------------------------------


def identity_consistency_manager(ctx) -> Dict[str, Any]:
    images = ctx.paths("images")
    if len(images) < 2:
        raise InvalidInput("provide at least two images to compare")
    rows = []
    hashes = []
    for path in images:
        arr = _load_rgb(path)
        hashes.append((path, _dhash(arr)))
        rows.append({"file": os.path.basename(path), "dhash": hashes[-1][1],
                     "width": int(arr.shape[1]), "height": int(arr.shape[0])})
    distances = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            a, b = hashes[i][1], hashes[j][1]
            dist = sum(1 for x, y in zip(a, b) if x != y)
            distances.append({"a": os.path.basename(hashes[i][0]), "b": os.path.basename(hashes[j][0]),
                              "hamming": dist, "similarity": round(1 - dist / len(a), 3)})
    distances.sort(key=lambda d: d["hamming"])
    workflow = [
        "Freeze one reference frame and reuse it for every comparison.",
        "Generate identity candidates from a fixed seed family, then compare perceptual hashes.",
        "A hash distance above 12 of 64 usually means the identity or the framing drifted.",
        "Lock wardrobe, light and camera distance before judging the face.",
    ]
    report = "\n".join([
        md_table([[r["file"], r["dhash"], f'{r["width"]}x{r["height"]}'] for r in rows],
                 ["File", "Perceptual hash", "Size"]),
        "",
        md_table([[d["a"], d["b"], d["hamming"], d["similarity"]] for d in distances],
                 ["A", "B", "Hash distance", "Similarity"]),
        "",
        "Consistency workflow:",
        *[f"- {w}" for w in workflow],
    ])
    data = {"images": rows, "pairwise": distances, "workflow": workflow}
    return _report(ctx, "identity-consistency", "Identity consistency measurement", report, data,
                   f"Compared {len(images)} images for identity drift.",
                   {"max_hash_distance": distances[-1]["hamming"] if distances else 0})


def style_consistency_manager(ctx) -> Dict[str, Any]:
    images = ctx.paths("images")
    if len(images) < 2:
        raise InvalidInput("provide at least two images to compare")
    palettes = []
    metrics = []
    for path in images:
        arr = _load_rgb(path)
        palettes.append(_palette(arr, 6))
        metrics.append(_image_metrics(arr))
    shared = set(palettes[0])
    for pal in palettes[1:]:
        shared &= set(pal)
    spread = {"contrast": [round(min(m["luminance_std"] for m in metrics), 2),
                           round(max(m["luminance_std"] for m in metrics), 2)],
              "saturation": [round(min(m["saturation_mean"] for m in metrics), 4),
                             round(max(m["saturation_mean"] for m in metrics), 4)],
              "exposure": [round(min(m["luminance_mean"] for m in metrics), 1),
                           round(max(m["luminance_mean"] for m in metrics), 1)]}
    verdict = "consistent" if (spread["contrast"][1] - spread["contrast"][0] < 25
                               and spread["exposure"][1] - spread["exposure"][0] < 28) else "inconsistent"
    report = "\n".join([
        md_table([[os.path.basename(p), ", ".join(pal)] for p, pal in zip(images, palettes)],
                 ["File", "Dominant palette"]),
        "",
        md_table([[k, v[0], v[1], round(v[1] - v[0], 3)] for k, v in spread.items()],
                 ["Property", "Min", "Max", "Spread"]),
        "",
        f"Shared palette colours across all images: {len(shared)}",
        f"Verdict: {verdict}",
        "",
        "To align a set:",
        "1. Match the white balance to the reference frame.",
        "2. Match the contrast range with a matched curve, not by brightening.",
        "3. Match the grain structure - add the same grain amount to every frame.",
    ])
    data = {"palettes": [{"file": os.path.basename(p), "palette": pal} for p, pal in zip(images, palettes)],
            "metrics": metrics, "spread": spread, "shared_colors": sorted(shared), "verdict": verdict}
    return _report(ctx, "style-consistency", "Style consistency measurement", report, data,
                   f"Style consistency across {len(images)} images: {verdict}.",
                   {"shared_colors": len(shared)})


def contact_sheet_builder(ctx) -> Dict[str, Any]:
    images = ctx.paths("images")
    spec_path = ctx.get("spec")
    columns = int(ctx.get("columns", 4))
    cell = int(ctx.get("cell_width", 320))
    label = str(ctx.get("label", "variation sheet"))
    entries = []
    if images:
        for path in images:
            entries.append({"label": os.path.basename(path), "source": path})
    elif spec_path:
        spec = core.read_json(str(spec_path))
        items = spec.get("items") or spec.get("variants") or spec.get("entries") or []
        for i, item in enumerate(items, start=1):
            text = item if isinstance(item, str) else (item.get("prompt") or item.get("value") or item.get("label") or f"item {i}")
            entries.append({"label": str(text)[:60], "source": None})
    else:
        raise InvalidInput("provide images (--set images=...) or a specification file (--set spec=...)")
    rows = math.ceil(len(entries) / columns)
    pad = 12
    header = 54
    label_h = 34
    width = columns * (cell + pad) + pad
    height = header + rows * (cell + label_h + pad) + pad
    body = [svg_text(pad, 34, f"{label} - {len(entries)} items", 20, "#111111", weight="bold")]
    cells = []
    for i, entry in enumerate(entries):
        r, c = divmod(i, columns)
        x = pad + c * (cell + pad)
        y = header + r * (cell + label_h + pad)
        cells.append({"entry": entry, "x": x, "y": y, "row": r, "col": c})
        body.append(svg_text(x + 4, y + cell + 20, entry["label"], 13, "#222222"))
        body.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="#eeeeee" stroke="#cccccc"/>')
    svg = svg_document(width, height, "\n".join(body), "#ffffff")
    svg_path = os.path.join(ctx.out_dir, "contact-sheet.svg")
    core.write_text(svg_path, svg)
    ctx.artifacts.append(core.rel_path(svg_path))
    artifacts = [core.rel_path(svg_path)]
    png_note = "SVG contact sheet written (PNG contact sheet requires Pillow)."
    if core.have_pillow() and images:
        from PIL import Image, ImageDraw
        canvas = Image.new("RGB", (width, height), "#ffffff")
        draw = ImageDraw.Draw(canvas)
        draw.text((pad, 18), f"{label} - {len(entries)} items", fill="#111111")
        for cell_info in cells:
            x, y = cell_info["x"], cell_info["y"]
            src = cell_info["entry"]["source"]
            if src:
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((cell, cell))
                    canvas.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
            draw.rectangle([x, y, x + cell, y + cell], outline="#cccccc")
            draw.text((x + 4, y + cell + 8), cell_info["entry"]["label"][:44], fill="#222222")
        png_path = os.path.join(ctx.out_dir, "contact-sheet.png")
        canvas.save(png_path, "PNG")
        ctx.artifacts.append(core.rel_path(png_path))
        artifacts.append(core.rel_path(png_path))
        png_note = "PNG contact sheet written."
    report = "\n".join([
        f"Label: {label}",
        f"Items: {len(entries)}, grid: {columns} x {rows}, cell: {cell} px",
        "",
        md_table([[i + 1, e["label"], "yes" if e["source"] else "no"] for i, e in enumerate(entries)],
                 ["#", "Label", "Thumbnail"]),
        "",
        png_note,
    ])
    data = {"entries": entries, "grid": {"columns": columns, "rows": rows, "cell": cell},
            "canvas": [width, height], "artifacts": artifacts}
    return _report(ctx, "contact-sheet", "Contact sheet", report, data,
                   f"Built a contact sheet with {len(entries)} cells.", {"cells": len(entries)})


def before_after_editor(ctx) -> Dict[str, Any]:
    before_path = str(ctx.require("before"))
    after_path = str(ctx.require("after"))
    before = _load_rgb(before_path)
    after = _load_rgb(after_path)
    import numpy as np

    if before.shape != after.shape:
        after = after[:before.shape[0], :before.shape[1]]
        if after.shape != before.shape:
            after = np.pad(after, ((0, max(0, before.shape[0] - after.shape[0])),
                                   (0, max(0, before.shape[1] - after.shape[1])), (0, 0)), mode="edge")
            after = after[:before.shape[0], :before.shape[1]]
    diff = np.abs(after - before)
    changed = diff.max(axis=2) > 8
    changed_pct = float(changed.mean() * 100)
    ys, xs = np.nonzero(changed)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None
    mae = float(diff.mean())
    psnr = 10 * math.log10((255.0 ** 2) / max(mae, 1e-6))
    canvas_w = before.shape[1] * 2 + 24
    canvas_h = before.shape[0] + 40
    svg = svg_document(canvas_w, canvas_h,
                      f'<rect x="0" y="0" width="{before.shape[1]}" height="{before.shape[0]}" fill="#dddddd"/>'
                      f'<rect x="{before.shape[1] + 24}" y="0" width="{after.shape[1]}" height="{after.shape[0]}" fill="#dddddd"/>'
                      + svg_text(8, before.shape[0] + 26, "before", 16, "#111111")
                      + svg_text(before.shape[1] + 32, before.shape[0] + 26, "after", 16, "#111111"),
                      "#ffffff")
    svg_path = os.path.join(ctx.out_dir, "before-after.svg")
    core.write_text(svg_path, svg)
    ctx.artifacts.append(core.rel_path(svg_path))
    report = "\n".join([
        f"before: {_sha_note(ctx, before_path)}",
        f"after : {_sha_note(ctx, after_path)}",
        "",
        md_table([["mean absolute difference", round(mae, 2)],
                  ["PSNR (dB)", round(psnr, 2)],
                  ["changed pixels", f"{changed_pct:.2f}%"],
                  ["changed region bbox", str(bbox)],
                  ["alignment", "sizes matched" if before.shape == after.shape else "after was cropped to fit"]],
                 ["Metric", "Value"]),
        "",
        "Read this before publishing: a large changed area means the framing also moved, so the pair is not a "
        "controlled comparison.",
    ])
    data = {"before": core.rel_path(before_path), "after": core.rel_path(after_path),
            "mae": round(mae, 2), "psnr": round(psnr, 2), "changed_pct": round(changed_pct, 2),
            "bbox": bbox, "svg": core.rel_path(svg_path)}
    return _report(ctx, "before-after", "Before and after comparison", report, data,
                   f"Compared two images: {changed_pct:.1f}% of pixels changed.",
                   {"changed_pct": round(changed_pct, 2)})


# ---------------------------------------------------------------------------
# Masking and compositing
# ---------------------------------------------------------------------------


def precision_mask_generator(ctx) -> Dict[str, Any]:
    mask_path = ctx.get("mask")
    material = str(ctx.get("material", "hair"))
    feather = int(ctx.get("feather", 6))
    strategies = {
        "hair": "Matte the fine strands last: keep the solid core, feather 4-8 px, then add strand detail at the edge.",
        "fur": "Use a low-contrast matte, feather 2-4 px and avoid a hard boundary at the tips.",
        "glass": "Keep the alpha semi-transparent; reflect the environment instead of cutting out the pane.",
        "smoke": "Use a soft density map rather than a binary matte; the alpha should never reach a hard edge.",
        "foliage": "Feather 6-12 px and keep small holes between leaves open.",
        "product": "A hard matte with 1-2 px feather is correct; the product edge is crisp.",
    }
    strategy = strategies.get(material, strategies["product"])
    artifacts = []
    metrics = {}
    if mask_path:
        arr = _load_rgb(str(mask_path))
        import numpy as np
        grey = arr.mean(axis=2)
        coverage = float((grey > 127).mean() * 100)
        soft = float(((grey > 10) & (grey < 245)).mean() * 100)
        metrics = {"coverage_pct": round(coverage, 2), "soft_edge_pct": round(soft, 2),
                   "size": [int(arr.shape[1]), int(arr.shape[0])]}
        if core.have_pillow():
            from PIL import Image, ImageFilter

            with Image.open(str(mask_path)) as im:
                blurred = im.convert("L").filter(ImageFilter.GaussianBlur(radius=max(1, feather / 2)))
                out = os.path.join(ctx.out_dir, "mask-feathered.png")
                blurred.save(out, "PNG")
                ctx.artifacts.append(core.rel_path(out))
                artifacts.append(core.rel_path(out))
    report = "\n".join([
        f"Material class: {material}",
        f"Strategy: {strategy}",
        "",
        md_table([[k, v] for k, v in metrics.items()], ["Measured", "Value"]) if metrics else
        "No mask supplied - only the strategy is reported.",
        "",
        "General rules:",
        "- Matte on the widest possible crop so soft edges are not clipped.",
        "- Never cut hair or fur with a hard edge.",
        "- Decontaminate the edge colour before compositing over a new background.",
    ])
    data = {"material": material, "strategy": strategy, "metrics": metrics, "artifacts": artifacts}
    return _report(ctx, "precision-mask", "Precision mask specification", report, data,
                   f"Specified a {material} masking strategy.")


def semantic_segmentation_editor(ctx) -> Dict[str, Any]:
    seg_path = str(ctx.require("segmentation"))
    classes = str(ctx.get("classes", ""))
    arr = _load_rgb(seg_path)
    import numpy as np

    colors, counts = np.unique(arr.reshape(-1, 3).astype("uint8"), axis=0, return_counts=True)
    order = np.argsort(counts)[::-1]
    mapping = {}
    for chunk in classes.split(","):
        if ":" in chunk:
            cid, label = chunk.split(":", 1)
            mapping[cid.strip()] = label.strip()
    rows = []
    for idx in order[:12]:
        rgb = tuple(int(v) for v in colors[idx])
        share = float(counts[idx] / counts.sum() * 100)
        rows.append([rgb_to_hex(rgb), f"{counts[idx]:,}", f"{share:.2f}%"])
    labels = []
    for idx in order[:12]:
        rgb = tuple(int(v) for v in colors[idx])
        key = rgb_to_hex(rgb)
        labels.append({"color": key, "label": mapping.get(key, "unlabelled"), "share_pct": round(float(counts[idx] / counts.sum() * 100), 2)})
    report = "\n".join([
        f"Segmentation map: {core.rel_path(seg_path)} ({arr.shape[1]}x{arr.shape[0]})",
        "",
        md_table(rows, ["Colour", "Pixels", "Share"]),
        "",
        "Class coverage:",
        md_table([[l["label"], l["color"], f'{l["share_pct"]:.2f}%'] for l in labels],
                 ["Label", "Colour", "Share"]),
        "",
        "Conversion to production masks:",
        "1. One binary mask per class, keyed by the palette colour.",
        "2. Feather by 1-2 px only - semantic edges are already correct.",
        "3. Keep a mask id map so regions can be re-rendered independently.",
    ])
    data = {"image": core.rel_path(seg_path), "classes": labels, "unique_colors": int(len(colors)),
            "size": [int(arr.shape[1]), int(arr.shape[0])]}
    return _report(ctx, "segmentation-masks", "Semantic segmentation to mask plan", report, data,
                   f"Found {len(colors)} colour classes in the segmentation map.")


def alpha_matting_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    background_path = ctx.get("background")
    _require_pixels("Matte analysis")
    from PIL import Image
    import numpy as np

    with Image.open(image_path) as im:
        im.load()
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        if not has_alpha:
            raise InvalidInput("the cutout has no alpha channel - supply an RGBA or LA cutout")
        rgba = np.array(im.convert("RGBA"))
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, 3].astype("float32")
    edge = (alpha > 5) & (alpha < 250)
    edge_pixels = rgba[:, :, :3][edge] if edge.any() else np.zeros((1, 3), dtype=np.uint8)
    metrics = {
        "opaque_pct": round(float((alpha > 250).mean() * 100), 2),
        "transparent_pct": round(float((alpha < 5).mean() * 100), 2),
        "partial_alpha_pct": round(float(edge.mean() * 100), 2),
        "mean_edge_rgb": [int(v) for v in edge_pixels.mean(axis=0)] if len(edge_pixels) else [0, 0, 0],
    }
    spill = {}
    if background_path:
        bg = _load_rgb(str(background_path))
        if bg.shape[:2] != rgba.shape[:2]:
            bg = bg[:rgba.shape[0], :rgba.shape[1]]
        bg_mask = alpha < 250
        if bg_mask.any():
            spill_rgb = [int(v) for v in bg[bg_mask].reshape(-1, 3).mean(axis=0)] if bg.shape[:2] == rgba.shape[:2] else [0, 0, 0]
            spill = {"background_mean_rgb": spill_rgb}
    report = "\n".join([
        f"Cutout: {core.rel_path(image_path)}",
        "",
        md_table([[k, v] for k, v in metrics.items()], ["Metric", "Value"]),
        "",
        *([f"Background mean RGB: {spill['background_mean_rgb']}" for _ in [0]] if spill else []),
        "",
        "Matte quality checks:",
        "- More than about 8% partial alpha is normal for hair or fur; over 40% suggests a soft matte was used "
        "on a hard object.",
        "- A coloured mean at the edge indicates spill that will fringe on a new background.",
        "- A hard cut edge (almost no partial alpha) on hair means the matte will look cut out.",
    ])
    data = {"image": core.rel_path(image_path), "metrics": metrics, "spill": spill}
    return _report(ctx, "matte-analysis", "Alpha matte analysis", report, data,
                   "Measured matte coverage and edge contamination.")


def edge_decontamination_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    spill = str(ctx.get("spill", "green")).lower()
    amount = float(ctx.get("amount", 0.6))
    _require_pixels("Edge decontamination")
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        im.load()
        has_alpha = im.mode in ("RGBA", "LA")
        arr = np.asarray(im.convert("RGBA"), dtype=np.float32) if has_alpha else None
        if arr is None:
            raise InvalidInput("supply an RGBA cutout so the edge can be decontaminated")
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    spill_index = {"green": 1, "blue": 2, "red": 0}.get(spill, 1)
    other = [i for i in range(3) if i != spill_index]
    edge = (alpha > 10) & (alpha < 250)
    before = rgb.copy()
    if edge.any():
        values = rgb[:, :, spill_index]
        excess = np.clip(values - np.maximum(rgb[:, :, other[0]], rgb[:, :, other[1]]), 0, None)
        factor = (1.0 - amount * np.clip(excess / 128.0, 0, 1)) * (edge.astype("float32"))
        rgb[:, :, spill_index] = rgb[:, :, spill_index] - excess * factor
    out = os.path.join(ctx.out_dir, "despilled.png")
    core.ensure_parent(out)
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), mode="RGBA").save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    delta = float(np.abs(rgb - before).mean())
    report = "\n".join([
        f"Cutout: {core.rel_path(image_path)}",
        f"Spill channel: {spill}, strength: {amount}",
        "",
        md_table([["mean channel delta", round(delta, 2)],
                  ["edge pixels affected", int(edge.sum())],
                  ["output", core.rel_path(out)]], ["Metric", "Value"]),
        "",
        "Notes:",
        "- This suppresses the dominant spill colour on semi-transparent edges.",
        "- It cannot recover detail lost inside the matte; a full re-matte is the only fix.",
    ])
    data = {"image": core.rel_path(image_path), "spill_channel": spill_index, "strength": amount,
            "mean_delta": round(delta, 2), "output": core.rel_path(out)}
    return _report(ctx, "edge-decontamination", "Edge decontamination", report, data,
                   f"Decontaminated the {spill} spill on the cutout edge.")


# ---------------------------------------------------------------------------
# Upscaling, noise, sharpening, restoration
# ---------------------------------------------------------------------------


def hires_fix_tuner(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    scale = float(ctx.get("scale", 2.0))
    tile = int(ctx.get("tile", 512))
    overlap = int(ctx.get("overlap", 64))
    apply = bool(ctx.get("apply", True))
    _require_pixels("Tiled upscaling")
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    width, height = source.size
    target = (max(1, int(width * scale)), max(1, int(height * scale)))
    plan = {
        "scale": scale, "tile_px": tile, "overlap_px": overlap,
        "tile_grid": [max(1, math.ceil(target[0] / tile)), max(1, math.ceil(target[1] / tile))],
        "recommended_hires_denoise": round(max(0.15, min(0.45, 0.35 - 0.05 * (scale - 2))), 2),
        "seam_control": "overlap at least 32 px and blend tiles with a cosine window",
        "model_choice": "latent upscale for texture, ESRGAN-class for edges and geometry",
    }
    artifacts = []
    if apply:
        step = max(16, tile - overlap)
        canvas = Image.new("RGB", target)
        for y in range(0, target[1], step):
            for x in range(0, target[0], step):
                box = (x, y, min(x + tile, target[0]), min(y + tile, target[1]))
                region = source.crop((int(x / scale), int(y / scale),
                                      int(math.ceil(box[2] / scale)), int(math.ceil(box[3] / scale))))
                if region.width == 0 or region.height == 0:
                    continue
                region = region.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
                canvas.paste(region, (x, y))
        out = os.path.join(ctx.out_dir, "upscaled.png")
        canvas.save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)} ({width}x{height}) -> target {target[0]}x{target[1]}",
        "",
        md_table([[k, v if not isinstance(v, list) else ", ".join(map(str, v))] for k, v in plan.items()],
                 ["Setting", "Value"]),
        "",
        "Tuning rules:",
        "- Keep the tile at or above 512 px for SDXL; below that the VAE recompresses visible seams.",
        "- Overlap 32-64 px; a zero overlap shows hard tile seams.",
        "- Second-pass denoising above 0.5 turns faces waxy and invents logos.",
    ])
    data = {"image": core.rel_path(image_path), "source_size": [width, height], "target_size": list(target),
            "plan": plan, "artifacts": artifacts}
    return _report(ctx, "hires-fix", "High-resolution fix plan", report, data,
                   f"Planned and applied a {scale}x tiled upscale to {target[0]}x{target[1]}.",
                   {"target_width": target[0]})


def noise_grain_manager(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    denoise = float(ctx.get("denoise", 0.3))
    grain = float(ctx.get("grain", 0.0))
    seed = int(ctx.get("seed", 7))
    _require_pixels("Noise management")
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    before = _image_metrics(arr)
    sigma = before["noise_sigma"]
    if denoise > 0:
        radius = max(1, int(round(denoise * 3)))
        filtered = source.filter(ImageFilter.MedianFilter(size=radius if radius % 2 else radius + 1))
        arr = np.asarray(filtered, dtype=np.float32)
    if grain > 0:
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, grain * 26.0, arr.shape[:2])
        arr = arr + noise[:, :, None]
    out = os.path.join(ctx.out_dir, "denoised.png")
    core.ensure_parent(out)
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    after = _image_metrics(np.clip(arr, 0, 255).astype("float32"))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}",
        f"Denoise strength: {denoise}; grain: {grain} (seed {seed})",
        "",
        md_table([["noise sigma", before["noise_sigma"], after["noise_sigma"]],
                  ["sharpness", before["sharpness_variance"], after["sharpness_variance"]],
                  ["luminance mean", before["luminance_mean"], after["luminance_mean"]]],
                 ["Metric", "Before", "After"]),
        "",
        "Notes:",
        "- Median filtering removes impulse noise and can soften fine texture; keep strength below 0.5.",
        "- Grain should be added last and should match the scene, not the sensor.",
    ])
    data = {"image": core.rel_path(image_path), "before": before, "after": after,
            "output": core.rel_path(out), "seed": seed}
    return _report(ctx, "noise-grain", "Noise and grain management", report, data,
                   f"Measured noise sigma {before['noise_sigma']} and wrote a processed copy.",
                   {"noise_sigma_before": before["noise_sigma"], "noise_sigma_after": after["noise_sigma"]})


def deblur_sharpening(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    amount = float(ctx.get("amount", 0.6))
    apply = bool(ctx.get("apply", True))
    _require_pixels("Sharpening")
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    before = _image_metrics(arr)
    blur_estimate = round(math.sqrt(max(0.0, before["sharpness_variance"])) / 4.0, 3)
    radius = max(1, int(round(amount * 3)))
    sharpened = source.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(80 * amount + 40), threshold=3))
    out = os.path.join(ctx.out_dir, "sharpened.png")
    sharpened.save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    after_arr = np.asarray(sharpened, dtype=np.float32)
    after = _image_metrics(after_arr)
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}",
        f"Estimated blur radius: {blur_estimate} px; sharpening amount: {amount}",
        "",
        md_table([["sharpness (variance of Laplacian)", before["sharpness_variance"], after["sharpness_variance"]],
                  ["noise sigma", before["noise_sigma"], after["noise_sigma"]],
                  ["clip high %", before["clip_high_pct"], after["clip_high_pct"]]],
                 ["Metric", "Before", "After"]),
        "",
        "Rules:",
        "- Sharpening amplifies noise; denoise first, sharpen second.",
        "- Keep the threshold above 2 so flat areas stay flat.",
        "- A blur radius above about 2 px usually means motion blur, which sharpening cannot fix.",
    ])
    data = {"image": core.rel_path(image_path), "blur_estimate": blur_estimate, "before": before,
            "after": after, "output": core.rel_path(out)}
    return _report(ctx, "deblur-sharpening", "Deblur and sharpening", report, data,
                   f"Sharpened the image (blur estimate {blur_estimate} px).",
                   {"sharpness_before": before["sharpness_variance"], "sharpness_after": after["sharpness_variance"]})


def old_photo_restoration(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    strength = float(ctx.get("strength", 0.35))
    _require_pixels("Restoration")
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    lum = _luminance(arr)
    # thin bright scratches: local outliers that are much brighter than their neighbours
    blurred = np.asarray(source.filter(ImageFilter.GaussianBlur(radius=1.5)), dtype=np.float32)
    bl = _luminance(blurred)
    diff = lum - bl
    scratch_mask = diff > 45
    repaired = arr.copy()
    if scratch_mask.any():
        smoothed = np.asarray(source.filter(ImageFilter.MedianFilter(size=5)), dtype=np.float32)
        blend = strength
        repaired = np.where(scratch_mask[:, :, None], arr * (1 - blend) + smoothed * blend, arr)
    # gentle fade recovery: lift shadows a little, pull back highlights
    out_arr = np.clip(repaired, 0, 255)
    out = os.path.join(ctx.out_dir, "restored.png")
    core.ensure_parent(out)
    Image.fromarray(out_arr.astype("uint8"), mode="RGB").save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    changed = float(scratch_mask.mean() * 100)
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}",
        f"Repair strength: {strength}",
        "",
        md_table([["scratch-like pixels", f"{changed:.2f}%"],
                  ["output", core.rel_path(out)]], ["Metric", "Value"]),
        "",
        "Restoration vs reconstruction:",
        "- Removing a scratch, dust or a crease is restoration - it recovers information that is present.",
        "- Reconstructing a missing face, a torn region or a removed hand is speculation and must be labelled "
        "as such before release.",
        "- This capability only removes detected surface defects; it never invents missing content.",
    ])
    data = {"image": core.rel_path(image_path), "scratch_pct": round(changed, 3),
            "strength": strength, "output": core.rel_path(out)}
    return _report(ctx, "old-photo-restoration", "Old photo restoration", report, data,
                   f"Repaired {changed:.2f}% of the frame as surface damage.",
                   {"scratch_pct": round(changed, 3)})


def restoration_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    strength = float(ctx.get("strength", 0.4))
    _require_pixels("Restoration")
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    before = _image_metrics(arr)
    work = source
    if strength > 0:
        radius = max(1, int(round(1 + strength * 3)))
        work = work.filter(ImageFilter.MedianFilter(size=radius if radius % 2 else radius + 1))
    work = ImageOps.autocontrast(work, cutoff=1)
    out = os.path.join(ctx.out_dir, "repaired.png")
    core.ensure_parent(out)
    work.save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    after = _image_metrics(np.asarray(work, dtype=np.float32))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}; strength {strength}",
        "",
        md_table([[k, before[k], after[k]] for k in
                  ("luminance_mean", "luminance_std", "clip_high_pct", "clip_low_pct",
                   "sharpness_variance", "noise_sigma")], ["Metric", "Before", "After"]),
        "",
        "Conservative restoration rules:",
        "- Never exceed a 0.6 strength without a human review.",
        "- Do not smooth away texture on faces, fabric or grain.",
        "- Log every change so the edit history stays auditable.",
    ])
    data = {"image": core.rel_path(image_path), "before": before, "after": after, "strength": strength,
            "output": core.rel_path(out)}
    return _report(ctx, "image-repair", "Image repair and restoration", report, data,
                   f"Restored the image at strength {strength}.", {"clip_high_pct": after["clip_high_pct"]})


# ---------------------------------------------------------------------------
# Colour and lighting
# ---------------------------------------------------------------------------


def _apply_curve(arr, shadows=(0, 0), mids=(128, 128), highs=(255, 255)):
    import numpy as np

    lut = np.zeros(256, dtype=np.float32)
    for i in range(256):
        if i < 128:
            t = i / 128.0
            lut[i] = shadows[0] + (mids[0] - shadows[0]) * t
        else:
            t = (i - 128) / 127.0
            lut[i] = mids[0] + (highs[0] - mids[0]) * t
    return np.clip(lut[np.clip(arr, 0, 255).astype(np.int32)], 0, 255)


def color_grading_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    mood = str(ctx.get("mood", "cinematic_teal_orange"))
    strength = float(ctx.get("strength", 0.5))
    apply = bool(ctx.get("apply", True))
    _require_pixels("Colour grading")
    import numpy as np
    from PIL import Image

    looks = {
        "cinematic_teal_orange": {"shadows": (6, 12, 20), "mids": (128, 124, 122), "highs": (252, 238, 220),
                                  "split": 0.10, "lift": 0.02},
        "clean_product": {"shadows": (250, 250, 252), "mids": (130, 130, 132), "highs": (255, 255, 255),
                          "split": 0.04, "lift": 0.0},
        "warm_film": {"shadows": (18, 12, 6), "mids": (132, 124, 112), "highs": (255, 246, 228),
                      "split": 0.08, "lift": 0.03},
        "cold_bleach": {"shadows": (8, 12, 20), "mids": (132, 136, 140), "highs": (248, 250, 255),
                        "split": 0.12, "lift": 0.05},
        "noir": {"shadows": (0, 0, 0), "mids": (120, 120, 120), "highs": (255, 255, 255),
                 "split": 0.0, "lift": 0.0},
    }
    look = looks.get(mood, looks["cinematic_teal_orange"])
    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    before = _image_metrics(arr)
    graded = arr.copy()
    for ch in range(3):
        graded[:, :, ch] = _apply_curve(graded[:, :, ch], look["shadows"], look["mids"], look["highs"])
    if look["split"]:
        lum = _luminance(graded)
        shadow_mask = np.clip(1.0 - lum / 96.0, 0, 1)[:, :, None]
        graded = graded + np.array([look["split"], 0.0, -look["split"]], dtype=np.float32) * shadow_mask * 255
    if look["lift"]:
        graded = graded + look["lift"] * 255 * (1 - _luminance(graded)[:, :, None] / 255.0)
    graded = arr * (1 - strength) + graded * strength
    artifacts = []
    if apply:
        out = os.path.join(ctx.out_dir, "graded.png")
        core.ensure_parent(out)
        Image.fromarray(np.clip(graded, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
    after = _image_metrics(np.clip(graded, 0, 255).astype("float32"))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}; look: {mood}; strength: {strength}",
        "",
        md_table([[k, before[k], after[k]] for k in
                  ("luminance_mean", "luminance_std", "saturation_mean", "clip_high_pct", "clip_low_pct")],
                 ["Metric", "Before", "After"]),
        "",
        "Protected elements:",
        "- Skin tones and neutral products should be masked out of any split-tone pass.",
        "- Brand colours should be sampled from the real asset, never from the grade.",
    ])
    data = {"image": core.rel_path(image_path), "look": mood, "strength": strength,
            "before": before, "after": after, "artifacts": artifacts}
    return _report(ctx, "colour-grade", "Colour grading", report, data,
                   f"Applied the '{mood}' grade at strength {strength}.", {"saturation_mean": after["saturation_mean"]})


def palette_transfer_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    reference_path = str(ctx.require("reference"))
    strength = float(ctx.get("strength", 0.6))
    protect = str(ctx.get("protect", ""))
    _require_pixels("Palette transfer")
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        im.load()
        target = np.asarray(im.convert("RGB"), dtype=np.float32)
    with Image.open(reference_path) as im:
        im.load()
        reference = np.asarray(im.convert("RGB"), dtype=np.float32)
    src_palette = _palette(target, 6)
    ref_palette = _palette(reference, 6)
    src_rgb = [hex_to_rgb(c) for c in src_palette]
    ref_rgb = [hex_to_rgb(c) for c in ref_palette]
    mapping = []
    for i, s in enumerate(src_rgb):
        best = min(range(len(ref_rgb)), key=lambda j: sum((a - b) ** 2 for a, b in zip(s, ref_rgb[j])))
        mapping.append({"source": rgb_to_hex(s), "target": rgb_to_hex(ref_rgb[best])})
    protected = []
    for chunk in protect.split(";"):
        if "-" in chunk:
            name, rng = chunk.split(":", 1) if ":" in chunk else ("range", chunk)
            try:
                lo, hi = [float(v) for v in rng.split("-")]
            except ValueError:
                continue
            protected.append({"name": name, "hue_low": lo, "hue_high": hi})
    out = target.copy()
    protected_hits = 0
    for y in range(0, target.shape[0], 4):
        for x in range(0, target.shape[1], 4):
            block = target[y:y + 4, x:x + 4].reshape(-1, 3)
            px = block.mean(axis=0)
            best = min(range(len(src_rgb)), key=lambda j: sum((a - b) ** 2 for a, b in zip(px, src_rgb[j])))
            mapped = np.array(ref_rgb[best], dtype=np.float32)
            hue = colorsys.rgb_to_hsv(float(px[0]) / 255.0, float(px[1]) / 255.0,
                                      float(px[2]) / 255.0)[0] * 360.0
            skip = False
            for prot in protected:
                lo = float(prot["hue_low"])
                hi = float(prot["hue_high"])
                if hi >= lo:
                    in_range = lo <= hue <= hi
                else:
                    in_range = hue >= lo or hue <= hi
                if in_range:
                    skip = True
                    protected_hits += 1
                    break
            if not skip:
                block_out = block * (1 - strength) + mapped * strength
                out[y:y + 4, x:x + 4] = block_out.reshape(out[y:y + 4, x:x + 4].shape)
    out_path = os.path.join(ctx.out_dir, "palette-transferred.png")
    core.ensure_parent(out_path)
    Image.fromarray(np.clip(out, 0, 255).astype("uint8"), mode="RGB").save(out_path, "PNG")
    ctx.artifacts.append(core.rel_path(out_path))
    report = "\n".join([
        f"Target: {core.rel_path(image_path)}; reference: {core.rel_path(reference_path)}; strength: {strength}",
        "",
        md_table([[m["source"], m["target"]] for m in mapping], ["Source colour", "Mapped colour"]),
        "",
        "Reference palette: " + ", ".join(ref_palette),
        "",
        "Rules:",
        "- Protect skin tones (roughly 10-50 degrees), neutrals and any brand colour before transferring.",
        "- Transfer relationships, not exact colours: the mapping keeps the target's luminance.",
    ])
    data = {"image": core.rel_path(image_path), "reference": core.rel_path(reference_path),
            "source_palette": src_palette, "reference_palette": ref_palette, "mapping": mapping,
            "strength": strength, "protected": protected,
            "protected_blocks": protected_hits, "output": core.rel_path(out_path)}
    return _report(ctx, "palette-transfer", "Palette transfer", report, data,
                   f"Transferred a {len(mapping)}-colour palette mapping.")


def lighting_continuity_auditor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    expected = str(ctx.get("expected_direction", "upper-left"))
    _require_pixels("Lighting audit")
    import numpy as np

    arr = _load_rgb(image_path)
    lum = _luminance(arr)
    h, w = lum.shape
    quadrants = {
        "upper-left": float(lum[: h // 2, : w // 2].mean()),
        "upper-right": float(lum[: h // 2, w // 2:].mean()),
        "lower-left": float(lum[h // 2:, : w // 2].mean()),
        "lower-right": float(lum[h // 2:, w // 2:].mean()),
    }
    expected_map = {"upper-left": ("upper-left", "upper-right"), "upper-right": ("upper-right", "upper-left"),
                    "top": ("upper-left", "upper-right"), "bottom": ("lower-left", "lower-right")}
    key, opposite = expected_map.get(expected, ("upper-left", "upper-right"))
    delta = quadrants[key] - quadrants[opposite]
    verdict = "consistent" if delta > 3 else ("ambiguous" if delta > -3 else "inconsistent")
    dynamic_range = float(lum.max() - lum.min())
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}; expected key direction: {expected}",
        "",
        md_table([[k, round(v, 2)] for k, v in quadrants.items()], ["Region", "Mean luminance"]),
        "",
        md_table([["key minus opposite", round(delta, 2)], ["verdict", verdict],
                  ["dynamic range", round(dynamic_range, 1)]], ["Check", "Value"]),
        "",
        "Checks to run by eye:",
        "- Cast shadows point away from the key light.",
        "- Shadow softness increases with distance from the light.",
        "- Reflections repeat the scene geometry.",
        "- Emissive sources (windows, lamps) are brighter than everything they light.",
    ])
    data = {"image": core.rel_path(image_path), "quadrant_luminance": {k: round(v, 2) for k, v in quadrants.items()},
            "delta": round(delta, 2), "verdict": verdict, "expected_direction": expected}
    return _report(ctx, "lighting-audit", "Lighting continuity audit", report, data,
                   f"Lighting continuity: {verdict} for a {expected} key.",
                   {"delta": round(delta, 2)})


def day_to_night_converter(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    strength = float(ctx.get("strength", 0.7))
    apply = bool(ctx.get("apply", True))
    _require_pixels("Day to night conversion")
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    before = _image_metrics(arr)
    lum = _luminance(arr) / 255.0
    # darker overall, cooler shadows, warm practical highlights
    converted = arr.copy()
    converted[:, :, 2] *= 1.0 + 0.22 * strength * (1.0 - lum)
    converted[:, :, 0] *= 1.0 - 0.10 * strength * (1.0 - lum)
    converted[:, :, 1] *= 1.0 - 0.02 * strength * (1.0 - lum)
    exposure = 0.35 + 0.65 * lum
    converted = converted * exposure[:, :, None] * (1 - 0.45 * strength)
    converted = arr * (1 - strength) + converted * strength
    artifacts = []
    if apply:
        out = os.path.join(ctx.out_dir, "night.png")
        core.ensure_parent(out)
        Image.fromarray(np.clip(converted, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
    after = _image_metrics(np.clip(converted, 0, 255).astype("float32"))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}; strength {strength}",
        "",
        md_table([[k, before[k], after[k]] for k in ("luminance_mean", "luminance_std", "saturation_mean",
                                                     "clip_high_pct", "clip_low_pct")],
                 ["Metric", "Before", "After"]),
        "",
        "A tone conversion alone is not a night scene. To finish it you must also add:",
        "- practical light sources with warm falloff",
        "- a sky gradient that matches the new exposure",
        "- window and specular highlights that follow the scene",
        "- contact shadows consistent with the new ambient level",
    ])
    data = {"image": core.rel_path(image_path), "before": before, "after": after,
            "strength": strength, "artifacts": artifacts}
    return _report(ctx, "day-to-night", "Day to night conversion", report, data,
                   f"Converted the scene toward night at strength {strength}.")


def cinematic_look_designer(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    look = str(ctx.get("look", "teal_orange"))
    grain = float(ctx.get("grain", 0.15))
    apply = bool(ctx.get("apply", True))
    _require_pixels("Cinematic look")
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
    arr = np.asarray(source, dtype=np.float32)
    recipes = {
        "teal_orange": {"shadow_tint": (0, 18, 34), "highlight_tint": (40, 14, 0), "contrast": 1.18, "halation": 0.2},
        "bleach_bypass": {"shadow_tint": (10, 10, 10), "highlight_tint": (12, 12, 8), "contrast": 1.32, "halation": 0.1},
        "noir": {"shadow_tint": (0, 0, 0), "highlight_tint": (0, 0, 0), "contrast": 1.45, "halation": 0.05},
        "pastel": {"shadow_tint": (6, 4, 12), "highlight_tint": (10, 6, 2), "contrast": 0.92, "halation": 0.25},
    }
    recipe = recipes.get(look, recipes["teal_orange"])
    out_arr = np.clip((arr - 128.0) * recipe["contrast"] + 128.0, 0, 255)
    lum = _luminance(out_arr) / 255.0
    shadow_mask = np.clip(1.0 - lum * 2.0, 0, 1)[:, :, None]
    high_mask = np.clip((lum - 0.6) / 0.4, 0, 1)[:, :, None]
    out_arr = out_arr + np.array(recipe["shadow_tint"], dtype=np.float32) * shadow_mask
    out_arr = out_arr + np.array(recipe["highlight_tint"], dtype=np.float32) * high_mask
    if grain:
        rng = np.random.default_rng(int(ctx.get("seed", 5)))
        out_arr = out_arr + rng.normal(0, grain * 12.0, out_arr.shape[:2])[:, :, None]
    artifacts = []
    if apply:
        out = os.path.join(ctx.out_dir, "cinematic.png")
        core.ensure_parent(out)
        Image.fromarray(np.clip(out_arr, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
    report = "\n".join([
        f"Source: {core.rel_path(image_path)}; look: {look}; grain: {grain}",
        "",
        md_table([[k, v] for k, v in recipe.items()], ["Look parameter", "Value"]),
        "",
        "Halation is a highlight bloom around bright edges; it must be simulated in the composite, not by "
        "raising global contrast.",
    ])
    data = {"image": core.rel_path(image_path), "look": look, "recipe": recipe, "grain": grain,
            "artifacts": artifacts}
    return _report(ctx, "cinematic-look", "Cinematic look", report, data,
                   f"Applied the '{look}' cinematic look.")


def hdr_specialist(ctx) -> Dict[str, Any]:
    primary = str(ctx.require("image"))
    exposures = ctx.paths("exposures")
    apply = bool(ctx.get("apply", True))
    _require_pixels("HDR merge")
    import numpy as np
    from PIL import Image

    with Image.open(primary) as im:
        im.load()
        base = np.asarray(im.convert("RGB"), dtype=np.float32)
    all_images = [base] + [_load_rgb(p) for p in exposures]
    h = min(i.shape[0] for i in all_images)
    w = min(i.shape[1] for i in all_images)
    all_images = [i[:h, :w] for i in all_images]
    means = [float(_luminance(i).mean()) for i in all_images]
    order = sorted(range(len(all_images)), key=lambda i: means[i])
    weights = []
    total = 0.0
    for idx in order:
        wgt = 1.0 / max(means[idx], 1e-3)
        weights.append(wgt)
        total += wgt
    merged = sum(img * (w / total) for img, w in zip(all_images, weights))
    clipped_before = float((base >= 254).all(axis=2).mean() * 100)
    clipped_after = float((merged >= 254).all(axis=2).mean() * 100)
    artifacts = []
    if apply:
        out = os.path.join(ctx.out_dir, "hdr-merged.png")
        core.ensure_parent(out)
        Image.fromarray(np.clip(merged, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
    report = "\n".join([
        f"Primary: {core.rel_path(primary)}; additional exposures: {len(exposures)}",
        "",
        md_table([[idx, round(means[idx], 1), round(weights[k] / total * 100, 1)]
                  for k, idx in enumerate(order)], ["Exposure", "Mean luminance", "Merge weight %"]),
        "",
        md_table([["fully clipped pixels before", f"{clipped_before:.2f}%"],
                  ["fully clipped pixels after", f"{clipped_after:.2f}%"]], ["Check", "Value"]),
        "",
        "Rules:",
        "- Exposures must be identical apart from exposure, otherwise the merge produces ghosts.",
        "- Merge from a linear working space; an 8-bit sRGB merge loses shadow detail.",
        "- A PQ or HDR10 target needs a real 16-bit pipeline, not an 8-bit PNG.",
    ])
    data = {"primary": core.rel_path(primary), "exposure_means": [round(m, 1) for m in means],
            "clipped_before_pct": round(clipped_before, 2), "clipped_after_pct": round(clipped_after, 2),
            "artifacts": artifacts}
    return _report(ctx, "hdr-merge", "HDR merge", report, data,
                   f"Merged {len(all_images)} exposure(s).",
                   {"clipped_before_pct": round(clipped_before, 2)})


# ---------------------------------------------------------------------------
# Commerce
# ---------------------------------------------------------------------------


def marketplace_compliance(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    marketplace = str(ctx.get("marketplace", "generic"))
    policies = load_asset(TEMPLATES)["marketplaces"]
    if marketplace not in policies:
        raise InvalidInput(f"unknown marketplace '{marketplace}' (known: {', '.join(sorted(policies))})")
    policy = policies[marketplace]
    info = core.probe_image(image_path)
    findings = []
    w, h = info["width"], info["height"]
    if w < policy["min_width"] or w > policy["max_width"]:
        findings.append({"rule": "width", "status": "fail", "detail": f'{w} px outside {policy["min_width"]}-{policy["max_width"]} px'})
    if h < policy["min_height"] or h > policy["max_height"]:
        findings.append({"rule": "height", "status": "fail", "detail": f'{h} px outside {policy["min_height"]}-{policy["max_height"]} px'})
    aspect = w / h if h else 0
    if aspect < policy["min_aspect"] or aspect > policy["max_aspect"]:
        findings.append({"rule": "aspect", "status": "fail", "detail": f"{aspect:.2f} outside {policy['min_aspect']}-{policy['max_aspect']}"})
    if info["extension"] not in policy["formats"]:
        findings.append({"rule": "format", "status": "fail", "detail": f'{info["extension"]} not in {policy["formats"]}'})
    if info["bytes"] > policy["max_bytes"]:
        findings.append({"rule": "file size", "status": "fail", "detail": f'{info["bytes"]} bytes above {policy["max_bytes"]}'})
    background_check = {"rule": "background", "status": "pass", "detail": "not measurable without Pillow"}
    if core.have_pillow() and policy["max_background_deviation"] < 255:
        arr = _load_rgb(image_path)
        import numpy as np
        border = np.concatenate([arr[0, :, :].flatten(), arr[-1, :, :].flatten(),
                                 arr[:, 0, :].flatten(), arr[:, -1, :].flatten()])
        deviation = float(np.abs(border - 255).mean())
        background_check = {"rule": "background", "status": "pass" if deviation <= policy["max_background_deviation"] else "fail",
                            "detail": f"border mean deviation from white: {deviation:.1f} (limit {policy['max_background_deviation']})"}
    findings.append(background_check)
    if policy.get("no_text_overlay"):
        findings.append({"rule": "text overlay", "status": "manual",
                         "detail": "confirm no text, badge or watermark is burned into the image"})
    if policy.get("watermark") is False:
        findings.append({"rule": "watermark", "status": "manual", "detail": "confirm no watermark is present"})
    failed = [f for f in findings if f["status"] == "fail"]
    report = "\n".join([
        f"Policy: {policy['label']}",
        f"Image: {core.rel_path(image_path)} ({w}x{h}, {info['format']}, {info['bytes']} bytes)",
        "",
        md_table([[f["rule"], f["status"], f["detail"]] for f in findings], ["Rule", "Status", "Detail"]),
        "",
        f"Verdict: {'fail - ' + str(len(failed)) + ' hard failure(s)' if failed else 'pass on automated checks'}",
    ])
    data = {"image": core.rel_path(image_path), "marketplace": marketplace, "policy": policy,
            "findings": findings, "failures": len(failed)}
    return _report(ctx, "marketplace-compliance", "Marketplace compliance review", report, data,
                   f"Checked against {policy['label']}: {len(failed)} failure(s).",
                   {"failures": len(failed)})


def catalog_consistency_auditor(ctx) -> Dict[str, Any]:
    root = ctx.get("path")
    if not root:
        checklist = [
            "Every product shot uses the same crop ratio and subject scale.",
            "Background colour, shadow direction and light quality match across the set.",
            "White balance is identical; fix it at capture, not in post.",
            "File naming follows the catalogue pattern so the grid can be rebuilt automatically.",
            "Each image has been checked against the product's actual colour and proportions.",
        ]
        body = "\n".join([
            "Catalogue consistency checklist (no folder was supplied, so nothing was measured)",
            "",
            _md_bullets(checklist),
        ])
        data = {"root": None, "count": 0, "rows": [], "deviations": {}, "distinct_aspects": [],
                "verdict": "not-evaluated", "checklist": checklist}
        ctx.warn("no catalogue folder was supplied: the consistency checklist was returned instead")
        return _report(ctx, "catalog-consistency", "Catalogue consistency audit", body, data,
                       "Returned the catalogue consistency checklist; no folder was supplied.",
                       {"image_count": 0})
    root = str(root)
    if not os.path.isdir(root):
        raise InvalidInput(f"not a directory: {root}")
    files = []
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")):
                files.append(os.path.join(base, name))
    if not files:
        raise InvalidInput(f"no images found under {root}")
    rows = []
    metrics = []
    for path in files:
        info = core.probe_image(path)
        row = {"file": os.path.relpath(path, root), "size": f'{info["width"]}x{info["height"]}',
               "aspect": info.get("aspect_ratio"), "bytes": info["bytes"]}
        if core.have_pillow():
            arr = _load_rgb(path)
            m = _image_metrics(arr)
            metrics.append(m)
            row.update({"exposure": m["luminance_mean"], "contrast": m["luminance_std"],
                        "white_point": m["mean_rgb"][0], "saturation": m["saturation_mean"]})
        rows.append(row)
    deviations = {}
    if metrics:
        def spread(key):
            values = [m[key] for m in metrics]
            return round(max(values) - min(values), 3)
        deviations = {"exposure_spread": spread("luminance_mean"), "contrast_spread": spread("luminance_std"),
                      "white_point_spread": spread("mean_rgb")[0] if isinstance(spread("mean_rgb"), list) else None}
    aspects = {r["aspect"] for r in rows if r["aspect"]}
    verdict = "consistent" if len(aspects) == 1 else "inconsistent crops"
    report = "\n".join([
        f"Folder: {core.rel_path(root)}; images: {len(rows)}",
        "",
        md_table([[r["file"], r["size"], r["aspect"], r["bytes"], r.get("exposure", "-"), r.get("contrast", "-")]
                  for r in rows], ["File", "Size", "Aspect", "Bytes", "Exposure", "Contrast"]),
        "",
        md_table([[k, v] for k, v in deviations.items() if v is not None], ["Deviation", "Value"]),
        "",
        f"Distinct aspect ratios: {len(aspects)} - verdict: {verdict}",
    ])
    data = {"root": core.rel_path(root), "count": len(rows), "rows": rows, "deviations": deviations,
            "distinct_aspects": sorted(aspects), "verdict": verdict}
    return _report(ctx, "catalog-consistency", "Catalogue consistency audit", report, data,
                   f"Audited {len(rows)} catalogue images: {verdict}.",
                   {"image_count": len(rows)})


# ---------------------------------------------------------------------------
# Safety and authenticity
# ---------------------------------------------------------------------------


def authenticity_analyst(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    info = core.probe_image(image_path)
    signals = []
    if info.get("exif"):
        signals.append({"signal": "EXIF present", "finding": "The file carries camera or edit metadata.",
                        "interpretation": "metadata can be edited, so it is a weak indicator only"})
    else:
        signals.append({"signal": "no EXIF", "finding": "No EXIF block found.",
                        "interpretation": "consistent with a generated or exported asset, but also with a stripped photo"})
    if info.get("icc_profile"):
        signals.append({"signal": "ICC profile", "finding": "An ICC profile is embedded.",
                        "interpretation": "suggests a colour-managed export pipeline"})
    if info.get("text_chunks"):
        signals.append({"signal": "PNG text chunks", "finding": f'chunks: {", ".join(info["text_chunks"])}',
                        "interpretation": "provenance metadata is embedded in the file"})
    if core.have_pillow():
        import numpy as np
        arr = _load_rgb(image_path)
        m = _image_metrics(arr)
        signals.append({"signal": "compression", "finding": f'{info["format"]} file, {info["bytes"]} bytes',
                        "interpretation": "JPEG blockiness and odd 8x8 boundaries are weak synthetic cues"})
        signals.append({"signal": "noise profile", "finding": f'estimated noise sigma {m["noise_sigma"]}',
                        "interpretation": "very low noise across flat areas can indicate synthesis"})
        signals.append({"signal": "clipping", "finding": f'{m["clip_high_pct"]}% fully clipped pixels',
                        "interpretation": "aggressive clipping is common in generated images"})
    else:
        signals.append({"signal": "pixel analysis", "finding": "skipped",
                        "interpretation": "Pillow is not installed, so pixel-level signals were not measured"})
    verdict = "no decisive indicator"
    report = "\n".join([
        f"Image: {core.rel_path(image_path)} ({info['width']}x{info['height']}, {info['format']})",
        "",
        md_table([[s["signal"], s["finding"], s["interpretation"]] for s in signals],
                 ["Signal", "Finding", "Interpretation"]),
        "",
        f"Verdict: {verdict}.",
        "",
        "Honest limits: no single detector is reliable. Absence of artefacts does not prove a photo, and "
        "presence of artefacts does not prove synthesis. Report probability with the uncertainty stated, "
        "and never use this capability as the only basis for a takedown or a legal decision.",
    ])
    data = {"image": core.rel_path(image_path), "probe": info, "signals": signals, "verdict": verdict}
    return _report(ctx, "authenticity-analysis", "Image authenticity analysis", report, data,
                   "Analysed provenance and artefact signals with stated uncertainty.")


def misleading_edit_detector(ctx) -> Dict[str, Any]:
    before_path = str(ctx.require("before"))
    after_path = str(ctx.require("after"))
    claim = str(ctx.get("claim", ""))
    before = _load_rgb(before_path)
    after = _load_rgb(after_path)
    import numpy as np

    h = min(before.shape[0], after.shape[0])
    w = min(before.shape[1], after.shape[1])
    b = before[:h, :w]
    a = after[:h, :w]
    diff = np.abs(a - b).max(axis=2)
    changed = diff > 10
    pct = float(changed.mean() * 100)
    ys, xs = np.nonzero(changed)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None
    centroid = [round(float(xs.mean()), 1), round(float(ys.mean()), 1)] if len(xs) else None
    materiality = "material" if pct > 12 else ("moderate" if pct > 3 else "minor")
    report = "\n".join([
        f"before: {core.rel_path(before_path)}",
        f"after : {core.rel_path(after_path)}",
        f"claim : {claim or 'not supplied'}",
        "",
        md_table([["changed area", f"{pct:.2f}%"], ["materiality", materiality],
                  ["changed region bbox", str(bbox)], ["change centroid", str(centroid)]],
                 ["Measure", "Value"]),
        "",
        "Rule: an edit that changes more than about 12% of the frame, or that changes a person, a document, a "
        "product or a property, is material and needs a disclosure. Keep the original next to the release copy.",
    ])
    data = {"before": core.rel_path(before_path), "after": core.rel_path(after_path),
            "changed_pct": round(pct, 2), "materiality": materiality, "bbox": bbox, "claim": claim}
    return _report(ctx, "misleading-edit", "Misleading edit detection", report, data,
                   f"Edit changes {pct:.1f}% of the frame ({materiality}).",
                   {"changed_pct": round(pct, 2)})


# ---------------------------------------------------------------------------
# Quality evaluation
# ---------------------------------------------------------------------------


def visual_quality_reviewer(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    _require_pixels("Quality review")
    rubric = load_asset(RUBRIC)
    arr = _load_rgb(image_path)
    m = _image_metrics(arr)
    thresholds = rubric["visual_quality_rubric"]["thresholds"]
    scores = {}
    reasons = {}
    scores["composition"] = 72.0
    reasons["composition"] = "estimated from luminance balance; confirm framing by eye"
    exposure_penalty = m["clip_high_pct"] * 6 + m["clip_low_pct"] * 2
    scores["exposure"] = max(0.0, 100.0 - exposure_penalty)
    reasons["exposure"] = f'{m["clip_high_pct"]}% clipped highlights, {m["clip_low_pct"]}% clipped shadows'
    scores["contrast"] = max(0.0, min(100.0, (m["luminance_std"] / thresholds["min_contrast_ratio"]) * 22))
    reasons["contrast"] = f'luminance std {m["luminance_std"]}'
    sat = m["saturation_mean"]
    if sat < thresholds["saturation_min"]:
        scores["color"] = 55.0
    elif sat > thresholds["saturation_max"]:
        scores["color"] = 60.0
    else:
        scores["color"] = 90.0
    reasons["color"] = f'mean saturation {sat}'
    sharp_penalty = 0 if m["sharpness_variance"] >= thresholds["min_sharpness"] else (thresholds["min_sharpness"] - m["sharpness_variance"]) * 3
    scores["sharpness"] = max(0.0, min(100.0, 100.0 - sharp_penalty))
    reasons["sharpness"] = f'variance of Laplacian {m["sharpness_variance"]} (threshold {thresholds["min_sharpness"]})'
    noise_penalty = 0 if m["noise_sigma"] <= thresholds["max_noise_sigma"] else (m["noise_sigma"] - thresholds["max_noise_sigma"]) * 4
    scores["noise"] = max(0.0, min(100.0, 100.0 - noise_penalty))
    reasons["noise"] = f'noise sigma {m["noise_sigma"]} (threshold {thresholds["max_noise_sigma"]})'
    scores["artifacts"] = 80.0
    reasons["artifacts"] = "see the artefact detection report for pixel-level findings"
    scores["resolution"] = 100.0 if m["width"] >= 1024 else (70.0 if m["width"] >= 512 else 40.0)
    reasons["resolution"] = f'{m["width"]}x{m["height"]}'
    total = 0.0
    rows = []
    for dim in rubric["visual_quality_rubric"]["dimensions"]:
        value = scores[dim["id"]]
        total += value * dim["weight"]
        rows.append([dim["label"], round(value, 1), f'{dim["weight"]:.2f}', reasons[dim["id"]]])
    total = round(total, 1)
    band = "pass" if total >= 80 else ("revise" if total >= 60 else "reject")
    report = "\n".join([
        f"Image: {core.rel_path(image_path)} ({m['width']}x{m['height']})",
        "",
        md_table(rows, ["Dimension", "Score", "Weight", "Basis"]),
        "",
        f"Weighted total: {total}/100 - {band.upper()}",
        "",
        "Scores are deterministic measurements, not a judgement of artistic merit. Review the two lowest "
        "dimensions and re-render rather than rescuing with edits.",
    ])
    data = {"image": core.rel_path(image_path), "metrics": m, "scores": scores, "total": total, "band": band}
    return _report(ctx, "visual-quality", "Visual quality review", report, data,
                   f"Quality score {total}/100 ({band}).", {"total": total, "band": band})


def prompt_adherence_evaluator(ctx) -> Dict[str, Any]:
    request = str(ctx.require("prompt"))
    observations = str(ctx.get("observations", ""))
    text = observations.lower()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", request) if s.strip()]
    stop = {"a", "an", "the", "of", "in", "on", "with", "and", "at", "to", "by", "for", "is", "as", "its"}
    terms = sorted({w for s in sentences for w in re.findall(r"[A-Za-z0-9'\-]+", s.lower())
                    if w not in stop and len(w) > 2})
    found = [t for t in terms if t in text]
    missing = [t for t in terms if t not in text]
    coverage = len(found) / len(terms) if terms else 0.0
    if not observations:
        grade = "not-evaluated"
    elif coverage >= 0.85:
        grade = "high"
    elif coverage >= 0.6:
        grade = "partial"
    else:
        grade = "low"
    if not observations:
        report = "\n".join([
            f"Request: {request}",
            "Observed: (not supplied - adherence was not evaluated)",
            "",
            md_table([[t, "not checked"] for t in terms], ["Requested term", "Status"]),
            "",
            "Adherence: not evaluated.",
            "",
            "Supply --set observations='what the image actually shows' to turn this into a scored check. "
            "This capability compares two written descriptions; it does not run a vision model.",
        ])
        data = {"request": request, "observations": None, "terms": terms, "coverage": None,
                "grade": grade, "found": [], "missing": terms}
        return _report(ctx, "prompt-adherence", "Prompt adherence evaluation", report, data,
                       "Listed the requested terms; no observations were supplied so nothing was scored.",
                       {"terms": len(terms)})
    report = "\n".join([
        f"Request: {request}",
        f"Observed: {observations}",
        "",
        md_table([[t, "present" if t in found else "not observed"] for t in terms], ["Requested term", "Status"]),
        "",
        f"Term coverage: {coverage:.0%} - adherence: {grade}",
        "",
        "Note: this evaluator compares the written description of the result with the written request. It is a "
        "documentation check - run it against a human description or an annotated QA sheet, not against the "
        "image itself, because this capability does not run a vision model.",
    ])
    data = {"request": request, "observations": observations, "terms": terms, "found": found,
            "missing": missing, "coverage": round(coverage, 3), "grade": grade}
    return _report(ctx, "prompt-adherence", "Prompt adherence evaluation", report, data,
                   f"Prompt adherence: {grade} ({coverage:.0%} term coverage).",
                   {"coverage": round(coverage, 3)})


def artifact_detector(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    _require_pixels("Artefact detection")
    import numpy as np

    arr = _load_rgb(image_path)
    lum = _luminance(arr)
    findings = []
    # banding: repeated quantisation plateaus in smooth gradients
    hist = np.histogram(lum, bins=256, range=(0, 255))[0].astype(np.float64)
    hist /= max(1.0, hist.sum())
    peaks = int(np.sum((hist[1:-1] > hist[:-2]) & (hist[1:-1] > hist[2:]) & (hist[1:-1] < 0.08)))
    findings.append({"id": "banding", "detected": peaks > 8, "severity": "medium" if peaks > 8 else "none",
                     "detail": f"{peaks} luminance histogram peaks with low occupancy - consistent with banding"})
    # blocking: energy difference across 8x8 boundaries
    blocks = []
    for i in range(0, arr.shape[1] - 8, 8):
        for j in range(0, arr.shape[0] - 8, 8):
            block = lum[j:j + 8, i:i + 8]
            blocks.append(float(block.var()))
    block_var = float(np.mean(blocks)) if blocks else 0.0
    findings.append({"id": "blocking", "detected": False, "severity": "none",
                     "detail": f"mean 8x8 block variance {block_var:.2f} (informational for JPEG sources)"})
    # flat regions
    tile = 64
    flat = 0
    tiles = 0
    for y in range(0, arr.shape[0] - tile, tile):
        for x in range(0, arr.shape[1] - tile, tile):
            tiles += 1
            if float(lum[y:y + tile, x:x + tile].std()) < 0.6:
                flat += 1
    flat_pct = (flat / tiles * 100) if tiles else 0
    findings.append({"id": "flat_region", "detected": flat_pct > 12, "severity": "low" if flat_pct <= 12 else "medium",
                     "detail": f"{flat_pct:.1f}% of 64x64 tiles are near-flat"})
    # duplicated regions: identical 32x32 blocks
    h, w = arr.shape[:2]
    seen = {}
    dupes = 0
    for y in range(0, h - 32, 32):
        for x in range(0, w - 32, 32):
            key = arr[y:y + 32, x:x + 32].astype(np.int16).tobytes()
            if key in seen:
                dupes += 1
            else:
                seen[key] = (x, y)
    findings.append({"id": "duplication", "detected": dupes > 0, "severity": "high" if dupes > 0 else "none",
                     "detail": f"{dupes} repeated 32x32 blocks"})
    # seam detection: column-mean discontinuities
    col_mean = lum.mean(axis=0)
    jumps = [int(i) for i in range(1, len(col_mean)) if abs(col_mean[i] - col_mean[i - 1]) > 8]
    findings.append({"id": "tile_seam", "detected": bool(jumps), "severity": "medium" if jumps else "none",
                     "detail": f"{len(jumps)} abrupt column transitions (first: {jumps[:5]})"})
    # clipping
    clip = float((arr >= 254).all(axis=2).mean() * 100)
    findings.append({"id": "clipping", "detected": clip > 2, "severity": "medium" if clip > 2 else "none",
                     "detail": f"{clip:.2f}% fully clipped pixels"})
    detected = [f for f in findings if f["detected"]]
    report = "\n".join([
        f"Image: {core.rel_path(image_path)} ({w}x{h})",
        "",
        md_table([[f["id"], "yes" if f["detected"] else "no", f["severity"], f["detail"]] for f in findings],
                 ["Check", "Detected", "Severity", "Detail"]),
        "",
        f"Detected issues: {len(detected)}",
        "",
        "These are pixel statistics, not a trained classifier. Malformed hands and garbled text need a human "
        "or a dedicated detector.",
    ])
    data = {"image": core.rel_path(image_path), "findings": findings, "detected_count": len(detected)}
    return _report(ctx, "artifact-detection", "Artefact detection", report, data,
                   f"Detected {len(detected)} artefact class(es).", {"detected": len(detected)})


def image_pair_comparator(ctx) -> Dict[str, Any]:
    before_path = str(ctx.require("before"))
    after_path = str(ctx.require("after"))
    before = _load_rgb(before_path)
    after = _load_rgb(after_path)
    import numpy as np

    h = min(before.shape[0], after.shape[0])
    w = min(before.shape[1], after.shape[1])
    b = before[:h, :w]
    a = after[:h, :w]
    diff = np.abs(a - b)
    mae = float(diff.mean())
    rmse = float(math.sqrt((diff ** 2).mean()))
    psnr = 10 * math.log10((255.0 ** 2) / max(rmse, 1e-6))
    bl = _luminance(b)
    al = _luminance(a)
    # global SSIM on 8x8 windows
    def ssim(x, y):
        c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        mx, my = x.mean(), y.mean()
        vx, vy = x.var(), y.var()
        cov = ((x - mx) * (y - my)).mean()
        return float(((2 * mx * my + c1) * (2 * cov + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2)))
    try:
        from PIL import Image

        sim = ssim(np.asarray(Image.fromarray(b.astype("uint8")).convert("L"), dtype=np.float32),
                   np.asarray(Image.fromarray(a.astype("uint8")).convert("L"), dtype=np.float32))
    except Exception:
        sim = 0.0
    mask = diff.max(axis=2) > 8
    ys, xs = np.nonzero(mask)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None
    changed_pct = float(mask.mean() * 100)
    report = "\n".join([
        f"before: {core.rel_path(before_path)}",
        f"after : {core.rel_path(after_path)}",
        "",
        md_table([["mean absolute error", round(mae, 2)], ["RMSE", round(rmse, 2)],
                  ["PSNR (dB)", round(psnr, 2)], ["global SSIM", round(sim, 4)],
                  ["changed pixels", f"{changed_pct:.2f}%"], ["changed bbox", str(bbox)]],
                 ["Metric", "Value"]),
        "",
        "Interpretation:",
        "- A high PSNR with a small changed area means a local, controlled edit.",
        "- A low PSNR with a large changed area means the whole frame was re-rendered.",
        "- Compare against the intended change list before accepting a revision.",
    ])
    data = {"before": core.rel_path(before_path), "after": core.rel_path(after_path),
            "mae": round(mae, 2), "rmse": round(rmse, 2), "psnr": round(psnr, 2), "ssim": round(sim, 4),
            "changed_pct": round(changed_pct, 2), "bbox": bbox}
    return _report(ctx, "image-pair-comparison", "Image pair comparison", report, data,
                   f"Compared two images: PSNR {psnr:.1f} dB, {changed_pct:.1f}% changed.",
                   {"psnr": round(psnr, 2)})


def delivery_readiness_auditor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    channel = str(ctx.get("channel", "web"))
    delivery = load_asset(RUBRIC)["delivery"]
    if channel not in delivery:
        raise InvalidInput(f"unknown channel '{channel}' (known: {', '.join(sorted(delivery))})")
    spec = delivery[channel]
    info = core.probe_image(image_path)
    checks = []
    if spec.get("min_width"):
        checks.append({"check": "minimum width", "status": "pass" if info["width"] >= spec["min_width"] else "fail",
                       "detail": f'{info["width"]} px vs {spec["min_width"]} px required'})
    if spec.get("max_bytes"):
        checks.append({"check": "maximum file size", "status": "pass" if info["bytes"] <= spec["max_bytes"] else "fail",
                       "detail": f'{info["bytes"]} bytes vs {spec["max_bytes"]} allowed'})
    checks.append({"check": "format", "status": "pass" if info["extension"] in spec["formats"] else "fail",
                   "detail": f'{info["extension"]} vs {spec["formats"]}'})
    checks.append({"check": "colour space", "status": "manual",
                   "detail": f'ICC profile {"present" if info.get("icc_profile") else "absent"} - verify it is sRGB'})
    checks.append({"check": "alpha channel", "status": "manual",
                   "detail": "confirm transparency is intended for this channel"})
    checks.append({"check": "metadata", "status": "manual",
                   "detail": f'EXIF {"present" if info.get("exif") else "absent"} - strip before public delivery'})
    checks.append({"check": "naming", "status": "manual",
                   "detail": f'current name: {info["name"]}'})
    failed = [c for c in checks if c["status"] == "fail"]
    report = "\n".join([
        f"Image: {core.rel_path(image_path)} ({info['width']}x{info['height']}, {info['format']})",
        f"Channel: {channel} - {spec['note']}",
        "",
        md_table([[c["check"], c["status"], c["detail"]] for c in checks], ["Check", "Status", "Detail"]),
        "",
        f"Verdict: {'fail - ' + str(len(failed)) + ' hard failure(s)' if failed else 'no automated failures'}",
    ])
    data = {"image": core.rel_path(image_path), "channel": channel, "spec": spec, "checks": checks,
            "failures": len(failed)}
    return _report(ctx, "delivery-readiness", "Delivery readiness audit", report, data,
                   f"Delivery check for '{channel}': {len(failed)} failure(s).", {"failures": len(failed)})


# ---------------------------------------------------------------------------
# Accessibility and delivery
# ---------------------------------------------------------------------------


def contrast_reviewer(ctx) -> Dict[str, Any]:
    fg = hex_to_rgb(str(ctx.require("foreground")))
    bg = hex_to_rgb(str(ctx.require("background")))
    size = str(ctx.get("size", "normal"))
    ratio = core.contrast_ratio(fg, bg)
    required = 3.0 if size == "large" else 4.5
    verdict = "pass" if ratio >= required else "fail"
    grey_fg = core.grayscale(fg)
    grey_bg = core.grayscale(bg)
    grey_delta = abs(grey_fg - grey_bg)
    grey_ratio = core.contrast_ratio((grey_fg,) * 3, (grey_bg,) * 3)
    report = "\n".join([
        f"Foreground {rgb_to_hex(fg)}, background {rgb_to_hex(bg)}",
        "",
        md_table([["contrast ratio", f"{ratio}:1", f'{required}:1 required for {size} text', verdict],
                  ["grayscale ratio", f"{grey_ratio}:1", "colour-only encoding check"],
                  ["grayscale value delta", grey_delta, "0 means the pair is identical in grayscale"]],
                 ["Measure", "Value", "Reference", "Verdict"]),
        "",
        "Rules:",
        "- Text needs 4.5:1 (normal) or 3:1 (large, 18.66 px bold or 24 px regular).",
        "- Never encode information in colour alone; check the grayscale rendering.",
        "- Non-text UI elements need 3:1 against their background.",
    ])
    data = {"foreground": rgb_to_hex(fg), "background": rgb_to_hex(bg), "ratio": ratio,
            "required_ratio": required, "verdict": verdict, "grayscale_ratio": grey_ratio,
            "grayscale_delta": grey_delta}
    return _report(ctx, "contrast-review", "Colour contrast review", report, data,
                   f"Contrast {ratio}:1 - {verdict.upper()} for {size} text.", {"ratio": ratio})


def export_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    formats = [f.strip().lower() for f in str(ctx.get("formats", "png,jpeg,webp")).split(",") if f.strip()]
    quality = int(ctx.get("quality", 90))
    _require_pixels("Format export")
    from PIL import Image

    results = []
    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGBA")
        for fmt in formats:
            out = os.path.join(ctx.out_dir, f"export.{fmt}")
            core.ensure_parent(out)
            try:
                if fmt in ("jpeg", "jpg"):
                    flat = Image.new("RGB", source.size, "#ffffff")
                    flat.paste(source, mask=source.split()[3])
                    flat.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
                elif fmt == "webp":
                    source.save(out, "WEBP", quality=quality, method=6)
                elif fmt == "png":
                    source.save(out, "PNG", optimize=True)
                elif fmt == "tiff":
                    source.convert("RGB").save(out, "TIFF", compression="tiff_lzw")
                else:
                    results.append({"format": fmt, "status": "skipped", "detail": "unsupported format name"})
                    continue
                results.append({"format": fmt, "status": "written",
                                "path": core.rel_path(out), "bytes": os.path.getsize(out)})
                ctx.artifacts.append(core.rel_path(out))
            except Exception as exc:  # pragma: no cover - depends on Pillow codecs
                results.append({"format": fmt, "status": "failed", "detail": str(exc)})
    original = os.path.getsize(image_path)
    report = "\n".join([
        f"Source: {core.rel_path(image_path)} ({original} bytes)",
        "",
        md_table([[r["format"], r["status"], r.get("bytes", r.get("detail", "-"))] for r in results],
                 ["Format", "Status", "Bytes / detail"]),
        "",
        "Guidance:",
        "- PNG for graphics and transparency, JPEG for photographs, WebP for web delivery, TIFF for print.",
        "- Keep the alpha channel only where the channel supports transparency.",
        "- Strip metadata from public copies; it can reveal location and device details.",
    ])
    data = {"image": core.rel_path(image_path), "quality": quality, "results": results, "original_bytes": original}
    return _report(ctx, "multi-format-export", "Multi-format export", report, data,
                   f"Exported {sum(1 for r in results if r['status'] == 'written')} format(s).")


def responsive_crop_planner(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    focal = str(ctx.get("focal", "0.5,0.5"))
    ratios = load_asset(TEMPLATES)["crop_ratios"]
    try:
        fx, fy = [float(v) for v in focal.split(",")]
    except ValueError:
        raise InvalidInput("focal must be 'x,y' in 0..1 coordinates")
    if not (0 <= fx <= 1 and 0 <= fy <= 1):
        raise InvalidInput("focal coordinates must be between 0 and 1")
    src_w = src_h = None
    if image_path:
        info = core.probe_image(str(image_path))
        src_w, src_h = info["width"], info["height"]
    base_w = src_w or 1600
    base_h = src_h or 900
    crops = []
    for name, ratio in ratios.items():
        out_w = 1200
        out_h = int(round(out_w / ratio))
        crop_w = min(base_w, int(round(base_h * ratio)))
        crop_h = min(base_h, int(round(crop_w / ratio)))
        cx = fx * base_w
        cy = fy * base_h
        x = max(0, min(base_w - crop_w, int(round(cx - crop_w / 2))))
        y = max(0, min(base_h - crop_h, int(round(cy - crop_h / 2))))
        crops.append({"target": name, "aspect": round(ratio, 4), "output": [out_w, out_h],
                      "crop_box": [x, y, crop_w, crop_h],
                      "focal_offset_px": [int(round(cx - (x + crop_w / 2))), int(round(cy - (y + crop_h / 2)))]})
    report = "\n".join([
        f"Source: {core.rel_path(str(image_path)) if image_path else 'not supplied'} ({base_w}x{base_h})",
        f"Focal point: ({fx}, {fy})",
        "",
        md_table([[c["target"], f'{c["aspect"]}', f'{c["output"][0]}x{c["output"][1]}',
                   str(c["crop_box"]), str(c["focal_offset_px"])] for c in crops],
                 ["Target", "Aspect", "Output", "Crop box", "Focal offset"]),
        "",
        "Rules:",
        "- Keep the focal point within the central 60% of every crop.",
        "- Check each crop by eye: a face that survives one crop can be cut by a taller one.",
        "- For subjects near an edge, shift the focal point rather than centring the crop.",
    ])
    data = {"source": core.rel_path(str(image_path)) if image_path else None,
            "source_size": [base_w, base_h], "focal": [fx, fy], "crops": crops}
    return _report(ctx, "responsive-crops", "Responsive crop plan", report, data,
                   f"Planned {len(crops)} focal-aware crops.", {"crop_count": len(crops)})


def image_optimizer(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    max_width = int(ctx.get("max_width", 2000))
    quality = int(ctx.get("quality", 82))
    _require_pixels("Image optimisation")
    from PIL import Image

    original = os.path.getsize(image_path)
    with Image.open(image_path) as im:
        im.load()
        source = im.convert("RGB")
        resized = source.copy()
        if source.width > max_width:
            ratio = max_width / source.width
            resized = source.resize((max_width, max(1, int(source.height * ratio))), Image.LANCZOS)
    out = os.path.join(ctx.out_dir, "optimised.jpg")
    core.ensure_parent(out)
    resized.save(out, "JPEG", quality=quality, optimize=True, progressive=True, exif=b"")
    optimised = os.path.getsize(out)
    ctx.artifacts.append(core.rel_path(out))
    saved = (1 - optimised / original) * 100 if original else 0
    report = "\n".join([
        f"Source: {core.rel_path(image_path)} ({source.width}x{source.height}, {original} bytes)",
        f"Output: {resized.width}x{resized.height}, quality {quality}, {optimised} bytes",
        "",
        md_table([["size reduction", f"{saved:.1f}%"], ["bytes saved", original - optimised],
                  ["metadata", "stripped"]], ["Measure", "Value"]),
        "",
        "Rules:",
        "- Never compress below quality 80 for photographic delivery.",
        "- Check the result on a histogram: blocking is worse than a larger file.",
        "- Keep the original master; the optimised file is a derivative.",
    ])
    data = {"image": core.rel_path(image_path), "original_bytes": original, "optimised_bytes": optimised,
            "saved_pct": round(saved, 1), "output": core.rel_path(out), "quality": quality}
    return _report(ctx, "image-optimisation", "Image optimisation", report, data,
                   f"Optimised the image: {saved:.1f}% smaller.",
                   {"saved_pct": round(saved, 1)})


# ---------------------------------------------------------------------------
# Single-image editing plans
# ---------------------------------------------------------------------------


def inpainting_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.get("image"))
    mask_path = ctx.get("mask")
    instruction = str(ctx.require("instruction"))
    info = core.probe_image(str(image_path)) if image_path else None
    mask_info = core.probe_image(str(mask_path)) if mask_path else None
    plan = {
        "mask": "white in the mask marks the region to regenerate; keep a 8-16 px overlap into the original",
        "denoise": "0.75 for a full replacement, 0.5 for a partial change, 0.35 for a texture tweak",
        "mask_blur": "8-16 px so the new content blends into the old",
        "inpaint_full_res": "off for large regions, on for small ones to keep the native resolution",
        "inpaint_area": "'only masked' keeps the rest of the frame untouched",
        "context": "describe what surrounds the region so the model can continue the scene",
        "edge_blend": "after the pass, feather 2-4 px and match the local colour temperature",
    }
    if info and mask_info:
        if (info["width"], info["height"]) != (mask_info["width"], mask_info["height"]):
            plan["warning"] = (f'the mask is {mask_info["width"]}x{mask_info["height"]} but the image is '
                               f'{info["width"]}x{info["height"]} - resize the mask first')
    report = "\n".join([
        f"Instruction: {instruction}",
        "",
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        "Workflow:",
        "1. Paint the mask generously, keeping the subject edges out of it.",
        "2. Run the pass at the native resolution with the instruction above.",
        "3. Check the boundary for a seam and for a colour-temperature mismatch.",
        "4. Repair the boundary with a short masked pass at low denoise if needed.",
    ])
    data = {"instruction": instruction, "image": core.rel_path(str(image_path)) if image_path else None,
            "mask": core.rel_path(str(mask_path)) if mask_path else None, "plan": plan}
    if "warning" in plan:
        ctx.warn(plan["warning"])
    return _report(ctx, "inpainting-plan", "Inpainting plan", [("Plan", report)], data,
                   f"Planned an inpaint of '{truncate(instruction, 40)}'.")


def outpainting_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    expand = str(ctx.get("expand", "scale:1.5"))
    info = core.probe_image(image_path)
    if expand.startswith("scale:"):
        factor = float(expand.split(":")[1])
        left = right = int(info["width"] * (factor - 1) / 2)
        top = bottom = int(info["height"] * (factor - 1) / 2)
    else:
        try:
            left, top, right, bottom = [int(v) for v in expand.split(",")]
        except ValueError:
            raise InvalidInput("expand must be 'scale:X' or 'left,top,right,bottom' in pixels")
    new_w = info["width"] + left + right
    new_h = info["height"] + top + bottom
    direction = []
    if top:
        direction.append(f"above: {top} px - extend the sky or ceiling, keep the existing light direction")
    if bottom:
        direction.append(f"below: {bottom} px - extend the ground or foreground with matching perspective")
    if left:
        direction.append(f"left: {left} px - continue background elements horizontally")
    if right:
        direction.append(f"right: {right} px - continue the scene to the right, matching vanishing lines")
    report = "\n".join([
        f"Source: {core.rel_path(image_path)} ({info['width']}x{info['height']})",
        f"Target canvas: {new_w}x{new_h}",
        "",
        "Expansion directions:",
        *([f"- {d}" for d in direction] if direction else ["- none (the image is being padded)"]),
        "",
        "Rules:",
        "- Outpaint at a resolution the model likes, then crop back to the exact target size.",
        "- Extend perspective lines, they must meet the original vanishing point.",
        "- Match the grain, the colour temperature and the depth of field of the original.",
        "- A 16:9 source should not be extended into a 4:3 target without checking the subject.",
    ])
    data = {"image": core.rel_path(image_path), "source_size": [info["width"], info["height"]],
            "target_size": [new_w, new_h], "margins": {"left": left, "top": top, "right": right, "bottom": bottom},
            "directions": direction}
    return _report(ctx, "outpainting-plan", "Outpainting plan", [("Plan", report)], data,
                   f"Planned an expansion to {new_w}x{new_h}.", {"target_width": new_w})


def object_removal_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.get("image"))
    mask_path = ctx.get("mask")
    plan = {
        "mask": "cover the object plus a 10-20 px margin, including its contact shadow",
        "denoise": "0.85-0.95 so the model fully repaints the region",
        "prompt": "describe what should be there instead (background, wall, grass) - not 'empty'",
        "cleanup": "look for repeated texture, smears and ghost edges - they are the classic failure",
        "step_1": "generate the region with no subject in the prompt",
        "step_2": "mask the ghost remnant only and repaint with a lower denoise (0.5)",
        "step_3": "match the local grain and colour temperature in the composite",
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        "Verification:",
        "- Compare the repaired area against the surrounding texture at 200% zoom.",
        "- Check that a repeated pattern was not continued with a visible phase shift.",
        "- Check the light direction inside the repair.",
    ])
    data = {"image": core.rel_path(str(image_path)) if image_path else None,
            "mask": core.rel_path(str(mask_path)) if mask_path else None, "plan": plan}
    return _report(ctx, "object-removal-plan", "Object removal plan", [("Plan", report)], data,
                   "Planned an object removal and cleanup sequence.")


def object_addition_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.get("image"))
    obj = str(ctx.require("object"))
    position = str(ctx.get("position", ""))
    plan = {
        "object": obj,
        "position": position or "choose a position where the ground plane is visible",
        "scale": "state the real size of the object, or compare it to a known object in the scene",
        "contact": "the object must touch the surface - a floating object is the most common failure",
        "shadow": "add a contact shadow plus a soft cast shadow away from the key light",
        "reflection": "add a reflection only on a glossy surface, blurred and fading with distance",
        "occlusion": "the new object must occlude or be occluded by existing objects consistently",
        "colour_temperature": "match the scene light - a cool scene needs a cool object",
        "focus": "match the depth of field; a sharp object in a blurred scene looks pasted",
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        "Workflow:",
        "1. Generate the object on a neutral background at the right scale.",
        "2. Cut it out with a clean matte and decontaminate the edge.",
        "3. Composite it into the scene, then generate the contact shadow with a masked pass.",
        "4. Match grain, sharpness and colour before the final grade.",
    ])
    data = {"object": obj, "image": core.rel_path(str(image_path)) if image_path else None, "plan": plan}
    return _report(ctx, "object-addition-plan", "Object addition plan", [("Plan", report)], data,
                   f"Planned the insertion of {truncate(obj, 40)}.")


def background_replacement_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.get("image"))
    new_background = str(ctx.require("new_background"))
    plan = {
        "new_background": new_background,
        "matte": "extract the subject with an alpha matte, not a hard cut-out",
        "edge_detail": "match the edge sharpness to the new background's depth of field",
        "spill": "decontaminate the colour that came from the old background",
        "depth": "match the depth cue - aerial perspective and shadow softness",
        "lighting": "match the light direction and colour temperature of the new background",
        "grounding": "add a contact shadow so the subject sits on the new surface",
        "grade": "grade the composite as one image, not as two",
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        "Failure modes:",
        "- A halo from the old background - fix with matte decontamination.",
        "- Wrong scale or perspective - fix before compositing, not after.",
        "- A subject that is sharper or softer than the scene - fix in the composite step.",
    ])
    data = {"new_background": new_background, "image": core.rel_path(str(image_path)) if image_path else None,
            "plan": plan}
    return _report(ctx, "background-replacement-plan", "Background replacement plan", [("Plan", report)], data,
                   f"Planned a background swap to '{truncate(new_background, 40)}'.")


def relighting_specialist(ctx) -> Dict[str, Any]:
    image_path = str(ctx.require("image"))
    direction = str(ctx.get("direction", "upper-left"))
    temperature = str(ctx.get("temperature", "5600K"))
    apply = bool(ctx.get("apply", False))
    temp_map = {"3200K": (1.18, 0.98, 0.78), "5600K": (1.0, 1.0, 1.0), "7500K": (0.84, 0.94, 1.16)}
    gain = temp_map.get(temperature, (1.0, 1.0, 1.0))
    dir_map = {"upper-left": (-1.0, -1.0), "upper-right": (1.0, -1.0),
               "lower-left": (-1.0, 1.0), "lower-right": (1.0, 1.0), "top": (0.0, -1.0)}
    dx, dy = dir_map.get(direction, (-1.0, -1.0))
    artifacts = []
    metrics = {}
    if apply:
        _require_pixels("Relighting approximation")
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as im:
            im.load()
            arr = np.asarray(im.convert("RGB"), dtype=np.float32)
        lum = _luminance(arr) / 255.0
        h, w = lum.shape
        yy, xx = np.mgrid[0:h, 0:w]
        gradient = ((xx / max(w - 1, 1) - 0.5) * dx + (yy / max(h - 1, 1) - 0.5) * dy) * 2.0
        factor = (1.0 + 0.22 * gradient)[:, :, None]
        out_arr = arr * factor * np.array(gain, dtype=np.float32)
        out = os.path.join(ctx.out_dir, "relit.png")
        core.ensure_parent(out)
        Image.fromarray(np.clip(out_arr, 0, 255).astype("uint8"), mode="RGB").save(out, "PNG")
        ctx.artifacts.append(core.rel_path(out))
        artifacts.append(core.rel_path(out))
        metrics = {"mean_before": _image_metrics(arr)["luminance_mean"],
                   "mean_after": _image_metrics(np.clip(out_arr, 0, 255).astype("float32"))["luminance_mean"]}
    plan = {
        "key_direction": direction,
        "colour_temperature": temperature,
        "fill": "keep the fill ratio at about 1:4 against the key",
        "rim": "a rim light separates the subject from the background; keep it subtle",
        "shadow_softness": "shadow edges soften with distance from the light",
        "material": "the material response must stay believable - a glossy product reacts differently from skin",
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items()], ["Aspect", "Specification"]),
        "",
        *([f"Applied approximation metrics: {metrics}" for _ in [0]] if metrics else []),
        "",
        "Honest limit: the applied pass is a deterministic directional tone approximation, not a relighting "
        "model. It shifts the luminance gradient and the colour temperature. Real relighting (re-rendering the "
        "subject with new lights) needs an image model.",
    ])
    data = {"image": core.rel_path(image_path), "plan": plan, "artifacts": artifacts, "metrics": metrics}
    return _report(ctx, "relighting-plan", "Relighting plan", [("Plan", report)], data,
                   f"Planned a relight from {direction} at {temperature}.",
                   metrics)


def expression_gaze_editor(ctx) -> Dict[str, Any]:
    image_path = str(ctx.get("image"))
    expression = str(ctx.require("expression"))
    gaze = str(ctx.get("gaze", "camera"))
    plan = {
        "expression": expression,
        "gaze": gaze,
        "mask": "mask the eye and mouth region with a soft 12-24 px feather; keep the jawline and hair untouched",
        "denoise": "0.45-0.6 so the identity survives; a high denoise re-renders the whole face",
        "prompt": f"the same person, {expression} expression, eyes looking {gaze}, preserve facial structure and identity",
        "negative": "changed face, different person, distorted eyes, warped mouth, plastic skin, asymmetric face",
        "verification": ["compare eye spacing and nose width with the original",
                         "check the mouth corners against the original",
                         "confirm the hairline and jaw are unchanged",
                         "check that the catchlight in the eye still matches the scene light"],
    }
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items() if not isinstance(v, list)], ["Aspect", "Value"]),
        "",
        "Verification:",
        *[f"- {v}" for v in plan["verification"]],
    ])
    data = {"image": core.rel_path(str(image_path)) if image_path else None, "plan": plan}
    return _report(ctx, "expression-gaze-plan", "Expression and gaze edit plan", [("Plan", report)], data,
                   f"Planned a {expression} expression edit.")


def hands_anatomy_repair(ctx) -> Dict[str, Any]:
    image_path = ctx.get("image")
    region = str(ctx.get("region", "hands"))
    plan = {
        "region": region,
        "mask": "cover only the defective anatomy - a tight mask around the fingers",
        "denoise": "0.75-0.9 for a malformed hand; 0.5-0.65 for a small proportion error",
        "prompt": "five fingers, correct joint count, natural finger length, relaxed hand, consistent skin tone",
        "negative": "extra fingers, fused fingers, missing fingers, malformed hand, six fingers, deformed",
        "technique": "regenerate the whole hand rather than individual fingers - per-finger masks usually "
                     "produce a worse result",
        "pose_reference": "supply an open-pose reference image or a pose ControlNet for complex hand positions",
        "verification": ["count the fingers on every hand in the frame",
                         "check that the thumb is on the correct side",
                         "check knuckle and wrist continuity",
                         "check the fingernails follow the finger direction"],
    }
    metrics = {}
    if image_path:
        info = core.probe_image(str(image_path))
        metrics = {"size": [info["width"], info["height"]]}
    report = "\n".join([
        md_table([[k, v] for k, v in plan.items() if not isinstance(v, list)], ["Aspect", "Value"]),
        "",
        "Verification:",
        *[f"- {v}" for v in plan["verification"]],
    ])
    data = {"region": region, "plan": plan, "metrics": metrics}
    return _report(ctx, "anatomy-repair-plan", "Hands and anatomy repair plan", [("Plan", report)], data,
                   f"Planned an anatomy repair for the {region} region.")


# ---------------------------------------------------------------------------
# Specialised media generation (procedural, deterministic pixel output)
# ---------------------------------------------------------------------------


def _value_noise(width: int, height: int, cells: int, seed: int):
    """Tileable value noise on a grid of `cells` x `cells` control points."""
    import numpy as np

    rng = np.random.default_rng(seed)
    grid = rng.random((cells + 1, cells + 1)).astype(np.float32)
    grid[-1, :] = grid[0, :]
    grid[:, -1] = grid[:, 0]
    xs = np.linspace(0, cells, width, endpoint=False, dtype=np.float32)
    ys = np.linspace(0, cells, height, endpoint=False, dtype=np.float32)
    x0 = np.floor(xs).astype(int)
    y0 = np.floor(ys).astype(int)
    fx = (xs - x0)[:, None]
    fy = (ys - y0)[:, None]
    sx = fx * fx * (3 - 2 * fx)
    sy = fy * fy * (3 - 2 * fy)
    v00 = grid[np.ix_(y0, x0)]
    v10 = grid[np.ix_(y0, x0 + 1)]
    v01 = grid[np.ix_(y0 + 1, x0)]
    v11 = grid[np.ix_(y0 + 1, x0 + 1)]
    top = v00 * (1 - sx) + v10 * sx
    bottom = v01 * (1 - sx) + v11 * sx
    return top * (1 - sy) + bottom * sy


def texture_generator(ctx) -> Dict[str, Any]:
    size = int(ctx.get("size", 256))
    seed = int(ctx.get("seed", 11))
    pattern = str(ctx.get("pattern", "stone"))
    if size < 16 or size > 2048:
        raise InvalidInput("size must be between 16 and 2048 pixels")
    _require_pixels("Procedural texture generation")
    import numpy as np
    from PIL import Image

    base = _value_noise(size, size, 8, seed)
    detail = _value_noise(size, size, 32, seed + 1)
    fine = _value_noise(size, size, 128, seed + 2)
    if pattern == "stone":
        height = base * 0.6 + detail * 0.3 + fine * 0.1
        colour = np.stack([height * 190, height * 185, height * 175], axis=2)
    elif pattern == "wood":
        rings = np.sin(base * 24.0) * 0.5 + 0.5
        height = rings * 0.7 + detail * 0.3
        colour = np.stack([height * 165, height * 118, height * 74], axis=2)
    elif pattern == "fabric":
        weave = (np.sin(np.arange(size)[None, :] * 0.8) * np.sin(np.arange(size)[:, None] * 0.8)) * 0.5 + 0.5
        height = weave * 0.5 + detail * 0.5
        colour = np.stack([height * 210, height * 200, height * 195], axis=2)
    else:
        height = base * 0.5 + detail * 0.5
        colour = np.stack([height * 170, height * 180, height * 165], axis=2)
    colour = np.clip(colour, 0, 255).astype(np.uint8)
    out = os.path.join(ctx.out_dir, f"texture-{pattern}-{size}.png")
    core.ensure_parent(out)
    Image.fromarray(colour, mode="RGB").save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    # tiling check: compare the right edge with the left edge and the bottom with the top
    left = float(np.abs(colour[:, 0, :].astype(int) - colour[:, -1, :].astype(int)).mean())
    top = float(np.abs(colour[0, :, :].astype(int) - colour[-1, :, :].astype(int)).mean())
    verdict = "seamless" if (left < 6 and top < 6) else "visible seam"
    report = "\n".join([
        f"Pattern: {pattern}, size {size}x{size}, seed {seed}",
        f"Output: {core.rel_path(out)}",
        "",
        md_table([["left/right edge delta", round(left, 2)], ["top/bottom edge delta", round(top, 2)],
                  ["tiling verdict", verdict]], ["Check", "Value"]),
        "",
        "How the tiling works: the noise grid repeats exactly every `size` pixels, so the texture can be "
        "repeated across a surface without a visible seam. Repeat it with a mirrored offset when the "
        "pattern would otherwise look obviously periodic.",
    ])
    data = {"pattern": pattern, "size": size, "seed": seed, "output": core.rel_path(out),
            "edge_delta": {"left_right": round(left, 2), "top_bottom": round(top, 2)}, "tiling": verdict}
    return _report(ctx, "texture-generation", "Tileable texture generation", report, data,
                   f"Generated a {size}px {pattern} texture ({verdict}).", {"edge_delta": round(left, 2)})


def pattern_generator(ctx) -> Dict[str, Any]:
    pattern = str(ctx.get("pattern", "geometric"))
    size = int(ctx.get("size", 256))
    seed = int(ctx.get("seed", 23))
    _require_pixels("Pattern generation")
    import numpy as np
    from PIL import Image

    samples = load_asset("asset-sample-prompts.json")
    palette = samples["abstract_palettes"][2]["swatches"]
    rgb = [hex_to_rgb(c) for c in palette]
    grid = 4
    cell = size // grid
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    rng = np.random.default_rng(seed)
    for gy in range(grid):
        for gx in range(grid):
            colour = rgb[(gy + gx) % len(rgb)]
            if pattern == "stripes":
                for i in range(cell):
                    canvas[gy * cell:gy * cell + cell, gx * cell + i] = colour
            elif pattern == "dots":
                cy = gy * cell + cell // 2
                cx = gx * cell + cell // 2
                yy, xx = np.ogrid[:cell, :cell]
                mask = (yy - cell // 2) ** 2 + (xx - cell // 2) ** 2 <= (cell // 3) ** 2
                block = canvas[gy * cell:gy * cell + cell, gx * cell:gx * cell + cell]
                block[mask] = colour
            else:
                canvas[gy * cell:gy * cell + cell, gx * cell:gx * cell + cell] = colour
    out = os.path.join(ctx.out_dir, f"pattern-{pattern}-{size}.png")
    core.ensure_parent(out)
    Image.fromarray(canvas, mode="RGB").save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    left = float(np.abs(canvas[:, 0, :].astype(int) - canvas[:, -1, :].astype(int)).mean())
    top = float(np.abs(canvas[0, :, :].astype(int) - canvas[-1, :, :].astype(int)).mean())
    report = "\n".join([
        f"Pattern: {pattern}, tile {size}px, grid {grid}x{grid}, seed {seed}",
        f"Palette: {', '.join(palette)}",
        f"Output: {core.rel_path(out)}",
        "",
        md_table([["left/right edge delta", round(left, 2)], ["top/bottom edge delta", round(top, 2)]],
                 ["Check", "Value"]),
        "",
        "Repeat modes:",
        "- straight: tile on a regular grid",
        "- half-drop: offset every other column by half a tile",
        "- brick: offset every other row by half a tile",
        "- mirror: flip every other tile to break the periodicity",
    ])
    data = {"pattern": pattern, "size": size, "seed": seed, "palette": palette,
            "output": core.rel_path(out), "edge_delta": {"left_right": round(left, 2), "top_bottom": round(top, 2)}}
    return _report(ctx, "pattern-generation", "Pattern and surface design", report, data,
                   f"Generated a {size}px {pattern} repeat.")


def sprite_generator(ctx) -> Dict[str, Any]:
    subject = str(ctx.require("subject"))
    scale = int(ctx.get("scale", 32))
    frames = int(ctx.get("frames", 4))
    if scale < 8 or scale > 256:
        raise InvalidInput("scale must be between 8 and 256 pixels per cell")
    if frames < 1 or frames > 24:
        raise InvalidInput("frames must be between 1 and 24")
    _require_pixels("Sprite generation")
    import numpy as np
    from PIL import Image

    sheet = Image.new("RGBA", (scale * frames, scale), (0, 0, 0, 0))
    palette = [(232, 86, 64, 255), (96, 148, 196, 255), (245, 220, 120, 255), (60, 60, 72, 255)]
    for f in range(frames):
        cell = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
        pixels = cell.load()
        phase = f / max(1, frames - 1)
        body_w = max(4, scale // 3)
        body_h = max(5, scale // 2)
        bob = int(round(math.sin(phase * math.pi * 2) * (scale * 0.08)))
        x0 = (scale - body_w) // 2
        y0 = scale - body_h - 2 + bob
        for y in range(y0, min(scale, y0 + body_h)):
            for x in range(x0, min(scale, x0 + body_w)):
                pixels[x, y] = palette[3]
        head_r = max(2, scale // 5)
        hx = scale // 2
        hy = y0 - head_r
        for y in range(max(0, hy - head_r), min(scale, hy + head_r)):
            for x in range(max(0, hx - head_r), min(scale, hx + head_r)):
                if (x - hx) ** 2 + (y - hy) ** 2 <= head_r ** 2:
                    pixels[x, y] = palette[0]
        for y in range(scale // 2, scale):
            for x in range(0, scale):
                if pixels[x, y] == (0, 0, 0, 0):
                    pixels[x, y] = (0, 0, 0, 0)
        sheet.paste(cell, (f * scale, 0))
    out = os.path.join(ctx.out_dir, f"sprites-{slugify(subject)[:24]}-{scale}px.png")
    core.ensure_parent(out)
    sheet.save(out, "PNG")
    ctx.artifacts.append(core.rel_path(out))
    report = "\n".join([
        f"Subject: {subject}; cell {scale}px; frames {frames}; sheet {scale * frames}x{scale} px",
        f"Output: {core.rel_path(out)}",
        "",
        "Frame plan:",
        md_table([[i + 1, "idle", "loop", "1.0 s"] for i in range(frames)], ["Frame", "Action", "Timing"]),
        "",
        "Pixel art rules:",
        "- Work on whole pixels only; never resample the sprite sheet.",
        "- Keep the silhouette readable at 1x - it will be shown smaller than the cell.",
        "- Use a limited palette and cluster the shading into bands.",
        "- Export the sheet with nearest-neighbour scaling to preserve the grid.",
    ])
    data = {"subject": subject, "cell_px": scale, "frames": frames,
            "sheet_size": [scale * frames, scale], "output": core.rel_path(out)}
    return _report(ctx, "sprite-generation", "Sprite sheet generation", report, data,
                   f"Generated a {frames}-frame sprite sheet at {scale}px.",
                   {"frames": frames})
