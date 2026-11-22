"""imagekit edit: editing, retouching, cutouts, compositing, grading, restoration,
format conversion, print preparation and motion preparation.

All operations are deterministic local Pillow work. Nothing is uploaded, no model
is called and no destructive change happens without an explicit output directory.
Destructive or overwriting operations require --approve.
"""

from __future__ import annotations

import glob
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageEnhance, ImageOps, ImageStat

import imagekit_core as core

MAX_BLUR = 40


# --------------------------------------------------------------------------
# shared input helpers
# --------------------------------------------------------------------------


def collect_inputs(a, ctx, exts=None):
    """Resolve --input/--inputs into an ordered, bounded list of existing files."""
    exts = exts or core.IMAGE_EXTS
    raw = []
    for item in (getattr(a, "inputs", None) or []):
        raw.extend(str(item).split(","))
    if getattr(a, "input", None):
        raw.extend(str(a.input).split(","))
    paths = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        p = Path(item).expanduser()
        if any(ch in item for ch in "*?["):
            matches = sorted(glob.glob(item))
            if not matches:
                raise core.input_error(f"Pattern matched no files: {item}")
            paths.extend(Path(m) for m in matches)
        elif p.is_dir():
            paths.extend(sorted(q for q in p.iterdir() if q.suffix.lower() in exts))
        else:
            paths.append(core.resolve_input(p))
    if not paths:
        raise core.usage_error("No input images supplied.", "Use --input FILE or --inputs a.png,b.png or a directory.")
    limit = ctx.config.limit("max_batch_items", 500)
    if len(paths) > limit:
        raise core.usage_error(f"{len(paths)} inputs exceeds limits.max_batch_items ({limit})",
                              "Process the batch in parts.")
    return paths


def parse_box(text, img=None):
    parts = [p for p in str(text).replace(" ", "").split(",") if p]
    if len(parts) != 4:
        raise core.usage_error(f"Invalid box '{text}'", "Use x,y,width,height in pixels.")
    try:
        x, y, w, h = (int(float(p)) for p in parts)
    except ValueError:
        raise core.usage_error(f"Invalid box '{text}'", "Use integer x,y,width,height.")
    if w <= 0 or h <= 0:
        raise core.usage_error("Box width and height must be positive")
    if img is not None:
        x, y = max(0, x), max(0, y)
        w, h = min(w, img.width - x), min(h, img.height - y)
        if w <= 1 or h <= 1:
            raise core.input_error("Box falls outside the image")
    return (x, y, w, h)


def emit_image(ctx, img, project, content, fmt, version=1, status="review", quality=None, dpi=None, description=""):
    fmt = fmt.lower().lstrip(".")
    project = core.slugify(project)
    if str(content).lower().startswith(project):
        content = str(content)[len(project):].lstrip("-_") or content
    name = core.build_filename({"project": project, "content": content, "dimensions": (img.width, img.height),
                                "version": version, "status": status}, fmt, ctx.config.naming)
    path = ctx.out_path(name, "." + fmt)
    meta = {"Software": f"imagekit {core.VERSION}", "Description": description}
    if fmt == "png":
        core.save_image(ctx, img, path, fmt, quality=quality or 92, dpi=dpi or (72, 72),
                        pnginfo=core.build_pnginfo(meta))
    else:
        core.save_image(ctx, img, path, fmt, quality=quality or 92, dpi=dpi or (72, 72),
                        exif=core.build_exif({"ImageDescription": description,
                                              "Software": meta["Software"]}))
    return path


def project_name(a, brief=None):
    return getattr(a, "project", None) or (brief or {}).get("project") or "edit"


# --------------------------------------------------------------------------
# basic geometric editing
# --------------------------------------------------------------------------


def op_edit(ctx, a):
    inputs = collect_inputs(a, ctx)
    max_dim = ctx.config.limit("max_output_dimension", 8192)
    ops_done = []
    outputs = []
    for src in inputs:
        img = core.open_image(src, ctx)
        if getattr(a, "auto_orient", False):
            img = ImageOps.exif_transpose(img)
            ops_done.append("auto_orient")
        if getattr(a, "rotate", None):
            angle = float(a.rotate)
            if not -360 <= angle <= 360:
                raise core.usage_error("--rotate must be between -360 and 360 degrees")
            img = img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            ops_done.append(f"rotate {angle}")
        if getattr(a, "flip", None):
            flip = a.flip.lower()
            if flip not in ("h", "v", "horizontal", "vertical"):
                raise core.usage_error("--flip must be h or v")
            img = ImageOps.mirror(img) if flip.startswith("h") else ImageOps.flip(img)
            ops_done.append(f"flip {flip}")
        if getattr(a, "crop", None):
            box = parse_box(a.crop, img)
            img = img.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
            ops_done.append(f"crop {box}")
        if getattr(a, "size", None):
            target = core.check_size(ctx.config, core.parse_size(a.size))
            mode = (getattr(a, "fit", None) or "cover").lower()
            if mode == "cover":
                img = cover_resize(img, target)
            elif mode == "contain":
                img = contain_resize(img, target, background=core.hex_to_rgb(getattr(a, "pad_color", None) or "#ffffff"))
            elif mode == "stretch":
                img = img.resize(target, Image.Resampling.LANCZOS)
            else:
                raise core.usage_error("--fit must be cover, contain or stretch")
            ops_done.append(f"resize {target[0]}x{target[1]} ({mode})")
        if getattr(a, "max_dim", None):
            md = int(a.max_dim)
            if md < 16 or md > max_dim:
                raise core.usage_error(f"--max-dim must be between 16 and {max_dim}")
            if max(img.size) > md:
                scale = md / max(img.size)
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                                 Image.Resampling.LANCZOS)
                ops_done.append(f"downscale to max {md}")
        if getattr(a, "pad", None):
            pad = int(a.pad)
            if pad < 0 or pad > max_dim:
                raise core.usage_error("--pad must be between 0 and the max output dimension")
            color = core.hex_to_rgb(getattr(a, "pad_color", None) or "#ffffff")
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            canvas = Image.new(mode, (img.width + pad * 2, img.height + pad * 2), color + ((0,) if mode == "RGBA" else ()))
            canvas.paste(img, (pad, pad), img if mode == "RGBA" else None)
            img = canvas
            ops_done.append(f"pad {pad}px")
        if not ops_done:
            raise core.usage_error("No edit operation requested.",
                                  "Pass at least one of --crop, --size, --rotate, --flip, --pad, --max-dim.")
        fmt = (getattr(a, "format", None) or src.suffix.lstrip(".") or "png").lower().lstrip(".")
        path = emit_image(ctx, img, project_name(a), f"{src.stem}-edit", fmt,
                          quality=int(getattr(a, "quality", 0) or 92),
                          description=f"Edited: {', '.join(dict.fromkeys(ops_done))}")
        outputs.append(path)
    ctx.audit("edit.completed", count=len(outputs), operations=list(dict.fromkeys(ops_done)))
    return {"data": {"operations": list(dict.fromkeys(ops_done)), "count": len(outputs),
                     "files": [core.rel_posix(p, ctx.out_root) for p in outputs]},
            "outputs": outputs}


def cover_resize(img, target):
    scale = max(target[0] / img.width, target[1] / img.height)
    new = (max(1, int(round(img.width * scale))), max(1, int(round(img.height * scale))))
    resized = img.resize(new, Image.Resampling.LANCZOS)
    left = (new[0] - target[0]) // 2
    top = (new[1] - target[1]) // 2
    return resized.crop((left, top, left + target[0], top + target[1]))


def contain_resize(img, target, background=(255, 255, 255)):
    scale = min(target[0] / img.width, target[1] / img.height)
    new = (max(1, int(round(img.width * scale))), max(1, int(round(img.height * scale))))
    resized = img.resize(new, Image.Resampling.LANCZOS)
    mode = "RGBA" if "A" in resized.getbands() else "RGB"
    canvas = Image.new(mode, target, background + ((255,) if mode == "RGBA" else ()))
    canvas.paste(resized, ((target[0] - new[0]) // 2, (target[1] - new[1]) // 2),
                 resized if mode == "RGBA" else None)
    return canvas


# --------------------------------------------------------------------------
# retouching
# --------------------------------------------------------------------------


def op_retouch(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'retouch' command needs --input IMAGE.")
    img = core.open_image(src, ctx)
    regions = []
    for raw in (getattr(a, "region", None) or []):
        for part in str(raw).split(";"):
            if part.strip():
                regions.append(parse_box(part, img))
    mode = (getattr(a, "mode", None) or "heal").lower()
    if mode not in ("heal", "blur", "sharpen", "denoise", "clone", "despeckle"):
        raise core.usage_error("--mode must be heal, blur, sharpen, denoise, clone or despeckle")
    if not regions and mode != "despeckle":
        raise core.usage_error("Supply at least one --region x,y,w,h (use ';' to separate several).")
    strength = float(getattr(a, "strength", 1.0) or 1.0)
    if not 0.05 <= strength <= 4.0:
        raise core.usage_error("--strength must be between 0.05 and 4.0")
    feather = int(getattr(a, "feather", 0) or max(2, int(min(img.size) * 0.006)))
    work = img.convert("RGBA")
    report = []
    for box in regions:
        mask = Image.new("L", work.size, 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle([box[0], box[1], box[0] + box[2], box[1] + box[3]],
                             radius=int(min(box[2], box[3]) * 0.25), fill=255)
        if feather:
            mask = mask.filter(ImageFilter.GaussianBlur(feather))
        if mode in ("heal", "clone"):
            radius = max(2, int(min(box[2], box[3]) * 0.6))
            work, iterations = diffuse_fill(work, mask, radius=int(radius), iterations=int(18 * strength + 6))
            report.append({"box": list(box), "mode": mode, "method": "mirror-fill + masked diffusion",
                           "iterations": iterations})
        elif mode == "blur":
            blurred = work.filter(ImageFilter.GaussianBlur(max(1, int(radius_guess(box) * 0.35 * strength))))
            work = Image.composite(blurred, work, mask)
            report.append({"box": list(box), "mode": "blur", "radius": radius_guess(box) * 0.35 * strength})
        elif mode == "sharpen":
            sharp = work.filter(ImageFilter.UnsharpMask(radius=2.2, percent=int(140 * strength), threshold=2))
            work = Image.composite(sharp, work, mask)
            report.append({"box": list(box), "mode": "sharpen", "percent": int(140 * strength)})
        elif mode == "denoise":
            smooth = work.filter(ImageFilter.MedianFilter(size=3))
            smooth = ImageEnhance.Contrast(smooth).enhance(1.0)
            work = Image.composite(smooth, work, mask)
            report.append({"box": list(box), "mode": "denoise", "method": "median 3"})
    if mode == "despeckle":
        work = despeckle(work, threshold=int(getattr(a, "threshold", 28) or 28))
        report.append({"mode": "despeckle", "threshold": int(getattr(a, "threshold", 28) or 28)})
    if feather:
        ctx.note(f"Edits were feathered by {feather}px; inspect the boundary at 200 percent before delivery.")
    fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
    path = emit_image(ctx, work, project_name(a), f"{src.stem}-retouch", fmt,
                      quality=int(getattr(a, "quality", 0) or 95),
                      description="Retouched regions: " + "; ".join(str(r.get("box")) for r in report))
    payload = {"operation": "retouch", "mode": mode, "strength": strength, "feather_px": feather,
               "regions": report, "file": core.rel_posix(path, ctx.out_root),
               "limits": ["heal fills from surrounding pixels; it cannot invent missing structure",
                          "large regions need a source patch or a model-based provider"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-{src.stem}-retouch-report"), payload)
    ctx.audit("retouch.completed", mode=mode, regions=len(report))
    return {"data": payload, "outputs": [path, out_json]}


def radius_guess(box):
    return max(2, int(min(box[2], box[3]) / 2))


def diffuse_fill(img, mask, radius=12, iterations=20):
    """Mirror the surroundings into the masked area, then relax with masked blur passes."""
    rgba = img.convert("RGBA")
    arr = rgba.copy()
    # mirror fill: flip the image and shift it so the masked area borrows nearby pixels
    flipped = ImageOps.mirror(rgba)
    shifted = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    shifted.paste(flipped, (0, 0))
    arr = Image.composite(shifted, arr, mask)
    for i in range(max(1, iterations)):
        blurred = arr.filter(ImageFilter.GaussianBlur(max(1, radius * (1 - i / (iterations + 4)))))
        arr = Image.composite(blurred, arr, mask)
    # keep the untouched alpha from the original outside the mask
    alpha = Image.composite(arr.getchannel("A"), rgba.getchannel("A"), mask)
    arr.putalpha(alpha)
    return arr, iterations


def despeckle(img, threshold=28):
    median = img.filter(ImageFilter.MedianFilter(size=3))
    diff = ImageChops.difference(img.convert("RGB"), median.convert("RGB")).convert("L")
    mask = diff.point(lambda v: 255 if v > threshold else 0).filter(ImageFilter.MaxFilter(3))
    return Image.composite(median, img, mask)


# --------------------------------------------------------------------------
# cutouts and transparency
# --------------------------------------------------------------------------


def op_cutout(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'cutout' command needs --input IMAGE.")
    img = core.open_image(src, ctx).convert("RGB")
    method = (getattr(a, "method", None) or "auto").lower()
    if method not in ("auto", "chroma", "border", "color"):
        raise core.usage_error("--method must be auto, chroma, border or color")
    tolerance = int(getattr(a, "tolerance", 0) or 34)
    if not 2 <= tolerance <= 200:
        raise core.usage_error("--tolerance must be between 2 and 200")
    feather = int(getattr(a, "feather", 0) or max(1, int(min(img.size) * 0.003)))
    small = img.copy()
    small.thumbnail((320, 320), Image.Resampling.BILINEAR)
    alpha_small = build_alpha(small, method, tolerance, getattr(a, "bg_color", None))
    alpha = alpha_small.resize(img.size, Image.Resampling.BILINEAR)
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    alpha = alpha.point(lambda v: 0 if v < 12 else (255 if v > 243 else v))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    bbox = alpha.getbbox()
    coverage = 1 - (sum(alpha.histogram()[:8]) / (alpha.width * alpha.height))
    fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
    if fmt in ("jpg", "jpeg"):
        raise core.usage_error("JPEG cannot store transparency", "Use png, webp or tiff for cutouts.")
    path = emit_image(ctx, out, project_name(a), f"{src.stem}-cutout", fmt,
                      description=f"Background removed with method '{method}'")
    matte_path = ctx.out_path(core.slugify(f"{project_name(a)}-{src.stem}-matte"), ".png")
    core.save_image(ctx, alpha.convert("L"), matte_path, "png")
    payload = {
        "operation": "cutout", "method": method, "tolerance": tolerance, "feather_px": feather,
        "foreground_coverage": round(coverage, 4), "subject_bbox": list(bbox) if bbox else None,
        "output_mode": out.mode, "has_alpha": True,
        "files": [core.rel_posix(path, ctx.out_root), core.rel_posix(matte_path, ctx.out_root)],
        "limits": ["edge matting is colour-distance based; hair, glass and smoke need a matting model",
                   "verify the alpha edge on a mid-grey background before compositing",
                   "colour decontamination at the edge is not performed"],
    }
    if coverage < 0.02:
        ctx.note("Foreground coverage is below 2 percent: the background may not have been isolated.")
        payload["warning"] = "very low foreground coverage"
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-{src.stem}-cutout-report"), payload)
    ctx.audit("cutout.completed", method=method, coverage=round(coverage, 3))
    return {"data": payload, "outputs": [path, matte_path, out_json]}


def build_alpha(small, method, tolerance, bg_color=None):
    """Alpha mask from colour distance to a key colour or to sampled border colours."""
    W, H = small.size
    px = small.load()
    if method in ("chroma", "color"):
        key = core.hex_to_rgb(bg_color) if bg_color else estimate_border_color(px, W, H)
        keys = [key]
        mask = Image.new("L", (W, H), 0)
        mp = mask.load()
        tol2 = tolerance * tolerance * 3
        for y in range(H):
            for x in range(W):
                r, g, b = px[x, y]
                if (r - key[0]) ** 2 + (g - key[1]) ** 2 + (b - key[2]) ** 2 <= tol2:
                    mp[x, y] = 0
                else:
                    mp[x, y] = 255
        return _remove_small_islands(mask)
    keys = sample_border_colors(px, W, H, limit=12)
    tol2 = tolerance * tolerance * 3
    visited = bytearray(W * H)
    from collections import deque
    queue = deque()
    for x in range(W):
        queue.append((x, 0))
        queue.append((x, H - 1))
    for y in range(H):
        queue.append((0, y))
        queue.append((W - 1, y))
    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= W or y >= H:
            continue
        idx = y * W + x
        if visited[idx]:
            continue
        r, g, b = px[x, y]
        if min((r - k[0]) ** 2 + (g - k[1]) ** 2 + (b - k[2]) ** 2 for k in keys) > tol2:
            continue
        visited[idx] = 1
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    mask = Image.new("L", (W, H), 255)
    mp = mask.load()
    for y in range(H):
        row = y * W
        for x in range(W):
            if visited[row + x]:
                mp[x, y] = 0
    return _remove_small_islands(mask)


def sample_border_colors(px, W, H, limit=12):
    """Collect distinct colours along the frame so gradients are matched, not just one median."""
    samples = []
    step_x = max(1, W // 24)
    step_y = max(1, H // 24)
    for x in range(0, W, step_x):
        samples.append(px[x, 0])
        samples.append(px[x, H - 1])
    for y in range(0, H, step_y):
        samples.append(px[0, y])
        samples.append(px[W - 1, y])
    chosen = []
    for color in samples:
        if all(sum(abs(color[i] - c[i]) for i in range(3)) > 24 for c in chosen):
            chosen.append(color)
        if len(chosen) >= limit:
            break
    return chosen or [(255, 255, 255)]


def _remove_small_islands(mask, min_frac=0.004):
    """Drop speckle foreground islands smaller than min_frac of the frame."""
    W, H = mask.size
    px = mask.load()
    seen = [[False] * W for _ in range(H)]
    from collections import deque
    keep = Image.new("L", (W, H), 0)
    kp = keep.load()
    min_area = max(4, int(W * H * min_frac))
    for y0 in range(H):
        for x0 in range(W):
            if seen[y0][x0] or px[x0, y0] == 0:
                continue
            queue = deque([(x0, y0)])
            seen[y0][x0] = True
            cells = []
            while queue:
                x, y = queue.popleft()
                cells.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx] and px[nx, ny] > 0:
                        seen[ny][nx] = True
                        queue.append((nx, ny))
            if len(cells) >= min_area:
                for x, y in cells:
                    kp[x, y] = 255
    return keep


def estimate_border_color(px, W, H):
    samples = []
    step = max(1, W // 32)
    for x in range(0, W, step):
        samples.append(px[x, 0])
        samples.append(px[x, H - 1])
    for y in range(0, H, step):
        samples.append(px[0, y])
        samples.append(px[W - 1, y])
    if not samples:
        return (255, 255, 255)
    return tuple(sorted(s[i] for s in samples)[len(samples) // 2] for i in range(3))


# --------------------------------------------------------------------------
# compositing
# --------------------------------------------------------------------------


def op_composite(ctx, a):
    subject_path = core.resolve_input(a.input or "")
    if not subject_path:
        raise core.usage_error("The 'composite' command needs --input SUBJECT (png with alpha).")
    subject = core.open_image(subject_path, ctx).convert("RGBA")
    if a.background:
        bg_path = core.resolve_input(a.background)
        background = core.open_image(bg_path, ctx).convert("RGBA")
    else:
        color = core.hex_to_rgb(getattr(a, "bg_color", None) or "#ffffff")
        background = Image.new("RGBA", subject.size, color + (255,))
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or
                                                      f"{background.width}x{background.height}"))
    background = cover_resize(background, size)
    scale = float(getattr(a, "scale", 0) or 0.6)
    if not 0.02 <= scale <= 1.5:
        raise core.usage_error("--scale must be between 0.02 and 1.5")
    target_w = max(8, int(size[0] * scale))
    ratio = target_w / subject.width
    subj = subject.resize((target_w, max(8, int(subject.height * ratio))), Image.Resampling.LANCZOS)
    pos = str(getattr(a, "position", None) or "center").lower()
    if "," in pos:
        try:
            fx, fy = (float(v) for v in pos.split(","))
        except ValueError:
            raise core.usage_error("--position must be centre words or 'x,y' fractions")
        x = int((size[0] - subj.width) * core.clamp(fx, 0, 1))
        y = int((size[1] - subj.height) * core.clamp(fy, 0, 1))
    else:
        cx = {"left": 0.1, "center": 0.5, "centre": 0.5, "right": 0.9}.get(pos, 0.5)
        cy = {"top": 0.15, "middle": 0.5, "bottom": 0.9}.get(pos, 0.55)
        x = int(size[0] * cx - subj.width / 2)
        y = int(size[1] * cy - subj.height / 2)
    canvas = background.copy()
    if getattr(a, "shadow", True):
        blur = int(getattr(a, "shadow_blur", 0) or max(6, int(subj.width * 0.06)))
        opacity = float(getattr(a, "shadow_opacity", 0.32) or 0.32)
        shadow = Image.new("RGBA", subj.size, (0, 0, 0, 0))
        shadow.putalpha(subj.getchannel("A").point(lambda v: int(v * opacity)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        canvas.alpha_composite(shadow, (max(0, x + int(subj.width * 0.03)), max(0, y + int(subj.height * 0.05))))
    canvas.alpha_composite(subj, (max(0, x), max(0, y)))
    fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
    path = emit_image(ctx, canvas, project_name(a), "composite", fmt,
                      quality=int(getattr(a, "quality", 0) or 94),
                      description="Composite of a cut-out subject over a background")
    payload = {"operation": "composite", "canvas": list(size), "subject": core.rel_posix(subject_path),
               "subject_box": [x, y, x + subj.width, y + subj.height], "scale": scale,
               "position": pos, "shadow": bool(getattr(a, "shadow", True)),
               "file": core.rel_posix(path, ctx.out_root),
               "checks": ["verify the contact shadow direction matches the background light",
                          "check subject edge against the background colour"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-composite-spec"), payload)
    ctx.audit("composite.created", size=size)
    return {"data": payload, "outputs": [path, out_json]}


# --------------------------------------------------------------------------
# colour, lighting and atmosphere
# --------------------------------------------------------------------------


def op_grade(ctx, a):
    inputs = collect_inputs(a, ctx)
    exposure = float(getattr(a, "exposure", 0) or 0)
    contrast = float(getattr(a, "contrast", 0) or 0)
    saturation = float(getattr(a, "saturation", 0) or 0)
    temperature = float(getattr(a, "temperature", 0) or 0)
    tint = float(getattr(a, "tint", 0) or 0)
    gamma = float(getattr(a, "gamma", 0) or 0)
    lift = float(getattr(a, "lift", 0) or 0)
    vignette = float(getattr(a, "vignette", 0) or 0)
    grain = float(getattr(a, "grain", 0) or 0)
    bloom = float(getattr(a, "bloom", 0) or 0)
    relight = getattr(a, "relight", None)
    if not any([exposure, contrast, saturation, temperature, tint, gamma, lift, vignette, grain, bloom, relight]):
        raise core.usage_error("No grade requested.",
                              "Use any of --exposure --contrast --saturation --temperature --tint --gamma "
                              "--lift --vignette --grain --bloom --relight.")
    bounds = [("exposure", exposure, -3, 3), ("contrast", contrast, -100, 100),
              ("saturation", saturation, -100, 100), ("temperature", temperature, -100, 100),
              ("tint", tint, -100, 100), ("lift", lift, 0, 60), ("vignette", vignette, 0, 1),
              ("grain", grain, 0, 0.2), ("bloom", bloom, 0, 1)]
    if gamma:
        bounds.append(("gamma", gamma, 0.2, 3.0))
    for name, val, lo, hi in bounds:
        if not lo <= val <= hi:
            raise core.usage_error(f"--{name} must be between {lo} and {hi}")
    outputs, reports = [], []
    for src in inputs:
        img = core.open_image(src, ctx).convert("RGB")
        original_mean = core.channel_means(img)
        work = img
        if pending := (exposure or contrast or saturation or temperature or tint or gamma or lift):
            work = apply_grade(work, exposure, contrast, saturation, temperature, tint, gamma, lift)
        if relight:
            work = apply_relight(work, relight, float(getattr(a, "relight_strength", 0.35) or 0.35))
        if bloom:
            work = apply_bloom(work, bloom)
        if vignette:
            mask = core.vignette_mask(work.size, strength=vignette, softness=1.0)
            dark = Image.new("RGB", work.size, (0, 0, 0))
            work = Image.composite(dark, work, mask)
        if grain:
            work = core.apply_grain(work.convert("RGBA"), grain, core.seeded_rng(str(src), "grade")).convert("RGB")
        fmt = (getattr(a, "format", None) or src.suffix.lstrip(".") or "png").lower().lstrip(".")
        path = emit_image(ctx, work, project_name(a), f"{src.stem}-grade", fmt,
                          quality=int(getattr(a, "quality", 0) or 94),
                          description="Colour graded with imagekit")
        outputs.append(path)
        report = {"source": core.rel_posix(src), "output": core.rel_posix(path, ctx.out_root),
                  "mean_before": original_mean, "mean_after": core.channel_means(work),
                  "tone": core.tone_stats(work), "params": {
                      "exposure": exposure, "contrast": contrast, "saturation": saturation,
                      "temperature": temperature, "tint": tint, "gamma": gamma, "lift": lift,
                      "vignette": vignette, "grain": grain, "bloom": bloom, "relight": relight}}
        reports.append(report)
    payload = {"operation": "grade", "count": len(outputs), "results": reports}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-grade-report"), payload)
    ctx.audit("grade.completed", count=len(outputs))
    return {"data": payload, "outputs": outputs + [out_json]}


def apply_grade(img, exposure, contrast, saturation, temperature, tint, gamma, lift):
    work = img.copy()
    if exposure:
        factor = 2.0 ** exposure
        work = work.point(lambda v: core.clamp(int(v * factor), 0, 255))
    if temperature or tint:
        r_mul = 1 + temperature / 220.0
        b_mul = 1 - temperature / 220.0
        g_mul = 1 + tint / 320.0
        r, g, b = work.split()
        r = r.point(lambda v: core.clamp(int(v * r_mul), 0, 255))
        g = g.point(lambda v: core.clamp(int(v * g_mul), 0, 255))
        b = b.point(lambda v: core.clamp(int(v * b_mul), 0, 255))
        work = Image.merge("RGB", (r, g, b))
    if gamma:
        lut = [core.clamp(int(255 * ((i / 255) ** (1 / gamma))), 0, 255) for i in range(256)]
        work = work.point(lut * 3)
    if lift:
        work = work.point(lambda v: core.clamp(int(lift + v * (1 - lift / 255)), 0, 255))
    if contrast:
        work = ImageEnhance.Contrast(work).enhance(1 + contrast / 100.0)
    if saturation:
        work = ImageEnhance.Color(work).enhance(1 + saturation / 100.0)
    return work


def apply_relight(img, direction, strength=0.35):
    """Directional light gradient multiplied over the frame to shift the presumed key light."""
    if direction not in ("left", "right", "top", "bottom", "top-left", "top-right"):
        raise core.usage_error("--relight must be left, right, top, bottom, top-left or top-right")
    w, h = img.size
    mask = Image.new("L", (96, 96))
    mp = mask.load()
    vectors = {"left": (0, 0.5), "right": (1, 0.5), "top": (0.5, 0), "bottom": (0.5, 1),
               "top-left": (0.1, 0.1), "top-right": (0.9, 0.1)}
    vx, vy = vectors[direction]
    for y in range(96):
        for x in range(96):
            nx, ny = x / 95, y / 95
            d = math.hypot(nx - vx, ny - vy)
            v = core.clamp(1 - d / 1.2, 0.25, 1.0)
            mp[x, y] = int(255 * (0.5 + (v - 0.5) * strength * 2))
    light = mask.resize((w, h), Image.Resampling.BICUBIC)
    work = img.convert("RGB")
    warm = Image.new("RGB", (w, h), (255, 246, 232))
    cool = Image.new("RGB", (w, h), (232, 240, 255))
    blended = Image.composite(warm, cool, light)
    return Image.blend(work, ImageChops.multiply(work, blended.point(lambda v: core.clamp(v + 60, 0, 255))), strength * 0.6)


def apply_bloom(img, amount):
    lum = img.convert("L")
    highlights = lum.point(lambda v: max(0, int((v - 190) * 3)))
    glow = Image.new("RGB", img.size, (255, 250, 240))
    glow.putalpha = None
    glow_layer = glow.convert("RGBA")
    glow_layer.putalpha(highlights.point(lambda v: int(v * amount)))
    out = Image.alpha_composite(img.convert("RGBA"), glow_layer.filter(ImageFilter.GaussianBlur(max(2, min(img.size) * 0.02))))
    return out.convert("RGB")


# --------------------------------------------------------------------------
# restoration and enhancement
# --------------------------------------------------------------------------


def op_restore(ctx, a):
    inputs = collect_inputs(a, ctx)
    steps = []
    denoise = bool(getattr(a, "denoise", False))
    autolevels = bool(getattr(a, "autolevels", False))
    sharpen = float(getattr(a, "sharpen", 0) or 0)
    upscale = float(getattr(a, "upscale", 0) or 0)
    despeckle_on = bool(getattr(a, "despeckle", False))
    if not any([denoise, autolevels, sharpen, upscale, despeckle_on]):
        raise core.usage_error("No restoration step requested.",
                              "Use --denoise --autolevels --despeckle --sharpen PERCENT --upscale FACTOR.")
    if upscale and not 1.0 < upscale <= 4.0:
        raise core.usage_error("--upscale must be greater than 1.0 and at most 4.0")
    if sharpen and not 0 < sharpen <= 300:
        raise core.usage_error("--sharpen must be between 0 and 300 percent")
    max_dim = ctx.config.limit("max_output_dimension", 8192)
    outputs, reports = [], []
    for src in inputs:
        img = core.open_image(src, ctx).convert("RGB")
        before = {"sharpness": core.laplacian_variance(img), "tone": core.tone_stats(img)}
        work = img
        if denoise:
            work = work.filter(ImageFilter.MedianFilter(size=3))
            steps.append("denoise (median 3)")
        if despeckle_on:
            work = despeckle(work, threshold=26)
            steps.append("despeckle")
        if autolevels:
            work = ImageOps.autocontrast(work, cutoff=(0.5, 0.5))
            steps.append("autolevels (0.5 percent cutoff)")
        if upscale:
            target = (int(work.width * upscale), int(work.height * upscale))
            if target[0] > max_dim or target[1] > max_dim:
                raise core.usage_error(f"Upscaled size {target[0]}x{target[1]} exceeds max_output_dimension")
            work = work.resize(target, Image.Resampling.LANCZOS)
            work = work.filter(ImageFilter.UnsharpMask(radius=1.6, percent=60, threshold=3))
            steps.append(f"upscale x{upscale} (lanczos + light unsharp)")
        if sharpen:
            work = work.filter(ImageFilter.UnsharpMask(radius=2.0, percent=int(sharpen), threshold=3))
            steps.append(f"unsharp mask {int(sharpen)} percent")
        after = {"sharpness": core.laplacian_variance(work), "tone": core.tone_stats(work)}
        fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
        path = emit_image(ctx, work, project_name(a), f"{src.stem}-restored", fmt,
                          quality=int(getattr(a, "quality", 0) or 95),
                          description="Restored: " + ", ".join(dict.fromkeys(steps)))
        outputs.append(path)
        reports.append({"source": core.rel_posix(src), "output": core.rel_posix(path, ctx.out_root),
                        "before": before, "after": after,
                        "sharpness_gain": round(after["sharpness"] - before["sharpness"], 2),
                        "size": [work.width, work.height]})
    payload = {"operation": "restore", "steps": list(dict.fromkeys(steps)), "results": reports,
               "limits": ["this is a deterministic enhancement pipeline, not generative restoration",
                          "it cannot recover information that is not present in the source",
                          "upscaling interpolates; it does not add true detail"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-restore-report"), payload)
    ctx.audit("restore.completed", count=len(outputs))
    return {"data": payload, "outputs": outputs + [out_json]}


# --------------------------------------------------------------------------
# conversion and delivery
# --------------------------------------------------------------------------


def op_convert(ctx, a):
    inputs = collect_inputs(a, ctx)
    fmt = (getattr(a, "format", None) or "").lower().lstrip(".")
    if not fmt:
        raise core.usage_error("The 'convert' command needs --format png|jpg|webp|tiff|avif|gif|bmp.")
    if fmt not in core.WRITE_FORMATS:
        raise core.usage_error(f"Unsupported --format '{fmt}'",
                               "Supported: " + ", ".join(sorted(core.WRITE_FORMATS)))
    mode = (getattr(a, "mode", None) or "").upper() or None
    if mode and mode not in ("RGB", "RGBA", "L", "LA", "P", "CMYK"):
        raise core.usage_error("--mode must be RGB, RGBA, L, LA, P or CMYK")
    quality = int(getattr(a, "quality", 0) or 92)
    dpi_in = getattr(a, "dpi", None)
    strip = bool(getattr(a, "strip_metadata", False))
    keep_alpha = fmt in ("png", "webp", "tiff", "avif", "gif")
    warnings = []
    if mode == "CMYK":
        warnings.append("CMYK conversion uses Pillow's naive channel conversion: no ICC transform is applied. "
                        "Convert with the printer profile for production.")
    outputs, reports = [], []
    for src in inputs:
        img = core.open_image(src, ctx)
        info_before = core.image_info(src)
        has_alpha = "A" in img.getbands()
        if has_alpha and not keep_alpha:
            background = core.hex_to_rgb(getattr(a, "bg_color", None) or "#ffffff")
            base = Image.new("RGB", img.size, background)
            base.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
            img = base
            warnings.append(f"{src.name}: alpha flattened onto {core.rgb_to_hex(background)} for {fmt.upper()}")
        if mode:
            img = img.convert(mode)
        elif img.mode == "P":
            img = img.convert("RGBA" if has_alpha and keep_alpha else "RGB")
        dpi = (float(dpi_in), float(dpi_in)) if dpi_in else (info_before.get("dpi") or (72, 72))
        if dpi_in and not 30 <= float(dpi_in) <= 1200:
            raise core.usage_error("--dpi must be between 30 and 1200")
        name = f"{src.stem}"
        path = ctx.out_path(core.slugify(f"{project_name(a)}-{name}"), "." + fmt)
        meta = {"Software": f"imagekit {core.VERSION}"}
        if not strip:
            meta["Description"] = f"Converted from {src.suffix.lstrip('.').upper()} by imagekit"
        core.save_image(ctx, img, path, fmt, quality=quality, dpi=dpi,
                        pnginfo=core.build_pnginfo(meta) if fmt == "png" else None,
                        exif=core.build_exif(meta) if fmt != "png" else None,
                        lossless=bool(getattr(a, "lossless", False)))
        after = core.image_info(path)
        outputs.append(path)
        reports.append({"source": core.rel_posix(src), "output": core.rel_posix(path, ctx.out_root),
                        "source_info": {k: info_before[k] for k in ("width", "height", "mode", "format", "bytes")},
                        "output_info": {k: after[k] for k in ("width", "height", "mode", "format", "bytes", "dpi")},
                        "verified_openable": True})
    payload = {"operation": "convert", "format": fmt, "mode": mode, "quality": quality,
               "results": reports, "warnings": warnings,
               "checks": ["every output was reopened and its dimensions/mode read back",
                          "alpha flattened only where the target format cannot store it"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-convert-report"), payload)
    ctx.audit("convert.completed", format=fmt, count=len(outputs))
    return {"data": payload, "outputs": outputs + [out_json], "warnings": warnings}


# --------------------------------------------------------------------------
# print preparation
# --------------------------------------------------------------------------


def op_bleed(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'bleed' command needs --input ARTWORK.")
    img = core.open_image(src, ctx).convert("RGB")
    bleed = int(getattr(a, "bleed", 0) or 3)
    dpi = int(getattr(a, "dpi", 0) or 300)
    if not 0 < bleed <= 50:
        raise core.usage_error("--bleed must be between 1 and 50 mm")
    if not 72 <= dpi <= 1200:
        raise core.usage_error("--dpi must be between 72 and 1200")
    pad_px = int(round(bleed / 25.4 * dpi))
    if pad_px < 1:
        raise core.usage_error("Bleed is smaller than a single pixel at this dpi")
    max_out = ctx.config.limit("max_output_dimension", 8192)
    if img.width + pad_px * 2 > max_out or img.height + pad_px * 2 > max_out:
        raise core.usage_error("Bleed output would exceed max_output_dimension; reduce dpi or trim size")
    # extend with mirrored edges so the bleed continues the artwork
    canvas = Image.new("RGB", (img.width + pad_px * 2, img.height + pad_px * 2))
    canvas.paste(img, (pad_px, pad_px))
    left = img.crop((0, 0, pad_px, img.height)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    right = img.crop((img.width - pad_px, 0, img.width, img.height)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    top = img.crop((0, 0, img.width, pad_px)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    bottom = img.crop((0, img.height - pad_px, img.width, img.height)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    canvas.paste(left, (0, pad_px))
    canvas.paste(right, (pad_px + img.width, pad_px))
    canvas.paste(top, (pad_px, 0))
    canvas.paste(bottom, (pad_px, pad_px + img.height))
    for corner, box_src, xy in (("tl", (0, 0, pad_px, pad_px), (0, 0)),
                                ("tr", (img.width - pad_px, 0, img.width, pad_px), (pad_px + img.width, 0)),
                                ("bl", (0, img.height - pad_px, pad_px, img.height), (0, pad_px + img.height)),
                                ("br", (img.width - pad_px, img.height - pad_px, img.width, img.height),
                                 (pad_px + img.width, pad_px + img.height))):
        patch = img.crop(box_src).transpose(Image.Transpose.ROTATE_180)
        canvas.paste(patch, xy)
    safe_px = int(round(float(getattr(a, "safe_margin", 5) or 5) / 25.4 * dpi))
    with_guides = canvas.copy()
    d = ImageDraw.Draw(with_guides)
    d.rectangle([pad_px, pad_px, pad_px + img.width, pad_px + img.height], outline=(255, 0, 80), width=2)
    d.rectangle([pad_px + safe_px, pad_px + safe_px, pad_px + img.width - safe_px, pad_px + img.height - safe_px],
                outline=(0, 190, 255), width=2)
    base = ctx.out_path(core.slugify(f"{project_name(a)}-{src.stem}-bleed"), ".tif")
    guide_path = ctx.out_path(core.slugify(f"{project_name(a)}-{src.stem}-bleed-guides"), ".png")
    core.save_image(ctx, canvas, base, "TIFF", dpi=(dpi, dpi))
    core.save_image(ctx, with_guides, guide_path, "PNG", dpi=(dpi, dpi))
    payload = {"operation": "bleed", "bleed_mm": bleed, "dpi": dpi, "bleed_px": pad_px,
               "trim_size_px": [img.width, img.height],
               "output_size_px": [canvas.width, canvas.height],
               "trim_size_mm": [round(img.width / dpi * 25.4, 1), round(img.height / dpi * 25.4, 1)],
               "safe_margin_mm": float(getattr(a, "safe_margin", 5) or 5),
               "files": [core.rel_posix(base, ctx.out_root), core.rel_posix(guide_path, ctx.out_root)],
               "notes": ["bleed is extended by mirroring the artwork edge; replace it with true background "
                         "extension when the artwork has continuous imagery",
                         "guides file is for review only; deliver the file without guides"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-{src.stem}-bleed-spec"), payload)
    ctx.audit("bleed.created", bleed_mm=bleed, dpi=dpi)
    return {"data": payload, "outputs": [base, guide_path, out_json]}


def op_tile(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'tile' command needs --input ARTWORK.")
    img = core.open_image(src, ctx).convert("RGB")
    cols = int(getattr(a, "columns", 0) or 2)
    rows = int(getattr(a, "rows", 0) or 2)
    if not (1 <= cols <= 12 and 1 <= rows <= 12):
        raise core.usage_error("--columns and --rows must be between 1 and 12")
    overlap = int(getattr(a, "overlap", 0) or 0)
    if not 0 <= overlap <= 200:
        raise core.usage_error("--overlap must be between 0 and 200 pixels")
    if cols * rows > ctx.config.limit("max_batch_items", 500):
        raise core.usage_error("Too many panels requested")
    tile_w = math.ceil(img.width / cols)
    tile_h = math.ceil(img.height / rows)
    outputs, panels = [], []
    for r in range(rows):
        for c in range(cols):
            x0 = max(0, c * tile_w - overlap)
            y0 = max(0, r * tile_h - overlap)
            x1 = min(img.width, (c + 1) * tile_w + overlap)
            y1 = min(img.height, (r + 1) * tile_h + overlap)
            panel = img.crop((x0, y0, x1, y1))
            name = core.slugify(f"{project_name(a)}-{src.stem}-panel-r{r + 1:02d}c{c + 1:02d}")
            path = ctx.out_path(name, ".png")
            core.save_image(ctx, panel, path, "PNG", dpi=(300, 300),
                            pnginfo=core.build_pnginfo({"Description":
                                                        f"panel row {r + 1} column {c + 1} of {rows}x{cols}"}))
            outputs.append(path)
            panels.append({"row": r + 1, "column": c + 1, "box": [x0, y0, x1, y1],
                           "size": [panel.width, panel.height], "file": core.rel_posix(path, ctx.out_root)})
    payload = {"operation": "tile", "source": core.rel_posix(src), "grid": [cols, rows],
               "overlap_px": overlap, "source_size": [img.width, img.height],
               "panels": panels,
               "assembly": "lay panels in row-major order; overlap allows trimming at the join",
               "files": [p["file"] for p in panels]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-{src.stem}-tiling-plan"), payload)
    ctx.audit("tile.created", panels=len(panels))
    return {"data": payload, "outputs": outputs + [out_json]}


def op_cropset(ctx, a):
    inputs = collect_inputs(a, ctx)
    ratios_arg = str(getattr(a, "ratios", None) or "1:1,4:5,16:9,9:16")
    ratios = []
    for part in ratios_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise core.usage_error(f"Invalid ratio '{part}'", "Use W:H, for example 1:1 or 16:9.")
        w, h = part.split(":", 1)
        try:
            rw, rh = float(w), float(h)
        except ValueError:
            raise core.usage_error(f"Invalid ratio '{part}'")
        if rw <= 0 or rh <= 0:
            raise core.usage_error(f"Invalid ratio '{part}'")
        ratios.append((part, rw / rh))
    if not ratios or len(ratios) > 12:
        raise core.usage_error("Supply 1 to 12 ratios")
    width = getattr(a, "width", None)
    outputs, reports = [], []
    for src in inputs:
        img = core.open_image(src, ctx)
        saliency = core.saliency_map(img)
        centroid = saliency_centroid(saliency)
        for label, ratio in ratios:
            if width:
                out_w = int(width)
                out_h = max(1, int(round(out_w / ratio)))
            else:
                out_w, out_h = fit_ratio_size(img.width, img.height, ratio)
            out_w, out_h = core.check_size(ctx.config, (out_w, out_h))
            crop = smart_crop(img, ratio, centroid)
            if crop.size != (out_w, out_h):
                crop = crop.resize((out_w, out_h), Image.Resampling.LANCZOS)
            name = core.slugify(f"{project_name(a)}-{src.stem}-{label.replace(':', 'x')}")
            path = ctx.out_path(name, ".png" if "A" in crop.getbands() else ".jpg")
            core.save_image(ctx, crop, path, "PNG" if path.suffix == ".png" else "JPEG", quality=92,
                            dpi=(72, 72),
                            pnginfo=core.build_pnginfo({"Description": f"crop {label} of {src.name}"})
                            if path.suffix == ".png" else None)
            outputs.append(path)
            reports.append({"source": core.rel_posix(src), "ratio": label,
                            "size": [crop.width, crop.height], "subject_centroid": list(centroid),
                            "file": core.rel_posix(path, ctx.out_root)})
    payload = {"operation": "cropset", "ratios": [r[0] for r in ratios], "results": reports,
               "framing": "subject-aware: the crop window follows the saliency centroid, clamped to the frame"}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-cropset-report"), payload)
    ctx.audit("cropset.created", outputs=len(outputs))
    return {"data": payload, "outputs": outputs + [out_json]}


def saliency_centroid(saliency):
    small = saliency.resize((64, 64), Image.Resampling.BILINEAR)
    px = small.load()
    total = 0.0
    sx = sy = 0.0
    for y in range(64):
        for x in range(64):
            v = px[x, y]
            if v <= 8:
                continue
            total += v
            sx += x * v
            sy += y * v
    if total <= 0:
        return (0.5, 0.5)
    return (round(sx / total / 63, 4), round(sy / total / 63, 4))


def fit_ratio_size(w, h, ratio):
    if w / h > ratio:
        return (max(1, int(h * ratio)), h)
    return (w, max(1, int(w / ratio)))


def smart_crop(img, ratio, centroid):
    W, H = img.size
    target_w, target_h = fit_ratio_size(W, H, ratio)
    cx, cy = centroid[0] * W, centroid[1] * H
    left = int(core.clamp(cx - target_w / 2, 0, W - target_w))
    top = int(core.clamp(cy - target_h / 2, 0, H - target_h))
    return img.crop((left, top, left + target_w, top + target_h))


# --------------------------------------------------------------------------
# motion preparation
# --------------------------------------------------------------------------


def op_gif(ctx, a):
    frames_args = []
    for item in (getattr(a, "inputs", None) or []):
        frames_args.extend(str(item).split(","))
    animated_source = None
    if getattr(a, "input", None):
        probe = core.resolve_input(a.input)
        if probe:
            with Image.open(probe) as probe_img:
                if getattr(probe_img, "n_frames", 1) > 1:
                    animated_source = probe
        if animated_source is None and frames_args:
            frames_args.append(a.input)
    duration = int(getattr(a, "duration", 0) or 80)
    if not 10 <= duration <= 4000:
        raise core.usage_error("--duration must be between 10 and 4000 ms per frame")
    loop = int(getattr(a, "loop", 0) or 0)
    colors = int(getattr(a, "colors", 0) or 64)
    if not 2 <= colors <= 256:
        raise core.usage_error("--colors must be between 2 and 256")
    max_frames = ctx.config.limit("max_frames", 240)
    frames = []
    if animated_source is not None:
        with Image.open(animated_source) as anim:
            total = getattr(anim, "n_frames", 1)
            if total > max_frames:
                raise core.usage_error(f"{total} frames exceeds limits.max_frames ({max_frames})")
            for i in range(total):
                anim.seek(i)
                frames.append(anim.convert("RGB").copy())
        target = core.check_size(ctx.config, core.parse_size(
            getattr(a, "size", None) or f"{frames[0].width}x{frames[0].height}"))
        frames = [cover_resize(f, target) for f in frames]
    elif frames_args:
        sources = collect_inputs(a, ctx)
        if len(sources) > max_frames:
            raise core.usage_error(f"{len(sources)} frames exceeds limits.max_frames ({max_frames})")
        size = (getattr(a, "size", None) or None)
        for src in sources:
            img = core.open_image(src, ctx).convert("RGB")
            frames.append(img)
        if size:
            target = core.check_size(ctx.config, core.parse_size(size))
            frames = [cover_resize(f, target) for f in frames]
        else:
            w = min(f.width for f in frames)
            h = min(f.height for f in frames)
            frames = [cover_resize(f, (w, h)) for f in frames]
    else:
        src = core.resolve_input(a.input or "")
        if not src:
            raise core.usage_error("The 'gif' command needs --input IMAGE or --inputs f1.png,f2.png.")
        base = core.open_image(src, ctx).convert("RGB")
        count = int(getattr(a, "frames", 0) or 24)
        if not 2 <= count <= max_frames:
            raise core.usage_error(f"--frames must be between 2 and {max_frames}")
        motion = (getattr(a, "motion", None) or "zoom").lower()
        if motion not in ("pan", "zoom", "pulse", "fade", "rotate", "parallax"):
            raise core.usage_error("--motion must be pan, zoom, pulse, fade, rotate or parallax")
        size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or
                                                          f"{min(base.width, 800)}x"
                                                          f"{int(min(base.width, 800) * base.height / base.width)}"))
        frames = build_motion_frames(base, size, motion, count, getattr(a, "direction", None) or "in")
    if not frames:
        raise core.usage_error("No frames were produced")
    fmt = (getattr(a, "format", None) or "gif").lower().lstrip(".")
    if fmt not in ("gif", "webp", "png"):
        raise core.usage_error("--format must be gif, webp or png (animated png)")
    project = project_name(a)
    base_name = getattr(a, "name", None) or "animation"
    if fmt == "gif":
        quantized = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors) for f in frames]
        path = ctx.out_path(core.slugify(f"{project}-{base_name}"), ".gif")
        quantized[0].save(path, save_all=True, append_images=quantized[1:], duration=duration,
                          loop=loop, optimize=True, disposal=2)
    elif fmt == "webp":
        path = ctx.out_path(core.slugify(f"{project}-{base_name}"), ".webp")
        try:
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration,
                           loop=loop, quality=88, method=4)
        except Exception as exc:
            raise core.ToolError("encode_failed", f"Animated WebP could not be written: {exc}",
                                 "This Pillow build lacks animated WebP support; use --format gif.", core.EXIT_INPUT)
    else:
        path = ctx.out_path(core.slugify(f"{project}-{base_name}"), ".png")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=loop)
    frame_paths = []
    for i, frame in enumerate(frames[: int(getattr(a, "export_frames", 0) or 0)]):
        fpath = ctx.out_path(core.slugify(f"{project}-{base_name}-frame"), f".f{i + 1:03d}.png")
        core.save_image(ctx, frame, fpath, "PNG")
        frame_paths.append(fpath)
    payload = {"operation": "gif", "format": fmt, "frames": len(frames),
               "duration_ms": duration, "loop": loop, "size": list(frames[0].size),
               "output": core.rel_posix(path, ctx.out_root),
               "exported_frames": [core.rel_posix(p, ctx.out_root) for p in frame_paths],
               "checks": ["palette limited to %d colours for GIF" % colors if fmt == "gif" else "full colour",
                          "frame count within limits.max_frames"],
               "notes": ["GIF is 256 colours maximum and 1-bit alpha; use WebP or APNG for smooth gradients",
                         "preview the loop point; the first and last frame should not duplicate"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project}-{base_name}-animation-spec"), payload)
    ctx.audit("gif.created", frames=len(frames), format=fmt)
    return {"data": payload, "outputs": [path] + frame_paths + [out_json]}


def build_motion_frames(base, size, motion, count, direction):
    frames = []
    W, H = size
    for i in range(count):
        t = i / max(1, count - 1)
        if motion == "zoom":
            zoom = 1.0 + (0.25 * t if direction == "in" else 0.25 * (1 - t))
            w, h = int(base.width / zoom), int(base.height / zoom)
            left = (base.width - w) // 2
            top = (base.height - h) // 2
            frame = base.crop((left, top, left + w, top + h))
        elif motion == "pan":
            span = base.width * 0.18
            offset = (t if direction != "out" else 1 - t) * span
            w = int(base.width * 0.82)
            box = (int(offset), 0, int(offset) + w, base.height)
            frame = base.crop(box)
        elif motion == "pulse":
            zoom = 1.0 + 0.06 * math.sin(t * math.pi * 2)
            w, h = int(base.width / zoom), int(base.height / zoom)
            left, top = (base.width - w) // 2, (base.height - h) // 2
            frame = base.crop((left, top, left + w, top + h))
        elif motion == "fade":
            frame = ImageEnhance.Brightness(base).enhance(0.35 + 0.65 * t if direction != "out" else 1 - 0.65 * t)
        elif motion == "rotate":
            frame = base.rotate(-8 * t, resample=Image.Resampling.BICUBIC, expand=False)
        else:  # parallax: shift a blurred copy under a sharper foreground band
            frame = base
        frames.append(cover_resize(frame if frame.mode == "RGB" else frame.convert("RGB"), size))
    return frames


def op_sprite(ctx, a):
    sources = collect_inputs(a, ctx)
    frame_w = int(getattr(a, "frame_width", 0) or 0)
    frame_h = int(getattr(a, "frame_height", 0) or 0)
    count = int(getattr(a, "frames", 0) or 0)
    if count and len(sources) == 1:
        base = core.open_image(sources[0], ctx).convert("RGB")
        cell = core.check_size(ctx.config, core.parse_size(
            getattr(a, "size", None) or f"{min(base.width, 512)}x"
            f"{int(min(base.width, 512) * base.height / base.width)}"))
        motion = (getattr(a, "motion", None) or "zoom").lower()
        if motion not in ("pan", "zoom", "pulse", "fade", "rotate", "parallax"):
            raise core.usage_error("--motion must be pan, zoom, pulse, fade, rotate or parallax")
        if not 2 <= count <= ctx.config.limit("max_frames", 240):
            raise core.usage_error("--frames must be between 2 and limits.max_frames")
        frames = build_motion_frames(base, cell, motion, count, getattr(a, "direction", None) or "in")
        src_stem = sources[0].stem
        tmp_dir = ctx.out_root / ".sprite-frames"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        sources = []
        for i, fr in enumerate(frames):
            fp = tmp_dir / f"{src_stem}-f{i:03d}.png"
            fr.convert("RGBA").save(fp, "PNG")
            sources.append(fp)
        frame_w = frame_w or cell[0]
        frame_h = frame_h or cell[1]
    images = [core.open_image(p, ctx).convert("RGBA") for p in sources]
    if not frame_w:
        frame_w = max(im.width for im in images)
    if not frame_h:
        frame_h = max(im.height for im in images)
    columns = int(getattr(a, "columns", 0) or len(images))
    if columns < 1 or columns > len(images):
        columns = len(images)
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGBA", (frame_w * columns, frame_h * rows), (0, 0, 0, 0))
    atlas = {"frames": [], "meta": {"size": [frame_w * columns, frame_h * rows],
                                    "frame_size": [frame_w, frame_h], "columns": columns, "rows": rows}}
    for i, im in enumerate(images):
        col, row = i % columns, i // columns
        cell = im.copy()
        cell.thumbnail((frame_w, frame_h), Image.Resampling.LANCZOS)
        sheet.alpha_composite(cell, (col * frame_w, row * frame_h))
        atlas["frames"].append({"index": i, "name": sources[i].stem,
                                "frame": {"x": col * frame_w, "y": row * frame_h, "w": frame_w, "h": frame_h},
                                "duration_ms": int(getattr(a, "duration", 0) or 80)})
    project = project_name(a)
    base_name = getattr(a, "name", None) or "sprite"
    path = ctx.out_path(core.slugify(f"{project}-{base_name}"), ".png")
    core.save_image(ctx, sheet, path, "PNG", pnginfo=core.build_pnginfo({"Description": "Sprite sheet"}))
    atlas_path = core.emit_json(ctx, core.slugify(f"{project}-{base_name}-atlas"), atlas)
    ctx.audit("sprite.created", frames=len(images))
    return {"data": {"operation": "sprite", "frames": len(images), "grid": [columns, rows],
                     "frame_size": [frame_w, frame_h], "sheet": core.rel_posix(path, ctx.out_root),
                     "atlas": core.rel_posix(atlas_path, ctx.out_root)},
            "outputs": [path, atlas_path]}


def op_frames(ctx, a):
    src = core.resolve_input(a.input or "")
    if not src:
        raise core.usage_error("The 'frames' command needs --input ANIMATED.gif|webp.")
    img = Image.open(src)
    n = getattr(img, "n_frames", 1)
    if n < 2:
        raise core.input_error(f"{src.name} is not animated (1 frame)",
                              "Use an animated GIF or WebP, or pass several images to 'gif'.")
    max_frames = ctx.config.limit("max_frames", 240)
    if n > max_frames:
        raise core.input_error(f"{n} frames exceeds limits.max_frames ({max_frames})")
    lock = bool(getattr(a, "lock_palette", False))
    palette_img = None
    outputs, timings = [], []
    for i in range(n):
        img.seek(i)
        frame = img.convert("RGBA")
        duration = img.info.get("duration", 0)
        timings.append(duration)
        if lock:
            if palette_img is None:
                palette_img = frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=int(getattr(a, "colors", 0) or 64))
            else:
                frame = frame.convert("RGB").quantize(palette=palette_img, dither=Image.Dither.NONE).convert("RGBA")
        name = core.slugify(f"{project_name(a)}-{src.stem}-frame")
        path = ctx.out_path(name, f".f{i + 1:03d}.png")
        core.save_image(ctx, frame, path, "PNG")
        outputs.append(path)
    payload = {"operation": "frames", "source": core.rel_posix(src), "frame_count": n,
               "durations_ms": timings, "palette_locked": lock,
               "files": [core.rel_posix(p, ctx.out_root) for p in outputs],
               "checks": ["every frame extracted and reopened", "durations preserved in the report"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project_name(a)}-{src.stem}-frames-report"), payload)
    ctx.audit("frames.extracted", count=n)
    return {"data": payload, "outputs": outputs + [out_json]}
